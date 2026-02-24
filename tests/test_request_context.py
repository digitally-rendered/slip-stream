"""Tests for RequestContext and HandlerOverride protocol."""

import uuid
from types import SimpleNamespace

import pytest
from dotted_dict import DottedDict
from starlette.requests import Request

from slip_stream.core.context import HandlerOverride, RequestContext


def _make_request(state_attrs: dict | None = None) -> Request:
    """Create a minimal ASGI request for testing."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": [],
        "query_string": b"",
    }
    req = Request(scope)
    if state_attrs:
        for k, v in state_attrs.items():
            setattr(req.state, k, v)
    return req


class TestRequestContext:
    """Tests for RequestContext dataclass."""

    def test_basic_construction(self):
        request = _make_request()
        ctx = RequestContext(
            request=request,
            operation="create",
            schema_name="widget",
        )
        assert ctx.operation == "create"
        assert ctx.schema_name == "widget"
        assert ctx.entity_id is None
        assert ctx.entity is None
        assert ctx.data is None
        assert ctx.current_user is None
        assert ctx.result is None
        assert ctx.skip == 0
        assert ctx.limit == 100
        assert ctx.extras == {}

    def test_full_construction(self):
        request = _make_request()
        eid = uuid.uuid4()
        user = {"id": "user-1", "role": "admin"}

        ctx = RequestContext(
            request=request,
            operation="update",
            schema_name="widget",
            entity_id=eid,
            current_user=user,
            skip=10,
            limit=50,
        )
        assert ctx.entity_id == eid
        assert ctx.current_user == user
        assert ctx.skip == 10
        assert ctx.limit == 50

    def test_extras_independent_per_instance(self):
        req = _make_request()
        ctx1 = RequestContext(request=req, operation="get", schema_name="a")
        ctx2 = RequestContext(request=req, operation="get", schema_name="b")
        ctx1.extras["key"] = "value"
        assert "key" not in ctx2.extras

    def test_result_can_be_set(self):
        req = _make_request()
        ctx = RequestContext(request=req, operation="get", schema_name="widget")
        ctx.result = {"id": "123", "name": "Test"}
        assert ctx.result["name"] == "Test"


class TestFromRequest:
    """Tests for RequestContext.from_request factory."""

    def test_basic_from_request(self):
        request = _make_request()
        ctx = RequestContext.from_request(
            request=request,
            operation="create",
            schema_name="widget",
        )
        assert ctx.operation == "create"
        assert ctx.schema_name == "widget"
        assert ctx.current_user is None

    def test_pulls_user_from_filter_context(self):
        filter_ctx = SimpleNamespace(user={"id": "user-1"})
        request = _make_request({"filter_context": filter_ctx})

        ctx = RequestContext.from_request(
            request=request,
            operation="get",
            schema_name="widget",
        )
        assert ctx.current_user == {"id": "user-1"}

    def test_explicit_user_overrides_filter_context(self):
        filter_ctx = SimpleNamespace(user={"id": "filter-user"})
        request = _make_request({"filter_context": filter_ctx})

        ctx = RequestContext.from_request(
            request=request,
            operation="get",
            schema_name="widget",
            current_user={"id": "explicit-user"},
        )
        assert ctx.current_user == {"id": "explicit-user"}

    def test_no_filter_context(self):
        request = _make_request()
        ctx = RequestContext.from_request(
            request=request,
            operation="list",
            schema_name="widget",
        )
        assert ctx.current_user is None

    def test_filter_context_without_user(self):
        filter_ctx = SimpleNamespace(user=None)
        request = _make_request({"filter_context": filter_ctx})

        ctx = RequestContext.from_request(
            request=request,
            operation="get",
            schema_name="widget",
        )
        assert ctx.current_user is None

    def test_passes_kwargs_through(self):
        request = _make_request()
        eid = uuid.uuid4()
        ctx = RequestContext.from_request(
            request=request,
            operation="get",
            schema_name="widget",
            entity_id=eid,
            db="mock-db",
        )
        assert ctx.entity_id == eid
        assert ctx.db == "mock-db"


class TestDottedDictIntegration:
    """Tests for DottedDict wrapping of current_user and extras."""

    def test_from_request_wraps_current_user(self):
        request = _make_request()
        ctx = RequestContext.from_request(
            request=request,
            operation="get",
            schema_name="widget",
            current_user={"id": "user-1", "role": "admin"},
        )
        assert isinstance(ctx.current_user, DottedDict)
        assert ctx.current_user.id == "user-1"
        assert ctx.current_user.role == "admin"
        # Dict access still works
        assert ctx.current_user["id"] == "user-1"

    def test_from_request_wraps_extras(self):
        request = _make_request()
        ctx = RequestContext.from_request(
            request=request,
            operation="get",
            schema_name="widget",
            extras={"quota": 10, "tier": "premium"},
        )
        assert isinstance(ctx.extras, DottedDict)
        assert ctx.extras.quota == 10
        assert ctx.extras.tier == "premium"

    def test_default_extras_is_dotted_dict(self):
        request = _make_request()
        ctx = RequestContext.from_request(
            request=request,
            operation="get",
            schema_name="widget",
        )
        assert isinstance(ctx.extras, DottedDict)
        ctx.extras.my_key = "my_value"
        assert ctx.extras["my_key"] == "my_value"

    def test_filter_context_user_wrapped(self):
        filter_ctx = SimpleNamespace(user={"id": "filter-user", "role": "viewer"})
        request = _make_request({"filter_context": filter_ctx})
        ctx = RequestContext.from_request(
            request=request,
            operation="get",
            schema_name="widget",
        )
        assert isinstance(ctx.current_user, DottedDict)
        assert ctx.current_user.role == "viewer"

    def test_none_current_user_stays_none(self):
        request = _make_request()
        ctx = RequestContext.from_request(
            request=request,
            operation="get",
            schema_name="widget",
        )
        assert ctx.current_user is None

    def test_already_dotted_dict_not_double_wrapped(self):
        request = _make_request()
        dd = DottedDict({"id": "user-1"})
        ctx = RequestContext.from_request(
            request=request,
            operation="get",
            schema_name="widget",
            current_user=dd,
        )
        assert ctx.current_user is dd

    def test_nested_dict_access(self):
        request = _make_request()
        ctx = RequestContext.from_request(
            request=request,
            operation="get",
            schema_name="widget",
            current_user={"id": "user-1", "permissions": {"can_edit": True}},
        )
        assert ctx.current_user.id == "user-1"
        # Nested dicts are accessible via key
        assert ctx.current_user.permissions["can_edit"] is True


class TestHandlerOverride:
    """Tests for HandlerOverride protocol."""

    def test_async_function_satisfies_protocol(self):
        async def my_handler(ctx: RequestContext):
            return {"result": "custom"}

        assert isinstance(my_handler, HandlerOverride)

    def test_non_callable_does_not_satisfy(self):
        assert not isinstance("not a function", HandlerOverride)

    @pytest.mark.asyncio
    async def test_handler_can_be_called(self):
        async def my_handler(ctx: RequestContext):
            return {"name": ctx.schema_name}

        request = _make_request()
        ctx = RequestContext(request=request, operation="get", schema_name="widget")
        result = await my_handler(ctx)
        assert result == {"name": "widget"}
