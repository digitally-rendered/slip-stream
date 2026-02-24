"""Tests for the slip-stream CLI."""

import json
import os

from slip_stream.cli import (
    _find_project_root,
    _snake_case,
    _title_case,
    build_parser,
    cmd_init,
    cmd_run,
    cmd_schema_add,
    cmd_schema_list,
    cmd_schema_validate,
    main,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestSnakeCase:
    def test_simple(self):
        assert _snake_case("widget") == "widget"

    def test_camel_case(self):
        assert _snake_case("WidgetType") == "widget_type"

    def test_hyphens(self):
        assert _snake_case("user-profile") == "user_profile"

    def test_mixed(self):
        assert _snake_case("MyHTTPClient") == "my_http_client"


class TestTitleCase:
    def test_simple(self):
        assert _title_case("widget") == "Widget"

    def test_multi_word(self):
        assert _title_case("widget_type") == "WidgetType"


class TestFindProjectRoot:
    def test_finds_root(self, tmp_path):
        (tmp_path / "schemas").mkdir()
        os.chdir(tmp_path)
        assert _find_project_root(tmp_path) == tmp_path

    def test_finds_root_from_subdirectory(self, tmp_path):
        (tmp_path / "schemas").mkdir()
        sub = tmp_path / "src" / "deep"
        sub.mkdir(parents=True)
        assert _find_project_root(sub) == tmp_path

    def test_returns_none_when_not_found(self, tmp_path):
        assert _find_project_root(tmp_path / "nonexistent") is None


# ---------------------------------------------------------------------------
# slip init
# ---------------------------------------------------------------------------


class TestInit:
    def test_creates_project(self, tmp_path):
        project_dir = tmp_path / "myproject"
        args = build_parser().parse_args(["init", str(project_dir)])
        code = cmd_init(args)

        assert code == 0
        assert (project_dir / "schemas").is_dir()
        assert (project_dir / "main.py").is_file()
        assert (project_dir / ".env").is_file()
        assert (project_dir / "schemas" / "item.json").is_file()

    def test_main_py_content(self, tmp_path):
        project_dir = tmp_path / "my_api"
        args = build_parser().parse_args(["init", str(project_dir)])
        cmd_init(args)

        content = (project_dir / "main.py").read_text()
        assert "from slip_stream import SlipStream" in content
        assert "MyApi" in content  # title case of my_api

    def test_env_file_content(self, tmp_path):
        project_dir = tmp_path / "cool_project"
        args = build_parser().parse_args(["init", str(project_dir)])
        cmd_init(args)

        content = (project_dir / ".env").read_text()
        assert "cool_project_db" in content

    def test_sample_schema_valid(self, tmp_path):
        project_dir = tmp_path / "proj"
        args = build_parser().parse_args(["init", str(project_dir)])
        cmd_init(args)

        schema = json.loads((project_dir / "schemas" / "item.json").read_text())
        assert schema["title"] == "Item"
        assert schema["version"] == "1.0.0"
        assert schema["type"] == "object"
        assert "properties" in schema

    def test_creates_claude_md(self, tmp_path):
        project_dir = tmp_path / "my_api"
        args = build_parser().parse_args(["init", str(project_dir)])
        cmd_init(args)

        claude_md = project_dir / "CLAUDE.md"
        assert claude_md.is_file()
        content = claude_md.read_text()
        assert "slip-stream" in content
        assert "MyApi" in content  # title case of my_api
        assert "my_api" in content  # project name in structure
        assert "slip schema add" in content
        assert "/api/v1/" in content

    def test_fails_if_not_empty(self, tmp_path):
        project_dir = tmp_path / "existing"
        project_dir.mkdir()
        (project_dir / "file.txt").write_text("exists")

        args = build_parser().parse_args(["init", str(project_dir)])
        code = cmd_init(args)
        assert code == 1


# ---------------------------------------------------------------------------
# slip schema add
# ---------------------------------------------------------------------------


class TestSchemaAdd:
    def test_adds_schema(self, tmp_path, monkeypatch):
        (tmp_path / "schemas").mkdir()
        monkeypatch.chdir(tmp_path)

        args = build_parser().parse_args(["schema", "add", "widget"])
        code = cmd_schema_add(args)

        assert code == 0
        schema_file = tmp_path / "schemas" / "widget.json"
        assert schema_file.exists()

        schema = json.loads(schema_file.read_text())
        assert schema["title"] == "Widget"
        assert schema["version"] == "1.0.0"

    def test_adds_camel_case_name(self, tmp_path, monkeypatch):
        (tmp_path / "schemas").mkdir()
        monkeypatch.chdir(tmp_path)

        args = build_parser().parse_args(["schema", "add", "UserProfile"])
        code = cmd_schema_add(args)

        assert code == 0
        assert (tmp_path / "schemas" / "user_profile.json").exists()

    def test_custom_description(self, tmp_path, monkeypatch):
        (tmp_path / "schemas").mkdir()
        monkeypatch.chdir(tmp_path)

        args = build_parser().parse_args(
            ["schema", "add", "order", "-d", "A purchase order."]
        )
        cmd_schema_add(args)

        schema = json.loads((tmp_path / "schemas" / "order.json").read_text())
        assert schema["description"] == "A purchase order."

    def test_fails_if_exists(self, tmp_path, monkeypatch):
        (tmp_path / "schemas").mkdir()
        (tmp_path / "schemas" / "widget.json").write_text("{}")
        monkeypatch.chdir(tmp_path)

        args = build_parser().parse_args(["schema", "add", "widget"])
        code = cmd_schema_add(args)
        assert code == 1

    def test_fails_without_project(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        args = build_parser().parse_args(["schema", "add", "widget"])
        code = cmd_schema_add(args)
        assert code == 1


# ---------------------------------------------------------------------------
# slip schema list
# ---------------------------------------------------------------------------


class TestSchemaList:
    def test_lists_schemas(self, tmp_path, monkeypatch, capsys):
        schemas = tmp_path / "schemas"
        schemas.mkdir()
        (schemas / "pet.json").write_text(
            json.dumps({"title": "Pet", "version": "1.0.0"})
        )
        (schemas / "order.json").write_text(
            json.dumps({"title": "Order", "version": "2.0.0"})
        )
        monkeypatch.chdir(tmp_path)

        args = build_parser().parse_args(["schema", "list"])
        code = cmd_schema_list(args)

        assert code == 0
        output = capsys.readouterr().out
        assert "order" in output
        assert "pet" in output
        assert "1.0.0" in output
        assert "2.0.0" in output

    def test_empty_schemas(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "schemas").mkdir()
        monkeypatch.chdir(tmp_path)

        args = build_parser().parse_args(["schema", "list"])
        code = cmd_schema_list(args)

        assert code == 0
        assert "No schemas found" in capsys.readouterr().out

    def test_handles_invalid_json(self, tmp_path, monkeypatch, capsys):
        schemas = tmp_path / "schemas"
        schemas.mkdir()
        (schemas / "bad.json").write_text("not json")
        monkeypatch.chdir(tmp_path)

        args = build_parser().parse_args(["schema", "list"])
        cmd_schema_list(args)

        output = capsys.readouterr().out
        assert "invalid JSON" in output


# ---------------------------------------------------------------------------
# slip schema validate
# ---------------------------------------------------------------------------


class TestSchemaValidate:
    def test_valid_schema(self, tmp_path, monkeypatch, capsys):
        schemas = tmp_path / "schemas"
        schemas.mkdir()
        (schemas / "good.json").write_text(
            json.dumps(
                {
                    "title": "Good",
                    "version": "1.0.0",
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                }
            )
        )
        monkeypatch.chdir(tmp_path)

        args = build_parser().parse_args(["schema", "validate"])
        code = cmd_schema_validate(args)

        assert code == 0
        output = capsys.readouterr().out
        assert "OK" in output

    def test_invalid_schema_missing_title(self, tmp_path, monkeypatch, capsys):
        schemas = tmp_path / "schemas"
        schemas.mkdir()
        (schemas / "bad.json").write_text(
            json.dumps({"version": "1.0.0", "type": "object", "properties": {}})
        )
        monkeypatch.chdir(tmp_path)

        args = build_parser().parse_args(["schema", "validate"])
        code = cmd_schema_validate(args)

        assert code == 1
        output = capsys.readouterr().out
        assert "FAIL" in output
        assert "missing 'title'" in output

    def test_invalid_json(self, tmp_path, monkeypatch, capsys):
        schemas = tmp_path / "schemas"
        schemas.mkdir()
        (schemas / "broken.json").write_text("{{{")
        monkeypatch.chdir(tmp_path)

        args = build_parser().parse_args(["schema", "validate"])
        code = cmd_schema_validate(args)

        assert code == 1
        assert "FAIL" in capsys.readouterr().out

    def test_multiple_issues(self, tmp_path, monkeypatch, capsys):
        schemas = tmp_path / "schemas"
        schemas.mkdir()
        (schemas / "bad.json").write_text(json.dumps({"type": "array"}))
        monkeypatch.chdir(tmp_path)

        args = build_parser().parse_args(["schema", "validate"])
        code = cmd_schema_validate(args)

        assert code == 1
        output = capsys.readouterr().out
        assert "missing 'title'" in output
        assert "missing 'version'" in output


# ---------------------------------------------------------------------------
# slip run
# ---------------------------------------------------------------------------


class TestRun:
    def test_fails_without_project(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        args = build_parser().parse_args(["run"])
        code = cmd_run(args)
        assert code == 1


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------


class TestMain:
    def test_no_args_shows_help(self, capsys):
        code = main([])
        assert code == 0

    def test_init(self, tmp_path):
        code = main(["init", str(tmp_path / "newproj")])
        assert code == 0
        assert (tmp_path / "newproj" / "schemas").is_dir()

    def test_schema_add(self, tmp_path, monkeypatch):
        (tmp_path / "schemas").mkdir()
        monkeypatch.chdir(tmp_path)
        code = main(["schema", "add", "gadget"])
        assert code == 0
        assert (tmp_path / "schemas" / "gadget.json").exists()

    def test_schema_list(self, tmp_path, monkeypatch, capsys):
        schemas = tmp_path / "schemas"
        schemas.mkdir()
        (schemas / "x.json").write_text(json.dumps({"title": "X", "version": "1.0.0"}))
        monkeypatch.chdir(tmp_path)
        code = main(["schema", "list"])
        assert code == 0

    def test_schema_validate(self, tmp_path, monkeypatch, capsys):
        schemas = tmp_path / "schemas"
        schemas.mkdir()
        (schemas / "ok.json").write_text(
            json.dumps(
                {
                    "title": "Ok",
                    "version": "1.0.0",
                    "type": "object",
                    "properties": {},
                }
            )
        )
        monkeypatch.chdir(tmp_path)
        code = main(["schema", "validate"])
        assert code == 0
