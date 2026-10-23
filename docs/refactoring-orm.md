# Рефакторинг BotFlow: ORM, Unit of Work и состояние доски

Задача: [#40](https://github.com/PVMalove/BotFlow.AIEngineeringTask/issues/40). База: `integration/botflow`, исходный коммит `1cf3a14`. Рабочая ветка: `feature/issue-40-orm-refactor`.

## Результат аудита

До изменений три сервиса использовали SQLAlchemy Core Table/Row. Операторский статус задания читался текстовым SQL с тремя вручную типизированными UUID-параметрами. В Identity файл Unit of Work также содержал классы репозиториев и rate limiter. Логика доски, сохранения, конфликтов и DnD находилась в UI-компоненте.

В проекте уже существуют общий `Result[T]` с фиксированным типом `Error`, общий HTTP-маппинг `{data, meta}` / `{error}` и базовый SQLAlchemy Unit of Work. Ожидаемые ошибки имеют коды snake_case. Query-хэндлеры не записывают данные и не вызывают commit. Эти механизмы сохранены; технические исключения инфраструктуры продолжают обрабатываться на существующих границах.

Фактический стек зафиксирован lockfiles: SQLAlchemy 2.1.5, React 19 и MUI. Зависимости и UI-библиотека не менялись.

## Слой данных: до и после

| Область | До | После |
| --- | --- | --- |
| Схема сервисов | Core `Table` и нетипизированные `Row` | Сервисные `Base(DeclarativeBase)`, `Mapped`, `mapped_column`; прежние Table-имена ссылаются на ту же ORM metadata |
| Задание / голос | Ручной outer join и Row-маппинг | `JobModel.voice` / `VoiceJobModel.job`, явная связь 1:1, загрузка через joinedload, явное восстановление чистого домена |
| Статус задания | Текстовый SQL и `bindparam(type_=Uuid)` | ORM-проекция только разрешённых полей; UUID получают тип от mapped-колонок |
| Репозитории | Core-выборки и ручные Row[Any] | `session.get`, `session.scalars`, `session.add` и ORM-enabled DML |
| Защита от гонок | ON CONFLICT, UPDATE с версией, advisory lock, SKIP LOCKED | Сохранены атомарные операции и прежние ключи блокировок |
| Inbox / outbox | Дублирование inbox и фабрика Core-схемы outbox | Общие InboxColumns / SqlInboxRepository и OutboxColumns для Tasks/Automation |

Доменные сущности не получают SQLAlchemy-зависимостей. Репозитории по-прежнему явно преобразуют модели в доменные сущности. В операторский запрос не попадают текст сообщений, имя автора, Telegram file_id, S3-ключ или payload outbox. Атомарные INSERT/UPDATE используют SQLAlchemy expressions; общий relay работает с канонической metadata моделей.

## Проверка Unit of Work всех сервисов

- **Automation:** подключает jobs, intents и inbox к одной AsyncSession; методы хранения находятся в репозиториях.
- **Tasks:** подключает tasks и inbox к одной AsyncSession; проверка версии остаётся в атомарном UPDATE репозитория.
- **Identity:** репозитории участников, login codes и сессий, а также rate limiter вынесены из unit_of_work.py в отдельные адаптеры. Сам UoW подключает репозитории и staging outbox. Rate limiter сохраняет прежнюю самостоятельную транзакцию: учёт попыток должен переживать rollback бизнес-операции.
- **Общий UoW:** rollback по умолчанию, закрытие сессии при выходе, staging outbox до commit, вызов обработчиков после успешного commit. Вместо Entity[Any] используется минимальный EventSource-протокол.

Тесты проверяют общую сессию репозиториев каждого сервиса, rollback при исключении, сбой staging и commit, порядок событий и сохранность commit при сбое обработчика.

## Frontend и типизация

Состояние доски, DnD, операции сохранения и восстановления после 409/сети вынесены в `useBoard`; lifecycle realtime — в `useBoardRealtime`. Сохранены session-scoped блокировка операции, защита от устаревших ответов, пагинация и прежние тексты ошибок.

Member/Task типы уточнены по backend DTO (access_version, nullable display_name/source_type); дублирование проверки отказа в доступе убрано через общий предикат. Оставшиеся подписи приложения, Telegram и выбора языка вынесены в оба словаря RU/HY.

Удалены Row[Any]-мапперы, дублированные inbox-адаптеры и заменённая фабрика outbox_table. Типы доменных идентификаторов и payload событий уточнены; убраны подавления типов в Result и буфере загрузки Telegram. Существующий стек и бизнес-правила сохранены.

## Верификация

Исходные `make check` и `make test` прошли до правок. После изолированных шагов выполнялись backend/frontend проверки.

- `make check`: PASS, включая строгий mypy, Ruff, ESLint, TypeScript и форматирование.
- `make test`: PASS — 839 backend-тестов, 36 тестов скриптов, 18 frontend-тестов; 18 отдельных интеграционных тестов пропущены без их opt-in переменных окружения.
- `make build`: PASS.
- `alembic check`: PASS для identity-service, tasks-service и automation-service в свежих БД после миграций; шаг включён в smoke перед запуском workers.
- Сравнение PostgreSQL DDL исходной и новой metadata: идентичны таблицы, индексы и ограничения всех трёх сервисов. Файлы миграций не изменялись; новых ревизий нет.
- `BOTFLOW_BUILD_NETWORK=host make smoke`: PASS с доступным Docker Desktop временным каталогом.
- `make e2e`: PASS — 38 браузерных тестов, 0 падений, 0 пропусков; обычный и узкий экран, реальные сервисы, повторные миграции и перезапуск. Полный запуск занял 846 с, включая подготовку и очистку Compose-проекта.

Для Docker Desktop использован `TMPDIR="$PWD/.local/tmp"`: daemon не видит созданные runner-процессом файлы в `/tmp`, а bind-mount из рабочей папки доступен. Это подтверждено отдельной проверкой безвредного файла; временные тестовые проекты используют собственные секреты и тома. При воспроизведении создать каталог и передать TMPDIR также для `make e2e`.

## Ограничения

- Smoke подменяет Telegram Bot API и распознавание речи на существующих портах. Реальный Telegram, настоящие аудиозаписи и повторный Whisper benchmark этим рефакторингом не проверялись.
- Any и отдельные type: ignore остаются на существующих динамических границах внешних SDK/Whisper и в части тестовых doubles. Проверка mypy проходит; механическая замена их на небезопасные cast не выполнялась.
- Pytest сообщает два существующих StarletteDeprecationWarning о TestClient/httpx. Миграция тестового HTTP-клиента требует отдельного изменения зависимостей.
- Проверки подтверждают покрытые сценарии; они не являются доказательством отсутствия любых возможных регрессий или нагрузочным benchmark.

## Изменённые файлы

### Общие библиотеки

- [backend/libs/kernel-domain/src/kernel_domain/domain_event.py](../backend/libs/kernel-domain/src/kernel_domain/domain_event.py)
- [backend/libs/kernel-domain/src/kernel_domain/result.py](../backend/libs/kernel-domain/src/kernel_domain/result.py)
- [backend/libs/kernel-domain/src/kernel_domain/value_object.py](../backend/libs/kernel-domain/src/kernel_domain/value_object.py)
- [backend/libs/kernel-platform/src/kernel_platform/inbox.py](../backend/libs/kernel-platform/src/kernel_platform/inbox.py)
- [backend/libs/kernel-platform/src/kernel_platform/migrations.py](../backend/libs/kernel-platform/src/kernel_platform/migrations.py)
- [backend/libs/kernel-platform/src/kernel_platform/outbox.py](../backend/libs/kernel-platform/src/kernel_platform/outbox.py)
- [backend/libs/kernel-platform/src/kernel_platform/resources.py](../backend/libs/kernel-platform/src/kernel_platform/resources.py)
- [backend/libs/kernel-platform/src/kernel_platform/sqlalchemy_unit_of_work.py](../backend/libs/kernel-platform/src/kernel_platform/sqlalchemy_unit_of_work.py)
- [backend/libs/kernel-platform/tests/test_outbox_routes.py](../backend/libs/kernel-platform/tests/test_outbox_routes.py)
- [backend/libs/kernel-platform/tests/unit/test_sqlalchemy_unit_of_work.py](../backend/libs/kernel-platform/tests/unit/test_sqlalchemy_unit_of_work.py)

### Automation

- [backend/services/automation-service/src/domain/events/job_domain_event.py](../backend/services/automation-service/src/domain/events/job_domain_event.py)
- [backend/services/automation-service/src/domain/value_objects/intent_id.py](../backend/services/automation-service/src/domain/value_objects/intent_id.py)
- [backend/services/automation-service/src/domain/value_objects/job_id.py](../backend/services/automation-service/src/domain/value_objects/job_id.py)
- [backend/services/automation-service/src/infrastructure/db/inbox.py](../backend/services/automation-service/src/infrastructure/db/inbox.py)
- [backend/services/automation-service/src/infrastructure/db/intent_repository.py](../backend/services/automation-service/src/infrastructure/db/intent_repository.py)
- [backend/services/automation-service/src/infrastructure/db/job_repository.py](../backend/services/automation-service/src/infrastructure/db/job_repository.py)
- [backend/services/automation-service/src/infrastructure/db/job_status_reader.py](../backend/services/automation-service/src/infrastructure/db/job_status_reader.py)
- [backend/services/automation-service/src/infrastructure/db/models.py](../backend/services/automation-service/src/infrastructure/db/models.py)
- [backend/services/automation-service/src/infrastructure/db/tables.py](../backend/services/automation-service/src/infrastructure/db/tables.py)
- [backend/services/automation-service/src/infrastructure/db/unit_of_work.py](../backend/services/automation-service/src/infrastructure/db/unit_of_work.py)
- [backend/services/automation-service/src/infrastructure/telegram_files.py](../backend/services/automation-service/src/infrastructure/telegram_files.py)
- [backend/services/automation-service/tests/unit/test_job_status.py](../backend/services/automation-service/tests/unit/test_job_status.py)
- [backend/services/automation-service/tests/unit/test_orm_models.py](../backend/services/automation-service/tests/unit/test_orm_models.py)

### Identity

- [backend/services/identity-service/src/api/containers.py](../backend/services/identity-service/src/api/containers.py)
- [backend/services/identity-service/src/domain/events/member_domain_event.py](../backend/services/identity-service/src/domain/events/member_domain_event.py)
- [backend/services/identity-service/src/domain/outbox.py](../backend/services/identity-service/src/domain/outbox.py)
- [backend/services/identity-service/src/domain/value_objects/member_id.py](../backend/services/identity-service/src/domain/value_objects/member_id.py)
- [backend/services/identity-service/src/infrastructure/db/login_code_repository.py](../backend/services/identity-service/src/infrastructure/db/login_code_repository.py)
- [backend/services/identity-service/src/infrastructure/db/member_repository.py](../backend/services/identity-service/src/infrastructure/db/member_repository.py)
- [backend/services/identity-service/src/infrastructure/db/models.py](../backend/services/identity-service/src/infrastructure/db/models.py)
- [backend/services/identity-service/src/infrastructure/db/outbox.py](../backend/services/identity-service/src/infrastructure/db/outbox.py)
- [backend/services/identity-service/src/infrastructure/db/rate_limiter.py](../backend/services/identity-service/src/infrastructure/db/rate_limiter.py)
- [backend/services/identity-service/src/infrastructure/db/session_repository.py](../backend/services/identity-service/src/infrastructure/db/session_repository.py)
- [backend/services/identity-service/src/infrastructure/db/tables.py](../backend/services/identity-service/src/infrastructure/db/tables.py)
- [backend/services/identity-service/src/infrastructure/db/unit_of_work.py](../backend/services/identity-service/src/infrastructure/db/unit_of_work.py)
- [backend/services/identity-service/tests/unit/test_access_decisions.py](../backend/services/identity-service/tests/unit/test_access_decisions.py)
- [backend/services/identity-service/tests/unit/test_orm_models.py](../backend/services/identity-service/tests/unit/test_orm_models.py)

### Tasks

- [backend/services/tasks-service/src/domain/events/task_domain_event.py](../backend/services/tasks-service/src/domain/events/task_domain_event.py)
- [backend/services/tasks-service/src/domain/value_objects/task_id.py](../backend/services/tasks-service/src/domain/value_objects/task_id.py)
- [backend/services/tasks-service/src/infrastructure/db/models.py](../backend/services/tasks-service/src/infrastructure/db/models.py)
- [backend/services/tasks-service/src/infrastructure/db/tables.py](../backend/services/tasks-service/src/infrastructure/db/tables.py)
- [backend/services/tasks-service/src/infrastructure/db/task_reader.py](../backend/services/tasks-service/src/infrastructure/db/task_reader.py)
- [backend/services/tasks-service/src/infrastructure/db/task_repository.py](../backend/services/tasks-service/src/infrastructure/db/task_repository.py)
- [backend/services/tasks-service/src/infrastructure/db/unit_of_work.py](../backend/services/tasks-service/src/infrastructure/db/unit_of_work.py)
- [backend/services/tasks-service/tests/unit/test_orm_models.py](../backend/services/tasks-service/tests/unit/test_orm_models.py)

### Frontend

- [frontend/src/AdminAccess.tsx](../frontend/src/AdminAccess.tsx)
- [frontend/src/App.tsx](../frontend/src/App.tsx)
- [frontend/src/Board.test.tsx](../frontend/src/Board.test.tsx)
- [frontend/src/Board.tsx](../frontend/src/Board.tsx)
- [frontend/src/TaskSource.tsx](../frontend/src/TaskSource.tsx)
- [frontend/src/api.ts](../frontend/src/api.ts)
- [frontend/src/errors.ts](../frontend/src/errors.ts)
- [frontend/src/locale.ts](../frontend/src/locale.ts)
- [frontend/src/tasks.test.ts](../frontend/src/tasks.test.ts)
- [frontend/src/useBoard.ts](../frontend/src/useBoard.ts)
- [frontend/src/useBoardRealtime.ts](../frontend/src/useBoardRealtime.ts)

### Smoke

- [scripts/integration-smoke.py](../scripts/integration-smoke.py)
