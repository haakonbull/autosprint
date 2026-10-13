"""Prompt transport and lossless compact reports for parent agents."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from autosprint.ask import run_ask
from autosprint.config import config


def install_dispatch(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    received: list[str] = []

    async def dispatch(agent, prompt, **kwargs):
        received.append(prompt)
        kwargs["result_capture"].update(status="success", summary="Review completed.", files_changed=[], tests_run=[], blockers=[])
        return "Detailed evidence. " * 2000

    monkeypatch.setattr("autosprint.ask.query_agent", dispatch)
    return received


@pytest.mark.parametrize("source", ["file", "stdin"])
def test_prompt_transport_preserves_unicode_newlines_and_quotes(monkeypatch, tmp_path, capsys, source):
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    received = install_dispatch(monkeypatch)
    text = 'Review "quoted paths".\nNorwegian: \u00e6\u00f8\u00e5\n' + "context " * 6000
    prompt_file = tmp_path / "task.txt"
    prompt_file.write_text(text, encoding="utf-8-sig")
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    options = {"prompt_file": str(prompt_file)} if source == "file" else {"stdin": True}
    assert run_ask("bug_hunter_gpt6", [], **options) == 0
    assert received[0].startswith(text.rstrip() + "\n\n")
    assert json.loads(capsys.readouterr().out)["status"] == "success"


@pytest.mark.parametrize("source", ["missing", "empty", "invalid-utf8", "interactive-stdin"])
def test_bad_prompt_source_fails_before_dispatch(monkeypatch, tmp_path, capsys, source):
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    dispatch = AsyncMock()
    monkeypatch.setattr("autosprint.ask.query_agent", dispatch)
    path = tmp_path / "task.txt"
    if source == "empty":
        path.write_text(" \n", encoding="utf-8")
    elif source == "invalid-utf8":
        path.write_bytes(b"\xff\xff")
    stream = io.StringIO("Do not read this interactively.")
    monkeypatch.setattr(stream, "isatty", lambda: True)
    monkeypatch.setattr("sys.stdin", stream)
    options = {"stdin": True} if source == "interactive-stdin" else {"prompt_file": str(path)}
    assert run_ask("bug_hunter_gpt6", [], **options) == 1
    assert json.loads(capsys.readouterr().out)["blockers"]
    dispatch.assert_not_called()


def test_compact_stdout_retains_full_report_in_explicit_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    install_dispatch(monkeypatch)
    path = tmp_path / "result.json"
    assert run_ask("bug_hunter_gpt6", ["Review."], compact=True, report_file=str(path)) == 0
    stdout = capsys.readouterr().out
    compact = json.loads(stdout)
    full_text = path.read_text(encoding="utf-8")
    full = json.loads(full_text)
    assert "raw_response" not in compact
    assert compact.pop("report_file") == str(path.resolve())
    assert compact == {key: value for key, value in full.items() if key != "raw_response"}
    assert len(stdout) < len(full_text) / 10
    assert len(full["raw_response"]) > 30000


def test_existing_report_file_is_never_overwritten_or_dispatched(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    dispatch = AsyncMock()
    monkeypatch.setattr("autosprint.ask.query_agent", dispatch)
    path = tmp_path / "existing.json"
    path.write_text("Existing report", encoding="utf-8")
    assert run_ask("bug_hunter_gpt6", ["Review."], compact=True, report_file=str(path)) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
    assert path.read_text(encoding="utf-8") == "Existing report"
    dispatch.assert_not_called()


def test_report_write_failure_returns_full_failure_not_compact_success(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    install_dispatch(monkeypatch)
    monkeypatch.setattr("autosprint.ask._write_report", lambda *args: (_ for _ in ()).throw(OSError("Disk full")))
    assert run_ask("bug_hunter_gpt6", ["Review."], compact=True, report_file=str(tmp_path / "result.json")) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "failed"
    assert "Disk full" in str(report["blockers"])
    assert report["raw_response"]
    assert "report_file" not in report


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["Task.", "--stdin"],
        ["Task.", "--prompt-file", "task.txt"],
        ["--stdin", "--prompt-file", "task.txt"],
        ["Task.", "--compact"],
        ["Task.", "--compact", "--report-file", "result.json", "--output", "text"],
    ],
)
def test_invalid_source_and_output_combinations_are_usage_errors(monkeypatch, tmp_path, arguments):
    from autosprint.cli import parse_cli_args

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["autosprint", "ask", "--agent", "bug_hunter_gpt6", *arguments])
    with pytest.raises(SystemExit) as error:
        parse_cli_args()
    assert error.value.code == 2


@pytest.mark.parametrize("status,code", [("blocked", 3), ("failed", 1)])
def test_compact_reports_keep_blockers_and_exit_status(monkeypatch, tmp_path, capsys, status, code):
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))

    async def dispatch(agent, prompt, **kwargs):
        kwargs["result_capture"].update(status=status, summary="Cannot complete.", files_changed=["partial.py"], tests_run=["pytest: failed"], blockers=["Needs a decision."])
        return "Full details."

    monkeypatch.setattr("autosprint.ask.query_agent", dispatch)
    path = tmp_path / "result.json"
    assert run_ask("bug_hunter_gpt6", ["Task."], compact=True, report_file=str(path)) == code
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == status
    assert report["blockers"] == ["Needs a decision."]
    assert report["files_changed"] == ["partial.py"]
    assert report["tests_run"] == ["pytest: failed"]


def test_relative_prompt_and_report_paths_use_caller_not_target(monkeypatch, tmp_path, capsys):
    from autosprint.orchestrator import main

    caller = tmp_path / "caller"
    target = tmp_path / "target"
    caller.mkdir()
    target.mkdir()
    monkeypatch.chdir(caller)
    (caller / "task.txt").write_text("Review the target.", encoding="utf-8")
    received = install_dispatch(monkeypatch)
    monkeypatch.setattr("sys.argv", ["autosprint", "ask", "--agent", "bug_hunter_gpt6", "--target", str(target), "--prompt-file", "task.txt", "--compact", "--report-file", "result.json"])
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 0
    assert received[0].startswith("Review the target.")
    assert (caller / "result.json").is_file()
    assert list(target.iterdir()) == []
    assert json.loads(capsys.readouterr().out)["target_repo"] == str(target)


@pytest.mark.parametrize("compact", [False, True])
def test_json_preserves_unicode_even_on_non_utf8_stdout(monkeypatch, tmp_path, compact):
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    summary = "Finding: \u00e6\u00f8\u00e5 \U0001f426"

    async def dispatch(agent, prompt, **kwargs):
        kwargs["result_capture"].update(status="success", summary=summary, files_changed=[], tests_run=[], blockers=[])
        return summary

    monkeypatch.setattr("autosprint.ask.query_agent", dispatch)
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="ascii", write_through=True)
    monkeypatch.setattr("sys.stdout", stream)
    assert run_ask("bug_hunter_gpt6", ["Review."], compact=compact, report_file=str(tmp_path / "report.json")) == 0
    assert json.loads(buffer.getvalue().decode("ascii"))["summary"] == summary


@pytest.mark.live
@pytest.mark.parametrize("source", ["file", "stdin"])
def test_live_prompt_transport_and_compact_archive(tmp_path: Path, source: str) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "memory.md").write_text("Review marker: blue-sparrow-73.\n", encoding="utf-8")
    prompt = 'Read memory.md. Include its marker and the literal text "\u00e6\u00f8\u00e5" in your summary.\nDo not edit anything or run tests. Submit the structured report.'
    task = tmp_path / "task.txt"
    task.write_text(prompt, encoding="utf-8")
    archive = tmp_path / "report.json"
    options = ["--prompt-file", str(task)] if source == "file" else ["--stdin"]
    result = subprocess.run(
        [sys.executable, "-m", "autosprint.orchestrator", "ask", "--agent", "bug_hunter_gpt6", "--target", str(target), *options, "--compact", "--report-file", str(archive)],
        input=prompt if source == "stdin" else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp_path,
        env={**os.environ, "LLM_SESSION_TIMEOUT_SECONDS": "90"},
        timeout=120,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    report = json.loads(result.stdout)
    full = json.loads(archive.read_text(encoding="utf-8"))
    assert report["status"] == "success" and report["structured"] is True
    assert "blue-sparrow-73" in report["summary"] and "\u00e6\u00f8\u00e5" in report["summary"]
    assert report["report_file"] == str(archive)
    assert "raw_response" not in report and "raw_response" in full
    assert full["summary"] == report["summary"]
    assert sorted(path.name for path in target.iterdir()) == ["memory.md"]
