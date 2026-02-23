"""Dependency container that resolves all schema-driven entities at startup.

Resolution order (each layer falls back to the generic default if no override
is found):

    1. Models     — check ``models_module`` for hand-crafted classes,
                    fall back to SchemaRegistry.generate_*_model()
    2. Repository — check ``repositories_module.{name}_repository``
                    for a custom class, fall back to RepositoryFactory.create()
    3. Services   — check ``services_module.{name}_service`` for custom
                    {Pascal}{Op}Service classes, fall back to Generic*Service
    4. Controller — check ``controllers_module.{name}_controller``
                    for a create_router() callable, None means use EndpointFactory

To override any layer for a specific entity, simply drop the appropriately
named module in the expected location and define the expected symbol. The
container discovers it automatically on the next startup.

Usage::

    container = init_container(
        schema_names=registry.get_schema_names(),
        models_module="myapp.domain.models",
        repositories_module="myapp.persistence",
        services_module="myapp.services",
        controllers_module="myapp.controllers",
    )
    reg = container.get("widget")
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import BaseModel

from slip_stream.adapters.persistence.db.repository_factory import RepositoryFactory
from slip_stream.core.domain.base import BaseDocument
from slip_stream.core.schema.registry import SchemaRegistry
from slip_stream.core.services.generic import (
    GenericCreateService,
    GenericDeleteService,
    GenericGetService,
    GenericListService,
    GenericUpdateService,
)

_container: "EntityContainer | None" = None


def _to_pascal(schema_name: str) -> str:
    """Convert a snake_case schema name to PascalCase."""
    return "".join(word.capitalize() for word in schema_name.split("_"))


def _try_import(module_path: str, attr_name: str) -> Any | None:
    """Attempt to import *attr_name* from *module_path*.

    Returns the attribute if the module and attribute both exist.
    Returns ``None`` on ``ImportError`` or ``AttributeError``.
    """
    try:
        module = importlib.import_module(module_path)
        return getattr(module, attr_name)
    except ImportError:
        return None
    except AttributeError:
        return None


@dataclass
class EntityRegistration:
    """Fully resolved registration for a single schema-driven entity.

    Attributes:
        schema_name: Snake-case schema identifier, e.g. ``"widget"``.
        document_model: Pydantic model class extending BaseDocument.
        create_model: Pydantic model class for creation payloads.
        update_model: Pydantic model class for update payloads.
        repository_class: Un-instantiated repository class (takes ``db`` arg).
        services: Mapping of operation name to service class.
        controller_factory: Custom ``create_router()`` callable, or ``None``.
    """

    schema_name: str
    document_model: type[BaseDocument]
    create_model: type[BaseModel]
    update_model: type[BaseModel]
    repository_class: type
    services: dict[str, type] = field(default_factory=dict)
    controller_factory: Any | None = None
    handler_overrides: dict[str, Any] = field(default_factory=dict)


class EntityContainer:
    """Resolves and stores registrations for all schema-driven entities.

    Call :meth:`resolve_all` once during application startup, then use
    :meth:`get` or :meth:`get_all` to retrieve resolved registrations.

    Args:
        models_module: Dotted module path to check for hand-crafted models.
        repositories_module: Dotted module path prefix for custom repositories.
        services_module: Dotted module path prefix for custom services.
        controllers_module: Dotted module path prefix for custom controllers.
    """

    def __init__(
        self,
        models_module: str | None = None,
        repositories_module: str | None = None,
        services_module: str | None = None,
        controllers_module: str | None = None,
    ) -> None:
        self._registrations: dict[str, EntityRegistration] = {}
        self._models_module = models_module
        self._repositories_module = repositories_module
        self._services_module = services_module
        self._controllers_module = controllers_module

    def resolve_all(self, schema_names: list[str]) -> None:
        """Resolve and register every schema name in *schema_names*."""
        for name in schema_names:
            registration = self._resolve_entity(name)
            self._registrations[name] = registration

    def get(self, schema_name: str) -> EntityRegistration:
        """Return the registration for *schema_name*.

        Raises:
            KeyError: If *schema_name* has not been resolved.
        """
        return self._registrations[schema_name]

    def get_all(self) -> dict[str, EntityRegistration]:
        """Return a shallow copy of all registrations."""
        return dict(self._registrations)

    def _resolve_entity(self, schema_name: str) -> EntityRegistration:
        """Resolve models, repository, services, and controller for one entity."""
        pascal = _to_pascal(schema_name)
        registry = SchemaRegistry()

        doc_model = self._resolve_document_model(schema_name, pascal, registry)
        create_model = self._resolve_create_model(schema_name, pascal, registry)
        update_model = self._resolve_update_model(schema_name, pascal, registry)

        repo_class = self._resolve_repository(
            schema_name, pascal, doc_model, create_model, update_model
        )

        services = self._resolve_services(schema_name, pascal)

        controller_factory = None
        handler_overrides: dict[str, Any] = {}
        if self._controllers_module:
            controller_factory = _try_import(
                f"{self._controllers_module}.{schema_name}_controller",
                "create_router",
            )
            # Resolve individual handler overrides
            for op in ("create", "get", "list", "update", "delete"):
                handler = _try_import(
                    f"{self._controllers_module}.{schema_name}_controller",
                    f"{op}_handler",
                )
                if handler is not None:
                    handler_overrides[op] = handler

        return EntityRegistration(
            schema_name=schema_name,
            document_model=doc_model,
            create_model=create_model,
            update_model=update_model,
            repository_class=repo_class,
            services=services,
            controller_factory=controller_factory,
            handler_overrides=handler_overrides,
        )

    def _resolve_document_model(
        self,
        schema_name: str,
        pascal: str,
        registry: SchemaRegistry,
    ) -> type[BaseDocument]:
        if self._models_module:
            candidate = _try_import(self._models_module, pascal)
            if (
                candidate is not None
                and isinstance(candidate, type)
                and issubclass(candidate, BaseDocument)
            ):
                return cast(type[BaseDocument], candidate)
        return registry.generate_document_model(schema_name)

    def _resolve_create_model(
        self,
        schema_name: str,
        pascal: str,
        registry: SchemaRegistry,
    ) -> type[BaseModel]:
        if self._models_module:
            candidate = _try_import(self._models_module, f"{pascal}Create")
            if (
                candidate is not None
                and isinstance(candidate, type)
                and issubclass(candidate, BaseModel)
            ):
                return cast(type[BaseModel], candidate)
        return registry.generate_create_model(schema_name)

    def _resolve_update_model(
        self,
        schema_name: str,
        pascal: str,
        registry: SchemaRegistry,
    ) -> type[BaseModel]:
        if self._models_module:
            candidate = _try_import(self._models_module, f"{pascal}Update")
            if (
                candidate is not None
                and isinstance(candidate, type)
                and issubclass(candidate, BaseModel)
            ):
                return cast(type[BaseModel], candidate)
        return registry.generate_update_model(schema_name)

    def _resolve_repository(
        self,
        schema_name: str,
        pascal: str,
        doc_model: type[BaseDocument],
        create_model: type[BaseModel],
        update_model: type[BaseModel],
    ) -> type:
        if self._repositories_module:
            custom = _try_import(
                f"{self._repositories_module}.{schema_name}_repository",
                f"{pascal}Repository",
            )
            if custom is not None:
                return cast(type, custom)
        return RepositoryFactory.create(
            schema_name=schema_name,
            doc_model=doc_model,
            create_model=create_model,
            update_model=update_model,
        )

    def _resolve_services(self, schema_name: str, pascal: str) -> dict[str, type]:
        ops: list[tuple[str, type]] = [
            ("create", GenericCreateService),
            ("get", GenericGetService),
            ("list", GenericListService),
            ("update", GenericUpdateService),
            ("delete", GenericDeleteService),
        ]
        services: dict[str, type] = {}
        for op_key, generic_cls in ops:
            op_pascal = op_key.capitalize()
            custom = None
            if self._services_module:
                custom = _try_import(
                    f"{self._services_module}.{schema_name}_service",
                    f"{pascal}{op_pascal}Service",
                )
            services[op_key] = custom if custom is not None else generic_cls
        return services


def init_container(
    schema_names: list[str],
    models_module: str | None = None,
    repositories_module: str | None = None,
    services_module: str | None = None,
    controllers_module: str | None = None,
) -> EntityContainer:
    """Create, populate, and store the module-level container singleton.

    Call once during application startup.

    Args:
        schema_names: List of schema names from ``SchemaRegistry().get_schema_names()``.
        models_module: Dotted path to module with hand-crafted Pydantic models.
        repositories_module: Dotted path prefix for custom repository modules.
        services_module: Dotted path prefix for custom service modules.
        controllers_module: Dotted path prefix for custom controller modules.

    Returns:
        The populated container instance (also stored as module singleton).
    """
    global _container
    container = EntityContainer(
        models_module=models_module,
        repositories_module=repositories_module,
        services_module=services_module,
        controllers_module=controllers_module,
    )
    container.resolve_all(schema_names)
    _container = container
    return container


def get_container() -> EntityContainer:
    """Return the module-level container singleton.

    Raises:
        RuntimeError: If :func:`init_container` has not been called yet.
    """
    if _container is None:
        raise RuntimeError(
            "EntityContainer has not been initialised. "
            "Call init_container() during application startup before using get_container()."
        )
    return _container
