# EventBus Lifecycle Hooks

slip-stream fires async lifecycle events around every CRUD operation. Register
handlers on an `EventBus` to run custom logic before or after the framework
executes a service — without touching route handlers or service classes.

## Overview

```
Request
  │
  ├─ pre_create / pre_get / pre_list / pre_update / pre_delete
  │     Global handlers → Schema-specific handlers
  │     (raise HookError to abort → 400/403/422/etc.)
  │
  ├─ [service executes]
  │
  └─ post_create / post_get / post_list / post_update / post_delete
        Global handlers → Schema-specific handlers
        (HookError propagates as 500 — do not raise here)
```

## EventBus

`EventBus` lives at `slip_stream.core.events` and is re-exported from the
top-level `slip_stream` package.

```python
from slip_stream import EventBus
```

### Constructor

```python
bus = EventBus()
```

No arguments. Creates an empty bus with no registered handlers.

### Registering handlers

**Decorator form — `@bus.on(event, schema_name="*")`**

```python
bus = EventBus()

@bus.on("post_create")
async def log_create(ctx: RequestContext) -> None:
    print(f"Created {ctx.schema_name} {ctx.entity_id}")
```

**Imperative form — `bus.register(event, handler, schema_name="*")`**

```python
async def log_create(ctx: RequestContext) -> None:
    print(f"Created {ctx.schema_name} {ctx.entity_id}")

bus.register("post_create", log_create)
```

Both forms are equivalent. Use the decorator form for inline definitions and
the imperative form when the handler function is defined elsewhere (e.g. in a
separate module or loaded dynamically).

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `event` | `str` | required | One of the 10 lifecycle event names |
| `schema_name` | `str` | `"*"` | Schema to scope to, or `"*"` for all schemas |

Passing an unknown event name raises `ValueError` immediately at registration
time — not at runtime.

### Emitting events

The framework calls `emit` internally. You do not call it yourself unless you
are building a custom endpoint or startup sequence.

```python
await bus.emit("pre_create", ctx)
```

### Passing the bus to SlipStream

```python
from pathlib import Path
from fastapi import FastAPI
from slip_stream import SlipStream, EventBus

bus = EventBus()

@bus.on("post_create")
async def audit(ctx):
    print(f"Created {ctx.schema_name}")

slip = SlipStream(
    app=FastAPI(),
    schema_dir=Path("./schemas"),
    event_bus=bus,
)
```

## The 10 Lifecycle Events

| Event | Fires | `entity_id` | `entity` | `data` | `result` |
|-------|-------|-------------|----------|--------|----------|
| `pre_create` | Before insert | — | — | request body | — |
| `post_create` | After insert | set | — | request body | created doc |
| `pre_get` | Before fetch | set | hydrated | — | — |
| `post_get` | After fetch | set | hydrated | — | fetched doc |
| `pre_list` | Before query | — | — | — | — |
| `post_list` | After query | — | — | — | list of docs |
| `pre_update` | Before update | set | hydrated | request body | — |
| `post_update` | After update | set | hydrated | request body | updated doc |
| `pre_delete` | Before soft-delete | set | hydrated | — | — |
| `post_delete` | After soft-delete | set | hydrated | — | — |

The `entity` field is the Pydantic model hydrated from MongoDB **before** the
service runs. On `pre_update` and `pre_delete` it reflects the current
persisted state, giving you the old values before any change is applied.

## Execution Order

When `bus.emit("pre_create", ctx)` is called, handlers run in this order:

1. Global handlers (`schema_name="*"`) — in registration order
2. Schema-specific handlers for `ctx.schema_name` — in registration order

```python
bus = EventBus()

@bus.on("pre_create")               # runs first (global)
async def global_handler(ctx):
    print("global")

@bus.on("pre_create", schema_name="widget")   # runs second (specific)
async def widget_handler(ctx):
    print("widget-specific")

# Emitting for schema "widget" prints: global, widget-specific
# Emitting for schema "order"  prints: global
```

Within each group, handlers execute in the order they were registered.

## HookError

Raise `HookError` from any `pre_*` handler to abort the request and return an
HTTP error response.

```python
from slip_stream import HookError

raise HookError(status_code=403, detail="Not allowed")
```

**Constructor:**

```python
HookError(status_code: int = 400, detail: str = "")
```

**How it is converted:**

The endpoint handler wraps `bus.emit("pre_*", ctx)` in a `try/except`:

```python
try:
    await bus.emit("pre_create", ctx)
except HookError as e:
    raise HTTPException(status_code=e.status_code, detail=e.detail) from e
```

This means `HookError` raised from `pre_*` hooks becomes an `HTTPException`
with the status code you specified. The service never runs.

**Do not raise `HookError` from `post_*` hooks.** Post-hook emission is not
wrapped in a try/except for `HookError`, so it will propagate as an unhandled
exception and produce a 500 response.

## RequestContext Fields by Phase

`RequestContext` is imported from `slip_stream` or `slip_stream.core.context`.

```python
from slip_stream import RequestContext
```

**Fields always present:**

| Field | Type | Description |
|-------|------|-------------|
| `request` | `starlette.requests.Request` | The raw HTTP request |
| `operation` | `str` | `"create"`, `"get"`, `"list"`, `"update"`, or `"delete"` |
| `schema_name` | `str` | The entity schema name, e.g. `"widget"` |
| `current_user` | `dict \| None` | Authenticated user dict from the auth dependency |
| `db` | `AsyncIOMotorDatabase` | The Motor database instance |
| `extras` | `dict` | Arbitrary key-value store for passing data between hooks |

**Fields present for get / update / delete operations:**

| Field | Type | Description |
|-------|------|-------------|
| `entity_id` | `uuid.UUID` | Parsed UUID from the URL path |
| `entity` | `BaseModel` | Hydrated entity from MongoDB (pre-service state) |

**Fields present for create / update operations:**

| Field | Type | Description |
|-------|------|-------------|
| `data` | `BaseModel` | Parsed and validated request body |

**Fields present only in `post_*` hooks:**

| Field | Type | Description |
|-------|------|-------------|
| `result` | `Any` | Return value from the service (or handler override) |

**Fields present only for `pre_list` / `post_list`:**

| Field | Type | Description |
|-------|------|-------------|
| `skip` | `int` | Pagination offset (default `0`) |
| `limit` | `int` | Pagination limit (default `100`) |

## Code Examples

### Authorization guard — block non-admin deletes

```python
from slip_stream import EventBus, HookError, RequestContext

bus = EventBus()

@bus.on("pre_delete", schema_name="widget")
async def admins_only(ctx: RequestContext) -> None:
    if ctx.current_user.get("role") != "admin":
        raise HookError(403, "Admin role required to delete widgets")
```

### Cross-field validation — reject invalid date ranges

```python
@bus.on("pre_create", schema_name="booking")
async def validate_dates(ctx: RequestContext) -> None:
    if ctx.data.end_date <= ctx.data.start_date:
        raise HookError(422, "end_date must be after start_date")
```

### Data normalization — lowercase before insert

```python
@bus.on("pre_create", schema_name="user")
@bus.on("pre_update", schema_name="user")
async def normalize_email(ctx: RequestContext) -> None:
    if ctx.data.email:
        ctx.data.email = ctx.data.email.lower().strip()
```

Note that stacking decorators like this registers the function for both events.

### Audit log — record all creates globally

```python
import logging

log = logging.getLogger(__name__)

@bus.on("post_create")
async def audit_create(ctx: RequestContext) -> None:
    user_id = (ctx.current_user or {}).get("id", "unknown")
    log.info(
        "Created %s entity_id=%s by user=%s",
        ctx.schema_name,
        ctx.entity_id,
        user_id,
    )
```

### Enriching context via `extras` — pass data between hooks

```python
@bus.on("pre_create", schema_name="order")
async def resolve_pricing(ctx: RequestContext) -> None:
    # Fetch from an external service and stash in extras
    ctx.extras["computed_price"] = await pricing_service.get(ctx.data.sku)

@bus.on("post_create", schema_name="order")
async def notify_warehouse(ctx: RequestContext) -> None:
    price = ctx.extras.get("computed_price")
    await warehouse_client.notify(order_id=ctx.entity_id, price=price)
```

### Read current state on update — comparing old vs new

```python
@bus.on("pre_update", schema_name="order")
async def guard_status_transition(ctx: RequestContext) -> None:
    old_status = ctx.entity.status   # hydrated before service runs
    new_status = ctx.data.status

    valid_transitions = {
        "pending": {"confirmed", "cancelled"},
        "confirmed": {"shipped", "cancelled"},
    }

    if new_status and new_status not in valid_transitions.get(old_status, set()):
        raise HookError(409, f"Cannot transition order from {old_status!r} to {new_status!r}")
```

### Pagination override — cap list results

```python
@bus.on("pre_list")
async def cap_limit(ctx: RequestContext) -> None:
    if ctx.limit > 50:
        ctx.limit = 50
```

### Imperative registration — from a factory function

```python
def make_audit_handler(service_name: str):
    async def handler(ctx: RequestContext) -> None:
        await audit_db.log(
            service=service_name,
            schema=ctx.schema_name,
            operation=ctx.operation,
            user=ctx.current_user,
        )
    return handler

bus.register("post_create", make_audit_handler("my-service"))
bus.register("post_update", make_audit_handler("my-service"))
bus.register("post_delete", make_audit_handler("my-service"))
```

## EventBus vs Registry `@on`

There are two ways to register lifecycle hooks. The right choice depends on
how your application is structured.

### `EventBus` — created by the consumer

Use this when you want to own the bus instance explicitly and pass it to
`SlipStream`. Best for small applications or when you need to reuse the bus
object directly (e.g. to call `bus.handler_count` or share it with other
components).

```python
from pathlib import Path
from fastapi import FastAPI
from slip_stream import SlipStream, EventBus, HookError, RequestContext

bus = EventBus()

@bus.on("pre_delete", schema_name="widget")
async def no_deletes(ctx: RequestContext) -> None:
    raise HookError(405, "Widgets cannot be deleted")

slip = SlipStream(
    app=FastAPI(),
    schema_dir=Path("./schemas"),
    event_bus=bus,
)
```

### `SlipStreamRegistry` `@on` — collected by the registry

Use this when you are already using the registry for guards, validators,
transforms, or handler overrides. The registry collects all decorators and
merges them into the bus during `lifespan()`. You do not need to create or
pass an `EventBus` yourself — `SlipStream` creates one automatically when a
registry is provided.

```python
from pathlib import Path
from fastapi import FastAPI
from slip_stream import SlipStream, SlipStreamRegistry, HookError, RequestContext

registry = SlipStreamRegistry()

@registry.on("post_create")
async def audit_create(ctx: RequestContext) -> None:
    print(f"Created {ctx.schema_name} {ctx.entity_id}")

@registry.on("pre_delete", schema_name="widget")
async def no_deletes(ctx: RequestContext) -> None:
    raise HookError(405, "Widgets cannot be deleted")

slip = SlipStream(
    app=FastAPI(),
    schema_dir=Path("./schemas"),
    registry=registry,       # EventBus is created automatically
)
```

### Mixing both

You can provide both an `EventBus` and a `SlipStreamRegistry`. The registry
applies its hooks into the bus you supply:

```python
bus = EventBus()

@bus.on("post_create")                  # registered directly on the bus
async def low_level_hook(ctx): ...

registry = SlipStreamRegistry()

@registry.on("post_create")             # collected by registry, merged into bus at startup
async def high_level_hook(ctx): ...

slip = SlipStream(
    app=FastAPI(),
    schema_dir=Path("./schemas"),
    event_bus=bus,
    registry=registry,
)
```

When both are present, directly-registered bus handlers run first (they were
registered earlier), then registry hooks are merged in during `lifespan()`.

### Summary

| | `EventBus` | `SlipStreamRegistry @on` |
|---|---|---|
| Import | `from slip_stream import EventBus` | `from slip_stream import SlipStreamRegistry` |
| Wire-up | `SlipStream(event_bus=bus)` | `SlipStream(registry=registry)` |
| Bus created by | You | Framework (auto) |
| Works alongside guards / validates / transforms | Separately | Together in one registry |
| Access bus instance | Yes — you hold the reference | No — managed internally |

## Reference

### Valid event names

```python
from slip_stream.core.events import LIFECYCLE_EVENTS

# frozenset of all 10 valid event names:
# pre_create, post_create, pre_get, post_get, pre_list, post_list,
# pre_update, post_update, pre_delete, post_delete
```

### `EventBus` API

```python
EventBus()

bus.on(event: str, schema_name: str = "*") -> decorator
bus.register(event: str, handler: EventHandler, schema_name: str = "*") -> None
await bus.emit(event: str, ctx: RequestContext) -> None
bus.handler_count -> int   # total handlers registered across all events
```

### `HookError` API

```python
HookError(status_code: int = 400, detail: str = "")

err.status_code  # int
err.detail       # str
```

## Related

- [Decorators Reference](decorators.md) — `@guard`, `@validate`, `@transform`, `@on` via `SlipStreamRegistry`
- [RequestContext](context.md) — complete field reference for the context object
- [Getting Started](getting-started.md) — end-to-end example with hooks
