"""Own a disposable Compose project; never use the developer's data or credentials."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from compose_build import build_images

ADMIN_TELEGRAM_ID = "4200000001"
BOOTSTRAP = (
    ["run", "--rm", "--no-deps", "identity-bootstrap"],
    ["run", "--rm", "--no-deps", "automation-bootstrap"],
    ["run", "--rm", "--no-deps", "tasks-bootstrap"],
    [
        "run",
        "--rm",
        "--no-deps",
        "identity-bootstrap",
        "python",
        "-m",
        "api.bootstrap",
        "admin",
    ],
)


def report_browser_result(output: bytes) -> None:
    """Print only test identities, counts and a redacted first diagnostic line."""
    report = json.loads(output)
    stats = report["stats"]
    print(
        f"Browser checks: {stats['expected']} passed, "
        f"{stats['unexpected']} failed, {stats['skipped']} skipped",
        flush=True,
    )

    def visit(suites: list[dict]) -> None:
        for suite in suites:
            for spec in suite.get("specs", []):
                for test in spec.get("tests", []):
                    for result in test.get("results", []):
                        if result["status"] in {"passed", "skipped"}:
                            continue
                        name = Path(spec["file"]).name
                        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
                            name = "browser-test"
                        print(f"FAIL: {name}:{spec['line']}", flush=True)
                        for error in result.get("errors", []):
                            line = error.get("message", "").splitlines()[0]
                            line = re.sub(r"\x1b\[[0-9;]*m", "", line)
                            line = re.sub(r"[A-Za-z0-9_-]{32,}", "[redacted]", line)
                            print(line[:200], flush=True)
            visit(suite.get("suites", []))

    visit(report.get("suites", []))
    # An empty or partly skipped run must not look like a passing gate.
    if stats["expected"] == 0 or stats["skipped"]:
        print("FAIL: browser run is empty or has skipped tests", flush=True)
        raise subprocess.CalledProcessError(1, "playwright")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true")
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run_id = uuid.uuid4().hex
    project = f"botflow-smoke-{run_id}"
    images = [
        f"botflow-{name}:{project}"
        for name in ("identity", "tasks", "automation", "frontend", "minio")
    ]
    with tempfile.TemporaryDirectory(prefix=project + "-") as temporary:
        directory = Path(temporary)
        secret_directory = directory / "secrets"
        empty_env = directory / "empty.env"
        empty_env.touch()
        env = {
            **os.environ,
            "COMPOSE_PROJECT_NAME": project,
            "COMPOSE_PROFILES": "",
            "BOTFLOW_ENVIRONMENT": "test",
            "BOTFLOW_HTTP_PORT": "8080",
            "BOTFLOW_IMAGE_TAG": project,
            "BOTFLOW_SMOKE_ID": run_id,
            "INITIAL_ADMIN_TELEGRAM_ID": ADMIN_TELEGRAM_ID,
            "BOTFLOW_SECRETS_DIR": str(secret_directory),
        }
        compose = [
            "docker",
            "compose",
            "--project-directory",
            str(root),
            "--env-file",
            str(empty_env),
            "-p",
            project,
            "-f",
            str(root / "docker-compose.yml"),
            "-f",
            str(root / "docker-compose.smoke.yml"),
        ]
        if options.browser:
            overlay = directory / "browser.json"
            overlay.write_text(
                json.dumps(
                    {
                        "services": {
                            "frontend": {
                                "ports": [
                                    {
                                        "target": 8080,
                                        "published": "0",
                                        "host_ip": "127.0.0.1",
                                    }
                                ]
                            }
                        }
                    }
                )
            )
            compose += ["-f", str(overlay)]
        stage = "secret initialization"
        result = 1
        cleanup_failed = False
        try:
            subprocess.run(
                [sys.executable, str(root / "scripts/init-local-secrets.py")],
                env=env,
                check=True,
                capture_output=True,
            )
            for stage, commands, phase in (
                ("Compose validation", (["config", "--quiet"],), "before"),
                ("image build", (["build"],), "before"),
                (
                    "initial startup",
                    (["up", "-d", "--wait", "--wait-timeout", "180"],),
                    "before",
                ),
                ("migrations and initial admin", BOOTSTRAP, "before"),
                # Relays the Identity outbox to RabbitMQ for the access checks. It is
                # absent after the restart: those checks only read persisted state.
                (
                    "identity worker",
                    (["up", "-d", "--no-deps", "--wait", "identity-worker"],),
                    "before",
                ),
                # Consumes CreateTask and relays TaskCreated for the text-path checks.
                # Like identity-worker it is absent after the restart.
                (
                    "tasks worker",
                    (["up", "-d", "--no-deps", "--wait", "tasks-worker"],),
                    "before",
                ),
                (
                    "resource and isolation checks",
                    (["run", "--rm", "--no-deps", "smoke"],),
                    "before",
                ),
                (
                    "volume-preserving shutdown",
                    (["--profile", "workers", "down", "--timeout", "20"],),
                    "before",
                ),
                (
                    "restart",
                    (["up", "-d", "--wait", "--wait-timeout", "180"],),
                    "after",
                ),
                ("repeated migrations and initial admin", BOOTSTRAP, "after"),
                (
                    "persistence checks",
                    (["run", "--rm", "--no-deps", "smoke"],),
                    "after",
                ),
            ):
                print(f"Integration smoke: {stage}", flush=True)
                if stage == "image build":
                    build_images(compose, env, root)
                    continue
                for command_args in commands:
                    subprocess.run(
                        [*compose, *command_args],
                        env={**env, "BOTFLOW_SMOKE_PHASE": phase},
                        check=True,
                        capture_output=True,
                    )
                # Resource checks deliberately block the admin to verify that a
                # repeated bootstrap after restart does not restore access.
                # Browser realtime also needs the actual CreateTask consumer.
                # Both workers now run after migrations, before browser checks.
                if stage == "tasks worker" and options.browser:
                    stage = "isolated browser checks"
                    address = subprocess.run(
                        [*compose, "port", "frontend", "8080"],
                        env=env,
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout.strip()
                    browser_result = subprocess.run(
                        [
                            os.environ.get("BOTFLOW_NODE", "node"),
                            str(root / "frontend/node_modules/@playwright/test/cli.js"),
                            "test",
                            "--reporter=json",
                        ],
                        cwd=root / "frontend",
                        env={
                            **env,
                            "BOTFLOW_E2E_BASE_URL": f"http://{address}",
                            "BOTFLOW_E2E_COMPOSE": json.dumps(compose),
                            "BOTFLOW_E2E_OUTPUT_DIR": str(
                                directory / "browser-results"
                            ),
                        },
                        check=False,
                        capture_output=True,
                    )
                    report_browser_result(browser_result.stdout)
                    browser_result.check_returncode()
                    subprocess.run(
                        [
                            *compose,
                            "run",
                            "--rm",
                            "--no-deps",
                            "-T",
                            "smoke",
                            "python",
                            "/workspace/backend/tests/integration/browser_fixture.py",
                            "reset",
                        ],
                        env=env,
                        check=True,
                        capture_output=True,
                    )
            result = 0
        except subprocess.CalledProcessError as error:
            # Container and SDK diagnostics can contain credentials or object data.
            print(
                f"Integration smoke failed: {stage} (exit {error.returncode}).",
                file=sys.stderr,
            )
            if stage == "image build":
                # Builds receive no secret mounts or credential arguments.
                print(error.stderr.decode(errors="replace")[-4000:], file=sys.stderr)
            elif stage in {"resource and isolation checks", "persistence checks"}:
                for line in error.stdout.decode(errors="replace").splitlines():
                    if line.startswith(
                        (
                            "FAIL: __main__.InfrastructureSmoke.",
                            "FAIL: access_flow.AccessFlow.",
                            "FAIL: auth_flow.AuthFlow.",
                            "FAIL: text_task_flow.TextTaskFlow.",
                            "FAIL: voice_task_flow.VoiceTaskFlow.",
                            "Resource checks:",
                        )
                    ):
                        print(line, file=sys.stderr)
        finally:
            # Only this UUID-owned project is disposable. Normal `make down` keeps volumes.
            cleanup = subprocess.run(
                [*compose, "down", "--volumes", "--remove-orphans", "--timeout", "20"],
                env=env,
                capture_output=True,
                check=False,
            )
            cleanup_failed = cleanup.returncode != 0
            subprocess.run(
                ["docker", "image", "rm", *images], capture_output=True, check=False
            )
        if cleanup_failed:
            print(
                f"Cleanup failed for owned project {project}; inspect it before removing its volumes.",
                file=sys.stderr,
            )
            return 1
        if result == 0:
            print(
                "Integration smoke PASS: join/access decisions and notifications, "
                "bot login/Identity exchange, text and voice message to task, REST and "
                "notification, real PG/RabbitMQ/Redis/MinIO and restart persistence."
            )
        return result


if __name__ == "__main__":
    raise SystemExit(main())
