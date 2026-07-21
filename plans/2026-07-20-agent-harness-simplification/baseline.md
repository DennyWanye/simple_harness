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
- R4.5-2 typed transaction surface is complete: the exact audited public starters are **21** (from 33, hard gate `≤23`). Activation, delivery, ReAct boundary, decision, tool claim, effect settle, run outcome, child signal, and recovery now each use one typed composite entry; all former split starters were removed after their callers migrated. Current raw counted orchestration is **33,973**, audited core **5,552**, Kernel **812**, DML authority **1**, fault windows **39**, and unknown classifications **0**. Full harness verification is **389 passed, 8 xfailed**.
- The R4.5 authority migration cohort is locked by `r45-migration-cohorts.json` to base `796905b9`, exact path/blob/hash/group/reason, and aggregate physical/raw-effective LOC. At the base, `execution_uow.py + checkpoint_execution.py` is physical **5,236** and raw-effective **4,651**; currently it is physical **5,251** and raw-effective **5,175**. The raw overcount is therefore mechanically `(5,175-4,651) - (5,251-5,236) = 509`, producing adjusted total **33,464**. Gates use the adjusted total while still reporting raw total and the complete adjustment. This does not raise the `33,618` target: it counts the checkpoint-to-UoW authority move once as a cohort physical net change. Real cohort/non-cohort additions still increase total, moves to new production files are net zero, genuine deletions reduce only their physical LOC, and fixture/snapshot/group/compression drift fails closed. The adjusted total gate is green; final core/LiveRun/Supervisor authority gates remain.
- R4.5-3 venue-wrapper slice: the locked `ProductVenueOpenResult` is a redundant two-slot container, so `ProductVenueRunAdapter.open()` now returns `ProductVenueRunSession | ProductVenueRunResult` directly. `ProductVenueRunResult` remains because it carries explicit pre-Kernel `short_circuited/cancelled` and post-run `run_id/status/final_text` semantics. `venues.py` is **406→386** counted lines, a real **20 core LOC** deletion against the locked **14 LOC** budget; no private-only change, line packing, move, or forwarding facade is used. Slice totals are raw **33,953**, adjusted **33,444**, core **5,532**, Kernel **812**. Focused Text/Voice/Presenter/venue regression is **69 passed**; fault/schema regression is **51 passed**; authority check and transition gate pass with starters **21**, DML authority **1**, Presenter converter **1**, fault windows **39**, production `legacy/0`. The isolated final LOC gate remains red only at core `5,532 > 5,500`, pending the other independent deletion slices.
- The R4.5 LiveRun slice removes the three duplicate maps (`RunKernel._active`, `ReActDriver._volatile`, and `LegacyAgentLoopCollaborator._active`) and leaves **one** run-map authority, `BoundedLiveIndex._runs`. Kernel injects that index through the generic `bind_live_index` capability; no driver creates a fallback index. `LiveRun` separately owns driver boundary and iterator references, durable continuation reads precede live fallback, identity checks prevent stale finalizers from clearing replacements, and iterator `aclose()` runs outside the live lock. Current slice-local adjusted total is **33,531**, core **5,619**, Kernel **811**; the transition gate is green, while the final core and Supervisor gates remain pending. The earlier spike's `+14` omitted these required production semantics; the formal net is **+67**, and the final `core<=5,500` target is unchanged. Expanded focused verification is **158 passed** after replacing obsolete `_active`/`_volatile` white-box assertions; authority check reports run map `1`, DML authority `1`, transaction starters `21`, Presenter `1`, legacy survivors `15`, and Supervisor `2`.
- After integrating both slices, current raw/adjusted/core/Kernel values are **34,020 / 33,511 / 5,599 / 811**. The adjusted total, transition gate, and run-map authority are green; final core and Supervisor authority remain pending.
- The single-Supervisor slice removes `ChildRunScheduler` and `HarnessRecoveryCoordinator` as separate resident-task/lifecycle owners. `HarnessSupervisor` now owns one task and bounded child-command, child-signal, recovery, late-ready, and delivery lanes, with explicit `16`-item scans, per-lane timeouts, monotonic deadline+wakeup scheduling, and timeout isolation. Startup performs child commands → recovery → child signals → optional delivery before exactly one supervisor start; shutdown performs supervisor stop → bounded effect close → final ready reconcile → Kernel drain → Driver close → LiveRun sweep. Late-ready recovery filters the requested run ids in SQLite before applying the `16`-row limit, so an older backlog cannot starve completed effects. Authority target is fully green: DML `1`, starters `21`, run map `1`, Supervisor `1`, Presenter `1`, legacy survivors `15`. Current raw/adjusted/core/Kernel values are **34,013 / 33,504 / 5,580 / 811**; this slice is a real core net deletion of **19**. Focused verification is **97 passed**, fault/authority/baseline verification is **75 passed**, and the full harness is **404 passed, 8 xfailed**. The transition gate is green; the R4.5 final LOC gate remains red only on core `5,580 > 5,500` and is not relaxed.
- The EffectBatchExecutor slice deletes `UnifiedToolExecutor` and its registry-forwarding surface. One high-level executor now owns durable claim/reuse/reconcile, ordered safe/unsafe execution, metadata/status, timeout unknown persistence, and post-signal durable verification before idempotent registry acknowledgement. `ReActDriver.signal` remains the only effect+continuation+event atomic settlement owner; signal/settle failures acknowledge nothing, all-late batches emit no signal, mixed ready/late outcomes preserve original indexes, settled restart reuse does not consult live policy or re-execute, and shutdown drains then performs final recovery before Driver close. Current raw/adjusted/core/Kernel values are **33,975 / 33,466 / 5,542 / 811**; the slice is a real core net deletion of **38**. Focused verification is **63 passed**, independent challenger verdict is **PASS**, and the full harness is **412 passed, 8 xfailed**. The transition gate remains green; final core `5,542 > 5,500` is still pending and not relaxed.
- Frontend Vitest/build and Rust are unchanged by the R4.5 baseline documentation update and retain the green results recorded above. They are rerun after production wiring changes in R6/R7; changed backend slices must run focused tests plus the full harness suite at every stage gate.
