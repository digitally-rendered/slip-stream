"""Tests for bulk create, update, and delete REST endpoints.

All tests use the registration-based router (create_router_from_registration)
because bulk endpoints only exist on that path.
"""

from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from slip_stream.adapters.api.endpoint_factory import EndpointFactory
from slip_stream.container import EntityContainer
from slip_stream.core.events import EventBus, HookError

# ---------------------------------------------------------------------------
# Shared mutable DB holder — same pattern as test_endpoint_factory.py
# ---------------------------------------------------------------------------

_db_holder: Dict[str, Any] = {}


def _get_db() -> Any:
    return _db_holder["db"]


def _get_current_user() -> Dict[str, Any]:
    return {"id": "test-user"}


@pytest.fixture(autouse=True)
def _fresh_bulk_db():
    """Give each test a fresh mock database to prevent data leakage."""
    client = AsyncMongoMockClient()
    _db_holder["db"] = client["test_bulk_db"]
    yield
    _db_holder.clear()


# ---------------------------------------------------------------------------
# App + client factory helpers
# ---------------------------------------------------------------------------


def _make_app(registry, event_bus=None):
    """Build a FastAPI app with widget bulk endpoints via registration."""
    container = EntityContainer()
    container.resolve_all(registry.get_schema_names())
    reg = container.get("widget")

    app = FastAPI()
    router = EndpointFactory.create_router_from_registration(
        registration=reg,
        get_db=_get_db,
        get_current_user=_get_current_user,
        event_bus=event_bus,
    )
    app.include_router(router, prefix="/api/v1/widget")
    return app


@pytest.fixture
def bulk_app(registry):
    return _make_app(registry)


@pytest.fixture
def bulk_client(bulk_app):
    return TestClient(bulk_app)


# ---------------------------------------------------------------------------
# Helper: create a widget via the single-item endpoint
# ---------------------------------------------------------------------------


def _create_widget(client: TestClient, name: str, color: str = "red") -> Dict[str, Any]:
    resp = client.post("/api/v1/widget/", json={"name": name, "color": color})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ===========================================================================
# POST /bulk — bulk create
# ===========================================================================


class TestBulkCreate:

    def test_bulk_create_success(self, bulk_client):
        """POST /bulk with 3 valid items returns 200 with all succeeded."""
        payload = [
            {"name": "Widget A", "color": "red"},
            {"name": "Widget B", "color": "blue"},
            {"name": "Widget C", "color": "green"},
        ]
        response = bulk_client.post("/api/v1/widget/bulk", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert data["succeeded"] == 3
        assert data["failed"] == 0
        assert len(data["items"]) == 3
        for item in data["items"]:
            assert item["status"] == "success"

    def test_bulk_create_returns_entity_ids(self, bulk_client):
        """Each bulk-created item has a unique entity_id."""
        payload = [{"name": f"Widget {i}"} for i in range(4)]
        response = bulk_client.post("/api/v1/widget/bulk", json=payload)
        assert response.status_code == 200
        data = response.json()
        entity_ids = [item["entity_id"] for item in data["items"]]
        # All entity_ids must be present and unique
        assert all(eid for eid in entity_ids)
        assert len(set(entity_ids)) == 4

    def test_bulk_create_empty_list(self, bulk_client):
        """POST /bulk with an empty list returns 200 with total=0."""
        response = bulk_client.post("/api/v1/widget/bulk", json=[])
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["succeeded"] == 0
        assert data["failed"] == 0
        assert data["items"] == []

    def test_bulk_create_exceeds_limit(self, bulk_client):
        """POST /bulk with more than 100 items returns 400."""
        payload = [{"name": f"Widget {i}"} for i in range(101)]
        response = bulk_client.post("/api/v1/widget/bulk", json=payload)
        assert response.status_code == 400

    def test_bulk_create_record_versions(self, bulk_client):
        """All bulk-created items have record_version=1."""
        payload = [{"name": "Alpha"}, {"name": "Beta"}, {"name": "Gamma"}]
        response = bulk_client.post("/api/v1/widget/bulk", json=payload)
        assert response.status_code == 200
        for item in response.json()["items"]:
            assert item["record_version"] == 1

    def test_bulk_create_with_guard(self, registry):
        """A pre_create guard that rejects 'bad' name produces an error item."""
        bus = EventBus()

        async def reject_bad_name(ctx):
            data = ctx.data
            name_val = getattr(data, "name", None) or (
                data.get("name") if isinstance(data, dict) else None
            )
            if name_val == "bad":
                raise HookError(422, "name 'bad' is not allowed")

        bus.register("pre_create", reject_bad_name, schema_name="widget")

        app = _make_app(registry, event_bus=bus)
        client = TestClient(app)

        payload = [
            {"name": "good"},
            {"name": "bad"},
            {"name": "also-good"},
        ]
        response = client.post("/api/v1/widget/bulk", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert data["succeeded"] == 2
        assert data["failed"] == 1

        # Index 1 ("bad") is the failure
        failed_item = next(item for item in data["items"] if item["status"] == "error")
        assert failed_item["index"] == 1
        assert (
            "bad" in failed_item["error"].lower()
            or "not allowed" in failed_item["error"].lower()
        )

    def test_bulk_create_atomic_success(self, bulk_client):
        """POST /bulk?atomic=true with all-valid items still returns all succeeded."""
        payload = [{"name": "Atomic A"}, {"name": "Atomic B"}]
        response = bulk_client.post("/api/v1/widget/bulk?atomic=true", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["succeeded"] == 2
        assert data["failed"] == 0


# ===========================================================================
# PATCH /bulk — bulk update
# ===========================================================================


class TestBulkUpdate:

    def test_bulk_update_success(self, bulk_client):
        """PATCH /bulk with 2 valid entity_ids updates both; succeeded=2."""
        w1 = _create_widget(bulk_client, "Update Me 1", "red")
        w2 = _create_widget(bulk_client, "Update Me 2", "blue")

        payload = [
            {"entity_id": w1["entity_id"], "color": "purple"},
            {"entity_id": w2["entity_id"], "color": "orange"},
        ]
        response = bulk_client.patch("/api/v1/widget/bulk", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["succeeded"] == 2
        assert data["failed"] == 0
        for item in data["items"]:
            assert item["status"] == "success"
            assert item["record_version"] == 2

    def test_bulk_update_not_found(self, bulk_client):
        """PATCH /bulk with a nonexistent entity_id produces an error item."""
        fake_id = "00000000-0000-0000-0000-000000000001"
        payload = [{"entity_id": fake_id, "color": "yellow"}]
        response = bulk_client.patch("/api/v1/widget/bulk", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["succeeded"] == 0
        assert data["failed"] == 1
        assert data["items"][0]["status"] == "error"
        assert data["items"][0]["error"] is not None

    def test_bulk_update_partial_failure(self, bulk_client):
        """PATCH /bulk with a mix of valid and invalid entity_ids gives partial results."""
        w1 = _create_widget(bulk_client, "Partial Good", "red")
        fake_id = "00000000-0000-0000-0000-000000000002"

        payload = [
            {"entity_id": w1["entity_id"], "color": "cyan"},
            {"entity_id": fake_id, "color": "magenta"},
        ]
        response = bulk_client.patch("/api/v1/widget/bulk", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["succeeded"] == 1
        assert data["failed"] == 1

        statuses = {item["index"]: item["status"] for item in data["items"]}
        assert statuses[0] == "success"
        assert statuses[1] == "error"


# ===========================================================================
# DELETE /bulk — bulk delete
# ===========================================================================


class TestBulkDelete:

    def test_bulk_delete_success(self, bulk_client):
        """DELETE /bulk with 3 valid entity_ids returns succeeded=3."""
        w1 = _create_widget(bulk_client, "Delete Me 1")
        w2 = _create_widget(bulk_client, "Delete Me 2")
        w3 = _create_widget(bulk_client, "Delete Me 3")

        entity_ids = [w1["entity_id"], w2["entity_id"], w3["entity_id"]]
        response = bulk_client.request("DELETE", "/api/v1/widget/bulk", json=entity_ids)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert data["succeeded"] == 3
        assert data["failed"] == 0
        for item in data["items"]:
            assert item["status"] == "success"

    def test_bulk_delete_not_found(self, bulk_client):
        """DELETE /bulk with a nonexistent entity_id produces an error item."""
        fake_id = "00000000-0000-0000-0000-000000000003"
        response = bulk_client.request("DELETE", "/api/v1/widget/bulk", json=[fake_id])
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["succeeded"] == 0
        assert data["failed"] == 1
        assert data["items"][0]["status"] == "error"

    def test_bulk_delete_verified(self, bulk_client):
        """After bulk delete, individual GET for each entity returns 404."""
        w1 = _create_widget(bulk_client, "Gone 1")
        w2 = _create_widget(bulk_client, "Gone 2")
        entity_ids = [w1["entity_id"], w2["entity_id"]]

        delete_resp = bulk_client.request(
            "DELETE", "/api/v1/widget/bulk", json=entity_ids
        )
        assert delete_resp.status_code == 200
        assert delete_resp.json()["succeeded"] == 2

        for eid in entity_ids:
            get_resp = bulk_client.get(f"/api/v1/widget/{eid}")
            assert get_resp.status_code == 404, f"Expected 404 for deleted {eid}"
