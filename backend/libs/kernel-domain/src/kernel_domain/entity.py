# ruff: noqa: E501
from typing import cast

from kernel_domain.domain_event import DomainEvent

PRIVATE_MARKER = object()
MISSING_MARKER = object()


class Entity[TId]:
    """Базовый строительный блок агрегата: равенство по `id` в
    рамках одного конкретного типа (не по значению полей), плюс накопление
    доменных событий, атомарно отдаваемых вызывающему через `pull_events()`
    — сама публикация (Outbox) остаётся за пределами этого класса.

    Конструктор вызывается только фабрикой `create()` для новой сущности или
    `reconstitute()` для гидратации из хранилища. Обе передают
    `PRIVATE_MARKER`; `reconstitute()` не повторяет бизнес-валидацию и не
    вызывает `add_domain_event(...)`.
    """

    def __init__(
        self,
        marker: object = MISSING_MARKER,
        id: TId = cast("TId", MISSING_MARKER),
    ) -> None:
        """Инициализирует базовый инстанс энтити.

        `marker` — закрытый токен `PRIVATE_MARKER`: он не даёт вызвать
        конструктор в обход доменной фабрики.

        Сразу проставляет переданный айдишник и подготавливает пустой массив под
        будущие доменные ивенты. Мутации состояния агрегата должны будут аппендить
        события именно в этот внутренний список.

        Args:
            marker: Закрытый токен конструктора.
            id (TId): Уникальный идентификатор сущности (обычно UUID или инт)."""
        if marker is not PRIVATE_MARKER:
            raise RuntimeError(
                "Entity instances must be created through create() or reconstitute()"
            )
        if id is MISSING_MARKER:
            raise TypeError("Entity id is required")

        self.id = id
        self._domain_events: list[DomainEvent] = []

    def add_domain_event(self, event: DomainEvent) -> None:
        """Регистрирует новое доменное событие во внутреннем стейте агрегата.

        Тупо аппендит ивент в in-memory список `_domain_events`.
        Ожидается, что этот метод будет дергаться из бизнес-методов энтити после
        успешной мутации стейта, чтобы потом инфраструктура смогла их слить и задиспатчить.

        Args:
            event (DomainEvent): Инстанс доменного события для добавления в очередь."""
        self._domain_events.append(event)

    def pull_events(self) -> list[DomainEvent]:
        """Забирает все накопленные доменные события и флушит внутреннюю очередь.

        Атомарно выгребает текущий список `_domain_events` и ресетит его в пустой.
        Сайд-эффект: после вызова очередь событий внутри агрегата очищается.
        Это нужно для того, чтобы при коммите транзакции аутбокс-паттерн или
        евент-бас забрали события ровно один раз.

        Returns:
            list[DomainEvent]: Список накопленных доменных событий."""
        events = self._domain_events
        self._domain_events = []
        return events

    def __eq__(self, other: object) -> bool:
        """Проверяет равенство двух сущностей по их айдишнику.

        Две энтити считаются равными, если они принадлежат строго к одному и тому же классу
        и имеют одинаковые `id`. Равенство по значению остальных полей игнорится.

        Args:
            other (object): Объект для сравнения.

        Returns:
            bool: True, если объекты одного типа и их id совпадают (либо это один и тот же инстанс)."""
        if type(other) is not type(self):
            return False
        return self is other or self.id == other.id

    def __hash__(self) -> int:
        """Вычисляет хэш сущности на основе ее типа и айдишника.

        Позволяет безопасно пихать энтити в сеты и использовать как ключи в диктах.
        Завязан только на неизменяемый `id` и класс, так что хэш стабилен на протяжении
        жизни объекта.

        Returns:
            int: Вычисленное значение хэша."""
        return hash((type(self), self.id))
