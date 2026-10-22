# ruff: noqa: E501
import pytest

from kernel_domain.errors import Error, ErrorType
from kernel_domain.result import Result


def _an_error() -> Error:
    return Error(code="x", description="y", type=ErrorType.FAILURE)


def test_ok_result_reports_is_ok_and_carries_the_value() -> None:
    result: Result[int] = Result[int].ok(42)

    assert result.is_ok is True
    assert result.is_err is False
    assert result.value == 42


def test_fail_result_reports_is_err_and_carries_the_error() -> None:
    error = _an_error()

    result: Result[int] = Result[int].fail(error)

    assert result.is_ok is False
    assert result.is_err is True
    assert result.error is error


def test_accessing_value_of_a_failed_result_raises() -> None:
    result: Result[int] = Result[int].fail(_an_error())

    with pytest.raises(ValueError):
        _ = result.value


def test_accessing_error_of_a_successful_result_raises() -> None:
    result: Result[int] = Result[int].ok(1)

    with pytest.raises(ValueError):
        _ = result.error


def test_ok_without_a_value_is_a_valid_void_style_result() -> None:
    result: Result[None] = Result[None].ok(None)

    assert result.is_ok is True
    assert result.value is None
