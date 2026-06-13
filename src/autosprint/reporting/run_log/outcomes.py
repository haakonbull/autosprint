"""Extracted from the original autosprint.reporting.run_log module."""

from __future__ import annotations

from datetime import UTC, datetime

from autosprint.config import config
from autosprint.infra import db
from autosprint.reporting.run_log.stats import extract_story_points
from autosprint.util.errors import add_context
from autosprint.util.paths import (
    SPRINT_LOG_FILENAME,
)

_RUN_LOG_HEADER = "# sprint | timestamp            | sp | title                                             | implement | test     | outcome\n"


def append_run_log(sprint_number: int, task_title: str, implement_status: str, test_status: str, outcome: str, revert_reason: str | None = None, recovered_by_fallback: str | None = None) -> None:
    """Append a single sprint outcome line to sprint-outcomes.log; writes a header line on first creation. `sprint_number` is the first column in the emitted row. `revert_reason` and `recovered_by_fallback` are propagated only to the SQLite mirror (the markdown log keeps its 5-column shape so existing readers/grep patterns stay valid). No-op in FAKE_IMPLEMENT mode so fake runs don't pollute the real run history used by escalation and plan-agent context."""
    if config.FAKE_IMPLEMENT:
        return
    log_path = config.TARGET_REPO_PATH / SPRINT_LOG_FILENAME
    sp = extract_story_points(task_title)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        is_new = not log_path.exists()
        with log_path.open("a", encoding="utf-8") as f:
            if is_new:
                f.write(_RUN_LOG_HEADER)
            sp_str = f"{sp:2d}" if sp is not None else " ?"
            f.write(f"{sprint_number} | {ts} | {sp_str} | {task_title} | {implement_status} | {test_status} | {outcome}\n")
    except Exception as e:
        raise add_context(e, f'Failed to log line for task "{task_title}" to {log_path}') from e
    db.record_task_attempt(sprint_number, task_title, sp, implement_status, test_status, outcome, revert_reason, recovered_by_fallback)


def write_run_separator() -> None:
    """Write a '# === run started <ts> ===' comment to sprint-outcomes.log so new autosprint runs are visually distinguishable when scrolling the file. Writes the header first if the file doesn't exist. Also opens a row in the SQLite mirror via `db.record_run_start`. No-op in FAKE_IMPLEMENT mode."""
    if config.FAKE_IMPLEMENT:
        return
    log_path = config.TARGET_REPO_PATH / SPRINT_LOG_FILENAME
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        is_new = not log_path.exists()
        with log_path.open("a", encoding="utf-8") as f:
            if is_new:
                f.write(_RUN_LOG_HEADER)
            f.write(f"# === run started {ts} ===\n")
    except Exception as e:
        raise add_context(e, f"Failed to write run separator to {log_path}") from e
    db.record_run_start(str(config.TARGET_REPO_PATH), config.TEAM, config.IMPLEMENT_AGENT)


def write_run_ended_separator(exit_reason: str) -> None:
    """Write a '# === run ended <ts> ({exit_reason}) ===' comment to sprint-outcomes.log so post-hoc readers can tell a clean termination from a process that died mid-run. Pairs with `write_run_separator()` which marks the start. Also closes the SQLite mirror row via `db.record_run_end`. No-op in FAKE_IMPLEMENT mode."""
    if config.FAKE_IMPLEMENT:
        return
    log_path = config.TARGET_REPO_PATH / SPRINT_LOG_FILENAME
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"# === run ended {ts} ({exit_reason}) ===\n")
    except Exception as e:
        raise add_context(e, f"Failed to write run-ended separator to {log_path}") from e
    db.record_run_end(exit_reason)
