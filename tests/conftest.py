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

from __future__ import annotations

import pytest

from autosprint import db
from autosprint.config import config


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
