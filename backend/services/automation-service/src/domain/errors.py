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

    @staticmethod
    def job_limit() -> Error:
        return Error.rate_limited(
            "job_limit", "Слишком много задач в обработке. Повторите позже."
        )

    @staticmethod
    def voice_limit() -> Error:
        return Error.rate_limited(
            "voice_limit", "Слишком много голосовых сообщений в обработке."
        )

    @staticmethod
    def unknown_notification_kind() -> Error:
        """No delivery rule exists for this kind; it is never sent by default."""
        return Error.problem(
            "unknown_notification_kind", "Для этого уведомления нет правила доставки."
        )
