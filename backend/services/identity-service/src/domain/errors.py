"""Registry of the expected Result errors of the identity domain and application.

Deliberately coarse: a caller learns why only as far as it can act on it.
"""

from kernel_domain.errors import Error


class IdentityErrors:
    @staticmethod
    def invalid_login_code() -> Error:
        return Error.unauthorized(
            "invalid_code", "Код недействителен. Запросите новый в боте."
        )

    @staticmethod
    def invalid_session() -> Error:
        return Error.unauthorized("unauthenticated", "Войдите заново.")

    @staticmethod
    def member_not_approved() -> Error:
        return Error.forbidden("not_approved", "Доступ не подтверждён.")

    @staticmethod
    def admin_required() -> Error:
        return Error.forbidden("admin_required", "Нужны права администратора.")

    @staticmethod
    def rate_limited() -> Error:
        return Error.rate_limited(
            "rate_limited", "Слишком много попыток. Повторите позже."
        )

    @staticmethod
    def member_not_found() -> Error:
        return Error.not_found("member_not_found", "Участник не найден.")

    @staticmethod
    def version_conflict() -> Error:
        return Error.conflict(
            "version_conflict", "Участник уже изменился. Обновите список."
        )

    @staticmethod
    def invalid_transition() -> Error:
        return Error.conflict(
            "invalid_transition", "Это решение недоступно для текущего статуса."
        )

    @staticmethod
    def cannot_block_self() -> Error:
        return Error.conflict("cannot_block_self", "Нельзя заблокировать себя.")

    @staticmethod
    def last_admin() -> Error:
        return Error.conflict("last_admin", "Нельзя убрать последнего администратора.")

    @staticmethod
    def service_key_required() -> Error:
        return Error.unauthorized("service_key_required", "Нужен ключ сервиса.")

    @staticmethod
    def operation_not_allowed() -> Error:
        return Error.forbidden(
            "operation_not_allowed", "Операция недоступна для этого ключа."
        )
