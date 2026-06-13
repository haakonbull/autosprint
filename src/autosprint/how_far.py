"""autosprint how-far — read-only distance-to-destination measurement.

Owns the `autosprint how-far` one-shot subcommand. It dispatches a single
read-only agent that follows the `how-far` skill (`.claude/skills/how-far/
SKILL.md`) against the target repo and prints a status table estimating how
much of `autosprint/destination.md` is already implemented.

The skill file is the single source of truth for the measurement method;
this module is only the dispatch wrapper that makes it runnable from the CLI
— so it composes in a shell (`autosprint run && autosprint how-far`) and works
on a Copilot-only setup. The dispatched agent always runs with the read-only
tool preset, so how-far can never modify the repo.
"""

from __future__ import annotations

import asyncio
import time

from autosprint.agents import AGENTS, TOOLS_READ_ONLY
from autosprint.banners import section_banner
from autosprint.config import _project_root, config
from autosprint.dispatch import query_agent
from autosprint.errors import add_context
from autosprint.output import printlev
from autosprint.paths import DESTINATION_FILENAME, LOGS_SUBDIR

_HOWFAR_SKILL_PATH = ".claude/skills/how-far/SKILL.md"
_HEARTBEAT_LOG_FILENAME = "howfar-heartbeat.log"


def _read_howfar_skill() -> str:
    """Returns the body of autosprint's how-far SKILL.md with the YAML frontmatter stripped. Read from autosprint's own repo so the skill file stays the single source of truth — a stale copy in the target repo is never used."""
    try:
        raw = (_project_root() / _HOWFAR_SKILL_PATH).read_text(encoding="utf-8")
    except Exception as e:
        raise add_context(e, f"Failed to read the how-far skill at {_HOWFAR_SKILL_PATH}") from e
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            return raw[end + 4 :].lstrip("\n")
    return raw


def _resolve_howfar_agent(agent_override: str | None) -> dict:
    """Returns the agent dict for `autosprint how-far`: the `--agent` flag value when given, else config.HOWFAR_AGENT. Raises RuntimeError naming the valid keys when the chosen key is not in agents.AGENTS."""
    key = agent_override or config.HOWFAR_AGENT
    if key not in AGENTS:
        raise RuntimeError(f"how-far agent '{key}' is not a known agent. Valid keys: {', '.join(sorted(AGENTS))}.")
    return AGENTS[key]


def _build_howfar_prompt(skill_body: str) -> str:
    """Returns the dispatch prompt: the how-far skill instructions wrapped with an explicit read-only framing and an instruction to print only the report."""
    return f'Run the autosprint "how-far" measurement on the repository you are in.\n\nYou have READ-ONLY tools (Read, Glob, Grep) — you cannot and must not edit, create, or delete any file. This is a measurement, not a change.\n\nFollow the skill instructions below exactly. Read autosprint/destination.md, decompose it into discrete requirements, verify each against the actual code and its tests, and print the headline + status table the instructions specify. Output only the report — no preamble, nothing after the closing verdict.\n\n=== how-far skill instructions ===\n{skill_body}\n=== end how-far skill instructions ==='


async def _run_howfar_agent(agent: dict, prompt: str) -> str:
    """Dispatch the how-far prompt to `agent` with the read-only tool preset and return the agent's report text."""
    return await query_agent(agent, prompt, tools=TOOLS_READ_ONLY, skip_cache=True, phase_tag="[how-far]")


async def dispatch_howfar_async(agent_override: str | None = None) -> str:
    """Dispatch the how-far measurement and return the agent's report text.
    Shared async core used by both `autosprint how-far` (the CLI subcommand) and
    the in-loop heartbeat. Raises RuntimeError when destination.md is missing —
    callers decide whether to propagate (CLI) or swallow (heartbeat)."""
    dest = config.TARGET_REPO_PATH / DESTINATION_FILENAME
    if not dest.exists() or not dest.read_text(encoding="utf-8").strip():
        raise RuntimeError(f"{DESTINATION_FILENAME} is missing or empty in {config.TARGET_REPO_PATH} — there is nothing to measure. Run `autosprint init`, then the /grill-destination skill to write it.")
    agent = _resolve_howfar_agent(agent_override)
    printlev(f"[how-far] Measuring distance to {DESTINATION_FILENAME} with {agent['name']} [{agent['assistant']}/{agent['model']}] — read-only, no changes will be made...", level=100)
    return await _run_howfar_agent(agent, _build_howfar_prompt(_read_howfar_skill()))


def run_how_far(agent_override: str | None = None) -> None:
    """`autosprint how-far` — dispatch one read-only agent that follows the how-far
    skill against the target repo and print its distance-to-destination report.
    `agent_override` is the `--agent` flag value; when None, config.HOWFAR_AGENT
    is used. Aborts with a clear message when destination.md is missing or empty
    — there is nothing to measure against."""
    try:
        printlev(f"\n{section_banner('HOW-FAR', 'START')}\n", level=100)
        report = asyncio.run(dispatch_howfar_async(agent_override))
        printlev(f"\n{report.strip()}\n", level=100)
        printlev(f"{section_banner('HOW-FAR', 'END')}", level=100)
    except RuntimeError:
        raise
    except Exception as e:
        raise add_context(e, f"Failed to run autosprint how-far in {config.TARGET_REPO_PATH}") from e


def _heartbeat_headline(report: str) -> str:
    """Extract the one-line headline from a how-far report — the line that begins with `Distance to destination`, with markdown bold markers stripped. Returns the first non-empty line as a fallback when the headline isn't found."""
    for raw in report.splitlines():
        line = raw.strip()
        if not line:
            continue
        normalised = line.replace("**", "").lstrip("> ").strip()
        if normalised.lower().startswith("distance to destination"):
            return normalised
    for raw in report.splitlines():
        line = raw.strip()
        if line:
            return line.replace("**", "")
    return "(empty how-far report)"


def _append_heartbeat_log(sprint_number: int, report: str) -> None:
    """Append the full how-far report to autosprint/logs/howfar-heartbeat.log with a sprint-number + timestamp header. Each entry is delimited so a `tail`-style read of the file is intelligible."""
    log_path = config.TARGET_REPO_PATH / LOGS_SUBDIR / _HEARTBEAT_LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry = f"\n=== Sprint {sprint_number} — {timestamp} ===\n{report.strip()}\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(entry)


async def run_howfar_heartbeat(sprint_number: int) -> None:
    """In-loop how-far heartbeat. Dispatches one how-far measurement, appends the
    full report to `autosprint/logs/howfar-heartbeat.log`, and prints the headline
    inline so a watching human sees the count trend without re-running how-far by
    hand. Read-only sensor — never feeds back into the planner (Goodhart-safe).
    Swallows its own errors: a heartbeat failure must never crash the PIT loop."""
    try:
        printlev(f"\n[heartbeat] 📡 Sprint {sprint_number} — running how-far progress check...", level=100)
        report = await dispatch_howfar_async(None)
        _append_heartbeat_log(sprint_number, report)
        headline = _heartbeat_headline(report)
        printlev(f"[heartbeat] {headline}", level=100)
        printlev(f"[heartbeat] Full report appended to {LOGS_SUBDIR}/{_HEARTBEAT_LOG_FILENAME}.\n", level=100)
    except Exception as e:
        printlev(f"[heartbeat] ⚠ how-far failed at sprint {sprint_number} — continuing PIT loop: {e}", level=100)
