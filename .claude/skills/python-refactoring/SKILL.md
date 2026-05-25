---
name: python-refactoring
description: Refactor Python code following project conventions — structure, style, error handling, formatting. Use when user asks to refactor, clean up, or improve Python code, or mentions error handling.
---

Refactor the marked code (or the current file if nothing is marked) following these project rules:

## Structure & Style

1. **No wrapper functions for simple reads**: Access config values directly. Do NOT create helper functions like `get_team_name()` just to return `config.TEAM`. Use `config.TEAM` directly where needed.

2. **No premature abstractions**: Don't extract functions just to reduce line count. Three similar lines is better than a helper called only once. Do extract functions when they serve a structural purpose — avoiding nested try/except, giving a name to a distinct logical step, or keeping a function flat.

3. **No separate variables for one-time values**: If a value is only used once and is simple (a config read, a constant), inline it. Don't create `team_name = config.TEAM` three lines before its only use — just use `config.TEAM` directly. **Exception**: don't nest function calls like `extract_result(await run_query(...))`. When one function's output feeds into another, use an intermediate variable so each step is debuggable on its own line.

4. **Imports**: Prefer imports at the top of the file. Remove unused imports. Use `from autosprint.config import config` for config access. Lazy imports inside functions are acceptable when there's a good reason — optional dependencies that may not be installed, or circular imports. In those cases, add a comment explaining why. Use judgment: if the dependency is always available, move it to the top.

5. **Type hints**: Add type hints to all function parameters, return types, and variables where the type isn't obvious from the assignment. Treat Python like TypeScript — everything should be typed. Use `from __future__ import annotations` at the top of each file for modern syntax.

6. **Every function gets a one-line docstring** placed immediately after the `def` line and before the `try` keyword. Exactly one line — no multi-line docstrings, no parameter tables, no type repetition. Describe the *what*, not the *how*.

   **Phrasing by function kind (Command-Query Separation):**
   - **Query** (primary purpose is to return a value): start the docstring with `Returns <what it returns>`. Example: `"""Returns the current HEAD commit hash as a short string."""`
   - **Command** (primary purpose is a side effect — writes a file, calls an API, mutates state): start with an imperative verb describing the action. Example: `"""Append a sprint outcome line to ai-run.log."""`
   - **Mixed** (both returns something meaningful AND has a non-trivial side effect, e.g. mutates state or writes to disk): describe both briefly — action first, then what's returned. Example: `"""Run the Plan phase, write plan.md, and return (plan, next_task, sprints_since_replan)."""` Prefer splitting such functions when practical.

   **Observability logging in queries is fine.** A Query may call `printlev` to log its decision, a cache hit/miss, or why it returned what it did. This is not a "Mixed" function — stdout/log output is observability, not state mutation. Treat the function as a Query for phrasing purposes (`Returns ...`), and optionally mention the log in the docstring if it's load-bearing. Example: `"""Returns (True, reason) if plan.md should be regenerated and prints the reason; else (False, "")."""` The CQS rule about "queries should not have side effects" is aimed at mutations (writing files, changing DB state, mutating arguments) — not at tracing what the function decided.

   **On every refactor, verify the docstring is still an accurate summary of what the function does** — if the function's behaviour drifted, rewrite the line; a stale one-liner is worse than none. Don't add other inline comments unless the logic isn't self-evident.

7. **Prefer long lines over line breaks**: Max line width is 1000. Do not manually break lines for readability — keep statements on a single line even if they're long. Let black handle formatting.

8. **Bottom-up function ordering**: Entry points (`main`, `if __name__`) at the bottom. Leaf/utility functions near the top. Constants and module-level definitions at the very top after imports.

9. **Extract logically groupable blocks into named functions**: If a block of code can be described by a single word or short phrase (e.g. "prepare", "validate", "select"), extract it into a function with that name. Each function should do one logical thing.

10. **Functions own their output AND their cleanup**: A function is responsible for checking that what it returns is valid — like a shop owner checking goods before selling. The caller should not need to validate the return value. If a function detects its output is bad, it should handle that internally — retry, raise, or return a clear sentinel. If a function fails and needs cleanup (e.g. git revert, logging), it does that itself before raising — the caller just catches and moves on.

11. **Remove dead/no-op code**: If an except block only does `pass`, or a variable is assigned but never used, or code does nothing meaningful — remove it. Code that does nothing should not exist.

12. **Consolidate print blocks**: If there are 4+ consecutive print/printlev calls, consolidate them into a single call with one `\n`-joined string. One print call, one string.

## Cleanup

13. **Open-source ready**: All code should look professional and be suitable for a public GitHub repo. Remove TODO comments that were clearly forgotten, placeholder text, comments in Norwegian or other non-English languages, embarrassing or informal notes, and any other artifacts that look like personal scratch work. Variable names, function names, and comments should all be clear, professional English.

## Error Handling

14. **Wrap the entire function body** in a `try ... except Exception as e:` block. The except block calls `add_context()` with a "Failed to ..." message — the inverse of the function's purpose. For example, if the function's job is to commit changes, the context should be `"Failed to commit {n} tasks"`. Include key variable values so the error is self-explanatory. Then re-raise with `from e`.

15. **Avoid nested try/except**: Unless you have a really good reason, extract inner try/except into its own function rather than nesting. Each function should ideally be flat: one try, one except. Orchestrator/loop functions that coordinate phases may catch specific exceptions (e.g. `PhaseFailedError`) to `continue` the loop — that's a good reason.

16. **Move prints into functions**: If calling code prints `"Running X..."` before calling `function_x()`, move that print inside the function. Each function announces its own execution.

17. **No error logging in except blocks**: No `logger.error()`, `print()`, etc. in except blocks. Errors should be printed only at the top-level handler (`main`).

18. **Import add_context** from `autosprint.errors` at the top of the file.

19. **No separate context variable**: Put the context string directly in the `add_context()` call.

20. **Always use `from e`**: Preserve the exception chain.

21. **Never swallow errors**: Every except block must re-raise. No `pass`, no `return None`, no silent fallbacks. Only `main()` may catch and print without re-raising.

## Logging

22. **Log levels (`printlev`)**: `printlev(msg, level=N)` prints iff `config.LOG_LEVEL <= N`. Default `level=50`. Lower `LOG_LEVEL` = more verbose output.

    **Mental model (Google Maps zoom):** `level` is how far out you can zoom before the message disappears. Top-line messages have high `level`; noisy internals have low `level`.

    **The ladder (six rungs):**
    - **100** — prod: iteration start/end, stop conditions, run summary, **plus one-line-per-phase outcome**: "Implement: success / failed", "Test: pass / fail", "Commit: <hash> / reverted". Think "what a prod operator tails".
    - **50** — high-level debugging: adds *in-progress* phase entry banners ("Entering Plan phase...", etc.), replan reason when replan triggers, next task title, pre-commit intent message.
    - **20** — normal debugging default: plan-level detail (pending-task summary list, "Skipping Plan", "Using existing plan", "Generating plan with team of N"), operational internals (reverts, parse retries, parallel agent dispatch).
    - **10** — detailed debugging: full serialised plan whenever Plan phase runs, all parse results.
    - **5** — more detailed debugging: agent team names, selected model, per-agent decision metadata.
    - **1** — super-detailed: raw file contents, full prompts, full LLM responses.

    **Default `config.LOG_LEVEL` is 50.** Devs actively debugging typically set `LOG_LEVEL=20`. Prod runs set `LOG_LEVEL=100`.

    **Picking a level for a new `printlev`:**
    - Iteration banner, stop condition, or phase outcome → 100
    - In-progress phase entry, replan reason, next task → 50 (default, omit the kwarg)
    - Plan detail, reverts, retries, parallel dispatch → 20
    - Serialised state dumps / parse results → 10
    - Agent/model metadata → 5
    - Raw data / full prompts / file contents → 1

    **Observability logging in queries is allowed** (see rule 6) — `printlev` is tracing, not state mutation.

23. **Log message phrasing**: Messages should be short, concise, and understandable on their own — a reader should be able to tell what a line means without reading the surrounding code. Aim for **≤120 characters** so the line fits a standard terminal without wrapping.

    **Don't say the same thing twice.** When including a config-driven value, pick *either* the config key *or* a human-readable phrasing — not both.

    **Bad** (says it twice — 104 chars but redundant):
    `5 sprints since last replan (REPLAN_EVERY_N_SPRINTS=5 — replanning at least every 5 sprints)`

    **Good (human-first)** — prefer when a newcomer is reading the log:
    `5 sprints since last replan (replan at least every 5 sprints)`

    **Good (config-first)** — prefer when the config key is the precise term the team uses:
    `5 sprints since last replan (REPLAN_EVERY_N_SPRINTS=5)`

    Pick whichever is clearer standalone. Same principle for hardcoded thresholds: `(replan after 2+ failures)` is enough — no need to repeat "of the same task" if the surrounding context already makes that obvious.

## After refactoring

Run black:

```
black --line-length 1000 <changed files>
```
