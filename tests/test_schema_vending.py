"""Tests for the Schema Vending API."""

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from slip_stream.adapters.api.schema_vending import create_schema_vending_router
from slip_stream.app import SlipStream
from slip_stream.core.schema.registry import SchemaRegistry


@pytest.fixture(autouse=True)
def _reset():
    SchemaRegistry.reset()
    yield
    SchemaRegistry.reset()


@pytest.fixture
def schema_dir():
    return Path(__file__).parent / "sample_schemas"


@pytest.fixture
def registry(schema_dir):
    return SchemaRegistry(schema_dir=schema_dir)


@pytest.fixture
def client(registry):
    """TestClient with schema vending router mounted."""
    app = FastAPI()
    router = create_schema_vending_router(schema_registry=registry, prefix="/schemas")
    app.include_router(router)
    return TestClient(app)


class TestSchemaVendingAPI:

    def test_list_schemas(self, client):
        resp = client.get("/schemas/")
        assert resp.status_code == 200
        data = resp.json()
        names = [s["name"] for s in data["schemas"]]
        assert "widget" in names
        assert "gadget" in names

    def test_list_schemas_includes_versions(self, client):
        resp = client.get("/schemas/")
        data = resp.json()
        gadget = next(s for s in data["schemas"] if s["name"] == "gadget")
        assert "1.0.0" in gadget["versions"]
        assert gadget["latest_version"] == "1.0.0"

    def test_get_schema_versions(self, client, registry):
        # Add a second version directly to memory (no disk write)
        if "gadget" not in registry._schemas:
            registry._schemas["gadget"] = {}
        registry._schemas["gadget"]["2.0.0"] = {
            "type": "object",
            "version": "2.0.0",
            "properties": {"label": {"type": "string"}, "extra": {"type": "string"}},
        }
        resp = client.get("/schemas/gadget")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "gadget"
        assert "1.0.0" in data["versions"]
        assert "2.0.0" in data["versions"]
        assert data["latest_version"] == "2.0.0"

    def test_get_schema_latest(self, client):
        resp = client.get("/schemas/gadget/latest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "gadget"
        assert data["version"] == "1.0.0"
        assert "properties" in data["schema"]

    def test_get_schema_specific_version(self, client):
        resp = client.get("/schemas/gadget/1.0.0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "gadget"
        assert data["version"] == "1.0.0"
        assert "properties" in data["schema"]

    def test_get_schema_not_found(self, client):
        resp = client.get("/schemas/nonexistent")
        assert resp.status_code == 404

    def test_get_version_not_found(self, client):
        resp = client.get("/schemas/gadget/9.9.9")
        assert resp.status_code == 404

    def test_schema_response_includes_full_definition(self, client):
        resp = client.get("/schemas/gadget/1.0.0")
        data = resp.json()
        schema = data["schema"]
        assert "properties" in schema
        assert "label" in schema["properties"]

    def test_schema_dag(self, client):
        resp = client.get("/schemas/dag")
        assert resp.status_code == 200
        data = resp.json()
        assert "schemas" in data
        names = [s["name"] for s in data["schemas"]]
        assert "widget" in names
        assert "gadget" in names
        # Each node has required fields
        for node in data["schemas"]:
            assert "versions" in node
            assert "latest_version" in node
            assert "dependencies" in node


class TestSlipStreamVendingIntegration:
    """Test that schema_vending=True wires vending endpoints via SlipStream."""

    def test_vending_enabled(self, schema_dir):
        app = FastAPI()
        slip = SlipStream(
            app=app,
            schema_dir=schema_dir,
            schema_vending=True,
            schema_vending_prefix="/schemas",
            get_db=AsyncMock(),
        )

        @asynccontextmanager
        async def lifespan(app):
            async with slip.lifespan():
                yield

        app = FastAPI(lifespan=lifespan)
        slip.app = app

        with TestClient(app) as client:
            resp = client.get("/schemas/")
            assert resp.status_code == 200
            names = [s["name"] for s in resp.json()["schemas"]]
            assert "widget" in names

    def test_vending_disabled_by_default(self, schema_dir):
        app = FastAPI()
        slip = SlipStream(app=app, schema_dir=schema_dir, get_db=AsyncMock())

        @asynccontextmanager
        async def lifespan(app):
            async with slip.lifespan():
                yield

        app = FastAPI(lifespan=lifespan)
        slip.app = app

        with TestClient(app) as client:
            resp = client.get("/schemas/")
            assert resp.status_code == 404
