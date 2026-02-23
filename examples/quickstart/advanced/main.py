"""Advanced Petstore API — hexagonal architecture with external integrations.

This example builds on the basic quickstart to demonstrate:

1. **Command Pattern**: Request/Command/Handler/UseCase mapping
2. **Hex Boundary Crossing**: Driven adapters for external REST APIs
3. **Config-Driven API Mapping**: External OpenAPI specs mapped to local
   schemas through YAML configuration
4. **Business Logic Overrides**: Guards, validators, transforms, hooks
   wired through slip-stream's decorator registry

Architecture::

    ┌─────────────────────────────────────────────────────────────┐
    │  Invoker (auto-generated FastAPI endpoints)                 │
    ├─────────────────────────────────────────────────────────────┤
    │  Use Cases (guard + validate + transform + handler chains)  │
    │    PlaceOrder:   pet_must_exist → qty_limits → ship → audit │
    │    RegisterPet:  normalize → validate_status                │
    │    GetPetDetails: enrich_with_breed_data                    │
    ├─────────────────────────────────────────────────────────────┤
    │  Domain Ports                                               │
    │    ShippingPort     PetEnrichmentPort                       │
    ├─────────────────────────────────────────────────────────────┤
    │  Driven Adapters                                            │
    │    ShipStationAdapter      BreedApiAdapter                  │
    │    MappedApiAdapter (config-driven)                         │
    │    StubShippingAdapter     StubPetEnrichmentAdapter          │
    └─────────────────────────────────────────────────────────────┘

Run with::

    cd examples/quickstart
    uvicorn advanced.main:app --reload

Visit http://localhost:8000/docs to see all endpoints.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from slip_stream import SlipStream, SlipStreamRegistry

# Import the business logic registrations
from examples.quickstart.advanced.services.order_logic import register_order_logic
from examples.quickstart.advanced.services.pet_logic import register_pet_logic

# Import adapter implementations
from examples.quickstart.advanced.adapters.shipping import (
    ShipStationAdapter,
    StubShippingAdapter,
)
from examples.quickstart.advanced.adapters.pet_enrichment import (
    BreedApiAdapter,
    StubPetEnrichmentAdapter,
)

SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"


def create_app() -> FastAPI:
    """Build the advanced petstore with all hex-architecture wiring.

    This function demonstrates the full setup:
    1. Create the registry (declarative decorator API)
    2. Register business logic (guards, validators, transforms, hooks)
    3. Create adapters (choose production vs stub based on environment)
    4. Inject adapters via the registry's extras mechanism
    5. Wire everything into SlipStream
    """

    # -----------------------------------------------------------------
    # 1. Create the registry — the declarative wiring surface
    # -----------------------------------------------------------------
    registry = SlipStreamRegistry()

    # -----------------------------------------------------------------
    # 2. Register business logic for each use case
    # -----------------------------------------------------------------
    # Each function registers its decorators on the registry.
    # This is where the Command Pattern's handlers are defined.
    register_order_logic(registry)
    register_pet_logic(registry)

    # -----------------------------------------------------------------
    # 3. Choose adapter implementations based on environment
    # -----------------------------------------------------------------
    use_real_apis = os.environ.get("USE_REAL_APIS", "false").lower() == "true"

    if use_real_apis:
        shipping_adapter = ShipStationAdapter(
            api_key=os.environ["SHIPSTATION_API_KEY"],
            api_secret=os.environ["SHIPSTATION_API_SECRET"],
        )
        enrichment_adapter = BreedApiAdapter(
            api_key=os.environ.get("DOG_API_KEY"),
        )
    else:
        shipping_adapter = StubShippingAdapter()
        enrichment_adapter = StubPetEnrichmentAdapter()

    # -----------------------------------------------------------------
    # 4. Inject adapters into the request lifecycle via extras
    # -----------------------------------------------------------------
    # The @on("pre_create") and @on("pre_get") hooks inject adapters
    # into ctx.extras so that transforms can use them without direct
    # coupling to concrete implementations.

    @registry.on("pre_create", schema="order")
    async def inject_shipping(ctx):
        ctx.extras["shipping_adapter"] = shipping_adapter

    @registry.on("pre_get", schema="pet")
    async def inject_enrichment(ctx):
        ctx.extras["enrichment_adapter"] = enrichment_adapter

    # -----------------------------------------------------------------
    # 5. Wire into SlipStream
    # -----------------------------------------------------------------
    slip = SlipStream(
        app=FastAPI(),  # placeholder
        schema_dir=SCHEMAS_DIR,
        api_prefix="/api/v1",
        registry=registry,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with slip.lifespan():
            yield
        # Clean up adapters
        if hasattr(shipping_adapter, "close"):
            await shipping_adapter.close()
        if hasattr(enrichment_adapter, "close"):
            await enrichment_adapter.close()

    app = FastAPI(
        title="Advanced Petstore API",
        description=(
            "Petstore with hexagonal architecture, command pattern, "
            "and config-driven external API integrations."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )
    slip.app = app

    # -----------------------------------------------------------------
    # Root endpoint with use case documentation
    # -----------------------------------------------------------------
    from examples.quickstart.advanced.commands import USE_CASE_MAP

    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "message": "Advanced Petstore API",
            "docs": "/docs",
            "architecture": "hexagonal + command pattern",
            "use_cases": {
                name: {
                    "endpoint": f"/api/v1/{uc['schema']}/",
                    "operation": uc["operation"],
                    "description": uc["description"],
                }
                for name, uc in USE_CASE_MAP.items()
            },
            "endpoints": {
                "pets": "/api/v1/pet/",
                "orders": "/api/v1/order/",
                "todos": "/api/v1/todo/",
            },
        }

    return app


app = create_app()
