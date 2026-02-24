"""Tests for JSON Schema $ref resolver."""

import json
from pathlib import Path

import pytest

from slip_stream.core.schema.ref_resolver import RefResolver


@pytest.fixture
def base_path():
    return Path(__file__).parent / "sample_schemas"


@pytest.fixture
def resolver(base_path):
    return RefResolver(base_path=base_path)


class TestInternalRefs:
    def test_internal_ref_resolved(self, resolver):
        schema = {
            "definitions": {
                "Color": {
                    "type": "object",
                    "properties": {"r": {"type": "integer"}},
                }
            },
            "properties": {"color": {"$ref": "#/definitions/Color"}},
        }
        result = resolver.resolve(schema)
        assert result["properties"]["color"]["type"] == "object"
        assert "r" in result["properties"]["color"]["properties"]
        assert "$ref" not in result["properties"]["color"]

    def test_nested_internal_ref(self, resolver):
        schema = {
            "definitions": {
                "Inner": {"type": "string"},
                "Outer": {
                    "type": "object",
                    "properties": {"value": {"$ref": "#/definitions/Inner"}},
                },
            },
            "properties": {"item": {"$ref": "#/definitions/Outer"}},
        }
        result = resolver.resolve(schema)
        assert result["properties"]["item"]["properties"]["value"]["type"] == "string"

    def test_missing_internal_ref_raises(self, resolver):
        schema = {"properties": {"x": {"$ref": "#/definitions/Missing"}}}
        with pytest.raises(ValueError, match="not found"):
            resolver.resolve(schema)


class TestFileRefs:
    def test_file_ref_resolved(self, resolver):
        schema = {"properties": {"addr": {"$ref": "definitions/address.json"}}}
        result = resolver.resolve(schema)
        assert result["properties"]["addr"]["type"] == "object"
        assert "street" in result["properties"]["addr"]["properties"]
        assert "$ref" not in result["properties"]["addr"]

    def test_missing_file_raises(self, resolver):
        schema = {"properties": {"x": {"$ref": "nonexistent.json"}}}
        with pytest.raises(ValueError, match="not found"):
            resolver.resolve(schema)

    def test_no_base_path_raises(self):
        resolver = RefResolver(base_path=None)
        schema = {"properties": {"x": {"$ref": "some_file.json"}}}
        with pytest.raises(ValueError, match="no base_path"):
            resolver.resolve(schema)

    def test_file_with_fragment(self, base_path, tmp_path):
        # Create a temp file with definitions
        shared = {
            "definitions": {
                "Status": {"type": "string", "enum": ["active", "inactive"]}
            }
        }
        shared_path = tmp_path / "shared.json"
        with open(shared_path, "w") as f:
            json.dump(shared, f)

        resolver = RefResolver(base_path=tmp_path)
        schema = {"properties": {"status": {"$ref": "shared.json#/definitions/Status"}}}
        result = resolver.resolve(schema)
        assert result["properties"]["status"]["type"] == "string"
        assert result["properties"]["status"]["enum"] == ["active", "inactive"]


class TestEdgeCases:
    def test_no_refs_passthrough(self, resolver):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        result = resolver.resolve(schema)
        assert result == schema

    def test_original_not_mutated(self, resolver):
        original = {
            "definitions": {"X": {"type": "integer"}},
            "properties": {"val": {"$ref": "#/definitions/X"}},
        }
        import copy

        snapshot = copy.deepcopy(original)
        resolver.resolve(original)
        assert original == snapshot

    def test_ref_in_array_items(self, resolver):
        schema = {
            "definitions": {"Tag": {"type": "string"}},
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {"$ref": "#/definitions/Tag"},
                }
            },
        }
        result = resolver.resolve(schema)
        assert result["properties"]["tags"]["items"]["type"] == "string"

    def test_circular_ref_raises(self, resolver):
        schema = {
            "definitions": {
                "A": {"$ref": "#/definitions/B"},
                "B": {"$ref": "#/definitions/A"},
            },
            "properties": {"x": {"$ref": "#/definitions/A"}},
        }
        with pytest.raises(ValueError, match="Circular"):
            resolver.resolve(schema)

    def test_file_cache_reused(self, resolver):
        """Loading the same file twice uses cache."""
        schema = {
            "properties": {
                "addr1": {"$ref": "definitions/address.json"},
                "addr2": {"$ref": "definitions/address.json"},
            }
        }
        result = resolver.resolve(schema)
        # Both should resolve independently (deep copies)
        assert result["properties"]["addr1"]["type"] == "object"
        assert result["properties"]["addr2"]["type"] == "object"


class TestModelGeneration:
    """End-to-end: schema with $ref produces correct Pydantic model."""

    @pytest.fixture(autouse=True)
    def _reset_registry(self):
        from slip_stream.core.schema.registry import SchemaRegistry

        SchemaRegistry.reset()
        yield
        SchemaRegistry.reset()

    def test_model_from_schema_with_refs(self, base_path):
        """Load widget_with_ref.json and generate models — $ref fields become dict."""
        from slip_stream.core.schema.ref_resolver import RefResolver
        from slip_stream.core.schema.registry import SchemaRegistry

        # Load the schema and resolve refs
        schema_path = base_path / "widget_with_ref.json"
        with open(schema_path) as f:
            raw_schema = json.load(f)

        resolver = RefResolver(base_path=base_path)
        resolved = resolver.resolve(raw_schema)

        # Register the resolved schema
        registry = SchemaRegistry()
        registry.register_schema("widget_with_ref", resolved, version="1.0.0")

        # Generate models
        doc_model = registry.generate_document_model("widget_with_ref", "1.0.0")
        create_model = registry.generate_create_model("widget_with_ref", "1.0.0")

        # Verify fields exist
        field_names = set(doc_model.model_fields.keys())
        assert "name" in field_names
        assert "color" in field_names
        assert "address" in field_names

        create_fields = set(create_model.model_fields.keys())
        assert "name" in create_fields
        assert "color" in create_fields
        assert "address" in create_fields
