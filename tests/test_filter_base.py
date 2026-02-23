"""Tests for filter base types (FilterBase, FilterContext, FilterShortCircuit)."""

import pytest

from slip_stream.adapters.api.filters.base import (
    FilterBase,
    FilterContext,
    FilterShortCircuit,
)


class TestFilterContext:
    """Tests for FilterContext dataclass."""

    def test_default_values(self):
        ctx = FilterContext()
        assert ctx.content_type == "application/json"
        assert ctx.accept == "application/json"
        assert ctx.user is None
        assert ctx.extras == {}

    def test_custom_values(self):
        ctx = FilterContext(
            content_type="application/yaml",
            accept="application/xml",
            user={"id": "user-1"},
            extras={"custom": "data"},
        )
        assert ctx.content_type == "application/yaml"
        assert ctx.accept == "application/xml"
        assert ctx.user == {"id": "user-1"}
        assert ctx.extras["custom"] == "data"

    def test_extras_independent_per_instance(self):
        ctx1 = FilterContext()
        ctx2 = FilterContext()
        ctx1.extras["key"] = "value"
        assert "key" not in ctx2.extras


class TestFilterShortCircuit:
    """Tests for FilterShortCircuit exception."""

    def test_default_values(self):
        exc = FilterShortCircuit()
        assert exc.status_code == 400
        assert exc.body == ""
        assert exc.headers == {}

    def test_custom_values(self):
        exc = FilterShortCircuit(
            status_code=401,
            body="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
        assert exc.status_code == 401
        assert exc.body == "Unauthorized"
        assert exc.headers == {"WWW-Authenticate": "Bearer"}

    def test_is_exception(self):
        exc = FilterShortCircuit(status_code=403)
        assert isinstance(exc, Exception)
        assert "403" in str(exc)


class TestFilterBase:
    """Tests for FilterBase ABC."""

    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            FilterBase()

    def test_default_order(self):
        class MyFilter(FilterBase):
            async def on_request(self, request, context):
                pass

            async def on_response(self, request, response, context):
                return response

        f = MyFilter()
        assert f.order == 100

    def test_custom_order(self):
        class EarlyFilter(FilterBase):
            order = 10

            async def on_request(self, request, context):
                pass

            async def on_response(self, request, response, context):
                return response

        f = EarlyFilter()
        assert f.order == 10

    def test_sorting_by_order(self):
        class FilterA(FilterBase):
            order = 50

            async def on_request(self, request, context):
                pass

            async def on_response(self, request, response, context):
                return response

        class FilterB(FilterBase):
            order = 10

            async def on_request(self, request, context):
                pass

            async def on_response(self, request, response, context):
                return response

        class FilterC(FilterBase):
            order = 30

            async def on_request(self, request, context):
                pass

            async def on_response(self, request, response, context):
                return response

        filters = [FilterA(), FilterC(), FilterB()]
        sorted_filters = sorted(filters, key=lambda f: f.order)
        assert [f.order for f in sorted_filters] == [10, 30, 50]
