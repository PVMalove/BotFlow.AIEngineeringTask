"""The smoke run starts identity-worker, and stops it with the rest of the project."""

import runpy
import subprocess
import sys
from pathlib import Path

import pytest


def recorded_compose_commands(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    scripts = Path(__file__).parents[1]
    monkeypatch.syspath_prepend(str(scripts))
    monkeypatch.setattr(sys, "argv", [str(scripts / "integration-smoke.py")])
    commands: list[list[str]] = []

    def docker_boundary(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        if command[:2] == ["docker", "compose"]:
            commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", docker_boundary)
    assert runpy.run_path(str(scripts / "integration-smoke.py"))["main"]() == 0
    return commands


def subcommands(commands: list[list[str]]) -> list[str]:
    """The Compose arguments after the project/file options."""
    return [
        " ".join(command[len(command) - command[::-1].index("-f") + 1 :])
        for command in commands
    ]


def test_identity_worker_runs_for_the_resource_checks_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steps = subcommands(recorded_compose_commands(monkeypatch))

    worker = steps.index("up -d --no-deps --wait identity-worker")
    checks = [
        i for i, step in enumerate(steps) if step.endswith("run --rm --no-deps smoke")
    ]
    migrations = steps.index("run --rm --no-deps identity-bootstrap")
    assert migrations < worker < checks[0]
    assert steps.count("up -d --no-deps --wait identity-worker") == 1


def test_the_volume_preserving_shutdown_also_stops_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steps = subcommands(recorded_compose_commands(monkeypatch))

    assert "--profile workers down --timeout 20" in steps


def test_tasks_worker_runs_after_its_migrations_and_before_the_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steps = subcommands(recorded_compose_commands(monkeypatch))

    migrations = steps.index("run --rm --no-deps tasks-bootstrap")
    worker = steps.index("up -d --no-deps --wait tasks-worker")
    checks = [
        i for i, step in enumerate(steps) if step.endswith("run --rm --no-deps smoke")
    ]
    assert migrations < worker < checks[0]
    assert steps.count("up -d --no-deps --wait tasks-worker") == 1
    # the repeated migrations after the restart keep the Tasks schema idempotent
    assert steps.count("run --rm --no-deps tasks-bootstrap") == 2
