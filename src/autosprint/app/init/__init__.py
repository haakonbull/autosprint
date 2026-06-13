"""autosprint init — bootstrap a TARGET_REPO so the PIT loop can run.

Owns the `autosprint init` subcommand and the prepare-step helpers that seed
files and check pre-conditions in TARGET_REPO. Split out of orchestrator.py
to keep loop logic separate from one-shot setup concerns. Functions here are
re-exported from orchestrator.py so existing
`from autosprint.app.orchestrator import _foo` paths still resolve."""

from __future__ import annotations

# Re-export the full public surface so existing import paths keep working.
from autosprint.app.init.assets import _PYTHON_SPECIFIC_SKILLS, _copy_claude_assets_to_target, _copy_claude_subdir, _migrate_legacy_autosprint_files, _target_is_python_repo  # noqa: F401
from autosprint.app.init.checks import _CLAUDE_MD_BLOATED_BYTES, _CLAUDE_MD_BLOATED_LINES, _CLAUDE_MD_MIN_USEFUL_CHARS, _CLI_BINARY_FOR_ASSISTANT, _CLI_INSTALL_HINT_FOR_ASSISTANT, _CREDENTIAL_PATTERNS, _DOCKERIGNORE_HIGH_PRIORITY, _PYTHON_PROJECT_MARKERS, _README_MIN_USEFUL_CHARS, _SENSITIVE_SCAN_MAX_FILE_BYTES, _SENSITIVE_SCAN_MAX_FINDINGS, CLAUDE_MD_FILENAME, README_FILENAME, _bootstrap_target_env_and_warn, _check_claude_md_and_warn, _check_cli_deps_or_abort, _check_dockerignore_and_warn, _check_readme_and_warn, _check_target_python_setup_and_warn, _print_init_config_summary, _required_assistants_for_run, _scan_for_sensitive_content_and_warn, _verify_target_is_git_repo, _verify_target_is_initialised  # noqa: F401
from autosprint.app.init.doctor import _DOCTOR_PROBE_AGENT_KEY, _REQUIRED_RUNTIME_DEPS, _check_install_health, _doctor_probe, probe_backends, run_doctor  # noqa: F401
from autosprint.app.init.gitignore import _PYTHON_GITIGNORE_DEFAULTS, _TS_GITIGNORE_DEFAULTS, _ensure_gitignore_entries  # noqa: F401
from autosprint.app.init.seeds import DEFAULT_DESTINATION_SEED_FILENAME, EXAMPLES_SOURCE_DIR, _assert_target_repo_not_self, _ensure_adr_stub, _ensure_destination_or_abort, _ensure_examples_dir_seeded  # noqa: F401
from autosprint.app.init.wizard import _detect_assistants, _ensure_config_toml, _prompt_choice, _prompt_yn, _run_config_wizard, _wizard_assistants, _wizard_auto_gates, _wizard_language  # noqa: F401
from autosprint.config import config
from autosprint.config.toml_io import render_config_toml as _render_config_toml  # noqa: F401
from autosprint.util.errors import add_context
from autosprint.util.output import printlev


def _run_init_update_skills() -> None:
    """`autosprint init --update-skills`: refresh the target repo's `.claude/skills/`, `.claude/agents/`, and `.github/skills/` from the autosprint source, **overwriting** existing entries. Use this after `git pull`-ing a newer autosprint to pick up updated skills without nuking the rest of init. Skips everything else (config.toml, gitignore, destination seed, sensitive-content scan, etc.) — those are one-time bootstrap concerns, not refresh-relevant."""
    from autosprint.reporting.banners import section_banner

    try:
        printlev(f"\n{section_banner('INIT --update-skills', 'START')}\n", level=100)
        _assert_target_repo_not_self()
        _verify_target_is_git_repo()
        printlev(f"[init] ✅ TARGET_REPO is a git repository: {config.TARGET_REPO_PATH}", level=100)
        _copy_claude_assets_to_target(overwrite=True)
        printlev("\n[init] ✅ Skills and agents updated from autosprint source.", level=100)
        printlev(f"{section_banner('INIT --update-skills', 'END')}", level=100)
    except Exception as e:
        raise add_context(e, f"Failed to update skills in {config.TARGET_REPO_PATH}") from e


def _run_init(assume_defaults: bool = False) -> None:
    """Bootstrap autosprint's working files in the target repo (already resolved to cwd, `--target`, or the TARGET_REPO env fallback before this runs). Steps: (1) assert the target is not the autosprint repo itself, (2) verify it is a git repo, (3) migrate any legacy file names from earlier autosprint versions, (4) seed autosprint/destination.md with the role-explaining template, (5) create an empty autosprint/adr.md stub, (6) create autosprint/config.toml — interactively via a short wizard (target language, AI backend) unless `assume_defaults` is set or stdin is not a TTY, in which case a default template is written, (7) append required .gitignore entries, (8) print resolved config. The destination grilling lives as a separate skill in Claude Code."""
    from autosprint.reporting.banners import section_banner

    try:
        printlev(f"\n{section_banner('INIT', 'START')}\n", level=100)
        _assert_target_repo_not_self()
        _check_cli_deps_or_abort()
        _verify_target_is_git_repo()
        printlev(f"[init] ✅ TARGET_REPO is a git repository: {config.TARGET_REPO_PATH}", level=100)
        _migrate_legacy_autosprint_files()
        _ensure_examples_dir_seeded()
        try:
            _ensure_destination_or_abort()
            printlev("[init] autosprint/destination.md exists and has content.", level=100)
        except RuntimeError as e:
            # _ensure_destination_or_abort raises when the file was just seeded —
            # for `init` that's the expected "created a seed" path, not an error.
            printlev(f"[init] {e}", level=100)
        _ensure_adr_stub()
        _ensure_config_toml(interactive=not assume_defaults)
        _ensure_gitignore_entries()
        _copy_claude_assets_to_target()
        _check_target_python_setup_and_warn()
        _check_claude_md_and_warn()
        _check_readme_and_warn()
        _bootstrap_target_env_and_warn()
        _check_dockerignore_and_warn()
        _scan_for_sensitive_content_and_warn()
        probe_backends(warn_only=True)
        _print_init_config_summary()
        printlev("\n[init] ✅ TARGET_REPO is ready.", level=100)
        printlev(f"[init] 👉 Next: open Claude Code in {config.TARGET_REPO_PATH} and run `/grill-destination` to flesh out autosprint/destination.md (the spec autosprint descends toward). When the file feels complete, run `autosprint` normally.", level=100)
        printlev(f"{section_banner('INIT', 'END')}", level=100)
    except Exception as e:
        raise add_context(e, f"Failed to initialise TARGET_REPO at {config.TARGET_REPO_PATH}") from e
