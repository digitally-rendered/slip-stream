"""Tests for channel-scoped decorators.

Verifies that the ``channel`` parameter on ``@handler``, ``@guard``,
``@validate``, and ``@transform`` correctly scopes execution to
specific transport channels (REST, GraphQL, or all).
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from slip_stream.core.context import RequestContext
from slip_stream.core.events import EventBus, HookError
from slip_stream.core.operation import OperationExecutor, _resolve_handler_override
from slip_stream.registry import SlipStreamRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeRequest:
    def __init__(self):
        self.headers = {}
        self.state = SimpleNamespace()
        self.query_params = {}
        self.url = SimpleNamespace(path="/test")


class _FakeEntity(BaseModel):
    id: str = "abc"
    name: str = "test"


class _FakeCreateModel(BaseModel):
    name: str = "test"


@pytest.fixture
def mock_registration():
    entity = _FakeEntity()
    svc = AsyncMock()
    svc.execute = AsyncMock(return_value=entity)
    repo = AsyncMock()
    repo.get_by_entity_id = AsyncMock(return_value=entity)

    reg = SimpleNamespace(
        schema_name="widget",
        document_model=_FakeEntity,
        create_model=_FakeCreateModel,
        update_model=_FakeCreateModel,
        repository_class=MagicMock(return_value=repo),
        services={
            "create": MagicMock(return_value=svc),
            "get": MagicMock(return_value=svc),
            "list": MagicMock(return_value=svc),
            "update": MagicMock(return_value=svc),
            "delete": MagicMock(return_value=svc),
        },
        handler_overrides={},
    )
    return reg


def _make_ctx(channel="rest", operation="create"):
    return RequestContext(
        request=_FakeRequest(),
        operation=operation,
        schema_name="widget",
        data=_FakeCreateModel(),
        current_user={"id": "user-1"},
        db="fake-db",
        channel=channel,
    )


# ---------------------------------------------------------------------------
# RequestContext.channel field
# ---------------------------------------------------------------------------


class TestContextChannel:

    def test_defaults_to_rest(self):
        ctx = RequestContext(
            request=_FakeRequest(),
            operation="create",
            schema_name="widget",
        )
        assert ctx.channel == "rest"

    def test_can_set_graphql(self):
        ctx = _make_ctx(channel="graphql")
        assert ctx.channel == "graphql"


# ---------------------------------------------------------------------------
# Handler override resolution with channel
# ---------------------------------------------------------------------------


class TestChannelOverrideResolution:

    def test_channel_specific_override(self, mock_registration):
        mock_registration.handler_overrides = {
            "create": "universal",
            "create@channel:graphql": "gql_handler",
        }
        result = _resolve_handler_override(
            mock_registration.handler_overrides, "create", channel="graphql"
        )
        assert result == "gql_handler"

    def test_rest_channel_does_not_match_graphql(self, mock_registration):
        mock_registration.handler_overrides = {
            "create": "universal",
            "create@channel:graphql": "gql_handler",
        }
        result = _resolve_handler_override(
            mock_registration.handler_overrides, "create", channel="rest"
        )
        assert result == "universal"

    def test_version_plus_channel(self, mock_registration):
        mock_registration.handler_overrides = {
            "create": "universal",
            "create@2.0.0": "v2",
            "create@channel:graphql": "gql",
            "create@2.0.0@channel:graphql": "v2_gql",
        }
        result = _resolve_handler_override(
            mock_registration.handler_overrides,
            "create",
            schema_version="2.0.0",
            channel="graphql",
        )
        assert result == "v2_gql"


# ---------------------------------------------------------------------------
# OperationExecutor with channel-scoped overrides
# ---------------------------------------------------------------------------


class TestExecutorWithChannels:

    @pytest.mark.asyncio
    async def test_graphql_channel_override_fires(self, mock_registration):
        called = []

        async def gql_handler(ctx):
            called.append("graphql")
            return _FakeEntity()

        mock_registration.handler_overrides["create@channel:graphql"] = gql_handler

        executor = OperationExecutor(mock_registration)
        ctx = _make_ctx(channel="graphql")
        await executor.execute_create(ctx)

        assert called == ["graphql"]

    @pytest.mark.asyncio
    async def test_rest_channel_does_not_fire_graphql_override(self, mock_registration):
        called = []

        async def gql_handler(ctx):
            called.append("graphql")
            return _FakeEntity()

        mock_registration.handler_overrides["create@channel:graphql"] = gql_handler

        executor = OperationExecutor(mock_registration)
        ctx = _make_ctx(channel="rest")
        await executor.execute_create(ctx)

        # GraphQL override should NOT fire for REST
        assert called == []

    @pytest.mark.asyncio
    async def test_universal_override_fires_for_both(self, mock_registration):
        called = []

        async def universal_handler(ctx):
            called.append(ctx.channel)
            return _FakeEntity()

        mock_registration.handler_overrides["create"] = universal_handler

        executor = OperationExecutor(mock_registration)

        ctx_rest = _make_ctx(channel="rest")
        await executor.execute_create(ctx_rest)

        ctx_gql = _make_ctx(channel="graphql")
        await executor.execute_create(ctx_gql)

        assert called == ["rest", "graphql"]


# ---------------------------------------------------------------------------
# SlipStreamRegistry with channel
# ---------------------------------------------------------------------------


class TestRegistryChannelDecorator:

    def test_handler_channel_validation(self):
        reg = SlipStreamRegistry()
        with pytest.raises(ValueError, match="Unknown channel"):
            @reg.handler("widget", "create", channel="invalid")
            async def h(ctx): ...

    def test_guard_channel_validation(self):
        reg = SlipStreamRegistry()
        with pytest.raises(ValueError, match="Unknown channel"):
            @reg.guard("widget", "create", channel="invalid")
            async def g(ctx): ...

    def test_validate_channel_validation(self):
        reg = SlipStreamRegistry()
        with pytest.raises(ValueError, match="Unknown channel"):
            @reg.validate("widget", "create", channel="invalid")
            async def v(ctx): ...

    def test_transform_channel_validation(self):
        reg = SlipStreamRegistry()
        with pytest.raises(ValueError, match="Unknown channel"):
            @reg.transform("widget", "create", channel="invalid")
            async def t(ctx): ...

    def test_handler_with_channel_stored_correctly(self):
        reg = SlipStreamRegistry()

        @reg.handler("widget", "create", channel="graphql")
        async def h(ctx): ...

        assert len(reg._handlers) == 1
        assert reg._handlers[0].channel == "graphql"

    def test_guard_with_channel_stored_correctly(self):
        reg = SlipStreamRegistry()

        @reg.guard("widget", "create", channel="rest")
        async def g(ctx): ...

        assert len(reg._guards) == 1
        assert reg._guards[0].channel == "rest"

    def test_apply_generates_channel_key(self, mock_registration):
        from slip_stream.container import EntityContainer

        container = EntityContainer()
        container._registrations["widget"] = mock_registration

        bus = EventBus()
        reg = SlipStreamRegistry()

        @reg.handler("widget", "create", channel="graphql")
        async def h(ctx): ...

        reg.apply(container, bus)

        assert "create@channel:graphql" in mock_registration.handler_overrides

    def test_apply_channel_plus_version_key(self, mock_registration):
        from slip_stream.container import EntityContainer

        container = EntityContainer()
        container._registrations["widget"] = mock_registration

        bus = EventBus()
        reg = SlipStreamRegistry()

        @reg.handler("widget", "create", version="2.0.0", channel="graphql")
        async def h(ctx): ...

        reg.apply(container, bus)

        assert "create@2.0.0@channel:graphql" in mock_registration.handler_overrides


# ---------------------------------------------------------------------------
# Channel-scoped hooks (guards, validators, transforms)
# ---------------------------------------------------------------------------


class TestChannelScopedHooks:

    @pytest.mark.asyncio
    async def test_guard_only_fires_for_matching_channel(self, mock_registration):
        from slip_stream.container import EntityContainer

        container = EntityContainer()
        container._registrations["widget"] = mock_registration

        bus = EventBus()
        reg = SlipStreamRegistry()
        guard_calls = []

        @reg.guard("widget", "create", channel="graphql")
        async def gql_guard(ctx):
            guard_calls.append(ctx.channel)

        reg.apply(container, bus)

        # Fire from REST — guard should NOT fire
        ctx_rest = _make_ctx(channel="rest")
        await bus.emit("pre_create", ctx_rest)
        assert guard_calls == []

        # Fire from GraphQL — guard should fire
        ctx_gql = _make_ctx(channel="graphql")
        await bus.emit("pre_create", ctx_gql)
        assert guard_calls == ["graphql"]

    @pytest.mark.asyncio
    async def test_universal_guard_fires_for_all_channels(self, mock_registration):
        from slip_stream.container import EntityContainer

        container = EntityContainer()
        container._registrations["widget"] = mock_registration

        bus = EventBus()
        reg = SlipStreamRegistry()
        guard_calls = []

        @reg.guard("widget", "create")  # default channel="*"
        async def universal_guard(ctx):
            guard_calls.append(ctx.channel)

        reg.apply(container, bus)

        ctx_rest = _make_ctx(channel="rest")
        await bus.emit("pre_create", ctx_rest)

        ctx_gql = _make_ctx(channel="graphql")
        await bus.emit("pre_create", ctx_gql)

        assert guard_calls == ["rest", "graphql"]

    @pytest.mark.asyncio
    async def test_transform_channel_scoped(self, mock_registration):
        from slip_stream.container import EntityContainer

        container = EntityContainer()
        container._registrations["widget"] = mock_registration

        bus = EventBus()
        reg = SlipStreamRegistry()
        transform_calls = []

        @reg.transform("widget", "create", when="before", channel="rest")
        async def rest_transform(ctx):
            transform_calls.append("rest")

        reg.apply(container, bus)

        # GraphQL should not trigger the REST-only transform
        ctx_gql = _make_ctx(channel="graphql")
        await bus.emit("pre_create", ctx_gql)
        assert transform_calls == []

        # REST should trigger it
        ctx_rest = _make_ctx(channel="rest")
        await bus.emit("pre_create", ctx_rest)
        assert transform_calls == ["rest"]
