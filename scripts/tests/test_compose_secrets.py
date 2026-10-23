"""Key material reaches only the containers that need it."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is required")
def test_private_key_is_mounted_only_in_identity_api() -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(ROOT),
            "config",
            "--format",
            "json",
        ],
        env={**os.environ, "COMPOSE_PROFILES": "bootstrap,workers"},
        capture_output=True,
        check=True,
    )
    services = json.loads(result.stdout)["services"]

    def mounted(secret: str) -> set[str]:
        return {
            name
            for name, service in services.items()
            if any(item["source"] == secret for item in service.get("secrets", []))
        }

    assert mounted("identity_jwt_private_key") == {"identity-api"}
    assert mounted("automation_service_key") == {
        "identity-api",
        "telegram-bot",
        "automation-worker",
    }
