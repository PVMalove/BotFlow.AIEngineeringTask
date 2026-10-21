# Асинхронный Python backend

Backend использует Python 3.12, FastAPI, Pydantic, SQLAlchemy async, asyncpg и Alembic. uv workspace объединяет независимо запускаемые сервисы и технические библиотеки; зависимости закрепляются lockfile.

Для Telegram выбран aiogram 3, Redis — redis-py asyncio, RabbitMQ — aio-pika; DI — dependency-injector. Проверки: Ruff, mypy, pytest, pytest-asyncio и HTTPX. Точная совместимость Whisper/PyTorch и инфраструктурных адаптеров подтверждается отдельными проверками, не наличием зависимостей в манифесте.
