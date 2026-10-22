"""API request schemas for identity service."""

from pydantic import BaseModel, Field

from application.commands.auth import (
    ExchangeLoginCodeCommand,
    IssueLoginCodeCommand,
)
from application.queries.members import FindMemberByTelegramQuery


class ExchangeRequest(BaseModel):
    """Запрос на обмен одноразового кода на сессию."""

    code: str = Field(
        max_length=64,
        description="32-символьный одноразовый код авторизации Telegram",
    )

    def to_command(self, *, client: str) -> ExchangeLoginCodeCommand:
        return ExchangeLoginCodeCommand(code=self.code, client=client)


class IssueCodeRequest(BaseModel):
    """Запрос на выпуск одноразового кода авторизации."""

    telegram_id: int = Field(
        gt=0,
        description="Идентификатор пользователя в Telegram",
    )

    def to_command(self) -> IssueLoginCodeCommand:
        return IssueLoginCodeCommand(telegram_id=self.telegram_id)


class FindMemberRequest(BaseModel):
    """Параметры поиска участника по Telegram ID."""

    telegram_id: int = Field(
        gt=0,
        description="Идентификатор пользователя в Telegram",
    )

    def to_query(self) -> FindMemberByTelegramQuery:
        return FindMemberByTelegramQuery(telegram_id=self.telegram_id)


__all__ = [
    "ExchangeRequest",
    "FindMemberRequest",
    "IssueCodeRequest",
]
