"""The Job aggregate and its voice record in PostgreSQL."""

from collections.abc import Callable
from uuid import UUID

from kernel_domain.entity import Entity
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from domain.entities.job import Job
from domain.entities.voice_message import VoiceMessage
from domain.job_limits import JobCounts
from domain.value_objects.job_id import JobId
from domain.value_objects.job_status import JobKind, JobStatus, VoiceStage
from infrastructure.db.models import JobModel, VoiceJobModel

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
        unfinished = JobModel.status == JobStatus.ACCEPTED.value
        unfinished_voice = unfinished & (JobModel.kind == JobKind.VOICE.value)
        own = JobModel.author_id == author_id
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
            await self._session.scalars(
                _select_job().where(
                    JobModel.bot_id == bot_id,
                    JobModel.chat_id == chat_id,
                    JobModel.message_id == message_id,
                )
            )
        ).one_or_none()
        return _job(row) if row else None

    async def get(self, job_id: JobId, *, lock: bool = False) -> Job | None:
        query = _select_job().where(JobModel.id == job_id.value)
        if lock:
            query = query.with_for_update(of=JobModel)
        row = (await self._session.scalars(query)).one_or_none()
        return _job(row) if row else None

    async def add(self, job: Job) -> None:
        model = JobModel(
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
        if job.voice is not None:
            model.voice = VoiceJobModel(
                job_id=job.id.value,
                file_id=job.voice.file_id,
                file_unique_id=job.voice.file_unique_id,
                duration_seconds=job.voice.duration_seconds,
                file_size_bytes=job.voice.file_size_bytes,
                mime_type=job.voice.mime_type,
                stage=job.voice.stage.value,
            )
        self._session.add(model)
        await self._session.flush()
        self._track(job)

    async def update(self, job: Job) -> None:
        model = (
            await self._session.scalars(
                _select_job().where(JobModel.id == job.id.value)
            )
        ).one_or_none()
        if model is not None:
            model.text = job.text
            model.status = job.status.value
            model.task_id = job.task_id
            model.last_error = job.last_error
            model.acked_at = job.acked_at
            model.updated_at = job.updated_at
            voice = job.voice
            stored = model.voice
            if voice is not None and stored is not None:
                stored.stage = voice.stage.value
                stored.s3_key = voice.s3_key
                stored.audio_bytes = voice.audio_bytes
                stored.audio_sha256 = voice.audio_sha256
                stored.language = voice.language
                stored.error_code = voice.error_code
                stored.attempts = voice.attempts
                stored.cold_start = voice.cold_start
                stored.started_at = voice.started_at
                stored.stored_at = voice.stored_at
                stored.stt_started_at = voice.stt_started_at
                stored.stt_finished_at = voice.stt_finished_at
            await self._session.flush()
        self._track(job)


def _select_job() -> Select[JobModel]:
    return select(JobModel).options(joinedload(JobModel.voice))


def _job(row: JobModel) -> Job:
    stored = row.voice
    voice = (
        VoiceMessage(
            file_id=stored.file_id,
            file_unique_id=stored.file_unique_id,
            duration_seconds=stored.duration_seconds,
            file_size_bytes=stored.file_size_bytes,
            mime_type=stored.mime_type,
            stage=VoiceStage(stored.stage),
            s3_key=stored.s3_key,
            audio_bytes=stored.audio_bytes,
            audio_sha256=stored.audio_sha256,
            language=stored.language,
            error_code=stored.error_code,
            attempts=stored.attempts,
            cold_start=stored.cold_start,
            started_at=stored.started_at,
            stored_at=stored.stored_at,
            stt_started_at=stored.stt_started_at,
            stt_finished_at=stored.stt_finished_at,
        )
        if stored is not None
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
