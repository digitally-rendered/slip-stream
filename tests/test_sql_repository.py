"""Tests for the SQL repository adapter.

Uses SQLite (aiosqlite) for an in-process async database — no external
services required.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import BaseModel

try:
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import (
        async_sessionmaker,
        create_async_engine,
    )

    HAS_SA = True
except ImportError:
    HAS_SA = False

pytestmark = pytest.mark.skipif(not HAS_SA, reason="sqlalchemy not installed")

from slip_stream.adapters.persistence.db.sql_repository import (
    SQLRepository,
    build_table_from_schema,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


class WidgetCreate(BaseModel):
    name: str
    color: str | None = None
    weight: float | None = None
    active: bool | None = None


class WidgetUpdate(BaseModel):
    name: str | None = None
    color: str | None = None
    weight: float | None = None
    active: bool | None = None


@pytest.fixture
async def db_session():
    """Create an in-memory SQLite async session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    metadata = sa.MetaData()
    table = build_table_from_schema("widget", WIDGET_SCHEMA, metadata)

    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session, table

    await engine.dispose()


# ---------------------------------------------------------------------------
# Table builder
# ---------------------------------------------------------------------------


class TestBuildTable:
    def test_creates_table(self):
        metadata = sa.MetaData()
        table = build_table_from_schema("widget", WIDGET_SCHEMA, metadata)
        assert table.name == "widget"
        col_names = {c.name for c in table.columns}
        assert "id" in col_names
        assert "entity_id" in col_names
        assert "name" in col_names
        assert "color" in col_names
        assert "weight" in col_names
        assert "active" in col_names
        assert "record_version" in col_names

    def test_audit_fields_not_duplicated(self):
        metadata = sa.MetaData()
        table = build_table_from_schema("widget", WIDGET_SCHEMA, metadata)
        col_names = [c.name for c in table.columns]
        assert col_names.count("id") == 1
        assert col_names.count("entity_id") == 1

    def test_empty_properties(self):
        metadata = sa.MetaData()
        table = build_table_from_schema(
            "empty", {"title": "Empty", "type": "object", "properties": {}}, metadata
        )
        col_names = {c.name for c in table.columns}
        assert "id" in col_names
        assert "entity_id" in col_names


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestSQLRepositoryCreate:
    @pytest.mark.asyncio
    async def test_create(self, db_session):
        session, table = db_session
        repo = SQLRepository(session, table)
        result = await repo.create(WidgetCreate(name="Gear"), user_id="u1")

        assert result["name"] == "Gear"
        assert result["record_version"] == 1
        assert result["created_by"] == "u1"
        assert result["entity_id"] is not None
        assert result["deleted_at"] is None

    @pytest.mark.asyncio
    async def test_create_with_entity_id(self, db_session):
        session, table = db_session
        repo = SQLRepository(session, table)
        eid = uuid.uuid4()
        result = await repo.create(WidgetCreate(name="Bolt"), entity_id=eid)
        assert result["entity_id"] == str(eid)


class TestSQLRepositoryGet:
    @pytest.mark.asyncio
    async def test_get_by_entity_id(self, db_session):
        session, table = db_session
        repo = SQLRepository(session, table)
        created = await repo.create(WidgetCreate(name="Sprocket"), user_id="u1")
        eid = uuid.UUID(created["entity_id"])

        fetched = await repo.get_by_entity_id(eid)
        assert fetched is not None
        assert fetched["name"] == "Sprocket"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, db_session):
        session, table = db_session
        repo = SQLRepository(session, table)
        result = await repo.get_by_entity_id(uuid.uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_get_deleted_returns_none(self, db_session):
        session, table = db_session
        repo = SQLRepository(session, table)
        created = await repo.create(WidgetCreate(name="Gone"), user_id="u1")
        eid = uuid.UUID(created["entity_id"])
        await repo.delete_by_entity_id(eid, user_id="u1")

        result = await repo.get_by_entity_id(eid)
        assert result is None


class TestSQLRepositoryList:
    @pytest.mark.asyncio
    async def test_list_empty(self, db_session):
        session, table = db_session
        repo = SQLRepository(session, table)
        result = await repo.list_latest_active()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_multiple(self, db_session):
        session, table = db_session
        repo = SQLRepository(session, table)
        await repo.create(WidgetCreate(name="A"), user_id="u1")
        await repo.create(WidgetCreate(name="B"), user_id="u1")
        await repo.create(WidgetCreate(name="C"), user_id="u1")

        result = await repo.list_latest_active()
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_list_excludes_deleted(self, db_session):
        session, table = db_session
        repo = SQLRepository(session, table)
        await repo.create(WidgetCreate(name="Keep"), user_id="u1")
        c2 = await repo.create(WidgetCreate(name="Delete"), user_id="u1")
        await repo.delete_by_entity_id(uuid.UUID(c2["entity_id"]), user_id="u1")

        result = await repo.list_latest_active()
        assert len(result) == 1
        assert result[0]["name"] == "Keep"

    @pytest.mark.asyncio
    async def test_list_shows_latest_version(self, db_session):
        session, table = db_session
        repo = SQLRepository(session, table)
        created = await repo.create(WidgetCreate(name="Old"), user_id="u1")
        eid = uuid.UUID(created["entity_id"])
        await repo.update_by_entity_id(eid, WidgetUpdate(name="New"), user_id="u1")

        result = await repo.list_latest_active()
        assert len(result) == 1
        assert result[0]["name"] == "New"

    @pytest.mark.asyncio
    async def test_list_with_skip_limit(self, db_session):
        session, table = db_session
        repo = SQLRepository(session, table)
        for i in range(5):
            await repo.create(WidgetCreate(name=f"W{i}"), user_id="u1")

        result = await repo.list_latest_active(skip=2, limit=2)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_with_filter(self, db_session):
        session, table = db_session
        repo = SQLRepository(session, table)
        await repo.create(WidgetCreate(name="Red", color="red"), user_id="u1")
        await repo.create(WidgetCreate(name="Blue", color="blue"), user_id="u1")

        result = await repo.list_latest_active(filter_criteria={"color": "red"})
        assert len(result) == 1
        assert result[0]["name"] == "Red"


class TestSQLRepositoryUpdate:
    @pytest.mark.asyncio
    async def test_update(self, db_session):
        session, table = db_session
        repo = SQLRepository(session, table)
        created = await repo.create(WidgetCreate(name="V1"), user_id="u1")
        eid = uuid.UUID(created["entity_id"])

        updated = await repo.update_by_entity_id(
            eid, WidgetUpdate(name="V2"), user_id="u2"
        )
        assert updated is not None
        assert updated["name"] == "V2"
        assert updated["record_version"] == 2
        assert updated["updated_by"] == "u2"

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, db_session):
        session, table = db_session
        repo = SQLRepository(session, table)
        result = await repo.update_by_entity_id(uuid.uuid4(), WidgetUpdate(name="X"))
        assert result is None

    @pytest.mark.asyncio
    async def test_update_no_change(self, db_session):
        session, table = db_session
        repo = SQLRepository(session, table)
        created = await repo.create(WidgetCreate(name="Same"), user_id="u1")
        eid = uuid.UUID(created["entity_id"])

        result = await repo.update_by_entity_id(eid, WidgetUpdate(name="Same"))
        assert result is None

    @pytest.mark.asyncio
    async def test_update_deleted_returns_none(self, db_session):
        session, table = db_session
        repo = SQLRepository(session, table)
        created = await repo.create(WidgetCreate(name="Del"), user_id="u1")
        eid = uuid.UUID(created["entity_id"])
        await repo.delete_by_entity_id(eid, user_id="u1")

        result = await repo.update_by_entity_id(eid, WidgetUpdate(name="New"))
        assert result is None


class TestSQLRepositoryDelete:
    @pytest.mark.asyncio
    async def test_delete(self, db_session):
        session, table = db_session
        repo = SQLRepository(session, table)
        created = await repo.create(WidgetCreate(name="Doomed"), user_id="u1")
        eid = uuid.UUID(created["entity_id"])

        deleted = await repo.delete_by_entity_id(eid, user_id="u1")
        assert deleted is not None
        assert deleted["deleted_at"] is not None
        assert deleted["deleted_by"] == "u1"
        assert deleted["record_version"] == 2

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, db_session):
        session, table = db_session
        repo = SQLRepository(session, table)
        result = await repo.delete_by_entity_id(uuid.uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_double_delete(self, db_session):
        session, table = db_session
        repo = SQLRepository(session, table)
        created = await repo.create(WidgetCreate(name="Once"), user_id="u1")
        eid = uuid.UUID(created["entity_id"])

        await repo.delete_by_entity_id(eid, user_id="u1")
        result = await repo.delete_by_entity_id(eid, user_id="u1")
        assert result is None
