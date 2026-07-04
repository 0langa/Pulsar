"""Shadow-git checkpoints under PULSAR_HOME/checkpoints/.

Each workspace gets its own shadow repository (GIT_DIR outside the project,
work-tree pointing at the workspace), so the user's real .git history is
never touched. Secret-shaped files are excluded from snapshots so they never
enter the checkpoint store.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

EXCLUDES = (
    ".git/",
    ".env",
    "*.env",
    ".env.*",
    "auth.json",
    "secrets.enc",
    "*.pem",
    "id_rsa",
    "id_ed25519",
    "node_modules/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    "research/",
)


class CheckpointError(RuntimeError):
    pass


def git_available() -> bool:
    return shutil.which("git") is not None


class CheckpointStore:
    def __init__(self, home: Path, workspace: Path):
        self.workspace = workspace.resolve()
        # normcase, not lower(): it folds case only on case-insensitive
        # platforms (Windows), so distinct paths that differ only in case on
        # Linux/macOS get distinct shadow repos instead of colliding onto one
        # linear history (which would corrupt cross-workspace rollbacks).
        digest = hashlib.sha1(
            os.path.normcase(str(self.workspace)).encode(), usedforsecurity=False
        ).hexdigest()[:12]
        self.git_dir = home / "checkpoints" / digest
        self._initialized = False

    def available(self) -> bool:
        return git_available()

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = [
            "git",
            "--git-dir", str(self.git_dir),
            "--work-tree", str(self.workspace),
            "-c", "user.name=Pulsar",
            "-c", "user.email=pulsar@localhost",
            "-c", "core.autocrlf=false",
            "-c", "core.quotepath=false",  # emit raw UTF-8 paths, not \NNN escapes
            *args,
        ]
        completed = subprocess.run(
            cmd, cwd=str(self.workspace), capture_output=True, text=True,
            errors="replace", timeout=120,
        )
        if check and completed.returncode != 0:
            raise CheckpointError(
                f"git {' '.join(args[:2])} failed: {completed.stderr.strip()[:300]}"
            )
        return completed

    def _ensure_repo(self) -> None:
        if self._initialized:
            return
        if not git_available():
            raise CheckpointError("git executable not found; checkpoints unavailable")
        if not (self.git_dir / "HEAD").exists():
            self.git_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "init", "--quiet", "--bare", str(self.git_dir)],
                capture_output=True, text=True, timeout=60, check=True,
            )
            self._git("config", "core.bare", "false")
            exclude_file = self.git_dir / "info" / "exclude"
            exclude_file.parent.mkdir(exist_ok=True)
            exclude_file.write_text("\n".join(EXCLUDES) + "\n", encoding="utf-8")
        self._initialized = True

    def snapshot(self, label: str = "checkpoint") -> str | None:
        """Commit the current workspace state. Returns the commit hash, or
        None if nothing changed since the last snapshot."""
        self._ensure_repo()
        self._git("add", "-A")
        status = self._git("status", "--porcelain", check=False)
        has_head = self._git("rev-parse", "--verify", "HEAD", check=False).returncode == 0
        if has_head and not status.stdout.strip():
            return None
        safe_label = (label or "checkpoint").replace("\n", " ")[:120]
        self._git("commit", "--quiet", "--allow-empty", "-m", safe_label)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def list(self, limit: int = 10) -> list[dict]:
        self._ensure_repo()
        if self._git("rev-parse", "--verify", "HEAD", check=False).returncode != 0:
            return []
        out = self._git(
            "log", f"--max-count={limit}", "--pretty=format:%H%x09%ci%x09%s"
        ).stdout
        entries = []
        for line in out.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                entries.append({"hash": parts[0], "date": parts[1], "label": parts[2]})
        return entries

    def restore(self, ref: str = "HEAD") -> str:
        """Restore the workspace to a checkpoint. Current state is snapshotted
        first and history stays linear, so a rollback is itself reversible."""
        self._ensure_repo()
        target = self._git("rev-parse", "--verify", ref).stdout.strip()
        self.snapshot("pre-rollback snapshot")
        added = self._git(
            "diff", "--name-only", "--diff-filter=A", target, "HEAD"
        ).stdout.splitlines()
        for rel in added:
            path = self.workspace / rel
            if path.is_file():
                path.unlink()
        self._git("checkout", target, "--", ".")
        self.snapshot(f"rollback to {target[:12]}")
        return target
