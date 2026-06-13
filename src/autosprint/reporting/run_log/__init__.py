"""Run logging: sprint-outcomes log, plan-decisions log, runtime stats, escalation.

Owns everything that gets written under TARGET_REPO/autosprint/logs/ as the
PIT loop runs:
- `sprint-outcomes.log` rows (one per attempted task) and the run-start /
  run-end separators that bracket each invocation.
- `plan-decisions.md` decision history with cap trimming.
- `console-verbose.log` size-cap trimming.
- `runtime-stats.md` rolling average for ETA estimates.
- `last-run-summary.md` end-of-run dashboard.
- `changelog.md` — the one *committed* run-record (lives at `autosprint/` root,
  not `logs/`); one entry appended per successful sprint so the run history
  survives a `git rebase -i` squash.

Plus the helpers that read those logs back: `recent_sprint_history` (used in
the planner prompt), `check_escalation` (raises when the same task has
reverted 3× in the last 20 entries), `task_attempt_stats` (per-task counts
for the planner's task-history section)."""

# Re-export the full public surface so existing import paths keep working.
from autosprint.reporting.run_log.changelog import _CHANGELOG_RUN_HEADING_WRITTEN, append_changelog_entry  # noqa: F401
from autosprint.reporting.run_log.destination import _RESOLVED_QUESTIONS_HEADING, _RESOLVED_QUESTIONS_PLACEHOLDER, _section_bounds, apply_destination_resolutions  # noqa: F401
from autosprint.reporting.run_log.history import check_escalation, recent_sprint_history, task_attempt_stats  # noqa: F401
from autosprint.reporting.run_log.maintenance import log_plan_decision, trim_console_verbose_log, trim_plan_decisions_log  # noqa: F401
from autosprint.reporting.run_log.outcomes import _RUN_LOG_HEADER, append_run_log, write_run_ended_separator, write_run_separator  # noqa: F401
from autosprint.reporting.run_log.stats import STORY_POINT_PATTERN, estimated_runtime_line, extract_story_points, read_runtime_stats, update_runtime_stats, write_runtime_stats  # noqa: F401
from autosprint.reporting.run_log.summary import log_outcome_per_task, persist_run_summary, print_run_summary, review_sprint  # noqa: F401
