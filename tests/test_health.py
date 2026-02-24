"""Tests for health and readiness probe endpoints."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from slip_stream.adapters.api.health import create_health_router


class _FakeDbManager:
    """Minimal stub for DatabaseManager."""

    def __init__(self, *, connected: bool = True) -> None:
        self.db = _FakeDb(connected) if connected else None


class _FakeDb:
    def __init__(self, should_succeed: bool) -> None:
        self._ok = should_succeed

    async def command(self, cmd: str) -> dict:
        if not self._ok:
            raise ConnectionError("not connected")
        return {"ok": 1}


class _FakeRegistry:
    def __init__(self, names: list[str] | None = None) -> None:
        self._names = names or []

    def get_schema_names(self) -> list[str]:
        return self._names


class TestHealthEndpoint:
    def test_health_returns_200(self):
        app = FastAPI()
        app.include_router(create_health_router())
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy"}


class TestReadyEndpoint:
    def test_ready_all_checks_pass(self):
        app = FastAPI()
        app.include_router(
            create_health_router(
                db_manager=_FakeDbManager(connected=True),
                schema_registry=_FakeRegistry(["widget"]),
            )
        )
        client = TestClient(app)
        resp = client.get("/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["checks"]["database"] is True
        assert data["checks"]["schemas"] is True

    def test_ready_503_when_db_not_connected(self):
        app = FastAPI()
        app.include_router(
            create_health_router(
                db_manager=_FakeDbManager(connected=False),
                schema_registry=_FakeRegistry(["widget"]),
            )
        )
        client = TestClient(app)
        resp = client.get("/ready")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "not_ready"
        assert data["checks"]["database"] is False

    def test_ready_503_when_no_schemas(self):
        app = FastAPI()
        app.include_router(
            create_health_router(
                db_manager=_FakeDbManager(connected=True),
                schema_registry=_FakeRegistry([]),
            )
        )
        client = TestClient(app)
        resp = client.get("/ready")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "not_ready"
        assert data["checks"]["schemas"] is False

    def test_ready_assumes_db_ok_when_no_manager(self):
        """When no db_manager (external get_db), database check passes."""
        app = FastAPI()
        app.include_router(
            create_health_router(
                db_manager=None,
                schema_registry=_FakeRegistry(["widget"]),
            )
        )
        client = TestClient(app)
        resp = client.get("/ready")
        assert resp.status_code == 200
        assert resp.json()["checks"]["database"] is True

    def test_ready_503_when_ping_fails(self):
        """Database ping exception results in database check failure."""
        db_manager = _FakeDbManager(connected=True)
        db_manager.db._ok = False  # make ping raise
        app = FastAPI()
        app.include_router(
            create_health_router(
                db_manager=db_manager,
                schema_registry=_FakeRegistry(["widget"]),
            )
        )
        client = TestClient(app)
        resp = client.get("/ready")
        assert resp.status_code == 503
        assert resp.json()["checks"]["database"] is False
