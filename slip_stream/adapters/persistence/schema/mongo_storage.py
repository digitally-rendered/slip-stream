"""MongoDB schema storage adapter.

Stores schema versions in a ``_schema_registry`` collection with one
document per ``(name, version)`` pair.  Decomposed version fields
(``version_major``, ``version_minor``, ``version_patch``) enable
MongoDB-native sorting without client-side semver parsing.

Collection schema::

    {
        "name": "widget",
        "version": "2.1.0",
        "version_major": 2,
        "version_minor": 1,
        "version_patch": 0,
        "schema": { ... },
        "created_at": ISODate,
        "checksum": "sha256:..."
    }
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from slip_stream.core.schema.versioning import parse_semver, sort_versions

logger = logging.getLogger(__name__)


class MongoSchemaStorage:
    """SchemaStoragePort adapter backed by MongoDB.

    Args:
        db: An ``AsyncIOMotorDatabase`` instance.
        collection_name: Name of the collection to store schemas in.
    """

    def __init__(self, db: Any, collection_name: str = "_schema_registry") -> None:
        self._db = db
        self._collection = db[collection_name]

    async def ensure_indexes(self) -> None:
        """Create indexes for efficient querying.

        Call once during application startup (idempotent).
        """
        await self._collection.create_index(
            [("name", 1), ("version", 1)],
            unique=True,
            name="name_version_unique",
        )
        await self._collection.create_index(
            [
                ("name", 1),
                ("version_major", -1),
                ("version_minor", -1),
                ("version_patch", -1),
            ],
            name="name_version_sort",
        )

    # ------------------------------------------------------------------
    # SchemaStoragePort implementation
    # ------------------------------------------------------------------

    async def save(self, name: str, version: str, schema: dict[str, Any]) -> None:
        """Persist a schema version (upsert)."""
        major, minor, patch = parse_semver(version)
        checksum = self._compute_checksum(schema)

        await self._collection.update_one(
            {"name": name, "version": version},
            {
                "$set": {
                    "name": name,
                    "version": version,
                    "version_major": major,
                    "version_minor": minor,
                    "version_patch": patch,
                    "schema": schema,
                    "checksum": checksum,
                    "updated_at": datetime.now(timezone.utc),
                },
                "$setOnInsert": {
                    "created_at": datetime.now(timezone.utc),
                },
            },
            upsert=True,
        )

    async def load(self, name: str, version: str) -> dict[str, Any] | None:
        """Load a specific schema version."""
        doc = await self._collection.find_one(
            {"name": name, "version": version},
            {"_id": 0, "schema": 1},
        )
        if doc is None:
            return None
        return doc["schema"]

    async def load_latest(self, name: str) -> tuple[str, dict[str, Any]] | None:
        """Load the latest version using decomposed version fields for sorting."""
        cursor = (
            self._collection.find(
                {"name": name},
                {"_id": 0, "version": 1, "schema": 1},
            )
            .sort(
                [
                    ("version_major", -1),
                    ("version_minor", -1),
                    ("version_patch", -1),
                ]
            )
            .limit(1)
        )

        doc = await cursor.to_list(length=1)
        if not doc:
            return None
        return (doc[0]["version"], doc[0]["schema"])

    async def list_versions(self, name: str) -> list[str]:
        """Return all version strings, sorted by semver ascending."""
        cursor = self._collection.find(
            {"name": name},
            {"_id": 0, "version": 1},
        )
        docs = await cursor.to_list(length=None)
        versions = [d["version"] for d in docs]
        return sort_versions(versions)

    async def list_names(self) -> list[str]:
        """Return all distinct schema names."""
        names = await self._collection.distinct("name")
        return sorted(names)

    async def exists(self, name: str, version: str) -> bool:
        """Check if a specific version exists."""
        count = await self._collection.count_documents(
            {"name": name, "version": version},
            limit=1,
        )
        return count > 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_checksum(schema: dict[str, Any]) -> str:
        """SHA-256 checksum of the canonical JSON representation."""
        canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        return f"sha256:{digest}"
