"""Board socket: relays "the board changed" hints to approved members.

The frame carries no task data: the panel re-reads the REST board. Every frame is
preceded by a fresh Identity check; the socket holds no database session.
"""

import asyncio
from contextlib import suppress

from fastapi import APIRouter, WebSocket
from kernel_platform.security.origin import is_same_origin
from starlette.websockets import WebSocketDisconnect

from api.containers import Services
from api.dependencies import ServicesDI
from application.ports import (
    BoardFeedUnavailable,
    BoardSubscription,
    IdentityUnavailable,
)
from infrastructure.identity_gateway import SESSION_COOKIE

router = APIRouter()

POLICY_VIOLATION = 1008  # no access: the client must re-check its session
TRY_AGAIN_LATER = 1013  # Identity cannot answer: reconnect and re-read the board
SEND_TIMEOUT = 5.0

BOARD_CHANGED = {"type": "board.changed"}
PING = {"type": "ping"}


async def _denial(services: Services, token: str) -> int | None:
    """The close code when the session has no access right now, else None."""
    try:
        result = await services.identity.require_approved(token)
    except IdentityUnavailable:
        return TRY_AGAIN_LATER
    return None if result.is_ok else POLICY_VIOLATION


async def _until_disconnect(websocket: WebSocket) -> None:
    while (await websocket.receive())["type"] != "websocket.disconnect":
        pass  # whatever the client sends is ignored


async def _relay(
    websocket: WebSocket,
    services: Services,
    token: str,
    subscription: BoardSubscription,
) -> None:
    while True:
        changed = await subscription.wait(services.board_heartbeat)
        denial = await _denial(services, token)
        if denial is not None:
            await websocket.close(code=denial)
            return
        async with asyncio.timeout(SEND_TIMEOUT):
            await websocket.send_json(BOARD_CHANGED if changed else PING)


async def _serve(
    websocket: WebSocket,
    services: Services,
    token: str,
    subscription: BoardSubscription,
) -> None:
    reader = asyncio.create_task(_until_disconnect(websocket))
    sender = asyncio.create_task(_relay(websocket, services, token, subscription))
    try:
        done, _ = await asyncio.wait(
            {reader, sender}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for task in (reader, sender):
            task.cancel()
        await asyncio.gather(reader, sender, return_exceptions=True)
    for task in done:
        with suppress(WebSocketDisconnect, TimeoutError):
            task.result()


@router.websocket("/ws/board")
async def board(websocket: WebSocket, services: ServicesDI) -> None:
    token = websocket.cookies.get(SESSION_COOKIE)
    if not is_same_origin(websocket) or not token:
        await websocket.close(code=POLICY_VIOLATION)
        return
    denial = await _denial(services, token)
    if denial is not None:
        await websocket.close(code=denial)
        return
    try:
        async with services.board_feed.subscribe() as subscription:
            await websocket.accept()
            await _serve(websocket, services, token, subscription)
    except BoardFeedUnavailable:
        # Not a storage error: nothing to roll back. The client reconnects and
        # re-reads the board, which also recovers anything it missed.
        await websocket.close(code=TRY_AGAIN_LATER)
