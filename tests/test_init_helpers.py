"""Tests for init seeders + gitignore + claude assets copy.

All fast — no LLM calls, no pit_loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import autosprint.init as init_mod
from autosprint.config import config
from autosprint.init import _copy_claude_assets_to_target, _ensure_adr_stub, _ensure_destination_or_abort, _ensure_gitignore_entries
from autosprint.paths import ADR_FILENAME, DESTINATION_FILENAME

# ---------------------------------------------------------------------------
# _ensure_adr_stub
# ---------------------------------------------------------------------------


def test_ensure_adr_stub_creates_file_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    _ensure_adr_stub()
    adr = tmp_path / ADR_FILENAME
    assert adr.exists()
    text = adr.read_text(encoding="utf-8")
    assert "Architecture Decision Records" in text
    assert "Supersedes" in text


def test_ensure_adr_stub_does_not_overwrite_existing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    adr = tmp_path / ADR_FILENAME
    adr.parent.mkdir(parents=True, exist_ok=True)
    adr.write_text("my custom adr\n", encoding="utf-8")
    _ensure_adr_stub()
    assert adr.read_text(encoding="utf-8") == "my custom adr\n"


# ---------------------------------------------------------------------------
# _ensure_destination_or_abort
# ---------------------------------------------------------------------------


def test_ensure_destination_creates_seed_and_aborts_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When destination.md is missing, init seeds `destination.example.md` (NOT destination.md) and aborts, telling the user to rename or write their own."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    with pytest.raises(RuntimeError, match="destination"):
        _ensure_destination_or_abort()
    # destination.md itself is NOT created — the user must decide whether to rename or start fresh.
    assert not (tmp_path / DESTINATION_FILENAME).exists()
    # The demo example was placed next to it for the user to rename if they want.
    example_path = (tmp_path / DESTINATION_FILENAME).parent / "destination.example.md"
    assert example_path.exists()
    assert example_path.read_text(encoding="utf-8").strip()  # non-empty seed


def test_ensure_destination_passes_when_file_has_content(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    path = tmp_path / DESTINATION_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# My project\n\nReal content here.\n", encoding="utf-8")
    _ensure_destination_or_abort()  # must not raise


def test_ensure_destination_aborts_when_file_is_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    path = tmp_path / DESTINATION_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("   \n\n   \n", encoding="utf-8")  # whitespace only
    with pytest.raises(RuntimeError, match="destination"):
        _ensure_destination_or_abort()


# ---------------------------------------------------------------------------
# _ensure_gitignore_entries — adds entries idempotently
# ---------------------------------------------------------------------------


def test_ensure_gitignore_creates_file_with_required_entries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    _ensure_gitignore_entries()
    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "autosprint/logs/*" in text
    assert "!autosprint/logs/sprint-outcomes.log" in text
    assert "!autosprint/logs/plan-decisions.md" in text
    assert "!autosprint/logs/runtime-stats.md" in text
    assert "autosprint/cache/" in text
    assert "autosprint/stop" in text


def test_ensure_gitignore_seeds_python_defaults_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    _ensure_gitignore_entries()
    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    # Python defaults seeded alongside the autosprint block.
    assert "__pycache__/" in text
    assert ".venv/" in text
    assert ".pytest_cache/" in text
    assert ".ruff_cache/" in text
    assert ".vscode/" in text
    # And the autosprint-specific block is still appended.
    assert "autosprint/logs/*" in text


def test_ensure_gitignore_does_not_seed_defaults_when_file_exists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    gi = tmp_path / ".gitignore"
    gi.write_text("# user content\nfoo/\n", encoding="utf-8")
    _ensure_gitignore_entries()
    text = gi.read_text(encoding="utf-8")
    # Existing file → no seeding, user content intact, autosprint block appended.
    assert "# user content" in text
    assert "foo/" in text
    assert "autosprint/logs/*" in text
    # Python defaults NOT added — user had their own file and we respect it.
    assert "__pycache__/" not in text
    assert ".pytest_cache/" not in text


def test_ensure_gitignore_does_not_duplicate_existing_entries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    gi = tmp_path / ".gitignore"
    gi.write_text("autosprint/logs/*\n!autosprint/logs/sprint-outcomes.log\n", encoding="utf-8")
    _ensure_gitignore_entries()
    _ensure_gitignore_entries()  # call twice to confirm idempotence
    text = gi.read_text(encoding="utf-8")
    assert text.count("autosprint/logs/*") == 1
    assert text.count("!autosprint/logs/sprint-outcomes.log") == 1
    assert "autosprint/cache/" in text


def test_ensure_gitignore_preserves_user_entries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    gi = tmp_path / ".gitignore"
    gi.write_text("# user content\n*.log\nnode_modules/\n", encoding="utf-8")
    _ensure_gitignore_entries()
    text = gi.read_text(encoding="utf-8")
    assert "# user content" in text
    assert "node_modules/" in text
    assert "autosprint/cache/" in text


def test_ensure_gitignore_migrates_legacy_logs_dir_entry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Existing target with the legacy `autosprint/logs/` line (no wildcard, ignores everything) should be migrated to the wildcard-plus-unignore pattern so the three history files start being tracked."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    gi = tmp_path / ".gitignore"
    gi.write_text("# user content\nautosprint/logs/\nautosprint/cache/\nautosprint/stop\nautosprint/stop-now\n", encoding="utf-8")
    _ensure_gitignore_entries()
    text = gi.read_text(encoding="utf-8")
    # Legacy line gone (no longer ignores everything).
    assert "\nautosprint/logs/\n" not in text
    # New pattern in place.
    assert "autosprint/logs/*" in text
    assert "!autosprint/logs/sprint-outcomes.log" in text
    assert "!autosprint/logs/plan-decisions.md" in text
    assert "!autosprint/logs/runtime-stats.md" in text
    # User content preserved.
    assert "# user content" in text


# ---------------------------------------------------------------------------
# _copy_claude_assets_to_target — ships skills + agents into TARGET_REPO/.claude/
# ---------------------------------------------------------------------------


def _make_fake_claude_src(root: Path, skills: list[str] = (), agent_files: list[str] = ()) -> None:
    claude = root / ".claude"
    if skills:
        for name in skills:
            skill = claude / "skills" / name
            skill.mkdir(parents=True, exist_ok=True)
            (skill / "SKILL.md").write_text(f"# {name}\n\nstub\n", encoding="utf-8")
    if agent_files:
        (claude / "agents").mkdir(parents=True, exist_ok=True)
        for name in agent_files:
            (claude / "agents" / name).write_text(f"# agent {name}\n", encoding="utf-8")


def test_copy_claude_assets_creates_target_and_copies_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src_root = tmp_path / "autosprint_src"
    _make_fake_claude_src(src_root, skills=["skill-a", "skill-b"], agent_files=["implement.md", "plan-team.md"])
    target = tmp_path / "target"
    monkeypatch.setattr(config, "TARGET_REPO", str(target))
    monkeypatch.setattr(init_mod, "_project_root", lambda: src_root)
    _copy_claude_assets_to_target()
    assert (target / ".claude" / "skills" / "skill-a" / "SKILL.md").exists()
    assert (target / ".claude" / "skills" / "skill-b" / "SKILL.md").exists()
    assert (target / ".claude" / "agents" / "implement.md").exists()
    assert (target / ".claude" / "agents" / "plan-team.md").exists()


def test_copy_claude_assets_does_not_overwrite_existing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src_root = tmp_path / "autosprint_src"
    _make_fake_claude_src(src_root, skills=["skill-a"], agent_files=["implement.md"])
    target = tmp_path / "target"
    existing_skill = target / ".claude" / "skills" / "skill-a"
    existing_skill.mkdir(parents=True, exist_ok=True)
    (existing_skill / "SKILL.md").write_text("# user-edited skill\n\nkeep me\n", encoding="utf-8")
    existing_agent_dir = target / ".claude" / "agents"
    existing_agent_dir.mkdir(parents=True, exist_ok=True)
    (existing_agent_dir / "implement.md").write_text("# user-edited agent\n", encoding="utf-8")
    monkeypatch.setattr(config, "TARGET_REPO", str(target))
    monkeypatch.setattr(init_mod, "_project_root", lambda: src_root)
    _copy_claude_assets_to_target()
    assert (existing_skill / "SKILL.md").read_text(encoding="utf-8") == "# user-edited skill\n\nkeep me\n"
    assert (existing_agent_dir / "implement.md").read_text(encoding="utf-8") == "# user-edited agent\n"


def test_copy_claude_assets_silent_when_source_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src_root = tmp_path / "autosprint_src"  # intentionally not created
    target = tmp_path / "target"
    monkeypatch.setattr(config, "TARGET_REPO", str(target))
    monkeypatch.setattr(init_mod, "_project_root", lambda: src_root)
    _copy_claude_assets_to_target()  # must not raise
    assert not (target / ".claude").exists()


def test_copy_claude_assets_overwrite_replaces_existing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`init --update-skills` path: overwrite=True replaces existing target entries with the autosprint source."""
    src_root = tmp_path / "autosprint_src"
    _make_fake_claude_src(src_root, skills=["skill-a"], agent_files=["implement.md"])
    target = tmp_path / "target"
    existing_skill = target / ".claude" / "skills" / "skill-a"
    existing_skill.mkdir(parents=True, exist_ok=True)
    (existing_skill / "SKILL.md").write_text("# stale user-edited skill\n", encoding="utf-8")
    existing_agent_dir = target / ".claude" / "agents"
    existing_agent_dir.mkdir(parents=True, exist_ok=True)
    (existing_agent_dir / "implement.md").write_text("# stale user-edited agent\n", encoding="utf-8")
    monkeypatch.setattr(config, "TARGET_REPO", str(target))
    monkeypatch.setattr(init_mod, "_project_root", lambda: src_root)
    _copy_claude_assets_to_target(overwrite=True)
    assert (existing_skill / "SKILL.md").read_text(encoding="utf-8") == "# skill-a\n\nstub\n"
    assert (existing_agent_dir / "implement.md").read_text(encoding="utf-8") == "# agent implement.md\n"
