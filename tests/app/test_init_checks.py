"""Tests for init pre-flight warn-on checks + verify_target_is_initialised.

All fast — no LLM calls, no pit_loop.
"""

import subprocess
from pathlib import Path

import pytest

import autosprint.app.init as init_mod

# ---------------------------------------------------------------------------
# _verify_target_is_initialised — guard for `autosprint run` / `plan` in random folders
# ---------------------------------------------------------------------------


def test_verify_target_is_initialised_aborts_when_not_git_repo(target_repo: Path) -> None:
    """Run/plan in a non-git folder must abort early with a clear `git init` pointer."""
    with pytest.raises(RuntimeError) as exc_info:
        init_mod._verify_target_is_initialised()
    assert "not a git repository" in str(exc_info.value)
    assert "git init" in str(exc_info.value)


def test_verify_target_is_initialised_aborts_when_config_toml_missing(git_target_repo: Path) -> None:
    """Run/plan in a git repo that has never been autosprint-init'd must abort with an `autosprint init` pointer."""
    with pytest.raises(RuntimeError) as exc_info:
        init_mod._verify_target_is_initialised()
    assert "no autosprint setup" in str(exc_info.value)
    assert "autosprint init" in str(exc_info.value)


def test_verify_target_is_initialised_passes_when_both_present(initialised_target_repo: Path) -> None:
    """When both `.git/` and `autosprint/config.toml` exist, the guard is a no-op."""
    init_mod._verify_target_is_initialised()  # must not raise


# ---------------------------------------------------------------------------
# _check_cli_deps_or_abort — fail-fast pre-flight for missing claude / gh.
# ---------------------------------------------------------------------------


def test_check_cli_deps_passes_when_claude_is_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: `claude` is on PATH → no raise. Note: copilot dispatch uses the github-copilot-sdk Python package directly (no CLI binary needed), so this check only probes for `claude`."""
    monkeypatch.setattr(init_mod.checks, "_required_assistants_for_run", lambda: {"claude", "copilot"})
    monkeypatch.setattr(init_mod.checks.shutil, "which", lambda name: f"/usr/bin/{name}")
    init_mod._check_cli_deps_or_abort()  # must not raise


def test_check_cli_deps_aborts_when_required_claude_cli_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """If TEAM uses Claude agents but `claude` isn't installed, fail fast before any side effects rather than letting the user discover it 90 seconds into sprint 1."""
    monkeypatch.setattr(init_mod.checks, "_required_assistants_for_run", lambda: {"claude"})
    monkeypatch.setattr(init_mod.checks.shutil, "which", lambda name: None if name == "claude" else f"/usr/bin/{name}")
    with pytest.raises(RuntimeError, match="claude"):
        init_mod._check_cli_deps_or_abort()


def test_check_cli_deps_silent_for_copilot_only_run_with_no_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    """Copilot-only runs use the github-copilot-sdk Python package, which talks to Microsoft's API directly — no CLI binary required. A missing `claude` (and missing `gh`) must NOT abort when the run is Copilot-only. This is the false-positive guard that makes the hard-abort behaviour safe."""
    monkeypatch.setattr(init_mod.checks, "_required_assistants_for_run", lambda: {"copilot"})
    monkeypatch.setattr(init_mod.checks.shutil, "which", lambda _name: None)
    init_mod._check_cli_deps_or_abort()  # must not raise — copilot has no CLI binary requirement


# ---------------------------------------------------------------------------
# _check_target_python_setup_and_warn — marker-file check that the target
# looks like a Python project, since autosprint assumes Python + pytest.
# ---------------------------------------------------------------------------


def test_check_python_setup_silent_when_pyproject_present(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Standard happy path — a target with pyproject.toml gets the ✅ acknowledgement, no ⚠ warning."""
    (target_repo / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    init_mod._check_target_python_setup_and_warn()
    out = capsys.readouterr().out
    assert "✅" in out
    assert "⚠" not in out


def test_check_python_setup_warns_when_no_markers_present(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A target with no pyproject / setup / requirements / pytest.ini → warn loudly. The user can ignore the warning if they're about to add one, but the surprise of "wait, this is a Go repo" should surface at init, not at sprint 1's Test phase."""
    init_mod._check_target_python_setup_and_warn()
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "Python" in out


def test_check_python_setup_silent_with_just_requirements_txt(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Pip-style projects (requirements.txt only, no pyproject) are still valid Python targets — the marker check accepts any of the standard files, not just pyproject."""
    (target_repo / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    init_mod._check_target_python_setup_and_warn()
    assert "⚠" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _check_claude_md_and_warn — sanity check on TARGET_REPO/CLAUDE.md, the file
# Claude Code / Agent SDK auto-loads as project context every agent invocation.
# ---------------------------------------------------------------------------


def test_check_claude_md_warns_when_missing(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """No CLAUDE.md at all → loud warning. Agents would otherwise start each task with zero project context and have to infer architecture from raw code, which measurably hurts output quality."""
    init_mod._check_claude_md_and_warn()
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "zero project context" in out


def test_check_claude_md_warns_when_tiny_placeholder(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A 30-char CLAUDE.md is worse than no CLAUDE.md — the user thinks they have project context but agents effectively don't. The 200-char threshold is empirical: anything below is almost always an unfilled `# Project` placeholder."""
    (target_repo / "CLAUDE.md").write_text("# Project\n\nTODO\n", encoding="utf-8")
    init_mod._check_claude_md_and_warn()
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "placeholder" in out


def test_check_claude_md_warns_when_bloated_by_line_count(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A 1500-line CLAUDE.md eats context budget on every agent invocation; warn at the conservative 1000-line threshold so the cost is visible. Threshold deliberately high to avoid nagging legitimate ~300-line files with real conventions."""
    (target_repo / "CLAUDE.md").write_text(("line\n" * 1500), encoding="utf-8")
    init_mod._check_claude_md_and_warn()
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "large" in out


def test_check_claude_md_warns_when_bloated_by_bytes(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Bytes-or-lines: a fat single-line CLAUDE.md (e.g. minified) should still trigger the bloat warning. ≥50KB is the byte threshold."""
    (target_repo / "CLAUDE.md").write_text("x" * (60 * 1024), encoding="utf-8")
    init_mod._check_claude_md_and_warn()
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "large" in out


def test_check_claude_md_silent_for_normal_file(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Happy path: a well-sized CLAUDE.md (between the placeholder floor and the bloat ceiling) gets the ✅ acknowledgement and no ⚠. Validates the thresholds aren't accidentally triggering on a typical 50-line file."""
    (target_repo / "CLAUDE.md").write_text("# Project\n\n" + "Real architecture content describing the system. " * 20, encoding="utf-8")
    init_mod._check_claude_md_and_warn()
    out = capsys.readouterr().out
    assert "✅" in out
    assert "⚠" not in out


# ---------------------------------------------------------------------------
# _check_readme_and_warn — README sanity, mirrors CLAUDE.md sanity but without
# a bloat ceiling (long READMEs are fine; agents don't auto-load it).
# ---------------------------------------------------------------------------


def test_check_readme_warns_when_missing(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """No README → warn. grill-destination mature-repo mode reads README as primary intent source; missing README weakens that mode."""
    init_mod._check_readme_and_warn()
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "README.md" in out


def test_check_readme_warns_when_tiny_placeholder(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A 30-char "# Project name\\nTODO" README is worse than no README — the user thinks they have docs but the content is empty signal. Same 200-char threshold as CLAUDE.md for consistency."""
    (target_repo / "README.md").write_text("# Project\n\nTODO\n", encoding="utf-8")
    init_mod._check_readme_and_warn()
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "placeholder" in out


def test_check_readme_silent_for_normal_file(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Happy path. Note: deliberately no upper bound — long READMEs are common and fine, unlike CLAUDE.md where every line costs context budget."""
    (target_repo / "README.md").write_text("# Demo project\n\n" + "Real description. " * 30, encoding="utf-8")
    init_mod._check_readme_and_warn()
    out = capsys.readouterr().out
    assert "✅" in out
    assert "⚠" not in out


# ---------------------------------------------------------------------------
# _bootstrap_target_env_and_warn — interactive Y/N copy of target's
# .env.example to .env when .env is missing. Default-Y so a single keystroke
# accepts; `n` skips. autosprint itself doesn't read target's .env, but
# target's app/tests usually do.
# ---------------------------------------------------------------------------


def test_bootstrap_target_env_copies_on_yes_and_warns_to_fill_placeholders(monkeypatch: pytest.MonkeyPatch, target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Default-Y path: user hits Enter, the example gets copied to .env, and a follow-up warning surfaces the "fill in placeholder values" reminder."""
    monkeypatch.setattr("builtins.input", lambda _prompt: "")  # default-Y: empty answer accepts
    (target_repo / ".env.example").write_text("DATABASE_URL=<your-url>\n", encoding="utf-8")
    init_mod._bootstrap_target_env_and_warn()
    assert (target_repo / ".env").exists()
    assert (target_repo / ".env").read_text(encoding="utf-8") == "DATABASE_URL=<your-url>\n"
    out = capsys.readouterr().out
    assert "✅" in out
    assert "placeholder" in out


def test_bootstrap_target_env_skips_on_no(monkeypatch: pytest.MonkeyPatch, target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """User typed `n` — must not copy, must print a clear "skipped" line so the user knows what happened."""
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    (target_repo / ".env.example").write_text("DATABASE_URL=<your-url>\n", encoding="utf-8")
    init_mod._bootstrap_target_env_and_warn()
    assert not (target_repo / ".env").exists()
    assert "Skipped" in capsys.readouterr().out


def test_bootstrap_target_env_silent_when_env_already_exists(monkeypatch: pytest.MonkeyPatch, target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Once .env exists, no prompt and no nagging — re-init should be quiet on this check. Critical because input() would block in non-interactive contexts; never call it when there's nothing to do."""

    def _fail_if_called(_prompt: str) -> str:
        raise AssertionError("input() must not be called when .env already exists")

    monkeypatch.setattr("builtins.input", _fail_if_called)
    (target_repo / ".env.example").write_text("DATABASE_URL=<your-url>\n", encoding="utf-8")
    (target_repo / ".env").write_text("DATABASE_URL=postgres://localhost/db\n", encoding="utf-8")
    init_mod._bootstrap_target_env_and_warn()
    assert capsys.readouterr().out == ""


def test_bootstrap_target_env_silent_when_no_example_present(monkeypatch: pytest.MonkeyPatch, target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Targets without a .env.example (pure libraries, repos that don't use dotenv) → no prompt, no warning."""

    def _fail_if_called(_prompt: str) -> str:
        raise AssertionError("input() must not be called when .env.example doesn't exist")

    monkeypatch.setattr("builtins.input", _fail_if_called)
    init_mod._bootstrap_target_env_and_warn()
    assert capsys.readouterr().out == ""


def test_bootstrap_target_env_handles_eof_gracefully(monkeypatch: pytest.MonkeyPatch, target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Non-interactive context (CI, pipe) → input() raises EOFError. Must skip cleanly with a "no input" message rather than crashing init."""

    def _eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    (target_repo / ".env.example").write_text("DATABASE_URL=<your-url>\n", encoding="utf-8")
    init_mod._bootstrap_target_env_and_warn()
    assert not (target_repo / ".env").exists()
    assert "no input" in capsys.readouterr().out.lower()


# ---------------------------------------------------------------------------
# _check_dockerignore_and_warn — only fires for Docker-using targets; flags
# the high-stakes case of `.env` baked into an image.
# ---------------------------------------------------------------------------


def test_check_dockerignore_silent_for_non_docker_target(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """No Dockerfile / compose file → the check is irrelevant and must stay silent. Most targets aren't Dockerised."""
    init_mod._check_dockerignore_and_warn()
    assert capsys.readouterr().out == ""


def test_check_dockerignore_warns_when_missing(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Dockerfile present + no .dockerignore → image build context includes .git/.venv/.env. High-stakes secret-leak path — warn loudly."""
    (target_repo / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
    init_mod._check_dockerignore_and_warn()
    out = capsys.readouterr().out
    assert "⚠" in out
    assert ".dockerignore" in out


def test_check_dockerignore_warns_on_missing_high_priority_entries(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A sparse .dockerignore that excludes node_modules but not .env still bakes secrets into the image. Catch the specific gaps."""
    (target_repo / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
    (target_repo / ".dockerignore").write_text("node_modules\n# comment line\n", encoding="utf-8")
    init_mod._check_dockerignore_and_warn()
    out = capsys.readouterr().out
    assert "⚠" in out
    assert ".env" in out


def test_check_dockerignore_silent_when_high_priority_covered(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A complete .dockerignore that covers every high-priority entry → ✅, no warnings. Validates the silence path on a real-world setup."""
    (target_repo / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (target_repo / ".dockerignore").write_text(".env\n.git\n.venv\nvenv\n__pycache__\n*.pyc\n", encoding="utf-8")
    init_mod._check_dockerignore_and_warn()
    out = capsys.readouterr().out
    assert "✅" in out
    assert "⚠" not in out


# ---------------------------------------------------------------------------
# _scan_for_sensitive_content_and_warn — pre-flight scan that flags the three
# top secret-leak conditions: committed .env, ungitignored .env, regex hits
# on credential patterns in tracked files.
# ---------------------------------------------------------------------------


def _git_init_with_files(tmp_path: Path, files: dict[str, str]) -> None:
    """Helper: init a real git repo at tmp_path and stage+commit the given file contents. Used by sensitive-scan tests because the helper relies on `git ls-files` for its scope."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=tmp_path, check=True)
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", rel], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "test"], cwd=tmp_path, check=True)


def test_sensitive_scan_clean_when_repo_has_no_secrets(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Happy path against a real git repo with mundane content → ✅, no warnings. Validates that benign code doesn't false-positive on the credential regexes."""
    _git_init_with_files(target_repo, {"app.py": "def main():\n    return 'hello world'\n", "README.md": "# Demo\n"})
    init_mod._scan_for_sensitive_content_and_warn()
    out = capsys.readouterr().out
    assert "✅" in out
    assert "⚠" not in out


def test_sensitive_scan_flags_committed_env_file(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The highest-stakes finding — `.env` is in git's tracked-file list. Already in history; remediation is more than just deleting the file. Must warn loudly."""
    _git_init_with_files(target_repo, {".env": "DATABASE_URL=postgres://prod\n", "app.py": "print('ok')\n"})
    init_mod._scan_for_sensitive_content_and_warn()
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "committed" in out.lower()


def test_sensitive_scan_flags_aws_access_key_in_tracked_file(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """High-confidence regex match. `AKIA` followed by 16 uppercase alphanumerics is unambiguously an AWS access key — false positives are vanishingly rare."""
    _git_init_with_files(target_repo, {"config.py": 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'})
    init_mod._scan_for_sensitive_content_and_warn()
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "AWS" in out


def test_sensitive_scan_flags_private_key_block(target_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A `-----BEGIN [...] PRIVATE KEY-----` header is a near-certain credential leak. Doesn't matter if the key was test-only — the user should know it's tracked."""
    _git_init_with_files(target_repo, {"id_rsa": "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n-----END RSA PRIVATE KEY-----\n"})
    init_mod._scan_for_sensitive_content_and_warn()
    out = capsys.readouterr().out
    assert "⚠" in out
    assert "Private key" in out
