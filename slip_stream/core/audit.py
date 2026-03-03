"""Audit trail system for slip-stream.

Automatically records every CRUD operation with who did what, when,
and what changed.  Plugs into the ``EventBus`` lifecycle hooks so
it works identically across REST and GraphQL transports.

Usage::

    from slip_stream.core.audit import AuditTrail

    audit = AuditTrail(collection_name="audit_log")

    # Register with an EventBus
    audit.register(event_bus)

    # Or use standalone
    await audit.record(
        operation="create",
        schema_name="widget",
        entity_id="abc-123",
        user_id="user-1",
        changes={"name": "Widget A"},
        db=db,
    )

    # Query audit history
    history = await audit.get_history(
        entity_id="abc-123", db=db
    )
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


class AuditEntry:
    """A single audit log entry.

    Attributes:
        timestamp: When the operation occurred (UTC).
        operation: CRUD operation (create, get, list, update, delete).
        schema_name: The entity type.
        entity_id: The entity's UUID (as string).
        user_id: Who performed the operation.
        channel: Transport channel (rest, graphql).
        changes: Dict of field changes (for create/update).
        metadata: Arbitrary extra context.
    """

    __slots__ = (
        "timestamp",
        "operation",
        "schema_name",
        "entity_id",
        "user_id",
        "channel",
        "changes",
        "metadata",
    )

    def __init__(
        self,
        operation: str,
        schema_name: str,
        entity_id: Optional[str] = None,
        user_id: Optional[str] = None,
        channel: str = "rest",
        changes: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        self.timestamp = datetime.now(timezone.utc)
        self.operation = operation
        self.schema_name = schema_name
        self.entity_id = str(entity_id) if entity_id else None
        self.user_id = user_id
        self.channel = channel
        self.changes = changes or {}
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert the audit entry to a plain dictionary.

        Returns:
            A dict representation of all entry fields, suitable for serialization.
        """
        return {
            "timestamp": self.timestamp,
            "operation": self.operation,
            "schema_name": self.schema_name,
            "entity_id": self.entity_id,
            "user_id": self.user_id,
            "channel": self.channel,
            "changes": self.changes,
            "metadata": self.metadata,
        }


class AuditTrail:
    """Records CRUD operations as audit log entries.

    Can persist to MongoDB or hold entries in memory (for testing).

    Args:
        collection_name: MongoDB collection for audit logs.
        in_memory: If True, stores entries in a list instead of MongoDB.
            Useful for testing.
        track_reads: If True, also records get/list operations.
            Defaults to False (only writes are audited).
    """

    def __init__(
        self,
        collection_name: str = "audit_log",
        in_memory: bool = False,
        track_reads: bool = False,
    ) -> None:
        self.collection_name = collection_name
        self.in_memory = in_memory
        self.track_reads = track_reads
        self._entries: list[dict[str, Any]] = []

    def register(self, event_bus: Any) -> None:
        """Register audit hooks on an EventBus.

        Listens to all post_* events so auditing happens after
        successful operations.
        """
        event_bus.register("post_create", self._on_post_create)
        event_bus.register("post_update", self._on_post_update)
        event_bus.register("post_delete", self._on_post_delete)

        if self.track_reads:
            event_bus.register("post_get", self._on_post_get)
            event_bus.register("post_list", self._on_post_list)

    async def record(
        self,
        operation: str,
        schema_name: str,
        entity_id: Optional[str] = None,
        user_id: Optional[str] = None,
        channel: str = "rest",
        changes: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
        db: Any = None,
    ) -> AuditEntry:
        """Record an audit entry.

        Args:
            operation: The CRUD operation name.
            schema_name: Entity type.
            entity_id: Entity ID.
            user_id: Who performed it.
            channel: Transport channel.
            changes: What changed.
            metadata: Extra context.
            db: MongoDB database (ignored if in_memory).

        Returns:
            The created AuditEntry.
        """
        entry = AuditEntry(
            operation=operation,
            schema_name=schema_name,
            entity_id=entity_id,
            user_id=user_id,
            channel=channel,
            changes=changes,
            metadata=metadata,
        )

        entry_dict = entry.to_dict()

        if self.in_memory:
            self._entries.append(entry_dict)
        elif db is not None:
            try:
                await db[self.collection_name].insert_one(entry_dict)
            except Exception as e:
                logger.error("Failed to write audit entry: %s", e)
        else:
            logger.warning("No db provided and not in_memory — audit entry lost")

        logger.debug(
            "Audit: %s %s/%s by %s",
            operation,
            schema_name,
            entity_id,
            user_id,
        )
        return entry

    async def get_history(
        self,
        entity_id: str,
        schema_name: Optional[str] = None,
        db: Any = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get audit history for an entity.

        Args:
            entity_id: The entity to look up.
            schema_name: Optional filter by entity type.
            db: MongoDB database (ignored if in_memory).
            limit: Max entries to return.

        Returns:
            List of audit entry dicts, newest first.
        """
        if self.in_memory:
            results = [
                e
                for e in self._entries
                if e["entity_id"] == str(entity_id)
                and (schema_name is None or e["schema_name"] == schema_name)
            ]
            return sorted(results, key=lambda e: e["timestamp"], reverse=True)[:limit]

        if db is None:
            return []

        query: dict[str, Any] = {"entity_id": str(entity_id)}
        if schema_name:
            query["schema_name"] = schema_name

        cursor = db[self.collection_name].find(query).sort("timestamp", -1).limit(limit)
        return [doc async for doc in cursor]

    async def get_user_activity(
        self,
        user_id: str,
        db: Any = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get all audit entries for a specific user.

        Args:
            user_id: The user whose activity to retrieve.
            db: MongoDB database (ignored if in_memory).
            limit: Maximum number of entries to return. Defaults to 100.

        Returns:
            List of audit entry dicts, newest first.
        """
        if self.in_memory:
            results = [e for e in self._entries if e["user_id"] == user_id]
            return sorted(results, key=lambda e: e["timestamp"], reverse=True)[:limit]

        if db is None:
            return []

        cursor = (
            db[self.collection_name]
            .find({"user_id": user_id})
            .sort("timestamp", -1)
            .limit(limit)
        )
        return [doc async for doc in cursor]

    # ------------------------------------------------------------------
    # EventBus handlers
    # ------------------------------------------------------------------

    def _extract_user_id(self, ctx: Any) -> str:
        user = getattr(ctx, "current_user", None)
        if user is None:
            return "anonymous"
        if isinstance(user, dict):
            return user.get("id", "anonymous")
        return getattr(user, "id", "anonymous")

    def _extract_changes(self, ctx: Any) -> dict[str, Any]:
        data = getattr(ctx, "data", None)
        if data is None:
            return {}
        if hasattr(data, "model_dump"):
            return data.model_dump(exclude_unset=True)
        if hasattr(data, "dict"):
            return data.dict(exclude_unset=True)
        if isinstance(data, dict):
            return data
        return {}

    async def _on_post_create(self, ctx: Any) -> None:
        entity_id = None
        result = getattr(ctx, "result", None)
        if result is not None:
            entity_id = getattr(result, "entity_id", None)

        await self.record(
            operation="create",
            schema_name=ctx.schema_name,
            entity_id=str(entity_id) if entity_id else None,
            user_id=self._extract_user_id(ctx),
            channel=getattr(ctx, "channel", "rest"),
            changes=self._extract_changes(ctx),
            db=getattr(ctx, "db", None),
        )

    async def _on_post_update(self, ctx: Any) -> None:
        entity_id = getattr(ctx, "entity_id", None)
        await self.record(
            operation="update",
            schema_name=ctx.schema_name,
            entity_id=str(entity_id) if entity_id else None,
            user_id=self._extract_user_id(ctx),
            channel=getattr(ctx, "channel", "rest"),
            changes=self._extract_changes(ctx),
            db=getattr(ctx, "db", None),
        )

    async def _on_post_delete(self, ctx: Any) -> None:
        entity_id = getattr(ctx, "entity_id", None)
        await self.record(
            operation="delete",
            schema_name=ctx.schema_name,
            entity_id=str(entity_id) if entity_id else None,
            user_id=self._extract_user_id(ctx),
            channel=getattr(ctx, "channel", "rest"),
            db=getattr(ctx, "db", None),
        )

    async def _on_post_get(self, ctx: Any) -> None:
        entity_id = getattr(ctx, "entity_id", None)
        await self.record(
            operation="get",
            schema_name=ctx.schema_name,
            entity_id=str(entity_id) if entity_id else None,
            user_id=self._extract_user_id(ctx),
            channel=getattr(ctx, "channel", "rest"),
            db=getattr(ctx, "db", None),
        )

    async def _on_post_list(self, ctx: Any) -> None:
        await self.record(
            operation="list",
            schema_name=ctx.schema_name,
            user_id=self._extract_user_id(ctx),
            channel=getattr(ctx, "channel", "rest"),
            db=getattr(ctx, "db", None),
        )
