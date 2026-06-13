"""Extracted from the original autosprint.app.cli module."""

from __future__ import annotations

import re
import shutil
from datetime import datetime

from autosprint.app.cli.args import _CLI_PRESETS
from autosprint.config import config
from autosprint.infra.gates import describe_gates
from autosprint.registry.teams import TEAMS
from autosprint.reporting.banners import section_banner
from autosprint.util.errors import add_context
from autosprint.util.output import printlev
from autosprint.util.paths import AUTOSPRINT_DIR_NAME


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
    from autosprint.registry.agents import AGENTS

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
    from autosprint.reporting.run_log import recent_sprint_history

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
    from autosprint.util.paths import LOGS_SUBDIR

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
