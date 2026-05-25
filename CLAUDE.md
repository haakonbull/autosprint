# Autosprint

Automated sprint loop — **Plan, Implement, Test, Commit** — powered by the Claude Agent SDK and GitHub Copilot SDK.

## Language

All tool output, code, comments, commit messages, and user-facing documentation are English only.

## What this project does

Autosprint runs a PIT loop against a **target repository**. Each sprint:
1. **Plan** — one or more AI agents propose tasks, a selector merges them into `autosprint/plan.md`
2. **Implement** — an AI agent implements the top pending task
3. **Test** — pytest runs the target repo's test suite (Python, not LLM)
4. **Commit** — if tests pass, changes are committed to the branch

**Autosprint never modifies itself.** The orchestrator refuses to run if
`TARGET_REPO` points at the autosprint repo. All file changes happen inside
the target repository; autosprint contains only methodology, prompts, and
orchestration.

## Project structure

```
src/autosprint/
  orchestrator.py   # pit_loop, commit_sprint, plan_only, main — the PIT loop itself
  cli.py            # argparse, prepare(), one-shot subcommands, stop control
  init.py           # `autosprint init` + config wizard + prepare-step seed/check helpers
  how_far.py        # `autosprint how-far` — read-only distance-to-destination report
  plan_phase.py     # Plan phase: prompts, team-lead context, update_plan, replan
  implement_phase.py # Implement phase: dispatch, refusal-fallback, failure logs
  test_phase.py     # Test phase: drives the test runner, revert/commit decision, self-test
  test_runners.py   # TestRunner adapters — normalizes test execution per language (pytest + vitest)
  run_log.py        # sprint-outcomes log, plan-decisions log, runtime stats, escalation
  banners.py        # Section banners, PIT-loop tree, start banner, show-config print
  git_ops.py        # git, git_restore, git_commit, summarise_working_tree_diff
  parsing.py        # Result-block parsing for agent responses
  paths.py          # Path/filename constants for autosprint/* layout
  agents.py         # Individual agent definitions (name, model, system_prompt, tools) + AGENTS registry
  teams.py          # TEAM_* dicts + TEAMS registry (composes agents into planning rosters)
  dispatch.py       # query_agent / query_agents — SDK dispatch, caching, parallelism
  config.py         # Environment-driven settings via pydantic-settings
  plan.py           # plan.md parser/writer, Plan/PendingTask/CompletedTask, group_titles
  db.py             # SQLite mirror of sprint outcomes
  output.py         # printlev() — level-gated stdout
  errors.py         # add_context, RevertReason, PhaseFailedError, StopRequested, WaypointReached
.claude/agents/
  plan-agent.md     # Prompt template for a single agent's Plan phase
  plan-team.md      # Prompt for the selector when merging team proposals
  implement.md      # Prompt for the Implement phase
tests/
  test_plan_phase.py
  test_implement.py
  test_pit_loop.py
  test_plan.py
```

`orchestrator.py` re-exports most names from the phase / helper modules so
existing `from autosprint.orchestrator import _foo` paths in tests keep
working. When monkeypatching internal helpers in tests, patch the *home*
module (e.g. `autosprint.plan_phase`, not `autosprint.orchestrator`) — the
re-export is just a name alias, the function looks up dependencies in its
own module's namespace.

## Running

```bash
# Activate venv (PowerShell)
.venv\Scripts\Activate.ps1

# Activate venv (bash / Claude Code terminal)
source .venv/Scripts/activate

# Run tests
uv run pytest

# Run the PIT loop (executes the reviewed autosprint/plan.md)
uv run autosprint run

# Plan phase only — draft autosprint/plan.md, then exit
uv run autosprint plan

# Verify the setup can run (target repo, destination.md, CLIs, agent round-trip)
uv run autosprint doctor

# Read-only distance-to-destination report (makes no changes)
uv run autosprint how-far

# Stop a running loop (from another terminal pointed at the same target repo)
uv run autosprint stop           # soft — finish current sprint, exit
uv run autosprint stop --now     # immediate — revert + exit

# Debug mode (LOG_LEVEL=1 returns hardcoded task, skips branch/test setup)
# Set LOG_LEVEL in config or .env
```

## Key concepts

- **Agent** — a dict with `name`, `assistant` (`"claude"` or `"copilot"`), `model`, `system_prompt`, `tools`. Defined in `agents.py`.
- **Team** — a dict with an `agents` list and an optional `selector` agent. Defined in `teams.py`.
- **Selector** — a designated agent who merges proposals from a multi-agent team into the final task list
- **`query_agent()`** — dispatches to Claude or Copilot, handles caching (keyed by agent + prompt hash)
- **Cache** — stored in `.cache/`, active when `LOG_LEVEL <= 15`. Skip with `skip_cache=True`
- **`parse_result()`** — extracts JSON from agent output using `---RESULT---...---END---` markers or JSON object scan
- **`PhaseFailedError`** — raised when a phase fails after retries; triggers revert + continue to next sprint
- **Escalation** — if the same task fails 3+ times in the last 20 log lines, the loop raises
- **Waypoint** — optional `autosprint/waypoint.md` file describing an *intermediate* target the user wants the loop to reach before continuing toward `destination.md`. When present and not paused, the Plan phase aims at it exclusively. When the team lead concludes the waypoint state is satisfied, it sets `waypoint_reached: true` in its JSON output; the orchestrator appends a `> **Status:** reached <date>` marker to the file (no auto-archive — the user reviews and decides) and halts via the `WaypointReached` exception. Pause without losing content: rename to `waypoint.md.paused`. Template: `examples/waypoint.example.md`.

## Waypoint workflow

When you have a specific feature or sub-goal you want the loop to focus on:

1. **Write `autosprint/waypoint.md`** — state-shaped prose with acceptance criteria (use the example template). The waypoint must be reachable without violating destination/ADRs.
2. **(Optional) Preview the decomposition** — `uv run autosprint plan` shows the planner's first take. Note: this is preview-only — the real run re-plans, so don't hand-edit plan.md and expect it to stick.
3. **Run the loop** — `uv run autosprint run`. Each sprint header logs `🧭 Waypoint active: <title>`.
4. **Loop halts when reached** — open `waypoint.md` to see the appended `> **Status:** reached` marker with the team lead's rationale. Decide what's next: delete the file (waypoint done), revise it (not done yet, remove the marker and continue), or set a new waypoint.

For one-shot work that fits in a single sprint, skip the waypoint and just open Claude Code — the loop's value is multi-sprint discipline.

## Config

Resolved from (low → high precedence): code defaults < per-repo `autosprint/config.toml` < environment / `.env` < CLI flags. Managed via pydantic-settings. Key vars:
- `TEAM` — team name (key in `teams.TEAMS`)
- `IMPLEMENT_AGENT` — agent key used for the Implement phase (key in `agents.AGENTS`)
- `HOWFAR_AGENT` — agent key used by `autosprint how-far` (key in `agents.AGENTS`)
- `MAX_SPRINTS` — sprint limit (in reviewed-plan mode, auto-sizes from `plan.md` length when left unset)
- `MAX_CONSECUTIVE_FAILURES` — consecutive failure limit before stopping
- `TARGET_REPO` — debug/dev fallback path; normal use targets the cwd or `--target`
- `LOG_LEVEL` — `1` = debug/hardcoded task, `15` = use cache, `50+` = full production mode
- `IMPLEMENT_TESTS_FAST_MARKER` — pytest marker expression for the Implement agent's inline self-check (default `"not slow"`). Mark slow tests in the target repo with `@pytest.mark.slow`.
- `TEST_PHASE_QUICK_ONLY` — if True, the Test phase runs only the quick (`not slow`) subset each sprint; default False runs the full suite every sprint.
- `TARGET_TEST_RUNNER` — which test runner the Test phase uses (`auto` | `pytest` | `vitest`); `auto` detects from the target repo's marker files. `TEST_COMMAND` optionally overrides the run command. See `test_runners.py`.

## Agents and skills

Project-local agents live in `.claude/agents/`. Skills in `.claude/skills/`. These are separate from CLAUDE.md and are not affected by changes here.
