"""Secret loading from PULSAR_HOME/.env.

Secrets are kept in a private dict, never exported into os.environ, never
written to config, logs, sessions, or chat. The redactor registers every
loaded value so accidental echoes are masked.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

ENV_FILENAME = ".env"


def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            values[key] = value
    return values


class SecretStore:
    def __init__(self, home: Path):
        self._home = home
        self._path = home / ENV_FILENAME
        self._values: dict[str, str] = {}
        if self._path.exists():
            self._values = parse_env_text(self._path.read_text(encoding="utf-8"))

    @property
    def path(self) -> Path:
        return self._path

    def get(self, name: str) -> str | None:
        """Resolve a secret by env-var name: .env first, then process env."""
        if name in self._values:
            return self._values[name]
        return os.environ.get(name)

    def names(self) -> list[str]:
        return sorted(self._values)

    def all_values(self) -> list[str]:
        """Secret values for redactor registration. Never log or display."""
        return [v for v in self._values.values() if v]

    def set(self, name: str, value: str) -> None:
        if "\n" in value or "\r" in value:
            # A newline in the value would corrupt the line-based .env parser
            # (later keys silently vanish). Reject rather than write garbage.
            raise ValueError("secret value must not contain newlines")
        self._values[name] = value
        lines: list[str] = []
        replaced = False
        if self._path.exists():
            for raw_line in self._path.read_text(encoding="utf-8").splitlines():
                key = raw_line.split("=", 1)[0].strip().removeprefix("export ").strip()
                if key == name:
                    lines.append(f"{name}={value}")
                    replaced = True
                else:
                    lines.append(raw_line)
        if not replaced:
            lines.append(f"{name}={value}")
        self._path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        restrict_file_permissions(self._path)


def restrict_file_permissions(path: Path) -> None:
    """Best-effort chmod 600. On Windows this only clears group/other bits."""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
