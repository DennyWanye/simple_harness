# Universal Action + Capability Packs — Green Baseline

> Date: 2026-07-24  
> Branch: `master`  
> HEAD: `50717c4c4548c294a948632c86941b0fb5fa60d4`

## Baseline decision

The implementation baseline is **usable with two pre-existing red lanes isolated**:

1. the full backend suite did not finish inside a 15-minute observation window;
2. frontend ESLint reports a large pre-existing backlog.

All focused production-path tests, frontend tests/type checking/build, Python
compilation, and Rust checks/tests are green. New work must keep those green
and must not increase the isolated lint backlog.

## Green lanes

| Lane | Command | Result |
|---|---|---|
| Backend collection | `backend\.venv\Scripts\python.exe -m pytest backend/tests --collect-only -q` | PASS — `5765/5774 tests collected (9 deselected)` in 4.38 s |
| Focused backend production path | `backend\.venv\Scripts\python.exe -m pytest backend/tests/harness_simplification/test_wi2_tool_executor.py backend/tests/test_prepared_tool_set_contract.py backend/tests/test_tool_capability_hydration.py backend/tests/test_workflow_routing.py -q` | PASS — 51 passed in 1.47 s |
| Python compilation | `backend\.venv\Scripts\python.exe -m compileall -q backend/deskpet backend/agent backend/main.py` | PASS |
| Frontend tests | bundled Node + `node_modules/vitest/vitest.mjs run` | PASS — 81 files, 841 tests in 23.25 s |
| Frontend TypeScript | bundled Node + `node_modules/typescript/bin/tsc -b --pretty false` | PASS |
| Frontend production bundle | bundled Node + `node_modules/vite/bin/vite.js build` | PASS — 1398 modules transformed |
| Rust check | `cargo check --manifest-path tauri-app/src-tauri/Cargo.toml` | PASS — one existing `dead_code` warning |
| Rust tests | `cargo test --manifest-path tauri-app/src-tauri/Cargo.toml` | PASS — 73 tests |

Expected frontend-test stderr about an unavailable relay/backend, missing
Tauri `invoke` in jsdom, and animation fallbacks did not fail the suite.
The Vite build also retained its existing large-chunk and ineffective dynamic
import warnings.

## Isolated pre-existing red lanes

### Full backend suite observation window

Command:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests -q
```

Result: **TIMEOUT / INCOMPLETE**, not a test assertion failure. Pytest emitted
no final result before the 904-second command limit. Collection independently
succeeds for 5774 tests, and the focused production path is green.

The exact timed-out process tree was cleaned up by command-line/PID scope only:
`27608, 15044, 19004, 25300, 29588, 25248, 29272, 7756, 16240, 26088, 32044`.
No matching process remained; approximately 912.6 MiB of working-set memory
was released.

Gate for this plan: every changed backend slice must have a focused suite, the
cross-slice acceptance suite must be green, and the full suite will be retried
at the final gate with progress/output capture rather than silently weakening
the requirement.

### Frontend lint backlog

Command:

```powershell
node node_modules/eslint/bin/eslint.js .
```

Result: **FAIL — 267 problems (257 errors, 10 warnings)**. The findings span
existing React hook/ref rules, `no-explicit-any`, unused symbols, and other
unrelated files.

Gate for this plan: changed frontend files must pass targeted ESLint, and a
final whole-tree lint comparison must be no worse than `257 errors / 10
warnings`.

### Package-manager bootstrap diagnostic

Ambient `npm` is unavailable. The workspace-bundled Node/pnpm runtime is used.
The first `pnpm run lint` bootstrap linked dependencies but reported ignored
package build scripts (including esbuild); invoking the checked-in local CLI
entrypoints with bundled Node produced the authoritative results above.

## Dirty-worktree ownership boundary

The repository was already dirty before implementation. Existing modified and
untracked files belong to the user and are preserved. Several current changes
overlap the future harness/tool/architecture work, so implementation must
inspect and integrate with those diffs rather than overwrite them. Any final
staging or commit must include only this plan's verified slice.
