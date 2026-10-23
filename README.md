# BotFlow

Управление задачами через Telegram и общую веб-панель. Участник отправляет
боту текст или голосовое сообщение, а задача появляется на Kanban-доске.
Распознавание речи выполняется локально с помощью OpenAI Whisper.

![Kanban-доска BotFlow](docs/presentation/images/02-kanban-board.png)

## Реализованная функциональность

- Создание задач из текстовых и голосовых сообщений в Telegram.
- Kanban-доска со статусами «Ожидает», «В работе» и «Завершено», перетаскиванием карточек и обновлением без перезагрузки страницы.
- Вход в веб-панель по одноразовому коду из Telegram.
- Рассмотрение заявок на доступ и управление участниками администратором.
- Разграничение прав: участники изменяют свои задачи, администраторы — любые.
- Интерфейс на русском и армянском языках с поддержкой компьютеров и мобильных устройств.
- Фоновое распознавание голосовых сообщений локальной моделью Whisper.
- Защита от дублирования задач при повторной обработке и повторный запуск заданий после ошибок.

## Архитектура

![Взаимодействие сервисов BotFlow](docs/presentation/images/architecture.svg)

[Интерактивная схема](docs/presentation/botflow-architecture.html) подготовлена по коду проекта.

| Сервис | Ответственность |
| --- | --- |
| Identity | Участники, допуск, роли и сессии |
| Tasks | Задачи, статусы, версии и WebSocket доски |
| Automation | Telegram-бот, голосовые задания и уведомления |

У каждого сервиса своя PostgreSQL. RabbitMQ передаёт команды и результаты между
сервисами, Redis Streams обслуживает фоновые задания Automation. Аудио хранится
в приватном bucket S3 хранилища MinIO. Панель получает сигнал через WebSocket и перечитывает
данные по REST.

## Стек

| Область | Технологии |
| --- | --- |
| Backend | Python 3.12, FastAPI, Pydantic Settings, SQLAlchemy async, asyncpg, Alembic |
| Архитектура | Hexagonal Architecture, локальный CQRS, UoW, outbox/inbox, DI |
| Telegram | aiogram 3, long polling |
| Распознавание | Официальный локальный OpenAI Whisper, ffmpeg; подключение после проверки модели |
| Межсервисный обмен | RabbitMQ, aio-pika |
| Внутренние задания | Redis Streams: транскрипция и Telegram-уведомления |
| Realtime | Redis Pub/Sub, WebSocket, REST-синхронизация |
| Хранение | PostgreSQL, приватный MinIO bucket для аудио |
| Frontend | React, TypeScript, Vite, Material UI, TanStack Query, React Router |
| Инструменты | uv/uv.lock, Node 24 LTS/npm/package-lock, Ruff, mypy, ESLint, Prettier |
| Проверки | pytest, HTTPX, Vitest, React Testing Library, Playwright — сценарии добавляются с функциональностью |
| Контейнеры | Docker Compose, отдельные образы сервисов, nginx для SPA/API/WS |

## Структура проекта

```text
BotFlow.AIEngineeringTask/
├── README.md
├── CONTEXT.md                         # язык предметной области
├── Makefile                           # общие команды
├── docker-compose.yml                 # инфраструктура и процессы
├── .env.example                       # открытые параметры и пути секретов
├── scripts/
│   └── init-local-secrets.py           # локальные credentials без вывода значений
├── docs/
│   ├── architecture.md                # границы, потоки, надёжность и статус каркаса
│   ├── frontend-design.md             # визуальное направление и RU/HY
│   └── adr/                           # архитектурные решения
├── infra/
│   ├── minio/Dockerfile                # сборка официального MinIO по commit SHA
│   └── redis/redis.conf                # AOF и ACL
├── frontend/
│   ├── Dockerfile
│   ├── nginx.conf                     # единый origin SPA/API/WebSocket
│   ├── package.json
│   ├── package-lock.json
│   ├── vite.config.ts
│   ├── playwright.config.ts
│   ├── e2e/                           # место браузерных сценариев
│   └── src/
│       ├── components/
│       ├── hooks/
│       ├── App.tsx
│       └── main.tsx
└── backend/
    ├── Makefile
    ├── pyproject.toml                  # uv workspace
    ├── uv.lock
    ├── libs/                           # общие технические библиотеки, не бизнес-модели
    │   ├── kernel-domain/              # Result, Error, Entity, DomainEvent
    │   ├── kernel-platform/            # HTTP-контракт, UoW, outbox, воркеры
    │   ├── observability/              # request id, логи
    │   └── test-support/               # общие тестовые двойники
    └── services/
        ├── identity-service/
        ├── tasks-service/
        └── automation-service/
            ├── Dockerfile             # у каждого сервиса свой образ
            ├── pyproject.toml
            ├── src/                   # корень импортов конкретного сервиса
            │   ├── domain/            # чистая модель и инварианты
            │   │   ├── entities/
            │   │   ├── value_objects/
            │   │   ├── events/
            │   │   ├── repositories.py
            │   │   └── unit_of_work.py
            │   ├── application/       # commands/, queries/, ports.py
            │   ├── infrastructure/    # реализации портов: db/, security/
            │   │   └── db/
            │   │       ├── alembic.ini
            │   │       └── alembic/
            │   ├── api/               # HTTP, схемы, containers.py, worker/bot/bootstrap
            │   ├── contracts/         # framework-independent View: frozen dataclasses
            │   ├── core/              # кросс-срезная политика сервиса
            │   └── common/            # локальные утилиты
            ├── tests/
            │   ├── unit/
            │   ├── integration/
            │   ├── e2e/
            │   └── performance/
            ├── k8s/
            ├── docs/
            ├── ci/
            └── scripts/               # служебные CLI (voice_latency, whisper_validate)
```

## Запуск

```bash
cp .env.example .env
make init
```

В `.env` задайте `INITIAL_ADMIN_TELEGRAM_ID`, а токен бота запишите в
`.secrets/telegram_bot_token`. Затем подготовьте веса модели и запустите стенд:

```bash
make whisper-prepare
make demo
```

Веб-панель: [localhost:8080](http://localhost:8080). Для входа отправьте боту
`/login` и вставьте полученный код. `make down` останавливает стенд с сохранением данных.
Подробная настройка и команды оператора — в [руководстве запуска](docs/runbook.md).

## Проверки

```bash
make deps       # установка зависимостей из lockfiles
make check      # линтеры, типы и форматирование
make test       # backend и frontend
make build      # Docker-образы
make e2e        # изолированный стенд и Playwright
```

[GitHub Actions](https://github.com/PVMalove/BotFlow.AIEngineeringTask/actions/workflows/ci.yml)
выполняет эти проверки при push и для pull request. E2E использует реальные
PostgreSQL, RabbitMQ, Redis и MinIO, тестовые адаптеры Telegram и распознавания речи.
Ручная проверка приложения проведена на двух пользователях.

## Материалы

- [Архитектура и потоки данных](docs/architecture.md)
- [Принятые решения](docs/adr/)
- [Дизайн и локализация](docs/frontend-design.md)

Проект выполнен как тестовое задание. Исходные материалы и локальные секреты
не включены в репозиторий.
