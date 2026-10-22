"""Internal member queries and login code issuance endpoints."""

from fastapi import APIRouter, Response
from kernel_platform.http import ApiResponse, match_result

from api.dependencies import (
    FindMemberDI,
    IssueLoginCodeDI,
    SubmitJoinRequestDI,
    service_operation,
)
from api.schemas import FindMemberRequest, IssueCodeRequest, JoinRequestBody
from api.security import Operation
from contracts.views import JoinRequestView, LoginCodeView, MemberView

router = APIRouter(tags=["internal"])


@router.get(
    "/internal/v1/members/by-telegram/{telegram_id}",
    response_model=ApiResponse[MemberView],
    summary="Поиск участника по Telegram ID",
    description=(
        "Служебный эндпоинт для внутренних сервисов: возвращает статус "
        "и роль зарегистрированного участника."
    ),
    dependencies=[service_operation(Operation.FIND_MEMBER)],
)
async def find_member(
    telegram_id: int, response: Response, handler: FindMemberDI
) -> ApiResponse[MemberView]:
    """Найти участника по Telegram ID."""
    query = FindMemberRequest(telegram_id=telegram_id).to_query()
    response.headers["Cache-Control"] = "no-store"
    return match_result(await handler.execute(query))


@router.post(
    "/internal/v1/login-codes",
    response_model=ApiResponse[LoginCodeView],
    summary="Выпуск одноразового кода авторизации",
    description=(
        "Служебный эндпоинт для Telegram-бота: выпускает одноразовый 5-минутный "
        "код для одобренного участника."
    ),
    dependencies=[service_operation(Operation.ISSUE_LOGIN_CODE)],
)
async def issue_login_code(
    body: IssueCodeRequest, response: Response, handler: IssueLoginCodeDI
) -> ApiResponse[LoginCodeView]:
    """Выпустить одноразовый код для входа в веб-панель."""
    response.headers["Cache-Control"] = "no-store"
    return match_result(await handler.execute(body.to_command()))


@router.post(
    "/internal/v1/join-requests",
    response_model=ApiResponse[JoinRequestView],
    summary="Заявка на доступ из Telegram",
    description=(
        "Служебный эндпоинт для Telegram-бота: создаёт заявку (ожидающего участника), "
        "повторяет её после отказа и не обходит блокировку."
    ),
    dependencies=[service_operation(Operation.SUBMIT_JOIN_REQUEST)],
)
async def submit_join_request(
    body: JoinRequestBody, response: Response, handler: SubmitJoinRequestDI
) -> ApiResponse[JoinRequestView]:
    """Принять заявку на доступ; решение о допуске принимает только Identity."""
    response.headers["Cache-Control"] = "no-store"
    return match_result(await handler.execute(body.to_command()))


__all__ = ["router"]
