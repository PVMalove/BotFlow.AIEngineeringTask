# ruff: noqa: E501
from abc import abstractmethod
from functools import total_ordering
from typing import Any

from kernel_domain.value_object import ValueObject


@total_ordering
class ComparableValueObject(ValueObject):
    """Объект-значение с полным порядком: сравнение —
    покомпонентно по `_comparable_equality_components()`, до первой
    различающейся пары; разные типы упорядочены по имени типа."""

    @abstractmethod
    def _comparable_equality_components(self) -> tuple[Any, ...]:
        """Возвращает кортеж компонентов для покомпонентного сравнения на больше/меньше.

        Под капотом используется для лексикографического сравнения двух инстансов value-объектов.
        Каждый элемент кортежа должен поддерживать операторы сравнения.

        Returns:
            tuple[Any, ...]: Кортеж значимых полей для определения порядка."""
        ...

    def _equality_components(self) -> tuple[Any, ...]:
        """Проксирует вызов к `_comparable_equality_components` для проверки равенства.

        Переиспользует логику из comparable-компонентов, чтобы не дублировать код возврата
        кортежа полей для базового класса ValueObject.

        Returns:
            tuple[Any, ...]: Тот же кортеж компонентов, что и для сравнения на больше/меньше."""
        return self._comparable_equality_components()

    def __lt__(self, other: object) -> bool:
        """Магический метод для оператора меньше (`<`).

        Алгоритм интуитивен: сначала чекаем, что объекты одного типа. Если типы разные,
        фоллбечимся на сравнение имен их классов в виде строк (чтобы избежать TypeError при
        сортировке микса разных value-объектов). Если типы совпадают, зипуем их компоненты и
        итерируемся до первого несовпадающего элемента. Как только нашли разницу — возвращаем
        результат сравнения этих компонентов. Если все проверенные компоненты равны, возвращаем False.

        Args:
            other (object): Объект, с которым сравниваем текущий инстанс.

        Returns:
            bool: True, если текущий объект строго меньше `other`, иначе False.
                  Возвращает NotImplemented, если `other` вообще не является инстансом `ComparableValueObject`."""
        if not isinstance(other, ComparableValueObject):
            return NotImplemented
        if type(other) is not type(self):
            return type(self).__name__ < type(other).__name__
        for left, right in zip(
            self._comparable_equality_components(),
            other._comparable_equality_components(),
        ):
            if left != right:
                return bool(left < right)
        return False
