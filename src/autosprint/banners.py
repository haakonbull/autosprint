"""Visual banners and tree diagrams printed by the PIT loop.

Pure formatting / printing helpers. The startup banner, the per-iteration
section banner, the box-drawing PIT-loop tree, and the show-config dump all
live here so orchestrator.py doesn't have to carry the visual layout.
"""

from __future__ import annotations

from autosprint.config import config
from autosprint.errors import add_context
from autosprint.output import printlev


def section_banner(name: str, tag: str) -> str:
    """Returns a single-line section banner. START tags use '-' as the rule character (a softer opener), END tags use '=' (a heavier closer). (END) is padded so START/END align."""
    char = "-" if tag.upper() == "START" else "="
    target_width = len(f" {name} (START) ")
    label = f" {name} ({tag}) ".ljust(target_width)
    return char * 33 + label + char * 33


def iteration_banner(sprint_number: int, tag: str) -> str:
    return section_banner(f"ITERATION {sprint_number}", tag)


def agent_tag(agent: dict) -> str:
    """Returns a single-line agent description: name + [assistant/model]. Used inside the PIT-loop tree diagram."""
    return f"{agent.get('name', 'unknown')} [{agent.get('assistant', '?')}/{agent.get('model', '?')}]"


def pit_loop_tree(task_group_target: int) -> list[str]:
    """Return a box-drawing-character tree showing the PIT loop for this sprint: Plan (team members + preflight → team lead → plan.md), Implement, Test, Commit, Review. The Plan branch renders differently for solo teams (1 agent does both roles) vs. multi-agent teams (members fan-in to a distinct team lead). The tree encodes the data flow visually so a reader can see at a glance that the team lead's output is derived from parallel member proposals plus the preflight pytest summary."""
    implementor_tag = agent_tag(config.IMPLEMENT_AGENT_CONFIG)
    test_description = 'pytest (quick subset: -m "not slow")' if config.TEST_PHASE_QUICK_ONLY else "pytest (full suite every sprint)"
    sp_hint = f" (aim ~{task_group_target} SP/group)" if task_group_target > 0 else ""

    if len(config.TEAM_AGENTS) == 1:
        plan_branch = [
            "   ├── [P] Plan — 1 agent proposes tasks AND writes autosprint/plan.md:",
            f"   │     └── {agent_tag(config.TEAM_AGENTS[0])}",
        ]
    else:
        members = config.TEAM_AGENTS
        member_lines = [f"   │           │     ├── {agent_tag(a)}" for a in members[:-1]]
        member_lines.append(f"   │           │     └── {agent_tag(members[-1])}")
        plan_branch = [
            "   ├── [P] Plan — team lead merges inputs into autosprint/plan.md:",
            f"   │     └── Team lead: {agent_tag(config.TEAM_SELECTOR)} — receives:",
            f"   │           ├── Team members ({len(members)}) — propose tasks in parallel:",
            *member_lines,
            "   │           └── Pre-flight pytest — summary of current test state",
        ]

    return [
        "   The PIT loop (one sprint)",
        "   │",
        *plan_branch,
        "   │",
        f"   ├── [I] Implement — top task(s) from plan.md{sp_hint}:",
        f"   │     └── {implementor_tag}",
        "   │",
        f"   ├── [T] Test — {test_description}",
        "   ├── [C] Commit — if tests pass, commit the sprint to the branch",
        "   └── [R] Review — verdict + escalation counters",
        "",
        "   ↺  loop back to [P] unless MAX_SPRINTS hit, failure cap reached, or stop requested",
    ]


def print_start_banner(branch_name: str) -> None:
    """Print a visually distinct startup banner summarising the PIT run configuration after prepare completes; always fires at level=100 (prod). Uses 3-space indentation and a box-drawing tree diagram for the Plan→Implement→Test→Commit→Review loop."""
    # Lazy import: _estimated_runtime_line lives in run_log; importing eagerly
    # would create banners ⇄ run_log circular at module-load time.
    from autosprint.run_log import estimated_runtime_line

    try:
        modes: list[str] = []
        if config.COMMIT_ON_START:
            modes.append("commit-on-start")
        if config.MANUAL_REVIEW:
            modes.append("manual-review")
        if config.AUTO_REPLAN:
            modes.append("auto-replan")
        if config.FAKE_PLAN_TITLE:
            modes.append("fake-plan")
        if config.FAKE_IMPLEMENT:
            modes.append("fake-implement")
        if not config.COMMIT_SUCCESSFUL_SPRINTS:
            modes.append("no-commit")
        if config.USE_CACHE:
            modes.append("use-cache")
        modes_str = ", ".join(modes) if modes else "(defaults)"

        commit_policy_lines = ["Each sprint is committed to the branch when all tests pass.", "On any test or implement failure the working tree is reverted (git restore + clean) and the loop continues."] if config.COMMIT_SUCCESSFUL_SPRINTS else ["Commits are disabled for this run (COMMIT_SUCCESSFUL_SPRINTS=False).", "Failing tests still trigger a revert (git restore + clean)."]

        sp_target_display = f"aim for ~{config.SPRINT_STORY_POINT_TARGET} SP/sprint (groups multiple tasks when they fit)" if config.SPRINT_STORY_POINT_TARGET > 0 else "disabled (one task per sprint)"
        token_limit_display = f"{config.CLAUDE_TOKEN_LIMIT:,} tokens (end-of-run summary shows Claude usage as % of this budget; Copilot not counted)" if config.CLAUDE_TOKEN_LIMIT > 0 else "not set (end-of-run summary shows raw Claude token estimate only; Copilot not counted)"

        stop_block = [
            "   How to stop this run (open a SEPARATE terminal in the same TARGET_REPO):",
            "      uv run autosprint stop         # soft: finish current sprint cleanly, then exit",
            "      uv run autosprint stop --now   # immediate: revert the working tree and exit",
            "   Audio cues on exit:",
            "      'completed correctly' — all planned sprints ran",
            "      'terminated early'    — something stopped the loop (failures, stop signal)",
            "      silence               — still running",
        ]

        banner_label = " 🚀 AUTOSPRINT "
        banner = "=" * 33 + banner_label + "=" * 33
        lines = [
            "",
            banner,
            "",
            *pit_loop_tree(config.SPRINT_STORY_POINT_TARGET),
            "",
            f"   Target repo:  {config.TARGET_REPO_PATH}",
            f"   Max sprints:  {config.MAX_SPRINTS}",
            f"   Branch:       {branch_name}",
            f"   Team:         {config.TEAM} ({len(config.TEAM_AGENTS)} member{'s' if len(config.TEAM_AGENTS) != 1 else ''} + {'shared lead' if len(config.TEAM_AGENTS) == 1 else 'team lead'}){'  — ' + config.TEAM_DESCRIPTION if config.TEAM_DESCRIPTION else ''}",
            f"   Modes:        {modes_str}",
            f"   {estimated_runtime_line(config.MAX_SPRINTS)}",
            "   Settings:",
            f"      Log level:                {config.LOG_LEVEL} (lower = more verbose)",
            f"      Max consecutive failures: {config.MAX_CONSECUTIVE_FAILURES} (abort after N reverts in a row)",
            f"      Replan cadence:           {f'at least every {config.REPLAN_EVERY_N_SPRINTS} sprints' if config.AUTO_REPLAN else 'disabled — runs the reviewed plan.md as-is'}",
            f"      How-far heartbeat:        {f'every {config.HOWFAR_HEARTBEAT_EVERY_N_SPRINTS} sprints (passive progress sensor → autosprint/logs/howfar-heartbeat.log)' if config.HOWFAR_HEARTBEAT_EVERY_N_SPRINTS > 0 else 'disabled'}",
            f"      Story-point band:         [{config.SPRINT_STORY_POINT_MIN}, {config.SPRINT_STORY_POINT_MAX}] (planner keeps task sizes in-band)",
            f"      Task-group target:        {sp_target_display}",
            f"      Claude token budget:      {token_limit_display}",
            "      Claude /usage tip:        snapshot `/usage` now in Claude Code; diff it against `/usage` after the run for exact subscription delta",
            "",
            "   Commit policy:",
            *[f"      {line}" for line in commit_policy_lines],
            "",
            "   Gates active per sprint:",
            *_active_gates_summary_lines(),
            *stop_block,
            banner,
            "",
        ]
        printlev("\n".join(lines), level=100)
    except Exception as e:
        raise add_context(e, "Failed to print start banner") from e


def print_effective_config(branch_name: str) -> None:
    """Print the resolved effective config (team roster, implementor, env overrides) and exit. Used by the show-config subcommand for debugging .env setup before a paid run."""
    try:
        lines = [
            "",
            section_banner("EFFECTIVE CONFIG", "START"),
            f"   TARGET_REPO           = {config.TARGET_REPO_PATH}",
            f"   TEAM                  = {config.TEAM}  ({len(config.TEAM_AGENTS)} agent(s))",
            f"   IMPLEMENT_AGENT       = {config.IMPLEMENT_AGENT}",
            f"   MAX_SPRINTS           = {config.MAX_SPRINTS}",
            f"   MAX_CONSECUTIVE_FAILURES = {config.MAX_CONSECUTIVE_FAILURES}",
            f"   REPLAN_EVERY_N_SPRINTS = {config.REPLAN_EVERY_N_SPRINTS}",
            f"   AUTO_REPLAN           = {config.AUTO_REPLAN}  ({'replans plan.md as the loop runs' if config.AUTO_REPLAN else 'reviewed-plan mode — runs plan.md as-is, never replans'})",
            f"   SPRINT_STORY_POINT_BAND = [{config.SPRINT_STORY_POINT_MIN}, {config.SPRINT_STORY_POINT_MAX}]",
            f"   SPRINT_STORY_POINT_TARGET = {config.SPRINT_STORY_POINT_TARGET}  ({'grouping off' if config.SPRINT_STORY_POINT_TARGET <= 0 else f'aim ~{config.SPRINT_STORY_POINT_TARGET} SP/sprint'})",
            f"   CLAUDE_TOKEN_LIMIT    = {config.CLAUDE_TOKEN_LIMIT:,}  ({'display-only %-of-budget; 0 = off; Copilot not counted' if config.CLAUDE_TOKEN_LIMIT <= 0 else 'end-of-run summary shows Claude token use as % of this budget'})",
            f"   TEST_PHASE_QUICK_ONLY = {config.TEST_PHASE_QUICK_ONLY}",
            f"   INITIAL_TESTS         = {config.INITIAL_TESTS!r}  (quick/all/none — hard-terminates on failure)",
            f"   IMPLEMENT_TESTS_FAST_MARKER = {config.IMPLEMENT_TESTS_FAST_MARKER!r}",
            f"   FAKE_IMPLEMENT_FAILURE_RATE = {config.FAKE_IMPLEMENT_FAILURE_RATE}",
            f"   LOG_LEVEL             = {config.LOG_LEVEL}  (lower = more verbose)",
            f"   COMMIT_SUCCESSFUL_SPRINTS = {config.COMMIT_SUCCESSFUL_SPRINTS}",
            f"   COMMIT_ON_START       = {config.COMMIT_ON_START}",
            f"   CREATE_BRANCH         = {config.CREATE_BRANCH}",
            f"   MANUAL_REVIEW         = {config.MANUAL_REVIEW}",
            f"   USE_CACHE             = {config.USE_CACHE}",
            f"   SAVE_CONSOLE_LOG      = {config.SAVE_CONSOLE_LOG}",
            f"   SPEAK_LEVEL           = {config.SPEAK_LEVEL}",
            f"   FAKE_PLAN_TITLE       = {config.FAKE_PLAN_TITLE!r}",
            f"   FAKE_IMPLEMENT        = {config.FAKE_IMPLEMENT}",
            f"   SELF_TEST_BEFORE_START = {config.SELF_TEST_BEFORE_START}",
            f"   (pending branch name for PIT run: {branch_name})",
            "",
            "   Team roster:",
        ]
        lines.extend(f"      - {agent.get('name', '?')} [{agent.get('assistant', '?')}/{agent.get('model', '?')}]" for agent in config.TEAM_AGENTS)
        lines.append(f"   Team lead (selector): {config.TEAM_SELECTOR.get('name', '?')} [{config.TEAM_SELECTOR.get('assistant', '?')}/{config.TEAM_SELECTOR.get('model', '?')}]")
        lines.append(f"   Implement agent: {config.IMPLEMENT_AGENT_CONFIG.get('name', '?')} [{config.IMPLEMENT_AGENT_CONFIG.get('assistant', '?')}/{config.IMPLEMENT_AGENT_CONFIG.get('model', '?')}]")
        lines.append(section_banner("EFFECTIVE CONFIG", "END"))
        lines.append("")
        printlev("\n".join(lines), level=100)
    except Exception as e:
        raise add_context(e, "Failed to print effective config") from e


def _active_gates_summary_lines() -> list[str]:
    """One short line per per-sprint gate for the startup banner. Imports `describe_gates` lazily to avoid a cli ↔ banners cycle at module load."""
    try:
        from autosprint.cli import describe_gates

        rows = describe_gates()
    except Exception:
        return ["      (gate inspection failed — run `autosprint gates` for details)"]
    lines: list[str] = []
    for row in rows:
        status = row["status"]
        name = row["name"]
        detail = row["detail"]
        icon = "✅" if status.startswith("active") else "⏸" if status == "off" else "⚠"
        lines.append(f"      {icon} {name:<16} {status:<22} {detail}")
    return lines
