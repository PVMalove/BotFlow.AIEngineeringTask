"""The worker entry points load and expose the STT command."""

import inspect
import runpy
from pathlib import Path

SCRIPTS = Path(__file__).parents[2] / "scripts"


def test_the_worker_script_loads_and_has_the_transcription_command() -> None:
    namespace = runpy.run_path(str(SCRIPTS / "worker.py"), run_name="worker_script")

    assert inspect.iscoroutinefunction(namespace["transcription"])
    assert inspect.iscoroutinefunction(namespace["replay"])


def test_the_bot_and_bootstrap_scripts_load() -> None:
    for name in ("bot.py", "bootstrap.py"):
        runpy.run_path(str(SCRIPTS / name), run_name="script_under_test")
