# Backend Patterns (slip-stream)

## Schema-Driven Entity Pattern (preferred)
The default way to create a new entity:

1. **Write a JSON schema** in your project's schemas directory
2. **Start the server** — SchemaRegistry auto-discovers and registers the schema
3. **Endpoints auto-appear** at `{api_prefix}/{entity-name}/` with full CRUD

### JSON Schema Requirements
Every schema SHOULD include these BaseDocument-compatible fields (they get auto-skipped during model generation but exist for documentation):
```json
{
  "title": "EntityName",
  "version": "1.0.0",
  "type": "object",
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
    "deleted_by": { "type": "string" }
  }
}
```

## BaseDocument Pattern
All persisted entities extend `slip_stream.core.domain.base.BaseDocument`:
- `id` (UUID): unique per document version (each version gets a new `id`)
- `entity_id` (UUID): stable across all versions of a logical entity
- `record_version` (int): increments with each new version
- `schema_version` (str): tracks schema evolution
- Audit fields: `created_at`, `updated_at`, `deleted_at`, `created_by`, `updated_by`, `deleted_by`
- `normalize_uuids` model validator handles BSON Binary -> UUID conversion

## Versioned Document Pattern (Append-Only)
**NEVER mutate a document in MongoDB. Always create a new version.**
- `create()`: new document, `record_version=1`, new `entity_id`
- `update()`: copies current doc, increments `record_version`, new `id`, applies changes
- `delete()`: creates tombstone record with `deleted_at` set
- `get_by_entity_id()`: finds latest version by `record_version` desc, filters soft-deleted
- `list_latest_active()`: aggregation pipeline groups by `entity_id`, takes latest, filters deleted

## BSON UUID Handling
```python
from bson.binary import Binary, UuidRepresentation

# Writing to MongoDB
bson_uuid = Binary(uuid_value.bytes, UuidRepresentation.PYTHON_LEGACY)

# Reading from MongoDB — handled by BaseDocument.normalize_uuids validator
```

## Override System
The EntityContainer supports 4-layer overrides. To override any layer for a specific entity, create the appropriately named module:
- **Models**: `{models_module}.{PascalName}` / `{PascalName}Create` / `{PascalName}Update`
- **Repository**: `{repositories_module}.{name}_repository.{PascalName}Repository`
- **Services**: `{services_module}.{name}_service.{PascalName}{Op}Service`
- **Controller**: `{controllers_module}.{name}_controller.create_router`

## Error Handling Pattern
```python
from fastapi import HTTPException

# 404 for missing entities
if not result:
    raise HTTPException(status_code=404, detail=f"{entity_type} not found")

# 400 for validation errors (Pydantic handles most automatically)
```
