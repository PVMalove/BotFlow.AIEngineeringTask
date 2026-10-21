.DEFAULT_GOAL := help

ROOT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
BACKEND_DIR := $(ROOT_DIR)/backend
FRONTEND_DIR := $(ROOT_DIR)/frontend
COMPOSE_FILE ?= $(ROOT_DIR)/docker-compose.yml
COMPOSE = docker compose --project-directory "$(ROOT_DIR)" -f "$(COMPOSE_FILE)"
NPM ?= npm
PYTHON ?= python3
UV ?= uv

.PHONY: init infra help deps check lint format test qa build setup demo run up_dev down \
	compose-check bootstrap-admin backend-check backend-test backend-format \
	frontend-check frontend-test frontend-format frontend-build e2e \
	_require-frontend-files _require-frontend-env _require-compose

.PHONY: smoke scripts-check scripts-test

help: ## Показать команды разработки BotFlow
	@$(PYTHON) -c "import pathlib,re; text=pathlib.Path(r'$(ROOT_DIR)/Makefile').read_text(); print('\n'.join(f'{m[1]:<20} {m[2]}' for m in re.finditer(r'^([a-zA-Z0-9_-]+):.*?## (.+)$$',text,re.M)))"

_require-frontend-files:
	$(if $(wildcard $(FRONTEND_DIR)/package.json),,$(error Нет frontend/package.json: сначала реализуйте согласованный frontend scaffold))
	$(if $(wildcard $(FRONTEND_DIR)/package-lock.json),,$(error Нет frontend/package-lock.json: зависимости должны быть зафиксированы))

_require-frontend-env: _require-frontend-files
	$(if $(wildcard $(FRONTEND_DIR)/node_modules/.package-lock.json),,$(error Frontend-зависимости не установлены: после разрешения установки выполните make deps))

_require-compose:
	$(if $(wildcard $(COMPOSE_FILE)),,$(error Нет $(COMPOSE_FILE): Docker Compose приложения ещё не реализован))

deps: _require-frontend-files ## Установить backend/frontend из lockfiles; выполняется отдельно от проверок
	$(MAKE) -C "$(BACKEND_DIR)" deps
	$(NPM) --prefix "$(FRONTEND_DIR)" ci

backend-check: ## Ruff/mypy всех backend-пакетов
	$(MAKE) -C "$(BACKEND_DIR)" check_all

backend-test: ## Pytest всех backend-пакетов
	$(MAKE) -C "$(BACKEND_DIR)" test_all

backend-format: ## Изменить форматирование backend-пакетов
	$(MAKE) -C "$(BACKEND_DIR)" format_all

frontend-check: _require-frontend-env ## ESLint, TypeScript и Prettier без изменения файлов
	$(NPM) --prefix "$(FRONTEND_DIR)" run lint
	$(NPM) --prefix "$(FRONTEND_DIR)" run typecheck
	$(NPM) --prefix "$(FRONTEND_DIR)" run format:check

frontend-test: _require-frontend-env ## Vitest/React Testing Library, без watch
	$(NPM) --prefix "$(FRONTEND_DIR)" run test -- --run

frontend-format: _require-frontend-env ## Изменить форматирование frontend
	$(NPM) --prefix "$(FRONTEND_DIR)" run format

frontend-build: _require-frontend-env ## Production-сборка Vite SPA
	$(NPM) --prefix "$(FRONTEND_DIR)" run build

e2e: _require-frontend-env ## Playwright; test:e2e обязан подготовить изолированное тестовое окружение
	$(NPM) --prefix "$(FRONTEND_DIR)" run test:e2e

scripts-check:
	$(UV) run --project "$(BACKEND_DIR)" --locked --no-sync ruff check scripts backend/tests
	$(UV) run --project "$(BACKEND_DIR)" --locked --no-sync ruff format --check scripts backend/tests

scripts-test:
	$(UV) run --project "$(BACKEND_DIR)" --locked --no-sync pytest scripts/tests

check: backend-check frontend-check scripts-check ## Проверить backend/frontend и служебные скрипты без автоисправлений

lint: check ## Синоним check, без автоформатирования

format: backend-format frontend-format ## Явно отформатировать backend и frontend

test: backend-test frontend-test scripts-test ## Запустить backend/frontend и тесты служебных скриптов

smoke: ## Настоящие ресурсы/DI/S3 и down/up в отдельном Compose-проекте; удаляет только свои тестовые тома
	$(PYTHON) scripts/integration-smoke.py

qa: ## Полная последовательность проверок, сборки образов и Playwright
	$(MAKE) check
	$(MAKE) test
	$(MAKE) build
	$(MAKE) e2e

init: ## Создать локальные secret-файлы без перезаписи существующих
	$(PYTHON) scripts/init-local-secrets.py

infra: compose-check ## Поднять PostgreSQL, Redis, RabbitMQ и MinIO
	$(COMPOSE) up -d --wait identity-db tasks-db automation-db redis rabbitmq minio

compose-check: _require-compose ## Проверить Compose без вывода подставленных секретов
	$(COMPOSE) config --quiet

build: compose-check ## Собрать Docker-образы frontend/API/workers
	$(PYTHON) scripts/compose_build.py --project-directory "$(ROOT_DIR)" -f "$(COMPOSE_FILE)"

setup: compose-check ## Поднять инфраструктуру и выполнить миграции/bootstrap без запуска приложения
	$(COMPOSE) up -d --wait identity-db tasks-db automation-db redis rabbitmq minio
	$(COMPOSE) run --rm --no-deps identity-bootstrap
	$(COMPOSE) run --rm --no-deps tasks-bootstrap
	$(COMPOSE) run --rm --no-deps automation-bootstrap

bootstrap-admin: compose-check ## Явно назначить первоначального admin по INITIAL_ADMIN_TELEGRAM_ID
	$(COMPOSE) run --rm identity-bootstrap bootstrap-admin

run: compose-check ## Поднять HTTP-каркас; healthchecks проверяют liveness
	$(COMPOSE) up -d --wait

up_dev: run ## Синоним локального запуска

demo: setup ## Пока отказывает: миграции и бизнес-bootstrap ещё не реализованы
	$(MAKE) bootstrap-admin
	$(MAKE) run

down: compose-check ## Остановить Compose с сохранением данных в томах
	$(COMPOSE) down
