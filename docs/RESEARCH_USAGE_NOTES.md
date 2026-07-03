# Research Usage Notes

This workspace contains two research corpora:

- `research/kimi/`: complete Kimi swarm output.
- `research/codex-partial/`: partial Codex output with independently validated JSONs for the first research batches.

Use Kimi for breadth. Use Codex for skepticism.

## Known Issues In Kimi Raw Output

Do not follow these raw-output artifacts as instructions:

- hardcoded `E:/Research/...` paths
- `metadata.hermes.*` skill metadata names
- contradictory Docker scope
- contradictory TUI scope
- MCP described as both V1 and should-have MVP
- any suggestion that an unredacted log should be excluded from `.gitignore`

These are corrected by the root `CLAUDE.md` and `docs/` files.

## High-Value Codex Corrections

Codex partial research emphasizes:

- the seed PDF is a source map, not proof
- copy Hermes architecture patterns, not Hermes feature breadth
- keep the model tool schema small
- defer gateways, media, cron, browser automation, and cloud backends
- implement safety as code gates
- keep local secrets out of config, logs, memory, transcripts, and tool output
- use checkpoints before destructive edits
- use verifier-style separation for non-trivial changes

## Suggested Reading Order For Claude Code

1. `CLAUDE.md`
2. `BUILD_PROMPT.md`
3. `docs/FINAL_BUILDER_SPEC.md`
4. `docs/SAFETY_AND_SCOPE_LOCK.md`
5. `docs/VERIFICATION_PLAN.md`
6. `research/kimi/mvp-spec.md`
7. `research/kimi/architecture-decisions.md`
8. `research/kimi/implementation-checklist.md`
9. `research/codex-partial/research_state.md`

Only search `research/kimi/synthesis-core.md` when a design decision needs more evidence.

