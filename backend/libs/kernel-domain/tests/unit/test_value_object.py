# ruff: noqa: E501
from dataclasses import dataclass
from typing import Any

from kernel_domain.value_object import ValueObject


@dataclass(frozen=True, eq=False)
class Money(ValueObject):
    amount: int
    currency: str

    def _equality_components(self) -> tuple[Any, ...]:
        return self.amount, self.currency


@dataclass(frozen=True, eq=False)
class Weight(ValueObject):
    amount: int
    currency: str

    def _equality_components(self) -> tuple[Any, ...]:
        return self.amount, self.currency


def test_value_objects_with_the_same_components_are_equal() -> None:
    assert Money(amount=10, currency="USD") == Money(amount=10, currency="USD")


def test_equal_value_objects_hash_the_same() -> None:
    first = Money(amount=10, currency="USD")
    second = Money(amount=10, currency="USD")

    assert hash(first) == hash(second)


def test_value_objects_with_different_components_are_not_equal() -> None:
    assert Money(amount=10, currency="USD") != Money(amount=10, currency="EUR")


def test_different_types_with_the_same_components_are_not_equal() -> None:
    assert Money(amount=10, currency="USD") != Weight(amount=10, currency="USD")


def test_a_value_object_is_not_equal_to_a_non_value_object() -> None:
    assert Money(amount=10, currency="USD") != object()
