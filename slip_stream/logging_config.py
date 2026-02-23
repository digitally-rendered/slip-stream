"""Centralized logging configuration for slip-stream.

Provides a standard log format and a one-call setup function that
consumers can invoke during application startup.

Usage::

    from slip_stream.logging_config import configure_logging

    # Simple — structured console output at INFO level
    configure_logging()

    # Custom level
    configure_logging(level="DEBUG")

    # JSON format for production
    configure_logging(fmt="json")

    # Custom format string
    configure_logging(fmt="%(asctime)s %(name)s %(message)s")

All slip-stream loggers use ``logging.getLogger(__name__)`` so they
live under the ``"slip_stream"`` namespace and can be controlled
independently of application loggers.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Literal


# ---------------------------------------------------------------------------
# Standard format strings
# ---------------------------------------------------------------------------

DEFAULT_FORMAT = (
    "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
)
"""Default human-readable log format."""

VERBOSE_FORMAT = (
    "%(asctime)s [%(levelname)-8s] %(name)s:%(funcName)s:%(lineno)d %(message)s"
)
"""Verbose format with function name and line number."""


# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------


class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects.

    Useful for structured logging in production environments where logs
    are ingested by systems like Datadog, ELK, or CloudWatch.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.funcName and record.funcName != "<module>":
            entry["function"] = record.funcName
        if record.lineno:
            entry["line"] = record.lineno
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def configure_logging(
    level: str | int = "INFO",
    fmt: Literal["default", "verbose", "json"] | str = "default",
    logger_name: str = "slip_stream",
) -> None:
    """Configure logging for the slip-stream library.

    Sets up a ``StreamHandler`` on the ``slip_stream`` root logger with
    the chosen format and level.  Safe to call multiple times — existing
    handlers are cleared first to avoid duplicates.

    Args:
        level: Log level name (``"DEBUG"``, ``"INFO"``, etc.) or numeric.
        fmt: Format preset (``"default"``, ``"verbose"``, ``"json"``)
            or a custom format string.
        logger_name: Logger namespace to configure.  Defaults to
            ``"slip_stream"`` which covers all library loggers.
    """
    root = logging.getLogger(logger_name)

    # Clear existing handlers to prevent duplicates on re-call
    root.handlers.clear()

    # Resolve formatter
    if fmt == "default":
        formatter = logging.Formatter(DEFAULT_FORMAT)
    elif fmt == "verbose":
        formatter = logging.Formatter(VERBOSE_FORMAT)
    elif fmt == "json":
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(fmt)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root.addHandler(handler)
    root.setLevel(level if isinstance(level, int) else getattr(logging, level.upper(), logging.INFO))
