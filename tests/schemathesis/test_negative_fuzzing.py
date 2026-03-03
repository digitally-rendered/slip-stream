"""Negative fuzzing: send invalid data and ensure no 5xx responses.

Uses schemathesis negative testing to generate schema-violating payloads.
Responses may be 4xx (expected) but must never be 5xx.
"""

import schemathesis
from hypothesis import settings
from schemathesis.checks import not_a_server_error

schema = schemathesis.pytest.from_fixture("api_schema")


@schema.parametrize()
@settings(max_examples=5, deadline=None)
def test_negative_fuzzing(case):
    """Invalid data should produce 4xx, never 5xx."""
    case.body = _corrupt_body(case.body)
    response = case.call()
    case.validate_response(
        response,
        checks=(not_a_server_error,),
    )


def _corrupt_body(body):
    """Mutate body to produce invalid data."""
    if body is None:
        return body

    if isinstance(body, dict):
        corrupted = {}
        for key, value in body.items():
            if isinstance(value, str):
                corrupted[key] = 12345  # wrong type
            elif isinstance(value, int):
                corrupted[key] = "not-a-number"
            elif isinstance(value, list):
                corrupted[key] = "not-a-list"
            else:
                corrupted[key] = value
        # Add an unknown field
        corrupted["__unknown_field__"] = "unexpected"
        return corrupted

    return body
