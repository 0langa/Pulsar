# Pulsar

Pulsar is a local-first, coding-first autonomous agent for repository work. It runs in your terminal, reads and edits files, searches code, runs commands and tests under an approval system, remembers bounded project context, delegates narrow subtasks to isolated subagents, and can roll back its own changes.

It is deliberately not a general assistant: no messaging gateways, no browser automation, no dashboards, no marketplace. Just a small, safety-bound coding loop.

## Install

Requires Python 3.11+.

```bash
pip install -e .
# or with dev tools:
pip install -e ".[dev]"
```

## Quick start

```bash
pulsar setup           # choose provider/model, store your API key
pulsar                 # interactive session in the current directory
python -m pulsar_agent # same thing, module form
```

`pulsar setup` writes behavior to `PULSAR_HOME/config.yaml` and secrets to `PULSAR_HOME/.env` only. `PULSAR_HOME` defaults to `~/.pulsar`.

Non-interactive single turn:

```bash
pulsar --once "explain the failing test in tests/test_auth.py"
```

## Providers

Models are addressed as `provider:model`:

```bash
pulsar model anthropic:claude-sonnet-5
pulsar model openai:gpt-4.1
pulsar model ollama:llama3          # local, no key needed
```

Builtin families: `anthropic` (Messages API), `openai`, `openrouter`, `ollama`, `lmstudio` (chat completions). Any other OpenAI-compatible endpoint goes in `config.yaml`:

```yaml
custom_providers:
  - name: myserver
    api_mode: custom_openai_compatible
    base_url: http://localhost:8080/v1
    api_key_env_var: MYSERVER_KEY   # value lives in PULSAR_HOME/.env
```

Inline keys in config are rejected at load. Optional `fallback_models` are tried once per turn on rate-limit/server/auth errors.

## Tools

The model tool surface is capped at eight tools: `read_file`, `write_file`, `patch`, `search_files`, `terminal`, `execute_code`, `todo`, `delegate_task`.

- File tools are workspace-scoped; credential files (`.env`, keys, `auth.json`, `.git/credentials`, …) are blocked in code.
- Editing requires reading the file first; `patch` does exact-block replacement.
- `terminal` classifies every command: safe commands run, mutating commands need approval, catastrophic commands (root deletes, disk formatting, fork bombs, raw device writes, …) are refused in every mode.
- `execute_code` runs Python in a child process with a scrubbed environment and no channel back into the agent.
- `delegate_task` spawns an isolated planner/explorer/verifier subagent with restricted tools and a bounded iteration budget.

## Approval presets

| Preset | Behavior |
|---|---|
| `paranoid` | approve every terminal command and every write |
| `review` (default) | reads auto-approved; writes and risky commands ask |
| `trusted-local` | low-risk local operations auto-approved; destructive commands still ask |

The hardline blocklist applies in all presets and cannot be overridden by config, allowlists, prompts, or project files.

## Sessions, memory, skills, checkpoints

- Sessions persist to SQLite (`PULSAR_HOME/state.db`) with FTS5 search: `pulsar sessions list|search|delete`.
- Memory is two bounded Markdown files (`memories/MEMORY.md`, `memories/USER.md`) loaded as a frozen snapshot; writes are size-bounded, secret-scanned, injection-scanned, and staged for approval.
- Skills are directories with a `SKILL.md`; builtin skills ship with the package, user skills go in `PULSAR_HOME/skills/`.
- Checkpoints snapshot the workspace into a shadow git store under `PULSAR_HOME/checkpoints/` before writes, patches, and destructive commands — your project's own git history is never touched. `/rollback` restores, and rollbacks are themselves reversible.

## Slash commands

`/model`, `/tools`, `/memory`, `/skills`, `/checkpoint`, `/rollback`, `/reset`, `/new`, `/help`, `/quit`

## Security

Secrets live only in `PULSAR_HOME/.env`. Redaction runs before console output, logs, session persistence, and every tool result returned to the model. See [SECURITY.md](SECURITY.md).

## Development

```bash
pip install -e ".[dev]"
python -m pytest
```

## License

MIT
