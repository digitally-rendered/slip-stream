"""Auto-generate filter/sort/pagination types from JSON Schema.

Generates both:
- **Strawberry GraphQL** filter input types (Hasura-style ``where``)
- **FastAPI/Pydantic** query models with full OpenAPI documentation

All types are generated dynamically from JSON Schema properties, so
adding a field to a schema automatically makes it filterable and
sortable — zero boilerplate.

Usage::

    from slip_stream.adapters.api.query_schema import (
        build_graphql_filter_types,
        build_rest_query_model,
    )

    # GraphQL
    WhereInput, OrderByInput = build_graphql_filter_types(schema, "Widget")

    # REST — generates a FastAPI-compatible dependency
    WidgetQuery = build_rest_query_model(schema, "Widget")
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON Schema type → Python type mapping
# ---------------------------------------------------------------------------

_SCHEMA_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


# ---------------------------------------------------------------------------
# REST: Pydantic query model for FastAPI (with OpenAPI docs)
# ---------------------------------------------------------------------------


def build_rest_query_model(
    schema: dict[str, Any],
    name: str,
) -> type[BaseModel]:
    """Build a Pydantic model for REST query parameters.

    The generated model accepts a JSON ``where`` body or individual
    query parameters for simple equality filters, plus ``sort``,
    ``skip``, and ``limit``.

    OpenAPI docs are generated automatically from the schema.

    Args:
        schema: The JSON Schema for the entity.
        name: Human-readable entity name (e.g., ``"Widget"``).

    Returns:
        A Pydantic model class with full OpenAPI annotations.
    """
    properties = schema.get("properties", {})
    filterable_fields = []
    for field_name, prop in properties.items():
        prop_type = prop.get("type", "")
        if prop_type in _SCHEMA_TYPE_MAP:
            filterable_fields.append(field_name)

    # Build operator examples for docs
    operator_docs = (
        "Hasura-style filter object. Supported operators: "
        "_eq, _neq, _gt, _gte, _lt, _lte, _in, _nin, "
        "_like, _ilike, _contains, _startswith, _endswith, "
        "_exists, _is_null. "
        "Logic: _and, _or, _not. "
        f"Filterable fields: {', '.join(sorted(filterable_fields))}. "
        'Example: {"name": {"_eq": "Alice"}, "age": {"_gt": 18}}'
    )

    sort_docs = (
        "Comma-separated sort fields. Prefix with - for descending. "
        f"Sortable fields: {', '.join(sorted(filterable_fields))}. "
        "Example: -created_at,name"
    )

    # Create the model dynamically
    model_fields: dict[str, Any] = {
        "__annotations__": {
            "where": Optional[str],
            "sort": Optional[str],
            "skip": int,
            "limit": int,
        },
        "where": Field(
            default=None,
            description=operator_docs,
            json_schema_extra={"example": '{"name": {"_eq": "Alice"}}'},
        ),
        "sort": Field(
            default=None,
            description=sort_docs,
            json_schema_extra={"example": "-created_at,name"},
        ),
        "skip": Field(default=0, ge=0, description="Number of records to skip."),
        "limit": Field(
            default=100,
            ge=1,
            le=1000,
            description="Maximum number of records to return (1–1000).",
        ),
    }

    model = type(f"{name}QueryParams", (BaseModel,), model_fields)

    # Attach metadata for downstream use
    model._filterable_fields = frozenset(filterable_fields)  # type: ignore[attr-defined]
    model._schema_ref = schema  # type: ignore[attr-defined]

    return model


def parse_rest_where(where_str: str | None) -> dict[str, Any] | None:
    """Parse the ``where`` query parameter from JSON string to dict.

    Returns ``None`` if the string is empty or None.

    Raises:
        ValueError: If the string is not valid JSON or not a dict.
    """
    if not where_str:
        return None
    try:
        parsed = json.loads(where_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in 'where' parameter: {e}") from e
    if not isinstance(parsed, dict):
        raise ValueError("'where' parameter must be a JSON object")
    return parsed


# ---------------------------------------------------------------------------
# GraphQL: Strawberry filter input types
# ---------------------------------------------------------------------------


def build_graphql_filter_types(
    schema: dict[str, Any],
    name: str,
) -> tuple[Any, Any]:
    """Build Strawberry GraphQL filter input types from JSON Schema.

    Returns a ``(WhereInput, OrderByInput)`` tuple of Strawberry input
    types with Hasura-style operator fields.

    Args:
        schema: The JSON Schema for the entity.
        name: Entity name for type naming (e.g., ``"Widget"``).

    Returns:
        Tuple of (WhereInput, OrderByInput) Strawberry input types.

    Raises:
        ImportError: If strawberry is not installed.
    """
    try:
        import strawberry
    except ImportError:
        raise ImportError(
            "strawberry-graphql is required for GraphQL filter types. "
            "Install with: pip install strawberry-graphql"
        ) from None

    properties = schema.get("properties", {})

    # --- Build per-field operator input types ---
    field_inputs: dict[str, Any] = {}
    for field_name, prop in properties.items():
        prop_type = prop.get("type", "")
        python_type = _SCHEMA_TYPE_MAP.get(prop_type)
        if python_type is None:
            continue

        # Create operator input for this field's type
        type_key = prop_type
        if type_key not in field_inputs:
            field_inputs[type_key] = _make_operator_input(name, type_key, python_type)

    # --- Build the WhereInput type ---
    where_annotations: dict[str, Any] = {}
    where_defaults: dict[str, Any] = {}

    for field_name, prop in properties.items():
        prop_type = prop.get("type", "")
        if prop_type in field_inputs:
            op_type = field_inputs[prop_type]
            where_annotations[field_name] = Optional[op_type]
            where_defaults[field_name] = strawberry.UNSET

    # Add logic operators
    # These will be typed as lists of the WhereInput itself (recursive)
    # For simplicity, we use JSON scalars for _and/_or/_not
    where_annotations["_and"] = Optional[list[strawberry.scalars.JSON]]
    where_defaults["_and"] = strawberry.UNSET
    where_annotations["_or"] = Optional[list[strawberry.scalars.JSON]]
    where_defaults["_or"] = strawberry.UNSET
    where_annotations["_not"] = Optional[strawberry.scalars.JSON]
    where_defaults["_not"] = strawberry.UNSET

    where_ns = {"__annotations__": where_annotations}
    where_ns.update(where_defaults)
    WhereInput = strawberry.input(type(f"{name}WhereInput", (), where_ns))

    # --- Build the OrderByInput type ---
    order_ns = {
        "__annotations__": {
            "field": str,
            "direction": Optional[str],
        },
        "field": strawberry.UNSET,
        "direction": "asc",
    }
    OrderByInput = strawberry.input(type(f"{name}OrderByInput", (), order_ns))

    return WhereInput, OrderByInput


def _make_operator_input(
    entity_name: str,
    type_key: str,
    python_type: type,
) -> Any:
    """Create a Strawberry input type with comparison operators for a type."""
    import strawberry

    annotations: dict[str, Any] = {
        "_eq": Optional[python_type],
        "_neq": Optional[python_type],
        "_gt": Optional[python_type],
        "_gte": Optional[python_type],
        "_lt": Optional[python_type],
        "_lte": Optional[python_type],
        "_in": Optional[list[python_type]],  # type: ignore[valid-type]
        "_nin": Optional[list[python_type]],  # type: ignore[valid-type]
        "_exists": Optional[bool],
        "_is_null": Optional[bool],
    }

    # Text operators only for strings
    if python_type is str:
        annotations.update(
            {
                "_like": Optional[str],
                "_ilike": Optional[str],
                "_contains": Optional[str],
                "_startswith": Optional[str],
                "_endswith": Optional[str],
            }
        )

    defaults: dict[str, Any] = {k: strawberry.UNSET for k in annotations}

    ns = {"__annotations__": annotations}
    ns.update(defaults)

    type_name = f"{entity_name}{type_key.capitalize()}Operators"
    return strawberry.input(type(type_name, (), ns))


def strawberry_where_to_dict(where_input: Any) -> dict[str, Any]:
    """Convert a Strawberry WhereInput instance to a plain dict.

    Strips ``UNSET`` values so only provided filters are included.
    """
    import strawberry

    result: dict[str, Any] = {}
    for key in where_input.__class__.__annotations__:
        val = getattr(where_input, key, strawberry.UNSET)
        if val is strawberry.UNSET:
            continue
        if key in ("_and", "_or"):
            # Already JSON scalars (list of dicts)
            result[key] = val
        elif key == "_not":
            result[key] = val
        elif hasattr(val, "__class__") and hasattr(val.__class__, "__annotations__"):
            # Operator input — convert to dict
            op_dict: dict[str, Any] = {}
            for op_key in val.__class__.__annotations__:
                op_val = getattr(val, op_key, strawberry.UNSET)
                if op_val is not strawberry.UNSET:
                    op_dict[op_key] = op_val
            if op_dict:
                result[key] = op_dict
        else:
            result[key] = val
    return result
