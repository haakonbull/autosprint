"""Path / filename constants for the autosprint working layout in TARGET_REPO.

Pure-data leaf module imported by orchestrator.py and init.py. Lives here
(rather than inside orchestrator.py) so init.py can import these without
pulling orchestrator and creating a circular import.
"""

from __future__ import annotations

AUTOSPRINT_DIR_NAME = "autosprint"
LOGS_SUBDIR = f"{AUTOSPRINT_DIR_NAME}/logs"
INPUTS_SUBDIR = f"{AUTOSPRINT_DIR_NAME}/inputs"
DOCS_SUBDIR = "docs"  # at TARGET_REPO root (not inside autosprint/) — project-level docs / reference material

SPRINT_LOG_FILENAME = f"{LOGS_SUBDIR}/sprint-outcomes.log"
PLAN_DECISIONS_FILENAME = f"{LOGS_SUBDIR}/plan-decisions.md"
PREFLIGHT_LOG_FILENAME = f"{LOGS_SUBDIR}/preflight-tests.log"
IMPLEMENT_FAILURES_LOG_FILENAME = f"{LOGS_SUBDIR}/implement-failures.log"
LAST_TEST_OUTPUT_FILENAME = f"{LOGS_SUBDIR}/last-test-output.log"
LAST_RUN_SUMMARY_FILENAME = f"{LOGS_SUBDIR}/last-run-summary.md"
RUNTIME_STATS_FILENAME = f"{LOGS_SUBDIR}/runtime-stats.md"
LAST_IMPLEMENT_FAILURE_FILENAME = f"{LOGS_SUBDIR}/last-implement-failure.txt"

STOP_CONTROL_FILENAME = f"{AUTOSPRINT_DIR_NAME}/stop"
STOP_NOW_CONTROL_FILENAME = f"{AUTOSPRINT_DIR_NAME}/stop-now"

ADR_FILENAME = f"{AUTOSPRINT_DIR_NAME}/adr.md"  # user-facing, checked in — lives under autosprint/
CHANGELOG_FILENAME = f"{AUTOSPRINT_DIR_NAME}/changelog.md"  # autosprint-authored, checked in — one entry per committed sprint; folded into the sprint commit so it survives a rebase squash
DESTINATION_FILENAME = f"{AUTOSPRINT_DIR_NAME}/destination.md"  # user-facing, checked in — lives under autosprint/
WAYPOINT_FILENAME = f"{AUTOSPRINT_DIR_NAME}/waypoint.md"  # optional — user-set intermediate target. Plan phase aims here exclusively when present and not paused. Loop halts (with status marker appended to the file) when team lead signals waypoint_reached. Pause-by-rename gesture: `waypoint.md.paused` is treated as absent.
WAYPOINT_PAUSED_SUFFIX = ".paused"
