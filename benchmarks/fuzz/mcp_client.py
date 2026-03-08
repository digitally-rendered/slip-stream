"""Minimal MCP client that talks to an MCP server subprocess via JSON-RPC 2.0 over stdio.

The client launches the server as a child process, writes newline-delimited
JSON-RPC 2.0 requests to its stdin, and reads responses from its stdout.
stderr is drained by a background thread so the child never blocks on output.

Example usage::

    client = MCPClient(["python", "-m", "slip_stream.mcp.server",
                        "--schema-dir", "benchmarks/schemas"])
    client.initialize()
    tools = client.list_tools()
    resp  = client.call_tool("list_schemas")
    client.close()
"""

from __future__ import annotations

import json
import subprocess
import threading
from typing import Any


class MCPClientError(Exception):
    """Raised when the MCP server process terminates unexpectedly."""


class MCPClient:
    """Thin JSON-RPC 2.0 client that drives an MCP server subprocess over stdio.

    The client is intentionally synchronous and single-threaded on the
    request/response path.  Only stderr draining runs on a background thread
    to prevent the child process from blocking when it produces log output.

    Args:
        cmd: Command list to launch the server.  The first element is the
             executable; the remainder are its arguments.  Example::

                 ["python", "-m", "slip_stream.mcp.server",
                  "--schema-dir", "./benchmarks/schemas"]

                 ["./stellar-drive", "mcp", "--config", "stellar.yaml"]
    """

    def __init__(self, cmd: list[str]) -> None:
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                # Use line-buffered I/O so newlines flush immediately.
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise MCPClientError(
                f"MCP server executable not found: {cmd[0]!r}. " f"Full command: {cmd}"
            ) from exc
        except PermissionError as exc:
            raise MCPClientError(
                f"Permission denied launching MCP server: {cmd[0]!r}"
            ) from exc

        self._id: int = 0
        self._stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()

        # Drain stderr in a background thread so the child never deadlocks
        # waiting for its stderr buffer to be consumed.
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_stderr(self) -> None:
        """Background thread: read stderr lines and store them."""
        try:
            assert self.proc.stderr is not None
            for line in self.proc.stderr:
                with self._stderr_lock:
                    self._stderr_lines.append(line.rstrip("\n"))
        except (OSError, ValueError):
            # Process already closed — ignore.
            pass

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _assert_alive(self) -> None:
        """Raise MCPClientError if the server process has already exited."""
        rc = self.proc.poll()
        if rc is not None:
            stderr_tail = self.get_stderr()[-5:]
            raise MCPClientError(
                f"MCP server exited with code {rc}. "
                f"Last stderr lines: {stderr_tail}"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one JSON-RPC 2.0 request and return the parsed response dict.

        Args:
            method: JSON-RPC method name (e.g. ``"tools/call"``).
            params: Optional parameters dict.

        Returns:
            The full JSON-RPC response dict, which may contain ``"result"``
            or ``"error"`` keys.

        Raises:
            MCPClientError: If the server process has died or stdout was closed.
            json.JSONDecodeError: If the server returns malformed JSON.
        """
        self._assert_alive()

        req: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
        }
        if params is not None:
            req["params"] = params

        line = json.dumps(req) + "\n"
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(line)
            self.proc.stdin.flush()
        except (OSError, BrokenPipeError) as exc:
            raise MCPClientError(f"Failed to write to MCP server stdin: {exc}") from exc

        assert self.proc.stdout is not None
        resp_line = self.proc.stdout.readline()
        if not resp_line:
            rc = self.proc.poll()
            raise MCPClientError(
                f"MCP server closed stdout unexpectedly (exit code: {rc}). "
                f"Last stderr: {self.get_stderr()[-5:]}"
            )

        return json.loads(resp_line)

    def initialize(self) -> dict[str, Any]:
        """Send the MCP ``initialize`` handshake.

        Returns:
            The full ``initialize`` response dict.
        """
        resp = self.send(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "fuzz-client", "version": "1.0.0"},
            },
        )
        # Send the ``initialized`` notification (no response expected).
        # We call send() and discard the result — the server must not reply.
        # Use a raw write instead because send() would wait for a response.
        notif = json.dumps({"jsonrpc": "2.0", "method": "initialized"}) + "\n"
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(notif)
            self.proc.stdin.flush()
        except (OSError, BrokenPipeError):
            pass
        return resp

    def list_tools(self) -> list[dict[str, Any]]:
        """Call ``tools/list`` and return the list of tool dicts.

        Returns:
            A list of tool description dicts, each with at least a ``"name"``
            key.  Returns an empty list on error.
        """
        resp = self.send("tools/list")
        return resp.get("result", {}).get("tools", [])

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call a named MCP tool and return the full JSON-RPC response.

        The response may have a ``"result"`` key (success) or an ``"error"``
        key (JSON-RPC level error).  Tool-level errors are communicated inside
        ``result.content[].text`` with ``result.isError = true``.

        Args:
            name: Tool name as declared in ``tools/list``.
            arguments: Optional dict of tool arguments.

        Returns:
            The full JSON-RPC response dict.
        """
        params: dict[str, Any] = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        return self.send("tools/call", params)

    def get_stderr(self) -> list[str]:
        """Return a snapshot of all stderr lines collected so far."""
        with self._stderr_lock:
            return list(self._stderr_lines)

    def is_alive(self) -> bool:
        """Return True if the server process is still running."""
        return self.proc.poll() is None

    def close(self) -> None:
        """Terminate the server subprocess and release resources.

        Safe to call multiple times.
        """
        if self.proc.poll() is not None:
            return  # Already exited.

        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass

        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
