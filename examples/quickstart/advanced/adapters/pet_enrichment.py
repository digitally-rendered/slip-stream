"""Pet enrichment adapter — fetches breed data from TheDogAPI/TheCatAPI.

Another driven adapter demonstrating how external REST APIs are accessed
through the hexagonal boundary. The domain asks for ``PetEnrichment``
data; this adapter knows how to talk to breed databases.

Namespace mapping:
    Domain: PetEnrichment(breed_group, temperament, life_span, origin)
    TheDogAPI: {breed_group, temperament, life_span, origin, ...25 more fields}

The adapter cherry-picks only what the domain port requires, discarding
the rest. This is a key hex principle: adapters *translate* between the
external world's shape and the domain's shape.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from examples.quickstart.advanced.ports import PetEnrichment, PetEnrichmentPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Category → API mapping
# ---------------------------------------------------------------------------

_API_URLS = {
    "dog": "https://api.thedogapi.com/v1/breeds/search",
    "cat": "https://api.thecatapi.com/v1/breeds/search",
}


def _from_breed_response(data: dict[str, Any]) -> PetEnrichment:
    """Map the external API's breed object to our domain transfer object.

    TheDogAPI/TheCatAPI return 25+ fields per breed. We extract only
    what our domain needs — the adapter filters at the boundary.
    """
    return PetEnrichment(
        breed_group=data.get("breed_group"),
        temperament=data.get("temperament"),
        life_span=data.get("life_span"),
        origin=data.get("origin"),
    )


# ---------------------------------------------------------------------------
# Production adapter
# ---------------------------------------------------------------------------


class BreedApiAdapter:
    """Driven adapter: enriches pets via TheDogAPI / TheCatAPI REST APIs."""

    def __init__(self, api_key: str | None = None) -> None:
        headers = {}
        if api_key:
            headers["x-api-key"] = api_key
        self._client = httpx.AsyncClient(headers=headers, timeout=10.0)

    async def enrich(self, category: str, name: str) -> PetEnrichment | None:
        """Look up breed data for a pet by category and name."""
        base_url = _API_URLS.get(category.lower())
        if not base_url:
            return None

        try:
            response = await self._client.get(base_url, params={"q": name})
            response.raise_for_status()
            results = response.json()
            if results:
                return _from_breed_response(results[0])
        except httpx.HTTPError:
            logger.warning("Breed API unavailable for %s/%s", category, name)

        return None

    async def close(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Stub adapter
# ---------------------------------------------------------------------------


class StubPetEnrichmentAdapter:
    """Test stub returning canned breed data."""

    CANNED = {
        "dog": PetEnrichment(
            breed_group="Sporting",
            temperament="Friendly, Active, Loyal",
            life_span="10 - 12 years",
            origin="United Kingdom",
        ),
        "cat": PetEnrichment(
            breed_group="Natural",
            temperament="Active, Energetic, Independent",
            life_span="14 - 15 years",
            origin="Egypt",
        ),
    }

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def enrich(self, category: str, name: str) -> PetEnrichment | None:
        self.calls.append((category, name))
        return self.CANNED.get(category.lower())


# Verify both implementations satisfy the port
assert isinstance(StubPetEnrichmentAdapter(), PetEnrichmentPort)
