from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pulsar_agent.intel import (
    build_project_map,
    git_diff_stat,
    git_summary,
    infer_test_commands,
    render_project_map,
    targeted_test_command,
)
from tests.conftest import requires_git


def make_python_repo(root: Path) -> Path:
    repo = root / "pyrepo"
    (repo / "tests").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "pyproject.toml").write_text(
        "[project]\nname='x'\n[tool.pytest.ini_options]\n", encoding="utf-8"
    )
    (repo / "README.md").write_text("# x", encoding="utf-8")
    (repo / "Dockerfile").write_text("FROM python:3.11", encoding="utf-8")
    (repo / "src" / "app.py").write_text("print('hi')", encoding="utf-8")
    (repo / "src" / "util.py").write_text("x = 1", encoding="utf-8")
    (repo / "tests" / "test_app.py").write_text("def test_a(): pass", encoding="utf-8")
    return repo


def make_node_repo(root: Path) -> Path:
    repo = root / "noderepo"
    repo.mkdir(parents=True)
    (repo / "package.json").write_text(
        json.dumps({"name": "y", "scripts": {"test": "jest"}}), encoding="utf-8"
    )
    (repo / "index.js").write_text("console.log(1)", encoding="utf-8")
    (repo / "app.ts").write_text("const a = 1", encoding="utf-8")
    return repo


def test_project_map_python_repo(tmp_path):
    repo = make_python_repo(tmp_path)
    project_map = build_project_map(repo)
    languages = dict(project_map.languages)
    assert languages.get("Python") == 3
    assert "Python project (pyproject)" in project_map.frameworks
    assert "Docker" in project_map.frameworks
    assert "pip" in project_map.package_managers
    assert "README.md" in project_map.important_files
    assert "python -m pytest" in project_map.test_commands
    assert project_map.file_count >= 5


def test_project_map_node_repo(tmp_path):
    repo = make_node_repo(tmp_path)
    project_map = build_project_map(repo)
    languages = dict(project_map.languages)
    assert "JavaScript" in languages and "TypeScript" in languages
    assert "Node.js project" in project_map.frameworks
    assert "npm" in project_map.package_managers
    assert project_map.test_commands == ["npm test"]


def test_project_map_skips_noise_dirs(tmp_path):
    repo = make_python_repo(tmp_path)
    noise = repo / "node_modules" / "dep"
    noise.mkdir(parents=True)
    for i in range(50):
        (noise / f"mod{i}.js").write_text("x", encoding="utf-8")
    project_map = build_project_map(repo)
    assert "JavaScript" not in dict(project_map.languages)


def test_project_map_survives_symlink_cycle(tmp_path):
    # Regression: a file-free symlink cycle (x/y -> x) must not hang the scan.
    repo = make_python_repo(tmp_path)
    target = repo / "cycle"
    target.mkdir()
    try:
        (target / "loop").symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        import pytest

        pytest.skip("symlinks not supported on this platform/privilege level")
    project_map = build_project_map(repo)  # must return, not hang
    assert dict(project_map.languages).get("Python") == 3


def test_infer_test_commands_empty_repo(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert infer_test_commands(empty) == []


def test_targeted_test_command(tmp_path):
    repo = make_python_repo(tmp_path)
    command = targeted_test_command(repo, "tests/test_app.py::test_a")
    assert command == "python -m pytest tests/test_app.py::test_a"
    node = make_node_repo(tmp_path)
    assert targeted_test_command(node, "src/app.test.js") == "npm test -- src/app.test.js"
    empty = tmp_path / "none"
    empty.mkdir()
    assert targeted_test_command(empty, "whatever") is None


def test_render_project_map_bounded(tmp_path):
    repo = make_python_repo(tmp_path)
    text = render_project_map(build_project_map(repo), "branch main, 0 changed file(s)")
    assert "Languages:" in text
    assert "Likely test commands:" in text
    assert "Git: branch main" in text
    assert len(text) < 4000


def test_git_summary_non_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert "not a git repository" in git_summary(plain)


@requires_git
def test_git_summary_and_diffstat(tmp_path):
    repo = make_python_repo(tmp_path)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=repo, check=True,
    )
    (repo / "src" / "app.py").write_text("print('changed')", encoding="utf-8")
    summary = git_summary(repo)
    assert "branch main" in summary
    assert "1 changed file(s)" in summary
    assert "init" in summary
    stat = git_diff_stat(repo)
    assert "app.py" in stat
