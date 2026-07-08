from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pulsar_agent.config import (
    CONFIG_FILENAME,
    CONFIG_VERSION,
    DEFAULT_CONFIG,
    ConfigError,
    config_warnings,
    deep_merge,
    load_config,
    migrate_config,
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


def test_docker_network_rejects_custom_network():
    config = deep_merge(DEFAULT_CONFIG, {"docker": {"network": "my-custom-net"}})
    with pytest.raises(ConfigError, match=r"docker\.network"):
        validate_config(config)


def test_docker_network_builtins_accepted():
    for network in ("none", "bridge", "host"):
        config = deep_merge(DEFAULT_CONFIG, {"docker": {"network": network}})
        validate_config(config)


def test_docker_image_must_be_nonempty_string():
    for bad in ("", "   ", 42, None):
        config = deep_merge(DEFAULT_CONFIG, {"docker": {"image": bad}})
        with pytest.raises(ConfigError, match=r"docker\.image"):
            validate_config(config)


def test_migrate_v1_file_rewritten_and_user_keys_kept(home):
    path = home / CONFIG_FILENAME
    path.write_text(
        yaml.safe_dump({"version": 1, "model": "mock:echo",
                        "terminal": {"timeout_seconds": 7}}),
        encoding="utf-8",
    )
    config = load_config(home)
    assert config["version"] == CONFIG_VERSION
    assert config["model"] == "mock:echo"
    assert config["terminal"]["timeout_seconds"] == 7
    on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert on_disk["version"] == CONFIG_VERSION
    assert on_disk["model"] == "mock:echo"
    # Only the user's keys are persisted, not the merged defaults.
    assert "web" not in on_disk


def test_missing_version_treated_as_v1():
    migrated_cfg, migrated = migrate_config({"model": "mock:echo"})
    assert migrated is True
    assert migrated_cfg["version"] == CONFIG_VERSION


def test_current_version_is_noop():
    migrated_cfg, migrated = migrate_config({"version": CONFIG_VERSION})
    assert migrated is False
    assert migrated_cfg["version"] == CONFIG_VERSION


def test_future_version_rejected(home):
    path = home / CONFIG_FILENAME
    path.write_text(yaml.safe_dump({"version": CONFIG_VERSION + 7}), encoding="utf-8")
    with pytest.raises(ConfigError, match=r"newer|understands"):
        load_config(home)


def test_garbage_version_rejected():
    with pytest.raises(ConfigError, match="version"):
        migrate_config({"version": "banana"})


def test_host_network_warns_only_with_docker_backend():
    config = deep_merge(
        DEFAULT_CONFIG,
        {"terminal": {"backend": "docker"}, "docker": {"network": "host"}},
    )
    warnings = config_warnings(config)
    assert len(warnings) == 1
    assert "host" in warnings[0]

    # Same network setting is silent while the local backend is active.
    config = deep_merge(DEFAULT_CONFIG, {"docker": {"network": "host"}})
    assert config_warnings(config) == []

    # Default config produces no advisory noise.
    assert config_warnings(deep_merge(DEFAULT_CONFIG, {})) == []
