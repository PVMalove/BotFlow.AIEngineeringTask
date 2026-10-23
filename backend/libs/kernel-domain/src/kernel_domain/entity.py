from kernel_domain.domain_event import DomainEvent


class Entity[TId]:
    """Building block of an aggregate: equal by `id` within one type, and it
    collects domain events that the unit of work takes with `pull_events()`.

    A new entity is built by a `create()` factory (validation and an event), a
    stored one by `reconstitute()` (no validation, no events). Publishing the
    events is not the entity's business.
    """

    def __init__(self, id: TId) -> None:
        self.id = id
        self._domain_events: list[DomainEvent] = []

    def add_domain_event(self, event: DomainEvent) -> None:
        self._domain_events.append(event)

    def pull_events(self) -> list[DomainEvent]:
        """Return the collected events and forget them: each is taken exactly once."""
        events = self._domain_events
        self._domain_events = []
        return events

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return False
        return self is other or self.id == other.id

    def __hash__(self) -> int:
        return hash((type(self), self.id))
