"""Shared test fixtures for slip-stream."""

from pathlib import Path

import pytest
from mongomock_motor import AsyncMongoMockClient

from slip_stream.core.schema.registry import SchemaRegistry

SAMPLE_SCHEMAS_DIR = Path(__file__).parent / "sample_schemas"


@pytest.fixture(autouse=True)
def reset_schema_registry():
    """Reset the SchemaRegistry singleton between tests for isolation."""
    SchemaRegistry.reset()
    yield
    SchemaRegistry.reset()


@pytest.fixture
def schema_dir():
    """Return the path to sample schemas."""
    return SAMPLE_SCHEMAS_DIR


@pytest.fixture
def registry(schema_dir):
    """Return a SchemaRegistry loaded with sample schemas."""
    return SchemaRegistry(schema_dir=schema_dir)


@pytest.fixture
def mock_db():
    """Return a mongomock-motor async database for testing."""
    client = AsyncMongoMockClient()
    return client["test_db"]
