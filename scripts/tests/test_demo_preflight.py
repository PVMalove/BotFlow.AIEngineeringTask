"""Demo preflight names every missing prerequisite and never echoes a secret."""

import runpy
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[1]


def load(monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return runpy.run_path(str(SCRIPTS / "demo_preflight.py"))


def config(admin: str = "4200000001", token: str = "/run/secrets/token") -> dict:
    return {
        "name": "botflow",
        "services": {
            "identity-bootstrap": {"environment": {"INITIAL_ADMIN_TELEGRAM_ID": admin}}
        },
        "secrets": {"telegram_bot_token": {"file": token}},
    }


def test_complete_setup_has_no_problems(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load(monkeypatch)
    assert module["problems"](config(), token_size=47, model_volume=True) == []


@pytest.mark.parametrize("admin", ["", "abc", "-5", "0"])
def test_invalid_initial_admin_is_reported(
    monkeypatch: pytest.MonkeyPatch, admin: str
) -> None:
    module = load(monkeypatch)
    found = module["problems"](config(admin), token_size=47, model_volume=True)
    assert len(found) == 1
    assert "INITIAL_ADMIN_TELEGRAM_ID" in found[0]


def test_empty_bot_token_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load(monkeypatch)
    found = module["problems"](config(), token_size=0, model_volume=True)
    assert len(found) == 1
    assert "telegram_bot_token" in found[0]


def test_missing_model_volume_points_to_whisper_prepare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load(monkeypatch)
    found = module["problems"](config(), token_size=47, model_volume=False)
    assert len(found) == 1
    assert "botflow_whisper_models" in found[0]
    assert "make whisper-prepare" in found[0]


def test_every_problem_is_listed_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load(monkeypatch)
    found = module["problems"](config(""), token_size=0, model_volume=False)
    assert len(found) == 3


def test_token_file_content_is_never_part_of_a_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = load(monkeypatch)
    token = tmp_path / "telegram_bot_token"
    token.write_text("123456:SECRET-VALUE")
    assert module["file_size"](str(token)) == len("123456:SECRET-VALUE")
    assert module["file_size"](str(tmp_path / "absent")) == 0
