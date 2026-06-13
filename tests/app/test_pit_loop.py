"""Tests for the pit_loop orchestration logic.

All LLM calls are mocked — these tests verify that phases are called in the
right order, replan triggers fire correctly, and commit behaviour respects
COMMIT_SUCCESSFUL_SPRINTS.
"""

import random
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import autosprint.app.orchestrator as orch
import autosprint.phases.plan_phase as plan_phase_mod
from autosprint.app.cli import resolve_max_sprints
from autosprint.config import config
from autosprint.core.plan import PendingTask, Plan, read_plan_md, write_plan_md
from autosprint.util.output import speak_tier_enabled

FAKE_TASK = {"title": "Add hello to hello.md", "description": "Append hello."}
FAKE_IMPLEMENT_RESULT = {"status": "success", "summary": "Appended hello."}


def test_resolve_max_sprints_passthrough_in_auto_replan() -> None:
    """In auto-replan mode the configured value is returned untouched."""
    assert resolve_max_sprints(reviewed_plan=False, explicitly_set=False, pending_task_count=40, configured=10) == 10


def test_resolve_max_sprints_explicit_wins() -> None:
    """An explicitly set MAX_SPRINTS is never auto-sized — even an explicit low value in reviewed-plan mode."""
    assert resolve_max_sprints(reviewed_plan=True, explicitly_set=True, pending_task_count=40, configured=5) == 5


def test_resolve_max_sprints_derives_from_plan_length() -> None:
    """Reviewed-plan mode with no explicit value → ceiling is 2× the pending task count."""
    assert resolve_max_sprints(reviewed_plan=True, explicitly_set=False, pending_task_count=28, configured=10) == 56


def test_resolve_max_sprints_floors_at_ten() -> None:
    """A short reviewed plan still gets a floor of 10 so retries aren't cut short."""
    assert resolve_max_sprints(reviewed_plan=True, explicitly_set=False, pending_task_count=2, configured=10) == 10
    assert resolve_max_sprints(reviewed_plan=True, explicitly_set=False, pending_task_count=0, configured=10) == 10


def test_speak_tier_enabled_off_silences_everything() -> None:
    """SPEAK_LEVEL=off → no tier speaks, not even run-level."""
    assert not speak_tier_enabled("run", "off")
    assert not speak_tier_enabled("all", "off")


def test_speak_tier_enabled_run_level_speaks_only_run_tier() -> None:
    """SPEAK_LEVEL=run → run-level events speak; per-sprint tiers stay silent."""
    assert speak_tier_enabled("run", "run")
    assert not speak_tier_enabled("reverts", "run")
    assert not speak_tier_enabled("sprints", "run")
    assert not speak_tier_enabled("all", "run")


def test_speak_tier_enabled_is_cumulative() -> None:
    """Each level speaks its own tier and every more-important one below it."""
    assert speak_tier_enabled("run", "sprints")
    assert speak_tier_enabled("reverts", "sprints")
    assert speak_tier_enabled("sprints", "sprints")
    assert not speak_tier_enabled("all", "sprints")
    assert all(speak_tier_enabled(t, "all") for t in ("run", "reverts", "sprints", "all"))


@pytest.fixture
def mock_phases(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> dict:
    """Patch all PIT phases and git helpers. Plan IO uses target_repo. Disables SAVE_CONSOLE_LOG so tests don't do per-print file IO. (Speech is silenced suite-wide by the conftest `_silence_speech` fixture.)"""
    monkeypatch.setattr(config, "SAVE_CONSOLE_LOG", False)

    plan_mock = AsyncMock(side_effect=lambda *a, **kw: Plan(pending=[PendingTask(title=FAKE_TASK["title"], description=FAKE_TASK["description"])]))
    implement_mock = AsyncMock(return_value=FAKE_IMPLEMENT_RESULT)
    test_mock = MagicMock()
    commit_mock = MagicMock()
    log_mock = MagicMock()

    # update_plan moved to plan_phase.py; pit_loop calls plan_phase() which
    # looks up update_plan in plan_phase's namespace, so patch there. The
    # orch alias is also patched for any call sites still resolving via orch.
    monkeypatch.setattr(plan_phase_mod, "update_plan", plan_mock)
    monkeypatch.setattr(orch, "update_plan", plan_mock)
    monkeypatch.setattr(orch, "run_implement", implement_mock)
    monkeypatch.setattr(orch, "run_test_phase", test_mock)
    monkeypatch.setattr(orch, "git_commit", commit_mock)
    monkeypatch.setattr(orch, "git", MagicMock(return_value=MagicMock(stdout="abc1234\n")))
    monkeypatch.setattr(orch, "get_commit_hash", MagicMock(return_value="abc1234"))
    monkeypatch.setattr(orch, "append_run_log", log_mock)
    monkeypatch.setattr(orch, "check_escalation", MagicMock())
    monkeypatch.setattr(orch, "speak", MagicMock())

    return {"plan": plan_mock, "implement": implement_mock, "test": test_mock, "commit": commit_mock, "log": log_mock, "tmp_path": target_repo}


async def test_pit_loop_runs_plan_when_plan_empty(monkeypatch: pytest.MonkeyPatch, mock_phases: dict) -> None:
    monkeypatch.setattr(config, "MAX_SPRINTS", 1)
    monkeypatch.setattr(config, "COMMIT_SUCCESSFUL_SPRINTS", True)

    await orch.pit_loop("test-branch")

    mock_phases["plan"].assert_called_once()
    mock_phases["implement"].assert_called_once()
    mock_phases["test"].assert_called_once()


async def test_pit_loop_fires_heartbeat_on_cadence(monkeypatch: pytest.MonkeyPatch, mock_phases: dict) -> None:
    """When HOWFAR_HEARTBEAT_EVERY_N_SPRINTS divides sprint_number, the heartbeat fires after that sprint."""
    monkeypatch.setattr(config, "MAX_SPRINTS", 2)
    monkeypatch.setattr(config, "COMMIT_SUCCESSFUL_SPRINTS", True)
    monkeypatch.setattr(config, "HOWFAR_HEARTBEAT_EVERY_N_SPRINTS", 2)
    heartbeat_mock = AsyncMock()
    monkeypatch.setattr(orch, "run_howfar_heartbeat", heartbeat_mock)

    await orch.pit_loop("test-branch")

    # Sprint 1 % 2 == 1 → no fire. Sprint 2 % 2 == 0 → fires once.
    heartbeat_mock.assert_awaited_once_with(2)


async def test_pit_loop_skips_heartbeat_when_disabled(monkeypatch: pytest.MonkeyPatch, mock_phases: dict) -> None:
    """HOWFAR_HEARTBEAT_EVERY_N_SPRINTS=0 → heartbeat never fires regardless of sprint number."""
    monkeypatch.setattr(config, "MAX_SPRINTS", 3)
    monkeypatch.setattr(config, "COMMIT_SUCCESSFUL_SPRINTS", True)
    monkeypatch.setattr(config, "HOWFAR_HEARTBEAT_EVERY_N_SPRINTS", 0)
    heartbeat_mock = AsyncMock()
    monkeypatch.setattr(orch, "run_howfar_heartbeat", heartbeat_mock)

    await orch.pit_loop("test-branch")

    heartbeat_mock.assert_not_awaited()


async def test_pit_loop_skips_plan_when_plan_has_pending(monkeypatch: pytest.MonkeyPatch, mock_phases: dict) -> None:
    """If plan.md already has pending and we're below the replan threshold, Plan phase is skipped on later sprints."""
    monkeypatch.setattr(config, "REPLAN_EVERY_N_SPRINTS", 100)
    monkeypatch.setattr(config, "COMMIT_SUCCESSFUL_SPRINTS", True)
    monkeypatch.setattr(config, "MAX_SPRINTS", 2)

    # Make plan return TWO pending tasks so that sprint 2 still has one left.
    multi_plan = Plan(pending=[PendingTask(title="task1", description="d1"), PendingTask(title="task2", description="d2")])
    mock_phases["plan"].side_effect = lambda *a, **kw: multi_plan

    await orch.pit_loop("test-branch")

    # Plan phase ran exactly once (sprint 1, forced initial replan); sprint 2 reused plan from disk
    assert mock_phases["plan"].call_count == 1
    assert mock_phases["implement"].call_count == 2
    assert mock_phases["test"].call_count == 2


async def test_pit_loop_skips_commit_when_disabled(monkeypatch: pytest.MonkeyPatch, mock_phases: dict) -> None:
    monkeypatch.setattr(config, "MAX_SPRINTS", 1)
    monkeypatch.setattr(config, "COMMIT_SUCCESSFUL_SPRINTS", False)

    await orch.pit_loop("test-branch")

    mock_phases["plan"].assert_called_once()
    mock_phases["implement"].assert_called_once()
    mock_phases["test"].assert_called_once()
    mock_phases["commit"].assert_not_called()


async def test_pit_loop_no_commit_still_logs_no_commit(monkeypatch: pytest.MonkeyPatch, mock_phases: dict) -> None:
    monkeypatch.setattr(config, "MAX_SPRINTS", 1)
    monkeypatch.setattr(config, "COMMIT_SUCCESSFUL_SPRINTS", False)

    await orch.pit_loop("test-branch")

    mock_phases["log"].assert_called_once()
    args = mock_phases["log"].call_args[0]
    assert args[-1] == "NO_COMMIT"


async def test_pit_loop_marks_top_pending_done_after_success(monkeypatch: pytest.MonkeyPatch, mock_phases: dict) -> None:
    """After a successful sprint with COMMIT=False, plan.md should have the task moved to completed."""
    monkeypatch.setattr(config, "MAX_SPRINTS", 1)
    monkeypatch.setattr(config, "COMMIT_SUCCESSFUL_SPRINTS", False)

    await orch.pit_loop("test-branch")

    final_plan = read_plan_md(mock_phases["tmp_path"])
    assert len(final_plan.completed) == 1
    assert final_plan.completed[0].title == FAKE_TASK["title"]
    assert final_plan.completed[0].summary == FAKE_IMPLEMENT_RESULT["summary"]
    assert len(final_plan.pending) == 0


async def test_pit_loop_reviewed_plan_exits_cleanly_when_plan_empty(monkeypatch: pytest.MonkeyPatch, mock_phases: dict) -> None:
    """In reviewed-plan mode with an empty plan.md, the loop exits immediately — no Plan/Implement/Test, no consecutive-failure cascade."""
    monkeypatch.setattr(config, "AUTO_REPLAN", False)
    monkeypatch.setattr(config, "MAX_SPRINTS", 5)

    await orch.pit_loop("test-branch")

    mock_phases["plan"].assert_not_called()
    mock_phases["implement"].assert_not_called()
    mock_phases["test"].assert_not_called()


async def test_pit_loop_reviewed_plan_runs_without_replanning(monkeypatch: pytest.MonkeyPatch, mock_phases: dict) -> None:
    """In reviewed-plan mode, the loop executes the tasks already in plan.md, never calls update_plan, exits cleanly once the reviewed plan is drained, and announces that as a success — not 'terminated early'."""
    monkeypatch.setattr(config, "AUTO_REPLAN", False)
    monkeypatch.setattr(config, "MAX_SPRINTS", 5)
    monkeypatch.setattr(config, "COMMIT_SUCCESSFUL_SPRINTS", False)
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_TARGET", 0)  # one task per sprint
    speak_mock = MagicMock()
    monkeypatch.setattr(orch, "speak", speak_mock)

    write_plan_md(mock_phases["tmp_path"], Plan(pending=[PendingTask(title="reviewed task A", description="a"), PendingTask(title="reviewed task B", description="b")]))

    await orch.pit_loop("test-branch")

    mock_phases["plan"].assert_not_called()  # update_plan never ran
    assert mock_phases["implement"].call_count == 2  # both reviewed tasks ran, then the loop exited
    # Draining the reviewed plan is a clean success — the end-of-run announcement must
    # say so, not "terminated early" (the misleading message before reviewed_plan_completed).
    final_announcement = speak_mock.call_args_list[-1].args[0].lower()
    assert "complete" in final_announcement
    assert "terminated early" not in final_announcement


async def test_pit_loop_writes_changelog_on_successful_commit(monkeypatch: pytest.MonkeyPatch, mock_phases: dict) -> None:
    """A successful, committed sprint appends an entry to autosprint/changelog.md — the committed record that survives a later `git rebase -i` squash."""
    monkeypatch.setattr(config, "MAX_SPRINTS", 1)
    monkeypatch.setattr(config, "COMMIT_SUCCESSFUL_SPRINTS", True)

    await orch.pit_loop("test-branch")

    changelog = mock_phases["tmp_path"] / "autosprint" / "changelog.md"
    assert changelog.exists()
    text = changelog.read_text(encoding="utf-8")
    assert "# Changelog" in text
    assert "## Sprint 1 — " in text
    assert FAKE_TASK["title"] in text


# ---------------------------------------------------------------------------
# Slow end-to-end test — real git, real pytest, fake plan + fake implement.
# Deselected from default runs via the "not slow" marker filter.
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    """Initialise a minimal git repo at `path` with one commit so HEAD exists."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "autosprint-test@example.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Autosprint Test"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("# test repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)


def _configure_fake_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, max_sprints: int, max_consecutive_failures: int) -> None:
    """Shared config setup for fake end-to-end pit_loop tests."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "FAKE_PLAN_TITLE", "Add hello to hello.md")
    monkeypatch.setattr(config, "FAKE_PLAN_DESC", "Append hello to hello.md, creating it if it does not exist.")
    monkeypatch.setattr(config, "FAKE_IMPLEMENT", True)
    monkeypatch.setattr(config, "MAX_SPRINTS", max_sprints)
    monkeypatch.setattr(config, "MAX_CONSECUTIVE_FAILURES", max_consecutive_failures)
    monkeypatch.setattr(config, "REPLAN_EVERY_N_SPRINTS", 3)
    monkeypatch.setattr(config, "COMMIT_SUCCESSFUL_SPRINTS", True)
    monkeypatch.setattr(config, "SAVE_CONSOLE_LOG", False)
    monkeypatch.setattr(config, "LOG_LEVEL", 100)


@pytest.mark.slow
async def test_pit_loop_fake_end_to_end_20_sprints(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """End-to-end smoke test of the full PIT loop with fake plan + fake implement for 20 sprints. Exercises real git, real pytest, real plan.md updates — the only mocked part is the LLM call via FAKE_PLAN/FAKE_IMPLEMENT. Marked slow: excluded from default runs with `-m "not slow"`; run explicitly with `uv run pytest -m slow`."""
    _init_git_repo(tmp_path)
    random.seed(42)
    _configure_fake_run(monkeypatch, tmp_path, max_sprints=20, max_consecutive_failures=25)

    await orch.pit_loop("test-branch")

    log = subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    pit_commits = [line for line in log.splitlines() if "[autosprint]" in line]
    assert len(pit_commits) >= 10, f"expected at least 10 autosprint commits out of 20 sprints, got {len(pit_commits)}:\n{log}"
    assert (tmp_path / "autosprint" / "fake-implement.log").exists()


@pytest.mark.slow
async def test_pit_loop_fake_escalation_triggers_on_many_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With a ~100% fake failure rate, pit_loop hits MAX_CONSECUTIVE_FAILURES and raises RuntimeError. Verifies the safety rail fires before the sprint cap."""
    _init_git_repo(tmp_path)
    _configure_fake_run(monkeypatch, tmp_path, max_sprints=30, max_consecutive_failures=3)
    # Force every fake implement to fail by raising the failure rate to 100%.
    monkeypatch.setattr(config, "FAKE_IMPLEMENT_FAILURE_RATE", 1.0)
    # FAKE_IMPLEMENT early-returns in check_escalation, so override that too —
    # we want the consecutive-failure path, not the task-repeat-revert path.

    with pytest.raises(RuntimeError, match="consecutive sprint failures"):
        await orch.pit_loop("test-branch")

    # No autosprint commits because every sprint reverted.
    log = subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    assert "[autosprint]" not in log


@pytest.mark.slow
async def test_pit_loop_fake_end_to_end_50_sprints_stability(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """50 sprints with the default 20% failure rate — stress-tests plan.md rewriting, commit-amend flow, and the revert path over a long run. Seeded for determinism; long runtime (~30s) is intentional."""
    _init_git_repo(tmp_path)
    random.seed(7)
    _configure_fake_run(monkeypatch, tmp_path, max_sprints=50, max_consecutive_failures=40)

    await orch.pit_loop("test-branch")

    log = subprocess.run(["git", "log", "--oneline"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    pit_commits = [line for line in log.splitlines() if "[autosprint]" in line]
    # With 20% failure rate over 50 sprints, ~40 should commit. Give generous
    # headroom to absorb run-to-run variance inside the seeded pattern.
    assert len(pit_commits) >= 30, f"expected at least 30 autosprint commits, got {len(pit_commits)}"
    # plan.md was rewritten many times without corruption — it should still parse.
    plan = read_plan_md(tmp_path)
    assert plan is not None
