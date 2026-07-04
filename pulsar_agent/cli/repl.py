"""Interactive REPL: session wiring, slash commands, approval prompts."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from pulsar_agent import __version__
from pulsar_agent.checkpoints.store import CheckpointStore
from pulsar_agent.config import save_config
from pulsar_agent.home import display_pulsar_home
from pulsar_agent.intel import (
    build_project_map,
    git_diff_stat,
    git_summary,
    render_project_map,
)
from pulsar_agent.mcp.manager import McpManager
from pulsar_agent.memory.store import MemoryStore
from pulsar_agent.prompt_builder import build_system_prompt
from pulsar_agent.providers.router import parse_model_id
from pulsar_agent.run_agent import Agent, build_agent_runtime
from pulsar_agent.secrets import SecretStore
from pulsar_agent.security.approvals import (
    ApprovalRequest,
    autonomy_from_config,
    build_approval_manager,
)
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
  /map                     show the project map and git status
  /memory [approve|discard] show memory; apply/discard staged writes
  /skills                  list discovered skills
  /checkpoint [label]      create a manual checkpoint
  /rollback [ref]          restore the last (or given) checkpoint
  /reset                   clear the conversation, keep the session
  /new                     start a new session
  /help                    show this help
  /quit                    exit
"""

# Recovery hints keyed by substring match against error text. Checked in
# order; first hit wins. Keep phrases lowercase.
RECOVERY_HINTS = (
    (("api key", "unauthorized", "401", "authentication"),
     "Provider auth problem. Run `pulsar setup` to store/refresh the key in PULSAR_HOME/.env."),
    (("rate limit", "429", "overloaded", "529"),
     "Provider is rate-limiting. Wait and retry, or configure `fallback_models` in config.yaml."),
    (("connection", "timed out", "timeout", "unreachable", "getaddrinfo"),
     "Network/provider unreachable. Check connectivity; for local models make sure the server (ollama/lmstudio) is running."),
    (("docker",),
     "Docker backend problem. Start Docker Desktop / the daemon, or switch back with `terminal.backend: local`."),
    (("mcp server",),
     "MCP server problem. Check the server's command/args in config.yaml `mcp.servers`; restart pulsar to reconnect."),
    (("hardline", "blocked"),
     "A safety boundary refused the action. This is not overridable; rephrase the task without the destructive step."),
    (("requires approval", "denied"),
     "Action needs approval. Re-run interactively, or grant the specific capability under `security.autonomy` (trusted-local only)."),
    (("checkpoint", "rollback"),
     "Checkpoint issue. Checkpoints need `git` on PATH; check `checkpoints.enabled` in config.yaml."),
)


def recovery_hint(error_text: str) -> str | None:
    lowered = error_text.lower()
    for needles, hint in RECOVERY_HINTS:
        if any(needle in lowered for needle in needles):
            return hint
    return None


def _console_approver(request: ApprovalRequest) -> bool:
    print(f"\n[approval needed] {request.kind}")
    print(f"  command : {request.description}")
    if request.cwd:
        print(f"  cwd     : {request.cwd}")
    print(f"  risk    : {request.risk.value}")
    if request.detail:
        print(f"  reason  : {request.detail}")
    print(f"  checkpoint: {'yes (rollback available)' if request.will_checkpoint else 'no'}")
    answer = input("Approve? [y/N]: ").strip().lower()
    return answer in ("y", "yes")


class Repl:
    def __init__(
        self,
        home: Path,
        config: dict,
        workspace: Path,
        interactive: bool = True,
        approver: Callable | None = None,
        on_tool_event: Callable[[str, str], None] | None = None,
        on_assistant_text: Callable[[str], None] | None = None,
    ):
        self.home = home
        self.config = config
        self.workspace = workspace.resolve()
        self._on_tool_event = on_tool_event
        self._on_assistant_text = on_assistant_text
        self._turn_started = 0.0
        self._tool_counter = 0
        self._cancelled = False
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
        self.approvals = build_approval_manager(
            config,
            approver if approver is not None
            else (_console_approver if interactive else None),
        )
        self.project_map_text = render_project_map(
            build_project_map(self.workspace), git_summary(self.workspace)
        )
        self.skills = discover_skills(home)
        # MCP warnings can carry server stderr (env dumps, echoed secrets);
        # redact before the console like every other output path.
        self.mcp = McpManager(
            config,
            warn=lambda message: print(f"  [mcp] {self.redactor.redact(message)}"),
        )
        self.mcp.start()
        self._agent: Agent | None = None
        self.model_id: str = config.get("model", "")
        self._build_agent(new_session=True)

    @property
    def agent(self) -> Agent:
        if self._agent is None:
            raise RuntimeError("agent not initialized")
        return self._agent

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
            if new_session or self._agent is None
            else self._agent.context.session_id
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
            on_tool_event=self._emit_tool_event,
        )
        system_prompt = build_system_prompt(
            workspace=self.workspace,
            memory=self.memory,
            skills=self.skills,
            project_map=self.project_map_text,
        )
        registry = build_core_registry()
        for spec in self.mcp.tool_specs():
            try:
                registry.register(spec)
            except ValueError:
                # Two servers whose names sanitize to the same prefix would
                # collide; skip the duplicate rather than abort startup.
                print(f"  [mcp] skipping duplicate tool name {spec.name!r}")
        self._agent = Agent(
            transport=transport,
            registry=registry,
            context=context,
            system_prompt=system_prompt,
            session_store=self.session_store,
            max_iterations=int(self.config.get("max_iterations", 40)),
            max_tokens=int(self.config.get("max_tokens", 8192)),
            fallback_transports=fallbacks,
            # Indirection so a caller (TUI) that swaps its sink after build
            # still receives assistant text.
            on_assistant_text=self._emit_assistant_text,
            should_cancel=lambda: self._cancelled,
        )

    def _emit_assistant_text(self, text: str) -> None:
        if self._on_assistant_text is not None:
            self._on_assistant_text(text)
        else:
            print(f"\n{text}\n")

    def _emit_tool_event(self, event: str, detail: str) -> None:
        """Progress line with per-turn tool counter and elapsed seconds so
        long runs stay readable."""
        detail = self.redactor.redact(detail)
        if event == "tool":
            self._tool_counter += 1
        elapsed = time.monotonic() - self._turn_started if self._turn_started else 0.0
        line = f"  · [{self._tool_counter:>2} {elapsed:5.1f}s] {event}: {detail}"
        if self._on_tool_event is not None:
            self._on_tool_event(event, line.strip())
        else:
            print(line)

    def start_turn_clock(self) -> None:
        self._turn_started = time.monotonic()
        self._tool_counter = 0

    def status_line(self) -> str:
        preset = self.approvals.preset
        grants = [k for k, v in autonomy_from_config(self.config).items() if v]
        badge = ""
        if preset == "trusted-local" and grants:
            badge = f"  [!] autonomy: {', '.join(g.replace('allow_', '') for g in grants)}"
        checkpoint_state = "on" if self.checkpoints else "off"
        backend = self.config.get("terminal", {}).get("backend", "local")
        return (
            f"pulsar {__version__} | model {self.model_id} | preset {preset}{badge}\n"
            f"session {self.agent.context.session_id} | workspace {self.workspace}\n"
            f"home {display_pulsar_home(self.home)} | checkpoints {checkpoint_state} "
            f"| backend {backend}"
        )

    def switch_model(self, model_id: str) -> str:
        """Switch the active model. Rebuilds the agent BEFORE persisting so a
        bad id (bad format, missing key) never gets written to config.yaml and
        brick the next startup. On failure the previous agent stays live."""
        if not model_id:
            return f"active model: {self.model_id}"
        previous = self.model_id
        try:
            parse_model_id(model_id)
            self.model_id = model_id
            self._build_agent(new_session=False)
        except Exception as exc:
            self.model_id = previous
            return f"cannot switch model: {exc}"
        self.config["model"] = model_id
        try:
            save_config(self.home, self.config)
        except Exception as exc:
            return f"switched to {model_id} (not saved: {exc})"
        return f"switched to {model_id}"

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
            print(self.switch_model(arg))
        elif command == "/tools":
            for spec in self.agent.registry.enabled(self.agent.context):
                print(f"- {spec.name}: {spec.description.splitlines()[0][:90]}")
        elif command == "/map":
            print(self.project_map_text or "(no project map)")
            diff = git_diff_stat(self.workspace)
            if diff:
                print("\nUncommitted diffstat:\n" + diff)
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
                except Exception as exc:
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
        try:
            self.start_turn_clock()
            return self.agent.run_turn(message)
        finally:
            self.close()

    def close(self) -> None:
        self.mcp.close()

    def run(self) -> int:
        print(self.status_line())
        print("Type a request, /help for commands, /quit to exit.\n")
        try:
            while True:
                try:
                    line = input("pulsar> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 0
                if not line:
                    continue
                if line.startswith("/"):
                    try:
                        if not self.handle_slash(line):
                            return 0
                    except Exception as exc:
                        print(f"[error] {self.redactor.redact(str(exc))}")
                    continue
                try:
                    self.start_turn_clock()
                    reply = self.agent.run_turn(line)
                    print(f"\n{reply}\n")
                except KeyboardInterrupt:
                    # run_turn already repaired history for the next turn.
                    print("\n[turn interrupted]")
                except Exception as exc:
                    message = self.redactor.redact(str(exc))
                    print(f"\n[error] {message}")
                    hint = recovery_hint(message)
                    if hint:
                        print(f"[hint] {hint}")
                    print()
        finally:
            self.close()
