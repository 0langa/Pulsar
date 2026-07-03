"""Local skill discovery: builtin package skills + PULSAR_HOME/skills.

A skill is a directory containing SKILL.md with YAML frontmatter (name,
description, optional version). Skill content is untrusted data — it is
listed in the prompt, and the agent reads the full SKILL.md via read_file
only when a skill matches the task.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

SKILL_FILENAME = "SKILL.md"


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    version: str
    path: Path
    source: str  # "builtin" or "user"


def builtin_skills_dir() -> Path:
    return Path(__file__).resolve().parent / "builtin"


def _parse_frontmatter(text: str) -> dict:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = next(
        (i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if end is None:
        return {}
    try:
        loaded = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _load_skill(directory: Path, source: str) -> SkillInfo | None:
    skill_file = directory / SKILL_FILENAME
    if not skill_file.is_file():
        return None
    try:
        front = _parse_frontmatter(skill_file.read_text(encoding="utf-8"))
    except OSError:
        return None
    name = str(front.get("name") or directory.name)
    description = str(front.get("description") or "")[:200]
    version = str(front.get("version") or "")
    return SkillInfo(
        name=name,
        description=description,
        version=version,
        path=skill_file,
        source=source,
    )


def discover_skills(home: Path) -> list[SkillInfo]:
    skills: dict[str, SkillInfo] = {}
    for source, root in (("builtin", builtin_skills_dir()), ("user", home / "skills")):
        if not root.is_dir():
            continue
        for directory in sorted(p for p in root.iterdir() if p.is_dir()):
            info = _load_skill(directory, source)
            if info is not None:
                skills[info.name] = info  # user skills override builtin on name clash
    return sorted(skills.values(), key=lambda s: s.name)
