"""Tests for `autosprint doctor` and `autosprint how-far` one-shot subcommands.

All fast — no LLM calls (dispatch is mocked).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import autosprint.init as init_mod
from autosprint.config import config

# ---------------------------------------------------------------------------
# run_doctor — verify-the-setup command with a live agent round-trip.
# ---------------------------------------------------------------------------


def _make_doctor_target(tmp_path: Path) -> None:
    """Build a tmp_path that passes doctor's repo + destination.md checks."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "autosprint").mkdir()
    (tmp_path / "autosprint" / "destination.md").write_text("x" * 300, encoding="utf-8")


def test_run_doctor_all_green(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A valid setup with a working dispatch round-trip passes — run_doctor does not raise."""
    from unittest.mock import AsyncMock

    from autosprint.init import run_doctor

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    _make_doctor_target(tmp_path)
    monkeypatch.setattr(init_mod, "_required_assistants_for_run", lambda: {"copilot"})
    monkeypatch.setattr("autosprint.dispatch.query_agent", AsyncMock(return_value="OK"))
    run_doctor()  # all checks pass → no SystemExit


def test_run_doctor_exits_nonzero_on_failed_dispatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A failing dispatch round-trip makes doctor exit non-zero (SystemExit)."""
    from unittest.mock import AsyncMock

    from autosprint.init import run_doctor

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    _make_doctor_target(tmp_path)
    monkeypatch.setattr(init_mod, "_required_assistants_for_run", lambda: {"copilot"})
    monkeypatch.setattr("autosprint.dispatch.query_agent", AsyncMock(side_effect=RuntimeError("no auth")))
    with pytest.raises(SystemExit):
        run_doctor()


# ---------------------------------------------------------------------------
# probe_backends — the live round-trip gate run at the start of `autosprint run`
# (abort mode) and at the end of `autosprint init` (warn-only mode).
# ---------------------------------------------------------------------------


def test_probe_backends_passes_on_working_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock

    from autosprint.init import probe_backends

    monkeypatch.setattr(config, "LOG_LEVEL", 100)
    monkeypatch.setattr(init_mod, "_required_assistants_for_run", lambda: {"claude", "copilot"})
    monkeypatch.setattr("autosprint.dispatch.query_agent", AsyncMock(return_value="OK"))
    assert probe_backends() is True


def test_probe_backends_raises_on_failed_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run mode: a dead backend aborts before any state mutation, pointing at doctor."""
    from unittest.mock import AsyncMock

    from autosprint.init import probe_backends

    monkeypatch.setattr(config, "LOG_LEVEL", 100)
    monkeypatch.setattr(init_mod, "_required_assistants_for_run", lambda: {"copilot"})
    monkeypatch.setattr("autosprint.dispatch.query_agent", AsyncMock(side_effect=RuntimeError("Copilot CLI not found")))
    with pytest.raises(RuntimeError, match="autosprint doctor"):
        probe_backends()


def test_probe_backends_warn_only_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Init mode: a dead backend warns and returns False — init still completes (auth may simply not be set up yet)."""
    from unittest.mock import AsyncMock

    from autosprint.init import probe_backends

    monkeypatch.setattr(config, "LOG_LEVEL", 100)
    monkeypatch.setattr(init_mod, "_required_assistants_for_run", lambda: {"copilot"})
    monkeypatch.setattr("autosprint.dispatch.query_agent", AsyncMock(side_effect=RuntimeError("no auth")))
    assert probe_backends(warn_only=True) is False


def test_probe_backends_skipped_in_debug_and_cache_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    """LOG_LEVEL <= 15 (debug / cache dev loops) must never make live calls."""
    from unittest.mock import AsyncMock

    from autosprint.init import probe_backends

    monkeypatch.setattr(config, "LOG_LEVEL", 15)
    dispatch_mock = AsyncMock(side_effect=AssertionError("probe must not dispatch in debug/cache mode"))
    monkeypatch.setattr("autosprint.dispatch.query_agent", dispatch_mock)
    assert probe_backends() is True
    dispatch_mock.assert_not_called()


# ---------------------------------------------------------------------------
# _check_install_health — spots stale `pip install -e .` installs whose
# metadata doesn't list the new runtime deps. Direct unit tests so we don't
# have to fight `run_doctor`'s full setup.
# ---------------------------------------------------------------------------


def test_check_install_health_green_in_normal_env() -> None:
    """In the dev venv where pytest runs, both runtime deps are importable and the installed version matches pyproject. So this is a real-environment smoke test — no mocking."""
    from autosprint.init import _check_install_health

    ok, msg = _check_install_health()
    assert ok, f"Expected install health OK in the dev venv, got: {msg}"
    assert "Install health OK" in msg


def test_check_install_health_flags_missing_runtime_dep(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a runtime dep can't be imported, doctor reports the missing module + install hint + the uv-tool-install recovery command."""
    import importlib

    from autosprint.init import _check_install_health

    real_import = importlib.import_module

    def fake_import(name: str, *args, **kwargs):
        if name == "copilot":
            raise ImportError("No module named 'copilot'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    ok, msg = _check_install_health()
    assert not ok
    assert "Stale install detected" in msg
    assert "`copilot`" in msg
    assert "github-copilot-sdk" in msg
    assert "uv tool install --editable" in msg


def test_check_install_health_flags_version_skew(monkeypatch: pytest.MonkeyPatch) -> None:
    """When `importlib.metadata.version('autosprint')` returns a different version than this checkout's pyproject.toml declares, doctor flags it as a stale install (the trap caused by pip-installed v0.1.0 metadata pointing at editable v0.2.0 code)."""
    import importlib.metadata

    from autosprint.init import _check_install_health

    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.1.0" if name == "autosprint" else importlib.metadata.version(name))
    ok, msg = _check_install_health()
    assert not ok
    assert "version skew" in msg
    assert "0.1.0" in msg
    assert "uv tool install --editable" in msg


def test_run_doctor_exits_nonzero_when_install_is_stale(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """End-to-end: a stale install (mocked as missing `copilot`) makes doctor exit non-zero even when everything else would have passed."""
    import importlib

    from unittest.mock import AsyncMock

    from autosprint.init import run_doctor

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    _make_doctor_target(tmp_path)
    monkeypatch.setattr(init_mod, "_required_assistants_for_run", lambda: {"copilot"})
    monkeypatch.setattr("autosprint.dispatch.query_agent", AsyncMock(return_value="OK"))

    real_import = importlib.import_module

    def fake_import(name: str, *args, **kwargs):
        if name == "copilot":
            raise ImportError("No module named 'copilot'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(SystemExit):
        run_doctor()


# ---------------------------------------------------------------------------
# run_how_far — read-only distance-to-destination measurement command.
# ---------------------------------------------------------------------------


def _make_howfar_target(tmp_path: Path) -> None:
    """Build a tmp_path with a non-empty destination.md so run_how_far gets past its abort check."""
    (tmp_path / "autosprint").mkdir()
    (tmp_path / "autosprint" / "destination.md").write_text("x" * 300, encoding="utf-8")


def test_run_how_far_dispatches_with_read_only_preset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """run_how_far dispatches one agent with the read-only tool preset and the skill instructions."""
    from unittest.mock import AsyncMock

    from autosprint.agents import TOOLS_READ_ONLY
    from autosprint.how_far import run_how_far

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    _make_howfar_target(tmp_path)
    mock = AsyncMock(return_value="Distance to destination — 3 requirements: 1 done")
    monkeypatch.setattr("autosprint.how_far.query_agent", mock)
    run_how_far()
    assert mock.await_count == 1
    assert mock.await_args.kwargs["tools"] == TOOLS_READ_ONLY
    assert "how-far skill instructions" in mock.await_args.args[1]


def test_run_how_far_aborts_when_destination_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No destination.md → run_how_far raises RuntimeError; nothing to measure against."""
    from autosprint.how_far import run_how_far

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    with pytest.raises(RuntimeError, match="nothing to measure"):
        run_how_far()


def test_run_how_far_unknown_agent_override_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An unknown --agent key raises RuntimeError naming it, rather than dispatching."""
    from autosprint.how_far import run_how_far

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    _make_howfar_target(tmp_path)
    with pytest.raises(RuntimeError, match="not a known agent"):
        run_how_far(agent_override="nonexistent_agent")


# ---------------------------------------------------------------------------
# run_howfar_heartbeat — in-loop progress sensor.
# ---------------------------------------------------------------------------


def test_heartbeat_headline_picks_distance_line() -> None:
    """The headline extractor returns the `Distance to destination` line stripped of markdown bold."""
    from autosprint.how_far import _heartbeat_headline

    report = "Some preamble\n\n**Distance to destination — 14 requirements: 6 ✅ done · 4 🟡 partial · 3 ⬜ not started · 1 ❓ unclear**\n\n| table | ..."
    assert _heartbeat_headline(report) == "Distance to destination — 14 requirements: 6 ✅ done · 4 🟡 partial · 3 ⬜ not started · 1 ❓ unclear"


def test_heartbeat_headline_falls_back_to_first_nonempty_line() -> None:
    """When no `Distance to destination` line is present, the first non-empty line is used as a fallback."""
    from autosprint.how_far import _heartbeat_headline

    assert _heartbeat_headline("\n\nRaw output line 1\nRaw output line 2") == "Raw output line 1"


def test_heartbeat_headline_handles_empty_report() -> None:
    """An empty report yields a sentinel string rather than crashing."""
    from autosprint.how_far import _heartbeat_headline

    assert _heartbeat_headline("") == "(empty how-far report)"


def test_run_howfar_heartbeat_appends_log_and_prints_headline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A successful heartbeat appends the full report to autosprint/logs/howfar-heartbeat.log with a sprint-numbered header, and prints the headline inline."""
    import asyncio
    from unittest.mock import AsyncMock

    from autosprint.how_far import run_howfar_heartbeat

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    _make_howfar_target(tmp_path)
    report = "**Distance to destination — 5 requirements: 2 ✅ done · 3 ⬜ not started**\n\n| Req | Status | Evidence |\n|---|---|---|\nVerdict: early days."
    monkeypatch.setattr("autosprint.how_far.query_agent", AsyncMock(return_value=report))

    asyncio.run(run_howfar_heartbeat(sprint_number=10))

    log_path = tmp_path / "autosprint" / "logs" / "howfar-heartbeat.log"
    assert log_path.exists()
    log_text = log_path.read_text(encoding="utf-8")
    assert "Sprint 10" in log_text
    assert "Distance to destination — 5 requirements" in log_text
    out = capsys.readouterr().out
    assert "📡 Sprint 10" in out
    assert "Distance to destination — 5 requirements: 2 ✅ done · 3 ⬜ not started" in out


def test_run_howfar_heartbeat_swallows_dispatch_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A dispatch failure does NOT raise — heartbeat is a sensor, not a gate. Warning is printed; PIT loop continues."""
    import asyncio
    from unittest.mock import AsyncMock

    from autosprint.how_far import run_howfar_heartbeat

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    _make_howfar_target(tmp_path)
    monkeypatch.setattr("autosprint.how_far.query_agent", AsyncMock(side_effect=RuntimeError("dispatch boom")))

    asyncio.run(run_howfar_heartbeat(sprint_number=20))  # must not raise

    out = capsys.readouterr().out
    assert "⚠" in out and "sprint 20" in out and "dispatch boom" in out


def test_run_howfar_heartbeat_swallows_missing_destination(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Missing destination.md swallows quietly too — surfaces a warning but loop continues."""
    import asyncio

    from autosprint.how_far import run_howfar_heartbeat

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    asyncio.run(run_howfar_heartbeat(sprint_number=10))
    out = capsys.readouterr().out
    flat = " ".join(out.split())  # collapse any line-wrapping in the printed message
    assert "⚠" in out and "nothing to" in flat and "measure" in flat
