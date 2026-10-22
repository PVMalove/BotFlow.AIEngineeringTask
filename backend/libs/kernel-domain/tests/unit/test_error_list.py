# ruff: noqa: E501
import json

import pytest

from kernel_domain.errors import Error, ErrorList, ErrorType


def _validation_error(code: str, field: str) -> Error:
    return Error.validation(code, f"Описание {code}", invalid_field=field)


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


def test_error_list_serializes_to_the_documented_json_shape() -> None:
    first = _validation_error("invalid_name", "name")
    second = Error.validation("invalid_form", "Общая ошибка формы")
    error_list = ErrorList.of([first, second])
    assert isinstance(error_list, ErrorList)

    payload = json.loads(error_list.serialize())

    assert payload == {
        "errors": [
            {
                "code": "invalid_name",
                "description": "Описание invalid_name",
                "type": "VALIDATION",
                "invalid_field": "name",
            },
            {
                "code": "invalid_form",
                "description": "Общая ошибка формы",
                "type": "VALIDATION",
                "invalid_field": None,
            },
        ]
    }


def test_error_list_round_trips_through_polymorphic_deserialize() -> None:
    first = _validation_error("invalid_name", "name")
    second = _validation_error("invalid_price", "price")
    error_list = ErrorList.of([first, second])
    assert isinstance(error_list, ErrorList)

    restored = Error.deserialize(error_list.serialize())

    assert isinstance(restored, ErrorList)
    assert restored.code == error_list.code
    assert restored.type is error_list.type
    assert restored.errors == error_list.errors


@pytest.mark.parametrize(
    "raw",
    [
        "{}",
        '{"errors": []}',
        '{"errors": [{"code": "x"}]}',
        '{"errors": [{"code": "x", "description": "y", "type": "NOT_A_TYPE", "invalid_field": null}]}',
    ],
)
def test_deserialize_raises_value_error_for_malformed_error_list_json(
    raw: str,
) -> None:
    with pytest.raises(ValueError):
        Error.deserialize(raw)
