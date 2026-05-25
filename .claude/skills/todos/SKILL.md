---
name: todos
description: Read `todos.md` at the repo root and execute each unchecked task in order, marking `[]` → `[x]` in the file as each task completes. Each `[]` line marks the start of a task; the text following it (until the next `[]` or end of file) is the task description. Use when the user says "see todos.md", "do todos", "run todos", or otherwise asks to process the todos file.
---

Read `todos.md` at the repo root and execute each unchecked task in it.

## File format

`todos.md` is a plain list of tasks. Conventions:

- **Each `[]` (or `[ ]`) on its own line marks the start of a task.** The lines below it — up to the next `[]` line, or end of file — are the task description.
- **Leading content above the first `[]`** (if any) is the first task, even if it has no explicit `[]` marker.
- **`[x]`** means completed — skip it.
- Completely blank sections between tasks are decorative; ignore them.

## What to do

1. Read `todos.md`.
2. Parse it into an ordered list of tasks. For each task, capture whether it's `[]` (pending) or `[x]` (done).
3. For each pending task, in order:
   - Re-read the task description carefully.
   - Decide what the user is asking for. If unclear, ask one concise question before proceeding.
   - Do the work — code changes, file edits, whatever the task requires.
   - Run the project's tests or other obvious validation if applicable.
   - **Only when the task is genuinely complete**, edit `todos.md` to change that task's `[]` to `[x]`. Leave the description intact.
   - If the task was the leading-content task with no explicit marker, add an `[x]` marker line immediately above it so future runs skip it.
4. **Re-read `todos.md` one last time** after the final pending task. The user often adds new `[]` items while tasks are running — any new pending items that appeared during the session must be picked up before ending. If new items exist, loop back to step 3.
5. After the last pending task is done (or if a task blocks on a question), summarise what was completed and what is still pending.
6. **End-of-run summary.** Write a short summary in chat of what was completed and what is still pending.

## What not to do

- Do not delete or reorder the task descriptions. Only change the checkbox.
- Do not batch the mark-as-done edits — mark each task done as soon as it is actually done, so a crash mid-run never loses track.
- Do not skip ahead. Tasks are listed in intended order.
- Do not invent new tasks. The file is authoritative.

## When the file is empty or all tasks are `[x]`

Reply with one line: "todos.md has no pending tasks." Don't generate new ones.
