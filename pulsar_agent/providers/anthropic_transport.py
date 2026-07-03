"""Anthropic Messages API transport."""

from __future__ import annotations

import json

import httpx

from pulsar_agent.providers.base import (
    CompletionResult,
    ProviderError,
    ToolCallRequest,
    Transport,
)
from pulsar_agent.providers.router import RuntimeProvider

API_VERSION = "2023-06-01"
TIMEOUT_SECONDS = 300.0


def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for msg in messages:
        role = msg["role"]
        if role == "user":
            out.append({"role": "user", "content": msg["content"]})
        elif role == "assistant":
            blocks: list[dict] = []
            if msg.get("content"):
                blocks.append({"type": "text", "text": msg["content"]})
            for call in msg.get("tool_calls") or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["name"],
                        "input": call["arguments"],
                    }
                )
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
        elif role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": msg["tool_call_id"],
                "content": msg["content"],
            }
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


class AnthropicTransport(Transport):
    def __init__(self, runtime: RuntimeProvider):
        self.runtime = runtime

    def complete(
        self, system: str, messages: list[dict], tools: list[dict], max_tokens: int
    ) -> CompletionResult:
        payload: dict = {
            "model": self.runtime.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": _to_anthropic_messages(messages),
        }
        if tools:
            payload["tools"] = [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["parameters"],
                }
                for t in tools
            ]
        headers = {
            "x-api-key": self.runtime.api_key or "",
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        url = self.runtime.profile.base_url.rstrip("/") + "/v1/messages"
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            raise ProviderError(f"anthropic request failed: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            retryable = response.status_code == 429 or response.status_code >= 500 \
                or response.status_code in (401, 403)
            raise ProviderError(
                f"anthropic HTTP {response.status_code}: {response.text[:400]}",
                status_code=response.status_code,
                retryable=retryable,
            )
        data = response.json()
        text_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                arguments = block.get("input")
                if isinstance(arguments, str):
                    arguments = json.loads(arguments or "{}")
                tool_calls.append(
                    ToolCallRequest(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        arguments=arguments or {},
                    )
                )
        return CompletionResult(
            text="\n".join(part for part in text_parts if part) or None,
            tool_calls=tool_calls,
            stop_reason=data.get("stop_reason", ""),
            usage=data.get("usage", {}),
        )
