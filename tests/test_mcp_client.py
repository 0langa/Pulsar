from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pulsar_agent.mcp.client import McpClient, McpError, McpServerSpec
from pulsar_agent.mcp.manager import McpManager, mcp_tool_name
from pulsar_agent.security.redaction import Redactor
from pulsar_agent.tools.registry import ToolRegistry
from tests.conftest import make_context

FAKE_SERVER = r'''
import json, os, sys, time

MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"

def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()

TOOLS = [
    {
        "name": "echo",
        "description": "Echo the given text back.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "env_probe",
        "description": "List PULSARTEST_* environment variable names.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "secret_leak",
        "description": "Return a fake credential string.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "boom",
        "description": "Always fails.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    message = json.loads(line)
    method = message.get("method")
    mid = message.get("id")
    if method == "initialize":
        if MODE == "mute":
            time.sleep(60)
            continue
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1.0"},
        }})
        if MODE == "crash":
            sys.exit(3)
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        if MODE == "slowcall":
            time.sleep(60)
        name = message["params"]["name"]
        args = message["params"].get("arguments") or {}
        if name == "boom":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "it broke"}], "isError": True,
            }})
            continue
        if name == "echo":
            text = str(args.get("text", ""))
        elif name == "env_probe":
            names = sorted(k for k in os.environ if k.startswith("PULSARTEST_"))
            text = "VARS:" + ",".join(names)
        elif name == "secret_leak":
            text = "token sk-fake-mcp-leak-abcdef1234567890"
        else:
            text = "unknown tool"
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": text}], "isError": False,
        }})
'''


@pytest.fixture
def server_script(tmp_path) -> Path:
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    return script


def server_entry(script: Path, mode: str = "normal", **overrides) -> dict:
    entry = {
        "name": "fake",
        "command": sys.executable,
        "args": [str(script), mode],
        "enabled": True,
        "startup_timeout": 15,
    }
    entry.update(overrides)
    return entry


def mcp_config(config: dict, *entries: dict) -> dict:
    config["mcp"]["servers"] = list(entries)
    return config


def make_manager(config: dict) -> McpManager:
    manager = McpManager(config)
    manager.start()
    return manager


# --- discovery, namespacing, gating ---


def test_disabled_by_default(config, server_script):
    mcp_config(config, server_entry(server_script, enabled=False))
    manager = make_manager(config)
    assert manager.clients == {}
    assert manager.tool_specs() == []
    manager.close()


def test_no_servers_configured_is_noop(config):
    manager = make_manager(config)
    assert manager.tool_specs() == []
    manager.close()


def test_discovery_and_namespacing(config, server_script):
    mcp_config(config, server_entry(server_script))
    manager = make_manager(config)
    try:
        names = [spec.name for spec in manager.tool_specs()]
        assert "mcp_fake_echo" in names
        assert "mcp_fake_env_probe" in names
        for spec in manager.tool_specs():
            assert spec.name.startswith("mcp_fake_")
            assert "MCP tool from server" in spec.description
    finally:
        manager.close()


def test_tool_name_sanitization():
    assert mcp_tool_name("my-server", "read.file") == "mcp_my_server_read_file"


def test_allowed_tools_filter(config, server_script):
    mcp_config(config, server_entry(server_script, allowed_tools=["echo"]))
    manager = make_manager(config)
    try:
        names = [spec.name for spec in manager.tool_specs()]
        assert names == ["mcp_fake_echo"]
    finally:
        manager.close()


def test_mcp_tools_hidden_from_subagents(config, server_script, workspace, home):
    mcp_config(config, server_entry(server_script))
    manager = make_manager(config)
    try:
        registry = ToolRegistry()
        for spec in manager.tool_specs():
            registry.register(spec)
        sub_context = make_context(workspace, home, config, is_subagent=True)
        assert registry.enabled(sub_context) == []
    finally:
        manager.close()


# --- invocation, approval, redaction ---


def test_tool_invocation_through_registry(config, server_script, workspace, home):
    mcp_config(config, server_entry(server_script))
    manager = make_manager(config)
    try:
        registry = ToolRegistry()
        for spec in manager.tool_specs():
            registry.register(spec)
        context = make_context(
            workspace, home, config, approver=lambda request: True
        )
        out = registry.dispatch("mcp_fake_echo", {"text": "ping-pulsar"}, context)
        assert "ping-pulsar" in out
    finally:
        manager.close()


def test_invocation_requires_approval(config, server_script, workspace, home):
    mcp_config(config, server_entry(server_script))
    manager = make_manager(config)
    try:
        registry = ToolRegistry()
        for spec in manager.tool_specs():
            registry.register(spec)
        context = make_context(
            workspace, home, config, preset="review", approver=None
        )
        out = registry.dispatch("mcp_fake_echo", {"text": "x"}, context)
        assert "BLOCKED" in out
    finally:
        manager.close()


def test_server_error_result(config, server_script, workspace, home):
    mcp_config(config, server_entry(server_script))
    manager = make_manager(config)
    try:
        registry = ToolRegistry()
        for spec in manager.tool_specs():
            registry.register(spec)
        context = make_context(workspace, home, config, approver=lambda r: True)
        out = registry.dispatch("mcp_fake_boom", {}, context)
        assert "ERROR (from mcp server)" in out
        assert "it broke" in out
    finally:
        manager.close()


def test_output_redacted(config, server_script, workspace, home):
    mcp_config(config, server_entry(server_script))
    manager = make_manager(config)
    try:
        registry = ToolRegistry()
        for spec in manager.tool_specs():
            registry.register(spec)
        context = make_context(
            workspace, home, config,
            approver=lambda request: True,
            redactor=Redactor(["sk-fake-mcp-leak-abcdef1234567890"]),
        )
        out = registry.dispatch("mcp_fake_secret_leak", {}, context)
        assert "sk-fake-mcp-leak" not in out
        assert "[REDACTED]" in out
    finally:
        manager.close()


# --- env filtering ---


def test_subprocess_env_allowlist_first(config, server_script, workspace, home, monkeypatch):
    monkeypatch.setenv("PULSARTEST_ALLOWED", "yes")
    monkeypatch.setenv("PULSARTEST_SECRET_KEY", "must-not-pass")
    monkeypatch.setenv("PULSARTEST_BLAND", "also-must-not-pass")
    mcp_config(
        config,
        server_entry(server_script, env_passthrough=["PULSARTEST_ALLOWED"]),
    )
    manager = make_manager(config)
    try:
        registry = ToolRegistry()
        for spec in manager.tool_specs():
            registry.register(spec)
        context = make_context(workspace, home, config, approver=lambda r: True)
        out = registry.dispatch("mcp_fake_env_probe", {}, context)
        assert "PULSARTEST_ALLOWED" in out
        # Allowlist-first: even a bland-named var is stripped unless declared.
        assert "PULSARTEST_BLAND" not in out
        assert "PULSARTEST_SECRET_KEY" not in out
    finally:
        manager.close()


# --- failure modes ---


def test_startup_timeout(config, server_script):
    mcp_config(config, server_entry(server_script, "mute", startup_timeout=2))
    manager = make_manager(config)
    try:
        assert "fake" in manager.errors
        assert "no response" in manager.errors["fake"]
        assert manager.tool_specs() == []
    finally:
        manager.close()


def test_server_crash_during_startup(config, server_script):
    mcp_config(config, server_entry(server_script, "crash"))
    manager = make_manager(config)
    try:
        assert "fake" in manager.errors
        assert manager.tool_specs() == []
    finally:
        manager.close()


def test_call_timeout(server_script):
    spec = McpServerSpec(
        name="fake",
        command=sys.executable,
        args=[str(server_script), "slowcall"],
        enabled=True,
        startup_timeout=15,
    )
    client = McpClient(spec)
    client.start()
    try:
        with pytest.raises(McpError, match="no response"):
            client.call_tool("echo", {"text": "x"}, timeout=2)
    finally:
        client.close()


def test_server_death_mid_session(config, server_script, workspace, home):
    mcp_config(config, server_entry(server_script))
    manager = make_manager(config)
    try:
        registry = ToolRegistry()
        for spec in manager.tool_specs():
            registry.register(spec)
        manager.clients["fake"].close()  # simulate crash
        context = make_context(workspace, home, config, approver=lambda r: True)
        out = registry.dispatch("mcp_fake_echo", {"text": "x"}, context)
        assert "ERROR" in out
        assert "stopped" in out
    finally:
        manager.close()


def test_missing_command_fails_gracefully(config):
    mcp_config(config, {
        "name": "ghost",
        "command": "definitely-not-a-real-binary-xyz",
        "enabled": True,
    })
    manager = make_manager(config)
    try:
        assert "ghost" in manager.errors
        assert manager.tool_specs() == []
    finally:
        manager.close()
