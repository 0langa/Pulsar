"""Interactive REPL: session wiring, slash commands, approval prompts."""

from __future__ import annotations

from pathlib import Path

from pulsar_agent import __version__
from pulsar_agent.checkpoints.store import CheckpointStore
from pulsar_agent.config import APPROVAL_PRESETS, save_config
from pulsar_agent.home import display_pulsar_home
from pulsar_agent.memory.store import MemoryStore
from pulsar_agent.prompt_builder import build_system_prompt
from pulsar_agent.providers.router import parse_model_id
from pulsar_agent.run_agent import Agent, build_agent_runtime
from pulsar_agent.secrets import SecretStore
from pulsar_agent.security.approvals import ApprovalManager, ApprovalRequest
from pulsar_agent.security.paths import PathPolicy
from pulsar_agent.security.redaction import Redactor
from pulsar_agent.sessions.store import DB_FILENAME, SessionStore
from pulsar_agent.skills.loader import builtin_skills_dir, discover_skills
from pulsar_agent.tools import build_core_registry
from pulsar_agent.tools.registry import ToolContext

HELP_TEXT = """\
Slash commands:
  /model [provider:model]  show or switch the active model
  /tools                   list enabled tools
  /memory [approve|discard] show memory; apply/discard staged writes
  /skills                  list discovered skills
  /checkpoint [label]      create a manual checkpoint
  /rollback [ref]          restore the last (or given) checkpoint
  /reset                   clear the conversation, keep the session
  /new                     start a new session
  /help                    show this help
  /quit                    exit
"""


def _console_approver(request: ApprovalRequest) -> bool:
    print(f"\n[approval needed] {request.kind}: {request.description}")
    if request.detail:
        print(f"  reason: {request.detail}")
    answer = input("Approve? [y/N]: ").strip().lower()
    return answer in ("y", "yes")


class Repl:
    def __init__(
        self,
        home: Path,
        config: dict,
        workspace: Path,
        interactive: bool = True,
    ):
        self.home = home
        self.config = config
        self.workspace = workspace.resolve()
        self.secrets = SecretStore(home)
        self.redactor = Redactor(
            known_values=self.secrets.all_values(),
            enabled=bool(config.get("security", {}).get("redact_secrets", True)),
        )
        self.session_store = SessionStore(home / DB_FILENAME, self.redactor)
        self.memory = MemoryStore(home, config, self.redactor)
        self.checkpoints: CheckpointStore | None = None
        if config.get("checkpoints", {}).get("enabled", True):
            store = CheckpointStore(home, self.workspace)
            self.checkpoints = store if store.available() else None
        self.approvals = ApprovalManager(
            preset=config.get("approval_preset", "review"),
            approver=_console_approver if interactive else None,
            command_allowlist=list(
                config.get("security", {}).get("command_allowlist") or []
            ),
        )
        self.skills = discover_skills(home)
        self.agent: Agent | None = None
        self.model_id: str = config.get("model", "")
        self._build_agent(new_session=True)

    def _build_agent(self, new_session: bool) -> None:
        transport, fallbacks, runtime = build_agent_runtime(
            self.model_id, self.config, self.secrets
        )
        if runtime.api_key:
            self.redactor.register_value(runtime.api_key)
        session_id = (
            self.session_store.create_session(
                workspace=str(self.workspace), model_id=self.model_id
            )
            if new_session or self.agent is None
            else self.agent.context.session_id
        )
        path_policy = PathPolicy(
            workspace=self.workspace,
            extra_read_roots=[builtin_skills_dir(), self.home / "skills"],
            protected_roots=[self.home],
        )
        context = ToolContext(
            workspace=self.workspace,
            home=self.home,
            config=self.config,
            path_policy=path_policy,
            approvals=self.approvals,
            redactor=self.redactor,
            checkpoints=self.checkpoints,
            session_id=session_id,
            runtime_provider=runtime,
            transport=transport,
            on_tool_event=lambda event, detail: print(
                f"  · {event}: {self.redactor.redact(detail)}"
            ),
        )
        system_prompt = build_system_prompt(
            workspace=self.workspace, memory=self.memory, skills=self.skills
        )
        self.agent = Agent(
            transport=transport,
            registry=build_core_registry(),
            context=context,
            system_prompt=system_prompt,
            session_store=self.session_store,
            max_iterations=int(self.config.get("max_iterations", 40)),
            max_tokens=int(self.config.get("max_tokens", 8192)),
            fallback_transports=fallbacks,
            on_assistant_text=lambda text: print(f"\n{text}\n"),
        )

    def status_line(self) -> str:
        preset = self.approvals.preset
        badge = "  [!] permissive mode" if preset == "trusted-local" else ""
        checkpoint_state = "on" if self.checkpoints else "off"
        return (
            f"pulsar {__version__} | model {self.model_id} | preset {preset}{badge}\n"
            f"session {self.agent.context.session_id} | workspace {self.workspace}\n"
            f"home {display_pulsar_home(self.home)} | checkpoints {checkpoint_state}"
        )

    def handle_slash(self, line: str) -> bool:
        """Handle a slash command. Returns False when the REPL should exit."""
        parts = line.strip().split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if command in ("/quit", "/exit"):
            return False
        if command == "/help":
            print(HELP_TEXT)
        elif command == "/model":
            if not arg:
                print(f"active model: {self.model_id}")
            else:
                try:
                    parse_model_id(arg)
                    self.model_id = arg
                    self.config["model"] = arg
                    save_config(self.home, self.config)
                    self._build_agent(new_session=False)
                    print(f"switched to {arg}")
                except Exception as exc:  # noqa: BLE001
                    print(f"cannot switch model: {exc}")
        elif command == "/tools":
            for spec in self.agent.registry.enabled(self.agent.context):
                print(f"- {spec.name}: {spec.description.splitlines()[0][:90]}")
        elif command == "/memory":
            if arg == "approve":
                print(self.memory.approve_staged())
            elif arg == "discard":
                print(self.memory.discard_staged())
            else:
                snapshot = self.memory.snapshot()
                print(snapshot or "(no memory yet)")
                if self.memory.staged:
                    print(f"\n{len(self.memory.staged)} staged write(s); "
                          "/memory approve or /memory discard")
        elif command == "/skills":
            if not self.skills:
                print("no skills found")
            for skill in self.skills:
                print(f"- {skill.name} ({skill.source}): {skill.description}")
        elif command == "/checkpoint":
            if self.checkpoints is None:
                print("checkpoints disabled or git unavailable")
            else:
                ref = self.checkpoints.snapshot(arg or "manual checkpoint")
                print(f"checkpoint {ref[:12]}" if ref else "no changes to checkpoint")
        elif command == "/rollback":
            if self.checkpoints is None:
                print("checkpoints disabled or git unavailable")
            else:
                try:
                    target = self.checkpoints.restore(arg or "HEAD")
                    print(f"restored workspace to {target[:12]}")
                    if self.agent.context.session_id:
                        self.session_store.append_message(
                            self.agent.context.session_id,
                            "system",
                            f"rollback to checkpoint {target}",
                        )
                except Exception as exc:  # noqa: BLE001
                    print(f"rollback failed: {exc}")
        elif command == "/reset":
            self.agent.reset()
            print("conversation cleared (session kept)")
        elif command == "/new":
            self._build_agent(new_session=True)
            print(f"new session {self.agent.context.session_id}")
        else:
            print(f"unknown command {command}; /help lists commands")
        return True

    def run_once(self, message: str) -> str:
        return self.agent.run_turn(message)

    def run(self) -> int:
        print(self.status_line())
        print("Type a request, /help for commands, /quit to exit.\n")
        while True:
            try:
                line = input("pulsar> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not line:
                continue
            if line.startswith("/"):
                if not self.handle_slash(line):
                    return 0
                continue
            try:
                reply = self.agent.run_turn(line)
                print(f"\n{reply}\n")
            except KeyboardInterrupt:
                print("\n[turn interrupted]")
            except Exception as exc:  # noqa: BLE001
                print(f"\n[error] {self.redactor.redact(str(exc))}\n")
