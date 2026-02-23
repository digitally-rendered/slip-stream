"""Composite schema storage — chains adapters with fallback + write-through.

Reads cascade through adapters in order until one succeeds.  Writes propagate
to all writable adapters (write-through) and optionally cache results in
earlier read layers.

Example::

    composite = CompositeSchemaStorage([
        FileSchemaStorage(Path("./schemas")),       # local cache (fast)
        HttpSchemaStorage("https://reg.example.com/schemas"),  # remote
    ])
    # load() checks file first, then HTTP if not found locally
    # save() writes to file (HTTP is read-only, silently skipped)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class CompositeSchemaStorage:
    """SchemaStoragePort adapter that chains multiple storage backends.

    Args:
        adapters: Ordered list of storage adapters.  Earlier adapters
            are checked first on reads and act as caches.
        write_through: When ``True`` (default), saves propagate to all
            adapters. When ``False``, only the first writable adapter
            receives saves.
    """

    def __init__(
        self,
        adapters: list[Any],
        *,
        write_through: bool = True,
    ) -> None:
        if not adapters:
            raise ValueError("CompositeSchemaStorage requires at least one adapter")
        self._adapters = adapters
        self._write_through = write_through

    # ------------------------------------------------------------------
    # SchemaStoragePort implementation
    # ------------------------------------------------------------------

    async def save(self, name: str, version: str, schema: dict[str, Any]) -> None:
        """Save to all adapters (write-through)."""
        for adapter in self._adapters:
            try:
                await adapter.save(name, version, schema)
            except Exception as exc:
                logger.warning(
                    "CompositeSchemaStorage: save to %s failed: %s",
                    type(adapter).__name__,
                    exc,
                )
                if not self._write_through:
                    raise

    async def load(self, name: str, version: str) -> dict[str, Any] | None:
        """Load from the first adapter that has the schema.

        If found in a later adapter, back-fill earlier adapters (cache warming).
        """
        for i, adapter in enumerate(self._adapters):
            try:
                result = await adapter.load(name, version)
            except Exception as exc:
                logger.warning(
                    "CompositeSchemaStorage: load from %s failed: %s",
                    type(adapter).__name__,
                    exc,
                )
                continue

            if result is not None:
                # Back-fill earlier adapters that didn't have it
                for j in range(i):
                    try:
                        await self._adapters[j].save(name, version, result)
                    except Exception:
                        pass
                return result

        return None

    async def load_latest(self, name: str) -> tuple[str, dict[str, Any]] | None:
        """Load latest from the first adapter that has the schema."""
        for i, adapter in enumerate(self._adapters):
            try:
                result = await adapter.load_latest(name)
            except Exception as exc:
                logger.warning(
                    "CompositeSchemaStorage: load_latest from %s failed: %s",
                    type(adapter).__name__,
                    exc,
                )
                continue

            if result is not None:
                version, schema = result
                # Back-fill earlier adapters
                for j in range(i):
                    try:
                        await self._adapters[j].save(name, version, schema)
                    except Exception:
                        pass
                return result

        return None

    async def list_versions(self, name: str) -> list[str]:
        """Merge version lists from all adapters, deduplicated and sorted."""
        from slip_stream.core.schema.versioning import sort_versions

        all_versions: set[str] = set()
        for adapter in self._adapters:
            try:
                versions = await adapter.list_versions(name)
                all_versions.update(versions)
            except Exception as exc:
                logger.warning(
                    "CompositeSchemaStorage: list_versions from %s failed: %s",
                    type(adapter).__name__,
                    exc,
                )

        return sort_versions(list(all_versions))

    async def list_names(self) -> list[str]:
        """Merge schema names from all adapters."""
        all_names: set[str] = set()
        for adapter in self._adapters:
            try:
                names = await adapter.list_names()
                all_names.update(names)
            except Exception as exc:
                logger.warning(
                    "CompositeSchemaStorage: list_names from %s failed: %s",
                    type(adapter).__name__,
                    exc,
                )

        return sorted(all_names)

    async def exists(self, name: str, version: str) -> bool:
        """Check any adapter."""
        for adapter in self._adapters:
            try:
                if await adapter.exists(name, version):
                    return True
            except Exception:
                continue
        return False
