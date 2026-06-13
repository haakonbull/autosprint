"""Extracted from the original autosprint.reporting.run_log module."""

from datetime import UTC, datetime

from autosprint.config import config
from autosprint.util.errors import add_context
from autosprint.util.output import printlev
from autosprint.util.paths import (
    DESTINATION_FILENAME,
)

_RESOLVED_QUESTIONS_HEADING = "## AI-resolved questions"
_RESOLVED_QUESTIONS_PLACEHOLDER = "_No questions resolved yet._"


def _section_bounds(lines: list[str], heading_text: str) -> tuple[int, int] | None:
    """Locate a ``## <heading_text>`` section in ``lines`` (a destination.md split on
    ``\\n``). Returns ``(heading_index, end_index)`` where ``end_index`` is the index of
    the next line beginning with ``## `` (or ``len(lines)`` at EOF) — i.e. the section
    body is ``lines[heading_index + 1 : end_index]``. Heading matching is tolerant: the
    caller's ``heading_text`` may arrive with or without a leading ``## ``, and the
    comparison is case-insensitive and whitespace-stripped. Returns ``None`` when no
    matching ``## `` heading is found."""
    wanted = heading_text.strip()
    if wanted.startswith("## "):
        wanted = wanted[3:].strip()
    wanted_lc = wanted.lower()
    heading_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("## ") and line[3:].strip().lower() == wanted_lc:
            heading_idx = i
            break
    if heading_idx == -1:
        return None
    end_idx = len(lines)
    for j in range(heading_idx + 1, len(lines)):
        if lines[j].startswith("## "):
            end_idx = j
            break
    return heading_idx, end_idx


def apply_destination_resolutions(resolutions: list[dict]) -> None:
    """Write resolved-open-question status markers + receipts into the target repo's
    ``destination.md`` — the deterministic, code-side half of the open-question
    resolution protocol (the agent only NAMES what it resolved via the
    ``resolved_open_questions`` field; this function does the mechanical writes).

    For each entry (``{"section", "answer", "adr_ref"}``):

    1. **Status marker (write #2).** Append a blockquote at the END of the named
       ``## <section>`` (immediately before the next ``## `` heading, or EOF):
       ``> **Status:** resolved <today> — <answer>. See `adr.md` <adr_ref>.``. The
       human-authored ``*(Open — autosprint to decide.)*`` line is left intact — the
       protocol is append-only, the blockquote is the resolved signal.
    2. **Receipt (write #3).** Append a bullet to ``## AI-resolved questions``:
       ``- **<section>:** <answer>. See `adr.md` <adr_ref>.``. On the first receipt the
       seed placeholder ``_No questions resolved yet._`` is deleted in the same write.

    A missing ``## <section>`` heading is logged loudly (level=100) and skipped — it does
    NOT raise and does NOT fail the sprint (the code work is already correct). Whole
    function is a no-op when ``resolutions`` is empty or in FAKE_IMPLEMENT mode.
    ``destination.md`` is read once and written back once after all entries are applied.
    """
    if not resolutions:
        return
    if config.FAKE_IMPLEMENT:
        return
    path = config.TARGET_REPO_PATH / DESTINATION_FILENAME
    try:
        if not path.exists():
            printlev(f"[writeback] ⚠️  destination.md not found at {path} — cannot write back {len(resolutions)} resolved open question(s). Skipping.", level=100)
            return
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        lines = path.read_text(encoding="utf-8").split("\n")
        for entry in resolutions:
            section = str(entry.get("section") or "").strip()
            answer = str(entry.get("answer") or "").strip()
            adr_ref = str(entry.get("adr_ref") or "").strip()
            if not section:
                printlev("[writeback] ⚠️  Resolution entry has no 'section' — skipping it.", level=100)
                continue
            bounds = _section_bounds(lines, section)
            if bounds is None:
                printlev(f"[writeback] ⚠️  destination.md has no '## {section}' section — cannot append the resolved-question status marker for it. Skipping this entry (the ADR / code work itself is unaffected).", level=100)
                continue
            _heading_idx, end_idx = bounds
            marker = f"> **Status:** resolved {date} — {answer}. See `adr.md` {adr_ref}."
            # Insert the marker as the last line of the section. A blank line before it
            # keeps the blockquote visually separated from the section's prose.
            insert_at = end_idx
            insertion = ["", marker]
            lines[insert_at:insert_at] = insertion

        # Write #3 — receipt(s) into ## AI-resolved questions. Recompute bounds after
        # the marker inserts above shifted the line indices.
        receipt_bounds = _section_bounds(lines, _RESOLVED_QUESTIONS_HEADING)
        if receipt_bounds is None:
            printlev(f"[writeback] ⚠️  destination.md has no '{_RESOLVED_QUESTIONS_HEADING}' section — cannot append resolution receipts. Status markers were still written.", level=100)
        else:
            _r_heading_idx, r_end_idx = receipt_bounds
            # Drop the seed placeholder if it's still present anywhere in the section.
            placeholder_idx = None
            for k in range(_r_heading_idx + 1, r_end_idx):
                if lines[k].strip() == _RESOLVED_QUESTIONS_PLACEHOLDER:
                    placeholder_idx = k
                    break
            if placeholder_idx is not None:
                del lines[placeholder_idx]
                r_end_idx -= 1
            receipts: list[str] = []
            for entry in resolutions:
                section = str(entry.get("section") or "").strip()
                answer = str(entry.get("answer") or "").strip()
                adr_ref = str(entry.get("adr_ref") or "").strip()
                if not section or _section_bounds(lines, section) is None:
                    # Skip receipts for entries whose section was missing — keeps the
                    # receipt list and the in-section markers consistent.
                    continue
                # Normalise the section tag (drop any leading '## ') so the bullet reads
                # as plain heading text regardless of how the agent supplied it.
                tag = section[3:].strip() if section.startswith("## ") else section
                receipts.append(f"- **{tag}:** {answer}. See `adr.md` {adr_ref}.")
            if receipts:
                # Append after the last non-blank line of the section so the bullets
                # join the existing list cleanly rather than landing after trailing
                # blank lines.
                last_content = _r_heading_idx
                for k in range(_r_heading_idx + 1, r_end_idx):
                    if lines[k].strip():
                        last_content = k
                lines[last_content + 1 : last_content + 1] = receipts

        path.write_text("\n".join(lines), encoding="utf-8")
        printlev(f"[writeback] ✅ Wrote {len(resolutions)} resolved-open-question marker(s)/receipt(s) into destination.md.", level=50)
    except Exception as e:
        raise add_context(e, f"Failed to apply {len(resolutions)} destination.md resolution(s)") from e
