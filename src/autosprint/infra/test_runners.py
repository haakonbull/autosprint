"""Test-runner adapters — pluggable per-language test execution for the Test phase.

The PIT loop's Test phase is the one language-specific phase: it must invoke the
target repo's real test runner and read a real pass/fail result, because that
deterministic gate is what makes progress monotonic. This module isolates that
knowledge behind a `TestRunner` adapter so the phase code (`test_phase.py`) stays
language-blind — it drives a runner and consumes a normalized `TestResult`, never
a runner's raw output format.

`PytestRunner` and `VitestRunner` ship today. Adding another runner (`go test`,
`cargo test`, ...) is a new `TestRunner` subclass plus a branch in `get_test_runner`
— with no change to `run_test_phase` or any other phase.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from abc import ABC, abstractmethod
from dataclasses import dataclass

from autosprint.config import config
from autosprint.util.errors import add_context


@dataclass
class TestResult:
    """Normalized outcome of one test run — runner-agnostic, the only shape the Test phase sees.

    `ok` is the gate verdict the Test phase acts on: True means green (tests passed, or the repo
    legitimately has no tests). `no_tests` distinguishes the empty-suite case so the phase can log it
    distinctly. `passed` is the passed-test count, or None when the runner's output didn't yield one.
    `failure_summary` is a short human-readable digest, populated only when `ok` is False.
    """

    # The `Test*` name prefix makes pytest try to collect this as a test class — it is not one.
    # No type annotation, so `@dataclass` does not treat this as a field.
    __test__ = False

    ok: bool
    no_tests: bool
    passed: int | None
    returncode: int
    stdout: str
    stderr: str
    elapsed: float
    failure_summary: str = ""


class TestRunner(ABC):
    """A per-language test-runner adapter.

    A runner knows how to build its invocation command, execute it in the target repo, and interpret
    the raw subprocess result into a `TestResult`. It also owns its runner-specific output formatters
    (`summarise_failure`, `highlights`). Everything language-specific about testing lives in a
    subclass; nothing leaks into the Test phase. `run()` is concrete — subclasses customise behaviour
    only via the abstract methods.
    """

    # The `Test*` name prefix makes pytest try to collect this as a test class — it is not one.
    __test__ = False

    #: Short runner identifier, surfaced in phase log lines (e.g. "pytest").
    name: str = "?"

    @abstractmethod
    def command(self, quick: bool, terse: bool = False) -> list[str]:
        """Return the subprocess argv. `quick` restricts to the fast subset; `terse` requests compact output (used by the planner's pre-flight survey)."""

    @abstractmethod
    def interpret(self, returncode: int, stdout: str, stderr: str) -> tuple[bool, bool, int | None]:
        """Interpret a finished run. Returns `(ok, no_tests, passed_count)` — `ok` is the gate verdict, `no_tests` flags an empty suite, `passed_count` is the passed-test count or None."""

    @abstractmethod
    def summarise_failure(self, stdout: str, stderr: str = "") -> str:
        """Return a short human-readable digest of a failing run, for logs and the revert message."""

    @abstractmethod
    def highlights(self, stdout: str, stderr: str = "") -> str:
        """Return a compact slice of a run's output (summary line + warnings) for the next sprint's team-lead prompt."""

    def pre_test_gate(self, quick: bool, terse: bool = False) -> tuple[bool, str, str, str]:
        """Optional gate that must pass *before* the runner's test command executes. Returns `(ok, name, stdout, stderr)` — `ok` False short-circuits `run()` with a TestResult flagged failed. Default implementation is a no-op (always ok). Subclasses override to add language-specific gates that the test runner itself doesn't cover — e.g. TypeScript's `tsc --noEmit` since `vitest` strips types without checking them, so a vitest-only gate would let real type errors ship green."""
        return True, "", "", ""

    def post_test_gate(self) -> tuple[bool, str, str, str]:
        """Optional gate that runs *after* the test command passes — catches things tests miss. Returns `(ok, name, stdout, stderr)` — `ok` False causes `run()` to return a failed TestResult so the sprint reverts. Default is a no-op (always ok). PytestRunner overrides with a smoke test: spawn `python -m <package>` to verify the app actually starts. Pytest mocks GUI/network/main-loop in most projects, so a green test suite + broken `__main__.py` (ImportError, missing dep, wiring bug) can silently commit. The smoke test catches that."""
        return True, "", "", ""

    def run(self, quick: bool, terse: bool = False) -> TestResult:
        """Execute the runner in the target repo and return a normalized `TestResult`. Concrete: runs the optional pre-test gate (e.g. TypeScript's `tsc --noEmit`); on gate failure, short-circuits with a failed TestResult tagged with the gate's name in `failure_summary`. Then builds the test command, runs the subprocess, interprets it. If the tests pass, runs the optional post-test gate (e.g. the Python smoke test that spawns `python -m <pkg>` to verify the app starts). Any of the three gates failing returns a failed TestResult so the sprint reverts."""
        start = time.monotonic()
        gate_ok, gate_name, gate_stdout, gate_stderr = self.pre_test_gate(quick, terse)
        if not gate_ok:
            elapsed = time.monotonic() - start
            summary = f"{gate_name} failed before tests ran:\n{(gate_stdout + gate_stderr).strip()[:1500]}"
            return TestResult(ok=False, no_tests=False, passed=None, returncode=1, stdout=gate_stdout, stderr=gate_stderr, elapsed=elapsed, failure_summary=summary)
        proc = subprocess.run(self.command(quick, terse), capture_output=True, text=True, cwd=config.TARGET_REPO_PATH)
        elapsed = time.monotonic() - start
        ok, no_tests, passed = self.interpret(proc.returncode, proc.stdout, proc.stderr)
        if not ok:
            failure_summary = self.summarise_failure(proc.stdout, proc.stderr)
            return TestResult(ok=False, no_tests=no_tests, passed=passed, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr, elapsed=elapsed, failure_summary=failure_summary)
        post_ok, post_name, post_stdout, post_stderr = self.post_test_gate()
        if not post_ok:
            elapsed = time.monotonic() - start
            summary = f"{post_name} failed after tests passed:\n{(post_stdout + post_stderr).strip()[:1500]}"
            return TestResult(ok=False, no_tests=no_tests, passed=passed, returncode=1, stdout=proc.stdout + "\n" + post_stdout, stderr=proc.stderr + "\n" + post_stderr, elapsed=elapsed, failure_summary=summary)
        return TestResult(ok=True, no_tests=no_tests, passed=passed, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr, elapsed=elapsed, failure_summary="")


def _resolve_exe(name: str) -> str:
    """Resolve an executable name to a concrete path so `subprocess` finds it without a shell — critical on Windows where `npx` / `pnpm` are `.cmd` shims that a bare-name, shell-less `subprocess.run` cannot locate. Falls back to the bare name when `which` finds nothing (let subprocess raise a clear error)."""
    return shutil.which(name) or name


def _command_override() -> list[str] | None:
    """Return the user's `TEST_COMMAND` as an argv list (shell-word-split, first token resolved on PATH), or None when unset. Lets a target with a non-standard invocation override a runner's default command while keeping that runner's output parser."""
    raw = config.TEST_COMMAND.strip()
    if not raw:
        return None
    parts = shlex.split(raw)
    if parts:
        parts[0] = _resolve_exe(parts[0])
    return parts


def count_passed_pytest(stdout: str) -> int | None:
    """Return the number of tests pytest reports as passed, or None if the summary line is missing."""
    match = re.search(r"(\d+)\s+passed", stdout)
    return int(match.group(1)) if match else None


class PytestRunner(TestRunner):
    """pytest adapter — the default and, today, only target-repo test runner.

    pytest's relevant exit codes: 0 = all passed, 5 = no tests collected (a valid state for a repo
    without tests yet), anything else = failure or collection error.
    """

    name = "pytest"

    def python_executable(self) -> str:
        """Return the Python interpreter to run the target repo's tests with. Prefers TARGET_REPO/.venv (Windows `Scripts/python.exe` or Unix `bin/python`) so pytest sees deps the Implement agent installed mid-sprint via `uv add` — those land in the target's venv, not autosprint's. Falls back to `sys.executable` when the target has no .venv."""
        try:
            target_venv = config.TARGET_REPO_PATH / ".venv"
            candidates = [target_venv / "Scripts" / "python.exe", target_venv / "bin" / "python"]
            for candidate in candidates:
                if candidate.exists():
                    return str(candidate)
            return sys.executable
        except Exception as e:
            raise add_context(e, "Failed to resolve target Python executable") from e

    def command(self, quick: bool, terse: bool = False) -> list[str]:
        """Build the pytest argv. `terse` adds `--tb=line -q` (compact pre-flight output); `quick` adds `-m "not slow"` to skip slow-marked tests. Uses the target repo's venv Python so mid-sprint deps are visible. A configured `TEST_COMMAND` overrides all of this."""
        override = _command_override()
        if override is not None:
            return override
        cmd = [self.python_executable(), "-m", "pytest"]
        if terse:
            cmd += ["--tb=line", "-q"]
        if quick:
            cmd += ["-m", "not slow"]
        return cmd

    def pre_test_gate(self, quick: bool, terse: bool = False) -> tuple[bool, str, str, str]:
        """Pre-test gates for Python targets, in order: format check (FORMAT_CHECK), lint check (LINT_CHECK), collect-only (PYTEST_COLLECT_GATE). Each is opt-in via config; default off. First failure short-circuits the sprint with that gate's name surfaced in the failure summary. Order is by speed: format/lint are sub-second; collect-only is faster than a real test run but slower than the static checks."""
        if config.FORMAT_CHECK != "off":
            ok, name, out, err = self._run_format_check()
            if not ok:
                return False, name, out, err
        if config.LINT_CHECK != "off":
            ok, name, out, err = self._run_lint_check()
            if not ok:
                return False, name, out, err
        if config.PYTEST_COLLECT_GATE:
            ok, name, out, err = self._run_collect_only()
            if not ok:
                return False, name, out, err
        return True, "", "", ""

    def _run_format_check(self) -> tuple[bool, str, str, str]:
        """Run `black --check` (or a user-configured equivalent). `auto` runs `black --check src tests` when black is on PATH; silently skips if black isn't installed (treat as ok). A literal value is shell-word-split. Fails the gate if the formatter reports unformatted files."""
        if config.FORMAT_CHECK == "auto":
            black_exe = shutil.which("black")
            if black_exe is None:
                return True, "", "", ""  # nothing to check against
            cmd = [black_exe, "--check", "src", "tests"]
        else:
            cmd = shlex.split(config.FORMAT_CHECK)
            if cmd:
                cmd[0] = _resolve_exe(cmd[0])
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=config.TARGET_REPO_PATH, timeout=60)
        except subprocess.TimeoutExpired:
            return False, "format-check", "", "format check timed out after 60s"
        if proc.returncode == 0:
            return True, "format-check", proc.stdout, proc.stderr
        return False, "format-check", proc.stdout, proc.stderr

    def _run_lint_check(self) -> tuple[bool, str, str, str]:
        """Run a lint check. `auto` detects from target-repo markers: ruff (pyproject `[tool.ruff]`) > flake8 (`.flake8` or `setup.cfg [flake8]`) > mypy (pyproject `[tool.mypy]` or `mypy.ini`). A literal value is shell-word-split. Skips silently when auto detects nothing — opt-in mode means user expects *something*, but missing tooling shouldn't break the sprint."""
        if config.LINT_CHECK == "auto":
            cmd = self._detect_lint_command()
            if cmd is None:
                return True, "", "", ""  # nothing detected
        else:
            cmd = shlex.split(config.LINT_CHECK)
            if cmd:
                cmd[0] = _resolve_exe(cmd[0])
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=config.TARGET_REPO_PATH, timeout=120)
        except subprocess.TimeoutExpired:
            return False, "lint-check", "", "lint check timed out after 120s"
        if proc.returncode == 0:
            return True, "lint-check", proc.stdout, proc.stderr
        return False, "lint-check", proc.stdout, proc.stderr

    def _detect_lint_command(self) -> list[str] | None:
        """Pick a lint command based on target-repo config-file presence. Returns None when nothing is configured. Ruff wins over flake8 wins over mypy because that's the practical order projects adopt."""
        root = config.TARGET_REPO_PATH
        pyproject = root / "pyproject.toml"
        if pyproject.exists():
            try:
                text = pyproject.read_text(encoding="utf-8")
                if "[tool.ruff]" in text or "[tool.ruff." in text:
                    ruff = shutil.which("ruff")
                    if ruff is not None:
                        return [ruff, "check", "."]
            except OSError:
                pass
        if (root / ".flake8").exists() or _setup_cfg_has_section(root, "flake8"):
            flake8 = shutil.which("flake8")
            if flake8 is not None:
                return [flake8, "."]
        if (root / "mypy.ini").exists() or _pyproject_has_tool(root, "mypy"):
            mypy = shutil.which("mypy")
            if mypy is not None:
                return [mypy, "."]
        return None

    def _run_collect_only(self) -> tuple[bool, str, str, str]:
        """Run `pytest --collect-only -q` as a pre-test gate. Verifies tests collect cleanly (no import errors in conftest.py or test modules, no syntax errors) before the real test run. Faster than a full pytest invocation and gives a cleaner failure when collection breaks. Treats exit 5 (no tests collected) as ok — same as the main test command."""
        cmd = [self.python_executable(), "-m", "pytest", "--collect-only", "-q"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=config.TARGET_REPO_PATH, timeout=60)
        except subprocess.TimeoutExpired:
            return False, "collect-only", "", "pytest --collect-only timed out after 60s"
        if proc.returncode in (0, 5):
            return True, "collect-only", proc.stdout, proc.stderr
        return False, "collect-only", proc.stdout, proc.stderr

    def interpret(self, returncode: int, stdout: str, stderr: str) -> tuple[bool, bool, int | None]:
        """Map pytest's exit code to `(ok, no_tests, passed_count)`. Exit 5 (no tests collected) counts as a green, empty run; exit 0 is a pass; anything else is a failure."""
        if returncode == 5:
            return True, True, None
        if returncode != 0:
            return False, False, None
        return True, False, count_passed_pytest(stdout)

    def summarise_failure(self, stdout: str, stderr: str = "") -> str:
        """Returns a short summary of a pytest failure: FAILED/ERROR lines from stdout + a leading 'E   ' exception line (ModuleNotFoundError / AssertionError / etc. — the actual cause pytest prefixes with 'E   ') + the final '=' summary line, plus any stderr content. Collection errors (pytest exit 2/3) are terse in the summary line but the 'E   ' traceback line carries the real cause; keep both. Falls back to the stdout tail if no structured lines are found."""
        lines = stdout.splitlines()
        failed = [line for line in lines if line.startswith(("FAILED", "ERROR"))]
        exception_lines = [line for line in lines if line.lstrip().startswith("E   ")]
        summary = next((line for line in reversed(lines) if line.startswith("=")), "")
        parts = failed + exception_lines[:5] + ([summary] if summary else [])
        result = "\n".join(parts) if parts else stdout[-400:]
        stderr_tail = stderr.strip()
        if stderr_tail:
            result += f"\n--- stderr ---\n{stderr_tail[-600:]}"
        return result

    def highlights(self, stdout: str, stderr: str = "") -> str:
        """Return a compact slice of pytest output: the 'warnings summary' section (if present) + the final '=' summary line + stderr tail. Successful runs with no warnings produce just the summary line; runs with warnings include the full warnings section so the team lead sees exactly which warnings fired and where. Keeps the team-lead prompt informative without dragging in hundreds of lines of test-collection noise."""
        lines = stdout.splitlines()
        kept: list[str] = []
        in_warnings_section = False
        for line in lines:
            stripped = line.strip()
            if "warnings summary" in stripped.lower() and stripped.startswith("="):
                in_warnings_section = True
                kept.append(line)
                continue
            if in_warnings_section:
                # Warnings section ends at the next '=' banner (usually the final summary).
                if stripped.startswith("=") and "warnings summary" not in stripped.lower():
                    in_warnings_section = False
                kept.append(line)
                continue
        summary_line = next((line for line in reversed(lines) if line.startswith("=")), "")
        if summary_line and (not kept or kept[-1] != summary_line):
            kept.append(summary_line)
        result = "\n".join(kept) if kept else (summary_line or stdout[-400:])
        stderr_tail = stderr.strip()
        if stderr_tail:
            result += f"\n--- stderr ---\n{stderr_tail[-400:]}"
        return result

    def _detect_package_name(self) -> str | None:
        """Read `pyproject.toml` to find the target's package name. Returns None when there's no pyproject, no `[project]` table, or no `name` key — the smoke test then silently no-ops since we can't construct `python -m <pkg>` without it."""
        try:
            pyproject = config.TARGET_REPO_PATH / "pyproject.toml"
            if not pyproject.exists():
                return None
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            name = data.get("project", {}).get("name")
            return str(name) if name else None
        except (OSError, tomllib.TOMLDecodeError, KeyError):
            return None

    def _find_main_module(self, package: str) -> bool:
        """Return True if the target repo has a `__main__.py` in either `src/<pkg>/` or `<pkg>/` layout — the precondition for `python -m <pkg>` to work."""
        candidates = [config.TARGET_REPO_PATH / "src" / package / "__main__.py", config.TARGET_REPO_PATH / package / "__main__.py"]
        return any(p.exists() for p in candidates)

    def _smoke_env(self) -> dict[str, str]:
        """Headless-mode environment for the smoke test — prevents GUI windows from opening for pygame/SDL/X11/pyglet/matplotlib apps so the smoke test stays AFK-friendly. Builds on the parent process env so the target's regular env (PATH, PYTHONPATH, venv vars) is preserved."""
        env = dict(os.environ)
        env["SDL_VIDEODRIVER"] = "dummy"
        env["SDL_AUDIODRIVER"] = "dummy"
        env["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
        env["PYGLET_HEADLESS"] = "1"
        env["MPLBACKEND"] = "Agg"
        env.pop("DISPLAY", None)
        return env

    def post_test_gate(self) -> tuple[bool, str, str, str]:
        """Post-test gates for Python targets, in order: import check (`python -c 'import <pkg>'`, IMPORT_CHECK), `python -m <pkg>` smoke (SMOKE_TEST), coverage tracking (COVERAGE_TRACK, warn-only). Import check runs first because it's the cheapest and a prereq for the -m smoke. Coverage runs last and is informational — currently logs a warning on regression but doesn't fail the sprint."""
        package = self._detect_package_name()
        if config.IMPORT_CHECK and package:
            ok, name, out, err = self._run_import_check(package)
            if not ok:
                return False, name, out, err
        smoke_ok, smoke_name, smoke_out, smoke_err = self._run_smoke_test(package)
        if not smoke_ok:
            return False, smoke_name, smoke_out, smoke_err
        if config.COVERAGE_TRACK and package:
            self._track_coverage(package)  # warn-only; never fails the gate
        return True, "", "", ""

    def _run_import_check(self, package: str) -> tuple[bool, str, str, str]:
        """Run `python -c "import <package>"` with headless env vars. Catches package-level `ImportError`, top-level exceptions in `__init__.py`, missing deps that mocking in tests hid. Treats the underscore/hyphen ambiguity: pyproject.toml names like `my-game` become Python imports as `my_game`. Cheap (~50ms typical), valuable, works for library projects too."""
        # PEP 503 normalisation: distribution name 'my-game' is imported as 'my_game'.
        module = package.replace("-", "_")
        cmd = [self.python_executable(), "-c", f"import {module}"]
        env = self._smoke_env()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=config.TARGET_REPO_PATH, env=env, timeout=10)
        except subprocess.TimeoutExpired:
            return False, "import-check", "", "import check timed out after 10s"
        if proc.returncode == 0:
            return True, "import-check", proc.stdout, proc.stderr
        return False, "import-check", proc.stdout, proc.stderr

    def _run_smoke_test(self, package: str | None) -> tuple[bool, str, str, str]:
        """The `python -m <pkg>` smoke test, factored out so `post_test_gate` can run the import-check before it. See class-level `SMOKE_TEST` config description for the auto/off/literal semantics. Returns (True, "", "", "") for the skip cases (off, no package, no __main__.py)."""
        if config.SMOKE_TEST == "off":
            return True, "", "", ""

        env = self._smoke_env()
        timeout = max(1, config.SMOKE_TEST_TIMEOUT)

        if config.SMOKE_TEST != "auto":
            cmd = shlex.split(config.SMOKE_TEST)
            if cmd:
                cmd[0] = _resolve_exe(cmd[0])
            return _run_smoke_with_timeout(cmd, timeout, env, "smoke-test")

        if not package:
            return True, "", "", ""
        if not self._find_main_module(package):
            return True, "", "", ""

        help_cmd = [self.python_executable(), "-m", package, "--help"]
        ok, name, out, err = _run_smoke_with_timeout(help_cmd, timeout, env, "smoke-test")
        if ok:
            return ok, name, out, err

        bare_cmd = [self.python_executable(), "-m", package]
        return _run_smoke_survive(bare_cmd, 3.0, env, "smoke-test")

    def _track_coverage(self, package: str) -> None:
        """Run pytest with `--cov` and append the coverage % to `autosprint/logs/coverage-history.log`. Warn-only — never fails the gate; the log is the audit trail and a future v2 will gate on regression. Silent-skips when pytest-cov isn't installed (no warning needed; user explicitly enabled this and is responsible for the dep)."""
        from autosprint.util.output import printlev
        from autosprint.util.paths import LOGS_SUBDIR

        module = package.replace("-", "_")
        cmd = [self.python_executable(), "-m", "pytest", f"--cov={module}", "--cov-report=term-missing", "-q"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=config.TARGET_REPO_PATH, timeout=300)
        except subprocess.TimeoutExpired:
            return
        match = re.search(r"^TOTAL\s+\d+\s+\d+\s+(\d+)%", proc.stdout, re.MULTILINE)
        if not match:
            return
        pct = int(match.group(1))
        log_path = config.TARGET_REPO_PATH / LOGS_SUBDIR / "coverage-history.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = log_path.read_text(encoding="utf-8").strip().splitlines() if log_path.exists() else []
        except OSError:
            existing = []
        last_pct: int | None = None
        for line in reversed(existing):
            parts = line.rsplit(" ", 1)
            if len(parts) == 2 and parts[1].endswith("%"):
                try:
                    last_pct = int(parts[1].rstrip("%"))
                    break
                except ValueError:
                    continue
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{timestamp} {pct}%\n")
        if last_pct is not None and pct < last_pct:
            printlev(f"[T] ⚠ Coverage dropped: {last_pct}% → {pct}%. Warn-only; sprint proceeds. See autosprint/logs/coverage-history.log.", level=100)


def _setup_cfg_has_section(root: object, section: str) -> bool:
    """Check whether `setup.cfg` in the target repo has a given INI section (e.g. `[flake8]`). Returns False on missing file or read errors."""
    setup_cfg = root / "setup.cfg"
    if not setup_cfg.exists():
        return False
    try:
        return f"[{section}]" in setup_cfg.read_text(encoding="utf-8")
    except OSError:
        return False


def _pyproject_has_tool(root: object, tool: str) -> bool:
    """Check whether `pyproject.toml` has a `[tool.<tool>]` table — used by lint auto-detection to pick mypy when its config lives in pyproject. Returns False on missing file or read errors."""
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return False
    try:
        text = pyproject.read_text(encoding="utf-8")
        return f"[tool.{tool}]" in text or f"[tool.{tool}." in text
    except OSError:
        return False


def _run_smoke_with_timeout(cmd: list[str], timeout: int, env: dict[str, str], name: str) -> tuple[bool, str, str, str]:
    """Run the smoke command with a fixed timeout. Exit 0 within the window → pass; any other exit code → fail; timeout → fail (a fast `--help` call shouldn't time out). Used for the `--help` form and for user-overridden literal commands."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=config.TARGET_REPO_PATH, env=env, timeout=timeout)
        if proc.returncode == 0:
            return True, name, proc.stdout, proc.stderr
        return False, name, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        return False, name, out, f"{err}\n(smoke command timed out after {timeout}s)"


def _run_smoke_survive(cmd: list[str], timeout: float, env: dict[str, str], name: str) -> tuple[bool, str, str, str]:
    """Spawn the smoke command and check it survives `timeout` seconds without crashing. Used as the fallback when the `--help` form returns non-zero — for apps that open a window and idle (no `--help` support, just GUI). If the process exits within the window with a non-zero code, that's a crash → fail. If it exits within the window with code 0, treat as pass (CLI that printed-and-exited fast). If it survives past `timeout`, kill it and treat as pass (GUI didn't crash on launch)."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=config.TARGET_REPO_PATH, env=env, text=True)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        if proc.returncode == 0:
            return True, name, stdout, stderr
        return False, name, stdout, stderr
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            stdout, stderr = proc.communicate(timeout=2.0)
        except subprocess.TimeoutExpired:
            stdout = stderr = ""
        return True, name, stdout, f"{stderr}\n(smoke survived {timeout}s — app did not crash on launch)"


def _extract_json_objects(text: str) -> list[dict]:
    """Find and parse every top-level JSON object embedded in `text`. Resilient to surrounding noise (log prefixes, banners, interleaved console output) because it scans for balanced `{...}` brace spans rather than assuming the whole stream is one JSON document — needed because a pnpm `-r exec` fan-out emits one vitest JSON report per workspace package, each possibly prefixed with the package name. String literals are tracked so braces inside JSON string values never skew the depth count."""
    objects: list[dict] = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    obj = json.loads(text[start : i + 1])
                    if isinstance(obj, dict):
                        objects.append(obj)
                except json.JSONDecodeError:
                    pass
                start = -1
    return objects


class VitestRunner(TestRunner):
    """vitest adapter — runs a JS/TS target repo's vitest suite and reads its JSON reporter.

    Two modes, chosen by the presence of `pnpm-workspace.yaml` at the target root:

    - **Single package** — `npx vitest run --reporter=json --passWithNoTests`.
    - **pnpm monorepo** — `pnpm -r --workspace-concurrency=1 exec vitest run --reporter=json
      --passWithNoTests`. Using `pnpm exec` (not `pnpm -r test`) deliberately bypasses each
      package's own `test` script — those are unreliable as a gate (a package may define
      `"test": "vitest"`, which is *watch mode* and would hang the loop forever). The fan-out
      emits one JSON report per package; `--workspace-concurrency=1` serialises them so the
      reports do not interleave, and `interpret` sums them.

    A configured `TEST_COMMAND` overrides command construction entirely; the JSON parser still
    applies, so the custom command must still emit vitest JSON. `quick` / `terse` are accepted
    for interface compatibility but ignored — vitest has no pytest-style slow-marker split.
    """

    name = "vitest"

    def _typecheck_command(self) -> list[str] | None:
        """Build the type-check argv for the target repo. Prefers a `package.json` `scripts.typecheck` entry when one exists — that lets the target tune the check (e.g. multiple tsconfig files, alternative checkers like `tsc -b`, `vue-tsc`) without autosprint guessing. Falls back to `tsc --noEmit`. Returns None when type-check is disabled (`config.TS_TYPECHECK` False), the target has no `tsconfig.json` (treating the type-check as opt-in to projects with a tsconfig), or a `TEST_COMMAND` override is in effect (the user owns command construction in that case)."""
        if not config.TS_TYPECHECK:
            return None
        if _command_override() is not None:
            return None
        if not (config.TARGET_REPO_PATH / "tsconfig.json").exists():
            return None
        pkg = config.TARGET_REPO_PATH / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8"))
                scripts = data.get("scripts") or {}
                if "typecheck" in scripts:
                    if (config.TARGET_REPO_PATH / "pnpm-workspace.yaml").exists():
                        return [_resolve_exe("pnpm"), "-r", "--workspace-concurrency=1", "run", "typecheck"]
                    return [_resolve_exe("npm"), "run", "typecheck", "--if-present"]
            except (json.JSONDecodeError, OSError):
                pass  # malformed package.json — fall through to tsc default
        return [_resolve_exe("npx"), "--no-install", "tsc", "--noEmit"]

    def pre_test_gate(self, quick: bool, terse: bool = False) -> tuple[bool, str, str, str]:
        """Run the TypeScript type-check gate before vitest. Vitest strips types without checking them (it transpiles via esbuild), so a real type error would otherwise ship green. Returns `(ok, "typecheck", stdout, stderr)`. No-op (always ok) when type-check is disabled, the target has no tsconfig, or a TEST_COMMAND override is in effect — see `_typecheck_command`."""
        cmd = self._typecheck_command()
        if cmd is None:
            return True, "", "", ""
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=config.TARGET_REPO_PATH)
        return proc.returncode == 0, "typecheck", proc.stdout, proc.stderr

    def command(self, quick: bool, terse: bool = False) -> list[str]:
        """Build the vitest argv. A configured `TEST_COMMAND` wins; otherwise pick single-package vs. pnpm-monorepo form from the target repo's layout. `quick` / `terse` are ignored (vitest has no quick/slow split)."""
        override = _command_override()
        if override is not None:
            return override
        base = ["vitest", "run", "--reporter=json", "--passWithNoTests"]
        if (config.TARGET_REPO_PATH / "pnpm-workspace.yaml").exists():
            return [_resolve_exe("pnpm"), "-r", "--workspace-concurrency=1", "exec", *base]
        return [_resolve_exe("npx"), *base]

    def _reports(self, stdout: str) -> list[dict]:
        """Return the vitest JSON reports found in stdout — every parsed object carrying a `numTotalTests` key (the per-package reports), discarding stray non-report objects a test's own console output might emit."""
        return [o for o in _extract_json_objects(stdout) if "numTotalTests" in o]

    def interpret(self, returncode: int, stdout: str, stderr: str) -> tuple[bool, bool, int | None]:
        """Sum the per-package vitest reports into `(ok, no_tests, passed)`. With no parseable report, a zero exit is treated as an empty green run and a non-zero exit as a failure (vitest crashed before reporting)."""
        reports = self._reports(stdout)
        if not reports:
            ok = returncode == 0
            return ok, ok, None
        total = sum(int(r.get("numTotalTests", 0)) for r in reports)
        failed = sum(int(r.get("numFailedTests", 0)) for r in reports)
        passed = sum(int(r.get("numPassedTests", 0)) for r in reports)
        if total == 0:
            return True, True, None
        return failed == 0, False, passed

    def summarise_failure(self, stdout: str, stderr: str = "") -> str:
        """Summarise a failing vitest run: a `<n> failed, <n> passed` header plus up to 15 `FAILED <test> — <first message line>` rows pulled from the JSON reports. When no report parsed (vitest crashed before running), fall back to the stdout/stderr tail."""
        reports = self._reports(stdout)
        if not reports:
            tail = stdout[-600:]
            if stderr.strip():
                tail += f"\n--- stderr ---\n{stderr.strip()[-600:]}"
            return tail.strip() or "vitest produced no parseable JSON report."
        rows: list[str] = []
        for r in reports:
            for suite in r.get("testResults", []):
                for a in suite.get("assertionResults", []):
                    if a.get("status") == "failed":
                        name = a.get("fullName") or a.get("title") or "?"
                        messages = a.get("failureMessages") or []
                        detail = ""
                        if messages:
                            detail = " — " + messages[0].splitlines()[0].strip()[:200]
                        rows.append(f"FAILED {name}{detail}")
        failed = sum(int(r.get("numFailedTests", 0)) for r in reports)
        passed = sum(int(r.get("numPassedTests", 0)) for r in reports)
        header = f"{failed} failed, {passed} passed"
        body = "\n".join(rows[:15])
        return f"{header}\n{body}" if body else header

    def highlights(self, stdout: str, stderr: str = "") -> str:
        """Return a one-line tallied summary across all per-package reports, for the next sprint's team-lead prompt. Falls back to the stdout tail when nothing parsed."""
        reports = self._reports(stdout)
        if not reports:
            return stdout[-300:].strip()
        total = sum(int(r.get("numTotalTests", 0)) for r in reports)
        failed = sum(int(r.get("numFailedTests", 0)) for r in reports)
        passed = sum(int(r.get("numPassedTests", 0)) for r in reports)
        return f"vitest: {passed}/{total} passed, {failed} failed across {len(reports)} package(s)."


def detect_runner() -> str:
    """Detect the target repo's test runner from its marker files. Python markers (`pyproject.toml` / `pytest.ini` / `setup.cfg`) win over `package.json` so a Python project with an incidental `package.json` (e.g. a bundled JS frontend) still resolves to pytest — the long-standing default. A `package.json` with no Python markers resolves to vitest. Nothing recognised falls back to pytest."""
    root = config.TARGET_REPO_PATH
    if any((root / marker).exists() for marker in ("pyproject.toml", "pytest.ini", "setup.cfg")):
        return "pytest"
    if (root / "package.json").exists():
        return "vitest"
    return "pytest"


def get_test_runner() -> TestRunner:
    """Resolve the `TestRunner` for the current target repo from `config.TARGET_TEST_RUNNER` — an explicit `pytest` / `vitest`, or `auto` (the default) which defers to `detect_runner`. Callers only ever see the `TestRunner` interface, so the Test phase is unaffected by which runner is chosen."""
    choice = config.TARGET_TEST_RUNNER
    if choice == "auto":
        choice = detect_runner()
    if choice == "pytest":
        return PytestRunner()
    if choice == "vitest":
        return VitestRunner()
    raise RuntimeError(f"Unknown test runner '{choice}' — TARGET_TEST_RUNNER must be auto, pytest, or vitest.")
