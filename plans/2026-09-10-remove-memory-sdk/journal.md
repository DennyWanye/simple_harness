# journal · 删记忆 SDK（2026-09-10）

> 被测 HEAD：Host 仓 `main`，基线 `f8363463`（= 备份分支
> `backup/pre-agent-orchestration-2026-09-10`）。SDK 仓（simple-harness-sdk /
> simple-harness-memory-sdk）**未动**。本轮未 push。

## 1. 范围与决定

用户决定：把 **simple-harness-memory-sdk**（Python 包名 `simple_harness_memory`，
Host 内叫「记忆 SDK / 认知记忆 / P4-S13 记忆系统」）从 Host 主分支彻底去掉，
让 Host 在**不安装该包**的情况下能启动、主对话流程（Primary chat → SDK ReAct Run
`agent.general`）仍能跑。这是为后续「Agent 编排层」大改减少干扰的**临时性清理**，
不是架构定论——记忆能力预计在编排层大改后以新形态回来。

### 1.1 与任务书的一处口径修正（重要）

任务书写的是「`backend/deskpet/memory/`（整个包 91 文件，删）」。**复核后没有照做**，
理由是复核结果与那个前提不符：

- `deskpet/memory/` 里只有 **28 个**模块 import `simple_harness_memory`；
- 其余 63 个是**纯 Host 模块**，并且被主对话流程直接依赖：
  `writer_fence`（15 个外部引用）、`trusted_disclosure`（9）、`primary_visibility`（9）、
  `human_memory_program`（9）、`human_memory_service`（8）、`schema` / `schema_chain` /
  `migrator`（state.db 迁移链）、`session_db`（Host 会话账本）、
  `primary_message_v2/v3`、`primary_message_evidence`、`recovery_work_items`、
  `primary_tool_causality` 等，引用方是 `deskpet/execution/foreground_queue.py`、
  `primary_history.py`、`primary_context.py`、`evidence_ingress.py`、
  `semantic_closure.py`、`admission_rejection.py`、`deskpet/task_scope/*`；
- 这里的 "human memory" 指的是 **Host 的前台会话程序（HM program）**，不是认知记忆 SDK。
  包名 `deskpet/memory` 是历史遗留。

整包删掉会把主对话流程本身删掉，与「主对话仍能跑」的硬要求直接冲突。故改为：
**只删依赖 `simple_harness_memory` 的部分，保留主对话流程需要的 Host 底座**，
并把 `deskpet/memory/` 从 91 个 `.py` 收敛到 43 个。SQL 迁移链
（`deskpet/memory/migrations/`，含 `primary/` `procedure/` `s5c/` 三个子目录，共 49 个
`.sql`）**原样保留**——旧 userdata 的 schema 链不能断（执行过程中一度误删，已即时
`git reset` + `git checkout` 恢复，见 §6 事故 1）。

## 2. 提交

| # | sha | 内容 |
|---|-----|------|
| 一 | `4b4dfba2` | 后端脱钩：空记忆端口 + main.py / p4_ipc / 冻结清单 + 删 118 个 `.py` |
| 二 | `8d6c6ddf` | 删记忆专属测试、修混合目录测试、PERSONA 去记忆 |
| 三 | `4087be65` | 前端记忆面板与嵌入器状态卡下线 |
| 四 | `2012f1f6` | 修 execution/operation_audit 侧测试 + 一处被提速暴露的关停竞态 |
| 五 | `fb08f475` | 依赖与 vendor 清理、服务槽位、剩余测试导入、文档回写、本 journal |

相对基线合计：**375 个文件变更，322 个文件删除，-72490 行**。

## 3. 删了什么

### 3.1 模型可见面（冻结工具清单）

| 项 | 前 | 后 |
|---|---|---|
| `real_tool_manifest.json` `tool_count` | 76 | **71** |
| `MANIFEST_SHA256` | `df979c0e0441…790bc4` | **`57900a1d4d7b24091b6e04cac4cd3b441e3ea15385134f262b10ed368ba4d02b`** |
| `schema_migrations.json` migrations | 70 | **66** |
| migrations `closed_object_paths` 合计 | 71 | **67** |
| `SDK_DIRECT_TOOL_KERNEL` | 19 | **16** |
| `PRODUCT_TOOL_NAMES` | 83 | **75** |
| `HOST_COMPOSED_TOOL_NAMES` | 7 | **4** |
| 静态 handler（清单减去 dynamic） | 63 | **60** |

清单删的五条：`memory_forget` / `memory_read` / `memory_recall` / `memory_search` /
`memory_write`。宿主组合的三条（不在冻结清单里）：`procedure_use` /
`procedure_discover` / `prospective_ack`。
守卫同步点：`tool_catalog/manifest.py`（哈希 + 两处 71）、
`tool_catalog/providers.py`（注册数 71 + `_ASYNC_TOOLS` 去名 + 删 `memory_forget`
构建期描述投影与六个稳定错误码）、`sdk_adapters/conformance.py`（inventory 71 +
删两个记忆 provider 桩）、`sdk_adapters/tool_authority.py`、`sdk_adapters/tools.py`。

**没有误删** `context_route` / `task_scope_update` / `task_scope_search` /
`context_page_in` / `workspace_recall`（task scope 与 Context 线）。判据是该工具的
provider 是否 import `simple_harness_memory`。

### 3.2 `context_route`：五路由 → 四路由

删 `memory_standalone`（类型化召回）与事件 V 的「争议确认」探针
（`_probe_recall` / `_contested_notice` / `_admitting_surface`）——后者唯一的判据
是类型化召回返回的冲突组。`contested` 参数保留为恒定的 `_NO_PROBE`，审计投影因此
**回到事件 V 之前的形状**，而不是每轮记一条假的 `"unavailable"`。
schema 去掉 `memory_types` / `include_short_horizon` 两个字段。

### 3.3 装配面（`backend/main.py`）

删：`_initialize_product_memory`（`MemoryManager.build_production` + WeMM embedder）、
`memory_recall` provider 注册、`OfficialMemoryFactsSurface`、
`compose_human_memory_runtime` / `HumanMemoryV7Runtime`、Procedure 与 Prospective 组装、
`ProductTypedContextUseAuthority`、记忆分析 lane（`HostEvidenceAuthority` +
`HostMemoryAnalysisExecutor` + `MemoryIngestionOutboxWorker` + `MemoryAnalysisLane`）、
`_primary_history_visibility_checker`、`_primary_suppression_resolver`、
`_occurrence_terminal_hook`、`verify_memory_candidate` 调用。
`sdk_typed_context_use_authority` 退出 `SDK_COMPOSITION_SLOTS`（它在下游本来就是
Optional：旧库 `user_version < 35` 时同样是 None）；`sdk_evidence_authority` /
`sdk_memory_analysis_executor` / `sdk_memory_ingestion_outbox` 三个槽位一并退出。

### 3.4 IPC 与前端

`p4_ipc.py` 删十条消息：`memory_search`、`memory_l1_list`、`memory_l1_delete`、
`memory_facts_list`、`memory_forget`、`memory_forget_undo`、`memory_pin`、
`memory_unpin`、`embedder_status`（+ 三个只服务它们的辅助函数）。
前端删组件 `MemoryPanel` / `PrimaryMemoryPanel` / `PrimaryMemoryGraph` /
`MemoryGraphCanvas` / `PrimaryAuditPanel` / `EmbedderStatusCard`，请求层
`cognitiveRequests` / `graphRequests` / `auditRequests` / `graphElements` /
`graphStyle`，`types/messages.ts` 删 16 个消息接口，入口去掉侧栏「记忆管理」与
主对话「记忆」抽屉。

### 3.5 依赖

`backend/pyproject.toml` 删 `simple-harness-memory-sdk==0.6.38` 与
`[tool.uv.sources]` 条目；`backend/vendor/` 删 **60 个** `simple_harness_memory_sdk-*`
（wheel + candidate manifest），harness / service 两个 SDK 的 wheel 原样保留；
`sdk_adapters/sdk_candidate.py` 删 `SDK_MEMORY_*` 六个常量、`sdk_memory_wheel_path`
与 `verify_memory_candidate`。

### 3.6 服务槽位（`backend/context.py`）

`_VALID_SERVICES` 与 `ServiceContext` 删八个已无任何 `register`/`get` 的槽位：
`memory_manager` / `memory_recall_query` / `memory_recall_scope_resolver` /
`memory_facts_surface` / `human_memory_v7_runtime` / `sdk_evidence_authority` /
`sdk_memory_analysis_executor` / `sdk_memory_ingestion_outbox`。
**没删**仍有 `register` 或 `get` 调用点的记忆相关名字（`embedder` / `vector_worker` /
`file_memory` / `memory_curator` / `memory_store` / `facts_store` /
`preference_memory` / `conversation_memory` / `memory_identity_*` /
`memory_display_invalidation` / `prospective_occurrence_coordinator` /
`sdk_typed_context_use_authority`）——多数是恒 `None` 的占位，删掉会让
`get()`/`register()` 抛 `"Unknown service"`（2026-05-30 与 2026-06-05 两次事故的形态）。
清理后单独做了一次冷启动冒烟（smoke3，端口 18129）：5 s 到 `startup complete`，
`Traceback / RuntimeError / Unknown service / p4_services_registration_failed`
计数 **0**，`/health` → `"startup_errors":[]`。

## 4. 保留了什么，以及为什么

- **`deskpet/memory/` 的 43 个 Host 模块**：Host 会话账本 `SessionDB`、S1 证据链
  （`primary_visibility` / `primary_message_evidence` / `primary_message_v2,v3`）、
  前台队列与运行时依赖（`writer_fence` / `trusted_disclosure` /
  `human_memory_program` / `human_memory_service` / `recovery_work_items`）、
  schema 迁移链、`identity` / `display_invalidation` / `control_binding` /
  `primary_authority` / `primary_decisions` / `primary_workspace_bindings` /
  `primary_tool_causality` / `current_input_source,visibility` /
  `analysis_lineage` / `evidence_authority` / `procedure_recovery_schema` /
  `s5c_*_schema`（后两组是**迁移链**的 Python 侧，不是记忆运行时）。
- **`deskpet/memory/migrations/` 全部 49 个 `.sql`**：包括 `procedure/`（v53、v54）
  与 `s5c/`（v50–v52）。这些表在旧 userdata 里已经存在，迁移链断一环就打不开旧库。
  表不再被写，但 schema 必须留。
- **主流程前端**：`PrimaryChatView`、`PrimaryRunPanel`、`PrimaryTaskPanel`
  （task scope，不是记忆）、`PrimaryWorkspaceBindings`、`runChannel`、`requests`、
  `taskScopeRequests`、`controller`。
- **Harness SDK 自己的 port**：`conversation_memory`、`memory_outbox` 等是 Harness
  协议，未动；SDK 仓未动。

## 5. 空记忆端口的语义（`deskpet/sdk_adapters/null_memory_port.py`）

Harness SDK 0.7.10 的 `build_production_runtime` 把 `memory: AgentMemoryPort` 列为
**必填**，并在 `ProductionRuntimeConfig.__post_init__` 与 `RuntimePorts` 两处校验
`record_committed_turn` 可调用。`AgentMemoryPort` 协议只有三个方法。Host 新增
`NoMemoryAgentPort`：

| 方法 | 行为 | 为什么这样才诚实 |
|---|---|---|
| `recall_for_turn` | 返回 `MemoryRecallResult(status=EMPTY, item_count=0, payload={}, byte_count=2, write_fence=None)` | 按 SDK 数据类真实构造并通过其 `__post_init__` 全部校验；**不伪造** `write_fence`，因为没有任何写栅栏可持有；EMPTY 是「查了，什么都没有」的真值 |
| `release_recall` | 无操作 | 召回没有预留任何东西，确实没有要释放的 |
| `record_committed_turn` | 返回 `CommittedTurnReceipt(status=APPLIED)`，**不持久化任何记忆** | `APPLIED` 是本端口真实的语义：这一轮在本端口的保留策略下被完整应用，而该策略就是「什么都不留」。`CONFLICT` / `REJECTED_ERASED` 会让 SDK 的 `memory_outbox`（`execution/memory_outbox.py:413,420`）判定成真实存储拒写 → 把行 park 住并向用户暴露一个无从处理的记忆故障，那才是撒谎 |

id 从请求确定性派生（`no-memory:recall:{query_id}` / `no-memory:turn:{turn_id}`），
重试同一轮观察到同一张回执，与真实存储的幂等形状一致。
`memory_failure_policy` 保持 SDK 默认（`DEGRADE_RECALL_AND_RETRY_RECORD`）。

契约实测（`.venv/bin/python` 直跑）：
```
recall ok empty 0 2 None 8473f0de01ea
release ok
record ok applied no-memory:turn:t1
```

### 5.1 连带的两处「诚实」改写

1. **`PrimaryHistoryPolicy`**（`deskpet/memory/primary_visibility.py`）：原来由一次
   公开 Memory 可见性批（`HistoryVisibilitySnapshot`）逐条判定证据是否可见。现在
   **保留全部 Host 结构证明**（S1 证据对读回与承诺校验、依赖 DAG 的环/深度/边数上限、
   终态观测必须被 Host 终态身份表证实、终态的当前 USER 输入必须落在自己的依赖闭包里），
   遍历通过即可见——没有记忆系统就没有抑制权威。链路损坏、哈希对不上、终态未证实的根
   **依然不可见**。`current_user_denial` 恒返回 `None`（「没有被证明的拒绝」，
   而不是拿遍历失败伪造一次拒绝）。`assert_source_admissible` 变成有文档的 no-op
   （保留为编排层大改后重新引入准入权威的落点）。`inline_evidence_limit()` 返回
   `None`（调用方本来就有「无上限」分支）。
2. **PERSONA**（`deskpet/execution/primary_context.py`）：删掉整段告诉模型
   `memory_standalone` 类型化召回、`procedure_discover` / `procedure_use`、
   `procedure_hint` / `trigger_local` / `conflict_notice` 三个 Host 提示字段用法的文字，
   以及 `REMINDER_CAPABILITY`（承诺「一次性时间提醒可以由后台记忆工作流处理」）。
   那些工具与字段一条都不存在了，留着就是对模型撒谎。换成一句能力边界声明：
   「本构建没有长期记忆：更早会话的东西既不存储也不可检索，也没有召回工具；
   当答案本该依赖用户已存事实/偏好/既往约定而它们不在当前上下文里时，直说并询问，
   不要用通行惯例替代用户自己的约定，也不要声称记得。」
   PERSONA 3908 → **2385** 字符，仍在 8192 tier 预算内。

## 6. 执行过程中的两处事故（如实记录）

1. **误删 SQL 迁移链**：`git rm -rq deskpet/memory/migrations` 时把 45 个条目
   （含 41 个 `.sql` 与三个子目录）一起删了。原因是静态依赖分析把
   `deskpet.memory.migrations` 判成「无 Python 引用的孤儿包」——它确实没有 Python
   importer，SQL 是按路径读的。立即 `git reset -q HEAD <path>` + `git checkout --`
   恢复，并在之后逐条核对「69 个删除全部是 `.py`」。
2. **一处被提速暴露的关停竞态（生产代码真修复）**：
   `deskpet/execution/foreground_runtime.py::close()`。`_drive_with_lease` 在租约
   保持器先完成时 `await keeper`，而保持器可能是被 `_stop_lease_keeper` **取消**的
   （关停竞态），`CancelledError` 于是穿过 `_run_driver` 从 `close()` 的
   `asyncio.shield` await 抛给调用方，并**跳过 `close_current_lease`**。这是既有的
   潜在竞态；记忆移除让历史读快了一个量级（不再每次读都走公开 Memory 批），时序变化
   把它变成稳定可见的 flake（`test_primary_none_routes_to_exact_task_and_writes_real_file`
   基线绿、改后 3 次跑 2 次红）。修复：只有 `close()` 自身被取消（此时 driver task
   尚未结束）才向外传播，否则继续做租约清理。修复后连跑 5 次全绿。

## 7. 验证

### 7.1 冷启动冒烟

| 轮次 | 条件 | 结果 |
|---|---|---|
| smoke1 | 临时 userdata、端口 18127、`DESKPET_DEV_MODE=1`，**包仍装着** | 4 s 到 `startup complete`；`Traceback / RuntimeError / Application startup failed` 计数 **0**；`/health` → `"startup_errors":[]` |
| smoke2 | 临时 userdata、端口 18128，**`uv sync` 卸载记忆 SDK 之后** | 4 s 到 `startup complete`；同上 0 条；`/health` → `"startup_errors":[]` |
| smoke3 | 临时 userdata、端口 18129，**`context.py` 删八个服务槽位之后** | 5 s 到 `startup complete`；加查 `Unknown service` / `p4_services_registration_failed` 也是 0；`/health` → `"startup_errors":[]` |

冷启动日志里只有两条非致命告警：`model_provision_failed`（临时 userdata 无模型，
预期）与 `assembler_unavailable_binding_sdk_skill_reader_only`（与记忆无关）。
未触碰 `~/Library/Application Support` 下的真实用户数据。

### 7.2 依赖

```
$ uv sync --extra dev
Resolved 415 packages in 2.71s
Uninstalled 1 package in 13ms
 - simple-harness-memory-sdk==0.6.38 (from file:///…/vendor/simple_harness_memory_sdk-0.6.38-py3-none-any.whl)

$ .venv/bin/python -c "import simple_harness_memory"
ModuleNotFoundError: No module named 'simple_harness_memory'
```

`uv.lock` 里 `[[package]] name = "simple-harness-memory-sdk"` 条目已删（`-43` 行）。
**遗留一处非 Host 引用**：`uv.lock:7784` 是 `simple-harness-service-sdk` 自己的
`[package.metadata].requires-dist` 里一条 `extra == 'memory'` 的可选依赖
（指向 memory-sdk **0.5.2** 的 GitHub release）。那是 service SDK 的包元数据，
Host 只装 `[realtime]`，不会引入它；要清掉得改 service SDK 仓，本轮不动（见 §9 L-3）。

### 7.3 冻结清单守卫自检

```
$ .venv/bin/python -c "…load_tool_manifest(); migrate_tool_schemas(m)…"
tools 71 migrations 66 sha 57900a1d4d7b…  workflows {}
```

### 7.4 后端测试（与基线 `f8363463` 逐条对比）

基线用 `git worktree add --detach f8363463` 建独立工作树 + 软链主仓 `.venv` **实跑**
取得，不是靠回忆或文档。基线失败集合并集 **139 条**（`tests/sdk_adapters` 35 +
其余目录 104）。

终态（依赖卸载、服务槽位清理、全部测试修改之后）分两次跑：

| 命令 | 结果 |
|---|---|
| `pytest -q tests/execution tests/sdk_adapters` | **68 failed / 933 passed / 1 error**（375 s） |
| `pytest -q tests/operation_audit tests/session tests/memory test_agent_loop_*×4 test_provider_runtime_refresh test_sdk_observability_host` | **11 failed / 214 passed**（51 s） |

两次并集 **79 条**，`comm -13 基线并集 终态并集` → **空**：**零新增红**。
（终态 79 < 基线 139，差额是被删掉的记忆专属用例。）

那 1 条 `error` 是 `tests/execution/test_expiry_terminal_public_recovery.py::
test_real_old_host_expiry_stop_cold_new_stack_public_recovery`——fixture 直接读
`os.environ["H077_IDENTITY_JSON"]`，是 installed-target 环境门。**基线同样 ERROR**
（在基线工作树里单跑复核过：`2 passed, 1 error`），与本轮无关。

新增红一度到过 156 条，根因是共享夹具
`tests/execution/test_primary_foreground_runtime.py::build` 的六个记忆入参
（`visibility_memory` / `visibility_checker` / `recall_executor` /
`occurrence_coordinator` / `context_use_memory` / `procedure_runtime`），修掉之后
剩 25 条，再逐条处理（删记忆专属用例、去掉「遗忘/抑制」参数化档）后剩 1 条
（`test_sdk_observability_host::test_main_wires_one_host_sink_into_memory_and_harness`
的两条记忆侧断言，改成只断言 Harness 一侧并改名），最终清零。

pytest 全量收集：**7295/7300 collected（5 deselected），0 errors**
（基线 8930/8938）。

**一次未能复现的挂起（如实记录）**：把上面两组合成一条命令跑时，出现过一次在
`tests/execution/test_foreground_permission_lease.py` 前两个用例之后卡住 40 分钟
（CPU 1.5%）。之后**无法复现**：该文件单独连跑 3 次均为 `10 failed in ~20 s`
（与基线同样的 10 条），`tests/execution + tests/sdk_adapters` 合跑 375 s 正常结束。
判定为环境/时序性偶发，不是确定性回归；但**记在这里**，如果后续再遇到，第一嫌疑
是本轮改过的 `foreground_runtime.close()` 关停路径（§6 事故 2）。

### 7.5 前端

```
$ npx tsc -b --noEmit      # 无输出
$ npm test                 # Test Files 94 passed (94) / Tests 723 passed (723)
$ npm run lint             # 162 problems（基线 169）
```
lint 按「有错误的文件集合」逐文件对比：**没有任何新文件进入错误集合**；
`MemoryPanel.tsx` / `PrimaryAuditPanel.tsx` 两处旧错随文件删除消失。

### 7.6 完成标准 grep

```
$ grep -rn simple_harness_memory backend tauri-app \
    --include='*.py' --include='*.ts' --include='*.tsx' \
    --include='*.toml' --include='*.json' \
  | grep -v /.venv/ | grep -v node_modules
backend/vendor/simple_harness_service_sdk-0.3.12.candidate-manifest.json:176,180
backend/vendor/simple_harness_service_sdk-0.3.13.candidate-manifest.json:42,71
```

**Host 自己的代码为 0**（所有 `.py` / `.ts` / `.tsx` / `pyproject.toml` /
`uv.lock` 的 `[[package]]` 段一处不剩）。剩下的 4 条全部落在 **service SDK 自己的
已签名 candidate manifest** 里——那是第三方发布产物的**不可变字节**，
`sdk_candidate.py::verify_service_candidate` 会逐字节校验它的 SHA-256
（`SDK_SERVICE_CANDIDATE_MANIFEST_SHA256`）。改一个字符就会让启动 fail-closed。
故不改，记为 L-3 的同一条遗留。

（历史 `plans/` 与 `.local-test-evidence/` 按任务书不管。）

## 8. 文档回写

- `ARCHITECTURE/index.md`、`ARCHITECTURE/AGENT_HARNESS.md`：置顶 2026-09-10 条目。
- `ARCHITECTURE/PROJECT_STATUS.md`：总表「Memory SDK 钉版」行改为「已于 2026-09-10
  整条移除」，新增「删记忆 SDK」行。
- `ARCHITECTURE/UI.md`：置顶前端下线说明。
- `ARCHITECTURE/MEMORY_SDK_BOUNDARY.md`：整篇加「已于 2026-09-10 整体作废
  （历史记录，不再描述生产事实）」抬头；文件保留，因为编排层大改后重新引入记忆时，
  这份边界清单仍是最完整的「上一版都约定过什么」底稿。
- `CLAUDE.md`：目录树里 `deskpet/memory/` 的说明改成「Host 会话账本 + S1 证据链
  （名字是历史遗留）」；Backend patterns 的「三层记忆」改成「无长期记忆 + 诚实空端口」。

## 9. 遗留清单（不悬空）

- **L-1（语义降级，需编排层大改时决策）**：`PrimaryHistoryPolicy` 现在「遍历通过即
  可见」。产品口径上这与 2026-09-07 的用户决定②一致（遗忘只作用于记忆，不隐藏会话
  记录），但**「在 UI 忘记一条认知记忆」这个动作本身已经没有了**。重新引入记忆时
  必须重新决定：抑制权威是回到 Memory 侧，还是改由 Host 自己持有。
- **L-2（读侧安全检查弱化）**：`deskpet/execution/preparation_rejection.py::
  _verify_source_binding_tx` 原本用记忆 SDK 的 `HistoryEvidenceBinding` 公开承诺重算
  `binding_hash`；SDK 移除后 Host 无法（也不应假装能）重算，现在只保留仍成立的两条
  Host 判据（来源种类必须是 `user_message`、envelope 承诺一致），`binding_hash` 只做
  存在性检查。影响面被限制在**旧 userdata 的历史行**：本构建下
  `current_user_denial` 恒为 `None`，不会再产生新的拒绝记录。
- **L-3（第三方不可变产物，改不得）**：四处 `simple_harness_memory` 字样留在
  `backend/vendor/simple_harness_service_sdk-0.3.1{2,3}.candidate-manifest.json`
  与 `backend/uv.lock:7784`——都是 **service SDK 自己的包元数据**，声明一个
  `extra == 'memory'` 的可选依赖（memory-sdk 0.5.2 / 0.6.12）。Host 只装
  `[realtime]`，不会引入它。manifest 的字节被 `verify_service_candidate()` 逐字节
  哈希校验，改一个字符启动就 fail-closed。要真正清掉必须改 service SDK 仓并重新
  发版，本轮按「不动其它仓库」的约束未处理。
- **L-4（死代码，未清）**：`main.py` 里 `embedder` / `vector_worker` / `memory_curator`
  / `_file_memory` 等服务槽位恒为 `None`，其消费点全部被 `if X is not None` 守着，
  是无害死代码；`sdk_adapters/context_authority.py` 的 `_pending_occurrence_message`
  与 `ProductRuntimeDecisionSink` 的 `reconcile` 分支同理（`reconcile` 恒为 `None`）。
  `context_route.py` 的 `_ContestedProbe` / `_NO_PROBE` / `_conflict_digest` /
  `recall_refs` 形参保留为「编排层重新引入争议判定时的落点」，已在类注释里写明。
  这些一并留给编排层大改时清理，不在本轮扩大 diff。
- **L-5（未做的验证）**：**没有做真人/原生 App 的主对话多轮冒烟**。本轮的主流程
  证据只有「冷启动到 `startup complete` + `/health startup_errors=[]` + 目标测试目录
  零新增红」。任务书没有要求原生旅程，且真实 provider 调用会花钱；**主对话端到端
  是否真的一轮都不掉，需要用户在原生 App 上实测确认**。
- **L-6**：`ARCHITECTURE/` 下仍有大量描述记忆系统的历史条目（`index.md` 63 处、
  `ARCHITECTURE.md` 115 处、`PROJECT_STATUS.md` 294 处）。按该目录「历史条目各自
  描述当时状态、不回改不删除」的既有约定，本轮只置顶新条目 + 改总表，未回改历史段落。
- **L-7（一次未复现的挂起）**：见 §7.4 末段。`tests/execution +
  tests/operation_audit + … + tests/sdk_adapters` 合成一条命令跑时出现过一次
  在 `test_foreground_permission_lease.py` 处卡死 40 分钟；单独与两两组合均无法
  复现。第一嫌疑是本轮改过的 `foreground_runtime.close()` 关停路径。
- **F-WF-1 / 删 workflow 线 Slice 2、3**：与本轮无关，仍在既有遗留清单里。

## 10. 未做 / 边界

- 未 push；未动 `plans/taskSys2/`；未动 `.local-test-evidence/`；
  未动 simple-harness-sdk / simple-harness-memory-sdk 两个仓；未起子代理。
- 未用 `git stash`（任务书明确禁止）。基线对比走 `git worktree`。
- 未跑全量 pytest（7295 条），只跑了任务书点名的目标目录 + 受影响文件。

VERDICT: DONE — 记忆 SDK 已从 Host 主分支移除；Host 在未安装该包的情况下冷启动通过，
目标测试目录零新增红，前端全绿。主对话端到端待用户真机确认（L-5）。
终态提交 `fb08f475`（切片一~五：`4b4dfba2` / `8d6c6ddf` / `4087be65` / `2012f1f6` / `fb08f475`），未 push。
