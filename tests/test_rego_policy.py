"""Tests for the Rego/OPA policy engine and filter."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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

        assert (
            await engine.evaluate("widget/create", {"user": {"role": "admin"}}) is True
        )
        assert (
            await engine.evaluate("widget/create", {"user": {"role": "viewer"}})
            is False
        )

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
        result = await f.on_response(_make_request(), response, FilterContext())
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


# ---------------------------------------------------------------------------
# OpaRemotePolicy — evaluate_raw and _get_client
# ---------------------------------------------------------------------------


class TestOpaRemotePolicyExtended:

    def _mock_opa_response(self, data):
        resp = MagicMock()
        resp.json.return_value = data
        resp.raise_for_status = MagicMock()
        return resp

    @pytest.mark.asyncio
    async def test_evaluate_raw_returns_full_dict(self):
        engine = OpaRemotePolicy(url="http://opa:8181")

        payload = {"result": {"allow": True, "reason": "admin role"}}
        mock_resp = self._mock_opa_response(payload)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        engine._client = mock_client

        result = await engine.evaluate_raw("authz/allow", {"user": "admin"})

        assert result == payload
        assert result["result"]["allow"] is True
        assert result["result"]["reason"] == "admin role"

    @pytest.mark.asyncio
    async def test_evaluate_raw_uses_default_policy_when_none(self):
        engine = OpaRemotePolicy(url="http://opa:8181", default_policy="default/check")

        mock_resp = self._mock_opa_response({"result": True})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        engine._client = mock_client

        await engine.evaluate_raw(None, {})

        mock_client.post.assert_called_once_with(
            "http://opa:8181/v1/data/default/check",
            json={"input": {}},
        )

    @pytest.mark.asyncio
    async def test_evaluate_raw_uses_empty_input_when_none(self):
        engine = OpaRemotePolicy(url="http://opa:8181")

        mock_resp = self._mock_opa_response({"result": False})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        engine._client = mock_client

        await engine.evaluate_raw("authz/allow", None)

        mock_client.post.assert_called_once_with(
            "http://opa:8181/v1/data/authz/allow",
            json={"input": {}},
        )

    @pytest.mark.asyncio
    async def test_evaluate_raw_raises_policy_evaluation_error_on_exception(self):
        engine = OpaRemotePolicy(url="http://opa:8181")

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=RuntimeError("network error"))
        engine._client = mock_client

        with pytest.raises(PolicyEvaluationError, match="network error"):
            await engine.evaluate_raw("authz/allow", {})

    @pytest.mark.asyncio
    async def test_evaluate_raw_reraises_policy_evaluation_error_unchanged(self):
        engine = OpaRemotePolicy(url="http://opa:8181")

        mock_client = AsyncMock()
        original_error = PolicyEvaluationError("already wrapped")
        mock_client.post = AsyncMock(side_effect=original_error)
        engine._client = mock_client

        with pytest.raises(PolicyEvaluationError, match="already wrapped"):
            await engine.evaluate_raw("authz/allow", {})

    @pytest.mark.asyncio
    async def test_get_client_creates_httpx_client_when_none(self, monkeypatch):
        engine = OpaRemotePolicy(url="http://opa:8181", timeout=3.0)

        mock_client_instance = AsyncMock()
        mock_httpx = MagicMock()
        mock_httpx.AsyncClient.return_value = mock_client_instance

        import sys

        monkeypatch.setitem(sys.modules, "httpx", mock_httpx)

        # Clear any cached client
        engine._client = None
        client = await engine._get_client()

        mock_httpx.AsyncClient.assert_called_once_with(timeout=3.0)
        assert client is mock_client_instance

    @pytest.mark.asyncio
    async def test_get_client_reuses_existing_client(self):
        engine = OpaRemotePolicy(url="http://opa:8181")
        existing_client = AsyncMock()
        engine._client = existing_client

        client = await engine._get_client()

        assert client is existing_client

    @pytest.mark.asyncio
    async def test_close_with_no_client_is_noop(self):
        engine = OpaRemotePolicy(url="http://opa:8181")
        assert engine._client is None
        # Should not raise
        await engine.close()
        assert engine._client is None

    @pytest.mark.asyncio
    async def test_evaluate_non_bool_result_cast_to_bool(self):
        engine = OpaRemotePolicy(url="http://opa:8181")

        # A truthy integer result should be cast to True
        mock_resp = self._mock_opa_response({"result": 1})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        engine._client = mock_client

        result = await engine.evaluate("authz/allow", {})
        assert result is True


# ---------------------------------------------------------------------------
# LocalRegoPolicy — mocked regorus
# ---------------------------------------------------------------------------


class TestLocalRegoPolicy:
    """Tests for LocalRegoPolicy using a mocked regorus module."""

    def _make_mock_regorus(self, eval_return_value=None):
        """Build a mock regorus module with an Engine class."""
        mock_engine_instance = MagicMock()

        if eval_return_value is None:
            eval_return_value = (
                '[{"expressions": [{"value": true, "text": "data.authz.allow"}]}]'
            )
        mock_engine_instance.eval_query.return_value = eval_return_value

        mock_regorus = MagicMock()
        mock_regorus.Engine.return_value = mock_engine_instance
        return mock_regorus, mock_engine_instance

    @pytest.mark.asyncio
    async def test_evaluate_returns_true_when_policy_allows(self, monkeypatch):
        from slip_stream.core.policy import LocalRegoPolicy

        mock_regorus, _ = self._make_mock_regorus(
            '[{"expressions": [{"value": true, "text": "data.authz.allow"}]}]'
        )

        import sys

        monkeypatch.setitem(sys.modules, "regorus", mock_regorus)

        engine = LocalRegoPolicy()
        result = await engine.evaluate("authz/allow", {"user": "admin"})
        assert result is True

    @pytest.mark.asyncio
    async def test_evaluate_returns_false_when_policy_denies(self, monkeypatch):
        from slip_stream.core.policy import LocalRegoPolicy

        mock_regorus, _ = self._make_mock_regorus(
            '[{"expressions": [{"value": false, "text": "data.authz.allow"}]}]'
        )

        import sys

        monkeypatch.setitem(sys.modules, "regorus", mock_regorus)

        engine = LocalRegoPolicy()
        result = await engine.evaluate("authz/allow", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_evaluate_raw_returns_dict_with_result_key(self, monkeypatch):
        from slip_stream.core.policy import LocalRegoPolicy

        mock_regorus, _ = self._make_mock_regorus(
            '[{"expressions": [{"value": true, "text": "data.authz.allow"}]}]'
        )

        import sys

        monkeypatch.setitem(sys.modules, "regorus", mock_regorus)

        engine = LocalRegoPolicy()
        result = await engine.evaluate_raw("authz/allow", {"role": "admin"})
        assert isinstance(result, dict)
        assert "result" in result
        assert result["result"] is True

    @pytest.mark.asyncio
    async def test_evaluate_raw_already_has_result_key(self, monkeypatch):
        from slip_stream.core.policy import LocalRegoPolicy

        # regorus returns a dict with "result" key directly
        mock_regorus, mock_engine_instance = self._make_mock_regorus()
        import json as _json

        mock_engine_instance.eval_query.return_value = _json.dumps({"result": True})

        import sys

        monkeypatch.setitem(sys.modules, "regorus", mock_regorus)

        engine = LocalRegoPolicy()
        result = await engine.evaluate_raw("authz/allow", {})
        assert result == {"result": True}

    @pytest.mark.asyncio
    async def test_evaluate_raw_raises_on_engine_error(self, monkeypatch):
        from slip_stream.core.policy import LocalRegoPolicy, PolicyEvaluationError

        mock_regorus, mock_engine_instance = self._make_mock_regorus()
        mock_engine_instance.eval_query.side_effect = RuntimeError("eval failed")

        import sys

        monkeypatch.setitem(sys.modules, "regorus", mock_regorus)

        engine = LocalRegoPolicy()
        with pytest.raises(PolicyEvaluationError, match="eval failed"):
            await engine.evaluate_raw("authz/allow", {})

    @pytest.mark.asyncio
    async def test_ensure_engine_not_installed_raises_import_error(self, monkeypatch):
        import sys

        from slip_stream.core.policy import LocalRegoPolicy

        monkeypatch.setitem(sys.modules, "regorus", None)

        engine = LocalRegoPolicy()
        # Force re-creation by clearing cached engine
        engine._engine = None

        with pytest.raises(ImportError, match="regorus is required"):
            engine._ensure_engine()

    @pytest.mark.asyncio
    async def test_add_policy_calls_engine(self, monkeypatch):
        from slip_stream.core.policy import LocalRegoPolicy

        mock_regorus, mock_engine_instance = self._make_mock_regorus()

        import sys

        monkeypatch.setitem(sys.modules, "regorus", mock_regorus)

        engine = LocalRegoPolicy()
        # Initialize engine first
        engine._ensure_engine()

        engine.add_policy("package authz\nallow = true", filename="test.rego")
        mock_engine_instance.add_policy.assert_called_once_with(
            "test.rego", "package authz\nallow = true"
        )

    @pytest.mark.asyncio
    async def test_add_data_calls_engine(self, monkeypatch):
        import json as _json

        from slip_stream.core.policy import LocalRegoPolicy

        mock_regorus, mock_engine_instance = self._make_mock_regorus()

        import sys

        monkeypatch.setitem(sys.modules, "regorus", mock_regorus)

        engine = LocalRegoPolicy()
        engine._ensure_engine()

        data = {"roles": ["admin", "viewer"]}
        engine.add_data(data)
        mock_engine_instance.add_data_json.assert_called_with(_json.dumps(data))

    @pytest.mark.asyncio
    async def test_loads_policy_files_from_dir(self, monkeypatch, tmp_path):
        from slip_stream.core.policy import LocalRegoPolicy

        # Create fake .rego files
        (tmp_path / "authz.rego").write_text("package authz\nallow = true")
        (tmp_path / "widget.rego").write_text("package widget\ncreate = true")

        mock_regorus, mock_engine_instance = self._make_mock_regorus()

        import sys

        monkeypatch.setitem(sys.modules, "regorus", mock_regorus)

        engine = LocalRegoPolicy(policy_dir=tmp_path)
        engine._ensure_engine()

        # Two .rego files should have been loaded
        assert mock_engine_instance.add_policy_from_file.call_count == 2

    @pytest.mark.asyncio
    async def test_loads_initial_data(self, monkeypatch):
        import json as _json

        from slip_stream.core.policy import LocalRegoPolicy

        mock_regorus, mock_engine_instance = self._make_mock_regorus()

        import sys

        monkeypatch.setitem(sys.modules, "regorus", mock_regorus)

        initial_data = {"org": "acme"}
        engine = LocalRegoPolicy(data=initial_data)
        engine._ensure_engine()

        mock_engine_instance.add_data_json.assert_called_once_with(
            _json.dumps(initial_data)
        )

    @pytest.mark.asyncio
    async def test_engine_reused_on_second_call(self, monkeypatch):
        from slip_stream.core.policy import LocalRegoPolicy

        mock_regorus, mock_engine_instance = self._make_mock_regorus()

        import sys

        monkeypatch.setitem(sys.modules, "regorus", mock_regorus)

        engine = LocalRegoPolicy()
        e1 = engine._ensure_engine()
        e2 = engine._ensure_engine()

        assert e1 is e2
        # Engine() should only have been constructed once
        mock_regorus.Engine.assert_called_once()
