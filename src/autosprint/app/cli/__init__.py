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
- The bundled `_CLI_PRESETS` dict."""

from __future__ import annotations

import argparse

# Re-export the full public surface so existing import paths keep working.
from autosprint.app.cli.args import _CLI_PRESETS, _CONFIG_TOML_KEYS, _HELP_EPILOG, _INVALID_SUBCOMMAND_RE, _ONESHOT_COMMANDS, _apply_config_toml, _config_toml_team, _resolve_reviewed_plan_max_sprints, _resolve_target_repo, _TerseArgumentParser, add_run_options, apply_cli_overrides, parse_cli_args, resolve_max_sprints  # noqa: F401
from autosprint.app.cli.commands import (
    _human_size,  # noqa: F401
    run_clear_logs,
    run_show_agents,
    run_show_config_keys,
    run_show_gates,
    run_show_logs,
    run_show_presets,
    run_show_skills,
    run_show_sprints,
    run_show_teams,
)
from autosprint.app.how_far import run_how_far
from autosprint.app.init import (
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
from autosprint.config import config
from autosprint.infra.git_ops import git
from autosprint.infra.stop import run_stop
from autosprint.phases.test_phase import check_initial_tests, run_self_test
from autosprint.reporting.banners import print_effective_config, print_start_banner, section_banner
from autosprint.reporting.run_log import trim_console_verbose_log, trim_plan_decisions_log
from autosprint.util.errors import add_context
from autosprint.util.output import printlev


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
                from autosprint.app.init import _run_init_update_skills

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
