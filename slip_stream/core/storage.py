"""Storage backend routing for multi-database support.

Maps schema names to storage backends (MongoDB or SQL) with a configurable
default.  Three layers can set the backend for a schema (highest precedence
wins):

    1. ``@registry.storage("widget", backend="sql")``  (decorator)
    2. ``SlipStream(storage_map={"widget": "sql"})``    (constructor)
    3. ``slip-stream.yml`` ``storage.schemas`` section   (config file)
    4. Default backend (``"mongo"`` unless overridden)

Usage::

    config = StorageConfig(default=StorageBackend.MONGO)
    config.set("widget", "sql")
    assert config.get("widget") == StorageBackend.SQL
    assert config.get("gadget") == StorageBackend.MONGO
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List


class StorageBackend(str, Enum):
    """Supported persistence backends."""

    MONGO = "mongo"
    SQL = "sql"


class StorageConfig:
    """Resolved storage routing -- maps schema names to backends.

    Args:
        default: The fallback backend for schemas without an explicit mapping.
        storage_map: Optional initial mapping of schema names to backends.
    """

    def __init__(
        self,
        default: StorageBackend = StorageBackend.MONGO,
        storage_map: Dict[str, StorageBackend] | None = None,
    ) -> None:
        self._default = default
        self._map: Dict[str, StorageBackend] = dict(storage_map or {})

    @property
    def default(self) -> StorageBackend:
        """The default backend for unmapped schemas."""
        return self._default

    def set(self, schema_name: str, backend: str | StorageBackend) -> None:
        """Assign a storage backend for *schema_name*.

        Args:
            schema_name: The schema to route.
            backend: ``"mongo"`` / ``"sql"`` or a ``StorageBackend`` enum value.

        Raises:
            ValueError: If *backend* is not a recognised value.
        """
        if isinstance(backend, str):
            try:
                backend = StorageBackend(backend)
            except ValueError:
                raise ValueError(
                    f"Unknown storage backend '{backend}'. "
                    f"Must be one of: {[b.value for b in StorageBackend]}"
                ) from None
        self._map[schema_name] = backend

    def get(self, schema_name: str) -> StorageBackend:
        """Return the backend for *schema_name*, falling back to the default."""
        return self._map.get(schema_name, self._default)

    def sql_schemas(self) -> List[str]:
        """Return schema names explicitly routed to SQL."""
        return [name for name, backend in self._map.items() if backend == StorageBackend.SQL]

    def mongo_schemas(self) -> List[str]:
        """Return schema names explicitly routed to MongoDB."""
        return [name for name, backend in self._map.items() if backend == StorageBackend.MONGO]

    def merge(self, other: StorageConfig) -> None:
        """Merge another config into this one (other's entries take precedence)."""
        for name, backend in other._map.items():
            self._map[name] = backend
