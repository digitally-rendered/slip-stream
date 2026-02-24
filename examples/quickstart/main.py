"""Petstore API — a complete example powered by slip-stream.

Inspired by the OpenAPI Petstore example. Drop JSON schemas for Pet, Order,
and Todo in the schemas/ directory and get full CRUD endpoints automatically.

Run with:
    cd examples/quickstart
    uvicorn main:app --reload

Then visit http://localhost:8000/docs to see the auto-generated CRUD endpoints:
    - /api/v1/pet/         (5 CRUD endpoints)
    - /api/v1/order/       (5 CRUD endpoints)
    - /api/v1/todo/        (5 CRUD endpoints)

Auto-mounted operational endpoints:
    - /health              (liveness probe — always 200)
    - /ready               (readiness — checks DB + schemas)
    - /_topology           (app structure as JSON)
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from slip_stream import SlipStream, ResponseEnvelopeFilter, FieldProjectionFilter

SCHEMAS_DIR = Path(__file__).parent / "schemas"


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    slip = SlipStream(
        app=FastAPI(),  # placeholder, replaced below
        schema_dir=SCHEMAS_DIR,
        api_prefix="/api/v1",
        structured_errors=True,          # RFC 7807 error responses
        filters=[
            ResponseEnvelopeFilter(),    # Wraps in {data, meta} with pagination
            FieldProjectionFilter(),     # Enables ?fields=name,status
        ],
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with slip.lifespan():
            yield

    app = FastAPI(
        title="Petstore API",
        description=(
            "A Petstore API powered by slip-stream. "
            "All endpoints are auto-generated from JSON Schema files."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    slip.app = app

    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "message": "Welcome to the Petstore API",
            "docs": "/docs",
            "endpoints": {
                "pets": "/api/v1/pet/",
                "orders": "/api/v1/order/",
                "todos": "/api/v1/todo/",
            },
            "operational": {
                "health": "/health",
                "ready": "/ready",
                "topology": "/_topology",
            },
        }

    return app


app = create_app()
