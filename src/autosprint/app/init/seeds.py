"""Extracted from the original autosprint.app.init module."""

from __future__ import annotations

import shutil

from autosprint.config import _project_root, config
from autosprint.util.errors import add_context
from autosprint.util.output import printlev
from autosprint.util.paths import (
    ADR_FILENAME,
    AUTOSPRINT_DIR_NAME,
    DESTINATION_FILENAME,
)


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
