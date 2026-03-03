"""Schema Registry for managing JSON schemas and generating Pydantic models.

Supports multiple schema versions with semver comparison and caches
generated Pydantic models to avoid repeated ``create_model()`` calls.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar, cast
from uuid import UUID

from pydantic import BaseModel, create_model

from slip_stream.core.domain.base import BaseDocument
from slip_stream.core.schema.ref_resolver import RefResolver
from slip_stream.core.schema.versioning import latest_version, sort_versions

logger = logging.getLogger(__name__)

DocumentModelType = TypeVar("DocumentModelType", bound=BaseDocument)
CreateModelType = TypeVar("CreateModelType", bound=BaseModel)
UpdateModelType = TypeVar("UpdateModelType", bound=BaseModel)

# Fields managed by BaseDocument — skipped during model generation
_AUDIT_FIELDS = frozenset(
    {
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
)


class SchemaRegistry:
    """Registry for JSON schemas with versioning support.

    Provides methods to retrieve schemas and generate Pydantic models
    (Document, Create, Update) from JSON Schema definitions.

    Usage::

        registry = SchemaRegistry(schema_dir=Path("./schemas"))
        doc_model = registry.generate_document_model("widget")
        create_model = registry.generate_create_model("widget")
        update_model = registry.generate_update_model("widget")
    """

    _instance: Optional["SchemaRegistry"] = None
    _schemas: Dict[str, Dict[str, Any]] = {}
    _schema_dir: Optional[Path] = None
    _model_cache: Dict[tuple, tuple] = {}

    def __new__(cls, schema_dir: Optional[Path] = None) -> "SchemaRegistry":
        if cls._instance is None:
            cls._instance = super(SchemaRegistry, cls).__new__(cls)
            cls._instance._schemas = {}
            cls._instance._model_cache = {}
            if schema_dir is not None:
                cls._instance._schema_dir = schema_dir
                cls._instance._load_schemas()
        elif schema_dir is not None and schema_dir != cls._instance._schema_dir:
            # Allow re-initialization with a different schema directory
            cls._instance._schema_dir = schema_dir
            cls._instance._schemas = {}
            cls._instance._model_cache = {}
            cls._instance._load_schemas()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance. Useful for testing."""
        cls._instance = None
        cls._schemas = {}
        cls._schema_dir = None
        cls._model_cache = {}

    def _load_schemas(self) -> None:
        """Load all schemas from the schema directory.

        Automatically resolves ``$ref`` pointers in each schema using
        :class:`RefResolver` with the schema directory as base path.
        """
        if self._schema_dir is None:
            return

        if not self._schema_dir.exists():
            os.makedirs(self._schema_dir, exist_ok=True)

        resolver = RefResolver(base_path=self._schema_dir)

        for schema_file in self._schema_dir.glob("*.json"):
            try:
                with open(schema_file, "r", encoding="utf-8") as f:
                    schema = json.load(f)
                    name = schema_file.stem
                    version = schema.get("version", "1.0.0")

                    # Resolve $ref pointers before storing
                    try:
                        schema = resolver.resolve(schema)
                    except ValueError as ref_err:
                        logger.warning(
                            "Could not resolve $ref in %s: %s",
                            schema_file,
                            ref_err,
                        )

                    if name not in self._schemas:
                        self._schemas[name] = {}

                    self._schemas[name][version] = schema
            except Exception as e:
                logger.error("Error loading schema %s: %s", schema_file, e)

    def get_schema_names(self) -> List[str]:
        """Return list of all registered schema names."""
        return list(self._schemas.keys())

    def get_schema(self, name: str, version: str = "latest") -> Dict[str, Any]:
        """Get a schema by name and version.

        Args:
            name: Schema name.
            version: Schema version or ``"latest"`` for the latest version.

        Returns:
            The schema as a dictionary.

        Raises:
            ValueError: If the schema or version is not found.
        """
        if name not in self._schemas:
            raise ValueError(f"Schema {name} not found")

        if version == "latest":
            versions = list(self._schemas[name].keys())
            if not versions:
                raise ValueError(f"No versions found for schema {name}")
            version = latest_version(versions)

        if version not in self._schemas[name]:
            raise ValueError(f"Version {version} not found for schema {name}")

        return cast(Dict[str, Any], self._schemas[name][version])

    def register_schema(
        self, name: str, schema: Dict[str, Any], version: str = "1.0.0"
    ) -> None:
        """Register a new schema or update an existing one.

        Args:
            name: Schema name.
            schema: Schema definition.
            version: Schema version.
        """
        if name not in self._schemas:
            self._schemas[name] = {}

        self._schemas[name][version] = schema

        # Save to disk if schema_dir is configured
        if self._schema_dir is not None:
            schema_path = self._schema_dir / f"{name}.json"
            schema_to_save = {**schema, "version": version}
            with open(schema_path, "w", encoding="utf-8") as f:
                json.dump(schema_to_save, f, indent=2)

    async def sync_from_storage(self, storage: Any) -> None:
        """Load all schema versions from a storage backend into memory.

        This is the integration point for :class:`SchemaStoragePort`
        adapters.  Call once during application startup (inside the
        lifespan).  After syncing, all sync methods (``get_schema``,
        ``generate_*_model``, etc.) work from the in-memory cache.

        Also persists any file-loaded schemas that are not yet in the
        storage backend (bidirectional sync).

        Args:
            storage: Any object implementing the ``SchemaStoragePort`` protocol.
        """
        # Pull all schemas from storage into memory
        names = await storage.list_names()
        for name in names:
            versions = await storage.list_versions(name)
            for version in versions:
                schema = await storage.load(name, version)
                if schema is not None:
                    if name not in self._schemas:
                        self._schemas[name] = {}
                    self._schemas[name][version] = schema

        # Push any file-loaded schemas that aren't in storage yet
        for name, version_dict in self._schemas.items():
            for version, schema in version_dict.items():
                if not await storage.exists(name, version):
                    await storage.save(name, version, schema)

        logger.info(
            "Schema registry synced: %d schemas, %d total versions",
            len(self._schemas),
            sum(len(v) for v in self._schemas.values()),
        )

    def get_all_versions(self, name: str) -> List[str]:
        """Return all version strings for a schema, sorted by semver ascending.

        Args:
            name: Schema name.

        Returns:
            Sorted list of version strings.

        Raises:
            ValueError: If the schema is not found.
        """
        if name not in self._schemas:
            raise ValueError(f"Schema {name} not found")
        return sort_versions(list(self._schemas[name].keys()))

    def get_latest_version(self, name: str) -> str:
        """Return the latest version string for a schema by semver.

        Args:
            name: Schema name.

        Returns:
            The latest version string.

        Raises:
            ValueError: If the schema is not found or has no versions.
        """
        versions = self.get_all_versions(name)
        if not versions:
            raise ValueError(f"No versions found for schema {name}")
        return versions[-1]

    def get_model_for_version(
        self, name: str, version: str = "latest"
    ) -> Tuple[Type[BaseDocument], Type[BaseModel], Type[BaseModel]]:
        """Return the (Document, Create, Update) model triple for a schema version.

        Results are cached — repeated calls with the same ``(name, version)``
        return the same model classes.

        Args:
            name: Schema name.
            version: Schema version or ``"latest"``.

        Returns:
            Tuple of ``(DocumentModel, CreateModel, UpdateModel)``.
        """
        # Resolve "latest" to a concrete version for cache key stability
        if version == "latest":
            version = self.get_latest_version(name)

        cache_key = (name, version)
        if cache_key in self._model_cache:
            return self._model_cache[cache_key]

        doc_model = self.generate_document_model(name, version)
        create_model_ = self.generate_create_model(name, version)
        update_model_ = self.generate_update_model(name, version)

        result = (doc_model, create_model_, update_model_)
        self._model_cache[cache_key] = result
        return result

    def generate_document_model(
        self, name: str, version: str = "latest"
    ) -> Type[BaseDocument]:
        """Generate a Pydantic model from a schema that extends BaseDocument.

        Args:
            name: Schema name.
            version: Schema version or ``"latest"``.

        Returns:
            A Pydantic model class extending BaseDocument.
        """
        schema = self.get_schema(name, version)
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        fields: Dict[str, Any] = {}
        for field_name, field_def in properties.items():
            if field_name in _AUDIT_FIELDS:
                continue

            python_type = self._json_type_to_python(field_def)

            if field_name in required:
                default = field_def.get("default", ...)
            else:
                default = field_def.get("default", None)
                python_type = Optional[python_type]  # type: ignore[assignment]

            fields[field_name] = (python_type, default)

        model_name = f"{name.title()}Document"
        model = cast(
            Type[BaseDocument],
            create_model(model_name, __base__=BaseDocument, **fields),
        )

        setattr(model, "__schema_version__", schema.get("version", "1.0.0"))

        return model

    def generate_create_model(
        self, name: str, version: str = "latest"
    ) -> Type[BaseModel]:
        """Generate a Pydantic model for creating documents.

        Args:
            name: Schema name.
            version: Schema version or ``"latest"``.

        Returns:
            A Pydantic model class for document creation.
        """
        schema = self.get_schema(name, version)
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        fields: Dict[str, Any] = {}
        for field_name, field_def in properties.items():
            if field_name in _AUDIT_FIELDS:
                continue

            python_type = self._json_type_to_python(field_def)

            if field_name in required:
                default = ...
            else:
                default = field_def.get("default", None)
                python_type = Optional[python_type]  # type: ignore[assignment]

            fields[field_name] = (python_type, default)

        model_name = f"{name.title()}Create"
        model = cast(Type[BaseModel], create_model(model_name, **fields))

        return model

    def generate_update_model(
        self, name: str, version: str = "latest"
    ) -> Type[BaseModel]:
        """Generate a Pydantic model for updating documents.

        All fields are optional in the update model.

        Args:
            name: Schema name.
            version: Schema version or ``"latest"``.

        Returns:
            A Pydantic model class for document updates.
        """
        schema = self.get_schema(name, version)
        properties = schema.get("properties", {})

        fields: Dict[str, Any] = {}
        for field_name, field_def in properties.items():
            if field_name in _AUDIT_FIELDS:
                continue

            python_type = self._json_type_to_python(field_def)
            optional_type: Any = Optional[python_type]  # type: ignore[misc]
            fields[field_name] = (optional_type, None)

        model_name = f"{name.title()}Update"
        model = cast(Type[BaseModel], create_model(model_name, **fields))

        model.model_config = {  # type: ignore[assignment]
            "populate_by_name": True,
            "from_attributes": True,
            "extra": "ignore",
        }

        return model

    def _json_type_to_python(self, field_def: Dict[str, Any]) -> Any:
        """Convert JSON schema type to Python type."""
        json_type = field_def.get("type")
        format_type = field_def.get("format")

        type_map = {
            "integer": int,
            "number": float,
            "boolean": bool,
            "object": Dict[str, Any],
        }
        if json_type in type_map:
            return type_map[json_type]
        if json_type == "string":
            string_format_map = {"date-time": datetime, "uuid": UUID}
            return string_format_map.get(format_type, str)  # type: ignore[arg-type]
        if json_type == "array":
            items = field_def.get("items", {})
            item_type = self._json_type_to_python(items)
            return List[item_type]  # type: ignore[valid-type]
        return Any
