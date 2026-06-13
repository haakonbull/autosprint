"""Agent definitions and the AGENTS registry.

An *agent* is a single (assistant, model, persona, tools) combination. Teams
that group agents into planning rosters live in `autosprint.registry.teams`.

Each agent declares:
  - name:           human-readable label
  - assistant:      "claude" or "copilot" — which SDK to dispatch to
  - model:          model id passed to the SDK
  - system_prompt:  role and behavior instructions
  - tools:          tool allowlist this agent may use

The Plan phase may further restrict tools (it passes ``TOOLS_READ_ONLY`` to
dispatch), but an agent can never exceed its declared tool preset.

File layout:
  1. Tool presets        — semantic labels each dispatcher translates.
  2. Shared prompt text  — prompt fragments re-used by multiple agents.
  3. Agents              — one dict per agent, grouped by role family.
  4. AGENTS registry     — string-key lookup table for config resolution."""

# Re-export the full public surface so existing import paths keep working.

from typing import Any

from autosprint.registry.agents.definitions import (
    AGENT_ANALYST_GPT52_COPILOT,
    AGENT_ANALYST_OPUS_CLAUDE,
    AGENT_ARCHITECT_COPILOT,
    AGENT_ARCHITECT_GPT55,
    AGENT_ARCHITECT_OPUS48,
    AGENT_BUG_HUNTER_CLAUDE,
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
    AGENT_HOWFAR_GPT55,
    AGENT_HOWFAR_OPUS48,
    AGENT_IMPLEMENTOR_GPT41,
    AGENT_IMPLEMENTOR_GPT55,
    AGENT_IMPLEMENTOR_OPUS48,
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
    AGENT_REFACTORER_CLAUDE,
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
from autosprint.registry.agents.personas import _HOWFAR_PERSONA, _IMPLEMENTOR_PERSONA, _PROMPT_ARCHITECT, _PROMPT_BUG_HUNTER, _PROMPT_EDITOR, _PROMPT_MINIMALIST, _PROMPT_NORTH_STAR, _PROMPT_PRAGMATIST, _PROMPT_STEELMANNER, _PROMPT_SYNTHESIZER, _PROMPT_TESTER, _PROMPT_WEB_RESEARCHER, _THINK_CAREFULLY, TOOLS_FULL, TOOLS_READ_ONLY, TOOLS_RESEARCH, VALID_PRESETS  # noqa: F401

AGENTS: dict[str, dict[str, Any]] = {
    "analyst_gpt52_copilot": AGENT_ANALYST_GPT52_COPILOT,
    "analyst_opus_claude": AGENT_ANALYST_OPUS_CLAUDE,
    "architect_copilot": AGENT_ARCHITECT_COPILOT,
    "architect_gpt55": AGENT_ARCHITECT_GPT55,
    "architect_opus48": AGENT_ARCHITECT_OPUS48,
    "bug_hunter_claude": AGENT_BUG_HUNTER_CLAUDE,
    "bug_hunter_gpt55": AGENT_BUG_HUNTER_GPT55,
    "bug_hunter_opus48": AGENT_BUG_HUNTER_OPUS48,
    "clarifier_claude": AGENT_CLARIFIER_CLAUDE,
    "clarifier_opus48": AGENT_CLARIFIER_OPUS48,
    "decider_haiku_claude": AGENT_DECIDER_HAIKU_CLAUDE,
    "deliberator_opus_claude": AGENT_DELIBERATOR_OPUS_CLAUDE,
    "deliberator_sonnet_claude": AGENT_DELIBERATOR_SONNET_CLAUDE,
    "editor_gpt55": AGENT_EDITOR_GPT55,
    "editor_opus48": AGENT_EDITOR_OPUS48,
    "guardian_copilot": AGENT_GUARDIAN_COPILOT,
    "guardian_gpt55": AGENT_GUARDIAN_GPT55,
    "howfar_gpt55": AGENT_HOWFAR_GPT55,
    "howfar_opus48": AGENT_HOWFAR_OPUS48,
    "implementor_gpt41": AGENT_IMPLEMENTOR_GPT41,
    "implementor_gpt55": AGENT_IMPLEMENTOR_GPT55,
    "implementor_opus48": AGENT_IMPLEMENTOR_OPUS48,
    "innovator_gpt55": AGENT_INNOVATOR_GPT55,
    "minimalist_gpt55": AGENT_MINIMALIST_GPT55,
    "minimalist_opus48": AGENT_MINIMALIST_OPUS48,
    "north_star_gpt55": AGENT_NORTH_STAR_GPT55,
    "north_star_opus48": AGENT_NORTH_STAR_OPUS48,
    "pragmatist_claude": AGENT_PRAGMATIST_CLAUDE,
    "pragmatist_gpt55": AGENT_PRAGMATIST_GPT55,
    "pragmatist_opus48": AGENT_PRAGMATIST_OPUS48,
    "quick_a_gpt41_copilot": AGENT_QUICK_A_GPT41_COPILOT,
    "quick_b_gpt41_copilot": AGENT_QUICK_B_GPT41_COPILOT,
    "refactorer_claude": AGENT_REFACTORER_CLAUDE,
    "refactorer_gpt55": AGENT_REFACTORER_GPT55,
    "research_lead_gpt55": AGENT_RESEARCH_LEAD_GPT55,
    "research_lead_opus48": AGENT_RESEARCH_LEAD_OPUS48,
    "speed_runner_copilot": AGENT_SPEED_RUNNER_COPILOT,
    "steelmanner_gpt55": AGENT_STEELMANNER_GPT55,
    "steelmanner_opus48": AGENT_STEELMANNER_OPUS48,
    "strategist_opus48": AGENT_STRATEGIST_OPUS48,
    "synthesizer_gpt55": AGENT_SYNTHESIZER_GPT55,
    "synthesizer_opus48": AGENT_SYNTHESIZER_OPUS48,
    "teamlead_gpt55": AGENT_TEAMLEAD_GPT55,
    "teamlead_opus48": AGENT_TEAMLEAD_OPUS48,
    "tester_copilot": AGENT_TESTER_COPILOT,
    "tester_gpt55": AGENT_TESTER_GPT55,
    "tester_opus48": AGENT_TESTER_OPUS48,
    "thinker_opus48": AGENT_THINKER_OPUS48,
    "visionary_claude": AGENT_VISIONARY_CLAUDE,
    "visionary_gpt55": AGENT_VISIONARY_GPT55,
    "visionary_opus48": AGENT_VISIONARY_OPUS48,
    "web_researcher_gpt55": AGENT_WEB_RESEARCHER_GPT55,
    "web_researcher_opus48": AGENT_WEB_RESEARCHER_OPUS48,
}
# Legacy aliases: target-repo config.toml files written before the Opus
# 4.7 → 4.8 bump may still reference `*_opus47` keys. They resolve to the
# same (now 4.8) agents so existing setups keep running unchanged.
AGENTS.update({key.replace("_opus48", "_opus47"): agent for key, agent in list(AGENTS.items()) if key.endswith("_opus48")})
