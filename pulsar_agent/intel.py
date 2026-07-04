"""Repo intelligence: project map, test discovery, git awareness.

Read-only heuristics over the workspace. The rendered map is injected into
the system prompt (bounded) and shown via the `/map` slash command so the
agent and the user share the same picture of the project.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "dist", "build", ".tox",
    ".idea", ".vscode", "target", ".next", ".nuxt", "coverage", ".eggs",
}

LANGUAGE_EXTENSIONS = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
    ".jsx": "JavaScript", ".rs": "Rust", ".go": "Go", ".java": "Java",
    ".kt": "Kotlin", ".cs": "C#", ".rb": "Ruby", ".php": "PHP", ".c": "C",
    ".h": "C/C++", ".cpp": "C++", ".hpp": "C++", ".swift": "Swift",
    ".sh": "Shell", ".ps1": "PowerShell", ".sql": "SQL", ".html": "HTML",
    ".css": "CSS", ".scss": "CSS", ".vue": "Vue", ".svelte": "Svelte",
}

# marker file -> (framework/tooling hint, package manager or None)
MARKER_FILES = {
    "pyproject.toml": ("Python project (pyproject)", "pip"),
    "setup.py": ("Python project (setup.py)", "pip"),
    "requirements.txt": ("Python requirements", "pip"),
    "Pipfile": ("Python (pipenv)", "pipenv"),
    "poetry.lock": ("Python (poetry)", "poetry"),
    "uv.lock": ("Python (uv)", "uv"),
    "package.json": ("Node.js project", "npm"),
    "pnpm-lock.yaml": ("Node.js (pnpm)", "pnpm"),
    "yarn.lock": ("Node.js (yarn)", "yarn"),
    "Cargo.toml": ("Rust (cargo)", "cargo"),
    "go.mod": ("Go module", "go"),
    "pom.xml": ("Java (Maven)", "maven"),
    "build.gradle": ("Java/Kotlin (Gradle)", "gradle"),
    "build.gradle.kts": ("Kotlin (Gradle)", "gradle"),
    "Gemfile": ("Ruby (bundler)", "bundler"),
    "composer.json": ("PHP (composer)", "composer"),
    "Dockerfile": ("Docker", None),
    "docker-compose.yml": ("Docker Compose", None),
    "docker-compose.yaml": ("Docker Compose", None),
    "Makefile": ("Makefile", None),
    ".pre-commit-config.yaml": ("pre-commit", None),
}

IMPORTANT_FILES = (
    "README.md", "README.rst", "LICENSE", "CONTRIBUTING.md", "SECURITY.md",
    "CHANGELOG.md", "AGENTS.md", "CLAUDE.md", "pyproject.toml", "package.json",
    "Cargo.toml", "go.mod", "Makefile", "Dockerfile",
)

CI_DIRS = (".github/workflows", ".gitlab-ci.yml", ".circleci")

MAX_SCAN_FILES = 5000
MAP_RENDER_LIMIT = 2500


@dataclass
class ProjectMap:
    languages: list[tuple[str, int]] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    important_files: list[str] = field(default_factory=list)
    test_commands: list[str] = field(default_factory=list)
    ci: list[str] = field(default_factory=list)
    file_count: int = 0
    truncated_scan: bool = False


def build_project_map(workspace: Path) -> ProjectMap:
    workspace = Path(workspace)
    counts: Counter[str] = Counter()
    frameworks: list[str] = []
    managers: list[str] = []
    scanned = 0
    truncated = False

    stack = [workspace]
    visited: set[str] = set()
    while stack:
        directory = stack.pop()
        # Guard against symlink cycles (e.g. x/y -> x): skip any real path we
        # have already descended, so a file-free loop can't hang startup.
        try:
            real = str(directory.resolve())
        except OSError:
            continue
        if real in visited:
            continue
        visited.add(real)
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for entry in entries:
            if scanned >= MAX_SCAN_FILES:
                truncated = True
                stack.clear()
                break
            if entry.is_dir():
                if (
                    entry.name not in SKIP_DIRS
                    and not entry.name.startswith(".")
                    and not entry.is_symlink()
                ):
                    stack.append(entry)
                continue
            scanned += 1
            language = LANGUAGE_EXTENSIONS.get(entry.suffix.lower())
            if language:
                counts[language] += 1
            if entry.parent == workspace and entry.name in MARKER_FILES:
                hint, manager = MARKER_FILES[entry.name]
                if hint not in frameworks:
                    frameworks.append(hint)
                if manager and manager not in managers:
                    managers.append(manager)

    important = [name for name in IMPORTANT_FILES if (workspace / name).is_file()]
    ci = []
    if (workspace / ".github" / "workflows").is_dir():
        ci.append("GitHub Actions")
    if (workspace / ".gitlab-ci.yml").is_file():
        ci.append("GitLab CI")
    if (workspace / ".circleci").is_dir():
        ci.append("CircleCI")

    return ProjectMap(
        languages=counts.most_common(6),
        frameworks=frameworks,
        package_managers=managers,
        important_files=important,
        test_commands=infer_test_commands(workspace),
        ci=ci,
        file_count=scanned,
        truncated_scan=truncated,
    )


def infer_test_commands(workspace: Path) -> list[str]:
    """Best-guess test commands, most likely first."""
    workspace = Path(workspace)
    commands: list[str] = []

    pyproject = workspace / "pyproject.toml"
    has_pytest = False
    if pyproject.is_file():
        try:
            content = pyproject.read_text(encoding="utf-8", errors="replace")
            has_pytest = "pytest" in content
        except OSError:
            pass
    if has_pytest or (workspace / "tests").is_dir() or (workspace / "test").is_dir():
        if (workspace / "manage.py").is_file():
            commands.append("python manage.py test")
        commands.append("python -m pytest")

    package_json = workspace / "package.json"
    if package_json.is_file():
        try:
            import json

            scripts = (json.loads(package_json.read_text(encoding="utf-8")) or {}).get(
                "scripts", {}
            )
            if "test" in scripts:
                commands.append("npm test")
        except (OSError, ValueError):
            pass

    if (workspace / "Cargo.toml").is_file():
        commands.append("cargo test")
    if (workspace / "go.mod").is_file():
        commands.append("go test ./...")
    if (workspace / "pom.xml").is_file():
        commands.append("mvn test")
    if (workspace / "build.gradle").is_file() or (workspace / "build.gradle.kts").is_file():
        commands.append("gradle test")
    return commands


def targeted_test_command(workspace: Path, target: str) -> str | None:
    """Command to run one test file/node, or None if no runner is inferable."""
    commands = infer_test_commands(Path(workspace))
    target = target.strip()
    if not target:
        return commands[0] if commands else None
    for command in commands:
        if "pytest" in command:
            return f"python -m pytest {target}"
        if command == "go test ./...":
            return f"go test {target}"
        if command == "cargo test":
            return f"cargo test {target}"
        if command == "npm test":
            return f"npm test -- {target}"
    return None


def _git(workspace: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def git_summary(workspace: Path, max_changed: int = 10) -> str:
    """Branch, change counts, changed files, and recent commits — or a note
    that the workspace is not a git repository."""
    workspace = Path(workspace)
    branch = _git(workspace, "rev-parse", "--abbrev-ref", "HEAD")
    if branch is None:
        return "not a git repository (or git unavailable)"
    status = _git(workspace, "status", "--short") or ""
    changed = [line for line in status.splitlines() if line.strip()]
    lines = [f"branch {branch}, {len(changed)} changed file(s)"]
    for entry in changed[:max_changed]:
        lines.append(f"  {entry}")
    if len(changed) > max_changed:
        lines.append(f"  … and {len(changed) - max_changed} more")
    log = _git(workspace, "log", "--oneline", "-3")
    if log:
        lines.append("recent commits:")
        for entry in log.splitlines():
            lines.append(f"  {entry}")
    return "\n".join(lines)


def git_diff_stat(workspace: Path) -> str:
    """Bounded diffstat of uncommitted changes for prompt context."""
    stat = _git(Path(workspace), "diff", "--stat", "--stat-count=15")
    if not stat:
        return ""
    if len(stat) > 1500:
        stat = stat[:1500] + "\n[diffstat truncated]"
    return stat


def render_project_map(project_map: ProjectMap, git_text: str = "") -> str:
    lines: list[str] = []
    if project_map.languages:
        lines.append(
            "Languages: "
            + ", ".join(f"{name} ({count})" for name, count in project_map.languages)
        )
    if project_map.frameworks:
        lines.append("Tooling: " + ", ".join(project_map.frameworks))
    if project_map.package_managers:
        lines.append("Package managers: " + ", ".join(project_map.package_managers))
    if project_map.ci:
        lines.append("CI: " + ", ".join(project_map.ci))
    if project_map.important_files:
        lines.append("Key files: " + ", ".join(project_map.important_files))
    if project_map.test_commands:
        lines.append("Likely test commands: " + "; ".join(project_map.test_commands))
    scan = f"Files scanned: {project_map.file_count}"
    if project_map.truncated_scan:
        scan += " (scan capped; large repo)"
    lines.append(scan)
    if git_text:
        lines.append("Git: " + git_text.replace("\n", "\n  "))
    text = "\n".join(lines)
    if len(text) > MAP_RENDER_LIMIT:
        text = text[:MAP_RENDER_LIMIT] + "\n[project map truncated]"
    return text
