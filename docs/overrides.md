# Module-Based Override System

The `EntityContainer` supports a 4-layer override system. Each layer can be customized per schema by dropping a module with the right naming convention. Everything else falls back to auto-generation.

## Overview

```python
slip = SlipStream(
    app=app,
    schema_dir=Path("./schemas"),
    models_module="myapp.models",           # Layer 1: Pydantic models
    repositories_module="myapp.repos",      # Layer 2: Persistence
    services_module="myapp.services",       # Layer 3: Business logic
    controllers_module="myapp.controllers", # Layer 4: Endpoints
)
```

## Resolution Order

For a schema named `widget`:

```
1. Models     → myapp.models.Widget, WidgetCreate, WidgetUpdate
2. Repository → myapp.repos.widget_repository.WidgetRepository
3. Services   → myapp.services.widget_service.WidgetCreateService, etc.
4. Controller → myapp.controllers.widget_controller.create_handler, etc.
```

Each layer falls back independently. You can override just the model without touching services, or just the service for one operation.

## Layer 1: Models

Override the Pydantic models generated from JSON Schema.

**Convention:** Export `{Pascal}`, `{Pascal}Create`, `{Pascal}Update` from the `models_module`.

```python
# myapp/models.py
from pydantic import BaseModel, field_validator
from slip_stream import BaseDocument

class Widget(BaseDocument):
    """Hand-crafted model with custom validation."""
    name: str
    color: str = "blue"
    weight: float = 0.0

    @field_validator("weight")
    @classmethod
    def weight_positive(cls, v):
        if v < 0:
            raise ValueError("Weight must be positive")
        return v

class WidgetCreate(BaseModel):
    name: str
    color: str = "blue"
    weight: float = 0.0

class WidgetUpdate(BaseModel):
    name: str | None = None
    color: str | None = None
    weight: float | None = None
```

You can override just the document model and let Create/Update be auto-generated, or vice versa.

## Layer 2: Repository

Override the persistence layer for a schema.

**Convention:** Export `{Pascal}Repository` from `{repositories_module}.{schema_name}_repository`.

```python
# myapp/repos/widget_repository.py
from slip_stream import RepositoryPort

class WidgetRepository(RepositoryPort):
    """Custom repository with additional queries."""

    def __init__(self, db):
        self.db = db
        self.collection = db["widget"]

    async def get_by_entity_id(self, entity_id):
        doc = await self.collection.find_one(
            {"entity_id": str(entity_id), "deleted_at": None},
            sort=[("record_version", -1)],
        )
        return doc

    async def create(self, data, user_id):
        # Custom create logic
        ...

    # Implement all RepositoryPort methods
```

## Layer 3: Services

Override business logic for specific operations.

**Convention:** Export `{Pascal}{Op}Service` from `{services_module}.{schema_name}_service`.

Operations: `Create`, `Get`, `List`, `Update`, `Delete`.

```python
# myapp/services/widget_service.py
class WidgetCreateService:
    """Custom create service with email notification."""

    def __init__(self, repository):
        self.repository = repository

    async def execute(self, data, user_id):
        result = await self.repository.create(data=data, user_id=user_id)
        await send_notification(f"Widget created: {result.name}")
        return result

# Other operations (Get, List, Update, Delete) fall back to generics
```

## Layer 4: Controllers

Override endpoint handlers for specific operations.

**Convention:** Export `{op}_handler` functions from `{controllers_module}.{schema_name}_controller`.

```python
# myapp/controllers/widget_controller.py
from slip_stream import RequestContext

async def create_handler(ctx: RequestContext):
    """Custom create with extra logic."""
    ctx.data.name = ctx.data.name.strip()
    # Access the registration's repository and service
    # Or implement entirely custom logic
    return {"name": ctx.data.name, "custom": True}

async def get_handler(ctx: RequestContext):
    """Custom get — ctx.entity is already hydrated."""
    return {**ctx.entity.model_dump(), "viewed_by": ctx.current_user["id"]}
```

### Full Router Override

For complete control, export `create_router` — it receives the `EntityRegistration` and returns an `APIRouter`:

```python
# myapp/controllers/widget_controller.py
from fastapi import APIRouter

def create_router(registration):
    """Complete custom router replacing all auto-generated endpoints."""
    router = APIRouter()

    @router.get("/")
    async def custom_list():
        return {"message": "fully custom"}

    return router
```

## Decorators vs. Module Overrides

| Feature | Registry Decorators | Module Overrides |
|---------|-------------------|-----------------|
| Location | Anywhere in codebase | Fixed module path |
| Discovery | Explicit registration | Auto-discovered by convention |
| Granularity | Per-operation handlers + hooks | Per-layer (model/repo/service/controller) |
| Use case | Guards, validators, transforms, hooks | Deep structural overrides |

**Recommendation:** Use registry decorators (`@handler`, `@guard`, `@validate`, `@transform`) for most customization. Use module overrides when you need to replace the Pydantic model, repository, or service class itself.

Both systems work together — module-based handler overrides and registry-based `@handler` decorators populate the same `handler_overrides` dict. Registry decorators take precedence (applied after module discovery).
