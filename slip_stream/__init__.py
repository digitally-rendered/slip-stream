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

from slip_stream.adapters.api.endpoint_factory import EndpointFactory
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
from slip_stream.adapters.api.filters.etag import ETagFilter
from slip_stream.adapters.api.filters.middleware import FilterChainMiddleware
from slip_stream.adapters.api.filters.projection import FieldProjectionFilter
from slip_stream.adapters.api.filters.schema_version import SchemaVersionFilter

try:
    from slip_stream.adapters.api.graphql_factory import GraphQLFactory

    _has_graphql = True
except ImportError:
    _has_graphql = False
from slip_stream.adapters.api.schema_router import (
    register_schema_endpoint,
    register_schema_endpoint_from_registration,
    register_schema_endpoints,
)
from slip_stream.adapters.api.schema_vending import create_schema_vending_router
from slip_stream.adapters.persistence.db.crud_factory import CRUDFactory
from slip_stream.adapters.persistence.schema.composite_storage import (
    CompositeSchemaStorage,
)
from slip_stream.adapters.persistence.schema.file_storage import FileSchemaStorage
from slip_stream.adapters.persistence.schema.mongo_storage import MongoSchemaStorage

try:
    from slip_stream.adapters.persistence.schema.http_storage import HttpSchemaStorage

    _has_http_storage = True
except ImportError:
    _has_http_storage = False
from dotted_dict import DottedDict

from slip_stream.adapters.api.filters.rate_limit import RateLimitFilter
from slip_stream.adapters.api.filters.rego import RegoPolicyFilter
from slip_stream.adapters.persistence.db.generic_crud import VersionedMongoCRUD
from slip_stream.adapters.persistence.db.repository_factory import RepositoryFactory
from slip_stream.adapters.streaming.base import (
    EventStreamBridge,
    InMemoryStream,
    StreamAdapter,
    StreamEvent,
)
from slip_stream.app import SlipStream
from slip_stream.container import (
    EntityContainer,
    EntityRegistration,
    get_container,
    init_container,
)
from slip_stream.core.audit import AuditTrail
from slip_stream.core.context import HandlerOverride, RequestContext
from slip_stream.core.domain.base import BaseDocument
from slip_stream.core.events import EventBus, HookError
from slip_stream.core.operation import OperationExecutor
from slip_stream.core.policy import (
    InlinePolicy,
    LocalRegoPolicy,
    OpaRemotePolicy,
    PolicyEngine,
    PolicyEvaluationError,
)
from slip_stream.core.query import QueryDSL, QueryValidationError, parse_sort_param
from slip_stream.core.schema.ref_resolver import RefResolver
from slip_stream.core.schema.watcher import SchemaWatcher
from slip_stream.core.webhooks import WebhookDispatcher

try:
    from slip_stream.adapters.persistence.db.sql_repository import (
        SQLRepository,
        build_table_from_schema,
    )
    from slip_stream.adapters.persistence.db.sql_repository_factory import (
        SQLRepositoryFactory,
    )

    _has_sql = True
except ImportError:
    _has_sql = False
from slip_stream.config import SlipStreamConfig
from slip_stream.core.ports.repository import RepositoryPort
from slip_stream.core.ports.schema_storage import SchemaStoragePort
from slip_stream.core.schema.registry import SchemaRegistry
from slip_stream.core.schema.versioning import (
    compare_versions,
    is_valid_semver,
    latest_version,
    parse_semver,
    sort_versions,
)
from slip_stream.core.services.generic import (
    GenericCreateService,
    GenericDeleteService,
    GenericGetService,
    GenericListService,
    GenericUpdateService,
)
from slip_stream.core.storage import StorageBackend, StorageConfig
from slip_stream.database import DatabaseManager
from slip_stream.logging_config import configure_logging
from slip_stream.registry import SlipStreamRegistry
from slip_stream.sdk_generator import generate_sdk

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
    "ETagFilter",
    "FieldProjectionFilter",
    "SchemaVersionFilter",
    # Context & Events
    "DottedDict",
    "RequestContext",
    "HandlerOverride",
    "EventBus",
    "HookError",
    "OperationExecutor",
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
    "CompositeSchemaStorage",
    # Schema utilities
    "RefResolver",
    "create_schema_vending_router",
    # API
    "EndpointFactory",
    "register_schema_endpoint",
    "register_schema_endpoints",
    "register_schema_endpoint_from_registration",
    # Container
    "EntityContainer",
    "EntityRegistration",
    "init_container",
    "get_container",
    # Policy
    "PolicyEngine",
    "OpaRemotePolicy",
    "LocalRegoPolicy",
    "InlinePolicy",
    "PolicyEvaluationError",
    "RegoPolicyFilter",
    # Query DSL
    "QueryDSL",
    "QueryValidationError",
    "parse_sort_param",
    # Audit & Webhooks
    "AuditTrail",
    "WebhookDispatcher",
    "RateLimitFilter",
    "SchemaWatcher",
    # Event streaming
    "StreamAdapter",
    "StreamEvent",
    "InMemoryStream",
    "EventStreamBridge",
    # Storage routing
    "StorageBackend",
    "StorageConfig",
    # Configuration
    "SlipStreamConfig",
    # SDK generation
    "generate_sdk",
    # Database
    "DatabaseManager",
    # Logging
    "configure_logging",
]

try:
    from slip_stream.adapters.api.filters.telemetry import TelemetryFilter
    from slip_stream.telemetry import SlipStreamInstrumentor

    _has_telemetry = True
except ImportError:
    _has_telemetry = False

# Conditionally include optional-dependency exports
if _has_graphql:
    __all__.append("GraphQLFactory")
if _has_http_storage:
    __all__.append("HttpSchemaStorage")
if _has_sql:
    __all__.extend(["SQLRepository", "build_table_from_schema", "SQLRepositoryFactory"])
if _has_telemetry:
    __all__.extend(["SlipStreamInstrumentor", "TelemetryFilter"])
