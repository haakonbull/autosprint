"""Extracted from the original autosprint.app.init module."""

from __future__ import annotations

import re
import shutil
import subprocess

from autosprint.config import config
from autosprint.util.errors import add_context
from autosprint.util.output import printlev
from autosprint.util.paths import (
    AUTOSPRINT_DIR_NAME,
)


def _verify_target_is_git_repo() -> None:
    """Refuse to init a TARGET_REPO that isn't a git repository — autosprint's revert/commit flow depends on it. Raises RuntimeError with a pointer to `git init`."""
    try:
        dot_git = config.TARGET_REPO_PATH / ".git"
        if not dot_git.exists():
            raise RuntimeError(f"TARGET_REPO ({config.TARGET_REPO_PATH}) is not a git repository. Autosprint's revert/commit flow requires a git repo — run `git init` in TARGET_REPO before `autosprint init`.")
    except RuntimeError:
        raise
    except Exception as e:
        raise add_context(e, f"Failed to verify TARGET_REPO is a git repo ({config.TARGET_REPO_PATH})") from e


def _verify_target_is_initialised() -> None:
    """Guard for `autosprint run` / `autosprint plan`: abort before any state-mutating prepare step if TARGET_REPO doesn't look like an autosprint-initialised git repo. Two checks: (1) `.git/` exists (we need a real git repo for branch/commit/revert), (2) `autosprint/config.toml` exists (the init marker — created by `autosprint init`, committed to the target). Without these, the legacy prepare flow would silently seed `autosprint/`, a `.gitignore`, etc., in whatever directory you're standing in — invasive and confusing. Raises RuntimeError with a clear next-step pointer; callers should let the message reach the user verbatim."""
    try:
        dot_git = config.TARGET_REPO_PATH / ".git"
        if not dot_git.exists():
            raise RuntimeError(f"`{config.TARGET_REPO_PATH}` is not a git repository — autosprint's revert/commit flow requires one.\n  → Run `git init` in the target repo, then `autosprint init`.")
        config_toml = config.TARGET_REPO_PATH / AUTOSPRINT_DIR_NAME / "config.toml"
        if not config_toml.exists():
            raise RuntimeError(f"`{config.TARGET_REPO_PATH}` has no autosprint setup (missing `{AUTOSPRINT_DIR_NAME}/config.toml`).\n  → Run `autosprint init` here first, then re-run.")
    except RuntimeError:
        raise
    except Exception as e:
        raise add_context(e, f"Failed to verify autosprint initialisation in {config.TARGET_REPO_PATH}") from e


CLAUDE_MD_FILENAME = "CLAUDE.md"
_CLAUDE_MD_MIN_USEFUL_CHARS = 200
_CLAUDE_MD_BLOATED_BYTES = 50 * 1024
_CLAUDE_MD_BLOATED_LINES = 1000
README_FILENAME = "README.md"
_README_MIN_USEFUL_CHARS = 200
_PYTHON_PROJECT_MARKERS: tuple[str, ...] = ("pyproject.toml", "pytest.ini", "setup.py", "setup.cfg", "requirements.txt", "conftest.py", "tox.ini")
# Maps each assistant kind to the CLI binary the dispatcher actually needs on PATH.
# Claude dispatch shells out to the `claude` CLI via claude_agent_sdk's spawn path,
# so the binary must be installed. Copilot dispatch uses the github-copilot-sdk
# Python package, which talks to Microsoft's API directly — no CLI binary needed —
# so "copilot" is intentionally NOT in this map.
_CLI_BINARY_FOR_ASSISTANT: dict[str, str] = {"claude": "claude"}
_CLI_INSTALL_HINT_FOR_ASSISTANT: dict[str, str] = {
    "claude": "Install Claude Code: https://claude.com/claude-code",
}


def _required_assistants_for_run() -> set[str]:
    """Collect the set of assistant kinds (e.g. 'claude', 'copilot') that the configured TEAM, IMPLEMENT_AGENT, IMPLEMENT_FALLBACK_AGENT, and HOWFAR_AGENT will dispatch to. Lets `_check_cli_deps_or_abort` probe only the CLIs that will actually be invoked, so Claude-only and Copilot-only setups don't get false-positive errors about the unused CLI. HOWFAR_AGENT is included so a Claude-only team with `HOWFAR_AGENT=howfar_gpt55` doesn't slip past doctor without Copilot auth being checked."""
    needed: set[str] = {agent["assistant"] for agent in config.TEAM_AGENTS}
    needed.add(config.IMPLEMENT_AGENT_CONFIG["assistant"])
    fallback = config.IMPLEMENT_FALLBACK_AGENT_CONFIG
    if fallback is not None:
        needed.add(fallback["assistant"])
    needed.add(config.HOWFAR_AGENT_CONFIG["assistant"])
    return needed


def _check_cli_deps_or_abort() -> None:
    """Fail-fast pre-flight: probe that the CLIs required by this run's TEAM/IMPLEMENT_AGENT/IMPLEMENT_FALLBACK_AGENT are actually on PATH. Without this check, a missing `claude` or `gh` surfaces as a confusing dispatch error 90 seconds into sprint 1; with it, the user gets a one-line "install X" pointer before any side effects. Hard abort (RuntimeError) — anything else gets ignored once the loop is running."""
    needed = _required_assistants_for_run()
    missing: list[tuple[str, str, str]] = []
    for assistant in sorted(needed):
        binary = _CLI_BINARY_FOR_ASSISTANT.get(assistant)
        if binary is None:
            continue  # unknown assistant kind — skip rather than block; future expansion can add an entry
        if shutil.which(binary) is None:
            missing.append((assistant, binary, _CLI_INSTALL_HINT_FOR_ASSISTANT[assistant]))
    if missing:
        lines = [f"Required CLI(s) missing for this configuration (TEAM={config.TEAM}, IMPLEMENT_AGENT={config.IMPLEMENT_AGENT}):"]
        for assistant, binary, hint in missing:
            lines.append(f"  - {assistant}: `{binary}` not on PATH. {hint}")
        raise RuntimeError("\n".join(lines))
    printlev(f"[init] ✅ Required CLIs on PATH: {', '.join(sorted(needed))}", level=100)


def _check_readme_and_warn() -> None:
    """Best-effort sanity check on TARGET_REPO/README.md. Missing → warn (the `grill-destination` skill's mature-repo mode reads README as a primary source for project intent; without it that mode falls back to weaker signals). Tiny → warn (placeholder). No bloat ceiling — long READMEs are fine; agents don't auto-load README the way they do CLAUDE.md, so context cost isn't a concern here."""
    readme = config.TARGET_REPO_PATH / README_FILENAME
    if not readme.exists():
        printlev(f"[init] ⚠ No {README_FILENAME} in TARGET_REPO. `grill-destination` mature-repo mode reads README to extract project intent — without it, that mode falls back to inferring from folder structure and commits.", level=100)
        return
    try:
        text = readme.read_text(encoding="utf-8")
    except OSError:
        return
    char_count = len(text)
    if char_count < _README_MIN_USEFUL_CHARS:
        printlev(f"[init] ⚠ {README_FILENAME} exists but is only {char_count} chars — looks like a placeholder. A real project description helps `grill-destination` and gives agents context they can't otherwise infer.", level=100)
        return
    printlev(f"[init] ✅ {README_FILENAME} present ({char_count} chars).", level=100)


def _check_target_python_setup_and_warn() -> None:
    """Best-effort sanity check: warn if TARGET_REPO doesn't look like a Python project, since autosprint assumes Python + pytest. Marker-file check only — running `pytest --collect-only` would be more accurate but the first sprint already runs the suite (INITIAL_TESTS=quick) so a non-Python target surfaces immediately. Cheap and quiet on the happy path."""
    target = config.TARGET_REPO_PATH
    found = [m for m in _PYTHON_PROJECT_MARKERS if (target / m).exists()]
    if found:
        printlev(f"[init] ✅ Target looks like a Python project (found: {', '.join(found)}).", level=100)
        return
    printlev(f"[init] ⚠ TARGET_REPO has none of {{{', '.join(_PYTHON_PROJECT_MARKERS)}}}. Autosprint assumes Python + pytest; a non-Python target will fail at the Test phase. Ignore if you're about to add a pyproject before sprint 1.", level=100)


def _check_claude_md_and_warn() -> None:
    """Best-effort sanity check on TARGET_REPO/CLAUDE.md — the file Claude Code / Agent SDK auto-loads as project context for every agent invocation. Three no-go conditions get flagged: missing → agents start each task with zero project context; tiny (<200 chars) → looks like an unfilled placeholder; bloated (≥50KB or ≥1000 lines) → eats context budget every turn. Subjective quality (well-written? up to date?) is grilling territory, not init's job."""
    claude_md = config.TARGET_REPO_PATH / CLAUDE_MD_FILENAME
    if not claude_md.exists():
        printlev(f"[init] ⚠ No {CLAUDE_MD_FILENAME} in TARGET_REPO. Agents will start each task with zero project context — they'll have to infer architecture and conventions from raw code. Consider a 30-line {CLAUDE_MD_FILENAME} (project description, structure, how to run tests, key concepts) before your first sprint.", level=100)
        return
    try:
        text = claude_md.read_text(encoding="utf-8")
    except OSError:
        return  # best-effort; don't fail init on a read error
    char_count = len(text)
    line_count = text.count("\n") + 1
    byte_count = len(text.encode("utf-8"))
    if char_count < _CLAUDE_MD_MIN_USEFUL_CHARS:
        printlev(f"[init] ⚠ {CLAUDE_MD_FILENAME} exists but is only {char_count} chars — looks like a placeholder. Agents need real project context (architecture, conventions, how to run tests) to be useful.", level=100)
        return
    if byte_count >= _CLAUDE_MD_BLOATED_BYTES or line_count >= _CLAUDE_MD_BLOATED_LINES:
        printlev(f"[init] ⚠ {CLAUDE_MD_FILENAME} is large ({char_count} chars / {line_count} lines / {byte_count // 1024} KB). Every agent invocation pays this in context budget — consider trimming non-essential sections.", level=100)
        return
    printlev(f"[init] ✅ {CLAUDE_MD_FILENAME} present ({char_count} chars / {line_count} lines).", level=100)


def _bootstrap_target_env_and_warn() -> None:
    """If the target has `.env.example` but no `.env`, ask Y/N before copying — defaulting to Y. Many users forget the manual copy step on a fresh clone, but a silent auto-copy could be presumptive (e.g. user already has a `.env` elsewhere or wants different values). The Y/N prompt with default-Y splits the difference: one keystroke accepts, one types `n` to skip. After a copy, warn the user to fill in any placeholder values. autosprint itself doesn't read the target's `.env` — but the target's app/tests usually do, which is why this matters."""
    target = config.TARGET_REPO_PATH
    example = target / ".env.example"
    actual = target / ".env"
    if not example.exists() or actual.exists():
        return
    try:
        answer = input(f"[init] Target has `.env.example` but no `.env`. Copy `{example.name}` → `.env`? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        printlev("\n[init] Skipped target `.env` bootstrap (no input). Run `cp .env.example .env` in the target manually if needed.", level=100)
        return
    if answer not in ("", "y", "yes"):
        printlev("[init] Skipped target `.env` bootstrap. Run `cp .env.example .env` in the target manually if needed.", level=100)
        return
    try:
        shutil.copy2(example, actual)
    except OSError as e:
        printlev(f"[init] ⚠ Couldn't copy `.env.example` → `.env`: {e}. Bootstrap manually with `cp .env.example .env` in {target}.", level=100)
        return
    printlev(f"[init] ✅ Copied target's `.env.example` → `.env` ({actual}).", level=100)
    printlev("[init] 👉 Edit `.env` to fill in any placeholder values (DB URLs, API keys, etc.) — placeholders like `<your-url>` will break tests until replaced.", level=100)


_DOCKERIGNORE_HIGH_PRIORITY: tuple[str, ...] = (".env", ".git", ".venv", "venv", "__pycache__", "*.pyc")


def _check_dockerignore_and_warn() -> None:
    """If the target has a Dockerfile or compose file, sanity-check `.dockerignore` exists and covers the high-priority entries that prevent secrets and bloat from baking into the image (`.env`, `.git`, `.venv`, `__pycache__`, `*.pyc`). Skipped silently for non-Docker targets — most aren't. The check is intentionally narrow: image hygiene is the user's call past the basics, but `.env` baked into a Docker image is a high-stakes leak that's worth catching."""
    target = config.TARGET_REPO_PATH
    has_docker = (target / "Dockerfile").exists() or (target / "docker-compose.yml").exists() or (target / "compose.yml").exists() or (target / "compose.yaml").exists()
    if not has_docker:
        return
    dockerignore = target / ".dockerignore"
    if not dockerignore.exists():
        printlev(f"[init] ⚠ Target has Dockerfile/compose but no `.dockerignore`. The image build context will include `.git/`, `.venv/`, and `.env` — bloated images and possible secret leak. Recommended minimum: {', '.join(_DOCKERIGNORE_HIGH_PRIORITY)}.", level=100)
        return
    try:
        existing = {line.strip() for line in dockerignore.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")}
    except OSError:
        return
    missing = [entry for entry in _DOCKERIGNORE_HIGH_PRIORITY if entry not in existing]
    if missing:
        printlev(f"[init] ⚠ `.dockerignore` is missing high-priority entries: {', '.join(missing)}. Without these your image may bake in secrets (`.env`) or bloat (`.git`, `.venv`, `__pycache__`).", level=100)
        return
    printlev("[init] ✅ `.dockerignore` covers high-priority entries.", level=100)


_CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{50,}")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}")),
    ("GitHub personal token", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[bpars]-[A-Za-z0-9\-]{10,}")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)
_SENSITIVE_SCAN_MAX_FILE_BYTES = 1_000_000
_SENSITIVE_SCAN_MAX_FINDINGS = 10


def _scan_for_sensitive_content_and_warn() -> None:
    """Pre-flight scan that flags conditions which would leak secrets via the next push. Three checks against TARGET_REPO: (1) `.env` is committed (high-priority — already in history, needs `git rm --cached` + history rewrite to fully scrub); (2) `.env` is in the worktree but not matched by .gitignore (about-to-leak); (3) high-confidence credential regexes (Anthropic/OpenAI keys, GitHub tokens, AWS access keys, Slack tokens, private-key blocks) match in any tracked file. Best-effort and non-blocking — autosprint's job is to flag, not enforce. Caps findings to keep output readable on a flagged repo."""
    target = config.TARGET_REPO_PATH
    findings: list[str] = []
    try:
        result = subprocess.run(["git", "ls-files"], cwd=target, capture_output=True, text=True, timeout=10)
        tracked = result.stdout.splitlines() if result.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        tracked = []
    if ".env" in tracked:
        findings.append("`.env` is committed to git. Remove with `git rm --cached .env`, add `.env` to .gitignore, then rewrite history to fully scrub if it ever held real secrets.")
    env_path = target / ".env"
    if env_path.exists() and ".env" not in tracked:
        try:
            ignored_check = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=target, capture_output=True, timeout=5)
            ignored = ignored_check.returncode == 0
        except (OSError, subprocess.SubprocessError):
            ignored = True  # if we can't check, don't false-positive
        if not ignored:
            findings.append("`.env` exists in the worktree but is not matched by .gitignore. Add `.env` to .gitignore before any commit.")
    matched: list[str] = []
    for rel_path in tracked:
        if not rel_path.strip():
            continue
        f = target / rel_path
        if not f.is_file():
            continue
        try:
            if f.stat().st_size > _SENSITIVE_SCAN_MAX_FILE_BYTES:
                continue
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, pattern in _CREDENTIAL_PATTERNS:
            if pattern.search(text):
                matched.append(f"{rel_path} → {label}")
                break  # one finding per file is enough; user can investigate
        if len(matched) >= _SENSITIVE_SCAN_MAX_FINDINGS:
            break
    if matched:
        joined = "\n      ".join(matched[:_SENSITIVE_SCAN_MAX_FINDINGS])
        findings.append(f"Possible credential matches in tracked files (regex match — verify before assuming false-positive):\n      {joined}")
    if findings:
        printlev("[init] ⚠ Sensitive-content scan flagged:", level=100)
        for finding in findings:
            printlev(f"      - {finding}", level=100)
        printlev("[init]   Review these before your first push. Autosprint won't block, but a leaked credential is hard to revoke after the fact.", level=100)
    else:
        printlev("[init] ✅ Sensitive-content scan clean (no committed .env, no high-confidence credential matches in tracked files).", level=100)


def _print_init_config_summary() -> None:
    """Print the resolved config bits init actually depends on. Lets the user catch a misconfigured .env before their first real sprint."""
    lines = [
        "[init] Resolved config:",
        f"       TARGET_REPO      = {config.TARGET_REPO_PATH}",
        f"       TEAM             = {config.TEAM} ({len(config.TEAM_AGENTS)} agent(s))",
        f"       IMPLEMENT_AGENT  = {config.IMPLEMENT_AGENT}",
        f"       MAX_SPRINTS      = {config.MAX_SPRINTS}",
        f"       INITIAL_TESTS    = {config.INITIAL_TESTS}",
    ]
    printlev("\n".join(lines), level=100)
