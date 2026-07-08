"""Anthropic Messages API transport (with optional SSE streaming)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable

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


def iter_sse_data(lines: Iterable[str]) -> Iterable[dict]:
    """Yield parsed JSON objects from SSE `data:` lines. Ignores comments,
    event names, blank keep-alives, and unparseable payloads."""
    for line in lines:
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


def fold_anthropic_stream(
    events: Iterable[dict], on_text: Callable[[str], None] | None = None
) -> CompletionResult:
    """Accumulate Anthropic stream events into one CompletionResult.
    Pure function so tests can drive it with literal event dicts."""
    text_parts: list[str] = []
    blocks: dict[int, dict] = {}
    stop_reason = ""
    usage: dict = {}
    for event in events:
        kind = event.get("type", "")
        if kind == "message_start":
            usage.update((event.get("message") or {}).get("usage") or {})
        elif kind == "content_block_start":
            index = int(event.get("index", 0))
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use":
                blocks[index] = {
                    "type": "tool_use",
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "json_parts": [],
                }
            else:
                blocks[index] = {"type": "text"}
        elif kind == "content_block_delta":
            index = int(event.get("index", 0))
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                chunk = delta.get("text", "")
                if chunk:
                    text_parts.append(chunk)
                    if on_text is not None:
                        on_text(chunk)
            elif delta.get("type") == "input_json_delta":
                block = blocks.setdefault(
                    index, {"type": "tool_use", "id": "", "name": "", "json_parts": []}
                )
                block.setdefault("json_parts", []).append(delta.get("partial_json", ""))
        elif kind == "message_delta":
            stop_reason = (event.get("delta") or {}).get("stop_reason") or stop_reason
            usage.update(event.get("usage") or {})
        elif kind == "error":
            detail = (event.get("error") or {}).get("message", "unknown stream error")
            raise ProviderError(f"anthropic stream error: {detail}", retryable=True)
    tool_calls: list[ToolCallRequest] = []
    for index in sorted(blocks):
        block = blocks[index]
        if block.get("type") != "tool_use":
            continue
        raw = "".join(block.get("json_parts") or [])
        try:
            arguments = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            arguments = {"_raw": raw}
        tool_calls.append(
            ToolCallRequest(
                id=block.get("id", ""), name=block.get("name", ""), arguments=arguments
            )
        )
    return CompletionResult(
        text="".join(text_parts) or None,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        usage=usage,
    )


class AnthropicTransport(Transport):
    def __init__(self, runtime: RuntimeProvider):
        self.runtime = runtime

    def _request_parts(
        self, system: str, messages: list[dict], tools: list[dict], max_tokens: int
    ) -> tuple[str, dict, dict]:
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
        return url, payload, headers

    @staticmethod
    def _raise_for_status(status_code: int, body: str) -> None:
        retryable = status_code == 429 or status_code >= 500 \
            or status_code in (401, 403)
        raise ProviderError(
            f"anthropic HTTP {status_code}: {body[:400]}",
            status_code=status_code,
            retryable=retryable,
        )

    def complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
        on_text: Callable[[str], None] | None = None,
    ) -> CompletionResult:
        url, payload, headers = self._request_parts(system, messages, tools, max_tokens)
        if on_text is None:
            return self._complete_once(url, payload, headers)
        try:
            return self._complete_stream(url, payload, headers, on_text)
        except ProviderError as exc:
            # A server that rejects streaming still works without it.
            if exc.status_code is not None and 400 <= exc.status_code < 500 \
                    and exc.status_code not in (401, 403, 429):
                return self._complete_once(url, payload, headers)
            raise

    def _complete_once(self, url: str, payload: dict, headers: dict) -> CompletionResult:
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            raise ProviderError(f"anthropic request failed: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            self._raise_for_status(response.status_code, response.text)
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

    def _complete_stream(
        self, url: str, payload: dict, headers: dict, on_text: Callable[[str], None]
    ) -> CompletionResult:
        payload = {**payload, "stream": True}
        try:
            with httpx.stream(
                "POST", url, json=payload, headers=headers, timeout=TIMEOUT_SECONDS
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    self._raise_for_status(response.status_code, response.text)
                return fold_anthropic_stream(
                    iter_sse_data(response.iter_lines()), on_text
                )
        except httpx.HTTPError as exc:
            raise ProviderError(f"anthropic stream failed: {exc}", retryable=True) from exc
