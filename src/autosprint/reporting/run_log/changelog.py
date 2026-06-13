"""Extracted from the original autosprint.reporting.run_log module."""

from __future__ import annotations

from datetime import UTC, datetime

from autosprint.config import config
from autosprint.domain.plan import group_titles
from autosprint.util.errors import add_context
from autosprint.util.paths import (
    CHANGELOG_FILENAME,
)

# Run-scoped flag: True once this process has emitted the `## Run …` heading to
# changelog.md, so subsequent sprint entries in the same run skip re-emitting
# it. Resets only when the process restarts (a new autosprint run), which is
# exactly the granularity we want — one `## Run …` heading per invocation. This
# mirrors how `write_run_separator()` brackets each run in sprint-outcomes.log.
_CHANGELOG_RUN_HEADING_WRITTEN: bool = False


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
        now = datetime.now(UTC)
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
