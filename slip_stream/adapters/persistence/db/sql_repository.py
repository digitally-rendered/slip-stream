"""SQLAlchemy-based repository adapter for slip-stream.

Implements the same append-only versioned document pattern used by the
MongoDB adapter, but backed by any SQLAlchemy-supported RDBMS
(PostgreSQL, MySQL, SQLite, etc.).

Tables are auto-generated from JSON Schema definitions — one table per
schema.  Each row corresponds to a document version (same as MongoDB's
versioned documents).

Usage::

    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from slip_stream.adapters.persistence.db.sql_repository import (
        SQLRepository,
        build_table_from_schema,
    )

    engine = create_async_engine("sqlite+aiosqlite:///app.db")
    SessionLocal = async_sessionmaker(engine)
    table = build_table_from_schema("widget", schema_dict, metadata)

    async with SessionLocal() as session:
        repo = SQLRepository(session, table)
        doc = await repo.create(data, user_id="user-1")

Requires: ``sqlalchemy[asyncio]`` (optional dependency).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

try:
    import sqlalchemy as sa
    from sqlalchemy import (
        Column,
        DateTime,
        Float,
        Integer,
        MetaData,
        String,
        Table,
        Text,
        Boolean,
    )
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID
    from sqlalchemy.ext.asyncio import AsyncSession

    HAS_SQLALCHEMY = True
except ImportError:
    HAS_SQLALCHEMY = False


# ---------------------------------------------------------------------------
# JSON Schema → SQLAlchemy table builder
# ---------------------------------------------------------------------------

# Mapping from JSON Schema types to SQLAlchemy column types
_JSON_SCHEMA_TO_SA: dict[str, Any] = {}
if HAS_SQLALCHEMY:
    _JSON_SCHEMA_TO_SA = {
        "string": String(255),
        "integer": Integer,
        "number": Float,
        "boolean": Boolean,
    }

# Audit fields managed by the framework (always present)
_AUDIT_FIELD_NAMES = frozenset({
    "id", "entity_id", "schema_version", "record_version",
    "created_at", "updated_at", "deleted_at",
    "created_by", "updated_by", "deleted_by",
})


def build_table_from_schema(
    schema_name: str,
    schema_dict: dict[str, Any],
    metadata: Any,  # sa.MetaData
) -> Any:  # sa.Table
    """Build a SQLAlchemy ``Table`` from a JSON Schema definition.

    Generates audit columns automatically plus one column per property
    defined in the schema.

    Args:
        schema_name: The entity name (used as table name).
        schema_dict: The JSON Schema dict.
        metadata: A SQLAlchemy ``MetaData`` instance.

    Returns:
        A SQLAlchemy ``Table`` object.
    """
    if not HAS_SQLALCHEMY:
        raise ImportError("sqlalchemy is required for SQL persistence")

    columns: list[Column] = [
        Column("id", String(36), primary_key=True),
        Column("entity_id", String(36), nullable=False, index=True),
        Column("schema_version", String(20), nullable=False, default="1.0.0"),
        Column("record_version", Integer, nullable=False, default=1),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        Column("deleted_at", DateTime(timezone=True), nullable=True),
        Column("created_by", String(255), nullable=True),
        Column("updated_by", String(255), nullable=True),
        Column("deleted_by", String(255), nullable=True),
    ]

    properties = schema_dict.get("properties", {})
    for prop_name, prop_def in properties.items():
        if prop_name in _AUDIT_FIELD_NAMES or prop_name == "_id":
            continue

        col_type = _resolve_column_type(prop_def)
        columns.append(Column(prop_name, col_type, nullable=True))

    return Table(schema_name, metadata, *columns)


def _resolve_column_type(prop_def: dict[str, Any]) -> Any:
    """Resolve a JSON Schema property definition to a SQLAlchemy column type.

    Handles the ``type`` and ``format`` keywords from JSON Schema.  Arrays and
    objects are stored as JSON-serialised ``Text``.  Strings longer than 1 000
    characters (via ``maxLength``) are also mapped to ``Text``; all other
    strings default to ``String(255)`` or ``String(maxLength)``.

    Args:
        prop_def: A single JSON Schema property definition dict, e.g.
            ``{"type": "string", "format": "date-time"}``.

    Returns:
        A SQLAlchemy column type instance or class suitable for use in a
        ``Column`` definition.
    """
    json_type = prop_def.get("type", "string")

    if json_type == "string":
        fmt = prop_def.get("format", "")
        if fmt == "date-time":
            return DateTime(timezone=True)
        if fmt == "uuid":
            return String(36)
        max_length = prop_def.get("maxLength", 255)
        if max_length > 1000:
            return Text
        return String(max_length)

    if json_type == "integer":
        return Integer

    if json_type == "number":
        return Float

    if json_type == "boolean":
        return Boolean

    if json_type == "array":
        return Text  # JSON-serialised

    if json_type == "object":
        return Text  # JSON-serialised

    return String(255)


# ---------------------------------------------------------------------------
# SQL Repository
# ---------------------------------------------------------------------------


class SQLRepository:
    """Versioned CRUD repository backed by SQLAlchemy.

    Implements the same append-only versioned document pattern as
    ``VersionedMongoCRUD`` — every write creates a new row (document
    version), never updates in place.

    The class satisfies ``RepositoryPort`` but uses plain dicts (with
    Pydantic ``BaseModel`` schema) rather than ``BaseDocument`` subclasses,
    so it can operate without MongoDB dependencies.
    """

    def __init__(
        self,
        session: Any,  # AsyncSession
        table: Any,  # sa.Table
        doc_model: type[BaseModel] | None = None,
    ) -> None:
        if not HAS_SQLALCHEMY:
            raise ImportError("sqlalchemy is required for SQL persistence")
        self._session: AsyncSession = session
        self._table: Table = table
        self._doc_model = doc_model

    def _to_result(self, row: Any) -> dict[str, Any] | BaseModel:
        """Convert a SQLAlchemy row to a dict or Pydantic model.

        When a ``doc_model`` was supplied at construction time the row is
        validated and returned as that model instance; otherwise a plain
        ``dict`` is returned.

        Args:
            row: A SQLAlchemy ``Row`` object returned by ``execute()``.

        Returns:
            A ``BaseModel`` instance if ``doc_model`` is set, otherwise a
            ``dict`` mapping column names to their Python values.
        """
        data = dict(row._mapping)
        if self._doc_model is not None:
            return self._doc_model.model_validate(data)
        return data

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    async def create(
        self,
        data: BaseModel,
        user_id: str | None = None,
        entity_id: uuid.UUID | None = None,
    ) -> dict[str, Any] | BaseModel:
        """Create a new entity (record_version=1).

        Inserts a single row with all audit fields populated.  The caller may
        supply an explicit ``entity_id``; if omitted a new ``uuid4`` is used.

        Args:
            data: A Pydantic model containing the entity's field values.
                Audit fields present in the model are stripped before insert.
            user_id: Optional identifier of the acting user, stored in
                ``created_by`` and ``updated_by``.
            entity_id: Optional explicit entity UUID.  Defaults to a new
                ``uuid4``.

        Returns:
            The newly created row as a ``dict`` or ``doc_model`` instance.
        """
        now = datetime.now(timezone.utc)
        eid = entity_id or uuid.uuid4()

        row_data = data.model_dump(exclude_unset=True)

        # Strip audit fields from input
        for f in _AUDIT_FIELD_NAMES:
            row_data.pop(f, None)
        row_data.pop("_id", None)

        row_data.update({
            "id": str(uuid.uuid4()),
            "entity_id": str(eid),
            "schema_version": "1.0.0",
            "record_version": 1,
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
            "created_by": user_id,
            "updated_by": user_id,
            "deleted_by": None,
        })

        # Serialise complex types
        row_data = self._serialise_complex(row_data)

        await self._session.execute(self._table.insert().values(**row_data))
        await self._session.flush()

        result = await self._session.execute(
            sa.select(self._table).where(self._table.c.id == row_data["id"])
        )
        return self._to_result(result.one())

    async def get_by_entity_id(
        self, entity_id: uuid.UUID
    ) -> dict[str, Any] | BaseModel | None:
        """Get the latest active version of an entity.

        Queries for the row with the highest ``record_version`` for the given
        ``entity_id`` and returns ``None`` if no such row exists or if the
        most recent version is a soft-deleted tombstone.

        Args:
            entity_id: UUID of the entity to retrieve.

        Returns:
            The latest non-deleted row as a ``dict`` or ``doc_model``
            instance, or ``None`` if the entity does not exist or is deleted.
        """
        eid = str(entity_id)
        result = await self._session.execute(
            sa.select(self._table)
            .where(self._table.c.entity_id == eid)
            .order_by(self._table.c.record_version.desc())
            .limit(1)
        )
        row = result.first()
        if row is None:
            return None

        data = dict(row._mapping)
        if data.get("deleted_at") is not None:
            return None

        return self._to_result(row)

    async def list_latest_active(
        self,
        skip: int = 0,
        limit: int = 100,
        sort_by: str = "created_at",
        sort_order: int = -1,
        filter_criteria: dict[str, Any] | None = None,
    ) -> list[dict[str, Any] | BaseModel]:
        """List the latest active version of every non-deleted entity.

        Uses a subquery to select only the highest ``record_version`` row per
        ``entity_id``, then filters out soft-deleted rows, applies optional
        equality filters, sorts, and paginates.

        Args:
            skip: Number of rows to skip (offset).  Defaults to ``0``.
            limit: Maximum number of rows to return.  Defaults to ``100``.
            sort_by: Column name to sort by.  Defaults to ``"created_at"``.
                Falls back to ``created_at`` if the column does not exist.
            sort_order: ``-1`` for descending (default), any other value for
                ascending.
            filter_criteria: Optional dict of ``{column_name: value}`` equality
                filters.  Unknown column names are silently ignored.

        Returns:
            A list of rows, each as a ``dict`` or ``doc_model`` instance.
            Returns an empty list when no matching entities exist.
        """
        # Subquery: latest record_version per entity_id
        latest_sub = (
            sa.select(
                self._table.c.entity_id,
                sa.func.max(self._table.c.record_version).label("max_rv"),
            )
            .group_by(self._table.c.entity_id)
            .subquery("latest")
        )

        query = (
            sa.select(self._table)
            .join(
                latest_sub,
                sa.and_(
                    self._table.c.entity_id == latest_sub.c.entity_id,
                    self._table.c.record_version == latest_sub.c.max_rv,
                ),
            )
            .where(self._table.c.deleted_at.is_(None))
        )

        if filter_criteria:
            for field_name, value in filter_criteria.items():
                if hasattr(self._table.c, field_name):
                    query = query.where(
                        getattr(self._table.c, field_name) == value
                    )

        # Sorting
        sort_col = getattr(self._table.c, sort_by, self._table.c.created_at)
        if sort_order == -1:
            query = query.order_by(sort_col.desc())
        else:
            query = query.order_by(sort_col.asc())

        query = query.offset(skip).limit(limit)

        result = await self._session.execute(query)
        return [self._to_result(row) for row in result.all()]

    async def count_active(
        self,
        filter_criteria: dict[str, Any] | None = None,
    ) -> int:
        """Count the total active (non-deleted) entities matching the filter."""
        latest_sub = (
            sa.select(
                self._table.c.entity_id,
                sa.func.max(self._table.c.record_version).label("max_rv"),
            )
            .group_by(self._table.c.entity_id)
            .subquery("latest")
        )

        query = (
            sa.select(sa.func.count())
            .select_from(self._table)
            .join(
                latest_sub,
                sa.and_(
                    self._table.c.entity_id == latest_sub.c.entity_id,
                    self._table.c.record_version == latest_sub.c.max_rv,
                ),
            )
            .where(self._table.c.deleted_at.is_(None))
        )

        if filter_criteria:
            for field_name, value in filter_criteria.items():
                if hasattr(self._table.c, field_name):
                    query = query.where(
                        getattr(self._table.c, field_name) == value
                    )

        result = await self._session.execute(query)
        return result.scalar() or 0

    async def update_by_entity_id(
        self,
        entity_id: uuid.UUID,
        data: BaseModel,
        user_id: str | None = None,
    ) -> dict[str, Any] | BaseModel | None:
        """Update an entity by inserting a new version row.

        Retrieves the current highest-version row, checks for actual field
        changes, and inserts a new row with an incremented ``record_version``.
        Returns ``None`` without writing if the entity does not exist, is
        soft-deleted, or the supplied data contains no changes.

        Args:
            entity_id: UUID of the entity to update.
            data: A Pydantic model carrying the fields to update.  Only fields
                with changed values (compared to the current version) are
                applied; audit fields are always ignored.
            user_id: Optional identifier of the acting user, stored in
                ``updated_by``.

        Returns:
            The newly inserted version row as a ``dict`` or ``doc_model``
            instance, or ``None`` if no update was performed.
        """
        now = datetime.now(timezone.utc)
        eid = str(entity_id)

        # Get latest version
        result = await self._session.execute(
            sa.select(self._table)
            .where(self._table.c.entity_id == eid)
            .order_by(self._table.c.record_version.desc())
            .limit(1)
        )
        current_row = result.first()
        if current_row is None:
            return None

        current = dict(current_row._mapping)
        if current.get("deleted_at") is not None:
            return None

        update_data = data.model_dump(exclude_unset=True)

        # Check for actual changes
        has_changed = any(
            current.get(k) != v
            for k, v in update_data.items()
            if k not in _AUDIT_FIELD_NAMES
        )
        if not has_changed:
            return None

        # Build new version
        new_row = dict(current)
        new_row.pop("id", None)

        for k, v in update_data.items():
            if k not in _AUDIT_FIELD_NAMES and k != "_id" and v is not None:
                new_row[k] = v

        new_row["id"] = str(uuid.uuid4())
        new_row["record_version"] = current["record_version"] + 1
        new_row["updated_at"] = now
        new_row["updated_by"] = user_id
        new_row["deleted_at"] = None
        new_row["deleted_by"] = None

        new_row = self._serialise_complex(new_row)

        await self._session.execute(self._table.insert().values(**new_row))
        await self._session.flush()

        result = await self._session.execute(
            sa.select(self._table).where(self._table.c.id == new_row["id"])
        )
        return self._to_result(result.one())

    async def delete_by_entity_id(
        self,
        entity_id: uuid.UUID,
        user_id: str | None = None,
    ) -> dict[str, Any] | BaseModel | None:
        """Soft-delete an entity by inserting a tombstone version row.

        Retrieves the current highest-version row and inserts a new row with
        an incremented ``record_version`` where ``deleted_at`` and
        ``deleted_by`` are set.  The entity is then invisible to
        ``get_by_entity_id`` and ``list_latest_active``.  Returns ``None``
        without writing if the entity does not exist or is already deleted.

        Args:
            entity_id: UUID of the entity to delete.
            user_id: Optional identifier of the acting user, stored in
                ``deleted_by`` and ``updated_by``.

        Returns:
            The tombstone row as a ``dict`` or ``doc_model`` instance, or
            ``None`` if the entity was not found or was already deleted.
        """
        now = datetime.now(timezone.utc)
        eid = str(entity_id)

        result = await self._session.execute(
            sa.select(self._table)
            .where(self._table.c.entity_id == eid)
            .order_by(self._table.c.record_version.desc())
            .limit(1)
        )
        current_row = result.first()
        if current_row is None:
            return None

        current = dict(current_row._mapping)
        if current.get("deleted_at") is not None:
            return None

        tombstone = dict(current)
        tombstone["id"] = str(uuid.uuid4())
        tombstone["record_version"] = current["record_version"] + 1
        tombstone["updated_at"] = now
        tombstone["updated_by"] = user_id
        tombstone["deleted_at"] = now
        tombstone["deleted_by"] = user_id

        tombstone = self._serialise_complex(tombstone)

        await self._session.execute(self._table.insert().values(**tombstone))
        await self._session.flush()

        result = await self._session.execute(
            sa.select(self._table).where(self._table.c.id == tombstone["id"])
        )
        return self._to_result(result.one())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _serialise_complex(row: dict[str, Any]) -> dict[str, Any]:
        """Serialise list/dict values to JSON strings for TEXT columns."""
        import json

        out = {}
        for k, v in row.items():
            if isinstance(v, (list, dict)):
                out[k] = json.dumps(v)
            elif isinstance(v, uuid.UUID):
                out[k] = str(v)
            else:
                out[k] = v
        return out
