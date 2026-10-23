"""Minimal projection for the operator status command (reads only)."""

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.queries.job_status import JobStatusRecord
from infrastructure.db.models import JobModel, OutboxModel, VoiceJobModel
from infrastructure.messaging.envelopes import outbox_id_for, transcribe_outbox_id_for

type StatusQuery = Select[
    str,
    str,
    bool,
    str | None,
    str | None,
    int | None,
    str | None,
    int | None,
    datetime | None,
    bool,
    str | None,
]


def status_query(job_id: UUID) -> StatusQuery:
    # Each UUID is compared with a mapped UUID column, so asyncpg receives typed
    # parameters. Never load an entity here: its private content must stay in DB.
    processing = VoiceJobModel.stage.is_not(None) & (VoiceJobModel.stage != "done")
    creating = VoiceJobModel.stage.is_(None) | (VoiceJobModel.stage == "done")
    query = (
        select(
            JobModel.kind,
            JobModel.status,
            JobModel.task_id.is_not(None),
            JobModel.last_error,
            VoiceJobModel.stage,
            VoiceJobModel.attempts,
            VoiceJobModel.error_code,
            OutboxModel.attempts,
            OutboxModel.next_attempt_at,
            OutboxModel.published_at.is_not(None),
            OutboxModel.last_error,
        )
        .outerjoin(JobModel.voice)
        .outerjoin(
            OutboxModel,
            or_(
                and_(processing, OutboxModel.id == transcribe_outbox_id_for(job_id)),
                and_(creating, OutboxModel.id == outbox_id_for(job_id)),
            ),
        )
        .where(JobModel.id == job_id)
    )
    # select overloads infer only ten columns and do not infer outer-join NULLs.
    return cast(StatusQuery, query)


async def read_job_status(
    session: AsyncSession, job_id: UUID
) -> JobStatusRecord | None:
    row = (await session.execute(status_query(job_id))).one_or_none()
    return JobStatusRecord(*row._tuple()) if row else None
