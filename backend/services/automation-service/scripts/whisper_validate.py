"""Local Whisper validation commands; never starts the application."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from infrastructure.stt import whisper_validation as validation


def check_corpus(manifest: Path) -> int:
    try:
        corpus = validation.load_corpus(manifest)
    except validation.CorpusError as error:
        print(f"corpus check failed: {error}", file=sys.stderr)
        return 1
    # Only measured facts: reference text never reaches the service logs.
    report = {
        "samples": [
            {
                "id": s.id,
                "language": s.language,
                "duration_seconds": s.duration_seconds,
                "size_bytes": s.size_bytes,
            }
            for s in corpus
        ]
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0


def prepare(models_dir: Path, model: str) -> int:
    """The only command allowed to download weights."""
    try:
        path = validation.prepare_weights(models_dir, model, validation.download_file)
    except validation.WeightsError as error:
        print(f"prepare failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"model": model, "path": str(path), "prepared": True}))
    return 0


def verify_weights(models_dir: Path, model: str) -> int:
    """Check the digest, then load the model from the local path; no network."""
    try:
        path = validation.verify_weights(models_dir, model)
        started = time.perf_counter()
        validation.load_whisper_model(path)
        load_seconds = time.perf_counter() - started
    except validation.WeightsError as error:
        print(f"verify failed: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"verify failed: model did not load: {error}", file=sys.stderr)
        return 1
    report = {
        "model": model,
        "path": str(path),
        "sha256": validation.TRUSTED_WEIGHTS[model].sha256,
        "size_bytes": path.stat().st_size,
        "loaded": True,
        "load_seconds": load_seconds,
    }
    print(json.dumps(report))
    return 0


def bench(manifest: Path, models_dir: Path, model: str, warm_repeats: int) -> int:
    """One JSON document on stdout (with transcripts); technical errors on stderr."""
    try:
        report = validation.run_validation(manifest, models_dir, model, warm_repeats)
    except (validation.CorpusError, validation.WeightsError) as error:
        print(f"bench failed: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"bench failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False))
    for sample in report["samples"]:
        for run in sample["runs"]:
            if not run["ok"]:
                print(
                    f"{sample['id']} pass {run['pass']}: {run['error']}",
                    file=sys.stderr,
                )
    return 0 if report["success"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-corpus", help="validate the owner's corpus")
    check.add_argument("--manifest", type=Path, default=Path("/corpus/manifest.json"))
    for name, help_text in (
        ("prepare", "download and verify weights (needs network)"),
        ("verify-weights", "verify and load local weights (no network)"),
        ("bench", "sequential cold/warm run over the corpus (no network)"),
    ):
        command = commands.add_parser(name, help=help_text)
        if name == "bench":
            command.add_argument(
                "--manifest", type=Path, default=Path("/corpus/manifest.json")
            )
            command.add_argument("--warm-repeats", type=int, default=3)
        command.add_argument(
            "--model", default=os.environ.get("WHISPER_MODEL") or "turbo"
        )
        command.add_argument(
            "--models-dir",
            type=Path,
            default=Path(os.environ.get("WHISPER_MODELS_DIR") or "/models"),
        )
    options = parser.parse_args(argv)
    if options.command == "check-corpus":
        return check_corpus(options.manifest)
    if options.command == "prepare":
        return prepare(options.models_dir, options.model)
    if options.command == "bench":
        if options.warm_repeats < 3:
            parser.error("--warm-repeats must be at least 3")
        return bench(
            options.manifest, options.models_dir, options.model, options.warm_repeats
        )
    return verify_weights(options.models_dir, options.model)


if __name__ == "__main__":
    raise SystemExit(main())
