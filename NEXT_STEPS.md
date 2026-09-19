# Pulsar — Status & Roadmap
_Portfolio audit: 2026-07-11_

## What this is

A local-first, coding-first autonomous agent that runs in the terminal: reads/edits files,
searches code, runs commands and tests under a three-preset approval system (`paranoid` /
`review` / `trusted-local`), persists sessions and bounded memory, delegates to isolated leaf
subagents, and rolls back its own changes via shadow-git checkpoints. Deliberately not a general
assistant — the scope lock lives in `docs/SAFETY_AND_SCOPE_LOCK.md`.

Stack: Python 3.11+, runtime deps only `pyyaml` + `httpx`, optional Textual TUI, SQLite+FTS5
session store, opt-in hardened Docker execution backend, stdio MCP client, read-only web tools
with SSRF resolve-then-pin, GitHub Actions CI.

## Current state

- **Feature-complete for its locked scope, and verified.** MVP, beta expansion Bars 1–8, and
  post-beta passes 1–4 are done, each tracked with pass/fail evidence in WORK_STATUS.md:
  - safety hardening (allowlist-first env, autonomy grants, non-overridable hardline blocklist);
  - Docker backend with cap-drop/no-new-privileges/network-none defaults and startup health
    check;
  - MCP client with crash auto-restart (cap 3) and `/mcp` status; read-only web retrieval with
    DNS-rebinding closed via resolve-then-pin;
  - TUI with modal approvals, streaming output, mid-tool cancellation (process-tree kill),
    per-session token/cost accounting, config schema versioning with in-place migration,
    provider profiles from `PULSAR_HOME/providers/*.yaml`, diff-aware file approvals.
- **Quality posture is strong**: 294 tests at the Bar-8 gate plus post-beta suites since (22
  test files in `tests/`, e.g. `test_streaming.py`, `test_cancellable.py`,
  `test_provider_plugins.py`); ruff, mypy, bandit, pip-audit all clean; CI green on Linux +
  Windows, Python 3.11/3.12; two independent self-audits with every P0/P1 fixed under a named
  regression test; zero TODO/FIXME in the implementation. `git status` clean.
- **But it has never been released.** `pyproject.toml` says 0.1.0; there are zero git tags, zero
  GitHub Releases, no PyPI package, and no CHANGELOG.md. The only install path is `pip install
  -e .` from a checkout. For a repo of only 20 commits, an unusual amount of finished, audited
  work is sitting unversioned on `main` — the gap between engineering maturity and release
  maturity is the defining fact of this project.
- **Docs are builder-facing**, not user-facing: `BUILD_PROMPT.md`, `docs/FINAL_BUILDER_SPEC.md`,
  `docs/VERIFICATION_PLAN.md`, `docs/SAFETY_AND_SCOPE_LOCK.md`. The README is the only end-user
  document (it is good).
- **Known limits are honestly documented** in PROJECT_STATE.md: local backend has no OS sandbox
  (classifier + approvals are the defense), the command classifier is heuristic, the DuckDuckGo
  search backend is scrape-fragile (Brave key is the reliable path), Docker timeout is
  client-side, and Windows is the primary hand-tested platform — POSIX coverage is mostly CI,
  macOS is untested.

## Definition of "finished"

Pulsar's scope is done; "finished" means it becomes an installable, versioned product:

- Tagged v0.1.0 on GitHub with a CHANGELOG and a release workflow producing sdist + wheel;
  installable via `pipx install pulsar-agent` (PyPI, or at minimum the GitHub release artifact).
- Clean-machine first-run verified on Windows, Ubuntu, and macOS: `pulsar setup` → a
  real-provider turn → `/rollback` → `pulsar sessions list`, with builtin skills confirmed
  inside the wheel (`[tool.setuptools.package-data]` is declared but has never been exercised by
  a real build).
- A provider support matrix (anthropic, openai, openrouter, ollama, lmstudio) smoke-tested
  against live endpoints at release time — CI only ever exercises the mock transport.
- User docs beyond the README, or an explicit decision that the README is the manual.

## Roadmap

### Phase 1 — Now (next 1–2 weeks)

1. **First public release.** Write `CHANGELOG.md` (WORK_STATUS.md's pass history is ready-made
   material), tag `v0.1.0`, add a `release.yml` workflow (`python -m build`, `twine check`,
   attach artifacts, optionally publish to PyPI). Check the `pulsar-agent` name on PyPI
   immediately — renaming is far cheaper now than after launch. Mind the recorded gotcha:
   pushing workflow files needs the keyring `gh` credential (`env -u GITHUB_TOKEN git push`).
2. **Package validation on a clean machine.** `pipx install` the built wheel on Windows and
   Ubuntu with no repo checkout present; verify the `pulsar` console script, `python -m
   pulsar_agent`, builtin skill discovery from `pulsar_agent/skills/builtin/`, and the first-run
   setup wizard.
3. **README install section** switched from `pip install -e .` to the released install path,
   keeping editable install under Development.

### Phase 2 — Next (2–6 weeks)

4. **Provider smoke matrix.** Scripted `--once` run per provider family with a recorded
   pass/fail table per release (mock is deterministic; anthropic/openai need keys,
   ollama/lmstudio need local models). This is the main "does it work against today's APIs" gap.
5. **macOS pass.** The code branches correctly on platform (`taskkill /F /T` vs `killpg`,
   `os.path.normcase` shadow-repo digests), but PROJECT_STATE admits real-world coverage is
   Windows-tested. One focused session covering terminal, checkpoints/rollback, TUI, and
   mid-tool cancellation.
6. **Richer diff rendering in the TUI** — the last open item on PROJECT_STATE's "recommended
   next additions" list. Approvals already carry redacted unified diffs; render them with syntax
   highlighting in the modal instead of plain text.
7. **User documentation**: a `docs/USAGE.md` (or wiki) covering the approval presets and
   `security.autonomy` grants (powerful, currently explained only in README prose), Docker
   backend setup, MCP server configuration, and the checkpoint/rollback model.
8. **Dogfood loop**: use Pulsar for real work on one of the sibling repos and file issues; the
   project has excellent synthetic verification but no recorded daily-driver mileage.

### Phase 3 — Later (optional/stretch)

9. Deferred ideas already triaged in PROJECT_STATE.md, in rough value order:
   - OS keychain secret backend (today `.env`-only);
   - vector/embedding session recall (FTS5-only today);
   - MCP HTTP/SSE transport plus resources/prompts (stdio + tools only today);
   - per-command Docker container reuse for speed; rootless podman;
   - web HTML cache with ETag revalidation and per-domain rate limiting;
   - skill hub / install flow.
10. LLM-assisted approval classification (explicitly deferred; the hardline blocklist must stay
    non-overridable).
11. Write down v1.0 criteria in PROJECT_STATE.md so the version number tracks something
    observable (e.g., N external users, a quarter of API stability, macOS parity).

## Effort to "finished"

**S** for the first tagged, installable release — the code is done, this is packaging and
verification. **M** to reach the full v1.0 bar with the provider matrix, macOS validation, and
user docs.
