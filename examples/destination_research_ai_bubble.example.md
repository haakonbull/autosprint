# Destination

*Target-state specification for this project. What + why, in plain language. Technical decisions live in XXXXXXXX. Last reviewed: YYYY-MM-DD by <user>.*

*Recommended autosprint config for this template (set in XXXXXXXXXXXXXXXXXXXXXXXX): XXXXXXXXXXXXXXXXXXXXXXXXXXX, XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX. Copilot-only setups: XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX, XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX.*

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
> **Status marker format** (used by agents when resolving an open question): `> **Status:** resolved <YYYY-MM-DD> — <one-line answer>. See` `` `adr.md` `` `<ADR title or date>.`
>
> **Promotion path.** When you want a resolution to graduate into the main spec, edit the original section to write the chosen answer in (replacing the prompt or "open" line), delete the status marker, and delete the receipt from `## AI-resolved questions`. The decision now reads as a normal human-authored answer; rationale stays in `adr.md` as history.

## Purpose

A long-form, source-backed analysis of one question: **what happens to AI stocks during the remainder of 2026?** The horizon ends at **31 December 2026** — nothing beyond that date is in scope. The endpoint is not a point prediction but a small set (max 5) of well-reasoned scenarios — each with an estimated probability, explicit trigger conditions, and observable signposts — describing what the analysis concludes are the most likely courses of events, so the reader can update their own view as 2026 unfolds.

**The headline deliverable is `results/paper.pdf`** — a publication-quality research paper a human reads end to end. Everything else in the repo (the source ledger, the claims tree, the ACH matrix, the deep-dives) is the audit trail that lets a skeptical reader drill into any number in the paper and see exactly what it stands on. The repo is both the deliverable and the working archive: all of it lives as version-controlled markdown.

Also: an autosprint demo on a **non-code project**. It proves the PIT loop is useful for incremental, sourced, multi-document research, not just software. Every sprint moves the claims tree or the prose built on it: a new source, a new or split claim, added counter-evidence, a re-weighed credence with logged rationale, a sharpened argument, or a tightened scenario. Polishing prose is a sprint task only when a gate or invariant demands it.

## Users

Someone forming their own view on where AI-exposed equities are heading over the rest of 2026 — for decisions they need to make this year (savings allocation, rebalancing, strategic bets at work). Not an active trader (no real-time signals, no personalised trade calls), and not someone asking about the 5–10 year endgame — the paper stops at 31 December 2026.

Also: anyone evaluating autosprint on long-form research workflows where each sprint produces visible written progress rather than running tests turning from red to green.

## Desired behaviour

**Everything autosprint produces lives under `results/`** (a `docs/` folder is not used — that convention is legacy). The repo's final state is **a set of cross-referenced markdown documents plus a rendered PDF, all under `results/`**, version-controlled. In priority order:

1. **`results/sources.md` — the source ledger.** Every paper, article, dataset, transcript, interview, or financial filing the analysis draws on. Each entry has: a stable identifier (DOI / archive.org snapshot URL / SEC filing reference), author, publication date, a short tag describing the claim or data point it supports, and a quality rating (primary data / secondary analysis / opinion / industry-rumour). Sortable by date and by topic-tag. Bare unstable URLs are not accepted — archive snapshots are preferred for anything not on a doi.org / arxiv.org / SEC.gov-style stable host.

2. **`results/claims/` — the claims tree. The analytical foundation everything else is built on.** One markdown file per claim, with a stable ID (`C-001`, `C-002`, …). Each claim file contains: the claim as a single declarative sentence; a **credence in %** with a short written rationale; links to its parent claim and child claims; **evidence for** (each item citing `sources.md`); **evidence against** (each item citing `sources.md`) — or the explicit marker *"No counter-evidence found despite searching."*; a **steelman** of the strongest opposing reading; and **cruxes** — the concrete observations during 2026 that would materially change this credence. Maximum tree depth is 3 (root claims → sub-claims → sub-sub-claims); deeper nesting is false precision. The tree is an **audit structure, not a calculator**: a parent's credence is set judgmentally with a written reconciliation against its children, never by multiplying probabilities upward — correlated evidence makes mechanical propagation double-count. Each non-leaf claim states its **decomposition logic**: whether its children are *jointly sufficient* (if all children hold, the parent very likely holds — and the file names the residual assumptions that remain outside the children), *independently sufficient* (any one child alone carries the parent), or *supporting evidence* (children raise the credence without entailing it). The reconciliation paragraph names the **weakest load-bearing child**: a parent credence sitting well above a child it depends on requires explicit justification, not silence.

3. **`results/ach.md` — the diagnosticity matrix** (Analysis of Competing Hypotheses). Key evidence items × the named scenarios, marking for each cell whether the evidence is consistent, inconsistent, or neutral — and flagging which evidence actually *discriminates* between scenarios. Evidence consistent with every scenario is non-diagnostic and is labelled as such; research effort goes toward finding diagnostic evidence and toward *refuting* scenarios, not confirming them.

4. **`results/paper.md` — the research-paper-style synthesis.** Human-readable, ~4 000–8 000 words, structured like a journal article: abstract, framing of the question, evidence base, the scenarios with probabilities and trigger conditions, conclusion. **The headline answer is stated up front in plain language:** the abstract gives the scenario probabilities in one or two plain sentences, and the paper opens with a summary table — one row per scenario with its name, a one-line plain-English description, and its probability in % — fully readable on its own. No claim IDs (`C-003`), ACH references, or other internal notation in the abstract or that table; a reader who sees nothing but the first page leaves knowing the numbers. The title block and abstract state the **as-of date** — the evidence cut-off the analysis reflects; all probabilities read as conditional on information through that date. Every substantive claim points to a tagged entry in `sources.md` via inline cite links, and the scenario section is built on the claims tree (see the scenario spec below). Each section is tight — no filler. Where evidence is genuinely thin, the text says so rather than smoothing it over.

5. **Deep-dive material under `results/` — exhaustive pro/con discussion.** For every subsection in `paper.md` that compresses an argument, there is a deep-dive somewhere under `results/` with the full argument tree: the steelman on each side (no strawmen — even the "weaker" position gets its best argument), where the disagreement is about facts vs about values, and what evidence would change the answer. Long-form by design — typically 500–2 000 words per topic.

   **Layout is autosprint's call**, decided as the work uncovers what's actually substantive enough to deserve its own treatment. A single `results/deep_dives.md`, a folder like `results/deep_dives/` with one file per topic, or some hybrid are all acceptable — whichever keeps the material navigable for the reader as it grows. The destination does not pre-commit; the planner picks the layout that fits what's been found, and the topic names are autosprint's call too. `paper.md` is the synthesis; the deep-dive material is where the work that justifies that synthesis lives.

6. **`results/paper.pdf` — the rendered paper.** A PDF mirror of `paper.md`, built from it by a single documented command and never hand-edited. Its visual format follows the **LaTeXTemplates.com "Journal Article" v2.0** template kept at `autosprint/examples/research_paper_assets/LaTeXTemplates_journal-article_v2.0/` (a reference input, seeded by `autosprint init`) — it must read like a published journal article, not a markdown file dumped to PDF. Concretely, matching that template means: A4 page; a journal-style **title block** (title, and where applicable author/date) carrying a one-line provenance note — *produced autonomously by AI agents (autosprint)* — and a *not investment advice* disclaimer; a **full-width abstract** spanning the top of the first page; a **two-column body** with numbered sections and subsections; captioned, numbered tables; a running head/footer; and a **formatted bibliography** generated from `sources.md`. The markdown stays the source of truth; the PDF is its publication-quality face, regenerated whenever the source changes. A proven reference build script ships alongside the template at `autosprint/examples/research_paper_assets/build_pdf.py` (pandoc → LaTeX → tectonic, Libertinus OpenType typography; tectonic and the fonts self-bootstrap into a gitignored `.tools/`, pandoc must be installed) — adopt it (typically copied to `scripts/build_pdf.py`) or replace it, recording the choice in `adr.md`. The reference script reads optional `_Subtitle: ..._` and `_Keywords: ..._` lines near the top of `paper.md` into the title block and abstract, and a pandoc-style `Table: ...` caption line after a pipe table becomes the table float's caption — `paper.md` carries these so the rendered title page and tables look finished. Whether the built PDF is committed to the repo is autosprint's call, recorded in `adr.md`.

Topic scope for the paper is set by the question itself ("what happens to AI stocks by 31 December 2026, and what would tell us early"), not by a hand-picked list in this file. **Autosprint determines the actual topic decomposition and naming** as the research uncovers what's load-bearing on the conclusion. To save the planner from cold-starting, the kinds of drivers an honest 2026 outlook usually touches include: hyperscaler capex guidance and earnings prints landing during 2026, Nvidia/semiconductor revenue trajectory, AI application-layer revenue growth, rate and macro backdrop, chip supply and export-control news, and market concentration in AI-exposed mega-caps. Treat these as priming, not as a binding contract — drop, merge, rename, or extend them once the evidence has a say.

The core of the paper is **a small set of weighted scenarios — no more than 5** — describing the most likely courses of events for AI-exposed equities through 31 December 2026. Naming and carving up the outcome space is autosprint's call; the scenarios must be mutually exclusive and together cover the realistic outcome space (e.g. *continued melt-up: 35 %, sideways digestion: 40 %, sharp correction: 20 %, broad bust: 5 %* — illustration only, not a binding set). **Exclusivity holds by construction, not by promise:** each scenario is defined over an observable end-2026 state — e.g. non-overlapping bands for the level or drawdown of a named AI-exposed basket — so no outcome can land in two scenarios at once. Narrative paths may share elements; the end-states may not. The chosen carve-up (which basket, which bands, why this decomposition over the alternatives considered) is recorded in `adr.md`. The paper also states the **resolution rule** — the exact data source and reading date (e.g. the closing level of the named basket on the last trading day of 2026) — so that after 31 December 2026 each scenario can be scored objectively right or wrong.

Each scenario specifies:

1. **A probability in %** — autosprint's calibrated best estimate of how likely the scenario is to play out by 31 December 2026. Rounded to 5 %-intervals to avoid false precision; the % across all named scenarios sums to ~100 %. A short paragraph next to the number states how it was arrived at and the chief sources of uncertainty — the reader can disagree and adjust.
2. **An outside-view base rate first.** Before any inside-view argument, a short cited paragraph stating how often comparable episodes (prior infrastructure investment cycles, prior valuation corrections) resolved this way within a one-year window. The inside view then argues why this time deviates from the base rate, or doesn't.
3. **Claim linkage.** The claim IDs from `results/claims/` whose credences drive this scenario's probability, plus a short reconciliation paragraph (e.g. *"given C-003 at 70 % and C-011 at 40 %, this scenario sits at 25 % because …"*). The reconciliation is judgmental, not arithmetic — but it must name the claims it leans on.
4. Trigger conditions — concrete events during 2026 that, if they happen, make this scenario substantially more likely.
5. Signposts to watch — specific metrics, prints, or news during 2026 the reader can monitor as the story unfolds.
6. The likely state of AI-exposed equities at end-2026 if the scenario plays out.

## Out of scope

- Personalised investment advice — position sizing, timing calls, or recommendations tuned to a specific portfolio or risk tolerance. The paper *should* take a clear stance: per-scenario positioning implications (which exposures are most sensitive, what a holder of AI-heavy equities would watch before acting) and a probability-weighted overall read in the conclusion are in scope. What stays out is pretending to know the reader's situation.
- Anything beyond 31 December 2026. No long-run AGI timelines, no 5-year valuation models, no "eventually" arguments — evidence and scenarios only matter insofar as they bear on where AI stocks stand at end-2026.
- Real-time market commentary or any feature that depends on the project being updated weekly. Refresh cadence is quarterly at most.
- Hosting on the public web, newsletter delivery, RSS, or any distribution mechanism. This stays a local archive — markdown sources plus a locally-built PDF, no web hosting or feeds.
- Translation. English only.
- Charts, images, or interactive widgets. Markdown text + tables only.

## Output-quality invariants

- **Cited claims.** Every substantive claim in `paper.md`, the claim files, and the deep-dive material resolves to an entry in `sources.md`. A paragraph that asserts a fact without an inline cite is a defect.
- **Counter-evidence everywhere.** Every claim file's evidence-against section is either populated (cited) or carries the explicit *"No counter-evidence found despite searching."* marker. A claim with only supporting evidence is a defect, not a strong claim.
- **Credence changes are logged.** Re-weighing a claim's % requires a dated one-line entry in `autosprint/changelog.md` with the rationale and the triggering evidence. Silent drift is a defect.
- **Stable links.** `sources.md` entries link to doi.org / arxiv.org / archive.org / SEC.gov / Wayback-machine snapshots. Bare links to news sites are flagged.
- **Honest about thin evidence.** Where the evidence base is weak, the text says so. No smoothing over uncertainty.
- **Quotes vs paraphrases.** Direct quotes are in quote-marks with cite; paraphrases are marked as paraphrases.
- **Steelmen, not strawmen.** Pro/con sections in the deep-dive material give the strongest version of each side. The author being skeptical of a position doesn't licence a weak rendering of it.
- **No filler.** A subsection with nothing new to say gets cut.
- **Human-readable throughout.** The claim files and the ACH matrix are written prose under markdown headings — short, well-written notes a human skims in a minute — not key-value dumps, JSON blobs, or wall-to-wall tables. The schema disciplines the *content*; the *form* stays readable.
- **Freshness markers.** Each doc has a `_Last revised: YYYY-MM-DD_` line at the top so the reader knows whether a section has been recently re-touched.
- **GitHub-renderable.** All markdown documents render cleanly in GitHub's markdown preview (no broken anchor links, no truncated tables, no rendering errors).
- **Publication-quality PDF.** `results/paper.pdf` builds from the markdown with one documented command and reads like a professional academic paper — title block, abstract, numbered sections, typeset body, formatted reference list. Visibly amateur output (broken tables, clipped text, raw markdown artefacts, missing references) or a build that errors is a defect.

## Verification strategy

Each sprint must satisfy the gate checks below (implemented as plain Python scripts under `tests/`, runnable via `pytest`):

- **Reference resolution.** Every `[sources.md#tag]` link in `paper.md`, in any claim file, and in any deep-dive file resolves to an existing source entry.
- **Claim schema.** Every file in `results/claims/` parses against the schema: one-sentence claim, credence % with rationale, parent/child links, evidence-for, evidence-against (or the explicit none-found marker), steelman, cruxes, `_Last revised:_` line — and, for non-leaf claims, a stated decomposition logic (jointly sufficient / independently sufficient / supporting evidence).
- **Tree integrity.** No orphan claims (every non-root claim is listed as a child by exactly one parent), no cycles, depth ≤ 3, and every claim-ID reference in `paper.md` and `ach.md` resolves to an existing claim file.
- **Staleness sweep.** Claim files whose `_Last revised:_` date is more than ~15 sprints old are listed as warnings — the standing re-audit queue that keeps long runs pointed at real work.
- **Probability calibration.** Probabilities are stated as honest estimates, not point predictions — each scenario's `%` is paired with a short uncertainty-paragraph explaining how it was arrived at and what would shift it most. Bare numbers without that paragraph are flagged.
- **Scenario-claim linkage.** Each scenario lists at least one claim ID and has a reconciliation paragraph; all listed IDs resolve.
- **Citation density.** No paragraph in `paper.md` longer than ~80 words contains zero source links. Flagged paragraphs are listed in the test failure with line numbers.
- **Scenario completeness.** `paper.md` names at most 5 scenarios, and each has all six elements of the scenario spec (probability %, base rate, claim linkage, triggers, signposts, end-2026 market shape). The probabilities across all named scenarios sum to within 95–105 % (rounding slack).
- **Source schema.** Every `sources.md` entry has the required fields (identifier, author, date, tag, quality rating).
- **Stale link sweep.** A periodic (manually-triggered) check that flags `sources.md` entries whose URL no longer resolves — does NOT auto-update; the user reviews.
- **Word-count band.** `paper.md` between 4 000 and 8 000 words (same band as the paper spec above); tests warn (not fail) when out of band. Claim files and deep-dives have no word budget — they are the working layer.
- **PDF build.** `results/paper.pdf` builds from `paper.md` via the documented command without error — the gate runs the build and fails on a non-zero exit (skip, not fail, when pandoc is absent on the machine). Visual polish (does it read like a proper academic paper) is checked by human read, not asserted in code.

Beyond the automated gates, a periodic full-paper read for narrative coherence is part of the Implement phase's responsibility — patchwork additions that read fine in isolation but break the flow get cleaned up in the same sprint they're introduced.

## AI-resolved questions

## (autosprint appends here as it answers open questions during sprints.)

## AI-generated subgoals

## (autosprint appends product/behavioural subgoals it proposes during planning here.)
