"""Consumer side of Identity events: inbox and notification intent in one step."""

import json
from uuid import UUID

from application.ports import IntentStore
from domain.notifications import NotificationIntent

ACCESS_DECIDED = "member.access-decided"
_DECISIONS = {"approved", "rejected", "unblocked"}


class InvalidEvent(Exception):
    """The message can never become an intent; redelivery would not help."""


class ProcessIdentityEvent:
    def __init__(self, store: IntentStore) -> None:
        self._store = store

    async def execute(
        self, message_id: str | None, event_type: str | None, body: bytes
    ) -> bool:
        """True when a new intent was stored; False for a redelivered message."""
        intent = self._parse(message_id, event_type, body)
        # message_id is the event id, so inbox and intent share one key.
        return await self._store.store_event(str(intent.id), intent)

    @staticmethod
    def _parse(
        message_id: str | None, event_type: str | None, body: bytes
    ) -> NotificationIntent:
        try:
            if event_type != ACCESS_DECIDED or message_id is None:
                raise ValueError
            payload = json.loads(body)
            telegram_id, version = payload["telegram_id"], payload["access_version"]
            decision = payload["decision"]
            valid = (
                isinstance(telegram_id, int)
                and not isinstance(telegram_id, bool)
                and telegram_id > 0
                and isinstance(version, int)
                and not isinstance(version, bool)
                and version >= 1
                and decision in _DECISIONS
            )
            if not valid:
                raise ValueError
            return NotificationIntent(
                id=UUID(message_id),
                chat_id=telegram_id,
                member_id=UUID(payload["member_id"]),
                kind=f"access.{decision}",
                access_version=version,
            )
        except (ValueError, KeyError, TypeError):
            raise InvalidEvent from None
