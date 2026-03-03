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

    async def list_latest_active_cursor(
        self,
        first: int | None = None,
        last: int | None = None,
        after: str | None = None,
        before: str | None = None,
        sort_by: str = "created_at",
        sort_order: int = -1,
        filter_criteria: dict[str, Any] | None = None,
    ) -> tuple[list[DocT], dict[str, Any]]:
        """Return a cursor-paginated list of latest active entities.

        Returns:
            Tuple of (items, page_info_dict).
        """
        ...

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

    async def bulk_create(
        self,
        items: list[Any],
        user_id: str | None = None,
    ) -> list[DocT]:
        """Create multiple documents in a single operation."""
        ...

    async def bulk_update(
        self,
        updates: list[tuple[uuid.UUID, Any]],
        user_id: str | None = None,
    ) -> list[DocT | None]:
        """Update multiple entities by creating new versions."""
        ...

    async def bulk_delete(
        self,
        entity_ids: list[uuid.UUID],
        user_id: str | None = None,
    ) -> list[DocT | None]:
        """Soft-delete multiple entities."""
        ...
