# 调查记录：Host 仓 workflow 线删除边界（2026-09-09，只读映射）

> 调查仓库 `simple_harness`，基线 `03de5052`。由调查子代理产出，本文为归档摘录，file:line 均为该基线上的位置。

## 0. 一句话结论

`backend/deskpet/workflows/` 不是可整包删除的目录，它同时装着三种东西：

| 层 | 内容 | 处置 |
|---|---|---|
| A. 图引擎 / 产品图 | runner / service / launcher / native / definition / definitions / adapters / evaluation / replay / recovery / ipc / progress / human / outbox / control / routing / proposal_state / output_contract / terminal_projection / delivery / retention / startup_recovery / runtime_adapters / execution_ports / trace/observer | 可删 |
| B. 主对话共享内核 | `contracts.py`、`effects.py`、`errors.py`、`deadlines.py`、`trace/`（除 observer） | 必须留 |
| C. 耐久账本 | `store/`（`execution_uow.py` / `schema.py` / `run_store.py` / `write_lane.py`） | 必须留 |

A、B、C 在 import 上纠缠：删 A 会连带打断 B / C 的 import 链（见 §1.3）。

## 1. 依赖图

### 1.1 `deskpet.workflows.*` 的外部 import（非 tests）

| 文件:行 | 导入符号 | 归属 |
|---|---|---|
| `backend/agent/agent_loop.py:70` | `workflows.trace`: SpanContext / SpanKind / SpanStatus / TraceStore | 保留侧 |
| `backend/agent/workflow_trace.py:16` | `workflows.trace` | 保留侧 |
| `backend/agent/harness_feedback.py:10` | `effects.ToolOutcomeState` | 保留侧 |
| `deskpet/capabilities/platform.py:45-46` | `contracts.EffectKind/EffectPolicy`；`effects.NormalizedToolOutcome` | 保留侧 |
| `deskpet/capabilities/tool_proxy.py:11` | `effects.NormalizedToolOutcome` | 保留侧 |
| `deskpet/harness/ports.py:20`、`runtime.py:27`、`tool_executor.py:39`、`drivers/react_boundary.py:29` | `effects.*` | 保留侧（ReAct driver） |
| `deskpet/permissions/runtime.py:21`、`sdk_adapters/tool_authority.py:63` | `effects.PreparedToolCall` | 保留侧 |
| `deskpet/tools/registry.py:87,522,613,742,3901` | `effects.*`、`contracts.EffectKind/EffectPolicy/ToolAccess/ToolInventoryEntry` | 保留侧（工具注册表核心） |
| `deskpet/tools/orchestration_controls.py:327` | `contracts.EffectKind/EffectPolicy` | 混合（文件留，spawn 段删） |
| `deskpet/companion/turn_authority.py:50-52` | `workflows.definitions.personal_workflow.personal_workflow_query_hash` 等 | 穿透：主对话 turn authority 依赖图定义包 |
| `deskpet/sdk_adapters/workflows.py:24-25` | `definitions.sdk_v2.ppt_pro`、`definitions.sdk_v7.deep_research` | workflow 线 |
| `deskpet/tools/research_tools.py:56,1781,2070,2534` | `definitions.deep_research_v5_contracts`、`definitions.research_core` | workflow 线 |
| `deskpet/quality/corpus_scoring_session.py:267,327-330` | `workflows.bootstrap.build_workflow_service` | 质量脚本，需改 |
| `backend/scripts/calibrate_deep_research_v6.py:26-29` | 图引擎符号 | workflow 线脚本 |

零引用确认：`deskpet/execution/`、`task_scope/`、`memory/`、`product_state/`、`session/`、`agent/`（除 `context_task.py` 鸭子类型 `WorkflowProjectionAdapter`）对 `deskpet.workflows.*` 没有 import，它们只经 `deskpet.execution.uow_ports` 协议 + `service_context["workflow_service"].execution_uow` 拿实现。

### 1.2 被点名模块

- `contracts.py`（471 行）必须留：导出 EffectKind / EffectPolicy / ToolAccess / ToolInventoryEntry / JsonValue / canonical_json / NodeExecutionIdentity。仅 workflow 线用的部分：WorkflowRunStatus / NodeStatus / WorkflowState / StatePatch / ChannelSpec / ReducerKind / WorkflowContext / RetryPolicy（`:65-99,118,168,292,327`）。
- `effects.py`（3066 行）必须留；`:26` 依赖 `deadlines`，`:27` 依赖 `store`（RunFence / StaleRunFence / initialize_workflow_db）。
- `errors.py` 必须留（`store/checkpointer.py:98,100`、`store/run_store.py:15`、`store/research_repository.py:23`、`contracts.py:17`）。
- `deadlines.py` 必须留（`effects.py:26`）。
- `lease.py` 纯 workflow 线（仅 `runner.py:48`）。
- `outbox.py` 几乎纯 workflow 线，穿透一条：`store/checkpointer.py:1718` import `stable_delivery_id, stable_event_id`。
- `control.py`、`execution_ports.py`：图引擎；但 `store/checkpointer.py:19`、`store/run_store.py:16` 依赖 `execution_ports.CheckpointExecutionAdapter`。
- `store/` 内部：`execution_uow.py` 只依赖 `.schema` / `.run_store`（RunFence）/ `.write_lane` / `deskpet.execution.contracts`，不依赖图引擎；`store/checkpointer.py` 依赖 `..execution_ports`(19)、`..errors`、`..contracts`、`..human`(624,2322)、`..native`(947,1297,1956,2179)、`..outbox`(1718)，是 store 与图引擎的主耦合点；`store/research_repository.py:1498-2124` 懒 import deep_research_v6；`store/__init__.py:5,17,19,31` eager 导出 checkpointer / run_store / execution_uow / checkpoint_execution。
- `trace/`：`agent_loop.py:70` 用 context / models / store（留）；`observer.py` 只被 calibrate 脚本用（可删）。

### 1.3 三条穿透边界

1. `effects.py:27` → `store/__init__.py` → `store/checkpointer.py` → `..human` / `..native` / `..outbox`。
2. `companion/turn_authority.py:50` → `workflows.definitions.personal_workflow` → `..definition`(26) → 整个图编译器。
3. `workflows/bootstrap.py:11-15,55-62` 一次性注册 v1..v7 全部图，同一函数里构造 `SqliteExecutionUnitOfWork`（`bootstrap.py:45`）。

### 1.4 `ProfileLaunchTicket` 是两个不同的类

- `deskpet/harness/execution_profiles.py:94`（Host dataclass）：`ProfileLaunchTicket` / `WorkflowSpawnRequest` 生产零引用；`ExecutionProfileDescriptor` / `LaunchPolicy` 被 `harness/profiles.py:16` 用。
- `deskpet/execution/contracts.py:1924`（账本契约，`ProfileLaunchTicketState` 在 :378）：被 `child_runs.py:14`、`execution_uow.py:75-76,13874-14713`、`execution/ports.py:166-192`、`execution/uow_ports.py:51,596-634` 使用。

`ProfileRegistry` / `ProfileSpec`（`harness/profiles.py`）不能整删：`harness/kernel.py:82,133-154,387-395` 用它解析 root profile。`harness/router.py` 生产零引用（`kernel.py:150` 允许 `router=None`），可删。

### 1.5 `child_runs.py` 的 ticket 分支

`ChildRunCoordinator` 被 `harness/kernel.py:60,136,191,201,1207-1210`、`harness/runtime.py:32,64,476-477`、`harness/reconciler.py:10` 使用，保留。仅 ticket 分支可删：`child_runs.py:14,57,78-79,80-170`、`:337-345`、`:407-412`；上游 `harness/ports.py:203-233`（`DelegateRun(profile_launch_ticket_ref=, profile_launch_request=)`）、`harness/drivers/react_boundary.py:405-409,420`。

### 1.6 `workflow_spawn` 工具的登记面

| 位置 | 内容 |
|---|---|
| `deskpet/tools/orchestration_controls.py:15,22,89-173,176-213,324-353,572` | 常量、schema、资源解析器、注册；`register_orchestration_controls` 生产无调用者（仅 `tests/capabilities/test_failure_receipts.py:273`、`tests/sdk_adapters/test_tool_catalog.py:280`） |
| `deskpet/tool_catalog/real_tool_manifest.json:7218`（tools[73]）+ `:7180,7200,7225,7231,7310` | 冻结 spec：`handler_id: deskpet.tools.orchestration_controls:_fail_closed`，`stable_handler_id: core.workflow_spawn.v1`，`dispatch_kind: delegate_control`，`execution_build_identity.artifacts` 钉了 `orchestration_controls.py` 的 sha256 |
| `deskpet/tool_catalog/manifest.py:152-157` | `_migrate_schema` 对 `workspace_ref` 的专项迁移 |
| `deskpet/tool_catalog/providers.py:37` | `_CONTROL_TOOLS` |
| `deskpet/capabilities/manifest.py:98` | `CORE_RESERVED_TOOL_NAMES` |
| `deskpet/sdk_adapters/tool_authority.py:173` | `SDK_DIRECT_TOOL_KERNEL` |
| `deskpet/sdk_adapters/tools.py:42` | 77 名冻结列表 |
| `deskpet/tools/prepared_snapshot.py:28`、`deskpet/tools/skill_tools.py:314` | `_SKILL_SCOPE_WIDENING_CONTROLS` |
| `deskpet/agent/turn_preparer.py:293,523,543` | system 提示词 + `core_names` + deny_selectors 注释 |
| `deskpet/companion/turn_authority.py:722` | personal_v1 提示词 |
| `agent/agent_loop.py:4497-4530` | 排他工具拆批按 `completion_semantics == "accepted_async"`（不是按名字） |

## 2. `main.py` 旧启动器

全部在 `lifespan`（`main.py:3380`）内的一个 try/except（`3563` – `5006`）：

| 行范围 | 内容 | 处置 |
|---|---|---|
| 3564-3571 | `build_workflow_service` / `RetentionPolicy` / `RegisteredBlobStore` import | 混合 |
| 3573-3730 | delivery handler 闭包 + `ProductDeliveryAdapter` | 删 |
| 3732-3748 | `await build_workflow_service(...)` | 改造：保留 execution_uow 构造 |
| 3749-3786 | 图定义 import | 删 |
| 3788-3789 | `WorkflowLauncher` | 删 |
| **3792** | `service_context.register("workflow_service", ...)`：全产品唯一 execution_uow 持有者 | 必须保留键或换键 |
| 3793-3806 | `HarnessPublicReadService` | 保留 |
| 3807-3833 | fence / `ProviderInvocationCoordinator(_workflow_service.execution_uow)` | 保留（依赖 uow） |
| 3834-3905 | provider workload router（`start_snapshot_reader=_workflow_service.execution_uow`） | 保留 |
| 3907-4600 | deep research v1..v7 context/state 工厂 | 删 |
| 4601-4653 | `register_adapter("deep_research", v1..v7)` | 删 |
| 4655-4937 | code / durable_task 工厂与注册 | 删 |
| 4938-4958 | personal runtime adapter | 删 |
| 4960-4968 | starter 置空 + `_configure_ppt_production` | 删 |
| **4969-4995** | `activate_and_recover_workflows`（启动恢复） | 删 |
| 4996-5006 | except：置 None | 改造 |
| 5007 | `_expire_ppt_outline_dangling_for_startup` | 删 |

另：`main.py:6904-6991`（PPT adapter / production）、`6152-6198,6773-6883`（PPT outline WS 分支）、`17079-17110`（`workflow_*` WS IPC 分发 → `workflows/ipc.py`）、`9948-9990`（`_try_resolve_execution_workflow_decision`，保留侧）。

`workflow_service` 消费点约 35 处：只要 `.execution_uow` 的（保留）`1301-1303`、`1919-1920`、`2696-2735`（无则 RuntimeError）、`6576-6584`、`6628-6640`、`8187`+`8372`、`9954-9974`、`10074-10082`、`11297-11300`（SDK Runtime 激活，无则 RuntimeError）、`11591`，及 `deskpet/tools/code_tools/spawn_subagents_tool.py:112-126`；真正要图能力的（可删）`2111-2112`、`2454-2455`、`6191-6198`、`6985-6991`、`11312`、`15581-15594`、`15811-15817`、`17083-17110`、`11624`、`11635-11645`。

启动时扫 workflow.db 四处：`bootstrap.py:44`（initialize）、`bootstrap.py:72-88`（`_activate_foundations`）、`main.py:4969`（`startup_recovery.py:7-42`）、`main.py:11295-11330`（legacy drain）。

## 3. 冻结清单机制

- `manifest.py:21-30` `canonical_hash`；`:15` 常量；`:55-86` `load_tool_manifest` 同时校验 JSON 内嵌 `manifest_sha256` 与常量（两处必须同步改）；`:66-73` `pre_cutover_count==79`、`tool_count==77`；`:74-79` workflows 投影必须恰好两键。
- `migrate_tool_schemas` `:187-231` 读 `schema_migrations.json`，校验其 `manifest_sha256`（:190）与 `workflow_spawn` 专项迁移（:152-157）。
- 没有再生成脚本，必须手工重算。
- 计数断言：`providers.py:793`（77）；`tests/sdk_adapters/test_tool_catalog.py:12-14,137-142,158,168-180,182,262,280,283,425-430,445,585-588,774-780,978`；`tests/companion/test_skill_runtime_snapshot.py:594-595,616`。

## 4. 前端

- 可整删：`components/workflows/`（RunInspectorPanel / WorkflowGraph / RunList / TraceTree / CheckpointTimeline / ReplayDialog / EvaluationPanel / DecisionPanel / types）。用户可见入口：`Sidebar.tsx:222-227`「更多」里的 ContextTrace 按钮 → `App.tsx:895,545,958-960` → `ContextTracePanel.tsx`（混合：`:6-8` import 面板、`:99,115,256,265,276-277,297` 发 workflow IPC、`:373` `decisions_list` 保留）。
- 混合谨慎：`components/workflow/WorkflowProgressGroup.tsx` / `ActivityTimeline.tsx` / `DurableTaskSteps.tsx`（`HarnessInspectorPanel.tsx:8-9,321,329` 也挂载，保留侧）、`workflowFinalAssistant.ts`、`code-panel/controlWs.ts:122-135,1282-1298,1674-1707,1737-1845,1913,2217`、`stores/sessionsStore.ts:47-48,213-239`、`chat/messageVisibility.ts:10-18`（`workflow_progress` / `workflow_stage` role 被当前 ReAct reasoning summary 复用）、`views/ChatView.tsx:115,140,441-529`、`PermissionPopup.tsx:114`、`hooks/usePermissionRequests.ts:252`。
- PPT：`code-panel/PPTOutlineCard.tsx`（`MessageBubble.tsx:93,142`、`MessageStreamPanel.tsx:49,721`）。
- 保留：`stores/harnessPublicSnapshotStore.ts`、`types/messages.ts` 的 `workflow_step_id` / `workflow_facts`（execution 账本 public read model）、subagent 面板。
- IPC：`workflow_runs_list` / `workflow_run_detail` / `workflow_decision_resolve` / `workflow_delivery_retry` / `workflow_delivery_discard` / `workflow_checkpoint_fork` / `workflow_evaluation_submit` / `workflow_run_action` / `workflow_run_retry_from_start` 及对应响应与推送；无 HTTP 路由。

## 5. workflow.db

- 建库唯一入口 `store/schema.py:231 initialize_workflow_db`（`effects.py:27` 等都调它）；87 张表：workflow 线表（`workflow_*`、`eval_*`；`trace_runs` / `trace_spans` 例外，agent_loop 的 TraceStore 仍写）、保留账本 `execution_*`（52 张）+ `workflow_schema_migrations` + `task_grants`。
- 边界表 `execution_profile_launch_tickets`（`schema.py:1753-1900,3119-3200`；`execution_uow.py:9300,10109-10110,13874-14713`）：属于 spawn 线但住在保留命名空间，`inspect_harness_run` 会 JOIN 它。
- 删线后 DB 文件保留不动是安全的：`initialize_workflow_db` 版本单向；`harness_public_read_service.py:865` 有 `workflow_runs` 存在性守卫；`project_session_reset.py:121-129,224-238` 按前缀清表；`:145,259-263` 会 rmtree `workflows/blobs`（若删 `RegisteredBlobStore` 需同改）。
- 风险：`store/schema.py:529` 升级期硬失败「pre-v4 deep_research/v6 continuation」；`:1085,2231-2243,2905-2937,3850-3895` 迁移验证若引用被删定义需确认。

## 6. 测试

- 可整删：Deep Research 约 40 个、PPT 16 个、workflow 引擎约 50 个（清单见调查原文）；`tests/companion/test_workflow_pack_adapter.py`。
- 混合需改：`tests/conftest.py:18`；`tests/sdk_adapters/test_tool_catalog.py`（计数/哈希/投影）；`tests/test_deskpet_agent_loop.py:129-274`；`tests/test_p5s2_sse_diagnostic.py:220,254,280,312`；`tests/companion/test_skill_runtime_snapshot.py:594-616`；`tests/companion/test_personal_workflow.py`；`tests/capabilities/test_failure_receipts.py`；`tests/session/test_project_scoped_sessions.py:25`；`tests/sdk_adapters/test_product_workflows.py:19`、`test_capability_catalog_adapter.py:251`、`test_product_host_ports.py:1466`；`tests/test_context_attempt_inventory.py:47`；`tests/quality/c08_retained_main_child.py`；`tests/test_workflow_{contracts,effects,deadlines,store_schema,tool_registry_effects,trace_store}.py` 实为保留侧测试。

## 7. 脚本与文档

- 可删脚本：`backend/scripts/calibrate_deep_research_v6.py`、`e2e_ppt_deepresearch.py`、`spike_deepresearch_baseline.py`、`scripts/eval_workflows.py`、`workflow_eval_adapter.py`、`test_workflow_tool_integration.py`、`scripts/acceptance/deepresearch_*`、`ppt_pro_smoke.py`。
- 需改脚本：`backend/scripts/generate_harness_public_fixture.py:169`、`backend/scripts/perf/x2_memory_lanes_growth.py:364`、`scripts/acceptance/*`、`scripts/bench/harness_baseline.py`、`scripts/native/*`。
- 文档：`ARCHITECTURE/DeepResearch.md`、`PPT.md`、`DEEP_RESEARCH_AGENT_REACH.md`、`SEARCH_GATEWAY_DEEPRESEARCH.md`、`AGENT_HARNESS.md`（`:1657 ## ReAct 与 Workflow 的关系` 等）、`ARCHITECTURE.md`（`:934 ## 4` 等）、`SDK_EXTRACTION.md`、`COMPANION_GROWTH.md`、`PROJECT_STATUS.md`、`docs/search-and-deepresearch.md`、`docs/legacy-deskpet-README.md`、`docs/CODE-WORKFLOW.md`。

## 8. 最可能打坏主对话的 5 个耦合点

1. `service_context["workflow_service"]` 是唯一 execution_uow 持有者（`main.py:3792`；`2696-2735`、`11297-11300`、`3824`、`3876` 都吃它）。必须先把 `SqliteExecutionUnitOfWork` 从 bootstrap 剥出来单独构造，再删图。
2. `effects.py:27` → `store/__init__.py:5` eager 导入 checkpointer → 懒 import human / native / outbox。删这三个文件而不先摘 checkpointer，`import deskpet.tools.registry` 即 ImportError。
3. `companion/turn_authority.py:50-52` 依赖 `definitions/personal_workflow` → `definition`（图编译器）。
4. `agent_loop.py:4497-4530` 排他拆批按 `completion_semantics == "accepted_async"`，`tool_activate` 也靠它；只删名字，保留机制。
5. `projection_kind="workflow_progress"` 被当前 ReAct 的 reasoning summary 复用（`run_presenter.py:902`、`session_db.py:90,104`、`messageVisibility.ts:18`、`controlWs.ts:1792-1800`）。按名字清理会让思考摘要气泡消失。

次级：`sdk_adapters/workflows.py` 产出的 resource_records 喂 `SdkRunToolAuthorityRegistry`（`main.py:8213-8223`），需确认 `workflows=()` 时 `capability_catalog.py:343-397,678-698,907-951` 不炸；`workflows/harness_delivery.py:17-45` 的 `workflow_event_envelope` 被 `main.py:11587` 用于历史 hydrate（保留），`:47+` 死代码；`deskpet/companion/workflows.py` 生产零引用，可整删。
