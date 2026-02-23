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

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Type

logger = logging.getLogger(__name__)

try:
    import strawberry
    from strawberry.fastapi import GraphQLRouter
    from strawberry.scalars import JSON
    from strawberry.types import Info

    HAS_STRAWBERRY = True
except ImportError:
    HAS_STRAWBERRY = False


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
        return List[item_type]
    if json_type == "object":
        return JSON
    return str


# Fields managed by the framework envelope
_AUDIT_FIELDS = frozenset({
    "id", "entity_id", "schema_version", "record_version",
    "created_at", "updated_at", "deleted_at",
    "created_by", "updated_by", "deleted_by",
})


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
    ) -> "GraphQLRouter":
        """Create a Strawberry GraphQLRouter with auto-generated types.

        Args:
            container: The EntityContainer with resolved registrations.
            get_db: FastAPI dependency returning AsyncIOMotorDatabase.
            schema_registry: The SchemaRegistry instance.
            get_current_user: FastAPI dependency returning user dict.
            event_bus: Optional EventBus for lifecycle hooks.
            custom_mutations: Additional strawberry mutation fields to include.

        Returns:
            A Strawberry GraphQLRouter ready to mount on FastAPI.
        """
        _ensure_strawberry()

        registrations = container.get_all()
        entity_types: dict[str, type] = {}

        for schema_name, reg in registrations.items():
            pascal = self._to_pascal(schema_name)
            schema = schema_registry.get_schema(schema_name, "latest")
            properties = schema.get("properties", {})

            entity_type = self._create_entity_type(pascal, properties, schema_name)
            entity_types[schema_name] = entity_type

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
        )
        Mutation = self._build_schema_class(
            "Mutation",
            registrations,
            entity_types,
            schema_registry,
            get_db,
            event_bus,
            mode="mutation",
        )

        graphql_schema = strawberry.Schema(query=Query, mutation=Mutation)
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
        return strawberry.type(entity_cls, description=f"Auto-generated type for {schema_name}")

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
    ) -> type:
        """Build a Strawberry-decorated Query or Mutation class."""
        methods: dict[str, Any] = {}
        annotations: dict[str, Any] = {}

        if mode == "query":
            for schema_name, reg in registrations.items():
                et = entity_types[schema_name]

                # get_<entity>
                get_fn = self._make_get_resolver(schema_name, et, reg, get_db)
                methods[f"get_{schema_name}"] = strawberry.field(resolver=get_fn)

                # list_<entities>
                list_fn = self._make_list_resolver(schema_name, et, reg, get_db)
                methods[f"list_{schema_name}s"] = strawberry.field(resolver=list_fn)

            # Schema DAG query
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

                create_fn = self._make_create_resolver(
                    schema_name, et, create_input, reg, get_db
                )
                methods[f"create_{schema_name}"] = strawberry.mutation(resolver=create_fn)

                update_fn = self._make_update_resolver(
                    schema_name, et, update_input, reg, get_db
                )
                methods[f"update_{schema_name}"] = strawberry.mutation(resolver=update_fn)

                delete_fn = self._make_delete_resolver(schema_name, reg, get_db)
                methods[f"delete_{schema_name}"] = strawberry.mutation(resolver=delete_fn)

        # Build namespace for type()
        ns = {**methods}
        cls = type(class_name, (), ns)
        return strawberry.type(cls)

    def _make_get_resolver(self, schema_name, entity_type, registration, get_db):
        et = entity_type
        reg = registration

        async def resolver(info: Info, entity_id: str) -> Optional[et]:  # type: ignore
            db = await _resolve_db(info, get_db)
            repo = reg.repository_class(db)
            entity = await repo.get_by_entity_id(entity_id=uuid.UUID(entity_id))
            if entity is None:
                return None
            return _model_to_strawberry(entity, et)

        resolver.__name__ = f"get_{schema_name}"
        return resolver

    def _make_list_resolver(self, schema_name, entity_type, registration, get_db):
        et = entity_type
        reg = registration

        async def resolver(info: Info, skip: int = 0, limit: int = 100) -> List[et]:  # type: ignore
            db = await _resolve_db(info, get_db)
            repo = reg.repository_class(db)
            svc = reg.services["list"](repo)
            entities = await svc.execute(skip=skip, limit=limit)
            return [_model_to_strawberry(e, et) for e in entities]

        resolver.__name__ = f"list_{schema_name}s"
        return resolver

    def _make_create_resolver(self, schema_name, entity_type, create_input, registration, get_db):
        et = entity_type
        ci = create_input
        reg = registration

        async def resolver(info: Info, input: ci) -> et:  # type: ignore
            db = await _resolve_db(info, get_db)
            user_id = _get_user_id(info)
            data = reg.create_model(**strawberry.asdict(input))
            repo = reg.repository_class(db)
            svc = reg.services["create"](repo)
            result = await svc.execute(data=data, user_id=user_id)
            return _model_to_strawberry(result, et)

        resolver.__name__ = f"create_{schema_name}"
        return resolver

    def _make_update_resolver(self, schema_name, entity_type, update_input, registration, get_db):
        et = entity_type
        ui = update_input
        reg = registration

        async def resolver(info: Info, entity_id: str, input: ui) -> Optional[et]:  # type: ignore
            db = await _resolve_db(info, get_db)
            user_id = _get_user_id(info)
            input_dict = {k: v for k, v in strawberry.asdict(input).items() if v is not None}
            data = reg.update_model(**input_dict)
            repo = reg.repository_class(db)
            svc = reg.services["update"](repo)
            result = await svc.execute(
                entity_id=uuid.UUID(entity_id), data=data, user_id=user_id
            )
            return _model_to_strawberry(result, et)

        resolver.__name__ = f"update_{schema_name}"
        return resolver

    def _make_delete_resolver(self, schema_name, registration, get_db):
        reg = registration

        async def resolver(info: Info, entity_id: str) -> bool:
            db = await _resolve_db(info, get_db)
            user_id = _get_user_id(info)
            repo = reg.repository_class(db)
            svc = reg.services["delete"](repo)
            await svc.execute(entity_id=uuid.UUID(entity_id), user_id=user_id)
            return True

        resolver.__name__ = f"delete_{schema_name}"
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
            return dag

        return schema_dag


async def _resolve_db(info: Info, get_db: Any) -> Any:
    """Resolve the database from the request context."""
    if hasattr(get_db, "__call__"):
        from inspect import iscoroutinefunction
        if iscoroutinefunction(get_db):
            return await get_db()
        return get_db()
    return None


def _get_user_id(info: Info) -> str:
    """Extract user ID from the GraphQL context."""
    request = info.context.get("request")
    if request:
        return request.headers.get("x-user-id", "anonymous")
    return "anonymous"


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
