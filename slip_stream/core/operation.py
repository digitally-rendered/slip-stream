"""Shared domain lifecycle executor for CRUD operations.

Encapsulates the full lifecycle that both REST and GraphQL transports
execute for every CRUD operation:

    pre-hooks → handler override check → default service → post-hooks

The ``OperationExecutor`` does NOT catch ``HookError`` — callers are
responsible for translating it to the appropriate error format for
their transport (e.g. ``HTTPException`` for REST, ``ValueError`` for
GraphQL).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from slip_stream.container import EntityRegistration
    from slip_stream.core.context import RequestContext
    from slip_stream.core.events import EventBus

logger = logging.getLogger(__name__)


def _resolve_handler_override(
    handler_overrides: Dict[str, Any],
    operation: str,
    schema_version: Optional[str] = None,
    channel: Optional[str] = None,
) -> Optional[Any]:
    """Resolve a handler override with decreasing specificity.

    Resolution order:
        1. ``{op}@{version}@channel:{channel}`` — most specific
        2. ``{op}@channel:{channel}`` — channel-specific, any version
        3. ``{op}@{version}`` — version-specific, any channel
        4. ``{op}`` — universal fallback

    Args:
        handler_overrides: The registration's handler_overrides dict.
        operation: CRUD operation name.
        schema_version: Optional schema version from the request.
        channel: Optional transport channel (``"rest"``, ``"graphql"``).

    Returns:
        The handler callable if found, otherwise ``None``.
    """
    # 1. Most specific: version + channel
    if schema_version and channel and channel != "*":
        key = f"{operation}@{schema_version}@channel:{channel}"
        override = handler_overrides.get(key)
        if override is not None:
            return override

    # 2. Channel-specific, any version
    if channel and channel != "*":
        key = f"{operation}@channel:{channel}"
        override = handler_overrides.get(key)
        if override is not None:
            return override

    # 3. Version-specific, any channel
    if schema_version:
        key = f"{operation}@{schema_version}"
        override = handler_overrides.get(key)
        if override is not None:
            return override

    # 4. Universal fallback
    return handler_overrides.get(operation)


class OperationExecutor:
    """Executes a CRUD operation through the full domain lifecycle.

    Used by both REST endpoint_factory and GraphQL resolvers to ensure
    identical behaviour regardless of transport channel.

    Args:
        registration: The resolved ``EntityRegistration`` for the entity.
        event_bus: Optional ``EventBus`` for lifecycle hooks.
    """

    def __init__(
        self,
        registration: "EntityRegistration",
        event_bus: "EventBus | None" = None,
    ) -> None:
        self.registration = registration
        self.event_bus = event_bus

    async def execute_create(self, ctx: "RequestContext") -> Any:
        """Execute a create operation through the full lifecycle.

        Raises:
            HookError: If a pre-hook aborts the operation.
        """
        logger.debug("execute_create: schema=%s", ctx.schema_name)
        if self.event_bus:
            await self.event_bus.emit("pre_create", ctx)

        override = _resolve_handler_override(
            self.registration.handler_overrides,
            "create",
            ctx.schema_version,
            getattr(ctx, "channel", None),
        )
        if override:
            logger.debug("Using handler override for %s.create", ctx.schema_name)
            ctx.result = await override(ctx)
        else:
            repo = self.registration.repository_class(ctx.db)
            service = self.registration.services["create"](repo)
            user_id = (
                ctx.current_user.get("id", "anonymous")
                if ctx.current_user
                else "anonymous"
            )
            ctx.result = await service.execute(data=ctx.data, user_id=user_id)

        if self.event_bus:
            await self.event_bus.emit("post_create", ctx)

        entity_id = getattr(ctx.result, "entity_id", None)
        logger.info("Created %s entity_id=%s", ctx.schema_name, entity_id)
        return ctx.result

    async def execute_get(self, ctx: "RequestContext") -> Any:
        """Execute a get operation through the full lifecycle.

        The caller must hydrate ``ctx.entity`` before calling this.

        Raises:
            HookError: If a pre-hook aborts the operation.
        """
        logger.debug(
            "execute_get: schema=%s entity_id=%s", ctx.schema_name, ctx.entity_id
        )
        if self.event_bus:
            await self.event_bus.emit("pre_get", ctx)

        override = _resolve_handler_override(
            self.registration.handler_overrides,
            "get",
            ctx.schema_version,
            getattr(ctx, "channel", None),
        )
        if override:
            logger.debug("Using handler override for %s.get", ctx.schema_name)
            ctx.result = await override(ctx)
        else:
            ctx.result = ctx.entity

        if self.event_bus:
            await self.event_bus.emit("post_get", ctx)

        return ctx.result

    async def execute_list(self, ctx: "RequestContext") -> Any:
        """Execute a list operation through the full lifecycle.

        Raises:
            HookError: If a pre-hook aborts the operation.
        """
        logger.debug(
            "execute_list: schema=%s skip=%s limit=%s",
            ctx.schema_name,
            ctx.skip,
            ctx.limit,
        )
        if self.event_bus:
            await self.event_bus.emit("pre_list", ctx)

        override = _resolve_handler_override(
            self.registration.handler_overrides,
            "list",
            ctx.schema_version,
            getattr(ctx, "channel", None),
        )
        if override:
            logger.debug("Using handler override for %s.list", ctx.schema_name)
            ctx.result = await override(ctx)
        else:
            repo = self.registration.repository_class(ctx.db)
            service = self.registration.services["list"](repo)
            kwargs: Dict[str, Any] = {"skip": ctx.skip, "limit": ctx.limit}
            if getattr(ctx, "filter_criteria", None):
                kwargs["filter_criteria"] = ctx.filter_criteria
            if getattr(ctx, "sort_by", None):
                kwargs["sort_by"] = ctx.sort_by
            if getattr(ctx, "sort_order", None) is not None:
                kwargs["sort_order"] = ctx.sort_order
            ctx.result = await service.execute(**kwargs)

            # Fetch total count for pagination metadata
            if hasattr(repo, "count_active"):
                try:
                    ctx.total_count = await repo.count_active(
                        filter_criteria=getattr(ctx, "filter_criteria", None),
                    )
                except Exception:
                    logger.warning("count_active failed for %s", ctx.schema_name)

        if self.event_bus:
            await self.event_bus.emit("post_list", ctx)

        return ctx.result

    async def execute_update(self, ctx: "RequestContext") -> Any:
        """Execute an update operation through the full lifecycle.

        The caller must hydrate ``ctx.entity`` before calling this.

        Raises:
            HookError: If a pre-hook aborts the operation.
        """
        logger.debug(
            "execute_update: schema=%s entity_id=%s", ctx.schema_name, ctx.entity_id
        )
        if self.event_bus:
            await self.event_bus.emit("pre_update", ctx)

        override = _resolve_handler_override(
            self.registration.handler_overrides,
            "update",
            ctx.schema_version,
            getattr(ctx, "channel", None),
        )
        if override:
            logger.debug("Using handler override for %s.update", ctx.schema_name)
            ctx.result = await override(ctx)
        else:
            repo = self.registration.repository_class(ctx.db)
            service = self.registration.services["update"](repo)
            user_id = (
                ctx.current_user.get("id", "anonymous")
                if ctx.current_user
                else "anonymous"
            )
            result = await service.execute(
                entity_id=ctx.entity_id,
                data=ctx.data,
                user_id=user_id,
            )
            # If no fields changed, return the current entity unchanged
            ctx.result = result if result is not None else ctx.entity

        if self.event_bus:
            await self.event_bus.emit("post_update", ctx)

        version = getattr(ctx.result, "record_version", None)
        logger.info(
            "Updated %s entity_id=%s record_version=%s",
            ctx.schema_name,
            ctx.entity_id,
            version,
        )
        return ctx.result

    async def execute_delete(self, ctx: "RequestContext") -> Any:
        """Execute a delete operation through the full lifecycle.

        The caller must hydrate ``ctx.entity`` before calling this.

        Raises:
            HookError: If a pre-hook aborts the operation.
        """
        logger.debug(
            "execute_delete: schema=%s entity_id=%s", ctx.schema_name, ctx.entity_id
        )
        if self.event_bus:
            await self.event_bus.emit("pre_delete", ctx)

        override = _resolve_handler_override(
            self.registration.handler_overrides,
            "delete",
            ctx.schema_version,
            getattr(ctx, "channel", None),
        )
        if override:
            logger.debug("Using handler override for %s.delete", ctx.schema_name)
            ctx.result = await override(ctx)
        else:
            repo = self.registration.repository_class(ctx.db)
            service = self.registration.services["delete"](repo)
            user_id = (
                ctx.current_user.get("id", "anonymous")
                if ctx.current_user
                else "anonymous"
            )
            ctx.result = await service.execute(entity_id=ctx.entity_id, user_id=user_id)

        if self.event_bus:
            await self.event_bus.emit("post_delete", ctx)

        logger.info("Deleted %s entity_id=%s", ctx.schema_name, ctx.entity_id)
        return ctx.result
