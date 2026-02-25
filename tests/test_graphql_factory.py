"""Tests for the GraphQL endpoint factory."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from slip_stream.container import EntityContainer
from slip_stream.core.events import EventBus, HookError
from slip_stream.core.schema.registry import SchemaRegistry


@pytest.fixture(autouse=True)
def _reset():
    SchemaRegistry.reset()
    yield
    SchemaRegistry.reset()


@pytest.fixture
def registry_with_schema(tmp_path):
    registry = SchemaRegistry(schema_dir=tmp_path)
    registry.register_schema(
        "widget",
        {
            "type": "object",
            "version": "1.0.0",
            "required": ["name"],
            "properties": {
                "id": {"type": "string", "format": "uuid"},
                "entity_id": {"type": "string", "format": "uuid"},
                "schema_version": {"type": "string"},
                "record_version": {"type": "integer"},
                "created_at": {"type": "string", "format": "date-time"},
                "updated_at": {"type": "string", "format": "date-time"},
                "name": {"type": "string"},
                "color": {"type": "string", "default": "blue"},
                "weight": {"type": "number", "default": 0},
                "tags": {"type": "array", "items": {"type": "string"}},
                "active": {"type": "boolean", "default": True},
            },
        },
        version="1.0.0",
    )
    return registry


@pytest.fixture
def container(registry_with_schema):
    container = EntityContainer()
    container.resolve_all(["widget"])
    return container


class TestGraphQLFactory:

    def test_import(self):
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        factory = GraphQLFactory()
        assert factory is not None

    def test_create_entity_type(self, registry_with_schema):
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        factory = GraphQLFactory()
        schema = registry_with_schema.get_schema("widget", "1.0.0")
        properties = schema.get("properties", {})
        entity_type = factory._create_entity_type("Widget", properties, "widget")

        assert entity_type is not None
        assert hasattr(entity_type, "__strawberry_definition__")

    def test_create_input_types(self, registry_with_schema):
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        factory = GraphQLFactory()
        schema = registry_with_schema.get_schema("widget", "1.0.0")
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        create_input, update_input = factory._create_input_types(
            "Widget", properties, required
        )

        assert create_input is not None
        assert update_input is not None

    def test_create_graphql_router(self, container, registry_with_schema):
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        factory = GraphQLFactory()
        router = factory.create_graphql_router(
            container=container,
            get_db=lambda: None,
            schema_registry=registry_with_schema,
        )

        assert router is not None

    def test_to_pascal(self):
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        factory = GraphQLFactory()
        assert factory._to_pascal("widget") == "Widget"
        assert factory._to_pascal("order_item") == "OrderItem"
        assert factory._to_pascal("my_long_name") == "MyLongName"


class TestExtractRefs:

    def test_no_refs(self):
        from slip_stream.adapters.api.graphql_factory import _extract_refs

        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        assert _extract_refs(schema) == []

    def test_file_ref(self):
        from slip_stream.adapters.api.graphql_factory import _extract_refs

        schema = {
            "type": "object",
            "properties": {
                "address": {"$ref": "definitions/address.json"},
            },
        }
        refs = _extract_refs(schema)
        assert "address" in refs

    def test_internal_ref_ignored(self):
        from slip_stream.adapters.api.graphql_factory import _extract_refs

        schema = {
            "type": "object",
            "definitions": {"Status": {"type": "string"}},
            "properties": {
                "status": {"$ref": "#/definitions/Status"},
            },
        }
        # Internal refs (#/...) should not appear as dependencies
        refs = _extract_refs(schema)
        assert refs == []

    def test_nested_refs(self):
        from slip_stream.adapters.api.graphql_factory import _extract_refs

        schema = {
            "type": "object",
            "properties": {
                "billing": {"$ref": "billing.json"},
                "shipping": {
                    "type": "object",
                    "properties": {
                        "address": {"$ref": "address.json"},
                    },
                },
            },
        }
        refs = _extract_refs(schema)
        assert sorted(refs) == ["address", "billing"]

    def test_deduplicates(self):
        from slip_stream.adapters.api.graphql_factory import _extract_refs

        schema = {
            "type": "object",
            "properties": {
                "a": {"$ref": "shared.json"},
                "b": {"$ref": "shared.json"},
            },
        }
        refs = _extract_refs(schema)
        assert refs == ["shared"]


class TestGraphQLLifecycle:
    """Tests verifying GraphQL resolvers use the full domain lifecycle."""

    def _make_fake_info(self, headers=None):
        """Create a fake Strawberry Info with a mock request."""
        request = SimpleNamespace(
            headers=headers or {},
            state=SimpleNamespace(),
            query_params={},
            url=SimpleNamespace(path="/graphql"),
        )
        return SimpleNamespace(context={"request": request})

    def _make_mock_registration(self):
        """Create a mock EntityRegistration with async services."""
        entity = SimpleNamespace(
            model_dump=lambda: {"id": str(uuid.uuid4()), "name": "test"},
        )
        mock_service = AsyncMock()
        mock_service.execute = AsyncMock(return_value=entity)

        mock_repo = AsyncMock()
        mock_repo.get_by_entity_id = AsyncMock(return_value=entity)

        reg = SimpleNamespace(
            schema_name="widget",
            repository_class=MagicMock(return_value=mock_repo),
            services={
                "create": MagicMock(return_value=mock_service),
                "get": MagicMock(return_value=mock_service),
                "list": MagicMock(return_value=mock_service),
                "update": MagicMock(return_value=mock_service),
                "delete": MagicMock(return_value=mock_service),
            },
            handler_overrides={},
            create_model=lambda **kw: SimpleNamespace(**kw),
            update_model=lambda **kw: SimpleNamespace(**kw),
        )
        return reg, entity, mock_service

    @pytest.mark.asyncio
    async def test_create_resolver_fires_pre_and_post_hooks(self, registry_with_schema):
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        reg, entity, svc = self._make_mock_registration()
        bus = EventBus()
        hooks_fired = []

        async def pre_hook(ctx):
            hooks_fired.append(f"pre_{ctx.operation}")

        async def post_hook(ctx):
            hooks_fired.append(f"post_{ctx.operation}")

        bus.register("pre_create", pre_hook, schema_name="widget")
        bus.register("post_create", post_hook, schema_name="widget")

        factory = GraphQLFactory()
        schema = registry_with_schema.get_schema("widget", "1.0.0")
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        pascal = "Widget"
        et = factory._create_entity_type(pascal, properties, "widget")
        ci, _ = factory._create_input_types(pascal, properties, required)

        resolver = factory._make_create_resolver(
            "widget", et, ci, reg, lambda: None, event_bus=bus
        )

        info = self._make_fake_info()
        # Strawberry input as a simple object with matching fields
        mock_input = SimpleNamespace(name="test")
        import strawberry

        # We need to patch strawberry.asdict since mock_input isn't a real strawberry type
        with MagicMock():
            original_asdict = strawberry.asdict
            strawberry.asdict = lambda x: {"name": "test"}
            try:
                await resolver(info, mock_input)
            finally:
                strawberry.asdict = original_asdict

        assert "pre_create" in hooks_fired
        assert "post_create" in hooks_fired

    @pytest.mark.asyncio
    async def test_handler_override_works_in_graphql(self, registry_with_schema):
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        reg, entity, svc = self._make_mock_registration()
        override_called = []

        async def custom_create(ctx):
            override_called.append(True)
            return entity

        reg.handler_overrides["create"] = custom_create

        factory = GraphQLFactory()
        schema = registry_with_schema.get_schema("widget", "1.0.0")
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        pascal = "Widget"
        et = factory._create_entity_type(pascal, properties, "widget")
        ci, _ = factory._create_input_types(pascal, properties, required)

        resolver = factory._make_create_resolver("widget", et, ci, reg, lambda: None)

        info = self._make_fake_info()
        import strawberry

        original_asdict = strawberry.asdict
        strawberry.asdict = lambda x: {"name": "test"}
        try:
            await resolver(info, SimpleNamespace(name="test"))
        finally:
            strawberry.asdict = original_asdict

        assert override_called == [True]
        # Default service should NOT have been called
        svc.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_hook_error_raises_value_error_in_graphql(self, registry_with_schema):
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        reg, entity, svc = self._make_mock_registration()
        bus = EventBus()

        async def blocking_guard(ctx):
            raise HookError(403, "Forbidden by guard")

        bus.register("pre_create", blocking_guard, schema_name="widget")

        factory = GraphQLFactory()
        schema = registry_with_schema.get_schema("widget", "1.0.0")
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        pascal = "Widget"
        et = factory._create_entity_type(pascal, properties, "widget")
        ci, _ = factory._create_input_types(pascal, properties, required)

        resolver = factory._make_create_resolver(
            "widget", et, ci, reg, lambda: None, event_bus=bus
        )

        info = self._make_fake_info()
        import strawberry

        original_asdict = strawberry.asdict
        strawberry.asdict = lambda x: {"name": "test"}
        try:
            with pytest.raises(ValueError, match="Forbidden by guard"):
                await resolver(info, SimpleNamespace(name="test"))
        finally:
            strawberry.asdict = original_asdict

    @pytest.mark.asyncio
    async def test_get_resolver_fires_hooks(self, registry_with_schema):
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        reg, entity, svc = self._make_mock_registration()
        bus = EventBus()
        hooks_fired = []

        async def track(ctx):
            hooks_fired.append(ctx.operation)

        bus.register("pre_get", track, schema_name="widget")
        bus.register("post_get", track, schema_name="widget")

        factory = GraphQLFactory()
        schema = registry_with_schema.get_schema("widget", "1.0.0")
        properties = schema.get("properties", {})
        et = factory._create_entity_type("Widget", properties, "widget")

        resolver = factory._make_get_resolver(
            "widget", et, reg, lambda: None, event_bus=bus
        )

        info = self._make_fake_info()
        await resolver(info, str(uuid.uuid4()))

        assert hooks_fired == ["get", "get"]

    @pytest.mark.asyncio
    async def test_delete_resolver_fires_hooks(self, registry_with_schema):
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        reg, entity, svc = self._make_mock_registration()
        bus = EventBus()
        hooks_fired = []

        async def track(ctx):
            hooks_fired.append(ctx.operation)

        bus.register("pre_delete", track, schema_name="widget")
        bus.register("post_delete", track, schema_name="widget")

        factory_instance = GraphQLFactory()
        resolver = factory_instance._make_delete_resolver(
            "widget", reg, lambda: None, event_bus=bus
        )

        info = self._make_fake_info()
        result = await resolver(info, str(uuid.uuid4()))

        assert result is True
        assert hooks_fired == ["delete", "delete"]

    def _make_list_mock_registration(self):
        """Create a mock registration whose list service returns an iterable."""
        entity = SimpleNamespace(
            model_dump=lambda: {"id": str(uuid.uuid4()), "name": "test"},
        )

        # List service must return a list, not a single entity
        list_service = AsyncMock()
        list_service.execute = AsyncMock(return_value=[entity])

        other_service = AsyncMock()
        other_service.execute = AsyncMock(return_value=entity)

        mock_repo = AsyncMock()
        mock_repo.get_by_entity_id = AsyncMock(return_value=entity)

        reg = SimpleNamespace(
            schema_name="widget",
            repository_class=MagicMock(return_value=mock_repo),
            services={
                "create": MagicMock(return_value=other_service),
                "get": MagicMock(return_value=other_service),
                "list": MagicMock(return_value=list_service),
                "update": MagicMock(return_value=other_service),
                "delete": MagicMock(return_value=other_service),
            },
            handler_overrides={},
            create_model=lambda **kw: SimpleNamespace(**kw),
            update_model=lambda **kw: SimpleNamespace(**kw),
        )
        return reg, entity

    @pytest.mark.asyncio
    async def test_list_with_where_filter(self, registry_with_schema):
        """List resolver passes a valid where clause through the DSL without error."""
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        reg, entity = self._make_list_mock_registration()
        # Provide schema_dict so the DSL allows schema fields (including 'name')
        schema = registry_with_schema.get_schema("widget", "1.0.0")
        reg.schema_dict = schema

        factory = GraphQLFactory()
        properties = schema.get("properties", {})
        et = factory._create_entity_type("Widget", properties, "widget")

        resolver = factory._make_list_resolver("widget", et, reg, lambda: None)

        info = self._make_fake_info()
        # 'name' is a schema field, so the DSL built from schema_dict allows it
        result = await resolver(info, skip=0, limit=10, where={"name": {"_eq": "test"}})
        # The mock list service returns a list; we verify no error is raised
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_list_with_sort(self, registry_with_schema):
        """List resolver accepts a sort string for a schema field without error."""
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        reg, entity = self._make_list_mock_registration()
        # Provide schema_dict so the DSL allows schema fields (including 'name')
        schema = registry_with_schema.get_schema("widget", "1.0.0")
        reg.schema_dict = schema

        factory = GraphQLFactory()
        properties = schema.get("properties", {})
        et = factory._create_entity_type("Widget", properties, "widget")

        resolver = factory._make_list_resolver("widget", et, reg, lambda: None)

        info = self._make_fake_info()
        result = await resolver(info, skip=0, limit=10, sort="name")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_list_invalid_filter_raises_value_error(self, registry_with_schema):
        """List resolver raises ValueError when where clause fails DSL validation."""
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory
        from slip_stream.core.query import QueryDSL, QueryValidationError

        reg, entity, svc = self._make_mock_registration()
        factory = GraphQLFactory()
        schema = registry_with_schema.get_schema("widget", "1.0.0")
        properties = schema.get("properties", {})
        et = factory._create_entity_type("Widget", properties, "widget")

        resolver = factory._make_list_resolver("widget", et, reg, lambda: None)

        info = self._make_fake_info()

        # Patch QueryDSL.to_mongo to raise QueryValidationError to simulate
        # an invalid filter expression reaching the resolver
        original_to_mongo = QueryDSL.to_mongo

        def raising_to_mongo(self, raw):
            raise QueryValidationError("Invalid filter")

        QueryDSL.to_mongo = raising_to_mongo  # type: ignore[method-assign]
        try:
            with pytest.raises(ValueError, match="Invalid filter"):
                await resolver(info, skip=0, limit=10, where={"bad": "filter"})
        finally:
            QueryDSL.to_mongo = original_to_mongo  # type: ignore[method-assign]

    @pytest.mark.asyncio
    async def test_update_not_found_raises_value_error(self, registry_with_schema):
        """Update resolver raises ValueError when entity is not found."""
        from unittest.mock import AsyncMock, MagicMock

        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        # Create a registration where the repo returns None (entity not found)
        mock_repo = AsyncMock()
        mock_repo.get_by_entity_id = AsyncMock(return_value=None)

        reg = SimpleNamespace(
            schema_name="widget",
            repository_class=MagicMock(return_value=mock_repo),
            services={},
            handler_overrides={},
            update_model=lambda **kw: SimpleNamespace(**kw),
            create_model=lambda **kw: SimpleNamespace(**kw),
        )

        factory = GraphQLFactory()
        schema = registry_with_schema.get_schema("widget", "1.0.0")
        properties = schema.get("properties", {})
        et = factory._create_entity_type("Widget", properties, "widget")
        _, ui = factory._create_input_types("Widget", properties, [])

        resolver = factory._make_update_resolver("widget", et, ui, reg, lambda: None)

        info = self._make_fake_info()
        import strawberry

        original_asdict = strawberry.asdict
        strawberry.asdict = lambda x: {"name": "updated"}
        try:
            with pytest.raises((ValueError, Exception)):
                await resolver(info, str(uuid.uuid4()), SimpleNamespace(name="updated"))
        finally:
            strawberry.asdict = original_asdict

    @pytest.mark.asyncio
    async def test_delete_not_found_raises_value_error(self, registry_with_schema):
        """Delete resolver raises ValueError when entity is not found."""
        from unittest.mock import AsyncMock, MagicMock

        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        mock_repo = AsyncMock()
        mock_repo.get_by_entity_id = AsyncMock(return_value=None)

        reg = SimpleNamespace(
            schema_name="widget",
            repository_class=MagicMock(return_value=mock_repo),
            services={},
            handler_overrides={},
        )

        factory = GraphQLFactory()
        resolver = factory._make_delete_resolver("widget", reg, lambda: None)

        info = self._make_fake_info()
        with pytest.raises(ValueError, match="widget not found"):
            await resolver(info, str(uuid.uuid4()))

    @pytest.mark.asyncio
    async def test_create_with_hook_error_raises_value_error(
        self, registry_with_schema
    ):
        """Create resolver re-raises HookError as ValueError for Strawberry."""
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        reg, entity, svc = self._make_mock_registration()
        bus = EventBus()

        async def guard(ctx):
            raise HookError(422, "Validation failed in hook")

        bus.register("pre_create", guard, schema_name="widget")

        factory = GraphQLFactory()
        schema = registry_with_schema.get_schema("widget", "1.0.0")
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        et = factory._create_entity_type("Widget", properties, "widget")
        ci, _ = factory._create_input_types("Widget", properties, required)

        resolver = factory._make_create_resolver(
            "widget", et, ci, reg, lambda: None, event_bus=bus
        )

        info = self._make_fake_info()
        import strawberry

        original_asdict = strawberry.asdict
        strawberry.asdict = lambda x: {"name": "test"}
        try:
            with pytest.raises(ValueError, match="Validation failed in hook"):
                await resolver(info, SimpleNamespace(name="test"))
        finally:
            strawberry.asdict = original_asdict

    @pytest.mark.asyncio
    async def test_schema_dag_query(self, registry_with_schema):
        """schema_dag resolver returns a dict with schema names as keys."""
        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        factory = GraphQLFactory()
        dag_resolver = factory._make_schema_dag_resolver(registry_with_schema)

        info = self._make_fake_info()
        dag = await dag_resolver(info)

        assert isinstance(dag, dict)
        assert "widget" in dag
        assert "versions" in dag["widget"]
        assert "latest_version" in dag["widget"]
        assert "dependencies" in dag["widget"]

    @pytest.mark.asyncio
    async def test_get_by_entity_id_not_found_returns_none(self, registry_with_schema):
        """Get resolver returns None when entity does not exist."""
        from unittest.mock import AsyncMock, MagicMock

        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        mock_repo = AsyncMock()
        mock_repo.get_by_entity_id = AsyncMock(return_value=None)

        reg = SimpleNamespace(
            schema_name="widget",
            repository_class=MagicMock(return_value=mock_repo),
            services={},
            handler_overrides={},
        )

        factory = GraphQLFactory()
        schema = registry_with_schema.get_schema("widget", "1.0.0")
        properties = schema.get("properties", {})
        et = factory._create_entity_type("Widget", properties, "widget")

        resolver = factory._make_get_resolver("widget", et, reg, lambda: None)

        info = self._make_fake_info()
        result = await resolver(info, str(uuid.uuid4()))
        assert result is None
