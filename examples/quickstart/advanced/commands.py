"""Command pattern — Request/Command/Handler/UseCase for the Petstore.

This module demonstrates how slip-stream's lifecycle maps to the
Command Pattern used in Clean/Hexagonal Architecture:

    ┌──────────────────────────────────────────────────────────┐
    │  Command Pattern          │  slip-stream equivalent      │
    │──────────────────────────────────────────────────────────│
    │  Request (DTO)            │  ctx.data (Pydantic model)   │
    │  Command                  │  RequestContext               │
    │  Handler (Action)         │  @registry.handler            │
    │  Use Case (Interactor)    │  @guard + @validate +         │
    │                           │  @transform + handler         │
    │  Invoker                  │  EndpointFactory (auto-gen)   │
    └──────────────────────────────────────────────────────────┘

The flow for PlaceOrder:

    1. Invoker: FastAPI endpoint receives HTTP POST, builds RequestContext
    2. Request/DTO: ``ctx.data`` is the ``OrderCreate`` Pydantic model
    3. Command: The ``RequestContext`` wraps the DTO with operation metadata
    4. Use Case: Guards → Validators → Transforms → Handler execute in order
    5. Handler (Action): Creates the order + triggers shipping via adapter

Each use case is self-contained and independently testable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Requests (DTOs) — Pure data objects carrying intent
# ---------------------------------------------------------------------------
# In slip-stream, these are auto-generated from JSON Schema as Pydantic
# models (OrderCreate, PetCreate, etc.). The examples below show the
# *conceptual* mapping. In practice, you use the auto-generated models.


@dataclass
class PlaceOrderRequest:
    """DTO for the PlaceOrder use case.

    Maps to: auto-generated ``OrderCreate`` Pydantic model from order.json

    In slip-stream, you don't create these manually — they're generated
    from the JSON Schema. This class exists to illustrate the pattern.
    """

    pet_id: UUID
    quantity: int = 1
    shipping_priority: str = "standard"


@dataclass
class CancelOrderRequest:
    """DTO for the CancelOrder use case.

    Maps to: ``entity_id`` path parameter + ``OrderUpdate`` model
    """

    order_entity_id: UUID
    reason: str | None = None


@dataclass
class RegisterPetRequest:
    """DTO for the RegisterPet use case.

    Maps to: auto-generated ``PetCreate`` Pydantic model from pet.json
    """

    name: str
    category: str | None = None
    status: str = "available"
    tags: list[str] | None = None


# ---------------------------------------------------------------------------
# Commands — Wrap DTOs with operation metadata (= RequestContext)
# ---------------------------------------------------------------------------
# In slip-stream, ``RequestContext`` IS the command object:
#   - ctx.operation = the command type ("create", "delete", etc.)
#   - ctx.schema_name = the aggregate ("order", "pet")
#   - ctx.data = the Request DTO
#   - ctx.entity = the current state (for updates/deletes)
#   - ctx.extras = shared state for the handler pipeline
#
# You don't need to create separate Command classes — RequestContext
# already carries all the metadata. This is the pragmatic trade-off
# of a schema-driven framework.


# ---------------------------------------------------------------------------
# Handlers (Actions) — The business logic that executes commands
# ---------------------------------------------------------------------------
# In slip-stream, handlers are registered via @registry.handler.
# Each handler receives a RequestContext (the Command) and returns
# the result. The handler IS the action.
#
# For the PlaceOrder use case, the handler pipeline is:
#   1. @guard("order", "create") → pet_must_exist
#   2. @validate("order", "create") → quantity_limits
#   3. @transform("order", "create", when="before") → set_initial_status
#   4. Default create handler (auto-generated CRUD)
#   5. @transform("order", "create", when="after") → trigger_shipping
#   6. @on("post_create", schema="order") → audit_log
#
# See services/order_logic.py for the implementations.


# ---------------------------------------------------------------------------
# Use Cases (Application Services) — Named business operations
# ---------------------------------------------------------------------------
# In slip-stream, a "use case" is the *combination* of:
#   - The schema (defines the entity)
#   - The operation (create/get/list/update/delete)
#   - The registered guards, validators, transforms, and hooks
#
# Each combination forms a complete use case:
#
#   PlaceOrder    = order + create + [pet_must_exist, quantity_limits,
#                                     set_initial_status, trigger_shipping,
#                                     audit_log]
#
#   CancelOrder   = order + delete + [prevent_shipped_delete]
#
#   RegisterPet   = pet + create + [normalize_pet_name, validate_status]
#
#   GetPetDetails = pet + get + [enrich_with_breed_data]
#
# The use case is declarative: you register the pieces, and the framework
# orchestrates them in the correct order during the request lifecycle.


# ---------------------------------------------------------------------------
# Invoker — Triggers commands (= EndpointFactory)
# ---------------------------------------------------------------------------
# In slip-stream, the invoker is the auto-generated FastAPI endpoint.
# EndpointFactory creates the route, parses the request, builds the
# RequestContext (Command), and dispatches it through the handler pipeline.
#
# The invoker is completely decoupled from the handlers:
#   - It doesn't know what guards/validators/transforms are registered
#   - It doesn't know which adapter will be called
#   - It just builds the Command and dispatches it
#
# This is identical to the Command Pattern's Invoker: it triggers
# the command without knowing how it's processed.


# ---------------------------------------------------------------------------
# Mapping to slip-stream code
# ---------------------------------------------------------------------------

USE_CASE_MAP = {
    "PlaceOrder": {
        "schema": "order",
        "operation": "create",
        "guards": ["pet_must_exist"],
        "validators": ["quantity_limits"],
        "transforms_before": ["set_initial_status"],
        "transforms_after": ["trigger_shipping"],
        "hooks": ["audit_order_created"],
        "description": (
            "Create an order for a pet, verify the pet exists, enforce "
            "quantity limits, submit to shipping provider, and log the event."
        ),
    },
    "CancelOrder": {
        "schema": "order",
        "operation": "delete",
        "guards": ["prevent_shipped_delete"],
        "validators": [],
        "transforms_before": [],
        "transforms_after": [],
        "hooks": [],
        "description": "Cancel an order if it hasn't been delivered yet.",
    },
    "RegisterPet": {
        "schema": "pet",
        "operation": "create",
        "guards": [],
        "validators": ["validate_status"],
        "transforms_before": ["normalize_pet_name"],
        "transforms_after": [],
        "hooks": [],
        "description": "Register a new pet with normalized name and validated status.",
    },
    "GetPetDetails": {
        "schema": "pet",
        "operation": "get",
        "guards": [],
        "validators": [],
        "transforms_before": [],
        "transforms_after": ["enrich_with_breed_data"],
        "hooks": [],
        "description": "Retrieve a pet with enriched breed data from external API.",
    },
}
"""Maps named use cases to their slip-stream implementation components.

This is for documentation/introspection. The actual wiring happens through
the registry decorators in services/order_logic.py and services/pet_logic.py.
"""
