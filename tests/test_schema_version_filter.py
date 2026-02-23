"""Tests for Phase 5: Multi-version object handling.

Covers:
- RequestContext.schema_version field and auto-population from header
- EntityContainer.resolve_version()
- SchemaVersionFilter request/response handling
- ResponseEnvelopeFilter schema_version in meta
- Version-aware decorator routing in SlipStreamRegistry
"""

import json
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.testclient import TestClient as StarletteTestClient

from slip_stream.adapters.api.filters.base import FilterContext
from slip_stream.adapters.api.filters.envelope import ResponseEnvelopeFilter
from slip_stream.adapters.api.filters.schema_version import SchemaVersionFilter
from slip_stream.adapters.api.endpoint_factory import _resolve_handler_override
from slip_stream.container import EntityContainer, EntityRegistration
from slip_stream.core.context import RequestContext
from slip_stream.core.events import EventBus
from slip_stream.core.schema.registry import SchemaRegistry
from slip_stream.registry import SlipStreamRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry():
    SchemaRegistry.reset()
    yield
    SchemaRegistry.reset()


@pytest.fixture
def mock_request():
    """Create a mock Starlette request."""
    request = MagicMock(spec=Request)
    request.headers = {}
    request.state = MagicMock()
    request.state.filter_context = None
    del request.state.schema_name  # ensure AttributeError on getattr
    return request


@pytest.fixture
def mock_request_with_version():
    """Create a mock request with X-Schema-Version header."""
    request = MagicMock(spec=Request)
    request.headers = {"x-schema-version": "1.0.0"}
    request.state = MagicMock()
    request.state.filter_context = None
    del request.state.schema_name
    return request


# ---------------------------------------------------------------------------
# RequestContext.schema_version
# ---------------------------------------------------------------------------


class TestRequestContextSchemaVersion:

    def test_schema_version_defaults_to_none(self, mock_request):
        ctx = RequestContext(
            request=mock_request,
            operation="get",
            schema_name="widget",
        )
        assert ctx.schema_version is None

    def test_schema_version_set_explicitly(self, mock_request):
        ctx = RequestContext(
            request=mock_request,
            operation="get",
            schema_name="widget",
            schema_version="2.0.0",
        )
        assert ctx.schema_version == "2.0.0"

    def test_from_request_pulls_schema_version_from_header(
        self, mock_request_with_version
    ):
        ctx = RequestContext.from_request(
            request=mock_request_with_version,
            operation="get",
            schema_name="widget",
        )
        assert ctx.schema_version == "1.0.0"

    def test_from_request_no_header_stays_none(self, mock_request):
        ctx = RequestContext.from_request(
            request=mock_request,
            operation="get",
            schema_name="widget",
        )
        assert ctx.schema_version is None

    def test_from_request_explicit_overrides_header(
        self, mock_request_with_version
    ):
        ctx = RequestContext.from_request(
            request=mock_request_with_version,
            operation="get",
            schema_name="widget",
            schema_version="3.0.0",
        )
        assert ctx.schema_version == "3.0.0"

    def test_from_request_pulls_from_filter_context(self, mock_request):
        filter_ctx = FilterContext()
        filter_ctx.extras["schema_version"] = "2.0.0"
        mock_request.state.filter_context = filter_ctx
        ctx = RequestContext.from_request(
            request=mock_request,
            operation="get",
            schema_name="widget",
        )
        assert ctx.schema_version == "2.0.0"


# ---------------------------------------------------------------------------
# EntityContainer.resolve_version
# ---------------------------------------------------------------------------


class TestEntityContainerResolveVersion:

    def test_resolve_none_returns_latest_models(self, tmp_path):
        registry = SchemaRegistry(schema_dir=tmp_path)
        registry.register_schema(
            "widget",
            {
                "type": "object",
                "version": "1.0.0",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            version="1.0.0",
        )
        container = EntityContainer()
        container.resolve_all(["widget"])
        reg = container.get("widget")

        doc, create, update = container.resolve_version("widget", None)
        assert doc is reg.document_model
        assert create is reg.create_model
        assert update is reg.update_model

    def test_resolve_latest_returns_latest_models(self, tmp_path):
        registry = SchemaRegistry(schema_dir=tmp_path)
        registry.register_schema(
            "widget",
            {
                "type": "object",
                "version": "1.0.0",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            version="1.0.0",
        )
        container = EntityContainer()
        container.resolve_all(["widget"])
        reg = container.get("widget")

        doc, create, update = container.resolve_version("widget", "latest")
        assert doc is reg.document_model

    def test_resolve_specific_version(self, tmp_path):
        registry = SchemaRegistry(schema_dir=tmp_path)
        registry.register_schema(
            "widget",
            {
                "type": "object",
                "version": "1.0.0",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            version="1.0.0",
        )
        registry.register_schema(
            "widget",
            {
                "type": "object",
                "version": "2.0.0",
                "properties": {
                    "name": {"type": "string"},
                    "color": {"type": "string"},
                },
                "required": ["name"],
            },
            version="2.0.0",
        )
        container = EntityContainer()
        container.resolve_all(["widget"])

        doc, create, update = container.resolve_version("widget", "1.0.0")
        assert issubclass(doc, BaseModel)

    def test_resolve_version_caches(self, tmp_path):
        registry = SchemaRegistry(schema_dir=tmp_path)
        registry.register_schema(
            "widget",
            {
                "type": "object",
                "version": "1.0.0",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            version="1.0.0",
        )
        container = EntityContainer()
        container.resolve_all(["widget"])

        triple1 = container.resolve_version("widget", "1.0.0")
        triple2 = container.resolve_version("widget", "1.0.0")
        assert triple1[0] is triple2[0]

    def test_resolve_unknown_schema_raises(self, tmp_path):
        registry = SchemaRegistry(schema_dir=tmp_path)
        registry.register_schema(
            "widget",
            {
                "type": "object",
                "version": "1.0.0",
                "properties": {"name": {"type": "string"}},
            },
            version="1.0.0",
        )
        container = EntityContainer()
        container.resolve_all(["widget"])

        with pytest.raises(KeyError):
            container.resolve_version("nonexistent", "1.0.0")


# ---------------------------------------------------------------------------
# SchemaVersionFilter
# ---------------------------------------------------------------------------


class TestSchemaVersionFilter:

    @pytest.fixture
    def version_filter(self):
        return SchemaVersionFilter()

    @pytest.mark.asyncio
    async def test_on_request_captures_header(self, version_filter):
        request = MagicMock(spec=Request)
        request.headers = {"x-schema-version": "1.0.0"}
        context = FilterContext()

        await version_filter.on_request(request, context)
        assert context.extras["schema_version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_on_request_no_header(self, version_filter):
        request = MagicMock(spec=Request)
        request.headers = {}
        context = FilterContext()

        await version_filter.on_request(request, context)
        assert "schema_version" not in context.extras

    @pytest.mark.asyncio
    async def test_on_response_passthrough_without_version(self, version_filter):
        request = MagicMock(spec=Request)
        context = FilterContext()
        response = Response(content="test", status_code=200)

        result = await version_filter.on_response(request, response, context)
        assert result is response

    @pytest.mark.asyncio
    async def test_on_response_passthrough_on_error(self, version_filter):
        request = MagicMock(spec=Request)
        context = FilterContext()
        context.extras["schema_version"] = "1.0.0"
        response = Response(content="error", status_code=500)

        result = await version_filter.on_response(request, response, context)
        assert result is response

    @pytest.mark.asyncio
    async def test_on_response_projects_object(self, version_filter, tmp_path):
        registry = SchemaRegistry(schema_dir=tmp_path)
        registry.register_schema(
            "widget",
            {
                "type": "object",
                "version": "1.0.0",
                "properties": {
                    "name": {"type": "string"},
                    "id": {"type": "string"},
                },
            },
            version="1.0.0",
        )
        registry.register_schema(
            "widget",
            {
                "type": "object",
                "version": "2.0.0",
                "properties": {
                    "name": {"type": "string"},
                    "color": {"type": "string"},
                    "id": {"type": "string"},
                },
            },
            version="2.0.0",
        )

        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/api/v1/widget/123"
        request.path_params = {}
        request.state = MagicMock()
        del request.state.schema_name

        context = FilterContext()
        context.extras["schema_version"] = "1.0.0"

        body = json.dumps({"name": "foo", "color": "blue", "id": "abc"})
        response = Response(content=body, status_code=200, media_type="application/json")

        result = await version_filter.on_response(request, response, context)
        data = json.loads(result.body)

        # v1.0.0 only has name and id, so color should be stripped
        assert "name" in data
        assert "id" in data
        assert "color" not in data

    @pytest.mark.asyncio
    async def test_on_response_null_fills_missing_fields(
        self, version_filter, tmp_path
    ):
        registry = SchemaRegistry(schema_dir=tmp_path)
        registry.register_schema(
            "widget",
            {
                "type": "object",
                "version": "1.0.0",
                "properties": {
                    "name": {"type": "string"},
                    "legacy_field": {"type": "string"},
                },
            },
            version="1.0.0",
        )

        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/api/v1/widget/123"
        request.path_params = {}
        request.state = MagicMock()
        del request.state.schema_name

        context = FilterContext()
        context.extras["schema_version"] = "1.0.0"

        # Response from latest version doesn't have legacy_field
        body = json.dumps({"name": "foo"})
        response = Response(content=body, status_code=200, media_type="application/json")

        result = await version_filter.on_response(request, response, context)
        data = json.loads(result.body)

        assert data["name"] == "foo"
        assert data["legacy_field"] is None

    @pytest.mark.asyncio
    async def test_on_response_handles_list(self, version_filter, tmp_path):
        registry = SchemaRegistry(schema_dir=tmp_path)
        registry.register_schema(
            "widget",
            {
                "type": "object",
                "version": "1.0.0",
                "properties": {"name": {"type": "string"}},
            },
            version="1.0.0",
        )

        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/api/v1/widget/"
        request.path_params = {}
        request.state = MagicMock()
        del request.state.schema_name

        context = FilterContext()
        context.extras["schema_version"] = "1.0.0"

        body = json.dumps([
            {"name": "a", "extra": "x"},
            {"name": "b", "extra": "y"},
        ])
        response = Response(content=body, status_code=200, media_type="application/json")

        result = await version_filter.on_response(request, response, context)
        data = json.loads(result.body)

        assert len(data) == 2
        assert data[0] == {"name": "a"}
        assert data[1] == {"name": "b"}

    def test_extract_schema_name_from_api_path(self, version_filter):
        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/api/v1/my-widget/123"
        request.path_params = {}
        request.state = MagicMock()
        del request.state.schema_name

        name = version_filter._extract_schema_name(request)
        assert name == "my_widget"

    def test_extract_schema_name_from_simple_path(self, version_filter):
        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/widget/"
        request.path_params = {}
        request.state = MagicMock()
        del request.state.schema_name

        name = version_filter._extract_schema_name(request)
        assert name == "widget"

    def test_extract_schema_name_from_path_params(self, version_filter):
        request = MagicMock(spec=Request)
        request.path_params = {"schema_name": "gadget"}

        name = version_filter._extract_schema_name(request)
        assert name == "gadget"

    def test_filter_order(self, version_filter):
        assert version_filter.order == 5


# ---------------------------------------------------------------------------
# ResponseEnvelopeFilter — schema_version in meta
# ---------------------------------------------------------------------------


class TestEnvelopeSchemaVersion:

    @pytest.mark.asyncio
    async def test_meta_includes_schema_version(self):
        envelope_filter = ResponseEnvelopeFilter()
        request = MagicMock(spec=Request)
        request.query_params = {}

        context = FilterContext()
        context.extras["request_id"] = "test-123"
        context.extras["schema_version"] = "2.0.0"

        body = json.dumps({"name": "foo"})
        response = Response(content=body, status_code=200, media_type="application/json")

        result = await envelope_filter.on_response(request, response, context)
        data = json.loads(result.body)

        assert data["meta"]["schema_version"] == "2.0.0"

    @pytest.mark.asyncio
    async def test_meta_omits_schema_version_when_absent(self):
        envelope_filter = ResponseEnvelopeFilter()
        request = MagicMock(spec=Request)
        request.query_params = {}

        context = FilterContext()
        context.extras["request_id"] = "test-123"

        body = json.dumps({"name": "foo"})
        response = Response(content=body, status_code=200, media_type="application/json")

        result = await envelope_filter.on_response(request, response, context)
        data = json.loads(result.body)

        assert "schema_version" not in data["meta"]


# ---------------------------------------------------------------------------
# _resolve_handler_override
# ---------------------------------------------------------------------------


class TestResolveHandlerOverride:

    def test_no_version_returns_base_override(self):
        overrides = {"create": "base_handler"}
        result = _resolve_handler_override(overrides, "create")
        assert result == "base_handler"

    def test_version_specific_override_found(self):
        overrides = {
            "create": "base_handler",
            "create@2.0.0": "v2_handler",
        }
        result = _resolve_handler_override(overrides, "create", "2.0.0")
        assert result == "v2_handler"

    def test_version_falls_back_to_base(self):
        overrides = {"create": "base_handler"}
        result = _resolve_handler_override(overrides, "create", "2.0.0")
        assert result == "base_handler"

    def test_no_override_returns_none(self):
        result = _resolve_handler_override({}, "create", "1.0.0")
        assert result is None

    def test_version_specific_without_base(self):
        overrides = {"create@2.0.0": "v2_handler"}
        result = _resolve_handler_override(overrides, "create", "2.0.0")
        assert result == "v2_handler"

    def test_different_version_no_match_no_base(self):
        overrides = {"create@2.0.0": "v2_handler"}
        result = _resolve_handler_override(overrides, "create", "1.0.0")
        assert result is None


# ---------------------------------------------------------------------------
# Version-aware decorators in SlipStreamRegistry
# ---------------------------------------------------------------------------


class TestVersionAwareDecorators:

    def test_handler_with_version(self):
        reg = SlipStreamRegistry()

        @reg.handler("widget", "create", version="2.0.0")
        async def v2_create(ctx):
            pass

        assert len(reg._handlers) == 1
        assert reg._handlers[0].version == "2.0.0"

    def test_handler_without_version(self):
        reg = SlipStreamRegistry()

        @reg.handler("widget", "create")
        async def create(ctx):
            pass

        assert reg._handlers[0].version is None

    def test_guard_with_version(self):
        reg = SlipStreamRegistry()

        @reg.guard("widget", "delete", version="1.0.0")
        async def v1_guard(ctx):
            pass

        assert len(reg._guards) == 1
        assert reg._guards[0].version == "1.0.0"

    def test_validate_with_version(self):
        reg = SlipStreamRegistry()

        @reg.validate("order", "create", version="2.0.0")
        async def v2_validate(ctx):
            pass

        assert len(reg._validators) == 1
        assert reg._validators[0].version == "2.0.0"

    def test_transform_with_version(self):
        reg = SlipStreamRegistry()

        @reg.transform("user", "create", when="before", version="1.0.0")
        async def v1_transform(ctx):
            pass

        assert len(reg._transforms_before) == 1
        assert reg._transforms_before[0].version == "1.0.0"

    def test_apply_version_handler_key_format(self, tmp_path):
        schema_registry = SchemaRegistry(schema_dir=tmp_path)
        schema_registry.register_schema(
            "widget",
            {
                "type": "object",
                "version": "1.0.0",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            version="1.0.0",
        )

        reg = SlipStreamRegistry()

        @reg.handler("widget", "create", version="2.0.0")
        async def v2_create(ctx):
            return "v2"

        @reg.handler("widget", "create")
        async def default_create(ctx):
            return "default"

        container = EntityContainer()
        container.resolve_all(["widget"])
        event_bus = EventBus()

        reg.apply(container, event_bus)

        registration = container.get("widget")
        assert "create" in registration.handler_overrides
        assert "create@2.0.0" in registration.handler_overrides

    @pytest.mark.asyncio
    async def test_version_hook_only_fires_for_matching_version(self, tmp_path):
        schema_registry = SchemaRegistry(schema_dir=tmp_path)
        schema_registry.register_schema(
            "widget",
            {
                "type": "object",
                "version": "1.0.0",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            version="1.0.0",
        )

        reg = SlipStreamRegistry()
        calls = []

        @reg.guard("widget", "create", version="2.0.0")
        async def v2_guard(ctx):
            calls.append("v2_guard")

        container = EntityContainer()
        container.resolve_all(["widget"])
        event_bus = EventBus()
        reg.apply(container, event_bus)

        # Create a context with version 1.0.0 — guard should NOT fire
        mock_req = MagicMock(spec=Request)
        mock_req.headers = {}
        mock_req.state = MagicMock()
        mock_req.state.filter_context = None
        del mock_req.state.schema_name

        ctx = RequestContext(
            request=mock_req,
            operation="create",
            schema_name="widget",
            schema_version="1.0.0",
        )
        await event_bus.emit("pre_create", ctx)
        assert calls == []

        # Now with version 2.0.0 — guard SHOULD fire
        ctx2 = RequestContext(
            request=mock_req,
            operation="create",
            schema_name="widget",
            schema_version="2.0.0",
        )
        await event_bus.emit("pre_create", ctx2)
        assert calls == ["v2_guard"]

    @pytest.mark.asyncio
    async def test_unversioned_hook_fires_for_all_versions(self, tmp_path):
        schema_registry = SchemaRegistry(schema_dir=tmp_path)
        schema_registry.register_schema(
            "widget",
            {
                "type": "object",
                "version": "1.0.0",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            version="1.0.0",
        )

        reg = SlipStreamRegistry()
        calls = []

        @reg.guard("widget", "create")
        async def universal_guard(ctx):
            calls.append(f"guard:{ctx.schema_version}")

        container = EntityContainer()
        container.resolve_all(["widget"])
        event_bus = EventBus()
        reg.apply(container, event_bus)

        mock_req = MagicMock(spec=Request)
        mock_req.headers = {}
        mock_req.state = MagicMock()
        mock_req.state.filter_context = None
        del mock_req.state.schema_name

        ctx1 = RequestContext(
            request=mock_req,
            operation="create",
            schema_name="widget",
            schema_version="1.0.0",
        )
        await event_bus.emit("pre_create", ctx1)

        ctx2 = RequestContext(
            request=mock_req,
            operation="create",
            schema_name="widget",
            schema_version="2.0.0",
        )
        await event_bus.emit("pre_create", ctx2)

        assert len(calls) == 2
        assert calls[0] == "guard:1.0.0"
        assert calls[1] == "guard:2.0.0"
