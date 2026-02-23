"""Schema vending API — serves schema definitions to clients via REST.

Provides a FastAPI router with endpoints for discovering and retrieving
JSON Schema definitions by name and version.  Useful for client SDK
generation, schema-driven validation, and cross-service schema sharing.

Endpoints::

    GET /schemas/                    → list all schema names with versions
    GET /schemas/{name}              → list versions for a schema
    GET /schemas/{name}/latest       → get the latest schema definition
    GET /schemas/{name}/{version}    → get a specific version
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from slip_stream.core.schema.registry import SchemaRegistry


# ------------------------------------------------------------------
# Response models
# ------------------------------------------------------------------


class SchemaNameEntry(BaseModel):
    """Summary of a single schema with available versions."""

    name: str
    versions: list[str]
    latest_version: str


class SchemaListResponse(BaseModel):
    """Response for listing all schemas."""

    schemas: list[SchemaNameEntry]


class SchemaVersionResponse(BaseModel):
    """Response containing a full schema definition."""

    name: str
    version: str
    schema_definition: dict[str, Any] = Field(alias="schema")

    model_config = {"populate_by_name": True}


class SchemaVersionsResponse(BaseModel):
    """Response listing versions of a specific schema."""

    name: str
    versions: list[str]
    latest_version: str


# ------------------------------------------------------------------
# Router factory
# ------------------------------------------------------------------


def create_schema_vending_router(
    schema_registry: SchemaRegistry | None = None,
    prefix: str = "",
) -> APIRouter:
    """Create a FastAPI router that serves schema definitions.

    Args:
        schema_registry: The ``SchemaRegistry`` instance to read from.
            If ``None``, uses the singleton instance.
        prefix: URL prefix for routes (typically ``/schemas``).

    Returns:
        A FastAPI ``APIRouter`` with four endpoints.
    """
    router = APIRouter(prefix=prefix, tags=["Schemas"])

    def _registry() -> SchemaRegistry:
        if schema_registry is not None:
            return schema_registry
        return SchemaRegistry()

    @router.get("/", response_model=SchemaListResponse)
    async def list_schemas() -> SchemaListResponse:
        """List all available schemas with their versions."""
        reg = _registry()
        entries = []
        for name in sorted(reg.get_schema_names()):
            versions = reg.get_all_versions(name)
            latest = reg.get_latest_version(name)
            entries.append(
                SchemaNameEntry(name=name, versions=versions, latest_version=latest)
            )
        return SchemaListResponse(schemas=entries)

    @router.get("/{name}", response_model=SchemaVersionsResponse)
    async def get_schema_versions(name: str) -> SchemaVersionsResponse:
        """List all versions of a specific schema."""
        reg = _registry()
        try:
            versions = reg.get_all_versions(name)
            latest = reg.get_latest_version(name)
        except ValueError:
            raise HTTPException(status_code=404, detail=f"Schema '{name}' not found")
        return SchemaVersionsResponse(
            name=name, versions=versions, latest_version=latest
        )

    @router.get("/{name}/latest", response_model=SchemaVersionResponse)
    async def get_schema_latest(name: str) -> dict[str, Any]:
        """Get the latest version of a schema."""
        reg = _registry()
        try:
            schema = reg.get_schema(name, "latest")
            version = reg.get_latest_version(name)
        except ValueError:
            raise HTTPException(status_code=404, detail=f"Schema '{name}' not found")
        return {"name": name, "version": version, "schema": schema}

    @router.get("/{name}/{version}", response_model=SchemaVersionResponse)
    async def get_schema_version(name: str, version: str) -> dict[str, Any]:
        """Get a specific version of a schema."""
        reg = _registry()
        try:
            schema = reg.get_schema(name, version)
        except ValueError as e:
            detail = str(e)
            if "not found" in detail.lower():
                raise HTTPException(status_code=404, detail=detail)
            raise HTTPException(status_code=400, detail=detail)
        return {"name": name, "version": version, "schema": schema}

    return router
