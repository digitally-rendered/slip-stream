# Exemplars: Advanced Petstore with Hex Integration

This guide walks through building a production-grade Petstore API that demonstrates slip-stream's full capabilities: the **Command Pattern**, **hexagonal architecture** boundary crossings, and **config-driven external API integration**.

## The Story

Your petstore needs two external integrations:

1. **ShipStation** — When an order is placed, submit a shipment to ShipStation's REST API
2. **TheDogAPI** — When a pet is retrieved, enrich the response with breed data

Both integrations cross hexagonal boundaries: the domain defines what it needs (ports), and the adapter layer handles the external REST calls, namespace translation, and error handling.

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│  INVOKER (auto-generated FastAPI endpoints)                  │
│  EndpointFactory creates routes from JSON Schema             │
│  POST /api/v1/order/  →  builds RequestContext (Command)     │
├──────────────────────────────────────────────────────────────┤
│  USE CASES (decorator-registered handler pipelines)          │
│                                                              │
│  PlaceOrder = order + create:                                │
│    @guard   → pet_must_exist (verify FK)                     │
│    @validate → quantity_limits (max 10)                      │
│    @transform(before) → set_initial_status                   │
│    [default handler] → VersionedMongoCRUD.create()           │
│    @transform(after) → trigger_shipping (hex boundary!)      │
│    @on(post_create) → audit_log                              │
│                                                              │
│  GetPetDetails = pet + get:                                  │
│    [default handler] → VersionedMongoCRUD.get()              │
│    @transform(after) → enrich_with_breed_data                │
├──────────────────────────────────────────────────────────────┤
│  DOMAIN PORTS (protocols — no external dependencies)         │
│    ShippingPort: create_shipment(), cancel_shipment()        │
│    PetEnrichmentPort: enrich()                               │
├──────────────────────────────────────────────────────────────┤
│  DRIVEN ADAPTERS (implement ports via external REST)         │
│    ShipStationAdapter  →  ShipStation REST API               │
│    BreedApiAdapter     →  TheDogAPI / TheCatAPI              │
│    MappedApiAdapter    →  Config-driven from YAML            │
│    Stub*Adapter        →  Test doubles                       │
└──────────────────────────────────────────────────────────────┘
```

## Command Pattern Mapping

slip-stream's lifecycle maps directly to the Command Pattern:

| Command Pattern | slip-stream | Purpose |
|----------------|-------------|---------|
| **Request (DTO)** | `ctx.data` (auto-generated Pydantic model) | Carries validated input data |
| **Command** | `RequestContext` | Wraps DTO with operation metadata |
| **Handler (Action)** | `@registry.handler` / default service | Executes the business logic |
| **Use Case** | Guard + Validate + Transform + Handler chain | Complete business operation |
| **Invoker** | `EndpointFactory` (auto-generated routes) | Triggers command dispatch |

The key insight: you don't need to create separate Command classes. `RequestContext` already carries the operation type, schema name, entity state, user info, and extras — it **is** the command.

## Step 1: Define Domain Ports

Ports live in the domain layer. They define *what* the domain needs without knowing *how* it's fulfilled.

```python
# advanced/ports.py
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID


@dataclass
class ShipmentRequest:
    """What the domain asks the shipping adapter to fulfil."""
    order_entity_id: UUID
    pet_name: str
    quantity: int
    customer_id: str
    shipping_priority: str = "standard"


@dataclass
class ShipmentResult:
    """What the shipping adapter returns."""
    tracking_number: str
    carrier: str
    estimated_days: int
    label_url: str | None = None


@runtime_checkable
class ShippingPort(Protocol):
    """Port for external shipping providers."""
    async def create_shipment(self, request: ShipmentRequest) -> ShipmentResult: ...
    async def cancel_shipment(self, tracking_number: str) -> bool: ...
```

The domain transfers objects (`ShipmentRequest`, `ShipmentResult`) define the **boundary shape** — the adapter translates between this shape and the external API's schema.

## Step 2: Build the Driven Adapter

The adapter implements the port and handles all external API details:

```python
# advanced/adapters/shipping.py
import httpx
from advanced.ports import ShipmentRequest, ShipmentResult, ShippingPort


def _to_shipstation_order(request: ShipmentRequest) -> dict:
    """Domain → ShipStation namespace translation.

    Domain (our world)          → ShipStation (their world)
    ─────────────────────────── → ──────────────────────────
    order_entity_id             → orderNumber
    pet_name                    → items[0].name
    quantity                    → items[0].quantity
    customer_id                 → customerEmail
    shipping_priority           → requestedShippingService
    """
    return {
        "orderNumber": str(request.order_entity_id),
        "orderStatus": "awaiting_shipment",
        "customerEmail": f"{request.customer_id}@petstore.example",
        "items": [{
            "name": request.pet_name,
            "quantity": request.quantity,
            "sku": f"PET-{str(request.order_entity_id)[:8]}",
        }],
        "requestedShippingService": {
            "standard": "usps_priority_mail",
            "express": "ups_2nd_day_air",
            "overnight": "fedex_priority_overnight",
        }.get(request.shipping_priority, "usps_priority_mail"),
    }


class ShipStationAdapter:
    """Driven adapter: ShipStation REST API."""

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://ssapi.shipstation.com",
            auth=(api_key, api_secret),
        )

    async def create_shipment(self, request: ShipmentRequest) -> ShipmentResult:
        # Domain → External namespace → HTTP → External response → Domain
        payload = _to_shipstation_order(request)
        response = await self._client.post("/orders/createorder", json=payload)
        response.raise_for_status()
        data = response.json()
        return ShipmentResult(
            tracking_number=str(data["shipmentId"]),
            carrier=data["carrierCode"],
            estimated_days=data.get("estimatedDays", 5),
        )


class StubShippingAdapter:
    """Test stub — records calls, returns canned data."""

    def __init__(self):
        self.shipments = []

    async def create_shipment(self, request: ShipmentRequest) -> ShipmentResult:
        self.shipments.append(request)
        return ShipmentResult(
            tracking_number="STUB-ABC123",
            carrier="stub_carrier",
            estimated_days=3,
        )

# Both satisfy the port
assert isinstance(StubShippingAdapter(), ShippingPort)
```

## Step 3: Register Business Logic (Use Cases)

Each use case is a set of decorator registrations:

```python
# advanced/services/order_logic.py
from slip_stream import HookError, RequestContext, SlipStreamRegistry


def register_order_logic(registry: SlipStreamRegistry) -> None:

    # --- PlaceOrder Use Case ---

    @registry.guard("order", "create")
    async def pet_must_exist(ctx: RequestContext) -> None:
        """Guard: verify foreign key before accepting order."""
        pet = await ctx.db["pet"].find_one(
            {"entity_id": str(ctx.data.pet_id), "deleted_at": None},
            sort=[("record_version", -1)],
        )
        if pet is None:
            raise HookError(404, f"Pet {ctx.data.pet_id} not found")
        ctx.extras["pet_doc"] = pet

    @registry.validate("order", "create", "update")
    async def quantity_limits(ctx: RequestContext) -> None:
        """Validator: enforce business rules beyond schema validation."""
        if getattr(ctx.data, "quantity", None) and ctx.data.quantity > 10:
            raise HookError(422, "Cannot order more than 10 pets at once")

    @registry.transform("order", "create", when="after")
    async def trigger_shipping(ctx: RequestContext) -> None:
        """Transform (after): cross the hex boundary to shipping adapter.

        This is where the magic happens:
        1. Read the persisted domain result (ctx.result)
        2. Build a domain transfer object (ShipmentRequest)
        3. Call the port (injected via ctx.extras)
        4. The adapter translates to ShipStation's namespace
        """
        shipping = ctx.extras.get("shipping_adapter")
        if not shipping:
            return

        from advanced.ports import ShipmentRequest
        result = await shipping.create_shipment(ShipmentRequest(
            order_entity_id=ctx.result.entity_id,
            pet_name=ctx.extras["pet_doc"]["name"],
            quantity=ctx.result.quantity,
            customer_id=str(ctx.current_user.get("id", "anon")),
        ))
        ctx.extras["tracking_number"] = result.tracking_number

    # --- CancelOrder Use Case ---

    @registry.guard("order", "delete")
    async def prevent_shipped_delete(ctx: RequestContext) -> None:
        """Guard: can't cancel delivered orders."""
        if ctx.entity and ctx.entity.status == "delivered":
            raise HookError(409, "Cannot cancel a delivered order")
```

## Step 4: Config-Driven API Mapping

For simpler integrations, skip the hand-written adapter entirely. Define the field mapping in `slip-stream.yml`:

```yaml
# slip-stream.yml
external_apis:
  shipstation:
    base_url: https://ssapi.shipstation.com
    auth:
      type: basic
      key_env: SHIPSTATION_API_KEY
      secret_env: SHIPSTATION_API_SECRET
    mappings:
      - local_schema: order
        remote_resource: /orders/createorder
        remote_method: POST
        trigger: post_create
        field_map:
          # local domain field  →  remote API field path
          entity_id:              orderNumber
          quantity:               items[0].quantity
        response_map:
          # remote response field  →  local extras key
          tracking_number:          shipmentId
          carrier:                  carrierCode
        constants:
          orderStatus: awaiting_shipment
```

The `MappedApiAdapter` reads this config and auto-generates the translation layer:

```python
# advanced/adapters/api_mapping.py

class MappedApiAdapter:
    """Config-driven adapter — no hand-written translation code."""

    def translate_outbound(self, mapping, local_data) -> dict:
        """Local domain fields → external API payload via field_map."""
        payload = {}
        for local_field, remote_path in mapping.field_map.items():
            value = local_data.get(local_field)
            if value is not None:
                _set_nested(payload, remote_path, value)
        for remote_path, value in mapping.constants.items():
            _set_nested(payload, remote_path, value)
        return payload

    def translate_inbound(self, mapping, remote_data) -> dict:
        """External API response → local domain fields via response_map."""
        result = {}
        for local_field, remote_path in mapping.response_map.items():
            value = _get_nested(remote_data, remote_path)
            if value is not None:
                result[local_field] = value
        return result
```

## Step 5: Wire Everything in main.py

```python
# advanced/main.py
from slip_stream import SlipStream, SlipStreamRegistry
from advanced.services.order_logic import register_order_logic
from advanced.services.pet_logic import register_pet_logic
from advanced.adapters.shipping import ShipStationAdapter, StubShippingAdapter

registry = SlipStreamRegistry()

# Register use cases
register_order_logic(registry)
register_pet_logic(registry)

# Choose adapter (production vs test)
shipping = (
    ShipStationAdapter(api_key=..., api_secret=...)
    if os.environ.get("USE_REAL_APIS") == "true"
    else StubShippingAdapter()
)

# Inject adapter into request lifecycle
@registry.on("pre_create", schema="order")
async def inject_shipping(ctx):
    ctx.extras["shipping_adapter"] = shipping

# Wire into slip-stream
slip = SlipStream(
    app=app,
    schema_dir=SCHEMAS_DIR,
    api_prefix="/api/v1",
    registry=registry,
)
```

## Step 6: Testing

The hex boundary makes testing trivial — swap the real adapter for a stub:

```python
# tests/test_place_order.py
from advanced.adapters.shipping import StubShippingAdapter

async def test_place_order_triggers_shipping(test_client):
    """The PlaceOrder use case should submit a shipment."""
    stub = StubShippingAdapter()

    # Create a pet first
    pet = test_client.post("/api/v1/pet/", json={"name": "Fido", "status": "available"})
    pet_id = pet.json()["entity_id"]

    # Place order — shipping adapter is injected via ctx.extras
    order = test_client.post("/api/v1/order/", json={
        "pet_id": pet_id,
        "quantity": 2,
    })

    assert order.status_code == 201
    assert len(stub.shipments) == 1
    assert stub.shipments[0].pet_name == "Fido"
    assert stub.shipments[0].quantity == 2
```

## Key Takeaways

1. **Ports define the contract** — domain code never imports external API clients
2. **Adapters translate namespaces** — `ShipmentRequest` ↔ ShipStation's JSON schema
3. **Config-driven mapping** replaces hand-written adapters for simple integrations
4. **Command Pattern is built-in** — `RequestContext` is the command, decorators are handlers
5. **Guards/Validators/Transforms** compose into named use cases
6. **Stub adapters** make testing trivial — no HTTP mocking needed
7. **Adapter injection** via `ctx.extras` keeps everything loosely coupled

## File Layout

```
examples/quickstart/
├── main.py                           # Basic petstore (no overrides)
├── schemas/
│   ├── pet.json
│   ├── order.json
│   └── todo.json
└── advanced/
    ├── main.py                       # Wired app with all integrations
    ├── commands.py                   # Command pattern documentation
    ├── slip-stream.yml               # Config with external API mappings
    ├── ports.py                      # Domain ports (ShippingPort, etc.)
    ├── adapters/
    │   ├── shipping.py               # ShipStation adapter + stub
    │   ├── pet_enrichment.py         # TheDogAPI adapter + stub
    │   └── api_mapping.py            # Config-driven generic adapter
    └── services/
        ├── order_logic.py            # PlaceOrder, CancelOrder use cases
        └── pet_logic.py              # RegisterPet, GetPetDetails use cases
```
