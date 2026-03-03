"""Tests for EndpointFactory (API endpoint generation)."""

from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from slip_stream.adapters.api.endpoint_factory import EndpointFactory

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
        response = client.get("/api/v1/widget/12345678-1234-1234-1234-123456789012")
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


# ---------------------------------------------------------------------------
# Helpers for registration-based tests
# ---------------------------------------------------------------------------


def _make_registration_app(registry, event_bus=None):
    """Build a FastAPI app using create_router_from_registration."""
    from slip_stream.container import EntityContainer

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
def reg_app(registry):
    """FastAPI app wired via create_router_from_registration."""
    return _make_registration_app(registry)


@pytest.fixture
def reg_client(reg_app):
    """TestClient for the registration-based app."""
    return TestClient(reg_app)


# ---------------------------------------------------------------------------
# create_router_from_registration tests
# ---------------------------------------------------------------------------


class TestRegistrationBasedEndpoints:

    def test_create_via_registration(self, reg_client):
        """POST / using create_router_from_registration creates a new entity."""
        response = reg_client.post(
            "/api/v1/widget/",
            json={"name": "Reg Widget", "color": "green"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Reg Widget"
        assert data["record_version"] == 1
        assert "entity_id" in data

    def test_get_via_registration(self, reg_client):
        """GET /{entity_id} returns the entity created via registration path."""
        create_resp = reg_client.post(
            "/api/v1/widget/",
            json={"name": "Reg Get Widget"},
        )
        entity_id = create_resp.json()["entity_id"]

        response = reg_client.get(f"/api/v1/widget/{entity_id}")
        assert response.status_code == 200
        assert response.json()["name"] == "Reg Get Widget"

    def test_update_not_found_404(self, reg_client):
        """PATCH a nonexistent entity_id returns 404."""
        fake_id = "12345678-1234-1234-1234-123456789012"
        response = reg_client.patch(
            f"/api/v1/widget/{fake_id}",
            json={"color": "purple"},
        )
        assert response.status_code == 404

    def test_delete_not_found_404(self, reg_client):
        """DELETE a nonexistent entity_id returns 404."""
        fake_id = "12345678-1234-1234-1234-123456789012"
        response = reg_client.delete(f"/api/v1/widget/{fake_id}")
        assert response.status_code == 404

    def test_list_with_where_filter(self, reg_client):
        """GET / with a valid ?where= clause returns 200 and passes through the DSL."""
        import json as _json

        reg_client.post("/api/v1/widget/", json={"name": "Alpha"})
        reg_client.post("/api/v1/widget/", json={"name": "Beta"})

        # record_version == 1 is always true for freshly created documents
        where = _json.dumps({"record_version": {"_eq": 1}})
        response = reg_client.get(f"/api/v1/widget/?where={where}")
        assert response.status_code == 200
        # Both documents have record_version == 1 so both should appear
        assert len(response.json()) == 2

    def test_list_with_sort(self, reg_client):
        """GET / with ?sort=created_at returns 200 using an audit field for sorting."""
        reg_client.post("/api/v1/widget/", json={"name": "Zebra"})
        reg_client.post("/api/v1/widget/", json={"name": "Apple"})
        reg_client.post("/api/v1/widget/", json={"name": "Mango"})

        # created_at is always an allowed sort field
        response = reg_client.get("/api/v1/widget/?sort=created_at")
        assert response.status_code == 200
        assert len(response.json()) == 3

    def test_create_hook_error_returns_http_error(self, registry):
        """A pre_create hook that raises HookError returns the expected HTTP status."""
        from slip_stream.core.events import EventBus, HookError

        bus = EventBus()

        async def blocking_guard(ctx):
            raise HookError(403, "Access denied by guard")

        bus.register("pre_create", blocking_guard, schema_name="widget")

        app = _make_registration_app(registry, event_bus=bus)
        client = TestClient(app)

        response = client.post(
            "/api/v1/widget/",
            json={"name": "Blocked Widget"},
        )
        assert response.status_code == 403
        assert "Access denied by guard" in response.json()["detail"]

    def test_list_count_active(self, reg_client):
        """List endpoint calls count_active without error."""
        reg_client.post("/api/v1/widget/", json={"name": "Widget A"})
        reg_client.post("/api/v1/widget/", json={"name": "Widget B"})

        response = reg_client.get("/api/v1/widget/")
        assert response.status_code == 200
        # Two widgets were created; the list should return them
        assert len(response.json()) == 2

    # ------------------------------------------------------------------
    # Cursor pagination params on list
    # ------------------------------------------------------------------

    def test_list_cursor_params_accepted(self, reg_client):
        """GET / with first= query param is accepted (cursor mode)."""
        reg_client.post("/api/v1/widget/", json={"name": "CursorWidget"})

        response = reg_client.get("/api/v1/widget/?first=10")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_list_cursor_and_offset_conflict_returns_400(self, reg_client):
        """Mixing cursor params (first=) with skip= > 0 returns 400."""
        response = reg_client.get("/api/v1/widget/?first=5&skip=10")
        assert response.status_code == 400
        assert (
            "cursor" in response.json()["detail"].lower()
            or "offset" in response.json()["detail"].lower()
        )

    def test_list_skip_zero_with_cursor_does_not_conflict(self, reg_client):
        """skip=0 (default) combined with first= is NOT a conflict."""
        reg_client.post("/api/v1/widget/", json={"name": "NoCursorConflict"})

        response = reg_client.get("/api/v1/widget/?first=5&skip=0")
        assert response.status_code == 200

    # ------------------------------------------------------------------
    # Bulk create
    # ------------------------------------------------------------------

    def test_bulk_create_returns_200(self, reg_client):
        """POST /bulk creates multiple entities and returns BulkOperationResult."""
        response = reg_client.post(
            "/api/v1/widget/bulk",
            json=[{"name": "Bulk A"}, {"name": "Bulk B"}, {"name": "Bulk C"}],
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert data["succeeded"] == 3
        assert data["failed"] == 0
        assert len(data["items"]) == 3
        for item in data["items"]:
            assert item["status"] == "success"
            assert "entity_id" in item

    def test_bulk_create_over_limit_returns_400(self, reg_client):
        """POST /bulk with > 100 items returns 400."""
        items = [{"name": f"Widget {i}"} for i in range(101)]
        response = reg_client.post("/api/v1/widget/bulk", json=items)
        assert response.status_code == 400
        assert "100" in response.json()["detail"]

    def test_bulk_create_empty_list(self, reg_client):
        """POST /bulk with an empty list returns a zero-total result."""
        response = reg_client.post("/api/v1/widget/bulk", json=[])
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["succeeded"] == 0
        assert data["failed"] == 0

    # ------------------------------------------------------------------
    # Bulk update
    # ------------------------------------------------------------------

    def test_bulk_update_returns_200(self, reg_client):
        """PATCH /bulk updates entities and returns BulkOperationResult."""
        # Create two widgets first
        r1 = reg_client.post("/api/v1/widget/", json={"name": "UpA", "color": "red"})
        r2 = reg_client.post("/api/v1/widget/", json={"name": "UpB", "color": "blue"})
        eid1 = r1.json()["entity_id"]
        eid2 = r2.json()["entity_id"]

        response = reg_client.patch(
            "/api/v1/widget/bulk",
            json=[
                {"entity_id": eid1, "color": "green"},
                {"entity_id": eid2, "color": "yellow"},
            ],
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["succeeded"] == 2
        assert data["failed"] == 0

    def test_bulk_update_missing_entity_id_records_error(self, reg_client):
        """PATCH /bulk item without entity_id records an error for that item."""
        response = reg_client.patch(
            "/api/v1/widget/bulk",
            json=[{"color": "purple"}],  # no entity_id
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["failed"] == 1
        assert data["items"][0]["status"] == "error"

    def test_bulk_update_over_limit_returns_400(self, reg_client):
        """PATCH /bulk with > 100 items returns 400."""
        items = [
            {"entity_id": "00000000-0000-0000-0000-000000000001", "color": "x"}
        ] * 101
        response = reg_client.patch("/api/v1/widget/bulk", json=items)
        assert response.status_code == 400

    # ------------------------------------------------------------------
    # Bulk delete
    # ------------------------------------------------------------------

    def test_bulk_delete_returns_200(self, reg_client):
        """DELETE /bulk soft-deletes entities and returns BulkOperationResult."""
        import json as _json

        r1 = reg_client.post("/api/v1/widget/", json={"name": "DelA"})
        r2 = reg_client.post("/api/v1/widget/", json={"name": "DelB"})
        eid1 = r1.json()["entity_id"]
        eid2 = r2.json()["entity_id"]

        response = reg_client.request(
            "DELETE",
            "/api/v1/widget/bulk",
            content=_json.dumps([eid1, eid2]),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["succeeded"] == 2
        assert data["failed"] == 0

        # Verify both are gone from the active list
        list_resp = reg_client.get("/api/v1/widget/")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 0

    def test_bulk_delete_nonexistent_entity_records_error(self, reg_client):
        """DELETE /bulk with an unknown entity_id records an error for that item."""
        import json as _json

        fake_id = "12345678-1234-1234-1234-123456789abc"
        response = reg_client.request(
            "DELETE",
            "/api/v1/widget/bulk",
            content=_json.dumps([fake_id]),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["failed"] == 1
        assert data["items"][0]["status"] == "error"

    def test_bulk_delete_over_limit_returns_400(self, reg_client):
        """DELETE /bulk with > 100 items returns 400."""
        import json as _json

        ids = ["12345678-1234-1234-1234-000000000001"] * 101
        response = reg_client.request(
            "DELETE",
            "/api/v1/widget/bulk",
            content=_json.dumps(ids),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_bulk_delete_atomic_aborts_on_first_error(self, reg_client):
        """DELETE /bulk?atomic=true raises HookError on first failure."""
        import json as _json

        fake_id = "12345678-0000-0000-0000-000000000099"
        response = reg_client.request(
            "DELETE",
            "/api/v1/widget/bulk?atomic=true",
            content=_json.dumps([fake_id]),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422
