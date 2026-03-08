"""GraphQL endpoint factory — auto-generates Strawberry types, queries, and mutations.

Follows the same generator pattern as the REST ``EndpointFactory``:
drop a JSON schema, get a full GraphQL API with queries and mutations.

Supports:
- Per-entity query + list + create + update + delete
- Handler overrides via ``EntityRegistration.handler_overrides``
- Version-aware operations via ``X-Schema-Version`` header
- Schema DAG introspection query

Requires ``strawberry-graphql[fastapi]`` (optional dependency)::

    pip install slip-stream[graphql]
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, List, Optional

from slip_stream.core.events import HookError

if TYPE_CHECKING:
    from slip_stream.core.context import RequestContext

logger = logging.getLogger(__name__)

try:
    import strawberry
    from strawberry.extensions import SchemaExtension
    from strawberry.fastapi import GraphQLRouter
    from strawberry.scalars import JSON
    from strawberry.types import Info

    HAS_STRAWBERRY = True
except ImportError:
    HAS_STRAWBERRY = False


def _measure_query_depth(document: Any) -> int:
    """Walk a GraphQL AST and return the maximum nesting depth of selection sets."""
    from graphql.language import ast as gql_ast

    def _walk(node: Any, current: int) -> int:
        if not hasattr(node, "selection_set") or node.selection_set is None:
            return current
        deepest = current + 1
        for sel in node.selection_set.selections:
            deepest = max(deepest, _walk(sel, current + 1))
        return deepest

    max_depth = 0
    for defn in document.definitions:
        if isinstance(defn, gql_ast.OperationDefinitionNode):
            max_depth = max(max_depth, _walk(defn, 0))
    return max_depth


def _make_depth_limiter(max_depth: int) -> type:
    """Create a Strawberry SchemaExtension class that limits query depth."""

    class QueryDepthLimiter(SchemaExtension):  # type: ignore[misc]
        """Rejects queries exceeding a maximum nesting depth."""

        def on_execute(self) -> Any:  # type: ignore[override]
            document = self.execution_context.graphql_document
            if document is not None:
                depth = _measure_query_depth(document)
                if depth > max_depth:
                    from graphql import GraphQLError

                    self.execution_context.result = strawberry.types.ExecutionResult(
                        data=None,
                        errors=[
                            GraphQLError(
                                f"Query depth {depth} exceeds maximum allowed depth of {max_depth}"
                            )
                        ],
                    )
            yield

    return QueryDepthLimiter


def _make_introspection_blocker() -> type:
    """Create a Strawberry SchemaExtension class that blocks introspection."""

    class DisableIntrospection(SchemaExtension):  # type: ignore[misc]
        """Blocks __schema and __type introspection queries."""

        def on_execute(self) -> Any:  # type: ignore[override]
            from graphql import GraphQLError
            from graphql.language import ast as gql_ast

            document = self.execution_context.graphql_document
            if document is not None:
                for defn in document.definitions:
                    if (
                        isinstance(defn, gql_ast.OperationDefinitionNode)
                        and defn.selection_set
                    ):
                        for sel in defn.selection_set.selections:
                            if isinstance(
                                sel, gql_ast.FieldNode
                            ) and sel.name.value in (
                                "__schema",
                                "__type",
                            ):
                                self.execution_context.result = (
                                    strawberry.types.ExecutionResult(
                                        data=None,
                                        errors=[
                                            GraphQLError("Introspection is disabled")
                                        ],
                                    )
                                )
            yield

    return DisableIntrospection


def _ensure_strawberry() -> None:
    if not HAS_STRAWBERRY:
        raise ImportError(
            "strawberry-graphql is required for GraphQL support. "
            "Install it with: pip install slip-stream[graphql]"
        )


def _json_type_to_strawberry(field_def: dict[str, Any]) -> Any:
    """Convert a JSON Schema field definition to a Strawberry type annotation."""
    json_type = field_def.get("type")
    format_type = field_def.get("format")

    type_map = {
        "integer": int,
        "number": float,
        "boolean": bool,
    }
    if json_type in type_map:
        return type_map[json_type]
    if json_type == "string":
        if format_type == "date-time":
            return datetime
        if format_type == "uuid":
            return str
        return str
    if json_type == "array":
        items = field_def.get("items", {})
        item_type = _json_type_to_strawberry(items)
        return List[item_type]  # type: ignore[valid-type]
    if json_type == "object":
        return JSON
    return str


# Fields managed by the framework envelope
_AUDIT_FIELDS = frozenset(
    {
        "id",
        "entity_id",
        "schema_version",
        "record_version",
        "created_at",
        "updated_at",
        "deleted_at",
        "created_by",
        "updated_by",
        "deleted_by",
    }
)


class GraphQLFactory:
    """Factory for generating Strawberry GraphQL schemas from JSON schemas.

    Usage::

        from slip_stream.adapters.api.graphql_factory import GraphQLFactory

        factory = GraphQLFactory()
        router = factory.create_graphql_router(
            container=container,
            get_db=get_db,
            schema_registry=registry,
        )
        app.include_router(router, prefix="/graphql")
    """

    def create_graphql_router(
        self,
        container: Any,
        get_db: Any,
        schema_registry: Any,
        get_current_user: Any | None = None,
        event_bus: Any | None = None,
        custom_mutations: list[Any] | None = None,
        max_query_depth: int = 10,
        allow_introspection: bool = True,
        versioned: bool = False,
    ) -> "GraphQLRouter":
        """Create a Strawberry GraphQLRouter with auto-generated types.

        Args:
            container: The EntityContainer with resolved registrations.
            get_db: FastAPI dependency returning AsyncIOMotorDatabase.
            schema_registry: The SchemaRegistry instance.
            get_current_user: FastAPI dependency returning user dict.
            event_bus: Optional EventBus for lifecycle hooks.
            custom_mutations: Additional strawberry mutation fields to include.
            max_query_depth: Maximum allowed query nesting depth (default: 10).
            allow_introspection: Whether to allow introspection queries (default: True).
            versioned: When ``True``, expose per-version types (e.g. ``PetV1_0_0``)
                alongside the unversioned latest type (``Pet``). Versioned resolvers
                set ``ctx.schema_version`` so version-aware handler overrides fire.

        Returns:
            A Strawberry GraphQLRouter ready to mount on FastAPI.
        """
        _ensure_strawberry()

        registrations = container.get_all()

        # entity_types maps schema_name -> latest (unversioned) Strawberry type
        entity_types: dict[str, type] = {}

        # versioned_entity_types maps (schema_name, version) -> Strawberry type
        # Only populated when versioned=True
        versioned_entity_types: dict[tuple[str, str], type] = {}

        for schema_name, reg in registrations.items():
            pascal = self._to_pascal(schema_name)
            schema = schema_registry.get_schema(schema_name, "latest")
            properties = schema.get("properties", {})

            entity_type = self._create_entity_type(pascal, properties, schema_name)
            entity_types[schema_name] = entity_type

            if versioned:
                try:
                    all_versions = schema_registry.get_all_versions(schema_name)
                except (ValueError, AttributeError):
                    all_versions = []

                try:
                    latest_ver = schema_registry.get_latest_version(schema_name)
                except (ValueError, AttributeError):
                    latest_ver = None

                for version in all_versions:
                    # The latest version uses the plain unversioned type already
                    # created above; no need to duplicate it.
                    if version == latest_ver:
                        versioned_entity_types[(schema_name, version)] = entity_type
                        continue

                    sanitized = version.replace(".", "_")
                    versioned_pascal = f"{pascal}V{sanitized}"
                    try:
                        ver_schema = schema_registry.get_schema(schema_name, version)
                    except (ValueError, AttributeError):
                        continue
                    ver_properties = ver_schema.get("properties", {})
                    ver_type = self._create_entity_type(
                        versioned_pascal, ver_properties, schema_name
                    )
                    versioned_entity_types[(schema_name, version)] = ver_type

        # Build schema using @strawberry.type decorated classes
        # We need to construct Query and Mutation with proper methods
        Query = self._build_schema_class(
            "Query",
            registrations,
            entity_types,
            schema_registry,
            get_db,
            event_bus,
            mode="query",
            versioned_entity_types=versioned_entity_types,
        )
        Mutation = self._build_schema_class(
            "Mutation",
            registrations,
            entity_types,
            schema_registry,
            get_db,
            event_bus,
            mode="mutation",
            versioned_entity_types=versioned_entity_types,
        )

        extensions: list[Any] = [_make_depth_limiter(max_query_depth)]
        if not allow_introspection:
            extensions.append(_make_introspection_blocker())

        graphql_schema = strawberry.Schema(
            query=Query, mutation=Mutation, extensions=extensions
        )
        return GraphQLRouter(graphql_schema)

    def _to_pascal(self, name: str) -> str:
        return "".join(w.capitalize() for w in name.split("_"))

    def _create_entity_type(
        self, pascal: str, properties: dict, schema_name: str
    ) -> type:
        """Dynamically create a Strawberry type for an entity."""
        annotations: dict[str, Any] = {}
        defaults: dict[str, Any] = {}

        # Always include audit fields
        annotations["id"] = Optional[str]
        defaults["id"] = None
        annotations["entity_id"] = Optional[str]
        defaults["entity_id"] = None
        annotations["schema_version"] = Optional[str]
        defaults["schema_version"] = None
        annotations["created_at"] = Optional[str]
        defaults["created_at"] = None
        annotations["updated_at"] = Optional[str]
        defaults["updated_at"] = None

        for field_name, field_def in properties.items():
            if field_name in _AUDIT_FIELDS:
                continue
            st_type = _json_type_to_strawberry(field_def)
            annotations[field_name] = Optional[st_type]
            defaults[field_name] = field_def.get("default")

        ns = {"__annotations__": annotations, **defaults}
        entity_cls = type(pascal, (), ns)
        return strawberry.type(
            entity_cls, description=f"Auto-generated type for {schema_name}"
        )

    def _create_input_types(
        self, pascal: str, properties: dict, required: list[str]
    ) -> tuple[type, type]:
        """Create Strawberry input types for create and update."""
        create_annotations: dict[str, Any] = {}
        create_defaults: dict[str, Any] = {}
        update_annotations: dict[str, Any] = {}
        update_defaults: dict[str, Any] = {}

        for field_name, field_def in properties.items():
            if field_name in _AUDIT_FIELDS:
                continue
            st_type = _json_type_to_strawberry(field_def)

            if field_name in required:
                create_annotations[field_name] = st_type
            else:
                create_annotations[field_name] = Optional[st_type]
                create_defaults[field_name] = field_def.get("default")

            update_annotations[field_name] = Optional[st_type]
            update_defaults[field_name] = None

        create_ns = {"__annotations__": create_annotations, **create_defaults}
        update_ns = {"__annotations__": update_annotations, **update_defaults}

        create_cls = type(f"{pascal}CreateInput", (), create_ns)
        update_cls = type(f"{pascal}UpdateInput", (), update_ns)

        return (
            strawberry.input(create_cls),
            strawberry.input(update_cls),
        )

    def _build_schema_class(
        self,
        class_name: str,
        registrations: dict,
        entity_types: dict,
        schema_registry: Any,
        get_db: Any,
        event_bus: Any,
        mode: str,
        versioned_entity_types: dict[tuple[str, str], type] | None = None,
    ) -> type:
        """Build a Strawberry-decorated Query or Mutation class.

        Strawberry requires types used in resolver annotations to be resolvable
        from the module's global scope. We register dynamically-created types
        in the graphql_factory module globals so Strawberry can find them.

        When ``versioned_entity_types`` is non-empty, additional versioned
        resolver methods are emitted for every ``(schema_name, version)`` pair
        that differs from the latest version.  The latest version's resolvers
        are the canonical unversioned ones (e.g. ``get_pet``) *and* a versioned
        alias (e.g. ``get_pet_v1_0_0``).
        """
        # Register all entity types in this module's globals so Strawberry can resolve them
        import sys

        this_module = sys.modules[__name__]
        for schema_name, et in entity_types.items():
            setattr(this_module, et.__name__, et)

        # Also register versioned types in module globals
        if versioned_entity_types:
            for (schema_name, _version), vet in versioned_entity_types.items():
                setattr(this_module, vet.__name__, vet)

        methods: dict[str, Any] = {}

        if mode == "query":
            for schema_name, reg in registrations.items():
                et = entity_types[schema_name]

                get_fn = self._make_get_resolver(
                    schema_name, et, reg, get_db, event_bus
                )
                methods[f"get_{schema_name}"] = strawberry.field(resolver=get_fn)

                list_fn = self._make_list_resolver(
                    schema_name, et, reg, get_db, event_bus
                )
                methods[f"list_{schema_name}s"] = strawberry.field(resolver=list_fn)

            # Versioned query methods
            if versioned_entity_types:
                for (schema_name, version), vet in versioned_entity_types.items():
                    reg = registrations.get(schema_name)
                    if reg is None:
                        continue
                    sanitized = version.replace(".", "_")
                    ver_suffix = f"_v{sanitized}"

                    get_fn_v = self._make_get_resolver(
                        schema_name,
                        vet,
                        reg,
                        get_db,
                        event_bus,
                        schema_version=version,
                    )
                    get_fn_v.__name__ = f"get_{schema_name}{ver_suffix}"
                    get_fn_v.__annotations__ = {
                        "info": Info,
                        "entity_id": str,
                        "return": Optional[vet],  # type: ignore[valid-type]
                    }
                    methods[f"get_{schema_name}{ver_suffix}"] = strawberry.field(
                        resolver=get_fn_v
                    )

                    list_fn_v = self._make_list_resolver(
                        schema_name,
                        vet,
                        reg,
                        get_db,
                        event_bus,
                        schema_version=version,
                    )
                    list_fn_v.__name__ = f"list_{schema_name}s{ver_suffix}"
                    list_fn_v.__annotations__ = {
                        "info": Info,
                        "skip": int,
                        "limit": int,
                        "where": Optional[strawberry.scalars.JSON],
                        "sort": Optional[str],
                        "return": List[vet],  # type: ignore[valid-type]
                    }
                    methods[f"list_{schema_name}s{ver_suffix}"] = strawberry.field(
                        resolver=list_fn_v
                    )

            dag_fn = self._make_schema_dag_resolver(schema_registry)
            methods["schema_dag"] = strawberry.field(resolver=dag_fn)

        elif mode == "mutation":
            for schema_name, reg in registrations.items():
                et = entity_types[schema_name]
                schema = schema_registry.get_schema(schema_name, "latest")
                properties = schema.get("properties", {})
                required_fields = schema.get("required", [])
                pascal = self._to_pascal(schema_name)
                create_input, update_input = self._create_input_types(
                    pascal, properties, required_fields
                )

                # Register input types in module globals
                setattr(this_module, create_input.__name__, create_input)
                setattr(this_module, update_input.__name__, update_input)

                create_fn = self._make_create_resolver(
                    schema_name, et, create_input, reg, get_db, event_bus
                )
                methods[f"create_{schema_name}"] = strawberry.mutation(
                    resolver=create_fn
                )

                update_fn = self._make_update_resolver(
                    schema_name, et, update_input, reg, get_db, event_bus
                )
                methods[f"update_{schema_name}"] = strawberry.mutation(
                    resolver=update_fn
                )

                delete_fn = self._make_delete_resolver(
                    schema_name, reg, get_db, event_bus
                )
                methods[f"delete_{schema_name}"] = strawberry.mutation(
                    resolver=delete_fn
                )

            # Versioned mutation methods
            if versioned_entity_types:
                for (schema_name, version), vet in versioned_entity_types.items():
                    reg = registrations.get(schema_name)
                    if reg is None:
                        continue
                    sanitized = version.replace(".", "_")
                    ver_suffix = f"_v{sanitized}"
                    pascal = self._to_pascal(schema_name)
                    versioned_pascal = vet.__name__  # e.g. PetV1_0_0 or Pet (latest)

                    try:
                        ver_schema = schema_registry.get_schema(schema_name, version)
                    except (ValueError, AttributeError):
                        continue
                    ver_properties = ver_schema.get("properties", {})
                    ver_required = ver_schema.get("required", [])

                    # Use versioned pascal for input type names to avoid collisions
                    create_input_v, update_input_v = self._create_input_types(
                        versioned_pascal, ver_properties, ver_required
                    )
                    setattr(this_module, create_input_v.__name__, create_input_v)
                    setattr(this_module, update_input_v.__name__, update_input_v)

                    create_fn_v = self._make_create_resolver(
                        schema_name,
                        vet,
                        create_input_v,
                        reg,
                        get_db,
                        event_bus,
                        schema_version=version,
                    )
                    create_fn_v.__name__ = f"create_{schema_name}{ver_suffix}"
                    create_fn_v.__annotations__ = {
                        "info": Info,
                        "input": create_input_v,
                        "return": vet,
                    }
                    methods[f"create_{schema_name}{ver_suffix}"] = strawberry.mutation(
                        resolver=create_fn_v
                    )

                    update_fn_v = self._make_update_resolver(
                        schema_name,
                        vet,
                        update_input_v,
                        reg,
                        get_db,
                        event_bus,
                        schema_version=version,
                    )
                    update_fn_v.__name__ = f"update_{schema_name}{ver_suffix}"
                    update_fn_v.__annotations__ = {
                        "info": Info,
                        "entity_id": str,
                        "input": update_input_v,
                        "return": Optional[vet],
                    }
                    methods[f"update_{schema_name}{ver_suffix}"] = strawberry.mutation(
                        resolver=update_fn_v
                    )

                    delete_fn_v = self._make_delete_resolver(
                        schema_name,
                        reg,
                        get_db,
                        event_bus,
                        schema_version=version,
                    )
                    delete_fn_v.__name__ = f"delete_{schema_name}{ver_suffix}"
                    delete_fn_v.__annotations__ = {
                        "info": Info,
                        "entity_id": str,
                        "return": bool,
                    }
                    methods[f"delete_{schema_name}{ver_suffix}"] = strawberry.mutation(
                        resolver=delete_fn_v
                    )

        ns = {**methods}
        cls = type(class_name, (), ns)
        return strawberry.type(cls)

    def _make_get_resolver(
        self,
        schema_name: str,
        entity_type: type,
        registration: Any,
        get_db: Any,
        event_bus: Any = None,
        schema_version: str | None = None,
    ) -> Any:
        et = entity_type
        reg = registration
        _schema_version = schema_version

        async def resolver(info: Info, entity_id: str) -> Optional[et]:  # type: ignore
            db = await _resolve_db(info, get_db)
            request = info.context.get("request")

            repo = reg.repository_class(db)
            entity = await repo.get_by_entity_id(entity_id=uuid.UUID(entity_id))
            if entity is None:
                return None

            ctx = _build_graphql_context(
                request=request,
                operation="get",
                schema_name=schema_name,
                entity_id=uuid.UUID(entity_id),
                entity=entity,
                db=db,
                info=info,
                schema_version=_schema_version,
            )

            from slip_stream.core.operation import OperationExecutor

            executor = OperationExecutor(reg, event_bus)
            try:
                result = await executor.execute_get(ctx)
            except HookError as e:
                raise ValueError(e.detail) from e

            return _model_to_strawberry(result, et)

        resolver.__name__ = f"get_{schema_name}"
        resolver.__annotations__ = {
            "info": Info,
            "entity_id": str,
            "return": Optional[et],
        }
        return resolver

    def _make_list_resolver(
        self,
        schema_name: str,
        entity_type: type,
        registration: Any,
        get_db: Any,
        event_bus: Any = None,
        schema_version: str | None = None,
    ) -> Any:
        et = entity_type
        reg = registration
        _schema_version = schema_version

        async def resolver(
            info: Info,
            skip: int = 0,
            limit: int = 100,
            where: Optional[strawberry.scalars.JSON] = None,
            sort: Optional[str] = None,
        ) -> List[et]:  # type: ignore
            limit = max(1, min(limit, 1000))
            db = await _resolve_db(info, get_db)
            request = info.context.get("request")

            # Parse where clause through safe DSL
            from slip_stream.core.query import (
                QueryDSL,
                QueryValidationError,
                parse_sort_param,
            )

            schema_dict = getattr(reg, "schema_dict", None) or {}
            dsl = QueryDSL.from_schema(schema_dict) if schema_dict else QueryDSL()

            filter_criteria = None
            sort_by = None
            sort_order = -1

            if where:
                try:
                    filter_criteria = dsl.to_mongo(where)  # type: ignore[arg-type]
                except QueryValidationError as e:
                    raise ValueError(str(e)) from e

            if sort:
                try:
                    sort_spec = parse_sort_param(sort, dsl._allowed)
                    mongo_sort = dsl.to_mongo_sort(sort_spec)
                    if mongo_sort:
                        sort_by = mongo_sort[0][0]
                        sort_order = mongo_sort[0][1]
                except QueryValidationError as e:
                    raise ValueError(str(e)) from e

            ctx = _build_graphql_context(
                request=request,
                operation="list",
                schema_name=schema_name,
                db=db,
                info=info,
                skip=skip,
                limit=limit,
                filter_criteria=filter_criteria,
                sort_by=sort_by,
                sort_order=sort_order,
                schema_version=_schema_version,
            )

            from slip_stream.core.operation import OperationExecutor

            executor = OperationExecutor(reg, event_bus)
            try:
                result = await executor.execute_list(ctx)
            except HookError as e:
                raise ValueError(e.detail) from e

            return [_model_to_strawberry(e, et) for e in result]

        resolver.__name__ = f"list_{schema_name}s"
        resolver.__annotations__ = {
            "info": Info,
            "skip": int,
            "limit": int,
            "where": Optional[strawberry.scalars.JSON],
            "sort": Optional[str],
            "return": List[et],  # type: ignore[valid-type]
        }
        return resolver

    def _make_create_resolver(
        self,
        schema_name: str,
        entity_type: type,
        create_input: type,
        registration: Any,
        get_db: Any,
        event_bus: Any = None,
        schema_version: str | None = None,
    ) -> Any:
        et = entity_type
        ci = create_input
        reg = registration
        _schema_version = schema_version

        async def resolver(info: Info, input: ci) -> et:  # type: ignore
            db = await _resolve_db(info, get_db)
            request = info.context.get("request")

            data = reg.create_model(**strawberry.asdict(input))
            ctx = _build_graphql_context(
                request=request,
                operation="create",
                schema_name=schema_name,
                data=data,
                db=db,
                info=info,
                schema_version=_schema_version,
            )

            from slip_stream.core.operation import OperationExecutor

            executor = OperationExecutor(reg, event_bus)
            try:
                result = await executor.execute_create(ctx)
            except HookError as e:
                raise ValueError(e.detail) from e

            return _model_to_strawberry(result, et)

        resolver.__name__ = f"create_{schema_name}"
        resolver.__annotations__ = {"info": Info, "input": ci, "return": et}
        return resolver

    def _make_update_resolver(
        self,
        schema_name: str,
        entity_type: type,
        update_input: type,
        registration: Any,
        get_db: Any,
        event_bus: Any = None,
        schema_version: str | None = None,
    ) -> Any:
        et = entity_type
        ui = update_input
        reg = registration
        _schema_version = schema_version

        async def resolver(info: Info, entity_id: str, input: ui) -> Optional[et]:  # type: ignore
            db = await _resolve_db(info, get_db)
            request = info.context.get("request")

            input_dict = {
                k: v for k, v in strawberry.asdict(input).items() if v is not None
            }
            data = reg.update_model(**input_dict)
            ctx = _build_graphql_context(
                request=request,
                operation="update",
                schema_name=schema_name,
                entity_id=uuid.UUID(entity_id),
                data=data,
                db=db,
                info=info,
                schema_version=_schema_version,
            )

            from slip_stream.core.operation import OperationExecutor

            executor = OperationExecutor(reg, event_bus)
            try:
                result = await executor.execute_update(ctx)
            except HookError as e:
                raise ValueError(e.detail) from e

            return _model_to_strawberry(result, et)

        resolver.__name__ = f"update_{schema_name}"
        resolver.__annotations__ = {
            "info": Info,
            "entity_id": str,
            "input": ui,
            "return": Optional[et],
        }
        return resolver

    def _make_delete_resolver(
        self,
        schema_name: str,
        registration: Any,
        get_db: Any,
        event_bus: Any = None,
        schema_version: str | None = None,
    ) -> Any:
        reg = registration
        _schema_version = schema_version

        async def resolver(info: Info, entity_id: str) -> bool:
            db = await _resolve_db(info, get_db)
            request = info.context.get("request")

            # Hydrate entity for pre-hooks
            repo = reg.repository_class(db)
            entity = await repo.get_by_entity_id(entity_id=uuid.UUID(entity_id))
            if entity is None:
                raise ValueError(f"{schema_name} not found")

            ctx = _build_graphql_context(
                request=request,
                operation="delete",
                schema_name=schema_name,
                entity_id=uuid.UUID(entity_id),
                entity=entity,
                db=db,
                info=info,
                schema_version=_schema_version,
            )

            from slip_stream.core.operation import OperationExecutor

            executor = OperationExecutor(reg, event_bus)
            try:
                await executor.execute_delete(ctx)
            except HookError as e:
                raise ValueError(e.detail) from e

            return True

        resolver.__name__ = f"delete_{schema_name}"
        resolver.__annotations__ = {"info": Info, "entity_id": str, "return": bool}
        return resolver

    def _make_schema_dag_resolver(self, schema_registry):
        reg = schema_registry

        async def schema_dag(info: Info) -> JSON:
            names = reg.get_schema_names()
            dag: dict[str, Any] = {}
            for name in names:
                versions = reg.get_all_versions(name)
                latest = reg.get_latest_version(name)
                schema = reg.get_schema(name, latest)
                deps = _extract_refs(schema)
                dag[name] = {
                    "versions": versions,
                    "latest_version": latest,
                    "dependencies": deps,
                }
            return dag  # type: ignore[return-value]

        return schema_dag


async def _resolve_db(info: "Info", get_db: Any) -> Any:
    """Resolve the database from the request context."""
    if hasattr(get_db, "__call__"):
        from inspect import iscoroutinefunction

        if iscoroutinefunction(get_db):
            return await get_db()
        return get_db()
    return None


def _get_user_id(info: "Info") -> str:
    """Extract user ID from the GraphQL context."""
    request = info.context.get("request") if info.context else None
    if request:
        return request.headers.get("x-user-id", "anonymous")
    return "anonymous"


def _build_graphql_context(
    request: Any,
    operation: str,
    schema_name: str,
    info: "Info",
    db: Any = None,
    entity_id: Any = None,
    entity: Any = None,
    data: Any = None,
    skip: int = 0,
    limit: int = 100,
    filter_criteria: Any = None,
    sort_by: Any = None,
    sort_order: int = -1,
    schema_version: str | None = None,
) -> "RequestContext":
    """Build a RequestContext for a GraphQL resolver.

    When a real Starlette ``Request`` is available (Strawberry provides it
    via ``info.context["request"]``), uses ``RequestContext.from_request()``
    to get full filter-context and header negotiation.  Falls back to direct
    construction when running in tests without a real request.

    Args:
        schema_version: When provided (e.g. from a versioned resolver), the
            resulting context's ``schema_version`` is set to this value.
            This takes precedence over the ``X-Schema-Version`` header so
            that versioned GraphQL methods always target the correct handler.
    """
    from slip_stream.core.context import RequestContext

    user_id = _get_user_id(info)
    current_user = {"id": user_id}

    kwargs: dict[str, Any] = {
        "current_user": current_user,
        "db": db,
    }
    if entity_id is not None:
        kwargs["entity_id"] = entity_id
    if entity is not None:
        kwargs["entity"] = entity
    if data is not None:
        kwargs["data"] = data
    if operation == "list":
        kwargs["skip"] = skip
        kwargs["limit"] = limit
        if filter_criteria is not None:
            kwargs["filter_criteria"] = filter_criteria
        if sort_by is not None:
            kwargs["sort_by"] = sort_by
        kwargs["sort_order"] = sort_order

    # Use from_request when we have a real Starlette Request
    if request is not None and hasattr(request, "headers"):
        ctx = RequestContext.from_request(
            request=request,
            operation=operation,  # type: ignore[arg-type]
            schema_name=schema_name,
            **kwargs,
        )
        ctx.channel = "graphql"
        # Versioned resolver overrides any header-negotiated version
        if schema_version is not None:
            ctx.schema_version = schema_version
        return ctx

    # Fallback: construct directly (e.g. in tests without real HTTP)
    from types import SimpleNamespace

    fake_request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(),
        query_params={},
        url=SimpleNamespace(path="/graphql"),
    )
    return RequestContext(
        request=fake_request,  # type: ignore[arg-type]
        operation=operation,  # type: ignore[arg-type]
        schema_name=schema_name,
        channel="graphql",
        schema_version=schema_version,
        **kwargs,
    )


def _model_to_strawberry(model: Any, strawberry_type: type) -> Any:
    """Convert a Pydantic model instance to a Strawberry type instance."""
    data = model.model_dump() if hasattr(model, "model_dump") else dict(model)
    for k, v in data.items():
        if isinstance(v, uuid.UUID):
            data[k] = str(v)
        elif isinstance(v, datetime):
            data[k] = v.isoformat() if v else None
    try:
        return strawberry_type(**data)
    except TypeError:
        hints = getattr(strawberry_type, "__annotations__", {})
        filtered = {k: v for k, v in data.items() if k in hints}
        return strawberry_type(**filtered)


def _extract_refs(schema: Any, seen: set | None = None) -> list[str]:
    """Walk a schema and extract referenced schema names from $ref pointers."""
    if seen is None:
        seen = set()
    refs: list[str] = []
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if ref and isinstance(ref, str) and not ref.startswith("#"):
            parts = ref.replace("\\", "/").split("/")
            name = parts[-1].replace(".json", "").split("#")[0]
            if name and name not in seen:
                seen.add(name)
                refs.append(name)
        for v in schema.values():
            refs.extend(_extract_refs(v, seen))
    elif isinstance(schema, list):
        for item in schema:
            refs.extend(_extract_refs(item, seen))
    return refs
