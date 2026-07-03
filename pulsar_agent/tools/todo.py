"""todo: lightweight per-session task list for the agent's own planning."""

from __future__ import annotations

from pulsar_agent.tools.registry import ToolContext, ToolSpec

VALID_STATUSES = ("pending", "in_progress", "done")


def _render(todos: list[dict]) -> str:
    if not todos:
        return "(todo list empty)"
    marks = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]"}
    return "\n".join(
        f"{i + 1}. {marks.get(item['status'], '[ ]')} {item['text']}"
        for i, item in enumerate(todos)
    )


def todo_handler(args: dict, context: ToolContext) -> str:
    action = str(args.get("action", "list"))
    todos = context.todos
    if action == "list":
        return _render(todos)
    if action == "set":
        items = args.get("items") or []
        new_list: list[dict] = []
        for item in items:
            if isinstance(item, str):
                new_list.append({"text": item, "status": "pending"})
            elif isinstance(item, dict) and item.get("text"):
                status = item.get("status", "pending")
                if status not in VALID_STATUSES:
                    status = "pending"
                new_list.append({"text": str(item["text"]), "status": status})
        todos.clear()
        todos.extend(new_list)
        return _render(todos)
    if action == "update":
        index = int(args.get("index", 0) or 0)
        if not 1 <= index <= len(todos):
            return f"ERROR: index {index} out of range (1..{len(todos)})"
        status = str(args.get("status", ""))
        if status not in VALID_STATUSES:
            return f"ERROR: status must be one of {VALID_STATUSES}"
        todos[index - 1]["status"] = status
        return _render(todos)
    return "ERROR: action must be list, set, or update"


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="todo",
            description=(
                "Manage the working todo list for this session. Actions: "
                "list; set (items: array of strings or {text,status}); "
                "update (index: 1-based, status: pending|in_progress|done)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "set", "update"]},
                    "items": {"type": "array", "items": {}},
                    "index": {"type": "integer"},
                    "status": {"type": "string", "enum": list(VALID_STATUSES)},
                },
                "required": ["action"],
            },
            handler=todo_handler,
            check_fn=lambda ctx: not ctx.is_subagent,
        ),
    ]
