"""Security headers filter — adds defensive HTTP headers to every response.

Runs at order=0 (outermost layer) so headers are applied regardless of
downstream filter or handler errors.

Usage::

    from slip_stream.adapters.api.filters.security_headers import SecurityHeadersFilter

    headers_filter = SecurityHeadersFilter()
    slip = SlipStream(app=app, schema_dir=..., filters=[headers_filter])
"""

from __future__ import annotations

from typing import Dict, Optional

from starlette.requests import Request
from starlette.responses import Response

from slip_stream.adapters.api.filters.base import FilterBase, FilterContext

_DEFAULT_HEADERS: Dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "X-XSS-Protection": "0",
}


class SecurityHeadersFilter(FilterBase):
    """Filter that adds security headers to every response.

    Default headers:
        - ``X-Content-Type-Options: nosniff``
        - ``X-Frame-Options: DENY``
        - ``Referrer-Policy: strict-origin-when-cross-origin``
        - ``Permissions-Policy: camera=(), microphone=(), geolocation=()``
        - ``X-XSS-Protection: 0`` (modern recommendation: disable, rely on CSP)

    Args:
        custom_headers: Additional or overriding headers to set.
        include_hsts: When ``True``, adds ``Strict-Transport-Security``
            header for HTTPS enforcement.

    Attributes:
        order: 0 — outermost filter layer.
    """

    order: int = 0

    def __init__(
        self,
        custom_headers: Optional[Dict[str, str]] = None,
        include_hsts: bool = False,
    ) -> None:
        self._headers = dict(_DEFAULT_HEADERS)
        if include_hsts:
            self._headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains"
            )
        if custom_headers:
            self._headers.update(custom_headers)

    async def on_request(self, request: Request, context: FilterContext) -> None:
        """No-op on request — headers are added on response only."""

    async def on_response(
        self, request: Request, response: Response, context: FilterContext
    ) -> Response:
        """Add security headers to the outgoing response."""
        for name, value in self._headers.items():
            response.headers[name] = value
        return response
