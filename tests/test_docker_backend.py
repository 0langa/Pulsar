from __future__ import annotations

import shutil
import subprocess

import pytest

from pulsar_agent.security.redaction import Redactor
from pulsar_agent.tools import build_core_registry, docker_backend
from pulsar_agent.tools.cancellable import RunOutcome
from pulsar_agent.tools.docker_backend import (
    DOCKER_UNAVAILABLE_GUIDANCE,
    build_docker_command,
    build_docker_env,
    run_in_docker,
    run_python_in_docker,
)
from tests.conftest import make_context


def docker_config(config: dict, **overrides) -> dict:
    config["terminal"]["backend"] = "docker"
    config["docker"].update(overrides)
    return config


# --- command construction (no daemon needed) ---


def test_docker_command_hardened_defaults(workspace, config):
    argv = build_docker_command(["sh", "-c", "echo hi"], str(workspace), config)
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--privileged" not in argv
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"
    assert argv[argv.index("--pids-limit") + 1] == "256"
    assert argv[argv.index("--memory") + 1] == "512m"
    assert argv[argv.index("-w") + 1] == "/workspace"
    assert argv[argv.index("-v") + 1] == f"{workspace}:/workspace:rw"
    assert argv[-4:] == ["python:3.11-slim", "sh", "-c", "echo hi"]


def test_docker_command_readonly_mount_and_rootfs(workspace, config):
    config["docker"]["workspace_mount"] = "ro"
    config["docker"]["read_only_rootfs"] = True
    argv = build_docker_command(["sh", "-c", "ls"], str(workspace), config)
    assert argv[argv.index("-v") + 1].endswith(":/workspace:ro")
    assert "--read-only" in argv
    assert "--tmpfs" in argv


def test_docker_env_allowlist_names_only(workspace, config, monkeypatch):
    monkeypatch.setenv("ALLOWED_VAR", "value-visible")
    monkeypatch.setenv("SNEAKY_API_KEY", "should-not-pass")
    config["docker"]["env_allowlist"] = ["ALLOWED_VAR"]
    argv = build_docker_command(["sh", "-c", "env"], str(workspace), config)
    # Forwarded by name only; the value never appears in the argv.
    assert "-e" in argv
    assert argv[argv.index("-e") + 1] == "ALLOWED_VAR"
    assert "value-visible" not in " ".join(argv)
    assert "SNEAKY_API_KEY" not in argv

    env = build_docker_env(config)
    assert env.get("ALLOWED_VAR") == "value-visible"
    assert "SNEAKY_API_KEY" not in env


def test_docker_env_absent_allowlisted_var_not_forwarded(workspace, config, monkeypatch):
    monkeypatch.delenv("NOT_SET_VAR", raising=False)
    config["docker"]["env_allowlist"] = ["NOT_SET_VAR"]
    argv = build_docker_command(["sh", "-c", "env"], str(workspace), config)
    assert "NOT_SET_VAR" not in argv


# --- failure modes ---


def test_docker_unavailable_graceful(workspace, config, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    code, output = run_in_docker("echo hi", str(workspace), config)
    assert code == 127
    assert DOCKER_UNAVAILABLE_GUIDANCE in output


def test_docker_timeout_kills_container(workspace, config, monkeypatch):
    monkeypatch.setattr(docker_backend, "docker_available", lambda: True)
    killed: list[list[str]] = []

    monkeypatch.setattr(
        "pulsar_agent.tools.cancellable.run_cancellable",
        lambda *a, **k: RunOutcome(-1, "", "", timed_out=True),
    )

    def fake_run(argv, **kwargs):
        if argv[:2] == ["docker", "kill"]:
            killed.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    code, output = run_in_docker("sleep 999", str(workspace), config)
    assert code == 124
    assert "timed out" in output
    assert killed and killed[0][2].startswith("pulsar-")


def test_docker_cancel_kills_container(workspace, config, monkeypatch):
    monkeypatch.setattr(docker_backend, "docker_available", lambda: True)
    killed: list[list[str]] = []

    monkeypatch.setattr(
        "pulsar_agent.tools.cancellable.run_cancellable",
        lambda *a, **k: RunOutcome(-1, "", "", cancelled=True),
    )

    def fake_run(argv, **kwargs):
        if argv[:2] == ["docker", "kill"]:
            killed.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    code, output = run_in_docker(
        "sleep 999", str(workspace), config, should_cancel=lambda: True
    )
    assert code == 130
    assert "cancelled" in output
    assert killed and killed[0][2].startswith("pulsar-")


def test_docker_daemon_down_guidance(workspace, config, monkeypatch):
    monkeypatch.setattr(docker_backend, "docker_available", lambda: True)
    monkeypatch.setattr(
        "pulsar_agent.tools.cancellable.run_cancellable",
        lambda *a, **k: RunOutcome(
            1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"
        ),
    )
    code, output = run_in_docker("echo hi", str(workspace), config)
    assert code == 127
    assert DOCKER_UNAVAILABLE_GUIDANCE in output


# --- backend selection through the real tool handlers ---


def test_terminal_backend_selection_docker(workspace, home, config, monkeypatch):
    docker_config(config)
    calls: list[str] = []

    def fake_run_in_docker(command, ws, cfg, should_cancel=None):
        calls.append(command)
        return 0, "docker-ran"

    monkeypatch.setattr(docker_backend, "run_in_docker", fake_run_in_docker)
    context = make_context(workspace, home, config, approver=lambda request: True)
    out = build_core_registry().dispatch("terminal", {"command": "echo via-docker"}, context)
    assert calls == ["echo via-docker"]
    assert "docker-ran" in out


def test_execute_code_backend_selection_docker(workspace, home, config, monkeypatch):
    docker_config(config)
    calls: list[str] = []

    def fake_run_python(code, ws, cfg, timeout, should_cancel=None):
        calls.append(code)
        return "[ok]\npython-in-docker"

    monkeypatch.setattr(docker_backend, "run_python_in_docker", fake_run_python)
    context = make_context(workspace, home, config)
    out = build_core_registry().dispatch("execute_code", {"code": "print(1)"}, context)
    assert calls == ["print(1)"]
    assert "python-in-docker" in out


def test_terminal_local_backend_still_default(workspace, home, config):
    assert config["terminal"]["backend"] == "local"
    context = make_context(workspace, home, config)
    out = build_core_registry().dispatch("terminal", {"command": "echo local-run"}, context)
    assert "local-run" in out


def test_docker_hardline_still_blocked(workspace, home, config):
    docker_config(config)
    context = make_context(workspace, home, config, approver=lambda request: True)
    out = build_core_registry().dispatch("terminal", {"command": "rm -rf /"}, context)
    assert "BLOCKED" in out and "hardline" in out


def test_docker_output_redacted(workspace, home, config, monkeypatch):
    docker_config(config)
    secret = "sk-docker-leak-abcdef1234567890"
    monkeypatch.setattr(
        docker_backend, "run_in_docker",
        lambda c, w, cfg, should_cancel=None: (0, f"token: {secret}"),
    )
    context = make_context(
        workspace, home, config,
        approver=lambda request: True,
        redactor=Redactor([secret]),
    )
    out = build_core_registry().dispatch("terminal", {"command": "echo x"}, context)
    assert secret not in out
    assert "[REDACTED]" in out


def test_docker_python_output_truncated(workspace, config, monkeypatch):
    monkeypatch.setattr(docker_backend, "docker_available", lambda: True)
    config["docker"]["output_limit_bytes"] = 50

    monkeypatch.setattr(
        "pulsar_agent.tools.cancellable.run_cancellable",
        lambda *a, **k: RunOutcome(0, "y" * 500, ""),
    )
    out = run_python_in_docker("print('y'*500)", str(workspace), config, 30)
    assert "[output truncated]" in out


# --- best-effort integration (skips cleanly without a working daemon) ---


def _docker_usable() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        probe = subprocess.run(
            ["docker", "info", "--format", "{{.OSType}}"],
            capture_output=True, timeout=15, text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    # The integration image is Linux-only; a Windows-containers daemon (e.g.
    # a GitHub windows runner) passes `docker info` but cannot run it.
    return probe.returncode == 0 and probe.stdout.strip() == "linux"


@pytest.mark.skipif(not _docker_usable(), reason="docker daemon not available")
def test_docker_integration_echo(workspace, config):
    config["docker"]["image"] = "python:3.11-slim"
    code, output = run_in_docker("echo pulsar-in-container", str(workspace), config)
    assert code == 0
    assert "pulsar-in-container" in output
