"""Tests for SlipStreamConfig YAML configuration loader."""

from pathlib import Path

import pytest
import yaml

from slip_stream.config import SlipStreamConfig


class TestSlipStreamConfigFromDict:
    """Test SlipStreamConfig.from_dict() with various configurations."""

    def test_empty_dict_returns_defaults(self):
        config = SlipStreamConfig.from_dict({})
        assert config.api_prefix == "/api/v1"
        assert config.structured_errors is False
        assert config.graphql_enabled is False
        assert config.graphql_prefix == "/graphql"
        assert config.mongo_uri is None
        assert config.mongo_database is None
        assert config.sql_url is None
        assert config.storage_default == "mongo"
        assert config.storage_map == {}
        assert config.filters == []

    def test_full_config(self):
        data = {
            "app": {
                "api_prefix": "/api/v2",
                "structured_errors": True,
                "graphql": {
                    "enabled": True,
                    "prefix": "/gql",
                },
            },
            "databases": {
                "mongo": {
                    "uri": "mongodb://db:27017",
                    "name": "testdb",
                },
                "sql": {
                    "url": "sqlite+aiosqlite:///test.db",
                },
            },
            "storage": {
                "default": "mongo",
                "schemas": {
                    "widget": "sql",
                    "order": "sql",
                    "pet": "mongo",
                },
            },
            "filters": [
                {"type": "auth"},
                {"type": "envelope"},
            ],
        }
        config = SlipStreamConfig.from_dict(data)
        assert config.api_prefix == "/api/v2"
        assert config.structured_errors is True
        assert config.graphql_enabled is True
        assert config.graphql_prefix == "/gql"
        assert config.mongo_uri == "mongodb://db:27017"
        assert config.mongo_database == "testdb"
        assert config.sql_url == "sqlite+aiosqlite:///test.db"
        assert config.storage_default == "mongo"
        assert config.storage_map == {"widget": "sql", "order": "sql", "pet": "mongo"}
        assert len(config.filters) == 2

    def test_storage_default_sql(self):
        data = {"storage": {"default": "sql"}}
        config = SlipStreamConfig.from_dict(data)
        assert config.storage_default == "sql"

    def test_invalid_storage_default_raises(self):
        data = {"storage": {"default": "redis"}}
        with pytest.raises(ValueError, match="Invalid storage.default"):
            SlipStreamConfig.from_dict(data)

    def test_invalid_schema_backend_raises(self):
        data = {"storage": {"schemas": {"widget": "redis"}}}
        with pytest.raises(ValueError, match="Invalid storage backend"):
            SlipStreamConfig.from_dict(data)

    def test_partial_app_section(self):
        data = {"app": {"api_prefix": "/v3"}}
        config = SlipStreamConfig.from_dict(data)
        assert config.api_prefix == "/v3"
        assert config.graphql_enabled is False

    def test_partial_databases_section(self):
        data = {"databases": {"mongo": {"uri": "mongodb://host:12345"}}}
        config = SlipStreamConfig.from_dict(data)
        assert config.mongo_uri == "mongodb://host:12345"
        assert config.mongo_database is None
        assert config.sql_url is None

    def test_schema_dir_from_config(self):
        data = {"app": {"schema_dir": "./my-schemas"}}
        config = SlipStreamConfig.from_dict(data)
        assert config.schema_dir == "./my-schemas"

    def test_schema_vending_from_config(self):
        data = {"app": {"schema_vending": True, "schema_vending_prefix": "/s"}}
        config = SlipStreamConfig.from_dict(data)
        assert config.schema_vending is True
        assert config.schema_vending_prefix == "/s"


class TestSlipStreamConfigFromFile:
    """Test SlipStreamConfig.from_file() with real YAML files."""

    def test_load_yaml_file(self, tmp_path):
        config_data = {
            "app": {"api_prefix": "/api/v1"},
            "databases": {"mongo": {"uri": "mongodb://localhost:27017", "name": "test"}},
            "storage": {"default": "mongo"},
        }
        config_file = tmp_path / "slip-stream.yml"
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = SlipStreamConfig.from_file(config_file)
        assert config.api_prefix == "/api/v1"
        assert config.mongo_uri == "mongodb://localhost:27017"
        assert config.mongo_database == "test"

    def test_file_not_found_raises(self, tmp_path):
        missing = tmp_path / "nope.yml"
        with pytest.raises(FileNotFoundError, match="not found"):
            SlipStreamConfig.from_file(missing)

    def test_empty_yaml_returns_defaults(self, tmp_path):
        config_file = tmp_path / "empty.yml"
        config_file.write_text("")

        config = SlipStreamConfig.from_file(config_file)
        assert config.api_prefix == "/api/v1"
        assert config.storage_map == {}

    def test_invalid_yaml_structure_raises(self, tmp_path):
        config_file = tmp_path / "bad.yml"
        config_file.write_text("- just\n- a\n- list\n")

        with pytest.raises(ValueError, match="Expected YAML mapping"):
            SlipStreamConfig.from_file(config_file)

    def test_full_roundtrip(self, tmp_path):
        config_data = {
            "app": {
                "api_prefix": "/api/v2",
                "structured_errors": True,
                "graphql": {"enabled": True, "prefix": "/graphql"},
            },
            "databases": {
                "mongo": {"uri": "mongodb://host:27017", "name": "mydb"},
                "sql": {"url": "sqlite+aiosqlite:///app.db"},
            },
            "storage": {
                "default": "mongo",
                "schemas": {"widget": "sql", "order": "sql"},
            },
            "filters": [
                {"type": "rate_limit", "requests_per_window": 100, "window_seconds": 60},
                {"type": "auth"},
            ],
        }
        config_file = tmp_path / "slip-stream.yml"
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = SlipStreamConfig.from_file(config_file)
        assert config.api_prefix == "/api/v2"
        assert config.structured_errors is True
        assert config.graphql_enabled is True
        assert config.mongo_uri == "mongodb://host:27017"
        assert config.sql_url == "sqlite+aiosqlite:///app.db"
        assert config.storage_map == {"widget": "sql", "order": "sql"}
        assert len(config.filters) == 2


class TestSlipStreamConfigConstructor:
    """Test direct constructor usage."""

    def test_defaults(self):
        config = SlipStreamConfig()
        assert config.api_prefix == "/api/v1"
        assert config.storage_default == "mongo"
        assert config.storage_map == {}

    def test_custom_values(self):
        config = SlipStreamConfig(
            api_prefix="/v3",
            storage_default="sql",
            storage_map={"widget": "sql"},
        )
        assert config.api_prefix == "/v3"
        assert config.storage_default == "sql"
        assert config.storage_map == {"widget": "sql"}
