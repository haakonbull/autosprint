---
name: grill-plan
description: Vet and sharpen the pending tasks in autosprint/plan.md so each is agent-ready before an autosprint run executes them. Walks plan.md task by task against an executability rubric — concrete, scoped, testable, self-contained, real, destination-aligned — and flags weak tasks with a sharper rewrite, a split, a drop, or a convert-to-decision. Use during the hand-review of a plan drafted by `autosprint plan`, or whenever the user wants to tighten a plan before running it. Use when the user says "grill the plan", "sharpen plan.md", "are these tasks agent-ready", or "review the plan".
---

Vet the pending tasks in `<target_repo>/autosprint/plan.md` for **agent-readiness** — whether an implement agent could pick each one up and execute it without guessing — and sharpen the weak ones with the user.

This is the plan-level companion to `grill-destination`. `grill-destination` sharpens the *destination*; `grill-plan` sharpens the *next steps toward it*. It is meant to be used during the hand-review of a plan that `autosprint plan` just drafted — a second pair of eyes on *executability*, before an autosprint run executes the plan as-is.

## What this skill is NOT

It does **not** re-plan. Generating tasks from scratch — reading the gap between the repo and `destination.md` and proposing what to do — is the Plan phase's job (`autosprint plan`). `grill-plan` vets and sharpens the tasks that are *already* in `plan.md`. It may sharpen, split, drop, or reframe a task, but it never invents a fresh plan. If the plan is wrong at the root (wrong tasks entirely), the fix is to re-run `autosprint plan` or hand-author `plan.md` — not this skill.

It also does not second-guess *strategy*. Whether a task is the right priority is the user's call and the planner's job. `grill-plan` asks one question per task: **could an agent execute this as written?**

Run it **between** autosprint runs, not while a loop is live — a running loop rewrites `plan.md` itself and would clobber your edits.

## Before starting

1. Read `<target_repo>/autosprint/plan.md` — the `## Pending` section is the subject. Leave `## Recent completed` alone.
2. Read `destination.md` and `adr.md` for context — a task must move toward the destination and must not contradict a locked decision.
3. Read the actual code the tasks refer to. A task's claim ("the parser silently swallows errors") must be verified against the real code — a task aimed at a problem that doesn't exist is not ready.

## The rubric — is a task agent-ready?

Judge each pending task against all of these:

- **Concrete** — names files, functions, or observable behavior. *"Improve the code"* fails; *"split the 400-line `run_implement` in `implement_phase.py`"* passes.
- **Scoped** — one coherent piece of work. Not a mega-task spanning many files, not so trivial it's noise. The trailing `(N)` story-point tag must be present and sane; anything clearly above `SPRINT_STORY_POINT_MAX` must be split.
- **Has a done-condition** — there is a way to know it is finished: a test that would pass, an observable behavior, a check. A task with no done-condition is one an agent cannot finish.
- **Self-contained** — does not depend on a decision nobody has made. A *decision in disguise* — *"use uv to add numpy for the vector math"* — hides the unmade decision *"do we want numpy?"*. That is not ready: it needs deciding (and recording in `adr.md`), not implementing.
- **Real** — the problem it describes actually exists in the current code. Verify against the code, not the task's say-so. A task may carry an explicit `(unverified: <…>)` tag where the planner flagged a claim it could not check — treat those as your first priority: verify the tagged claim against the code, then strip the tag once confirmed, or sharpen/drop the task if the claim is false.
- **Destination-aligned** — closes distance to something in `destination.md`. A task that does not is orphan scope.
- **Distinct** — does not overlap or duplicate another pending task.

Then check the list **as a whole**:

- **Dependencies & ordering.** The team lead annotates hard prerequisites with a `Depends on: <task title>` line at the end of a task's description. Verify each one: (a) the named prerequisite is a real pending task in the list, and (b) it sits *above* the task that depends on it — the loop takes from the top, so a prerequisite below its dependent never runs first. Flag any `Depends on:` that names a missing title or a task lower down. Flag the inverse too — a task that plainly needs another to land first but carries no annotation; recommend adding the `Depends on:` line and moving it into order.
- **Priority.** Highest-value, most-reliable work on top; speculative work lower. Where priority and a dependency disagree, the dependency wins — a high-priority task waits behind its prerequisite.

## The interaction

Go **task by task**, in plan order. For each, give a one-line verdict; for anything below "ready", a concrete recommendation. Lead with the verdict:

> **grill-plan — `plan.md` review**
> 1. Split the 400-line `run_implement` (5) — **ready**.
> 2. Improve error handling (3) — **weak: vague.** Which module? Recommend: *"Replace the bare `except Exception` in `dispatch.py` `_connect` with a typed handler that logs and re-raises."*
> 3. Add caching layer (8) — **decision in disguise.** Hides "do we want a caching layer, and where?". Recommend: convert to *"Decide whether to add response caching (record in adr.md); implement if yes."*
> 4. Make tests better (2) — **drop: no done-condition.** Nothing an agent could finish or you could verify. Cut it, or replace with a concrete test task.
> 5. Refactor parser + add CLI flag + write docs (13) — **split.** Three unrelated concerns, oversized. Recommend three tasks.
> 6. Wire the CLI flag through (3) — **dependency gap.** Needs the new config field to exist first, but no `Depends on:` line says so. Recommend: add `Depends on: <the config-field task>` and check it sits above this one.

Each task then takes one outcome — the user chooses:

- **Keep** — agent-ready, leave it.
- **Sharpen** — you propose a more concrete rewrite; the user accepts or edits.
- **Split** — too big or multi-concern; break into ordered sub-tasks, each with its own `(N)`.
- **Drop** — vague beyond repair, duplicate, orphan scope, or no done-condition.
- **Convert to a decision task** — a decision in disguise; reframe as *"Decide X (record rationale in adr.md), then implement if validated."*

Lead with a recommendation for every weak task — never just "this is vague, what do you want?". Propose the sharper version; the user disposes.

## Writing the file

1. Print the full revised `## Pending` section in the conversation — sharpened tasks, splits expanded, drops removed, reordered.
2. Ask for approval. Allow one round of inline edits.
3. On approval, write `plan.md` — replace only the `## Pending` section, keeping each task in the `- [ ] Title` + indented-description format. Preserve any `Depends on:` lines; if you drop or rename a task, update or remove every `Depends on:` line that referenced it so no annotation dangles. **Never touch `## Recent completed`** (that is loop history) and never change a completed task.

If every task is already agent-ready, say so in one line and write nothing — a clean plan needs no edit.
