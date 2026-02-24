"""Reference auth filter implementation.

Provides a simple authentication filter that delegates to a caller-supplied
``authenticate`` callable. If authentication fails, the filter short-circuits
the chain with a 401 response.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from starlette.requests import Request
from starlette.responses import Response

from slip_stream.adapters.api.filters.base import (
    FilterBase,
    FilterContext,
    FilterShortCircuit,
)


class AuthFilter(FilterBase):
    """Authentication filter that runs early in the chain (order=10).

    Args:
        authenticate: Async callable that receives the ``Request`` and returns
            a user dict on success or ``None`` on failure.
        realm: Optional realm string for the ``WWW-Authenticate`` header.

    Usage::

        async def my_auth(request: Request) -> dict | None:
            token = request.headers.get("authorization")
            if token == "Bearer valid-token":
                return {"id": "user-1", "role": "admin"}
            return None

        auth_filter = AuthFilter(authenticate=my_auth)
    """

    order: int = 10

    def __init__(
        self,
        authenticate: Callable[[Request], Awaitable[Optional[Dict[str, Any]]]],
        realm: str = "slip-stream",
    ) -> None:
        self.authenticate = authenticate
        self.realm = realm

    async def on_request(self, request: Request, context: FilterContext) -> None:
        user = await self.authenticate(request)
        if user is None:
            raise FilterShortCircuit(
                status_code=401,
                body="Authentication required",
                headers={"WWW-Authenticate": f'Bearer realm="{self.realm}"'},
            )
        context.user = user

    async def on_response(
        self, request: Request, response: Response, context: FilterContext
    ) -> Response:
        return response
