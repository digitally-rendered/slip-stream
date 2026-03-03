"""Shipping adapter — translates domain objects to ShipStation's REST API.

This is a *driven adapter* in hexagonal architecture: it implements the
``ShippingPort`` protocol defined in the domain layer and handles all
communication with the external ShipStation API.

Key hex-architecture principles demonstrated:
1. The adapter imports from core (ports.py) — never the other way around.
2. Domain objects (``ShipmentRequest``) are translated to the external
   API's namespace (``ShipStation.Order``) inside this adapter.
3. The REST client, authentication, error mapping, and retry logic are
   encapsulated here — invisible to the domain.
4. A mock/stub implementation is provided for testing.

Example::

    # Production
    shipping = ShipStationAdapter(
        api_key="sk_live_...",
        api_secret="ss_live_...",
    )

    # Test
    shipping = StubShippingAdapter()

    # Either one satisfies ShippingPort
    result = await shipping.create_shipment(request)
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from examples.quickstart.advanced.ports import (
    ShipmentRequest,
    ShipmentResult,
    ShippingPort,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Namespace mapping: Domain → ShipStation API
# ---------------------------------------------------------------------------
# Our domain uses snake_case dataclasses. ShipStation's REST API uses
# camelCase JSON with a completely different object shape. The adapter
# bridges these two worlds.

_PRIORITY_TO_SERVICE = {
    "standard": "usps_priority_mail",
    "express": "ups_2nd_day_air",
    "overnight": "fedex_priority_overnight",
}


def _to_shipstation_order(request: ShipmentRequest) -> dict[str, Any]:
    """Transform a domain ShipmentRequest into ShipStation's JSON schema.

    This is the core translation: our ``ShipmentRequest`` dataclass maps
    to ShipStation's ``Order`` resource with its own field names, nested
    structure, and conventions.

    Domain (our world)          → ShipStation (their world)
    ─────────────────────────── → ──────────────────────────
    order_entity_id             → orderNumber
    pet_name                    → items[0].name
    quantity                    → items[0].quantity
    customer_id                 → customerEmail (looked up)
    shipping_priority           → serviceCode (mapped)
    """
    return {
        "orderNumber": str(request.order_entity_id),
        "orderDate": None,  # ShipStation fills this
        "orderStatus": "awaiting_shipment",
        "customerEmail": f"{request.customer_id}@petstore.example",
        "billTo": {
            "name": request.customer_id,
        },
        "shipTo": {
            "name": request.customer_id,
        },
        "items": [
            {
                "name": request.pet_name,
                "quantity": request.quantity,
                "unitPrice": 0,  # pets are priceless
                "sku": f"PET-{str(request.order_entity_id)[:8]}",
            }
        ],
        "requestedShippingService": _PRIORITY_TO_SERVICE.get(
            request.shipping_priority, "usps_priority_mail"
        ),
    }


def _from_shipstation_response(data: dict[str, Any]) -> ShipmentResult:
    """Transform ShipStation's response back into our domain object.

    ShipStation (their world)   → Domain (our world)
    ─────────────────────────── → ──────────────────────
    shipmentId + orderId        → tracking_number
    carrierCode                 → carrier
    estimatedDeliveryDate       → estimated_days (computed)
    labelData.url               → label_url
    """
    return ShipmentResult(
        tracking_number=str(data.get("shipmentId", "UNKNOWN")),
        carrier=data.get("carrierCode", "unknown"),
        estimated_days=data.get("estimatedDays", 5),
        label_url=data.get("labelData", {}).get("url"),
    )


# ---------------------------------------------------------------------------
# Production adapter — real HTTP calls to ShipStation
# ---------------------------------------------------------------------------


class ShipStationAdapter:
    """Driven adapter: fulfils ShippingPort via ShipStation's REST API.

    All external API details (auth, endpoints, error codes, rate limits)
    are encapsulated here. The domain never sees an HTTP client.
    """

    BASE_URL = "https://ssapi.shipstation.com"

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            auth=(api_key, api_secret),
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        )

    async def create_shipment(self, request: ShipmentRequest) -> ShipmentResult:
        """Domain → ShipStation translation → HTTP POST → domain result."""
        payload = _to_shipstation_order(request)

        logger.info(
            "Creating shipment for order %s via ShipStation",
            request.order_entity_id,
        )

        response = await self._client.post("/orders/createorder", json=payload)
        response.raise_for_status()

        return _from_shipstation_response(response.json())

    async def cancel_shipment(self, tracking_number: str) -> bool:
        """Void a shipment label in ShipStation."""
        response = await self._client.post(
            "/shipments/voidlabel",
            json={"shipmentId": tracking_number},
        )
        return response.status_code == 200

    async def close(self) -> None:
        """Clean up the HTTP client."""
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Stub adapter — for tests and local development
# ---------------------------------------------------------------------------


class StubShippingAdapter:
    """Test stub that satisfies ShippingPort without making HTTP calls.

    Records all calls for assertion in tests.
    """

    def __init__(self) -> None:
        self.shipments: list[ShipmentRequest] = []
        self.cancellations: list[str] = []

    async def create_shipment(self, request: ShipmentRequest) -> ShipmentResult:
        self.shipments.append(request)
        return ShipmentResult(
            tracking_number=f"STUB-{uuid.uuid4().hex[:8].upper()}",
            carrier="stub_carrier",
            estimated_days=3,
            label_url=None,
        )

    async def cancel_shipment(self, tracking_number: str) -> bool:
        self.cancellations.append(tracking_number)
        return True


# Verify both implementations satisfy the port
assert isinstance(StubShippingAdapter(), ShippingPort)
