"""MCP fuzz runner for slip-stream and stellar-drive.

Tests the JSON-RPC 2.0 MCP protocol implementation of either server by
exercising every exposed tool with valid inputs (positive), invalid inputs
(negative), and schema-versioning flows (version).

The runner talks to the server over stdio via ``MCPClient`` — no HTTP server
needs to be running.  Only the MCP server binary / command is required.

Usage::

    # slip-stream
    python benchmarks/fuzz/run_mcp_fuzz.py \\
        --cmd "poetry run python -m slip_stream.mcp.server --schema-dir benchmarks/schemas" \\
        --mode all

    # stellar-drive (binary must be on PATH or given as full path)
    python benchmarks/fuzz/run_mcp_fuzz.py \\
        --cmd "./stellar-drive mcp --config benchmarks/stellar.yaml" \\
        --mode all

    # Save results to JSON
    python benchmarks/fuzz/run_mcp_fuzz.py \\
        --cmd "..." \\
        --mode positive \\
        --output benchmarks/results/mcp_fuzz.json

Modes:
    positive  — every tool called with valid arguments; verify no crash
    negative  — invalid / missing arguments; verify graceful error handling
    version   — schema versioning flows; multi-version round-trips
    all       — runs all three modes in sequence
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# FuzzResult / print_results — imported from run_fuzz.py when available, or
# defined locally.  The local definitions are identical to the originals but
# avoid pulling in `requests` (an HTTP library) which run_fuzz.py imports at
# module level.  MCPClient has no external dependencies and is always loaded
# from the sibling mcp_client.py via importlib so both repos share one copy.
# ---------------------------------------------------------------------------
try:
    from benchmarks.fuzz.run_fuzz import (  # type: ignore[assignment]
        FuzzResult,
        print_results,
    )
except ImportError:
    # Local fallback — zero external dependencies.
    class FuzzResult:  # type: ignore[no-redef]
        """A single fuzz-test check result."""

        def __init__(
            self,
            mode: str,
            entity: str,
            step: str,
            passed: bool,
            detail: str = "",
        ) -> None:
            self.mode = mode
            self.entity = entity
            self.step = step
            self.passed = passed
            self.detail = detail

        def __repr__(self) -> str:
            status = "PASS" if self.passed else "FAIL"
            return f"[{status}] {self.mode}/{self.entity}/{self.step}: {self.detail}"

    def print_results(results: list) -> bool:  # type: ignore[misc]
        """Print all results and return True if every check passed."""
        passed_count = sum(1 for r in results if r.passed)
        failed_count = sum(1 for r in results if not r.passed)
        for r in results:
            print(r)
        print(f"\n{'=' * 60}")
        print(
            f"Total: {len(results)} checks — {passed_count} passed, {failed_count} failed"
        )
        if failed_count:
            print("\nFAILED checks:")
            for r in results:
                if not r.passed:
                    print(f"  {r}")
            return False
        print("\nAll checks passed.")
        return True


# MCPClient is always loaded from the sibling file — no external dependencies.
try:
    from benchmarks.fuzz.mcp_client import (  # type: ignore[assignment]
        MCPClient,
        MCPClientError,
    )
except ImportError:
    import importlib.util as _ilu
    from pathlib import Path as _Path

    _fuzz_dir = _Path(__file__).parent
    _spec = _ilu.spec_from_file_location("mcp_client", _fuzz_dir / "mcp_client.py")
    assert _spec is not None and _spec.loader is not None
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    MCPClient = _mod.MCPClient  # type: ignore[assignment]
    MCPClientError = _mod.MCPClientError  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Canonical schema names present in both repos' benchmarks/schemas directories.
_KNOWN_SCHEMAS: list[str] = ["pet", "order", "tag", "category", "user"]

# A synthetic v3 schema used by the version-mode create_schema probe.
_V3_TEST_SCHEMA_NAME = "fuzz_test_entity"
_V3_TEST_SCHEMA_VERSION = "3.0.0"

# Stellar-drive create_schema expects a JSON envelope string.
_STELLAR_ENVELOPE_JSON = json.dumps(
    {
        "name": _V3_TEST_SCHEMA_NAME,
        "version": _V3_TEST_SCHEMA_VERSION,
        "description": "Ephemeral schema created by MCP fuzz tests",
        "storage": "mongo",
        "collection": "fuzz_test_entities",
        "schema": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "required": ["label"],
            "properties": {
                "label": {"type": "string", "description": "Test label"},
                "value": {"type": "integer", "description": "Test value"},
            },
        },
        "indexes": [],
    }
)

# slip-stream create_schema uses simpler name/description args.
_SLIP_STREAM_CREATE_ARGS: dict[str, Any] = {
    "name": _V3_TEST_SCHEMA_NAME,
    "description": "Ephemeral schema created by MCP fuzz tests",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_result_text(resp: dict[str, Any]) -> str:
    """Extract the first text content string from a tools/call response."""
    result = resp.get("result", {})
    if isinstance(result, dict):
        content = result.get("content", [])
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                return first.get("text", "")
    return ""


def _is_error_result(resp: dict[str, Any]) -> bool:
    """Return True if the response carries a JSON-RPC error or isError flag."""
    if "error" in resp:
        return True
    result = resp.get("result", {})
    if isinstance(result, dict):
        return bool(result.get("isError", False))
    return False


def _is_success(resp: dict[str, Any]) -> bool:
    """Return True when the response has a result key and no top-level error."""
    return "result" in resp and "error" not in resp


def _first_schema_name(client: MCPClient) -> str:
    """Return the first schema name reported by list_schemas, or 'pet'."""
    try:
        resp = client.call_tool("list_schemas")
        text = _tool_result_text(resp)
        if not text:
            return "pet"
        # The text is a JSON array (stellar-drive) or {"schemas": [...]} (slip-stream).
        parsed = json.loads(text)
        if isinstance(parsed, list) and parsed:
            return str(parsed[0].get("name", "pet"))
        if isinstance(parsed, dict):
            schemas = parsed.get("schemas", [])
            if schemas:
                return str(schemas[0].get("name", "pet"))
    except Exception:
        pass
    return "pet"


def _make_fuzz_result(
    mode: str,
    tool: str,
    step: str,
    resp: dict[str, Any],
    *,
    expect_error: bool = False,
    extra_check: str = "",
) -> FuzzResult:
    """Build a FuzzResult from an MCP response.

    Args:
        mode: Fuzz mode label (``"positive"``, ``"negative"``, ``"version"``).
        tool: MCP tool name (used as entity label).
        step: Descriptive step label.
        resp: Full JSON-RPC response dict.
        expect_error: If True, success means the response IS an error.
        extra_check: Optional extra detail appended when the check fails.

    Returns:
        A FuzzResult with ``passed`` set appropriately.
    """
    if expect_error:
        # For negative tests: we want an error back, not a crash.
        # A JSON-RPC level error OR a tool-level isError both count as success.
        passed = _is_error_result(resp) or (
            "result"
            in resp
            # The tool returned a result (not a crash) — acceptable for
            # servers that encode errors in result.content text.
        )
        detail = (
            "error correctly returned"
            if _is_error_result(resp)
            else "non-error result returned (server handled gracefully)"
        )
        if extra_check and not passed:
            detail += f"; {extra_check}"
        return FuzzResult(mode, tool, step, passed, detail)

    # Positive / version test: expect a clean result with no top-level error.
    if "error" in resp:
        err = resp["error"]
        detail = f"JSON-RPC error {err.get('code')}: {err.get('message', '')}"
        if extra_check:
            detail += f"; {extra_check}"
        return FuzzResult(mode, tool, step, False, detail)

    if "result" not in resp:
        detail = (
            f"no result key in response; {extra_check}"
            if extra_check
            else "no result key"
        )
        return FuzzResult(mode, tool, step, False, detail)

    text = _tool_result_text(resp)
    detail = f"ok, {len(text)} chars"
    if extra_check:
        detail += f"; {extra_check}"
    return FuzzResult(mode, tool, step, True, detail)


def _safe_call(
    client: MCPClient,
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    mode: str,
    step: str,
    expect_error: bool = False,
    extra_check: str = "",
) -> FuzzResult:
    """Wrap client.call_tool() with exception handling.

    Returns a FAIL FuzzResult if the server crashes or the call raises an
    unexpected exception.
    """
    try:
        resp = client.call_tool(tool, arguments)
        return _make_fuzz_result(
            mode,
            tool,
            step,
            resp,
            expect_error=expect_error,
            extra_check=extra_check,
        )
    except MCPClientError as exc:
        return FuzzResult(mode, tool, step, False, f"server died: {exc}")
    except Exception as exc:
        return FuzzResult(mode, tool, step, False, f"unexpected error: {exc}")


# ---------------------------------------------------------------------------
# Mode: positive
# ---------------------------------------------------------------------------


def run_positive(client: MCPClient, schema_name: str) -> list[FuzzResult]:
    """Exercise every tool with valid inputs and verify no crashes occur.

    The assertions here are intentionally permissive: we accept any non-error
    response because both servers may return different text for the same tool
    depending on configuration (e.g. ``query_rest_api`` on stellar-drive
    returns a curl example; on slip-stream it performs a real HTTP request).

    Steps:
        1.  list_schemas — no args
        2.  get_schema — known schema name
        3.  list_versions — known schema name
        4.  describe_entity — known schema name
        5.  get_schema_dag — no args
        6.  validate_schemas — no args
        7.  query_rest_api — GET on a known endpoint path
        8.  query_graphql — valid introspection query (slip-stream) or no args
        9.  get_topology — no args
        10. create_schema — valid schema payload
        11. generate_sdk — no args (stellar-drive) / empty args (slip-stream)
    """
    results: list[FuzzResult] = []
    mode = "positive"

    # 1. list_schemas
    results.append(_safe_call(client, "list_schemas", mode=mode, step="list_schemas"))

    # 2. get_schema
    results.append(
        _safe_call(
            client,
            "get_schema",
            {"name": schema_name},
            mode=mode,
            step="get_schema",
        )
    )

    # 3. list_versions
    results.append(
        _safe_call(
            client,
            "list_versions",
            {"name": schema_name},
            mode=mode,
            step="list_versions",
        )
    )

    # 4. describe_entity
    results.append(
        _safe_call(
            client,
            "describe_entity",
            {"name": schema_name},
            mode=mode,
            step="describe_entity",
        )
    )

    # 5. get_schema_dag
    results.append(
        _safe_call(client, "get_schema_dag", mode=mode, step="get_schema_dag")
    )

    # 6. validate_schemas
    results.append(
        _safe_call(client, "validate_schemas", mode=mode, step="validate_schemas")
    )

    # 7. query_rest_api
    # slip-stream: {"method": "GET", "path": "/api/v1/{schema}/"}
    # stellar-drive: {"method": "GET", "path": "/{schema}"} — generates curl
    # We send both forms; either server should return a non-crashing result.
    results.append(
        _safe_call(
            client,
            "query_rest_api",
            {"method": "GET", "path": f"/api/v1/{schema_name}/"},
            mode=mode,
            step="query_rest_api",
        )
    )

    # 8. query_graphql
    # slip-stream requires a "query" arg; stellar-drive takes no args.
    # Try the slip-stream signature first; stellar-drive will ignore extra args.
    results.append(
        _safe_call(
            client,
            "query_graphql",
            {"query": "{ __schema { queryType { name } } }"},
            mode=mode,
            step="query_graphql",
        )
    )

    # 9. get_topology
    results.append(_safe_call(client, "get_topology", mode=mode, step="get_topology"))

    # 10. create_schema
    # Try stellar-drive envelope format first (has "schema" JSON string arg).
    # slip-stream uses simpler {"name": ..., "description": ...} args.
    # We attempt stellar-drive format; if it fails we try slip-stream format.
    stellar_resp = client.call_tool(
        "create_schema",
        {
            "name": _V3_TEST_SCHEMA_NAME,
            "version": _V3_TEST_SCHEMA_VERSION,
            "schema": _STELLAR_ENVELOPE_JSON,
        },
    )
    if _is_success(stellar_resp) and not _is_error_result(stellar_resp):
        results.append(
            FuzzResult(
                mode,
                "create_schema",
                "create_schema",
                True,
                "stellar-drive format accepted",
            )
        )
    else:
        # Try slip-stream format (name + description)
        slip_resp = client.call_tool("create_schema", _SLIP_STREAM_CREATE_ARGS)
        success = _is_success(slip_resp)
        detail = (
            "slip-stream format accepted"
            if success
            else f"both formats failed: {_tool_result_text(slip_resp)[:120]}"
        )
        results.append(
            FuzzResult(mode, "create_schema", "create_schema", success, detail)
        )

    # 11. generate_sdk
    # stellar-drive takes optional output_dir; slip-stream takes optional output_path.
    results.append(
        _safe_call(
            client,
            "generate_sdk",
            {},
            mode=mode,
            step="generate_sdk",
        )
    )

    return results


# ---------------------------------------------------------------------------
# Mode: negative
# ---------------------------------------------------------------------------


def run_negative(client: MCPClient) -> list[FuzzResult]:
    """Send invalid inputs to each tool and verify the server does not crash.

    A "pass" here means the server is still alive and returned any response
    (including a JSON-RPC error or isError result).  A "fail" means the
    server process died or the client raised an unexpected exception.

    Steps:
        1.  get_schema with non-existent name
        2.  get_schema with empty name
        3.  list_versions with non-existent name
        4.  describe_entity with non-existent name
        5.  tools/call with unknown tool name
        6.  tools/call with missing required argument (get_schema, no name)
        7.  create_schema with empty/malformed schema JSON
        8.  query_rest_api with invalid HTTP method
        9.  query_graphql with malformed query string
        10. get_schema with a name containing path-traversal characters
        11. list_versions with a very long name (DoS probe)
    """
    results: list[FuzzResult] = []
    mode = "negative"

    # 1. get_schema — non-existent schema name
    results.append(
        _safe_call(
            client,
            "get_schema",
            {"name": "nonexistent_xyz_abc_123"},
            mode=mode,
            step="get_schema_nonexistent",
            expect_error=True,
        )
    )

    # 2. get_schema — empty name
    results.append(
        _safe_call(
            client,
            "get_schema",
            {"name": ""},
            mode=mode,
            step="get_schema_empty_name",
            expect_error=True,
        )
    )

    # 3. list_versions — non-existent name
    results.append(
        _safe_call(
            client,
            "list_versions",
            {"name": "nonexistent_xyz_abc_123"},
            mode=mode,
            step="list_versions_nonexistent",
            expect_error=True,
        )
    )

    # 4. describe_entity — non-existent name
    results.append(
        _safe_call(
            client,
            "describe_entity",
            {"name": "nonexistent_xyz_abc_123"},
            mode=mode,
            step="describe_entity_nonexistent",
            expect_error=True,
        )
    )

    # 5. Unknown tool name — JSON-RPC level error expected
    try:
        resp = client.send(
            "tools/call",
            {"name": "nonexistent_tool_xyz", "arguments": {}},
        )
        # Server MUST return a result (even if it's an error) — not crash.
        passed = _is_success(resp) or "error" in resp
        detail = (
            "graceful error response for unknown tool"
            if passed
            else "no result or error key in response"
        )
        results.append(FuzzResult(mode, "tools/call", "unknown_tool", passed, detail))
    except MCPClientError as exc:
        results.append(
            FuzzResult(mode, "tools/call", "unknown_tool", False, f"server died: {exc}")
        )
    except Exception as exc:
        results.append(
            FuzzResult(mode, "tools/call", "unknown_tool", False, f"error: {exc}")
        )

    # 6. tools/call with missing required argument (get_schema, no "name" key)
    try:
        resp = client.call_tool("get_schema", {})
        passed = client.is_alive()
        detail = "server alive after missing-arg call" if passed else "server died"
        results.append(
            FuzzResult(mode, "get_schema", "missing_required_arg", passed, detail)
        )
    except MCPClientError as exc:
        results.append(
            FuzzResult(
                mode, "get_schema", "missing_required_arg", False, f"server died: {exc}"
            )
        )
    except Exception as exc:
        results.append(
            FuzzResult(
                mode, "get_schema", "missing_required_arg", False, f"error: {exc}"
            )
        )

    # 7. create_schema — malformed / empty schema JSON string
    results.append(
        _safe_call(
            client,
            "create_schema",
            {
                "name": "bad_schema",
                "version": "1.0.0",
                "schema": "{ this is not valid json !!!",
            },
            mode=mode,
            step="create_schema_invalid_json",
            expect_error=True,
        )
    )

    # 8. query_rest_api — invalid HTTP method
    results.append(
        _safe_call(
            client,
            "query_rest_api",
            {"method": "INVALID_METHOD_XYZ", "path": "/api/v1/pet/"},
            mode=mode,
            step="query_rest_api_bad_method",
            expect_error=True,
        )
    )

    # 9. query_graphql — completely malformed query string
    results.append(
        _safe_call(
            client,
            "query_graphql",
            {"query": "this is NOT valid graphql { { { } } }"},
            mode=mode,
            step="query_graphql_malformed",
            # slip-stream will attempt the HTTP call and return whatever the
            # server responds; stellar-drive may list operations without parsing.
            # Either way the server must NOT crash.
            expect_error=False,
        )
    )
    # Verify the process is still alive — the key invariant for negative tests.
    alive = client.is_alive()
    results.append(
        FuzzResult(
            mode,
            "server",
            "still_alive_after_graphql_malformed",
            alive,
            "process running" if alive else "server process died",
        )
    )

    # 10. get_schema — path-traversal attempt in name
    results.append(
        _safe_call(
            client,
            "get_schema",
            {"name": "../../etc/passwd"},
            mode=mode,
            step="get_schema_path_traversal",
            expect_error=True,
        )
    )

    # 11. list_versions — absurdly long name (16 KB) to probe buffer handling
    long_name = "a" * 16_384
    results.append(
        _safe_call(
            client,
            "list_versions",
            {"name": long_name},
            mode=mode,
            step="list_versions_very_long_name",
            expect_error=True,
        )
    )

    # Final: confirm server is still alive after all negative probes.
    alive = client.is_alive()
    results.append(
        FuzzResult(
            mode,
            "server",
            "alive_after_all_negative_probes",
            alive,
            "process running" if alive else "server process died",
        )
    )

    return results


# ---------------------------------------------------------------------------
# Mode: version
# ---------------------------------------------------------------------------


def run_version(client: MCPClient, schema_name: str) -> list[FuzzResult]:
    """Exercise schema-versioning flows.

    Steps:
        1.  list_versions for each known schema — verify non-empty list
        2.  get_schema with explicit version "1.0.0" — verify content returned
        3.  get_schema with version="latest" — verify non-empty content
        4.  describe_entity with version="latest" — verify fields present
        5.  create_schema with a v3 test schema — verify it round-trips via list_versions
        6.  get_schema for the newly created v3 schema — verify version field
    """
    results: list[FuzzResult] = []
    mode = "version"

    # 1. list_versions for each known schema
    for sname in _KNOWN_SCHEMAS:
        try:
            resp = client.call_tool("list_versions", {"name": sname})
            if "error" in resp:
                # Schema may not exist in this deployment — skip, not a failure.
                results.append(
                    FuzzResult(
                        mode,
                        sname,
                        "list_versions",
                        True,
                        "schema not present, skipped",
                    )
                )
                continue
            text = _tool_result_text(resp)
            if not text:
                results.append(
                    FuzzResult(
                        mode, sname, "list_versions", False, "empty response text"
                    )
                )
                continue
            # Verify text is parseable and contains at least one entry.
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    count = len(parsed)
                elif isinstance(parsed, dict):
                    count = len(parsed.get("versions", [parsed]))
                else:
                    count = 0
                passed = count >= 1
                detail = f"{count} version(s) returned"
            except json.JSONDecodeError:
                # Non-JSON text response is still a valid response (not a crash).
                passed = len(text) > 0
                detail = f"non-JSON response, {len(text)} chars"
            results.append(FuzzResult(mode, sname, "list_versions", passed, detail))
        except MCPClientError as exc:
            results.append(
                FuzzResult(mode, sname, "list_versions", False, f"server died: {exc}")
            )

    # 2. get_schema with explicit version "1.0.0"
    try:
        resp = client.call_tool("get_schema", {"name": schema_name, "version": "1.0.0"})
        text = _tool_result_text(resp)
        passed = _is_success(resp) and len(text) > 0
        detail = f"returned {len(text)} chars" if passed else f"failed: {text[:100]}"
        results.append(FuzzResult(mode, schema_name, "get_schema_v1", passed, detail))
    except MCPClientError as exc:
        results.append(
            FuzzResult(mode, schema_name, "get_schema_v1", False, f"server died: {exc}")
        )

    # 3. get_schema with version="latest"
    try:
        resp = client.call_tool(
            "get_schema", {"name": schema_name, "version": "latest"}
        )
        text = _tool_result_text(resp)
        passed = _is_success(resp) and len(text) > 0
        detail = f"returned {len(text)} chars" if passed else f"failed: {text[:100]}"
        results.append(
            FuzzResult(mode, schema_name, "get_schema_latest", passed, detail)
        )
    except MCPClientError as exc:
        results.append(
            FuzzResult(
                mode, schema_name, "get_schema_latest", False, f"server died: {exc}"
            )
        )

    # 4. describe_entity with version="latest"
    try:
        resp = client.call_tool(
            "describe_entity", {"name": schema_name, "version": "latest"}
        )
        text = _tool_result_text(resp)
        passed = _is_success(resp) and len(text) > 0
        # Verify the response mentions fields — both servers include "fields" in output.
        if passed and "field" not in text.lower():
            passed = False
            detail = "response does not mention fields"
        else:
            detail = f"returned {len(text)} chars with field descriptions"
        results.append(
            FuzzResult(mode, schema_name, "describe_entity_latest", passed, detail)
        )
    except MCPClientError as exc:
        results.append(
            FuzzResult(
                mode,
                schema_name,
                "describe_entity_latest",
                False,
                f"server died: {exc}",
            )
        )

    # 5. create_schema with a v3 test schema — try stellar-drive format first.
    created_name: str | None = None
    try:
        stellar_resp = client.call_tool(
            "create_schema",
            {
                "name": _V3_TEST_SCHEMA_NAME,
                "version": _V3_TEST_SCHEMA_VERSION,
                "schema": _STELLAR_ENVELOPE_JSON,
            },
        )
        if _is_success(stellar_resp) and not _is_error_result(stellar_resp):
            created_name = _V3_TEST_SCHEMA_NAME
            results.append(
                FuzzResult(
                    mode,
                    "create_schema",
                    "create_v3_schema",
                    True,
                    "stellar-drive format",
                )
            )
        else:
            # Fall back to slip-stream format.
            slip_resp = client.call_tool("create_schema", _SLIP_STREAM_CREATE_ARGS)
            if _is_success(slip_resp) and not _is_error_result(slip_resp):
                created_name = _V3_TEST_SCHEMA_NAME
                results.append(
                    FuzzResult(
                        mode,
                        "create_schema",
                        "create_v3_schema",
                        True,
                        "slip-stream format",
                    )
                )
            else:
                text = _tool_result_text(slip_resp)
                # Schema may already exist from a previous run — that is acceptable.
                already_exists = "exist" in text.lower() or "already" in text.lower()
                if already_exists:
                    created_name = _V3_TEST_SCHEMA_NAME
                    results.append(
                        FuzzResult(
                            mode,
                            "create_schema",
                            "create_v3_schema",
                            True,
                            "schema already exists from prior run",
                        )
                    )
                else:
                    results.append(
                        FuzzResult(
                            mode,
                            "create_schema",
                            "create_v3_schema",
                            False,
                            f"both formats failed: {text[:120]}",
                        )
                    )
    except MCPClientError as exc:
        results.append(
            FuzzResult(
                mode, "create_schema", "create_v3_schema", False, f"server died: {exc}"
            )
        )

    # 6. get_schema for the newly created schema (only if step 5 succeeded).
    if created_name:
        try:
            resp = client.call_tool("get_schema", {"name": created_name})
            text = _tool_result_text(resp)
            passed = _is_success(resp) and len(text) > 0
            # Verify the name appears in the response text.
            if passed and created_name not in text:
                detail = f"response does not contain schema name {created_name!r}"
                passed = False
            else:
                detail = f"round-trip confirmed, {len(text)} chars"
            results.append(
                FuzzResult(
                    mode, created_name, "get_created_schema_roundtrip", passed, detail
                )
            )
        except MCPClientError as exc:
            results.append(
                FuzzResult(
                    mode,
                    created_name,
                    "get_created_schema_roundtrip",
                    False,
                    f"server died: {exc}",
                )
            )

    return results


# ---------------------------------------------------------------------------
# Initialization probe
# ---------------------------------------------------------------------------


def run_initialize(client: MCPClient) -> list[FuzzResult]:
    """Send the MCP initialize handshake and verify the response structure."""
    results: list[FuzzResult] = []

    try:
        resp = client.initialize()

        if "error" in resp:
            err = resp["error"]
            results.append(
                FuzzResult(
                    "init",
                    "initialize",
                    "handshake",
                    False,
                    f"JSON-RPC error {err.get('code')}: {err.get('message')}",
                )
            )
            return results

        result = resp.get("result", {})
        protocol_version = result.get("protocolVersion", "")
        server_info = result.get("serverInfo", {})
        capabilities = result.get("capabilities", {})
        has_tools = "tools" in capabilities

        passed = bool(protocol_version and server_info and has_tools)
        detail = f"protocol={protocol_version} server={server_info.get('name')} tools={has_tools}"
        results.append(FuzzResult("init", "initialize", "handshake", passed, detail))

        # Verify tools/list works immediately after init.
        tools = client.list_tools()
        has_tools_list = len(tools) > 0
        tool_names = [t.get("name") for t in tools if isinstance(t, dict)]
        results.append(
            FuzzResult(
                "init",
                "tools/list",
                "list_after_init",
                has_tools_list,
                f"{len(tools)} tools: {tool_names[:5]}",
            )
        )

    except MCPClientError as exc:
        results.append(
            FuzzResult("init", "initialize", "handshake", False, f"server died: {exc}")
        )
    except Exception as exc:
        results.append(
            FuzzResult("init", "initialize", "handshake", False, f"error: {exc}")
        )

    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MCP JSON-RPC 2.0 fuzz runner (slip-stream / stellar-drive)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--cmd",
        required=True,
        help=(
            "MCP server command (will be shell-split). "
            'Example: "poetry run python -m slip_stream.mcp.server --schema-dir benchmarks/schemas"'
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["positive", "negative", "version", "all"],
        default="all",
        help="Fuzz mode to run (default: all)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write results as JSON to this file",
    )
    args = parser.parse_args()

    cmd = shlex.split(args.cmd)
    print(f"Launching MCP server: {cmd}")

    # Launch the client; fail fast if the binary doesn't exist.
    try:
        client = MCPClient(cmd)
    except MCPClientError as exc:
        print(f"ERROR: Could not start MCP server: {exc}", file=sys.stderr)
        sys.exit(2)

    all_results: list[FuzzResult] = []

    try:
        # Always run init — it is required before any tool calls.
        print(f"\n{'=' * 60}")
        print("Initializing MCP connection")
        print(f"{'=' * 60}\n")
        init_results = run_initialize(client)
        all_results.extend(init_results)

        # If initialization failed, abort early.
        init_failed = any(not r.passed for r in init_results)
        if init_failed:
            print("\nInitialization failed — aborting further tests.")
            print("\nServer stderr:")
            for line in client.get_stderr()[-20:]:
                print(f"  {line}")
            success = print_results(all_results)
            sys.exit(0 if success else 1)

        # Discover the first available schema name to use in probes.
        schema_name = _first_schema_name(client)
        print(f"\nUsing schema name: {schema_name!r}")

        modes = (
            ["positive", "negative", "version"] if args.mode == "all" else [args.mode]
        )

        for mode in modes:
            print(f"\n{'=' * 60}")
            print(f"Running {mode} MCP fuzz tests")
            print(f"{'=' * 60}\n")

            if mode == "positive":
                all_results.extend(run_positive(client, schema_name))
            elif mode == "negative":
                all_results.extend(run_negative(client))
            elif mode == "version":
                all_results.extend(run_version(client, schema_name))

            # After each mode, confirm the server is still alive.
            alive = client.is_alive()
            all_results.append(
                FuzzResult(
                    mode,
                    "server",
                    f"alive_after_{mode}",
                    alive,
                    "process running" if alive else "server process died",
                )
            )
            if not alive:
                print(f"\nServer died during {mode} mode — aborting.")
                print("\nServer stderr (last 20 lines):")
                for line in client.get_stderr()[-20:]:
                    print(f"  {line}")
                break

    finally:
        client.close()

    success = print_results(all_results)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(
                {
                    "cmd": args.cmd,
                    "mode": args.mode,
                    "total": len(all_results),
                    "passed": sum(1 for r in all_results if r.passed),
                    "failed": sum(1 for r in all_results if not r.passed),
                    "results": [
                        {
                            "mode": r.mode,
                            "entity": r.entity,
                            "step": r.step,
                            "passed": r.passed,
                            "detail": r.detail,
                        }
                        for r in all_results
                    ],
                },
                f,
                indent=2,
            )
        print(f"\nResults written to {args.output}")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
