from __future__ import annotations

import json

import pytest

from pulsar_agent.cli.repl import StreamSink
from pulsar_agent.providers.anthropic_transport import (
    AnthropicTransport,
    fold_anthropic_stream,
    iter_sse_data,
)
from pulsar_agent.providers.base import ProviderError
from pulsar_agent.providers.openai_transport import (
    ChatCompletionsTransport,
    fold_openai_stream,
)
from pulsar_agent.providers.router import RuntimeProvider, resolve_runtime_provider
from pulsar_agent.secrets import SecretStore

# --- SSE line parsing ---


def test_iter_sse_data_parses_and_skips_noise():
    lines = [
        ": keep-alive comment",
        "event: content_block_delta",
        'data: {"type": "ping"}',
        "data:",
        "data: [DONE]",
        "data: not-json{{{",
        'data: {"ok": 1}',
        "",
    ]
    assert list(iter_sse_data(lines)) == [{"type": "ping"}, {"ok": 1}]


# --- Anthropic stream folding ---


def _anthropic_events():
    return [
        {"type": "message_start", "message": {"usage": {"input_tokens": 12}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "Hello "}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "world"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "id": "tu_1", "name": "read_file"}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": '{"path": "a'}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": '.py"}'}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
         "usage": {"output_tokens": 7}},
        {"type": "message_stop"},
    ]


def test_fold_anthropic_stream_text_tools_usage():
    deltas: list[str] = []
    result = fold_anthropic_stream(_anthropic_events(), deltas.append)
    assert result.text == "Hello world"
    assert deltas == ["Hello ", "world"]
    assert result.stop_reason == "tool_use"
    assert result.usage == {"input_tokens": 12, "output_tokens": 7}
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert (call.id, call.name) == ("tu_1", "read_file")
    assert call.arguments == {"path": "a.py"}


def test_fold_anthropic_stream_error_event_raises():
    events = [{"type": "error", "error": {"message": "overloaded"}}]
    with pytest.raises(ProviderError, match="overloaded"):
        fold_anthropic_stream(events)


def test_fold_anthropic_stream_bad_tool_json_preserved_raw():
    events = [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "tu", "name": "patch"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": "{broken"}},
    ]
    result = fold_anthropic_stream(events)
    assert result.tool_calls[0].arguments == {"_raw": "{broken"}


# --- OpenAI stream folding ---


def _openai_chunks():
    return [
        {"choices": [{"delta": {"content": "Hi "}}]},
        {"choices": [{"delta": {"content": "there"}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1",
             "function": {"name": "search_files", "arguments": '{"que'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": 'ry": "x"}'}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 9}},
    ]


def test_fold_openai_stream_text_tools_usage():
    deltas: list[str] = []
    result = fold_openai_stream(_openai_chunks(), deltas.append)
    assert result.text == "Hi there"
    assert deltas == ["Hi ", "there"]
    assert result.stop_reason == "tool_calls"
    assert result.usage == {"prompt_tokens": 20, "completion_tokens": 9}
    call = result.tool_calls[0]
    assert (call.id, call.name) == ("call_1", "search_files")
    assert call.arguments == {"query": "x"}


# --- StreamSink (line-buffered redaction) ---


def test_stream_sink_line_buffering_and_redaction():
    emitted: list[str] = []
    secret = "sk-verysecretvalue"
    sink = StreamSink(
        redact=lambda text: text.replace(secret, "[redacted]"),
        emit=emitted.append,
    )
    # Secret split across two deltas within one line: the sink must not emit
    # until the newline so the redactor sees the complete value.
    sink.write("token: sk-verysecret")
    assert emitted == []
    sink.write("value is set\nnext")
    assert emitted == ["token: [redacted] is set"]
    sink.flush()
    assert emitted == ["token: [redacted] is set", "next"]
    assert sink.streamed is True
    sink.reset()
    assert sink.streamed is False


# --- transport fallback: server rejects streaming -> non-streaming retry ---


class _FakeResponse:
    def __init__(self, status_code: int, body: str = "", lines: list[str] | None = None):
        self.status_code = status_code
        self.text = body
        self._lines = lines or []

    def read(self) -> None:
        pass

    def json(self) -> dict:
        return json.loads(self.text)

    def iter_lines(self):
        yield from self._lines


class _FakeStreamCM:
    def __init__(self, response: _FakeResponse):
        self._response = response

    def __enter__(self) -> _FakeResponse:
        return self._response

    def __exit__(self, *exc) -> bool:
        return False


def _runtime(home, model_id="openai:gpt-test") -> RuntimeProvider:
    secrets = SecretStore(home)
    secrets.set("OPENAI_API_KEY", "sk-test-not-a-real-key-123")
    secrets.set("ANTHROPIC_API_KEY", "sk-ant-test-not-real-456")
    return resolve_runtime_provider(model_id, {}, secrets)


def test_openai_stream_rejected_falls_back(home, monkeypatch):
    transport = ChatCompletionsTransport(_runtime(home))
    monkeypatch.setattr(
        "pulsar_agent.providers.openai_transport.httpx.stream",
        lambda *a, **k: _FakeStreamCM(_FakeResponse(400, "streaming unsupported")),
    )
    final = {
        "choices": [{"message": {"content": "plain answer"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    }
    monkeypatch.setattr(
        "pulsar_agent.providers.openai_transport.httpx.post",
        lambda *a, **k: _FakeResponse(200, json.dumps(final)),
    )
    result = transport.complete("sys", [{"role": "user", "content": "q"}], [], 100,
                                on_text=lambda _t: None)
    assert result.text == "plain answer"


def test_openai_stream_auth_error_not_swallowed(home, monkeypatch):
    transport = ChatCompletionsTransport(_runtime(home))
    monkeypatch.setattr(
        "pulsar_agent.providers.openai_transport.httpx.stream",
        lambda *a, **k: _FakeStreamCM(_FakeResponse(401, "bad key")),
    )
    with pytest.raises(ProviderError) as excinfo:
        transport.complete("sys", [{"role": "user", "content": "q"}], [], 100,
                           on_text=lambda _t: None)
    assert excinfo.value.status_code == 401


def test_anthropic_stream_end_to_end(home, monkeypatch):
    transport = AnthropicTransport(_runtime(home, "anthropic:claude-test"))
    sse_lines = ["data: " + json.dumps(e) for e in _anthropic_events()]
    captured: dict = {}

    def fake_stream(method, url, **kwargs):
        captured["payload"] = kwargs.get("json")
        return _FakeStreamCM(_FakeResponse(200, lines=sse_lines))

    monkeypatch.setattr(
        "pulsar_agent.providers.anthropic_transport.httpx.stream", fake_stream
    )
    deltas: list[str] = []
    result = transport.complete("sys", [{"role": "user", "content": "q"}], [], 100,
                                on_text=deltas.append)
    assert captured["payload"]["stream"] is True
    assert result.text == "Hello world"
    assert deltas == ["Hello ", "world"]
    assert result.usage == {"input_tokens": 12, "output_tokens": 7}


# --- end-to-end through the mock provider and REPL sink ---


def test_repl_streams_mock_text(workspace, home, config):
    from pulsar_agent.cli.repl import Repl

    config["model"] = "mock:echo"
    config["streaming"] = True
    lines: list[str] = []
    repl = Repl(
        home=home,
        config=config,
        workspace=workspace,
        interactive=True,
        approver=lambda request: False,
        on_assistant_text=lines.append,
    )
    try:
        assert repl.stream_sink is not None
        repl.start_turn_clock()
        reply = repl.agent.run_turn("stream me")
        assert repl.stream_sink.streamed is True
        repl.stream_sink.flush()
        assert "".join(lines) == "echo: stream me" == reply
    finally:
        repl.close()


def test_streaming_disabled_by_config(workspace, home, config):
    from pulsar_agent.cli.repl import Repl

    config["model"] = "mock:echo"
    config["streaming"] = False
    repl = Repl(home=home, config=config, workspace=workspace, interactive=True,
                approver=lambda request: False)
    try:
        assert repl.stream_sink is None
        assert repl.agent.on_stream_text is None
    finally:
        repl.close()
