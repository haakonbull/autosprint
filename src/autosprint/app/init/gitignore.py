"""Extracted from the original autosprint.app.init module."""

from autosprint.config import config
from autosprint.util.errors import add_context
from autosprint.util.output import printlev
from autosprint.util.paths import (
    AUTOSPRINT_DIR_NAME,
    LOGS_SUBDIR,
    PLAN_DECISIONS_FILENAME,
    RUNTIME_STATS_FILENAME,
    SPRINT_LOG_FILENAME,
)

_PYTHON_GITIGNORE_DEFAULTS = "# Python\n__pycache__/\n*.py[cod]\n*$py.class\n*.egg-info/\ndist/\nbuild/\n\n# Virtual envs\n.venv/\nvenv/\nenv/\n.env\n\n# Testing / type-checking / linting\n.pytest_cache/\n.coverage\nhtmlcov/\n.mypy_cache/\n.ruff_cache/\n\n# Notebooks\n.ipynb_checkpoints/\n\n# IDE\n.idea/\n.vscode/\n\n# OS\n.DS_Store\nThumbs.db\n"
_TS_GITIGNORE_DEFAULTS = "# Node / TypeScript\nnode_modules/\ndist/\nbuild/\ncoverage/\n*.tsbuildinfo\n\n# Logs\nnpm-debug.log*\nyarn-debug.log*\nyarn-error.log*\npnpm-debug.log*\n\n# Env\n.env\n.env.local\n\n# IDE\n.idea/\n.vscode/\n\n# OS\n.DS_Store\nThumbs.db\n"


def _ensure_gitignore_entries() -> None:
    """Ensure TARGET_REPO/.gitignore ignores noisy autosprint-generated files (verbose console logs, cache, control files) while *tracking* the three history files (`sprint-outcomes.log`, `plan-decisions.md`, `runtime-stats.md`). Tracked history means a checkout to an older commit also rewinds the loop's view of "what's been tried", so branch-jumping doesn't need a manual `clear-logs`. The wildcard-plus-unignore pattern (`logs/*` then `!logs/sprint-outcomes.log` …) is the standard git idiom for "ignore a directory except these specific files". When the file is missing entirely (fresh repo), seed it with language-appropriate defaults first (Python venv/__pycache__/etc. when `pyproject.toml` is present, or node_modules/dist/etc. when `package.json` is present and `pyproject.toml` is not), then append the autosprint block. Also adds the language's essential ignores (`node_modules/` for TS, `__pycache__/` + `.venv/` for Python) to the required list so even an existing thin `.gitignore` picks them up. Idempotent: also migrates legacy `autosprint/logs/` (no wildcard, ignores everything) to the new pattern so existing target repos pick up the tracked-history policy on next run."""
    try:
        has_pyproject = (config.TARGET_REPO_PATH / "pyproject.toml").exists()
        has_package_json = (config.TARGET_REPO_PATH / "package.json").exists()
        target_is_ts = has_package_json and not has_pyproject

        language_essentials: list[str] = []
        if target_is_ts:
            language_essentials = ["node_modules/", "dist/", "coverage/"]
        elif has_pyproject:
            language_essentials = ["__pycache__/", ".venv/", "*.pyc"]

        required = [*language_essentials, f"{LOGS_SUBDIR}/*", f"!{SPRINT_LOG_FILENAME}", f"!{PLAN_DECISIONS_FILENAME}", f"!{RUNTIME_STATS_FILENAME}", f"{AUTOSPRINT_DIR_NAME}/cache/", f"{AUTOSPRINT_DIR_NAME}/stop", f"{AUTOSPRINT_DIR_NAME}/stop-now"]
        gitignore_path = config.TARGET_REPO_PATH / ".gitignore"
        seeded = not gitignore_path.exists()
        existing_text = "" if seeded else gitignore_path.read_text(encoding="utf-8")
        if seeded:
            if target_is_ts:
                existing_text = _TS_GITIGNORE_DEFAULTS
                printlev(f"[prepare] Seeded {gitignore_path} with Node/TypeScript-project defaults.", level=100)
            else:
                existing_text = _PYTHON_GITIGNORE_DEFAULTS
                printlev(f"[prepare] Seeded {gitignore_path} with Python-project defaults.", level=100)

        # Migration: legacy `autosprint/logs/` (no wildcard) ignored everything in logs/ — replace with the new wildcard-plus-unignore pattern so history files start being tracked.
        legacy_line = f"{LOGS_SUBDIR}/"
        if any(line.strip() == legacy_line for line in existing_text.splitlines()):
            existing_text = "\n".join(line for line in existing_text.splitlines() if line.strip() != legacy_line) + ("\n" if existing_text.endswith("\n") else "")
            printlev(f"[prepare] Migrating .gitignore: replaced `{legacy_line}` with the tracked-history pattern (sprint-outcomes.log, plan-decisions.md, runtime-stats.md are now committed).", level=100)

        existing_lines = {line.strip() for line in existing_text.splitlines()}
        missing = [entry for entry in required if entry not in existing_lines]
        if not missing and not seeded:
            return
        addition = ("\n" if existing_text and not existing_text.endswith("\n") else "") + "\n# autosprint — ignore verbose logs / cache / control files; track the three history files\n" + "\n".join(missing) + "\n" if missing else ""
        gitignore_path.write_text(existing_text + addition, encoding="utf-8")
        if missing:
            printlev(f"[prepare] Appended to .gitignore: {', '.join(missing)}", level=50)
    except Exception as e:
        raise add_context(e, "Failed to ensure .gitignore entries in TARGET_REPO") from e
