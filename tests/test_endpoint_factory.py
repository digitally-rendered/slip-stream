"""Tests for EndpointFactory (API endpoint generation)."""

from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from slip_stream.adapters.api.endpoint_factory import EndpointFactory
from slip_stream.core.schema.registry import SchemaRegistry


# Use a mutable holder so each test gets a fresh DB via the fixture,
# while the dependency function signature remains stable for FastAPI.
_db_holder: Dict[str, Any] = {}


def _get_db() -> Any:
    return _db_holder["db"]


def _get_current_user() -> Dict[str, Any]:
    return {"id": "test-user"}


@pytest.fixture(autouse=True)
def _fresh_endpoint_db():
    """Give each test a fresh mock database."""
    client = AsyncMongoMockClient()
    _db_holder["db"] = client["test_endpoint_db"]
    yield
    _db_holder.clear()


@pytest.fixture
def app(registry):
    """Create a FastAPI app with widget endpoints."""
    app = FastAPI()

    router = EndpointFactory.create_router(
        schema_name="widget",
        get_db=_get_db,
        get_current_user=_get_current_user,
    )
    app.include_router(router, prefix="/api/v1/widget")
    return app


@pytest.fixture
def client(app):
    """Return a TestClient for the app."""
    return TestClient(app)


class TestEndpointFactory:
    """Tests for auto-generated CRUD endpoints."""

    def test_create_endpoint(self, client):
        """POST / creates a new entity."""
        response = client.post(
            "/api/v1/widget/",
            json={"name": "Test Widget", "color": "blue"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Test Widget"
        assert data["record_version"] == 1
        assert "entity_id" in data

    def test_get_endpoint(self, client):
        """GET /{entity_id} returns the created entity."""
        create_resp = client.post(
            "/api/v1/widget/",
            json={"name": "Get Widget"},
        )
        entity_id = create_resp.json()["entity_id"]

        response = client.get(f"/api/v1/widget/{entity_id}")
        assert response.status_code == 200
        assert response.json()["name"] == "Get Widget"

    def test_get_not_found(self, client):
        """GET /{entity_id} returns 404 for non-existent entity."""
        response = client.get(
            "/api/v1/widget/12345678-1234-1234-1234-123456789012"
        )
        assert response.status_code == 404

    def test_list_endpoint(self, client):
        """GET / returns a list of entities."""
        client.post("/api/v1/widget/", json={"name": "Widget 1"})
        client.post("/api/v1/widget/", json={"name": "Widget 2"})

        response = client.get("/api/v1/widget/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    def test_update_endpoint(self, client):
        """PATCH /{entity_id} updates the entity."""
        create_resp = client.post(
            "/api/v1/widget/",
            json={"name": "Original", "color": "red"},
        )
        entity_id = create_resp.json()["entity_id"]

        response = client.patch(
            f"/api/v1/widget/{entity_id}",
            json={"color": "green"},
        )
        assert response.status_code == 200
        assert response.json()["record_version"] == 2

    def test_delete_endpoint(self, client):
        """DELETE /{entity_id} soft-deletes the entity."""
        create_resp = client.post(
            "/api/v1/widget/",
            json={"name": "Delete Me"},
        )
        entity_id = create_resp.json()["entity_id"]

        response = client.delete(f"/api/v1/widget/{entity_id}")
        assert response.status_code == 204

        # Verify it's gone
        get_resp = client.get(f"/api/v1/widget/{entity_id}")
        assert get_resp.status_code == 404

    def test_invalid_entity_id_format(self, client):
        """GET with invalid UUID returns 400."""
        response = client.get("/api/v1/widget/not-a-uuid")
        assert response.status_code == 400

    def test_get_db_required(self, registry):
        """EndpointFactory.create_router() raises if get_db not provided."""
        with pytest.raises(ValueError, match="get_db dependency must be provided"):
            EndpointFactory.create_router(schema_name="widget")
