"""Unified request context for the slip-stream handler lifecycle.

Provides ``RequestContext`` — the single structured object that flows through
endpoint handlers, lifecycle hooks, and controller overrides. Both ASGI-level
filters and handler-level overrides work with this same context.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Literal, Optional, Protocol, runtime_checkable

from dotted_dict import DottedDict
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import Response

OperationType = Literal["create", "get", "list", "update", "delete"]


@dataclass
class RequestContext:
    """Unified context carried through the entire request lifecycle.

    Created inside each endpoint handler after FastAPI dependency injection,
    then passed to lifecycle hooks and handler overrides.

    Attributes:
        request: The Starlette/FastAPI Request object.
        operation: The CRUD operation being performed.
        schema_name: The entity schema name (e.g., ``"widget"``).
        entity_id: The parsed UUID from the path (GET/PATCH/DELETE).
        entity: The hydrated entity from the database (typed Pydantic model).
        data: The parsed request body (Create or Update model).
        current_user: The authenticated user dict.
        db: The async Motor database instance.
        response: The Response object (populated after service execution).
        result: The service execution result.
        skip: Pagination offset (list operations).
        limit: Pagination limit (list operations).
        extras: Arbitrary key-value store for extensions.
    """

    request: Request
    operation: OperationType
    schema_name: str

    # Populated by framework before handler/hooks
    entity_id: Optional[uuid.UUID] = None
    entity: Optional[BaseModel] = None
    data: Optional[BaseModel] = None
    current_user: Optional[Dict[str, Any]] = None
    db: Any = None

    # Populated after service execution
    response: Optional[Response] = None
    result: Any = None

    # List-specific parameters
    skip: int = 0
    limit: int = 100
    filter_criteria: Optional[Dict[str, Any]] = None
    sort_by: Optional[str] = None
    sort_order: int = -1

    # Schema version negotiation
    schema_version: Optional[str] = None

    # Transport channel (set by the transport layer: "rest", "graphql", etc.)
    channel: str = "rest"

    # Extension point
    extras: Dict[str, Any] = field(default_factory=DottedDict)

    @classmethod
    def from_request(
        cls,
        request: Request,
        operation: OperationType,
        schema_name: str,
        **kwargs: Any,
    ) -> RequestContext:
        """Build a RequestContext, pulling user from FilterContext if available.

        If ``current_user`` is not provided in kwargs and a ``FilterContext``
        exists on ``request.state`` with a user, it will be used automatically.

        Plain dicts passed as ``current_user`` or ``extras`` are automatically
        wrapped in :class:`DottedDict` for attribute-style access
        (e.g. ``ctx.current_user.role`` instead of ``ctx.current_user["role"]``).
        """
        filter_ctx = getattr(request.state, "filter_context", None)
        if "current_user" not in kwargs and filter_ctx is not None:
            user = getattr(filter_ctx, "user", None)
            if user is not None:
                kwargs["current_user"] = user

        # Auto-pull schema version from header or filter context
        if "schema_version" not in kwargs:
            header_version = request.headers.get("x-schema-version")
            if header_version:
                kwargs["schema_version"] = header_version
            elif filter_ctx is not None:
                extras = getattr(filter_ctx, "extras", None)
                if extras is not None:
                    sv = extras.get("schema_version")
                    if sv:
                        kwargs["schema_version"] = sv

        # Wrap plain dicts for attribute-style access
        if "current_user" in kwargs and isinstance(kwargs["current_user"], dict) and not isinstance(kwargs["current_user"], DottedDict):
            kwargs["current_user"] = DottedDict(kwargs["current_user"])
        if "extras" in kwargs and isinstance(kwargs["extras"], dict) and not isinstance(kwargs["extras"], DottedDict):
            kwargs["extras"] = DottedDict(kwargs["extras"])

        return cls(
            request=request,
            operation=operation,
            schema_name=schema_name,
            **kwargs,
        )


@runtime_checkable
class HandlerOverride(Protocol):
    """Protocol for controller-level handler overrides.

    Implementors receive the fully populated ``RequestContext`` and return
    the result (or ``None`` to indicate the default service should run).

    Usage::

        async def create_handler(ctx: RequestContext) -> Any:
            # ctx.data is the parsed Create model
            # ctx.current_user is the authenticated user
            return await my_custom_create(ctx.data, ctx.current_user)
    """

    async def __call__(self, ctx: RequestContext) -> Any: ...
