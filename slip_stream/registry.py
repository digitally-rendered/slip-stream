"""Declarative decorator registry for slip-stream handler customization.

``SlipStreamRegistry`` provides a fluent, decorator-based API for registering
handler overrides and lifecycle hooks without relying on file naming conventions.

Usage::

    from slip_stream import SlipStreamRegistry, HookError, RequestContext

    registry = SlipStreamRegistry()

    @registry.handler("widget", "create")
    async def custom_create(ctx: RequestContext) -> Any:
        # Full control over the create operation
        return await my_custom_create(ctx.data, ctx.current_user)

    @registry.guard("widget", "delete")
    async def admins_only(ctx: RequestContext) -> None:
        if ctx.current_user.get("role") != "admin":
            raise HookError(403, "Admin role required")

    @registry.validate("order", "create", "update")
    async def check_dates(ctx: RequestContext) -> None:
        if ctx.data.end_date < ctx.data.start_date:
            raise HookError(422, "end_date must be after start_date")

    @registry.transform("user", "create", "update", when="before")
    async def normalize_email(ctx: RequestContext) -> None:
        if ctx.data.email:
            ctx.data.email = ctx.data.email.lower()

    @registry.on("post_create")
    async def audit_log(ctx: RequestContext) -> None:
        log.info("Created %s %s", ctx.schema_name, ctx.entity_id)

    slip = SlipStream(app=app, schema_dir=..., registry=registry)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from slip_stream.core.events import LIFECYCLE_EVENTS, EventBus

if TYPE_CHECKING:
    from slip_stream.container import EntityContainer
    from slip_stream.core.context import RequestContext

logger = logging.getLogger(__name__)

EventHandler = Callable[["RequestContext"], Awaitable[None]]
"""Type alias for event/hook handler functions."""

_VALID_OPERATIONS = frozenset({"create", "get", "list", "update", "delete"})
"""All valid CRUD operation names."""

_VALID_WHENS = frozenset({"before", "after"})
"""Valid values for the ``when`` parameter on ``@transform``."""

_VALID_CHANNELS = frozenset({"*", "rest", "graphql"})
"""Valid values for the ``channel`` parameter on decorators."""


@dataclass
class _HandlerEntry:
    """Internal record for a ``@handler`` registration."""

    schema_name: str
    operation: str
    handler: Any
    version: str | None = None
    channel: str = "*"


@dataclass
class _HookEntry:
    """Internal record for ``@guard``, ``@validate``, ``@transform``, ``@on``."""

    schema_name: str
    operation: str | None
    event_name: str | None
    handler: EventHandler
    version: str | None = None
    channel: str = "*"


class SlipStreamRegistry:
    """Collects handler overrides and lifecycle hooks via decorators.

    All registrations are deferred — nothing executes at decoration time.
    Call :meth:`apply` during application startup (done automatically by
    ``SlipStream.lifespan()``) to merge registrations into the container
    and event bus.

    Execution order within a single ``pre_*`` event:

    1. ``@guard`` handlers (in declaration order)
    2. ``@validate`` handlers (in declaration order)
    3. ``@transform(when="before")`` handlers (in declaration order)

    For ``post_*`` events:

    1. ``@transform(when="after")`` handlers (in declaration order)
    2. ``@on("post_*")`` handlers (in declaration order)
    """

    def __init__(self) -> None:
        self._handlers: list[_HandlerEntry] = []
        self._guards: list[_HookEntry] = []
        self._validators: list[_HookEntry] = []
        self._transforms_before: list[_HookEntry] = []
        self._transforms_after: list[_HookEntry] = []
        self._on_hooks: list[_HookEntry] = []

    def handler(
        self,
        schema: str,
        operation: str,
        *,
        version: str | None = None,
        channel: str = "*",
    ) -> Callable[[Any], Any]:
        """Register a handler override for a schema + operation.

        The decorated function replaces the default service for that operation.
        It receives a fully populated :class:`RequestContext` and must return
        the result (the entity or list of entities).

        Args:
            schema: Schema name (e.g., ``"widget"``).
            operation: CRUD operation (``"create"``, ``"get"``, ``"list"``,
                ``"update"``, ``"delete"``).
            version: Optional schema version to scope to. When ``None``
                the handler applies to all versions.
            channel: Transport channel to scope to (``"rest"``, ``"graphql"``,
                or ``"*"`` for all channels).

        Example::

            @registry.handler("widget", "create")
            async def custom_create(ctx: RequestContext) -> Any:
                ctx.data.name = ctx.data.name.upper()
                return await default_service.execute(data=ctx.data, ...)

            @registry.handler("widget", "create", channel="graphql")
            async def graphql_only_create(ctx: RequestContext) -> Any:
                # Only handles GraphQL create requests
                ...
        """
        if operation not in _VALID_OPERATIONS:
            raise ValueError(
                f"Unknown operation '{operation}'. "
                f"Must be one of: {sorted(_VALID_OPERATIONS)}"
            )
        if channel not in _VALID_CHANNELS:
            raise ValueError(
                f"Unknown channel '{channel}'. "
                f"Must be one of: {sorted(_VALID_CHANNELS)}"
            )

        def decorator(fn: Any) -> Any:
            self._handlers.append(
                _HandlerEntry(
                    schema_name=schema,
                    operation=operation,
                    handler=fn,
                    version=version,
                    channel=channel,
                )
            )
            return fn

        return decorator

    def guard(
        self,
        schema: str,
        *operations: str,
        version: str | None = None,
        channel: str = "*",
    ) -> Callable[[EventHandler], EventHandler]:
        """Register an authorization guard for one or more operations.

        Guards run as ``pre_*`` hooks *before* validators and transforms.
        Raise :class:`HookError` to abort the request.

        Args:
            schema: Schema name, or ``"*"`` for all schemas.
            *operations: One or more CRUD operation names.
            version: Optional schema version to scope to.
            channel: Transport channel (``"rest"``, ``"graphql"``, ``"*"``).

        Example::

            @registry.guard("widget", "delete", "update")
            async def admins_only(ctx: RequestContext) -> None:
                if ctx.current_user.get("role") != "admin":
                    raise HookError(403, "Admin role required")
        """
        for op in operations:
            if op not in _VALID_OPERATIONS:
                raise ValueError(
                    f"Unknown operation '{op}'. "
                    f"Must be one of: {sorted(_VALID_OPERATIONS)}"
                )
        if channel not in _VALID_CHANNELS:
            raise ValueError(
                f"Unknown channel '{channel}'. "
                f"Must be one of: {sorted(_VALID_CHANNELS)}"
            )

        def decorator(fn: EventHandler) -> EventHandler:
            for op in operations:
                self._guards.append(
                    _HookEntry(
                        schema_name=schema,
                        operation=op,
                        event_name=None,
                        handler=fn,
                        version=version,
                        channel=channel,
                    )
                )
            return fn

        return decorator

    def validate(
        self,
        schema: str,
        *operations: str,
        version: str | None = None,
        channel: str = "*",
    ) -> Callable[[EventHandler], EventHandler]:
        """Register a cross-field validation hook for one or more operations.

        Validators run as ``pre_*`` hooks *after* guards but *before* transforms.
        Raise :class:`HookError` to reject the request.

        Args:
            schema: Schema name, or ``"*"`` for all schemas.
            *operations: One or more CRUD operation names.
            version: Optional schema version to scope to.
            channel: Transport channel (``"rest"``, ``"graphql"``, ``"*"``).

        Example::

            @registry.validate("order", "create", "update")
            async def check_date_range(ctx: RequestContext) -> None:
                if ctx.data.end_date < ctx.data.start_date:
                    raise HookError(422, "end_date must be after start_date")
        """
        for op in operations:
            if op not in _VALID_OPERATIONS:
                raise ValueError(
                    f"Unknown operation '{op}'. "
                    f"Must be one of: {sorted(_VALID_OPERATIONS)}"
                )
        if channel not in _VALID_CHANNELS:
            raise ValueError(
                f"Unknown channel '{channel}'. "
                f"Must be one of: {sorted(_VALID_CHANNELS)}"
            )

        def decorator(fn: EventHandler) -> EventHandler:
            for op in operations:
                self._validators.append(
                    _HookEntry(
                        schema_name=schema,
                        operation=op,
                        event_name=None,
                        handler=fn,
                        version=version,
                        channel=channel,
                    )
                )
            return fn

        return decorator

    def transform(
        self,
        schema: str,
        *operations: str,
        when: str = "before",
        version: str | None = None,
        channel: str = "*",
    ) -> Callable[[EventHandler], EventHandler]:
        """Register a field transformation hook.

        ``when="before"`` transforms run as ``pre_*`` hooks (after guards
        and validators). ``when="after"`` transforms run as ``post_*`` hooks.

        Args:
            schema: Schema name, or ``"*"`` for all schemas.
            *operations: One or more CRUD operation names.
            when: ``"before"`` (pre-hook) or ``"after"`` (post-hook).
            version: Optional schema version to scope to.
            channel: Transport channel (``"rest"``, ``"graphql"``, ``"*"``).

        Example::

            @registry.transform("user", "create", "update", when="before")
            async def normalize_email(ctx: RequestContext) -> None:
                if ctx.data.email:
                    ctx.data.email = ctx.data.email.lower().strip()
        """
        if when not in _VALID_WHENS:
            raise ValueError(
                f"'when' must be 'before' or 'after', got '{when}'"
            )
        for op in operations:
            if op not in _VALID_OPERATIONS:
                raise ValueError(
                    f"Unknown operation '{op}'. "
                    f"Must be one of: {sorted(_VALID_OPERATIONS)}"
                )
        if channel not in _VALID_CHANNELS:
            raise ValueError(
                f"Unknown channel '{channel}'. "
                f"Must be one of: {sorted(_VALID_CHANNELS)}"
            )

        target = self._transforms_before if when == "before" else self._transforms_after

        def decorator(fn: EventHandler) -> EventHandler:
            for op in operations:
                target.append(
                    _HookEntry(
                        schema_name=schema,
                        operation=op,
                        event_name=None,
                        handler=fn,
                        version=version,
                        channel=channel,
                    )
                )
            return fn

        return decorator

    def on(
        self, event: str, schema_name: str = "*"
    ) -> Callable[[EventHandler], EventHandler]:
        """Register a lifecycle hook by event name.

        This is equivalent to ``EventBus.on()`` but collected via the registry
        so consumers don't need to wire the ``EventBus`` manually.

        Args:
            event: Lifecycle event name (e.g., ``"post_create"``).
            schema_name: Schema to scope to, or ``"*"`` for all schemas.

        Example::

            @registry.on("post_create")
            async def audit_log(ctx: RequestContext) -> None:
                log.info("Created %s %s", ctx.schema_name, ctx.entity_id)

            @registry.on("pre_delete", schema_name="widget")
            async def prevent_delete(ctx: RequestContext) -> None:
                raise HookError(403, "Widgets cannot be deleted")
        """
        if event not in LIFECYCLE_EVENTS:
            raise ValueError(
                f"Unknown event '{event}'. "
                f"Must be one of: {sorted(LIFECYCLE_EVENTS)}"
            )

        def decorator(fn: EventHandler) -> EventHandler:
            self._on_hooks.append(
                _HookEntry(
                    schema_name=schema_name,
                    operation=None,
                    event_name=event,
                    handler=fn,
                )
            )
            return fn

        return decorator

    def apply(
        self,
        container: "EntityContainer",
        event_bus: EventBus,
    ) -> None:
        """Merge all registrations into the container and event bus.

        Called by ``SlipStream.lifespan()`` after ``init_container()`` and
        before endpoint registration. Should not be called manually unless
        you are building a custom startup sequence.

        Version-scoped handlers (``version != None``) are stored with a
        ``{operation}@{version}`` key in ``handler_overrides``.  The endpoint
        factory checks for a version-specific override first, falling back to
        the unversioned handler.

        Version-scoped hooks are wrapped so they only fire when the
        ``RequestContext.schema_version`` matches.

        Raises:
            ValueError: If a ``@handler`` references an unknown schema name.
        """
        available = list(container.get_all().keys())

        # 1. Apply handler overrides to EntityRegistration
        for entry in self._handlers:
            try:
                registration = container.get(entry.schema_name)
            except KeyError:
                raise ValueError(
                    f"@handler registered for unknown schema '{entry.schema_name}'. "
                    f"Available schemas: {available}"
                ) from None

            if entry.version:
                key = f"{entry.operation}@{entry.version}"
            else:
                key = entry.operation
            registration.handler_overrides[key] = entry.handler

        # 2. Register pre_* hooks in order: guards → validators → before-transforms
        for entry in self._guards:
            handler = self._wrap_version_hook(entry.handler, entry.version)
            event_bus.register(
                f"pre_{entry.operation}",
                handler,
                schema_name=entry.schema_name,
            )

        for entry in self._validators:
            handler = self._wrap_version_hook(entry.handler, entry.version)
            event_bus.register(
                f"pre_{entry.operation}",
                handler,
                schema_name=entry.schema_name,
            )

        for entry in self._transforms_before:
            handler = self._wrap_version_hook(entry.handler, entry.version)
            event_bus.register(
                f"pre_{entry.operation}",
                handler,
                schema_name=entry.schema_name,
            )

        # 3. Register post_* hooks: after-transforms
        for entry in self._transforms_after:
            handler = self._wrap_version_hook(entry.handler, entry.version)
            event_bus.register(
                f"post_{entry.operation}",
                handler,
                schema_name=entry.schema_name,
            )

        # 4. Direct @on hooks
        for entry in self._on_hooks:
            event_bus.register(
                entry.event_name,
                entry.handler,
                schema_name=entry.schema_name,
            )

        logger.info(
            "Registry applied: %d handler(s), %d guard(s), %d validator(s), "
            "%d transform(s), %d hook(s)",
            len(self._handlers),
            len(self._guards),
            len(self._validators),
            len(self._transforms_before) + len(self._transforms_after),
            len(self._on_hooks),
        )

    @staticmethod
    def _wrap_version_hook(
        handler: EventHandler, version: str | None
    ) -> EventHandler:
        """Wrap a hook so it only fires for a specific schema version.

        If *version* is ``None``, the handler runs unconditionally.
        """
        if version is None:
            return handler

        async def versioned_handler(ctx: "RequestContext") -> None:
            if ctx.schema_version == version:
                await handler(ctx)

        versioned_handler.__wrapped__ = handler  # type: ignore[attr-defined]
        return versioned_handler
