# ruff: noqa: E501
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    """Базовый контракт доменного события — без зависимости от
    какого-либо message-фреймворка или брокера. `id` принадлежит событию,
    не агрегату. Подклассы добавляют свои поля через тот же `kw_only=True`.

    `event_type`/`aggregate_type` и методы `aggregate_id()`/`to_payload()`
    — общий контракт, которым управляется generic drain-в-outbox из
    `kernel_platform.outbox.drain` (ADR 0006/0010): любой потребитель, знающий
    только базовый `DomainEvent`, может собрать строку Outbox без импорта
    конкретного подкласса события. `event_type`/`aggregate_type` — обычные
    (не `ClassVar`) поля с дефолтом: `support-service`'s `TicketCreated` и
    аналогичные события уже переопределяли `event_type: str = "..."` как
    поле экземпляра до этого контракта — `ClassVar` здесь конфликтовал бы
    с этим существующим переопределением (mypy: "Cannot override class
    variable with instance variable")."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    occurred_on_utc: datetime = field(default_factory=lambda: datetime.now(UTC))

    event_type: str = ""
    aggregate_type: str = ""

    def aggregate_id(self) -> uuid.UUID:
        """Возвращает id агрегата, породившего событие — переопределяется
        подклассом, несущим специфичное для домена поле (например,
        `product_id`), не переименовывая и не убирая это поле."""
        raise NotImplementedError

    def to_payload(self) -> dict[str, Any]:
        """Сериализует специфичные для события поля в JSON-совместимый
        payload строки Outbox."""
        raise NotImplementedError
