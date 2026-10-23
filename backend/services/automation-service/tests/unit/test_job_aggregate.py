"""The Job aggregate: acceptance, results, delivery failure and the voice life cycle."""

from datetime import timedelta
from uuid import uuid4

from conftest import NOW

from domain.entities.job import Job
from domain.entities.voice_message import VoiceMessage
from domain.events.job_domain_event import (
    JobCompleted,
    JobRestarted,
    TextJobAccepted,
    VoiceJobAccepted,
    VoiceJobFailed,
    VoiceTranscribed,
)
from domain.job_limits import JobCounts, JobLimits
from domain.value_objects.job_id import JobId
from domain.value_objects.job_status import JobKind, JobStatus, VoiceStage

AUTHOR = uuid4()
LATER = NOW + timedelta(minutes=1)


def common(message_id: int = 7) -> dict[str, object]:
    return dict(
        bot_id=1,
        chat_id=2,
        message_id=message_id,
        author_id=AUTHOR,
        author_telegram_id=2,
        author_name="Ann",
        message_date=NOW,
        received_at=NOW,
        now=NOW,
    )


def text_job() -> Job:
    return Job.accept_text(text="Купить молоко", **common())  # type: ignore[arg-type]


def voice_job(**voice: object) -> Job:
    return Job.accept_voice(
        VoiceMessage("file", "uniq", 12, None, "audio/ogg", **voice),  # type: ignore[arg-type]
        **common(),  # type: ignore[arg-type]
    )


def test_an_accepted_text_job_is_unfinished_and_raises_its_accepted_event() -> None:
    job = text_job()

    assert job.id == JobId.from_source(1, 2, 7)
    assert (job.kind, job.status, job.text) == (
        JobKind.TEXT,
        JobStatus.ACCEPTED,
        "Купить молоко",
    )
    (event,) = job.pull_events()
    assert isinstance(event, TextJobAccepted) and event.text == "Купить молоко"


def test_an_accepted_voice_job_starts_queued_without_text() -> None:
    job = voice_job()

    assert (job.kind, job.text) == (JobKind.VOICE, None)
    assert job.voice is not None and job.voice.stage is VoiceStage.QUEUED
    (event,) = job.pull_events()
    assert isinstance(event, VoiceJobAccepted)


def test_the_first_ack_is_kept() -> None:
    job = text_job()

    job.mark_acked(NOW)
    job.mark_acked(LATER)

    assert job.acked_at == NOW


def test_a_result_completes_the_job_once_and_raises_one_event() -> None:
    job = text_job()
    job.pull_events()
    task_id = uuid4()

    assert job.record_result(task_id, LATER) is True
    assert job.record_result(uuid4(), LATER) is False

    assert (job.status, job.task_id, job.updated_at) == (
        JobStatus.COMPLETED,
        task_id,
        LATER,
    )
    (event,) = job.pull_events()
    assert isinstance(event, JobCompleted) and event.text == "Купить молоко"


def test_a_late_result_completes_a_job_that_failed_after_its_retries() -> None:
    job = text_job()
    job.fail_delivery("exhausted", LATER)

    assert job.record_result(uuid4(), LATER) is True
    assert (job.status, job.last_error) == (JobStatus.COMPLETED, None)


def test_a_delivery_failure_frees_the_slot_only_for_an_unfinished_job() -> None:
    job = text_job()

    assert job.fail_delivery("exhausted", LATER) is True
    assert (job.status, job.last_error) == (JobStatus.FAILED, "exhausted")
    assert job.fail_delivery("again", LATER) is False
    assert job.restart_delivery(LATER) is True and job.status is JobStatus.ACCEPTED
    assert job.restart_delivery(LATER) is False


def test_the_first_voice_attempt_fixes_the_cold_start_mark() -> None:
    job = voice_job()

    assert job.begin_voice_attempt(NOW, cold_start=True) is True
    assert job.begin_voice_attempt(LATER, cold_start=False) is True

    assert job.voice is not None
    assert (job.voice.attempts, job.voice.cold_start, job.voice.started_at) == (
        2,
        True,
        NOW,
    )


def test_a_checkpoint_stores_the_object_and_moves_the_stage() -> None:
    job = voice_job()

    assert job.checkpoint_audio("voice/key", 100, "sha", LATER) is True

    voice = job.voice
    assert voice is not None
    assert (voice.stage, voice.s3_key, voice.audio_bytes, voice.stored_at) == (
        VoiceStage.STORED,
        "voice/key",
        100,
        LATER,
    )


def test_a_transcript_becomes_the_task_text_and_raises_the_create_task_event() -> None:
    job = voice_job()
    job.pull_events()

    done = job.complete_transcription("Позвонить маме", "ru", NOW, LATER, LATER)

    assert done is True
    assert (job.text, job.status) == ("Позвонить маме", JobStatus.ACCEPTED)
    assert job.voice is not None and job.voice.stage is VoiceStage.DONE
    (event,) = job.pull_events()
    assert isinstance(event, VoiceTranscribed) and event.text == "Позвонить маме"
    assert job.complete_transcription("again", "ru", NOW, LATER, LATER) is False


def test_a_final_voice_error_fails_the_job_and_tells_the_author() -> None:
    job = voice_job()
    job.pull_events()

    failed = job.fail_voice("timeout", "no task", LATER, NOW, LATER)

    assert failed is True
    assert (job.status, job.last_error) == (JobStatus.FAILED, "timeout")
    assert job.voice is not None
    assert (job.voice.stage, job.voice.error_code) == (VoiceStage.FAILED, "timeout")
    (event,) = job.pull_events()
    assert isinstance(event, VoiceJobFailed) and event.notice == "no task"
    assert job.fail_voice("timeout", "no task", LATER) is False


def test_a_failed_voice_runs_again_from_the_object_or_from_telegram() -> None:
    stored = voice_job(s3_key="voice/key")
    stored.fail_voice("unavailable", "x", LATER)
    fresh = voice_job()
    fresh.fail_voice("unavailable", "x", LATER)
    stored.pull_events()

    assert stored.restart_voice(LATER) is True
    assert fresh.restart_voice(LATER) is True

    assert stored.voice is not None and fresh.voice is not None
    assert stored.voice.stage is VoiceStage.STORED
    assert fresh.voice.stage is VoiceStage.QUEUED
    assert (stored.status, stored.voice.attempts, stored.voice.error_code) == (
        JobStatus.ACCEPTED,
        0,
        None,
    )
    (event,) = stored.pull_events()
    assert isinstance(event, JobRestarted) and event.voice is True


def test_a_voice_that_did_not_fail_is_not_restarted() -> None:
    assert voice_job().restart_voice(LATER) is False
    assert text_job().restart_voice(LATER) is False


def test_a_finished_job_does_not_take_voice_steps() -> None:
    job = voice_job()
    job.fail_delivery("exhausted", LATER)

    assert job.begin_voice_attempt(LATER, False) is False
    assert job.checkpoint_audio("k", 1, "s", LATER) is False


def test_a_job_finished_between_read_and_write_takes_no_transcript() -> None:
    job = voice_job()
    job.fail_delivery("exhausted", LATER)
    job.pull_events()

    assert job.complete_transcription("текст", "ru", NOW, LATER, LATER) is False
    assert job.text is None
    assert job.pull_events() == []


def test_a_job_finished_between_read_and_write_takes_no_second_failure() -> None:
    job = voice_job()
    job.checkpoint_audio("k", 1, "s", NOW)
    assert job.complete_transcription("текст", "ru", NOW, LATER, LATER) is True
    job.pull_events()

    assert job.fail_voice("unavailable", "notice", LATER) is False
    assert job.pull_events() == []


def test_the_limits_refuse_the_common_cap_before_the_voice_cap() -> None:
    limits = JobLimits()

    assert limits.check(JobCounts(0, 0, 0, 0), voice=True) is None
    assert limits.check(JobCounts(100, 0, 0, 0), voice=False) is not None
    assert limits.check(JobCounts(5, 10, 0, 0), voice=False) is not None
    refusal = limits.check(JobCounts(5, 2, 10, 2), voice=True)
    assert refusal is not None and refusal.code == "voice_limit"
    assert limits.check(JobCounts(5, 2, 10, 2), voice=False) is None
