"""Voice latency from Automation's own timestamps (no cross-database SQL).

Update -> task on the board is measured from `jobs.received_at` to the moment the
TaskCreated result was recorded (`jobs.updated_at` of the completed job): by then
Tasks has committed the task, so it is readable through REST. The wait in the STT
queue (`outbox.published_at` -> `voice_jobs.started_at`) is reported separately and
left out of the total; the first record of a worker process (cold start) is
reported apart and never decides the verdict.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from statistics import median
from uuid import UUID

LIMIT_SECONDS = 60.0


class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NO_DATA = "NO_DATA"


@dataclass(frozen=True, slots=True)
class VoiceRecord:
    job_id: UUID
    status: str  # jobs.status: accepted, completed or failed
    duration_seconds: int
    cold_start: bool
    received_at: datetime
    acked_at: datetime | None
    published_at: datetime | None  # the TranscribeVoice row reached the stream
    started_at: datetime | None  # the STT worker took the record
    stored_at: datetime | None  # the audio is in MinIO
    stt_started_at: datetime | None
    stt_finished_at: datetime | None
    completed_at: datetime | None  # TaskCreated recorded (completed jobs only)


@dataclass(frozen=True, slots=True)
class Timing:
    job_id: UUID
    duration_seconds: int
    cold_start: bool
    ack: float | None  # update -> reply
    queue_wait: float | None  # published -> taken by the worker
    download_store: float | None  # download from Telegram, MinIO, checkpoint
    stt: float | None
    handoff: float | None  # recognition done -> TaskCreated recorded
    total: float | None  # update -> task, queue wait excluded


def timing(record: VoiceRecord) -> Timing:
    completed = record.status == "completed"
    end = record.completed_at if completed else None
    queue_wait = _seconds(record.published_at, record.started_at)
    elapsed = _seconds(record.received_at, end)
    return Timing(
        job_id=record.job_id,
        duration_seconds=record.duration_seconds,
        cold_start=record.cold_start,
        ack=_seconds(record.received_at, record.acked_at),
        queue_wait=queue_wait,
        download_store=_seconds(record.started_at, record.stored_at),
        stt=_seconds(record.stt_started_at, record.stt_finished_at),
        handoff=_seconds(record.stt_finished_at, end),
        total=None if elapsed is None or queue_wait is None else elapsed - queue_wait,
    )


@dataclass(frozen=True, slots=True)
class Stat:
    count: int
    median: float
    p95: float
    worst: float


@dataclass(frozen=True, slots=True)
class LatencyReport:
    limit_seconds: float
    warm: int  # completed records of a warm worker: the measured set
    cold: tuple[Timing, ...]  # completed first records of a worker process
    failed: int
    unfinished: int
    stats: dict[str, Stat]
    min_duration: int | None
    max_duration: int | None
    verdict: Verdict


_STAGES: tuple[tuple[str, str, Callable[[Timing], float | None]], ...] = (
    ("ack", "ACK (update -> ответ автору)", lambda t: t.ack),
    ("queue_wait", "Ожидание в очереди (не входит в итог)", lambda t: t.queue_wait),
    ("download_store", "Скачивание и MinIO", lambda t: t.download_store),
    ("stt", "STT (Whisper)", lambda t: t.stt),
    ("handoff", "Передача до TaskCreated", lambda t: t.handoff),
    ("total", "Итого update -> задача", lambda t: t.total),
)


def analyze(
    records: Sequence[VoiceRecord], limit_seconds: float = LIMIT_SECONDS
) -> LatencyReport:
    timings = [(r, timing(r)) for r in records]
    warm = [t for r, t in timings if r.status == "completed" and not r.cold_start]
    cold = tuple(t for r, t in timings if r.status == "completed" and r.cold_start)
    stats: dict[str, Stat] = {}
    for key, _, pick in _STAGES:
        stat = _stat([v for t in warm if (v := pick(t)) is not None])
        if stat is not None:
            stats[key] = stat
    total = stats.get("total")
    if total is None:
        verdict = Verdict.NO_DATA
    else:
        verdict = Verdict.PASS if total.worst <= limit_seconds else Verdict.FAIL
    durations = [t.duration_seconds for t in warm]
    return LatencyReport(
        limit_seconds=limit_seconds,
        warm=len(warm),
        cold=cold,
        failed=sum(1 for r in records if r.status == "failed"),
        unfinished=sum(1 for r in records if r.status == "accepted"),
        stats=stats,
        min_duration=min(durations) if durations else None,
        max_duration=max(durations) if durations else None,
        verdict=verdict,
    )


def render(report: LatencyReport) -> str:
    limit = f"{report.limit_seconds:g}"
    lines = [
        f"Голосовой путь: от сообщения до задачи на панели (цель не более {limit} с)",
        f"Записи: прогретых завершённых {report.warm}, холодный старт "
        f"{len(report.cold)} (отдельно), с ошибкой {report.failed}, "
        f"не завершено {report.unfinished}.",
        f"{'Этап, секунды':<42}{'медиана':>9}{'p95':>9}{'худшее':>9}",
    ]
    for key, label, _ in _STAGES:
        stat = report.stats.get(key)
        if stat is None:
            lines.append(f"{label:<42}{'-':>9}{'-':>9}{'-':>9}")
        else:
            lines.append(
                f"{label:<42}{stat.median:>9.1f}{stat.p95:>9.1f}{stat.worst:>9.1f}"
            )
    for cold in report.cold:
        shown = "-" if cold.total is None else f"{cold.total:.1f}"
        lines.append(
            f"Холодный старт {cold.job_id}: итого {shown} с (в вердикт не входит)"
        )
    queue = report.stats.get("queue_wait")
    total = report.stats.get("total")
    if total is None:
        lines.append(f"Вердикт: {report.verdict.value} - нет завершённых записей.")
    else:
        relation = "не более" if report.verdict is Verdict.PASS else "больше"
        lines.append(
            f"Вердикт: {report.verdict.value} - худший итог {total.worst:.1f} с, "
            f"{relation} {limit} с."
        )
    queue_text = (
        f"худшее ожидание {queue.worst:.1f} с, учтено отдельно"
        if queue
        else "ожидание не измерено"
    )
    audio = (
        f"{report.min_duration}-{report.max_duration} с"
        if report.max_duration is not None
        else "нет данных"
    )
    lines.append(
        "Условия: прогретый worker (запись холодного старта исключена), "
        f"пустая очередь ({queue_text}), длина аудио {audio}."
    )
    return "\n".join(lines)


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


def _stat(values: list[float]) -> Stat | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(0.95 * len(ordered)) - 1  # nearest rank
    return Stat(len(ordered), median(ordered), ordered[rank], ordered[-1])
