"""Policy evaluation engine for slip-stream.

Supports two modes:

1. **Remote OPA** — POST policy evaluation to an OPA server.
2. **Local Rego** — Evaluate ``.rego`` files in-process using ``regorus``.

Usage::

    # Remote OPA
    engine = OpaRemotePolicy(url="http://localhost:8181")
    result = await engine.evaluate("authz/allow", input_data)

    # Local Rego
    engine = LocalRegoPolicy(policy_dir=Path("./policies"))
    result = await engine.evaluate("authz/allow", input_data)

    # As a filter
    from slip_stream.adapters.api.filters.rego import RegoPolicyFilter
    filters = [RegoPolicyFilter(engine=engine, policy_path="authz/allow")]

    # As a guard decorator
    @registry.guard("widget", "create")
    async def check_policy(ctx):
        allowed = await engine.evaluate("widget/create", {
            "user": ctx.current_user,
            "data": ctx.data.model_dump(),
        })
        if not allowed:
            raise HookError(403, "Policy denied")
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class PolicyEngine(Protocol):
    """Protocol for policy evaluation backends."""

    async def evaluate(
        self, policy_path: str, input_data: dict[str, Any]
    ) -> bool:
        """Evaluate a policy and return whether the request is allowed.

        Args:
            policy_path: Dot or slash-separated policy path
                (e.g., ``"authz/allow"`` or ``"authz.allow"``).
            input_data: The input document for policy evaluation.

        Returns:
            ``True`` if the policy allows the action, ``False`` otherwise.
        """
        ...

    async def evaluate_raw(
        self, policy_path: str, input_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Evaluate a policy and return the full result document.

        Args:
            policy_path: Policy path.
            input_data: The input document.

        Returns:
            The full OPA decision document.
        """
        ...


class OpaRemotePolicy:
    """Evaluate policies against a remote OPA server via REST API.

    Args:
        url: Base URL of the OPA server (e.g., ``http://localhost:8181``).
        default_policy: Default policy path when none specified.
        timeout: HTTP request timeout in seconds.

    Usage::

        engine = OpaRemotePolicy(url="http://localhost:8181")
        allowed = await engine.evaluate("authz/allow", {"user": "admin"})
    """

    def __init__(
        self,
        url: str = "http://localhost:8181",
        default_policy: str = "authz/allow",
        timeout: float = 5.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.default_policy = default_policy
        self.timeout = timeout
        self._client = None

    async def _get_client(self) -> Any:
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def evaluate(
        self, policy_path: str | None = None, input_data: dict[str, Any] | None = None
    ) -> bool:
        """Evaluate an OPA policy for the given input.

        Args:
            policy_path: Slash or dot-separated policy path. Defaults to ``default_policy``.
            input_data: The input document for policy evaluation.

        Returns:
            ``True`` if the policy allows the action, ``False`` otherwise.
        """
        result = await self.evaluate_raw(policy_path, input_data)
        # OPA returns {"result": true/false} for boolean policies
        # or {"result": {...}} for object policies
        decision = result.get("result", False)
        if isinstance(decision, bool):
            return decision
        # If result is a dict, check for an "allow" key
        if isinstance(decision, dict):
            return decision.get("allow", False)
        return bool(decision)

    async def evaluate_raw(
        self, policy_path: str | None = None, input_data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Evaluate an OPA policy and return the raw response.

        Args:
            policy_path: Slash or dot-separated policy path. Defaults to ``default_policy``.
            input_data: The input document for policy evaluation.

        Returns:
            The full OPA decision document as returned by the REST API.
        """
        path = (policy_path or self.default_policy).replace(".", "/")
        url = f"{self.url}/v1/data/{path}"

        client = await self._get_client()
        payload = {"input": input_data or {}}

        try:
            resp = await client.post(url, json=payload)
            if hasattr(resp, "raise_for_status"):
                resp.raise_for_status()
            resp_data = resp.json()
            if hasattr(resp_data, "__await__"):
                resp_data = await resp_data
            return resp_data
        except PolicyEvaluationError:
            raise
        except Exception as e:
            logger.error("OPA evaluation failed: %s", e)
            raise PolicyEvaluationError(f"OPA evaluation failed: {e}") from e

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class LocalRegoPolicy:
    """Evaluate Rego policies in-process using regorus.

    Args:
        policy_dir: Directory containing ``.rego`` files.
        data: Optional static data document to load into the policy engine.

    Requires ``regorus`` (optional dependency)::

        pip install slip-stream[rego]

    Usage::

        engine = LocalRegoPolicy(policy_dir=Path("./policies"))
        allowed = await engine.evaluate("authz/allow", {"user": "admin"})
    """

    def __init__(
        self,
        policy_dir: Path | None = None,
        policy_files: list[Path] | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self._policy_dir = policy_dir
        self._policy_files = policy_files or []
        self._data = data or {}
        self._engine = None

    def _ensure_engine(self) -> Any:
        if self._engine is not None:
            return self._engine

        try:
            import regorus
        except ImportError:
            raise ImportError(
                "regorus is required for local Rego evaluation. "
                "Install it with: pip install regorus"
            ) from None

        engine = regorus.Engine()

        # Load policy files
        if self._policy_dir and self._policy_dir.is_dir():
            for rego_file in sorted(self._policy_dir.glob("**/*.rego")):
                engine.add_policy_from_file(str(rego_file))
                logger.debug("Loaded policy: %s", rego_file)

        for policy_file in self._policy_files:
            engine.add_policy_from_file(str(policy_file))

        # Load data document
        if self._data:
            engine.add_data_json(json.dumps(self._data))

        self._engine = engine
        return engine

    async def evaluate(
        self, policy_path: str | None = None, input_data: dict[str, Any] | None = None
    ) -> bool:
        """Evaluate a Rego policy locally using regopy.

        Args:
            policy_path: Slash or dot-separated policy path (e.g., ``"authz/allow"``).
            input_data: The input document for policy evaluation.

        Returns:
            ``True`` if the policy allows the action, ``False`` otherwise.
        """
        result = await self.evaluate_raw(policy_path, input_data)
        decision = result.get("result", False)
        if isinstance(decision, bool):
            return decision
        if isinstance(decision, dict):
            return decision.get("allow", False)
        return bool(decision)

    async def evaluate_raw(
        self, policy_path: str | None = None, input_data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Evaluate a Rego policy and return the raw result set.

        Args:
            policy_path: Slash or dot-separated policy path (e.g., ``"authz/allow"``).
            input_data: The input document for policy evaluation.

        Returns:
            A dict with a ``"result"`` key containing the raw value from regorus.
        """
        engine = self._ensure_engine()

        # Convert policy path to Rego query format: "data.authz.allow"
        path = (policy_path or "authz/allow").replace("/", ".")
        query = f"data.{path}"

        engine.set_input_json(json.dumps(input_data or {}))

        try:
            result = engine.eval_query(query)
            # regorus returns a JSON string
            parsed = json.loads(result) if isinstance(result, str) else result
            # Extract the value from regorus result format
            if isinstance(parsed, dict) and "result" in parsed:
                return parsed
            # regorus returns [{"expressions": [{"value": ..., "text": "..."}]}]
            if isinstance(parsed, list) and parsed:
                first = parsed[0]
                if isinstance(first, dict) and "expressions" in first:
                    exprs = first["expressions"]
                    if exprs:
                        return {"result": exprs[0].get("value")}
            return {"result": parsed}
        except Exception as e:
            logger.error("Local Rego evaluation failed: %s", e)
            raise PolicyEvaluationError(f"Rego evaluation failed: {e}") from e

    def add_policy(self, policy_text: str, filename: str = "inline.rego") -> None:
        """Add a policy from a string (useful for testing)."""
        engine = self._ensure_engine()
        engine.add_policy(filename, policy_text)

    def add_data(self, data: dict[str, Any]) -> None:
        """Add or merge data into the policy engine."""
        engine = self._ensure_engine()
        engine.add_data_json(json.dumps(data))


class InlinePolicy:
    """Simple Python-based policy engine for quick rules without Rego.

    Register Python callables as policy handlers. Useful for simple
    authorization rules that don't need a full policy language.

    Usage::

        engine = InlinePolicy()

        @engine.rule("widget/create")
        def allow_create(input_data):
            return input_data.get("user", {}).get("role") == "admin"

        allowed = await engine.evaluate("widget/create", {"user": {"role": "admin"}})
    """

    def __init__(self) -> None:
        self._rules: dict[str, Any] = {}

    def rule(self, policy_path: str) -> Any:
        """Decorator to register a policy rule function.

        Args:
            policy_path: The policy path to bind the rule to (e.g., ``"widget/create"``).

        Returns:
            The original function, unchanged.
        """
        def decorator(fn: Any) -> Any:
            normalized = policy_path.replace(".", "/")
            self._rules[normalized] = fn
            return fn
        return decorator

    def register_rule(self, policy_path: str, fn: Any) -> None:
        """Register a policy rule function.

        Args:
            policy_path: The policy path to bind the rule to (e.g., ``"widget/create"``).
            fn: A callable (sync or async) that accepts ``input_data`` and returns a bool.
        """
        normalized = policy_path.replace(".", "/")
        self._rules[normalized] = fn

    async def evaluate(
        self, policy_path: str | None = None, input_data: dict[str, Any] | None = None
    ) -> bool:
        """Evaluate all registered rules for the given input.

        Args:
            policy_path: The policy path identifying the rule to invoke.
            input_data: The input document passed to the rule function.

        Returns:
            ``True`` if the matched rule returns a truthy value, ``False`` otherwise.
        """
        path = (policy_path or "").replace(".", "/")
        handler = self._rules.get(path)
        if handler is None:
            logger.warning("No policy rule registered for: %s", path)
            return False

        import asyncio
        if asyncio.iscoroutinefunction(handler):
            return bool(await handler(input_data or {}))
        return bool(handler(input_data or {}))

    async def evaluate_raw(
        self, policy_path: str | None = None, input_data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Evaluate all registered rules and return raw results.

        Args:
            policy_path: The policy path identifying the rule to invoke.
            input_data: The input document passed to the rule function.

        Returns:
            A dict with a ``"result"`` key containing the boolean decision.
        """
        result = await self.evaluate(policy_path, input_data)
        return {"result": result}


class PolicyEvaluationError(Exception):
    """Raised when a policy evaluation fails."""
    pass
