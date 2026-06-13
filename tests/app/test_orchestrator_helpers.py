"""Tests for orchestrator-adjacent helpers: implement parse, story points, task grouping,
revert reason classifier, post-revert hint builder, config warning, write_run_separator.

All fast — no LLM calls, no pit_loop.
"""

from pathlib import Path

import pytest

from autosprint.config import Config, config
from autosprint.core.plan import PendingTask, Plan
from autosprint.phases.implement_phase import dump_last_implement_raw
from autosprint.phases.plan_phase import SprintRevertRecord, select_sprint_task_group
from autosprint.phases.plan_phase import build_post_revert_hint as _build_post_revert_hint
from autosprint.reporting.run_log import extract_story_points as _extract_story_points
from autosprint.reporting.run_log import write_run_separator
from autosprint.util.errors import RevertReason
from autosprint.util.errors import revert_reason_shrinks_cap as _revert_reason_shrinks_cap
from autosprint.util.parsing import ImplementResponseMalformed, parse_implement_result
from autosprint.util.paths import LAST_IMPLEMENT_FAILURE_FILENAME, SPRINT_LOG_FILENAME

# ---------------------------------------------------------------------------
# dump_last_implement_raw
# ---------------------------------------------------------------------------


def test_dump_last_implement_raw_writes_sidecar_with_header_and_body(target_repo: Path) -> None:
    raw = "agent response text with ---RESULT---\nno END marker here"
    dump_last_implement_raw(raw, "Malformed response: missing ---END---")
    written = (target_repo / LAST_IMPLEMENT_FAILURE_FILENAME).read_text(encoding="utf-8")
    assert raw in written
    assert "missing ---END---" in written
    assert "# autosprint" in written


def test_dump_last_implement_raw_overwrites_on_subsequent_failures(target_repo: Path) -> None:
    dump_last_implement_raw("first failure body", "reason 1")
    dump_last_implement_raw("second failure body", "reason 2")
    written = (target_repo / LAST_IMPLEMENT_FAILURE_FILENAME).read_text(encoding="utf-8")
    assert "second failure body" in written
    assert "first failure body" not in written  # overwrite, not append
    assert "reason 2" in written


def test_dump_last_implement_raw_noop_in_fake_mode(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    monkeypatch.setattr(config, "FAKE_IMPLEMENT", True)
    dump_last_implement_raw("should not be written", "fake")
    assert not (target_repo / LAST_IMPLEMENT_FAILURE_FILENAME).exists()


# ---------------------------------------------------------------------------
# write_run_separator
# ---------------------------------------------------------------------------


def test_write_run_separator_creates_file_with_header(target_repo: Path) -> None:
    write_run_separator()
    lines = (target_repo / SPRINT_LOG_FILENAME).read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# sprint")  # header first
    assert any("run started" in line for line in lines)


def test_write_run_separator_appends_to_existing_file(target_repo: Path) -> None:
    path = target_repo / SPRINT_LOG_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# existing header\n1 | ts | task | OK | OK | abc\n", encoding="utf-8")
    write_run_separator()
    text = path.read_text(encoding="utf-8")
    assert "1 | ts | task | OK | OK | abc" in text
    assert "run started" in text
    # Header was not rewritten
    assert text.count("# existing header") == 1


def test_write_run_separator_skipped_in_fake_implement(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    monkeypatch.setattr(config, "FAKE_IMPLEMENT", True)
    write_run_separator()
    assert not (target_repo / SPRINT_LOG_FILENAME).exists()


# ---------------------------------------------------------------------------
# parse_implement_result — contract enforcement
# ---------------------------------------------------------------------------


def test_parse_implement_result_success_with_summary() -> None:
    raw = '---RESULT---\n{"status": "success", "summary": "Added retry logic to query_agent."}\n---END---'
    result = parse_implement_result(raw)
    # `resolved_open_questions` defaults to [] when the field is absent (the common case).
    assert result == {"status": "success", "summary": "Added retry logic to query_agent.", "resolved_open_questions": []}


def test_parse_implement_result_failure_with_reason() -> None:
    raw = '---RESULT---\n{"status": "failure", "reason": "Could not resolve the import"}\n---END---'
    result = parse_implement_result(raw)
    assert result == {"status": "failure", "reason": "Could not resolve the import"}


def test_parse_implement_result_failure_without_reason_uses_default() -> None:
    raw = '---RESULT---\n{"status": "failure"}\n---END---'
    result = parse_implement_result(raw)
    assert result["status"] == "failure"
    assert result["reason"] == "(no reason given)"


def test_parse_implement_result_malformed_no_result_block() -> None:
    with pytest.raises(ImplementResponseMalformed, match="No parseable"):
        parse_implement_result("just some text, no result block")


def test_parse_implement_result_malformed_success_without_summary() -> None:
    raw = '---RESULT---\n{"status": "success"}\n---END---'
    with pytest.raises(ImplementResponseMalformed, match="summary"):
        parse_implement_result(raw)


def test_parse_implement_result_malformed_unknown_status() -> None:
    raw = '---RESULT---\n{"status": "maybe", "summary": "hmm"}\n---END---'
    with pytest.raises(ImplementResponseMalformed, match="Unknown"):
        parse_implement_result(raw)


def test_parse_implement_result_finds_json_without_result_fence() -> None:
    """Fallback: if the agent forgot the ---RESULT--- fence but embedded valid JSON, parse_result still extracts it."""
    raw = 'Some preamble text.\n{"status": "success", "summary": "Did the thing."}\nTrailing text.'
    result = parse_implement_result(raw)
    assert result["status"] == "success"
    assert result["summary"] == "Did the thing."


# ---------------------------------------------------------------------------
# _extract_story_points
# ---------------------------------------------------------------------------


def test_extract_story_points_trailing_tag() -> None:
    assert _extract_story_points("Add retry to query_agent (2)") == 2


def test_extract_story_points_tag_with_trailing_whitespace() -> None:
    assert _extract_story_points("Add retry to query_agent (5)   ") == 5


def test_extract_story_points_multi_digit() -> None:
    assert _extract_story_points("Refactor the world (13)") == 13


def test_extract_story_points_missing_tag_returns_none() -> None:
    assert _extract_story_points("Add retry to query_agent") is None


def test_extract_story_points_tag_not_at_end_returns_none() -> None:
    assert _extract_story_points("Touch file (2) and then do other stuff") is None


def test_extract_story_points_empty_string() -> None:
    assert _extract_story_points("") is None


def test_extract_story_points_non_numeric_parens_returns_none() -> None:
    assert _extract_story_points("Fix thing (maybe)") is None


# ---------------------------------------------------------------------------
# select_sprint_task_group — experimental task-group feature
# ---------------------------------------------------------------------------


def _plan_with(titles: list[str]) -> Plan:
    return Plan(completed=[], pending=[PendingTask(title=t, description=f"desc for {t}") for t in titles])


def test_select_sprint_task_group_target_zero_returns_single_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """When TARGET=0 (default), grouping is disabled — always return exactly one task."""
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_TARGET", 0)
    plan = _plan_with(["Task A (2)", "Task B (3)", "Task C (2)"])
    group = select_sprint_task_group(plan)
    assert len(group) == 1
    assert group[0]["title"] == "Task A (2)"


def test_select_sprint_task_group_empty_plan_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_TARGET", 5)
    assert select_sprint_task_group(Plan(completed=[], pending=[])) == []


def test_select_sprint_task_group_bundles_until_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three (2) tasks with TARGET=5 → bundle first three (sum 6) then stop."""
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_TARGET", 5)
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_MAX", 20)
    plan = _plan_with(["A (2)", "B (2)", "C (2)", "D (2)"])
    group = select_sprint_task_group(plan)
    assert [t["title"] for t in group] == ["A (2)", "B (2)", "C (2)"]


def test_select_sprint_task_group_first_task_over_target_passes_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A (8) with TARGET=5 stands alone — never bundled with smaller followers."""
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_TARGET", 5)
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_MAX", 20)
    plan = _plan_with(["Big (8)", "Small (2)"])
    group = select_sprint_task_group(plan)
    assert [t["title"] for t in group] == ["Big (8)"]


def test_select_sprint_task_group_respects_max(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bundling stops when adding the next task would exceed MAX."""
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_TARGET", 10)
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_MAX", 6)
    plan = _plan_with(["A (3)", "B (3)", "C (3)"])
    group = select_sprint_task_group(plan)
    # A+B = 6 fits MAX; A+B+C = 9 exceeds MAX so we stop at two.
    assert [t["title"] for t in group] == ["A (3)", "B (3)"]


def test_select_sprint_task_group_untagged_task_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Top task without an SP tag is never grouped — taken alone."""
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_TARGET", 5)
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_MAX", 20)
    plan = _plan_with(["Untagged task", "B (3)", "C (2)"])
    group = select_sprint_task_group(plan)
    assert [t["title"] for t in group] == ["Untagged task"]


def test_select_sprint_task_group_stops_at_untagged_mid_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bundling halts when an untagged task appears — earlier tagged picks are kept."""
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_TARGET", 10)
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_MAX", 20)
    plan = _plan_with(["A (2)", "Untagged", "C (3)"])
    group = select_sprint_task_group(plan)
    assert [t["title"] for t in group] == ["A (2)"]


# ---------------------------------------------------------------------------
# task_count_cap respected by select_sprint_task_group
# ---------------------------------------------------------------------------


def test_select_sprint_task_group_respects_count_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """With count_cap=2, bundler stops at two tasks even if SP target not yet met."""
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_TARGET", 10)
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_MAX", 20)
    plan = _plan_with(["A (2)", "B (2)", "C (2)", "D (2)"])
    group = select_sprint_task_group(plan, task_count_cap=2)
    assert [t["title"] for t in group] == ["A (2)", "B (2)"]


def test_select_sprint_task_group_count_cap_of_one_gives_single_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """When cap has shrunk to 1 after repeated reverts, the bundler returns one task even when more would fit SP-wise."""
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_TARGET", 10)
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_MAX", 20)
    plan = _plan_with(["A (2)", "B (2)", "C (2)"])
    group = select_sprint_task_group(plan, task_count_cap=1)
    assert [t["title"] for t in group] == ["A (2)"]


def test_select_sprint_task_group_big_task_ignores_count_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule (b): a single task whose SP ≥ TARGET ships alone regardless of count cap — cap=1 or cap=3 doesn't matter, the big task stands on its own."""
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_TARGET", 5)
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_MAX", 20)
    plan = _plan_with(["Big (8)", "Small (2)"])
    group = select_sprint_task_group(plan, task_count_cap=1)
    assert [t["title"] for t in group] == ["Big (8)"]


def test_select_sprint_task_group_count_cap_floor_at_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """Passing cap=0 (or negative) clamps to 1 so the bundler never returns an empty group for a non-empty plan."""
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_TARGET", 10)
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_MAX", 20)
    plan = _plan_with(["A (2)", "B (2)"])
    assert len(select_sprint_task_group(plan, task_count_cap=0)) == 1
    assert len(select_sprint_task_group(plan, task_count_cap=-5)) == 1


# ---------------------------------------------------------------------------
# RevertReason classifier
# ---------------------------------------------------------------------------


def test_revert_reason_shrinks_cap_only_on_real_failures() -> None:
    assert _revert_reason_shrinks_cap(RevertReason.TEST_FAILURE) is True
    assert _revert_reason_shrinks_cap(RevertReason.IMPLEMENT_REFUSED) is True
    assert _revert_reason_shrinks_cap(RevertReason.IMPLEMENT_MALFORMED) is False
    assert _revert_reason_shrinks_cap(RevertReason.IMPLEMENT_FAILED) is False
    assert _revert_reason_shrinks_cap(RevertReason.OTHER) is False


# ---------------------------------------------------------------------------
# post-revert planner hint builder
# ---------------------------------------------------------------------------


def test_post_revert_hint_empty_when_no_records() -> None:
    assert _build_post_revert_hint([]) == ""


def test_post_revert_hint_includes_real_failures_and_action_suggestions() -> None:
    records = [
        SprintRevertRecord(sprint_number=3, task_titles=["Task A (2)", "Task B (3)"], reason=RevertReason.TEST_FAILURE, reason_message="tests failed at assertion X"),
        SprintRevertRecord(sprint_number=4, task_titles=["Task A (2)"], reason=RevertReason.TEST_FAILURE, reason_message="still failing at Y"),
    ]
    hint = _build_post_revert_hint(records)
    assert "recent reverts since last replan" in hint.lower()
    assert "Sprint 3" in hint
    assert "Sprint 4" in hint
    assert "Task A (2)" in hint
    assert "test_failure" in hint.lower()
    assert "split" in hint.lower()
    assert "deprioritise" in hint.lower() or "deprioritize" in hint.lower()


def test_post_revert_hint_parser_only_window_gets_soft_note() -> None:
    """When the only reverts in the window are parser-format hiccups, the planner gets a SHORT note explaining it's an autosprint bug, not a task problem — the task-splitting / deprioritise framing is suppressed."""
    records = [
        SprintRevertRecord(sprint_number=3, task_titles=["Task A (2)"], reason=RevertReason.IMPLEMENT_MALFORMED, reason_message="missing ---END---"),
    ]
    hint = _build_post_revert_hint(records)
    assert "parser hiccup" in hint.lower()
    # The "split/deprioritise/reword" framing is absent — nothing to act on.
    assert "Consider when choosing" not in hint


def test_post_revert_hint_mixes_real_and_parser_with_real_taking_priority() -> None:
    """If the window has BOTH real + parser reverts, the hint shows the real ones (which drive action) and ignores the parser ones (they don't mean a task needs splitting)."""
    records = [
        SprintRevertRecord(sprint_number=3, task_titles=["Parser-ghost (1)"], reason=RevertReason.IMPLEMENT_MALFORMED, reason_message="missing ---END---"),
        SprintRevertRecord(sprint_number=4, task_titles=["Real-issue (3)"], reason=RevertReason.TEST_FAILURE, reason_message="pytest fail"),
    ]
    hint = _build_post_revert_hint(records)
    assert "Real-issue" in hint
    assert "Parser-ghost" not in hint  # filtered out
    assert "split" in hint.lower()  # action framing present


# ---------------------------------------------------------------------------
# config warning when MAX_CONSECUTIVE_FAILURES <= REPLAN_EVERY_N_SPRINTS
# ---------------------------------------------------------------------------


def test_config_warning_fires_when_failure_cap_defeats_replan(capsys: pytest.CaptureFixture) -> None:
    """In auto-replan mode, Config with MAX_CONSECUTIVE_FAILURES <= REPLAN_EVERY_N_SPRINTS must write a warning to stderr (not raise)."""
    Config(AUTO_REPLAN=True, MAX_CONSECUTIVE_FAILURES=2, REPLAN_EVERY_N_SPRINTS=3)  # must not raise
    captured = capsys.readouterr()
    assert "MAX_CONSECUTIVE_FAILURES" in captured.err
    assert "REPLAN_EVERY_N_SPRINTS" in captured.err


def test_config_no_warning_when_relationship_is_sound(capsys: pytest.CaptureFixture) -> None:
    Config(AUTO_REPLAN=True, MAX_CONSECUTIVE_FAILURES=5, REPLAN_EVERY_N_SPRINTS=2)
    captured = capsys.readouterr()
    assert "MAX_CONSECUTIVE_FAILURES" not in captured.err


def test_config_no_warning_in_reviewed_plan_mode(capsys: pytest.CaptureFixture) -> None:
    """Reviewed-plan mode (AUTO_REPLAN=False, the default) has no periodic replan, so the escape-valve warning must stay silent even when the cap relationship would otherwise trigger it."""
    Config(MAX_CONSECUTIVE_FAILURES=2, REPLAN_EVERY_N_SPRINTS=3)
    captured = capsys.readouterr()
    assert "MAX_CONSECUTIVE_FAILURES" not in captured.err
