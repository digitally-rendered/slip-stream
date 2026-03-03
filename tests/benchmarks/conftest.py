"""Shared fixtures for benchmark tests."""

from pathlib import Path

import pytest
from mongomock_motor import AsyncMongoMockClient

from slip_stream.core.schema.registry import SchemaRegistry

SAMPLE_SCHEMAS_DIR = Path(__file__).parent.parent / "sample_schemas"


@pytest.fixture(autouse=True)
def _reset():
    SchemaRegistry.reset()
    yield
    SchemaRegistry.reset()


@pytest.fixture
def mock_db():
    client = AsyncMongoMockClient()
    return client["bench_db"]
