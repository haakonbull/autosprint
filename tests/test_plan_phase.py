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


# ---------------------------------------------------------------------------
# Research teams + prompt-file selection — added with research_council.
# Asserts: every research team key resolves; agents declare the research
# prompt files; Web Researcher gets TOOLS_RESEARCH; the build/assemble helpers
# route to the declared prompt files when set.
# ---------------------------------------------------------------------------


def test_research_council_teams_registered() -> None:
    """All three research_council variants resolve in TEAMS and have a selector."""
    from autosprint.teams import TEAMS

    for key in ("research_council", "research_council_opus", "research_council_gpt55"):
        team = TEAMS[key]
        assert "agents" in team and len(team["agents"]) == 4, f"{key} should have 4 members"
        assert "selector" in team, f"{key} needs a selector"


def test_research_agents_declare_research_prompt_files() -> None:
    """Every research-role agent points at the research-flavored plan-agent prompt; the research-lead agents point at the research team-lead prompt."""
    from autosprint.agents import (
        AGENT_EDITOR_GPT55,
        AGENT_EDITOR_OPUS48,
        AGENT_RESEARCH_LEAD_GPT55,
        AGENT_RESEARCH_LEAD_OPUS48,
        AGENT_STEELMANNER_GPT55,
        AGENT_STEELMANNER_OPUS48,
        AGENT_SYNTHESIZER_GPT55,
        AGENT_SYNTHESIZER_OPUS48,
        AGENT_WEB_RESEARCHER_GPT55,
        AGENT_WEB_RESEARCHER_OPUS48,
    )

    members = [AGENT_WEB_RESEARCHER_OPUS48, AGENT_WEB_RESEARCHER_GPT55, AGENT_SYNTHESIZER_OPUS48, AGENT_SYNTHESIZER_GPT55, AGENT_STEELMANNER_OPUS48, AGENT_STEELMANNER_GPT55, AGENT_EDITOR_OPUS48, AGENT_EDITOR_GPT55]
    for a in members:
        assert a.get("plan_prompt_file") == ".claude/agents/plan-agent-research.md", f"{a['name']} should point at the research member prompt"
    for lead in (AGENT_RESEARCH_LEAD_OPUS48, AGENT_RESEARCH_LEAD_GPT55):
        assert lead.get("plan_lead_prompt_file") == ".claude/agents/plan-team-research.md", f"{lead['name']} should point at the research team-lead prompt"


def test_web_researcher_has_research_tools_preset() -> None:
    """Web Researcher needs web access — declares TOOLS_RESEARCH so its preset survives the Plan-phase TOOLS_READ_ONLY override."""
    from autosprint.agents import AGENT_WEB_RESEARCHER_GPT55, AGENT_WEB_RESEARCHER_OPUS48, TOOLS_RESEARCH

    assert AGENT_WEB_RESEARCHER_OPUS48["tools"] == TOOLS_RESEARCH
    assert AGENT_WEB_RESEARCHER_GPT55["tools"] == TOOLS_RESEARCH


def test_research_prompt_files_exist_on_disk() -> None:
    """The two research prompt files referenced by the agents are checked into the repo so `read_agent_file` can load them at dispatch time."""
    from autosprint.config import _project_root

    member_prompt = _project_root() / ".claude" / "agents" / "plan-agent-research.md"
    lead_prompt = _project_root() / ".claude" / "agents" / "plan-team-research.md"
    assert member_prompt.exists(), f"missing research member prompt: {member_prompt}"
    assert lead_prompt.exists(), f"missing research team-lead prompt: {lead_prompt}"


def test_build_prompt_for_plan_phase_routes_to_research_prompt_when_declared(monkeypatch: pytest.MonkeyPatch) -> None:
    """An agent that declares `plan_prompt_file` makes build_prompt_for_plan_phase load that file instead of the default plan-agent.md."""
    captured: dict[str, str] = {}

    def fake_read(rel: str) -> str:
        captured["loaded"] = rel
        return "TEMPLATE {name} {system_prompt}"

    monkeypatch.setattr(plan_phase_mod, "read_agent_file", fake_read)
    monkeypatch.setattr(plan_phase_mod, "plan_phase_context", lambda: "")

    research_agent = {"name": "Synth", "system_prompt": "be smart", "plan_prompt_file": ".claude/agents/plan-agent-research.md"}
    plan_phase_mod.build_prompt_for_plan_phase(research_agent)
    assert captured["loaded"] == ".claude/agents/plan-agent-research.md"

    code_agent = {"name": "Bug", "system_prompt": "find bugs"}
    plan_phase_mod.build_prompt_for_plan_phase(code_agent)
    assert captured["loaded"] == ".claude/agents/plan-agent.md"


def test_assemble_prompt_for_team_lead_routes_to_research_prompt_when_selector_declares_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the selector declares `plan_lead_prompt_file`, assemble_prompt_for_team_lead loads that file. When no selector or no declared file, it falls back to the default plan-team.md."""
    captured: dict[str, str] = {}

    def fake_read(rel: str) -> str:
        captured["loaded"] = rel
        return ""

    monkeypatch.setattr(plan_phase_mod, "read_agent_file", fake_read)

    # Minimal ctx — only `to_proposals_text` and the two summary fields are used.
    class _FakeTaskLists:
        def to_proposals_text(self) -> str:
            return ""

    ctx = plan_phase_mod.TeamLeadContext(proposed_task_lists=_FakeTaskLists(), preflight_summary="", last_test_output="")

    research_selector = {"name": "Research Lead", "plan_lead_prompt_file": ".claude/agents/plan-team-research.md"}
    plan_phase_mod.assemble_prompt_for_team_lead(ctx, selector=research_selector)
    assert captured["loaded"] == ".claude/agents/plan-team-research.md"

    code_selector = {"name": "Team Lead"}
    plan_phase_mod.assemble_prompt_for_team_lead(ctx, selector=code_selector)
    assert captured["loaded"] == ".claude/agents/plan-team.md"

    # selector=None path — legacy callers / fixtures that don't pass the selector still get the default code prompt.
    plan_phase_mod.assemble_prompt_for_team_lead(ctx)
    assert captured["loaded"] == ".claude/agents/plan-team.md"


def test_tools_research_preset_survives_read_only_override() -> None:
    """A TOOLS_RESEARCH agent overridden to TOOLS_READ_ONLY keeps its preset — Plan phase passes TOOLS_READ_ONLY but we don't want to neuter Web Researcher's web access."""
    from autosprint.agents import TOOLS_FULL, TOOLS_READ_ONLY, TOOLS_RESEARCH
    from autosprint.dispatch import _effective_preset

    research_agent = {"tools": TOOLS_RESEARCH}
    assert _effective_preset(research_agent, TOOLS_READ_ONLY) == TOOLS_RESEARCH
    assert _effective_preset(research_agent, None) == TOOLS_RESEARCH

    # Sanity: normal agents still get the more-restrictive preset.
    full_agent = {"tools": TOOLS_FULL}
    assert _effective_preset(full_agent, TOOLS_READ_ONLY) == TOOLS_READ_ONLY
    assert _effective_preset(full_agent, None) == TOOLS_FULL


def test_claude_research_tool_preset_includes_web_and_write() -> None:
    """Dispatch maps TOOLS_RESEARCH to a Claude allowlist that includes WebFetch/WebSearch (for fetching new sources) and Write/Edit (for landing them in `results/sources.md`), but NOT Bash (research agents don't shell out)."""
    from autosprint.agents import TOOLS_RESEARCH
    from autosprint.dispatch import _CLAUDE_TOOLS

    tools = set(_CLAUDE_TOOLS[TOOLS_RESEARCH])
    assert {"WebFetch", "WebSearch", "Read", "Write", "Edit", "Glob", "Grep"}.issubset(tools)
    assert "Bash" not in tools


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
