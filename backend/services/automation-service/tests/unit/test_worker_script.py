"""The worker entry points load and expose the STT command."""

import importlib
import inspect
import runpy
from pathlib import Path

from api import worker

SCRIPTS = Path(__file__).parents[2] / "scripts"


def test_the_worker_loads_and_has_the_transcription_command() -> None:
    assert inspect.iscoroutinefunction(worker.transcription)
    assert inspect.iscoroutinefunction(worker.replay)
    assert inspect.iscoroutinefunction(worker.status)


def test_the_bot_and_bootstrap_entry_points_load() -> None:
    for name in ("api.bot", "api.bootstrap"):
        importlib.import_module(name)


def test_the_latency_script_loads() -> None:
    runpy.run_path(str(SCRIPTS / "voice_latency.py"), run_name="script_under_test")
