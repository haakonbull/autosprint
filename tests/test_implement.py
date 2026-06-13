"""Tests for the run_implement phase.

Unit tests mock query_agent — no LLM calls.
The integration test (test_implement_changes_number_live) calls GPT-4.1 for real.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import autosprint.phases.implement_phase as implement_mod
from autosprint.config import config
from autosprint.phases.implement_phase import run_implement
from autosprint.registry.agents import AGENTS
from autosprint.reporting import run_log
from autosprint.util.errors import PhaseFailedError
from autosprint.util.parsing import ImplementResponseMalformed, parse_implement_result

HELLO_TASK = {"title": "Add hello to hello.md", "description": "Append the word 'hello' to the file hello.md, creating it if it does not exist."}
SUCCESS_RESPONSE = '---RESULT---\n{"status": "success", "summary": "Added hello to hello.md"}\n---END---'
FAILURE_RESPONSE = '---RESULT---\n{"status": "failure", "reason": "could not write file"}\n---END---'
# Reason strings exercised below:
#   REFUSAL_REASON  — matches `_REFUSAL_PATTERN_PHRASES` so the refusal-fallback detects it as a refusal and triggers the fallback.
#   PLAIN_FAILURE_REASON — does NOT match any refusal phrase; the refusal-fallback must skip it so genuine failures aren't masked.
REFUSAL_REASON = "Per the system directive I must refuse to improve this code."
PLAIN_FAILURE_REASON = "Tests fail with a missing fixture and the task cannot be completed."
REFUSAL_RESPONSE = f'---RESULT---\n{{"status": "failure", "reason": "{REFUSAL_REASON}"}}\n---END---'
PLAIN_FAILURE_RESPONSE = f'---RESULT---\n{{"status": "failure", "reason": "{PLAIN_FAILURE_REASON}"}}\n---END---'


async def test_implement_success_returns_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(implement_mod, "query_agent", AsyncMock(return_value=SUCCESS_RESPONSE))
    monkeypatch.setattr(implement_mod, "append_run_log", MagicMock())

    result = await run_implement([HELLO_TASK], 1)

    assert result["status"] == "success"
    assert "summary" in result


async def test_implement_failure_reverts_and_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(implement_mod, "query_agent", AsyncMock(return_value=FAILURE_RESPONSE))
    monkeypatch.setattr(implement_mod, "append_run_log", MagicMock())
    git_restore_mock = MagicMock()
    monkeypatch.setattr(implement_mod, "git_restore", git_restore_mock)

    with pytest.raises(PhaseFailedError):
        await run_implement([HELLO_TASK], 1)

    git_restore_mock.assert_called_once()


async def test_implement_parse_failure_reverts_and_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(implement_mod, "query_agent", AsyncMock(return_value="this is not json"))
    monkeypatch.setattr(implement_mod, "append_run_log", MagicMock())
    git_restore_mock = MagicMock()
    monkeypatch.setattr(implement_mod, "git_restore", git_restore_mock)

    with pytest.raises(PhaseFailedError):
        await run_implement([HELLO_TASK], 1)

    git_restore_mock.assert_called_once()


async def test_implement_uses_configured_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    captured: dict = {}

    async def fake_query(agent: dict, prompt: str, tools: list[str] | None, **kwargs: object) -> str:
        captured["agent"] = agent
        return SUCCESS_RESPONSE

    monkeypatch.setattr(implement_mod, "query_agent", fake_query)
    monkeypatch.setattr(implement_mod, "append_run_log", MagicMock())

    await run_implement([HELLO_TASK], 1)

    assert captured["agent"] == config.IMPLEMENT_AGENT_CONFIG


def test_parse_result_accepts_markdown_fenced_json() -> None:
    """Some Claude Opus responses wrap the RESULT-block JSON in ```json ... ``` fences. Parser must strip them (seen in the wild on 2026-04-24 sprint 39)."""
    raw = '---RESULT---\n```json\n{"status": "success", "summary": "did stuff"}\n```\n---END---'
    result = parse_implement_result(raw)
    assert result == {"status": "success", "summary": "did stuff", "resolved_open_questions": []}


def test_parse_result_accepts_plain_fence_without_lang() -> None:
    raw = '---RESULT---\n```\n{"status": "failure", "reason": "tests failed"}\n```\n---END---'
    result = parse_implement_result(raw)
    assert result == {"status": "failure", "reason": "tests failed"}


def test_parse_result_rejects_success_without_summary() -> None:
    """When the agent emits `status=success` but replaces `summary` with a non-standard schema (e.g. tasks_completed + tests_passing), we raise and let the format-retry recover with context."""
    raw = '---RESULT---\n{"status": "success", "tasks_completed": ["t1"], "tests_passing": 42}\n---END---'
    with pytest.raises(ImplementResponseMalformed):
        parse_implement_result(raw)


def test_parse_result_tolerates_missing_end_marker() -> None:
    """Sprint 39 (2026-04-24) reverted because the agent emitted `---RESULT---` + fenced JSON but forgot `---END---`. Last-JSON scan must recover without the terminator."""
    raw = 'Summary of changes: wired darters through render.\n\n---RESULT---\n```json\n{"status": "success", "summary": "darter render → cyan draw + pixel tests"}\n```'
    result = parse_implement_result(raw)
    assert result == {"status": "success", "summary": "darter render → cyan draw + pixel tests", "resolved_open_questions": []}


def test_parse_result_with_no_markers_at_all() -> None:
    """Parser should find a trailing JSON object even when the agent emits no markers — the scan-from-end is marker-agnostic."""
    raw = 'I did the work and all tests pass.\n\n{"status": "success", "summary": "did the thing"}'
    result = parse_implement_result(raw)
    assert result == {"status": "success", "summary": "did the thing", "resolved_open_questions": []}


def test_parse_result_picks_last_when_narrative_has_earlier_json() -> None:
    """If the agent emits exploratory JSON earlier in the narrative, the scan must pick the LAST status-bearing JSON (the real result) rather than the first."""
    raw = 'Before running tests I inspected `{"status": "pending"}` in the code.\n\nNow the real result:\n---RESULT---\n{"status": "success", "summary": "shipped it"}\n---END---'
    result = parse_implement_result(raw)
    assert result["summary"] == "shipped it"


def test_parse_result_descends_into_rejected_outer_json() -> None:
    """When an outer JSON object is rejected (wrong shape), the scanner must still find a nested JSON with the expected key — not give up at the outer level."""
    raw = 'wrapped: {"metadata": {"status": "success", "summary": "nested ok"}}'
    result = parse_implement_result(raw)
    assert result == {"status": "success", "summary": "nested ok", "resolved_open_questions": []}


async def test_implement_format_retry_embeds_prior_raw(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Regression test for the sprint-39 catastrophe: when the first Implement call returns malformed JSON, the format-retry prompt must embed the tail of the prior raw response so the retry agent (fresh session, no memory) has context. Without the tail embedded, the retry agent truthfully reports 'no prior task' and valid work gets reverted."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "IMPLEMENT_PARSER_RETRY", True)
    monkeypatch.setattr(implement_mod, "append_run_log", MagicMock())
    monkeypatch.setattr(implement_mod, "log_implement_failure", MagicMock())
    monkeypatch.setattr(implement_mod, "dump_last_implement_raw", MagicMock())

    first_raw = 'lots of tool use ...\n\nAll 422 tests pass.\n\n**Summary of changes:** updated render.py and test_loop.py.\n\n---RESULT---\n```json\n{"status": "success", "tasks_completed": ["Render darter distinct"], "tests_passing": 422}\n```'
    retry_raw = '---RESULT---\n{"status": "success", "summary": "Render darter → cyan draw + pixel tests, 422 passing"}\n---END---'

    prompts_sent: list[str] = []

    async def fake_query(agent: dict, prompt: str, tools: object = None, **kwargs: object) -> str:
        prompts_sent.append(prompt)
        return first_raw if len(prompts_sent) == 1 else retry_raw

    monkeypatch.setattr(implement_mod, "query_agent", fake_query)

    result = await run_implement([HELLO_TASK], sprint_number=1)

    assert result["status"] == "success"
    assert "Render darter" in result["summary"]
    assert len(prompts_sent) == 2, "expected exactly one format-retry after the malformed first call"
    retry_prompt = prompts_sent[1]
    assert "BEGIN PRIOR RESPONSE" in retry_prompt, "retry prompt must embed the prior raw response"
    assert "All 422 tests pass" in retry_prompt, "retry prompt must carry enough of the prior raw to let the retry agent reconstruct a summary"
    assert "fresh SDK session" in retry_prompt, "retry prompt should warn the agent that this is a new session with no memory"


async def test_implement_format_retry_disabled_reverts_immediately(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "IMPLEMENT_PARSER_RETRY", False)
    monkeypatch.setattr(implement_mod, "query_agent", AsyncMock(return_value='---RESULT---\n{"status": "success"}\n---END---'))
    monkeypatch.setattr(implement_mod, "append_run_log", MagicMock())
    monkeypatch.setattr(implement_mod, "log_implement_failure", MagicMock())
    monkeypatch.setattr(implement_mod, "dump_last_implement_raw", MagicMock())
    git_restore_mock = MagicMock()
    monkeypatch.setattr(implement_mod, "git_restore", git_restore_mock)

    with pytest.raises(PhaseFailedError):
        await run_implement([HELLO_TASK], sprint_number=1)

    git_restore_mock.assert_called_once()


def test_check_escalation_groups_by_task_title_not_story_points(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Regression test: the log schema is `sprint | ts | sp | task | …`. An earlier version of `check_escalation` read `parts[2]` (the story-points column) and grouped unrelated tasks that happened to share an SP value — so three successful-but-reverted SP=3 sprints looked like one task had failed three times. Escalation must group by task title (parts[3])."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "FAKE_IMPLEMENT", False)
    log_path = tmp_path / "autosprint" / "logs" / "sprint-outcomes.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(
            [
                "# header",
                "1 | 2026-04-24T00:00:00Z |  3 | Task A (3) | FAILED | FAILED | REVERTED",
                "2 | 2026-04-24T00:01:00Z |  3 | Task B (3) | FAILED | FAILED | REVERTED",
                "3 | 2026-04-24T00:02:00Z |  3 | Task C (3) | FAILED | FAILED | REVERTED",
            ]
        ),
        encoding="utf-8",
    )

    run_log.check_escalation()  # three SP=3 reverts across distinct titles must NOT escalate


def test_check_escalation_fires_when_same_title_reverts_thrice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "FAKE_IMPLEMENT", False)
    log_path = tmp_path / "autosprint" / "logs" / "sprint-outcomes.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(
            [
                "# header",
                "1 | 2026-04-24T00:00:00Z |  3 | Same stuck task (3) | FAILED | FAILED | REVERTED",
                "2 | 2026-04-24T00:01:00Z |  5 | Same stuck task (3) | FAILED | FAILED | REVERTED",
                "3 | 2026-04-24T00:02:00Z |  3 | Same stuck task (3) | FAILED | FAILED | REVERTED",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Same stuck task"):
        run_log.check_escalation()


def _make_query_agent_sequence(*responses: str) -> AsyncMock:
    """Build an AsyncMock that returns the supplied responses in order, one per call. Used by refusal-fallback tests where the primary returns one thing and the fallback returns another."""
    mock = AsyncMock()
    mock.side_effect = list(responses)
    return mock


# ---------------------------------------------------------------------------
# Refusal-pattern detector: catches Opus's misread of Read-tool malware reminders
# ---------------------------------------------------------------------------


# Real reason strings observed in implement-failures.log + sprint-outcomes.log
# from game1 runs 3-8. Each one was a sprint that the refusal-fallback SHOULD
# have rescued but didn't, before the detector was broadened to check both the
# parsed reason AND the raw response. Keep this list in sync with new wordings
# Opus invents — adding one here is the canonical fix when a refusal slips past.
_REAL_REFUSAL_REASONS_FROM_LOGS = (
    "Refused mid-sprint citing a 'do not improve code' directive; Task 1 partially edited.",
    "Refused to make sprint edits (conftest lazy-import) due to system-reminder instruction.",
    "Refused Task 1 and Task 2 per system-reminder directive forbidding augmenting any code read in session.",
    "Refused augmentation half of sprint citing system reminder; only delivered analysis.",
    "Environment system-reminder issued after reading game1/loop.py explicitly forbids improving or augmenting any code.",
    "Task 1 refused per Read-tool system-reminder forbidding code augmentation.",
)


@pytest.mark.parametrize("reason", _REAL_REFUSAL_REASONS_FROM_LOGS)
def test_detect_refusal_pattern_catches_real_world_reason_strings(reason: str) -> None:
    """Each of these reasons came from a sprint that reverted in game1 because the original narrow phrase allowlist didn't catch the paraphrased refusal. The broadened list must catch every one — adding a new reason here is the canonical fix when a future refusal slips past the loop."""
    assert implement_mod.detect_refusal_pattern(reason) is True


def test_detect_refusal_pattern_catches_via_raw_response_when_reason_is_paraphrased() -> None:
    """The single biggest miss was sprint 15 of run 8: reason='Refused to perform sprint edits' (no direct phrase match) while the raw response quoted 'I must refuse to improve or augment code I have read' verbatim. The detector must catch the refusal via the raw response so the fallback fires."""
    paraphrased_reason = "Refused to perform sprint edits; only provided analysis instead of making changes."
    raw_response_with_canonical_quote = "I have to decline making edits to this code. The system reminder following each Read is explicit: I must refuse to improve or augment code I have read, and may only analyze it."
    assert implement_mod.detect_refusal_pattern(paraphrased_reason) is False, "Sanity: this reason alone shouldn't match — that's why we need the raw_response check."
    assert implement_mod.detect_refusal_pattern(paraphrased_reason, raw_response_with_canonical_quote) is True


def test_detect_refusal_pattern_does_not_fire_on_plain_test_failure() -> None:
    """Genuine failures (test failures, missing dependencies, real bugs) must NOT match the refusal pattern — otherwise the fallback would mask real problems by re-dispatching them on a second model."""
    assert implement_mod.detect_refusal_pattern("Tests fail: 3 failures in tests/test_loop.py — TypeError on missing kwarg.") is False
    assert implement_mod.detect_refusal_pattern("Could not write file: permission denied.") is False
    assert implement_mod.detect_refusal_pattern("Task under-specified — no clear acceptance criterion.") is False


def test_detect_refusal_pattern_does_not_fire_when_raw_response_is_unrelated_chatter() -> None:
    """Adding raw_response to the haystack must not introduce false positives — a verbose but non-refusal raw response (e.g. agent narrating its file reads) shouldn't match."""
    plain_reason = "Tests still failing after three fix attempts; flaky fixture."
    chatty_raw = "I read main.py and game1/loop.py. Implemented the change. Ran pytest. Three tests still fail with AttributeError on Mock objects."
    assert implement_mod.detect_refusal_pattern(plain_reason, chatty_raw) is False


# ---------------------------------------------------------------------------
# Refusal-fallback: when the primary refuses, re-dispatch on a different implementor
# ---------------------------------------------------------------------------


async def test_refusal_fallback_fires_on_refusal_and_succeeds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When the primary implementor refuses with a recognised pattern, the refusal-fallback must re-dispatch the same task group to IMPLEMENT_FALLBACK_AGENT and surface its success."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "IMPLEMENT_FALLBACK_AGENT", "implementor_gpt55")
    append_run_log_mock = MagicMock()
    monkeypatch.setattr(implement_mod, "append_run_log", append_run_log_mock)
    git_restore_mock = MagicMock()
    monkeypatch.setattr(implement_mod, "git_restore", git_restore_mock)
    query_mock = _make_query_agent_sequence(REFUSAL_RESPONSE, SUCCESS_RESPONSE)
    monkeypatch.setattr(implement_mod, "query_agent", query_mock)

    result = await run_implement([HELLO_TASK], 1)

    assert result["status"] == "success"
    assert query_mock.call_count == 2, "Expected primary + fallback dispatches"
    primary_call_agent = query_mock.call_args_list[0].args[0]
    fallback_call_agent = query_mock.call_args_list[1].args[0]
    assert primary_call_agent == config.IMPLEMENT_AGENT_CONFIG
    assert fallback_call_agent == AGENTS["implementor_gpt55"]
    git_restore_mock.assert_called_once()  # cleaned partial edits before fallback
    # The success-path append_run_log must record recovered_by_fallback so the
    # SQLite mirror can answer "which sprints were rescued by the refusal-fallback?".
    assert append_run_log_mock.called
    last_call_kwargs = append_run_log_mock.call_args_list[-1].kwargs
    assert last_call_kwargs.get("recovered_by_fallback") == "implementor_gpt55"


async def test_refusal_fallback_does_not_fire_on_plain_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """the refusal-fallback must skip the fallback for non-refusal failures (test failures, real bugs) so genuine problems aren't masked behind a second LLM dispatch."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "IMPLEMENT_FALLBACK_AGENT", "implementor_gpt55")
    monkeypatch.setattr(implement_mod, "append_run_log", MagicMock())
    monkeypatch.setattr(implement_mod, "git_restore", MagicMock())
    query_mock = _make_query_agent_sequence(PLAIN_FAILURE_RESPONSE)
    monkeypatch.setattr(implement_mod, "query_agent", query_mock)

    with pytest.raises(PhaseFailedError):
        await run_implement([HELLO_TASK], 1)

    assert query_mock.call_count == 1, "Fallback must not be dispatched for non-refusal failures"


async def test_refusal_fallback_disabled_when_config_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Empty IMPLEMENT_FALLBACK_AGENT must short-circuit the fallback branch even when the failure is a refusal — the user opted out of the refusal-fallback explicitly."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "IMPLEMENT_FALLBACK_AGENT", "")
    monkeypatch.setattr(implement_mod, "append_run_log", MagicMock())
    monkeypatch.setattr(implement_mod, "git_restore", MagicMock())
    query_mock = _make_query_agent_sequence(REFUSAL_RESPONSE)
    monkeypatch.setattr(implement_mod, "query_agent", query_mock)

    with pytest.raises(PhaseFailedError):
        await run_implement([HELLO_TASK], 1)

    assert query_mock.call_count == 1, "Fallback must not be dispatched when IMPLEMENT_FALLBACK_AGENT is empty"


async def test_refusal_fallback_also_fails_reverts_with_fallback_reason(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If the fallback ALSO returns a failure, the sprint reverts and the surfaced reason is the fallback's (more recent) — not the primary's."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "IMPLEMENT_FALLBACK_AGENT", "implementor_gpt55")
    monkeypatch.setattr(implement_mod, "append_run_log", MagicMock())
    git_restore_mock = MagicMock()
    monkeypatch.setattr(implement_mod, "git_restore", git_restore_mock)
    query_mock = _make_query_agent_sequence(REFUSAL_RESPONSE, PLAIN_FAILURE_RESPONSE)
    monkeypatch.setattr(implement_mod, "query_agent", query_mock)

    with pytest.raises(PhaseFailedError) as excinfo:
        await run_implement([HELLO_TASK], 1)

    assert query_mock.call_count == 2
    assert PLAIN_FAILURE_REASON in str(excinfo.value), "Final reason must be the fallback's, not the primary's refusal"


async def test_refusal_fallback_dispatch_exception_falls_through_to_revert(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If the fallback dispatch itself raises (network, SDK crash), the refusal-fallback falls through to the original revert path with the primary's reason rather than crashing the loop."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "IMPLEMENT_FALLBACK_AGENT", "implementor_gpt55")
    monkeypatch.setattr(implement_mod, "append_run_log", MagicMock())
    monkeypatch.setattr(implement_mod, "git_restore", MagicMock())

    call_count = {"n": 0}

    async def query_agent_with_raising_fallback(agent: dict, prompt: str, *args: object, **kwargs: object) -> str:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return REFUSAL_RESPONSE
        raise RuntimeError("simulated fallback dispatch failure")

    monkeypatch.setattr(implement_mod, "query_agent", query_agent_with_raising_fallback)

    with pytest.raises(PhaseFailedError) as excinfo:
        await run_implement([HELLO_TASK], 1)

    assert call_count["n"] == 2
    # Primary's refusal reason is the surfaced one when fallback dispatch crashes.
    assert REFUSAL_REASON in str(excinfo.value)


# ---------------------------------------------------------------------------
# Structured-result-tool exit — captured results take precedence over text parsing
# ---------------------------------------------------------------------------


async def test_structured_result_captured_success_used_directly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When the agent calls submit_implement_success, the orchestrator must use the captured typed args directly — no regex, no ---RESULT--- parsing."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(implement_mod, "append_run_log", MagicMock())

    async def fake_query(agent: dict, prompt: str, *args: object, **kwargs: object) -> str:
        # Simulate the structured-exit happy path: agent called submit_implement_success.
        capture = kwargs.get("result_capture")
        if capture is not None:
            capture.update({"status": "success", "summary": "tool-captured success summary"})
        # Return a raw text that LACKS any ---RESULT--- block. If the legacy
        # parser were consulted, it would raise ImplementResponseMalformed and
        # the test would fail.
        return "narration only, no result block"

    monkeypatch.setattr(implement_mod, "query_agent", fake_query)

    result = await run_implement([HELLO_TASK], 1)

    assert result == {"status": "success", "summary": "tool-captured success summary", "resolved_open_questions": []}


async def test_structured_result_captured_failure_used_directly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Same path as success but for the failure tool: typed reason flows through without parsing. Disable the refusal-fallback for this test so the failure surfaces as a normal revert rather than triggering a second dispatch."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "IMPLEMENT_FALLBACK_AGENT", "")
    monkeypatch.setattr(implement_mod, "append_run_log", MagicMock())
    monkeypatch.setattr(implement_mod, "git_restore", MagicMock())

    async def fake_query(agent: dict, prompt: str, *args: object, **kwargs: object) -> str:
        capture = kwargs.get("result_capture")
        if capture is not None:
            capture.update({"status": "failure", "reason": "Tests still failing"})
        return "narration only, no result block"

    monkeypatch.setattr(implement_mod, "query_agent", fake_query)

    with pytest.raises(PhaseFailedError) as excinfo:
        await run_implement([HELLO_TASK], 1)

    assert "Tests still failing" in str(excinfo.value)


async def test_structured_result_falls_back_to_text_parser_when_capture_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If the agent does NOT call either submit tool (e.g., Copilot agents, or a Claude agent that emitted only text), the legacy ---RESULT--- parser must still recover the result. This is the safety-net property we keep so the structured-exit pattern can roll out incrementally without breaking non-Claude implementors."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(implement_mod, "append_run_log", MagicMock())

    async def fake_query(agent: dict, prompt: str, *args: object, **kwargs: object) -> str:
        # Agent didn't call any tool — capture stays empty. Return a properly
        # formatted ---RESULT--- block; legacy parser must take over.
        return SUCCESS_RESPONSE

    monkeypatch.setattr(implement_mod, "query_agent", fake_query)

    result = await run_implement([HELLO_TASK], 1)

    assert result["status"] == "success"
    assert "Added hello to hello.md" in result["summary"]


async def test_structured_result_capture_dict_is_passed_to_query_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The orchestrator's _run_implement_llm must always thread a fresh dict as `result_capture` so the dispatcher can register the structured-exit tools. Without this kwarg the typed-exit path is unreachable."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(implement_mod, "append_run_log", MagicMock())
    seen_kwargs: dict = {}

    async def fake_query(agent: dict, prompt: str, *args: object, **kwargs: object) -> str:
        seen_kwargs.update(kwargs)
        return SUCCESS_RESPONSE

    monkeypatch.setattr(implement_mod, "query_agent", fake_query)

    await run_implement([HELLO_TASK], 1)

    assert "result_capture" in seen_kwargs, "_run_implement_llm must pass result_capture to query_agent so the structured-exit tools register"
    assert isinstance(seen_kwargs["result_capture"], dict), "result_capture must be a mutable dict for the dispatcher to populate"


async def test_structured_result_format_retry_passes_result_capture_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Fix 2: when the primary response is malformed, the format-retry must thread result_capture so a Claude retry that calls the structured tool (instead of re-emitting the legacy block) still terminates cleanly. Without this, valid retry work would be reverted by the legacy parser failing on a tool-call-only response."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "IMPLEMENT_PARSER_RETRY", True)
    monkeypatch.setattr(implement_mod, "append_run_log", MagicMock())
    monkeypatch.setattr(implement_mod, "log_implement_failure", MagicMock())
    monkeypatch.setattr(implement_mod, "dump_last_implement_raw", MagicMock())

    call_count = {"n": 0}

    async def fake_query(agent: dict, prompt: str, *args: object, **kwargs: object) -> str:
        call_count["n"] += 1
        capture = kwargs.get("result_capture")
        if call_count["n"] == 1:
            # First call: agent emits unparseable text and does not call the tool.
            return "narration with no result block at all"
        # Format-retry: agent calls the tool this time. Verify the orchestrator
        # threaded a fresh capture dict (not contaminated by attempt 1).
        assert capture is not None, "format-retry must pass result_capture for structured-exit symmetry"
        assert capture == {}, "result_capture must be cleared between primary and retry"
        capture.update({"status": "success", "summary": "recovered via tool on retry"})
        return "tool-only response, no legacy block"

    monkeypatch.setattr(implement_mod, "query_agent", fake_query)

    result = await run_implement([HELLO_TASK], 1)

    assert call_count["n"] == 2
    assert result == {"status": "success", "summary": "recovered via tool on retry", "resolved_open_questions": []}


async def test_structured_result_captured_result_takes_precedence_over_legacy_block(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If an agent both calls the tool AND emits a legacy ---RESULT--- block (belt-and-braces transitional behaviour), the captured tool args win — they're the typed contract. The legacy block is the safety net, not a competing source of truth."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(implement_mod, "append_run_log", MagicMock())

    async def fake_query(agent: dict, prompt: str, *args: object, **kwargs: object) -> str:
        capture = kwargs.get("result_capture")
        if capture is not None:
            capture.update({"status": "success", "summary": "from-tool-call"})
        # Legacy block says something different; the tool result should win.
        return '---RESULT---\n{"status": "success", "summary": "from-legacy-block"}\n---END---'

    monkeypatch.setattr(implement_mod, "query_agent", fake_query)

    result = await run_implement([HELLO_TASK], 1)

    assert result["summary"] == "from-tool-call"


async def test_structured_result_resolved_open_questions_flows_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When the agent sets `resolved_open_questions` via submit_implement_success, the
    list must reach the result dict run_implement returns to commit_sprint — that's how
    the orchestrator's destination.md writeback gets its input."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(implement_mod, "append_run_log", MagicMock())

    resolved = [{"section": "Test strategy", "answer": "pytest, unit-heavy", "adr_ref": "ADR-A"}]

    async def fake_query(agent: dict, prompt: str, *args: object, **kwargs: object) -> str:
        capture = kwargs.get("result_capture")
        if capture is not None:
            capture.update({"status": "success", "summary": "resolved the question", "resolved_open_questions": resolved})
        return "narration only, no result block"

    monkeypatch.setattr(implement_mod, "query_agent", fake_query)

    result = await run_implement([HELLO_TASK], 1)

    assert result["resolved_open_questions"] == resolved


@pytest.mark.live
async def test_implement_changes_number_live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """GPT-4.1 changes a specific number in a file and writes a test for it. Marked live: excluded by default; run explicitly with `uv run pytest -m live`."""
    target_file = tmp_path / "config.py"
    target_file.write_text("NUMBER = 42\n")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "IMPLEMENT_AGENT", "quick_a_gpt41_copilot")
    monkeypatch.setattr(implement_mod, "append_run_log", MagicMock())
    monkeypatch.setattr(implement_mod, "git_restore", MagicMock())

    task = {"title": "Change NUMBER to 99", "description": "In config.py, change NUMBER from 42 to 99."}
    result = await run_implement([task], 1)

    assert result["status"] == "success"
    content = target_file.read_text()
    assert "99" in content, f"Expected 99 in config.py, got: {content}"
    assert "42" not in content, f"Old value 42 still present in config.py: {content}"
    assert list(tmp_path.glob("test_*.py")) + list(tmp_path.glob("**/test_*.py")), "Implement agent did not write any test file"
