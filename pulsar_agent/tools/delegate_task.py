"""delegate_task: spawn an isolated planner/explorer/verifier subagent.

Subagents get a restricted tool registry (read-only for planner/explorer;
verifier may also run terminal commands through the same approval pipeline),
a bounded iteration budget, and no access to delegate_task, execute_code,
todo, memory, or session state. They return a text summary only.
"""

from __future__ import annotations

from pulsar_agent.tools.registry import ToolContext, ToolSpec

ROLES = ("planner", "explorer", "verifier")
ROLE_TOOLS = {
    "planner": ("read_file", "search_files"),
    "explorer": ("read_file", "search_files"),
    "verifier": ("read_file", "search_files", "terminal"),
}
MAX_BUDGET = 15

ROLE_PROMPTS = {
    "planner": (
        "You are a planning subagent. Explore only as needed and produce a "
        "concise, numbered implementation plan for the goal. Do not modify anything."
    ),
    "explorer": (
        "You are an exploration subagent. Locate the code, files, and facts "
        "relevant to the goal and report findings with file paths. Do not modify anything."
    ),
    "verifier": (
        "You are a verification subagent. Check whether the goal is satisfied, "
        "running read-only inspection and non-mutating test commands as needed. "
        "Report pass/fail with evidence."
    ),
}


def delegate_task_handler(args: dict, context: ToolContext) -> str:
    if context.is_subagent:
        return "ERROR: subagents cannot delegate further"
    role = str(args.get("role", "explorer"))
    if role not in ROLES:
        return f"ERROR: role must be one of {ROLES}"
    goal = str(args.get("goal", "")).strip()
    if not goal:
        return "ERROR: goal is required"
    budget = int(args.get("max_iterations", 0) or 0)
    default_budget = int(context.config.get("delegate", {}).get("max_iterations", 8))
    budget = min(budget or default_budget, MAX_BUDGET)

    from pulsar_agent.run_agent import run_subagent

    context.emit("delegate", f"{role}: {goal[:80]}")
    return run_subagent(parent_context=context, role=role, goal=goal, budget=budget)


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="delegate_task",
            description=(
                "Delegate a narrow subtask to an isolated subagent. Roles: "
                "planner (produce a plan), explorer (find code/facts), "
                "verifier (check results, may run non-mutating commands). "
                "Returns a text summary."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": list(ROLES)},
                    "goal": {"type": "string"},
                    "max_iterations": {"type": "integer", "description": f"Budget, max {MAX_BUDGET}"},
                },
                "required": ["role", "goal"],
            },
            handler=delegate_task_handler,
            check_fn=lambda ctx: (
                not ctx.is_subagent
                and bool(ctx.config.get("delegate", {}).get("enabled", True))
            ),
        ),
    ]
