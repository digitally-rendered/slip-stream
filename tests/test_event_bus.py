"""Tests for EventBus lifecycle event system."""

import pytest
from starlette.requests import Request

from slip_stream.core.context import RequestContext
from slip_stream.core.events import (
    LIFECYCLE_EVENTS,
    EventBus,
    HookError,
)


def _make_ctx(schema_name: str = "widget", operation: str = "create") -> RequestContext:
    """Create a minimal RequestContext for testing."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/test",
        "headers": [],
        "query_string": b"",
    }
    request = Request(scope)
    return RequestContext(
        request=request,
        operation=operation,
        schema_name=schema_name,
    )


class TestEventBusRegistration:
    """Tests for event handler registration."""

    def test_register_with_decorator(self):
        bus = EventBus()

        @bus.on("pre_create")
        async def handler(ctx):
            pass

        assert bus.handler_count == 1

    def test_register_imperatively(self):
        bus = EventBus()

        async def handler(ctx):
            pass

        bus.register("post_create", handler)
        assert bus.handler_count == 1

    def test_register_schema_specific(self):
        bus = EventBus()

        @bus.on("pre_update", schema_name="widget")
        async def handler(ctx):
            pass

        assert bus.handler_count == 1

    def test_unknown_event_raises(self):
        bus = EventBus()
        with pytest.raises(ValueError, match="Unknown event"):
            bus.on("not_a_real_event")

    def test_unknown_event_register_raises(self):
        bus = EventBus()

        async def handler(ctx):
            pass

        with pytest.raises(ValueError, match="Unknown event"):
            bus.register("invalid_event", handler)

    def test_all_lifecycle_events_valid(self):
        bus = EventBus()
        for event in LIFECYCLE_EVENTS:
            # Should not raise
            @bus.on(event)
            async def handler(ctx):
                pass

        assert bus.handler_count == len(LIFECYCLE_EVENTS)

    def test_multiple_handlers_same_event(self):
        bus = EventBus()

        @bus.on("pre_create")
        async def handler1(ctx):
            pass

        @bus.on("pre_create")
        async def handler2(ctx):
            pass

        assert bus.handler_count == 2


class TestEventBusEmission:
    """Tests for event emission."""

    @pytest.mark.asyncio
    async def test_global_handler_called(self):
        bus = EventBus()
        calls = []

        @bus.on("pre_create")
        async def handler(ctx):
            calls.append(ctx.schema_name)

        ctx = _make_ctx("widget")
        await bus.emit("pre_create", ctx)
        assert calls == ["widget"]

    @pytest.mark.asyncio
    async def test_schema_specific_handler(self):
        bus = EventBus()
        calls = []

        @bus.on("pre_create", schema_name="widget")
        async def handler(ctx):
            calls.append("widget-specific")

        # Should fire for widget
        ctx = _make_ctx("widget")
        await bus.emit("pre_create", ctx)
        assert calls == ["widget-specific"]

        # Should NOT fire for order
        calls.clear()
        ctx = _make_ctx("order")
        await bus.emit("pre_create", ctx)
        assert calls == []

    @pytest.mark.asyncio
    async def test_global_runs_before_specific(self):
        bus = EventBus()
        order = []

        @bus.on("post_create")
        async def global_handler(ctx):
            order.append("global")

        @bus.on("post_create", schema_name="widget")
        async def specific_handler(ctx):
            order.append("specific")

        ctx = _make_ctx("widget")
        await bus.emit("post_create", ctx)
        assert order == ["global", "specific"]

    @pytest.mark.asyncio
    async def test_emit_no_handlers(self):
        bus = EventBus()
        ctx = _make_ctx()
        # Should not raise
        await bus.emit("pre_create", ctx)

    @pytest.mark.asyncio
    async def test_handler_can_modify_context(self):
        bus = EventBus()

        @bus.on("pre_create")
        async def enrich(ctx):
            ctx.extras["enriched"] = True

        ctx = _make_ctx()
        await bus.emit("pre_create", ctx)
        assert ctx.extras["enriched"] is True

    @pytest.mark.asyncio
    async def test_handler_order_preserved(self):
        bus = EventBus()
        order = []

        @bus.on("pre_create")
        async def first(ctx):
            order.append(1)

        @bus.on("pre_create")
        async def second(ctx):
            order.append(2)

        @bus.on("pre_create")
        async def third(ctx):
            order.append(3)

        ctx = _make_ctx()
        await bus.emit("pre_create", ctx)
        assert order == [1, 2, 3]


class TestHookError:
    """Tests for HookError exception."""

    def test_default_values(self):
        err = HookError()
        assert err.status_code == 400
        assert err.detail == ""

    def test_custom_values(self):
        err = HookError(status_code=403, detail="Forbidden action")
        assert err.status_code == 403
        assert err.detail == "Forbidden action"

    def test_is_exception(self):
        err = HookError(422, "Validation failed")
        assert isinstance(err, Exception)
        assert str(err) == "Validation failed"

    @pytest.mark.asyncio
    async def test_hook_can_raise(self):
        bus = EventBus()

        @bus.on("pre_delete")
        async def block_delete(ctx):
            raise HookError(403, "Deletion not allowed")

        ctx = _make_ctx(operation="delete")
        with pytest.raises(HookError) as exc_info:
            await bus.emit("pre_delete", ctx)

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Deletion not allowed"
