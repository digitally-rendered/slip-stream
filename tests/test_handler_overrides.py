"""Tests for handler overrides via EntityRegistration.

Tests verify that custom handler overrides receive hydrated entities
via RequestContext and can replace default service behavior.
"""

from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from slip_stream.adapters.api.endpoint_factory import EndpointFactory
from slip_stream.container import EntityContainer
from slip_stream.core.context import RequestContext

_db_holder: Dict[str, Any] = {}


def _get_db() -> Any:
    return _db_holder["db"]


def _get_current_user() -> Dict[str, Any]:
    return {"id": "test-user"}


@pytest.fixture(autouse=True)
def _fresh_override_db():
    client = AsyncMongoMockClient()
    _db_holder["db"] = client["test_override_db"]
    yield
    _db_holder.clear()


@pytest.fixture
def registration(registry):
    """Get a resolved EntityRegistration for widget."""
    container = EntityContainer()
    container.resolve_all(["widget"])
    return container.get("widget")


def _create_widget_via(client, name="Test Widget", color="blue"):
    resp = client.post("/api/v1/widget/", json={"name": name, "color": color})
    assert resp.status_code == 201
    return resp.json()


class TestHandlerOverrides:
    """Tests for custom handler overrides."""

    def test_get_override_receives_hydrated_entity(self, registration):
        """GET override receives ctx.entity with hydrated data."""
        received_ctx = {}

        async def get_handler(ctx: RequestContext) -> Any:
            received_ctx["entity"] = ctx.entity
            received_ctx["entity_id"] = ctx.entity_id
            received_ctx["operation"] = ctx.operation
            return ctx.entity

        registration.handler_overrides["get"] = get_handler

        app = FastAPI()
        router = EndpointFactory.create_router_from_registration(
            registration=registration,
            get_db=_get_db,
            get_current_user=_get_current_user,
        )
        app.include_router(router, prefix="/api/v1/widget")
        client = TestClient(app)

        created = _create_widget_via(client, "Override Widget")
        entity_id = created["entity_id"]

        response = client.get(f"/api/v1/widget/{entity_id}")
        assert response.status_code == 200
        assert received_ctx["operation"] == "get"
        assert received_ctx["entity"] is not None
        assert str(received_ctx["entity_id"]) == entity_id

    def test_create_override_replaces_service(self, registration):
        """CREATE override replaces default service behavior."""

        async def create_handler(ctx: RequestContext) -> Any:
            # Custom behavior: add a suffix to name
            repo = registration.repository_class(ctx.db)
            service = registration.services["create"](repo)
            # Modify the data before creating
            return await service.execute(data=ctx.data, user_id=ctx.current_user["id"])

        registration.handler_overrides["create"] = create_handler

        app = FastAPI()
        router = EndpointFactory.create_router_from_registration(
            registration=registration,
            get_db=_get_db,
            get_current_user=_get_current_user,
        )
        app.include_router(router, prefix="/api/v1/widget")
        client = TestClient(app)

        response = client.post(
            "/api/v1/widget/",
            json={"name": "Custom Widget", "color": "purple"},
        )
        assert response.status_code == 201
        assert response.json()["name"] == "Custom Widget"

    def test_update_override_receives_entity_and_data(self, registration):
        """UPDATE override receives both hydrated entity and update data."""
        received = {}

        async def update_handler(ctx: RequestContext) -> Any:
            received["entity_name"] = (
                ctx.entity.name if hasattr(ctx.entity, "name") else None
            )
            received["update_data"] = ctx.data
            received["operation"] = ctx.operation
            # Delegate to default
            repo = registration.repository_class(ctx.db)
            service = registration.services["update"](repo)
            return await service.execute(
                entity_id=ctx.entity_id,
                data=ctx.data,
                user_id=ctx.current_user["id"],
            )

        registration.handler_overrides["update"] = update_handler

        app = FastAPI()
        router = EndpointFactory.create_router_from_registration(
            registration=registration,
            get_db=_get_db,
            get_current_user=_get_current_user,
        )
        app.include_router(router, prefix="/api/v1/widget")
        client = TestClient(app)

        created = _create_widget_via(client, "Original Name", "red")
        entity_id = created["entity_id"]

        response = client.patch(
            f"/api/v1/widget/{entity_id}",
            json={"color": "blue"},
        )
        assert response.status_code == 200
        assert received["operation"] == "update"
        assert received["entity_name"] == "Original Name"

    def test_delete_override_receives_hydrated_entity(self, registration):
        """DELETE override receives ctx.entity before deletion."""
        received = {}

        async def delete_handler(ctx: RequestContext) -> Any:
            received["entity"] = ctx.entity
            received["entity_id"] = ctx.entity_id
            # Perform the actual delete
            repo = registration.repository_class(ctx.db)
            service = registration.services["delete"](repo)
            await service.execute(
                entity_id=ctx.entity_id,
                user_id=ctx.current_user["id"],
            )

        registration.handler_overrides["delete"] = delete_handler

        app = FastAPI()
        router = EndpointFactory.create_router_from_registration(
            registration=registration,
            get_db=_get_db,
            get_current_user=_get_current_user,
        )
        app.include_router(router, prefix="/api/v1/widget")
        client = TestClient(app)

        created = _create_widget_via(client)
        entity_id = created["entity_id"]

        response = client.delete(f"/api/v1/widget/{entity_id}")
        assert response.status_code == 204
        assert received["entity"] is not None
        assert str(received["entity_id"]) == entity_id

    def test_list_override(self, registration):
        """LIST override receives pagination params."""
        received = {}

        async def list_handler(ctx: RequestContext) -> Any:
            received["skip"] = ctx.skip
            received["limit"] = ctx.limit
            received["operation"] = ctx.operation
            repo = registration.repository_class(ctx.db)
            service = registration.services["list"](repo)
            return await service.execute(skip=ctx.skip, limit=ctx.limit)

        registration.handler_overrides["list"] = list_handler

        app = FastAPI()
        router = EndpointFactory.create_router_from_registration(
            registration=registration,
            get_db=_get_db,
            get_current_user=_get_current_user,
        )
        app.include_router(router, prefix="/api/v1/widget")
        client = TestClient(app)

        _create_widget_via(client, "Widget 1")
        _create_widget_via(client, "Widget 2")

        response = client.get("/api/v1/widget/?skip=0&limit=50")
        assert response.status_code == 200
        assert received["operation"] == "list"
        assert received["skip"] == 0
        assert received["limit"] == 50
        assert len(response.json()) == 2

    def test_no_overrides_uses_defaults(self, registration):
        """Without overrides, default service behavior is used."""
        assert registration.handler_overrides == {}

        app = FastAPI()
        router = EndpointFactory.create_router_from_registration(
            registration=registration,
            get_db=_get_db,
            get_current_user=_get_current_user,
        )
        app.include_router(router, prefix="/api/v1/widget")
        client = TestClient(app)

        response = client.post(
            "/api/v1/widget/",
            json={"name": "Default Widget"},
        )
        assert response.status_code == 201
        assert response.json()["name"] == "Default Widget"

    def test_override_ctx_has_current_user(self, registration):
        """Override receives current_user in context."""
        received = {}

        async def get_handler(ctx: RequestContext) -> Any:
            received["user"] = ctx.current_user
            return ctx.entity

        registration.handler_overrides["get"] = get_handler

        app = FastAPI()
        router = EndpointFactory.create_router_from_registration(
            registration=registration,
            get_db=_get_db,
            get_current_user=_get_current_user,
        )
        app.include_router(router, prefix="/api/v1/widget")
        client = TestClient(app)

        created = _create_widget_via(client)
        client.get(f"/api/v1/widget/{created['entity_id']}")
        assert received["user"] == {"id": "test-user"}
