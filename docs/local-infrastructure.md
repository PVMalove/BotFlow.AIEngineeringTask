# Локальная инфраструктура и проверки #2

Общие технические адаптеры находятся в `kernel_platform.resources`. У каждого
сервиса свой `src/api/containers.py` с `Container`: при старте HTTP-процесса
открываются его PostgreSQL и RabbitMQ, у Tasks/Automation также Redis, у Automation — S3-клиент. FastAPI
lifespan и `providers.Resource` закрывают их при остановке. `AsyncExitStack`
освобождает уже открытые подключения при ошибке следующего ресурса.

`resources.sessions()` создаёт независимую SQLAlchemy AsyncSession на операцию.
Commit должен быть явным; закрытие сессии откатывает незакоммиченные изменения.
Общих бизнес-моделей или чтения чужой БД библиотека не вводит.

`/health/live` сообщает только о процессе. `/health/ready` остаётся 503:
работающие подключения не означают готовность всего приложения. Миграции Identity
и Automation, первый admin и Telegram-вход реализованы; обработчики задач и
фоновые задания ещё отсутствуют.

## Структура сервисов и импорты

Каждый `backend/services/<service>/src/` непосредственно содержит `domain/`,
`application/`, `infrastructure/`, `api/`, `contracts/`, `core/`, `common/`.
`api/containers.py` собирает зависимости; `contracts/` предназначен для View
на основе frozen dataclasses без зависимости от FastAPI и ORM. Доменные
репозитории и Unit of Work объявляются в `domain/`, реализации — в
`infrastructure/`. Сущности и порты будут заполнены бизнес-задачами.

В корне каждого сервиса находятся `tests/{unit,integration,e2e,performance}/`,
`k8s/`, `docs/`, `ci/`, `scripts/`. Процессы bootstrap, worker и Telegram
bot находятся в слое `api` (`python -m api.bootstrap`, `api.worker`, `api.bot`); bootstrap всех трёх сервисов, бот (login, заявки и
текст), `identity-worker`, `tasks-worker` и `automation-worker` работают,
остальные точки входа явно сообщают об отсутствии реализации. Каталоги для будущих артефактов не означают готовность Kubernetes,
нагрузочных тестов или бизнес-функций.

Сервисы — virtual projects uv (`package = false`): workspace устанавливает их
зависимости, но не смешивает одноимённые пакеты `api`, `domain` и другие слои в
`site-packages`. Проверки каждого сервиса выполняются отдельным процессом из
его каталога; pytest и mypy получают только его `src`. Общие библиотеки
(`kernel_domain`, `kernel_platform`, `observability`, `test_support`) устанавливаются
в workspace как обычные пакеты.

Docker-образ копирует только исходники выбранного сервиса в `/srv/src`, задаёт
`PYTHONPATH=/srv/src` и запускает `uvicorn api.main:app`. Smoke использует
composition root своего Automation-образа и общий ресурсный адаптер для трёх
БД; чужие исходники сервисов в процесс не подключаются. Запущенные API каждого
сервиса проверяются по HTTP.

Для локального HTTP-процесса после `make deps` (инфраструктура и secret-файлы
должны быть доступны по параметрам `BOTFLOW_*`):

```bash
cd backend/services/identity-service
PYTHONPATH=src uv run --locked --no-sync uvicorn api.main:app --port 8000
```

## Запуск

Нужны Docker Compose с поддержкой `!reset` (2.24+), uv, Python 3.12 и Node 24.
`make deps` устанавливает backend и frontend из lockfiles. `make init` создаёт
credentials один раз; `BOTFLOW_SECRETS_DIR` позволяет задать отдельный каталог.
Каталог имеет права 0700, secret-файлы читаются непривилегированными контейнерами.
Существующие значения и Redis ACL при повторном запуске сохраняются.

```bash
make deps
make init
make check
make test
make build
make smoke
npm --prefix frontend exec -- playwright install chromium
make e2e
```

Обычный Compose сохраняет тома при `make down`. Наружу публикуется только
frontend ingress на `127.0.0.1`; PostgreSQL, RabbitMQ, Redis и MinIO доступны
только внутри Compose. Bot token для этих проверок не требуется.

## Изоляция smoke и браузера

`scripts/integration-smoke.py` создаёт UUID проекта, image tag и временный
каталог секретов; `.env` локального стенда и его `COMPOSE_PROFILES` не используются. `docker-compose.smoke.yml`
убирает опубликованные порты. Браузерная проверка добавляет единственный временный
loopback-порт, который назначает Docker; адрес передаётся Playwright явно.
Прямой запуск Playwright без подготовленного адреса запрещён конфигурацией.

Проверяются настоящие ресурсы:

- Три БД: разные серверные соединения, изоляция незакоммиченных данных,
  rollback при закрытии сессии и сохранение committed-данных после down/up.
- RabbitMQ: persistent-сообщение в durable-очереди переживает down/up.
- Redis: аутентифицированный roundtrip и AOF-сохранность после down/up.
- MinIO: запись/чтение/удаление объекта, 403 при анонимном чтении приватного
  объекта и сохранность другого объекта после down/up.
- Закрытие DI-ресурсов; HTTP liveness, отсутствие бизнес-readiness и доступность SPA.
- При `make e2e`: Playwright Chromium для обычного и узкого экранов и запрет
  доступа к `/internal/` через ingress.

В `finally` удаляются только контейнеры, сеть, тома и image tags собственного
UUID-проекта. Эти удаления не относятся к локальным данным обычного стенда.
Ошибки SDK и значения тестовых объектов не печатаются; вывод содержит стадию
сбоя, а для сборок без secret-монтов — ограниченную диагностику build.

## Сеть сборки

По умолчанию используется стандартная сеть Docker. Если DNS build-контейнера
не разрешает registry модулей Go, можно явно выбрать сеть хоста:

```bash
BOTFLOW_BUILD_NETWORK=host make build
BOTFLOW_BUILD_NETWORK=host make smoke
BOTFLOW_BUILD_NETWORK=host make e2e
```

Smoke получает bake-план через Compose и явно передаёт BuildKit
`--allow=network.host`. Это локальная настройка сборки; pinned-образы, исходники,
runtime-сеть и внешние порты приложения не меняются. CI использует стандартную
сеть. MinIO собирается из закреплённого официального commit, Go-модули проверяются
по upstream `go.sum`.

GitHub Actions запускает проверки и сборку существующего приложения. Smoke
подтверждает инфраструктуру и сценарий входа; управление задачами и фоновые
задания добавляются в следующих задачах.


## Первый admin и Telegram-вход (#5)

После `make init` укажите свой числовой Telegram ID в `.env` как
`INITIAL_ADMIN_TELEGRAM_ID`. Токен BotFather запишите непосредственно в локальный
secret-файл `.secrets/telegram_bot_token`, не в `.env`, историю shell или Git.
`make init` создаёт RSA-ключ `identity_jwt_private_key` через openssl без вывода
и не перезаписывает уже существующие секреты. Не меняйте ключ после появления
сессий без запланированной ротации: старые JWT перестанут проверяться.

```bash
docker compose up -d --wait identity-db automation-db rabbitmq
docker compose run --rm --no-deps identity-bootstrap
docker compose run --rm --no-deps automation-bootstrap
make bootstrap-admin
docker compose up -d --wait identity-api
docker compose --profile bot up -d telegram-bot
```

`make setup` применяет миграции всех трёх сервисов (Tasks bootstrap теперь рабочий,
см. раздел #9). Для входа используйте команды выше. Повтор миграций и `make bootstrap-admin` безопасен:
существующий участник сохраняет свой статус, в том числе блокировку.
Для браузерного API нужен ingress (`docker compose up -d --wait frontend`).
Отправьте боту `/start`, затем `/login` в личном чате. Полученный код можно один
раз передать JSON-телом `{"code":"..."}` в `POST /api/v1/auth/exchange` через
тот же ingress с его Origin. UI ввода кода будет добавлен отдельной frontend-задачей.
`GET /api/v1/users/me` проверяет cookie, `POST /api/v1/auth/logout` отзывает вход.

`make smoke` и `make e2e` создают собственные секреты и первого admin, применяют
миграции, проверяют полный login-путь и повторяют bootstrap после рестарта.
Telegram подменяется только на порту отправки: реальны бот-сценарий, внутренний
HTTP-клиент, Identity, nginx, SQL и транзакции PostgreSQL. Проверяются hash/TTL,
отмена старого кода, повтор/гонка обмена и выдачи, лимиты, Origin, cookie и logout.
Приватный RSA-ключ отсутствует в Automation/smoke; ключ Automation не открывает
внутренние маршруты через ingress. Тесты не требуют настоящего Telegram token.
Живой long polling с токеном владельца остаётся отдельной ручной приёмкой.

## Заявки и воркеры уведомлений (#7)

Воркеры лежат в профиле `workers` и не стартуют по умолчанию. `identity-worker`
(секреты БД и RabbitMQ) переносит outbox решений в RabbitMQ; `automation-worker`
(БД, RabbitMQ, Redis, MinIO, ключ Automation и токен бота) принимает события,
создаёт notification intents и доставляет их через Redis Stream в Telegram.
Для рассылки решений нужны оба воркера, `identity-api` и миграции обеих БД:

```bash
docker compose --profile workers up -d identity-worker automation-worker
```

Администратор решает заявки через `identity-api` (`/api/v1/admin/`). В frontend
ingress этого маршрута пока нет, поэтому до среза админ-панели API доступен
только внутри сети Compose. `make smoke` запускает `identity-worker` для проверок
доступа (`AccessFlow`); `automation-worker` там не стартует, потому что требует
токен бота: его шаги работают внутри процесса проверки с тестовым Messenger-портом.

## Текстовый путь и Tasks (#9)

`tasks-bootstrap` применяет миграции Tasks (`python -m api.bootstrap migrate`).
`tasks-worker` (БД, RabbitMQ, Redis) принимает CreateTask и переносит TaskCreated в
RabbitMQ; `automation-worker` дополнительно переносит CreateTask из outbox Automation,
принимает результат и создаёт уведомление. `tasks-api` обращается к `identity-api`
(`BOTFLOW_IDENTITY_URL`) для проверки сессии, поэтому зависит от него:

```bash
docker compose --profile bootstrap run --rm tasks-bootstrap
docker compose --profile workers up -d identity-worker tasks-worker automation-worker
docker compose --profile workers exec automation-worker python -m api.worker replay <job_id>
```

Список и карточки доступны через ingress: `GET /api/v1/tasks`, `GET /api/v1/tasks/{id}`
(cookie `botflow_session` из `/login`). `make smoke` запускает `tasks-worker` для
класса `TextTaskFlow`; Automation-шаги и тестовый Messenger работают внутри процесса
проверки. Сбой брокера и Telegram, который нельзя вызвать из контейнера проверки,
подменяется на портах publisher и Messenger поверх настоящих строк PostgreSQL.
Живую задержку ACK и работу с токеном владельца смотрите по
`jobs.received_at`/`acked_at` (запрос в `docs/architecture.md`).

## Голос и STT-воркер (#14)

`automation-bootstrap` теперь применяет миграцию `0004` и идемпотентно создаёт
приватный bucket `botflow-audio` (нужны `minio_password` и работающий `minio`; `make setup`
поднимает его раньше). `automation-worker` дополнительно переносит голосовые задания из
outbox в Redis Stream `botflow:automation:voice`. Распознаёт их отдельный `whisper-worker`
(образ `botflow-whisper-worker`, цель `stt-runtime`, профиль `workers`): `make build` его не
собирает, веса — постоянный том `whisper_models`, смонтированный только на чтение.

```bash
make whisper-build                       # образ stt-runtime (нужна сеть)
make whisper-prepare WHISPER_MODEL=turbo # веса в том (единственный шаг с сетью)
docker compose build whisper-worker
docker compose --profile workers up -d automation-worker whisper-worker
docker compose --profile workers exec automation-worker python -m api.worker replay <job_id>
make voice-latency ARGS="--last 20"      # задержка update -> задача по данным Automation
```

Готовность `whisper-worker` — файл `/tmp/whisper-ready`, он создаётся после проверки
SHA-256 весов и загрузки модели; до этого healthcheck не проходит (`start_period` 2 мин).
Без подготовленных весов процесс завершается с понятной ошибкой и перезапускается.
Переменные: `WHISPER_MODEL` (модель, по умолчанию `turbo`), `BOTFLOW_STT_TIMEOUT_SECONDS`
(по умолчанию 120). После таймаута распознавания воркер сам завершается и перезапускается
(`restart: unless-stopped`) — так задумано. `make smoke` проверяет весь путь на настоящих
PostgreSQL/Redis/RabbitMQ/MinIO без Whisper и без Telegram (`VoiceTaskFlow`); качество
RU/hy и живой Telegram проверяются отдельно (#17).


## Тесты на PostgreSQL

Интеграционные тесты tasks-service с реальной БД (`tests/integration`) пропускаются, пока не
задан `BOTFLOW_TEST_DATABASE_URL` — URL одноразовой БД, например
`postgresql+asyncpg://user:password@localhost:5432/test`. Тесты создают и очищают свои таблицы,
поэтому указывать рабочую БД нельзя. Без переменной `make test` их не запускает; полный путь
через PostgreSQL, RabbitMQ, Redis и MinIO проверяют `make smoke` и `make e2e`.

## Тесты на Redis

`tests/integration/test_board_realtime.py` проверяет сигнал `board.changed` и WS `/ws/board`
на настоящем Redis и пропускается, пока не задан `BOTFLOW_TEST_REDIS_URL` — URL одноразового
Redis без ACL, например `redis://localhost:6379/0`. Тесты только публикуют и подписываются на
канал `botflow.board.changed`, но указывать рабочий Redis нельзя. Compose-Redis закрыт ACL с
паролем из `.secrets` и для этого не подходит. Одноразовый Redis:

```bash
docker run -d --rm --name botflow-test-redis -p 127.0.0.1:6379:6379 redis:8-alpine
BOTFLOW_TEST_REDIS_URL=redis://127.0.0.1:6379/0 make backend-test
docker stop botflow-test-redis
```

Без переменной `make test` эти тесты пропускает (skip, не ошибка). Pub/Sub не журнал:
тесты не требуют воспроизведения пропущенного сигнала.
