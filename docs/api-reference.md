# API Reference

All public symbols exported from `slip_stream`.

```python
from slip_stream import <symbol>
```

## App Builder

### SlipStream

```python
class SlipStream:
    def __init__(
        self,
        app: FastAPI,
        schema_dir: Path,
        api_prefix: str = "/api/v1",
        get_db: Callable | None = None,
        get_current_user: Callable | None = None,
        mongo_uri: str | None = None,
        database_name: str | None = None,
        filters: list[FilterBase] | None = None,
        event_bus: EventBus | None = None,
        structured_errors: bool = False,
        models_module: str | None = None,
        repositories_module: str | None = None,
        services_module: str | None = None,
        controllers_module: str | None = None,
        registry: SlipStreamRegistry | None = None,
    ) -> None: ...

    async def lifespan(self) -> AsyncIterator[None]: ...
    @property
    def container(self) -> EntityContainer: ...
```

### SlipStreamRegistry

```python
class SlipStreamRegistry:
    def handler(self, schema: str, operation: str) -> decorator: ...
    def guard(self, schema: str, *operations: str) -> decorator: ...
    def validate(self, schema: str, *operations: str) -> decorator: ...
    def transform(self, schema: str, *operations: str, when: str = "before") -> decorator: ...
    def on(self, event: str, schema_name: str = "*") -> decorator: ...
    def apply(self, container: EntityContainer, event_bus: EventBus) -> None: ...
```

## Context & Events

### RequestContext

```python
@dataclass
class RequestContext:
    request: Request
    operation: Literal["create", "get", "list", "update", "delete"]
    schema_name: str
    entity_id: UUID | None = None
    entity: BaseModel | None = None
    data: BaseModel | None = None
    current_user: dict | None = None
    db: Any = None
    response: Response | None = None
    result: Any = None
    skip: int = 0
    limit: int = 100
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_request(cls, request, operation, schema_name, **kwargs) -> RequestContext: ...
```

### HandlerOverride

```python
@runtime_checkable
class HandlerOverride(Protocol):
    async def __call__(self, ctx: RequestContext) -> Any: ...
```

### EventBus

```python
class EventBus:
    def on(self, event: str, schema_name: str = "*") -> decorator: ...
    def register(self, event: str, handler: EventHandler, schema_name: str = "*") -> None: ...
    async def emit(self, event: str, ctx: RequestContext) -> None: ...
    @property
    def handler_count(self) -> int: ...
```

### HookError

```python
class HookError(Exception):
    def __init__(self, status_code: int = 400, detail: str = "") -> None: ...
    status_code: int
    detail: str
```

## Filters

### FilterBase

```python
class FilterBase(ABC):
    order: int = 100
    @abstractmethod
    async def on_request(self, request: Request, context: FilterContext) -> None: ...
    @abstractmethod
    async def on_response(self, request: Request, response: Response, context: FilterContext) -> Response: ...
```

### FilterContext

```python
@dataclass
class FilterContext:
    content_type: str = "application/json"
    accept: str = "application/json"
    user: dict | None = None
    extras: dict = field(default_factory=dict)
```

### FilterShortCircuit

```python
class FilterShortCircuit(Exception):
    def __init__(self, status_code: int = 400, body: str = "", headers: dict | None = None): ...
```

### FilterChain

```python
class FilterChain:
    def add_filter(self, f: FilterBase) -> None: ...
    def add_filters(self, filters: list[FilterBase]) -> None: ...
    async def process_request(self, request: Request) -> FilterContext: ...
    async def process_response(self, request, response, context) -> Response: ...
```

### FilterChainMiddleware

```python
class FilterChainMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, filter_chain: FilterChain) -> None: ...
```

### Built-in Filters

```python
class AuthFilter(FilterBase):           # order=10
    def __init__(self, authenticate: Callable, realm: str = "slip-stream"): ...

class ContentNegotiationFilter(FilterBase):  # order=50
    pass  # No constructor args

class ResponseEnvelopeFilter(FilterBase):    # order=90
    def __init__(self, include_pagination: bool = True): ...

class FieldProjectionFilter(FilterBase):     # order=95
    def __init__(self, role_field_rules: dict | None = None, allow_query_projection: bool = True): ...
```

## Core Domain

### BaseDocument

```python
class BaseDocument(BaseModel):
    id: UUID | None = None
    entity_id: UUID | None = None
    schema_version: str = "1.0.0"
    record_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None
    created_by: str | None = None
    updated_by: str | None = None
    deleted_by: str | None = None
```

### RepositoryPort

```python
class RepositoryPort(Protocol):
    async def create(self, data, user_id: str) -> BaseDocument: ...
    async def get_by_entity_id(self, entity_id: UUID) -> BaseDocument | None: ...
    async def list_latest_active(self, skip: int = 0, limit: int = 100) -> list: ...
    async def update_by_entity_id(self, entity_id: UUID, data, user_id: str) -> BaseDocument: ...
    async def delete_by_entity_id(self, entity_id: UUID, user_id: str) -> None: ...
```

### SchemaRegistry

```python
class SchemaRegistry:
    def __init__(self, schema_dir: Path | None = None): ...
    def get_schema_names(self) -> list[str]: ...
    def get_schema(self, name: str, version: str = "latest") -> dict: ...
    def generate_document_model(self, name: str, version: str = "latest") -> type[BaseDocument]: ...
    def generate_create_model(self, name: str, version: str = "latest") -> type[BaseModel]: ...
    def generate_update_model(self, name: str, version: str = "latest") -> type[BaseModel]: ...
    def register_schema(self, name: str, schema: dict, version: str | None = None) -> None: ...
    @classmethod
    def reset(cls) -> None: ...
```

## Services

```python
class GenericCreateService:
    def __init__(self, repository): ...
    async def execute(self, data, user_id: str) -> BaseDocument: ...

class GenericGetService:
    def __init__(self, repository): ...
    async def execute(self, entity_id: UUID) -> BaseDocument | None: ...

class GenericListService:
    def __init__(self, repository): ...
    async def execute(self, skip: int = 0, limit: int = 100) -> list: ...

class GenericUpdateService:
    def __init__(self, repository): ...
    async def execute(self, entity_id: UUID, data, user_id: str) -> BaseDocument: ...

class GenericDeleteService:
    def __init__(self, repository): ...
    async def execute(self, entity_id: UUID, user_id: str) -> None: ...
```

## Container

### EntityRegistration

```python
@dataclass
class EntityRegistration:
    schema_name: str
    document_model: type[BaseDocument]
    create_model: type[BaseModel]
    update_model: type[BaseModel]
    repository_class: type
    services: dict[str, type]
    controller_factory: Any | None = None
    handler_overrides: dict[str, Any] = field(default_factory=dict)
```

### EntityContainer

```python
class EntityContainer:
    def __init__(self, models_module=None, repositories_module=None, services_module=None, controllers_module=None): ...
    def resolve_all(self, schema_names: list[str]) -> None: ...
    def get(self, schema_name: str) -> EntityRegistration: ...
    def get_all(self) -> dict[str, EntityRegistration]: ...
```

### Factory Functions

```python
def init_container(schema_names, models_module=None, ...) -> EntityContainer: ...
def get_container() -> EntityContainer: ...
```

## Persistence

```python
class VersionedMongoCRUD:
    def __init__(self, db, collection_name, document_model, create_model, update_model): ...
    async def create(self, data, user_id) -> BaseDocument: ...
    async def get_by_entity_id(self, entity_id) -> BaseDocument | None: ...
    async def list_latest_active(self, skip=0, limit=100) -> list: ...
    async def update_by_entity_id(self, entity_id, data, user_id) -> BaseDocument: ...
    async def delete_by_entity_id(self, entity_id, user_id) -> None: ...

class CRUDFactory:
    @classmethod
    def create_crud_instance(cls, db, schema_name, version="latest") -> VersionedMongoCRUD: ...

class RepositoryFactory:
    @classmethod
    def create(cls, schema_name, doc_model, create_model, update_model) -> type: ...
```

## Database

```python
class DatabaseManager:
    def __init__(self, mongo_uri: str | None = None, database_name: str | None = None): ...
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    def get_database(self) -> AsyncIOMotorDatabase: ...
```

## Lifecycle Events

```python
LIFECYCLE_EVENTS = frozenset({
    "pre_create",  "post_create",
    "pre_get",     "post_get",
    "pre_list",    "post_list",
    "pre_update",  "post_update",
    "pre_delete",  "post_delete",
})
```
