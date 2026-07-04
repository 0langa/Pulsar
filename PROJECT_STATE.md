# Pulsar — Project State

Durable project map for future agents. Keep this current: architecture, feature inventory, known limits, deferred ideas, next additions. Do not put secrets or chain-of-thought here.

## What Pulsar is

A local-first, single-user coding agent. Terminal-native. Reads/edits files, searches code, runs commands and tests under an approval system, remembers bounded project context, delegates to isolated subagents, rolls back its own changes. Provider-agnostic (bring-your-own-key). This repo is the product.

## Repository status

- Public repo: https://github.com/0langa/Pulsar (`origin`, branch `main`).
- MVP shipped (commits `68f71cf`, `ffc862f`).
- Beta expansion Bars 1-4 done (safety hardening, Docker backend, MCP client, web retrieval). Remaining candidates: TUI, repo intelligence, production hygiene. Tracked bar-by-bar in `WORK_STATUS.md`.
- `research/` is local-only reference and git-ignored. Never commit it. `START_HERE.md` and `docs/HANDOFF_AUDIT.md` are git-ignored (machine-specific paths).

## Architecture map

Package `pulsar_agent` (dist `pulsar-agent`). Python 3.11+. Runtime deps kept small: `pyyaml`, `httpx`. Optional extras gate heavier features.

```
pulsar_agent/
  home.py            PULSAR_HOME resolution + layout (default ~/.pulsar)
  config.py          config.yaml load/merge/validate; inline secrets rejected
  secrets.py         .env-only secret store; never exported to os.environ
  prompt_builder.py  stable system prompt (identity, memory, skills, project ctx)
  run_agent.py       synchronous ReAct loop; subagent runner; runtime builder
  security/
    redaction.py     Redactor: known values + credential patterns
    paths.py         PathPolicy: workspace scoping + credential-file blocking
    command_risk.py  3-tier classifier + non-overridable hardline blocklist
    approvals.py     ApprovalManager: presets + per-action-kind policy
  providers/
    router.py        provider:model resolution; builtin + custom profiles
    base.py          Transport ABC, neutral message/result types
    anthropic_transport.py / openai_transport.py / mock_transport.py
  tools/
    registry.py      ToolRegistry + ToolContext + check_fn gating + dispatch
    file_tools.py    read_file, write_file, patch, search_files
    terminal.py      terminal (backend-aware); allowlist/scrub env builders
    execute_code.py  execute_code (backend-aware)
    docker_backend.py opt-in hardened docker run for terminal/execute_code
    web_tools.py     read-only web_search + web_extract with SSRF policy
    todo.py          per-session todo list
    delegate_task.py planner/explorer/verifier subagents
  mcp/
    client.py        stdio JSON-RPC MCP client (initialize/list/call)
    manager.py       server lifecycle -> namespaced ToolSpecs (mcp_<srv>_<tool>)
  sessions/store.py  SQLite WAL + FTS5; redact-before-persist
  memory/store.py    bounded MEMORY.md / USER.md; staged writes
  skills/loader.py   builtin + PULSAR_HOME/skills discovery
  checkpoints/store.py shadow-git per workspace; reversible rollback
  cli/
    main.py          argparse entry (pulsar / python -m pulsar_agent)
    repl.py          interactive REPL + slash commands + console approver
    setup_wizard.py  first-run config
```

### Core invariants (do not break)

1. **Hardline blocklist is non-overridable.** `command_risk.classify_command` returns BLOCKED for catastrophic patterns; terminal/execute_code raise before approval; `ApprovalManager.check` independently refuses BLOCKED even if an approver says yes. No preset, allowlist, config, prompt text, or subagent path may bypass it.
2. **Secrets never leave `.env`.** Not in config, not in `os.environ`, not in checkpoints/exports. Redaction runs before console, logs, session DB, and every tool result returned to the model.
3. **Model tool schema is resolved once per session** (prompt-cache friendly). `check_fn` decides exposure.
4. **File tools are workspace-scoped** with a credential-file blocklist and a protected `PULSAR_HOME`.
5. **Checkpoints use a shadow git store**, never the user's real `.git`.
6. **Subagents are leaf** (no delegate/execute_code/todo/memory; restricted registry).
7. **Untrusted data** (project files, skills, memory, tool output, web/MCP output) cannot change rules, tools, or approvals.

## Feature inventory

(Updated as bars land — see WORK_STATUS.md for pass/fail evidence.)

- Providers: anthropic_messages, chat_completions, custom_openai_compatible, local presets (ollama, lmstudio), mock. Fallback chain on retryable errors.
- Tools: read_file, write_file, patch, search_files, terminal, execute_code, todo, delegate_task; plus config-gated web_search/web_extract and namespaced MCP tools.
- Execution backends: local (default, less isolated) and opt-in docker (cap-drop ALL, no-new-privileges, network none, workspace-only mount, resource limits, env allowlist by name, graceful degradation without a daemon).
- MCP: stdio client, disabled-by-default per-server config, allowlist-first server env, approval kind `mcp` with `allow_mcp` grant, output truncation+redaction, subagent-excluded.
- Web: GET-only search (duckduckgo no-key default, brave with user key) + extract (HTML→text, metadata header), SSRF policy with per-hop redirect checks, `allow_private_urls` opt-in, `web.enabled` kill switch.
- Sessions: SQLite+FTS5, list/search/delete.
- Memory: bounded Markdown, staged writes with approval.
- Skills: builtin + user; one builtin (`python-test-and-fix`).
- Checkpoints: shadow-git, reversible rollback.
- CLI: REPL, slash commands, setup wizard, sessions subcommands, `--once`.

## Known limits / weaknesses (do not forget)

- Local terminal backend has no OS-level sandbox; approvals + classifier are the defense. Docker backend adds isolation but is opt-in.
- Command classifier is heuristic; unknown commands default to APPROVAL. A disguised hostile command is possible — `paranoid` exists for untrusted contexts.
- Redaction cannot mask a secret it has never seen that also matches no pattern. Allowlist-first subprocess env mode reduces this for child processes.
- Windows is the primary dev platform; POSIX paths in hardline patterns are covered but real-world coverage is Windows-tested.
- Checkpoints protect workspace files only, not external state (DBs, remote, cloud).
- Web SSRF check resolves DNS separately from the request → DNS rebinding is a residual risk (documented in SECURITY.md). A fetched URL is also an outbound data channel; `paranoid` prompts per fetch.
- DuckDuckGo HTML search backend is markup-scrape best-effort; can rate-limit or silently break. Brave backend (user key) is the reliable path.
- Docker timeout is client-side with best-effort `docker kill`; a wedged daemon can still leave a container.
- MCP servers reconnect only at startup; a crashed server stays down until pulsar restarts.

## Deferred ideas (valuable, outside current pass)

- OS keychain secret backend (currently `.env` only).
- Vector/embedding session recall (currently FTS5 only).
- SSH / remote terminal backends.
- Provider plugin loading from `PULSAR_HOME/plugins`.
- MCP HTTP/SSE transport (stdio only today); MCP server auto-restart/reconnect; MCP resources and prompts (tools only today).
- Smart/LLM-assisted approval classification.
- Skill hub / install flow.
- Docker: image pre-pull/health check at startup; per-command container reuse for speed; rootless podman support.
- Web: HTML cache with ETag revalidation; per-domain rate limiting; resolve-then-pin connections to close the DNS-rebinding gap; `/web` slash command for manual fetches.
- REPL `/mcp` slash command showing server status + discovered tools.

## Recommended next additions (after this pass)

- Streaming token output in CLL/TUI.
- Cost/token budget accounting surfaced per session.
- Richer diff rendering in TUI.
- Config schema versioning + migration harness.
