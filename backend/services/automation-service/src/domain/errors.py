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
