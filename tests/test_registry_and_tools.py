from __future__ import annotations

import pytest

from pulsar_agent.tools import (
    CORE_TOOL_NAMES,
    build_core_registry,
    build_subagent_registry,
)
from pulsar_agent.tools.registry import ToolRegistry, ToolSpec
from tests.conftest import make_context


def test_core_registry_is_capped_at_eight(context):
    registry = build_core_registry()
    assert sorted(registry.names()) == sorted(CORE_TOOL_NAMES)
    assert len(registry.names()) == 8
    enabled = {spec.name for spec in registry.enabled(context)}
    assert enabled == set(CORE_TOOL_NAMES)


def test_check_fn_filters_subagent_tools(workspace, home, config):
    registry = build_core_registry()
    sub_context = make_context(workspace, home, config, is_subagent=True)
    enabled = {spec.name for spec in registry.enabled(sub_context)}
    assert "delegate_task" not in enabled
    assert "execute_code" not in enabled
    assert "todo" not in enabled


def test_duplicate_registration_rejected():
    registry = ToolRegistry()
    spec = ToolSpec(
        name="x", description="d", parameters={"type": "object"},
        handler=lambda args, ctx: "ok",
    )
    registry.register(spec)
    with pytest.raises(ValueError):
        registry.register(spec)


def test_dispatch_unknown_tool(context):
    registry = build_core_registry()
    assert "unknown or disabled" in registry.dispatch("nope", {}, context)


def test_dispatch_redacts_results(workspace, home, config):
    from pulsar_agent.security.redaction import Redactor

    secret = "sk-test-leakyvalue1234567890abc"
    context = make_context(workspace, home, config, redactor=Redactor([secret]))
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="leak", description="d", parameters={"type": "object"},
            handler=lambda args, ctx: f"key is {secret}",
        )
    )
    assert secret not in registry.dispatch("leak", {}, context)


def test_read_write_patch_cycle(context, workspace):
    registry = build_core_registry()
    assert "wrote" in registry.dispatch(
        "write_file", {"path": "demo.py", "content": "x = 1\ny = 2\n"}, context
    )
    read_result = registry.dispatch("read_file", {"path": "demo.py"}, context)
    assert "x = 1" in read_result
    patch_result = registry.dispatch(
        "patch", {"path": "demo.py", "old_text": "x = 1", "new_text": "x = 42"}, context
    )
    assert "patched" in patch_result
    assert (workspace / "demo.py").read_text() == "x = 42\ny = 2\n"


def test_write_existing_without_read_refused(context, workspace):
    (workspace / "exists.txt").write_text("original")
    registry = build_core_registry()
    result = registry.dispatch(
        "write_file", {"path": "exists.txt", "content": "clobber"}, context
    )
    assert "ERROR" in result and "read" in result
    assert (workspace / "exists.txt").read_text() == "original"


def test_patch_requires_prior_read(context, workspace):
    (workspace / "p.txt").write_text("hello world")
    registry = build_core_registry()
    result = registry.dispatch(
        "patch", {"path": "p.txt", "old_text": "hello", "new_text": "bye"}, context
    )
    assert "ERROR" in result


def test_patch_ambiguous_match_refused(context, workspace):
    registry = build_core_registry()
    registry.dispatch("write_file", {"path": "a.txt", "content": "dup\ndup\n"}, context)
    result = registry.dispatch(
        "patch", {"path": "a.txt", "old_text": "dup", "new_text": "x"}, context
    )
    assert "matches 2 times" in result


def test_read_file_pagination(context, workspace):
    (workspace / "big.txt").write_text("\n".join(f"line{i}" for i in range(1, 101)))
    registry = build_core_registry()
    out = registry.dispatch("read_file", {"path": "big.txt", "offset": 50, "limit": 2}, context)
    assert "line50" in out and "line51" in out and "line52" not in out


def test_read_file_refuses_binary(context, workspace):
    (workspace / "bin.dat").write_bytes(b"abc\x00def")
    registry = build_core_registry()
    assert "binary" in registry.dispatch("read_file", {"path": "bin.dat"}, context)


def test_search_files(context, workspace):
    (workspace / "one.py").write_text("def alpha():\n    pass\n")
    (workspace / "two.py").write_text("def beta():\n    return alpha()\n")
    registry = build_core_registry()
    out = registry.dispatch("search_files", {"pattern": r"alpha\(", "glob": "*.py"}, context)
    assert "one.py:1" in out and "two.py:2" in out


def test_file_tools_blocked_outside_workspace(context):
    registry = build_core_registry()
    result = registry.dispatch("read_file", {"path": "../../etc/passwd"}, context)
    assert "BLOCKED" in result or "ERROR" in result


def test_sensitive_file_read_blocked(context, workspace):
    (workspace / ".env").write_text("KEY=value")
    registry = build_core_registry()
    assert "BLOCKED" in registry.dispatch("read_file", {"path": ".env"}, context)


def test_todo_tool(context):
    registry = build_core_registry()
    out = registry.dispatch(
        "todo", {"action": "set", "items": ["first", {"text": "second", "status": "in_progress"}]},
        context,
    )
    assert "1. [ ] first" in out and "2. [~] second" in out
    out = registry.dispatch("todo", {"action": "update", "index": 1, "status": "done"}, context)
    assert "1. [x] first" in out


def test_subagent_registry_role_restrictions():
    planner = build_subagent_registry("planner")
    verifier = build_subagent_registry("verifier")
    assert set(planner.names()) == {"read_file", "search_files"}
    assert set(verifier.names()) == {"read_file", "search_files", "terminal"}
    for registry in (planner, verifier):
        for banned in ("delegate_task", "execute_code", "todo", "write_file", "patch"):
            assert banned not in registry.names()
