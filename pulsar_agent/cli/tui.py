"""Opt-in full-screen TUI (`pulsar --tui`).

The classic CLI stays the default. Textual is an optional extra
(`pip install "pulsar-agent[tui]"`); without it — or on terminals Textual
cannot drive — `run_tui` fails gracefully with guidance instead of a
traceback.

Layout: status bar (model, session, preset, checkpoints, backend), scrolling
transcript, input composer. Agent turns run in a worker thread; approval
requests from that thread block on an event while a modal asks the user.

`TuiController` holds all UI-toolkit-independent logic (command parsing,
status text, model switching) so tests never need a terminal or textual.
"""

from __future__ import annotations

import importlib.util
import threading
from dataclasses import dataclass

from pulsar_agent import __version__
from pulsar_agent.cli.repl import HELP_TEXT, Repl, recovery_hint
from pulsar_agent.security.approvals import ApprovalRequest

TUI_INSTALL_HINT = (
    "The TUI needs the optional dependency 'textual'.\n"
    'Install it with:  pip install "pulsar-agent[tui]"\n'
    "or run the classic CLI: just `pulsar` without --tui."
)

APPROVAL_WAIT_SECONDS = 600


def tui_available() -> bool:
    return importlib.util.find_spec("textual") is not None


@dataclass(frozen=True)
class TuiCommand:
    kind: str  # "message" | "quit" | "help" | "reset" | "model" | "empty" | "unknown"
    arg: str = ""


class TuiController:
    """Toolkit-independent TUI logic; the Textual app is a thin shell over it."""

    def __init__(self, repl: Repl):
        self.repl = repl

    @staticmethod
    def parse_input(line: str) -> TuiCommand:
        line = line.strip()
        if not line:
            return TuiCommand("empty")
        if not line.startswith("/"):
            return TuiCommand("message", line)
        parts = line.split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if command in ("/quit", "/exit"):
            return TuiCommand("quit")
        if command == "/help":
            return TuiCommand("help")
        if command == "/reset":
            return TuiCommand("reset")
        if command == "/model":
            return TuiCommand("model", arg)
        if command == "/memory":
            return TuiCommand("memory", arg)
        return TuiCommand("unknown", command)

    def status_text(self) -> str:
        repl = self.repl
        checkpoints = "on" if repl.checkpoints else "off"
        backend = repl.config.get("terminal", {}).get("backend", "local")
        session = repl.agent.context.session_id or "-"
        return (
            f" pulsar {__version__}  │  model {repl.model_id}  │  "
            f"preset {repl.approvals.preset}  │  session {session}  │  "
            f"checkpoints {checkpoints}  │  backend {backend} "
        )

    def help_text(self) -> str:
        return (
            "TUI commands: /model [provider:model], /memory [approve|discard], "
            "/reset, /help, /quit\n"
            "Everything else is sent to the agent.\n\n"
            "Classic-CLI command reference:\n" + HELP_TEXT
        )

    def switch_model(self, model_id: str) -> str:
        # Delegate to the REPL so the TUI shares the validate-before-persist
        # behavior (a bad id never bricks the next startup).
        return self.repl.switch_model(model_id)

    def memory(self, arg: str) -> str:
        if arg == "approve":
            return self.repl.memory.approve_staged()
        if arg == "discard":
            return self.repl.memory.discard_staged()
        snapshot = self.repl.memory.snapshot() or "(no memory yet)"
        if self.repl.memory.staged:
            snapshot += (
                f"\n\n{len(self.repl.memory.staged)} staged write(s); "
                "/memory approve or /memory discard"
            )
        return snapshot

    def reset(self) -> str:
        self.repl.agent.reset()
        return "conversation cleared (session kept)"

    def run_message(self, text: str) -> str:
        self.repl.start_turn_clock()
        return self.repl.agent.run_turn(text)


def run_tui(home, config, workspace) -> int:
    """Entry point for `pulsar --tui`. Returns a process exit code."""
    if not tui_available():
        print(TUI_INSTALL_HINT)
        return 2

    events: list[str] = []
    repl: Repl | None = None
    try:
        # Repl construction (provider resolution, MCP start) can raise; keep it
        # inside the guard so startup errors degrade to guidance, not a raw
        # traceback that also skips redaction.
        repl = Repl(
            home=home,
            config=config,
            workspace=workspace,
            interactive=True,
            # Buffered until the app runs; app.on_mount swaps in live sinks.
            on_tool_event=lambda event, line: events.append(line),
            on_assistant_text=lambda text: events.append(text),
        )
        app = _build_app(repl, startup_lines=events)
        app.run()
        return 0
    except Exception as exc:
        redact = repl.redactor.redact if repl is not None else str
        message = redact(str(exc))
        print(f"TUI failed to start: {message}")
        hint = recovery_hint(message)
        if hint:
            print(f"hint: {hint}")
        print("Falling back is easy: run `pulsar` without --tui for the classic CLI.")
        return 2
    finally:
        if repl is not None:
            repl.close()


def _build_app(repl: Repl, startup_lines: list[str]):
    """Import textual lazily and construct the app class."""
    from textual.app import App, ComposeResult
    from textual.containers import Vertical
    from textual.screen import ModalScreen
    from textual.widgets import Button, Input, RichLog, Static

    controller = TuiController(repl)

    class ApprovalScreen(ModalScreen):
        """Modal shown while the agent worker thread waits for a decision."""

        def __init__(self, request: ApprovalRequest, done: threading.Event, result: dict):
            super().__init__()
            self._request = request
            self._done = done
            self._result = result

        def compose(self) -> ComposeResult:
            request = self._request
            body = (
                f"[b]Approval needed: {request.kind}[/b]\n\n"
                f"{request.description}\n"
                f"risk: {request.risk.value}"
                + (f"\nreason: {request.detail}" if request.detail else "")
                + (f"\ncwd: {request.cwd}" if request.cwd else "")
                + ("\ncheckpoint: yes (rollback available)"
                   if request.will_checkpoint else "")
            )
            yield Vertical(
                Static(body, id="approval-body"),
                Button("Approve (y)", id="approve", variant="success"),
                Button("Deny (n)", id="deny", variant="error"),
                id="approval-box",
            )

        def on_button_pressed(self, event: Button.Pressed) -> None:
            self._finish(event.button.id == "approve")

        def on_key(self, event) -> None:
            if event.key in ("y", "Y"):
                self._finish(True)
            elif event.key in ("n", "N", "escape"):
                self._finish(False)

        def _finish(self, approved: bool) -> None:
            self._result["approved"] = approved
            self._done.set()
            self.app.pop_screen()

    class PulsarTui(App):
        TITLE = "pulsar"
        CSS = """
        #status { height: 1; background: $surface; color: $text; }
        #transcript { border: solid $primary; }
        #composer { dock: bottom; }
        #approval-box { padding: 1 2; background: $surface; border: heavy $warning;
                        width: 80%; height: auto; align: center middle; }
        #approval-body { margin-bottom: 1; }
        ApprovalScreen { align: center middle; }
        """
        BINDINGS = [("ctrl+c", "quit", "Quit")]  # noqa: RUF012 - textual convention

        def compose(self) -> ComposeResult:
            yield Static(controller.status_text(), id="status")
            yield RichLog(id="transcript", wrap=True, markup=False)
            yield Input(placeholder="Type a request… (/help for commands)", id="composer")

        def on_mount(self) -> None:
            self._pending_approvals: set[threading.Event] = set()
            self._shutting_down = False
            repl._cancelled = False
            transcript = self.query_one("#transcript", RichLog)
            for line in startup_lines:
                transcript.write(line)
            transcript.write("Ready. /help for commands, /quit to exit.")
            # Route agent output through the running app from here on.
            repl._on_tool_event = lambda event, line: self.call_from_thread(
                self._write, line
            )
            repl._on_assistant_text = lambda text: self.call_from_thread(
                self._write, text
            )
            repl.approvals.approver = self._threaded_approver
            self.query_one("#composer", Input).focus()

        def on_unmount(self) -> None:
            # Cooperatively stop any in-flight turn and release approval waits so
            # the worker thread does not keep running (or block shutdown) after
            # the user quits.
            self._shutting_down = True
            repl._cancelled = True
            for event in list(self._pending_approvals):
                event.set()

        def _write(self, line: str) -> None:
            self.query_one("#transcript", RichLog).write(line)

        def _refresh_status(self) -> None:
            self.query_one("#status", Static).update(controller.status_text())

        def _threaded_approver(self, request: ApprovalRequest) -> bool:
            if self._shutting_down:
                return False
            done = threading.Event()
            result: dict = {}
            self._pending_approvals.add(done)
            try:
                self.call_from_thread(
                    self.push_screen, ApprovalScreen(request, done, result)
                )
                done.wait(timeout=APPROVAL_WAIT_SECONDS)
            finally:
                self._pending_approvals.discard(done)
            # Deny on shutdown or timeout (result unset).
            return bool(result.get("approved", False)) and not self._shutting_down

        def on_input_submitted(self, event: Input.Submitted) -> None:
            command = controller.parse_input(event.value)
            composer = self.query_one("#composer", Input)
            composer.value = ""
            if command.kind == "empty":
                return
            if command.kind == "quit":
                self.exit(0)
                return
            if command.kind == "help":
                self._write(controller.help_text())
                return
            if command.kind == "reset":
                self._write(controller.reset())
                return
            if command.kind == "memory":
                self._write(controller.memory(command.arg))
                return
            if command.kind == "model":
                self._write(controller.switch_model(command.arg))
                self._refresh_status()
                return
            if command.kind == "unknown":
                self._write(f"unknown command {command.arg}; /help lists commands")
                return
            self._write(f"> {command.arg}")
            composer.disabled = True
            repl._cancelled = False
            self.run_worker(
                lambda: self._run_turn(command.arg), thread=True, exclusive=True
            )

        def _run_turn(self, text: str) -> None:
            try:
                reply = controller.run_message(text)
                self.call_from_thread(self._write, reply)
            except Exception as exc:
                message = repl.redactor.redact(str(exc))
                self.call_from_thread(self._write, f"[error] {message}")
                hint = recovery_hint(message)
                if hint:
                    self.call_from_thread(self._write, f"[hint] {hint}")
            finally:
                # The app may already be tearing down; ignore if so.
                try:
                    self.call_from_thread(self._enable_composer)
                except Exception:
                    pass

        def _enable_composer(self) -> None:
            composer = self.query_one("#composer", Input)
            composer.disabled = False
            composer.focus()

    return PulsarTui()
