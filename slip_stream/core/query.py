"""Safe query DSL for slip-stream.

Provides a Hasura-style ``where`` filter pattern that is safe against
injection attacks.  Field names are validated against the JSON schema,
and only allowlisted operators are permitted.  The DSL translates to
MongoDB aggregation ``$match`` stages — MongoDB query language is never
exposed to clients.

Supported operators::

    Comparison : eq, neq, gt, gte, lt, lte
    Set        : in, nin
    Text       : like, ilike, contains, startswith, endswith
    Existence  : exists, is_null
    Logic      : _and, _or, _not

Usage::

    from slip_stream.core.query import QueryDSL

    dsl = QueryDSL(allowed_fields={"name", "age", "status", "created_at"})

    # Parse a where clause
    where = {"name": {"_eq": "Alice"}, "age": {"_gt": 18}}
    mongo_filter = dsl.to_mongo(where)
    # => {"name": "Alice", "age": {"$gt": 18}}

    # Parse sort
    sort = [{"field": "created_at", "direction": "desc"}]
    mongo_sort = dsl.to_mongo_sort(sort)
    # => [("created_at", -1)]
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Operator allowlist — these are the ONLY operators we permit.
# Maps DSL operator name → MongoDB operator.
# ---------------------------------------------------------------------------

_COMPARISON_OPS: dict[str, str] = {
    "_eq": "$eq",
    "_neq": "$ne",
    "_gt": "$gt",
    "_gte": "$gte",
    "_lt": "$lt",
    "_lte": "$lte",
}

_SET_OPS: dict[str, str] = {
    "_in": "$in",
    "_nin": "$nin",
}

_TEXT_OPS: set[str] = {"_like", "_ilike", "_contains", "_startswith", "_endswith"}

_EXISTENCE_OPS: set[str] = {"_exists", "_is_null"}

_LOGIC_OPS: set[str] = {"_and", "_or", "_not"}

_ALL_OPS: set[str] = (
    set(_COMPARISON_OPS) | set(_SET_OPS) | _TEXT_OPS | _EXISTENCE_OPS | _LOGIC_OPS
)

# Fields that are always allowed (framework-level metadata)
_FRAMEWORK_FIELDS: frozenset[str] = frozenset(
    {
        "entity_id",
        "record_version",
        "schema_version",
        "created_at",
        "updated_at",
        "created_by",
        "updated_by",
        "deleted_at",
    }
)

# Maximum nesting depth for logical operators to prevent abuse
_MAX_DEPTH = 8


class QueryValidationError(Exception):
    """Raised when a query DSL expression is invalid."""


class QueryDSL:
    """Parse and validate Hasura-style ``where`` clauses.

    Args:
        allowed_fields: Set of field names that clients may filter on.
            If ``None``, only framework fields are allowed.
        max_depth: Maximum nesting depth for logical operators.
    """

    def __init__(
        self,
        allowed_fields: set[str] | None = None,
        max_depth: int = _MAX_DEPTH,
    ) -> None:
        self._allowed = (allowed_fields or set()) | _FRAMEWORK_FIELDS
        self._max_depth = max_depth

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def to_mongo(self, where: dict[str, Any] | None) -> dict[str, Any]:
        """Convert a ``where`` clause to a MongoDB ``$match`` filter.

        Args:
            where: Hasura-style filter dict, e.g.
                ``{"name": {"_eq": "Alice"}, "age": {"_gt": 18}}``.

        Returns:
            A MongoDB query dict safe for use in ``$match``.

        Raises:
            QueryValidationError: If the query is malformed or uses
                disallowed fields/operators.
        """
        if not where:
            return {}
        return self._parse_where(where, depth=0)

    def to_mongo_sort(self, sort: list[dict[str, str]] | None) -> list[tuple[str, int]]:
        """Convert a sort specification to MongoDB sort tuples.

        Args:
            sort: List of ``{"field": "name", "direction": "asc"|"desc"}``.

        Returns:
            List of ``(field_name, direction)`` tuples for pymongo.
        """
        if not sort:
            return [("created_at", -1)]

        result: list[tuple[str, int]] = []
        for entry in sort:
            field = entry.get("field", "")
            self._validate_field(field)
            direction = entry.get("direction", "asc").lower()
            if direction not in ("asc", "desc"):
                raise QueryValidationError(
                    f"Invalid sort direction: {direction!r} (must be 'asc' or 'desc')"
                )
            result.append((field, 1 if direction == "asc" else -1))
        return result

    @classmethod
    def from_schema(cls, schema: dict[str, Any], **kwargs: Any) -> "QueryDSL":
        """Create a QueryDSL from a JSON Schema, extracting filterable fields.

        Walks the ``properties`` of the schema and collects all scalar
        field names as allowed filter fields.
        """
        fields = _extract_fields_from_schema(schema)
        return cls(allowed_fields=fields, **kwargs)

    # ------------------------------------------------------------------
    # Internal parsing
    # ------------------------------------------------------------------

    def _parse_where(self, where: dict[str, Any], depth: int) -> dict[str, Any]:
        if depth > self._max_depth:
            raise QueryValidationError(
                f"Query nesting depth exceeds maximum ({self._max_depth})"
            )

        result: dict[str, Any] = {}
        and_clauses: list[dict[str, Any]] = []

        for key, value in where.items():
            if key in _LOGIC_OPS:
                parsed = self._parse_logic(key, value, depth)
                result.update(parsed)
            elif key.startswith("_"):
                raise QueryValidationError(f"Unknown operator at top level: {key!r}")
            else:
                # Field-level filter
                self._validate_field(key)
                if isinstance(value, dict):
                    field_filter = self._parse_field_ops(key, value)
                    # Merge field filters — if same field appears in
                    # multiple top-level keys we use $and
                    if key in result:
                        and_clauses.append({key: result.pop(key)})
                        and_clauses.append({key: field_filter})
                    else:
                        result[key] = field_filter
                else:
                    # Shorthand: {"name": "Alice"} → {"name": {"$eq": "Alice"}}
                    result[key] = value

        if and_clauses:
            existing_and = result.pop("$and", [])
            result["$and"] = existing_and + and_clauses

        return result

    def _parse_field_ops(self, field: str, ops: dict[str, Any]) -> Any:
        """Parse operator dict for a single field."""
        if len(ops) == 1:
            op, val = next(iter(ops.items()))
            return self._translate_op(field, op, val)

        # Multiple operators on the same field → combine into single dict
        combined: dict[str, Any] = {}
        for op, val in ops.items():
            translated = self._translate_op(field, op, val)
            if isinstance(translated, dict):
                combined.update(translated)
            else:
                # Scalar result (e.g., _eq returns the value directly)
                # Wrap it
                combined["$eq"] = translated
        return combined

    def _translate_op(self, field: str, op: str, value: Any) -> Any:
        if op not in _ALL_OPS:
            raise QueryValidationError(
                f"Unknown operator {op!r} on field {field!r}. "
                f"Allowed: {sorted(_ALL_OPS)}"
            )

        # --- Comparison ---
        if op in _COMPARISON_OPS:
            if op == "_eq":
                return value  # MongoDB shorthand
            return {_COMPARISON_OPS[op]: value}

        # --- Set ---
        if op in _SET_OPS:
            if not isinstance(value, list):
                raise QueryValidationError(
                    f"Operator {op!r} requires a list value, got {type(value).__name__}"
                )
            return {_SET_OPS[op]: value}

        # --- Text ---
        if op in _TEXT_OPS:
            if not isinstance(value, str):
                raise QueryValidationError(f"Operator {op!r} requires a string value")
            return self._text_op_to_mongo(op, value)

        # --- Existence ---
        if op == "_exists":
            return {"$exists": bool(value)}
        if op == "_is_null":
            if value:
                return None  # field == null
            return {"$ne": None}  # field != null

        raise QueryValidationError(f"Unhandled operator: {op!r}")

    def _text_op_to_mongo(self, op: str, value: str) -> dict[str, Any]:
        """Convert text operators to safe MongoDB regex."""
        # Escape regex special chars to prevent ReDoS
        escaped = re.escape(value)

        if op == "_like":
            # SQL LIKE: % → .*, _ → .
            pattern = escaped.replace(r"\%", ".*").replace(r"\_", ".")
            return {"$regex": f"^{pattern}$"}
        if op == "_ilike":
            pattern = escaped.replace(r"\%", ".*").replace(r"\_", ".")
            return {"$regex": f"^{pattern}$", "$options": "i"}
        if op == "_contains":
            return {"$regex": escaped, "$options": "i"}
        if op == "_startswith":
            return {"$regex": f"^{escaped}"}
        if op == "_endswith":
            return {"$regex": f"{escaped}$"}

        raise QueryValidationError(f"Unhandled text operator: {op!r}")

    def _parse_logic(self, op: str, value: Any, depth: int) -> dict[str, Any]:
        """Parse logical operators (_and, _or, _not)."""
        if op == "_and":
            if not isinstance(value, list):
                raise QueryValidationError("_and requires a list of conditions")
            clauses = [self._parse_where(clause, depth + 1) for clause in value]
            return {"$and": clauses}
        if op == "_or":
            if not isinstance(value, list):
                raise QueryValidationError("_or requires a list of conditions")
            clauses = [self._parse_where(clause, depth + 1) for clause in value]
            return {"$or": clauses}
        if op == "_not":
            if not isinstance(value, dict):
                raise QueryValidationError("_not requires a dict condition")
            inner = self._parse_where(value, depth + 1)
            # MongoDB $not works on field-level, but for top-level
            # negation we use $nor with a single element
            return {"$nor": [inner]}

        raise QueryValidationError(f"Unknown logic operator: {op!r}")

    def _validate_field(self, field: str) -> None:
        """Ensure the field name is allowed and safe."""
        if not field:
            raise QueryValidationError("Empty field name")
        if field.startswith("$"):
            raise QueryValidationError(
                f"Field names must not start with '$': {field!r}"
            )
        if ".." in field:
            raise QueryValidationError(f"Invalid field path: {field!r}")
        # Allow dotted paths for nested fields (e.g., "address.city")
        # but validate each part
        parts = field.split(".")
        root = parts[0]
        if root not in self._allowed:
            raise QueryValidationError(
                f"Field {root!r} is not filterable. "
                f"Allowed fields: {sorted(self._allowed)}"
            )


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------


def _extract_fields_from_schema(schema: dict[str, Any], prefix: str = "") -> set[str]:
    """Extract filterable field names from a JSON Schema."""
    fields: set[str] = set()
    properties = schema.get("properties", {})

    for name, prop_schema in properties.items():
        full_name = name if not prefix else f"{prefix}.{name}"
        prop_type = prop_schema.get("type", "")

        # Skip array and complex nested types for filtering
        if prop_type in ("string", "number", "integer", "boolean"):
            fields.add(full_name)
        elif prop_type == "object" and "properties" in prop_schema:
            # Allow dotted paths for nested objects
            fields.add(full_name)
            nested = _extract_fields_from_schema(prop_schema, full_name)
            fields.update(nested)
        elif not prop_type:
            # Could be a $ref or anyOf — add it as filterable by default
            fields.add(full_name)

    return fields


# ---------------------------------------------------------------------------
# Sort / Pagination helpers
# ---------------------------------------------------------------------------


def parse_sort_param(
    sort_str: str | None, allowed_fields: set[str] | None = None
) -> list[dict[str, str]]:
    """Parse a REST sort string like ``"-created_at,name"`` into sort dicts.

    Uses JSON:API convention: ``-`` prefix means descending.

    Args:
        sort_str: Comma-separated field names, ``-`` prefix for desc.
        allowed_fields: Optional set of valid field names.

    Returns:
        List of ``{"field": "...", "direction": "asc"|"desc"}``.
    """
    if not sort_str:
        return []

    result: list[dict[str, str]] = []
    for part in sort_str.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("-"):
            field = part[1:]
            direction = "desc"
        else:
            field = part
            direction = "asc"

        if (
            allowed_fields
            and field not in allowed_fields
            and field not in _FRAMEWORK_FIELDS
        ):
            raise QueryValidationError(
                f"Cannot sort by {field!r}. Allowed: {sorted(allowed_fields | _FRAMEWORK_FIELDS)}"
            )

        result.append({"field": field, "direction": direction})
    return result
