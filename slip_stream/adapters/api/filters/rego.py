"""Rego/OPA policy filter for the ASGI filter chain.

Evaluates policies for every request using either a remote OPA server
or a local Rego engine. Runs at order=3 (after schema version, before auth).

Usage::

    from slip_stream.core.policy import OpaRemotePolicy, InlinePolicy
    from slip_stream.adapters.api.filters.rego import RegoPolicyFilter

    # Remote OPA
    engine = OpaRemotePolicy(url="http://localhost:8181")
    policy_filter = RegoPolicyFilter(engine=engine, policy_path="authz/allow")

    # Inline Python policies
    engine = InlinePolicy()
    @engine.rule("authz/allow")
    def allow(input_data):
        return input_data.get("method") != "DELETE"

    slip = SlipStream(app=app, schema_dir=..., filters=[policy_filter])
"""

from __future__ import annotations

import json
import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from slip_stream.adapters.api.filters.base import (
    FilterBase,
    FilterContext,
    FilterShortCircuit,
)

logger = logging.getLogger(__name__)


class RegoPolicyFilter(FilterBase):
    """ASGI filter that evaluates Rego/OPA policies per request.

    Args:
        engine: A ``PolicyEngine`` instance (``OpaRemotePolicy``,
            ``LocalRegoPolicy``, or ``InlinePolicy``).
        policy_path: Default policy path to evaluate.
        skip_paths: URL path prefixes to skip policy evaluation
            (e.g., ``["/health", "/schemas"]``).
        build_input: Optional callable to customize the OPA input document.
            Receives ``(request, context)`` and returns a dict.

    Attributes:
        order: 3 — runs very early, before auth (10).
    """

    order = 3

    def __init__(
        self,
        engine: Any,
        policy_path: str = "authz/allow",
        skip_paths: list[str] | None = None,
        build_input: Any | None = None,
    ) -> None:
        self.engine = engine
        self.policy_path = policy_path
        self.skip_paths = skip_paths or [
            "/health",
            "/ready",
            "/_topology",
            "/docs",
            "/openapi.json",
        ]
        self._build_input = build_input

    async def on_request(self, request: Request, context: FilterContext) -> None:
        path = request.url.path

        # Skip configured paths
        for skip in self.skip_paths:
            if path.startswith(skip):
                return

        # Build input document
        if self._build_input:
            input_data = self._build_input(request, context)
        else:
            input_data = self._default_input(request, context)

        try:
            allowed = await self.engine.evaluate(self.policy_path, input_data)
        except Exception as e:
            logger.error("Policy evaluation error: %s", e)
            raise FilterShortCircuit(
                status_code=503,
                body=json.dumps(
                    {
                        "type": "https://slip-stream.dev/errors/service-unavailable",
                        "title": "Service Unavailable",
                        "status": 503,
                        "detail": "Policy service temporarily unavailable",
                        "instance": path,
                    }
                ),
                headers={"Content-Type": "application/problem+json"},
            )

        if not allowed:
            logger.info("Policy denied: %s %s", request.method, path)
            raise FilterShortCircuit(
                status_code=403,
                body=json.dumps(
                    {
                        "type": "https://slip-stream.dev/errors/policy-denied",
                        "title": "Policy Denied",
                        "status": 403,
                        "detail": f"Request denied by policy: {self.policy_path}",
                        "instance": path,
                    }
                ),
                headers={"Content-Type": "application/problem+json"},
            )

        # Store policy decision in context for downstream use
        context.extras["policy_decision"] = True

    async def on_response(
        self, request: Request, response: Response, context: FilterContext
    ) -> Response:
        return response

    def _default_input(
        self, request: Request, context: FilterContext
    ) -> dict[str, Any]:
        """Build the default OPA input document from the request."""
        user = context.user or {}
        path_parts = [p for p in request.url.path.split("/") if p]

        return {
            "method": request.method,
            "path": request.url.path,
            "path_parts": path_parts,
            "user": dict(user) if hasattr(user, "items") else user,
            "headers": dict(request.headers) if request.headers else {},
        }
