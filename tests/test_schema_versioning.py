"""Tests for semver utilities and SchemaRegistry version management."""

import pytest
from pydantic import BaseModel

from slip_stream.core.schema.versioning import (
    compare_versions,
    is_valid_semver,
    latest_version,
    parse_semver,
    sort_versions,
)
from slip_stream.core.schema.registry import SchemaRegistry


# ---------------------------------------------------------------------------
# Semver utilities
# ---------------------------------------------------------------------------


class TestParseSemver:
    def test_valid(self):
        assert parse_semver("1.2.3") == (1, 2, 3)

    def test_zeros(self):
        assert parse_semver("0.0.0") == (0, 0, 0)

    def test_large_numbers(self):
        assert parse_semver("10.200.3000") == (10, 200, 3000)

    def test_whitespace_stripped(self):
        assert parse_semver("  1.0.0  ") == (1, 0, 0)

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid semver"):
            parse_semver("abc")

    def test_two_parts_raises(self):
        with pytest.raises(ValueError, match="Invalid semver"):
            parse_semver("1.0")

    def test_four_parts_raises(self):
        with pytest.raises(ValueError, match="Invalid semver"):
            parse_semver("1.0.0.0")

    def test_prerelease_rejected(self):
        with pytest.raises(ValueError, match="Invalid semver"):
            parse_semver("1.0.0-beta")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Invalid semver"):
            parse_semver("")


class TestIsValidSemver:
    def test_valid(self):
        assert is_valid_semver("1.0.0") is True

    def test_invalid(self):
        assert is_valid_semver("nope") is False

    def test_partial(self):
        assert is_valid_semver("1.0") is False


class TestCompareVersions:
    def test_less_than(self):
        assert compare_versions("1.0.0", "2.0.0") == -1

    def test_equal(self):
        assert compare_versions("1.0.0", "1.0.0") == 0

    def test_greater_than(self):
        assert compare_versions("2.0.0", "1.0.0") == 1

    def test_minor_comparison(self):
        assert compare_versions("1.1.0", "1.2.0") == -1

    def test_patch_comparison(self):
        assert compare_versions("1.0.1", "1.0.2") == -1

    def test_ten_vs_nine(self):
        """Semver comparison must beat string sorting: 1.10.0 > 1.9.0."""
        assert compare_versions("1.10.0", "1.9.0") == 1


class TestSortVersions:
    def test_basic_sort(self):
        assert sort_versions(["2.0.0", "1.0.0", "1.1.0"]) == [
            "1.0.0",
            "1.1.0",
            "2.0.0",
        ]

    def test_single(self):
        assert sort_versions(["1.0.0"]) == ["1.0.0"]

    def test_empty(self):
        assert sort_versions([]) == []

    def test_already_sorted(self):
        versions = ["1.0.0", "1.1.0", "2.0.0"]
        assert sort_versions(versions) == versions

    def test_ten_vs_nine_order(self):
        """String sort would give wrong result: '1.10.0' < '1.9.0'."""
        assert sort_versions(["1.9.0", "1.10.0"]) == ["1.9.0", "1.10.0"]

    def test_invalid_versions_at_end(self):
        result = sort_versions(["1.0.0", "bad", "2.0.0"])
        assert result == ["1.0.0", "2.0.0", "bad"]


class TestLatestVersion:
    def test_basic(self):
        assert latest_version(["1.0.0", "2.0.0", "1.5.0"]) == "2.0.0"

    def test_ten_vs_nine(self):
        assert latest_version(["1.0.0", "1.10.0", "1.9.0"]) == "1.10.0"

    def test_single(self):
        assert latest_version(["3.0.0"]) == "3.0.0"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            latest_version([])


# ---------------------------------------------------------------------------
# SchemaRegistry version-aware behavior
# ---------------------------------------------------------------------------


class TestRegistryVersioning:
    """Tests for SchemaRegistry's version management enhancements."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        SchemaRegistry.reset()
        yield
        SchemaRegistry.reset()

    def test_get_schema_latest_uses_semver(self, tmp_path):
        """Ensure 'latest' uses semver, not string sort."""
        registry = SchemaRegistry(schema_dir=tmp_path)
        registry.register_schema(
            "test_entity",
            {"type": "object", "properties": {"name": {"type": "string"}}},
            version="1.9.0",
        )
        registry.register_schema(
            "test_entity",
            {"type": "object", "properties": {"name": {"type": "string"}, "extra": {"type": "string"}}},
            version="1.10.0",
        )
        schema = registry.get_schema("test_entity", "latest")
        assert "extra" in schema.get("properties", {})

    def test_get_all_versions(self, tmp_path):
        registry = SchemaRegistry(schema_dir=tmp_path)
        registry.register_schema("versioned", {"type": "object", "properties": {}}, "2.0.0")
        registry.register_schema("versioned", {"type": "object", "properties": {}}, "1.0.0")
        registry.register_schema("versioned", {"type": "object", "properties": {}}, "1.5.0")
        assert registry.get_all_versions("versioned") == ["1.0.0", "1.5.0", "2.0.0"]

    def test_get_all_versions_unknown_schema_raises(self, tmp_path):
        registry = SchemaRegistry(schema_dir=tmp_path)
        with pytest.raises(ValueError, match="not found"):
            registry.get_all_versions("nonexistent")

    def test_get_latest_version(self, tmp_path):
        registry = SchemaRegistry(schema_dir=tmp_path)
        registry.register_schema("v_test", {"type": "object", "properties": {}}, "3.0.0")
        registry.register_schema("v_test", {"type": "object", "properties": {}}, "1.0.0")
        assert registry.get_latest_version("v_test") == "3.0.0"

    def test_get_model_for_version(self, tmp_path):
        registry = SchemaRegistry(schema_dir=tmp_path)
        registry.register_schema(
            "mtest",
            {"type": "object", "version": "1.0.0", "required": ["name"], "properties": {"name": {"type": "string"}}},
            version="1.0.0",
        )
        doc_model, create_model, update_model = registry.get_model_for_version("mtest", "1.0.0")
        assert issubclass(doc_model, BaseModel)
        assert issubclass(create_model, BaseModel)
        assert issubclass(update_model, BaseModel)

    def test_model_cache_returns_same_objects(self, tmp_path):
        registry = SchemaRegistry(schema_dir=tmp_path)
        registry.register_schema(
            "cached",
            {"type": "object", "version": "1.0.0", "properties": {"name": {"type": "string"}}},
            version="1.0.0",
        )
        triple1 = registry.get_model_for_version("cached", "1.0.0")
        triple2 = registry.get_model_for_version("cached", "1.0.0")
        assert triple1[0] is triple2[0]
        assert triple1[1] is triple2[1]
        assert triple1[2] is triple2[2]

    def test_model_cache_latest_resolves(self, tmp_path):
        """'latest' should resolve to concrete version and share cache."""
        registry = SchemaRegistry(schema_dir=tmp_path)
        registry.register_schema(
            "latest_test",
            {"type": "object", "version": "1.0.0", "properties": {"x": {"type": "integer"}}},
            version="1.0.0",
        )
        triple_explicit = registry.get_model_for_version("latest_test", "1.0.0")
        triple_latest = registry.get_model_for_version("latest_test", "latest")
        assert triple_explicit[0] is triple_latest[0]

    def test_existing_schemas_use_semver(self, schema_dir):
        """Pre-loaded schemas (widget, gadget) still work with semver."""
        registry = SchemaRegistry(schema_dir=schema_dir)
        assert registry.get_latest_version("widget") == "1.0.0"
        assert registry.get_all_versions("widget") == ["1.0.0"]

    def test_model_cache_cleared_on_reset(self, tmp_path):
        registry = SchemaRegistry(schema_dir=tmp_path)
        registry.register_schema(
            "reset_test",
            {"type": "object", "version": "1.0.0", "properties": {}},
            version="1.0.0",
        )
        _ = registry.get_model_for_version("reset_test", "1.0.0")
        assert len(registry._model_cache) > 0
        SchemaRegistry.reset()
        assert SchemaRegistry._model_cache == {}
