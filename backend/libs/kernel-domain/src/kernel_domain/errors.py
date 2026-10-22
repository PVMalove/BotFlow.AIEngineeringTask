# ruff: noqa: E501
import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

_SERIALIZATION_DELIMITER = "||"
_MULTIPLE_VALIDATION_CODE = "general_multiple_validation_errors"
_MULTIPLE_VALIDATION_DESCRIPTION = "Обнаружены множественные ошибки валидации"


class ErrorType(Enum):
    VALIDATION = "VALIDATION"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    FORBIDDEN = "FORBIDDEN"
    UNAUTHORIZED = "UNAUTHORIZED"
    PROBLEM = "PROBLEM"
    FAILURE = "FAILURE"


@dataclass(frozen=True, kw_only=True)
class Error:
    """Единая доменная ошибка (ADR 0003/0014). `invalid_field` — публичное
    `snake_case`-имя JSON-поля запроса, которого касается нарушение; `None`
    для ошибок без привязки к конкретному полю."""

    code: str
    description: str
    type: ErrorType
    invalid_field: str | None = None

    @classmethod
    def validation(
        cls, code: str, description: str, *, invalid_field: str | None = None
    ) -> "Error":
        """
        Создаёт объект Error, указывающий, что запрос не прошёл проверку бизнес-правила.

        Используется, когда значение поля или запроса в целом нарушает доменное
        правило валидации. Соответствует HTTP 400 (Bad Request).
        Пример: Error.validation("invalid_price", "Цена не может быть отрицательной", invalid_field="price")
        """
        return cls(
            code=code,
            description=description,
            type=ErrorType.VALIDATION,
            invalid_field=invalid_field,
        )

    @classmethod
    def not_found(cls, code: str, description: str) -> "Error":
        """
        Создаёт объект Error, указывающий, что запрашиваемый ресурс не найден.

        Используется, когда запрашиваемый ресурс не существует. Соответствует HTTP 404 (Not Found).
        Пример: Error.not_found("CheckEnterprise.NotFound", "Не найдено проверяемое предприятие с идентификатором 12345")
        """
        return cls(code=code, description=description, type=ErrorType.NOT_FOUND)

    @classmethod
    def conflict(cls, code: str, description: str) -> "Error":
        """
        Создаёт объект Error, указывающий, что операция конфликтует с текущим состоянием ресурса.

        Используется, когда ресурс уже находится в состоянии, несовместимом с
        запрошенным действием (повторная активация, нарушение уникальности и
        т. п.). Соответствует HTTP 409 (Conflict).
        Пример: Error.conflict("already_active", "Товар уже активен")
        """
        return cls(code=code, description=description, type=ErrorType.CONFLICT)

    @classmethod
    def forbidden(cls, code: str, description: str) -> "Error":
        """
        Создаёт объект Error, указывающий, что действие запрещено для текущего актёра.

        Используется, когда актёр аутентифицирован, но не имеет прав на
        запрошенное действие. Соответствует HTTP 403 (Forbidden).
        Пример: Error.forbidden("not_owner", "Товар принадлежит другому пользователю")
        """
        return cls(code=code, description=description, type=ErrorType.FORBIDDEN)

    @classmethod
    def unauthorized(cls, code: str, description: str) -> "Error":
        """
        Создаёт объект Error, указывающий, что актёр не аутентифицирован.

        Используется, когда запрос не несёт валидных учётных данных (токен
        отсутствует, истёк или некорректен). Соответствует HTTP 401 (Unauthorized).
        Пример: Error.unauthorized("invalid_token", "Токен доступа недействителен")
        """
        return cls(code=code, description=description, type=ErrorType.UNAUTHORIZED)

    @classmethod
    def problem(cls, code: str, description: str) -> "Error":
        """
        Создаёт объект Error для доменного нарушения, не привязанного к конкретному полю.

        Используется для бизнес-проблем, которые не являются ни ошибкой
        отдельного поля (см. `validation`), ни конфликтом состояния ресурса
        (см. `conflict`) — например, превышение общего лимита операции.
        Соответствует HTTP 400 (Bad Request).
        Пример: Error.problem("order_limit_exceeded", "Превышен лимит заказов на сегодня")
        """
        return cls(code=code, description=description, type=ErrorType.PROBLEM)

    @classmethod
    def failure(cls, code: str, description: str) -> "Error":
        """
        Создаёт объект Error для непредвиденного технического сбоя.

        Используется, когда операция не может быть завершена по причине,
        не являющейся бизнес-правилом (сбой инфраструктуры, недоступность
        зависимости и т. п.), но домену всё равно нужно вернуть `Result.err`,
        а не бросить исключение. Соответствует HTTP 500 (Internal Server Error).
        Пример: Error.failure("identity_unavailable", "identity-service недоступен")
        """
        return cls(code=code, description=description, type=ErrorType.FAILURE)

    def serialize(self) -> str:
        """`||`-формат одиночной ошибки (ADR 0014). Отказывается терять
        данные: если значение поля само содержит разделитель, это ошибка
        вызывающего кода, а не повод молча всё сломать при десериализации."""
        field_value = self.invalid_field or ""
        parts = (self.code, self.description, self.type.value, field_value)
        for part in parts:
            if _SERIALIZATION_DELIMITER in part:
                raise ValueError(
                    f"Значение поля Error содержит зарезервированный разделитель {_SERIALIZATION_DELIMITER!r}: {part!r}"
                )
        return _SERIALIZATION_DELIMITER.join(parts)

    @classmethod
    def deserialize(cls, raw: str) -> "Error":
        """Полиморфная десериализация (ADR 0014): JSON-объект восстанавливает
        `ErrorList`, иначе — `||`-формат одиночной `Error`."""
        if raw.startswith("{"):
            return ErrorList._deserialize_json(raw)
        return cls._deserialize_delimited(raw)

    @classmethod
    def _deserialize_delimited(cls, raw: str) -> "Error":
        parts = raw.split(_SERIALIZATION_DELIMITER)
        if len(parts) != 4:
            raise ValueError(f"Некорректный формат сериализованной Error: {raw!r}")
        code, description, type_value, field_value = parts
        try:
            error_type = ErrorType(type_value)
        except ValueError as exc:
            raise ValueError(
                f"Некорректный формат сериализованной Error: {raw!r}"
            ) from exc
        return cls(
            code=code,
            description=description,
            type=error_type,
            invalid_field=field_value or None,
        )


@dataclass(frozen=True, kw_only=True)
class ErrorList(Error):
    """Несколько независимых нарушений одной операции (ADR 0014). Наследует
    `Error`, поэтому остаётся совместимой с `Result[T]`, но фиксирует
    `code`/`type` и несёт дочерние validation-ошибки в `errors`."""

    errors: tuple[Error, ...]

    @classmethod
    def of(cls, errors: Sequence[Error]) -> Error:
        """Фабрика: разворачивает вложенные `ErrorList` в плоскую коллекцию
        с сохранением порядка и повторов, запрещает пустой результат и
        возвращает единственную ошибку без обёртки, если после разворачивания
        осталась только одна."""
        flattened: list[Error] = []
        for error in errors:
            if isinstance(error, ErrorList):
                flattened.extend(error.errors)
            else:
                flattened.append(error)

        if not flattened:
            raise ValueError("ErrorList требует хотя бы одну ошибку")

        for error in flattened:
            if error.type is not ErrorType.VALIDATION:
                raise ValueError(
                    f"ErrorList принимает только ошибки типа VALIDATION, получено {error.type}"
                )

        if len(flattened) == 1:
            return flattened[0]

        return cls(
            code=_MULTIPLE_VALIDATION_CODE,
            description=_MULTIPLE_VALIDATION_DESCRIPTION,
            type=ErrorType.VALIDATION,
            errors=tuple(flattened),
        )

    def serialize(self) -> str:
        """`ErrorList` сериализуется строго как JSON-объект (ADR 0014),
        в отличие от `||`-формата одиночной `Error`."""
        return json.dumps(
            {
                "errors": [
                    {
                        "code": error.code,
                        "description": error.description,
                        "type": error.type.value,
                        "invalid_field": error.invalid_field,
                    }
                    for error in self.errors
                ]
            }
        )

    @classmethod
    def _deserialize_json(cls, raw: str) -> "ErrorList":
        try:
            payload = json.loads(raw)
            children_raw = payload["errors"]
            children = tuple(
                Error(
                    code=child["code"],
                    description=child["description"],
                    type=ErrorType(child["type"]),
                    invalid_field=child.get("invalid_field"),
                )
                for child in children_raw
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Некорректный формат сериализованной ErrorList: {raw!r}"
            ) from exc

        if not children:
            raise ValueError(f"Некорректный формат сериализованной ErrorList: {raw!r}")

        return cls(
            code=_MULTIPLE_VALIDATION_CODE,
            description=_MULTIPLE_VALIDATION_DESCRIPTION,
            type=ErrorType.VALIDATION,
            errors=children,
        )
