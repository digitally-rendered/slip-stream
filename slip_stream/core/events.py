"""Lifecycle event system for slip-stream.

Provides an async ``EventBus`` for registering and emitting lifecycle hooks
around CRUD operations. Handlers receive the unified ``RequestContext``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Awaitable, Callable, Dict, List

from slip_stream.core.context import RequestContext

logger = logging.getLogger(__name__)

EventHandler = Callable[[RequestContext], Awaitable[None]]
"""Type alias for event handler functions."""

LIFECYCLE_EVENTS = frozenset(
    {
        "pre_create",
        "post_create",
        "pre_get",
        "post_get",
        "pre_list",
        "post_list",
        "pre_update",
        "post_update",
        "pre_delete",
        "post_delete",
    }
)
"""All supported lifecycle event names."""


class HookError(Exception):
    """Raise from a lifecycle hook to abort with a specific HTTP status.

    The endpoint handler will catch this and convert it to an HTTPException.

    Args:
        status_code: HTTP status code.
        detail: Error detail message.
    """

    def __init__(self, status_code: int = 400, detail: str = "") -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class EventBus:
    """Lightweight async event bus for lifecycle hooks.

    Handlers are registered per ``(event_name, schema_name)`` or globally
    with ``schema_name="*"``. Global handlers run before schema-specific ones.

    Usage::

        bus = EventBus()

        @bus.on("post_create")
        async def audit_log(ctx: RequestContext) -> None:
            log.info("Created %s", ctx.schema_name)

        @bus.on("pre_update", schema_name="widget")
        async def validate(ctx: RequestContext) -> None:
            if ctx.data.status == "invalid":
                raise HookError(400, "Invalid status")
    """

    def __init__(self) -> None:
        self._handlers: Dict[tuple[str, str], List[EventHandler]] = defaultdict(list)

    def on(
        self,
        event: str,
        schema_name: str = "*",
    ) -> Callable[[EventHandler], EventHandler]:
        """Decorator to register a handler for a lifecycle event.

        Args:
            event: Event name (e.g., ``"pre_create"``).
            schema_name: Schema to scope to, or ``"*"`` for all schemas.
        """
        if event not in LIFECYCLE_EVENTS:
            raise ValueError(
                f"Unknown event '{event}'. "
                f"Must be one of: {sorted(LIFECYCLE_EVENTS)}"
            )

        def decorator(fn: EventHandler) -> EventHandler:
            self._handlers[(event, schema_name)].append(fn)
            return fn

        return decorator

    def register(
        self,
        event: str,
        handler: EventHandler,
        schema_name: str = "*",
    ) -> None:
        """Register a handler imperatively (non-decorator form).

        Args:
            event: Event name.
            handler: Async callable receiving ``RequestContext``.
            schema_name: Schema to scope to, or ``"*"`` for all.
        """
        if event not in LIFECYCLE_EVENTS:
            raise ValueError(
                f"Unknown event '{event}'. "
                f"Must be one of: {sorted(LIFECYCLE_EVENTS)}"
            )
        self._handlers[(event, schema_name)].append(handler)

    async def emit(self, event: str, ctx: RequestContext) -> None:
        """Emit an event, running all matching handlers in registration order.

        Global handlers (``schema_name="*"``) run first, then schema-specific.

        Args:
            event: The lifecycle event name.
            ctx: The RequestContext for this request.
        """
        # Global handlers first
        for handler in self._handlers.get((event, "*"), []):
            await handler(ctx)

        # Schema-specific handlers
        if ctx.schema_name != "*":
            for handler in self._handlers.get((event, ctx.schema_name), []):
                await handler(ctx)

    @property
    def handler_count(self) -> int:
        """Total number of registered handlers across all events."""
        return sum(len(handlers) for handlers in self._handlers.values())
