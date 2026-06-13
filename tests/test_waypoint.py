"""Tests for the waypoint feature — sub-destination support in the Plan phase.

A waypoint is a user-set intermediate target written to `autosprint/waypoint.md`.
When present, the Plan phase aims at it exclusively until the team lead signals
`waypoint_reached: true`, at which point the orchestrator appends a status marker
to waypoint.md and raises `WaypointReached` so the pit_loop can halt cleanly.

LLM-mocked unit tests; no real API calls.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import autosprint.phases.plan_phase as plan_phase_mod
from autosprint.config import config
from autosprint.phases.plan_phase import update_plan
from autosprint.registry.agents import AGENT_QUICK_A_GPT41_COPILOT
from autosprint.util.errors import WaypointReached

WAYPOINT_BODY = """# Waypoint — Add export-to-CSV feature

## Purpose

The reporting module can produce a CSV export of any saved view, callable from the UI and the CLI.

## Acceptance criteria

- A `View.export_csv(path)` method writes the rendered rows to disk.
- The CLI `report export <view> <path>` invokes it.
- One unit test covers a small saved view round-tripped through CSV.
"""

VALID_PLAN_RESPONSE = '---RESULT---\n{"pending": [{"title": "Add View.export_csv (3)", "description": "Implement the method per waypoint."}]}\n---END---'

WAYPOINT_REACHED_RESPONSE = '---RESULT---\n{"pending": [], "waypoint_reached": true, "waypoint_reached_rationale": "All three acceptance criteria are now satisfied: View.export_csv exists, the CLI subcommand is wired, and a round-trip test passes."}\n---END---'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_waypoint(repo: Path, body: str = WAYPOINT_BODY) -> Path:
    autosprint_dir = repo / "autosprint"
    autosprint_dir.mkdir(parents=True, exist_ok=True)
    path = autosprint_dir / "waypoint.md"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. Plan with waypoint → tasks aim at waypoint (prompt carries the section)
# ---------------------------------------------------------------------------


async def test_plan_phase_with_waypoint_prepends_waypoint_section(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When waypoint.md exists, the plan-phase context for team members and the
    team lead must include a `## Waypoint active` section so all planners see
    the same target. We capture the prompt the mock dispatcher receives and
    assert the section is present and quotes the waypoint body."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    _write_waypoint(tmp_path)

    captured: list[str] = []

    async def fake_query(agent, prompt, *args, **kwargs):
        captured.append(prompt)
        return VALID_PLAN_RESPONSE

    monkeypatch.setattr(plan_phase_mod, "query_agent", AsyncMock(side_effect=fake_query))
    await update_plan([AGENT_QUICK_A_GPT41_COPILOT], AGENT_QUICK_A_GPT41_COPILOT)

    assert captured, "expected at least one query_agent dispatch"
    prompt = captured[0]
    assert "## Waypoint active" in prompt
    assert "export-to-CSV feature" in prompt  # waypoint body is quoted into the prompt


# ---------------------------------------------------------------------------
# 2. Plan without waypoint → unchanged (regression guard)
# ---------------------------------------------------------------------------


async def test_plan_phase_without_waypoint_omits_section(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No waypoint.md → no `## Waypoint active` section in the dispatched prompt.
    Existing destination-driven planning is unchanged."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    captured: list[str] = []

    async def fake_query(agent, prompt, *args, **kwargs):
        captured.append(prompt)
        return VALID_PLAN_RESPONSE

    monkeypatch.setattr(plan_phase_mod, "query_agent", AsyncMock(side_effect=fake_query))
    await update_plan([AGENT_QUICK_A_GPT41_COPILOT], AGENT_QUICK_A_GPT41_COPILOT)

    assert captured
    assert "## Waypoint active" not in captured[0]


# ---------------------------------------------------------------------------
# 3. Waypoint reached → status marker written, WaypointReached raised
# ---------------------------------------------------------------------------


async def test_waypoint_reached_writes_status_marker_and_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Team lead sets `waypoint_reached: true` while a waypoint is active →
    orchestrator appends a status marker to waypoint.md and raises WaypointReached.
    Critical: the file is *not* deleted (no auto-archive — user reviews and decides)."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    waypoint_path = _write_waypoint(tmp_path)
    monkeypatch.setattr(plan_phase_mod, "query_agent", AsyncMock(return_value=WAYPOINT_REACHED_RESPONSE))

    with pytest.raises(WaypointReached) as exc_info:
        await update_plan([AGENT_QUICK_A_GPT41_COPILOT], AGENT_QUICK_A_GPT41_COPILOT)

    assert "All three acceptance criteria" in exc_info.value.rationale
    assert waypoint_path.exists(), "waypoint.md must NOT be auto-deleted on reached"
    body = waypoint_path.read_text(encoding="utf-8")
    assert "**Status:**" in body
    assert "reached" in body
    assert "All three acceptance criteria" in body


async def test_waypoint_reached_ignored_when_no_waypoint_active(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If the LLM hallucinates `waypoint_reached: true` while no waypoint.md exists,
    the orchestrator must ignore the flag and complete the plan normally — never
    halt on a phantom waypoint."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(plan_phase_mod, "query_agent", AsyncMock(return_value=WAYPOINT_REACHED_RESPONSE))

    plan = await update_plan([AGENT_QUICK_A_GPT41_COPILOT], AGENT_QUICK_A_GPT41_COPILOT)
    # Empty pending list because the mock response has no tasks; the important
    # assertion is that we got back a Plan instead of WaypointReached being raised.
    assert plan is not None
    assert (tmp_path / "autosprint" / "plan.md").exists()


# ---------------------------------------------------------------------------
# 4. Conflict-flagging instruction is in the team-lead prompt template
# ---------------------------------------------------------------------------


def test_team_lead_prompt_template_carries_conflict_rule() -> None:
    """The waypoint→destination/ADR conflict-surfacing rule lives in plan-team.md
    so the team lead is reminded to flag rather than silently pick a side. We
    can't make a mocked LLM actually obey it, but we can guard against the rule
    being deleted by accident — that's what this test does."""
    template = plan_phase_mod.read_agent_file(".claude/agents/plan-team.md")
    assert "Conflict surfacing" in template
    assert "Resolve conflict" in template or "conflict" in template.lower()
    # Reached-detection contract must also stay intact — the most fragile rule.
    assert "Reached-detection" in template
    assert "waypoint_reached" in template


# ---------------------------------------------------------------------------
# 5. Pause-by-rename → waypoint.md.paused is treated as absent
# ---------------------------------------------------------------------------


def test_paused_waypoint_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The documented pause gesture is to rename `waypoint.md` to
    `waypoint.md.paused`. Only the active filename is read; the paused file is
    not picked up. This lets users disable a waypoint without losing the
    content."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    autosprint_dir = tmp_path / "autosprint"
    autosprint_dir.mkdir(parents=True, exist_ok=True)
    paused_path = autosprint_dir / "waypoint.md.paused"
    paused_path.write_text(WAYPOINT_BODY, encoding="utf-8")

    assert plan_phase_mod.read_waypoint() == ""
    assert plan_phase_mod.waypoint_section() == ""
    assert plan_phase_mod.waypoint_title() == ""


def test_already_reached_waypoint_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If waypoint.md already carries a `> **Status:** reached` marker from a
    previous halt that the user hasn't cleared, the next run must treat it as
    inactive (circuit-breaker). Otherwise we'd halt on every restart until the
    user manually deletes the file."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    body = WAYPOINT_BODY + "\n\n> **Status:** reached 2026-05-04 — all criteria met.\n"
    _write_waypoint(tmp_path, body)

    assert plan_phase_mod.waypoint_section() == ""
    assert plan_phase_mod.waypoint_title() == ""
