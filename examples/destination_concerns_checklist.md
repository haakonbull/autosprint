# Destination concerns checklist

A walkthrough document for use **once**, when you're populating `autosprint/destination.md` for the first time (or refining it years later). It is **not** the spec itself — it's the menu you order from.

For each concern below, decide one of three things:

- **Include it AND I know the answer** → write the section + answer in `destination.md`.
- **Include it BUT the answer is open** → write a **destination-shaped sentence** in `destination.md` describing what the project will have once decided, followed by an explicit `*(Open — autosprint to decide: <one-sentence framing>.)*` italic marker. Example for an unanswered Test strategy: *"We have a well-thought-through testing strategy with a clear rationale documented in `adr.md`. (Open — autosprint to decide: unit-heavy vs integration-heavy fit for our domain.)"*. The next sprint that touches this area will produce a Decide+implement task; the implementor records the full rationale in `adr.md`, appends a status marker (`> **Status:** resolved <date> — <answer>. See adr.md <title>.`) at the end of the resolved section, and adds a one-line receipt in `## AI-resolved questions` at the bottom.
- **Not relevant for my project** → skip. Don't add the section.

**Aim for tightness.** A section that's marked "open and unlikely to be answered any time soon" is noise on every sprint. If you find yourself adding a section *because the checklist mentions it* rather than *because your project actually needs it*, drop it. The destination should fit roughly one screen of section names and re-read in under 60 seconds.

**For non-code projects** (research, writing, design — projects whose deliverable is markdown / text / artifacts rather than running code): the checklist still applies, but the concrete answers differ. "Test strategy" below becomes verification strategy (citation density, scenario completeness, source schema instead of unit / integration / e2e). "Project shape" is usually skippable — there's no `src/` layout to declare. "Code-quality invariants" becomes artifact-quality invariants (citation rules, source-quality bar, scope discipline, freshness markers). See `destination_research_ai_bubble.example.md` for a worked example of the non-code shape.

---

## Always include

These are load-bearing for any project. Skip them and the planner can't operate.

### Purpose
**Question:** What does this project exist to do, and why?
**Good answer:** 1-3 sentences in plain language. Names the problem and the solution.

### Users
**Question:** Who uses it, and what do they get out of it?
**Good answer:** Names a user type and what they want. *"Solo developer iterating on a side project"* beats *"developers"*.

### Desired behavior
**Question:** In priority order, what does the system do from a user's perspective?
**Good answer:** Numbered list of user-visible behaviors. *"Accepts a URL and returns a JSON summary in under 2 seconds"* beats *"is fast and useful"*. This is also where API quality, CLI quality, UX, and accessibility expectations land — they're all forms of "what the user sees".

### Non-goals
**Question:** What does the project deliberately NOT do?
**Good answer:** Concrete items the planner should refuse to drift into. *"Won't support Windows"*, *"won't add a web UI"*, *"won't optimise for users of type Y"*.

### Non-negotiable constraints
**Question:** What hard limits must the planner never trade away?
**Good answer:** Each constraint has a number or a clear yes/no. *"Cost ≤ $1/sprint"*, *"no third-party calls in test suite"*, *"must run offline"*. Soft phrasing is rejected.

### Target platform / deployment shape
**Question:** Where does this run, and what's the output shape?
**Good answer:** One sentence. *"CLI tool on developer laptops"*, *"HTTP service on Linux"*, *"Python library others import"*, *"GitHub Action"*.

### Success criteria
**Question:** What does "done" look like?
**Good answer:** Numbered, user-visible checkpoints. Earlier = nearer-term. *"A non-coder can run one full sprint without editing code"* beats *"implement config loader"*.

---

## Almost always include

These shape what good looks like. Without them, the planner can't pick between competing valid paths.

### Project shape *(ships a recommended default)*
**Question:** Where does the code live, what manages dependencies, and how do you run it?
**Good answer (the recommended default):** src-based Python layout with code under `src/<package>/`, `uv` as package manager, runnable via `uv run python -m <package>`. Includes a folder-structure tree drawing showing the destination shape. Use the **accept default** path for normal Python projects; **modify default** when the project genuinely needs a different shape — and remember the modification reasoning belongs in `adr.md`. **Skip** entirely for non-Python projects.

### Code-quality invariants *(ships a recommended default)*
**Question:** What permanent standards should the code respect?
**Good answer (the recommended default):** A short bulleted list. The default seed gives 7 sensible ones (clean code, honest tests, readable README, architecture reflects responsibility, ADR hygiene, focused commits, no silent errors) — accept all, or modify by pruning/extending. **This is also where naming, type-safety, formatting, and dependency-discipline rules belong** as one-liners if you have project-wide opinions on them. One bullet each, not a whole section.

### Test strategy
**Question:** What does the test suite need to prove?
**Good answer:** Three to six bullets covering coverage philosophy ("every non-trivial behavior has a test"), what kind of tests dominate (unit vs integration vs e2e), what counts as redundant ("don't clone smoke tests across modules — parametrise"), and what's off-limits ("no mocks where a real subprocess works"). Don't fragment into eleven sub-sections — one cohesive section beats a sprawl.

### Documentation quality
**Question:** What standard should docs meet?
**Good answer:** Outcome-shaped, not process-shaped. *"A new reader gets clone-to-running in 10 minutes"*. *"README commands actually execute"*. Folds onboarding and README quality together.

---

## Include when the project actually has them

### Referenced inputs
**Include when:** there are working artifacts (data model draft, glossary, project description) under `autosprint/inputs/` that the destination depends on.
**Skip when:** there's nothing to reference yet.
**Good answer:** A list of inputs/ files with status flags ("half-finished", "settled", "read-on-demand") and the rule for using each. Authority is always: *destination.md wins over the artifact*.

### Performance & cost
**Include when:** the system has user-visible performance constraints or a real cost ceiling.
**Skip when:** "fast enough" is fine and there's no budget pressure.
**Good answer:** Concrete numbers. *"p99 latency < 500ms"*, *"monthly cost ≤ $50"*, *"memory footprint ≤ 256MB"*. Folds latency, throughput, memory, cost, and scalability — they're all flavors of the same envelope.

### Reliability
**Include when:** the system has uptime expectations or recovery requirements.
**Skip when:** it's a CLI tool, a research project, or otherwise stateless and easy to restart.
**Good answer:** Numbers. *"99.5% availability"*, *"RTO ≤ 5 min"*, *"no data loss on crash"*. Folds availability, resilience, and recovery readiness — same envelope.

### Observability
**Include when:** the system runs unattended or others might need to debug it.
**Skip when:** it's a script you run by hand and read the output of.
**Good answer:** What signals must be visible from the outside. *"Every sprint outcome lands in `sprint-outcomes.log` with task title and verdict"*. Folds logging, metrics, traceability, and alerts — pick concrete examples, don't list categories.

### Visualization
**Include when:** the project produces outputs (reports, plots, dashboards, user-facing explanations).
**Skip when:** the project is a pure backend service or library with no human-facing output.
**Good answer:** Outcome-shaped statements about what those outputs look like. *"Matching results print as terminal tables a human can scan in 5 seconds"*. *"Monthly summary renders as a single PNG with three subplots"*. *"Audit logs queryable via `<cli> audit`"*.

### Data model
**Include when:** the project's domain has structured data with schemas worth pinning down.
**Skip when:** the project is mostly stateless or operates on free-form input.
**Good answer:** Either *inline* (a few fields with constraints — only if the model is small) OR a *reference* to `inputs/data_model.md` (preferred for anything substantive). Don't try to cover both. Folds schemas, data flow, state management.

### Security & privacy posture
**Include when:** the system handles sensitive data, runs in a multi-tenant setting, or is exposed to the internet.
**Skip when:** it's a single-user dev tool with no secrets and no PII.
**Good answer:** Outcome-shaped statements. *"Secrets never logged"*, *"PII encrypted at rest"*, *"no external network calls in test suite"*. Folds secret safety and privacy.

### Auth model
**Include when:** auth is a first-class product surface (login flow, API tokens, RBAC).
**Skip when:** there's no auth.
**Good answer:** Names the model and the boundary it enforces. *"Bearer-token auth on /api/*; tokens scoped per-user, no admin scope"*. Specific implementation goes in adr.md.

### Compliance readiness
**Include when:** the project must satisfy a specific regulation (GDPR, HIPAA, SOC2, etc.).
**Skip when:** there's no regulatory requirement — most projects don't have one.

---

## Almost never include in destination.md

These come up often when brainstorming, but they belong elsewhere.

### Architecture, module boundaries, interfaces, contracts
**Where they go:** ADRs as decisions are made. The destination spec doesn't need to pre-declare structure — it emerges from the work and gets pinned in `adr.md` once decided. If you write it here ahead of time, agents will refactor toward your preconception even when the work reveals a better shape.

### Folder structure, naming, coding style, formatting, linting, type safety
**Where they go:** `CLAUDE.md` (concrete conventions agents see on every read) or as one-line bullets under Code-quality invariants. They're rules, not destinations.

### Branching model, PR quality, review quality, merge safety, commit history
**Where they go:** `CONTRIBUTING.md` or a team handbook. These are team workflow, not product spec.

### CI quality, CD quality, build reproducibility, deployability, release readiness
**Where they go:** CI config + ops/runbook. The destination spec shouldn't carry deployment process detail; it should specify what "done" looks like (which is in Success criteria).

### Language fit, runtime compatibility, package management, import discipline
**Where they go:** ADRs (the actual tech choices, with rationale). One bullet under Code-quality invariants if there's a general rule like *"external deps are pinned and minimal"*.

### Ownership, governance, ADR coverage, rationale quality
**Where they go:** `README.md` or team docs. The "Code-quality invariants" entry on ADR hygiene already covers the meta-rule that decisions get recorded.

### Migration safety, recovery readiness, error model, exception safety
**Reformulate when relevant:** these are *process* concerns ("how do migrations work?") that have *state* equivalents ("RTO ≤ 5 min", "errors surface with breadcrumb context"). Put the state form under Reliability or Observability. Drop the abstract heading.

### Test data quality, fixture quality, mock quality, synthetic data quality, property test relevance, regression safety, test coverage
**Where they go:** Fold into Test strategy as a few bullets. Eleven sub-sections is overkill; one cohesive section is right.

### Feature flag model, environment model
**Where they go:** ADRs if there's a real decision worth recording. Drop otherwise.

### Reproducibility, portability, auditability, technical debt visibility, simplicity, consistency, maintainability, extensibility, "code quality"
**Where they go:** Fold into Code-quality invariants as one or two bullets each. They're invariants, not separate destinations.

---

## How to actually use this

1. **Walk the checklist top to bottom**, once. For each concern, decide which of the three buckets it belongs in for *your* project.
2. **Write `destination.md`** with only the sections from buckets 1 and 2 (include with answer / include with open).
3. **Save this checklist** somewhere you can find it (committed to the repo or kept in your notes). When the project changes shape later, walk it again — concerns that were "not relevant" on day one might become relevant on day 200.
4. **Don't pre-include sections "for completeness"**. An empty section invites bad fills; a missing section is honest.

The grill skill (`/grill-destination`) walks this for you interactively — but if you'd rather think it through with paper and coffee, this file is the same content in static form.
