# ruff: noqa: E501
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from kernel_domain.domain_event import DomainEvent


def test_domain_event_gets_a_unique_id_and_utc_occurrence_timestamp() -> None:
    event = DomainEvent()

    assert isinstance(event.id, uuid.UUID)
    assert isinstance(event.occurred_on_utc, datetime)
    assert event.occurred_on_utc.tzinfo is UTC


def test_two_domain_events_get_different_ids() -> None:
    first = DomainEvent()
    second = DomainEvent()

    assert first.id != second.id


def test_a_subclass_can_add_its_own_fields_while_keeping_id_and_occurred_on() -> None:
    @dataclass(frozen=True, kw_only=True)
    class UserRegistered(DomainEvent):
        user_id: uuid.UUID

    event = UserRegistered(user_id=uuid.uuid4())

    assert isinstance(event.id, uuid.UUID)
    assert isinstance(event.occurred_on_utc, datetime)


def test_base_domain_event_leaves_aggregate_id_and_payload_unimplemented() -> None:
    event = DomainEvent()

    with pytest.raises(NotImplementedError):
        event.aggregate_id()
    with pytest.raises(NotImplementedError):
        event.to_payload()


def test_a_subclass_implementing_the_contract_is_driven_entirely_by_it() -> None:
    """Generic drain-в-outbox (`kernel_platform.outbox.drain`) читает ровно
    эти четыре атрибута/метода — тест доказывает, что их реализации в
    подклассе достаточно, без знания о его специфичных полях."""

    @dataclass(frozen=True, kw_only=True)
    class OrderPlaced(DomainEvent):
        event_type: str = "order.placed.v1"
        aggregate_type: str = "Order"

        order_id: uuid.UUID
        total: float

        def aggregate_id(self) -> uuid.UUID:
            return self.order_id

        def to_payload(self) -> dict[str, Any]:
            return {"order_id": str(self.order_id), "total": self.total}

    event: DomainEvent = OrderPlaced(order_id=uuid.uuid4(), total=9.99)

    assert event.event_type == "order.placed.v1"
    assert event.aggregate_type == "Order"
    assert event.aggregate_id() == event.order_id  # type: ignore[attr-defined]
    assert event.to_payload() == {
        "order_id": str(event.order_id),  # type: ignore[attr-defined]
        "total": 9.99,
    }


def test_a_subclass_can_override_only_event_type_leaving_aggregate_type_default() -> (
    None
):
    """Регрессия: ранее `event_type`/`aggregate_type` были объявлены как
    `ClassVar` на базовом классе — это ломало mypy для существующих
    потребителей `DomainEvent` (`support-service`), которые уже
    переопределяли `event_type: str = "..."` как обычное поле экземпляра,
    не трогая `aggregate_type` вовсе. Оба поля — обычные dataclass-поля с
    дефолтом, переопределение одного без другого не конфликтует."""

    @dataclass(frozen=True, kw_only=True)
    class TicketCreated(DomainEvent):
        ticket_id: uuid.UUID
        event_type: str = "ticket.created.v1"

    event = TicketCreated(ticket_id=uuid.uuid4())

    assert event.event_type == "ticket.created.v1"
    assert event.aggregate_type == ""
