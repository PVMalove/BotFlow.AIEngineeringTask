"""Check what `make demo` needs before it starts anything; never print a secret value."""

import json
import subprocess
import sys
from pathlib import Path


def file_size(path: str) -> int:
    """Return the stripped size only, so the content never reaches a message."""
    try:
        return len(Path(path).read_bytes().strip())
    except OSError:
        return 0


def problems(config: dict, token_size: int, model_volume: bool) -> list[str]:
    found = []
    environment = config["services"]["identity-bootstrap"].get("environment") or {}
    admin = environment.get("INITIAL_ADMIN_TELEGRAM_ID", "")
    if not admin.isdigit() or int(admin) == 0:
        found.append(
            "INITIAL_ADMIN_TELEGRAM_ID не задан числом: укажите Telegram ID "
            "первого администратора в .env."
        )
    if token_size == 0:
        found.append(
            "Файл telegram_bot_token пуст: запишите токен бота из BotFather "
            "в .secrets/telegram_bot_token (make init создаёт пустой файл)."
        )
    if not model_volume:
        name = f"{config['name']}_whisper_models"
        found.append(
            f"Нет тома {name} с весами Whisper: выполните make whisper-prepare "
            "(единственный шаг с сетью, ≈1.5 GiB для turbo)."
        )
    return found


def main() -> int:
    rendered = subprocess.run(
        [
            "docker",
            "compose",
            *sys.argv[1:],
            # Inactive profiles drop their services and secrets from the rendered config.
            "--profile",
            "workers",
            "--profile",
            "bootstrap",
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
    ).stdout
    config = json.loads(rendered)
    volume = subprocess.run(
        ["docker", "volume", "inspect", f"{config['name']}_whisper_models"],
        capture_output=True,
        check=False,
    )
    found = problems(
        config,
        file_size(config["secrets"]["telegram_bot_token"]["file"]),
        volume.returncode == 0,
    )
    for line in found:
        print(f"make demo: {line}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
