# Локальная инфраструктура и проверки #2

Общие технические адаптеры находятся в `botflow_platform.resources`. У каждого
сервиса свой `Container`: при старте HTTP-процесса открываются его PostgreSQL и
RabbitMQ, у Tasks/Automation также Redis, у Automation — S3-клиент. FastAPI
lifespan и `providers.Resource` закрывают их при остановке. `AsyncExitStack`
освобождает уже открытые подключения при ошибке следующего ресурса.

`resources.sessions()` создаёт независимую SQLAlchemy AsyncSession на операцию.
Commit должен быть явным; закрытие сессии откатывает незакоммиченные изменения.
Общих бизнес-моделей или чтения чужой БД библиотека не вводит.

`/health/live` сообщает только о процессе. `/health/ready` остаётся 503:
работающие подключения не означают готовность бизнес-обработчиков. Bootstrap,
миграции бизнес-данных, Telegram и STT не заменены успешными заглушками.

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

GitHub Actions запускает проверки и сборку существующего каркаса. Успешный
инфраструктурный smoke не означает готовность пользовательского сценария;
бизнес-тесты добавляются в последующих задачах.
