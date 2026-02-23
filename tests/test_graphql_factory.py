"""Tests for the GraphQL endpoint factory."""

import pytest
from pydantic import BaseModel

from slip_stream.core.schema.registry import SchemaRegistry
from slip_stream.container import EntityContainer


@pytest.fixture(autouse=True)
def _reset():
    SchemaRegistry.reset()
    yield
    SchemaRegistry.reset()


@pytest.fixture
def registry_with_schema(tmp_path):
    registry = SchemaRegistry(schema_dir=tmp_path)
    registry.register_schema(
        "widget",
        {
            "type": "object",
            "version": "1.0.0",
            "required": ["name"],
            "properties": {
                "id": {"type": "string", "format": "uuid"},
                "entity_id": {"type": "string", "format": "uuid"},
                "schema_version": {"type": "string"},
                "record_version": {"type": "integer"},
                "created_at": {"type": "string", "format": "date-time"},
                "updated_at": {"type": "string", "format": "date-time"},
                "name": {"type": "string"},
                "color": {"type": "string", "default": "blue"},
                "weight": {"type": "number", "default": 0},
                "tags": {"type": "array", "items": {"type": "string"}},
                "active": {"type": "boolean", "default": True},
            },
        },
        version="1.0.0",
    )
    return registry


@pytest.fixture
def container(registry_with_schema):
    container = EntityContainer()
    container.resolve_all(["widget"])
    return container


class TestGraphQLFactory:

    def test_import(self):
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory
        factory = GraphQLFactory()
        assert factory is not None

    def test_create_entity_type(self, registry_with_schema):
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        factory = GraphQLFactory()
        schema = registry_with_schema.get_schema("widget", "1.0.0")
        properties = schema.get("properties", {})
        entity_type = factory._create_entity_type("Widget", properties, "widget")

        assert entity_type is not None
        assert hasattr(entity_type, "__strawberry_definition__")

    def test_create_input_types(self, registry_with_schema):
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        factory = GraphQLFactory()
        schema = registry_with_schema.get_schema("widget", "1.0.0")
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        create_input, update_input = factory._create_input_types(
            "Widget", properties, required
        )

        assert create_input is not None
        assert update_input is not None

    def test_create_graphql_router(self, container, registry_with_schema):
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        factory = GraphQLFactory()
        router = factory.create_graphql_router(
            container=container,
            get_db=lambda: None,
            schema_registry=registry_with_schema,
        )

        assert router is not None

    def test_to_pascal(self):
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        factory = GraphQLFactory()
        assert factory._to_pascal("widget") == "Widget"
        assert factory._to_pascal("order_item") == "OrderItem"
        assert factory._to_pascal("my_long_name") == "MyLongName"


class TestExtractRefs:

    def test_no_refs(self):
        from slip_stream.adapters.api.graphql_factory import _extract_refs

        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        assert _extract_refs(schema) == []

    def test_file_ref(self):
        from slip_stream.adapters.api.graphql_factory import _extract_refs

        schema = {
            "type": "object",
            "properties": {
                "address": {"$ref": "definitions/address.json"},
            },
        }
        refs = _extract_refs(schema)
        assert "address" in refs

    def test_internal_ref_ignored(self):
        from slip_stream.adapters.api.graphql_factory import _extract_refs

        schema = {
            "type": "object",
            "definitions": {"Status": {"type": "string"}},
            "properties": {
                "status": {"$ref": "#/definitions/Status"},
            },
        }
        # Internal refs (#/...) should not appear as dependencies
        refs = _extract_refs(schema)
        assert refs == []

    def test_nested_refs(self):
        from slip_stream.adapters.api.graphql_factory import _extract_refs

        schema = {
            "type": "object",
            "properties": {
                "billing": {"$ref": "billing.json"},
                "shipping": {
                    "type": "object",
                    "properties": {
                        "address": {"$ref": "address.json"},
                    },
                },
            },
        }
        refs = _extract_refs(schema)
        assert sorted(refs) == ["address", "billing"]

    def test_deduplicates(self):
        from slip_stream.adapters.api.graphql_factory import _extract_refs

        schema = {
            "type": "object",
            "properties": {
                "a": {"$ref": "shared.json"},
                "b": {"$ref": "shared.json"},
            },
        }
        refs = _extract_refs(schema)
        assert refs == ["shared"]
