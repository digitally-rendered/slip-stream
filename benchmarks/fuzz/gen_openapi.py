"""Generate an OpenAPI 3.0.3 spec from JSON Schema files for schemathesis fuzzing.

Handles both slip-stream (flat *.json) and stellar-drive (envelope *.schema.json) formats.

Usage:
    python benchmarks/fuzz/gen_openapi.py --schema-dir benchmarks/schemas --output benchmarks/fuzz/openapi.json
    python benchmarks/fuzz/gen_openapi.py --schema-dir benchmarks/schemas --api-prefix /api/v1
"""

import argparse
import json
import sys
from pathlib import Path

_AUDIT_FIELDS = {
    "id",
    "entity_id",
    "schema_version",
    "record_version",
    "created_at",
    "updated_at",
    "deleted_at",
    "created_by",
    "updated_by",
    "deleted_by",
}

_BASE_RESPONSE_PROPS = {
    "id": {"type": "string", "format": "uuid"},
    "entity_id": {"type": "string", "format": "uuid"},
    "schema_version": {"type": "string"},
    "record_version": {"type": "integer"},
    "created_at": {"type": "string", "format": "date-time"},
    "updated_at": {"type": "string", "format": "date-time"},
    "created_by": {"type": "string"},
    "updated_by": {"type": "string"},
}


def _load_schema(path: Path) -> tuple[str, dict, list[str]]:
    """Load a schema file and return (name, domain_properties, required_fields)."""
    with open(path) as f:
        raw = json.load(f)

    # Detect envelope format (stellar-drive)
    if "schema" in raw and "name" in raw:
        name = raw["name"]
        schema = raw["schema"]
    else:
        # Flat format (slip-stream)
        name = path.stem
        schema = raw

    all_props = schema.get("properties", {})
    required = schema.get("required", [])

    # Strip audit fields
    domain_props = {k: v for k, v in all_props.items() if k not in _AUDIT_FIELDS}
    domain_required = [r for r in required if r not in _AUDIT_FIELDS]

    return name, domain_props, domain_required


def _make_create_schema(name: str, props: dict, required: list[str]) -> dict:
    """Build the Create request body schema."""
    return {
        "type": "object",
        "properties": props,
        "required": required,
    }


def _make_update_schema(name: str, props: dict) -> dict:
    """Build the Update (PATCH) request body schema — all fields optional."""
    return {
        "type": "object",
        "properties": props,
    }


def _make_response_schema(name: str, props: dict) -> dict:
    """Build the response schema (domain fields + audit fields)."""
    all_props = {**_BASE_RESPONSE_PROPS, **props}
    return {
        "type": "object",
        "properties": all_props,
    }


def _make_list_response_schema(ref_name: str) -> dict:
    """Build the list response schema (array of items)."""
    return {
        "type": "array",
        "items": {"$ref": f"#/components/schemas/{ref_name}"},
    }


def _entity_paths(name: str, api_prefix: str) -> dict:
    """Generate CRUD paths for a single entity."""
    kebab = name.replace("_", "-")
    base = f"{api_prefix}/{kebab}"
    item = f"{base}/{{entity_id}}"

    create_ref = f"#/components/schemas/{name}_create"
    update_ref = f"#/components/schemas/{name}_update"
    response_ref = f"#/components/schemas/{name}_response"

    paths = {}

    # POST + GET list
    paths[f"{base}/"] = {
        "post": {
            "operationId": f"create_{name}",
            "summary": f"Create a {name}",
            "tags": [name],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": create_ref},
                    }
                },
            },
            "responses": {
                "201": {
                    "description": "Created",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": response_ref},
                        }
                    },
                },
                "422": {"description": "Validation Error"},
            },
        },
        "get": {
            "operationId": f"list_{name}",
            "summary": f"List {name}s",
            "tags": [name],
            "parameters": [
                {
                    "name": "limit",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "integer", "default": 50},
                },
                {
                    "name": "offset",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "integer", "default": 0},
                },
            ],
            "responses": {
                "200": {
                    "description": "OK",
                    "content": {
                        "application/json": {
                            "schema": _make_list_response_schema(f"{name}_response"),
                        }
                    },
                },
            },
        },
    }

    # GET, PATCH, DELETE by entity_id
    paths[item] = {
        "get": {
            "operationId": f"get_{name}",
            "summary": f"Get a {name} by entity_id",
            "tags": [name],
            "parameters": [
                {
                    "name": "entity_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "format": "uuid"},
                }
            ],
            "responses": {
                "200": {
                    "description": "OK",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": response_ref},
                        }
                    },
                },
                "404": {"description": "Not Found"},
            },
        },
        "patch": {
            "operationId": f"update_{name}",
            "summary": f"Update a {name}",
            "tags": [name],
            "parameters": [
                {
                    "name": "entity_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "format": "uuid"},
                }
            ],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": update_ref},
                    }
                },
            },
            "responses": {
                "200": {
                    "description": "Updated",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": response_ref},
                        }
                    },
                },
                "404": {"description": "Not Found"},
                "422": {"description": "Validation Error"},
            },
        },
        "delete": {
            "operationId": f"delete_{name}",
            "summary": f"Delete a {name}",
            "tags": [name],
            "parameters": [
                {
                    "name": "entity_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "format": "uuid"},
                }
            ],
            "responses": {
                "200": {"description": "Deleted"},
                "204": {"description": "Deleted"},
                "404": {"description": "Not Found"},
            },
        },
    }

    return paths


def generate_openapi(
    schema_dir: Path,
    api_prefix: str = "/api/v1",
    title: str = "Benchmark API",
    version: str = "1.0.0",
) -> dict:
    """Generate a complete OpenAPI 3.0.3 spec from schema files."""
    spec = {
        "openapi": "3.0.3",
        "info": {"title": title, "version": version},
        "paths": {},
        "components": {"schemas": {}},
    }

    # Load all schema files (*.json and *.schema.json)
    schema_files = sorted(schema_dir.glob("*.json"))
    if not schema_files:
        print(f"Warning: no schema files found in {schema_dir}", file=sys.stderr)
        return spec

    for path in schema_files:
        name, domain_props, required = _load_schema(path)

        # Add component schemas
        spec["components"]["schemas"][f"{name}_create"] = _make_create_schema(
            name, domain_props, required
        )
        spec["components"]["schemas"][f"{name}_update"] = _make_update_schema(
            name, domain_props
        )
        spec["components"]["schemas"][f"{name}_response"] = _make_response_schema(
            name, domain_props
        )

        # Add paths
        paths = _entity_paths(name, api_prefix)
        spec["paths"].update(paths)

    return spec


def main():
    parser = argparse.ArgumentParser(
        description="Generate OpenAPI spec from JSON schemas"
    )
    parser.add_argument(
        "--schema-dir",
        type=Path,
        default=Path("benchmarks/schemas"),
        help="Directory containing JSON schema files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmarks/fuzz/openapi.json"),
        help="Output path for the generated spec",
    )
    parser.add_argument(
        "--api-prefix",
        default="/api/v1",
        help="API path prefix (default: /api/v1)",
    )
    parser.add_argument(
        "--title",
        default="Benchmark API",
        help="API title in the spec",
    )
    args = parser.parse_args()

    spec = generate_openapi(args.schema_dir, args.api_prefix, args.title)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(spec, f, indent=2)

    entity_count = len(
        [k for k in spec["components"]["schemas"] if k.endswith("_create")]
    )
    print(
        f"Generated OpenAPI spec: {args.output} ({entity_count} entities, {len(spec['paths'])} paths)"
    )


if __name__ == "__main__":
    main()
