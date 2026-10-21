"""Prepare local credentials once, without overwriting existing values."""

import hashlib
import os
import secrets
from pathlib import Path


def main() -> None:
    directory = Path(
        os.environ.get(
            "BOTFLOW_SECRETS_DIR", Path(__file__).resolve().parents[1] / ".secrets"
        )
    ).resolve()
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    names = (
        "identity_db_password",
        "tasks_db_password",
        "automation_db_password",
        "rabbitmq_password",
        "minio_password",
        "tasks_service_key",
        "automation_service_key",
        "redis_automation_password",
        "redis_tasks_password",
        "redis_health_password",
    )
    for name in names:
        path = directory / name
        if not path.exists():
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(descriptor, "w") as output:
                output.write(secrets.token_urlsafe(32))
    acl = directory / "redis_acl"
    if not acl.exists():
        lines = ["user default off"]
        for name, permissions in (
            ("automation", "~botflow:automation:* ~botflow:fsm:* &botflow.* +@all"),
            ("tasks", "&botflow.board.* +ping +publish +subscribe +unsubscribe"),
            ("health", "+ping"),
        ):
            password = (directory / f"redis_{name}_password").read_bytes()
            digest = hashlib.sha256(password).hexdigest()
            lines.append(f"user {name} on #{digest} {permissions}")
        descriptor = os.open(acl, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "w") as output:
            output.write("\n".join(lines) + "\n")
    for name in (*names, "redis_acl"):
        (directory / name).chmod(0o644)
    token = directory / "telegram_bot_token"
    if not token.exists():
        descriptor = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.close(descriptor)
        token.chmod(0o644)
    print("Local secret files prepared. Set telegram_bot_token before bot runtime.")


if __name__ == "__main__":
    main()
