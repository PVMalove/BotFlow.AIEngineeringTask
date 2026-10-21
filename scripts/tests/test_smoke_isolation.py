"""The smoke launcher must not inherit a live workspace's Compose selection."""

import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest


def test_live_compose_profiles_and_project_are_not_inherited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts = Path(__file__).parents[1]
    monkeypatch.syspath_prepend(str(scripts))
    monkeypatch.setattr(sys, "argv", [str(scripts / "integration-smoke.py")])
    monkeypatch.setenv("COMPOSE_PROFILES", "workers,bootstrap")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "operator-stack")
    monkeypatch.delenv("BOTFLOW_BUILD_NETWORK", raising=False)
    environments: list[dict[str, str]] = []

    def docker_boundary(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        env = kwargs.get("env")
        if command[:2] == ["docker", "compose"] and isinstance(env, dict):
            environments.append(env)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", docker_boundary)
    launcher = runpy.run_path(str(scripts / "integration-smoke.py"))

    assert launcher["main"]() == 0

    assert environments
    assert all(env["COMPOSE_PROFILES"] == "" for env in environments)
    assert all(
        env["COMPOSE_PROJECT_NAME"].startswith("botflow-smoke-") for env in environments
    )
    assert os.environ["COMPOSE_PROJECT_NAME"] == "operator-stack"
