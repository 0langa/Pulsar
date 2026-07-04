from __future__ import annotations

from pathlib import Path

import pytest

from pulsar_agent.config import (
    DEFAULT_CONFIG,
    ConfigError,
    deep_merge,
    load_config,
    save_config,
    validate_config,
)
from pulsar_agent.home import display_pulsar_home, ensure_home_layout, get_pulsar_home


def test_pulsar_home_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PULSAR_HOME", str(tmp_path / "custom"))
    assert get_pulsar_home() == (tmp_path / "custom").resolve()


def test_pulsar_home_default(monkeypatch):
    monkeypatch.delenv("PULSAR_HOME", raising=False)
    assert get_pulsar_home() == (Path.home() / ".pulsar").resolve()


def test_display_home_uses_tilde(monkeypatch):
    monkeypatch.delenv("PULSAR_HOME", raising=False)
    assert display_pulsar_home().startswith("~")


def test_ensure_home_layout(tmp_path):
    home = ensure_home_layout(tmp_path / "h")
    for sub in ("memories", "skills", "checkpoints", "logs"):
        assert (home / sub).is_dir()


def test_load_config_defaults(home):
    config = load_config(home)
    assert config["model"] == DEFAULT_CONFIG["model"]
    assert config["approval_preset"] == "review"


def test_config_merge_preserves_nested(home):
    save_config(home, deep_merge(DEFAULT_CONFIG, {"terminal": {"timeout_seconds": 5}}))
    config = load_config(home)
    assert config["terminal"]["timeout_seconds"] == 5
    assert config["terminal"]["output_limit_bytes"] == 20000


def test_custom_provider_inline_api_key_rejected():
    config = deep_merge(
        DEFAULT_CONFIG,
        {
            "custom_providers": [
                {
                    "name": "local",
                    "api_mode": "chat_completions",
                    "base_url": "http://localhost:8000/v1",
                    "api_key_env_var": "LOCAL_KEY",
                    "api_key": "sk-inline-secret-value",
                }
            ]
        },
    )
    with pytest.raises(ConfigError, match="inline"):
        validate_config(config)


def test_custom_provider_requires_fields():
    config = deep_merge(
        DEFAULT_CONFIG,
        {"custom_providers": [{"name": "x", "api_mode": "chat_completions"}]},
    )
    with pytest.raises(ConfigError, match="missing"):
        validate_config(config)


def test_invalid_preset_rejected():
    config = deep_merge(DEFAULT_CONFIG, {"approval_preset": "yolo"})
    with pytest.raises(ConfigError):
        validate_config(config)
