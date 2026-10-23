"""Operator view of one job: attempts, a whitelisted error code, no message content."""

import re
from datetime import UTC, datetime

import pytest

from application.queries.job_status import (
    JobStatusRecord,
    error_code_of,
    render,
    view_of,
)
from domain.notices import VOICE_FAILURES
from infrastructure.db.job_status_reader import STATUS_QUERY

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


def test_the_status_query_reads_no_text_file_id_object_key_or_author_name() -> None:
    sql = STATUS_QUERY.text

    for forbidden in (
        r"\.text\b",
        "file_id",
        "file_unique_id",
        "s3_key",
        "author_name",
    ):
        assert not re.search(forbidden, sql), forbidden
    assert "payload" not in sql
