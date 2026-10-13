"""Tool-only checks of one configured agent, independent of sprint setup."""

from __future__ import annotations

import asyncio
import json
from contextlib import aclosing, redirect_stdout
from importlib.metadata import version
import sys
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import BaseModel, Field

from autosprint.agents import AGENTS
from autosprint.ask_report import AskOutcome
from autosprint.dispatch import _build_claude_ask_report_tools, _build_copilot_ask_report_tools, _stop_copilot_client

_PROBE_TIMEOUT_SECONDS = 60.0
_PROBE_PROMPT = "This is a connectivity check, not a repository task. Do not read files, run commands, or do other work. " "Call submit_ask_report exactly once with status success, summary AUTOSPRINT_OK, " "and empty files_changed, tests_run, and blockers lists."


class AgentDoctorReport(BaseModel):
    schema_version: Literal[1] = 1
    status: Literal["success", "failed"] = "failed"
    agent: str
    assistant: str = ""
    model: str = ""
    reasoning_effort: str | None = None
    sdk_version: str | None = None
    runtime_version: str | None = None
    checks: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


def _check_receipt(captured: dict) -> None:
    receipt = AskOutcome.model_validate(captured)
    if receipt.status != "success" or receipt.summary != "AUTOSPRINT_OK" or receipt.files_changed or receipt.tests_run:
        raise RuntimeError("The selected model did not complete the structured tool-only probe.")


async def _check_copilot(report: AgentDoctorReport, directory: str) -> None:
    from copilot import CopilotClient
    from copilot.session import PermissionHandler

    client = CopilotClient(working_directory=directory)
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            await client.start()
            report.runtime_version = (await client.get_status()).version
            report.checks.append("Copilot runtime started.")
            if not (await client.get_auth_status()).isAuthenticated:
                raise RuntimeError("Copilot is not authenticated. Log in before delegating.")
            report.checks.append("Copilot authentication available.")
            model = next((model for model in await client.list_models() if model.id == report.model), None)
            if model is None:
                raise RuntimeError(f"Model {report.model!r} is not advertised by this runtime/account. Check model access and update the Copilot SDK/runtime.")
            if model.policy is not None and model.policy.state == "disabled":
                raise RuntimeError(f"Model {report.model!r} is disabled by policy.")
            report.checks.append("Selected model advertised by runtime.")
            if report.reasoning_effort is not None:
                if report.reasoning_effort not in (model.supported_reasoning_efforts or []):
                    raise RuntimeError(f"Reasoning effort {report.reasoning_effort!r} is not advertised for {report.model!r}. Update the SDK/runtime or select a supported configuration.")
                report.checks.append(f"Reasoning effort {report.reasoning_effort!r} advertised.")
            captured: dict = {}
            session = await client.create_session(
                model=report.model,
                reasoning_effort=report.reasoning_effort,
                working_directory=directory,
                available_tools=["submit_ask_report"],
                tools=_build_copilot_ask_report_tools(captured),
                on_permission_request=PermissionHandler.approve_all,
                system_message={"mode": "replace", "content": _PROBE_PROMPT},
            )
            await session.send_and_wait(_PROBE_PROMPT, timeout=_PROBE_TIMEOUT_SECONDS)
            _check_receipt(captured)
    finally:
        await _stop_copilot_client(client)


async def _check_claude(report: AgentDoctorReport, directory: str) -> None:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, SystemMessage, query

    if report.reasoning_effort is not None:
        raise ValueError("Autosprint reasoning_effort configuration is only supported for Copilot agents.")
    captured: dict = {}
    server, tool_names = _build_claude_ask_report_tools(captured)
    options = ClaudeAgentOptions(model=report.model, cwd=directory, system_prompt=_PROBE_PROMPT, setting_sources=[], tools=[], allowed_tools=tool_names, mcp_servers={"autosprint": server}, permission_mode="dontAsk", max_turns=3)
    async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS), aclosing(query(prompt=_PROBE_PROMPT, options=options)) as messages:
        async for message in messages:
            if isinstance(message, SystemMessage) and message.subtype == "init":
                runtime_version = message.data.get("claude_code_version")
                if isinstance(runtime_version, str):
                    report.runtime_version = runtime_version
            if isinstance(message, ResultMessage) and message.is_error:
                raise RuntimeError(f"Claude probe failed: {message.result or message.subtype}")
    _check_receipt(captured)
    report.checks.append("Claude runtime, authentication and model access verified by live tool exchange.")


def run_agent_doctor(agent_key: str) -> int:
    """Emit one JSON report. Only a small synthetic probe is sent to the chosen backend."""
    report = AgentDoctorReport(agent=agent_key)
    exit_code = 1
    with redirect_stdout(sys.stderr):
        try:
            if agent_key not in AGENTS:
                raise ValueError(f"Unknown agent {agent_key!r}. Use `autosprint agents` to list valid keys.")
            agent = AGENTS[agent_key]
            report.assistant = agent["assistant"]
            report.model = agent["model"]
            report.reasoning_effort = agent.get("reasoning_effort")
            if report.assistant not in ("copilot", "claude"):
                raise ValueError(f"Unsupported assistant: {report.assistant!r}")
            package = "github-copilot-sdk" if report.assistant == "copilot" else "claude-agent-sdk"
            report.sdk_version = version(package)
            report.checks.append(f"{package} installed.")
            with TemporaryDirectory(prefix="autosprint-agent-doctor-") as directory:
                check = _check_copilot if report.assistant == "copilot" else _check_claude
                asyncio.run(check(report, directory))
            report.checks.append("Selected model completed a live structured-tool round-trip.")
            report.status = "success"
            exit_code = 0
        except (Exception, KeyboardInterrupt) as exc:
            interrupted = isinstance(exc, KeyboardInterrupt)
            report.blockers.append("Interrupted by caller." if interrupted else str(exc) or type(exc).__name__)
            exit_code = 130 if interrupted else 1
    sys.stdout.write(json.dumps(report.model_dump(), indent=2) + "\n")
    return exit_code
