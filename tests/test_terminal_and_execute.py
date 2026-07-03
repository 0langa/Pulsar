from __future__ import annotations

import sys

from pulsar_agent.security.redaction import Redactor
from pulsar_agent.tools import build_core_registry
from pulsar_agent.tools.terminal import scrubbed_environment
from tests.conftest import make_context


def test_scrubbed_environment_strips_secret_names(monkeypatch):
    monkeypatch.setenv("SOME_API_KEY", "secret1")
    monkeypatch.setenv("MY_TOKEN", "secret2")
    monkeypatch.setenv("DB_PASSWORD", "secret3")
    monkeypatch.setenv("HARMLESS_VAR", "fine")
    env = scrubbed_environment()
    assert "SOME_API_KEY" not in env
    assert "MY_TOKEN" not in env
    assert "DB_PASSWORD" not in env
    assert env.get("HARMLESS_VAR") == "fine"
    assert "PATH" in env


def test_scrubbed_environment_passthrough(monkeypatch):
    monkeypatch.setenv("NEEDED_TOKEN", "explicit")
    env = scrubbed_environment(["NEEDED_TOKEN"])
    assert env.get("NEEDED_TOKEN") == "explicit"


def test_terminal_runs_safe_command(context):
    registry = build_core_registry()
    out = registry.dispatch("terminal", {"command": "git --version"}, context)
    # `git --version` is not in the SAFE list, so trusted-local asks; use echo.
    out = registry.dispatch("terminal", {"command": "echo hello-pulsar"}, context)
    assert "hello-pulsar" in out


def test_terminal_hardline_blocked_trusted_local(context):
    registry = build_core_registry()
    out = registry.dispatch("terminal", {"command": "rm -rf /"}, context)
    assert "BLOCKED" in out and "hardline" in out


def test_terminal_destructive_denied_without_approver(workspace, home, config):
    context = make_context(workspace, home, config, preset="review", approver=None)
    registry = build_core_registry()
    out = registry.dispatch("terminal", {"command": "pip install requests"}, context)
    assert "BLOCKED" in out


def test_terminal_output_truncated(workspace, home, config):
    config["terminal"]["output_limit_bytes"] = 50
    context = make_context(workspace, home, config, approver=lambda request: True)
    registry = build_core_registry()
    command = f'"{sys.executable}" -c "print(\'x\' * 500)"'
    out = registry.dispatch("terminal", {"command": command}, context)
    assert "[output truncated]" in out


def test_terminal_output_redacted(workspace, home, config):
    secret = "sk-test-terminal-leak-abcdef123456"
    context = make_context(
        workspace, home, config,
        approver=lambda request: True,
        redactor=Redactor([secret]),
    )
    registry = build_core_registry()
    command = f'"{sys.executable}" -c "print(\'{secret}\')"'
    out = registry.dispatch("terminal", {"command": command}, context)
    assert secret not in out
    assert "[REDACTED]" in out


def test_execute_code_runs(context):
    registry = build_core_registry()
    out = registry.dispatch("execute_code", {"code": "print(2 + 40)"}, context)
    assert "42" in out


def test_execute_code_env_scrubbed(context, monkeypatch):
    monkeypatch.setenv("LEAKY_API_KEY", "should-not-appear-xyz")
    registry = build_core_registry()
    code = (
        "import os\n"
        "hits = [k for k in os.environ if 'LEAKY' in k]\n"
        "print('HITS:', hits)\n"
    )
    out = registry.dispatch("execute_code", {"code": code}, context)
    assert "HITS: []" in out
    assert "should-not-appear-xyz" not in out


def test_execute_code_hardline_pattern_blocked(context):
    registry = build_core_registry()
    out = registry.dispatch(
        "execute_code", {"code": "import os\nos.system('rm -rf /')\n"}, context
    )
    assert "BLOCKED" in out


def test_execute_code_disabled_for_subagents(workspace, home, config):
    sub_context = make_context(workspace, home, config, is_subagent=True)
    registry = build_core_registry()
    out = registry.dispatch("execute_code", {"code": "print(1)"}, sub_context)
    assert "unknown or disabled" in out
