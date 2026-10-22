# ruff: noqa: E501
from kernel_domain.errors import Error


class Result[T]:
    """Явный успех/неудача без исключений на ожидаемых сбоях бизнес-логики
    . `Result` без параметра — то же самое с T как Any; отдельного
    беззначенческого класса не заводим, `Result[None].ok(None)` покрывает
    void-кейс."""

    def __init__(self, *, value: T | None, error: Error | None, is_ok: bool) -> None:
        """Приватный конструктор объекта Result.

        Не предполагается для прямого вызова из бизнес-логики. Для инстанцирования
        надо юзать фабричные методы `ok()` или `fail()`. Под капотом просто сетит
        внутренние проперти значения, ошибки и флага успешности.

        Args:
            value (T | None): Полезная нагрузка (пе payload) при успешном исходе.
            error (Error | None): Инстанс доменной ошибки при фейле.
            is_ok (bool): Флаг статуса: True для саксесса, False для фейла."""
        self._value = value
        self._error = error
        self._is_ok = is_ok

    @classmethod
    def ok(cls, value: T) -> "Result[T]":
        """Фабричный метод для создания успешного результата.

        Пакует переданное значение в инстанс Result с проставленным флагом `is_ok=True`
        и пустой ошибкой. Если функция ничего не возвращает, обычно передают None.

        Args:
            value (T): Значение, которое нужно вернуть из операции.

        Returns:
            Result[T]: Успешный результат, оборачивающий переданное значение."""
        return cls(value=value, error=None, is_ok=True)

    @classmethod
    def fail(cls, error: Error) -> "Result[T]":
        """Фабричный метод для создания результата с ошибкой.

        Создает фейловый инстанс, куда прокидывается доменная ошибка.
        Значение полезной нагрузки при этом сетапится в None, а флаг `is_ok` — в False.

        Args:
            error (Error): Объект доменной ошибки с кодом и описанием.

        Returns:
            Result[T]: Ошибочный результат с зашитой внутрь инфой о фейле."""
        return cls(value=None, error=error, is_ok=False)

    @property
    def is_ok(self) -> bool:
        """Чекер успешности результата.

        Returns:
            bool: True, если операция завершилась успехом, без ошибки."""
        return self._is_ok

    @property
    def is_err(self) -> bool:
        """Чекер наличия ошибки.

        Returns:
            bool: True, если операция зафейлилась (т.е. `is_ok` равно False)."""
        return not self._is_ok

    @property
    def value(self) -> T:
        """Достает полезную нагрузку из успешного результата.

        Если попытаться дернуть этот проперти у фейлового результата, выкинет исключение.
        Ожидается, что вызывающий код предварительно сделает чек через `is_ok`.

        Raises:
            ValueError: Если результат находится в состоянии ошибки.

        Returns:
            T: Значение успешного выполнения операции."""
        if not self._is_ok:
            raise ValueError("Result в состоянии ошибки не несёт значения")
        return self._value  # type: ignore[return-value]

    @property
    def error(self) -> Error:
        """Достает объект ошибки из фейлового результата.

        Рейзит исключение, если попытаться получить ошибку из саксесс-результата.
        Всегда стоит чекать `is_err` перед обращением.

        Raises:
            ValueError: Если результат успешный и никакой ошибки не содержит.

        Returns:
            Error: Инстанс доменной ошибки."""
        if self._is_ok:
            raise ValueError("Успешный Result не несёт ошибки")
        assert self._error is not None
        return self._error
