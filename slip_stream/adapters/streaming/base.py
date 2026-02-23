"""Event streaming adapters for slip-stream.

Bridges the ``EventBus`` lifecycle to external event streaming systems
(Kafka, SQS, NATS, Redis Pub/Sub, etc.) so that CRUD events are
automatically published as messages.

Architecture:
- ``StreamAdapter`` protocol defines the publish interface
- ``InMemoryStream`` for testing without external dependencies
- ``EventStreamBridge`` plugs into EventBus and fans out to adapters

Usage::

    from slip_stream.adapters.streaming.base import (
        EventStreamBridge,
        InMemoryStream,
    )

    # In-memory for testing
    stream = InMemoryStream()
    bridge = EventStreamBridge(adapters=[stream])
    bridge.register(event_bus)

    # Events automatically published on create/update/delete
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class StreamAdapter(Protocol):
    """Protocol for event streaming backends."""

    async def publish(
        self,
        topic: str,
        key: str | None,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> None:
        """Publish an event message.

        Args:
            topic: The topic/queue/channel name.
            key: Optional partition key (e.g., entity_id).
            payload: The event payload dict.
            headers: Optional message headers.
        """
        ...

    async def close(self) -> None:
        """Close the adapter and release resources."""
        ...


# ---------------------------------------------------------------------------
# Event message
# ---------------------------------------------------------------------------


@dataclass
class StreamEvent:
    """An event message ready for publishing."""
    topic: str
    key: str | None
    payload: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# In-memory adapter (testing)
# ---------------------------------------------------------------------------


class InMemoryStream:
    """In-memory event stream for testing.

    Stores all published events in a list for inspection.
    """

    def __init__(self) -> None:
        self.events: list[StreamEvent] = []

    async def publish(
        self,
        topic: str,
        key: str | None,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> None:
        """Append a new StreamEvent to the in-memory events list."""
        event = StreamEvent(
            topic=topic,
            key=key,
            payload=payload,
            headers=headers or {},
        )
        self.events.append(event)

    async def close(self) -> None:
        """No-op close to satisfy the StreamAdapter protocol."""
        pass


# ---------------------------------------------------------------------------
# EventBus bridge
# ---------------------------------------------------------------------------


class EventStreamBridge:
    """Bridges EventBus lifecycle events to streaming adapters.

    Listens to post_create, post_update, post_delete events and
    publishes them to all registered stream adapters.

    Args:
        adapters: List of StreamAdapter instances to publish to.
        topic_prefix: Prefix for topic names (e.g., ``"slip-stream"``).
            Topics are formatted as ``{prefix}.{schema_name}.{operation}``.
        include_data: If True, include the entity data in the payload.
    """

    def __init__(
        self,
        adapters: list[Any] | None = None,
        topic_prefix: str = "slip-stream",
        include_data: bool = True,
    ) -> None:
        self._adapters: list[Any] = adapters or []
        self.topic_prefix = topic_prefix
        self.include_data = include_data

    def add_adapter(self, adapter: Any) -> None:
        """Add a streaming adapter."""
        self._adapters.append(adapter)

    def register(self, event_bus: Any) -> None:
        """Register event handlers on an EventBus."""
        event_bus.register("post_create", self._on_post_create)
        event_bus.register("post_update", self._on_post_update)
        event_bus.register("post_delete", self._on_post_delete)

    async def close(self) -> None:
        """Close all adapters."""
        for adapter in self._adapters:
            try:
                await adapter.close()
            except Exception as e:
                logger.error("Error closing stream adapter: %s", e)

    # ------------------------------------------------------------------
    # EventBus handlers
    # ------------------------------------------------------------------

    async def _on_post_create(self, ctx: Any) -> None:
        await self._publish_event("create", ctx)

    async def _on_post_update(self, ctx: Any) -> None:
        await self._publish_event("update", ctx)

    async def _on_post_delete(self, ctx: Any) -> None:
        await self._publish_event("delete", ctx)

    async def _publish_event(self, operation: str, ctx: Any) -> None:
        schema_name = getattr(ctx, "schema_name", "unknown")
        topic = f"{self.topic_prefix}.{schema_name}.{operation}"

        # Build entity key
        entity_id = getattr(ctx, "entity_id", None)
        if entity_id is None:
            result = getattr(ctx, "result", None)
            if result is not None:
                entity_id = getattr(result, "entity_id", None)
        key = str(entity_id) if entity_id else None

        # Build payload
        payload: dict[str, Any] = {
            "event": operation,
            "schema_name": schema_name,
            "entity_id": key,
            "timestamp": time.time(),
            "channel": getattr(ctx, "channel", "rest"),
        }

        # Extract user
        user = getattr(ctx, "current_user", None)
        if isinstance(user, dict):
            payload["user_id"] = user.get("id")
        elif user is not None:
            payload["user_id"] = getattr(user, "id", None)

        # Include data if requested
        if self.include_data:
            data = getattr(ctx, "data", None)
            if data is not None:
                if hasattr(data, "model_dump"):
                    payload["data"] = data.model_dump(exclude_unset=True)
                elif isinstance(data, dict):
                    payload["data"] = data

        headers = {
            "x-event-type": operation,
            "x-schema-name": schema_name,
        }

        for adapter in self._adapters:
            try:
                await adapter.publish(
                    topic=topic,
                    key=key,
                    payload=payload,
                    headers=headers,
                )
            except Exception as e:
                logger.error(
                    "Failed to publish to %s: %s",
                    type(adapter).__name__, e,
                )
