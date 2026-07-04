# Work Status

Status: MVP + beta expansion Bars 1-4 implemented and verified. 238 passed, 1 skipped (docker integration; skips cleanly without a daemon).

## Beta expansion pass

- **Bar 1 (safety hardening)** — done in the previous session: allowlist-first subprocess env (`terminal.env_mode`), autonomy grants scoped to `trusted-local`, exact-match command allowlist, richer approval prompts, audit log. Tests in `tests/test_safety_hardening.py`.
- **Bar 2 (Docker backend)** — done. `pulsar_agent/tools/docker_backend.py`; `terminal`/`execute_code` select backend via `terminal.backend` (`local`|`docker`). Hardened defaults (no privileged, cap-drop ALL, no-new-privileges, network none, workspace-only mount, mem/cpu/pids limits, env allowlist by name, timeout + kill, output limit). Graceful guidance when Docker is missing/daemon down. Approval semantics unchanged. Tests: `tests/test_docker_backend.py` (13 mocked + 1 best-effort integration that skips without a daemon).
- **Bar 3 (MCP client)** — done. `pulsar_agent/mcp/` (stdio JSON-RPC client + manager). Disabled by default; per-server `enabled` flag, allowed_tools, env_passthrough (allowlist-first), startup_timeout. Tools namespaced `mcp_<server>_<tool>`, approval kind `mcp` (auto only via `security.autonomy.allow_mcp` under trusted-local), output truncated+redacted, crashed/absent servers excluded from schema, subagents excluded. Tests: `tests/test_mcp_client.py` (16, with a fake stdio server covering discovery, namespacing, env filtering, redaction, disabled-by-default, startup/call timeouts, crash handling, invocation).
- **Bar 4 (web retrieval)** — done. `pulsar_agent/tools/web_tools.py`: `web_search` (DuckDuckGo no-key default, documented as best-effort; `brave` backend with user key via `web.search_results_api_env_var`) and `web_extract` (GET-only fetch, HTML→text, title/status/content-type/truncation metadata). SSRF policy blocks localhost/private/link-local/metadata/file:/redirect-into-private by default; `web.allow_private_urls` opt-in. Gated by `web.enabled` + hidden from subagents; `paranoid` prompts per fetch. Tests: `tests/test_web_tools.py` (32).

Public repository: https://github.com/0langa/Pulsar (`origin`, branch `main`).

## Implemented

- Package `pulsar_agent`, distribution `pulsar-agent`, CLI `pulsar` + `python -m pulsar_agent` (pyproject.toml, entry point).
- `PULSAR_HOME` state root (`home.py`): env override, `~/.pulsar` default, `get_pulsar_home()` / `display_pulsar_home()`, layout bootstrap.
- Config (`config.py`): YAML behavior config with deep-merge defaults; custom providers validated; inline `api_key` in config rejected.
- Secrets (`secrets.py`): `.env`-only secret store, never exported to `os.environ`, restricted file permissions, redactor registration.
- Security (`security/`): centralized redaction (values + patterns), workspace path policy with credential-file blocklist and protected `PULSAR_HOME`, three-tier command risk classifier with non-overridable hardline blocklist, approval presets `paranoid`/`review`/`trusted-local` with audit log.
- Providers (`providers/`): `provider:model` router; `anthropic_messages`, `chat_completions`, `custom_openai_compatible` transports; local Ollama/LM Studio presets; deterministic `mock` transport; fallback chain on retryable errors.
- Tools (`tools/`): registry with `check_fn` gating; core set capped at eight: `read_file`, `write_file`, `patch`, `search_files`, `terminal`, `execute_code`, `todo`, `delegate_task`. Read-before-edit enforced; terminal/execute run with scrubbed env; every result redacted + truncated.
- Sessions (`sessions/store.py`): SQLite WAL + FTS5, redact-before-persist, create/append/list/search/delete.
- Memory (`memory/store.py`): bounded `MEMORY.md`/`USER.md`, frozen snapshot, secret + injection scans, staged writes with `/memory approve`.
- Skills (`skills/`): builtin + `PULSAR_HOME/skills` discovery, frontmatter parsing, one builtin skill (`python-test-and-fix`).
- Checkpoints (`checkpoints/store.py`): shadow-git store per workspace under `PULSAR_HOME/checkpoints/`, secrets excluded, linear-history rollback that is itself reversible.
- Agent loop (`run_agent.py`): sync ReAct loop, iteration budget with graceful exhaustion, stable per-session tool schema, subagent runner with role-restricted registries.
- CLI (`cli/`): REPL with status header and tool progress, slash commands (`/model /tools /memory /skills /checkpoint /rollback /reset /new /help /quit`), `pulsar setup`, `pulsar model`, `pulsar sessions list|search|delete`, `--once` non-interactive turn.
- Docs: `README.md`, `SECURITY.md`, `LICENSE` (MIT).

## Test Status

Command: `python -m pytest` → 238 passed, 1 skipped (docker daemon integration check). Unit + integration; integration covers `--help`, setup with fake key, mocked-provider session, patch+rollback in temp repo, terminal-output redaction into session DB, session search snippets, and (when a daemon is present) a real docker echo run.

Audit greps from `docs/VERIFICATION_PLAN.md` run clean: no TODO/FIXME in implementation, no stale research paths or prohibited terms in shipped code/docs (remaining `api_key:` hits are Python type annotations, not config secrets). Handoff artifacts with machine-specific paths (`START_HERE.md`, `docs/HANDOFF_AUDIT.md`) are git-ignored along with `research/`.

## Deferred (per scope lock)

Remaining should-have: full-screen TUI. All V1+ items (gateways, cron, dashboards, browser automation, marketplace, cloud terminals) remain out of scope. Future-improvement ideas are tracked in `PROJECT_STATE.md`.

## Open Questions

None blocking.
