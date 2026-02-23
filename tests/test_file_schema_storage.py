"""Tests for FileSchemaStorage adapter."""

import json

import pytest

from slip_stream.adapters.persistence.schema.file_storage import FileSchemaStorage


@pytest.fixture
def storage(tmp_path):
    """FileSchemaStorage backed by a temporary directory."""
    return FileSchemaStorage(schema_dir=tmp_path)


@pytest.fixture
def sample_schema():
    return {
        "type": "object",
        "title": "Widget",
        "properties": {"name": {"type": "string"}},
    }


class TestFileSchemaStorage:

    @pytest.mark.asyncio
    async def test_save_and_load_round_trip(self, storage, sample_schema):
        await storage.save("widget", "1.0.0", sample_schema)
        loaded = await storage.load("widget", "1.0.0")
        assert loaded is not None
        assert loaded["title"] == "Widget"
        assert loaded["version"] == "1.0.0"

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
    async def test_load_latest_empty_returns_none(self, storage):
        result = await storage.load_latest("missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_exists(self, storage, sample_schema):
        await storage.save("widget", "1.0.0", {**sample_schema})
        assert await storage.exists("widget", "1.0.0") is True
        assert await storage.exists("widget", "2.0.0") is False

    @pytest.mark.asyncio
    async def test_backward_compat_flat_files(self, tmp_path):
        """Flat JSON files in the schema dir root are discovered."""
        flat_schema = {
            "type": "object",
            "title": "Flat",
            "version": "1.0.0",
            "properties": {"name": {"type": "string"}},
        }
        flat_path = tmp_path / "flat_entity.json"
        with open(flat_path, "w") as f:
            json.dump(flat_schema, f)

        storage = FileSchemaStorage(schema_dir=tmp_path)
        names = await storage.list_names()
        assert "flat_entity" in names

        versions = await storage.list_versions("flat_entity")
        assert versions == ["1.0.0"]

        loaded = await storage.load("flat_entity", "1.0.0")
        assert loaded is not None
        assert loaded["title"] == "Flat"

    @pytest.mark.asyncio
    async def test_mixed_flat_and_versioned(self, tmp_path, sample_schema):
        """Flat file + versioned directory for different schemas coexist."""
        # Flat file for gadget
        with open(tmp_path / "gadget.json", "w") as f:
            json.dump({**sample_schema, "version": "1.0.0", "title": "Gadget"}, f)

        # Versioned directory for widget
        storage = FileSchemaStorage(schema_dir=tmp_path)
        await storage.save("widget", "1.0.0", {**sample_schema, "title": "Widget"})
        await storage.save("widget", "2.0.0", {**sample_schema, "title": "Widget v2"})

        names = await storage.list_names()
        assert "gadget" in names
        assert "widget" in names

        assert await storage.list_versions("gadget") == ["1.0.0"]
        assert await storage.list_versions("widget") == ["1.0.0", "2.0.0"]

    @pytest.mark.asyncio
    async def test_save_overwrites_existing(self, storage, sample_schema):
        await storage.save("widget", "1.0.0", {**sample_schema, "title": "Original"})
        await storage.save("widget", "1.0.0", {**sample_schema, "title": "Updated"})
        loaded = await storage.load("widget", "1.0.0")
        assert loaded["title"] == "Updated"

    @pytest.mark.asyncio
    async def test_list_versions_empty_schema(self, storage):
        versions = await storage.list_versions("nonexistent")
        assert versions == []
