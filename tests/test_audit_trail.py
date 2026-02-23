"""Tests for the audit trail system."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from slip_stream.core.audit import AuditEntry, AuditTrail
from slip_stream.core.events import EventBus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeEntity(BaseModel):
    entity_id: str = "ent-123"
    name: str = "Widget A"


class _FakeCreateData(BaseModel):
    name: str = "Widget A"


def _make_ctx(
    operation="create",
    schema_name="widget",
    entity_id=None,
    data=None,
    result=None,
    channel="rest",
    user_id="user-1",
):
    return SimpleNamespace(
        operation=operation,
        schema_name=schema_name,
        entity_id=entity_id,
        data=data,
        result=result,
        channel=channel,
        current_user={"id": user_id},
        db=None,
    )


# ---------------------------------------------------------------------------
# AuditEntry
# ---------------------------------------------------------------------------


class TestAuditEntry:

    def test_creates_with_timestamp(self):
        entry = AuditEntry(operation="create", schema_name="widget")
        assert entry.timestamp is not None
        assert entry.operation == "create"
        assert entry.schema_name == "widget"

    def test_to_dict(self):
        entry = AuditEntry(
            operation="update",
            schema_name="widget",
            entity_id="abc-123",
            user_id="user-1",
            changes={"name": "New Name"},
        )
        d = entry.to_dict()
        assert d["operation"] == "update"
        assert d["entity_id"] == "abc-123"
        assert d["user_id"] == "user-1"
        assert d["changes"]["name"] == "New Name"
        assert "timestamp" in d


# ---------------------------------------------------------------------------
# AuditTrail — in-memory mode
# ---------------------------------------------------------------------------


class TestAuditTrailInMemory:

    @pytest.mark.asyncio
    async def test_record_stores_entry(self):
        audit = AuditTrail(in_memory=True)
        entry = await audit.record(
            operation="create",
            schema_name="widget",
            entity_id="ent-1",
            user_id="user-1",
        )
        assert entry.operation == "create"
        assert len(audit._entries) == 1

    @pytest.mark.asyncio
    async def test_get_history(self):
        audit = AuditTrail(in_memory=True)
        await audit.record(operation="create", schema_name="widget", entity_id="ent-1", user_id="user-1")
        await audit.record(operation="update", schema_name="widget", entity_id="ent-1", user_id="user-1")
        await audit.record(operation="create", schema_name="widget", entity_id="ent-2", user_id="user-1")

        history = await audit.get_history("ent-1")
        assert len(history) == 2
        # Newest first
        assert history[0]["operation"] == "update"
        assert history[1]["operation"] == "create"

    @pytest.mark.asyncio
    async def test_get_history_filters_by_schema(self):
        audit = AuditTrail(in_memory=True)
        await audit.record(operation="create", schema_name="widget", entity_id="ent-1")
        await audit.record(operation="create", schema_name="gadget", entity_id="ent-1")

        history = await audit.get_history("ent-1", schema_name="widget")
        assert len(history) == 1
        assert history[0]["schema_name"] == "widget"

    @pytest.mark.asyncio
    async def test_get_user_activity(self):
        audit = AuditTrail(in_memory=True)
        await audit.record(operation="create", schema_name="widget", entity_id="ent-1", user_id="user-1")
        await audit.record(operation="update", schema_name="widget", entity_id="ent-2", user_id="user-2")
        await audit.record(operation="delete", schema_name="widget", entity_id="ent-3", user_id="user-1")

        activity = await audit.get_user_activity("user-1")
        assert len(activity) == 2
        assert all(e["user_id"] == "user-1" for e in activity)

    @pytest.mark.asyncio
    async def test_history_limit(self):
        audit = AuditTrail(in_memory=True)
        for i in range(10):
            await audit.record(operation="update", schema_name="widget", entity_id="ent-1", user_id="user-1")

        history = await audit.get_history("ent-1", limit=3)
        assert len(history) == 3


# ---------------------------------------------------------------------------
# AuditTrail — EventBus integration
# ---------------------------------------------------------------------------


class TestAuditEventBusIntegration:

    @pytest.mark.asyncio
    async def test_post_create_records_entry(self):
        audit = AuditTrail(in_memory=True)
        bus = EventBus()
        audit.register(bus)

        result = _FakeEntity()
        ctx = _make_ctx(
            operation="create",
            data=_FakeCreateData(name="Widget A"),
            result=result,
        )

        await bus.emit("post_create", ctx)

        assert len(audit._entries) == 1
        entry = audit._entries[0]
        assert entry["operation"] == "create"
        assert entry["schema_name"] == "widget"
        assert entry["user_id"] == "user-1"
        assert entry["changes"]["name"] == "Widget A"

    @pytest.mark.asyncio
    async def test_post_update_records_entry(self):
        audit = AuditTrail(in_memory=True)
        bus = EventBus()
        audit.register(bus)

        ctx = _make_ctx(
            operation="update",
            entity_id="ent-123",
            data=_FakeCreateData(name="Updated"),
        )

        await bus.emit("post_update", ctx)

        assert len(audit._entries) == 1
        entry = audit._entries[0]
        assert entry["operation"] == "update"
        assert entry["entity_id"] == "ent-123"

    @pytest.mark.asyncio
    async def test_post_delete_records_entry(self):
        audit = AuditTrail(in_memory=True)
        bus = EventBus()
        audit.register(bus)

        ctx = _make_ctx(operation="delete", entity_id="ent-456")
        await bus.emit("post_delete", ctx)

        assert len(audit._entries) == 1
        assert audit._entries[0]["operation"] == "delete"

    @pytest.mark.asyncio
    async def test_reads_not_tracked_by_default(self):
        audit = AuditTrail(in_memory=True)
        bus = EventBus()
        audit.register(bus)

        ctx = _make_ctx(operation="get", entity_id="ent-1")
        await bus.emit("post_get", ctx)

        assert len(audit._entries) == 0

    @pytest.mark.asyncio
    async def test_reads_tracked_when_enabled(self):
        audit = AuditTrail(in_memory=True, track_reads=True)
        bus = EventBus()
        audit.register(bus)

        ctx = _make_ctx(operation="get", entity_id="ent-1")
        await bus.emit("post_get", ctx)

        assert len(audit._entries) == 1
        assert audit._entries[0]["operation"] == "get"

    @pytest.mark.asyncio
    async def test_list_tracked_when_enabled(self):
        audit = AuditTrail(in_memory=True, track_reads=True)
        bus = EventBus()
        audit.register(bus)

        ctx = _make_ctx(operation="list")
        await bus.emit("post_list", ctx)

        assert len(audit._entries) == 1
        assert audit._entries[0]["operation"] == "list"

    @pytest.mark.asyncio
    async def test_channel_recorded(self):
        audit = AuditTrail(in_memory=True)
        bus = EventBus()
        audit.register(bus)

        ctx = _make_ctx(operation="create", data=_FakeCreateData(), result=_FakeEntity(), channel="graphql")
        await bus.emit("post_create", ctx)

        assert audit._entries[0]["channel"] == "graphql"

    @pytest.mark.asyncio
    async def test_anonymous_user_fallback(self):
        audit = AuditTrail(in_memory=True)
        bus = EventBus()
        audit.register(bus)

        ctx = SimpleNamespace(
            operation="create",
            schema_name="widget",
            entity_id=None,
            data=None,
            result=None,
            channel="rest",
            current_user=None,
            db=None,
        )
        await bus.emit("post_create", ctx)

        assert audit._entries[0]["user_id"] == "anonymous"

    @pytest.mark.asyncio
    async def test_full_lifecycle(self):
        """Simulate create → update → delete and verify audit trail."""
        audit = AuditTrail(in_memory=True)
        bus = EventBus()
        audit.register(bus)

        # Create
        ctx = _make_ctx(
            operation="create",
            data=_FakeCreateData(name="Widget A"),
            result=_FakeEntity(entity_id="ent-1"),
        )
        await bus.emit("post_create", ctx)

        # Update
        ctx = _make_ctx(
            operation="update",
            entity_id="ent-1",
            data=_FakeCreateData(name="Widget B"),
        )
        await bus.emit("post_update", ctx)

        # Delete
        ctx = _make_ctx(operation="delete", entity_id="ent-1")
        await bus.emit("post_delete", ctx)

        history = await audit.get_history("ent-1")
        assert len(history) == 3
        assert history[0]["operation"] == "delete"
        assert history[1]["operation"] == "update"
        assert history[2]["operation"] == "create"
