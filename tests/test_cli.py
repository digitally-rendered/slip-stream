"""Tests for the slip-stream CLI."""

import json
import os

import pytest

from slip_stream.cli import (
    _find_project_root,
    _snake_case,
    _title_case,
    build_parser,
    cmd_init,
    cmd_run,
    cmd_schema_add,
    cmd_schema_list,
    cmd_schema_test,
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


# ---------------------------------------------------------------------------
# slip run — subprocess-based dev server
# ---------------------------------------------------------------------------


class TestRunCommand:
    """Tests for cmd_run() using mocked subprocess.call."""

    def test_run_calls_uvicorn_with_defaults(self, tmp_path, monkeypatch):
        (tmp_path / "schemas").mkdir()
        monkeypatch.chdir(tmp_path)

        captured_cmd = {}

        def fake_call(cmd, cwd=None):
            captured_cmd["cmd"] = cmd
            captured_cmd["cwd"] = cwd
            return 0

        monkeypatch.setattr("slip_stream.cli.subprocess.call", fake_call)

        args = build_parser().parse_args(["run"])
        code = cmd_run(args)

        assert code == 0
        assert "uvicorn" in captured_cmd["cmd"]
        assert "main:app" in captured_cmd["cmd"]
        assert "127.0.0.1" in captured_cmd["cmd"]
        assert "8000" in captured_cmd["cmd"]
        assert "--reload" in captured_cmd["cmd"]

    def test_run_respects_custom_app_host_port(self, tmp_path, monkeypatch):
        (tmp_path / "schemas").mkdir()
        monkeypatch.chdir(tmp_path)

        captured_cmd = {}

        def fake_call(cmd, cwd=None):
            captured_cmd["cmd"] = cmd
            return 0

        monkeypatch.setattr("slip_stream.cli.subprocess.call", fake_call)

        args = build_parser().parse_args(
            ["run", "--app", "myapp:application", "--host", "0.0.0.0", "--port", "9000"]
        )
        code = cmd_run(args)

        assert code == 0
        assert "myapp:application" in captured_cmd["cmd"]
        assert "0.0.0.0" in captured_cmd["cmd"]
        assert "9000" in captured_cmd["cmd"]

    def test_run_keyboard_interrupt_returns_zero(self, tmp_path, monkeypatch):
        (tmp_path / "schemas").mkdir()
        monkeypatch.chdir(tmp_path)

        def fake_call(cmd, cwd=None):
            raise KeyboardInterrupt

        monkeypatch.setattr("slip_stream.cli.subprocess.call", fake_call)

        args = build_parser().parse_args(["run"])
        code = cmd_run(args)
        assert code == 0

    def test_run_passes_reload_dir_as_project_root(self, tmp_path, monkeypatch):
        (tmp_path / "schemas").mkdir()
        monkeypatch.chdir(tmp_path)

        captured_cmd = {}

        def fake_call(cmd, cwd=None):
            captured_cmd["cmd"] = cmd
            captured_cmd["cwd"] = cwd
            return 0

        monkeypatch.setattr("slip_stream.cli.subprocess.call", fake_call)

        args = build_parser().parse_args(["run"])
        cmd_run(args)

        idx = captured_cmd["cmd"].index("--reload-dir")
        assert captured_cmd["cmd"][idx + 1] == str(tmp_path)

    def test_main_dispatches_run(self, tmp_path, monkeypatch):
        (tmp_path / "schemas").mkdir()
        monkeypatch.chdir(tmp_path)

        def fake_call(cmd, cwd=None):
            return 0

        monkeypatch.setattr("slip_stream.cli.subprocess.call", fake_call)

        code = main(["run"])
        assert code == 0


# ---------------------------------------------------------------------------
# slip schema test — schemathesis integration
# ---------------------------------------------------------------------------


class TestSchemaTest:
    """Tests for cmd_schema_test() with mocked SchemaTestRunner."""

    def _setup_project(self, tmp_path, monkeypatch):
        """Create a minimal project layout and chdir into it."""
        schemas = tmp_path / "schemas"
        schemas.mkdir()
        monkeypatch.chdir(tmp_path)
        return tmp_path

    def test_fails_without_project(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        args = build_parser().parse_args(["schema", "test"])
        code = cmd_schema_test(args)
        assert code == 1

    def test_fails_when_schemathesis_not_installed(self, tmp_path, monkeypatch):
        self._setup_project(tmp_path, monkeypatch)

        # Simulate missing slip_stream.testing.runner import
        import sys

        monkeypatch.setitem(sys.modules, "slip_stream.testing.runner", None)

        args = build_parser().parse_args(["schema", "test"])
        code = cmd_schema_test(args)
        assert code == 1

    def test_lifecycle_mode_success(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        self._setup_project(tmp_path, monkeypatch)

        from slip_stream.testing.runner import TestResult

        mock_runner = MagicMock()
        mock_runner.run_lifecycle_tests.return_value = [
            TestResult(schema_name="widget", passed=True, steps_completed=6)
        ]
        mock_runner.print_results = MagicMock()

        MockRunnerClass = MagicMock(return_value=mock_runner)

        import sys

        mock_module = MagicMock()
        mock_module.SchemaTestRunner = MockRunnerClass
        monkeypatch.setitem(sys.modules, "slip_stream.testing.runner", mock_module)

        args = build_parser().parse_args(["schema", "test", "--mode", "lifecycle"])
        code = cmd_schema_test(args)

        assert code == 0
        mock_runner.run_lifecycle_tests.assert_called_once()

    def test_lifecycle_mode_with_failure(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        self._setup_project(tmp_path, monkeypatch)

        from slip_stream.testing.runner import TestResult

        mock_runner = MagicMock()
        mock_runner.run_lifecycle_tests.return_value = [
            TestResult(schema_name="widget", passed=False, error="500 on create")
        ]
        mock_runner.print_results = MagicMock()

        MockRunnerClass = MagicMock(return_value=mock_runner)

        import sys

        mock_module = MagicMock()
        mock_module.SchemaTestRunner = MockRunnerClass
        monkeypatch.setitem(sys.modules, "slip_stream.testing.runner", mock_module)

        args = build_parser().parse_args(["schema", "test", "--mode", "lifecycle"])
        code = cmd_schema_test(args)

        assert code == 1

    def test_fuzz_mode_delegates_to_schemathesis_cli(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        self._setup_project(tmp_path, monkeypatch)

        mock_runner = MagicMock()
        mock_runner.run_schemathesis_cli.return_value = 0

        MockRunnerClass = MagicMock(return_value=mock_runner)

        import sys

        mock_module = MagicMock()
        mock_module.SchemaTestRunner = MockRunnerClass
        monkeypatch.setitem(sys.modules, "slip_stream.testing.runner", mock_module)

        args = build_parser().parse_args(["schema", "test", "--mode", "fuzz"])
        code = cmd_schema_test(args)

        assert code == 0
        mock_runner.run_schemathesis_cli.assert_called_once()

    def test_all_mode_runs_lifecycle_then_fuzz(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        self._setup_project(tmp_path, monkeypatch)

        from slip_stream.testing.runner import TestResult

        mock_runner = MagicMock()
        mock_runner.run_lifecycle_tests.return_value = [
            TestResult(schema_name="widget", passed=True, steps_completed=6)
        ]
        mock_runner.print_results = MagicMock()
        mock_runner.run_schemathesis_cli.return_value = 0

        MockRunnerClass = MagicMock(return_value=mock_runner)

        import sys

        mock_module = MagicMock()
        mock_module.SchemaTestRunner = MockRunnerClass
        monkeypatch.setitem(sys.modules, "slip_stream.testing.runner", mock_module)

        args = build_parser().parse_args(["schema", "test", "--mode", "all"])
        code = cmd_schema_test(args)

        assert code == 0
        mock_runner.run_lifecycle_tests.assert_called_once()
        mock_runner.run_schemathesis_cli.assert_called_once()

    def test_all_mode_stops_after_lifecycle_failure(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        self._setup_project(tmp_path, monkeypatch)

        from slip_stream.testing.runner import TestResult

        mock_runner = MagicMock()
        mock_runner.run_lifecycle_tests.return_value = [
            TestResult(schema_name="widget", passed=False, error="create failed")
        ]
        mock_runner.print_results = MagicMock()

        MockRunnerClass = MagicMock(return_value=mock_runner)

        import sys

        mock_module = MagicMock()
        mock_module.SchemaTestRunner = MockRunnerClass
        monkeypatch.setitem(sys.modules, "slip_stream.testing.runner", mock_module)

        args = build_parser().parse_args(["schema", "test", "--mode", "all"])
        code = cmd_schema_test(args)

        # Exits with 1, does NOT call schemathesis
        assert code == 1
        mock_runner.run_schemathesis_cli.assert_not_called()

    def test_main_dispatches_schema_test(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        self._setup_project(tmp_path, monkeypatch)

        from slip_stream.testing.runner import TestResult

        mock_runner = MagicMock()
        mock_runner.run_lifecycle_tests.return_value = [
            TestResult(schema_name="widget", passed=True)
        ]
        mock_runner.print_results = MagicMock()

        MockRunnerClass = MagicMock(return_value=mock_runner)

        import sys

        mock_module = MagicMock()
        mock_module.SchemaTestRunner = MockRunnerClass
        monkeypatch.setitem(sys.modules, "slip_stream.testing.runner", mock_module)

        code = main(["schema", "test"])
        assert code == 0

    def test_schema_subcommand_no_command_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Calling "schema" with no subcommand triggers the help branch in main()
        # which calls parser.parse_args(["schema", "--help"]) -> SystemExit(0)
        with pytest.raises(SystemExit) as exc_info:
            main(["schema"])
        assert exc_info.value.code == 0
