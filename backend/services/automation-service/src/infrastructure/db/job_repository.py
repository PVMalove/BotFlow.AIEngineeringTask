"""The Job aggregate and its voice record in PostgreSQL."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from kernel_domain.entity import Entity
from sqlalchemy import Row, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from domain.entities.job import Job
from domain.entities.voice_message import VoiceMessage
from domain.job_limits import JobCounts
from domain.value_objects.job_id import JobId
from domain.value_objects.job_status import JobKind, JobStatus, VoiceStage
from infrastructure.db.tables import jobs, voice_jobs

# Serializes acceptance so the 100 / 10 limits cannot be overshot by racing updates
# (the same advisory-lock approach as ADR-0022); released when the transaction ends.
_ACCEPT_LOCK_KEY = 7_204_122


class SqlJobRepository:
    def __init__(
        self, session: AsyncSession, track: Callable[[Entity[JobId]], None]
    ) -> None:
        self._session = session
        self._track = track

    async def lock_acceptance(self) -> None:
        await self._session.execute(
            select(func.pg_advisory_xact_lock(_ACCEPT_LOCK_KEY))
        )

    async def count_unfinished(self, author_id: UUID) -> JobCounts:
        unfinished = jobs.c.status == JobStatus.ACCEPTED.value
        unfinished_voice = unfinished & (jobs.c.kind == JobKind.VOICE.value)
        own = jobs.c.author_id == author_id
        total, mine, voice_total, voice_mine = (
            await self._session.execute(
                select(
                    func.count().filter(unfinished),
                    func.count().filter(unfinished, own),
                    func.count().filter(unfinished_voice),
                    func.count().filter(unfinished_voice, own),
                )
            )
        ).one()
        return JobCounts(total, mine, voice_total, voice_mine)

    async def get_by_source(
        self, bot_id: int, chat_id: int, message_id: int
    ) -> Job | None:
        row = (
            await self._session.execute(
                _select_job().where(
                    jobs.c.bot_id == bot_id,
                    jobs.c.chat_id == chat_id,
                    jobs.c.message_id == message_id,
                )
            )
        ).one_or_none()
        return _job(row) if row else None

    async def get(self, job_id: JobId, *, lock: bool = False) -> Job | None:
        query = _select_job().where(jobs.c.id == job_id.value)
        if lock:
            query = query.with_for_update(of=jobs)
        row = (await self._session.execute(query)).one_or_none()
        return _job(row) if row else None

    async def add(self, job: Job) -> None:
        await self._session.execute(
            insert(jobs).values(
                id=job.id.value,
                bot_id=job.bot_id,
                chat_id=job.chat_id,
                message_id=job.message_id,
                author_id=job.author_id,
                author_telegram_id=job.author_telegram_id,
                author_name=job.author_name,
                text=job.text,
                kind=job.kind.value,
                status=job.status.value,
                message_date=job.message_date,
                received_at=job.received_at,
                created_at=job.created_at,
                updated_at=job.updated_at,
            )
        )
        if job.voice is not None:
            await self._session.execute(
                insert(voice_jobs).values(
                    job_id=job.id.value,
                    file_id=job.voice.file_id,
                    file_unique_id=job.voice.file_unique_id,
                    duration_seconds=job.voice.duration_seconds,
                    file_size_bytes=job.voice.file_size_bytes,
                    mime_type=job.voice.mime_type,
                    stage=job.voice.stage.value,
                )
            )
        self._track(job)

    async def update(self, job: Job) -> None:
        await self._session.execute(
            update(jobs)
            .where(jobs.c.id == job.id.value)
            .values(
                text=job.text,
                status=job.status.value,
                task_id=job.task_id,
                last_error=job.last_error,
                acked_at=job.acked_at,
                updated_at=job.updated_at,
            )
        )
        voice = job.voice
        if voice is not None:
            await self._session.execute(
                update(voice_jobs)
                .where(voice_jobs.c.job_id == job.id.value)
                .values(
                    stage=voice.stage.value,
                    s3_key=voice.s3_key,
                    audio_bytes=voice.audio_bytes,
                    audio_sha256=voice.audio_sha256,
                    language=voice.language,
                    error_code=voice.error_code,
                    attempts=voice.attempts,
                    cold_start=voice.cold_start,
                    started_at=voice.started_at,
                    stored_at=voice.stored_at,
                    stt_started_at=voice.stt_started_at,
                    stt_finished_at=voice.stt_finished_at,
                )
            )
        self._track(job)


def _select_job() -> Any:
    return select(
        jobs,
        voice_jobs.c.file_id,
        voice_jobs.c.file_unique_id,
        voice_jobs.c.duration_seconds,
        voice_jobs.c.file_size_bytes,
        voice_jobs.c.mime_type,
        voice_jobs.c.stage,
        voice_jobs.c.s3_key,
        voice_jobs.c.audio_bytes,
        voice_jobs.c.audio_sha256,
        voice_jobs.c.language,
        voice_jobs.c.error_code,
        voice_jobs.c.attempts,
        voice_jobs.c.cold_start,
        voice_jobs.c.started_at,
        voice_jobs.c.stored_at,
        voice_jobs.c.stt_started_at,
        voice_jobs.c.stt_finished_at,
    ).select_from(jobs.outerjoin(voice_jobs, voice_jobs.c.job_id == jobs.c.id))


def _job(row: Row[Any]) -> Job:
    voice = (
        VoiceMessage(
            file_id=row.file_id,
            file_unique_id=row.file_unique_id,
            duration_seconds=row.duration_seconds,
            file_size_bytes=row.file_size_bytes,
            mime_type=row.mime_type,
            stage=VoiceStage(row.stage),
            s3_key=row.s3_key,
            audio_bytes=row.audio_bytes,
            audio_sha256=row.audio_sha256,
            language=row.language,
            error_code=row.error_code,
            attempts=row.attempts,
            cold_start=row.cold_start,
            started_at=row.started_at,
            stored_at=row.stored_at,
            stt_started_at=row.stt_started_at,
            stt_finished_at=row.stt_finished_at,
        )
        if row.file_id is not None
        else None
    )
    return Job.reconstitute(
        JobId(row.id),
        bot_id=row.bot_id,
        chat_id=row.chat_id,
        message_id=row.message_id,
        author_id=row.author_id,
        author_telegram_id=row.author_telegram_id,
        author_name=row.author_name,
        kind=JobKind(row.kind),
        text=row.text,
        status=JobStatus(row.status),
        task_id=row.task_id,
        last_error=row.last_error,
        message_date=row.message_date,
        received_at=row.received_at,
        acked_at=row.acked_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        voice=voice,
    )
