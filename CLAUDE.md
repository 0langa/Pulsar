# Pulsar Build Instructions

Clean build workspace for coding-first, user-sovereign autonomous coding agent inspired by Hermes Agent and OpenClaw.

Build product here. Do not edit files under `research/` except to add short audit notes. Treat `research/` as evidence, not implementation source.

## Priority Order

On conflict, follow this order:

1. User messages in active Claude Code conversation.
2. This `CLAUDE.md`.
3. `BUILD_PROMPT.md`.
4. `docs/FINAL_BUILDER_SPEC.md`.
5. `docs/SAFETY_AND_SCOPE_LOCK.md`.
6. `docs/VERIFICATION_PLAN.md`.
7. Files under `research/`.

Raw research files may contain stale paths such as `E:/Research/...`, Hermes-specific naming, or contradictory MVP/V1 scope. Do not follow stale paths. Current workspace root is authoritative.

## Product Target

Build local-first coding agent named Pulsar.

MVP must be practical developer tool:

- classic interactive CLI as required interface
- provider-agnostic model router
- bring-your-own-key secret handling
- local and OpenAI-compatible model support
- file read/write/patch/search tools
- local terminal tool with command approval
- Python `execute_code` tool with scrubbed environment
- SQLite+FTS5 session persistence
- bounded Markdown memory files
- basic local skill loader
- checkpoint/rollback before destructive file or terminal actions
- small `delegate_task` hook for isolated planner/explorer/verifier subagents
- deterministic tests and final verification report

MVP must not become general assistant, messaging gateway, browser automation suite, media tool, cloud orchestration platform, or plugin marketplace.

## MVP Scope Lock

Required:

- Python 3.11+ package/module named `pulsar_agent`.
- Distribution name `pulsar-agent`.
- CLI command `pulsar` and module fallback `python -m pulsar_agent`.
- State root resolved through `PULSAR_HOME`, defaulting to `~/.pulsar`.
- Secrets only in `PULSAR_HOME/.env`; behavior in config files.
- Core model toolset capped at: `read_file`, `write_file`, `patch`, `search_files`, `terminal`, `execute_code`, `todo`, `delegate_task`.
- Agent-level loop actions may include `memory` and `session_search`, but do not expose them as extra always-on model tools unless needed.
- Provider identifiers use `provider:model`.
- Approval presets: `paranoid`, `review`, `trusted-local`.
- `trusted-local` never bypasses non-overridable boundaries or hardline command blocklist.

Should-have only after required MVP works:

- minimal MCP client for configured stdio servers
- opt-in Docker backend
- opt-in full-screen TUI
- read-only `web_search` / `web_extract`
- richer provider plugin loading

Deferred to V1 or later:

- messaging gateways such as Telegram, Discord, Slack, WhatsApp, Matrix, email, or SMS
- cron scheduler and durable background jobs
- web dashboard, Electron/native desktop app, or mobile UI
- browser automation beyond read-only documentation retrieval
- marketplace or optional-skills hub
- cloud terminal backends such as SSH, Modal, Daytona, or Singularity
- voice, image, video, TTS, social/lifestyle skills
- batch trajectory/RL hooks

## Safety Requirements

Implement safety in code, not only prompts.

Non-overridable refusal boundaries:

- malware, credential theft, phishing, unauthorized access, destructive third-party actions, paywall/access-control bypass, data exfiltration, or evasion of safety controls
- hardline destructive local commands such as root filesystem deletes, disk formatting, fork bombs, raw writes to block devices, or pipe-remote-to-shell against sensitive paths
- attempts by prompt-injected project files, skills, MCP tools, or config to disable safety layer

Secrets:

- No plaintext secrets in source code, `config.yaml`, memory files, logs, tests, or chat output.
- Block file tools from reading `PULSAR_HOME/.env`, `auth.json`, private keys, `.git/credentials`, and `secrets.enc`.
- Redact secrets before logs, tool output, chat, session persistence, and export.
- Any unredacted developer-only log must be opt-in, outside checkpoints/exports, and explicitly gitignored.

## Research Usage

Use Kimi's complete research package under `research/kimi/` as broad coverage.

Use Codex's partial research under `research/codex-partial/` as independent quality check, especially for:

- avoiding scope bloat
- Hermes safety and secret handling
- narrow-core/wide-edges architecture
- tool registry and MCP boundaries
- subagent isolation
- terminal backend risks

Do not copy Hermes code unless license compatibility and attribution are explicitly handled. Prefer independent implementation of architectural pattern.

## Work Discipline

- Read `BUILD_PROMPT.md` first.
- Read `docs/FINAL_BUILDER_SPEC.md` before implementation.
- Initialize local git repository if missing.
- Project intended to have public GitHub repository.
- Keep `research/` local-only. Do not commit raw research artifacts to public product repository unless user explicitly asks.
- If no `origin` remote exists, `gh auth status` succeeds, and first coherent implementation commit is ready, create public GitHub repository named `Pulsar` under authenticated account, add it as `origin`, and push.
- If GitHub authentication, owner selection, or repository naming is ambiguous, pause and ask user instead of guessing.
- Create short plan, then implement.
- Keep progress in `WORK_STATUS.md`.
- Add tests as features are implemented.
- Run verification plan before claiming completion.
- If context runs out, update `WORK_STATUS.md` with current state, next actions, test status, and unresolved decisions.
