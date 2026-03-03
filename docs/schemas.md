# JSON Schemas

slip-stream is driven entirely by JSON Schema files. Each file you drop into the configured `schema_dir` becomes a full set of CRUD endpoints with no additional code required.

## Schema Directory

The schema directory is passed to `SlipStream` at startup:

```python
from pathlib import Path
from fastapi import FastAPI
from slip_stream import SlipStream

slip = SlipStream(
    app=FastAPI(),
    schema_dir=Path("./schemas"),
)
```

At startup, slip-stream scans every `*.json` file in that directory, registers each schema, and generates the corresponding Pydantic models and FastAPI routes.

## File Naming and URL Paths

The filename (without the `.json` extension) becomes the schema name. The schema name is then converted to a kebab-case URL path segment.

| File | Schema name | API prefix |
|------|-------------|------------|
| `schemas/widget.json` | `widget` | `/api/v1/widget/` |
| `schemas/pet.json` | `pet` | `/api/v1/pet/` |
| `schemas/order_item.json` | `order_item` | `/api/v1/order-item/` |
| `schemas/blog_post.json` | `blog_post` | `/api/v1/blog-post/` |

Underscores in the schema name are replaced with hyphens in the URL path. The schema name itself (with underscores) is used internally for model class names, collection names, and decorator targeting.

Each registered schema produces five endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/{name}/` | Create a new document |
| `GET` | `/api/v1/{name}/` | List all documents |
| `GET` | `/api/v1/{name}/{entity_id}` | Get the latest version of a document by entity ID |
| `PATCH` | `/api/v1/{name}/{entity_id}` | Partially update a document |
| `DELETE` | `/api/v1/{name}/{entity_id}` | Soft-delete a document |

## Schema Structure

Schema files are standard JSON Schema objects. slip-stream recognizes the following top-level keys:

| Key | Required | Description |
|-----|----------|-------------|
| `title` | Recommended | Human-readable name. Used in OpenAPI documentation. |
| `version` | Recommended | Semantic version string (e.g. `"1.0.0"`). Defaults to `"1.0.0"` if omitted. |
| `type` | Yes | Must be `"object"`. |
| `properties` | Yes | Field definitions (see [Field Types](#field-types) below). |
| `required` | No | Array of field names that must be present on creation. |

## Audit Fields

The following fields are reserved by the framework. They are managed automatically and must not be included in create or update requests. You may include them in `properties` for documentation purposes, but the framework will ignore them during model generation for input models.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` (uuid) | Unique identifier for each document version. A new `id` is assigned on every write. |
| `entity_id` | `string` (uuid) | Stable logical identifier. Remains the same across all versions of a document. |
| `schema_version` | `string` | Records the schema version that was active when the document was written. |
| `record_version` | `integer` | Monotonically incrementing counter. Starts at `1`, increments on each update. |
| `created_at` | `string` (date-time) | UTC timestamp set when the entity is first created. Never changes. |
| `updated_at` | `string` (date-time) | UTC timestamp updated on every write. |
| `deleted_at` | `string` (date-time) | Set to a non-null timestamp when the entity is soft-deleted. `null` otherwise. |
| `created_by` | `string` | Identifier of the user who created the entity. |
| `updated_by` | `string` | Identifier of the user who performed the most recent update. |
| `deleted_by` | `string` | Identifier of the user who soft-deleted the entity. |

Audit fields are sourced from `BaseDocument`, the Pydantic base class that all generated document models extend. Including audit field names in your schema's `required` array has no effect — they are always excluded from the Create model.

## Field Types

slip-stream maps JSON Schema types to Python types when generating Pydantic models. The supported mappings are:

| JSON Schema | Extra constraint | Python / Pydantic type |
|-------------|-----------------|------------------------|
| `"type": "string"` | — | `str` |
| `"type": "string"` | `"format": "uuid"` | `uuid.UUID` |
| `"type": "string"` | `"format": "date-time"` | `datetime` |
| `"type": "integer"` | — | `int` |
| `"type": "number"` | — | `float` |
| `"type": "boolean"` | — | `bool` |
| `"type": "array"` | `"items": { "type": "..." }` | `List[T]` (where `T` is the resolved item type) |
| `"type": "object"` | — | `Dict[str, Any]` |

Array items are resolved recursively, so `"items": { "type": "string", "format": "uuid" }` produces `List[uuid.UUID]`.

Fields with a `"default"` value in the schema use that value as the Pydantic field default.

## Generated Pydantic Models

For each schema, three Pydantic models are generated at startup:

### Document Model

The full representation of a persisted document. Extends `BaseDocument`, which provides all audit fields. Used for reading from and writing to MongoDB.

- Class name: `{TitleCaseName}Document` (e.g., `PetDocument`, `OrderItemDocument`)
- Contains all domain fields from `properties`, plus all audit fields inherited from `BaseDocument`
- Required domain fields have no default; optional domain fields default to `None`
- Fields that specify a `"default"` in the schema use that value

### Create Model

The model used to validate the request body for `POST` requests. Audit fields are excluded entirely — they cannot be supplied by the caller.

- Class name: `{TitleCaseName}Create` (e.g., `PetCreate`, `OrderItemCreate`)
- Contains only domain fields (audit fields omitted)
- Fields listed in `"required"` are mandatory (no default)
- Fields not in `"required"` are optional and default to `None`, or to the `"default"` value if one is specified in the schema

### Update Model

The model used to validate the request body for `PATCH` requests. All fields are optional to support partial updates — only the fields present in the request body are applied.

- Class name: `{TitleCaseName}Update` (e.g., `PetUpdate`, `OrderItemUpdate`)
- Contains only domain fields (audit fields omitted)
- Every field is `Optional[T]` with a default of `None`, regardless of the `"required"` array

## Schema Versioning

The `"version"` field at the root of the schema is stored as `schema_version` on every document written while that version is active. This lets you track which schema definition produced a given document and migrate data incrementally.

When multiple versions of the same schema exist, the registry resolves `"latest"` by sorting the version strings and selecting the last one. You can request a specific version programmatically:

```python
from slip_stream.core.schema.registry import SchemaRegistry

registry = SchemaRegistry()
schema = registry.get_schema("pet", version="2.0.0")
model  = registry.generate_document_model("pet", version="2.0.0")
```

## Full Annotated Example

The following schema defines a `pet` resource. Save it as `schemas/pet.json` and slip-stream will expose it at `/api/v1/pet/`.

```json
{
  "title": "Pet",
  "version": "1.0.0",
  "type": "object",

  "required": ["name", "status"],

  "properties": {

    "id":             { "type": "string", "format": "uuid" },
    "entity_id":      { "type": "string", "format": "uuid" },
    "schema_version": { "type": "string", "default": "1.0.0" },
    "record_version": { "type": "integer", "default": 1 },
    "created_at":     { "type": "string", "format": "date-time" },
    "updated_at":     { "type": "string", "format": "date-time" },
    "deleted_at":     { "type": "string", "format": "date-time" },
    "created_by":     { "type": "string" },
    "updated_by":     { "type": "string" },
    "deleted_by":     { "type": "string" },

    "name": {
      "type": "string"
    },

    "status": {
      "type": "string",
      "default": "available"
    },

    "category": {
      "type": "string"
    },

    "tags": {
      "type": "array",
      "items": { "type": "string" }
    },

    "age_months": {
      "type": "integer"
    },

    "weight_kg": {
      "type": "number"
    },

    "is_vaccinated": {
      "type": "boolean",
      "default": false
    },

    "metadata": {
      "type": "object"
    }
  }
}
```

### What this schema produces

**Endpoints**

```
POST   /api/v1/pet/
GET    /api/v1/pet/
GET    /api/v1/pet/{entity_id}
PATCH  /api/v1/pet/{entity_id}
DELETE /api/v1/pet/{entity_id}
```

**PetCreate** — required fields `name` and `status` must be provided; `status` has a default of `"available"` so it can be omitted; all other domain fields are optional:

```python
class PetCreate(BaseModel):
    name:          str
    status:        str            = "available"
    category:      Optional[str]  = None
    tags:          Optional[List[str]] = None
    age_months:    Optional[int]  = None
    weight_kg:     Optional[float] = None
    is_vaccinated: Optional[bool] = False
    metadata:      Optional[Dict[str, Any]] = None
```

**PetUpdate** — every field is optional for partial updates:

```python
class PetUpdate(BaseModel):
    name:          Optional[str]  = None
    status:        Optional[str]  = None
    category:      Optional[str]  = None
    tags:          Optional[List[str]] = None
    age_months:    Optional[int]  = None
    weight_kg:     Optional[float] = None
    is_vaccinated: Optional[bool] = None
    metadata:      Optional[Dict[str, Any]] = None
```

**PetDocument** — extends `BaseDocument`; all audit fields are inherited:

```python
class PetDocument(BaseDocument):
    # Inherited from BaseDocument:
    # id:             uuid.UUID
    # entity_id:      uuid.UUID
    # schema_version: str
    # record_version: int
    # created_at:     datetime
    # updated_at:     datetime
    # deleted_at:     Optional[datetime]
    # created_by:     Optional[str]
    # updated_by:     Optional[str]
    # deleted_by:     Optional[str]

    name:          str
    status:        str            = "available"
    category:      Optional[str]  = None
    tags:          Optional[List[str]] = None
    age_months:    Optional[int]  = None
    weight_kg:     Optional[float] = None
    is_vaccinated: Optional[bool] = False
    metadata:      Optional[Dict[str, Any]] = None
```

### Example requests

Create a pet (only required fields):

```bash
curl -X POST http://localhost:8000/api/v1/pet/ \
  -H "Content-Type: application/json" \
  -d '{"name": "Buddy"}'
```

Create a pet (all fields):

```bash
curl -X POST http://localhost:8000/api/v1/pet/ \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Buddy",
    "status": "available",
    "category": "dog",
    "tags": ["friendly", "vaccinated"],
    "age_months": 18,
    "weight_kg": 12.5,
    "is_vaccinated": true
  }'
```

Partial update (only the fields you want to change):

```bash
curl -X PATCH http://localhost:8000/api/v1/pet/550e8400-e29b-41d4-a716-446655440000 \
  -H "Content-Type: application/json" \
  -d '{"status": "adopted"}'
```
