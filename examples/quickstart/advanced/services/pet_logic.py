"""Pet business logic — enriches pet data from external breed APIs.

Demonstrates a different adapter pattern: read-only enrichment on GET
operations. When a pet is retrieved, the enrichment adapter is called
to merge breed information from an external API.

    GET /api/v1/pet/{id}
        → default get service (auto-generated CRUD)
            → @transform(when="after"): enrich with breed data
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from slip_stream import RequestContext, SlipStreamRegistry

if TYPE_CHECKING:
    from examples.quickstart.advanced.ports import PetEnrichmentPort

logger = logging.getLogger(__name__)


def register_pet_logic(registry: SlipStreamRegistry) -> None:
    """Register pet enrichment hooks with the registry."""

    @registry.transform("pet", "get", when="after")
    async def enrich_with_breed_data(ctx: RequestContext) -> None:
        """Merge breed data from an external API into the GET response.

        The enrichment adapter (TheDogAPI, TheCatAPI, or stub) is
        injected via ``ctx.extras``. The result is merged into
        ``ctx.result`` so the response includes breed information.
        """
        enricher: PetEnrichmentPort | None = ctx.extras.get("enrichment_adapter")
        if enricher is None or ctx.result is None:
            return

        category = getattr(ctx.result, "category", None)
        name = getattr(ctx.result, "name", None)
        if not category or not name:
            return

        enrichment = await enricher.enrich(category, name)
        if enrichment:
            # Merge enrichment data into extras (available to filters)
            ctx.extras["breed_data"] = {
                "breed_group": enrichment.breed_group,
                "temperament": enrichment.temperament,
                "life_span": enrichment.life_span,
                "origin": enrichment.origin,
            }
            logger.info(
                "Enriched pet %s with breed data: %s",
                ctx.entity_id,
                enrichment.breed_group,
            )

    @registry.transform("pet", "create", when="before")
    async def normalize_pet_name(ctx: RequestContext) -> None:
        """Normalize pet names to title case."""
        if ctx.data and ctx.data.name:
            ctx.data.name = ctx.data.name.strip().title()

    @registry.validate("pet", "create", "update")
    async def validate_status(ctx: RequestContext) -> None:
        """Ensure pet status is one of the allowed values."""
        from slip_stream import HookError

        allowed = {"available", "pending", "sold"}
        status = getattr(ctx.data, "status", None)
        if status is not None and status not in allowed:
            raise HookError(
                422, f"Invalid status '{status}'. Must be one of: {', '.join(sorted(allowed))}"
            )
