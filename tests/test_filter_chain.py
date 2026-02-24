"""Tests for FilterChain (ordered pipeline)."""

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from slip_stream.adapters.api.filters.base import (
    FilterBase,
    FilterContext,
    FilterShortCircuit,
)
from slip_stream.adapters.api.filters.chain import FilterChain


class TrackingFilter(FilterBase):
    """Filter that records call order for testing."""

    def __init__(self, name: str, order: int = 100):
        self.name = name
        self.order = order
        self.request_calls = []
        self.response_calls = []

    async def on_request(self, request, context):
        self.request_calls.append(self.name)
        context.extras.setdefault("request_order", []).append(self.name)

    async def on_response(self, request, response, context):
        self.response_calls.append(self.name)
        context.extras.setdefault("response_order", []).append(self.name)
        return response


class ShortCircuitFilter(FilterBase):
    """Filter that short-circuits the chain."""

    def __init__(self, order: int = 100):
        self.order = order

    async def on_request(self, request, context):
        raise FilterShortCircuit(status_code=403, body="Forbidden")

    async def on_response(self, request, response, context):
        return response


def _make_request() -> Request:
    """Create a minimal ASGI request for testing."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": [],
        "query_string": b"",
    }
    return Request(scope)


class TestFilterChain:
    """Tests for the FilterChain pipeline."""

    @pytest.mark.asyncio
    async def test_empty_chain_returns_context(self):
        chain = FilterChain()
        request = _make_request()
        context = await chain.process_request(request)
        assert isinstance(context, FilterContext)

    @pytest.mark.asyncio
    async def test_request_ascending_order(self):
        chain = FilterChain()
        f1 = TrackingFilter("first", order=10)
        f2 = TrackingFilter("second", order=20)
        f3 = TrackingFilter("third", order=30)

        # Add in wrong order to verify sorting
        chain.add_filters([f3, f1, f2])

        request = _make_request()
        context = await chain.process_request(request)

        assert context.extras["request_order"] == ["first", "second", "third"]

    @pytest.mark.asyncio
    async def test_response_descending_order(self):
        chain = FilterChain()
        f1 = TrackingFilter("first", order=10)
        f2 = TrackingFilter("second", order=20)
        f3 = TrackingFilter("third", order=30)
        chain.add_filters([f1, f2, f3])

        request = _make_request()
        context = await chain.process_request(request)

        response = JSONResponse(content={"ok": True})
        await chain.process_response(request, response, context)

        assert context.extras["response_order"] == ["third", "second", "first"]

    @pytest.mark.asyncio
    async def test_short_circuit_stops_chain(self):
        chain = FilterChain()
        f1 = TrackingFilter("before", order=10)
        f2 = ShortCircuitFilter(order=20)
        f3 = TrackingFilter("after", order=30)
        chain.add_filters([f1, f2, f3])

        request = _make_request()

        with pytest.raises(FilterShortCircuit) as exc_info:
            await chain.process_request(request)

        assert exc_info.value.status_code == 403
        # f1 should have run, f3 should not
        assert f1.request_calls == ["before"]
        assert f3.request_calls == []

    @pytest.mark.asyncio
    async def test_add_filter_maintains_order(self):
        chain = FilterChain()
        chain.add_filter(TrackingFilter("c", order=30))
        chain.add_filter(TrackingFilter("a", order=10))
        chain.add_filter(TrackingFilter("b", order=20))

        orders = [f.order for f in chain.filters]
        assert orders == [10, 20, 30]

    @pytest.mark.asyncio
    async def test_context_stored_on_request_state(self):
        chain = FilterChain()
        chain.add_filter(TrackingFilter("test", order=10))

        request = _make_request()
        context = await chain.process_request(request)

        assert request.state.filter_context is context

    @pytest.mark.asyncio
    async def test_empty_chain_response_passthrough(self):
        chain = FilterChain()
        request = _make_request()
        context = FilterContext()
        original = JSONResponse(content={"data": "test"})
        result = await chain.process_response(request, original, context)
        assert result is original
