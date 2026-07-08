"""Provider registry and `provider:model` resolution.

Builtin families: anthropic (anthropic_messages), openai and openrouter
(chat_completions), local Ollama/LM Studio presets, and a deterministic
`mock` provider for tests. Custom endpoints come from `custom_providers`
in config.yaml; inline API keys there are rejected at config load.
"""

from __future__ import annotations

from dataclasses import dataclass

from pulsar_agent.providers.base import Transport
from pulsar_agent.secrets import SecretStore


class ProviderResolutionError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    api_mode: str
    base_url: str
    api_key_env_var: str = ""
    default_model: str = ""
    requires_key: bool = True


BUILTIN_PROFILES: dict[str, ProviderProfile] = {
    "anthropic": ProviderProfile(
        name="anthropic",
        api_mode="anthropic_messages",
        base_url="https://api.anthropic.com",
        api_key_env_var="ANTHROPIC_API_KEY",
        default_model="claude-sonnet-5",
    ),
    "openai": ProviderProfile(
        name="openai",
        api_mode="chat_completions",
        base_url="https://api.openai.com/v1",
        api_key_env_var="OPENAI_API_KEY",
        default_model="gpt-4.1",
    ),
    "openrouter": ProviderProfile(
        name="openrouter",
        api_mode="chat_completions",
        base_url="https://openrouter.ai/api/v1",
        api_key_env_var="OPENROUTER_API_KEY",
    ),
    "ollama": ProviderProfile(
        name="ollama",
        api_mode="chat_completions",
        base_url="http://localhost:11434/v1",
        requires_key=False,
    ),
    "lmstudio": ProviderProfile(
        name="lmstudio",
        api_mode="chat_completions",
        base_url="http://localhost:1234/v1",
        requires_key=False,
    ),
    "mock": ProviderProfile(
        name="mock",
        api_mode="mock",
        base_url="",
        requires_key=False,
        default_model="echo",
    ),
}


@dataclass(frozen=True)
class RuntimeProvider:
    profile: ProviderProfile
    model: str
    api_key: str | None

    @property
    def model_id(self) -> str:
        return f"{self.profile.name}:{self.model}"


def parse_model_id(model_id: str) -> tuple[str, str]:
    if not model_id or ":" not in model_id:
        raise ProviderResolutionError(
            f"model identifier must be 'provider:model', got {model_id!r}"
        )
    provider, _, model = model_id.partition(":")
    provider = provider.strip().lower()
    model = model.strip()
    if not provider or not model:
        raise ProviderResolutionError(
            f"model identifier must be 'provider:model', got {model_id!r}"
        )
    return provider, model


def _custom_profiles(config: dict) -> dict[str, ProviderProfile]:
    profiles: dict[str, ProviderProfile] = {}
    # Discovered PULSAR_HOME/providers/*.yaml profiles first, then
    # config.yaml custom_providers — config wins a name collision.
    entries = [
        *(config.get("provider_plugins") or []),
        *(config.get("custom_providers") or []),
    ]
    for entry in entries:
        name = str(entry["name"]).lower()
        profiles[name] = ProviderProfile(
            name=name,
            api_mode=entry["api_mode"],
            base_url=entry["base_url"].rstrip("/"),
            api_key_env_var=entry.get("api_key_env_var", ""),
            default_model=entry.get("default_model", ""),
            requires_key=bool(entry.get("requires_key", True)),
        )
    return profiles


def list_provider_names(config: dict) -> list[str]:
    names = set(BUILTIN_PROFILES) | set(_custom_profiles(config))
    return sorted(names)


def resolve_runtime_provider(
    model_id: str, config: dict, secrets: SecretStore
) -> RuntimeProvider:
    provider_name, model = parse_model_id(model_id)
    profiles = {**BUILTIN_PROFILES, **_custom_profiles(config)}
    profile = profiles.get(provider_name)
    if profile is None:
        raise ProviderResolutionError(
            f"unknown provider {provider_name!r}; known: {', '.join(sorted(profiles))}"
        )
    api_key: str | None = None
    if profile.api_key_env_var:
        api_key = secrets.get(profile.api_key_env_var)
    if profile.requires_key and not api_key:
        raise ProviderResolutionError(
            f"provider {profile.name!r} needs the {profile.api_key_env_var} secret; "
            f"add it to PULSAR_HOME/.env (run `pulsar setup`)"
        )
    if profile.base_url.startswith("http://") and "localhost" not in profile.base_url \
            and "127.0.0.1" not in profile.base_url:
        import warnings

        warnings.warn(
            f"provider {profile.name!r} uses plain HTTP on a non-local host",
            stacklevel=2,
        )
    return RuntimeProvider(profile=profile, model=model, api_key=api_key)


def create_transport(runtime: RuntimeProvider) -> Transport:
    mode = runtime.profile.api_mode
    if mode == "anthropic_messages":
        from pulsar_agent.providers.anthropic_transport import AnthropicTransport

        return AnthropicTransport(runtime)
    if mode in ("chat_completions", "custom_openai_compatible"):
        from pulsar_agent.providers.openai_transport import ChatCompletionsTransport

        return ChatCompletionsTransport(runtime)
    if mode == "mock":
        from pulsar_agent.providers.mock_transport import MockTransport

        return MockTransport(runtime)
    raise ProviderResolutionError(f"unsupported api_mode {mode!r}")
