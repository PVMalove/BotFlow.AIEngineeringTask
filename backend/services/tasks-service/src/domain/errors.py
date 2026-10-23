"""Registry of the expected Result errors of the tasks domain and application.

Stable snake_case codes and safe descriptions; the HTTP status follows from the
error type (kernel_platform.http.errors).
"""

from kernel_domain.errors import Error


class TaskErrors:
    @staticmethod
    def not_found() -> Error:
        return Error.not_found("task_not_found", "Задача не найдена.")

    @staticmethod
    def forbidden() -> Error:
        return Error.forbidden(
            "forbidden",
            "Менять статус этой задачи может только её автор или администратор.",
        )

    @staticmethod
    def version_conflict() -> Error:
        return Error.conflict(
            "version_conflict",
            "Задача уже изменилась. Обновите карточку и повторите действие.",
        )

    @staticmethod
    def invalid_text(max_length: int) -> Error:
        return Error.validation(
            "invalid_text",
            f"Текст задачи должен быть от 1 до {max_length} символов.",
            invalid_field="text",
        )

    @staticmethod
    def invalid_author_name(max_length: int) -> Error:
        return Error.validation(
            "invalid_author_name",
            f"Имя автора должно быть от 1 до {max_length} символов.",
            invalid_field="author_name",
        )

    @staticmethod
    def invalid_cursor() -> Error:
        return Error.validation(
            "invalid_cursor",
            "Страница списка недействительна.",
            invalid_field="cursor",
        )
