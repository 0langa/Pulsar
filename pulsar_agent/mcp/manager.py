"""MCP server lifecycle + registry glue.

Turns configured, enabled, successfully-started servers into namespaced
ToolSpecs (`mcp_<server>_<tool>`). Servers that are disabled, missing, or
crash on startup contribute nothing to the model schema. Every invocation
goes through the approval pipeline (kind "mcp") and the registry's
redact-and-truncate path; tool descriptions from servers are untrusted and
therefore length-capped and prefixed with their origin.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from pulsar_agent.mcp.client import (
    DEFAULT_CALL_TIMEOUT,
    McpClient,
    McpError,
    McpServerSpec,
)
from pulsar_agent.security.approvals import KIND_MCP, ApprovalRequest
from pulsar_agent.tools.registry import ToolContext, ToolSpec

MAX_DESCRIPTION_CHARS = 400
MCP_OUTPUT_LIMIT = 20000
# Auto-restart cap per server per process run; a flapping server should not
# be restarted forever.
MAX_AUTO_RESTARTS = 3

_NAME_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_]")


def _sanitize(name: str) -> str:
    return _NAME_SANITIZE_RE.sub("_", name).strip("_") or "unnamed"


def mcp_tool_name(server: str, tool: str) -> str:
    return f"mcp_{_sanitize(server)}_{_sanitize(tool)}"


class McpManager:
    def __init__(self, config: dict, warn: Callable[[str], None] | None = None):
        self._config = config
        self._warn = warn or (lambda message: None)
        self.clients: dict[str, McpClient] = {}
        self.errors: dict[str, str] = {}
        self.specs: dict[str, McpServerSpec] = {}
        self.restarts: dict[str, int] = {}

    def start(self) -> None:
        """Start every explicitly enabled server; record failures, never raise."""
        for entry in (self._config.get("mcp", {}) or {}).get("servers") or []:
            spec = McpServerSpec.from_config(entry)
            self.specs[spec.name] = spec
            if not spec.enabled:
                continue
            self._start_client(spec)

    def _start_client(self, spec: McpServerSpec) -> McpClient | None:
        client = McpClient(spec)
        try:
            client.start()
        except McpError as exc:
            client.close()
            self.errors[spec.name] = str(exc)
            self._warn(f"mcp server {spec.name!r} unavailable: {exc}")
            return None
        self.clients[spec.name] = client
        self.errors.pop(spec.name, None)
        return client

    def ensure_client(self, server_name: str) -> McpClient | None:
        """Return a live client for the server, restarting it once per call if
        it has died (capped at MAX_AUTO_RESTARTS per run)."""
        client = self.clients.get(server_name)
        if client is not None and client.alive():
            return client
        spec = self.specs.get(server_name)
        if spec is None or not spec.enabled:
            return None
        attempts = self.restarts.get(server_name, 0)
        if attempts >= MAX_AUTO_RESTARTS:
            return None
        self.restarts[server_name] = attempts + 1
        if client is not None:
            client.close()
            self.clients.pop(server_name, None)
        self._warn(
            f"mcp server {server_name!r} stopped; restarting "
            f"({attempts + 1}/{MAX_AUTO_RESTARTS})"
        )
        return self._start_client(spec)

    def status(self) -> list[dict]:
        """One row per configured server for /mcp display."""
        rows: list[dict] = []
        for name, spec in self.specs.items():
            client = self.clients.get(name)
            if not spec.enabled:
                state = "disabled"
            elif client is not None and client.alive():
                state = "running"
            elif name in self.errors:
                state = "failed"
            else:
                state = "stopped"
            rows.append(
                {
                    "name": name,
                    "state": state,
                    "tools": len(client.tools) if client is not None else 0,
                    "restarts": self.restarts.get(name, 0),
                    "error": self.errors.get(name, ""),
                }
            )
        return rows

    def close(self) -> None:
        for client in self.clients.values():
            client.close()
        self.clients.clear()

    def tool_specs(self) -> list[ToolSpec]:
        specs: list[ToolSpec] = []
        for server_name, client in self.clients.items():
            allowed = client.spec.allowed_tools
            for tool in client.tools:
                tool_name = str(tool.get("name", ""))
                if allowed is not None and tool_name not in allowed:
                    continue
                specs.append(self._make_spec(server_name, client, tool))
        return specs

    def _make_spec(self, server_name: str, client: McpClient, tool: dict) -> ToolSpec:
        tool_name = str(tool["name"])
        description = str(tool.get("description") or "").strip()
        if len(description) > MAX_DESCRIPTION_CHARS:
            description = description[:MAX_DESCRIPTION_CHARS] + "…"
        parameters = tool.get("inputSchema")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            parameters = {"type": "object", "properties": {}}

        def handler(args: dict, context: ToolContext, _tool=tool_name) -> str:
            # Resolve the client through the manager at call time so an
            # auto-restarted server is picked up (a captured client would be
            # a stale, dead handle after restart).
            return _invoke(self, server_name, _tool, args, context)

        return ToolSpec(
            name=mcp_tool_name(server_name, tool_name),
            description=f"[MCP tool from server {server_name!r}] {description}",
            parameters=parameters,
            handler=handler,
            # MCP tools are for the main agent only; subagents stay leaf.
            check_fn=lambda ctx: not ctx.is_subagent,
        )


def _invoke(
    manager: McpManager,
    server_name: str,
    tool_name: str,
    args: dict,
    context: ToolContext,
) -> str:
    context.approvals.check(
        ApprovalRequest(
            kind=KIND_MCP,
            description=f"{server_name}:{tool_name}",
            detail=f"call MCP tool {tool_name!r} on server {server_name!r}",
            cwd=str(context.workspace),
        )
    )
    context.emit("mcp", f"{server_name}:{tool_name}")
    client = manager.ensure_client(server_name)
    if client is None:
        return (
            f"ERROR: mcp server {server_name!r} is not running and could not "
            "be restarted; check its command in config.yaml (see /mcp)"
        )
    if tool_name not in {str(t.get("name", "")) for t in client.tools}:
        return (
            f"ERROR: mcp server {server_name!r} no longer offers tool "
            f"{tool_name!r} after restart"
        )
    try:
        output = client.call_tool(tool_name, args, timeout=DEFAULT_CALL_TIMEOUT)
    except McpError as exc:
        return f"ERROR: {exc}"
    if len(output) > MCP_OUTPUT_LIMIT:
        output = output[:MCP_OUTPUT_LIMIT] + "\n[output truncated]"
    return output
