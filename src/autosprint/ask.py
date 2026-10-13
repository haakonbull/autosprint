"""One-shot agent tasks, independent of sprint planning, caching and git operations."""

from __future__ import annotations

import asyncio
import sys
from contextlib import redirect_stdout

from autosprint.agents import AGENTS, TOOLS_FULL, TOOLS_READ_ONLY, TOOLS_RESEARCH
from autosprint.ask_report import AskOutcome, AskReport
from autosprint.config import config
from autosprint.dispatch import query_agent
from autosprint.init import _assert_target_repo_not_self

_TOOL_PRESETS = {"read-only": TOOLS_READ_ONLY, "full": TOOLS_FULL, "research": TOOLS_RESEARCH}
_EXIT_CODES = {"success": 0, "failed": 1, "blocked": 3}
_ASK_INSTRUCTIONS = """
This is an ad-hoc task, not an autosprint planning or implementation phase.
Apply your role to the caller's task only. Do not require destination.md or plan.md
unless the task needs them. Follow repository guidance and the caller's context
instructions. Do not make unrequested product or architecture decisions.
If you need a human decision, report blocked instead of waiting for interactive input.
Provide the requested answer/findings, including file/line references where useful.
Finish by calling submit_ask_report exactly once. Include status, summary,
files_changed, tests_run (commands and observed outcomes), and blockers.
A successful review means the review completed, not that the code has no bugs.
"""


def _ask_preset(agent: dict, tools: str) -> str:
    # The sprint planner's research exception must not widen a read-only ask.
    declared = agent["tools"]
    requested = _TOOL_PRESETS[tools]
    if TOOLS_READ_ONLY in (declared, requested):
        return TOOLS_READ_ONLY
    if TOOLS_RESEARCH in (declared, requested):
        return TOOLS_RESEARCH
    return TOOLS_FULL


def _report(agent_key: str, tools: str, outcome: AskOutcome, raw_response: str = "", structured: bool = False) -> AskReport:
    agent = AGENTS.get(agent_key, {})
    if agent and tools in _TOOL_PRESETS:
        preset = _ask_preset(agent, tools)
        tools = next(key for key, value in _TOOL_PRESETS.items() if value == preset)
    return AskReport(
        agent=agent_key,
        assistant=agent.get("assistant", ""),
        model=agent.get("model", ""),
        reasoning_effort=agent.get("reasoning_effort"),
        tools=tools,
        target_repo=str(config.TARGET_REPO_PATH),
        **outcome.model_dump(),
        raw_response=raw_response,
        structured=structured,
    )


async def dispatch_ask_async(agent_key: str, prompt_parts: list[str], tools: str = "read-only") -> dict[str, object]:
    """Run one task, returning a validated report; missing reports are failures."""
    if agent_key not in AGENTS:
        raise ValueError(f"Unknown ask agent '{agent_key}'. Use `autosprint agents` to list valid keys.")
    if tools not in _TOOL_PRESETS:
        raise ValueError(f"Unknown ask tools preset '{tools}'.")
    prompt = " ".join(prompt_parts).strip()
    if not prompt:
        raise ValueError("autosprint ask needs a non-empty task prompt.")
    if not config.TARGET_REPO_PATH.is_dir():
        raise ValueError(f"Target directory does not exist: {config.TARGET_REPO_PATH}")
    _assert_target_repo_not_self()

    agent = dict(AGENTS[agent_key])
    preset = _ask_preset(agent, tools)
    agent["tools"] = preset
    effective_tools = next(key for key, value in _TOOL_PRESETS.items() if value == preset)
    agent["system_prompt"] = f"{agent.get('system_prompt', '')}\n\n{_ASK_INSTRUCTIONS}"
    captured: dict = {}
    raw = await asyncio.wait_for(
        query_agent(agent, prompt + "\n\n" + _ASK_INSTRUCTIONS, tools=preset, skip_cache=True, result_capture=captured, dispatch_mode="ask"),
        timeout=config.LLM_SESSION_TIMEOUT_SECONDS,
    )
    if not captured:
        return _report(
            agent_key,
            effective_tools,
            AskOutcome(
                status="failed",
                summary="Agent did not submit a valid structured report.",
                files_changed=[],
                tests_run=[],
                blockers=["Task completion is unverified. Inspect raw_response and any working-tree changes before retrying."],
            ),
            raw_response=raw,
        ).model_dump()
    outcome = AskOutcome.model_validate(captured)
    return _report(agent_key, effective_tools, outcome, raw_response=raw, structured=True).model_dump()


def run_ask(agent_key: str, prompt_parts: list[str], tools: str = "read-only", output: str = "json") -> int:
    """Print exactly one JSON document (or a human report), returning the task exit code."""
    save_logs = config.SAVE_CONSOLE_LOG
    exit_code = None
    try:
        config.SAVE_CONSOLE_LOG = False
        # An ask runs alone in its CLI process. SDK chatter belongs on stderr,
        # never in the parent's JSON input or in the target's sprint logs.
        with redirect_stdout(sys.stderr):
            try:
                report = AskReport.model_validate(asyncio.run(dispatch_ask_async(agent_key, prompt_parts, tools)))
            except (Exception, KeyboardInterrupt) as exc:
                interrupted = isinstance(exc, KeyboardInterrupt)
                reason = "Interrupted by caller." if interrupted else str(exc) or type(exc).__name__
                report = _report(
                    agent_key,
                    tools,
                    AskOutcome(
                        status="failed",
                        summary="Agent task interrupted." if interrupted else "Agent task failed.",
                        files_changed=[],
                        tests_run=[],
                        blockers=[reason, "Changes may be partial; inspect the target before retrying."],
                    ),
                )
                exit_code = 130 if interrupted else 1
    finally:
        config.SAVE_CONSOLE_LOG = save_logs
    if output == "json":
        sys.stdout.write(report.model_dump_json(indent=2) + "\n")
    else:
        sys.stdout.write(f"{report.status.upper()}: {report.summary}\n")
        if report.raw_response:
            sys.stdout.write(report.raw_response + "\n")
        for label, items in (("Files changed", report.files_changed), ("Tests run", report.tests_run), ("Blockers", report.blockers)):
            sys.stdout.write(f"{label}: {'; '.join(items) if items else '(none reported)'}\n")
    return exit_code if exit_code is not None else _EXIT_CODES[report.status]
