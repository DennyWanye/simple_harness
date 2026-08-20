# Execution baseline — 2026-08-21

## Scope

- Tested worktree: existing shared `main` worktree before SDK Context cutover implementation.
- Runner: `python3 scripts/baseline_runner.py --run-dir .local-test-evidence/2026-08-21/sdk-context-baseline --known-failures .local-test-evidence/2026-08-21/sdk-context-baseline/known-failures.json --resume --accept-current-failures --heartbeat-seconds 20`
- Test inventory at lock time: 586 Python/Vitest test files; large-repository shard runner required.
- The worktree already contained user-owned changes for prior IME/stop/provider work. They are part of the tested baseline and must not be overwritten.

## Result

- Effective green shards: 18/18.
- Passed cleanly: 8 shards.
- Existing failures captured as baseline signatures: 10 shards.
- Newly introduced failures at baseline time: none after local signature capture.

Clean shards included SDK adapters, frontend Vitest/typecheck/build, Rust test/check, backend B and backend G-L.

Existing red areas include old Agent Reach pinning, retired ContextAssembler/Memory expectations, legacy DeepResearch/wiring tests, office path policy, legacy memory IPC/tool search, historical workflow/capability fixtures, root E2E requiring a live backend, and existing frontend lint findings. These failures predate this implementation. Any changed signature or additional failing test is a regression unless the corresponding legacy oracle is explicitly retired by the approved cutover and replaced by a stronger AC-bound test.

## Evidence

- Raw state: `.local-test-evidence/2026-08-21/sdk-context-baseline/baseline-state.json`
- Local known-failure snapshot: `.local-test-evidence/2026-08-21/sdk-context-baseline/known-failures.json`
- Raw shard logs: `.local-test-evidence/2026-08-21/sdk-context-baseline/logs/`
- State SHA-256: `e6c049e46c3bf0a9abbc4ae91edff9a9a27f1eabecd0c97882db5756dd973875`
- Failure snapshot SHA-256: `978c96ba227e02dd7bfe848d18e1768a73934c7ff5007bec1d208c43e0e7d3de`

Raw evidence is intentionally Git-ignored under the project evidence policy.
