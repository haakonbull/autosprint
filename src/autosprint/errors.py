"""Structured error context helper and autosprint exception types.

Phase exceptions (PhaseFailedError, StopRequested, WaypointReached) and the
RevertReason enum live here so any phase module (plan/implement/test) can
raise or catch them without depending on orchestrator. Mirrors the role of
`io` in the stdlib — a small leaf module of types everyone shares.
"""

from __future__ import annotations

from enum import Enum


class StopSignalDetected(BaseException):
    """Raised by the Copilot dispatcher when a stop-now control file is detected
    while an LLM call is in flight.  Inherits from BaseException (not Exception)
    so it propagates through every ``except Exception`` handler in the dispatch →
    orchestrator call chain without being accidentally swallowed, and surfaces
    directly in pit_loop's stop-handling clause.
    """


class RevertReason(str, Enum):
    """Why a sprint was reverted. Drives the adaptive task-count cap and the post-revert planner hint. Only TEST_FAILURE and IMPLEMENT_REFUSED shrink the cap — formatting hiccups (IMPLEMENT_MALFORMED) are autosprint's fault and shouldn't punish the loop."""

    TEST_FAILURE = "test_failure"  # pytest exited non-zero after the Implement phase
    IMPLEMENT_MALFORMED = "implement_malformed"  # parser couldn't extract RESULT block (after retry)
    IMPLEMENT_REFUSED = "implement_refused"  # agent returned status=failure with a refusal-pattern reason
    IMPLEMENT_FAILED = "implement_failed"  # agent returned status=failure (non-refusal)
    OTHER = "other"


def revert_reason_shrinks_cap(reason: "RevertReason") -> bool:
    """Which revert reasons justify shrinking the adaptive task-count cap? Bundle-size problems (tests failing, agent refusing to work on the group) do. Autosprint bugs (mangled parser output) don't — punishing the loop for our parser would cascade."""
    return reason in (RevertReason.TEST_FAILURE, RevertReason.IMPLEMENT_REFUSED)


class PhaseFailedError(Exception):
    """Raised when a PIT phase (Plan, Implement, Test) fails after retries. `revert_reason` classifies why so the pit-loop can decide whether to shrink the adaptive task-count cap and whether the post-revert planner hint should surface this failure."""

    def __init__(self, message: str, revert_reason: "RevertReason" = None) -> None:  # type: ignore[assignment]
        super().__init__(message)
        self.revert_reason = revert_reason if revert_reason is not None else RevertReason.OTHER


class StopRequested(Exception):
    """Raised when a stop control file is detected mid-sprint. Carries the stop
    kind ('soft' or 'immediate') so the pit_loop can react correctly.
    """

    def __init__(self, kind: str) -> None:
        super().__init__(f"Stop requested: {kind}")
        self.kind = kind


class WaypointReached(Exception):
    """Raised by the Plan phase when the team lead signals `waypoint_reached: true`. The pit_loop catches this, halts cleanly with the rationale captured. Mirrors the StopRequested pattern — a clean exit signal carried by an exception so the orchestrator's happy-path doesn't need a four-tuple return signature."""

    def __init__(self, rationale: str) -> None:
        super().__init__(f"Waypoint reached: {rationale}")
        self.rationale = rationale


def add_context(error: Exception, message: str) -> Exception:
    """Append a context breadcrumb to an exception.

    Each catch-and-rethrow adds one entry. When the error finally
    reaches the top-level handler, error._context is a list showing
    the full path the error bubbled through, deepest first.
    """
    if not hasattr(error, "_context"):
        error._context = [str(error)]
    error._context.append(message)
    error.args = (" -> ".join(reversed(error._context)),) + error.args[1:]
    return error
