from kernel_domain.domain_event import DomainEvent
from kernel_domain.entity import Entity


def test_a_fresh_entity_has_no_pending_events() -> None:
    entity = Entity(1)

    assert entity.pull_events() == []


def test_added_events_are_returned_by_pull_events_in_the_order_added() -> None:
    entity = Entity(1)
    first = DomainEvent()
    second = DomainEvent()

    entity.add_domain_event(first)
    entity.add_domain_event(second)

    assert entity.pull_events() == [first, second]


def test_pull_events_clears_the_store_so_a_second_call_returns_nothing() -> None:
    entity = Entity(1)
    entity.add_domain_event(DomainEvent())

    entity.pull_events()

    assert entity.pull_events() == []


def test_events_added_after_a_pull_are_returned_by_the_next_pull() -> None:
    entity = Entity(1)
    entity.add_domain_event(DomainEvent())
    entity.pull_events()

    new_event = DomainEvent()
    entity.add_domain_event(new_event)

    assert entity.pull_events() == [new_event]


def test_entities_of_the_same_type_with_the_same_id_are_equal() -> None:
    assert Entity(1) == Entity(1)


def test_entities_of_the_same_type_with_the_same_id_hash_the_same() -> None:
    assert hash(Entity(1)) == hash(Entity(1))


def test_entities_of_the_same_type_with_different_ids_are_not_equal() -> None:
    assert Entity(1) != Entity(2)


def test_entities_of_different_types_with_the_same_id_are_not_equal() -> None:
    class OtherEntity(Entity[int]):
        pass

    assert Entity(1) != OtherEntity(1)


def test_an_entity_is_not_equal_to_a_non_entity() -> None:
    assert Entity(1) != object()
