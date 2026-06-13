"""Extracted from the original autosprint.reporting.run_log module."""

from __future__ import annotations

from autosprint.config import config
from autosprint.util.errors import add_context
from autosprint.util.parsing import detect_refusal_pattern
from autosprint.util.paths import (
    SPRINT_LOG_FILENAME,
)


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


def check_escalation() -> None:
    """Raise if the same task has REVERTED 3+ times across distinct sprint failures in the last 20 log entries; skipped in FAKE_IMPLEMENT mode (stochastic fake failures would falsely trigger it).

    Two subtleties the implementation handles:

    1. **Dual-write deduping.** A single failed sprint produces two log entries
       per task — first ``FAILED | n/a | REVERTED`` from ``run_implement``'s
       failure handler, then ``FAILED | FAILED | SPRINT_REVERTED: <reason>``
       from the outer ``pit_loop`` ``PhaseFailedError`` handler. Counting both
       inflates the apparent failure rate 2×; we dedupe by ``(sprint_no, task)``
       so each unique sprint failure counts once.

    2. **Fallback-aware skip.** When ``IMPLEMENT_FALLBACK_AGENT`` is configured,
       refusal-pattern reverts in the log history don't count toward
       escalation — the fallback now intercepts those automatically, so a task
       with 3 historical refusals (pre-fallback) shouldn't be permanently
       locked out. Non-refusal failures (test failures, real bugs) still
       escalate as before so genuine problems aren't masked.

    The log schema is ``sprint | ts | sp | task | implement | test | outcome``;
    the task title lives at column index 3, the outcome at index 6.
    """
    log_path = config.TARGET_REPO_PATH / SPRINT_LOG_FILENAME
    try:
        if config.FAKE_IMPLEMENT:
            return
        if not log_path.exists():
            return
        lines = [line for line in log_path.read_text(encoding="utf-8").strip().splitlines() if not line.lstrip().startswith("#")]
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
            outcome = parts[6].strip() if len(parts) >= 7 else ""
            is_refusal = detect_refusal_pattern(outcome)
            key = (sprint_no, task)
            sprint_revert_is_refusal[key] = sprint_revert_is_refusal.get(key, False) or is_refusal
        recent_reverts: dict[str, int] = {}
        for (_sprint_no, task), is_refusal in sprint_revert_is_refusal.items():
            if a6_enabled and is_refusal:
                # The refusal-fallback will intercept future refusals on this task; don't escalate
                # historical refusal-only reverts that predate the safety net.
                continue
            recent_reverts[task] = recent_reverts.get(task, 0) + 1
        for task, count in recent_reverts.items():
            if count >= 3:
                raise RuntimeError(f"Escalation: task '{task}' has failed {count} times. It may need to be broken down or re-scoped.")
    except RuntimeError:
        raise
    except Exception as e:
        raise add_context(e, f"Failed to check escalation from {log_path}") from e
