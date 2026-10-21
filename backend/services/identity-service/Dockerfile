FROM ghcr.io/astral-sh/uv:0.12.21 AS uv
FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /srv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock ./
COPY libs/platform/pyproject.toml libs/platform/pyproject.toml
COPY services/identity-service/pyproject.toml services/identity-service/pyproject.toml
COPY services/tasks-service/pyproject.toml services/tasks-service/pyproject.toml
COPY services/automation-service/pyproject.toml services/automation-service/pyproject.toml
RUN uv sync --locked --package identity-service --no-dev --no-install-workspace
COPY libs/platform/src libs/platform/src
RUN uv sync --locked --package identity-service --no-dev --no-editable

FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS runtime
RUN groupadd --gid 10001 botflow && useradd --uid 10001 --gid botflow --create-home botflow
WORKDIR /srv
COPY --from=builder --chown=botflow:botflow /srv/.venv /srv/.venv
COPY --chown=botflow:botflow services/identity-service/src /srv/src
COPY --chown=botflow:botflow services/identity-service/scripts /srv/scripts
ENV PYTHONPATH="/srv/src" PATH="/srv/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER botflow
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
