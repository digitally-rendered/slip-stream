"""Tests for slip_stream.schema_utils shared utilities."""

import json

import pytest

from slip_stream.schema_utils import (
    create_schema_file,
    snake_case,
    title_case,
    validate_all_schemas,
    validate_schema_file,
)


class TestSnakeCase:
    def test_simple(self):
        assert snake_case("Widget") == "widget"

    def test_camel_case(self):
        assert snake_case("UserProfile") == "user_profile"

    def test_kebab_case(self):
        assert snake_case("my-entity") == "my_entity"

    def test_already_snake(self):
        assert snake_case("hello_world") == "hello_world"


class TestTitleCase:
    def test_simple(self):
        assert title_case("widget") == "Widget"

    def test_multi_word(self):
        assert title_case("user_profile") == "UserProfile"


class TestCreateSchemaFile:
    def test_creates_valid_json(self, tmp_path):
        target = create_schema_file(tmp_path, "gadget")
        assert target.exists()
        data = json.loads(target.read_text())
        assert data["title"] == "Gadget"
        assert data["version"] == "1.0.0"
        assert data["type"] == "object"
        assert "properties" in data

    def test_custom_description(self, tmp_path):
        target = create_schema_file(tmp_path, "gadget", description="A cool gadget.")
        data = json.loads(target.read_text())
        assert data["description"] == "A cool gadget."

    def test_normalizes_name(self, tmp_path):
        target = create_schema_file(tmp_path, "UserProfile")
        assert target.name == "user_profile.json"

    def test_raises_if_exists(self, tmp_path):
        create_schema_file(tmp_path, "gadget")
        with pytest.raises(FileExistsError):
            create_schema_file(tmp_path, "gadget")


class TestValidateSchemaFile:
    def test_valid_schema(self, tmp_path):
        path = create_schema_file(tmp_path, "widget")
        issues = validate_schema_file(path)
        assert issues == []

    def test_missing_title(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"version": "1.0.0", "type": "object", "properties": {}}))
        issues = validate_schema_file(path)
        assert "missing 'title'" in issues

    def test_invalid_json(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json")
        issues = validate_schema_file(path)
        assert len(issues) == 1
        assert "invalid JSON" in issues[0]


class TestValidateAllSchemas:
    def test_all_valid(self, tmp_path):
        create_schema_file(tmp_path, "widget")
        create_schema_file(tmp_path, "gadget")
        results = validate_all_schemas(tmp_path)
        assert len(results) == 2
        assert all(len(issues) == 0 for issues in results.values())

    def test_mixed_results(self, tmp_path):
        create_schema_file(tmp_path, "good")
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"type": "string"}))
        results = validate_all_schemas(tmp_path)
        assert len(results) == 2
        # good.json should be valid
        assert results["good.json"] == []
        # bad.json should have issues
        assert len(results["bad.json"]) > 0
