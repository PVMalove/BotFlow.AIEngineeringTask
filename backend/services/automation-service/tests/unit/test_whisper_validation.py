"""The Whisper corpus contract is enforced from files, never from declared values."""

import hashlib
import json
import runpy
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from infrastructure.stt.whisper_validation import (
    DECODE_PARAMETERS,
    TRUSTED_WEIGHTS,
    CorpusError,
    CorpusSample,
    ModelWeights,
    WeightsError,
    build_samples_report,
    ffprobe_duration,
    load_corpus,
    prepare_weights,
    read_cgroup_limits,
    run_bench,
    verify_weights,
)

MIB = 1024 * 1024
DURATIONS = {
    "ru-10": 9.6,
    "ru-30": 31.2,
    "ru-60": 58.4,
    "hy-10": 11.0,
    "hy-30": 28.7,
    "hy-60": 59.9,
}
SCRIPTS = Path(__file__).parents[2] / "scripts"


def sample(sample_id: str) -> dict[str, Any]:
    language = sample_id.split("-")[0]
    return {
        "id": sample_id,
        "language": language,
        "audio": f"{sample_id}.ogg",
        "reference_text": "Купить молоко до пятницы"
        if language == "ru"
        else "Գնել կաթ ուրբաթից առաջ",
        "expected_action": "купить" if language == "ru" else "գնել",
        "expected_object": "молоко" if language == "ru" else "կաթ",
        "expected_names": ["Анна"] if language == "ru" else ["Արամ"],
        "expected_numbers": ["5"],
    }


def write_corpus(directory: Path, samples: list[dict[str, Any]] | None = None) -> Path:
    entries = samples if samples is not None else [sample(i) for i in DURATIONS]
    for entry in entries:
        audio = directory / str(entry["audio"])
        if not audio.exists():
            audio.parent.mkdir(parents=True, exist_ok=True)
            audio.write_bytes(b"opus")
    manifest = directory / "manifest.json"
    manifest.write_text(json.dumps({"samples": entries}), encoding="utf-8")
    return manifest


def probe_from(durations: dict[str, float]) -> Callable[[Path], float]:
    def probe(path: Path) -> float:
        return durations[path.stem]

    return probe


def test_valid_corpus_uses_measured_values_not_declared_ones(tmp_path: Path) -> None:
    entries = [sample(i) | {"duration_seconds": 1, "size_bytes": 1} for i in DURATIONS]
    manifest = write_corpus(tmp_path, entries)

    corpus = load_corpus(manifest, probe_duration=probe_from(DURATIONS))

    assert [s.id for s in corpus] == list(DURATIONS)
    assert [s.duration_seconds for s in corpus] == list(DURATIONS.values())
    assert all(s.size_bytes == 4 for s in corpus)
    assert corpus[0].audio == (tmp_path / "ru-10.ogg").resolve()
    assert corpus[3].reference_text == "Գնել կաթ ուրբաթից առաջ"
    assert corpus[3].expected_names == ("Արամ",)


def test_explicit_empty_names_and_numbers_are_accepted_when_some_exist(
    tmp_path: Path,
) -> None:
    entries = [sample(i) for i in DURATIONS]
    entries[1] |= {"expected_names": [], "expected_numbers": []}
    manifest = write_corpus(tmp_path, entries)

    corpus = load_corpus(manifest, probe_duration=probe_from(DURATIONS))

    assert corpus[1].expected_names == ()
    assert corpus[1].expected_numbers == ()


@pytest.mark.parametrize("field", ["expected_names", "expected_numbers"])
def test_absence_of_names_or_numbers_must_be_explicit(
    tmp_path: Path, field: str
) -> None:
    entries = [sample(i) for i in DURATIONS]
    del entries[0][field]
    manifest = write_corpus(tmp_path, entries)

    with pytest.raises(CorpusError, match=rf"ru-10.*{field}"):
        load_corpus(manifest, probe_duration=probe_from(DURATIONS))


@pytest.mark.parametrize("language", ["ru", "hy"])
@pytest.mark.parametrize("field", ["expected_names", "expected_numbers"])
def test_each_language_needs_names_and_numbers(
    tmp_path: Path, language: str, field: str
) -> None:
    entries = [
        sample(i) | ({field: []} if i.startswith(language) else {}) for i in DURATIONS
    ]
    manifest = write_corpus(tmp_path, entries)

    with pytest.raises(CorpusError, match=rf"{language}.*{field}"):
        load_corpus(manifest, probe_duration=probe_from(DURATIONS))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"language": "en"}, "language"),
        ({"reference_text": ""}, "reference_text"),
        ({"reference_text": "   "}, "reference_text"),
        ({"expected_action": ""}, "expected_action"),
        ({"expected_object": ""}, "expected_object"),
        ({"id": ""}, "id"),
    ],
)
def test_invalid_fields_are_rejected(
    tmp_path: Path, change: dict[str, str], message: str
) -> None:
    entries = [sample(i) for i in DURATIONS]
    entries[2] |= change
    manifest = write_corpus(tmp_path, entries)

    with pytest.raises(CorpusError, match=message):
        load_corpus(manifest, probe_duration=probe_from(DURATIONS))


def test_missing_required_field_names_the_sample(tmp_path: Path) -> None:
    entries = [sample(i) for i in DURATIONS]
    del entries[4]["reference_text"]
    manifest = write_corpus(tmp_path, entries)

    with pytest.raises(CorpusError, match="hy-30.*reference_text"):
        load_corpus(manifest, probe_duration=probe_from(DURATIONS))


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    entries = [sample(i) for i in DURATIONS]
    entries[1] = sample("ru-10") | {"audio": "ru-30.ogg"}
    manifest = write_corpus(tmp_path, entries)

    with pytest.raises(CorpusError, match="duplicate.*ru-10"):
        load_corpus(manifest, probe_duration=probe_from(DURATIONS))


def test_missing_audio_file_is_rejected(tmp_path: Path) -> None:
    manifest = write_corpus(tmp_path)
    (tmp_path / "hy-60.ogg").unlink()

    with pytest.raises(CorpusError, match="hy-60.*not found"):
        load_corpus(manifest, probe_duration=probe_from(DURATIONS))


def test_audio_outside_corpus_directory_is_rejected(tmp_path: Path) -> None:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (tmp_path / "outside.ogg").write_bytes(b"opus")
    link_target = tmp_path / "outside.ogg"
    (corpus_dir / "linked.ogg").symlink_to(link_target)
    escapes = ["../outside.ogg", str(link_target), "linked.ogg"]

    for escape in escapes:
        entries = [sample(i) for i in DURATIONS]
        entries[0] |= {"audio": escape}
        manifest = write_corpus(corpus_dir, entries)
        with pytest.raises(CorpusError, match="ru-10.*outside"):
            load_corpus(manifest, probe_duration=probe_from(DURATIONS))


@pytest.mark.parametrize(
    "ids",
    [
        ["ru-10", "ru-30", "ru-60", "hy-10", "hy-30"],
        ["ru-10", "ru-30", "ru-60", "ru-70", "hy-10", "hy-30"],
        [*DURATIONS, "ru-extra"],
    ],
)
def test_corpus_is_three_samples_per_language(tmp_path: Path, ids: list[str]) -> None:
    manifest = write_corpus(tmp_path, [sample(i) for i in ids])
    durations = DURATIONS | {"ru-70": 20.0, "ru-extra": 20.0}

    with pytest.raises(CorpusError, match="three.*per language"):
        load_corpus(manifest, probe_duration=probe_from(durations))


def test_durations_must_be_near_ten_thirty_and_sixty_seconds(tmp_path: Path) -> None:
    manifest = write_corpus(tmp_path)
    durations = DURATIONS | {"ru-30": 12.0}

    with pytest.raises(CorpusError, match="ru.*10, 30 and 60"):
        load_corpus(manifest, probe_duration=probe_from(durations))


def test_duration_tolerance_boundaries_are_explicit(tmp_path: Path) -> None:
    manifest = write_corpus(tmp_path)
    edge = {
        "ru-10": 7.0,
        "ru-30": 35.0,
        "ru-60": 50.0,
        "hy-10": 13.0,
        "hy-30": 25.0,
        "hy-60": 61.0,
    }

    assert len(load_corpus(manifest, probe_duration=probe_from(edge))) == 6

    for sample_id, value in {"ru-10": 6.9, "ru-30": 35.1, "hy-60": 49.9}.items():
        with pytest.raises(CorpusError):
            load_corpus(manifest, probe_duration=probe_from(edge | {sample_id: value}))


def test_audio_longer_than_sixty_seconds_is_rejected(tmp_path: Path) -> None:
    manifest = write_corpus(tmp_path)

    # Opus framing makes a 60-second recording probe as 60.006: still accepted.
    opus_sixty = probe_from(DURATIONS | {"hy-60": 60.0065})
    assert len(load_corpus(manifest, probe_duration=opus_sixty)) == 6

    with pytest.raises(CorpusError, match="hy-60.*61 seconds"):
        load_corpus(manifest, probe_duration=probe_from(DURATIONS | {"hy-60": 61.01}))


def test_audio_larger_than_five_mebibytes_is_rejected(tmp_path: Path) -> None:
    manifest = write_corpus(tmp_path)

    with (tmp_path / "ru-60.ogg").open("r+b") as audio:
        audio.truncate(5 * MIB)
    assert len(load_corpus(manifest, probe_duration=probe_from(DURATIONS))) == 6

    with (tmp_path / "ru-60.ogg").open("r+b") as audio:
        audio.truncate(5 * MIB + 1)
    with pytest.raises(CorpusError, match="ru-60.*5 MiB"):
        load_corpus(manifest, probe_duration=probe_from(DURATIONS))


def test_empty_audio_file_is_rejected_before_probing(tmp_path: Path) -> None:
    manifest = write_corpus(tmp_path)
    (tmp_path / "ru-10.ogg").write_bytes(b"")

    def probe(path: Path) -> float:
        raise AssertionError("probe must not run for an empty file")

    with pytest.raises(CorpusError, match="ru-10.*empty"):
        load_corpus(manifest, probe_duration=probe)


@pytest.mark.parametrize("content", ["not json", "[]", '{"samples": "x"}', "{}"])
def test_malformed_manifest_is_rejected(tmp_path: Path, content: str) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(content, encoding="utf-8")

    with pytest.raises(CorpusError, match="manifest"):
        load_corpus(manifest, probe_duration=probe_from(DURATIONS))


def test_missing_manifest_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="manifest.*not found"):
        load_corpus(tmp_path / "manifest.json", probe_duration=probe_from(DURATIONS))


def completed(
    stdout: str, returncode: int = 0
) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, returncode, stdout, "boom")

    return run


def test_ffprobe_duration_reads_seconds_from_the_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "12.345\n", "")

    monkeypatch.setattr(subprocess, "run", run)

    assert ffprobe_duration(tmp_path / "a.ogg") == 12.345
    assert commands[0][0] == "ffprobe"
    assert str(tmp_path / "a.ogg") in commands[0]


def test_missing_ffprobe_is_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(CorpusError, match="ffprobe is not installed"):
        ffprobe_duration(tmp_path / "a.ogg")


@pytest.mark.parametrize(
    ("stdout", "returncode"),
    [("", 1), ("N/A\n", 0), ("0\n", 0), ("nan\n", 0), ("-3\n", 0)],
)
def test_corrupt_audio_is_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stdout: str, returncode: int
) -> None:
    monkeypatch.setattr(subprocess, "run", completed(stdout, returncode))

    with pytest.raises(CorpusError, match="a.ogg.*readable audio"):
        ffprobe_duration(tmp_path / "a.ogg")


def run_cli(argv: list[str]) -> int:
    sys.path.insert(0, str(SCRIPTS))
    try:
        cli = runpy.run_path(str(SCRIPTS / "whisper_validate.py"))
    finally:
        sys.path.remove(str(SCRIPTS))
    result = cli["main"](argv)
    assert isinstance(result, int)
    return result


def test_check_corpus_reports_ok_without_transcript_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = write_corpus(tmp_path)
    monkeypatch.setattr(
        "infrastructure.stt.whisper_validation.ffprobe_duration",
        probe_from(DURATIONS),
    )

    assert run_cli(["check-corpus", "--manifest", str(manifest)]) == 0

    report = json.loads(capsys.readouterr().out)
    assert [row["id"] for row in report["samples"]] == list(DURATIONS)
    assert "Купить" not in json.dumps(report, ensure_ascii=False)


def test_check_corpus_fails_with_stderr_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_cli(["check-corpus", "--manifest", str(tmp_path / "manifest.json")]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "manifest" in captured.err


# SHA-256 of b"abc" (FIPS 180-2 test vector): independent of the code under test.
ABC = b"abc"
ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
TRUSTED = {"small": ModelWeights("https://weights.test/small.pt", ABC_SHA256)}


def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden here")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


def fake_download(content: bytes) -> Callable[[str, Path], None]:
    def download(url: str, destination: Path) -> None:
        destination.write_bytes(content)

    return download


def test_verify_accepts_weights_matching_the_trusted_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_network(monkeypatch)
    (tmp_path / "small.pt").write_bytes(ABC)

    assert verify_weights(tmp_path, "small", TRUSTED) == tmp_path / "small.pt"


def test_verify_fails_for_missing_weights_without_downloading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_network(monkeypatch)

    with pytest.raises(WeightsError, match="small.*missing.*prepare"):
        verify_weights(tmp_path, "small", TRUSTED)
    with pytest.raises(WeightsError, match="missing"):
        verify_weights(tmp_path / "no-such-volume", "small", TRUSTED)


def test_verify_fails_for_digest_mismatch_without_downloading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_network(monkeypatch)
    (tmp_path / "small.pt").write_bytes(b"abd")

    with pytest.raises(WeightsError, match="SHA-256 mismatch"):
        verify_weights(tmp_path, "small", TRUSTED)


@pytest.mark.parametrize("model", ["small.en", "tiny.en", "unknown", ""])
def test_only_pinned_multilingual_models_are_accepted(
    tmp_path: Path, model: str
) -> None:
    with pytest.raises(WeightsError, match="not a pinned multilingual model"):
        verify_weights(tmp_path, model, TRUSTED)
    with pytest.raises(WeightsError, match="not a pinned multilingual model"):
        prepare_weights(tmp_path, model, fake_download(ABC), TRUSTED)


def test_prepare_downloads_once_and_publishes_only_verified_weights(
    tmp_path: Path,
) -> None:
    urls: list[str] = []

    def download(url: str, destination: Path) -> None:
        urls.append(url)
        assert destination != tmp_path / "small.pt"
        destination.write_bytes(ABC)

    path = prepare_weights(tmp_path, "small", download, TRUSTED)

    assert urls == ["https://weights.test/small.pt"]
    assert path == tmp_path / "small.pt"
    assert path.read_bytes() == ABC
    assert [p.name for p in tmp_path.iterdir()] == ["small.pt"]
    assert verify_weights(tmp_path, "small", TRUSTED) == path


def test_prepare_does_not_download_again_when_weights_are_valid(
    tmp_path: Path,
) -> None:
    (tmp_path / "small.pt").write_bytes(ABC)

    def download(url: str, destination: Path) -> None:
        raise AssertionError("valid weights must not be downloaded again")

    assert (
        prepare_weights(tmp_path, "small", download, TRUSTED) == tmp_path / "small.pt"
    )


def test_prepare_replaces_corrupt_weights_with_verified_ones(tmp_path: Path) -> None:
    (tmp_path / "small.pt").write_bytes(b"corrupt")

    prepare_weights(tmp_path, "small", fake_download(ABC), TRUSTED)

    assert (tmp_path / "small.pt").read_bytes() == ABC


def test_prepare_rejects_a_download_with_the_wrong_digest(tmp_path: Path) -> None:
    with pytest.raises(WeightsError, match="SHA-256 mismatch"):
        prepare_weights(tmp_path, "small", fake_download(b"tampered"), TRUSTED)

    assert list(tmp_path.iterdir()) == []


def test_prepare_keeps_no_partial_file_when_the_download_fails(
    tmp_path: Path,
) -> None:
    def download(url: str, destination: Path) -> None:
        destination.write_bytes(b"ab")
        raise OSError("connection reset")

    with pytest.raises(WeightsError, match="download failed.*connection reset"):
        prepare_weights(tmp_path, "small", download, TRUSTED)

    assert list(tmp_path.iterdir()) == []


def test_trusted_table_pins_multilingual_https_weights_by_digest() -> None:
    assert "small" in TRUSTED_WEIGHTS
    for name, weights in TRUSTED_WEIGHTS.items():
        assert not name.endswith(".en")
        assert weights.url.startswith("https://")
        assert f"/{weights.sha256}/" in weights.url
        assert len(weights.sha256) == 64


def cli_environment(
    monkeypatch: pytest.MonkeyPatch, models: Path, trusted: dict[str, ModelWeights]
) -> None:
    monkeypatch.setattr(
        "infrastructure.stt.whisper_validation.TRUSTED_WEIGHTS", trusted
    )
    monkeypatch.setenv("WHISPER_MODELS_DIR", str(models))


def test_cli_prepare_then_verify_loads_the_verified_local_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_environment(monkeypatch, tmp_path, TRUSTED)
    monkeypatch.setattr(
        "infrastructure.stt.whisper_validation.download_file", fake_download(ABC)
    )
    loaded: list[object] = []
    monkeypatch.setattr(
        "infrastructure.stt.whisper_validation.load_whisper_model",
        lambda path: loaded.append(path),
    )

    assert run_cli(["prepare", "--model", "small"]) == 0
    no_network(monkeypatch)
    assert run_cli(["verify-weights", "--model", "small"]) == 0

    assert loaded == [tmp_path / "small.pt"]
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report["model"] == "small"
    assert report["sha256"] == ABC_SHA256
    assert report["loaded"] is True


def test_cli_verify_fails_without_weights_and_never_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_environment(monkeypatch, tmp_path, TRUSTED)
    no_network(monkeypatch)

    def loader(path: Path) -> None:
        raise AssertionError("the model must not load before verification")

    monkeypatch.setattr(
        "infrastructure.stt.whisper_validation.load_whisper_model", loader
    )

    assert run_cli(["verify-weights", "--model", "small"]) == 1
    assert "missing" in capsys.readouterr().err


def test_cli_verify_fails_when_the_model_cannot_be_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_environment(monkeypatch, tmp_path, TRUSTED)
    (tmp_path / "small.pt").write_bytes(ABC)

    def loader(path: Path) -> None:
        raise RuntimeError("not a checkpoint")

    monkeypatch.setattr(
        "infrastructure.stt.whisper_validation.load_whisper_model", loader
    )

    assert run_cli(["verify-weights", "--model", "small"]) == 1
    assert "not a checkpoint" in capsys.readouterr().err


class FakeClock:
    """Time moves only when the fake model "decodes", by independent amounts."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class FakeModel:
    def __init__(
        self,
        clock: FakeClock,
        seconds: dict[str, float],
        text: dict[str, str] | None = None,
        failures: set[tuple[str, int]] | None = None,
    ) -> None:
        self.clock = clock
        self.seconds = seconds
        self.text = text or {}
        self.failures = failures or set()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.seen: dict[str, int] = {}

    def transcribe(self, audio: str, **kwargs: Any) -> dict[str, str]:
        stem = Path(audio).stem
        self.calls.append((audio, kwargs))
        attempt = self.seen[stem] = self.seen.get(stem, -1) + 1
        self.clock.now += self.seconds[stem]
        if (stem, attempt) in self.failures:
            raise RuntimeError("decoder exploded")
        language = stem.split("-")[0]
        return {"text": self.text.get(stem, f"text of {stem}"), "language": language}


class FakeSampler:
    def __enter__(self) -> "FakeSampler":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    @property
    def peak_mib(self) -> float:
        return 321.5


SECONDS = {
    "ru-10": 2.0,
    "ru-30": 6.0,
    "ru-60": 12.0,
    "hy-10": 3.0,
    "hy-30": 9.0,
    "hy-60": 18.0,
}


def corpus_of(directory: Path) -> list[CorpusSample]:
    return load_corpus(write_corpus(directory), probe_duration=probe_from(DURATIONS))


def bench(
    directory: Path, model: FakeModel, clock: FakeClock, repeats: int = 3
) -> tuple[list[CorpusSample], list[Any]]:
    corpus = corpus_of(directory)
    runs = run_bench(
        corpus, model, warm_repeats=repeats, clock=clock, sampler_factory=FakeSampler
    )
    return corpus, runs


def test_model_gets_no_language_hint_translation_or_reference_prompt(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    model = FakeModel(clock, SECONDS)

    corpus, _ = bench(tmp_path, model, clock)

    assert len(model.calls) == 24
    for audio, kwargs in model.calls:
        assert kwargs["task"] == "transcribe"
        assert kwargs["language"] is None
        assert kwargs["initial_prompt"] is None
        assert kwargs["fp16"] is False
        assert kwargs["verbose"] is None
        assert audio in {str(s.audio) for s in corpus}
        passed = [str(v) for v in kwargs.values()]
        assert not {"ru", "hy"} & set(passed)
        assert not any(s.reference_text in passed for s in corpus)
    assert {k: v for k, v in model.calls[0][1].items()} == dict(DECODE_PARAMETERS)


def test_one_model_serves_every_recording_in_sequential_passes(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    model = FakeModel(clock, SECONDS)

    _, runs = bench(tmp_path, model, clock)

    order = [Path(audio).stem for audio, _ in model.calls]
    assert order == list(DURATIONS) * 4
    assert [(r.sample_id, r.pass_index) for r in runs] == [
        (i, n) for n in range(4) for i in DURATIONS
    ]
    assert [r.phase for r in runs[:7]] == ["cold"] + ["first_pass"] * 5 + ["warm"]
    assert {r.phase for r in runs[6:]} == {"warm"}


def test_times_and_rtf_come_from_the_clock_and_measured_duration(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    model = FakeModel(clock, SECONDS)

    corpus, runs = bench(tmp_path, model, clock)

    for run in runs:
        assert run.seconds == SECONDS[run.sample_id]
        assert run.rtf == pytest.approx(
            SECONDS[run.sample_id] / DURATIONS[run.sample_id]
        )
        assert run.peak_rss_mib == 321.5
        assert run.ok and run.error is None


def test_report_keeps_every_id_with_raw_times_and_maximum(tmp_path: Path) -> None:
    clock = FakeClock()
    model = FakeModel(clock, SECONDS)

    corpus, runs = bench(tmp_path, model, clock)
    samples, success = build_samples_report(corpus, runs, warm_repeats=3)

    assert success is True
    assert [s["id"] for s in samples] == list(DURATIONS)
    ru10 = samples[0]
    assert ru10["reference_language"] == "ru"
    assert ru10["detected_language"] == "ru"
    assert ru10["language_stable"] is True
    assert ru10["audio"]["size_bytes"] == 4
    assert ru10["audio"]["sha256"] == hashlib.sha256(b"opus").hexdigest()
    assert ru10["audio"]["duration_seconds"] == 9.6
    assert len(ru10["runs"]) == 4
    assert ru10["runs"][0]["text"] == "text of ru-10"
    assert [r["seconds"] for r in ru10["runs"] if r["phase"] == "warm"] == [2.0] * 3
    assert ru10["warm"]["count_ok"] == 3
    assert ru10["warm"]["seconds_max"] == 2.0


def test_empty_result_and_exceptions_are_failures_but_runs_continue(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    model = FakeModel(
        clock,
        SECONDS,
        text={"ru-30": "   "},
        failures={("hy-10", 0)},
    )

    corpus, runs = bench(tmp_path, model, clock)
    samples, success = build_samples_report(corpus, runs, warm_repeats=3)

    assert success is False
    assert len(runs) == 24
    by_id = {s["id"]: s for s in samples}
    assert by_id["ru-30"]["runs"][0]["error"] == "empty transcription"
    assert by_id["ru-30"]["warm"]["count_ok"] == 0
    failed = by_id["hy-10"]["runs"][0]
    assert failed["ok"] is False
    assert failed["error"] == "RuntimeError: decoder exploded"
    assert failed["text"] == ""
    assert by_id["hy-10"]["warm"]["count_ok"] == 3
    assert by_id["ru-10"]["success"] is True
    assert by_id["hy-10"]["success"] is False


def test_a_missing_warm_repeat_is_never_a_success(tmp_path: Path) -> None:
    clock = FakeClock()
    model = FakeModel(clock, SECONDS, failures={("hy-60", 3)})

    corpus, runs = bench(tmp_path, model, clock)
    samples, success = build_samples_report(corpus, runs, warm_repeats=3)

    assert success is False
    assert samples[-1]["warm"]["count_ok"] == 2
    # dropping runs entirely must not look like success either
    _, dropped = build_samples_report(corpus, runs[:-3], warm_repeats=3)
    assert dropped is False


def test_cgroup_limits_are_read_or_reported_unavailable(tmp_path: Path) -> None:
    (tmp_path / "memory.max").write_text("max\n")
    (tmp_path / "memory.peak").write_text("1048576\n")
    (tmp_path / "cpu.max").write_text("200000 100000\n")

    assert read_cgroup_limits(tmp_path) == {
        "memory_max_bytes": None,
        "memory_peak_bytes": 1048576,
        "cpu_max": "200000 100000",
    }
    assert read_cgroup_limits(tmp_path / "absent") == {
        "memory_max_bytes": "unavailable",
        "memory_peak_bytes": "unavailable",
        "cpu_max": "unavailable",
    }


def cli_bench_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: FakeModel
) -> Path:
    models = tmp_path / "models"
    models.mkdir()
    (models / "small.pt").write_bytes(ABC)
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    manifest = write_corpus(corpus_dir)
    cli_environment(monkeypatch, models, TRUSTED)
    monkeypatch.setattr(
        "infrastructure.stt.whisper_validation.ffprobe_duration",
        probe_from(DURATIONS),
    )
    monkeypatch.setattr(
        "infrastructure.stt.whisper_validation.load_whisper_model", lambda path: model
    )
    monkeypatch.setattr("infrastructure.stt.whisper_validation.RssSampler", FakeSampler)
    return manifest


def test_cli_bench_prints_one_json_document_and_no_transcripts_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = FakeClock()
    model = FakeModel(clock, SECONDS)
    manifest = cli_bench_environment(tmp_path, monkeypatch, model)
    no_network(monkeypatch)

    assert run_cli(["bench", "--manifest", str(manifest), "--model", "small"]) == 0

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["success"] is True
    assert report["model"] == {"name": "small", "weights_sha256": ABC_SHA256}
    assert report["parameters"]["decode"] == dict(DECODE_PARAMETERS)
    assert report["parameters"]["device"] == "cpu"
    assert {"process_startup_seconds", "model_load_seconds"} <= set(report["startup"])
    assert len(report["samples"]) == 6
    assert "text of" not in captured.err
    assert "Купить" not in captured.err


def test_cli_bench_reports_failure_with_json_and_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = FakeClock()
    model = FakeModel(clock, SECONDS, failures={("ru-10", 1)})
    manifest = cli_bench_environment(tmp_path, monkeypatch, model)

    assert run_cli(["bench", "--manifest", str(manifest), "--model", "small"]) == 1

    captured = capsys.readouterr()
    assert json.loads(captured.out)["success"] is False
    assert "decoder exploded" in captured.err
    assert "text of" not in captured.err


def test_cli_bench_without_verified_weights_never_downloads_or_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = FakeClock()
    manifest = cli_bench_environment(tmp_path, monkeypatch, FakeModel(clock, SECONDS))
    (tmp_path / "models" / "small.pt").write_bytes(b"tampered")
    no_network(monkeypatch)

    def loader(path: Path) -> None:
        raise AssertionError("an unverified checkpoint must not be loaded")

    monkeypatch.setattr(
        "infrastructure.stt.whisper_validation.load_whisper_model", loader
    )

    assert run_cli(["bench", "--manifest", str(manifest), "--model", "small"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "SHA-256 mismatch" in captured.err


def test_unreadable_audio_names_permissions_not_a_corrupt_file(tmp_path: Path) -> None:
    manifest = write_corpus(tmp_path)
    (tmp_path / "ru-30.ogg").chmod(0o000)

    try:
        with pytest.raises(CorpusError, match="ru-30.*not readable.*permissions"):
            load_corpus(manifest, probe_duration=probe_from(DURATIONS))
    finally:
        (tmp_path / "ru-30.ogg").chmod(0o644)
