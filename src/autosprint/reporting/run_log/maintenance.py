"""Extracted from the original autosprint.reporting.run_log module."""

from datetime import UTC, datetime

from autosprint.config import config
from autosprint.core.plan import Plan
from autosprint.util.errors import add_context
from autosprint.util.output import printlev
from autosprint.util.paths import (
    PLAN_DECISIONS_FILENAME,
)


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
    from autosprint.util.output import CONSOLE_LOG_FILENAME

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


def log_plan_decision(plan: Plan, proposals_text: str = "") -> None:
    log_path = config.TARGET_REPO_PATH / PLAN_DECISIONS_FILENAME
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
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
