"""Validated report shared by both SDK adapters and the ask CLI."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, model_validator

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, strict=True)]


class AskOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["success", "blocked", "failed"]
    summary: NonEmptyText
    files_changed: list[NonEmptyText]
    tests_run: list[NonEmptyText]
    blockers: list[NonEmptyText]

    @model_validator(mode="after")
    def consistent_status(self) -> Self:
        if self.status == "success" and self.blockers:
            raise ValueError("A successful report must not have blockers.")
        if self.status != "success" and not self.blockers:
            raise ValueError("A blocked or failed report must explain the blockers.")
        return self


class AskReport(AskOutcome):
    schema_version: Literal[1] = 1
    agent: str
    assistant: str = ""
    model: str = ""
    reasoning_effort: str | None = None
    tools: str
    target_repo: str
    raw_response: str = ""
    structured: bool = False


ASK_REPORT_DESCRIPTION = "Submit the final report once the requested task is finished. " "Use success for a completed task/review, blocked for a missing prerequisite or human decision, " "and failed for unsuccessful work. A completed review can succeed even when it finds bugs: " "describe the findings in your response and summary. Include every required list, using [] " "when empty. tests_run must include commands AND their observed outcomes; never claim unrun tests. " "Do not claim success with outstanding blockers."


def capture_ask_report(args: dict, capture: dict) -> str | None:
    """Both backends reject malformed reports without inventing missing evidence."""
    try:
        report = AskOutcome.model_validate(args)
    except ValidationError as exc:
        return f"Invalid ask report. Correct the fields and call submit_ask_report again: {exc.errors(include_input=False, include_url=False)}"
    capture.update(report.model_dump())
    return None
