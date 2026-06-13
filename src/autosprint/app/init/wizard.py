"""Extracted from the original autosprint.app.init module."""

from __future__ import annotations

import shutil
import sys

from autosprint.config import config
from autosprint.config.toml_io import render_config_toml as _render_config_toml
from autosprint.util.errors import add_context
from autosprint.util.output import printlev
from autosprint.util.paths import (
    AUTOSPRINT_DIR_NAME,
)


def _prompt_yn(question: str, default_yes: bool = True) -> bool | None:
    """Ask a Y/N question. Returns the boolean answer, or None when input is
    unavailable (EOF / Ctrl-C) so the caller can fall back to its own default."""
    suffix = "[Y/n]" if default_yes else "[y/N]"
    try:
        answer = input(f"{question} {suffix} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None
    if answer == "":
        return default_yes
    return answer in ("y", "yes")


def _prompt_choice(question: str, options: list[tuple[str, str]], default_key: str) -> str | None:
    """Ask a numbered multiple-choice question. `options` is a list of (key, label);
    returns the chosen key, the default on a blank or unrecognised answer, or None
    when input is unavailable (EOF / Ctrl-C)."""
    printlev(question, level=100)
    keys = [key for key, _ in options]
    for i, (key, label) in enumerate(options, 1):
        marker = "  (default)" if key == default_key else ""
        printlev(f"   {i}) {label}{marker}", level=100)
    try:
        raw = input(f"Choose 1-{len(options)} [{keys.index(default_key) + 1}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if raw == "":
        return default_key
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return keys[int(raw) - 1]
    printlev(f"[init] Unrecognised choice '{raw}' — using the default ({default_key}).", level=100)
    return default_key


def _detect_assistants() -> tuple[bool, bool]:
    """Best-effort probe of which AI backends this machine can dispatch to:
    Claude (the `claude` CLI on PATH) and Copilot (the `copilot` SDK package
    importable). Importable ≠ authenticated — `autosprint doctor` does the live
    round-trip — but it is a good-enough signal to pre-select the wizard default."""
    import importlib.util

    claude = shutil.which("claude") is not None
    copilot = importlib.util.find_spec("copilot") is not None
    return claude, copilot


def _wizard_language(active: dict[str, str]) -> None:
    """Wizard step 1 — target-repo language / test runner. A repo that already has
    marker files gets its detected language shown for confirmation; an empty repo is
    asked outright. `target_test_runner` is recorded only when the answer differs
    from what `TARGET_TEST_RUNNER=auto` would resolve to anyway — so confirming the
    detection writes nothing (auto already does the right thing)."""
    from autosprint.infra.test_runners import detect_runner

    root = config.TARGET_REPO_PATH
    has_markers = any((root / m).exists() for m in ("pyproject.toml", "pytest.ini", "setup.cfg", "package.json"))
    auto_choice = detect_runner()
    if has_markers:
        label = "Python / pytest" if auto_choice == "pytest" else "TypeScript-JavaScript / vitest"
        ok = _prompt_yn(f"[init] Detected target language: {label}. Correct?", default_yes=True)
        chosen = auto_choice if (ok is None or ok) else ("vitest" if auto_choice == "pytest" else "pytest")
    else:
        choice = _prompt_choice(
            "[init] No language marker files found yet. What will the target repo be?",
            [("pytest", "Python (pytest)"), ("vitest", "TypeScript / JavaScript (vitest)")],
            "pytest",
        )
        chosen = choice or "pytest"
    if chosen != auto_choice:
        active["target_test_runner"] = chosen
        printlev(f'[init] → target_test_runner = "{chosen}"', level=100)
    else:
        printlev(f"[init] → target_test_runner stays auto (detection resolves to {chosen}).", level=100)


def _wizard_assistants(active: dict[str, str]) -> None:
    """Wizard step 2 — which AI backend(s) autosprint will use. Resolves the answer
    into the matching planning team / implement agent / refusal-fallback / how-far
    agent. Only keys that deviate from the both-backends defaults are recorded; a
    Claude-only answer notably disables the (Copilot) refusal-fallback so `doctor`
    won't then demand Copilot auth."""
    claude, copilot = _detect_assistants()
    printlev(f"[init] Backend detection: Claude CLI {'✓' if claude else '✗'}, Copilot SDK {'✓' if copilot else '✗'}", level=100)
    if claude and not copilot:
        default_key = "claude"
    elif copilot and not claude:
        default_key = "copilot"
    else:
        default_key = "both"
    choice = _prompt_choice(
        "[init] Which AI backend(s) will autosprint use? (sets the planning team + implement agent)",
        [
            ("both", "Both Claude and Copilot — council team (mixed), Opus implementor"),
            ("claude", "Claude only — council_opus team (6-agent Claude), Opus implementor, no Copilot fallback"),
            ("copilot", "Copilot only — council_gpt55 team (6-agent Copilot), GPT-5.5 implementor"),
        ],
        default_key,
    )
    if choice is None or choice == "both":
        printlev("[init] → using the default both-backend team (council).", level=100)
        return
    if choice == "claude":
        active["team"] = "council_opus"
        active["implement_agent"] = "implementor_opus48"  # explicit so a future change to the default doesn't leak Copilot into a Claude-only setup
        active["implement_fallback_agent"] = ""  # default fallback is Copilot — disable it for a Claude-only setup
        active["howfar_agent"] = "howfar_opus48"
        printlev("[init] → team = council_opus (6-agent Claude), implementor + how-far on Opus 4.8, refusal-fallback disabled (Claude-only).", level=100)
    elif choice == "copilot":
        active["team"] = "council_gpt55"
        active["implement_agent"] = "implementor_gpt55"
        active["implement_fallback_agent"] = ""  # default fallback is Copilot — already same backend, no point
        active["howfar_agent"] = "howfar_gpt55"
        printlev("[init] → team = council_gpt55 (6-agent Copilot), implementor + how-far on GPT-5.5 (Copilot-only).", level=100)


def _wizard_auto_gates(active: dict[str, str]) -> None:
    """Wizard step 3 — opt into auto-detected per-sprint gates (format-check,
    lint-check, coverage-track). Y writes `format_check="auto"`, `lint_check="auto"`,
    and `coverage_track=true` to config.toml so the corresponding gates kick in
    on every sprint. N leaves the safe defaults in place (gates off). The
    always-on gates (import-check, smoke-test) are not touched by this question."""
    ok = _prompt_yn(
        "[init] Enable auto-detected gates (format-check, lint-check, coverage-track)?",
        default_yes=True,
    )
    if ok is None or not ok:
        printlev("[init] → auto-detected gates left off (smoke-test and import-check still active).", level=100)
        return
    active["format_check"] = "auto"
    active["lint_check"] = "auto"
    active["coverage_track"] = "true"
    printlev("[init] → format_check=auto, lint_check=auto, coverage_track=true (gates skip silently if their tools aren't installed).", level=100)


def _run_config_wizard() -> dict[str, str]:
    """Run the interactive `autosprint init` wizard and return the config.toml keys
    that deviate from defaults. Three questions: target language, AI backend(s), and
    auto-gates. The caller guarantees an interactive TTY; each prompt still tolerates EOF."""
    printlev("\n[init] Configuring autosprint for this repo — press Enter to accept each default.", level=100)
    active: dict[str, str] = {}
    _wizard_language(active)
    _wizard_assistants(active)
    _wizard_auto_gates(active)
    return active


def _ensure_config_toml(interactive: bool = False) -> None:
    """Create autosprint/config.toml in the target repo if missing. With
    `interactive=True` and a real TTY, a short poetry-init-style wizard asks for the
    target language and AI backend(s) and writes the answers as live settings;
    otherwise a fully-commented template is written. Committed, like destination.md
    / adr.md. Idempotent — an existing config.toml is never touched, so the wizard
    runs at most once per repo (delete the file to re-run it)."""
    try:
        path = config.TARGET_REPO_PATH / AUTOSPRINT_DIR_NAME / "config.toml"
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        active: dict[str, str] = {}
        if interactive and sys.stdin.isatty():
            active = _run_config_wizard()
        path.write_text(_render_config_toml(active), encoding="utf-8")
        if active:
            printlev(f"[init] Created {AUTOSPRINT_DIR_NAME}/config.toml from your answers ({', '.join(sorted(active))}).", level=100)
        else:
            printlev(f"[prepare] Created {AUTOSPRINT_DIR_NAME}/config.toml template in {config.TARGET_REPO_PATH}.", level=50)
    except Exception as e:
        raise add_context(e, f"Failed to create config.toml in {config.TARGET_REPO_PATH}") from e
