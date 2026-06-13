# Destination

_Target-state specification for this project. What + why, in plain language. Technical decisions live in `adr.md`. Last reviewed: YYYY-MM-DD by <user>._

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

> **Every parameter has a concrete answer or is explicitly open.** If you know the answer, write it directly under the section, replacing the italic prompt. If you don't, write a **destination-shaped sentence** describing what the repo will have once the question is answered, followed by an explicit `*(Open — autosprint to decide.)*` italic marker. Example for an unanswered Test strategy: _"We have a well thought-through testing strategy with a clear rationale specified in XXXXXXXX. (Open — autosprint to decide.)"_. The destination-shaped sentence keeps the file consistent (every section reads as a destination, not a process); the italic marker is the unambiguous signal to the planner that this section is still pending. Autosprint resolves open parameters by recording the full rationale in `adr.md`, appending a status marker at the end of the resolved section, and adding a one-line receipt to `## AI-resolved questions` at the bottom. Do not write "TBD" or invented placeholders — silent assumptions are the failure mode this rule prevents.
>
> **Section ownership.** Human-authored content lives above the `## AI-resolved questions` and `## AI-generated subgoals` headings at the bottom. Agents may append to those sections only, and may append a single status-marker blockquote at the end of a section once they've resolved that section's open question. Agents never modify the human content above those markers.
>
> **Status marker format** (used by agents when resolving an open question): `> **Status:** resolved <YYYY-MM-DD> — <one-line answer>. See` `` `adr.md` `` `<ADR title or date>.`
>
> **Promotion path.** When you want a resolution to graduate into the main spec, edit the original section to write the chosen answer in (replacing the prompt or "open" line), delete the status marker, and delete the receipt from `## AI-resolved questions`. The decision now reads as a normal human-authored answer; rationale stays in `adr.md` as history.
>
> **For non-code projects.** This template ships with code-project defaults — "Project shape" assumes a `src/` Python layout and "Code-quality invariants" reads as code rules. For research, writing, or other artifact-deliverable projects: skip "Project shape" entirely, and treat "Code-quality invariants" as artifact-quality invariants (citation rules, source-quality bar, scope discipline, freshness markers — see `destination_research_ai_bubble.example.md` for the shape). "Test strategy" similarly becomes verification strategy (citation density, scenario completeness, source schema instead of unit/integration). The remaining sections work as-is for both project types.

## Purpose

_What does this project exist to do, and why? 1–3 sentences in plain language. Names the problem and the solution._

## Users

_Who uses it, and what do they get out of it? Names a user type and what they want from this. Even a one-person tool has a user (often you) — name them and what they want._

## Desired behavior

_In priority order (most important first), what does the system do from a user's perspective? Numbered list of user-visible behaviors. Concrete: "accepts a URL and returns a JSON summary in under 2 seconds" beats "is fast and useful". This is also where API surface, CLI surface, UX, and accessibility expectations land — they're all forms of "what the user sees"._

1. _Replace with the most important user-visible behavior._
2. _Replace with the next._

## Non-goals

_What does the project deliberately NOT do? Concrete items the planner should refuse to drift into. Examples: "won't support Windows", "won't add a web UI", "won't optimise for users of type Y"._

## Non-negotiable constraints

_Hard limits the planner must never trade away. Every constraint has a number or a clear yes/no — soft phrasing is rejected. Examples: "cost ≤ $1/sprint", "no third-party calls in test suite", "must run offline"._

## Target platform / deployment shape

_One sentence: where does this run, and what's the output shape? Examples: "CLI tool on developer laptops", "HTTP service on Linux", "Python library others import", "GitHub Action"._

## Referenced inputs

_Optional. Working artifacts under `autosprint/inputs/` that the destination depends on. **`destination.md` is authoritative** — if any artifact below contradicts what this file says, the artifact is wrong and gets updated, not this file. Each reference says (a) what the doc is, (b) where it lives, (c) what its status is, and (d) the rule for using it._

_Examples (delete or replace):_

- _**Data model** — `inputs/data_model.md`. The destination must be consistent with the schema described there. Half-finished; treat unfilled sections as open questions._
- _**Domain glossary** — `inputs/glossary.md`. Use these terms as defined; if you encounter a domain term not in the glossary, surface it as an open question rather than inventing a definition._
- _**Project background** — `inputs/project_description.md`. Why this project exists. Read once for orientation; not load-bearing per sprint._

## Project shape

_Recommended default: a src-based Python layout managed with `uv`, runnable as a module. Accept the default for normal Python projects; modify only if you have a specific reason (and record the reason as an ADR in `adr.md`). Skip the section entirely for non-Python projects._

**Folder structure:**

```text
<repo>/
├── README.md
├── pyproject.toml
├── uv.lock
├── .gitignore
├── .env.example
├── src/
│   └── <package>/
│       ├── __init__.py
│       ├── __main__.py        ← entry point for `python -m <package>`
│       ├── main.py            ← top-level orchestration
│       ├── cli.py             ← CLI argument parsing
│       ├── config.py          ← pydantic-settings config
│       └── ... (further subdirectories per architectural style)
├── tests/
│   ├── conftest.py
│   ├── unit/
│   └── integration/
└── autosprint/                ← committed by autosprint init
    ├── destination.md
    ├── inputs/
    ├── plan.md
    ├── adr.md
    ├── logs/                  ← gitignored
    └── cache/                 ← gitignored
```

Optional folders for specific project types: `data/raw/interim/processed/external/` and `notebooks/` (data-science projects), `scripts/` (one-off operational scripts), `docs/` (long-form design docs beyond what fits in `autosprint/`).

**Package manager:** `uv` — lockfile (`uv.lock`), venv management, fast resolver, single tool for the whole workflow.

**How to run:** `uv run python -m <package>` runs the app via the `__main__.py` entry point. `uv run pytest` runs tests. Specific CLI subcommands are dispatched from `cli.py`.

## Code-quality invariants

_Cross-project standards the planners and implementors should treat as permanent targets. The list below is a reasonable default — keep, edit, or delete to match your project. This is also where naming, type-safety, formatting, and dependency rules belong as one-liners (don't fragment them into separate sections)._

- **Clean code.** No dead code, no commented-out blocks, no stale comments. Names match what the code actually does.
- **Honest tests.** Every non-trivial behavior has a test that would fail if reverted. No two tests assert the same invariant on the same code path — when a behavior needs more than one test, parametrise rather than clone. Tests assert _behavior_, not snapshot state.
- **Readable README.** A new reader can get from clone to running the project in ~10 minutes. README commands actually execute.
- **Architecture reflects responsibility.** Folder structure matches concerns; no circular imports; modules have single purposes.
- **ADR hygiene.** Long-term technical decisions (library, schema, major pattern) live in `autosprint/adr.md`. Superseded entries stay in the file as history; nothing is deleted.
- **Focused commits.** One concern per commit. A commit touches only what its stated purpose requires.
- **No silent errors.** Exceptions are logged or re-raised; nothing swallows errors. Boundaries validate input; internal code trusts contracts.

## Test strategy

_What does the test suite need to prove? A few bullets covering coverage philosophy, what kind of tests dominate (unit vs integration vs e2e), and what counts as redundant. One cohesive section beats fragmenting into many sub-sections._

## Documentation quality

_What standard should the project's documentation meet? Outcome-shaped, not process-shaped. Examples: "a new reader gets clone-to-running in 10 minutes", "README commands actually execute", "every public function has a one-line docstring describing what it does"._

## Performance & cost

_Optional — include only if the system has user-visible performance constraints or a real cost ceiling. Concrete numbers only: latency, throughput, memory, cost. Examples: "p99 latency < 500ms", "monthly cost ≤ $50", "memory footprint ≤ 256MB"._

## Reliability

_Optional — include only if the system has uptime expectations or recovery requirements. Concrete numbers: availability, RTO, data-loss tolerance. Examples: "99.5% availability", "RTO ≤ 5 min", "no data loss on crash"._

## Observability

_Optional — include if the system runs unattended or others might need to debug it. What signals must be visible from the outside? Concrete examples beat category lists. Example: "every sprint outcome lands in `sprint-outcomes.log` with task title and verdict"._

## Visualization

_Optional — include only if the project produces outputs (reports, plots, dashboards, user-facing explanations). What should those outputs look like? Outcome-shaped: "matching results print as terminal tables a human can scan in 5 seconds", "monthly summary renders as a single PNG with three subplots", "audit logs are queryable via `<cli> audit`". Skip the section entirely if visualization isn't relevant to this project._

## Success criteria

_Concrete, user-visible checkpoints that signal the project is working as intended. Numbered, earlier = nearer-term. Good: "a non-coder can run one full sprint end-to-end without editing code". Bad: "implement config loader"._

1. _Replace with the first observable checkpoint._

## AI-resolved questions

This section is reserved for one-line summaries of open questions that autosprint's implementor has resolved. Only the implementor writes here, and only after the full rationale has been recorded in `autosprint/adr.md`. The original question text in the human-authored spec above stays untouched — this section is the agent's _receipt_, not a rewrite.

Each entry: `**<short tag>:** chose <answer>. See` `` `adr.md` `` `<ADR title or date>.`. Keep it terse — the rationale lives in `adr.md`.

Humans may later promote a resolution by editing the human-authored spec above (writing the chosen answer in directly), then delete the status marker from that section AND the entry from this list. Do not edit other entries directly — if a resolution turned out to be wrong, supersede it via a new `adr.md` entry rather than rewriting history here.

_No questions resolved yet._

## AI-generated subgoals

This section is reserved for product / behavioral subgoals that autosprint's planning phase has proposed. Only the planning phase should write here. Humans can audit or delete this section without touching the human-authored spec above.

Scope rules: product and behavioral goals only; no technical decisions (those go in `adr.md`).

_No AI-generated subgoals yet._
