"""Tests for slip_stream/logging_config.py."""

import json
import logging

import pytest

from slip_stream.logging_config import (
    DEFAULT_FORMAT,
    VERBOSE_FORMAT,
    JSONFormatter,
    configure_logging,
)

# Use a dedicated logger namespace so tests do not pollute the real slip_stream logger.
_TEST_LOGGER_NAME = "slip_stream_test_logging"


@pytest.fixture(autouse=True)
def _clean_test_logger():
    """Reset the test logger before and after each test."""
    logger = logging.getLogger(_TEST_LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)
    yield
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)


def test_configure_default_format():
    """configure_logging(fmt='default') attaches a handler with DEFAULT_FORMAT."""
    configure_logging(fmt="default", logger_name=_TEST_LOGGER_NAME)
    logger = logging.getLogger(_TEST_LOGGER_NAME)

    assert len(logger.handlers) == 1
    handler = logger.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.formatter._fmt == DEFAULT_FORMAT  # type: ignore[union-attr]


def test_configure_verbose_format():
    """configure_logging(fmt='verbose') attaches a handler with VERBOSE_FORMAT."""
    configure_logging(fmt="verbose", logger_name=_TEST_LOGGER_NAME)
    logger = logging.getLogger(_TEST_LOGGER_NAME)

    assert len(logger.handlers) == 1
    handler = logger.handlers[0]
    assert handler.formatter._fmt == VERBOSE_FORMAT  # type: ignore[union-attr]


def test_configure_json_format():
    """configure_logging(fmt='json') attaches a JSONFormatter handler."""
    configure_logging(fmt="json", logger_name=_TEST_LOGGER_NAME)
    logger = logging.getLogger(_TEST_LOGGER_NAME)

    assert len(logger.handlers) == 1
    handler = logger.handlers[0]
    assert isinstance(handler.formatter, JSONFormatter)


def test_configure_custom_format_string():
    """configure_logging with an arbitrary format string uses it verbatim."""
    custom_fmt = "%(levelname)s | %(message)s"
    configure_logging(fmt=custom_fmt, logger_name=_TEST_LOGGER_NAME)
    logger = logging.getLogger(_TEST_LOGGER_NAME)

    assert len(logger.handlers) == 1
    handler = logger.handlers[0]
    assert handler.formatter._fmt == custom_fmt  # type: ignore[union-attr]


def test_configure_numeric_level():
    """configure_logging accepts an integer log level."""
    configure_logging(level=logging.DEBUG, logger_name=_TEST_LOGGER_NAME)
    logger = logging.getLogger(_TEST_LOGGER_NAME)

    assert logger.level == logging.DEBUG


def test_json_formatter_basic_message():
    """JSONFormatter.format() produces valid JSON with required keys."""
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test_file.py",
        lineno=42,
        msg="hello world",
        args=(),
        exc_info=None,
    )
    output = formatter.format(record)
    parsed = json.loads(output)

    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test_logger"
    assert parsed["message"] == "hello world"
    assert "timestamp" in parsed
    assert parsed["line"] == "42"


def test_json_formatter_with_exception():
    """JSONFormatter.format() includes 'exception' key when exc_info is set."""
    formatter = JSONFormatter()

    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test_logger",
        level=logging.ERROR,
        pathname="test_file.py",
        lineno=10,
        msg="something went wrong",
        args=(),
        exc_info=exc_info,
    )
    output = formatter.format(record)
    parsed = json.loads(output)

    assert "exception" in parsed
    assert "ValueError" in parsed["exception"]
    assert "boom" in parsed["exception"]


def test_json_formatter_function_name_included():
    """JSONFormatter includes 'function' key when funcName is a real function name."""
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.DEBUG,
        pathname="test_file.py",
        lineno=5,
        msg="inside a function",
        args=(),
        exc_info=None,
    )
    record.funcName = "my_function"

    output = formatter.format(record)
    parsed = json.loads(output)

    assert parsed["function"] == "my_function"


def test_json_formatter_module_level_excludes_function():
    """JSONFormatter omits 'function' when funcName is '<module>'."""
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.DEBUG,
        pathname="test_file.py",
        lineno=1,
        msg="module-level log",
        args=(),
        exc_info=None,
    )
    record.funcName = "<module>"

    output = formatter.format(record)
    parsed = json.loads(output)

    assert "function" not in parsed
