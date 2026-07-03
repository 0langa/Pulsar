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

Three presets: `paranoid`, `review` (default), `trusted-local`. Even `trusted-local` requires approval for destructive commands and always enforces the hardline blocklist. There is no fully autonomous destructive mode. All approval decisions are recorded in an in-session audit log.

## Known limits

- The local terminal backend offers no OS-level sandboxing; the risk classifier and approvals are the primary defense. Review commands before approving them.
- The command classifier is heuristic. Unknown commands default to requiring approval, but a hostile command can be disguised; `paranoid` mode exists for untrusted contexts.
- Checkpoints protect workspace files only — not databases, remote state, or anything a command mutates outside the workspace.
- Redaction cannot mask secrets it has never seen if they also match no known pattern.

## Reporting

Open a GitHub issue for non-sensitive reports. For sensitive vulnerabilities, use GitHub's private security advisory feature on the repository.
