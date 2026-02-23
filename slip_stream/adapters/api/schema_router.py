"""Schema-driven router registration for FastAPI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, List, Optional

from fastapi import APIRouter

from slip_stream.adapters.api.endpoint_factory import EndpointFactory
from slip_stream.core.events import EventBus

if TYPE_CHECKING:
    from slip_stream.container import EntityRegistration


def register_schema_endpoints(
    api_router: APIRouter,
    schema_names: List[str],
    get_db: Callable,
    get_current_user: Optional[Callable] = None,
) -> None:
    """Register API endpoints for multiple schemas.

    Args:
        api_router: API router to register endpoints with.
        schema_names: List of schema names to register.
        get_db: FastAPI dependency returning AsyncIOMotorDatabase.
        get_current_user: FastAPI dependency returning user dict.
    """
    for schema_name in schema_names:
        path_name = schema_name.replace("_", "-")

        router = EndpointFactory.create_router(
            schema_name=schema_name,
            prefix=path_name,
            tags=[schema_name.replace("_", " ").title()],
            get_db=get_db,
            get_current_user=get_current_user,
        )

        api_router.include_router(
            router,
            prefix=f"/{path_name}",
            tags=[schema_name.replace("_", " ").title()],
        )


def register_schema_endpoint(
    api_router: APIRouter,
    schema_name: str,
    get_db: Callable,
    get_current_user: Optional[Callable] = None,
    custom_path: Optional[str] = None,
    custom_tags: Optional[List[str]] = None,
) -> None:
    """Register API endpoints for a single schema.

    Args:
        api_router: API router to register endpoints with.
        schema_name: Schema name to register.
        get_db: FastAPI dependency returning AsyncIOMotorDatabase.
        get_current_user: FastAPI dependency returning user dict.
        custom_path: Custom URL path (default: kebab-case of schema_name).
        custom_tags: Custom OpenAPI tags.
    """
    path_name = custom_path or schema_name.replace("_", "-")
    tags = custom_tags or [schema_name.replace("_", " ").title()]

    router = EndpointFactory.create_router(
        schema_name=schema_name,
        prefix=path_name,
        tags=tags,
        get_db=get_db,
        get_current_user=get_current_user,
    )

    api_router.include_router(router, prefix=f"/{path_name}", tags=tags)  # type: ignore[arg-type]


def register_schema_endpoint_from_registration(
    api_router: APIRouter,
    registration: "EntityRegistration",
    get_db: Callable,
    get_current_user: Optional[Callable] = None,
    custom_path: Optional[str] = None,
    custom_tags: Optional[List[str]] = None,
    event_bus: Optional[EventBus] = None,
) -> None:
    """Register API endpoints using a pre-resolved EntityRegistration.

    If the registration carries a custom ``controller_factory``, that callable
    is used to build the router. Otherwise
    ``EndpointFactory.create_router_from_registration()`` is used.

    Args:
        api_router: API router to register endpoints with.
        registration: Pre-resolved EntityRegistration from the container.
        get_db: FastAPI dependency returning AsyncIOMotorDatabase.
        get_current_user: FastAPI dependency returning user dict.
        custom_path: Custom URL path (default: kebab-case of schema_name).
        custom_tags: Custom OpenAPI tags.
    """
    schema_name = registration.schema_name
    path_name = custom_path or schema_name.replace("_", "-")
    tags = custom_tags or [schema_name.replace("_", " ").title()]

    if registration.controller_factory is not None:
        router = registration.controller_factory(registration)
    else:
        router = EndpointFactory.create_router_from_registration(
            registration=registration,
            prefix=path_name,
            tags=tags,
            get_db=get_db,
            get_current_user=get_current_user,
            event_bus=event_bus,
        )

    api_router.include_router(router, prefix=f"/{path_name}", tags=tags)  # type: ignore[arg-type]
