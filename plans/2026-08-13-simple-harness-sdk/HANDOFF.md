# Simple Harness SDK handoff

状态日期：2026-08-15（Asia/Shanghai）  
当前结论：**INCOMPLETE / 可继续，但必须从小 slice 恢复**。

本文是给新 session 的事实交接，不是完成声明，也不是授权 push、发布或产品切换。

## 1. 目标与边界

目标是完成可复用的 Simple Harness SDK：durable RunKernel、Native Workflow、Provider/Tool/Effect/Checkpoint/Recovery/Reconciliation、SQLite lease/fence、crash/reopen/exact replay，以及：

`Agent ReAct -> workflow_spawn -> ATTACHED child Workflow -> parent continuation`

Host 不应重新实现 Workflow 引擎或 durable semantics。

保持以下边界：

- 不 push 到 public remote，不发布 wheel，不切换 DeskPet product ingress；
- 不执行 AIPhone/桌面真实 E2E，除非另有明确授权；
- 不使用 destructive Git 操作；当前未提交改动必须先审计；
- 测试原始日志、数据库、截图和凭据留在本机，不加入 Git；
- 任何真正完成的 slice 必须同步更新 `ARCHITECTURE/` 与 `PROJECT_STATUS.md`。

## 2. 仓库与工作树

### Product / plan 仓库

- 路径：`/Users/denny/projects/simple_harness`
- 分支：`main`
- HEAD：`660054df docs: resolve sdk workflow authority plan conflicts`
- 当前工作区：`CLAUDE.md` 是既有修改；本 handoff 和 `verification/` 是本次未跟踪文档/账本；不要把 `CLAUDE.md` 混入 SDK 提交。

主要事实源：

- [`ARCHITECTURE/index.md`](/Users/denny/projects/simple_harness/ARCHITECTURE/index.md)
- [`acceptance.md`](/Users/denny/projects/simple_harness/plans/2026-08-13-simple-harness-sdk/acceptance.md)
- [`behavior-contract.md`](/Users/denny/projects/simple_harness/plans/2026-08-13-simple-harness-sdk/behavior-contract.md)
- [`implementation-tasks.md`](/Users/denny/projects/simple_harness/plans/2026-08-13-simple-harness-sdk/implementation-tasks.md)

### SDK 仓库

- 路径：`/Users/denny/projects/simple-harness-sdk`
- 分支：`codex/sdk-v0.1-foundation`
- HEAD：`86edaa7 feat(runtime): checkpoint workflow launch and kernel lifecycle`
- 当前未提交文件：

  - `src/simple_harness/runtime/kernel.py`
  - `src/simple_harness/runtime/orchestration.py`
  - `src/simple_harness/tools/contracts.py`
  - `tests/unit/runtime/test_termination.py`
  - `tests/unit/runtime/test_workflow_spawn_contracts.py`

这些改动是未完成的 catalog-selection pin / public spawn contract 施工，不能 reset、checkout、clean 或覆盖。

## 3. 已提交且可回滚的 SDK checkpoint

当前 HEAD 已包含：

- `927694f`：允许 Context append 使用 caller transaction；
- `d3e5fab`：Tool/schema bounded preflight；
- `015268b`：immutable workflow catalog authority contracts；
- `d31e595`：partial workflow runtime/persistence foundation，包含 H16 lifecycle、SQLite durable primitives、runner/recovery/replay/native checkpoint 及对应测试；
- `86edaa7`：workflow launch admission、official workflow driver、Kernel lifecycle/cancel carrier 与相关测试。

两个最近 checkpoint 都经过干净临时树验证：

- `d31e595` 候选：`710 passed`，变更范围 Ruff、生产 Pyright、REUSE、wheel/sdist、twine 均通过；
- `86edaa7` 之后：全套 `977 passed`，当前 slice focused `267 passed`，相关 Ruff/Pyright/diff-check/REUSE/build/twine 通过。

这些是恢复点，不代表 SDK-AC-5/6/8、T4.1 或 T4.2 已完成。

## 4. 独立 worktree 与未合并成果

### Parent-terminal slice：可审阅、尚未合并

- worktree：`/Users/denny/projects/simple-harness-sdk-t4-parent-terminal`
- branch：`codex/t4-parent-terminal-settle`
- commit：`95c3d2d23c72e7562fd6917dcfac54c692857734`
- 修改文件：SQLite UoW + H16 integration test 两个文件；worktree clean
- 证据：parent-terminal focused `25 passed`；H16 文件 `281 passed`；full suite `1002 passed`；touched Ruff PASS；生产 Pyright 0；diff-check PASS。
- 内容：ticket-only、ready-unactivated、activated/reclaim successor 三种 parent-terminal settlement shape；COMPLETED/FAILED/CANCELLED；ACTIVE CAS consume；Effect/completion/continuation atomic settlement；exact replay/conflicting evidence；reader fail-closed。
- 合并前必须在主 SDK HEAD 上重新 cherry-pick/审阅并复跑，不要直接复制文件。

### Activation-adapter slice：未完成、不要合并

- worktree：`/Users/denny/projects/simple-harness-sdk-t41-adapters`
- branch：`codex/t41-activation-adapters`
- base：`86edaa7`
- 当前 dirty：`execution/dispatch.py`、`execution/sqlite/uow.py`、新 `workflow/adapters.py`、两个 RED 测试。
- 首次 RED：缺 `simple_harness.workflow.adapters`，属于计划内接口缺口；该 agent 随后因模型不可用退出，**没有 commit**。
- 不要从该 worktree 猜测或手工拷贝半成品；先重新启动独立 agent 或自己完成后再验证。

旧 detached worktree（仅供历史比对，通常不要触碰）：

- `/private/tmp/simple-harness-persistence-range.lXUuNJ/wt`
- `/private/tmp/simple-harness-sdk-a.n5XP1U`
- `/private/tmp/simple-harness-sdk-a2.8RefMA`

## 5. 最新完成度审计：仍未闭合的真实缺口

### P0-1：公开 workflow_spawn 前链

缺少或未完整接线：

- `RunClient.workflow_spawn_catalog()`
- `RunClient.bind_workflow_spawn(...)`
- `RunClient.workflow_spawn(...)`
- `RunClient.prove_graph_unavailable(...)`
- SDK-owned static `workflow_spawn` handler
- durable catalog snapshot 与 ReAct/provider turn 的绑定

### P0-2：Tool / Effect / ReAct reserved seam

- `ToolContext` typed `workflow_spawn_context` 尚未完成 durable production 接线；
- `EffectExecutor` 尚未拥有 sealed `WorkflowSpawnHandlerOutcome` 分支；
- ReAct 首次 Provider tool-call 尚未把 static handler、effect handoff、ticket/continuation、typed control outcome 串成一条真实链；
- `WorkflowProviderAdapter` / `WorkflowEffectAdapter` 尚未合并。

### P0-3：terminal/retry typed seam

- durable terminal/retry types 和 SQLite verifier 已有基础；
- `DriverResult.workflow_terminal` / `workflow_retry_wake` 尚未完整接到 Workflow Driver 和 Kernel；
- Kernel 仍有 generic terminalize 旁路风险；
- benign terminal lease-release、retry timer/startup race 仍需真实测试。

### P0-4：parent-terminal settlement

独立 worktree 的 `95c3d2d` 已实现，但尚未合并、复跑和形成主分支证据。

### P1：recovery/scanner/fork orchestration

- `WorkflowRunner.recover_expired()` 当前仍为 stub `return []`；
- `recover()` 仍可能绕过 classify/repair/quarantine pipeline；
- recovery Port 的 repair/quarantine primitives 及 SDK policy 尚未闭环；
- start/resume scanner、`WorkflowRecoveryCursor`、due `WorkflowRetryWake`、standalone RETRY_WAIT wake、orphan fork startup consumer 尚未完成。

### 架构决策提醒：catalog pin

durable immutable catalog snapshot 是正确的 TOCTOU/replay 方向，但不要把大段 workflow 专属数据随意塞进通用 `TerminationState`。建议使用 typed `WorkflowCatalogSelectionPin`，挂在 ReAct turn/checkpoint 或 spawn invocation receipt 上，保存 canonical bytes/hash、generation/version、profile fingerprint、schema ref/hash；ToolContext 只携 hash/receipt，binder 从同一 transaction owner 的 durable pin 读取。当前未提交测试正处于 RED：`TerminationState` 尚未实现新增 pin 字段，不能将其标绿或直接作为最终架构。

## 6. 当前可复现实证

在 `86edaa7` clean HEAD（未包含当前五个未提交文件）上：

- `uv run pytest -q`：`977 passed in 8.91s`；
- workflow launch/cancel/spawn focused：`267 passed`；
- 相关变更路径 Ruff：PASS；生产路径 Pyright：0 errors；
- `uv run reuse lint`：183/183 compliant（新增文件版本为 183/183）；
- `uv build` + `uv run twine check dist/*`：PASS。

当前未提交施工状态：

- spawn contracts + termination focused：`20 passed / 1 failed`；
- 唯一失败是 `TerminationState.__init__()` 尚未接受 `workflow_catalog_selection`，这是当前明确施工 RED；
- 相关当前 Ruff：PASS。

## 7. 继续工作的推荐顺序（2026-08-15 更新）

**T4.x 已完成**（步骤 2-6 已在后续提交中完成）：
- ✅ WorkflowCatalogSelectionPin 完成（`b3e8e0c`）
- ✅ Parent-terminal settlement 完成（`f7faf13`, `95c3d2d` merged）
- ✅ Activation adapters 完成（`1fb70be`）
- ✅ RunClient workflow_spawn API 完成（`22fa696`）
- ✅ Recovery scanner 完成（`98c591a`）
- ✅ Terminal/retry seam 完成（`f2258a5`）

**当前状态**（HEAD: `f2258a5`）：
- 1009 tests PASSED
- 工作树干净
- T0-T4 完整实现验证通过

**下一阶段**：T5.x 官方 Workflow Profiles（大型任务）

### T5.1 durable_task 进度（2026-08-15 开始）

**已完成**：
- ✅ Phase 1: 设计文档（`verification/T5.1-durable-task-design.md`）
- ✅ Phase 2: 基础合约（~1000行）
  - `workflows/durable_task/__init__.py`
  - `workflows/durable_task/ports.py`（5个 Port 接口）
  - `workflows/durable_task/state.py`（Proposal 状态机）

**待完成**（约1500+行需从产品重写）：
- ⏳ Phase 3: Graph 定义（`definition.py`）
- ⏳ Phase 4: 节点处理器（`nodes.py`，最复杂部分）
- ⏳ Phase 5: 输出合约（`output_contract.py`）
- ⏳ Phase 6: 测试（4个测试文件）

**后续任务**：
- T5.2: personal_v1 workflow
- T5.3: capability_build workflow
- T5.4: Conformance CLI
- T6: Simple Harness cutover

1. 新 session 首先读取本文件、`AGENTS.md`、`ARCHITECTURE/index.md` 和 `implementation-tasks.md`。
2. 继续 T5.1 Phase 3：创建 `definition.py`，定义 durable_task graph 结构。
3. 实现 Phase 4：从产品 `code_nodes.py` (1584行) 重写节点处理器，通过 Ports 调用能力。
4. 每个 phase 都必须：语法检查 → 单元测试 → Ruff/Pyright → 提交。
5. 所有 T5.x 完成后才进入 T6 cutover；没有真实 SDK-AC-1..8 receipt 前，不更新 ARCHITECTURE 为完成。

建议 commit 语义保持单一：

```text
feat(runtime): add durable workflow selection pin
feat(workflow): merge parent-terminal settlement
feat(runtime): wire workflow activation adapters
feat(runtime): expose durable workflow spawn handler
feat(workflow): implement recovery scanner policy
```

## 8. plan-test ledger、验证与原始日志位置

### 机器账本

- 早期流程分析账本：
  `/Users/denny/projects/simple_harness/plans/2026-08-13-simple-harness-sdk/verification/2026-08-14-program-r1/plan-test-run.json`
  - `runs=0`、`evidence=0`，只能用于审计历史流程，不能作为完成凭证。
- 当前恢复 ledger：
  `/Users/denny/projects/simple_harness/plans/2026-08-13-simple-harness-sdk/verification/2026-08-14-program-r2/plan-test-run.json`
  - run id：`run-20260814-225810`
  - phase-3 已登记；7 个 black-box 场景仍 NOT_RUN，这是实现阶段的预期状态。
- plan challenge finding：
  `/Users/denny/projects/simple_harness/plans/2026-08-13-simple-harness-sdk/verification/2026-08-14-program-r1/findings-plan-iteration-001-round-1.json`
  （若该路径不存在，以 `verification/2026-08-14-program-r1/findings-plan-iteration-001-round-1.json` 为准。）

### plan-test skill

- 当前 skill：`/Users/denny/.codex/skills/plan-test/SKILL.md`
- 本次核验的 local/remote HEAD：`1f0e3fa57bc0f3accc6599c12af4b3438ee743c4`
- 远程仓库：`https://github.com/DennyWanye/plan-test-skill.git`
- 当前没有 plan-test finalize receipt；不要把 phase-3 ledger 当最终完成凭证。

### 原始 Codex session logs（只读，不进 Git）

- 主 plan-test 长日志：
  `/Users/denny/.codex/sessions/2026/08/14/rollout-2026-08-14T00-06-41-019ffbe0-306f-73e3-adb3-b71e18d2c802.jsonl`
- 后续实现/审计日志：
  `/Users/denny/.codex/sessions/2026/08/14/rollout-2026-08-14T17-11-43-019fff8a-a0c0-75f0-a675-7ad859c279e7.jsonl`
  `/Users/denny/.codex/sessions/2026/08/14/rollout-2026-08-14T22-32-50-01a000b0-9fe7-70d0-8242-2318f2c36e53.jsonl`
  `/Users/denny/.codex/sessions/2026/08/14/rollout-2026-08-14T22-41-30-01a000b8-8e6f-7380-be65-b2a780a19f77.jsonl`

原始日志可能包含环境路径和内部工具输出，只在本机查看，不复制到仓库、handoff 或 memory。

## 9. 新 session 的第一组安全命令

```bash
cd /Users/denny/projects/simple_harness
sed -n '1,260p' plans/2026-08-13-simple-harness-sdk/HANDOFF.md
git status --short

cd /Users/denny/projects/simple-harness-sdk
git status --short
git log -8 --oneline --decorate
uv run pytest -q
python3 /Users/denny/.codex/skills/plan-test/scripts/plan_test_gate.py \
  check-wip-limit --repo-dir /Users/denny/projects/simple-harness-sdk
```

如果先合并 parent-terminal worktree：

```bash
git show --stat --oneline 95c3d2d
git cherry-pick 95c3d2d
uv run pytest -q tests/integration/execution/test_workflow_launch_admission_h16.py
uv run pytest -q
```

任何 cherry-pick 冲突必须先保存状态并审阅，不得用 `reset --hard`、`checkout --` 或 `git clean` 处理。
