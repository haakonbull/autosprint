"""SQLite mirror of run/sprint/task data — additive observability layer.

Writes to TARGET_REPO/autosprint/runs.db alongside the existing markdown
logs. The flat ``.log`` files remain authoritative and human-readable; this
DB exists purely so retrospective queries ("which task names get reverted
most?", "what's the per-model refusal rate?", "is the refusal rate growing
with codebase size?") become a one-liner instead of a grep expedition.

Design rules:
- **Never break the loop.** Every public function swallows its own
  exceptions and emits a ``printlev`` warning instead. A locked file, a
  read-only filesystem, or a corrupt DB must not abort a sprint.
- **No-op in ``FAKE_IMPLEMENT`` mode** — mirrors ``append_run_log`` so fake
  runs don't pollute the real history that drives downstream analysis.
- **Schema is idempotent.** ``CREATE TABLE IF NOT EXISTS`` on every
  connection so the user can delete the file at any time and the next write
  rebuilds it cleanly.
- **Single-process model.** The orchestrator runs one PIT loop per process;
  the current run id lives in module state (mirrors how
  ``_INITIAL_TESTS_SUMMARY`` is kept in ``orchestrator.py``).
"""

from __future__ import annotations

import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from autosprint.config import config
from autosprint.output import printlev

_DB_FILENAME = "autosprint/runs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    target_repo TEXT NOT NULL,
    team TEXT NOT NULL,
    implement_agent TEXT NOT NULL,
    end_reason TEXT
);
CREATE TABLE IF NOT EXISTS task_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    sprint_no INTEGER NOT NULL,
    logged_at TEXT NOT NULL,
    task_title TEXT NOT NULL,
    story_points INTEGER,
    implement_status TEXT NOT NULL,
    test_status TEXT NOT NULL,
    outcome TEXT NOT NULL,
    revert_reason TEXT,
    recovered_by_fallback TEXT,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE INDEX IF NOT EXISTS idx_task_attempts_run ON task_attempts(run_id);
CREATE INDEX IF NOT EXISTS idx_task_attempts_outcome ON task_attempts(outcome);
"""

# Idempotent column-add migrations for DBs created before each new column existed.
# SQLite has no IF NOT EXISTS for ALTER TABLE ADD COLUMN, so we wrap each in a
# try/except OperationalError; the failure mode "duplicate column name" is the
# expected steady-state for already-migrated DBs and is silently tolerated.
_MIGRATIONS: tuple[str, ...] = ("ALTER TABLE task_attempts ADD COLUMN recovered_by_fallback TEXT;",)

_CURRENT_RUN_ID: int | None = None


def _db_path() -> Path:
    return config.TARGET_REPO_PATH / _DB_FILENAME


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_gitignore_excludes_runs_db() -> None:
    """Idempotently add ``runs.db`` to ``TARGET_REPO/autosprint/.gitignore`` so the local SQLite scratch never accidentally lands in the target repo's git history. Without this, the prepare-phase uncommitted-changes guard sees a modified ``runs.db`` on every sprint and stalls waiting for a Y/n on stdin (real bug observed 2026-04-25 on game1). Failure is silently swallowed — we never want gitignore housekeeping to break a sprint."""
    try:
        gitignore = config.TARGET_REPO_PATH / "autosprint" / ".gitignore"
        gitignore.parent.mkdir(parents=True, exist_ok=True)
        if gitignore.exists():
            existing = gitignore.read_text(encoding="utf-8")
            # Crude substring check — fine because the line we add is unique
            # enough that any false positive means the entry is already there.
            if "runs.db" in existing:
                return
            new_text = existing.rstrip("\n") + "\nruns.db\n"
        else:
            new_text = "# autosprint local scratch — not part of the target repo's history\nruns.db\n"
        gitignore.write_text(new_text, encoding="utf-8")
    except Exception as e:
        printlev(f"[db] _ensure_gitignore_excludes_runs_db failed (non-fatal): {e}", level=50)


def _untrack_runs_db_if_tracked() -> None:
    """If ``runs.db`` is tracked in the target repo (residual from a run that predates the gitignore entry), untrack it from the git index without removing the working-tree file. Gitignore only prevents tracking of NEW files; an already-tracked ``runs.db`` keeps being tracked, lands in commits, and — worse on Windows — gets touched by ``git restore .`` during sprint revert, where SQLite's file lock makes the restore fail with exit 255 (real bug observed 2026-05-29). Self-heals on next ``_connect()``. Failure is silently swallowed — never break a sprint over housekeeping."""
    try:
        # ls-files --error-unmatch returns non-zero when the path is not tracked
        # — exactly what we want to gate on. capture_output suppresses git's
        # error text on the not-tracked case so it doesn't pollute console.
        check = subprocess.run(
            ["git", "ls-files", "--error-unmatch", _DB_FILENAME],
            capture_output=True,
            text=True,
            cwd=config.TARGET_REPO_PATH,
        )
        if check.returncode != 0:
            return  # not tracked, nothing to do
        subprocess.run(
            ["git", "rm", "--cached", "--quiet", _DB_FILENAME],
            capture_output=True,
            text=True,
            cwd=config.TARGET_REPO_PATH,
            check=True,
        )
        printlev(f"[db] Untracked {_DB_FILENAME} from git index (local scratch only — gets recreated automatically). Next commit will drop it from history.", level=50)
    except Exception as e:
        printlev(f"[db] _untrack_runs_db_if_tracked failed (non-fatal): {e}", level=50)


def _connect() -> sqlite3.Connection:
    _ensure_gitignore_excludes_runs_db()
    _untrack_runs_db_if_tracked()
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, timeout=5.0)
    conn.executescript(_SCHEMA)
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            # Expected when the column already exists on an already-migrated DB.
            # Any other OperationalError (corrupt DB, locked, etc.) re-surfaces
            # via the next caller's exception handler — same swallow pattern as
            # the rest of this module's public functions.
            pass
    return conn


def record_run_start(target_repo: str, team: str, implement_agent: str) -> None:
    """Insert a new row into ``runs`` and stash the id in module state so subsequent ``record_task_attempt`` calls can FK to it. No-op in ``FAKE_IMPLEMENT`` mode. Failures are logged as warnings, never raised — this layer must not be able to break the sprint loop."""
    global _CURRENT_RUN_ID
    if config.FAKE_IMPLEMENT:
        return
    try:
        # `closing()` is required because sqlite3.Connection's own context manager
        # only commits/rolls back the transaction — it does NOT close the
        # connection. On Windows, an un-closed connection holds the file lock on
        # runs.db until GC; if a leaked connection is still alive when
        # `git_restore()` fires during sprint revert, `git restore .` returns
        # exit 255 because the file is locked. Wrapping in `closing()` guarantees
        # a real `conn.close()` and releases the lock immediately.
        with closing(_connect()) as conn:
            cur = conn.execute(
                "INSERT INTO runs (started_at, target_repo, team, implement_agent) VALUES (?, ?, ?, ?)",
                (_now(), target_repo, team, implement_agent),
            )
            _CURRENT_RUN_ID = cur.lastrowid
    except Exception as e:
        printlev(f"[db] record_run_start failed (non-fatal): {e}", level=50)


def record_run_end(end_reason: str) -> None:
    """Set ``ended_at`` and ``end_reason`` on the current run, then clear the module state. No-op in ``FAKE_IMPLEMENT`` mode or when no run is currently active (process killed mid-run, etc.)."""
    global _CURRENT_RUN_ID
    if config.FAKE_IMPLEMENT:
        return
    if _CURRENT_RUN_ID is None:
        return
    try:
        with closing(_connect()) as conn:
            conn.execute(
                "UPDATE runs SET ended_at = ?, end_reason = ? WHERE id = ?",
                (_now(), end_reason, _CURRENT_RUN_ID),
            )
    except Exception as e:
        printlev(f"[db] record_run_end failed (non-fatal): {e}", level=50)
    finally:
        _CURRENT_RUN_ID = None


def record_task_attempt(
    sprint_no: int,
    task_title: str,
    story_points: int | None,
    implement_status: str,
    test_status: str,
    outcome: str,
    revert_reason: str | None = None,
    recovered_by_fallback: str | None = None,
) -> None:
    """Insert one row into ``task_attempts``. Mirrors the columns of ``sprint-outcomes.log`` plus an explicit ``revert_reason`` (matches ``RevertReason`` enum values when known; ``NULL`` for non-revert outcomes) and ``recovered_by_fallback`` (set to the fallback agent's key when the refusal-fallback rescued a refused sprint; ``NULL`` otherwise — including for primary-only successes and for sprints that ultimately reverted). Silently skipped when no run is active so the FK relationship can't be violated by an out-of-order write."""
    if config.FAKE_IMPLEMENT:
        return
    if _CURRENT_RUN_ID is None:
        return
    try:
        with closing(_connect()) as conn:
            conn.execute(
                """
                INSERT INTO task_attempts
                (run_id, sprint_no, logged_at, task_title, story_points, implement_status, test_status, outcome, revert_reason, recovered_by_fallback)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (_CURRENT_RUN_ID, sprint_no, _now(), task_title, story_points, implement_status, test_status, outcome, revert_reason, recovered_by_fallback),
            )
    except Exception as e:
        printlev(f"[db] record_task_attempt failed (non-fatal): {e}", level=50)


def current_run_id() -> int | None:
    """Returns the current run's database id, or None if no run is active. Used by tests and any caller that needs to associate auxiliary data with the active run."""
    return _CURRENT_RUN_ID


def _reset_for_tests() -> None:
    """Test-only helper: clear the module-level run id so tests can simulate fresh runs without leaking state across the suite."""
    global _CURRENT_RUN_ID
    _CURRENT_RUN_ID = None
