"""Stop-control file lifecycle: write, poll, and raise.

A running PIT loop and the `autosprint stop` command communicate through a
small control file under the target repo. Keeping the write/read/raise trio
here (below the phases and app layers) lets the Plan phase poll for an
immediate stop without reaching up into the CLI.
"""

from __future__ import annotations

from datetime import UTC, datetime

from autosprint.config import config
from autosprint.util.errors import StopRequested, add_context
from autosprint.util.output import printlev
from autosprint.util.paths import AUTOSPRINT_DIR_NAME, STOP_CONTROL_FILENAME, STOP_NOW_CONTROL_FILENAME


def run_stop(immediate: bool) -> None:
    """Drop a small control file under TARGET_REPO/autosprint/ so a running PIT loop pointed at the same repo notices and exits. 'stop' means finish the current sprint and exit cleanly; 'stop-now' means stop mid-sprint and revert uncommitted changes. The live loop deletes the file on consumption, so a stale file can never hijack the next run."""
    try:
        autosprint_dir = config.TARGET_REPO_PATH / AUTOSPRINT_DIR_NAME
        autosprint_dir.mkdir(parents=True, exist_ok=True)
        filename = STOP_NOW_CONTROL_FILENAME if immediate else STOP_CONTROL_FILENAME
        control_file = config.TARGET_REPO_PATH / filename
        control_file.write_text(datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ\n"), encoding="utf-8")
        mode = "stop-now (immediate + revert)" if immediate else "stop (soft — finish current sprint, then exit)"
        printlev(f"\n[stop] Wrote {filename} in {config.TARGET_REPO_PATH}.", level=100)
        printlev(f"[stop] Mode: {mode}", level=100)
        printlev("[stop] The live run deletes the control file once it responds, so no cleanup is needed.", level=100)
    except Exception as e:
        raise add_context(e, f"Failed to write stop control file (immediate={immediate})") from e


def check_stop_request(immediate_only: bool = False) -> str | None:
    """Return 'immediate', 'soft', or None depending on which stop control file (if any) is present in TARGET_REPO. Deletes the file on detection. Between-phase callers pass immediate_only=True so soft stops don't interrupt an Implement/Test that's already in motion — soft stops only fire at sprint boundaries."""
    try:
        target = config.TARGET_REPO_PATH
        immediate_path = target / STOP_NOW_CONTROL_FILENAME
        if immediate_path.exists():
            immediate_path.unlink()
            return "immediate"
        if immediate_only:
            return None
        soft_path = target / STOP_CONTROL_FILENAME
        if soft_path.exists():
            soft_path.unlink()
            return "soft"
        return None
    except Exception as e:
        raise add_context(e, "Failed to check stop request") from e


def raise_if_stop_between_phases() -> None:
    """Between-phase stop check — only fires for 'stop-now'. Raises StopRequested('immediate') so the current sprint aborts cleanly. Soft stops are deliberately ignored here; the live loop catches them at the sprint boundary."""
    kind = check_stop_request(immediate_only=True)
    if kind == "immediate":
        raise StopRequested(kind)
