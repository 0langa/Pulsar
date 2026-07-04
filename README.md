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

The core model tool surface is capped at eight tools: `read_file`, `write_file`, `patch`, `search_files`, `terminal`, `execute_code`, `todo`, `delegate_task`. Two read-only web tools (`web_search`, `web_extract`) and any MCP tools you configure are added on top, each behind its own gate.

- File tools are workspace-scoped; credential files (`.env`, keys, `auth.json`, `.git/credentials`, …) are blocked in code.
- Editing requires reading the file first; `patch` does exact-block replacement.
- `terminal` classifies every command: safe commands run, mutating commands need approval, catastrophic commands (root deletes, disk formatting, fork bombs, raw device writes, …) are refused in every mode.
- `execute_code` runs Python in a child process with a scrubbed environment and no channel back into the agent.
- `delegate_task` spawns an isolated planner/explorer/verifier subagent with restricted tools and a bounded iteration budget. Subagents never see web or MCP tools.

## Execution backends

`terminal` and `execute_code` run on a selectable backend:

```yaml
terminal:
  backend: local        # default; or "docker"
docker:
  image: python:3.11-slim
  network: none         # container network mode; no network by default
  workspace_mount: rw   # or "ro"
  timeout_seconds: 120
  output_limit_bytes: 20000
  env_allowlist: []     # variable NAMES forwarded into the container
  memory: 512m
  cpus: "1.0"
  pids_limit: 256
  read_only_rootfs: false
```

The Docker backend is opt-in isolation: containers run with `--rm`, no privileged mode, `--cap-drop ALL`, `--security-opt no-new-privileges`, memory/cpu/pids limits, network disabled unless configured, and only the workspace mounted. Environment forwarding is by name (`-e NAME`) so values never appear in the docker command line. If Docker is missing or the daemon is down, commands fail with guidance instead of a stack trace.

The `local` backend remains the default and is **less isolated** — no OS-level sandbox, only the risk classifier, approvals, and env allowlisting. Approval semantics are identical on both backends, and the hardline blocklist applies before any backend runs.

## MCP servers (opt-in)

Pulsar includes a minimal stdio MCP client. No servers are enabled by default; each entry must set `enabled: true` explicitly:

```yaml
mcp:
  servers:
    - name: docs
      command: npx
      args: ["-y", "@example/mcp-docs-server"]
      cwd: ~/tools           # optional
      enabled: true          # required; default is off
      allowed_tools: [search_docs]   # optional; default all discovered
      env_passthrough: []    # variable NAMES the server process may inherit
      startup_timeout: 20
```

Discovered tools appear namespaced as `mcp_<server>_<tool>`. Server subprocesses get an allowlist-first environment (baseline + `env_passthrough` names only). Every MCP tool call goes through the approval pipeline (auto-approval only via the `security.autonomy.allow_mcp` grant under `trusted-local`), and all output is truncated and redacted before reaching the console, the session DB, or the model. Servers that are disabled, missing, or crash on startup simply do not appear in the toolset.

## Web retrieval (read-only)

`web_search` and `web_extract` are for everyday coding-agent lookups: docs, package/API references, official project pages, release notes.

- Strictly GET-only. No POST, no uploads, no cookies, no credentials, no browser automation.
- SSRF protection on by default: localhost, private ranges, link-local/cloud-metadata addresses, and `file:` URLs are blocked, and redirects are re-checked hop by hop. `web.allow_private_urls: true` is the explicit opt-in for internal URLs.
- Search uses DuckDuckGo's no-key HTML endpoint by default (best-effort — it can rate-limit or change markup). For reliability, set `web.search_backend: brave` and put `BRAVE_API_KEY=<key>` (or the name configured in `web.search_results_api_env_var`) in `PULSAR_HOME/.env`.
- Outputs carry URL, status, content type, title, and truncation metadata; everything is redacted before display, persistence, and the model.
- Disable entirely with `web.enabled: false`. The `paranoid` preset prompts before every fetch.

## Approval presets

| Preset | Behavior |
|---|---|
| `paranoid` | auto-approve only workspace file reads; ask for every terminal command (even read-only) and every mutating action |
| `review` (default) | auto-approve reads and SAFE (read-only/test) terminal commands; ask for writes, patches, `execute_code`, memory writes, and any mutating/risky command |
| `trusted-local` | same low-risk auto set as `review`, **plus** it honors explicit, per-capability autonomy grants and the exact-match command allowlist |

`trusted-local` by itself does **not** auto-approve file writes, `execute_code`, memory writes, dependency installs, networked commands, or destructive commands. Higher autonomy is opt-in and capability-scoped in `config.yaml`:

```yaml
approval_preset: trusted-local
security:
  autonomy:
    allow_writes: true          # auto-approve workspace file writes/patches
    allow_execute_code: false   # still ask before running Python
    allow_memory_writes: false  # still stage memory writes for review
  command_allowlist:            # exact commands that skip the prompt
    - "python -m pytest -q"
```

Grants take effect **only** under `trusted-local`; `review`/`paranoid` ignore them. No grant, allowlist, preset, prompt text, or subagent path can bypass the hardline blocklist. Terminal approval prompts show the command, cwd, risk tier, reason, and whether a checkpoint will be taken.

Subprocess environment handling is allowlist-first by default (`terminal.env_mode: allowlist`): child processes receive only a fixed baseline plus variables you name in `terminal.env_passthrough`, so a secret with a bland variable name is not inherited. `scrub` mode (drop only secret-named variables) is available but weaker.

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
