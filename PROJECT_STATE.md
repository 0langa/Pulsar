# Pulsar — Project State

Durable project map for future agents. Keep this current: architecture, feature inventory, known limits, deferred ideas, next additions. Do not put secrets or chain-of-thought here.

## What Pulsar is

A local-first, single-user coding agent. Terminal-native. Reads/edits files, searches code, runs commands and tests under an approval system, remembers bounded project context, delegates to isolated subagents, rolls back its own changes. Provider-agnostic (bring-your-own-key). This repo is the product.

## Repository status

- Public repo: https://github.com/0langa/Pulsar (`origin`, branch `main`).
- MVP shipped (commits `68f71cf`, `ffc862f`).
- Beta expansion Bars 1-8 done (safety hardening, Docker backend, MCP client, web retrieval, TUI, repo intelligence, production hygiene, e2e verification + self-audit). Tracked bar-by-bar in `WORK_STATUS.md`.
- Post-beta pass 1 done: CI workflow pushed and green on GitHub; docker network/image validation + host-network startup warning; `pulsar model` resolves before persisting; TUI stale approval modal auto-dismisses; per-session token/cost accounting (`/usage`).
- Pushing `.github/workflows/` changes needs the keyring `gh` credential (`workflow` scope): `env -u GITHUB_TOKEN git push`.
- `research/` is local-only reference and git-ignored. Never commit it. `START_HERE.md` and `docs/HANDOFF_AUDIT.md` are git-ignored (machine-specific paths).

## Architecture map

Package `pulsar_agent` (dist `pulsar-agent`). Python 3.11+. Runtime deps kept small: `pyyaml`, `httpx`. Optional extras gate heavier features.

```
pulsar_agent/
  home.py            PULSAR_HOME resolution + layout (default ~/.pulsar)
  config.py          config.yaml load/merge/validate; inline secrets rejected;
                     config_warnings() advisories (e.g. docker host network)
  usage.py           UsageTracker: per-run token/cost accounting (/usage)
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
    cancellable.py   cancel-aware subprocess runner (process-tree kill)
    docker_backend.py opt-in hardened docker run for terminal/execute_code
    web_tools.py     read-only web_search + web_extract with SSRF policy
    todo.py          per-session todo list
    delegate_task.py planner/explorer/verifier subagents
  mcp/
    client.py        stdio JSON-RPC MCP client (initialize/list/call)
    manager.py       server lifecycle -> namespaced ToolSpecs (mcp_<srv>_<tool>)
  intel.py           project map, test discovery, git summary/diffstat
  cli/tui.py         opt-in Textual TUI (controller + thin app shell)
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

- Providers: anthropic_messages, chat_completions, custom_openai_compatible, local presets (ollama, lmstudio), mock. Fallback chain on retryable errors. SSE streaming (default on, `streaming: false` to disable) with automatic non-streaming fallback when a server rejects it; mock transport streams two deterministic chunks.
- Tools: read_file, write_file, patch, search_files, terminal, execute_code, todo, delegate_task; plus config-gated web_search/web_extract and namespaced MCP tools.
- Execution backends: local (default, less isolated) and opt-in docker (cap-drop ALL, no-new-privileges, network none, workspace-only mount, resource limits, env allowlist by name, graceful degradation without a daemon).
- MCP: stdio client, disabled-by-default per-server config, allowlist-first server env, approval kind `mcp` with `allow_mcp` grant, output truncation+redaction, subagent-excluded.
- Web: GET-only search (duckduckgo no-key default, brave with user key) + extract (HTML→text, metadata header), SSRF policy with per-hop redirect checks, `allow_private_urls` opt-in, `web.enabled` kill switch.
- Sessions: SQLite+FTS5 (thread-safe: locked connection), list/search/delete.
- Memory: bounded Markdown, staged writes with approval.
- Skills: builtin + user; one builtin (`python-test-and-fix`).
- Checkpoints: shadow-git, reversible rollback.
- CLI: REPL, slash commands (incl. `/map`, `/usage`), setup wizard, sessions subcommands, `--once`; tool progress lines with per-turn counter + elapsed time; recovery hints on common failures.
- TUI: `pulsar --tui` (textual extra), status bar (incl. token totals), transcript, composer, modal approvals from worker thread (stale modals auto-dismiss), graceful fallback without the dependency.
- Usage accounting: one `UsageTracker` per run shared by main agent and subagents; `/usage` shows requests, tokens (total + last turn), cache counters, and cost when `pricing.*_per_mtok` is configured.
- Repo intelligence (`intel.py`): project map (languages, tooling, package managers, key files, CI), test-command inference + targeted-test helper, git summary + diffstat; injected into system prompt as untrusted data.
- Hygiene: ruff + mypy + bandit + pip-audit configured and passing; GitHub Actions CI (tests on Linux/Windows py3.11-3.12, lint, type check, security scans); coverage via pytest-cov.

## Known limits / weaknesses (do not forget)

- Local terminal backend has no OS-level sandbox; approvals + classifier are the defense. Docker backend adds isolation but is opt-in.
- Command classifier is heuristic; unknown commands default to APPROVAL. A disguised hostile command is possible — `paranoid` exists for untrusted contexts.
- Redaction cannot mask a secret it has never seen that also matches no pattern. Allowlist-first subprocess env mode reduces this for child processes.
- Windows is the primary dev platform; POSIX paths in hardline patterns are covered but real-world coverage is Windows-tested.
- Checkpoints protect workspace files only, not external state (DBs, remote, cloud).
- Web fetches resolve-then-pin (post-beta pass 4): connections go to the vetted IP with Host/SNI kept, closing the DNS-rebinding gap. A fetched URL is still an outbound data channel; `paranoid` prompts per fetch.
- DuckDuckGo HTML search backend is markup-scrape best-effort; can rate-limit or silently break. Brave backend (user key) is the reliable path.
- Docker timeout is client-side with best-effort `docker kill`; a wedged daemon can still leave a container.
- MCP auto-restart is capped at 3 per server per run; a server flapping past that stays down until pulsar restarts. A restarted server is assumed to offer the same tools (the model schema is fixed per session); a tool that vanished after restart errors per call.

## Deferred ideas (valuable, outside current pass)

- OS keychain secret backend (currently `.env` only).
- Vector/embedding session recall (currently FTS5 only).
- SSH / remote terminal backends.
- ~~Provider plugin loading~~ — done (post-beta pass 4): declarative profiles in `PULSAR_HOME/providers/*.yaml` (custom_providers schema, validated incl. inline-secret rejection, broken files skip with a startup warning, config.yaml entries win collisions, never persisted into config.yaml). Deliberately NOT code plugins — nothing executes from PULSAR_HOME.
- MCP HTTP/SSE transport (stdio only today); MCP resources and prompts (tools only today). (Auto-restart + `/mcp` status landed in post-beta pass 3.)
- Smart/LLM-assisted approval classification.
- Skill hub / install flow.
- Docker: image pre-pull/health check at startup; per-command container reuse for speed; rootless podman support.
- Web: HTML cache with ETag revalidation; per-domain rate limiting. (Resolve-then-pin landed in post-beta pass 4; `/web` slash command also pass 4.)
- ~~REPL `/mcp` slash command~~ — done (post-beta pass 3; REPL + TUI).

## Deferred audit findings (P2/P3 accepted for this beta)

Recorded from the Bar 1-8 release self-audit. All P0/P1 were fixed in-pass; the
items below are accepted with rationale and a next step.

- ~~P3 — Docker `network`/`image` config not validated~~ **Fixed in post-beta pass 1**: `network` restricted to `{none, bridge, host}`, `image` must be a non-empty string, startup advisory via `config_warnings()` when the docker backend uses `host`.
- ~~P3 — `pulsar model <id>` validates format only~~ **Fixed in post-beta pass 1**: the subcommand resolves the provider (profile + key presence) before persisting.
- ~~P3 — TUI approval modal cosmetic-stale after timeout~~ **Fixed in post-beta pass 1**: the modal denies-and-dismisses from its own `set_timer` (UI thread, same idempotent `_finish` path as a click); the worker keeps only a backstop wait. Do NOT pop a textual screen cross-thread — it deadlocks the screen-close await chain (verified with task-stack probes).
- ~~P3 — Redactor ignores known secrets shorter than 6 chars~~ **Fixed in post-beta pass 3**: 3-5 char known values mask as standalone tokens (lookaround-anchored, so symbol-edged values anchor too); 6+ still mask anywhere. Floor configurable via `security.redaction_min_length` (3-6; 6 disables short masking). Hard floor of 3 — 1-2 char values are never registered.
- ~~P3 — Cooperative cancel is between-iteration, not mid-tool~~ **Fixed in post-beta pass 3**: `tools/cancellable.py` polls the cancel callable while the child runs and kills the whole process tree (taskkill /F /T on Windows, killpg on POSIX); docker cancel also kills the container. Terminal, execute_code, and both docker paths use it via `ToolContext.should_cancel`.

## Recommended next additions (after this pass)

- ~~Streaming token output in CLI/TUI~~ — done (post-beta pass 2). SSE streaming in both HTTP transports (`stream: true`; OpenAI also `stream_options.include_usage`), pure fold functions (`fold_anthropic_stream`/`fold_openai_stream`) unit-testable without a network, 4xx-on-stream falls back to non-streaming (auth/rate errors still raise). Display is line-buffered through `StreamSink` (repl.py) so redaction always sees complete lines — never flush partial lines on a size boundary, a secret split across deltas would slip through. `streaming: true` config toggle; `--once` and subagents stay non-streaming. Known limits: line-granularity display (not per-token); a mid-stream provider failure that triggers a fallback transport can re-print already-streamed partial text.
- ~~Cost/token budget accounting surfaced per session~~ — done (post-beta pass 1, `usage.py`, `/usage`). Follow-ups done in post-beta pass 3: per-session totals persisted in SQLite (`sessions.input_tokens/output_tokens`, migrated in place on old DBs), shown by `pulsar sessions list`; `budget.session_tokens` warns once per session when crossed (warn-only, never blocks).
- Richer diff rendering in TUI.
- ~~Config schema versioning + migration harness~~ — done (post-beta pass 3). `CONFIG_VERSION` + `MIGRATIONS` chain in config.py: user files migrate in place on load (only user keys persisted back, never merged defaults), missing version = v1, future versions refuse with guidance. Additive keys need NO version bump (deep_merge fills defaults); bump only for renames/removals/semantic changes.
- ~~Mid-tool cancellation~~ — done (post-beta pass 3, `tools/cancellable.py`).
