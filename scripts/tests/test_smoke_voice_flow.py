"""The voice smoke class is registered and covers every promised scenario."""

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
INTEGRATION = ROOT / "backend/tests/integration"


def test_the_smoke_runs_the_voice_flow_in_the_before_phase() -> None:
    tree = ast.parse((INTEGRATION / "smoke.py").read_text())
    loaded = {
        arg.id
        for guard in ast.walk(tree)
        if isinstance(guard, ast.If)
        and isinstance(guard.test, ast.UnaryOp)
        and isinstance(guard.test.operand, ast.Name)
        and guard.test.operand.id == "AFTER"
        for call in ast.walk(guard)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "loadTestsFromTestCase"
        for arg in call.args
        if isinstance(arg, ast.Name)
    }

    assert "VoiceTaskFlow" in loaded


def test_failures_of_the_voice_flow_are_reported_by_name() -> None:
    source = (ROOT / "scripts/integration-smoke.py").read_text()

    assert '"FAIL: voice_task_flow.VoiceTaskFlow."' in source


def test_the_voice_flow_covers_the_promised_scenarios() -> None:
    tree = ast.parse((INTEGRATION / "voice_task_flow.py").read_text())
    (flow,) = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "VoiceTaskFlow"
    ]
    tests = {
        node.name
        for node in flow.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("test_")
    }

    for scenario in (
        "ru_voice_becomes_a_task",
        "hy_voice_becomes_one_task",
        "repeated_update_and_a_redelivered_entry",
        "without_speech",
        "corrupted_recording",
        "too_long",
        "telegram_file_that_is_gone",
        "timeout",
        "voice_limits_ten_and_two",
        "common_limits_still_apply",
        "taken_over_after_a_crash",
        "blocked_recognition_holds_no_transaction",
        "audio_bucket",
    ):
        assert any(scenario in name for name in tests), scenario
