"""API request schemas for identity service."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from application.commands.access import DecideMemberCommand
from application.commands.auth import (
    ExchangeLoginCodeCommand,
    IssueLoginCodeCommand,
)
from application.commands.join import SubmitJoinRequestCommand
from application.queries.members import FindMemberByTelegramQuery, ListMembersQuery
from domain.access import Action
from domain.entities.auth import MemberStatus


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


class JoinRequestBody(BaseModel):
    """Заявка на доступ, поданная в Telegram."""

    telegram_id: int = Field(
        gt=0,
        description="Идентификатор пользователя в Telegram",
    )
    display_name: str | None = Field(
        default=None,
        max_length=64,
        description="Имя, по которому администратор узнает заявителя",
    )

    def to_command(self) -> SubmitJoinRequestCommand:
        name = (self.display_name or "").strip()
        return SubmitJoinRequestCommand(
            telegram_id=self.telegram_id, display_name=name or None
        )


class DecisionBody(BaseModel):
    """Версия доступа участника, которую администратор видел перед решением."""

    expected_version: int = Field(
        ge=1,
        description="access_version участника в момент просмотра",
    )

    def to_command(
        self, actor_id: UUID, member_id: UUID, action: Action
    ) -> DecideMemberCommand:
        return DecideMemberCommand(
            actor_id=actor_id,
            member_id=member_id,
            action=action,
            expected_version=self.expected_version,
        )


class JoinRequestsListRequest(BaseModel):
    """Страница заявок: ожидающие или отклонённые."""

    status: Literal["pending", "rejected"] = Field(
        "pending", description="pending|rejected"
    )
    limit: int = Field(50, ge=1, le=100)
    offset: int = Field(0, ge=0)

    def to_query(self) -> ListMembersQuery:
        return ListMembersQuery((MemberStatus(self.status),), self.limit, self.offset)


class MembersListRequest(BaseModel):
    """Страница участников; без status — допущенные и заблокированные."""

    status: MemberStatus | None = None
    limit: int = Field(50, ge=1, le=100)
    offset: int = Field(0, ge=0)

    def to_query(self) -> ListMembersQuery:
        statuses = (
            (self.status,)
            if self.status
            else (MemberStatus.APPROVED, MemberStatus.BLOCKED)
        )
        return ListMembersQuery(statuses, self.limit, self.offset)


__all__ = [
    "DecisionBody",
    "ExchangeRequest",
    "FindMemberRequest",
    "IssueCodeRequest",
    "JoinRequestBody",
    "JoinRequestsListRequest",
    "MembersListRequest",
]
