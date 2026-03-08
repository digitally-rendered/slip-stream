"""GraphQL schemathesis fuzz runner for slip-stream and stellar-drive.

Language-agnostic: works against any running GraphQL server that implements
the hex CRUD contract (Strawberry/Python or graphql-go or any conformant
implementation). Uses introspection to discover entity types and operations.

Usage:
    python benchmarks/fuzz/run_graphql_fuzz.py --url http://localhost:8100/graphql --mode all
    python benchmarks/fuzz/run_graphql_fuzz.py --url http://localhost:8200/graphql --mode lifecycle
    python benchmarks/fuzz/run_graphql_fuzz.py --url http://localhost:8100/graphql --mode positive --max-examples 50

Modes:
    lifecycle  — CRUD lifecycle per entity (create→get→list→update→delete→verify null)
    positive   — schemathesis parametrized fuzzing via CLI (auto-discovers via introspection)
    negative   — malformed queries/mutations, verify no 500 errors
    all        — runs all three
"""

import argparse
import json
import random
import sys
import uuid
from pathlib import Path
from typing import Any

import requests

# Support running as both `python -m benchmarks.fuzz.run_graphql_fuzz`
# and `python benchmarks/fuzz/run_graphql_fuzz.py`
try:
    from benchmarks.fuzz.run_fuzz import FuzzResult, print_results
except ImportError:
    import importlib.util
    from pathlib import Path as _Path

    _fuzz_dir = _Path(__file__).parent
    _spec = importlib.util.spec_from_file_location(
        "run_fuzz", _fuzz_dir / "run_fuzz.py"
    )
    _mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
    _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
    FuzzResult = _mod.FuzzResult
    print_results = _mod.print_results


# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------

_SCHEMA_INTROSPECTION_QUERY = """
{
  __schema {
    mutationType {
      fields {
        name
        args {
          name
          type {
            name
            kind
            ofType {
              name
              kind
              ofType {
                name
                kind
              }
            }
          }
        }
      }
    }
    queryType {
      fields {
        name
        args {
          name
          type {
            name
            kind
            ofType {
              name
              kind
              ofType {
                name
                kind
              }
            }
          }
        }
      }
    }
  }
}
"""

_TYPE_INTROSPECTION_QUERY = """
query InspectType($name: String!) {
  __type(name: $name) {
    name
    kind
    inputFields {
      name
      type {
        name
        kind
        ofType {
          name
          kind
          ofType {
            name
            kind
          }
        }
      }
    }
    enumValues {
      name
    }
  }
}
"""

# Fallback entity list when introspection cannot determine entities.
# Covers typical petstore schemas used in both projects.
_FALLBACK_ENTITIES = ["pet", "order", "user", "tag", "category"]


def _graphql_request(
    url: str,
    query: str,
    variables: dict[str, Any] | None = None,
    timeout: int = 10,
) -> requests.Response:
    """Send a GraphQL request and return the raw response."""
    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables
    return requests.post(url, json=payload, timeout=timeout)


def _unwrap_type_name(type_node: dict[str, Any]) -> tuple[str | None, bool]:
    """Recursively unwrap NON_NULL/LIST wrappers and return (type_name, is_list).

    GraphQL type nodes are nested: e.g. NON_NULL → LIST → NON_NULL → String.
    Returns the innermost named type and whether a LIST wrapper was encountered.
    """
    is_list = False
    node = type_node
    while node:
        kind = node.get("kind", "")
        if kind == "LIST":
            is_list = True
        if node.get("name"):
            return node["name"], is_list
        node = node.get("ofType") or {}
    return None, is_list


def _introspect_input_type(url: str, type_name: str) -> dict[str, Any]:
    """Fetch field definitions for a named input type via __type introspection."""
    resp = _graphql_request(url, _TYPE_INTROSPECTION_QUERY, {"name": type_name})
    if resp.status_code != 200:
        return {}
    body = resp.json()
    return body.get("data", {}).get("__type") or {}


def _discover_entities(url: str) -> list[str]:
    """Discover entity names by introspecting mutation names matching create{Entity}.

    Returns lower-cased entity names, e.g. ['pet', 'order'].
    Falls back to _FALLBACK_ENTITIES on any failure.
    """
    resp = _graphql_request(url, _SCHEMA_INTROSPECTION_QUERY)
    if resp.status_code != 200:
        return list(_FALLBACK_ENTITIES)

    body = resp.json()
    schema = body.get("data", {}).get("__schema", {})
    mutation_type = schema.get("mutationType") or {}
    fields = mutation_type.get("fields") or []

    entities: list[str] = []
    for field in fields:
        name: str = field.get("name", "")
        if name.startswith("create") and len(name) > 6:
            # "createPet" → "pet"
            entity = name[6:]  # strip leading "create"
            entities.append(entity[0].lower() + entity[1:])

    return entities if entities else list(_FALLBACK_ENTITIES)


def _discover_mutation_args(url: str, mutation_name: str) -> list[dict[str, Any]]:
    """Return the args list for a specific mutation from introspection."""
    resp = _graphql_request(url, _SCHEMA_INTROSPECTION_QUERY)
    if resp.status_code != 200:
        return []

    body = resp.json()
    schema = body.get("data", {}).get("__schema", {})
    mutation_type = schema.get("mutationType") or {}
    for field in mutation_type.get("fields") or []:
        if field.get("name") == mutation_name:
            return field.get("args") or []
    return []


def _discover_query_args(url: str, query_name: str) -> list[dict[str, Any]]:
    """Return the args list for a specific query from introspection."""
    resp = _graphql_request(url, _SCHEMA_INTROSPECTION_QUERY)
    if resp.status_code != 200:
        return []

    body = resp.json()
    schema = body.get("data", {}).get("__schema", {})
    query_type = schema.get("queryType") or {}
    for field in query_type.get("fields") or []:
        if field.get("name") == query_name:
            return field.get("args") or []
    return []


# ---------------------------------------------------------------------------
# Payload generation
# ---------------------------------------------------------------------------


def _generate_scalar_value(
    field_name: str,
    type_name: str | None,
    is_list: bool,
    enum_values: list[str] | None = None,
) -> Any:
    """Generate a plausible test value for a scalar/enum field."""
    if enum_values:
        return enum_values[0]

    name_lower = (field_name or "").lower()
    type_lower = (type_name or "string").lower()

    if is_list:
        inner = _generate_scalar_value(field_name, type_name, False, enum_values)
        return [inner]

    # Derive value from type hint first, then field name hints
    if type_lower in ("int", "integer", "long"):
        return random.randint(1, 100)
    if type_lower in ("float", "double"):
        return round(random.uniform(1.0, 100.0), 2)
    if type_lower in ("boolean", "bool"):
        return True

    # String-based: refine by field name hints
    hex6 = uuid.uuid4().hex[:6]
    if "email" in name_lower:
        return f"test-{hex6}@example.com"
    if "url" in name_lower or "uri" in name_lower:
        return f"https://example.com/{hex6}"
    if "uuid" in name_lower or name_lower.endswith("_id") or name_lower == "id":
        return str(uuid.uuid4())
    if "date" in name_lower or "time" in name_lower or "at" in name_lower:
        return "2026-01-01T00:00:00Z"
    if "status" in name_lower:
        return "active"
    if "name" in name_lower:
        return f"test-{field_name}-{hex6}"

    return f"test-{field_name}-{hex6}"


def _generate_input_from_type(url: str, type_name: str) -> dict[str, Any]:
    """Build a test input dict for a GraphQL input type by introspecting its fields."""
    type_info = _introspect_input_type(url, type_name)
    input_fields = type_info.get("inputFields") or []

    payload: dict[str, Any] = {}
    for field in input_fields:
        fname = field.get("name", "")
        raw_type = field.get("type") or {}
        resolved_name, is_list = _unwrap_type_name(raw_type)

        # Collect enum values if this resolves to an enum type
        enum_values: list[str] | None = None
        if resolved_name:
            nested_info = _introspect_input_type(url, resolved_name)
            if nested_info.get("kind") == "ENUM":
                ev = nested_info.get("enumValues") or []
                enum_values = [e["name"] for e in ev] if ev else None
            elif nested_info.get("kind") == "INPUT_OBJECT":
                # Nested input object — recurse one level
                payload[fname] = _generate_input_from_type(url, resolved_name)
                continue

        payload[fname] = _generate_scalar_value(
            fname, resolved_name, is_list, enum_values
        )

    return payload


def _generate_create_input(url: str, entity: str) -> dict[str, Any]:
    """Generate a create input payload for the given entity.

    Strategy:
    1. Introspect the createEntity mutation args for an 'input' argument.
    2. Introspect that input type's fields.
    3. Fall back to a minimal name-only payload if introspection fails.
    """
    pascal = entity[0].upper() + entity[1:]
    mutation_name = f"create{pascal}"
    args = _discover_mutation_args(url, mutation_name)

    for arg in args:
        if arg.get("name") == "input":
            type_node = arg.get("type") or {}
            input_type_name, _ = _unwrap_type_name(type_node)
            if input_type_name:
                generated = _generate_input_from_type(url, input_type_name)
                if generated:
                    return generated

    # Minimal fallback: most entities have at least a name field
    return {"name": f"test-{entity}-{uuid.uuid4().hex[:6]}"}


# ---------------------------------------------------------------------------
# GraphQL operation builders
# ---------------------------------------------------------------------------


def _pascal(entity: str) -> str:
    return entity[0].upper() + entity[1:]


def _build_create_mutation(entity: str, input_val: dict[str, Any]) -> tuple[str, dict]:
    """Return (query_string, variables) for a create mutation."""
    pascal = _pascal(entity)
    query = f"""
mutation Create{pascal}($input: {pascal}CreateInput!) {{
  create{pascal}(input: $input) {{
    entityId
    recordVersion
    createdAt
    updatedAt
    deletedAt
  }}
}}
"""
    return query, {"input": input_val}


def _build_get_query(entity: str, entity_id: str) -> tuple[str, dict]:
    pascal = _pascal(entity)
    query = f"""
query Get{pascal}($entityId: String!) {{
  get{pascal}(entityId: $entityId) {{
    entityId
    recordVersion
    createdAt
    updatedAt
    deletedAt
  }}
}}
"""
    return query, {"entityId": entity_id}


def _build_list_query(entity: str, limit: int = 10) -> tuple[str, dict]:
    # Pluralise: naive append of 's' — matches the convention used in both projects
    plural = entity + "s"
    plural_pascal = _pascal(plural)
    query = f"""
query List{plural_pascal}($limit: Int) {{
  list{plural_pascal}(limit: $limit) {{
    entityId
    recordVersion
  }}
}}
"""
    return query, {"limit": limit}


def _build_update_mutation(
    entity: str, entity_id: str, update_val: dict[str, Any]
) -> tuple[str, dict]:
    pascal = _pascal(entity)
    query = f"""
mutation Update{pascal}($entityId: String!, $input: {pascal}UpdateInput!) {{
  update{pascal}(entityId: $entityId, input: $input) {{
    entityId
    recordVersion
    updatedAt
  }}
}}
"""
    return query, {"entityId": entity_id, "input": update_val}


def _build_delete_mutation(entity: str, entity_id: str) -> tuple[str, dict]:
    pascal = _pascal(entity)
    query = f"""
mutation Delete{pascal}($entityId: String!) {{
  delete{pascal}(entityId: $entityId) {{
    entityId
    recordVersion
    deletedAt
  }}
}}
"""
    return query, {"entityId": entity_id}


# ---------------------------------------------------------------------------
# Response extraction helpers
# ---------------------------------------------------------------------------


def _extract_gql_data(
    body: dict[str, Any], operation_name: str
) -> dict[str, Any] | None:
    """Extract the named operation data from a GraphQL response body.

    GraphQL responses are: {"data": {"createPet": {...}}, "errors": [...]}
    """
    data = body.get("data") or {}
    return data.get(operation_name)


def _has_gql_errors(body: dict[str, Any]) -> bool:
    errors = body.get("errors")
    return bool(errors)


def _gql_errors_text(body: dict[str, Any]) -> str:
    errors = body.get("errors") or []
    messages = [e.get("message", str(e)) for e in errors[:3]]
    return "; ".join(messages)


# ---------------------------------------------------------------------------
# Lifecycle mode
# ---------------------------------------------------------------------------


def run_lifecycle(url: str) -> list[FuzzResult]:
    """Run CRUD lifecycle tests against each entity discovered via introspection."""
    results: list[FuzzResult] = []
    entities = _discover_entities(url)

    for entity in entities:
        pascal = _pascal(entity)
        input_val = _generate_create_input(url, entity)

        # ---- CREATE -------------------------------------------------------
        query, variables = _build_create_mutation(entity, input_val)
        resp = _graphql_request(url, query, variables)

        if resp.status_code != 200:
            results.append(
                FuzzResult(
                    "lifecycle",
                    entity,
                    "create",
                    False,
                    f"http={resp.status_code} body={resp.text[:200]}",
                )
            )
            continue

        body = resp.json()
        if _has_gql_errors(body):
            results.append(
                FuzzResult(
                    "lifecycle",
                    entity,
                    "create",
                    False,
                    f"gql errors: {_gql_errors_text(body)}",
                )
            )
            continue

        create_data = _extract_gql_data(body, f"create{pascal}")
        if not create_data:
            results.append(
                FuzzResult(
                    "lifecycle",
                    entity,
                    "create",
                    False,
                    f"no data.create{pascal} in response: {json.dumps(body)[:200]}",
                )
            )
            continue

        entity_id = create_data.get("entityId")
        record_version = create_data.get("recordVersion")

        if not entity_id:
            results.append(
                FuzzResult(
                    "lifecycle",
                    entity,
                    "create_entity_id",
                    False,
                    "missing entityId in create response",
                )
            )
            continue

        if record_version != 1:
            results.append(
                FuzzResult(
                    "lifecycle",
                    entity,
                    "create_record_version",
                    False,
                    f"recordVersion={record_version}, expected 1",
                )
            )
        else:
            results.append(
                FuzzResult(
                    "lifecycle",
                    entity,
                    "create",
                    True,
                    f"entityId={entity_id} recordVersion=1",
                )
            )

        # ---- GET ----------------------------------------------------------
        query, variables = _build_get_query(entity, entity_id)
        resp = _graphql_request(url, query, variables)
        body = resp.json() if resp.status_code == 200 else {}
        get_data = _extract_gql_data(body, f"get{pascal}")

        passed = (
            resp.status_code == 200
            and not _has_gql_errors(body)
            and get_data is not None
            and get_data.get("entityId") == entity_id
        )
        detail = (
            f"entityId matches"
            if passed
            else f"http={resp.status_code} errors={_gql_errors_text(body)} data={get_data}"
        )
        results.append(FuzzResult("lifecycle", entity, "get", passed, detail))

        # ---- LIST ---------------------------------------------------------
        query, variables = _build_list_query(entity)
        resp = _graphql_request(url, query, variables)
        body = resp.json() if resp.status_code == 200 else {}

        plural = entity + "s"
        plural_pascal = _pascal(plural)
        list_data = _extract_gql_data(body, f"list{plural_pascal}")

        passed = (
            resp.status_code == 200
            and not _has_gql_errors(body)
            and isinstance(list_data, list)
        )
        # Verify that the just-created entity appears in the list
        if passed and list_data is not None:
            ids_in_list = [item.get("entityId") for item in list_data]
            if entity_id not in ids_in_list:
                results.append(
                    FuzzResult(
                        "lifecycle",
                        entity,
                        "list",
                        False,
                        f"entity {entity_id} not found in list of {len(list_data)}",
                    )
                )
            else:
                results.append(
                    FuzzResult(
                        "lifecycle",
                        entity,
                        "list",
                        True,
                        f"{len(list_data)} items, entity present",
                    )
                )
        else:
            detail = (
                f"http={resp.status_code} errors={_gql_errors_text(body)}"
                if not passed
                else "list_data is None"
            )
            results.append(FuzzResult("lifecycle", entity, "list", passed, detail))

        # ---- UPDATE -------------------------------------------------------
        # Build an update payload: flip the first string field to a new value
        update_val: dict[str, Any] = {}
        for k, v in input_val.items():
            if isinstance(v, str):
                update_val[k] = f"updated-{uuid.uuid4().hex[:6]}"
                break
            if isinstance(v, int):
                update_val[k] = v + 1
                break
        if not update_val:
            # No obvious field; re-send the same input (idempotent update)
            update_val = dict(input_val)

        query, variables = _build_update_mutation(entity, entity_id, update_val)
        resp = _graphql_request(url, query, variables)
        body = resp.json() if resp.status_code == 200 else {}
        update_data = _extract_gql_data(body, f"update{pascal}")

        if resp.status_code == 200 and not _has_gql_errors(body) and update_data:
            rv = update_data.get("recordVersion")
            if rv != 2:
                results.append(
                    FuzzResult(
                        "lifecycle",
                        entity,
                        "update_record_version",
                        False,
                        f"recordVersion={rv}, expected 2",
                    )
                )
            else:
                results.append(
                    FuzzResult(
                        "lifecycle",
                        entity,
                        "update",
                        True,
                        "200, recordVersion=2",
                    )
                )
        else:
            results.append(
                FuzzResult(
                    "lifecycle",
                    entity,
                    "update",
                    False,
                    f"http={resp.status_code} errors={_gql_errors_text(body)} data={update_data}",
                )
            )

        # ---- DELETE -------------------------------------------------------
        query, variables = _build_delete_mutation(entity, entity_id)
        resp = _graphql_request(url, query, variables)
        body = resp.json() if resp.status_code == 200 else {}
        delete_data = _extract_gql_data(body, f"delete{pascal}")

        passed = (
            resp.status_code == 200
            and not _has_gql_errors(body)
            and delete_data is not None
        )
        detail = (
            f"deletedAt={delete_data.get('deletedAt') if delete_data else None}"
            if passed
            else f"http={resp.status_code} errors={_gql_errors_text(body)}"
        )
        results.append(FuzzResult("lifecycle", entity, "delete", passed, detail))

        # ---- GET after DELETE → null --------------------------------------
        query, variables = _build_get_query(entity, entity_id)
        resp = _graphql_request(url, query, variables)
        body = resp.json() if resp.status_code == 200 else {}
        post_delete_data = _extract_gql_data(body, f"get{pascal}")

        # A soft-deleted entity should return null from the GraphQL resolver
        passed = (
            resp.status_code == 200
            and not _has_gql_errors(body)
            and post_delete_data is None
        )
        detail = (
            "get returns null after delete"
            if passed
            else (
                f"expected null, got {post_delete_data}"
                if resp.status_code == 200
                else f"http={resp.status_code}"
            )
        )
        results.append(
            FuzzResult(
                "lifecycle",
                entity,
                "get_after_delete",
                passed,
                detail,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Positive fuzz mode (schemathesis CLI)
# ---------------------------------------------------------------------------


def run_positive_fuzz(url: str, max_examples: int = 20) -> list[FuzzResult]:
    """Run schemathesis CLI against the GraphQL endpoint.

    Schemathesis discovers operations via introspection automatically when
    given a GraphQL URL — no spec file required.
    """
    import shutil
    import subprocess

    results: list[FuzzResult] = []

    st_cmd = shutil.which("schemathesis") or "schemathesis"
    cmd = [
        st_cmd,
        "run",
        url,
        "--checks=not_a_server_error",
        f"--max-examples={max_examples}",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        passed = proc.returncode == 0
        output = proc.stdout + proc.stderr
        lines = output.strip().split("\n")
        summary = next(
            (
                line
                for line in reversed(lines)
                if any(
                    kw in line.lower()
                    for kw in ("passed", "failed", "error", "no tests")
                )
            ),
            lines[-1] if lines else "no output",
        )
        detail = f"exit={proc.returncode}: {summary.strip()}"
        results.append(FuzzResult("positive", "*", "schemathesis", passed, detail))

    except subprocess.TimeoutExpired:
        results.append(
            FuzzResult(
                "positive",
                "*",
                "schemathesis",
                False,
                "timeout after 120s",
            )
        )
    except FileNotFoundError:
        results.append(
            FuzzResult(
                "positive",
                "*",
                "schemathesis",
                False,
                "schemathesis not found — install with: pip install schemathesis",
            )
        )

    return results


# ---------------------------------------------------------------------------
# Negative fuzz mode
# ---------------------------------------------------------------------------


def run_negative_fuzz(url: str) -> list[FuzzResult]:
    """Send malformed GraphQL operations and verify no 500 errors are returned.

    GraphQL servers MUST return HTTP 200 with an 'errors' key for all user
    errors — they should never return 5xx for malformed client input.
    """
    results: list[FuzzResult] = []
    failures: list[str] = []

    # Discover a real entity name to use in probes; fall back gracefully
    entities = _discover_entities(url)
    entity = entities[0] if entities else "pet"
    pascal = _pascal(entity)

    probes: list[tuple[str, dict[str, Any] | None]] = [
        # 1. Type mismatch: string where int expected
        (
            f"""
mutation {{
  create{pascal}(input: {{name: 999, age: "not-an-int"}}) {{
    entityId
  }}
}}
""",
            None,
        ),
        # 2. Non-existent entity_id on get
        (
            f"""
query {{
  get{pascal}(entityId: "00000000-0000-0000-0000-000000000000") {{
    entityId
    recordVersion
  }}
}}
""",
            None,
        ),
        # 3. Create with empty input object
        (
            f"""
mutation {{
  create{pascal}(input: {{}}) {{
    entityId
  }}
}}
""",
            None,
        ),
        # 4. Create with extra unknown fields via variables
        (
            f"""
mutation CreateWithUnknown($input: {pascal}CreateInput!) {{
  create{pascal}(input: $input) {{
    entityId
  }}
}}
""",
            {"input": {"__unknown_field__": "injected", "name": "test"}},
        ),
        # 5. Deeply nested query (attempt depth > 10 — should be rejected or handled)
        (
            f"""
query {{
  get{pascal}(entityId: "x") {{
    entityId
    ... on {pascal} {{
      entityId
      ... on {pascal} {{
        entityId
        ... on {pascal} {{
          entityId
          ... on {pascal} {{
            entityId
            ... on {pascal} {{
              entityId
              ... on {pascal} {{
                entityId
                ... on {pascal} {{
                  entityId
                  ... on {pascal} {{
                    entityId
                    ... on {pascal} {{
                      entityId
                      ... on {pascal} {{
                        entityId
                      }}
                    }}
                  }}
                }}
              }}
            }}
          }}
        }}
      }}
    }}
  }}
}}
""",
            None,
        ),
        # 6. Completely malformed GraphQL syntax
        (
            "this is not graphql { at all } } {",
            None,
        ),
        # 7. Null value for a non-null field
        (
            f"""
mutation {{
  create{pascal}(input: null) {{
    entityId
  }}
}}
""",
            None,
        ),
        # 8. Mutation with integer overflow
        (
            f"""
mutation {{
  create{pascal}(input: {{recordVersion: 99999999999999999999}}) {{
    entityId
  }}
}}
""",
            None,
        ),
    ]

    for i, (query, variables) in enumerate(probes, start=1):
        probe_name = f"probe_{i}"
        try:
            resp = _graphql_request(url, query, variables)
            if resp.status_code >= 500:
                failures.append(
                    f"{probe_name}: HTTP {resp.status_code} — server error on malformed input"
                )
        except requests.exceptions.ConnectionError as exc:
            failures.append(f"{probe_name}: connection error — {exc}")
        except requests.exceptions.Timeout:
            failures.append(f"{probe_name}: timeout")

    total = len(probes)
    passed = len(failures) == 0
    detail = f"{total} malformed probes sent, {len(failures)} server errors"
    if failures:
        detail += f": {failures[:3]}"

    results.append(FuzzResult("negative", "*", "malformed_probes", passed, detail))
    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="GraphQL schemathesis fuzz runner (slip-stream / stellar-drive)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--url",
        required=True,
        help="GraphQL endpoint URL (e.g., http://localhost:8100/graphql)",
    )
    parser.add_argument(
        "--mode",
        choices=["lifecycle", "positive", "negative", "all"],
        default="all",
        help="Fuzz mode to run (default: all)",
    )
    parser.add_argument(
        "--max-examples",
        type=int,
        default=20,
        help="Max examples for positive/negative schemathesis fuzzing (default: 20)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write results as JSON to this file",
    )
    args = parser.parse_args()

    all_results: list[FuzzResult] = []
    modes = ["lifecycle", "positive", "negative"] if args.mode == "all" else [args.mode]

    for mode in modes:
        print(f"\n{'=' * 60}")
        print(f"Running {mode} GraphQL fuzz tests against {args.url}")
        print(f"{'=' * 60}\n")

        if mode == "lifecycle":
            all_results.extend(run_lifecycle(args.url))
        elif mode == "positive":
            all_results.extend(run_positive_fuzz(args.url, args.max_examples))
        elif mode == "negative":
            all_results.extend(run_negative_fuzz(args.url))

    success = print_results(all_results)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(
                {
                    "url": args.url,
                    "mode": args.mode,
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
                f,
                indent=2,
            )
        print(f"\nResults written to {args.output}")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
