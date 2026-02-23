"""Schemathesis test fixtures for slip-stream sample schemas.

Builds a FastAPI app with sample schema endpoints and provides
schemathesis fixtures for parametrized fuzzing.
"""

import pytest
import schemathesis
from mongomock_motor import AsyncMongoMockClient

from slip_stream.core.schema.registry import SchemaRegistry
from slip_stream.testing.app_builder import build_test_app
from slip_stream.testing.checks import register_hex_checks

# Register custom hex-architecture invariant checks
register_hex_checks()

SAMPLE_SCHEMAS_DIR = __import__("pathlib").Path(__file__).parent.parent / "sample_schemas"


# ---------------------------------------------------------------------------
# Auth hook — injects X-User-ID into every schemathesis request
# ---------------------------------------------------------------------------


@schemathesis.hook("before_call")
def inject_auth_header(_context, case, _kwargs):
    """Inject a dummy X-User-ID header required by get_current_user()."""
    if case.headers is None:
        case.headers = {}
    case.headers["X-User-ID"] = "schemathesis-fuzzer"


# ---------------------------------------------------------------------------
# Module-scoped mock database
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def schemathesis_mock_db():
    """Module-scoped mock MongoDB for schemathesis tests."""
    client = AsyncMongoMockClient()
    return client.get_database("schemathesis_test_db")


# ---------------------------------------------------------------------------
# Full-app fixture with all sample schema endpoints
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def schemathesis_app(schemathesis_mock_db):
    """Build a FastAPI app with all sample schema endpoints."""
    return build_test_app(
        schema_dir=SAMPLE_SCHEMAS_DIR,
        mock_db=schemathesis_mock_db,
    )


# ---------------------------------------------------------------------------
# Override root conftest autouse fixtures to prevent interference
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_schema_registry_override():
    """Override root conftest autouse reset — we manage our own lifecycle."""
    yield


# ---------------------------------------------------------------------------
# Schemathesis schema fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def api_schema(schemathesis_app):
    """Schemathesis schema loaded from the test app."""
    return schemathesis.openapi.from_asgi(
        "/openapi.json",
        app=schemathesis_app,
    )
