# Changelog

Notable changes to autosprint. Format loosely follows [Keep a Changelog](https://keepachangelog.com/);
versions are unreleased until autosprint cuts a numbered tag. Until then, this file is a running log
of meaningful shifts grouped by date.

## 2026-05-25 — Smoke test gate + longer retry tolerance + five new per-sprint gates

### Added

- **Per-sprint smoke test** (`SMOKE_TEST` config, default `auto`). After pytest/vitest passes, the Test phase runs `python -m <package>` (auto-detected from `pyproject.toml`) with headless env vars (`SDL_VIDEODRIVER=dummy`, `PYGLET_HEADLESS=1`, etc.) and either: (a) accepts `--help` exit 0, or (b) falls back to a 3-second spawn-and-survive check. A failed smoke test reverts the sprint via the same gate that handles failed tests. Closes a real bug class — pytest mocks the main loop in most game/GUI projects, so an `ImportError` in `__main__.py` or a missing dep that's mocked away in tests could ship green commits while the actual app was broken. Skipped silently for library projects (no `__main__.py`) and overrideable with a literal command string for non-standard launchers.
- `TestRunner.post_test_gate()` abstract hook (paired with the existing `pre_test_gate`). Subclass-overridable; default no-op. PytestRunner implements it as the smoke test above. VitestRunner stays no-op for now (TS smoke testing belongs in a later phase).
- **Import check** (`IMPORT_CHECK` config, default `true`). Runs `python -c "import <pkg>"` before the `-m <pkg>` smoke. Catches package-level `ImportError`, top-level exceptions in `__init__.py`, missing deps that mocking hid — without needing a `__main__.py`, so library projects benefit too. Distribution-name to module-name normalisation (`my-game` → `my_game`) handled. ~50ms typical cost.
- **Format check gate** (`FORMAT_CHECK` config, default `off`). `auto` runs `black --check src tests` for Python (skips silently if black isn't installed); a literal value is a shell-split command. Failure reverts the sprint. Opt-in to avoid surprising projects without a formatter.
- **Lint check gate** (`LINT_CHECK` config, default `off`). `auto` detects ruff (`[tool.ruff]` in pyproject) > flake8 (`.flake8` / `setup.cfg [flake8]`) > mypy (`mypy.ini` / `[tool.mypy]`) and runs whichever is configured AND on PATH. Failure reverts the sprint. Opt-in.
- **Pytest collect-only gate** (`PYTEST_COLLECT_GATE` config, default `false`). When on, runs `pytest --collect-only -q` before the main test command — faster failure on broken conftest.py / import errors in test files. Marginal over plain pytest's error handling; opt-in.
- **Coverage tracking** (`COVERAGE_TRACK` config, default `false`). When on, runs pytest with `--cov=<pkg>` after the main pass and appends the coverage % to `autosprint/logs/coverage-history.log`. Warn-only — prints a console warning on drop but doesn't revert. Future v2 will gate on regression once baseline-storage and noise-handling stabilise.
- **`autosprint gates`** subcommand — prints a table of every per-sprint gate with its current config, status (active / off / auto-skipped), and the concrete command/reason. Active gates also appear in the startup banner so a run begins with an explicit "here's what guards this commit" line. Implementor agent prompt updated to pre-empt the gates rather than discover them by revert.
- **Init wizard** asks a third question: enable the auto-detected gates (format-check, lint-check, coverage-track)? Default Y. On Y the wizard writes `format_check="auto"`, `lint_check="auto"`, `coverage_track=true` to `autosprint/config.toml` so new repos start with the safe-auto gate set. The always-on gates (import-check, smoke-test) are untouched by this question.
- **Pre-flight import + smoke** — `check_initial_tests` now runs the runner's `post_test_gate` after pytest passes, so a broken-master state (import error in `__init__.py`, `__main__.py` that won't launch) is caught BEFORE sprint 1. Previously the loop would revert into the broken baseline on every sprint and never make progress.
- **`how-far` heartbeat** (`HOWFAR_HEARTBEAT_EVERY_N_SPRINTS` config, default `10`). The PIT loop now fires `autosprint how-far` automatically every N sprints as a passive progress sensor. Full report → `autosprint/logs/howfar-heartbeat.log`; compact headline (`Distance to destination — 14 requirements: 6 ✅ done · 4 🟡 partial · 3 ⬜ not started · 1 ❓ unclear`) prints inline so a long auto-replan run gives the human a way to spot "47 sprints, still 0 done" before sprint 48 happens. Read-only sensor — never feeds back into planning (Goodhart-safe). Heartbeat failures swallow quietly: the loop continues. Set to `0` to disable. Cost: ~1 LLM dispatch per N sprints (~5% overhead at default N=10). Motivated by the 47-sprint run where pytest stayed green while real progress was zero — visibility was the missing piece.

### Changed

- **LLM retry tolerance** raised: `LLM_RETRY_ATTEMPTS` default `1` → `3`, `LLM_RETRY_BACKOFF_SECONDS` default `2.0` → `5.0`, multiplier doubled → tripled (5s/15s/45s schedule). Result: ~65 seconds of total tolerance instead of ~6 seconds — survives the typical 30-120s network blip seen on overnight `--auto-replan` runs. Motivated by a 47-sprint run that died on sprint 47 after losing Copilot connectivity for ~30 seconds.

## 2026-05-23 — Big refactor + cleanup

### Added

- `--claude-only` / `--copilot-only` boolean CLI shortcuts (sugar over `--preset claude-only` / `--preset copilot-only`).
- `--preset claude-only` and `--preset copilot-only` for single-backend runs (paired with the new council variants below).
- `council_opus` team — six-lens all-Claude mirror of `council` (North Star + Bug Hunter + Pragmatist + Tester + Minimalist + Architect, all Opus 4.7 + Opus team lead).
- `council_gpt55` team — six-lens all-Copilot mirror of `council` (same roles, all GPT-5.5 + GPT-5.5 team lead).
- `autosprint init --update-skills` — refresh `.claude/skills/` and `.claude/agents/` in a target repo from the autosprint source. Run after a `git pull` of autosprint.
- `_verify_target_is_initialised` guard at the start of `run_prepare_steps` — refuses to run in a non-git folder or a folder without `autosprint/config.toml`, with clear next-step pointers. No more silent partial-init in a random directory.
- `_TerseArgumentParser` — replaces argparse's full usage banner with a one-line "Did you mean: clear-logs?" hint on subcommand typos. Other argument errors keep the default banner.
- `grill-waypoint-from-issue` skill — builds `autosprint/waypoint.md` from a GitHub issue. Fetches via `gh`, extracts purpose + acceptance criteria from body and comments, grills the user only when there are real gaps.
- Tracked history files: `sprint-outcomes.log`, `plan-decisions.md`, `runtime-stats.md` now committed by default so `git checkout` rewinds the loop's view of "what's been tried". Other generated logs stay gitignored. `git_restore` excludes the three history files via pathspec so in-flight writes survive a revert.
- TypeScript type-check gate (`pre_test_gate`) for the vitest runner: runs `tsc --noEmit` (or `package.json#scripts.typecheck`) before vitest, requires both green. Opt out with `TS_TYPECHECK=false`. Closes phase 3 of the multi-language test-phase work.
- Council-family sanity tests: `council_gpt55` is all-Copilot, `council_opus` is all-Claude, `council` is mixed, all three carry the same six lenses.
- `doctor` now probes the `HOWFAR_AGENT` backend too, not just team + implement agent + fallback.

### Changed

- Default branch name for sprint runs: `pit/<timestamp>` → `autosprint/<timestamp>`.
- `MAX_SPRINTS` default raised from 10 to 100. Reviewed-plan mode still auto-sizes to 2× the plan length when the default is in effect, floored at 10.
- `agents.py` split: team definitions and the `TEAMS` registry moved to `teams.py`. `agents.py` keeps individual agent definitions and the `AGENTS` registry. Imports updated everywhere.
- `config.toml` rendering extracted from `init.py` into `config_toml.py`.
- Council-lens prompts (North Star, Bug Hunter, Pragmatist, Tester, Minimalist, Architect) extracted as shared constants so Opus and GPT-5.5 variants stay in lockstep. Previously the two variants of a lens could drift.
- `AGENT_MINIMALIST_CLAUDE` renamed to `AGENT_MINIMALIST_OPUS47` for consistency with the `*_OPUS47` / `*_GPT55` convention.
- `AGENT_BUG_HUNTER_GPT55` added; `AGENT_HUNTER_GPT55` removed and its usages in teams swapped over — consolidates a near-duplicate lens.
- CLI help: flag descriptions trimmed to one line each; epilog points to the README "CLI cookbook" section; `--max-sprints` uses metavar `N`.
- Orchestrator re-export shim trimmed: ~210 lines of legacy re-exports removed. Test imports now point at home modules directly (e.g. `from autosprint.parsing import parse_implement_result` instead of via `autosprint.orchestrator`).
- `test_prepare_helpers.py` (1879-line junk drawer) split into seven focused test files: `test_init_helpers.py`, `test_init_checks.py`, `test_init_wizard.py`, `test_cli.py`, `test_orchestrator_helpers.py`, `test_doctor_howfar.py`, `test_test_runners.py`. Logging-related tests merged into the existing `test_logging.py`. `test_prepare_helpers.py` deleted.

### Removed

- Three sprint-cadence hygiene nudges (`refactor_code_nudge`, `test_refactor_nudge`, `adr_consistency_nudge`) deleted from `plan_phase.py`. Evidence from a 48-sprint run showed they produced zero cleanup tasks. Hygiene lensing relegated to team-member voices: `Minimalist` agents propose deletion tasks; `Refactorer`'s system prompt now also audits `adr.md` for drift.
- `_copy_github_skills_to_target` removed — Copilot reads skills from `.claude/skills/` natively (verified against GitHub Copilot Agent Skills docs), so the second copy into `.github/skills/` was dead weight.
- `readme-blueprint-generator` skill — foreign GitHub-Copilot-IDE skill that was being shipped to every target repo.

### Fixed

- Minimalist prompt drift: `AGENT_MINIMALIST_GPT55` had a heavyweight prompt with `_THINK_CAREFULLY`, while the Opus variant had the original lite prompt. Now both share the heavyweight `_PROMPT_MINIMALIST` constant.
- `python-refactoring` skill: duplicate rule numbered 13 (Cleanup section + Error Handling section) — renumbered Error Handling rules to 14–20.
- Stale doc references after the teams.py split: `README.md` and `CLAUDE.md` updated to reference `teams.TEAMS` rather than `agents.TEAMS`.
- `git_restore` no longer blows away the just-failed sprint's FAILED line in `sprint-outcomes.log` — the three tracked history files are excluded from restore via pathspec.
- `README.md` typo: "to run run autosprint once" → "to run autosprint once".
- `_required_assistants_for_run` includes `HOWFAR_AGENT`'s backend so doctor catches missing claude/gh auth for the how-far command.
- Python-specific skills (`python-refactoring`, `test-refactoring`) are no longer copied into non-Python target repos.

## Earlier history

See `git log` for changes prior to 2026-05-23.
