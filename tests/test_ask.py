"""Public contract for using ask from another process."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from autosprint.ask import dispatch_ask_async, run_ask
from autosprint.config import config


def outcome(status: str = "success") -> dict:
    return {
        "status": status,
        "summary": ("Reviewed the file. " + "Detailed finding with spaces. " * 20).strip(),
        "files_changed": [],
        "tests_run": [],
        "blockers": [] if status == "success" else ["Needs a decision."],
    }


@pytest.mark.parametrize("status,exit_code", [("success", 0), ("failed", 1), ("blocked", 3)])
def test_json_stdout_is_one_document_with_meaningful_exit_status(monkeypatch, tmp_path, capsys, status, exit_code):
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))

    async def dispatch(*args, **kwargs):
        print("SDK progress")
        kwargs["result_capture"].update(outcome(status))
        return "Finding: the requested review completed."

    monkeypatch.setattr("autosprint.ask.query_agent", dispatch)
    assert run_ask("bug_hunter_gpt6", ["Review the file."], output="json") == exit_code
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["status"] == status
    assert report["summary"] == outcome(status)["summary"]
    assert report["structured"] is True
    assert report["raw_response"] == "Finding: the requested review completed."
    assert "SDK progress" in captured.err
    assert list(tmp_path.iterdir()) == []


def test_unstructured_refusal_is_not_success(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr("autosprint.ask.query_agent", AsyncMock(return_value="I could not complete the task."))
    report = asyncio.run(dispatch_ask_async("bug_hunter_gpt6", ["Review."]))
    assert report["status"] == "failed"
    assert report["structured"] is False
    assert report["blockers"]


def test_sdk_exception_returns_failed_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr("autosprint.ask.query_agent", AsyncMock(side_effect=RuntimeError("Backend unavailable.")))
    assert run_ask("bug_hunter_gpt6", ["Review."], output="json") == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "failed"
    assert report["structured"] is False
    assert "Backend unavailable." in str(report["blockers"])


@pytest.mark.parametrize("agent,effective_tools", [("web_researcher_gpt55", "research"), ("howfar_gpt6", "read-only")])
def test_error_report_retains_effective_permissions(monkeypatch, tmp_path, capsys, agent, effective_tools):
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr("autosprint.ask.query_agent", AsyncMock(side_effect=RuntimeError("Backend unavailable.")))
    assert run_ask(agent, ["Review."], tools="full") == 1
    assert json.loads(capsys.readouterr().out)["tools"] == effective_tools


@pytest.mark.parametrize("error,exit_code", [(asyncio.TimeoutError(), 1), (KeyboardInterrupt(), 130)])
def test_interrupted_ask_returns_failed_json(monkeypatch, tmp_path, capsys, error, exit_code):
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr("autosprint.ask.query_agent", AsyncMock(side_effect=error))
    assert run_ask("bug_hunter_gpt6", ["Review."], output="json") == exit_code
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


def test_read_only_research_agent_is_actually_restricted(monkeypatch, tmp_path):
    from autosprint.dispatch import _effective_preset

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    seen = []

    async def dispatch(agent, prompt, **kwargs):
        seen.append(_effective_preset(agent, kwargs.get("tools")))
        kwargs["result_capture"].update(outcome())
        return ""

    monkeypatch.setattr("autosprint.ask.query_agent", dispatch)
    report = asyncio.run(dispatch_ask_async("web_researcher_gpt55", ["Review."]))
    assert seen == ["read_only"]
    assert report["tools"] == "read-only"


def test_read_only_hook_permits_structured_report_but_not_shell():
    from autosprint.dispatch import _copilot_read_only_hook

    assert asyncio.run(_copilot_read_only_hook({"toolName": "submit_ask_report"}, {}))["permissionDecision"] == "allow"
    assert asyncio.run(_copilot_read_only_hook({"toolName": "powershell"}, {}))["permissionDecision"] == "deny"


def test_ask_never_retries_or_writes_sprint_cache(monkeypatch, tmp_path):
    from autosprint import dispatch as dispatch_module
    from autosprint.dispatch import query_agent

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "LLM_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(config, "LLM_RETRY_BACKOFF_SECONDS", 0)
    dispatch = AsyncMock(side_effect=RuntimeError("Failed after editing."))
    monkeypatch.setitem(dispatch_module.DISPATCHERS, "copilot", dispatch)
    with pytest.raises(RuntimeError):
        asyncio.run(query_agent({"assistant": "copilot", "tools": "full"}, "Fix.", result_capture={}, dispatch_mode="ask"))
    assert dispatch.await_count == 1
    dispatch.side_effect = None
    dispatch.return_value = "Done."
    asyncio.run(query_agent({"assistant": "copilot", "tools": "full"}, "Fix.", result_capture={}, dispatch_mode="ask"))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("target_position", ["before", "after", "cwd"])
def test_ask_target_does_not_leak_from_sprint_config(monkeypatch, tmp_path, target_position):
    from autosprint.cli import parse_cli_args

    target = tmp_path / "linked-worktree"
    target.mkdir()
    (target / ".git").write_text("gitdir: irrelevant", encoding="utf-8")
    monkeypatch.chdir(target)
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path / "wrong-repo"))
    prefix = ["--target", str(target)] if target_position == "before" else []
    suffix = ["--target", str(target)] if target_position == "after" else []
    monkeypatch.setattr("sys.argv", ["autosprint", *prefix, "ask", "--agent", "bug_hunter_gpt6", *suffix, "Review."])
    args = parse_cli_args()
    assert args.command == "ask"
    assert config.TARGET_REPO_PATH == target


def test_missing_target_fails_without_dispatch(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path / "missing"))
    dispatch = AsyncMock()
    monkeypatch.setattr("autosprint.ask.query_agent", dispatch)
    assert run_ask("bug_hunter_gpt6", ["Review."], output="json") == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
    dispatch.assert_not_called()


def test_every_bundled_preset_references_existing_agents_and_teams():
    from autosprint.agents import AGENTS
    from autosprint.cli import _CLI_PRESETS
    from autosprint.teams import TEAMS

    for preset in _CLI_PRESETS.values():
        assert preset["team"] in TEAMS
        assert preset["implement_agent"] in AGENTS


@pytest.mark.parametrize("agent,prompt", [("missing", ["Review."]), ("bug_hunter_gpt6", [" "])])
def test_invalid_task_returns_failed_json_without_dispatch(monkeypatch, tmp_path, capsys, agent, prompt):
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    dispatch = AsyncMock()
    monkeypatch.setattr("autosprint.ask.query_agent", dispatch)
    assert run_ask(agent, prompt) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
    dispatch.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"status": "not-a-status"},
        {"summary": " "},
        {"summary": 123},
        {"files_changed": "file.py"},
        {"tests_run": [123]},
        {"status": "blocked", "blockers": []},
        {"blockers": ["Still broken."]},
        {"surprise": True},
    ],
)
def test_report_contract_rejects_malformed_evidence(change):
    from autosprint.ask_report import capture_ask_report

    capture = {}
    error = capture_ask_report(outcome() | change, capture)
    assert error and "submit_ask_report" in error
    assert capture == {}


def test_report_contract_requires_evidence_lists():
    from autosprint.ask_report import capture_ask_report

    capture = {}
    assert capture_ask_report({"status": "success", "summary": "Done."}, capture)
    assert capture == {}


@pytest.mark.parametrize("preset", ["read-only", "research", "full"])
def test_copilot_sdk_receives_persona_permissions_and_report_tool(monkeypatch, tmp_path, preset):
    from types import SimpleNamespace

    from autosprint.agents import AGENTS
    from autosprint.dispatch import _run_copilot
    from copilot.tools import ToolInvocation

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    stop_file = tmp_path / "autosprint" / "stop-now"
    stop_file.parent.mkdir()
    stop_file.write_text("Unrelated sprint stop signal.", encoding="utf-8")
    captured = {}
    sessions = []

    class Client:
        async def start(self):
            pass

        async def create_session(self, **kwargs):
            sessions.append(kwargs)
            return self

        async def send_and_wait(self, prompt, timeout):
            kwargs = sessions[-1]
            kwargs["on_event"](SimpleNamespace(type=SimpleNamespace(value="assistant.message"), data=SimpleNamespace(content="First detailed finding.")))
            tool = kwargs["tools"][0]
            if "hooks" in kwargs:
                decision = await kwargs["hooks"]["on_pre_tool_use"]({"toolName": tool.name}, {})
                assert decision["permissionDecision"] == "allow"
            await tool.handler(ToolInvocation(session_id="test", tool_call_id="report", tool_name=tool.name, arguments=outcome()))
            return SimpleNamespace(data=SimpleNamespace(content="Review findings."))

        async def stop(self):
            pass

    monkeypatch.setattr("copilot.CopilotClient", Client)
    actual = asyncio.run(_run_copilot(AGENTS["bug_hunter_gpt6"], "Review.", preset.replace("-", "_"), captured, "ask"))
    assert actual == "First detailed finding.\n\nReview findings."
    assert captured == outcome()
    assert stop_file.read_text(encoding="utf-8") == "Unrelated sprint stop signal."
    kwargs = sessions[0]
    assert kwargs["model"] == "gpt-6-astra"
    assert kwargs["reasoning_effort"] == "high"
    assert kwargs["system_message"]["content"] == AGENTS["bug_hunter_gpt6"]["system_prompt"]
    assert kwargs["working_directory"] == str(tmp_path)
    if preset != "full":
        assert "submit_ask_report" in kwargs["available_tools"]
        assert "powershell" not in kwargs["available_tools"]
        hook = kwargs["hooks"]["on_pre_tool_use"]
        for name in ["powershell", "bash", "task", "mcp__unknown"]:
            assert asyncio.run(hook({"toolName": name}, {}))["permissionDecision"] == "deny"
        if preset == "read-only":
            assert asyncio.run(hook({"toolName": "edit"}, {}))["permissionDecision"] == "deny"


def test_claude_report_tool_validates_the_same_contract(monkeypatch):
    from autosprint.dispatch import _build_claude_ask_report_tools

    tools = []

    def make_server(name, version, **kwargs):
        tools.extend(kwargs["tools"])
        return {}

    monkeypatch.setattr("claude_agent_sdk.create_sdk_mcp_server", make_server)
    capture = {}
    _, names = _build_claude_ask_report_tools(capture)
    assert names == ["mcp__autosprint__submit_ask_report"]
    invalid = outcome() | {"tests_run": ["pytest"], "status": "invalid"}
    assert asyncio.run(tools[0].handler(invalid))["is_error"] is True
    assert capture == {}
    result = asyncio.run(tools[0].handler(outcome()))
    assert not result.get("is_error", False)
    assert capture == outcome()


def test_claude_sdk_receives_restricted_tools_and_reports_backend_errors(monkeypatch, tmp_path):
    from claude_agent_sdk import ResultMessage
    from autosprint.agents import AGENTS
    from autosprint.dispatch import _run_claude

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    received = []

    async def query(*, prompt, options: object):
        received.append(options)
        yield ResultMessage(subtype="error_max_turns", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=1, session_id="test")

    monkeypatch.setattr("claude_agent_sdk.query", query)
    with pytest.raises(RuntimeError, match="Claude task failed"):
        asyncio.run(_run_claude(AGENTS["bug_hunter_opus48"], "Review.", "read_only", {}, "ask"))
    opts = received[0]
    assert opts.tools == ["Read", "Glob", "Grep"]
    assert "mcp__autosprint__submit_ask_report" in opts.allowed_tools
    assert opts.setting_sources == ["project"]
    assert opts.permission_mode == "dontAsk"
    assert opts.system_prompt["append"] == AGENTS["bug_hunter_opus48"]["system_prompt"]


async def test_cancellation_waits_for_owned_copilot_cleanup():
    from types import SimpleNamespace

    from autosprint.dispatch import _stop_ask_client

    stopping = asyncio.Event()
    release = asyncio.Event()
    stopped = asyncio.Event()

    async def stop():
        stopping.set()
        await release.wait()
        stopped.set()

    client = SimpleNamespace(stop=stop, force_stop=AsyncMock())
    task = asyncio.create_task(_stop_ask_client(client))
    await stopping.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
    client.force_stop.assert_not_called()


async def test_hung_copilot_shutdown_forces_stop_and_surfaces_failure(monkeypatch):
    from types import SimpleNamespace

    from autosprint.dispatch import _stop_ask_client

    async def stop():
        await asyncio.Event().wait()

    monkeypatch.setattr("autosprint.dispatch._COPILOT_SHUTDOWN_TIMEOUT_SECONDS", 0.01)
    client = SimpleNamespace(stop=stop, force_stop=AsyncMock())
    with pytest.raises(TimeoutError):
        await _stop_ask_client(client)
    client.force_stop.assert_awaited_once()


def test_ask_deadline_during_shutdown_waits_for_cleanup_before_reporting(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace

    stopped = []

    async def stop():
        await asyncio.sleep(1.05)
        stopped.append(True)

    session = SimpleNamespace(send_and_wait=AsyncMock(return_value=None))
    client = SimpleNamespace(start=AsyncMock(), create_session=AsyncMock(return_value=session), stop=stop, force_stop=AsyncMock())
    monkeypatch.setattr("copilot.CopilotClient", lambda: client)
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "LLM_SESSION_TIMEOUT_SECONDS", 1)
    assert run_ask("bug_hunter_gpt6", ["Review."]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
    assert stopped == [True]
    client.force_stop.assert_not_called()


def test_invalid_dispatch_mode_does_not_start_a_sprint(monkeypatch):
    from autosprint import dispatch

    backend = AsyncMock()
    monkeypatch.setitem(dispatch.DISPATCHERS, "copilot", backend)
    with pytest.raises(ValueError, match="Unknown dispatch mode"):
        asyncio.run(dispatch.query_agent({"assistant": "copilot"}, "Task.", dispatch_mode="ak"))
    backend.assert_not_called()


def test_cli_entry_point_returns_report_without_entering_sprint_preparation(monkeypatch, tmp_path, capsys):
    from autosprint.orchestrator import main

    async def dispatch(*args, **kwargs):
        kwargs["result_capture"].update(outcome())
        return ""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("autosprint.ask.query_agent", dispatch)
    monkeypatch.setattr("sys.argv", ["autosprint", "ask", "--agent", "bug_hunter_gpt6", "Review."])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "success"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.live
def test_live_copilot_ask_reads_requested_memory_and_returns_json(tmp_path: Path) -> None:
    """A synthetic target checks the actual SDK/CLI protocol without real repo data."""
    (tmp_path / "memory.md").write_text("The example project's review marker is orange-lighthouse.\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "autosprint.orchestrator",
            "ask",
            "--agent",
            "bug_hunter_gpt6",
            "--target",
            str(tmp_path),
            "Read memory.md in the target directory. Report its review marker in your summary. " "Do not edit anything or run tests. Submit a success report only if you actually read the marker.",
        ],
        cwd=tmp_path,
        env={**os.environ, "LLM_SESSION_TIMEOUT_SECONDS": "90"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    report = json.loads(result.stdout)
    assert report["status"] == "success"
    assert report["structured"] is True
    assert report["model"] == "gpt-6-astra"
    assert report["reasoning_effort"] == "high"
    assert "orange-lighthouse" in report["summary"]
    assert report["files_changed"] == []
    assert report["tests_run"] == []
    assert sorted(path.name for path in tmp_path.iterdir()) == ["memory.md"]
