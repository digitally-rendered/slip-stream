"""Domain ports — contracts that driven adapters must satisfy.

These protocols define what the core domain *needs* without knowing
how it's fulfilled. The adapter layer provides concrete implementations.

This is the key hexagonal boundary: domain code imports from here,
adapter code implements these interfaces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

# ---------------------------------------------------------------------------
# Domain transfer objects — the shape of data crossing the port boundary
# ---------------------------------------------------------------------------


@dataclass
class ShipmentRequest:
    """What the domain asks the shipping adapter to fulfil."""

    order_entity_id: UUID
    pet_name: str
    quantity: int
    customer_id: str
    shipping_priority: str = "standard"  # "standard" | "express" | "overnight"


@dataclass
class ShipmentResult:
    """What the shipping adapter returns to the domain."""

    tracking_number: str
    carrier: str
    estimated_days: int
    label_url: str | None = None


@dataclass
class PetEnrichment:
    """External breed/temperament data to merge into pet listings."""

    breed_group: str | None = None
    temperament: str | None = None
    life_span: str | None = None
    origin: str | None = None


# ---------------------------------------------------------------------------
# Port protocols — the contracts driven adapters implement
# ---------------------------------------------------------------------------


@runtime_checkable
class ShippingPort(Protocol):
    """Port for submitting shipment requests to an external carrier.

    The domain calls ``create_shipment()`` without knowing whether the
    adapter talks to ShipStation, EasyPost, or a mock stub.
    """

    async def create_shipment(self, request: ShipmentRequest) -> ShipmentResult:
        """Submit a shipment and return tracking info."""
        ...

    async def cancel_shipment(self, tracking_number: str) -> bool:
        """Cancel a pending shipment. Returns True if successful."""
        ...


@runtime_checkable
class PetEnrichmentPort(Protocol):
    """Port for enriching pet data with external breed information.

    The domain calls ``enrich()`` without knowing whether the adapter
    talks to TheDogAPI, a local cache, or a stub.
    """

    async def enrich(self, category: str, name: str) -> PetEnrichment | None:
        """Fetch enrichment data for a pet. Returns None if unavailable."""
        ...
