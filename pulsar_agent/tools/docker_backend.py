"""Docker execution backend for terminal and execute_code.

Opt-in isolation layer (`terminal.backend: docker`). Containers run with
hardened defaults: no privileged mode, `--cap-drop ALL`,
`--security-opt no-new-privileges`, network disabled unless configured,
memory/cpu/pids limits, and only the workspace mounted. Environment is
allowlist-only: variable *names* from `docker.env_allowlist` are forwarded
with `-e NAME` (values never appear in the docker argv) and the docker CLI
itself runs under the same allowlist-first environment used for local
subprocesses.

The docker CLI must be on PATH and the daemon running; otherwise commands
fail gracefully with guidance instead of a stack trace.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid

from pulsar_agent.tools.terminal import allowlist_environment

CONTAINER_WORKDIR = "/workspace"

DOCKER_UNAVAILABLE_GUIDANCE = (
    "docker backend selected but Docker is not usable. "
    "Install Docker Desktop (or the docker engine), make sure the daemon is "
    "running and `docker` is on PATH, or switch back with "
    "`terminal.backend: local` in config.yaml."
)


def docker_available() -> bool:
    return shutil.which("docker") is not None


def _docker_cfg(config: dict) -> dict:
    return config.get("docker", {}) or {}


def build_docker_env(config: dict) -> dict[str, str]:
    """Environment for the docker CLI process itself: fixed baseline plus the
    configured allowlist. Secrets with undeclared names never reach docker."""
    allowlist = [
        name for name in _docker_cfg(config).get("env_allowlist") or []
        if isinstance(name, str)
    ]
    return allowlist_environment(allowlist)


def build_docker_command(
    inner_argv: list[str],
    workspace: str,
    config: dict,
    interactive: bool = False,
) -> list[str]:
    """Construct the `docker run` argv. Pure function so tests can assert the
    exact flags without a docker daemon."""
    cfg = _docker_cfg(config)
    mount_mode = "ro" if str(cfg.get("workspace_mount", "rw")) == "ro" else "rw"
    args = [
        "docker", "run", "--rm",
        "--name", f"pulsar-{uuid.uuid4().hex[:12]}",
        "--network", str(cfg.get("network", "none")),
        "--memory", str(cfg.get("memory", "512m")),
        "--cpus", str(cfg.get("cpus", "1.0")),
        "--pids-limit", str(cfg.get("pids_limit", 256)),
        "--security-opt", "no-new-privileges",
        "--cap-drop", "ALL",
        "-v", f"{workspace}:{CONTAINER_WORKDIR}:{mount_mode}",
        "-w", CONTAINER_WORKDIR,
    ]
    if cfg.get("read_only_rootfs", False):
        args += ["--read-only", "--tmpfs", "/tmp"]
    if interactive:
        args.append("-i")
    # `-e NAME` (no value) forwards from the docker CLI environment, keeping
    # secret values out of the process argv / `ps` output.
    for name in cfg.get("env_allowlist") or []:
        if isinstance(name, str) and name in os.environ:
            args += ["-e", name]
    args.append(str(cfg.get("image", "python:3.11-slim")))
    args.extend(inner_argv)
    return args


def _container_name(argv: list[str]) -> str:
    try:
        return argv[argv.index("--name") + 1]
    except (ValueError, IndexError):
        return ""


def _kill_container(name: str, env: dict[str, str]) -> None:
    """Best-effort kill after a client-side timeout so the container does not
    keep running detached."""
    if not name:
        return
    try:
        subprocess.run(
            ["docker", "kill", name],
            env=env, capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _run(
    argv: list[str],
    config: dict,
    timeout: int,
    stdin_text: str | None = None,
) -> tuple[int, str]:
    if not docker_available():
        return 127, f"ERROR: {DOCKER_UNAVAILABLE_GUIDANCE}"
    env = build_docker_env(config)
    try:
        completed = subprocess.run(
            argv,
            input=stdin_text,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _kill_container(_container_name(argv), env)
        return 124, f"[command timed out after {timeout}s in docker]"
    except FileNotFoundError:
        return 127, f"ERROR: {DOCKER_UNAVAILABLE_GUIDANCE}"
    stderr = completed.stderr or ""
    if completed.returncode != 0 and (
        "Cannot connect to the Docker daemon" in stderr
        or "error during connect" in stderr
        or "docker daemon is not running" in stderr.lower()
    ):
        return 127, f"ERROR: {DOCKER_UNAVAILABLE_GUIDANCE}\n[docker said]\n{stderr.strip()}"
    output = (completed.stdout or "") + (("\n[stderr]\n" + stderr) if stderr else "")
    return completed.returncode, output


def run_in_docker(command: str, workspace: str, config: dict) -> tuple[int, str]:
    """Run a shell command inside the configured container image."""
    cfg = _docker_cfg(config)
    timeout = int(cfg.get("timeout_seconds", 120))
    argv = build_docker_command(["sh", "-c", command], workspace, config)
    return _run(argv, config, timeout)


def run_python_in_docker(
    code: str, workspace: str, config: dict, timeout: int
) -> str:
    """Run a Python snippet inside the container via stdin (`python -`), so the
    code never lands in the workspace or the process argv."""
    cfg = _docker_cfg(config)
    timeout = min(int(timeout), 600)
    argv = build_docker_command(
        ["python", "-I", "-"], workspace, config, interactive=True
    )
    returncode, output = _run(argv, config, timeout, stdin_text=code)
    limit = int(cfg.get("output_limit_bytes", 20000))
    if len(output) > limit:
        output = output[:limit] + "\n[output truncated]"
    status = "ok" if returncode == 0 else f"exit code {returncode}"
    return f"[{status}]\n{output}".rstrip() or f"[{status}]"
