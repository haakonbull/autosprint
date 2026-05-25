"""Tests for autosprint.dispatch — focused unit tests on the retry/capture plumbing.

The bulk of dispatch behaviour is exercised end-to-end through test_implement.py
(via mocked query_agent). The tests here target the narrow contracts that live
purely inside dispatch.py — most importantly the structured-exit invariants:

- `_dispatch_with_retry` clears `result_capture` between attempts so a tool
  callback that fired on a failed attempt cannot leak stale state into the next.
- `_build_copilot_result_tools` produces handlers whose closure correctly updates
  the supplied capture dict — symmetric with the Claude-side structured-exit wiring.
"""

from __future__ import annotations

import asyncio

import pytest

from autosprint.config import config
from autosprint.dispatch import _build_copilot_result_tools, _copilot_send_with_stop_check, _CopilotFailureParams, _CopilotSuccessParams, _dispatch_with_retry
from autosprint.errors import StopSignalDetected


def _invocation(tool_name: str, arguments: dict):
    """Build a minimal `copilot.tools.ToolInvocation` so unit tests can call the wrapped handlers directly without booting the full SDK. The decorator's `wrapped_handler` reads `invocation.arguments` and feeds it to the Pydantic model — that's the only field these tests exercise."""
    from copilot.tools import ToolInvocation

    return ToolInvocation(session_id="t", tool_call_id="t", tool_name=tool_name, arguments=arguments)


async def test_dispatch_retry_clears_result_capture_between_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix 1: if the first dispatch attempt populates result_capture (e.g., a tool callback fired) and then raises, the retry must see an empty capture dict — otherwise stale tool-call state from the failed attempt would survive into the retry's exit path. Cleared in place so the caller's reference remains valid; assert by inspecting the dict from the dispatcher's vantage."""
    monkeypatch.setattr(config, "LLM_RETRY_ATTEMPTS", 1)
    monkeypatch.setattr(config, "LLM_RETRY_BACKOFF_SECONDS", 0.0)
    capture: dict = {}
    seen: list[dict] = []
    attempts = {"n": 0}

    async def fake_dispatcher(agent: dict, prompt: str, preset: str, result_capture: dict | None = None) -> str:
        attempts["n"] += 1
        seen.append(dict(result_capture) if result_capture is not None else {})
        if attempts["n"] == 1:
            # Simulate a tool callback firing mid-stream, then crash.
            result_capture.update({"status": "success", "summary": "stale-from-attempt-1"})
            raise RuntimeError("simulated mid-stream failure after tool callback")
        return "ok-on-attempt-2"

    out = await _dispatch_with_retry(fake_dispatcher, {"name": "X"}, "p", "full", "label", "[T]", result_capture=capture)

    assert out == "ok-on-attempt-2"
    assert attempts["n"] == 2
    assert seen[0] == {}, "Attempt 1 starts with an empty capture"
    assert seen[1] == {}, "Attempt 2 must also see an empty capture (stale state from attempt 1 must be cleared)"


async def test_dispatch_retry_preserves_capture_when_no_retry_fires(monkeypatch: pytest.MonkeyPatch) -> None:
    """Symmetric guarantee: when the first attempt succeeds, the capture is NOT cleared by the retry plumbing — only the retry path clears it. A successful first attempt's tool-call args must reach the caller intact."""
    monkeypatch.setattr(config, "LLM_RETRY_ATTEMPTS", 1)
    capture: dict = {}

    async def fake_dispatcher(agent: dict, prompt: str, preset: str, result_capture: dict | None = None) -> str:
        if result_capture is not None:
            result_capture.update({"status": "success", "summary": "first-attempt-success"})
        return "ok"

    await _dispatch_with_retry(fake_dispatcher, {"name": "X"}, "p", "full", "label", "[T]", result_capture=capture)

    assert capture == {"status": "success", "summary": "first-attempt-success"}, "Successful-first-attempt capture must NOT be cleared"


async def test_dispatch_retry_no_op_when_capture_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plan-phase callers leave result_capture=None. The retry plumbing must not misfire (no AttributeError on `.clear()`) when there's no capture dict to manage."""
    monkeypatch.setattr(config, "LLM_RETRY_ATTEMPTS", 1)
    monkeypatch.setattr(config, "LLM_RETRY_BACKOFF_SECONDS", 0.0)

    call_count = {"n": 0}

    async def fake_dispatcher(agent: dict, prompt: str, preset: str, result_capture: dict | None = None) -> str:
        call_count["n"] += 1
        assert result_capture is None, "result_capture must be passed through as None when caller didn't supply one"
        if call_count["n"] == 1:
            raise RuntimeError("transient failure")
        return "ok-after-retry"

    out = await _dispatch_with_retry(fake_dispatcher, {"name": "X"}, "p", "full", "label", "[T]", result_capture=None)

    assert out == "ok-after-retry"
    assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# Copilot structured-exit — tool builder + handler closure
# ---------------------------------------------------------------------------


def test_build_copilot_result_tools_returns_two_tools_with_expected_names() -> None:
    """The Copilot structured-exit builder must produce exactly the success + failure pair, both named identically to their Claude-side counterparts so the implement.md prompt's tool-name guidance applies to both backends."""
    tools = _build_copilot_result_tools({})
    names = sorted(t.name for t in tools)
    assert names == ["submit_implement_failure", "submit_implement_success"]


async def test_build_copilot_b2_success_handler_updates_capture() -> None:
    """The Copilot success-tool handler must close over the capture dict and write {status: success, summary: <arg>} when invoked, mirroring the Claude side. Calls the wrapped handler directly with a ToolInvocation built locally — bypasses the SDK to keep this a pure unit test."""
    capture: dict = {}
    tools = _build_copilot_result_tools(capture)
    success = next(t for t in tools if t.name == "submit_implement_success")
    await success.handler(_invocation("submit_implement_success", {"summary": "wired Foo into Bar"}))
    # `resolved_open_questions` defaults to [] when the agent omits it (the common case).
    assert capture == {"status": "success", "summary": "wired Foo into Bar", "resolved_open_questions": []}


async def test_build_copilot_b2_failure_handler_updates_capture() -> None:
    """Symmetric to the success handler: failure-tool handler writes {status: failure, reason: <arg>} into the same capture dict."""
    capture: dict = {}
    tools = _build_copilot_result_tools(capture)
    failure = next(t for t in tools if t.name == "submit_implement_failure")
    await failure.handler(_invocation("submit_implement_failure", {"reason": "missing fixture"}))
    assert capture == {"status": "failure", "reason": "missing fixture"}


async def test_build_copilot_success_handler_captures_resolved_open_questions() -> None:
    """The Copilot success handler must thread the optional `resolved_open_questions` arg
    into the capture dict as a list of `{section, answer, adr_ref}` dicts — that's the
    Copilot-side wiring of the open-question-writeback contract."""
    capture: dict = {}
    tools = _build_copilot_result_tools(capture)
    success = next(t for t in tools if t.name == "submit_implement_success")
    await success.handler(
        _invocation(
            "submit_implement_success",
            {"summary": "resolved a question", "resolved_open_questions": [{"section": "Test strategy", "answer": "pytest", "adr_ref": "ADR-A"}]},
        )
    )
    assert capture["resolved_open_questions"] == [{"section": "Test strategy", "answer": "pytest", "adr_ref": "ADR-A"}]


async def test_build_copilot_b2_handlers_have_independent_captures_per_call() -> None:
    """Each call to `_build_copilot_result_tools` must produce a distinct closure — two separate runs of the dispatcher must NOT share state via leaked module-level mutables. Critical because parallel Copilot dispatches (e.g., plan-phase fan-out, or refusal-fallback firing while another sprint is queued) would otherwise overwrite each other's results."""
    cap_a: dict = {}
    cap_b: dict = {}
    tools_a = _build_copilot_result_tools(cap_a)
    tools_b = _build_copilot_result_tools(cap_b)

    success_a = next(t for t in tools_a if t.name == "submit_implement_success")
    success_b = next(t for t in tools_b if t.name == "submit_implement_success")
    await success_a.handler(_invocation("submit_implement_success", {"summary": "from-a"}))
    await success_b.handler(_invocation("submit_implement_success", {"summary": "from-b"}))
    assert cap_a == {"status": "success", "summary": "from-a", "resolved_open_questions": []}
    assert cap_b == {"status": "success", "summary": "from-b", "resolved_open_questions": []}


def test_build_copilot_b2_param_schema_requires_field() -> None:
    """Pydantic enforces the param schema before the handler runs — a caller that constructs the params without the required field must raise ValidationError. This is the structural guarantee that the structured-exit pattern buys us: the SDK rejects malformed tool calls before they reach our code."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _CopilotSuccessParams()  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        _CopilotFailureParams()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Handler-level validation — Pydantic-AI-style retry-on-bad-args
# ---------------------------------------------------------------------------


async def test_copilot_b2_success_handler_rejects_empty_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty-string summary passes the SDK's `str` schema but is semantically meaningless. The handler must reject it with a 'Validation error:' message and leave result_capture empty so the agent retries on the same dispatch."""
    capture: dict = {}
    tools = _build_copilot_result_tools(capture)
    success = next(t for t in tools if t.name == "submit_implement_success")
    result = await success.handler(_invocation("submit_implement_success", {"summary": ""}))
    # Tool returned an error string; capture stayed empty so the agent must retry.
    assert capture == {}
    # The string result is what the agent sees — must explain the problem.
    assert "Validation error" in str(result.content[0].text if hasattr(result, "content") else result), f"Expected validation-error message, got: {result}"


async def test_copilot_b2_success_handler_rejects_whitespace_only_summary() -> None:
    """Whitespace-only summary is the same semantic gap as empty string — must be rejected."""
    capture: dict = {}
    tools = _build_copilot_result_tools(capture)
    success = next(t for t in tools if t.name == "submit_implement_success")
    await success.handler(_invocation("submit_implement_success", {"summary": "   \n\t  "}))
    assert capture == {}, "Whitespace-only summary must NOT populate the capture"


async def test_copilot_b2_success_handler_rejects_over_length_summary() -> None:
    """Summary >200 chars exceeds the contract (commit-body limit). Must be rejected so the agent shortens it instead of getting a malformed commit message."""
    capture: dict = {}
    tools = _build_copilot_result_tools(capture)
    success = next(t for t in tools if t.name == "submit_implement_success")
    long_summary = "x" * 250
    await success.handler(_invocation("submit_implement_success", {"summary": long_summary}))
    assert capture == {}, "Over-length summary must NOT populate the capture"


async def test_copilot_b2_success_handler_strips_and_accepts_valid_summary() -> None:
    """A valid summary with surrounding whitespace must be normalised (.strip()) and stored. This is the happy path after the validation gate."""
    capture: dict = {}
    tools = _build_copilot_result_tools(capture)
    success = next(t for t in tools if t.name == "submit_implement_success")
    await success.handler(_invocation("submit_implement_success", {"summary": "  wired Foo into Bar  "}))
    assert capture == {"status": "success", "summary": "wired Foo into Bar", "resolved_open_questions": []}, "Valid summary must be stripped and stored"


async def test_copilot_b2_failure_handler_rejects_empty_reason() -> None:
    """Same gate on the failure side: empty/whitespace reason is rejected so the agent provides a concrete blocker rather than a placeholder."""
    capture: dict = {}
    tools = _build_copilot_result_tools(capture)
    failure = next(t for t in tools if t.name == "submit_implement_failure")
    await failure.handler(_invocation("submit_implement_failure", {"reason": "   "}))
    assert capture == {}, "Empty/whitespace reason must NOT populate the capture"


# ---------------------------------------------------------------------------
# _copilot_send_with_stop_check — hard outer timeout + stop-file polling
# ---------------------------------------------------------------------------


class _FakeSession:
    """Stand-in for a Copilot session whose only contract here is `send_and_wait`. The real SDK type is opaque to this helper, so duck-typing is fine. Each test instance picks a behaviour: hang forever, return immediately, or raise."""

    def __init__(self, behaviour: str, return_value: object = None) -> None:
        self.behaviour = behaviour
        self.return_value = return_value
        self.cancelled = False

    async def send_and_wait(self, prompt: str, timeout: float):  # noqa: ARG002
        if self.behaviour == "hang":
            try:
                await asyncio.sleep(timeout * 10)  # far longer than the test will wait
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            return self.return_value
        if self.behaviour == "return":
            return self.return_value
        if self.behaviour == "raise":
            raise RuntimeError("simulated SDK failure")
        raise AssertionError(f"unknown behaviour {self.behaviour!r}")


async def test_send_with_stop_check_outer_hard_timeout_fires_when_sdk_hangs(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """The reproduction of sprint 24's bug: the SDK swallows or ignores its `timeout=` arg and `send_and_wait` runs forever. The outer asyncio guard must fire at our `timeout` deadline and raise `TimeoutError` so the orchestrator can revert and continue. Without this guard the loop would hang indefinitely (observed: ~2h before manual kill)."""
    import autosprint.dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "_STOP_CHECK_POLL_INTERVAL_SECONDS", 0.05)
    session = _FakeSession("hang")
    stop_file = tmp_path / "stop-now"

    with pytest.raises(TimeoutError, match="hard timeout"):
        await _copilot_send_with_stop_check(session, "p", timeout=0.2, stop_file=stop_file)
    assert session.cancelled, "Inner SDK task must be cancelled when the outer guard fires — otherwise the work continues in the background"


async def test_send_with_stop_check_passes_larger_timeout_to_sdk(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Defence-in-depth: the SDK's own timeout is the backstop in case our cancellation doesn't propagate (buggy SDK, blocked C extension). It must be set strictly larger than our outer guard so the outer always fires first; if they were equal, races would let either fire first and surface inconsistent error types."""
    import autosprint.dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "_STOP_CHECK_POLL_INTERVAL_SECONDS", 0.05)
    seen_timeouts: list[float] = []

    class _CapturingSession:
        async def send_and_wait(self, prompt: str, timeout: float):  # noqa: ARG002
            seen_timeouts.append(timeout)
            return "ok"

    out = await _copilot_send_with_stop_check(_CapturingSession(), "p", timeout=10.0, stop_file=tmp_path / "stop-now")
    assert out == "ok"
    assert seen_timeouts == [15.0], f"SDK must receive timeout * 1.5 as the backstop; got {seen_timeouts}"


async def test_send_with_stop_check_returns_value_on_normal_completion(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Sanity: when the SDK returns within the deadline, the helper must surface its value unchanged. Guards against the new elapsed-time check accidentally raising on fast paths."""
    import autosprint.dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "_STOP_CHECK_POLL_INTERVAL_SECONDS", 0.05)
    sentinel = object()
    out = await _copilot_send_with_stop_check(_FakeSession("return", return_value=sentinel), "p", timeout=2.0, stop_file=tmp_path / "stop-now")
    assert out is sentinel


async def test_send_with_stop_check_stop_file_still_wins_over_hard_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """The stop-now signal must remain the user's authoritative kill switch and surface as `StopSignalDetected` — not get masked as a `TimeoutError` if the user happens to drop the file near the deadline. Stop-file is checked before the elapsed-time check inside the loop, so this ordering is preserved."""
    import autosprint.dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "_STOP_CHECK_POLL_INTERVAL_SECONDS", 0.05)
    stop_file = tmp_path / "stop-now"
    stop_file.touch()
    session = _FakeSession("hang")

    with pytest.raises(StopSignalDetected):
        await _copilot_send_with_stop_check(session, "p", timeout=2.0, stop_file=stop_file)
    assert not stop_file.exists(), "Stop-now file must be consumed (unlinked) when detected"
    assert session.cancelled, "Inner SDK task must be cancelled on stop-now too"
