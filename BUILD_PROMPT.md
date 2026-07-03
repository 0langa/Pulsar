# Build Prompt For Claude Code / Fable 5

You are Claude Fable 5 operating in Claude Code desktop inside this workspace.

Build the MVP described by `CLAUDE.md` and `docs/FINAL_BUILDER_SPEC.md`. The result should be a working local-first autonomous coding agent named Pulsar, implemented in this repository.

Before coding:

1. Read `CLAUDE.md`.
2. Read `docs/FINAL_BUILDER_SPEC.md`.
3. Read `docs/SAFETY_AND_SCOPE_LOCK.md`.
4. Read `docs/VERIFICATION_PLAN.md`.
5. Skim `research/kimi/mvp-spec.md`, `research/kimi/architecture-decisions.md`, and `research/kimi/implementation-checklist.md` only for supporting detail.
6. Use `research/kimi/synthesis-core.md` and `research/codex-partial/results/*.json` only as reference evidence when a design choice is unclear.

Build priorities:

1. Implement the small local coding-agent core first.
2. Make the CLI usable before adding optional surfaces.
3. Make safety gates, checkpoints, secrets handling, and tests real.
4. Keep optional features behind explicit config gates.
5. Do not expand MVP scope because the research mentions a larger Hermes/OpenClaw feature.
6. Initialize local git if missing.
7. This project is intended to be public on GitHub. Keep `research/` local-only and out of git. After the first coherent implementation commit, create/push a public GitHub repo named `Pulsar` if `gh auth status` succeeds and no `origin` exists; ask the user if GitHub account/owner/repo naming is ambiguous.

Required final deliverables:

- working Python package/module `pulsar_agent`
- distribution name `pulsar-agent`
- `pyproject.toml`
- CLI command `pulsar` and `python -m pulsar_agent`
- provider router with at least Anthropic Messages, OpenAI-compatible chat completions, and custom/local OpenAI-compatible endpoints
- `PULSAR_HOME` profile/state system
- `.env` secret loading and redaction
- SQLite+FTS5 session store
- Markdown memory loader
- local skill loader
- file, search, terminal, code execution, todo, and delegate tools
- command approval profiles and hardline blocklist
- checkpoint/rollback
- tests for the above
- `README.md`, `SECURITY.md`, and `WORK_STATUS.md`

Stop conditions:

- Stop only when the MVP is implemented and verified, or when blocked by a real missing secret/user decision.
- If blocked or interrupted, update `WORK_STATUS.md` with exact status, commands run, failing tests, next step, and open questions.

Do not ask the user to choose between equivalent implementation options. Make conservative choices that match the spec and keep the product small.
