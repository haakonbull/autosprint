"""Tests for the test-runner adapters (pytest + vitest) in autosprint.test_runners.

All fast — no subprocesses, no LLM calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autosprint.config import config

# ---------------------------------------------------------------------------
# test-runner adapters (test_runners.py)
# ---------------------------------------------------------------------------


def test_pytest_runner_command_full_suite() -> None:
    """A full-suite command is `<python> -m pytest` with no marker filter."""
    from autosprint.test_runners import PytestRunner

    cmd = PytestRunner().command(quick=False)
    assert cmd[1:] == ["-m", "pytest"]


def test_pytest_runner_command_quick_adds_marker() -> None:
    """A quick command appends the `-m "not slow"` marker filter."""
    from autosprint.test_runners import PytestRunner

    cmd = PytestRunner().command(quick=True)
    assert cmd[1:] == ["-m", "pytest", "-m", "not slow"]


def test_pytest_runner_command_terse_adds_compact_flags() -> None:
    """A terse command (planner pre-flight) adds `--tb=line -q` before the marker filter."""
    from autosprint.test_runners import PytestRunner

    cmd = PytestRunner().command(quick=True, terse=True)
    assert cmd[1:] == ["-m", "pytest", "--tb=line", "-q", "-m", "not slow"]


def test_pytest_runner_interpret_no_tests_is_green() -> None:
    """pytest exit 5 (no tests collected) interprets as ok + no_tests."""
    from autosprint.test_runners import PytestRunner

    assert PytestRunner().interpret(5, "", "") == (True, True, None)


def test_pytest_runner_interpret_failure() -> None:
    """A non-zero, non-5 exit interprets as not ok."""
    from autosprint.test_runners import PytestRunner

    assert PytestRunner().interpret(1, "FAILED tests/test_x.py::test_y", "") == (False, False, None)


def test_pytest_runner_interpret_pass_counts_tests() -> None:
    """A clean exit 0 interprets as ok and extracts the passed count from the summary line."""
    from autosprint.test_runners import PytestRunner

    assert PytestRunner().interpret(0, "======= 12 passed in 0.30s =======", "") == (True, False, 12)


def test_get_test_runner_returns_pytest_today() -> None:
    """`get_test_runner` resolves to the pytest adapter for a Python target (autosprint's own repo)."""
    from autosprint.test_runners import get_test_runner

    assert get_test_runner().name == "pytest"


# ---------------------------------------------------------------------------
# vitest adapter (test_runners.VitestRunner) + runner detection
# ---------------------------------------------------------------------------


def _vitest_report(total: int, passed: int, failed: int, failed_names: list[str] | None = None) -> dict:
    """Build a minimal vitest/Jest-shaped JSON report for adapter tests."""
    assertion_results: list[dict] = [{"status": "passed", "fullName": f"pass {i}"} for i in range(passed)]
    assertion_results.extend({"status": "failed", "fullName": name, "failureMessages": [f"AssertionError: {name} broke\n  at file.test.ts:1"]} for name in failed_names or [])
    return {
        "numTotalTests": total,
        "numPassedTests": passed,
        "numFailedTests": failed,
        "success": failed == 0,
        "testResults": [{"name": "suite.test.ts", "assertionResults": assertion_results}],
    }


def test_extract_json_objects_finds_multiple_reports() -> None:
    """The brace-scanner pulls every top-level JSON object out of a noisy multi-package stream."""
    from autosprint.test_runners import _extract_json_objects

    stream = "src/api exec$ " + json.dumps({"numTotalTests": 2}) + "\nsrc/web exec$ " + json.dumps({"numTotalTests": 3})
    objs = _extract_json_objects(stream)
    assert [o["numTotalTests"] for o in objs] == [2, 3]


def test_extract_json_objects_ignores_braces_inside_strings() -> None:
    """A `}` inside a JSON string value must not prematurely close the object."""
    from autosprint.test_runners import _extract_json_objects

    objs = _extract_json_objects('noise {"msg": "a } brace in a string", "numTotalTests": 1} tail')
    assert len(objs) == 1
    assert objs[0]["numTotalTests"] == 1


def test_vitest_runner_command_single_package(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With no pnpm-workspace.yaml, vitest runs single-package via npx."""
    from autosprint.test_runners import VitestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    assert VitestRunner().command(quick=False)[1:] == ["vitest", "run", "--reporter=json", "--passWithNoTests"]


def test_vitest_runner_command_monorepo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A pnpm-workspace.yaml switches vitest to the `pnpm -r exec` fan-out, serialised."""
    from autosprint.test_runners import VitestRunner

    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'src/*'\n", encoding="utf-8")
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    assert VitestRunner().command(quick=False)[1:] == ["-r", "--workspace-concurrency=1", "exec", "vitest", "run", "--reporter=json", "--passWithNoTests"]


def test_vitest_runner_interpret_all_pass() -> None:
    """A single all-passing report interprets as ok with the passed count."""
    from autosprint.test_runners import VitestRunner

    assert VitestRunner().interpret(0, json.dumps(_vitest_report(5, 5, 0)), "") == (True, False, 5)


def test_vitest_runner_interpret_monorepo_aggregates() -> None:
    """Multiple per-package reports are summed; any failure makes the run not-ok."""
    from autosprint.test_runners import VitestRunner

    stdout = json.dumps(_vitest_report(40, 40, 0)) + "\n" + json.dumps(_vitest_report(12, 10, 2, ["x", "y"]))
    assert VitestRunner().interpret(1, stdout, "") == (False, False, 50)


def test_vitest_runner_interpret_no_tests() -> None:
    """Zero total tests across all reports interprets as a green, empty run."""
    from autosprint.test_runners import VitestRunner

    assert VitestRunner().interpret(0, json.dumps(_vitest_report(0, 0, 0)), "") == (True, True, None)


def test_vitest_runner_interpret_crash_without_json() -> None:
    """A non-zero exit with no parseable report (vitest crashed) interprets as not-ok."""
    from autosprint.test_runners import VitestRunner

    assert VitestRunner().interpret(1, "Error: Cannot find vitest config", "") == (False, False, None)


def test_vitest_runner_summarise_failure_names_tests() -> None:
    """A failure summary carries the tally header and names the failed tests."""
    from autosprint.test_runners import VitestRunner

    stdout = json.dumps(_vitest_report(3, 1, 2, ["api rejects bad input", "api handles nulls"]))
    summary = VitestRunner().summarise_failure(stdout, "")
    assert "2 failed, 1 passed" in summary
    assert "api rejects bad input" in summary


def test_command_override_applies_to_any_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A configured TEST_COMMAND overrides command construction for both runners."""
    from autosprint.test_runners import PytestRunner, VitestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "TEST_COMMAND", "pnpm run alltests --json")
    assert VitestRunner().command(quick=False)[1:] == ["run", "alltests", "--json"]
    assert PytestRunner().command(quick=True)[1:] == ["run", "alltests", "--json"]


def test_detect_runner_pytest_from_pyproject(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A pyproject.toml marks the target as a pytest project."""
    from autosprint.test_runners import detect_runner

    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    assert detect_runner() == "pytest"


def test_detect_runner_vitest_from_package_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A package.json with no Python markers marks the target as a vitest project."""
    from autosprint.test_runners import detect_runner

    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    assert detect_runner() == "vitest"


def test_detect_runner_python_markers_win_over_package_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When both a pyproject.toml and a package.json are present, pytest wins (the safe default)."""
    from autosprint.test_runners import detect_runner

    (tmp_path / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    assert detect_runner() == "pytest"


def test_get_test_runner_respects_explicit_vitest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An explicit TARGET_TEST_RUNNER=vitest selects the vitest adapter regardless of marker files."""
    from autosprint.test_runners import get_test_runner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "TARGET_TEST_RUNNER", "vitest")
    assert get_test_runner().name == "vitest"


# ---------------------------------------------------------------------------
# VitestRunner typecheck gate (`pre_test_gate`)
# ---------------------------------------------------------------------------


def test_vitest_typecheck_command_is_none_without_tsconfig(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No `tsconfig.json` in the target → the type-check gate is a no-op. Treat type-check as opt-in via the presence of a tsconfig — a vanilla JS repo never gets nagged about types."""
    from autosprint.test_runners import VitestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "TS_TYPECHECK", True)
    assert VitestRunner()._typecheck_command() is None


def test_vitest_typecheck_command_is_none_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`TS_TYPECHECK=False` disables the gate even when tsconfig.json exists."""
    from autosprint.test_runners import VitestRunner

    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "TS_TYPECHECK", False)
    assert VitestRunner()._typecheck_command() is None


def test_vitest_typecheck_command_is_none_when_test_command_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A user-set TEST_COMMAND owns command construction — the type-check gate steps aside so the user's exact invocation isn't second-guessed."""
    from autosprint.test_runners import VitestRunner

    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "TS_TYPECHECK", True)
    monkeypatch.setattr(config, "TEST_COMMAND", "my-custom-test-runner")
    assert VitestRunner()._typecheck_command() is None


def test_vitest_typecheck_defaults_to_npx_tsc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A target with tsconfig.json but no `scripts.typecheck` falls back to `npx --no-install tsc --noEmit`. The `--no-install` flag fails fast if tsc isn't installed rather than silently fetching."""
    from autosprint.test_runners import VitestRunner

    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps({"name": "t", "scripts": {"build": "vite build"}}), encoding="utf-8")
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "TS_TYPECHECK", True)
    cmd = VitestRunner()._typecheck_command()
    assert cmd is not None
    # Last three tokens fix the shape regardless of how `npx` resolved.
    assert cmd[-3:] == ["--no-install", "tsc", "--noEmit"]


def test_vitest_typecheck_prefers_package_json_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When `package.json` defines `scripts.typecheck`, prefer that — the target may have tuned it (multiple tsconfigs, vue-tsc, etc.). Single-package form uses `npm run typecheck --if-present`."""
    from autosprint.test_runners import VitestRunner

    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps({"name": "t", "scripts": {"typecheck": "vue-tsc --noEmit"}}), encoding="utf-8")
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "TS_TYPECHECK", True)
    cmd = VitestRunner()._typecheck_command()
    assert cmd is not None
    assert cmd[-3:] == ["run", "typecheck", "--if-present"]


def test_vitest_typecheck_uses_pnpm_recursive_in_monorepo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A pnpm monorepo (workspace yaml present) fans the typecheck script out across packages with `pnpm -r run typecheck`, matching the same pattern VitestRunner.command uses for tests."""
    from autosprint.test_runners import VitestRunner

    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/*'\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps({"name": "root", "scripts": {"typecheck": "tsc -b"}}), encoding="utf-8")
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "TS_TYPECHECK", True)
    cmd = VitestRunner()._typecheck_command()
    assert cmd is not None
    assert "pnpm" in cmd[0] or cmd[0].endswith("pnpm") or "pnpm" in cmd[0].lower()
    assert "-r" in cmd
    assert cmd[-2:] == ["run", "typecheck"]


def test_vitest_pre_test_gate_passes_through_when_no_typecheck(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When no typecheck command resolves (no tsconfig, disabled, or override), `pre_test_gate` is a clean no-op so vitest runs as normal."""
    from autosprint.test_runners import VitestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "TS_TYPECHECK", True)
    ok, name, _stdout, _stderr = VitestRunner().pre_test_gate(quick=False)
    assert ok is True
    assert name == ""


# ---------------------------------------------------------------------------
# PytestRunner smoke test (`post_test_gate`)
# ---------------------------------------------------------------------------


def test_pytest_smoke_skipped_when_smoke_test_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`SMOKE_TEST=off` disables the smoke gate entirely — even with pyproject + __main__.py present. IMPORT_CHECK is disabled here too so we isolate the smoke-off behaviour from the import-check gate."""
    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "SMOKE_TEST", "off")
    monkeypatch.setattr(config, "IMPORT_CHECK", False)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    (tmp_path / "src" / "x").mkdir(parents=True)
    (tmp_path / "src" / "x" / "__main__.py").write_text("print('hi')", encoding="utf-8")
    ok, name, _, _ = PytestRunner().post_test_gate()
    assert ok is True
    assert name == ""


def test_pytest_smoke_skipped_when_no_pyproject(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No `pyproject.toml` → can't find a package name → skip smoke test silently (no false failures for non-standard layouts)."""
    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "SMOKE_TEST", "auto")
    ok, name, _, _ = PytestRunner().post_test_gate()
    assert ok is True
    assert name == ""


def test_pytest_smoke_skipped_when_no_main_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Library project (pyproject present, but no `__main__.py`) → skip smoke test silently. A library doesn't have a `python -m <pkg>` entrypoint to test. IMPORT_CHECK disabled here to isolate the smoke behaviour from the import-check gate (which would fail because the package isn't actually installed)."""
    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "SMOKE_TEST", "auto")
    monkeypatch.setattr(config, "IMPORT_CHECK", False)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "libonly"\n', encoding="utf-8")
    (tmp_path / "src" / "libonly").mkdir(parents=True)
    (tmp_path / "src" / "libonly" / "__init__.py").write_text("", encoding="utf-8")
    ok, _name, _, _ = PytestRunner().post_test_gate()
    assert ok is True


def test_pytest_detects_package_name_from_pyproject(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`_detect_package_name` reads `[project].name` from pyproject.toml."""
    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "my-game"\nversion = "0.1.0"\n', encoding="utf-8")
    assert PytestRunner()._detect_package_name() == "my-game"


def test_pytest_detect_returns_none_when_no_pyproject(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No pyproject → None (not an error). Smoke test skips gracefully."""
    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    assert PytestRunner()._detect_package_name() is None


def test_pytest_find_main_module_src_layout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """src-layout: `src/<pkg>/__main__.py` is detected."""
    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    (tmp_path / "src" / "thepkg").mkdir(parents=True)
    (tmp_path / "src" / "thepkg" / "__main__.py").write_text("", encoding="utf-8")
    assert PytestRunner()._find_main_module("thepkg") is True


def test_pytest_find_main_module_flat_layout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Flat layout: `<pkg>/__main__.py` at repo root is also detected."""
    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    (tmp_path / "thepkg").mkdir()
    (tmp_path / "thepkg" / "__main__.py").write_text("", encoding="utf-8")
    assert PytestRunner()._find_main_module("thepkg") is True


def test_pytest_find_main_module_returns_false_when_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No `__main__.py` in either layout → False (smoke test skips)."""
    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    (tmp_path / "src" / "thepkg").mkdir(parents=True)
    (tmp_path / "src" / "thepkg" / "__init__.py").write_text("", encoding="utf-8")
    assert PytestRunner()._find_main_module("thepkg") is False


def test_pytest_smoke_env_sets_headless_vars() -> None:
    """`_smoke_env` returns a dict with the canonical headless-mode env vars set so SDL/pygame/pyglet/matplotlib don't open real windows. `DISPLAY` is removed (not set to "") because some libraries treat `DISPLAY=""` as 'use display :0'."""
    from autosprint.test_runners import PytestRunner

    env = PytestRunner()._smoke_env()
    assert env["SDL_VIDEODRIVER"] == "dummy"
    assert env["SDL_AUDIODRIVER"] == "dummy"
    assert env["PYGAME_HIDE_SUPPORT_PROMPT"] == "1"
    assert env["PYGLET_HEADLESS"] == "1"
    assert env["MPLBACKEND"] == "Agg"
    assert "DISPLAY" not in env


def test_run_smoke_with_timeout_passes_on_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A subprocess that exits 0 within the timeout → smoke passes."""
    from unittest.mock import MagicMock

    from autosprint.test_runners import _run_smoke_with_timeout

    mock_proc = MagicMock(returncode=0, stdout="usage: x [--help]", stderr="")
    monkeypatch.setattr("autosprint.test_runners.subprocess.run", MagicMock(return_value=mock_proc))
    ok, name, stdout, _ = _run_smoke_with_timeout(["python", "-m", "x", "--help"], 5, {}, "smoke-test")
    assert ok is True
    assert name == "smoke-test"
    assert "usage" in stdout


def test_run_smoke_with_timeout_fails_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-zero exit → smoke fails. The returned stdout/stderr lets the caller include the crash output in the failure message."""
    from unittest.mock import MagicMock

    from autosprint.test_runners import _run_smoke_with_timeout

    mock_proc = MagicMock(returncode=1, stdout="", stderr="ImportError: No module named missing_dep")
    monkeypatch.setattr("autosprint.test_runners.subprocess.run", MagicMock(return_value=mock_proc))
    ok, _, _, stderr = _run_smoke_with_timeout(["python", "-m", "x", "--help"], 5, {}, "smoke-test")
    assert ok is False
    assert "ImportError" in stderr


# ---------------------------------------------------------------------------
# PytestRunner pre_test_gate (format / lint / collect-only) + import-check
# ---------------------------------------------------------------------------


def test_pytest_pre_test_gate_all_off_is_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When all opt-in gates are off (defaults), pre_test_gate is a clean no-op."""
    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "FORMAT_CHECK", "off")
    monkeypatch.setattr(config, "LINT_CHECK", "off")
    monkeypatch.setattr(config, "PYTEST_COLLECT_GATE", False)
    ok, name, _, _ = PytestRunner().pre_test_gate(quick=False)
    assert ok is True
    assert name == ""


def test_pytest_format_check_fail_short_circuits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A failing format check short-circuits the pre-test gate — the lint and collect-only checks don't run."""
    from unittest.mock import MagicMock

    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "FORMAT_CHECK", "black --check src")
    monkeypatch.setattr(config, "LINT_CHECK", "off")
    monkeypatch.setattr(config, "PYTEST_COLLECT_GATE", False)
    fail_proc = MagicMock(returncode=1, stdout="would reformat foo.py\n", stderr="")
    monkeypatch.setattr("autosprint.test_runners.subprocess.run", MagicMock(return_value=fail_proc))
    ok, name, stdout, _ = PytestRunner().pre_test_gate(quick=False)
    assert ok is False
    assert name == "format-check"
    assert "would reformat" in stdout


def test_pytest_format_check_auto_skips_when_black_not_on_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`FORMAT_CHECK=auto` silently skips when black isn't installed — opt-in mode shouldn't fail just because tooling is missing."""
    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "FORMAT_CHECK", "auto")
    monkeypatch.setattr(config, "LINT_CHECK", "off")
    monkeypatch.setattr(config, "PYTEST_COLLECT_GATE", False)
    monkeypatch.setattr("autosprint.test_runners.shutil.which", lambda name: None)
    ok, name, _, _ = PytestRunner().pre_test_gate(quick=False)
    assert ok is True
    assert name == ""


def test_pytest_lint_auto_prefers_ruff_when_pyproject_has_tool_ruff(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`LINT_CHECK=auto` picks ruff when `[tool.ruff]` is configured in pyproject.toml and ruff is on PATH."""
    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n", encoding="utf-8")
    monkeypatch.setattr("autosprint.test_runners.shutil.which", lambda name: f"/usr/bin/{name}")
    cmd = PytestRunner()._detect_lint_command()
    assert cmd is not None
    assert cmd[-2:] == ["check", "."]
    assert "ruff" in cmd[0]


def test_pytest_lint_auto_falls_back_to_flake8_when_no_ruff_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`LINT_CHECK=auto` picks flake8 when `.flake8` exists and no ruff config is present."""
    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    (tmp_path / ".flake8").write_text("[flake8]\nmax-line-length = 100\n", encoding="utf-8")
    monkeypatch.setattr("autosprint.test_runners.shutil.which", lambda name: f"/usr/bin/{name}" if name == "flake8" else None)
    cmd = PytestRunner()._detect_lint_command()
    assert cmd is not None
    assert "flake8" in cmd[0]


def test_pytest_lint_auto_returns_none_when_nothing_configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Empty target → `_detect_lint_command` returns None → lint gate skips silently."""
    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    assert PytestRunner()._detect_lint_command() is None


def test_pytest_collect_only_gate_passes_on_zero_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A clean `pytest --collect-only` exits 0 → gate passes."""
    from unittest.mock import MagicMock

    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "FORMAT_CHECK", "off")
    monkeypatch.setattr(config, "LINT_CHECK", "off")
    monkeypatch.setattr(config, "PYTEST_COLLECT_GATE", True)
    pass_proc = MagicMock(returncode=0, stdout="5 tests collected", stderr="")
    monkeypatch.setattr("autosprint.test_runners.subprocess.run", MagicMock(return_value=pass_proc))
    ok, _name, _, _ = PytestRunner().pre_test_gate(quick=False)
    assert ok is True


def test_pytest_collect_only_gate_passes_on_exit_5(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Exit 5 (no tests collected) is a valid empty-tree state — gate passes, same as the main test command treats it."""
    from unittest.mock import MagicMock

    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "FORMAT_CHECK", "off")
    monkeypatch.setattr(config, "LINT_CHECK", "off")
    monkeypatch.setattr(config, "PYTEST_COLLECT_GATE", True)
    empty_proc = MagicMock(returncode=5, stdout="no tests ran", stderr="")
    monkeypatch.setattr("autosprint.test_runners.subprocess.run", MagicMock(return_value=empty_proc))
    ok, _name, _, _ = PytestRunner().pre_test_gate(quick=False)
    assert ok is True


def test_pytest_import_check_passes_when_package_imports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A clean `python -c 'import <pkg>'` (exit 0) → import check passes."""
    from unittest.mock import MagicMock

    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    pass_proc = MagicMock(returncode=0, stdout="", stderr="")
    monkeypatch.setattr("autosprint.test_runners.subprocess.run", MagicMock(return_value=pass_proc))
    ok, name, _, _ = PytestRunner()._run_import_check("mygame")
    assert ok is True
    assert name == "import-check"


def test_pytest_import_check_fails_on_importerror(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A failing import (e.g. missing dep) → import check fails with the stderr surfaced."""
    from unittest.mock import MagicMock

    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    fail_proc = MagicMock(returncode=1, stdout="", stderr="ModuleNotFoundError: No module named 'pygame'")
    monkeypatch.setattr("autosprint.test_runners.subprocess.run", MagicMock(return_value=fail_proc))
    ok, _, _, stderr = PytestRunner()._run_import_check("mygame")
    assert ok is False
    assert "ModuleNotFoundError" in stderr


def test_pytest_import_check_normalises_hyphen_to_underscore(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Distribution names like `my-game` (PEP 503) are imported in Python as `my_game`. The check has to do that normalisation or it tries to import the wrong name and gets a syntax error."""
    from unittest.mock import MagicMock

    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("autosprint.test_runners.subprocess.run", fake_run)
    PytestRunner()._run_import_check("my-game")
    # The -c argument should be `import my_game`, not `import my-game`.
    assert "import my_game" in captured["cmd"]


def test_pytest_smoke_literal_command_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A literal `SMOKE_TEST` (not 'auto'/'off') is shell-split and run as-is. Lets users with a non-standard launch (e.g. a wrapper script) plug in their own command. IMPORT_CHECK disabled to isolate the smoke override from the import-check gate."""
    from unittest.mock import MagicMock

    from autosprint.test_runners import PytestRunner

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "SMOKE_TEST", "./scripts/smoke.sh")
    monkeypatch.setattr(config, "SMOKE_TEST_TIMEOUT", 5)
    monkeypatch.setattr(config, "IMPORT_CHECK", False)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("autosprint.test_runners.subprocess.run", fake_run)
    ok, _, _, _ = PytestRunner().post_test_gate()
    assert ok is True
    # The literal command was used, not the auto `python -m <pkg>` form.
    assert captured["cmd"][-1].endswith("smoke.sh") or "smoke.sh" in captured["cmd"][0]
