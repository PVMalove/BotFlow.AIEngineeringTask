"""Own a disposable Compose project; never use the developer's data or credentials."""

import argparse
import json
import os
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
    [
        "run",
        "--rm",
        "--no-deps",
        "identity-bootstrap",
        "python",
        "scripts/bootstrap.py",
        "admin",
    ],
)


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
                (
                    "resource and isolation checks",
                    (["run", "--rm", "--no-deps", "smoke"],),
                    "before",
                ),
                (
                    "volume-preserving shutdown",
                    (["down", "--timeout", "20"],),
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
                if stage == "resource and isolation checks" and options.browser:
                    stage = "isolated browser checks"
                    address = subprocess.run(
                        [*compose, "port", "frontend", "8080"],
                        env=env,
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout.strip()
                    subprocess.run(
                        [
                            os.environ.get("BOTFLOW_NODE", "node"),
                            str(root / "frontend/node_modules/@playwright/test/cli.js"),
                            "test",
                        ],
                        cwd=root / "frontend",
                        env={**env, "BOTFLOW_E2E_BASE_URL": f"http://{address}"},
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
                            "FAIL: auth_flow.AuthFlow.",
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
                "Integration smoke PASS: bot login/Identity exchange, "
                "real PG/RabbitMQ/Redis/MinIO and restart persistence."
            )
        return result


if __name__ == "__main__":
    raise SystemExit(main())
