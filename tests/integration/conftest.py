"""Fixtures for integration tests against real MongoDB.

These tests require a running MongoDB instance. They are skipped when
MONGO_URI is not set (local dev without MongoDB) and run in CI where
a MongoDB service container is configured.

Set MONGO_URI=mongodb://localhost:27017 to run locally.
"""

import os
import uuid

import pytest
import pytest_asyncio

from slip_stream.core.schema.registry import SchemaRegistry

MONGO_URI = os.environ.get("MONGO_URI")

# Skip all tests in this directory if no MongoDB is available
pytestmark = pytest.mark.skipif(
    MONGO_URI is None,
    reason="MONGO_URI not set — skipping integration tests (set MONGO_URI to run)",
)


@pytest.fixture(autouse=True)
def _reset():
    SchemaRegistry.reset()
    yield
    SchemaRegistry.reset()


@pytest_asyncio.fixture
async def real_db():
    """Connect to a real MongoDB and return a unique test database.

    Each test gets its own database to avoid cross-contamination.
    Databases are dropped after the test.
    """
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(MONGO_URI)
    db_name = f"slip_stream_test_{uuid.uuid4().hex[:8]}"
    db = client[db_name]

    yield db

    await client.drop_database(db_name)
    client.close()
