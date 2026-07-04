from __future__ import annotations

import os
from pathlib import Path

import pytest

from pulsar_agent.config import DEFAULT_CONFIG, deep_merge
from pulsar_agent.home import ensure_home_layout
from pulsar_agent.security.approvals import ApprovalManager
from pulsar_agent.security.paths import PathPolicy
from pulsar_agent.security.redaction import Redactor
from pulsar_agent.tools.registry import ToolContext

SECRET_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "PULSAR_MOCK_SCRIPT",
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch, tmp_path):
    for name in SECRET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PULSAR_HOME", str(tmp_path / "pulsar_home"))
    yield


@pytest.fixture
def home(tmp_path) -> Path:
    return ensure_home_layout(tmp_path / "pulsar_home")


@pytest.fixture
def workspace(tmp_path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def config() -> dict:
    return deep_merge(DEFAULT_CONFIG, {})


FULL_AUTONOMY = {
    "allow_writes": True,
    "allow_execute_code": True,
    "allow_memory_writes": True,
}


def make_context(
    workspace: Path,
    home: Path,
    config: dict,
    preset: str = "trusted-local",
    approver=None,
    checkpoints=None,
    redactor: Redactor | None = None,
    is_subagent: bool = False,
    autonomy: dict | None = None,
) -> ToolContext:
    # Default: trusted-local with full autonomy grants so tool-functionality
    # tests exercise handlers without an interactive approver. Approval-policy
    # tests construct ApprovalManager directly instead of using this helper.
    return ToolContext(
        workspace=workspace,
        home=home,
        config=config,
        path_policy=PathPolicy(
            workspace=workspace,
            extra_read_roots=[home / "skills"],
            protected_roots=[home],
        ),
        approvals=ApprovalManager(
            preset=preset,
            approver=approver,
            autonomy=FULL_AUTONOMY if autonomy is None else autonomy,
        ),
        redactor=redactor or Redactor(),
        checkpoints=checkpoints,
        is_subagent=is_subagent,
    )


@pytest.fixture
def context(workspace, home, config) -> ToolContext:
    return make_context(workspace, home, config)


def git_available() -> bool:
    import shutil

    return shutil.which("git") is not None


requires_git = pytest.mark.skipif(not git_available(), reason="git not on PATH")
