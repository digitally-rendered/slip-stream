"""Generate an OpenAPI 3.0.3 spec from JSON Schema files for schemathesis fuzzing.

Handles both slip-stream (flat *.json) and stellar-drive (envelope *.schema.json) formats.

Usage:
    python benchmarks/fuzz/gen_openapi.py --schema-dir benchmarks/schemas --output benchmarks/fuzz/openapi.json
    python benchmarks/fuzz/gen_openapi.py --schema-dir benchmarks/schemas --api-prefix /api/v1
    python benchmarks/fuzz/gen_openapi.py --schema-dir benchmarks/schemas --versioned
"""

import argparse
import json
import sys
from collections import defaultdict
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

_X_SCHEMA_VERSION_PARAM = {
    "name": "X-Schema-Version",
    "in": "header",
    "required": False,
    "description": "Requested schema version for response projection",
    "schema": {"type": "string", "example": "2.0.0"},
}


def _logical_name(stem: str) -> str:
    """Strip trailing ``_v<digits>`` version suffixes from a file stem.

    Examples::

        "pet"     -> "pet"
        "pet_v2"  -> "pet"
        "pet_v10" -> "pet"
    """
    import re

    return re.sub(r"_v\d+$", "", stem)


def _load_schema(path: Path) -> tuple[str, str, dict, list[str]]:
    """Load a schema file and return (name, version, domain_properties, required_fields)."""
    with open(path) as f:
        raw = json.load(f)

    # Detect envelope format (stellar-drive)
    if "schema" in raw and "name" in raw:
        name = raw["name"]
        version = raw.get("version", "1.0.0")
        schema = raw["schema"]
    else:
        # Flat format (slip-stream): strip _v<N> suffix so pet_v2.json -> "pet"
        name = _logical_name(path.stem)
        version = raw.get("version", "1.0.0")
        schema = raw

    all_props = schema.get("properties", {})
    required = schema.get("required", [])

    # Strip audit fields
    domain_props = {k: v for k, v in all_props.items() if k not in _AUDIT_FIELDS}
    domain_required = [r for r in required if r not in _AUDIT_FIELDS]

    return name, version, domain_props, domain_required


def _sanitize_version(version: str) -> str:
    """Convert a semver string to a safe identifier component (e.g. '2.0.0' -> '2_0_0')."""
    return version.replace(".", "_")


def _parse_semver(version: str) -> tuple[int, ...]:
    """Parse a semver string into a comparable tuple of ints."""
    parts = []
    for part in version.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


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
    """Generate CRUD paths for a single entity (unversioned)."""
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


def _versioned_entity_paths(
    name: str,
    version: str,
    is_latest: bool,
    api_prefix: str,
) -> dict:
    """Generate CRUD paths for a specific schema version.

    Latest version uses the canonical ``{api_prefix}/{kebab-name}/`` paths.
    Non-latest versions use ``{api_prefix}/{kebab-name}@{version}/`` paths.
    All operations include an ``X-Schema-Version`` header parameter.
    """
    kebab = name.replace("_", "-")
    ver_san = _sanitize_version(version)
    component_prefix = f"{name}_v{ver_san}"

    if is_latest:
        base = f"{api_prefix}/{kebab}"
        op_suffix = name
    else:
        base = f"{api_prefix}/{kebab}@{version}"
        op_suffix = f"{name}_v{ver_san}"

    item = f"{base}/{{entity_id}}"

    create_ref = f"#/components/schemas/{component_prefix}_create"
    update_ref = f"#/components/schemas/{component_prefix}_update"
    response_ref = f"#/components/schemas/{component_prefix}_response"

    header_param = dict(_X_SCHEMA_VERSION_PARAM)

    paths: dict = {}

    # POST + GET list
    paths[f"{base}/"] = {
        "post": {
            "operationId": f"create_{op_suffix}",
            "summary": f"Create a {name} (schema v{version})",
            "tags": [name],
            "parameters": [header_param],
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
            "operationId": f"list_{op_suffix}",
            "summary": f"List {name}s (schema v{version})",
            "tags": [name],
            "parameters": [
                header_param,
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
                            "schema": _make_list_response_schema(
                                f"{component_prefix}_response"
                            ),
                        }
                    },
                },
            },
        },
    }

    # GET, PATCH, DELETE by entity_id
    paths[item] = {
        "get": {
            "operationId": f"get_{op_suffix}",
            "summary": f"Get a {name} by entity_id (schema v{version})",
            "tags": [name],
            "parameters": [
                header_param,
                {
                    "name": "entity_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "format": "uuid"},
                },
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
            "operationId": f"update_{op_suffix}",
            "summary": f"Update a {name} (schema v{version})",
            "tags": [name],
            "parameters": [
                header_param,
                {
                    "name": "entity_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "format": "uuid"},
                },
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
            "operationId": f"delete_{op_suffix}",
            "summary": f"Delete a {name} (schema v{version})",
            "tags": [name],
            "parameters": [
                header_param,
                {
                    "name": "entity_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "format": "uuid"},
                },
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
        name, _ver, domain_props, required = _load_schema(path)

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


def generate_versioned_openapi(
    schema_dir: Path,
    api_prefix: str = "/api/v1",
    title: str = "Benchmark API",
    version: str = "1.0.0",
) -> dict:
    """Generate a versioned OpenAPI 3.0.3 spec from schema files.

    Schemas sharing the same logical name (e.g. ``pet`` and ``pet_v2``) are
    grouped together.  For each (name, schema-version) pair the spec contains:

    - Versioned component schemas:
        ``{name}_v{ver_san}_create`` / ``_update`` / ``_response``
    - Unversioned aliases for the latest version:
        ``{name}_create`` / ``_update`` / ``_response``
    - Versioned paths for non-latest versions:
        ``{api_prefix}/{kebab-name}@{version}/`` and ``…/{entity_id}``
    - Canonical (unversioned) paths for the latest version:
        ``{api_prefix}/{kebab-name}/`` and ``…/{entity_id}``
    - An ``X-Schema-Version`` header parameter on every operation.
    """
    spec = {
        "openapi": "3.0.3",
        "info": {"title": title, "version": version},
        "paths": {},
        "components": {"schemas": {}},
    }

    schema_files = sorted(schema_dir.glob("*.json"))
    if not schema_files:
        print(f"Warning: no schema files found in {schema_dir}", file=sys.stderr)
        return spec

    # Group by logical entity name; a file may carry a different version from
    # another file with the same base name (e.g. pet.json vs pet_v2.json both
    # resolve to entity name "pet" after stripping the stem suffix).
    # Key: logical name  Value: list of (schema_version, domain_props, required)
    groups: dict[str, list[tuple[str, dict, list[str]]]] = defaultdict(list)

    for path in schema_files:
        name, schema_version, domain_props, required = _load_schema(path)
        groups[name].append((schema_version, domain_props, required))

    for entity_name, entries in groups.items():
        # Sort ascending so the highest version is last (= latest)
        entries.sort(key=lambda e: _parse_semver(e[0]))
        latest_version = entries[-1][0]

        for schema_version, domain_props, required in entries:
            ver_san = _sanitize_version(schema_version)
            is_latest = schema_version == latest_version
            component_prefix = f"{entity_name}_v{ver_san}"

            # Versioned component schemas
            spec["components"]["schemas"][f"{component_prefix}_create"] = (
                _make_create_schema(entity_name, domain_props, required)
            )
            spec["components"]["schemas"][f"{component_prefix}_update"] = (
                _make_update_schema(entity_name, domain_props)
            )
            spec["components"]["schemas"][f"{component_prefix}_response"] = (
                _make_response_schema(entity_name, domain_props)
            )

            # Unversioned aliases for the latest version
            if is_latest:
                spec["components"]["schemas"][f"{entity_name}_create"] = {
                    "$ref": f"#/components/schemas/{component_prefix}_create"
                }
                spec["components"]["schemas"][f"{entity_name}_update"] = {
                    "$ref": f"#/components/schemas/{component_prefix}_update"
                }
                spec["components"]["schemas"][f"{entity_name}_response"] = {
                    "$ref": f"#/components/schemas/{component_prefix}_response"
                }

            # Versioned or canonical paths
            paths = _versioned_entity_paths(
                entity_name, schema_version, is_latest, api_prefix
            )
            spec["paths"].update(paths)

    return spec


def main() -> None:
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
    parser.add_argument(
        "--versioned",
        action="store_true",
        default=False,
        help=(
            "Generate a versioned spec that groups schemas by logical name, "
            "emits per-version component schemas, versioned paths for non-latest "
            "versions, and X-Schema-Version header parameters on all operations."
        ),
    )
    args = parser.parse_args()

    if args.versioned:
        spec = generate_versioned_openapi(args.schema_dir, args.api_prefix, args.title)
    else:
        spec = generate_openapi(args.schema_dir, args.api_prefix, args.title)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(spec, f, indent=2)

    entity_count = len(
        [k for k in spec["components"]["schemas"] if k.endswith("_create")]
    )
    mode = "versioned" if args.versioned else "standard"
    print(
        f"Generated {mode} OpenAPI spec: {args.output} "
        f"({entity_count} entity/version pairs, {len(spec['paths'])} paths)"
    )


if __name__ == "__main__":
    main()
