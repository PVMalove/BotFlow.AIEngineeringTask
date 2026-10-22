"""Speech to text with the local official Whisper model (ADR-0006).

The decode runs in a daemon thread: a Python thread cannot be stopped, so after
the timeout the worker answers with a final error and replaces its own process
(see `runner.py`) before the stuck decode can overlap the next record.
"""

import asyncio
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from application.ports import (
    AudioTooLarge,
    AudioTooLong,
    CorruptedAudio,
    EmptyAudio,
    Transcript,
    TranscriptionTimeout,
)
from infrastructure.stt.whisper_validation import (
    DECODE_PARAMETERS,
    MAX_DURATION_SECONDS,
    MAX_SIZE_BYTES,
    CorpusError,
    ffprobe_duration,
)

DEFAULT_TIMEOUT_SECONDS = 120.0  # twice the slowest validated decode (#4)
_DECODE_FAILURE = "Failed to load audio"  # what whisper raises when ffmpeg fails


class WhisperTranscriber:
    def __init__(
        self,
        model: Any,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        probe: Callable[[Path], float] = ffprobe_duration,
    ) -> None:
        self._model = model
        self._timeout = timeout_seconds
        self._probe = probe

    async def transcribe(self, audio: bytes) -> Transcript:
        if len(audio) > MAX_SIZE_BYTES:
            raise AudioTooLarge()
        with tempfile.TemporaryDirectory(prefix="botflow-stt-") as directory:
            path = Path(directory) / "voice.oga"
            path.write_bytes(audio)
            try:
                duration = await asyncio.to_thread(self._probe, path)
            except CorpusError:
                raise CorruptedAudio() from None
            if duration > MAX_DURATION_SECONDS:
                raise AudioTooLong()
            result = await self._decode(path)
        text = str(result.get("text", "")).strip()
        if not text:
            raise EmptyAudio()
        return Transcript(text, str(result.get("language") or "") or None)

    async def _decode(self, path: Path) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()

        def deliver(result: dict[str, Any] | None, error: BaseException | None) -> None:
            if future.done():  # the caller timed out: nobody waits any more
                return
            if error is not None:
                future.set_exception(error)
            else:
                assert result is not None
                future.set_result(result)

        def decode() -> None:
            try:
                result = self._model.transcribe(str(path), **DECODE_PARAMETERS)
            except BaseException as error:
                loop.call_soon_threadsafe(deliver, None, error)
            else:
                loop.call_soon_threadsafe(deliver, result, None)

        threading.Thread(target=decode, name="whisper-decode", daemon=True).start()
        try:
            return await asyncio.wait_for(future, self._timeout)
        except TimeoutError:
            raise TranscriptionTimeout() from None
        except RuntimeError as error:
            if str(error).startswith(_DECODE_FAILURE):
                raise CorruptedAudio() from None
            raise
