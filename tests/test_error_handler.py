"""Tests for structured error handlers."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from slip_stream.adapters.api.error_handler import install_error_handlers


class ItemCreate(BaseModel):
    name: str
    count: int


def _create_app() -> FastAPI:
    """Create a test app with structured error handlers installed."""
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/ok")
    async def ok():
        return {"status": "ok"}

    @app.get("/not-found")
    async def not_found():
        raise HTTPException(status_code=404, detail="widget not found")

    @app.get("/forbidden")
    async def forbidden():
        raise HTTPException(status_code=403, detail="access denied")

    @app.get("/crash")
    async def crash():
        raise RuntimeError("unexpected failure")

    @app.post("/validate")
    async def validate(item: ItemCreate):
        return {"name": item.name}

    return app


@pytest.fixture
def client():
    return TestClient(_create_app(), raise_server_exceptions=False)


class TestStructuredErrorHandlers:
    """Tests for install_error_handlers()."""

    def test_successful_response_unaffected(self, client):
        response = client.get("/ok")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_http_exception_structured(self, client):
        response = client.get("/not-found")
        assert response.status_code == 404
        data = response.json()
        assert "error" in data
        assert data["error"]["status"] == 404
        assert data["error"]["detail"] == "widget not found"
        assert data["error"]["type"] == "HTTPException"

    def test_http_exception_403(self, client):
        response = client.get("/forbidden")
        assert response.status_code == 403
        data = response.json()
        assert data["error"]["status"] == 403
        assert data["error"]["detail"] == "access denied"

    def test_validation_error_structured(self, client):
        response = client.post("/validate", json={"name": "test", "count": "not-int"})
        assert response.status_code == 422
        data = response.json()
        assert "error" in data
        assert data["error"]["status"] == 422
        assert data["error"]["type"] == "RequestValidationError"
        assert "errors" in data["error"]

    def test_validation_error_missing_field(self, client):
        response = client.post("/validate", json={})
        assert response.status_code == 422
        data = response.json()
        assert data["error"]["status"] == 422

    def test_generic_exception_structured(self, client):
        response = client.get("/crash")
        assert response.status_code == 500
        data = response.json()
        assert "error" in data
        assert data["error"]["status"] == 500
        assert data["error"]["detail"] == "Internal server error"
        assert data["error"]["type"] == "RuntimeError"

    def test_error_response_is_json(self, client):
        response = client.get("/not-found")
        assert "application/json" in response.headers["content-type"]

    def test_multiple_errors_consistent_format(self, client):
        """All error types share the same top-level structure."""
        endpoints = ["/not-found", "/crash"]
        for endpoint in endpoints:
            response = client.get(endpoint)
            data = response.json()
            assert "error" in data
            error = data["error"]
            assert "status" in error
            assert "detail" in error
            assert "type" in error
