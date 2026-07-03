"""Central tool registry with check_fn gating.

Every tool — builtin now, MCP/plugins later — goes through one register/
dispatch path. `check_fn(context)` decides whether a tool is exposed for a
given session; the enabled set is resolved once per session so the model
tool schema stays stable (prompt-cache friendly).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pulsar_agent.security.approvals import ApprovalManager
from pulsar_agent.security.paths import PathPolicy
from pulsar_agent.security.redaction import Redactor

MAX_TOOL_RESULT_CHARS = 30000


@dataclass
class ToolContext:
    workspace: Path
    home: Path
    config: dict
    path_policy: PathPolicy
    approvals: ApprovalManager
    redactor: Redactor
    checkpoints: object | None = None
    session_id: str | None = None
    read_files: set = field(default_factory=set)
    todos: list = field(default_factory=list)
    is_subagent: bool = False
    subagent_role: str | None = None
    runtime_provider: object | None = None
    transport: object | None = None
    on_tool_event: Callable[[str, str], None] | None = None

    def emit(self, event: str, detail: str) -> None:
        if self.on_tool_event:
            self.on_tool_event(event, detail)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict, ToolContext], str]
    check_fn: Callable[[ToolContext], bool] | None = None

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name!r} already registered")
        self._tools[spec.name] = spec

    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def enabled(self, context: ToolContext) -> list[ToolSpec]:
        return [
            spec
            for _, spec in sorted(self._tools.items())
            if spec.check_fn is None or spec.check_fn(context)
        ]

    def schemas(self, context: ToolContext) -> list[dict]:
        return [spec.schema() for spec in self.enabled(context)]

    def dispatch(self, name: str, arguments: dict, context: ToolContext) -> str:
        spec = self._tools.get(name)
        if spec is None or (spec.check_fn is not None and not spec.check_fn(context)):
            return f"ERROR: unknown or disabled tool {name!r}"
        try:
            result = spec.handler(arguments or {}, context)
        except PermissionError as exc:
            result = f"BLOCKED: {exc}"
        except Exception as exc:  # noqa: BLE001 - tool failures go back to the model
            result = f"ERROR: {type(exc).__name__}: {exc}"
        result = context.redactor.redact(str(result))
        if len(result) > MAX_TOOL_RESULT_CHARS:
            result = result[:MAX_TOOL_RESULT_CHARS] + "\n[output truncated]"
        return result
