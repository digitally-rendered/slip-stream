# Decorators Reference

`SlipStreamRegistry` provides five decorators that cover everything you need to customize CRUD behavior: replacing default service logic, enforcing authorization, performing cross-field validation, transforming data, and hooking into lifecycle events.

All decorators are collected at decoration time and applied during application startup — nothing runs until `SlipStream.lifespan()` calls `registry.apply()`. This means you can define your handlers in any module, import order, and at any scope.

## Import

```python
from slip_stream import SlipStream, SlipStreamRegistry, HookError, RequestContext
from pathlib import Path

registry = SlipStreamRegistry()
```

## The Five Decorators

| Decorator | Purpose | Runs as |
|-----------|---------|---------|
| `@registry.handler` | Replace the default service for one operation | The operation itself |
| `@registry.guard` | Authorization check | `pre_*` hook, first |
| `@registry.validate` | Cross-field validation | `pre_*` hook, after guards |
| `@registry.transform` | Mutate data before or after service | `pre_*` or `post_*` hook |
| `@registry.on` | Direct lifecycle event hook | `pre_*` or `post_*` hook |

---

## @registry.handler

Replaces the default service for a single CRUD operation on a schema. Your function receives a fully populated `RequestContext` and must return the result — an entity, a list, or `None` for delete.

```python
@registry.handler(schema, operation)
async def my_handler(ctx: RequestContext) -> Any:
    ...
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `schema` | `str` | Schema name, e.g. `"widget"` |
| `operation` | `str` | One of `"create"`, `"get"`, `"list"`, `"update"`, `"delete"` |

**What's in `ctx` at handler time:**

| Attribute | Available for |
|-----------|--------------|
| `ctx.data` | `create`, `update` |
| `ctx.entity_id` | `get`, `update`, `delete` |
| `ctx.entity` | `get`, `update`, `delete` — the hydrated current entity |
| `ctx.current_user` | All operations |
| `ctx.db` | All operations |
| `ctx.skip`, `ctx.limit` | `list` |

The handler is responsible for all persistence logic. If you want to run the default service alongside custom logic, you can retrieve it from `EntityRegistration`:

```python
from slip_stream import get_container

@registry.handler("widget", "create")
async def custom_create(ctx: RequestContext) -> Any:
    # Mutate the incoming data before persistence
    ctx.data.name = ctx.data.name.upper()

    # Delegate to the default create service
    registration = get_container().get("widget")
    repo = registration.repository_class(ctx.db)
    service = registration.services["create"](repo)
    return await service.execute(data=ctx.data, user_id=ctx.current_user["id"])
```

A fully custom implementation that bypasses the default service entirely:

```python
@registry.handler("widget", "list")
async def filtered_list(ctx: RequestContext) -> list:
    # Only return widgets owned by the current user
    registration = get_container().get("widget")
    repo = registration.repository_class(ctx.db)
    service = registration.services["list"](repo)
    results = await service.execute(skip=ctx.skip, limit=ctx.limit)
    user_id = ctx.current_user["id"]
    return [r for r in results if str(r.created_by) == user_id]
```

**Validation:** The `operation` argument is validated at decoration time. Passing an unknown operation name raises `ValueError` immediately.

---

## @registry.guard

Registers an authorization check that runs before any other hook for the specified operations. Raise `HookError` to abort the request and return an HTTP error response. The function signature returns `None` — returning a value has no effect.

```python
@registry.guard(schema, *operations)
async def my_guard(ctx: RequestContext) -> None:
    ...
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `schema` | `str` | Schema name, or `"*"` for all schemas |
| `*operations` | `str` | One or more of `"create"`, `"get"`, `"list"`, `"update"`, `"delete"` |

**Example — admin-only delete and update:**

```python
@registry.guard("widget", "delete", "update")
async def admins_only(ctx: RequestContext) -> None:
    if ctx.current_user.get("role") != "admin":
        raise HookError(403, "Admin role required")
```

**Example — ownership check on get:**

```python
@registry.guard("document", "get", "update", "delete")
async def owner_only(ctx: RequestContext) -> None:
    if ctx.entity is None:
        return  # entity not loaded yet for create/list
    owner_id = str(ctx.entity.created_by)
    if owner_id != ctx.current_user["id"] and ctx.current_user.get("role") != "admin":
        raise HookError(403, "You do not have access to this document")
```

**Example — global guard across all schemas:**

```python
@registry.guard("*", "create", "update", "delete")
async def require_auth(ctx: RequestContext) -> None:
    if not ctx.current_user:
        raise HookError(401, "Authentication required")
```

**Validation:** Each operation name is validated at decoration time. The schema name `"*"` is always accepted and means the guard applies to every schema.

---

## @registry.validate

Registers a cross-field validation hook. Validators run after guards but before transforms. This is the right place for business logic that the schema's field-level constraints cannot express — date range checks, conditional required fields, uniqueness that requires a DB lookup, and so on.

Raise `HookError` to reject the request. For `update` operations, `ctx.entity` contains the current persisted state, letting you compare incoming changes against what already exists.

```python
@registry.validate(schema, *operations)
async def my_validator(ctx: RequestContext) -> None:
    ...
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `schema` | `str` | Schema name, or `"*"` for all schemas |
| `*operations` | `str` | One or more of `"create"`, `"get"`, `"list"`, `"update"`, `"delete"` |

**Example — date range check:**

```python
@registry.validate("order", "create", "update")
async def check_date_range(ctx: RequestContext) -> None:
    if ctx.data.end_date < ctx.data.start_date:
        raise HookError(422, "end_date must be after start_date")
```

**Example — minimum name length:**

```python
@registry.validate("pet", "create", "update")
async def validate_name(ctx: RequestContext) -> None:
    if ctx.data.name and len(ctx.data.name) < 2:
        raise HookError(422, "Pet name must be at least 2 characters")
```

**Example — DB uniqueness check using `ctx.entity` to exclude self on update:**

```python
@registry.validate("widget", "create", "update")
async def unique_serial(ctx: RequestContext) -> None:
    if not ctx.data.serial_number:
        return
    registration = get_container().get("widget")
    repo = registration.repository_class(ctx.db)
    existing = await repo.find_by_serial(ctx.data.serial_number)
    if existing and (ctx.entity is None or existing.entity_id != ctx.entity.entity_id):
        raise HookError(409, f"Serial number '{ctx.data.serial_number}' is already in use")
```

---

## @registry.transform

Mutates data in place. Use `when="before"` to modify the incoming request payload before it reaches the service layer, or `when="after"` to modify the result before it is returned to the client.

Transform functions return `None` — mutate `ctx.data` (before) or `ctx.result` (after) directly.

```python
@registry.transform(schema, *operations, when="before"|"after")
async def my_transform(ctx: RequestContext) -> None:
    ...
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `schema` | `str` | Schema name, or `"*"` for all schemas |
| `*operations` | `str` | One or more of `"create"`, `"get"`, `"list"`, `"update"`, `"delete"` |
| `when` | `str` | `"before"` (default) or `"after"` |

**`when="before"` — modifying `ctx.data` before persistence:**

```python
@registry.transform("user", "create", "update", when="before")
async def normalize_email(ctx: RequestContext) -> None:
    if ctx.data.email:
        ctx.data.email = ctx.data.email.lower().strip()
```

```python
@registry.transform("pet", "create", "update", when="before")
async def normalize_category(ctx: RequestContext) -> None:
    if ctx.data.category:
        ctx.data.category = ctx.data.category.lower()
```

**`when="after"` — computing derived fields on `ctx.result`:**

```python
@registry.transform("widget", "create", when="after")
async def add_computed_fields(ctx: RequestContext) -> None:
    ctx.result.slug = ctx.result.name.lower().replace(" ", "-")
```

```python
@registry.transform("order", "get", "list", when="after")
async def attach_display_total(ctx: RequestContext) -> None:
    items = ctx.result if isinstance(ctx.result, list) else [ctx.result]
    for item in items:
        item.display_total = f"${item.total:.2f}"
```

**Validation:** Both `when` and each operation name are validated at decoration time.

---

## @registry.on

Registers a handler directly against a lifecycle event name. This is equivalent to calling `EventBus.on()` but collected through the registry so you do not need to wire the `EventBus` manually.

Within a `pre_*` event, `@on` handlers run after guards, validators, and before-transforms. Within a `post_*` event, they run after after-transforms.

```python
@registry.on(event, schema_name="*")
async def my_hook(ctx: RequestContext) -> None:
    ...
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `event` | `str` | Lifecycle event name (see table below) |
| `schema_name` | `str` | Schema to scope to, or `"*"` for all schemas (default) |

**All valid event names:**

| Event | Fires |
|-------|-------|
| `pre_create` | Before create service, after guards/validators/transforms |
| `post_create` | After create service, after after-transforms |
| `pre_get` | Before get service |
| `post_get` | After get service |
| `pre_list` | Before list service |
| `post_list` | After list service |
| `pre_update` | Before update service |
| `post_update` | After update service |
| `pre_delete` | Before delete service |
| `post_delete` | After delete service |

**Example — global audit log on all creates:**

```python
import logging

log = logging.getLogger(__name__)

@registry.on("post_create")
async def audit_create(ctx: RequestContext) -> None:
    log.info(
        "Created %s entity_id=%s by user=%s",
        ctx.schema_name,
        ctx.entity_id,
        ctx.current_user.get("id"),
    )
```

**Example — scoped to a single schema:**

```python
@registry.on("post_delete", schema_name="widget")
async def notify_widget_deleted(ctx: RequestContext) -> None:
    await send_notification(
        topic="widget.deleted",
        payload={"entity_id": str(ctx.entity_id)},
    )
```

**Example — prevent deletion by raising `HookError` in a `pre_*` hook:**

```python
@registry.on("pre_delete", schema_name="widget")
async def block_widget_delete(ctx: RequestContext) -> None:
    raise HookError(403, "Widgets cannot be deleted through this API")
```

**Validation:** The event name is validated at decoration time against the complete set of lifecycle event names. Passing an unknown event raises `ValueError` immediately.

---

## Execution Order

The diagram below shows the complete request lifecycle and where each decorator type runs:

```
Request arrives
    │
    ▼
pre_* event fires (in registration order within each group):
    1. @guard handlers
    2. @validate handlers
    3. @transform(when="before") handlers
    4. @on("pre_*") handlers
    │
    ▼
@handler (or default service if no @handler registered)
    │
    ▼
post_* event fires (in registration order within each group):
    1. @transform(when="after") handlers
    2. @on("post_*") handlers
    │
    ▼
Response returned
```

Within each group, handlers execute in the order they were decorated. Global handlers (`schema_name="*"`) run before schema-specific handlers within the same group.

---

## Wiring to SlipStream

Pass the registry to `SlipStream`. That is the only wiring required — no `EventBus` setup, no `apply()` calls.

```python
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from slip_stream import SlipStream, SlipStreamRegistry, HookError, RequestContext

registry = SlipStreamRegistry()

@registry.guard("widget", "delete", "update")
async def admins_only(ctx: RequestContext) -> None:
    if ctx.current_user.get("role") != "admin":
        raise HookError(403, "Admin role required")

@registry.validate("widget", "create", "update")
async def validate_name(ctx: RequestContext) -> None:
    if ctx.data.name and len(ctx.data.name) < 2:
        raise HookError(422, "Name must be at least 2 characters")

@registry.transform("widget", "create", "update", when="before")
async def normalize_name(ctx: RequestContext) -> None:
    if ctx.data.name:
        ctx.data.name = ctx.data.name.strip()

@registry.on("post_create")
async def audit(ctx: RequestContext) -> None:
    print(f"Created {ctx.schema_name} {ctx.entity_id}")

slip = SlipStream(
    app=FastAPI(),
    schema_dir=Path("./schemas"),
    registry=registry,  # That's it — no EventBus wiring needed
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with slip.lifespan():
        yield

app = FastAPI(title="My API", lifespan=lifespan)
slip.app = app
```

When `registry` is provided without an explicit `event_bus`, `SlipStream` automatically creates an `EventBus` and passes it to `registry.apply()` during startup.

---

## Validation and Error Timing

| What is validated | When |
|-------------------|------|
| `operation` argument on `@handler`, `@guard`, `@validate`, `@transform` | At decoration time — raises `ValueError` immediately |
| `when` argument on `@transform` | At decoration time |
| `event` name on `@on` | At decoration time |
| `schema` name on all decorators | At startup, when `registry.apply()` runs — error includes the list of available schema names |

Schema name validation at startup means you get a clear error message with all discovered schemas listed if you misspell a name:

```
ValueError: @handler registered for unknown schema 'widgett'.
Available schemas: ['order', 'pet', 'widget']
```

---

## Complete Working Example

The following is a self-contained example combining all five decorators for a `widget` and `order` schema.

```python
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from slip_stream import (
    SlipStream,
    SlipStreamRegistry,
    HookError,
    RequestContext,
    get_container,
)

log = logging.getLogger(__name__)
registry = SlipStreamRegistry()


# --- @handler: replace the default create with custom logic ---

@registry.handler("widget", "create")
async def custom_widget_create(ctx: RequestContext) -> Any:
    # Force the name to uppercase before saving
    ctx.data.name = ctx.data.name.upper()

    registration = get_container().get("widget")
    repo = registration.repository_class(ctx.db)
    service = registration.services["create"](repo)
    return await service.execute(data=ctx.data, user_id=ctx.current_user["id"])


# --- @guard: authorization ---

@registry.guard("widget", "delete", "update")
async def widget_admins_only(ctx: RequestContext) -> None:
    if ctx.current_user.get("role") != "admin":
        raise HookError(403, "Admin role required to modify widgets")


@registry.guard("*", "create", "update", "delete")
async def require_authenticated(ctx: RequestContext) -> None:
    if not ctx.current_user or not ctx.current_user.get("id"):
        raise HookError(401, "Authentication required")


# --- @validate: cross-field validation ---

@registry.validate("order", "create", "update")
async def check_date_range(ctx: RequestContext) -> None:
    if ctx.data.end_date and ctx.data.start_date:
        if ctx.data.end_date < ctx.data.start_date:
            raise HookError(422, "end_date must be after start_date")


@registry.validate("widget", "create", "update")
async def validate_widget_name(ctx: RequestContext) -> None:
    if ctx.data.name and len(ctx.data.name) < 2:
        raise HookError(422, "Widget name must be at least 2 characters")


# --- @transform: mutate data ---

@registry.transform("user", "create", "update", when="before")
async def normalize_email(ctx: RequestContext) -> None:
    if ctx.data.email:
        ctx.data.email = ctx.data.email.lower().strip()


@registry.transform("widget", "create", when="after")
async def add_computed_slug(ctx: RequestContext) -> None:
    ctx.result.slug = ctx.result.name.lower().replace(" ", "-")


# --- @on: lifecycle events ---

@registry.on("post_create")
async def audit_create(ctx: RequestContext) -> None:
    log.info(
        "Created %s entity_id=%s by user=%s",
        ctx.schema_name,
        ctx.entity_id,
        ctx.current_user.get("id"),
    )


@registry.on("pre_delete", schema_name="widget")
async def confirm_widget_delete(ctx: RequestContext) -> None:
    log.warning("Widget %s is being deleted by %s", ctx.entity_id, ctx.current_user.get("id"))


# --- App setup ---

slip = SlipStream(
    app=FastAPI(),
    schema_dir=Path("./schemas"),
    registry=registry,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with slip.lifespan():
        yield


app = FastAPI(title="Widget API", lifespan=lifespan)
slip.app = app
```
