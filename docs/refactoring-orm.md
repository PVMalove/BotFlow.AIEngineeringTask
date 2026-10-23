# Рефакторинг BotFlow: ORM, Unit of Work и состояние доски

Задача: [#40](https://github.com/PVMalove/BotFlow.AIEngineeringTask/issues/40). База: `integration/botflow`, исходный коммит `1cf3a14`. Рабочая ветка: `feature/issue-40-orm-refactor`.

## Результат аудита

До изменений три сервиса использовали SQLAlchemy Core Table/Row. Операторский статус задания читался текстовым SQL с тремя вручную типизированными UUID-параметрами. В Identity файл Unit of Work также содержал классы репозиториев и rate limiter. Логика доски, сохранения, конфликтов и DnD находилась в UI-компоненте.

В проекте уже существуют общий `Result[T]` с фиксированным типом `Error`, общий HTTP-маппинг `{data, meta}` / `{error}` и базовый SQLAlchemy Unit of Work. Ожидаемые ошибки имеют коды snake_case. Query-хэндлеры не записывают данные и не вызывают commit. Эти механизмы сохранены; технические исключения инфраструктуры продолжают обрабатываться на существующих границах.

Фактический стек зафиксирован lockfiles: SQLAlchemy 2.1.5, React 19 и MUI. Зависимости и UI-библиотека не менялись.

## Слой данных: до и после

| Область | До | После |
| --- | --- | --- |
| Схема сервисов | Core `Table` и нетипизированные `Row` | Сервисные `Base(DeclarativeBase)`, `Mapped`, `mapped_column`; единственная metadata — `Base.metadata` (alembic `env.py` берёт её из `models.py`); модули `tables.py` удалены |
| Задание / голос | Ручной outer join и Row-маппинг | `JobModel.voice` / `VoiceJobModel.job`, явная связь 1:1, загрузка через joinedload, явное восстановление чистого домена |
| Статус задания | Текстовый SQL и `bindparam(type_=Uuid)` | ORM-проекция только разрешённых полей; UUID получают тип от mapped-колонок |
| Репозитории | Core-выборки и ручные Row[Any] | `session.get`, `session.scalars`, `session.add` и ORM-enabled DML; чтение под блокировкой (`lock=True`, `get` задач) использует `populate_existing`, чтобы не получить устаревшую копию из identity map |
| Защита от гонок | ON CONFLICT, UPDATE с версией, advisory lock, SKIP LOCKED | Сохранены атомарные операции и прежние ключи блокировок |
| Inbox / outbox | Дублирование inbox и фабрика Core-схемы outbox | Общие `InboxColumns` / `SqlInboxRepository` и `OutboxColumns`; `enqueue`, `replay`, `SqlOutboxTx` и `sql_transactions` принимают ORM-класс, а не Core `Table`. Inbox обновлений Telegram — ORM-вставка `TelegramUpdateModel` |

Доменные сущности не получают SQLAlchemy-зависимостей. Репозитории по-прежнему явно преобразуют модели в доменные сущности. В операторский запрос не попадают текст сообщений, имя автора, Telegram file_id, S3-ключ или payload outbox. Атомарные INSERT/UPDATE (`ON CONFLICT`, UPDATE с версией в WHERE, `SKIP LOCKED`) выражены ORM-enabled `insert`/`update` над моделями. `SqlJobRepository.update` записывает изменения через атрибуты ORM-строки; все вызывающие берут `get(lock=True)`, поэтому строка уже загружена под блокировкой и дополнительного SELECT нет, а запись не атомарна сама по себе, а опирается на эту блокировку.

## Проверка Unit of Work всех сервисов

- **Automation:** подключает jobs, intents и inbox к одной AsyncSession; методы хранения находятся в репозиториях.
- **Tasks:** подключает tasks и inbox к одной AsyncSession; проверка версии остаётся в атомарном UPDATE репозитория.
- **Identity:** репозитории участников, login codes и сессий, а также rate limiter вынесены из unit_of_work.py в отдельные адаптеры. Сам UoW подключает репозитории и staging outbox. Rate limiter сохраняет прежнюю самостоятельную транзакцию: учёт попыток должен переживать rollback бизнес-операции.
- **Общий UoW:** rollback по умолчанию, закрытие сессии при выходе, staging outbox до commit, вызов обработчиков после успешного commit. Вместо Entity[Any] используется минимальный EventSource-протокол.

Тесты проверяют работу репозиториев каждого сервиса в сессии UoW через публичные методы (без обращения к приватным полям), rollback при исключении, сбой staging и commit, порядок событий и сохранность commit при сбое обработчика.

## Frontend и типизация

Логика доски разделена на хуки: `useBoardColumns` (три курсорные колонки и признак отказа в доступе), `useBoardDrag` (состояние DnD, подавление клика, чистая `dropTarget`), `useBoardRealtime` (жизненный цикл подписки и переподключение), `useBoard` (операции сохранения и восстановления). Сохранены session-scoped блокировка операции, защита от устаревших ответов, пагинация и прежние тексты ошибок.

Ветвление по ответу на неудачную смену статуса (409, сеть/5xx, 403, 404, 401, 429) вынесено в чистую `classifySaveError` (`errors.ts`) и покрыто тестами; предикат `isAccessDenied` применён везде, где проверялись 401/403. Подпись объявления DnD собирается из шаблона словаря (`dragOver: '… {column}'`), а бренд, метки языков и общие эндонимы вынесены в `shared` словарей RU/HY.

TS-типы `Task`, `Member`, `AdminMember` сверены с backend-контрактами (`TaskView`, `MemberView`, `AdminMemberView`); совпадение полей фиксирует backend-тест `test_frontend_contract.py`. Генерация типов из OpenAPI не вводилась: это новая зависимость и отдельное решение.

Удалены Row[Any]-мапперы, дублированные inbox-адаптеры, фабрика outbox_table и модули `tables.py`. Типы доменных идентификаторов и payload событий уточнены; подавление типа в `Result.value` заменено на `cast`, из буфера загрузки Telegram убрано. Существующий стек и бизнес-правила сохранены.

## Структурные проверки

- `test_error_codes.py` — все коды реестров ошибок в snake_case.
- `test_query_purity.py` — модули запросов не вызывают commit и записывающие методы.
- `test_frontend_contract.py` — поля TS-моделей совпадают с публичными backend-представлениями.
- `test_outbox_routes.py` — фильтр routing key проверяется по структуре выражения (оператор и значение), а не по тексту SQL.

## Верификация

Исходные backend `make check` и `make test` прошли до правок; после изолированных шагов проверки повторялись. Итоговый прогон (2026-10-23, после доработок по code-review):

- `make check`: PASS — Ruff, строгий mypy, ESLint, TypeScript, форматирование.
- `make test`: PASS — 847 backend-тестов, 36 тестов скриптов, 33 frontend-теста; 18 интеграционных тестов пропущены без opt-in переменных окружения.
- `alembic check`: PASS для identity-service, tasks-service и automation-service в свежих БД после миграций (шаг «schema drift checks» в smoke). Файлы миграций не изменялись; новых ревизий нет.
- `BOTFLOW_BUILD_NETWORK=host make smoke`: PASS на реальных PostgreSQL, RabbitMQ, Redis и MinIO, включая перезапуск и повторные миграции.
- `make e2e`: PASS — 38 браузерных тестов, 0 падений, 0 пропусков.

Сравнение PostgreSQL DDL исходной и новой metadata проводилось в первом проходе рефакторинга (идентичны таблицы, индексы и ограничения всех трёх сервисов); после доработок модели не менялись, а отсутствие дрейфа подтверждает `alembic check`.

Для Docker Desktop smoke и e2e запускались с `TMPDIR="$PWD/.local/tmp"`: daemon не видит файлы, созданные runner-процессом в `/tmp`. Первый запуск после создания каталога может упасть на старте стенда («bind source path does not exist») — это ожидание синхронизации Docker Desktop, повторный запуск проходит.

## Ограничения

- Smoke подменяет Telegram Bot API и распознавание речи на существующих портах. Реальный Telegram, настоящие аудиозаписи и повторный Whisper benchmark этим рефакторингом не проверялись.
- `Result[T]` по-прежнему параметризован одним типом: ошибка всегда `kernel_domain.errors.Error` (он и есть `DomainError`). Второй параметр не вводился — это переписало бы каждую сигнатуру без изменения поведения.
- `Any` и `type: ignore` остаются только на границах нетипизированных зависимостей и JSON: redis (`streams.py`), aioboto3/botocore (`resources.py`, `s3_audio.py`), Whisper (`whisper_*`), JSON-тела (`envelopes.py`, `identity_gateway.py`, `create_task_consumer.py`), DI-контейнеры (`create_*_app`, `shutdown`) и в ряде тестов. Замена их на `cast` не выполнялась.
- Сырой `sqlalchemy.text` остаётся только в межсервисных проверочных скриптах `backend/tests/integration/smoke.py` и `browser_fixture.py`: они обращаются к нескольким БД и не могут импортировать модели разных сервисов под одним именем `infrastructure.db.models`. В коде сервисов и библиотек (`src`, `scripts`) `text` нет.
- Хрупкие регулярные выражения по тексту SQL заменены структурными проверками в `test_job_status.py` и `test_outbox_routes.py`. Эквивалентность запросов на настоящем PostgreSQL подтверждают smoke и e2e, а не unit-тесты.
- Типы TS не генерируются из OpenAPI: их соответствие контрактам проверяет тест (см. выше).
- Тест хуков React с рендерингом не добавлялся: в проекте нет DOM-окружения (jsdom/happy-dom), а установка зависимостей вне задачи. Тестируется чистая логика (`classifySaveError`, `dropTarget`); поведение хуков покрывают e2e.
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
- [backend/libs/kernel-domain/tests/unit/test_error_codes.py](../backend/libs/kernel-domain/tests/unit/test_error_codes.py)
- [backend/libs/kernel-domain/tests/unit/test_query_purity.py](../backend/libs/kernel-domain/tests/unit/test_query_purity.py)
- [backend/libs/kernel-domain/tests/unit/test_frontend_contract.py](../backend/libs/kernel-domain/tests/unit/test_frontend_contract.py)
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
- [backend/services/identity-service/src/infrastructure/db/unit_of_work.py](../backend/services/identity-service/src/infrastructure/db/unit_of_work.py)
- [backend/services/identity-service/tests/unit/test_access_decisions.py](../backend/services/identity-service/tests/unit/test_access_decisions.py)
- [backend/services/identity-service/tests/unit/test_orm_models.py](../backend/services/identity-service/tests/unit/test_orm_models.py)

### Tasks

- [backend/services/tasks-service/src/domain/events/task_domain_event.py](../backend/services/tasks-service/src/domain/events/task_domain_event.py)
- [backend/services/tasks-service/src/domain/value_objects/task_id.py](../backend/services/tasks-service/src/domain/value_objects/task_id.py)
- [backend/services/tasks-service/src/infrastructure/db/models.py](../backend/services/tasks-service/src/infrastructure/db/models.py)
- [backend/services/tasks-service/src/infrastructure/db/task_mapper.py](../backend/services/tasks-service/src/infrastructure/db/task_mapper.py)
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
- [frontend/src/useBoardColumns.ts](../frontend/src/useBoardColumns.ts)
- [frontend/src/useBoardDrag.ts](../frontend/src/useBoardDrag.ts)
- [frontend/src/errors.test.ts](../frontend/src/errors.test.ts)
- [frontend/src/useBoardDrag.test.ts](../frontend/src/useBoardDrag.test.ts)
- [frontend/src/boardRealtime.ts](../frontend/src/boardRealtime.ts)

### Smoke

- [scripts/integration-smoke.py](../scripts/integration-smoke.py)
