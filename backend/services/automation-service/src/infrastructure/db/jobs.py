"""Jobs and their outbox: accepted atomically, limits counted in PostgreSQL."""

from datetime import UTC, datetime
from uuid import UUID

from kernel_platform.outbox import enqueue, outbox_table
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from application.ports import (
    Accepted,
    AcceptOutcome,
    NewJob,
    NewVoiceJob,
    OutboxMessage,
)
from domain.jobs import JobKind, JobLimits, JobStatus, VoiceStage
from infrastructure.db.inbox import metadata

# Serializes acceptance so the 100 / 10 limits cannot be overshot by racing updates
# (the same advisory-lock approach as ADR-0022); released when the transaction ends.
_ACCEPT_LOCK_KEY = 7_204_122

jobs = Table(
    "jobs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("bot_id", BigInteger, nullable=False),
    Column("chat_id", BigInteger, nullable=False),
    Column("message_id", BigInteger, nullable=False),
    Column("author_id", Uuid, nullable=False),
    Column("author_telegram_id", BigInteger, nullable=False),
    Column("author_name", Text, nullable=False),
    Column("text", Text),  # a voice job gets its text from the transcript
    Column("kind", Text, nullable=False, server_default=JobKind.TEXT.value),
    Column("status", Text, nullable=False),
    Column("task_id", Uuid),
    Column("last_error", Text),
    Column("message_date", DateTime(timezone=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("acked_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("bot_id", "chat_id", "message_id", name="jobs_source_key"),
    CheckConstraint(
        "status IN ('accepted', 'completed', 'failed')", name="jobs_status_check"
    ),
    CheckConstraint("kind IN ('text', 'voice')", name="jobs_kind_check"),
    CheckConstraint("kind = 'voice' OR text IS NOT NULL", name="jobs_text_present"),
    Index(
        "jobs_unfinished",
        "author_id",
        postgresql_where=text("status = 'accepted'"),
    ),
)

voice_jobs = Table(
    "voice_jobs",
    metadata,
    Column("job_id", Uuid, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
    Column("file_id", Text, nullable=False),
    Column("file_unique_id", Text, nullable=False),
    Column("duration_seconds", Integer, nullable=False),
    Column("file_size_bytes", BigInteger),
    Column("mime_type", Text),
    Column("stage", Text, nullable=False),
    Column("s3_key", Text),
    Column("audio_bytes", BigInteger),
    Column("audio_sha256", Text),
    Column("language", Text),
    Column("error_code", Text),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("cold_start", Boolean, nullable=False, server_default=text("false")),
    Column("started_at", DateTime(timezone=True)),
    Column("stored_at", DateTime(timezone=True)),
    Column("stt_started_at", DateTime(timezone=True)),
    Column("stt_finished_at", DateTime(timezone=True)),
    CheckConstraint(
        "stage IN ('queued', 'stored', 'done', 'failed')",
        name="voice_jobs_stage_check",
    ),
    Index(
        "voice_jobs_unfinished",
        "stage",
        postgresql_where=text("stage IN ('queued', 'stored')"),
    ),
)

outbox = outbox_table(metadata)


class SqlJobStore:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        limits: JobLimits = JobLimits(),
    ) -> None:
        self._sessions = sessions
        self._limits = limits

    async def accept(self, job: NewJob, message: OutboxMessage) -> Accepted:
        return await self._accept(job, message, text=job.text, voice=None)

    async def accept_voice(self, job: NewVoiceJob, message: OutboxMessage) -> Accepted:
        return await self._accept(job, message, text=None, voice=job)

    async def mark_acked(self, job_id: UUID, at: datetime) -> None:
        async with self._sessions() as session:
            await session.execute(
                update(jobs)
                .where(jobs.c.id == job_id, jobs.c.acked_at.is_(None))
                .values(acked_at=at)
            )
            await session.commit()

    async def _accept(
        self,
        job: NewJob | NewVoiceJob,
        message: OutboxMessage,
        *,
        text: str | None,
        voice: NewVoiceJob | None,
    ) -> Accepted:
        now = datetime.now(UTC)
        async with self._sessions() as session:
            await session.execute(select(func.pg_advisory_xact_lock(_ACCEPT_LOCK_KEY)))
            existing = (
                await session.execute(
                    select(jobs.c.id, jobs.c.acked_at).where(
                        jobs.c.bot_id == job.bot_id,
                        jobs.c.chat_id == job.chat_id,
                        jobs.c.message_id == job.message_id,
                    )
                )
            ).one_or_none()
            if (
                existing is not None
            ):  # checked before the limits: a retry is no new load
                return Accepted(
                    AcceptOutcome.DUPLICATE, existing.id, existing.acked_at is not None
                )
            over = await self._over_limit(session, job.author_id, voice is not None)
            if over is not None:
                return Accepted(over, job.id, False)
            await session.execute(
                insert(jobs).values(
                    id=job.id,
                    bot_id=job.bot_id,
                    chat_id=job.chat_id,
                    message_id=job.message_id,
                    author_id=job.author_id,
                    author_telegram_id=job.author_telegram_id,
                    author_name=job.author_name,
                    text=text,
                    kind=(JobKind.VOICE if voice else JobKind.TEXT).value,
                    status=JobStatus.ACCEPTED.value,
                    message_date=job.message_date,
                    received_at=job.received_at,
                    created_at=now,
                    updated_at=now,
                )
            )
            if voice is not None:
                await session.execute(
                    insert(voice_jobs).values(
                        job_id=voice.id,
                        file_id=voice.file_id,
                        file_unique_id=voice.file_unique_id,
                        duration_seconds=voice.duration_seconds,
                        file_size_bytes=voice.file_size_bytes,
                        mime_type=voice.mime_type,
                        stage=VoiceStage.QUEUED.value,
                    )
                )
            await enqueue(
                session,
                outbox,
                entry_id=message.id,
                type=message.type,
                routing_key=message.routing_key,
                payload=message.payload,
                correlation_id=message.correlation_id,
                now=now,
            )
            await session.commit()  # the ACK is sent only after this
        return Accepted(AcceptOutcome.ACCEPTED, job.id, False)

    async def _over_limit(
        self, session: AsyncSession, author_id: UUID, voice: bool
    ) -> AcceptOutcome | None:
        """Common limits first, then the voice ones: all in the locked transaction."""
        unfinished = jobs.c.status == JobStatus.ACCEPTED.value
        unfinished_voice = unfinished & (jobs.c.kind == JobKind.VOICE.value)
        own = jobs.c.author_id == author_id
        total, mine, voice_total, voice_mine = (
            await session.execute(
                select(
                    func.count().filter(unfinished),
                    func.count().filter(unfinished, own),
                    func.count().filter(unfinished_voice),
                    func.count().filter(unfinished_voice, own),
                )
            )
        ).one()
        if total >= self._limits.total or mine >= self._limits.per_author:
            return AcceptOutcome.LIMIT
        if voice and (
            voice_total >= self._limits.voice_total
            or voice_mine >= self._limits.voice_per_author
        ):
            return AcceptOutcome.VOICE_LIMIT
        return None
