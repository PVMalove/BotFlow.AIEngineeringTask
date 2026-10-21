.DEFAULT_GOAL := help

BACKEND_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
UV ?= uv
PYTHON ?= python3
pkg ?=
msg ?=
ALEMBIC_CONFIG ?= src/infrastructure/db/alembic.ini

MEMBER_MANIFESTS := $(wildcard $(BACKEND_DIR)/libs/*/pyproject.toml $(BACKEND_DIR)/services/*/pyproject.toml)
ALL_MEMBERS := $(sort $(notdir $(patsubst %/,%,$(dir $(MEMBER_MANIFESTS)))))
member_manifests = $(wildcard $(BACKEND_DIR)/libs/$(pkg)/pyproject.toml $(BACKEND_DIR)/services/$(pkg)/pyproject.toml)
member_dir = $(patsubst %/pyproject.toml,%,$(firstword $(member_manifests)))

.PHONY: help deps check lint format test check_all lint_all format_all test_all \
	db-revision db-upgrade db-current db-history _require-workspace \
	_require-env _require-members _require-pkg _require-service _require-alembic

help: ## Показать команды backend; pkg — имя пакета в services/ или libs/
	@$(PYTHON) -c "import pathlib,re; text=pathlib.Path(r'$(BACKEND_DIR)/Makefile').read_text(); print('\n'.join(f'{m[1]:<20} {m[2]}' for m in re.finditer(r'^([a-zA-Z0-9_-]+):.*?## (.+)$$',text,re.M)))"

_require-workspace:
	$(if $(wildcard $(BACKEND_DIR)/pyproject.toml),,$(error Нет backend/pyproject.toml: приложение ещё не реализовано))
	$(if $(wildcard $(BACKEND_DIR)/uv.lock),,$(error Нет backend/uv.lock: зависимости должны быть зафиксированы))

_require-env: _require-workspace
	$(if $(or $(wildcard $(BACKEND_DIR)/.venv/bin/python),$(wildcard $(BACKEND_DIR)/.venv/Scripts/python.exe)),,$(error Backend-окружение отсутствует: после разрешения установки выполните make deps))

_require-members: _require-workspace
	$(if $(ALL_MEMBERS),,$(error Нет backend-пакетов с pyproject.toml в services/ или libs/: пустая проверка недопустима))

_require-pkg: _require-workspace
	$(if $(pkg),,$(error Укажите pkg: make check pkg=identity-service))
	$(if $(filter $(pkg),$(ALL_MEMBERS)),,$(error Пакет '$(pkg)' не найден в services/ или libs/))
	$(if $(word 2,$(member_manifests)),$(error Имя '$(pkg)' неоднозначно: совпадает пакет в services/ и libs/),)

_require-service: _require-pkg
	$(if $(wildcard $(BACKEND_DIR)/services/$(pkg)/pyproject.toml),,$(error Миграции доступны только сервису из services/))

_require-alembic: _require-service
	$(if $(wildcard $(member_dir)/$(ALEMBIC_CONFIG)),,$(error Нет $(ALEMBIC_CONFIG) в сервисе $(pkg)))

deps: _require-workspace ## Установить workspace из uv.lock; не вызывается проверками
	cd "$(BACKEND_DIR)" && $(UV) sync --locked --all-packages

check: _require-pkg _require-env ## Ruff, mypy и проверка формата: make check pkg=<member>
	cd "$(member_dir)" && $(UV) run --locked --no-sync ruff check .
	cd "$(member_dir)" && $(UV) run --locked --no-sync mypy --cache-dir .mypy_cache .
	cd "$(member_dir)" && $(UV) run --locked --no-sync ruff format --check .

lint: check ## Синоним check без изменения файлов

format: _require-pkg _require-env ## Явное автоформатирование пакета
	cd "$(member_dir)" && $(UV) run --locked --no-sync ruff check --fix .
	cd "$(member_dir)" && $(UV) run --locked --no-sync ruff format .

test: _require-pkg _require-env ## Pytest пакета; отсутствие тестов является ошибкой
	cd "$(member_dir)" && $(UV) run --locked --no-sync pytest .

check_all: _require-members _require-env ## Проверить все backend-пакеты
	@set -eu; for member in $(ALL_MEMBERS); do $(MAKE) -C "$(BACKEND_DIR)" check pkg="$$member"; done

lint_all: check_all ## Синоним check_all

format_all: _require-members _require-env ## Отформатировать все backend-пакеты
	@set -eu; for member in $(ALL_MEMBERS); do $(MAKE) -C "$(BACKEND_DIR)" format pkg="$$member"; done

test_all: _require-members _require-env ## Протестировать все backend-пакеты
	@set -eu; for member in $(ALL_MEMBERS); do $(MAKE) -C "$(BACKEND_DIR)" test pkg="$$member"; done

db-revision: _require-alembic _require-env ## Autogenerate: pkg=<service> msg="описание"
	$(if $(strip $(msg)),,$(error Укажите msg для новой ревизии))
	cd "$(member_dir)" && $(UV) run --locked --no-sync alembic -c "$(ALEMBIC_CONFIG)" revision --autogenerate -m "$(msg)"

db-upgrade: _require-alembic _require-env ## Применить миграции сервиса до head
	cd "$(member_dir)" && $(UV) run --locked --no-sync alembic -c "$(ALEMBIC_CONFIG)" upgrade head

db-current: _require-alembic _require-env ## Показать текущую ревизию БД сервиса
	cd "$(member_dir)" && $(UV) run --locked --no-sync alembic -c "$(ALEMBIC_CONFIG)" current

db-history: _require-alembic _require-env ## Показать историю миграций сервиса
	cd "$(member_dir)" && $(UV) run --locked --no-sync alembic -c "$(ALEMBIC_CONFIG)" history
