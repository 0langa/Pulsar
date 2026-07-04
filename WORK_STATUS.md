# Work Status

Status: MVP + full beta expansion (Bars 1-8) implemented, self-audited, and verified.

Public repository: https://github.com/0langa/Pulsar (`origin`, branch `main`).

## Bar status (beta expansion)

| Bar | Scope | Status | Evidence |
|---|---|---|---|
| 1 | Safety hardening (allowlist-first env, autonomy grants, command allowlist, audit log) | PASS | `tests/test_safety_hardening.py` |
| 2 | Docker execution backend (opt-in, hardened defaults, graceful degradation) | PASS | `tests/test_docker_backend.py` (14, incl. live daemon integration) |
| 3 | stdio MCP client (disabled by default, namespaced tools, allowlist env, approval-gated) | PASS | `tests/test_mcp_client.py` (16, fake stdio server) |
| 4 | Read-only web retrieval (SSRF policy, GET-only, DDG + Brave backends) | PASS | `tests/test_web_tools.py` (32) |
| 5 | TUI (`pulsar --tui`, textual extra, graceful fallback, modal approvals) | PASS | `tests/test_tui.py` (parser, controller, headless pilot) |
| 6 | Repo intelligence (project map, test discovery, git awareness, progress + recovery UX) | PASS | `tests/test_intel.py`, recovery-hint tests in `tests/test_tui.py` |
| 7 | Production hygiene (ruff, mypy, bandit, pip-audit, GitHub Actions CI, coverage) | PASS | `.github/workflows/ci.yml`, `pyproject.toml` tool sections |
| 8 | End-to-end verification + audit greps | PASS | commands below |

## Exact commands run (Bar 8 verification)

```
python -m pytest                          → 294 passed (incl. docker integration; skips cleanly without daemon)
python -m pytest --cov=pulsar_agent --cov-report=term-missing   (coverage command)
ruff check pulsar_agent tests             → All checks passed!
mypy                                      → Success: no issues found in 44 source files
bandit -c pyproject.toml -r pulsar_agent  → 0 issues (justified skips documented in pyproject)
pip-audit --skip-editable                 → no findings for Pulsar deps (pyyaml, httpx, textual);
                                            unrelated hermes-venv packages excluded from scope, CI runs in a clean env
python -m pulsar_agent --help             → OK
pulsar --help                             → OK (console script installed)
PULSAR_HOME=<tmp> python -m pulsar_agent --once "verify beta pass"   (mock provider) → OK
python -m pulsar_agent sessions search "verify beta"                 → snippet found
```

Audit greps: no `fable5`/`hermes` names in shipped code/docs, no `research/` files tracked by git, no TODO/FIXME in implementation, no inline secrets in config.

## Self-audit (release gate)

Two independent audit agents reviewed Bars 1-8 (security/backends; CLI/TUI/persistence). Every P0/P1 and the load-bearing P2/P3 findings were fixed in the same pass, each with a regression test. Remaining P3s are accepted and documented in `PROJECT_STATE.md`.

Fixes applied (with the test that guards each):

- **P1 — SAFE-classification command-injection bypass.** `command_risk.classify_command` treated `cat $(rm -rf ~)`, backticks, `;`/`&&` chains, and embedded newlines as SAFE (auto-approved under `review`). Added a shell-metacharacter gate that forfeits SAFE before the allow patterns. Test: `test_shell_escape_never_safe`.
- **P1 — TUI worker kept running after quit.** Thread worker had no cancel path; actions executed post-exit and shutdown blocked on it. Added cooperative cancel (`Agent.should_cancel`, checked each iteration; pending calls still get recorded results) and `on_unmount` releases pending approvals + sets cancel. Tests: `test_cooperative_cancel_stops_turn`, `test_cancel_records_tool_results_for_pending_calls`.
- **P2 — MCP startup warnings/stderr reached console unredacted.** Routed the MCP warn callback through the redactor.
- **P2 — `/model` persisted a bad id before validating**, bricking next startup. `switch_model` now rebuilds before persisting and rolls back on failure. Test: `test_switch_model_does_not_persist_bad_id`.
- **P2 — Interrupted turn left a dangling `tool_use`** → every later turn 400s. `run_turn` repairs history on any interruption. Test: `test_interrupted_turn_history_repaired`.
- **P2 — Checkpoint shadow repos collided on case-sensitive FS** (`str.lower()` digest). Switched to `os.path.normcase`. Test: `test_case_distinct_workspaces_get_distinct_shadow_repos`.
- **P2 — `intel.build_project_map` hung on a symlink cycle** at startup. Added a visited-realpath set and symlink skip. Test: `test_project_map_survives_symlink_cycle`.
- **P2 — `run_tui` built `Repl` outside its try** → raw traceback on startup error. Moved inside the guard.
- **P2 — TUI assistant text sink captured pre-mount** → intermediate output lost. Agent now reads the sink indirectly at emit time.
- **P2 — Unhandled slash-command exception killed the REPL.** Wrapped `handle_slash` per-command.
- **P2 — Staged memory writes clobbered each other** (each composed from disk). `_compose` now builds on the latest pending staged content. Tests: `test_multiple_staged_writes_are_cumulative`, `test_staged_duplicate_detected_against_pending`.
- **P3 — SSRF missed CGNAT (100.64.0.0/10).** `_ip_blocked` now also rejects `not is_global`. Tests: CGNAT cases in `test_private_and_metadata_urls_blocked`, `test_cgnat_hostname_blocked`.
- **P3 — MCP unbounded read buffer** (giant no-newline line). Bounded `readline`; oversized frames dropped. Test: `test_oversized_frame_dropped_not_buffered`.
- **P3 — MCP sanitized-name collision aborted startup.** Duplicate namespaced tools are now skipped with a warning.
- **P3 — `/memory` unusable in the TUI** (staged writes were a dead end). Added `/memory` to the TUI. Test: `test_memory_command_available_in_tui`.
- **P3 — Checkpoint rollback missed non-ASCII added files** (git quotepath). Set `core.quotepath=false`.
- **P3 — `SecretStore.set` corrupted `.env` on a multiline value.** Rejects newlines. Test: `test_secret_store_rejects_newline_value`.
- **Earlier in-pass fixes:** SessionStore thread-safety (locked, `check_same_thread=False`); project-map prompt-injection framing; typed `build_agent_runtime`; bandit findings (SHA-1 `usedforsecurity=False`, asserts → guards, annotated best-effort paths).

Accepted/deferred P3s (rationale + next step in `PROJECT_STATE.md`): docker network/image validation, `pulsar model` subcommand resolvability, TUI approval-modal stale-timeout cosmetic, sub-6-char secret redaction, mid-tool (vs between-iteration) cancellation.

## Final verification commands (Bar 8 + self-audit)

```
python -m pytest                          → 294 passed
ruff check pulsar_agent tests             → All checks passed!
mypy                                      → Success: no issues found in 44 source files
bandit -c pyproject.toml -r pulsar_agent  → 0 issues
pip-audit --skip-editable                 → no findings for Pulsar deps
python -m pulsar_agent --help / pulsar --help → OK
pulsar --once (mock) + sessions search    → OK
docker integration test                   → passed (daemon present)
```

## Deferred (per scope lock)

All V1+ items (gateways, cron, dashboards, browser automation, marketplace, cloud terminals) remain out of scope. Future-improvement ideas: see `PROJECT_STATE.md`.

## Open Questions

None blocking.
