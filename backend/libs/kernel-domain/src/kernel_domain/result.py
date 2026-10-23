from typing import cast

from kernel_domain.errors import Error


class Result[T]:
    """Success with a value, or an expected failure with an `Error`; no exceptions.

    Build one with `ok` or `fail`. `Result[None].ok(None)` covers a void operation.
    """

    def __init__(self, *, value: T | None, error: Error | None, is_ok: bool) -> None:
        self._value = value
        self._error = error
        self._is_ok = is_ok

    @classmethod
    def ok(cls, value: T) -> "Result[T]":
        return cls(value=value, error=None, is_ok=True)

    @classmethod
    def fail(cls, error: Error) -> "Result[T]":
        return cls(value=None, error=error, is_ok=False)

    @property
    def is_ok(self) -> bool:
        return self._is_ok

    @property
    def is_err(self) -> bool:
        return not self._is_ok

    @property
    def value(self) -> T:
        """The value of a successful result; asking a failed one raises ValueError."""
        if not self._is_ok:
            raise ValueError("Result в состоянии ошибки не несёт значения")
        return cast(T, self._value)

    @property
    def error(self) -> Error:
        """The error of a failed result; asking a successful one raises ValueError."""
        if self._is_ok:
            raise ValueError("Успешный Result не несёт ошибки")
        assert self._error is not None
        return self._error
