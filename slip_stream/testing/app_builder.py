"""Build a test-ready FastAPI app from slip-stream schemas.

Creates a FastAPI app with all schema-driven endpoints registered,
backed by a mock MongoDB database — ready for schemathesis testing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from slip_stream.adapters.api.schema_router import (
    register_schema_endpoint_from_registration,
)
from slip_stream.container import EntityContainer, init_container
from slip_stream.core.schema.registry import SchemaRegistry
from slip_stream.testing.openapi import downgrade_openapi

logger = logging.getLogger(__name__)


def build_test_app(
    schema_dir: Optional[Path] = None,
    api_prefix: str = "/api/v1",
    mock_db: Any = None,
    get_current_user: Any = None,
    title: str = "slip-stream Test API",
) -> FastAPI:
    """Build a fully-wired FastAPI app for testing.

    Creates a FastAPI app with all schema-driven endpoints backed by a
    mock database. The OpenAPI schema is downgraded to 3.0.3 for
    schemathesis compatibility.

    Args:
        schema_dir: Path to schemas directory. Defaults to auto-discovery.
        api_prefix: URL prefix for endpoints. Defaults to ``"/api/v1"``.
        mock_db: A mock database instance (e.g., from mongomock-motor).
            If not provided, creates one automatically.
        get_current_user: Custom user dependency. If not provided, uses default.
        title: Title for the OpenAPI schema.

    Returns:
        A fully configured FastAPI app with endpoints and middleware.
    """
    if mock_db is None:
        from mongomock_motor import AsyncMongoMockClient
        client = AsyncMongoMockClient()
        mock_db = client.get_database("schemathesis_test_db")

    # Reset registry for clean state
    SchemaRegistry.reset()

    if schema_dir is not None:
        registry = SchemaRegistry(schema_dir=schema_dir)
    else:
        registry = SchemaRegistry()

    schema_names = registry.get_schema_names()
    container = init_container(schema_names)

    app = FastAPI(title=title, version="1.0.0")
    router = APIRouter()

    for name in schema_names:
        path_name = name.replace("_", "-")
        registration = container.get(name)
        register_schema_endpoint_from_registration(
            api_router=router,
            registration=registration,
            get_db=lambda: mock_db,
            get_current_user=get_current_user,
            custom_path=path_name,
            custom_tags=[name.replace("_", " ").title()],
        )

    app.include_router(router, prefix=api_prefix)

    # Build tuple of exceptions that indicate invalid input (not server bugs)
    _validation_errors: tuple[type[Exception], ...] = (
        TypeError, ValueError, OverflowError, KeyError, AttributeError,
    )
    try:
        from bson.errors import InvalidDocument, InvalidId
        _validation_errors = _validation_errors + (InvalidDocument, InvalidId)
    except ImportError:
        pass

    # Error-catching middleware
    @app.middleware("http")
    async def catch_unhandled_errors(request: Request, call_next: Any) -> Any:
        try:
            return await call_next(request)
        except _validation_errors as exc:
            logger.warning("Validation-like error: %s: %s", type(exc).__name__, exc)
            return JSONResponse(
                status_code=422,
                content={
                    "detail": [
                        {
                            "loc": ["body"],
                            "msg": f"{type(exc).__name__}: {exc}",
                            "type": "value_error",
                        }
                    ]
                },
            )
        except Exception as exc:
            logger.error("Unhandled error: %s: %s", type(exc).__name__, exc)
            return JSONResponse(
                status_code=500,
                content={"detail": f"{type(exc).__name__}: {exc}"},
            )

    # Downgrade OpenAPI for schemathesis compatibility
    setattr(app, "openapi", lambda: downgrade_openapi(app))

    return app
