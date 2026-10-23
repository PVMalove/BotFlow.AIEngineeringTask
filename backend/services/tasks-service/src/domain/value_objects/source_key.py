def source_key(bot_id: int, chat_id: int, message_id: int) -> str:
    """The Telegram message a task came from; unique per task."""
    return f"{bot_id}:{chat_id}:{message_id}"
