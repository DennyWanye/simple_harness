# Plan: DeskPet Durable Graph Workflows And Trace

> 状态：**RESUMED**。用户已于 2026-07-10 明确要求恢复 AC-1..AC-18 全范围并端到端执行；上一轮阻断记录保留在 [`blocked.md`](./blocked.md)，其剩余问题在本轮继续闭环。

## 关联验收标准

本计划覆盖根目录 [`acceptance.md`](../../acceptance.md) 的 AC-1 至 AC-18。验收标准是唯一真相来源；本计划不得通过删减 AC 来收敛。

## 技术选型与最佳实践调研

### 选择：LangGraph 内核 + DeskPet 产品适配层

使用 LangGraph 的 `StateGraph`、durable checkpointer、interrupt 和 checkpoint history 作为图执行内核，在 `backend/deskpet/workflows/` 外包一层稳定的 DeskPet API。业务代码不得直接把 LangGraph 类型泄漏到 ToolRegistry、UI 或领域 workflow。

理由：

- LangGraph 会在每个 step/superstep 保存 checkpoint，并保留成功节点的 pending writes；这正好覆盖节点恢复、HITL 和 time-travel 的基础语义。[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- LangGraph interrupt 会先保存状态，恢复时通过同一 thread/checkpoint 继续；官方同时明确要求 interrupt 前的副作用必须幂等。[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- LangGraph time travel 明确区分 replay 与 fork，并且 checkpoint 之前的节点不重新执行，之后的 LLM/API/interrupt 会重新执行。[LangGraph time travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel)
- DeskPet 已有 ToolRegistry、权限、Receipt、Artifact、VerifyGate 和本地 SQLite；这些产品能力仍由 DeskPet adapter 控制，不交给通用框架。

放弃方案：

- 不整体迁移现有 AgentLoop。普通聊天和简单工具调用继续走现有 ReAct，避免破坏成熟产品路径。
- 不使用 Temporal。它的 event-history replay、durable execution 和 Activity 模型很完整，但需要独立服务、worker 和 deterministic workflow 约束，对单机桌宠过重。[Temporal Workflow Execution](https://docs.temporal.io/workflow-execution)
- 不手写 graph 调度内核。复用 LangGraph 的 StateGraph、interrupt、serializer 和 checkpoint protocol；仅实现遵守该 protocol 的 `FencedAsyncSqliteSaver`，把 DeskPet lease、同事务 outbox/blob refs 和 projection fencing 接入写路径。
- 不接入 LangSmith SaaS。默认全部本地存储，借鉴其 dataset -> evaluator -> experiment -> comparison 模型。[LangSmith evaluation](https://docs.langchain.com/langsmith/evaluation)

### 持久化决策

- 新建 `<user_data>/data/workflow.db`，与 `state.db` 隔离，降低消息/记忆迁移爆炸半径。
- LangGraph checkpoint protocol 与 DeskPet workflow metadata 共用该物理文件；写入由 `FencedAsyncSqliteSaver` 和统一事务 API 管理，禁止标准 SQLite saver 绕过 lease fencing。
- 大型 passages、模型原始输出、图片和 deck 不内联进 checkpoint；写到 `<user_data>/workflows/blobs/<sha256>`，checkpoint 只保存内容寻址引用。
- `thread_id` 表示一条可分叉的 checkpoint lineage，`run_id` 表示 UI/Trace 中的一次执行。新任务初始 `thread_id=run_id`；fork 复用 source `thread_id`，但创建新的 `run_id/trace_id` 并从 source checkpoint 建新 branch。
- 共享 thread 绝不按“thread 最新 checkpoint”恢复。每次 run/resume/recover 显式传该 run 的 `{thread_id,checkpoint_ns,checkpoint_id=head_checkpoint_id}`；aput以expected prior head CAS同事务更新branch head/owner。恢复只沿run owner/parent链找head，禁止省略checkpoint_id。测试两分支交错推进后分别重启恢复。
- 先修复现有 `state.db` 的 v17 目标版本与实际迁移分叉，再新增独立 workflow schema，避免启动期持续备份。

### 副作用与恢复决策

- 每个副作用先写 `workflow_effects` intent，再执行，再提交结果/Receipt/Artifact refs。
- effect 唯一性由“run lineage scope + `effect_fingerprint`”表达，fingerprint 基于 workflow/version、node、logical key、最终规范化参数、输入 blob hash 和 policy version。已成功 effect 仅按 branch link/复用策略复用；过期 running effect 进入 `uncertain`，不得自动重放危险操作。
- 读操作可自动重试；文件/Office 操作用 Receipt、artifact hash 和目标存在性 reconcile；shell/desktop 等不可安全判定的操作需人工确认。
- `cancel_requested` 与 `cancelled` 分开。线程池工作不能被 asyncio 强杀，晚到结果要进入 effect reconciliation。

### Trace 与评测决策

- 采用 OpenTelemetry 式 `trace_id/span_id/parent_span_id` 和上下文传播，不要求安装 Collector 或上传云端。[OpenTelemetry traces](https://opentelemetry.io/docs/concepts/signals/traces/) [context propagation](https://opentelemetry.io/docs/concepts/context-propagation/)
- `metrics.jsonl` 继续只做低基数匿名指标；丰富 Trace 存本地 `workflow.db`，有独立脱敏和保留策略。
- 统一评测记录覆盖 code rules、VerifyGate、GoalChecker、ExternalEvaluator、PPT visual review、LLM judge 和人工评分。
- 本地 eval 仿照 LangSmith：Dataset -> Example -> Experiment -> Result -> Evaluation；支持 direct score 和 pairwise/version comparison。

### Round 1 挑战后的规范性协议

以下内容是实现契约，不是建议项。

#### 1. 权威状态与 checkpoint 提交协议

- `FencedAsyncSqliteSaver(BaseCheckpointSaver)` 是 graph state/frontier 权威。按 pinned 4.1.1 实现 async 全协议；production只允许 async API，sync methods抛 AsyncOnlyWorkflowError。v1 compiler禁止 DeltaChannel，运行期任何 delta history请求稳定抛 UnsupportedDeltaChannelError，绝不返回空掩盖错误；GC只走实现过的 prune/delete APIs。所有写走 BEGIN IMMEDIATE+run fence。
- `workflow_runs/workflow_nodes` 是可重建索引、lease/cancel authority 和 UI latest projection，不能反向覆盖 checkpoint state；`workflow_node_attempts(node_execution_id,retry_attempt)` 是每次尝试的不可覆盖历史。
- graph 在发出 task 前生成稳定 `invocation_key`：串行/循环为 `node_id:superstep:loop_iteration`，map 为 `node_id:map_key:loop_iteration`；`node_execution_id=sha256(thread_id|checkpoint_ns|base_checkpoint_id|invocation_key)`。DeskPet node wrapper 只读公开 `Runtime.execution_info.task_id/checkpoint_id/checkpoint_ns` 加上 JSON-safe invocation key，在 handler 前持久 start；`task_path` 不在 Runtime 上读取，等 saver 收到公开 `aput_writes(...,task_id,task_path)` 参数后按 task id 补到同一行。不读取 `__pregel_*` 私有 config。per-slide/research map 在 Send 前按 domain id 排序；同一 base checkpoint 恢复复用 execution id，只增加 retry attempt。
- 节点开始：CAS 校验 run fence 后，稳定 execution 首次 insert；每次运行 insert `workflow_node_attempts(node_execution_id,retry_attempt,status=running,started_at,...)`，同时更新 `workflow_nodes.latest_attempt/status`。pending writes 主键遵循 protocol：`(thread_id, checkpoint_ns, base_checkpoint_id, task_id, write_index)`，重复恢复覆盖同一 task write，不覆盖旧 attempt。
- `aput_writes` 是第一阶段：同一事务校验 epoch/version/status 并写该 task 的 pending writes/refs；按官方 special channels 分类，存在 `__error__` 时 node=`failed` 并持久错误，存在 `__interrupt__` 时执行 HITL open 协议并置 `waiting`，只有正常 writes 才置 `succeeded_pending`。三者都不推进 run head、不发“节点最终完成”事件；零业务输出的成功节点由 wrapper 写保留的 normal completion marker，不能和 error/interrupt 混淆。
- Interrupt-capable `NodeDefinition` 必须标 `exclusive_superstep=True`，且只能由 dedicated barrier/join 节点单独调度；compiler 拒绝它与 fan-out sibling、并行 Send 或其他 outgoing task 共处一个 superstep。三条生产图的 outline/permission/clarification/plan wait 都使用该 barrier。作为防御，若同一 base checkpoint 已存在其他 running task 又收到 `__interrupt__`，saver 不释放 run lease而置 `blocked:interrupt_barrier_violation`；同一 task 同时出现 error/interrupt 时 error 优先、prepared decision abandoned。测试证明合法 interrupt 时不存在尚未收敛的兄弟 writes。
- node wrapper 的异常顺序固定：`asyncio.CancelledError`、LangGraph `GraphBubbleUp/GraphInterrupt` 原样抛出；仅普通 provider/tool/domain exception 规范化为 WorkflowNodeError。禁止 broad `except Exception` 吞 interrupt 或 cancel。
- `aput` 是第二阶段：当 LangGraph 聚合 superstep 时，同一事务再次校验 epoch/version/status，写 full checkpoint/parent/owner/blob/effect refs，把该 checkpoint 已吸收的 `succeeded_pending` executions 置为 `succeeded`，推进 run head，并写 durable lifecycle outbox。checkpoint 失败时 pending writes 仍可被 `aget_tuple` 返回供 LangGraph 恢复，projection 不得提前显示最终成功。
- 非 checkpoint trace/eval projection 仍允许在事务后异步写；崩溃时 reconciler 遍历 checkpoint metadata 中的 `deskpet_run_id/node_id/attempt/lease_epoch/event_seq` 补齐。
- 崩溃于 node start 且没有 pending writes：当前 attempt row 标 `abandoned/ended_at/error_ref`，同一 execution 新增 retry_attempt+1；已有 pending writes 时对应 attempt 保持 `succeeded_pending`，恢复不得重跑该 task。execution latest projection 可更新，但所有 attempt rows 永久保留到 run retention。
- `workflow_runs.(head_checkpoint_ns,head_checkpoint_id)` 是branch cursor缓存；不一致时只能沿该run的checkpoint owner/parent链重建，禁止查询共享thread全局latest。
- full checkpoint、run/node head、正式 blob/effect refs、checkpoint ownership 和 lifecycle outbox 由 `aput` 同事务；pending writes 与 pending refs 由 `aput_writes` 同事务；core trace/evaluation records 在 node 输出前已同步持久并由 checkpoint 引用，只有 metrics/legacy JSONL/detail export 是可收敛 projection。故障矩阵分别证明两个 saver 阶段、canonical trace/eval 和 export projection。
- 所有生产 `ainvoke/astream/resume` 显式传 `durability="sync"`；subgraph 继承，测试断言 N+1 node start 不早于 N 的 `aput` commit。fork 的 `aupdate_state` 必须 await saver commit 后才启动 child。禁止使用 `async/exit` durability；manifest compile test 和 wiring contract 固定该值。

#### 2. Fenced lease 与确定性 merge

- lease 字段：`lease_owner`、单调递增 `lease_epoch`、`lease_expires_at`、`heartbeat_at`、`run_version`。
- 默认 TTL 90 秒、heartbeat 15 秒；长 LLM/搜索/渲染等待期间 heartbeat task 独立续租。连续两次续租失败或 epoch 不匹配，owner 设置本地 cancel token 并抛 `LeaseLostError`，不得提交 node/effect/projection。
- 所有 saver 写、heartbeat、run transition、effect begin/commit、decision consume 和 projection update 都带 expected `lease_epoch + run_version + allowed_status`；任一不匹配返回 `stale_run_fence`。`request_cancel()` 在一个事务内递增 `run_version` 和 `lease_epoch`、清 owner/expiry、设置 cancel 状态，从而立即废弃同 epoch 的旧节点；晚结果只能由新 fence 下的 reconciler 记为 `late_orphan/uncertain`，绝不能推进 graph。
- 本产品只允许一个 backend 进程，但测试用两个独立 DB connection 验证 fencing；进程重启后旧进程已死亡，expired lease 才可 reclaim。
- 并行节点只可写声明过的 channels：标量为 single-writer；dict 仅允许 disjoint keys；列表 reducer 以稳定 item id 去重并排序。两个节点写同一 scalar/key 时抛 `StateMergeConflict`，不得 last-write-wins。

#### 3. WorkflowState、节点签名与错误状态机

```python
class WorkflowState(TypedDict):
    schema_version: int
    workflow_name: str
    workflow_version: str
    thread_id: str
    run_id: str
    session_id: str
    active_nodes: list[str]
    active_step_id: str | None
    status: str
    values: dict[str, JsonValue]
    blob_refs: list[str]
    artifact_refs: list[str]
    receipt_refs: list[str]
    loop_counters: dict[str, int]
    budgets: dict[str, int]
    errors: list[dict[str, JsonValue]]

NodeHandler = Callable[[WorkflowState, WorkflowContext], Awaitable[StatePatch]]
```

- `StatePatch` 只接受 JSON-safe primitives/refs；禁止 pickle、任意 Python 对象和 open handles。
- `WorkflowDefinition.channels` 为每个 state key 声明 `ChannelSpec(value_type, reducer, allowed_writers)`；runtime 根据当前 node 注入不可伪造的 writer id。Patch 写未授权 channel 或并行写 single-writer key 直接 `StateMergeConflict`。fan-out frontier 从 checkpoint tasks/`active_nodes` 投影，不靠单值 active node。
- run 状态全集：`created|running|waiting|retryable|cancel_requested|cancelling|blocked|completed|failed|cancelled`。`created/retryable -> running` 必须 claim 新 lease；`running -> waiting` 只能由识别 `__interrupt__` 的 `aput_writes` 在 open decision/outbox 同事务后释放 lease；decision response 以 nonce/version CAS 将 `waiting -> retryable`，随后 runner 再 claim；retryable failure 释放 lease；任何非 terminal cancel 都先用 fence invalidation 进入 `cancel_requested`，有 active/uncertain effect 时进入 `cancelling`，收敛后到 `cancelled`，不能自动判断时到 `blocked(reason=effect_uncertain)`。`completed|failed|cancelled` terminal 不可回退，`blocked` 仅能由 typed recovery decision 转 `retryable|cancelled`。
- node 状态全集：`pending|running|succeeded_pending|succeeded|waiting|retryable|failed|abandoned|cancelled`。`uncertain` 只属于 effect，不作为 node/run 状态；恢复扫描非 terminal runs、过期 lease、`succeeded_pending` nodes 与 uncertain effects。
- 错误 taxonomy：`retryable_provider`、`retryable_tool`、`invalid_state`、`checkpoint_corrupt`、`permission_denied`、`cancelled`、`lease_lost`、`effect_uncertain`、`permanent`；每类固定 `retryable/user_message/recovery_action`。

#### 4. Effect fingerprint、线程晚结果与 fork 复用

- `args_hash = sha256(canonical_json(tool/effect args + relevant input blob hashes))`。
- `effect_fingerprint = sha256(workflow/version + node_id + logical_effect_key + effect_type + args_hash + policy_version)`。
- `workflow_effects` 保存执行事实；node links允许task重入；`workflow_checkpoint_effects(thread_id,checkpoint_ns,checkpoint_id,effect_id,node_execution_id)` 只在aput确认result进入full checkpoint时创建。规范唯一性是branch scope+fingerprint。
- fork 只看 source checkpoint 的祖先可达 `workflow_checkpoint_effects`，不会复制“effect 已执行但结果尚未进入 checkpoint”的 node link。只有声明 `deterministic_reusable` 的纯结果可跨 branch 直接复用；写文件/Office/system effect 即使祖先可达，也只作为历史完成证据，重执行节点仍按策略复用或要求确认。
- workflow 路径把同步 handler 提交为可观察的 `asyncio.Future`，使用 `await wait_for(shield(future))`。timeout 后 future 不取消，注册 done callback；callback 通过线程安全 loop handoff 调 `EffectJournal.finalize_late()`。晚结果必须校验 args hash、effect id 和 lease epoch；失 fence 时记 `late_orphan` 等待 reconcile，不能直接推进 graph。
- ToolRegistry 新增 `prepare_call(tool_name, raw_params, session_id, stable_call_id)`：先合并 session context、解析 handler defaults/动态输出路径，产出 immutable `PreparedToolCall(final_params,args_hash,prepared_targets,tool_spec_version,schema_hash,permission_policy_version,effect_type)`。`PreparedTarget` 固定 `final_path/staging_path/reservation_key/mode(create|replace|append|edit)/pre_exists/pre_hash/pre_size/pre_mtime/format`；graph 默认输出路径含 `run_id + stable_call_id`。Windows reservation key 以 absolute resolved path 做 separator/drive/UNC normalization + Unicode normalization + casefold 后 SHA-256，POSIX 保留 case；`workflow_target_reservations(reservation_key PRIMARY KEY,display_path,run_id,call_id,status,lease_expires_at)` 状态为 `prepared|claimed|committed|released|expired`，CAS claim/commit，崩溃 sweeper 仅回收无 active effect 的过期项；禁止秒级时间戳或 path spelling 绕过碰撞。
- 真实 `ToolSpec` 增加 `prepare/stage/validate/commit/rollback/reconcile`、EffectPolicy，以及必填的 `outcome_parser/outcome_parser_version/outcome_parser_hash`（对graph allowlist）。新增 committed `workflows/implementations/v1/tool-outcomes.json`，每个manifest工具恰好一行：tool/spec/schema hash、parser id/version、success/failure/malformed golden fixtures；compile比较allowlist集合，缺失/多余/hash漂移均失败。
- parser ids固定：`json_error_envelope_v1`（file_read/file_write/file_glob/file_grep/workspace_recall及同形工具，无error且满足required success keys）、`code_array_or_error_v1`（glob/grep，array/合法结果object成功，error object失败）、`shell_exit_v1`（run_shell仅exit_code=0成功）、`artifact_envelope_v1`（doc/excel/pdf/PPT内部效果要求有效artifact/path+格式验证）、`accepted_run_v1`（deepresearch/ppt_pro要求accepted+run_id）、`mcp_explicit_v1`（仅显式isError/effect声明的opt-in dynamic）。每个实际allowlist ToolSpec绑定其中一个或专用版本；未知/畸形known结果fail closed。
- v1 manifest allowlist 明确：`write_file/edit_file/file_write/desktop_create_file/doc_create/doc_edit/excel_create/pdf_export` 和 PPT graph 内部图片/deck/preview 必须 `staged_file`；`run_shell`、自动打开和 desktop/computer action 为 `opaque_manual`；`file_organize/memory_write/memory_forget/ppt_create` 排除。只对 manifest allowlist 内工具强制 inventory；未知 `plugin:*`/`mcp:*` write tool 默认不进入 durable graph并诊断，不影响 graph 启动，只有显式 opt-in 某 graph version 时才要求 lifecycle callbacks/hash。
- `workflow_effect_targets` 状态固定 `prepared -> staged -> committing -> committed`（或 rolled_back/uncertain）。stage 文件 FlushFileBuffers/fsync 后持久 validated hash/size/format/precondition/created_dirs；commit 前置 committing，随后 replace + durability flush，再写 committed。POSIX fsync parent dir；Windows 使用 write-through file handle/FlushFileBuffers，并把目录元数据的 power-loss 原子性标为平台能力限制，但保证正常进程崩溃恢复。恢复按 final/staging hash 对账；rollback 只删本 effect 创建且仍为空的目录。
- shell/desktop/delete/跨卷 move 等不能安全 staging 的工具标 `opaque_manual`：intent/permission 后最多执行一次，timeout/crash 一律 uncertain，不自动 retry，必须人工确认结果。cancel/timeout 后只有 reservation、pre/post evidence、格式验证和 commit marker 能自动 reconcile；“目标存在”本身不能证明成功。

#### 5. Durable decision 与一次性授权

- `workflow_decisions` 明确列：`decision_id/logical_key/generation/run_id/node_execution_id/interrupt_id/task_id/checkpoint_ns/kind/payload_hash/status/expires_at/resolved_at/response_hash/response_json/nonce_hash/version`，`UNIQUE(run_id,logical_key,generation)`；logical key本身也包含 thread/node namespace。未被 abandon的 prepared/open/resolved复用；orphan后 generation+1。`workflow_grants` 保存绑定/claim字段并保证 token/claimed effect唯一。
- wait node 先 prepared decision，再调用 `interrupt({decision_id})`。`aput_writes(__interrupt__)` 从 pinned `Interrupt.id` 持久化 interrupt/task/ns 并原子 open/waiting/outbox/release lease。UI resolve 只 CAS 保存 validated response/hash，不直接执行；runner 重新读取 DB 并校验 hash，然后用同一 thread/checkpoint config 调 `ainvoke(Command(resume={interrupt_id: response}), durability="sync")`。节点从头重入时按 logical key 读取 resolved response，所有前置动作幂等。prepared 尚未被 recovery abandon时重用原 ID；确认 orphan 后才 generation+1。
- permission 被拆成 `prepare_effect -> wait_permission -> claim_effect_and_grant -> execute_effect`。授权 token 绑定 `tool_name + args_hash + permission_policy_version + effect_fingerprint + expires_at`，只存 token hash。
- `claim_effect_and_grant` 在一个 `BEGIN IMMEDIATE` 事务内把 decision/grant 绑定到 `effect_id` 并 claim effect。崩溃后同一 effect id/owner epoch 可幂等重入；不同 effect 不能复用该 grant，因此没有“已消费但未 dispatch 就永久丢授权”的窗口。
- ToolRegistry 通过结构化 `AuthorizationGrant` 接收授权，grant 不进入公共 tool args/schema。`execute_prepared()` 顺序固定为：重新解析同名 `ToolSpec`并比对 spec/schema/policy version -> 重验 disabled/toolset/code-plan-readonly/session identity/circuit breaker -> 校验 grant 未过期且已绑定当前 effect -> 执行 exact `final_params`（不再 merge/default/permission prompt）-> timeout/normalize Artifact/Receipt/effect。旧 `execute_tool()` 与新路径下沉复用同一个 `_dispatch_validated()`，旧 permission Future 仅作为 UI transport cache；DB decision 才是权威。任一版本变化都阻塞并要求 re-prepare/re-authorize。
- clarification、plan、PPT outline、skill candidate 复用相同 decision state machine，但各自有 typed payload/response validator。
- expiry sweeper 使用 ClockPort：`open -> expired` 时关闭未 claim grant、写 decision-expired outbox，并按 decision policy 将 run 转 `blocked(reason=decision_expired)` 或 `cancel_requested`；UI 提供“重新发起决定/取消任务”。cancel transaction 同时把该 run 所有 prepared/open decisions 与未 claim grants 置 cancelled，不能永久留 waiting。

#### 6. Receipt v2 与 PPT accepted/delivered 语义

- 新 Receipt 增加 `sig_version=2`、`phase=accepted|delivered|executed`、`outcome=pending|success|failed`、可选 run/node/effect refs。
- 无 `sig_version` 的旧 JSON 一律按 v1 字段集合 canonicalize/验签；`Receipt.from_dict` 不把 v2 默认字段混入 v1 HMAC。新写记录只用 v2，不批量重签历史。
- `ToolSpec` 增加 `completion_semantics=sync|accepted_async`。本次把 `deepresearch` 与 `ppt_pro` graph 改成 durable `accepted_async`：启动写 `accepted+pending` receipt，不作为 VerifyGate 完成证据；full checkpoint 提升 terminal intent 后写 `delivered+success|failed`。普通 `ppt_create` 继续走现有同步/进程内边界且明确标为 non-durable；不得借它绕过 PPT Pro graph。
- 旧 `researching/generating` WS/UI 文案保持兼容；VerifyGate 只接受 `executed/delivered + success` 作为完成证据。

#### 7. Workflow ports 与 Harness 等价治理

公共 ports 固定为：`LLMPort`、`SearchPort`、`FetchPort`、`ToolPort`、`EffectPort`、`PermissionPort`、`ArtifactPort`、`ReceiptPort`、`NotifierPort`、`EvaluatorPort`、`ClockPort`。

- DeepResearch 的 plan/gap/synth/rerank 走 LLMPort；search/fetch/direct source 走 Search/FetchPort；每个 port 自动 trace/retry/cancel，但只读操作不虚构权限弹窗。
- PPT 生图/文件渲染/自动打开走 EffectPort；Artifact/Receipt 只能由对应 ports 发布。
- 三类 workflow terminal node 都调用统一 completion evaluator adapter（VerifyGate/GoalChecker/领域 evaluator）并写 evaluation record。
- 业务 node 禁止直接 import ToolRegistry、provider client、WebSocket 或 ReceiptStore；legacy wrapper 可以作为 port adapter 实现。

#### 8. Trace root 与跨线程传播范围

- root spans：`main.py::_run_chat` 文字 ReAct、`pipeline/voice_pipeline.py` 语音 AgentLoop、每个 workflow run、会启动/恢复/fork/cancel workflow 的 slash/control command。
- workflow 相关 Tauri artifact command 携带 `trace_id/run_id/span_id` 回 backend 记录 linked child span；非 agent 的窗口/设置命令不伪装成 agent trace。
- LLM instrumentation 明确覆盖实际生产入口 `backend/providers/openai_compatible.py`、provider chain/resolution wrappers、DeepResearch rerank bridge、PPT visual-review bridge。
- 所有 workflow node/port 显式传 `WorkflowContext`；`contextvars` 仅用于同一 async call chain。线程池统一使用 `copy_context().run` 包装，并同时显式传 immutable SpanContext，测试不依赖隐式传播。
- Trace 不是只靠 checkpoint metadata 可重建的异步 projection。新增 `trace_runs(trace_id PRIMARY KEY,run_kind,workflow_run_id,session_id,request_id,turn_id,status,error_code,root_span_id,started_at,ended_at)` 作为普通 ReAct、voice 和 workflow 共用索引；core span 与 refs 在每个 LLM/tool/evaluator 返回后同步提交。workflow root/core 写失败 -> fenced `blocked:trace_store_unavailable`；text/voice 在调用 LLM 前若 root 写失败，则不执行本回合并发送现有 chat/voice error envelope `trace_store_unavailable`，不会留下“执行过但无 trace”的 run；执行中 detail payload 失败可继续但 span 标 `detail_degraded`，core close 失败发送 diagnostic error并把 trace_run failed。checkpoint metadata 只补 links，不声称重建 payload/latency/parent。

#### 9. Fork 算法

- LangGraph `thread_id` 是 checkpoint lineage id，DeskPet `run_id` 是 branch execution id。
- 新增 `workflow_fork_requests(fork_key PRIMARY KEY,source_run_id,source_checkpoint_ns,source_checkpoint_id,source_version,request_id,patch_hash,child_run_id,child_checkpoint_ns,child_checkpoint_id,status,created_at,updated_at)`；fork_key进入saver metadata，checkpoint与checkpointed同事务。恢复prepared先按三元key/fork_key查已提交checkpoint。
- v1 只允许 root、无 pending/interrupted、single-writer safe point。可信 runtime 强制重写 run_id/parent_run_id/source_checkpoint_id/trace_id 等内部 branch identity并禁止用户 patch；同 thread lineage保留。`aupdate_state(as_node=last_writer)` 后 saver校验 state identity与child fence一致。waiting/fan-out/subgraph只读。
- 新 run 有独立 trace/eval/lease/effect links；source run 永不更新。fork 复制 source checkpoint 及之前的 committed blob/effect refs，之后节点重新执行。
- waiting checkpoint 不支持 v1 fork；用户先 resume/cancel 到新的 safe checkpoint。所有允许 fork 的 state patch 仍先过 schema/patch validator。

#### 10. Blob ownership 与清理

- 增加 `workflow_blobs(hash,path,size,privacy_class,ref_count,created_at,last_accessed_at)` 与 `workflow_blob_refs(owner_type,owner_id,blob_hash)` 唯一表。
- checkpoint refs 由 fenced saver 与 checkpoint 同事务；span/evaluation/artifact refs 由各自 owner 事务增减。blob 文件先原子落盘再入 DB，DB 失败只产生无 ref orphan，24 小时后可删；绝不产生 checkpoint 指向 ref_count=0 的活 blob。
- 清理器仅删除 `ref_count=0` 且超过 24 小时 grace 的文件；启动 reconciliation 以 refs 表重算 ref_count，处理 DB commit 后文件未删/文件缺失。

#### 11. IPC、fault injection、redaction 与 eval 细节

- IPC request：`{type, request_id, payload, expected_version?, cursor?, limit?}`；response 回显 request id；error 为 `{code,message,retryable,current_version?}`。列表最大 100，cursor 稳定；run events 有单调 `seq`，重连用 `after_seq` 补齐并去重。
- `FaultInjector` 至少覆盖：node start 后、normal handler 后/`aput_writes` 前、`aput_writes` transaction 内、`__error__` write、prepared decision 后/interrupt 前、interrupt write 后/open decision 前、terminal intent staged 后/full `aput` 前、full `aput` transaction 内、N checkpoint 后/N+1 start 前、grant/effect claim 后/dispatch 前、staging validate 后/commit 前、effect commit 后/pending write 前、Receipt temp fsync 后/replace 前、SessionDB message 后/delivery mark 前、session tombstone 后/late delivery 前、blob file 后/ref commit 前。每个 hook 有恢复后唯一不变量，不再用固定“九个”数量掩盖新增窗口。
- 新增 `resources/diagnostic-redaction.json` 作为 Python trace、UI payload 和 Rust diagnostic bundle 的单一规则源；UI 只展示 backend 已脱敏数据。
- Eval 版本键为 `workflow_version + model_id + prompt_hash + tool_schema_hash + evaluator_version`。内置 deterministic fixtures 有明确期望：Research 引用完整；PPT outline/preview/QA 全节点；Code 文件 hash + tests pass + todos complete。`--live` 默认每场景 3 次，用中位 score/latency，质量阈值按 dataset 固定，网络错误单列不伪装质量失败。
- UI 图固定使用 `@xyflow/react==12.11.2`；独立 spike 门：生产 bundle gzip 增量 <=250KB、桌面/移动截图非空、无 console error。失败则执行预先定义的 CSS DAG fallback，不临场再选方案。

#### 12. Checkpoint ownership、branch GC 与 graph 版本

- custom saver及所有checkpoint外键统一使用 `(thread_id,checkpoint_ns,checkpoint_id)`；owners、effects、blob refs、fork requests都带namespace，禁止仅checkpoint_id关联根图/子图。
- 删除 run 时只删 owner link；GC 从所有 live run heads/active decisions 做祖先可达性标记，只有“无 owner 且不在任何 live head 祖先集合”的 checkpoint/pending writes/blob refs 才删除。`adelete_thread` 只在 thread 无任何 live run/owner 时调用。
- compiled graph registry 键为 `(workflow_name, workflow_version)`，定义与行为 adapter 放在 immutable version modules。manifest 记录 definition/state/prompt/tool/policy/callable source/uv.lock hashes并合成 implementation bundle。任何 live nonterminal run、active decision、未投递 event或 retained evaluation 都持续 pin bundle，不受 terminal 30 天清理；只有无引用且超过全部 retention 才可从 frozen bundle 移除。共享实现改变未升 v2则 golden CI 失败或旧 run blocked，绝不静默换行为。
- 若旧 graph 代码确实移除，必须提供显式 `StateMigration(from_graph_version,to_graph_version)` 并由用户/测试确认后 fork 到新 run；无法迁移则状态 `blocked:graph_version_unavailable`，不得用新图猜测恢复。
- 每个 checkpoint/pending blob 保存 SHA-256、serializer version 和 parent id；读取先验 hash 再 strict deserialize。head cache 错但历史链有效可自动重建 projection；checkpoint 本体损坏则复制原 bytes/metadata 到 quarantine、原 run `blocked:checkpoint_corrupt` 且不改写 lineage。若存在最近有效 single-writer ancestor，UI 提供“从有效祖先 fork 恢复”并列出 ancestor 后已提交 effects，用户确认后创建新 run；无有效祖先/manifest/state migration 时 `blocked:checkpoint_corrupt_unrecoverable`，只允许导出诊断/取消。禁止静默回退 parent 继续原 run。自动/人工/不可恢复三类都有稳定 recovery action 和 fault test。

#### 13. Durable lifecycle event outbox 与完成交付

- `OutboxIntent` 增加 `intent_id/payload_hash/logical_target`；intent id 固定为 `sha256(run_id|node_execution_id|intent_ordinal|kind|logical_target)`，event id 固定为 `uuid5(NAMESPACE_URL,"deskpet://workflow/{run_id}/intent/{intent_id}")`，delivery key 为 `sha256(event_id|channel|normalized_target)`。run-create/progress/fault 使用保留 execution key `run:create` 或具体 node execution/attempt 和稳定 ordinal，不能随机生成。
- 增加 `workflow_events(run_id, seq, event_id, intent_id, type, payload_hash, payload_ref, promoted_checkpoint_id, created_at, expires_at)`，event/intent id 唯一且 `UNIQUE(run_id,seq)`；重复 promotion 用 `INSERT ... ON CONFLICT`，existing payload hash/checkpoint 不同即 `outbox_intent_conflict`，相同则返回原 event。增加 `workflow_deliveries(event_id,channel,target,idempotency_key,status,owner,lease_expires_at,next_attempt_at,attempts,delivered_at,last_error)`，`UNIQUE(event_id,channel,target)` 与 idempotency key 唯一。
- accepted/waiting/progress/fault 由对应权威 run/saver transaction 直接写可投递 event；terminal node 只能返回 JSON-safe `OutboxIntent(kind,payload_ref,channels,request_id,turn_id)` staged write，delivery worker看不到它。只有吸收该 write 的 full `aput` 才原子提升为 `workflow_events`，同时提交 completed head；`aput` 失败时 intent 保持 pending 且不可见，恢复不会提前交付完成。
- delivery worker 分别处理 `session_message`、`tool_message`、`artifact_message`、`receipt_jsonl`、`websocket`：v17 给 SessionDB `messages` 增加 nullable `workflow_event_id` + partial unique index，`append_message(workflow_event_id=...)` 返回已存在行；Receipt v2 的 `receipt_id=uuid5(NAMESPACE_URL, "deskpet://workflow-event/{event_id}/receipt/{phase}")`，name 使用 UTF-8、phase 只允许 canonical enum。`ReceiptStore.append_once()` 在单-backend 文件锁内读取并修复无换行/非法 JSON 尾部，写完整 session 内容到同目录 temp、fsync 后 `os.replace`，按 receipt id 去重；retention 也用同样 temp-replace 逐 receipt compaction，只有空文件才删除。Python/TypeScript 固定 golden vectors。WS payload 带 event id，前端 store 去重。只有目标持久化/发送成功才标 delivery delivered。
- UI 重连通过 `after_seq` 补发；若 cursor 小于该 run 仍保留的最早 seq，返回 `history_expired` 和 `earliest_available_seq`，不伪装为空结果。SessionDB message/ReceiptStore/WS 任一失败只重试对应 channel，不重复其他已完成 channel。
- DeepResearch 与 PPT Pro graph 都使用 `completion_semantics=accepted_async`；普通 `ppt_create` 排除。handler 返回 run/request/turn ids 与 accepted；原 AgentLoop 只回复“已开始”且不声明完成。terminal node只产 OutboxIntent，full checkpoint 后投递；legacy kill-switch 仍走原阻塞 handler。
- PPT Pro 使用同一 outbox。accepted receipt 与 delivered receipt 分离；后台重启不依赖原 AgentLoop coroutine。
- workflow 完成的 WS 类型是独立 `workflow_event/workflow_final`，始终带 `run_id+request_id+turn_id+event_id`；它可以写入聊天历史但不得触发当前普通聊天的 `chat_v2_final` working-state reducer。两个 workflow 与一个普通流式回合并发时，前端分别归属/去重。
- delivery状态机保持。任何带session_message且会改变WorkflowCard的事件（handoff/waiting/progress/fault/completed/failed/cancelled）history DTO都返回完整event payload；history service按event ids join workflow_events，缺event degraded且不seen。前端对每条事件先用同一workflow reducer成功hydrate/update card，再加入seen；随后after_seq/WS才去重。参数化测试每种event在history成功、WS前崩溃后重连。
- 新增 `workflow_receipt_ledger(receipt_id PRIMARY KEY,run_id,phase,outcome,reason,payload_hash,created_at)` 作为 v2 Receipt 权威，ReceiptStore JSONL 是兼容 projection；VerifyGate 先读 ledger。completed/failed/cancelled 都原子关闭 accepted ledger。session delete 的 cancel 仍写 `delivered+failed(reason=session_deleted)` ledger，该 audit channel 不受 UI session epoch 丢弃、不会复活聊天；JSONL 可稍后 append_once 投影。

#### 13A. Workflow start 幂等与 AgentLoop handoff

- start identity tuple固定为 `(venue,base_session_id,code_session_id_or_empty,delivery_session_id,base_epoch,code_epoch_or_zero,request_id,turn_id,workflow_name,logical_slot)`，canonical JSON SHA-256为identity_key主键。logical_slot：accepted_async single call=`accepted_async:0`；control/API使用持久request稳定ordinal，禁止provider call id。request_hash=`sha256(workflow_version|definition_hash|args_hash|capability_hash)`。先查identity：同hash返回旧run；不同hash返回start_payload_conflict(existing_run_id)且不新建；不存在才单transaction创建。覆盖并发、参数漂移、提交后崩溃。
- Tool dispatch返回DispatchOutcome；accepted_handoff以ASYNC_HANDOFF结束ReAct，但AgentLoop绝不直接写SessionDB/WS。唯一权威是start创建的accepted event：session_message用accepted_event_id作workflow_event_id，websocket由同event生成chat_v2_handoff_final。AgentLoop只调用 `WorkflowService.deliver_event_once(event_id)` 触发CAS delivery；session epoch/tombstone可discard，避免删除后复活。前端清当前turn working并创建running card，不标完成。
- batch preflight 在执行任何工具前检查：accepted_async call 必须是该 assistant message 唯一 tool call；mixed batch 整批零执行，返回 `accepted_async_must_be_single` 让下一次 LLM 修正。单独 accepted call 通过 start 幂等协议后立即 handoff；普通 tool batch 不变。

#### 13B. 工具结果唯一语义

- NormalizedToolOutcome原始 `domain_status=success|failed|unknown` 不可变。Effect另有 `executed_unknown -> waiting_reconciliation -> reconciled_success|reconciled_failed`；decision写独立reconciliation/effective outcome。unknown不写success Receipt/Eval、不自动retry，确认后Graph按effective outcome推进。transport异常直接failed。
- Effect/Receipt/Verify/Eval/AgentLoop只消费outcome：success要求transport_ok且domain_status=success；unknown不得完成/评分。公共 `execute_tool()->dict` 保持字节兼容；新增execute_tool_outcome，或内部_dispatch_validated返回outcome再由旧API适配dict，现有direct callers/subagents/mocks不改。

#### 14. Complex Code proposal/effect checkpoint 边界

- Code ReAct graph 不使用“一次 LLM + tool batch”单节点。拆成 `llm_proposal`（仅生成 assistant content/tool proposals，checkpoint 固定 proposal/args）-> `tool_execution`（按 proposal 的稳定 call id 执行/复用 effects，全部结果 checkpoint）-> `completion_decision`。
- stable call id 在 prepare 前按 execution/base checkpoint/iteration/index/tool/raw args 生成。只有 proposal normal pending write 或 full checkpoint 已提交后，恢复才保证不再次调用 LLM；provider 返回后、任何 durable write 前的进程崩溃可能重新调用模型，这是无 provider idempotency key 时的明确边界，但不会重复已 journaled tool effect。proposal checkpoint id 仅后置关联。
- 无 tool proposal 时进入 completion gates；需要修复则下一轮 `llm_proposal`。原短聊天 `AgentLoop.run()` 也复用提取出的 proposal/dispatch primitives，但不强制 durable graph。
- `ProposalStateV1` 必须完整 JSON 序列化：effective messages 与 role/tool-call ids、original request/request/turn ids、system/prompt/skill refs、compaction summary/ref 和 token estimate、iteration/proposal/fix/tools-used counters、active plan/step/todo ids、tool-signature repeat window、completion/verify/self-check attempts 与 prior outcomes/evidence refs、provider/model snapshot 与 fallback attempts、last error、pending/committed tool results。内嵌逐字段 `GateConfigV1(max_turns,tool_budget_hard,wall_clock_seconds,max_budget_usd,per_tool_max_consecutive)` 与 `GateStateV1(started_at,turns_used,tools_used,cost_usd,per_tool_consecutive,per_tool_last_sig,last_transition,terminated,terminated_reason)`；`started_at` 是 ClockPort UTC epoch，若 wall clock 启用则应用离线时间也计入，与现有绝对时间语义一致。convergence controller 的计数/reason 同样显式字段，不用“counters”笼统代替。`ProposalOutcomeV1` 固定 assistant content/reasoning summary ref、raw tool proposals、PreparedToolCalls/stable call ids、stop reason、usage/provider/model、gate request 与 typed error。每次 proposal 从 state 重建 adapters并写回，不持久化 manager/client/lock/Future；短聊天用同 primitive 和逐事件 golden parity。

#### 15. Cancel/uncertain 收敛

- `cancel_requested` 有 active/uncertain effect 时进入 `cancelling`，不直接 `cancelled`。recovery 扫描 `cancel_requested/cancelling/blocked` runs，并单独扫描 `running/uncertain/late_orphan` effects；run 没有 `uncertain` 状态。
- 晚成功 effect 仍写 journal/trace，但 cancel token 阻止 graph 推进；完成 reconcile 后 run 转 `cancelled`。无法自动 reconcile 的危险 effect 转 `blocked:effect_uncertain` 并创建用户 decision，解决后才能 `cancelled` 或恢复。

#### 16. Todo/Goal projection 所有权

- v17 同时给 `code_todos` 和 `goal_tasks` 增加 `workflow_run_id/workflow_step_id` 与 partial unique index `(workflow_run_id,workflow_step_id) WHERE workflow_step_id IS NOT NULL`。Graph 不调用现有 replace-all 语义，而用 `sync_workflow_code_todos()` / goal upsert 按稳定 step id 更新，只删除“同一 workflow run 所有且已从权威 checkpoint 消失”的行；reconciler 可从 checkpoint 重建。
- legacy `replace_code_todos()` 只替换 `workflow_run_id IS NULL` 的旧行，不删除 graph-owned rows；Code graph toolset 不向 LLM 暴露 `todo_write`，todo 是 graph projection。旧记录没有 ID 时保持 legacy ownership，不用标题模糊匹配；新 graph 首次接管按 plan step 创建新 owned row。
- 这会明确废止旧的“goal_mode OFF 时 state.db 字节不变/绝不创建 goal_tasks 表”测试契约：v17 正式迁移总是创建 schema，但 flag OFF 仍不得写任何 goal row。更新 `test_goal_tasks_db.py` 为“schema 可存在、无业务写入”。

#### 17. Research core 与子代理边界

- 抽出无 `persist/finalize/receipt/final-assistant` 的 `research_core` checkpointed subgraph；DeepResearch wrapper 在 core 后负责 report/citation persist 和 delivery，PPT 只消费 core 结果，绝不提前发送“调研完成”。
- 移除 `research_tools.py::_js_render_run_count` 进程全局计数；FetchPort 从 WorkflowContext 读取并通过 state patch 递增 `js_render_used`，每 run hard cap 4，两个并发 research run 完全隔离。legacy wrapper 使用一次调用内的局部 budget object 保持相同上限。
- Complex Code v1 禁止使用进程内 `spawn_subagents/await_subagents/spawn_team`，也不承诺它们跨重启；这些工具在普通短聊天/ReAct 中原样保留并做回归。Code graph 的 tool allowlist 只含可通过 EffectPort 观测的工具；模型提出被禁控制工具时得到 `unsupported_in_durable_workflow`，回到下一次 proposal 自行完成，禁止自动转 legacy。后续若要 durable 子代理，必须建 child run/checkpoint 协议，不能把 `asyncio.Task` 塞进 state。

#### 17A. Dynamic capability 与 Code 会话引用

- Code run 创建时保存 `CapabilitySnapshot(tool_name,source,schema_hash,spec_version,effect_policy,lifecycle_hash,outcome_parser_hash)`；恢复逐项校验。任何 plugin/MCP 未显式声明版本化 EffectPolicy+outcome parser时一律 unknown/opaque_manual，绝不根据 ToolRegistry 默认 read_file 自动重试/回放。只有显式 idempotent_read 才自动执行；write还需 lifecycle callbacks/hash。
- 用户明确要求或 tool_search 发现 unknown dynamic tool时，Graph 进入 durable unsupported_dynamic_tool HITL，选项 allow_once_opaque/continue_without/cancel；allow once无论工具自称读写都按 opaque effect、崩溃 uncertain。扩展 tool_search 返回 source/durability，但安全判定只信 CapabilitySnapshot/ToolSpec声明。
- `WorkflowSessionRef(base_session_id,code_session_id,delivery_session_id,project_root_hash,base_epoch,code_epoch)` 是 run 必填。DeepResearch/PPT 的 base=delivery、code=null；Complex Code 同时保存聊天 base、CodeMode project session 和 delivery target。
- 普通 `session.delete` 按 base/delivery refs 加锁、tombstone并 cancel 所有关联 runs；`code_session_delete` 按排序后的 base/code locks，递增 code epoch、cancel code runs、删除 CodeMode/todo projection但保留 base messages，并向仍有效 delivery session 投递一次取消。todo/effect/final adapters 同时校验 base/code epoch，迟到结果不能重建已删 code project。

#### 18. 持久循环预算

- run 创建时把有效配置复制进 `budgets`，恢复不重新读全局配置；`loop_counters` 只由对应 node reducer 单调递增。v1 硬上限：DeepResearch `gap_rounds<=2`（quick/standard 初始预算 1、deep 2）、PPT outline revisions 2、visual revision rounds 2、Code 总 proposal turns 20、完成审计后的 fix rounds 3；用户参数只能向下收紧，不能超过硬上限。
- 达预算时进入明确 conditional edge：Research 用已有证据 synthesize 并标 degraded，PPT 发布最后一次通过 preflight 的 deck 或 failed，Code 进入 honest completion audit/blocked；不得重置计数或无界循环。每条路径有 crash/resume 预算不增加/不重置测试。
- manifest 同时固定 LangGraph `recursion_limit`：DeepResearch 64、PPT Pro 128、Complex Code 128，且 compile test 证明大于按硬预算推导的最大 supersteps。所有调用从 manifest 传该值；`GraphRecursionError` 映射 `blocked(reason=recursion_limit_before_budget_exit)` 并保留 checkpoint，不能伪装 provider error。

#### 19. 保留与清理顺序

- 默认：terminal run 的 full checkpoints/pending writes/events/trace/blob refs 保留 30 天；evaluation result 和被其引用的最小 run tombstone 保留 180 天；用户 dataset/experiment 定义不自动删；现有 Artifact/Receipt 继续遵循 30 天产品策略；无 ref blob/orphan temp 24 小时后清理。waiting/running/retryable/cancelling/blocked runs、未 resolved decision、未 delivered outbox 永不按年龄自动删。
- 清理顺序固定为：重试/确认无未投递 delivery -> 保留 evaluation/run tombstone -> 删除 checkpoint owner -> reachability GC pending/checkpoint refs -> 删 trace/eval payload refs -> ref_count=0 grace -> 文件；Receipt JSONL 由升级后的 temp-replace compactor 按每条 deterministic receipt id/created_at 清理，只有空 session 文件才删除，不能先删 workflow delivery 证据。所有天数进配置且范围校验，测试使用 ClockPort。

#### 20. Session 删除与异步交付

- v17 新增 `session_delivery_state(session_id PRIMARY KEY,epoch,deleted_at,reason)`。workflow start、run-create/accepted event、delivery 与 delete 全部共用 per-session async lock：start 在锁内重读 state.db epoch/deleted_at，创建 workflow run+accepted event 成功后才释放；delete 因而一定能看到并 cancel 已创建 run，或先 tombstone 使后续 start 拒绝。SessionDB `append_message_if_epoch()` 同事务条件 insert，重复不触发 embedding hook；Receipt/WS 在锁内校验 epoch。delete bump epoch+tombstone 后 fenced cancel/discard UI channels；startup recovery 补 tombstoned session 的半完成 cancel/discard/receipt-ledger closure。测试覆盖 start 在 tombstone 前后两个窗口。
- 已持久化到用户选择的外部文件不因删聊天而删除；effect journal 仍完成 reconcile，但禁止新的聊天投递。测试覆盖删除与 terminal/staging/late thread result 的竞态。

#### 21. Code 路由真值表

- 路由在 plan 前只使用现有 ask/task intent、code mode 和 deterministic signals，优先级固定：显式 `/` 控制命令按原 handler；code mode OFF 按原聊天；存在 write/edit/create/delete/run-shell/build/test/fix/implement 信号或 ask+action 混合请求 -> Complex Code graph；只有 explain/read/review/search/grep 且无 action signal -> ReAct；无法分类但 task intent 且涉及 workspace -> graph；ask intent 且无 action -> ReAct。冲突时 action signal 胜出，绝不先调用 LLM plan 再决定。
- 固定中英文 corpus 覆盖：解释代码、只读 review、查找引用、写单文件、跨文件实现、运行测试、修 bug、直接文件写、解释后修改、模糊 workspace task；router golden test 固定结果。普通非 code direct tools 与聊天不受影响。

#### 22. 轮次与退出规则

- plan 挑战、完成度审计、testcase 迭代每类最多 6 轮。
- 第 6 轮仍存在 blocker 时停止执行，结果标 `BLOCKED`，记录 blocker、已尝试修正和需要的用户/外部条件；禁止继续空转。

## 文件影响清单

| 文件/目录 | 职责 | 本次改动 |
|---|---|---|
| `backend/pyproject.toml` / `backend/uv.lock` | Python 依赖/锁定 | 加并锁定 LangGraph、checkpoint protocol 与 SQLite reference saver，补 package data。 |
| `backend/deskpet-backend.spec` | PyInstaller | 收集 LangGraph/checkpointer 动态模块并做 frozen smoke。 |
| `backend/deskpet/memory/migrations/` | state.db 正式迁移 | 增加 v17 迁移，收口当前 lazy DDL 基线。 |
| `backend/deskpet/workflows/` | 新的通用 runtime | contracts、definition、runner、fenced saver/store、effects、HITL、recovery、outbox、trace、evaluation、IPC。 |
| `backend/deskpet/workflows/definitions/v1/` | 不可变生产图注册表 | DeepResearch、PPT Pro、Complex Code v1 graph；后续升级新增版本目录。 |
| `backend/main.py` / `backend/context.py` | 生产接线 | 注册 runtime/store/recovery，创建 trace context，路由 IPC 与复杂任务。 |
| `backend/agent/agent_loop.py` | ReAct primitive 与短聊天 | 抽出 LLM proposal/tool dispatch primitive 并注入 trace/eval context；短聊天行为保持。 |
| `backend/deskpet/tools/registry.py` | 产品工具入口 | 接 workflow execution context、effect id、trace span 与 Receipt 关联。 |
| `backend/deskpet/tools/receipt.py` / `receipt_store.py` | 执行证据 | 增加 sig v2、accepted/delivered、deterministic append-once、尾部修复和逐条 compaction；v1 canonicalization/验签保持。 |
| `backend/deskpet/agent/assembler/` | 上下文装配 | 统一 trace emitter，保留旧 decisions projection。 |
| `backend/deskpet/tools/research_tools.py` | DeepResearch | 抽出可重入阶段函数，legacy wrapper 与 graph adapter。 |
| `backend/deskpet/tools/ppt_tools.py` / `ppt_outline_store.py` | PPT Pro | graph 节点化，outline interrupt、逐页/渲染 checkpoint，旧编排器作回退。 |
| `backend/deskpet/agent/external_evaluator.py` 等 gates | 评测 | 适配统一 EvaluationOutcome，修高后果 malformed JSON fail-open。 |
| `resources/diagnostic-redaction.json` / Tauri resource 配置 | 跨进程脱敏规则 | Python/UI/Rust 共用、随 frozen/Tauri 打包；缺失或解析失败时 fail closed。 |
| `tauri-app/src/types/messages.ts` / WS store / `App.tsx` | 跨层契约 | 新 workflow run/trace/checkpoint/eval/decision/final 消息；按 run/request/turn 归属，不触发普通 chat final reducer。 |
| `tauri-app/src/components/ContextTracePanel.tsx` | 旧 context trace | 改成统一 Run Inspector 入口，Context 成为一个过滤视图。 |
| `tauri-app/src/components/workflows/` | 新 UI | run 列表、拓扑/时间线、checkpoint、fork、评分、确认对话框。 |
| `tauri-app/package.json` / `package-lock.json` | 图可视化依赖 | npm 精确固定 `@xyflow/react` 为 `12.11.2` 并更新 lock；按 250KB gzip/截图/console 门决定保留或执行 CSS DAG fallback。 |
| `scripts/eval_workflows.py` | 本地回归评测 | 运行 dataset/experiment 并比较版本。 |
| `backend/tests/test_workflow_*.py` | 自动化 | runtime、迁移、故障注入、三图、trace/eval/replay。 |
| `tauri-app/src/**/__tests__` | 前端自动化 | Run Inspector、decision/replay/eval 交互。 |
| `testcase/2026-07-10-durable-graph-workflows-trace/` | 真机用例 | 三类任务、重启恢复、Trace、fork、人工评分。 |

## 任务清单

### Task 1 - 依赖与 frozen 可行性 spike [AC-1, AC-3, AC-17]

- 改动文件：`backend/pyproject.toml`、`backend/uv.lock`、`backend/deskpet-backend.spec`、新增 `backend/tests/test_workflow_dependency_smoke.py`。
- 修改方式：锁定 `langgraph==1.2.8`、`langgraph-checkpoint==4.1.1`、`langgraph-checkpoint-sqlite==3.1.0` 并用 `uv lock` 提交 transitive lock；设置 `LANGGRAPH_STRICT_MSGPACK=true`、no pickle fallback。state 只存 JSON-safe；协议 allowlist golden 仅含 pinned LangGraph 的 `Interrupt/Send/Command` 和 versioned DeskPet `WorkflowNodeError`。node wrapper 把任意 provider/tool exception 先规范化为该错误的 `code/message_ref/retryable`，不序列化原异常对象；spike 记录 4.1.1 实际 module/type path，若与清单不同立即更新清单和 golden，不扩大到整个模块。
- 验证：最小图以 sync durability 完成 normal/error/interrupt/fan-out；验证 pinned saver async 全接口 delete_for_runs/copy_thread/prune，compiler 拒绝 DeltaChannel且 history 不被生产调用；wrapper 原样抛 GraphBubbleUp/GraphInterrupt/CancelledError；恶意 exception 只留 ErrorEnvelope；frozen import 完整。
- 依赖：无。若 spike 失败，停止并在挑战阶段重新决策，不静默切到手写引擎。

### Task 2 - 修复 state.db v17 迁移基线 [AC-17]

- 改动文件：`backend/deskpet/memory/migrations/009_memory_v2_v17.sql`、`migrator.py`、`memory_v2_schema.py`、`session_db.py`、`ppt_outline_store.py`、`skill_codifier.py`、`todo_write_tool.py` 与迁移测试。
- 现状：`TARGET_SCHEMA_VERSION=17`，有效 SQL/test 基线仍停在 16，部分表由业务模块 lazy DDL。
- 修改方式：009 使用 Python migration callback，不依赖 SQLite `ADD COLUMN IF NOT EXISTS`。对每张 lazy table先查 `sqlite_master/PRAGMA table_info/index_list`：表缺失则执行 canonical CREATE；列缺失逐条 ALTER ADD；列已存在先校验 type/default/not-null兼容；需要约束重建时 create `_v17_new`、copy/validate row count、rename in one transaction。migration marker/user_version 只在全部成功后写；失败由现有 backup restore。覆盖 shared memory tables、独立 goal lazy组合、todo/message ownership和 session delivery state。
- 验证：fresh DB、真实 v16 fixture、已有 lazy table fixture、graph/legacy todo 并存、workflow_event_id 重复 append 返回同一 message、session tombstone 阻止迟到 append、goal flag OFF 零 row、失败回滚全部通过；migration 只执行一次且第二次启动不新增备份。
- 依赖：Task 1 可并行。

### Task 3 - 建立 DeskPet workflow contracts [AC-1, AC-2, AC-15, AC-16]

- 新增：`backend/deskpet/workflows/contracts.py`、`definition.py`、`errors.py`、`__init__.py`。
- 修改方式：定义 `WorkflowDefinition`、`NodeDefinition`、普通/条件 Edge、`WorkflowRunStatus`、`NodeStatus`、`NodeExecutionIdentity`、`RetryPolicy`、`StatePatch`、`WorkflowContext`、`EffectPolicy`、loop budgets/recursion limit；node wrapper 使用公开 `Runtime.execution_info`；包装 LangGraph，不向业务层暴露框架类型；编译产出 definition/state/prompt/tool/policy manifest hashes并强制 durability sync。
- 校验：compile 时拒绝无 entry、缺节点、非法 edge、不可达 required node、重复 node、未知 reducer；interrupt-capable node 必须经 dedicated barrier 且 exclusive superstep；write ToolSpec 必须 inventory 分类；workflow/version/state schema/implementation bundle/durability/recursion limit 必填。
- 验证：纯单测覆盖合法线性图、条件循环、并行 fan-out 与全部错误。
- 依赖：Task 1。

### Task 4 - workflow.db、blob store 与 checkpoint adapter [AC-2, AC-3, AC-17]

- 新增：`store/schema.py`、`store/run_store.py`、`store/blob_store.py`、`store/checkpointer.py`。
- 表：`workflow_schema_migrations`、`workflow_start_requests`、`workflow_fork_requests`、`workflow_runs`、`workflow_session_refs`、`workflow_capabilities`、`workflow_nodes`、`workflow_node_attempts`、`workflow_decisions`、`workflow_grants`、`workflow_effects`、`workflow_effect_targets`、`workflow_node_effects`、`workflow_checkpoint_effects`、`workflow_target_reservations`、`workflow_checkpoints`、`workflow_pending_writes`、`workflow_checkpoint_owners`、`workflow_blobs`、`workflow_blob_refs`、`workflow_events`、`workflow_deliveries`、`workflow_receipt_ledger`、`trace_runs`、`trace_spans`、`evaluations`、`eval_datasets/examples/experiments/results`。
- `workflow_runs` 保存完整 identity/fence/status/timestamps/error；`workflow_nodes(node_execution_id PK,...,latest_attempt,latest_status,updated_at)` 唯一 `(run_id,base_checkpoint_id,invocation_key)`；`workflow_node_attempts(node_execution_id,retry_attempt,task_id,task_path,status,started_at,ended_at,error_ref,PRIMARY KEY(node_execution_id,retry_attempt))` 保存不可覆盖历史。索引 run/status/task/lease/session；时间均 ClockPort UTC，terminal attempt ended_at 非空。
- 修改方式：WAL/busy timeout/schema/CAS；实现 fenced saver：`aput_writes` 按 normal/error/interrupt 分类为 succeeded_pending/failed/waiting，exclusive interrupt 原子 open decision；`aput` 写 full checkpoint/正式 refs/head/outbox promotion/succeeded；两者校验完整 fence。checkpoint 是 authority，run/node 是 projection；大型值按 SHA-256 blob refs。
- 验证：fresh/migrate/rollback、并发 CAS、两个 saver 阶段中 lease/cancel 被抢后整事务回滚、fan-out 一个 task 成功另一个失败时 pending write 恢复、稳定 task identity、损坏 JSON/blob、large state 不内联、checkpoint list/get/fork、owner reachability GC、outbox keys/seq 单调。
- 依赖：Task 3。

### Task 5 - Runner、lease、恢复与取消状态机 [AC-3, AC-16]

- 新增：`runner.py`、`lease.py`、`recovery.py`。
- API：`start()`、`run()`、`resume()`、`request_cancel()`、`recover_expired()`、`get_state_history()`。
- 修改方式：run/node 在执行前 CAS 获取 fenced lease；15 秒 heartbeat/90 秒 TTL，所有提交和 custom saver 写入带 epoch+version+allowed status；`request_cancel` 原子 bump 两个 fence；所有 invoke/resume 用 manifest sync durability/recursion limit；按完整状态机处理 wait/reclaim/cancel；启动扫描 nonterminal/succeeded_pending/uncertain；checkpoint 读做 hash+strict serde，缓存错自动重建，本体坏 quarantine 并阻塞，最近有效 single-writer ancestor 只通过用户确认 fork 恢复；按 manifest/implementation bundle hash 加载版本，缺失即阻塞。
- 验证：完整 FaultInjector 矩阵、双 DB connection runner 竞争、cancel 发生在 node return 与 saver write 之间时拒写、N+1 不早于 N checkpoint commit、heartbeat/reclaim、waiting 零 lease、projection/outbox 重建、重复 resume/cancel、active_nodes fan-out 与确定性 reducer conflict、GraphRecursionError 映射、graph/version hash unavailable、uncertain effect 取消收敛、全部合法/非法状态转换。
- 依赖：Task 4。

### Task 6 - Effect Journal 与 Receipt/Artifact 关联 [AC-4, AC-9, AC-12, AC-16]

- 新增：`effects.py`、`adapters/tool_executor.py`；修改 `ToolSpec`/ToolRegistry、Receipt 与 ReceiptStore。
- 修改方式：ToolRegistry 先用 `prepare_call(stable_call_id)` 产生含 `PreparedTarget` 的 immutable call 并预留默认输出路径；fingerprint 只基于最终参数/input blob hash/policy version。为 registry 全量 ToolSpec 生成 committed effect inventory，graph write allowlist 必须拥有 staged lifecycle 或明确 opaque-manual；file/Office create/append/edit 使用同目录 staging、copy-on-write、format validate、precondition CAS、atomic replace；副作用 intent 先落库；sync handler 用 shield future 保留晚结果；安全读可 retry；成功 effect 按 links/policy 复用；uncertain 写操作 reconcile；危险重放创建 decision。
- ToolRegistry新增兼容execute_tool_outcome、三态outcome和auth；ToolSpec outcome parser字段+v1逐工具inventory/golden与manifest集合严格相等；unknown opaque必须reconciliation；动态门/grant/target/Receipt保持。
- 验证：全 ToolSpec inventory 无漏分类、同秒并发默认路径不碰撞、create/replace/append/edit staging 与 rollback、prepare 后 tool disabled/policy/schema 变化拒绝、授权 claim 后 dispatch 前崩溃可同 effect 幂等重入、已存在文件/部分 Office 文件不能误判成功、文件写在“commit/receipt 前崩溃”、线程池 timeout 后晚成功/失 fence、args 变化不复用、fork 仅看 checkpoint effect links、重复 replay、Receipt 半行修复/temp-replace crash/逐条 retention 去重、v1/v2 HMAC 与 VerifyGate 语义。
- 依赖：Task 4、Task 5。

### Task 7 - Durable Human-in-the-loop [AC-5, AC-7, AC-12, AC-16]

- 新增：`human.py`；修改 workflow IPC 与 decision store。
- 修改方式：decision prepared 后 interrupt；saver 持久 interrupt id/task/ns并 open/waiting；resolve 只保存 validated DB response；runner 以同 thread/config 调 `Command(resume={interrupt_id: db_response})` + sync durability。重复/过期 nonce CAS 拒绝；expiry/cancel 关闭 decision/grant；permission grant 同事务绑定 effect；UI 只显示 open。
- 兼容：plan confirm、ask clarification、PPT outline、permission、skill candidate 通过 typed adapter 复用；旧 Future 仅 transport cache，不一次删除旧消息类型。
- 验证：prepared 后/interrupt 前崩溃、interrupt write 分类、进程重启后点击继续、双击、旧卡、过期/reopen、cancel 关闭 grant、修改 state 后继续。
- 依赖：Task 5、Task 6。

### Task 8 - 统一 Trace Store 与上下文传播 [AC-10, AC-11, AC-17]

- 新增：`trace/models.py`、`trace/context.py`、`trace/store.py`、`trace/redaction.py`，含统一 `trace_runs` 索引。
- schema：`trace_id/span_id/parent_span_id/run_id/session_id/workflow/version/node/lifecycle_stage/kind/status/start/end/duration/attributes/input_ref/output_ref/error/privacy_class`。
- 修改方式：WorkflowContext 显式传播，线程池 copy_context + immutable SpanContext；root/core span 同步持久，checkpoint 只关联已存在 spans；text/voice/workflow 都写 trace_runs；workflow core 故障 blocked，text/voice root 故障在 LLM 前返回 typed error，detail 才可 degraded；Python/Rust 共读 redaction；blob refs 安全清理。
- 验证：父子树、完整 input/output/error/latency refs、并发子 span、thread context bridge、普通 ReAct/voice run 查询、trace DB 故障稳定 blocked、redaction、rotation/retention、旧 iteration JSONL adapter。
- 依赖：Task 4。

### Task 9 - Harness/LLM/Tool/Gate instrumentation [AC-9, AC-10]

- 修改：`main._run_chat`/workflow control commands、`pipeline/voice_pipeline.py`、`ContextAssembler.assemble/feedback`、`AgentLoop.run`、`ToolRegistry.execute_tool`、`backend/providers/openai_compatible.py` 与 provider chain、DeepResearch rerank、PPT visual review、completion gates、workflow 相关 Tauri artifact callback。
- 修改方式：每类 ingress 创建 root；AgentLoop/LLM/tool/subagent/gate spans；增加 DispatchOutcome 与 ASYNC_HANDOFF：accepted_async 单 call 结束当前 ReAct 回合且绕过 completion claim，mixed batch 在零执行 preflight 拒绝；后台 workflow 独立 trace；所有 return/error terminal；IterationTracer 兼容 export。
- 修复：真实 session/run correlation；trace 默认开启；Context decisions 写持久 trace，同时保留旧 UI projection。
- 验证：短聊天/workflow完整 tree；accepted_async 单 call 发 chat_v2_handoff_final 并清 working但 WorkflowCard仍running；mixed batch零执行；异常/max/cancel终态；metrics低基数。
- 依赖：Task 6、Task 8。

### Task 10 - 统一 EvaluationOutcome 与持久评测记录 [AC-13, AC-14]

- 新增：`evaluation/models.py`、`evaluation/store.py`、`evaluation/adapters.py`、`evaluation/runner.py`。
- 修改：VerifyGate、GoalChecker、ExternalEvaluator、SelfCheckGate、PPT visual review 调用点。
- 修改方式：统一 evaluator name/version/type、score、verdict、labels、explanation、evidence refs、degraded；包装旧返回类型；每次执行关联 run/span。
- 修复：ExternalEvaluator malformed JSON 在高后果 + conservative 模式不得返回 10/pass；不再依赖被 metrics 白名单丢弃的事件保存结果。
- 验证：code rule、LLM judge、人工、pairwise、异常降级与旧 gate 行为回归。
- 依赖：Task 8。

### Task 11 - Workflow service wiring 与 IPC [AC-5, AC-11, AC-12, AC-13, AC-16]

- 新增：`service.py`、`ipc.py`；修改 `context.py` 白名单和 `main.py` lifespan/control dispatcher。
- IPC：runs list/detail、trace tree、checkpoint history、decision resolve、resume/cancel、fork、evaluation submit、experiment compare、events-after-seq，以及 delivery list/retry/discard。delivery mutation 必带 expected version，写 audit event并返回当前状态。
- 修改方式：start identity/snapshot协议；accepted event唯一权威；start/delivery/delete锁；stable outbox/receipt/recovery；所有card-mutating history events携完整payload并用统一reducer先hydrate再seen。
- 验证：ServiceContext/manifest/wiring contract、动态 WS handler、terminal intent 后/full checkpoint 前崩溃不可见、未连接 UI 时恢复、每个 channel 的“目标写成功/mark delivered 前崩溃”、session 删除竞态、某 channel 失败不重复其他 channel、普通 stream + 两 workflow final 不互相结束 working state、重连 after_seq/history_expired 与 event_id 去重。
- 依赖：Task 5、6、7、8、10。

### Task 12 - DeepResearch 阶段函数抽取（行为保持） [AC-6, AC-9]

- 修改：`research_tools.py`，新增 `definitions/research_core.py`、`deep_research_nodes.py`。
- 修改方式：把 plan、expand、search、fetch/extract、direct、score/rerank、gap、synth、citation 拆成无产品交付副作用的 `research_core` async nodes；把全局 `_js_render_run_count` 改为 WorkflowContext/state 的 per-run FetchPort budget（legacy 用调用局部 object）；persist/finalize/delivery 只在 DeepResearch wrapper；原 `deepresearch()` 串联 core + legacy terminal adapter，确保输出字节/coverage 兼容。
- 大型 passages/search payload 写 blob ref；并行结果使用稳定排序/reducer。
- 验证：现有 DeepResearch 全套先保持全绿；新增 stage contract、两个并发 run 的 JS budget 隔离和 flat-vs-extracted parity tests。
- 依赖：Task 3、4。

### Task 13 - DeepResearch Graph 接入与恢复 [AC-3, AC-6, AC-10, AC-15]

- 新增：`definitions/deep_research.py`；修改 `_handle_deepresearch()` 与配置。
- 图：normalize -> checkpointed research_core(plan -> expand -> search/direct map -> fetch -> score -> gap conditional loop -> rerank -> synth -> cite) -> persist -> finalize。
- 修改方式：默认 `[workflows].deep_research=true`；graph tool 使用 accepted_async，调用方只收到 run/request/turn id 与“调研已开始”；fetch 恢复复用 search checkpoint；fan-out 使用公开 execution info/stable map key；budget/recursion 固化；terminal node只返回 Report/ArtifactCard/final-assistant OutboxIntents，full aput 后 delivery；legacy kill-switch 保留原阻塞行为。
- 验证：happy path parity、accepted 后 success/error/cancel 都有且仅有一个 closing Receipt、调用方断开仍 delivery、fetch crash/restart、fan-out pending 恢复、无结果、gap budget、旧 schema。
- 依赖：Task 5、6、8、11、12。

### Task 14 - PPT Pro Graph 与持久大纲/逐页 checkpoint [AC-4, AC-5, AC-7, AC-9, AC-15]

- 新增：`definitions/ppt_pro.py`、`ppt_pro_nodes.py`；修改 `ppt_tools.py`、`ppt_outline_store.py`、PPT notifier/receipt adapter。
- 图：normalize -> research_core subgraph -> outline -> wait decision -> revise loop -> preflight -> image probe -> per-slide image map -> render -> preview -> visual evaluate -> revision loop -> publish -> terminal receipt。research core 用 `checkpointer=True`/继承父 checkpointer，并固定 parent run/node 与 child namespace 映射，使内部 search/fetch/rerank 可独立恢复且不执行 DeepResearch terminal delivery。
- 修改方式：`ppt_outline_history` 成为 projection；generic decision 是权威；生图/渲染/打开走 staged/opaque EffectPort；每页 stable slide id 作为 map key，image pre/post hash/path、render mode、pptx/preview/review refs 入 state；默认输出含 run/call id 并预留；outline/visual budget与 recursion limit 持久；tool metadata 改 accepted_async，terminal 只 stage delivered receipt/artifact/final intents；默认 graph ON，只有启动新 run 前对应 flag false 才路由旧 orchestrator，graph 已开始后禁止自动切 legacy。
- 验证：重启后大纲继续、不重复 research/outline/图片；slide pending 恢复；revision budget；success/error/cancel closing Receipt；模板 fallback、visual review、artifact、取消；无 DeepResearch 提前交付。
- 依赖：Task 6、7、10、11、13。

### Task 15 - Complex Code Graph 与 ReAct 节点封装 [AC-5, AC-8, AC-9, AC-15]

- 新增：`definitions/code_task.py`、`code_nodes.py`、`routing.py`、`proposal_state.py`；修改 `main._run_chat` 的 code plan-confirm 段、`agent/agent_loop.py`、`agent/termination.py`、`agent/context_manager.py`/`history_compactor.py`、`deskpet/agent/convergence_controller.py`、completion/evidence/self-check/verify gate adapters。
- 路由：实现规范 §21 的 deterministic priority/truth table 与中英文 golden corpus；ask/read-only explain/review/search 保持 ReAct，write/edit/build/test/fix/implement 与混合 action 进入 graph，未知 workspace task 取 graph，graph 内 plan 可走 trivial single-step 分支，消除“先 plan 才能路由”的循环。
- 重构：从 `AgentLoop.run()` 提取版本化 `propose(ProposalStateV1)->ProposalOutcomeV1` 与 `dispatch(prepared_calls)` primitives；按规范逐字段序列化 messages/compaction/counters/tool signature/gates/evidence/provider fallback/plan todo/error，不携带进程对象；原短聊天 `run()` 继续组合调用并做 golden event/tool/gate parity；Complex Code graph 不把 LLM 与 tool batch 放在同一 checkpoint 边界。
- 图：intake -> clarify -> plan -> wait approval -> llm_proposal -> tool_execution -> completion_decision loop -> test -> audit -> fix proposal/effect loop -> finalize。`llm_proposal` checkpoint 固定消息、最终 prepared args 和稳定 call id；重启从该 checkpoint 恢复且不再次调用 LLM。plan steps 生成稳定 `workflow_step_id`，todo/goal 用 graph-owned upsert projection；总 proposal budget 20、audit fix budget 3 固化 state；tool effect 绑定 active step id + prepared args hash。
- 工具边界：Code v1 排除 process-local subagents/todo_write；短聊天不变。run 固化 CapabilitySnapshot；dynamic read/lifecycle write可 opt-in，未知 dynamic write 进入 allow-once-opaque/continue/cancel HITL，不走死路或自动 fallback。WorkflowSessionRef 同时绑定 base/code/delivery ids 与 epochs。
- 验证：纯问答零 graph；复杂 action 默认 graph；proposal checkpoint 后崩溃不重复 LLM；tool batch 部分成功后只复用 committed effects；graph/legacy todo 并存与 checkpoint reconciliation；预算跨重启；未完成 todo 不假完成；短聊天子代理能力回归。
- 依赖：Task 6、7、9、10、11。

### Task 16 - Replay、fork 与副作用确认 API [AC-4, AC-12]

- 新增：`replay.py`；扩展 IPC。
- 修改方式：只读 replay；safe root fork使用workflow_fork_requests saga，fork_key随saver metadata，child checkpoint与saga checkpointed同事务；prepared恢复先查已有checkpoint；可信runtime重写branch identity；waiting/fan-out/subgraph禁用；危险effect确认。
- 验证：source版本冲突、child row后/aupdate前、child checkpoint commit后/service返回前崩溃；两branch交错推进/重启各用自身head；原run不变、namespace拒绝、lineage/GC、identity重写、危险effect确认与复用。
- 依赖：Task 6、7、8、11。

### Task 17 - Run Inspector 基础 UI [AC-11]

- 修改：`ContextTracePanel.tsx`；新增 `components/workflows/RunInspectorPanel.tsx`、`RunList.tsx`、`TraceTree.tsx`、`CheckpointTimeline.tsx`、`WorkflowGraph.tsx`。
- 修改方式：ContextTrace 入口升级为 Runs/Trace/Checkpoints/Evaluations tabs；显示全部 node 状态与 recovery action；Run detail 增加 blocked/retry-wait delivery 列表、attempt/error、下一重试时间及“重试/丢弃”按钮，按钮走 expected-version IPC并显示审计结果；展开模型/工具/evaluator span；敏感详情 redacted。
- 图可视化独立 spike 使用 npm exact dependency `@xyflow/react: "12.11.2"` 并提交 `package-lock.json`；gzip 增量<=250KB、桌面/移动非空、无 console error 才保留，否则删除依赖/更新 lock 并切预定义 CSS DAG；移动端上下布局，所有按钮用现有 Icon 和 tooltip。
- 验证：vitest/RTL 覆盖空态、加载、错误、长文本、窄宽、非空 DAG、trace tree、dead-letter retry/discard/stale version。
- 依赖：Task 11。

### Task 18 - Replay/HITL/Eval UI [AC-5, AC-12, AC-13]

- 新增：`DecisionPanel.tsx`、`ReplayDialog.tsx`、`EvaluationPanel.tsx`；修改 WS store/types。
- 修改方式：open waiting decision 可跨重启显示，prepared 不显示；过期提供重新发起/取消；重复点击禁用；fork 只允许 root checkpoint/state patch；危险副作用显示明确确认；人工评分用 score/labels/comment；比较 experiment 版本；workflow_final 按 run/request/turn 独立归属，不改变普通聊天 working 状态。
- 兼容：旧 PlanCard/PPTOutlineCard 通过 adapter 发送 generic decision，过渡期保留原视觉。
- 验证：消息契约、double-submit、stale decision、fork confirm、评分写入、文本不溢出。
- 依赖：Task 7、10、16、17。

### Task 19 - 配置、默认启用、诊断与保留 [AC-15, AC-17]

- 修改：`config.toml`、`config.py`、`resources/diagnostic-redaction.json`、PyInstaller/Tauri resource 配置、diagnostic bundle、README/设置说明。
- 配置：`[workflows] enabled/deep_research/ppt_pro/code_complex=true`；显式 false 是新 run 的 legacy kill-switch，已经创建的 graph run 仍按记录版本恢复，禁止运行中自动 fallback；trace retention/detail；eval local-only。
- 修改方式：配置 terminal 30 天、evaluation/tombstone 180 天、orphan 24 小时；live nonterminal/active decision/undelivered/retained eval 永久 pin implementation bundle；按 delivery -> tombstone -> owner/reachability -> refs -> grace 清理；Receipt ledger 权威、JSONL compaction；version modules/callback/lock hashes随 frozen 打包校验；redaction fail closed。
- 验证：factory backfill 幂等、显式 false 保留、默认 ON、旧配置升级、ClockPort 边界/未投递保护/清理顺序、frozen graph v1 manifest 可恢复、篡改 v1 hash CI 失败、redaction resource 缺失 fail closed、诊断无敏感内容。
- 依赖：Task 8、11、13-15。

### Task 20 - 本地 Eval Suite 与版本比较 [AC-13, AC-14]

- 新增：`scripts/eval_workflows.py`、`backend/deskpet/workflows/evaluation/fixtures/`。
- 三个固定场景：DeepResearch 证据/引用轨迹、PPT outline/render/QA 轨迹、Complex Code write/test/fix 轨迹。
- 修改方式：版本键固定为 workflow/model/prompt/tool-schema/evaluator hashes；支持 deterministic adapters 和 `--live`；内置 fixtures 明确 Research 引用、PPT preview/QA、Code file hash/tests/todos 期望；live 每场景 3 次取中位，网络错误单列；支持 direct/pairwise compare。
- report schema 固定 `dataset/version_a/version_b/total_examples/denominator/pass_count/pass_rate/score_mean_median_p95/latency_median_p95/error_count_by_taxonomy/paired_wins_ties_losses/per_example_deltas/excluded_live_network_errors`；deterministic 分母包含全部 fixture，缺失/异常均算 fail；live 网络错误只从质量分母排除但单列数量。CLI gate：任一 required fixture fail、pass rate 下降、配置阈值分数下降或错误数增加即非零；同时输出 JSON 与简表。
- 验证：同版本重复结果稳定；故意破坏节点/引用/测试时 evaluator 变红；两个版本的通过率、分数、耗时、错误完整聚合与 pairing 正确；CLI exit code 可做 CI gate。
- AC-14 落地结果：默认 CLI 不再读取 fixture 预置 `output`，而是由 `scripts/workflow_eval_adapter.py` 用确定性 fake ports 实际实例化并执行 `deep_research_v1`、`ppt_pro_v1`、`code_v1`；结果从 node observer、最终 graph state 与端口执行记录投影。
- 每个 fixture 对每个版本执行 3 次；质量取中位，同时对去除 `latency_ms` 后的完整输出做确定性指纹比较，不一致标记 `nondeterministic_output` 并使 gate 失败。
- 回归门禁证据：`graph-v1` 对比真实编译执行的 `graph-v2-regression` 候选图，DeepResearch 引用退化会造成 required fixture、通过率、均分和错误数门禁同时失败；同版本比较保持全绿。
- 验证命令：`backend/.venv/Scripts/python -m pytest backend/tests/test_workflow_eval_cli.py -q`；默认运行 `backend/.venv/Scripts/python scripts/eval_workflows.py`；回归演示 `backend/.venv/Scripts/python scripts/eval_workflows.py --version-a graph-v1 --version-b graph-v2-regression`（预期 exit 1）。
- 依赖：Task 10、13-15。

### Task 21 - 自动化、故障注入与性能门 [AC-1..AC-17]

- 新增/扩展测试覆盖 contracts、migration、runner、lease、checkpoint、HITL、effects、trace、eval、三图、IPC、UI。
- 必测故障点：除既有矩阵外，逐ToolSpec outcome golden/manifest集合门禁；handoff/waiting/progress/fault/completed/failed/cancelled各自session_message后/WS前崩溃重连参数化；两branch交错head恢复；fork checkpoint/saga原子窗口。
- 性能：同一固定本地图、预热后分别跑 no-op tracer、minimal trace、detail trace 三组（每组至少 50 次，报告中位/p95）；AC 门以 minimal trace 相对 no-op 中位开销 <=10%，detail 单独报告不偷换基线；大 state 只存 refs。
- 回归：Harness gate、DeepResearch、PPT、Code/plan/todo、ToolRegistry/Receipt、前端 tsc/vitest、Rust diagnostics。
- 依赖：Task 1-20。

### Task 22 - 真机 E2E、文档与 STATUS 收尾 [AC-18]

- 新增：`testcase/2026-07-10-durable-graph-workflows-trace/manual-test.md`、`plans/.../results.md`。用例在实现 Wave A 即先建骨架，Task 22 填结果。
- 真测：用项目规定 windows-mcp 真点击三类任务；后台重启后恢复；PPT 大纲等待；Trace 拓扑/时间线；checkpoint fork；危险 replay 确认；人工评分。
- 证据：每 case 截图、动作坐标声明、backend trace/log/run id、产物 hash；禁止 WebSocket 注入替代 UI。
- 文档：更新 `ARCHITECTURE/`、`docs/agent-harness-lifecycle.md`、README、testcase index、`STATUS/status.md` 顶部日期/模块/里程碑。
- 预定义必测：原 DR/PPT/CODE/TRACE/FORK/EVAL 用例，加 `UI-START-01` 重复 accepted 调用只一 run、`UI-HANDOFF-01` mixed batch 零执行且单 call 正确结束回合、`UI-CONC-01` 并发不串台、`UI-DELETE-01` 普通会话删除、`UI-CODE-DELETE-01` 运行中 code project 删除不复活 todo/final、`UI-DYNAMIC-01` 未 opt-in MCP/plugin write 的 durable decision。每条记录坐标、截图、run/trace/request/turn、状态和 hash。
- 依赖：Task 21。

## AC 可追溯矩阵

| AC | Tasks |
|---|---|
| AC-1 | 1, 3, 21 |
| AC-2 | 3, 4, 21 |
| AC-3 | 1, 4, 5, 13, 21 |
| AC-4 | 6, 14, 16, 21 |
| AC-5 | 7, 11, 14, 15, 18 |
| AC-6 | 12, 13, 20, 21 |
| AC-7 | 7, 14, 20, 21 |
| AC-8 | 15, 20, 21 |
| AC-9 | 6, 9, 12-15, 21 |
| AC-10 | 8, 9, 13, 21 |
| AC-11 | 8, 11, 17, 21 |
| AC-12 | 6, 7, 16, 18, 21 |
| AC-13 | 10, 18, 20, 21 |
| AC-14 | 10, 20, 21 |
| AC-15 | 3, 13-15, 19 |
| AC-16 | 3, 5-7, 11, 21 |
| AC-17 | 1, 2, 4, 8, 19, 21 |
| AC-18 | 22 |

## 执行波次与并行边界

1. Wave A（地基）：Task 1-4；Task 2 可与 3 并行。
2. Wave B（runtime）：先 Task 5 -> Task 6 -> Task 7；Task 8/10 在 Task 4 后可并行，Task 9 等 Task 6+8。禁止 6/7 无条件并行。
3. Wave C（接线）：Task 11-12。
4. Wave D（三图）：Task 13 完成 research_core/DeepResearch 后 Task 14 接 PPT；Task 15 Complex Code 可与 13 并行。共享 adapter 只由主线程合并。
5. Wave E（产品面）：Task 16-20；backend replay/eval 与 frontend 可并行。
6. Wave F（验收）：Task 21-22。

每个 wave 必须先过本 wave 的 AC 审计和便宜测试，再进入下一 wave。任何失败循环最多 6 轮，超过即按 plan-test 规则标记 BLOCKED。
