from __future__ import annotations

import pytest

from pulsar_agent.security.paths import PathPolicy, PathSecurityError


@pytest.fixture
def policy(workspace, home):
    (home / "skills").mkdir(exist_ok=True)
    return PathPolicy(
        workspace=workspace,
        extra_read_roots=[home / "skills"],
        protected_roots=[home],
    )


def test_relative_path_resolves_inside_workspace(policy, workspace):
    (workspace / "a.txt").write_text("x")
    assert policy.resolve("a.txt") == workspace / "a.txt"


def test_escape_via_dotdot_blocked(policy):
    with pytest.raises(PathSecurityError, match="outside"):
        policy.resolve("../outside.txt")


def test_absolute_outside_blocked(policy, tmp_path):
    with pytest.raises(PathSecurityError):
        policy.resolve(str(tmp_path / "elsewhere.txt"))


def test_sensitive_files_blocked_even_inside_workspace(policy, workspace):
    for name in (".env", "auth.json", "secrets.enc", "id_rsa", "server.pem"):
        (workspace / name).write_text("secret")
        with pytest.raises(PathSecurityError, match="sensitive"):
            policy.resolve(name)


def test_git_credentials_blocked(policy, workspace):
    creds = workspace / ".git" / "credentials"
    creds.parent.mkdir()
    creds.write_text("token")
    with pytest.raises(PathSecurityError):
        policy.resolve(str(creds))


def test_pulsar_home_blocked_for_tools(policy, home):
    target = home / "config.yaml"
    target.write_text("model: x:y")
    with pytest.raises(PathSecurityError, match="state directory"):
        policy.resolve(str(target))


def test_skills_read_root_allowed_read_only(policy, home):
    skill = home / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: demo\n---\n")
    assert policy.resolve(str(skill), mode="read") == skill
    with pytest.raises(PathSecurityError):
        policy.resolve(str(skill), mode="write")


def test_env_blocked_in_pulsar_home(policy, home):
    (home / ".env").write_text("KEY=v")
    with pytest.raises(PathSecurityError):
        policy.resolve(str(home / ".env"), mode="read")
