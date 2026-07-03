"""Behavioral configuration: config.yaml load/merge/validate.

Secrets never live here; they belong in PULSAR_HOME/.env (see secrets.py).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILENAME = "config.yaml"

APPROVAL_PRESETS = ("paranoid", "review", "trusted-local")

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "model": "anthropic:claude-sonnet-5",
    "approval_preset": "review",
    "max_iterations": 40,
    "max_tokens": 8192,
    "fallback_models": [],
    "custom_providers": [],
    "terminal": {
        "timeout_seconds": 120,
        "output_limit_bytes": 20000,
        "env_passthrough": [],
    },
    "security": {
        "redact_secrets": True,
        "command_allowlist": [],
    },
    "memory": {
        "max_memory_chars": 4000,
        "max_user_chars": 2000,
        "write_approval": True,
    },
    "checkpoints": {
        "enabled": True,
    },
    "delegate": {
        "enabled": True,
        "max_iterations": 8,
    },
}

CUSTOM_PROVIDER_REQUIRED = ("name", "api_mode", "base_url", "api_key_env_var")
CUSTOM_PROVIDER_ALLOWED = CUSTOM_PROVIDER_REQUIRED + ("default_model", "requires_key")
VALID_API_MODES = ("anthropic_messages", "chat_completions", "custom_openai_compatible")


class ConfigError(ValueError):
    pass


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def validate_config(config: dict) -> None:
    preset = config.get("approval_preset")
    if preset not in APPROVAL_PRESETS:
        raise ConfigError(
            f"approval_preset must be one of {APPROVAL_PRESETS}, got {preset!r}"
        )
    providers = config.get("custom_providers") or []
    if not isinstance(providers, list):
        raise ConfigError("custom_providers must be a list")
    for entry in providers:
        if not isinstance(entry, dict):
            raise ConfigError("custom_providers entries must be mappings")
        for banned in ("api_key", "apikey", "token", "secret"):
            if banned in {k.lower() for k in entry}:
                raise ConfigError(
                    f"custom provider {entry.get('name', '?')!r} contains an inline "
                    f"secret field {banned!r}; secrets belong in PULSAR_HOME/.env "
                    "and config must reference api_key_env_var instead"
                )
        missing = [k for k in CUSTOM_PROVIDER_REQUIRED if not entry.get(k)]
        if missing:
            raise ConfigError(
                f"custom provider {entry.get('name', '?')!r} missing fields: {missing}"
            )
        if entry["api_mode"] not in VALID_API_MODES:
            raise ConfigError(
                f"custom provider {entry['name']!r} has invalid api_mode "
                f"{entry['api_mode']!r}; valid: {VALID_API_MODES}"
            )
        unknown = [k for k in entry if k not in CUSTOM_PROVIDER_ALLOWED]
        if unknown:
            raise ConfigError(
                f"custom provider {entry['name']!r} has unknown fields: {unknown}"
            )


def load_config(home: Path) -> dict:
    path = home / CONFIG_FILENAME
    user_cfg: dict = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if loaded is not None and not isinstance(loaded, dict):
            raise ConfigError(f"{path} must contain a YAML mapping")
        user_cfg = loaded or {}
    config = deep_merge(DEFAULT_CONFIG, user_cfg)
    validate_config(config)
    return config


def save_config(home: Path, config: dict) -> Path:
    validate_config(config)
    path = home / CONFIG_FILENAME
    path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path
