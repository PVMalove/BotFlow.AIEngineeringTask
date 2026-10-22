"""The poller owns the offset: it advances only after the handler returned."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from infrastructure.polling import TelegramPoller


def update(update_id: int) -> Any:
    return SimpleNamespace(update_id=update_id)


class FakeBot:
    def __init__(self, *batches: list[Any]) -> None:
        self.batches = list(batches)
        self.offsets: list[int | None] = []
        self.options: list[dict[str, Any]] = []
        self.failure: Exception | None = None

    async def get_updates(self, **kwargs: Any) -> list[Any]:
        self.offsets.append(kwargs.get("offset"))
        self.options.append(kwargs)
        if self.failure:
            raise self.failure
        return self.batches.pop(0) if self.batches else []


class FakeDispatcher:
    def __init__(self) -> None:
        self.fed: list[tuple[int, dict[str, Any]]] = []
        self.failures: dict[int, list[Exception]] = {}

    async def feed_update(self, bot: Any, update: Any, **kwargs: Any) -> None:
        queue = self.failures.get(update.update_id)
        if queue:
            raise queue.pop(0)
        self.fed.append((update.update_id, kwargs))


class Harness:
    def __init__(self, *batches: list[Any]) -> None:
        self.bot = FakeBot(*batches)
        self.dispatcher = FakeDispatcher()
        self.sleeps: list[float] = []
        self.logs: list[str] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    @property
    def poller(self) -> TelegramPoller:
        return TelegramPoller(
            self.bot,  # type: ignore[arg-type]
            self.dispatcher,  # type: ignore[arg-type]
            pause_seconds=5.0,
            rng=lambda: 0.5,
            sleep=self.sleep,
            log=self.logs.append,
        )


async def test_the_offset_moves_past_the_updates_whose_handler_returned() -> None:
    h = Harness([update(10), update(11)])
    poller = h.poller

    await poller.poll_once()
    await poller.poll_once()

    assert h.bot.offsets == [None, 12]
    assert [u for u, _ in h.dispatcher.fed] == [10, 11]


async def test_the_handler_receives_the_time_the_update_arrived() -> None:
    h = Harness([update(10)])

    await h.poller.poll_once()

    received_at = h.dispatcher.fed[0][1]["received_at"]
    assert isinstance(received_at, datetime) and received_at.tzinfo is UTC


async def test_an_infrastructure_failure_holds_the_offset_and_pauses() -> None:
    h = Harness([update(10), update(11)], [update(11)])
    h.dispatcher.failures[11] = [OSError("pg down")]
    poller = h.poller

    await poller.poll_once()
    await poller.poll_once()

    assert h.bot.offsets == [None, 11]  # 10 is confirmed, 11 is asked for again
    assert h.sleeps == [5.0]
    assert [u for u, _ in h.dispatcher.fed] == [10, 11]


async def test_a_database_error_is_infrastructure_too() -> None:
    h = Harness([update(10)])
    h.dispatcher.failures[10] = [OperationalError("SELECT 1", {}, Exception("x"))]
    poller = h.poller

    await poller.poll_once()
    await poller.poll_once()

    assert h.bot.offsets == [None, None]  # nothing confirmed: still the first update


async def test_any_other_error_is_logged_with_a_fixed_text_and_skipped() -> None:
    h = Harness([update(10), update(11)])
    h.dispatcher.failures[10] = [RuntimeError("https://api.telegram.org/bot123:SECRET")]
    poller = h.poller

    await poller.poll_once()
    await poller.poll_once()

    assert h.bot.offsets == [None, 12]  # no poison pill: 10 was skipped
    assert [u for u, _ in h.dispatcher.fed] == [11]
    assert h.logs and all("SECRET" not in line for line in h.logs)
    assert "Update handler failed (RuntimeError); the update is skipped." in h.logs


async def test_a_failed_fetch_pauses_without_moving_the_offset() -> None:
    h = Harness([update(10)])
    poller = h.poller
    await poller.poll_once()
    h.bot.failure = ConnectionError("https://api.telegram.org/bot123:SECRET")

    await poller.poll_once()
    h.bot.failure = None
    await poller.poll_once()

    assert h.bot.offsets == [None, 11, 11]
    assert h.sleeps == [5.0]
    assert all("SECRET" not in line for line in h.logs)
    assert "Telegram fetch failed (ConnectionError); retrying." in h.logs


async def test_fetch_conflict_is_logged_with_exception_class_without_token() -> None:
    from aiogram.exceptions import TelegramConflictError
    from aiogram.methods import GetUpdates

    h = Harness([update(10)])
    poller = h.poller
    h.bot.failure = TelegramConflictError(
        GetUpdates(), "Conflict: terminated by other getUpdates request"
    )

    await poller.poll_once()

    assert "Telegram fetch failed (TelegramConflictError); retrying." in h.logs


async def test_only_messages_and_edits_are_requested_with_a_long_poll() -> None:
    h = Harness()

    await h.poller.poll_once()

    options = h.bot.options[0]
    assert options["allowed_updates"] == ["message", "edited_message"]
    assert options["timeout"] > 0


@pytest.mark.parametrize("rng, expected", [(lambda: 0.0, 4.0), (lambda: 1.0, 6.0)])
async def test_the_pause_is_jittered(rng: Any, expected: float) -> None:
    h = Harness([update(10)])
    h.dispatcher.failures[10] = [OSError("pg down")]
    poller = TelegramPoller(
        h.bot,  # type: ignore[arg-type]
        h.dispatcher,  # type: ignore[arg-type]
        pause_seconds=5.0,
        rng=rng,
        sleep=h.sleep,
        log=h.logs.append,
    )

    await poller.poll_once()

    assert h.sleeps == [expected]
