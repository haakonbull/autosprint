"""Plan phase: build prompts, dispatch the team, merge proposals, write plan.md.

Owns:
- Prompt assembly (`build_prompt_for_plan_phase`, `assemble_prompt_for_team_lead`,
  `_plan_phase_context`, lock-destination notice).
- Team-lead context gathering (`build_context_for_team_lead`, `TeamLeadContext`,
  `get_proposals_from_team_members`, `query_team_lead_with_retry`).
- Waypoint handling (`_read_waypoint`, `_waypoint_section`,
  `_write_waypoint_status_marker`, `_waypoint_title`).
- The Plan-phase entry points called by pit_loop: `update_plan` (full
  re-plan), `plan_phase` (decide-then-plan), `should_replan`, plus the
  task-group selection helpers (`select_sprint_task_group`,
  `select_top_tasks`, `_log_plan_skip`).
- Two small shared utilities (`read_agent_file`, `_read_adr`,
  `lock_destination_section`, `SprintRevertRecord`, `build_post_revert_hint`)
  that the implementor-prompt and post-revert-hint paths also depend on.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from autosprint.config import _project_root, config
from autosprint.core.plan import PendingTask, Plan, format_full_plan, read_plan_md, serialise_plan, write_plan_md
from autosprint.infra.dispatch import AgentResults, query_agent, query_agents
from autosprint.infra.stop import raise_if_stop_between_phases
from autosprint.phases.test_phase import get_initial_tests_summary, read_last_test_output, run_preflight_tests
from autosprint.registry.agents import TOOLS_READ_ONLY
from autosprint.reporting.run_log import append_run_log, log_plan_decision, recent_sprint_history
from autosprint.util.errors import PhaseFailedError, RevertReason, WaypointReached, add_context
from autosprint.util.output import printlev
from autosprint.util.parsing import parse_result
from autosprint.util.paths import ADR_FILENAME, WAYPOINT_FILENAME

# ---------------------------------------------------------------------------
# Agent files & shared prompt fragments (used by both Plan and Implement)
# ---------------------------------------------------------------------------


def read_agent_file(relative_path: str) -> str:
    try:
        return (_project_root() / relative_path).read_text(encoding="utf-8")
    except Exception as e:
        raise add_context(e, f'Failed to read agent file "{relative_path}"') from e


def read_adr() -> str:
    """Returns the contents of adr.md in TARGET_REPO, or an empty string if the file is missing."""
    try:
        path = config.TARGET_REPO_PATH / ADR_FILENAME
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except Exception as e:
        raise add_context(e, f"Failed to read adr.md from {config.TARGET_REPO_PATH}") from e


def prioritize_section() -> str:
    """Returns the prompt fragment exposing a user-flagged priority for this run. Empty when `config.PRIORITIZE` is unset, so the section has zero footprint by default. Surfaces in both the team-member prompt (via `plan_phase_context`) and the team-lead prompt (via `assemble_prompt_for_team_lead`) so every planner sees the same hint. The string is passed verbatim — no parsing, no heading lookup: the planner reads it as natural-language guidance and resolves any reference into destination.md itself."""
    text = config.PRIORITIZE.strip()
    if not text:
        return ""
    return f"\n\n## User priority for this run\n\nThe user has flagged the following as a priority for this planning run:\n\n```\n{text}\n```\n\nSurface tasks that address this priority near the top of your proposed list. If the text references an existing section of `destination.md` (vaguely or by name), look it up and prioritise tasks toward that section. If it introduces a concern not yet in destination.md, propose tasks for it as one-off work for this run — do not add the priority itself to destination.md; that is the human's decision to make later. When in doubt about how to interpret the hint, follow the most direct reading and proceed."


def lock_destination_section() -> str:
    """Returns a prompt fragment instructing planners and the implementor not to propose any expansion of `destination.md` when LOCK_DESTINATION is True. Empty when False so it has zero footprint by default. Surfaces in three prompts: team-member plan-phase, team-lead plan-phase, and implementor."""
    if not config.LOCK_DESTINATION:
        return ""
    return (
        "\n\n## TARGET STATE LOCKED — convergence mode\n\n"
        "`autosprint/destination.md` is **locked** for this run. "
        "Do not propose any task that expands destination.md — including new entries under `## AI-generated subgoals`, "
        "new entries in `## Open questions`, or new entries in `## Technical decisions`. "
        "The PIT loop is in **convergence mode**: every task must close distance toward content already in the file. "
        "If you find yourself wanting to propose 'add new subgoal X', 'surface open question Y', or 'add new tech decision Z', drop it — the human has decided the spec is final for this run.\n\n"
        "**Tasks that ARE allowed:** implementing existing pending items; refactoring; test consolidation; bug fixes; documentation that reflects existing scope; ADR entries that record technical decisions made during implementation (those go to `adr.md`, not destination.md); and one-line *receipts* under `## AI-resolved questions` when an existing open question gets answered (that's convergence, not expansion — the original question stays untouched and the receipt only points at the new ADR).\n\n"
        "**For the implementor:** if a task requires expanding destination.md to complete, do not write to the file. Instead submit `submit_implement_failure` with reason='target-state-locked: <what the task wanted to add>' so the human can decide whether to unlock or drop the task."
    )


# ---------------------------------------------------------------------------
# Waypoint helpers
# ---------------------------------------------------------------------------


def read_waypoint() -> str:
    """Returns the contents of waypoint.md in TARGET_REPO when the file exists and is *active*. An empty string means no waypoint is in effect — either the file is absent, or the user paused it by renaming to `waypoint.md.paused`. The pause-by-rename gesture is documented in CLAUDE.md as the standard way to temporarily disable a waypoint without losing the content."""
    try:
        path = config.TARGET_REPO_PATH / WAYPOINT_FILENAME
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")
    except Exception as e:
        raise add_context(e, f"Failed to read waypoint.md from {config.TARGET_REPO_PATH}") from e


def waypoint_already_reached(text: str) -> bool:
    """Returns True if waypoint.md already carries a `> **Status:** reached` marker — indicating the team lead halted the loop on this waypoint in a prior run and the user hasn't cleared it yet. The orchestrator uses this as a circuit-breaker so a stale waypoint doesn't trigger a fresh halt every run."""
    if not text:
        return False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">") and "**Status:**" in stripped and "reached" in stripped.lower():
            return True
    return False


def waypoint_section() -> str:
    """Returns the prompt fragment exposing the active waypoint to plan-phase agents. Empty string when no waypoint is active or the existing marker shows it was already reached — both states mean "plan against destination as normal". When active, the section appears in both the team-member prompt (via `_plan_phase_context`) and the team-lead prompt (via `_assemble_prompt_for_team_lead`), so all planners see the same target. The section header is the load-bearing signal — agents key off `## Waypoint active` to know whether to apply the strict exclusivity rule from plan-agent.md / plan-team.md."""
    text = read_waypoint()
    if not text.strip():
        return ""
    if waypoint_already_reached(text):
        return ""
    return f"\n\n## Waypoint active\n\nA user-set intermediate target is in effect. Plan tasks toward this state exclusively until it is reached. The waypoint overrides destination.md as the current planning target — destination's constraints (ADRs, conventions) still apply, but no new destination-driven tasks should be proposed until the waypoint is satisfied. See plan-agent.md / plan-team.md for the full rules and the reached-detection contract.\n\n```\n{text}\n```"


def write_waypoint_status_marker(rationale: str) -> None:
    """Append a `> **Status:** reached <YYYY-MM-DD> — <rationale>` blockquote to waypoint.md so the user sees the team lead's verdict next time they open the file. The file itself is left in place — no auto-archive — so the user decides whether to delete, archive, or remove the marker (re-activate). Idempotent: repeated calls add additional markers. The marker also acts as a circuit-breaker for `_waypoint_section` so a stale reached waypoint doesn't re-trigger a halt on every restart."""
    try:
        path = config.TARGET_REPO_PATH / WAYPOINT_FILENAME
        if not path.exists():
            return  # waypoint was deleted between detection and write — nothing to mark
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        clean_rationale = rationale.strip().replace("\n", " ") or "(no rationale provided)"
        marker = f"\n\n> **Status:** reached {date} — {clean_rationale}\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(marker)
    except Exception as e:
        raise add_context(e, f"Failed to append reached-status marker to {WAYPOINT_FILENAME}") from e


def waypoint_title() -> str:
    """Best-effort one-line title for the active waypoint, used in the sprint header so the user sees which mode the loop is in. Pulled from the first non-blank, non-`#`-only Markdown heading in waypoint.md. Falls back to `(unnamed waypoint)` if none can be found. Returns empty string when no waypoint is active so callers can branch on truthiness."""
    text = read_waypoint()
    if not text.strip() or waypoint_already_reached(text):
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("# ").strip()
            if heading:
                return heading
    return "(unnamed waypoint)"


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def plan_phase_context() -> str:
    """Shared context appended to every plan-phase prompt (story-point band + plan + decisions + history). When LOCK_DESTINATION is set, the lock notice is prepended so team members see the convergence-mode instruction first. When a waypoint is active, the waypoint section is prepended too so every planner aims at it exclusively."""
    current_plan = read_plan_md(config.TARGET_REPO_PATH)
    plan_text = serialise_plan(current_plan, recent_count=config.PLAN_RECENT_COMPLETED_COUNT)
    ctx = waypoint_section() + lock_destination_section() + prioritize_section()
    ctx += f"\n\n## Story-point band for this sprint\n\nSPRINT_STORY_POINT_MIN = {config.SPRINT_STORY_POINT_MIN}, SPRINT_STORY_POINT_MAX = {config.SPRINT_STORY_POINT_MAX}. Tag each proposed task with a trailing '(N)' Fibonacci-ish estimate. Anywhere inside [{config.SPRINT_STORY_POINT_MIN}, {config.SPRINT_STORY_POINT_MAX}] is fine. A single '(1)' task that stands alone (a focused bug fix, a small independent improvement) is also welcome — don't up-size it artificially. Tasks above {config.SPRINT_STORY_POINT_MAX} must be split by the team lead before writing plan.md; a pattern of sub-min tasks that share a concern should be bundled."
    ctx += f"\n\n## Current autosprint/plan.md\n\n{plan_text}"
    adr = read_adr()
    if adr.strip():
        ctx += f"\n\n## Current autosprint/adr.md (architecture decision records — stable choices, respect, do not overturn casually)\n\n{adr}"
    history = recent_sprint_history()
    if history:
        ctx += f"\n\n## Recent sprint history\n\n{history}"
    return ctx


def build_prompt_for_plan_phase(agent: dict) -> str:
    """Assemble the plan-phase prompt for `agent` by reading the prompt template fresh each call (so prompt edits apply without restart) and substituting the agent's name + persona, then appending the shared plan-phase context block. Defaults to `.claude/agents/plan-agent.md` (the code-flavored prompt); research agents declare `plan_prompt_file = ".claude/agents/plan-agent-research.md"` on their agent dict to use the research-flavored variant instead."""
    try:
        prompt_file = agent.get("plan_prompt_file", ".claude/agents/plan-agent.md")
        template = read_agent_file(prompt_file)
        prompt = template.replace("{name}", agent["name"]).replace("{system_prompt}", agent["system_prompt"])
        prompt += plan_phase_context()
        return prompt
    except Exception as e:
        raise add_context(e, f'Failed to build plan prompt for agent "{agent.get("name", "unknown")}"') from e


def log_proposed_tasks(raw: str) -> None:
    """Print the proposed pending tasks from a plan-agent response at level=10 so the user can see what each team member (and the team-lead) proposed."""
    try:
        parsed = parse_result(raw, "pending")
        if not parsed or "pending" not in parsed:
            return
        lines = ["   [] " + str(t.get("title", "")) for t in parsed.get("pending", []) if isinstance(t, dict)]
        if lines:
            printlev("\n".join(lines), level=10)
    except Exception as e:
        raise add_context(e, "Failed to log proposed tasks from plan-agent response") from e


def result_to_plan(result: dict, existing_completed: list) -> Plan:
    plan = Plan(completed=list(existing_completed))
    for item in result.get("pending", []):
        plan.pending.append(PendingTask(title=item.get("title", "(untitled)"), description=item.get("description", "")))
    return plan


# ---------------------------------------------------------------------------
# Team-lead context + prompt assembly
# ---------------------------------------------------------------------------


@dataclass
class TeamLeadContext:
    """Everything gathered for the team lead's prompt. `proposed_task_lists` is always populated; preflight and last test output are conditional enrichments."""

    proposed_task_lists: AgentResults
    preflight_summary: str = ""
    last_test_output: str = ""


async def get_proposals_from_team_members(team_members: list[dict]) -> AgentResults:
    """Build a plan-phase prompt for each team member and dispatch them all in parallel. Returns an AgentResults wrapping one response per member — each member's output contains its own list of proposed tasks. Called as `await` in the normal case, or passed (unawaited) to `asyncio.gather` when we want to run it concurrently with preflight."""
    team_member_prompts = [build_prompt_for_plan_phase(member) for member in team_members]
    return await query_agents(team_members, team_member_prompts, phase_tag="[P]", on_result=log_proposed_tasks, result_log_suffix=". Here are the tasks it suggested:")


async def build_context_for_team_lead(team_members: list[dict], sprint_number: int, prev_sprint_reverted: bool) -> TeamLeadContext:
    """Build the full TeamLeadContext for this sprint. Reads top-to-bottom as a checklist — every piece of context the team lead will see is computed here, at the same abstraction level:

    1. Proposed task lists from each team member — always gathered.
    2. Pre-flight pytest summary — only when sprint-state warrants it (post-revert recovery runs fresh in parallel with member dispatch; sprint 1 reuses the startup summary; otherwise none).
    3. Previous sprint's test-phase output — always included when the log exists.

    Terminology: in this codebase "team members" excludes the team lead (the lead is the selector). The full set of LLM agents in the Plan phase is "team members + team lead".
    """
    try:
        # (1) Proposed task lists from each team member — always gathered.
        proposals_coroutine = get_proposals_from_team_members(team_members)

        # (2) Preflight pytest summary — conditional on sprint state. `run_preflight_test` makes the fresh-pytest branch self-documenting; the sprint-1-reuse condition stays inline because it involves a global and reads naturally as-is. When the fresh path fires, preflight runs concurrently with proposal dispatch so we never wait serially.
        run_preflight_test = prev_sprint_reverted

        initial_tests_summary = get_initial_tests_summary()
        if run_preflight_test:
            proposed_task_lists, preflight_summary = await asyncio.gather(proposals_coroutine, asyncio.to_thread(run_preflight_tests))
        elif sprint_number == 1 and initial_tests_summary:
            printlev("[P] Reusing initial-test summary as sprint-1 pre-flight context (no re-run).", level=20)
            proposed_task_lists = await proposals_coroutine
            preflight_summary = initial_tests_summary
        else:
            proposed_task_lists = await proposals_coroutine
            preflight_summary = ""

        # (3) Previous sprint's test-phase output — always included when the log exists. Captures pass count + warnings + failure context from the just-completed Test phase so the team lead can see the signal that would otherwise only appear on console.
        last_test_output = read_last_test_output()

        return TeamLeadContext(
            proposed_task_lists=proposed_task_lists,
            preflight_summary=preflight_summary,
            last_test_output=last_test_output,
        )
    except Exception as e:
        raise add_context(e, f"Failed to build team-lead context (sprint_number={sprint_number}, prev_reverted={prev_sprint_reverted})") from e


def assemble_prompt_for_team_lead(ctx: TeamLeadContext, selector: dict | None = None, post_revert_hint: str = "", plan_only_mode: bool = False) -> str:
    """Stitch the full team-lead prompt from the already-built context: team-lead-prompt-file base + optional plan-only depth section + optional post-revert hint + optional pre-flight section + previous sprint's test output + the Proposals block (one per team member). Pure string concatenation. The prompt-file path defaults to `.claude/agents/plan-team.md` (code-flavored); research selectors declare `plan_lead_prompt_file = ".claude/agents/plan-team-research.md"` on their agent dict to use the research-flavored variant. `selector=None` is accepted (defaults to the code prompt) so older callers and test fixtures that didn't pass the selector keep working — pass `selector=<agent dict>` when you want the prompt-file selection respected. `plan_only_mode` is True only on an `autosprint plan-only` run; it appends guidance telling the lead to produce a fuller, human-curated candidate list."""
    prompt_file = (selector or {}).get("plan_lead_prompt_file", ".claude/agents/plan-team.md")
    prompt_for_team_lead = read_agent_file(prompt_file) + waypoint_section() + lock_destination_section() + prioritize_section() + plan_depth_section(plan_only_mode) + post_revert_hint + preflight_prompt_section(ctx.preflight_summary) + last_test_output_section(ctx.last_test_output) + f"\n\n## Proposals\n\n{ctx.proposed_task_lists.to_proposals_text()}"
    printlev(f"[P] Full team-lead prompt ({len(prompt_for_team_lead)} chars):\n{prompt_for_team_lead}", level=1)
    return prompt_for_team_lead


def preflight_prompt_section(summary: str) -> str:
    """Return the team-lead-prompt section that exposes pre-flight test context. Empty when summary is blank so we add zero noise when pre-flight didn't run."""
    if not summary.strip():
        return ""
    return f"\n\n## Pre-flight test context (team lead only — team members did not see this)\n\nA pre-flight test run produced the output below. You — the team lead — decide whether fixing any failures is the right priority for this sprint. This is *information*, not a forced directive: weigh it against the team's proposals the same way you weigh anything else. If the baseline is green, no action needed; proceed with normal planning.\n\n```\n{summary}\n```"


def last_test_output_section(last_output: str) -> str:
    """Return the team-lead-prompt section exposing the previous sprint's test-phase output (summary line, warnings, failure context if any). Empty when the log is missing or blank — that happens on sprint 1 before any Test phase has run, after clear-logs, or in FAKE_IMPLEMENT mode. Surfaces warnings that would otherwise only appear on console and vanish."""
    if not last_output.strip():
        return ""
    return f"\n\n## Previous sprint's test output (team lead only — team members did not see this)\n\nPytest output from the Test phase of the just-completed sprint. The pass/fail outcome is already reflected in plan.md; what matters here is **warnings** (deprecations, runtime warnings, unclosed resources) that pytest emitted without failing the build. These often point at real issues worth queueing as cleanup tasks. Use your judgement — not every warning warrants action, but a growing warning count is a signal the codebase is accumulating debt.\n\n```\n{last_output}\n```"


def plan_depth_section(plan_only_mode: bool) -> str:
    """Team-lead prompt section that fires only for `autosprint plan`. A plan-only
    run produces a candidate list a human will review and curate by hand, so the lead
    should cast a wider net than the 5–10 tasks the loop wants. Empty in loop mode —
    there the `plan-team.md` default stands (a short, fresh horizon the loop re-plans
    often, so a long stale tail is wasted)."""
    if not plan_only_mode:
        return ""
    return '\n\n## Plan-only mode — produce a fuller candidate list\n\nThis is an `autosprint plan` run: the plan you produce will be **reviewed and curated by a human**, not executed immediately. Override the "aim for 5–10 pending tasks" guidance above — instead aim for a **broader candidate list of roughly 15–30 pending tasks**, still ordered by strategic value (most reliable, highest-value work first; more speculative work last). The human will prune, reorder, and delete — your job here is coverage and good ordering, not a minimal list. Every task must still meet the quality bar above (concrete title, story-point estimate, ADR conversion for decisions-in-disguise); breadth is not licence for vague filler. Because the list is deep, the **Dependency ordering** final pass matters most here — be thorough and explicit with `Depends on:` annotations so the human curating plan.md can verify the execution order at a glance.'


# ---------------------------------------------------------------------------
# Post-revert context for the next replan
# ---------------------------------------------------------------------------


@dataclass
class SprintRevertRecord:
    """One entry per sprint-since-last-replan. Built in pit_loop as each sprint ends, consumed by `build_post_revert_hint` at the next replan."""

    sprint_number: int
    task_titles: list[str]
    reason: RevertReason
    reason_message: str


def build_post_revert_hint(records: list[SprintRevertRecord]) -> str:
    """Build an explicit hint for the team lead when one or more sprints since the last replan were reverted with a REAL reason (test failure, implementer refused/failed — not parser-format hiccups). Empty string when the window is all-green — silence is the signal that the plan is working. Parser-format reverts are mentioned as context but not framed as evidence that a task needs rethinking. The window is bounded by the caller (pit_loop clears the records list each replan)."""
    if not records:
        return ""
    real = [r for r in records if r.reason != RevertReason.IMPLEMENT_MALFORMED]
    if not real:
        # Only parser-format reverts since the last replan — mention briefly so the
        # planner isn't surprised to see the task still pending, but don't imply
        # the task itself is broken.
        n = len(records)
        return f"\n\n## Context for this replan (autosprint parser hiccup — not a task problem)\n\nSince the last replan, {n} sprint(s) were reverted purely because autosprint's RESULT-block parser couldn't read the implementer's response (format issue, not task failure). The work may already have been done. Do not re-scope or split these tasks on account of those reverts — just plan normally.\n"
    lines = ["", "## Context for this replan (recent reverts since last replan)", ""]
    for r in real:
        titles = "; ".join(r.task_titles) if r.task_titles else "(no task)"
        lines.append(f"- Sprint {r.sprint_number}: reverted on `{titles}` — reason: **{r.reason.value}** ({r.reason_message[:120]})")
    lines.append("")
    lines.append("Consider when choosing the top pending tasks:")
    lines.append("  1. **Split** the failing task(s) into smaller, clearly-scoped pieces if size looks like the issue.")
    lines.append("  2. **Deprioritise** — pick a different top task and let the problem one cool off.")
    lines.append("  3. **Re-word** the task description if it was ambiguous and the implementer took a wrong turn.")
    lines.append("")
    lines.append("Do not just re-propose the same task unchanged — the loop already tried that. Do not force a fix if a different top task is genuinely more important now.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Team-lead retry loop
# ---------------------------------------------------------------------------


async def query_team_lead_with_retry(agent: dict, prompt: str, sprint_number: int, on_result=None) -> dict:
    """Call the team lead (or, in solo-team mode, the lone agent who plays both member and lead) and parse the returned task list. Retries up to 3 times on parse failure. Returns the parsed `{"pending": [...]}` plan dict, or raises PhaseFailedError if every attempt fails."""
    try:
        for attempt in range(3):
            raw = await query_agent(agent, prompt, TOOLS_READ_ONLY, cache_validator=lambda t: parse_result(t, "pending"), phase_tag="[P]", on_result=on_result, result_log_suffix=". It looked at all proposals, and suggested following tasks:")
            parsed_plan = parse_result(raw, "pending")
            if parsed_plan and "pending" in parsed_plan and isinstance(parsed_plan["pending"], list):
                return parsed_plan
            printlev(f"[P] Parse failure (attempt {attempt + 1}/3)", level=20)
        append_run_log(sprint_number, "PARSE_FAILURE", "n/a", "n/a", "SKIPPED")
        raise PhaseFailedError("Plan failed after 3 attempts")
    except PhaseFailedError:
        raise
    except Exception as e:
        raise add_context(e, f"Failed to query agent '{agent.get('name', 'unknown')}' with retry") from e


# ---------------------------------------------------------------------------
# update_plan / plan_phase / should_replan
# ---------------------------------------------------------------------------


async def update_plan(team: list[dict], selector: dict, sprint_number: int = 0, prev_sprint_reverted: bool = False, post_revert_hint: str = "", plan_only_mode: bool = False) -> Plan:
    """Generate plan.md: query the team for task proposals (or use FAKE_PLAN_TITLE), let the selector merge them, write plan.md. Decides internally whether to surface a pre-flight pytest summary to the team lead based on sprint_number and prev_sprint_reverted — callers hand in raw sprint state, update_plan owns the "what does the team lead see" decision. Pre-flight context only appears in the team-lead prompt (multi-agent teams); team members stay unaware so their proposals remain independent. `post_revert_hint` is prepended to the team-lead prompt when this replan follows one or more real reverts since the last replan. When the team lead returns `waypoint_reached: true` AND a waypoint is currently active, raises `WaypointReached` after appending a status marker to waypoint.md — the pit_loop catches it and halts cleanly. `plan_only_mode` is True only when called from the `autosprint plan-only` entry point; it appends `plan_depth_section` guidance so the lead drafts a fuller candidate list for human curation instead of the loop's short 5–10. On a plan-only run the lead's `plan_summary` editorial is rendered as a blockquote atop plan.md; loop mode ignores it."""
    try:
        existing = read_plan_md(config.TARGET_REPO_PATH).completed
        waypoint_active = bool(waypoint_section())  # captured pre-dispatch; the file shouldn't change mid-call but we want a stable snapshot

        if config.FAKE_PLAN_TITLE:
            printlev(f"[P] Using FAKE_PLAN_TITLE (no agent call): {config.FAKE_PLAN_TITLE}", level=20)
            plan = Plan(completed=list(existing), pending=[PendingTask(title=config.FAKE_PLAN_TITLE, description=config.FAKE_PLAN_DESC)])
            parsed_plan: dict = {}
        elif len(team) == 1:
            printlev(f"[P] Generating plan with single agent: {selector.get('name', 'unknown')}", level=20)
            solo_prompt = build_prompt_for_plan_phase(selector) + post_revert_hint + plan_depth_section(plan_only_mode)
            parsed_plan = await query_team_lead_with_retry(selector, solo_prompt, sprint_number, on_result=log_proposed_tasks)
            plan = result_to_plan(parsed_plan, existing)
            log_plan_decision(plan)
        else:
            printlev(f"[P] Generating plan with team of {len(team)} agent(s)...", level=20)
            agent_names = ", ".join(a.get("name", "unknown") for a in team)
            printlev(f"[P] Team agents: {agent_names} | Selector: {selector.get('name', 'unknown')}", level=5)
            printlev(f"[P] Running {len(team)} agents in parallel...", level=20)
            context: TeamLeadContext = await build_context_for_team_lead(team, sprint_number, prev_sprint_reverted)
            team_lead_prompt = assemble_prompt_for_team_lead(context, selector=selector, post_revert_hint=post_revert_hint, plan_only_mode=plan_only_mode)
            printlev(f"[P] All {len(team)} team proposals received. Asking '{selector.get('name', 'unknown')}' (team-lead role) to merge them into the final plan...", level=20)
            raise_if_stop_between_phases()  # `stop --now` issued during team-members dispatch should short-circuit before another LLM call
            parsed_plan = await query_team_lead_with_retry(selector, team_lead_prompt, sprint_number, on_result=log_proposed_tasks)
            plan = result_to_plan(parsed_plan, existing)
            log_plan_decision(plan, context.proposed_task_lists.to_proposals_text())

        # Waypoint-reached signal — only honored when a waypoint was actually active at the start of this call.
        # An LLM that sets the flag spuriously when no waypoint exists is ignored. We write the status marker
        # BEFORE writing plan.md so the marker lands even if write_plan_md raises, and BEFORE raising so the
        # pit_loop sees a populated waypoint.md when it surfaces the halt reason to the user.
        if waypoint_active and bool(parsed_plan.get("waypoint_reached")):
            rationale = str(parsed_plan.get("waypoint_reached_rationale") or "").strip() or "(team lead set waypoint_reached but provided no rationale)"
            write_waypoint_status_marker(rationale)
            printlev(f"[P] 🏁 Team lead signalled waypoint_reached. Status marker appended to {WAYPOINT_FILENAME}. Halting loop.\n[P] Rationale: {rationale}", level=100)
            raise WaypointReached(rationale)

        # Plan-only mode: render the lead's `plan_summary` editorial as a blockquote atop
        # plan.md so the human reviewer sees the verdict first. Gated on plan_only_mode in
        # code as well as in the prompt — a loop-mode plan.md stays clean.
        plan_summary = str(parsed_plan.get("plan_summary") or "").strip() if plan_only_mode else ""
        write_plan_md(config.TARGET_REPO_PATH, plan, recent_count=config.PLAN_RECENT_COMPLETED_COUNT, plan_summary=plan_summary)
        pending_lines = "\n".join(f"      {i}. {t.title}" for i, t in enumerate(plan.pending, 1))
        printlev(f"[P] Tasklist from team lead written into plan.md. {len(plan.pending)} pending task(s):\n{pending_lines}", level=20)
        printlev(f"[P] Full plan.md after update (completed + pending).\n{format_full_plan(serialise_plan(plan, recent_count=config.PLAN_RECENT_COMPLETED_COUNT))}", level=5)
        return plan
    except PhaseFailedError:
        raise
    except WaypointReached:
        # Clean halt signal — propagate without add_context wrapping so the pit_loop sees the bare exception type.
        raise
    except Exception as e:
        raise add_context(e, f"Failed to generate plan with {len(team)} agents") from e


def log_plan_skip(plan: Plan, sprints_since_replan: int) -> None:
    """Log the 'Plan phase skipped — reusing existing plan.md' path. Two lines: a terse summary at level=20 and the full pending list at level=5 for verbose mode."""
    printlev(f"[P] Skipping Plan phase (plan.md has {len(plan.pending)} pending task(s), {sprints_since_replan} sprints since last replan)", level=20)
    printlev(f"[P] Using existing plan.md.\n{format_full_plan(serialise_plan(plan, recent_count=config.PLAN_RECENT_COMPLETED_COUNT))}", level=5)


def select_sprint_task_group(plan: Plan, task_count_cap: int | None = None) -> list[dict]:
    """Return the task group for this sprint. When SPRINT_STORY_POINT_TARGET is 0 (disabled), returns a list of length 1 — the single top pending task (legacy single-task-per-sprint behavior). When TARGET > 0, greedily combines the top N pending tasks, stopping as soon as cumulative story points ≥ TARGET, without exceeding SPRINT_STORY_POINT_MAX. `task_count_cap` additionally stops the bundler at this many tasks even if SP target wasn't met — used by the adaptive cap to shrink sprints after a real revert. When None, uses `SPRINT_TASK_COUNT_CAP_INITIAL`. Edge cases: (a) untagged tasks never get grouped with others (too ambiguous to size — take alone if first slot, otherwise stop); (b) a first task whose SP already ≥ TARGET passes as-is (never combined with smaller followers, count cap ignored for solo big tasks); (c) when MAX < TARGET (nonsensical config), groups stop at MAX, so TARGET is unreachable — groups come back smaller than intended but the function never returns an empty group for a non-empty plan. Raises nothing — an empty plan returns an empty list, which the caller handles as 'no pending tasks'."""
    # Lazy import: extract_story_points lives in run_log; importing eagerly
    # would require both modules to be loaded in a stable order.
    from autosprint.reporting.run_log import extract_story_points

    target = config.SPRINT_STORY_POINT_TARGET
    cap = task_count_cap if task_count_cap is not None else config.SPRINT_TASK_COUNT_CAP_INITIAL
    cap = max(cap, 1)
    if not plan.pending:
        return []
    if target <= 0:
        # Grouping disabled → single-task mode (legacy behavior).
        top = plan.pending[0]
        return [{"title": top.title, "description": top.description}]
    group: list[dict] = []
    sp_sum = 0
    for t in plan.pending:
        sp = extract_story_points(t.title)
        if sp is None:
            # Untagged task → never bundle. If this is the first slot, take it alone; otherwise stop.
            if not group:
                group.append({"title": t.title, "description": t.description})
            break
        if not group and sp >= target:
            # First task already at or above target → take it alone. Count cap
            # deliberately ignored here: a single big important task should always
            # ship, even if cap has shrunk to 1 after a prior failure.
            return [{"title": t.title, "description": t.description}]
        if sp_sum + sp > config.SPRINT_STORY_POINT_MAX:
            break
        group.append({"title": t.title, "description": t.description})
        sp_sum += sp
        if sp_sum >= target:
            break
        if len(group) >= cap:
            # Hit the adaptive (or static-initial) task-count cap.
            break
    return group


def select_top_tasks(plan: Plan, task_count_cap: int | None = None) -> list[dict]:
    """Return the sprint's task group via `select_sprint_task_group`, or raise PhaseFailedError if the plan is empty. Emits the level=50 'Task(s) chosen' banner. Threads `task_count_cap` through so the pit loop can feed the adaptive cap into task selection."""
    from autosprint.reporting.run_log import extract_story_points

    task_group = select_sprint_task_group(plan, task_count_cap=task_count_cap)
    if not task_group:
        printlev("[P] plan.md has no pending tasks after Plan phase; aborting sprint.")
        raise PhaseFailedError("No pending tasks after Plan phase")
    if len(task_group) == 1:
        printlev(f"[P] 📌 Task chosen for this iteration: {task_group[0]['title']}", level=50)
    else:
        total_sp = sum(sp for sp in (extract_story_points(t["title"]) for t in task_group) if sp is not None)
        sp_suffix = f", {total_sp} SP total" if total_sp else ""
        header = f"[P] 📌 Following tasks are bundled into a task group for this iteration ({len(task_group)} tasks{sp_suffix}):"
        task_lines = [f"   [] {t['title']}" for t in task_group]
        printlev("\n".join([header, *task_lines]), level=50)
    return task_group


async def plan_phase(sprints_since_replan: int, task_failure_counts: dict[str, int], sprint_number: int = 0, prev_sprint_reverted: bool = False, force_replan: bool = False, task_count_cap: int | None = None, post_revert_hint: str = "") -> tuple[Plan, list[dict], int]:
    """Run the Plan phase: read plan.md, regenerate if needed, and return the plan, next task group, and updated sprints_since_replan. The task group is a list of 1+ tasks depending on SPRINT_STORY_POINT_TARGET and the optional `task_count_cap`; when TARGET=0 (default), always a list of one task. When a replan is triggered, sprint_number + prev_sprint_reverted decide whether to surface pre-flight test context to the team lead. `force_replan=True` skips the should_replan decision and regenerates the plan unconditionally — used on the first sprint so a stale plan.md from a previous run is always refreshed. `post_revert_hint` is an extra block prepended to the team-lead prompt when this replan follows one or more real reverts since the last replan — makes the failure signal impossible to miss."""
    try:
        printlev("\n[P] 📋 Entering Plan phase...", level=50)
        plan = read_plan_md(config.TARGET_REPO_PATH)
        replan, _reason = should_replan(plan, sprints_since_replan, task_failure_counts, force=force_replan)
        if replan:
            plan = await update_plan(config.TEAM_AGENTS, config.TEAM_SELECTOR, sprint_number=sprint_number, prev_sprint_reverted=prev_sprint_reverted, post_revert_hint=post_revert_hint)
            sprints_since_replan = 0
        else:
            log_plan_skip(plan, sprints_since_replan)
        task_group = select_top_tasks(plan, task_count_cap=task_count_cap)
        return plan, task_group, sprints_since_replan
    except PhaseFailedError:
        raise
    except WaypointReached:
        # Clean halt signal from update_plan — propagate to pit_loop without wrapping.
        raise
    except Exception as e:
        raise add_context(e, f"Failed to run Plan phase (sprints_since_replan={sprints_since_replan})") from e


def should_replan(plan: Plan, sprints_since_replan: int, task_failure_counts: dict[str, int], force: bool = False) -> tuple[bool, str]:
    """Returns (True, reason) if plan.md should be regenerated and prints the reason; else (False, ""). `force=True` unconditionally triggers a replan (used for the first sprint of a run, where the plan from a previous run may be stale even if it has pending items).

    Reviewed-plan mode — `config.AUTO_REPLAN` False, the default for `autosprint run` — overrides everything, including `force`, and pins the answer to (False, ""): the loop runs only the human-reviewed plan.md and never regenerates it. `autosprint run --auto-replan` sets AUTO_REPLAN True and the normal triggers below apply."""
    if not config.AUTO_REPLAN:
        return False, ""
    if force:
        reason = "first sprint of this run — regenerating to ensure plan reflects current destination/adr"
    elif plan.is_empty():
        reason = "plan is empty"
    elif sprints_since_replan >= config.REPLAN_EVERY_N_SPRINTS:
        reason = f"{sprints_since_replan} sprints since last replan (replan at least every {config.REPLAN_EVERY_N_SPRINTS} sprints)"
    else:
        top = plan.top_pending()
        if top and task_failure_counts.get(top.title, 0) >= 2:
            reason = f"top task '{top.title}' has failed {task_failure_counts[top.title]} times (replan after 2+ failures)"
        else:
            return False, ""
    printlev(f"[P] Replan triggered: {reason}", level=50)
    return True, reason
