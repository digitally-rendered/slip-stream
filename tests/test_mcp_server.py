"""Tests for the MCP server tools."""

import json
from pathlib import Path

import pytest

from slip_stream.core.schema.registry import SchemaRegistry
from slip_stream.mcp.server import create_mcp_server, _extract_refs_from_schema


@pytest.fixture(autouse=True)
def _reset():
    SchemaRegistry.reset()
    yield
    SchemaRegistry.reset()


@pytest.fixture
def registry(tmp_path):
    reg = SchemaRegistry(schema_dir=tmp_path)
    reg.register_schema(
        "widget",
        {
            "type": "object",
            "version": "1.0.0",
            "required": ["name"],
            "properties": {
                "id": {"type": "string", "format": "uuid"},
                "entity_id": {"type": "string", "format": "uuid"},
                "schema_version": {"type": "string"},
                "name": {"type": "string"},
                "color": {"type": "string", "default": "blue"},
            },
        },
        version="1.0.0",
    )
    reg.register_schema(
        "gadget",
        {
            "type": "object",
            "version": "1.0.0",
            "required": ["label"],
            "properties": {
                "label": {"type": "string"},
            },
        },
        version="1.0.0",
    )
    return reg


@pytest.fixture
def server(registry):
    return create_mcp_server(
        schema_registry=registry,
        base_url="http://localhost:8000",
    )


class TestMcpServerCreation:

    def test_creates_server(self, server):
        assert server is not None

    @pytest.mark.asyncio
    async def test_list_tools(self, server):
        tools = await server.list_tools()
        tool_names = [t.name for t in tools]
        assert "list_schemas" in tool_names
        assert "get_schema" in tool_names
        assert "get_schema_dag" in tool_names
        assert "list_versions" in tool_names
        assert "describe_entity" in tool_names
        assert "query_rest_api" in tool_names
        assert "query_graphql" in tool_names


class TestMcpListSchemas:

    @pytest.mark.asyncio
    async def test_list_schemas(self, server):
        result = await server.call_tool("list_schemas", {})
        assert len(result) == 1
        data = json.loads(result[0].text)
        names = [s["name"] for s in data["schemas"]]
        assert "widget" in names
        assert "gadget" in names


class TestMcpGetSchema:

    @pytest.mark.asyncio
    async def test_get_schema(self, server):
        result = await server.call_tool("get_schema", {"name": "widget"})
        data = json.loads(result[0].text)
        assert data["name"] == "widget"
        assert "properties" in data["schema"]

    @pytest.mark.asyncio
    async def test_get_schema_specific_version(self, server):
        result = await server.call_tool(
            "get_schema", {"name": "widget", "version": "1.0.0"}
        )
        data = json.loads(result[0].text)
        assert data["version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_get_schema_not_found(self, server):
        result = await server.call_tool(
            "get_schema", {"name": "nonexistent"}
        )
        assert "Error" in result[0].text


class TestMcpGetSchemaDag:

    @pytest.mark.asyncio
    async def test_get_dag(self, server):
        result = await server.call_tool("get_schema_dag", {})
        data = json.loads(result[0].text)
        assert "dag" in data
        names = [n["name"] for n in data["dag"]]
        assert "widget" in names


class TestMcpListVersions:

    @pytest.mark.asyncio
    async def test_list_versions(self, server):
        result = await server.call_tool(
            "list_versions", {"name": "widget"}
        )
        data = json.loads(result[0].text)
        assert data["name"] == "widget"
        assert "1.0.0" in data["versions"]


class TestMcpDescribeEntity:

    @pytest.mark.asyncio
    async def test_describe_entity(self, server):
        result = await server.call_tool(
            "describe_entity", {"name": "widget"}
        )
        data = json.loads(result[0].text)
        assert data["name"] == "widget"
        assert data["user_fields"] > 0
        field_names = [f["name"] for f in data["fields"]]
        assert "name" in field_names
        assert "color" in field_names

    @pytest.mark.asyncio
    async def test_describe_entity_includes_api_endpoints(self, server):
        result = await server.call_tool(
            "describe_entity", {"name": "widget"}
        )
        data = json.loads(result[0].text)
        assert "api_endpoints" in data
        assert "rest" in data["api_endpoints"]
        assert "list" in data["api_endpoints"]["rest"]


class TestExtractRefsFromSchema:

    def test_no_refs(self):
        schema = {"type": "object", "properties": {}}
        assert _extract_refs_from_schema(schema) == []

    def test_file_ref(self):
        schema = {"properties": {"addr": {"$ref": "address.json"}}}
        refs = _extract_refs_from_schema(schema)
        assert "address" in refs

    def test_internal_ref_skipped(self):
        schema = {"properties": {"x": {"$ref": "#/definitions/Foo"}}}
        assert _extract_refs_from_schema(schema) == []
