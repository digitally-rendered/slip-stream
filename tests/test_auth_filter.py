"""Tests for AuthFilter reference implementation."""

from typing import Any, Dict, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from slip_stream.adapters.api.filters.auth import AuthFilter
from slip_stream.adapters.api.filters.chain import FilterChain
from slip_stream.adapters.api.filters.middleware import FilterChainMiddleware


async def _valid_auth(request: Request) -> Optional[Dict[str, Any]]:
    """Auth function that accepts 'Bearer valid-token'."""
    auth = request.headers.get("authorization", "")
    if auth == "Bearer valid-token":
        return {"id": "user-1", "role": "admin"}
    return None


def _create_app_with_auth() -> FastAPI:
    """Create a test app with auth filter."""
    app = FastAPI()

    @app.get("/protected")
    async def protected():
        return {"message": "secret data"}

    @app.get("/public")
    async def public():
        return {"message": "public data"}

    chain = FilterChain()
    chain.add_filter(AuthFilter(authenticate=_valid_auth))
    app.add_middleware(FilterChainMiddleware, filter_chain=chain)

    return app


@pytest.fixture
def client():
    return TestClient(_create_app_with_auth())


class TestAuthFilter:
    """Tests for AuthFilter."""

    def test_valid_auth_passes(self, client):
        response = client.get(
            "/protected",
            headers={"Authorization": "Bearer valid-token"},
        )
        assert response.status_code == 200
        assert response.json() == {"message": "secret data"}

    def test_missing_auth_returns_401(self, client):
        response = client.get("/protected")
        assert response.status_code == 401
        assert response.json() == {"detail": "Authentication required"}
        assert "WWW-Authenticate" in response.headers

    def test_invalid_auth_returns_401(self, client):
        response = client.get(
            "/protected",
            headers={"Authorization": "Bearer bad-token"},
        )
        assert response.status_code == 401

    def test_endpoint_not_called_on_auth_failure(self):
        call_count = 0

        app = FastAPI()

        @app.get("/test")
        async def endpoint():
            nonlocal call_count
            call_count += 1
            return {"data": "test"}

        chain = FilterChain()
        chain.add_filter(AuthFilter(authenticate=_valid_auth))
        app.add_middleware(FilterChainMiddleware, filter_chain=chain)

        client = TestClient(app)
        client.get("/test")
        assert call_count == 0

    def test_auth_sets_context_user(self):
        """Auth filter should populate context.user for downstream access."""
        captured_user = {}

        app = FastAPI()

        @app.get("/whoami")
        async def whoami(request: Request):
            ctx = request.state.filter_context
            captured_user.update(ctx.user or {})
            return {"user": ctx.user}

        chain = FilterChain()
        chain.add_filter(AuthFilter(authenticate=_valid_auth))
        app.add_middleware(FilterChainMiddleware, filter_chain=chain)

        client = TestClient(app)
        response = client.get(
            "/whoami",
            headers={"Authorization": "Bearer valid-token"},
        )
        assert response.status_code == 200
        assert captured_user["id"] == "user-1"
        assert captured_user["role"] == "admin"

    def test_auth_order_is_10(self):
        f = AuthFilter(authenticate=_valid_auth)
        assert f.order == 10

    def test_auth_runs_before_content_negotiation(self):
        """Auth (order=10) should run before ContentNegotiation (order=50)."""
        from slip_stream.adapters.api.filters.content_negotiation import (
            ContentNegotiationFilter,
        )

        auth = AuthFilter(authenticate=_valid_auth)
        cn = ContentNegotiationFilter()
        assert auth.order < cn.order

    def test_custom_realm(self):
        f = AuthFilter(authenticate=_valid_auth, realm="my-api")
        assert f.realm == "my-api"
