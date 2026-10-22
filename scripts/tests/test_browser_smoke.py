"""Browser smoke ordering and diagnostic secrecy at the subprocess boundary."""

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest


def launcher(monkeypatch: pytest.MonkeyPatch) -> dict:
    scripts = Path(__file__).parents[1]
    monkeypatch.syspath_prepend(str(scripts))
    return runpy.run_path(str(scripts / "integration-smoke.py"))


def test_browser_diagnostics_keep_test_location_but_redact_a_login_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = "a" * 32
    report = {
        "stats": {"expected": 1, "unexpected": 1, "skipped": 0},
        "suites": [
            {
                "specs": [
                    {
                        "file": "e2e/auth.spec.ts",
                        "line": 42,
                        "tests": [
                            {
                                "results": [
                                    {
                                        "status": "failed",
                                        "errors": [
                                            {
                                                "message": f"Error: fill {code}\nraw diagnostic"
                                            }
                                        ],
                                    }
                                ]
                            }
                        ],
                    }
                ]
            }
        ],
    }

    launcher(monkeypatch)["report_browser_result"](json.dumps(report).encode())
    output = capsys.readouterr().out

    assert "Browser checks: 1 passed, 1 failed, 0 skipped" in output
    assert "FAIL: auth.spec.ts:42" in output
    assert code not in output
    assert "raw diagnostic" not in output


def test_browser_auth_runs_before_resource_checks_change_admin_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = launcher(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["integration-smoke.py", "--browser"])
    monkeypatch.delenv("BOTFLOW_BUILD_NETWORK", raising=False)
    stages: list[str] = []

    def subprocess_boundary(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        output = b""
        if command[-3:] == ["port", "frontend", "8080"]:
            output = b"127.0.0.1:43210"
        elif "--reporter=json" in command:
            stages.append("browser")
            output = json.dumps(
                {"stats": {"expected": 10, "unexpected": 0, "skipped": 0}, "suites": []}
            ).encode()
            env = kwargs["env"]
            assert isinstance(env, dict)
            assert env["BOTFLOW_E2E_BASE_URL"] == "http://127.0.0.1:43210"
            assert env["BOTFLOW_E2E_COMPOSE"]
            assert env["BOTFLOW_E2E_OUTPUT_DIR"].endswith("/browser-results")
        elif command[-1] == "reset":
            stages.append("reset")
        elif command[-1] == "smoke":
            stages.append("resources")
        if kwargs.get("text"):
            return subprocess.CompletedProcess(
                command, 0, stdout=output.decode(), stderr=""
            )  # type: ignore[return-value]
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr=b"")

    monkeypatch.setattr(subprocess, "run", subprocess_boundary)

    assert module["main"]() == 0
    assert stages == ["browser", "reset", "resources", "resources"]
