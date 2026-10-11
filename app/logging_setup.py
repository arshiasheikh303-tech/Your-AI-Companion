"""Structured (JSON-lines) logging with secret redaction."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from app.config import get_settings

_STANDARD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class RedactingJsonFormatter(logging.Formatter):
    def __init__(self, secrets: list[str] | None = None):
        super().__init__()
        self._secrets = [s for s in (secrets or []) if s]

    def _redact(self, text: str) -> str:
        for s in self._secrets:
            text = text.replace(s, "***")
        return text

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return self._redact(json.dumps(payload, default=str, ensure_ascii=False))


def setup_logging() -> None:
    s = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingJsonFormatter(s.secrets()))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(s.log_level.upper())
    # httpx logs full URLs (which include the Telegram token) -> keep quiet.
    for noisy in ("httpx", "httpcore", "telegram", "apscheduler", "alembic", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
