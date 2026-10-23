# BotFlow.AIEngineeringTask

## Описание проекта

**BotFlow** — управление задачами команды через Telegram и веб-панель.
Пользователь отправляет текст или голосовое сообщение, получает подтверждение
приёма и видит созданную задачу на общей Kanban-доске. Голос распознаётся
локальным OpenAI Whisper на русском или армянском с определением языка.

Одна команда работает с одной доской. Участник меняет статус своих задач,
администратор — любых задач, а также одобряет заявки, блокирует и разблокирует
участников. Вход в веб-панель выполняется через одноразовый код Telegram.
Веб-интерфейс поддерживает русский и армянский с переключателем языка;
визуальное направление — светлая доска, графитовые поверхности и золотистые акценты
под собственным названием BotFlow. [Дизайн и локализация](docs/frontend-design.md).

Первая версия ориентирована на полный демонстрационный сценарий: базовые
повторы, дедупликация и восстановление после рестарта. Расширенные процедуры
аварийного восстановления и эксплуатации отложены: [ADR-0021](docs/adr/0021-proportionate-reliability.md).

**Состояние:** реализованы вход через Telegram, заявки и управление допуском, текстовый и голосовой
путь Telegram → задача, Kanban с Drag & Drop, realtime доски и replay упавшего задания (#5–#15, #18).
Три сервиса (Identity, Tasks, Automation) со своими PostgreSQL, RabbitMQ, Redis и MinIO запускаются
через Compose. Полный изолированный E2E, `make demo` и CI gate описаны ниже (#16).
Живая приёмка с настоящим ботом и качество Whisper на hy проверяются отдельно (#17).

## Стек

| Область | Технологии |
| --- | --- |
| Backend | Python 3.12, FastAPI, Pydantic Settings, SQLAlchemy async, asyncpg, Alembic |
| Архитектура | Hexagonal Architecture, локальный CQRS, UoW, outbox/inbox, dependency-injector |
| Telegram | aiogram 3, long polling |
| Распознавание | Официальный локальный OpenAI Whisper, ffmpeg; подключение после проверки модели |
| Межсервисный обмен | RabbitMQ, aio-pika |
| Внутренние задания | Redis Streams: транскрипция и Telegram-уведомления |
| Realtime | Redis Pub/Sub, WebSocket, REST-синхронизация |
| Хранение | Три PostgreSQL, приватный MinIO bucket для аудио |
| Frontend | React, TypeScript, Vite, Material UI, TanStack Query, React Router |
| Инструменты | uv/uv.lock, Node 24 LTS/npm/package-lock, Ruff, mypy, ESLint, Prettier |
| Проверки | pytest, HTTPX, Vitest, React Testing Library, Playwright — сценарии добавляются с функциональностью |
| Контейнеры | Docker Compose, отдельные образы сервисов, nginx для SPA/API/WS |

## Схема взаимодействия компонентов

Схема описывает целевое поведение. Каждый сервис пишет только в свою БД.
Текст пропускает Whisper; вход и намерение обработки сохраняются до ответа бота.

```mermaid
flowchart TD
    User[Пользователь в Telegram] -->|Текст или голос .ogg| Bot[Telegram Bot · Automation]
    Bot -->|Проверка допуска| Identity[Identity API]
    Identity --> IDB[(Identity PostgreSQL)]
    Bot --> ADB[(Automation PostgreSQL + outbox)]
    ADB -->|После commit: подтверждение приёма| User

    ADB -->|Голос: задание| STT[Redis Streams · transcription]
    STT --> Worker[Whisper worker · Automation]
    Worker -->|Скачать аудио| TG[Telegram File API]
    Worker --> S3[(MinIO · приватное аудио)]
    Worker --> Whisper[Локальный OpenAI Whisper]
    Whisper -->|Транскрипция| ADB

    ADB -->|Текст или транскрипция: CreateTask| Rabbit[RabbitMQ]
    Rabbit --> Tasks[Tasks worker]
    Tasks --> TDB[(Tasks PostgreSQL + inbox/outbox)]
    TDB -->|TaskCreated| Rabbit
    Rabbit --> Result[Automation · обработчик результата]
    Result --> ADB
    ADB --> Notify[Redis Streams · notifications]
    Notify --> Sender[Telegram notification worker]
    Sender -->|Результат обработки| User

    TDB --> Events[Redis Pub/Sub · botflow.board.changed]
    Events --> API[Tasks API · WebSocket]
    API --> Dashboard[Frontend Dashboard · React]
    Dashboard -->|GET данных / PATCH статуса + версия| API
    API --> TDB
```

RabbitMQ отвечает за команды/результаты между сервисами, Redis Streams —
за внутренние задания Automation. Pub/Sub передаёт сигнал перечитать REST,
а не надёжный журнал: после reconnect и пропущенных сигналов панель
синхронизируется заново. Временный FSM бота не является источником допуска
или состояния принятого задания.

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

Identity и Tasks имеют такую же структуру, собственные миграции и workers.
Слои лежат непосредственно в `src/`; у каждого процесса свой корень импортов.
Правила запуска и изоляции описаны в [локальной инфраструктуре](docs/local-infrastructure.md).
Бизнес-модели не разделяются через общую библиотеку и не читают чужие БД.

## Начало работы

Нужны Docker с Compose, Python для служебных скриптов; для локальных проверок —
uv и Node 24 LTS/npm. Секреты не входят в репозиторий.

```bash
cp .env.example .env
make init            # создаёт .secrets, существующие значения не перезаписывает
make compose-check   # проверка конфигурации без печати секретов
make build           # сборка стартовых образов
make infra           # PostgreSQL, Redis, RabbitMQ, MinIO
make run             # только HTTP-оболочки и SPA на localhost:8080, без bot и workers
make demo            # полный стенд: допуск, миграции, admin, bucket, API, bot, workers, SPA
make down            # остановка всех сервисов, включая bot и workers, с сохранением томов
make smoke           # собственные PG/RabbitMQ/Redis/MinIO, DI/S3, down/up; очистка тестовых томов
make job-status JOB=<uuid>   # безопасное состояние одного задания: статус, попытки, код ошибки
make job-replay JOB=<uuid>   # повтор упавшего задания с прежним id; лимит проверяется заново
```

Задание, упавшее после трёх попыток или с постоянной ошибкой, оператор смотрит и повторяет
этими командами (повтор не создаёт вторую задачу). Удаление томов, backup/restore, сверка
после потери Redis и автоматический retention в готовность первой версии не входят;
подробности — `docs/architecture.md` («Ошибка задания и replay») и
`docs/local-infrastructure.md`.

MinIO собирается из официального исходника с закреплённым SHA, поскольку
старый тег контейнера из архитектурного референса недоступен. Сборка требует
сети и отдельно проверяется перед рабочим запуском.

Whisper проверяется отдельно от приложения (#4): `make whisper-build` (образ
Python 3.12 + openai-whisper + torch CPU + ffmpeg), `make whisper-prepare`
(единственный шаг с сетью, веса в том `botflow_whisper_models`, SHA-256; `turbo` ≈1.5 GiB),
`make whisper-verify`, `make whisper-corpus-check`, `make whisper-bench` (без сети;
корпус из `.local/whisper/`, результат `.local/whisper/results.json`). Корпус и результаты
не коммитятся. Установка и загрузка выполняются только явно. По замерам на шести записях выбрана
`turbo` (CPU, fp32); качество на hy низкое, а STT на длинной hy-записи около 58 с — подтверждения
владельца нет. Подробности и ограничения:
`backend/services/automation-service/docs/whisper-validation.md`.

`make deps` явно устанавливает зависимости из lockfiles. `make check` и
`make test` проверяют текущий backend/frontend и служебные скрипты; пустой набор
pytest/Vitest не считается успешным. `make build` собирает образы.
`make smoke` поднимает реальные ресурсы в уникальном Compose-проекте, проверяет
DI, независимые DB-сессии с rollback, RabbitMQ/Redis, приватный S3 roundtrip и
сохранность данных после down/up. Тестовые credentials и тома отделены от `.secrets`
и локального стенда; удаляются только ресурсы этого тестового проекта.

`make e2e` запускает тот же изолированный smoke и Playwright на обычном и узком
экране. Вход проходит через настоящий Identity API/nginx и PostgreSQL: код
получает реальный обработчик `/login`, отправку сообщения перехватывает тестовый
Telegram-адаптер. Проверяются восстановление сессии, RU/HY, logout, повтор кода,
истечение и отзыв сессии. Admin-сценарии проходят через настоящие `/join`,
списки и решения API: отказ, повторная заявка, одобрение, блокировка с отзывом
доступа, разблокировка и конфликт конкурентного решения. Проверяется серверный
запрет admin API для обычного пользователя. Realtime-сценарии создают задачу через
настоящий RabbitMQ и tasks-worker, проверяют появление и смену статуса на открытой
доске через Redis/WS без reload. После разрыва WS изменения восстанавливаются
перечитыванием REST при reconnect; Pub/Sub не хранит журнал пропущенных событий.
Голосовая задача (source_type=voice) доходит до доски через тот же настоящий
RabbitMQ/tasks-worker; её маркер «Голос»/«Ձայն» проверяется после смены языка и reload.
Путь голоса от Telegram-файла до MinIO и распознавания проверяет backend-часть
`make smoke`/`make e2e` на подмене Bot API и STT.
Пустой или частично пропущенный набор браузерных проверок (`expected = 0`, `skipped > 0`,
`test.only`) считается провалом, а не успехом.
Chromium готовится явно:
`npm --prefix frontend exec -- playwright install chromium` (в Ubuntu CI добавляется
`--with-deps`). Проверки не используют живой Telegram long polling и настоящий Whisper:
внешние порты подменены, публичного test-login нет.

Веб-вход доступен через Compose ingress: отправьте боту `/login` в личном чате,
вставьте 32-символьный код на `/login`. Он одноразовый и действует 5 минут.
`/` требует активной сессии; кнопка «Выйти» отзывает её. Сессия проверяется после
загрузки, при возвращении на вкладку/восстановлении соединения и каждые 30 секунд
активной вкладки. RU/HY переключается без повторного входа; localStorage хранит
только язык. При недоступном Identity защищённая оболочка скрыта и доступен повтор.

Администратору доступен `/admin` по ссылке «Управление доступом»: ожидающие
и отклонённые заявки, допущенные и заблокированные участники, страницы по 20
записей. Одобрение, отказ, блокировка и разблокировка передают показанную
`access_version`; после ответа список перечитывается. Действия не повторяются
автоматически. При ошибке перечитывания старые действия скрыты до успешного
обновления списка. Интерфейс и ошибки доступны на RU/HY; право admin проверяет
сервер при каждом запросе.

Оболочку можно проверить отдельно от API и Docker:
`npm --prefix frontend run test:ui` запускает собственный Vite и Playwright
на широком экране и ширине 320 px. На HTTP-границе подменяются ответы Identity:
проверяются состояния входа и admin-экрана, ошибки RU/HY, отзыв сессии,
конфликт решения, 503 и потеря ответа без повторной отправки действия,
постраничные списки, повтор после 503,
локальные шрифты, отсутствие переполнения и клавиатурный focus.
Для разработки только интерфейса: `npm --prefix frontend run dev`; настоящий вход
требует единого origin SPA/API через Compose ingress.

GitHub Actions (workflow `CI`, job `check-build-smoke`) выполняет установку по lockfiles,
`make check`, `make test`, `make build` и изолированный smoke с браузерной проверкой через
`make e2e`. Required check для защиты веток — `CI / check-build-smoke`. HA, CD и disaster
recovery не добавляются.
Подробности lifecycle, изоляции и диагностики: [локальная инфраструктура](docs/local-infrastructure.md).

## Демонстрационный запуск (#16)

`make demo` готовит и запускает полный стенд на данных обычного проекта `botflow`
(тома `botflow_*`); тестовые данные `make e2e` и `make smoke` его не затрагивают.

Что нужно один раз:

```bash
cp .env.example .env            # задайте INITIAL_ADMIN_TELEGRAM_ID — числовой Telegram ID первого admin
make init                       # .secrets; существующие значения не перезаписываются
# запишите токен бота из BotFather в .secrets/telegram_bot_token (файл не коммитится)
make whisper-prepare            # единственный шаг с сетью: веса модели в том botflow_whisper_models
```

Модель `turbo` занимает около 1.5 GiB; `WHISPER_MODEL` в `.env` выбирает другую.
Затем `make demo`: проверка допуска (`scripts/demo_preflight.py`), сборка образов, включая Whisper
(при проблемах DNS сборки — `BOTFLOW_BUILD_NETWORK=host make demo`), миграции трёх БД,
приватный bucket MinIO, первоначальный admin, API/SPA, telegram-bot и workers.
Топологию RabbitMQ идемпотентно объявляют сами tasks/automation-процессы при старте.
SPA открывается на `http://localhost:8080`; `/login` боту выдаёт код входа.

- Повтор `make demo` не сбрасывает данные и не возвращает права отозванному admin.
- `make down` останавливает контейнеры и сохраняет тома; следующий `make demo`
  возвращает пользователей и задачи. Тома удаляются только вручную и в `make demo`/`make down` не входят.
- Ошибка допуска печатает все проблемы сразу: пустой `INITIAL_ADMIN_TELEGRAM_ID`,
  пустой `telegram_bot_token`, нет тома Whisper. Значения секретов не выводятся.
- `whisper-worker` готов, когда вес проверен и модель загружена (до 2 минут и более на холодном CPU).
  Задержка голоса и качество hy ограничены: см. `backend/services/automation-service/docs/whisper-validation.md`.
- Ограничения: один узел, без HA, backup/restore и автоматического retention.
  Живой бот проверяется вручную в #17, E2E на настоящий Telegram и Whisper не опирается.

Архитектурные детали и принятые компромиссы: [docs/architecture.md](docs/architecture.md),
[ADR](docs/adr/).
