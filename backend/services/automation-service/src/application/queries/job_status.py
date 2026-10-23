"""Operator view of one job: state, attempts and a whitelisted error code.

The record holds only safe columns. The message text, Telegram file identity, object
key, author name and raw error texts never reach it: an error is shown as a code
from a closed list, anything else is `unknown`.
"""

from dataclasses import dataclass
from datetime import datetime

from application.commands.transcribe_voice import MAX_ATTEMPTS
from domain.notices import VOICE_FAILURES

DELIVERY_FAILED = "delivery_failed"  # the hand-off to the broker or stream failed
UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class JobStatusRecord:
    kind: str
    status: str  # jobs.status: accepted, completed or failed
    task_created: bool
    job_error: str | None
    stage: str | None  # voice record only
    processing_attempts: int | None  # voice record only
    voice_error: str | None
    delivery_attempts: int | None  # None: the job has no outbox row
    next_attempt_at: datetime | None
    published: bool
    outbox_error: str | None


@dataclass(frozen=True, slots=True)
class JobStatusView:
    kind: str
    status: str
    stage: str | None
    delivery_attempts: int | None
    processing_attempts: int | None
    max_attempts: int
    error_code: str | None
    next_attempt_at: datetime | None
    task_created: bool


def error_code_of(record: JobStatusRecord) -> str | None:
    """The first recorded error as a code from the closed list."""
    stale = record.published  # the error of a row that was published later is old
    raw = (
        record.voice_error
        or record.job_error
        or (None if stale else record.outbox_error)
    )
    if raw is None:
        return None
    if raw in VOICE_FAILURES:
        return raw
    return DELIVERY_FAILED if raw.startswith("publish failed") else UNKNOWN


def view_of(record: JobStatusRecord) -> JobStatusView:
    return JobStatusView(
        kind=record.kind,
        status=record.status,
        stage=record.stage,
        delivery_attempts=record.delivery_attempts,
        processing_attempts=record.processing_attempts,
        max_attempts=MAX_ATTEMPTS,
        error_code=error_code_of(record),
        next_attempt_at=None if record.published else record.next_attempt_at,
        task_created=record.task_created,
    )


def render(view: JobStatusView) -> str:
    """`key=value` lines, one per field; `-` for a value the job does not have."""

    def shown(value: object) -> str:
        return "-" if value is None else str(value)

    return "\n".join(
        (
            f"kind={view.kind}",
            f"status={view.status}",
            f"stage={shown(view.stage)}",
            f"delivery_attempts={shown(view.delivery_attempts)}",
            f"processing_attempts={shown(view.processing_attempts)}",
            f"max_attempts={view.max_attempts}",
            f"error_code={shown(view.error_code)}",
            "next_attempt_at="
            + shown(view.next_attempt_at and view.next_attempt_at.isoformat()),
            f"task_created={'yes' if view.task_created else 'no'}",
        )
    )
