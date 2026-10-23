"""The Whisper launcher owns its Compose file and never touches the live stack."""

import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).parents[1]
ROOT = SCRIPTS.parent


def load_launcher(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    monkeypatch.setattr(sys, "argv", [str(SCRIPTS / "whisper-validation.py")])
    return runpy.run_path(str(SCRIPTS / "whisper-validation.py"))


def test_build_uses_only_whisper_compose_without_live_stack_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPOSE_FILE", str(ROOT / "docker-compose.yml"))
    monkeypatch.setenv("COMPOSE_PROFILES", "workers,bootstrap")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "operator-stack")
    monkeypatch.delenv("BOTFLOW_BUILD_NETWORK", raising=False)
    calls: list[tuple[list[str], dict[str, str]]] = []

    def docker_boundary(
        command: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    launcher = load_launcher(monkeypatch)
    monkeypatch.setattr(subprocess, "run", docker_boundary)

    assert launcher["main"](["build"]) == 0

    [(command, env)] = calls
    compose_files = [command[i + 1] for i, a in enumerate(command) if a == "-f"]
    assert command[:2] == ["docker", "compose"]
    assert compose_files == [str(ROOT / "docker-compose.whisper.yml")]
    assert command[-1] == "build"
    assert {"up", "run", "down"}.isdisjoint(command)
    assert command[command.index("-p") + 1] == "botflow-whisper"
    assert "COMPOSE_FILE" not in env
    assert "COMPOSE_PROJECT_NAME" not in env
    assert env["COMPOSE_PROFILES"] == ""
    assert Path(command[command.index("--env-file") + 1]) != ROOT / ".env"


def test_build_failure_returns_docker_exit_code_and_reports_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("BOTFLOW_BUILD_NETWORK", raising=False)

    def docker_boundary(command: list[str], **kwargs: Any) -> None:
        raise subprocess.CalledProcessError(3, command, stderr=b"resolver failed")

    launcher = load_launcher(monkeypatch)
    monkeypatch.setattr(subprocess, "run", docker_boundary)

    assert launcher["main"](["build"]) == 3
    assert "resolver failed" in capsys.readouterr().err


RESULT = {"schema": 1, "success": True, "samples": [{"id": "ru-10", "runs": []}]}


class Docker:
    """Boundary double for docker/git; records every command and its env."""

    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.commands: list[list[str]] = []
        self.envs: list[dict[str, str]] = []

    def __call__(
        self, command: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        self.envs.append(kwargs.get("env") or {})
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, "sha256:feed\n", "")
        if command[0] == "git":
            out = "" if "status" in command else "c0ffee\n"
            return subprocess.CompletedProcess(command, 0, out, "")
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, "")

    @property
    def compose_runs(self) -> list[list[str]]:
        return [c for c in self.commands if c[:2] == ["docker", "compose"]]


def run_bench(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, docker: Docker
) -> tuple[int, Path, Path]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    results = tmp_path / "out" / "results.json"
    launcher = load_launcher(monkeypatch)
    monkeypatch.setattr(subprocess, "run", docker)
    code = launcher["main"](
        ["bench", "--corpus", str(corpus), "--results", str(results)]
    )
    return code, corpus, results


def test_bench_saves_the_container_json_with_host_facts_atomically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    docker = Docker(stdout=json.dumps(RESULT))
    results = tmp_path / "out" / "results.json"
    results.parent.mkdir()
    results.write_text('{"success": true, "stale": 1}')

    code, corpus, results = run_bench(monkeypatch, tmp_path, docker)

    saved = json.loads(results.read_text())
    assert code == 0
    assert saved["samples"] == RESULT["samples"] and "stale" not in saved
    assert saved["host"]["image_id"] == "sha256:feed"
    assert saved["host"]["git_commit"] == "c0ffee"
    assert [p.name for p in results.parent.iterdir()] == ["results.json"]
    [run] = docker.compose_runs
    assert run[-5:] == ["run", "--rm", "--no-deps", "-T", "whisper-bench"]
    assert {"up", "down"}.isdisjoint(run)
    assert docker.envs[docker.commands.index(run)]["WHISPER_CORPUS_DIR"] == str(corpus)


@pytest.mark.parametrize(
    ("stdout", "returncode"),
    [("", 137), ("progress... not json", 0), ("[1, 2]", 0)],
)
def test_a_failed_run_never_leaves_an_old_success_behind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stdout: str, returncode: int
) -> None:
    results = tmp_path / "out" / "results.json"
    results.parent.mkdir()
    results.write_text('{"success": true}')

    code, _, results = run_bench(monkeypatch, tmp_path, Docker(stdout, returncode))

    saved = json.loads(results.read_text())
    assert code != 0
    assert saved["success"] is False
    assert "not json" not in results.read_text()


def test_a_failed_protocol_keeps_its_report_but_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failed = RESULT | {"success": False}

    code, _, results = run_bench(
        monkeypatch, tmp_path, Docker(json.dumps(failed), returncode=1)
    )

    saved = json.loads(results.read_text())
    assert code == 1
    assert saved["success"] is False and saved["samples"] == RESULT["samples"]


def test_bench_without_a_corpus_directory_does_not_start_docker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    docker = Docker()
    launcher = load_launcher(monkeypatch)
    monkeypatch.setattr(subprocess, "run", docker)
    corpus = tmp_path / "missing"

    code = launcher["main"](
        ["bench", "--corpus", str(corpus), "--results", str(tmp_path / "r.json")]
    )

    assert code != 0
    assert docker.commands == []
    assert not corpus.exists()
    assert "corpus" in capsys.readouterr().err


def test_offline_commands_run_one_isolated_service_each(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for command, service in (
        ("verify", "whisper-verify"),
        ("prepare", "whisper-prepare"),
        ("corpus-check", "whisper-corpus-check"),
    ):
        docker = Docker()
        launcher = load_launcher(monkeypatch)
        monkeypatch.setattr(subprocess, "run", docker)
        corpus = tmp_path / "corpus"
        corpus.mkdir(exist_ok=True)

        extra = ["--corpus", str(corpus)] if command == "corpus-check" else []
        assert launcher["main"]([command, *extra]) == 0

        [run] = docker.compose_runs
        assert run[-5:] == ["run", "--rm", "--no-deps", "-T", service]
        assert "docker-compose.yml" not in " ".join(run)


@pytest.mark.parametrize(
    ("target", "command"),
    [
        ("whisper-build", "build"),
        ("whisper-prepare", "prepare"),
        ("whisper-verify", "verify"),
        ("whisper-corpus-check", "corpus-check"),
        ("whisper-bench", "bench"),
    ],
)
def test_make_targets_only_call_the_launcher(target: str, command: str) -> None:
    dry_run = subprocess.run(
        ["make", "-n", "-s", target],
        check=False,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    lines = dry_run.stdout.strip().splitlines()
    assert dry_run.returncode == 0
    assert len(lines) == 1
    assert lines[0].endswith(f"scripts/whisper-validation.py {command}")
    assert not {"sync", "install", "deps", "pip", "--build"} & set(lines[0].split())


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not installed")
def test_compose_isolates_offline_services_and_shares_the_weights_volume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def config(env: dict[str, str]) -> dict[str, Any]:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                "/dev/null",
                "-p",
                "botflow-whisper",
                "-f",
                str(ROOT / "docker-compose.whisper.yml"),
                "config",
                "--format",
                "json",
            ],
            env={**env, "PATH": str(Path(shutil.which("docker") or "").parent)},
            capture_output=True,
            text=True,
            check=True,
        )
        parsed: dict[str, Any] = json.loads(result.stdout)
        return parsed

    default = config({"WHISPER_CORPUS_DIR": str(ROOT)})
    services = default["services"]

    for name in ("whisper-verify", "whisper-corpus-check", "whisper-bench"):
        assert services[name]["network_mode"] == "none"
        assert services[name]["pull_policy"] == "never"
        assert all(v["read_only"] for v in services[name]["volumes"])
        assert "secrets" not in services[name] and "build" not in services[name]
    assert {v["target"] for v in services["whisper-bench"]["volumes"]} == {
        "/models",
        "/corpus",
    }
    assert "network_mode" not in services["whisper-prepare"]
    assert services["whisper-prepare"]["volumes"][0].get("read_only") is not True
    assert default["volumes"]["whisper_models"]["name"] == "botflow_whisper_models"
    custom = config({"WHISPER_CORPUS_DIR": str(ROOT), "WHISPER_MODELS_VOLUME": "other"})
    assert custom["volumes"]["whisper_models"]["name"] == "other"
