"""Terminal tool: workspace-scoped command execution.

Pipeline per call: hardline blocklist check (non-overridable) -> risk
classification -> approval per preset -> scrubbed-environment subprocess ->
truncation -> redaction (registry applies redaction to every result too).
"""

from __future__ import annotations

import os
import subprocess

from pulsar_agent.security.approvals import KIND_TERMINAL, ApprovalRequest
from pulsar_agent.security.command_risk import (
    HardlineBlocked,
    RiskTier,
    classify_command,
)
from pulsar_agent.security.redaction import SECRET_NAME_RE
from pulsar_agent.tools.registry import ToolContext, ToolSpec

BASELINE_ENV_VARS = (
    "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
    "TEMP", "TMP", "LANG", "LC_ALL", "TZ", "SHELL", "TERM", "OS",
    "PYTHONIOENCODING", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA",
    "LOCALAPPDATA", "APPDATA", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    "HOMEDRIVE", "HOMEPATH", "USERNAME", "COMPUTERNAME", "VIRTUAL_ENV",
)


def scrubbed_environment(passthrough: list[str] | None = None) -> dict[str, str]:
    """Scrub mode: baseline env + all non-secret-named vars; secret-named vars
    are dropped unless explicitly passed through. Weaker than allowlist mode
    because it trusts variable *names*."""
    passthrough_set = {name.upper() for name in (passthrough or [])}
    env: dict[str, str] = {}
    for name, value in os.environ.items():
        upper = name.upper()
        if upper in passthrough_set or upper in BASELINE_ENV_VARS:
            env[name] = value
        elif not SECRET_NAME_RE.search(name):
            env[name] = value
    return env


def allowlist_environment(passthrough: list[str] | None = None) -> dict[str, str]:
    """Allowlist-first mode: ONLY a fixed baseline plus explicitly declared
    passthrough vars survive. A secret with a bland name (e.g. MYVALUE) is
    stripped because it is neither baseline nor declared."""
    passthrough_set = {name.upper() for name in (passthrough or [])}
    env: dict[str, str] = {}
    for name, value in os.environ.items():
        upper = name.upper()
        if upper in BASELINE_ENV_VARS or upper in passthrough_set:
            env[name] = value
    return env


def build_subprocess_env(config: dict) -> dict[str, str]:
    """Build the child-process environment per config.

    `terminal.env_mode`: "allowlist" (default, safest) or "scrub".
    `terminal.env_passthrough`: variable names explicitly allowed in either mode.
    """
    terminal_cfg = config.get("terminal", {}) or {}
    passthrough = terminal_cfg.get("env_passthrough") or []
    mode = str(terminal_cfg.get("env_mode", "allowlist")).lower()
    if mode == "scrub":
        return scrubbed_environment(passthrough)
    return allowlist_environment(passthrough)


def run_scrubbed(
    command: str,
    cwd: str,
    timeout: int,
    passthrough: list[str] | None = None,
) -> tuple[int, str]:
    """Back-compat helper (scrub-mode env). Prefer run_local_command."""
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            env=scrubbed_environment(passthrough),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, f"[command timed out after {timeout}s]"
    output = (completed.stdout or "") + (
        ("\n[stderr]\n" + completed.stderr) if completed.stderr else ""
    )
    return completed.returncode, output


def run_local_command(command: str, cwd: str, config: dict) -> tuple[int, str]:
    terminal_cfg = config.get("terminal", {}) or {}
    timeout = int(terminal_cfg.get("timeout_seconds", 120))
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            env=build_subprocess_env(config),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, f"[command timed out after {timeout}s]"
    output = (completed.stdout or "") + (
        ("\n[stderr]\n" + completed.stderr) if completed.stderr else ""
    )
    return completed.returncode, output


def terminal_handler(args: dict, context: ToolContext) -> str:
    command = str(args.get("command", "")).strip()
    if not command:
        return "ERROR: command is required"
    tier, reason = classify_command(command)
    if tier is RiskTier.BLOCKED:
        raise HardlineBlocked(f"hardline blocklist ({reason}): refusing to run")

    terminal_cfg = context.config.get("terminal", {}) or {}
    backend = str(terminal_cfg.get("backend", "local")).lower()
    will_checkpoint = tier is not RiskTier.SAFE and context.checkpoints is not None

    context.approvals.check(
        ApprovalRequest(
            kind=KIND_TERMINAL,
            description=command,
            risk=tier,
            detail=reason,
            cwd=str(context.workspace),
            will_checkpoint=will_checkpoint,
        )
    )
    if will_checkpoint:
        try:
            context.checkpoints.snapshot(f"before terminal: {command[:60]}")  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            context.emit("warn", f"checkpoint failed: {exc}")
    context.emit("terminal", f"[{backend}] {command}")

    if backend == "docker":
        from pulsar_agent.tools.docker_backend import run_in_docker

        code, output = run_in_docker(command, str(context.workspace), context.config)
    else:
        code, output = run_local_command(command, str(context.workspace), context.config)

    limit = int(terminal_cfg.get("output_limit_bytes", 20000))
    if len(output) > limit:
        output = output[:limit] + "\n[output truncated]"
    status = "ok" if code == 0 else f"exit code {code}"
    return f"[{status}]\n{output}".rstrip() or f"[{status}]"


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="terminal",
            description=(
                "Run a shell command in the workspace. Output is truncated and "
                "redacted. Destructive commands need approval; catastrophic "
                "ones are always refused."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                },
                "required": ["command"],
            },
            handler=terminal_handler,
        ),
    ]
