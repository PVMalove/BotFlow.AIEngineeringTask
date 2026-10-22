"""Voice jobs in PostgreSQL: each method is one short transaction.

The STT worker never holds a transaction or a session while it downloads, uploads
or recognises: it calls these methods between the slow steps.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from botflow_platform.outbox import OutboxRow, SqlOutboxTx, enqueue
from sqlalchemy import Boolean, case, delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from application.commands.accept_voice import transcribe_outbox_id_for
from application.commands.transcribe_voice import FAILURES
from application.ports import OutboxMessage, Transcript, VoiceWork
from domain.jobs import JobKind, JobStatus, VoiceStage, result_intent_id_for
from infrastructure.db.jobs import jobs, outbox, voice_jobs
from infrastructure.db.notifications import notification_intents

KIND = "transcription.result"
_UNFINISHED = (VoiceStage.QUEUED.value, VoiceStage.STORED.value)


async def _author_notice(
    session: AsyncSession,
    job_id: UUID,
    chat_id: int,
    author_id: UUID,
    body: str,
    now: datetime,
) -> None:
    await session.execute(
        insert(notification_intents)
        .values(
            id=result_intent_id_for(job_id),
            chat_id=chat_id,  # a private chat id equals the Telegram user id
            member_id=author_id,
            kind=KIND,
            access_version=0,  # a result is not tied to an access decision
            status="pending",
            attempts=0,
            body=body,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=[notification_intents.c.id])
    )


async def _fail(
    session: AsyncSession,
    job_id: UUID,
    chat_id: int,
    author_id: UUID,
    code: str,
    now: datetime,
    marks: dict[str, Any],
) -> bool:
    """Voice record, job (slot freed) and author notice; False if already finished."""
    ended = await session.execute(
        update(voice_jobs)
        .where(voice_jobs.c.job_id == job_id, voice_jobs.c.stage.in_(_UNFINISHED))
        .values(stage=VoiceStage.FAILED.value, error_code=code, **marks)
        .returning(voice_jobs.c.job_id)
    )
    if ended.first() is None:
        return False
    await session.execute(
        update(jobs)
        .where(jobs.c.id == job_id, jobs.c.status == JobStatus.ACCEPTED.value)
        .values(status=JobStatus.FAILED.value, last_error=code, updated_at=now)
    )
    await _author_notice(session, job_id, chat_id, author_id, FAILURES[code], now)
    return True


class SqlVoiceStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def begin(
        self, job_id: UUID, now: datetime, cold_start: bool
    ) -> VoiceWork | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(
                        jobs.c.bot_id,
                        jobs.c.chat_id,
                        jobs.c.message_id,
                        jobs.c.author_id,
                        jobs.c.author_telegram_id,
                        jobs.c.author_name,
                        jobs.c.received_at,
                        jobs.c.status,
                        voice_jobs.c.file_id,
                        voice_jobs.c.mime_type,
                        voice_jobs.c.stage,
                        voice_jobs.c.s3_key,
                    )
                    .join(voice_jobs, voice_jobs.c.job_id == jobs.c.id)
                    .where(jobs.c.id == job_id)
                    .with_for_update(of=voice_jobs)
                )
            ).one_or_none()
            if (
                row is None
                or row.status != JobStatus.ACCEPTED.value
                or row.stage not in _UNFINISHED
            ):
                return None
            attempts = (
                await session.execute(
                    update(voice_jobs)
                    .where(voice_jobs.c.job_id == job_id)
                    .values(
                        attempts=voice_jobs.c.attempts + 1,
                        # The first attempt fixes whether the record met a cold worker.
                        cold_start=case(
                            (
                                voice_jobs.c.started_at.is_(None),
                                literal(cold_start, Boolean),
                            ),
                            else_=voice_jobs.c.cold_start,
                        ),
                        started_at=func.coalesce(voice_jobs.c.started_at, now),
                    )
                    .returning(voice_jobs.c.attempts)
                )
            ).scalar_one()
            await session.commit()
        return VoiceWork(
            job_id=job_id,
            bot_id=row.bot_id,
            chat_id=row.chat_id,
            message_id=row.message_id,
            author_id=row.author_id,
            author_telegram_id=row.author_telegram_id,
            author_name=row.author_name,
            received_at=row.received_at,
            file_id=row.file_id,
            mime_type=row.mime_type,
            stage=row.stage,
            s3_key=row.s3_key,
            attempts=attempts,
        )

    async def checkpoint(
        self, job_id: UUID, key: str, audio_bytes: int, sha256: str, now: datetime
    ) -> None:
        async with self._sessions() as session:
            await session.execute(
                update(voice_jobs)
                .where(
                    voice_jobs.c.job_id == job_id, voice_jobs.c.stage.in_(_UNFINISHED)
                )
                .values(
                    stage=VoiceStage.STORED.value,
                    s3_key=key,
                    audio_bytes=audio_bytes,
                    audio_sha256=sha256,
                    stored_at=now,
                )
            )
            await session.commit()

    async def complete(
        self,
        work: VoiceWork,
        transcript: Transcript,
        create_task: OutboxMessage,
        stt_started_at: datetime,
        stt_finished_at: datetime,
        now: datetime,
    ) -> bool:
        async with self._sessions() as session:
            done = await session.execute(
                update(voice_jobs)
                .where(
                    voice_jobs.c.job_id == work.job_id,
                    voice_jobs.c.stage.in_(_UNFINISHED),
                )
                .values(
                    stage=VoiceStage.DONE.value,
                    language=transcript.language,
                    stt_started_at=stt_started_at,
                    stt_finished_at=stt_finished_at,
                )
                .returning(voice_jobs.c.job_id)
            )
            if done.first() is None:
                return False
            await session.execute(
                update(jobs)
                .where(
                    jobs.c.id == work.job_id,
                    jobs.c.status == JobStatus.ACCEPTED.value,
                    jobs.c.kind == JobKind.VOICE.value,
                )
                .values(text=transcript.text, updated_at=now)
            )
            await enqueue(
                session,
                outbox,
                entry_id=create_task.id,
                type=create_task.type,
                routing_key=create_task.routing_key,
                payload=create_task.payload,
                correlation_id=create_task.correlation_id,
                now=now,
            )
            await (
                session.commit()
            )  # job text, stage and CreateTask become visible together
        return True

    async def fail(
        self,
        work: VoiceWork,
        error_code: str,
        body: str,
        now: datetime,
        stt_started_at: datetime | None = None,
        stt_finished_at: datetime | None = None,
    ) -> bool:
        marks = {
            name: value
            for name, value in (
                ("stt_started_at", stt_started_at),
                ("stt_finished_at", stt_finished_at),
            )
            if value is not None
        }
        async with self._sessions() as session:
            failed = await _fail(
                session,
                work.job_id,
                work.chat_id,
                work.author_id,
                error_code,
                now,
                marks,
            )
            await session.commit()
        return failed


async def fail_voice_job(tx: SqlOutboxTx, row: OutboxRow, error: str) -> None:
    """The hand-off to the STT stream is exhausted: end the job like a final error."""
    if row.correlation_id is None:
        return
    job = (
        await tx.session.execute(
            select(jobs.c.chat_id, jobs.c.author_id).where(
                jobs.c.id == row.correlation_id,
                jobs.c.status == JobStatus.ACCEPTED.value,
            )
        )
    ).one_or_none()
    if job is None:
        return
    await _fail(
        tx.session,
        row.correlation_id,
        job.chat_id,
        job.author_id,
        "unavailable",
        datetime.now(UTC),
        {},
    )


async def replay_voice_job(
    sessions: async_sessionmaker[AsyncSession], job_id: UUID
) -> bool:
    """Run a failed voice job again from the stored object, or from Telegram."""
    now = datetime.now(UTC)
    async with sessions() as session:
        reset = await session.execute(
            update(voice_jobs)
            .where(
                voice_jobs.c.job_id == job_id,
                voice_jobs.c.stage == VoiceStage.FAILED.value,
            )
            .values(
                stage=case(
                    (
                        voice_jobs.c.s3_key.is_not(None),
                        VoiceStage.STORED.value,
                    ),
                    else_=VoiceStage.QUEUED.value,
                ),
                error_code=None,
                attempts=0,
                started_at=None,
                cold_start=False,
            )
            .returning(voice_jobs.c.job_id)
        )
        if reset.first() is None:
            return False
        await session.execute(
            update(jobs)
            .where(jobs.c.id == job_id, jobs.c.status == JobStatus.FAILED.value)
            .values(status=JobStatus.ACCEPTED.value, last_error=None, updated_at=now)
        )
        # The failure notice owns the one result-intent id; a later result needs it.
        await session.execute(
            delete(notification_intents).where(
                notification_intents.c.id == result_intent_id_for(job_id),
                notification_intents.c.kind == KIND,
            )
        )
        await session.execute(
            update(outbox)
            .where(outbox.c.id == transcribe_outbox_id_for(job_id))
            .values(published_at=None, attempts=0, next_attempt_at=now, last_error=None)
        )
        await session.commit()
    return True
