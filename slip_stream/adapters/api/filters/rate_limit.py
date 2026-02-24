"""Sliding-window rate limiting filter for the ASGI filter chain.

Provides in-memory, per-key rate limiting with no external dependencies.
Runs at order=2 so it fires before the Rego policy filter (order=3) and
the auth filter (order=10), guarding the server even for unauthenticated
traffic.

Usage::

    from slip_stream.adapters.api.filters.rate_limit import RateLimitFilter

    rate_limit = RateLimitFilter(
        default_limit=100,
        default_window=60,
        per_route_limits={
            "/api/v1/widget/": {"limit": 10, "window": 60},
        },
    )
    slip = SlipStream(app=app, schema_dir=..., filters=[rate_limit])
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Callable, Dict, List, Optional, Tuple

from starlette.requests import Request
from starlette.responses import Response

from slip_stream.adapters.api.filters.base import (
    FilterBase,
    FilterContext,
    FilterShortCircuit,
)

logger = logging.getLogger(__name__)

# Type alias: a deque of timestamps (floats) stored per rate-limit key.
_Timestamps = deque


class RateLimitFilter(FilterBase):
    """ASGI filter that enforces sliding-window rate limits.

    Each request is identified by a *key* (IP address by default, or a
    caller-supplied value from ``key_func``).  If the key has made more than
    ``limit`` requests within the last ``window`` seconds the filter raises a
    :class:`~slip_stream.adapters.api.filters.base.FilterShortCircuit` with
    status 429 and a ``Retry-After`` header.

    Rate-limit headers (``X-RateLimit-Limit``, ``X-RateLimit-Remaining``,
    ``X-RateLimit-Reset``) are attached to every response via
    ``context.extras`` so that ``on_response`` can write them without
    needing to re-compute the window.

    Args:
        default_limit: Maximum requests allowed per window (global default).
        default_window: Window duration in seconds (global default).
        per_route_limits: Optional mapping of URL path prefixes to
            ``{"limit": int, "window": int}`` overrides.  The longest
            matching prefix wins.
        key_func: Optional callable ``(request, context) -> str`` that
            returns the rate-limit key for the request.  Defaults to the
            client IP address (``request.client.host``).
        skip_paths: URL path prefixes that bypass rate limiting entirely.

    Attributes:
        order: 2 — runs before policy (3) and auth (10).
    """

    order = 2

    def __init__(
        self,
        default_limit: int = 100,
        default_window: int = 60,
        per_route_limits: Optional[Dict[str, Dict[str, int]]] = None,
        key_func: Optional[Callable[[Request, FilterContext], str]] = None,
        skip_paths: Optional[List[str]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.default_limit = default_limit
        self.default_window = default_window
        self.per_route_limits: Dict[str, Dict[str, int]] = per_route_limits or {}
        self._key_func = key_func
        self.skip_paths: List[str] = skip_paths or []
        self._clock = clock or time.monotonic

        # ``_store`` maps rate-limit key -> deque of request timestamps.
        self._store: Dict[str, _Timestamps] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # FilterBase interface
    # ------------------------------------------------------------------

    async def on_request(
        self, request: Request, context: FilterContext
    ) -> None:
        """Check the rate limit and short-circuit with 429 when exceeded."""
        path = request.url.path

        for skip in self.skip_paths:
            if path.startswith(skip):
                return

        limit, window = self._resolve_limit(path, context)
        key = self._build_key(request, context)
        now = self._clock()

        async with self._lock:
            self._evict_expired(key, now, window)
            timestamps = self._store.setdefault(key, deque())
            count = len(timestamps)

            remaining = max(0, limit - count - 1)
            reset_in = self._reset_in(timestamps, window, now)

            if count >= limit:
                retry_after = int(reset_in) + 1
                logger.info(
                    "Rate limit exceeded for key=%s path=%s limit=%d window=%ds",
                    key, path, limit, window,
                )
                raise FilterShortCircuit(
                    status_code=429,
                    body=json.dumps({
                        "type": "https://slip-stream.dev/errors/rate-limited",
                        "title": "Rate Limited",
                        "status": 429,
                        "detail": (
                            f"Rate limit of {limit} requests per {window}s exceeded. "
                            f"Retry after {retry_after}s."
                        ),
                        "instance": path,
                    }),
                    headers={
                        "Content-Type": "application/problem+json",
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(now + reset_in)),
                    },
                )

            # Record this request timestamp and stash header data for response.
            timestamps.append(now)
            context.extras["_rate_limit"] = {
                "limit": limit,
                "remaining": remaining,
                "reset": int(now + reset_in) if reset_in > 0 else int(now + window),
            }

    async def on_response(
        self, request: Request, response: Response, context: FilterContext
    ) -> Response:
        """Attach X-RateLimit-* headers to every successful response."""
        rl = context.extras.get("_rate_limit")
        if rl is None:
            return response

        response.headers["X-RateLimit-Limit"] = str(rl["limit"])
        response.headers["X-RateLimit-Remaining"] = str(rl["remaining"])
        response.headers["X-RateLimit-Reset"] = str(rl["reset"])
        return response

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_limit(
        self, path: str, context: FilterContext
    ) -> Tuple[int, int]:
        """Return (limit, window) for the given path.

        Iterates ``per_route_limits`` and picks the longest matching prefix
        so that ``/api/v1/widget/123`` correctly inherits ``/api/v1/widget/``
        overrides.
        """
        best_prefix = ""
        best_cfg: Optional[Dict[str, int]] = None

        for prefix, cfg in self.per_route_limits.items():
            if path.startswith(prefix) and len(prefix) > len(best_prefix):
                best_prefix = prefix
                best_cfg = cfg

        if best_cfg is not None:
            return best_cfg.get("limit", self.default_limit), best_cfg.get("window", self.default_window)

        return self.default_limit, self.default_window

    def _build_key(self, request: Request, context: FilterContext) -> str:
        """Return the rate-limit key for this request.

        When ``context.user`` is set and no custom ``key_func`` was provided,
        the key is ``"user:<user_id>"`` so that per-user limits are enforced
        regardless of the originating IP.  Falls back to the client IP.
        """
        if self._key_func is not None:
            return self._key_func(request, context)

        if context.user:
            user_id = context.user.get("id") or context.user.get("sub") or context.user.get("username")
            if user_id:
                return f"user:{user_id}"

        # Fall back to client IP, honouring the X-Forwarded-For header when
        # the application is behind a trusted reverse proxy.
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            # Take the leftmost (original client) address only.
            return forwarded_for.split(",")[0].strip()

        if request.client:
            return request.client.host

        return "unknown"

    def _evict_expired(self, key: str, now: float, window: int) -> None:
        """Remove timestamps outside the current sliding window.

        Must be called while the caller holds ``self._lock``.
        """
        timestamps = self._store.get(key)
        if timestamps is None:
            return

        cutoff = now - window
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

        # Drop the deque entirely when empty to keep memory bounded.
        if not timestamps:
            del self._store[key]

    def _reset_in(
        self, timestamps: _Timestamps, window: int, now: float
    ) -> float:
        """Seconds until the oldest in-window timestamp expires.

        Returns ``0.0`` when the deque is empty (window is already clear).
        """
        if not timestamps:
            return 0.0
        oldest = timestamps[0]
        return max(0.0, (oldest + window) - now)

    async def cleanup_expired(self) -> int:
        """Remove all expired entries from the store.

        Intended to be called periodically (e.g. from a background task).
        Returns the number of keys removed.
        """
        now = self._clock()
        removed = 0
        async with self._lock:
            keys_to_check = list(self._store.keys())
            for key in keys_to_check:
                timestamps = self._store.get(key)
                if timestamps is None:
                    continue
                # Use the default window for cleanup; per-route window
                # discrepancies result in conservative (safe) retention.
                cutoff = now - self.default_window
                while timestamps and timestamps[0] < cutoff:
                    timestamps.popleft()
                if not timestamps:
                    del self._store[key]
                    removed += 1
        return removed
