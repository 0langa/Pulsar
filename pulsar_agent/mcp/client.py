"""Stdio MCP client: JSON-RPC 2.0 over newline-delimited stdin/stdout.

Scope: initialize handshake, tools/list discovery, tools/call invocation.
No HTTP/SSE transport, no resources/prompts/sampling (deferred).

Security posture:
- Servers are opt-in per entry (`enabled: true`); nothing starts by default.
- The server subprocess env is allowlist-first: fixed baseline plus the
  variable names declared in `env_passthrough`. Undeclared secrets never
  reach the server.
- Server stderr is captured to a bounded buffer, never streamed to console.
- All responses are parsed defensively; a crashed or silent server surfaces
  as McpError, not a hang (startup and per-call timeouts).
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field

from pulsar_agent.tools.terminal import allowlist_environment

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "pulsar", "version": "0.2"}
DEFAULT_STARTUP_TIMEOUT = 20
DEFAULT_CALL_TIMEOUT = 60
MAX_STDERR_CHARS = 4000


class McpError(RuntimeError):
    pass


@dataclass
class McpServerSpec:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    enabled: bool = False
    allowed_tools: list[str] | None = None  # None = all discovered tools
    env_passthrough: list[str] = field(default_factory=list)
    startup_timeout: int = DEFAULT_STARTUP_TIMEOUT

    @classmethod
    def from_config(cls, entry: dict) -> "McpServerSpec":
        allowed = entry.get("allowed_tools")
        return cls(
            name=str(entry["name"]),
            command=str(entry["command"]),
            args=[str(a) for a in entry.get("args") or []],
            cwd=str(entry["cwd"]) if entry.get("cwd") else None,
            enabled=bool(entry.get("enabled", False)),
            allowed_tools=[str(t) for t in allowed] if allowed is not None else None,
            env_passthrough=[str(v) for v in entry.get("env_passthrough") or []],
            startup_timeout=int(entry.get("startup_timeout", DEFAULT_STARTUP_TIMEOUT)),
        )


class McpClient:
    def __init__(self, spec: McpServerSpec):
        self.spec = spec
        self.tools: list[dict] = []
        self._process: subprocess.Popen | None = None
        self._messages: queue.Queue = queue.Queue()
        self._pending: dict[int, dict] = {}
        self._next_id = 0
        self._stderr_tail = ""
        self._lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        """Spawn the server, perform the initialize handshake, discover tools."""
        env = allowlist_environment(self.spec.env_passthrough)
        try:
            self._process = subprocess.Popen(
                [self.spec.command, *self.spec.args],
                cwd=self.spec.cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise McpError(f"cannot start mcp server {self.spec.name!r}: {exc}") from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

        timeout = self.spec.startup_timeout
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            timeout=timeout,
        )
        if not isinstance(result, dict):
            raise McpError(f"mcp server {self.spec.name!r}: bad initialize result")
        self._notify("notifications/initialized")
        self.tools = self.list_tools(timeout=timeout)

    def close(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        except OSError:
            pass

    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    # -- protocol ------------------------------------------------------

    def list_tools(self, timeout: int = DEFAULT_CALL_TIMEOUT) -> list[dict]:
        result = self._request("tools/list", {}, timeout=timeout)
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise McpError(f"mcp server {self.spec.name!r}: bad tools/list result")
        out = []
        for tool in tools:
            if isinstance(tool, dict) and tool.get("name"):
                out.append(tool)
        return out

    def call_tool(
        self, name: str, arguments: dict, timeout: int = DEFAULT_CALL_TIMEOUT
    ) -> str:
        result = self._request(
            "tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout
        )
        if not isinstance(result, dict):
            raise McpError(f"mcp tool {name!r}: bad result")
        parts: list[str] = []
        for item in result.get("content") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(f"[unsupported content type {item.get('type')!r}]")
        text = "\n".join(parts).strip()
        if result.get("isError"):
            return f"ERROR (from mcp server): {text or 'tool reported an error'}"
        return text or "(empty result)"

    # -- plumbing ------------------------------------------------------

    def _read_stdout(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue  # non-protocol noise on stdout
            if isinstance(message, dict):
                self._messages.put(message)
        self._messages.put({"__eof__": True})

    def _read_stderr(self) -> None:
        process = self._process
        assert process is not None and process.stderr is not None
        for line in process.stderr:
            self._stderr_tail = (self._stderr_tail + line)[-MAX_STDERR_CHARS:]

    def _write(self, payload: dict) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise McpError(self._death_message())
        try:
            process.stdin.write(json.dumps(payload) + "\n")
            process.stdin.flush()
        except OSError as exc:
            raise McpError(self._death_message()) from exc

    def _notify(self, method: str, params: dict | None = None) -> None:
        message: dict = {"jsonrpc": "2.0", "method": method}
        if params:
            message["params"] = params
        self._write(message)

    def _request(self, method: str, params: dict, timeout: int):
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
        self._write(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        deadline = time.monotonic() + timeout
        if request_id in self._pending:
            return self._finish(self._pending.pop(request_id), method)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpError(
                    f"mcp server {self.spec.name!r}: no response to {method} "
                    f"within {timeout}s"
                )
            try:
                message = self._messages.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            if message.get("__eof__"):
                raise McpError(self._death_message())
            msg_id = message.get("id")
            if msg_id == request_id:
                return self._finish(message, method)
            if msg_id is not None and "method" not in message:
                self._pending[msg_id] = message  # response to another request
            # notifications and server->client requests are ignored

    def _finish(self, message: dict, method: str):
        if "error" in message:
            error = message["error"] or {}
            raise McpError(
                f"mcp server {self.spec.name!r} {method} failed: "
                f"{error.get('message', 'unknown error')} (code {error.get('code')})"
            )
        return message.get("result")

    def _death_message(self) -> str:
        code = self._process.poll() if self._process else None
        detail = f" [stderr tail]\n{self._stderr_tail.strip()}" if self._stderr_tail else ""
        return (
            f"mcp server {self.spec.name!r} is not running "
            f"(exit code {code}).{detail}"
        )
