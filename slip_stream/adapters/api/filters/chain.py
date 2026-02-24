"""FilterChain — ordered pipeline that runs filters in onion model.

Filters are sorted by ``order`` and executed ascending on request,
descending on response.
"""

from __future__ import annotations

from typing import List

from starlette.requests import Request
from starlette.responses import Response

from slip_stream.adapters.api.filters.base import (
    FilterBase,
    FilterContext,
)


class FilterChain:
    """Manages an ordered sequence of filters.

    Filters are kept sorted by their ``order`` attribute.  On request they
    execute in ascending order; on response in descending order (onion model).
    """

    def __init__(self) -> None:
        self._filters: List[FilterBase] = []

    def add_filter(self, filt: FilterBase) -> None:
        """Add a filter and re-sort by order."""
        self._filters.append(filt)
        self._filters.sort(key=lambda f: f.order)

    def add_filters(self, filters: List[FilterBase]) -> None:
        """Add multiple filters and re-sort."""
        self._filters.extend(filters)
        self._filters.sort(key=lambda f: f.order)

    @property
    def filters(self) -> List[FilterBase]:
        """Return the current filter list (sorted by order)."""
        return list(self._filters)

    async def process_request(self, request: Request) -> FilterContext:
        """Run all filters' ``on_request`` in ascending order.

        Creates a fresh ``FilterContext`` and attaches it to
        ``request.state.filter_context``.

        Returns:
            The populated FilterContext.

        Raises:
            FilterShortCircuit: If a filter aborts the chain.
        """
        context = FilterContext()
        request.state.filter_context = context

        for filt in self._filters:
            await filt.on_request(request, context)

        return context

    async def process_response(
        self,
        request: Request,
        response: Response,
        context: FilterContext,
    ) -> Response:
        """Run all filters' ``on_response`` in descending order.

        Returns:
            The (possibly replaced) Response.
        """
        for filt in reversed(self._filters):
            response = await filt.on_response(request, response, context)

        return response
