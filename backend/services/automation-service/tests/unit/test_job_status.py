"""Operator view of one job: attempts, a whitelisted error code, no message content."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import Table, Uuid
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import BindParameter, ColumnClause

from application.queries.job_status import (
    JobStatusRecord,
    error_code_of,
    render,
    view_of,
)
from domain.notices import VOICE_FAILURES
from infrastructure.db.job_status_reader import status_query

DUE = datetime(2026, 1, 1, 12, 0, 5, tzinfo=UTC)


def record(**overrides: object) -> JobStatusRecord:
    values: dict[str, object] = dict(
        kind="text",
        status="accepted",
        task_created=False,
        job_error=None,
        stage=None,
        processing_attempts=None,
        voice_error=None,
        delivery_attempts=0,
        next_attempt_at=None,
        published=False,
        outbox_error=None,
    )
    values.update(overrides)
    return JobStatusRecord(**values)  # type: ignore[arg-type]


def fields(rendered: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in rendered.splitlines())


def test_a_text_job_shows_its_delivery_attempts_and_no_processing_attempts() -> None:
    view = view_of(
        record(delivery_attempts=2, next_attempt_at=DUE, outbox_error="publish failed")
    )

    assert fields(render(view)) == {
        "kind": "text",
        "status": "accepted",
        "stage": "-",
        "delivery_attempts": "2",
        "processing_attempts": "-",
        "max_attempts": "3",
        "error_code": "delivery_failed",
        "next_attempt_at": DUE.isoformat(),
        "task_created": "no",
    }


def test_a_failed_voice_job_shows_stage_processing_attempts_and_its_error_code() -> (
    None
):
    view = view_of(
        record(
            kind="voice",
            status="failed",
            job_error="empty_audio",
            stage="failed",
            processing_attempts=1,
            voice_error="empty_audio",
            delivery_attempts=0,
        )
    )

    shown = fields(render(view))
    assert (shown["stage"], shown["processing_attempts"]) == ("failed", "1")
    assert (shown["status"], shown["error_code"]) == ("failed", "empty_audio")
    assert shown["next_attempt_at"] == "-"


def test_a_completed_job_reports_the_task_and_no_next_attempt() -> None:
    view = view_of(record(status="completed", task_created=True, published=True))

    shown = fields(render(view))
    assert (shown["task_created"], shown["next_attempt_at"]) == ("yes", "-")
    assert shown["error_code"] == "-"


@pytest.mark.parametrize("code", sorted(VOICE_FAILURES))
def test_every_known_voice_error_code_is_shown_as_it_is(code: str) -> None:
    assert error_code_of(record(kind="voice", voice_error=code)) == code


def test_an_unlisted_error_text_is_never_echoed() -> None:
    secret = "Купить молоко token=123456:AAA"

    assert error_code_of(record(job_error=secret)) == "unknown"
    assert error_code_of(record(voice_error=secret)) == "unknown"
    assert secret not in render(view_of(record(job_error=secret)))


def test_a_stale_delivery_error_of_a_published_row_is_not_reported() -> None:
    assert error_code_of(record(published=True, outbox_error="publish failed")) is None


def test_status_projection_contains_only_safe_columns() -> None:
    query = status_query(uuid4())
    selected = {
        (column.table.name, column.name)
        for expression in query.selected_columns
        for column in visitors.iterate(expression)
        if isinstance(column, ColumnClause) and isinstance(column.table, Table)
    }
    assert selected == {
        ("jobs", "kind"),
        ("jobs", "status"),
        ("jobs", "task_id"),
        ("jobs", "last_error"),
        ("voice_jobs", "stage"),
        ("voice_jobs", "attempts"),
        ("voice_jobs", "error_code"),
        ("outbox", "attempts"),
        ("outbox", "next_attempt_at"),
        ("outbox", "published_at"),
        ("outbox", "last_error"),
    }


def test_uuid_parameters_inherit_the_mapped_column_type() -> None:
    from infrastructure.messaging.envelopes import (
        outbox_id_for,
        transcribe_outbox_id_for,
    )

    job_id = uuid4()
    parameters = {
        bind.value: bind.type
        for bind in visitors.iterate(status_query(job_id))
        if isinstance(bind, BindParameter) and isinstance(bind.value, type(job_id))
    }
    assert set(parameters) == {
        job_id,
        outbox_id_for(job_id),
        transcribe_outbox_id_for(job_id),
    }
    assert all(isinstance(type_, Uuid) for type_ in parameters.values())
