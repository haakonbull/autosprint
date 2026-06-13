"""Result-block parsing for agent responses.

The agents emit a JSON object as the last top-level JSON in their response
(by convention wrapped in `---RESULT---...---END---` markers, but we don't
rely on the markers). These helpers find the last top-level JSON object with
a given key and validate it against the Implement-phase schema. Used by both
the plan phase and the implement phase, which is why this lives in its own
leaf module.
"""

from __future__ import annotations

import json

from autosprint.util.output import printlev


class ImplementResponseMalformed(Exception):
    """Raised when the Implement agent's response cannot be parsed into a valid result.

    Distinct from a legitimate `{"status": "failure"}` — malformed means the agent
    did not follow the contract at all (missing RESULT block, unknown status,
    missing required fields). Logged separately so it's visible in the sprint log.
    """


def _parse_json_safe(raw: str) -> dict | None:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _find_last_json_with_key(text: str, key: str) -> dict | None:
    """Scan `text` from the end for the last well-formed JSON object that has `key` at the top level. Robust to missing/mangled result-block delimiters, markdown code fences, and stray JSON in the agent's narrative — the final result payload is, by convention, the last JSON object in the response. Walks backward through close-braces, matches each with its open-brace by depth-counting, and tries json.loads on each candidate; returns on the first top-level match, skips rejected candidates while still descending into nested JSON (the upper bound passed to rfind is exclusive, so the inner close-brace of a rejected outer object is still reachable)."""
    needle = f'"{key}"'
    i = len(text)
    while True:
        close = text.rfind("}", 0, i)
        if close == -1:
            return None
        depth = 0
        start = -1
        for j in range(close, -1, -1):
            ch = text[j]
            if ch == "}":
                depth += 1
            elif ch == "{":
                depth -= 1
                if depth == 0:
                    start = j
                    break
        if start != -1:
            candidate = text[start : close + 1]
            if needle in candidate:
                parsed = _parse_json_safe(candidate)
                if parsed is not None and key in parsed:
                    return parsed
        i = close


def parse_result(text: str, expected_key: str = "pending") -> dict | None:
    """Return the last top-level JSON object in `text` that has `expected_key`, or None if nothing matches. The caller (parse_implement_result / plan parsing) validates shape. No longer depends on `---RESULT---`/`---END---` markers or markdown-fence stripping — agents still emit markers by convention but parsing doesn't hinge on them, which eliminates the whole class of "valid work reverted because the terminator was missing" cascades."""
    try:
        found = _find_last_json_with_key(text, expected_key)
        if found is None:
            excerpt = text[-200:] if len(text) > 200 else text
            printlev(f"[parse_result] Could not find JSON object with key {expected_key!r}; last 200 chars: {excerpt!r}", level=50)
        return found
    except Exception as e:
        excerpt = text[-200:] if len(text) > 200 else text
        printlev(f"[parse_result] Parse exception ({type(e).__name__}: {e}); last 200 chars: {excerpt!r}", level=50)
        return None


def _coerce_resolved_open_questions(raw_value: object) -> list[dict]:
    """Normalise the optional `resolved_open_questions` field of a SUCCESS result into a
    clean `list[dict]`. The field names the destination.md open questions a sprint
    resolved; autosprint (not the agent) writes the status marker + receipt back. Most
    sprints resolve nothing, so absent/empty/None all collapse to `[]` — that is the
    common, harmless case. Each entry is expected to carry `section`, `answer`, and
    `adr_ref` string fields; malformed entries (non-dict) are dropped silently rather
    than failing the parse, and the three fields are coerced to stripped strings so a
    downstream writeback never trips on a non-string value."""
    if not isinstance(raw_value, list):
        return []
    cleaned: list[dict] = []
    for entry in raw_value:
        if not isinstance(entry, dict):
            continue
        cleaned.append(
            {
                "section": str(entry.get("section") or "").strip(),
                "answer": str(entry.get("answer") or "").strip(),
                "adr_ref": str(entry.get("adr_ref") or "").strip(),
            }
        )
    return cleaned


def parse_implement_result(raw: str) -> dict:
    """Validate the Implement agent's response against the schema.

    Returns a dict with shape `{"status": "success", "summary": str,
    "resolved_open_questions": list[dict]}` or `{"status": "failure", "reason": str}`.
    Raises `ImplementResponseMalformed` if the response does not follow the contract at
    all (missing RESULT block, unknown status, missing required field).

    `resolved_open_questions` is optional on a SUCCESS result — absent → `[]`, present →
    a list of `{"section", "answer", "adr_ref"}` dicts. It names destination.md open
    questions the sprint resolved; the orchestrator (not the agent) writes the status
    marker + receipt back deterministically.
    """
    parsed = parse_result(raw, "status")
    if parsed is None:
        excerpt = raw[-300:] if len(raw) > 300 else raw
        raise ImplementResponseMalformed(f"No parseable ---RESULT--- block found. Last 300 chars: {excerpt!r}")
    status = parsed.get("status")
    if status == "success":
        summary = str(parsed.get("summary") or "").strip()
        if not summary:
            raise ImplementResponseMalformed("status=success but 'summary' is missing or empty")
        return {"status": "success", "summary": summary, "resolved_open_questions": _coerce_resolved_open_questions(parsed.get("resolved_open_questions"))}
    if status == "failure":
        reason = str(parsed.get("reason") or "").strip() or "(no reason given)"
        return {"status": "failure", "reason": reason}
    raise ImplementResponseMalformed(f"Unknown or missing status: {status!r}")


# Refusal detection lives here (a leaf parsing concern) so both the implement
# phase and the run-log escalation check can use it without either importing
# the other. The model paraphrases its refusal in the reason field while the
# raw response usually quotes the safety reminder verbatim, so matching scans
# both texts.
_REFUSAL_PATTERN_PHRASES: tuple[str, ...] = (
    "refuse to improve",
    "refuse to augment",
    "refusing to improve",
    "refusing to augment",
    "instructed to refuse",
    "system directive",
    "must refuse",
    "system-reminder",
    "system reminder",
    "forbids improving",
    "forbids augmenting",
    "forbidding augmenting",
    "forbidding code augmentation",
    "do not improve code",
)


def detect_refusal_pattern(reason: str, raw_response: str = "") -> bool:
    """Return True if a failure looks like the known misread of Read-tool safety reminders as a refusal directive. Checks the parsed reason AND the raw response together — the agent paraphrases its refusal in the reason field (e.g. "Refused to perform sprint edits") while the raw response typically quotes the canonical reminder verbatim ("MUST refuse to improve or augment"). Catching either path is what lifted the live fallback hit-rate substantially. Matching is case-insensitive on the phrase list."""
    haystack = f"{reason}\n{raw_response}".lower()
    return any(phrase in haystack for phrase in _REFUSAL_PATTERN_PHRASES)
