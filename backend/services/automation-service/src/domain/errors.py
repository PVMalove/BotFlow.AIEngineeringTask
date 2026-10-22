"""Registry of the expected Result errors of the automation domain and application."""

from kernel_domain.errors import Error


class AutomationErrors:
    @staticmethod
    def login_denied() -> Error:
        return Error.forbidden("login_denied", "Доступ не предоставлен.")

    @staticmethod
    def rate_limited() -> Error:
        return Error.rate_limited(
            "rate_limited", "Слишком много запросов. Повторите позже."
        )

    @staticmethod
    def unknown_job() -> Error:
        return Error.not_found("unknown_job", "Задание не найдено.")

    @staticmethod
    def invalid_decision() -> Error:
        return Error.validation("invalid_decision", "Решение о доступе недопустимо.")
