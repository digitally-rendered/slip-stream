"""Tests for the Rego/OPA policy engine and filter."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.datastructures import Headers

from slip_stream.adapters.api.filters.base import FilterContext, FilterShortCircuit
from slip_stream.adapters.api.filters.rego import RegoPolicyFilter
from slip_stream.core.policy import (
    InlinePolicy,
    OpaRemotePolicy,
    PolicyEvaluationError,
)


# ---------------------------------------------------------------------------
# InlinePolicy
# ---------------------------------------------------------------------------


class TestInlinePolicy:

    @pytest.mark.asyncio
    async def test_basic_allow(self):
        engine = InlinePolicy()

        @engine.rule("authz/allow")
        def allow(input_data):
            return True

        assert await engine.evaluate("authz/allow", {}) is True

    @pytest.mark.asyncio
    async def test_basic_deny(self):
        engine = InlinePolicy()

        @engine.rule("authz/allow")
        def deny(input_data):
            return False

        assert await engine.evaluate("authz/allow", {}) is False

    @pytest.mark.asyncio
    async def test_conditional_policy(self):
        engine = InlinePolicy()

        @engine.rule("widget/create")
        def check_role(input_data):
            return input_data.get("user", {}).get("role") == "admin"

        assert await engine.evaluate("widget/create", {"user": {"role": "admin"}}) is True
        assert await engine.evaluate("widget/create", {"user": {"role": "viewer"}}) is False

    @pytest.mark.asyncio
    async def test_async_rule(self):
        engine = InlinePolicy()

        @engine.rule("async/check")
        async def async_check(input_data):
            return input_data.get("allowed", False)

        assert await engine.evaluate("async/check", {"allowed": True}) is True
        assert await engine.evaluate("async/check", {"allowed": False}) is False

    @pytest.mark.asyncio
    async def test_missing_rule_returns_false(self):
        engine = InlinePolicy()
        assert await engine.evaluate("nonexistent/rule", {}) is False

    @pytest.mark.asyncio
    async def test_register_rule_imperatively(self):
        engine = InlinePolicy()
        engine.register_rule("test/rule", lambda d: d.get("ok", False))
        assert await engine.evaluate("test/rule", {"ok": True}) is True

    @pytest.mark.asyncio
    async def test_dot_notation_matches_slash(self):
        engine = InlinePolicy()

        @engine.rule("my.policy.path")
        def check(input_data):
            return True

        # Dot-separated should be normalized to slash
        assert await engine.evaluate("my/policy/path", {}) is True

    @pytest.mark.asyncio
    async def test_evaluate_raw(self):
        engine = InlinePolicy()

        @engine.rule("test/raw")
        def check(input_data):
            return True

        result = await engine.evaluate_raw("test/raw", {})
        assert result == {"result": True}

    @pytest.mark.asyncio
    async def test_method_based_policy(self):
        engine = InlinePolicy()

        @engine.rule("api/access")
        def no_deletes(input_data):
            return input_data.get("method") != "DELETE"

        assert await engine.evaluate("api/access", {"method": "GET"}) is True
        assert await engine.evaluate("api/access", {"method": "POST"}) is True
        assert await engine.evaluate("api/access", {"method": "DELETE"}) is False


# ---------------------------------------------------------------------------
# OpaRemotePolicy
# ---------------------------------------------------------------------------


class TestOpaRemotePolicy:

    def _mock_opa_response(self, data):
        """Create a mock httpx response."""
        resp = MagicMock()
        resp.json.return_value = data
        resp.raise_for_status = MagicMock()
        return resp

    @pytest.mark.asyncio
    async def test_evaluate_sends_correct_request(self):
        engine = OpaRemotePolicy(url="http://opa:8181")

        mock_resp = self._mock_opa_response({"result": True})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        engine._client = mock_client

        result = await engine.evaluate("authz/allow", {"user": "admin"})

        assert result is True
        mock_client.post.assert_called_once_with(
            "http://opa:8181/v1/data/authz/allow",
            json={"input": {"user": "admin"}},
        )

    @pytest.mark.asyncio
    async def test_evaluate_deny(self):
        engine = OpaRemotePolicy(url="http://opa:8181")

        mock_resp = self._mock_opa_response({"result": False})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        engine._client = mock_client

        result = await engine.evaluate("authz/allow", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_evaluate_dict_result_with_allow_key(self):
        engine = OpaRemotePolicy(url="http://opa:8181")

        mock_resp = self._mock_opa_response({"result": {"allow": True, "reason": "ok"}})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        engine._client = mock_client

        result = await engine.evaluate("authz/allow", {})
        assert result is True

    @pytest.mark.asyncio
    async def test_evaluate_connection_error_raises(self):
        engine = OpaRemotePolicy(url="http://opa:8181")

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
        engine._client = mock_client

        with pytest.raises(PolicyEvaluationError, match="connection refused"):
            await engine.evaluate("authz/allow", {})

    @pytest.mark.asyncio
    async def test_dot_notation_converted_to_slash(self):
        engine = OpaRemotePolicy(url="http://opa:8181")

        mock_resp = self._mock_opa_response({"result": True})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        engine._client = mock_client

        await engine.evaluate("authz.allow", {})

        mock_client.post.assert_called_once_with(
            "http://opa:8181/v1/data/authz/allow",
            json={"input": {}},
        )

    @pytest.mark.asyncio
    async def test_close(self):
        engine = OpaRemotePolicy(url="http://opa:8181")
        mock_client = AsyncMock()
        engine._client = mock_client
        await engine.close()
        mock_client.aclose.assert_called_once()
        assert engine._client is None


# ---------------------------------------------------------------------------
# RegoPolicyFilter
# ---------------------------------------------------------------------------


def _make_request(method="GET", path="/api/v1/widget/", headers=None):
    """Create a fake Starlette Request."""
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        headers=Headers(headers or {}),
    )


class TestRegoPolicyFilter:

    @pytest.mark.asyncio
    async def test_allows_when_policy_returns_true(self):
        engine = InlinePolicy()
        engine.register_rule("authz/allow", lambda d: True)

        f = RegoPolicyFilter(engine=engine, policy_path="authz/allow")
        ctx = FilterContext()
        request = _make_request()

        # Should not raise
        await f.on_request(request, ctx)
        assert ctx.extras.get("policy_decision") is True

    @pytest.mark.asyncio
    async def test_denies_when_policy_returns_false(self):
        engine = InlinePolicy()
        engine.register_rule("authz/allow", lambda d: False)

        f = RegoPolicyFilter(engine=engine, policy_path="authz/allow")
        ctx = FilterContext()
        request = _make_request()

        with pytest.raises(FilterShortCircuit) as exc_info:
            await f.on_request(request, ctx)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_skips_configured_paths(self):
        engine = InlinePolicy()
        engine.register_rule("authz/allow", lambda d: False)

        f = RegoPolicyFilter(
            engine=engine,
            policy_path="authz/allow",
            skip_paths=["/health", "/schemas"],
        )
        ctx = FilterContext()

        # Health endpoint should be skipped
        request = _make_request(path="/health")
        await f.on_request(request, ctx)

        # Schemas should be skipped
        request = _make_request(path="/schemas/widget")
        await f.on_request(request, ctx)

    @pytest.mark.asyncio
    async def test_custom_input_builder(self):
        received_input = {}

        engine = InlinePolicy()

        @engine.rule("custom/check")
        def check(input_data):
            received_input.update(input_data)
            return True

        def custom_builder(request, context):
            return {"custom_key": "custom_value", "method": request.method}

        f = RegoPolicyFilter(
            engine=engine,
            policy_path="custom/check",
            build_input=custom_builder,
        )
        ctx = FilterContext()
        request = _make_request(method="POST")

        await f.on_request(request, ctx)

        assert received_input["custom_key"] == "custom_value"
        assert received_input["method"] == "POST"

    @pytest.mark.asyncio
    async def test_default_input_includes_method_and_path(self):
        received_input = {}

        engine = InlinePolicy()

        @engine.rule("check/input")
        def check(input_data):
            received_input.update(input_data)
            return True

        f = RegoPolicyFilter(engine=engine, policy_path="check/input")
        ctx = FilterContext()
        request = _make_request(method="PATCH", path="/api/v1/widget/123")

        await f.on_request(request, ctx)

        assert received_input["method"] == "PATCH"
        assert received_input["path"] == "/api/v1/widget/123"
        assert "path_parts" in received_input

    @pytest.mark.asyncio
    async def test_engine_error_returns_503(self):
        engine = AsyncMock()
        engine.evaluate = AsyncMock(side_effect=Exception("engine down"))

        f = RegoPolicyFilter(engine=engine, policy_path="authz/allow")
        ctx = FilterContext()
        request = _make_request()

        with pytest.raises(FilterShortCircuit) as exc_info:
            await f.on_request(request, ctx)

        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_on_response_passes_through(self):
        engine = InlinePolicy()
        f = RegoPolicyFilter(engine=engine)

        response = SimpleNamespace(status_code=200)
        result = await f.on_response(
            _make_request(), response, FilterContext()
        )
        assert result is response

    @pytest.mark.asyncio
    async def test_user_context_passed_to_input(self):
        received_input = {}

        engine = InlinePolicy()

        @engine.rule("check/user")
        def check(input_data):
            received_input.update(input_data)
            return True

        f = RegoPolicyFilter(engine=engine, policy_path="check/user")
        ctx = FilterContext(user={"id": "user-1", "role": "admin"})
        request = _make_request()

        await f.on_request(request, ctx)

        assert received_input["user"]["id"] == "user-1"
        assert received_input["user"]["role"] == "admin"

    def test_order_is_3(self):
        engine = InlinePolicy()
        f = RegoPolicyFilter(engine=engine)
        assert f.order == 3
