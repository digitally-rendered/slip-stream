"""Repository port interface for hexagonal architecture."""

from __future__ import annotations

import uuid
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from slip_stream.core.domain.base import BaseDocument

DocT = TypeVar("DocT", bound=BaseDocument)
CreateT = TypeVar("CreateT", bound=BaseModel)
UpdateT = TypeVar("UpdateT", bound=BaseModel)


@runtime_checkable
class RepositoryPort(Protocol[DocT, CreateT, UpdateT]):  # type: ignore[misc]
    """Protocol defining the repository contract for all entity repositories.

    Any class implementing these five methods satisfies the port, whether it
    wraps VersionedMongoCRUD or an entirely different persistence backend.
    """

    async def create(
        self,
        data: CreateT,
        user_id: str | None = None,
    ) -> DocT:
        """Create a new document (first version) and return it."""

    async def get_by_entity_id(
        self,
        entity_id: uuid.UUID,
    ) -> DocT | None:
        """Return the latest active version of an entity, or None if not found."""

    async def list_latest_active(
        self,
        skip: int = 0,
        limit: int = 100,
        sort_by: str = "created_at",
        sort_order: int = -1,
        filter_criteria: dict[str, Any] | None = None,
    ) -> list[DocT]:
        """Return a paginated list of the latest active version of each entity."""

    async def update_by_entity_id(
        self,
        entity_id: uuid.UUID,
        data: UpdateT,
        user_id: str | None = None,
    ) -> DocT | None:
        """Create a new version with the applied changes and return it, or None if not found."""

    async def count_active(
        self,
        filter_criteria: dict[str, Any] | None = None,
    ) -> int:
        """Return the total count of active (non-deleted) entities matching the filter."""

    async def delete_by_entity_id(
        self,
        entity_id: uuid.UUID,
        user_id: str | None = None,
    ) -> DocT | None:
        """Soft-delete by creating a tombstone version and return it, or None if not found."""
