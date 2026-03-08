"""pytest-benchmark tests for slip-stream MCP tool handlers.

Measures the latency of MCP tool handlers invoked directly — no stdio transport,
no subprocess, no network.  The handlers are called by constructing a
``CallToolRequest`` and dispatching it through the server's registered
``request_handlers`` mapping, which is the same path the stdio transport
takes after deserialising an incoming JSON-RPC message.

Run with:
    poetry run pytest benchmarks/mcp/ --benchmark-only -v
    poetry run pytest benchmarks/mcp/ --benchmark-json=benchmarks/results/mcp-bench.json

Benchmarks:

- ``test_bench_mcp_list_schemas``    — list all registered schemas
- ``test_bench_mcp_get_schema``      — fetch a single schema by name
- ``test_bench_mcp_describe_entity`` — describe all fields of an entity
- ``test_bench_mcp_list_versions``   — list all versions for a schema
- ``test_bench_mcp_get_schema_dag``  — build the full schema dependency DAG
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Optional import — skip when mcp package is not installed.
# ---------------------------------------------------------------------------

try:
    from mcp.server import Server  # noqa: F401
    from mcp.types import CallToolRequest, CallToolRequestParams

    HAS_MCP = True
except ImportError:
    HAS_MCP = False

pytestmark = pytest.mark.skipif(
    not HAS_MCP,
    reason="mcp package not installed — install with: poetry install --extras mcp",
)

# ---------------------------------------------------------------------------
# Shared benchmark schemas
# ---------------------------------------------------------------------------

_BENCH_SCHEMAS: dict[str, dict[str, Any]] = {
    "pet": {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Pet",
        "version": "1.0.0",
        "type": "object",
        "required": ["name", "status"],
        "properties": {
            "id": {"type": "string", "format": "uuid"},
            "entity_id": {"type": "string", "format": "uuid"},
            "schema_version": {"type": "string", "default": "1.0.0"},
            "record_version": {"type": "integer", "default": 1},
            "created_at": {"type": "string", "format": "date-time"},
            "updated_at": {"type": "string", "format": "date-time"},
            "deleted_at": {"type": "string", "format": "date-time"},
            "name": {"type": "string", "description": "Name of the pet"},
            "status": {
                "type": "string",
                "enum": ["available", "pending", "sold"],
                "default": "available",
            },
            "category": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "photo_urls": {"type": "array", "items": {"type": "string"}},
        },
    },
    "order": {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Order",
        "version": "1.0.0",
        "type": "object",
        "required": ["pet_id", "status"],
        "properties": {
            "id": {"type": "string", "format": "uuid"},
            "entity_id": {"type": "string", "format": "uuid"},
            "schema_version": {"type": "string", "default": "1.0.0"},
            "record_version": {"type": "integer", "default": 1},
            "created_at": {"type": "string", "format": "date-time"},
            "updated_at": {"type": "string", "format": "date-time"},
            "deleted_at": {"type": "string", "format": "date-time"},
            "pet_id": {"type": "string", "format": "uuid"},
            "quantity": {"type": "integer", "default": 1},
            "status": {
                "type": "string",
                "enum": ["placed", "approved", "delivered"],
                "default": "placed",
            },
            "complete": {"type": "boolean", "default": False},
        },
    },
    "tag": {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Tag",
        "version": "1.0.0",
        "type": "object",
        "required": ["name"],
        "properties": {
            "id": {"type": "string", "format": "uuid"},
            "entity_id": {"type": "string", "format": "uuid"},
            "schema_version": {"type": "string", "default": "1.0.0"},
            "record_version": {"type": "integer", "default": 1},
            "created_at": {"type": "string", "format": "date-time"},
            "updated_at": {"type": "string", "format": "date-time"},
            "deleted_at": {"type": "string", "format": "date-time"},
            "name": {"type": "string"},
        },
    },
}


def _build_registry() -> Any:
    """Return a SchemaRegistry pre-loaded with benchmark schemas."""
    from slip_stream.core.schema.registry import SchemaRegistry

    SchemaRegistry.reset()
    reg = SchemaRegistry()
    for name, schema in _BENCH_SCHEMAS.items():
        reg.register_schema(name, schema)
    # Register a second version of 'pet' to exercise list_versions and DAG.
    pet_v2 = {**_BENCH_SCHEMAS["pet"], "version": "2.0.0"}
    reg.register_schema("pet", pet_v2)
    return reg


@pytest.fixture(scope="module")
def mcp_registry() -> Any:
    """Module-scoped SchemaRegistry — built once, shared across all benchmarks."""
    return _build_registry()


@pytest.fixture(scope="module")
def event_loop_runner():
    """Module-scoped event loop — reused across all benchmark invocations."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# Handler dispatch helper
# ---------------------------------------------------------------------------


def _make_call_tool_request(
    tool_name: str, arguments: dict[str, Any]
) -> "CallToolRequest":
    """Build a CallToolRequest for the given tool name and arguments."""
    return CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name=tool_name, arguments=arguments),
    )


def _get_dispatch(registry: Any) -> Any:
    """Create an MCP server and return the CallToolRequest dispatch function.

    The MCP SDK stores the handler in ``server.request_handlers`` keyed by
    the request class.  We extract it here so each benchmark can call it
    directly as a coroutine without going through the stdio transport.
    """
    from mcp.types import CallToolRequest as _CTR

    from slip_stream.mcp.server import create_mcp_server

    server = create_mcp_server(
        schema_registry=registry,
        base_url="http://localhost:8000",
    )
    handler = server.request_handlers[_CTR]
    return handler


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------


def test_bench_mcp_list_schemas(benchmark, mcp_registry, event_loop_runner):
    """Benchmark list_schemas tool handler — iterates all schema names and versions."""
    dispatch = _get_dispatch(mcp_registry)
    req = _make_call_tool_request("list_schemas", {})

    async def invoke():
        return await dispatch(req)

    def run():
        return event_loop_runner.run_until_complete(invoke())

    result = benchmark(run)

    assert result is not None
    # The result is a CallToolResult with a content list.
    content = result.root.content if hasattr(result, "root") else result.content
    assert len(content) > 0
    data = json.loads(content[0].text)
    assert "schemas" in data
    assert any(s["name"] == "pet" for s in data["schemas"])


def test_bench_mcp_get_schema(benchmark, mcp_registry, event_loop_runner):
    """Benchmark get_schema tool handler — fetches and serialises one schema."""
    dispatch = _get_dispatch(mcp_registry)
    req = _make_call_tool_request("get_schema", {"name": "pet", "version": "latest"})

    async def invoke():
        return await dispatch(req)

    def run():
        return event_loop_runner.run_until_complete(invoke())

    result = benchmark(run)

    assert result is not None
    content = result.root.content if hasattr(result, "root") else result.content
    data = json.loads(content[0].text)
    assert data["name"] == "pet"
    assert "properties" in data["schema"]


def test_bench_mcp_describe_entity(benchmark, mcp_registry, event_loop_runner):
    """Benchmark describe_entity tool handler — enumerates and classifies fields."""
    dispatch = _get_dispatch(mcp_registry)
    req = _make_call_tool_request("describe_entity", {"name": "pet"})

    async def invoke():
        return await dispatch(req)

    def run():
        return event_loop_runner.run_until_complete(invoke())

    result = benchmark(run)

    assert result is not None
    content = result.root.content if hasattr(result, "root") else result.content
    data = json.loads(content[0].text)
    assert "field_count" in data
    assert "fields" in data
    assert data["field_count"] > 0


def test_bench_mcp_list_versions(benchmark, mcp_registry, event_loop_runner):
    """Benchmark list_versions tool handler — sorts semver versions for a schema."""
    dispatch = _get_dispatch(mcp_registry)
    req = _make_call_tool_request("list_versions", {"name": "pet"})

    async def invoke():
        return await dispatch(req)

    def run():
        return event_loop_runner.run_until_complete(invoke())

    result = benchmark(run)

    assert result is not None
    content = result.root.content if hasattr(result, "root") else result.content
    data = json.loads(content[0].text)
    # 'pet' has two registered versions: 1.0.0 and 2.0.0.
    assert len(data["versions"]) >= 1
    assert data["latest_version"] is not None


def test_bench_mcp_get_schema_dag(benchmark, mcp_registry, event_loop_runner):
    """Benchmark get_schema_dag tool handler — builds the full schema dependency DAG."""
    dispatch = _get_dispatch(mcp_registry)
    req = _make_call_tool_request("get_schema_dag", {})

    async def invoke():
        return await dispatch(req)

    def run():
        return event_loop_runner.run_until_complete(invoke())

    result = benchmark(run)

    assert result is not None
    content = result.root.content if hasattr(result, "root") else result.content
    data = json.loads(content[0].text)
    assert "dag" in data
    assert len(data["dag"]) >= len(_BENCH_SCHEMAS)
