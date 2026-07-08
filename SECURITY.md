# Security

Pulsar's safety layer is implemented in code, not just prompts. This document describes the guarantees and their limits.

## Non-overridable boundaries

The following are enforced in code and cannot be disabled by prompts, project files, skills, memory content, config, or approval presets:

- **Hardline command blocklist** — root filesystem deletes, disk formatting, fork bombs, raw writes to block devices, drive-root recursive deletes, piping remote scripts to privileged shells, and disk-wipe tooling are refused in every preset (`pulsar_agent/security/command_risk.py`). The classifier runs before the approval system, and the approval system independently refuses BLOCKED-tier requests even if asked to approve them.
- **Credential file blocking** — file tools refuse `.env`, `.env.*`, `auth.json`, `secrets.enc`, private keys (`id_rsa`, `id_ed25519`, `*.pem`, …), `.git/credentials`, and the whole `PULSAR_HOME` state directory (`pulsar_agent/security/paths.py`).
- **Workspace scoping** — file tools cannot read or write outside the workspace, except explicit read-only skill directories.

## Secrets

- Secrets live only in `PULSAR_HOME/.env` (created with restricted permissions). `config.yaml` may name environment variables but never contain key values; inline `api_key` fields in config are rejected at load.
- Secrets are never exported into `os.environ`; providers receive keys directly in request headers.
- Redaction runs before: console output, session DB writes, every tool result returned to the model, and error messages. It masks known secret values loaded from `.env` plus common credential patterns (API-key shapes, bearer headers, `KEY=value` assignments).
- `terminal` and `execute_code` child processes get a scrubbed environment: variables whose names contain `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `CREDENTIAL`, or `AUTH` are stripped unless explicitly listed in `terminal.env_passthrough`.
- Checkpoint snapshots exclude `.env`-style and key files, so secrets never enter the checkpoint store.

## Prompt injection

Project files (`AGENTS.md`, `CLAUDE.md`), skills, memory files, and tool output are treated as untrusted data. They are fenced as data in the prompt, and memory writes are scanned for injection markers and secret-shaped content before being staged. Nothing read from the repository can change the toolset, approvals, or safety boundaries, because those live in code paths the model does not control.

## Approval model

Three presets: `paranoid`, `review` (default), `trusted-local`.

- **paranoid** — auto-approves only workspace file reads; every terminal command (even read-only) and every mutating action requires approval.
- **review** (default) — auto-approves reads and SAFE (read-only/test) terminal commands; asks for file writes, patches, `execute_code`, memory writes, and any mutating or risky terminal command.
- **trusted-local** — same low-risk auto set as `review`. It additionally honors explicit, per-capability autonomy grants (`security.autonomy.allow_writes`, `allow_execute_code`, `allow_memory_writes`) and the exact-match `security.command_allowlist`.

Autonomy grants are the only way to raise autonomy above `review`, they are off by default, each grant unlocks exactly one capability, and they take effect only under `trusted-local` (stricter presets ignore them). By itself, no preset auto-approves dependency installs, networked commands, or destructive commands — those are APPROVAL-tier terminal actions and require an approval or an exact allowlist entry the user configured. There is no fully autonomous destructive mode.

Every terminal approval request carries the command, working directory, risk tier, human-readable reason, and whether a checkpoint will be taken first. All approval decisions and boundary triggers are recorded in an in-session audit log.

### Non-overridable guarantee (defense in depth)

Hardline-BLOCKED commands are refused at two independent layers: the tool refuses them before building an approval request, and `ApprovalManager.check` refuses any BLOCKED request even if an approver returns yes. No preset, autonomy grant, command allowlist, config value, prompt text, or subagent path can bypass this. This is covered by tests in `tests/test_safety_hardening.py` and `tests/test_command_risk.py`.

### Subprocess environment isolation

`terminal.env_mode` is `allowlist` by default: `terminal` and `execute_code` child processes (and the Docker/MCP backends) receive only a fixed baseline of non-sensitive variables plus names explicitly listed in `terminal.env_passthrough`. A secret stored in an environment variable with a bland name (e.g. `MYVALUE`) is therefore not inherited by child processes. The weaker `scrub` mode (drop only variables whose *name* matches a secret pattern) is available for compatibility but trusts variable names and can leak bland-named secrets.

## Docker backend

The opt-in Docker backend (`terminal.backend: docker`) adds OS-level isolation for `terminal` and `execute_code`:

- containers run with `--rm`, never `--privileged`, plus `--cap-drop ALL` and `--security-opt no-new-privileges`
- network disabled by default (`docker.network: none`), memory/cpu/pids limits applied
- only the workspace is mounted (`rw` or `ro` per config); `PULSAR_HOME` and the rest of the filesystem are not visible
- environment is allowlist-only (`docker.env_allowlist` holds variable *names*), forwarded as `-e NAME` so values never appear in the docker argv
- client-side timeout with best-effort `docker kill` so a timed-out container does not keep running

The hardline blocklist and approval pipeline run before either backend; Docker changes the blast radius, not the permission model. The `local` backend stays available and is documented as less isolated.

## MCP client

MCP support is stdio-only and off by default: a server runs only if its config entry sets `enabled: true`. Per-server config declares command, args, cwd, allowed tools, env passthrough names, and startup timeout.

- server subprocesses get an allowlist-first environment (fixed baseline + declared `env_passthrough` names); undeclared secrets are never inherited
- discovered tools are namespaced `mcp_<server>_<tool>`; `allowed_tools` filters what is exposed; unavailable or crashed servers contribute nothing to the model schema
- every invocation passes through the approval pipeline (kind `mcp`). Auto-approval requires the explicit `security.autonomy.allow_mcp` grant *and* the `trusted-local` preset; `review`/`paranoid` always prompt
- tool descriptions from servers are untrusted input: they are length-capped and prefixed with their origin before entering the model schema
- all MCP output is truncated and redacted before console, session DB, and model context
- subagents never see MCP tools

MCP servers are third-party code running on your machine. Enabling one is equivalent to running that program yourself — review what you enable.

## Web retrieval

`web_search` / `web_extract` are read-only by construction: the implementation issues only GET requests, sends no cookies or credentials, and has no POST/upload path.

- SSRF policy (default on): only http/https; loopback, private, link-local (incl. cloud metadata `169.254.169.254`), reserved, and multicast ranges are blocked for both literal IPs and every resolved address; redirects are re-validated hop by hop; `file:` and other schemes are always refused
- resolve-then-pin: each request connects to the exact IP that passed validation (Host header and TLS SNI/verification keep the original hostname), so a DNS-rebinding server cannot answer public for the check and private for the fetch
- `web.allow_private_urls: true` is the explicit opt-in for internal URLs; it never unlocks `file:` URLs
- responses are size-capped (`web.max_bytes`), text-capped (`web.text_limit`), and redacted before console, session DB, and model context
- under `paranoid`, every fetch requires approval; `review`/`trusted-local` auto-approve because the tools are read-only

Web-specific limits: a fetched URL is also an outbound channel — a prompt-injected page could ask the model to encode data into a subsequent request's query string. Redaction masks known secrets in tool *output*, not in requested URLs; `paranoid` puts a human in front of every request. `allow_private_urls` disables both the range checks and connection pinning — keep the default policy on for untrusted content.

## TUI

The optional TUI changes presentation only, never the permission model: approval requests from the agent worker thread block until the user decides in a modal (default deny on timeout/escape), the same redaction runs before anything reaches the transcript, and the session store is thread-safe (single connection behind a lock). If Textual is missing or the terminal is insufficient, Pulsar degrades to guidance and the classic CLI remains fully functional.

## Static analysis and CI

`ruff`, `mypy`, `bandit`, and `pip-audit` run in CI on every push/PR alongside the test suite (Linux + Windows). Bandit's subprocess findings are deliberately skipped with rationale in `pyproject.toml`: subprocess/shell execution *is* the terminal tool, and every invocation is risk-classified, approval-gated, and environment-scrubbed as described above.

## Known limits

- The local terminal backend offers no OS-level sandboxing; the risk classifier and approvals are the primary defense. Review commands before approving them.
- The command classifier is heuristic. Unknown commands default to requiring approval, but a hostile command can be disguised; `paranoid` mode exists for untrusted contexts.
- Checkpoints protect workspace files only — not databases, remote state, or anything a command mutates outside the workspace.
- Redaction cannot mask secrets it has never seen if they also match no known pattern.

## Reporting

Open a GitHub issue for non-sensitive reports. For sensitive vulnerabilities, use GitHub's private security advisory feature on the repository.
