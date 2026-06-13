"""Extracted from the original autosprint.reporting.run_log module."""

from datetime import UTC, datetime

from autosprint.config import config
from autosprint.infra.dispatch import get_claude_usage_estimate
from autosprint.reporting.run_log.outcomes import append_run_log
from autosprint.reporting.run_log.stats import extract_story_points, read_runtime_stats
from autosprint.util.errors import add_context
from autosprint.util.output import printlev
from autosprint.util.paths import (
    LAST_RUN_SUMMARY_FILENAME,
)


def review_sprint(sprint_number: int, outcome: str, task_title: str, detail: str, consecutive_failures: int, sprints_since_replan: int) -> None:
    """Print the per-sprint [R]esult verdict, a narrative recap on success, and escalation counters."""
    try:
        mark = "✅" if outcome == "ok" else "❌"
        short_detail = detail if len(detail) <= 80 else detail[:77] + "..."
        sp = extract_story_points(task_title)
        sp_tag = f" [SP={sp}]" if sp is not None else ""
        printlev(f"\n[R] {mark} Sprint {sprint_number}{sp_tag}: {task_title} ({short_detail})", level=100)
        if outcome == "ok":
            tail = f"committed as {short_detail}" if short_detail != "no-commit" else "commit skipped by config"
            printlev(f"[R] 🏁 Sprint {sprint_number} finished — found task '{task_title}', implemented it, ran the tests, all tests passed, {tail}.", level=100)
        printlev(f"[R] Consecutive failures: {consecutive_failures}/{config.MAX_CONSECUTIVE_FAILURES} | Sprints since replan: {sprints_since_replan}/{config.REPLAN_EVERY_N_SPRINTS}", level=20)
    except Exception as e:
        raise add_context(e, f"Failed to print review for sprint {sprint_number}") from e


def persist_run_summary(rendered: str) -> None:
    """Write the end-of-run dashboard to autosprint/logs/last-run-summary.md so it survives the session — if the user walks away and the terminal scrolls past, the summary is still recoverable from disk. Overwrites each run; no historical archive (git history of the file gives that for free if it's committed). No-op in FAKE_IMPLEMENT mode."""
    if config.FAKE_IMPLEMENT:
        return
    try:
        log_path = config.TARGET_REPO_PATH / LAST_RUN_SUMMARY_FILENAME
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        log_path.write_text(f"# Last autosprint run summary\n\n_Written at {ts}._\n\n```\n{rendered}\n```\n", encoding="utf-8")
    except Exception as e:
        raise add_context(e, "Failed to persist run summary to last-run-summary.md") from e


def print_run_summary(results: list[dict], elapsed_sec: float) -> None:
    try:
        if not results:
            return
        succeeded = 0
        reverted = 0
        succeeded_sp: list[int] = []
        reverted_sp: list[int] = []
        size_counts: dict[int, int] = {}
        lines: list[str] = [f"\n{'-' * 23} Run summary {'-' * 23}"]
        for r in results:
            sp = extract_story_points(r.get("task", ""))
            if sp is not None:
                size_counts[sp] = size_counts.get(sp, 0) + 1
            if r["outcome"] == "ok":
                succeeded += 1
                if sp is not None:
                    succeeded_sp.append(sp)
                lines.append(f"Sprint {r['sprint']:>3}: ✅ {r['task'][:50]} ({r['hash']})")
            else:
                reverted += 1
                if sp is not None:
                    reverted_sp.append(sp)
                reason_first_line = r["reason"].splitlines()[0] if r.get("reason") else ""
                lines.append(f"Sprint {r['sprint']:>3}: ❌ {r['task'][:50]} ({reason_first_line[:90]})")
        sprints_run = succeeded + reverted
        elapsed_min = elapsed_sec / 60
        avg_this_run = (elapsed_sec / sprints_run) if sprints_run else 0.0
        rolling_avg, rolling_count, rolling_total_sp = read_runtime_stats()
        rolling_sp_line = f", {(rolling_avg * rolling_count) / rolling_total_sp:.1f}s/SP" if rolling_total_sp > 0 else ""
        rolling_line = f" | rolling avg {rolling_avg:.1f}s/sprint{rolling_sp_line} over {rolling_count} sprint(s)" if rolling_count else ""
        lines.append(f"\n{succeeded} completed, {reverted} reverted, {sprints_run} sprints, {elapsed_min:.1f} min elapsed (this run avg {avg_this_run:.1f}s/sprint){rolling_line}")
        revert_pct = (reverted / sprints_run * 100) if sprints_run else 0.0
        lines.append(f"Revert rate: {revert_pct:.0f}% ({reverted}/{sprints_run})")
        if succeeded_sp or reverted_sp:
            succ_str = f"completed avg: {sum(succeeded_sp) / len(succeeded_sp):.1f} (n={len(succeeded_sp)})" if succeeded_sp else "completed avg: n/a"
            rev_str = f"reverted avg: {sum(reverted_sp) / len(reverted_sp):.1f} (n={len(reverted_sp)})" if reverted_sp else "reverted avg: n/a"
            lines.append(f"Story points — {succ_str}, {rev_str}")
        if size_counts:
            dist = "  ".join(f"({sp})×{c}" for sp, c in sorted(size_counts.items()))
            lines.append(f"Size distribution (attempted): {dist}")
        lines.append(f"Story-point band in effect: [{config.SPRINT_STORY_POINT_MIN}, {config.SPRINT_STORY_POINT_MAX}]  (tune in .env if revert rate is out of your target band)")
        usage = get_claude_usage_estimate()
        if usage["total_calls"] > 0:
            cache_note = f", {usage['cache_hits']} cache hits (free)" if usage["cache_hits"] else ""
            pct_note = ""
            if config.CLAUDE_TOKEN_LIMIT > 0:
                pct = (usage["estimated_tokens"] / config.CLAUDE_TOKEN_LIMIT) * 100
                pct_note = f" ({pct:.1f}% of CLAUDE_TOKEN_LIMIT={config.CLAUDE_TOKEN_LIMIT:,})"
            lines.append(f"Claude usage: {usage['total_calls']} live calls{cache_note}, ~{usage['estimated_tokens']:,} tokens estimated{pct_note} ({usage['prompt_chars']:,} in + {usage['response_chars']:,} out, 4-chars-per-token heuristic — actual may vary ±30%; Copilot calls excluded — flat-rate subscription)")
            lines.append("For precise subscription-level numbers, run `/usage` in Claude Code before the run and again now — the delta is this run's exact Claude consumption.")
        lines.append(f"{'=' * 24} Run summary {'=' * 28}")
        rendered = "\n".join(lines)
        printlev(rendered)
        persist_run_summary(rendered)
    except Exception as e:
        raise add_context(e, f"Failed to print run summary (results={len(results)}, elapsed_sec={elapsed_sec:.1f})") from e


def log_outcome_per_task(sprint_number: int, task_group: list[dict] | None, implement_status: str, test_status: str, outcome_msg: str) -> None:
    """Write one sprint-outcomes.log row per task in the group, or a single row with a '(no task)' title when the sprint aborted before a task group was assigned (e.g. Plan phase failed). Guarantees the sprint is never silent in the log."""
    if task_group:
        for t in task_group:
            append_run_log(sprint_number, t["title"], implement_status, test_status, outcome_msg)
    else:
        append_run_log(sprint_number, "(no task)", implement_status, test_status, outcome_msg)
