"""PULSAR_HOME resolution and state-directory layout."""

from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "PULSAR_HOME"
DEFAULT_DIRNAME = ".pulsar"

SUBDIRS = ("memories", "skills", "checkpoints", "logs", "providers")


def get_pulsar_home() -> Path:
    """Return the Pulsar state root. PULSAR_HOME env var wins, else ~/.pulsar."""
    raw = os.environ.get(ENV_VAR, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / DEFAULT_DIRNAME).resolve()


def display_pulsar_home(home: Path | None = None) -> str:
    """Render the home path with ~ substitution for display."""
    home = home or get_pulsar_home()
    try:
        return "~" + os.sep + str(home.relative_to(Path.home()))
    except ValueError:
        return str(home)


def ensure_home_layout(home: Path | None = None) -> Path:
    """Create the PULSAR_HOME directory tree if missing. Returns the home path."""
    home = home or get_pulsar_home()
    home.mkdir(parents=True, exist_ok=True)
    for sub in SUBDIRS:
        (home / sub).mkdir(exist_ok=True)
    return home
