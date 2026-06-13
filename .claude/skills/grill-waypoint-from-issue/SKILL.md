---
name: grill-waypoint-from-issue
description: Build autosprint/waypoint.md from a GitHub issue. Fetches the issue via `gh`, extracts purpose and acceptance criteria from its body and comments, interviews the user only when needed to fill genuine gaps, then renders the waypoint following the canonical template. The autosprint loop will then aim at the issue exclusively until the team lead concludes the waypoint state is satisfied. Use when the user says "make a waypoint from issue #42", "import GH issue as waypoint", or wants to point autosprint at a specific tracked piece of work.
---

Build `<target_repo>/autosprint/waypoint.md` from a GitHub issue. A waypoint is a user-set intermediate target the autosprint loop aims at *exclusively* until reached; importing one from a tracked issue bridges "we use GitHub issues as backlog" and "we use autosprint to grind toward goals" without manual transcription.

## What this skill is NOT

It does **not** replace the planner. The waypoint defines *what state to reach*; the autosprint Plan phase decomposes the gap into sprintable tasks. This skill only writes the waypoint — pointing the loop at the issue. Decomposition happens when the user runs `autosprint plan` or `autosprint run`.

It does **not** modify the GitHub issue (no labels, no comments, no close). The issue and the waypoint are linked by reference (the waypoint carries a `Tracks:` line) but stay independent. The user closes the issue manually after reviewing what the loop produced.

It is **single-issue, single-waypoint**. A waypoint is by design a focused sub-goal; if the user wants to address multiple issues, they run the skill once per issue (in sequence, after each waypoint reaches and gets reviewed).

## Before starting

1. **Verify prerequisites.**
   - Run `gh auth status`. If not authenticated, stop and tell the user to run `gh auth login` first.
   - Confirm we are inside an autosprint-initialised repo: check that `<target_repo>/autosprint/config.toml` exists. If not, stop and recommend `autosprint init` first.
   - Check whether `autosprint/waypoint.md` already exists. If yes, ask: replace it, abort, or rename existing to `waypoint.md.paused` before writing the new one? Default to abort on EOF.

2. **Confirm the repo context.** Run `gh repo view --json nameWithOwner,url` to confirm which GitHub repo `gh` resolves to from the current directory. If the user expected a different repo, stop and let them `cd` first or pass `--repo owner/name` to `gh` calls below.

## Pick the issue

Two flows, depending on how the skill is invoked:

- **Direct (`/grill-waypoint-from-issue 42` or with a URL like `…/issues/42`):** parse the number, skip to "Fetch the issue".
- **No argument:** list open issues so the user can pick:

  ```text
  gh issue list --state open --limit 20 --json number,title,labels --template '{{range .}}#{{.number}} — {{.title}} {{range .labels}}[{{.name}}] {{end}}\n{{end}}'
  ```

  Display the list, ask the user for the number. Accept just the integer or `#42`. Refuse `closed` issues with a soft warning — closed issues usually mean done, and importing them into a waypoint is rarely what the user wants. If the user insists, proceed.

## Fetch the issue

```text
gh issue view <number> --json number,title,body,labels,comments,url,state,author
```

Read the JSON. Capture:

- **title** — one-line summary
- **body** — main spec
- **labels** — useful context (e.g. `bug`, `enhancement`, `breaking`)
- **comments** — clarifications, follow-ups, ACs added in discussion
- **url** — for the `Tracks:` line
- **state** — sanity check (warn if not `open`)

If the body is empty (just a title), say so and skip directly to the grill — the user will need to author the purpose and ACs from scratch with the issue title as the only anchor.

## Extract purpose + acceptance criteria

From the body and (especially) the comments, extract two things:

1. **Purpose** — a 1–3 sentence state-shaped description of *what end state the repo should be in*. Bias toward the issue's first paragraph and any "Background"/"Motivation" sections. **Convert imperative or task-list phrasing into state-shaped prose:**
   - Bad (task-shaped): *"Add CSV export to the reports module."*
   - Good (state-shaped): *"The reports module exposes a CSV export callable from the UI and the CLI."*

2. **Acceptance criteria** — 2–5 observable conditions. Hunt in this order:
   - Explicit "- [ ]" checklist in the body
   - "AC:" / "Acceptance criteria:" / "Done when:" sections
   - Comments where the issue author or a maintainer added clarifications
   - Sentences in the body containing "should", "must", "needs to" — convert to ACs

   Each AC must be **checkable by reading the code state**, not by guessing intent. *"Code is clean"* is not an AC; *"`View.export_csv(path)` writes UTF-8 with a header row"* is.

If you found enough — purpose + at least 2 concrete ACs — skip the grill and render directly. Otherwise, grill (next section).

## Grill the user (only when needed)

Ask **only the questions whose answers you actually need**. Don't bury the user in 5 prompts for a clean issue; don't skip the grill for a one-liner issue. Calibrate.

Typical gaps and the question to ask each:

- **Purpose ambiguous** → "From the issue I read this as `<your rephrasing>`. Accurate, or sharpen?"
- **No ACs at all** → "What should be true about the code when this is done? Name 2-3 observable conditions."
- **One vague AC** → restate the AC and ask: "Concretely — what file, function, or behavior would tell you this is true?"
- **Scope unclear** → "Does this waypoint also cover [related concern], or is that out of scope?"
- **Hidden decision** → if the issue silently assumes a design choice ("use Redis for the cache"), surface it: "The issue assumes Redis. Is that decided, or should the waypoint say *'cache layer (choice deferred to adr.md)'*?"

Cap at **3 questions** unless the issue is genuinely empty. Better to render a slightly imperfect waypoint and let the user edit than to drill them into fatigue.

## Render the waypoint

Use the canonical template at `examples/waypoint.example.md`. **Strip the meta sections** (the "How to use this file" and "Lifecycle" preambles) — those are for the example, not a live waypoint. The live file should be lean: title, status line, purpose, ACs, optional out-of-scope, the tracks line.

Shape:

```markdown
# Waypoint — <issue title, lightly cleaned up>

_Set <YYYY-MM-DD> from <issue url>. Delete or rename this file (e.g. to `waypoint.md.paused`) to disable._

## Purpose

<state-shaped prose, 1-3 sentences>

## Acceptance criteria

- <AC 1>
- <AC 2>
- <AC 3>

## Out of scope

<list, or `_(none)_` if obvious>

---

Tracks: <issue url>
```

The `Tracks:` line lets future humans (or future-you in a year) jump back to the GitHub thread for context. Keep it.

## Preview and save

Print the rendered waypoint in a fenced block in chat. Ask: **Save to `autosprint/waypoint.md`? (Y/n, or "edit" to revise)**.

- **Y / Enter** → write the file, confirm path.
- **n** → discard, end.
- **edit** → ask which section to revise, let user dictate the change, re-render, re-confirm.

On save, finish with a one-line next-step hint:

> Waypoint set. Run `autosprint plan` to preview the decomposition, or `autosprint run` to start grinding toward it.

## Edge cases

- **Issue referenced in body via `Closes #X` / `Depends on #Y`** — note these in the waypoint's "Out of scope" section with a one-line pointer (`Related: #X — kept out of this waypoint scope`). Don't recursively pull in related issues; one waypoint per run.
- **Issue body contains code blocks or large logs** — quote only what's load-bearing for the ACs. Don't paste a 500-line log into waypoint.md.
- **Issue is a discussion / question, not a feature** — refuse politely: *"Issue #N reads as a question, not a target state. Waypoints need a concrete reached-criterion. Either close the discussion first and open a feature issue, or describe the target state yourself."*
- **Labels carry signal** — `breaking` warrants flagging "Implementation may require ADR entry for the breaking change." Add this as a one-line note under the purpose, not as an AC.

## What "good" looks like

A waypoint generated by this skill should read like one a careful human would write: short, state-shaped, checkable, with a clear link back to the source issue. If you're tempted to write more than 30 lines, you're decomposing — stop. The planner decomposes; the waypoint just sets the goal.
