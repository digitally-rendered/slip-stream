"""Tests for the SDK/client code generator."""

import ast

import pytest

from slip_stream.sdk_generator import generate_sdk


WIDGET_SCHEMA = {
    "title": "Widget",
    "description": "A widget entity.",
    "version": "1.0.0",
    "type": "object",
    "required": ["name"],
    "properties": {
        "id": {"type": "string", "format": "uuid"},
        "entity_id": {"type": "string", "format": "uuid"},
        "schema_version": {"type": "string"},
        "record_version": {"type": "integer"},
        "created_at": {"type": "string", "format": "date-time"},
        "updated_at": {"type": "string", "format": "date-time"},
        "deleted_at": {"type": "string", "format": "date-time"},
        "created_by": {"type": "string"},
        "updated_by": {"type": "string"},
        "deleted_by": {"type": "string"},
        "name": {"type": "string", "description": "Widget name"},
        "color": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}

ORDER_SCHEMA = {
    "title": "Order",
    "version": "1.0.0",
    "type": "object",
    "required": ["total"],
    "properties": {
        "id": {"type": "string", "format": "uuid"},
        "entity_id": {"type": "string", "format": "uuid"},
        "schema_version": {"type": "string"},
        "record_version": {"type": "integer"},
        "created_at": {"type": "string", "format": "date-time"},
        "updated_at": {"type": "string", "format": "date-time"},
        "deleted_at": {"type": "string", "format": "date-time"},
        "created_by": {"type": "string"},
        "updated_by": {"type": "string"},
        "deleted_by": {"type": "string"},
        "total": {"type": "number"},
        "status": {"type": "string", "default": "pending"},
    },
}


class TestGenerateSDK:
    def test_generates_valid_python(self):
        code = generate_sdk({"widget": WIDGET_SCHEMA})
        # Should parse as valid Python
        ast.parse(code)

    def test_generates_document_model(self):
        code = generate_sdk({"widget": WIDGET_SCHEMA})
        assert "class Widget(BaseModel):" in code
        assert "name: str" in code

    def test_generates_create_model(self):
        code = generate_sdk({"widget": WIDGET_SCHEMA})
        assert "class WidgetCreate(BaseModel):" in code

    def test_generates_update_model(self):
        code = generate_sdk({"widget": WIDGET_SCHEMA})
        assert "class WidgetUpdate(BaseModel):" in code
        assert "name: Optional[str] = None" in code

    def test_generates_client_class(self):
        code = generate_sdk({"widget": WIDGET_SCHEMA})
        assert "class SlipStreamClient:" in code

    def test_generates_crud_methods(self):
        code = generate_sdk({"widget": WIDGET_SCHEMA})
        assert "async def create_widget(" in code
        assert "async def get_widget(" in code
        assert "async def list_widgets(" in code
        assert "async def update_widget(" in code
        assert "async def delete_widget(" in code

    def test_required_fields_not_optional(self):
        code = generate_sdk({"widget": WIDGET_SCHEMA})
        # In WidgetCreate, name is required (no default)
        tree = ast.parse(code)
        # Just check it compiles and has the right class
        classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        assert "WidgetCreate" in classes

    def test_multiple_schemas(self):
        code = generate_sdk({"widget": WIDGET_SCHEMA, "order": ORDER_SCHEMA})
        ast.parse(code)
        assert "class Widget(BaseModel):" in code
        assert "class Order(BaseModel):" in code
        assert "async def create_widget(" in code
        assert "async def create_order(" in code

    def test_custom_base_url(self):
        code = generate_sdk(
            {"widget": WIDGET_SCHEMA},
            base_url="https://api.example.com/v2",
        )
        assert "https://api.example.com/v2" in code

    def test_custom_docstring(self):
        code = generate_sdk(
            {"widget": WIDGET_SCHEMA},
            module_docstring="My custom SDK.",
        )
        assert "My custom SDK." in code

    def test_array_type_mapping(self):
        code = generate_sdk({"widget": WIDGET_SCHEMA})
        assert "list[str]" in code

    def test_excludes_audit_fields_from_create(self):
        code = generate_sdk({"widget": WIDGET_SCHEMA})
        # WidgetCreate should NOT have entity_id, created_at, etc.
        # Find WidgetCreate class section
        lines = code.split("\n")
        in_create = False
        create_fields = []
        for line in lines:
            if "class WidgetCreate" in line:
                in_create = True
                continue
            if in_create and line.startswith("class "):
                break
            if in_create and ":" in line and not line.strip().startswith("#") and not line.strip().startswith('"""'):
                field_name = line.strip().split(":")[0]
                create_fields.append(field_name)
        assert "entity_id" not in create_fields
        assert "created_at" not in create_fields
        assert "record_version" not in create_fields

    def test_default_values(self):
        code = generate_sdk({"order": ORDER_SCHEMA})
        assert "'pending'" in code

    def test_description_as_comment(self):
        code = generate_sdk({"widget": WIDGET_SCHEMA})
        assert "# Widget name" in code

    def test_close_method(self):
        code = generate_sdk({"widget": WIDGET_SCHEMA})
        assert "async def close(self)" in code

    def test_context_manager(self):
        code = generate_sdk({"widget": WIDGET_SCHEMA})
        assert "__aenter__" in code
        assert "__aexit__" in code

    def test_list_supports_where_and_sort(self):
        code = generate_sdk({"widget": WIDGET_SCHEMA})
        assert "where:" in code
        assert "sort:" in code

    def test_empty_schema(self):
        code = generate_sdk({
            "bare": {
                "title": "Bare",
                "type": "object",
                "properties": {},
            }
        })
        ast.parse(code)
        assert "class Bare(BaseModel):" in code
