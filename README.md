# slip-stream

JSON Schema-driven hexagonal backend framework for FastAPI + MongoDB.

Drop a JSON schema file, get full CRUD API endpoints with versioned MongoDB persistence. No boilerplate, fully overridable at every layer.

## Install

```bash
pip install slip-stream
```

## Quick Start

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

**4. Visit `/docs`** — 5 CRUD endpoints are auto-generated:

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/pet/` | Create a pet |
| GET | `/api/v1/pet/` | List all pets |
| GET | `/api/v1/pet/{entity_id}` | Get pet by ID |
| PATCH | `/api/v1/pet/{entity_id}` | Update a pet |
| DELETE | `/api/v1/pet/{entity_id}` | Soft-delete a pet |

Add more schemas — each gets its own set of endpoints automatically.

## How It Works

```
JSON Schema file (schemas/pet.json)
  ↓  SchemaRegistry loads at startup
Pydantic models (PetDocument, PetCreate, PetUpdate)
  ↓  CRUDFactory / RepositoryFactory
VersionedMongoCRUD (append-only versioned persistence)
  ↓  EndpointFactory
FastAPI CRUD endpoints (POST, GET, GET list, PATCH, DELETE)
```

## Versioned Document Pattern

Every write creates a new document version — no in-place mutations:

- **Create**: `record_version=1`, new `entity_id`
- **Update**: copies doc, increments `record_version`, applies changes
- **Delete**: creates tombstone with `deleted_at` set
- **Get**: returns latest active version
- **List**: aggregation pipeline returns latest active version per entity

## Declarative Decorators

Use `SlipStreamRegistry` to add guards, validators, transforms, and handler overrides — no file naming conventions needed:

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

@registry.on("post_create")
async def audit(ctx: RequestContext) -> None:
    print(f"Created {ctx.schema_name} {ctx.entity_id}")

slip = SlipStream(app=app, schema_dir=Path("./schemas"), registry=registry)
```

See [docs/decorators.md](docs/decorators.md) for the full reference.

## Filters

ASGI middleware filters for auth, content negotiation, response envelopes, and field projection:

```python
from slip_stream import (
    SlipStream,
    AuthFilter,
    ContentNegotiationFilter,
    ResponseEnvelopeFilter,
    FieldProjectionFilter,
)

slip = SlipStream(
    app=app,
    schema_dir=Path("./schemas"),
    filters=[
        AuthFilter(authenticate=my_auth_fn),
        ContentNegotiationFilter(),        # JSON/YAML/XML via Accept header
        ResponseEnvelopeFilter(),          # Wraps in {data, meta}
        FieldProjectionFilter(),           # ?fields=name,status
    ],
    structured_errors=True,
)
```

See [docs/filters.md](docs/filters.md) for the full reference.

## Override System

The `EntityContainer` supports 4-layer overrides for when you need custom logic:

```python
slip = SlipStream(
    app=app,
    schema_dir=Path("./schemas"),
    models_module="myapp.models",           # Hand-crafted Pydantic models
    repositories_module="myapp.repos",      # Custom persistence logic
    services_module="myapp.services",       # Custom business logic
    controllers_module="myapp.controllers", # Custom endpoint routing
)
```

Override any layer by dropping a module with the right naming convention. Everything else falls back to auto-generation. See [docs/overrides.md](docs/overrides.md).

## Documentation

Full documentation: [docs/index.md](docs/index.md)

| Guide | Description |
|-------|-------------|
| [Getting Started](docs/getting-started.md) | Step-by-step tutorial |
| [Schemas](docs/schemas.md) | JSON Schema format reference |
| [Decorators](docs/decorators.md) | @handler, @guard, @validate, @transform, @on |
| [Events](docs/events.md) | EventBus lifecycle hooks |
| [Filters](docs/filters.md) | Filter chain and built-in filters |
| [Overrides](docs/overrides.md) | Module-based override system |
| [RequestContext](docs/context.md) | The unified context object |
| [API Reference](docs/api-reference.md) | All exported symbols |

## Dependencies

- Python ^3.12
- FastAPI >=0.115.0
- Motor ^3.4.0 (async MongoDB driver)
- Pydantic v2

## License

MIT
