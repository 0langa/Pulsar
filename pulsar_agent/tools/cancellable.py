"""Cancellable subprocess execution.

The agent's cooperative cancel (TUI quit, interrupted turn) used to stop
only *between* tool dispatches: a child process already running kept going
to completion. This runner polls a cancel callable while the child runs and,
on cancel or timeout, kills the whole process tree so no orphan keeps
working after the user has quit.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

POLL_INTERVAL_SECONDS = 0.2


@dataclass(frozen=True)
class RunOutcome:
    returncode: int
    stdout: str
    stderr: str
    cancelled: bool = False
    timed_out: bool = False

    def merged_output(self) -> str:
        return (self.stdout or "") + (
            ("\n[stderr]\n" + self.stderr) if self.stderr else ""
        )


def kill_process_tree(process: subprocess.Popen) -> None:
    """Best-effort kill of the child and all its descendants. A shell=True
    command spawns a shell whose children would survive a plain kill()."""
    if process.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                timeout=10,
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        process.kill()
    except OSError:
        pass


def run_cancellable(
    command: list[str] | str,
    *,
    shell: bool = False,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: int,
    should_cancel: Callable[[], bool] | None = None,
    stdin_text: str | None = None,
) -> RunOutcome:
    """Run a child process, polling `should_cancel` while it executes.

    On cancel or timeout the process tree is killed and whatever output the
    child produced so far is returned with the corresponding flag set.
    """
    popen_kwargs: dict = {
        "shell": shell,
        "cwd": cwd,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        "text": True,
        "errors": "replace",
    }
    if sys.platform != "win32":
        # Own process group so killpg reaches the whole tree.
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **popen_kwargs)

    deadline = time.monotonic() + timeout
    pending_input = stdin_text
    cancelled = False
    timed_out = False
    while True:
        try:
            stdout, stderr = process.communicate(
                input=pending_input, timeout=POLL_INTERVAL_SECONDS
            )
            break
        except subprocess.TimeoutExpired:
            # communicate() may only receive input on the first call.
            pending_input = None
            if should_cancel is not None and should_cancel():
                cancelled = True
            elif time.monotonic() >= deadline:
                timed_out = True
            else:
                continue
            kill_process_tree(process)
            try:
                stdout, stderr = process.communicate(timeout=10)
            except (subprocess.TimeoutExpired, ValueError, OSError):
                stdout, stderr = "", ""
            break
    return RunOutcome(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=stdout or "",
        stderr=stderr or "",
        cancelled=cancelled,
        timed_out=timed_out,
    )
