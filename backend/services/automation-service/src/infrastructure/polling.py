"""Long polling that owns the Telegram offset.

aiogram's `start_polling` moves the offset after a handler failed, so an update
the database could not store would be lost. Here the offset moves only after the
handler returned: infrastructure errors hold it and the same update comes back,
any other error is logged with a fixed text and skipped, so a poison update
cannot block the queue. Exception text is never logged: it can carry the token.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher
from botflow_platform.retry import Rng, jittered
from sqlalchemy.exc import DBAPIError, TimeoutError

ALLOWED_UPDATES = ["message", "edited_message"]
POLL_TIMEOUT_SECONDS = 30

# Storage and OS failures: the update is fine, the dependency is not.
_INFRASTRUCTURE_ERRORS = (DBAPIError, TimeoutError, OSError)


class TelegramPoller:
    def __init__(
        self,
        bot: Bot,
        dispatcher: Dispatcher,
        *,
        pause_seconds: float = 5.0,
        rng: Rng = random.random,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        log: Callable[[str], None] = lambda line: print(line, flush=True),
    ) -> None:
        self._bot = bot
        self._dispatcher = dispatcher
        self._pause_seconds = pause_seconds
        self._rng = rng
        self._sleep = sleep
        self._log = log
        self._offset: int | None = None

    async def run(self) -> None:
        """Poll until cancelled."""
        while True:
            await self.poll_once()

    async def poll_once(self) -> int:
        """Fetch one batch and hand it over; return how many updates were confirmed."""
        try:
            updates = await self._bot.get_updates(
                offset=self._offset,
                timeout=POLL_TIMEOUT_SECONDS,
                allowed_updates=ALLOWED_UPDATES,
            )
        except Exception as error:
            self._log(f"Telegram fetch failed ({type(error).__name__}); retrying.")
            await self._pause()
            return 0
        confirmed = 0
        for update in updates:
            received_at = datetime.now(UTC)
            try:
                await self._dispatcher.feed_update(
                    self._bot, update, received_at=received_at
                )
            except _INFRASTRUCTURE_ERRORS:
                self._log("Storage unavailable; the update will be retried.")
                await self._pause()
                return confirmed  # the offset stays on this update
            except Exception as error:
                self._log(
                    f"Update handler failed ({type(error).__name__}); "
                    "the update is skipped."
                )
            self._offset = update.update_id + 1
            confirmed += 1
        return confirmed

    async def _pause(self) -> None:
        await self._sleep(jittered(self._pause_seconds, rng=self._rng))
