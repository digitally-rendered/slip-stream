"""Hot schema reload watcher for slip-stream.

Watches a directory of JSON schema files for changes and reloads them into
the :class:`~slip_stream.core.schema.registry.SchemaRegistry` without
requiring an application restart.

The watcher uses a pure-asyncio polling approach — no third-party file-watch
library is required — making it safe to add as a zero-dependency background
task in the FastAPI lifespan.

Design decisions
----------------
- **Polling over inotify/FSEvents**: avoids a hard dependency on ``watchdog``
  or ``watchfiles``.  The 500 ms default interval is imperceptible for schema
  development workflows and imposes negligible CPU overhead.
- **mtime + size fingerprint**: cheap to compute, catches every write that
  changes content.  A rename-replace write pattern (used by most editors)
  always produces a new mtime.
- **Debounce per path**: a rapid burst of writes (e.g. ``vim`` swapfile flush)
  only triggers one reload after the quiet period expires.
- **Event emission via a custom async callback**: keeps this module decoupled
  from the framework-level ``EventBus``, which expects a ``RequestContext``
  tied to an HTTP request.  Callers that want EventBus integration pass an
  ``on_reload`` coroutine that bridges the two.

Usage::

    from pathlib import Path
    from slip_stream.core.schema.watcher import SchemaWatcher
    from slip_stream.core.schema.registry import SchemaRegistry

    watcher = SchemaWatcher(
        schema_dir=Path("./schemas"),
        registry=SchemaRegistry(),
        on_reload=my_async_callback,   # optional
        poll_interval=0.5,
        debounce_seconds=0.5,
    )

    # Inside FastAPI lifespan:
    await watcher.start()
    yield
    await watcher.stop()
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from slip_stream.core.schema.ref_resolver import RefResolver
from slip_stream.core.schema.registry import SchemaRegistry

logger = logging.getLogger(__name__)

# Async callback invoked after each successful schema reload.
# Signature: (schema_name: str, version: str, schema: dict) -> None
ReloadCallback = Callable[[str, str, Dict[str, Any]], Awaitable[None]]


def _file_fingerprint(path: Path) -> tuple[float, int]:
    """Return an (mtime, size) tuple for *path*, or (-1, -1) if missing."""
    try:
        stat = path.stat()
        return (stat.st_mtime, stat.st_size)
    except OSError:
        return (-1, -1)


def _load_schema_from_file(path: Path, schema_dir: Path) -> tuple[str, str, Dict[str, Any]] | None:
    """Read, parse, and $ref-resolve a schema file.

    Returns ``(schema_name, version, schema_dict)`` on success, or ``None``
    if the file is missing, unreadable, or contains invalid JSON.

    The schema name is derived from the file stem (e.g. ``widget.json``
    yields ``"widget"``).
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("SchemaWatcher: could not read %s — %s", path, exc)
        return None

    name = path.stem
    version = raw.get("version", "1.0.0")

    resolver = RefResolver(base_path=schema_dir)
    try:
        schema = resolver.resolve(raw)
    except ValueError as ref_err:
        logger.warning(
            "SchemaWatcher: $ref resolution failed for %s — %s", path, ref_err
        )
        schema = raw  # fall back to unresolved schema

    return name, version, schema


class SchemaWatcher:
    """Background task that polls a schema directory for JSON file changes.

    On detecting a create, modify, or delete event the watcher updates the
    :class:`~slip_stream.core.schema.registry.SchemaRegistry` in-memory and
    optionally calls *on_reload* so that downstream systems can react (e.g.
    emitting a ``schema_reloaded`` event onto the framework's ``EventBus``).

    .. note::
        Route generation is **not** updated on a hot reload — that requires an
        application restart.  The registry update ensures that model generation
        (``generate_document_model``, ``generate_create_model``,
        ``generate_update_model``) and schema lookups use the latest schema for
        all subsequent requests.

    Args:
        schema_dir: Directory containing ``*.json`` schema files.
        registry: The :class:`SchemaRegistry` singleton to update.
        on_reload: Optional async callback invoked after each reload with
            ``(schema_name, version, schema_dict)``.  Ideal for bridging to
            the framework ``EventBus``.
        poll_interval: How often (seconds) to scan the directory.
            Default: ``0.5``.
        debounce_seconds: Quiet period after the last change to a file before
            the reload fires.  Default: ``0.5``.
    """

    def __init__(
        self,
        schema_dir: Path,
        registry: SchemaRegistry,
        on_reload: Optional[ReloadCallback] = None,
        poll_interval: float = 0.5,
        debounce_seconds: float = 0.5,
    ) -> None:
        self._schema_dir = schema_dir
        self._registry = registry
        self._on_reload = on_reload
        self._poll_interval = poll_interval
        self._debounce_seconds = debounce_seconds

        # Last-known fingerprints: path → (mtime, size)
        self._fingerprints: Dict[Path, tuple[float, int]] = {}

        # Pending debounce handles: path → asyncio.TimerHandle
        self._pending: Dict[Path, asyncio.TimerHandle] = {}

        self._task: Optional[asyncio.Task[None]] = None
        self._running = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background polling task.

        Safe to call multiple times — subsequent calls are no-ops if the
        watcher is already running.
        """
        if self._running:
            logger.debug("SchemaWatcher: already running, ignoring start()")
            return

        self._running = True
        # Snapshot current state so we don't fire spurious events on startup.
        self._fingerprints = self._snapshot()
        self._task = asyncio.create_task(self._poll_loop(), name="schema-watcher")
        logger.info(
            "SchemaWatcher started (dir=%s, interval=%.2fs, debounce=%.2fs)",
            self._schema_dir,
            self._poll_interval,
            self._debounce_seconds,
        )

    async def stop(self) -> None:
        """Stop the background polling task and cancel pending debounce timers.

        Waits for the polling task to finish cleanly.
        """
        if not self._running:
            return

        self._running = False

        # Cancel any outstanding debounce timers
        for handle in self._pending.values():
            handle.cancel()
        self._pending.clear()

        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info("SchemaWatcher stopped")

    # ------------------------------------------------------------------
    # Internal — polling loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Run until cancelled, comparing directory snapshots each tick."""
        while self._running:
            try:
                await asyncio.sleep(self._poll_interval)
                self._check_for_changes()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover
                logger.exception("SchemaWatcher: unexpected error in poll loop")

    def _snapshot(self) -> Dict[Path, tuple[float, int]]:
        """Return a fingerprint dict for every ``*.json`` in the schema dir."""
        result: Dict[Path, tuple[float, int]] = {}
        if not self._schema_dir.exists():
            return result
        for p in self._schema_dir.glob("*.json"):
            result[p] = _file_fingerprint(p)
        return result

    def _check_for_changes(self) -> None:
        """Compare the current snapshot against the last-known fingerprints.

        Schedules debounced reloads for:

        - **Created / modified** files (fingerprint changed or new)
        - **Deleted** files (path disappeared from snapshot)
        """
        current = self._snapshot()
        known = self._fingerprints

        # Detect creates and modifications
        for path, fp in current.items():
            if known.get(path) != fp:
                self._schedule_reload(path, deleted=False)

        # Detect deletions
        for path in list(known.keys()):
            if path not in current:
                self._schedule_reload(path, deleted=True)

        self._fingerprints = current

    # ------------------------------------------------------------------
    # Internal — debounce and reload
    # ------------------------------------------------------------------

    def _schedule_reload(self, path: Path, *, deleted: bool) -> None:
        """Debounce a reload for *path*.

        Cancels any in-flight timer for the same path and starts a new one.
        This means only the final change in a rapid burst triggers a reload.
        """
        # Cancel an existing pending timer for this path
        existing = self._pending.pop(path, None)
        if existing is not None:
            existing.cancel()

        loop = asyncio.get_running_loop()
        handle = loop.call_later(
            self._debounce_seconds,
            self._fire_reload,
            path,
            deleted,
        )
        self._pending[path] = handle

    def _fire_reload(self, path: Path, deleted: bool) -> None:
        """Called by the event loop after the debounce delay expires."""
        self._pending.pop(path, None)

        if deleted:
            self._handle_delete(path)
        else:
            self._handle_upsert(path)

    def _handle_upsert(self, path: Path) -> None:
        """Reload a created or modified schema file.

        Writes directly into the registry's internal ``_schemas`` dict rather
        than calling ``register_schema()``.  This avoids the write-back path
        inside ``register_schema()`` — which would re-write the file to disk
        and immediately trigger another watcher event, creating an infinite loop.
        """
        result = _load_schema_from_file(path, self._schema_dir)
        if result is None:
            return

        name, version, schema = result

        # Evict stale model-cache entries for this schema so the registry
        # regenerates models from the updated definition on next access.
        self._evict_model_cache(name)

        # Update in-memory registry directly — do NOT call register_schema(),
        # which would write the file back to disk and re-trigger this handler.
        registry_schemas: dict = self._registry._schemas  # type: ignore[attr-defined]
        if name not in registry_schemas:
            registry_schemas[name] = {}
        registry_schemas[name][version] = schema

        logger.info("SchemaWatcher: reloaded schema '%s' v%s from %s", name, version, path)

        if self._on_reload is not None:
            asyncio.get_running_loop().create_task(
                self._on_reload(name, version, schema),
            )

    def _handle_delete(self, path: Path) -> None:
        """Remove a deleted schema from the registry."""
        name = path.stem
        if name in self._registry._schemas:
            del self._registry._schemas[name]  # type: ignore[attr-defined]
            self._evict_model_cache(name)
            logger.info("SchemaWatcher: removed schema '%s' (file deleted)", name)
        else:
            logger.debug("SchemaWatcher: deleted file %s had no registered schema", path)

    def _evict_model_cache(self, name: str) -> None:
        """Remove all cached model triples for *name* from the registry cache.

        The registry caches ``(name, version)`` → ``(doc, create, update)``
        model triples in ``_model_cache``.  After a schema changes, stale
        cached models must be purged so the next call to
        ``generate_*_model()`` builds fresh Pydantic classes.
        """
        cache: dict = self._registry._model_cache  # type: ignore[attr-defined]
        stale_keys = [k for k in cache if k[0] == name]
        for key in stale_keys:
            del cache[key]
        if stale_keys:
            logger.debug(
                "SchemaWatcher: evicted %d cached model(s) for schema '%s'",
                len(stale_keys),
                name,
            )
