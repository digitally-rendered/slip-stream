"""Base types for the filter chain system.

Defines the FilterBase ABC, FilterContext dataclass, and FilterShortCircuit
exception used throughout the filter pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from starlette.requests import Request
from starlette.responses import Response


@dataclass
class FilterContext:
    """Per-request context carried through the filter chain.

    Stored on ``request.state.filter_context`` so FastAPI dependencies
    can access filter-produced data (e.g. the authenticated user).

    Attributes:
        content_type: The parsed Content-Type of the incoming request.
        accept: The parsed Accept type for the response.
        user: Authenticated user dict (populated by auth filters).
        extras: Arbitrary key-value store for custom filter data.
    """

    content_type: str = "application/json"
    accept: str = "application/json"
    user: Optional[Dict[str, Any]] = None
    extras: Dict[str, Any] = field(default_factory=dict)


class FilterShortCircuit(Exception):
    """Raise from a filter's ``on_request`` to abort the chain early.

    The middleware will catch this and return the embedded response directly
    without calling downstream filters or the endpoint.

    Args:
        status_code: HTTP status code for the response.
        body: Response body (will be returned as-is).
        headers: Optional extra response headers.
    """

    def __init__(
        self,
        status_code: int = 400,
        body: str = "",
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.status_code = status_code
        self.body = body
        self.headers = headers or {}
        super().__init__(f"FilterShortCircuit({status_code})")


class FilterBase(ABC):
    """Abstract base class for all filters in the chain.

    Subclasses must implement ``on_request`` and ``on_response``.
    The ``order`` attribute controls execution sequence: lower values run first
    on request, last on response (onion model).

    Attributes:
        order: Execution priority. Convention: auth=10, content_negotiation=50,
            user filters=100+.
    """

    order: int = 100

    @abstractmethod
    async def on_request(
        self, request: Request, context: FilterContext
    ) -> None:
        """Process an incoming request before it reaches the endpoint.

        Modify the request or context in-place. Raise ``FilterShortCircuit``
        to abort the chain and return an immediate response.
        """

    @abstractmethod
    async def on_response(
        self, request: Request, response: Response, context: FilterContext
    ) -> Response:
        """Process an outgoing response before it is sent to the client.

        May return the original response or a new ``Response`` object.
        """

    @staticmethod
    async def _read_body(response: Response) -> bytes:
        """Read response body from body_iterator or body attribute.

        Handles both streaming (``body_iterator``) and buffered (``body``)
        Starlette responses.  Returns empty bytes if neither is available.
        """
        if hasattr(response, "body_iterator"):
            chunks = []
            async for chunk in response.body_iterator:
                if isinstance(chunk, str):
                    chunks.append(chunk.encode("utf-8"))
                else:
                    chunks.append(chunk)
            return b"".join(chunks)
        elif hasattr(response, "body"):
            return response.body
        return b""
