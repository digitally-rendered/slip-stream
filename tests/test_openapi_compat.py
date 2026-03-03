"""OpenAPI backward compatibility tests.

Ensures that changes to slip-stream's code generation don't silently
break the API surface. The test generates an OpenAPI spec from sample schemas
and validates structural invariants that downstream consumers depend on.

To update the baseline after intentional API changes:
    make snapshot-api
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from slip_stream.core.schema.registry import SchemaRegistry
from slip_stream.testing.app_builder import build_test_app

SAMPLE_SCHEMAS_DIR = Path(__file__).parent / "sample_schemas"
BASELINE_PATH = Path(__file__).parent.parent / ".openapi-baseline.json"


@pytest.fixture(autouse=True)
def _reset():
    SchemaRegistry.reset()
    yield
    SchemaRegistry.reset()


@pytest.fixture
def openapi_spec():
    """Generate a fresh OpenAPI spec from sample schemas."""
    app = build_test_app(schema_dir=SAMPLE_SCHEMAS_DIR)
    client = TestClient(app)
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    return resp.json()


class TestOpenApiStructure:
    """Verify the generated OpenAPI spec has expected structure."""

    def test_has_info(self, openapi_spec):
        assert "info" in openapi_spec
        assert "title" in openapi_spec["info"]
        assert "version" in openapi_spec["info"]

    def test_has_paths(self, openapi_spec):
        assert "paths" in openapi_spec
        assert len(openapi_spec["paths"]) > 0

    def test_every_schema_has_endpoints(self, openapi_spec):
        """Each sample schema must produce CRUD paths in the spec."""
        registry = SchemaRegistry(schema_dir=SAMPLE_SCHEMAS_DIR)
        for name in registry.get_schema_names():
            path_name = name.replace("_", "-")
            list_path = f"/api/v1/{path_name}/"
            detail_path = f"/api/v1/{path_name}/{{entity_id}}"

            assert (
                list_path in openapi_spec["paths"]
            ), f"Missing list path for '{name}': {list_path}"
            assert (
                detail_path in openapi_spec["paths"]
            ), f"Missing detail path for '{name}': {detail_path}"

    def test_list_endpoint_has_get(self, openapi_spec):
        assert "get" in openapi_spec["paths"]["/api/v1/widget/"]

    def test_list_endpoint_has_post(self, openapi_spec):
        assert "post" in openapi_spec["paths"]["/api/v1/widget/"]

    def test_detail_endpoint_has_get_patch_delete(self, openapi_spec):
        detail = openapi_spec["paths"]["/api/v1/widget/{entity_id}"]
        assert "get" in detail
        assert "patch" in detail
        assert "delete" in detail


class TestOpenApiEndpointShapes:
    """Verify endpoint request/response shapes match schema expectations."""

    def test_post_has_request_body(self, openapi_spec):
        post = openapi_spec["paths"]["/api/v1/widget/"]["post"]
        assert "requestBody" in post

    def test_post_returns_201(self, openapi_spec):
        post = openapi_spec["paths"]["/api/v1/widget/"]["post"]
        assert "201" in post["responses"]

    def test_get_list_returns_200(self, openapi_spec):
        get = openapi_spec["paths"]["/api/v1/widget/"]["get"]
        assert "200" in get["responses"]

    def test_get_detail_returns_200(self, openapi_spec):
        get = openapi_spec["paths"]["/api/v1/widget/{entity_id}"]["get"]
        assert "200" in get["responses"]

    def test_patch_has_request_body(self, openapi_spec):
        patch = openapi_spec["paths"]["/api/v1/widget/{entity_id}"]["patch"]
        assert "requestBody" in patch

    def test_delete_returns_success(self, openapi_spec):
        delete = openapi_spec["paths"]["/api/v1/widget/{entity_id}"]["delete"]
        # Delete may return 200 or 204
        assert "200" in delete["responses"] or "204" in delete["responses"]


class TestOpenApiBaselineRegression:
    """Compare generated spec against a committed baseline.

    If no baseline exists, the test passes with a warning.
    To create/update the baseline: make snapshot-api
    """

    def test_no_paths_removed(self, openapi_spec):
        """No endpoints may be removed without a deliberate baseline update."""
        if not BASELINE_PATH.exists():
            pytest.skip("No baseline — run 'make snapshot-api' to create one")

        baseline = json.loads(BASELINE_PATH.read_text())
        baseline_paths = set(baseline.get("paths", {}).keys())
        current_paths = set(openapi_spec.get("paths", {}).keys())

        removed = baseline_paths - current_paths
        assert not removed, (
            f"Endpoints removed from API (breaking change): {removed}. "
            "If intentional, run 'make snapshot-api' to update the baseline."
        )

    def test_no_methods_removed(self, openapi_spec):
        """No HTTP methods may be removed from existing endpoints."""
        if not BASELINE_PATH.exists():
            pytest.skip("No baseline — run 'make snapshot-api' to create one")

        baseline = json.loads(BASELINE_PATH.read_text())

        for path, methods in baseline.get("paths", {}).items():
            if path not in openapi_spec.get("paths", {}):
                continue  # path removal caught by test_no_paths_removed
            current_methods = set(openapi_spec["paths"][path].keys())
            baseline_methods = set(methods.keys())
            removed = baseline_methods - current_methods
            assert not removed, (
                f"Methods removed from {path}: {removed}. "
                "If intentional, run 'make snapshot-api' to update the baseline."
            )

    def test_no_required_fields_added_to_existing_endpoints(self, openapi_spec):
        """Adding required fields to existing request bodies is a breaking change."""
        if not BASELINE_PATH.exists():
            pytest.skip("No baseline — run 'make snapshot-api' to create one")

        baseline = json.loads(BASELINE_PATH.read_text())

        for path, methods in baseline.get("paths", {}).items():
            if path not in openapi_spec.get("paths", {}):
                continue
            for method, spec in methods.items():
                if method not in openapi_spec["paths"][path]:
                    continue

                old_body = spec.get("requestBody", {})
                new_body = openapi_spec["paths"][path][method].get("requestBody", {})

                # Extract required fields from both
                old_required = self._extract_required(old_body)
                new_required = self._extract_required(new_body)

                added_required = new_required - old_required
                assert not added_required, (
                    f"New required fields in {method.upper()} {path}: {added_required}. "
                    "Adding required fields to existing endpoints is a breaking change."
                )

    @staticmethod
    def _extract_required(request_body: dict) -> set:
        """Extract required field names from an OpenAPI requestBody."""
        required = set()
        content = request_body.get("content", {})
        for media_type, media_spec in content.items():
            schema = media_spec.get("schema", {})
            required.update(schema.get("required", []))
        return required
