"""Tests for the OperationExecutor — shared domain lifecycle.

Verifies that the executor correctly orchestrates:
- EventBus pre/post hooks
- Handler override resolution (including version-scoped)
- Default service fallback
- HookError propagation
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from slip_stream.core.context import RequestContext
from slip_stream.core.events import EventBus, HookError
from slip_stream.core.operation import OperationExecutor, _resolve_handler_override

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeRequest:
    """Minimal fake request for RequestContext."""

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
def fake_request():
    return _FakeRequest()


@pytest.fixture
def mock_repo():
    repo = AsyncMock()
    repo.get_by_entity_id = AsyncMock(return_value=_FakeEntity())
    return repo


@pytest.fixture
def mock_service():
    svc = AsyncMock()
    svc.execute = AsyncMock(return_value=_FakeEntity())
    return svc


@pytest.fixture
def mock_registration(mock_repo, mock_service):
    """Create a minimal EntityRegistration-like object."""
    reg = SimpleNamespace(
        schema_name="widget",
        document_model=_FakeEntity,
        create_model=_FakeCreateModel,
        update_model=_FakeCreateModel,
        repository_class=MagicMock(return_value=mock_repo),
        services={
            "create": MagicMock(return_value=mock_service),
            "get": MagicMock(return_value=mock_service),
            "list": MagicMock(return_value=mock_service),
            "update": MagicMock(return_value=mock_service),
            "delete": MagicMock(return_value=mock_service),
        },
        handler_overrides={},
    )
    return reg


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def ctx(fake_request):
    return RequestContext(
        request=fake_request,
        operation="create",
        schema_name="widget",
        data=_FakeCreateModel(),
        current_user={"id": "user-1"},
        db="fake-db",
    )


# ---------------------------------------------------------------------------
# _resolve_handler_override
# ---------------------------------------------------------------------------


class TestResolveHandlerOverride:

    def test_universal_fallback(self):
        overrides = {"create": "universal"}
        assert _resolve_handler_override(overrides, "create") == "universal"

    def test_version_specific(self):
        overrides = {"create": "universal", "create@2.0.0": "v2"}
        assert (
            _resolve_handler_override(overrides, "create", schema_version="2.0.0")
            == "v2"
        )

    def test_version_fallback_to_universal(self):
        overrides = {"create": "universal"}
        assert (
            _resolve_handler_override(overrides, "create", schema_version="2.0.0")
            == "universal"
        )

    def test_channel_specific(self):
        overrides = {"create": "universal", "create@channel:graphql": "gql"}
        assert (
            _resolve_handler_override(overrides, "create", channel="graphql") == "gql"
        )

    def test_channel_fallback_to_universal(self):
        overrides = {"create": "universal"}
        assert (
            _resolve_handler_override(overrides, "create", channel="graphql")
            == "universal"
        )

    def test_version_plus_channel(self):
        overrides = {
            "create": "universal",
            "create@2.0.0": "v2",
            "create@channel:graphql": "gql",
            "create@2.0.0@channel:graphql": "v2-gql",
        }
        result = _resolve_handler_override(
            overrides, "create", schema_version="2.0.0", channel="graphql"
        )
        assert result == "v2-gql"

    def test_no_override(self):
        assert _resolve_handler_override({}, "create") is None

    def test_star_channel_treated_as_universal(self):
        overrides = {"create": "universal"}
        assert (
            _resolve_handler_override(overrides, "create", channel="*") == "universal"
        )


# ---------------------------------------------------------------------------
# OperationExecutor
# ---------------------------------------------------------------------------


class TestExecutorCreate:

    @pytest.mark.asyncio
    async def test_calls_default_service(self, mock_registration, ctx):
        executor = OperationExecutor(mock_registration)
        result = await executor.execute_create(ctx)
        assert result is not None
        mock_registration.services["create"].assert_called_once()

    @pytest.mark.asyncio
    async def test_fires_pre_and_post_hooks(self, mock_registration, event_bus, ctx):
        pre_called = []
        post_called = []

        async def pre_hook(c):
            pre_called.append(c.operation)

        async def post_hook(c):
            post_called.append(c.operation)

        event_bus.register("pre_create", pre_hook)
        event_bus.register("post_create", post_hook)

        executor = OperationExecutor(mock_registration, event_bus)
        await executor.execute_create(ctx)

        assert pre_called == ["create"]
        assert post_called == ["create"]

    @pytest.mark.asyncio
    async def test_handler_override_takes_precedence(self, mock_registration, ctx):
        override_result = {"custom": True}

        async def custom_handler(c):
            return override_result

        mock_registration.handler_overrides["create"] = custom_handler

        executor = OperationExecutor(mock_registration)
        result = await executor.execute_create(ctx)
        assert result == override_result
        # Default service should NOT have been called
        mock_registration.services["create"].assert_not_called()

    @pytest.mark.asyncio
    async def test_version_scoped_override(self, mock_registration, ctx):
        async def v2_handler(c):
            return "v2-result"

        mock_registration.handler_overrides["create@2.0.0"] = v2_handler
        ctx.schema_version = "2.0.0"

        executor = OperationExecutor(mock_registration)
        result = await executor.execute_create(ctx)
        assert result == "v2-result"

    @pytest.mark.asyncio
    async def test_hook_error_propagates(self, mock_registration, event_bus, ctx):
        async def failing_guard(c):
            raise HookError(403, "Forbidden")

        event_bus.register("pre_create", failing_guard)

        executor = OperationExecutor(mock_registration, event_bus)
        with pytest.raises(HookError, match="Forbidden"):
            await executor.execute_create(ctx)

    @pytest.mark.asyncio
    async def test_no_event_bus_still_works(self, mock_registration, ctx):
        executor = OperationExecutor(mock_registration, event_bus=None)
        result = await executor.execute_create(ctx)
        assert result is not None


class TestExecutorGet:

    @pytest.mark.asyncio
    async def test_returns_entity(self, mock_registration, ctx):
        ctx.operation = "get"
        ctx.entity = _FakeEntity(name="found")
        executor = OperationExecutor(mock_registration)
        result = await executor.execute_get(ctx)
        assert result.name == "found"

    @pytest.mark.asyncio
    async def test_override_replaces_default(self, mock_registration, ctx):
        ctx.operation = "get"
        ctx.entity = _FakeEntity()

        async def custom_get(c):
            return {"overridden": True}

        mock_registration.handler_overrides["get"] = custom_get

        executor = OperationExecutor(mock_registration)
        result = await executor.execute_get(ctx)
        assert result == {"overridden": True}


class TestExecutorList:

    @pytest.mark.asyncio
    async def test_calls_list_service(self, mock_registration, ctx):
        ctx.operation = "list"
        executor = OperationExecutor(mock_registration)
        result = await executor.execute_list(ctx)
        assert result is not None
        mock_registration.services["list"].assert_called_once()


class TestExecutorUpdate:

    @pytest.mark.asyncio
    async def test_calls_update_service(self, mock_registration, ctx):
        ctx.operation = "update"
        ctx.entity_id = uuid.uuid4()
        ctx.entity = _FakeEntity()
        executor = OperationExecutor(mock_registration)
        result = await executor.execute_update(ctx)
        assert result is not None

    @pytest.mark.asyncio
    async def test_fires_hooks(self, mock_registration, event_bus, ctx):
        ctx.operation = "update"
        ctx.entity_id = uuid.uuid4()
        ctx.entity = _FakeEntity()
        events_fired = []

        async def track(c):
            events_fired.append(c.operation)

        event_bus.register("pre_update", track)
        event_bus.register("post_update", track)

        executor = OperationExecutor(mock_registration, event_bus)
        await executor.execute_update(ctx)
        assert events_fired == ["update", "update"]


class TestExecutorDelete:

    @pytest.mark.asyncio
    async def test_calls_delete_service(self, mock_registration, ctx):
        ctx.operation = "delete"
        ctx.entity_id = uuid.uuid4()
        ctx.entity = _FakeEntity()
        executor = OperationExecutor(mock_registration)
        await executor.execute_delete(ctx)
        mock_registration.services["delete"].assert_called_once()

    @pytest.mark.asyncio
    async def test_hook_error_blocks_delete(self, mock_registration, event_bus, ctx):
        ctx.operation = "delete"
        ctx.entity_id = uuid.uuid4()
        ctx.entity = _FakeEntity()

        async def block_delete(c):
            raise HookError(403, "Cannot delete")

        event_bus.register("pre_delete", block_delete)

        executor = OperationExecutor(mock_registration, event_bus)
        with pytest.raises(HookError, match="Cannot delete"):
            await executor.execute_delete(ctx)

        # Service should not have been called
        mock_registration.services["delete"].assert_not_called()
