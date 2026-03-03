"""Benchmarks for schema parsing and model generation.

These are the hot paths that run on every app startup. Track for regressions.

Run: poetry run pytest tests/benchmarks/ -v --benchmark-only
"""

import json
from pathlib import Path

from slip_stream.container import init_container
from slip_stream.core.schema.registry import SchemaRegistry

SAMPLE_SCHEMAS_DIR = Path(__file__).parent.parent / "sample_schemas"


def _make_schema(name: str, num_fields: int = 5) -> dict:
    """Generate a test schema with N user fields."""
    props = {
        "id": {"type": "string", "format": "uuid"},
        "entity_id": {"type": "string", "format": "uuid"},
        "schema_version": {"type": "string"},
        "record_version": {"type": "integer", "default": 1},
    }
    for i in range(num_fields):
        props[f"field_{i}"] = {"type": "string"}
    return {
        "title": name.title(),
        "version": "1.0.0",
        "type": "object",
        "required": ["field_0"],
        "properties": props,
    }


class TestSchemaRegistryBenchmarks:
    """Benchmark schema registration and model generation."""

    def test_register_single_schema(self, benchmark, tmp_path):
        """Time to register a single schema and generate models."""

        def register():
            SchemaRegistry.reset()
            reg = SchemaRegistry(schema_dir=tmp_path)
            reg.register_schema("widget", _make_schema("widget"), version="1.0.0")
            return reg.get_model_for_version("widget")

        result = benchmark(register)
        assert result is not None

    def test_register_10_schemas(self, benchmark, tmp_path):
        """Time to register 10 schemas — simulates a medium-sized app."""

        def register_many():
            SchemaRegistry.reset()
            reg = SchemaRegistry(schema_dir=tmp_path)
            for i in range(10):
                name = f"entity_{i}"
                reg.register_schema(name, _make_schema(name), version="1.0.0")
            return reg.get_schema_names()

        result = benchmark(register_many)
        assert len(result) == 10

    def test_register_50_schemas(self, benchmark, tmp_path):
        """Time to register 50 schemas — simulates a large app."""

        def register_many():
            SchemaRegistry.reset()
            reg = SchemaRegistry(schema_dir=tmp_path)
            for i in range(50):
                name = f"entity_{i}"
                reg.register_schema(name, _make_schema(name), version="1.0.0")
            return reg.get_schema_names()

        result = benchmark(register_many)
        assert len(result) == 50

    def test_schema_with_many_fields(self, benchmark, tmp_path):
        """Time to generate models from a schema with 50 fields."""

        def register_wide():
            SchemaRegistry.reset()
            reg = SchemaRegistry(schema_dir=tmp_path)
            reg.register_schema(
                "wide", _make_schema("wide", num_fields=50), version="1.0.0"
            )
            return reg.get_model_for_version("wide")

        result = benchmark(register_wide)
        assert result is not None

    def test_get_model_for_version_cached(self, benchmark, tmp_path):
        """Time to retrieve already-generated models (cache hit)."""
        SchemaRegistry.reset()
        reg = SchemaRegistry(schema_dir=tmp_path)
        reg.register_schema("widget", _make_schema("widget"), version="1.0.0")
        reg.get_model_for_version("widget")  # warm the cache

        result = benchmark(reg.get_model_for_version, "widget")
        assert result is not None


class TestContainerBenchmarks:
    """Benchmark container initialization."""

    def test_init_container_3_schemas(self, benchmark, tmp_path):
        """Time to initialize the container with sample schemas."""

        def init():
            SchemaRegistry.reset()
            SchemaRegistry(schema_dir=SAMPLE_SCHEMAS_DIR)
            return init_container(["widget", "gadget", "widget_with_ref"])

        container = benchmark(init)
        assert container is not None

    def test_init_container_20_schemas(self, benchmark, tmp_path):
        """Time to initialize the container with 20 schemas."""

        def init():
            SchemaRegistry.reset()
            reg = SchemaRegistry(schema_dir=tmp_path)
            names = []
            for i in range(20):
                name = f"entity_{i}"
                reg.register_schema(name, _make_schema(name), version="1.0.0")
                names.append(name)
            return init_container(names)

        container = benchmark(init)
        assert container is not None


class TestSchemaFileBenchmarks:
    """Benchmark file-based schema loading."""

    def test_load_from_directory(self, benchmark, tmp_path):
        """Time to discover and load schemas from a directory."""
        for i in range(10):
            schema = _make_schema(f"entity_{i}")
            (tmp_path / f"entity_{i}.json").write_text(json.dumps(schema))

        def load():
            SchemaRegistry.reset()
            reg = SchemaRegistry(schema_dir=tmp_path)
            return reg.get_schema_names()

        result = benchmark(load)
        assert len(result) == 10
