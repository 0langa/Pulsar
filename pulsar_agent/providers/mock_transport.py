"""Deterministic mock transport for tests and offline demos.

If PULSAR_MOCK_SCRIPT points to a JSON file containing a list of steps,
each `complete()` call pops the next step:

  [{"text": "final answer"},
   {"tool_calls": [{"name": "read_file", "arguments": {"path": "x.py"}}]}]

Without a script it echoes the last user message.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pulsar_agent.providers.base import CompletionResult, ToolCallRequest, Transport
from pulsar_agent.providers.router import RuntimeProvider

SCRIPT_ENV_VAR = "PULSAR_MOCK_SCRIPT"


class MockTransport(Transport):
    def __init__(self, runtime: RuntimeProvider, script: list[dict] | None = None):
        self.runtime = runtime
        self._steps: list[dict] | None = script
        if self._steps is None:
            script_path = os.environ.get(SCRIPT_ENV_VAR, "")
            if script_path and Path(script_path).exists():
                self._steps = json.loads(Path(script_path).read_text(encoding="utf-8"))
        self._index = 0

    def complete(
        self, system: str, messages: list[dict], tools: list[dict], max_tokens: int
    ) -> CompletionResult:
        if self._steps is not None and self._index < len(self._steps):
            step = self._steps[self._index]
            self._index += 1
            calls = [
                ToolCallRequest(
                    id=call.get("id", f"mock_call_{i}"),
                    name=call["name"],
                    arguments=call.get("arguments", {}),
                )
                for i, call in enumerate(step.get("tool_calls") or [])
            ]
            return CompletionResult(
                text=step.get("text"),
                tool_calls=calls,
                stop_reason="tool_use" if calls else "end_turn",
                # Deterministic usage so token accounting is testable offline.
                usage=step.get("usage") or {"input_tokens": 10, "output_tokens": 5},
            )
        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        return CompletionResult(
            text=f"echo: {last_user}",
            stop_reason="end_turn",
            usage={"input_tokens": 10, "output_tokens": 5},
        )
