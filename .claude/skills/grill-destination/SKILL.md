---
name: grill-destination
description: Interview the user to produce or refine autosprint/destination.md — the sole target-state input to the autosprint PIT loop. For an existing destination.md it first assesses each section against a quality rubric and recommends fixes, then walks a 3-pass interview (Foundation, Quality bar, Optional concerns). Every section gets a recommended answer and one of three outcomes — answered with a concrete answer, marked open for autosprint to decide, or skipped entirely. Use when the user says "grill me about destination", wants to write or revise destination.md, or is starting a new project.
---

Interview the user to produce a concrete `destination.md` at `<target_repo>/autosprint/destination.md`. **This file is the sole input to autosprint's planning phase** — the single fixed point the PIT loop iterates toward.

The interview walks **three passes** through the canonical concerns. At each section the user picks one of these outcomes:

- **Answer it** → user dictates the answer; you write it into `destination.md`.
- **Accept the recommended default** (only for sections that ship one) → you write the default content as-is.
- **Modify the recommended default** (only for sections that ship one) → user dictates changes; you write the modified version. If the modification carries non-trivial reasoning, flag that the change deserves an ADR entry recording the rationale.
- **Let autosprint decide** → you write a *destination-shaped sentence* with an `*(Open — autosprint to decide.)*` marker. Autosprint will resolve in a future sprint, recording rationale in `adr.md`.
- **Skip** → you don't add the section to `destination.md` at all. (Allowed only in passes 2 and 3.)

The resulting file contains only the sections the user picked into. Everything else stays out — no empty placeholders, no aspirational sections nobody intends to fill in.

## Where things live

`destination.md` is the **destination — the GPS coordinate** the PIT loop descends toward. It lives at `<target_repo>/autosprint/destination.md` and is the load-bearing input the planner reads on every sprint.

Sibling locations under `<target_repo>/autosprint/`:

- **`inputs/`** — supporting human-authored material the destination may *reference*. Half-finished data models, domain glossary, project description, design notes. Read on demand, not on every sprint. **`destination.md` is authoritative**; if a file in `inputs/` contradicts it, the file in `inputs/` is wrong and gets updated. Each file in `inputs/` is anchored by humans; agents may *append* to designated AI-append sections (e.g. `## AI-observed inconsistencies`) but never modify human content above them. Pattern: `destination.md`'s "Referenced inputs" section names which `inputs/` files matter for which kinds of tasks.
- **`adr.md`** — append-only history of technical decisions. Read by Plan and Implement on every sprint as context for what's already locked in. Both humans and the implementor agent author entries here. To change a decision, append a new entry that references the old one under `**Supersedes:**` — old entries stay in the file as history.
- **`plan.md`** — loop state authored by the planner. Not an input to the grill skill.
- **`logs/`, `cache/`** — agent-generated bookkeeping. Never touch by hand.

Plus the standard agent file:

- **`<target_repo>/CLAUDE.md`** — agent navigation context (folder layout, conventions, "we use uv not pip"). Loaded on every agent invocation. Not part of this interview, but updated by autosprint as project shape changes.

## Before starting

1. **Always re-read `destination.md` at the start of each session**, even if you've seen it earlier in conversation. The file may have been edited externally between sessions; conversation memory can be stale and lead to working from an outdated snapshot. **Follow its links:** when `destination.md` references other files — markdown links, `inputs/` files named under "Referenced inputs", `adr.md`, any path mentioned in prose — read those too. A destination is only as sound as the material it leans on, and a stale, missing, or contradicted linked file is itself a finding for Pass 0.
2. Check if `<target_repo>/autosprint/destination.md` exists.
   - If yes, load it and run in **refine mode**: start with **Pass 0 — Assess the existing destination** (below) to judge what's already there, then walk each section and the not-yet-present Pass 2 / Pass 3 sections — focusing the interview on what Pass 0 flagged as weak or missing rather than re-walking solid sections.
   - If no, choose between **fresh mode** and **mature-repo mode**:
     - **Fresh mode** — repo is greenfield (no source code, or only a stub README). Design the destination from scratch.
     - **Mature-repo mode** — repo already has substantial code (≥10 source files, real README, active commit history). Real intent already lives in the code; extract a first-draft destination from it, then validate with the user.
3. To pick fresh vs mature: list the target one level deep, count source files (`*.py`, `*.ts`, `*.go`, `*.rs`, `*.js`, etc.), check whether `README.md` has real content (>200 chars, not a placeholder), and skim recent commit messages if available. Use judgement — when in doubt, ask the user.
4. If a file at a *different* path already serves this purpose (e.g. `docs/vision.md`), ask the user before creating a duplicate.

## Mature-repo mode (extract before grilling)

When the repo has real code already, the destination is partly *latent* in what's been built. Inspect first, then validate.

1. Read inputs that reveal **intent** (what + why), in this order:
   - `README.md` — usually the closest thing to a stated purpose.
   - Top-level docstrings on the main module / package.
   - Folder structure one level deep — names like `api/`, `models/`, `cli/` reveal *shape*.
   - Public-facing entry points (CLI commands, HTTP routes, exported functions) — read their signatures and docstrings, not their bodies.
   - `requirements.txt` / `pyproject.toml` / `package.json` / `Dockerfile` — reveals current technical decisions.
   - Existing `autosprint/adr.md` if present — extracts of current technical state.
   - Recent commit messages (last 20–30) — what features have been actively added.
2. Do NOT read implementation details unless they reveal a technical decision you'll need.
3. **Draft tentative answers** for each section based on what you found. Tag each draft with its source — "(extracted from README)", "(inferred from folder names)", "(based on requirements.txt)" — so the user knows which parts to validate hardest.
4. Present each section's draft and ask the user to pick one of the three outcomes (answer / let autosprint decide / skip). The user's job is **validation and correction**, not authoring from scratch.

## Fresh mode (the original interview)

Used when the repo is greenfield. Do NOT read source code or folder structure — those would bias the user toward describing what exists instead of what they want. README + top-level docstrings are still OK as Purpose prefill. Walk the three passes asking one section at a time; the user is starting from a blank slate and benefits from focus.

## Interview style

- **Always lead with a recommendation.** Like the `grill-me` skill — for *every* section, put a concrete recommended answer on the table *before* asking, never just an open question. For sections that ship a canonical default, that's the default. For the rest, draft one from context — the repo, the Pass 0 assessment, what the user has already said — and label it clearly as your recommendation. The user then accepts it, edits it, defers it ("let autosprint decide"), or skips. A bare "what do you want for X?" is a last resort, not the default move.
- **Soft on vision** (Purpose, Users, Non-goals): some vagueness is legitimate. Don't force numbers onto feelings.
- **Hard on constraints and success criteria**: these must be concrete or the planner can't act on them. *"Should be cheap"* is worthless; *"must cost under $1/sprint"* is actionable.
- **Break vagueness with pick-from-options**: when the user gives a vague answer, propose 2–3 concrete versions and ask them to pick or edit. Don't ask "can you be more specific?" — it produces more vagueness.
- **Always offer the fork at each section**, even when the user is engaged with answering — they may want to defer parts to autosprint or accept a default.

## The fork at each section

At each section in the interview, after the question is posed and (in mature-repo / refine mode) the draft proposed, present the available options. Some sections ship a **recommended default** (an opinionated starting answer for settled best-practice cases); others don't. The fork shape depends on which:

**Sections that ship a recommended default** (Project shape, Code-quality invariants, and any other section whose default appears in the canonical example file):

1. **Accept the default** — you write the default content as-is into `destination.md`. Frictionless for the common case.
2. **Modify the default** — user dictates changes; you write the modified version. **If the modification carries non-trivial reasoning** (e.g. *"we don't use uv because we're locked to Poetry by another team"*), tell the user: *"I'll record that reasoning as an ADR — it's load-bearing context the planner should see."* Then both write the modified content into `destination.md` AND draft a brief ADR entry (Decision / Why / Alternatives considered) to append to `adr.md`. Trivial cosmetic tweaks (renaming a folder, adding a sub-bullet) don't need an ADR.
3. **Let autosprint decide** — see below.
4. **Skip** — Pass 2 and Pass 3 only.

**Sections that don't ship a default** (Purpose, Users, Desired behavior, Non-goals, Constraints, Target platform, Success criteria, and most Pass 3 sections):

1. **Accept or edit my recommended answer** — you've already put a concrete recommendation on the table (see "Always lead with a recommendation"); the user accepts it as-is or dictates edits, and you write the result into `destination.md`, replacing the italic prompt. A user who prefers to author from scratch still can — but the recommendation goes on the table first.
2. **Let autosprint decide** — see below.
3. **Skip** — Pass 2 and Pass 3 only.

**The "let autosprint decide" path** (both fork shapes): prompt the user for a **one-sentence framing of the question** before generating the destination-shaped sentence — autosprint needs enough context to actually decide, and a vague open marker produces mush. Example exchange: *"OK, autosprint decides. Briefly: what's the question? E.g. 'we don't know whether unit-heavy or integration-heavy fits this project best'."* Then incorporate that framing into the destination sentence.

### Destination-shaped phrasing template

When writing the "let autosprint decide" outcome, use the pattern:

> *"We have a [adjective] [topic noun] with [a clear rationale | the rationale | the reasoning] documented in `adr.md`. (Open — autosprint to decide: [user's framing].)"*

Adjectives: *well-thought-through, well-considered, sound, sensible, appropriate, clear, coherent.*

Examples:

- Test strategy: *"We have a well-thought-through testing strategy with a clear rationale documented in `adr.md`. (Open — autosprint to decide: unit-heavy vs integration-heavy fit for our domain.)"*
- Performance & cost: *"We have a sensible performance and cost target with the rationale documented in `adr.md`. (Open — autosprint to decide: what latency budget realistic users will tolerate.)"*
- Auth model: *"We have an appropriate auth model for our needs with the rationale documented in `adr.md`. (Open — autosprint to decide: bearer-token vs session-cookie for our integration shape.)"*

The italic `(Open — ...)` suffix is the unambiguous signal to the planner that this section is still pending and needs resolution.

## Pass 0 — Assess the existing destination (refine mode only)

Before re-interviewing, judge what's already in `destination.md`. Fresh mode and mature-repo mode skip this — there is nothing to assess yet.

1. Read `destination.md` in full, plus every file it links to (see "Before starting" step 1). Also read `adr.md` and any `inputs/` files it names — the destination must not contradict them.
2. Judge each section against the rubric below. A section is **solid**, **weak** (a concrete fix is warranted), or — for an expected section that is not present — **missing**.
3. Present a compact assessment to the user: one line per section, verdict first, then a specific recommendation for each weak or missing item. Lead with the verdict, not a wall of prose:

   > **Pass 0 assessment**
   > - Purpose — solid.
   > - Desired behavior — weak: items 2 and 4 describe implementation ("uses Redis"), not user-visible behavior. Recommend rephrasing to outcomes.
   > - Non-negotiable constraints — weak: "should be fast" has no number. Recommend a concrete latency budget, or mark it open for autosprint to decide.
   > - Success criteria — missing. Recommend adding 2–3 numbered, user-visible checkpoints.
   > - Test strategy — solid.

4. Then run the interview, **focused on the weak and missing items**. Don't re-walk solid sections question-by-question — confirm them in one pass ("Purpose, Users, Test strategy look solid — leave them as-is?") and spend the interview where Pass 0 found gaps. Each weak/missing item still goes through the normal fork (accept recommendation / let autosprint decide / skip).

### Rubric — what makes a destination "good"

- **Answered or explicitly open.** Every parameter has a concrete answer *or* an `*(Open — autosprint to decide.)*` marker. No "TBD", no invented placeholders, no unreplaced italic template prompts.
- **Concrete where it must be.** Constraints and success criteria carry a number or a clear yes/no. *"Should be cheap"* is a finding; *"under $1/sprint"* passes. (Vision sections — Purpose, Users, Non-goals — may stay soft; don't force numbers onto feelings.)
- **State-shaped, not process-shaped.** Sections describe the target the repo should reach, not the journey toward it. *"Refactor the parser"* or *"add more tests"* is a process leak — flag it; the destination states the *quality* wanted, not the work to get there.
- **Behavior is user-visible.** Desired behavior describes what the system does for a user, not implementation. *"Uses FastAPI"* is a leak; *"exposes a REST API for X"* is the behavior.
- **Internally consistent.** No section contradicts another, and nothing contradicts `adr.md` or the files in `inputs/`.
- **Links resolve.** Every file `destination.md` references exists and actually says what the destination claims. A dangling or stale linked file is a finding.
- **Scoped.** Only the sections the project genuinely needs are present — no aspirational empty sections, no Pass 3 sections kept "just in case".
- **Not stale.** The dated signoff is not far behind recent commits and code reality.

## Pass 1 — Foundation (always include, no skip)

These are load-bearing for any project. Walk them in order. The user picks **answer** or **let autosprint decide** (no skip).

1. **Purpose** — why does this project exist? What problem is it solving? (Minimum to call it answered: 1 sentence.)
2. **Users** — who uses it, and what do they get out of it? (Minimum: 1 user type + 1 benefit.)
3. **Desired behavior** — in priority order, what does the system do from a user's perspective? Numbered list. If the user gives implementation ("uses FastAPI"), redirect: "that's how — what does it *do* from a user's perspective?". (Minimum: 1–5 user-visible behaviors, ordered.)
4. **Non-goals** — what does the project deliberately NOT do? (Minimum: 1 item, or explicit "nothing to exclude yet".)
5. **Non-negotiable constraints** — hard limits the planner must never trade away. Every constraint has a number or a clear yes/no. (Minimum: 1 concrete item, or explicit "no hard constraints".)
6. **Target platform / deployment shape** — where does this run, and what's the output shape? (Minimum: 1 sentence.)
7. **Success criteria** — concrete, user-visible checkpoints that signal "done". Numbered, earlier = nearer-term. (Minimum: 1 numbered criterion.)

## Pass 2 — Quality bar (almost always include; skip allowed if explicit)

These shape what good looks like. Skip only if the user explicitly opts out and gives a brief reason (so future-them remembers why this isn't here).

8. **Project shape** *(ships a recommended default)* — folder structure (src-based Python layout tree drawing), package manager (`uv`), how to run the app (`uv run python -m <package>`). Use the **accept default** path for any project that's "a normal Python project"; **modify default** when the project is unusual (e.g. flat layout because it's a small script, Poetry instead of uv, etc.) — and remember to draft an ADR for the modification rationale. **Skip** only for non-Python projects.
9. **Code-quality invariants** *(ships a recommended default)* — present the seeded defaults (Clean code, Honest tests, Readable README, Architecture reflects responsibility, ADR hygiene, Focused commits, No silent errors). Use **accept default** to keep all, **modify default** to prune/extend.
10. **Test strategy** — what does the test suite need to prove? Coverage philosophy, what kind of tests dominate, what counts as redundant. One cohesive section, not eleven sub-sections.
11. **Documentation quality** — what standard should docs meet? Outcome-shaped: *"a new reader gets clone-to-running in 10 minutes"* beats *"good docs"*.

## Pass 3 — Optional (relevance-gated, then fork)

For each section, **first ask whether it's relevant to the project**. If yes, the fork applies. If no, skip without further questions.

12. **Referenced inputs** — relevance gate: are there existing artifacts under `autosprint/inputs/` (data model draft, glossary, project description) that the destination depends on? If yes: list them with status flags and rule-for-using-it.
13. **Performance & cost** — relevance gate: does the system have user-visible performance constraints or a real cost ceiling? If yes: concrete numbers (latency, throughput, memory, monthly cost).
14. **Reliability** — relevance gate: does the system have uptime expectations or recovery requirements? If yes: numbers (availability, RTO, data-loss tolerance).
15. **Observability** — relevance gate: does the system run unattended, or might others need to debug it? If yes: what signals must be visible from outside.
16. **Data model** — relevance gate: does the project's domain have structured data with schemas worth pinning? If yes: inline (small) or reference `inputs/data_model.md` (preferred for substantive models).
17. **Visualization** — relevance gate: does the project produce outputs (reports, plots, dashboards, user-facing explanations)? If yes: how should those outputs look? Outcome-shaped — *"matching results print as terminal tables a human can scan in 5 seconds"* beats *"good visualization"*. If not: skip.
18. **Security & privacy posture** — relevance gate: does the system handle sensitive data, run multi-tenant, or face the internet? If yes: outcome-shaped statements (*"secrets never logged"*, *"PII encrypted at rest"*).
19. **Auth model** — relevance gate: is auth a first-class product surface? If yes: name the model and the boundary it enforces.
20. **Compliance readiness** — relevance gate: must the project satisfy a specific regulation (GDPR, HIPAA, SOC2)? If yes: name it; details often live in a separate compliance doc.

## Termination

Once all Pass 1 sections are answered (or marked "let autosprint decide") and the user has been walked through Pass 2 and Pass 3, stop asking. Move straight to propose-then-write. Do **not** loop back for "anything else?" — if the user wants to add more, they can do it during proposal review.

## Writing the file

**Fresh / mature-repo mode — propose then write:**
1. Print the full drafted `destination.md` in the conversation. The conceptual primer (`## How to read this document` section) is the same for every project — copy it verbatim. If `autosprint/destination.md` already exists, read it from there. Otherwise use this embedded copy:

   ```
   ## How to read this document

   A destination document describes the target shape of the repo.

   It is analogous to a destination we want to travel to. It describes where we want to go, not how to get there.
   Think of it as a GPS coordinate that a Google Maps user is navigating toward.
   In the analogy, Google Maps uses the current location and the destination to plan a route.
   In our situation, the AI assistant uses the current state of the codebase and `destination.md` to plan which tasks to do.

   The destination will typically contain information that we already know, and many things that we don't know the concrete answer to yet. When the answer is already known, the document states the decision directly. When the answer is not yet known, the destination names the topic or question, and specifies that the destination of the repo will be that we have an answer or solution that is well thought through, has a clear rationale, is consistent with the project goals, and where the rationale behind the choice is documented in `adr.md`. In that case it will be up to autosprint to determine this choice.

   The destination describes the state we want to come to. It does not describe how we get there. For instance, this document might state that we want good code quality according to best practice — it does not describe refactoring, since that is part of the journey, not the destination. Because the destination is stable while the journey is not, this document is re-read often but rewritten rarely.

   > **Role of this file.** `destination.md` is the single fixed point the PIT loop descends toward — the human-authored input the planner reads each sprint. Append-only decision history (Context / Decision / Consequences for each technical choice) lives in `autosprint/adr.md` (read on demand, not as a parallel input). Methodology narrative is autosprint's output at `docs/methodology.md` (humans review for accuracy; humans do not author into it).
   >
   > **Every parameter has a concrete answer or is explicitly open.** If you know the answer, write it directly under the relevant section. If you don't, leave the section as just the question or write *"Open — autosprint to decide and document rationale in `adr.md`"*. Autosprint resolves open parameters by recording the full rationale in `adr.md`, appending a status marker at the end of the resolved section, and adding a one-line receipt to `## AI-resolved questions` at the bottom. Do not write "TBD" or invented placeholders — silent assumptions are the failure mode this rule prevents.
   >
   > **Section ownership.**
   > - **Human-authored only:** Purpose, Users, Desired behavior, Non-goals, Non-negotiable constraints, Target platform, Code-quality invariants, Success criteria. Agents never modify these.
   > - **Read-write by both, with provenance tags:** "Technical decisions" (each entry tagged `Author: Human` or `Author: Autosprint`; human entries require explicit human approval to supersede) and "Open questions" (autosprint-surfaced questions marked `(surfaced by Autosprint)`).
   > - **Agent-append only:** `## AI-resolved questions` and `## AI-generated subgoals`, plus a single status-marker blockquote at the end of a resolved section. Default: humans don't edit. Agents only append (never modify existing entries) and never modify the human content above the markers.
   >
   > **Status marker format** (used by agents when resolving an open question): `> **Status:** resolved <YYYY-MM-DD> — <one-line answer>. See `adr.md` <ADR title or date>.`
   >
   > **Promotion path.** When you want a resolution to graduate into the main spec, edit the original section (or "Open questions" bullet) to write the chosen answer in directly, delete the status marker, and delete the receipt from `## AI-resolved questions`. The decision now reads as a normal human-authored answer; rationale stays in `adr.md` as history.
   ```
2. Ask "ready to write this to `<target_repo>/autosprint/destination.md`?".
3. Allow one round of inline edits ("change success criterion 2 to...").
4. Write the file on explicit approval.

**Refine mode — diff or full-file then write:**
1. For **light refines** (a few sections changed): show a diff against the existing file, only the changed sections.
2. For **heavy refines** (structural reorganisation, multiple new sections, section renames): show the **full assembled file** — diffs become unreadable when structure changes.
3. Ask for approval.
4. Write on approval.

In **refine mode**, never edit content under the `## AI-resolved questions` or `## AI-generated subgoals` headings — those sections are owned by autosprint's implementor and planner respectively. If a section above the bottom has a status marker (`> **Status:** resolved...`), that's also agent-authored; leave it intact unless the human is performing a *promotion* (writing the resolved answer back into the section and removing the marker).

## File template

The canonical template shape is described below. `autosprint init` seeds `<TARGET_REPO>/autosprint/destination.md` for new projects.

The shape in brief — the file has these sections in this order, but **only the sections the user picked into during the interview are kept** (Pass 1 always present; Pass 2 usually present unless explicitly skipped; Pass 3 only when relevant):

1. `# Destination` — title + one-line "last reviewed" tag.
2. `## How to read this document` — the conceptual primer (GPS analogy, state-vs-process, every-parameter-answered-or-open rule, section-ownership rule, status-marker format, promotion path). Same content for every project — copy verbatim from the canonical example file.
3. `## Purpose` (Pass 1)
4. `## Users` (Pass 1)
5. `## Desired behavior` (Pass 1)
6. `## Non-goals` (Pass 1)
7. `## Non-negotiable constraints` (Pass 1)
8. `## Target platform / deployment shape` (Pass 1)
9. `## Referenced inputs` (Pass 3 — only if relevant)
10. `## Code-quality invariants` (Pass 2)
11. `## Test strategy` (Pass 2)
12. `## Documentation quality` (Pass 2)
13. `## Performance & cost` (Pass 3 — only if relevant)
14. `## Reliability` (Pass 3 — only if relevant)
15. `## Observability` (Pass 3 — only if relevant)
16. Other Pass 3 sections (Data model, Security & privacy, Auth, Compliance) — only if relevant.
17. `## Success criteria` (Pass 1)
18. `## AI-resolved questions` — agent-append section: one-line receipts of resolutions. Always present (even when empty).
19. `## AI-generated subgoals` — agent-append section: product/behavioral subgoals proposed by the planner. Always present (even when empty).

Section format: heading + the answer (when answered) OR the destination-shaped sentence with `*(Open — autosprint to decide.)*` marker (when deferred). The italic prompts in the example file are placeholders the interview replaces.

Rules baked into the template:

- **Lives at `<target_repo>/autosprint/destination.md`** — next to `plan.md`, `adr.md`, and the `inputs/`, `logs/`, `cache/` folders. Seeded automatically by `autosprint init` from the canonical example file in autosprint's repo.
- **Filename is exactly `destination.md`** — lowercase, underscore. Matches `plan.md` convention.
- **"How to read this document" section near the top** — every destination.md gets the same primer so any reader (human or agent) immediately understands the conventions.
- **Dated signoff at top** — stale destinations are visible.
- **Numbered success criteria + numbered desired behavior** — implies priority order.
- **Every parameter has a concrete answer or is explicitly open** — destination-shaped sentence + `*(Open — autosprint to decide.)*` marker. No "TBD" placeholders. Open parameters get resolved by autosprint with the three-write protocol.
- **Section ownership** — agents may only append to `## AI-resolved questions`, `## AI-generated subgoals`, and as a single status-marker blockquote at the end of a resolved section. Agents never modify the human content above those markers.
- **AI-resolved questions and AI-generated subgoals sections at the bottom** — fixed structural invariants the implementor and planner rely on. Never remove these headings even if empty.
- **Checked into git** — it's a decided document, it belongs in history.
