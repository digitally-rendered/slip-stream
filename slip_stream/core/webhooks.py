"""Webhook outbound system for slip-stream.

Sends HTTP notifications to registered webhook URLs when CRUD events
occur.  Plugs into the ``EventBus`` lifecycle hooks.

Features:
- Register webhooks per entity type and operation
- Async delivery with configurable retry
- HMAC-SHA256 signature for payload verification
- Configurable timeout and headers

Usage::

    from slip_stream.core.webhooks import WebhookDispatcher

    webhooks = WebhookDispatcher()

    # Register a webhook
    webhooks.add(
        url="https://example.com/hook",
        schema_name="widget",
        events=["create", "update", "delete"],
        secret="my-secret",  # for HMAC signing
    )

    # Plug into EventBus
    webhooks.register(event_bus)

    # Webhooks fire automatically on post_create, post_update, post_delete
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class WebhookRegistration:
    """A registered webhook endpoint."""

    url: str
    schema_name: str
    events: list[str]
    secret: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 10.0
    max_retries: int = 3
    active: bool = True


@dataclass
class WebhookDelivery:
    """Record of a webhook delivery attempt."""

    webhook_url: str
    event: str
    schema_name: str
    entity_id: Optional[str]
    status_code: Optional[int]
    success: bool
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


class WebhookDispatcher:
    """Dispatches webhook notifications on CRUD events.

    Args:
        in_memory: If True, records deliveries in-memory instead of
            sending real HTTP requests. Useful for testing.
    """

    def __init__(self, in_memory: bool = False) -> None:
        self._webhooks: list[WebhookRegistration] = []
        self._deliveries: list[WebhookDelivery] = []
        self.in_memory = in_memory

    def add(
        self,
        url: str,
        schema_name: str = "*",
        events: Optional[list[str]] = None,
        secret: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: float = 10.0,
        max_retries: int = 3,
    ) -> WebhookRegistration:
        """Register a webhook.

        Args:
            url: The endpoint URL to POST to.
            schema_name: Entity type to filter (``"*"`` for all).
            events: List of events (create, update, delete). Defaults to all.
            secret: HMAC-SHA256 secret for signing payloads.
            headers: Extra HTTP headers.
            timeout: Request timeout in seconds.
            max_retries: Number of retries on failure.

        Returns:
            The created WebhookRegistration.
        """
        reg = WebhookRegistration(
            url=url,
            schema_name=schema_name,
            events=events or ["create", "update", "delete"],
            secret=secret,
            headers=headers or {},
            timeout=timeout,
            max_retries=max_retries,
        )
        self._webhooks.append(reg)
        return reg

    def remove(self, url: str) -> None:
        """Remove all webhooks for a URL."""
        self._webhooks = [w for w in self._webhooks if w.url != url]

    def register(self, event_bus: Any) -> None:
        """Register webhook handlers on an EventBus."""
        event_bus.register("post_create", self._on_post_create)
        event_bus.register("post_update", self._on_post_update)
        event_bus.register("post_delete", self._on_post_delete)

    @property
    def deliveries(self) -> list[WebhookDelivery]:
        """Get delivery history (in-memory mode)."""
        return list(self._deliveries)

    # ------------------------------------------------------------------
    # Payload building
    # ------------------------------------------------------------------

    @staticmethod
    def _build_payload(
        event: str,
        schema_name: str,
        entity_id: Optional[str],
        changes: Optional[dict[str, Any]],
        user_id: Optional[str],
        channel: str,
    ) -> dict[str, Any]:
        return {
            "event": event,
            "schema_name": schema_name,
            "entity_id": entity_id,
            "changes": changes or {},
            "user_id": user_id,
            "channel": channel,
            "timestamp": time.time(),
        }

    @staticmethod
    def _sign_payload(payload_bytes: bytes, secret: str) -> str:
        """Compute HMAC-SHA256 signature."""
        return hmac.new(
            secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    async def _deliver(
        self,
        webhook: WebhookRegistration,
        payload: dict[str, Any],
    ) -> WebhookDelivery:
        """Deliver a webhook payload."""
        payload_bytes = json.dumps(payload, default=str).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Event": payload["event"],
            "X-Webhook-Schema": payload["schema_name"],
            **webhook.headers,
        }

        if webhook.secret:
            sig = self._sign_payload(payload_bytes, webhook.secret)
            headers["X-Webhook-Signature"] = f"sha256={sig}"

        if self.in_memory:
            delivery = WebhookDelivery(
                webhook_url=webhook.url,
                event=payload["event"],
                schema_name=payload["schema_name"],
                entity_id=payload.get("entity_id"),
                status_code=200,
                success=True,
            )
            self._deliveries.append(delivery)
            return delivery

        # Real HTTP delivery with retry
        import httpx

        last_error = None
        for attempt in range(webhook.max_retries):
            try:
                async with httpx.AsyncClient(timeout=webhook.timeout) as client:
                    resp = await client.post(
                        webhook.url,
                        content=payload_bytes,
                        headers=headers,
                    )
                    delivery = WebhookDelivery(
                        webhook_url=webhook.url,
                        event=payload["event"],
                        schema_name=payload["schema_name"],
                        entity_id=payload.get("entity_id"),
                        status_code=resp.status_code,
                        success=200 <= resp.status_code < 300,
                    )
                    self._deliveries.append(delivery)
                    if delivery.success:
                        return delivery
                    last_error = f"HTTP {resp.status_code}"
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "Webhook delivery attempt %d/%d failed: %s",
                    attempt + 1,
                    webhook.max_retries,
                    e,
                )

        delivery = WebhookDelivery(
            webhook_url=webhook.url,
            event=payload["event"],
            schema_name=payload["schema_name"],
            entity_id=payload.get("entity_id"),
            status_code=None,
            success=False,
            error=last_error,
        )
        self._deliveries.append(delivery)
        return delivery

    def _matching_webhooks(
        self, event: str, schema_name: str
    ) -> list[WebhookRegistration]:
        """Find webhooks matching an event and schema."""
        return [
            w
            for w in self._webhooks
            if w.active
            and event in w.events
            and (w.schema_name == "*" or w.schema_name == schema_name)
        ]

    # ------------------------------------------------------------------
    # EventBus handlers
    # ------------------------------------------------------------------

    def _extract_ctx(
        self, ctx: Any
    ) -> tuple[str, Optional[str], Optional[dict], Optional[str], str]:
        """Extract common fields from a RequestContext."""
        schema_name = getattr(ctx, "schema_name", "unknown")
        entity_id = getattr(ctx, "entity_id", None)
        if entity_id is not None:
            entity_id = str(entity_id)

        # Try to get entity_id from result for create operations
        if entity_id is None:
            result = getattr(ctx, "result", None)
            if result is not None:
                eid = getattr(result, "entity_id", None)
                if eid is not None:
                    entity_id = str(eid)

        # Extract changes
        data = getattr(ctx, "data", None)
        changes = None
        if data is not None:
            if hasattr(data, "model_dump"):
                changes = data.model_dump(exclude_unset=True)
            elif hasattr(data, "dict"):
                changes = data.dict(exclude_unset=True)
            elif isinstance(data, dict):
                changes = data

        # Extract user
        user = getattr(ctx, "current_user", None)
        user_id = None
        if isinstance(user, dict):
            user_id = user.get("id")
        elif user is not None:
            user_id = getattr(user, "id", None)

        channel = getattr(ctx, "channel", "rest")

        return schema_name, entity_id, changes, user_id, channel

    async def _dispatch(self, event: str, ctx: Any) -> None:
        schema_name, entity_id, changes, user_id, channel = self._extract_ctx(ctx)
        webhooks = self._matching_webhooks(event, schema_name)

        if not webhooks:
            return

        payload = self._build_payload(
            event=event,
            schema_name=schema_name,
            entity_id=entity_id,
            changes=changes,
            user_id=user_id,
            channel=channel,
        )

        for webhook in webhooks:
            try:
                await self._deliver(webhook, payload)
            except Exception as e:
                logger.error("Webhook delivery failed: %s", e)

    async def _on_post_create(self, ctx: Any) -> None:
        await self._dispatch("create", ctx)

    async def _on_post_update(self, ctx: Any) -> None:
        await self._dispatch("update", ctx)

    async def _on_post_delete(self, ctx: Any) -> None:
        await self._dispatch("delete", ctx)
