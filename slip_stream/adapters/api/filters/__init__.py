"""Filter chain system for slip-stream.

Provides an onion-model filter pipeline applied via ASGI middleware.
Filters execute in ascending order on request and descending order on response.
"""

from slip_stream.adapters.api.filters.base import (
    FilterBase,
    FilterContext,
    FilterShortCircuit,
)
from slip_stream.adapters.api.filters.chain import FilterChain
from slip_stream.adapters.api.filters.envelope import ResponseEnvelopeFilter
from slip_stream.adapters.api.filters.middleware import FilterChainMiddleware
from slip_stream.adapters.api.filters.projection import FieldProjectionFilter

__all__ = [
    "FilterBase",
    "FilterContext",
    "FilterShortCircuit",
    "FilterChain",
    "FilterChainMiddleware",
    "ResponseEnvelopeFilter",
    "FieldProjectionFilter",
]
