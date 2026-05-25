"""Tests for the SQLite mirror in autosprint.db.

Each test points config.TARGET_REPO at a tmp_path so the runs.db lives
inside the test's scratch directory and never touches a real repo.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from autosprint import db
from autosprint.config import config


@pytest.fixture
def tmp_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "FAKE_IMPLEMENT", False)
    return tmp_path


def _query(tmp_path: Path, sql: str) -> list[tuple]:
    with sqlite3.connect(tmp_path / "autosprint" / "runs.db") as conn:
        return list(conn.execute(sql))


def test_record_run_start_creates_db_file_and_row(tmp_repo: Path) -> None:
    db.record_run_start("/some/repo", "builder", "implementor_gpt55")
    assert (tmp_repo / "autosprint" / "runs.db").exists()
    rows = _query(tmp_repo, "SELECT target_repo, team, implement_agent, ended_at, end_reason FROM runs")
    assert rows == [("/some/repo", "builder", "implementor_gpt55", None, None)]
    assert db.current_run_id() is not None


def test_record_run_end_sets_ended_at_and_clears_state(tmp_repo: Path) -> None:
    db.record_run_start("/x", "builder", "implementor_gpt55")
    assert db.current_run_id() is not None
    db.record_run_end("max sprints reached (10)")
    rows = _query(tmp_repo, "SELECT ended_at, end_reason FROM runs")
    assert len(rows) == 1
    ended_at, end_reason = rows[0]
    assert ended_at is not None
    assert end_reason == "max sprints reached (10)"
    assert db.current_run_id() is None


def test_record_task_attempt_inserts_row_with_fk(tmp_repo: Path) -> None:
    db.record_run_start("/x", "builder", "implementor_gpt55")
    run_id = db.current_run_id()
    db.record_task_attempt(
        sprint_no=1,
        task_title="Add Foo (3)",
        story_points=3,
        implement_status="OK",
        test_status="OK",
        outcome="abc1234",
        revert_reason=None,
    )
    rows = _query(tmp_repo, "SELECT run_id, sprint_no, task_title, story_points, implement_status, test_status, outcome, revert_reason FROM task_attempts")
    assert rows == [(run_id, 1, "Add Foo (3)", 3, "OK", "OK", "abc1234", None)]


def test_record_task_attempt_without_active_run_is_silent(tmp_repo: Path) -> None:
    # No record_run_start; record_task_attempt should silently skip rather than raise.
    db.record_task_attempt(1, "T", 2, "OK", "OK", "sha", None)
    assert not (tmp_repo / "autosprint" / "runs.db").exists()


def test_record_task_attempt_persists_revert_reason(tmp_repo: Path) -> None:
    db.record_run_start("/x", "builder", "implementor_opus47")
    db.record_task_attempt(7, "Refused (2)", 2, "FAILED", "n/a", "REVERTED", revert_reason="implement_refused")
    rows = _query(tmp_repo, "SELECT revert_reason FROM task_attempts")
    assert rows == [("implement_refused",)]


def test_fake_implement_mode_skips_all_writes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "FAKE_IMPLEMENT", True)
    db.record_run_start("/x", "builder", "implementor_gpt55")
    db.record_task_attempt(1, "T", 2, "OK", "OK", "sha", None)
    db.record_run_end("done")
    assert not (tmp_path / "autosprint" / "runs.db").exists()
    assert db.current_run_id() is None


def test_schema_is_idempotent_when_db_already_exists(tmp_repo: Path) -> None:
    db.record_run_start("/x", "builder", "implementor_gpt55")
    db.record_run_end("done")
    db._reset_for_tests()
    # Second run should reuse the existing file without raising.
    db.record_run_start("/y", "duo", "implementor_opus47")
    rows = _query(tmp_repo, "SELECT target_repo FROM runs ORDER BY id")
    assert [r[0] for r in rows] == ["/x", "/y"]


def test_db_failure_does_not_raise_to_caller(tmp_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Force a connection failure by pointing the DB path at an unwritable location.
    bad = tmp_repo / "nope" / "runs.db"

    def boom() -> Path:
        return bad

    monkeypatch.setattr(db, "_db_path", boom)

    # Make the directory exist but be a file (so mkdir succeeds-ish but connect fails).
    (tmp_repo / "nope").write_text("not-a-dir", encoding="utf-8")

    # All three public functions must absorb the resulting exception.
    db.record_run_start("/x", "builder", "implementor_gpt55")
    db.record_task_attempt(1, "T", 2, "OK", "OK", "sha", None)
    db.record_run_end("done")


def test_record_task_attempt_persists_recovered_by_fallback(tmp_repo: Path) -> None:
    """The refusal-fallback path records which fallback agent rescued a sprint so post-hoc queries can answer 'how often did the fallback fire and which model rescued?'."""
    db.record_run_start("/x", "builder", "implementor_opus47")
    db.record_task_attempt(7, "Add Foo (3)", 3, "OK", "pending", "sha123", revert_reason=None, recovered_by_fallback="implementor_gpt55")
    rows = _query(tmp_repo, "SELECT recovered_by_fallback FROM task_attempts")
    assert rows == [("implementor_gpt55",)]


def test_record_task_attempt_default_recovered_by_fallback_is_null(tmp_repo: Path) -> None:
    """The vast majority of writes should leave recovered_by_fallback NULL (only refusal-fallback-rescued sprints set it). The kwarg default must therefore be None, not the empty string."""
    db.record_run_start("/x", "builder", "implementor_opus47")
    db.record_task_attempt(1, "T (2)", 2, "OK", "OK", "sha", None)
    rows = _query(tmp_repo, "SELECT recovered_by_fallback FROM task_attempts")
    assert rows == [(None,)]


def test_schema_migration_adds_recovered_by_fallback_to_legacy_db(tmp_repo: Path) -> None:
    """A pre-fallback-feature DB created without recovered_by_fallback must be migrated transparently on the next connection — without losing any existing rows. Simulates upgrading autosprint on a target repo that already has a populated runs.db."""
    legacy_schema = """
    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL, ended_at TEXT, target_repo TEXT NOT NULL,
        team TEXT NOT NULL, implement_agent TEXT NOT NULL, end_reason TEXT
    );
    CREATE TABLE IF NOT EXISTS task_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL, sprint_no INTEGER NOT NULL, logged_at TEXT NOT NULL,
        task_title TEXT NOT NULL, story_points INTEGER,
        implement_status TEXT NOT NULL, test_status TEXT NOT NULL,
        outcome TEXT NOT NULL, revert_reason TEXT,
        FOREIGN KEY(run_id) REFERENCES runs(id)
    );
    """
    db_path = tmp_repo / "autosprint" / "runs.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(legacy_schema)
        conn.execute("INSERT INTO runs (started_at, target_repo, team, implement_agent) VALUES (?, ?, ?, ?)", ("t0", "/r", "builder", "implementor_opus47"))
        conn.execute("INSERT INTO task_attempts (run_id, sprint_no, logged_at, task_title, story_points, implement_status, test_status, outcome, revert_reason) VALUES (1, 1, 't1', 'legacy task', 2, 'OK', 'OK', 'sha', NULL)")

    # Trigger migration via a normal write through the public API.
    db.record_run_start("/r2", "builder", "implementor_opus47")
    db.record_task_attempt(1, "post-migration (2)", 2, "OK", "OK", "sha2", None, recovered_by_fallback="implementor_gpt55")

    # Legacy row must survive untouched; new row must carry the new column.
    rows = _query(tmp_repo, "SELECT task_title, recovered_by_fallback FROM task_attempts ORDER BY id")
    assert rows == [("legacy task", None), ("post-migration (2)", "implementor_gpt55")]


def test_connect_creates_gitignore_excluding_runs_db(tmp_repo: Path) -> None:
    """The first DB connection must drop a ``.gitignore`` next to the DB that excludes ``runs.db`` from the target repo's git history. Without this, autosprint's prepare-phase uncommitted-changes guard stalls every sprint waiting for confirmation that the modified runs.db is OK to commit (real bug seen on game1 2026-04-25)."""
    db.record_run_start("/x", "builder", "implementor_opus47")
    gitignore = tmp_repo / "autosprint" / ".gitignore"
    assert gitignore.exists(), "First DB write must create autosprint/.gitignore"
    contents = gitignore.read_text(encoding="utf-8")
    assert "runs.db" in contents


def test_connect_appends_runs_db_to_existing_gitignore(tmp_repo: Path) -> None:
    """An existing ``autosprint/.gitignore`` (with other entries) must be preserved and have ``runs.db`` appended — not overwritten. Critical because target repos may have their own meaningful entries in this file."""
    gitignore = tmp_repo / "autosprint" / ".gitignore"
    gitignore.parent.mkdir(parents=True, exist_ok=True)
    gitignore.write_text("# user's existing rules\n*.tmp\nlocal-secrets/\n", encoding="utf-8")
    db.record_run_start("/x", "builder", "implementor_opus47")
    contents = gitignore.read_text(encoding="utf-8")
    assert "*.tmp" in contents, "Existing entries must survive"
    assert "local-secrets/" in contents
    assert "runs.db" in contents


def test_connect_does_not_duplicate_runs_db_entry_on_subsequent_connections(tmp_repo: Path) -> None:
    """Repeated DB connections (across many sprints) must not append duplicate ``runs.db`` entries — the file should stabilise after the first write."""
    db.record_run_start("/x", "builder", "implementor_opus47")
    db.record_run_end("done")
    db._reset_for_tests()
    db.record_run_start("/y", "builder", "implementor_opus47")
    db.record_run_end("done")
    db._reset_for_tests()
    db.record_run_start("/z", "builder", "implementor_opus47")
    contents = (tmp_repo / "autosprint" / ".gitignore").read_text(encoding="utf-8")
    assert contents.count("runs.db") == 1, f"Expected exactly one 'runs.db' line, got: {contents!r}"


def test_schema_migration_is_idempotent_on_already_migrated_db(tmp_repo: Path) -> None:
    """Running the migration twice (e.g., across two autosprint runs) must not raise — the duplicate-column error is the expected steady-state and is silently tolerated."""
    db.record_run_start("/x", "builder", "implementor_opus47")
    db.record_task_attempt(1, "T (2)", 2, "OK", "OK", "sha", None)
    db.record_run_end("done")
    db._reset_for_tests()

    # Second connection cycle re-runs _MIGRATIONS; must not raise.
    db.record_run_start("/y", "builder", "implementor_opus47")
    db.record_task_attempt(1, "T2 (2)", 2, "OK", "OK", "sha2", None)
    rows = _query(tmp_repo, "SELECT COUNT(*) FROM task_attempts")
    assert rows == [(2,)]
