"""`pulsar setup`: interactive first-run configuration.

Writes behavior to config.yaml and secrets (via getpass, never echoed) to
PULSAR_HOME/.env with restricted permissions.
"""

from __future__ import annotations

import getpass
from pathlib import Path

from pulsar_agent.config import (
    APPROVAL_PRESETS,
    load_config,
    save_config,
)
from pulsar_agent.home import display_pulsar_home, ensure_home_layout
from pulsar_agent.providers.router import BUILTIN_PROFILES, parse_model_id
from pulsar_agent.secrets import SecretStore


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def run_setup(home: Path) -> int:
    ensure_home_layout(home)
    config = load_config(home)
    secrets = SecretStore(home)

    print(f"Pulsar setup — state directory: {display_pulsar_home(home)}")
    print("Secrets go to PULSAR_HOME/.env only; config.yaml stays secret-free.\n")

    provider_names = [
        name for name in sorted(BUILTIN_PROFILES) if name != "mock"
    ]
    provider = _ask(
        f"Provider ({'/'.join(provider_names)} or custom name)",
        default="anthropic",
    ).lower()

    profile = BUILTIN_PROFILES.get(provider)
    if profile is None:
        print(f"\n{provider!r} is not builtin; add it under custom_providers in "
              f"{home / 'config.yaml'} with name/api_mode/base_url/api_key_env_var.")
        default_model = _ask("Model name for this provider", default="default")
        config["model"] = f"{provider}:{default_model}"
    else:
        model = _ask("Model", default=profile.default_model or "")
        if not model:
            print("A model name is required.")
            return 1
        parse_model_id(f"{provider}:{model}")
        config["model"] = f"{provider}:{model}"
        if profile.requires_key:
            existing = secrets.get(profile.api_key_env_var)
            if existing:
                print(f"{profile.api_key_env_var} already present in .env; keeping it.")
            else:
                key = getpass.getpass(f"{profile.api_key_env_var} (input hidden): ").strip()
                if key:
                    secrets.set(profile.api_key_env_var, key)
                    print(f"Stored {profile.api_key_env_var} in {secrets.path}")
                else:
                    print("No key entered; you can add it to .env later.")

    preset = _ask(
        f"Approval preset ({'/'.join(APPROVAL_PRESETS)})",
        default=config.get("approval_preset", "review"),
    )
    if preset not in APPROVAL_PRESETS:
        print(f"Unknown preset {preset!r}; keeping {config.get('approval_preset')!r}.")
    else:
        config["approval_preset"] = preset

    save_config(home, config)
    print(f"\nSaved {home / 'config.yaml'}. Run `pulsar` to start.")
    return 0
