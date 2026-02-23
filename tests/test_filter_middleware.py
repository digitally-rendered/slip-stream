"""Tests for FilterChainMiddleware (ASGI integration)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from slip_stream.adapters.api.filters.base import (
    FilterBase,
    FilterContext,
    FilterShortCircuit,
)
from slip_stream.adapters.api.filters.chain import FilterChain
from slip_stream.adapters.api.filters.middleware import FilterChainMiddleware


class HeaderAddingFilter(FilterBase):
    """Filter that adds a custom header to the response."""

    order = 50

    async def on_request(self, request, context):
        context.extras["filter_ran"] = True

    async def on_response(self, request, response, context):
        response.headers["X-Filter-Ran"] = "true"
        return response


class RejectAllFilter(FilterBase):
    """Filter that rejects every request."""

    order = 10

    async def on_request(self, request, context):
        raise FilterShortCircuit(
            status_code=401,
            body="Not allowed",
        )

    async def on_response(self, request, response, context):
        return response


def _create_app_with_filters(*filters: FilterBase) -> FastAPI:
    """Create a test FastAPI app with the given filters."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint():
        return {"message": "hello"}

    @app.post("/echo")
    async def echo_endpoint(data: dict):
        return data

    chain = FilterChain()
    chain.add_filters(list(filters))
    app.add_middleware(FilterChainMiddleware, filter_chain=chain)

    return app


class TestFilterChainMiddleware:
    """Tests for ASGI middleware integration."""

    def test_filter_runs_on_request_and_response(self):
        app = _create_app_with_filters(HeaderAddingFilter())
        client = TestClient(app)

        response = client.get("/test")
        assert response.status_code == 200
        assert response.json() == {"message": "hello"}
        assert response.headers.get("X-Filter-Ran") == "true"

    def test_short_circuit_returns_early(self):
        app = _create_app_with_filters(RejectAllFilter())
        client = TestClient(app)

        response = client.get("/test")
        assert response.status_code == 401
        assert response.json() == {"detail": "Not allowed"}

    def test_short_circuit_skips_endpoint(self):
        """Endpoint should never be called when filter short-circuits."""
        call_count = 0

        app = FastAPI()

        @app.get("/test")
        async def endpoint():
            nonlocal call_count
            call_count += 1
            return {"message": "hello"}

        chain = FilterChain()
        chain.add_filter(RejectAllFilter())
        app.add_middleware(FilterChainMiddleware, filter_chain=chain)

        client = TestClient(app)
        client.get("/test")
        assert call_count == 0

    def test_multiple_filters_in_order(self):
        class OrderTracker(FilterBase):
            order = 20

            async def on_request(self, request, context):
                context.extras.setdefault("order", []).append("request-20")

            async def on_response(self, request, response, context):
                context.extras.setdefault("order", []).append("response-20")
                return response

        class LateTracker(FilterBase):
            order = 40

            async def on_request(self, request, context):
                context.extras.setdefault("order", []).append("request-40")

            async def on_response(self, request, response, context):
                context.extras.setdefault("order", []).append("response-40")
                return response

        app = _create_app_with_filters(LateTracker(), OrderTracker())
        client = TestClient(app)

        response = client.get("/test")
        assert response.status_code == 200

    def test_empty_filter_chain_passthrough(self):
        app = _create_app_with_filters()
        client = TestClient(app)

        response = client.get("/test")
        assert response.status_code == 200
        assert response.json() == {"message": "hello"}

    def test_filter_with_post_request(self):
        app = _create_app_with_filters(HeaderAddingFilter())
        client = TestClient(app)

        response = client.post(
            "/echo",
            json={"key": "value"},
        )
        assert response.status_code == 200
        assert response.json() == {"key": "value"}
        assert response.headers.get("X-Filter-Ran") == "true"
