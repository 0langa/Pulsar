"""execute_code: run Python in a scrubbed child process.

The child gets a minimal environment (no secret-named variables), runs in
the workspace, and has no RPC channel back into the agent — so it cannot
call tools, delegate_task, or MCP recursively by construction. Code that
embeds hardline shell patterns is refused.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from pulsar_agent.security.approvals import KIND_EXECUTE_CODE, ApprovalRequest
from pulsar_agent.security.command_risk import HardlineBlocked, RiskTier, classify_command
from pulsar_agent.tools.registry import ToolContext, ToolSpec
from pulsar_agent.tools.terminal import scrubbed_environment

DEFAULT_TIMEOUT = 120


def execute_code_handler(args: dict, context: ToolContext) -> str:
    code = str(args.get("code", ""))
    if not code.strip():
        return "ERROR: code is required"
    for line in code.splitlines():
        tier, reason = classify_command(line)
        if tier is RiskTier.BLOCKED:
            raise HardlineBlocked(f"hardline blocklist ({reason}) in code")
    context.approvals.check(
        ApprovalRequest(
            kind=KIND_EXECUTE_CODE,
            description=f"execute_code ({len(code)} chars)",
            detail=code[:200],
        )
    )
    timeout = int(args.get("timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)
    timeout = min(timeout, 600)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(code)
        script_path = Path(handle.name)
    try:
        completed = subprocess.run(
            [sys.executable, "-I", str(script_path)],
            cwd=str(context.workspace),
            env=scrubbed_environment(
                context.config.get("terminal", {}).get("env_passthrough") or []
            ),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: code timed out after {timeout}s"
    finally:
        try:
            script_path.unlink()
        except OSError:
            pass
    output = (completed.stdout or "") + (
        ("\n[stderr]\n" + completed.stderr) if completed.stderr else ""
    )
    status = "ok" if completed.returncode == 0 else f"exit code {completed.returncode}"
    return f"[{status}]\n{output}".rstrip() or f"[{status}]"


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="execute_code",
            description=(
                "Run a Python snippet in an isolated child process with a "
                "scrubbed environment. Use for calculations, scripting, and "
                "quick data processing. No access to agent tools."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "timeout": {"type": "integer", "description": "Seconds, max 600"},
                },
                "required": ["code"],
            },
            handler=execute_code_handler,
            check_fn=lambda ctx: not ctx.is_subagent,
        ),
    ]
