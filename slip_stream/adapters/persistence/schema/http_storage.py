"""HTTP schema storage adapter — fetches schemas from a remote vending API.

Consumes the schema vending REST API exposed by another slip-stream instance
(or any service that follows the same ``/schemas/`` contract).

Schemas are cached in memory with a configurable TTL to reduce network calls.

Requires ``httpx`` (optional dependency)::

    pip install slip-stream[remote]
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class HttpSchemaStorage:
    """SchemaStoragePort adapter backed by a remote HTTP registry.

    Args:
        base_url: Root URL of the schema vending API, e.g.
            ``"https://registry.example.com/schemas"``.
        ttl: Cache time-to-live in seconds (default 300 = 5 min).
        headers: Optional dict of extra HTTP headers (e.g. auth tokens).
        timeout: HTTP request timeout in seconds (default 10).
    """

    def __init__(
        self,
        base_url: str,
        *,
        ttl: float = 300.0,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._ttl = ttl
        self._headers = headers or {}
        self._timeout = timeout
        self._cache: dict[str, tuple[float, Any]] = {}
        self._client: Any = None

    async def _get_client(self) -> Any:
        if self._client is None:
            try:
                import httpx
            except ImportError as e:
                raise ImportError(
                    "httpx is required for HttpSchemaStorage. "
                    "Install it with: pip install slip-stream[remote]"
                ) from e
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=self._headers,
                timeout=self._timeout,
            )
        return self._client

    def _cache_get(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.monotonic() - ts > self._ttl:
            del self._cache[key]
            return None
        return value

    def _cache_set(self, key: str, value: Any) -> None:
        self._cache[key] = (time.monotonic(), value)

    # ------------------------------------------------------------------
    # SchemaStoragePort implementation
    # ------------------------------------------------------------------

    async def save(self, name: str, version: str, schema: dict[str, Any]) -> None:
        """Remote storage is read-only — save is a no-op.

        Write-through is handled by CompositeSchemaStorage wrapping a
        writable adapter.
        """
        logger.debug(
            "HttpSchemaStorage.save() is a no-op (read-only): %s@%s",
            name,
            version,
        )

    async def load(self, name: str, version: str) -> dict[str, Any] | None:
        """Fetch a specific schema version from the remote API."""
        cache_key = f"load:{name}:{version}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        client = await self._get_client()
        resp = await client.get(f"/{name}/{version}")

        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        data = resp.json()
        schema = data.get("schema", data)
        self._cache_set(cache_key, schema)
        return schema

    async def load_latest(self, name: str) -> tuple[str, dict[str, Any]] | None:
        """Fetch the latest version from the remote API."""
        cache_key = f"latest:{name}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        client = await self._get_client()
        resp = await client.get(f"/{name}/latest")

        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        data = resp.json()
        version = data.get("version", "1.0.0")
        schema = data.get("schema", data)
        result = (version, schema)
        self._cache_set(cache_key, result)
        return result

    async def list_versions(self, name: str) -> list[str]:
        """List all versions from the remote API."""
        cache_key = f"versions:{name}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        client = await self._get_client()
        resp = await client.get(f"/{name}")

        if resp.status_code == 404:
            return []
        resp.raise_for_status()

        data = resp.json()
        versions = data.get("versions", [])
        self._cache_set(cache_key, versions)
        return versions

    async def list_names(self) -> list[str]:
        """List all schema names from the remote API."""
        cache_key = "names"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        client = await self._get_client()
        resp = await client.get("/")

        resp.raise_for_status()

        data = resp.json()
        schemas = data.get("schemas", [])
        names = [s["name"] if isinstance(s, dict) else s for s in schemas]
        self._cache_set(cache_key, names)
        return names

    async def exists(self, name: str, version: str) -> bool:
        """Check existence by attempting to load."""
        return await self.load(name, version) is not None

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
