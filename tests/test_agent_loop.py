from __future__ import annotations

from pulsar_agent.providers.base import CompletionResult, ProviderError, ToolCallRequest, Transport
from pulsar_agent.providers.mock_transport import MockTransport
from pulsar_agent.providers.router import BUILTIN_PROFILES, RuntimeProvider
from pulsar_agent.run_agent import Agent
from pulsar_agent.security.redaction import Redactor
from pulsar_agent.sessions.store import SessionStore
from pulsar_agent.tools import build_core_registry
from tests.conftest import make_context

MOCK_RUNTIME = RuntimeProvider(profile=BUILTIN_PROFILES["mock"], model="echo", api_key=None)


def make_agent(context, script, session_store=None, max_iterations=10, fallbacks=None):
    context.transport = MockTransport(MOCK_RUNTIME, script=script)
    return Agent(
        transport=context.transport,
        registry=build_core_registry(),
        context=context,
        system_prompt="test system prompt",
        session_store=session_store,
        max_iterations=max_iterations,
        fallback_transports=fallbacks or [],
    )


def test_simple_text_turn(context):
    agent = make_agent(context, script=[{"text": "hello user"}])
    assert agent.run_turn("hi") == "hello user"


def test_tool_call_loop(context, workspace):
    (workspace / "app.py").write_text("value = 7\n")
    agent = make_agent(
        context,
        script=[
            {"tool_calls": [{"name": "read_file", "arguments": {"path": "app.py"}}]},
            {"text": "the value is 7"},
        ],
    )
    result = agent.run_turn("what is value?")
    assert result == "the value is 7"
    tool_message = next(m for m in agent.messages if m["role"] == "tool")
    assert "value = 7" in tool_message["content"]


def test_iteration_budget_graceful(context):
    script = [
        {"tool_calls": [{"name": "todo", "arguments": {"action": "list"}}]}
        for _ in range(5)
    ] + [{"text": "stopping now"}]
    agent = make_agent(context, script=script, max_iterations=3)
    result = agent.run_turn("loop forever")
    assert result  # graceful final message, no exception


def test_session_persistence_via_agent(context, tmp_path):
    store = SessionStore(tmp_path / "s.db", Redactor())
    context.session_id = store.create_session()
    agent = make_agent(
        context,
        script=[
            {"tool_calls": [{"name": "todo", "arguments": {"action": "list"}}]},
            {"text": "done"},
        ],
        session_store=store,
    )
    agent.run_turn("track this")
    roles = [m["role"] for m in store.get_messages(context.session_id)]
    assert roles == ["user", "tool", "assistant"]
    store.close()


def test_fallback_transport_used(context):
    class FailingTransport(Transport):
        def complete(self, system, messages, tools, max_tokens):
            raise ProviderError("HTTP 429", status_code=429, retryable=True)

    fallback = MockTransport(MOCK_RUNTIME, script=[{"text": "fallback replied"}])
    agent = Agent(
        transport=FailingTransport(),
        registry=build_core_registry(),
        context=context,
        system_prompt="s",
        fallback_transports=[fallback],
    )
    assert agent.run_turn("hi") == "fallback replied"


def test_assistant_output_redacted(workspace, home, config):
    secret = "sk-test-agent-reply-leak-123456789"
    context = make_context(workspace, home, config, redactor=Redactor([secret]))
    agent = make_agent(context, script=[{"text": f"key: {secret}"}])
    result = agent.run_turn("what is the key")
    assert secret not in result


def test_reset_clears_state(context):
    agent = make_agent(context, script=[{"text": "a"}])
    agent.run_turn("x")
    context.read_files.add("marker")
    agent.reset()
    assert agent.messages == []
    assert context.read_files == set()


def test_delegate_task_runs_subagent(context, workspace):
    (workspace / "target.py").write_text("answer = 99\n")
    # Parent script: delegate -> final. Subagent shares the parent transport,
    # so its steps interleave on the same script after the delegate call.
    script = [
        {"tool_calls": [{"name": "delegate_task",
                         "arguments": {"role": "explorer", "goal": "find answer"}}]},
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "target.py"}}]},
        {"text": "found answer = 99 in target.py"},
        {"text": "delegation complete"},
    ]
    agent = make_agent(context, script=script)
    result = agent.run_turn("explore")
    assert result == "delegation complete"
    tool_message = next(m for m in agent.messages if m["role"] == "tool")
    assert "explorer subagent report" in tool_message["content"]
    assert "found answer = 99" in tool_message["content"]


def test_subagent_cannot_delegate(context):
    from pulsar_agent.run_agent import run_subagent

    context.transport = MockTransport(
        MOCK_RUNTIME,
        script=[
            {"tool_calls": [{"name": "delegate_task",
                             "arguments": {"role": "explorer", "goal": "nested"}}]},
            {"text": "gave up on nesting"},
        ],
    )
    report = run_subagent(context, role="explorer", goal="try to nest", budget=3)
    assert "gave up on nesting" in report


def test_subagent_write_tools_unavailable(context, workspace):
    from pulsar_agent.run_agent import run_subagent

    context.transport = MockTransport(
        MOCK_RUNTIME,
        script=[
            {"tool_calls": [{"name": "write_file",
                             "arguments": {"path": "evil.txt", "content": "x"}}]},
            {"text": "could not write"},
        ],
    )
    report = run_subagent(context, role="planner", goal="write something", budget=3)
    assert "could not write" in report
    assert not (workspace / "evil.txt").exists()
