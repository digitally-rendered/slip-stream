"""OpenAPI schema helpers for schemathesis compatibility.

Schemathesis works best with OpenAPI 3.0.x. FastAPI generates 3.1.0 by
default. This module provides a helper to downgrade the schema.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


def downgrade_openapi(app: FastAPI) -> dict[str, Any]:
    """Generate an OpenAPI 3.0.3 schema from a FastAPI app.

    This is needed because schemathesis has better support for OpenAPI 3.0.x
    than 3.1.0. The only change is the ``openapi`` version field.

    Args:
        app: A FastAPI application with routes registered.

    Returns:
        An OpenAPI schema dict with version set to ``"3.0.3"``.
    """
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description or "",
        routes=app.routes,
    )

    schema["openapi"] = "3.0.3"

    app.openapi_schema = schema
    return schema
