"""RFC 7807 Problem Details error handlers for consistent error responses.

When installed via ``structured_errors=True``, all exceptions produce
`application/problem+json` responses that flow through the filter chain —
enabling content negotiation on error responses (YAML/XML errors work
automatically).

See: https://datatracker.ietf.org/doc/html/rfc7807
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# RFC 7807 error type URI base
ERROR_TYPE_BASE = "https://slip-stream.dev/errors/"

# Maps HTTP status codes to (slug, title) pairs for the ``type`` URI.
_ERROR_TYPES: Dict[int, tuple[str, str]] = {
    400: ("bad-request", "Bad Request"),
    403: ("policy-denied", "Policy Denied"),
    404: ("not-found", "Not Found"),
    409: ("conflict", "Conflict"),
    422: ("validation-error", "Validation Error"),
    429: ("rate-limited", "Rate Limited"),
    500: ("internal-error", "Internal Server Error"),
    503: ("service-unavailable", "Service Unavailable"),
}

PROBLEM_JSON = "application/problem+json"


def _problem_response(
    status: int,
    detail: str,
    instance: str,
    **extra: Any,
) -> JSONResponse:
    """Build an RFC 7807 Problem Details JSONResponse."""
    slug, title = _ERROR_TYPES.get(status, ("error", "Error"))
    body: Dict[str, Any] = {
        "type": f"{ERROR_TYPE_BASE}{slug}",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
    }
    body.update(extra)
    return JSONResponse(
        status_code=status,
        content=body,
        media_type=PROBLEM_JSON,
    )


def install_error_handlers(app: FastAPI) -> None:
    """Install exception handlers that produce RFC 7807 Problem Details.

    Error format::

        {
            "type": "https://slip-stream.dev/errors/not-found",
            "title": "Not Found",
            "status": 404,
            "detail": "widget not found",
            "instance": "/api/v1/widget/abc123"
        }
    """

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return _problem_response(
            status=exc.status_code,
            detail=str(exc.detail),
            instance=request.url.path,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _problem_response(
            status=422,
            detail="Validation error",
            instance=request.url.path,
            errors=exc.errors(),
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        return _problem_response(
            status=500,
            detail="Internal server error",
            instance=request.url.path,
        )
