"""Tests for the event streaming adapter."""

import pytest

from slip_stream.adapters.streaming.base import (
    EventStreamBridge,
    InMemoryStream,
    StreamAdapter,
    StreamEvent,
)

# ---------------------------------------------------------------------------
# StreamEvent dataclass
# ---------------------------------------------------------------------------


class TestStreamEvent:
    def test_creates_with_defaults(self):
        event = StreamEvent(
            topic="test.widget.create",
            key="abc-123",
            payload={"event": "create", "entity_id": "abc-123"},
        )
        assert event.topic == "test.widget.create"
        assert event.key == "abc-123"
        assert event.payload["event"] == "create"
        assert event.headers == {}
        assert isinstance(event.timestamp, float)

    def test_creates_with_headers(self):
        event = StreamEvent(
            topic="t",
            key=None,
            payload={},
            headers={"x-event-type": "create"},
        )
        assert event.headers == {"x-event-type": "create"}

    def test_creates_with_custom_timestamp(self):
        ts = 1700000000.0
        event = StreamEvent(topic="t", key=None, payload={}, timestamp=ts)
        assert event.timestamp == ts


# ---------------------------------------------------------------------------
# InMemoryStream
# ---------------------------------------------------------------------------


class TestInMemoryStream:
    @pytest.mark.asyncio
    async def test_implements_protocol(self):
        stream = InMemoryStream()
        assert isinstance(stream, StreamAdapter)

    @pytest.mark.asyncio
    async def test_publish_stores_event(self):
        stream = InMemoryStream()
        await stream.publish(
            topic="test.widget.create",
            key="w-1",
            payload={"event": "create"},
            headers={"x-event-type": "create"},
        )
        assert len(stream.events) == 1
        evt = stream.events[0]
        assert evt.topic == "test.widget.create"
        assert evt.key == "w-1"
        assert evt.payload == {"event": "create"}
        assert evt.headers == {"x-event-type": "create"}

    @pytest.mark.asyncio
    async def test_publish_without_headers(self):
        stream = InMemoryStream()
        await stream.publish(topic="t", key=None, payload={})
        assert stream.events[0].headers == {}

    @pytest.mark.asyncio
    async def test_publish_multiple_events(self):
        stream = InMemoryStream()
        for i in range(5):
            await stream.publish(topic=f"t.{i}", key=str(i), payload={"i": i})
        assert len(stream.events) == 5
        assert [e.key for e in stream.events] == ["0", "1", "2", "3", "4"]

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        stream = InMemoryStream()
        await stream.close()  # should not raise


# ---------------------------------------------------------------------------
# EventStreamBridge
# ---------------------------------------------------------------------------


class _FakeCtx:
    """Minimal context object for testing."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeEventBus:
    """Minimal event bus for testing."""

    def __init__(self):
        self._handlers: dict[str, list] = {}

    def register(self, event: str, handler):
        self._handlers.setdefault(event, []).append(handler)

    async def emit(self, event: str, ctx):
        for handler in self._handlers.get(event, []):
            await handler(ctx)


class TestEventStreamBridge:
    @pytest.mark.asyncio
    async def test_register_hooks(self):
        bus = _FakeEventBus()
        bridge = EventStreamBridge()
        bridge.register(bus)
        assert "post_create" in bus._handlers
        assert "post_update" in bus._handlers
        assert "post_delete" in bus._handlers

    @pytest.mark.asyncio
    async def test_create_event_published(self):
        stream = InMemoryStream()
        bridge = EventStreamBridge(adapters=[stream])
        bus = _FakeEventBus()
        bridge.register(bus)

        ctx = _FakeCtx(
            schema_name="widget",
            entity_id="w-1",
            current_user={"id": "user-1"},
            data={"name": "Gear"},
            channel="rest",
        )
        await bus.emit("post_create", ctx)

        assert len(stream.events) == 1
        evt = stream.events[0]
        assert evt.topic == "slip-stream.widget.create"
        assert evt.key == "w-1"
        assert evt.payload["event"] == "create"
        assert evt.payload["schema_name"] == "widget"
        assert evt.payload["entity_id"] == "w-1"
        assert evt.payload["user_id"] == "user-1"
        assert evt.payload["data"] == {"name": "Gear"}
        assert evt.headers["x-event-type"] == "create"
        assert evt.headers["x-schema-name"] == "widget"

    @pytest.mark.asyncio
    async def test_update_event_published(self):
        stream = InMemoryStream()
        bridge = EventStreamBridge(adapters=[stream])
        bus = _FakeEventBus()
        bridge.register(bus)

        ctx = _FakeCtx(
            schema_name="widget",
            entity_id="w-1",
            current_user={"id": "user-2"},
            data={"name": "Updated"},
            channel="graphql",
        )
        await bus.emit("post_update", ctx)

        assert len(stream.events) == 1
        evt = stream.events[0]
        assert evt.topic == "slip-stream.widget.update"
        assert evt.payload["event"] == "update"
        assert evt.payload["channel"] == "graphql"

    @pytest.mark.asyncio
    async def test_delete_event_published(self):
        stream = InMemoryStream()
        bridge = EventStreamBridge(adapters=[stream])
        bus = _FakeEventBus()
        bridge.register(bus)

        ctx = _FakeCtx(
            schema_name="widget",
            entity_id="w-1",
            current_user={"id": "user-1"},
            channel="rest",
        )
        await bus.emit("post_delete", ctx)

        assert len(stream.events) == 1
        evt = stream.events[0]
        assert evt.topic == "slip-stream.widget.delete"
        assert evt.payload["event"] == "delete"
        assert evt.payload["entity_id"] == "w-1"

    @pytest.mark.asyncio
    async def test_custom_topic_prefix(self):
        stream = InMemoryStream()
        bridge = EventStreamBridge(adapters=[stream], topic_prefix="myapp")
        bus = _FakeEventBus()
        bridge.register(bus)

        ctx = _FakeCtx(
            schema_name="order",
            entity_id="o-1",
            current_user={"id": "u"},
            channel="rest",
        )
        await bus.emit("post_create", ctx)

        assert stream.events[0].topic == "myapp.order.create"

    @pytest.mark.asyncio
    async def test_include_data_false(self):
        stream = InMemoryStream()
        bridge = EventStreamBridge(adapters=[stream], include_data=False)
        bus = _FakeEventBus()
        bridge.register(bus)

        ctx = _FakeCtx(
            schema_name="widget",
            entity_id="w-1",
            current_user={"id": "u"},
            data={"secret": "value"},
            channel="rest",
        )
        await bus.emit("post_create", ctx)

        assert "data" not in stream.events[0].payload

    @pytest.mark.asyncio
    async def test_multiple_adapters(self):
        s1 = InMemoryStream()
        s2 = InMemoryStream()
        bridge = EventStreamBridge(adapters=[s1, s2])
        bus = _FakeEventBus()
        bridge.register(bus)

        ctx = _FakeCtx(
            schema_name="widget",
            entity_id="w-1",
            current_user={"id": "u"},
            channel="rest",
        )
        await bus.emit("post_create", ctx)

        assert len(s1.events) == 1
        assert len(s2.events) == 1

    @pytest.mark.asyncio
    async def test_add_adapter(self):
        bridge = EventStreamBridge()
        stream = InMemoryStream()
        bridge.add_adapter(stream)
        bus = _FakeEventBus()
        bridge.register(bus)

        ctx = _FakeCtx(
            schema_name="widget",
            entity_id="w-1",
            current_user={"id": "u"},
            channel="rest",
        )
        await bus.emit("post_create", ctx)

        assert len(stream.events) == 1

    @pytest.mark.asyncio
    async def test_entity_id_from_result(self):
        """When ctx has no entity_id, falls back to ctx.result.entity_id."""
        stream = InMemoryStream()
        bridge = EventStreamBridge(adapters=[stream])
        bus = _FakeEventBus()
        bridge.register(bus)

        result = _FakeCtx(entity_id="from-result")
        ctx = _FakeCtx(
            schema_name="widget",
            result=result,
            current_user={"id": "u"},
            channel="rest",
        )
        await bus.emit("post_create", ctx)

        assert stream.events[0].key == "from-result"
        assert stream.events[0].payload["entity_id"] == "from-result"

    @pytest.mark.asyncio
    async def test_no_entity_id(self):
        """When no entity_id available, key is None."""
        stream = InMemoryStream()
        bridge = EventStreamBridge(adapters=[stream])
        bus = _FakeEventBus()
        bridge.register(bus)

        ctx = _FakeCtx(
            schema_name="widget",
            current_user={"id": "u"},
            channel="rest",
        )
        await bus.emit("post_create", ctx)

        assert stream.events[0].key is None

    @pytest.mark.asyncio
    async def test_user_object_with_id_attr(self):
        """Supports user objects with .id attribute (not just dicts)."""
        stream = InMemoryStream()
        bridge = EventStreamBridge(adapters=[stream])
        bus = _FakeEventBus()
        bridge.register(bus)

        user = _FakeCtx(id="attr-user")
        ctx = _FakeCtx(
            schema_name="widget",
            entity_id="w-1",
            current_user=user,
            channel="rest",
        )
        await bus.emit("post_create", ctx)

        assert stream.events[0].payload["user_id"] == "attr-user"

    @pytest.mark.asyncio
    async def test_no_user(self):
        """When no current_user, user_id is absent."""
        stream = InMemoryStream()
        bridge = EventStreamBridge(adapters=[stream])
        bus = _FakeEventBus()
        bridge.register(bus)

        ctx = _FakeCtx(
            schema_name="widget",
            entity_id="w-1",
            channel="rest",
        )
        await bus.emit("post_create", ctx)

        assert "user_id" not in stream.events[0].payload

    @pytest.mark.asyncio
    async def test_data_with_model_dump(self):
        """Supports Pydantic-style data with model_dump()."""
        stream = InMemoryStream()
        bridge = EventStreamBridge(adapters=[stream])
        bus = _FakeEventBus()
        bridge.register(bus)

        class FakeModel:
            def model_dump(self, exclude_unset=False):
                return {"name": "Pydantic"}

        ctx = _FakeCtx(
            schema_name="widget",
            entity_id="w-1",
            current_user={"id": "u"},
            data=FakeModel(),
            channel="rest",
        )
        await bus.emit("post_create", ctx)

        assert stream.events[0].payload["data"] == {"name": "Pydantic"}

    @pytest.mark.asyncio
    async def test_adapter_error_does_not_propagate(self):
        """A failing adapter doesn't prevent other adapters from publishing."""

        class FailingStream:
            async def publish(self, **kwargs):
                raise RuntimeError("boom")

            async def close(self):
                pass

        good_stream = InMemoryStream()
        bridge = EventStreamBridge(adapters=[FailingStream(), good_stream])
        bus = _FakeEventBus()
        bridge.register(bus)

        ctx = _FakeCtx(
            schema_name="widget",
            entity_id="w-1",
            current_user={"id": "u"},
            channel="rest",
        )
        await bus.emit("post_create", ctx)

        # Good stream still received the event
        assert len(good_stream.events) == 1

    @pytest.mark.asyncio
    async def test_close_all_adapters(self):
        closed = []

        class TrackingStream:
            async def publish(self, **kwargs):
                pass

            async def close(self):
                closed.append(True)

        bridge = EventStreamBridge(adapters=[TrackingStream(), TrackingStream()])
        await bridge.close()
        assert len(closed) == 2

    @pytest.mark.asyncio
    async def test_close_handles_adapter_error(self):
        """Closing continues even if one adapter fails."""

        class FailClose:
            async def publish(self, **kwargs):
                pass

            async def close(self):
                raise RuntimeError("close failed")

        good = InMemoryStream()
        bridge = EventStreamBridge(adapters=[FailClose(), good])
        await bridge.close()  # should not raise

    @pytest.mark.asyncio
    async def test_default_channel_is_rest(self):
        """When ctx has no channel attr, defaults to 'rest'."""
        stream = InMemoryStream()
        bridge = EventStreamBridge(adapters=[stream])
        bus = _FakeEventBus()
        bridge.register(bus)

        ctx = _FakeCtx(
            schema_name="widget",
            entity_id="w-1",
            current_user={"id": "u"},
        )
        await bus.emit("post_create", ctx)

        assert stream.events[0].payload["channel"] == "rest"

    @pytest.mark.asyncio
    async def test_full_lifecycle(self):
        """Create, update, delete all publish correctly."""
        stream = InMemoryStream()
        bridge = EventStreamBridge(adapters=[stream])
        bus = _FakeEventBus()
        bridge.register(bus)

        base = dict(
            schema_name="order",
            entity_id="o-1",
            current_user={"id": "u"},
            channel="rest",
        )

        await bus.emit("post_create", _FakeCtx(**base, data={"total": 100}))
        await bus.emit("post_update", _FakeCtx(**base, data={"total": 200}))
        await bus.emit("post_delete", _FakeCtx(**base))

        assert len(stream.events) == 3
        topics = [e.topic for e in stream.events]
        assert topics == [
            "slip-stream.order.create",
            "slip-stream.order.update",
            "slip-stream.order.delete",
        ]
        ops = [e.payload["event"] for e in stream.events]
        assert ops == ["create", "update", "delete"]
