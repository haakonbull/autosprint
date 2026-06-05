"""Tests for the autosprint init wizard, config.toml apply, and target-repo resolution.

All fast — no LLM calls, no pit_loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import autosprint.init as init_mod
from autosprint.config import config

# ---------------------------------------------------------------------------
# _resolve_target_repo — cwd-first target resolution (terraform-style).
# ---------------------------------------------------------------------------


def test_resolve_target_repo_cwd_beats_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """cwd, when it is a git repo, wins over the TARGET_REPO env fallback."""
    import argparse

    from autosprint.cli import _resolve_target_repo

    monkeypatch.setattr(config, "TARGET_REPO", "/some/env/fallback")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.chdir(repo)
    _resolve_target_repo(argparse.Namespace(target=None))
    assert Path(config.TARGET_REPO).resolve() == repo.resolve()


def test_resolve_target_repo_flag_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An explicit --target path overrides cwd and the env fallback."""
    import argparse

    from autosprint.cli import _resolve_target_repo

    monkeypatch.setattr(config, "TARGET_REPO", "")
    _resolve_target_repo(argparse.Namespace(target="/explicit/target"))
    assert config.TARGET_REPO == "/explicit/target"


def test_resolve_target_repo_falls_back_to_env_when_cwd_not_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When cwd is not a git repo, the TARGET_REPO env fallback is left untouched."""
    import argparse

    from autosprint.cli import _resolve_target_repo

    monkeypatch.setattr(config, "TARGET_REPO", "/env/fallback")
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    _resolve_target_repo(argparse.Namespace(target=None))
    assert config.TARGET_REPO == "/env/fallback"


# ---------------------------------------------------------------------------
# _apply_config_toml — per-repo config.toml overlay; _ensure_config_toml seed.
# ---------------------------------------------------------------------------


def _write_config_toml(tmp_path: Path, body: str) -> None:
    """Write `body` to {tmp_path}/autosprint/config.toml."""
    cfg_dir = tmp_path / "autosprint"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text(body, encoding="utf-8")


def test_apply_config_toml_overlays_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """config.toml values overlay onto config when the field was not set by env."""
    import argparse

    from autosprint.cli import _apply_config_toml

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_TARGET", 8)
    monkeypatch.setattr(config, "IMPLEMENT_AGENT", "implementor_opus48")
    monkeypatch.setattr("autosprint.cli.ENV_SET_FIELDS", frozenset())
    _write_config_toml(tmp_path, 'sp_target = 15\nimplement_agent = "implementor_gpt55"\n')
    _apply_config_toml(argparse.Namespace(command="run", auto_replan=False))
    assert config.SPRINT_STORY_POINT_TARGET == 15
    assert config.IMPLEMENT_AGENT == "implementor_gpt55"


def test_apply_config_toml_env_beats_toml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A field set by env / .env (in ENV_SET_FIELDS) is not overwritten by config.toml."""
    import argparse

    from autosprint.cli import _apply_config_toml

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "SPRINT_STORY_POINT_TARGET", 7)
    monkeypatch.setattr("autosprint.cli.ENV_SET_FIELDS", frozenset({"SPRINT_STORY_POINT_TARGET"}))
    _write_config_toml(tmp_path, "sp_target = 99\n")
    _apply_config_toml(argparse.Namespace(command="run", auto_replan=False))
    assert config.SPRINT_STORY_POINT_TARGET == 7  # env value kept, toml ignored


def test_apply_config_toml_missing_file_is_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No autosprint/config.toml present → silent no-op, no raise."""
    import argparse

    from autosprint.cli import _apply_config_toml

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    _apply_config_toml(argparse.Namespace(command="run", auto_replan=False))


def test_apply_config_toml_mode_specific_team(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The [plan] section's team is used for `autosprint plan`."""
    import argparse

    from autosprint.cli import _apply_config_toml

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "TEAM", "builder")
    monkeypatch.setattr("autosprint.cli.ENV_SET_FIELDS", frozenset())
    _write_config_toml(tmp_path, '[plan]\nteam = "council"\n\n[auto_replan]\nteam = "duo"\n')
    _apply_config_toml(argparse.Namespace(command="plan", auto_replan=False))
    assert config.TEAM == "council"


def test_ensure_config_toml_seeds_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """_ensure_config_toml writes a commented template when config.toml is missing."""
    from autosprint.init import _ensure_config_toml

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    _ensure_config_toml()
    seeded = tmp_path / "autosprint" / "config.toml"
    assert seeded.exists()
    assert "implement_agent" in seeded.read_text(encoding="utf-8")


def test_apply_config_toml_disables_fallback_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """config.toml can set implement_fallback_agent = "" to disable the refusal-fallback."""
    import argparse

    from autosprint.cli import _apply_config_toml

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr(config, "IMPLEMENT_FALLBACK_AGENT", "implementor_gpt55")
    monkeypatch.setattr("autosprint.cli.ENV_SET_FIELDS", frozenset())
    _write_config_toml(tmp_path, 'implement_fallback_agent = ""\n')
    _apply_config_toml(argparse.Namespace(command="run", auto_replan=False))
    assert config.IMPLEMENT_FALLBACK_AGENT == ""


# ---------------------------------------------------------------------------
# _render_config_toml — config.toml text generator (plain template + wizard).
# ---------------------------------------------------------------------------


def test_render_config_toml_empty_is_all_commented_template() -> None:
    """_render_config_toml({}) is valid TOML with zero live keys — a pure template."""
    import tomllib

    text = init_mod._render_config_toml({})
    assert tomllib.loads(text) == {}  # every setting commented out
    assert "implement_agent" in text and "implement_fallback_agent" in text


def test_render_config_toml_writes_active_keys_as_live_settings() -> None:
    """Keys in the active mapping render as live TOML; the rest stay commented."""
    import tomllib

    text = init_mod._render_config_toml({"team": "solo_gpt55", "target_test_runner": "vitest", "implement_fallback_agent": ""})
    data = tomllib.loads(text)
    assert data == {"team": "solo_gpt55", "target_test_runner": "vitest", "implement_fallback_agent": ""}


# ---------------------------------------------------------------------------
# `autosprint init` configuration wizard.
# ---------------------------------------------------------------------------


def _feed_input(monkeypatch: pytest.MonkeyPatch, *answers: str) -> None:
    """Make builtins.input return `answers` in order (then raise EOFError)."""
    queue = iter(answers)

    def fake_input(prompt: str = "") -> str:
        try:
            return next(queue)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr("builtins.input", fake_input)


def test_wizard_assistants_both_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Choosing 'both' keeps every default — nothing is written to config.toml."""
    monkeypatch.setattr(init_mod, "_detect_assistants", lambda: (True, True))
    _feed_input(monkeypatch, "1")
    active: dict[str, str] = {}
    init_mod._wizard_assistants(active)
    assert active == {}


def test_wizard_assistants_claude_only_disables_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude-only resolves to council_opus (6-agent Claude mirror of council), pins implementor + how-far to Opus 4.8, and disables the (Copilot) refusal-fallback."""
    monkeypatch.setattr(init_mod, "_detect_assistants", lambda: (True, False))
    _feed_input(monkeypatch, "2")
    active: dict[str, str] = {}
    init_mod._wizard_assistants(active)
    assert active == {"team": "council_opus", "implement_agent": "implementor_opus48", "implement_fallback_agent": "", "howfar_agent": "howfar_opus48"}


def test_wizard_assistants_copilot_only_sets_gpt_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    """Copilot-only resolves team + implement + how-far agents to the GPT-5.5 keys (council_gpt55 multi-agent team, GPT-5.5 implementor, fallback disabled since primary is already Copilot)."""
    monkeypatch.setattr(init_mod, "_detect_assistants", lambda: (False, True))
    _feed_input(monkeypatch, "3")
    active: dict[str, str] = {}
    init_mod._wizard_assistants(active)
    assert active == {"team": "council_gpt55", "implement_agent": "implementor_gpt55", "implement_fallback_agent": "", "howfar_agent": "howfar_gpt55"}


def test_wizard_assistants_eof_falls_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """An EOF mid-prompt is treated as 'use defaults' — nothing recorded."""
    monkeypatch.setattr(init_mod, "_detect_assistants", lambda: (True, True))
    _feed_input(monkeypatch)  # no answers — input raises EOFError
    active: dict[str, str] = {}
    init_mod._wizard_assistants(active)
    assert active == {}


def test_wizard_language_confirming_detection_records_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Confirming the detected language leaves target_test_runner = auto (unwritten)."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    _feed_input(monkeypatch, "y")
    active: dict[str, str] = {}
    init_mod._wizard_language(active)
    assert active == {}


def test_wizard_language_overriding_detection_records_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Rejecting the detected Python project records the other runner explicitly."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    _feed_input(monkeypatch, "n")
    active: dict[str, str] = {}
    init_mod._wizard_language(active)
    assert active == {"target_test_runner": "vitest"}


def test_wizard_language_empty_repo_asks_outright(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An empty repo (no markers) asks the language; picking vitest records it."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    _feed_input(monkeypatch, "2")
    active: dict[str, str] = {}
    init_mod._wizard_language(active)
    assert active == {"target_test_runner": "vitest"}


def test_wizard_language_empty_repo_python_choice_records_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An empty repo where the user picks Python needs no entry — auto resolves to pytest."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    _feed_input(monkeypatch, "1")
    active: dict[str, str] = {}
    init_mod._wizard_language(active)
    assert active == {}


def test_run_config_wizard_copilot_typescript_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Full wizard: a TypeScript repo on Copilot-only resolves runner + team + agents."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(init_mod, "_detect_assistants", lambda: (False, True))
    _feed_input(monkeypatch, "y", "3")  # confirm TS detection, then Copilot-only
    active = init_mod._run_config_wizard()
    assert active == {"team": "council_gpt55", "implement_agent": "implementor_gpt55", "implement_fallback_agent": "", "howfar_agent": "howfar_gpt55"}


def test_detect_assistants_returns_two_bools() -> None:
    """_detect_assistants probes the machine and returns a (claude, copilot) bool pair."""
    claude, copilot = init_mod._detect_assistants()
    assert isinstance(claude, bool) and isinstance(copilot, bool)


def test_ensure_config_toml_interactive_skips_wizard_without_tty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """interactive=True still writes the plain template when stdin is not a TTY."""
    import tomllib

    from autosprint.init import _ensure_config_toml

    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    _ensure_config_toml(interactive=True)  # pytest stdin is not a TTY → no wizard
    written = (tmp_path / "autosprint" / "config.toml").read_text(encoding="utf-8")
    assert tomllib.loads(written) == {}
