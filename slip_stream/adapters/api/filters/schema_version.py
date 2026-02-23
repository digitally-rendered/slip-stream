"""Schema version negotiation filter.

Reads ``X-Schema-Version`` from the incoming request and projects the response
through the requested schema version.  New fields that did not exist in the
older schema are null-filled so a client speaking an older schema version
always receives a structurally valid payload.

Order **5** — runs very early on request (to capture the header) and very late
on response (to project *after* the handler has produced its result but
*before* the response envelope wraps it).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Set

from starlette.requests import Request
from starlette.responses import Response

from slip_stream.adapters.api.filters.base import FilterBase, FilterContext
from slip_stream.core.schema.registry import SchemaRegistry

logger = logging.getLogger(__name__)


class SchemaVersionFilter(FilterBase):
    """Project responses through a requested schema version.

    When a client sends ``X-Schema-Version: 1.0.0``, the response body is
    filtered to include only the fields defined in that version of the schema.
    Fields present in the latest version but absent in the requested version
    are omitted.  Fields present in the requested version but absent in the
    response are null-filled.

    Attributes:
        order: 5 — captures header early, projects response late.
    """

    order: int = 5

    async def on_request(
        self, request: Request, context: FilterContext
    ) -> None:
        """Extract schema version from request header."""
        version = request.headers.get("x-schema-version")
        if version:
            context.extras["schema_version"] = version

    async def on_response(
        self, request: Request, response: Response, context: FilterContext
    ) -> Response:
        """Project the response through the requested schema version."""
        requested_version = context.extras.get("schema_version")
        if not requested_version:
            return response

        if response.status_code >= 400 or response.status_code == 204:
            return response

        body = await self._read_body(response)
        if not body:
            return response

        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return response

        # Determine schema name from route path
        schema_name = self._extract_schema_name(request)
        if not schema_name:
            return response

        try:
            registry = SchemaRegistry()
            schema = registry.get_schema(schema_name, requested_version)
        except ValueError:
            return response

        allowed_fields = set(schema.get("properties", {}).keys())

        if isinstance(data, list):
            projected = [
                self._project_item(item, allowed_fields) for item in data
            ]
        elif isinstance(data, dict):
            # Could be an envelope {"data": ...} or a bare object
            if "data" in data and "meta" in data:
                inner = data["data"]
                if isinstance(inner, list):
                    data["data"] = [
                        self._project_item(item, allowed_fields)
                        for item in inner
                    ]
                elif isinstance(inner, dict):
                    data["data"] = self._project_item(inner, allowed_fields)
                projected = data
            else:
                projected = self._project_item(data, allowed_fields)
        else:
            return response

        new_body = json.dumps(projected)

        new_headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in ("content-length", "content-type")
        }

        return Response(
            content=new_body,
            status_code=response.status_code,
            headers=new_headers,
            media_type="application/json",
        )

    def _project_item(
        self, item: Dict[str, Any], allowed_fields: Set[str]
    ) -> Dict[str, Any]:
        """Keep only fields present in the schema version; null-fill missing."""
        if not isinstance(item, dict):
            return item
        result: Dict[str, Any] = {}
        for field_name in allowed_fields:
            result[field_name] = item.get(field_name)
        return result

    def _extract_schema_name(self, request: Request) -> Optional[str]:
        """Try to determine the schema name from the request path.

        Looks for ``schema_name`` in the route's path parameters or falls
        back to parsing the URL path segments.
        """
        # Check if the endpoint has schema_name in path params
        path_params = request.path_params
        if "schema_name" in path_params:
            return path_params["schema_name"]

        # Check request state for schema name set by endpoint
        schema_name = getattr(request.state, "schema_name", None)
        if schema_name:
            return schema_name

        # Parse from URL: /api/v1/{schema_name}/...
        parts = [p for p in request.url.path.strip("/").split("/") if p]
        if len(parts) >= 3 and parts[0] == "api":
            # /api/v1/widget/... → widget
            return parts[2].replace("-", "_")
        elif len(parts) >= 1:
            # /widget/... → widget
            return parts[0].replace("-", "_")

        return None

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
