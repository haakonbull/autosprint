# Waypoint — <one-line title for the intermediate target>

_Optional sub-destination that the autosprint loop will aim at **exclusively**, before resuming work toward `destination.md`. Delete or rename this file (e.g. to `waypoint.md.paused`) to disable. Last set: YYYY-MM-DD by <user>._

## How to use this file

A waypoint is a user-set intermediate target — a state you want the codebase to reach *before* the loop continues toward the broader destination. Think of it as a route waypoint in Google Maps: the destination doesn't move, but the next several sprints route via this point first.

Authoring conventions (same shape as `destination.md`):

- Write **state-shaped prose**: describe the target end state, not the steps to get there. ("The reporting module exposes a CSV export…", not "First, add the CSV writer, then…")
- Keep it **short**: one purpose, 2–5 acceptance criteria, no architectural detail. Architectural choices belong in `adr.md`.
- Don't write a task list — the planner decomposes the gap into sub-tasks.
- The waypoint must be **reachable without violating destination.md or any ADR**. If there's a real conflict, the team lead surfaces it as an open task instead of silently picking a side.

## Lifecycle

1. **Set:** drop a `waypoint.md` file at `autosprint/waypoint.md` with the content below filled in.
2. **(Optional) Preview:** run `uv run autosprint plan-only` to see the decomposition the planner produces. Note that `plan-only` is a preview — the real run will re-plan and may produce slightly different tasks.
3. **Run:** start `uv run autosprint`. The Plan phase aims at the waypoint exclusively. Each sprint header logs `🧭 Waypoint active: <title>` so you can see the loop is in waypoint mode.
4. **Reached:** when the team lead concludes the waypoint state is satisfied, the orchestrator appends a `> **Status:** reached <date> — <rationale>` blockquote to this file and halts the loop. The file is **not** deleted — you review and decide what's next.
5. **Pause (without losing content):** rename to `waypoint.md.paused`. The loop ignores it. Rename back to re-activate.
6. **Done:** delete the file (or move to an archive folder), then resume autosprint normally.

## When **not** to use a waypoint

- For one-shot work that fits in a single sprint: just open Claude Code and ask. The loop's value is multi-sprint discipline.
- For a vague "focus area": waypoints need a concrete reached criterion. If you can't say what "done" looks like, the loop won't be able to either.

## Purpose

_What state should the repo be in once this waypoint is reached, and why? 1–3 sentences in plain language._

## Acceptance criteria

_2–5 concrete, observable conditions that — when all satisfied by the actual code state — mean the waypoint is reached. Each should be checkable by the team lead reading the code, not by guessing intent._

- _Criterion 1_
- _Criterion 2_
- _Criterion 3_

## Out of scope

_Any nearby work that would be tempting to bundle in but should NOT be part of this waypoint. Helps the planner stay focused. Optional — leave the heading and write `_(none)_` if the waypoint scope is obvious._
