from __future__ import annotations

import yaml

from pulsar_agent.config import (
    load_config,
    load_provider_plugins,
    save_config,
)
from pulsar_agent.home import ensure_home_layout
from pulsar_agent.providers.router import (
    list_provider_names,
    resolve_runtime_provider,
)
from pulsar_agent.secrets import SecretStore


def write_plugin(home, filename: str, entry: dict) -> None:
    root = home / "providers"
    root.mkdir(exist_ok=True)
    (root / filename).write_text(yaml.safe_dump(entry), encoding="utf-8")


def plugin_entry(**overrides) -> dict:
    entry = {
        "name": "myprovider",
        "api_mode": "chat_completions",
        "base_url": "http://localhost:9999/v1",
        "api_key_env_var": "MYPROVIDER_KEY",
        "requires_key": False,
    }
    entry.update(overrides)
    return entry


def test_home_layout_creates_providers_dir(tmp_path):
    home = ensure_home_layout(tmp_path / "h")
    assert (home / "providers").is_dir()


def test_plugin_discovered_and_resolvable(home):
    write_plugin(home, "myprovider.yaml", plugin_entry())
    config = load_config(home)
    assert "myprovider" in list_provider_names(config)
    runtime = resolve_runtime_provider(
        "myprovider:some-model", config, SecretStore(home)
    )
    assert runtime.profile.base_url == "http://localhost:9999/v1"
    assert runtime.model == "some-model"


def test_plugin_inline_secret_skipped_with_warning(home):
    write_plugin(
        home, "leaky.yaml", plugin_entry(name="leaky", api_key="sk-inline-oops")
    )
    entries, warnings = load_provider_plugins(home)
    assert entries == []
    assert warnings and "inline" in warnings[0]
    # And startup surfaces it through config_warnings.
    from pulsar_agent.config import config_warnings

    config = load_config(home)
    assert any("inline" in w for w in config_warnings(config))


def test_malformed_plugin_skipped_not_fatal(home):
    root = home / "providers"
    root.mkdir(exist_ok=True)
    (root / "broken.yaml").write_text("{{{not yaml", encoding="utf-8")
    write_plugin(home, "good.yaml", plugin_entry(name="good"))
    config = load_config(home)  # must not raise
    assert "good" in list_provider_names(config)
    assert any("broken.yaml" in w for w in config.get("_plugin_warnings", []))


def test_config_custom_provider_wins_name_collision(home):
    write_plugin(
        home, "dup.yaml", plugin_entry(name="dup", base_url="http://localhost:1111/v1")
    )
    import yaml as _yaml

    (home / "config.yaml").write_text(
        _yaml.safe_dump(
            {
                "custom_providers": [
                    plugin_entry(name="dup", base_url="http://localhost:2222/v1")
                ]
            }
        ),
        encoding="utf-8",
    )
    config = load_config(home)
    runtime = resolve_runtime_provider("dup:m", config, SecretStore(home))
    assert runtime.profile.base_url == "http://localhost:2222/v1"


def test_save_config_never_persists_plugins(home):
    write_plugin(home, "myprovider.yaml", plugin_entry())
    config = load_config(home)
    assert config["provider_plugins"]
    save_config(home, config)
    on_disk = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert "provider_plugins" not in on_disk
    assert "_plugin_warnings" not in on_disk
    # Reload still discovers the plugin from its own file.
    assert "myprovider" in list_provider_names(load_config(home))
