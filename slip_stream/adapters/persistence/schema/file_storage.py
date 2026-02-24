"""File-system schema storage adapter.

Stores schemas as JSON files supporting two layouts:

**Flat (backward-compatible)**::

    schemas/
        widget.json        # version read from the file's "version" key

**Versioned directory**::

    schemas/
        widget/
            1.0.0.json
            2.0.0.json

Both layouts can coexist. The versioned directory takes precedence when
the same schema name appears in both.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from slip_stream.core.schema.versioning import latest_version, sort_versions

logger = logging.getLogger(__name__)


class FileSchemaStorage:
    """SchemaStoragePort adapter backed by the local filesystem.

    Args:
        schema_dir: Root directory containing schema files.
    """

    def __init__(self, schema_dir: Path) -> None:
        self._dir = schema_dir

    # ------------------------------------------------------------------
    # SchemaStoragePort implementation
    # ------------------------------------------------------------------

    async def save(self, name: str, version: str, schema: dict[str, Any]) -> None:
        """Save a schema version to a versioned directory layout."""

        def _write() -> None:
            versioned_dir = self._dir / name
            versioned_dir.mkdir(parents=True, exist_ok=True)
            path = versioned_dir / f"{version}.json"
            schema["version"] = version
            with open(path, "w", encoding="utf-8") as f:
                json.dump(schema, f, indent=2)

        await asyncio.to_thread(_write)

    async def load(self, name: str, version: str) -> dict[str, Any] | None:
        """Load a specific schema version.

        Checks versioned directory first, then flat file.
        """

        def _read() -> dict[str, Any] | None:
            # Try versioned directory first
            versioned_path = self._dir / name / f"{version}.json"
            if versioned_path.exists():
                return self._read_json(versioned_path)

            # Fall back to flat file
            flat_path = self._dir / f"{name}.json"
            if flat_path.exists():
                schema = self._read_json(flat_path)
                if schema and schema.get("version", "1.0.0") == version:
                    return schema

            return None

        return await asyncio.to_thread(_read)

    async def load_latest(self, name: str) -> tuple[str, dict[str, Any]] | None:
        """Load the latest version by semver."""
        versions = await self.list_versions(name)
        if not versions:
            return None
        latest = latest_version(versions)
        schema = await self.load(name, latest)
        if schema is None:
            return None
        return (latest, schema)

    async def list_versions(self, name: str) -> list[str]:
        """Return all version strings for a schema, sorted by semver."""

        def _list() -> list[str]:
            versions: list[str] = []

            # Check versioned directory
            versioned_dir = self._dir / name
            if versioned_dir.is_dir():
                for f in versioned_dir.glob("*.json"):
                    versions.append(f.stem)

            # Check flat file (if not already covered by versioned dir)
            flat_path = self._dir / f"{name}.json"
            if flat_path.exists():
                schema = self._read_json(flat_path)
                if schema:
                    v = schema.get("version", "1.0.0")
                    if v not in versions:
                        versions.append(v)

            return sort_versions(versions)

        return await asyncio.to_thread(_list)

    async def list_names(self) -> list[str]:
        """Return all schema names discovered from files and directories."""

        def _list() -> list[str]:
            names: set[str] = set()

            if not self._dir.exists():
                return []

            # Flat JSON files
            for f in self._dir.glob("*.json"):
                names.add(f.stem)

            # Versioned directories (contain .json files)
            for d in self._dir.iterdir():
                if d.is_dir() and any(d.glob("*.json")):
                    names.add(d.name)

            return sorted(names)

        return await asyncio.to_thread(_list)

    async def exists(self, name: str, version: str) -> bool:
        """Check if a specific version exists on disk."""
        return await self.load(name, version) is not None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.error("Error reading schema file %s: %s", path, e)
            return None
