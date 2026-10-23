import pytest

from kernel_domain.errors import Error, ErrorType


def test_error_type_has_exactly_the_seven_values() -> None:
    assert {member.name for member in ErrorType} == {
        "VALIDATION",
        "NOT_FOUND",
        "CONFLICT",
        "FORBIDDEN",
        "UNAUTHORIZED",
        "RATE_LIMITED",
        "PROBLEM",
    }


def test_error_carries_code_description_and_type() -> None:
    error = Error(
        code="user.not_found",
        description="Пользователь не найден",
        type=ErrorType.NOT_FOUND,
    )

    assert error.code == "user.not_found"
    assert error.description == "Пользователь не найден"
    assert error.type is ErrorType.NOT_FOUND
    assert error.invalid_field is None


def test_error_carries_an_optional_invalid_field() -> None:
    error = Error(
        code="invalid_price",
        description="Цена не может быть отрицательной",
        type=ErrorType.VALIDATION,
        invalid_field="price",
    )

    assert error.invalid_field == "price"


@pytest.mark.parametrize(
    ("factory_name", "expected_type"),
    [
        ("not_found", ErrorType.NOT_FOUND),
        ("conflict", ErrorType.CONFLICT),
        ("forbidden", ErrorType.FORBIDDEN),
        ("unauthorized", ErrorType.UNAUTHORIZED),
        ("rate_limited", ErrorType.RATE_LIMITED),
        ("problem", ErrorType.PROBLEM),
    ],
)
def test_factory_builds_an_error_with_the_matching_type(
    factory_name: str, expected_type: ErrorType
) -> None:
    factory = getattr(Error, factory_name)

    error = factory("some_code", "Некое описание")

    assert error == Error(
        code="some_code", description="Некое описание", type=expected_type
    )


def test_validation_factory_accepts_an_optional_invalid_field() -> None:
    error = Error.validation(
        "invalid_price", "Цена не может быть отрицательной", invalid_field="price"
    )

    assert error == Error(
        code="invalid_price",
        description="Цена не может быть отрицательной",
        type=ErrorType.VALIDATION,
        invalid_field="price",
    )


def test_validation_factory_defaults_invalid_field_to_none() -> None:
    error = Error.validation("invalid_price", "Цена не может быть отрицательной")

    assert error.invalid_field is None
