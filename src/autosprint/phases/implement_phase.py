"""Implement phase: dispatch the implementor LLM, parse, log, revert on failure.

Owns:
- The Implement step entry point `run_implement` plus its helpers
  `_run_implement_llm` (single dispatch, parser-retry fallback) and
  `_attempt_refusal_fallback` (re-dispatch on Opus 4.8 safety-reminder misread).
- Failure logging: `log_implement_failure`, `dump_last_implement_raw`,
  `_warn_if_refusal_pattern`.
- Refusal detection: `detect_refusal_pattern` and the phrase list.
- The fake implementor used in FAKE_IMPLEMENT mode (`_fake_implement`).
- The "prior attempts" prompt section (`_task_history_section`) baked into
  every Implement-agent dispatch.
"""

import random
from datetime import UTC, datetime

from autosprint.config import config
from autosprint.core.plan import group_titles, read_plan_md, serialise_plan
from autosprint.infra.dispatch import query_agent
from autosprint.infra.git_ops import git_restore, summarise_working_tree_diff
from autosprint.phases.plan_phase import lock_destination_section, read_adr, read_agent_file
from autosprint.registry.agents import TOOLS_FULL, TOOLS_READ_ONLY
from autosprint.reporting.run_log import append_run_log, extract_story_points, task_attempt_stats
from autosprint.util.errors import PhaseFailedError, RevertReason, add_context
from autosprint.util.output import printlev

# detect_refusal_pattern lives in the parsing leaf so run_log can reuse it
# without a run_log <-> implement_phase cycle; re-exported here because callers
# and tests reach it as implement_phase.detect_refusal_pattern.
from autosprint.util.parsing import ImplementResponseMalformed, detect_refusal_pattern, parse_implement_result
from autosprint.util.paths import AUTOSPRINT_DIR_NAME, IMPLEMENT_FAILURES_LOG_FILENAME, LAST_IMPLEMENT_FAILURE_FILENAME


def log_implement_failure(sprint_number: int, task: dict, reason: str, raw_response: str) -> None:
    """Append a full Implement-failure record to autosprint/logs/implement-failures.log: timestamp, sprint number, task title, failure reason, and a head/tail excerpt of the raw agent response so the root cause is recoverable without digging through autosprint/cache/. Silent no-op in FAKE_IMPLEMENT mode (stochastic fake failures would pollute a real failure log)."""
    if config.FAKE_IMPLEMENT:
        return
    log_path = config.TARGET_REPO_PATH / IMPLEMENT_FAILURES_LOG_FILENAME
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        raw = raw_response or "(no raw response captured)"
        excerpt = raw if len(raw) <= 4000 else f"{raw[:2000]}\n\n... [middle of response truncated, {len(raw) - 4000} chars omitted] ...\n\n{raw[-2000:]}"
        block = f"\n# === {ts} · sprint {sprint_number} ===\nTask: {task.get('title', '(unknown)')}\nReason: {reason}\n\n--- Raw agent response ---\n{excerpt}\n"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(block)
    except Exception as e:
        raise add_context(e, f"Failed to write implement failure record to {log_path}") from e


def dump_last_implement_raw(raw_response: str, reason: str) -> None:
    """Write the full raw implementer response to ``autosprint/logs/last-implement-failure.txt`` on any Implement-phase failure (malformed response or status=failure). Complements ``implement-failures.log`` (which appends truncated excerpts keyed by timestamp) by giving a single always-up-to-date sidecar file for quick manual recovery when a sprint was reverted but the agent's work looked substantively correct.

    **Important UX note baked into the file header:** this file is overwritten **only on failure**. Successful runs do not touch it, so after many green sprints in a row it can hold a transcript from a much older failed run. The header makes that explicit so a casual reader doesn't mistake stale failure context for the most recent run's outcome. Silent no-op in ``FAKE_IMPLEMENT`` mode.
    """
    if config.FAKE_IMPLEMENT:
        return
    path = config.TARGET_REPO_PATH / LAST_IMPLEMENT_FAILURE_FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        header = f"# autosprint — last implement-phase failure transcript\n# This file is overwritten ONLY when an implement phase fails; successful sprints DO NOT update it.\n# If many sprints have succeeded since the timestamp below, this transcript is from an older failed run — check sprint-outcomes.log for the current state.\n# timestamp: {ts}\n# reason: {reason}\n# raw response follows (unredacted):\n\n"
        path.write_text(header + (raw_response or "(no raw response captured)"), encoding="utf-8")
    except Exception:
        # Best-effort only — never block the revert path on a dump failure.
        pass


def warn_if_refusal_pattern(reason: str) -> None:
    """Print a one-line hint when an Implement failure looks like the Opus 4.8 safety-reminder misread. Quietly returns when the pattern doesn't match so genuine failures aren't drowned in noise."""
    if detect_refusal_pattern(reason):
        printlev("[I] ⚠️  Looks like an Opus 4.8 safety-reminder misread — see autosprint/logs/implement-failures.log for the full response. The counter-language in .claude/agents/implement.md is meant to suppress this; if it keeps firing, consider switching the Implement agent to Opus 4.6 or Sonnet 4.6.", level=100)


def fake_implement(task_group: list[dict]) -> dict:
    """Returns a simulated Implement result for a task group (1+ tasks): success appends a marker line per task to autosprint/fake-implement.log, failure returns without touching the filesystem. Failure probability is config.FAKE_IMPLEMENT_FAILURE_RATE (default 0.2). Writing inside autosprint/ keeps fake sprints from tripping real test suites in the target repo."""
    try:
        if random.random() < config.FAKE_IMPLEMENT_FAILURE_RATE:
            return {"status": "failure", "reason": f"fake implement: simulated failure ({int(config.FAKE_IMPLEMENT_FAILURE_RATE * 100)}% rate)"}
        marker_path = config.TARGET_REPO_PATH / AUTOSPRINT_DIR_NAME / "fake-implement.log"
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        existing = marker_path.read_text(encoding="utf-8") if marker_path.exists() else ""
        marker_path.write_text(existing + "".join(f"fake sprint: {t['title']}\n" for t in task_group), encoding="utf-8")
        titles = group_titles(task_group)
        return {"status": "success", "summary": f"fake implement: appended {len(task_group)} marker(s) to {AUTOSPRINT_DIR_NAME}/fake-implement.log (tasks: {titles})"}
    except Exception as e:
        raise add_context(e, f"Failed to run fake implement for task group '{group_titles(task_group)}'") from e


def task_history_section(task_title: str) -> str:
    """Build the 'Prior attempts' block for the Implement prompt. Always includes computed facts (attempts + reverts) and specific file pointers with a reason for reading each one — the agent has Read/Grep tools and should use them rather than reading inline excerpts that go stale. Generic 'check these files' guidance is deliberately avoided."""
    attempts, reverts = task_attempt_stats(task_title)
    attempts_line = "This task has not been attempted before." if attempts == 0 else f"This task has been attempted {attempts} time(s) in prior sprints; {reverts} of those were reverted."
    pointers = [
        f"- Grep `autosprint/logs/sprint-outcomes.log` for {task_title!r} — one line per prior attempt, with outcome + commit hash or revert reason.",
        "- If any of those prior attempts show REVERTED, read `autosprint/logs/implement-failures.log` for the full raw Implement-agent response on the most recent failure — it shows exactly what went wrong.",
        "- Check `autosprint/logs/preflight-tests.log` — if the prior sprint failed and left the codebase reverted, a pre-flight survey may have captured failing tests that the team lead saw before picking this task.",
    ]
    return "\n\n## Prior attempts on this task\n\n" + attempts_line + "\n\n" + "\n".join(pointers)


async def run_implement_llm(task_group: list[dict], sprint_number: int, agent_override: dict | None = None, phase_tag: str = "[I]") -> tuple[dict, str]:
    """Call the Implement agent with a task group (1+ tasks), parse the response, and return (parsed_result, raw_response); revert + raise PhaseFailedError on malformed output. The raw response is returned so the caller can log it verbatim on failure (context that's otherwise buried in autosprint/cache/).

    Args:
        agent_override: Optional agent dict to dispatch to instead of `config.IMPLEMENT_AGENT_CONFIG`. Used by the refusal-fallback path to re-dispatch a refused sprint to a different model on the same prompt.
        phase_tag: Phase prefix for log lines. Defaults to ``[I]``; the refusal-fallback dispatch passes ``[I-fallback]`` so the second attempt is attributable in the console log.
    """
    try:
        plan_text = serialise_plan(read_plan_md(config.TARGET_REPO_PATH), recent_count=config.PLAN_RECENT_COMPLETED_COUNT)
        adr = read_adr()
        adr_block = f"\n\n## Current autosprint/adr.md (architecture decision records — stable choices, respect, do not overturn casually)\n\n{adr}" if adr.strip() else ""
        history = "".join(task_history_section(t["title"]) for t in task_group)
        if len(task_group) == 1:
            t = task_group[0]
            task_section = f"\n\n## Current task\n\nTitle: {t['title']}\nDescription: {t['description']}\n"
            plan_context_note = "You are executing the FIRST pending task only."
        else:
            task_lines = "\n\n".join(f"### Task {i}: {t['title']}\n\n{t['description']}" for i, t in enumerate(task_group, 1))
            task_section = f"\n\n## Current task group ({len(task_group)} tasks)\n\nYou are implementing this group of {len(task_group)} tasks together in a single sprint. Work through them in order. The scope-discipline rules apply **per task** — each task should touch only what that task needs; don't let task-1 bleed into task-2's files unless a concrete dependency requires it. Your RESULT summary at the end should cover the whole group in one `summary` string (e.g. 'Task 1: X → Y. Task 2: A → B.').\n\n{task_lines}\n"
            plan_context_note = f"You are executing the FIRST {len(task_group)} pending tasks together as a group."
        agent_to_use = agent_override if agent_override is not None else config.IMPLEMENT_AGENT_CONFIG
        prompt = read_agent_file(".claude/agents/implement.md") + lock_destination_section() + f"\n\n## Plan context\n\nHere is the current autosprint/plan.md. {plan_context_note}\n\n{plan_text}{adr_block}{history}{task_section}"
        # Structured-result capture: both backends register `submit_implement_success`
        # / `submit_implement_failure` tools when this dict is passed; the agent's
        # typed args land here directly, no text parsing required.
        captured_result: dict = {}
        raw = await query_agent(agent_to_use, prompt, TOOLS_FULL, skip_cache=True, phase_tag=phase_tag, result_capture=captured_result)
        # Happy path: the agent called one of the structured-exit tools, so the
        # capture dict has a typed result. The legacy `---RESULT---` text parser
        # stays as the fallback for cases where the agent emits a text block
        # instead of (or in addition to) calling the tool.
        if captured_result.get("status") in ("success", "failure"):
            if captured_result["status"] == "success" and captured_result.get("summary"):
                printlev(f"{phase_tag} 🔧 Result captured via structured-exit tool (submit_implement_success).", level=20)
                return {"status": "success", "summary": str(captured_result["summary"]).strip(), "resolved_open_questions": list(captured_result.get("resolved_open_questions") or [])}, raw
            if captured_result["status"] == "failure":
                reason = str(captured_result.get("reason") or "").strip() or "(no reason given)"
                printlev(f"{phase_tag} 🔧 Result captured via structured-exit tool (submit_implement_failure).", level=20)
                return {"status": "failure", "reason": reason}, raw
            # Captured a status with no payload — fall through to text parsing
            # rather than raising, so the legacy block can still rescue.
        try:
            result = parse_implement_result(raw)
            printlev(f"{phase_tag} 📜 Result parsed from legacy ---RESULT--- text block (agent did not call the structured-exit tool).", level=20)
        except ImplementResponseMalformed as e:
            # Before reverting, try one retry asking the agent to re-emit
            # ONLY the RESULT block. This recovers sprints where the work is
            # already on disk but the terminating format was mangled (forgot
            # `---END---`, wrote plain text instead of JSON, etc.).
            if config.IMPLEMENT_PARSER_RETRY:
                printlev(f"[I] ⚠ Implement response malformed: {e}. Trying one-shot format-retry before reverting...", level=100)
                # Retry runs in a FRESH SDK session with no memory of the prior call,
                # so we MUST embed the tail of the prior raw response. Without it the
                # retry agent truthfully reports "no prior task in this conversation"
                # and the loop reverts work that's actually on disk (sprint 39 on 2026-04-24).
                raw_tail_chars = 6000
                raw_head_chars = 2000
                if len(raw) > raw_tail_chars + raw_head_chars:
                    raw_head = raw[:raw_head_chars]
                    raw_tail = raw[-raw_tail_chars:]
                    truncation_note = f"[…truncated {len(raw) - raw_tail_chars - raw_head_chars} chars in the middle…]\n"
                    raw_excerpt = raw_head + "\n" + truncation_note + raw_tail
                else:
                    raw_excerpt = raw
                    truncation_note = ""
                retry_prompt = (
                    "Your previous response was missing or malformed a RESULT block in the required format, "
                    "but the implementation work is already on disk. We need one correctly-formatted RESULT block.\n\n"
                    "IMPORTANT: this message is in a fresh SDK session — you cannot remember your prior work directly, "
                    "so the head and tail of your previous response is embedded below. Read it, then emit the RESULT block.\n\n"
                    "----- BEGIN PRIOR RESPONSE -----\n"
                    f"{raw_excerpt}\n"
                    "----- END PRIOR RESPONSE -----\n\n"
                    "Now re-emit ONLY the RESULT block, nothing else. Required format:\n\n"
                    "---RESULT---\n"
                    '{"status": "success", "summary": "<≤120 chars: what was asked → what you did>"}\n'
                    "---END---\n\n"
                    "Write the summary based on the prior response tail above — it shows what you changed and whether tests passed. "
                    "Do NOT say 'no prior task' — the prior response is embedded above; use it. "
                    "If the tail clearly shows the sprint genuinely failed (test failures, refusal, blocker), "
                    'use `{"status": "failure", "reason": "..."}` instead. '
                    "No prose around the block. Start with `---RESULT---` and end with `---END---` on its own line."
                )
                # Reset the structured-result capture for the retry so a stale
                # value from the primary attempt cannot contaminate the retry's
                # exit. Pass it through so a Claude retry that prefers calling
                # the structured tool over re-emitting the legacy block still
                # terminates cleanly.
                captured_result.clear()
                try:
                    retry_raw = await query_agent(agent_to_use, retry_prompt, TOOLS_READ_ONLY, skip_cache=True, phase_tag=f"{phase_tag}-retry", result_capture=captured_result)
                    if captured_result.get("status") == "success" and captured_result.get("summary"):
                        result = {"status": "success", "summary": str(captured_result["summary"]).strip(), "resolved_open_questions": list(captured_result.get("resolved_open_questions") or [])}
                    elif captured_result.get("status") == "failure":
                        reason_b2 = str(captured_result.get("reason") or "").strip() or "(no reason given)"
                        result = {"status": "failure", "reason": reason_b2}
                    else:
                        result = parse_implement_result(retry_raw)
                    printlev(f"[I] ✅ Format-retry recovered the sprint — parsed status={result.get('status')!r}.", level=100)
                    # Merge retry_raw into the on-disk raw so downstream dumps
                    # show the whole conversation.
                    raw = f"{raw}\n\n---[format-retry prompt sent, agent replied:]---\n{retry_raw}"
                    return result, raw
                except ImplementResponseMalformed as retry_err:
                    printlev(f"[I] ❌ Format-retry also malformed: {retry_err}. Reverting.", level=100)
                    log_implement_failure(sprint_number, task_group[0], f"Malformed response (after one retry): {retry_err}", raw)
                    dump_last_implement_raw(raw, f"Malformed response (after one retry): {retry_err}")
                    git_restore()
                    for t in task_group:
                        append_run_log(sprint_number, t["title"], "MALFORMED", "n/a", "REVERTED", revert_reason=RevertReason.IMPLEMENT_MALFORMED.value)
                    raise PhaseFailedError(f"Implement response malformed (after one retry): {retry_err}", RevertReason.IMPLEMENT_MALFORMED) from retry_err
                except Exception as retry_err:
                    printlev(f"[I] ❌ Format-retry dispatch failed: {retry_err}. Reverting.", level=100)
                    log_implement_failure(sprint_number, task_group[0], f"Malformed response; retry dispatch failed: {retry_err}", raw)
                    dump_last_implement_raw(raw, f"Malformed response; retry dispatch failed: {retry_err}")
                    git_restore()
                    for t in task_group:
                        append_run_log(sprint_number, t["title"], "MALFORMED", "n/a", "REVERTED", revert_reason=RevertReason.IMPLEMENT_MALFORMED.value)
                    raise PhaseFailedError(f"Implement response malformed (retry dispatch failed): {retry_err}", RevertReason.IMPLEMENT_MALFORMED) from retry_err
            printlev(f"[I] ❌ Implement response malformed: {e}. Reverting.", level=100)
            log_implement_failure(sprint_number, task_group[0], f"Malformed response: {e}", raw)
            dump_last_implement_raw(raw, f"Malformed response: {e}")
            git_restore()
            for t in task_group:
                append_run_log(sprint_number, t["title"], "MALFORMED", "n/a", "REVERTED", revert_reason=RevertReason.IMPLEMENT_MALFORMED.value)
            raise PhaseFailedError(f"Implement response malformed: {e}", RevertReason.IMPLEMENT_MALFORMED) from e
        return result, raw
    except PhaseFailedError:
        raise
    except Exception as e:
        raise add_context(e, f"Failed to run Implement LLM call for task group '{group_titles(task_group)}'") from e


async def attempt_refusal_fallback(task_group: list[dict], sprint_number: int, primary_reason: str, primary_raw: str) -> tuple[dict, str] | None:
    """When the primary implementor returns a refusal-pattern failure and a fallback agent is configured, revert any partial edits the primary made and re-dispatch the same task group to the fallback. Returns ``(result, merged_raw)`` on a successful rescue, or ``None`` if the fallback isn't configured / also fails. The merged raw stitches primary + fallback transcripts so downstream logs (last-implement-failure.txt, implement-failures.log) preserve the full conversation. Failure inside the fallback dispatch (network, malformed, or another refusal) is treated as 'fallback didn't rescue' rather than a hard error — the caller falls through to the original revert path with the primary failure as the reason."""
    fallback = config.IMPLEMENT_FALLBACK_AGENT_CONFIG
    if fallback is None:
        return None
    fallback_key = config.IMPLEMENT_FALLBACK_AGENT
    primary_key = config.IMPLEMENT_AGENT
    printlev(f"[I] ⚠ Primary implementor ({primary_key}) refused — refusal-fallback re-dispatching to {fallback_key}...", level=100)
    # Clean any partial edits the refused primary made before the fallback runs,
    # so the fallback starts from the same clean tree the primary saw.
    git_restore()
    try:
        fallback_result, fallback_raw = await run_implement_llm(task_group, sprint_number, agent_override=fallback, phase_tag="[I-fallback]")
    except PhaseFailedError as fb_err:
        # Fallback hit malformed-response or another orchestrator-detected fault.
        # Surface as "fallback failed" but don't mask the primary's reason — the
        # caller logs the original refusal context.
        printlev(f"[I] ❌ refusal-fallback dispatch failed ({fb_err}). Falling back to original revert.", level=100)
        return None
    except Exception as fb_err:
        printlev(f"[I] ❌ refusal-fallback dispatch raised ({type(fb_err).__name__}: {fb_err}). Falling back to original revert.", level=100)
        return None
    merged_raw = f"{primary_raw}\n\n---[refusal-fallback dispatched: primary={primary_key} refused; fallback={fallback_key} re-ran the prompt]---\n{fallback_raw}"
    if fallback_result["status"] == "success":
        printlev(f"[I] ✅ refusal-fallback ({fallback_key}) rescued the sprint after {primary_key} refused.", level=100)
        return fallback_result, merged_raw
    # Fallback ran cleanly but also returned status=failure. Don't mask: caller
    # will revert and surface the fallback's reason (which is more recent than
    # the primary's) — but the merged raw preserves both for the log.
    printlev(f"[I] ❌ refusal-fallback ({fallback_key}) also failed: {fallback_result.get('reason', '?')}. Reverting.", level=100)
    return fallback_result, merged_raw


async def run_implement(task_group: list[dict], sprint_number: int) -> dict:
    """Run the Implement phase for a task group (1+ tasks): dispatch to _fake_implement or the LLM, revert + raise on failure, and return the parsed result on success. The whole group is atomic — any failure reverts every task in the group. Refusal-fallback: if the primary implementor returns a refusal-pattern failure and `IMPLEMENT_FALLBACK_AGENT` is configured, the fallback agent is re-dispatched on the same task group before reverting; a successful fallback commits as usual with `recovered_by_fallback` recorded in the SQLite mirror."""
    try:
        printlev("\n[I] 🔨 Entering Implement phase...")
        total_sp = sum(sp for sp in (extract_story_points(t["title"]) for t in task_group) if sp is not None)
        header = f"[I] Sending {len(task_group)} task{'s' if len(task_group) != 1 else ''} to implementer" + (f" ({total_sp} SP total):" if total_sp else ":")
        task_lines = [f"   [] {t['title']}" for t in task_group]
        printlev("\n".join([header, *task_lines]))
        raw_response: str = ""
        recovered_by: str | None = None
        if config.FAKE_IMPLEMENT:
            printlev(f"[I] Using FAKE_IMPLEMENT (no LLM, {int(config.FAKE_IMPLEMENT_FAILURE_RATE * 100)}% failure rate)", level=20)
            result = fake_implement(task_group)
        else:
            result, raw_response = await run_implement_llm(task_group, sprint_number)
            # Refusal-only fallback. Non-refusal failures (test failures,
            # malformed responses, real bugs) skip the fallback so problems
            # aren't masked.
            if result["status"] == "failure" and detect_refusal_pattern(result["reason"], raw_response):
                primary_reason = result["reason"]
                fallback_outcome = await attempt_refusal_fallback(task_group, sprint_number, primary_reason, raw_response)
                if fallback_outcome is not None:
                    fallback_result, merged_raw = fallback_outcome
                    raw_response = merged_raw
                    if fallback_result["status"] == "success":
                        recovered_by = config.IMPLEMENT_FALLBACK_AGENT
                        result = fallback_result
                    else:
                        # Fallback also failed — keep its (more recent) reason
                        # so the log line reflects the actual final outcome.
                        result = fallback_result
        if result["status"] == "failure":
            reason = result["reason"]
            printlev("\n".join([f"   [failed] {t['title']}" for t in task_group]), level=100)
            printlev(f"[I] ❌ Implement failed: {reason}. Reverting.", level=100)
            log_implement_failure(sprint_number, task_group[0], reason, raw_response)
            dump_last_implement_raw(raw_response, reason)
            warn_if_refusal_pattern(reason)
            git_restore()
            impl_reason = RevertReason.IMPLEMENT_REFUSED if detect_refusal_pattern(reason) else RevertReason.IMPLEMENT_FAILED
            for t in task_group:
                append_run_log(sprint_number, t["title"], "FAILED", "n/a", "REVERTED", revert_reason=impl_reason.value)
            raise PhaseFailedError(f"Implement failed: {reason}", impl_reason)
        summary = result["summary"]
        printlev("\n".join([f"   [x] {t['title']}" for t in task_group]))
        printlev(f"[I] Files changed:\n{summarise_working_tree_diff()}")
        printlev(f"[I] ✅ Implement phase succeeded: {summary[:120]}", level=100)
        for t in task_group:
            append_run_log(sprint_number, t["title"], "OK", "pending", summary[:120], recovered_by_fallback=recovered_by)
        return result
    except PhaseFailedError:
        raise
    except Exception as e:
        git_restore()
        printlev(f"[I] ❌ Unexpected error in Implement phase ({type(e).__name__}): {e}. Reverting.", level=100)
        for t in task_group:
            append_run_log(sprint_number, t["title"], "FAILED", "n/a", "REVERTED", revert_reason=RevertReason.IMPLEMENT_FAILED.value)
        raise PhaseFailedError(
            f"Implement phase unexpected error ({type(e).__name__}): {e}",
            RevertReason.IMPLEMENT_FAILED,
        ) from e
