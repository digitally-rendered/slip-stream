"""Tests for SecurityHeadersFilter — defensive HTTP response headers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers
from starlette.responses import Response

from slip_stream.adapters.api.filters.base import FilterContext
from slip_stream.adapters.api.filters.security_headers import SecurityHeadersFilter


def _make_request(path: str = "/api/v1/widget/") -> SimpleNamespace:
    return SimpleNamespace(
        method="GET",
        url=SimpleNamespace(path=path),
        headers=Headers({}),
        client=SimpleNamespace(host="1.2.3.4"),
    )


def _make_response(status_code: int = 200) -> Response:
    return Response(status_code=status_code)


class TestDefaultHeaders:

    @pytest.mark.asyncio
    async def test_default_headers_added(self):
        f = SecurityHeadersFilter()
        request = _make_request()
        ctx = FilterContext()
        response = _make_response()

        result = await f.on_response(request, response, ctx)

        assert result.headers["X-Content-Type-Options"] == "nosniff"
        assert result.headers["X-Frame-Options"] == "DENY"
        assert result.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "camera=()" in result.headers["Permissions-Policy"]
        assert result.headers["X-XSS-Protection"] == "0"

    @pytest.mark.asyncio
    async def test_on_request_is_noop(self):
        f = SecurityHeadersFilter()
        request = _make_request()
        ctx = FilterContext()
        # Should not raise or modify anything
        await f.on_request(request, ctx)

    @pytest.mark.asyncio
    async def test_order_is_zero(self):
        f = SecurityHeadersFilter()
        assert f.order == 0


class TestCustomHeaders:

    @pytest.mark.asyncio
    async def test_custom_header_overrides(self):
        f = SecurityHeadersFilter(
            custom_headers={"X-Frame-Options": "SAMEORIGIN", "X-Custom": "value"}
        )
        request = _make_request()
        ctx = FilterContext()
        response = _make_response()

        result = await f.on_response(request, response, ctx)

        assert result.headers["X-Frame-Options"] == "SAMEORIGIN"
        assert result.headers["X-Custom"] == "value"
        # Other defaults should still be present
        assert result.headers["X-Content-Type-Options"] == "nosniff"


class TestHSTS:

    @pytest.mark.asyncio
    async def test_no_hsts_by_default(self):
        f = SecurityHeadersFilter()
        request = _make_request()
        ctx = FilterContext()
        response = _make_response()

        result = await f.on_response(request, response, ctx)

        assert "Strict-Transport-Security" not in result.headers

    @pytest.mark.asyncio
    async def test_hsts_opt_in(self):
        f = SecurityHeadersFilter(include_hsts=True)
        request = _make_request()
        ctx = FilterContext()
        response = _make_response()

        result = await f.on_response(request, response, ctx)

        hsts = result.headers["Strict-Transport-Security"]
        assert "max-age=63072000" in hsts
        assert "includeSubDomains" in hsts
