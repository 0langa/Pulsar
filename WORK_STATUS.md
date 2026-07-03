# Work Status

Status: MVP implemented and verified. 153/153 tests passing.

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

Command: `python -m pytest` → 153 passed (unit + integration; integration covers `--help`, setup with fake key, mocked-provider session, patch+rollback in temp repo, terminal-output redaction into session DB, session search snippets).

Audit greps from `docs/VERIFICATION_PLAN.md` run clean: no TODO/FIXME in implementation, no stale research paths or prohibited terms in shipped code/docs (remaining `api_key:` hits are Python type annotations, not config secrets). Handoff artifacts with machine-specific paths (`START_HERE.md`, `docs/HANDOFF_AUDIT.md`) are git-ignored along with `research/`.

## Deferred (per scope lock)

Should-haves not included in this MVP pass: MCP client, Docker backend, full-screen TUI, read-only web_search/web_extract. All V1+ items (gateways, cron, dashboards, browser automation, marketplace, cloud terminals) remain out of scope.

## Open Questions

None blocking.
