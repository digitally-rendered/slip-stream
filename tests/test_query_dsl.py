"""Tests for the safe query DSL.

Verifies:
- Operator translation (Hasura-style → MongoDB)
- Field validation (only allowed fields pass)
- Injection prevention (no $ operators, no raw MongoDB)
- Text operator safety (regex escaping)
- Logical operators (_and, _or, _not)
- Sort parsing (JSON:API style)
- Schema-derived allowed fields
"""

import pytest

from slip_stream.core.query import (
    QueryDSL,
    QueryValidationError,
    _extract_fields_from_schema,
    parse_sort_param,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WIDGET_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"},
        "price": {"type": "number"},
        "active": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "address": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "zip": {"type": "string"},
            },
        },
    },
}


@pytest.fixture
def dsl():
    return QueryDSL(allowed_fields={"name", "age", "price", "active", "status"})


@pytest.fixture
def schema_dsl():
    return QueryDSL.from_schema(WIDGET_SCHEMA)


# ---------------------------------------------------------------------------
# Basic comparison operators
# ---------------------------------------------------------------------------


class TestComparisonOps:

    def test_eq_shorthand(self, dsl):
        result = dsl.to_mongo({"name": {"_eq": "Alice"}})
        assert result == {"name": "Alice"}

    def test_neq(self, dsl):
        result = dsl.to_mongo({"name": {"_neq": "Bob"}})
        assert result == {"name": {"$ne": "Bob"}}

    def test_gt(self, dsl):
        result = dsl.to_mongo({"age": {"_gt": 18}})
        assert result == {"age": {"$gt": 18}}

    def test_gte(self, dsl):
        result = dsl.to_mongo({"age": {"_gte": 21}})
        assert result == {"age": {"$gte": 21}}

    def test_lt(self, dsl):
        result = dsl.to_mongo({"price": {"_lt": 100.0}})
        assert result == {"price": {"$lt": 100.0}}

    def test_lte(self, dsl):
        result = dsl.to_mongo({"price": {"_lte": 50.0}})
        assert result == {"price": {"$lte": 50.0}}

    def test_shorthand_value(self, dsl):
        """Plain value without operator dict → equality."""
        result = dsl.to_mongo({"name": "Alice"})
        assert result == {"name": "Alice"}


# ---------------------------------------------------------------------------
# Set operators
# ---------------------------------------------------------------------------


class TestSetOps:

    def test_in(self, dsl):
        result = dsl.to_mongo({"status": {"_in": ["active", "pending"]}})
        assert result == {"status": {"$in": ["active", "pending"]}}

    def test_nin(self, dsl):
        result = dsl.to_mongo({"status": {"_nin": ["deleted"]}})
        assert result == {"status": {"$nin": ["deleted"]}}

    def test_in_requires_list(self, dsl):
        with pytest.raises(QueryValidationError, match="requires a list"):
            dsl.to_mongo({"status": {"_in": "active"}})

    def test_nin_requires_list(self, dsl):
        with pytest.raises(QueryValidationError, match="requires a list"):
            dsl.to_mongo({"status": {"_nin": "deleted"}})


# ---------------------------------------------------------------------------
# Text operators
# ---------------------------------------------------------------------------


class TestTextOps:

    def test_contains(self, dsl):
        result = dsl.to_mongo({"name": {"_contains": "ali"}})
        assert result == {"name": {"$regex": "ali", "$options": "i"}}

    def test_startswith(self, dsl):
        result = dsl.to_mongo({"name": {"_startswith": "Al"}})
        assert result == {"name": {"$regex": "^Al"}}

    def test_endswith(self, dsl):
        result = dsl.to_mongo({"name": {"_endswith": "ce"}})
        assert result == {"name": {"$regex": "ce$"}}

    def test_like(self, dsl):
        result = dsl.to_mongo({"name": {"_like": "%alice%"}})
        assert "$regex" in result["name"]

    def test_ilike(self, dsl):
        result = dsl.to_mongo({"name": {"_ilike": "%alice%"}})
        assert result["name"]["$options"] == "i"

    def test_text_requires_string(self, dsl):
        with pytest.raises(QueryValidationError, match="requires a string"):
            dsl.to_mongo({"name": {"_contains": 123}})

    def test_regex_chars_escaped(self, dsl):
        """Special regex chars in user input must be escaped."""
        result = dsl.to_mongo({"name": {"_contains": "a.b*c"}})
        # The dots and stars should be escaped
        regex = result["name"]["$regex"]
        assert r"\." in regex
        assert r"\*" in regex


# ---------------------------------------------------------------------------
# Existence operators
# ---------------------------------------------------------------------------


class TestExistenceOps:

    def test_exists_true(self, dsl):
        result = dsl.to_mongo({"name": {"_exists": True}})
        assert result == {"name": {"$exists": True}}

    def test_exists_false(self, dsl):
        result = dsl.to_mongo({"name": {"_exists": False}})
        assert result == {"name": {"$exists": False}}

    def test_is_null_true(self, dsl):
        result = dsl.to_mongo({"name": {"_is_null": True}})
        assert result == {"name": None}

    def test_is_null_false(self, dsl):
        result = dsl.to_mongo({"name": {"_is_null": False}})
        assert result == {"name": {"$ne": None}}


# ---------------------------------------------------------------------------
# Logical operators
# ---------------------------------------------------------------------------


class TestLogicOps:

    def test_and(self, dsl):
        result = dsl.to_mongo(
            {
                "_and": [
                    {"name": {"_eq": "Alice"}},
                    {"age": {"_gt": 18}},
                ]
            }
        )
        assert "$and" in result
        assert len(result["$and"]) == 2

    def test_or(self, dsl):
        result = dsl.to_mongo(
            {
                "_or": [
                    {"status": {"_eq": "active"}},
                    {"status": {"_eq": "pending"}},
                ]
            }
        )
        assert "$or" in result
        assert len(result["$or"]) == 2

    def test_not(self, dsl):
        result = dsl.to_mongo({"_not": {"status": {"_eq": "deleted"}}})
        assert "$nor" in result

    def test_nested_logic(self, dsl):
        result = dsl.to_mongo(
            {
                "_and": [
                    {"name": {"_eq": "Alice"}},
                    {
                        "_or": [
                            {"age": {"_gt": 18}},
                            {"status": {"_eq": "admin"}},
                        ]
                    },
                ]
            }
        )
        assert "$and" in result
        assert "$or" in result["$and"][1]

    def test_max_depth_exceeded(self, dsl):
        """Deeply nested queries should be rejected."""
        deep = {"name": {"_eq": "x"}}
        for _ in range(10):
            deep = {"_and": [deep]}
        with pytest.raises(QueryValidationError, match="depth"):
            dsl.to_mongo(deep)


# ---------------------------------------------------------------------------
# Multiple operators on same field
# ---------------------------------------------------------------------------


class TestMultipleOps:

    def test_range_filter(self, dsl):
        result = dsl.to_mongo({"age": {"_gte": 18, "_lte": 65}})
        assert result == {"age": {"$gte": 18, "$lte": 65}}

    def test_combined_comparison_and_existence(self, dsl):
        result = dsl.to_mongo({"age": {"_gt": 0, "_exists": True}})
        assert "$gt" in result["age"]
        assert "$exists" in result["age"]


# ---------------------------------------------------------------------------
# Field validation & injection prevention
# ---------------------------------------------------------------------------


class TestFieldValidation:

    def test_unknown_field_rejected(self, dsl):
        with pytest.raises(QueryValidationError, match="not filterable"):
            dsl.to_mongo({"unknown_field": {"_eq": "x"}})

    def test_dollar_field_rejected(self, dsl):
        with pytest.raises(QueryValidationError, match="must not start with"):
            dsl.to_mongo({"$where": "1==1"})

    def test_unknown_operator_rejected(self, dsl):
        with pytest.raises(QueryValidationError, match="Unknown operator"):
            dsl.to_mongo({"name": {"_badop": "x"}})

    def test_framework_fields_always_allowed(self, dsl):
        """created_at, entity_id, etc. should always be filterable."""
        result = dsl.to_mongo({"created_at": {"_gt": "2024-01-01"}})
        assert result == {"created_at": {"$gt": "2024-01-01"}}

    def test_empty_where_returns_empty(self, dsl):
        assert dsl.to_mongo(None) == {}
        assert dsl.to_mongo({}) == {}

    def test_mongo_operator_in_field_name(self, dsl):
        with pytest.raises(QueryValidationError):
            dsl.to_mongo({"$gt": 1})

    def test_double_dot_rejected(self, dsl):
        with pytest.raises(QueryValidationError, match="Invalid field path"):
            dsl.to_mongo({"name..x": {"_eq": "y"}})


# ---------------------------------------------------------------------------
# Schema-derived fields
# ---------------------------------------------------------------------------


class TestSchemaExtraction:

    def test_extracts_scalar_fields(self):
        fields = _extract_fields_from_schema(WIDGET_SCHEMA)
        assert "name" in fields
        assert "age" in fields
        assert "price" in fields
        assert "active" in fields

    def test_skips_array_fields(self):
        fields = _extract_fields_from_schema(WIDGET_SCHEMA)
        assert "tags" not in fields

    def test_extracts_nested_object_fields(self):
        fields = _extract_fields_from_schema(WIDGET_SCHEMA)
        assert "address" in fields
        assert "address.city" in fields
        assert "address.zip" in fields

    def test_from_schema_creates_working_dsl(self, schema_dsl):
        result = schema_dsl.to_mongo({"name": {"_eq": "Widget A"}})
        assert result == {"name": "Widget A"}

    def test_from_schema_rejects_unknown_fields(self, schema_dsl):
        with pytest.raises(QueryValidationError, match="not filterable"):
            schema_dsl.to_mongo({"nonexistent": {"_eq": "x"}})

    def test_from_schema_allows_nested_dot_path(self, schema_dsl):
        result = schema_dsl.to_mongo({"address.city": {"_eq": "Toronto"}})
        assert result == {"address.city": "Toronto"}


# ---------------------------------------------------------------------------
# Sort parsing
# ---------------------------------------------------------------------------


class TestSortParsing:

    def test_simple_asc(self, dsl):
        sort = parse_sort_param("name")
        mongo = dsl.to_mongo_sort(sort)
        assert mongo == [("name", 1)]

    def test_desc_prefix(self, dsl):
        sort = parse_sort_param("-created_at")
        mongo = dsl.to_mongo_sort(sort)
        assert mongo == [("created_at", -1)]

    def test_multiple_fields(self, dsl):
        sort = parse_sort_param("-created_at,name")
        mongo = dsl.to_mongo_sort(sort)
        assert mongo == [("created_at", -1), ("name", 1)]

    def test_default_sort(self, dsl):
        """No sort → default created_at desc."""
        mongo = dsl.to_mongo_sort(None)
        assert mongo == [("created_at", -1)]

    def test_invalid_direction(self, dsl):
        with pytest.raises(QueryValidationError, match="Invalid sort direction"):
            dsl.to_mongo_sort([{"field": "name", "direction": "sideways"}])

    def test_sort_validates_fields(self):
        """Sort should reject fields not in allowed set."""
        with pytest.raises(QueryValidationError, match="Cannot sort"):
            parse_sort_param("hacked_field", allowed_fields={"name", "age"})

    def test_empty_sort_string(self):
        assert parse_sort_param("") == []
        assert parse_sort_param(None) == []


# ---------------------------------------------------------------------------
# Integration: complex real-world query
# ---------------------------------------------------------------------------


class TestComplexQueries:

    def test_real_world_query(self, dsl):
        """Simulate a typical frontend filter."""
        where = {
            "_and": [
                {"name": {"_contains": "widget"}},
                {
                    "_or": [
                        {"status": {"_eq": "active"}},
                        {"status": {"_eq": "pending"}},
                    ]
                },
                {"age": {"_gte": 18, "_lte": 65}},
            ]
        }
        result = dsl.to_mongo(where)
        assert "$and" in result
        assert len(result["$and"]) == 3
        # The second element should have $or
        assert "$or" in result["$and"][1]

    def test_empty_where_passes_through(self, dsl):
        assert dsl.to_mongo(None) == {}
        assert dsl.to_mongo({}) == {}
