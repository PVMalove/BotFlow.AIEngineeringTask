"""Drive the standalone Whisper validation Compose; never touches the live stack."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from compose_build import build_images

PROJECT = "botflow-whisper"
ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local" / "whisper"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build", help="build the stt-runtime image")
    commands.add_parser("prepare", help="download and verify weights into the volume")
    commands.add_parser("verify", help="verify and load weights without network")
    for name, help_text in (
        ("corpus-check", "validate the owner's corpus (read-only, no network)"),
        ("bench", "cold/warm run; saves the JSON result locally (no network)"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--corpus", type=Path, default=LOCAL / "corpus")
        if name == "bench":
            command.add_argument("--results", type=Path, default=LOCAL / "results.json")
    options = parser.parse_args(argv)
    corpus = getattr(options, "corpus", LOCAL / "corpus")
    if options.command in ("corpus-check", "bench") and not corpus.is_dir():
        print(f"corpus directory {corpus} does not exist", file=sys.stderr)
        return 2
    # The live stack's Compose selection must not leak into this project.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME")
    }
    env["COMPOSE_PROFILES"] = ""
    env["WHISPER_CORPUS_DIR"] = str(corpus.resolve())
    with tempfile.TemporaryDirectory(prefix=PROJECT + "-") as temporary:
        empty_env = Path(temporary) / "empty.env"
        empty_env.touch()
        compose = [
            "docker",
            "compose",
            "--project-directory",
            str(ROOT),
            "--env-file",
            str(empty_env),
            "-p",
            PROJECT,
            "-f",
            str(ROOT / "docker-compose.whisper.yml"),
        ]
        try:
            if options.command == "build":
                build_images(compose, env, ROOT)
                return 0
            run = [*compose, "run", "--rm", "--no-deps", "-T"]
            if options.command == "bench":
                return bench(run, env, options.results)
            service = f"whisper-{options.command}"
            return subprocess.run([*run, service], env=env, check=False).returncode
        except subprocess.CalledProcessError as error:
            print(error.stderr.decode(errors="replace")[-4000:], file=sys.stderr)
            return error.returncode


def bench(run: list[str], env: dict[str, str], results: Path) -> int:
    """Save the container's JSON; an old success never survives a new run."""
    results.parent.mkdir(parents=True, exist_ok=True)
    results.unlink(missing_ok=True)
    completed = subprocess.run(
        [*run, "whisper-bench"],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    document = parse_result(completed.stdout)
    if document is None:
        # The raw stdout may hold transcripts: never copy it into the marker.
        document = {
            "success": False,
            "error": f"bench produced no result (exit code {completed.returncode})",
        }
    else:
        document["host"] = host_facts(env)
        if completed.returncode != 0:
            document["success"] = False
    save_atomically(results, document)
    ok = document["success"] is True and completed.returncode == 0
    print(f"results saved to {results}: success={document['success']}", file=sys.stderr)
    return 0 if ok else completed.returncode or 1


def parse_result(stdout: str) -> dict[str, Any] | None:
    try:
        document = json.loads(stdout)
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def host_facts(env: dict[str, str]) -> dict[str, Any]:
    tag = env.get("BOTFLOW_IMAGE_TAG", "local")
    image = f"botflow-whisper-validation:{tag}"
    return {
        "image_id": capture(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image], env
        ),
        "git_commit": capture(["git", "-C", str(ROOT), "rev-parse", "HEAD"], env),
        "git_dirty": capture(["git", "-C", str(ROOT), "status", "--porcelain"], env)
        != "",
    }


def capture(command: list[str], env: dict[str, str]) -> str:
    try:
        done = subprocess.run(
            command, env=env, capture_output=True, text=True, check=False
        )
    except OSError:
        return "unavailable"
    return done.stdout.strip() if done.returncode == 0 else "unavailable"


def save_atomically(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2))
    os.replace(temporary, path)


if __name__ == "__main__":
    raise SystemExit(main())
