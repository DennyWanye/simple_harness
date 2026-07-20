# Agent Harness Simplification — Execution Baseline

> Captured: 2026-07-20 (Asia/Shanghai)  
> Git: `master@961c7d34` (`feat(deepresearch): simplify orchestration and restore report UX`)  
> Scope: current dirty master before any harness business-code implementation

## Workspace state

- DeepResearch v7 work was committed to `master` as `961c7d34` while baseline capture was in progress; the final baseline and all implementation worktrees use that commit, so the harness implementation includes rather than overwrites the latest production stack.
- The harness plan adds only documentation in the primary worktree at this point. Business implementation uses isolated `codex/harness-*` worktrees and must not reset or overwrite the primary worktree.
- `tauri-app/pnpm-lock.yaml` and `pnpm-workspace.yaml` were already untracked at capture time.

## Baseline results

| Gate | Command | Result | Time / notes |
|---|---|---|---|
| Harness contracts | `python -m pytest <test_agent_harness_*.py> -q` | PASS — 30 | 4.45s |
| AgentLoop group | `python -m pytest <test_agent_loop_*.py + runtime> -q` | PASS — 36 | 3.83s |
| Tool/Registry group | `python -m pytest <test_tool_*.py + test_registry_*.py> -q` | PASS — 86 | 2.91s |
| Workflow group | `python -m pytest <test_workflow_*.py> -q` | PASS — 650 | 177.55s, one pre-existing DeprecationWarning |
| Known vector-worker flaky | `python -m pytest backend/tests/test_deskpet_vector_worker.py::test_enqueue_small_batch_flushes_on_interval -q` | PASS — 1 | 0.84s; remains documented as time-based flaky |
| Backend full suite | `python -m pytest backend/tests -q` | INCOMPLETE — timeout | 904s with no final summary; project status already documents a full-suite vector/embedder-worker hang, so this is not counted green |
| Frontend Vitest | `node node_modules/vitest/vitest.mjs run --reporter=dot` | PASS — 79 files / 832 tests | 15.21s; stderr contains existing Tauri/window test-environment noise |
| Frontend TypeScript + Vite | `node node_modules/typescript/bin/tsc -b` then `node node_modules/vite/bin/vite.js build` | PASS | 21.2s; existing chunk-size/dynamic-import warnings |
| Frontend ESLint | `node node_modules/eslint/bin/eslint.js .` | BASELINE RED — 265 problems | 255 errors, 10 warnings; 4 errors + 2 warnings auto-fixable |
| Rust | `cargo test` | PASS — 73 | 2.07s tests; existing dead-code/linker warnings |

## Environment notes

- Ambient `npm` is unavailable. The bundled `pnpm.cmd` attempted an install and stopped at `ERR_PNPM_IGNORED_BUILDS` for `esbuild`; no approval or lockfile mutation was performed.
- Frontend baseline therefore invokes the already-installed repository `node_modules` entrypoints with the bundled Node executable.
- Running backend pytest from `backend/` alone makes four `scripts.*` imports fail collection; the canonical baseline command runs from the repository root with `backend/tests`.

## Regression policy

- Harness/AgentLoop/Tool/Workflow focused groups, frontend tests/build, and Rust are the green comparison set.
- ESLint's 255 existing errors and the backend full-suite timeout are pre-existing baseline defects, not permission to add new failures. Changed-file lint must be clean and the post-change error set must not grow.
- Any touched backend area must pass its focused suite plus the 650-test workflow group; final validation retries the full backend suite with hang diagnostics rather than reporting it green without a summary.

## Phase-2 rollback checkpoint

> Captured after the failed WI-12 production cutover was fully rolled back.

- Integration worktree: `F:\projects\deskpet-harness-integration`
- Safe commit: `4d38979e` (`feat(harness): journal authorized effects atomically`)
- Worktree after rollback: clean
- `python -m pytest -q backend/tests/harness_simplification`: **PASS — 150 passed, 9 xfailed** in 15.20s; one `aiosqlite` event-loop-close warning remains to be fixed before final gates.
- Current post-foundation orchestration LOC: **33,228** (`fixed_total=22,266`, `execution/harness=7,982`, previously omitted `workflows/store/execution_uow.py=2,980`). This is an intermediate seam baseline, not an accepted simplification result; the final gate remains the original **≤17,250**. R0 must replace the manual file set with fixed-SHA automatic discovery so moves and new orchestration files cannot escape counting.
- First WI-12 attempt reached 25,954 LOC but silently lost ContextAssembler/history/persona/memory, attachments, problem-pipeline state, Skill Codify and several UI events. It was therefore rejected under AC-18 even though owner count had fallen.

## R0 mechanical baseline — complete

- Locked manifests reproduce phase-0 `21,563` LOC at `961c7d340334927acfa07cfaffe071510f56cff3` and rollback/current `33,228` LOC at `4d38979ec9d965afdef32243fe6492e1627eb8ec`; current unknown classifications: `0`.
- The schema-2 canonical benchmark ran 10,000 real `RunKernel.start → SqliteExecutionUnitOfWork.finalize → RunKernel.close` lifecycles in 685.9s: starts/terminal rows/final events/closes all `10,000`, completed-run strong refs `0`, RSS delta `1,470,464` bytes.
- Product census: `141` legacy callsites, `unmapped_count=0`; direct behavior tests cover canonical messages, real tool capability filtering, read-only/Code/DeepResearch/PPT routing, Voice, AutoResume, permission restore, SessionDB/workflow delivery and codify.
- Integrated gate after both R0 slices: `176 passed, 9 xfailed`; census check PASS; LOC check PASS. R0 changes scripts/tests/manifests/docs only and do not change production ownership.
- R1 触及既有 Native Workflow 共享底座后，LOC 继续以 rollback SHA 为机械 BASE：manifest 内 orchestration 按全文、新 production 文件按全文、BASE 已存在且 manifest 外的 schema/effect/outbox 等文件只计 Git 正向 added-lines；删除行不允许抵扣。R1 整体以 `--loc-only --r1-gate` 强制不高于 33,228。此口径避免把本来就存在的共享底座整文件冒充 harness 新增，同时保留移动/复制 manifest 内容的全文追踪。
