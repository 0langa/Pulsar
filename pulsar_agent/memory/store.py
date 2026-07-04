"""Bounded Markdown memory: MEMORY.md (project/agent facts), USER.md (prefs).

Loaded once per session as a frozen snapshot. Writes are size-bounded,
secret-scanned, and injection-scanned; with write_approval enabled they are
staged for user review instead of applied directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pulsar_agent.security.redaction import Redactor

MEMORY_FILE = "MEMORY.md"
USER_FILE = "USER.md"

INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard your instructions",
    "disregard all instructions",
    "you are now",
    "new system prompt",
    "override the system",
    "disable safety",
    "disable approvals",
)


class MemoryWriteRejected(ValueError):
    pass


@dataclass
class StagedWrite:
    target: str
    new_content: str
    reason: str


class MemoryStore:
    def __init__(self, home: Path, config: dict, redactor: Redactor):
        self.dir = home / "memories"
        self.dir.mkdir(parents=True, exist_ok=True)
        memory_cfg = config.get("memory", {})
        self.bounds = {
            MEMORY_FILE: int(memory_cfg.get("max_memory_chars", 4000)),
            USER_FILE: int(memory_cfg.get("max_user_chars", 2000)),
        }
        self.write_approval = bool(memory_cfg.get("write_approval", True))
        self.redactor = redactor
        self.staged: list[StagedWrite] = []

    def _path(self, target: str) -> Path:
        if target not in (MEMORY_FILE, USER_FILE):
            raise MemoryWriteRejected(f"unknown memory file {target!r}")
        return self.dir / target

    def read(self, target: str) -> str:
        path = self._path(target)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def snapshot(self) -> str:
        """Frozen bounded snapshot for the system prompt."""
        parts = []
        for target, label in ((MEMORY_FILE, "Project memory"), (USER_FILE, "User preferences")):
            content = self.read(target).strip()
            if content:
                bound = self.bounds[target]
                if len(content) > bound:
                    content = content[:bound] + "\n[memory truncated at bound]"
                parts.append(f"## {label} ({target})\n{content}")
        return "\n\n".join(parts)

    def _scan(self, content: str) -> None:
        lowered = content.lower()
        for marker in INJECTION_MARKERS:
            if marker in lowered:
                raise MemoryWriteRejected(
                    f"memory write rejected: prompt-injection marker {marker!r}"
                )
        if self.redactor.redact(content) != content:
            raise MemoryWriteRejected("memory write rejected: contains secret-like content")

    def _current(self, target: str) -> str:
        """Base content to compose onto: the most recent staged write for this
        target if one is pending, otherwise the on-disk file. Without this,
        multiple staged writes in one turn each build on the file and the last
        approve clobbers the earlier ones."""
        for staged in reversed(self.staged):
            if staged.target == target:
                return staged.new_content
        return self.read(target)

    def _compose(self, target: str, action: str, content: str, old: str) -> str:
        current = self._current(target)
        if action == "add":
            entry = content.strip()
            if entry and entry in current:
                raise MemoryWriteRejected("memory write rejected: duplicate entry")
            new = (current.rstrip() + "\n" if current.strip() else "") + f"- {entry}\n"
        elif action == "replace":
            if not old or old not in current:
                raise MemoryWriteRejected(f"replace target not found in {target}")
            new = current.replace(old, content, 1)
        elif action == "remove":
            if not old or old not in current:
                raise MemoryWriteRejected(f"remove target not found in {target}")
            new = current.replace(old, "", 1)
        else:
            raise MemoryWriteRejected(f"unknown memory action {action!r}")
        bound = self.bounds[target]
        if len(new) > bound:
            raise MemoryWriteRejected(
                f"{target} would exceed its {bound}-char bound; consolidate entries first"
            )
        return new

    def apply(
        self, target: str, action: str, content: str = "", old: str = ""
    ) -> str:
        """Validate and either stage (write_approval) or write immediately."""
        self._path(target)
        if action != "remove":
            self._scan(content)
        new_content = self._compose(target, action, content, old)
        if self.write_approval:
            self.staged.append(
                StagedWrite(target=target, new_content=new_content, reason=action)
            )
            return f"staged {action} for {target}; approve with /memory approve"
        self._path(target).write_text(new_content, encoding="utf-8")
        return f"applied {action} to {target}"

    def approve_staged(self) -> str:
        if not self.staged:
            return "no staged memory writes"
        applied = 0
        for staged in self.staged:
            self._path(staged.target).write_text(staged.new_content, encoding="utf-8")
            applied += 1
        self.staged.clear()
        return f"applied {applied} staged memory write(s)"

    def discard_staged(self) -> str:
        count = len(self.staged)
        self.staged.clear()
        return f"discarded {count} staged memory write(s)"
