"""The RabbitMQ adapter maps an outbox entry onto a confirmed publish."""

import json
from typing import Any
from uuid import uuid4

from conftest import NOW

from domain.access import ACCESS_DECIDED, OutboxEntry
from infrastructure.messaging.publisher import RabbitEventPublisher


class Confirmed:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def publish(
        self, routing_key: str, body: bytes, *, message_id: str, type: str
    ) -> None:
        self.calls.append((routing_key, body, message_id, type))


async def test_entry_id_is_the_message_id_and_the_payload_is_json() -> None:
    confirmed = Confirmed()
    entry = OutboxEntry(uuid4(), ACCESS_DECIDED, {"decision": "approved"}, NOW)

    await RabbitEventPublisher(confirmed).publish(entry)  # type: ignore[arg-type]

    ((key, body, message_id, kind),) = confirmed.calls
    assert (key, kind) == (ACCESS_DECIDED, ACCESS_DECIDED)
    assert message_id == str(entry.id)
    assert json.loads(body) == {"decision": "approved"}
