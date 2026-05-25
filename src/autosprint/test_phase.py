"""Test phase: run the target repo's test suite, summarise output, persist last result.

This module owns:
- The Test step of the PIT loop (`run_test_phase`).
- Pre-flight tests used by the planner (`run_preflight_tests`).
- The initial-test gate run before sprint 1 (`check_initial_tests`), and
  the module-level `_INITIAL_TESTS_SUMMARY` it captures so plan-phase can
  reuse the result without re-running. Read via `get_initial_tests_summary()`.
- Last-test-output persistence (`write_last_test_output`, `read_last_test_output`).
- Self-test helpers used by `autosprint self-test`: `run_self_test`, `run_black_check`.

The actual test-runner invocation lives behind a `TestRunner` adapter in
`test_runners.py` — this module is language-blind: it drives a runner and acts on
the normalized `TestResult` it returns. The `summarise_pytest_failure` /
`count_passed_tests` / `extract_test_output_highlights` / `target_python_executable`
/ `pytest_cmd` names are kept as thin pytest-pinned shims for backwards
compatibility (orchestrator.py re-exports them and tests import the aliases).
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone

from autosprint.config import config, _project_root
from autosprint.errors import PhaseFailedError, RevertReason, add_context
from autosprint.git_ops import git_restore
from autosprint.output import printlev
from autosprint.paths import LAST_TEST_OUTPUT_FILENAME, PREFLIGHT_LOG_FILENAME
from autosprint.plan import group_titles
from autosprint.test_runners import PytestRunner, count_passed_pytest, get_test_runner

_INITIAL_TESTS_SUMMARY: str | None = None


def get_initial_tests_summary() -> str | None:
    """Returns the summary captured by the most recent `check_initial_tests` call, or None if it hasn't been called or returned no usable summary. Plan phase reads this on sprint 1 to inline the initial-tests result into the team-lead prompt without paying for a re-run."""
    return _INITIAL_TESTS_SUMMARY


# ---------------------------------------------------------------------------
# Backwards-compatibility shims. These were module-level functions before the
# test-runner adapter extraction; orchestrator.py re-exports them under legacy
# `_`-prefixed names and tests import those. They delegate to a pytest adapter,
# preserving the pre-refactor behaviour exactly. New code should resolve a
# runner via `get_test_runner()` and use the `TestRunner` interface instead.
# ---------------------------------------------------------------------------

_pytest_runner = PytestRunner()


def summarise_pytest_failure(stdout: str, stderr: str = "") -> str:
    """Shim — see `PytestRunner.summarise_failure`."""
    return _pytest_runner.summarise_failure(stdout, stderr)


def count_passed_tests(stdout: str) -> int | None:
    """Shim — number of tests pytest reports as passed, or None if the summary line is missing."""
    return count_passed_pytest(stdout)


def extract_test_output_highlights(stdout: str, stderr: str = "") -> str:
    """Shim — see `PytestRunner.highlights`."""
    return _pytest_runner.highlights(stdout, stderr)


def target_python_executable() -> str:
    """Shim — see `PytestRunner.python_executable`."""
    return _pytest_runner.python_executable()


def pytest_cmd(quick_only: bool) -> list[str]:
    """Shim — the pytest argv for a full or quick run; see `PytestRunner.command`."""
    return _pytest_runner.command(quick=quick_only)


def write_last_test_output(sprint_number: int, outcome: str, stdout: str, stderr: str = "") -> None:
    """Persist a compact summary of the just-completed Test phase to autosprint/logs/last-test-output.log so the next sprint's team lead can see pass count, warnings, and any failure context. Overwrites each sprint — the log is always 'last sprint only', never a running history. Header includes the sprint number so post-hoc reviewers can correlate with sprint-outcomes.log. No-op in FAKE_IMPLEMENT mode to keep debug runs quiet."""
    if config.FAKE_IMPLEMENT:
        return
    try:
        log_path = config.TARGET_REPO_PATH / LAST_TEST_OUTPUT_FILENAME
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        highlights = extract_test_output_highlights(stdout, stderr)
        log_path.write_text(f"# Last test-phase output | sprint={sprint_number} | {ts} | outcome={outcome}\n\n{highlights}\n", encoding="utf-8")
    except Exception as e:
        raise add_context(e, f"Failed to write last-test-output log ({outcome})") from e


def read_last_test_output() -> str:
    """Return the contents of autosprint/last-test-output.log, always surfacing failure output and surfacing passing-run warnings when present. A failing log is where the planner needs context — what broke, which tests, which file. For passing runs, only the warnings-summary section is surfaced (already terse; growing warning counts are otherwise invisible until they cause a failure). Returns empty when the file is missing (sprint 1 before any Test phase, `clear-logs` wiped it, FAKE_IMPLEMENT mode). Truncates to the last ~60 lines so a noisy failure can't blow up the team-lead prompt."""
    try:
        log_path = config.TARGET_REPO_PATH / LAST_TEST_OUTPUT_FILENAME
        if not log_path.exists():
            return ""
        text = log_path.read_text(encoding="utf-8")
        first_line = text.split("\n", 1)[0]
        passing = "outcome=FAIL" not in first_line
        lines = text.splitlines()
        if passing:
            # For passing runs, only include the warnings/summary highlights — skip
            # the full stdout to keep the planner prompt lean.
            highlights = extract_test_output_highlights(text)
            if not highlights.strip():
                return ""
            return f"# Last test-phase output (previous sprint PASSED — warnings only)\n{highlights}"
        if len(lines) > 60:
            lines = ["[...truncated — oldest lines dropped...]"] + lines[-60:]
        return "\n".join(lines)
    except Exception as e:
        raise add_context(e, "Failed to read last-test-output log") from e


def run_test_phase(task_group: list[dict], sprint_number: int) -> None:
    """Run the Test phase: execute the target repo's test runner (full suite by default, or quick-only subset when config.TEST_PHASE_QUICK_ONLY is True), revert + raise if tests fail, else print the pass count. A failing test phase reverts the whole group atomically. `sprint_number` is passed explicitly (not read from a context var) for loggability."""
    # Lazy import: append_run_log lives in run_log.py which imports test_phase
    # for output formatters — lazy avoids the cycle at module-load time. While
    from autosprint.run_log import append_run_log

    try:
        runner = get_test_runner()
        quick_only = config.TEST_PHASE_QUICK_ONLY
        scope = f"quick subset ({runner.name})" if quick_only else "full suite (all tests)"
        printlev(f"\n[T] 🧪 Entering Test phase — {scope}...")
        result = runner.run(quick=quick_only)
        if result.no_tests:
            write_last_test_output(sprint_number, "PASS (no tests collected)", "")
            printlev("[T] ✅ No tests collected — treating as pass.")
            return
        if not result.ok:
            write_last_test_output(sprint_number, "FAIL", result.stdout, result.stderr)
            printlev(f"[T] ❌ Tests failed. Reverting.\n{result.failure_summary}", level=100)
            git_restore()
            for t in task_group:
                append_run_log(sprint_number, t["title"], "OK", "FAILED", "REVERTED", revert_reason=RevertReason.TEST_FAILURE.value)
            raise PhaseFailedError(f"Tests failed: {result.failure_summary}", RevertReason.TEST_FAILURE)
        write_last_test_output(sprint_number, "PASS", result.stdout)
        printlev(f"[T] ✅ All {result.passed} tests passed." if result.passed is not None else "[T] ✅ All tests passed.", level=100)
    except PhaseFailedError:
        raise
    except Exception as e:
        raise add_context(e, f"Failed to run Test phase for task group '{group_titles(task_group)}'") from e


def check_initial_tests() -> None:
    """Run the target repo's tests once before the PIT loop; scope controlled by config.INITIAL_TESTS ('quick' | 'all' | 'none'). After tests pass, also runs the runner's post-test gate (import-check + smoke-test for pytest, no-op for vitest) so a baseline state that imports broken or won't launch is caught before sprint 1 — otherwise the loop will keep reverting to a broken master forever. Captures a compact summary into the module-level _INITIAL_TESTS_SUMMARY so sprint 1's plan phase can reuse it without re-running. On failure, raises — the user fixes the repo before any LLM tokens burn."""
    global _INITIAL_TESTS_SUMMARY
    try:
        scope = config.INITIAL_TESTS
        if scope == "none":
            printlev("[prepare] Skipping initial tests (INITIAL_TESTS=none).", level=100)
            _INITIAL_TESTS_SUMMARY = None
            return
        runner = get_test_runner()
        quick_only = scope == "quick"
        label = f"quick subset ({runner.name})" if quick_only else "full suite"
        printlev(f"[prepare] Running initial tests — {label}...")
        result = runner.run(quick=quick_only)
        if result.no_tests:
            printlev("[prepare] ✅ No tests collected — treating as pass.")
            _INITIAL_TESTS_SUMMARY = f"Initial tests ({scope} scope): no tests collected."
            _check_initial_post_test_gate(runner)
            return
        if not result.ok:
            printlev(f"\n[prepare] ❌ Initial tests are failing in {config.TARGET_REPO_PATH} ({scope} scope):\n{result.failure_summary}", level=100)
            raise RuntimeError(f"Initial tests failing in TARGET_REPO ({scope} scope). Fix the failing tests before re-running autosprint.")
        passed = result.passed or 0
        _INITIAL_TESTS_SUMMARY = f"Initial tests ({scope} scope): {passed} passed."
        printlev(f"[prepare] ✅ All {passed} tests passed ({scope} scope).")
        _check_initial_post_test_gate(runner)
        printlev("[prepare] Starting PIT loop.\n")
    except RuntimeError:
        raise
    except Exception as e:
        raise add_context(e, "Failed initial test check") from e


def _check_initial_post_test_gate(runner) -> None:
    """Run the runner's post-test gate (import-check + smoke for pytest) as part of the pre-flight check. A failure raises RuntimeError so the user fixes baseline before any LLM tokens burn. Vitest's base implementation is a no-op, so this is a free skip for TS/JS projects."""
    ok, gate_name, gate_out, gate_err = runner.post_test_gate()
    if ok:
        return
    detail_blocks = []
    if gate_out.strip():
        detail_blocks.append(gate_out.strip())
    if gate_err.strip():
        detail_blocks.append(f"--- stderr ---\n{gate_err.strip()}")
    detail = "\n".join(detail_blocks) if detail_blocks else "(no output)"
    printlev(f"\n[prepare] ❌ Initial post-test gate '{gate_name}' failed in {config.TARGET_REPO_PATH}:\n{detail}", level=100)
    raise RuntimeError(f"Initial {gate_name} gate failed in TARGET_REPO. Fix the baseline before re-running autosprint (or disable the gate in autosprint/config.toml).")


def run_preflight_tests() -> str:
    """Run a quick, terse test survey in the target repo. Used as the Plan phase's pre-flight context when the previous sprint was reverted. Writes the full raw output to autosprint/logs/preflight-tests.log; returns a compact summary string suitable for inlining into the team-lead prompt. Pure subprocess — no LLM call."""
    try:
        printlev("[P] Running pre-flight tests (parallel to team)...", level=20)
        runner = get_test_runner()
        result = runner.run(quick=True, terse=True)
        stderr_block = f"\n--- stderr ---\n{result.stderr}" if result.stderr.strip() else ""
        full_output = f"# Pre-flight test run at {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n# exit={result.returncode} elapsed={result.elapsed:.1f}s\n\n{result.stdout}{stderr_block}"
        log_path = config.TARGET_REPO_PATH / PREFLIGHT_LOG_FILENAME
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(full_output, encoding="utf-8")
        if result.no_tests:
            summary = "Pre-flight: no tests collected."
        elif result.ok:
            passed = result.passed or 0
            summary = f"Pre-flight: all {passed} quick tests passed — baseline is green."
        else:
            summary = f"Pre-flight FAILED (exit={result.returncode}).\n{result.failure_summary}"
        printlev(f"[P] Pre-flight done in {result.elapsed:.1f}s: {summary.splitlines()[0][:120]}", level=20)
        return summary
    except Exception as e:
        raise add_context(e, "Failed to run pre-flight tests") from e


def run_black_check() -> None:
    """Verify src/ and tests/ are black-formatted at the project's 1000-char line width. Runs `black --check` in report-only mode; a non-zero exit means drift and the self-test aborts. Silently skipped when the `black` module isn't installed so a stripped-down dev env can still run self-test."""
    try:
        try:
            import black  # noqa: F401
        except ImportError:
            printlev("[prepare] Skipping black --check — `black` not installed.", level=20)
            return
        result = subprocess.run(
            [sys.executable, "-m", "black", "--check", "--line-length", "1000", "src", "tests"],
            capture_output=True,
            text=True,
            cwd=str(_project_root()),
        )
        if result.returncode != 0:
            raise RuntimeError(f"black --check failed — code is not formatted. Run `black --line-length 1000 src tests` to fix.\n{result.stdout}\n{result.stderr}")
    except RuntimeError:
        raise
    except Exception as e:
        raise add_context(e, "Failed to run black --check") from e


def run_self_test() -> None:
    """Run autosprint's own test suite + black formatting check to verify the orchestrator is healthy. Excludes the `live` marker (requires API credentials) and the `slow` marker (multi-second stress tests) — self-test is meant to be fast and self-contained. black runs first so formatting drift is caught before the pytest output drowns it out."""
    try:
        printlev("[prepare] Running black --check on src/ and tests/...")
        run_black_check()
        printlev("[prepare] Running autosprint self-test (pytest)...")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-m", "not live and not slow"],
            capture_output=True,
            text=True,
            cwd=str(_project_root()),
        )
        if result.returncode != 0:
            raise RuntimeError(f"Autosprint self-test failed. Fix autosprint's own tests before running a PIT session.\n{result.stdout}\n{result.stderr}")
        printlev("[prepare] ✅ Self-test passed.\n")
    except RuntimeError:
        raise
    except Exception as e:
        raise add_context(e, "Failed to run autosprint self-test") from e
