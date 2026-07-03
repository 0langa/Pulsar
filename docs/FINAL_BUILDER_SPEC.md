# Final Builder Spec

## Mission

Build Pulsar: a coding-first, local-first autonomous agent for repository work.

It should feel closer to a terminal-native coding agent than a general chat assistant. It reads and edits files, searches code, runs commands and tests, remembers bounded project context, delegates narrow subtasks, and rolls back unsafe changes.

## Architecture

Use a narrow-core / wide-edges architecture:

- `pulsar_agent/run_agent.py`: synchronous ReAct-style agent loop.
- `pulsar_agent/prompt_builder.py`: stable prompt assembly from identity, project context, memory, skills, and user turn.
- `pulsar_agent/providers/`: provider profiles and transport adapters.
- `pulsar_agent/tools/registry.py`: central self-registering tool registry with `check_fn` gating.
- `pulsar_agent/tools/`: core tools.
- `pulsar_agent/sessions/store.py`: SQLite+FTS5 session persistence.
- `pulsar_agent/memory/`: bounded Markdown memory snapshots.
- `pulsar_agent/skills/`: local skill discovery and reading.
- `pulsar_agent/security/`: approvals, redaction, path checks, command risk classification.
- `pulsar_agent/checkpoints/`: shadow-git checkpoints and rollback.
- `pulsar_agent/cli/`: CLI entry point and slash commands.

Do not build gateway, dashboard, desktop app, cloud scheduler, broad browser automation, or marketplace architecture in the MVP.

## Required CLI

Support:

- `python -m pulsar_agent`
- `pulsar`
- `pulsar setup`
- `pulsar model <provider>:<model>`
- `pulsar sessions list`
- `pulsar sessions delete <id>`

Interactive slash commands:

- `/model`
- `/tools`
- `/memory`
- `/skills`
- `/checkpoint`
- `/rollback`
- `/reset`
- `/new`
- `/help`
- `/quit`

The CLI should show active provider/model, approval profile, session id, and command/tool progress.

## Provider Router

Use `provider:model` identifiers.

Required API modes:

- `anthropic_messages`
- `chat_completions`
- `custom_openai_compatible`

Required provider families:

- Anthropic through `ANTHROPIC_API_KEY`
- OpenAI-compatible through `OPENAI_API_KEY` or configured env var
- local/custom endpoints through `custom_providers` in config

Custom providers must define:

- `name`
- `api_mode`
- `base_url`
- `api_key_env_var`
- optional `default_model`

Inline `api_key` values in config are forbidden.

## State Layout

All mutable state goes under `PULSAR_HOME`, default `~/.pulsar`.

Suggested layout:

```text
PULSAR_HOME/
  config.yaml
  .env
  state.db
  memories/
    MEMORY.md
    USER.md
  skills/
  checkpoints/
  logs/
```

Use helpers such as `get_pulsar_home()` and `display_pulsar_home()`. Do not hardcode `~/.pulsar` in implementation paths except as the default value.

## Core Tools

The always-available model tool surface is capped at eight:

1. `read_file`
2. `write_file`
3. `patch`
4. `search_files`
5. `terminal`
6. `execute_code`
7. `todo`
8. `delegate_task`

Implementation notes:

- File tools are workspace-scoped.
- The agent must read a file before editing it unless creating a new file.
- Prefer `patch` over shell text editing.
- `terminal` runs in the workspace with timeout, output limit, redaction, and approval gates.
- `execute_code` runs Python in a scrubbed child process and cannot recursively call itself, MCP tools, or `delegate_task`.
- `delegate_task` spawns isolated planner/explorer/verifier tasks with restricted tools and explicit iteration budget.
- `memory` and `session_search` may be loop-level actions, but do not inflate the always-on model tool schema unless necessary.

## Memory And Skills

Memory:

- `MEMORY.md`: project/agent facts.
- `USER.md`: user preferences.
- Load as bounded frozen snapshots at session start.
- Stage memory writes for user approval.
- Scan memory writes for secrets and prompt-injection text.

Skills:

- A skill is a directory containing `SKILL.md`.
- Frontmatter should use project-neutral or `pulsar` names, not `metadata.hermes`.
- Built-in skills live under package data.
- User skills live under `PULSAR_HOME/skills`.
- The agent reads skill instructions before acting when a skill matches the task.

## Checkpoints

Create a checkpoint before:

- `write_file`
- `patch`
- destructive terminal commands

Use a shadow-git store under `PULSAR_HOME/checkpoints/`. Do not pollute the user's project git history. `/rollback` restores the last checkpoint and records the rollback in the session.

## Optional Should-Have Features

Implement only after the required MVP is passing tests:

- minimal stdio MCP client with no enabled servers by default
- Docker backend for `terminal` and `execute_code`
- full-screen TUI behind `pulsar --tui`
- read-only `web_search` and `web_extract`

If optional work threatens completion, defer it and document it in `WORK_STATUS.md`.
