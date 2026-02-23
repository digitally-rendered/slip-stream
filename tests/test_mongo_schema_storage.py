"""Tests for MongoSchemaStorage adapter."""

import pytest
from mongomock_motor import AsyncMongoMockClient

from slip_stream.adapters.persistence.schema.mongo_storage import MongoSchemaStorage


@pytest.fixture
def db():
    """In-memory mock MongoDB database."""
    client = AsyncMongoMockClient()
    return client["test_schema_db"]


@pytest.fixture
def storage(db):
    return MongoSchemaStorage(db=db)


@pytest.fixture
def sample_schema():
    return {
        "type": "object",
        "title": "Widget",
        "properties": {"name": {"type": "string"}},
    }


class TestMongoSchemaStorage:

    @pytest.mark.asyncio
    async def test_save_and_load_round_trip(self, storage, sample_schema):
        await storage.save("widget", "1.0.0", sample_schema)
        loaded = await storage.load("widget", "1.0.0")
        assert loaded is not None
        assert loaded["title"] == "Widget"

    @pytest.mark.asyncio
    async def test_save_duplicate_version_upserts(self, storage, sample_schema):
        await storage.save("widget", "1.0.0", {**sample_schema, "title": "Original"})
        await storage.save("widget", "1.0.0", {**sample_schema, "title": "Updated"})
        loaded = await storage.load("widget", "1.0.0")
        assert loaded["title"] == "Updated"

    @pytest.mark.asyncio
    async def test_load_nonexistent_returns_none(self, storage):
        result = await storage.load("missing", "1.0.0")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_versions_sorted_by_semver(self, storage, sample_schema):
        await storage.save("widget", "2.0.0", {**sample_schema})
        await storage.save("widget", "1.0.0", {**sample_schema})
        await storage.save("widget", "1.5.0", {**sample_schema})
        versions = await storage.list_versions("widget")
        assert versions == ["1.0.0", "1.5.0", "2.0.0"]

    @pytest.mark.asyncio
    async def test_list_names(self, storage, sample_schema):
        await storage.save("widget", "1.0.0", {**sample_schema})
        await storage.save("gadget", "1.0.0", {**sample_schema})
        names = await storage.list_names()
        assert names == ["gadget", "widget"]

    @pytest.mark.asyncio
    async def test_load_latest(self, storage, sample_schema):
        await storage.save("widget", "1.0.0", {**sample_schema, "title": "v1"})
        await storage.save("widget", "2.0.0", {**sample_schema, "title": "v2"})
        result = await storage.load_latest("widget")
        assert result is not None
        version, schema = result
        assert version == "2.0.0"
        assert schema["title"] == "v2"

    @pytest.mark.asyncio
    async def test_load_latest_no_versions_returns_none(self, storage):
        result = await storage.load_latest("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exists(self, storage, sample_schema):
        await storage.save("widget", "1.0.0", {**sample_schema})
        assert await storage.exists("widget", "1.0.0") is True
        assert await storage.exists("widget", "2.0.0") is False

    @pytest.mark.asyncio
    async def test_checksum_stored(self, db, storage, sample_schema):
        await storage.save("widget", "1.0.0", sample_schema)
        doc = await db["_schema_registry"].find_one({"name": "widget"})
        assert doc is not None
        assert doc["checksum"].startswith("sha256:")

    @pytest.mark.asyncio
    async def test_decomposed_version_fields(self, db, storage, sample_schema):
        await storage.save("widget", "2.3.4", sample_schema)
        doc = await db["_schema_registry"].find_one({"name": "widget", "version": "2.3.4"})
        assert doc["version_major"] == 2
        assert doc["version_minor"] == 3
        assert doc["version_patch"] == 4

    @pytest.mark.asyncio
    async def test_multiple_schemas_independent(self, storage, sample_schema):
        await storage.save("widget", "1.0.0", {**sample_schema, "title": "Widget"})
        await storage.save("gadget", "1.0.0", {**sample_schema, "title": "Gadget"})
        w = await storage.load("widget", "1.0.0")
        g = await storage.load("gadget", "1.0.0")
        assert w["title"] == "Widget"
        assert g["title"] == "Gadget"

    @pytest.mark.asyncio
    async def test_list_versions_empty(self, storage):
        versions = await storage.list_versions("nonexistent")
        assert versions == []
