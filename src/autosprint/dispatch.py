"""Dispatch prompts to agents across different AI assistants.

Architecture note — the structured-result-tool exit pattern
============================================================
The Implement phase needs typed structured output from the agent (status +
summary or reason). We get this by registering a pair of tools the agent
calls to terminate — ``submit_implement_success(summary)`` and
``submit_implement_failure(reason)``. The tool handlers close over a
caller-supplied ``result_capture: dict``; the agent's typed args land in that
dict, and the orchestrator reads it after the SDK call returns.

This is **the same pattern Pydantic AI implements as ``output_type``** — per
their docs: "the output JSON schema of each output type is provided to the
model as the parameters schema of a special output tool." We hand-rolled the
pattern (instead of adopting Pydantic AI as a dependency) for two reasons:

1. **Multi-provider symmetry.** Pydantic AI doesn't support GitHub Copilot
   CLI as a provider. Autosprint's design principle is low-friction backend
   swap (Claude ↔ Copilot ↔ future), so we wire each backend's native
   custom-tool API directly: ``claude_agent_sdk.tool`` +
   ``create_sdk_mcp_server`` on the Claude side, ``copilot.define_tool`` +
   ``CopilotClient.create_session(tools=...)`` on the Copilot side. The
   dict-capture closure is the unifying abstraction both sides hand off to.

2. **Bespoke orchestration.** Autosprint needs the refusal-fallback,
   per-team escalation tracking, schema migration, and other custom flows
   that Pydantic AI doesn't model. Wrapping it would be net-zero work.

Tradeoff: more glue code than ``Agent(output_type=Model)`` would require, but
backend-flexible and orchestrator-bespoke. The legacy ``---RESULT---`` text
parser stays as a safety net for cases where the agent fails to call the
structured tool — analogous to Pydantic AI's auto-retry on validation
failure.

Handler-level validation (added on top of the SDK's schema enforcement)
catches semantic gaps the schema doesn't — e.g., empty-string summary passes
``{"summary": str}`` but is meaningless. Invalid args → tool returns
``is_error=True`` (Claude) / non-fatal error string (Copilot), agent sees the
error and retries within the same dispatch. ``result_capture`` stays empty
until the agent supplies a valid call.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from autosprint.agents import TOOLS_FULL, TOOLS_READ_ONLY, TOOLS_RESEARCH, VALID_PRESETS
from autosprint.config import config
from autosprint.errors import StopSignalDetected, add_context
from autosprint.output import printlev


@dataclass
class AgentResult:
    name: str
    assistant: str
    model: str
    status: str  # "success" or "failed"
    output: str
    duration_ms: int
    error: str = ""


@dataclass
class AgentResults:
    results: list[AgentResult] = field(default_factory=list)

    def to_proposals_text(self) -> str:
        lines = []
        for r in self.results:
            lines.append(f"### {r.name} [{r.assistant}/{r.model}] ({r.status}, {r.duration_ms}ms)")
            lines.append(r.output if r.status == "success" else f"Error: {r.error}")
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Preset → native tool mapping per SDK
# ---------------------------------------------------------------------------

_CLAUDE_TOOLS: dict[str, list[str]] = {
    TOOLS_READ_ONLY: ["Read", "Glob", "Grep"],
    # Full preset includes web access so the unified implementor can fetch external sources on research tasks (and reach API docs on code tasks); a code task that doesn't need web simply doesn't use it.
    TOOLS_FULL: ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebFetch", "WebSearch"],
    # Research preset: read + write + web, no Bash. Used by Plan-phase research agents (Web Researcher) that need web access during planning even though Plan-phase normally clips to TOOLS_READ_ONLY — see `_effective_preset`.
    TOOLS_RESEARCH: ["Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch"],
}

_COPILOT_READ_ONLY_TOOLS: set[str] = {"read_file", "view", "glob", "grep"}

_CACHE_DIRNAME = "autosprint/cache"

_CACHE_EVICTED_THIS_RUN: bool = False


def _agent_label(agent: dict) -> str:
    return f"{agent.get('name', 'unknown')} [{agent.get('assistant', '?')}/{agent.get('model', 'default')}]"


def _evict_cache_if_over_cap() -> None:
    """Keep autosprint/cache/ under config.CACHE_MAX_ENTRIES files. Evicts oldest-mtime entries. Runs once per process. No-op when CACHE_MAX_ENTRIES is 0."""
    global _CACHE_EVICTED_THIS_RUN
    if _CACHE_EVICTED_THIS_RUN:
        return
    _CACHE_EVICTED_THIS_RUN = True
    cap = config.CACHE_MAX_ENTRIES
    if cap <= 0:
        return
    try:
        cache_dir = config.TARGET_REPO_PATH / _CACHE_DIRNAME
        if not cache_dir.exists():
            return
        entries = sorted(cache_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime)
        excess = len(entries) - cap
        if excess <= 0:
            return
        for entry in entries[:excess]:
            entry.unlink(missing_ok=True)
    except Exception:
        pass  # cache housekeeping must never break a live run


def _effective_preset(agent: dict, override: str | None) -> str:
    """Pick the more restrictive of the agent's declared preset and the caller's override. Research agents are a special case: their declared TOOLS_RESEARCH preset is preserved even when a caller overrides to TOOLS_READ_ONLY, because the Plan phase's read-only contract is enforced by the prompt protocol (agents are asked to *propose tasks*, not execute writes), and stripping web access from a Web Researcher member would neuter its role for no security gain."""
    declared = agent.get("tools", TOOLS_FULL)
    if override is None:
        return declared
    if declared == TOOLS_RESEARCH:
        return TOOLS_RESEARCH
    if TOOLS_READ_ONLY in (declared, override):
        return TOOLS_READ_ONLY
    return TOOLS_FULL


# ---------------------------------------------------------------------------
# Result-tool validators — shared by both backends
#
# Both Claude and Copilot handlers need the same semantic validation on top of
# the SDK's schema enforcement: an empty-string ``summary`` passes ``str`` but
# is meaningless, and an over-length ``summary`` blows past the commit-body
# contract. Centralising the validators keeps the message wording identical
# across backends so the agent sees the same error text regardless of which
# SDK ran the call. On invalid args the tool returns the error string and
# leaves ``result_capture`` empty, prompting the agent to retry mid-stream.
# ---------------------------------------------------------------------------

_MAX_SUMMARY_CHARS: int = 200  # hard cap; prompt asks for ≤120, this is the slack we accept

_ERR_EMPTY_SUMMARY: str = "Validation error: `summary` is required and must be a non-empty, non-whitespace string. Call submit_implement_success again with a concrete summary, or call submit_implement_failure if the implementation didn't succeed."
_ERR_EMPTY_REASON: str = "Validation error: `reason` is required and must be a non-empty, non-whitespace string. Call submit_implement_failure again with a one-sentence reason naming the concrete blocker."


def _validate_summary(raw: object) -> tuple[str | None, str | None]:
    """Returns ``(cleaned_summary, error_message)`` exactly one of which is non-None. ``cleaned_summary`` is the stripped value when valid; ``error_message`` is the agent-facing text the tool returns when the SDK-validated string fails our semantic gate (empty / whitespace / over-length). Single source of truth used by both backends."""
    cleaned = str(raw or "").strip()
    if not cleaned:
        return None, _ERR_EMPTY_SUMMARY
    if len(cleaned) > _MAX_SUMMARY_CHARS:
        return None, f"Validation error: `summary` is {len(cleaned)} characters; the contract limits it to {_MAX_SUMMARY_CHARS} (target ≤120 for the commit body). Call submit_implement_success again with a shorter summary that captures the change in one line."
    return cleaned, None


def _validate_reason(raw: object) -> tuple[str | None, str | None]:
    """Returns ``(cleaned_reason, error_message)`` symmetric to ``_validate_summary``. Reason has no length cap because it's used in a log line + revert reason, not a commit body — verbose blockers are still useful diagnostically."""
    cleaned = str(raw or "").strip()
    if not cleaned:
        return None, _ERR_EMPTY_REASON
    return cleaned, None


def _normalise_resolved_open_questions(raw: object) -> list[dict]:
    """Normalise the optional ``resolved_open_questions`` arg of ``submit_implement_success`` into a clean ``list[dict]``. The agent names which destination.md open questions the sprint resolved; the orchestrator (not the agent) appends the status marker + receipt. Absent / None / non-list all collapse to ``[]`` — the common case, since most sprints resolve nothing. Non-dict entries are dropped; ``section`` / ``answer`` / ``adr_ref`` are coerced to stripped strings. Shared by both backends so the agent sees identical handling regardless of which SDK ran the call."""
    if not isinstance(raw, list):
        return []
    cleaned: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        cleaned.append(
            {
                "section": str(entry.get("section") or "").strip(),
                "answer": str(entry.get("answer") or "").strip(),
                "adr_ref": str(entry.get("adr_ref") or "").strip(),
            }
        )
    return cleaned


# Tool descriptions the SDK exposes to the agent. Module-level so they're easy
# to find and tweak in one place when the prompt-engineering of the descriptions
# matters more than the surrounding plumbing.
_SUCCESS_TOOL_DESC: str = 'Call exactly once at the end of your work when the implementation succeeded AND `pytest -m "not slow"` is green. Provide a single ≤120-char `summary` formatted "<what was asked> → <what you did>". If the sprint resolved an open question in destination.md, also set `resolved_open_questions` — a list of objects, each with `section` (the exact destination.md ## heading), `answer` (one line), and `adr_ref` (the ADR title or date); leave it empty/omitted otherwise. Do NOT also emit a ---RESULT--- block; this tool is the structured replacement.'
_FAILURE_TOOL_DESC: str = "Call exactly once at the end of your work when the task cannot be completed (tests still fail, blocker, missing dependency, refusal). Provide a single `reason` sentence naming the concrete blocker. Do NOT also emit a ---RESULT--- block; this tool is the structured replacement."


def _build_claude_result_tools(result_capture: dict) -> tuple[object, list[str]]:
    """Build the Claude-side structured-exit MCP server and the fully-qualified tool names to add to ``allowed_tools``. Returns ``(server, tool_names)``. The handlers close over ``result_capture`` and use the shared validators so the wording stays identical to the Copilot side. Each call produces fresh closures so parallel dispatches can't collide."""
    from claude_agent_sdk import tool, create_sdk_mcp_server

    @tool("submit_implement_success", _SUCCESS_TOOL_DESC, {"summary": str, "resolved_open_questions": list})
    async def submit_implement_success(args: dict) -> dict:
        cleaned, err = _validate_summary(args.get("summary"))
        if err is not None:
            return {"content": [{"type": "text", "text": err}], "is_error": True}
        result_capture.update({"status": "success", "summary": cleaned, "resolved_open_questions": _normalise_resolved_open_questions(args.get("resolved_open_questions"))})
        return {"content": [{"type": "text", "text": "Success captured."}]}

    @tool("submit_implement_failure", _FAILURE_TOOL_DESC, {"reason": str})
    async def submit_implement_failure(args: dict) -> dict:
        cleaned, err = _validate_reason(args.get("reason"))
        if err is not None:
            return {"content": [{"type": "text", "text": err}], "is_error": True}
        result_capture.update({"status": "failure", "reason": cleaned})
        return {"content": [{"type": "text", "text": "Failure captured."}]}

    server = create_sdk_mcp_server("autosprint", "0.1.0", tools=[submit_implement_success, submit_implement_failure])
    tool_names = [
        "mcp__autosprint__submit_implement_success",
        "mcp__autosprint__submit_implement_failure",
    ]
    return server, tool_names


# ---------------------------------------------------------------------------
# Assistant dispatchers
# ---------------------------------------------------------------------------


async def _run_claude(agent: dict, prompt: str, preset: str, result_capture: dict | None = None) -> str:
    """Dispatch a prompt to Claude via the Anthropic Agent SDK and return the concatenated narration text. When ``result_capture`` is a dict (used by the Implement phase for the structured-result-tool exit pattern), an in-process MCP server exposing ``submit_implement_success`` and ``submit_implement_failure`` is registered, and the agent's typed args from whichever tool it calls are written into the dict. Plan-phase callers leave ``result_capture=None`` so no extra tools are registered and behaviour is unchanged."""
    from claude_agent_sdk import ClaudeAgentOptions, AssistantMessage, query

    try:
        allowed_tools = list(_CLAUDE_TOOLS[preset])  # copy so per-call extension doesn't mutate the preset
        opts_kwargs: dict = {
            "allowed_tools": allowed_tools,
            "model": agent.get("model"),
            "cwd": config.TARGET_REPO_PATH,
        }

        if result_capture is not None:
            server, tool_names = _build_claude_result_tools(result_capture)
            opts_kwargs["mcp_servers"] = {"autosprint": server}
            allowed_tools.extend(tool_names)

        result_text = ""
        async for message in query(prompt=prompt, options=ClaudeAgentOptions(**opts_kwargs)):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text"):
                        result_text += block.text
        return result_text
    except Exception as e:
        raise add_context(e, f"Failed to run Claude query with model {agent.get('model')}") from e


async def _copilot_read_only_hook(input_data: dict, invocation: object) -> dict:
    tool_name = input_data.get("toolName", "")
    if tool_name in _COPILOT_READ_ONLY_TOOLS:
        return {"permissionDecision": "allow"}
    return {"permissionDecision": "deny", "permissionDecisionReason": f'Read-only preset: "{tool_name}" is not allowed.'}


class _ResolvedOpenQuestion(BaseModel):
    """One destination.md open question a sprint resolved. The agent names the question; autosprint (not the agent) appends the status marker + receipt back to destination.md deterministically. Module-level so ``copilot.define_tool``'s ``get_type_hints`` can resolve it as the element type of ``_CopilotSuccessParams.resolved_open_questions``."""

    section: str = Field(description="The exact destination.md ## section heading the resolved question lived in (heading text, with or without leading '## ').")
    answer: str = Field(description="A one-line answer to the resolved open question.")
    adr_ref: str = Field(description="The ADR title or date that records the rationale (matches an entry in adr.md).")


class _CopilotSuccessParams(BaseModel):
    """Pydantic schema for the Copilot-side ``submit_implement_success`` tool. Module-level so ``copilot.define_tool``'s ``get_type_hints`` can resolve the forward reference — nested-in-function BaseModels raise NameError when the decorator inspects the handler signature."""

    summary: str = Field(description="≤120 chars: '<what was asked> → <what you did>'. Used as the git commit body.")
    resolved_open_questions: list[_ResolvedOpenQuestion] = Field(default_factory=list, description="Open questions in destination.md this sprint resolved — one entry per resolved section. Leave empty if the sprint resolved no open question (the common case). Autosprint appends the status marker + receipt for you.")


class _CopilotFailureParams(BaseModel):
    """Pydantic schema for the Copilot-side ``submit_implement_failure`` tool. Module-level for the same reason as ``_CopilotSuccessParams``."""

    reason: str = Field(description="One sentence naming the concrete blocker (test failure, missing dep, scope conflict).")


def _build_copilot_result_tools(result_capture: dict) -> list:
    """Build Copilot-side structured-exit tools that mirror the Claude ``submit_implement_success`` / ``submit_implement_failure`` pair. Returns a list of ``Tool`` objects suitable for passing to ``CopilotClient.create_session(tools=...)``.

    The handlers close over ``result_capture`` so the agent's typed args land in
    the same dict the orchestrator inspects after the SDK call returns — unifying
    the two backends behind the in-process result-capture pattern. Param schemas
    live at module level (``_CopilotSuccessParams`` / ``_CopilotFailureParams``)
    so the SDK's ``get_type_hints`` can resolve forward references.
    """
    from copilot import define_tool

    @define_tool(name="submit_implement_success", description='Call exactly once at the end of your work when the implementation succeeded AND `pytest -m "not slow"` is green. If the sprint resolved an open question in destination.md, set `resolved_open_questions` (one entry per resolved section); leave it empty otherwise. Do not also emit a ---RESULT--- text block; this tool is the structured replacement.')
    def submit_implement_success(params: _CopilotSuccessParams) -> str:
        cleaned, err = _validate_summary(params.summary)
        if err is not None:
            return err
        # The SDK hands `resolved_open_questions` back as a list of `_ResolvedOpenQuestion`
        # models; dump each to a plain dict so the capture payload is JSON-shaped and
        # symmetric with the Claude side, then run it through the shared normaliser.
        resolved = _normalise_resolved_open_questions([q.model_dump() for q in params.resolved_open_questions])
        result_capture.update({"status": "success", "summary": cleaned, "resolved_open_questions": resolved})
        return "Success captured."

    @define_tool(name="submit_implement_failure", description="Call exactly once at the end of your work when the task cannot be completed. Do not also emit a ---RESULT--- text block; this tool is the structured replacement.")
    def submit_implement_failure(params: _CopilotFailureParams) -> str:
        cleaned, err = _validate_reason(params.reason)
        if err is not None:
            return err
        result_capture.update({"status": "failure", "reason": cleaned})
        return "Failure captured."

    return [submit_implement_success, submit_implement_failure]


_STOP_CHECK_POLL_INTERVAL_SECONDS = 5.0


async def _copilot_send_with_stop_check(session, prompt: str, timeout: float, stop_file: Path) -> object:
    """Run ``session.send_and_wait`` as an asyncio task, polling every 5 seconds for
    (a) the stop-now control file and (b) our own elapsed-time deadline. If the file
    appears the LLM task is cancelled and ``StopSignalDetected`` is raised so
    ``pit_loop`` can exit cleanly. If the deadline expires before the SDK returns,
    the task is cancelled and ``TimeoutError`` is raised.

    Two timeouts are in play, by design:
      * **Outer (this loop, ``timeout``)** — our authoritative hard kill. Fires first.
      * **Inner (``timeout * 1.5`` passed to the SDK)** — backstop in case our
        cancellation doesn't propagate (buggy SDK, blocked C extension). The SDK's
        own timeout then forces the call to return and ``client.stop()`` (in the
        caller's ``finally``) tears down any subprocess so we don't orphan work.

    ``StopSignalDetected`` inherits from ``BaseException`` so it propagates through
    every ``except Exception`` guard in the dispatch chain unimpeded.
    """
    sdk_timeout = timeout * 1.5
    task = asyncio.create_task(session.send_and_wait(prompt, timeout=sdk_timeout))
    deadline = time.monotonic() + timeout
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=_STOP_CHECK_POLL_INTERVAL_SECONDS)
            if done:
                return task.result()  # returns value or re-raises task's exception
            if stop_file.exists():
                stop_file.unlink(missing_ok=True)
                raise StopSignalDetected("stop-now detected during LLM call")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Implement LLM call exceeded {timeout:.0f}s hard timeout (outer guard fired before SDK timeout)")
    except BaseException:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        raise


async def _run_copilot(agent: dict, prompt: str, preset: str, result_capture: dict | None = None) -> str:
    """Dispatch to Copilot via the GitHub Copilot SDK and return the assistant's coalesced response text. When ``result_capture`` is supplied, two custom tools (``submit_implement_success`` / ``submit_implement_failure``) are registered with the session so the agent's typed args land in the dict — the same structured-exit contract the Claude dispatcher uses. The legacy ``---RESULT---`` text path stays as a fallback in the orchestrator for cases where the agent answers in prose."""
    from copilot import CopilotClient
    from copilot.session import PermissionHandler

    try:
        client = CopilotClient()
        await client.start()
        try:
            create_kwargs: dict = {
                "on_permission_request": PermissionHandler.approve_all,
                "model": agent.get("model"),
                "working_directory": str(config.TARGET_REPO_PATH),
            }
            if preset == TOOLS_READ_ONLY:
                create_kwargs["hooks"] = {"on_pre_tool_use": _copilot_read_only_hook}
            if result_capture is not None:
                create_kwargs["tools"] = _build_copilot_result_tools(result_capture)
            session = await client.create_session(**create_kwargs)
            response = await _copilot_send_with_stop_check(
                session,
                prompt,
                config.LLM_SESSION_TIMEOUT_SECONDS,
                config.TARGET_REPO_PATH / "autosprint/stop-now",
            )
            return response.data.content
        finally:
            await client.stop()
    except Exception as e:
        raise add_context(e, f"Failed to run Copilot query with model {agent.get('model')}") from e


DISPATCHERS: dict[str, object] = {
    "claude": _run_claude,
    "copilot": _run_copilot,
}


async def _dispatch_with_retry(dispatcher, agent: dict, prompt: str, preset: str, label: str, prefix: str, result_capture: dict | None = None) -> str:
    """Call `dispatcher(agent, prompt, preset, result_capture=...)` with retries on transient failures. Retries config.LLM_RETRY_ATTEMPTS times with exponential backoff — initial delay = `LLM_RETRY_BACKOFF_SECONDS`, triples between attempts (e.g. defaults 5s → 15s → 45s, ~65s total tolerance for a 3-retry budget). The triple multiplier (rather than double) is tuned for overnight `--auto-replan` runs where typical network blips are 30-120s — doubling burned the retry budget too fast on real transient outages. A visible one-line warning is printed on each retry so the user sees the hiccup. `result_capture` is threaded to whichever dispatcher is selected — both `_run_claude` and `_run_copilot` register their backend's structured-exit tools when this dict is supplied; the agent's typed args land in the dict regardless of which SDK ran the call."""
    try:
        attempts = max(0, config.LLM_RETRY_ATTEMPTS)
        backoff = config.LLM_RETRY_BACKOFF_SECONDS
        last_error: Exception | None = None
        for attempt in range(attempts + 1):
            # Invariant: each retry starts with a clean capture dict so a
            # tool callback that fired on a previous (failed) attempt cannot
            # leak stale state into the next attempt's exit path. Cleared in
            # place so the caller's reference stays valid.
            if attempt > 0 and result_capture is not None:
                result_capture.clear()
            try:
                return await dispatcher(agent, prompt, preset, result_capture=result_capture)
            except Exception as e:
                last_error = e
                if attempt >= attempts:
                    raise
                printlev(f"{prefix}[retry] {label} failed ({type(e).__name__}: {e}). Retrying in {backoff:.1f}s (attempt {attempt + 2}/{attempts + 1}).", level=100)
                await asyncio.sleep(backoff)
                backoff *= 3
        assert last_error is not None  # unreachable — loop always returns or raises
        raise last_error
    except Exception as e:
        raise add_context(e, f"Failed to dispatch to {label} after {config.LLM_RETRY_ATTEMPTS} retries") from e


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


# Per-process Claude-usage accumulator. Claude is priced per-token so usage tracking
# matters; Copilot is a flat-rate subscription so its calls are deliberately NOT counted
# here (would inflate the number without reflecting cost). Character-based because the
# claude_agent_sdk doesn't return precise token counts in every response path. Token
# estimates use the rough ~4-chars-per-token heuristic; actual usage can vary ±30%
# depending on content density (code is denser than prose).
_CLAUDE_PROMPT_CHARS: int = 0
_CLAUDE_RESPONSE_CHARS: int = 0
_CLAUDE_CALLS: int = 0
_CLAUDE_CACHE_HITS: int = 0


def _record_llm_usage(assistant: str, prompt: str, response: str) -> None:
    """Increment per-process prompt+response char counts for a live Claude dispatch. No-op for non-Claude assistants (Copilot is subscription-priced so token counts don't reflect cost). Call only after an actual SDK round-trip — cache hits should call `_record_cache_hit` instead so the usage estimate reflects real Claude spend, not free replays."""
    if assistant != "claude":
        return
    global _CLAUDE_PROMPT_CHARS, _CLAUDE_RESPONSE_CHARS, _CLAUDE_CALLS
    _CLAUDE_PROMPT_CHARS += len(prompt)
    _CLAUDE_RESPONSE_CHARS += len(response)
    _CLAUDE_CALLS += 1


def _record_cache_hit(assistant: str) -> None:
    """Increment the Claude cache-hit counter. No-op for non-Claude assistants (cache hits on Copilot have no token-cost story). Cache hits cost no tokens but are worth surfacing so the user sees cache efficiency in the end-of-run summary."""
    if assistant != "claude":
        return
    global _CLAUDE_CACHE_HITS
    _CLAUDE_CACHE_HITS += 1


def get_claude_usage_estimate() -> dict:
    """Return per-process Claude-usage counters plus a rough token estimate. Keys: `prompt_chars`, `response_chars`, `total_chars`, `total_calls`, `cache_hits`, `estimated_tokens`. The token count is `total_chars // 4` — a loose heuristic, not a billing-grade measurement. Copilot calls are deliberately excluded because Copilot is subscription-priced (tokens don't track cost). Call at end-of-run to print a dashboard line or to decide whether the run's Claude spend felt reasonable."""
    total_chars = _CLAUDE_PROMPT_CHARS + _CLAUDE_RESPONSE_CHARS
    return {
        "prompt_chars": _CLAUDE_PROMPT_CHARS,
        "response_chars": _CLAUDE_RESPONSE_CHARS,
        "total_chars": total_chars,
        "total_calls": _CLAUDE_CALLS,
        "cache_hits": _CLAUDE_CACHE_HITS,
        "estimated_tokens": total_chars // 4,
    }


async def query_agent(agent: dict, prompt: str, tools: str | None = None, cache_validator=None, cache_namespace: str = "", skip_cache: bool = False, phase_tag: str = "", on_result: Callable[[str], None] | None = None, result_log_suffix: str = "", result_capture: dict | None = None) -> str:
    """Dispatch a prompt to the agent's assistant SDK.

    Args:
        tools: A preset string (``TOOLS_READ_ONLY`` or ``TOOLS_FULL``).
               If ``None``, uses the agent's declared preset. The effective
               preset is the more restrictive of the two.
        cache_validator: Only runs on cached results to decide freshness; live
               results are returned raw for the caller to validate via
               ``parse_result()`` / ``parse_implement_result()``.
        phase_tag: Optional phase prefix (e.g. "[P]" or "[I]") prepended to
               this function's log lines so they are attributable to the
               calling phase.
        on_result: Optional callback invoked with the raw result string right
               after the "Got result" line is printed. Used by callers to
               emit result-specific log details (e.g. proposed tasks from a
               plan agent) while preserving chronological log ordering.
        result_log_suffix: Optional text appended to the "Got result from …"
               log line (e.g. ". Here are the tasks it suggested:") so the
               caller can hint at the shape of the on_result preview that
               follows immediately below.
        result_capture: Optional mutable dict; when supplied, the dispatcher
               registers structured-exit tools (``submit_implement_success`` /
               ``submit_implement_failure``) and writes the agent's typed args
               into the dict instead of (or in addition to) the agent emitting
               a ``---RESULT---`` text block. Caller checks ``result_capture``
               after the call returns: if populated, use it directly and skip
               text parsing; if empty, fall back to the legacy parser path.
               Active on both backends — Claude registers via
               ``create_sdk_mcp_server``, Copilot registers via
               ``CopilotClient.create_session(tools=...)``. The dict-based
               capture pattern unifies the two: regardless of which SDK ran
               the call, the orchestrator inspects the same dict.

               **Cache interaction caveat:** when a cache hit fires (the cache
               stores raw response strings only, not tool-call args),
               ``result_capture`` is NOT populated and the caller will fall
               through to text parsing. The Implement phase always passes
               ``skip_cache=True`` so this path is unreachable today, but any
               new caller combining ``result_capture`` with a cacheable call
               must treat an empty capture as the expected outcome on a hit.
    """
    try:
        if tools is not None and tools not in VALID_PRESETS:
            raise ValueError(f"tools must be a preset ({VALID_PRESETS}), got {tools!r}")
        preset = _effective_preset(agent, tools)
        label = _agent_label(agent)
        prefix = f"{phase_tag} " if phase_tag else ""
        _evict_cache_if_over_cap()
        cache_key = hashlib.md5((cache_namespace + agent.get("name", "") + agent.get("assistant", "") + agent.get("model", "") + prompt).encode()).hexdigest()
        cache_dir = config.TARGET_REPO_PATH / _CACHE_DIRNAME
        cache_file = cache_dir / f"{cache_key}.txt"

        if not skip_cache and config.USE_CACHE and cache_file.exists():
            try:
                cached = cache_file.read_text(encoding="utf-8")
                if cached and (cache_validator is None or cache_validator(cached)):
                    printlev(f"{prefix}[cache] Using cached result for {label}")
                    _record_cache_hit(agent["assistant"])
                    if on_result is not None:
                        on_result(cached)
                    return cached
                cache_file.unlink(missing_ok=True)
            except Exception as cache_err:
                # Surface the failure reason (permission, encoding, disk-full,
                # poisoned payload) so corrupt cache entries don't look like
                # silent cache misses that waste tokens on re-dispatch.
                printlev(f"{prefix}[cache] read failed for {label}: {cache_err}; dropping entry and re-dispatching", level=50)
                try:
                    cache_file.unlink(missing_ok=True)
                except Exception:
                    pass

        dispatcher = DISPATCHERS.get(agent["assistant"])
        if not dispatcher:
            raise ValueError(f"Unknown assistant: {agent['assistant']}")
        printlev(f"{prefix}Querying {label}...")
        start = time.monotonic()
        result = await _dispatch_with_retry(dispatcher, agent, prompt, preset, label, prefix, result_capture=result_capture)
        printlev(f"{prefix}Got result from {label} ({time.monotonic() - start:.1f}s){result_log_suffix}")
        _record_llm_usage(agent["assistant"], prompt, result)
        if on_result is not None:
            on_result(result)

        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(result, encoding="utf-8")

        return result
    except Exception as e:
        raise add_context(e, f"Failed to run query for {agent.get('assistant')}/{agent.get('model')}") from e


async def _query_agent_timed(agent: dict, prompt: str, cache_namespace: str = "", phase_tag: str = "", on_result: Callable[[str], None] | None = None, result_log_suffix: str = "") -> AgentResult:
    name = agent.get("name", "unknown")
    assistant = agent["assistant"]
    model = agent.get("model", "default")
    start = time.monotonic()

    try:
        output = await query_agent(agent, prompt, cache_namespace=cache_namespace, phase_tag=phase_tag, on_result=on_result, result_log_suffix=result_log_suffix)
        duration_ms = int((time.monotonic() - start) * 1000)
        return AgentResult(name=name, assistant=assistant, model=model, status="success", output=output, duration_ms=duration_ms)
    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        return AgentResult(name=name, assistant=assistant, model=model, status="failed", output="", duration_ms=duration_ms, error=str(e))


async def query_agents(agents: list[dict], prompts: list[str], phase_tag: str = "", on_result: Callable[[str], None] | None = None, result_log_suffix: str = "") -> AgentResults:
    try:
        coros = [_query_agent_timed(agent, prompt, cache_namespace=str(i), phase_tag=phase_tag, on_result=on_result, result_log_suffix=result_log_suffix) for i, (agent, prompt) in enumerate(zip(agents, prompts))]
        results = await asyncio.gather(*coros)
        return AgentResults(results=list(results))
    except Exception as e:
        raise add_context(e, f"Failed to run {len(agents)} agents in parallel") from e
