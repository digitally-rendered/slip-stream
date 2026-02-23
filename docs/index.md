# slip-stream Documentation

JSON Schema-driven hexagonal backend framework for FastAPI + MongoDB.

Drop a JSON schema file, get full CRUD API endpoints with versioned MongoDB persistence. No boilerplate, fully overridable at every layer.

## Install

```bash
pip install slip-stream

# Optional format support:
pip install slip-stream[yaml]    # YAML request/response
pip install slip-stream[xml]     # XML request/response
pip install slip-stream[all]     # Both
```

## Minimal Example

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

app = FastAPI(title="My API", lifespan=lifespan)
slip.app = app
```

Place a JSON schema in `./schemas/widget.json` and you get 5 endpoints:

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/widget/` | Create |
| GET | `/api/v1/widget/` | List all |
| GET | `/api/v1/widget/{entity_id}` | Get by ID |
| PATCH | `/api/v1/widget/{entity_id}` | Update |
| DELETE | `/api/v1/widget/{entity_id}` | Soft-delete |

## Documentation Index

| Guide | Description |
|-------|-------------|
| [Getting Started](getting-started.md) | Step-by-step tutorial from zero to custom logic |
| [JSON Schemas](schemas.md) | Schema format, audit fields, types |
| [Decorators](decorators.md) | `@handler`, `@guard`, `@validate`, `@transform`, `@on` |
| [Events](events.md) | EventBus lifecycle hooks |
| [Filters](filters.md) | ASGI filter chain, content negotiation, envelope, projection |
| [Overrides](overrides.md) | Module-based 4-layer override system |
| [RequestContext](context.md) | The unified context object |
| [API Reference](api-reference.md) | All exported symbols |

## Architecture

```
┌─────────────────────────────────────────────┐
│              Driving Adapters                │
│  Filters (ASGI) → FastAPI Endpoints         │
│  Decorators (Registry) → Handler Overrides  │
├─────────────────────────────────────────────┤
│              Core Domain                     │
│  RequestContext ← EventBus (Lifecycle Hooks)│
│  Schema Registry → Pydantic Models          │
│  Ports → Generic Services                   │
├─────────────────────────────────────────────┤
│              Driven Adapters                 │
│  MongoDB (Motor) → VersionedMongoCRUD       │
│  Append-only versioned document storage     │
└─────────────────────────────────────────────┘
```
