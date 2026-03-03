"""Integration tests for SQLRepository against real PostgreSQL.

Validates that the append-only versioned document pattern works correctly
with a real PostgreSQL instance — catching behavior differences that
in-memory SQLite might not surface (type coercion, constraint handling,
concurrent access).
"""

import os
import uuid

import pytest
from pydantic import BaseModel

pytestmark = pytest.mark.skipif(
    os.environ.get("DATABASE_URL") is None,
    reason="DATABASE_URL not set",
)

from slip_stream.adapters.persistence.db.sql_repository import SQLRepository


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


class TestSQLCreateIntegration:
    @pytest.mark.asyncio
    async def test_create_returns_document(self, real_sql_session):
        session, table = real_sql_session
        repo = SQLRepository(session, table)
        result = await repo.create(WidgetCreate(name="real-widget"), user_id="u1")
        assert result["name"] == "real-widget"
        assert result["record_version"] == 1
        assert result["entity_id"] is not None

    @pytest.mark.asyncio
    async def test_create_generates_unique_entity_ids(self, real_sql_session):
        session, table = real_sql_session
        repo = SQLRepository(session, table)
        r1 = await repo.create(WidgetCreate(name="w1"), user_id="u1")
        r2 = await repo.create(WidgetCreate(name="w2"), user_id="u1")
        assert r1["entity_id"] != r2["entity_id"]

    @pytest.mark.asyncio
    async def test_uuid_roundtrip(self, real_sql_session):
        """UUIDs must survive write -> read without corruption."""
        session, table = real_sql_session
        repo = SQLRepository(session, table)
        created = await repo.create(WidgetCreate(name="uuid-test"), user_id="u1")
        fetched = await repo.get_by_entity_id(uuid.UUID(created["entity_id"]))
        assert fetched is not None
        assert fetched["entity_id"] == created["entity_id"]


class TestSQLUpdateIntegration:
    @pytest.mark.asyncio
    async def test_update_increments_version(self, real_sql_session):
        session, table = real_sql_session
        repo = SQLRepository(session, table)
        created = await repo.create(WidgetCreate(name="orig"), user_id="u1")
        eid = uuid.UUID(created["entity_id"])
        updated = await repo.update_by_entity_id(
            eid, WidgetUpdate(color="red"), user_id="u2"
        )
        assert updated["record_version"] == 2
        assert updated["color"] == "red"
        assert updated["name"] == "orig"  # unchanged

    @pytest.mark.asyncio
    async def test_update_preserves_entity_id(self, real_sql_session):
        session, table = real_sql_session
        repo = SQLRepository(session, table)
        created = await repo.create(WidgetCreate(name="orig"), user_id="u1")
        eid = uuid.UUID(created["entity_id"])
        updated = await repo.update_by_entity_id(
            eid, WidgetUpdate(name="changed"), user_id="u2"
        )
        assert updated["entity_id"] == created["entity_id"]

    @pytest.mark.asyncio
    async def test_update_creates_new_row(self, real_sql_session):
        """Update must create a NEW row (append-only), not mutate."""
        session, table = real_sql_session
        repo = SQLRepository(session, table)
        created = await repo.create(WidgetCreate(name="orig"), user_id="u1")
        eid = uuid.UUID(created["entity_id"])
        updated = await repo.update_by_entity_id(
            eid, WidgetUpdate(name="v2"), user_id="u2"
        )
        # Different row IDs (new row was inserted)
        assert updated["id"] != created["id"]


class TestSQLDeleteIntegration:
    @pytest.mark.asyncio
    async def test_soft_delete(self, real_sql_session):
        session, table = real_sql_session
        repo = SQLRepository(session, table)
        created = await repo.create(WidgetCreate(name="to-delete"), user_id="u1")
        eid = uuid.UUID(created["entity_id"])
        deleted = await repo.delete_by_entity_id(eid, user_id="u1")
        assert deleted["deleted_at"] is not None
        assert deleted["record_version"] == 2

    @pytest.mark.asyncio
    async def test_deleted_entity_not_in_list(self, real_sql_session):
        session, table = real_sql_session
        repo = SQLRepository(session, table)
        await repo.create(WidgetCreate(name="keep"), user_id="u1")
        to_delete = await repo.create(WidgetCreate(name="remove"), user_id="u1")
        eid = uuid.UUID(to_delete["entity_id"])
        await repo.delete_by_entity_id(eid, user_id="u1")

        active = await repo.list_latest_active()
        entity_ids = [doc["entity_id"] for doc in active]
        assert to_delete["entity_id"] not in entity_ids

    @pytest.mark.asyncio
    async def test_deleted_entity_returns_none_on_get(self, real_sql_session):
        session, table = real_sql_session
        repo = SQLRepository(session, table)
        created = await repo.create(WidgetCreate(name="gone"), user_id="u1")
        eid = uuid.UUID(created["entity_id"])
        await repo.delete_by_entity_id(eid, user_id="u1")
        result = await repo.get_by_entity_id(eid)
        assert result is None


class TestSQLListIntegration:
    @pytest.mark.asyncio
    async def test_list_returns_latest_versions_only(self, real_sql_session):
        """list_latest_active must return only the most recent version."""
        session, table = real_sql_session
        repo = SQLRepository(session, table)
        created = await repo.create(WidgetCreate(name="v1"), user_id="u1")
        eid = uuid.UUID(created["entity_id"])
        await repo.update_by_entity_id(eid, WidgetUpdate(name="v2"), user_id="u1")
        await repo.update_by_entity_id(eid, WidgetUpdate(name="v3"), user_id="u1")

        active = await repo.list_latest_active()
        matching = [d for d in active if d["entity_id"] == created["entity_id"]]
        assert len(matching) == 1
        assert matching[0]["name"] == "v3"
        assert matching[0]["record_version"] == 3

    @pytest.mark.asyncio
    async def test_count_active(self, real_sql_session):
        session, table = real_sql_session
        repo = SQLRepository(session, table)
        await repo.create(WidgetCreate(name="a"), user_id="u1")
        await repo.create(WidgetCreate(name="b"), user_id="u1")
        to_delete = await repo.create(WidgetCreate(name="c"), user_id="u1")
        await repo.delete_by_entity_id(uuid.UUID(to_delete["entity_id"]), user_id="u1")

        count = await repo.count_active()
        assert count == 2

    @pytest.mark.asyncio
    async def test_pagination(self, real_sql_session):
        """Skip/limit must work correctly with real PostgreSQL."""
        session, table = real_sql_session
        repo = SQLRepository(session, table)
        for i in range(5):
            await repo.create(WidgetCreate(name=f"item-{i}"), user_id="u1")

        page = await repo.list_latest_active(skip=2, limit=2)
        assert len(page) == 2
