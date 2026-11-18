"""The supervisor's logging (ADR-0398): one setup for every logger.

Every line is one JSON object on stderr, where Docker's json-file log takes it: ``time`` (UTC,
milliseconds), ``level``, ``logger`` and ``message``, then ``fields`` (what a call passed as
``extra=``) and ``exception`` (the traceback) when there are any. uvicorn's own loggers
(``uvicorn``, ``uvicorn.error``, ``uvicorn.access``) hold no handler, so their lines go through the
same handler, and they follow the same level. The handler holds back a warning or error that
repeats within ten minutes (``RepeatLimit``), so a failure that lasts writes a line per ten minutes.

``configure_logging()`` is called by ``src/main.py`` before anything else of the package is
imported, and by ``scripts/standin/run_supervisor.py`` through ``logging_config()``; uvicorn loads
the application after it has set up its own loggers and before its first line, so the setup
replaces them in time. The level is INFO, or what ``UNOS_LOG_LEVEL`` names; it applies to UNOS's own loggers (``src.*``)
and uvicorn's. Every other library logs warnings and above only, whatever the level: at DEBUG
aiosqlite writes the bound values of each statement, which hold password hashes and secrets.
"""
from __future__ import annotations

import json
import logging
import logging.config
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

LOG_LEVEL_ENV = "UNOS_LOG_LEVEL"
DEFAULT_LEVEL = logging.INFO
LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR,
          "CRITICAL": logging.CRITICAL}

# What a LogRecord has by itself; every other attribute came from ``extra=``.
_RECORD_ATTRIBUTES = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {
    "message", "asctime", "taskName",
    # uvicorn's coloured copy of its message
    "color_message",
}

# A backstop, not the protection: no code logs a secret (tests/test_logging.py). A value after a
# secret-like name, and a Bearer credential, is masked in what is written.
_SECRET_NAME = (r"(?:password|passwd|secret|token|session[_-]?key|totp[_-]?code|recovery[_-]?code|api[_-]?key"
                r"|authorization|cookie|input_value)[\w-]{0,20}")
_MASKED_VALUE = re.compile(
    rf"(?P<name>{_SECRET_NAME}[\"']?\s*[:=]\s*)(?P<value>\"[^\"]*\"|'[^']*'|(?:Bearer\s+)?[^\s,&;}}\]]+)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
# SQLAlchemy's DBAPIError text ends with "[parameters: (...)]" (the engine hides them already).
_SQL_PARAMETERS = re.compile(r"\[parameters: .*\]$", re.MULTILINE)
REDACTED = "[redacted]"


def mask_secrets(text: str) -> str:
    text = _SQL_PARAMETERS.sub(f"[parameters: {REDACTED}]", text)
    text = _MASKED_VALUE.sub(lambda m: f"{m.group('name')}{REDACTED}", text)
    return _BEARER.sub(lambda m: f"{m.group(1)}{REDACTED}", text)


class JsonFormatter(logging.Formatter):
    """One JSON object per record, on one line."""

    def format(self, record: logging.LogRecord) -> str:
        moment = datetime.fromtimestamp(record.created, timezone.utc)
        entry: dict[str, Any] = {
            "time": moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": mask_secrets(record.getMessage()),
        }
        fields = {key: _field(value) for key, value in vars(record).items() if key not in _RECORD_ATTRIBUTES}
        if fields:
            entry["fields"] = fields
        if record.exc_info:
            entry["exception"] = mask_secrets(self.formatException(record.exc_info))
        elif record.exc_text:
            entry["exception"] = mask_secrets(record.exc_text)
        if record.stack_info:
            entry["stack"] = mask_secrets(self.formatStack(record.stack_info))
        return json.dumps(entry, ensure_ascii=False, default=str)


def _field(value: Any) -> Any:
    if isinstance(value, str):
        return mask_secrets(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return mask_secrets(str(value))


def resolve_level(value: str | None) -> tuple[int, str | None]:
    """The level that ``UNOS_LOG_LEVEL`` names, and the value when it names none (INFO is used)."""
    text = (value or "").strip()
    if not text:
        return DEFAULT_LEVEL, None
    level = LEVELS.get(text.upper())
    if level is None:
        return DEFAULT_LEVEL, text
    return level, None


class RepeatLimit(logging.Filter):
    """Lets an identical warning or error through once per ``window`` seconds.

    A loop that fails on every pass, or a page that polls an endpoint that fails, would write the
    same line (and traceback) for as long as the failure lasts: one line per ``window`` instead,
    which says how many it held back (``suppressed_repeats``). Lines below WARNING (the changes
    of state) and the access log are never limited.
    """

    def __init__(self, window: float = 600.0, clock=time.monotonic, limit: int = 256) -> None:
        super().__init__()
        self.window, self.clock, self.limit = window, clock, limit
        self._seen: dict[tuple, list] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.WARNING or record.name == "uvicorn.access":
            return True
        error = record.exc_info[1] if record.exc_info else None
        key = (record.name, record.levelno, record.getMessage(), type(error).__name__, str(error))
        now = self.clock()
        entry = self._seen.get(key)
        if entry is not None and now - entry[0] < self.window:
            entry[1] += 1
            return False
        if entry is not None and entry[1]:
            record.suppressed_repeats = entry[1]
        if len(self._seen) >= self.limit:
            self._seen.pop(next(iter(self._seen)))
        self._seen[key] = [now, 0]
        return True


def logging_config(level: int = DEFAULT_LEVEL) -> dict[str, Any]:
    """The dictConfig of every logger, uvicorn's included: the one place that decides how a line looks."""
    name = logging.getLevelName(level)
    own = {"handlers": [], "level": name, "propagate": True}
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": f"{__name__}.JsonFormatter"}},
        "filters": {"repeats": {"()": f"{__name__}.RepeatLimit"}},
        "handlers": {"stderr": {"class": "logging.StreamHandler", "formatter": "json", "stream": "ext://sys.stderr",
                                "filters": ["repeats"]}},
        "root": {"level": "WARNING", "handlers": ["stderr"]},
        "loggers": {"src": dict(own), "uvicorn": dict(own), "uvicorn.error": dict(own), "uvicorn.access": dict(own)},
    }


def configure_logging() -> None:
    """Set every logger up; safe to call again (the handlers are replaced, not added)."""
    level, invalid = resolve_level(os.environ.get(LOG_LEVEL_ENV))
    logging.config.dictConfig(logging_config(level))
    if invalid is not None:
        logging.getLogger(__name__).warning(
            "%s=%r is not a log level (DEBUG, INFO, WARNING, ERROR or CRITICAL); using INFO", LOG_LEVEL_ENV, invalid
        )
