"""Tests for SchemaWatcher — hot schema reload system.

Uses a real temporary directory and real JSON schema files to exercise the
polling, debounce, registry update, and event-emission logic without any
mocking of the filesystem.

All tests are async because SchemaWatcher.start()/stop() are async and the
poll loop runs as an asyncio Task.  The ``asyncio_mode = "auto"`` setting in
``pyproject.toml`` means no explicit ``@pytest.mark.asyncio`` markers are
required, but they are included for clarity.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from slip_stream.core.schema.registry import SchemaRegistry
from slip_stream.core.schema.watcher import SchemaWatcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_schema(path: Path, schema: Dict[str, Any]) -> None:
    """Write *schema* as JSON to *path*, updating mtime."""
    path.write_text(json.dumps(schema), encoding="utf-8")


def _minimal_schema(name: str, version: str = "1.0.0", extra_field: str | None = None) -> Dict[str, Any]:
    """Return a minimal but valid JSON Schema dict."""
    props: Dict[str, Any] = {"name": {"type": "string"}}
    if extra_field:
        props[extra_field] = {"type": "string"}
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": name.title(),
        "version": version,
        "type": "object",
        "required": ["name"],
        "properties": props,
    }


async def _wait_for(condition_fn, timeout: float = 3.0, interval: float = 0.05) -> bool:
    """Poll *condition_fn()* until it returns truthy or *timeout* seconds pass.

    Returns ``True`` if the condition was met, ``False`` on timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition_fn():
            return True
        await asyncio.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_schema_dir(tmp_path: Path) -> Path:
    """Return an empty temporary directory for schema files."""
    return tmp_path


@pytest.fixture
def fresh_registry(tmp_schema_dir: Path) -> SchemaRegistry:
    """Return a fresh SchemaRegistry pointed at the temp dir.

    SchemaRegistry.reset() is called by the autouse fixture in conftest.py, so
    this fixture always starts clean.
    """
    return SchemaRegistry(schema_dir=tmp_schema_dir)


@pytest.fixture
def watcher(tmp_schema_dir: Path, fresh_registry: SchemaRegistry) -> SchemaWatcher:
    """Return a SchemaWatcher with a fast poll + debounce for test speed."""
    return SchemaWatcher(
        schema_dir=tmp_schema_dir,
        registry=fresh_registry,
        poll_interval=0.05,    # 50 ms — fast enough for tests
        debounce_seconds=0.1,  # 100 ms debounce
    )


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------

class TestSchemaWatcherLifecycle:
    """Tests for start() / stop() behaviour."""

    @pytest.mark.asyncio
    async def test_start_creates_background_task(self, watcher: SchemaWatcher) -> None:
        await watcher.start()
        assert watcher._task is not None
        assert not watcher._task.done()
        await watcher.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_background_task(self, watcher: SchemaWatcher) -> None:
        await watcher.start()
        task = watcher._task
        await watcher.stop()
        assert task is not None
        assert task.done()

    @pytest.mark.asyncio
    async def test_double_start_is_idempotent(self, watcher: SchemaWatcher) -> None:
        await watcher.start()
        first_task = watcher._task
        await watcher.start()  # second call should be a no-op
        assert watcher._task is first_task
        await watcher.stop()

    @pytest.mark.asyncio
    async def test_stop_before_start_is_safe(self, watcher: SchemaWatcher) -> None:
        # Should not raise
        await watcher.stop()

    @pytest.mark.asyncio
    async def test_double_stop_is_safe(self, watcher: SchemaWatcher) -> None:
        await watcher.start()
        await watcher.stop()
        await watcher.stop()  # second stop should be a no-op


# ---------------------------------------------------------------------------
# File detection tests
# ---------------------------------------------------------------------------

class TestSchemaWatcherFileDetection:
    """Tests for create / modify / delete detection."""

    @pytest.mark.asyncio
    async def test_detects_new_schema_file(
        self,
        watcher: SchemaWatcher,
        fresh_registry: SchemaRegistry,
        tmp_schema_dir: Path,
    ) -> None:
        """A schema file created after start() is picked up and registered."""
        await watcher.start()

        schema_path = tmp_schema_dir / "gadget.json"
        _write_schema(schema_path, _minimal_schema("gadget"))

        registered = await _wait_for(
            lambda: "gadget" in fresh_registry.get_schema_names()
        )
        await watcher.stop()

        assert registered, "SchemaWatcher did not detect the new gadget.json file"

    @pytest.mark.asyncio
    async def test_detects_modified_schema_file(
        self,
        watcher: SchemaWatcher,
        fresh_registry: SchemaRegistry,
        tmp_schema_dir: Path,
    ) -> None:
        """Modifying an existing file causes the registry to reflect the new schema."""
        # Write initial schema and register it before the watcher starts
        schema_path = tmp_schema_dir / "widget.json"
        _write_schema(schema_path, _minimal_schema("widget"))
        fresh_registry.register_schema("widget", _minimal_schema("widget"), "1.0.0")

        await watcher.start()

        # Overwrite with a schema that has an extra field
        await asyncio.sleep(0.15)  # let the watcher snapshot the initial state
        _write_schema(schema_path, _minimal_schema("widget", extra_field="colour"))

        updated = await _wait_for(
            lambda: "colour" in fresh_registry.get_schema("widget").get("properties", {})
        )
        await watcher.stop()

        assert updated, "SchemaWatcher did not detect modification to widget.json"

    @pytest.mark.asyncio
    async def test_detects_deleted_schema_file(
        self,
        watcher: SchemaWatcher,
        fresh_registry: SchemaRegistry,
        tmp_schema_dir: Path,
    ) -> None:
        """Deleting a schema file removes it from the registry."""
        schema_path = tmp_schema_dir / "doomed.json"
        _write_schema(schema_path, _minimal_schema("doomed"))
        fresh_registry.register_schema("doomed", _minimal_schema("doomed"), "1.0.0")

        await watcher.start()
        await asyncio.sleep(0.15)  # let the watcher snapshot the initial state

        schema_path.unlink()

        removed = await _wait_for(
            lambda: "doomed" not in fresh_registry.get_schema_names()
        )
        await watcher.stop()

        assert removed, "SchemaWatcher did not remove schema after file deletion"

    @pytest.mark.asyncio
    async def test_ignores_non_json_files(
        self,
        watcher: SchemaWatcher,
        fresh_registry: SchemaRegistry,
        tmp_schema_dir: Path,
    ) -> None:
        """Non-.json files in the schema directory are silently ignored."""
        await watcher.start()
        (tmp_schema_dir / "README.md").write_text("# hello")
        (tmp_schema_dir / "notes.txt").write_text("notes")

        await asyncio.sleep(0.4)  # give the watcher several poll cycles
        await watcher.stop()

        # No schemas should have been registered from non-JSON files
        assert fresh_registry.get_schema_names() == []

    @pytest.mark.asyncio
    async def test_new_schema_updates_model_generation(
        self,
        watcher: SchemaWatcher,
        fresh_registry: SchemaRegistry,
        tmp_schema_dir: Path,
    ) -> None:
        """After a hot reload, generate_document_model() reflects the new fields."""
        schema_path = tmp_schema_dir / "product.json"
        _write_schema(schema_path, _minimal_schema("product"))
        await watcher.start()

        # Wait until registered
        await _wait_for(lambda: "product" in fresh_registry.get_schema_names())

        # Now update with a new field
        _write_schema(schema_path, _minimal_schema("product", extra_field="sku"))

        def _has_sku():
            try:
                return "sku" in fresh_registry.get_schema("product").get("properties", {})
            except ValueError:
                return False

        updated = await _wait_for(_has_sku)
        assert updated

        doc_model = fresh_registry.generate_document_model("product")
        assert "sku" in doc_model.model_fields

        await watcher.stop()


# ---------------------------------------------------------------------------
# Debounce tests
# ---------------------------------------------------------------------------

class TestSchemaWatcherDebounce:
    """Tests that rapid changes are coalesced into a single reload."""

    @pytest.mark.asyncio
    async def test_debounces_rapid_writes(
        self,
        watcher: SchemaWatcher,
        fresh_registry: SchemaRegistry,
        tmp_schema_dir: Path,
    ) -> None:
        """Multiple rapid writes to the same file produce exactly one reload call."""
        reload_calls: List[str] = []

        async def capture_reload(name: str, version: str, schema: Dict[str, Any]) -> None:
            reload_calls.append(name)

        # Rebuild watcher with the callback
        w = SchemaWatcher(
            schema_dir=tmp_schema_dir,
            registry=fresh_registry,
            on_reload=capture_reload,
            poll_interval=0.05,
            debounce_seconds=0.2,  # 200 ms quiet window
        )
        await w.start()
        await asyncio.sleep(0.1)  # allow initial snapshot

        schema_path = tmp_schema_dir / "burst.json"

        # Fire 5 rapid writes within a single poll cycle
        for i in range(5):
            _write_schema(schema_path, _minimal_schema("burst", extra_field=f"field_{i}"))
            await asyncio.sleep(0.01)

        # Wait long enough for debounce to fire exactly once
        await asyncio.sleep(0.6)
        await w.stop()

        assert len(reload_calls) == 1, (
            f"Expected exactly 1 reload call after debounce, got {len(reload_calls)}"
        )

    @pytest.mark.asyncio
    async def test_debounce_timer_reset_on_each_change(
        self,
        watcher: SchemaWatcher,
        fresh_registry: SchemaRegistry,
        tmp_schema_dir: Path,
    ) -> None:
        """Each new write resets the debounce timer for that path."""
        reloaded_versions: List[str] = []

        async def capture_version(name: str, version: str, schema: Dict[str, Any]) -> None:
            reloaded_versions.append(schema.get("properties", {}).get("seq", {}).get("description", ""))

        w = SchemaWatcher(
            schema_dir=tmp_schema_dir,
            registry=fresh_registry,
            on_reload=capture_version,
            poll_interval=0.05,
            debounce_seconds=0.15,
        )
        await w.start()
        await asyncio.sleep(0.1)

        schema_path = tmp_schema_dir / "seq.json"

        # Write three times within the debounce window — only the last should win
        for i in range(3):
            s = _minimal_schema("seq")
            s["properties"]["seq"] = {"type": "string", "description": f"v{i}"}
            _write_schema(schema_path, s)
            await asyncio.sleep(0.05)  # shorter than debounce, so timer keeps resetting

        await asyncio.sleep(0.5)  # wait for the final debounce to fire
        await w.stop()

        # The final schema should reflect v2
        assert fresh_registry.get_schema("seq")["properties"]["seq"]["description"] == "v2"


# ---------------------------------------------------------------------------
# on_reload callback / event emission tests
# ---------------------------------------------------------------------------

class TestSchemaWatcherEventEmission:
    """Tests for the on_reload callback (schema_reloaded event bridge)."""

    @pytest.mark.asyncio
    async def test_on_reload_called_with_correct_args(
        self,
        tmp_schema_dir: Path,
        fresh_registry: SchemaRegistry,
    ) -> None:
        """on_reload receives (schema_name, version, schema_dict)."""
        received: List[tuple] = []

        async def capture(name: str, version: str, schema: Dict[str, Any]) -> None:
            received.append((name, version, schema))

        w = SchemaWatcher(
            schema_dir=tmp_schema_dir,
            registry=fresh_registry,
            on_reload=capture,
            poll_interval=0.05,
            debounce_seconds=0.1,
        )
        await w.start()

        _write_schema(tmp_schema_dir / "order.json", _minimal_schema("order", version="2.0.0"))

        got_call = await _wait_for(lambda: len(received) > 0)
        await w.stop()

        assert got_call, "on_reload was never called"
        name, version, schema = received[0]
        assert name == "order"
        assert version == "2.0.0"
        assert schema["title"] == "Order"

    @pytest.mark.asyncio
    async def test_on_reload_not_called_when_no_changes(
        self,
        tmp_schema_dir: Path,
        fresh_registry: SchemaRegistry,
    ) -> None:
        """on_reload is never called if the schema directory is untouched."""
        called = False

        async def mark_called(name: str, version: str, schema: Dict[str, Any]) -> None:
            nonlocal called
            called = True

        w = SchemaWatcher(
            schema_dir=tmp_schema_dir,
            registry=fresh_registry,
            on_reload=mark_called,
            poll_interval=0.05,
            debounce_seconds=0.1,
        )
        await w.start()
        await asyncio.sleep(0.5)  # several poll cycles, no file changes
        await w.stop()

        assert not called, "on_reload should not have been called with no file changes"

    @pytest.mark.asyncio
    async def test_on_reload_integration_with_event_bus(
        self,
        tmp_schema_dir: Path,
        fresh_registry: SchemaRegistry,
    ) -> None:
        """Demonstrates bridging on_reload to an EventBus-style subscriber list."""
        emitted_events: List[Dict[str, Any]] = []

        async def schema_reloaded_bridge(
            schema_name: str, version: str, schema: Dict[str, Any]
        ) -> None:
            emitted_events.append({
                "event": "schema_reloaded",
                "schema_name": schema_name,
                "version": version,
            })

        w = SchemaWatcher(
            schema_dir=tmp_schema_dir,
            registry=fresh_registry,
            on_reload=schema_reloaded_bridge,
            poll_interval=0.05,
            debounce_seconds=0.1,
        )
        await w.start()

        _write_schema(tmp_schema_dir / "invoice.json", _minimal_schema("invoice", version="3.1.0"))

        fired = await _wait_for(lambda: len(emitted_events) > 0)
        await w.stop()

        assert fired
        event = emitted_events[0]
        assert event["event"] == "schema_reloaded"
        assert event["schema_name"] == "invoice"
        assert event["version"] == "3.1.0"

    @pytest.mark.asyncio
    async def test_on_reload_not_called_on_delete(
        self,
        tmp_schema_dir: Path,
        fresh_registry: SchemaRegistry,
    ) -> None:
        """on_reload is NOT called for deletions — only for creates/modifies."""
        reload_calls: List[str] = []

        async def track(name: str, version: str, schema: Dict[str, Any]) -> None:
            reload_calls.append(name)

        schema_path = tmp_schema_dir / "temp.json"
        _write_schema(schema_path, _minimal_schema("temp"))
        fresh_registry.register_schema("temp", _minimal_schema("temp"), "1.0.0")

        w = SchemaWatcher(
            schema_dir=tmp_schema_dir,
            registry=fresh_registry,
            on_reload=track,
            poll_interval=0.05,
            debounce_seconds=0.1,
        )
        await w.start()
        await asyncio.sleep(0.2)  # let snapshot settle

        reload_calls.clear()  # discard any reload from initial write
        schema_path.unlink()

        await asyncio.sleep(0.5)  # give time for a delete event to propagate
        await w.stop()

        assert reload_calls == [], (
            "on_reload should not be called on file deletion"
        )


# ---------------------------------------------------------------------------
# Model cache eviction tests
# ---------------------------------------------------------------------------

class TestSchemaWatcherModelCacheEviction:
    """Tests that the Pydantic model cache is cleared on reload."""

    @pytest.mark.asyncio
    async def test_model_cache_evicted_on_reload(
        self,
        tmp_schema_dir: Path,
        fresh_registry: SchemaRegistry,
    ) -> None:
        """After a hot reload the registry regenerates models with the new schema."""
        schema_path = tmp_schema_dir / "device.json"
        _write_schema(schema_path, _minimal_schema("device"))
        fresh_registry.register_schema("device", _minimal_schema("device"), "1.0.0")

        # Pre-warm the model cache via get_model_for_version (which caches)
        _ = fresh_registry.get_model_for_version("device")
        assert ("device", "1.0.0") in fresh_registry._model_cache  # type: ignore[attr-defined]

        w = SchemaWatcher(
            schema_dir=tmp_schema_dir,
            registry=fresh_registry,
            poll_interval=0.05,
            debounce_seconds=0.1,
        )
        await w.start()
        await asyncio.sleep(0.15)  # let snapshot settle

        # Modify the schema to add a field
        _write_schema(schema_path, _minimal_schema("device", extra_field="serial"))

        def _has_serial():
            try:
                return "serial" in fresh_registry.get_schema("device").get("properties", {})
            except ValueError:
                return False

        updated = await _wait_for(_has_serial)
        assert updated

        # The old cache entry must be gone
        assert ("device", "1.0.0") not in fresh_registry._model_cache  # type: ignore[attr-defined]

        # And a freshly generated model must include the new field
        new_model = fresh_registry.generate_document_model("device")
        assert "serial" in new_model.model_fields

        await w.stop()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestSchemaWatcherEdgeCases:
    """Tests for invalid files, missing directories, and boundary conditions."""

    @pytest.mark.asyncio
    async def test_invalid_json_file_does_not_crash_watcher(
        self,
        tmp_schema_dir: Path,
        fresh_registry: SchemaRegistry,
    ) -> None:
        """A file containing invalid JSON is skipped without stopping the watcher."""
        w = SchemaWatcher(
            schema_dir=tmp_schema_dir,
            registry=fresh_registry,
            poll_interval=0.05,
            debounce_seconds=0.1,
        )
        await w.start()

        (tmp_schema_dir / "broken.json").write_text("{ this is not valid json !!!", encoding="utf-8")

        # Wait several poll cycles — watcher should still be alive
        await asyncio.sleep(0.5)

        assert w._running, "Watcher should still be running after an invalid JSON file"
        assert w._task is not None and not w._task.done()

        await w.stop()

    @pytest.mark.asyncio
    async def test_nonexistent_schema_dir_does_not_crash_watcher(
        self,
        tmp_path: Path,
        fresh_registry: SchemaRegistry,
    ) -> None:
        """Watcher starts cleanly even if the schema directory does not exist yet."""
        missing_dir = tmp_path / "does_not_exist"

        w = SchemaWatcher(
            schema_dir=missing_dir,
            registry=fresh_registry,
            poll_interval=0.05,
            debounce_seconds=0.1,
        )
        await w.start()
        await asyncio.sleep(0.3)
        assert w._running

        await w.stop()

    @pytest.mark.asyncio
    async def test_multiple_schemas_detected_independently(
        self,
        tmp_schema_dir: Path,
        fresh_registry: SchemaRegistry,
    ) -> None:
        """Each schema file is tracked independently by the watcher."""
        w = SchemaWatcher(
            schema_dir=tmp_schema_dir,
            registry=fresh_registry,
            poll_interval=0.05,
            debounce_seconds=0.1,
        )
        await w.start()

        _write_schema(tmp_schema_dir / "alpha.json", _minimal_schema("alpha"))
        _write_schema(tmp_schema_dir / "beta.json", _minimal_schema("beta"))

        registered = await _wait_for(
            lambda: {"alpha", "beta"}.issubset(set(fresh_registry.get_schema_names()))
        )
        await w.stop()

        assert registered, "Both schemas should be detected independently"
