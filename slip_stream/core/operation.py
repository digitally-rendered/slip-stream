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

            if getattr(ctx, "pagination_mode", "offset") == "cursor":
                # Cursor-based pagination
                items, page_info = await repo.list_latest_active_cursor(
                    first=getattr(ctx, "first", None),
                    last=getattr(ctx, "last", None),
                    after=getattr(ctx, "after_cursor", None),
                    before=getattr(ctx, "before_cursor", None),
                    sort_by=getattr(ctx, "sort_by", None) or "created_at",
                    sort_order=getattr(ctx, "sort_order", -1),
                    filter_criteria=getattr(ctx, "filter_criteria", None),
                )
                ctx.result = items
                ctx.page_info = page_info
            else:
                # Offset-based pagination (existing)
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

    async def execute_bulk_create(self, ctx: "RequestContext") -> Any:
        """Execute bulk create with per-item lifecycle hooks.

        Emits ``pre_bulk_create`` / ``post_bulk_create`` around the batch,
        and ``pre_create`` / ``post_create`` per item for guards/validators.

        Raises:
            HookError: If a per-item or batch hook aborts the operation.
        """
        from slip_stream.core.bulk import BulkItemResult, BulkOperationResult

        logger.debug(
            "execute_bulk_create: schema=%s items=%s",
            ctx.schema_name,
            len(ctx.bulk_items or []),
        )
        if self.event_bus:
            await self.event_bus.emit("pre_bulk_create", ctx)

        items = ctx.bulk_items or []
        results: list[BulkItemResult] = []
        user_id = (
            ctx.current_user.get("id", "anonymous") if ctx.current_user else "anonymous"
        )

        for i, item in enumerate(items):
            ctx.bulk_index = i
            ctx.data = item
            try:
                if self.event_bus:
                    await self.event_bus.emit("pre_create", ctx)

                override = _resolve_handler_override(
                    self.registration.handler_overrides,
                    "bulk_create",
                    ctx.schema_version,
                    getattr(ctx, "channel", None),
                ) or _resolve_handler_override(
                    self.registration.handler_overrides,
                    "create",
                    ctx.schema_version,
                    getattr(ctx, "channel", None),
                )
                if override:
                    result = await override(ctx)
                else:
                    repo = self.registration.repository_class(ctx.db)
                    service = self.registration.services["create"](repo)
                    result = await service.execute(data=item, user_id=user_id)

                ctx.result = result
                if self.event_bus:
                    await self.event_bus.emit("post_create", ctx)

                results.append(
                    BulkItemResult(
                        index=i,
                        status="success",
                        entity_id=str(getattr(result, "entity_id", "")),
                        record_version=getattr(result, "record_version", None),
                    )
                )
            except Exception as exc:
                status_code = getattr(exc, "status_code", 422)
                detail = getattr(exc, "detail", str(exc))
                results.append(
                    BulkItemResult(
                        index=i,
                        status="error",
                        error=detail,
                        error_code=status_code,
                    )
                )
                if ctx.atomic:
                    from slip_stream.core.events import HookError

                    raise HookError(
                        422, f"Atomic bulk create failed at index {i}: {detail}"
                    ) from exc

        succeeded = sum(1 for r in results if r.status == "success")
        bulk_result = BulkOperationResult(
            total=len(items),
            succeeded=succeeded,
            failed=len(items) - succeeded,
            items=results,
        )
        ctx.result = bulk_result
        ctx.bulk_results = results

        if self.event_bus:
            await self.event_bus.emit("post_bulk_create", ctx)

        logger.info(
            "Bulk created %s: %s/%s succeeded", ctx.schema_name, succeeded, len(items)
        )
        return bulk_result

    async def execute_bulk_update(self, ctx: "RequestContext") -> Any:
        """Execute bulk update with per-item lifecycle hooks.

        Raises:
            HookError: If a per-item or batch hook aborts the operation.
        """
        from slip_stream.core.bulk import BulkItemResult, BulkOperationResult

        logger.debug(
            "execute_bulk_update: schema=%s items=%s",
            ctx.schema_name,
            len(ctx.bulk_items or []),
        )
        if self.event_bus:
            await self.event_bus.emit("pre_bulk_update", ctx)

        items = ctx.bulk_items or []
        results: list[BulkItemResult] = []
        user_id = (
            ctx.current_user.get("id", "anonymous") if ctx.current_user else "anonymous"
        )
        repo = self.registration.repository_class(ctx.db)

        for i, item in enumerate(items):
            ctx.bulk_index = i
            entity_id = (
                item.get("entity_id")
                if isinstance(item, dict)
                else getattr(item, "entity_id", None)
            )
            try:
                import uuid as _uuid

                parsed_id = _uuid.UUID(str(entity_id)) if entity_id else None
                if parsed_id is None:
                    raise ValueError("entity_id is required for bulk update")

                entity = await repo.get_by_entity_id(entity_id=parsed_id)
                if entity is None:
                    raise ValueError(f"Entity {entity_id} not found")

                ctx.entity_id = parsed_id
                ctx.entity = entity

                update_data = {
                    k: v
                    for k, v in (
                        item.items()
                        if isinstance(item, dict)
                        else item.model_dump(exclude_unset=True).items()
                    )
                    if k != "entity_id"
                }
                update_model_cls = self.registration.update_model
                data = update_model_cls(**update_data)
                ctx.data = data

                if self.event_bus:
                    await self.event_bus.emit("pre_update", ctx)

                service = self.registration.services["update"](repo)
                result = await service.execute(
                    entity_id=parsed_id, data=data, user_id=user_id
                )
                result = result if result is not None else entity
                ctx.result = result

                if self.event_bus:
                    await self.event_bus.emit("post_update", ctx)

                results.append(
                    BulkItemResult(
                        index=i,
                        status="success",
                        entity_id=str(getattr(result, "entity_id", "")),
                        record_version=getattr(result, "record_version", None),
                    )
                )
            except Exception as exc:
                status_code = getattr(exc, "status_code", 422)
                detail = getattr(exc, "detail", str(exc))
                results.append(
                    BulkItemResult(
                        index=i,
                        status="error",
                        error=detail,
                        error_code=status_code,
                    )
                )
                if ctx.atomic:
                    from slip_stream.core.events import HookError

                    raise HookError(
                        422, f"Atomic bulk update failed at index {i}: {detail}"
                    ) from exc

        succeeded = sum(1 for r in results if r.status == "success")
        bulk_result = BulkOperationResult(
            total=len(items),
            succeeded=succeeded,
            failed=len(items) - succeeded,
            items=results,
        )
        ctx.result = bulk_result
        ctx.bulk_results = results

        if self.event_bus:
            await self.event_bus.emit("post_bulk_update", ctx)

        logger.info(
            "Bulk updated %s: %s/%s succeeded", ctx.schema_name, succeeded, len(items)
        )
        return bulk_result

    async def execute_bulk_delete(self, ctx: "RequestContext") -> Any:
        """Execute bulk delete with per-item lifecycle hooks.

        Raises:
            HookError: If a per-item or batch hook aborts the operation.
        """
        from slip_stream.core.bulk import BulkItemResult, BulkOperationResult

        logger.debug(
            "execute_bulk_delete: schema=%s items=%s",
            ctx.schema_name,
            len(ctx.bulk_items or []),
        )
        if self.event_bus:
            await self.event_bus.emit("pre_bulk_delete", ctx)

        items = ctx.bulk_items or []
        results: list[BulkItemResult] = []
        user_id = (
            ctx.current_user.get("id", "anonymous") if ctx.current_user else "anonymous"
        )
        repo = self.registration.repository_class(ctx.db)

        for i, entity_id_raw in enumerate(items):
            ctx.bulk_index = i
            try:
                import uuid as _uuid

                parsed_id = _uuid.UUID(str(entity_id_raw))

                entity = await repo.get_by_entity_id(entity_id=parsed_id)
                if entity is None:
                    raise ValueError(f"Entity {entity_id_raw} not found")

                ctx.entity_id = parsed_id
                ctx.entity = entity

                if self.event_bus:
                    await self.event_bus.emit("pre_delete", ctx)

                service = self.registration.services["delete"](repo)
                result = await service.execute(entity_id=parsed_id, user_id=user_id)
                ctx.result = result

                if self.event_bus:
                    await self.event_bus.emit("post_delete", ctx)

                results.append(
                    BulkItemResult(
                        index=i,
                        status="success",
                        entity_id=str(parsed_id),
                        record_version=getattr(result, "record_version", None),
                    )
                )
            except Exception as exc:
                status_code = getattr(exc, "status_code", 422)
                detail = getattr(exc, "detail", str(exc))
                results.append(
                    BulkItemResult(
                        index=i,
                        status="error",
                        error=detail,
                        error_code=status_code,
                    )
                )
                if ctx.atomic:
                    from slip_stream.core.events import HookError

                    raise HookError(
                        422, f"Atomic bulk delete failed at index {i}: {detail}"
                    ) from exc

        succeeded = sum(1 for r in results if r.status == "success")
        bulk_result = BulkOperationResult(
            total=len(items),
            succeeded=succeeded,
            failed=len(items) - succeeded,
            items=results,
        )
        ctx.result = bulk_result
        ctx.bulk_results = results

        if self.event_bus:
            await self.event_bus.emit("post_bulk_delete", ctx)

        logger.info(
            "Bulk deleted %s: %s/%s succeeded", ctx.schema_name, succeeded, len(items)
        )
        return bulk_result
