# ruff: noqa: E501
from abc import ABC, abstractmethod
from typing import Any


class ValueObject(ABC):
    """Объект-значение (по образцу CSharpFunctionalExtensions.ValueObject):
    равенство и хэш — по значению компонентов из `_equality_components()`,
    не по идентичности. Разные типы никогда не равны, даже с одинаковыми
    компонентами."""

    @abstractmethod
    def _equality_components(self) -> tuple[Any, ...]:
        """Возвращает кортеж значимых полей для проверки равенства value-объекта.

        Должен быть имплементирован в классах-наследниках. Возвращаемый кортеж
        определяет стейт объекта. Компоненты будут сравниваться по порядку.

        Returns:
            tuple[Any, ...]: Кортеж значений, составляющих суть объекта."""
        ...

    def __eq__(self, other: object) -> bool:
        """Проверяет равенство двух value-объектов по значению.

        Два объекта равны, если они строго одного типа и кортежи, возвращаемые их
        `_equality_components()`, полностью идентичны. Ссылочная идентичность не обязательна.

        Args:
            other (object): Другой объект для сравнения.

        Returns:
            bool: True, если объекты структурно эквивалентны, иначе False."""
        if type(other) is not type(self):
            return False
        return self._equality_components() == other._equality_components()

    def __hash__(self) -> int:
        """Вычисляет хэш value-объекта на основе его типа и всех компонентов.

        Делает объект пригодным для использования в качестве ключа в хеш-таблицах (сетах, диктах).
        Так как value-объекты иммутабельны, их хэш вычисляется надежно на базе `_equality_components()`.

        Returns:
            int: Целочисленный хэш объекта."""
        return hash((type(self), self._equality_components()))
