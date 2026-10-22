"""WhisperTranscriber: probe limits, decode parameters, timeout and error mapping."""

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from application.ports import (
    AudioTooLarge,
    AudioTooLong,
    CorruptedAudio,
    EmptyAudio,
    Transcript,
    TranscriptionTimeout,
)
from infrastructure.stt.whisper_transcriber import WhisperTranscriber
from infrastructure.stt.whisper_validation import DECODE_PARAMETERS, CorpusError

AUDIO = b"OggS-voice"


class Model:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.heard: list[bytes] = []
        self.paths: list[Path] = []
        self.result: dict[str, Any] = {"text": " Купить молоко ", "language": "ru"}
        self.error: Exception | None = None
        self.release = threading.Event()
        self.block = False
        self.thread_names: list[str] = []

    def transcribe(self, path: str, **parameters: Any) -> dict[str, Any]:
        self.calls.append((path, parameters))
        self.paths.append(Path(path))
        self.heard.append(Path(path).read_bytes())
        self.thread_names.append(threading.current_thread().name)
        if self.block:
            self.release.wait(timeout=5)
        if self.error:
            raise self.error
        return self.result


def transcriber(
    model: Model, duration: float = 12.0, timeout: float = 5.0
) -> WhisperTranscriber:
    return WhisperTranscriber(
        model, timeout_seconds=timeout, probe=lambda path: duration
    )


async def test_the_audio_reaches_the_model_as_a_file_with_validated_settings() -> None:
    model = Model()

    transcript = await transcriber(model).transcribe(AUDIO)

    assert transcript == Transcript("Купить молоко", "ru")
    ((_, parameters),) = model.calls
    assert parameters == DECODE_PARAMETERS
    assert parameters["language"] is None and parameters["fp16"] is False
    assert model.heard == [AUDIO]


async def test_the_temporary_file_is_removed_afterwards() -> None:
    model = Model()

    await transcriber(model).transcribe(AUDIO)

    assert not model.paths[0].exists()


async def test_the_file_is_removed_after_a_failure_too() -> None:
    model = Model()
    model.error = RuntimeError("Failed to load audio: bad")

    with pytest.raises(CorruptedAudio):
        await transcriber(model).transcribe(AUDIO)

    assert not model.paths[0].exists()


async def test_recognition_runs_off_the_event_loop() -> None:
    model = Model()
    model.block = True
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.005)

    task = asyncio.create_task(ticker())
    decode = asyncio.create_task(transcriber(model).transcribe(AUDIO))
    await asyncio.sleep(0.1)
    ticked_while_blocked = ticks
    model.release.set()
    await decode
    task.cancel()

    assert ticked_while_blocked > 5
    assert model.thread_names[0] != threading.main_thread().name


@pytest.mark.parametrize("text", ["", "   \n"])
async def test_a_recording_without_words_is_an_empty_audio_error(text: str) -> None:
    model = Model()
    model.result = {"text": text, "language": "ru"}

    with pytest.raises(EmptyAudio):
        await transcriber(model).transcribe(AUDIO)


async def test_a_decoding_failure_is_a_corrupted_audio_error() -> None:
    model = Model()
    model.error = RuntimeError("Failed to load audio: Invalid data found")

    with pytest.raises(CorruptedAudio):
        await transcriber(model).transcribe(AUDIO)


async def test_any_other_model_failure_is_not_blamed_on_the_audio() -> None:
    model = Model()
    model.error = RuntimeError("CUDA out of memory")

    with pytest.raises(RuntimeError, match="out of memory"):
        await transcriber(model).transcribe(AUDIO)


async def test_an_unreadable_file_is_corrupted_and_never_reaches_the_model() -> None:
    model = Model()

    def probe(path: Path) -> float:
        raise CorpusError("voice.oga is not readable audio")

    sut = WhisperTranscriber(model, timeout_seconds=5, probe=probe)

    with pytest.raises(CorruptedAudio):
        await sut.transcribe(AUDIO)

    assert model.calls == []


@pytest.mark.parametrize("duration", [61.0, 12.0, 0.4])
async def test_a_recording_up_to_sixty_one_seconds_is_recognised(
    duration: float,
) -> None:
    model = Model()

    await transcriber(model, duration=duration).transcribe(AUDIO)

    assert len(model.calls) == 1


async def test_a_recording_over_sixty_one_seconds_is_too_long() -> None:
    model = Model()

    with pytest.raises(AudioTooLong):
        await transcriber(model, duration=61.5).transcribe(AUDIO)

    assert model.calls == []


async def test_a_file_over_five_mebibytes_is_too_large_before_any_probe() -> None:
    model = Model()
    probed: list[Path] = []

    def probe(path: Path) -> float:
        probed.append(path)
        return 1.0

    sut = WhisperTranscriber(model, timeout_seconds=5, probe=probe)

    with pytest.raises(AudioTooLarge):
        await sut.transcribe(b"x" * (5 * 1024 * 1024 + 1))

    assert probed == [] and model.calls == []


async def test_a_decode_that_outlives_the_timeout_is_a_timeout_error() -> None:
    model = Model()
    model.block = True

    try:
        with pytest.raises(TranscriptionTimeout):
            await transcriber(model, timeout=0.05).transcribe(AUDIO)
    finally:
        model.release.set()  # let the daemon thread end


async def test_a_late_result_of_a_timed_out_decode_is_ignored() -> None:
    model = Model()
    model.block = True
    with pytest.raises(TranscriptionTimeout):
        await transcriber(model, timeout=0.05).transcribe(AUDIO)

    model.release.set()
    await asyncio.sleep(0.05)  # the thread finishes into a cancelled future
    # no "Future exception was never retrieved" and no crash: the next call works
    model.block = False
    assert (await transcriber(model).transcribe(AUDIO)).text == "Купить молоко"
