"""Tests for the /_topology introspection endpoint."""

import pytest
from dataclasses import dataclass, field
from fastapi import FastAPI
from fastapi.testclient import TestClient

from slip_stream.adapters.api.topology import create_topology_router


@dataclass
class _FakeRegistration:
    schema_name: str
    storage_backend: str = "mongo"
    handler_overrides: dict = field(default_factory=dict)
    controller_factory: object = None
    repository_class: type = type("AutoRepo", (), {"_is_auto_generated": True})


class _FakeContainer:
    def __init__(self, registrations: dict):
        self._registrations = registrations

    def get_all(self):
        return self._registrations


class _FakeRegistry:
    def __init__(self, versions: dict):
        self._versions = versions

    def get_all_versions(self, name: str):
        return self._versions.get(name, ["1.0.0"])


class _FakeFilter:
    def __init__(self, name: str, order: int):
        self.__class__ = type(name, (), {"order": order})
        self.order = order


class TestTopologyEndpoint:

    def _make_client(self, registrations=None, versions=None, filters=None, **config):
        regs = registrations or {
            "widget": _FakeRegistration(schema_name="widget"),
        }
        vers = versions or {"widget": ["1.0.0"]}

        container = _FakeContainer(regs)
        registry = _FakeRegistry(vers)

        app = FastAPI()
        router = create_topology_router(
            container=container,
            schema_registry=registry,
            filters=filters,
            api_prefix=config.get("api_prefix", "/api/v1"),
            graphql_enabled=config.get("graphql_enabled", False),
            graphql_prefix=config.get("graphql_prefix", "/graphql"),
            schema_vending_enabled=config.get("schema_vending_enabled", False),
            structured_errors=config.get("structured_errors", True),
            storage_default=config.get("storage_default", "mongo"),
        )
        app.include_router(router)
        return TestClient(app)

    def test_topology_returns_200(self):
        client = self._make_client()
        resp = client.get("/_topology")
        assert resp.status_code == 200

    def test_topology_schema_structure(self):
        client = self._make_client()
        data = client.get("/_topology").json()
        schemas = data["schemas"]
        assert len(schemas) == 1
        s = schemas[0]
        assert s["name"] == "widget"
        assert s["storage_backend"] == "mongo"
        assert s["versions"] == ["1.0.0"]
        assert s["has_custom_repository"] is False
        assert s["has_custom_controller"] is False
        assert s["endpoints"]["rest"] == "/api/v1/widget/"

    def test_topology_custom_handler_detected(self):
        reg = _FakeRegistration(
            schema_name="widget",
            handler_overrides={"create": lambda ctx: None},
        )
        client = self._make_client(registrations={"widget": reg})
        data = client.get("/_topology").json()
        s = data["schemas"][0]
        assert s["has_custom_handler"]["create"] is True
        assert s["has_custom_handler"]["get"] is False

    def test_topology_custom_repository_detected(self):
        class CustomRepo:
            pass

        reg = _FakeRegistration(schema_name="widget")
        reg.repository_class = CustomRepo
        client = self._make_client(registrations={"widget": reg})
        data = client.get("/_topology").json()
        assert data["schemas"][0]["has_custom_repository"] is True

    def test_topology_filters_sorted_by_order(self):
        filters = [
            _FakeFilter("EnvelopeFilter", 90),
            _FakeFilter("AuthFilter", 10),
            _FakeFilter("RateLimitFilter", 2),
        ]
        client = self._make_client(filters=filters)
        data = client.get("/_topology").json()
        orders = [f["order"] for f in data["filters"]]
        assert orders == [2, 10, 90]

    def test_topology_config_values(self):
        client = self._make_client(
            api_prefix="/api/v2",
            graphql_enabled=True,
            structured_errors=True,
            storage_default="sql",
        )
        data = client.get("/_topology").json()
        config = data["config"]
        assert config["api_prefix"] == "/api/v2"
        assert config["graphql_enabled"] is True
        assert config["structured_errors"] is True
        assert config["storage_default"] == "sql"

    def test_topology_multiple_schemas(self):
        regs = {
            "widget": _FakeRegistration(schema_name="widget"),
            "order": _FakeRegistration(schema_name="order", storage_backend="sql"),
        }
        client = self._make_client(
            registrations=regs,
            versions={"widget": ["1.0.0"], "order": ["1.0.0", "2.0.0"]},
        )
        data = client.get("/_topology").json()
        assert len(data["schemas"]) == 2
        names = {s["name"] for s in data["schemas"]}
        assert names == {"widget", "order"}

    def test_topology_no_secrets_exposed(self):
        """Topology must NOT contain database URIs, credentials, or env vars."""
        client = self._make_client()
        text = client.get("/_topology").text
        assert "mongodb://" not in text
        assert "localhost:27017" not in text
        assert "password" not in text.lower()
