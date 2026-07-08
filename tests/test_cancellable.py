from __future__ import annotations

import sys
import threading
import time

from pulsar_agent.tools.cancellable import run_cancellable

SLEEP_FOREVER = [sys.executable, "-c", "import time; time.sleep(60)"]


def test_normal_completion():
    outcome = run_cancellable(
        [sys.executable, "-c", "print('done')"], timeout=30
    )
    assert outcome.returncode == 0
    assert "done" in outcome.stdout
    assert not outcome.cancelled and not outcome.timed_out


def test_stdin_text_delivered():
    outcome = run_cancellable(
        [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
        timeout=30,
        stdin_text="hello",
    )
    assert "HELLO" in outcome.stdout


def test_timeout_kills_process():
    start = time.monotonic()
    outcome = run_cancellable(SLEEP_FOREVER, timeout=1)
    assert outcome.timed_out is True
    assert time.monotonic() - start < 20


def test_cancel_kills_process_quickly():
    cancel = threading.Event()
    timer = threading.Timer(0.6, cancel.set)
    timer.start()
    start = time.monotonic()
    try:
        outcome = run_cancellable(
            SLEEP_FOREVER, timeout=60, should_cancel=cancel.is_set
        )
    finally:
        timer.cancel()
    assert outcome.cancelled is True
    assert time.monotonic() - start < 20  # nowhere near the 60s sleep


def test_cancel_captures_partial_output():
    cancel = threading.Event()
    code = "import sys, time\nprint('early', flush=True)\ntime.sleep(60)"
    timer = threading.Timer(1.0, cancel.set)
    timer.start()
    try:
        outcome = run_cancellable(
            [sys.executable, "-c", code], timeout=60, should_cancel=cancel.is_set
        )
    finally:
        timer.cancel()
    assert outcome.cancelled is True
    assert "early" in outcome.stdout


def test_terminal_cancel_path(workspace, home, config):
    # End-to-end: a cancelled context stops a running terminal command.
    from pulsar_agent.tools.terminal import run_local_command

    cancel = threading.Event()
    command = f'"{sys.executable}" -c "import time; time.sleep(60)"'
    timer = threading.Timer(0.8, cancel.set)
    timer.start()
    start = time.monotonic()
    try:
        code, output = run_local_command(
            command, str(workspace), config, should_cancel=cancel.is_set
        )
    finally:
        timer.cancel()
    assert code == 130
    assert "cancelled" in output
    assert time.monotonic() - start < 30
