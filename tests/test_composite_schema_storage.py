"""Tests for CompositeSchemaStorage — chained adapter with fallback."""

from unittest.mock import AsyncMock

import pytest

from slip_stream.adapters.persistence.schema.composite_storage import (
    CompositeSchemaStorage,
)


def _make_adapter(**overrides):
    """Create a mock storage adapter with async methods."""
    adapter = AsyncMock()
    adapter.save = AsyncMock()
    adapter.load = AsyncMock(return_value=None)
    adapter.load_latest = AsyncMock(return_value=None)
    adapter.list_versions = AsyncMock(return_value=[])
    adapter.list_names = AsyncMock(return_value=[])
    adapter.exists = AsyncMock(return_value=False)
    for k, v in overrides.items():
        setattr(adapter, k, AsyncMock(return_value=v))
    return adapter


class TestCompositeInit:

    def test_requires_at_least_one_adapter(self):
        with pytest.raises(ValueError, match="at least one"):
            CompositeSchemaStorage([])


class TestCompositeLoad:

    @pytest.mark.asyncio
    async def test_returns_from_first_adapter(self):
        a1 = _make_adapter(load={"type": "object"})
        a2 = _make_adapter()
        composite = CompositeSchemaStorage([a1, a2])

        result = await composite.load("widget", "1.0.0")
        assert result == {"type": "object"}
        a2.load.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_through_to_second_adapter(self):
        a1 = _make_adapter()
        a2 = _make_adapter(load={"type": "object", "from": "remote"})
        composite = CompositeSchemaStorage([a1, a2])

        result = await composite.load("widget", "1.0.0")
        assert result == {"type": "object", "from": "remote"}

    @pytest.mark.asyncio
    async def test_backfills_earlier_adapter(self):
        a1 = _make_adapter()
        a2 = _make_adapter(load={"type": "object"})
        composite = CompositeSchemaStorage([a1, a2])

        await composite.load("widget", "1.0.0")
        a1.save.assert_called_once_with("widget", "1.0.0", {"type": "object"})

    @pytest.mark.asyncio
    async def test_returns_none_when_all_miss(self):
        a1 = _make_adapter()
        a2 = _make_adapter()
        composite = CompositeSchemaStorage([a1, a2])

        result = await composite.load("widget", "1.0.0")
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_failed_adapter(self):
        a1 = _make_adapter()
        a1.load = AsyncMock(side_effect=ConnectionError("down"))
        a2 = _make_adapter(load={"type": "object"})
        composite = CompositeSchemaStorage([a1, a2])

        result = await composite.load("widget", "1.0.0")
        assert result == {"type": "object"}


class TestCompositeLoadLatest:

    @pytest.mark.asyncio
    async def test_returns_from_first_adapter(self):
        a1 = _make_adapter(load_latest=("2.0.0", {"type": "object"}))
        a2 = _make_adapter()
        composite = CompositeSchemaStorage([a1, a2])

        result = await composite.load_latest("widget")
        assert result == ("2.0.0", {"type": "object"})

    @pytest.mark.asyncio
    async def test_backfills_on_fallthrough(self):
        a1 = _make_adapter()
        a2 = _make_adapter(load_latest=("1.0.0", {"type": "object"}))
        composite = CompositeSchemaStorage([a1, a2])

        result = await composite.load_latest("widget")
        assert result is not None
        a1.save.assert_called_once_with("widget", "1.0.0", {"type": "object"})


class TestCompositeSave:

    @pytest.mark.asyncio
    async def test_writes_to_all_adapters(self):
        a1 = _make_adapter()
        a2 = _make_adapter()
        composite = CompositeSchemaStorage([a1, a2])

        await composite.save("widget", "1.0.0", {"type": "object"})
        a1.save.assert_called_once()
        a2.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_continues_on_failure_in_write_through(self):
        a1 = _make_adapter()
        a1.save = AsyncMock(side_effect=ConnectionError("down"))
        a2 = _make_adapter()
        composite = CompositeSchemaStorage([a1, a2], write_through=True)

        await composite.save("widget", "1.0.0", {"type": "object"})
        a2.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_on_failure_without_write_through(self):
        a1 = _make_adapter()
        a1.save = AsyncMock(side_effect=ConnectionError("down"))
        a2 = _make_adapter()
        composite = CompositeSchemaStorage([a1, a2], write_through=False)

        with pytest.raises(ConnectionError):
            await composite.save("widget", "1.0.0", {"type": "object"})


class TestCompositeListVersions:

    @pytest.mark.asyncio
    async def test_merges_versions_from_all_adapters(self):
        a1 = _make_adapter(list_versions=["1.0.0"])
        a2 = _make_adapter(list_versions=["2.0.0", "1.0.0"])
        composite = CompositeSchemaStorage([a1, a2])

        versions = await composite.list_versions("widget")
        assert versions == ["1.0.0", "2.0.0"]

    @pytest.mark.asyncio
    async def test_deduplicates_versions(self):
        a1 = _make_adapter(list_versions=["1.0.0", "2.0.0"])
        a2 = _make_adapter(list_versions=["1.0.0", "3.0.0"])
        composite = CompositeSchemaStorage([a1, a2])

        versions = await composite.list_versions("widget")
        assert versions == ["1.0.0", "2.0.0", "3.0.0"]


class TestCompositeListNames:

    @pytest.mark.asyncio
    async def test_merges_names(self):
        a1 = _make_adapter(list_names=["widget"])
        a2 = _make_adapter(list_names=["gadget", "widget"])
        composite = CompositeSchemaStorage([a1, a2])

        names = await composite.list_names()
        assert names == ["gadget", "widget"]


class TestCompositeExists:

    @pytest.mark.asyncio
    async def test_returns_true_if_any_adapter(self):
        a1 = _make_adapter()
        a2 = _make_adapter(exists=True)
        composite = CompositeSchemaStorage([a1, a2])

        assert await composite.exists("widget", "1.0.0") is True

    @pytest.mark.asyncio
    async def test_returns_false_if_none(self):
        a1 = _make_adapter()
        a2 = _make_adapter()
        composite = CompositeSchemaStorage([a1, a2])

        assert await composite.exists("widget", "1.0.0") is False
