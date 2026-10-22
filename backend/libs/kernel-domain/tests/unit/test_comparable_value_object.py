# ruff: noqa: E501
from dataclasses import dataclass
from typing import Any

from kernel_domain.comparable_value_object import ComparableValueObject


@dataclass(frozen=True, eq=False)
class Quantity(ComparableValueObject):
    value: int

    def _comparable_equality_components(self) -> tuple[Any, ...]:
        return (self.value,)


@dataclass(frozen=True, eq=False)
class Weight(ComparableValueObject):
    value: int

    def _comparable_equality_components(self) -> tuple[Any, ...]:
        return (self.value,)


def test_a_smaller_value_object_is_less_than_a_bigger_one() -> None:
    assert Quantity(value=1) < Quantity(value=2)


def test_a_bigger_value_object_is_greater_than_a_smaller_one() -> None:
    assert Quantity(value=2) > Quantity(value=1)


def test_equal_value_objects_are_not_less_than_each_other() -> None:
    assert not (Quantity(value=1) < Quantity(value=1))
    assert Quantity(value=1) <= Quantity(value=1)
    assert Quantity(value=1) >= Quantity(value=1)


def test_sorting_uses_the_comparable_components() -> None:
    assert sorted([Quantity(value=3), Quantity(value=1), Quantity(value=2)]) == [
        Quantity(value=1),
        Quantity(value=2),
        Quantity(value=3),
    ]


def test_comparable_value_objects_still_compare_equal_by_value() -> None:
    assert Quantity(value=1) == Quantity(value=1)


def test_different_types_are_ordered_by_type_name() -> None:
    weight = Weight(value=1)
    quantity = Quantity(value=1)

    lesser, greater = sorted([weight, quantity])

    assert type(lesser).__name__ < type(greater).__name__
