"""Extracted from the original autosprint.app.cli module."""

from __future__ import annotations

import argparse
import difflib
import re
import sys
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

from autosprint.config import ENV_SET_FIELDS, _project_root, config
from autosprint.domain.plan import read_plan_md
from autosprint.util.errors import add_context
from autosprint.util.output import printlev
from autosprint.util.paths import AUTOSPRINT_DIR_NAME

# Bundled CLI presets: a single flag that expands to a (team, implement_agent)
# pair. Kept at the CLI layer — the planning_team and implement_agent concepts
# stay separate internally because they're orthogonal axes (one team can be
# paired with any implement agent). The preset is a UX shortcut, not a merge.
_CLI_PRESETS: dict[str, dict[str, Any]] = {
    "solo-gpt55": {"team": "solo_gpt55", "implement_agent": "implementor_gpt55"},
    "claude-only": {"team": "council_opus", "implement_agent": "implementor_opus48"},
    "copilot-only": {"team": "council_gpt55", "implement_agent": "implementor_gpt55"},
    "quick-debug": {
        "team": "quick",
        "implement_agent": "implementor_gpt41",
        "initial_tests": "none",
        "sp_target": 3,
        "sp_max": 3,
    },
}
_ONESHOT_COMMANDS: frozenset[str] = frozenset({"self-test", "show-config", "settings", "init", "doctor", "how-far", "clear-logs", "stop", "teams", "agents", "presets", "gates", "sprints", "logs", "skills", "config-keys"})
_INVALID_SUBCOMMAND_RE = re.compile(r"argument COMMAND: invalid choice: '([^']+)' \(choose from (.+)\)")


class _TerseArgumentParser(argparse.ArgumentParser):
    """argparse subclass that replaces the default `error()` output for the most common typo case — an unknown subcommand — with a compact "did you mean?" hint plus a short list of valid commands. Default argparse dumps the full multi-line usage banner for any error, which buries the actual mistake. Other errors still fall through to the default formatting so flag-level mistakes keep their full context.

    Two-pass suggestion logic: first scans the *other* CLI tokens for a known subcommand (so `autosprint list teams` resolves to "Did you mean: teams?" by finding the valid `teams` token after the unknown `list`); then falls back to difflib close-matching on the typed word (catches single-word typos like `clear-logss` → `clear-logs`).
    """

    def error(self, message: str) -> NoReturn:
        match = _INVALID_SUBCOMMAND_RE.search(message)
        if match:
            typed = match.group(1)
            choices = [c.strip() for c in match.group(2).split(",")]
            choice_set = set(choices)
            # First: did the user type extra verbs like `autosprint list teams`? Pick the trailing valid command.
            extra_token_hits = [tok for tok in sys.argv[1:] if tok != typed and not tok.startswith("-") and tok in choice_set]
            suggestions = extra_token_hits[:3] if extra_token_hits else difflib.get_close_matches(typed, choices, n=3, cutoff=0.5)
            lines = [f"autosprint: unknown subcommand '{typed}'"]
            if suggestions:
                hint = suggestions[0] if len(suggestions) == 1 else ", ".join(suggestions)
                lines.append(f"  Did you mean: {hint}?")
            lines.append("")
            lines.append("Valid subcommands: " + ", ".join(choices))
            lines.append("Run `autosprint --help` for descriptions.")
            print("\n".join(lines), file=sys.stderr)
            sys.exit(2)
        super().error(message)


def add_run_options(parser: argparse.ArgumentParser) -> None:
    """Attach the run-mode options that apply to the `run`, `plan`, and `show-config` subcommands. Kept in one helper so all three stay in lockstep."""
    parser.add_argument("--branch", default=None, metavar="NAME", help="Branch name for the PIT run (default: autosprint/<timestamp>).")
    parser.add_argument("--max-sprints", type=int, default=None, metavar="N", help="Override MAX_SPRINTS (default 100).")
    parser.add_argument("--team", type=str, default=None, metavar="NAME", help="Override TEAM — the planning team (key in agents.TEAMS, e.g. council, power, duo). Implementor is set separately via --implement-agent.")
    parser.add_argument("--implement-agent", type=str, default=None, metavar="KEY", help="Override IMPLEMENT_AGENT — the Implement-phase agent (key in agents.AGENTS, e.g. implementor_opus48).")
    parser.add_argument("--preset", type=str, default=None, metavar="NAME", choices=sorted(_CLI_PRESETS), help=f"Bundled preset that sets several flags at once. Choices: {', '.join(sorted(_CLI_PRESETS))}. Explicit flags override preset values.")
    parser.add_argument("--claude-only", action="store_true", help="Shortcut: use the all-Claude council_opus team + Opus implementor. Equivalent to `--preset claude-only`.")
    parser.add_argument("--copilot-only", action="store_true", help="Shortcut: use the all-Copilot council_gpt55 team + GPT-5.5 implementor. Equivalent to `--preset copilot-only`.")
    parser.add_argument("--fake-plan", type=str, default=None, metavar="TITLE", help="Skip the Plan LLM call; inject this hardcoded task title. Debug only.")
    parser.add_argument("--fake-desc", type=str, default="", metavar="DESC", help="Description for --fake-plan (ignored unless --fake-plan is set).")
    parser.add_argument("--fake-implement", action="store_true", help="Skip the Implement LLM call; simulate success/failure via FAKE_IMPLEMENT_FAILURE_RATE.")
    parser.add_argument("--skip-first-plan", action="store_true", help="Reuse existing plan.md on sprint 1 instead of forcing a replan. Debug escape hatch.")
    parser.add_argument("--sp-target", type=int, default=None, metavar="N", help="Override SPRINT_STORY_POINT_TARGET (task-grouping aim, default 8). 0 disables grouping.")
    parser.add_argument("--sp-min", type=int, default=None, metavar="N", help="Override SPRINT_STORY_POINT_MIN (soft lower bound, default 2).")
    parser.add_argument("--sp-max", type=int, default=None, metavar="N", help="Override SPRINT_STORY_POINT_MAX (hard upper bound; team lead splits anything above, default 20).")
    parser.add_argument("--auto-replan", action="store_true", help="Autonomous self-planning loop — regenerates plan.md as the run proceeds. Default is reviewed-plan mode (no replan).")
    parser.add_argument("--manual-review", action="store_true", help="After Plan, prompt to approve the next task before Implement runs.")
    parser.add_argument("--commit-on-start", action="store_true", help="Commit pre-existing uncommitted changes in TARGET_REPO at startup without the Y/N prompt.")
    parser.add_argument("--use-cache", action="store_true", help="Read cached agent responses from .cache/ (writes always happen). Dev iteration only.")
    parser.add_argument("--no-branch", action="store_true", help="Run on the current branch instead of cutting a fresh autosprint/<ts> branch.")
    parser.add_argument("--initial-tests", choices=["quick", "all", "none"], default=None, help="Override INITIAL_TESTS — startup test scope. Autosprint terminates on failure.")
    parser.add_argument("--test-phase-quick-only", action="store_true", help='Run only the quick subset (-m "not slow") in the Test phase every sprint.')
    parser.add_argument("--prioritize", type=str, default=None, metavar="TEXT", help="Freeform priority hint for this planning run. Surfaces in both team-member and team-lead prompts.")


_HELP_EPILOG = """\
Examples:
  autosprint init                       bootstrap autosprint in the current repo
  autosprint doctor                     check the setup can run (live agent round-trip)
  autosprint plan --team council        draft a reviewed plan into autosprint/plan.md
  autosprint run                        run the reviewed plan.md top to bottom
  autosprint run --auto-replan          autonomous self-planning loop
  autosprint how-far                    measure distance to destination.md (read-only)
  autosprint show-config                print the resolved config and exit
  autosprint stop                       signal a running loop to finish and exit

See the README "CLI cookbook" section for the full set.
"""


def parse_cli_args() -> argparse.Namespace:
    """Define the argparse schema (root + subcommands), parse sys.argv, apply the resulting overrides to the global `config`, and fill args.branch with a default. Does no side-effecting prepare work — orchestration lives in prepare()."""
    try:
        parser = _TerseArgumentParser(
            prog="autosprint",
            description="Run the PIT loop — Plan, Implement, Test, Commit. Pick a subcommand below; with no subcommand, this help is shown.",
            epilog=_HELP_EPILOG,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.add_argument("--debug-traceback", action="store_true", help="Print full Python traceback on top-level errors.")
        parser.add_argument("--target", type=str, default=None, metavar="PATH", help="Path to the target repo (default: current directory).")
        add_run_options(parser)  # also supported at root so `autosprint --team foo` still works

        subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
        run_p = subparsers.add_parser("run", help="Execute the reviewed autosprint/plan.md top to bottom. Add --auto-replan for the autonomous self-planning loop.")
        add_run_options(run_p)
        stop_p = subparsers.add_parser("stop", help="Signal a running PIT loop to stop. Without --now, the loop finishes the current sprint and exits cleanly; with --now, it stops mid-sprint and reverts uncommitted work.")
        stop_p.add_argument("--now", action="store_true", help="Stop immediately and revert the in-progress sprint's uncommitted changes.")
        plan_p = subparsers.add_parser("plan", help="Draft autosprint/plan.md — run the Prepare + Plan phases only, then exit. Review the plan, then `autosprint run`.")
        add_run_options(plan_p)
        subparsers.add_parser("doctor", help="Verify the setup can run — target repo, destination.md, required CLIs, and one live agent round-trip per backend in use. Exits non-zero on any failure.")
        howfar_p = subparsers.add_parser("how-far", help="Measure how far the codebase is from autosprint/destination.md — a read-only status table (done / partial / not started / unclear). Dispatches one agent; makes no changes.")
        howfar_p.add_argument("--agent", type=str, default=None, metavar="KEY", help="Override HOWFAR_AGENT for this run (a key in agents.AGENTS). Use `howfar_gpt55` for a Copilot-only run, e.g. when Claude tokens are exhausted.")
        subparsers.add_parser("self-test", help="Run autosprint's own test suite (fast, no live calls) and exit.")
        show_config_p = subparsers.add_parser("show-config", help="Print the resolved config (team roster, implementor, env overrides) and exit.")
        add_run_options(show_config_p)
        init_p = subparsers.add_parser("init", help="Bootstrap autosprint working files in TARGET_REPO (autosprint/ folder with destination.md seed, adr.md stub, config.toml, gitignore entries) and exit. Interactive by default — a short wizard asks for the target language and AI backend; pass --yes to skip it.")
        init_p.add_argument("target_repo", nargs="?", default=None, metavar="TARGET_REPO", help="Path to the repo to bootstrap. Omit to bootstrap the current directory.")
        init_p.add_argument("--yes", "-y", action="store_true", help="Skip the interactive config wizard; write a default autosprint/config.toml.")
        init_p.add_argument("--update-skills", action="store_true", help="Refresh .claude/skills, .claude/agents, .github/skills from the autosprint source (overwrite). Skips the rest of init.")
        subparsers.add_parser("teams", help="List all available planning teams (key, description, roster, lead) so you can pick one for --team.")
        subparsers.add_parser("agents", help="List every agent in agents.AGENTS (key, name, backend, model) — useful for picking --implement-agent or HOWFAR_AGENT.")
        subparsers.add_parser("presets", help="List bundled --preset values (claude-only, copilot-only, solo-gpt55, quick-debug) and what each expands to.")
        subparsers.add_parser("gates", help="List per-sprint gates (import-check, smoke-test, format-check, lint-check, collect-only, coverage-track) with their config value + active/off/auto-skipped status (and why) for the configured target repo.")
        subparsers.add_parser("sprints", help="List recent sprint outcomes from autosprint/logs/sprint-outcomes.log — the last ~20 lines, dedup'd across the dual-write pattern.")
        subparsers.add_parser("logs", help="List files in autosprint/logs/ with size and last-modified timestamp, so you can see what's been logged without filesystem-browsing.")
        subparsers.add_parser("skills", help="List Claude Code skills installed in the target repo's `.claude/skills/` directory (the autosprint-shipped grill-* skills plus any local ones).")
        subparsers.add_parser("config-keys", help="List all config fields with default + description (from config.py Settings model). Differs from `show-config` / `settings` which show resolved current values.")
        settings_p = subparsers.add_parser("settings", help="Alias for `show-config` — print the resolved config (team roster, implementor, env overrides) and exit.")
        add_run_options(settings_p)
        subparsers.add_parser("help", help="Show this help message and exit — same as `autosprint` with no arguments, or `autosprint -h`.")
        subparsers.add_parser("clear-logs", help="Delete autosprint's generated logs in TARGET_REPO (sprint-outcomes.log, console-verbose.log, plan-decisions.md, fake-implement.log, preflight-tests.log, implement-failures.log, last-test-output.log, cache/) and exit. Leaves committed files (plan.md, adr.md, destination.md) alone.")

        args = parser.parse_args()
        if args.command is None or args.command == "help":
            parser.print_help()
            raise SystemExit(0)

        _resolve_target_repo(args)
        _apply_config_toml(args)
        apply_cli_overrides(args, parser)
        args.branch = getattr(args, "branch", None) or f"autosprint/{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        return args
    except SystemExit:
        raise
    except Exception as e:
        raise add_context(e, "Failed to parse CLI args") from e


def resolve_max_sprints(*, reviewed_plan: bool, explicitly_set: bool, pending_task_count: int, configured: int) -> int:
    """Pick the sprint ceiling. Normally the configured MAX_SPRINTS. In reviewed-plan mode
    (`autosprint run` without `--auto-replan`) the plan is a fixed, human-curated list the
    loop cannot refill — MAX_SPRINTS stops being a budget and is only a runaway backstop.
    So when the user hasn't set it explicitly, derive it from the plan: 2× the pending
    tasks, floored at 10. That keeps a deep reviewed plan from being cut short, and a
    short plan from being allowed to spin all the way to the high default. An explicitly
    set value always wins (including an explicit low one). Pure function — the disk read
    and config mutation live in the caller."""
    if not reviewed_plan or explicitly_set:
        return configured
    return max(pending_task_count * 2, 10)


def _resolve_reviewed_plan_max_sprints(max_sprints_explicit: bool) -> None:
    """Apply `resolve_max_sprints` to the global config when reviewed-plan auto-sizing is
    in effect, logging the derived ceiling so the user sees what a run will use. No-op
    when `--auto-replan` is set and when MAX_SPRINTS was set explicitly."""
    if config.AUTO_REPLAN:
        return
    pending = len(read_plan_md(config.TARGET_REPO_PATH).pending)
    resolved = resolve_max_sprints(reviewed_plan=True, explicitly_set=max_sprints_explicit, pending_task_count=pending, configured=config.MAX_SPRINTS)
    if resolved != config.MAX_SPRINTS:
        config.MAX_SPRINTS = resolved
        printlev(f"[prepare] Reviewed-plan mode: MAX_SPRINTS auto-sized to {resolved} (2× the {pending} reviewed task(s) in plan.md, floored at 10). Set MAX_SPRINTS in .env or pass --max-sprints to override.", level=50)


def _resolve_target_repo(args: argparse.Namespace) -> None:
    """Resolve which repo autosprint operates on and pin it onto `config.TARGET_REPO`.
    Order: an explicit `--target` flag (or, for `init`, the positional path) wins;
    otherwise the current working directory, when it is a git repo and not the
    autosprint repo itself; otherwise the `TARGET_REPO` env value is left in place as
    a debug/dev fallback (e.g. running autosprint from its own repo). When nothing
    resolves, `config.TARGET_REPO` stays empty and `TARGET_REPO_PATH` falls back to
    cwd; the invoked command then validates the target as needed."""
    explicit = getattr(args, "target", None) or getattr(args, "target_repo", None)
    if explicit:
        config.TARGET_REPO = explicit
        return
    cwd = Path.cwd()
    if (cwd / ".git").is_dir() and cwd.resolve() != _project_root().resolve():
        config.TARGET_REPO = str(cwd)


_CONFIG_TOML_KEYS: dict[str, str] = {
    "team": "TEAM",
    "implement_agent": "IMPLEMENT_AGENT",
    "implement_fallback_agent": "IMPLEMENT_FALLBACK_AGENT",
    "howfar_agent": "HOWFAR_AGENT",
    "sp_target": "SPRINT_STORY_POINT_TARGET",
    "sp_min": "SPRINT_STORY_POINT_MIN",
    "sp_max": "SPRINT_STORY_POINT_MAX",
    "replan_every_n_sprints": "REPLAN_EVERY_N_SPRINTS",
    "test_phase_quick_only": "TEST_PHASE_QUICK_ONLY",
    "target_test_runner": "TARGET_TEST_RUNNER",
    "test_command": "TEST_COMMAND",
}


def _config_toml_team(data: dict, args: argparse.Namespace) -> str | None:
    """Pick the planning team from a parsed config.toml for this invocation: the
    [auto_replan] section's `team` for `run --auto-replan`, the [plan] section's
    `team` for `plan`, otherwise a top-level `team`. Returns None when none is set."""
    if args.command == "run" and getattr(args, "auto_replan", False):
        section = data.get("auto_replan") or {}
    elif args.command == "plan":
        section = data.get("plan") or {}
    else:
        section = {}
    chosen = section.get("team") if isinstance(section, dict) else None
    return chosen or data.get("team")


def _apply_config_toml(args: argparse.Namespace) -> None:
    """Overlay {TARGET_REPO}/autosprint/config.toml onto `config`. Precedence:
    code defaults < config.toml < env vars < CLI flags — an env-set field (captured
    in ENV_SET_FIELDS at import) is never overwritten here, and CLI flags applied
    afterwards by `apply_cli_overrides` win over both. A missing or unreadable file
    is a silent no-op. The `team` value may be mode-specific (see `_config_toml_team`)."""
    path = config.TARGET_REPO_PATH / AUTOSPRINT_DIR_NAME / "config.toml"
    if not path.exists():
        return
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        printlev(f"[prepare] ⚠ Could not read {path} ({e}); ignoring it.", level=100)
        return
    resolved_team = _config_toml_team(data, args)
    values = {**data, "team": resolved_team} if resolved_team is not None else data
    for toml_key, field_name in _CONFIG_TOML_KEYS.items():
        if values.get(toml_key) is None:
            continue
        if field_name in ENV_SET_FIELDS:
            continue  # an explicit env / .env value outranks config.toml
        try:
            setattr(config, field_name, values[toml_key])
        except Exception as e:
            printlev(f"[prepare] ⚠ config.toml: ignoring invalid `{toml_key}` ({e}).", level=100)


def apply_cli_overrides(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Translate parsed CLI args into mutations on the global `config`. Skips run-mode options for subcommands that don't accept them (stop, self-test, init, clear-logs)."""
    try:
        if args.debug_traceback:
            config.DEBUG_TRACEBACK = True
        if args.command not in ("run", "plan", "show-config"):
            return
        # Whether MAX_SPRINTS was explicitly chosen — captured before the --max-sprints
        # assignment below so an .env value isn't masked by it. Drives reviewed-plan auto-sizing.
        max_sprints_explicit = (getattr(args, "max_sprints", None) is not None) or ("MAX_SPRINTS" in config.model_fields_set)
        # --claude-only / --copilot-only are sugar over the matching `--preset` value.
        # Mutually exclusive; an explicit --preset wins (the user is being specific).
        claude_only = getattr(args, "claude_only", False)
        copilot_only = getattr(args, "copilot_only", False)
        if claude_only and copilot_only:
            raise SystemExit("--claude-only and --copilot-only are mutually exclusive.")
        preset = getattr(args, "preset", None)
        if preset is None and claude_only:
            preset = "claude-only"
        elif preset is None and copilot_only:
            preset = "copilot-only"
        if preset:
            preset_vals = _CLI_PRESETS[preset]
            # Fill any CLI arg not explicitly set by the user with the preset value.
            # Explicit --team / --implement-agent / --initial-tests / --sp-* always win.
            for key, val in preset_vals.items():
                if getattr(args, key, None) is None:
                    setattr(args, key, val)
        if args.max_sprints:
            config.MAX_SPRINTS = args.max_sprints
        if args.team:
            config.TEAM = args.team
        if args.implement_agent:
            config.IMPLEMENT_AGENT = args.implement_agent
        if args.fake_plan:
            config.FAKE_PLAN_TITLE = args.fake_plan
            config.FAKE_PLAN_DESC = args.fake_desc
        elif args.fake_desc:
            printlev("[prepare] Warning: --fake-desc ignored because --fake-plan was not set.", level=100)
        if args.fake_implement:
            config.FAKE_IMPLEMENT = True
        if args.skip_first_plan:
            config.SKIP_FIRST_PLAN = True
        if args.sp_target is not None:
            config.SPRINT_STORY_POINT_TARGET = args.sp_target
        if args.sp_min is not None:
            config.SPRINT_STORY_POINT_MIN = args.sp_min
        if args.sp_max is not None:
            config.SPRINT_STORY_POINT_MAX = args.sp_max
        if args.auto_replan:
            config.AUTO_REPLAN = True
        if args.manual_review:
            config.MANUAL_REVIEW = True
        if args.commit_on_start:
            config.COMMIT_ON_START = True
        if args.use_cache:
            config.USE_CACHE = True
        if args.no_branch:
            config.CREATE_BRANCH = False
        if args.initial_tests:
            config.INITIAL_TESTS = args.initial_tests
        if args.test_phase_quick_only:
            config.TEST_PHASE_QUICK_ONLY = True
        if getattr(args, "prioritize", None) is not None:
            config.PRIORITIZE = args.prioritize
        _resolve_reviewed_plan_max_sprints(max_sprints_explicit)
    except Exception as e:
        raise add_context(e, f"Failed to apply CLI overrides for command '{getattr(args, 'command', '?')}'") from e
