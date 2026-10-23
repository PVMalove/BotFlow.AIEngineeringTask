import pytest

from kernel_domain.errors import Error, ErrorList, ErrorType


def test_of_a_single_error_returns_it_unwrapped() -> None:
    error = _validation_error("invalid_name", "name")

    result = ErrorList.of([error])

    assert result is error


def test_of_multiple_errors_returns_an_error_list_with_the_fixed_shape() -> None:
    first = _validation_error("invalid_name", "name")
    second = _validation_error("invalid_price", "price")

    result = ErrorList.of([first, second])

    assert isinstance(result, ErrorList)
    assert isinstance(result, Error)
    assert result.code == "general_multiple_validation_errors"
    assert result.type is ErrorType.VALIDATION
    assert result.errors == (first, second)


def test_of_preserves_order_and_duplicates() -> None:
    first = _validation_error("invalid_name", "name")
    second = _validation_error("invalid_price", "price")

    result = ErrorList.of([first, second, first])

    assert isinstance(result, ErrorList)
    assert result.errors == (first, second, first)


def test_of_flattens_nested_error_lists_preserving_order() -> None:
    first = _validation_error("invalid_name", "name")
    second = _validation_error("invalid_price", "price")
    third = _validation_error("invalid_category", "category")
    nested = ErrorList.of([first, second])
    assert isinstance(nested, ErrorList)

    result = ErrorList.of([nested, third])

    assert isinstance(result, ErrorList)
    assert result.errors == (first, second, third)


def test_of_rejects_an_empty_collection() -> None:
    with pytest.raises(ValueError):
        ErrorList.of([])


def test_of_rejects_a_non_validation_error() -> None:
    validation_error = _validation_error("invalid_name", "name")
    not_found_error = Error.not_found("user.not_found", "Пользователь не найден")

    with pytest.raises(ValueError):
        ErrorList.of([validation_error, not_found_error])


def _validation_error(code: str, field: str) -> Error:
    return Error.validation(code, f"Описание {code}", invalid_field=field)
