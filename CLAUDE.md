# slip-stream

JSON Schema-driven hexagonal backend framework for FastAPI + MongoDB.

## Critical

- Always use `poetry run` for Python commands.
- **NEVER modify the source project at `/Users/draw/development/vo-comp`.**
- This is a **library** — it has no domain-specific logic. Keep it generic.

## What slip-stream Does

Drop a JSON schema file in your schemas directory. On startup, slip-stream auto-generates:
1. **Pydantic models** (Document, Create, Update variants) from JSON Schema properties
2. **MongoDB CRUD operations** via VersionedMongoCRUD (append-only versioning)
3. **FastAPI endpoints** (POST, GET, GET list, PATCH, DELETE) via EndpointFactory

## Architecture: Hexagonal (Schema-Driven)

```
┌─────────────────────────────────────────┐
│              Driving Adapters            │
│  slip_stream/adapters/api/              │
│  (FastAPI routers, endpoint factory)    │
├─────────────────────────────────────────┤
│              Core Domain                 │
│  slip_stream/core/domain/base.py        │
│  slip_stream/core/schema/registry.py    │
│  slip_stream/core/ports/repository.py   │
│  slip_stream/core/services/generic.py   │
├─────────────────────────────────────────┤
│              Driven Adapters             │
│  slip_stream/adapters/persistence/db/   │
│  (MongoDB via Motor, VersionedMongoCRUD)│
└─────────────────────────────────────────┘
```

**Import rules**: Core MUST NOT import from Adapters. Adapters MAY import from Core.

## Versioned Document Pattern (Append-Only)

NEVER mutate a document in MongoDB. Always create a new version:
- `create()` → `record_version=1`, new `entity_id`
- `update()` → copies doc, increments `record_version`, new `id`
- `delete()` → tombstone with `deleted_at` set
- All documents extend `BaseDocument` with audit fields

## Override System (Container)

The `EntityContainer` resolves entities with a 4-layer fallback:
1. **Models** — hand-crafted Pydantic models
2. **Repository** — custom persistence logic
3. **Services** — custom business logic
4. **Controller** — custom endpoint routing

Consumer passes their module paths; container discovers overrides by convention.

## Registry System (Decorator API)

`SlipStreamRegistry` is the declarative decorator layer:
- `@registry.handler(schema, op)` → populates `EntityRegistration.handler_overrides`
- `@registry.guard(schema, *ops)` → registers as `pre_*` EventBus hooks
- `@registry.validate(schema, *ops)` → registers as `pre_*` EventBus hooks (after guards)
- `@registry.transform(schema, *ops, when)` → `pre_*` or `post_*` hooks
- `@registry.on(event, schema)` → direct EventBus registration

`registry.apply(container, event_bus)` is called by `SlipStream.lifespan()` after `init_container()` and before endpoint registration.

Execution order within pre_* hooks: guards → validators → before-transforms → @on hooks.

## Filter Chain

Onion-model ASGI middleware. Order convention: auth=10, content_negotiation=50, envelope=90, projection=95.

## Key Files

| File | Purpose |
|------|---------|
| `slip_stream/__init__.py` | Public API exports |
| `slip_stream/app.py` | SlipStream app builder |
| `slip_stream/registry.py` | SlipStreamRegistry (decorator API) |
| `slip_stream/container.py` | EntityContainer with configurable module paths |
| `slip_stream/database.py` | MongoDB connection manager |
| `slip_stream/core/context.py` | RequestContext, HandlerOverride protocol |
| `slip_stream/core/events.py` | EventBus, HookError, lifecycle events |
| `slip_stream/core/domain/base.py` | BaseDocument (audit fields, UUID handling) |
| `slip_stream/core/schema/registry.py` | SchemaRegistry (JSON → Pydantic models) |
| `slip_stream/core/ports/repository.py` | RepositoryPort protocol |
| `slip_stream/core/services/generic.py` | Generic CRUD services |
| `slip_stream/adapters/api/endpoint_factory.py` | EndpointFactory (generates routes) |
| `slip_stream/adapters/api/error_handler.py` | Structured error handlers |
| `slip_stream/adapters/api/filters/` | Filter chain (auth, content neg, envelope, projection) |
| `slip_stream/adapters/persistence/db/generic_crud.py` | VersionedMongoCRUD |
| `docs/` | Comprehensive documentation with code exemplars |

## Tech Stack

- Python ^3.12
- FastAPI >=0.115.0
- Motor ^3.4.0 (async MongoDB)
- Pydantic v2
- Poetry (dependency management)

## Testing

- pytest + pytest-asyncio
- mongomock-motor for MongoDB mocking
- Run: `poetry run pytest tests/ -x -q`

## Commands

```bash
poetry install              # Install dependencies
poetry run pytest           # Run tests
poetry build                # Build package
poetry publish --dry-run    # Validate PyPI readiness
```
