"""Schema contract tests — verify schema → model → endpoint round-trip consistency.

These tests ensure that:
1. Every JSON schema produces valid Pydantic models (Document, Create, Update)
2. Generated models accept the fields defined in the schema
3. Generated endpoints use the correct models
4. Schema version bumps don't silently break the pipeline
5. The public API surface (__all__) matches actual exports
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from slip_stream.core.domain.base import BaseDocument
from slip_stream.core.schema.registry import SchemaRegistry
from slip_stream.testing.app_builder import build_test_app

SAMPLE_SCHEMAS_DIR = Path(__file__).parent / "sample_schemas"


@pytest.fixture(autouse=True)
def _reset():
    SchemaRegistry.reset()
    yield
    SchemaRegistry.reset()


class TestSchemaToModelContract:
    """Every schema file produces valid Document, Create, and Update models."""

    @pytest.fixture
    def registry(self):
        return SchemaRegistry(schema_dir=SAMPLE_SCHEMAS_DIR)

    def test_every_schema_produces_three_models(self, registry):
        """Each schema must generate Document, Create, and Update model variants."""
        for name in registry.get_schema_names():
            models = registry.get_model_for_version(name)
            assert models is not None, f"No models for schema '{name}'"
            doc_model, create_model, update_model = models

            assert issubclass(
                doc_model, BaseDocument
            ), f"{name} Document model must extend BaseDocument"
            assert issubclass(
                create_model, BaseModel
            ), f"{name} Create model must be a Pydantic model"
            assert issubclass(
                update_model, BaseModel
            ), f"{name} Update model must be a Pydantic model"

    def test_create_model_has_user_fields(self, registry):
        """Create models must include non-audit user-defined fields."""
        audit_fields = {
            "id",
            "entity_id",
            "schema_version",
            "record_version",
            "created_at",
            "updated_at",
            "deleted_at",
            "created_by",
            "updated_by",
            "deleted_by",
        }
        for name in registry.get_schema_names():
            schema = registry.get_schema(name, "latest")
            user_fields = set(schema.get("properties", {}).keys()) - audit_fields
            _, create_model, _ = registry.get_model_for_version(name)
            create_field_names = set(create_model.model_fields.keys())

            for field in user_fields:
                assert (
                    field in create_field_names
                ), f"Create model for '{name}' missing user field '{field}'"

    def test_update_model_fields_are_optional(self, registry):
        """All fields in Update models must be optional (partial updates)."""
        for name in registry.get_schema_names():
            _, _, update_model = registry.get_model_for_version(name)
            for field_name, field_info in update_model.model_fields.items():
                assert (
                    not field_info.is_required()
                ), f"Update model for '{name}' has required field '{field_name}'"

    def test_document_model_has_audit_fields(self, registry):
        """Document models must have standard audit fields from BaseDocument."""
        required_audit = {"id", "entity_id", "record_version", "schema_version"}
        for name in registry.get_schema_names():
            doc_model, _, _ = registry.get_model_for_version(name)
            doc_fields = set(doc_model.model_fields.keys())
            missing = required_audit - doc_fields
            assert (
                not missing
            ), f"Document model for '{name}' missing audit fields: {missing}"

    def test_schema_required_fields_enforced_in_create(self, registry):
        """Fields marked 'required' in JSON Schema must be required in Create model."""
        audit_fields = {
            "id",
            "entity_id",
            "schema_version",
            "record_version",
            "created_at",
            "updated_at",
            "deleted_at",
            "created_by",
            "updated_by",
            "deleted_by",
        }
        for name in registry.get_schema_names():
            schema = registry.get_schema(name, "latest")
            required = set(schema.get("required", [])) - audit_fields
            _, create_model, _ = registry.get_model_for_version(name)

            for field in required:
                assert (
                    field in create_model.model_fields
                ), f"Required field '{field}' not in Create model for '{name}'"
                assert create_model.model_fields[
                    field
                ].is_required(), (
                    f"Required field '{field}' is optional in Create model for '{name}'"
                )


class TestSchemaToEndpointContract:
    """Generated endpoints match the schema definitions."""

    @pytest.fixture
    def app(self):
        def get_current_user():
            return {"id": "test-user"}

        return build_test_app(
            schema_dir=SAMPLE_SCHEMAS_DIR,
            get_current_user=get_current_user,
        )

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_every_schema_has_crud_endpoints(self, app):
        """Each schema must produce 5 CRUD endpoint routes."""
        registry = SchemaRegistry()
        for name in registry.get_schema_names():
            path_name = name.replace("_", "-")
            routes = [r.path for r in app.routes]
            prefix = f"/api/v1/{path_name}"

            assert f"{prefix}/" in routes, f"Missing list endpoint for '{name}'"
            assert (
                f"{prefix}/{{entity_id}}" in routes
            ), f"Missing detail endpoint for '{name}'"

    def test_create_endpoint_accepts_valid_payload(self, client):
        """POST endpoint must accept a payload matching the Create model."""
        response = client.post("/api/v1/widget/", json={"name": "test-widget"})
        assert response.status_code == 201
        data = response.json()
        assert "entity_id" in data
        assert data["name"] == "test-widget"

    def test_create_endpoint_rejects_missing_required(self, client):
        """POST endpoint must reject payloads missing required fields."""
        response = client.post("/api/v1/widget/", json={"color": "red"})
        assert response.status_code == 422

    def test_get_endpoint_returns_document_shape(self, client):
        """GET endpoint must return the full Document model shape."""
        create_resp = client.post("/api/v1/widget/", json={"name": "w1"})
        entity_id = create_resp.json()["entity_id"]

        get_resp = client.get(f"/api/v1/widget/{entity_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()

        # Must have audit fields
        assert "entity_id" in data
        assert "record_version" in data
        assert data["record_version"] == 1

    def test_update_endpoint_partial_update(self, client):
        """PATCH endpoint must accept partial updates."""
        create_resp = client.post(
            "/api/v1/widget/", json={"name": "orig", "color": "blue"}
        )
        entity_id = create_resp.json()["entity_id"]

        patch_resp = client.patch(f"/api/v1/widget/{entity_id}", json={"color": "red"})
        assert patch_resp.status_code == 200
        assert patch_resp.json()["color"] == "red"
        assert patch_resp.json()["name"] == "orig"  # unchanged field preserved

    def test_delete_endpoint_soft_deletes(self, client):
        """DELETE endpoint must soft-delete (GET returns 404 after)."""
        create_resp = client.post("/api/v1/widget/", json={"name": "to-delete"})
        entity_id = create_resp.json()["entity_id"]

        del_resp = client.delete(f"/api/v1/widget/{entity_id}")
        assert del_resp.status_code in (200, 204)

        get_resp = client.get(f"/api/v1/widget/{entity_id}")
        assert get_resp.status_code == 404

    def test_list_endpoint_returns_array(self, client):
        """GET list endpoint must return an array of documents."""
        client.post("/api/v1/widget/", json={"name": "w1"})
        client.post("/api/v1/widget/", json={"name": "w2"})

        resp = client.get("/api/v1/widget/")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2


class TestSchemaVersionContract:
    """Schema version changes must produce valid versioned models."""

    @pytest.fixture
    def registry(self, tmp_path):
        schemas = tmp_path / "schemas"
        schemas.mkdir()

        # v1: name only
        v1 = {
            "title": "Product",
            "version": "1.0.0",
            "type": "object",
            "required": ["name"],
            "properties": {
                "id": {"type": "string", "format": "uuid"},
                "entity_id": {"type": "string", "format": "uuid"},
                "schema_version": {"type": "string"},
                "record_version": {"type": "integer", "default": 1},
                "name": {"type": "string"},
            },
        }
        (schemas / "product.json").write_text(json.dumps(v1))

        reg = SchemaRegistry(schema_dir=schemas)

        # v2: name + price
        v2 = {**v1, "version": "2.0.0"}
        v2["properties"] = {
            **v1["properties"],
            "price": {"type": "number", "default": 0},
        }
        reg.register_schema("product", v2, version="2.0.0")

        return reg

    def test_both_versions_produce_models(self, registry):
        """Both v1 and v2 must produce valid model triples."""
        v1_models = registry.get_model_for_version("product", "1.0.0")
        v2_models = registry.get_model_for_version("product", "2.0.0")

        assert v1_models is not None
        assert v2_models is not None

    def test_v2_has_new_field(self, registry):
        """v2 model must include the new 'price' field."""
        _, create_v2, _ = registry.get_model_for_version("product", "2.0.0")
        assert "price" in create_v2.model_fields

    def test_v1_lacks_new_field(self, registry):
        """v1 model must NOT include the v2 'price' field."""
        _, create_v1, _ = registry.get_model_for_version("product", "1.0.0")
        assert "price" not in create_v1.model_fields

    def test_latest_resolves_to_highest_version(self, registry):
        """get_latest_version() must return the highest semver."""
        latest = registry.get_latest_version("product")
        assert latest == "2.0.0"


class TestPublicApiContract:
    """The __all__ exports must match the actual module contents."""

    def test_all_exports_are_importable(self):
        """Every name in __all__ must be importable from slip_stream."""
        import slip_stream

        for name in slip_stream.__all__:
            assert hasattr(
                slip_stream, name
            ), f"'{name}' is in __all__ but not importable from slip_stream"

    def test_all_exports_are_not_none(self):
        """Every exported name must resolve to a real object (not None)."""
        import slip_stream

        for name in slip_stream.__all__:
            obj = getattr(slip_stream, name)
            assert obj is not None, f"'{name}' exported as None from slip_stream"
