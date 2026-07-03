from __future__ import annotations

import pytest

from pulsar_agent.config import DEFAULT_CONFIG, deep_merge
from pulsar_agent.providers.mock_transport import MockTransport
from pulsar_agent.providers.router import (
    ProviderResolutionError,
    create_transport,
    list_provider_names,
    parse_model_id,
    resolve_runtime_provider,
)
from pulsar_agent.secrets import SecretStore

FAKE_ANTHROPIC = "sk-ant-test000000000000000000000"
FAKE_OPENAI = "sk-test111111111111111111111111"


@pytest.fixture
def secrets(home):
    (home / ".env").write_text(
        f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC}\n"
        f"OPENAI_API_KEY={FAKE_OPENAI}\n"
        "MY_LOCAL_KEY=local-key-123456\n",
        encoding="utf-8",
    )
    return SecretStore(home)


def test_parse_model_id():
    assert parse_model_id("anthropic:claude-sonnet-5") == ("anthropic", "claude-sonnet-5")
    assert parse_model_id("openrouter:anthropic/claude-3.7-sonnet") == (
        "openrouter",
        "anthropic/claude-3.7-sonnet",
    )


@pytest.mark.parametrize("bad", ["", "no-colon", ":model", "provider:", ":"])
def test_parse_model_id_rejects(bad):
    with pytest.raises(ProviderResolutionError):
        parse_model_id(bad)


def test_resolve_anthropic(config, secrets):
    runtime = resolve_runtime_provider("anthropic:claude-sonnet-5", config, secrets)
    assert runtime.profile.api_mode == "anthropic_messages"
    assert runtime.api_key == FAKE_ANTHROPIC
    assert runtime.model_id == "anthropic:claude-sonnet-5"


def test_resolve_openai(config, secrets):
    runtime = resolve_runtime_provider("openai:gpt-4.1", config, secrets)
    assert runtime.profile.api_mode == "chat_completions"
    assert runtime.api_key == FAKE_OPENAI


def test_resolve_local_no_key_needed(config, secrets):
    runtime = resolve_runtime_provider("ollama:llama3", config, secrets)
    assert runtime.profile.api_mode == "chat_completions"
    assert runtime.profile.requires_key is False
    assert "localhost" in runtime.profile.base_url


def test_resolve_custom_provider(secrets):
    config = deep_merge(
        DEFAULT_CONFIG,
        {
            "custom_providers": [
                {
                    "name": "myserver",
                    "api_mode": "custom_openai_compatible",
                    "base_url": "http://localhost:8080/v1",
                    "api_key_env_var": "MY_LOCAL_KEY",
                }
            ]
        },
    )
    runtime = resolve_runtime_provider("myserver:custom-model", config, secrets)
    assert runtime.profile.api_mode == "custom_openai_compatible"
    assert runtime.api_key == "local-key-123456"
    assert "myserver" in list_provider_names(config)


def test_missing_key_raises(config, home):
    empty_secrets = SecretStore(home)
    with pytest.raises(ProviderResolutionError, match="ANTHROPIC_API_KEY"):
        resolve_runtime_provider("anthropic:claude-sonnet-5", config, empty_secrets)


def test_unknown_provider_raises(config, secrets):
    with pytest.raises(ProviderResolutionError, match="unknown provider"):
        resolve_runtime_provider("nope:model", config, secrets)


def test_create_transport_modes(config, secrets):
    from pulsar_agent.providers.anthropic_transport import AnthropicTransport
    from pulsar_agent.providers.openai_transport import ChatCompletionsTransport

    anthropic = create_transport(
        resolve_runtime_provider("anthropic:claude-sonnet-5", config, secrets)
    )
    openai = create_transport(resolve_runtime_provider("openai:gpt-4.1", config, secrets))
    mock = create_transport(resolve_runtime_provider("mock:echo", config, secrets))
    assert isinstance(anthropic, AnthropicTransport)
    assert isinstance(openai, ChatCompletionsTransport)
    assert isinstance(mock, MockTransport)


def test_mock_transport_echo(config, secrets):
    transport = create_transport(resolve_runtime_provider("mock:echo", config, secrets))
    result = transport.complete("sys", [{"role": "user", "content": "hi"}], [], 100)
    assert result.text == "echo: hi"


def test_mock_transport_scripted(config, secrets):
    transport = MockTransport(
        resolve_runtime_provider("mock:echo", config, secrets),
        script=[
            {"tool_calls": [{"name": "read_file", "arguments": {"path": "x"}}]},
            {"text": "done"},
        ],
    )
    first = transport.complete("s", [], [], 10)
    assert first.tool_calls[0].name == "read_file"
    second = transport.complete("s", [], [], 10)
    assert second.text == "done"


def test_message_conversion_anthropic():
    from pulsar_agent.providers.anthropic_transport import _to_anthropic_messages

    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "checking",
            "tool_calls": [{"id": "t1", "name": "read_file", "arguments": {"path": "a"}}],
        },
        {"role": "tool", "tool_call_id": "t1", "name": "read_file", "content": "data"},
    ]
    converted = _to_anthropic_messages(messages)
    assert converted[0] == {"role": "user", "content": "hi"}
    assert converted[1]["content"][1]["type"] == "tool_use"
    assert converted[2]["content"][0]["type"] == "tool_result"


def test_message_conversion_openai():
    from pulsar_agent.providers.openai_transport import _to_openai_messages

    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "t1", "name": "search_files", "arguments": {"pattern": "x"}}],
        },
        {"role": "tool", "tool_call_id": "t1", "name": "search_files", "content": "found"},
    ]
    converted = _to_openai_messages("sys", messages)
    assert converted[0]["role"] == "system"
    assert converted[2]["tool_calls"][0]["function"]["name"] == "search_files"
    assert converted[3]["role"] == "tool"
