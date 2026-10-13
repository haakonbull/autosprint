"""Standalone preflight must check the selected agent, never the sprint team."""

import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from autosprint.agent_doctor import run_agent_doctor


def copilot_client(monkeypatch):
    from copilot.tools import ToolInvocation

    model = SimpleNamespace(id="gpt-6-astra", policy=None, supported_reasoning_efforts=["low", "high"])
    options = []

    async def create_session(**kwargs):
        options.append(kwargs)

        async def send_and_wait(*args, **kwargs):
            tool = options[-1]["tools"][0]
            await tool.handler(ToolInvocation(session_id="probe", tool_call_id="result", tool_name=tool.name, arguments={"status": "success", "summary": "AUTOSPRINT_OK", "files_changed": [], "tests_run": [], "blockers": []}))

        return SimpleNamespace(send_and_wait=send_and_wait)

    client = SimpleNamespace(start=AsyncMock(), get_status=AsyncMock(return_value=SimpleNamespace(version="test-runtime")), get_auth_status=AsyncMock(return_value=SimpleNamespace(isAuthenticated=True)), list_models=AsyncMock(return_value=[model]), create_session=AsyncMock(side_effect=create_session), stop=AsyncMock(), force_stop=AsyncMock())

    def create_client(**kwargs):
        client.startup_directory = kwargs["working_directory"]
        return client

    monkeypatch.setattr("copilot.CopilotClient", create_client)
    return client, model, options


def test_selected_copilot_agent_gets_tool_only_roundtrip(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    client, _, options = copilot_client(monkeypatch)
    assert run_agent_doctor("bug_hunter_gpt6") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "success"
    assert report["runtime_version"] == "test-runtime"
    assert options[0]["model"] == "gpt-6-astra"
    assert options[0]["reasoning_effort"] == "high"
    assert options[0]["available_tools"] == ["submit_ask_report"]
    assert options[0]["working_directory"] != str(tmp_path)
    assert client.startup_directory == options[0]["working_directory"]
    client.stop.assert_awaited_once()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("problem", ["auth", "missing-model", "policy", "reasoning", "runtime"])
def test_preflight_fails_before_inference_for_incompatible_configuration(monkeypatch, capsys, problem):
    client, model, _ = copilot_client(monkeypatch)
    if problem == "auth":
        client.get_auth_status.return_value.isAuthenticated = False
    elif problem == "missing-model":
        client.list_models.return_value = []
    elif problem == "policy":
        model.policy = SimpleNamespace(state="disabled")
    elif problem == "reasoning":
        model.supported_reasoning_efforts = ["low"]
    else:
        client.start.side_effect = RuntimeError("Cannot start runtime")
    assert run_agent_doctor("bug_hunter_gpt6") == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "failed"
    assert report["blockers"]
    client.create_session.assert_not_called()
    client.stop.assert_awaited_once()


def test_doctor_times_out_and_cleans_up(monkeypatch, capsys):
    client, _, _ = copilot_client(monkeypatch)
    monkeypatch.setattr("autosprint.agent_doctor._PROBE_TIMEOUT_SECONDS", 0.01)

    async def hang():
        await asyncio.Event().wait()

    client.list_models.side_effect = hang
    assert run_agent_doctor("bug_hunter_gpt6") == 1
    assert "Timeout" in str(json.loads(capsys.readouterr().out)["blockers"])
    client.stop.assert_awaited_once()


def test_unknown_agent_returns_failure_without_starting_runtime(monkeypatch, capsys):
    client, _, _ = copilot_client(monkeypatch)
    assert run_agent_doctor("no-such-agent") == 1
    assert "Unknown agent" in str(json.loads(capsys.readouterr().out)["blockers"])
    client.start.assert_not_called()


def test_agent_doctor_bypasses_all_sprint_preparation(monkeypatch, tmp_path, capsys):
    from autosprint.orchestrator import main

    copilot_client(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["autosprint", "doctor", "--agent", "bug_hunter_gpt6"])
    monkeypatch.setattr("autosprint.cli._apply_config_toml", lambda *args: pytest.fail("Must not load sprint config"))
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "success"
    assert list(tmp_path.iterdir()) == []


def test_claude_checks_only_selected_backend_and_closes_stream(monkeypatch, capsys):
    from claude_agent_sdk import SystemMessage
    from autosprint.agents import AGENTS

    closed = []
    received = []
    tools = []

    def server(name, version, **kwargs):
        tools.extend(kwargs["tools"])
        return {}

    async def query(*, prompt, options):
        received.append(options)
        try:
            yield SystemMessage(subtype="init", data={"claude_code_version": "test-claude"})
            await tools[0].handler({"status": "success", "summary": "AUTOSPRINT_OK", "files_changed": [], "tests_run": [], "blockers": []})
        finally:
            closed.append(True)

    monkeypatch.setattr("claude_agent_sdk.create_sdk_mcp_server", server)
    monkeypatch.setattr("claude_agent_sdk.query", query)
    monkeypatch.setattr("copilot.CopilotClient", lambda: pytest.fail("Must not probe other backend"))
    assert run_agent_doctor("bug_hunter_opus48") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["runtime_version"] == "test-claude"
    assert received[0].model == AGENTS["bug_hunter_opus48"]["model"]
    assert received[0].tools == []
    assert received[0].setting_sources == []
    assert received[0].permission_mode == "dontAsk"
    assert closed == [True]


def test_claude_error_result_is_not_success(monkeypatch, capsys):
    from claude_agent_sdk import ResultMessage

    async def query(**kwargs):
        yield ResultMessage(subtype="error_max_turns", duration_ms=0, duration_api_ms=0, is_error=True, num_turns=1, session_id="probe")

    monkeypatch.setattr("claude_agent_sdk.query", query)
    assert run_agent_doctor("bug_hunter_opus48") == 1
    assert "error_max_turns" in str(json.loads(capsys.readouterr().out)["blockers"])


def test_missing_tool_receipt_is_not_success(monkeypatch, capsys):
    client, _, _ = copilot_client(monkeypatch)
    client.create_session.side_effect = None
    client.create_session.return_value = SimpleNamespace(send_and_wait=AsyncMock(return_value="OK"))
    assert run_agent_doctor("bug_hunter_gpt6") == 1
    assert json.loads(capsys.readouterr().out)["blockers"]


def test_doctor_json_supports_non_utf8_stdout(monkeypatch):
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="ascii", write_through=True)
    monkeypatch.setattr("sys.stdout", stream)
    assert run_agent_doctor("missing-\u00e6") == 1
    assert json.loads(buffer.getvalue().decode("ascii"))["agent"] == "missing-\u00e6"


def test_delegation_skill_is_available_to_non_python_repos_without_overwrites(monkeypatch, tmp_path):
    from autosprint.config import config
    from autosprint.init import _copy_claude_subdir

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    copied, _ = _copy_claude_subdir("skills")
    assert "autosprint-ask" in copied
    skill = tmp_path / ".claude" / "skills" / "autosprint-ask" / "SKILL.md"
    assert "--prompt-file" in skill.read_text(encoding="utf-8")
    skill.write_text("Local customization", encoding="utf-8")
    _, skipped = _copy_claude_subdir("skills")
    assert "autosprint-ask" in skipped
    assert skill.read_text(encoding="utf-8") == "Local customization"
