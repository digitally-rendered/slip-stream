"""Tests for HttpSchemaStorage — remote schema registry adapter."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from slip_stream.adapters.persistence.schema.http_storage import HttpSchemaStorage


@pytest.fixture
def mock_httpx_client():
    """Create a mock httpx.AsyncClient."""
    client = AsyncMock()
    return client


@pytest.fixture
def storage(mock_httpx_client):
    """HttpSchemaStorage with a mocked client."""
    s = HttpSchemaStorage("https://registry.example.com/schemas", ttl=60.0)
    s._client = mock_httpx_client
    return s


class TestHttpSchemaStorageLoad:

    @pytest.mark.asyncio
    async def test_load_success(self, storage, mock_httpx_client):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "name": "widget",
            "version": "1.0.0",
            "schema": {"type": "object", "properties": {"name": {"type": "string"}}},
        }
        mock_httpx_client.get = AsyncMock(return_value=resp)

        result = await storage.load("widget", "1.0.0")
        assert result == {"type": "object", "properties": {"name": {"type": "string"}}}
        mock_httpx_client.get.assert_called_with("/widget/1.0.0")

    @pytest.mark.asyncio
    async def test_load_not_found(self, storage, mock_httpx_client):
        resp = MagicMock()
        resp.status_code = 404
        mock_httpx_client.get = AsyncMock(return_value=resp)

        result = await storage.load("nonexistent", "1.0.0")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_uses_cache(self, storage, mock_httpx_client):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"schema": {"type": "object"}}
        mock_httpx_client.get = AsyncMock(return_value=resp)

        await storage.load("widget", "1.0.0")
        await storage.load("widget", "1.0.0")

        assert mock_httpx_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_cache_expires(self, storage, mock_httpx_client):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"schema": {"type": "object"}}
        mock_httpx_client.get = AsyncMock(return_value=resp)

        await storage.load("widget", "1.0.0")

        # Expire the cache by manipulating timestamp
        key = "load:widget:1.0.0"
        ts, val = storage._cache[key]
        storage._cache[key] = (ts - 120, val)  # push back past TTL

        await storage.load("widget", "1.0.0")
        assert mock_httpx_client.get.call_count == 2


class TestHttpSchemaStorageLoadLatest:

    @pytest.mark.asyncio
    async def test_load_latest_success(self, storage, mock_httpx_client):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "name": "widget",
            "version": "2.0.0",
            "schema": {"type": "object", "properties": {"name": {"type": "string"}}},
        }
        mock_httpx_client.get = AsyncMock(return_value=resp)

        result = await storage.load_latest("widget")
        assert result is not None
        version, schema = result
        assert version == "2.0.0"
        assert schema["type"] == "object"

    @pytest.mark.asyncio
    async def test_load_latest_not_found(self, storage, mock_httpx_client):
        resp = MagicMock()
        resp.status_code = 404
        mock_httpx_client.get = AsyncMock(return_value=resp)

        result = await storage.load_latest("nonexistent")
        assert result is None


class TestHttpSchemaStorageListVersions:

    @pytest.mark.asyncio
    async def test_list_versions(self, storage, mock_httpx_client):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "name": "widget",
            "versions": ["1.0.0", "2.0.0"],
            "latest_version": "2.0.0",
        }
        mock_httpx_client.get = AsyncMock(return_value=resp)

        versions = await storage.list_versions("widget")
        assert versions == ["1.0.0", "2.0.0"]

    @pytest.mark.asyncio
    async def test_list_versions_not_found(self, storage, mock_httpx_client):
        resp = MagicMock()
        resp.status_code = 404
        mock_httpx_client.get = AsyncMock(return_value=resp)

        versions = await storage.list_versions("nonexistent")
        assert versions == []


class TestHttpSchemaStorageListNames:

    @pytest.mark.asyncio
    async def test_list_names(self, storage, mock_httpx_client):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "schemas": [
                {"name": "widget", "versions": ["1.0.0"]},
                {"name": "gadget", "versions": ["1.0.0"]},
            ]
        }
        mock_httpx_client.get = AsyncMock(return_value=resp)

        names = await storage.list_names()
        assert sorted(names) == ["gadget", "widget"]


class TestHttpSchemaStorageExists:

    @pytest.mark.asyncio
    async def test_exists_true(self, storage, mock_httpx_client):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"schema": {"type": "object"}}
        mock_httpx_client.get = AsyncMock(return_value=resp)

        assert await storage.exists("widget", "1.0.0") is True

    @pytest.mark.asyncio
    async def test_exists_false(self, storage, mock_httpx_client):
        resp = MagicMock()
        resp.status_code = 404
        mock_httpx_client.get = AsyncMock(return_value=resp)

        assert await storage.exists("widget", "9.9.9") is False


class TestHttpSchemaStorageSave:

    @pytest.mark.asyncio
    async def test_save_is_noop(self, storage):
        # Should not raise
        await storage.save("widget", "1.0.0", {"type": "object"})


class TestHttpSchemaStorageClose:

    @pytest.mark.asyncio
    async def test_close(self, storage, mock_httpx_client):
        mock_httpx_client.aclose = AsyncMock()
        await storage.close()
        mock_httpx_client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_without_client(self):
        s = HttpSchemaStorage("https://example.com")
        await s.close()  # Should not raise
