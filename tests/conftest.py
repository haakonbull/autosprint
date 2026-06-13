"""Shared pytest fixtures for the autosprint test suite.

Autosprint's own unit tests exercise code paths (`printlev`, `should_replan`)
that write to `TARGET_REPO/autosprint/console-verbose.log` via `printlev`. If
`TARGET_REPO` in the local `.env` points at a real project, running
`uv run pytest` would pollute that project's log with test output. The
autouse fixtures below turn off the side-effecting persistence layers for
the duration of the suite — log tee + SQLite mirror — so a stray
``append_run_log`` from a unit test can't write into a real target repo's
``autosprint/runs.db``.
"""

from pathlib import Path

import pytest

from autosprint.config import config
from autosprint.infra import db
from autosprint.util.paths import AUTOSPRINT_DIR_NAME

# ---------------------------------------------------------------------------
# Target-repo scaffolding
#
# The vast majority of tests point the config singleton's TARGET_REPO at a
# throwaway tmp_path and, depending on the code path, need that directory to
# look like a git repo and/or an autosprint-initialised repo. These three
# fixtures layer those states so a test asks for exactly the tier it needs:
#   target_repo            -> TARGET_REPO set, empty dir
#   git_target_repo        -> + a .git directory
#   initialised_target_repo -> + autosprint/config.toml (autosprint-init'd)
# ---------------------------------------------------------------------------


@pytest.fixture
def target_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A tmp_path registered as the active TARGET_REPO. Base for most tests."""
    monkeypatch.setattr(config, "TARGET_REPO", str(tmp_path))
    return tmp_path


@pytest.fixture
def git_target_repo(target_repo: Path) -> Path:
    """A target repo that is a git repository but not yet autosprint-initialised."""
    (target_repo / ".git").mkdir()
    return target_repo


@pytest.fixture
def initialised_target_repo(git_target_repo: Path) -> Path:
    """A fully autosprint-initialised target repo: git + autosprint/config.toml."""
    autosprint_dir = git_target_repo / AUTOSPRINT_DIR_NAME
    autosprint_dir.mkdir()
    (autosprint_dir / "config.toml").write_text("", encoding="utf-8")
    return git_target_repo


@pytest.fixture(autouse=True)
def _disable_console_log_tee(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "SAVE_CONSOLE_LOG", False)


@pytest.fixture(autouse=True)
def _reset_auto_replan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin AUTO_REPLAN to True for every test. The suite was written when the loop
    replanned by default; a user's local `.env` (now reviewed-plan by default) would
    otherwise leak in and flip `should_replan` / `pit_loop` behaviour, breaking tests
    that assume the loop replans. Tests that exercise reviewed-plan mode set
    AUTO_REPLAN=False explicitly with their own monkeypatch."""
    monkeypatch.setattr(config, "AUTO_REPLAN", True)


@pytest.fixture(autouse=True)
def _silence_speech(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin SPEAK_LEVEL to 'off' for every test. A user's local `.env` may set
    SPEAK_LEVEL=run (or louder); without this, that value leaks into the suite via the
    config singleton and tests would spawn real pyttsx3 TTS threads. Tests that exercise
    the speech tiers use the pure `speak_tier_enabled` helper, not real audio."""
    monkeypatch.setattr(config, "SPEAK_LEVEL", "off")


@pytest.fixture(autouse=True)
def _isolate_db_module_state() -> None:
    """Reset the db module's per-process run-id state before each test so the order in which tests run cannot cause one test's record_run_start to bleed into another test's append_run_log call."""
    db._reset_for_tests()
    yield
    db._reset_for_tests()
