"""pytest-benchmark tests for slip-stream GraphQL schema generation.

Benchmarks the pure Python cost of building Strawberry types and the overall
GraphQL schema object from JSON schema definitions — no network, no DB.

Run with:
    poetry run pytest benchmarks/graphql/ --benchmark-only -v
    poetry run pytest benchmarks/graphql/ --benchmark-json=benchmarks/results/graphql-bench.json

These benchmarks exercise three distinct hot paths:

1. ``test_bench_graphql_schema_build`` — end-to-end: register schemas,
   build EntityContainer, create GraphQLFactory, build the strawberry.Schema.

2. ``test_bench_graphql_versioned_schema_build`` — same as above but with
   ``versioned=True`` which forces Strawberry to produce per-version types
   for each schema (e.g. PetV1_0_0).

3. ``test_bench_graphql_type_generation`` — micro-benchmark of the single
   ``_create_entity_type()`` call that converts a JSON Schema property dict
   into a @strawberry.type decorated class.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Optional imports — skip gracefully when strawberry is not installed.
# ---------------------------------------------------------------------------

try:
    import strawberry  # noqa: F401

    HAS_STRAWBERRY = True
except ImportError:
    HAS_STRAWBERRY = False

pytestmark = pytest.mark.skipif(
    not HAS_STRAWBERRY,
    reason="strawberry-graphql not installed — install with: poetry install --extras graphql",
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"

# Minimal single-entity schema for micro-benchmarks.
_PET_SCHEMA: dict[str, Any] = {
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
        "name": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["available", "pending", "sold"],
            "default": "available",
        },
        "category": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "photo_urls": {"type": "array", "items": {"type": "string"}},
    },
}

# Multi-entity schema set matching benchmarks/schemas/*.json
_MULTI_ENTITY_SCHEMAS: dict[str, dict[str, Any]] = {
    "pet": _PET_SCHEMA,
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


def _make_registry_with_schemas(schemas: dict[str, dict[str, Any]]) -> Any:
    """Create a fresh SchemaRegistry singleton loaded with the given schemas."""
    from slip_stream.core.schema.registry import SchemaRegistry

    SchemaRegistry.reset()
    reg = SchemaRegistry()
    for name, schema in schemas.items():
        reg.register_schema(name, schema)
    return reg


def _make_container(registry: Any) -> Any:
    """Build a minimal EntityContainer from a registry (no overrides)."""
    from slip_stream.container import EntityContainer

    container = EntityContainer()
    container.resolve_all(registry.get_schema_names())
    return container


def _noop_get_db():
    """Placeholder async dependency — never called during schema construction."""
    return None


# ---------------------------------------------------------------------------
# Benchmark 1: full schema build (latest types only)
# ---------------------------------------------------------------------------


def test_bench_graphql_schema_build(benchmark):
    """Benchmark end-to-end GraphQL schema build for 3 entities (no versioning).

    Measures:
    - SchemaRegistry.reset() + register_schema() × 3
    - EntityContainer.register() × 3
    - GraphQLFactory.create_graphql_router() → strawberry.Schema compilation
    """
    from slip_stream.adapters.api.graphql_factory import GraphQLFactory

    def build():
        reg = _make_registry_with_schemas(_MULTI_ENTITY_SCHEMAS)
        container = _make_container(reg)
        factory = GraphQLFactory()
        return factory.create_graphql_router(
            container=container,
            get_db=_noop_get_db,
            schema_registry=reg,
            event_bus=None,
            versioned=False,
        )

    result = benchmark(build)
    assert result is not None, "create_graphql_router returned None"


# ---------------------------------------------------------------------------
# Benchmark 2: versioned schema build
# ---------------------------------------------------------------------------


def test_bench_graphql_versioned_schema_build(benchmark):
    """Benchmark GraphQL schema build with versioned=True.

    When versioned=True, GraphQLFactory generates per-version Strawberry types
    (e.g. PetV1_0_0) in addition to the canonical unversioned types.  This
    benchmark measures the additional overhead of that second pass.
    """
    from slip_stream.adapters.api.graphql_factory import GraphQLFactory

    def build():
        from slip_stream.core.schema.registry import SchemaRegistry

        SchemaRegistry.reset()
        reg = SchemaRegistry()
        for name, schema in _MULTI_ENTITY_SCHEMAS.items():
            reg.register_schema(name, {**schema, "version": "1.0.0"})
            reg.register_schema(name, {**schema, "version": "2.0.0"})

        from slip_stream.container import EntityContainer

        container = EntityContainer()
        container.resolve_all(reg.get_schema_names())

        factory = GraphQLFactory()
        return factory.create_graphql_router(
            container=container,
            get_db=_noop_get_db,
            schema_registry=reg,
            event_bus=None,
            versioned=True,
        )

    result = benchmark(build)
    assert result is not None, "create_graphql_router (versioned) returned None"


# ---------------------------------------------------------------------------
# Benchmark 3: single entity type generation
# ---------------------------------------------------------------------------


def test_bench_graphql_type_generation(benchmark):
    """Benchmark _create_entity_type() — the per-schema Strawberry type factory.

    This is the hot path called once per schema (and once per version when
    versioned=True).  It converts a JSON Schema property dict into a fully
    decorated @strawberry.type class using Python's dataclasses machinery.
    """
    from slip_stream.adapters.api.graphql_factory import GraphQLFactory

    factory = GraphQLFactory()
    properties = _PET_SCHEMA["properties"]

    def generate():
        # Reset the global Strawberry type registry between iterations to
        # prevent duplicate-name errors in repeated benchmark rounds.
        import sys

        # Remove any previously created Pet type from the module globals.
        for key in list(
            sys.modules.get(
                "slip_stream.adapters.api.graphql_factory", {}
            ).__dict__.keys()
        ):
            if key.startswith("BenchPet"):
                del sys.modules["slip_stream.adapters.api.graphql_factory"].__dict__[
                    key
                ]

        return factory._create_entity_type(
            pascal="BenchPet",
            properties=properties,
            schema_name="pet",
        )

    result = benchmark(generate)
    assert result is not None, "_create_entity_type returned None"
    assert hasattr(
        result, "__strawberry_definition__"
    ), "result is not a Strawberry type"
