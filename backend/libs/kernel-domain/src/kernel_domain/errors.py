from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

_MULTIPLE_VALIDATION_CODE = "general_multiple_validation_errors"
_MULTIPLE_VALIDATION_DESCRIPTION = "Обнаружены множественные ошибки валидации"


class ErrorType(Enum):
    VALIDATION = "VALIDATION"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    FORBIDDEN = "FORBIDDEN"
    UNAUTHORIZED = "UNAUTHORIZED"
    RATE_LIMITED = "RATE_LIMITED"
    PROBLEM = "PROBLEM"


@dataclass(frozen=True, kw_only=True)
class Error:
    """An expected failure of a business operation.

    `code` is a stable snake_case identifier, `description` a text that is safe to
    show to the user. `invalid_field` names the public JSON field a validation error
    is about; it is `None` for an error that belongs to no single field.
    """

    code: str
    description: str
    type: ErrorType
    invalid_field: str | None = None

    @classmethod
    def validation(
        cls, code: str, description: str, *, invalid_field: str | None = None
    ) -> "Error":
        """A business rule rejected a value (HTTP 400)."""
        return cls(
            code=code,
            description=description,
            type=ErrorType.VALIDATION,
            invalid_field=invalid_field,
        )

    @classmethod
    def not_found(cls, code: str, description: str) -> "Error":
        """The resource does not exist (HTTP 404)."""
        return cls(code=code, description=description, type=ErrorType.NOT_FOUND)

    @classmethod
    def conflict(cls, code: str, description: str) -> "Error":
        """The operation contradicts the current state of the resource (HTTP 409)."""
        return cls(code=code, description=description, type=ErrorType.CONFLICT)

    @classmethod
    def forbidden(cls, code: str, description: str) -> "Error":
        """The actor is known but may not do this (HTTP 403)."""
        return cls(code=code, description=description, type=ErrorType.FORBIDDEN)

    @classmethod
    def unauthorized(cls, code: str, description: str) -> "Error":
        """The actor is not authenticated (HTTP 401)."""
        return cls(code=code, description=description, type=ErrorType.UNAUTHORIZED)

    @classmethod
    def rate_limited(cls, code: str, description: str) -> "Error":
        """Too many attempts (HTTP 429)."""
        return cls(code=code, description=description, type=ErrorType.RATE_LIMITED)

    @classmethod
    def problem(cls, code: str, description: str) -> "Error":
        """A business problem tied to no field and no resource state (HTTP 400)."""
        return cls(code=code, description=description, type=ErrorType.PROBLEM)


@dataclass(frozen=True, kw_only=True)
class ErrorList(Error):
    """Several independent validation failures of one operation."""

    errors: tuple[Error, ...]

    @classmethod
    def of(cls, errors: Sequence[Error]) -> Error:
        """Flatten nested lists, keep order, accept only validation errors.

        A single remaining error is returned as it is, without the wrapper.
        """
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
                    "ErrorList принимает только ошибки типа VALIDATION, "
                    f"получено {error.type}"
                )

        if len(flattened) == 1:
            return flattened[0]

        return cls(
            code=_MULTIPLE_VALIDATION_CODE,
            description=_MULTIPLE_VALIDATION_DESCRIPTION,
            type=ErrorType.VALIDATION,
            errors=tuple(flattened),
        )
