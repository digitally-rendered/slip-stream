# MCP Server

slip-stream includes an [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that exposes schema and entity tools to AI assistants like Claude.

## Install

```bash
pip install slip-stream[mcp]
```

## Running the Server

```bash
python -m slip_stream.mcp.server \
  --schema-dir ./schemas \
  --base-url http://localhost:8000 \
  --api-prefix /api/v1
```

The server runs over stdio transport and can be connected to any MCP-compatible client.

## Available Tools

### Read Tools

| Tool | Description | Parameters |
|------|-------------|------------|
| `list_schemas` | List all registered schema names and versions | — |
| `get_schema` | Get the full JSON Schema definition | `name`, `version` (default: latest) |
| `get_schema_dag` | Get the dependency graph of all schemas | — |
| `list_versions` | List all versions for a schema | `name` |
| `describe_entity` | Describe fields, types, and API endpoints | `name`, `version` (default: latest) |
| `query_rest_api` | Query the REST API | `path`, `method`, `body`, `schema_version` |
| `query_graphql` | Execute a GraphQL query | `query`, `variables` |
| `get_topology` | Get the running app's topology | — |

### Write Tools

Write tools require `--schema-dir` to be set when starting the server.

| Tool | Description | Parameters |
|------|-------------|------------|
| `create_schema` | Create a new JSON schema file | `name`, `description` (optional) |
| `validate_schemas` | Validate all schemas in the project | — |
| `generate_sdk` | Generate a typed Python SDK client | `output_path` (optional) |

## Tool Details

### create_schema

Creates a new JSON schema file with all standard slip-stream fields (id, entity_id, versioning, audit fields).

```
Input:  {"name": "invoice", "description": "A billing invoice."}
Output: {"created": "/path/to/schemas/invoice.json", "schema_name": "invoice", "endpoint": "/api/v1/invoice/"}
```

### validate_schemas

Checks every `*.json` file in the schemas directory for required fields (title, version, type=object, properties).

```
Output: {
  "schemas": [
    {"file": "invoice.json", "valid": true},
    {"file": "bad.json", "valid": false, "issues": ["missing 'title'"]}
  ],
  "total": 2,
  "valid": false
}
```

### generate_sdk

Generates a complete Python module with Pydantic models and an async HTTP client for every schema. The generated code depends only on `httpx` and `pydantic`.

```
Input:  {"output_path": "./client.py"}
Output: {"written_to": "./client.py", "schemas": ["invoice", "order"], "lines": 245}
```

Without `output_path`, returns the generated code directly.

The generated client includes:

```python
class SlipStreamClient:
    async def create_invoice(self, data: InvoiceCreate) -> Invoice: ...
    async def get_invoice(self, entity_id: str | UUID) -> Invoice: ...
    async def list_invoices(self, skip=0, limit=100) -> list[Invoice]: ...
    async def update_invoice(self, entity_id, data: InvoiceUpdate) -> Invoice: ...
    async def delete_invoice(self, entity_id: str | UUID) -> Invoice: ...
```

### get_topology

Fetches `GET /_topology` from the running application. Returns the same JSON as described in [Observability](observability.md).

## Programmatic Usage

```python
from slip_stream.mcp.server import create_mcp_server

server = create_mcp_server(
    schema_registry=registry,          # SchemaRegistry instance
    base_url="http://localhost:8000",
    api_prefix="/api/v1",
    schema_dir="./schemas",            # enables write tools
)
```

## AI Agent Workflow

A typical AI agent workflow using MCP tools:

1. **Scaffold** — `create_schema` to add new entities
2. **Validate** — `validate_schemas` to check all schemas
3. **Inspect** — `get_topology` to see the running app structure
4. **Query** — `query_rest_api` or `query_graphql` to interact with data
5. **Generate** — `generate_sdk` to produce a typed client for integration
