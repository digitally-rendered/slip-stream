"""Health and readiness probe endpoints.

Auto-mounted by :class:`~slip_stream.app.SlipStream` during lifespan startup.

- ``GET /health`` — liveness probe, always returns 200.
- ``GET /ready``  — readiness probe, checks database connectivity and
  schema registry state.  Returns 503 when not ready.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


def create_health_router(
    db_manager: Any = None,
    schema_registry: Any = None,
) -> APIRouter:
    """Create a router with ``/health`` and ``/ready`` endpoints.

    Args:
        db_manager: The :class:`~slip_stream.database.DatabaseManager` instance,
            or ``None`` if an external ``get_db`` was provided.
        schema_registry: The :class:`~slip_stream.core.schema.registry.SchemaRegistry`
            singleton.
    """
    router = APIRouter(tags=["Health"])

    @router.get("/health", include_in_schema=False)
    async def health() -> JSONResponse:
        return JSONResponse({"status": "healthy"})

    @router.get("/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        checks: dict[str, bool] = {"database": False, "schemas": False}

        # Check database
        if db_manager is not None and db_manager.db is not None:
            try:
                await db_manager.db.command("ping")
                checks["database"] = True
            except Exception:
                logger.warning("Readiness check: database ping failed")
        elif db_manager is None:
            # External get_db provided — assume ready
            checks["database"] = True

        # Check schemas loaded
        if schema_registry is not None:
            checks["schemas"] = len(schema_registry.get_schema_names()) > 0

        all_ready = all(checks.values())
        status_code = 200 if all_ready else 503
        status_text = "ready" if all_ready else "not_ready"

        return JSONResponse(
            {"status": status_text, "checks": checks},
            status_code=status_code,
        )

    return router
