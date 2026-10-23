"""Local Whisper validation: corpus contract, pinned weights and a sequential bench.

The corpus is real speech supplied by the owner. Duration and size are measured
from the files; values declared in the manifest are ignored. Weights are
downloaded only by `prepare_weights`; every other path reads a verified local file.
"""

import hashlib
import importlib
import json
import math
import os
import platform
import resource
import subprocess
import threading
import time
import urllib.request
from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Self

MIB = 1024 * 1024
# Product limit is 60 s; one extra second absorbs Opus framing (60 s probes as 60.006).
MAX_DURATION_SECONDS = 61.0
MAX_SIZE_BYTES = 5 * MIB
LANGUAGES = ("ru", "hy")
SAMPLES_PER_LANGUAGE = 3
# (target, lowest, highest) in seconds; the last bucket ends at the hard limit.
DURATION_BUCKETS = ((10.0, 7.0, 13.0), (30.0, 25.0, 35.0), (60.0, 50.0, 61.0))
STRING_FIELDS = ("reference_text", "expected_action", "expected_object")
LIST_FIELDS = ("expected_names", "expected_numbers")

DurationProbe = Callable[[Path], float]
Downloader = Callable[[str, Path], None]

# Recorded verbatim in the results. No language, prompt or translation: the
# language is auto-detected on the whole recording (all 30-second windows).
DECODE_PARAMETERS: dict[str, Any] = {
    "task": "transcribe",
    "language": None,
    "initial_prompt": None,
    "fp16": False,
    "verbose": None,
    "temperature": 0.0,
    "compression_ratio_threshold": 2.4,
    "logprob_threshold": -1.0,
    "no_speech_threshold": 0.6,
    "condition_on_previous_text": False,
    "word_timestamps": False,
}


@dataclass(frozen=True)
class ModelWeights:
    url: str
    sha256: str


# Multilingual checkpoints only (no `.en`). URL and SHA-256 are copied from
# `whisper._MODELS` of openai-whisper 20250625 (the pinned version); upstream
# embeds the SHA-256 of the checkpoint as the penultimate URL path segment.
_BASE = "https://openaipublic.azureedge.net/main/whisper/models"
TRUSTED_WEIGHTS: dict[str, ModelWeights] = {
    name: ModelWeights(f"{_BASE}/{digest}/{filename}", digest)
    for name, digest, filename in (
        (
            "tiny",
            "65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9",
            "tiny.pt",
        ),
        (
            "base",
            "ed3a0b6b1c0edf879ad9b11b1af5a0e6ab5db9205f891f668f8b0e6c6326e34e",
            "base.pt",
        ),
        (
            "small",
            "9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794",
            "small.pt",
        ),
        (
            "medium",
            "345ae4da62f9b3d59415adc60127b97c714f32e89e936602e85993674d08dcb1",
            "medium.pt",
        ),
        (
            "large-v3",
            "e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb",
            "large-v3.pt",
        ),
        (
            "turbo",
            "aff26ae408abcba5fbf8813c21e62b0941638c5f6eebfb145be0c9839262a19a",
            "large-v3-turbo.pt",
        ),
    )
}


class CorpusError(ValueError):
    """The corpus or its audio violates the validation contract."""


class WeightsError(RuntimeError):
    """Model weights are unknown, missing, corrupt or could not be prepared."""


@dataclass(frozen=True)
class CorpusSample:
    id: str
    language: str
    audio: Path
    reference_text: str
    expected_action: str
    expected_object: str
    expected_names: tuple[str, ...]
    expected_numbers: tuple[str, ...]
    duration_seconds: float
    size_bytes: int


def ffprobe_duration(audio: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as error:
        raise CorpusError("ffprobe is not installed") from error
    try:
        duration = float(result.stdout.strip())
    except ValueError:
        duration = math.nan
    if result.returncode != 0 or not math.isfinite(duration) or duration <= 0:
        raise CorpusError(f"{audio.name} is not readable audio")
    return duration


def load_corpus(
    manifest: Path, probe_duration: DurationProbe | None = None
) -> list[CorpusSample]:
    probe = probe_duration or ffprobe_duration
    entries = _read_entries(manifest)
    samples = [_load_sample(entry, manifest.parent, probe) for entry in entries]
    _check_unique_ids(samples)
    _check_composition(samples)
    return samples


def weights_path(models_dir: Path, model: str) -> Path:
    return models_dir / f"{model}.pt"


def verify_weights(
    models_dir: Path, model: str, trusted: Mapping[str, ModelWeights] | None = None
) -> Path:
    """Check a local file against the trusted digest; never touches the network."""
    weights = _trusted(model, trusted)
    path = weights_path(models_dir, model)
    if not path.is_file():
        raise WeightsError(f"weights for {model!r} missing at {path}: run prepare")
    _check_digest(path, weights.sha256)
    return path


def download_file(url: str, destination: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as response:
        with destination.open("wb") as stream:
            while chunk := response.read(1024 * 1024):
                stream.write(chunk)


def prepare_weights(
    models_dir: Path,
    model: str,
    download: Downloader,
    trusted: Mapping[str, ModelWeights] | None = None,
) -> Path:
    """The only network step: publish a file only after it matches the digest."""
    weights = _trusted(model, trusted)
    path = weights_path(models_dir, model)
    try:
        verify_weights(models_dir, model, trusted)
        return path
    except WeightsError:
        pass
    models_dir.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    try:
        try:
            download(weights.url, partial)
        except Exception as error:
            raise WeightsError(f"download failed for {model!r}: {error}") from error
        _check_digest(partial, weights.sha256)
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)
    return path


def load_whisper_model(path: Path) -> object:
    """Load a verified local checkpoint on CPU; a path never triggers a download."""
    whisper = importlib.import_module("whisper")  # heavy: only in the STT runtime
    return whisper.load_model(str(path), device="cpu")


@dataclass(frozen=True)
class Run:
    sample_id: str
    phase: str  # cold: very first call; first_pass: first call per other file; warm
    pass_index: int
    seconds: float
    rtf: float
    peak_rss_mib: float
    ok: bool
    error: str | None
    text: str
    language: str


class RssSampler:
    """Peak RSS of this process plus its children (ffmpeg) while active.

    Sampled, so a child that lives only milliseconds can be missed; the exact
    child maximum is taken separately from `getrusage(RUSAGE_CHILDREN)`.
    """

    def __init__(self, interval: float = 0.02) -> None:
        self._psutil = importlib.import_module("psutil")
        self._process = self._psutil.Process()
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._peak = 0

    def __enter__(self) -> Self:
        self._sample()
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()
        self._sample()

    @property
    def peak_mib(self) -> float:
        return self._peak / MIB

    def _sample(self) -> None:
        total = self._process.memory_info().rss
        with suppress(self._psutil.Error):
            for child in self._process.children(recursive=True):
                with suppress(self._psutil.Error):
                    total += child.memory_info().rss
        self._peak = max(self._peak, total)

    def _poll(self) -> None:
        while not self._stop.wait(self._interval):
            self._sample()


def run_bench(
    corpus: list[CorpusSample],
    model: Any,
    *,
    warm_repeats: int = 3,
    clock: Callable[[], float] | None = None,
    sampler_factory: Callable[[], Any] | None = None,
) -> list[Run]:
    """One model, one recording at a time: a first pass, then `warm_repeats` passes."""
    now = clock or time.perf_counter
    sampler_for = sampler_factory or RssSampler
    runs: list[Run] = []
    for pass_index in range(warm_repeats + 1):
        for sample in corpus:
            if not runs:
                phase = "cold"
            else:
                phase = "first_pass" if pass_index == 0 else "warm"
            text, language, error = "", "", None
            with sampler_for() as sampler:
                started = now()
                try:
                    result = model.transcribe(str(sample.audio), **DECODE_PARAMETERS)
                    text = str(result.get("text", "")).strip()
                    language = str(result.get("language", ""))
                    if not text:
                        error = "empty transcription"
                except Exception as failure:
                    error = f"{type(failure).__name__}: {failure}"
                seconds = now() - started
            runs.append(
                Run(
                    sample_id=sample.id,
                    phase=phase,
                    pass_index=pass_index,
                    seconds=seconds,
                    rtf=seconds / sample.duration_seconds,
                    peak_rss_mib=sampler.peak_mib,
                    ok=error is None,
                    error=error,
                    text=text,
                    language=language,
                )
            )
    return runs


def build_samples_report(
    corpus: list[CorpusSample], runs: list[Run], warm_repeats: int
) -> tuple[list[dict[str, Any]], bool]:
    """Per-recording rows; a missing, empty or failed run is never a success."""
    samples = []
    for sample in corpus:
        own = [r for r in runs if r.sample_id == sample.id]
        warm = [r for r in own if r.phase == "warm" and r.ok]
        languages = sorted({r.language for r in own if r.ok})
        success = len(own) == warm_repeats + 1 and all(r.ok for r in own)
        success = success and len(warm) >= warm_repeats
        samples.append(
            {
                "id": sample.id,
                "reference_language": sample.language,
                "detected_language": next((r.language for r in own if r.ok), None),
                "language_stable": len(languages) == 1,
                "audio": {
                    "file": sample.audio.name,
                    "size_bytes": sample.size_bytes,
                    "sha256": _sha256(sample.audio),
                    "duration_seconds": sample.duration_seconds,
                },
                "runs": [
                    {
                        "phase": r.phase,
                        "pass": r.pass_index,
                        "seconds": r.seconds,
                        "rtf": r.rtf,
                        "peak_rss_mib": r.peak_rss_mib,
                        "ok": r.ok,
                        "error": r.error,
                        "text": r.text,
                        "language": r.language,
                    }
                    for r in own
                ],
                "warm": {
                    "count_ok": len(warm),
                    "seconds_max": max((r.seconds for r in warm), default=None),
                    "seconds_mean": (
                        sum(r.seconds for r in warm) / len(warm) if warm else None
                    ),
                    "rtf_max": max((r.rtf for r in warm), default=None),
                },
                "success": success,
            }
        )
    return samples, all(s["success"] for s in samples)


def read_cgroup_limits(root: Path = Path("/sys/fs/cgroup")) -> dict[str, Any]:
    """cgroup v2 limits of the container; `unavailable` when not readable."""

    def read(name: str) -> str | None:
        try:
            return (root / name).read_text().strip()
        except OSError:
            return None

    def number(raw: str | None) -> Any:
        if raw is None:
            return "unavailable"
        return None if raw == "max" else int(raw)  # None: no limit

    cpu = read("cpu.max")
    return {
        "memory_max_bytes": number(read("memory.max")),
        "memory_peak_bytes": number(read("memory.peak")),
        "cpu_max": "unavailable" if cpu is None else cpu,
    }


def collect_environment() -> dict[str, Any]:
    psutil = _optional_psutil()
    memory = psutil.virtual_memory() if psutil else None
    return {
        "python": platform.python_version(),
        "openai_whisper": _version("openai-whisper"),
        "torch": _version("torch"),
        "ffmpeg": _tool_version("ffmpeg"),
        "ffprobe": _tool_version("ffprobe"),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_model": _cpu_model(),
        "cpu_count_host": os.cpu_count(),
        "cpu_count_allowed": len(os.sched_getaffinity(0)),
        "ram_total_mib": memory.total / MIB if memory else "unavailable",
        "ram_available_mib": memory.available / MIB if memory else "unavailable",
        "cgroup": read_cgroup_limits(),
    }


def run_validation(
    manifest: Path, models_dir: Path, model_name: str, warm_repeats: int = 3
) -> dict[str, Any]:
    """Verify, load and run the whole protocol in this (fresh) process."""
    psutil = _optional_psutil()
    started_at = time.time()
    startup = {
        "process_startup_seconds": (
            started_at - psutil.Process().create_time() if psutil else None
        )
    }
    corpus = load_corpus(manifest)
    path = verify_weights(models_dir, model_name)
    environment = collect_environment()
    loading = time.perf_counter()
    model = load_whisper_model(path)  # imports whisper/torch, then reads weights
    startup["model_load_seconds"] = time.perf_counter() - loading
    rss_after_load = psutil.Process().memory_info().rss / MIB if psutil else None
    runs = run_bench(corpus, model, warm_repeats=warm_repeats)
    samples, success = build_samples_report(corpus, runs, warm_repeats)
    environment["cgroup_after_run"] = read_cgroup_limits()
    return {
        "schema": 1,
        "success": success,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": {
            "name": model_name,
            "weights_sha256": TRUSTED_WEIGHTS[model_name].sha256,
        },
        "parameters": {
            "decode": DECODE_PARAMETERS,
            "device": "cpu",
            "compute_type": "fp32",
            "torch_threads": _torch_threads(),
            "warm_repeats": warm_repeats,
        },
        "startup": startup,
        "memory": {
            "process_rss_mib_after_load": rss_after_load,
            "ru_maxrss_self_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            / 1024,
            "ru_maxrss_children_mib": resource.getrusage(
                resource.RUSAGE_CHILDREN
            ).ru_maxrss
            / 1024,
        },
        "environment": environment,
        "samples": samples,
    }


def _read_entries(manifest: Path) -> list[dict[str, Any]]:
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CorpusError(f"manifest {manifest} not found") from error
    except (OSError, ValueError) as error:
        raise CorpusError(f"manifest {manifest} is not valid JSON") from error
    entries = document.get("samples") if isinstance(document, dict) else None
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise CorpusError("manifest must contain a 'samples' list of objects")
    return entries


def _load_sample(
    entry: dict[str, Any], directory: Path, probe: DurationProbe
) -> CorpusSample:
    sample_id = _text(entry, "id", "<unknown>")
    language = _text(entry, "language", sample_id)
    if language not in LANGUAGES:
        raise CorpusError(f"{sample_id}: language must be one of {LANGUAGES}")
    texts = {field: _text(entry, field, sample_id) for field in STRING_FIELDS}
    lists = {field: _list(entry, field, sample_id) for field in LIST_FIELDS}
    audio = _audio_path(_text(entry, "audio", sample_id), directory, sample_id)
    size = audio.stat().st_size
    if size == 0:
        raise CorpusError(f"{sample_id}: audio file is empty")
    if size > MAX_SIZE_BYTES:
        raise CorpusError(f"{sample_id}: audio exceeds the 5 MiB limit")
    duration = probe(audio)
    if duration > MAX_DURATION_SECONDS:
        raise CorpusError(f"{sample_id}: audio exceeds 61 seconds")
    return CorpusSample(
        id=sample_id,
        language=language,
        audio=audio,
        expected_names=lists["expected_names"],
        expected_numbers=lists["expected_numbers"],
        duration_seconds=duration,
        size_bytes=size,
        **texts,
    )


def _text(entry: dict[str, Any], field: str, sample_id: str) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CorpusError(f"{sample_id}: {field} must be a non-empty string")
    return value


def _list(entry: dict[str, Any], field: str, sample_id: str) -> tuple[str, ...]:
    """Absence is stated explicitly: the key is required and `[]` means none."""
    value = entry.get(field)
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise CorpusError(
            f"{sample_id}: {field} must be a list of non-empty strings "
            "(use [] when none are expected)"
        )
    return tuple(value)


def _audio_path(name: str, directory: Path, sample_id: str) -> Path:
    root = directory.resolve()
    audio = (root / name).resolve()
    if not audio.is_relative_to(root):
        raise CorpusError(f"{sample_id}: audio path is outside the corpus directory")
    if not audio.is_file():
        raise CorpusError(f"{sample_id}: audio file {name} not found")
    if not os.access(audio, os.R_OK):
        raise CorpusError(
            f"{sample_id}: audio file {name} is not readable (permissions: the "
            "container user needs read access)"
        )
    return audio


def _check_unique_ids(samples: list[CorpusSample]) -> None:
    duplicates = [i for i, count in Counter(s.id for s in samples).items() if count > 1]
    if duplicates:
        raise CorpusError(f"duplicate sample id: {', '.join(duplicates)}")


def _check_composition(samples: list[CorpusSample]) -> None:
    for language in LANGUAGES:
        group = [s for s in samples if s.language == language]
        if len(group) != SAMPLES_PER_LANGUAGE:
            raise CorpusError(
                f"corpus needs three samples per language, {language} has {len(group)}"
            )
        durations = sorted(s.duration_seconds for s in group)
        if not all(
            low <= duration <= high
            for duration, (_, low, high) in zip(durations, DURATION_BUCKETS)
        ):
            raise CorpusError(
                f"{language} durations must be about 10, 30 and 60 seconds "
                f"(allowed 7-13, 25-35 and 50-61), got {durations}"
            )
        for field in LIST_FIELDS:
            if not any(getattr(s, field) for s in group):
                raise CorpusError(
                    f"language {language} needs at least one sample with {field}"
                )


def _trusted(model: str, trusted: Mapping[str, ModelWeights] | None) -> ModelWeights:
    table = TRUSTED_WEIGHTS if trusted is None else trusted
    if model not in table:
        raise WeightsError(f"{model!r} is not a pinned multilingual model")
    return table[model]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _check_digest(path: Path, expected: str) -> None:
    actual = _sha256(path)
    if actual != expected:
        raise WeightsError(
            f"SHA-256 mismatch for {path.name}: expected {expected}, got {actual}"
        )


def _optional_psutil() -> Any:
    try:
        return importlib.import_module("psutil")
    except ImportError:
        return None


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "unavailable"


def _tool_version(tool: str) -> str:
    try:
        output = subprocess.run(
            [tool, "-version"], capture_output=True, text=True, check=False
        ).stdout
    except OSError:
        return "unavailable"
    return output.splitlines()[0] if output else "unavailable"


def _cpu_model() -> str:
    with suppress(OSError):
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.partition(":")[2].strip()
    return "unavailable"


def _torch_threads() -> int | str:
    try:
        return int(importlib.import_module("torch").get_num_threads())
    except ImportError:
        return "unavailable"
