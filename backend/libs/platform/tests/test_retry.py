"""Retry delays: 5 s then 30 s, with bounded jitter, three attempts in total."""

from datetime import timedelta

from botflow_platform.retry import RetryPolicy, jittered


def test_delays_are_five_then_thirty_seconds_without_jitter() -> None:
    sut = RetryPolicy(jitter=0.0)

    assert sut.delay_after(1) == timedelta(seconds=5)
    assert sut.delay_after(2) == timedelta(seconds=30)


def test_the_third_failure_exhausts_the_retries() -> None:
    sut = RetryPolicy()

    assert sut.delay_after(3) is None
    assert sut.max_attempts == 3


def test_jitter_stays_within_twenty_percent_of_the_base_delay() -> None:
    low = RetryPolicy(rng=lambda: 0.0)
    high = RetryPolicy(rng=lambda: 1.0)

    assert low.delay_after(1) == timedelta(seconds=4)
    assert high.delay_after(1) == timedelta(seconds=6)
    assert low.delay_after(2) == timedelta(seconds=24)
    assert high.delay_after(2) == timedelta(seconds=36)


def test_jittered_scales_a_plain_number_of_seconds() -> None:
    assert jittered(10.0, rng=lambda: 0.5, ratio=0.2) == 10.0
    assert jittered(10.0, rng=lambda: 0.0, ratio=0.2) == 8.0
