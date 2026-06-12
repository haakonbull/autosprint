"""autosprint init — bootstrap a TARGET_REPO so the PIT loop can run.

Owns the `autosprint init` subcommand and the prepare-step helpers that seed
files and check pre-conditions in TARGET_REPO. Split out of orchestrator.py
to keep loop logic separate from one-shot setup concerns. Functions here are
re-exported from orchestrator.py so existing
`from autosprint.orchestrator import _foo` paths still resolve.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import sys
from datetime import datetime

from autosprint.config import config, _project_root
from autosprint.config_toml import render_config_toml as _render_config_toml
from autosprint.errors import add_context
from autosprint.output import printlev
from autosprint.paths import (
    ADR_FILENAME,
    AUTOSPRINT_DIR_NAME,
    DESTINATION_FILENAME,
    LOGS_SUBDIR,
    PLAN_DECISIONS_FILENAME,
    RUNTIME_STATS_FILENAME,
    SPRINT_LOG_FILENAME,
)

# ---------------------------------------------------------------------------
# Refuse to act on the autosprint repo itself
# ---------------------------------------------------------------------------


def _assert_target_repo_not_self() -> None:
    """Autosprint must never modify itself. The target repo must be a different directory."""
    try:
        autosprint_root = _project_root().resolve()
        target = config.TARGET_REPO_PATH.resolve()
        if target == autosprint_root:
            raise RuntimeError(f"TARGET_REPO must not point at the autosprint repo itself ({autosprint_root}).\nAutosprint contains only methodology and orchestration — set TARGET_REPO to a different repository.")
    except RuntimeError:
        raise
    except Exception as e:
        raise add_context(e, "Failed to verify TARGET_REPO separation") from e


# ---------------------------------------------------------------------------
# Seeded files in TARGET_REPO/autosprint/
# ---------------------------------------------------------------------------


EXAMPLES_SOURCE_DIR = "examples"
DEFAULT_DESTINATION_SEED_FILENAME = "destination_game.example.md"


def _ensure_examples_dir_seeded() -> list[str]:
    """Mirror autosprint's `examples/` folder into `<target>/autosprint/examples/` so users see all available destination templates (game, flight-shooter, full template, blank template, concerns checklist), the waypoint example, and asset folders (e.g. `research_paper_assets/` with the journal LaTeX template + reference PDF build script) alongside their own `destination.md`. Idempotent: per-file copy (recursing into subfolders), existing files are left alone so user edits survive re-init. Returns the list of relative paths newly copied (empty when nothing changed) so callers can log. Silent (returns []) when autosprint's own examples/ folder is missing — defensive, shouldn't happen in a normal install."""
    try:
        src_root = _project_root() / EXAMPLES_SOURCE_DIR
        if not src_root.is_dir():
            return []
        dst_root = config.TARGET_REPO_PATH / AUTOSPRINT_DIR_NAME / EXAMPLES_SOURCE_DIR
        dst_root.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []
        for entry in sorted(src_root.rglob("*")):
            if not entry.is_file():
                continue
            rel = entry.relative_to(src_root)
            dst = dst_root / rel
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, dst)
            copied.append(rel.as_posix())
        if copied:
            printlev(f"[init] Seeded {AUTOSPRINT_DIR_NAME}/{EXAMPLES_SOURCE_DIR}/: {', '.join(copied)}", level=100)
        return copied
    except Exception as e:
        raise add_context(e, f"Failed to seed examples/ folder into {config.TARGET_REPO_PATH}") from e


def _ensure_destination_or_abort() -> None:
    """Abort if destination.md is missing or empty. The seed templates live in `<target>/autosprint/examples/` (placed there by `_ensure_examples_dir_seeded`), so the abort message points the user at the default seed (`destination_game.example.md`) for a quick start, or at `destination_full_template.md` if they'd rather write from scratch."""
    try:
        dest_path = config.TARGET_REPO_PATH / DESTINATION_FILENAME
        if dest_path.exists() and dest_path.read_text(encoding="utf-8").strip():
            return
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        examples_rel = f"{AUTOSPRINT_DIR_NAME}/{EXAMPLES_SOURCE_DIR}"
        raise RuntimeError(f"Aborted: {dest_path} is missing or empty. Quick start: `cp {examples_rel}/{DEFAULT_DESTINATION_SEED_FILENAME} {dest_path.as_posix()}` to use the bundled 3D-game demo, or `cp {examples_rel}/destination_research_ai_bubble.example.md {dest_path.as_posix()}` for a research-project demo. To write your own, copy `{examples_rel}/destination_full_template.md` and fill in the prompts. Then re-run.")
    except RuntimeError:
        raise
    except Exception as e:
        raise add_context(e, f"Failed to check destination.md in {config.TARGET_REPO_PATH}") from e


def _ensure_adr_stub() -> None:
    """Create an empty adr.md stub in TARGET_REPO if the file is missing, so the Plan and Implement agents always have something to read."""
    try:
        path = config.TARGET_REPO_PATH / ADR_FILENAME
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        stub = "# Architecture Decision Records\n\nLong-term technical decisions live here (libraries, major patterns, schemas, tooling). Each entry is immutable; to change a decision, add a new entry that references the old one under `**Supersedes:**`.\n\n_No decisions recorded yet._\n"
        path.write_text(stub, encoding="utf-8")
        printlev(f"[prepare] Created empty {ADR_FILENAME} stub in {config.TARGET_REPO_PATH}.", level=50)
    except Exception as e:
        raise add_context(e, f"Failed to create adr.md stub in {config.TARGET_REPO_PATH}") from e


# ---------------------------------------------------------------------------
# `autosprint init` configuration wizard (poetry-init style)
# ---------------------------------------------------------------------------


def _prompt_yn(question: str, default_yes: bool = True) -> bool | None:
    """Ask a Y/N question. Returns the boolean answer, or None when input is
    unavailable (EOF / Ctrl-C) so the caller can fall back to its own default."""
    suffix = "[Y/n]" if default_yes else "[y/N]"
    try:
        answer = input(f"{question} {suffix} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None
    if answer == "":
        return default_yes
    return answer in ("y", "yes")


def _prompt_choice(question: str, options: list[tuple[str, str]], default_key: str) -> str | None:
    """Ask a numbered multiple-choice question. `options` is a list of (key, label);
    returns the chosen key, the default on a blank or unrecognised answer, or None
    when input is unavailable (EOF / Ctrl-C)."""
    printlev(question, level=100)
    keys = [key for key, _ in options]
    for i, (key, label) in enumerate(options, 1):
        marker = "  (default)" if key == default_key else ""
        printlev(f"   {i}) {label}{marker}", level=100)
    try:
        raw = input(f"Choose 1-{len(options)} [{keys.index(default_key) + 1}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if raw == "":
        return default_key
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return keys[int(raw) - 1]
    printlev(f"[init] Unrecognised choice '{raw}' — using the default ({default_key}).", level=100)
    return default_key


def _detect_assistants() -> tuple[bool, bool]:
    """Best-effort probe of which AI backends this machine can dispatch to:
    Claude (the `claude` CLI on PATH) and Copilot (the `copilot` SDK package
    importable). Importable ≠ authenticated — `autosprint doctor` does the live
    round-trip — but it is a good-enough signal to pre-select the wizard default."""
    import importlib.util

    claude = shutil.which("claude") is not None
    copilot = importlib.util.find_spec("copilot") is not None
    return claude, copilot


def _wizard_language(active: dict[str, str]) -> None:
    """Wizard step 1 — target-repo language / test runner. A repo that already has
    marker files gets its detected language shown for confirmation; an empty repo is
    asked outright. `target_test_runner` is recorded only when the answer differs
    from what `TARGET_TEST_RUNNER=auto` would resolve to anyway — so confirming the
    detection writes nothing (auto already does the right thing)."""
    from autosprint.test_runners import detect_runner

    root = config.TARGET_REPO_PATH
    has_markers = any((root / m).exists() for m in ("pyproject.toml", "pytest.ini", "setup.cfg", "package.json"))
    auto_choice = detect_runner()
    if has_markers:
        label = "Python / pytest" if auto_choice == "pytest" else "TypeScript-JavaScript / vitest"
        ok = _prompt_yn(f"[init] Detected target language: {label}. Correct?", default_yes=True)
        chosen = auto_choice if (ok is None or ok) else ("vitest" if auto_choice == "pytest" else "pytest")
    else:
        choice = _prompt_choice(
            "[init] No language marker files found yet. What will the target repo be?",
            [("pytest", "Python (pytest)"), ("vitest", "TypeScript / JavaScript (vitest)")],
            "pytest",
        )
        chosen = choice or "pytest"
    if chosen != auto_choice:
        active["target_test_runner"] = chosen
        printlev(f'[init] → target_test_runner = "{chosen}"', level=100)
    else:
        printlev(f"[init] → target_test_runner stays auto (detection resolves to {chosen}).", level=100)


def _wizard_assistants(active: dict[str, str]) -> None:
    """Wizard step 2 — which AI backend(s) autosprint will use. Resolves the answer
    into the matching planning team / implement agent / refusal-fallback / how-far
    agent. Only keys that deviate from the both-backends defaults are recorded; a
    Claude-only answer notably disables the (Copilot) refusal-fallback so `doctor`
    won't then demand Copilot auth."""
    claude, copilot = _detect_assistants()
    printlev(f"[init] Backend detection: Claude CLI {'✓' if claude else '✗'}, Copilot SDK {'✓' if copilot else '✗'}", level=100)
    if claude and not copilot:
        default_key = "claude"
    elif copilot and not claude:
        default_key = "copilot"
    else:
        default_key = "both"
    choice = _prompt_choice(
        "[init] Which AI backend(s) will autosprint use? (sets the planning team + implement agent)",
        [
            ("both", "Both Claude and Copilot — council team (mixed), Opus implementor"),
            ("claude", "Claude only — council_opus team (6-agent Claude), Opus implementor, no Copilot fallback"),
            ("copilot", "Copilot only — council_gpt55 team (6-agent Copilot), GPT-5.5 implementor"),
        ],
        default_key,
    )
    if choice is None or choice == "both":
        printlev("[init] → using the default both-backend team (council).", level=100)
        return
    if choice == "claude":
        active["team"] = "council_opus"
        active["implement_agent"] = "implementor_opus48"  # explicit so a future change to the default doesn't leak Copilot into a Claude-only setup
        active["implement_fallback_agent"] = ""  # default fallback is Copilot — disable it for a Claude-only setup
        active["howfar_agent"] = "howfar_opus48"
        printlev("[init] → team = council_opus (6-agent Claude), implementor + how-far on Opus 4.8, refusal-fallback disabled (Claude-only).", level=100)
    elif choice == "copilot":
        active["team"] = "council_gpt55"
        active["implement_agent"] = "implementor_gpt55"
        active["implement_fallback_agent"] = ""  # default fallback is Copilot — already same backend, no point
        active["howfar_agent"] = "howfar_gpt55"
        printlev("[init] → team = council_gpt55 (6-agent Copilot), implementor + how-far on GPT-5.5 (Copilot-only).", level=100)


def _wizard_auto_gates(active: dict[str, str]) -> None:
    """Wizard step 3 — opt into auto-detected per-sprint gates (format-check,
    lint-check, coverage-track). Y writes `format_check="auto"`, `lint_check="auto"`,
    and `coverage_track=true` to config.toml so the corresponding gates kick in
    on every sprint. N leaves the safe defaults in place (gates off). The
    always-on gates (import-check, smoke-test) are not touched by this question."""
    ok = _prompt_yn(
        "[init] Enable auto-detected gates (format-check, lint-check, coverage-track)?",
        default_yes=True,
    )
    if ok is None or not ok:
        printlev("[init] → auto-detected gates left off (smoke-test and import-check still active).", level=100)
        return
    active["format_check"] = "auto"
    active["lint_check"] = "auto"
    active["coverage_track"] = "true"
    printlev("[init] → format_check=auto, lint_check=auto, coverage_track=true (gates skip silently if their tools aren't installed).", level=100)


def _run_config_wizard() -> dict[str, str]:
    """Run the interactive `autosprint init` wizard and return the config.toml keys
    that deviate from defaults. Three questions: target language, AI backend(s), and
    auto-gates. The caller guarantees an interactive TTY; each prompt still tolerates EOF."""
    printlev("\n[init] Configuring autosprint for this repo — press Enter to accept each default.", level=100)
    active: dict[str, str] = {}
    _wizard_language(active)
    _wizard_assistants(active)
    _wizard_auto_gates(active)
    return active


def _ensure_config_toml(interactive: bool = False) -> None:
    """Create autosprint/config.toml in the target repo if missing. With
    `interactive=True` and a real TTY, a short poetry-init-style wizard asks for the
    target language and AI backend(s) and writes the answers as live settings;
    otherwise a fully-commented template is written. Committed, like destination.md
    / adr.md. Idempotent — an existing config.toml is never touched, so the wizard
    runs at most once per repo (delete the file to re-run it)."""
    try:
        path = config.TARGET_REPO_PATH / AUTOSPRINT_DIR_NAME / "config.toml"
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        active: dict[str, str] = {}
        if interactive and sys.stdin.isatty():
            active = _run_config_wizard()
        path.write_text(_render_config_toml(active), encoding="utf-8")
        if active:
            printlev(f"[init] Created {AUTOSPRINT_DIR_NAME}/config.toml from your answers ({', '.join(sorted(active))}).", level=100)
        else:
            printlev(f"[prepare] Created {AUTOSPRINT_DIR_NAME}/config.toml template in {config.TARGET_REPO_PATH}.", level=50)
    except Exception as e:
        raise add_context(e, f"Failed to create config.toml in {config.TARGET_REPO_PATH}") from e


# ---------------------------------------------------------------------------
# .gitignore management
# ---------------------------------------------------------------------------


_PYTHON_GITIGNORE_DEFAULTS = "# Python\n__pycache__/\n*.py[cod]\n*$py.class\n*.egg-info/\ndist/\nbuild/\n\n# Virtual envs\n.venv/\nvenv/\nenv/\n.env\n\n# Testing / type-checking / linting\n.pytest_cache/\n.coverage\nhtmlcov/\n.mypy_cache/\n.ruff_cache/\n\n# Notebooks\n.ipynb_checkpoints/\n\n# IDE\n.idea/\n.vscode/\n\n# OS\n.DS_Store\nThumbs.db\n"

_TS_GITIGNORE_DEFAULTS = "# Node / TypeScript\nnode_modules/\ndist/\nbuild/\ncoverage/\n*.tsbuildinfo\n\n# Logs\nnpm-debug.log*\nyarn-debug.log*\nyarn-error.log*\npnpm-debug.log*\n\n# Env\n.env\n.env.local\n\n# IDE\n.idea/\n.vscode/\n\n# OS\n.DS_Store\nThumbs.db\n"


def _ensure_gitignore_entries() -> None:
    """Ensure TARGET_REPO/.gitignore ignores noisy autosprint-generated files (verbose console logs, cache, control files) while *tracking* the three history files (`sprint-outcomes.log`, `plan-decisions.md`, `runtime-stats.md`). Tracked history means a checkout to an older commit also rewinds the loop's view of "what's been tried", so branch-jumping doesn't need a manual `clear-logs`. The wildcard-plus-unignore pattern (`logs/*` then `!logs/sprint-outcomes.log` …) is the standard git idiom for "ignore a directory except these specific files". When the file is missing entirely (fresh repo), seed it with language-appropriate defaults first (Python venv/__pycache__/etc. when `pyproject.toml` is present, or node_modules/dist/etc. when `package.json` is present and `pyproject.toml` is not), then append the autosprint block. Also adds the language's essential ignores (`node_modules/` for TS, `__pycache__/` + `.venv/` for Python) to the required list so even an existing thin `.gitignore` picks them up. Idempotent: also migrates legacy `autosprint/logs/` (no wildcard, ignores everything) to the new pattern so existing target repos pick up the tracked-history policy on next run."""
    try:
        has_pyproject = (config.TARGET_REPO_PATH / "pyproject.toml").exists()
        has_package_json = (config.TARGET_REPO_PATH / "package.json").exists()
        target_is_ts = has_package_json and not has_pyproject

        language_essentials: list[str] = []
        if target_is_ts:
            language_essentials = ["node_modules/", "dist/", "coverage/"]
        elif has_pyproject:
            language_essentials = ["__pycache__/", ".venv/", "*.pyc"]

        required = language_essentials + [
            f"{LOGS_SUBDIR}/*",
            f"!{SPRINT_LOG_FILENAME}",
            f"!{PLAN_DECISIONS_FILENAME}",
            f"!{RUNTIME_STATS_FILENAME}",
            f"{AUTOSPRINT_DIR_NAME}/cache/",
            f"{AUTOSPRINT_DIR_NAME}/stop",
            f"{AUTOSPRINT_DIR_NAME}/stop-now",
        ]
        gitignore_path = config.TARGET_REPO_PATH / ".gitignore"
        seeded = not gitignore_path.exists()
        existing_text = "" if seeded else gitignore_path.read_text(encoding="utf-8")
        if seeded:
            if target_is_ts:
                existing_text = _TS_GITIGNORE_DEFAULTS
                printlev(f"[prepare] Seeded {gitignore_path} with Node/TypeScript-project defaults.", level=100)
            else:
                existing_text = _PYTHON_GITIGNORE_DEFAULTS
                printlev(f"[prepare] Seeded {gitignore_path} with Python-project defaults.", level=100)

        # Migration: legacy `autosprint/logs/` (no wildcard) ignored everything in logs/ — replace with the new wildcard-plus-unignore pattern so history files start being tracked.
        legacy_line = f"{LOGS_SUBDIR}/"
        if any(line.strip() == legacy_line for line in existing_text.splitlines()):
            existing_text = "\n".join(line for line in existing_text.splitlines() if line.strip() != legacy_line) + ("\n" if existing_text.endswith("\n") else "")
            printlev(f"[prepare] Migrating .gitignore: replaced `{legacy_line}` with the tracked-history pattern (sprint-outcomes.log, plan-decisions.md, runtime-stats.md are now committed).", level=100)

        existing_lines = {line.strip() for line in existing_text.splitlines()}
        missing = [entry for entry in required if entry not in existing_lines]
        if not missing and not seeded:
            return
        addition = ("\n" if existing_text and not existing_text.endswith("\n") else "") + "\n# autosprint — ignore verbose logs / cache / control files; track the three history files\n" + "\n".join(missing) + "\n" if missing else ""
        gitignore_path.write_text(existing_text + addition, encoding="utf-8")
        if missing:
            printlev(f"[prepare] Appended to .gitignore: {', '.join(missing)}", level=50)
    except Exception as e:
        raise add_context(e, "Failed to ensure .gitignore entries in TARGET_REPO") from e


# ---------------------------------------------------------------------------
# Copy autosprint's .claude/ and .github/skills/ assets into TARGET_REPO
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Migrate legacy filenames from earlier autosprint layouts
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Pre-flight checks for `autosprint init`
# ---------------------------------------------------------------------------


def _verify_target_is_git_repo() -> None:
    """Refuse to init a TARGET_REPO that isn't a git repository — autosprint's revert/commit flow depends on it. Raises RuntimeError with a pointer to `git init`."""
    try:
        dot_git = config.TARGET_REPO_PATH / ".git"
        if not dot_git.exists():
            raise RuntimeError(f"TARGET_REPO ({config.TARGET_REPO_PATH}) is not a git repository. Autosprint's revert/commit flow requires a git repo — run `git init` in TARGET_REPO before `autosprint init`.")
    except RuntimeError:
        raise
    except Exception as e:
        raise add_context(e, f"Failed to verify TARGET_REPO is a git repo ({config.TARGET_REPO_PATH})") from e


def _verify_target_is_initialised() -> None:
    """Guard for `autosprint run` / `autosprint plan`: abort before any state-mutating prepare step if TARGET_REPO doesn't look like an autosprint-initialised git repo. Two checks: (1) `.git/` exists (we need a real git repo for branch/commit/revert), (2) `autosprint/config.toml` exists (the init marker — created by `autosprint init`, committed to the target). Without these, the legacy prepare flow would silently seed `autosprint/`, a `.gitignore`, etc., in whatever directory you're standing in — invasive and confusing. Raises RuntimeError with a clear next-step pointer; callers should let the message reach the user verbatim."""
    try:
        dot_git = config.TARGET_REPO_PATH / ".git"
        if not dot_git.exists():
            raise RuntimeError(f"`{config.TARGET_REPO_PATH}` is not a git repository — autosprint's revert/commit flow requires one.\n  → Run `git init` in the target repo, then `autosprint init`.")
        config_toml = config.TARGET_REPO_PATH / AUTOSPRINT_DIR_NAME / "config.toml"
        if not config_toml.exists():
            raise RuntimeError(f"`{config.TARGET_REPO_PATH}` has no autosprint setup (missing `{AUTOSPRINT_DIR_NAME}/config.toml`).\n  → Run `autosprint init` here first, then re-run.")
    except RuntimeError:
        raise
    except Exception as e:
        raise add_context(e, f"Failed to verify autosprint initialisation in {config.TARGET_REPO_PATH}") from e


CLAUDE_MD_FILENAME = "CLAUDE.md"
_CLAUDE_MD_MIN_USEFUL_CHARS = 200
_CLAUDE_MD_BLOATED_BYTES = 50 * 1024
_CLAUDE_MD_BLOATED_LINES = 1000

README_FILENAME = "README.md"
_README_MIN_USEFUL_CHARS = 200

_PYTHON_PROJECT_MARKERS: tuple[str, ...] = ("pyproject.toml", "pytest.ini", "setup.py", "setup.cfg", "requirements.txt", "conftest.py", "tox.ini")

# Maps each assistant kind to the CLI binary the dispatcher actually needs on PATH.
# Claude dispatch shells out to the `claude` CLI via claude_agent_sdk's spawn path,
# so the binary must be installed. Copilot dispatch uses the github-copilot-sdk
# Python package, which talks to Microsoft's API directly — no CLI binary needed —
# so "copilot" is intentionally NOT in this map.
_CLI_BINARY_FOR_ASSISTANT: dict[str, str] = {"claude": "claude"}
_CLI_INSTALL_HINT_FOR_ASSISTANT: dict[str, str] = {
    "claude": "Install Claude Code: https://claude.com/claude-code",
}


def _required_assistants_for_run() -> set[str]:
    """Collect the set of assistant kinds (e.g. 'claude', 'copilot') that the configured TEAM, IMPLEMENT_AGENT, IMPLEMENT_FALLBACK_AGENT, and HOWFAR_AGENT will dispatch to. Lets `_check_cli_deps_or_abort` probe only the CLIs that will actually be invoked, so Claude-only and Copilot-only setups don't get false-positive errors about the unused CLI. HOWFAR_AGENT is included so a Claude-only team with `HOWFAR_AGENT=howfar_gpt55` doesn't slip past doctor without Copilot auth being checked."""
    needed: set[str] = {agent["assistant"] for agent in config.TEAM_AGENTS}
    needed.add(config.IMPLEMENT_AGENT_CONFIG["assistant"])
    fallback = config.IMPLEMENT_FALLBACK_AGENT_CONFIG
    if fallback is not None:
        needed.add(fallback["assistant"])
    needed.add(config.HOWFAR_AGENT_CONFIG["assistant"])
    return needed


def _check_cli_deps_or_abort() -> None:
    """Fail-fast pre-flight: probe that the CLIs required by this run's TEAM/IMPLEMENT_AGENT/IMPLEMENT_FALLBACK_AGENT are actually on PATH. Without this check, a missing `claude` or `gh` surfaces as a confusing dispatch error 90 seconds into sprint 1; with it, the user gets a one-line "install X" pointer before any side effects. Hard abort (RuntimeError) — anything else gets ignored once the loop is running."""
    needed = _required_assistants_for_run()
    missing: list[tuple[str, str, str]] = []
    for assistant in sorted(needed):
        binary = _CLI_BINARY_FOR_ASSISTANT.get(assistant)
        if binary is None:
            continue  # unknown assistant kind — skip rather than block; future expansion can add an entry
        if shutil.which(binary) is None:
            missing.append((assistant, binary, _CLI_INSTALL_HINT_FOR_ASSISTANT[assistant]))
    if missing:
        lines = [f"Required CLI(s) missing for this configuration (TEAM={config.TEAM}, IMPLEMENT_AGENT={config.IMPLEMENT_AGENT}):"]
        for assistant, binary, hint in missing:
            lines.append(f"  - {assistant}: `{binary}` not on PATH. {hint}")
        raise RuntimeError("\n".join(lines))
    printlev(f"[init] ✅ Required CLIs on PATH: {', '.join(sorted(needed))}", level=100)


def _check_readme_and_warn() -> None:
    """Best-effort sanity check on TARGET_REPO/README.md. Missing → warn (the `grill-destination` skill's mature-repo mode reads README as a primary source for project intent; without it that mode falls back to weaker signals). Tiny → warn (placeholder). No bloat ceiling — long READMEs are fine; agents don't auto-load README the way they do CLAUDE.md, so context cost isn't a concern here."""
    readme = config.TARGET_REPO_PATH / README_FILENAME
    if not readme.exists():
        printlev(f"[init] ⚠ No {README_FILENAME} in TARGET_REPO. `grill-destination` mature-repo mode reads README to extract project intent — without it, that mode falls back to inferring from folder structure and commits.", level=100)
        return
    try:
        text = readme.read_text(encoding="utf-8")
    except OSError:
        return
    char_count = len(text)
    if char_count < _README_MIN_USEFUL_CHARS:
        printlev(f"[init] ⚠ {README_FILENAME} exists but is only {char_count} chars — looks like a placeholder. A real project description helps `grill-destination` and gives agents context they can't otherwise infer.", level=100)
        return
    printlev(f"[init] ✅ {README_FILENAME} present ({char_count} chars).", level=100)


def _check_target_python_setup_and_warn() -> None:
    """Best-effort sanity check: warn if TARGET_REPO doesn't look like a Python project, since autosprint assumes Python + pytest. Marker-file check only — running `pytest --collect-only` would be more accurate but the first sprint already runs the suite (INITIAL_TESTS=quick) so a non-Python target surfaces immediately. Cheap and quiet on the happy path."""
    target = config.TARGET_REPO_PATH
    found = [m for m in _PYTHON_PROJECT_MARKERS if (target / m).exists()]
    if found:
        printlev(f"[init] ✅ Target looks like a Python project (found: {', '.join(found)}).", level=100)
        return
    printlev(f"[init] ⚠ TARGET_REPO has none of {{{', '.join(_PYTHON_PROJECT_MARKERS)}}}. Autosprint assumes Python + pytest; a non-Python target will fail at the Test phase. Ignore if you're about to add a pyproject before sprint 1.", level=100)


def _check_claude_md_and_warn() -> None:
    """Best-effort sanity check on TARGET_REPO/CLAUDE.md — the file Claude Code / Agent SDK auto-loads as project context for every agent invocation. Three no-go conditions get flagged: missing → agents start each task with zero project context; tiny (<200 chars) → looks like an unfilled placeholder; bloated (≥50KB or ≥1000 lines) → eats context budget every turn. Subjective quality (well-written? up to date?) is grilling territory, not init's job."""
    claude_md = config.TARGET_REPO_PATH / CLAUDE_MD_FILENAME
    if not claude_md.exists():
        printlev(f"[init] ⚠ No {CLAUDE_MD_FILENAME} in TARGET_REPO. Agents will start each task with zero project context — they'll have to infer architecture and conventions from raw code. Consider a 30-line {CLAUDE_MD_FILENAME} (project description, structure, how to run tests, key concepts) before your first sprint.", level=100)
        return
    try:
        text = claude_md.read_text(encoding="utf-8")
    except OSError:
        return  # best-effort; don't fail init on a read error
    char_count = len(text)
    line_count = text.count("\n") + 1
    byte_count = len(text.encode("utf-8"))
    if char_count < _CLAUDE_MD_MIN_USEFUL_CHARS:
        printlev(f"[init] ⚠ {CLAUDE_MD_FILENAME} exists but is only {char_count} chars — looks like a placeholder. Agents need real project context (architecture, conventions, how to run tests) to be useful.", level=100)
        return
    if byte_count >= _CLAUDE_MD_BLOATED_BYTES or line_count >= _CLAUDE_MD_BLOATED_LINES:
        printlev(f"[init] ⚠ {CLAUDE_MD_FILENAME} is large ({char_count} chars / {line_count} lines / {byte_count // 1024} KB). Every agent invocation pays this in context budget — consider trimming non-essential sections.", level=100)
        return
    printlev(f"[init] ✅ {CLAUDE_MD_FILENAME} present ({char_count} chars / {line_count} lines).", level=100)


def _bootstrap_target_env_and_warn() -> None:
    """If the target has `.env.example` but no `.env`, ask Y/N before copying — defaulting to Y. Many users forget the manual copy step on a fresh clone, but a silent auto-copy could be presumptive (e.g. user already has a `.env` elsewhere or wants different values). The Y/N prompt with default-Y splits the difference: one keystroke accepts, one types `n` to skip. After a copy, warn the user to fill in any placeholder values. autosprint itself doesn't read the target's `.env` — but the target's app/tests usually do, which is why this matters."""
    target = config.TARGET_REPO_PATH
    example = target / ".env.example"
    actual = target / ".env"
    if not example.exists() or actual.exists():
        return
    try:
        answer = input(f"[init] Target has `.env.example` but no `.env`. Copy `{example.name}` → `.env`? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        printlev("\n[init] Skipped target `.env` bootstrap (no input). Run `cp .env.example .env` in the target manually if needed.", level=100)
        return
    if answer not in ("", "y", "yes"):
        printlev("[init] Skipped target `.env` bootstrap. Run `cp .env.example .env` in the target manually if needed.", level=100)
        return
    try:
        shutil.copy2(example, actual)
    except OSError as e:
        printlev(f"[init] ⚠ Couldn't copy `.env.example` → `.env`: {e}. Bootstrap manually with `cp .env.example .env` in {target}.", level=100)
        return
    printlev(f"[init] ✅ Copied target's `.env.example` → `.env` ({actual}).", level=100)
    printlev("[init] 👉 Edit `.env` to fill in any placeholder values (DB URLs, API keys, etc.) — placeholders like `<your-url>` will break tests until replaced.", level=100)


_DOCKERIGNORE_HIGH_PRIORITY: tuple[str, ...] = (".env", ".git", ".venv", "venv", "__pycache__", "*.pyc")


def _check_dockerignore_and_warn() -> None:
    """If the target has a Dockerfile or compose file, sanity-check `.dockerignore` exists and covers the high-priority entries that prevent secrets and bloat from baking into the image (`.env`, `.git`, `.venv`, `__pycache__`, `*.pyc`). Skipped silently for non-Docker targets — most aren't. The check is intentionally narrow: image hygiene is the user's call past the basics, but `.env` baked into a Docker image is a high-stakes leak that's worth catching."""
    target = config.TARGET_REPO_PATH
    has_docker = (target / "Dockerfile").exists() or (target / "docker-compose.yml").exists() or (target / "compose.yml").exists() or (target / "compose.yaml").exists()
    if not has_docker:
        return
    dockerignore = target / ".dockerignore"
    if not dockerignore.exists():
        printlev(f"[init] ⚠ Target has Dockerfile/compose but no `.dockerignore`. The image build context will include `.git/`, `.venv/`, and `.env` — bloated images and possible secret leak. Recommended minimum: {', '.join(_DOCKERIGNORE_HIGH_PRIORITY)}.", level=100)
        return
    try:
        existing = {line.strip() for line in dockerignore.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")}
    except OSError:
        return
    missing = [entry for entry in _DOCKERIGNORE_HIGH_PRIORITY if entry not in existing]
    if missing:
        printlev(f"[init] ⚠ `.dockerignore` is missing high-priority entries: {', '.join(missing)}. Without these your image may bake in secrets (`.env`) or bloat (`.git`, `.venv`, `__pycache__`).", level=100)
        return
    printlev("[init] ✅ `.dockerignore` covers high-priority entries.", level=100)


_CREDENTIAL_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{50,}")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}")),
    ("GitHub personal token", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[bpars]-[A-Za-z0-9\-]{10,}")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)
_SENSITIVE_SCAN_MAX_FILE_BYTES = 1_000_000
_SENSITIVE_SCAN_MAX_FINDINGS = 10


def _scan_for_sensitive_content_and_warn() -> None:
    """Pre-flight scan that flags conditions which would leak secrets via the next push. Three checks against TARGET_REPO: (1) `.env` is committed (high-priority — already in history, needs `git rm --cached` + history rewrite to fully scrub); (2) `.env` is in the worktree but not matched by .gitignore (about-to-leak); (3) high-confidence credential regexes (Anthropic/OpenAI keys, GitHub tokens, AWS access keys, Slack tokens, private-key blocks) match in any tracked file. Best-effort and non-blocking — autosprint's job is to flag, not enforce. Caps findings to keep output readable on a flagged repo."""
    target = config.TARGET_REPO_PATH
    findings: list[str] = []
    try:
        result = subprocess.run(["git", "ls-files"], cwd=target, capture_output=True, text=True, timeout=10)
        tracked = result.stdout.splitlines() if result.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        tracked = []
    if ".env" in tracked:
        findings.append("`.env` is committed to git. Remove with `git rm --cached .env`, add `.env` to .gitignore, then rewrite history to fully scrub if it ever held real secrets.")
    env_path = target / ".env"
    if env_path.exists() and ".env" not in tracked:
        try:
            ignored_check = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=target, capture_output=True, timeout=5)
            ignored = ignored_check.returncode == 0
        except (OSError, subprocess.SubprocessError):
            ignored = True  # if we can't check, don't false-positive
        if not ignored:
            findings.append("`.env` exists in the worktree but is not matched by .gitignore. Add `.env` to .gitignore before any commit.")
    matched: list[str] = []
    for rel_path in tracked:
        if not rel_path.strip():
            continue
        f = target / rel_path
        if not f.is_file():
            continue
        try:
            if f.stat().st_size > _SENSITIVE_SCAN_MAX_FILE_BYTES:
                continue
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, pattern in _CREDENTIAL_PATTERNS:
            if pattern.search(text):
                matched.append(f"{rel_path} → {label}")
                break  # one finding per file is enough; user can investigate
        if len(matched) >= _SENSITIVE_SCAN_MAX_FINDINGS:
            break
    if matched:
        joined = "\n      ".join(matched[:_SENSITIVE_SCAN_MAX_FINDINGS])
        findings.append(f"Possible credential matches in tracked files (regex match — verify before assuming false-positive):\n      {joined}")
    if findings:
        printlev("[init] ⚠ Sensitive-content scan flagged:", level=100)
        for finding in findings:
            printlev(f"      - {finding}", level=100)
        printlev("[init]   Review these before your first push. Autosprint won't block, but a leaked credential is hard to revoke after the fact.", level=100)
    else:
        printlev("[init] ✅ Sensitive-content scan clean (no committed .env, no high-confidence credential matches in tracked files).", level=100)


def _print_init_config_summary() -> None:
    """Print the resolved config bits init actually depends on. Lets the user catch a misconfigured .env before their first real sprint."""
    lines = [
        "[init] Resolved config:",
        f"       TARGET_REPO      = {config.TARGET_REPO_PATH}",
        f"       TEAM             = {config.TEAM} ({len(config.TEAM_AGENTS)} agent(s))",
        f"       IMPLEMENT_AGENT  = {config.IMPLEMENT_AGENT}",
        f"       MAX_SPRINTS      = {config.MAX_SPRINTS}",
        f"       INITIAL_TESTS    = {config.INITIAL_TESTS}",
    ]
    printlev("\n".join(lines), level=100)


# ---------------------------------------------------------------------------
# Top-level init driver
# ---------------------------------------------------------------------------


def _run_init_update_skills() -> None:
    """`autosprint init --update-skills`: refresh the target repo's `.claude/skills/`, `.claude/agents/`, and `.github/skills/` from the autosprint source, **overwriting** existing entries. Use this after `git pull`-ing a newer autosprint to pick up updated skills without nuking the rest of init. Skips everything else (config.toml, gitignore, destination seed, sensitive-content scan, etc.) — those are one-time bootstrap concerns, not refresh-relevant."""
    from autosprint.banners import section_banner

    try:
        printlev(f"\n{section_banner('INIT --update-skills', 'START')}\n", level=100)
        _assert_target_repo_not_self()
        _verify_target_is_git_repo()
        printlev(f"[init] ✅ TARGET_REPO is a git repository: {config.TARGET_REPO_PATH}", level=100)
        _copy_claude_assets_to_target(overwrite=True)
        printlev("\n[init] ✅ Skills and agents updated from autosprint source.", level=100)
        printlev(f"{section_banner('INIT --update-skills', 'END')}", level=100)
    except Exception as e:
        raise add_context(e, f"Failed to update skills in {config.TARGET_REPO_PATH}") from e


def _run_init(assume_defaults: bool = False) -> None:
    """Bootstrap autosprint's working files in the target repo (already resolved to cwd, `--target`, or the TARGET_REPO env fallback before this runs). Steps: (1) assert the target is not the autosprint repo itself, (2) verify it is a git repo, (3) migrate any legacy file names from earlier autosprint versions, (4) seed autosprint/destination.md with the role-explaining template, (5) create an empty autosprint/adr.md stub, (6) create autosprint/config.toml — interactively via a short wizard (target language, AI backend) unless `assume_defaults` is set or stdin is not a TTY, in which case a default template is written, (7) append required .gitignore entries, (8) print resolved config. The destination grilling lives as a separate skill in Claude Code."""
    from autosprint.banners import section_banner

    try:
        printlev(f"\n{section_banner('INIT', 'START')}\n", level=100)
        _assert_target_repo_not_self()
        _check_cli_deps_or_abort()
        _verify_target_is_git_repo()
        printlev(f"[init] ✅ TARGET_REPO is a git repository: {config.TARGET_REPO_PATH}", level=100)
        _migrate_legacy_autosprint_files()
        _ensure_examples_dir_seeded()
        try:
            _ensure_destination_or_abort()
            printlev("[init] autosprint/destination.md exists and has content.", level=100)
        except RuntimeError as e:
            # _ensure_destination_or_abort raises when the file was just seeded —
            # for `init` that's the expected "created a seed" path, not an error.
            printlev(f"[init] {e}", level=100)
        _ensure_adr_stub()
        _ensure_config_toml(interactive=not assume_defaults)
        _ensure_gitignore_entries()
        _copy_claude_assets_to_target()
        _check_target_python_setup_and_warn()
        _check_claude_md_and_warn()
        _check_readme_and_warn()
        _bootstrap_target_env_and_warn()
        _check_dockerignore_and_warn()
        _scan_for_sensitive_content_and_warn()
        probe_backends(warn_only=True)
        _print_init_config_summary()
        printlev("\n[init] ✅ TARGET_REPO is ready.", level=100)
        printlev(f"[init] 👉 Next: open Claude Code in {config.TARGET_REPO_PATH} and run `/grill-destination` to flesh out autosprint/destination.md (the spec autosprint descends toward). When the file feels complete, run `autosprint` normally.", level=100)
        printlev(f"{section_banner('INIT', 'END')}", level=100)
    except Exception as e:
        raise add_context(e, f"Failed to initialise TARGET_REPO at {config.TARGET_REPO_PATH}") from e


# ---------------------------------------------------------------------------
# autosprint doctor — verify the configured setup can actually run
# ---------------------------------------------------------------------------


# Probe agent per backend — used only for the doctor connectivity probe. Claude probes
# Haiku (cheapest). Copilot probes gpt-5.5, the model production teams actually dispatch
# to — gpt-4.1 was retired from Copilot's lineup and probing it blocked runs whose own
# models were fine.
_DOCTOR_PROBE_AGENT_KEY: dict[str, str] = {"claude": "decider_haiku_claude", "copilot": "editor_gpt55"}

# Runtime deps the dispatch layer imports. If any of these are missing, every
# Plan/Implement dispatch will fail mid-sprint — exactly the trap a stale
# `pip install -e .` from before pyproject's deps were declared puts you in.
# (module_import_name, install_name_for_the_hint).
_REQUIRED_RUNTIME_DEPS: tuple[tuple[str, str], ...] = (
    ("claude_agent_sdk", "claude-agent-sdk"),
    ("copilot", "github-copilot-sdk"),
)


def _check_install_health() -> tuple[bool, str]:
    """Spot a stale autosprint install before any sprint touches it. Two specific traps this catches: (1) one or more of the runtime deps the dispatch layer needs (`claude_agent_sdk`, `copilot`) can't be imported — the symptom is "Copilot CLI not found" / similar errors several minutes into the first Plan phase; (2) `importlib.metadata.version("autosprint")` disagrees with the version declared in this checkout's `pyproject.toml`, which means the installed `autosprint.exe` is wired to an older metadata snapshot (typically a leftover `pip install -e .` from before the v0.2.0 dep list landed) — the editable hook still runs new code, but `pip` and pyproject don't agree on the dep list, so other pip activity can wipe out deps without warning. Returns (ok, message) so the caller can render it like any other doctor check."""
    import importlib
    import importlib.metadata
    import tomllib

    missing: list[tuple[str, str]] = []
    for module_name, pkg_name in _REQUIRED_RUNTIME_DEPS:
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append((module_name, pkg_name))

    pyproject_version: str | None = None
    try:
        with (_project_root() / "pyproject.toml").open("rb") as f:
            pyproject_version = tomllib.load(f).get("project", {}).get("version")
    except (OSError, tomllib.TOMLDecodeError):
        pyproject_version = None

    installed_version: str | None = None
    try:
        installed_version = importlib.metadata.version("autosprint")
    except importlib.metadata.PackageNotFoundError:
        installed_version = None

    version_skew = bool(pyproject_version and installed_version and pyproject_version != installed_version)
    if not missing and not version_skew:
        ver_label = f"v{installed_version}" if installed_version else "version unknown"
        return True, f"Install health OK ({ver_label}; all runtime deps importable)"

    problems: list[str] = []
    if missing:
        mods = ", ".join(f"`{m}` (from `{p}`)" for m, p in missing)
        problems.append(f"missing runtime deps: {mods}")
    if version_skew:
        problems.append(f"version skew: installed autosprint=={installed_version} but this checkout's pyproject.toml is {pyproject_version} — the installed metadata is stale")
    autosprint_dir = _project_root()
    hint = f"Stale install detected — {'; '.join(problems)}.\n      Fix: `python -m pip uninstall -y autosprint` (if pip-installed), then `uv tool install --editable {autosprint_dir}`. This gives autosprint its own venv with pyproject.toml deps, so other pip activity can't silently break dispatch."
    return False, hint


async def _doctor_probe(assistant: str) -> tuple[bool, str]:
    """Dispatch one trivial prompt to `assistant`'s cheapest agent to confirm that
    auth + dispatch actually work end to end. Returns (ok, human-readable detail)."""
    from autosprint.agents import AGENTS
    from autosprint.dispatch import query_agent

    agent = AGENTS[_DOCTOR_PROBE_AGENT_KEY[assistant]]
    try:
        reply = await query_agent(agent, "Reply with the single word: OK", skip_cache=True, phase_tag="[doctor]")
        if reply and reply.strip():
            return True, f"{assistant} dispatch — live round-trip OK (probed {agent['model']})"
        return False, f"{assistant} dispatch returned an empty response"
    except Exception as e:
        return False, f"{assistant} dispatch failed — {type(e).__name__}: {e}"


def probe_backends(warn_only: bool = False) -> bool:
    """Live round-trip probe of every AI backend the configured team dispatches to — one cheap call per backend, the same probe `autosprint doctor` uses. Catches breakage that static PATH checks can't see (a Copilot SDK that lost its bundled CLI binary, an expired Claude login, a hit usage cap) *before* a multi-hour run starts limping with half its council dead. `warn_only=True` (init's mode) prints a warning on failure and returns False — init's job is seeding files, and auth may legitimately not be set up yet; the run path raises RuntimeError with a pointer to `autosprint doctor`. Skipped entirely (returns True) in debug/cache modes (LOG_LEVEL <= 15) so dev loops and tests never make live calls."""
    if config.LOG_LEVEL <= 15:
        printlev("[probe] Skipping backend round-trip probe (LOG_LEVEL <= 15 debug/cache mode).", level=20)
        return True
    label = "init" if warn_only else "prepare"
    printlev(f"[{label}] Probing AI backends (one cheap call per backend)...", level=100)
    failures: list[str] = []
    for assistant in sorted(_required_assistants_for_run()):
        if assistant not in _DOCTOR_PROBE_AGENT_KEY:
            continue
        ok, detail = asyncio.run(_doctor_probe(assistant))
        printlev(f"[{label}] {'✅' if ok else '❌'} {detail}", level=100)
        if not ok:
            failures.append(detail)
    if not failures:
        return True
    msg = "Backend probe failed:\n  " + "\n  ".join(failures) + "\n  Fix auth/install before starting a run — `autosprint doctor` prints the full checklist."
    if warn_only:
        printlev(f"[{label}] ⚠ {msg}", level=100)
        return False
    raise RuntimeError(msg)


def run_doctor() -> None:
    """`autosprint doctor` — verify the configured setup can actually run a sprint:
    the target repo, destination.md, the CLI(s) the team needs, and one live
    round-trip per backend in use (Claude / Copilot) confirming auth + dispatch.
    Prints a checklist; exits non-zero (SystemExit) if any hard check fails."""
    from autosprint.banners import section_banner

    try:
        printlev(f"\n{section_banner('DOCTOR', 'START')}\n", level=100)
        results: list[bool] = []

        def check(ok: bool, msg: str) -> None:
            results.append(ok)
            printlev(f"[doctor] {'✅' if ok else '❌'} {msg}", level=100)

        # 1. Install health — runtime-dep importability + version-skew between installed metadata and this checkout's pyproject.
        # Runs first so a stale install is the first thing the user sees, not the symptom (dispatch errors several minutes in).
        install_ok, install_msg = _check_install_health()
        check(install_ok, install_msg)

        # 2. Target repo.
        target = config.TARGET_REPO_PATH
        if target.resolve() == _project_root().resolve():
            check(False, f"Target repo is the autosprint repo itself ({target}) — cd into your project, or pass --target PATH.")
        elif not target.is_dir():
            check(False, f"Target repo path does not exist: {target}")
        elif not (target / ".git").exists():
            check(False, f"Target repo is not a git repository: {target} — run `git init` there.")
        else:
            check(True, f"Target repo: {target}")

        # 2. destination.md.
        dest = target / DESTINATION_FILENAME
        if not dest.exists():
            check(False, f"{DESTINATION_FILENAME} is missing — run `autosprint init`.")
        else:
            chars = len((dest.read_text(encoding="utf-8") or "").strip())
            check(chars >= 200, f"{DESTINATION_FILENAME} present ({chars} chars)" + ("" if chars >= 200 else " — looks like a placeholder; flesh it out with the /grill-destination skill"))

        # 3. CLI dependencies for the configured team.
        try:
            _check_cli_deps_or_abort()
            check(True, "Required CLI(s) for the configured team are on PATH")
        except RuntimeError as e:
            check(False, str(e).splitlines()[0])

        # 4. Live round-trip — one cheap call per backend the configured team dispatches to.
        for assistant in sorted(_required_assistants_for_run()):
            if assistant not in _DOCTOR_PROBE_AGENT_KEY:
                continue
            ok, detail = asyncio.run(_doctor_probe(assistant))
            check(ok, detail)

        # 5. Resolved config, for the record.
        _print_init_config_summary()

        failures = results.count(False)
        if failures:
            printlev(f"\n[doctor] ❌ {failures} check(s) failed — fix the above before running a sprint.\n{section_banner('DOCTOR', 'END')}", level=100)
            raise SystemExit(1)
        printlev(f"\n[doctor] ✅ All checks passed — the setup is ready to run.\n{section_banner('DOCTOR', 'END')}", level=100)
    except SystemExit:
        raise
    except Exception as e:
        raise add_context(e, "Failed to run autosprint doctor") from e
