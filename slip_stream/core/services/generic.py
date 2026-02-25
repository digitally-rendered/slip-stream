"""Generic service classes (use cases) that delegate to a repository port.

Each class is a thin pass-through. Override a specific service to inject
domain logic while keeping the default schema-driven flow intact.
"""

from __future__ import annotations

import uuid
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from slip_stream.core.domain.base import BaseDocument
from slip_stream.core.ports.repository import RepositoryPort

DocT = TypeVar("DocT", bound=BaseDocument)
CreateT = TypeVar("CreateT", bound=BaseModel)
UpdateT = TypeVar("UpdateT", bound=BaseModel)


class GenericCreateService(Generic[DocT, CreateT, UpdateT]):
    """Use case: create a new entity document."""

    def __init__(self, repository: RepositoryPort[DocT, CreateT, UpdateT]) -> None:
        self._repository = repository

    async def execute(self, data: CreateT, user_id: str | None = None) -> DocT:
        """Delegate to repository.create()."""
        return await self._repository.create(data=data, user_id=user_id)


class GenericGetService(Generic[DocT, CreateT, UpdateT]):
    """Use case: retrieve a single entity by its stable entity_id."""

    def __init__(self, repository: RepositoryPort[DocT, CreateT, UpdateT]) -> None:
        self._repository = repository

    async def execute(self, entity_id: uuid.UUID) -> DocT | None:
        """Delegate to repository.get_by_entity_id()."""
        return await self._repository.get_by_entity_id(entity_id=entity_id)


class GenericListService(Generic[DocT, CreateT, UpdateT]):
    """Use case: list the latest active version of all entities."""

    def __init__(self, repository: RepositoryPort[DocT, CreateT, UpdateT]) -> None:
        self._repository = repository

    async def execute(
        self,
        skip: int = 0,
        limit: int = 100,
        sort_by: str = "created_at",
        sort_order: int = -1,
        filter_criteria: dict[str, Any] | None = None,
    ) -> list[DocT]:
        """Delegate to repository.list_latest_active()."""
        return await self._repository.list_latest_active(
            skip=skip,
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order,
            filter_criteria=filter_criteria,
        )


class GenericUpdateService(Generic[DocT, CreateT, UpdateT]):
    """Use case: update an entity by creating a new document version."""

    def __init__(self, repository: RepositoryPort[DocT, CreateT, UpdateT]) -> None:
        self._repository = repository

    async def execute(
        self,
        entity_id: uuid.UUID,
        data: UpdateT,
        user_id: str | None = None,
    ) -> DocT | None:
        """Delegate to repository.update_by_entity_id()."""
        return await self._repository.update_by_entity_id(
            entity_id=entity_id, data=data, user_id=user_id
        )


class GenericDeleteService(Generic[DocT, CreateT, UpdateT]):
    """Use case: soft-delete an entity by creating a tombstone document version."""

    def __init__(self, repository: RepositoryPort[DocT, CreateT, UpdateT]) -> None:
        self._repository = repository

    async def execute(
        self,
        entity_id: uuid.UUID,
        user_id: str | None = None,
    ) -> DocT | None:
        """Delegate to repository.delete_by_entity_id()."""
        return await self._repository.delete_by_entity_id(
            entity_id=entity_id, user_id=user_id
        )


class GenericBulkCreateService(Generic[DocT, CreateT, UpdateT]):
    """Use case: bulk create entity documents."""

    def __init__(self, repository: RepositoryPort[DocT, CreateT, UpdateT]) -> None:
        self._repository = repository

    async def execute(
        self, items: list[CreateT], user_id: str | None = None
    ) -> list[DocT]:
        """Delegate to repository.bulk_create()."""
        return await self._repository.bulk_create(items=items, user_id=user_id)


class GenericBulkUpdateService(Generic[DocT, CreateT, UpdateT]):
    """Use case: bulk update entity documents."""

    def __init__(self, repository: RepositoryPort[DocT, CreateT, UpdateT]) -> None:
        self._repository = repository

    async def execute(
        self,
        updates: list[tuple[uuid.UUID, UpdateT]],
        user_id: str | None = None,
    ) -> list[DocT | None]:
        """Delegate to repository.bulk_update()."""
        return await self._repository.bulk_update(updates=updates, user_id=user_id)


class GenericBulkDeleteService(Generic[DocT, CreateT, UpdateT]):
    """Use case: bulk soft-delete entity documents."""

    def __init__(self, repository: RepositoryPort[DocT, CreateT, UpdateT]) -> None:
        self._repository = repository

    async def execute(
        self,
        entity_ids: list[uuid.UUID],
        user_id: str | None = None,
    ) -> list[DocT | None]:
        """Delegate to repository.bulk_delete()."""
        return await self._repository.bulk_delete(
            entity_ids=entity_ids, user_id=user_id
        )
