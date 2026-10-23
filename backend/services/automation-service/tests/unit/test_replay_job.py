"""ReplayJobCommandHandler: only a failed job, the limits again, the original ids."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from conftest import NOW, FakeAutomationUnitOfWork, World

from application.commands.replay_job import (
    ReplayJobCommand,
    ReplayJobCommandHandler,
    ReplayOutcome,
)
from domain.entities.job import Job
from domain.entities.voice_message import VoiceMessage
from domain.job_limits import JobLimits
from domain.value_objects.job_id import JobId
from domain.value_objects.job_status import JobStatus, VoiceStage
from infrastructure.messaging.envelopes import outbox_id_for, transcribe_outbox_id_for

AUTHOR = uuid4()
LATER = NOW + timedelta(minutes=1)


def source(message_id: int, author: UUID = AUTHOR) -> dict[str, object]:
    return dict(
        bot_id=1,
        chat_id=2,
        message_id=message_id,
        author_id=author,
        author_telegram_id=2,
        author_name="Ann",
        message_date=NOW,
        received_at=NOW,
        now=NOW,
    )


def text_job(message_id: int = 1, author: UUID = AUTHOR) -> Job:
    job = Job.accept_text(text="Купить молоко", **source(message_id, author))  # type: ignore[arg-type]
    job.pull_events()
    return job


def voice_job(message_id: int = 1, author: UUID = AUTHOR) -> Job:
    job = Job.accept_voice(
        VoiceMessage("file", "uniq", 12, None, "audio/ogg"),
        **source(message_id, author),  # type: ignore[arg-type]
    )
    job.pull_events()
    return job


def failed_text_job(message_id: int = 1) -> Job:
    job = text_job(message_id)
    job.fail_delivery("publish failed: OSError", NOW)
    return job


def failed_voice_job(*, stored: bool = False) -> Job:
    job = voice_job()
    assert job.voice is not None
    if stored:
        job.checkpoint_audio("voice/key", 100, "sha", NOW)
    job.fail_voice("unavailable", "no task", NOW)
    job.pull_events()
    return job


class Harness:
    def __init__(self, *jobs: Job, limits: JobLimits = JobLimits()) -> None:
        self.world = World()
        for job in jobs:
            self.world.jobs[job.id] = job
        self.sut = ReplayJobCommandHandler(
            lambda: FakeAutomationUnitOfWork(self.world),
            limits,
            clock=lambda: LATER,
        )

    async def replay(self, job: Job) -> ReplayOutcome:
        result = await self.sut.execute(ReplayJobCommand(job.id.value))
        return result.value


async def test_a_failed_text_job_keeps_its_id_and_makes_its_own_outbox_row_due() -> (
    None
):
    job = failed_text_job()
    h = Harness(job)

    assert await h.replay(job) is ReplayOutcome.REPLAYED

    stored = h.world.jobs[job.id]
    assert list(h.world.jobs) == [JobId.from_source(1, 2, 1)]
    assert (stored.status, stored.last_error) == (JobStatus.ACCEPTED, None)
    assert h.world.resets == [outbox_id_for(job.id.value)]


async def test_a_repeated_command_is_a_no_op_and_resets_nothing_again() -> None:
    job = failed_text_job()
    h = Harness(job)
    await h.replay(job)
    commits = h.world.commits

    assert await h.replay(job) is ReplayOutcome.UNCHANGED

    assert h.world.resets == [outbox_id_for(job.id.value)]
    assert h.world.commits == commits


@pytest.mark.parametrize("status", [JobStatus.ACCEPTED, JobStatus.COMPLETED])
async def test_a_job_that_did_not_fail_is_left_alone(status: JobStatus) -> None:
    job = text_job()
    if status is JobStatus.COMPLETED:
        job.record_result(uuid4(), NOW)
        job.pull_events()
    h = Harness(job)

    assert await h.replay(job) is ReplayOutcome.UNCHANGED

    assert h.world.jobs[job.id].status is status
    assert (h.world.resets, h.world.commits) == ([], 0)


async def test_an_unknown_job_is_an_error() -> None:
    h = Harness()

    result = await h.sut.execute(ReplayJobCommand(uuid4()))

    assert result.is_err and result.error.code == "unknown_job"


async def test_the_job_limit_is_checked_again_and_a_refusal_changes_nothing() -> None:
    job = failed_text_job()
    others = [text_job(message_id) for message_id in range(10, 12)]
    h = Harness(job, *others, limits=JobLimits(per_author=2))

    result = await h.sut.execute(ReplayJobCommand(job.id.value))

    assert result.is_err and result.error.code == "job_limit"
    assert h.world.jobs[job.id].status is JobStatus.FAILED
    assert (h.world.resets, h.world.commits) == ([], 0)


async def test_the_failed_job_does_not_count_against_its_own_limit() -> None:
    job = failed_text_job()
    other = text_job(10)
    h = Harness(job, other, limits=JobLimits(per_author=2))

    assert await h.replay(job) is ReplayOutcome.REPLAYED


async def test_a_voice_job_is_also_held_by_the_voice_limit_but_a_text_job_is_not() -> (
    None
):
    voice = failed_voice_job()
    busy = [voice_job(message_id) for message_id in range(10, 12)]
    text = failed_text_job(30)
    h = Harness(voice, text, *busy, limits=JobLimits(voice_per_author=2))

    refused = await h.sut.execute(ReplayJobCommand(voice.id.value))

    assert refused.is_err and refused.error.code == "voice_limit"
    assert await h.replay(text) is ReplayOutcome.REPLAYED


@pytest.mark.parametrize(
    ("stored", "stage"), [(True, VoiceStage.STORED), (False, VoiceStage.QUEUED)]
)
async def test_a_failed_voice_job_runs_again_from_its_object_or_from_telegram(
    stored: bool, stage: VoiceStage
) -> None:
    job = failed_voice_job(stored=stored)
    h = Harness(job)

    assert await h.replay(job) is ReplayOutcome.REPLAYED

    voice = h.world.jobs[job.id].voice
    assert voice is not None and (voice.stage, voice.attempts) == (stage, 0)
    assert h.world.resets == [transcribe_outbox_id_for(job.id.value)]


async def test_a_voice_job_with_a_transcript_replays_its_create_task_row() -> None:
    job = voice_job()
    job.complete_transcription("Позвонить маме", "ru", NOW, NOW, NOW)
    job.fail_delivery("publish failed: OSError", NOW)
    job.pull_events()
    h = Harness(job)

    assert await h.replay(job) is ReplayOutcome.REPLAYED

    assert h.world.resets == [outbox_id_for(job.id.value)]
    assert h.world.jobs[job.id].status is JobStatus.ACCEPTED
