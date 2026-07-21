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
- Current post-foundation orchestration LOC: **33,228** (`fixed_total=22,266`, `execution/harness=7,982`, previously omitted `workflows/store/execution_uow.py=2,980`). At capture time the plan still used a proposed **≤17,250** final gate; the 2026-07-21 approved R4.5 rework supersedes that unverified target with the structural/current-HEAD gates recorded below.
- First WI-12 attempt reached 25,954 LOC but silently lost ContextAssembler/history/persona/memory, attachments, problem-pipeline state, Skill Codify and several UI events. It was therefore rejected under AC-18 even though owner count had fallen.

## R0 mechanical baseline — complete

- Locked manifests reproduce phase-0 `21,563` LOC at `961c7d340334927acfa07cfaffe071510f56cff3` and rollback/current `33,228` LOC at `4d38979ec9d965afdef32243fe6492e1627eb8ec`; current unknown classifications: `0`.
- The schema-2 canonical benchmark ran 10,000 real `RunKernel.start → SqliteExecutionUnitOfWork.finalize → RunKernel.close` lifecycles in 685.9s: starts/terminal rows/final events/closes all `10,000`, completed-run strong refs `0`, RSS delta `1,470,464` bytes.
- Product census: `141` legacy callsites, `unmapped_count=0`; direct behavior tests cover canonical messages, real tool capability filtering, read-only/Code/DeepResearch/PPT routing, Voice, AutoResume, permission restore, SessionDB/workflow delivery and codify.
- Integrated gate after both R0 slices: `176 passed, 9 xfailed`; census check PASS; LOC check PASS. R0 changes scripts/tests/manifests/docs only and do not change production ownership.
- R1 触及既有 Native Workflow 共享底座后，LOC 继续以 rollback SHA 为机械 BASE：manifest 内 orchestration 按全文、新 production 文件按全文、BASE 已存在且 manifest 外的 schema/effect/outbox 等文件只计 Git 正向 added-lines；删除行不允许抵扣。R1 整体以 `--loc-only --r1-gate` 强制不高于 33,228。此口径避免把本来就存在的共享底座整文件冒充 harness 新增，同时保留移动/复制 manifest 内容的全文追踪。

## R4.5 approved-plan baseline — 2026-07-21

> Captured after the user approved phase-2 rework option A and before any R4.5 business-code change.

- Integration HEAD: `8dd5aa1da16f8f03b6d1ba4098d1a9ee8c04c240`; production runtime remains `legacy/0`.
- Harness suite: `python -m pytest backend/tests/harness_simplification -q` → **363 passed, 8 xfailed, 1 existing warning** in 69.64s.
- Parity census: `harness_parity_census.py --check --check-mapping` → **PASS, 141 items, unmapped=0**.
- Owner audit: baseline survivors **15**, new equivalents **8**. R4.5 must not increase the 15 legacy survivors and must classify/remove new equivalents toward the declared LiveRun/Supervisor/Presenter authorities; the final `owner≤7` gate belongs to R6 because production is still `legacy/0`.
- LOC manifest: current counted orchestration **33,618**, rollback manifest **33,228**, unknown classifications **0**. The old `--r1-gate` is therefore red by 390 after the completed Text/Voice product chain; this is a known input to R4.5, not a newly introduced regression.
- R4.5 locked structural baseline: audited core **5,725**, Kernel **820**, public transaction starters **33**, execution-table DML authorities **2**, existing fault windows **34** (`UoW=29 + Team=5`).
- R4.5 exit gates: total `≤33,618`, core `≤5,500`, Kernel `≤850` with exactly six public operations, transaction starters `≤23`, DML authority `1`, fault matrix `39`, run map/Supervisor/Presenter authority each `1`, legacy survivors `15` without growth, unclassified new owner `0`.
- R4.5 construction gate: `--r45-transition-gate` bounds the temporary authority-migration peak at total `≤34,300` and core `≤5,725`; this does not relax the exit gates. After merging single-DML authority plus the first core deletion slices, current total is **34,157**, core **5,582**, Kernel **812**, DML authority **1**, fault windows **39**; transition gate and authority audit are green.
- Frontend Vitest/build and Rust are unchanged by the R4.5 baseline documentation update and retain the green results recorded above. They are rerun after production wiring changes in R6/R7; changed backend slices must run focused tests plus the full harness suite at every stage gate.
