# Hexagonal Architecture (slip-stream)

## Layer Structure
slip-stream uses a simplified hexagonal architecture adapted for schema-driven auto-generation:

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

## Import Rules
- **Core MUST NOT import from Adapters.** `slip_stream/core/` must never import from `slip_stream/adapters/`.
- **Adapters MAY import from Core.** Adapters use core domain models, schemas, and base classes.
- **Adapters MUST NOT import from each other.** API adapters must not directly import persistence adapters.
- **Dependencies flow inward.** External frameworks (FastAPI, Motor) stay in adapter layer.

## Schema-Driven Note
slip-stream's hex architecture is NOT classic DDD. The schema-driven approach intentionally merges some domain + infrastructure concerns:
- JSON schemas ARE the domain definition (not separate domain entities)
- SchemaRegistry generates both domain models AND persistence models from the same schemas
- This is a pragmatic trade-off for rapid development

## Directory Mapping
| Hex Layer | Directory | Responsibility |
|---|---|---|
| Driving Adapter | `slip_stream/adapters/api/` | HTTP routes, request/response, OpenAPI |
| Core Domain | `slip_stream/core/domain/` | Base classes |
| Core Service | `slip_stream/core/schema/` | SchemaRegistry (schema -> model generation) |
| Core Port | `slip_stream/core/ports/` | RepositoryPort protocol |
| Core Use Cases | `slip_stream/core/services/` | Generic CRUD services |
| Driven Adapter | `slip_stream/adapters/persistence/db/` | MongoDB CRUD, BSON conversion |
| Configuration | `slip_stream/database.py`, `slip_stream/container.py` | DB connection, DI container |
