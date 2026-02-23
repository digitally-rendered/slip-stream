"""ASGI middleware that drives the filter chain.

Integrates the FilterChain into Starlette/FastAPI's middleware stack so that
filters can intercept both request and response at the ASGI level — before
FastAPI performs JSON body parsing.
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from slip_stream.adapters.api.filters.base import FilterShortCircuit
from slip_stream.adapters.api.filters.chain import FilterChain

logger = logging.getLogger(__name__)


class FilterChainMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that runs a :class:`FilterChain` around every request.

    Usage::

        from slip_stream.adapters.api.filters import FilterChain, FilterChainMiddleware

        chain = FilterChain()
        chain.add_filter(my_filter)
        app.add_middleware(FilterChainMiddleware, filter_chain=chain)
    """

    def __init__(self, app, filter_chain: FilterChain) -> None:  # noqa: ANN001
        super().__init__(app)
        self.filter_chain = filter_chain

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Run request filters → endpoint → response filters."""
        try:
            context = await self.filter_chain.process_request(request)
        except FilterShortCircuit as sc:
            logger.debug(
                "Filter short-circuited with status %d", sc.status_code
            )
            response = JSONResponse(
                status_code=sc.status_code,
                content={"detail": sc.body} if sc.body else {},
                headers=sc.headers,
            )
            # Route short-circuit responses through response filters
            # so content negotiation can convert error format (e.g. YAML/XML)
            from slip_stream.adapters.api.filters.base import FilterContext

            error_context = FilterContext()
            response = await self.filter_chain.process_response(
                request, response, error_context
            )
            return response

        response = await call_next(request)

        response = await self.filter_chain.process_response(
            request, response, context
        )

        return response
