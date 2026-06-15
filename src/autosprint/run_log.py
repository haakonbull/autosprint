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
the planner prompt), `stale_task_titles` (titles that have reverted
QUARANTINE_TASK_AFTER_FAILURES× in the last 20 entries, which the loop
quarantines into Blocked / Deferred), `task_attempt_stats` (per-task counts
for the planner's task-history section).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from autosprint import db
from autosprint.config import config
from autosprint.dispatch import get_claude_usage_estimate
from autosprint.errors import add_context
from autosprint.output import printlev
from autosprint.paths import (
    CHANGELOG_FILENAME,
    DESTINATION_FILENAME,
    LAST_RUN_SUMMARY_FILENAME,
    PLAN_DECISIONS_FILENAME,
    RUNTIME_STATS_FILENAME,
    SPRINT_LOG_FILENAME,
)
from autosprint.plan import Plan, group_titles, read_plan_md

STORY_POINT_PATTERN = re.compile(r"\((\d+)\)\s*$")

_RUN_LOG_HEADER = "# sprint | timestamp            | sp | title                                             | implement | test     | outcome\n"

# Run-scoped flag: True once this process has emitted the `## Run …` heading to
# changelog.md, so subsequent sprint entries in the same run skip re-emitting
# it. Resets only when the process restarts (a new autosprint run), which is
# exactly the granularity we want — one `## Run …` heading per invocation. This
# mirrors how `write_run_separator()` brackets each run in sprint-outcomes.log.
_CHANGELOG_RUN_HEADING_WRITTEN: bool = False


# ---------------------------------------------------------------------------
# sprint-outcomes.log
# ---------------------------------------------------------------------------


def append_run_log(sprint_number: int, task_title: str, implement_status: str, test_status: str, outcome: str, revert_reason: str | None = None, recovered_by_fallback: str | None = None) -> None:
    """Append a single sprint outcome line to sprint-outcomes.log; writes a header line on first creation. `sprint_number` is the first column in the emitted row. `revert_reason` and `recovered_by_fallback` are propagated only to the SQLite mirror (the markdown log keeps its 5-column shape so existing readers/grep patterns stay valid). No-op in FAKE_IMPLEMENT mode so fake runs don't pollute the real run history used by escalation and plan-agent context."""
    if config.FAKE_IMPLEMENT:
        return
    log_path = config.TARGET_REPO_PATH / SPRINT_LOG_FILENAME
    sp = extract_story_points(task_title)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        is_new = not log_path.exists()
        with log_path.open("a", encoding="utf-8") as f:
            if is_new:
                f.write(_RUN_LOG_HEADER)
            sp_str = f"{sp:2d}" if sp is not None else " ?"
            f.write(f"{sprint_number} | {ts} | {sp_str} | {task_title} | {implement_status} | {test_status} | {outcome}\n")
    except Exception as e:
        raise add_context(e, f'Failed to log line for task "{task_title}" to {log_path}') from e
    db.record_task_attempt(sprint_number, task_title, sp, implement_status, test_status, outcome, revert_reason, recovered_by_fallback)


def write_run_separator() -> None:
    """Write a '# === run started <ts> ===' comment to sprint-outcomes.log so new autosprint runs are visually distinguishable when scrolling the file. Writes the header first if the file doesn't exist. Also opens a row in the SQLite mirror via `db.record_run_start`. No-op in FAKE_IMPLEMENT mode."""
    if config.FAKE_IMPLEMENT:
        return
    log_path = config.TARGET_REPO_PATH / SPRINT_LOG_FILENAME
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        is_new = not log_path.exists()
        with log_path.open("a", encoding="utf-8") as f:
            if is_new:
                f.write(_RUN_LOG_HEADER)
            f.write(f"# === run started {ts} ===\n")
    except Exception as e:
        raise add_context(e, f"Failed to write run separator to {log_path}") from e
    db.record_run_start(str(config.TARGET_REPO_PATH), config.TEAM, config.IMPLEMENT_AGENT)


def write_run_ended_separator(exit_reason: str) -> None:
    """Write a '# === run ended <ts> ({exit_reason}) ===' comment to sprint-outcomes.log so post-hoc readers can tell a clean termination from a process that died mid-run. Pairs with `write_run_separator()` which marks the start. Also closes the SQLite mirror row via `db.record_run_end`. No-op in FAKE_IMPLEMENT mode."""
    if config.FAKE_IMPLEMENT:
        return
    log_path = config.TARGET_REPO_PATH / SPRINT_LOG_FILENAME
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"# === run ended {ts} ({exit_reason}) ===\n")
    except Exception as e:
        raise add_context(e, f"Failed to write run-ended separator to {log_path}") from e
    db.record_run_end(exit_reason)


# ---------------------------------------------------------------------------
# changelog.md — committed, human-readable record of accomplished sprints
# ---------------------------------------------------------------------------


def append_changelog_entry(sprint_number: int, task_group: list[dict], summary: str) -> None:
    """Append one entry to `autosprint/changelog.md` for a successful, committed sprint.

    Unlike the files under `logs/` (gitignored bookkeeping), `changelog.md` is a
    *committed* record — `commit_sprint` folds it into the sprint commit. That is what
    makes it survive a `git rebase -i` squash: the squashed commit's tree keeps the whole
    changelog even though the per-sprint commit messages collapse into one.

    **Run-scoped headings.** `sprint_number` is the per-run loop counter (resets to 0
    every `pit_loop`), so a flat `## Sprint {n}` heading collides across runs against the
    same repo. Instead, the FIRST entry of a run emits a `## Run YYYY-MM-DD HH:MM`
    heading (gated by the module-level `_CHANGELOG_RUN_HEADING_WRITTEN` flag, mirroring
    how `write_run_separator()` brackets each run in sprint-outcomes.log), and each
    per-sprint entry is a `### Sprint {n} — <date>` one level deeper. The sprint number
    stays per-run, which now reads correctly as "sprint N *of this run*".

    No commit hash is recorded — a commit cannot contain its own final hash (the hash
    depends on this file's content), and hashes go stale on squash anyway. Sprint number
    + date are the durable anchors; `sprint-outcomes.log` keeps the hash.

    No-op in FAKE_IMPLEMENT mode so fake runs don't pollute the real changelog.
    """
    if config.FAKE_IMPLEMENT:
        return
    global _CHANGELOG_RUN_HEADING_WRITTEN
    path = config.TARGET_REPO_PATH / CHANGELOG_FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        date = now.strftime("%Y-%m-%d")
        is_new = not path.exists()
        with path.open("a", encoding="utf-8") as f:
            if is_new:
                f.write("# Changelog\n\nWhat autosprint accomplished — one entry per committed sprint, oldest first, grouped under the run that produced it. Append-only and committed, so it survives a `git rebase -i` squash: the squashed commit keeps the whole file even though per-commit messages collapse.\n")
            if not _CHANGELOG_RUN_HEADING_WRITTEN:
                f.write(f"\n## Run {now.strftime('%Y-%m-%d %H:%M')}\n")
                _CHANGELOG_RUN_HEADING_WRITTEN = True
            f.write(f"\n### Sprint {sprint_number} — {date}\n\n{group_titles(task_group)}\n")
            if summary.strip():
                f.write(f"\n{summary.strip()}\n")
    except Exception as e:
        raise add_context(e, f"Failed to append changelog entry for sprint {sprint_number}") from e


# ---------------------------------------------------------------------------
# destination.md writeback — resolved-open-question status markers + receipts
# ---------------------------------------------------------------------------

_RESOLVED_QUESTIONS_HEADING = "## AI-resolved questions"
_RESOLVED_QUESTIONS_PLACEHOLDER = "_No questions resolved yet._"


def _section_bounds(lines: list[str], heading_text: str) -> tuple[int, int] | None:
    """Locate a ``## <heading_text>`` section in ``lines`` (a destination.md split on
    ``\\n``). Returns ``(heading_index, end_index)`` where ``end_index`` is the index of
    the next line beginning with ``## `` (or ``len(lines)`` at EOF) — i.e. the section
    body is ``lines[heading_index + 1 : end_index]``. Heading matching is tolerant: the
    caller's ``heading_text`` may arrive with or without a leading ``## ``, and the
    comparison is case-insensitive and whitespace-stripped. Returns ``None`` when no
    matching ``## `` heading is found."""
    wanted = heading_text.strip()
    if wanted.startswith("## "):
        wanted = wanted[3:].strip()
    wanted_lc = wanted.lower()
    heading_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("## ") and line[3:].strip().lower() == wanted_lc:
            heading_idx = i
            break
    if heading_idx == -1:
        return None
    end_idx = len(lines)
    for j in range(heading_idx + 1, len(lines)):
        if lines[j].startswith("## "):
            end_idx = j
            break
    return heading_idx, end_idx


def apply_destination_resolutions(resolutions: list[dict]) -> None:
    """Write resolved-open-question status markers + receipts into the target repo's
    ``destination.md`` — the deterministic, code-side half of the open-question
    resolution protocol (the agent only NAMES what it resolved via the
    ``resolved_open_questions`` field; this function does the mechanical writes).

    For each entry (``{"section", "answer", "adr_ref"}``):

    1. **Status marker (write #2).** Append a blockquote at the END of the named
       ``## <section>`` (immediately before the next ``## `` heading, or EOF):
       ``> **Status:** resolved <today> — <answer>. See `adr.md` <adr_ref>.``. The
       human-authored ``*(Open — autosprint to decide.)*`` line is left intact — the
       protocol is append-only, the blockquote is the resolved signal.
    2. **Receipt (write #3).** Append a bullet to ``## AI-resolved questions``:
       ``- **<section>:** <answer>. See `adr.md` <adr_ref>.``. On the first receipt the
       seed placeholder ``_No questions resolved yet._`` is deleted in the same write.

    A missing ``## <section>`` heading is logged loudly (level=100) and skipped — it does
    NOT raise and does NOT fail the sprint (the code work is already correct). Whole
    function is a no-op when ``resolutions`` is empty or in FAKE_IMPLEMENT mode.
    ``destination.md`` is read once and written back once after all entries are applied.
    """
    if not resolutions:
        return
    if config.FAKE_IMPLEMENT:
        return
    path = config.TARGET_REPO_PATH / DESTINATION_FILENAME
    try:
        if not path.exists():
            printlev(f"[writeback] ⚠️  destination.md not found at {path} — cannot write back {len(resolutions)} resolved open question(s). Skipping.", level=100)
            return
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = path.read_text(encoding="utf-8").split("\n")
        for entry in resolutions:
            section = str(entry.get("section") or "").strip()
            answer = str(entry.get("answer") or "").strip()
            adr_ref = str(entry.get("adr_ref") or "").strip()
            if not section:
                printlev("[writeback] ⚠️  Resolution entry has no 'section' — skipping it.", level=100)
                continue
            bounds = _section_bounds(lines, section)
            if bounds is None:
                printlev(f"[writeback] ⚠️  destination.md has no '## {section}' section — cannot append the resolved-question status marker for it. Skipping this entry (the ADR / code work itself is unaffected).", level=100)
                continue
            _heading_idx, end_idx = bounds
            marker = f"> **Status:** resolved {date} — {answer}. See `adr.md` {adr_ref}."
            # Insert the marker as the last line of the section. A blank line before it
            # keeps the blockquote visually separated from the section's prose.
            insert_at = end_idx
            insertion = ["", marker]
            lines[insert_at:insert_at] = insertion

        # Write #3 — receipt(s) into ## AI-resolved questions. Recompute bounds after
        # the marker inserts above shifted the line indices.
        receipt_bounds = _section_bounds(lines, _RESOLVED_QUESTIONS_HEADING)
        if receipt_bounds is None:
            printlev(f"[writeback] ⚠️  destination.md has no '{_RESOLVED_QUESTIONS_HEADING}' section — cannot append resolution receipts. Status markers were still written.", level=100)
        else:
            _r_heading_idx, r_end_idx = receipt_bounds
            # Drop the seed placeholder if it's still present anywhere in the section.
            placeholder_idx = None
            for k in range(_r_heading_idx + 1, r_end_idx):
                if lines[k].strip() == _RESOLVED_QUESTIONS_PLACEHOLDER:
                    placeholder_idx = k
                    break
            if placeholder_idx is not None:
                del lines[placeholder_idx]
                r_end_idx -= 1
            receipts: list[str] = []
            for entry in resolutions:
                section = str(entry.get("section") or "").strip()
                answer = str(entry.get("answer") or "").strip()
                adr_ref = str(entry.get("adr_ref") or "").strip()
                if not section or _section_bounds(lines, section) is None:
                    # Skip receipts for entries whose section was missing — keeps the
                    # receipt list and the in-section markers consistent.
                    continue
                # Normalise the section tag (drop any leading '## ') so the bullet reads
                # as plain heading text regardless of how the agent supplied it.
                tag = section[3:].strip() if section.startswith("## ") else section
                receipts.append(f"- **{tag}:** {answer}. See `adr.md` {adr_ref}.")
            if receipts:
                # Append after the last non-blank line of the section so the bullets
                # join the existing list cleanly rather than landing after trailing
                # blank lines.
                last_content = _r_heading_idx
                for k in range(_r_heading_idx + 1, r_end_idx):
                    if lines[k].strip():
                        last_content = k
                lines[last_content + 1 : last_content + 1] = receipts

        path.write_text("\n".join(lines), encoding="utf-8")
        printlev(f"[writeback] ✅ Wrote {len(resolutions)} resolved-open-question marker(s)/receipt(s) into destination.md.", level=50)
    except Exception as e:
        raise add_context(e, f"Failed to apply {len(resolutions)} destination.md resolution(s)") from e


# ---------------------------------------------------------------------------
# Trimming helpers (run cap-enforcement on the larger logs)
# ---------------------------------------------------------------------------


def trim_plan_decisions_log() -> None:
    """Soft-cap `plan-decisions.md` at `config.PLAN_DECISIONS_RECENT_COUNT` sprint entries. Each entry starts with a `## <timestamp>` heading; we keep the last N such sections and drop everything older. Silent when nothing to trim, when the cap is 0 (disabled), or when the file doesn't exist. Git history still has the complete trail — this just stops the live file from growing unbounded."""
    cap = config.PLAN_DECISIONS_RECENT_COUNT
    if cap <= 0:
        return
    log_path = config.TARGET_REPO_PATH / PLAN_DECISIONS_FILENAME
    if not log_path.exists():
        return
    try:
        text = log_path.read_text(encoding="utf-8")
        # Each sprint entry starts with "## " at column 0. The split preserves
        # everything-before-the-first-entry as segments[0] (file header/preamble),
        # then one segment per sprint. We keep preamble + last `cap` sprints.
        parts = text.split("\n## ")
        if len(parts) <= cap + 1:
            return  # preamble + ≤ cap sprints — nothing to trim
        preamble = parts[0]
        kept_sprints = parts[-cap:]
        trimmed_count = len(parts) - 1 - cap
        new_text = preamble + "\n## " + "\n## ".join(kept_sprints)
        if not new_text.endswith("\n"):
            new_text += "\n"
        log_path.write_text(new_text, encoding="utf-8")
        printlev(f"[prepare] Trimmed plan-decisions.md: dropped {trimmed_count} oldest sprint entries, kept last {cap} (git history has the full trail).", level=50)
    except Exception as e:
        raise add_context(e, f"Failed to trim plan-decisions.md at {log_path}") from e


def trim_console_verbose_log() -> None:
    """Soft-cap `console-verbose.log` at `config.CONSOLE_LOG_MAX_BYTES` by dropping the oldest `# === run started ===` blocks until the file fits. Preserves whole run blocks (we never cut mid-run) so the surviving log remains readable. Silent when nothing to trim, cap is 0 (disabled), or file doesn't exist."""
    cap = config.CONSOLE_LOG_MAX_BYTES
    if cap <= 0:
        return
    from autosprint.output import CONSOLE_LOG_FILENAME

    log_path = config.TARGET_REPO_PATH / CONSOLE_LOG_FILENAME
    if not log_path.exists():
        return
    try:
        if log_path.stat().st_size <= cap:
            return
        text = log_path.read_text(encoding="utf-8")
        # Split on the run-started marker; parts[0] is anything-before-first-run,
        # then one segment per run. We drop oldest runs until under cap.
        marker = "\n# === run started "
        parts = text.split(marker)
        if len(parts) <= 2:
            return  # only one run block — nothing we can drop without cutting mid-run
        # Keep trimming oldest run until size fits. parts[0] is dropped along with
        # the first real run block since it's pre-first-run noise (usually empty).
        kept = parts
        dropped = 0
        # Measure the size *if we keep parts[i:]* prefixed with marker for all but the first kept one.
        while len(kept) > 2:
            candidate = marker + marker.join(kept[1:])
            if len(candidate.encode("utf-8")) <= cap:
                dropped += 1
                break
            kept = kept[1:]
            dropped += 1
        new_text = marker + marker.join(kept[1:]) if len(kept) > 1 else (marker + kept[0])
        if not new_text.endswith("\n"):
            new_text += "\n"
        log_path.write_text(new_text, encoding="utf-8")
        printlev(f"[prepare] Trimmed console-verbose.log: dropped {dropped} oldest run block(s) to fit {cap:,} bytes.", level=50)
    except Exception as e:
        raise add_context(e, f"Failed to trim console-verbose.log at {log_path}") from e


# ---------------------------------------------------------------------------
# plan-decisions.md
# ---------------------------------------------------------------------------


def log_plan_decision(plan: Plan, proposals_text: str = "") -> None:
    log_path = config.TARGET_REPO_PATH / PLAN_DECISIONS_FILENAME
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n## {ts} — {config.TEAM}\n\n")
            if proposals_text:
                f.write(f"### Proposals\n\n{proposals_text}\n\n")
            f.write("### Final pending\n\n")
            for t in plan.pending:
                f.write(f"- {t.title}\n  {t.description}\n")
            f.write("\n")
    except Exception as e:
        raise add_context(e, f'Failed to log plan decision for team "{config.TEAM}" to {log_path}') from e


# ---------------------------------------------------------------------------
# Sprint history readbacks
# ---------------------------------------------------------------------------


def task_attempt_stats(task_title: str) -> tuple[int, int]:
    """Return (attempts, reverts) for `task_title` from sprint-outcomes.log. 'attempts' counts every sprint that ran Implement on this task — successful, reverted, or stopped. 'reverts' counts only lines with REVERTED in the outcome column. Returns (0, 0) when the log is missing or the task has never been attempted."""
    try:
        log_path = config.TARGET_REPO_PATH / SPRINT_LOG_FILENAME
        if not log_path.exists():
            return 0, 0
        attempts = 0
        reverts = 0
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 7:
                continue
            if parts[3] != task_title:
                continue
            attempts += 1
            if "REVERTED" in parts[6]:
                reverts += 1
        return attempts, reverts
    except Exception as e:
        raise add_context(e, f"Failed to compute attempt stats for task '{task_title}'") from e


def task_revert_sprint_count(task_title: str) -> int:
    """Return the number of distinct sprints where `task_title` reverted.

    Unlike `task_attempt_stats`, this dedupes the dual-write pattern where one
    failed sprint writes both `REVERTED` and `SPRINT_REVERTED` rows for the same
    task. Used by blocked-task deferral thresholds so fresh autosprint processes
    still see historical repeated blockers.
    """
    try:
        log_path = config.TARGET_REPO_PATH / SPRINT_LOG_FILENAME
        if not log_path.exists():
            return 0
        sprint_ids: set[str] = set()
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#") or "REVERTED" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 7 or parts[3] != task_title:
                continue
            sprint_ids.add(parts[0])
        return len(sprint_ids)
    except Exception as e:
        raise add_context(e, f"Failed to compute revert sprint count for task '{task_title}'") from e


def recent_sprint_history(n: int = 5) -> str:
    """Return the last `n` unique sprint rows from sprint-outcomes.log for planner context.
    Deduplicates dual-write rows (each successful sprint writes an intermediate ``OK | pending``
    row then a final ``OK | OK | <hash>`` row). We keep only the highest-confidence row per
    (sprint_no, task_title) pair — the commit-hash row when present, else the intermediate.
    """
    log_path = config.TARGET_REPO_PATH / SPRINT_LOG_FILENAME
    try:
        if not log_path.exists():
            return ""
        raw_lines = [line for line in log_path.read_text(encoding="utf-8").strip().splitlines() if line.strip() and not line.lstrip().startswith("#")]
        # Deduplicate: prefer commit-hash rows (test_status == "OK", outcome looks like a sha/NO_COMMIT)
        # over intermediate rows (test_status == "pending").
        seen: dict[tuple[str, str], str] = {}
        for line in raw_lines:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 7:
                seen.setdefault((parts[0] if parts else line, line), line)
                continue
            key = (parts[0], parts[3])  # (sprint_no, task_title)
            existing = seen.get(key)
            if existing is None:
                seen[key] = line
            else:
                # Prefer the commit row (test=OK) over the intermediate row (test=pending).
                existing_parts = [p.strip() for p in existing.split("|")]
                if len(existing_parts) >= 6 and existing_parts[5] == "pending":
                    seen[key] = line
        deduped = list(seen.values())
        return "\n".join(deduped[-n:]) if deduped else ""
    except Exception as e:
        raise add_context(e, f"Failed to read recent sprint history from {log_path}") from e


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------


def stale_task_titles() -> list[str]:
    """Return the titles of tasks that have REVERTED ``QUARANTINE_TASK_AFTER_FAILURES``+ times across distinct sprint failures in the last 20 log entries — the loop quarantines these (moves them to Blocked / Deferred) and continues, rather than halting. Returns ``[]`` in FAKE_IMPLEMENT mode (stochastic fake failures would falsely trigger it) or when quarantine is disabled (threshold ``<= 0``).

    Two subtleties the implementation handles:

    1. **Dual-write deduping.** A single failed sprint produces two log entries
       per task — first ``FAILED | n/a | REVERTED`` from ``run_implement``'s
       failure handler, then ``FAILED | FAILED | SPRINT_REVERTED: <reason>``
       from the outer ``pit_loop`` ``PhaseFailedError`` handler. Counting both
       inflates the apparent failure rate 2×; we dedupe by ``(sprint_no, task)``
       so each unique sprint failure counts once.

    2. **Fallback-aware skip.** When ``IMPLEMENT_FALLBACK_AGENT`` is configured,
       refusal-pattern reverts in the log history don't count toward
       quarantine — the fallback now intercepts those automatically, so a task
       with historical refusals (pre-fallback) shouldn't be benched on their
       account. Non-refusal failures (test failures, real bugs) still count so
       a genuinely stuck task gets quarantined.

    The log schema is ``sprint | ts | sp | task | implement | test | outcome``;
    the task title lives at column index 3, the outcome at index 6.
    """
    # Lazy import: detect_refusal_pattern lives in implement_phase, which is
    # downstream of run_log in module dependency order. By the time
    # stale_task_titles runs, implement_phase is fully loaded.
    from autosprint.implement_phase import detect_refusal_pattern

    log_path = config.TARGET_REPO_PATH / SPRINT_LOG_FILENAME
    try:
        threshold = config.QUARANTINE_TASK_AFTER_FAILURES
        if config.FAKE_IMPLEMENT or threshold <= 0:
            return []
        if not log_path.exists():
            return []
        lines = [line for line in log_path.read_text(encoding="utf-8").strip().splitlines() if not line.lstrip().startswith("#")]
        blocked_titles = {task.title for task in read_plan_md(config.TARGET_REPO_PATH).blocked}
        a6_enabled = bool(config.IMPLEMENT_FALLBACK_AGENT_CONFIG)
        # (sprint_no, task) -> "this sprint's revert is recognised as a refusal".
        # We OR across the two dual-write entries so the SPRINT_REVERTED line
        # (which carries the reason) can flip a sprint to refusal=True even if
        # the bare REVERTED line was scanned first.
        sprint_revert_is_refusal: dict[tuple[str, str], bool] = {}
        for line in reversed(lines[-20:]):
            if "REVERTED" not in line:
                continue
            parts = line.split("|")
            if len(parts) < 4:
                continue
            sprint_no = parts[0].strip()
            task = parts[3].strip()
            if task in blocked_titles:
                continue
            outcome = parts[6].strip() if len(parts) >= 7 else ""
            is_refusal = detect_refusal_pattern(outcome)
            key = (sprint_no, task)
            sprint_revert_is_refusal[key] = sprint_revert_is_refusal.get(key, False) or is_refusal
        recent_reverts: dict[str, int] = {}
        for (sprint_no, task), is_refusal in sprint_revert_is_refusal.items():
            if a6_enabled and is_refusal:
                # The refusal-fallback will intercept future refusals on this task; don't quarantine
                # historical refusal-only reverts that predate the safety net.
                continue
            recent_reverts[task] = recent_reverts.get(task, 0) + 1
        return [task for task, count in recent_reverts.items() if count >= threshold]
    except Exception as e:
        raise add_context(e, f"Failed to compute stale task titles from {log_path}") from e


# ---------------------------------------------------------------------------
# Story-point parsing
# ---------------------------------------------------------------------------


def extract_story_points(title: str) -> int | None:
    """Parse a trailing '(N)' story-point tag from a task title. Returns None if the tag is missing, malformed, or the title is empty."""
    if not title:
        return None
    m = STORY_POINT_PATTERN.search(title)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# runtime-stats.md (rolling avg used in startup banner ETA)
# ---------------------------------------------------------------------------


def read_runtime_stats() -> tuple[float, int, int]:
    """Read the rolling (average_sprint_time_seconds, sprint_count, total_sp) from TARGET_REPO/autosprint/logs/runtime-stats.md. Returns (0.0, 0, 0) when the file is missing or malformed — a malformed file shouldn't block a real run, so we degrade silently and let the next successful write restore clean state. `total_sp` is the cumulative story points across all recorded sprints; divide total_seconds by total_sp for a seconds-per-SP estimate."""
    try:
        path = config.TARGET_REPO_PATH / RUNTIME_STATS_FILENAME
        if not path.exists():
            return 0.0, 0, 0
        avg = 0.0
        count = 0
        total_sp = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("average_sprint_time_seconds:"):
                try:
                    avg = float(stripped.split(":", 1)[1].strip())
                except ValueError:
                    return 0.0, 0, 0
            elif stripped.startswith("sprint_count:"):
                try:
                    count = int(stripped.split(":", 1)[1].strip())
                except ValueError:
                    return 0.0, 0, 0
            elif stripped.startswith("total_story_points:"):
                try:
                    total_sp = int(stripped.split(":", 1)[1].strip())
                except ValueError:
                    total_sp = 0
        return avg, count, total_sp
    except Exception as e:
        raise add_context(e, "Failed to read runtime stats") from e


def write_runtime_stats(average_seconds: float, count: int, total_sp: int) -> None:
    """Rewrite TARGET_REPO/autosprint/logs/runtime-stats.md with the current rolling state. The file is intentionally small and human-readable so the user can eyeball it without parsing tooling."""
    try:
        path = config.TARGET_REPO_PATH / RUNTIME_STATS_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        sec_per_sp = (average_seconds * count) / total_sp if total_sp > 0 else 0.0
        sec_per_sp_line = f"seconds_per_story_point: {sec_per_sp:.2f}\n" if total_sp > 0 else ""
        body = "# Autosprint runtime stats\n\nRolling average used to estimate runtime for future autosprint runs. Autosprint updates this file after each sprint — edit by hand only if the numbers get skewed by an outlier and you want to reset.\n\n" + f"average_sprint_time_seconds: {average_seconds:.2f}\nsprint_count: {count}\ntotal_story_points: {total_sp}\n{sec_per_sp_line}"
        path.write_text(body, encoding="utf-8")
    except Exception as e:
        raise add_context(e, f"Failed to write runtime stats ({count} sprints, avg={average_seconds:.1f}s, total_sp={total_sp})") from e


def update_runtime_stats(latest_sprint_seconds: float, sprint_sp: int = 0) -> None:
    """Apply the rolling-average update after one sprint finishes (success or revert both count). `sprint_sp` is the total story points attempted in the sprint (sum over the task group) so the stats file can track a seconds-per-SP estimate. Skipped in FAKE_IMPLEMENT / FAKE_PLAN_TITLE runs so stub sprints don't deflate the estimate."""
    try:
        if config.FAKE_IMPLEMENT or config.FAKE_PLAN_TITLE:
            return
        old_avg, old_count, old_sp = read_runtime_stats()
        new_count = old_count + 1
        new_avg = ((old_avg * old_count) + latest_sprint_seconds) / new_count
        new_total_sp = old_sp + max(0, sprint_sp)
        write_runtime_stats(new_avg, new_count, new_total_sp)
    except Exception as e:
        raise add_context(e, f"Failed to update runtime stats with latest_sprint_seconds={latest_sprint_seconds:.1f}, sprint_sp={sprint_sp}") from e


def estimated_runtime_line(planned_sprints: int) -> str:
    """One-line description of estimated wall-clock runtime based on the rolling average. Returns a neutral 'no history yet' line when the file is missing so the first run still gets a banner entry."""
    avg_sec, count, total_sp = read_runtime_stats()
    if count == 0:
        return "Estimated runtime: no history yet — first run."
    total_min = (avg_sec * planned_sprints) / 60
    sp_line = ""
    if total_sp > 0:
        sec_per_sp = (avg_sec * count) / total_sp
        sp_line = f" · avg {sec_per_sp:.1f}s/SP"
    return f"Estimated runtime: ~{total_min:.1f} min for {planned_sprints} sprints (based on {count} historical sprint(s), avg {avg_sec:.1f}s/sprint{sp_line})."


# ---------------------------------------------------------------------------
# Per-sprint review + end-of-run summary
# ---------------------------------------------------------------------------


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
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
