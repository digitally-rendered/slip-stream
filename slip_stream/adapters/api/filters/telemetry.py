"""OpenTelemetry HTTP span filter for the ASGI filter chain.

Creates a root span for each HTTP request. Order 1 — runs before all
other filters so the HTTP span encloses everything else in the chain.

Usage::

    from slip_stream.adapters.api.filters.telemetry import TelemetryFilter
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider()
    slip = SlipStream(
        app=app,
        schema_dir=...,
        filters=[TelemetryFilter(tracer_provider=provider)],
    )

Install the optional dependency::

    pip install slip-stream[telemetry]
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from starlette.requests import Request
from starlette.responses import Response

from slip_stream.adapters.api.filters.base import FilterBase, FilterContext

logger = logging.getLogger(__name__)

try:
    from opentelemetry import context as otel_context
    from opentelemetry import propagate, trace
    from opentelemetry.trace import Status, StatusCode

    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False


class TelemetryFilter(FilterBase):
    """ASGI filter that creates a root HTTP span for every request.

    Runs at ``order=1`` so it wraps all other filters and the endpoint.
    On response it records the HTTP status code and marks the span as
    ``ERROR`` for 4xx/5xx responses.

    The span and context token are stored in ``context.extras`` under the
    keys ``_otel_span`` and ``_otel_token`` so inner filters can access
    the active span if needed.

    Args:
        tracer_provider: Optional ``TracerProvider``. Defaults to the
            global provider when ``None``.

    Raises:
        ImportError: If ``opentelemetry-api`` is not installed.

    Usage::

        TelemetryFilter()
        TelemetryFilter(tracer_provider=my_provider)
    """

    order: int = 1

    def __init__(self, tracer_provider: Optional[Any] = None) -> None:
        if not HAS_OTEL:
            raise ImportError(
                "opentelemetry-api is required for TelemetryFilter. "
                "Install it with: pip install slip-stream[telemetry]"
            )
        if tracer_provider is not None:
            self._tracer = tracer_provider.get_tracer("slip-stream")
        else:
            self._tracer = trace.get_tracer("slip-stream")

    async def on_request(self, request: Request, context: FilterContext) -> None:
        """Start an HTTP span and attach it to the current OTel context.

        The propagated trace context from incoming headers (e.g. W3C
        ``traceparent``) is extracted so distributed traces propagate
        correctly through gateways and load balancers.
        """
        # Extract trace context propagated from upstream callers
        carrier = dict(request.headers)
        parent_ctx = propagate.extract(carrier)

        method = request.method
        path = request.url.path
        span_name = f"{method} {path}"

        # Start the span inside the propagated parent context
        span = self._tracer.start_span(span_name, context=parent_ctx)

        # Make the new span the current context so child spans nest under it
        span_token = otel_context.attach(trace.set_span_in_context(span))

        span.set_attribute("http.method", method)
        span.set_attribute("http.url", str(request.url))
        span.set_attribute("http.route", path)

        schema_name = self._extract_schema_name(path)
        if schema_name:
            span.set_attribute("slip_stream.schema_name", schema_name)

        # Persist span and token so on_response can close them
        context.extras["_otel_span"] = span
        context.extras["_otel_token"] = span_token

    async def on_response(
        self, request: Request, response: Response, context: FilterContext
    ) -> Response:
        """Record the HTTP status code and end the span."""
        span = context.extras.get("_otel_span")
        token = context.extras.get("_otel_token")

        if span is not None:
            status_code = response.status_code
            span.set_attribute("http.status_code", status_code)

            if status_code >= 400:
                span.set_status(Status(StatusCode.ERROR, f"HTTP {status_code}"))
            else:
                span.set_status(Status(StatusCode.OK))

            span.end()

        if token is not None:
            otel_context.detach(token)

        return response

    @staticmethod
    def _extract_schema_name(path: str) -> str:
        """Extract the schema name from a URL path segment.

        Converts kebab-case path segments back to snake_case schema names.
        Examples::

            /api/v1/widget/ -> "widget"
            /api/v1/labor-market-analysis/abc-123 -> "labor_market_analysis"
            /health -> ""

        Args:
            path: The raw URL path string.

        Returns:
            The snake_case schema name, or an empty string if not found.
        """
        # Strip leading slash and split
        parts = [p for p in path.strip("/").split("/") if p]

        # Skip versioned API prefix segments like "api" and "v1"
        filtered = []
        for part in parts:
            lower = part.lower()
            if lower == "api":
                continue
            # Skip version segments: v1, v2, v10, etc.
            if len(lower) >= 2 and lower[0] == "v" and lower[1:].isdigit():
                continue
            filtered.append(part)

        if not filtered:
            return ""

        # First remaining segment is the resource name; convert kebab -> snake
        return filtered[0].replace("-", "_")
