"""Core tool assembly. The always-on model toolset is capped at eight."""

from __future__ import annotations

from pulsar_agent.tools import (
    delegate_task,
    execute_code,
    file_tools,
    terminal,
    todo,
    web_tools,
)
from pulsar_agent.tools.registry import ToolContext, ToolRegistry, ToolSpec

CORE_TOOL_NAMES = (
    "read_file",
    "write_file",
    "patch",
    "search_files",
    "terminal",
    "execute_code",
    "todo",
    "delegate_task",
)

# Read-only web retrieval: should-have extras beyond the core eight, gated
# by `web.enabled` and hidden from subagents.
WEB_TOOL_NAMES = ("web_search", "web_extract")


def build_core_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for module in (file_tools, terminal, execute_code, todo, delegate_task, web_tools):
        for spec in module.build_specs():
            registry.register(spec)
    return registry


def build_subagent_registry(role: str) -> ToolRegistry:
    from pulsar_agent.tools.delegate_task import ROLE_TOOLS

    allowed = set(ROLE_TOOLS.get(role, ()))
    registry = ToolRegistry()
    for module in (file_tools, terminal):
        for spec in module.build_specs():
            if spec.name in allowed:
                registry.register(spec)
    return registry


__all__ = [
    "CORE_TOOL_NAMES",
    "WEB_TOOL_NAMES",
    "ToolContext",
    "ToolRegistry",
    "ToolSpec",
    "build_core_registry",
    "build_subagent_registry",
]
