"""Expected authentication/authorization outcomes shared by all services."""

from kernel_domain.errors import Error


class AuthErrors:
    @staticmethod
    def unauthenticated() -> Error:
        return Error.unauthorized("unauthenticated", "Войдите заново.")

    @staticmethod
    def not_approved() -> Error:
        return Error.forbidden("not_approved", "Доступ к доске не подтверждён.")
