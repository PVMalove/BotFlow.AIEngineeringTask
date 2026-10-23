"""Retry delays: up to three attempts, 5 s then 30 s apart, with bounded jitter."""

import random
from collections.abc import Callable
from datetime import timedelta

Rng = Callable[[], float]  # a number in [0, 1]


def jittered(seconds: float, *, rng: Rng = random.random, ratio: float = 0.2) -> float:
    """Spread `seconds` over +-`ratio` so that parallel workers do not synchronize."""
    return seconds * (1 + ratio * (2 * rng() - 1))


class RetryPolicy:
    def __init__(
        self,
        delays: tuple[timedelta, ...] = (timedelta(seconds=5), timedelta(seconds=30)),
        *,
        jitter: float = 0.2,
        rng: Rng = random.random,
    ) -> None:
        self._delays = delays
        self._jitter = jitter
        self._rng = rng

    @property
    def max_attempts(self) -> int:
        return len(self._delays) + 1

    def delay_after(self, failed_attempts: int) -> timedelta | None:
        """Pause before the next try; None once every attempt has failed."""
        if failed_attempts > len(self._delays):
            return None
        base = self._delays[failed_attempts - 1]
        return timedelta(
            seconds=jittered(base.total_seconds(), rng=self._rng, ratio=self._jitter)
        )
