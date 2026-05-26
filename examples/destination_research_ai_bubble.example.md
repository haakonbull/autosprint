# Destination

_Target-state specification for this project. What + why, in plain language. Technical decisions live in `adr.md`. Last reviewed: YYYY-MM-DD by <user>._

_Recommended autosprint config for this template (set in `autosprint/config.toml`): `team = "research_council"`, `implement_agent = "implementor_opus47"`. Copilot-only setups: `team = "research_council_gpt55"`, `implement_agent = "implementor_gpt55"`._

## How to use this document

A destination document describes the target shape of the repo.

It is analogous to a destination we want to travel to. It describes where we want to go, not how to get there.
Think of it as a GPS coordinate that a Google Maps user is navigating toward.
In the analogy, Google Maps uses the current location and the destination to plan a route.
In our situation, the AI assistant uses the current state of the codebase and `destination.md` to plan which tasks to do.

The destination will typically contain information that we already know, and many things that we don't know the concrete answer to yet.
When the answer is already known, the document should state the decision directly.
When the answer is not yet known, the destination can name the topic or question, and specify that the destination of the repo will be that we have an answer or solution to this that is well thought through, that has a clear rationale, that is consistent with the project goals, and where the rationale behind the choice is documented in `adr.md`. In that case it will be up to autosprint to determine this choice.

The destination document will describe the state we want to come to. It will not describe how we will come to that state. For instance, the destination document might state that we want good code quality according to best practice. It will not describe refactoring, since that is related to the journey not the destination. So even though good refactoring routines are important, it is not within the scope of the destination document to describe how to refactor — the destination document will describe the state we aim for after refactoring. Because the destination is stable while the journey is not, this document is re-read often but rewritten rarely.

> **Every parameter has a concrete answer or is explicitly open.** If you know the answer, write it directly under the section, replacing the italic prompt. If you don't, write a **destination-shaped sentence** describing what the repo will have once the question is answered, followed by an explicit `*(Open — autosprint to decide.)*` italic marker. Example for an unanswered Test strategy: *"We have a well thought-through testing strategy with a clear rationale specified in `adr.md`. (Open — autosprint to decide.)"*. The destination-shaped sentence keeps the file consistent (every section reads as a destination, not a process); the italic marker is the unambiguous signal to the planner that this section is still pending. Autosprint resolves open parameters by recording the full rationale in `adr.md`, appending a status marker at the end of the resolved section, and adding a one-line receipt to `## AI-resolved questions` at the bottom. Do not write "TBD" or invented placeholders — silent assumptions are the failure mode this rule prevents.
>
> **Section ownership.** Human-authored content lives above the `## AI-resolved questions` and `## AI-generated subgoals` headings at the bottom. Agents may append to those sections only, and may append a single status-marker blockquote at the end of a section once they've resolved that section's open question. Agents never modify the human content above those markers.
>
> **Status marker format** (used by agents when resolving an open question): `> **Status:** resolved <YYYY-MM-DD> — <one-line answer>. See ` `` `adr.md` `` ` <ADR title or date>.`
>
> **Promotion path.** When you want a resolution to graduate into the main spec, edit the original section to write the chosen answer in (replacing the prompt or "open" line), delete the status marker, and delete the receipt from `## AI-resolved questions`. The decision now reads as a normal human-authored answer; rationale stays in `adr.md` as history.

## Purpose

A long-form, source-backed analysis of the *AI bubble* question: is the current AI investment cycle (2023–2026) a bubble, and if so, along which dimensions? The endpoint is not a yes/no verdict but a set of well-reasoned scenarios — each with explicit trigger conditions and observable signposts — so the reader can update their own view as events unfold. The repo is both the deliverable and the working archive: sources, narrative, and deep dives all live as version-controlled markdown.

Also: an autosprint demo on a **non-code project**. It proves the PIT loop is useful for incremental, sourced, multi-document research, not just software. Every sprint adds at least one of: a new source, a refined subsection, a sharpened argument, or a tightened scenario.

## Users

Someone forming their own view on whether the current AI investment cycle is sustainable, well-priced, or overheated — for decisions on the 1–10 year horizon (career, savings allocation, founding direction, strategic bets at work). Not an active trader (no real-time signals, no buy/sell calls).

Also: anyone evaluating autosprint on long-form research workflows where each sprint produces visible written progress rather than running tests turning from red to green.

## Desired behaviour

The repo's final state is **three markdown documents** under `docs/`, version-controlled and cross-referenced. In priority order:

1. **`docs/sources.md` — the source ledger.** Every paper, article, dataset, transcript, interview, or financial filing the analysis draws on. Each entry has: a stable identifier (DOI / archive.org snapshot URL / SEC filing reference), author, publication date, a short tag describing the claim or data point it supports, and a quality rating (primary data / secondary analysis / opinion / industry-rumour). Sortable by date and by topic-tag. Bare unstable URLs are not accepted — archive snapshots are preferred for anything not on a doi.org / arxiv.org / SEC.gov-style stable host.

2. **`docs/paper.md` — the research-paper-style synthesis.** Human-readable, ~6 000–10 000 words, structured like a longform essay: framing of the question, evidence base, the multiple scenarios with trigger conditions, conclusion. Every substantive claim points to a tagged entry in `sources.md` via inline cite links. Each section is tight — no filler. Where evidence is genuinely thin, the text says so rather than smoothing it over.

3. **Deep-dive material under `docs/` — exhaustive pro/con discussion.** For every subsection in `paper.md` that compresses an argument, there is a deep-dive somewhere under `docs/` with the full argument tree: the steelman on each side (no strawmen — even the "weaker" position gets its best argument), where the disagreement is about facts vs about values, and what evidence would change the answer. Long-form by design — typically 500–2 000 words per topic.

   **Layout is autosprint's call**, decided as the work uncovers what's actually substantive enough to deserve its own treatment. A single `docs/deep_dives.md`, a folder like `docs/deep_dives/` with one file per topic, or some hybrid are all acceptable — whichever keeps the material navigable for the reader as it grows. The destination does not pre-commit; the planner picks the layout that fits what's been found, and the topic names are autosprint's call too. `paper.md` is the synthesis; the deep-dive material is where the work that justifies that synthesis lives.

Topic scope for the paper is set by the question itself ("is this a bubble, along which dimensions, under which conditions"), not by a hand-picked list in this file. **Autosprint determines the actual topic decomposition and naming** as the research uncovers what's load-bearing on the conclusion. To save the planner from cold-starting, the kinds of topics an honest review usually touches include: capital flows vs realised revenue, comparisons to historical bubbles (dotcom, telecom, railroads, Japanese 1980s, tulip), capacity / power / chip supply, single-vendor concentration, circular financing patterns, realised productivity gains, geopolitical exposure, and the AGI-pull thesis. Treat these as priming, not as a binding contract — drop, merge, rename, or extend them once the evidence has a say.

The conclusion sketches **multiple weighted scenarios**, not a point prediction. Each scenario is named, described in 2–3 sentences, and tagged with autosprint's calibrated probability estimate. Starting set (autosprint may refine):

- **Soft landing.** Revenue catches up over 3–5 years; equity multiples contract ~30 %; capex digests but doesn't collapse.
- **Hard bust.** Nvidia datacenter revenue stalls within one fiscal year; hyperscaler capex cut in 2026; broad AI-exposed equity drawdown 50 %+.
- **Slow bleed.** Multi-year sideways. Productivity gains accrete. Normalisation without dramatic crash.
- **Not a bubble.** Current valuations roughly correct; AGI-pull justifies spend; productive use cases broaden faster than skeptics modelled.
- **Bifurcated.** Bubble in infra layer (overbuilt capex, chip glut, datacentre oversupply); not a bubble in application layer (real revenue, real users).

Each scenario specifies:

1. **A probability in %** — autosprint's calibrated best estimate of how likely the scenario is to play out within a 2–3 year horizon. Rounded to 5 %-intervals to avoid false precision; the % across all named scenarios sums to ~100 %. A short paragraph next to the number states how it was arrived at and the chief sources of uncertainty — the reader can disagree and adjust.
2. Trigger conditions — concrete events that, if they happen, make this scenario substantially more likely.
3. Signposts to watch — specific metrics or news the reader can monitor as the story unfolds.
4. The likely 2–3 year market shape if the scenario plays out.

## Out of scope

- Buy / sell recommendations or position-sizing advice — actions, not analysis. The output is the analysis (with probability estimates on scenarios); the reader does their own thing with it.
- Real-time market commentary or any feature that depends on the project being updated weekly. Refresh cadence is quarterly at most.
- Hosting on the public web, newsletter delivery, RSS, or any distribution mechanism. This stays a local markdown archive.
- Translation. English only.
- Charts, images, or interactive widgets. Markdown text + tables only.

## Output-quality invariants

- **Cited claims.** Every substantive claim in `paper.md` and `deep_dives.md` resolves to an entry in `sources.md`. A paragraph that asserts a fact without an inline cite is a defect.
- **Stable links.** `sources.md` entries link to doi.org / arxiv.org / archive.org / SEC.gov / Wayback-machine snapshots. Bare links to news sites are flagged.
- **Honest about thin evidence.** Where the evidence base is weak, the text says so. No smoothing over uncertainty.
- **Quotes vs paraphrases.** Direct quotes are in quote-marks with cite; paraphrases are marked as paraphrases.
- **Steelmen, not strawmen.** Pro/con sections in `deep_dives.md` give the strongest version of each side. The author being skeptical of a position doesn't licence a weak rendering of it.
- **No filler.** A subsection with nothing new to say gets cut.
- **Freshness markers.** Each doc has a `_Last revised: YYYY-MM-DD_` line at the top so the reader knows whether a section has been recently re-touched.
- **GitHub-renderable.** All three docs render cleanly in GitHub's markdown preview (no broken anchor links, no truncated tables, no rendering errors).

## Verification strategy

Each sprint must satisfy the gate checks below (implemented as plain Python scripts under `tests/`, runnable via `pytest`):

- **Reference resolution.** Every `[sources.md#tag]` link in `paper.md` and in any deep-dive file resolves to an existing source entry.
- **Probability calibration.** Probabilities are stated as honest estimates, not point predictions — each scenario's `%` is paired with a short uncertainty-paragraph explaining how it was arrived at and what would shift it most. Bare numbers without that paragraph are flagged.
- **Citation density.** No paragraph in `paper.md` longer than ~80 words contains zero source links. Flagged paragraphs are listed in the test failure with line numbers.
- **Scenario completeness.** Each scenario named in `paper.md` has all four of: probability % (rounded to 5 %-intervals), trigger conditions, signposts, 2–3 year market shape. The probabilities across all named scenarios sum to within 95–105 % (rounding slack).
- **Source schema.** Every `sources.md` entry has the required fields (identifier, author, date, tag, quality rating).
- **Stale link sweep.** A periodic (manually-triggered) check that flags `sources.md` entries whose URL no longer resolves — does NOT auto-update; the user reviews.
- **Word-count band.** `paper.md` between 6 000 and 12 000 words; tests warn (not fail) when out of band.

Beyond the automated gates, a periodic full-paper read for narrative coherence is part of the Implement phase's responsibility — patchwork additions that read fine in isolation but break the flow get cleaned up in the same sprint they're introduced.

## AI-resolved questions

_(autosprint appends here as it answers open questions during sprints.)_

## AI-generated subgoals

_(autosprint appends product/behavioural subgoals it proposes during planning here.)_
