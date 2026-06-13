"""Per-sprint gate inspection.

Reads the live config (and probes the target repo's tooling) to report what
each optional quality gate will actually do this run. Returns plain rows so
both the `autosprint gates` command and the startup banner render the same
truth without either reaching up into the CLI.
"""

import shutil

from autosprint.config import config
from autosprint.infra.test_runners import PytestRunner


def describe_gates() -> list[dict[str, str]]:
    """Inspect the live config and return one row per per-sprint gate: `{name, config_value, status, detail}`. `status` is one of `active` (will fire), `off` (user-disabled), or `auto-skipped` (enabled but missing tooling/config so the gate is a no-op). `detail` carries the why for the skip. Used by both `run_show_gates` (the dedicated subcommand) and the startup banner so the two stay consistent."""
    rows: list[dict[str, str]] = []
    # IMPORT_CHECK — Python-only, auto-skips for non-Python (no pyproject [project].name).
    if config.IMPORT_CHECK:
        try:
            pkg = PytestRunner()._detect_package_name() if config.TARGET_REPO_PATH.exists() else None
            if pkg:
                rows.append({"name": "import-check", "config_value": "true", "status": "active", "detail": f'`python -c "import {pkg.replace("-", "_")}"`'})
            else:
                rows.append({"name": "import-check", "config_value": "true", "status": "auto-skipped", "detail": "no pyproject.toml [project].name in target"})
        except Exception:
            rows.append({"name": "import-check", "config_value": "true", "status": "active", "detail": "(target inspection failed; will retry per-sprint)"})
    else:
        rows.append({"name": "import-check", "config_value": "false", "status": "off", "detail": "IMPORT_CHECK=false"})
    # SMOKE_TEST
    if config.SMOKE_TEST == "off":
        rows.append({"name": "smoke-test", "config_value": "off", "status": "off", "detail": "SMOKE_TEST=off"})
    elif config.SMOKE_TEST != "auto":
        rows.append({"name": "smoke-test", "config_value": config.SMOKE_TEST, "status": "active", "detail": f"literal command: {config.SMOKE_TEST}"})
    else:
        try:
            r = PytestRunner()
            pkg = r._detect_package_name() if config.TARGET_REPO_PATH.exists() else None
            if pkg and r._find_main_module(pkg):
                rows.append({"name": "smoke-test", "config_value": "auto", "status": "active", "detail": f"`python -m {pkg} --help` → spawn-survive fallback"})
            else:
                rows.append({"name": "smoke-test", "config_value": "auto", "status": "auto-skipped", "detail": "no `__main__.py` in target package"})
        except Exception:
            rows.append({"name": "smoke-test", "config_value": "auto", "status": "active", "detail": "(target inspection failed; will retry per-sprint)"})
    # FORMAT_CHECK
    if config.FORMAT_CHECK == "off":
        rows.append({"name": "format-check", "config_value": "off", "status": "off", "detail": "FORMAT_CHECK=off (opt-in)"})
    elif config.FORMAT_CHECK == "auto":
        if shutil.which("black") is not None:
            rows.append({"name": "format-check", "config_value": "auto", "status": "active", "detail": "`black --check src tests`"})
        else:
            rows.append({"name": "format-check", "config_value": "auto", "status": "auto-skipped", "detail": "black not on PATH"})
    else:
        rows.append({"name": "format-check", "config_value": config.FORMAT_CHECK, "status": "active", "detail": f"literal: {config.FORMAT_CHECK}"})
    # LINT_CHECK
    if config.LINT_CHECK == "off":
        rows.append({"name": "lint-check", "config_value": "off", "status": "off", "detail": "LINT_CHECK=off (opt-in)"})
    elif config.LINT_CHECK == "auto":
        try:
            cmd = PytestRunner()._detect_lint_command() if config.TARGET_REPO_PATH.exists() else None
            if cmd:
                rows.append({"name": "lint-check", "config_value": "auto", "status": "active", "detail": " ".join(cmd[-2:])})
            else:
                rows.append({"name": "lint-check", "config_value": "auto", "status": "auto-skipped", "detail": "no ruff/flake8/mypy config detected, or linter not on PATH"})
        except Exception:
            rows.append({"name": "lint-check", "config_value": "auto", "status": "active", "detail": "(target inspection failed; will retry per-sprint)"})
    else:
        rows.append({"name": "lint-check", "config_value": config.LINT_CHECK, "status": "active", "detail": f"literal: {config.LINT_CHECK}"})
    # PYTEST_COLLECT_GATE
    rows.append({"name": "collect-only", "config_value": str(config.PYTEST_COLLECT_GATE).lower(), "status": "active" if config.PYTEST_COLLECT_GATE else "off", "detail": "`pytest --collect-only -q`" if config.PYTEST_COLLECT_GATE else "PYTEST_COLLECT_GATE=false (opt-in)"})
    # COVERAGE_TRACK
    rows.append({"name": "coverage-track", "config_value": str(config.COVERAGE_TRACK).lower(), "status": "active (warn-only)" if config.COVERAGE_TRACK else "off", "detail": "`pytest --cov=<pkg>` → autosprint/logs/coverage-history.log" if config.COVERAGE_TRACK else "COVERAGE_TRACK=false (opt-in)"})
    # TS_TYPECHECK (vitest-only — skip mention when runner is pytest)
    return rows
