# slip-stream

JSON Schema-driven hexagonal backend framework for FastAPI + MongoDB.

Drop a JSON schema file, get full CRUD API endpoints with versioned MongoDB persistence. No boilerplate, fully overridable at every layer.

## Features

- **Zero-boilerplate CRUD** — JSON Schema in, REST + GraphQL endpoints out
- **Versioned documents** — append-only persistence, no in-place mutations
- **Hexagonal architecture** — ports & adapters, swap persistence backends freely
- **Declarative decorators** — guards, validators, transforms, handler overrides
- **Channel-scoped hooks** — target REST, GraphQL, or both with `channel=`
- **ASGI filter chain** — auth, rate limiting, content negotiation, envelopes
- **Safe query DSL** — Hasura-style `where` filters, never exposes raw MongoDB
- **Schema versioning** — semver-based, multi-version coexistence
- **Hot schema reload** — file watcher detects changes, reloads without restart
- **Event streaming** — bridge EventBus to Kafka, SQS, NATS, Redis Pub/Sub
- **Audit trail** — automatic CRUD event logging with user tracking
- **Webhooks** — outbound HTTP with HMAC-SHA256 signing and retries
- **OPA/Rego policy** — inline, local Rego, or remote OPA policy evaluation
- **SQL support** — optional SQLAlchemy adapter (PostgreSQL, MySQL, SQLite)
- **SDK generation** — auto-generate typed Python API clients from schemas
- **CLI tooling** — `slip init`, `slip schema add`, `slip run`

## Install

```bash
pip install slip-stream
```

With optional extras:

```bash
pip install slip-stream[graphql]    # GraphQL (Strawberry)
pip install slip-stream[sql]       # SQLAlchemy RDBMS adapter
pip install slip-stream[remote]    # Remote schema registry (httpx)
pip install slip-stream[all]       # Everything
```

## Quick Start

### Using the CLI

```bash
slip init myproject
cd myproject
slip schema add pet
slip schema add order
slip run
```

### Manual Setup

**1. Create a JSON schema** (`schemas/pet.json`):

```json
{
  "title": "Pet",
  "version": "1.0.0",
  "type": "object",
  "required": ["name", "status"],
  "properties": {
    "id": { "type": "string", "format": "uuid" },
    "entity_id": { "type": "string", "format": "uuid" },
    "schema_version": { "type": "string", "default": "1.0.0" },
    "record_version": { "type": "integer", "default": 1 },
    "created_at": { "type": "string", "format": "date-time" },
    "updated_at": { "type": "string", "format": "date-time" },
    "deleted_at": { "type": "string", "format": "date-time" },
    "created_by": { "type": "string" },
    "updated_by": { "type": "string" },
    "deleted_by": { "type": "string" },
    "name": { "type": "string" },
    "status": { "type": "string", "default": "available" },
    "category": { "type": "string" },
    "tags": { "type": "array", "items": { "type": "string" } }
  }
}
```

**2. Create your app** (`main.py`):

```python
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from slip_stream import SlipStream

slip = SlipStream(
    app=FastAPI(),
    schema_dir=Path("./schemas"),
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with slip.lifespan():
        yield

app = FastAPI(title="Petstore API", lifespan=lifespan)
slip.app = app
```

**3. Run it:**

```bash
uvicorn main:app --reload
```

**4. Visit `/docs`** — 5 CRUD endpoints per schema:

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/pet/` | Create a pet |
| GET | `/api/v1/pet/` | List pets (with filtering and sorting) |
| GET | `/api/v1/pet/{entity_id}` | Get pet by ID |
| PATCH | `/api/v1/pet/{entity_id}` | Update a pet |
| DELETE | `/api/v1/pet/{entity_id}` | Soft-delete a pet |

## Architecture

```
JSON Schema files
  ↓  SchemaRegistry
Pydantic models (auto-generated)
  ↓  EntityContainer (DI)
RepositoryPort ←→ VersionedMongoCRUD | SQLRepository
  ↓  OperationExecutor (shared lifecycle)
EventBus hooks (pre/post) → AuditTrail, Webhooks, Streaming
  ↓  EndpointFactory | GraphQLFactory
FastAPI REST + Strawberry GraphQL endpoints
  ↑  FilterChainMiddleware
ASGI Filters (Auth, RateLimit, Envelope, Projection, Rego)
```

## Declarative Decorators

Use `SlipStreamRegistry` to add guards, validators, transforms, and handler overrides:

```python
from slip_stream import SlipStream, SlipStreamRegistry, HookError, RequestContext

registry = SlipStreamRegistry()

@registry.guard("pet", "delete")
async def admins_only(ctx: RequestContext) -> None:
    if ctx.current_user.get("role") != "admin":
        raise HookError(403, "Admin role required")

@registry.validate("pet", "create", "update")
async def validate_name(ctx: RequestContext) -> None:
    if ctx.data.name and len(ctx.data.name) < 2:
        raise HookError(422, "Name must be at least 2 characters")

@registry.transform("pet", "create", when="before")
async def normalize(ctx: RequestContext) -> None:
    ctx.data.name = ctx.data.name.strip()

@registry.handler("pet", "create", channel="graphql")
async def graphql_only_create(ctx: RequestContext) -> Any:
    """This handler only fires for GraphQL mutations, not REST."""
    ...

@registry.on("post_create")
async def audit(ctx: RequestContext) -> None:
    print(f"Created {ctx.schema_name} {ctx.entity_id}")

slip = SlipStream(app=app, schema_dir=Path("./schemas"), registry=registry)
```

## Query DSL

Hasura-style `where` filters with safe operator allowlisting (never exposes raw MongoDB):

```bash
# REST — JSON in query param
GET /api/v1/pet/?where={"status":{"_eq":"available"},"name":{"_like":"Max"}}&sort=-created_at,name

# Supported operators: _eq, _neq, _gt, _gte, _lt, _lte,
#   _in, _nin, _like, _ilike, _contains, _startswith, _endswith,
#   _exists, _is_null, _and, _or, _not
```

```graphql
# GraphQL — JSON scalar
query {
  pets(where: {status: {_eq: "available"}}, sort: "-created_at") {
    name
    status
  }
}
```

## Filters

ASGI middleware filters applied to all HTTP requests (REST and GraphQL):

```python
from slip_stream import (
    SlipStream,
    AuthFilter,
    ContentNegotiationFilter,
    ResponseEnvelopeFilter,
    FieldProjectionFilter,
    RateLimitFilter,
    RegoPolicyFilter,
)

slip = SlipStream(
    app=app,
    schema_dir=Path("./schemas"),
    filters=[
        RateLimitFilter(requests_per_window=100),  # 429 Too Many Requests
        AuthFilter(authenticate=my_auth_fn),
        ContentNegotiationFilter(),        # JSON/YAML/XML via Accept header
        ResponseEnvelopeFilter(),          # Wraps in {data, meta}
        FieldProjectionFilter(),           # ?fields=name,status
    ],
    structured_errors=True,
)
```

## Event Streaming

Bridge CRUD events to external messaging systems:

```python
from slip_stream import EventStreamBridge, InMemoryStream

# In-memory for testing
stream = InMemoryStream()
bridge = EventStreamBridge(adapters=[stream], topic_prefix="myapp")
bridge.register(event_bus)

# Events published as: myapp.pet.create, myapp.pet.update, myapp.pet.delete
```

## Audit Trail

Automatic CRUD event logging:

```python
from slip_stream import AuditTrail

audit = AuditTrail(in_memory=True)  # or with MongoDB collection
audit.register(event_bus)

# Query audit history
entries = audit.get_history("pet", entity_id="abc-123")
activity = audit.get_user_activity("user-1")
```

## Webhooks

Outbound HTTP webhooks with HMAC-SHA256 signing:

```python
from slip_stream import WebhookDispatcher

dispatcher = WebhookDispatcher(in_memory=True)
dispatcher.add(url="https://example.com/hook", schema_name="pet", events=["create", "update"])
dispatcher.register(event_bus)
```

## SQL Support

Optional SQLAlchemy adapter for RDBMS backends:

```python
from slip_stream import SQLRepository, build_table_from_schema
import sqlalchemy as sa

metadata = sa.MetaData()
table = build_table_from_schema("pet", schema_dict, metadata)

repo = SQLRepository(session, table)
doc = await repo.create(data, user_id="user-1")
```

## SDK Generation

Auto-generate typed Python API clients:

```python
from slip_stream import generate_sdk

code = generate_sdk(
    schemas={"pet": pet_schema, "order": order_schema},
    base_url="http://localhost:8000/api/v1",
)
Path("client.py").write_text(code)

# Generated client usage:
# async with SlipStreamClient() as client:
#     pet = await client.create_pet(PetCreate(name="Max", status="available"))
#     pets = await client.list_pets(where={"status": {"_eq": "available"}})
```

## Policy Engine

OPA/Rego policy evaluation — inline, local, or remote:

```python
from slip_stream import InlinePolicy

policy = InlinePolicy()

@policy.rule("pet", "delete")
async def check_ownership(input_data: dict) -> dict:
    return {"allow": input_data["user"]["id"] == input_data["resource"]["created_by"]}

result = await policy.evaluate("pet", "delete", input_data)
```

## Schema Versioning

Semver-based schema versioning with multi-version coexistence:

```
schemas/
  pet/
    1.0.0.json
    2.0.0.json
```

Request a specific version via header: `X-Schema-Version: 2.0.0`

## Hot Schema Reload

File watcher detects schema changes and reloads without restart:

```python
from slip_stream import SchemaWatcher

watcher = SchemaWatcher(schema_dir=Path("./schemas"), registry=schema_registry)
await watcher.start()
# Schemas reload automatically when files change
```

## Override System

The `EntityContainer` supports 4-layer overrides for custom logic:

```python
slip = SlipStream(
    app=app,
    schema_dir=Path("./schemas"),
    models_module="myapp.models",           # Custom Pydantic models
    repositories_module="myapp.repos",      # Custom persistence
    services_module="myapp.services",       # Custom business logic
    controllers_module="myapp.controllers", # Custom endpoints
)
```

## CLI

```bash
slip init myproject          # Scaffold a new project
slip schema add widget       # Add a new JSON Schema
slip schema list             # List discovered schemas
slip schema validate         # Validate all schemas
slip run                     # Start dev server with auto-reload
```

## API Documentation

- **REST**: Auto-generated OpenAPI docs at `/docs` (Swagger UI) and `/redoc`
- **GraphQL**: Interactive GraphQL playground at `/graphql`
- **Python API docs**: Generate with `pdoc slip_stream`

## Dependencies

**Required:**
- Python ^3.12
- FastAPI >=0.115.0
- Motor ^3.4.0 (async MongoDB driver)
- Pydantic v2

**Optional:**
- `strawberry-graphql` — GraphQL support
- `sqlalchemy` — SQL database support
- `httpx` — Remote schema registry, webhook delivery
- `pyyaml` — YAML content negotiation
- `xmltodict` — XML content negotiation
- `mcp` — Model Context Protocol server

## License

MIT
