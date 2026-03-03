"""Tests for FieldProjectionFilter."""

import json

import pytest
from starlette.requests import Request
from starlette.responses import Response

from slip_stream.adapters.api.filters.base import FilterContext
from slip_stream.adapters.api.filters.projection import FieldProjectionFilter


def _make_request(
    path: str = "/api/v1/widget/",
    query_string: str = "",
) -> Request:
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


class TestQueryProjection:
    """Tests for ?fields= query parameter projection."""

    @pytest.mark.asyncio
    async def test_fields_param_projects_dict(self):
        f = FieldProjectionFilter()
        request = _make_request(query_string="fields=name,color")
        context = FilterContext()
        await f.on_request(request, context)

        data = {"name": "Widget", "color": "blue", "weight": 10, "entity_id": "abc"}
        response = _make_response(data)
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        assert body == {"name": "Widget", "color": "blue"}

    @pytest.mark.asyncio
    async def test_fields_param_projects_list(self):
        f = FieldProjectionFilter()
        request = _make_request(query_string="fields=name")
        context = FilterContext()
        await f.on_request(request, context)

        data = [
            {"name": "A", "color": "red"},
            {"name": "B", "color": "green"},
        ]
        response = _make_response(data)
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        assert body == [{"name": "A"}, {"name": "B"}]

    @pytest.mark.asyncio
    async def test_no_fields_param_passthrough(self):
        f = FieldProjectionFilter()
        request = _make_request()
        context = FilterContext()
        await f.on_request(request, context)

        data = {"name": "Widget", "color": "blue"}
        response = _make_response(data)
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        assert body == {"name": "Widget", "color": "blue"}

    @pytest.mark.asyncio
    async def test_fields_with_spaces_trimmed(self):
        f = FieldProjectionFilter()
        request = _make_request(query_string="fields=name , color ")
        context = FilterContext()
        await f.on_request(request, context)

        data = {"name": "Widget", "color": "blue", "weight": 10}
        response = _make_response(data)
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        assert body == {"name": "Widget", "color": "blue"}

    @pytest.mark.asyncio
    async def test_query_projection_disabled(self):
        f = FieldProjectionFilter(allow_query_projection=False)
        request = _make_request(query_string="fields=name")
        context = FilterContext()
        await f.on_request(request, context)

        data = {"name": "Widget", "color": "blue"}
        response = _make_response(data)
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        assert body == {"name": "Widget", "color": "blue"}


class TestRoleBasedProjection:
    """Tests for role-based field restriction."""

    @pytest.mark.asyncio
    async def test_role_restricts_fields(self):
        f = FieldProjectionFilter(
            role_field_rules={
                "widget": {
                    "viewer": {"name", "entity_id"},
                    "admin": {"name", "entity_id", "color", "weight"},
                }
            }
        )
        request = _make_request(path="/api/v1/widget/")
        context = FilterContext(user={"role": "viewer"})
        await f.on_request(request, context)

        data = {"name": "Widget", "color": "blue", "weight": 10, "entity_id": "abc"}
        response = _make_response(data)
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        assert body == {"name": "Widget", "entity_id": "abc"}

    @pytest.mark.asyncio
    async def test_admin_sees_all_configured_fields(self):
        f = FieldProjectionFilter(
            role_field_rules={
                "widget": {
                    "admin": {"name", "color", "weight", "entity_id"},
                }
            }
        )
        request = _make_request(path="/api/v1/widget/")
        context = FilterContext(user={"role": "admin"})
        await f.on_request(request, context)

        data = {"name": "Widget", "color": "blue", "weight": 10, "entity_id": "abc"}
        response = _make_response(data)
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        assert body == data

    @pytest.mark.asyncio
    async def test_wildcard_role_fallback(self):
        f = FieldProjectionFilter(
            role_field_rules={
                "widget": {
                    "*": {"name", "entity_id"},
                    "admin": {"name", "entity_id", "color"},
                }
            }
        )
        request = _make_request(path="/api/v1/widget/")
        context = FilterContext(user={"role": "unknown_role"})
        await f.on_request(request, context)

        data = {"name": "Widget", "color": "blue", "entity_id": "abc"}
        response = _make_response(data)
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        assert body == {"name": "Widget", "entity_id": "abc"}

    @pytest.mark.asyncio
    async def test_no_user_skips_role_rules(self):
        f = FieldProjectionFilter(
            role_field_rules={
                "widget": {"viewer": {"name"}},
            }
        )
        request = _make_request(path="/api/v1/widget/")
        context = FilterContext()
        await f.on_request(request, context)

        data = {"name": "Widget", "color": "blue"}
        response = _make_response(data)
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        assert body == {"name": "Widget", "color": "blue"}


class TestCombinedProjection:
    """Tests for interaction between query params and role rules."""

    @pytest.mark.asyncio
    async def test_intersection_of_role_and_query(self):
        f = FieldProjectionFilter(
            role_field_rules={
                "widget": {
                    "viewer": {"name", "color", "entity_id"},
                }
            }
        )
        request = _make_request(
            path="/api/v1/widget/",
            query_string="fields=name,weight",
        )
        context = FilterContext(user={"role": "viewer"})
        await f.on_request(request, context)

        data = {"name": "Widget", "color": "blue", "weight": 10, "entity_id": "abc"}
        response = _make_response(data)
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        # weight is not in role-allowed, so only name (intersection)
        assert body == {"name": "Widget"}

    @pytest.mark.asyncio
    async def test_query_cannot_expose_role_hidden_fields(self):
        f = FieldProjectionFilter(
            role_field_rules={
                "widget": {"viewer": {"name"}},
            }
        )
        request = _make_request(
            path="/api/v1/widget/",
            query_string="fields=name,color,weight",
        )
        context = FilterContext(user={"role": "viewer"})
        await f.on_request(request, context)

        data = {"name": "Widget", "color": "blue", "weight": 10}
        response = _make_response(data)
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        assert body == {"name": "Widget"}


class TestProjectionEdgeCases:
    """Edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_skips_error_responses(self):
        f = FieldProjectionFilter()
        request = _make_request(query_string="fields=name")
        context = FilterContext()
        await f.on_request(request, context)

        response = _make_response({"error": "not found"}, status_code=404)
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        assert body == {"error": "not found"}

    @pytest.mark.asyncio
    async def test_skips_204(self):
        f = FieldProjectionFilter()
        request = _make_request(query_string="fields=name")
        context = FilterContext()
        await f.on_request(request, context)

        response = Response(status_code=204)
        result = await f.on_response(request, response, context)
        assert result.status_code == 204

    @pytest.mark.asyncio
    async def test_handles_envelope_format(self):
        f = FieldProjectionFilter()
        request = _make_request(query_string="fields=name")
        context = FilterContext()
        await f.on_request(request, context)

        data = {
            "data": {"name": "Widget", "color": "blue"},
            "meta": {"request_id": "abc"},
        }
        response = _make_response(data)
        result = await f.on_response(request, response, context)

        body = json.loads(result.body)
        assert body["data"] == {"name": "Widget"}
        assert body["meta"] == {"request_id": "abc"}

    @pytest.mark.asyncio
    async def test_order_is_95(self):
        f = FieldProjectionFilter()
        assert f.order == 95

    @pytest.mark.asyncio
    async def test_extract_schema_name(self):
        f = FieldProjectionFilter()
        assert f._extract_schema_name("/api/v1/widget/") == "widget"
        assert f._extract_schema_name("/api/v1/my-thing/123") == "my_thing"
        assert f._extract_schema_name("/short") is None

    @pytest.mark.asyncio
    async def test_non_json_passthrough(self):
        f = FieldProjectionFilter()
        request = _make_request(query_string="fields=name")
        context = FilterContext()
        await f.on_request(request, context)

        response = Response(
            content="not json", status_code=200, media_type="text/plain"
        )
        result = await f.on_response(request, response, context)
        assert result.body == b"not json"
