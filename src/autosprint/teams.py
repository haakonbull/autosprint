"""Team definitions and the TEAMS registry.

A team is a dict with an "agents" list and an optional "selector" agent. The
selector runs the final merge when there are multiple agents. If omitted,
agents[0] is used. Single-agent teams never need a selector. Teams may only
reference agents defined in autosprint.agents.

Split out from agents.py so each file has one job: agents.py defines
individual agents and their AGENTS registry; this module composes those into
teams and exposes the TEAMS registry the CLI / config / wizard look up.
"""

from __future__ import annotations

from typing import Any

from autosprint.agents import (
    AGENT_ANALYST_GPT52_COPILOT,
    AGENT_ANALYST_OPUS_CLAUDE,
    AGENT_ARCHITECT_GPT55,
    AGENT_ARCHITECT_OPUS48,
    AGENT_BUG_HUNTER_GPT55,
    AGENT_BUG_HUNTER_OPUS48,
    AGENT_CLARIFIER_CLAUDE,
    AGENT_CLARIFIER_OPUS48,
    AGENT_DECIDER_HAIKU_CLAUDE,
    AGENT_DELIBERATOR_OPUS_CLAUDE,
    AGENT_DELIBERATOR_SONNET_CLAUDE,
    AGENT_EDITOR_GPT55,
    AGENT_EDITOR_OPUS48,
    AGENT_GUARDIAN_COPILOT,
    AGENT_GUARDIAN_GPT55,
    AGENT_INNOVATOR_GPT55,
    AGENT_MINIMALIST_GPT55,
    AGENT_MINIMALIST_OPUS48,
    AGENT_NORTH_STAR_GPT55,
    AGENT_NORTH_STAR_OPUS48,
    AGENT_PRAGMATIST_CLAUDE,
    AGENT_PRAGMATIST_GPT55,
    AGENT_PRAGMATIST_OPUS48,
    AGENT_QUICK_A_GPT41_COPILOT,
    AGENT_QUICK_B_GPT41_COPILOT,
    AGENT_REFACTORER_GPT55,
    AGENT_RESEARCH_LEAD_GPT55,
    AGENT_RESEARCH_LEAD_OPUS48,
    AGENT_SPEED_RUNNER_COPILOT,
    AGENT_STEELMANNER_GPT55,
    AGENT_STEELMANNER_OPUS48,
    AGENT_STRATEGIST_OPUS48,
    AGENT_SYNTHESIZER_GPT55,
    AGENT_SYNTHESIZER_OPUS48,
    AGENT_TEAMLEAD_GPT55,
    AGENT_TEAMLEAD_OPUS48,
    AGENT_TESTER_COPILOT,
    AGENT_TESTER_GPT55,
    AGENT_TESTER_OPUS48,
    AGENT_THINKER_OPUS48,
    AGENT_VISIONARY_CLAUDE,
    AGENT_VISIONARY_GPT55,
    AGENT_VISIONARY_OPUS48,
    AGENT_WEB_RESEARCHER_GPT55,
    AGENT_WEB_RESEARCHER_OPUS48,
)

# -----------------------------------------------------------------------------
# Solo teams — one planner, no selector needed. Alphabetised.
# -----------------------------------------------------------------------------

TEAM_SOLO: dict[str, Any] = {"agents": [AGENT_ANALYST_OPUS_CLAUDE], "description": "1 agent (Analyst Opus 4.8) as planner + shared lead"}
TEAM_SOLO_LITE: dict[str, Any] = {"agents": [AGENT_SPEED_RUNNER_COPILOT], "description": "1 agent (Speed-runner GPT-4.1 Copilot) — cheap/fast debug team"}
TEAM_SOLO_GPT52: dict[str, Any] = {"agents": [AGENT_ANALYST_GPT52_COPILOT], "description": "1 agent (Analyst GPT-5.2 Copilot) as planner + shared lead"}
# solo_gpt55 — single GPT-5.5 planner. Pair with implementor_gpt55 via
# `--preset solo-gpt55` for an all-Copilot GPT-5.5 run, or with
# implementor_opus48 for a cheap-plan / strong-implement combo.
TEAM_SOLO_GPT55: dict[str, Any] = {"agents": [AGENT_BUG_HUNTER_GPT55], "description": "1 agent (Bug Hunter GPT-5.5 Copilot) as planner + shared lead"}
TEAM_SOLO_HAIKU_TEST: dict[str, Any] = {"agents": [AGENT_DECIDER_HAIKU_CLAUDE], "description": "1 agent (Decider Haiku 4.5) as planner — Claude-side smoke/debug"}
TEAM_SOLO_OPUS: dict[str, Any] = {"agents": [AGENT_DELIBERATOR_OPUS_CLAUDE], "description": "1 agent (Deliberator Opus 4.8) as planner + shared lead"}
TEAM_SOLO_SONNET: dict[str, Any] = {"agents": [AGENT_DELIBERATOR_SONNET_CLAUDE], "description": "1 agent (Deliberator Sonnet 4.6) as planner + shared lead"}

# -----------------------------------------------------------------------------
# Multi-agent teams — explicit team lead (selector) required.
# Alphabetised to match the TEAMS registry.
# -----------------------------------------------------------------------------

# Debug team — two identical GPT-4.1 Copilot agents. Use to exercise
# multi-agent select machinery without burning Claude tokens.
TEAM_DEBUG_DUAL_GPT41: dict[str, Any] = {
    "agents": [
        AGENT_QUICK_A_GPT41_COPILOT,
        AGENT_QUICK_B_GPT41_COPILOT,
    ],
    "selector": AGENT_QUICK_A_GPT41_COPILOT,
    "description": "2 identical GPT-4.1 Copilot planners + team lead — exercises multi-agent machinery without burning Claude tokens",
}

# Duo team — 2 planners + Opus 4.8 team lead. Lighter than `power` for
# everyday iteration on smaller projects where 10 parallel planners is overkill.
TEAM_DUO: dict[str, Any] = {
    "agents": [AGENT_THINKER_OPUS48, AGENT_BUG_HUNTER_GPT55],
    "selector": AGENT_TEAMLEAD_OPUS48,
    "description": "2 planners (Thinker Opus 4.8 + Bug Hunter GPT-5.5) + Team Lead Opus 4.8 — default everyday team",
}

TEAM_MIXED: dict[str, Any] = {
    "agents": [
        AGENT_GUARDIAN_COPILOT,
        AGENT_PRAGMATIST_CLAUDE,
        AGENT_CLARIFIER_CLAUDE,
        AGENT_TESTER_COPILOT,
        AGENT_VISIONARY_CLAUDE,
    ],
    "selector": AGENT_DELIBERATOR_OPUS_CLAUDE,
    "description": "5 planners (3 Claude + 2 Copilot personas) + Deliberator Opus 4.8 as lead",
}

# Power team — ten-agent production roster (5 Opus 4.8 + 5 GPT-5.5)
# + Opus 4.8 team lead. The heavyweight option for deep planning.
TEAM_POWER: dict[str, Any] = {
    "agents": [
        AGENT_STRATEGIST_OPUS48,
        AGENT_ARCHITECT_GPT55,
        AGENT_BUG_HUNTER_OPUS48,
        AGENT_MINIMALIST_GPT55,
        AGENT_TESTER_GPT55,
        AGENT_CLARIFIER_OPUS48,
        AGENT_GUARDIAN_GPT55,
        AGENT_VISIONARY_OPUS48,
        AGENT_PRAGMATIST_OPUS48,
        AGENT_REFACTORER_GPT55,
    ],
    "selector": AGENT_TEAMLEAD_OPUS48,
    "description": "10 planners (5 Opus 4.8 + 5 GPT-5.5) + Team Lead Opus 4.8 — heavyweight for deep planning",
}

TEAM_QUARTET: dict[str, Any] = {
    "agents": [
        AGENT_TESTER_GPT55,
        AGENT_MINIMALIST_GPT55,
        AGENT_VISIONARY_OPUS48,
        AGENT_BUG_HUNTER_OPUS48,
        AGENT_NORTH_STAR_OPUS48,
    ],
    "selector": AGENT_TEAMLEAD_OPUS48,
    "description": "5 planners (Tester + Minimalist GPT-5.5, Visionary + Bug Hunter + North Star Opus 4.8) + Team Lead Opus 4.8 — North Star enforces goal-progress bias against test-pin drift",
}

TEAM_BUILDER: dict[str, Any] = {
    "agents": [
        AGENT_TESTER_GPT55,
        AGENT_MINIMALIST_GPT55,
        AGENT_PRAGMATIST_OPUS48,
        AGENT_NORTH_STAR_OPUS48,
    ],
    "selector": AGENT_TEAMLEAD_OPUS48,
    "description": "4 planners (Tester + Minimalist GPT-5.5, Pragmatist + North Star Opus 4.8) + Team Lead Opus 4.8 — balanced quartet: quality/coverage from GPT-5.5, low-hanging-fruit hunting + destination gap closing from Opus 4.8; avoids the Visionary/NorthStar overlap where both voices chased goal-progress tasks",
}

TEAM_COUNCIL: dict[str, Any] = {
    "agents": [
        AGENT_NORTH_STAR_OPUS48,
        AGENT_BUG_HUNTER_OPUS48,
        AGENT_PRAGMATIST_OPUS48,
        AGENT_TESTER_GPT55,
        AGENT_MINIMALIST_GPT55,
        AGENT_ARCHITECT_GPT55,
    ],
    "selector": AGENT_TEAMLEAD_OPUS48,
    "description": "6 planners (North Star + Bug Hunter + Pragmatist Opus 4.8, Tester + Minimalist + Architect GPT-5.5) + Team Lead Opus 4.8 — six deliberately orthogonal lenses: goal progress, correctness, quick wins, test quality, subtraction, structure. No two voices overlap, so the merged proposal list spans the codebase widely. Built for a one-off `autosprint plan` call you will hand-curate: wide net from the six voices, the lead dedupes, prunes, and ranks. Heavier than `builder` — use it for a thorough candidate list, not for every replan inside a loop.",
}

TEAM_COUNCIL_GPT55: dict[str, Any] = {
    "agents": [
        AGENT_NORTH_STAR_GPT55,
        AGENT_BUG_HUNTER_GPT55,
        AGENT_PRAGMATIST_GPT55,
        AGENT_TESTER_GPT55,
        AGENT_MINIMALIST_GPT55,
        AGENT_ARCHITECT_GPT55,
    ],
    "selector": AGENT_TEAMLEAD_GPT55,
    "description": "All-GPT-5.5 mirror of `council` — six orthogonal lenses (goal progress, correctness, quick wins, test quality, subtraction, structure) + GPT-5.5 lead. Same role shape as `council`, every seat filled by a Copilot agent. Use when you want to lean on a Copilot subscription and spare Claude tokens. Pair with `--implement-agent implementor_gpt55` for a fully-Copilot run.",
}

TEAM_COUNCIL_OPUS: dict[str, Any] = {
    "agents": [
        AGENT_NORTH_STAR_OPUS48,
        AGENT_BUG_HUNTER_OPUS48,
        AGENT_PRAGMATIST_OPUS48,
        AGENT_TESTER_OPUS48,
        AGENT_MINIMALIST_OPUS48,
        AGENT_ARCHITECT_OPUS48,
    ],
    "selector": AGENT_TEAMLEAD_OPUS48,
    "description": "All-Opus 4.8 mirror of `council` — six orthogonal lenses (goal progress, correctness, quick wins, test quality, subtraction, structure) + Opus 4.8 lead. Same role shape as `council`, every seat filled by a Claude agent. Use when you want a Claude-only run with the full six-lens depth. More expensive per planning round than `council` (no GPT-5.5 cost-sharing); cheaper than running `power`.",
}

TEAM_HUNTER: dict[str, Any] = {
    "agents": [
        AGENT_BUG_HUNTER_OPUS48,
        AGENT_BUG_HUNTER_GPT55,
        AGENT_GUARDIAN_GPT55,
        AGENT_TESTER_GPT55,
    ],
    "selector": AGENT_TEAMLEAD_OPUS48,
    "description": "4 planners (Bug Hunter Opus 4.8 + Bug Hunter/Guardian/Tester GPT-5.5) + Team Lead Opus 4.8 — all four voices specialised in finding concrete failure modes (off-by-ones, unhandled None, risky changes, coverage gaps). For stabilise-before-next-feature phases where the codebase has accumulated risk and `builder`'s forward-pull would paper over real problems",
}

TEAM_REFINER: dict[str, Any] = {
    "agents": [
        AGENT_REFACTORER_GPT55,
        AGENT_MINIMALIST_GPT55,
        AGENT_CLARIFIER_OPUS48,
        AGENT_TESTER_GPT55,
    ],
    "selector": AGENT_TEAMLEAD_OPUS48,
    "description": "4 planners (Refactorer + Minimalist + Tester GPT-5.5, Clarifier Opus 4.8) + Team Lead Opus 4.8 — focused on structural cleanliness, naming, subtraction, and test consolidation. For cleanup phases after feature surface stabilises; use sparingly and not as a default — running a full repo through a refiner team when there's unfinished feature work wastes sprint budget on polish",
}

# -----------------------------------------------------------------------------
# Research teams — four lenses (Web Researcher, Synthesizer, Steelmanner,
# Editor) tuned for research projects whose deliverables are markdown documents
# (sources / paper / deep-dives), not running code. All three variants route
# both members and selector to the research-flavored plan-team prompts via the
# `plan_prompt_file` / `plan_lead_prompt_file` attributes on each agent.
# -----------------------------------------------------------------------------

TEAM_RESEARCH_COUNCIL: dict[str, Any] = {
    "agents": [
        AGENT_WEB_RESEARCHER_GPT55,
        AGENT_SYNTHESIZER_OPUS48,
        AGENT_STEELMANNER_OPUS48,
        AGENT_EDITOR_GPT55,
    ],
    "selector": AGENT_RESEARCH_LEAD_OPUS48,
    "description": "4 research planners (Web Researcher GPT-5.5, Synthesizer + Steelmanner Opus 4.8, Editor GPT-5.5) + Research Lead Opus 4.8 — four research lenses (source coverage, synthesis across material, argument balance, format discipline) for projects whose deliverables are markdown documents (sources / paper / deep-dives). Mixed Opus + GPT to spread cost; the thinking-heavy roles (Synthesizer, Steelmanner) get Opus, the rule-checking and external-fetching roles (Editor, Web Researcher) get GPT-5.5.",
}

TEAM_RESEARCH_COUNCIL_OPUS: dict[str, Any] = {
    "agents": [
        AGENT_WEB_RESEARCHER_OPUS48,
        AGENT_SYNTHESIZER_OPUS48,
        AGENT_STEELMANNER_OPUS48,
        AGENT_EDITOR_OPUS48,
    ],
    "selector": AGENT_RESEARCH_LEAD_OPUS48,
    "description": "All-Opus 4.8 mirror of `research_council` — same four research lenses, every seat filled by a Claude agent. Use when you want a Claude-only research run, or when GPT-5.5 quota is the binding constraint.",
}

TEAM_RESEARCH_COUNCIL_GPT55: dict[str, Any] = {
    "agents": [
        AGENT_WEB_RESEARCHER_GPT55,
        AGENT_SYNTHESIZER_GPT55,
        AGENT_STEELMANNER_GPT55,
        AGENT_EDITOR_GPT55,
    ],
    "selector": AGENT_RESEARCH_LEAD_GPT55,
    "description": "All-GPT-5.5 mirror of `research_council` — same four research lenses, every seat filled by a Copilot agent. Use when you want to lean on a Copilot subscription and spare Claude tokens. Pair with an implementor on the Copilot side too.",
}

TEAM_RESEARCH_COUNCIL_THREEQUARTERS_GPT: dict[str, Any] = {
    "agents": [
        AGENT_WEB_RESEARCHER_OPUS48,
        AGENT_SYNTHESIZER_GPT55,
        AGENT_STEELMANNER_GPT55,
        AGENT_EDITOR_GPT55,
    ],
    "selector": AGENT_RESEARCH_LEAD_GPT55,
    "description": "Three-quarters-GPT mirror of `research_council` — same four research lenses (source coverage, synthesis across material, argument balance, format discipline), but only one Claude seat: Web Researcher (source discovery + quality judgement, the foundation of a research project) runs Opus 4.8 while Synthesizer, Steelmanner, and Editor run GPT-5.5, with a GPT-5.5 Research Lead. Use when you want to lean on a Copilot subscription but keep the strongest model on source selection. Pair with `--implement-agent implementor_gpt55` for a mostly-Copilot run.",
}

TEAM_QUICK_MIXED: dict[str, Any] = {
    "agents": [
        AGENT_DECIDER_HAIKU_CLAUDE,
        AGENT_SPEED_RUNNER_COPILOT,
    ],
    "selector": AGENT_DECIDER_HAIKU_CLAUDE,
    "description": "2 planners (Decider Haiku 4.5 + Speed-runner GPT-4.1) + Haiku as lead — cheap/fast team",
}

TEAM_QUICK: dict[str, Any] = {
    "agents": [
        AGENT_DECIDER_HAIKU_CLAUDE,
        AGENT_SPEED_RUNNER_COPILOT,
    ],
    "selector": AGENT_SPEED_RUNNER_COPILOT,
    "description": "2 planners (Decider Haiku 4.5 + Speed-runner GPT-4.1) + Speed-runner GPT-4.1 as lead — cheapest/fastest full team",
}

# Superquick — single GPT-4.1 Copilot agent, no team lead. The orchestrator's
# single-agent branch handles this: the lone agent both proposes and is used
# as the selector (config.TEAM_SELECTOR falls back to agents[0] when no
# explicit selector is set). For fastest-possible debug iteration.
TEAM_SUPERQUICK: dict[str, Any] = {
    "agents": [AGENT_QUICK_A_GPT41_COPILOT],
    "description": "1 agent (Quick-A GPT-4.1 Copilot) — fastest-possible debug iteration",
}

# Trio GPT-5.5 — three all-Copilot GPT-5.5 planners + GPT-5.5 team lead.
# Pair with implementor_gpt55 for a fully Copilot, GPT-5.5-only run.
TEAM_TRIO_GPT55: dict[str, Any] = {
    "agents": [
        AGENT_INNOVATOR_GPT55,
        AGENT_VISIONARY_GPT55,
        AGENT_TESTER_GPT55,
    ],
    "selector": AGENT_TEAMLEAD_GPT55,
    "description": "3 planners (Innovator + Visionary + Tester, all GPT-5.5 Copilot) + Team Lead GPT-5.5 — all-Copilot team biased toward good code quality, forward-thinking design, and solid test coverage. Pair with implementor_gpt55.",
}


# -----------------------------------------------------------------------------
# TEAMS registry — string-key lookup table resolved from env / CLI / config.
# Alphabetised so adding a new entry is a 1-line diff.
# -----------------------------------------------------------------------------

TEAMS: dict[str, dict[str, Any]] = {
    "builder": TEAM_BUILDER,
    "council": TEAM_COUNCIL,
    "council_gpt55": TEAM_COUNCIL_GPT55,
    "council_opus": TEAM_COUNCIL_OPUS,
    "debug_dual_gpt41": TEAM_DEBUG_DUAL_GPT41,
    "duo": TEAM_DUO,
    "hunter": TEAM_HUNTER,
    "mixed": TEAM_MIXED,
    "power": TEAM_POWER,
    "quartet": TEAM_QUARTET,
    "quick": TEAM_QUICK,
    "quick_mixed": TEAM_QUICK_MIXED,
    "refiner": TEAM_REFINER,
    "research_council": TEAM_RESEARCH_COUNCIL,
    "research_council_gpt55": TEAM_RESEARCH_COUNCIL_GPT55,
    "research_council_opus": TEAM_RESEARCH_COUNCIL_OPUS,
    "research_council_threequartersGPT": TEAM_RESEARCH_COUNCIL_THREEQUARTERS_GPT,
    "solo": TEAM_SOLO,
    "solo_lite": TEAM_SOLO_LITE,
    "solo_gpt52": TEAM_SOLO_GPT52,
    "solo_gpt55": TEAM_SOLO_GPT55,
    "solo_haiku_test": TEAM_SOLO_HAIKU_TEST,
    "solo_opus": TEAM_SOLO_OPUS,
    "solo_sonnet": TEAM_SOLO_SONNET,
    "superquick": TEAM_SUPERQUICK,
    "trio_gpt55": TEAM_TRIO_GPT55,
}
