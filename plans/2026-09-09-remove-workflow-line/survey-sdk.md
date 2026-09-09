# 调查记录：Harness SDK 仓 workflow-spawn 线删除边界（2026-09-09，只读映射）

> 调查仓库 `simple-harness-sdk`，基线 `fd12e7dd`（0.7.10 候选提交 `031fdc68`）。由调查子代理产出，本文为归档摘录。

## 1. 仓库现状

- `git status -sb`：`## main...origin/main`，仅 `.gitignore` 有改动。
- 版本：`src/simple_harness/version.py:6` `__version__ = "0.7.10"`；`pyproject.toml:47-48` 动态版本。Host 侧 `sdk_candidate.py:23-26` 常量名是 `SDK_VERSION`（不是 `SDK_HARNESS_VERSION`）。
- CHANGELOG 最新：`## 0.7.10 — bounded nullable Tool schemas (source candidate)`。
- 构建：`docs/build-and-release.md` §2：detached worktree → `uv sync --frozen --group dev` → pytest / mypy / ruff（显式文件清单）/ `scripts/check_source_provenance.py` / `reuse lint` → `uv build --out-dir dist`；hatchling 1.32.0，`packages = ["src/simple_harness"]`。

## 2. 依赖图

### 2.1 `runtime/workflow_spawn.py`

导出 16 个符号（`:440-457`）。引用：`runtime/drivers/react.py:45`（`WorkflowSpawnFailed`）、`runtime/kernel.py:133-135,401-407,1397-1400,3354`、`tools/contracts.py:32,127`（`WorkflowSpawnToolContext`，污染公共 `ToolContext`）、`execution/sqlite/uow.py:174,12996,13093`。反向依赖 `AttachmentPolicy`（保留）、`orchestration` 7 个符号、`start_snapshot.RunStart`（保留）。可整删，先切 `tools/contracts.py` 与 `react.py` 的引用。

### 2.2 `runtime/drivers/workflow.py`

`WORKFLOW_DRIVER_KIND`(:29)、`WorkflowRuntimeDriver`(:45)、`build_workflow_runtime_driver`(:194)；被 `runtime/drivers/__init__.py:17-19,24,34` 与 `runtime/__init__.py:336-341,441,693,711` 再导出（公共 API 面）；`kernel.py:1492-1494,1504-1505,2800,2811-2812`。依赖 `simple_harness.workflow.runner.WorkflowRunner`。可整删。

### 2.3 `runtime/kernel.py`

`WORKFLOW_DRIVER_KIND` 使用点：`:148,510,1482,1485,1525-1526,1913,3006,3057-3058,3482,3485,3490,3514`，全部只服务 workflow 线。`_workflow_runner`：`:1520`（赋值）、`:1458`（`RunClient.workflow_spawn` 读取）。私有状态 `_workflow_spawn_ready_activations` / `_workflow_start_dispatches` / `_workflow_recovery_work`：`:1553-1555,2025-2027,3326-3328,3343-3345,2496-2498,2696-2711,2830-2834,3422,3439`。`_CanonicalWorkflowSpawnRuntimeCoordinator` 整类 `:370-527`；Protocol `:295-306`；`RuntimeServices.workflow_spawn` `:362`；`DriverInvocation` 三字段 `:180-196`；`DriverResult.workflow_spawn_control` `:205,223-226,239`；`RunClient.workflow_spawn_catalog / bind_workflow_spawn / workflow_spawn` `:1381-1462`；`_accept_workflow_spawn_control` 等 `:3351-3444`；`ReactCheckpointPort` 两个 spawn 方法 `:327-346`。`self.children = ChildCoordinator(self)` `:1573` 保留。

### 2.4 `runtime/drivers/react.py`

spawn ready activation 块 `:113-131`；`child_wait` 块 `:139-149`（其后 `:150` 起的 continuation 循环是保留主路径）；import `:45`。`react_loop.py:843-855,870`。

### 2.5 `execution/sqlite/uow.py`

纯 spawn（可删）：`:10913-11043` `_require_workflow_spawn_issue_authority`（含 `:11006`）、`:11044-11215` `issue`、`:11243-14537` 十余个 spawn continuation / admission / settle 方法（含 `:13077-13583 continue_spawn_admission`，`:13251` 注入 `name="workflow_spawn"` 的 Tool 消息）、`:10816-10903` catalog、`:15736-16238` 辅助、`:16241,16572,16597`。
混合（不能整删）：`:2376-2593 ack_spawn_child_continuation_and_continue_batch`（`react.py:143` 调）、`:2594-2761 commit_pending_spawn_child_completion_and_react_ready`（`react_loop.py:845` 调）、`:3762-3876 claim_continuation`、`:4272-4536`、`:5046-5512`、`:5531-5537 is_workflow_spawn_child`（`kernel.py:1914` 调）、`:14571-14780 admit_runtime_start`（保留）、`:14781+`。
**必须保留（不属于 workflow 线）**：`:4537-4584 issue_profile_launch_ticket`、`:4585-4775 claim_profile_launch_and_commit_child`、`:5513-5518 read_profile_launch_ticket`、`:16859-16870 _profile_launch_ticket`。

### 2.6 schema

DDL 集中在 `execution/sqlite/migrations/0005_fresh.sql`；`schema.py:12 SCHEMA_VERSION = 9`，`:39-67` 叠加描述符，`:70-74 accepted_descriptor_rows()`。
workflow 线专属表：`workflow_catalog_authorities`(:244)、`workflow_launch_ticket_receipts`(:253)、`workflow_spawn_continuations`(:557)、`workflow_spawn_continuation_ready`(:588)、`workflow_spawn_ready_activations`(:602,+索引 :628,:631)、`workflow_spawn_child_wait_receipts`(:634)、`workflow_spawn_completion_receipts`(:696)。
必须保留：`profile_launch_tickets`(:128)、`child_commands`(:140)、`run_links`(:153)、`child_terminal_receipts`(:162)、`child_signals`(:176)、`child_signal_ack_receipts`(:209)。
关键耦合：`child_commands` 二选一 CHECK（:145-150）`(ticket_id IS NOT NULL) <> (workflow_ticket_receipt_id IS NOT NULL)`，删 workflow 表必须改列与 CHECK，属 schema breaking change（需 v10 / 新 fresh 描述符 / checksum 连锁）。审计登记 `audit_core.py:16,38,40,46,52,114-119`、`audit.py:423`。

### 2.7 其余散点

- `orchestration.py`：可删 `WorkflowCatalogAuthority:193`、`VerifiedWorkflowCatalogAuthority:263`、`WorkflowSpawn*`（:318-783）、`WorkflowLaunchRequest:786`、`WorkflowLaunchTicket:852`、`VerifiedWorkflowLaunchTicket:873`、`VerifiedWorkflowGraphUnavailable:979`、`WorkflowLaunchTicketPort:1268` 的 spawn 方法。**保留**：`StartInputSchema:72`、`ProfileDescriptor`、`RuntimeStartDisposition:1078`、`RuntimeStartDispatchState:1090`、`RuntimeActivationClaim:1096`、`RuntimeStartReceipt:1115`、`RuntimeStartActivation:1142`、`RuntimeStartDispatchClaim:1161`、`RuntimeStartDispatchRecord:1178`、`RuntimeStartAdmission:1190`、`WorkflowProfileRegistration:107`、`WorkflowCatalogProfileBinding:125`（`workflows/_registration.py:11` 与 Host `capability_catalog.py` 用）。
- `runtime/termination.py`：`TerminationState` 四个 workflow 字段（`:36,40-42,171,175-177,225-251,427-433,515-534`）在 ReAct checkpoint 序列化 schema 里。
- `runtime/production.py:95,240`；`tools/contracts.py:32,112,126-130`（`ToolContext.workflow_spawn_context`）；`execution/uow.py:406`。
- `delegate_control` 在 SDK 内不存在（Host 概念，`orchestration_controls.py:330`）。SDK `ToolDispatchKind` 只有 SYNC / ASYNC / CONTEXT / STAGED / CONTROL / PROVIDER。

## 3. 前提纠正：SDK 内不存在「非 ticket 的 child 路径」

`spawn_subagents` / `await_subagents` 在 SDK 里没有实现（只有 `workflows/durable_task/nodes.py:52` 与 `workflow/contracts.py:289,320` 的字符串）。真正实现在 Host `deskpet/tools/code_tools/spawn_subagents_tool.py`，用 Host 自有 `deskpet.harness.ports.AttachmentPolicy` / `deskpet.execution.contracts` 与 Host 自有 `deskpet/workflows/store/execution_uow.py`。

SDK 唯一 child-run 路径就是 ticket 路径：`Runtime.children (kernel.py:1573)` → `ChildCoordinator.launch (child_coordinator.py:17)` → `uow.claim_profile_launch_and_commit_child (uow.py:4585)` → `profile_launch_tickets` CAS + `child_commands` + `run_links` + 父 Run waiting → `ChildRunHandle`。`child_runs.py:4` docstring：「Ticket-only child launch and restart contracts.」

共用且不能碰：`AttachmentPolicy`（`children.py:51`）、`ProfileLaunchTicket` / `ProfileLaunchTicketState`（:44,72）、`ChildCommandRecord`、`ChildLaunchResult`、`ChildSignal*`、`ChildTerminal*`、`child_launch_fingerprint`、四张 child 表、`read_child_attachment_policy` 等 uow 方法、`ChildSignalRuntime`。

正确边界：删 `workflow_launch_ticket_receipts` 分支（`child_commands.workflow_ticket_receipt_id` 一侧），保留 `profile_launch_tickets` 分支；`is_workflow_spawn_child` 可整删。

## 4. 公共 API 面

- `simple_harness/__init__.py` 不导出 workflow 符号。
- `simple_harness/runtime/__init__.py` 导出：`:284-289` ChildLaunchRequest / ChildRunHandle / ChildRunUnitOfWork / ProfileLaunchTicketRef（保留）；`:336-341,441,584,693,711` WORKFLOW_DRIVER_IMPLEMENTATION_FINGERPRINT / WORKFLOW_DRIVER_KIND / WorkflowRuntimeDriver / build_workflow_runtime_driver。
- 冻结快照 `tests/unit/contracts/public-api.json` 含 `WorkflowRuntimeDriver` 与 `build_workflow_runtime_driver`；`test_public_api.py:16` 逐符号断言，`:31-61` 五组「只增不减」兼容断言（0.7.1/0.7.2/0.7.3/0.7.4/0.7.7）。删这两个符号会打红且属公开 API 破坏。
- Host 侧 `from simple_harness ...` 含 workflow 的 import：`sdk_adapters/conformance.py:70,94-109`、`sdk_adapters/workflows.py:12`、`personal_catalog.py:9`、`product_workflows/*`、`workflows/definitions/sdk_v2/ppt_pro.py:9`、`sdk_v7/deep_research.py:14`、`capability_catalog.py:25`（`WorkflowProfileRecord`）、若干测试。Host 没有 import 任何 `runtime.workflow_spawn.*` / `WorkflowRuntimeDriver` / `WORKFLOW_DRIVER_KIND` / `build_workflow_runtime_driver`。
- 硬耦合：Host `composition.py:415` `build_runtime(..., workflow_runner=workflow.runner)`（`workflow_factory` 为 None 时 runner 为 None，但 kwarg 仍传）。`simple_harness.workflow`（DAG 引擎）与 `simple_harness.workflows`（官方定义）是 Host 活依赖，与 spawn 线是两件事：删 spawn 线可不动 `workflow/runner.py`，只删 `runtime/drivers/workflow.py` 这座桥。

## 5. 测试

- 只测 spawn 线（可整删）：`tests/integration/execution/test_workflow_launch_admission_h16.py`（8131 行 / 85 用例）、`tests/unit/runtime/test_workflow_spawn_contracts.py`、`tests/integration/runtime/test_workflow_cancel_kernel.py`、`tests/integration/execution/test_workflow_spawn_sqlite.py`。
- 必须保留（ticket child 路径）：`test_ticket_generation.py`、`tests/runtime/test_child_restart.py`、`test_runtime_h12.py`、`test_child_terminal_h12.py`、`test_atomic_child_launch.py`、child signal 各文件。
- 混合需改：`tests/conformance/test_full_runtime_seam.py`、`tests/integration/runtime/test_context_checkpoint.py:113`（字符串 `name="workflow_spawn"`）、`tests/unit/contracts/test_public_api.py` + 7 个快照、`tests/artifact/test_exact_wheel_consumer.py:176-208`、`src/simple_harness/testing/suites/workflow.py:32-35`、`pyproject.toml:98-99`（markers）、`:112-140`（mypy files 白名单）。
- 基线：仓内没有 0.7.x 全量 pytest 基线记录（最近 `plans/2026-09-07-nullable-tool-schema/RESULTS.md` 只跑 scoped 批次）。动手前需自建基线。

## 6. 最可能打坏 ReAct 主链的 5 个耦合点

1. `RuntimeStartAdmission` / `admit_runtime_start`（`orchestration.py:1190,1419-1440`；`uow.py:14571`；`kernel.py:530` 让 `RuntimeUnitOfWork` 继承 `WorkflowLaunchTicketPort`）是所有 Run 的启动准入，名字带 Workflow 但语义是 Runtime start。
2. `react.py:139-149` 的 `child_wait` 块与 `:150` 起的 continuation 主路径黏在同一个 `if invocation.continuations:`（:135）分支里。
3. `TerminationState` 四个 workflow 字段在 ReAct checkpoint 的 canonical JSON 里，删字段 → `checkpoint_hash` 全变 → 存量 checkpoint 被判 corrupt（`uow.py:13228-13236`）。
4. `ReactCheckpointPort` 两个 spawn 方法（`kernel.py:327-346`）被 `react.py:143`、`react_loop.py:845` 直接调用；Protocol 是 `@runtime_checkable`。
5. `tools/contracts.py:112` `ToolContext.workflow_spawn_context` 字段位置在 `call_id` / `effect_id` 之前，删字段会改变位置参数顺序。

附加：`schema.py:12,70-74` 描述符 checksum 连锁；`public-api.json` 只增不减断言；Host `composition.py:415` 的 `workflow_runner=` kwarg。

## 一句话边界

可安全删：`runtime/workflow_spawn.py`、`runtime/drivers/workflow.py`、`kernel.py:295-306 / 370-527 / 1381-1462 / 3351-3444` 及三个 `_workflow_*` dict、`orchestration.py` 的 WorkflowSpawn* / WorkflowCatalog* / WorkflowLaunchTicket / Request / VerifiedWorkflow* 族、`uow.py` 约 20 个 spawn 方法、7 张 `workflow_*` 表、4 个 spawn-only 测试文件。
必须保留：`profile_launch_tickets` 表与 4 个方法、`child_runs.py` / `child_coordinator.py`、`children.py` 全部 child 契约、`orchestration.py` 的 RuntimeStart* / RuntimeActivationClaim / StartInputSchema / ProfileDescriptor / WorkflowProfileRegistration / WorkflowCatalogProfileBinding、`simple_harness.workflow` 与 `simple_harness.workflows` 两个包。
