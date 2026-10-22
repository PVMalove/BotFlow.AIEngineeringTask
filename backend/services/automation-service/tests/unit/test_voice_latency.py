"""Voice latency: stage timings from Automation timestamps, and the 60 s verdict."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from application.queries.voice_latency import (
    LIMIT_SECONDS,
    Verdict,
    VoiceRecord,
    analyze,
    render,
    timing,
)

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def record(**overrides: object) -> VoiceRecord:
    """A warm record: ack 0.5 s, queue 2 s, download 3 s, STT 20 s, hand-off 4 s."""
    values: dict[str, object] = dict(
        job_id=uuid4(),
        status="completed",
        duration_seconds=30,
        cold_start=False,
        received_at=at(0),
        acked_at=at(0.5),
        published_at=at(1),
        started_at=at(3),
        stored_at=at(6),
        stt_started_at=at(6.5),
        stt_finished_at=at(26.5),
        completed_at=at(30.5),
    )
    values.update(overrides)
    return VoiceRecord(**values)  # type: ignore[arg-type]


def test_every_stage_is_measured_from_the_stored_timestamps() -> None:
    t = timing(record())

    assert t.ack == pytest.approx(0.5)
    assert t.queue_wait == pytest.approx(2.0)
    assert t.download_store == pytest.approx(3.0)
    assert t.stt == pytest.approx(20.0)
    assert t.handoff == pytest.approx(4.0)


def test_the_total_runs_from_the_update_to_the_task_without_the_queue_wait() -> None:
    t = timing(record())

    assert t.total == pytest.approx(30.5 - 2.0)


def test_queue_wait_is_its_own_figure_and_does_not_stretch_the_total() -> None:
    quick = timing(record())
    queued = timing(
        record(
            started_at=at(53),
            stored_at=at(56),
            stt_started_at=at(56.5),
            stt_finished_at=at(76.5),
            completed_at=at(80.5),
        )
    )

    assert queued.queue_wait == pytest.approx(52.0)
    assert queued.total == pytest.approx(quick.total or 0)


def test_a_job_that_did_not_complete_has_no_total_and_no_hand_off() -> None:
    t = timing(record(status="accepted", completed_at=None, stt_finished_at=None))

    assert t.total is None and t.handoff is None and t.stt is None
    assert t.ack == pytest.approx(0.5) and t.queue_wait == pytest.approx(2.0)


def test_a_missing_timestamp_leaves_only_its_figures_empty() -> None:
    t = timing(record(acked_at=None, published_at=None))

    assert t.ack is None and t.queue_wait is None and t.total is None
    assert t.stt == pytest.approx(20.0)


def test_the_cold_start_record_is_reported_apart_from_the_warm_ones() -> None:
    report = analyze([record(), record(cold_start=True, completed_at=at(95))])

    assert report.warm == 1 and len(report.cold) == 1
    assert report.stats["total"].count == 1
    assert report.stats["total"].worst == pytest.approx(28.5)
    assert report.cold[0].total == pytest.approx(93.0)


def test_the_verdict_passes_when_the_worst_warm_record_is_within_a_minute() -> None:
    report = analyze([record(), record(completed_at=at(61.9))])  # 59.9 s without queue

    assert LIMIT_SECONDS == 60.0
    assert report.verdict is Verdict.PASS
    assert report.stats["total"].worst == pytest.approx(59.9)


def test_the_verdict_fails_when_one_warm_record_is_over_sixty_seconds() -> None:
    report = analyze([record(), record(completed_at=at(62.5))])  # 60.5 s

    assert report.verdict is Verdict.FAIL


def test_the_cold_start_record_never_decides_the_verdict() -> None:
    report = analyze([record(), record(cold_start=True, completed_at=at(300))])

    assert report.verdict is Verdict.PASS


def test_without_a_finished_warm_record_there_is_no_verdict() -> None:
    nothing = analyze([])
    only_cold = analyze([record(cold_start=True)])
    unfinished = analyze([record(status="accepted", completed_at=None)])

    assert nothing.verdict is Verdict.NO_DATA
    assert only_cold.verdict is Verdict.NO_DATA
    assert unfinished.verdict is Verdict.NO_DATA


def test_failed_and_unfinished_jobs_are_counted_but_not_measured() -> None:
    report = analyze(
        [
            record(),
            record(status="failed", completed_at=None),
            record(status="accepted", completed_at=None),
        ]
    )

    assert (report.warm, report.failed, report.unfinished) == (1, 1, 1)
    assert report.stats["total"].count == 1


def test_stats_give_the_median_the_95th_percentile_and_the_worst() -> None:
    records = [record(completed_at=at(2 + n)) for n in range(1, 21)]  # totals 1..20

    stat = analyze(records).stats["total"]

    assert stat.count == 20
    assert stat.median == pytest.approx(10.5)
    assert stat.p95 == pytest.approx(19.0)
    assert stat.worst == pytest.approx(20.0)


def test_the_conditions_name_the_audio_length_and_the_queue_wait() -> None:
    report = analyze(
        [record(duration_seconds=12), record(duration_seconds=58, started_at=at(5))]
    )

    assert (report.min_duration, report.max_duration) == (12, 58)
    assert report.stats["queue_wait"].worst == pytest.approx(4.0)


def test_the_report_states_the_verdict_and_its_conditions_in_words() -> None:
    text = render(analyze([record(duration_seconds=58)]))

    assert "PASS" in text and "60" in text
    assert "прогретый" in text and "очередь" in text and "58" in text
    for label in ("ACK", "Ожидание в очереди", "Скачивание", "STT", "Передача"):
        assert label in text


def test_the_report_without_data_says_so_instead_of_claiming_success() -> None:
    text = render(analyze([]))

    assert "NO_DATA" in text and "PASS" not in text


def test_the_queue_wait_line_is_marked_as_excluded_from_the_total() -> None:
    text = render(analyze([record()]))

    queue_line = next(line for line in text.splitlines() if "очереди" in line.lower())
    assert "не входит" in queue_line
