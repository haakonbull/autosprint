"""Tests for the CLI parser: flag shortcuts, council family sanity, TerseArgumentParser.

All fast — no LLM calls, no pit_loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autosprint.config import config

# ---------------------------------------------------------------------------
# --claude-only / --copilot-only boolean shortcuts
# ---------------------------------------------------------------------------


def test_claude_only_flag_expands_to_council_opus(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--claude-only sets team + implementor to the all-Claude pair, same as --preset claude-only."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "autosprint").mkdir()
    (tmp_path / "autosprint" / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["autosprint", "show-config", "--claude-only"])
    from autosprint.cli import parse_cli_args

    parse_cli_args()
    assert config.TEAM == "council_opus"
    assert config.IMPLEMENT_AGENT == "implementor_opus48"


def test_copilot_only_flag_expands_to_council_gpt55(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--copilot-only sets team + implementor to the all-Copilot pair."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "autosprint").mkdir()
    (tmp_path / "autosprint" / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["autosprint", "show-config", "--copilot-only"])
    from autosprint.cli import parse_cli_args

    parse_cli_args()
    assert config.TEAM == "council_gpt55"
    assert config.IMPLEMENT_AGENT == "implementor_gpt55"


def test_claude_only_and_copilot_only_are_mutually_exclusive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Setting both at once is a user error — raise rather than silently picking one."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "autosprint").mkdir()
    (tmp_path / "autosprint" / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["autosprint", "show-config", "--claude-only", "--copilot-only"])
    from autosprint.cli import parse_cli_args

    with pytest.raises(SystemExit) as exc_info:
        parse_cli_args()
    assert "mutually exclusive" in str(exc_info.value)


def test_explicit_team_wins_over_claude_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An explicit --team value overrides the --claude-only sugar — user is being specific."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "autosprint").mkdir()
    (tmp_path / "autosprint" / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["autosprint", "show-config", "--claude-only", "--team", "power"])
    from autosprint.cli import parse_cli_args

    parse_cli_args()
    assert config.TEAM == "power"


# ---------------------------------------------------------------------------
# Council-family sanity — backend purity per team
# ---------------------------------------------------------------------------


def test_council_gpt55_is_all_copilot() -> None:
    """council_gpt55 must be 100% Copilot — every planner + the team lead. Drift here breaks the Copilot-only preset's promise."""
    from autosprint.teams import TEAMS

    team = TEAMS["council_gpt55"]
    for agent in team["agents"]:
        assert agent["assistant"] == "copilot", f"council_gpt55 planner {agent['name']} is not Copilot: {agent['assistant']}"
    assert team["selector"]["assistant"] == "copilot", f"council_gpt55 selector is not Copilot: {team['selector']['name']}"


def test_council_opus_is_all_claude() -> None:
    """council_opus must be 100% Claude — every planner + the team lead. Drift here breaks the Claude-only preset's promise."""
    from autosprint.teams import TEAMS

    team = TEAMS["council_opus"]
    for agent in team["agents"]:
        assert agent["assistant"] == "claude", f"council_opus planner {agent['name']} is not Claude: {agent['assistant']}"
    assert team["selector"]["assistant"] == "claude", f"council_opus selector is not Claude: {team['selector']['name']}"


def test_council_default_is_mixed() -> None:
    """The default `council` team is deliberately mixed (3 Claude + 3 Copilot); a drift toward all-one-backend would lose the cost-sharing rationale."""
    from autosprint.teams import TEAMS

    team = TEAMS["council"]
    backends = {agent["assistant"] for agent in team["agents"]}
    assert backends == {"claude", "copilot"}, f"council should be mixed, got backends: {backends}"


def test_council_family_has_same_six_lenses() -> None:
    """All three council variants must have the same six role names (North Star, Bug Hunter, Pragmatist, Tester, Minimalist, Architect) — that's the family-resemblance contract. Backend suffix in the display name is ignored."""
    from autosprint.teams import TEAMS

    def lens(name: str) -> str:
        # Strip a trailing backend-tag like " (Opus 4.8)" / " (GPT-5.5)".
        return name.split("(")[0].strip()

    expected_lenses = {"The North Star", "The Bug Hunter", "The Pragmatist", "The Tester", "The Minimalist", "The Architect"}
    for key in ("council", "council_gpt55", "council_opus"):
        lenses = {lens(agent["name"]) for agent in TEAMS[key]["agents"]}
        assert lenses == expected_lenses, f"team {key} lenses {lenses} != expected {expected_lenses}"


# ---------------------------------------------------------------------------
# _TerseArgumentParser — typo-friendly subcommand errors
# ---------------------------------------------------------------------------


def test_terse_argparser_handles_subcommand_typo(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A typo'd subcommand should yield a compact 'did you mean?' message, not the giant usage banner."""
    from autosprint.cli import parse_cli_args

    monkeypatch.setattr("sys.argv", ["autosprint", "clear-logss"])
    with pytest.raises(SystemExit) as exc_info:
        parse_cli_args()
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "unknown subcommand 'clear-logss'" in err
    # Hint may include other close matches too (e.g. "logs") — we only require clear-logs is suggested.
    assert "Did you mean:" in err
    assert "clear-logs" in err
    assert "Valid subcommands:" in err
    # The verbose default-argparse usage banner should NOT appear for this case.
    assert "usage: autosprint" not in err


def test_terse_argparser_suggests_known_token_when_extra_verb_typed(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """`autosprint list teams` is natural English but `list` isn't a valid subcommand. The handler scans the other tokens and finds `teams` (which IS valid) rather than guessing via difflib."""
    from autosprint.cli import parse_cli_args

    monkeypatch.setattr("sys.argv", ["autosprint", "list", "teams"])
    with pytest.raises(SystemExit):
        parse_cli_args()
    err = capsys.readouterr().err
    assert "unknown subcommand 'list'" in err
    assert "Did you mean: teams?" in err


def test_terse_argparser_passes_through_other_errors(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Non-subcommand errors (e.g. unknown flags) keep the default argparse output so the user gets full usage context."""
    from autosprint.cli import parse_cli_args

    monkeypatch.setattr("sys.argv", ["autosprint", "run", "--definitely-not-a-flag"])
    with pytest.raises(SystemExit):
        parse_cli_args()
    err = capsys.readouterr().err
    # Default argparse output includes the usage banner.
    assert "usage:" in err
    assert "unrecognized arguments" in err
