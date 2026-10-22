"""The STT worker loop: load the model, become ready, then one record at a time.

Order at start: delete the stale readiness marker, verify and load the model,
create the stream group, write the marker. The healthcheck reads the marker, so
the container is never ready before the model is in memory. A record whose decode
timed out is acknowledged (its final error is already stored) and the process is
replaced: a stuck decode thread cannot be stopped and must not overlap the next
record.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from application.commands.transcribe_voice import TranscribeResult
from application.ports import VoiceQueue
from infrastructure.stt.whisper_transcriber import DEFAULT_TIMEOUT_SECONDS

BLOCK_MS = 5_000
FAILURE_SECONDS = 5.0


class Handler(Protocol):
    async def execute(self, job_id: UUID) -> TranscribeResult: ...


class ReadinessMarker:
    """A file that exists exactly while the worker is ready to serve."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)

    def set(self) -> None:
        self._path.write_text("ready\n")

    def is_set(self) -> bool:
        return self._path.is_file()


@dataclass(frozen=True, slots=True)
class SttConfig:
    model: str
    models_dir: Path
    timeout_seconds: float
    ready_file: Path

    @classmethod
    def from_environ(cls, env: Mapping[str, str]) -> "SttConfig":
        timeout = float(env.get("BOTFLOW_STT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
        if not timeout > 0:
            raise ValueError("BOTFLOW_STT_TIMEOUT_SECONDS must be positive.")
        return cls(
            model=env.get("BOTFLOW_WHISPER_MODEL", "turbo"),
            models_dir=Path(env.get("BOTFLOW_WHISPER_MODELS_DIR", "/models")),
            timeout_seconds=timeout,
            ready_file=Path(
                env.get("BOTFLOW_WHISPER_READY_FILE", "/tmp/whisper-ready")
            ),
        )


async def run_worker(
    *,
    marker: ReadinessMarker,
    load: Callable[[], Awaitable[Handler]],
    queue: VoiceQueue,
    stop: asyncio.Event,
    exit_process: Callable[[], None],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    log: Callable[[str], None] = print,
) -> None:
    marker.clear()
    handler = await load()  # weights verified and the model in memory
    await queue.ensure_group()
    marker.set()
    log("STT worker ready.")
    try:
        while not stop.is_set():
            try:
                entries = await queue.claim_idle(1) or await queue.read(1, BLOCK_MS)
            except Exception:
                # Driver errors can carry credentials: report nothing but a fixed text.
                log("Voice queue unavailable; retrying.")
                await sleep(FAILURE_SECONDS)
                continue
            for entry in entries[:1]:  # strictly one record at a time
                try:
                    if entry.job_id is None:
                        await queue.ack(entry.entry_id)
                        continue
                    result = await handler.execute(entry.job_id)
                    await queue.ack(entry.entry_id)
                except Exception as error:
                    if getattr(error, "restart_required", False):
                        log("STT thread timed out; replacing process.")
                        marker.clear()
                        exit_process()
                        return
                    log("Voice record failed; it stays pending.")
                    await sleep(FAILURE_SECONDS)
                    continue
                if result.restart_required:
                    marker.clear()
                    exit_process()
                    return
    finally:
        marker.clear()
