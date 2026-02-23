"""Structured error handlers for consistent error responses.

When installed, all exceptions produce a uniform JSON structure that flows
through the filter chain — enabling content negotiation on error responses
(YAML/XML errors work automatically).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


def install_error_handlers(app: FastAPI) -> None:
    """Install exception handlers that produce structured error responses.

    Error format::

        {
            "error": {
                "status": 404,
                "detail": "widget not found",
                "type": "HTTPException"
            }
        }
    """

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "status": exc.status_code,
                    "detail": exc.detail,
                    "type": type(exc).__name__,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "status": 422,
                    "detail": "Validation error",
                    "type": "RequestValidationError",
                    "errors": exc.errors(),
                }
            },
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "status": 500,
                    "detail": "Internal server error",
                    "type": type(exc).__name__,
                }
            },
        )
