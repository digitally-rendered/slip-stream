"""Benchmark petstore app for slip-stream performance testing.

Run with:
    BENCH_PORT=8100 poetry run uvicorn benchmarks.app:app --port 8100

Environment variables:
    BENCH_MONGO_URI   — MongoDB connection string (default: mongodb://localhost:27017)
    BENCH_DB_NAME     — Database name (default: slip_stream_bench)
    BENCH_PORT        — Server port (default: 8100)
    BENCH_SCHEMA_DIR  — Schema directory (default: benchmarks/schemas)
    BENCH_BACKEND     — Storage backend: mongo or sql (default: mongo)
    BENCH_SQL_URL     — SQL connection string for sql backend
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from slip_stream import SlipStream

SCHEMA_DIR = Path(os.environ.get("BENCH_SCHEMA_DIR", Path(__file__).parent / "schemas"))
MONGO_URI = os.environ.get("BENCH_MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("BENCH_DB_NAME", "slip_stream_bench")
BACKEND = os.environ.get("BENCH_BACKEND", "mongo")
SQL_URL = os.environ.get("BENCH_SQL_URL", "")


def create_app() -> FastAPI:
    """Create a minimal benchmark app — no filters, no middleware overhead."""

    async def _noop_user():
        return {"id": "bench", "name": "benchmark"}

    kwargs: dict = {
        "schema_dir": SCHEMA_DIR,
        "api_prefix": "/api/v1",
        "mongo_uri": MONGO_URI,
        "database_name": DB_NAME,
        "structured_errors": True,
        "get_current_user": _noop_user,
    }

    if BACKEND == "sql" and SQL_URL:
        from sqlalchemy.ext.asyncio import create_async_engine

        kwargs["sql_engine"] = create_async_engine(SQL_URL)
        # Route all schemas to SQL
        kwargs["storage_map"] = {
            "pet": "sql",
            "order": "sql",
            "user": "sql",
            "tag": "sql",
            "category": "sql",
        }

    slip = SlipStream(app=FastAPI(), **kwargs)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with slip.lifespan():
            yield

    app = FastAPI(
        title="slip-stream Benchmark",
        version="1.0.0",
        lifespan=lifespan,
    )
    slip.app = app

    return app


app = create_app()
