import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

from observability.context import request_id_var

_DEV_FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class JsonFormatter(logging.Formatter):
    """One JSON object per line with a fixed set of keys; the access-log keys
    are `null` outside the record written by `RequestContextMiddleware`."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self._service,
            "request_id": request_id_var.get(),
            "method": getattr(record, "method", None),
            "path": getattr(record, "path", None),
            "status_code": getattr(record, "status_code", None),
            "duration_ms": getattr(record, "duration_ms", None),
        }
        return json.dumps(payload, ensure_ascii=False)


class ColorFormatter(logging.Formatter):
    """ANSI-coloured text lines for local development."""

    _COLORS = {
        logging.DEBUG: "\x1b[36m",
        logging.INFO: "\x1b[32m",
        logging.WARNING: "\x1b[33m",
        logging.ERROR: "\x1b[31m",
        logging.CRITICAL: "\x1b[35m",
    }
    _RESET = "\x1b[0m"

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        color = self._COLORS.get(record.levelno, self._RESET)
        return f"{color}{message}{self._RESET}"


def select_formatter(app_env: str, service: str) -> logging.Formatter:
    """JSON by default; coloured text for `dev` or `OBSERVABILITY_LOG_FORMAT`."""
    requested_format = os.getenv("OBSERVABILITY_LOG_FORMAT", "").lower()
    if requested_format == "json":
        return JsonFormatter(service)
    if requested_format in {"color", "text"} or app_env == "dev":
        return ColorFormatter(_DEV_FMT)
    return JsonFormatter(service)


def configure_logging(
    app_env: str,
    service: str,
    *,
    logger: logging.Logger | None = None,
) -> None:
    """Attach the selected formatter to a process logger once."""
    target = logger if logger is not None else logging.getLogger()
    if target.handlers:
        return
    target.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(select_formatter(app_env, service))
    target.addHandler(handler)
