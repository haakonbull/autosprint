"""Tests for the plan_phase phase and should_replan logic. (Plan phase = the P in PIT loop)

LLM-mocked unit tests; no real API calls.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import autosprint.plan_phase as plan_phase_mod
from autosprint.agents import AGENT_QUICK_A_GPT41_COPILOT
from autosprint.config import config
from autosprint.errors import PhaseFailedError
from autosprint.plan_phase import should_replan, update_plan
from autosprint.plan import PendingTask, Plan

VALID_PLAN_RESPONSE = '---RESULT---\n{"pending": [{"title": "Do thing", "description": "Do the first thing."}, {"title": "Do other", "description": "Do the second thing."}]}\n---END---'
PLAN_RESPONSE_WITH_SUMMARY = '---RESULT---\n{"pending": [{"title": "Do thing", "description": "Do the first thing."}], "plan_summary": "Merged 8 proposals into 1 task."}\n---END---'


def test_plan_depth_section_empty_in_loop_mode() -> None:
    """Loop mode (plan_only_mode=False) gets no depth section — the plan-team.md 5–10 default stands."""
    assert plan_phase_mod.plan_depth_section(False) == ""


def test_plan_depth_section_present_in_plan_only_mode() -> None:
    """plan-only mode injects guidance for a fuller 15–30 candidate list, ordered by dependency, for human curation."""
    section = plan_phase_mod.plan_depth_section(True)
    assert "15" in section and "30" in section
    assert "plan-only" in section.lower()
    assert "depend" in section.lower()


async def test_update_plan_uses_fake_plan_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """FAKE_PLAN_TITLE set writes a hardcoded plan with that task, no LLM call."""
    monkeypatch.setattr(config, "FAKE_PLAN_TITLE", "Add hello to hello.md")
    monkeypatch.setattr(config, "FAKE_PLAN_DESC", "Append the word 'hello' to the file hello.md.")
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    plan = await update_plan([AGENT_QUICK_A_GPT41_COPILOT], AGENT_QUICK_A_GPT41_COPILOT)
    assert len(plan.pending) == 1
    assert plan.pending[0].title == "Add hello to hello.md"
    assert (tmp_path / "autosprint" / "plan.md").exists()


async def test_update_plan_single_agent_writes_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "LOG_LEVEL", 10)
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(plan_phase_mod, "query_agent", AsyncMock(return_value=VALID_PLAN_RESPONSE))
    plan = await update_plan([AGENT_QUICK_A_GPT41_COPILOT], AGENT_QUICK_A_GPT41_COPILOT)
    assert len(plan.pending) == 2
    assert plan.pending[0].title == "Do thing"
    assert (tmp_path / "autosprint" / "plan.md").exists()


async def test_update_plan_writes_plan_summary_in_plan_only_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """plan-only mode renders the lead's plan_summary as a blockquote atop plan.md."""
    monkeypatch.setattr(config, "LOG_LEVEL", 10)
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(plan_phase_mod, "query_agent", AsyncMock(return_value=PLAN_RESPONSE_WITH_SUMMARY))
    await update_plan([AGENT_QUICK_A_GPT41_COPILOT], AGENT_QUICK_A_GPT41_COPILOT, plan_only_mode=True)
    plan_text = (tmp_path / "autosprint" / "plan.md").read_text(encoding="utf-8")
    assert "> Merged 8 proposals into 1 task." in plan_text


async def test_update_plan_omits_plan_summary_in_loop_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Loop mode ignores plan_summary even when the lead returns one — loop-mode plan.md stays clean."""
    monkeypatch.setattr(config, "LOG_LEVEL", 10)
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(plan_phase_mod, "query_agent", AsyncMock(return_value=PLAN_RESPONSE_WITH_SUMMARY))
    await update_plan([AGENT_QUICK_A_GPT41_COPILOT], AGENT_QUICK_A_GPT41_COPILOT, plan_only_mode=False)
    plan_text = (tmp_path / "autosprint" / "plan.md").read_text(encoding="utf-8")
    assert "Merged 8 proposals" not in plan_text


async def test_update_plan_raises_phase_failed_on_bad_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "LOG_LEVEL", 10)
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(plan_phase_mod, "query_agent", AsyncMock(return_value="this is not json"))
    with pytest.raises(PhaseFailedError):
        await update_plan([AGENT_QUICK_A_GPT41_COPILOT], AGENT_QUICK_A_GPT41_COPILOT)


def test_should_replan_when_plan_is_empty() -> None:
    plan = Plan()
    replan, reason = should_replan(plan, sprints_since_replan=0, task_failure_counts={})
    assert replan
    assert "empty" in reason.lower()


def test_should_replan_when_n_sprints_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "REPLAN_EVERY_N_SPRINTS", 5)
    plan = Plan(pending=[PendingTask(title="x")])
    replan, reason = should_replan(plan, sprints_since_replan=5, task_failure_counts={})
    assert replan
    assert "5 sprints" in reason


def test_should_replan_when_top_task_failed_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "REPLAN_EVERY_N_SPRINTS", 100)
    plan = Plan(pending=[PendingTask(title="flaky")])
    replan, reason = should_replan(plan, sprints_since_replan=0, task_failure_counts={"flaky": 2})
    assert replan
    assert "flaky" in reason


def test_should_not_replan_when_plan_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "REPLAN_EVERY_N_SPRINTS", 100)
    plan = Plan(pending=[PendingTask(title="fresh"), PendingTask(title="next")])
    replan, _ = should_replan(plan, sprints_since_replan=1, task_failure_counts={})
    assert not replan


def test_reviewed_plan_mode_overrides_every_other_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reviewed-plan mode (AUTO_REPLAN False) pins should_replan to (False, "") — even an empty plan, a long replan gap, a failing top task, and force=True together cannot override it."""
    monkeypatch.setattr(config, "AUTO_REPLAN", False)
    replan, reason = should_replan(Plan(), sprints_since_replan=999, task_failure_counts={"x": 5}, force=True)
    assert not replan
    assert reason == ""


def test_lock_destination_section_empty_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LOCK_DESTINATION", False)
    assert plan_phase_mod.lock_destination_section() == ""


def test_lock_destination_section_returns_lock_notice_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LOCK_DESTINATION", True)
    section = plan_phase_mod.lock_destination_section()
    assert "TARGET STATE LOCKED" in section
    assert "convergence mode" in section
    assert "target-state-locked" in section


def test_plan_phase_context_carries_lock_notice_when_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "LOCK_DESTINATION", True)
    ctx = plan_phase_mod.plan_phase_context()
    assert "TARGET STATE LOCKED" in ctx


def test_plan_phase_context_omits_lock_notice_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "LOCK_DESTINATION", False)
    ctx = plan_phase_mod.plan_phase_context()
    assert "TARGET STATE LOCKED" not in ctx


def test_prioritize_section_empty_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "PRIORITIZE", "")
    assert plan_phase_mod.prioritize_section() == ""


def test_prioritize_section_empty_when_whitespace_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """A whitespace-only PRIORITIZE behaves the same as empty — no section, no noise."""
    monkeypatch.setattr(config, "PRIORITIZE", "   \n  \t  ")
    assert plan_phase_mod.prioritize_section() == ""


def test_prioritize_section_carries_user_text_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "PRIORITIZE", "add dark mode toggle to settings page")
    section = plan_phase_mod.prioritize_section()
    assert "User priority for this run" in section
    assert "add dark mode toggle to settings page" in section


def test_plan_phase_context_carries_priority_section_when_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "PRIORITIZE", "fix the login bug first")
    ctx = plan_phase_mod.plan_phase_context()
    assert "User priority for this run" in ctx
    assert "fix the login bug first" in ctx


def test_plan_phase_context_omits_priority_section_when_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "PRIORITIZE", "")
    ctx = plan_phase_mod.plan_phase_context()
    assert "User priority for this run" not in ctx
