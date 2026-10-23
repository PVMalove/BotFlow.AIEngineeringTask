"""Browser fixtures for the disposable Compose project; no public test endpoints."""

import argparse
import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from api.containers import build_bot_command
from application.commands.handle_bot_command import HandleBotCommandDto
from auth_flow import TelegramPort
from domain.value_objects.job_status import JobKind
from infrastructure.identity_gateway import HttpIdentityGateway
from infrastructure.messaging.envelopes import create_task_envelope, outbox_id_for
from kernel_platform.messaging import ConfirmedPublisher, declare_task_topology
from kernel_platform.resources import open_database, open_resources
from kernel_platform.settings import Settings
from sqlalchemy import text

APPLICANT = 4_200_000_008
REALTIME_BOT_ID = 7_700_035


async def board_fixture(action: str, author_id: UUID) -> None:
    """Seed only the disposable board; reads still use the real Tasks API."""
    async with open_database(Settings(service="tasks")) as database:
        async with database.sessions() as session:
            if action == "reset":
                jobs = await session.scalars(
                    text(
                        "SELECT correlation_id FROM tasks WHERE source_key LIKE :source"
                    ),
                    {"source": f"{REALTIME_BOT_ID}:%"},
                )
                for job_id in jobs:
                    await session.execute(
                        text("DELETE FROM inbox_messages WHERE message_id = :id"),
                        {"id": str(outbox_id_for(job_id))},
                    )
                    await session.execute(
                        text("DELETE FROM outbox WHERE correlation_id = :id"),
                        {"id": job_id},
                    )
                await session.execute(
                    text(
                        "DELETE FROM tasks WHERE source_key LIKE 'browser-board:%' "
                        "OR source_key LIKE :source"
                    ),
                    {"source": f"{REALTIME_BOT_ID}:%"},
                )
            elif action in {"board", "dnd-board"}:
                statuses = (
                    ("pending", "in_progress", "completed")
                    if action == "board"
                    else ("pending", "in_progress")
                )
                for status in statuses:
                    for index in range(52 if action == "board" else 1):
                        # Tied timestamps exercise the ID half of the keyset cursor.
                        await session.execute(
                            text(
                                "INSERT INTO tasks (id, source_key, "
                                "correlation_id, text, "
                                "status, version, author_id, author_telegram_id, "
                                "author_name, created_at, updated_at) VALUES "
                                "(:id, :source, :correlation, :text, :status, 1, "
                                ":author, :telegram, :name, :created, :created)"
                            ),
                            {
                                "id": uuid4(),
                                "source": f"browser-board:{status}:{index}",
                                "correlation": uuid4(),
                                "text": f"{status} #{index} / Տեքստ "
                                + ("длинный" * 20 if action == "board" else ""),
                                "status": status,
                                "author": author_id,
                                "telegram": int(
                                    os.environ["INITIAL_ADMIN_TELEGRAM_ID"]
                                ),
                                "name": "Анна / Աննա",
                                "created": datetime(2026, 1, 1, tzinfo=UTC)
                                + timedelta(minutes=index // 4),
                            },
                        )
            elif action == "board-change":
                await session.execute(
                    text(
                        "UPDATE tasks SET status = 'in_progress', version = 2 "
                        "WHERE source_key = 'browser-board:pending:0'"
                    )
                )
                await session.execute(
                    text(
                        "INSERT INTO tasks (id, source_key, correlation_id, text, "
                        "status, version, author_id, author_telegram_id, author_name, "
                        "created_at, updated_at) VALUES (:id, 'browser-board:new', "
                        ":correlation, 'Новая задача / Նոր առաջադրանք', 'pending', 1, "
                        ":author, :telegram, 'Анна / Աննա', now(), now())"
                    ),
                    {
                        "id": uuid4(),
                        "correlation": uuid4(),
                        "author": author_id,
                        "telegram": int(os.environ["INITIAL_ADMIN_TELEGRAM_ID"]),
                    },
                )
            await session.commit()


async def realtime_task(author_id: UUID, kind: JobKind) -> dict[str, str]:
    """Publish the production command through real RabbitMQ to tasks-worker."""
    job_id = uuid4()
    task_text = f"Realtime #35 / Թարմացում {job_id}"
    if kind is JobKind.VOICE:
        task_text = f"Голос #16 / Ձայն {job_id}"
    telegram_id = int(os.environ["INITIAL_ADMIN_TELEGRAM_ID"])
    message_id, routing, envelope = create_task_envelope(
        job_id=job_id,
        bot_id=REALTIME_BOT_ID,
        chat_id=telegram_id,
        message_id=uuid4().int % (2**63),
        author_id=author_id,
        author_telegram_id=telegram_id,
        author_name="Анна / Աննա",
        text=task_text,
        source_type=kind,
        received_at=datetime.now(UTC),
    )
    async with open_resources(Settings(service="automation")) as resources:
        await ConfirmedPublisher(resources.rabbitmq, declare_task_topology).publish(
            routing,
            json.dumps(envelope).encode(),
            message_id=str(message_id),
            type="CreateTask",
        )
    async with open_database(Settings(service="tasks")) as database:
        async with asyncio.timeout(15):
            while True:
                async with database.sessions() as session:
                    task_id = await session.scalar(
                        text("SELECT id FROM tasks WHERE correlation_id = :id"),
                        {"id": job_id},
                    )
                if task_id is not None:
                    return {"task_id": str(task_id), "text": task_text}
                await asyncio.sleep(0.1)


async def run(action: str) -> dict[str, str]:
    run_id = UUID(os.environ["BOTFLOW_SMOKE_ID"]).hex
    if (
        os.environ.get("BOTFLOW_ENVIRONMENT") != "test"
        or os.environ.get("COMPOSE_PROJECT_NAME") != f"botflow-smoke-{run_id}"
    ):
        raise RuntimeError("Browser fixtures require an isolated test project")

    async with open_database(Settings(service="identity")) as identity:
        if action in {
            "reset",
            "board",
            "board-change",
            "dnd-board",
            "realtime-task",
            "voice-task",
        }:
            async with identity.sessions() as session:
                author_id = await session.scalar(
                    text("SELECT id FROM members WHERE telegram_id = :telegram"),
                    {"telegram": int(os.environ["INITIAL_ADMIN_TELEGRAM_ID"])},
                )
            if action in {"realtime-task", "voice-task"}:
                return await realtime_task(
                    author_id,
                    JobKind.VOICE if action == "voice-task" else JobKind.TEXT,
                )
            await board_fixture(action, author_id)
            if action != "reset":
                return {"status": "ok"}
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
        "action",
        choices=[
            "reset",
            "code",
            "user-code",
            "join",
            "expire",
            "revoke",
            "board",
            "board-change",
            "dnd-board",
            "realtime-task",
            "voice-task",
        ],
    )
    arguments = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        print(json.dumps(asyncio.run(run(arguments.action))))
    except Exception:
        # Never expose a code, token, connection URL or service-key diagnostic.
        raise SystemExit("Browser fixture failed") from None
