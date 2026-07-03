"""OpenAI-compatible chat completions transport (cloud and local endpoints)."""

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

TIMEOUT_SECONDS = 300.0


def _to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}]
    for msg in messages:
        role = msg["role"]
        if role == "user":
            out.append({"role": "user", "content": msg["content"]})
        elif role == "assistant":
            entry: dict = {"role": "assistant", "content": msg.get("content") or None}
            calls = msg.get("tool_calls") or []
            if calls:
                entry["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call["arguments"]),
                        },
                    }
                    for call in calls
                ]
            out.append(entry)
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": msg["tool_call_id"],
                    "content": msg["content"],
                }
            )
    return out


class ChatCompletionsTransport(Transport):
    def __init__(self, runtime: RuntimeProvider):
        self.runtime = runtime

    def complete(
        self, system: str, messages: list[dict], tools: list[dict], max_tokens: int
    ) -> CompletionResult:
        payload: dict = {
            "model": self.runtime.model,
            "max_tokens": max_tokens,
            "messages": _to_openai_messages(system, messages),
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["parameters"],
                    },
                }
                for t in tools
            ]
        headers = {"content-type": "application/json"}
        if self.runtime.api_key:
            headers["authorization"] = f"Bearer {self.runtime.api_key}"
        url = self.runtime.profile.base_url.rstrip("/") + "/chat/completions"
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            raise ProviderError(f"chat_completions request failed: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            retryable = response.status_code == 429 or response.status_code >= 500 \
                or response.status_code in (401, 403)
            raise ProviderError(
                f"{self.runtime.profile.name} HTTP {response.status_code}: {response.text[:400]}",
                status_code=response.status_code,
                retryable=retryable,
            )
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls: list[ToolCallRequest] = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                arguments = {"_raw": raw_args}
            tool_calls.append(
                ToolCallRequest(
                    id=call.get("id", ""),
                    name=function.get("name", ""),
                    arguments=arguments or {},
                )
            )
        return CompletionResult(
            text=message.get("content"),
            tool_calls=tool_calls,
            stop_reason=choice.get("finish_reason", ""),
            usage=data.get("usage", {}),
        )
