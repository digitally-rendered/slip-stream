"""Tests for SlipStreamRegistry decorator system."""

from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient
from starlette.requests import Request

from slip_stream.adapters.api.endpoint_factory import EndpointFactory
from slip_stream.container import EntityContainer
from slip_stream.core.context import RequestContext
from slip_stream.core.events import EventBus, HookError
from slip_stream.registry import SlipStreamRegistry


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


# --- DB holder for integration tests ---

_db_holder: Dict[str, Any] = {}


def _get_db() -> Any:
    return _db_holder["db"]


def _get_current_user() -> Dict[str, Any]:
    return {"id": "test-user", "role": "viewer"}


@pytest.fixture(autouse=True)
def _fresh_registry_db():
    client = AsyncMongoMockClient()
    _db_holder["db"] = client["test_registry_db"]
    yield
    _db_holder.clear()


@pytest.fixture
def container(registry):
    """Resolve a container with widget schema."""
    c = EntityContainer()
    c.resolve_all(["widget"])
    return c


class TestRegistryRegistration:
    """Tests for decorator registration (no application yet)."""

    def test_handler_registers_entry(self):
        reg = SlipStreamRegistry()

        @reg.handler("widget", "create")
        async def fn(ctx):
            pass

        assert len(reg._handlers) == 1
        assert reg._handlers[0].schema_name == "widget"
        assert reg._handlers[0].operation == "create"
        assert reg._handlers[0].handler is fn

    def test_guard_registers_entry(self):
        reg = SlipStreamRegistry()

        @reg.guard("widget", "delete")
        async def fn(ctx):
            pass

        assert len(reg._guards) == 1
        assert reg._guards[0].schema_name == "widget"
        assert reg._guards[0].operation == "delete"

    def test_validate_registers_entry(self):
        reg = SlipStreamRegistry()

        @reg.validate("widget", "create")
        async def fn(ctx):
            pass

        assert len(reg._validators) == 1

    def test_transform_before_registers_entry(self):
        reg = SlipStreamRegistry()

        @reg.transform("widget", "create", when="before")
        async def fn(ctx):
            pass

        assert len(reg._transforms_before) == 1
        assert len(reg._transforms_after) == 0

    def test_transform_after_registers_entry(self):
        reg = SlipStreamRegistry()

        @reg.transform("widget", "create", when="after")
        async def fn(ctx):
            pass

        assert len(reg._transforms_after) == 1
        assert len(reg._transforms_before) == 0

    def test_on_registers_entry(self):
        reg = SlipStreamRegistry()

        @reg.on("post_create")
        async def fn(ctx):
            pass

        assert len(reg._on_hooks) == 1
        assert reg._on_hooks[0].event_name == "post_create"
        assert reg._on_hooks[0].schema_name == "*"

    def test_on_with_schema(self):
        reg = SlipStreamRegistry()

        @reg.on("pre_delete", schema_name="widget")
        async def fn(ctx):
            pass

        assert reg._on_hooks[0].schema_name == "widget"

    def test_handler_invalid_operation_raises(self):
        reg = SlipStreamRegistry()
        with pytest.raises(ValueError, match="Unknown operation"):
            reg.handler("widget", "purge")

    def test_guard_invalid_operation_raises(self):
        reg = SlipStreamRegistry()
        with pytest.raises(ValueError, match="Unknown operation"):
            reg.guard("widget", "nuke")

    def test_validate_invalid_operation_raises(self):
        reg = SlipStreamRegistry()
        with pytest.raises(ValueError, match="Unknown operation"):
            reg.validate("widget", "explode")

    def test_transform_invalid_when_raises(self):
        reg = SlipStreamRegistry()
        with pytest.raises(ValueError, match="'when' must be"):
            reg.transform("widget", "create", when="during")

    def test_transform_invalid_operation_raises(self):
        reg = SlipStreamRegistry()
        with pytest.raises(ValueError, match="Unknown operation"):
            reg.transform("widget", "zap", when="before")

    def test_on_invalid_event_raises(self):
        reg = SlipStreamRegistry()
        with pytest.raises(ValueError, match="Unknown event"):
            reg.on("pre_explode")

    def test_guard_multiple_operations(self):
        reg = SlipStreamRegistry()

        @reg.guard("widget", "create", "update", "delete")
        async def fn(ctx):
            pass

        assert len(reg._guards) == 3
        ops = {e.operation for e in reg._guards}
        assert ops == {"create", "update", "delete"}

    def test_validate_multiple_operations(self):
        reg = SlipStreamRegistry()

        @reg.validate("widget", "create", "update")
        async def fn(ctx):
            pass

        assert len(reg._validators) == 2

    def test_transform_multiple_operations(self):
        reg = SlipStreamRegistry()

        @reg.transform("widget", "create", "update", when="before")
        async def fn(ctx):
            pass

        assert len(reg._transforms_before) == 2

    def test_decorator_returns_original_function(self):
        reg = SlipStreamRegistry()

        async def original(ctx):
            pass

        result = reg.handler("widget", "create")(original)
        assert result is original

        result = reg.guard("widget", "delete")(original)
        assert result is original

        result = reg.validate("widget", "create")(original)
        assert result is original

        result = reg.transform("widget", "create", when="before")(original)
        assert result is original

        result = reg.on("post_create")(original)
        assert result is original


class TestRegistryApply:
    """Tests for apply() merging into container and event bus."""

    def test_handler_populates_handler_overrides(self, container):
        reg = SlipStreamRegistry()
        bus = EventBus()

        async def my_handler(ctx):
            return {"custom": True}

        reg.handler("widget", "create")(my_handler)
        reg.apply(container, bus)

        registration = container.get("widget")
        assert registration.handler_overrides["create"] is my_handler

    def test_apply_unknown_schema_raises(self, container):
        reg = SlipStreamRegistry()
        bus = EventBus()

        @reg.handler("nonexistent", "create")
        async def fn(ctx):
            pass

        with pytest.raises(ValueError, match="unknown schema 'nonexistent'"):
            reg.apply(container, bus)

    def test_guard_registers_on_event_bus(self, container):
        reg = SlipStreamRegistry()
        bus = EventBus()

        @reg.guard("widget", "delete")
        async def fn(ctx):
            pass

        reg.apply(container, bus)
        assert bus.handler_count == 1

    def test_validate_registers_on_event_bus(self, container):
        reg = SlipStreamRegistry()
        bus = EventBus()

        @reg.validate("widget", "create")
        async def fn(ctx):
            pass

        reg.apply(container, bus)
        assert bus.handler_count == 1

    def test_transform_before_registers_pre_hook(self, container):
        reg = SlipStreamRegistry()
        bus = EventBus()

        @reg.transform("widget", "create", when="before")
        async def fn(ctx):
            pass

        reg.apply(container, bus)
        assert bus.handler_count == 1

    def test_transform_after_registers_post_hook(self, container):
        reg = SlipStreamRegistry()
        bus = EventBus()

        @reg.transform("widget", "create", when="after")
        async def fn(ctx):
            pass

        reg.apply(container, bus)
        assert bus.handler_count == 1

    def test_on_hook_registers_directly(self, container):
        reg = SlipStreamRegistry()
        bus = EventBus()

        @reg.on("post_create", schema_name="widget")
        async def fn(ctx):
            pass

        reg.apply(container, bus)
        assert bus.handler_count == 1

    @pytest.mark.asyncio
    async def test_execution_order_guard_validator_transform(self, container):
        """Guards run before validators, validators before transforms."""
        reg = SlipStreamRegistry()
        bus = EventBus()
        order = []

        @reg.guard("widget", "create")
        async def guard_fn(ctx):
            order.append("guard")

        @reg.validate("widget", "create")
        async def validate_fn(ctx):
            order.append("validate")

        @reg.transform("widget", "create", when="before")
        async def transform_fn(ctx):
            order.append("transform")

        reg.apply(container, bus)

        ctx = _make_ctx("widget", "create")
        await bus.emit("pre_create", ctx)
        assert order == ["guard", "validate", "transform"]

    @pytest.mark.asyncio
    async def test_multiple_guards_preserve_declaration_order(self, container):
        reg = SlipStreamRegistry()
        bus = EventBus()
        order = []

        @reg.guard("widget", "create")
        async def first(ctx):
            order.append("first")

        @reg.guard("widget", "create")
        async def second(ctx):
            order.append("second")

        reg.apply(container, bus)

        ctx = _make_ctx("widget", "create")
        await bus.emit("pre_create", ctx)
        assert order == ["first", "second"]

    @pytest.mark.asyncio
    async def test_global_guard_applies_to_all_schemas(self, container):
        reg = SlipStreamRegistry()
        bus = EventBus()
        calls = []

        @reg.guard("*", "create")
        async def global_guard(ctx):
            calls.append(ctx.schema_name)

        reg.apply(container, bus)

        ctx = _make_ctx("widget", "create")
        await bus.emit("pre_create", ctx)
        assert calls == ["widget"]

        calls.clear()
        ctx = _make_ctx("order", "create")
        await bus.emit("pre_create", ctx)
        assert calls == ["order"]


class TestRegistryIntegration:
    """End-to-end HTTP tests with registry-based overrides."""

    def _make_app(self, registry, registration):
        bus = EventBus()
        container = EntityContainer()
        container._registrations["widget"] = registration
        registry.apply(container, bus)

        app = FastAPI()
        router = EndpointFactory.create_router_from_registration(
            registration=registration,
            get_db=_get_db,
            get_current_user=_get_current_user,
            event_bus=bus,
        )
        app.include_router(router, prefix="/api/v1/widget")
        return app

    def _create_widget(self, client, name="Test Widget", color="blue"):
        resp = client.post("/api/v1/widget/", json={"name": name, "color": color})
        assert resp.status_code == 201
        return resp.json()

    def test_handler_override_via_registry(self, registry):
        reg = SlipStreamRegistry()
        container = EntityContainer()
        container.resolve_all(["widget"])
        registration = container.get("widget")

        received = {}

        @reg.handler("widget", "get")
        async def custom_get(ctx: RequestContext) -> Any:
            received["entity_id"] = ctx.entity_id
            received["entity"] = ctx.entity
            return ctx.entity

        app = self._make_app(reg, registration)
        client = TestClient(app)

        created = self._create_widget(client)
        response = client.get(f"/api/v1/widget/{created['entity_id']}")
        assert response.status_code == 200
        assert received["entity"] is not None

    def test_guard_blocks_unauthorized(self, registry):
        reg = SlipStreamRegistry()
        container = EntityContainer()
        container.resolve_all(["widget"])
        registration = container.get("widget")

        @reg.guard("widget", "delete")
        async def admins_only(ctx: RequestContext) -> None:
            if ctx.current_user.get("role") != "admin":
                raise HookError(403, "Admin role required")

        app = self._make_app(reg, registration)
        client = TestClient(app)

        created = self._create_widget(client)
        response = client.delete(f"/api/v1/widget/{created['entity_id']}")
        assert response.status_code == 403
        assert "Admin role required" in response.json()["detail"]

    def test_guard_allows_authorized(self, registry):
        """Guard passes when condition is met."""
        reg = SlipStreamRegistry()
        container = EntityContainer()
        container.resolve_all(["widget"])
        registration = container.get("widget")

        @reg.guard("widget", "delete")
        async def allow_all(ctx: RequestContext) -> None:
            pass  # No error = allowed

        app = self._make_app(reg, registration)
        client = TestClient(app)

        created = self._create_widget(client)
        response = client.delete(f"/api/v1/widget/{created['entity_id']}")
        assert response.status_code == 204

    def test_validate_rejects_bad_data(self, registry):
        reg = SlipStreamRegistry()
        container = EntityContainer()
        container.resolve_all(["widget"])
        registration = container.get("widget")

        @reg.validate("widget", "create")
        async def no_bad_names(ctx: RequestContext) -> None:
            if ctx.data.name == "FORBIDDEN":
                raise HookError(422, "That name is not allowed")

        app = self._make_app(reg, registration)
        client = TestClient(app)

        response = client.post(
            "/api/v1/widget/",
            json={"name": "FORBIDDEN", "color": "red"},
        )
        assert response.status_code == 422
        assert "not allowed" in response.json()["detail"]

    def test_validate_allows_good_data(self, registry):
        reg = SlipStreamRegistry()
        container = EntityContainer()
        container.resolve_all(["widget"])
        registration = container.get("widget")

        @reg.validate("widget", "create")
        async def no_bad_names(ctx: RequestContext) -> None:
            if ctx.data.name == "FORBIDDEN":
                raise HookError(422, "That name is not allowed")

        app = self._make_app(reg, registration)
        client = TestClient(app)

        response = client.post(
            "/api/v1/widget/",
            json={"name": "Good Widget", "color": "blue"},
        )
        assert response.status_code == 201

    def test_transform_modifies_data_before_create(self, registry):
        reg = SlipStreamRegistry()
        container = EntityContainer()
        container.resolve_all(["widget"])
        registration = container.get("widget")

        @reg.transform("widget", "create", when="before")
        async def uppercase_name(ctx: RequestContext) -> None:
            ctx.data.name = ctx.data.name.upper()

        app = self._make_app(reg, registration)
        client = TestClient(app)

        response = client.post(
            "/api/v1/widget/",
            json={"name": "lowercase widget", "color": "blue"},
        )
        assert response.status_code == 201
        assert response.json()["name"] == "LOWERCASE WIDGET"

    def test_on_hook_fires(self, registry):
        reg = SlipStreamRegistry()
        container = EntityContainer()
        container.resolve_all(["widget"])
        registration = container.get("widget")
        post_create_calls = []

        @reg.on("post_create", schema_name="widget")
        async def on_created(ctx: RequestContext) -> None:
            post_create_calls.append(ctx.schema_name)

        app = self._make_app(reg, registration)
        client = TestClient(app)

        self._create_widget(client)
        assert post_create_calls == ["widget"]

    def test_auto_event_bus_creation(self, schema_dir):
        """SlipStream creates EventBus automatically when registry is provided."""
        from slip_stream.app import SlipStream

        reg = SlipStreamRegistry()
        slip = SlipStream(
            app=FastAPI(),
            schema_dir=schema_dir,
            registry=reg,
        )
        assert slip._event_bus is not None


class TestRegistryPublish:
    """Tests for @registry.publish() decorator."""

    def test_publish_registers_entry(self):
        reg = SlipStreamRegistry()

        @reg.publish("widget", "create", "update")
        class WidgetEvents:
            pass

        assert len(reg._publish_entries) == 1
        entry = reg._publish_entries[0]
        assert entry.schema_name == "widget"
        assert entry.operations == ("create", "update")
        assert entry.include_data is True

    def test_publish_defaults_to_write_operations(self):
        reg = SlipStreamRegistry()
        reg.publish("widget")
        entry = reg._publish_entries[0]
        assert entry.operations == ("create", "update", "delete")

    def test_publish_custom_topic_and_key(self):
        reg = SlipStreamRegistry()
        reg.publish(
            "order",
            "create",
            topic="orders.{operation}",
            key="{entity_id}",
        )
        entry = reg._publish_entries[0]
        assert entry.topic == "orders.{operation}"
        assert entry.key == "{entity_id}"

    def test_publish_invalid_operation_raises(self):
        reg = SlipStreamRegistry()
        with pytest.raises(ValueError, match="Unknown operation"):
            reg.publish("widget", "explode")

    def test_publish_returns_original(self):
        reg = SlipStreamRegistry()

        class Original:
            pass

        result = reg.publish("widget", "create")(Original)
        assert result is Original

    def test_publish_with_custom_headers(self):
        reg = SlipStreamRegistry()
        reg.publish(
            "widget",
            "create",
            headers={"x-custom": "value"},
        )
        entry = reg._publish_entries[0]
        assert entry.headers == {"x-custom": "value"}

    def test_publish_include_data_false(self):
        reg = SlipStreamRegistry()
        reg.publish("widget", "delete", include_data=False)
        entry = reg._publish_entries[0]
        assert entry.include_data is False

    def test_get_publish_entries(self):
        reg = SlipStreamRegistry()
        reg.publish("widget", "create")
        reg.publish("order", "update")
        entries = reg.get_publish_entries()
        assert len(entries) == 2
        assert entries[0].schema_name == "widget"
        assert entries[1].schema_name == "order"

    def test_publish_wires_post_hooks_on_apply(self, container):
        reg = SlipStreamRegistry()
        bus = EventBus()
        reg.publish("widget", "create", "update")
        reg.apply(container, bus)
        # Should register 2 post hooks (post_create, post_update)
        assert bus.handler_count == 2

    @pytest.mark.asyncio
    async def test_publish_fires_on_create(self, container):
        """@publish wires a post_create hook that publishes to adapters."""
        from slip_stream.adapters.streaming.base import InMemoryStream

        reg = SlipStreamRegistry()
        bus = EventBus()
        stream = InMemoryStream()

        reg.publish("widget", "create", topic="widgets.created")
        reg.set_stream_bridge(type("Bridge", (), {"_adapters": [stream]})())
        reg.apply(container, bus)

        ctx = _make_ctx("widget", "create")
        # Simulate a result with entity_id
        ctx.result = type(
            "FakeResult",
            (),
            {
                "entity_id": "abc-123",
                "model_dump": lambda self, **kw: {"name": "Test"},
            },
        )()
        ctx.current_user = {"id": "user-1"}

        await bus.emit("post_create", ctx)

        assert len(stream.events) == 1
        event = stream.events[0]
        assert event.topic == "widgets.created"
        assert event.key == "abc-123"
        assert event.payload["event"] == "create"
        assert event.payload["schema_name"] == "widget"
        assert event.payload["entity_id"] == "abc-123"
        assert event.payload["user_id"] == "user-1"
        assert event.payload["data"] == {"name": "Test"}

    @pytest.mark.asyncio
    async def test_publish_template_interpolation(self, container):
        """Topic and key templates are interpolated with context values."""
        from slip_stream.adapters.streaming.base import InMemoryStream

        reg = SlipStreamRegistry()
        bus = EventBus()
        stream = InMemoryStream()

        reg.publish(
            "widget",
            "delete",
            topic="{schema_name}.{operation}.events",
            key="partition-{entity_id}",
        )
        reg.set_stream_bridge(type("Bridge", (), {"_adapters": [stream]})())
        reg.apply(container, bus)

        ctx = _make_ctx("widget", "delete")
        ctx.entity_id = "entity-xyz"
        ctx.result = None

        await bus.emit("post_delete", ctx)

        event = stream.events[0]
        assert event.topic == "widget.delete.events"
        assert event.key == "partition-entity-xyz"

    @pytest.mark.asyncio
    async def test_publish_include_data_false_omits_data(self, container):
        """When include_data=False, payload has no 'data' key."""
        from slip_stream.adapters.streaming.base import InMemoryStream

        reg = SlipStreamRegistry()
        bus = EventBus()
        stream = InMemoryStream()

        reg.publish("widget", "delete", include_data=False)
        reg.set_stream_bridge(type("Bridge", (), {"_adapters": [stream]})())
        reg.apply(container, bus)

        ctx = _make_ctx("widget", "delete")
        ctx.entity_id = "eid-1"
        ctx.result = type(
            "R",
            (),
            {
                "entity_id": "eid-1",
                "model_dump": lambda self, **kw: {"should": "not appear"},
            },
        )()

        await bus.emit("post_delete", ctx)

        assert "data" not in stream.events[0].payload

    @pytest.mark.asyncio
    async def test_publish_custom_headers_merged(self, container):
        """Extra static headers are merged into the message."""
        from slip_stream.adapters.streaming.base import InMemoryStream

        reg = SlipStreamRegistry()
        bus = EventBus()
        stream = InMemoryStream()

        reg.publish(
            "widget",
            "create",
            headers={"x-source": "petstore", "x-env": "test"},
        )
        reg.set_stream_bridge(type("Bridge", (), {"_adapters": [stream]})())
        reg.apply(container, bus)

        ctx = _make_ctx("widget", "create")
        ctx.result = type(
            "R",
            (),
            {
                "entity_id": "eid",
                "model_dump": lambda self, **kw: {},
            },
        )()

        await bus.emit("post_create", ctx)

        headers = stream.events[0].headers
        assert headers["x-source"] == "petstore"
        assert headers["x-env"] == "test"
        assert headers["x-event-type"] == "create"
        assert headers["x-schema-name"] == "widget"

    @pytest.mark.asyncio
    async def test_publish_via_ctx_extras_adapters(self, container):
        """Adapters can be injected via ctx.extras['stream_adapters']."""
        from slip_stream.adapters.streaming.base import InMemoryStream

        reg = SlipStreamRegistry()
        bus = EventBus()
        stream = InMemoryStream()

        reg.publish("widget", "create")
        # No bridge set — adapters come from ctx.extras
        reg.apply(container, bus)

        ctx = _make_ctx("widget", "create")
        ctx.extras = {"stream_adapters": [stream]}
        ctx.result = type(
            "R",
            (),
            {
                "entity_id": "eid",
                "model_dump": lambda self, **kw: {"name": "via-extras"},
            },
        )()

        await bus.emit("post_create", ctx)

        assert len(stream.events) == 1
        assert stream.events[0].payload["data"] == {"name": "via-extras"}

    @pytest.mark.asyncio
    async def test_publish_no_adapters_logs_error(self, container, caplog):
        """When no adapters are available, publish logs an error."""
        import logging

        reg = SlipStreamRegistry()
        bus = EventBus()

        reg.publish("widget", "create")
        reg.apply(container, bus)

        ctx = _make_ctx("widget", "create")
        ctx.result = type(
            "R",
            (),
            {
                "entity_id": "eid",
                "model_dump": lambda self, **kw: {},
            },
        )()

        with caplog.at_level(logging.ERROR, logger="slip_stream.registry"):
            await bus.emit("post_create", ctx)

        assert any("no stream adapters" in msg.lower() for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_publish_wildcard_schema(self, container):
        """@publish('*') registers hooks for all write operations."""
        from slip_stream.adapters.streaming.base import InMemoryStream

        reg = SlipStreamRegistry()
        bus = EventBus()
        stream = InMemoryStream()

        reg.publish("*")
        reg.set_stream_bridge(type("Bridge", (), {"_adapters": [stream]})())
        reg.apply(container, bus)

        # post_create on any schema should publish
        ctx = _make_ctx("widget", "create")
        ctx.result = type(
            "R",
            (),
            {
                "entity_id": "eid",
                "model_dump": lambda self, **kw: {},
            },
        )()
        await bus.emit("post_create", ctx)

        assert len(stream.events) == 1
        assert stream.events[0].payload["schema_name"] == "widget"

    def test_publish_e2e_via_http(self, registry):
        """End-to-end: HTTP POST triggers publish to InMemoryStream."""
        from slip_stream.adapters.streaming.base import InMemoryStream

        reg = SlipStreamRegistry()
        container_inst = EntityContainer()
        container_inst.resolve_all(["widget"])
        registration = container_inst.get("widget")
        bus = EventBus()
        stream = InMemoryStream()

        reg.publish("widget", "create", topic="widgets.new")
        reg.set_stream_bridge(type("Bridge", (), {"_adapters": [stream]})())
        reg.apply(container_inst, bus)

        app = FastAPI()
        router = EndpointFactory.create_router_from_registration(
            registration=registration,
            get_db=_get_db,
            get_current_user=_get_current_user,
            event_bus=bus,
        )
        app.include_router(router, prefix="/api/v1/widget")

        client = TestClient(app)
        resp = client.post(
            "/api/v1/widget/",
            json={"name": "Published Widget", "color": "green"},
        )
        assert resp.status_code == 201

        assert len(stream.events) == 1
        event = stream.events[0]
        assert event.topic == "widgets.new"
        assert event.payload["event"] == "create"
        assert event.payload["data"]["name"] == "Published Widget"
