"""SqlVoiceStore decisions around its statements, against a scripted session.

The statements themselves run against PostgreSQL in the smoke tests.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from kernel_platform.outbox import OutboxRow
from sqlalchemy.dialects import postgresql

from application.ports import OutboxMessage, Transcript, VoiceStore, VoiceWork
from infrastructure.db.voice import SqlVoiceStore, fail_voice_job

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
JOB_ID = uuid4()
AUTHOR_ID = uuid4()


class Result:
    def __init__(self, row: Any = None) -> None:
        self.row = row

    def one_or_none(self) -> Any:
        return self.row

    def first(self) -> Any:
        return self.row

    def scalar_one(self) -> Any:
        return self.row


class Session:
    def __init__(self, results: list[Any]) -> None:
        self.results = results
        self.statements: list[str] = []
        self.saves = 0

    async def __aenter__(self) -> "Session":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, statement: Any) -> Result:
        dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
        self.statements.append(statement.compile(dialect=dialect).string)
        return Result(self.results.pop(0) if self.results else None)

    async def commit(self) -> None:
        self.saves += 1


def store(session: Session) -> SqlVoiceStore:
    return SqlVoiceStore(lambda: session)  # type: ignore[arg-type]


def job_row(**overrides: Any) -> Any:
    values: dict[str, Any] = dict(
        bot_id=123,
        chat_id=4_200_000_001,
        message_id=7,
        author_id=AUTHOR_ID,
        author_telegram_id=4_200_000_001,
        author_name="Ann (@ann)",
        received_at=NOW,
        status="accepted",
        file_id="AwACfile",
        mime_type="audio/ogg",
        stage="queued",
        s3_key=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def work() -> VoiceWork:
    return VoiceWork(
        JOB_ID, 123, 1, 7, AUTHOR_ID, 1, "Ann", NOW, "f", None, "queued", None, 1
    )


def message() -> OutboxMessage:
    return OutboxMessage(uuid4(), JOB_ID, "CreateTask", "task.create", {"x": 1})


def exhausted() -> OutboxRow:
    return OutboxRow(
        uuid4(), "TranscribeVoice", "voice.transcribe", {}, NOW, 3, None, JOB_ID
    )


def test_the_store_satisfies_the_use_case_port() -> None:
    sessions: Any = object()
    port: VoiceStore = SqlVoiceStore(sessions)

    assert port is not None


async def test_an_unknown_job_is_not_started() -> None:
    session = Session([None])

    assert await store(session).begin(JOB_ID, NOW, True) is None
    assert session.saves == 0


async def test_a_finished_job_is_not_started_again() -> None:
    for row in (job_row(status="failed"), job_row(stage="done")):
        session = Session([row])

        assert await store(session).begin(JOB_ID, NOW, False) is None
        assert len(session.statements) == 1  # nothing was updated


async def test_an_active_job_counts_the_attempt_and_returns_its_work() -> None:
    session = Session([job_row(stage="stored", s3_key="voice/x"), 2])

    result = await store(session).begin(JOB_ID, NOW, False)

    assert result is not None
    assert (result.job_id, result.attempts, result.stage) == (JOB_ID, 2, "stored")
    assert (result.file_id, result.s3_key) == ("AwACfile", "voice/x")
    assert session.saves == 1


async def test_a_job_finished_in_the_meantime_is_not_completed_twice() -> None:
    session = Session([None])

    done = await store(session).complete(
        work(), Transcript("t", "ru"), message(), NOW, NOW, NOW
    )

    assert done is False and session.saves == 0


async def test_a_job_finished_in_the_meantime_is_not_failed_twice() -> None:
    session = Session([None])

    failed = await store(session).fail(work(), "empty_audio", "b", NOW)

    assert failed is False


async def test_a_final_error_changes_the_voice_record_the_job_and_the_notice() -> None:
    session = Session([(JOB_ID,)])

    failed = await store(session).fail(work(), "empty_audio", "b", NOW, NOW, NOW)

    assert failed is True and session.saves == 1
    tables = [statement.split()[1] for statement in session.statements]
    assert tables[:2] == ["voice_jobs", "jobs"]
    assert session.statements[2].startswith("INSERT INTO notification_intents")


async def test_an_exhausted_hand_off_fails_a_running_job_with_a_notice() -> None:
    session = Session([SimpleNamespace(chat_id=1, author_id=AUTHOR_ID), (JOB_ID,)])
    tx: Any = SimpleNamespace(session=session)

    await fail_voice_job(tx, exhausted(), "x")

    assert session.statements[-1].startswith("INSERT INTO notification_intents")


async def test_an_exhausted_hand_off_of_a_finished_job_changes_nothing() -> None:
    session = Session([None])
    tx: Any = SimpleNamespace(session=session)

    await fail_voice_job(tx, exhausted(), "x")

    assert len(session.statements) == 1
