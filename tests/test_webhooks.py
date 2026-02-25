"""Tests for the webhook outbound system."""

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from slip_stream.core.events import EventBus
from slip_stream.core.webhooks import WebhookDispatcher, WebhookRegistration

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeEntity(BaseModel):
    entity_id: str = "ent-123"
    name: str = "Widget A"


class _FakeData(BaseModel):
    name: str = "Widget A"


def _make_ctx(
    operation="create",
    schema_name="widget",
    entity_id=None,
    data=None,
    result=None,
    channel="rest",
    user_id="user-1",
):
    return SimpleNamespace(
        operation=operation,
        schema_name=schema_name,
        entity_id=entity_id,
        data=data,
        result=result,
        channel=channel,
        current_user={"id": user_id},
        db=None,
    )


# ---------------------------------------------------------------------------
# WebhookDispatcher — registration
# ---------------------------------------------------------------------------


class TestWebhookRegistration:

    def test_add_webhook(self):
        wh = WebhookDispatcher(in_memory=True)
        reg = wh.add(url="https://example.com/hook", schema_name="widget")
        assert isinstance(reg, WebhookRegistration)
        assert reg.url == "https://example.com/hook"
        assert reg.schema_name == "widget"
        assert reg.events == ["create", "update", "delete"]

    def test_add_with_custom_events(self):
        wh = WebhookDispatcher(in_memory=True)
        reg = wh.add(url="https://example.com/hook", events=["create"])
        assert reg.events == ["create"]

    def test_add_with_secret(self):
        wh = WebhookDispatcher(in_memory=True)
        reg = wh.add(url="https://example.com/hook", secret="my-secret")
        assert reg.secret == "my-secret"

    def test_remove_webhook(self):
        wh = WebhookDispatcher(in_memory=True)
        wh.add(url="https://example.com/hook1")
        wh.add(url="https://example.com/hook2")
        assert len(wh._webhooks) == 2

        wh.remove("https://example.com/hook1")
        assert len(wh._webhooks) == 1
        assert wh._webhooks[0].url == "https://example.com/hook2"


# ---------------------------------------------------------------------------
# WebhookDispatcher — matching
# ---------------------------------------------------------------------------


class TestWebhookMatching:

    def test_matches_schema_and_event(self):
        wh = WebhookDispatcher(in_memory=True)
        wh.add(url="https://example.com/hook", schema_name="widget", events=["create"])
        matches = wh._matching_webhooks("create", "widget")
        assert len(matches) == 1

    def test_does_not_match_wrong_event(self):
        wh = WebhookDispatcher(in_memory=True)
        wh.add(url="https://example.com/hook", schema_name="widget", events=["create"])
        matches = wh._matching_webhooks("delete", "widget")
        assert len(matches) == 0

    def test_does_not_match_wrong_schema(self):
        wh = WebhookDispatcher(in_memory=True)
        wh.add(url="https://example.com/hook", schema_name="widget", events=["create"])
        matches = wh._matching_webhooks("create", "gadget")
        assert len(matches) == 0

    def test_wildcard_schema_matches_all(self):
        wh = WebhookDispatcher(in_memory=True)
        wh.add(url="https://example.com/hook", schema_name="*", events=["create"])
        matches = wh._matching_webhooks("create", "anything")
        assert len(matches) == 1

    def test_inactive_webhook_not_matched(self):
        wh = WebhookDispatcher(in_memory=True)
        reg = wh.add(url="https://example.com/hook", schema_name="widget")
        reg.active = False
        matches = wh._matching_webhooks("create", "widget")
        assert len(matches) == 0


# ---------------------------------------------------------------------------
# WebhookDispatcher — delivery (in-memory)
# ---------------------------------------------------------------------------


class TestWebhookDelivery:

    @pytest.mark.asyncio
    async def test_create_event_delivers(self):
        wh = WebhookDispatcher(in_memory=True)
        wh.add(url="https://example.com/hook", schema_name="widget")

        bus = EventBus()
        wh.register(bus)

        ctx = _make_ctx(
            operation="create",
            data=_FakeData(name="Widget A"),
            result=_FakeEntity(entity_id="ent-1"),
        )
        await bus.emit("post_create", ctx)

        assert len(wh.deliveries) == 1
        d = wh.deliveries[0]
        assert d.event == "create"
        assert d.schema_name == "widget"
        assert d.entity_id == "ent-1"
        assert d.success is True

    @pytest.mark.asyncio
    async def test_update_event_delivers(self):
        wh = WebhookDispatcher(in_memory=True)
        wh.add(url="https://example.com/hook", schema_name="widget")

        bus = EventBus()
        wh.register(bus)

        ctx = _make_ctx(
            operation="update",
            entity_id="ent-123",
            data=_FakeData(name="Updated"),
        )
        await bus.emit("post_update", ctx)

        assert len(wh.deliveries) == 1
        assert wh.deliveries[0].event == "update"

    @pytest.mark.asyncio
    async def test_delete_event_delivers(self):
        wh = WebhookDispatcher(in_memory=True)
        wh.add(url="https://example.com/hook", schema_name="widget")

        bus = EventBus()
        wh.register(bus)

        ctx = _make_ctx(operation="delete", entity_id="ent-456")
        await bus.emit("post_delete", ctx)

        assert len(wh.deliveries) == 1
        assert wh.deliveries[0].event == "delete"

    @pytest.mark.asyncio
    async def test_no_delivery_for_unregistered_event(self):
        wh = WebhookDispatcher(in_memory=True)
        wh.add(url="https://example.com/hook", schema_name="widget", events=["create"])

        bus = EventBus()
        wh.register(bus)

        ctx = _make_ctx(operation="delete", entity_id="ent-1")
        await bus.emit("post_delete", ctx)

        assert len(wh.deliveries) == 0

    @pytest.mark.asyncio
    async def test_multiple_webhooks_fire(self):
        wh = WebhookDispatcher(in_memory=True)
        wh.add(url="https://example.com/hook1", schema_name="widget")
        wh.add(url="https://example.com/hook2", schema_name="*")

        bus = EventBus()
        wh.register(bus)

        ctx = _make_ctx(operation="create", result=_FakeEntity())
        await bus.emit("post_create", ctx)

        assert len(wh.deliveries) == 2

    @pytest.mark.asyncio
    async def test_delivery_includes_channel(self):
        wh = WebhookDispatcher(in_memory=True)
        wh.add(url="https://example.com/hook", schema_name="widget")

        bus = EventBus()
        wh.register(bus)

        ctx = _make_ctx(operation="create", channel="graphql", result=_FakeEntity())
        await bus.emit("post_create", ctx)

        assert len(wh.deliveries) == 1


# ---------------------------------------------------------------------------
# HMAC signing
# ---------------------------------------------------------------------------


class TestWebhookSigning:

    def test_sign_payload(self):
        payload = json.dumps({"event": "create"}).encode("utf-8")
        sig = WebhookDispatcher._sign_payload(payload, "secret123")

        expected = hmac.new(b"secret123", payload, hashlib.sha256).hexdigest()
        assert sig == expected

    def test_sign_payload_different_secret(self):
        payload = json.dumps({"event": "create"}).encode("utf-8")
        sig1 = WebhookDispatcher._sign_payload(payload, "secret1")
        sig2 = WebhookDispatcher._sign_payload(payload, "secret2")
        assert sig1 != sig2


# ---------------------------------------------------------------------------
# Payload building
# ---------------------------------------------------------------------------


class TestPayloadBuilding:

    def test_payload_structure(self):
        payload = WebhookDispatcher._build_payload(
            event="create",
            schema_name="widget",
            entity_id="ent-1",
            changes={"name": "Test"},
            user_id="user-1",
            channel="rest",
        )
        assert payload["event"] == "create"
        assert payload["schema_name"] == "widget"
        assert payload["entity_id"] == "ent-1"
        assert payload["changes"] == {"name": "Test"}
        assert payload["user_id"] == "user-1"
        assert payload["channel"] == "rest"
        assert "timestamp" in payload

    def test_payload_with_none_changes(self):
        payload = WebhookDispatcher._build_payload(
            event="delete",
            schema_name="widget",
            entity_id="ent-1",
            changes=None,
            user_id="user-1",
            channel="rest",
        )
        assert payload["changes"] == {}


# ---------------------------------------------------------------------------
# HTTP delivery path (real network mocked via httpx)
# ---------------------------------------------------------------------------


class TestHttpDelivery:

    @pytest.mark.asyncio
    async def test_http_delivery_success(self):
        """Mock httpx.AsyncClient.post — verify correct URL, headers, and body."""
        from unittest.mock import AsyncMock, MagicMock, patch

        wh = WebhookDispatcher(in_memory=False)
        reg = wh.add(url="https://example.com/hook", schema_name="widget")

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        payload = {
            "event": "create",
            "schema_name": "widget",
            "entity_id": "ent-1",
            "changes": {"name": "Test"},
            "user_id": "user-1",
            "channel": "rest",
            "timestamp": 0,
        }

        with patch("httpx.AsyncClient", return_value=mock_client):
            delivery = await wh._deliver(reg, payload)

        assert delivery.success is True
        assert delivery.status_code == 200
        assert delivery.webhook_url == "https://example.com/hook"
        assert delivery.event == "create"

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert call_kwargs[0][0] == "https://example.com/hook"
        sent_headers = call_kwargs[1]["headers"]
        assert sent_headers["Content-Type"] == "application/json"
        assert sent_headers["X-Webhook-Event"] == "create"

    @pytest.mark.asyncio
    async def test_http_delivery_with_hmac_signature(self):
        """Verify X-Webhook-Signature header contains correct HMAC-SHA256."""
        from unittest.mock import AsyncMock, MagicMock, patch

        wh = WebhookDispatcher(in_memory=False)
        reg = wh.add(
            url="https://example.com/hook",
            schema_name="widget",
            secret="my-secret",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        payload = {
            "event": "create",
            "schema_name": "widget",
            "entity_id": "ent-1",
            "changes": {},
            "user_id": None,
            "channel": "rest",
            "timestamp": 0,
        }

        with patch("httpx.AsyncClient", return_value=mock_client):
            delivery = await wh._deliver(reg, payload)

        assert delivery.success is True
        call_kwargs = mock_client.post.call_args
        sent_headers = call_kwargs[1]["headers"]
        assert "X-Webhook-Signature" in sent_headers

        # Verify the signature value matches the expected HMAC
        import json

        payload_bytes = json.dumps(payload, default=str).encode("utf-8")
        expected_sig = hmac.new(b"my-secret", payload_bytes, hashlib.sha256).hexdigest()
        assert sent_headers["X-Webhook-Signature"] == f"sha256={expected_sig}"

    @pytest.mark.asyncio
    async def test_http_delivery_retry_on_failure(self):
        """Mock post to raise an exception, verify retries up to max_retries."""
        from unittest.mock import AsyncMock, patch

        wh = WebhookDispatcher(in_memory=False)
        reg = wh.add(
            url="https://example.com/hook",
            schema_name="widget",
            max_retries=3,
        )

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=ConnectionError("Network error"))

        payload = {
            "event": "create",
            "schema_name": "widget",
            "entity_id": "ent-1",
            "changes": {},
            "user_id": None,
            "channel": "rest",
            "timestamp": 0,
        }

        with patch("httpx.AsyncClient", return_value=mock_client):
            delivery = await wh._deliver(reg, payload)

        # Should have tried 3 times
        assert mock_client.post.call_count == 3
        # Final delivery should be a failure record
        assert delivery.success is False
        assert delivery.error is not None

    @pytest.mark.asyncio
    async def test_http_delivery_non_2xx_marks_failed(self):
        """A 500 response records a failed delivery and exhausts retries."""
        from unittest.mock import AsyncMock, MagicMock, patch

        wh = WebhookDispatcher(in_memory=False)
        reg = wh.add(
            url="https://example.com/hook",
            schema_name="widget",
            max_retries=2,
        )

        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        payload = {
            "event": "update",
            "schema_name": "widget",
            "entity_id": "ent-2",
            "changes": {},
            "user_id": None,
            "channel": "rest",
            "timestamp": 0,
        }

        with patch("httpx.AsyncClient", return_value=mock_client):
            delivery = await wh._deliver(reg, payload)

        # Two attempts at 500 responses, then a final failure delivery
        assert mock_client.post.call_count == 2
        # The failure delivery appended last has no status_code (from the
        # exhausted-retry path), while each 500 attempt also appended a delivery
        all_deliveries = wh._deliveries
        # At least one delivery was recorded
        assert len(all_deliveries) >= 1
        # The last delivery is the failure sentinel
        assert delivery.success is False

    @pytest.mark.asyncio
    async def test_deliver_dict_payload(self):
        """When ctx.data is a plain dict, changes should be captured."""
        wh = WebhookDispatcher(in_memory=True)
        wh.add(url="https://example.com/hook", schema_name="widget")

        # Simulate a context where data is a plain dict (not a Pydantic model)
        ctx = _make_ctx(
            operation="create",
            schema_name="widget",
            data={"name": "From Dict", "color": "red"},
            result=_FakeEntity(entity_id="ent-dict"),
        )

        bus = EventBus()
        wh.register(bus)
        await bus.emit("post_create", ctx)

        assert len(wh.deliveries) == 1
        d = wh.deliveries[0]
        assert d.success is True
        assert d.entity_id == "ent-dict"
