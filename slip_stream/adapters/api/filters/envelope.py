"""Response envelope filter — wraps responses in a standardized structure.

Opt-in filter that wraps successful responses in::

    {
        "data": <original response>,
        "meta": {
            "request_id": "uuid",
            "pagination": {"skip": 0, "limit": 100, "count": 42}
        }
    }
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional

from starlette.requests import Request
from starlette.responses import Response

from slip_stream.adapters.api.filters.base import FilterBase, FilterContext


class ResponseEnvelopeFilter(FilterBase):
    """Filter that wraps responses in a standardized envelope.

    Attributes:
        order: 90 — response runs before content negotiation (50),
            so the envelope gets format-converted to YAML/XML.
    """

    order: int = 90

    def __init__(self, include_pagination: bool = True) -> None:
        self.include_pagination = include_pagination

    async def on_request(
        self, request: Request, context: FilterContext
    ) -> None:
        """Generate and store a request_id."""
        context.extras["request_id"] = str(uuid.uuid4())

    async def on_response(
        self, request: Request, response: Response, context: FilterContext
    ) -> Response:
        """Wrap successful responses in an envelope."""
        if response.status_code >= 400 or response.status_code == 204:
            return response

        body = await self._read_body(response)
        if not body:
            return response

        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return response

        meta: Dict[str, Any] = {
            "request_id": context.extras.get("request_id"),
        }

        if isinstance(data, list) and self.include_pagination:
            skip = int(request.query_params.get("skip", "0"))
            limit = int(request.query_params.get("limit", "100"))
            meta["pagination"] = {
                "skip": skip,
                "limit": limit,
                "count": len(data),
            }

        envelope = {"data": data, "meta": meta}

        new_headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in ("content-length", "content-type")
        }

        return Response(
            content=json.dumps(envelope),
            status_code=response.status_code,
            headers=new_headers,
            media_type="application/json",
        )

    async def _read_body(self, response: Response) -> bytes:
        """Read response body from body_iterator or body attribute."""
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
