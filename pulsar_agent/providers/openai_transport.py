"""OpenAI-compatible chat completions transport (cloud and local endpoints),
with optional SSE streaming."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable

import httpx

from pulsar_agent.providers.anthropic_transport import iter_sse_data
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


def _parse_tool_arguments(raw_args: object) -> dict:
    if not raw_args:
        return {}
    try:
        arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except json.JSONDecodeError:
        return {"_raw": raw_args}
    return arguments if isinstance(arguments, dict) else {"_raw": raw_args}


def fold_openai_stream(
    chunks: Iterable[dict], on_text: Callable[[str], None] | None = None
) -> CompletionResult:
    """Accumulate chat-completions stream chunks into one CompletionResult.
    Pure function so tests can drive it with literal chunk dicts."""
    text_parts: list[str] = []
    calls: dict[int, dict] = {}
    stop_reason = ""
    usage: dict = {}
    for chunk in chunks:
        if chunk.get("usage"):
            usage.update(chunk["usage"])
        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        stop_reason = choice.get("finish_reason") or stop_reason
        delta = choice.get("delta") or {}
        content = delta.get("content")
        if content:
            text_parts.append(content)
            if on_text is not None:
                on_text(content)
        for call_delta in delta.get("tool_calls") or []:
            index = int(call_delta.get("index", 0))
            entry = calls.setdefault(index, {"id": "", "name": "", "arg_parts": []})
            if call_delta.get("id"):
                entry["id"] = call_delta["id"]
            function = call_delta.get("function") or {}
            if function.get("name"):
                entry["name"] = function["name"]
            if function.get("arguments"):
                entry["arg_parts"].append(function["arguments"])
    tool_calls = [
        ToolCallRequest(
            id=calls[index]["id"],
            name=calls[index]["name"],
            arguments=_parse_tool_arguments("".join(calls[index]["arg_parts"]) or "{}"),
        )
        for index in sorted(calls)
    ]
    return CompletionResult(
        text="".join(text_parts) or None,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        usage=usage,
    )


class ChatCompletionsTransport(Transport):
    def __init__(self, runtime: RuntimeProvider):
        self.runtime = runtime

    def _request_parts(
        self, system: str, messages: list[dict], tools: list[dict], max_tokens: int
    ) -> tuple[str, dict, dict]:
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
        return url, payload, headers

    def _raise_for_status(self, status_code: int, body: str) -> None:
        retryable = status_code == 429 or status_code >= 500 \
            or status_code in (401, 403)
        raise ProviderError(
            f"{self.runtime.profile.name} HTTP {status_code}: {body[:400]}",
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
            # Servers (esp. local/proxy) that reject streaming or
            # stream_options still work without them.
            if exc.status_code is not None and 400 <= exc.status_code < 500 \
                    and exc.status_code not in (401, 403, 429):
                return self._complete_once(url, payload, headers)
            raise

    def _complete_once(self, url: str, payload: dict, headers: dict) -> CompletionResult:
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            raise ProviderError(f"chat_completions request failed: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            self._raise_for_status(response.status_code, response.text)
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls: list[ToolCallRequest] = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            tool_calls.append(
                ToolCallRequest(
                    id=call.get("id", ""),
                    name=function.get("name", ""),
                    arguments=_parse_tool_arguments(function.get("arguments") or "{}"),
                )
            )
        return CompletionResult(
            text=message.get("content"),
            tool_calls=tool_calls,
            stop_reason=choice.get("finish_reason", ""),
            usage=data.get("usage", {}),
        )

    def _complete_stream(
        self, url: str, payload: dict, headers: dict, on_text: Callable[[str], None]
    ) -> CompletionResult:
        payload = {
            **payload,
            "stream": True,
            # Ask for a final usage chunk; widely supported (OpenAI, LM Studio,
            # ollama). A server that rejects it 4xxs and complete() falls back.
            "stream_options": {"include_usage": True},
        }
        try:
            with httpx.stream(
                "POST", url, json=payload, headers=headers, timeout=TIMEOUT_SECONDS
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    self._raise_for_status(response.status_code, response.text)
                return fold_openai_stream(iter_sse_data(response.iter_lines()), on_text)
        except httpx.HTTPError as exc:
            raise ProviderError(f"chat_completions stream failed: {exc}", retryable=True) from exc
