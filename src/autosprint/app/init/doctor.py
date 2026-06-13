"""Extracted from the original autosprint.app.init module."""

from __future__ import annotations

import asyncio

from autosprint.app.init.checks import _check_cli_deps_or_abort, _print_init_config_summary, _required_assistants_for_run
from autosprint.config import _project_root, config
from autosprint.util.errors import add_context
from autosprint.util.output import printlev
from autosprint.util.paths import (
    DESTINATION_FILENAME,
)

# Probe agent per backend — used only for the doctor connectivity probe. Claude probes
# Haiku (cheapest). Copilot probes gpt-5.5, the model production teams actually dispatch
# to — gpt-4.1 was retired from Copilot's lineup and probing it blocked runs whose own
# models were fine.
_DOCTOR_PROBE_AGENT_KEY: dict[str, str] = {"claude": "decider_haiku_claude", "copilot": "editor_gpt55"}
# Runtime deps the dispatch layer imports. If any of these are missing, every
# Plan/Implement dispatch will fail mid-sprint — exactly the trap a stale
# `pip install -e .` from before pyproject's deps were declared puts you in.
# (module_import_name, install_name_for_the_hint).
_REQUIRED_RUNTIME_DEPS: tuple[tuple[str, str], ...] = (
    ("claude_agent_sdk", "claude-agent-sdk"),
    ("copilot", "github-copilot-sdk"),
)


def _check_install_health() -> tuple[bool, str]:
    """Spot a stale autosprint install before any sprint touches it. Two specific traps this catches: (1) one or more of the runtime deps the dispatch layer needs (`claude_agent_sdk`, `copilot`) can't be imported — the symptom is "Copilot CLI not found" / similar errors several minutes into the first Plan phase; (2) `importlib.metadata.version("autosprint")` disagrees with the version declared in this checkout's `pyproject.toml`, which means the installed `autosprint.exe` is wired to an older metadata snapshot (typically a leftover `pip install -e .` from before the v0.2.0 dep list landed) — the editable hook still runs new code, but `pip` and pyproject don't agree on the dep list, so other pip activity can wipe out deps without warning. Returns (ok, message) so the caller can render it like any other doctor check."""
    import importlib
    import importlib.metadata
    import tomllib

    missing: list[tuple[str, str]] = []
    for module_name, pkg_name in _REQUIRED_RUNTIME_DEPS:
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append((module_name, pkg_name))

    pyproject_version: str | None = None
    try:
        with (_project_root() / "pyproject.toml").open("rb") as f:
            pyproject_version = tomllib.load(f).get("project", {}).get("version")
    except (OSError, tomllib.TOMLDecodeError):
        pyproject_version = None

    installed_version: str | None = None
    try:
        installed_version = importlib.metadata.version("autosprint")
    except importlib.metadata.PackageNotFoundError:
        installed_version = None

    version_skew = bool(pyproject_version and installed_version and pyproject_version != installed_version)
    if not missing and not version_skew:
        ver_label = f"v{installed_version}" if installed_version else "version unknown"
        return True, f"Install health OK ({ver_label}; all runtime deps importable)"

    problems: list[str] = []
    if missing:
        mods = ", ".join(f"`{m}` (from `{p}`)" for m, p in missing)
        problems.append(f"missing runtime deps: {mods}")
    if version_skew:
        problems.append(f"version skew: installed autosprint=={installed_version} but this checkout's pyproject.toml is {pyproject_version} — the installed metadata is stale")
    autosprint_dir = _project_root()
    hint = f"Stale install detected — {'; '.join(problems)}.\n      Fix: `python -m pip uninstall -y autosprint` (if pip-installed), then `uv tool install --editable {autosprint_dir}`. This gives autosprint its own venv with pyproject.toml deps, so other pip activity can't silently break dispatch."
    return False, hint


async def _doctor_probe(assistant: str) -> tuple[bool, str]:
    """Dispatch one trivial prompt to `assistant`'s cheapest agent to confirm that
    auth + dispatch actually work end to end. Returns (ok, human-readable detail)."""
    from autosprint.infra.dispatch import query_agent
    from autosprint.registry.agents import AGENTS

    agent = AGENTS[_DOCTOR_PROBE_AGENT_KEY[assistant]]
    try:
        reply = await query_agent(agent, "Reply with the single word: OK", skip_cache=True, phase_tag="[doctor]")
        if reply and reply.strip():
            return True, f"{assistant} dispatch — live round-trip OK (probed {agent['model']})"
        return False, f"{assistant} dispatch returned an empty response"
    except Exception as e:
        return False, f"{assistant} dispatch failed — {type(e).__name__}: {e}"


def probe_backends(warn_only: bool = False) -> bool:
    """Live round-trip probe of every AI backend the configured team dispatches to — one cheap call per backend, the same probe `autosprint doctor` uses. Catches breakage that static PATH checks can't see (a Copilot SDK that lost its bundled CLI binary, an expired Claude login, a hit usage cap) *before* a multi-hour run starts limping with half its council dead. `warn_only=True` (init's mode) prints a warning on failure and returns False — init's job is seeding files, and auth may legitimately not be set up yet; the run path raises RuntimeError with a pointer to `autosprint doctor`. Skipped entirely (returns True) in debug/cache modes (LOG_LEVEL <= 15) so dev loops and tests never make live calls."""
    if config.LOG_LEVEL <= 15:
        printlev("[probe] Skipping backend round-trip probe (LOG_LEVEL <= 15 debug/cache mode).", level=20)
        return True
    label = "init" if warn_only else "prepare"
    printlev(f"[{label}] Probing AI backends (one cheap call per backend)...", level=100)
    failures: list[str] = []
    for assistant in sorted(_required_assistants_for_run()):
        if assistant not in _DOCTOR_PROBE_AGENT_KEY:
            continue
        ok, detail = asyncio.run(_doctor_probe(assistant))
        printlev(f"[{label}] {'✅' if ok else '❌'} {detail}", level=100)
        if not ok:
            failures.append(detail)
    if not failures:
        return True
    msg = "Backend probe failed:\n  " + "\n  ".join(failures) + "\n  Fix auth/install before starting a run — `autosprint doctor` prints the full checklist."
    if warn_only:
        printlev(f"[{label}] ⚠ {msg}", level=100)
        return False
    raise RuntimeError(msg)


def run_doctor() -> None:
    """`autosprint doctor` — verify the configured setup can actually run a sprint:
    the target repo, destination.md, the CLI(s) the team needs, and one live
    round-trip per backend in use (Claude / Copilot) confirming auth + dispatch.
    Prints a checklist; exits non-zero (SystemExit) if any hard check fails."""
    from autosprint.reporting.banners import section_banner

    try:
        printlev(f"\n{section_banner('DOCTOR', 'START')}\n", level=100)
        results: list[bool] = []

        def check(ok: bool, msg: str) -> None:
            results.append(ok)
            printlev(f"[doctor] {'✅' if ok else '❌'} {msg}", level=100)

        # 1. Install health — runtime-dep importability + version-skew between installed metadata and this checkout's pyproject.
        # Runs first so a stale install is the first thing the user sees, not the symptom (dispatch errors several minutes in).
        install_ok, install_msg = _check_install_health()
        check(install_ok, install_msg)

        # 2. Target repo.
        target = config.TARGET_REPO_PATH
        if target.resolve() == _project_root().resolve():
            check(False, f"Target repo is the autosprint repo itself ({target}) — cd into your project, or pass --target PATH.")
        elif not target.is_dir():
            check(False, f"Target repo path does not exist: {target}")
        elif not (target / ".git").exists():
            check(False, f"Target repo is not a git repository: {target} — run `git init` there.")
        else:
            check(True, f"Target repo: {target}")

        # 2. destination.md.
        dest = target / DESTINATION_FILENAME
        if not dest.exists():
            check(False, f"{DESTINATION_FILENAME} is missing — run `autosprint init`.")
        else:
            chars = len((dest.read_text(encoding="utf-8") or "").strip())
            check(chars >= 200, f"{DESTINATION_FILENAME} present ({chars} chars)" + ("" if chars >= 200 else " — looks like a placeholder; flesh it out with the /grill-destination skill"))

        # 3. CLI dependencies for the configured team.
        try:
            _check_cli_deps_or_abort()
            check(True, "Required CLI(s) for the configured team are on PATH")
        except RuntimeError as e:
            check(False, str(e).splitlines()[0])

        # 4. Live round-trip — one cheap call per backend the configured team dispatches to.
        for assistant in sorted(_required_assistants_for_run()):
            if assistant not in _DOCTOR_PROBE_AGENT_KEY:
                continue
            ok, detail = asyncio.run(_doctor_probe(assistant))
            check(ok, detail)

        # 5. Resolved config, for the record.
        _print_init_config_summary()

        failures = results.count(False)
        if failures:
            printlev(f"\n[doctor] ❌ {failures} check(s) failed — fix the above before running a sprint.\n{section_banner('DOCTOR', 'END')}", level=100)
            raise SystemExit(1)
        printlev(f"\n[doctor] ✅ All checks passed — the setup is ready to run.\n{section_banner('DOCTOR', 'END')}", level=100)
    except SystemExit:
        raise
    except Exception as e:
        raise add_context(e, "Failed to run autosprint doctor") from e
