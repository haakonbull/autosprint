"""Tests for the plan.md parser, writer, and mutation helpers.

Pure functions, no LLM calls. Tests against tmp_path file system.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autosprint.plan import (
    BlockedTask,
    CompletedTask,
    PendingTask,
    Plan,
    defer_pending_tasks,
    mark_top_pending_done,
    parse_plan,
    read_plan_md,
    serialise_plan,
    write_plan_md,
)


def test_parse_empty_plan() -> None:
    plan = parse_plan("")
    assert plan.is_empty()
    assert plan.completed == []
    assert plan.pending == []


def test_parse_plan_with_completed_and_pending() -> None:
    text = """# Plan

## Recent completed

- [x] Load dataset (abc1234)
  Loaded the CSV and printed shape.
- [x] Clean nulls (def5678)
  Dropped 3 rows with null target.

## Pending

- [ ] Train baseline model
  Logistic regression with 80/20 split.
- [ ] Feature engineering
  Add interaction terms for sleep and stress.
"""
    plan = parse_plan(text)
    assert len(plan.completed) == 2
    assert plan.completed[0].title == "Load dataset"
    assert plan.completed[0].commit_hash == "abc1234"
    assert "Loaded the CSV" in plan.completed[0].summary
    assert len(plan.pending) == 2
    assert plan.pending[0].title == "Train baseline model"
    assert "Logistic regression" in plan.pending[0].description


def test_parse_plan_completed_without_hash() -> None:
    text = """## Recent completed

- [x] Some task
  A summary.
"""
    plan = parse_plan(text)
    assert len(plan.completed) == 1
    assert plan.completed[0].commit_hash == ""


def test_parse_plan_pending_without_description() -> None:
    text = """## Pending

- [ ] Bare task
"""
    plan = parse_plan(text)
    assert len(plan.pending) == 1
    assert plan.pending[0].title == "Bare task"
    assert plan.pending[0].description == ""


def test_serialise_round_trip() -> None:
    plan = Plan(
        completed=[CompletedTask(title="A", summary="did A", commit_hash="abc1234")],
        pending=[PendingTask(title="B", description="do B")],
        blocked=[BlockedTask(title="C", description="blocked until source exists")],
    )
    text = serialise_plan(plan)
    parsed = parse_plan(text)
    assert len(parsed.completed) == 1
    assert parsed.completed[0].title == "A"
    assert parsed.completed[0].commit_hash == "abc1234"
    assert len(parsed.pending) == 1
    assert parsed.pending[0].title == "B"
    assert len(parsed.blocked) == 1
    assert parsed.blocked[0].title == "C"


def test_serialise_truncates_completed_to_recent_count() -> None:
    plan = Plan(completed=[CompletedTask(title=f"task{i}", summary="x") for i in range(10)])
    text = serialise_plan(plan, recent_count=3)
    parsed = parse_plan(text)
    assert len(parsed.completed) == 3
    assert parsed.completed[0].title == "task7"
    assert parsed.completed[-1].title == "task9"


def test_serialise_plan_summary_renders_as_blockquote() -> None:
    """plan_summary is rendered as a blockquote under ## Pending without disturbing task parsing."""
    plan = Plan(pending=[PendingTask(title="B", description="do B")])
    text = serialise_plan(plan, plan_summary="Merged 9 proposals into 3 tasks.")
    assert "> Merged 9 proposals into 3 tasks." in text
    parsed = parse_plan(text)
    assert len(parsed.pending) == 1
    assert parsed.pending[0].title == "B"


def test_serialise_no_plan_summary_by_default() -> None:
    """The default call writes no blockquote — loop-mode plans stay clean."""
    plan = Plan(pending=[PendingTask(title="B", description="do B")])
    assert ">" not in serialise_plan(plan)


def test_parse_blocked_deferred_section() -> None:
    text = """# Plan

## Pending

- [ ] Actionable task (2)

## Blocked / Deferred

- [ ] Integrate July Section 232 print (3)
  Blocked until official print exists.
"""
    plan = parse_plan(text)
    assert [task.title for task in plan.pending] == ["Actionable task (2)"]
    assert [task.title for task in plan.blocked] == ["Integrate July Section 232 print (3)"]
    assert "official print" in plan.blocked[0].description


def test_serialise_multiline_description_indents_each_line() -> None:
    """A multi-line description (e.g. a trailing Consensus/Importance line) is indented under the task and round-trips."""
    plan = Plan(pending=[PendingTask(title="B", description="do B\nConsensus: 5/6 · Importance: must")])
    text = serialise_plan(plan)
    assert "  do B" in text
    assert "  Consensus: 5/6 · Importance: must" in text
    parsed = parse_plan(text)
    assert "Consensus: 5/6" in parsed.pending[0].description
    assert "Importance: must" in parsed.pending[0].description


def test_read_plan_missing_file_returns_empty(tmp_path: Path) -> None:
    plan = read_plan_md(tmp_path)
    assert plan.is_empty()


def test_write_then_read(tmp_path: Path) -> None:
    plan = Plan(
        completed=[CompletedTask(title="done", summary="finished")],
        pending=[PendingTask(title="todo", description="do it")],
    )
    write_plan_md(tmp_path, plan)
    loaded = read_plan_md(tmp_path)
    assert len(loaded.completed) == 1
    assert len(loaded.pending) == 1
    assert loaded.pending[0].title == "todo"


def test_mark_top_pending_done_moves_task(tmp_path: Path) -> None:
    plan = Plan(
        pending=[
            PendingTask(title="first", description="do first"),
            PendingTask(title="second", description="do second"),
        ]
    )
    write_plan_md(tmp_path, plan)
    updated = mark_top_pending_done(tmp_path, summary="first done", commit_hash="abc1234")
    assert len(updated.pending) == 1
    assert updated.pending[0].title == "second"
    assert len(updated.completed) == 1
    assert updated.completed[0].title == "first"
    assert updated.completed[0].summary == "first done"
    assert updated.completed[0].commit_hash == "abc1234"


def test_mark_top_pending_done_raises_when_empty(tmp_path: Path) -> None:
    plan = Plan()
    write_plan_md(tmp_path, plan)
    with pytest.raises(Exception):
        mark_top_pending_done(tmp_path, summary="x")


def test_defer_pending_tasks_moves_only_matching_titles(tmp_path: Path) -> None:
    write_plan_md(
        tmp_path,
        Plan(
            pending=[
                PendingTask(title="Integrate June FOMC after publication (5)", description="Do not start before publication."),
                PendingTask(title="Refresh BTOS source (3)", description="Actionable source refresh."),
            ]
        ),
    )

    updated = defer_pending_tasks(
        tmp_path,
        ["Integrate June FOMC after publication (5)"],
        "FOMC is not published yet.",
        sprint_number=7,
    )

    assert [task.title for task in updated.pending] == ["Refresh BTOS source (3)"]
    assert [task.title for task in updated.blocked] == ["Integrate June FOMC after publication (5)"]
    assert "Blocked after sprint 7" in updated.blocked[0].description


def test_top_pending_returns_first() -> None:
    plan = Plan(pending=[PendingTask(title="first"), PendingTask(title="second")])
    assert plan.top_pending() is not None
    assert plan.top_pending().title == "first"  # type: ignore[union-attr]


def test_top_pending_empty_plan_returns_none() -> None:
    assert Plan().top_pending() is None
