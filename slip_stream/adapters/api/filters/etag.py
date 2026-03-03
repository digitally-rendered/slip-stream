"""ETag and conditional request filter.

Generates ETags for responses and handles conditional request headers:
- If-None-Match on GET → 304 Not Modified
- If-Match on PATCH/DELETE → 412 Precondition Failed (via EventBus hook)

ETag strategies:
- Single entity: W/"{entity_id}:{record_version}" (weak, version-based)
- List/other: W/"list:{sha256(body)[:16]}" (weak, content-hash)

Order 85 — runs after projection (95) but before envelope (90) on
the response side, so ETags reflect entity data, not envelope metadata.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from starlette.requests import Request
from starlette.responses import Response

from slip_stream.adapters.api.filters.base import FilterBase, FilterContext

logger = logging.getLogger(__name__)


class ETagFilter(FilterBase):
    """Filter that generates ETags and handles conditional request headers.

    On the request side, parses ``If-None-Match`` and ``If-Match`` headers and
    stores them in ``context.extras`` for later evaluation.

    On the response side, computes a weak ETag for the response body and:
    - For GET requests with a matching ``If-None-Match``, returns ``304 Not
      Modified`` (saving bandwidth).
    - For all successful non-204 responses, sets the ``ETag`` response header.

    Precondition checks for mutation operations (PATCH / DELETE) are enforced
    via an ``EventBus`` hook registered at construction time.  The hook reads
    the ``If-Match`` value stored in ``filter_context.extras`` and compares it
    against the hydrated entity's ``entity_id:record_version``.  A mismatch
    raises ``HookError(412, ...)`` which the endpoint handler converts to an
    HTTP 412 response.

    Attributes:
        order: 85 — response phase runs after projection (95) so ETags cover
            entity fields, not the envelope (90) wrapper.
    """

    order: int = 85

    def __init__(
        self,
        event_bus: Any = None,
        enable_precondition_checks: bool = True,
    ) -> None:
        """Initialise the filter.

        Args:
            event_bus: Optional :class:`~slip_stream.core.events.EventBus`
                instance.  When provided and *enable_precondition_checks* is
                ``True``, a ``pre_update`` and ``pre_delete`` hook is
                registered to enforce ``If-Match`` preconditions.
            enable_precondition_checks: Whether to register the ``pre_update``
                / ``pre_delete`` hooks.  Set to ``False`` to generate ETags
                without enforcing write preconditions.
        """
        self._event_bus = event_bus
        self._enable_precondition_checks = enable_precondition_checks

        if event_bus is not None and enable_precondition_checks:
            event_bus.register("pre_update", ETagFilter._precondition_hook)
            event_bus.register("pre_delete", ETagFilter._precondition_hook)

    # ------------------------------------------------------------------
    # FilterBase interface
    # ------------------------------------------------------------------

    async def on_request(self, request: Request, context: FilterContext) -> None:
        """Parse conditional request headers and store in extras."""
        if_none_match = request.headers.get("if-none-match")
        if if_none_match:
            context.extras["if_none_match"] = if_none_match.strip()

        if_match = request.headers.get("if-match")
        if if_match:
            context.extras["if_match"] = if_match.strip()

    async def on_response(
        self, request: Request, response: Response, context: FilterContext
    ) -> Response:
        """Compute and attach ETag; handle If-None-Match conditional GETs."""
        # Skip error responses and no-content responses.
        if response.status_code >= 400 or response.status_code == 204:
            return response

        body = await self._read_body(response)
        if not body:
            return response

        etag = self._compute_etag(body)
        if etag is None:
            return response

        # For GET requests check If-None-Match.
        if request.method.upper() == "GET":
            if_none_match = context.extras.get("if_none_match")
            if if_none_match and self._etags_match(if_none_match, etag):
                return Response(
                    status_code=304,
                    headers={"ETag": etag},
                )

        # Rebuild the response with the ETag header preserved.  We need to
        # reconstruct because Starlette responses may have already streamed.
        new_headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in ("content-length", "content-type")
        }
        new_headers["ETag"] = etag

        return Response(
            content=body,
            status_code=response.status_code,
            headers=new_headers,
            media_type=response.media_type or "application/json",
        )

    # ------------------------------------------------------------------
    # ETag computation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_etag(body: bytes) -> Optional[str]:
        """Compute a weak ETag for the response body.

        For a single entity (JSON object with both ``entity_id`` and
        ``record_version`` fields) returns a deterministic version-based tag::

            W/"<entity_id>:<record_version>"

        For a list (JSON array) returns a content-hash tag::

            W/"list:<sha256[:16]>"

        Returns ``None`` when the body cannot be parsed as JSON or does not
        match either pattern.
        """
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

        if isinstance(data, dict):
            entity_id = data.get("entity_id")
            record_version = data.get("record_version")
            if entity_id is not None and record_version is not None:
                return f'W/"{entity_id}:{record_version}"'

        if isinstance(data, list):
            digest = hashlib.sha256(body).hexdigest()[:16]
            return f'W/"list:{digest}"'

        return None

    # ------------------------------------------------------------------
    # ETag comparison
    # ------------------------------------------------------------------

    @staticmethod
    def _etags_match(header_value: str, current_etag: str) -> bool:
        """Return True if *current_etag* matches any tag in *header_value*.

        Handles:
        - Wildcard ``*`` — matches any ETag.
        - Comma-separated list — matches if any individual tag equals
          *current_etag* (weak comparison: strips the ``W/`` prefix before
          comparing quoted strings).
        """
        stripped = header_value.strip()
        if stripped == "*":
            return True

        # Normalise the current ETag for comparison (strip W/ prefix).
        def _normalise(tag: str) -> str:
            tag = tag.strip()
            if tag.startswith("W/"):
                tag = tag[2:]
            return tag

        current_norm = _normalise(current_etag)

        for candidate in stripped.split(","):
            if _normalise(candidate) == current_norm:
                return True

        return False

    # ------------------------------------------------------------------
    # EventBus precondition hook
    # ------------------------------------------------------------------

    @staticmethod
    async def _precondition_hook(ctx: Any) -> None:
        """EventBus hook that enforces If-Match preconditions on writes.

        Registered on ``pre_update`` and ``pre_delete`` when an EventBus is
        provided.  Reads ``if_match`` from ``filter_context.extras`` (set by
        :meth:`on_request`) and compares it against the hydrated entity.

        Raises:
            HookError: With status 412 when the precondition fails.
        """
        # Import here to avoid circular imports (adapters must not import core
        # at module level in a way that violates hex boundaries).
        from slip_stream.core.events import HookError  # noqa: PLC0415

        # Locate the filter context that holds our If-Match value.
        filter_ctx = None
        if ctx.request is not None:
            filter_ctx = getattr(ctx.request.state, "filter_context", None)

        if filter_ctx is None:
            return

        if_match = filter_ctx.extras.get("if_match")
        if not if_match:
            # No If-Match header — skip precondition enforcement.
            return

        # Wildcard always passes.
        if if_match.strip() == "*":
            return

        # We need the hydrated entity to build the current ETag.
        entity = ctx.entity
        if entity is None:
            # Entity not yet loaded — cannot check.
            return

        entity_id = getattr(entity, "entity_id", None)
        record_version = getattr(entity, "record_version", None)

        if entity_id is None or record_version is None:
            return

        current_etag = f'W/"{entity_id}:{record_version}"'

        if not ETagFilter._etags_match(if_match, current_etag):
            raise HookError(
                status_code=412,
                detail=(
                    f"Precondition Failed: entity has changed. "
                    f"Current ETag is {current_etag}."
                ),
            )
