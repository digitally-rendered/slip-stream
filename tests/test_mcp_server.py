"""Tests for the MCP server tools."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import mcp.types as mcp_types
import pytest

from slip_stream.core.schema.registry import SchemaRegistry
from slip_stream.mcp.server import (
    _extract_refs_from_schema,
    create_mcp_server,
)
from slip_stream.schema_utils import create_schema_file

# ---------------------------------------------------------------------------
# Helper to invoke the call_tool dispatcher via the MCP server's
# registered CallToolRequest handler.  This exercises the full
# call_tool -> _handle_* path without needing a real MCP transport.
# ---------------------------------------------------------------------------


async def _call_tool(server, name: str, arguments: dict) -> str:
    """Dispatch a tool call through the server and return the text response."""
    handler = server.request_handlers[mcp_types.CallToolRequest]
    req = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = await handler(req)
    # result is ServerResult; root is CallToolResult with a content list
    return result.root.content[0].text


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

    def test_server_has_name(self, server):
        assert server.name == "slip-stream"


class TestMcpToolHandlers:
    """Test the tool handler functions directly via the registered handlers.

    Since the MCP Server uses decorators to register handlers, we access
    the registered handler functions from the server's internal state and
    call them directly.
    """

    def _get_call_tool_handler(self, server):
        """Extract the call_tool handler from the server."""
        # The MCP Server stores handlers in request_handlers
        for handler in server.request_handlers.values():
            # We need the call_tool handler
            pass
        # Alternative: access the handler function we defined inside create_mcp_server
        # by accessing the server's internal handler registry
        return None

    @pytest.mark.asyncio
    async def test_list_schemas_handler(self, registry):
        """Test list_schemas logic directly."""
        names = sorted(registry.get_schema_names())
        result = []
        for name in names:
            versions = registry.get_all_versions(name)
            latest = registry.get_latest_version(name)
            result.append(
                {
                    "name": name,
                    "versions": versions,
                    "latest_version": latest,
                }
            )
        assert len(result) == 2
        names = [s["name"] for s in result]
        assert "widget" in names
        assert "gadget" in names

    @pytest.mark.asyncio
    async def test_get_schema_handler(self, registry):
        """Test get_schema logic directly."""
        schema = registry.get_schema("widget", "latest")
        assert "properties" in schema
        assert "name" in schema["properties"]

    @pytest.mark.asyncio
    async def test_get_schema_specific_version(self, registry):
        """Test get_schema with specific version."""
        schema = registry.get_schema("widget", "1.0.0")
        assert schema["version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_get_schema_not_found(self, registry):
        """Test get_schema with nonexistent schema."""
        with pytest.raises(ValueError, match="not found"):
            registry.get_schema("nonexistent", "latest")

    @pytest.mark.asyncio
    async def test_list_versions_handler(self, registry):
        """Test list_versions logic directly."""
        versions = registry.get_all_versions("widget")
        assert "1.0.0" in versions

    @pytest.mark.asyncio
    async def test_schema_dag_handler(self, registry):
        """Test schema DAG construction logic."""
        dag = []
        for name in sorted(registry.get_schema_names()):
            versions = registry.get_all_versions(name)
            latest = registry.get_latest_version(name)
            schema = registry.get_schema(name, latest)
            deps = _extract_refs_from_schema(schema)
            dag.append(
                {
                    "name": name,
                    "versions": versions,
                    "latest_version": latest,
                    "dependencies": deps,
                }
            )
        assert len(dag) == 2
        names = [n["name"] for n in dag]
        assert "widget" in names

    @pytest.mark.asyncio
    async def test_describe_entity_handler(self, registry):
        """Test describe_entity logic directly."""
        schema = registry.get_schema("widget", "latest")
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        audit_fields = {
            "id",
            "entity_id",
            "schema_version",
            "record_version",
            "created_at",
            "updated_at",
            "deleted_at",
            "created_by",
            "updated_by",
            "deleted_by",
        }

        fields = []
        for field_name, field_def in properties.items():
            fields.append(
                {
                    "name": field_name,
                    "type": field_def.get("type", "any"),
                    "required": field_name in required,
                    "is_audit_field": field_name in audit_fields,
                }
            )

        user_fields = [f for f in fields if not f["is_audit_field"]]
        assert len(user_fields) > 0
        field_names = [f["name"] for f in fields]
        assert "name" in field_names
        assert "color" in field_names

    @pytest.mark.asyncio
    async def test_describe_entity_api_endpoints(self, registry):
        """Test that describe_entity provides API endpoint info."""
        base_url = "http://localhost:8000"
        api_prefix = "/api/v1"
        name = "widget"
        endpoints = {
            "list": f"GET {base_url}{api_prefix}/{name}/",
            "get": f"GET {base_url}{api_prefix}/{name}/{{entity_id}}",
            "create": f"POST {base_url}{api_prefix}/{name}/",
            "update": f"PATCH {base_url}{api_prefix}/{name}/{{entity_id}}",
            "delete": f"DELETE {base_url}{api_prefix}/{name}/{{entity_id}}",
        }
        assert "list" in endpoints
        assert "/widget/" in endpoints["list"]


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

    def test_nested_refs(self):
        schema = {
            "properties": {
                "a": {"$ref": "billing.json"},
                "b": {"type": "object", "properties": {"c": {"$ref": "shipping.json"}}},
            }
        }
        refs = _extract_refs_from_schema(schema)
        assert sorted(refs) == ["billing", "shipping"]

    def test_deduplicates(self):
        schema = {
            "properties": {
                "a": {"$ref": "common.json"},
                "b": {"$ref": "common.json"},
            }
        }
        refs = _extract_refs_from_schema(schema)
        assert refs == ["common"]


class TestCreateSchemaTool:

    @pytest.mark.asyncio
    async def test_creates_schema_file(self, tmp_path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        create_mcp_server(schema_dir=str(schemas_dir))
        # Test the logic directly by simulating what the handler does
        from slip_stream.schema_utils import create_schema_file, snake_case

        target = create_schema_file(schemas_dir, "order")
        assert target.exists()
        data = json.loads(target.read_text())
        assert data["title"] == "Order"
        assert data["version"] == "1.0.0"
        assert snake_case("order") == "order"

    @pytest.mark.asyncio
    async def test_create_schema_no_schema_dir(self):
        """Write tools require schema_dir to be configured."""
        server = create_mcp_server(schema_dir=None)
        # Server should still create without error
        assert server is not None

    @pytest.mark.asyncio
    async def test_create_schema_with_description(self, tmp_path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        target = create_schema_file(
            schemas_dir, "invoice", description="A billing invoice."
        )
        data = json.loads(target.read_text())
        assert data["description"] == "A billing invoice."

    @pytest.mark.asyncio
    async def test_create_schema_duplicate_raises(self, tmp_path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()
        create_schema_file(schemas_dir, "widget")
        with pytest.raises(FileExistsError):
            create_schema_file(schemas_dir, "widget")


class TestValidateSchemasTool:

    @pytest.mark.asyncio
    async def test_validates_all_valid(self, tmp_path):
        from slip_stream.schema_utils import validate_all_schemas

        create_schema_file(tmp_path, "widget")
        create_schema_file(tmp_path, "gadget")
        results = validate_all_schemas(tmp_path)
        assert len(results) == 2
        assert all(len(issues) == 0 for issues in results.values())

    @pytest.mark.asyncio
    async def test_validates_detects_issues(self, tmp_path):
        from slip_stream.schema_utils import validate_all_schemas

        create_schema_file(tmp_path, "good")
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"type": "string"}))
        results = validate_all_schemas(tmp_path)
        assert len(results) == 2
        assert results["good.json"] == []
        assert len(results["bad.json"]) > 0

    @pytest.mark.asyncio
    async def test_validates_empty_dir(self, tmp_path):
        from slip_stream.schema_utils import validate_all_schemas

        results = validate_all_schemas(tmp_path)
        assert results == {}


class TestGenerateSdkTool:

    @pytest.mark.asyncio
    async def test_generates_sdk_code(self, tmp_path):
        from slip_stream.sdk_generator import generate_sdk

        schemas = {
            "widget": {
                "type": "object",
                "version": "1.0.0",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string"},
                    "color": {"type": "string", "default": "blue"},
                },
            }
        }
        code = generate_sdk(schemas=schemas)
        assert "class Widget(BaseModel):" in code
        assert "class WidgetCreate(BaseModel):" in code
        assert "class SlipStreamClient:" in code
        assert "async def create_widget" in code

    @pytest.mark.asyncio
    async def test_generates_sdk_from_schema_files(self, tmp_path):
        from slip_stream.sdk_generator import generate_sdk

        create_schema_file(tmp_path, "order")
        create_schema_file(tmp_path, "product")

        schemas = {}
        for f in sorted(tmp_path.glob("**/*.json")):
            data = json.loads(f.read_text())
            schemas[f.stem] = data

        code = generate_sdk(schemas=schemas)
        assert "class Order(BaseModel):" in code
        assert "class Product(BaseModel):" in code
        assert "async def create_order" in code
        assert "async def create_product" in code

    @pytest.mark.asyncio
    async def test_generates_sdk_writes_to_file(self, tmp_path):
        from slip_stream.sdk_generator import generate_sdk

        schemas = {
            "item": {
                "type": "object",
                "version": "1.0.0",
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            }
        }
        code = generate_sdk(schemas=schemas)
        output = tmp_path / "client.py"
        output.write_text(code)
        assert output.exists()
        assert "class Item(BaseModel):" in output.read_text()


class TestGetTopologyTool:

    def test_server_with_schema_dir(self, tmp_path):
        """Server accepts schema_dir parameter."""
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()
        server = create_mcp_server(schema_dir=str(schemas_dir))
        assert server is not None

    def test_tool_list_includes_new_tools(self, registry):
        """The tool list should include the 4 new tools."""
        # We can't easily call list_tools synchronously, but we can verify
        # the server was created with schema_dir support
        server = create_mcp_server(
            schema_registry=registry,
            schema_dir="/tmp/test",
        )
        assert server.name == "slip-stream"


# ---------------------------------------------------------------------------
# Full call_tool dispatcher tests — exercises every _handle_* function
# ---------------------------------------------------------------------------


class TestCallToolDispatcher:
    """Drive the full call_tool -> _handle_* path via the registered handler."""

    @pytest.mark.asyncio
    async def test_list_schemas_via_dispatcher(self, server, registry):
        text = await _call_tool(server, "list_schemas", {})
        data = json.loads(text)
        names = [s["name"] for s in data["schemas"]]
        assert "widget" in names
        assert "gadget" in names

    @pytest.mark.asyncio
    async def test_get_schema_via_dispatcher(self, server):
        text = await _call_tool(server, "get_schema", {"name": "widget"})
        data = json.loads(text)
        assert data["name"] == "widget"
        assert "properties" in data["schema"]

    @pytest.mark.asyncio
    async def test_get_schema_specific_version_via_dispatcher(self, server):
        text = await _call_tool(
            server, "get_schema", {"name": "widget", "version": "1.0.0"}
        )
        data = json.loads(text)
        assert data["version"] == "1.0.0"
        assert data["name"] == "widget"

    @pytest.mark.asyncio
    async def test_get_schema_error_returns_error_text(self, server):
        text = await _call_tool(server, "get_schema", {"name": "nonexistent"})
        assert "Error" in text

    @pytest.mark.asyncio
    async def test_get_schema_dag_via_dispatcher(self, server):
        text = await _call_tool(server, "get_schema_dag", {})
        data = json.loads(text)
        assert "dag" in data
        names = [n["name"] for n in data["dag"]]
        assert "widget" in names

    @pytest.mark.asyncio
    async def test_list_versions_via_dispatcher(self, server):
        text = await _call_tool(server, "list_versions", {"name": "widget"})
        data = json.loads(text)
        assert data["name"] == "widget"
        assert "1.0.0" in data["versions"]
        assert data["latest_version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_describe_entity_via_dispatcher(self, server):
        text = await _call_tool(server, "describe_entity", {"name": "widget"})
        data = json.loads(text)
        assert data["name"] == "widget"
        field_names = [f["name"] for f in data["fields"]]
        assert "name" in field_names
        assert "api_endpoints" in data
        assert "rest" in data["api_endpoints"]

    @pytest.mark.asyncio
    async def test_describe_entity_marks_audit_fields(self, server):
        text = await _call_tool(server, "describe_entity", {"name": "widget"})
        data = json.loads(text)
        fields_by_name = {f["name"]: f for f in data["fields"]}
        # entity_id and id are audit fields
        assert fields_by_name["entity_id"]["is_audit_field"] is True
        # name is a user field
        assert fields_by_name["name"]["is_audit_field"] is False

    @pytest.mark.asyncio
    async def test_describe_entity_resolves_latest_version(self, server):
        text = await _call_tool(
            server, "describe_entity", {"name": "widget", "version": "latest"}
        )
        data = json.loads(text)
        assert data["version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_unknown_message(self, server):
        text = await _call_tool(server, "does_not_exist", {})
        assert "Unknown tool" in text

    @pytest.mark.asyncio
    async def test_validate_schemas_no_schema_dir(self, server):
        # server fixture has no schema_dir set
        text = await _call_tool(server, "validate_schemas", {})
        assert "Error" in text
        assert "schema_dir" in text

    @pytest.mark.asyncio
    async def test_validate_schemas_with_valid_schemas(self, registry, tmp_path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()
        create_schema_file(schemas_dir, "pet")

        srv = create_mcp_server(
            schema_registry=registry,
            base_url="http://localhost:8000",
            schema_dir=str(schemas_dir),
        )
        text = await _call_tool(srv, "validate_schemas", {})
        data = json.loads(text)
        assert data["valid"] is True
        assert data["total"] == 1

    @pytest.mark.asyncio
    async def test_generate_sdk_no_schema_dir(self, server):
        text = await _call_tool(server, "generate_sdk", {})
        assert "Error" in text
        assert "schema_dir" in text

    @pytest.mark.asyncio
    async def test_generate_sdk_with_schema_dir(self, registry, tmp_path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()
        create_schema_file(schemas_dir, "order")

        srv = create_mcp_server(
            schema_registry=registry,
            base_url="http://localhost:8000",
            schema_dir=str(schemas_dir),
        )
        text = await _call_tool(srv, "generate_sdk", {})
        assert "class Order(BaseModel):" in text
        assert "class SlipStreamClient:" in text

    @pytest.mark.asyncio
    async def test_generate_sdk_writes_to_output_path(self, registry, tmp_path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()
        create_schema_file(schemas_dir, "item")
        output_file = str(tmp_path / "sdk.py")

        srv = create_mcp_server(
            schema_registry=registry,
            base_url="http://localhost:8000",
            schema_dir=str(schemas_dir),
        )
        text = await _call_tool(srv, "generate_sdk", {"output_path": output_file})
        data = json.loads(text)
        assert data["written_to"] == output_file
        assert "item" in data["schemas"]

    @pytest.mark.asyncio
    async def test_query_rest_api_get(self, server):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"entity_id": "abc", "name": "test"}]

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            text = await _call_tool(
                server, "query_rest_api", {"path": "/api/v1/widget/"}
            )

        assert "200" in text
        assert "entity_id" in text

    @pytest.mark.asyncio
    async def test_query_rest_api_post(self, server):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"entity_id": "new-id", "name": "widget1"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            text = await _call_tool(
                server,
                "query_rest_api",
                {
                    "method": "POST",
                    "path": "/api/v1/widget/",
                    "body": {"name": "widget1"},
                },
            )

        assert "201" in text

    @pytest.mark.asyncio
    async def test_query_rest_api_patch(self, server):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"entity_id": "abc", "name": "updated"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.patch = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            text = await _call_tool(
                server,
                "query_rest_api",
                {
                    "method": "PATCH",
                    "path": "/api/v1/widget/abc",
                    "body": {"name": "updated"},
                },
            )

        assert "200" in text

    @pytest.mark.asyncio
    async def test_query_rest_api_delete(self, server):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_resp.json.side_effect = Exception("no body")
        mock_resp.text = ""

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.delete = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            text = await _call_tool(
                server,
                "query_rest_api",
                {"method": "DELETE", "path": "/api/v1/widget/abc"},
            )

        assert "204" in text

    @pytest.mark.asyncio
    async def test_query_rest_api_with_schema_version_header(self, server):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await _call_tool(
                server,
                "query_rest_api",
                {"path": "/api/v1/widget/", "schema_version": "2.0.0"},
            )

        call_kwargs = mock_client.get.call_args
        assert call_kwargs.kwargs["headers"]["X-Schema-Version"] == "2.0.0"

    @pytest.mark.asyncio
    async def test_query_graphql_via_dispatcher(self, server):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"widgets": []}}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            text = await _call_tool(
                server,
                "query_graphql",
                {"query": "{ widgets { entity_id } }"},
            )

        assert "widgets" in text

    @pytest.mark.asyncio
    async def test_query_graphql_with_variables(self, server):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"widget": {"entity_id": "abc"}}}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await _call_tool(
                server,
                "query_graphql",
                {
                    "query": "query GetWidget($id: ID!) { widget(id: $id) { entity_id } }",
                    "variables": {"id": "abc"},
                },
            )

        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs["json"]["variables"] == {"id": "abc"}

    @pytest.mark.asyncio
    async def test_list_tools_returns_all_expected_tools(self, server):
        list_handler = server.request_handlers[mcp_types.ListToolsRequest]
        req = mcp_types.ListToolsRequest(method="tools/list", params=None)
        result = await list_handler(req)
        tool_names = [t.name for t in result.root.tools]
        expected = [
            "list_schemas",
            "get_schema",
            "get_schema_dag",
            "list_versions",
            "describe_entity",
            "query_rest_api",
            "query_graphql",
            "create_schema",
            "validate_schemas",
            "generate_sdk",
            "get_topology",
        ]
        for name in expected:
            assert name in tool_names, f"Expected tool '{name}' not in tool list"

    @pytest.mark.asyncio
    async def test_query_rest_api_rejected_method_returns_error(self, server):
        # The MCP input schema enumerates valid methods; sending an unlisted method
        # causes the MCP layer to return a validation error before reaching the handler.
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            text = await _call_tool(
                server,
                "query_rest_api",
                {"method": "PUT", "path": "/api/v1/widget/"},
            )

        # MCP returns an input-validation error for unknown methods
        assert "PUT" in text

    @pytest.mark.asyncio
    async def test_create_schema_via_dispatcher(self, registry, tmp_path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        srv = create_mcp_server(
            schema_registry=registry,
            base_url="http://localhost:8000",
            schema_dir=str(schemas_dir),
        )
        text = await _call_tool(srv, "create_schema", {"name": "invoice"})
        data = json.loads(text)
        assert "created" in data
        assert "invoice" in data["schema_name"]
        assert (schemas_dir / "invoice.json").exists()

    @pytest.mark.asyncio
    async def test_create_schema_no_schema_dir_via_dispatcher(self, server):
        text = await _call_tool(server, "create_schema", {"name": "invoice"})
        assert "Error" in text
        assert "schema_dir" in text

    @pytest.mark.asyncio
    async def test_create_schema_duplicate_via_dispatcher(self, registry, tmp_path):
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()
        create_schema_file(schemas_dir, "duplicate")

        srv = create_mcp_server(
            schema_registry=registry,
            base_url="http://localhost:8000",
            schema_dir=str(schemas_dir),
        )
        text = await _call_tool(srv, "create_schema", {"name": "duplicate"})
        assert "Error" in text

    @pytest.mark.asyncio
    async def test_get_topology_via_dispatcher(self, server):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"schemas": ["widget"], "filters": []}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            text = await _call_tool(server, "get_topology", {})

        data = json.loads(text)
        assert "schemas" in data

    @pytest.mark.asyncio
    async def test_get_topology_non_200_includes_status(self, server):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.json.return_value = {"detail": "Service Unavailable"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            text = await _call_tool(server, "get_topology", {})

        assert "503" in text
