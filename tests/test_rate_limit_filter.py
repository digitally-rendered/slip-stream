"""Tests for RateLimitFilter — sliding-window in-memory rate limiting."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers
from starlette.responses import Response

from slip_stream.adapters.api.filters.base import FilterContext, FilterShortCircuit
from slip_stream.adapters.api.filters.rate_limit import RateLimitFilter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    path: str = "/api/v1/widget/",
    method: str = "GET",
    ip: str = "1.2.3.4",
    headers: dict | None = None,
) -> SimpleNamespace:
    """Build a minimal fake Starlette request (same pattern as test_rego_policy.py)."""
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        headers=Headers(headers or {}),
        client=SimpleNamespace(host=ip),
    )


def _make_response(status_code: int = 200) -> Response:
    return Response(status_code=status_code)


async def _fire(
    f: RateLimitFilter,
    n: int,
    *,
    path: str = "/api/v1/widget/",
    ip: str = "1.2.3.4",
    ctx: FilterContext | None = None,
) -> list[Exception | None]:
    """Fire n requests through the filter; return one result per call.

    None means the request passed; a FilterShortCircuit instance means it
    was rejected.
    """
    results = []
    for _ in range(n):
        request = _make_request(path=path, ip=ip)
        c = ctx if ctx is not None else FilterContext()
        try:
            await f.on_request(request, c)
            results.append(None)
        except FilterShortCircuit as exc:
            results.append(exc)
    return results


# ---------------------------------------------------------------------------
# Basic rate limiting
# ---------------------------------------------------------------------------


class TestBasicRateLimiting:

    @pytest.mark.asyncio
    async def test_requests_within_limit_are_allowed(self):
        f = RateLimitFilter(default_limit=3, default_window=60)
        results = await _fire(f, 3)
        assert all(r is None for r in results), "All requests should pass within limit"

    @pytest.mark.asyncio
    async def test_exceeding_limit_returns_429(self):
        f = RateLimitFilter(default_limit=3, default_window=60)
        results = await _fire(f, 4)
        # First 3 pass, 4th is rejected
        assert results[:3] == [None, None, None]
        assert isinstance(results[3], FilterShortCircuit)
        assert results[3].status_code == 429

    @pytest.mark.asyncio
    async def test_order_is_2(self):
        f = RateLimitFilter()
        assert f.order == 2

    @pytest.mark.asyncio
    async def test_different_ips_tracked_independently(self):
        f = RateLimitFilter(default_limit=2, default_window=60)

        # Exhaust limit for IP A
        results_a = await _fire(f, 3, ip="10.0.0.1")
        assert isinstance(results_a[2], FilterShortCircuit)

        # IP B should still have a full budget
        results_b = await _fire(f, 2, ip="10.0.0.2")
        assert all(r is None for r in results_b)

    @pytest.mark.asyncio
    async def test_limit_resets_after_window_expires(self):
        current_time = [time.monotonic()]
        f = RateLimitFilter(
            default_limit=2, default_window=1, clock=lambda: current_time[0]
        )
        await _fire(f, 2)  # exhaust the budget

        # Wind time forward past the window
        current_time[0] += 2  # 2 s > window of 1 s
        results = await _fire(f, 1)
        assert results[0] is None, "Should be allowed after window reset"


# ---------------------------------------------------------------------------
# 429 response contract
# ---------------------------------------------------------------------------


class TestRateLimitResponse:

    @pytest.mark.asyncio
    async def test_429_body_is_json(self):
        import json

        f = RateLimitFilter(default_limit=1, default_window=60)
        await _fire(f, 1)  # consume the single slot
        results = await _fire(f, 1)
        exc = results[0]
        assert isinstance(exc, FilterShortCircuit)
        body = json.loads(exc.body)
        assert body["type"] == "https://slip-stream.dev/errors/rate-limited"
        assert body["title"] == "Rate Limited"
        assert body["status"] == 429
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_retry_after_header_present_on_429(self):
        f = RateLimitFilter(default_limit=1, default_window=60)
        await _fire(f, 1)
        results = await _fire(f, 1)
        exc = results[0]
        assert "Retry-After" in exc.headers
        retry_after = int(exc.headers["Retry-After"])
        assert retry_after > 0

    @pytest.mark.asyncio
    async def test_x_ratelimit_headers_on_429(self):
        f = RateLimitFilter(default_limit=5, default_window=60)
        await _fire(f, 5)  # exhaust
        results = await _fire(f, 1)
        exc = results[0]
        assert exc.headers.get("X-RateLimit-Limit") == "5"
        assert exc.headers.get("X-RateLimit-Remaining") == "0"
        assert "X-RateLimit-Reset" in exc.headers

    @pytest.mark.asyncio
    async def test_retry_after_is_numeric_string(self):
        f = RateLimitFilter(default_limit=1, default_window=30)
        await _fire(f, 1)
        results = await _fire(f, 1)
        exc = results[0]
        # Must be parseable as a positive integer
        value = int(exc.headers["Retry-After"])
        assert value >= 1


# ---------------------------------------------------------------------------
# X-RateLimit-* response headers on successful requests
# ---------------------------------------------------------------------------


class TestResponseHeaders:

    @pytest.mark.asyncio
    async def test_response_headers_attached_after_first_request(self):
        f = RateLimitFilter(default_limit=10, default_window=60)
        request = _make_request()
        ctx = FilterContext()
        await f.on_request(request, ctx)

        response = _make_response()
        result = await f.on_response(request, response, ctx)

        assert result.headers.get("X-RateLimit-Limit") == "10"
        assert result.headers.get("X-RateLimit-Remaining") == "9"
        assert "X-RateLimit-Reset" in result.headers

    @pytest.mark.asyncio
    async def test_remaining_decrements_with_each_request(self):
        f = RateLimitFilter(default_limit=5, default_window=60)
        ip = "2.3.4.5"

        for expected_remaining in [4, 3, 2]:
            request = _make_request(ip=ip)
            ctx = FilterContext()
            await f.on_request(request, ctx)
            response = _make_response()
            await f.on_response(request, response, ctx)
            assert response.headers.get("X-RateLimit-Remaining") == str(
                expected_remaining
            )

    @pytest.mark.asyncio
    async def test_response_passes_through_when_path_skipped(self):
        f = RateLimitFilter(default_limit=5, default_window=60, skip_paths=["/health"])
        request = _make_request(path="/health")
        ctx = FilterContext()
        await f.on_request(request, ctx)  # skip_path — no extras set

        response = _make_response()
        result = await f.on_response(request, response, ctx)

        # No rate-limit headers injected for skipped paths
        assert "X-RateLimit-Limit" not in result.headers

    @pytest.mark.asyncio
    async def test_x_ratelimit_reset_is_epoch_seconds(self):
        f = RateLimitFilter(default_limit=10, default_window=60)
        request = _make_request()
        ctx = FilterContext()
        before = int(time.monotonic())
        await f.on_request(request, ctx)

        response = _make_response()
        await f.on_response(request, response, ctx)

        reset_val = int(response.headers["X-RateLimit-Reset"])
        # Reset should be within the window from now
        assert reset_val >= before
        assert reset_val <= int(time.monotonic()) + 61


# ---------------------------------------------------------------------------
# Per-route limit overrides
# ---------------------------------------------------------------------------


class TestPerRouteLimits:

    @pytest.mark.asyncio
    async def test_per_route_limit_applied(self):
        f = RateLimitFilter(
            default_limit=100,
            default_window=60,
            per_route_limits={"/api/v1/widget/": {"limit": 2, "window": 60}},
        )
        results = await _fire(f, 3, path="/api/v1/widget/")
        assert results[:2] == [None, None]
        assert isinstance(results[2], FilterShortCircuit)

    @pytest.mark.asyncio
    async def test_default_limit_unaffected_by_per_route(self):
        f = RateLimitFilter(
            default_limit=5,
            default_window=60,
            per_route_limits={"/api/v1/widget/": {"limit": 1, "window": 60}},
        )
        # A different route should use the default limit
        results = await _fire(f, 5, path="/api/v1/other/")
        assert all(r is None for r in results)

    @pytest.mark.asyncio
    async def test_longest_prefix_wins(self):
        f = RateLimitFilter(
            default_limit=100,
            default_window=60,
            per_route_limits={
                "/api/": {"limit": 50, "window": 60},
                "/api/v1/widget/": {"limit": 2, "window": 60},
            },
        )
        # The more specific prefix "/api/v1/widget/" should take precedence
        results = await _fire(f, 3, path="/api/v1/widget/123")
        assert isinstance(results[2], FilterShortCircuit)
        # The 429 limit header should reflect the widget-specific limit
        assert results[2].headers["X-RateLimit-Limit"] == "2"

    @pytest.mark.asyncio
    async def test_per_route_window_respected(self):
        current_time = [time.monotonic()]
        f = RateLimitFilter(
            default_limit=100,
            default_window=60,
            per_route_limits={"/narrow/": {"limit": 1, "window": 5}},
            clock=lambda: current_time[0],
        )
        await _fire(f, 1, path="/narrow/")

        # Advance time past the per-route window (5 s)
        current_time[0] += 6
        results = await _fire(f, 1, path="/narrow/")
        assert results[0] is None


# ---------------------------------------------------------------------------
# Per-user limiting
# ---------------------------------------------------------------------------


class TestPerUserLimiting:

    @pytest.mark.asyncio
    async def test_user_in_context_uses_user_key(self):
        f = RateLimitFilter(default_limit=2, default_window=60)

        # Two different IPs, same user — should share quota
        for ip in ("10.0.0.1", "10.0.0.2", "10.0.0.3"):
            request = _make_request(ip=ip)
            ctx = FilterContext(user={"id": "shared-user"})
            try:
                await f.on_request(request, ctx)
            except FilterShortCircuit:
                pass  # expected on 3rd call

        # Third request from any IP for the same user should be blocked
        request = _make_request(ip="10.0.0.99")
        ctx = FilterContext(user={"id": "shared-user"})
        with pytest.raises(FilterShortCircuit) as exc_info:
            await f.on_request(request, ctx)
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_different_users_tracked_independently(self):
        f = RateLimitFilter(default_limit=2, default_window=60)

        # Exhaust user-a quota
        for _ in range(2):
            request = _make_request()
            ctx = FilterContext(user={"id": "user-a"})
            await f.on_request(request, ctx)

        # user-b should still have a full budget
        request = _make_request()
        ctx = FilterContext(user={"id": "user-b"})
        await f.on_request(request, ctx)  # should not raise

    @pytest.mark.asyncio
    async def test_user_key_prefers_id_field(self):
        f = RateLimitFilter(default_limit=1, default_window=60)

        ctx = FilterContext(user={"id": "uid-999", "sub": "sub-999"})
        request = _make_request()
        await f.on_request(request, ctx)

        # A second request for the same user id should be blocked
        ctx2 = FilterContext(user={"id": "uid-999"})
        request2 = _make_request()
        with pytest.raises(FilterShortCircuit):
            await f.on_request(request2, ctx2)

    @pytest.mark.asyncio
    async def test_user_falls_back_to_sub_when_no_id(self):
        f = RateLimitFilter(default_limit=1, default_window=60)

        ctx = FilterContext(user={"sub": "oauth-subject"})
        request = _make_request()
        await f.on_request(request, ctx)

        ctx2 = FilterContext(user={"sub": "oauth-subject"})
        request2 = _make_request()
        with pytest.raises(FilterShortCircuit):
            await f.on_request(request2, ctx2)


# ---------------------------------------------------------------------------
# Custom key function
# ---------------------------------------------------------------------------


class TestCustomKeyFunction:

    @pytest.mark.asyncio
    async def test_custom_key_func_used(self):
        # Partition all traffic into a single bucket named "global"
        f = RateLimitFilter(
            default_limit=2,
            default_window=60,
            key_func=lambda req, ctx: "global",
        )

        # Two different IPs both consume from the same "global" bucket
        results_a = await _fire(f, 1, ip="10.0.0.1")
        results_b = await _fire(f, 1, ip="10.0.0.2")
        assert results_a[0] is None
        assert results_b[0] is None

        # Third request (any IP) should be blocked
        results_c = await _fire(f, 1, ip="10.0.0.3")
        assert isinstance(results_c[0], FilterShortCircuit)

    @pytest.mark.asyncio
    async def test_custom_key_func_receives_request_and_context(self):
        received: list = []

        def capturing_key(req, ctx):
            received.append((req.url.path, ctx.user))
            return req.url.path  # key by path

        f = RateLimitFilter(default_limit=5, default_window=60, key_func=capturing_key)
        request = _make_request(path="/special/")
        ctx = FilterContext(user={"id": "u1"})
        await f.on_request(request, ctx)

        assert received[0] == ("/special/", {"id": "u1"})


# ---------------------------------------------------------------------------
# Skip paths
# ---------------------------------------------------------------------------


class TestSkipPaths:

    @pytest.mark.asyncio
    async def test_skip_path_bypasses_rate_limit(self):
        f = RateLimitFilter(
            default_limit=1,
            default_window=60,
            skip_paths=["/health", "/docs"],
        )
        # Would normally be blocked after 1 request
        results = await _fire(f, 5, path="/health")
        assert all(r is None for r in results)

    @pytest.mark.asyncio
    async def test_skip_applies_to_subpaths(self):
        f = RateLimitFilter(
            default_limit=1,
            default_window=60,
            skip_paths=["/internal"],
        )
        results = await _fire(f, 3, path="/internal/metrics")
        assert all(r is None for r in results)

    @pytest.mark.asyncio
    async def test_non_skipped_path_still_limited(self):
        f = RateLimitFilter(
            default_limit=1,
            default_window=60,
            skip_paths=["/health"],
        )
        results = await _fire(f, 2, path="/api/data")
        assert isinstance(results[1], FilterShortCircuit)


# ---------------------------------------------------------------------------
# X-Forwarded-For header handling
# ---------------------------------------------------------------------------


class TestForwardedFor:

    @pytest.mark.asyncio
    async def test_forwarded_for_ignored_by_default(self):
        """By default, X-Forwarded-For is NOT trusted — uses client IP instead."""
        f = RateLimitFilter(default_limit=1, default_window=60)

        # Two requests from different proxied IPs but same client host
        req1 = _make_request(
            ip="192.168.1.1",
            headers={"x-forwarded-for": "10.0.0.1"},
        )
        req2 = _make_request(
            ip="192.168.1.1",
            headers={"x-forwarded-for": "10.0.0.2"},
        )

        await f.on_request(req1, FilterContext())
        # Same client IP (192.168.1.1) so should be blocked even though
        # X-Forwarded-For differs
        with pytest.raises(FilterShortCircuit):
            await f.on_request(req2, FilterContext())

    @pytest.mark.asyncio
    async def test_forwarded_for_used_when_trusted(self):
        """When trust_forwarded_for=True, X-Forwarded-For is used as key."""
        f = RateLimitFilter(
            default_limit=1, default_window=60, trust_forwarded_for=True
        )

        # Request comes from 10.0.0.1 via proxy at 192.168.1.1
        request = _make_request(
            ip="192.168.1.1",
            headers={"x-forwarded-for": "10.0.0.1, 192.168.1.1"},
        )
        ctx = FilterContext()
        await f.on_request(request, ctx)  # consumes 10.0.0.1's slot

        # Second request with the same X-Forwarded-For should be blocked
        request2 = _make_request(
            ip="192.168.1.1",
            headers={"x-forwarded-for": "10.0.0.1, 192.168.1.1"},
        )
        ctx2 = FilterContext()
        with pytest.raises(FilterShortCircuit):
            await f.on_request(request2, ctx2)

    @pytest.mark.asyncio
    async def test_x_forwarded_for_used_as_key(self):
        f = RateLimitFilter(
            default_limit=1, default_window=60, trust_forwarded_for=True
        )

        # Request comes from 10.0.0.1 via proxy at 192.168.1.1
        request = _make_request(
            ip="192.168.1.1",
            headers={"x-forwarded-for": "10.0.0.1, 192.168.1.1"},
        )
        ctx = FilterContext()
        await f.on_request(request, ctx)  # consumes 10.0.0.1's slot

        # Second request with the same X-Forwarded-For should be blocked
        request2 = _make_request(
            ip="192.168.1.1",
            headers={"x-forwarded-for": "10.0.0.1, 192.168.1.1"},
        )
        ctx2 = FilterContext()
        with pytest.raises(FilterShortCircuit):
            await f.on_request(request2, ctx2)

    @pytest.mark.asyncio
    async def test_different_forwarded_ips_tracked_separately(self):
        f = RateLimitFilter(
            default_limit=1, default_window=60, trust_forwarded_for=True
        )

        req_a = _make_request(headers={"x-forwarded-for": "11.0.0.1"})
        req_b = _make_request(headers={"x-forwarded-for": "11.0.0.2"})

        await f.on_request(req_a, FilterContext())
        await f.on_request(req_b, FilterContext())  # different IP, should pass


# ---------------------------------------------------------------------------
# Auto-cleanup of expired entries
# ---------------------------------------------------------------------------


class TestAutoCleanup:

    @pytest.mark.asyncio
    async def test_cleanup_removes_expired_entries(self):
        current_time = [time.monotonic()]
        f = RateLimitFilter(
            default_limit=5, default_window=1, clock=lambda: current_time[0]
        )

        # Populate two keys
        await _fire(f, 1, ip="1.1.1.1")
        await _fire(f, 1, ip="2.2.2.2")
        assert len(f._store) == 2

        # Advance time so all entries are expired
        current_time[0] += 5  # well past the 1 s window
        removed = await f.cleanup_expired()

        assert removed == 2
        assert len(f._store) == 0

    @pytest.mark.asyncio
    async def test_cleanup_only_removes_expired_keys(self):
        f = RateLimitFilter(default_limit=5, default_window=60)

        # Populate one key; it won't expire for 60 s
        await _fire(f, 1, ip="3.3.3.3")
        assert len(f._store) == 1

        removed = await f.cleanup_expired()
        assert removed == 0
        assert len(f._store) == 1

    @pytest.mark.asyncio
    async def test_eviction_happens_on_next_request_for_key(self):
        """Old timestamps are pruned inline when the key makes a new request."""
        current_time = [time.monotonic()]
        f = RateLimitFilter(
            default_limit=2, default_window=1, clock=lambda: current_time[0]
        )

        await _fire(f, 2, ip="4.4.4.4")  # fill the window

        # Advance past the window so the old timestamps expire
        current_time[0] += 2
        # The next request should succeed because old stamps were evicted
        results = await _fire(f, 1, ip="4.4.4.4")
        assert results[0] is None


# ---------------------------------------------------------------------------
# Thread / concurrency safety
# ---------------------------------------------------------------------------


class TestConcurrency:

    @pytest.mark.asyncio
    async def test_concurrent_requests_respect_limit(self):
        """Under concurrent load, the filter must not allow more than limit requests."""
        limit = 10
        f = RateLimitFilter(default_limit=limit, default_window=60)
        total_requests = 20

        async def make_request():
            request = _make_request(ip="5.5.5.5")
            ctx = FilterContext()
            try:
                await f.on_request(request, ctx)
                return "ok"
            except FilterShortCircuit as exc:
                return exc.status_code

        results = await asyncio.gather(*[make_request() for _ in range(total_requests)])
        ok_count = sum(1 for r in results if r == "ok")
        blocked_count = sum(1 for r in results if r == 429)

        assert ok_count == limit
        assert blocked_count == total_requests - limit


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:

    @pytest.mark.asyncio
    async def test_limit_of_1_allows_exactly_one_request(self):
        f = RateLimitFilter(default_limit=1, default_window=60)
        results = await _fire(f, 2)
        assert results[0] is None
        assert isinstance(results[1], FilterShortCircuit)

    @pytest.mark.asyncio
    async def test_request_without_client_uses_unknown_key(self):
        f = RateLimitFilter(default_limit=2, default_window=60)
        request = SimpleNamespace(
            method="GET",
            url=SimpleNamespace(path="/test/"),
            headers=Headers({}),
            client=None,
        )
        ctx = FilterContext()
        await f.on_request(request, ctx)  # should not raise

    @pytest.mark.asyncio
    async def test_on_response_returns_response_object(self):
        f = RateLimitFilter(default_limit=5, default_window=60)
        request = _make_request()
        ctx = FilterContext()
        await f.on_request(request, ctx)

        response = _make_response()
        result = await f.on_response(request, response, ctx)
        assert result is response

    @pytest.mark.asyncio
    async def test_on_response_without_on_request_noop(self):
        """on_response must be safe even if on_request was never called."""
        f = RateLimitFilter()
        request = _make_request()
        ctx = FilterContext()  # no _rate_limit in extras
        response = _make_response()
        result = await f.on_response(request, response, ctx)
        assert result is response
        assert "X-RateLimit-Limit" not in result.headers
