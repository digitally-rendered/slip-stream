# Getting Started

This tutorial walks through building a complete API from scratch with slip-stream.

## 1. Install

```bash
pip install slip-stream
# or with Poetry:
poetry add slip-stream
```

## 2. Create a Schema

Create `schemas/pet.json`:

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

## 3. Create the App

Create `main.py`:

```python
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from slip_stream import SlipStream

SCHEMAS_DIR = Path(__file__).parent / "schemas"

slip = SlipStream(
    app=FastAPI(),
    schema_dir=SCHEMAS_DIR,
    api_prefix="/api/v1",
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with slip.lifespan():
        yield

app = FastAPI(
    title="Petstore API",
    version="0.1.0",
    lifespan=lifespan,
)
slip.app = app
```

## 4. Run It

```bash
uvicorn main:app --reload
```

Visit `http://localhost:8000/docs` to see 5 auto-generated endpoints for `pet`.

## 5. Add More Schemas

Drop `schemas/order.json` and restart. New endpoints appear automatically:

```
POST   /api/v1/order/
GET    /api/v1/order/
GET    /api/v1/order/{entity_id}
PATCH  /api/v1/order/{entity_id}
DELETE /api/v1/order/{entity_id}
```

## 6. Add Custom Logic with Decorators

Use `SlipStreamRegistry` to add guards, validators, and hooks:

```python
from slip_stream import SlipStream, SlipStreamRegistry, HookError, RequestContext

registry = SlipStreamRegistry()

# Block non-admin users from deleting pets
@registry.guard("pet", "delete")
async def admins_only(ctx: RequestContext) -> None:
    if ctx.current_user.get("role") != "admin":
        raise HookError(403, "Admin role required")

# Validate pet names
@registry.validate("pet", "create", "update")
async def validate_name(ctx: RequestContext) -> None:
    if ctx.data.name and len(ctx.data.name) < 2:
        raise HookError(422, "Pet name must be at least 2 characters")

# Normalize category to lowercase before saving
@registry.transform("pet", "create", "update", when="before")
async def normalize_category(ctx: RequestContext) -> None:
    if ctx.data.category:
        ctx.data.category = ctx.data.category.lower()

# Log all creates
@registry.on("post_create")
async def audit_create(ctx: RequestContext) -> None:
    print(f"Created {ctx.schema_name} {ctx.entity_id} by {ctx.current_user['id']}")

slip = SlipStream(
    app=FastAPI(),
    schema_dir=SCHEMAS_DIR,
    registry=registry,
)
```

## 7. Add Response Formatting

Enable response envelopes and field projection:

```python
from slip_stream import (
    SlipStream,
    SlipStreamRegistry,
    ResponseEnvelopeFilter,
    FieldProjectionFilter,
)

slip = SlipStream(
    app=FastAPI(),
    schema_dir=SCHEMAS_DIR,
    registry=registry,
    filters=[
        ResponseEnvelopeFilter(),         # Wraps in {data, meta}
        FieldProjectionFilter(),          # Enables ?fields=name,status
    ],
    structured_errors=True,               # RFC 7807 error responses
)
```

Now list responses look like:

```json
{
  "data": [
    {"name": "Buddy", "status": "available"},
    {"name": "Max", "status": "adopted"}
  ],
  "meta": {
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "pagination": {
      "skip": 0,
      "limit": 100,
      "count": 2,
      "total_count": 42,
      "has_more": true
    }
  }
}
```

And `GET /api/v1/pet/?fields=name,status` returns only those fields.

## 8. Add Content Negotiation

Accept and return YAML or XML:

```bash
pip install slip-stream[all]
```

```python
from slip_stream import ContentNegotiationFilter

slip = SlipStream(
    app=FastAPI(),
    schema_dir=SCHEMAS_DIR,
    filters=[ContentNegotiationFilter()],
)
```

```bash
# Send YAML, receive YAML
curl -X POST http://localhost:8000/api/v1/pet/ \
  -H "Content-Type: application/yaml" \
  -H "Accept: application/yaml" \
  -d "name: Buddy\nstatus: available"
```

## 9. Health and Observability

Three operational endpoints are auto-mounted by `SlipStream.lifespan()` — no configuration needed:

```
GET /health       → {"status": "healthy"}
GET /ready        → {"status": "ready", "checks": {"database": true, "schemas": true}}
GET /_topology    → {schemas, filters, config}
```

- `/health` — liveness probe, always 200
- `/ready` — readiness probe, checks DB connectivity and schema registry (200 or 503)
- `/_topology` — full app structure as JSON (schemas, filters, config) — no secrets exposed

See [Observability](observability.md) for full details.

## Next Steps

- [Errors Reference](errors.md) — RFC 7807 structured error format
- [Observability](observability.md) — health probes, topology endpoint
- [Decorators Reference](decorators.md) — full API for `@handler`, `@guard`, `@validate`, `@transform`, `@on`
- [Filters Reference](filters.md) — built-in filters and how to write custom ones
- [Override System](overrides.md) — module-based overrides for models, repositories, services, controllers
- [RequestContext](context.md) — what's available in `ctx`
- [MCP Server](mcp.md) — AI agent tools and SDK generation
