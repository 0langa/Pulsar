---
name: python-test-and-fix
description: Run a Python project's tests, diagnose failures, fix, and re-verify
version: "1.0"
metadata:
  pulsar:
    category: coding
    tags: [python, pytest, testing]
---

# Python: Test And Fix

## Procedure

1. Detect the test runner: `pyproject.toml` with `[tool.pytest.ini_options]`
   or a `tests/` directory means pytest; otherwise try `python -m unittest discover`.
2. Run the suite once with `terminal` (e.g. `python -m pytest`) before changing
   anything, to get a clean baseline of failures.
3. For each failure, read the failing test and the code under test with
   `read_file` before editing. Prefer `patch` for minimal, targeted fixes.
4. Fix one failure cluster at a time; re-run only the affected test file
   (`python -m pytest tests/test_x.py -q`) to iterate quickly.
5. When individual fixes pass, run the full suite again to catch regressions.

## Pitfalls

- Do not "fix" a test by weakening its assertions unless the test itself is
  demonstrably wrong; state your reasoning when you do.
- Import errors often mean a missing dev dependency, not broken code; check
  `pyproject.toml` extras before editing sources.
- Beware of tests that depend on state (env vars, cwd, network); they can
  fail for environmental reasons unrelated to the change.

## Verification

- The full test suite exits with code 0.
- No previously passing test now fails.
- Summarize which tests failed initially, what changed, and the final counts.
