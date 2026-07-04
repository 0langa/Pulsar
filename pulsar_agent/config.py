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
        "backend": "local",  # "local" or "docker"
        "timeout_seconds": 120,
        "output_limit_bytes": 20000,
        "env_mode": "allowlist",  # "allowlist" (safe default) or "scrub"
        "env_passthrough": [],
    },
    "docker": {
        "image": "python:3.11-slim",
        "network": "none",  # docker --network mode; "none" by default
        "workspace_mount": "rw",  # "rw" or "ro"
        "timeout_seconds": 120,
        "output_limit_bytes": 20000,
        "env_allowlist": [],  # variable names passed into the container
        "memory": "512m",
        "cpus": "1.0",
        "pids_limit": 256,
        "read_only_rootfs": False,
    },
    "security": {
        "redact_secrets": True,
        "command_allowlist": [],
        "autonomy": {
            "allow_writes": False,
            "allow_execute_code": False,
            "allow_memory_writes": False,
            "allow_mcp": False,
        },
    },
    "mcp": {
        "servers": [],  # see validate_config for the per-server schema
    },
    "web": {
        "enabled": True,
        "search_backend": "duckduckgo",  # no-key HTML backend
        "search_results_api_env_var": "",  # optional user-supplied provider key
        "max_bytes": 400000,
        "text_limit": 12000,
        "timeout_seconds": 20,
        "allow_private_urls": False,  # SSRF guard; opt-in only
        "user_agent": "Pulsar/0.2 (+https://github.com/0langa/Pulsar)",
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
CUSTOM_PROVIDER_ALLOWED = (*CUSTOM_PROVIDER_REQUIRED, "default_model", "requires_key")
VALID_API_MODES = ("anthropic_messages", "chat_completions", "custom_openai_compatible")

VALID_BACKENDS = ("local", "docker")
VALID_ENV_MODES = ("allowlist", "scrub")
VALID_WEB_BACKENDS = ("duckduckgo", "brave")

MCP_SERVER_REQUIRED = ("name", "command")
MCP_SERVER_ALLOWED = (
    *MCP_SERVER_REQUIRED,
    "args", "cwd", "enabled", "allowed_tools", "env_passthrough", "startup_timeout",
)
# Secret-shaped keys that must never appear inline in any config section.
INLINE_SECRET_KEYS = ("api_key", "apikey", "token", "secret", "password", "credential")


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

    _validate_terminal_and_docker(config)
    _validate_mcp(config)
    _validate_web(config)


def _validate_web(config: dict) -> None:
    web = config.get("web", {}) or {}
    backend = web.get("search_backend", "duckduckgo")
    if backend not in VALID_WEB_BACKENDS:
        raise ConfigError(
            f"web.search_backend must be one of {VALID_WEB_BACKENDS}, got {backend!r}"
        )
    for banned in INLINE_SECRET_KEYS:
        if banned in {k.lower() for k in web}:
            raise ConfigError(
                "web config must not hold inline secrets; use "
                "search_results_api_env_var and PULSAR_HOME/.env"
            )


def _validate_terminal_and_docker(config: dict) -> None:
    terminal = config.get("terminal", {}) or {}
    backend = terminal.get("backend", "local")
    if backend not in VALID_BACKENDS:
        raise ConfigError(
            f"terminal.backend must be one of {VALID_BACKENDS}, got {backend!r}"
        )
    env_mode = terminal.get("env_mode", "allowlist")
    if env_mode not in VALID_ENV_MODES:
        raise ConfigError(
            f"terminal.env_mode must be one of {VALID_ENV_MODES}, got {env_mode!r}"
        )
    docker = config.get("docker", {}) or {}
    mount = docker.get("workspace_mount", "rw")
    if mount not in ("rw", "ro"):
        raise ConfigError(f"docker.workspace_mount must be 'rw' or 'ro', got {mount!r}")
    for banned in INLINE_SECRET_KEYS:
        if banned in {k.lower() for k in docker.get("env_allowlist", []) if isinstance(k, str)}:
            raise ConfigError(
                "docker.env_allowlist holds variable *names* to pass through, "
                "not secret values; put secrets in PULSAR_HOME/.env"
            )


def _validate_mcp(config: dict) -> None:
    mcp = config.get("mcp", {}) or {}
    servers = mcp.get("servers") or []
    if not isinstance(servers, list):
        raise ConfigError("mcp.servers must be a list")
    seen: set[str] = set()
    for entry in servers:
        if not isinstance(entry, dict):
            raise ConfigError("mcp.servers entries must be mappings")
        for banned in INLINE_SECRET_KEYS:
            if banned in {k.lower() for k in entry}:
                raise ConfigError(
                    f"mcp server {entry.get('name', '?')!r} has inline secret field "
                    f"{banned!r}; use env_passthrough names and PULSAR_HOME/.env"
                )
        missing = [k for k in MCP_SERVER_REQUIRED if not entry.get(k)]
        if missing:
            raise ConfigError(
                f"mcp server {entry.get('name', '?')!r} missing fields: {missing}"
            )
        name = str(entry["name"])
        if name in seen:
            raise ConfigError(f"duplicate mcp server name {name!r}")
        seen.add(name)
        unknown = [k for k in entry if k not in MCP_SERVER_ALLOWED]
        if unknown:
            raise ConfigError(
                f"mcp server {name!r} has unknown fields: {unknown}"
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
