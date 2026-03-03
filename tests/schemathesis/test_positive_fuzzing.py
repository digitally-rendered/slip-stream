"""Positive fuzzing: generate valid data for all schema-driven endpoints.

Uses schemathesis to test endpoints with schema-conforming payloads.
Responses must not produce server errors (5xx). Custom hex-architecture
invariant checks (entity_id UUID, record_version, audit timestamps,
no deleted_at) are auto-applied via the registered checks.
"""

import schemathesis
from hypothesis import settings
from schemathesis.checks import not_a_server_error

schema = schemathesis.pytest.from_fixture("api_schema")


@schema.parametrize()
@settings(max_examples=5, deadline=None)
def test_positive_fuzzing(case):
    """Valid data should never produce 5xx server errors."""
    response = case.call()
    case.validate_response(
        response,
        checks=(not_a_server_error,),
    )
