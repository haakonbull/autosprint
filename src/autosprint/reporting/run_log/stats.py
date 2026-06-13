"""Extracted from the original autosprint.reporting.run_log module."""

from __future__ import annotations

import re

from autosprint.config import config
from autosprint.util.errors import add_context
from autosprint.util.paths import (
    RUNTIME_STATS_FILENAME,
)

STORY_POINT_PATTERN = re.compile(r"\((\d+)\)\s*$")


def extract_story_points(title: str) -> int | None:
    """Parse a trailing '(N)' story-point tag from a task title. Returns None if the tag is missing, malformed, or the title is empty."""
    if not title:
        return None
    m = STORY_POINT_PATTERN.search(title)
    return int(m.group(1)) if m else None


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
