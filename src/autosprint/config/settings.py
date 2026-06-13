"""PIT loop configuration — pydantic-settings with .env override.

Agent and team definitions live in `agents.py`. This module is only
concerned with environment-driven settings and helpers that resolve
string keys back to the agent/team dicts.
"""

from pathlib import Path
from typing import Any, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from autosprint.registry.agents import AGENTS
from autosprint.registry.teams import TEAMS


def _project_root() -> Path:
    # This file lives at src/autosprint/config/settings.py; the autosprint repo
    # root (which holds .claude/agents, examples, .env) is four levels up.
    return Path(__file__).resolve().parents[3]


def _default_env_file() -> str:
    return str(_project_root() / ".env")


# Speech verbosity tiers, ordered least → most talkative. Index = rank; `speak()`
# emits a message only when its tier rank ≤ the configured SPEAK_LEVEL rank.
SPEAK_LEVELS: tuple[str, ...] = ("off", "run", "reverts", "sprints", "all")


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_default_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        validate_assignment=True,
    )

    TARGET_REPO: str = Field(
        default="",
        description="Debug/dev fallback for the target-repo path. Normally unset: autosprint operates on the current working directory, or the path passed to `--target` / `autosprint init <path>`. This env var only takes effect when cwd is not a git repo (e.g. running autosprint from its own repo while developing it); it never overrides a valid cwd. Leave empty for normal use.",
    )
    MAX_SPRINTS: int = Field(
        default=100,
        description="Maximum number of sprints to run before stopping. In reviewed-plan mode (`autosprint run` without `--auto-replan`), when this is left at the default — not set in .env and no --max-sprints flag — the ceiling is auto-derived from plan.md at prepare time: 2× the pending task count, floored at 10. The plan there is a fixed human-curated list the loop cannot refill, so MAX_SPRINTS stops being a budget and is only a runaway backstop; auto-sizing keeps a deep reviewed plan from being cut short at 10. An explicitly set value (env or --max-sprints) always wins, including an explicit low value.",
    )
    LOG_LEVEL: int = Field(
        default=50,
        description=("Console verbosity threshold from 0 to 100. Lower values print more messages; higher values print fewer."),
    )
    MAX_CONSECUTIVE_FAILURES: int = Field(default=5, description="Stop the PIT loop if this many sprints fail in a row.")
    TEAM: str = Field(
        default="council",
        description="Which team configuration to use. See agents.TEAMS for available options. Default `council` is a 6-planner team — North Star + Bug Hunter + Pragmatist on Opus 4.8, Tester + Minimalist + Architect on GPT-5.5 — merged by an Opus 4.8 team lead. Six deliberately orthogonal lenses for broad coverage. Heavier than the smaller teams (6 LLM dispatches per plan); in the reviewed-plan workflow it runs once via `autosprint plan`, so the weight is paid once. Claude-only users: set TEAM=solo_opus. Copilot-only users: set TEAM=solo_gpt55. Lighter teams for the in-loop auto-plan cadence: `builder` (4 voices) or `duo` (2).",
    )
    SPRINT_STORY_POINT_MIN: int = Field(
        default=2,
        description="Lower end of the preferred story-point band for a sprint task. Soft signal: a single '(1)' task that stands alone (e.g. a standalone bug fix) passes freely, but a *pattern* of sub-min tasks prompts the team lead to bundle adjacent items that share a concern. Prevents a drift toward many trivial iterations.",
    )
    SPRINT_STORY_POINT_MAX: int = Field(
        default=20,
        description="Upper end of the preferred story-point band for a sprint task. Team members tag each proposed task with '(N)' where N is a Fibonacci-ish estimate; the team lead must split any task whose estimate exceeds this max before writing plan.md. Sizing signal only — still one task per sprint regardless of estimate. Current default (20) is intentionally high so the dashboard can surface whether very large tasks actually ship; once the revert-rate pattern stabilises, tune down in .env. Healthy band is ~20–35%; below 15% suggests MAX could go higher, above 40% suggests MAX is too high.",
    )
    SPRINT_STORY_POINT_TARGET: int = Field(
        default=8,
        description="Task-grouping target. When > 0, the orchestrator combines the top N pending tasks (greedily, without exceeding MAX) into a single 'task group' handed to the Implement agent in one sprint. Amortises the per-sprint test overhead when individual tasks are smaller than the target. Whole-group atomic revert on test failure (one bad task loses the whole group — accept this tradeoff). Untagged tasks never get grouped. Set to 0 to disable (single-task-per-sprint behavior).",
    )
    # Two-level naming: the field `IMPLEMENT_AGENT` is the *string key* into `agents.AGENTS`;
    # the resolved agent dict (the thing dispatch actually needs) is exposed via the
    # `IMPLEMENT_AGENT_CONFIG` property below. Callers wanting "the agent record" use
    # `config.IMPLEMENT_AGENT_CONFIG`; callers wanting "which key is configured" use
    # `config.IMPLEMENT_AGENT`. Same pattern for TEAM and HOWFAR_AGENT.
    IMPLEMENT_AGENT: str = Field(
        default="implementor_opus48",
        description="Agent key (into agents.AGENTS) for the Implement phase. Default `implementor_opus48` (Claude Opus 4.8) is the strong production pick. Copilot-only users: set IMPLEMENT_AGENT=implementor_gpt55. Debug/quick iteration: set IMPLEMENT_AGENT=quick_a_gpt41_copilot. The resolved agent dict is at `config.IMPLEMENT_AGENT_CONFIG`.",
    )
    IMPLEMENT_FALLBACK_AGENT: str = Field(
        default="implementor_gpt55",
        description="Refusal-fallback agent. When the primary `IMPLEMENT_AGENT` returns a refusal-pattern failure (the Opus 4.8 read-tool malware-reminder misread), the orchestrator reverts the primary's partial edits and re-dispatches the same task group to this fallback agent. Default `implementor_gpt55` (Copilot) sidesteps the failure mode entirely because the Copilot SDK doesn't carry the offending reminder. Set to empty string to disable the fallback (legacy behaviour: refusal → revert + raise). Only fires on refusal patterns; non-refusal failures (test failures, malformed responses, real bugs) revert as before so problems aren't masked.",
    )
    HOWFAR_AGENT: str = Field(
        default="howfar_opus48",
        description="Agent that runs `autosprint how-far` — the read-only distance-to-destination measurement. Must be a key in agents.AGENTS. Default `howfar_opus48` (Claude Opus 4.8). On a Copilot-only setup, or when Claude tokens are exhausted, set HOWFAR_AGENT=howfar_gpt55 (Copilot GPT-5.5) — or pass `autosprint how-far --agent howfar_gpt55` for a one-off. The agent runs with the read-only tool preset regardless, so how-far can never modify the repo.",
    )
    HOWFAR_HEARTBEAT_EVERY_N_SPRINTS: int = Field(
        default=10,
        description="Run `autosprint how-far` automatically every N sprints from inside the PIT loop as a passive progress sensor. The full report is appended to `autosprint/logs/howfar-heartbeat.log`; a compact headline + verdict is printed inline so a watching human (or a returning AFK user reading the log) can spot 'progress has been flat for 30 sprints' without re-running how-far by hand. Read-only, never feeds back into planning (Goodhart-safe). Set to 0 to disable. Cost: ~1 LLM dispatch per N sprints (~5% overhead at N=10).",
    )
    COMMIT_SUCCESSFUL_SPRINTS: bool = Field(
        default=True,
        description="If False, skip git commit after a successful sprint. Useful for testing the full PIT loop without creating commits.",
    )
    REPLAN_EVERY_N_SPRINTS: int = Field(
        default=5,
        description="Run the Plan phase at least every N sprints, even if the plan still has pending items.",
    )
    PLAN_RECENT_COMPLETED_COUNT: int = Field(
        default=5,
        description="How many completed tasks to keep inline in plan.md. Older completed tasks are dropped (git history is the archive).",
    )
    TEST_PHASE_QUICK_ONLY: bool = Field(
        default=False,
        description="If True, the Test phase runs only the quick-marker subset (pytest -m 'not slow') every sprint. Default False: the Test phase runs ALL tests so every commit is fully validated. Independent of IMPLEMENT_TESTS_FAST_MARKER (which controls the Implement agent's inline self-check — that one always runs the quick subset). Has no effect for runners without a quick/slow split (e.g. vitest).",
    )
    TARGET_TEST_RUNNER: str = Field(
        default="auto",
        description="Which test runner the Test phase uses against the target repo. 'auto' (default) detects it from the repo's marker files — a Python project (pyproject.toml / pytest.ini / setup.cfg) resolves to 'pytest'; a JS/TS project (package.json, no Python markers) resolves to 'vitest'. Set 'pytest' or 'vitest' explicitly to override detection. The chosen runner also fixes the output parser, so TEST_COMMAND (if set) must still produce that runner's output format.",
    )
    TEST_COMMAND: str = Field(
        default="",
        description="Optional override for the Test phase's run command. Empty (default) means the resolved runner builds its own command. When set, the string is shell-word-split and run verbatim (first token resolved on PATH) — useful for a non-standard invocation such as a monorepo fan-out script. The runner named by TARGET_TEST_RUNNER still supplies the output parser, so a custom command must still emit that runner's output format.",
    )
    TS_TYPECHECK: bool = Field(
        default=True,
        description="For TS/JS targets (vitest runner): also run a type-check gate (`tsc --noEmit`, or the target's `package.json` `scripts.typecheck` when defined) before vitest, and require both green for the Test phase to pass. Vitest strips types without checking them, so without this gate a real type error would ship green. Set False to skip the gate when the target intentionally allows type drift (rare). No-op for non-vitest runners.",
    )
    SMOKE_TEST: str = Field(
        default="auto",
        description="Per-sprint smoke test that runs after pytest/vitest pass — verifies the target app actually starts. `auto` (default): for Python targets with `src/<pkg>/__main__.py`, runs `python -m <pkg> --help` (5s timeout, headless env vars set); falls back to a 3s spawn-and-survive check if --help doesn't return cleanly. `off`: skip entirely. Any other value is treated as a literal command (shell-word-split) and run as-is. Catches bugs pytest misses: ImportError in __main__.py, module-level exceptions at package init, missing deps that are mocked away in tests, wiring bugs between components. A failed smoke triggers the same revert as a failed test — the sprint never commits.",
    )
    SMOKE_TEST_TIMEOUT: int = Field(
        default=5,
        description="Seconds to wait for the smoke test before giving up. Applies to the `--help` form. The spawn-and-survive fallback uses a separate 3s window (a `python -m <pkg>` that survives 3 seconds without crashing is treated as healthy — captures the 'GUI app that opens a window and waits' case).",
    )
    IMPORT_CHECK: bool = Field(
        default=True,
        description="Pre-smoke import check (`python -c 'import <pkg>'`) for Python targets. Catches package-level ImportError, top-level exceptions in `__init__.py`, missing deps mocked away in tests — without needing `__main__.py`. Default on because it's cheap (~50ms), valuable, and works for library projects too. Disable for non-Python targets (auto-skips when there's no `pyproject.toml [project].name`).",
    )
    FORMAT_CHECK: str = Field(
        default="off",
        description="Pre-test format gate. `off` (default): skip. `auto`: auto-detect — runs `black --check src tests` for Python targets when black is on PATH, or `npx prettier --check .` for JS/TS targets when prettier is in package.json. Any other value is treated as a literal command. Fails the sprint if formatting is off — catches sloppy LLM output before commit. Opt-in because not every project uses a formatter and a forced rewrite-and-commit dance would surprise existing users.",
    )
    LINT_CHECK: str = Field(
        default="off",
        description="Pre-test lint gate. `off` (default): skip. `auto`: auto-detect — runs `ruff check .` when `[tool.ruff]` exists in pyproject.toml; else `flake8 .` when `.flake8` or `[flake8]` in setup.cfg; else `mypy .` when `mypy.ini` or `[tool.mypy]` exists. Any other value is a literal command. Fails the sprint on lint errors — catches subtle bugs (unused imports, mutable defaults, broad excepts) pytest doesn't see. Opt-in to avoid surprising users whose lint config might not match their LLM-written code.",
    )
    PYTEST_COLLECT_GATE: bool = Field(
        default=False,
        description="If true, runs `pytest --collect-only -q` as a pre-test gate before the real pytest invocation. Fails fast on collection errors (broken `conftest.py`, syntax errors in test files, import errors in `tests/`) with a cleaner error than letting pytest abort mid-suite. Off by default because pytest already fails clearly on collection — the gate's marginal value is faster failure on broken-test-file sprints.",
    )
    COVERAGE_TRACK: bool = Field(
        default=False,
        description="If true, runs pytest with `--cov=<pkg> --cov-report=term-missing` and tracks the coverage percentage in `autosprint/logs/coverage-history.log` (one line per sprint). Currently warn-only — a drop in coverage logs a warning to console but does NOT revert the sprint. Future v2 will gate on regression once baseline-storage and noise-handling stabilise. Requires `pytest-cov` installed in the target repo.",
    )
    FAKE_IMPLEMENT_FAILURE_RATE: float = Field(
        default=0.2,
        description="Probability (0.0-1.0) that _fake_implement returns a simulated failure. Only consulted when FAKE_IMPLEMENT=True. Override via env for stress/smoke testing fake loops.",
    )
    IMPLEMENT_TESTS_FAST_MARKER: str = Field(
        default="not slow",
        description="Pytest marker expression the Implement agent should use for its inline self-check pytest run. Default 'not slow' keeps Implement fast. The Test phase is controlled independently via TEST_PHASE_QUICK_ONLY.",
    )
    DEBUG_TRACEBACK: bool = Field(
        default=False,
        description="If True, the top-level error handler prints the full Python traceback alongside the add_context breadcrumb chain. Useful when diagnosing hard failures.",
    )
    CACHE_MAX_ENTRIES: int = Field(
        default=500,
        description="Max number of cached agent-response files to retain in autosprint/cache/. Older entries are evicted (oldest mtime first) on startup. Set to 0 to disable the cap.",
    )
    LLM_RETRY_ATTEMPTS: int = Field(
        default=3,
        description="On a transient dispatch failure (network error or any exception raised inside the SDK call), retry the request this many times before giving up. Default 3 with the default LLM_RETRY_BACKOFF_SECONDS=5 gives a 5s/15s/45s exponential schedule — ~65s of total tolerance, tuned for the typical 30-120s network blip seen on overnight `--auto-replan` runs. Set to 0 to disable retry. Bump higher (e.g. 5) if your network is flaky enough that a longer tolerance is worth the wait.",
    )
    LLM_RETRY_BACKOFF_SECONDS: float = Field(
        default=5.0,
        description="Initial backoff (seconds) before the first retry attempt; triples between attempts. Default 5 with the default LLM_RETRY_ATTEMPTS=3 gives 5s/15s/45s. Triple (rather than double) is tuned for overnight network blips where doubling burns the retry budget too fast on a real 60s outage. Only consulted when LLM_RETRY_ATTEMPTS > 0.",
    )
    LLM_SESSION_TIMEOUT_SECONDS: int = Field(
        default=900,
        description="Max wall-clock seconds per LLM session (Copilot send_and_wait timeout). Needs headroom for big package installs (e.g. `uv add scikit-learn joblib` can take several minutes). Default 15 min; bump if you frequently add heavy deps.",
    )
    CLAUDE_TOKEN_LIMIT: int = Field(
        default=0,
        description="Soft Claude-token budget for this run — display-only. When > 0, the end-of-run Claude-usage line shows the estimated token count as a percentage of this limit so the user can see at a glance how close the run came to a self-imposed budget. Copilot calls are NOT counted here because Copilot is subscription-priced (tokens don't track cost); this budget targets Claude's pay-per-token model specifically. 0 (default) disables the percentage display. Not a hard limit: autosprint never stops a run based on this value. Estimation uses a rough 4-chars-per-token heuristic; actual usage may vary ±30%.",
    )
    SELF_TEST_BEFORE_START: bool = Field(
        default=False,
        description="Run autosprint's own test suite before starting the PIT loop.",
    )
    INITIAL_TESTS: str = Field(
        default="quick",
        description="Scope of the target-repo tests that run once at startup as a sanity check. 'quick' = pytest -m 'not slow' (default, fast-fail on obvious breakage). 'all' = full suite (slower, best for long runs where it matters that the baseline is fully green). 'none' = skip entirely. On failure, autosprint terminates so you can fix the repo before any sprint burns LLM tokens. Override per run with --initial-tests {quick,all,none}.",
    )
    USE_CACHE: bool = Field(
        default=False,
        description="If true, dispatch.query_agent reads cached responses from .cache/ before dispatching to the SDK. Writes always happen. Enable for dev iteration; disable for production runs.",
    )
    CREATE_BRANCH: bool = Field(
        default=True,
        description="If true, cut a fresh git branch and snapshot uncommitted changes before the PIT loop. Disable with --no-branch to run on the current branch.",
    )
    FAKE_PLAN_TITLE: str = Field(
        default="",
        description="If set, plan_phase skips the LLM call and uses this task title. Set via --fake-plan CLI flag for debug/quick iteration.",
    )
    FAKE_PLAN_DESC: str = Field(
        default="",
        description="Description for the --fake-plan task. Ignored unless FAKE_PLAN_TITLE is set.",
    )
    FAKE_IMPLEMENT: bool = Field(
        default=False,
        description="If true, run_implement skips the LLM call and simulates success (appends a line to hello.md) or stochastic failure. For debug iteration.",
    )
    SKIP_FIRST_PLAN: bool = Field(
        default=False,
        description="If true, the first sprint does NOT force a replan — it reuses whatever plan.md is already on disk. Normal cadence still applies from sprint 2 onward. Debug-only knob: lets you iterate on Implement/Test without paying for a fresh planning call every run. If plan.md is empty, `should_replan` will still trigger a plan via its `plan.is_empty()` branch — so this flag is a no-op on a clean target.",
    )
    AUTO_REPLAN: bool = Field(
        default=False,
        description="If true, the Plan phase regenerates plan.md as the loop runs — re-planning every REPLAN_EVERY_N_SPRINTS sprints, after a task fails twice, and whenever the plan empties. This is the autonomous self-planning loop, opted into with `autosprint run --auto-replan`. Default False is reviewed-plan mode: `autosprint run` executes the human-reviewed plan.md top to bottom and never invents new tasks; `should_replan` returns False, overriding the first-sprint force, the REPLAN_EVERY_N_SPRINTS cadence, and the replan-after-2-failures trigger. When plan.md drains the loop exits cleanly — reviewed-plan mode cannot refill the plan. A task that keeps failing is not rescued by a replan; it reverts each sprint until MAX_CONSECUTIVE_FAILURES aborts the run.",
    )
    MANUAL_REVIEW: bool = Field(
        default=False,
        description="If true, prompt the user after the Plan phase to approve the next task before Implement runs. Rejecting aborts the loop cleanly.",
    )
    COMMIT_ON_START: bool = Field(
        default=False,
        description="If true, commit any pre-existing uncommitted changes in TARGET_REPO at startup automatically instead of prompting Y/N. Startup hygiene only — autosprint wants a clean working tree before it cuts a branch. Distinct from the per-sprint commit, which is always on via COMMIT_SUCCESSFUL_SPRINTS.",
    )
    SPEAK_LEVEL: str = Field(
        default="run",
        description="How much autosprint speaks aloud via pyttsx3. Cumulative tiers, least → most talkative: 'off' (silent), 'run' (run-level events only — completed / reviewed-plan-complete / terminated / halts / stop), 'reverts' (+ each sprint that reverts), 'sprints' (+ each sprint that succeeds), 'all' (+ each sprint start). Default 'run' keeps the end-of-run announcement without per-sprint chatter.",
    )
    TTS_VOICE: str = Field(
        default="zira",
        description="Case-insensitive substring matched against each pyttsx3 voice's id and name. First match wins. Empty string falls back to the pyttsx3 default voice. On Windows, 'zira' = Microsoft Zira Desktop (en-US), 'hazel' = Microsoft Hazel Desktop (en-GB). Install extra SAPI5 voices via Windows Settings → Time & Language → Speech.",
    )
    TTS_RATE: int = Field(
        default=400,
        description="pyttsx3 speech rate in words per minute. SAPI5 default is ~200; 400 is roughly 2x speed. Range on most engines: 100–500.",
    )
    SAVE_CONSOLE_LOG: bool = Field(
        default=True,
        description="If true, tee every printlev call to TARGET_REPO/autosprint/logs/console-verbose.log so past runs can be reviewed. A run-started separator is appended on each autosprint start.",
    )
    PLAN_DECISIONS_RECENT_COUNT: int = Field(
        default=30,
        description="Soft cap on plan-decisions.md: on startup, trim the file so only the last N sprint entries are kept inline. Older entries are dropped silently (git history archives the full trail). Mirrors PLAN_RECENT_COMPLETED_COUNT for plan.md — without this the file grew past 1 MB (6500+ lines) in real use. Set to 0 to disable the cap.",
    )
    CONSOLE_LOG_MAX_BYTES: int = Field(
        default=1_048_576,  # 1 MiB
        description="Soft cap on console-verbose.log: on startup, if the file exceeds this size, drop the oldest `# === run started ===` blocks until it fits. Keeps grep-friendly logs usable without requiring a manual `clear-logs`. Set to 0 to disable.",
    )
    IMPLEMENT_PARSER_RETRY: bool = Field(
        default=True,
        description="When the implementer's response is missing the RESULT block in the required format (but work may already be on disk), send one follow-up call asking for the RESULT block alone before reverting. Recovers sprints where the agent forgot `---END---` or wrote a plain-text summary instead of JSON. Set to False to keep strict immediate-revert semantics.",
    )
    SPRINT_TASK_COUNT_CAP_INITIAL: int = Field(
        default=3,
        description="Initial and maximum number of tasks the sprint bundler will pull into a single sprint. Keeps 4-task groups (higher atomic-revert blast radius) off by default. The cap shrinks on real reverts and recovers on green sprints — see SPRINT_TASK_COUNT_CAP_MIN. SP target is a separate lever (SPRINT_STORY_POINT_TARGET); this caps the *count* so one big important task can still ship alone.",
    )
    SPRINT_TASK_COUNT_CAP_MIN: int = Field(
        default=1,
        description="Floor for the adaptive task-count cap. The cap never shrinks below this after a revert. Default 1 preserves the ability to ship a single task per sprint at the extreme; raise to 2 if you never want solo sprints.",
    )
    LOCK_DESTINATION: bool = Field(
        default=False,
        description="When True, plan-phase prompts (team members and team lead) and the implementor prompt all carry an explicit instruction NOT to propose any task that expands `autosprint/destination.md` — no new entries under `## AI-generated subgoals`, no new entries in `## Open questions`, no new entries in `## Technical decisions`. Use when the spec is final for this run and you want the loop to **converge** on existing content rather than expand scope. Implementation-only tasks, refactors, test consolidation, bug fixes, and ADR entries (which go to `adr.md`) remain allowed. The implementor will submit `submit_implement_failure` with reason='target-state-locked' if a task requires expanding destination.md, surfacing the conflict for human review. Default False (existing behavior — destination expansion is a normal task type).",
    )
    PRIORITIZE: str = Field(
        default="",
        description="Freeform priority hint for this planning run. When non-empty, the Plan phase appends a 'User priority for this run' section to both team-member and team-lead prompts, surfacing the string verbatim and asking the planner to put tasks addressing it near the top of plan.md. The text can be a fresh request ('add dark mode toggle to settings page') or a vague reference into destination.md ('see the section about authentication'); the planner resolves the reference itself. Typically set per-invocation via `--prioritize TEXT`, not in .env — the priority is run-scoped, not project-scoped. Empty (default) means no priority is in effect.",
    )

    @field_validator("TEAM")
    @classmethod
    def validate_team(cls, value: str) -> str:
        if value not in TEAMS:
            raise ValueError(f"TEAM must be one of {sorted(TEAMS)}, got '{value}'")
        return value

    @field_validator("INITIAL_TESTS")
    @classmethod
    def validate_initial_tests(cls, value: str) -> str:
        allowed = {"quick", "all", "none"}
        if value not in allowed:
            raise ValueError(f"INITIAL_TESTS must be one of {sorted(allowed)}, got '{value}'")
        return value

    @field_validator("TARGET_TEST_RUNNER")
    @classmethod
    def validate_target_test_runner(cls, value: str) -> str:
        allowed = {"auto", "pytest", "vitest"}
        if value not in allowed:
            raise ValueError(f"TARGET_TEST_RUNNER must be one of {sorted(allowed)}, got '{value}'")
        return value

    @field_validator("SPEAK_LEVEL")
    @classmethod
    def validate_speak_level(cls, value: str) -> str:
        if value not in SPEAK_LEVELS:
            raise ValueError(f"SPEAK_LEVEL must be one of {list(SPEAK_LEVELS)}, got '{value}'")
        return value

    @field_validator("IMPLEMENT_AGENT")
    @classmethod
    def validate_agent(cls, value: str) -> str:
        if value and value not in AGENTS:
            raise ValueError(f"IMPLEMENT_AGENT must be one of {sorted(AGENTS)}, got '{value}'")
        return value

    @field_validator("IMPLEMENT_FALLBACK_AGENT")
    @classmethod
    def validate_fallback_agent(cls, value: str) -> str:
        # Empty string is the explicit "disable refusal-fallback" sentinel; otherwise the value must
        # name a real agent so a typo can't silently disable the fallback at runtime.
        if value and value not in AGENTS:
            raise ValueError(f"IMPLEMENT_FALLBACK_AGENT must be either '' (disabled) or one of {sorted(AGENTS)}, got '{value}'")
        return value

    @field_validator("HOWFAR_AGENT")
    @classmethod
    def validate_howfar_agent(cls, value: str) -> str:
        if value and value not in AGENTS:
            raise ValueError(f"HOWFAR_AGENT must be one of {sorted(AGENTS)}, got '{value}'")
        return value

    @property
    def TEAM_AGENTS(self) -> list[dict[str, Any]]:
        return TEAMS[self.TEAM]["agents"]

    @property
    def TEAM_SELECTOR(self) -> dict[str, Any]:
        team = TEAMS[self.TEAM]
        return team.get("selector", team["agents"][0])

    @property
    def TEAM_DESCRIPTION(self) -> str:
        """Human-readable one-line team composition — empty string if the team dict omits the optional `description` key, so old teams without it degrade gracefully."""
        return str(TEAMS[self.TEAM].get("description") or "")

    @property
    def TARGET_REPO_PATH(self) -> Path:
        return Path(self.TARGET_REPO).resolve() if self.TARGET_REPO else Path.cwd()

    @property
    def IMPLEMENT_AGENT_CONFIG(self) -> dict[str, Any]:
        return AGENTS[self.IMPLEMENT_AGENT]

    @property
    def IMPLEMENT_FALLBACK_AGENT_CONFIG(self) -> dict[str, Any] | None:
        """Return the fallback agent's dict, or None when the fallback is disabled (empty IMPLEMENT_FALLBACK_AGENT). Callers gate their fallback branch on `is None` so the rest of the orchestrator doesn't have to know about the disabled-sentinel encoding."""
        return AGENTS[self.IMPLEMENT_FALLBACK_AGENT] if self.IMPLEMENT_FALLBACK_AGENT else None

    @property
    def HOWFAR_AGENT_CONFIG(self) -> dict[str, Any]:
        return AGENTS[self.HOWFAR_AGENT]

    @model_validator(mode="after")
    def _warn_if_failure_cap_defeats_replan_escape_valve(self) -> Self:
        """If `MAX_CONSECUTIVE_FAILURES <= REPLAN_EVERY_N_SPRINTS`, the loop aborts before the planner gets a chance to replan after failures — defeating the "let the planner rescue us" escape valve. Only meaningful in auto-replan mode: reviewed-plan mode (the default) never does a periodic replan, so there is no escape valve to defeat and the comparison is moot — gate the warning on `AUTO_REPLAN` so a default-config run stays silent. Emit to stderr (not a hard error; some short-run auto-replan configs legitimately want this) so the user sees the misconfiguration before a real failure exposes it."""
        if self.AUTO_REPLAN and self.MAX_CONSECUTIVE_FAILURES <= self.REPLAN_EVERY_N_SPRINTS:
            import sys as _sys

            _sys.stderr.write(f"[warning] MAX_CONSECUTIVE_FAILURES={self.MAX_CONSECUTIVE_FAILURES} is ≤ REPLAN_EVERY_N_SPRINTS={self.REPLAN_EVERY_N_SPRINTS}: the run will abort on consecutive failures before the planner gets a chance to course-correct. Recommended: set MAX_CONSECUTIVE_FAILURES > REPLAN_EVERY_N_SPRINTS.\n")
        return self


config = Config()

# Field names explicitly set by environment / .env at import time, captured before any
# runtime mutation. The per-repo config.toml overlay (cli._apply_config_toml) consults
# this so an env value is never overwritten by config.toml: defaults < config.toml < env.
ENV_SET_FIELDS: frozenset[str] = frozenset(config.model_fields_set)
