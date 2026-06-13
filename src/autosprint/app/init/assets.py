"""Extracted from the original autosprint.app.init module."""

from __future__ import annotations

import shutil
from datetime import datetime

from autosprint.app.init.checks import _PYTHON_PROJECT_MARKERS
from autosprint.config import _project_root, config
from autosprint.util.errors import add_context
from autosprint.util.output import printlev
from autosprint.util.paths import (
    AUTOSPRINT_DIR_NAME,
    LOGS_SUBDIR,
)

# Skills that hardcode Python-specific assumptions (autosprint internals,
# pytest patterns, etc.) — exclude from copy when the target repo isn't Python.
# Detection is marker-file based: a target with no `pyproject.toml`/`setup.py`/
# `requirements.txt` doesn't get these copied over.
_PYTHON_SPECIFIC_SKILLS: frozenset[str] = frozenset({"python-refactoring", "test-refactoring"})


def _target_is_python_repo() -> bool:
    """True if TARGET_REPO has at least one of the canonical Python project markers — used to gate skills that hardcode Python-specific assumptions out of the copy step. Marker-file check only (cheap, no subprocess), matching the same heuristic `_check_target_python_setup_and_warn` uses."""
    return any((config.TARGET_REPO_PATH / m).exists() for m in _PYTHON_PROJECT_MARKERS)


def _copy_claude_subdir(subdir_name: str, overwrite: bool = False) -> tuple[list[str], list[str]]:
    """Copy each entry (file or subdir) under autosprint/.claude/<subdir_name>/ into TARGET_REPO/.claude/<subdir_name>/. Used by `_copy_claude_assets_to_target` to cover skills (subdirs) and agents (files) with one helper. Silent when source dir is missing. Returns (copied_names, skipped_names) so the caller can emit subdir-specific log lines. When `overwrite=False` (default), existing target entries are kept and reported in `skipped`; when `overwrite=True` (used by `init --update-skills`), existing entries are deleted and replaced from source so users pick up newer versions. Python-specific skills (`_PYTHON_SPECIFIC_SKILLS`) are filtered out when the target isn't a Python repo so a TS/JS target doesn't get skills that import `autosprint.config` or assume pytest."""
    src_root = _project_root() / ".claude" / subdir_name
    if not src_root.is_dir():
        return [], []
    dst_root = config.TARGET_REPO_PATH / ".claude" / subdir_name
    dst_root.mkdir(parents=True, exist_ok=True)

    skip_python_specific = subdir_name == "skills" and not _target_is_python_repo()
    copied: list[str] = []
    skipped: list[str] = []
    for entry in sorted(src_root.iterdir()):
        if skip_python_specific and entry.name in _PYTHON_SPECIFIC_SKILLS:
            continue
        dst = dst_root / entry.name
        if dst.exists():
            if not overwrite:
                skipped.append(entry.name)
                continue
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        if entry.is_dir():
            shutil.copytree(entry, dst)
        else:
            shutil.copy2(entry, dst)
        copied.append(entry.name)
    return copied, skipped


def _copy_claude_assets_to_target(overwrite: bool = False) -> None:
    """Copy autosprint's `.claude/{skills,agents}/` into TARGET_REPO/.claude/ on init. Target-repo users opening Claude Code get autosprint-shipped skills (grill-destination, todos, …) and the Plan/Implement agent prompts (plan-agent, plan-team, implement) without a manual copy step. `overwrite=False` (default) preserves user edits on re-init; `overwrite=True` (used by `init --update-skills`) replaces target entries with the autosprint source versions. `settings.local.json` is user-specific (permissions / local state) and is NOT copied — each workspace maintains its own."""
    try:
        for subdir in ("skills", "agents"):
            copied, skipped = _copy_claude_subdir(subdir, overwrite=overwrite)
            if copied:
                verb = "Updated" if overwrite else "Copied to"
                printlev(f"[init] {verb} .claude/{subdir}/: {', '.join(copied)}", level=100)
            if skipped:
                printlev(f"[init] Skipped (already present in .claude/{subdir}/): {', '.join(skipped)}", level=50)
    except Exception as e:
        raise add_context(e, f"Failed to copy .claude assets into {config.TARGET_REPO_PATH}/.claude") from e


def _migrate_legacy_autosprint_files() -> None:
    """Migration covering three eras of layout churn. Runs on every startup so any legacy name that gets re-created (e.g. a stray `console.log` after the `console-verbose.log` migration ran) still gets handled. First: move root-level legacy files (ai-run.log, plan-decision-log.md, autosprint-console.log, .cache) from pre-folder autosprint runs into TARGET_REPO/autosprint/. Second: rename the older role-ambiguous names inside autosprint/ (ai-run.log → sprint-outcomes.log, console.log → console-verbose.log, plan-decision-log.md → plan-decisions.md). Third (logs-subfolder layout): move generated logs from autosprint/ root into autosprint/logs/ subfolder. When BOTH old and new exist (a re-creation after a prior migration), the orphan old file is renamed to `<name>.orphan-<timestamp>` — no data loss, the live log keeps flowing, and the user gets a warning to investigate what re-created the old name. Silent when nothing to do."""
    try:
        autosprint_dir = config.TARGET_REPO_PATH / AUTOSPRINT_DIR_NAME
        autosprint_dir.mkdir(parents=True, exist_ok=True)
        logs_dir = config.TARGET_REPO_PATH / LOGS_SUBDIR
        root_moves = [("ai-run.log", "sprint-outcomes.log"), ("plan-decision-log.md", "plan-decisions.md"), ("autosprint-console.log", "console-verbose.log"), (".cache", "cache"), ("plan.md", "plan.md"), ("adr.md", "adr.md"), ("ideal_state.md", "destination.md"), ("desired_state.md", "destination.md"), ("destination.md", "destination.md")]
        folder_renames = [("ai-run.log", "sprint-outcomes.log"), ("console.log", "console-verbose.log"), ("plan-decision-log.md", "plan-decisions.md"), ("ideal_state.md", "destination.md"), ("desired_state.md", "destination.md")]
        # Log files that lived at autosprint/ root in earlier versions now live under autosprint/logs/.
        # Each entry is the bare filename (same name, just deeper path).
        log_files_to_relocate = [
            "sprint-outcomes.log",
            "console-verbose.log",
            "console-all.log",
            "plan-decisions.md",
            "preflight-tests.log",
            "implement-failures.log",
            "last-test-output.log",
            "last-run-summary.md",
            "last-implement-failure.txt",
            "runtime-stats.md",
        ]
        migrated: list[str] = []
        orphaned: list[str] = []

        def _handle(old_path, new_path, label: str) -> None:
            if not old_path.exists():
                return
            if not new_path.exists():
                new_path.parent.mkdir(parents=True, exist_ok=True)
                old_path.rename(new_path)
                migrated.append(label)
                return
            # Both exist — legacy name got re-created after a prior migration.
            # Rename the orphan (don't delete; preserve user data) so the legacy
            # name is freed for the next run to surface any re-creation cleanly.
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            orphan_path = old_path.with_name(f"{old_path.name}.orphan-{stamp}")
            try:
                old_path.rename(orphan_path)
                orphaned.append(f"{old_path.name} → {orphan_path.name}")
            except OSError:
                pass  # best-effort; log is still usable via the new name

        for old_name, new_name in root_moves:
            _handle(config.TARGET_REPO_PATH / old_name, autosprint_dir / new_name, f"{old_name} → {AUTOSPRINT_DIR_NAME}/{new_name}")
        for old_name, new_name in folder_renames:
            _handle(autosprint_dir / old_name, autosprint_dir / new_name, f"{AUTOSPRINT_DIR_NAME}/{old_name} → {AUTOSPRINT_DIR_NAME}/{new_name}")
        for log_name in log_files_to_relocate:
            _handle(autosprint_dir / log_name, logs_dir / log_name, f"{AUTOSPRINT_DIR_NAME}/{log_name} → {LOGS_SUBDIR}/{log_name}")
        if migrated:
            printlev("[prepare] Migrated legacy autosprint files: " + ", ".join(migrated), level=50)
        if orphaned:
            printlev("[prepare] Found re-created legacy files alongside current ones — renamed orphans for inspection: " + ", ".join(orphaned), level=100)
    except Exception as e:
        raise add_context(e, f"Failed to migrate legacy autosprint files in {config.TARGET_REPO_PATH}") from e
