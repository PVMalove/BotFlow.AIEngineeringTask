"""The STT worker container: Whisper image, read-only weights, marker healthcheck."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker is required"
)


def compose_services(profiles: str) -> dict[str, Any]:
    env = {k: v for k, v in os.environ.items() if k != "COMPOSE_FILE"}
    env["COMPOSE_PROFILES"] = profiles
    result = subprocess.run(
        ["docker", "compose", "--project-directory", str(ROOT), "config", "--format"]
        + ["json"],
        env=env,
        capture_output=True,
        check=True,
    )
    services: dict[str, Any] = json.loads(result.stdout)["services"]
    return services


@pytest.fixture(scope="module")
def worker() -> dict[str, Any]:
    service: dict[str, Any] = compose_services("workers")["whisper-worker"]
    return service


def test_it_runs_the_transcription_command_in_the_stt_runtime_image(
    worker: dict[str, Any],
) -> None:
    assert worker["command"] == ["python", "scripts/worker.py", "transcription"]
    assert worker["build"]["target"] == "stt-runtime"
    assert worker["image"].startswith("botflow-whisper-worker:")
    assert worker["restart"] == "unless-stopped"


def test_the_weights_volume_is_read_only(worker: dict[str, Any]) -> None:
    (models,) = [v for v in worker["volumes"] if v["target"] == "/models"]

    assert models["source"] == "whisper_models" and models["read_only"] is True


def test_it_gets_only_the_secrets_of_its_own_work(worker: dict[str, Any]) -> None:
    secrets = {item["source"] for item in worker["secrets"]}

    assert secrets == {
        "automation_db_password",
        "redis_automation_password",
        "minio_password",
        "telegram_bot_token",
    }


def test_it_needs_no_broker_and_waits_for_its_stores(worker: dict[str, Any]) -> None:
    assert set(worker["depends_on"]) == {"automation-db", "redis", "minio"}


def test_the_model_and_the_timeout_are_configured(worker: dict[str, Any]) -> None:
    assert worker["environment"]["BOTFLOW_WHISPER_MODEL"] == "turbo"
    assert worker["environment"]["BOTFLOW_STT_TIMEOUT_SECONDS"] == "120"


def test_readiness_is_the_marker_file_written_after_the_model_loads(
    worker: dict[str, Any],
) -> None:
    check = worker["healthcheck"]

    assert check["test"] == ["CMD", "test", "-f", "/tmp/whisper-ready"]
    assert check["start_period"] == "2m0s"


def test_the_default_profile_neither_builds_nor_starts_it() -> None:
    assert "whisper-worker" not in compose_services("")
