"""YAML configuration loader for slip-stream.

Reads a ``slip-stream.yml`` file and produces a ``SlipStreamConfig`` object
that can be passed to ``SlipStream()`` or ``SlipStream.from_config()``.

Example ``slip-stream.yml``::

    app:
      api_prefix: /api/v1
      structured_errors: true
      graphql:
        enabled: true
        prefix: /graphql

    databases:
      mongo:
        uri: mongodb://localhost:27017
        name: myapp_db
      sql:
        url: sqlite+aiosqlite:///app.db

    storage:
      default: mongo
      schemas:
        widget: sql
        order: sql

    filters:
      - type: rate_limit
        requests_per_window: 100
        window_seconds: 60
      - type: auth
      - type: envelope
      - type: projection

Usage::

    config = SlipStreamConfig.from_file(Path("slip-stream.yml"))
    slip = SlipStream(app=app, config=config)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class SlipStreamConfig:
    """Parsed and validated configuration from a slip-stream YAML file.

    Attributes:
        api_prefix: URL prefix for generated endpoints.
        structured_errors: Whether to install structured JSON error handlers.
        graphql_enabled: Whether to mount the GraphQL API.
        graphql_prefix: URL prefix for the GraphQL endpoint.
        mongo_uri: MongoDB connection URI.
        mongo_database: MongoDB database name.
        sql_url: SQLAlchemy async connection URL.
        storage_default: Default storage backend (``"mongo"`` or ``"sql"``).
        storage_map: Per-schema storage backend mapping.
        filters: List of filter configuration dicts.
        schema_dir: Path to the schemas directory.
        schema_vending: Whether to enable schema vending API.
        schema_vending_prefix: URL prefix for schema vending.
    """

    def __init__(
        self,
        *,
        api_prefix: str = "/api/v1",
        structured_errors: bool = False,
        graphql_enabled: bool = False,
        graphql_prefix: str = "/graphql",
        mongo_uri: Optional[str] = None,
        mongo_database: Optional[str] = None,
        sql_url: Optional[str] = None,
        storage_default: str = "mongo",
        storage_map: Optional[Dict[str, str]] = None,
        filters: Optional[List[Dict[str, Any]]] = None,
        schema_dir: Optional[str] = None,
        schema_vending: bool = False,
        schema_vending_prefix: str = "/schemas",
    ) -> None:
        self.api_prefix = api_prefix
        self.structured_errors = structured_errors
        self.graphql_enabled = graphql_enabled
        self.graphql_prefix = graphql_prefix
        self.mongo_uri = mongo_uri
        self.mongo_database = mongo_database
        self.sql_url = sql_url
        self.storage_default = storage_default
        self.storage_map: Dict[str, str] = dict(storage_map or {})
        self.filters: List[Dict[str, Any]] = list(filters or [])
        self.schema_dir = schema_dir
        self.schema_vending = schema_vending
        self.schema_vending_prefix = schema_vending_prefix

    @classmethod
    def from_file(cls, path: Path) -> SlipStreamConfig:
        """Load configuration from a YAML file.

        Args:
            path: Path to the YAML configuration file.

        Returns:
            A validated ``SlipStreamConfig`` instance.

        Raises:
            ImportError: If PyYAML is not installed.
            FileNotFoundError: If the config file does not exist.
            ValueError: If the YAML content is invalid.
        """
        if not HAS_YAML:
            raise ImportError(
                "PyYAML is required for YAML configuration. "
                "Install it with: pip install slip-stream[yaml]"
            )

        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")

        with open(path, "r") as f:
            data = yaml.safe_load(f)

        if data is None:
            data = {}

        if not isinstance(data, dict):
            raise ValueError(
                f"Expected YAML mapping at top level, got {type(data).__name__}"
            )

        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SlipStreamConfig:
        """Create a config from a parsed dictionary.

        Args:
            data: Dictionary matching the slip-stream.yml structure.

        Returns:
            A validated ``SlipStreamConfig`` instance.

        Raises:
            ValueError: If the data contains invalid values.
        """
        app = data.get("app", {})
        databases = data.get("databases", {})
        storage = data.get("storage", {})

        graphql_section = app.get("graphql", {})

        # Validate storage default
        storage_default = storage.get("default", "mongo")
        valid_backends = ("mongo", "sql")
        if storage_default not in valid_backends:
            raise ValueError(
                f"Invalid storage.default '{storage_default}'. "
                f"Must be one of: {valid_backends}"
            )

        # Validate per-schema storage mappings
        storage_schemas = storage.get("schemas", {})
        for schema_name, backend in storage_schemas.items():
            if backend not in valid_backends:
                raise ValueError(
                    f"Invalid storage backend '{backend}' for schema '{schema_name}'. "
                    f"Must be one of: {valid_backends}"
                )

        mongo = databases.get("mongo", {})
        sql = databases.get("sql", {})

        return cls(
            api_prefix=app.get("api_prefix", "/api/v1"),
            structured_errors=app.get("structured_errors", False),
            graphql_enabled=graphql_section.get("enabled", False),
            graphql_prefix=graphql_section.get("prefix", "/graphql"),
            mongo_uri=mongo.get("uri"),
            mongo_database=mongo.get("name"),
            sql_url=sql.get("url"),
            storage_default=storage_default,
            storage_map=storage_schemas,
            filters=data.get("filters", []),
            schema_dir=app.get("schema_dir"),
            schema_vending=app.get("schema_vending", False),
            schema_vending_prefix=app.get("schema_vending_prefix", "/schemas"),
        )
