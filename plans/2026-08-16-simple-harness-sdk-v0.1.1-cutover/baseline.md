# Pre-change baseline — 2026-08-16

> Scope: product checkout `/Users/denny/projects/simple_harness` and SDK checkout
> `/Users/denny/projects/simple-harness-sdk`, before v0.1.1 implementation.

## SDK baseline

- Command: `cd /Users/denny/projects/simple-harness-sdk && .venv/bin/python -m pytest -q`
- Result: **PASS**, `1122 passed, 2 skipped in 13.72s`.
- The two skips are the conformance pytest-plugin cases that require an explicit
  `--simple-harness-host MODULE:FACTORY`.
- SDK checkout: `main...origin/main`, clean.

## Public full-surface smoke baseline

- Command: `SDK_PYTHON=backend/.venv/bin/python PYTHONPATH=backend
  CONFORMANCE_HOST=deskpet.sdk_adapters.conformance:build_host
  EVIDENCE_RUN_DIR=<repo>/.local-test-evidence/2026-08-16/simple-harness-sdk/2026-08-16-r1
  bash testcase/2026-08-13-simple-harness-sdk/run-sdk-public-smoke.sh`.
- Import: **PASS**, `SDK_IMPORT_OK 0.1.0`.
- Conformance: **EXPECTED RED**, exit `1`, report status `not_implemented` with
  message `T5.4 conformance test execution not yet implemented`.
- Raw local evidence:
  `.local-test-evidence/2026-08-16/simple-harness-sdk/2026-08-16-r1/smoke/`.
- Interpretation: this is a pre-existing product/SDK cutover blocker assigned to
  slice A; it is not accepted as a green gate.

## Product baseline

- Capture command: `python3 scripts/baseline_runner.py --run-dir
  plans/2026-08-16-simple-harness-sdk-v0.1.1-cutover/verification/2026-08-16-r1/baseline-product
  --known-failures
  plans/2026-08-16-simple-harness-sdk-v0.1.1-cutover/verification/2026-08-16-r1/baseline-known-failures.snapshot.json
  --heartbeat-seconds 20`.
- The runner initially wrote to the plan verification directory; before delivery its entire raw output directory was
  moved unchanged to the Git-ignored local evidence path below to satisfy the repository evidence policy.
- Result: **effective green against the recorded baseline**, `8 passed` shards and
  `9 known-failure` shards; no unexpected failure fingerprint.
- Passed shards: `backend-b`, `backend-g-l`, `backend-companion`,
  `frontend-vitest`, `frontend-typecheck`, `frontend-build`, `rust-test`,
  `rust-check`.
- Known-failure shards: `backend-a`, `backend-c`, `backend-d-f`, `backend-m-r`,
  `backend-s-z`, `backend-capabilities`, `backend-harness-simplification`,
  `root-tests`, `frontend-lint`.
- Raw logs and canonical machine state (Git-ignored):
  `.local-test-evidence/2026-08-16/simple-harness-sdk/2026-08-16-r1/baseline-product/`.
- Frozen per-run known-failure copy:
  `verification/2026-08-16-r1/baseline-known-failures.snapshot.json`.
- Product checkout at capture: `main...origin/main [ahead 2]`; only the new
  architecture/plan/gate files were dirty. No production code had been changed.

## Regression rule

Post-change comparison must preserve all passing shards. A previously red shard
may become green; any failure fingerprint not present in the per-run snapshot is
a new regression and blocks the slice. The public conformance expected-red must
become a real four-suite PASS before slice A can complete.
