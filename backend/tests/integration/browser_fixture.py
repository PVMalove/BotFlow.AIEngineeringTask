"""Browser fixtures for the disposable Compose project; no public test endpoints."""

import argparse
import asyncio
import json
import logging
import os
import re
from uuid import UUID, uuid4

import httpx
from api.containers import build_bot_command
from application.commands.handle_bot_command import HandleBotCommandDto
from auth_flow import TelegramPort
from botflow_platform.resources import open_database
from botflow_platform.settings import Settings
from infrastructure.identity_gateway import HttpIdentityGateway
from sqlalchemy import text

APPLICANT = 4_200_000_008


async def run(action: str) -> dict[str, str]:
    run_id = UUID(os.environ["BOTFLOW_SMOKE_ID"]).hex
    if (
        os.environ.get("BOTFLOW_ENVIRONMENT") != "test"
        or os.environ.get("COMPOSE_PROJECT_NAME") != f"botflow-smoke-{run_id}"
    ):
        raise RuntimeError("Browser fixtures require an isolated test project")

    async with open_database(Settings(service="identity")) as identity:
        if action in {"code", "user-code", "join"}:
            actor = (
                int(os.environ["INITIAL_ADMIN_TELEGRAM_ID"])
                if action == "code"
                else APPLICANT
            )
            key = Settings(service="automation").secret("automation_service_key")
            async with (
                open_database(Settings(service="automation")) as automation,
                httpx.AsyncClient(
                    base_url="http://identity-api:8000", timeout=10
                ) as http,
            ):
                telegram = TelegramPort()
                bot = build_bot_command(
                    automation,
                    HttpIdentityGateway(http, key.get_secret_value()),
                    telegram,
                )
                await bot.execute(
                    HandleBotCommandDto(
                        update_id=uuid4().int % (2**63),
                        telegram_id=actor,
                        chat_id=actor,
                        text="/join" if action == "join" else "/login",
                        private=True,
                        display_name="Анна / Աննա",
                    )
                )
                chat_id, message = telegram.messages[-1]
                if action == "join":
                    if "Заявка отправлена." not in message:
                        raise RuntimeError("The bot did not submit a join request")
                    return {"status": "ok"}
                match = re.search(r"Код входа: ([A-Za-z0-9_-]{32})\n", message)
                if chat_id != actor or match is None:
                    raise RuntimeError("The bot did not deliver a login code")
                return {"code": match.group(1)}
        async with identity.sessions() as session:
            if action == "reset":
                await session.execute(text("DELETE FROM rate_limits"))
                await session.execute(text("DELETE FROM auth_sessions"))
                await session.execute(text("DELETE FROM login_codes"))
                await session.execute(
                    text("DELETE FROM members WHERE telegram_id = :telegram_id"),
                    {"telegram_id": APPLICANT},
                )
            elif action == "expire":
                await session.execute(
                    text(
                        "UPDATE auth_sessions "
                        "SET expires_at = now() - interval '1 second'"
                    )
                )
            elif action == "revoke":
                await session.execute(
                    text("UPDATE auth_sessions SET revoked_at = now()")
                )
            await session.commit()
        if action == "reset":
            async with open_database(Settings(service="automation")) as automation:
                async with automation.sessions() as session:
                    await session.execute(text("DELETE FROM telegram_updates"))
                    await session.commit()
    return {"status": "ok"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["reset", "code", "user-code", "join", "expire", "revoke"]
    )
    arguments = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        print(json.dumps(asyncio.run(run(arguments.action))))
    except Exception:
        # Never expose a code, token, connection URL or service-key diagnostic.
        raise SystemExit("Browser fixture failed") from None
