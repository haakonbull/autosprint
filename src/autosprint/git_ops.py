"""Git wrappers used by the PIT loop.

Thin layer over `subprocess` and `git` so the rest of the codebase doesn't
have to think about cwd, capture, or check-args. All commands run with
TARGET_REPO_PATH as cwd. Pure side-effecting helpers — no module-level
state, no caching — so they can be re-imported / re-exported safely.
"""

from __future__ import annotations

import subprocess

from autosprint.config import config
from autosprint.errors import add_context
from autosprint.output import printlev
from autosprint.paths import PLAN_DECISIONS_FILENAME, RUNTIME_STATS_FILENAME, SPRINT_LOG_FILENAME
from autosprint.plan import group_titles

# History files that survive `git restore` — these are tracked by git so they
# time-travel with `git checkout`, but their in-flight (uncommitted) writes from
# the just-failed sprint must NOT be blown away on revert. Pathspec exclusion
# does exactly that. The next successful sprint picks up the accumulated lines
# via `git add -A` in commit_sprint, so the FAILED entry for sprint N gets
# committed alongside the OK entry for sprint N+1.
_RESTORE_EXCLUDE_PATHSPECS: tuple[str, ...] = (
    f":(exclude){SPRINT_LOG_FILENAME}",
    f":(exclude){PLAN_DECISIONS_FILENAME}",
    f":(exclude){RUNTIME_STATS_FILENAME}",
)


def git(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True, cwd=config.TARGET_REPO_PATH)
    except Exception as e:
        raise add_context(e, f"Failed to run git {' '.join(args)}") from e


def git_restore() -> None:
    """Revert the working tree to HEAD, except for the three tracked history files (sprint-outcomes.log, plan-decisions.md, runtime-stats.md) — those accumulate across sprints and must survive a revert so the just-failed sprint's FAILED line and planning archive entry aren't lost. `git clean -fd` still removes any untracked junk."""
    try:
        printlev("   Reverting changes...", level=20)
        git("restore", ".", *_RESTORE_EXCLUDE_PATHSPECS)
        git("clean", "-fd")
    except Exception as e:
        raise add_context(e, "Failed to restore git working tree to last commit") from e


def git_commit(task_group: list[dict], summary: str) -> None:
    """Commit all staged changes for a task group (1+ tasks) as a single git commit. Single-task groups keep the legacy message shape `[autosprint] Title`; multi-task groups use `[autosprint] Group (N tasks): Title-A; Title-B` so the subject line stays scannable in git log."""
    try:
        git("add", "-A")
        subject = f"[autosprint] {task_group[0]['title']}" if len(task_group) == 1 else f"[autosprint] Group ({len(task_group)} tasks): {group_titles(task_group)}"
        git("commit", "-m", f"{subject}\n\n{summary}")
    except Exception as e:
        raise add_context(e, f"Failed to git commit task group '{group_titles(task_group)}'") from e


def get_commit_hash() -> str:
    try:
        return git("rev-parse", "--short", "HEAD").stdout.strip()
    except Exception as e:
        raise add_context(e, "Failed to get current commit hash") from e


def summarise_working_tree_diff() -> str:
    """Returns a short per-file summary of changes in the target repo vs HEAD (each line indented 4 spaces to align under the '[I] Files changed:' headline), or a sentinel if the repo has no HEAD. Filters out autosprint/plan.md — that's orchestrator bookkeeping, not an Implement-agent change — so readers don't mistake it for code the agent touched. Also drops git's aggregate 'N file(s) changed, M insertions, K deletions' trailer, which would otherwise misleadingly count the filtered plan.md as a real change."""
    try:
        check = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=config.TARGET_REPO_PATH)
        if check.returncode != 0:
            return "    (target repo has no HEAD or is not a git repo — skipping diff)"
        stat = git("diff", "HEAD", "--stat").stdout.rstrip()
        untracked = [line[3:] for line in git("status", "--porcelain").stdout.splitlines() if line.startswith("??")]
        stat_lines = [line for line in stat.splitlines() if "autosprint/plan.md" not in line and "autosprint\\plan.md" not in line and " file changed" not in line and " files changed" not in line]
        parts: list[str] = []
        if stat_lines:
            parts.append("\n".join("    " + line.lstrip() for line in stat_lines))
        if untracked:
            parts.append("    Untracked (new): " + ", ".join(untracked))
        return "\n".join(parts) if parts else "    (no changes detected)"
    except Exception as e:
        raise add_context(e, "Failed to summarise working-tree diff") from e
