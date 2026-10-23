"""Safe columns of one job for the operator status command (reads only)."""

from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from application.queries.job_status import JobStatusRecord
from infrastructure.messaging.envelopes import outbox_id_for, transcribe_outbox_id_for

# Never selected: jobs.text, voice_jobs.file_id/file_unique_id/s3_key, author_name,
# outbox.payload. A voice job that has no transcript yet is handed over by its
# TranscribeVoice row; a transcribed one (and a text job) by its CreateTask row.
STATUS_QUERY = text(
    """
    SELECT j.kind, j.status, j.task_id IS NOT NULL, j.last_error,
           v.stage, v.attempts, v.error_code,
           o.attempts, o.next_attempt_at, o.published_at IS NOT NULL, o.last_error
    FROM jobs j
    LEFT JOIN voice_jobs v ON v.job_id = j.id
    LEFT JOIN outbox o ON o.id = CASE
        WHEN v.stage IS NOT NULL AND v.stage <> 'done' THEN :transcribe_id
        ELSE :create_id END
    WHERE j.id = :job_id
    """
).bindparams(
    # Typed binds: untyped ones inside CASE reach PostgreSQL as text and
    # `uuid = text` fails; the asyncpg dialect renders a ::UUID cast for them.
    bindparam("job_id", type_=Uuid),
    bindparam("create_id", type_=Uuid),
    bindparam("transcribe_id", type_=Uuid),
)


async def read_job_status(
    session: AsyncSession, job_id: UUID
) -> JobStatusRecord | None:
    row = (
        await session.execute(
            STATUS_QUERY,
            {
                "job_id": job_id,
                "create_id": outbox_id_for(job_id),
                "transcribe_id": transcribe_outbox_id_for(job_id),
            },
        )
    ).one_or_none()
    return JobStatusRecord(*row) if row else None
