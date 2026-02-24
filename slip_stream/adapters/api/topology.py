"""App topology introspection endpoint.

Returns the full application structure as JSON — schemas, filters, config —
so that AI agents and dev tools can reason about the running app without
reading source code.

**Security**: This endpoint exposes structural metadata only. It does NOT
expose secrets, database URIs, credentials, or environment variables.

Auto-mounted by :class:`~slip_stream.app.SlipStream` during lifespan startup
at ``GET /_topology``.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from fastapi import APIRouter
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


def create_topology_router(
    container: Any,
    schema_registry: Any,
    filters: Optional[List[Any]] = None,
    api_prefix: str = "/api/v1",
    graphql_enabled: bool = False,
    graphql_prefix: str = "/graphql",
    schema_vending_enabled: bool = False,
    structured_errors: bool = False,
    storage_default: str = "mongo",
) -> APIRouter:
    """Create a router with the ``/_topology`` introspection endpoint.

    Args:
        container: The resolved :class:`~slip_stream.container.EntityContainer`.
        schema_registry: The :class:`~slip_stream.core.schema.registry.SchemaRegistry`.
        filters: The list of active :class:`~slip_stream.adapters.api.filters.base.FilterBase` instances.
        api_prefix: The REST API prefix (e.g. ``/api/v1``).
        graphql_enabled: Whether GraphQL is mounted.
        graphql_prefix: The GraphQL API prefix.
        schema_vending_enabled: Whether schema vending is mounted.
        structured_errors: Whether RFC 7807 error handlers are installed.
        storage_default: The default storage backend (``"mongo"`` or ``"sql"``).
    """
    router = APIRouter(tags=["Topology"])

    @router.get("/_topology", include_in_schema=False)
    async def topology() -> JSONResponse:
        schemas = []
        for reg in container.get_all().values():
            path_name = reg.schema_name.replace("_", "-")
            ops = ("create", "get", "list", "update", "delete")
            schema_info: dict[str, Any] = {
                "name": reg.schema_name,
                "storage_backend": reg.storage_backend,
                "versions": schema_registry.get_all_versions(reg.schema_name),
                "has_custom_handler": {
                    op: op in reg.handler_overrides for op in ops
                },
                "has_custom_repository": not getattr(
                    reg.repository_class, "_is_auto_generated", False
                ),
                "has_custom_controller": reg.controller_factory is not None,
                "endpoints": {
                    "rest": f"{api_prefix}/{path_name}/",
                    "graphql": graphql_enabled,
                },
            }
            schemas.append(schema_info)

        filter_list = []
        if filters:
            for f in sorted(filters, key=lambda x: x.order):
                filter_list.append({
                    "name": type(f).__name__,
                    "order": f.order,
                })

        config = {
            "api_prefix": api_prefix,
            "graphql_enabled": graphql_enabled,
            "graphql_prefix": graphql_prefix,
            "schema_vending_enabled": schema_vending_enabled,
            "structured_errors": structured_errors,
            "storage_default": storage_default,
        }

        return JSONResponse({
            "schemas": schemas,
            "filters": filter_list,
            "config": config,
        })

    return router
