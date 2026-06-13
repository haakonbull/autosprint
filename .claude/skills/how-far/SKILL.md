---
name: how-far
description: Estimate how much of autosprint/destination.md is already implemented — a fast "how far along the route are we" read. Decomposes destination.md into discrete requirements, checks each against the actual codebase and its tests, and reports a status table (done / partial / not started / unclear) with code evidence plus a headline count. Read-only — it measures distance to the destination, it does not plan or change anything. Use when the user asks "how far are we", "how far from destination", "how much is done", "what's left to build", or "are we close".
---

Estimate how far the codebase has travelled toward `autosprint/destination.md` and report it as a scannable status table. autosprint drives toward the destination along an approved route; this skill is the **odometer** — a quick read of how much of the target state is already real, and how far is left to go.

This is the measurement companion to `grill-destination` and `grill-plan`. `grill-destination` sharpens the *destination*; `grill-plan` sharpens the *next steps*; `how-far` measures the *distance still to travel*. It is the inverse of `grill-destination`'s mature-repo mode — that mode drafts a destination *from* the code; this one measures the code *against* the destination.

## What this skill is NOT

- It does **not** plan. Proposing what to do next is the Plan phase's job (`autosprint plan`) and `grill-plan`'s. This skill only reports where things stand.
- It does **not** edit `destination.md`. Sharpening the destination is `grill-destination`'s job.
- It does **not** impose its own rubric. The rows are the requirements *stated in destination.md* — nothing more. If a dimension you care about (test coverage, deployment, a refactor) is not a row, that is because destination.md does not state it as a goal — which is itself a useful finding (see below).
- It does **not** emit a percentage. Per-requirement status is categorical; "73 % done" is a vibe an LLM cannot calibrate, and it drifts optimistic.

## Before starting

1. Read `<target_repo>/autosprint/destination.md` in full — both the human-authored spec and the `## AI-generated subgoals` section. This is the subject.
2. Read `<target_repo>/autosprint/adr.md` for context — technical decisions already made. ADRs are *how*, not *what*; they do not become rows.
3. **Read the actual code.** This is the load-bearing step. Every status you assign must be verified against real source and tests — not inferred from destination.md's wording or from a first impression.

## Method

### 1. Decompose destination.md into requirements

destination.md is prose. Break it into **discrete, separately-assessable requirements** — each a stated feature, behavior, or success criterion. Each becomes one row. A good row is something you can stand in front of the code and answer "is this real?" about.

Reflect destination.md faithfully. Do not invent rows for work it does not name. If you expected a dimension (test coverage, deployment, a refactor) and it is not in destination.md, do not add a row for it — instead note in the closing summary that the destination is silent on it. That tells the user the destination may need that goal added — a `grill-destination` job.

### 2. Assess each requirement against the code

For each requirement, search the codebase for the implementing code **and its tests**. Verify by reading them. A requirement is only as done as the code proves.

### 3. Assign a status — categorical, evidence-anchored

| Status             | Means                                                                                                              | Evidence rule                                           |
| ------------------ | ------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------- |
| ✅ **done**        | Fully implemented and verifiable — the behavior exists and tests (or another concrete check) cover it.             | Must cite the implementing code and its test(s).        |
| 🟡 **partial**     | The core path exists but something is missing — an edge case, error handling, a sub-requirement, or test coverage. | Must cite what *is* there and name the specific gap.    |
| ⬜ **not started** | No implementing code found.                                                                                        | —                                                       |
| ❓ **unclear**     | The requirement is too vague to assess, or the code state genuinely cannot be determined.                          | Say which — a vague requirement is a finding in itself. |

**The evidence rule is the load-bearing one.** `done` and `partial` are not allowed without a concrete code pointer — a file, a function, a test. A requirement you would *like* to call done but cannot point at code for is not done: mark it ⬜ or ❓. LLM self-assessment drifts optimistic; the code pointer is the anchor that keeps the report honest.

## Output

Lead with a one-line headline — an honest **count**, never a percentage:

> **Distance to destination — 14 requirements: 6 ✅ done · 4 🟡 partial · 3 ⬜ not started · 1 ❓ unclear**

Then a table, in destination.md's own order so each row maps back to the doc:

> | Requirement (from destination.md)  | Status         | Evidence / gap                                                             |
> | ---------------------------------- | -------------- | -------------------------------------------------------------------------- |
> | Hearing ingest from the source API | ✅ done        | `sync/core.py` `_sync_locked`; covered by `tests/unit/test_sync_core.py`   |
> | Vector search over hearing chunks  | 🟡 partial     | `embeddings.py` upserts vectors, but no query/retrieval path and no test   |
> | On-demand HTTP API                 | ⬜ not started | no `api/` module, no HTTP framework in dependencies                        |
> | "Fast" ingest                      | ❓ unclear     | destination.md states no latency target — too vague to assess against code |

Close with a 2–4 sentence verdict: what is solid, the biggest remaining gap, and which 🟡 partial is closest to done. If several rows are ❓, say so plainly — that means destination.md needs sharpening before this skill can give a clean read.

## This is a snapshot, not a ledger

- **Print the table in the conversation. Do not write it to a file** — it would be stale the next sprint. Re-run the skill whenever you want a fresh read; like `autosprint plan`, it is an on-demand instrument, not a stored artifact.
- It is an LLM judgment against destination.md *as currently written*. A vague destination produces vague rows. If the report is mostly ❓, the fix is `grill-destination`, not this skill.
- It reports distance; it does not close it. To act on a gap, run `autosprint plan` (or `grill-plan` to sharpen the resulting tasks).
