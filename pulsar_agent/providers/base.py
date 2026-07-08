"""Transport interface and neutral message/completion types.

Internal message format (provider-neutral):
- {"role": "user", "content": str}
- {"role": "assistant", "content": str | None,
   "tool_calls": [{"id": str, "name": str, "arguments": dict}]}
- {"role": "tool", "tool_call_id": str, "name": str, "content": str}

Tool schemas are neutral dicts: {"name", "description", "parameters"} where
parameters is a JSON schema object. Transports adapt both directions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field


class ProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: dict


@dataclass
class CompletionResult:
    text: str | None = None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict = field(default_factory=dict)


class Transport(ABC):
    @abstractmethod
    def complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
        on_text: Callable[[str], None] | None = None,
    ) -> CompletionResult:
        """Run one completion. When `on_text` is given, transports that can
        stream call it with incremental text deltas as they arrive; the full
        CompletionResult is still returned at the end either way. Transports
        without streaming support may ignore `on_text`."""
        ...
