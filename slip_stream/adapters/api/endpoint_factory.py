"""API Endpoint Factory for generating FastAPI endpoints from schemas.

NOTE: This module intentionally does NOT use ``from __future__ import annotations``
because FastAPI needs to resolve dynamic Pydantic model types used in route
function signatures at decoration time.
"""

import uuid
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.params import Body
from motor.motor_asyncio import AsyncIOMotorDatabase

from slip_stream.adapters.api.dependencies import default_get_current_user
from slip_stream.adapters.persistence.db.crud_factory import CRUDFactory
from slip_stream.core.context import RequestContext
from slip_stream.core.events import EventBus, HookError
from slip_stream.core.schema.registry import SchemaRegistry

if TYPE_CHECKING:
    from slip_stream.container import EntityRegistration


def _parse_entity_id(entity_id: Union[str, uuid.UUID], schema_name: str) -> uuid.UUID:
    """Parse entity_id to UUID, raising 400 on invalid format."""
    if isinstance(entity_id, uuid.UUID):
        return entity_id
    try:
        return uuid.UUID(entity_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid entity ID format",
        ) from exc


class EndpointFactory:
    """Factory for creating API endpoints from schemas.

    Supports two modes:
        1. ``create_router()`` — direct schema-driven generation (no container).
        2. ``create_router_from_registration()`` — uses a pre-resolved
           EntityRegistration from the container (supports overrides).

    Both modes accept injectable ``get_db`` and ``get_current_user`` dependencies
    so consumers can plug in their own auth and database logic.
    """

    @classmethod
    def create_router(
        cls,
        schema_name: str,
        version: str = "latest",
        prefix: Optional[str] = None,
        tags: Optional[List[str]] = None,
        get_db: Optional[Callable] = None,
        get_current_user: Optional[Callable] = None,
        event_bus: Optional[EventBus] = None,
    ) -> APIRouter:
        """Create a router with CRUD endpoints for a schema.

        Args:
            schema_name: Name of the schema.
            version: Schema version or ``"latest"``.
            prefix: URL prefix (defaults to kebab-case of schema_name).
            tags: OpenAPI tags.
            get_db: FastAPI dependency returning AsyncIOMotorDatabase.
            get_current_user: FastAPI dependency returning user dict.
            event_bus: Optional EventBus for lifecycle hooks.

        Returns:
            A FastAPI router with 5 CRUD endpoints.
        """
        if get_db is None:
            raise ValueError(
                "get_db dependency must be provided. "
                "Pass a FastAPI dependency that returns an AsyncIOMotorDatabase."
            )

        _get_current_user = get_current_user or default_get_current_user

        registry = SchemaRegistry()

        document_model = registry.generate_document_model(schema_name, version)
        create_model = registry.generate_create_model(schema_name, version)
        update_model = registry.generate_update_model(schema_name, version)

        if prefix is None:
            prefix = schema_name.replace("_", "-")

        resolved_tags: Sequence[str] = (
            tags if tags is not None else [schema_name.replace("_", " ").title()]
        )

        router = APIRouter()

        @router.post(
            "/",
            response_model=document_model,
            response_model_by_alias=False,
            status_code=status.HTTP_201_CREATED,
            summary=f"Create a new {schema_name.replace('_', ' ')}",
            tags=list(resolved_tags),
        )
        async def create(
            request: Request,
            data: create_model = Body(...),  # type: ignore[valid-type]
            db: AsyncIOMotorDatabase = Depends(get_db),
            current_user: Dict[str, Any] = Depends(_get_current_user),
        ) -> Any:
            ctx = RequestContext.from_request(
                request=request,
                operation="create",
                schema_name=schema_name,
                data=data,
                current_user=current_user,
                db=db,
            )
            if event_bus:
                try:
                    await event_bus.emit("pre_create", ctx)
                except HookError as e:
                    raise HTTPException(status_code=e.status_code, detail=e.detail) from e

            crud = CRUDFactory.create_crud_instance(db, schema_name, version)
            ctx.result = await crud.create(data=ctx.data, user_id=current_user["id"])

            if event_bus:
                await event_bus.emit("post_create", ctx)

            return ctx.result

        @router.get(
            "/{entity_id}",
            response_model=document_model,
            response_model_by_alias=False,
            summary=f"Get a {schema_name.replace('_', ' ')} by ID",
            tags=list(resolved_tags),
        )
        async def get_by_id(
            request: Request,
            entity_id: Union[str, uuid.UUID],
            db: AsyncIOMotorDatabase = Depends(get_db),
            current_user: Dict[str, Any] = Depends(_get_current_user),
        ) -> Any:
            parsed_id = _parse_entity_id(entity_id, schema_name)
            crud = CRUDFactory.create_crud_instance(db, schema_name, version)

            entity = await crud.get_by_entity_id(entity_id=parsed_id)
            if entity is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"{schema_name.replace('_', ' ')} not found",
                )

            ctx = RequestContext.from_request(
                request=request,
                operation="get",
                schema_name=schema_name,
                entity_id=parsed_id,
                entity=entity,
                current_user=current_user,
                db=db,
            )

            if event_bus:
                try:
                    await event_bus.emit("pre_get", ctx)
                except HookError as e:
                    raise HTTPException(status_code=e.status_code, detail=e.detail) from e

            ctx.result = ctx.entity

            if event_bus:
                await event_bus.emit("post_get", ctx)

            return ctx.result

        @router.get(
            "/",
            response_model=List[document_model],  # type: ignore[valid-type]
            response_model_by_alias=False,
            summary=f"List {schema_name.replace('_', ' ')}s",
            tags=list(resolved_tags),
        )
        async def list_all(
            request: Request,
            skip: int = Query(0, ge=0),
            limit: int = Query(100, ge=1, le=1000),
            db: AsyncIOMotorDatabase = Depends(get_db),
            current_user: Dict[str, Any] = Depends(_get_current_user),
        ) -> Any:
            ctx = RequestContext.from_request(
                request=request,
                operation="list",
                schema_name=schema_name,
                current_user=current_user,
                db=db,
                skip=skip,
                limit=limit,
            )

            if event_bus:
                try:
                    await event_bus.emit("pre_list", ctx)
                except HookError as e:
                    raise HTTPException(status_code=e.status_code, detail=e.detail) from e

            crud = CRUDFactory.create_crud_instance(db, schema_name, version)
            ctx.result = await crud.list_latest_active(skip=ctx.skip, limit=ctx.limit)

            if event_bus:
                await event_bus.emit("post_list", ctx)

            return ctx.result

        @router.patch(
            "/{entity_id}",
            response_model=document_model,
            response_model_by_alias=False,
            summary=f"Update a {schema_name.replace('_', ' ')}",
            tags=list(resolved_tags),
        )
        async def update(
            request: Request,
            entity_id: Union[str, uuid.UUID],
            data: update_model = Body(...),  # type: ignore[valid-type]
            db: AsyncIOMotorDatabase = Depends(get_db),
            current_user: Dict[str, Any] = Depends(_get_current_user),
        ) -> Any:
            parsed_id = _parse_entity_id(entity_id, schema_name)
            crud = CRUDFactory.create_crud_instance(db, schema_name, version)

            entity = await crud.get_by_entity_id(entity_id=parsed_id)
            if entity is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"{schema_name.replace('_', ' ')} not found",
                )

            ctx = RequestContext.from_request(
                request=request,
                operation="update",
                schema_name=schema_name,
                entity_id=parsed_id,
                entity=entity,
                data=data,
                current_user=current_user,
                db=db,
            )

            if event_bus:
                try:
                    await event_bus.emit("pre_update", ctx)
                except HookError as e:
                    raise HTTPException(status_code=e.status_code, detail=e.detail) from e

            ctx.result = await crud.update_by_entity_id(
                entity_id=parsed_id, data=ctx.data, user_id=current_user["id"]
            )

            if event_bus:
                await event_bus.emit("post_update", ctx)

            return ctx.result

        @router.delete(
            "/{entity_id}",
            status_code=status.HTTP_204_NO_CONTENT,
            response_class=Response,
            summary=f"Delete a {schema_name.replace('_', ' ')}",
            tags=list(resolved_tags),
        )
        async def delete(
            request: Request,
            entity_id: Union[str, uuid.UUID],
            db: AsyncIOMotorDatabase = Depends(get_db),
            current_user: Dict[str, Any] = Depends(_get_current_user),
        ) -> None:
            parsed_id = _parse_entity_id(entity_id, schema_name)
            crud = CRUDFactory.create_crud_instance(db, schema_name, version)

            entity = await crud.get_by_entity_id(entity_id=parsed_id)
            if entity is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"{schema_name.replace('_', ' ')} not found",
                )

            ctx = RequestContext.from_request(
                request=request,
                operation="delete",
                schema_name=schema_name,
                entity_id=parsed_id,
                entity=entity,
                current_user=current_user,
                db=db,
            )

            if event_bus:
                try:
                    await event_bus.emit("pre_delete", ctx)
                except HookError as e:
                    raise HTTPException(status_code=e.status_code, detail=e.detail) from e

            await crud.delete_by_entity_id(
                entity_id=parsed_id, user_id=current_user["id"]
            )

            if event_bus:
                await event_bus.emit("post_delete", ctx)

        return router

    @classmethod
    def create_router_from_registration(
        cls,
        registration: "EntityRegistration",
        prefix: Optional[str] = None,
        tags: Optional[List[str]] = None,
        get_db: Optional[Callable] = None,
        get_current_user: Optional[Callable] = None,
        event_bus: Optional[EventBus] = None,
    ) -> APIRouter:
        """Create a router with CRUD endpoints from a resolved EntityRegistration.

        Routes through the container's repository and service classes instead of
        calling CRUDFactory directly. Supports handler overrides and lifecycle hooks.

        Args:
            registration: Pre-resolved EntityRegistration from the container.
            prefix: URL prefix (defaults to kebab-case of schema_name).
            tags: OpenAPI tags.
            get_db: FastAPI dependency returning AsyncIOMotorDatabase.
            get_current_user: FastAPI dependency returning user dict.
            event_bus: Optional EventBus for lifecycle hooks.

        Returns:
            A FastAPI router with 5 CRUD endpoints.
        """
        if get_db is None:
            raise ValueError(
                "get_db dependency must be provided. "
                "Pass a FastAPI dependency that returns an AsyncIOMotorDatabase."
            )

        _get_current_user = get_current_user or default_get_current_user

        schema_name = registration.schema_name
        document_model = registration.document_model
        create_model = registration.create_model
        update_model = registration.update_model
        handler_overrides = registration.handler_overrides

        if prefix is None:
            prefix = schema_name.replace("_", "-")

        resolved_tags: Sequence[str] = (
            tags if tags is not None else [schema_name.replace("_", " ").title()]
        )

        router = APIRouter()

        @router.post(
            "/",
            response_model=document_model,
            response_model_by_alias=False,
            status_code=status.HTTP_201_CREATED,
            summary=f"Create a new {schema_name.replace('_', ' ')}",
            tags=list(resolved_tags),
        )
        async def create(
            request: Request,
            data: create_model = Body(...),  # type: ignore[valid-type]
            db: AsyncIOMotorDatabase = Depends(get_db),
            current_user: Dict[str, Any] = Depends(_get_current_user),
        ) -> Any:
            ctx = RequestContext.from_request(
                request=request,
                operation="create",
                schema_name=schema_name,
                data=data,
                current_user=current_user,
                db=db,
            )

            if event_bus:
                try:
                    await event_bus.emit("pre_create", ctx)
                except HookError as e:
                    raise HTTPException(status_code=e.status_code, detail=e.detail) from e

            override = handler_overrides.get("create")
            if override:
                ctx.result = await override(ctx)
            else:
                repo = registration.repository_class(db)
                service = registration.services["create"](repo)
                ctx.result = await service.execute(data=ctx.data, user_id=current_user["id"])

            if event_bus:
                await event_bus.emit("post_create", ctx)

            return ctx.result

        @router.get(
            "/{entity_id}",
            response_model=document_model,
            response_model_by_alias=False,
            summary=f"Get a {schema_name.replace('_', ' ')} by ID",
            tags=list(resolved_tags),
        )
        async def get_by_id(
            request: Request,
            entity_id: Union[str, uuid.UUID],
            db: AsyncIOMotorDatabase = Depends(get_db),
            current_user: Dict[str, Any] = Depends(_get_current_user),
        ) -> Any:
            parsed_id = _parse_entity_id(entity_id, schema_name)

            # Hydrate entity
            repo = registration.repository_class(db)
            entity = await repo.get_by_entity_id(entity_id=parsed_id)
            if entity is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"{schema_name.replace('_', ' ')} not found",
                )

            ctx = RequestContext.from_request(
                request=request,
                operation="get",
                schema_name=schema_name,
                entity_id=parsed_id,
                entity=entity,
                current_user=current_user,
                db=db,
            )

            if event_bus:
                try:
                    await event_bus.emit("pre_get", ctx)
                except HookError as e:
                    raise HTTPException(status_code=e.status_code, detail=e.detail) from e

            override = handler_overrides.get("get")
            if override:
                ctx.result = await override(ctx)
            else:
                ctx.result = entity

            if event_bus:
                await event_bus.emit("post_get", ctx)

            return ctx.result

        @router.get(
            "/",
            response_model=List[document_model],  # type: ignore[valid-type]
            response_model_by_alias=False,
            summary=f"List {schema_name.replace('_', ' ')}s",
            tags=list(resolved_tags),
        )
        async def list_all(
            request: Request,
            skip: int = Query(0, ge=0),
            limit: int = Query(100, ge=1, le=1000),
            db: AsyncIOMotorDatabase = Depends(get_db),
            current_user: Dict[str, Any] = Depends(_get_current_user),
        ) -> Any:
            ctx = RequestContext.from_request(
                request=request,
                operation="list",
                schema_name=schema_name,
                current_user=current_user,
                db=db,
                skip=skip,
                limit=limit,
            )

            if event_bus:
                try:
                    await event_bus.emit("pre_list", ctx)
                except HookError as e:
                    raise HTTPException(status_code=e.status_code, detail=e.detail) from e

            override = handler_overrides.get("list")
            if override:
                ctx.result = await override(ctx)
            else:
                repo = registration.repository_class(db)
                service = registration.services["list"](repo)
                ctx.result = await service.execute(skip=ctx.skip, limit=ctx.limit)

            if event_bus:
                await event_bus.emit("post_list", ctx)

            return ctx.result

        @router.patch(
            "/{entity_id}",
            response_model=document_model,
            response_model_by_alias=False,
            summary=f"Update a {schema_name.replace('_', ' ')}",
            tags=list(resolved_tags),
        )
        async def update(
            request: Request,
            entity_id: Union[str, uuid.UUID],
            data: update_model = Body(...),  # type: ignore[valid-type]
            db: AsyncIOMotorDatabase = Depends(get_db),
            current_user: Dict[str, Any] = Depends(_get_current_user),
        ) -> Any:
            parsed_id = _parse_entity_id(entity_id, schema_name)

            # Hydrate entity
            repo = registration.repository_class(db)
            entity = await repo.get_by_entity_id(entity_id=parsed_id)
            if entity is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"{schema_name.replace('_', ' ')} not found",
                )

            ctx = RequestContext.from_request(
                request=request,
                operation="update",
                schema_name=schema_name,
                entity_id=parsed_id,
                entity=entity,
                data=data,
                current_user=current_user,
                db=db,
            )

            if event_bus:
                try:
                    await event_bus.emit("pre_update", ctx)
                except HookError as e:
                    raise HTTPException(status_code=e.status_code, detail=e.detail) from e

            override = handler_overrides.get("update")
            if override:
                ctx.result = await override(ctx)
            else:
                service = registration.services["update"](repo)
                ctx.result = await service.execute(
                    entity_id=parsed_id, data=ctx.data, user_id=current_user["id"]
                )

            if event_bus:
                await event_bus.emit("post_update", ctx)

            return ctx.result

        @router.delete(
            "/{entity_id}",
            status_code=status.HTTP_204_NO_CONTENT,
            response_class=Response,
            summary=f"Delete a {schema_name.replace('_', ' ')}",
            tags=list(resolved_tags),
        )
        async def delete(
            request: Request,
            entity_id: Union[str, uuid.UUID],
            db: AsyncIOMotorDatabase = Depends(get_db),
            current_user: Dict[str, Any] = Depends(_get_current_user),
        ) -> None:
            parsed_id = _parse_entity_id(entity_id, schema_name)

            # Hydrate entity
            repo = registration.repository_class(db)
            entity = await repo.get_by_entity_id(entity_id=parsed_id)
            if entity is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"{schema_name.replace('_', ' ')} not found",
                )

            ctx = RequestContext.from_request(
                request=request,
                operation="delete",
                schema_name=schema_name,
                entity_id=parsed_id,
                entity=entity,
                current_user=current_user,
                db=db,
            )

            if event_bus:
                try:
                    await event_bus.emit("pre_delete", ctx)
                except HookError as e:
                    raise HTTPException(status_code=e.status_code, detail=e.detail) from e

            override = handler_overrides.get("delete")
            if override:
                ctx.result = await override(ctx)
            else:
                service = registration.services["delete"](repo)
                await service.execute(
                    entity_id=parsed_id, user_id=current_user["id"]
                )

            if event_bus:
                await event_bus.emit("post_delete", ctx)

        return router
