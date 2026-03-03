"""Fixtures for integration tests against real MongoDB and PostgreSQL.

These tests require running database instances. They are skipped when
the relevant env var is not set:

- MONGO_URI=mongodb://localhost:27017         (for MongoDB tests)
- DATABASE_URL=postgresql+asyncpg://...       (for PostgreSQL tests)

Use ``make integration`` to start services and run all integration tests.
"""

import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from slip_stream.core.schema.registry import SchemaRegistry

MONGO_URI = os.environ.get("MONGO_URI")
DATABASE_URL = os.environ.get("DATABASE_URL")

SAMPLE_SCHEMAS_DIR = Path(__file__).parent.parent / "sample_schemas"

WIDGET_SCHEMA = {
    "title": "Widget",
    "version": "1.0.0",
    "type": "object",
    "required": ["name"],
    "properties": {
        "id": {"type": "string", "format": "uuid"},
        "entity_id": {"type": "string", "format": "uuid"},
        "schema_version": {"type": "string"},
        "record_version": {"type": "integer"},
        "created_at": {"type": "string", "format": "date-time"},
        "updated_at": {"type": "string", "format": "date-time"},
        "deleted_at": {"type": "string", "format": "date-time"},
        "created_by": {"type": "string"},
        "updated_by": {"type": "string"},
        "deleted_by": {"type": "string"},
        "name": {"type": "string", "description": "Widget name"},
        "color": {"type": "string"},
        "weight": {"type": "number"},
        "active": {"type": "boolean"},
    },
}


@pytest.fixture(autouse=True)
def _reset():
    SchemaRegistry.reset()
    yield
    SchemaRegistry.reset()


# ---------------------------------------------------------------------------
# MongoDB fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def real_db():
    """Connect to a real MongoDB and return a unique test database.

    Each test gets its own database to avoid cross-contamination.
    Databases are dropped after the test.
    """
    if MONGO_URI is None:
        pytest.skip("MONGO_URI not set")

    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(MONGO_URI)
    db_name = f"slip_stream_test_{uuid.uuid4().hex[:8]}"
    db = client[db_name]

    yield db

    await client.drop_database(db_name)
    client.close()


# ---------------------------------------------------------------------------
# PostgreSQL fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def real_sql_session():
    """Connect to a real PostgreSQL and return a session + table.

    Each test gets a fresh table (created, then dropped after the test).
    """
    if DATABASE_URL is None:
        pytest.skip("DATABASE_URL not set")

    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from slip_stream.adapters.persistence.db.sql_repository import (
        build_table_from_schema,
    )

    engine = create_async_engine(DATABASE_URL)
    metadata = sa.MetaData()
    suffix = uuid.uuid4().hex[:8]
    table = build_table_from_schema(f"widget_{suffix}", WIDGET_SCHEMA, metadata)

    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session, table

    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)

    await engine.dispose()
