"""File tools: read_file, write_file, patch, search_files.

All are workspace-scoped through PathPolicy. Editing requires a prior read
in the same session unless the file is new. Writes checkpoint first.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from pulsar_agent.security.approvals import (
    KIND_READ,
    KIND_WRITE,
    ApprovalRequest,
)
from pulsar_agent.security.command_risk import RiskTier
from pulsar_agent.tools.registry import ToolContext, ToolSpec

MAX_READ_LINES = 2000
MAX_LINE_CHARS = 2000
MAX_SEARCH_RESULTS = 100
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build", ".tox",
}


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def _checkpoint(context: ToolContext, label: str) -> None:
    store = context.checkpoints
    if store is not None:
        try:
            store.snapshot(label)  # type: ignore[attr-defined]
        except Exception as exc:
            context.emit("warn", f"checkpoint failed: {exc}")


def _mark_read(context: ToolContext, path: Path) -> None:
    context.read_files.add(os.path.normcase(str(path)))


def _was_read(context: ToolContext, path: Path) -> bool:
    return os.path.normcase(str(path)) in context.read_files


def read_file_handler(args: dict, context: ToolContext) -> str:
    path = context.path_policy.resolve(str(args.get("path", "")), mode="read")
    if not path.exists():
        return f"ERROR: file not found: {args.get('path')}"
    if path.is_dir():
        entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
        return "\n".join(entries[:500]) or "(empty directory)"
    context.approvals.check(
        ApprovalRequest(kind=KIND_READ, description=f"read {path}", risk=RiskTier.SAFE)
    )
    data = path.read_bytes()
    if _looks_binary(data):
        return f"ERROR: {path.name} looks binary ({len(data)} bytes); refusing to read"
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    offset = max(int(args.get("offset", 1) or 1), 1)
    limit = min(int(args.get("limit", MAX_READ_LINES) or MAX_READ_LINES), MAX_READ_LINES)
    window = lines[offset - 1 : offset - 1 + limit]
    numbered = [
        f"{offset + i:>6}\t{line[:MAX_LINE_CHARS]}" for i, line in enumerate(window)
    ]
    _mark_read(context, path)
    suffix = ""
    if offset - 1 + limit < len(lines):
        suffix = f"\n[{len(lines) - (offset - 1 + limit)} more lines; continue with offset={offset + limit}]"
    return "\n".join(numbered) + suffix if numbered else "(empty file)"


def write_file_handler(args: dict, context: ToolContext) -> str:
    raw_path = str(args.get("path", ""))
    content = str(args.get("content", ""))
    path = context.path_policy.resolve(raw_path, mode="write")
    exists = path.exists()
    if exists and not _was_read(context, path):
        return (
            f"ERROR: {raw_path} exists but has not been read this session; "
            "read it first, or use patch for edits"
        )
    context.approvals.check(
        ApprovalRequest(
            kind=KIND_WRITE,
            description=f"write {path} ({len(content)} chars)",
            detail="file write",
            cwd=str(context.workspace),
            will_checkpoint=context.checkpoints is not None,
        )
    )
    _checkpoint(context, f"before write_file {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _mark_read(context, path)
    return f"wrote {len(content)} chars to {raw_path}"


def patch_handler(args: dict, context: ToolContext) -> str:
    raw_path = str(args.get("path", ""))
    old_text = str(args.get("old_text", ""))
    new_text = str(args.get("new_text", ""))
    replace_all = bool(args.get("replace_all", False))
    if not old_text:
        return "ERROR: old_text must be non-empty; use write_file to create files"
    path = context.path_policy.resolve(raw_path, mode="write")
    if not path.exists():
        return f"ERROR: file not found: {raw_path}"
    if not _was_read(context, path):
        return f"ERROR: {raw_path} has not been read this session; read it first"
    text = path.read_text(encoding="utf-8")
    count = text.count(old_text)
    if count == 0:
        stripped = "\n".join(line.rstrip() for line in old_text.splitlines())
        normalized = "\n".join(line.rstrip() for line in text.splitlines())
        if stripped and normalized.count(stripped) == 1:
            start = normalized.index(stripped)
            line_start = normalized.count("\n", 0, start)
            original_lines = text.splitlines(keepends=True)
            n_lines = len(stripped.splitlines())
            prefix = "".join(original_lines[:line_start])
            middle = "".join(original_lines[line_start : line_start + n_lines])
            suffix = "".join(original_lines[line_start + n_lines :])
            if middle.endswith("\n") and not new_text.endswith("\n"):
                new_text += "\n"
            new_content = prefix + new_text + suffix
            context.approvals.check(
                ApprovalRequest(
                    kind=KIND_WRITE, description=f"patch {path}", detail="file patch",
                    cwd=str(context.workspace),
                    will_checkpoint=context.checkpoints is not None,
                )
            )
            _checkpoint(context, f"before patch {path.name}")
            path.write_text(new_content, encoding="utf-8")
            return f"patched {raw_path} (fuzzy whitespace match, 1 replacement)"
        return f"ERROR: old_text not found in {raw_path}"
    if count > 1 and not replace_all:
        return (
            f"ERROR: old_text matches {count} times in {raw_path}; "
            "add surrounding context or set replace_all=true"
        )
    context.approvals.check(
        ApprovalRequest(
            kind=KIND_WRITE, description=f"patch {path}", detail="file patch",
            cwd=str(context.workspace),
            will_checkpoint=context.checkpoints is not None,
        )
    )
    _checkpoint(context, f"before patch {path.name}")
    replaced = count if replace_all else 1
    path.write_text(
        text.replace(old_text, new_text) if replace_all
        else text.replace(old_text, new_text, 1),
        encoding="utf-8",
    )
    return f"patched {raw_path} ({replaced} replacement{'s' if replaced != 1 else ''})"


def search_files_handler(args: dict, context: ToolContext) -> str:
    pattern = str(args.get("pattern", ""))
    glob = str(args.get("glob", "") or "*")
    max_results = min(int(args.get("max_results", 50) or 50), MAX_SEARCH_RESULTS)
    if not pattern:
        return "ERROR: pattern is required"
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"ERROR: invalid regex: {exc}"
    root = context.path_policy.workspace
    results: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            if not fnmatch.fnmatch(filename, glob):
                continue
            file_path = Path(dirpath) / filename
            try:
                context.path_policy.resolve(str(file_path), mode="read")
            except PermissionError:
                continue
            try:
                data = file_path.read_bytes()
            except OSError:
                continue
            if _looks_binary(data):
                continue
            rel = file_path.relative_to(root)
            for line_no, line in enumerate(
                data.decode("utf-8", errors="replace").splitlines(), start=1
            ):
                if regex.search(line):
                    results.append(f"{rel}:{line_no}: {line.strip()[:300]}")
                    if len(results) >= max_results:
                        return "\n".join(results) + "\n[result limit reached]"
    return "\n".join(results) if results else "no matches"


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="read_file",
            description=(
                "Read a text file with line numbers. Supports offset/limit "
                "pagination. Directories list their entries."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path (workspace-relative or absolute)"},
                    "offset": {"type": "integer", "description": "1-based first line to read"},
                    "limit": {"type": "integer", "description": "Max lines to return"},
                },
                "required": ["path"],
            },
            handler=read_file_handler,
        ),
        ToolSpec(
            name="write_file",
            description=(
                "Create or overwrite a text file. Overwriting requires reading "
                "the file first in this session. Prefer patch for edits."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            handler=write_file_handler,
        ),
        ToolSpec(
            name="patch",
            description=(
                "Replace an exact text block in a file. old_text must match "
                "uniquely unless replace_all is set. Read the file first."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "old_text", "new_text"],
            },
            handler=patch_handler,
        ),
        ToolSpec(
            name="search_files",
            description=(
                "Regex search across workspace text files. Optional filename "
                "glob filter and max_results limit."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python regex"},
                    "glob": {"type": "string", "description": "Filename glob, e.g. *.py"},
                    "max_results": {"type": "integer"},
                },
                "required": ["pattern"],
            },
            handler=search_files_handler,
        ),
    ]
