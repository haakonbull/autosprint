"""Rendering for `autosprint/config.toml` — the per-repo settings file.

Split out from init.py so init.py focuses on init orchestration (file seeding,
gitignore, CLI checks) while the rendering details — the header text, the
field list, the live-vs-commented logic — live here.

Public surface: `render_config_toml(active)` returns the toml text the init
wizard writes. Callers pass a mapping of toml-key → string value for the
settings that should be written as live (uncommented) overrides; every other
known field is rendered commented-out at its code default so the user has a
reference to uncomment by hand.
"""

from __future__ import annotations

_CONFIG_TOML_HEADER = """\
# autosprint/config.toml — per-repo autosprint settings (committed).
#
# Precedence: code defaults < this file < environment / .env < CLI flags.
# Uncomment a line to override a default for this repository."""

# Scalar settings rendered into config.toml: (toml key, default literal, inline comment).
# A key present in the `active` mapping passed to `render_config_toml` is written as a
# live line; every other key is written commented-out at its default — a reference the
# user can uncomment by hand. Order here is the order in the file.
_CONFIG_TOML_SCALAR_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("implement_agent", '"implementor_opus48"', "agent that runs the Implement phase"),
    ("implement_fallback_agent", '"implementor_gpt55"', 'refusal-fallback agent ("" disables it)'),
    ("howfar_agent", '"howfar_opus48"', "agent for `autosprint how-far` (howfar_gpt55 = Copilot-only)"),
    ("howfar_heartbeat_every_n_sprints", "10", "run passive how-far heartbeat every N sprints (0 disables)"),
    ("sp_target", "8", "task-grouping story-point aim (0 disables)"),
    ("sp_min", "2", "preferred story-point band, low end"),
    ("sp_max", "20", "preferred story-point band, high end"),
    ("replan_every_n_sprints", "5", "force a replan at least this often"),
    ("defer_blocked_task_after_failures", "2", "move future-publication blockers to Blocked after N failures (0 disables)"),
    ("test_phase_quick_only", "false", 'true runs only `-m "not slow"` each sprint'),
    ("target_test_runner", '"auto"', "test runner: auto (detect) | pytest | vitest"),
    ("test_command", '""', "override the Test-phase command (parser stays the runner's)"),
    ("format_check", '"off"', 'format gate: off | auto | "<literal command>"'),
    ("lint_check", '"off"', 'lint gate: off | auto | "<literal command>"'),
    ("coverage_track", "false", "track pytest --cov in autosprint/logs/coverage-history.log"),
)


def _render_active_value(key: str, value: str, default_literal: str) -> str:
    """Format `key = value` for a live (uncommented) line in config.toml.
    Booleans (`true` / `false`) and the empty-string sentinel render bare
    when the default does — quoted strings (`"foo"`) stay quoted. Mirroring
    the default-literal style keeps a wizard-written file parseable as TOML
    rather than coercing every value into a quoted string."""
    bare = not (default_literal.startswith('"') and default_literal.endswith('"'))
    if bare or value in ("true", "false"):
        return f"{key} = {value}"
    return f'{key} = "{value}"'


def render_config_toml(active: dict[str, str] | None = None) -> str:
    """Render the autosprint/config.toml text. Keys in `active` (a mapping of toml key → string value) are written as live settings; every other known key is written commented-out at its default. `render_config_toml({})` reproduces the plain all-commented template. The init wizard passes only the answers that deviate from defaults, so a generated config.toml records genuine per-repo choices and nothing else — a both-backends Python repo (all defaults) yields a pure template."""
    active = active or {}
    out: list[str] = [_CONFIG_TOML_HEADER, ""]
    for key, default_literal, comment in _CONFIG_TOML_SCALAR_FIELDS:
        assignment = _render_active_value(key, active[key], default_literal) if key in active else f"# {key} = {default_literal}"
        out.append(f"{assignment:<50}# {comment}")
    out.append("")
    out.append("# Planning team. A single `team` applies everywhere; or set it per mode:")
    out.append(f'team = "{active["team"]}"' if "team" in active else '# team = "council"')
    out.append("#")
    out.append("# [plan]")
    out.append('# team = "council"      # team for `autosprint plan` (hand-reviewed — cast a wide net)')
    out.append("#")
    out.append("# [auto_replan]")
    out.append('# team = "builder"      # team for `autosprint run --auto-replan` (in-loop — lighter)')
    return "\n".join(out) + "\n"
