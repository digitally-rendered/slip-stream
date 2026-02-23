"""Tests for entity hydration (auto-lookup by entity_id in path).

Tests verify that GET, PATCH, DELETE handlers automatically hydrate
the entity from the database and return 404 for missing entities.
"""

from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from slip_stream.adapters.api.endpoint_factory import EndpointFactory
from slip_stream.core.schema.registry import SchemaRegistry


_db_holder: Dict[str, Any] = {}


def _get_db() -> Any:
    return _db_holder["db"]


def _get_current_user() -> Dict[str, Any]:
    return {"id": "test-user"}


@pytest.fixture(autouse=True)
def _fresh_hydration_db():
    client = AsyncMongoMockClient()
    _db_holder["db"] = client["test_hydration_db"]
    yield
    _db_holder.clear()


@pytest.fixture
def app(registry):
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
    return TestClient(app)


def _create_widget(client, name="Test Widget", color="blue"):
    resp = client.post("/api/v1/widget/", json={"name": name, "color": color})
    assert resp.status_code == 201
    return resp.json()


class TestEntityHydration:
    """Tests that entities are hydrated for ID-based operations."""

    def test_get_hydrates_entity(self, client):
        """GET returns the hydrated entity."""
        created = _create_widget(client, "Hydrated Widget")
        entity_id = created["entity_id"]

        response = client.get(f"/api/v1/widget/{entity_id}")
        assert response.status_code == 200
        assert response.json()["name"] == "Hydrated Widget"

    def test_get_nonexistent_returns_404(self, client):
        """GET for nonexistent entity returns 404."""
        fake_id = "12345678-1234-1234-1234-123456789012"
        response = client.get(f"/api/v1/widget/{fake_id}")
        assert response.status_code == 404

    def test_update_hydrates_entity(self, client):
        """PATCH hydrates entity before applying update."""
        created = _create_widget(client, "Original", "red")
        entity_id = created["entity_id"]

        response = client.patch(
            f"/api/v1/widget/{entity_id}",
            json={"color": "green"},
        )
        assert response.status_code == 200
        assert response.json()["color"] == "green"
        assert response.json()["record_version"] == 2

    def test_update_nonexistent_returns_404(self, client):
        """PATCH for nonexistent entity returns 404."""
        fake_id = "12345678-1234-1234-1234-123456789012"
        response = client.patch(
            f"/api/v1/widget/{fake_id}",
            json={"color": "green"},
        )
        assert response.status_code == 404

    def test_delete_hydrates_entity(self, client):
        """DELETE hydrates entity before deletion."""
        created = _create_widget(client)
        entity_id = created["entity_id"]

        response = client.delete(f"/api/v1/widget/{entity_id}")
        assert response.status_code == 204

        # Verify entity no longer returned
        response = client.get(f"/api/v1/widget/{entity_id}")
        assert response.status_code == 404

    def test_delete_nonexistent_returns_404(self, client):
        """DELETE for nonexistent entity returns 404."""
        fake_id = "12345678-1234-1234-1234-123456789012"
        response = client.delete(f"/api/v1/widget/{fake_id}")
        assert response.status_code == 404

    def test_invalid_id_format_returns_400(self, client):
        """Invalid UUID format returns 400, not 500."""
        response = client.get("/api/v1/widget/not-a-valid-uuid")
        assert response.status_code == 400

    def test_invalid_id_on_update_returns_400(self, client):
        response = client.patch(
            "/api/v1/widget/not-a-uuid",
            json={"color": "green"},
        )
        assert response.status_code == 400

    def test_invalid_id_on_delete_returns_400(self, client):
        response = client.delete("/api/v1/widget/not-a-uuid")
        assert response.status_code == 400
