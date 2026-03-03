"""End-to-end onboarding tests.

Validates that the full onboarding journey produces a working application:
  1. `slip init` creates a valid project scaffold
  2. `slip schema add` creates valid schemas that SchemaRegistry can load
  3. The generated main.py produces a FastAPI app with working CRUD endpoints
  4. The library API (SlipStream) works with user-created schemas
"""

import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from slip_stream import SlipStream
from slip_stream.cli import (
    build_parser,
    cmd_init,
    cmd_schema_add,
    cmd_schema_validate,
)
from slip_stream.core.schema.registry import SchemaRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset():
    SchemaRegistry.reset()
    yield
    SchemaRegistry.reset()


@pytest.fixture
def project_dir(tmp_path):
    """Scaffold a project via `slip init` and return its path."""
    target = tmp_path / "testproject"
    args = build_parser().parse_args(["init", str(target)])
    code = cmd_init(args)
    assert code == 0
    return target


# ---------------------------------------------------------------------------
# CLI onboarding flow
# ---------------------------------------------------------------------------


class TestCLIOnboardingFlow:
    """Test the CLI-driven onboarding: init -> add schemas -> validate."""

    def test_init_produces_valid_schema(self, project_dir):
        """The default item.json from `slip init` passes validation."""
        schema_file = project_dir / "schemas" / "item.json"
        schema = json.loads(schema_file.read_text())

        assert schema["title"] == "Item"
        assert schema["version"] == "1.0.0"
        assert schema["type"] == "object"
        assert "name" in schema["properties"]

    def test_init_schema_loadable_by_registry(self, project_dir):
        """SchemaRegistry can load the scaffolded project's schemas."""
        registry = SchemaRegistry(schema_dir=project_dir / "schemas")
        names = registry.get_schema_names()

        assert "item" in names
        doc, create, update = registry.get_model_for_version("item")
        assert doc is not None
        assert create is not None
        assert update is not None

    def test_add_schema_then_validate(self, project_dir, monkeypatch):
        """Add a schema via CLI, then validate passes."""
        monkeypatch.chdir(project_dir)

        # Add a new schema
        args = build_parser().parse_args(["schema", "add", "customer"])
        code = cmd_schema_add(args)
        assert code == 0
        assert (project_dir / "schemas" / "customer.json").exists()

        # Validate all schemas
        args = build_parser().parse_args(["schema", "validate"])
        code = cmd_schema_validate(args)
        assert code == 0

    def test_add_schema_loadable_by_registry(self, project_dir, monkeypatch):
        """A schema added via `slip schema add` is loadable by SchemaRegistry."""
        monkeypatch.chdir(project_dir)

        args = build_parser().parse_args(["schema", "add", "order"])
        cmd_schema_add(args)

        registry = SchemaRegistry(schema_dir=project_dir / "schemas")
        names = registry.get_schema_names()

        assert "item" in names
        assert "order" in names

        doc, create, update = registry.get_model_for_version("order")
        # The generated schema has a "name" field
        instance = create(name="test-order")
        assert instance.name == "test-order"

    def test_multiple_schemas_coexist(self, project_dir, monkeypatch):
        """Multiple schemas added via CLI all load correctly."""
        monkeypatch.chdir(project_dir)

        for name in ["widget", "gadget", "invoice"]:
            args = build_parser().parse_args(["schema", "add", name])
            assert cmd_schema_add(args) == 0

        registry = SchemaRegistry(schema_dir=project_dir / "schemas")
        names = registry.get_schema_names()

        # item (from init) + 3 added
        assert len(names) >= 4
        for name in ["item", "widget", "gadget", "invoice"]:
            assert name in names


# ---------------------------------------------------------------------------
# Generated app produces working endpoints
# ---------------------------------------------------------------------------


class TestGeneratedAppEndpoints:
    """Test that the scaffolded project produces a working FastAPI app."""

    def _build_app(self, schema_dir: Path) -> FastAPI:
        """Build a testable FastAPI app from a schema directory."""
        app = FastAPI()
        slip = SlipStream(
            app=app,
            schema_dir=schema_dir,
            api_prefix="/api/v1",
            get_db=AsyncMock(),
        )

        @asynccontextmanager
        async def lifespan(a):
            async with slip.lifespan():
                yield

        app = FastAPI(lifespan=lifespan)
        slip.app = app
        return app

    def test_init_app_has_item_endpoints(self, project_dir):
        """The scaffolded project has CRUD endpoints for the default item schema."""
        app = self._build_app(project_dir / "schemas")

        with TestClient(app) as client:
            # OpenAPI spec should have item endpoints
            resp = client.get("/openapi.json")
            assert resp.status_code == 200
            spec = resp.json()
            paths = list(spec["paths"].keys())

            assert "/api/v1/item/" in paths
            assert "/api/v1/item/{entity_id}" in paths

    def test_added_schema_produces_endpoints(self, project_dir, monkeypatch):
        """Schemas added via `slip schema add` appear as endpoints."""
        monkeypatch.chdir(project_dir)

        args = build_parser().parse_args(["schema", "add", "customer"])
        cmd_schema_add(args)

        app = self._build_app(project_dir / "schemas")

        with TestClient(app) as client:
            spec = client.get("/openapi.json").json()
            paths = list(spec["paths"].keys())

            assert "/api/v1/item/" in paths
            assert "/api/v1/customer/" in paths

    def test_kebab_case_endpoint_for_multi_word_schema(self, project_dir, monkeypatch):
        """Multi-word schema names produce kebab-case endpoints."""
        monkeypatch.chdir(project_dir)

        args = build_parser().parse_args(["schema", "add", "purchase_order"])
        cmd_schema_add(args)

        app = self._build_app(project_dir / "schemas")

        with TestClient(app) as client:
            spec = client.get("/openapi.json").json()
            paths = list(spec["paths"].keys())

            assert "/api/v1/purchase-order/" in paths
            assert "/api/v1/purchase-order/{entity_id}" in paths

    def test_endpoints_have_all_crud_methods(self, project_dir):
        """Each schema gets POST, GET (list + single), PATCH, DELETE."""
        app = self._build_app(project_dir / "schemas")

        with TestClient(app) as client:
            spec = client.get("/openapi.json").json()

            list_path = spec["paths"]["/api/v1/item/"]
            detail_path = spec["paths"]["/api/v1/item/{entity_id}"]

            assert "post" in list_path
            assert "get" in list_path
            assert "get" in detail_path
            assert "patch" in detail_path
            assert "delete" in detail_path

    def test_health_endpoint_available(self, project_dir):
        """The generated app has health probes."""
        app = self._build_app(project_dir / "schemas")

        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200

    def test_topology_endpoint_available(self, project_dir):
        """The generated app has a topology introspection endpoint."""
        app = self._build_app(project_dir / "schemas")

        with TestClient(app) as client:
            resp = client.get("/_topology")
            assert resp.status_code == 200
            data = resp.json()
            schema_names = [s["name"] for s in data["schemas"]]
            assert "item" in schema_names


# ---------------------------------------------------------------------------
# Library API onboarding (no CLI)
# ---------------------------------------------------------------------------


class TestLibraryOnboarding:
    """Test the library-driven onboarding: create schemas manually, wire SlipStream."""

    def test_manual_schema_file_produces_endpoints(self, tmp_path):
        """A hand-written JSON schema file produces working endpoints."""
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Product",
            "version": "1.0.0",
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "description": "Product name"},
                "price": {"type": "number", "description": "Price in dollars"},
                "in_stock": {"type": "boolean", "default": True},
            },
        }
        (schemas_dir / "product.json").write_text(json.dumps(schema, indent=2))

        app = FastAPI()
        slip = SlipStream(
            app=app,
            schema_dir=schemas_dir,
            api_prefix="/api/v1",
            get_db=AsyncMock(),
        )

        @asynccontextmanager
        async def lifespan(a):
            async with slip.lifespan():
                yield

        app = FastAPI(lifespan=lifespan)
        slip.app = app

        with TestClient(app) as client:
            spec = client.get("/openapi.json").json()
            paths = list(spec["paths"].keys())

            assert "/api/v1/product/" in paths
            assert "/api/v1/product/{entity_id}" in paths

    def test_schema_with_custom_fields_reflected_in_openapi(self, tmp_path):
        """Custom schema fields appear in the OpenAPI spec."""
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Task",
            "version": "1.0.0",
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": {"type": "string"},
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                },
                "due_date": {"type": "string", "format": "date-time"},
            },
        }
        (schemas_dir / "task.json").write_text(json.dumps(schema, indent=2))

        app = FastAPI()
        slip = SlipStream(
            app=app,
            schema_dir=schemas_dir,
            api_prefix="/api/v1",
            get_db=AsyncMock(),
        )

        @asynccontextmanager
        async def lifespan(a):
            async with slip.lifespan():
                yield

        app = FastAPI(lifespan=lifespan)
        slip.app = app

        with TestClient(app) as client:
            spec = client.get("/openapi.json").json()
            # Find the create schema in components
            schemas = spec.get("components", {}).get("schemas", {})
            # There should be a TaskCreate schema with our custom fields
            create_schema_names = [
                k for k in schemas if k.startswith("Task") and "Create" in k
            ]
            assert len(create_schema_names) >= 1

            create_schema = schemas[create_schema_names[0]]
            props = create_schema.get("properties", {})
            assert "title" in props
            assert "priority" in props

    def test_multiple_manual_schemas(self, tmp_path):
        """Multiple hand-written schemas all produce endpoints."""
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        for name in ["alpha", "beta", "gamma"]:
            schema = {
                "title": name.capitalize(),
                "version": "1.0.0",
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            }
            (schemas_dir / f"{name}.json").write_text(json.dumps(schema))

        app = FastAPI()
        slip = SlipStream(
            app=app,
            schema_dir=schemas_dir,
            api_prefix="/api/v1",
            get_db=AsyncMock(),
        )

        @asynccontextmanager
        async def lifespan(a):
            async with slip.lifespan():
                yield

        app = FastAPI(lifespan=lifespan)
        slip.app = app

        with TestClient(app) as client:
            spec = client.get("/openapi.json").json()
            paths = list(spec["paths"].keys())

            for name in ["alpha", "beta", "gamma"]:
                assert f"/api/v1/{name}/" in paths


# ---------------------------------------------------------------------------
# Generated main.py is importable
# ---------------------------------------------------------------------------


class TestGeneratedMainPyImportable:
    """Test that the generated main.py can be imported and produces an app."""

    def test_main_py_is_valid_python(self, project_dir):
        """The generated main.py is syntactically valid Python."""
        main_py = project_dir / "main.py"
        source = main_py.read_text()
        # compile() will raise SyntaxError if invalid
        compile(source, str(main_py), "exec")

    def test_main_py_contains_required_imports(self, project_dir):
        """The generated main.py imports SlipStream and FastAPI."""
        content = (project_dir / "main.py").read_text()
        assert "from slip_stream import SlipStream" in content
        assert "from fastapi import FastAPI" in content
        assert "create_app" in content
        assert "app = create_app()" in content
