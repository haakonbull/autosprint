"""CLI surface: argparse, prepare-steps, one-shot subcommands, stop control.

Owns every code path that runs **before** the PIT loop starts (or that
implements a one-shot subcommand instead of running it):
- `parse_cli_args` / `add_run_options` / `apply_cli_overrides` — argparse setup
  and the translation from `args` to `config` mutations.
- `prepare()` — top-level entry point that dispatches to subcommands or
  delegates to `run_prepare_steps()` for `run` / `plan`.
- `run_prepare_steps` — the prepare sequence (banner, migration, seeds,
  gitignore, commit prompt, branch creation, initial tests).
- One-shot subcommand handlers: `run_show_teams`, `run_clear_logs`, `run_stop`.
- Stop control: `check_stop_request`, `raise_if_stop_between_phases`,
  `prompt_commit_or_abort`.
- The bundled `_CLI_PRESETS` dict.
"""

from __future__ import annotations

import argparse
import difflib
import re
import shutil
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autosprint.banners import print_effective_config, print_start_banner, section_banner
from autosprint.config import ENV_SET_FIELDS, _project_root, config
from autosprint.errors import StopRequested, add_context
from autosprint.git_ops import git
from autosprint.how_far import run_how_far
from autosprint.init import (
    _assert_target_repo_not_self,
    _ensure_adr_stub,
    _ensure_destination_or_abort,
    _ensure_gitignore_entries,
    _migrate_legacy_autosprint_files,
    _run_init,
    _verify_target_is_initialised,
    probe_backends,
    run_doctor,
)
from autosprint.output import printlev
from autosprint.paths import AUTOSPRINT_DIR_NAME, STOP_CONTROL_FILENAME, STOP_NOW_CONTROL_FILENAME
from autosprint.plan import read_plan_md
from autosprint.run_log import trim_console_verbose_log, trim_plan_decisions_log
from autosprint.teams import TEAMS
from autosprint.test_phase import check_initial_tests, run_self_test

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


# ---------------------------------------------------------------------------
# Pre-sprint commit prompt (uncommitted changes)
# ---------------------------------------------------------------------------


def prompt_commit_or_abort() -> None:
    """Commit uncommitted changes in TARGET_REPO before starting; prompt Y/N unless COMMIT_ON_START is set."""
    try:
        status = git("status", "--porcelain").stdout.strip()
        if not status:
            return
        printlev(f"\n[prepare] Uncommitted changes detected in {config.TARGET_REPO_PATH}:\n{status}", level=100)
        if config.COMMIT_ON_START:
            printlev("[prepare] COMMIT_ON_START=True — committing without prompting.", level=100)
            approved = True
        else:
            answer = input("\nCommit all changes before starting PIT? [Y/n]: ").strip().lower()
            approved = answer in ("", "y", "yes")
        if approved:
            git("add", "-A")
            git("commit", "-m", "[autosprint] Pre-sprint snapshot (committed before autosprint loop)")
            printlev("[prepare] ✅ Changes committed. Proceeding.\n", level=100)
        else:
            raise RuntimeError("Aborted by user: uncommitted changes in TARGET_REPO. Commit, stash, or discard them, then re-run.")
    except RuntimeError:
        raise
    except Exception as e:
        raise add_context(e, "Failed to handle uncommitted changes in TARGET_REPO") from e


# ---------------------------------------------------------------------------
# argparse plumbing
# ---------------------------------------------------------------------------


_INVALID_SUBCOMMAND_RE = re.compile(r"argument COMMAND: invalid choice: '([^']+)' \(choose from (.+)\)")


class _TerseArgumentParser(argparse.ArgumentParser):
    """argparse subclass that replaces the default `error()` output for the most common typo case — an unknown subcommand — with a compact "did you mean?" hint plus a short list of valid commands. Default argparse dumps the full multi-line usage banner for any error, which buries the actual mistake. Other errors still fall through to the default formatting so flag-level mistakes keep their full context.

    Two-pass suggestion logic: first scans the *other* CLI tokens for a known subcommand (so `autosprint list teams` resolves to "Did you mean: teams?" by finding the valid `teams` token after the unknown `list`); then falls back to difflib close-matching on the typed word (catches single-word typos like `clear-logss` → `clear-logs`).
    """

    def error(self, message: str) -> None:  # type: ignore[override]
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


# ---------------------------------------------------------------------------
# prepare()  —  prepare phase entry point
# ---------------------------------------------------------------------------


def run_prepare_steps(args: argparse.Namespace) -> None:
    """Run the target-repo preparation sequence for a real PIT run: banner, migration, seeds, gitignore, commit prompt, branch creation, initial tests. Plan-only mode skips the banner/commit/branch/initial-tests steps since no sprint will execute."""
    try:
        _verify_target_is_initialised()
        is_plan_only = args.command == "plan"
        if not is_plan_only:
            print_start_banner(args.branch)
        printlev(f"\n{section_banner('PREPARE PHASE', 'START')}\n", level=100)
        if config.SELF_TEST_BEFORE_START:
            run_self_test()
        _migrate_legacy_autosprint_files()
        trim_plan_decisions_log()
        trim_console_verbose_log()
        _ensure_destination_or_abort()
        _ensure_adr_stub()
        _ensure_gitignore_entries()
        if not is_plan_only:
            # Live backend probe before any state mutation (commit prompt, branch
            # creation): a backend that broke since init — lost Copilot wheel,
            # expired login, hit usage cap — should stop the run here, not hours in.
            probe_backends()
            prompt_commit_or_abort()
            if config.CREATE_BRANCH:
                git("checkout", "-b", args.branch)
                printlev(f"[prepare] Created branch: {args.branch}")
            else:
                printlev("[prepare] Skipping branch creation (CREATE_BRANCH=False).", level=20)
            check_initial_tests()
        printlev(f"\n{section_banner('PREPARE PHASE', 'END')}", level=100)
    except Exception as e:
        raise add_context(e, "Failed to run prepare steps") from e


def prepare() -> argparse.Namespace:
    """Top-level prepare: parse CLI args, dispatch one-shot subcommands (self-test, teams, init, doctor, show-config, clear-logs, stop), or run the full prepare-steps sequence for `run` / `plan`. Returns the parsed args. main() decides what to do next based on args.command."""
    try:
        args = parse_cli_args()

        if args.command == "self-test":
            run_self_test()
            return args
        if args.command == "teams":
            run_show_teams()
            return args
        if args.command == "agents":
            run_show_agents()
            return args
        if args.command == "presets":
            run_show_presets()
            return args
        if args.command == "gates":
            run_show_gates()
            return args
        if args.command == "sprints":
            run_show_sprints()
            return args
        if args.command == "logs":
            run_show_logs()
            return args
        if args.command == "skills":
            run_show_skills()
            return args
        if args.command == "config-keys":
            run_show_config_keys()
            return args
        if args.command == "settings":
            print_effective_config(args.branch)
            return args

        if args.command == "init":
            if getattr(args, "update_skills", False):
                from autosprint.init import _run_init_update_skills

                _run_init_update_skills()
            else:
                _run_init(assume_defaults=getattr(args, "yes", False))
            return args
        if args.command == "doctor":
            run_doctor()
            return args

        _assert_target_repo_not_self()

        if args.command == "how-far":
            run_how_far(getattr(args, "agent", None))
            return args
        if args.command == "show-config":
            print_effective_config(args.branch)
            return args
        if args.command == "clear-logs":
            run_clear_logs()
            return args
        if args.command == "stop":
            run_stop(immediate=args.now)
            return args

        run_prepare_steps(args)
        return args
    except Exception as e:
        raise add_context(e, "Failed to prepare") from e


# ---------------------------------------------------------------------------
# One-shot subcommand handlers
# ---------------------------------------------------------------------------


def run_show_teams() -> None:
    """Print every team in `agents.TEAMS` with its description and roster so the user can pick one for `--team` without opening the source. Uses the same `description` key surfaced in the startup banner."""
    try:
        lines = ["", section_banner("TEAMS", "START")]
        default = config.TEAM
        for key in sorted(TEAMS.keys()):
            team = TEAMS[key]
            marker = "  (default)" if key == default else ""
            desc = team.get("description", "")
            agents_list = team.get("agents", [])
            selector = team.get("selector")
            lines.append("")
            lines.append(f"   {key}{marker}")
            if desc:
                lines.append(f"      {desc}")
            lines.extend(f"      - {agent.get('name', '?')} [{agent.get('assistant', '?')}/{agent.get('model', '?')}]" for agent in agents_list)
            if selector:
                lines.append(f"      lead: {selector.get('name', '?')} [{selector.get('assistant', '?')}/{selector.get('model', '?')}]")
            else:
                lines.append("      lead: (shared — single-agent team uses the same agent as selector)")
        lines.append("")
        lines.append("   Use:  autosprint run --team <key>   (or set TEAM=<key> in .env)")
        lines.append(section_banner("TEAMS", "END"))
        lines.append("")
        printlev("\n".join(lines), level=100)
    except Exception as e:
        raise add_context(e, "Failed to print teams list") from e


def run_show_agents() -> None:
    """Print every agent in `agents.AGENTS` with its backend and model so the user can pick one for `--implement-agent` (or for `HOWFAR_AGENT` in config). Default IMPLEMENT_AGENT and HOWFAR_AGENT are marked. Alphabetised by key."""
    from autosprint.agents import AGENTS

    try:
        default_implement = config.IMPLEMENT_AGENT
        default_howfar = config.HOWFAR_AGENT
        default_fallback = config.IMPLEMENT_FALLBACK_AGENT
        lines = ["", section_banner("AGENTS", "START")]
        for key in sorted(AGENTS.keys()):
            agent = AGENTS[key]
            markers = []
            if key == default_implement:
                markers.append("default IMPLEMENT_AGENT")
            if key == default_howfar:
                markers.append("default HOWFAR_AGENT")
            if key == default_fallback:
                markers.append("default IMPLEMENT_FALLBACK_AGENT")
            marker = f"  ({', '.join(markers)})" if markers else ""
            lines.append("")
            lines.append(f"   {key}{marker}")
            lines.append(f"      name:    {agent.get('name', '?')}")
            lines.append(f"      backend: {agent.get('assistant', '?')}")
            lines.append(f"      model:   {agent.get('model', '?')}")
        lines.append("")
        lines.append("   Use:  autosprint run --implement-agent <key>   (or set IMPLEMENT_AGENT=<key> in .env)")
        lines.append(section_banner("AGENTS", "END"))
        lines.append("")
        printlev("\n".join(lines), level=100)
    except Exception as e:
        raise add_context(e, "Failed to print agents list") from e


def run_show_presets() -> None:
    """Print every bundled --preset and what it expands to. Lets the user see at a glance what `--preset claude-only` (etc.) actually sets without reading README or source."""
    try:
        lines = ["", section_banner("PRESETS", "START")]
        for name in sorted(_CLI_PRESETS.keys()):
            expansion = _CLI_PRESETS[name]
            lines.append("")
            lines.append(f"   --preset {name}")
            for key, value in expansion.items():
                lines.append(f"      --{key.replace('_', '-')} {value}")
        lines.append("")
        lines.append("   Use:  autosprint run --preset <name>   (explicit flags override preset values)")
        lines.append(section_banner("PRESETS", "END"))
        lines.append("")
        printlev("\n".join(lines), level=100)
    except Exception as e:
        raise add_context(e, "Failed to print presets list") from e


def describe_gates() -> list[dict[str, str]]:
    """Inspect the live config and return one row per per-sprint gate: `{name, config_value, status, detail}`. `status` is one of `active` (will fire), `off` (user-disabled), or `auto-skipped` (enabled but missing tooling/config so the gate is a no-op). `detail` carries the why for the skip. Used by both `run_show_gates` (the dedicated subcommand) and the startup banner so the two stay consistent."""
    rows: list[dict[str, str]] = []
    # IMPORT_CHECK — Python-only, auto-skips for non-Python (no pyproject [project].name).
    if config.IMPORT_CHECK:
        try:
            from autosprint.test_runners import PytestRunner

            pkg = PytestRunner()._detect_package_name() if config.TARGET_REPO_PATH.exists() else None
            if pkg:
                rows.append({"name": "import-check", "config_value": "true", "status": "active", "detail": f'`python -c "import {pkg.replace("-", "_")}"`'})
            else:
                rows.append({"name": "import-check", "config_value": "true", "status": "auto-skipped", "detail": "no pyproject.toml [project].name in target"})
        except Exception:
            rows.append({"name": "import-check", "config_value": "true", "status": "active", "detail": "(target inspection failed; will retry per-sprint)"})
    else:
        rows.append({"name": "import-check", "config_value": "false", "status": "off", "detail": "IMPORT_CHECK=false"})
    # SMOKE_TEST
    if config.SMOKE_TEST == "off":
        rows.append({"name": "smoke-test", "config_value": "off", "status": "off", "detail": "SMOKE_TEST=off"})
    elif config.SMOKE_TEST != "auto":
        rows.append({"name": "smoke-test", "config_value": config.SMOKE_TEST, "status": "active", "detail": f"literal command: {config.SMOKE_TEST}"})
    else:
        try:
            from autosprint.test_runners import PytestRunner

            r = PytestRunner()
            pkg = r._detect_package_name() if config.TARGET_REPO_PATH.exists() else None
            if pkg and r._find_main_module(pkg):
                rows.append({"name": "smoke-test", "config_value": "auto", "status": "active", "detail": f"`python -m {pkg} --help` → spawn-survive fallback"})
            else:
                rows.append({"name": "smoke-test", "config_value": "auto", "status": "auto-skipped", "detail": "no `__main__.py` in target package"})
        except Exception:
            rows.append({"name": "smoke-test", "config_value": "auto", "status": "active", "detail": "(target inspection failed; will retry per-sprint)"})
    # FORMAT_CHECK
    if config.FORMAT_CHECK == "off":
        rows.append({"name": "format-check", "config_value": "off", "status": "off", "detail": "FORMAT_CHECK=off (opt-in)"})
    elif config.FORMAT_CHECK == "auto":
        if shutil.which("black") is not None:
            rows.append({"name": "format-check", "config_value": "auto", "status": "active", "detail": "`black --check src tests`"})
        else:
            rows.append({"name": "format-check", "config_value": "auto", "status": "auto-skipped", "detail": "black not on PATH"})
    else:
        rows.append({"name": "format-check", "config_value": config.FORMAT_CHECK, "status": "active", "detail": f"literal: {config.FORMAT_CHECK}"})
    # LINT_CHECK
    if config.LINT_CHECK == "off":
        rows.append({"name": "lint-check", "config_value": "off", "status": "off", "detail": "LINT_CHECK=off (opt-in)"})
    elif config.LINT_CHECK == "auto":
        try:
            from autosprint.test_runners import PytestRunner

            cmd = PytestRunner()._detect_lint_command() if config.TARGET_REPO_PATH.exists() else None
            if cmd:
                rows.append({"name": "lint-check", "config_value": "auto", "status": "active", "detail": " ".join(cmd[-2:])})
            else:
                rows.append({"name": "lint-check", "config_value": "auto", "status": "auto-skipped", "detail": "no ruff/flake8/mypy config detected, or linter not on PATH"})
        except Exception:
            rows.append({"name": "lint-check", "config_value": "auto", "status": "active", "detail": "(target inspection failed; will retry per-sprint)"})
    else:
        rows.append({"name": "lint-check", "config_value": config.LINT_CHECK, "status": "active", "detail": f"literal: {config.LINT_CHECK}"})
    # PYTEST_COLLECT_GATE
    rows.append({"name": "collect-only", "config_value": str(config.PYTEST_COLLECT_GATE).lower(), "status": "active" if config.PYTEST_COLLECT_GATE else "off", "detail": "`pytest --collect-only -q`" if config.PYTEST_COLLECT_GATE else "PYTEST_COLLECT_GATE=false (opt-in)"})
    # COVERAGE_TRACK
    rows.append({"name": "coverage-track", "config_value": str(config.COVERAGE_TRACK).lower(), "status": "active (warn-only)" if config.COVERAGE_TRACK else "off", "detail": "`pytest --cov=<pkg>` → autosprint/logs/coverage-history.log" if config.COVERAGE_TRACK else "COVERAGE_TRACK=false (opt-in)"})
    # TS_TYPECHECK (vitest-only — skip mention when runner is pytest)
    return rows


def run_show_gates() -> None:
    """Print every per-sprint gate with its config value + whether it's active, off, or auto-skipped (and why). Lets the user see at a glance what protection is actually in effect for the configured target — answers the recurring question 'is FORMAT_CHECK=auto actually doing anything?'."""
    try:
        rows = describe_gates()
        lines = ["", section_banner("GATES", "START")]
        lines.append("")
        lines.append(f"   {'Gate':<20} {'Config':<10} {'Status':<22} Detail")
        lines.append(f"   {'-' * 20} {'-' * 10} {'-' * 22} {'-' * 40}")
        lines.extend(f"   {row['name']:<20} {row['config_value']:<10} {row['status']:<22} {row['detail']}" for row in rows)
        lines.append("")
        lines.append("   Use:  edit `autosprint/config.toml` to flip a gate. `auto` mode safely skips when tooling is missing.")
        lines.append(section_banner("GATES", "END"))
        lines.append("")
        printlev("\n".join(lines), level=100)
    except Exception as e:
        raise add_context(e, "Failed to list gates") from e


def run_show_sprints() -> None:
    """Print the last ~20 sprint outcomes from `autosprint/logs/sprint-outcomes.log`. Uses `recent_sprint_history()` so the dual-write pattern (REVERTED + SPRINT_REVERTED for the same sprint) gets dedup'd just like the team-lead prompt sees it. Empty log → friendly note rather than blank output."""
    from autosprint.run_log import recent_sprint_history

    try:
        history = recent_sprint_history(n=20)
        lines = ["", section_banner("RECENT SPRINTS", "START")]
        if not history.strip():
            lines.append("")
            lines.append("   No sprint history yet — autosprint/logs/sprint-outcomes.log is empty or missing.")
            lines.append("   Run `autosprint run` to start producing entries.")
        else:
            lines.append("")
            lines.extend(f"   {line}" for line in history.splitlines())
        lines.append("")
        lines.append(section_banner("RECENT SPRINTS", "END"))
        lines.append("")
        printlev("\n".join(lines), level=100)
    except Exception as e:
        raise add_context(e, "Failed to print sprint history") from e


def run_show_logs() -> None:
    """List every file in `autosprint/logs/` with its size and last-modified timestamp, so you can see what's been logged without filesystem-browsing. Subfolders (e.g. nested log dirs) are listed by name. Empty/missing dir → friendly note."""
    from autosprint.paths import LOGS_SUBDIR

    try:
        logs_dir = config.TARGET_REPO_PATH / LOGS_SUBDIR
        lines = ["", section_banner("LOGS", "START")]
        if not logs_dir.exists() or not logs_dir.is_dir():
            lines.append("")
            lines.append(f"   No {LOGS_SUBDIR}/ directory yet — nothing has been logged. Run `autosprint run` once to create it.")
        else:
            entries = sorted(logs_dir.iterdir(), key=lambda p: p.name)
            if not entries:
                lines.append("")
                lines.append(f"   {LOGS_SUBDIR}/ is empty.")
            else:
                lines.append("")
                lines.append(f"   {'Name':<35} {'Size':>10}  Last modified")
                lines.append(f"   {'-' * 35} {'-' * 10}  {'-' * 19}")
                for entry in entries:
                    try:
                        stat = entry.stat()
                        size = _human_size(stat.st_size) if entry.is_file() else "(dir)"
                        mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                        lines.append(f"   {entry.name:<35} {size:>10}  {mtime}")
                    except OSError:
                        lines.append(f"   {entry.name:<35} {'?':>10}  (stat failed)")
        lines.append("")
        lines.append("   Use:  autosprint clear-logs   to wipe the gitignored logs (keeps the three tracked history files in place via git).")
        lines.append(section_banner("LOGS", "END"))
        lines.append("")
        printlev("\n".join(lines), level=100)
    except Exception as e:
        raise add_context(e, "Failed to list logs") from e


def _human_size(n: int) -> str:
    """Render a byte count as a short human-readable string (B / KB / MB). Used by `run_show_logs` so a 200 KB log doesn't print as 204800."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} TB"


def run_show_skills() -> None:
    """List Claude Code skills installed in the target repo's `.claude/skills/` directory. Each skill is a subdirectory containing a `SKILL.md`; this command shows the name and the first non-frontmatter sentence of the description (frontmatter `description:` field when present). Missing dir → note + pointer to `autosprint init --update-skills`."""
    try:
        skills_dir = config.TARGET_REPO_PATH / ".claude" / "skills"
        lines = ["", section_banner("SKILLS", "START")]
        if not skills_dir.exists() or not skills_dir.is_dir():
            lines.append("")
            lines.append("   No .claude/skills/ directory in the target repo.")
            lines.append("   Run `autosprint init` (fresh setup) or `autosprint init --update-skills` (existing repo) to install the autosprint-shipped skills.")
        else:
            entries = sorted([p for p in skills_dir.iterdir() if p.is_dir()], key=lambda p: p.name)
            if not entries:
                lines.append("")
                lines.append("   .claude/skills/ exists but is empty.")
            else:
                for entry in entries:
                    skill_md = entry / "SKILL.md"
                    description = ""
                    if skill_md.exists():
                        try:
                            text = skill_md.read_text(encoding="utf-8")
                            # Pull `description:` line from YAML frontmatter when present.
                            m = re.search(r"^description:\s*(.+?)$", text, re.MULTILINE)
                            if m:
                                description = m.group(1).strip().strip("'\"")
                                # Trim to a manageable preview length.
                                if len(description) > 160:
                                    description = description[:157] + "..."
                        except OSError:
                            pass
                    lines.append("")
                    lines.append(f"   {entry.name}")
                    if description:
                        lines.append(f"      {description}")
        lines.append("")
        lines.append("   Use:  /<skill-name>   in Claude Code to invoke a skill.")
        lines.append(section_banner("SKILLS", "END"))
        lines.append("")
        printlev("\n".join(lines), level=100)
    except Exception as e:
        raise add_context(e, "Failed to list skills") from e


def run_show_config_keys() -> None:
    """Print every config field (from the `Settings` pydantic model) with its default value and a one-line description. Different from `show-config`/`settings` which show resolved current values — this command is the menu of knobs you can tweak. Useful for a new user asking "what can I configure?"."""
    try:
        lines = ["", section_banner("CONFIG KEYS", "START")]
        for name, field in sorted(config.model_fields.items()):
            default = field.default
            default_repr = repr(default) if default is not None else "None"
            desc = (field.description or "").strip()
            # Trim long descriptions to keep the table scannable.
            if len(desc) > 240:
                desc = desc[:237] + "..."
            lines.append("")
            lines.append(f"   {name}")
            lines.append(f"      default: {default_repr}")
            if desc:
                lines.append(f"      {desc}")
        lines.append("")
        lines.append("   Use:  autosprint settings   to see the resolved values for the current target repo.")
        lines.append(section_banner("CONFIG KEYS", "END"))
        lines.append("")
        printlev("\n".join(lines), level=100)
    except Exception as e:
        raise add_context(e, "Failed to print config keys") from e


def run_clear_logs() -> None:
    """Delete autosprint's generated log files in TARGET_REPO so the next run starts with a clean slate. Removes sprint-outcomes.log, console-verbose.log, plan-decisions.md, fake-implement.log, preflight-tests.log, implement-failures.log, and the cache/ folder. Also cleans up pre-rename legacy names (ai-run.log, console.log, plan-decision-log.md) if they're still lying around. Leaves the committed files (plan.md, adr.md, destination.md) alone."""
    try:
        autosprint_dir = config.TARGET_REPO_PATH / AUTOSPRINT_DIR_NAME
        logs_dir = autosprint_dir / "logs"
        deleted: list[str] = []
        skipped: list[str] = []
        for name in ("sprint-outcomes.log", "console-verbose.log", "console-all.log", "plan-decisions.md", "fake-implement.log", "preflight-tests.log", "implement-failures.log", "last-test-output.log", "last-run-summary.md", "runtime-stats.md", "ai-run.log", "console.log", "plan-decision-log.md"):
            path = logs_dir / name
            if path.exists():
                path.unlink()
                deleted.append(name)
            else:
                skipped.append(name)
        cache_dir = autosprint_dir / "cache"
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
            deleted.append("cache/")
        else:
            skipped.append("cache/")
        printlev(f"\n{section_banner('CLEAR LOGS', 'START')}\n", level=100)
        if deleted:
            printlev(f"[clear] ✅ Deleted: {', '.join(deleted)}", level=100)
        if skipped:
            printlev(f"[clear] (already gone: {', '.join(skipped)})", level=100)
        printlev(f"{section_banner('CLEAR LOGS', 'END')}", level=100)
    except Exception as e:
        raise add_context(e, f"Failed to clear logs in {config.TARGET_REPO_PATH}") from e


def run_stop(immediate: bool) -> None:
    """Drop a small control file under TARGET_REPO/autosprint/ so a running PIT loop pointed at the same repo notices and exits. 'stop' means finish the current sprint and exit cleanly; 'stop-now' means stop mid-sprint and revert uncommitted changes. The live loop deletes the file on consumption, so a stale file can never hijack the next run."""
    try:
        autosprint_dir = config.TARGET_REPO_PATH / AUTOSPRINT_DIR_NAME
        autosprint_dir.mkdir(parents=True, exist_ok=True)
        filename = STOP_NOW_CONTROL_FILENAME if immediate else STOP_CONTROL_FILENAME
        control_file = config.TARGET_REPO_PATH / filename
        control_file.write_text(datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ\n"), encoding="utf-8")
        mode = "stop-now (immediate + revert)" if immediate else "stop (soft — finish current sprint, then exit)"
        printlev(f"\n[stop] Wrote {filename} in {config.TARGET_REPO_PATH}.", level=100)
        printlev(f"[stop] Mode: {mode}", level=100)
        printlev("[stop] The live run deletes the control file once it responds, so no cleanup is needed.", level=100)
    except Exception as e:
        raise add_context(e, f"Failed to write stop control file (immediate={immediate})") from e


# ---------------------------------------------------------------------------
# Stop-control file polling (used by pit_loop)
# ---------------------------------------------------------------------------


def check_stop_request(immediate_only: bool = False) -> str | None:
    """Return 'immediate', 'soft', or None depending on which stop control file (if any) is present in TARGET_REPO. Deletes the file on detection. Between-phase callers pass immediate_only=True so soft stops don't interrupt an Implement/Test that's already in motion — soft stops only fire at sprint boundaries."""
    try:
        target = config.TARGET_REPO_PATH
        immediate_path = target / STOP_NOW_CONTROL_FILENAME
        if immediate_path.exists():
            immediate_path.unlink()
            return "immediate"
        if immediate_only:
            return None
        soft_path = target / STOP_CONTROL_FILENAME
        if soft_path.exists():
            soft_path.unlink()
            return "soft"
        return None
    except Exception as e:
        raise add_context(e, "Failed to check stop request") from e


def raise_if_stop_between_phases() -> None:
    """Between-phase stop check — only fires for 'stop-now'. Raises StopRequested('immediate') so the current sprint aborts cleanly. Soft stops are deliberately ignored here; the live loop catches them at the sprint boundary."""
    kind = check_stop_request(immediate_only=True)
    if kind == "immediate":
        raise StopRequested(kind)
