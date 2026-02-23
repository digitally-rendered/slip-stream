"""Tests for ResponseEnvelopeFilter."""

import json

import pytest
from starlette.requests import Request
from starlette.responses import Response
from starlette.testclient import TestClient
from fastapi import FastAPI

from slip_stream.adapters.api.filters.base import FilterContext
from slip_stream.adapters.api.filters.chain import FilterChain
from slip_stream.adapters.api.filters.envelope import ResponseEnvelopeFilter
from slip_stream.adapters.api.filters.middleware import FilterChainMiddleware


def _make_request(path: str = "/api/v1/widget/", query_string: str = "") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": query_string.encode(),
    }
    return Request(scope)


def _make_response(data, status_code: int = 200) -> Response:
    return Response(
        content=json.dumps(data),
        status_code=status_code,
        media_type="application/json",
    )


class TestResponseEnvelopeFilter:
    """Unit tests for the envelope filter."""

    @pytest.mark.asyncio
    async def test_wraps_dict_response(self):
        f = ResponseEnvelopeFilter()
        request = _make_request()
        context = FilterContext()
        await f.on_request(request, context)

        response = _make_response({"name": "Widget"})
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        assert "data" in body
        assert "meta" in body
        assert body["data"] == {"name": "Widget"}
        assert "request_id" in body["meta"]

    @pytest.mark.asyncio
    async def test_wraps_list_with_pagination(self):
        f = ResponseEnvelopeFilter(include_pagination=True)
        request = _make_request(query_string="skip=10&limit=25")
        context = FilterContext()
        await f.on_request(request, context)

        items = [{"name": "A"}, {"name": "B"}]
        response = _make_response(items)
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        assert body["data"] == items
        assert body["meta"]["pagination"] == {
            "skip": 10,
            "limit": 25,
            "count": 2,
        }

    @pytest.mark.asyncio
    async def test_pagination_defaults(self):
        f = ResponseEnvelopeFilter()
        request = _make_request()
        context = FilterContext()
        await f.on_request(request, context)

        response = _make_response([{"id": 1}])
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        assert body["meta"]["pagination"]["skip"] == 0
        assert body["meta"]["pagination"]["limit"] == 100

    @pytest.mark.asyncio
    async def test_no_pagination_for_dict(self):
        f = ResponseEnvelopeFilter()
        request = _make_request()
        context = FilterContext()
        await f.on_request(request, context)

        response = _make_response({"name": "Widget"})
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        assert "pagination" not in body["meta"]

    @pytest.mark.asyncio
    async def test_no_pagination_when_disabled(self):
        f = ResponseEnvelopeFilter(include_pagination=False)
        request = _make_request()
        context = FilterContext()
        await f.on_request(request, context)

        response = _make_response([{"id": 1}])
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        assert "pagination" not in body["meta"]

    @pytest.mark.asyncio
    async def test_skips_error_responses(self):
        f = ResponseEnvelopeFilter()
        request = _make_request()
        context = FilterContext()
        await f.on_request(request, context)

        response = _make_response({"error": "not found"}, status_code=404)
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        assert body == {"error": "not found"}

    @pytest.mark.asyncio
    async def test_skips_204(self):
        f = ResponseEnvelopeFilter()
        request = _make_request()
        context = FilterContext()
        await f.on_request(request, context)

        response = Response(status_code=204)
        result = await f.on_response(request, response, context)
        assert result.status_code == 204

    @pytest.mark.asyncio
    async def test_request_id_unique(self):
        f = ResponseEnvelopeFilter()
        ctx1 = FilterContext()
        ctx2 = FilterContext()
        request = _make_request()
        await f.on_request(request, ctx1)
        await f.on_request(request, ctx2)

        assert ctx1.extras["request_id"] != ctx2.extras["request_id"]

    @pytest.mark.asyncio
    async def test_order_is_90(self):
        f = ResponseEnvelopeFilter()
        assert f.order == 90

    @pytest.mark.asyncio
    async def test_non_json_body_passthrough(self):
        f = ResponseEnvelopeFilter()
        request = _make_request()
        context = FilterContext()
        await f.on_request(request, context)

        response = Response(
            content="plain text",
            status_code=200,
            media_type="text/plain",
        )
        result = await f.on_response(request, response, context)
        # Can't parse as JSON, so should pass through
        assert result.body == b"plain text"


class TestEnvelopeFilterIntegration:
    """Integration tests with FastAPI middleware."""

    def test_envelope_wraps_endpoint(self):
        app = FastAPI()

        @app.get("/items")
        async def items():
            return [{"name": "A"}, {"name": "B"}]

        chain = FilterChain()
        chain.add_filter(ResponseEnvelopeFilter())
        app.add_middleware(FilterChainMiddleware, filter_chain=chain)

        client = TestClient(app)
        response = client.get("/items")
        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert "meta" in body
        assert len(body["data"]) == 2
        assert "pagination" in body["meta"]
