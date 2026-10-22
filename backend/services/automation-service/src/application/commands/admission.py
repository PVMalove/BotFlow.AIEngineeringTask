"""Who may send work: Identity's answer, shared by the text and the voice path."""

from uuid import UUID

from application.ports import IdentityUnavailable, MemberDirectory, Messenger

DENIED = "Доступ не предоставлен. Обратитесь к администратору."
UNAVAILABLE = "Сервис временно недоступен. Повторите отправку позже."


class Admission:
    def __init__(self, identity: MemberDirectory, messenger: Messenger) -> None:
        self._identity = identity
        self._messenger = messenger

    async def admit(self, telegram_id: int, chat_id: int) -> UUID | None:
        """The member's id, or None after the refusal reply was sent."""
        try:
            member = await self._identity.find_member(telegram_id)
        except IdentityUnavailable:
            await self.reply(chat_id, UNAVAILABLE)
            return None
        if member is None or member.status != "approved":
            await self.reply(chat_id, DENIED)
            return None
        if member.member_id is None:
            await self.reply(chat_id, UNAVAILABLE)
            return None
        return member.member_id

    async def reply(self, chat_id: int, text: str) -> bool:
        try:
            await self._messenger.send(chat_id, text)
        except Exception:
            return False  # the message is stored; a failed reply must not undo it
        return True
