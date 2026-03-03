# RequestContext

`RequestContext` is the unified dataclass that flows through the entire request lifecycle — endpoint handlers, lifecycle hooks, guards, validators, transforms, and handler overrides all receive the same object.

## Fields

```python
@dataclass
class RequestContext:
    # Always populated
    request: Request              # Starlette/FastAPI Request object
    operation: OperationType      # "create" | "get" | "list" | "update" | "delete"
    schema_name: str              # e.g., "widget"

    # Populated before hooks/handlers
    entity_id: UUID | None        # Parsed from URL path (get/update/delete)
    entity: BaseModel | None      # Hydrated from DB (get/update/delete)
    data: BaseModel | None        # Parsed request body (create/update)
    current_user: dict | None     # Authenticated user
    db: Any                       # AsyncIOMotorDatabase instance

    # Populated after service execution
    response: Response | None     # Response object
    result: Any                   # Service execution result

    # List-specific
    skip: int = 0                 # Pagination offset
    limit: int = 100              # Pagination limit

    # Extension point
    extras: dict = {}             # Arbitrary key-value store
```

## What's Available When

### Create Operation

| Phase | entity_id | entity | data | result |
|-------|-----------|--------|------|--------|
| pre_create | - | - | Create model | - |
| @handler | - | - | Create model | - |
| post_create | - | - | Create model | Created entity |

### Get Operation

| Phase | entity_id | entity | data | result |
|-------|-----------|--------|------|--------|
| pre_get | UUID | Hydrated | - | - |
| @handler | UUID | Hydrated | - | - |
| post_get | UUID | Hydrated | - | Entity |

### List Operation

| Phase | entity_id | entity | data | skip/limit |
|-------|-----------|--------|------|------------|
| pre_list | - | - | - | From query params |
| @handler | - | - | - | From query params |
| post_list | - | - | - | From query params |

`result` is set to the list of entities after the handler.

### Update Operation

| Phase | entity_id | entity | data | result |
|-------|-----------|--------|------|--------|
| pre_update | UUID | Current version | Update model | - |
| @handler | UUID | Current version | Update model | - |
| post_update | UUID | Current version | Update model | Updated entity |

### Delete Operation

| Phase | entity_id | entity | data | result |
|-------|-----------|--------|------|--------|
| pre_delete | UUID | Current version | - | - |
| @handler | UUID | Current version | - | - |
| post_delete | UUID | Current version | - | - |

## Entity Hydration

For `get`, `update`, and `delete` operations, the framework automatically:

1. Parses `entity_id` from the URL path
2. Looks up the entity from the database via the repository
3. Returns **404** if not found
4. Sets `ctx.entity` to the fully hydrated, **typed Pydantic model**

This means handler overrides and hooks never need boilerplate entity lookup:

```python
@registry.handler("widget", "update")
async def custom_update(ctx: RequestContext) -> Any:
    # ctx.entity is already the current version from DB
    # ctx.data is the Update model with changes
    print(f"Updating {ctx.entity.name} to {ctx.data.name}")
    # ... custom logic ...
```

Invalid UUIDs return **400** automatically.

## Using extras

The `extras` dict is a free-form extension point for passing data between hooks:

```python
@registry.guard("widget", "create")
async def check_quota(ctx: RequestContext) -> None:
    quota = await get_user_quota(ctx.current_user["id"])
    ctx.extras["remaining_quota"] = quota
    if quota <= 0:
        raise HookError(429, "Quota exceeded")

@registry.on("post_create", schema_name="widget")
async def decrement_quota(ctx: RequestContext) -> None:
    remaining = ctx.extras.get("remaining_quota", 0)
    await update_quota(ctx.current_user["id"], remaining - 1)
```

## from_request() Factory

`RequestContext.from_request()` bridges the ASGI filter layer and the handler layer:

```python
ctx = RequestContext.from_request(
    request=request,
    operation="create",
    schema_name="widget",
    data=data,
    current_user=current_user,
    db=db,
)
```

If `current_user` is not provided but a `FilterContext` with a user exists on `request.state.filter_context` (set by `AuthFilter`), it is automatically used.

## HandlerOverride Protocol

Any async callable matching this signature satisfies the protocol:

```python
async def my_handler(ctx: RequestContext) -> Any:
    ...
```

It's `@runtime_checkable`, so you can verify at runtime:

```python
from slip_stream import HandlerOverride

assert isinstance(my_handler, HandlerOverride)
```
