"""plan.md parser, writer, and mutation helpers.

The plan file lives in TARGET_REPO at plan.md and has the structure:

    # Plan

    ## Recent completed

    - [x] Title (commit_hash)
      Summary line.

    ## Pending

    - [ ] Title
      Description (may span multiple indented lines)

The orchestrator is the only thing that mutates plan.md after creation.
LLMs read it as context and propose new plan content during the Plan phase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from autosprint.util.errors import add_context
from autosprint.util.output import printlev

PLAN_FILENAME = "autosprint/plan.md"


def group_titles(task_group: list[dict]) -> str:
    """Short semicolon-joined label for a task group (for speaking, logging headlines, error messages). Returns the single title for a group of one, or 'title_A; title_B; …' for larger groups."""
    return "; ".join(t["title"] for t in task_group)


@dataclass
class CompletedTask:
    title: str
    summary: str
    commit_hash: str = ""


@dataclass
class PendingTask:
    title: str
    description: str = ""


@dataclass
class Plan:
    completed: list[CompletedTask] = field(default_factory=list)
    pending: list[PendingTask] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.pending

    def top_pending(self) -> PendingTask | None:
        return self.pending[0] if self.pending else None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


_HEADER_COMPLETED = re.compile(r"^##\s+Recent completed\s*$", re.IGNORECASE)
_HEADER_PENDING = re.compile(r"^##\s+Pending\s*$", re.IGNORECASE)
_HEADER_ANY = re.compile(r"^##\s+")
_COMPLETED_LINE = re.compile(r"^-\s+\[x\]\s+(.*?)(?:\s+\(([0-9a-f]{4,40})\))?\s*$", re.IGNORECASE)
_PENDING_LINE = re.compile(r"^-\s+\[\s\]\s+(.*?)\s*$")


def _parse_section(lines: list[str], start: int) -> tuple[list[tuple[str, str | None, list[str]]], int]:
    """Parse a section of checkbox items starting at line `start`.

    Returns a list of (header_line, optional_hash, body_lines) tuples and the
    index where the section ended.
    """
    items: list[tuple[str, str | None, list[str]]] = []
    i = start
    current: tuple[str, str | None, list[str]] | None = None
    while i < len(lines):
        line = lines[i]
        if _HEADER_ANY.match(line):
            break
        completed_match = _COMPLETED_LINE.match(line)
        pending_match = _PENDING_LINE.match(line)
        if completed_match:
            if current:
                items.append(current)
            current = (completed_match.group(1), completed_match.group(2), [])
        elif pending_match:
            if current:
                items.append(current)
            current = (pending_match.group(1), None, [])
        elif current is not None and line.strip():
            current[2].append(line.strip())
        i += 1
    if current:
        items.append(current)
    return items, i


def parse_plan(text: str) -> Plan:
    """Parse plan.md text into a Plan dataclass. Tolerant of missing sections."""
    plan = Plan()
    if not text.strip():
        return plan
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if _HEADER_COMPLETED.match(line):
            items, i = _parse_section(lines, i + 1)
            for title, commit_hash, body in items:
                plan.completed.append(CompletedTask(title=title, summary=" ".join(body), commit_hash=commit_hash or ""))
            continue
        if _HEADER_PENDING.match(line):
            items, i = _parse_section(lines, i + 1)
            for title, _, body in items:
                plan.pending.append(PendingTask(title=title, description=" ".join(body)))
            continue
        i += 1
    return plan


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def serialise_plan(plan: Plan, recent_count: int = 5, plan_summary: str = "") -> str:
    """Serialise a Plan to markdown. Only the last `recent_count` completed
    items are kept inline; earlier ones are dropped (git history is the archive).

    `plan_summary`, when non-empty, is rendered as a blockquote directly under the
    `## Pending` heading — the team lead's at-a-glance editorial from an
    `autosprint plan` run. It is plan-time-only review metadata: `parse_plan`
    does not read it back, so it does not survive a re-serialise (the first
    post-plan loop write drops it, which is fine — its job is done by then).
    """
    lines: list[str] = ["# Plan", ""]
    lines.append("## Recent completed")
    lines.append("")
    recent = plan.completed[-recent_count:] if recent_count > 0 else plan.completed
    if not recent:
        lines.append("_(none yet)_")
    for task in recent:
        suffix = f" ({task.commit_hash})" if task.commit_hash else ""
        lines.append(f"- [x] {task.title}{suffix}")
        if task.summary:
            lines.append(f"  {task.summary}")
    lines.append("")
    lines.append("## Pending")
    lines.append("")
    if plan_summary.strip():
        lines.extend(f"> {summary_line}".rstrip() for summary_line in plan_summary.strip().split("\n"))
        lines.append("")
    if not plan.pending:
        lines.append("_(none — Plan phase will populate this)_")
    for task in plan.pending:
        lines.append(f"- [ ] {task.title}")
        if task.description:
            # Indent every line of a (possibly multi-line) description so trailing
            # annotation lines (Consensus/Importance, Depends on) render under the
            # task instead of flush-left.
            lines.extend(f"  {desc_line}".rstrip() for desc_line in task.description.split("\n"))
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------


def _strip_gfm_dash(line: str) -> str:
    """Returns the line with a leading '- ' stripped from GFM checkbox markers so log output reads as '[ ] Title' instead of '- [ ] Title'."""
    if line.startswith(("- [ ]", "- [x]", "- [X]")):
        return line[2:]
    return line


def format_full_plan(plan_text: str) -> str:
    """Returns the plan text framed with document-start/document-end markers, indented 4 spaces per line, with empty lines stripped and GFM '- ' checkbox prefixes removed for readability."""
    body = "\n".join("    " + _strip_gfm_dash(line) for line in plan_text.splitlines() if line.strip())
    return f"    ---------- plan.md (entire file) ----------\n{body}\n    __________ plan.md (entire file) __________"


def plan_path(repo_path: Path) -> Path:
    return Path(repo_path) / PLAN_FILENAME


def read_plan_md(repo_path: Path) -> Plan:
    """Read plan.md from the target repo. Returns an empty Plan if missing."""
    try:
        path = plan_path(repo_path)
        if not path.exists():
            return Plan()
        text = path.read_text(encoding="utf-8")
        printlev(f"[read_plan_md] {path}\n{format_full_plan(text)}", level=1)
        return parse_plan(text)
    except Exception as e:
        raise add_context(e, f"Failed to read plan.md from {repo_path}") from e


def write_plan_md(repo_path: Path, plan: Plan, recent_count: int = 5, plan_summary: str = "") -> None:
    try:
        path = plan_path(repo_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialise_plan(plan, recent_count=recent_count, plan_summary=plan_summary), encoding="utf-8")
    except Exception as e:
        raise add_context(e, f"Failed to write plan.md to {repo_path}") from e


def mark_top_pending_done(repo_path: Path, summary: str, commit_hash: str = "", recent_count: int = 5) -> Plan:
    """Move the top pending task to completed with the given summary, then write."""
    try:
        plan = read_plan_md(repo_path)
        if not plan.pending:
            raise ValueError("Cannot mark task done: no pending tasks in plan")
        task = plan.pending.pop(0)
        plan.completed.append(CompletedTask(title=task.title, summary=summary, commit_hash=commit_hash))
        write_plan_md(repo_path, plan, recent_count=recent_count)
        return plan
    except Exception as e:
        raise add_context(e, f"Failed to mark top pending task done in {repo_path}") from e
