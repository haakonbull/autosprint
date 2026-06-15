"""PIT loop orchestrator — Plan, Implement, Test, Commit.

This module contains the live PIT loop (`pit_loop`), per-sprint commit
finalization (`commit_sprint`), the plan-only dry-run mode (`plan_only`),
and the synchronous `main()` entry point. Everything else — phase logic,
prompts, logging, CLI parsing, init, banners, git wrappers — lives in
sibling modules and is re-exported below so existing
`from autosprint.orchestrator import _foo` paths in tests keep resolving.

When monkeypatching internal helpers in tests, patch the **home** module
(e.g. `autosprint.plan_phase`, not `autosprint.orchestrator`) — the
re-export is a name alias; the function looks up its dependencies in its
own module's `__globals__`.
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone

from autosprint.config import config
from autosprint.errors import StopSignalDetected, add_context
from autosprint.output import printlev, speak
from autosprint.plan import PLAN_FILENAME, CompletedTask, PendingTask, Plan, defer_pending_tasks, format_full_plan, read_plan_md, serialise_plan, write_plan_md

# ---------------------------------------------------------------------------
# Imports — only the names this orchestrator body actually uses. Phase logic,
# CLI parsing, init, banners, git wrappers live in sibling modules and are
# imported directly by their callers; tests likewise import from the home
# module rather than from here.
# ---------------------------------------------------------------------------

from autosprint.banners import iteration_banner as _iteration_banner  # noqa: E402
from autosprint.cli import _ONESHOT_COMMANDS, check_stop_request as _check_stop_request, prepare, raise_if_stop_between_phases as _raise_if_stop_between_phases  # noqa: E402
from autosprint.errors import PhaseFailedError, RevertReason, StopRequested, WaypointReached, revert_reason_shrinks_cap as _revert_reason_shrinks_cap  # noqa: E402
from autosprint.git_ops import get_commit_hash, git, git_commit, git_restore  # noqa: E402
from autosprint.how_far import run_howfar_heartbeat  # noqa: E402
from autosprint.implement_phase import run_implement  # noqa: E402
from autosprint.paths import CHANGELOG_FILENAME, DESTINATION_FILENAME, WAYPOINT_FILENAME  # noqa: E402
from autosprint.plan import group_titles as _group_titles  # noqa: E402
from autosprint.plan_phase import SprintRevertRecord, build_post_revert_hint as _build_post_revert_hint, plan_phase, update_plan, waypoint_title as _waypoint_title  # noqa: E402
from autosprint.run_log import append_changelog_entry, append_run_log, apply_destination_resolutions, extract_story_points as _extract_story_points, log_outcome_per_task as _log_outcome_per_task, print_run_summary as _print_run_summary, review_sprint as _review_sprint, stale_task_titles as _stale_task_titles, task_revert_sprint_count as _task_revert_sprint_count, update_runtime_stats as _update_runtime_stats, write_run_ended_separator, write_run_separator  # noqa: E402
from autosprint.test_phase import run_test_phase  # noqa: E402

# ---------------------------------------------------------------------------
# PIT-loop core: commit finalization, manual-review prompt, the loop itself.
# ---------------------------------------------------------------------------


_TEMPORAL_BLOCKER_REASON_TERMS = (
    "not published",
    "not yet published",
    "not available",
    "not yet available",
    "postdates",
    "precedes",
    "before the",
    "before official",
    "future event",
    "future print",
    "release-gated",
    "publication",
    "published",
    "external publication",
    "official print",
    "does not exist",
    "404",
)

_TEMPORAL_TASK_TERMS = (
    "do not start before",
    "after publication",
    "after release",
    "after the release",
    "once live",
    "when live",
    "once it prints",
    "on print",
    "after print",
    "publication",
    "published",
    "official print",
    "fomc",
    "section 232",
    "q2",
    "earnings",
    "nvidia",
    "eu ai act",
)


def _is_temporal_blocker_reason(message: str) -> bool:
    text = message.lower()
    return any(term in text for term in _TEMPORAL_BLOCKER_REASON_TERMS)


def _looks_future_gated_task(task: dict) -> bool:
    text = f"{task.get('title', '')}\n{task.get('description', '')}".lower()
    return any(term in text for term in _TEMPORAL_TASK_TERMS)


def _deferable_blocked_titles(task_group: list[dict] | None, task_failure_counts: dict[str, int], failure_message: str) -> list[str]:
    """Return task titles that should be moved out of Pending after repeated temporal blockers.

    Multi-task groups can contain innocent co-bundled tasks that only reverted because
    a sibling was blocked. Only defer tasks whose own title/description looks
    future-publication gated; for a single-task group the reason is sufficient.
    """
    if not task_group or config.DEFER_BLOCKED_TASK_AFTER_FAILURES <= 0:
        return []
    if not _is_temporal_blocker_reason(failure_message):
        return []
    threshold = config.DEFER_BLOCKED_TASK_AFTER_FAILURES
    eligible = [t for t in task_group if max(task_failure_counts.get(t["title"], 0), _task_revert_sprint_count(t["title"])) >= threshold]
    if not eligible:
        return []
    if len(task_group) == 1:
        return [eligible[0]["title"]]
    return [t["title"] for t in eligible if _looks_future_gated_task(t)]


def _defer_blocked_tasks_if_needed(task_group: list[dict] | None, task_failure_counts: dict[str, int], failure_message: str, sprint_number: int) -> list[str]:
    titles = _deferable_blocked_titles(task_group, task_failure_counts, failure_message)
    if not titles:
        return []
    defer_pending_tasks(config.TARGET_REPO_PATH, titles, failure_message, sprint_number, recent_count=config.PLAN_RECENT_COMPLETED_COUNT)
    for title in titles:
        task_failure_counts.pop(title, None)
    printlev(f"[R] Deferred blocked task(s) after repeated temporal blocker: {', '.join(titles)}", level=100)
    if config.COMMIT_SUCCESSFUL_SPRINTS:
        git("add", PLAN_FILENAME)
        git("commit", "-m", f"[autosprint] Defer blocked task(s)\n\nMoved future-gated task(s) out of Pending after repeated blocked implement attempts:\n- " + "\n- ".join(titles))
        printlev(f"[R] Deferred-task plan update committed: {get_commit_hash()}", level=100)
    return titles


def _quarantine_stale_tasks(task_failure_counts: dict[str, int], sprint_number: int) -> list[str]:
    """Move tasks that have reverted ``QUARANTINE_TASK_AFTER_FAILURES``+ times to Blocked / Deferred and continue.

    Called at the top of each sprint, before task selection: a task that keeps failing on real
    problems (not the temporal-blocker case `_defer_blocked_tasks_if_needed` already handles earlier,
    and not a refusal the fallback intercepts) is benched so the loop spends the next sprint on other
    work instead of re-attempting the stuck task. Replaces the old escalation that halted the whole
    run at 3 reverts. Only acts on titles still in the Pending queue, so a title already quarantined
    (now in Blocked / Deferred) is not re-detected. Deliberately does NOT touch `consecutive_failures`:
    the reverts that earned the quarantine were genuine, so a broad failure streak still trips the
    MAX_CONSECUTIVE_FAILURES hard stop."""
    titles = _stale_task_titles()
    if not titles:
        return []
    pending_titles = {task.title for task in read_plan_md(config.TARGET_REPO_PATH).pending}
    titles = [t for t in titles if t in pending_titles]
    if not titles:
        return []
    reason = f"Quarantined after {config.QUARANTINE_TASK_AFTER_FAILURES} sprint failures on the same task — moved out of Pending so the loop can continue. Review and re-scope, split, or drop it before returning it to Pending."
    defer_pending_tasks(config.TARGET_REPO_PATH, titles, reason, sprint_number, recent_count=config.PLAN_RECENT_COMPLETED_COUNT)
    for title in titles:
        task_failure_counts.pop(title, None)
    printlev(f"[R] 🪧 Quarantined stale task(s) after {config.QUARANTINE_TASK_AFTER_FAILURES} failures — moved to Blocked / Deferred, continuing: {', '.join(titles)}", level=100)
    if config.COMMIT_SUCCESSFUL_SPRINTS:
        git("add", PLAN_FILENAME)
        git("commit", "-m", f"[autosprint] Quarantine stale task(s)\n\nMoved repeatedly-failing task(s) out of Pending after {config.QUARANTINE_TASK_AFTER_FAILURES} sprint failures:\n- " + "\n- ".join(titles))
        printlev(f"[R] Quarantine plan update committed: {get_commit_hash()}", level=100)
    return titles


def commit_sprint(task_group: list[dict], plan: Plan, implement_result: dict, sprint_number: int, sprint_results: list[dict]) -> str:
    """Finalize a successful sprint for a task group (1+ tasks) — move each task to completed in plan.md, commit changes (unless COMMIT_SUCCESSFUL_SPRINTS is False), log one sprint-outcomes.log row per task (all sharing the same commit hash), and return the commit hash (or 'no-commit')."""
    try:
        summary = implement_result.get("summary", "")
        # Open-question writeback: the implementor names which destination.md sections it
        # resolved (in `resolved_open_questions`); autosprint deterministically appends
        # the status marker + receipt. Done before the commit so the destination.md edit
        # is staged into the same sprint commit below.
        resolved_open_questions = implement_result.get("resolved_open_questions") or []
        apply_destination_resolutions(resolved_open_questions)
        for _ in task_group:
            done = plan.pending.pop(0)
            plan.completed.append(CompletedTask(title=done.title, summary=summary, commit_hash=""))

        if config.COMMIT_SUCCESSFUL_SPRINTS:
            printlev(f"\n[C] All good — committing sprint {sprint_number}...")
            git_commit(task_group, implement_result["summary"])
            new_hash = get_commit_hash()
            for i in range(len(task_group)):
                plan.completed[-(len(task_group) - i)].commit_hash = new_hash
            write_plan_md(config.TARGET_REPO_PATH, plan, recent_count=config.PLAN_RECENT_COMPLETED_COUNT)
            append_changelog_entry(sprint_number, task_group, summary)
            git("add", PLAN_FILENAME)
            if (config.TARGET_REPO_PATH / CHANGELOG_FILENAME).exists():
                git("add", CHANGELOG_FILENAME)
            if resolved_open_questions and (config.TARGET_REPO_PATH / DESTINATION_FILENAME).exists():
                git("add", DESTINATION_FILENAME)
            git("commit", "--amend", "--no-edit")
            final_hash = get_commit_hash()
            printlev(f"[C] ✅ Committed: {final_hash}", level=100)
            for t in task_group:
                append_run_log(sprint_number, t["title"], "OK", "OK", final_hash)
            sprint_results.append({"sprint": sprint_number, "outcome": "ok", "task": _group_titles(task_group), "hash": final_hash})
            return final_hash

        write_plan_md(config.TARGET_REPO_PATH, plan, recent_count=config.PLAN_RECENT_COMPLETED_COUNT)
        printlev("\n[C] All good, but skipping commit because COMMIT_SUCCESSFUL_SPRINTS is False in config.", level=100)
        for t in task_group:
            append_run_log(sprint_number, t["title"], "OK", "OK", "NO_COMMIT")
        sprint_results.append({"sprint": sprint_number, "outcome": "ok", "task": _group_titles(task_group), "hash": "no-commit"})
        return "no-commit"
    except Exception as e:
        raise add_context(e, f"Failed to commit sprint {sprint_number} (task group '{_group_titles(task_group)}')") from e


def _prompt_task_approval(task_group: list[dict], plan: Plan) -> bool:
    """Returns True if the user approves the next task group at the manual-review prompt; False otherwise. Displays each task in the group when grouping is active."""
    try:
        if len(task_group) == 1:
            t = task_group[0]
            lines = [f"\n[Review] Next task: {t['title']}"]
            if t.get("description"):
                lines.append(f"  Description: {t['description']}")
        else:
            lines = [f"\n[Review] Next task group ({len(task_group)} tasks):"]
            for i, t in enumerate(task_group, 1):
                lines.append(f"  {i}. {t['title']}")
                if t.get("description"):
                    lines.append(f"     {t['description']}")
        lines.append(f"  Plan has {len(plan.pending)} pending task(s) total.")
        printlev("\n".join(lines), level=100)
        answer = input("Approve and run Implement? [Y/n]: ").strip().lower()
        return answer in ("", "y", "yes")
    except Exception as e:
        raise add_context(e, f"Failed to prompt for task-group approval: '{_group_titles(task_group)}'") from e


# (review_sprint / extract_story_points / persist_run_summary / print_run_summary
# / log_outcome_per_task moved to autosprint.run_log along with the rest of the
# logging cluster.)


async def _maybe_run_heartbeat(sprint_number: int) -> None:
    """Fire a how-far heartbeat when this sprint matches the configured cadence.
    Guarded so a missing or 0 cadence skips silently, and so sprint_number 0
    (we never finish sprint 0) never triggers. `run_howfar_heartbeat` swallows
    its own errors — this wrapper only decides whether to call it."""
    n = config.HOWFAR_HEARTBEAT_EVERY_N_SPRINTS
    if n <= 0 or sprint_number <= 0 or sprint_number % n != 0:
        return
    await run_howfar_heartbeat(sprint_number)


async def pit_loop(branch_name: str) -> None:
    """Run the Plan/Implement/Test/Commit loop until one of these exits fires:
    - `config.MAX_SPRINTS` reached (normal termination).
    - A stop signal (soft or `--now`) detected at a sprint boundary or between phases.
    - `consecutive_failures >= config.MAX_CONSECUTIVE_FAILURES` reverts raised as RuntimeError.
      (A single task that keeps failing no longer halts the run: at `QUARANTINE_TASK_AFTER_FAILURES`
      reverts it is moved to Blocked / Deferred via `_quarantine_stale_tasks` and the loop continues.)
    - User rejects the task at the manual-review prompt (MANUAL_REVIEW mode).

    Each sprint: plan → implement → test → commit on success, or revert + continue on any PhaseFailedError. Failure counters per task title support escalation; the whole-group atomic revert model means a task group is committed or reverted together, never partially.

    State invariants between sprints: `sprints_since_replan` increments on both success and failure (so replan cadence fires even during failure streaks); `prev_sprint_reverted` tells the next Plan phase whether preflight pytest is warranted; `task_failure_counts` is cleared per task on success but accumulates on revert.

    `branch_name` is only used in the outer exception's context message — branch creation happens earlier in `prepare()`.
    """
    try:
        write_run_separator()
        sprint_number = 0
        consecutive_failures = 0
        sprints_since_replan = 0
        first_sprint = True  # the first sprint always triggers a replan (see force_replan in plan_phase)
        task_failure_counts: dict[str, int] = {}
        sprint_results: list[dict] = []
        prev_sprint_reverted = False  # triggers pre-flight tests on the next plan phase
        # Adaptive task-count cap, starts at INITIAL, shrinks on real revert, grows on success.
        task_count_cap = max(config.SPRINT_TASK_COUNT_CAP_MIN, config.SPRINT_TASK_COUNT_CAP_INITIAL)
        consecutive_successes = 0  # cap recovers only after 2 consecutive green sprints
        # Sprint-revert records accumulate until each replan; cleared when replan fires.
        revert_records_since_replan: list[SprintRevertRecord] = []
        pending_post_revert_hint = ""  # set after a real revert, consumed at next replan
        loop_start = time.monotonic()
        exit_reason = "completed"  # mutated on early exits; written into sprint-outcomes.log at end

        while sprint_number < config.MAX_SPRINTS:
            stop_kind = _check_stop_request()
            if stop_kind is not None:
                printlev(f"\n[stop] {stop_kind.capitalize()} stop detected at sprint boundary. Exiting after {sprint_number} sprint(s).", level=100)
                exit_reason = f"stop signal ({stop_kind})"
                break
            # Reviewed-plan mode: the loop runs only the human-reviewed plan.md and can't
            # refill it. When it runs dry, exit cleanly here — otherwise empty-plan
            # sprints would cascade into the consecutive-failure abort.
            if not config.AUTO_REPLAN and read_plan_md(config.TARGET_REPO_PATH).is_empty():
                if sprint_number == 0:
                    printlev("\n[stop] plan.md has no pending tasks — nothing to run. Run `autosprint plan` to draft tasks, or `autosprint run --auto-replan` to plan as the loop goes.", level=100)
                else:
                    printlev(f"\n[stop] All reviewed tasks in plan.md are done after {sprint_number} sprint(s). Exiting.", level=100)
                exit_reason = "plan.md empty (reviewed plan done)"
                break
            sprint_number += 1
            sprint_start = time.monotonic()
            printlev(f"\n\n{_iteration_banner(sprint_number, 'START')}", level=100)
            wp_title = _waypoint_title()
            if wp_title:
                printlev(f"[wp] 🧭 Waypoint active: {wp_title} — Plan phase aims here exclusively until reached.", level=100)
            _quarantine_stale_tasks(task_failure_counts, sprint_number)
            task_group: list[dict] | None = None

            try:
                prior_sprints_since_replan = sprints_since_replan
                force_first_replan = first_sprint and not config.SKIP_FIRST_PLAN
                if first_sprint and config.SKIP_FIRST_PLAN:
                    printlev("[P] --skip-first-plan is set: reusing existing plan.md for sprint 1 (will still replan if plan.md is empty).", level=50)
                plan, task_group, sprints_since_replan = await plan_phase(sprints_since_replan, task_failure_counts, sprint_number=sprint_number, prev_sprint_reverted=prev_sprint_reverted, force_replan=force_first_replan, task_count_cap=task_count_cap, post_revert_hint=pending_post_revert_hint)
                # If this sprint triggered a replan (sprints_since_replan reset to 0),
                # consume the pending hint and clear the records — the planner has now seen them.
                if sprints_since_replan == 0 and prior_sprints_since_replan != 0 or first_sprint:
                    revert_records_since_replan.clear()
                    pending_post_revert_hint = ""
                first_sprint = False
                if not task_group:
                    # The Plan phase produced no executable task this sprint. In auto-replan mode this is the
                    # legitimate "nothing to do now" state — e.g. every remaining destination requirement is
                    # gated on a future external event (the planner is now instructed to keep such work out of
                    # Pending), or the plan is genuinely complete. Exit cleanly instead of spinning empty replans
                    # until MAX_SPRINTS. (Reviewed-plan mode exits earlier via the is_empty() guard above.)
                    printlev(f"\n[stop] Plan phase produced no executable tasks at sprint {sprint_number}. Nothing to run now — the plan may be complete, or all remaining work is gated on a future event. Exiting.", level=100)
                    printlev(f"\n{_iteration_banner(sprint_number, 'END')}", level=100)
                    speak("Autosprint stopping. No executable tasks.")
                    exit_reason = "no executable tasks (plan empty after replan)"
                    break
                task_count = len(task_group)
                speak(f"Sprint {sprint_number}: {task_count} {'task' if task_count == 1 else 'tasks'}.", tier="all")
                _raise_if_stop_between_phases()
                if config.MANUAL_REVIEW and not _prompt_task_approval(task_group, plan):
                    git_restore()
                    printlev("\n[Review] Task rejected. Exiting so you can edit plan.md or tweak prompts, then re-run.", level=100)
                    speak("Autosprint stopped. Task rejected at manual review.")
                    exit_reason = "manual review rejected task"
                    break
                implement_result = await run_implement(task_group, sprint_number)
                _raise_if_stop_between_phases()
                run_test_phase(task_group, sprint_number)
            except StopRequested as sr:
                speak("Autosprint stopping now by request.")
                git_restore()
                _log_outcome_per_task(sprint_number, task_group, "STOP", "STOP", f"STOP_NOW: {sr.kind}")
                printlev(f"\n[stop] Immediate stop mid-sprint at sprint {sprint_number}. Working tree reverted. Exiting.", level=100)
                printlev(f"\n{_iteration_banner(sprint_number, 'END')}", level=100)
                exit_reason = f"stop-now mid-sprint ({sr.kind})"
                break
            except StopSignalDetected:
                speak("Autosprint stopping now by request.")
                git_restore()
                _log_outcome_per_task(sprint_number, task_group, "STOP", "STOP", "STOP_NOW: stop-now detected mid-LLM-call")
                printlev(f"\n[stop] Stop-now detected mid-LLM-call at sprint {sprint_number}. Working tree reverted. Exiting.", level=100)
                printlev(f"\n{_iteration_banner(sprint_number, 'END')}", level=100)
                exit_reason = "stop-now mid-LLM-call"
                break
            except WaypointReached as wp:
                # Clean halt — Plan phase signalled the user-set waypoint is satisfied. No revert (no
                # implementation ran this sprint), no failure counter bump, no consecutive-failure tally.
                # The status marker has already been appended to waypoint.md inside update_plan, so the
                # user can open the file and see the rationale next to the waypoint definition.
                _log_outcome_per_task(sprint_number, task_group, "WAYPOINT", "WAYPOINT", f"WAYPOINT_REACHED: {wp.rationale[:80]}")
                printlev(f"\n[wp] 🏁 Waypoint reached at sprint {sprint_number}. See {WAYPOINT_FILENAME} for the appended status marker. Halting loop.", level=100)
                printlev(f"\n{_iteration_banner(sprint_number, 'END')}", level=100)
                speak("Autosprint stopping. Waypoint reached.")
                exit_reason = f"waypoint reached: {wp.rationale[:80]}"
                break
            except PhaseFailedError as pf:
                consecutive_failures += 1
                sprints_since_replan += 1
                group_label = _group_titles(task_group) if task_group else "(no task)"
                if task_group is not None:
                    for t in task_group:
                        task_failure_counts[t["title"]] = task_failure_counts.get(t["title"], 0) + 1
                deferred_titles = _defer_blocked_tasks_if_needed(task_group, task_failure_counts, str(pf), sprint_number)
                if deferred_titles:
                    consecutive_failures = 0
                    group_label = f"{group_label} [deferred: {', '.join(deferred_titles)}]"
                    sprint_results.append({"sprint": sprint_number, "outcome": "deferred", "task": group_label, "reason": str(pf)})
                else:
                    sprint_results.append({"sprint": sprint_number, "outcome": "reverted", "task": group_label, "reason": str(pf)})
                # Pre-flight next sprint only when Implement+Test actually ran
                # (task_group was set). Plan-only failures leave the tree untouched,
                # so a pre-flight survey would be wasted work.
                prev_sprint_reverted = task_group is not None and not deferred_titles
                # Shrink the adaptive task-count cap on real reverts only.
                # Parser-format reverts don't reflect bundle-size problems, so they
                # don't punish the loop.
                if _revert_reason_shrinks_cap(pf.revert_reason):
                    new_cap = max(config.SPRINT_TASK_COUNT_CAP_MIN, task_count_cap - 1)
                    if new_cap != task_count_cap:
                        printlev(f"[R] Task-count cap: {task_count_cap} → {new_cap} ({pf.revert_reason.value}; will recover +1 after 2 consecutive green sprints)", level=100)
                        task_count_cap = new_cap
                    consecutive_successes = 0  # reset on any real revert
                # Record this sprint in the post-replan hint builder.
                if not deferred_titles:
                    revert_records_since_replan.append(SprintRevertRecord(sprint_number=sprint_number, task_titles=[t["title"] for t in (task_group or [])], reason=pf.revert_reason, reason_message=str(pf)))
                pending_post_revert_hint = _build_post_revert_hint(revert_records_since_replan)
                # Guarantee at least one sprint-outcomes.log entry per sprint. Phase
                # helpers already log REVERTED when they can, but paths that fail
                # before a phase helper reaches its log call (e.g. plan fails with
                # no task, SDK raises mid-dispatch) would otherwise leave the sprint
                # invisible in the log.
                if deferred_titles:
                    _log_outcome_per_task(sprint_number, task_group, "DEFERRED", "DEFERRED", f"SPRINT_DEFERRED: {str(pf)[:80]}")
                    _review_sprint(sprint_number, "deferred", group_label, f"deferred blocked task(s): {', '.join(deferred_titles)}", consecutive_failures, sprints_since_replan)
                else:
                    _log_outcome_per_task(sprint_number, task_group, "FAILED", "FAILED", f"SPRINT_REVERTED: {str(pf)[:80]}")
                    _review_sprint(sprint_number, "reverted", group_label, str(pf), consecutive_failures, sprints_since_replan)
                sprint_sp = sum(sp for sp in (_extract_story_points(t["title"]) for t in (task_group or [])) if sp is not None)
                _update_runtime_stats(time.monotonic() - sprint_start, sprint_sp)
                speak(f"Sprint {sprint_number} reverted.", tier="reverts")
                printlev(f"\n{_iteration_banner(sprint_number, 'END')}", level=100)
                if consecutive_failures >= config.MAX_CONSECUTIVE_FAILURES:
                    _print_run_summary(sprint_results, time.monotonic() - loop_start)
                    write_run_ended_separator(f"aborted: {consecutive_failures} consecutive failures")
                    speak(f"Autosprint terminated early. {consecutive_failures} consecutive failures. Human input needed.")
                    raise RuntimeError(f"{consecutive_failures} consecutive sprint failures. Human input needed.")
                await _maybe_run_heartbeat(sprint_number)
                continue

            # Success — reset per-sprint state.
            assert task_group is not None  # guaranteed: we only reach this point after plan_phase returned
            consecutive_failures = 0
            consecutive_successes += 1
            sprints_since_replan += 1
            prev_sprint_reverted = False
            # Cap recovers +1 only after 2 consecutive green sprints (asymmetric:
            # shrinks immediately on revert, recovers slowly to avoid fail-succeed thrashing).
            if consecutive_successes >= 2:
                recovered_cap = min(config.SPRINT_TASK_COUNT_CAP_INITIAL, task_count_cap + 1)
                if recovered_cap != task_count_cap:
                    printlev(f"[R] Task-count cap: {task_count_cap} → {recovered_cap} ({consecutive_successes} consecutive green sprints; recovering toward initial {config.SPRINT_TASK_COUNT_CAP_INITIAL})", level=50)
                    task_count_cap = recovered_cap
                    consecutive_successes = 0  # reset after each +1 recovery step
            for t in task_group:
                task_failure_counts.pop(t["title"], None)

            # Record outcome: commit, review, stats, banner, audio.
            sprint_detail = commit_sprint(task_group, plan, implement_result, sprint_number, sprint_results)
            _review_sprint(sprint_number, "ok", _group_titles(task_group), sprint_detail, consecutive_failures, sprints_since_replan)
            sprint_sp = sum(sp for sp in (_extract_story_points(t["title"]) for t in task_group) if sp is not None)
            _update_runtime_stats(time.monotonic() - sprint_start, sprint_sp)
            printlev(f"\n{_iteration_banner(sprint_number, 'END')}", level=100)
            speak(f"Sprint {sprint_number} finished.", tier="sprints")
            await _maybe_run_heartbeat(sprint_number)

        # Normalize the "hit max sprints" case — reaching MAX_SPRINTS is a clean completion, not an early exit.
        completed_all_sprints = exit_reason == "completed" and sprint_number >= config.MAX_SPRINTS
        if completed_all_sprints:
            exit_reason = f"max sprints reached ({config.MAX_SPRINTS})"
        # A reviewed-plan run that exhausts its plan.md is a clean success — it ran
        # every task it was given. (sprint_number == 0 means plan.md was empty to begin with;
        # that is "nothing to do", not a completion — exclude it.)
        reviewed_plan_completed = exit_reason == "plan.md empty (reviewed plan done)" and sprint_number > 0
        _print_run_summary(sprint_results, time.monotonic() - loop_start)
        write_run_ended_separator(exit_reason)
        # Distinguish clean completion vs early termination by audio so the user can walk away
        # and know from sound alone whether autosprint needs attention. Silence = still running.
        if reviewed_plan_completed:
            speak(f"Autosprint reviewed plan complete. {sprint_number} sprints done.", wait=True)
        elif completed_all_sprints:
            speak(f"Autosprint completed correctly. {sprint_number} sprints processed.", wait=True)
        else:
            speak(f"Autosprint terminated early. Reason: {exit_reason}.", wait=True)
    except Exception as e:
        # Unexpected failure — speak before raising so the user hears this over walk-away distance.
        speak("Autosprint terminated early due to an unexpected failure.", wait=True)
        raise add_context(e, f'Failed during PIT loop on branch "{branch_name}"') from e


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------


def plan_only() -> None:
    """Run only the plan regeneration step, print the resulting pending tasks, and speak a completion notice (plan-only often runs unattended)."""
    try:
        printlev(f"Autosprint — Plan Only\n   Team: {config.TEAM} ({len(config.TEAM_AGENTS)} agents)\n")
        plan = asyncio.run(update_plan(config.TEAM_AGENTS, config.TEAM_SELECTOR, plan_only_mode=True))
        printlev(f"Updated plan ({len(plan.pending)} pending tasks):")
        for i, t in enumerate(plan.pending, 1):
            printlev(f"   {i}. {t.title}\n      {t.description}")
        # Spoken completion notice — plan-only often runs while the user is away from the
        # PC. speak() self-gates on SPEAK_LEVEL; wait=True lets the announcement finish
        # before the process exits.
        task_count = len(plan.pending)
        speak(f"Autosprint plan complete. {task_count} {'task' if task_count == 1 else 'tasks'} drafted.", wait=True)
    except Exception as e:
        raise add_context(e, f'Failed to run plan-only mode for team "{config.TEAM}"') from e


def main() -> None:
    try:
        args = prepare()
        if args.command in _ONESHOT_COMMANDS:
            return
        if args.command == "plan":
            plan_only()
            return
        asyncio.run(pit_loop(args.branch))
    except Exception as e:
        printlev(f"\n*** ERROR ***\n{e}")
        if config.DEBUG_TRACEBACK:
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
