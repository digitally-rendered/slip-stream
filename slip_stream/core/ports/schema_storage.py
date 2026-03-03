"""Schema storage port — hexagonal interface for schema persistence backends.

Implementations may store schemas on the filesystem, in MongoDB, in a relational
database, or in a remote HTTP registry.  All methods are async so that network-
backed adapters work naturally.

See Also:
    - :class:`~slip_stream.adapters.persistence.schema.file_storage.FileSchemaStorage`
    - :class:`~slip_stream.adapters.persistence.schema.mongo_storage.MongoSchemaStorage`
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SchemaStoragePort(Protocol):
    """Protocol defining the contract for schema persistence backends.

    Any class implementing these six methods satisfies the port, whether
    it wraps local files, MongoDB, an RDBMS, or an HTTP registry.
    """

    async def save(self, name: str, version: str, schema: dict[str, Any]) -> None:
        """Persist a schema version.

        If the ``(name, version)`` pair already exists, it should be
        overwritten (upsert semantics).
        """

    async def load(self, name: str, version: str) -> dict[str, Any] | None:
        """Load a specific schema version.

        Returns:
            The schema dict, or ``None`` if not found.
        """

    async def load_latest(self, name: str) -> tuple[str, dict[str, Any]] | None:
        """Load the latest version of a schema by semver.

        Returns:
            A ``(version, schema)`` tuple, or ``None`` if no versions exist.
        """

    async def list_versions(self, name: str) -> list[str]:
        """Return all version strings for a schema, sorted by semver ascending."""

    async def list_names(self) -> list[str]:
        """Return all known schema names."""

    async def exists(self, name: str, version: str) -> bool:
        """Check whether a specific ``(name, version)`` pair exists."""
