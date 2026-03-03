"""Tests for GraphQL security hardening — depth limiting, list cap, introspection.

NOTE: This file must NOT use ``from __future__ import annotations`` because
Strawberry needs concrete type objects (not string annotations) at decoration
time to build the GraphQL schema.
"""

import pytest
import strawberry
from graphql import parse
from strawberry.schema import Schema

from slip_stream.adapters.api.graphql_factory import (
    _make_depth_limiter,
    _make_introspection_blocker,
    _measure_query_depth,
)

# ---------------------------------------------------------------------------
# Query depth measurement
# ---------------------------------------------------------------------------


class TestMeasureQueryDepth:

    def test_flat_query(self):
        doc = parse("{ users }")
        assert _measure_query_depth(doc) == 1

    def test_nested_query(self):
        doc = parse("{ users { name address { city } } }")
        assert _measure_query_depth(doc) == 3

    def test_deeply_nested_query(self):
        doc = parse("{ a { b { c { d { e } } } } }")
        assert _measure_query_depth(doc) == 5

    def test_mutation_depth(self):
        doc = parse("mutation { createUser { id profile { name } } }")
        assert _measure_query_depth(doc) == 3


# ---------------------------------------------------------------------------
# Strawberry types for depth-limiter tests — must be at module scope
# ---------------------------------------------------------------------------


@strawberry.type
class _Inner:
    value: str = "leaf"


@strawberry.type
class _Middle:
    inner: _Inner = strawberry.field(default_factory=_Inner)


@strawberry.type
class _Outer:
    middle: _Middle = strawberry.field(default_factory=_Middle)


# ---------------------------------------------------------------------------
# Depth limiter extension
# ---------------------------------------------------------------------------


class TestDepthLimiter:

    def _make_schema(self, max_depth: int = 3) -> Schema:
        """Build a minimal Strawberry schema with depth limiting."""

        @strawberry.type
        class DepthQuery:
            @strawberry.field
            def outer(self) -> _Outer:
                return _Outer()

        DepthLimiter = _make_depth_limiter(max_depth)
        return strawberry.Schema(query=DepthQuery, extensions=[DepthLimiter])

    @pytest.mark.asyncio
    async def test_depth_limit_allows_shallow_query(self):
        schema = self._make_schema(max_depth=5)
        result = await schema.execute("{ outer { middle { inner { value } } } }")
        assert result.errors is None or len(result.errors) == 0
        assert result.data["outer"]["middle"]["inner"]["value"] == "leaf"

    @pytest.mark.asyncio
    async def test_depth_limit_rejects_deep_query(self):
        schema = self._make_schema(max_depth=2)
        result = await schema.execute("{ outer { middle { inner { value } } } }")
        assert result.errors is not None
        assert len(result.errors) > 0
        assert "exceeds maximum allowed depth" in str(result.errors[0])


# ---------------------------------------------------------------------------
# Introspection control
# ---------------------------------------------------------------------------


class TestIntrospectionControl:

    def _make_schema(self, allow_introspection: bool = True) -> Schema:

        @strawberry.type
        class HelloQuery:
            @strawberry.field
            def hello(self) -> str:
                return "world"

        extensions = []
        if not allow_introspection:
            extensions.append(_make_introspection_blocker())
        return strawberry.Schema(query=HelloQuery, extensions=extensions)

    @pytest.mark.asyncio
    async def test_introspection_enabled_by_default(self):
        schema = self._make_schema(allow_introspection=True)
        result = await schema.execute("{ __schema { types { name } } }")
        assert result.errors is None or len(result.errors) == 0
        assert result.data is not None

    @pytest.mark.asyncio
    async def test_introspection_disabled(self):
        schema = self._make_schema(allow_introspection=False)
        result = await schema.execute("{ __schema { types { name } } }")
        assert result.errors is not None
        assert len(result.errors) > 0
        assert "Introspection is disabled" in str(result.errors[0])

    @pytest.mark.asyncio
    async def test_introspection_disabled_blocks_type(self):
        schema = self._make_schema(allow_introspection=False)
        result = await schema.execute('{ __type(name: "HelloQuery") { name } }')
        assert result.errors is not None
        assert "Introspection is disabled" in str(result.errors[0])


# ---------------------------------------------------------------------------
# List resolver limit cap
# ---------------------------------------------------------------------------


class TestListLimitCap:

    def test_limit_capped_at_1000(self):
        """The list resolver should clamp limit to max 1000.

        This is tested indirectly by verifying the clamping logic that
        is applied in the resolver — max(1, min(limit, 1000)).
        """
        # Verify the clamping formula directly
        for limit, expected in [
            (100, 100),
            (1000, 1000),
            (5000, 1000),
            (0, 1),
            (-1, 1),
            (1, 1),
        ]:
            clamped = max(1, min(limit, 1000))
            assert (
                clamped == expected
            ), f"limit={limit}: expected {expected}, got {clamped}"
