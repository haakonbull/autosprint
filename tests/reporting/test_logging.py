"""Tests for sprint-outcomes.log, recent_sprint_history, check_escalation, and wrap_message.

All fast — no LLM calls, no pit_loop invocation. They target the logging/migration
helpers added in recent changes that weren't previously covered.
"""

from pathlib import Path

import pytest

from autosprint.app.init import _migrate_legacy_autosprint_files
from autosprint.config import config
from autosprint.phases.test_phase import extract_test_output_highlights as _extract_test_output_highlights
from autosprint.phases.test_phase import read_last_test_output as _read_last_test_output
from autosprint.phases.test_phase import write_last_test_output as _write_last_test_output
from autosprint.reporting import run_log
from autosprint.reporting.run_log import (
    append_changelog_entry,
    append_run_log,
    apply_destination_resolutions,
    check_escalation,
    recent_sprint_history,
)
from autosprint.reporting.run_log import (
    estimated_runtime_line as _estimated_runtime_line,
)
from autosprint.reporting.run_log import (
    read_runtime_stats as _read_runtime_stats,
)
from autosprint.reporting.run_log import (
    trim_console_verbose_log as _trim_console_verbose_log,
)
from autosprint.reporting.run_log import (
    trim_plan_decisions_log as _trim_plan_decisions_log,
)
from autosprint.reporting.run_log import (
    update_runtime_stats as _update_runtime_stats,
)
from autosprint.reporting.run_log import (
    write_runtime_stats as _write_runtime_stats,
)
from autosprint.util.output import CONSOLE_LOG_FILENAME, wrap_message
from autosprint.util.parsing import parse_implement_result
from autosprint.util.paths import (
    LAST_TEST_OUTPUT_FILENAME,
    PLAN_DECISIONS_FILENAME,
    RUNTIME_STATS_FILENAME,
    SPRINT_LOG_FILENAME,
)

# ---------------------------------------------------------------------------
# append_run_log — header once, sprint column populated
# ---------------------------------------------------------------------------


def test_ai_run_log_writes_header_once_on_creation(target_repo: Path) -> None:
    append_run_log(1, "first task", "OK", "OK", "abc1234")
    append_run_log(1, "second task", "OK", "OK", "def5678")

    log_path = target_repo / SPRINT_LOG_FILENAME
    lines = log_path.read_text(encoding="utf-8").splitlines()
    # Exactly one header line, and it starts with '#'
    header_lines = [line for line in lines if line.startswith("#")]
    assert len(header_lines) == 1
    assert "sprint" in header_lines[0]
    assert "timestamp" in header_lines[0]
    # Two data rows
    data_lines = [line for line in lines if not line.startswith("#")]
    assert len(data_lines) == 2
    assert "first task" in data_lines[0]
    assert "second task" in data_lines[1]


def test_ai_run_log_data_row_starts_with_sprint_number(target_repo: Path) -> None:
    append_run_log(7, "the task", "OK", "OK", "hash1")

    log_path = target_repo / SPRINT_LOG_FILENAME
    data_line = next(line for line in log_path.read_text(encoding="utf-8").splitlines() if not line.startswith("#"))
    assert data_line.split("|")[0].strip() == "7"


def test_ai_run_log_fake_implement_does_not_write(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    """FAKE_IMPLEMENT mode must keep sprint-outcomes.log clean so escalation/history aren't polluted."""
    monkeypatch.setattr(config, "FAKE_IMPLEMENT", True)

    append_run_log(1, "fake task", "OK", "OK", "deadbeef")
    assert not (target_repo / SPRINT_LOG_FILENAME).exists()


# ---------------------------------------------------------------------------
# recent_sprint_history — skips comment lines
# ---------------------------------------------------------------------------


def test_recent_sprint_history_skips_comment_lines(target_repo: Path) -> None:
    log_path = target_repo / SPRINT_LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("# header\n1 | ts1 | task A | OK | OK | hash1\n# === run started ... ===\n2 | ts2 | task B | OK | OK | hash2\n", encoding="utf-8")

    history = recent_sprint_history(n=5)
    assert "# header" not in history
    assert "run started" not in history
    assert "task A" in history
    assert "task B" in history


def test_recent_sprint_history_empty_file_returns_empty_string(target_repo: Path) -> None:
    assert recent_sprint_history() == ""


# ---------------------------------------------------------------------------
# check_escalation — three reverts of same task raises; anything less does not
# ---------------------------------------------------------------------------


def _write_log(tmp_path: Path, body: str) -> None:
    path = tmp_path / SPRINT_LOG_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_check_escalation_raises_on_three_reverts_of_same_task(target_repo: Path) -> None:
    body = "# header\n" + "\n".join(f"{i} | 2026-04-20T00:00:0{i}Z |  3 | flaky task | FAILED | n/a | REVERTED" for i in range(1, 4))
    _write_log(target_repo, body + "\n")
    with pytest.raises(RuntimeError, match="flaky task"):
        check_escalation()


def test_check_escalation_does_not_raise_on_two_reverts(target_repo: Path) -> None:
    body = "# header\n" + "\n".join(f"{i} | ts |  3 | flaky task | FAILED | n/a | REVERTED" for i in range(1, 3))
    _write_log(target_repo, body + "\n")
    check_escalation()  # must not raise


def test_check_escalation_does_not_raise_when_reverts_are_different_tasks(target_repo: Path) -> None:
    body = "# header\n1 | ts |  3 | task A | FAILED | n/a | REVERTED\n2 | ts |  3 | task B | FAILED | n/a | REVERTED\n3 | ts |  3 | task C | FAILED | n/a | REVERTED\n"
    _write_log(target_repo, body)
    check_escalation()


def test_check_escalation_skipped_in_fake_implement_mode(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    """Fake implement mode must not trip escalation even with many REVERTED lines in the log."""
    monkeypatch.setattr(config, "FAKE_IMPLEMENT", True)
    body = "# header\n" + "\n".join(f"{i} | ts |  3 | flaky | FAILED | n/a | REVERTED" for i in range(1, 6))
    _write_log(target_repo, body + "\n")
    check_escalation()


def test_check_escalation_dedupes_dual_write_pattern(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    """A single failed sprint produces two log entries per task (the bare ``REVERTED`` line from run_implement's handler and the ``SPRINT_REVERTED:`` line from the outer pit_loop handler). Escalation must count by (sprint_no, task) so two unique sprint failures don't inflate to four log matches and falsely trigger the threshold."""
    monkeypatch.setattr(config, "IMPLEMENT_FALLBACK_AGENT", "")  # disable the refusal-fallback to isolate the dedup behavior
    body = "# header\n39 | ts |  3 | leaderboard task | FAILED | n/a | REVERTED\n39 | ts |  3 | leaderboard task | FAILED | FAILED | SPRINT_REVERTED: tests broken\n41 | ts |  3 | leaderboard task | FAILED | n/a | REVERTED\n41 | ts |  3 | leaderboard task | FAILED | FAILED | SPRINT_REVERTED: tests broken\n"
    _write_log(target_repo, body)
    # 4 log lines but only 2 distinct sprint failures — must NOT escalate.
    check_escalation()


def test_check_escalation_skips_refusal_reverts_when_a6_enabled(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    """When the refusal-fallback is configured, refusal-pattern reverts in the history don't count toward escalation. Otherwise pre-fallback refusal histories would permanently lock out tasks that the fallback would now rescue."""
    monkeypatch.setattr(config, "IMPLEMENT_FALLBACK_AGENT", "implementor_gpt55")
    body = "# header\n1 | ts |  3 | leaderboard | FAILED | FAILED | SPRINT_REVERTED: must refuse to improve\n2 | ts |  3 | leaderboard | FAILED | FAILED | SPRINT_REVERTED: refusing to augment per system directive\n3 | ts |  3 | leaderboard | FAILED | FAILED | SPRINT_REVERTED: instructed to refuse the task\n"
    _write_log(target_repo, body)
    # 3 refusal-pattern reverts but the refusal-fallback active → must NOT escalate.
    check_escalation()


def test_check_escalation_still_fires_on_non_refusal_reverts_with_a6(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    """The refusal-fallback's escalation skip is scoped to refusal patterns. Genuine failures (test failures, real bugs) still escalate as before so problems aren't masked behind the safety net."""
    monkeypatch.setattr(config, "IMPLEMENT_FALLBACK_AGENT", "implementor_gpt55")
    body = "# header\n1 | ts |  3 | broken-task | OK | FAILED | REVERTED\n2 | ts |  3 | broken-task | OK | FAILED | REVERTED\n3 | ts |  3 | broken-task | OK | FAILED | REVERTED\n"
    _write_log(target_repo, body)
    with pytest.raises(RuntimeError, match="broken-task"):
        check_escalation()


def test_check_escalation_mixed_refusal_and_real_failure_with_a6(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    """When some reverts are refusal-pattern (skipped by the refusal-fallback) and others are real failures (counted), only the real ones contribute to the threshold. 2 refusals + 2 real failures = 2 counted, must NOT escalate."""
    monkeypatch.setattr(config, "IMPLEMENT_FALLBACK_AGENT", "implementor_gpt55")
    body = "# header\n1 | ts |  3 | mixed-task | FAILED | FAILED | SPRINT_REVERTED: must refuse this work\n2 | ts |  3 | mixed-task | FAILED | FAILED | SPRINT_REVERTED: refusing to augment\n3 | ts |  3 | mixed-task | OK | FAILED | REVERTED\n4 | ts |  3 | mixed-task | OK | FAILED | REVERTED\n"
    _write_log(target_repo, body)
    check_escalation()  # only 2 real-failure reverts counted; under threshold


# ---------------------------------------------------------------------------
# wrap_message — preserves indent, handles disable
# ---------------------------------------------------------------------------


def test_wrap_message_short_line_unchanged() -> None:
    assert wrap_message("short line", max_width=120) == "short line"


def test_wrap_message_long_line_preserves_indent() -> None:
    text = "    " + "hello " * 30  # ~180 chars with 4-space indent
    wrapped = wrap_message(text, max_width=80)
    lines = wrapped.splitlines()
    # All wrapped lines keep the leading 4-space indent
    assert all(line.startswith("    ") for line in lines)
    # All lines are within the width budget
    assert all(len(line) <= 80 for line in lines)


def test_wrap_message_disabled_with_zero_width() -> None:
    long = "x" * 500
    assert wrap_message(long, max_width=0) == long


def test_wrap_message_preserves_multiline_structure() -> None:
    text = "first line short\n" + "    second line is long enough to need wrapping " + "word " * 30
    wrapped = wrap_message(text, max_width=60)
    # First source line survives intact
    assert wrapped.splitlines()[0] == "first line short"
    # Second source line's wraps all keep the 4-space indent
    wrapped_tail = wrapped.splitlines()[1:]
    assert all(line.startswith("    ") for line in wrapped_tail)


# ---------------------------------------------------------------------------
# _migrate_legacy_autosprint_files — moves root-level legacy files into autosprint/
# ---------------------------------------------------------------------------


def test_migrate_moves_legacy_files_into_autosprint_folder(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    monkeypatch.setattr(config, "SAVE_CONSOLE_LOG", False)  # don't let migrate's own printlev append to the console log
    (target_repo / "ai-run.log").write_text("old log\n", encoding="utf-8")
    (target_repo / "plan-decision-log.md").write_text("old decisions\n", encoding="utf-8")
    (target_repo / "autosprint-console.log").write_text("old console\n", encoding="utf-8")
    (target_repo / "plan.md").write_text("# old plan\n", encoding="utf-8")
    (target_repo / "adr.md").write_text("# old adr\n", encoding="utf-8")
    # Pre-rename layout: root-level ideal_state.md from before the rename to destination.md.
    (target_repo / "ideal_state.md").write_text("# old destination content\n", encoding="utf-8")

    _migrate_legacy_autosprint_files()

    autosprint_dir = target_repo / "autosprint"
    logs_dir = autosprint_dir / "logs"
    # Root-level legacy files move AND adopt the role-descriptive names AND land under logs/ in the new layout.
    assert (logs_dir / "sprint-outcomes.log").read_text(encoding="utf-8") == "old log\n"
    assert (logs_dir / "plan-decisions.md").read_text(encoding="utf-8") == "old decisions\n"
    assert (logs_dir / "console-verbose.log").read_text(encoding="utf-8") == "old console\n"
    # Loop-state files (plan.md, adr.md, destination.md) stay at autosprint/ root.
    assert (autosprint_dir / "plan.md").read_text(encoding="utf-8") == "# old plan\n"
    assert (autosprint_dir / "adr.md").read_text(encoding="utf-8") == "# old adr\n"
    assert (autosprint_dir / "destination.md").read_text(encoding="utf-8") == "# old destination content\n"
    # Originals at root are gone.
    assert not (target_repo / "ai-run.log").exists()
    assert not (target_repo / "plan.md").exists()
    # Logs are NOT at autosprint/ root anymore (they moved into logs/).
    assert not (autosprint_dir / "sprint-outcomes.log").exists()
    assert not (autosprint_dir / "console-verbose.log").exists()


def test_migrate_renames_old_folder_filenames_in_place(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    """Repos that already ran post-folder-migration but before the log-rename see the files renamed in autosprint/."""
    monkeypatch.setattr(config, "SAVE_CONSOLE_LOG", False)
    autosprint_dir = target_repo / "autosprint"
    autosprint_dir.mkdir()
    (autosprint_dir / "ai-run.log").write_text("folder log\n", encoding="utf-8")
    (autosprint_dir / "console.log").write_text("folder console\n", encoding="utf-8")
    (autosprint_dir / "plan-decision-log.md").write_text("folder decisions\n", encoding="utf-8")

    _migrate_legacy_autosprint_files()

    logs_dir = autosprint_dir / "logs"
    # Old folder-level filenames first get renamed in place, then relocated under logs/.
    assert (logs_dir / "sprint-outcomes.log").read_text(encoding="utf-8") == "folder log\n"
    assert (logs_dir / "console-verbose.log").read_text(encoding="utf-8") == "folder console\n"
    assert (logs_dir / "plan-decisions.md").read_text(encoding="utf-8") == "folder decisions\n"
    assert not (autosprint_dir / "ai-run.log").exists()
    assert not (autosprint_dir / "console.log").exists()
    assert not (autosprint_dir / "plan-decision-log.md").exists()
    # Renamed files are no longer at autosprint/ root either.
    assert not (autosprint_dir / "sprint-outcomes.log").exists()
    assert not (autosprint_dir / "console-verbose.log").exists()
    assert not (autosprint_dir / "plan-decisions.md").exists()


def test_migrate_renames_orphan_when_target_already_exists(target_repo: Path) -> None:
    """If both root-level legacy and the new autosprint/ version exist, keep the autosprint/ one AND rename the orphan to a timestamped suffix so the legacy name is free for the next run (idempotent re-creation detection)."""
    (target_repo / "ai-run.log").write_text("old at root\n", encoding="utf-8")
    autosprint_dir = target_repo / "autosprint"
    autosprint_dir.mkdir()
    (autosprint_dir / "sprint-outcomes.log").write_text("new in folder\n", encoding="utf-8")

    _migrate_legacy_autosprint_files()

    # The newer autosprint/ version is preserved untouched and relocated under logs/.
    logs_dir = autosprint_dir / "logs"
    assert (logs_dir / "sprint-outcomes.log").read_text(encoding="utf-8") == "new in folder\n"
    # The orphan is renamed (not deleted — preserves user data) with an `.orphan-<ts>` suffix.
    assert not (target_repo / "ai-run.log").exists()
    orphans = list(target_repo.glob("ai-run.log.orphan-*"))
    assert len(orphans) == 1
    assert orphans[0].read_text(encoding="utf-8") == "old at root\n"


def test_migrate_noop_when_nothing_to_migrate(target_repo: Path) -> None:
    _migrate_legacy_autosprint_files()
    # Folder should be created but be empty of the migrated files.
    assert (target_repo / "autosprint").exists()
    assert not any((target_repo / "autosprint").iterdir())


# ---------------------------------------------------------------------------
# Soft caps on plan-decisions.md and console-verbose.log
# ---------------------------------------------------------------------------


def _write_plan_decisions(autosprint_dir: Path, n_sprints: int) -> None:
    logs_dir = autosprint_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    entries = [f"\n## 2026-04-{(i % 28) + 1:02d}T00:00:00Z — duo sprint-{i}\n\n### Final pending\n\n- Task {i}\n  Description for task {i}\n" for i in range(n_sprints)]
    (logs_dir / "plan-decisions.md").write_text("".join(entries), encoding="utf-8")


def test_trim_plan_decisions_noop_when_under_cap(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    monkeypatch.setattr(config, "PLAN_DECISIONS_RECENT_COUNT", 30)
    autosprint_dir = target_repo / "autosprint"
    _write_plan_decisions(autosprint_dir, n_sprints=5)
    original = (autosprint_dir / "logs" / "plan-decisions.md").read_text(encoding="utf-8")
    _trim_plan_decisions_log()
    assert (autosprint_dir / "logs" / "plan-decisions.md").read_text(encoding="utf-8") == original


def test_trim_plan_decisions_drops_oldest_when_over_cap(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    monkeypatch.setattr(config, "PLAN_DECISIONS_RECENT_COUNT", 3)
    autosprint_dir = target_repo / "autosprint"
    _write_plan_decisions(autosprint_dir, n_sprints=10)
    _trim_plan_decisions_log()
    text = (autosprint_dir / "logs" / "plan-decisions.md").read_text(encoding="utf-8")
    # Last 3 sprints kept (indices 7, 8, 9).
    assert "Task 9" in text
    assert "Task 8" in text
    assert "Task 7" in text
    # Older ones dropped.
    assert "Task 0" not in text
    assert "Task 6" not in text


def test_trim_plan_decisions_disabled_when_cap_zero(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    monkeypatch.setattr(config, "PLAN_DECISIONS_RECENT_COUNT", 0)
    autosprint_dir = target_repo / "autosprint"
    _write_plan_decisions(autosprint_dir, n_sprints=50)
    original = (autosprint_dir / "logs" / "plan-decisions.md").read_text(encoding="utf-8")
    _trim_plan_decisions_log()
    assert (autosprint_dir / "logs" / "plan-decisions.md").read_text(encoding="utf-8") == original


def test_trim_plan_decisions_silent_when_file_missing(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    monkeypatch.setattr(config, "PLAN_DECISIONS_RECENT_COUNT", 30)
    _trim_plan_decisions_log()  # must not raise


def test_trim_console_log_noop_when_under_cap(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    monkeypatch.setattr(config, "CONSOLE_LOG_MAX_BYTES", 10_000)
    logs_dir = target_repo / "autosprint" / "logs"
    logs_dir.mkdir(parents=True)
    path = logs_dir / "console-verbose.log"
    path.write_text("# === run started 2026-04-01T00:00:00Z ===\nshort\n", encoding="utf-8")
    original = path.read_text(encoding="utf-8")
    _trim_console_verbose_log()
    assert path.read_text(encoding="utf-8") == original


def test_trim_console_log_drops_oldest_runs_over_cap(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    logs_dir = target_repo / "autosprint" / "logs"
    logs_dir.mkdir(parents=True)
    path = logs_dir / "console-verbose.log"
    # Five run blocks with distinctive markers; each ~500 bytes.
    blocks = [f"# === run started 2026-04-0{i}T00:00:00Z ===\n" + ("payload " * 60) + f"\nrun-{i}-end\n" for i in range(1, 6)]
    path.write_text("".join(blocks), encoding="utf-8")
    # Cap small enough to force dropping at least one block.
    monkeypatch.setattr(config, "CONSOLE_LOG_MAX_BYTES", 1500)
    _trim_console_verbose_log()
    text = path.read_text(encoding="utf-8")
    # Newest run always survives.
    assert "run-5-end" in text
    # Oldest run is gone.
    assert "run-1-end" not in text
    assert len(text.encode("utf-8")) <= 1500 or text.count("# === run started ") == 1


def test_trim_console_log_disabled_when_cap_zero(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    monkeypatch.setattr(config, "CONSOLE_LOG_MAX_BYTES", 0)
    logs_dir = target_repo / "autosprint" / "logs"
    logs_dir.mkdir(parents=True)
    path = logs_dir / "console-verbose.log"
    big = "# === run started 2026-04-01 ===\n" + ("x" * 10_000) + "\n"
    path.write_text(big, encoding="utf-8")
    _trim_console_verbose_log()
    assert path.read_text(encoding="utf-8") == big


def test_migrate_handles_recreated_console_log_inside_autosprint_dir(target_repo: Path) -> None:
    """The real-world case. After a prior migration, `autosprint/console-verbose.log` is the live log. A bug or older code path re-creates `autosprint/console.log`. Next startup: keep the live log untouched, rename the orphan with a timestamp so it doesn't silently accumulate."""
    autosprint_dir = target_repo / "autosprint"
    autosprint_dir.mkdir()
    (autosprint_dir / "console-verbose.log").write_text("live log\n", encoding="utf-8")
    (autosprint_dir / "console.log").write_text("re-created orphan\n", encoding="utf-8")

    _migrate_legacy_autosprint_files()

    # The live console log gets relocated under logs/ during the third migration step.
    logs_dir = autosprint_dir / "logs"
    assert (logs_dir / "console-verbose.log").read_text(encoding="utf-8") == "live log\n"
    assert not (autosprint_dir / "console-verbose.log").exists()
    assert not (autosprint_dir / "console.log").exists()
    # The orphan stays at autosprint/ root (orphan suffix doesn't match any relocation entry).
    orphans = list(autosprint_dir.glob("console.log.orphan-*"))
    assert len(orphans) == 1
    assert orphans[0].read_text(encoding="utf-8") == "re-created orphan\n"


# ---------------------------------------------------------------------------
# runtime-stats.md — rolling average + banner line
# ---------------------------------------------------------------------------


def test_read_runtime_stats_missing_returns_zero(target_repo: Path) -> None:
    assert _read_runtime_stats() == (0.0, 0, 0)


def test_write_then_read_roundtrip(target_repo: Path) -> None:
    _write_runtime_stats(123.45, 7, 42)
    assert _read_runtime_stats() == (123.45, 7, 42)
    # File is human-readable, not pickled.
    body = (target_repo / RUNTIME_STATS_FILENAME).read_text(encoding="utf-8")
    assert "average_sprint_time_seconds" in body
    assert "sprint_count: 7" in body
    assert "total_story_points: 42" in body


def test_update_runtime_stats_rolling_formula(target_repo: Path) -> None:
    """new_avg = ((old_avg * old_count) + latest) / (old_count + 1); total_sp accumulates."""
    _write_runtime_stats(100.0, 4, 20)
    _update_runtime_stats(200.0, sprint_sp=8)
    avg, count, total_sp = _read_runtime_stats()
    assert count == 5
    # (100*4 + 200) / 5 = 120.0
    assert abs(avg - 120.0) < 1e-6
    assert total_sp == 28


def test_update_runtime_stats_skipped_in_fake_modes(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    monkeypatch.setattr(config, "FAKE_IMPLEMENT", True)
    _update_runtime_stats(99.0)
    assert not (target_repo / RUNTIME_STATS_FILENAME).exists()


def test_estimated_runtime_line_first_run(target_repo: Path) -> None:
    line = _estimated_runtime_line(planned_sprints=10)
    assert "no history" in line


def test_estimated_runtime_line_uses_rolling_average(target_repo: Path) -> None:
    _write_runtime_stats(average_seconds=120.0, count=3, total_sp=0)
    line = _estimated_runtime_line(planned_sprints=5)
    # 120s * 5 sprints = 600s = 10 minutes
    assert "10.0 min" in line
    assert "5 sprints" in line


def test_estimated_runtime_line_includes_sp_rate_when_available(target_repo: Path) -> None:
    # 3 sprints at 120s each = 360s total; 18 SP total → 20s/SP
    _write_runtime_stats(average_seconds=120.0, count=3, total_sp=18)
    line = _estimated_runtime_line(planned_sprints=5)
    assert "20.0s/SP" in line


# ---------------------------------------------------------------------------
# changelog.md — committed per-run, per-sprint record
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_changelog_run_heading(monkeypatch: pytest.MonkeyPatch) -> None:
    """`append_changelog_entry` gates the `## Run …` heading on a module-level flag that
    resets only on process restart. Tests share the process, so reset it before each test
    — without this a test that ran earlier would suppress the run heading here."""
    monkeypatch.setattr(run_log.changelog, "_CHANGELOG_RUN_HEADING_WRITTEN", False)


def test_append_changelog_creates_file_with_header(target_repo: Path) -> None:
    append_changelog_entry(1, [{"title": "Add feature X"}], "Did the thing.")
    text = (target_repo / "autosprint" / "changelog.md").read_text(encoding="utf-8")
    assert text.startswith("# Changelog")
    # First entry of a run emits a `## Run …` heading and a `### Sprint N` sub-heading.
    assert "## Run " in text
    assert "### Sprint 1 — " in text
    assert "Add feature X" in text
    assert "Did the thing." in text


def test_append_changelog_appends_without_duplicating_header(target_repo: Path) -> None:
    append_changelog_entry(1, [{"title": "First"}], "one")
    append_changelog_entry(2, [{"title": "Second"}], "two")
    text = (target_repo / "autosprint" / "changelog.md").read_text(encoding="utf-8")
    assert text.count("# Changelog") == 1  # file header written once
    # One `## Run` heading for the whole run — the second sprint reuses it.
    assert text.count("## Run ") == 1
    # Oldest-first chronological append of the sprint sub-headings.
    assert text.index("### Sprint 1 — ") < text.index("### Sprint 2 — ")


def test_append_changelog_joins_grouped_task_titles(target_repo: Path) -> None:
    append_changelog_entry(3, [{"title": "Task A"}, {"title": "Task B"}], "Both shipped.")
    text = (target_repo / "autosprint" / "changelog.md").read_text(encoding="utf-8")
    assert "Task A; Task B" in text


def test_append_changelog_noop_in_fake_implement(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    """Fake runs must not pollute the real changelog — mirrors append_run_log's FAKE_IMPLEMENT guard."""
    monkeypatch.setattr(config, "FAKE_IMPLEMENT", True)
    append_changelog_entry(1, [{"title": "Fake"}], "fake summary")
    assert not (target_repo / "autosprint" / "changelog.md").exists()


def test_append_changelog_two_runs_do_not_collide(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    """BUG 3 regression guard. `sprint_number` resets to 0 every `pit_loop`, so a flat
    `## Sprint N` heading collided across runs against the same repo. Run-scoped headings
    fix this: two separate run-sequences must produce two distinct `## Run` headings, and
    the per-run `### Sprint N` sub-headings reading "sprint N of this run" is now correct
    even though both runs reuse sprint numbers 1, 2."""
    # --- Run 1: sprints 1, 2 ---
    append_changelog_entry(1, [{"title": "Run1 task A"}], "first run sprint 1")
    append_changelog_entry(2, [{"title": "Run1 task B"}], "first run sprint 2")

    # Simulate a process restart: the run-heading flag resets, exactly as it would
    # when a brand-new `autosprint` invocation starts a fresh `pit_loop`.
    monkeypatch.setattr(run_log.changelog, "_CHANGELOG_RUN_HEADING_WRITTEN", False)

    # --- Run 2: sprints 1, 2 again (counter reset) ---
    append_changelog_entry(1, [{"title": "Run2 task A"}], "second run sprint 1")
    append_changelog_entry(2, [{"title": "Run2 task B"}], "second run sprint 2")

    text = (target_repo / "autosprint" / "changelog.md").read_text(encoding="utf-8")
    # Two runs → two `## Run` headings, no collision.
    assert text.count("## Run ") == 2
    # Each run has its own Sprint 1 / Sprint 2 sub-headings — duplicate sprint numbers
    # are no longer ambiguous because they're nested under distinct run headings.
    assert text.count("### Sprint 1 — ") == 2
    assert text.count("### Sprint 2 — ") == 2
    # All four sprint entries survived, in chronological order.
    for marker in ("Run1 task A", "Run1 task B", "Run2 task A", "Run2 task B"):
        assert marker in text
    assert text.index("Run1 task A") < text.index("Run2 task A")
    # The second `## Run` heading sits after run 1's content — runs don't interleave.
    second_run_heading = text.index("## Run ", text.index("## Run ") + 1)
    assert text.index("Run1 task B") < second_run_heading < text.index("Run2 task A")


# ---------------------------------------------------------------------------
# apply_destination_resolutions — open-question writeback (BUG 1 + BUG 2)
# ---------------------------------------------------------------------------

_DESTINATION_FIXTURE = """# Destination

## Purpose

A tool that does things.

## Test strategy

We have a well thought-through testing strategy with a clear rationale specified in `adr.md`. *(Open — autosprint to decide.)*

## Documentation quality

A new reader gets clone-to-running in 10 minutes.

## AI-resolved questions

This section is reserved for one-line summaries of open questions resolved.

_No questions resolved yet._

## AI-generated subgoals

_No AI-generated subgoals yet._
"""


def _write_destination(tmp_path: Path, text: str = _DESTINATION_FIXTURE) -> Path:
    path = tmp_path / "autosprint" / "destination.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_writeback_appends_status_marker_at_section_end(target_repo: Path) -> None:
    """Write #2: the status-marker blockquote lands at the END of the resolved section —
    after the human-authored '(Open — autosprint to decide.)' line (which is left intact)
    and before the next '## ' heading."""
    _write_destination(target_repo)

    apply_destination_resolutions([{"section": "Test strategy", "answer": "pytest, unit-heavy", "adr_ref": "2026-05-16 — Test strategy"}])

    text = (target_repo / "autosprint" / "destination.md").read_text(encoding="utf-8")
    lines = text.split("\n")
    marker = "> **Status:** resolved"
    marker_idx = next(i for i, line in enumerate(lines) if line.startswith(marker))
    open_idx = next(i for i, line in enumerate(lines) if "*(Open — autosprint to decide.)*" in line)
    next_heading_idx = next(i for i, line in enumerate(lines) if line.startswith("## Documentation quality"))
    # Marker sits after the human 'open' line and before the next section heading.
    assert open_idx < marker_idx < next_heading_idx
    # Human-authored 'open' line is untouched (append-only protocol).
    assert "*(Open — autosprint to decide.)*" in text
    # Marker carries the answer and the ADR reference.
    assert "pytest, unit-heavy" in lines[marker_idx]
    assert "`adr.md` 2026-05-16 — Test strategy" in lines[marker_idx]


def test_writeback_appends_receipt_and_removes_placeholder(target_repo: Path) -> None:
    """Write #3 (also BUG 2): a receipt bullet is appended to '## AI-resolved questions'
    and the seed '_No questions resolved yet._' placeholder is deleted on the first
    receipt."""
    _write_destination(target_repo)

    apply_destination_resolutions([{"section": "Test strategy", "answer": "pytest, unit-heavy", "adr_ref": "2026-05-16 — Test strategy"}])

    text = (target_repo / "autosprint" / "destination.md").read_text(encoding="utf-8")
    assert "_No questions resolved yet._" not in text
    assert "- **Test strategy:** pytest, unit-heavy. See `adr.md` 2026-05-16 — Test strategy." in text
    # Receipt landed inside the AI-resolved-questions section, before the next heading.
    assert text.index("- **Test strategy:**") < text.index("## AI-generated subgoals")


def test_writeback_missing_section_warns_and_does_not_raise(target_repo: Path) -> None:
    """A resolution naming a '## <section>' heading that doesn't exist must be logged
    loudly and skipped — it must NOT raise and must NOT corrupt the file. The code work
    behind the resolution is already correct, so a bad section name can't fail the sprint."""
    _write_destination(target_repo)

    # Must not raise.
    apply_destination_resolutions([{"section": "Nonexistent section", "answer": "x", "adr_ref": "y"}])

    text = (target_repo / "autosprint" / "destination.md").read_text(encoding="utf-8")
    # No status marker written (the section was missing) and no receipt for it either.
    assert "> **Status:** resolved" not in text
    assert "- **Nonexistent section:**" not in text


def test_writeback_handles_multiple_resolutions_in_one_call(target_repo: Path) -> None:
    """A single call resolving two sections must write both markers and both receipts —
    and the line-index shift from the first marker insert must not corrupt the second."""
    _write_destination(target_repo)

    apply_destination_resolutions(
        [
            {"section": "Test strategy", "answer": "pytest", "adr_ref": "ADR-A"},
            {"section": "Documentation quality", "answer": "docstrings everywhere", "adr_ref": "ADR-B"},
        ]
    )

    text = (target_repo / "autosprint" / "destination.md").read_text(encoding="utf-8")
    assert text.count("> **Status:** resolved") == 2
    assert "pytest" in text
    assert "docstrings everywhere" in text
    assert "- **Test strategy:** pytest. See `adr.md` ADR-A." in text
    assert "- **Documentation quality:** docstrings everywhere. See `adr.md` ADR-B." in text
    # Each marker landed in its own section: the Test-strategy marker before the
    # Documentation-quality heading, the Documentation-quality marker before AI-resolved.
    ts_marker = text.index("> **Status:** resolved")
    doc_heading = text.index("## Documentation quality")
    assert ts_marker < doc_heading


def test_writeback_heading_match_tolerant_of_leading_hashes(target_repo: Path) -> None:
    """The 'section' value may arrive with or without a leading '## ' — both must resolve
    to the same section heading."""
    _write_destination(target_repo)

    apply_destination_resolutions([{"section": "## Test strategy", "answer": "pytest", "adr_ref": "ADR-A"}])

    text = (target_repo / "autosprint" / "destination.md").read_text(encoding="utf-8")
    assert "> **Status:** resolved" in text
    # Receipt tag drops the leading '## ' so the bullet reads naturally.
    assert "- **Test strategy:** pytest. See `adr.md` ADR-A." in text


def test_writeback_empty_list_is_noop(target_repo: Path) -> None:
    """The common case: a sprint resolves nothing. An empty list must leave destination.md
    byte-identical."""
    path = _write_destination(target_repo)
    original = path.read_text(encoding="utf-8")

    apply_destination_resolutions([])

    assert path.read_text(encoding="utf-8") == original


def test_writeback_noop_in_fake_implement_mode(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    """FAKE_IMPLEMENT runs must not touch destination.md — mirrors the other run_log guards."""
    monkeypatch.setattr(config, "FAKE_IMPLEMENT", True)
    path = _write_destination(target_repo)
    original = path.read_text(encoding="utf-8")

    apply_destination_resolutions([{"section": "Test strategy", "answer": "pytest", "adr_ref": "ADR-A"}])

    assert path.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# parse_implement_result — resolved_open_questions field tolerance
# ---------------------------------------------------------------------------


def test_parse_implement_result_field_absent_yields_empty_list() -> None:
    """The common case: a success result with no `resolved_open_questions` key must
    parse to an empty list, not raise."""
    result = parse_implement_result('---RESULT---\n{"status": "success", "summary": "did the thing"}\n---END---')
    assert result == {"status": "success", "summary": "did the thing", "resolved_open_questions": []}


def test_parse_implement_result_field_present_yields_list_of_dicts() -> None:
    """When `resolved_open_questions` is present it parses to a list of normalised
    `{section, answer, adr_ref}` dicts."""
    raw = '---RESULT---\n{"status": "success", "summary": "s", "resolved_open_questions": [{"section": "Test strategy", "answer": "pytest", "adr_ref": "ADR-A"}]}\n---END---'
    result = parse_implement_result(raw)
    assert result["resolved_open_questions"] == [{"section": "Test strategy", "answer": "pytest", "adr_ref": "ADR-A"}]


# ---------------------------------------------------------------------------
# last-test-output persistence (warnings surfaced to next sprint's team lead)
# ---------------------------------------------------------------------------


def test_extract_test_output_highlights_picks_up_warnings_section() -> None:
    stdout = "tests/test_foo.py::test_bar PASSED\n\n============== warnings summary ==============\ntests/test_foo.py::test_bar\n  /path/to/mod.py:42: DeprecationWarning: foo is deprecated\n\n-- Docs: https://...\n============== 1 passed, 1 warning in 0.10s ==============\n"
    out = _extract_test_output_highlights(stdout)
    assert "warnings summary" in out.lower()
    assert "DeprecationWarning" in out
    assert "1 passed, 1 warning" in out


def test_extract_test_output_highlights_no_warnings_returns_summary_only() -> None:
    stdout = "tests/test_foo.py::test_bar PASSED\n\n======= 1 passed in 0.05s =======\n"
    out = _extract_test_output_highlights(stdout)
    assert "1 passed" in out
    assert "warnings summary" not in out.lower()


def test_read_last_test_output_returns_warnings_when_previous_sprint_passed(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    monkeypatch.setattr(config, "FAKE_IMPLEMENT", False)
    stdout_with_warnings = "============== warnings summary ==============\ntests/test_foo.py::test_bar\n  /mod.py:42: DeprecationWarning: old thing\n\n-- Docs: https://docs.pytest.org\n============== 1 passed, 1 warning in 0.10s ==============\n"
    _write_last_test_output(1, "PASS", stdout_with_warnings)
    content = _read_last_test_output()
    # New behaviour: passing-run warnings are surfaced so the team lead sees growing warning counts.
    assert content != ""
    assert "previous sprint PASSED" in content
    assert "DeprecationWarning" in content


def test_read_last_test_output_returns_empty_for_passing_run_with_no_warnings(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    monkeypatch.setattr(config, "FAKE_IMPLEMENT", False)
    # For a passing run the summary line is surfaced so the team lead always
    # sees at least the pass count. Only truly empty output (no summary, no
    # warnings) should return "".
    _write_last_test_output(1, "PASS", "======= 1 passed in 0.05s =======\n")
    content = _read_last_test_output()
    # Summary line must be present, wrapped in the PASS header.
    assert "previous sprint PASSED" in content
    assert "1 passed in 0.05s" in content


def test_read_last_test_output_surfaces_failure_context(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    monkeypatch.setattr(config, "FAKE_IMPLEMENT", False)
    # `_extract_test_output_highlights` keeps the final `=` summary line, so that's what ends up in the log.
    _write_last_test_output(2, "FAIL", "FAILED tests/test_foo.py::test_bar - AssertionError\n======= 1 failed in 0.08s =======\n")
    content = _read_last_test_output()
    assert "outcome=FAIL" in content
    assert "1 failed in 0.08s" in content


def test_write_last_test_output_is_noop_in_fake_mode(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    monkeypatch.setattr(config, "FAKE_IMPLEMENT", True)
    _write_last_test_output(1, "PASS", "======= 1 passed in 0.05s =======\n")
    assert not (target_repo / LAST_TEST_OUTPUT_FILENAME).exists()


def test_read_last_test_output_returns_empty_when_missing(target_repo: Path) -> None:
    assert _read_last_test_output() == ""


# ---------------------------------------------------------------------------
# _trim_console_verbose_log — off-by-one regression + round-trip cap check
# ---------------------------------------------------------------------------


def test_trim_console_verbose_log_stays_under_cap(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    """After trimming, the file must fit within the cap. Previously an off-by-one
    advanced `kept` twice when the candidate fit, dropping one extra run block."""
    log_path = target_repo / CONSOLE_LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Build a log with 5 run blocks, each ~200 bytes. Set cap to keep ~2 blocks.
    block = "x" * 180
    text = "\n# === run started 2026-01-01T00:00:00Z ===\n" + block
    for i in range(2, 6):
        text += f"\n# === run started 2026-01-0{i}T00:00:00Z ===\n" + block

    cap = len(text.encode("utf-8")) // 3  # keep roughly the newest third
    monkeypatch.setattr(config, "CONSOLE_LOG_MAX_BYTES", cap)
    log_path.write_text(text, encoding="utf-8")

    _trim_console_verbose_log()

    result = log_path.read_text(encoding="utf-8")
    assert len(result.encode("utf-8")) <= cap
    # Newest run block must be preserved.
    assert "2026-01-05" in result


def test_trim_console_verbose_log_preserves_at_least_one_block(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    """When the cap is very small (smaller than a single run block), trim stops at
    2 segments (never cuts mid-run) and the file will exceed the cap rather than
    losing the only run block."""
    log_path = target_repo / CONSOLE_LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)

    text = "\n# === run started 2026-01-01T00:00:00Z ===\n" + "x" * 500
    text += "\n# === run started 2026-01-02T00:00:00Z ===\n" + "x" * 500

    monkeypatch.setattr(config, "CONSOLE_LOG_MAX_BYTES", 10)  # impossibly small cap
    log_path.write_text(text, encoding="utf-8")

    _trim_console_verbose_log()

    result = log_path.read_text(encoding="utf-8")
    assert "2026-01-02" in result  # newest run still present


# ---------------------------------------------------------------------------
# _trim_plan_decisions_log — round-trip cap check
# ---------------------------------------------------------------------------


def test_trim_plan_decisions_log_keeps_last_n_entries(monkeypatch: pytest.MonkeyPatch, target_repo: Path) -> None:
    """After trimming, exactly `cap` sprint entries remain (plus the preamble)."""
    monkeypatch.setattr(config, "PLAN_DECISIONS_RECENT_COUNT", 2)

    log_path = target_repo / PLAN_DECISIONS_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    content = "# Plan decisions\n"
    for i in range(1, 6):
        content += f"\n## 2026-01-0{i}T00:00:00Z — myteam\n\nsome entry {i}\n"
    log_path.write_text(content, encoding="utf-8")

    _trim_plan_decisions_log()

    result = log_path.read_text(encoding="utf-8")
    # Only the 2 newest entries should remain.
    assert "2026-01-04" in result
    assert "2026-01-05" in result
    assert "2026-01-01" not in result
    assert "2026-01-02" not in result
    assert "2026-01-03" not in result


# ---------------------------------------------------------------------------
# recent_sprint_history — deduplication of dual-write rows
# ---------------------------------------------------------------------------


def test_recent_sprint_history_deduplicates_pending_and_commit_rows(target_repo: Path) -> None:
    """A successful sprint writes two rows per task (OK|pending then OK|OK|<hash>).
    recent_sprint_history must return only one row per (sprint, task) pair,
    preferring the commit-hash row."""
    log_path = target_repo / SPRINT_LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Simulate two sprints, each writing intermediate + commit rows.
    rows = [
        "1 | 2026-01-01T00:00:00Z |  3 | Add feature X (3) | OK | pending | some summary",
        "1 | 2026-01-01T00:00:01Z |  3 | Add feature X (3) | OK | OK      | abc1234",
        "2 | 2026-01-02T00:00:00Z |  2 | Fix bug Y (2) | OK | pending | another summary",
        "2 | 2026-01-02T00:00:01Z |  2 | Fix bug Y (2) | OK | OK      | def5678",
    ]
    log_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    history = recent_sprint_history(n=10)
    lines = [ln for ln in history.splitlines() if ln.strip()]
    assert len(lines) == 2  # 2 sprints, not 4 rows
    assert all("OK      | " in ln or "OK | OK" in ln for ln in lines)  # commit rows preferred
    assert "abc1234" in history
    assert "def5678" in history
    assert "pending" not in history
