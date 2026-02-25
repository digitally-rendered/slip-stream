"""Tests for slip_stream/adapters/api/schema_router.py."""

from typing import Any, Dict

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from slip_stream.adapters.api.schema_router import (
    register_schema_endpoint,
    register_schema_endpoint_from_registration,
    register_schema_endpoints,
)
from slip_stream.container import EntityContainer, EntityRegistration
from slip_stream.core.schema.registry import SchemaRegistry

# ---------------------------------------------------------------------------
# Shared mutable db holder — same pattern used in test_endpoint_factory.py
# ---------------------------------------------------------------------------

_db_holder: Dict[str, Any] = {}


def _get_db() -> Any:
    return _db_holder["db"]


def _get_current_user() -> Dict[str, Any]:
    return {"id": "test-user"}


@pytest.fixture(autouse=True)
def _fresh_db():
    """Provide a fresh mongomock database for every test."""
    client = AsyncMongoMockClient()
    _db_holder["db"] = client["test_router_db"]
    yield
    _db_holder.clear()


# ---------------------------------------------------------------------------
# Helper: build a loaded SchemaRegistry and resolve a container
# ---------------------------------------------------------------------------


@pytest.fixture
def loaded_registry(schema_dir):
    """Return a SchemaRegistry pre-loaded from the sample_schemas directory."""
    return SchemaRegistry(schema_dir=schema_dir)


@pytest.fixture
def widget_registration(loaded_registry):
    """Return an EntityRegistration resolved from the widget schema."""
    container = EntityContainer()
    container.resolve_all(["widget"])
    return container.get("widget")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRegisterSchemaEndpointsMulti:
    """register_schema_endpoints() registers routes for each schema in the list."""

    def test_register_schema_endpoints_multi(self, loaded_registry):
        """Routes for both 'widget' and 'gadget' are present after registration."""
        api_router = APIRouter()
        register_schema_endpoints(
            api_router=api_router,
            schema_names=["widget", "gadget"],
            get_db=_get_db,
            get_current_user=_get_current_user,
        )

        app = FastAPI()
        app.include_router(api_router, prefix="/api/v1")
        client = TestClient(app)

        # Widget and gadget collections should each have a POST endpoint
        resp_widget = client.post("/api/v1/widget/", json={"name": "Test Widget"})
        assert resp_widget.status_code == 201

        resp_gadget = client.post("/api/v1/gadget/", json={"label": "Test Gadget"})
        assert resp_gadget.status_code == 201

    def test_register_schema_endpoints_routes_count(self, loaded_registry):
        """Two schemas produce at least 10 routes (5 per schema)."""
        api_router = APIRouter()
        register_schema_endpoints(
            api_router=api_router,
            schema_names=["widget", "gadget"],
            get_db=_get_db,
            get_current_user=_get_current_user,
        )

        # Each schema generates POST, GET (list), GET (by id), PATCH, DELETE = 5 routes
        route_paths = [r.path for r in api_router.routes]
        widget_routes = [p for p in route_paths if "widget" in p]
        gadget_routes = [p for p in route_paths if "gadget" in p]

        assert len(widget_routes) >= 5
        assert len(gadget_routes) >= 5


class TestRegisterSchemaEndpointCustomPath:
    """register_schema_endpoint() supports a custom URL path override."""

    def test_register_schema_endpoint_custom_path(self, loaded_registry):
        """Endpoints are served under the custom path, not the default kebab-case name."""
        api_router = APIRouter()
        register_schema_endpoint(
            api_router=api_router,
            schema_name="widget",
            get_db=_get_db,
            get_current_user=_get_current_user,
            custom_path="my-widgets",
        )

        app = FastAPI()
        app.include_router(api_router, prefix="/api/v1")
        client = TestClient(app)

        # The custom path should work
        resp = client.post("/api/v1/my-widgets/", json={"name": "Custom Path Widget"})
        assert resp.status_code == 201

        # The default path should NOT exist
        default_resp = client.post("/api/v1/widget/", json={"name": "Should Not Exist"})
        assert default_resp.status_code == 404

    def test_register_schema_endpoint_default_path(self, loaded_registry):
        """Without custom_path, the default kebab-case path is used."""
        api_router = APIRouter()
        register_schema_endpoint(
            api_router=api_router,
            schema_name="widget",
            get_db=_get_db,
            get_current_user=_get_current_user,
        )

        app = FastAPI()
        app.include_router(api_router, prefix="/api/v1")
        client = TestClient(app)

        resp = client.post("/api/v1/widget/", json={"name": "Default Path Widget"})
        assert resp.status_code == 201


class TestRegisterSchemaEndpointCustomTags:
    """register_schema_endpoint() supports custom OpenAPI tags."""

    def test_register_schema_endpoint_custom_tags(self, loaded_registry):
        """Custom tags appear in the OpenAPI schema for the generated routes."""
        api_router = APIRouter()
        register_schema_endpoint(
            api_router=api_router,
            schema_name="widget",
            get_db=_get_db,
            get_current_user=_get_current_user,
            custom_tags=["Custom Tag"],
        )

        app = FastAPI()
        app.include_router(api_router, prefix="/api/v1")

        openapi = app.openapi()
        # Collect all tags used across all operations in the OpenAPI spec
        used_tags: set = set()
        for path_item in openapi.get("paths", {}).values():
            for operation in path_item.values():
                if isinstance(operation, dict):
                    for tag in operation.get("tags", []):
                        used_tags.add(tag)

        assert "Custom Tag" in used_tags

    def test_register_schema_endpoint_default_tags(self, loaded_registry):
        """Without custom_tags, the schema name title-case tag is applied."""
        api_router = APIRouter()
        register_schema_endpoint(
            api_router=api_router,
            schema_name="widget",
            get_db=_get_db,
            get_current_user=_get_current_user,
        )

        app = FastAPI()
        app.include_router(api_router, prefix="/api/v1")

        openapi = app.openapi()
        used_tags: set = set()
        for path_item in openapi.get("paths", {}).values():
            for operation in path_item.values():
                if isinstance(operation, dict):
                    for tag in operation.get("tags", []):
                        used_tags.add(tag)

        assert "Widget" in used_tags


class TestRegisterSchemaEndpointFromRegistration:
    """register_schema_endpoint_from_registration() uses EntityRegistration."""

    def test_register_schema_endpoint_from_registration(self, widget_registration):
        """Endpoints are created from a pre-resolved EntityRegistration."""
        api_router = APIRouter()
        register_schema_endpoint_from_registration(
            api_router=api_router,
            registration=widget_registration,
            get_db=_get_db,
            get_current_user=_get_current_user,
        )

        app = FastAPI()
        app.include_router(api_router, prefix="/api/v1")
        client = TestClient(app)

        resp = client.post("/api/v1/widget/", json={"name": "Registration Widget"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Registration Widget"
        assert data["record_version"] == 1

    def test_register_schema_endpoint_from_registration_uses_controller_factory(
        self, widget_registration
    ):
        """When registration.controller_factory is set, that factory is used instead."""
        custom_router_called = []

        def my_controller_factory(reg: EntityRegistration) -> APIRouter:
            custom_router_called.append(True)
            router = APIRouter()

            @router.get("/custom-health")
            def health():
                return {"status": "ok"}

            return router

        widget_registration.controller_factory = my_controller_factory

        api_router = APIRouter()
        register_schema_endpoint_from_registration(
            api_router=api_router,
            registration=widget_registration,
            get_db=_get_db,
            get_current_user=_get_current_user,
        )

        # The custom factory was invoked
        assert custom_router_called, "controller_factory was not called"

        app = FastAPI()
        app.include_router(api_router, prefix="/api/v1")
        client = TestClient(app)

        resp = client.get("/api/v1/widget/custom-health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
