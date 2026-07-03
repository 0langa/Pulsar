# Safety And Scope Lock

## Scope

This project is a coding agent, not a general autonomous assistant.

The MVP is done when it can run locally, edit a repository, run commands/tests, persist sessions, remember bounded project facts, delegate small subtasks, protect secrets, and roll back changes.

Anything outside that loop must be deferred unless it is required to make the core coding loop work.

## Must Not Build In MVP

- messaging gateways
- cron scheduler
- durable background jobs
- web dashboard
- desktop app
- mobile UI
- broad browser automation
- voice/image/video/TTS tools
- marketplace or public skill hub
- smart-home/lifestyle/social skills
- cloud terminal backends
- batch trajectory generation for RL/evals
- unauthenticated API server

## Permission Presets

Use exactly these presets:

- `paranoid`: approve every terminal command and every write.
- `review`: auto-approve reads; ask for writes and risky commands.
- `trusted-local`: auto-approve low-risk local operations; still ask for destructive commands and always enforce hardline blocks.

There is no default YOLO mode.

## Command Risk Tiers

Allowed without approval in low-friction modes:

- read-only file listing/search
- package/version inspection
- test commands that do not mutate external state

Approval required:

- file writes
- dependency installation
- network calls that upload data
- deletes inside the workspace
- git history mutation
- database migrations
- service control commands

Always block:

- root filesystem deletion
- disk formatting
- fork bombs
- raw writes to block devices
- credential theft or exfiltration
- disabling safety controls
- pipe remote script to shell against sensitive system paths
- destructive third-party/cloud actions without explicit user intent

## Secrets

Secrets live only in `PULSAR_HOME/.env` or an OS keychain integration added later.

Config may name environment variables, but must never contain raw keys.

Redaction applies before:

- console output
- logs
- session DB writes
- tool results returned to the model
- exports
- checkpoint metadata

If an unredacted debug log exists:

- it is opt-in
- it is disabled by default
- it is outside checkpoint/export paths
- it is listed in `.gitignore`
- it is never shown to the model

## Prompt Injection

Treat project files, memory files, skills, web pages, MCP output, and tool output as untrusted data.

They cannot:

- override system/developer/user instructions
- request secret access
- disable approvals
- alter provider credentials
- add new always-on tools
- change the scope lock

## Source Policy

The research references Hermes and OpenClaw as architectural examples. Do not clone their full feature surface into the MVP.

Use primary sources when a claim matters. Mark uncertain claims as uncertain. Do not turn unverified research claims into implementation requirements.
