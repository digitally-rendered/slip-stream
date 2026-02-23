"""SlipStream app builder — wires schema registry, container, and endpoints.

Provides a high-level integration class that handles the full lifecycle:
schema discovery, container resolution, and endpoint registration.

Usage::

    from pathlib import Path
    from fastapi import FastAPI
    from slip_stream import SlipStream

    app = FastAPI()
    slip = SlipStream(
        app=app,
        schema_dir=Path("./schemas"),
        api_prefix="/api/v1",
    )

    # In FastAPI lifespan:
    async with slip.lifespan():
        yield
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable, List, Optional

from fastapi import APIRouter, FastAPI

from slip_stream.adapters.api.filters.base import FilterBase
from slip_stream.adapters.api.filters.chain import FilterChain
from slip_stream.adapters.api.filters.middleware import FilterChainMiddleware
from slip_stream.adapters.api.schema_router import (
    register_schema_endpoint_from_registration,
)
from slip_stream.container import EntityContainer, init_container
from slip_stream.core.events import EventBus
from slip_stream.core.schema.registry import SchemaRegistry
from slip_stream.database import DatabaseManager
from slip_stream.registry import SlipStreamRegistry

logger = logging.getLogger(__name__)


class SlipStream:
    """High-level integration class for schema-driven FastAPI applications.

    Handles schema discovery, container initialization, endpoint registration,
    and database lifecycle.

    Args:
        app: The FastAPI application instance.
        schema_dir: Path to the directory containing JSON schema files.
        api_prefix: URL prefix for all generated endpoints (default: ``/api/v1``).
        get_db: FastAPI dependency returning AsyncIOMotorDatabase. If not
            provided, uses the built-in DatabaseManager.
        get_current_user: FastAPI dependency returning a user dict. If not
            provided, uses the default X-User-ID header dependency.
        mongo_uri: MongoDB connection string (used only with built-in DatabaseManager).
        database_name: MongoDB database name (used only with built-in DatabaseManager).
        filters: List of ``FilterBase`` instances to apply as ASGI middleware.
            Filters are sorted by ``order`` and executed in onion model.
        event_bus: Optional ``EventBus`` for lifecycle hooks (pre/post CRUD).
        structured_errors: When ``True``, install structured error handlers that
            produce consistent JSON error format and flow through filters.
        models_module: Dotted module path for hand-crafted Pydantic model overrides.
        repositories_module: Dotted module path prefix for custom repository overrides.
        services_module: Dotted module path prefix for custom service overrides.
        controllers_module: Dotted module path prefix for custom controller overrides.
        registry: A ``SlipStreamRegistry`` instance with decorator-based handler
            overrides and lifecycle hooks. Applied during ``lifespan()`` startup.
    """

    def __init__(
        self,
        app: FastAPI,
        schema_dir: Path,
        api_prefix: str = "/api/v1",
        get_db: Optional[Callable] = None,
        get_current_user: Optional[Callable] = None,
        mongo_uri: Optional[str] = None,
        database_name: Optional[str] = None,
        filters: Optional[List[FilterBase]] = None,
        event_bus: Optional[EventBus] = None,
        structured_errors: bool = False,
        models_module: Optional[str] = None,
        repositories_module: Optional[str] = None,
        services_module: Optional[str] = None,
        controllers_module: Optional[str] = None,
        registry: Optional[SlipStreamRegistry] = None,
        schema_storage: Optional[Any] = None,
        schema_vending: bool = False,
        schema_vending_prefix: str = "/schemas",
        graphql: bool = False,
        graphql_prefix: str = "/graphql",
    ) -> None:
        self.app = app
        self.schema_dir = schema_dir
        self.api_prefix = api_prefix
        self.get_current_user = get_current_user
        self.models_module = models_module
        self.repositories_module = repositories_module
        self.services_module = services_module
        self.controllers_module = controllers_module

        self._filters = filters
        self._event_bus = event_bus
        self._structured_errors = structured_errors
        self._registry = registry
        self._schema_storage = schema_storage
        self._schema_vending = schema_vending
        self._schema_vending_prefix = schema_vending_prefix
        self._graphql = graphql
        self._graphql_prefix = graphql_prefix

        # Auto-create EventBus when registry is provided but no bus given
        if registry is not None and self._event_bus is None:
            self._event_bus = EventBus()
        self._db_manager: Optional[DatabaseManager] = None
        self._container: Optional[EntityContainer] = None
        self._api_router = APIRouter()

        if get_db is not None:
            self._get_db = get_db
        else:
            self._db_manager = DatabaseManager(
                mongo_uri=mongo_uri,
                database_name=database_name,
            )
            self._get_db = self._db_manager.get_database

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Async context manager for the full application lifecycle.

        Connects to MongoDB, initializes the schema registry and container,
        registers all endpoints, then yields. On exit, closes the DB connection.
        """
        # Connect to database if using built-in manager
        if self._db_manager is not None:
            await self._db_manager.connect()
            logger.info("Connected to MongoDB")

        # Initialize schema registry
        registry = SchemaRegistry(schema_dir=self.schema_dir)

        # Sync with storage backend (bidirectional: file→storage, storage→memory)
        if self._schema_storage is not None:
            await registry.sync_from_storage(self._schema_storage)
            logger.info("Schema registry synced with storage backend")

        schema_names = registry.get_schema_names()
        logger.info("Discovered schemas: %s", schema_names)

        # Resolve all overrides via the container
        self._container = init_container(
            schema_names=schema_names,
            models_module=self.models_module,
            repositories_module=self.repositories_module,
            services_module=self.services_module,
            controllers_module=self.controllers_module,
        )

        # Apply registry overrides (must run after container init, before routes)
        if self._registry is not None:
            self._registry.apply(self._container, self._event_bus)
            logger.info("Registry applied: handlers and hooks merged")

        # Register endpoints for all schemas
        for schema_name in schema_names:
            path_name = schema_name.replace("_", "-")
            registration = self._container.get(schema_name)
            logger.info("Registering endpoint: /%s", path_name)
            register_schema_endpoint_from_registration(
                api_router=self._api_router,
                registration=registration,
                get_db=self._get_db,
                get_current_user=self.get_current_user,
                custom_path=path_name,
                custom_tags=[schema_name.replace("_", " ").title()],
                event_bus=self._event_bus,
            )

        self.app.include_router(self._api_router, prefix=self.api_prefix)

        # Mount schema vending API if enabled
        if self._schema_vending:
            from slip_stream.adapters.api.schema_vending import (
                create_schema_vending_router,
            )

            vending_router = create_schema_vending_router(
                schema_registry=registry,
                prefix=self._schema_vending_prefix,
            )
            self.app.include_router(vending_router)
            logger.info(
                "Schema vending API mounted at %s", self._schema_vending_prefix
            )

        # Mount GraphQL API if enabled
        if self._graphql:
            from slip_stream.adapters.api.graphql_factory import GraphQLFactory

            gql_factory = GraphQLFactory()
            gql_router = gql_factory.create_graphql_router(
                container=self._container,
                get_db=self._get_db,
                schema_registry=registry,
                get_current_user=self.get_current_user,
                event_bus=self._event_bus,
            )
            self.app.include_router(gql_router, prefix=self._graphql_prefix)
            logger.info("GraphQL API mounted at %s", self._graphql_prefix)

        # Install structured error handlers if enabled
        if self._structured_errors:
            from slip_stream.adapters.api.error_handler import (
                install_error_handlers,
            )

            install_error_handlers(self.app)
            logger.info("Structured error handlers installed")

        # Install filter chain middleware if filters were provided
        if self._filters:
            chain = FilterChain()
            chain.add_filters(self._filters)
            self.app.add_middleware(FilterChainMiddleware, filter_chain=chain)
            logger.info(
                "Filter chain installed with %d filter(s)", len(self._filters)
            )

        yield

        # Cleanup
        if self._db_manager is not None:
            await self._db_manager.close()
            logger.info("Closed MongoDB connection")

    @property
    def container(self) -> EntityContainer:
        """Return the resolved container. Only available after lifespan starts."""
        if self._container is None:
            raise RuntimeError(
                "Container not initialized. Ensure SlipStream.lifespan() has started."
            )
        return self._container
