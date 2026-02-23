"""slip-stream: JSON Schema-driven hexagonal backend framework for FastAPI + MongoDB.

Drop a JSON schema file, get full CRUD API endpoints with versioned MongoDB
persistence. No boilerplate, fully overridable at every layer.

Quick start::

    from pathlib import Path
    from fastapi import FastAPI
    from slip_stream import SlipStream

    app = FastAPI()
    slip = SlipStream(app=app, schema_dir=Path("./schemas"))
"""

from slip_stream.adapters.api.filters.auth import AuthFilter
from slip_stream.adapters.api.filters.base import (
    FilterBase,
    FilterContext,
    FilterShortCircuit,
)
from slip_stream.adapters.api.filters.chain import FilterChain
from slip_stream.adapters.api.filters.content_negotiation import (
    ContentNegotiationFilter,
)
from slip_stream.adapters.api.filters.envelope import ResponseEnvelopeFilter
from slip_stream.adapters.api.filters.middleware import FilterChainMiddleware
from slip_stream.adapters.api.filters.projection import FieldProjectionFilter
from slip_stream.adapters.api.filters.schema_version import SchemaVersionFilter
from slip_stream.adapters.api.endpoint_factory import EndpointFactory

try:
    from slip_stream.adapters.api.graphql_factory import GraphQLFactory
except ImportError:
    pass
from slip_stream.adapters.api.schema_router import (
    register_schema_endpoint,
    register_schema_endpoint_from_registration,
    register_schema_endpoints,
)
from slip_stream.adapters.api.schema_vending import create_schema_vending_router
from slip_stream.adapters.persistence.db.crud_factory import CRUDFactory
from slip_stream.adapters.persistence.schema.file_storage import FileSchemaStorage
from slip_stream.adapters.persistence.schema.mongo_storage import MongoSchemaStorage
from slip_stream.adapters.persistence.schema.composite_storage import CompositeSchemaStorage

try:
    from slip_stream.adapters.persistence.schema.http_storage import HttpSchemaStorage
except ImportError:
    pass
from slip_stream.core.schema.ref_resolver import RefResolver
from slip_stream.adapters.persistence.db.generic_crud import VersionedMongoCRUD
from slip_stream.adapters.persistence.db.repository_factory import RepositoryFactory
from slip_stream.app import SlipStream
from slip_stream.container import (
    EntityContainer,
    EntityRegistration,
    get_container,
    init_container,
)
from dotted_dict import DottedDict
from slip_stream.core.context import HandlerOverride, RequestContext
from slip_stream.core.domain.base import BaseDocument
from slip_stream.core.events import EventBus, HookError
from slip_stream.core.ports.repository import RepositoryPort
from slip_stream.core.ports.schema_storage import SchemaStoragePort
from slip_stream.core.schema.versioning import (
    compare_versions,
    is_valid_semver,
    latest_version,
    parse_semver,
    sort_versions,
)
from slip_stream.registry import SlipStreamRegistry
from slip_stream.core.schema.registry import SchemaRegistry
from slip_stream.core.services.generic import (
    GenericCreateService,
    GenericDeleteService,
    GenericGetService,
    GenericListService,
    GenericUpdateService,
)
from slip_stream.database import DatabaseManager

__all__ = [
    # App builder
    "SlipStream",
    "SlipStreamRegistry",
    # Filters
    "FilterBase",
    "FilterContext",
    "FilterShortCircuit",
    "FilterChain",
    "FilterChainMiddleware",
    "ContentNegotiationFilter",
    "AuthFilter",
    "ResponseEnvelopeFilter",
    "FieldProjectionFilter",
    "SchemaVersionFilter",
    # Context & Events
    "DottedDict",
    "RequestContext",
    "HandlerOverride",
    "EventBus",
    "HookError",
    # Core domain
    "BaseDocument",
    "RepositoryPort",
    "SchemaStoragePort",
    # Schema versioning
    "parse_semver",
    "compare_versions",
    "sort_versions",
    "latest_version",
    "is_valid_semver",
    # Schema registry
    "SchemaRegistry",
    # Services
    "GenericCreateService",
    "GenericGetService",
    "GenericListService",
    "GenericUpdateService",
    "GenericDeleteService",
    # Persistence
    "VersionedMongoCRUD",
    "CRUDFactory",
    "RepositoryFactory",
    # Schema storage
    "FileSchemaStorage",
    "MongoSchemaStorage",
    "HttpSchemaStorage",
    "CompositeSchemaStorage",
    # Schema utilities
    "RefResolver",
    "create_schema_vending_router",
    # API
    "EndpointFactory",
    "GraphQLFactory",
    "register_schema_endpoint",
    "register_schema_endpoints",
    "register_schema_endpoint_from_registration",
    # Container
    "EntityContainer",
    "EntityRegistration",
    "init_container",
    "get_container",
    # Database
    "DatabaseManager",
]
