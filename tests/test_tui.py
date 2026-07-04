from __future__ import annotations

import asyncio

import pytest

from pulsar_agent.cli import tui as tui_module
from pulsar_agent.cli.main import build_parser, main
from pulsar_agent.cli.repl import Repl, recovery_hint
from pulsar_agent.cli.tui import TUI_INSTALL_HINT, TuiController, run_tui


def make_mock_repl(workspace, home, config) -> Repl:
    config["model"] = "mock:echo"
    return Repl(home=home, config=config, workspace=workspace, interactive=False)


# --- argument parsing ---


def test_parser_accepts_tui_flag():
    args = build_parser().parse_args(["--tui"])
    assert args.tui is True
    args = build_parser().parse_args([])
    assert args.tui is False  # classic CLI stays the default


def test_tui_and_once_conflict(tmp_path, capsys):
    code = main(["--tui", "--once", "hello", "--workspace", str(tmp_path)])
    assert code == 2
    assert "cannot be combined" in capsys.readouterr().err


# --- graceful degradation ---


def test_run_tui_without_textual(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(tui_module, "tui_available", lambda: False)
    code = run_tui(tmp_path, {}, tmp_path)
    assert code == 2
    out = capsys.readouterr().out
    assert "pulsar-agent[tui]" in out
    assert TUI_INSTALL_HINT.splitlines()[0] in out


# --- controller logic (no terminal, no textual needed) ---


@pytest.mark.parametrize(
    ("line", "kind", "arg"),
    [
        ("", "empty", ""),
        ("   ", "empty", ""),
        ("hello agent", "message", "hello agent"),
        ("/quit", "quit", ""),
        ("/exit", "quit", ""),
        ("/help", "help", ""),
        ("/reset", "reset", ""),
        ("/model", "model", ""),
        ("/model mock:echo", "model", "mock:echo"),
        ("/bogus", "unknown", "/bogus"),
    ],
)
def test_parse_input(line, kind, arg):
    command = TuiController.parse_input(line)
    assert command.kind == kind
    assert command.arg == arg


def test_controller_status_and_flow(workspace, home, config):
    repl = make_mock_repl(workspace, home, config)
    try:
        controller = TuiController(repl)
        status = controller.status_text()
        assert "mock:echo" in status
        assert repl.approvals.preset in status
        assert str(repl.agent.context.session_id) in status
        assert "checkpoints" in status and "backend" in status

        assert "TUI commands" in controller.help_text()
        assert controller.run_message("ping") == "echo: ping"
        assert "cleared" in controller.reset()

        assert "active model: mock:echo" in controller.switch_model("")
        assert "cannot switch model" in controller.switch_model("not-a-model-id")
        assert "switched to mock:echo" in controller.switch_model("mock:echo")
    finally:
        repl.close()


def test_switch_model_does_not_persist_bad_id(workspace, home, config):
    # Regression: a bad id must not be written to config.yaml (would brick the
    # next startup). model_id must stay on the previous good value.
    from pulsar_agent.config import load_config

    repl = make_mock_repl(workspace, home, config)
    try:
        assert "cannot switch model" in repl.switch_model("not-a-model-id")
        assert repl.model_id == "mock:echo"
        # The bad id was never written to config.yaml.
        assert load_config(home).get("model") != "not-a-model-id"
    finally:
        repl.close()


def test_memory_command_available_in_tui(workspace, home, config):
    repl = make_mock_repl(workspace, home, config)
    try:
        controller = TuiController(repl)
        assert TuiController.parse_input("/memory").kind == "memory"
        # staged write is reachable and approvable from the TUI
        repl.memory.apply("MEMORY.md", "add", "tui memory fact")
        assert "staged" in controller.memory("")
        assert "applied" in controller.memory("approve")
        assert "tui memory fact" in repl.memory.snapshot()
    finally:
        repl.close()


# --- app smoke test (headless textual pilot) ---


def test_tui_app_message_flow(workspace, home, config):
    pytest.importorskip("textual")
    repl = make_mock_repl(workspace, home, config)
    app = tui_module._build_app(repl, startup_lines=["startup-line"])

    async def scenario():
        async with app.run_test() as pilot:
            composer = app.query_one("#composer")
            composer.value = "hello tui"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            transcript = app.query_one("#transcript")
            text = "\n".join(strip.text for strip in transcript.lines)
            assert "startup-line" in text
            assert "> hello tui" in text
            assert "echo: hello tui" in text

    try:
        asyncio.run(scenario())
    finally:
        repl.close()


def test_tui_app_help_and_status(workspace, home, config):
    pytest.importorskip("textual")
    repl = make_mock_repl(workspace, home, config)
    app = tui_module._build_app(repl, startup_lines=[])

    async def scenario():
        async with app.run_test() as pilot:
            composer = app.query_one("#composer")
            composer.value = "/help"
            await pilot.press("enter")
            await pilot.pause()
            transcript = app.query_one("#transcript")
            text = "\n".join(strip.text for strip in transcript.lines)
            assert "TUI commands" in text
            status = app.query_one("#status")
            assert "mock:echo" in str(status.render())

    try:
        asyncio.run(scenario())
    finally:
        repl.close()


# --- recovery hints (Bar 6) ---


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("HTTP 401 unauthorized", "pulsar setup"),
        ("rate limit exceeded (429)", "fallback_models"),
        ("connection timed out", "connectivity"),
        ("docker daemon is not running", "terminal.backend: local"),
        ("mcp server 'x' is not running", "mcp.servers"),
        ("hardline blocked: rm -rf /", "not overridable"),
        ("terminal requires approval", "security.autonomy"),
        ("totally novel failure", None),
    ],
)
def test_recovery_hints(error, expected):
    hint = recovery_hint(error)
    if expected is None:
        assert hint is None
    else:
        assert hint is not None and expected in hint
