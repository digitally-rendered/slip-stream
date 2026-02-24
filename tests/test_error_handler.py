"""Tests for RFC 7807 Problem Details error handlers."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from slip_stream.adapters.api.error_handler import (
    ERROR_TYPE_BASE,
    PROBLEM_JSON,
    install_error_handlers,
)


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


class TestProblemDetails:
    """Tests for RFC 7807 Problem Details format."""

    def test_successful_response_unaffected(self, client):
        response = client.get("/ok")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_404_problem_details(self, client):
        response = client.get("/not-found")
        assert response.status_code == 404
        data = response.json()
        assert data["type"] == f"{ERROR_TYPE_BASE}not-found"
        assert data["title"] == "Not Found"
        assert data["status"] == 404
        assert data["detail"] == "widget not found"
        assert data["instance"] == "/not-found"

    def test_403_problem_details(self, client):
        response = client.get("/forbidden")
        assert response.status_code == 403
        data = response.json()
        assert data["type"] == f"{ERROR_TYPE_BASE}policy-denied"
        assert data["title"] == "Policy Denied"
        assert data["status"] == 403
        assert data["detail"] == "access denied"

    def test_validation_error_problem_details(self, client):
        response = client.post("/validate", json={"name": "test", "count": "not-int"})
        assert response.status_code == 422
        data = response.json()
        assert data["type"] == f"{ERROR_TYPE_BASE}validation-error"
        assert data["title"] == "Validation Error"
        assert data["status"] == 422
        assert data["detail"] == "Validation error"
        assert "errors" in data
        assert isinstance(data["errors"], list)
        assert data["instance"] == "/validate"

    def test_validation_error_missing_field(self, client):
        response = client.post("/validate", json={})
        assert response.status_code == 422
        data = response.json()
        assert data["status"] == 422
        assert len(data["errors"]) > 0

    def test_generic_exception_problem_details(self, client):
        response = client.get("/crash")
        assert response.status_code == 500
        data = response.json()
        assert data["type"] == f"{ERROR_TYPE_BASE}internal-error"
        assert data["title"] == "Internal Server Error"
        assert data["status"] == 500
        assert data["detail"] == "Internal server error"

    def test_content_type_is_problem_json(self, client):
        response = client.get("/not-found")
        assert PROBLEM_JSON in response.headers["content-type"]

    def test_all_errors_share_rfc7807_fields(self, client):
        """All error types include type, title, status, detail, instance."""
        endpoints = ["/not-found", "/crash", "/forbidden"]
        for endpoint in endpoints:
            response = client.get(endpoint)
            data = response.json()
            assert "type" in data, f"Missing 'type' for {endpoint}"
            assert "title" in data, f"Missing 'title' for {endpoint}"
            assert "status" in data, f"Missing 'status' for {endpoint}"
            assert "detail" in data, f"Missing 'detail' for {endpoint}"
            assert "instance" in data, f"Missing 'instance' for {endpoint}"
