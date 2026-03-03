"""Tests for ETagFilter (ETag generation and conditional request handling)."""

import json
import uuid
from unittest.mock import MagicMock

import pytest
from starlette.requests import Request
from starlette.responses import Response

from slip_stream.adapters.api.filters.base import FilterContext
from slip_stream.adapters.api.filters.etag import ETagFilter
from slip_stream.core.events import EventBus, HookError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    method: str = "GET",
    path: str = "/api/v1/widget/",
    headers: list | None = None,
) -> Request:
    """Build a minimal Starlette Request for filter testing."""
    raw_headers = []
    for name, value in headers or []:
        raw_headers.append((name.lower().encode("latin-1"), value.encode("latin-1")))
    scope = {
        "type": "http",
        "method": method.upper(),
        "path": path,
        "headers": raw_headers,
        "query_string": b"",
    }
    return Request(scope)


def _make_response(data, status_code: int = 200) -> Response:
    return Response(
        content=json.dumps(data),
        status_code=status_code,
        media_type="application/json",
    )


def _entity_payload(
    entity_id: str | None = None,
    record_version: int = 1,
    **extra,
) -> dict:
    """Return a dict that looks like a versioned entity response."""
    return {
        "entity_id": entity_id or str(uuid.uuid4()),
        "record_version": record_version,
        "name": "Widget",
        **extra,
    }


# ---------------------------------------------------------------------------
# Unit tests — ETagFilter
# ---------------------------------------------------------------------------


class TestETagSingleEntity:
    """ETag generation for single-entity responses."""

    @pytest.mark.asyncio
    async def test_etag_single_entity(self):
        """Response with entity_id + record_version gets a version-based ETag."""
        f = ETagFilter()
        request = _make_request()
        context = FilterContext()
        await f.on_request(request, context)

        entity_id = "abc123"
        payload = _entity_payload(entity_id=entity_id, record_version=3)
        response = _make_response(payload)
        result = await f.on_response(request, response, context)

        assert result.headers["etag"] == f'W/"{entity_id}:3"'
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_no_conditional_headers(self):
        """Normal GET with no conditional header still gets ETag set."""
        f = ETagFilter()
        request = _make_request()
        context = FilterContext()
        await f.on_request(request, context)

        payload = _entity_payload(entity_id="xyz", record_version=1)
        response = _make_response(payload)
        result = await f.on_response(request, response, context)

        assert "etag" in result.headers
        assert result.status_code == 200


class TestETagList:
    """ETag generation for list responses."""

    @pytest.mark.asyncio
    async def test_etag_list(self):
        """List response gets a content-hash-based ETag."""
        f = ETagFilter()
        request = _make_request()
        context = FilterContext()
        await f.on_request(request, context)

        items = [_entity_payload(), _entity_payload()]
        response = _make_response(items)
        result = await f.on_response(request, response, context)

        etag = result.headers.get("etag", "")
        assert etag.startswith('W/"list:')
        assert etag.endswith('"')
        # Hash portion is 16 hex characters.
        hash_part = etag[len('W/"list:') : -1]
        assert len(hash_part) == 16


class TestIfNoneMatch:
    """Conditional GET — If-None-Match handling."""

    @pytest.mark.asyncio
    async def test_if_none_match_hit_returns_304(self):
        """GET with matching If-None-Match returns 304 Not Modified."""
        entity_id = str(uuid.uuid4())
        record_version = 2
        etag = f'W/"{entity_id}:{record_version}"'

        f = ETagFilter()
        request = _make_request(
            headers=[("if-none-match", etag)],
        )
        context = FilterContext()
        await f.on_request(request, context)

        payload = _entity_payload(entity_id=entity_id, record_version=record_version)
        response = _make_response(payload)
        result = await f.on_response(request, response, context)

        assert result.status_code == 304
        assert result.headers["etag"] == etag

    @pytest.mark.asyncio
    async def test_if_none_match_miss_returns_full_response(self):
        """GET with non-matching If-None-Match returns 200 + ETag."""
        entity_id = str(uuid.uuid4())
        f = ETagFilter()
        request = _make_request(
            headers=[("if-none-match", 'W/"other-entity:99"')],
        )
        context = FilterContext()
        await f.on_request(request, context)

        payload = _entity_payload(entity_id=entity_id, record_version=1)
        response = _make_response(payload)
        result = await f.on_response(request, response, context)

        assert result.status_code == 200
        assert "etag" in result.headers
        assert result.headers["etag"] != 'W/"other-entity:99"'

    @pytest.mark.asyncio
    async def test_if_none_match_wildcard(self):
        """If-None-Match: * matches any entity and returns 304."""
        f = ETagFilter()
        request = _make_request(headers=[("if-none-match", "*")])
        context = FilterContext()
        await f.on_request(request, context)

        payload = _entity_payload(record_version=1)
        response = _make_response(payload)
        result = await f.on_response(request, response, context)

        assert result.status_code == 304

    @pytest.mark.asyncio
    async def test_if_none_match_multiple_etags(self):
        """Comma-separated If-None-Match list: 304 when any tag matches."""
        entity_id = str(uuid.uuid4())
        record_version = 5
        matching_etag = f'W/"{entity_id}:{record_version}"'
        header = f'W/"stale-tag:1", {matching_etag}, W/"other:2"'

        f = ETagFilter()
        request = _make_request(headers=[("if-none-match", header)])
        context = FilterContext()
        await f.on_request(request, context)

        payload = _entity_payload(entity_id=entity_id, record_version=record_version)
        response = _make_response(payload)
        result = await f.on_response(request, response, context)

        assert result.status_code == 304


class TestSkippedResponses:
    """ETag filter skips error and no-content responses."""

    @pytest.mark.asyncio
    async def test_etag_skips_error_responses(self):
        """400+ responses pass through without an ETag header."""
        f = ETagFilter()
        request = _make_request()
        context = FilterContext()
        await f.on_request(request, context)

        response = _make_response({"detail": "Not Found"}, status_code=404)
        result = await f.on_response(request, response, context)

        assert result.status_code == 404
        assert "etag" not in result.headers

    @pytest.mark.asyncio
    async def test_etag_skips_204_responses(self):
        """204 No Content passes through without an ETag header."""
        f = ETagFilter()
        request = _make_request(method="DELETE")
        context = FilterContext()
        await f.on_request(request, context)

        response = Response(status_code=204)
        result = await f.on_response(request, response, context)

        assert result.status_code == 204
        assert "etag" not in result.headers


class TestIfMatchPrecondition:
    """If-Match precondition enforcement via EventBus hook."""

    @pytest.mark.asyncio
    async def test_if_match_precondition_success(self):
        """Matching If-Match header allows the write (no HookError raised)."""
        entity_id = uuid.uuid4()
        record_version = 3
        etag = f'W/"{entity_id}:{record_version}"'

        # Build a mock RequestContext with a hydrated entity.
        entity = MagicMock()
        entity.entity_id = entity_id
        entity.record_version = record_version

        filter_ctx = FilterContext()
        filter_ctx.extras["if_match"] = etag

        request_state = MagicMock()
        request_state.filter_context = filter_ctx

        ctx = MagicMock()
        ctx.entity = entity
        ctx.request = MagicMock()
        ctx.request.state = request_state

        # Should not raise.
        await ETagFilter._precondition_hook(ctx)

    @pytest.mark.asyncio
    async def test_if_match_precondition_failure(self):
        """Mismatching If-Match header raises HookError with 412."""
        entity_id = uuid.uuid4()
        record_version = 3

        entity = MagicMock()
        entity.entity_id = entity_id
        entity.record_version = record_version

        filter_ctx = FilterContext()
        # Supply a stale ETag.
        filter_ctx.extras["if_match"] = f'W/"{entity_id}:1"'

        request_state = MagicMock()
        request_state.filter_context = filter_ctx

        ctx = MagicMock()
        ctx.entity = entity
        ctx.request = MagicMock()
        ctx.request.state = request_state

        with pytest.raises(HookError) as exc_info:
            await ETagFilter._precondition_hook(ctx)

        assert exc_info.value.status_code == 412

    @pytest.mark.asyncio
    async def test_if_match_wildcard(self):
        """If-Match: * always passes regardless of entity version."""
        entity = MagicMock()
        entity.entity_id = uuid.uuid4()
        entity.record_version = 99

        filter_ctx = FilterContext()
        filter_ctx.extras["if_match"] = "*"

        request_state = MagicMock()
        request_state.filter_context = filter_ctx

        ctx = MagicMock()
        ctx.entity = entity
        ctx.request = MagicMock()
        ctx.request.state = request_state

        # Should not raise.
        await ETagFilter._precondition_hook(ctx)

    @pytest.mark.asyncio
    async def test_precondition_skipped_when_no_if_match(self):
        """When no If-Match header is present, the hook is a no-op."""
        entity = MagicMock()
        entity.entity_id = uuid.uuid4()
        entity.record_version = 1

        filter_ctx = FilterContext()
        # Deliberately no "if_match" key in extras.

        request_state = MagicMock()
        request_state.filter_context = filter_ctx

        ctx = MagicMock()
        ctx.entity = entity
        ctx.request = MagicMock()
        ctx.request.state = request_state

        # Should not raise.
        await ETagFilter._precondition_hook(ctx)


class TestEventBusRegistration:
    """ETagFilter registers hooks on EventBus at construction time."""

    def test_hooks_registered_on_event_bus(self):
        """Providing an EventBus causes pre_update and pre_delete hooks to register."""
        bus = EventBus()
        assert bus.handler_count == 0

        ETagFilter(event_bus=bus, enable_precondition_checks=True)

        # Two hooks should be registered: pre_update and pre_delete.
        assert bus.handler_count == 2

    def test_no_hooks_when_precondition_disabled(self):
        """When enable_precondition_checks=False, no hooks are registered."""
        bus = EventBus()
        ETagFilter(event_bus=bus, enable_precondition_checks=False)
        assert bus.handler_count == 0

    def test_no_hooks_without_event_bus(self):
        """Constructing without an EventBus does not raise."""
        f = ETagFilter()
        assert f._event_bus is None

    def test_order_is_85(self):
        """ETagFilter.order must be 85."""
        assert ETagFilter.order == 85


class TestComputeEtag:
    """Unit tests for _compute_etag static method."""

    def test_entity_with_entity_id_and_record_version(self):
        payload = {"entity_id": "abc", "record_version": 7, "name": "Widget"}
        body = json.dumps(payload).encode()
        etag = ETagFilter._compute_etag(body)
        assert etag == 'W/"abc:7"'

    def test_list_payload(self):
        payload = [{"entity_id": "a", "record_version": 1}]
        body = json.dumps(payload).encode()
        etag = ETagFilter._compute_etag(body)
        assert etag is not None
        assert etag.startswith('W/"list:')

    def test_dict_missing_record_version(self):
        payload = {"entity_id": "abc"}
        body = json.dumps(payload).encode()
        etag = ETagFilter._compute_etag(body)
        assert etag is None

    def test_non_json_body(self):
        etag = ETagFilter._compute_etag(b"not json")
        assert etag is None


class TestETagsMatch:
    """Unit tests for _etags_match static method."""

    def test_exact_match(self):
        assert ETagFilter._etags_match('W/"abc:1"', 'W/"abc:1"') is True

    def test_no_match(self):
        assert ETagFilter._etags_match('W/"abc:1"', 'W/"abc:2"') is False

    def test_wildcard(self):
        assert ETagFilter._etags_match("*", 'W/"anything:99"') is True

    def test_comma_list_one_matches(self):
        header = 'W/"stale:1", W/"abc:5"'
        assert ETagFilter._etags_match(header, 'W/"abc:5"') is True

    def test_comma_list_none_match(self):
        header = 'W/"stale:1", W/"other:2"'
        assert ETagFilter._etags_match(header, 'W/"abc:5"') is False

    def test_weak_vs_strong_comparison(self):
        """Weak comparison strips W/ prefix."""
        assert ETagFilter._etags_match('"abc:1"', 'W/"abc:1"') is True
