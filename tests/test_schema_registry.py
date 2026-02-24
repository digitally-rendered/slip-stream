"""Tests for the SchemaRegistry."""

import pytest
from pydantic import BaseModel

from slip_stream.core.domain.base import BaseDocument
from slip_stream.core.schema.registry import SchemaRegistry


class TestSchemaRegistry:
    """Tests for schema loading and model generation."""

    def test_loads_schemas_from_directory(self, registry):
        """SchemaRegistry discovers and loads JSON schema files."""
        names = registry.get_schema_names()
        assert "widget" in names

    def test_get_schema_returns_dict(self, registry):
        """get_schema returns the raw schema as a dict."""
        schema = registry.get_schema("widget")
        assert schema["title"] == "Widget"
        assert "properties" in schema

    def test_get_schema_not_found(self, registry):
        """get_schema raises ValueError for unknown schemas."""
        with pytest.raises(ValueError, match="Schema nonexistent not found"):
            registry.get_schema("nonexistent")

    def test_generate_document_model(self, registry):
        """generate_document_model returns a BaseDocument subclass."""
        model = registry.generate_document_model("widget")
        assert issubclass(model, BaseDocument)
        assert "name" in model.model_fields
        assert "color" in model.model_fields
        assert "weight" in model.model_fields

    def test_generate_create_model(self, registry):
        """generate_create_model returns a BaseModel (not BaseDocument)."""
        model = registry.generate_create_model("widget")
        assert issubclass(model, BaseModel)
        assert not issubclass(model, BaseDocument)
        assert "name" in model.model_fields

    def test_generate_update_model_all_optional(self, registry):
        """generate_update_model makes all fields optional."""
        model = registry.generate_update_model("widget")
        assert issubclass(model, BaseModel)
        # All fields should default to None
        instance = model()
        assert instance.name is None  # type: ignore[attr-defined]
        assert instance.color is None  # type: ignore[attr-defined]

    def test_audit_fields_excluded_from_create_model(self, registry):
        """Create model should not include audit fields like entity_id, created_at."""
        model = registry.generate_create_model("widget")
        fields = model.model_fields
        assert "entity_id" not in fields
        assert "created_at" not in fields
        assert "record_version" not in fields

    def test_register_schema_programmatically(self, registry):
        """register_schema allows adding schemas at runtime."""
        registry.register_schema(
            "gadget",
            {
                "title": "Gadget",
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                },
                "required": ["label"],
            },
        )
        assert "gadget" in registry.get_schema_names()
        model = registry.generate_document_model("gadget")
        assert "label" in model.model_fields

    def test_singleton_returns_same_instance(self, schema_dir):
        """SchemaRegistry is a singleton with the same schema_dir."""
        r1 = SchemaRegistry(schema_dir=schema_dir)
        r2 = SchemaRegistry(schema_dir=schema_dir)
        assert r1 is r2

    def test_reset_clears_singleton(self, schema_dir):
        """reset() allows creating a fresh instance."""
        r1 = SchemaRegistry(schema_dir=schema_dir)
        SchemaRegistry.reset()
        r2 = SchemaRegistry(schema_dir=schema_dir)
        assert r1 is not r2

    def test_array_field_type(self, registry):
        """Array fields are correctly typed as List."""
        model = registry.generate_document_model("widget")
        instance = model(
            entity_id="12345678-1234-1234-1234-123456789012",
            name="Test",
            tags=["a", "b"],
        )
        assert instance.tags == ["a", "b"]  # type: ignore[attr-defined]

    def test_boolean_default(self, registry):
        """Boolean fields with defaults preserve them in document model."""
        model = registry.generate_document_model("widget")
        instance = model(
            entity_id="12345678-1234-1234-1234-123456789012",
            name="Test",
        )
        assert instance.active is True  # type: ignore[attr-defined]
