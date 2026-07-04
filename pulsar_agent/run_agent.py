"""Synchronous ReAct agent loop with tool dispatch and fallback providers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pulsar_agent.providers.base import CompletionResult, ProviderError, Transport
from pulsar_agent.providers.router import (
    RuntimeProvider,
    create_transport,
    resolve_runtime_provider,
)
from pulsar_agent.secrets import SecretStore
from pulsar_agent.sessions.store import SessionStore
from pulsar_agent.tools.registry import ToolContext, ToolRegistry

DEFAULT_MAX_ITERATIONS = 40


@dataclass
class Agent:
    transport: Transport
    registry: ToolRegistry
    context: ToolContext
    system_prompt: str
    session_store: SessionStore | None = None
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    max_tokens: int = 8192
    fallback_transports: list[Transport] = field(default_factory=list)
    on_assistant_text: Callable[[str], None] | None = None
    # Cooperative cancel: checked before each iteration so a running turn stops
    # making provider calls / tool actions once the TUI user quits.
    should_cancel: Callable[[], bool] | None = None
    messages: list[dict] = field(default_factory=list)
    _tool_schemas: list[dict] | None = None

    def _cancelled(self) -> bool:
        return self.should_cancel is not None and self.should_cancel()

    def _emit_assistant(self, text: str) -> None:
        # Read the sink indirectly at call time so a caller (e.g. the TUI)
        # that swaps its sink after the Agent is built still receives output.
        if self.on_assistant_text:
            self.on_assistant_text(text)

    def _repair_incomplete_turn(self) -> None:
        """Keep message history provider-valid after an interruption. A trailing
        assistant message with tool_calls but missing tool results, or a
        trailing bare user message, would 400 on the next turn — drop it."""
        while self.messages:
            last = self.messages[-1]
            if last.get("role") == "assistant" and last.get("tool_calls"):
                answered = {
                    m.get("tool_call_id")
                    for m in self.messages
                    if m.get("role") == "tool"
                }
                if not all(c["id"] in answered for c in last["tool_calls"]):
                    self.messages.pop()
                    continue
            if last.get("role") in ("user", "tool"):
                self.messages.pop()
                continue
            break

    def _schemas(self) -> list[dict]:
        # Resolved once per session so the model toolset stays stable.
        if self._tool_schemas is None:
            self._tool_schemas = self.registry.schemas(self.context)
        return self._tool_schemas

    def _persist(self, role: str, content: str, tool_name: str = "") -> None:
        if self.session_store is not None and self.context.session_id:
            self.session_store.append_message(
                self.context.session_id, role, content, tool_name
            )

    def _complete(self) -> CompletionResult:
        transports = [self.transport, *self.fallback_transports]
        last_error: ProviderError | None = None
        for index, transport in enumerate(transports):
            try:
                return transport.complete(
                    self.system_prompt, self.messages, self._schemas(), self.max_tokens
                )
            except ProviderError as exc:
                last_error = exc
                if not exc.retryable or index == len(transports) - 1:
                    raise
                self.context.emit("fallback", f"provider failed ({exc}); trying fallback")
        raise last_error or ProviderError("no transports configured")

    def run_turn(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})
        self._persist("user", user_text)
        try:
            return self._run_loop()
        except BaseException:
            # KeyboardInterrupt, provider error, or cancel mid-turn: repair the
            # history so the next turn is not rejected for a dangling tool_use.
            self._repair_incomplete_turn()
            raise

    def _run_loop(self) -> str:
        for _ in range(self.max_iterations):
            if self._cancelled():
                return "[turn cancelled]"
            result = self._complete()
            assistant_msg: dict = {
                "role": "assistant",
                "content": result.text or "",
                "tool_calls": [
                    {"id": c.id or f"call_{i}", "name": c.name, "arguments": c.arguments}
                    for i, c in enumerate(result.tool_calls)
                ],
            }
            self.messages.append(assistant_msg)
            if result.text:
                safe_text = self.context.redactor.redact(result.text)
                self._persist("assistant", safe_text)
                if result.tool_calls:
                    self._emit_assistant(safe_text)

            if not result.tool_calls:
                return self.context.redactor.redact(result.text or "")

            for call in assistant_msg["tool_calls"]:
                self.context.emit("tool", f"{call['name']}({_summarize(call['arguments'])})")
                if self._cancelled():
                    # Still record a result for every pending call so history
                    # stays valid, then stop the turn.
                    output = "ERROR: turn cancelled by user"
                else:
                    output = self.registry.dispatch(
                        call["name"], call["arguments"], self.context
                    )
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "name": call["name"],
                        "content": output,
                    }
                )
                self._persist("tool", output, tool_name=call["name"])
            if self._cancelled():
                return "[turn cancelled]"

        final = (
            "Iteration budget exhausted before the task finished. "
            "Summarize progress so far and continue in a new turn."
        )
        self.messages.append({"role": "user", "content": final})
        result = self._complete()
        text = self.context.redactor.redact(result.text or final)
        self.messages.append({"role": "assistant", "content": text, "tool_calls": []})
        self._persist("assistant", text)
        return text

    def reset(self) -> None:
        self.messages.clear()
        self.context.read_files.clear()
        self.context.todos.clear()


def _summarize(arguments: dict) -> str:
    parts = []
    for key, value in list(arguments.items())[:4]:
        text = str(value).replace("\n", " ")
        if len(text) > 60:
            text = text[:60] + "…"
        parts.append(f"{key}={text!r}")
    return ", ".join(parts)


def run_subagent(parent_context: ToolContext, role: str, goal: str, budget: int) -> str:
    """Run an isolated leaf subagent. Restricted tools, no session writes,
    no delegate/execute_code/todo/memory access."""
    from pulsar_agent.prompt_builder import build_system_prompt
    from pulsar_agent.tools import build_subagent_registry
    from pulsar_agent.tools.delegate_task import ROLE_PROMPTS

    registry = build_subagent_registry(role)
    sub_context = ToolContext(
        workspace=parent_context.workspace,
        home=parent_context.home,
        config=parent_context.config,
        path_policy=parent_context.path_policy,
        approvals=parent_context.approvals,
        redactor=parent_context.redactor,
        checkpoints=parent_context.checkpoints,
        session_id=None,
        is_subagent=True,
        subagent_role=role,
        transport=parent_context.transport,
        on_tool_event=parent_context.on_tool_event,
    )
    system_prompt = build_system_prompt(
        workspace=parent_context.workspace,
        memory=None,
        skills=[],
        subagent_role_prompt=ROLE_PROMPTS[role],
    )
    transport = parent_context.transport
    if transport is None:
        return "ERROR: no transport available for delegation"
    agent = Agent(
        transport=transport,  # type: ignore[arg-type]
        registry=registry,
        context=sub_context,
        system_prompt=system_prompt,
        session_store=None,
        max_iterations=budget,
    )
    try:
        summary = agent.run_turn(f"Goal: {goal}")
    except Exception as exc:
        return f"ERROR: subagent {role} failed: {type(exc).__name__}: {exc}"
    return f"[{role} subagent report]\n{summary}"


def build_agent_runtime(
    model_id: str,
    config: dict,
    secrets: SecretStore,
) -> tuple[Transport, list[Transport], RuntimeProvider]:
    """Resolve the primary and fallback transports for a model id."""
    runtime = resolve_runtime_provider(model_id, config, secrets)
    primary = create_transport(runtime)
    fallbacks: list[Transport] = []
    for fallback_id in config.get("fallback_models") or []:
        try:
            fallback_runtime = resolve_runtime_provider(fallback_id, config, secrets)
            fallbacks.append(create_transport(fallback_runtime))
        except Exception:  # nosec B112 - unusable fallback entries are skipped
            continue
    return primary, fallbacks, runtime
