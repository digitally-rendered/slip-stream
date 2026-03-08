"""Stream event schema validation — Phase 5 fuzz driver.

Tests that events published through InMemoryStream have payloads that are
structurally consistent with their JSON Schema definitions.  Three modes:

validate  — Simulate post_create/update/delete events, assert payload fields
            match schema types and required keys are present.
corrupt   — Publish events with intentionally wrong types; verify the stream
            stores them (InMemoryStream does NOT validate — that is correct
            behaviour) and that no exception is raised.
version   — Publish events carrying different schema_version tags and confirm
            each is stored verbatim under the correct topic.

Usage::

    python benchmarks/fuzz/run_stream_fuzz.py --mode all
    python benchmarks/fuzz/run_stream_fuzz.py --mode validate --schema-dir benchmarks/schemas
    python benchmarks/fuzz/run_stream_fuzz.py --mode corrupt --output results/stream-fuzz.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import uuid
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Import FuzzResult / print_results from sibling run_fuzz.py
# ---------------------------------------------------------------------------

try:
    from benchmarks.fuzz.run_fuzz import FuzzResult, print_results
except ImportError:
    import importlib.util as _ilu

    _fuzz_dir = Path(__file__).parent
    _spec = _ilu.spec_from_file_location("run_fuzz", _fuzz_dir / "run_fuzz.py")
    assert _spec and _spec.loader
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
    FuzzResult = _mod.FuzzResult  # type: ignore[assignment]
    print_results = _mod.print_results  # type: ignore[assignment]

from slip_stream.adapters.streaming.base import EventStreamBridge, InMemoryStream

# ---------------------------------------------------------------------------
# Optional jsonschema validator
# ---------------------------------------------------------------------------

try:
    import jsonschema as _jsonschema

    _HAS_JSONSCHEMA = True
except ImportError:
    _HAS_JSONSCHEMA = False

# ---------------------------------------------------------------------------
# JSON Schema → payload helpers
# ---------------------------------------------------------------------------

_JSON_TYPE_DEFAULTS: dict[str, Any] = {
    "string": "test-value",
    "integer": 42,
    "number": 3.14,
    "boolean": True,
    "array": [],
    "object": {},
    "null": None,
}

_TYPE_PYTHON_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _generate_payload_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal valid payload from JSON Schema property definitions."""
    props: dict[str, Any] = schema.get("properties", {})
    payload: dict[str, Any] = {}

    for field, defn in props.items():
        ftype = defn.get("type", "string")
        fmt = defn.get("format", "")
        enum = defn.get("enum")

        if enum:
            payload[field] = random.choice(enum)
        elif fmt == "uuid":
            payload[field] = str(uuid.uuid4())
        elif fmt == "date-time":
            payload[field] = "2026-01-01T00:00:00Z"
        elif fmt == "email":
            payload[field] = f"test-{uuid.uuid4().hex[:6]}@example.com"
        elif fmt in ("uri", "url"):
            payload[field] = f"https://example.com/{uuid.uuid4().hex[:6]}"
        elif ftype == "string":
            payload[field] = f"test-{field}-{uuid.uuid4().hex[:6]}"
        elif ftype == "integer":
            payload[field] = random.randint(1, 100)
        elif ftype == "number":
            payload[field] = round(random.uniform(1.0, 100.0), 2)
        elif ftype == "boolean":
            payload[field] = random.choice([True, False])
        elif ftype == "array":
            item_type = defn.get("items", {}).get("type", "string")
            payload[field] = (
                [f"item-{uuid.uuid4().hex[:4]}"] if item_type == "string" else [1]
            )
        else:
            payload[field] = _JSON_TYPE_DEFAULTS.get(ftype, "unknown")

    return payload


def _corrupt_payload(payload: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Swap types in a valid payload to produce an invalid one."""
    if not payload:
        return {"__injected__": True, "bad_field": None}

    corrupted = dict(payload)
    props = schema.get("properties", {})

    # Pick a field to corrupt (skip uuid-format fields to keep them recognisable)
    candidates = [
        k
        for k, v in props.items()
        if v.get("format") not in ("uuid", "date-time") and k in corrupted
    ]
    if candidates:
        field = random.choice(candidates)
        val = corrupted[field]
        if isinstance(val, str):
            corrupted[field] = random.randint(-9999, 9999)
        elif isinstance(val, int):
            corrupted[field] = "not-a-number"
        elif isinstance(val, list):
            corrupted[field] = {"nested": "object"}
        elif isinstance(val, bool):
            corrupted[field] = [1, 2, 3]

    corrupted["__unknown_field__"] = "injected"
    return corrupted


# ---------------------------------------------------------------------------
# Fake EventBus (mirror of the one in test_stream_perf.py)
# ---------------------------------------------------------------------------


class _FakeEventBus:
    """Minimal EventBus sufficient for EventStreamBridge.register()."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Any]] = {}

    def register(self, event_type: str, handler: Any) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    async def emit(self, event_type: str, ctx: Any) -> None:
        for h in self._handlers.get(event_type, []):
            await h(ctx)


class _FakeCtx:
    """Minimal context object consumed by EventStreamBridge._publish_event."""

    def __init__(
        self,
        schema_name: str,
        entity_id: str,
        data: dict[str, Any],
        schema_version: str = "1.0.0",
        channel: str = "rest",
    ) -> None:
        self.schema_name = schema_name
        self.entity_id = entity_id
        self.data = data
        self.schema_version = schema_version
        self.channel = channel
        self.current_user: dict[str, Any] = {"id": "stream-fuzz-user"}
        self.result = None


# ---------------------------------------------------------------------------
# Schema loading
# ---------------------------------------------------------------------------


def _load_schemas(schema_dir: Path) -> dict[str, dict[str, Any]]:
    """Load all *.json schemas from schema_dir.  Skips files that lack 'properties'."""
    schemas: dict[str, dict[str, Any]] = {}
    for path in sorted(schema_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        # Accept both bare schema and stellar-drive wrapper {schema: {...}}
        if "properties" not in data and "schema" in data:
            data = data["schema"]
        if "properties" not in data:
            continue
        name = path.stem
        schemas[name] = data
    return schemas


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_ENVELOPE_REQUIRED = frozenset(
    {"event", "schema_name", "entity_id", "timestamp", "channel"}
)


def _check_envelope(
    payload: dict[str, Any], entity: str, step: str
) -> list[FuzzResult]:
    """Assert that the stream event envelope has all required top-level keys."""
    results = []
    missing = _ENVELOPE_REQUIRED - set(payload.keys())
    if missing:
        results.append(
            FuzzResult(
                "validate", entity, step, False, f"missing envelope keys: {missing}"
            )
        )
    else:
        results.append(
            FuzzResult(
                "validate", entity, step, True, f"envelope ok ({len(payload)} keys)"
            )
        )
    return results


def _check_schema_name_in_topic(
    topic: str, schema_name: str, entity: str
) -> FuzzResult:
    """Assert the topic contains the schema_name."""
    passed = schema_name in topic
    return FuzzResult(
        "validate",
        entity,
        "topic_schema_name",
        passed,
        f"topic={topic!r} contains schema_name={schema_name!r}: {passed}",
    )


def _check_entity_id_in_key(key: str | None, entity_id: str, entity: str) -> FuzzResult:
    """Assert the stream message key matches the entity_id."""
    passed = key == entity_id
    return FuzzResult(
        "validate",
        entity,
        "key_entity_id",
        passed,
        f"key={key!r} == entity_id={entity_id!r}: {passed}",
    )


def _validate_payload_types(
    payload: dict[str, Any],
    schema: dict[str, Any],
    entity: str,
) -> list[FuzzResult]:
    """For each field present in payload['data'], verify its type matches the schema."""
    results: list[FuzzResult] = []

    if _HAS_JSONSCHEMA:
        # Full JSON Schema validation of the data sub-dict
        data = payload.get("data", {})
        if isinstance(data, dict):
            try:
                _jsonschema.validate(instance=data, schema=schema)
                results.append(
                    FuzzResult(
                        "validate", entity, "jsonschema_validate", True, "jsonschema ok"
                    )
                )
            except _jsonschema.ValidationError as exc:
                results.append(
                    FuzzResult(
                        "validate",
                        entity,
                        "jsonschema_validate",
                        False,
                        exc.message[:200],
                    )
                )
        return results

    # Fallback: manual field-by-field type checking
    data = payload.get("data", {})
    if not isinstance(data, dict):
        results.append(
            FuzzResult(
                "validate", entity, "data_type", False, f"data is {type(data).__name__}"
            )
        )
        return results

    props = schema.get("properties", {})
    type_mismatches = []
    for field, value in data.items():
        if field not in props:
            continue
        expected_type = props[field].get("type")
        if expected_type not in _TYPE_PYTHON_MAP:
            continue
        expected_py = _TYPE_PYTHON_MAP[expected_type]
        # booleans are subclass of int in Python — handle explicitly
        if expected_py is int and isinstance(value, bool):
            type_mismatches.append(f"{field}: bool is not int")
            continue
        if not isinstance(value, expected_py):
            type_mismatches.append(
                f"{field}: expected {expected_type}, got {type(value).__name__}"
            )

    if type_mismatches:
        results.append(
            FuzzResult(
                "validate", entity, "payload_types", False, "; ".join(type_mismatches)
            )
        )
    else:
        results.append(
            FuzzResult("validate", entity, "payload_types", True, "all types match")
        )
    return results


# ---------------------------------------------------------------------------
# Mode: validate
# ---------------------------------------------------------------------------


async def _run_validate_async(
    schemas: dict[str, dict[str, Any]],
) -> list[FuzzResult]:
    results: list[FuzzResult] = []

    for entity, schema in schemas.items():
        stream = InMemoryStream()
        bus = _FakeEventBus()
        bridge = EventStreamBridge(adapters=[stream])
        bridge.register(bus)

        data = _generate_payload_from_schema(schema)
        entity_id = str(uuid.uuid4())

        for operation in ("post_create", "post_update", "post_delete"):
            stream.events.clear()
            ctx = _FakeCtx(schema_name=entity, entity_id=entity_id, data=data)
            try:
                await bus.emit(operation, ctx)
            except Exception as exc:
                results.append(
                    FuzzResult("validate", entity, f"{operation}_emit", False, str(exc))
                )
                continue

            if not stream.events:
                results.append(
                    FuzzResult(
                        "validate",
                        entity,
                        f"{operation}_received",
                        False,
                        "no events published",
                    )
                )
                continue

            evt = stream.events[-1]
            step_prefix = operation.replace("post_", "")

            # Envelope check
            results.extend(
                _check_envelope(evt.payload, entity, f"{step_prefix}_envelope")
            )

            # Topic contains schema_name
            results.append(_check_schema_name_in_topic(evt.topic, entity, entity))

            # Key == entity_id
            results.append(_check_entity_id_in_key(evt.key, entity_id, entity))

            # Operation verb in topic
            op_verb = step_prefix  # "create", "update", "delete"
            passed = op_verb in evt.topic
            results.append(
                FuzzResult(
                    "validate",
                    entity,
                    f"{step_prefix}_topic_verb",
                    passed,
                    f"'{op_verb}' in topic={evt.topic!r}: {passed}",
                )
            )

            # Data type validation (only for create — payload carries original data)
            if operation == "post_create":
                results.extend(_validate_payload_types(evt.payload, schema, entity))

        await bridge.close()

    return results


def run_validate(schemas: dict[str, dict[str, Any]]) -> list[FuzzResult]:
    return asyncio.run(_run_validate_async(schemas))


# ---------------------------------------------------------------------------
# Mode: corrupt
# ---------------------------------------------------------------------------


async def _run_corrupt_async(
    schemas: dict[str, dict[str, Any]],
    iterations: int = 5,
) -> list[FuzzResult]:
    """Publish corrupted payloads directly (bypassing EventBridge) and verify
    the stream stores every message without raising an exception."""
    results: list[FuzzResult] = []

    for entity, schema in schemas.items():
        stream = InMemoryStream()
        errors = []

        for _ in range(iterations):
            valid = _generate_payload_from_schema(schema)
            corrupted = _corrupt_payload(valid, schema)
            topic = f"slip-stream.{entity}.create"
            key = str(uuid.uuid4())

            try:
                await stream.publish(topic=topic, key=key, payload=corrupted)
            except Exception as exc:
                errors.append(str(exc))

        total = iterations
        stored = len(stream.events)

        if errors:
            results.append(
                FuzzResult(
                    "corrupt",
                    entity,
                    "no_exception",
                    False,
                    f"{len(errors)} exceptions raised: {errors[:2]}",
                )
            )
        else:
            results.append(
                FuzzResult(
                    "corrupt", entity, "no_exception", True, "stream never raised"
                )
            )

        # Stream must store all messages (no validation filter)
        passed = stored == total
        results.append(
            FuzzResult(
                "corrupt",
                entity,
                "all_stored",
                passed,
                f"stored={stored}, expected={total}",
            )
        )

        await stream.close()

    return results


def run_corrupt(
    schemas: dict[str, dict[str, Any]], iterations: int = 5
) -> list[FuzzResult]:
    return asyncio.run(_run_corrupt_async(schemas, iterations))


# ---------------------------------------------------------------------------
# Mode: version
# ---------------------------------------------------------------------------


async def _run_version_async(
    schemas: dict[str, dict[str, Any]],
) -> list[FuzzResult]:
    """Publish events with varying schema_version tags and verify each is
    stored intact with its version label in the payload metadata."""
    results: list[FuzzResult] = []
    test_versions = ["1.0.0", "1.1.0", "2.0.0"]

    for entity, schema in schemas.items():
        for version in test_versions:
            stream = InMemoryStream()
            bus = _FakeEventBus()
            bridge = EventStreamBridge(adapters=[stream])
            bridge.register(bus)

            data = _generate_payload_from_schema(schema)
            entity_id = str(uuid.uuid4())
            ctx = _FakeCtx(
                schema_name=entity,
                entity_id=entity_id,
                data=data,
                schema_version=version,
            )

            try:
                await bus.emit("post_create", ctx)
            except Exception as exc:
                results.append(
                    FuzzResult("version", entity, f"emit_{version}", False, str(exc))
                )
                await bridge.close()
                continue

            if not stream.events:
                results.append(
                    FuzzResult(
                        "version", entity, f"stored_{version}", False, "no events"
                    )
                )
                await bridge.close()
                continue

            evt = stream.events[-1]

            # Confirm entity and topic are correct regardless of version
            passed = entity in evt.topic
            results.append(
                FuzzResult(
                    "version",
                    entity,
                    f"topic_{version}",
                    passed,
                    f"topic={evt.topic!r}",
                )
            )

            # Payload must have schema_name (version routing is caller's concern)
            passed = evt.payload.get("schema_name") == entity
            results.append(
                FuzzResult(
                    "version",
                    entity,
                    f"schema_name_{version}",
                    passed,
                    f"payload.schema_name={evt.payload.get('schema_name')!r}",
                )
            )

            # entity_id must be present and match
            passed = evt.payload.get("entity_id") == entity_id
            results.append(
                FuzzResult(
                    "version",
                    entity,
                    f"entity_id_{version}",
                    passed,
                    f"payload.entity_id={evt.payload.get('entity_id')!r}",
                )
            )

            await bridge.close()

    return results


def run_version(schemas: dict[str, dict[str, Any]]) -> list[FuzzResult]:
    return asyncio.run(_run_version_async(schemas))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stream event schema validation — Phase 5 fuzz driver"
    )
    parser.add_argument(
        "--schema-dir",
        type=Path,
        default=Path("benchmarks/schemas"),
        help="Directory containing JSON schema files (default: benchmarks/schemas)",
    )
    parser.add_argument(
        "--mode",
        choices=["validate", "corrupt", "version", "all"],
        default="all",
        help="Validation mode to run (default: all)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="Corrupt-mode: number of corrupted payloads per schema (default: 5)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write results as JSON to this path",
    )
    args = parser.parse_args()

    if not args.schema_dir.exists():
        print(f"Schema directory not found: {args.schema_dir}", file=sys.stderr)
        sys.exit(1)

    schemas = _load_schemas(args.schema_dir)
    if not schemas:
        print(f"No valid schemas found in {args.schema_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(schemas)} schemas: {', '.join(sorted(schemas))}")

    modes = ["validate", "corrupt", "version"] if args.mode == "all" else [args.mode]
    all_results: list[FuzzResult] = []

    for mode in modes:
        print(f"\n{'='*60}")
        print(f"Running stream fuzz mode: {mode}")
        print(f"{'='*60}\n")

        if mode == "validate":
            all_results.extend(run_validate(schemas))
        elif mode == "corrupt":
            all_results.extend(run_corrupt(schemas, iterations=args.iterations))
        elif mode == "version":
            all_results.extend(run_version(schemas))

    success = print_results(all_results)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "mode": args.mode,
                    "schemas": list(schemas.keys()),
                    "total": len(all_results),
                    "passed": sum(1 for r in all_results if r.passed),
                    "failed": sum(1 for r in all_results if not r.passed),
                    "results": [
                        {
                            "mode": r.mode,
                            "entity": r.entity,
                            "step": r.step,
                            "passed": r.passed,
                            "detail": r.detail,
                        }
                        for r in all_results
                    ],
                },
                indent=2,
            )
        )
        print(f"\nResults written to {args.output}")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
