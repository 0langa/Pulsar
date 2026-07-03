# Verification Plan

Run verification continuously. Do not wait until the end.

## Minimum Test Coverage

Required unit tests:

- provider identifier parsing and resolution
- custom provider config rejects inline `api_key`
- `PULSAR_HOME` path helper
- secret loading and redaction
- file path scoping
- command risk classifier and hardline blocklist
- tool registry registration and `check_fn` filtering
- session store create/append/search/delete
- memory snapshot loading and bounds
- skill discovery
- checkpoint create/restore
- `execute_code` environment scrubbing
- `delegate_task` restrictions

Required integration tests:

- `python -m pulsar_agent --help`
- `pulsar setup` with fake keys and temp `PULSAR_HOME`
- simple session with mocked provider and tool call
- file patch plus rollback in a temp repository
- terminal command output is redacted before persistence
- session search returns expected snippets

Required audit checks:

```powershell
rg -n --glob '!research/**' "E:/Research|C:/Users/Julius|~/.hermes|~/.fable/|metadata\\.hermes|api_key\\s*:" .
rg -n --glob '!research/**' "TODO|TBD|FIXME" pulsar_agent tests README.md SECURITY.md pyproject.toml
rg -n --glob '!research/**' "YOLO|review-writes|interactive|smart mode|HERMES_REDACT" .
```

Allowed findings:

- references under `research/` may still contain stale source text.
- generated lockfiles may contain paths if created by tooling.
- this verification document may contain search strings that name prohibited terms.

Not allowed in implementation/docs:

- hardcoded user-machine paths
- stale Hermes home paths
- inline secrets in config examples
- contradictory MVP/V1 scope
- TODO/FIXME left in implementation

## Completion Criteria

Before declaring done:

1. All required tests pass.
2. `python -m pulsar_agent --help` works.
3. CLI can run a mocked provider session.
4. File patch and rollback work in a temp repo.
5. Secret redaction tests prove secrets do not reach logs/session/tool output.
6. The hardline blocklist cannot be overridden by config or approval preset.
7. `WORK_STATUS.md` summarizes implemented features, test commands, failures, deferred items, and open questions.
