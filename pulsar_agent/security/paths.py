"""Workspace path scoping and sensitive-file blocking for file tools."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

SENSITIVE_BASENAMES = {
    ".env",
    "auth.json",
    "secrets.enc",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
}

SENSITIVE_GLOBS = (".env.*", "*.pem", "*.key", "*.p12", "*.pfx")

SENSITIVE_PATH_FRAGMENTS = (
    os.sep + os.path.join(".git", "credentials"),
    "/.git/credentials",
)


class PathSecurityError(PermissionError):
    pass


def _norm(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def is_sensitive_path(path: Path) -> bool:
    name = path.name.lower()
    if name in SENSITIVE_BASENAMES:
        return True
    for pattern in SENSITIVE_GLOBS:
        if fnmatch.fnmatch(name, pattern):
            return True
    normalized = _norm(path)
    return any(
        os.path.normcase(fragment.replace("/", os.sep)) in normalized
        for fragment in SENSITIVE_PATH_FRAGMENTS
    )


class PathPolicy:
    """Confines file tools to the workspace plus explicit read-only roots."""

    def __init__(
        self,
        workspace: Path,
        extra_read_roots: list[Path] | None = None,
        protected_roots: list[Path] | None = None,
    ):
        self.workspace = workspace.resolve()
        self.extra_read_roots = [p.resolve() for p in (extra_read_roots or [])]
        self.protected_roots = [p.resolve() for p in (protected_roots or [])]

    def _within(self, path: Path, root: Path) -> bool:
        return _norm(path) == _norm(root) or _norm(path).startswith(
            _norm(root) + os.sep
        )

    def resolve(self, raw: str, mode: str = "read") -> Path:
        """Validate and resolve a tool-supplied path. mode: 'read' or 'write'."""
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        resolved = candidate.resolve()

        if is_sensitive_path(resolved):
            raise PathSecurityError(
                f"access to sensitive file {resolved.name!r} is blocked"
            )
        if mode == "read":
            for root in self.extra_read_roots:
                if self._within(resolved, root):
                    return resolved
        for protected in self.protected_roots:
            if self._within(resolved, protected):
                raise PathSecurityError(
                    "access to the Pulsar state directory is blocked for file tools"
                )
        if self._within(resolved, self.workspace):
            return resolved
        raise PathSecurityError(
            f"path {raw!r} is outside the workspace {str(self.workspace)!r}"
        )
