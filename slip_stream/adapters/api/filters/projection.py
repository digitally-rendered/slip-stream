"""Field projection filter — controls field visibility in responses.

Supports two projection mechanisms:
1. Query parameter: ``?fields=name,status,entity_id``
2. Role-based configuration: restrict fields per role per schema
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Set

from starlette.requests import Request
from starlette.responses import Response

from slip_stream.adapters.api.filters.base import FilterBase, FilterContext


class FieldProjectionFilter(FilterBase):
    """Filter that controls which fields appear in responses.

    When both query params and role rules are active, uses their intersection
    (query params cannot expose fields hidden by role config).

    Attributes:
        order: 95 — response runs first (strips fields), then envelope (90)
            wraps, then content negotiation (50) converts format.
    """

    order: int = 95

    def __init__(
        self,
        role_field_rules: Optional[Dict[str, Dict[str, Set[str]]]] = None,
        allow_query_projection: bool = True,
    ) -> None:
        """
        Args:
            role_field_rules: ``schema_name -> role -> allowed_fields``.
                A special role ``"*"`` matches any role not explicitly listed.
            allow_query_projection: Whether to honor ``?fields=`` query param.
        """
        self.role_field_rules = role_field_rules or {}
        self.allow_query_projection = allow_query_projection

    async def on_request(self, request: Request, context: FilterContext) -> None:
        """Parse ``?fields=`` query parameter."""
        if self.allow_query_projection:
            fields_param = request.query_params.get("fields")
            if fields_param:
                context.extras["projected_fields"] = {
                    f.strip() for f in fields_param.split(",") if f.strip()
                }

    async def on_response(
        self, request: Request, response: Response, context: FilterContext
    ) -> Response:
        """Apply field projection to the response body."""
        if response.status_code >= 400 or response.status_code == 204:
            return response

        # Determine allowed fields from role config
        role_allowed: Optional[Set[str]] = None
        if self.role_field_rules and context.user:
            schema_name = self._extract_schema_name(request.url.path)
            if schema_name and schema_name in self.role_field_rules:
                user_role = context.user.get("role", "*")
                schema_rules = self.role_field_rules[schema_name]
                role_allowed = schema_rules.get(user_role, schema_rules.get("*"))

        query_fields: Optional[Set[str]] = context.extras.get("projected_fields")

        # Compute effective fields
        if role_allowed is not None and query_fields is not None:
            effective_fields = role_allowed & query_fields
        elif role_allowed is not None:
            effective_fields = role_allowed
        elif query_fields is not None:
            effective_fields = query_fields
        else:
            return response

        body = await self._read_body(response)
        if not body:
            return response

        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return response

        projected = self._project(data, effective_fields)

        new_headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in ("content-length", "content-type")
        }

        return Response(
            content=json.dumps(projected),
            status_code=response.status_code,
            headers=new_headers,
            media_type="application/json",
        )

    def _project(self, data: Any, fields: Set[str]) -> Any:
        """Remove fields not in the allowed set."""
        if isinstance(data, list):
            return [self._project(item, fields) for item in data]
        if isinstance(data, dict):
            # Handle envelope format
            if "data" in data and "meta" in data:
                data["data"] = self._project(data["data"], fields)
                return data
            return {k: v for k, v in data.items() if k in fields}
        return data

    def _extract_schema_name(self, path: str) -> Optional[str]:
        """Extract schema name from URL path (e.g., /api/v1/widget/ -> widget)."""
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 3:
            return parts[2].replace("-", "_")
        return None
