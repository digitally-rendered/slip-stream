"""MCP server for slip-stream — exposes schema and entity tools to AI assistants.

Tools exposed:

**Read tools:**

- ``list_schemas`` — list all registered schema names and versions
- ``get_schema`` — get the full JSON schema definition for a name+version
- ``get_schema_dag`` — get the dependency graph of all schemas
- ``list_versions`` — list all versions for a schema
- ``describe_entity`` — describe the fields and types for an entity
- ``query_entity`` — query the REST API for entity data
- ``query_graphql`` — execute a GraphQL query
- ``get_topology`` — get the running app's topology (schemas, filters, config)

**Write tools:**

- ``create_schema`` — create a new JSON schema file in the project
- ``validate_schemas`` — validate all schemas in the project
- ``generate_sdk`` — generate a typed Python SDK client from schemas

Usage::

    from slip_stream.mcp.server import create_mcp_server

    server = create_mcp_server(
        schema_registry=registry,
        base_url="http://localhost:8000",
    )
    # Run with: mcp run slip_stream/mcp/server.py

Or as a standalone script::

    python -m slip_stream.mcp.server --base-url http://localhost:8000
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool

    HAS_MCP = True
except ImportError:
    HAS_MCP = False


def create_mcp_server(
    schema_registry: Any | None = None,
    base_url: str = "http://localhost:8000",
    api_prefix: str = "/api/v1",
    schema_prefix: str = "/schemas",
    schema_dir: Any | None = None,
) -> "Server":
    """Create an MCP server with slip-stream schema tools.

    Args:
        schema_registry: The SchemaRegistry instance. If None, creates one.
        base_url: Base URL of the running slip-stream application.
        api_prefix: REST API prefix.
        schema_prefix: Schema vending API prefix.
        schema_dir: Path to the schema directory for write tools (create_schema,
            validate_schemas, generate_sdk). If None, write tools will return errors.

    Returns:
        An MCP Server instance ready to run.
    """
    if not HAS_MCP:
        raise ImportError(
            "mcp is required for the MCP server. "
            "Install it with: pip install slip-stream[mcp]"
        )

    server = Server("slip-stream")

    def _get_registry() -> Any:
        if schema_registry is not None:
            return schema_registry
        from slip_stream.core.schema.registry import SchemaRegistry
        return SchemaRegistry()

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="list_schemas",
                description=(
                    "List all registered entity schemas in the slip-stream application. "
                    "Returns schema names, their available versions, and the latest version."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="get_schema",
                description=(
                    "Get the full JSON Schema definition for a specific entity. "
                    "Shows all fields, types, required fields, and defaults. "
                    "Use version='latest' for the most recent version."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Schema name (e.g. 'widget', 'order')",
                        },
                        "version": {
                            "type": "string",
                            "description": "Version string (e.g. '1.0.0') or 'latest'",
                            "default": "latest",
                        },
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="get_schema_dag",
                description=(
                    "Get the dependency graph (DAG) of all schemas. "
                    "Shows which schemas reference other schemas via $ref, "
                    "their versions, and the overall structure of the data model."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="list_versions",
                description=(
                    "List all available versions for a specific schema, "
                    "sorted by semantic versioning."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Schema name",
                        },
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="describe_entity",
                description=(
                    "Describe all fields of an entity including their types, "
                    "whether they're required, defaults, and which are audit/system fields. "
                    "Useful for understanding what data an entity holds."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Schema name",
                        },
                        "version": {
                            "type": "string",
                            "description": "Version string or 'latest'",
                            "default": "latest",
                        },
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="query_rest_api",
                description=(
                    "Query the slip-stream REST API. Supports GET operations "
                    "on entity endpoints. Returns JSON response data."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "method": {
                            "type": "string",
                            "enum": ["GET", "POST", "PATCH", "DELETE"],
                            "description": "HTTP method",
                            "default": "GET",
                        },
                        "path": {
                            "type": "string",
                            "description": (
                                "API path relative to base URL "
                                "(e.g. '/api/v1/widget/' or '/api/v1/widget/{id}')"
                            ),
                        },
                        "body": {
                            "type": "object",
                            "description": "Request body for POST/PATCH",
                        },
                        "schema_version": {
                            "type": "string",
                            "description": "Schema version to request via X-Schema-Version header",
                        },
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="query_graphql",
                description=(
                    "Execute a GraphQL query against the slip-stream GraphQL API. "
                    "Use the schema tools first to understand available types and fields."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "GraphQL query string",
                        },
                        "variables": {
                            "type": "object",
                            "description": "GraphQL variables",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="create_schema",
                description=(
                    "Create a new JSON schema file in the project's schemas directory. "
                    "The schema will include all standard slip-stream fields (id, entity_id, "
                    "versioning, audit fields) plus a 'name' property."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Entity name (e.g. 'widget', 'user_profile')",
                        },
                        "description": {
                            "type": "string",
                            "description": "Optional description for the schema",
                        },
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="validate_schemas",
                description=(
                    "Validate all JSON schema files in the project's schemas directory. "
                    "Checks that each schema has a title, version, type=object, and properties."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="generate_sdk",
                description=(
                    "Generate a typed Python SDK client from all schemas in the project. "
                    "The generated client includes Pydantic models and async CRUD methods "
                    "for each entity. Requires httpx and pydantic at runtime."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "output_path": {
                            "type": "string",
                            "description": "File path to write the generated SDK to (optional, returns code if omitted)",
                        },
                    },
                },
            ),
            Tool(
                name="get_topology",
                description=(
                    "Get the running application's topology from the /_topology endpoint. "
                    "Returns the registered schemas, filters, and configuration. "
                    "Does NOT expose secrets or database URIs."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            if name == "list_schemas":
                return await _handle_list_schemas()
            elif name == "get_schema":
                return await _handle_get_schema(arguments)
            elif name == "get_schema_dag":
                return await _handle_get_schema_dag()
            elif name == "list_versions":
                return await _handle_list_versions(arguments)
            elif name == "describe_entity":
                return await _handle_describe_entity(arguments)
            elif name == "query_rest_api":
                return await _handle_query_rest(arguments)
            elif name == "query_graphql":
                return await _handle_query_graphql(arguments)
            elif name == "create_schema":
                return await _handle_create_schema(arguments)
            elif name == "validate_schemas":
                return await _handle_validate_schemas()
            elif name == "generate_sdk":
                return await _handle_generate_sdk(arguments)
            elif name == "get_topology":
                return await _handle_get_topology()
            else:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    async def _handle_list_schemas() -> list[TextContent]:
        reg = _get_registry()
        result = []
        for name in sorted(reg.get_schema_names()):
            versions = reg.get_all_versions(name)
            latest = reg.get_latest_version(name)
            result.append({
                "name": name,
                "versions": versions,
                "latest_version": latest,
            })
        return [TextContent(
            type="text",
            text=json.dumps({"schemas": result}, indent=2),
        )]

    async def _handle_get_schema(args: dict) -> list[TextContent]:
        reg = _get_registry()
        name = args["name"]
        version = args.get("version", "latest")
        schema = reg.get_schema(name, version)
        return [TextContent(
            type="text",
            text=json.dumps({
                "name": name,
                "version": version if version != "latest" else reg.get_latest_version(name),
                "schema": schema,
            }, indent=2),
        )]

    async def _handle_get_schema_dag() -> list[TextContent]:
        reg = _get_registry()
        dag = []
        for name in sorted(reg.get_schema_names()):
            versions = reg.get_all_versions(name)
            latest = reg.get_latest_version(name)
            schema = reg.get_schema(name, latest)
            deps = _extract_refs_from_schema(schema)
            dag.append({
                "name": name,
                "versions": versions,
                "latest_version": latest,
                "dependencies": deps,
            })
        return [TextContent(
            type="text",
            text=json.dumps({"dag": dag}, indent=2),
        )]

    async def _handle_list_versions(args: dict) -> list[TextContent]:
        reg = _get_registry()
        name = args["name"]
        versions = reg.get_all_versions(name)
        return [TextContent(
            type="text",
            text=json.dumps({
                "name": name,
                "versions": versions,
                "latest_version": reg.get_latest_version(name),
            }, indent=2),
        )]

    async def _handle_describe_entity(args: dict) -> list[TextContent]:
        reg = _get_registry()
        name = args["name"]
        version = args.get("version", "latest")
        schema = reg.get_schema(name, version)
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        audit_fields = {
            "id", "entity_id", "schema_version", "record_version",
            "created_at", "updated_at", "deleted_at",
            "created_by", "updated_by", "deleted_by",
        }

        fields = []
        for field_name, field_def in properties.items():
            fields.append({
                "name": field_name,
                "type": field_def.get("type", "any"),
                "format": field_def.get("format"),
                "required": field_name in required,
                "default": field_def.get("default"),
                "is_audit_field": field_name in audit_fields,
            })

        resolved_version = version
        if version == "latest":
            resolved_version = reg.get_latest_version(name)

        return [TextContent(
            type="text",
            text=json.dumps({
                "name": name,
                "version": resolved_version,
                "field_count": len(fields),
                "user_fields": len([f for f in fields if not f["is_audit_field"]]),
                "fields": fields,
                "api_endpoints": {
                    "rest": {
                        "list": f"GET {base_url}{api_prefix}/{name.replace('_', '-')}/",
                        "get": f"GET {base_url}{api_prefix}/{name.replace('_', '-')}/{{entity_id}}",
                        "create": f"POST {base_url}{api_prefix}/{name.replace('_', '-')}/",
                        "update": f"PATCH {base_url}{api_prefix}/{name.replace('_', '-')}/{{entity_id}}",
                        "delete": f"DELETE {base_url}{api_prefix}/{name.replace('_', '-')}/{{entity_id}}",
                    },
                    "schema": f"GET {base_url}{schema_prefix}/{name}/{resolved_version}",
                },
            }, indent=2),
        )]

    async def _handle_query_rest(args: dict) -> list[TextContent]:
        try:
            import httpx
        except ImportError:
            return [TextContent(
                type="text",
                text="Error: httpx required for REST queries. Install with: pip install httpx",
            )]

        method = args.get("method", "GET")
        path = args["path"]
        body = args.get("body")
        schema_version = args.get("schema_version")

        url = f"{base_url}{path}"
        headers: dict[str, str] = {}
        if schema_version:
            headers["X-Schema-Version"] = schema_version

        async with httpx.AsyncClient() as client:
            if method == "GET":
                resp = await client.get(url, headers=headers)
            elif method == "POST":
                resp = await client.post(url, json=body, headers=headers)
            elif method == "PATCH":
                resp = await client.patch(url, json=body, headers=headers)
            elif method == "DELETE":
                resp = await client.delete(url, headers=headers)
            else:
                return [TextContent(type="text", text=f"Unsupported method: {method}")]

        try:
            data = resp.json()
            text = json.dumps(data, indent=2, default=str)
        except Exception:
            text = resp.text

        return [TextContent(
            type="text",
            text=f"HTTP {resp.status_code}\n\n{text}",
        )]

    async def _handle_query_graphql(args: dict) -> list[TextContent]:
        try:
            import httpx
        except ImportError:
            return [TextContent(
                type="text",
                text="Error: httpx required for GraphQL queries. Install with: pip install httpx",
            )]

        query = args["query"]
        variables = args.get("variables", {})
        url = f"{base_url}/graphql"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json={"query": query, "variables": variables},
            )

        try:
            data = resp.json()
            text = json.dumps(data, indent=2, default=str)
        except Exception:
            text = resp.text

        return [TextContent(
            type="text",
            text=text,
        )]

    async def _handle_create_schema(args: dict) -> list[TextContent]:
        if schema_dir is None:
            return [TextContent(
                type="text",
                text="Error: schema_dir not configured. Pass --schema-dir when starting the MCP server.",
            )]

        from pathlib import Path
        from slip_stream.schema_utils import create_schema_file, snake_case

        schemas_path = Path(schema_dir)
        name = args["name"]
        description = args.get("description")

        try:
            target = create_schema_file(schemas_path, name, description)
        except FileExistsError as e:
            return [TextContent(type="text", text=f"Error: {e}")]

        snake = snake_case(name)
        return [TextContent(
            type="text",
            text=json.dumps({
                "created": str(target),
                "schema_name": snake,
                "endpoint": f"{api_prefix}/{snake.replace('_', '-')}/",
            }, indent=2),
        )]

    async def _handle_validate_schemas() -> list[TextContent]:
        if schema_dir is None:
            return [TextContent(
                type="text",
                text="Error: schema_dir not configured. Pass --schema-dir when starting the MCP server.",
            )]

        from pathlib import Path
        from slip_stream.schema_utils import validate_all_schemas

        schemas_path = Path(schema_dir)
        results = validate_all_schemas(schemas_path)

        if not results:
            return [TextContent(type="text", text=json.dumps({"schemas": [], "valid": True}, indent=2))]

        output = []
        all_valid = True
        for fname, issues in sorted(results.items()):
            entry = {"file": fname, "valid": len(issues) == 0}
            if issues:
                entry["issues"] = issues
                all_valid = False
            output.append(entry)

        return [TextContent(
            type="text",
            text=json.dumps({
                "schemas": output,
                "total": len(output),
                "valid": all_valid,
            }, indent=2),
        )]

    async def _handle_generate_sdk(args: dict) -> list[TextContent]:
        if schema_dir is None:
            return [TextContent(
                type="text",
                text="Error: schema_dir not configured. Pass --schema-dir when starting the MCP server.",
            )]

        from pathlib import Path
        from slip_stream.sdk_generator import generate_sdk

        schemas_path = Path(schema_dir)
        schemas: dict[str, Any] = {}
        for f in sorted(schemas_path.glob("**/*.json")):
            try:
                data = json.loads(f.read_text())
                schemas[f.stem] = data
            except (json.JSONDecodeError, KeyError):
                continue

        if not schemas:
            return [TextContent(type="text", text="Error: no valid schemas found.")]

        code = generate_sdk(schemas=schemas, base_url=f"{base_url}{api_prefix}")

        output_path = args.get("output_path")
        if output_path:
            Path(output_path).write_text(code)
            return [TextContent(
                type="text",
                text=json.dumps({
                    "written_to": output_path,
                    "schemas": list(schemas.keys()),
                    "lines": len(code.splitlines()),
                }, indent=2),
            )]

        return [TextContent(type="text", text=code)]

    async def _handle_get_topology() -> list[TextContent]:
        try:
            import httpx
        except ImportError:
            return [TextContent(
                type="text",
                text="Error: httpx required for topology queries. Install with: pip install httpx",
            )]

        url = f"{base_url}/_topology"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)

        try:
            data = resp.json()
            text = json.dumps(data, indent=2, default=str)
        except Exception:
            text = resp.text

        return [TextContent(
            type="text",
            text=f"HTTP {resp.status_code}\n\n{text}" if resp.status_code != 200 else text,
        )]

    return server


def _extract_refs_from_schema(
    schema: Any, seen: set | None = None
) -> list[str]:
    """Extract $ref dependencies from a schema."""
    if seen is None:
        seen = set()
    refs: list[str] = []
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if ref and isinstance(ref, str) and not ref.startswith("#"):
            parts = ref.replace("\\", "/").split("/")
            name = parts[-1].replace(".json", "").split("#")[0]
            if name and name not in seen:
                seen.add(name)
                refs.append(name)
        for v in schema.values():
            refs.extend(_extract_refs_from_schema(v, seen))
    elif isinstance(schema, list):
        for item in schema:
            refs.extend(_extract_refs_from_schema(item, seen))
    return refs


async def main() -> None:
    """Run the MCP server via stdio transport."""
    import argparse

    parser = argparse.ArgumentParser(description="slip-stream MCP server")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL of the slip-stream application",
    )
    parser.add_argument("--api-prefix", default="/api/v1")
    parser.add_argument("--schema-prefix", default="/schemas")
    parser.add_argument("--schema-dir", default=None, help="Path to schema directory")
    args = parser.parse_args()

    registry = None
    if args.schema_dir:
        from pathlib import Path
        from slip_stream.core.schema.registry import SchemaRegistry
        registry = SchemaRegistry(schema_dir=Path(args.schema_dir))

    server = create_mcp_server(
        schema_registry=registry,
        base_url=args.base_url,
        api_prefix=args.api_prefix,
        schema_prefix=args.schema_prefix,
        schema_dir=args.schema_dir,
    )

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
