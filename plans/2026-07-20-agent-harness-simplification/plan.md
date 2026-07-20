# Plan：DeskPet Agent Harness 提炼与简化

> plan-status: finalized (phase-2 rework approved 2026-07-20)
>
> 状态：WI-1～WI-11 的安全底座已落到 `codex/harness-integration@4d38979e`；首次 WI-12 原子切换因 AC-18 功能不等价且新增层达到 8,597 LOC 而整体回退。回炉方案已完成 5 轮 challenger（最终 PASS），并于 2026-07-20 获用户 review 通过；从 R0 开始继续执行。
>
> 关联文档：[`acceptance.md`](./acceptance.md) · [`architecture-baseline.md`](./architecture-baseline.md) · [`target-architecture.md`](./target-architecture.md)

> [!IMPORTANT]
> 2026-07-20 回炉修订是本文唯一生效的后续执行路线。原 WI-1～WI-11 作为已完成、尚未接管生产 owner 的基础保留；原 WI-12～WI-14 及其中“新增独立 Ledger/Decision/Effect/Projector owner”的描述已被本修订废止，不能据此继续实现。

### Phase-2 挑战记录

| 轮次 | 结果 | 收敛内容 |
|---|---|---|
| 1 | FAIL | 诚实 LOC、单 UoW、profile 同源、原子 effect/child、only-add 与切换协议 |
| 2 | FAIL | durable promotion、activation generation、AC 追踪、唯一 dispatcher/reconciler、自动 parity census |
| 3 | FAIL | 固定 LOC manifests、可枚举 fault matrix 与计数 oracle |
| 4 | FAIL | v7 schema 前移、持久 drain manifest、无 crash gap activation |
| 5 | **PASS** | 四份文档一致，未发现新的代码可执行性硬阻塞 |

## 1. 主要矛盾

决定成败的不是“能否再加一层统一 API”，而是：

> **迁移完成后，route、trusted context、cancel、effect commit、terminal delivery 各只能有一个权威 owner；同时 ReAct 与 Native Workflow 必须保留不同执行语义。**

如果只在 `main.py`、`AgentLoop`、`WorkflowLauncher`、Voice 和 Subagent 外面套 Facade，旧 owner 仍然存在，复杂度反而增加。本计划因此按“先建立可验证契约和安全底座 → 每个入口一次性切 owner → 立即删除旧入口”的顺序执行。

## 2. 方案选择

### 2.1 采用的方案

- 收敛产品无关的 `deskpet.execution`：只保留 Run/Decision/Event/Child identity 与一个 UoW port，不另建持久 Ledger/Effect/Delivery owner。
- 收敛薄 `deskpet.harness`：`RunKernel`、两个 Driver adapter、ChildRun 协调与健康状态；公开控制操作只保留 `start/observe/signal/cancel/recover/close`。
- 把现有 `AgentLoop` 收敛为 `ReActDriver` 的内部循环；把现有 Native `WorkflowLauncher/Runner/Service` 收敛为 `WorkflowDriver`。
- Text/Code/Voice 通过 ProductTurnPreparer 保留现有产品语义，再进入 venue adapter；DeepResearch/PPT/Code 只在注册的 route/profile builder 中出现。
- 新 durable 请求复用现有 `SqliteExecutionUnitOfWork`、workflow effect/outbox 与 `PreparedToolCall`；普通 ReAct token 仍为轻量 live event。
- WI-1～WI-11 是已完成的 add-only seam；后续按 R0～R7 收敛，并由 R6 一个 activation commit 切断全部旧 owner，不保留 shadow、双写或默认 OFF。

### 2.2 放弃的方案

| 方案 | 不采用原因 |
|---|---|
| 所有请求都 durable graph | 普通 token/PCM 热路径无需 checkpoint；会增加写放大和 TTFT。 |
| ReAct 与 Workflow 继续分别修补 | 无法解决跨 venue 的身份、取消、事件和父子 owner 分裂。 |
| 只加 Facade | 不能降低 owner 数、LOC 或恢复歧义，违反 AC-16。 |
| 引入 Temporal/LangGraph/Agents SDK | 当前 Native engine 已有 lease/checkpoint/effect/outbox；换引擎扩大迁移面且没有解决产品 adapter 漂移。 |
| 把 Goal/Plan/TeamTask/WorkflowRun 合表 | 它们是不同领域对象；用 typed link 即可，不应制造万能状态表。 |

### 2.3 2026-07-20 回炉后的唯一实施方案

首次 WI-12 暴露了两个 plan 层缺陷：一是把旧 `_run_chat` 当成“胶水”整体删除，却没有先冻结其中的产品能力；二是在现有 Native Workflow/UoW/ToolRegistry 旁新增了第二套 Ledger、Decision、Effect、Outcome、Projector owner。修订后采用以下结构：

1. **Product Turn Preparer 先于 Kernel**：先完整保留 ContextAssembler、SessionDB 历史、Persona、Memory、Skill/MCP、附件、memory policy、task scope、Problem Pipeline、Plan/Preference、Supervisor hint，再把规范化请求交给 Kernel。Kernel 不吸收这些产品策略。
2. **Run Presenter 位于 Driver 之后**：统一保存和投影 assistant/reasoning、tool outcome、context usage、pipeline、approval、progress、artifact、codify 与 terminal；不得只映射 token/final/tool batch。
3. **现有 `SqliteExecutionUnitOfWork` 是唯一 durable authority**：Run、Decision、Boundary、Child command/signal、Effect link 与 durable Event 的原子推进全部通过同一连接/UoW；删除独立 `ExecutionLedger`、Decision SQLite、Effect SQLite、Continuation SQLite。
4. **复用成熟 primitive**：工具调用统一复用 `PreparedToolCall`、`NormalizedToolOutcome`、现有 permission/effect/late-reconcile/outbox；不再定义 `PreparedExecutionCall`、第二个 `ToolOutcome` 或 `LateEffectSupervisor`。
5. **Kernel 仍只有六个操作**，但不保存第二份 durable event history；短 ReAct 只保留有界 live subscriber，durable observe/recover 查询 UoW，terminal/close 必须释放进程引用。
6. **先无行为抽取，再原子切换 owner**：Product Turn Preparer/Presenter 可以先由旧生产入口调用并用 golden parity 锁定，这是职责移动而非第二执行 owner；真正的 start/recovery/decision/delivery owner 翻转仍在一个原子 activation commit 完成。
7. **所有迁移代码纳入 LOC**：`execution/`、`harness/`、新建的 turn preparer/presenter、Team reconciler 和 venue adapters 都进入 benchmark，禁止通过移出固定文件集降低数字。

修订目标结构以 [`target-architecture.md`](./target-architecture.md) 中保存的 Mermaid 图为准。

### 2.4 产品能力等价清单（删除旧入口前必须逐项全绿）

| 能力族 | 必须保留的具体行为 | 自动化证据 |
|---|---|---|
| Prompt 准备 | SessionDB 历史、Persona、长期记忆、Skill/MCP、tool policy、ContextAssembler bundle 顺序 | 同输入/同 fixture 的 canonical message 与 toolset golden diff |
| 输入附件 | 文本、图片/文件 attachment blocks、memory-policy override、explicit-new task scope | Text adapter parity + 真实 UI 附件 case |
| 问题处理 | intent/主要矛盾、澄清、short-circuit、evidence/self-check/convergence、pipeline observability | 七步 pipeline 分支矩阵 |
| Plan/Preference | plan confirm、read-only plan、preference-memory plan reuse、intent record | decision restart/duplicate/wrong-session + preference fixture |
| 上下文恢复 | supervisor hint、summary-quality reinjection、late nudge、Context OS prepared context | preflush/reinject 与 restart fixture |
| 事件与持久化 | AssistantMessage、reasoning、ContextCompacted、PipelineEvent、tool result、context usage、billing、SessionActivity | 旧/new presenter frame + SessionDB side-effect golden diff |
| 技能闭环 | ToolPathRecorder complete、SkillCandidate propose、持久 decision、accept/reject/restart/duplicate/hash mismatch、codifier confirm | detached child + decision/UoW 故障注入 |
| 长任务产品 | Code、DeepResearch、PPT accepted/progress/final、Artifact、history、PPT outline | workflow fixture + outbox delivery parity |
| Voice | ASR/TTS/VAD/barge-in 不变；共享 prepared request、decision、tool outcome 与 workflow handoff | Voice adapter contract + 真人语音/UI case |
| 生命周期 | `/stop`、UI cancel、provider chain、capability subset、child join、Goal final、terminal delivery once | cross-session/cancel/restart/final race tests |

任何一项没有等价证据，AC-18 即为 FAIL，旧分支不得删除。

## 3. 最佳实践调研与 DeskPet 适配

| 实践来源 | 可复用原则 | DeskPet 适配 | 明确不照搬 |
|---|---|---|---|
| Python `asyncio.TaskGroup` structured concurrency | 子任务应有词法/结构化 owner；父取消能聚合传播，异常能收敛 | `RunKernel` 只管理当前进程的 attached task handles；持久父子事实写 UoW，重启后由 Driver 持 lease/fence recover | 不把 TaskGroup 当持久状态；detached ChildRun 不绑定父 coroutine 正常结束 |
| Temporal workflow determinism/replay | 可恢复调度与外部副作用必须分离；重放复用已提交 Effect 结果 | 保留 Native Workflow checkpoint；两个 Driver 统一走稳定 effect id 与 UoW effect link/reconcile 原语 | 不引入 Temporal；不把 ReAct token 循环做确定性 replay |
| AWS idempotent API / client request identifier | 重试方提供稳定请求标识，服务端对同一意图返回同一结果 | `request_id + run_id + call_id + effect fingerprint` 生成稳定 effect id；超时进入 unknown/late reconcile，禁止盲重试 | 不把“同参数”简单视为同业务意图；effect id 必须包含当前 Run/调用身份 |
| OpenTelemetry context propagation | trace/span identity 显式随调用传播，而非依赖全局变量 | `RunContext` 携带 trace/root/parent/session/turn；Driver、Tool、ChildRun、RunPresenter 只接受显式 context | 不把完整 prompt/工具参数塞进 trace baggage；继续使用现有 redaction |
| Transactional outbox | 业务终态与待投递事件同事务提交，投影幂等重试 | UoW 的 terminal CAS 与 `execution_events/deliveries` 同事务；SessionDB/WS/TTS sink handler 幂等消费 | token delta 不进 outbox；不对同一终态同时使用 workflow outbox 和 Kernel outbox |

外部资料锚点：

- <https://docs.python.org/3/library/asyncio-task.html#task-groups>
- <https://docs.temporal.io/workflow-definition>
- <https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/>
- <https://opentelemetry.io/docs/concepts/context-propagation/>

## 4. 目标契约与 owner 表

> [!WARNING]
> §4.1～§4.8 是首次 WI-12 使用的 V1 契约设计，因产生双 Ledger/Decision/Effect/Projector owner 已作废，仅保留复盘。执行时只使用 §4.9 的回炉契约。

### 4.1 公开 Run API

```python
class RunKernel(Protocol):
    async def start(self, request: RunRequest, host: HostContext) -> RunHandle: ...
    async def observe(self, ref: RunRef, actor: ActorContext, cursor: int | None = None) -> AsyncIterator[RunEvent]: ...
    async def signal(self, ref: RunRef, actor: ActorContext, signal: RunSignal) -> SignalReceipt: ...
    async def cancel(self, ref: RunRef, actor: ActorContext, reason: CancelReason) -> CancelReceipt: ...
    async def recover(self, ref: RunRef, actor: ActorContext) -> RunHandle: ...
    async def close(self, ref: RunRef, actor: ActorContext) -> None: ...
```

`HostContext` 只由入口宿主创建；模型可见参数是另一对象。保留字段固定为：

`session_id/root_run_id/parent_run_id/request_id/turn_id/venue/workspace/capability_hash/provider_plan/trace_id`。

`RunRef` 包含 `run_id + expected_session_id`，`ActorContext` 来自已认证 venue（principal/session/auth epoch），不能由模型构造。六个控制操作进入 Driver 前都由 Ledger 校验 actor 对 session/root 的 ownership；后台 worker 使用由 Kernel 签发、限定 root/capability/expiry 的内部 authority。仅知道 `run_id` 不足以 observe/signal/cancel。

`start()` 在启动 Driver 前建立 bounded event subscription，并把它放入返回的 `RunHandle`；因此调用者不需要“先 start 再 observe”才能拿到首段非持久 token。后续重新 observe 先回放 durable cursor，再订阅 live tail；ephemeral ReAct token 只保证原始 handle 的实时传输，不承诺断线重放。

### 4.2 单一 owner

| Concern | 目标 authority | Driver/Adapter 只做什么 |
|---|---|---|
| Run identity / coarse state / parent links | `ExecutionLedger` + `RunKernel` | 使用已分配身份，不生成第二套 root id |
| Route | 注册式 `RunRouter` | Driver 不重判产品意图；只校验 capability |
| LLM iteration | `ReActDriver` | Kernel 不看 token/iteration/retry |
| Graph/checkpoint/lease | `WorkflowDriver` 内现有 Native Runner | Kernel 只看 accepted/waiting/terminal |
| Tool policy/dispatch | `UnifiedToolExecutor` + ToolRegistry V2 | Driver 只能提交 `PreparedExecutionCall` |
| Effect commit/reconcile | generic `EffectJournal` | Driver 不自行判断可重试写是否已完成 |
| Signal/decision | `ExecutionDecisionStore` | UI Future 只可做唤醒优化，不是事实源 |
| Terminal delivery | `ExecutionLedger.finalize_with_event()` | SessionDB/WS/TTS projector 幂等消费 |
| Child ownership | `ChildRunCoordinator` + Ledger link | Scheduler/Team worker 不维护全局无主完成队列 |

### 4.3 Run 身份与幂等键

- `execution_runs.run_id` 是跨层唯一 Run id；Workflow Driver 的 `workflow_runs.run_id` 使用**同一个值**，不是第二个 workflow id。
- `execution_runs.idempotency_key` 是唯一约束，不使用会让 ChildRun 冲突的 `(session_id, request_id, turn_id)` 唯一键。
- root：`root:{session_id}:{request_id}:{turn_id}`。
- Router/Driver 动态委派：`delegate:{parent_run_id}:{command_id}:{profile_key}`。
- Team claim：`team:{team_id}:{task_id}:{claim_epoch}`。
- workflow 内可见 child：`workflow:{parent_run_id}:{node_execution_id}:{logical_slot}`。
- blocking span 不创建 Run row；其 span id 为 `span:{parent_run_id}:{command_id}`。
- 相同 key 的并发/重启请求必须返回已有 `run_id`；相同 key 但 payload/capability fingerprint 不同必须 fail closed 为 `idempotency_conflict`。

### 4.4 分级持久化（H-1 spike 后修订）

同一 `ExecutionLedger` authority 同时包含 bounded active index 与 durable store，Kernel 不另建第二套 run map：

| Run 类型/边界 | 创建时 | 何时必须 promote durable |
|---|---|---|
| 普通 ReAct 短答 | stable id + active ledger record；0 次同步 SQLite 写 | 第一次 decision、ChildRun/delegation、可恢复/写 Effect 或 durable artifact 前 |
| Workflow Driver | Driver start 前与 `workflow_runs` 同事务创建 | 一开始就是 durable |
| ChildRun/Team worker | 调度/claim ack 前 | 一开始就是 durable |
| 已 promotion 的 ReAct | 原 run id 原子插入；不得生成新 id | 后续 decision/effect/coarse event 全部 durable |

普通 ReAct 终态由 active Ledger 做单次 CAS 后投影到既有 SessionDB assistant message；其 token/terminal 不再额外写 execution outbox。进入 durable 边界后的 Run，其 terminal CAS 与 execution outbox 同事务。这样 terminal owner 仍只有 Ledger，但不会让所有普通聊天承担 `synchronous=FULL` 的写延迟。

### 4.5 新 Workflow 逐表 owner 与 Unit of Work

新 Workflow Run 的 `execution_runs` 与 `workflow_runs` 共用 `workflow.db`、共用同一 `run_id`，由 `ExecutionUnitOfWork.start_workflow()` 在**一个连接、一个 `BEGIN IMMEDIATE`** 中原子写入。两层职责如下：

| 表 | 新 Run owner/用途 | 历史 Run |
|---|---|---|
| `execution_runs/run_links` | root/parent、route、coarse state、cancel、唯一 terminal | 无对应 row；compat reader 生成只读 projection |
| `workflow_runs/nodes/checkpoints/pending_writes/leases` | Workflow Driver 私有 scheduler state | 原样恢复 |
| `execution_decisions` | 所有新 Run 的 permission/plan/clarification/HITL | `workflow_decisions` 仅 legacy recovery |
| `execution_effects/attempts` | 所有新 Run 唯一 EffectJournal | `workflow_effects*` 仅 legacy recovery |
| `execution_events/deliveries` | 新 durable Run coarse/public event 与投递 | `workflow_events/deliveries` 仅 legacy recovery |
| trace/evaluation/blob/receipt | 现有通用表，增加 execution run link | 继续读取 |

Workflow checkpoint/lease/node 与 coarse Run cancel/final 的跨表更新必须进入 `ExecutionUnitOfWork`，禁止 WorkflowService 和 Ledger 各开连接顺序提交。旧表选择由“是否存在 `execution_runs` row”一次决定：存在即 new owner；不存在且 manifest 属于历史 registry 才能走 legacy store，不能双写。

### 4.6 新 Run 的逐事件 authority

| 事件 | 唯一生成者 | 新 Run 写入/seq | 唯一投递者 |
|---|---|---|---|
| accepted | Kernel 接收 Driver accepted candidate 后 | durable Run→`execution_events.durable_seq`；ephemeral ReAct 通常无 accepted | Execution projector |
| token/PCM | Driver | handle live buffer，只分配 live cursor | Venue live sink |
| progress | Workflow/Child Driver candidate | `ExecutionUnitOfWork.append_event` 分配 `(run_id, durable_seq)` | Execution projector；现有 workflow progress reducer 只负责 payload/UI reduce |
| waiting/decision | DecisionStore/Kernel | decision 与 waiting event 同事务 | Execution projector |
| tool outcome | UnifiedToolExecutor | durable boundary后写 execution event；纯只读短调用可 live | Execution projector |
| cancel_requested/ack/late | Kernel/Driver candidate | execution event CAS | Execution projector |
| final | 仅 `ExecutionLedger.finalize_with_event` | terminal row + event + deliveries 同事务 | Execution projector |

`execution_events` 分别使用 `UNIQUE(run_id, durable_seq)` 与 `UNIQUE(run_id, event_key)`；`execution_deliveries` 另按 `UNIQUE(event_id, sink_kind, sink_instance, target_id)` 去重。新 Workflow 的旧 progress/accepted/terminal publishers 在 WI-12 切换 commit 中一起改为 candidate sink；legacy run 才继续写 `workflow_events/deliveries`。

每个 public event 在同一事务按目标创建独立 delivery row：SessionDB 使用 `durable_required`；WS session/peer group 使用 `retry_while_bound`（网络物理语义 at-least-once，前端按 event id 去重）；当前 Voice/TTS connection 使用 `best_effort`，断线后不跨重启重放语音，但 SessionDB/history 仍保留文字事实。某一 sink 成功只 CAS 自己的 row，不能 ack 其他 sink。逻辑“恰好一次可见”由 sink 领域表的 stable event id 唯一约束和 reducer 去重实现，不宣称网络 exactly-once。

### 4.7 typed outcome/event

- `OutcomeStatus = succeeded | failed | accepted | waiting | cancel_requested | cancelled | unknown`
- `RunEvent` 至少包含 `event_id/run_id/root_run_id/session_id/kind/status/driver_kind/correlation/error/artifact_refs/created_at`，排序字段分成 `durable_seq: int | None` 与 `live_cursor: {stream_epoch, live_seq} | None`，二者互斥。
- `ToolOutcome` 至少包含 `call_id/effect_id/status/value/error/receipt_ref/artifact_refs/retryable/reconciliation`。
- `accepted` 不允许触发 Run terminal；`unknown` 不允许被 completion gate 当 success；terminal CAS 只允许一次。
- token/PCM delta 使用同一 envelope/correlation，但 `durable=False`、只携带 live cursor，不写 SQLite。

### 4.7.1 双事件排序域

- durable accepted/progress/waiting/tool/final 由 Execution UoW 分配 per-run 单调 `durable_seq`；hydrate/replay/dedupe 只比较 `(run_id, durable_seq/event_id)`。
- 每次 Driver live subscription 创建不可复用的 `stream_epoch`，该连接内 token/PCM 使用从 1 开始的 `live_seq`；live reducer 只比较 `(run_id, stream_epoch, live_seq)`，绝不与 durable seq 比大小。
- `start()` 返回前创建 epoch/buffer，所以首 token 不丢；重连创建新 epoch，不承诺补发旧 token。最终 assistant message/terminal 由 durable event或 SessionDB projection 收敛，旧 epoch 的迟到 delta 在 epoch 切换后丢弃。
- 前端 `ws.ts` 拆成 `reduceDurableRunEvent` 与 `reduceLiveStreamEvent`；WorkflowProgress card 只吃 durable domain，assistant token bubble/Voice PCM 只吃 live domain。binary PCM 仍依赖 WS 帧序，控制 metadata 带相同 live cursor。

### 4.8 Decision 与一次性授权

- `DecisionKind = permission | plan | clarification | ppt_outline | skill_candidate | workflow_hitl`；每种 kind 有版本化 prompt/response schema 与 domain ref。
- `permission` decision 在创建时冻结 `run_id/call_id/effect_id/tool_name/args_hash/capability_hash/scope_hash`。`signal(allow)` 在同一事务 CAS resolve decision 并签发 `DecisionAuthorization`/`execution_grants`；deny 只 resolve，不产生 grant。
- `UnifiedToolExecutor.prepare_effect()` 在同一 Unit of Work 校验并一次性消费 grant，然后才把 effect 从 prepared 转 running。wrong run/call/effect/capability/scope、expired、duplicate consume 全部 fail closed；重启加载的是同一 durable grant，不根据 UI payload 重建授权。
- plan/clarification/PPT/skill/workflow decision 不产生工具 grant，只把 typed response 路由给对应 continuation/domain adapter。

### 4.9 回炉后的 authority 与原子契约（唯一生效）

| Concern | 唯一 authority | 禁止的第二 owner |
|---|---|---|
| Ephemeral Run/live stream | 薄 Kernel 的有界 active index；terminal/close 立即释放 | `ExecutionLedger` active/event cache |
| Durable Run/Decision/Boundary/Child | 现有 `SqliteExecutionUnitOfWork` 同一连接事务 | DecisionStore/ContinuationStore 各开 SQLite |
| Workflow checkpoint/lease | 现有 Native WorkflowRunner/CheckpointStore | Kernel 重做 graph scheduler |
| Tool call/outcome | ToolRegistry V2 + `PreparedToolCall` + `NormalizedToolOutcome` | `PreparedExecutionCall`/第二个 ToolOutcome |
| Effect/late reconcile | 现有 workflow effect primitive，由 UoW 链接 Run/Boundary | 新 EffectJournal/LateEffectSupervisor |
| Durable delivery | `execution_deliveries` 行由 UoW 写入；复用现有 workflow outbox 的 claim/retry/sink handler 算法 | 新 ExecutionProjector/DeliveryWorker outbox，或新路径调用会自行开连接的旧 facade |
| Product prompt | ProductTurnPreparer | Kernel/Driver 内产品特判 |
| Product projection | RunPresenter | main/Voice/Driver 各自重解释结果 |

新请求的**控制面事实**只写 `execution_runs/execution_decisions/execution_grants/execution_continuations/execution_child_commands/execution_effects/execution_effect_links/execution_events/execution_deliveries`。新 Workflow 的 graph/checkpoint/lease 内部状态仍写 Native `workflow_runs/checkpoint` 表，但不得再写 legacy decision/effect/event/delivery owner 表。历史 workflow run 继续走旧 compatibility 数据；两类 run 以持久化 `owner_kind + owner_generation` 判定 owner，不靠进程内 flag 猜测。

“复用现有 EffectJournal/outbox”只表示复用 fingerprint、claim、retry、reconcile 与 sink handler 算法，不表示复用会自行 `_connect()/commit()` 的 facade。R1 必须把它们抽成显式接收同一 connection 的 `*_tx(connection, ...)` 原语；新 run 只允许 `SqliteExecutionUnitOfWork` 调用这些原语，禁止任意新路径在事务中再次开连接。

必须提供八个原子 UoW 方法：

1. `resolve_decision_and_advance_boundary(...)`：decision CAS、一次性 grant（如适用）、boundary state 和 waiting/resumed event 同事务；commit 后的内存 wakeup 只是优化。
2. `settle_effect_and_advance_boundary(...)`：effect outcome link、tool response、boundary next state 同事务；外部动作成功但进程崩溃时由既有 stable effect/reconcile 读取，不重新执行。
3. `finalize_and_enqueue_delivery(...)`：唯一 terminal CAS、Goal link 和 durable delivery 同事务；ReAct live token 不进入该 outbox。
4. `apply_child_signal_and_ack(...)`：parent boundary 应用 accepted/terminal signal 与 inbox ack 同事务；重复投递返回已应用结果，不能因 pending delegate 已清除而失败。
5. `consume_grant_and_claim_effect(...)`：一次性 grant CAS、effect claim/lease/fence 与 attempt start 同事务；grant 已消费或 lease epoch 过期都 fail closed。
6. `promote_and_persist_batch_boundary(...)`：ephemeral ReAct 首次遇到 ToolBatch/Decision/ChildRun 时，在同一事务创建 durable run、完整 batch/continuation boundary、首个 pending command 与 waiting event；崩溃后要么全部不存在，要么 recover 能看到完整 batch，禁止先 promote 再补 command。
7. `create_child_link_and_enqueue_command(...)`：child run、parent/root/session link 与 stable schedule command 同事务；不能留下无 command 的 child。
8. `finalize_child_and_enqueue_parent_signal(...)`：child terminal CAS、terminal event 与 parent inbox signal 同事务；后续由方法 4 原子应用并 ack。

外部写 Effect 固定为 `prepare/claim（事务）→ 外部执行（事务外）→ settle（事务）`。在外部动作已经可能发生、但 receipt 尚未落库的窗口，状态只能是 `unknown` 并交给可观察 target/receipt reconciler；禁止假装回滚，也禁止盲目自动重试。

`observe()` 对 ephemeral run 只读当前 stream epoch 的有界 live tail；对 durable run 查询 UoW/outbox cursor。重启后不得创建空 subscriber 永久等待。`signal()` 先执行上述原子 UoW 方法，再通知 active Driver；若进程在 commit 后崩溃，recover 从已推进 boundary 继续而不是重复 resolve decision。`recover()`/reconciler 除进程内 `_active` 外还必须持有数据库 lease 与递增 fence epoch，旧进程或过期 worker 的写入全部 CAS 失败。

唯一 `ProfileRegistry` 存放 immutable `ProfileSpec(profile_key, driver_kind, capabilities, workflow_version, request_factory, driver_factory)`。Router 读取全量投影，WorkflowDriver 读取 `driver_kind=workflow` 子集；bootstrap 断言 `router_workflow_keys == workflow_driver_keys`，任何 profile 缺失都使对应能力 degraded，不能等到首个用户请求才报错。

## 5. 解剖麻雀：S-2 复杂 Code 请求

> 下方 V1 文本链路中的 `ExecutionLedger`、`UnifiedToolExecutor`、新 EffectJournal/Projector 已作废。R0 将该场景重写为以下唯一链路，并保持旧产品准备行为：

```text
WS/Voice Adapter
  -> ProductTurnPreparer（history/persona/memory/skill/attachment/pipeline/plan）
  -> RunKernel.start（可信 HostContext + route_once）
  -> WorkflowDriver -> WorkflowLauncher.launch_precreated(code_complex/v1)
  -> SqliteExecutionUnitOfWork + Native checkpoint/effect/outbox
  -> ToolRegistry V2（PreparedToolCall + NormalizedToolOutcome）
  -> RunPresenter（accepted/progress/decision/artifact/final）
  -> SessionDB / WS / TTS
```

同一模式用于 DeepResearch/PPT；ReAct 的不同点只在 Driver 内部是动态 LLM loop，工具、ChildRun、事件和终态仍使用相同 UoW 与 ToolRegistry 底座。不存在第二个 Ledger、ToolExecutor、Projector 或 delivery worker。

## 6. 生效文件影响清单

| 范围 | R1～R7 的唯一目标 |
|---|---|
| `backend/deskpet/execution/{contracts,ports}.py` | 收敛为最小共享 identity/event/signal/UoW 契约 |
| `backend/deskpet/workflows/store/{schema,execution_uow}.py` | 新 run 表与唯一事务 writer；增加 `*_tx(connection, ...)`、lease/fence、owner generation |
| `backend/deskpet/workflows/{effects,outbox,launcher,runner}.py` | 保留算法、Native graph/checkpoint 与 legacy compatibility；新 run 不调用自开连接 facade |
| `backend/deskpet/harness/{kernel,profiles,drivers/*,children}.py` | 薄 Kernel、同源 ProfileRegistry、两个 Driver 与最小 Child saga；删除重复 store/executor/projector |
| `backend/deskpet/agent/{turn_preparer,run_presenter}.py` | 唯一新增的产品组合层，分别冻结输入准备与结果投影语义 |
| `backend/{main.py,agent/agent_loop.py,pipeline/voice_pipeline.py}` | 先无行为抽取，R6 一次切 owner 并删除旧编排 |
| `backend/deskpet/tools/registry.py` 与 Team/Subagent tools | 复用 PreparedToolCall/outcome、可信 context、统一 ChildRun scope |
| frontend/config/ARCHITECTURE | typed outcome、默认 ON、health 与最终事实源 |

删文件、改名和重构不违反 R1～R5 的 “only-add” 安全语义；它的准确含义是：**R6 前不得改变生产 owner、生产调用顺序或用户可见能力，不得生产双写/双跑**。WI-1～WI-11 未接管生产的重复 seam 可以在 R1 删除。任何搬家后的 orchestration 文件仍由自动发现 benchmark 计数，移动代码不能降低 LOC。

## 7. 工作项（按依赖顺序）

### 7.0 回炉执行序列（唯一生效；原 WI-12～WI-14 作废）

已提交的 WI-1～WI-11 只视为可回退的研究/安全 seam，不代表目标结构。后续按 R0～R7 执行；每个 R 项一个独立 commit，R6 是唯一生产 owner activation commit。

#### R0 — 冻结产品能力 parity 与真实回退基线 〔AC-3, AC-12, AC-15, AC-18〕

- 在 `backend/tests/harness_simplification/test_product_turn_parity.py` 建立 §2.4 全部能力的 fixture/inventory；测试直接观测 canonical messages、toolset、route decision、WS frame、SessionDB 写入和 domain side effect，不能只做源码字符串断言。
- parity inventory 每一行固定记录：`capability`、旧 production callsite、启用 flag/前置条件、输入字段、输出 frame、DB/vector/file/外部 side effect、调用顺序、错误/取消语义、新 owner、自动测试、真人用例、状态。任一列未知或 `PARTIAL` 都不能进入 R6。
- `scripts/acceptance/harness_parity_census.py` 从旧生产 `_run_chat`/Voice/AutoResume/Workflow publisher 的 event type、WS send、SessionDB/vector/file sink、waiter/cancel/codify/permission restore 调用点生成 JSON census；baseline 每项锁定 `callsite/source_hash/kind/count`。每个旧 span/event/sink 必须映射到新 owner 与 golden testcase，`unmapped_count == 0`；动态调用无法分类时 fail closed。至少显式覆盖 text user-echo、plan/cancel/error、tool start+result 双事件、auto-resume、Voice session/peer remap、system fragment、Context OS、permission restore、持久化与 codify。
- 把旧 `_run_chat` 的输入参数全部列入 typed `TurnInput`：text、session/request/turn、venue/mode、memory policy、explicit-new、attachment blocks、provider/capability/workspace refs；测试禁止 adapter 接收后静默丢弃字段。
- 为 `AssistantMessageEvent`、reasoning、`ContextCompactedEvent`、`PipelineEvent`、tool result、accepted/progress/final 建立完整 presenter golden。
- 修正 benchmark：10k completed run 必须真实 start/final/close Kernel/UoW，不能用“创建 list 后 clear”代替。R0 固化 `loc-phase0-manifest.json` 与 `loc-rollback-manifest.json` 两份逐文件 path/LOC/hash/group 清单，先分别断言总数 21,563 与 33,228；不一致立即 FAIL。runner 再验证执行 worktree 的 `4d38979e` 是 BASE 且可达，使用 rename-aware `BASE..HEAD` diff，并 union baseline manifest、当前 tracked、dirty 与 untracked Python 文件。
- 计数机器规则：baseline manifest 文件永远计入；本计划 commit 中新增或修改的 `backend/**/*.py` 默认计入，只有 `backend/tests/**`、生成的 `*_pb2.py` 和 vendored 目录可按固定 path rule 排除；未知分类直接 FAIL。输出逐文件 `path/baseline_loc/current_loc/group/reason/status` JSON；改名/搬家用 blob/hash 追踪，任何排除项必须命中固定规则，禁止人工 `reason` 放行。
- 证据：回退点 `4d38979e` worktree clean；harness suite 150 passed/9 xfailed；诚实中间 LOC 33,228（原 30,248 漏算 `execution_uow.py` 2,980 行）。该数字只是基础 seam 基线，最终门槛仍是 phase-0 的 17,250。

#### R1 — 合并为一个契约集与一个 UoW 〔AC-4～AC-10, AC-13, AC-14, AC-16〕

- 先做纯 additive v6→v7 migration：增加 `execution_runtime_state`、run `owner_kind/owner_generation`、`execution_legacy_drain_items` 与必要 fence 约束；默认 singleton 为 `phase='legacy', generation=0`，因此不改变任何生产 owner。R1～R5 的全部测试都运行在 v7 production-shaped schema 上。
- R1～R5 的 test-only 新路径使用独立临时数据库，并调用与生产完全相同的 drain/activate CAS（空 legacy manifest）进入 `activated/open, generation=1` 后才能创建 `owner_kind='kernel'` row；legacy compatibility fixture 保持 `legacy/0`。禁止 `test_mode`、绕过 flag 或在 `legacy` phase 偷写 kernel owner row。
- `backend/deskpet/execution/contracts.py`：只保留 Run/Decision/Event/Child identity 与 host-trusted context；删除重复 ToolOutcome、无生产消费者 codec、细碎 store-specific record。
- `backend/deskpet/execution/ports.py`：合并为一个窄 `ExecutionUnitOfWork` protocol，禁止 Event/Delivery/Decision 多个切片协议制造伪 owner。
- 依赖方向固定为：`execution` 只含中立 identity/event/signal/UoW contract，永不 import `workflows`；Driver 与 ToolRegistry 可直接使用既有 `workflows.effects.PreparedToolCall/NormalizedToolOutcome`，UoW 方法只接最小 call/effect identity 与 JSON payload，不把 workflow 类型塞回 execution contract。删除当前 `tools.registry → harness.tool_executor` 反向依赖，禁止用 `TYPE_CHECKING` 掩盖循环。
- 删除 `execution/ledger.py`；ephemeral identity/live subscription 由 Kernel 的有界 active index 管理，跨 durable boundary 后一切 authority 在 UoW。
- 删除独立 `harness/decisions.py`、`effects.py`、`continuations.py` 的 SQLite owner；把 §4.9 八个原子操作作为同连接事务写入 `backend/deskpet/workflows/store/execution_uow.py`。
- 工具契约直接使用 `backend/deskpet/workflows/effects.py` 的 `PreparedToolCall`/`NormalizedToolOutcome` 和 late-reconcile 算法；把 effect/outbox 中自开连接的逻辑拆成 UoW 可调用的 `*_tx(connection, ...)`，`ToolRegistry` 只增加可信 `ToolExecutionContext` adapter，不增加第二套执行器。
- 新 run 明确只写 §4.9 所列 `execution_*` 表；legacy run 继续使用旧 workflow store facade。测试扫描生产新路径，出现 EffectJournal/Outbox `_connect()` 或独立 `commit()` 即失败。
- 故障注入覆盖 promotion→batch boundary→first claim、decision CAS→boundary、grant consume→effect claim/start、effect settle→continuation、child create/link→schedule lease、child terminal→parent inbox→apply/ack、TeamStore→Child command saga、terminal→delivery 每个写点；事务内中断整体回滚，跨库 Team saga 用 stable operation id/outbox 对账，外部执行窗口只能落 `unknown/reconcile`，不能宣称回滚或盲重试。
- `backend/tests/harness_simplification/fault_matrix.json` 每行固定 `window_id/injection_hook/durable_before/durable_after/restart_actor/idempotency_key/expected_counts`；八个 UoW 方法和 Team saga 在代码中导出稳定 `FAULT_HOOKS`，测试对每个 hook 执行 kill/restart，并核对 run/boundary/decision/effect/attempt/child/link/inbox/event/delivery/external-write 计数。门禁断言 `tested_window_ids == required_window_ids == exported_fault_hooks`，多一个或少一个都 FAIL。
- R1 只要求 LOC 自动快照单调下降且不新增等价 owner；combined core/UoW ≤2,800 的硬门在 R2～R4 完成 Preparer/Presenter、ToolExecutor/Projector 迁移和 Kernel/Driver 收敛后，于 R5 进入 R6 前执行。禁止为了让 R1 当场达 2,800 而提前切生产 owner或删除尚未 parity 的产品行为。
- `start()` 接受仅由宿主构造的 `precreated_run_ref`/start source；Child scheduler 仍调用公开 start 语义，不保留 `_accept_child` 隐藏第七入口。模型 payload 不能设置该字段。

#### R2 — 无行为抽取 ProductTurnPreparer 与 RunPresenter 〔AC-1, AC-9～AC-12, AC-15, AC-18〕

- 从 `backend/main.py::_run_chat` 抽取 `backend/deskpet/agent/turn_preparer.py`，但旧生产入口仍调用它；按 §2.4 顺序复用现有 ContextAssembler、pipeline、plan/preference、summary/supervisor、provider/capability 组件。
- 从旧 AgentEvent bridge 抽取 `backend/deskpet/agent/run_presenter.py`，旧生产入口仍调用它；保留所有 WS/SessionDB/vector/billing/SessionActivity/codify 副作用与错误语义。
- 两个类只能组合调用现有产品组件，不得把约 2,263 行 `_run_chat` 原样搬成新 god object；preparer 按 prepare-context/route-intent/plan-decision 三个纯阶段返回 typed result，presenter 按 live/durable/domain 三类 handler 注册。
- 抽取 commit 不改变路由、AgentLoop、Workflow 或 Voice owner。旧/new helper 双跑只允许在 test fixture 内，生产不 shadow、不双写。
- parity golden 必须 100% 一致；差异只能是修复了 acceptance 已明确要求的错误（例如失败不再硬编码 `ok=true`），并需单独测试记录。

#### R3 — 收敛薄 Kernel 与 ReAct Driver 〔AC-1～AC-10, AC-13～AC-16〕

- `harness/kernel.py` 压到 ≤450 LOC：六操作、一次 route、一个有界 live index；durable observe/recover 只查 UoW；terminal/close 删除 subscriber/task 引用。
- durable `recover()` 必须先 CAS 获取数据库 lease/fence epoch；心跳续租、过期接管和所有恢复写入都校验 epoch。`_active` 只防本进程重复启动，不能当跨进程锁。
- `harness/ports.py` 只保留一个 tagged `DriverEvent` 与一个 `DriverSignal`，删除 emission/candidate/event 三套 taxonomy。
- `drivers/react.py` 复用 AgentLoop 的 provider/token/completion 核心；ToolBatch 携带完整 canonical messages，resume 必须消费 tool/decision/child response 并从已提交 boundary 继续，禁止重跑原 prompt。
- capability subset 必须落实到实际 schema/tool filter，而非只进入 hash；DelegateRun 的 Code/DR/PPT payload 由对应 profile builder 补齐并有 production contract test。
- `AgentLoop` 删除 internal dispatch/全局 subagent drain/产品 handoff 后压到 ≤3,800 LOC；现有完成门禁、compaction、skill remount、evaluator/pipeline 行为由 R2 parity 兜底。
- completion/Verify 使用显式 `EvidenceContext(run_id, turn_id, call_id, effect_id, target_digest, artifact_ref)`；只查询当前 context 的 receipt/artifact，查不到返回 `unknown`，禁止旧 session 同名工具结果让新请求通过。R5 增加同名跨 session/跨 turn 反例。

#### R4 — 收敛 Workflow/Child/Delivery adapters 〔AC-3, AC-5, AC-6, AC-8～AC-10, AC-14, AC-16〕

- Workflow adapter 直接复用 `route_task`、`WorkflowLauncher.launch_precreated`、Native checkpoint/effect/outbox；不再新增 router/profile/projector/delivery worker 第二套框架。
- Child coordinator 只保留 `submit → schedule/ack → deliver terminal` 三动作和稳定 operation id；Team 通过 outbox saga 接入，Subagent 不再用全局 queue。
- `apply_child_signal_and_ack` 必须原子；startup/periodic reconciler 扫 pending、过期 leased、scheduled-without-ack command，并用 stable operation id 重放 start。
- durable Event/Delivery 继续使用现有 UoW/outbox handler；ReAct live token 只走 presenter，不写 durable token event。
- Router profile key 与 WorkflowDriver profile catalog 必须由同一个 immutable `ProfileRegistry` 生成；bootstrap 断言 `router_workflow_keys == workflow_driver_keys`，而 ReAct key 是全量 registry 中的另一合法子集，不参与该相等式。当前 `workflow.deep_research|ppt_pro|code_complex` 与 `research.deep.v7|ppt.create.v1|code.execute.v1` 的错配作为 R4 回归 fixture 固定，禁止任何别名漂移。
- durable delivery 的唯一 owner 命名为 `ExecutionDeliveryDispatcher`：复用现有 outbox loop 算法，但只能经 UoW `claim_delivery/complete_delivery` 并校验 run owner generation；legacy dispatcher 只处理不存在 execution row 的历史 run。RunPresenter 只是 sink adapter。effect unknown 的唯一 reconciler 归 Workflow/effect service，Kernel 与 Driver 只提交/观察状态，不能各起 supervisor。
- 历史 v1-v7 run 只由 compatibility reader 恢复；launcher/outbox/recovery 在同一入口先按 `execution_runs` 是否存在选 owner，一旦存在就禁止降级旧 start/recovery/delivery。

#### R5 — Text/Voice test-only 全链路就绪与 parity 门禁 〔AC-1～AC-18〕

- Text adapter 顺序固定为 `Venue decode → ProductTurnPreparer → Kernel → Driver → RunPresenter`；旧 `_run_chat` 仍是唯一生产入口。
- Voice 只保留 transport，但必须复用同一 preparer/kernel/presenter；ASR/TTS/VAD/barge-in fixture 与文字并发隔离全绿。
- Voice parity 额外覆盖 `StreamingTagParser`、emotion/action→Live2D、terminal-only TTS、transcript sync、provider indicator、lip-sync；不得把原始标签直接朗读。
- Presenter parity 必须覆盖 delta/intermediate、tool 参数/结果脱敏、SessionDB/vector、peer fanout、usage/billing/feedback/activity、workflow delivery、codify 与 error；每个生产 adapter 在 terminal/cancel/disconnect 后调用 `close` 并证明 active refs 归零。
- Workflow durable delivery 只由既有 outbox handler 投递；RunPresenter 只消费并翻译同一 delivery，不再创建 row。故障注入断言一个 terminal event 对每个 sink 只有一次领域可见交付。
- 运行 §2.4 parity inventory、S-1～S-7 自动化、kill/restart/cross-session/fault injection；任何功能 PARTIAL 都不允许进入 R6。
- G1～G4 必须全绿，core+migrated adapter 实测 LOC 在切换前已经满足预算；不能指望 R6 临时大删才达标。

#### R6 — 全 venue + recovery 原子 owner 切换 〔AC-1～AC-18〕

- R1 已经 additive 建好 `execution_runtime_state(generation, phase, drain_manifest_hash, drain_count, activated_at, updated_at)` 与 `execution_legacy_drain_items`；R6 不再首次迁移 schema。deployment state machine 固定为：`stop ingress → 等待 ephemeral legacy active=0 → 单事务登记全部 durable legacy active run + manifest hash/count，并 CAS legacy→draining → 重扫验证无未知 legacy owner → 排空持久 manifest → CAS draining→activated 并递增 generation → 切 bootstrap/recovery/delivery owner → CAS activated→open → open ingress`。如果在登记事务前崩溃，phase 仍是 legacy；事务后崩溃，完整 manifest 可恢复。任一步失败保持 ingress 关闭并报告 degraded，不能两套 owner 同时接单。
- `activation generation` 是部署级永久 fence，写入每个 run；`lease_epoch` 是单 run/command 的短期接管 fence，两者禁止复用字段。start/recover/delivery 同时校验 runtime generation 与 row generation，reconciler 再校验自身 lease epoch。
- 启动恢复协议：读到 `legacy` 才允许旧 runtime；读到 `draining` 就保持入口关闭并继续排空/由运维重试 activation；读到 `activated/open` 必须只启动新 owner，其中 `activated` 先完成 bootstrap/recovery 后再 CAS `open`。只要该 generation 已有 `execution_runs` row，就禁止退回 `legacy`。
- 一个 activation commit 同时切 Text/Code/DeepResearch/PPT/Voice start、decision、cancel、recovery、terminal delivery；默认 ON，不保留 runtime flag、shadow 或语义较弱 fallback。每个新 `execution_runs` row 持久化 `owner_kind='kernel'` 与单调递增 `owner_generation`，后续 start/recover/deliver 都按 row 校验。
- 同 commit 删除 `_run_chat` 旧编排体、AutoResume redispatch、旧 waiter/task maps、全局 Subagent queue、Voice AgentLoop bridge、AgentLoop internal dispatch、Registry legacy 新请求入口和旧 PPT background owner。
- `main.py` 只保留 transport/bootstrap，≤6,800 LOC；Voice ≤500；Registry ≤1,900；全部计数文件总 LOC ≤17,250，owner ≤7，新增等价 owner=0。
- promotion→boundary、accepted→checkpoint、decision CAS→boundary、grant→effect claim、effect external commit→outcome、child create→schedule/ack、child terminal→parent signal/ack、terminal→delivery 等全部 crash window 在 production wiring 下逐一 kill/restart。
- activation 的 `legacy→draining`、drain record、`draining→activated`、owner bootstrap、`activated→open` 每个写点都用副本库 kill/restart；断言入口要么关闭，要么只有一个 generation 的 owner 可写。
- activation 之前任一 gate 失败可整体回退到 R5。一旦生产已创建首条 `execution_runs` row，旧 runtime 不具备该 row 的安全恢复能力，禁止代码回退；此后只能 fail-closed 并 roll-forward 修复。切换前必须用副本数据库演练升级与恢复，启动 health 明确显示 irreversible activation 状态。

#### R7 — Health、架构事实源、全量测试与真人 E2E 〔AC-1～AC-18〕

- `/health` manifest 从真实 Driver/profile/event 注册导出；初始化失败显式 degraded，不能 broad-exception 后落回旧路径。
- 按 phase-4/phase-5 执行自动化、前端、Rust、性能和 S-1～S-7 Windows 真人点击矩阵；维护 testcase/results。
- 全绿后同次交付更新 `ARCHITECTURE/AGENT_HARNESS.md`、`ARCHITECTURE/ARCHITECTURE.md`、`ARCHITECTURE/AgentLoop.md`、`ARCHITECTURE/index.md`、`ARCHITECTURE/PROJECT_STATUS.md` 日期、生产链路与证据。

### 7.0.1 回炉 LOC 预算

| 计数组 | R0 基线 | R6 上限 |
|---|---:|---:|
| `backend/main.py` | 11,538 | 6,800 |
| `backend/agent/agent_loop.py` | 5,662 | 3,800 |
| `backend/pipeline/voice_pipeline.py` | 1,215 | 500 |
| `backend/deskpet/tools/registry.py` | 2,591 | 1,900 |
| 旧 auto-resume/manifest/subagent/spawn/routing | 1,260 | 220（仅必要 compatibility） |
| 精简后的 `execution/` + `harness/` core + 完整 `execution_uow.py` | 10,962 | 2,800 |
| ProductTurnPreparer + RunPresenter + 迁移 adapters | 0 | 1,000 |
| **总计** | **33,228** | **≤17,020**（低于硬门槛 17,250） |

预算以固定 SHA + 自动 diff/内容归属规则执行；新增、改名或搬迁的 orchestration 文件必须自动计入，不允许人工白名单漏计。combined core/UoW 的 2,800 行上限包含当前 2,980 行 `execution_uow.py`，因此 R1 必须真实删除重复 façade/record/method，而不是把它排除出指标。

### WI-0 — 冻结反例与复杂度测量 〔AC-7, AC-9, AC-16, AC-17〕

- 新增：`backend/tests/harness_simplification/test_current_failures.py`、`scripts/acceptance/harness_owner_audit.py`、`scripts/bench/harness_baseline.py`。
- 把 phase-0 已确认问题写成失败契约：mixed safe/unsafe 顺序、两个 unsafe 不并发、model reserved override、Voice/main `ok:false`、跨 session subagent、Code venue Research/PPT sink、`/stop` 未取消 workflow。
- owner audit 固定识别 15 个基线容器，并扫描新增同义 `dict/set/queue/task registry`；输出 JSON（baseline=15、survivors、new_equivalents）。
- baseline 脚本记录固定 orchestration 文件集 LOC、chat Kernel 增量、TTFT、event-loop lag、workflow node 事务/序列化字节、10k Run RSS/强引用。
- 验证：反例测试在旧实现下按预期 red；measurement 脚本可重复两次且差异在噪声阈值内。
- 依赖：无。禁止在此 WI 改业务行为。

### WI-1 — 建立 generic Execution Ledger schema 与 immutable contracts 〔AC-1, AC-4, AC-5, AC-6, AC-9, AC-13, AC-14〕

- 在 `workflows/store/schema.py` 增加 v4→v5 原子迁移与 fresh-db 等价 schema：
  - `execution_runs`：RunContext、`idempotency_key UNIQUE`、payload/capability fingerprint、driver/profile、persistence level、coarse status、version、terminal_event_id、timestamps。
  - `execution_run_links`：root/parent/child、attachment policy、domain link（workflow/goal/team task）。
  - `execution_decisions`：decision id/nonce/status/prompt/response/version/expiry。
  - `execution_grants`：仅 permission allow 产生；绑定 decision/run/call/effect/capability_hash/scope_hash、expiry、single-use status/version。
  - `execution_effects`、`execution_effect_attempts`：stable fingerprint、unknown/late state、receipt/artifact refs。
  - `execution_effect_links`：`run_id/node_execution_id/effect_id/checkpoint_ns/checkpoint_id`，把 generic effect 与 Native node/checkpoint 关联；同 effect/link 幂等唯一。
  - `execution_events`：分别约束 `UNIQUE(run_id, durable_seq)`、`UNIQUE(run_id, event_key)`。
  - `execution_deliveries`：一行只代表一个 sink，含 `event_id/sink_kind/sink_instance/target_id/policy/status/attempts/version/next_attempt/error/delivered_at`，约束 `UNIQUE(event_id, sink_kind, sink_instance, target_id)` 并独立 CAS。
  - `execution_continuations`：ReAct 可恢复 command boundary 的 schema/version、canonical provider-neutral messages through pending tool call、Session projection cursor、PreparedContext/tool-set snapshot refs、pending prepared call/decision、iteration/provider state。附件/大内容只存 immutable blob/artifact ref，不把 body 复制进 row。
  - `execution_child_commands`：所有 durable ChildRun 的 stable operation id、parent/child/profile/join policy、capability snapshot ref、status、schedule lease owner/epoch、attempts/next_attempt/error/ack_at；`UNIQUE(operation_id)`，与 child run/link 在同一 UoW 写入。
- 新增 `workflows/store/execution_uow.py::SqliteExecutionUnitOfWork` 实现 `execution.ports.ExecutionUnitOfWork`：在同一个 workflow.db connection/transaction 内创建同 ID 的 `execution_runs + workflow_runs`、提交 workflow coarse cancel/final/outbox；不得调用两个各自 commit 的 store facade。依赖方向固定为 Harness/Drivers→execution Protocol，bootstrap→SQLite implementation；`deskpet.execution` 不 import `deskpet.workflows`，避免循环依赖。
- `execution/contracts.py` 使用 frozen dataclass/enum；所有反序列化 fail closed；payload schema version 显式。
- `ExecutionLedger.create/promote/finalize/link/query/authorize` 使用 CAS；root/delegate/team/workflow idempotency key 按 §4.3 生成并校验 fingerprint；terminal state 不可回退，durable terminal event 与 outbox 同事务。
- 历史 `workflow_*` row、checkpoint、manifest 不改写；compat reader 在内存生成 `LegacyRunProjection`，不回填 execution row。
- 测试：fresh/v4 migration、中断重跑、各类 key 并发 create 与 conflict、ephemeral→durable 同 id promotion、同 ID workflow 原子 start、terminal race、parent cycle、foreign-session actor query、历史 v1-v7 fixture byte-for-byte 不变。
- 依赖：WI-0。

### WI-2 — 可信 RunContext 与单一工具执行边界 〔AC-4, AC-7, AC-8, AC-9〕

- `HostContextFactory` 从 WS/Voice/background 的已认证 session、workspace、capability resolver、provider resolver 创建 `RunContext`；用户/模型 `RunRequest` 不含保留字段。
- 固定新 handler port 为 `ToolHandler(model_args: Mapping[str, JsonValue], context: ToolExecutionContext) -> ToolHandlerResult`；`ToolExecutionContext` 只读携带 session/workspace/write scope/request/turn/run/call/effect/trace/capability，MCP/plugin bridge 默认只收到 model args，只有显式声明 `needs_host_context` 的可信本地 adapter 才收到受限 context view。
- 迁移 inventory 中当前命中的 17 个文件，至少包括 `file_tools.py`、`image_tools.py`、`ppt_tools.py`、`research_tools.py`、`os_tools/{read,write,edit_file,run_shell}.py`、`code_tools/{glob,grep,clarify,agent,agent_parallel,spawn_subagents,spawn_team,todo_write}.py` 和 `registry.py`；用 AST 测试禁止 handler 再读取 `_session_id/_write_scope_root/_project_root/_request_id/_turn_id`。
- `registry.py` 新增不同名入口 `execute_call(call: PreparedExecutionCall, context: ToolExecutionContext)`，避免与现有 `execute_prepared(prepared, *, effect_id, authorization)` 异签名冲突。先让新入口与 handler adapter 在 test-only wiring 下可用；**此 WI 不删除或断开生产旧 merge**，完整 owner 翻转与删除只发生在 WI-12 全入口/Workflow/recovery 原子切换 commit。
- 历史 workflow recovery 不保留不可信 merge：WI-6 同时提供 `LegacyPreparedCallAdapter`，从持久 workflow session refs/capability snapshot/authorized prepared call 重建 `ToolExecutionContext` 并剥离 args 中保留字段；历史模型 args 本身永远不能成为 trusted context。
- `UnifiedToolExecutor.execute_batch()` 按原始调用序列扫描：连续 `concurrency_safe=true` 段使用 TaskGroup 并发；遇 unsafe 先等待前段，再单独执行 unsafe，随后继续。不得直接复用当前“全部 safe 后全部 unsafe”的 `partition_dispatch()`。
- 写/可恢复工具在执行前调用 generic EffectJournal；只读且明确不会 replay 的短调用允许 light receipt。复用并泛化现有 `workflows/adapters/tool_executor.py::execute_sync_handler()` 的 `shield(future)+mark_uncertain+finalize_late`，由一个 `LateEffectSupervisor` 保存活跃 future/finalizer；正常 shutdown 等待上限后持久化 unknown，进程重启由 tool-specific `EffectReconciler`（文件 hash/目标存在性/外部 receipt）收敛。无 reconciler 的 unknown 保持显式未知并禁止自动重试。
- permission-required call 只接受 §4.8 的 `DecisionAuthorization`；EffectJournal `prepare/consume_grant/start_attempt` 共享 UoW，保证 grant 一次性绑定当前 effect/scope。现有 `PreparedToolAuthorization` 兼容对象由 adapter 转换，不能让 Driver 自行拼 grant。
- `ToolOutcome` 贯穿 AgentLoop/Workflow/receipt/artifact，不再把 JSON envelope 当字符串成功。
- 同步更新 `receipt.py/receipt_store.py`：新 receipt 绑定 run/turn/call/effect/target/artifact；旧 JSON/JSONL 只读兼容。
- 测试：恶意 reserved override、17 文件 context inventory 清零、MCP/plugin context 最小披露、wrong/expired grant、错 call/effect/scope、duplicate consume、grant 后重启、contiguous batch 时间线、两个 unsafe barrier、safe 异常聚合、timeout 后真实线程晚写、shutdown/restart reconcile、cancel+retry、同 effect 并发、receipt/artifact 关联。
- 依赖：WI-1。此 WI 先完成安全语义，后续才允许 recover/AutoResume 切换。

### WI-3 — 统一 RunEvent 与投影器 〔AC-9, AC-12, AC-14, AC-18〕

- `projector.py` 只消费 `RunEvent/ToolOutcome`，分别投影 SessionDB message/activity、WS envelope、TTS cue；不重新推断 `ok`。
- durable coarse event（accepted/waiting/cancel/terminal/tool outcome）按 §4.6 写 execution outbox；ephemeral ReAct 的 token/terminal 走 start 返回前已建立的 RunHandle live buffer/SessionDB 投影，SQLite 增量仍为 0。
- Workflow progress 的现有 reducer payload 保持兼容；新 run 的 accepted/progress/waiting/final 全部改投 execution event sink，旧 workflow outbox 只服务 absence-of-execution-row 的 legacy run。
- 前端 `ws.ts`/stores/components 共享 status 语义，但使用 §4.7.1 的 durable/live 两个 reducer：`failed/unknown` 非绿色，`accepted` 非完成；各自排序域内重复/out-of-order 被忽略，跨域不比较。
- delivery dispatcher 按 `(event,sink)` 独立 claim/CAS；SessionDB message/activity 增加 stable event id 唯一约束或现有 upsert key，WS/前端按 event id 去重，TTS best-effort 不阻塞 required sink terminal ack。
- 测试：同一 fixture 经过 Registry→Driver→Projector→SessionDB/WS/Voice 结论完全一致；durable seq 与多个 live epoch 交错、hydrate+live merge、旧 epoch迟到；SessionDB/WS/TTS 三 sink 的全部部分失败顺序、进程重启、重复消费，required terminal 恰好一次可见且失败 sink 可独立重试。
- 依赖：WI-1、WI-2。

### WI-4 — 单次注册式路由决策 〔AC-2, AC-3, AC-11, AC-15〕

- `router.py` 接收 `RunRequest + trusted context + capability health`，只返回一份持久 `RouteDecision`；不启动 Driver。
- `profiles.py` 注册 `react_short/deep_research/ppt_pro/code_complex` 的 intent/capability/venue policy。产品名不得进入 `kernel.py`。
- 否定/只读 guard 在任何 write profile 之前执行；`route_task()` 现有启发式可作为 classifier adapter，但 Code/Research/PPT 不再由 `main.py` 二次判断。
- route decision 写 trace，包含候选、否决原因、capability revision；durable profile 不健康时返回显式 unavailable/degraded event，禁止 fall through ReAct。
- 测试矩阵覆盖 Text/Code/Voice、肯定/否定、Research/PPT/Code、launcher missing、read-only；Code venue Research/PPT 必须选对应 Workflow profile。
- 依赖：WI-1。

### WI-5 — 定义 Driver port 并收敛 ReAct Driver 〔AC-2, AC-3, AC-7, AC-9, AC-10, AC-14〕

- `Driver` 内部操作固定为 `start/signal/cancel/recover/close`；输出 typed async event stream，不持有 WS/SessionDB。
- 增加产品无关的 `DriverCommand`：`ExecuteTools`、`OpenDecision`、`DelegateRun`、`TerminalCandidate`。`DelegateRun` 包含稳定 `command_id`、child request、route hint（非产品名枚举）、capability subset、attachment policy 与 `join_policy=join_before_final|root_terminal_child|detached`；Kernel 对 child 再执行一次 Router，Driver 不 import workflow 产品。
- 保留 `ppt_create/deepresearch` 等公开工具名，但把当前“handler 内直接 start workflow”改为注册点的 `DelegationToolAdapter`：handler 只返回 typed `DelegationRequest`，不创建 run/不发 accepted；`UnifiedToolExecutor` 将其交还 ReActDriver，ReActDriver 再发 `DelegateRun`，只有 Kernel/ChildRunCoordinator 能实际 start 并生成 accepted。对应改动点为 `ppt_tools.py`、`research_tools.py`、`registry.py` 的 `accepted_async` normalization 和 `agent_loop.py` 当前 AsyncHandoff 分支。
- 将 `AgentLoop` 的 LLM/provider fallback/token/completion 核心移入 `harness/drivers/react.py` 或缩为其私有 collaborator；删除：
  - AgentLoop 自行全 batch `gather`；
  - 全局 Subagent completion queue drain；
  - PPT/产品专用 accepted handoff；
  - v2 envelope→字符串 ToolResultEvent；
  - Session/venue 级 cancel owner。
- 在 permission/plan/clarification 前写 `ReactCommandBoundary`：自包含 canonical messages（不依赖 SessionDB 投影先提交）、Session transcript cursor、PreparedContext/tool-set snapshot refs、完整 pending `PreparedExecutionCall`、provider/iteration/completion state和 decision id。boundary+decision 同一 workflow.db 事务先提交，SessionDB tool-call card 只是幂等 projection；PermissionGate 不再在 Registry 内 await Future，而是返回 `OpenDecision`。signal 后 ReActDriver 从 boundary 恢复消息栈并继续同一 call/effect。
- **任何含 write/recoverable Effect 的 batch，即使无需审批，也必须先把整个 prepared batch 持久化为 command boundary**：冻结原始 call 顺序、call ids、stable effect ids、args/tool/capability fingerprints 和已完成 outcome refs；若 Run 仍是 ephemeral，先以同一 run id promote durable。每个 effect 完成/unknown 后先把 journal outcome 与 continuation progress 同一 UoW 关联，再向模型消息栈回填。重启只从该 boundary 对账现有 effect outcome/receipt：committed 直接按原 call id 回填，unknown 先 reconcile，绝不重新让模型生成或换 effect id。全只读、明确不可 replay 的普通迭代仍不 checkpoint。
- S-5/动态 PPT/Research 使用 `DelegateRun`：短答可以先流出；`root_terminal_child` 让父 controller handoff 后根 Run 保持 nonterminal，由 child terminal 驱动根唯一 final；`detached` child 不阻止父 final。
- completion/Verify/Evidence 查询必须带当前 `run_id/turn_id/call_id/effect_id/target_hash/artifact_ref`；旧 session 同名 receipt 不计入证据，无法证明则 `unknown`。
- ReAct final 只向 Kernel 提交 `DriverTerminalCandidate`；不得自行写业务 terminal。
- 本 WI 只建立 test-only ReActDriver wiring；旧 AgentLoop 生产入口不变。测试：流式 token 首段不丢、provider fallback、短工具循环、boundary kill/reload/signal、同 pending call 不重复、无需审批单写 effect 在“外部写成功→outcome 回填前”kill/restart、mixed safe/read/write batch 在每个 effect 后 fault injection 且恢复顺序不变、DelegateRun 三种 join、当前证据门禁、旧 receipt 反例、driver cancel ack、无产品 import 静态检查。
- 依赖：WI-2、WI-3。

### WI-6 — 收敛 Workflow Driver 并保留 Native engine 〔AC-3, AC-6, AC-8, AC-9, AC-13, AC-18〕

- `workflow.py` 把 profile 映射到已注册 immutable workflow definition；Kernel 不知道版本名。
- 新请求由 `ExecutionUnitOfWork.start_workflow()` 用同一 `run_id` 原子创建 `execution_runs + workflow_runs`；workflow row 只拥有 graph/checkpoint/lease/node state，不拥有根 terminal delivery。
- 给 `WorkflowRunner/Service/Launcher` 增加显式 `WorkflowExecutionPorts`（decision/effect/public_event/final_candidate/unit_of_work），不是在现有 facade 外拦截。新 run ports 指向 generic stores；旧 `workflow_effects/decisions/events/deliveries` 仅供 absence-of-execution-row 的历史 run recovery/read。
- 具体拆缝：
  - 把 `WorkflowService.start_workflow:540-685` 的 identity/manifest/hash 校验抽成无 I/O `prepare_start() -> PreparedWorkflowStart`；
  - 把 `WorkflowRunStore.create_run:54-136`、`bind_session_refs:146+` 的 SQL 抽成接收现有 `aiosqlite.Connection` 的 `insert_*_tx`，public wrapper 仅供 legacy；
  - `ExecutionUnitOfWork.start_workflow(prepared, execution_spec)` 在一个事务内写 capability、execution run/link、workflow run/start_request/session_refs、execution accepted event/deliveries；
  - `WorkflowRunner` 增加 `run_precreated(run_id, state, context)`，不得再次调用 `create_run`；`WorkflowLauncher` 只 schedule/drive 已可靠 accepted 的 run。
- checkpoint/terminal 拆缝：
  - 给 `NativeCheckpointStore` 注入 `CheckpointExecutionAdapter`，所有 adapter 方法接收 checkpointer 当前已打开的 `aiosqlite.Connection`，自身禁止 commit/另开连接；
  - 把 `checkpointer.py::aput`、frontier `commit_*` 中现有 `_consume_decisions`、`workflow_node_effects→workflow_checkpoint_effects`、`_materialize_intent` 和 terminal update 分派为 `LegacyCheckpointExecutionAdapter` 或 `ExecutionCheckpointAdapter`；选择仍以 execution row 是否存在为准；
  - new adapter 在 checkpoint transaction 内 consume `execution_decisions`、验证相关 grant 已被当前 effect 一次性消费、把已 committed `execution_effects` 写入 `execution_effect_links`、分配 execution event seq；terminal commit 同时更新 workflow internal terminal 与 `execution_runs` terminal/outbox；
  - effect 实际外部执行仍在 checkpoint 前独立 journal transaction 完成；checkpoint transaction 只验证 effect status/fence 并原子建立 link，绝不重执行 effect。
- launcher missing、definition missing、start exception 在 accepted 前返回 typed failed/unavailable；accepted 后故障由同一 workflow run 恢复，绝不 fall through ReAct。
- workflow accepted/progress/waiting/terminal 全部提交 candidate 给 execution ports，seq 只由 ExecutionUnitOfWork 分配；历史 run 继续走 legacy projector，且入口只能是带 actor 授权的 `recover(legacy_run_id)`。
- 本 WI 只建立 test-only WorkflowDriver wiring；生产 launcher 仍按旧路径。测试：同 ID/start transaction fault injection、checkpoint 在 decision consume/effect link/event seq/final 每条 SQL 后 crash rollback、DR v7/PPT v1/Code v1 start/wait/resume/restart/cancel/effect replay；逐事件 owner SQL 断言；v1-v7 历史 fixture 100% read/recover；new-start/checkpoint SQL 断言不写 legacy effect/decision/event owner 表。
- 依赖：WI-1～WI-4。

### WI-7 — 实现薄 RunKernel 与结构化进程内任务树 〔AC-1, AC-2, AC-3, AC-6, AC-14, AC-15〕

- `kernel.py` 仅组合 Ledger/Router/DriverRegistry/Projector/Child/Health ports；源码 AST 禁止 DeepResearch/PPT/Code/Voice import 或字符串分支。
- `start`：active Ledger 分配 stable run id 与 live buffer→单次 route→按 durability policy 原子 promote→绑定 driver→启动；返回的 RunHandle 已携带订阅。同 idempotency key 不创建第二 run。
- active attached handles 用 `TaskGroup`/run-scoped task set 管理；它们只是当前进程执行句柄，事实仍在 Ledger。父 coroutine 正常 handoff 不等于 cancel detached child。
- `observe/signal/cancel/recover/close` 先调用 Ledger `authorize(ref, actor, action)`；`cancel` 持久化 `cancel_requested` 后按 link policy fan-out，收齐 driver/child ack 或 late reconcile 后终态 CAS。
- `recover` 根据 ledger driver kind 恢复，不重新 route；`close` 只释放句柄/订阅，不改业务终态。
- Kernel core（contracts/ports/kernel/router/children，不含 drivers/adapters/storage）≤2,000 LOC；公开操作恰为 6。
- 测试：并发 start、start/observe 首 token 时序、foreign-session actor 全操作拒绝、driver failure isolation、cancel tree、detached/root-terminal handoff、restart recover、terminal race、20 session event-loop fault isolation。
- 依赖：WI-4～WI-6。

### WI-8 — ChildRun 化 Subagent 与 Team 〔AC-5, AC-6, AC-10, AC-14, AC-16〕

- `ChildRunCoordinator.create()` 必须接收父 `RunContext`、attachment/join policy、capability subset 和 stable operation id；同一 UoW 原子写 child run/link + `execution_child_commands`，提交后才允许调度。
- `ChildRunScheduler` 对所有 durable Subagent、`DelegateRun`、`root_terminal_child`、SkillCandidate review 与 workflow child 统一执行 `pending→leased(epoch)→scheduled→acked`；Driver start 以 operation id 幂等，schedule lease/fence 阻止旧 scheduler ack。startup/retry reconciler 扫描 pending/过期 leased/scheduled-without-ack command；child terminal/join inbox 再以 terminal event id 幂等推进 parent。该协议覆盖 create→schedule、schedule→ack、ack→parent join 三个 crash gap，不能只给 Team 特例补偿。
- nonblocking Subagent 结果通过 child terminal event/join 查询，不再进入全局 `completion_queue`；fetch/await 强制校验 session/root/parent。
- blocking `agent_parallel` 可在父 Driver TaskGroup 内作为 spans，但仍使用 scoped capability/tool executor；不得注册到全局 queue。
- TeamTask 保持领域表；`claim_task` 同一 TeamStore 事务写 `claimed_pending_run + claim_epoch + team_childrun_commands(op_id)` domain outbox。`TeamChildRunReconciler` 用 `team:{team}:{task}:{epoch}` 幂等调用上述通用 ChildRun command UoW，ack 丢失可安全重放；child terminal 用 operation inbox 回写 TeamTask。跨库 claim→generic command 仍由 Team saga 补偿，generic create 之后的 schedule gaps 则统一由 `ChildRunScheduler` 补偿，Team message 不充当执行终态。
- 具体修改 `team_store.py::_SCHEMA_SQL/_VALID_STATUSES/_row_to_task/claim_task`：为 task 加 `claim_epoch/child_run_id/lease_expires_at`，新增 command outbox；`claim_task` 的同一 `BEGIN IMMEDIATE` 内完成状态更新与 command insert。`_ensure_schema` 增加 per-team schema version/幂等 ALTER，而不是只靠当前进程 `_initialised` cache 猜版本。
- **本 WI 仅建立 add-only/test-only wiring**；WI-12 前 production 的全局 Subagent queue、`cancel_all()`、AgentLoop drain 和 legacy Team/Voice consumers 全部保留为唯一旧 owner。新 coordinator 的测试 wiring 不得被生产 bootstrap 注册。
- 新路径完成 child 只保留持久 metadata，释放进程强引用；旧 queue/drain 的实际删除放入 WI-12 原子切换 commit。
- 测试：A/B session 并行 create/fetch/cancel、parent cascade、detached survive、通用 child 三 crash gap/duplicate schedule/stale epoch/root_terminal join、Team 跨库 saga/duplicate reconcile/lease reclaim、10k completed child 内存回收。
- 依赖：WI-7。

### WI-9 — 持久 signal/decision、取消与 Goal finalization 〔AC-6, AC-10, AC-13, AC-14〕

- Permission/Plan/Clarification/PPT outline/SkillCandidate 统一为 §4.8 的 typed `RunSignal` + persisted decision id/nonce/version；OpenDecision 与 ReactCommandBoundary/Workflow checkpoint 同一 UoW 提交。UI response 先 CAS store，再唤醒 active handle；重启时 ReAct 从 boundary、Workflow 从 checkpoint 恢复，绝不重新 route 或重跑已准备 Effect。
- Skill codify 保持 `SkillCandidateStore` 作为候选内容 authority；提案后创建 `skill_candidate_review` profile 的 detached ChildRun 和 `skill_candidate` decision，domain ref 冻结 candidate id/content hash。根对话可正常 terminal；UI `skill_candidate_confirm` signal child run，adapter 幂等调用 `SkillCodifier.confirm` 后 child terminal。重复/错 candidate/session/hash 响应拒绝，重启后从 candidate store + decision 恢复，不再依赖 `_SKILL_CANDIDATE_WAITERS`。
- 新 Kernel test wiring 中，`_permission_pending/_clarify_pending/_PLAN_CONFIRM_WAITERS/_SKILL_CANDIDATE_WAITERS/_PPT_OUTLINE_WAITERS` 不拥有事实；临时通知只封装在 `DecisionWakeupCache` 一个容器内且 miss/重启时读 store，缓存不能保存 continuation payload。
- **本 WI 仍是 add-only/test-only**：WI-12 前 legacy Text/Code/DR/PPT/Voice/recovery 的旧 waiters 与按产品取消逻辑保持原样且生产独占；不得提前翻 owner 或删除。新路径的 `/stop`、UI cancel、内部 deadline contract 只调用 `RunKernel.cancel(run_id)`，所有生产入口统一在 WI-12 原子切换。
- Goal/Plan/TeamTask/WorkflowRun 通过 typed link 更新；Kernel terminal CAS 是根请求唯一业务终态，driver child terminal 不能重复完成 root。Goal/SessionDB 位于另一事务域，因此 terminal UoW 同时写 stable `goal_projection/session_projection` delivery；projector 以 `terminal_event_id` 幂等 CAS 领域 store，失败由 outbox 重试，不尝试跨库事务。
- 测试：kill/restart decision response、permission grant 绑定/一次性消费、duplicate/expired/wrong-session signal、SkillCandidate accept/reject/restart/duplicate/hash mismatch、cancel while waiting、late effect cancel、root/child final race；静态/production-bootstrap 测试证明 WI-12 前 legacy waiter/queue 仍唯一可达而新 test wiring 不可达。
- 依赖：WI-7、WI-8。

### WI-10 — Text Adapter 与全链路切换就绪 〔AC-1, AC-3, AC-11, AC-15, AC-18〕

- `bootstrap.py` 在 startup 构造唯一 Kernel 与 capability health；初始化失败明确阻止对应 profile，不允许 broad-exception fail-open。
- 新增 `TextVenueAdapter`，把 chat payload 解码为 `RunRequest/HostContext/ActorContext` 后只调用 Kernel 六操作 API；在隔离的 live-stack test bootstrap 中连通 ReAct、Decision、ChildRun、WorkflowDriver 和 projector，覆盖普通短答、写 Effect、动态 DelegateRun、Code/DR/PPT profile。
- **此 WI 不修改生产 `/ws/control` 注册，也不删除 `_run_chat`、Code/DR/PPT pre-loop、旧 ToolResult projector 或 AutoResume。**原因是普通 Text 的动态 DelegateRun 也能创建 workflow；在 Workflow start/recovery 一起激活前，不能安全地只切“看似纯 ReAct”的入口。
- 生产仍由整套 legacy 路径唯一拥有；新 Text adapter 只在显式 test bootstrap 可达。切换前门禁运行新 full live-stack smoke 与旧 Text/Code/DR/PPT start+restart 双套测试，但同一个生产请求绝不双跑。
- 依赖：WI-7～WI-9。

### WI-11 — Voice Adapter 就绪但不切生产 〔AC-1, AC-9, AC-12, AC-18〕

- 为 `VoicePipeline` 增加 constructor-injected `RunClient` seam；test wiring 中让它仅保留音频 ingress、ASR、TTS、VAD、barge-in 与 PCM transport，并验证转写文本可经 `RunKernel.start`、统一 projector Voice sink 可驱动 TTS。
- **此 WI 不注册生产 Voice adapter，也不删除旧 Voice owner。**生产仍由现有 Voice ContextAssembler/AgentLoop/`chat_stream`、waiters 和 context merge 独占，避免 Voice 发起 workflow 后却只能由旧 background recovery 恢复。
- 测试覆盖短答 TTS、工具失败、permission signal、DR handoff accepted/progress/final、barge-in 按 current run id cancel、同时文字请求隔离；这些先作为新 adapter contract test，真实生产切换与 UI E2E 放在 WI-12。
- 依赖：WI-10；与 Text 一样只准备 adapter，不做生产 owner 翻转。

### WI-12 — 历史 V1 方案（已作废；不得执行）

> 首次执行已整体回退。以下内容仅保留事故复盘上下文；生产切换只能按 R6 执行。

- 用**一个不可拆分的 production commit**同时完成四件事：① 注册 WorkflowDriver 的 start 与 recover；② 把 Code/DeepResearch/PPT pre-loop 和 Voice 转为共享 Router/Kernel；③ 把 launcher recovery/team reconciler 转为 Kernel；④ 对新 workflow 关闭 legacy event/effect/decision stores。新 workflow 的第一次 start 与对应 recovery owner 从来不会跨版本分离。
- 同一 commit 将 `run_kernel_v1` 出厂默认设为 true，旧新请求入口不可通过配置重新打开；这个 flag 只表达完整 shared harness readiness，不承担灰度或双路由。
- commit 前状态：Text/Code/DR/PPT/Voice/recovery 全部=legacy；commit 后状态：所有新请求与新 recovery=Kernel。部署前后先停止接入并 drain/记录 active legacy run，再重启完成单点切换；不存在“新 WorkflowDriver 已 start、后台仍由 legacy recover”的可运行版本。
- 历史非终态 run 只按 `execution_runs` 缺失判定，由 WI-6 `LegacyPreparedCallAdapter` + compatibility reader 恢复；一旦存在 execution row 就禁止降级 legacy。切换验收必须在 `accepted` 与首个 checkpoint 之间 kill/restart，确认新 recovery 唯一接管且 effect 恰好一次。
- Voice 在同一 commit 使用 WI-11 已验证的 adapter；permission/plan/clarification/SkillCandidate/PPT signals 全走 Kernel/DecisionStore，barge-in 只 cancel 明确 current voice run id。
- 删除旧 Registry/ToolUsingAgent 注册入口、AgentLoop `dispatch()` compatibility branch、全局 Subagent registry/queue、`cancel_all()`/AgentLoop drain、Voice bridge、AutoResume 旁路、产品 waiter/task maps、`PermissionGate.current_source`、按产品猜测 task 的 cancel 逻辑与 Registry 新请求可达的旧 context merge；同时把 WI-8/9 的 ChildRun scheduler、DecisionStore/WakeupCache 注册为生产唯一 owner。
- `registry.dispatch()` 若历史 fixture 无调用需求则删除；否则移动到 `compat/legacy_tool_dispatch.py`，只接受 `LegacyRecoveryContext`，生产 start 路径静态不可达。
- workflow v1-v7 manifest/checkpoint reader 移入/标注 compatibility registry；只有明确 legacy run id 的 `recover` 可调用，不允许 route/start。
- 测试：全路由 golden、Text/Code/DR/PPT/Voice live stack、Voice 短答/TTS/permission/barge-in、accepted→首 checkpoint kill/restart、checkpoint→terminal kill/restart、历史 run absence-of-execution-row recovery、并发文字/语音隔离；随后按 S-1～S-7 做真实 UI E2E。
- 运行 owner audit：15 个基线容器最多 7 个仍拥有执行/决策事实；新增等价 owner=0。运行 import/call graph 测试证明新请求不可达 compatibility modules。
- orchestration 固定文件集总 LOC 净减≥20%；不能通过移出统计集规避，新增 `deskpet/harness`/`deskpet/execution` 全部计入。
- 依赖：WI-1～WI-11 全部门禁通过；不得拆成“先某个 venue/start、后 recovery”的多个生产 commit。

### WI-13 — 历史 V1 方案（已作废；由 R7 取代）

> 不执行本节；见 R7。

- Driver/Profile/Event/Tool stage registry 在 bootstrap 后导出 runtime manifest；架构测试比对实际注册与文档，不手写另一套阶段表。
- health 输出 Kernel、Driver、ToolExecutor、Ledger schema、compat reader 状态与 degraded reason；UI diagnostic bundle 不含 prompt/args/secret。
- 默认 ON capability 初始化失败时 profile 显式 unavailable；启动日志包含 active owner/schema/version，不静默弱化。
- 更新 `ARCHITECTURE/AGENT_HARNESS.md`、`ARCHITECTURE/ARCHITECTURE.md`、`ARCHITECTURE/AgentLoop.md`、`ARCHITECTURE/index.md`、`ARCHITECTURE/PROJECT_STATUS.md` 顶部日期、生产链路、证据。
- 依赖：WI-12。

### WI-14 — 历史 V1 方案（已作废；由 R7 取代）

> 不执行本节；见 R7。

- 先跑 S-1～S-5 自然语言 value smoke；主要矛盾不满足立即停止昂贵回归。
- 自动化层：contracts/store/property、router golden、tool failure injection、cross-session/cancel/restart、historical fixtures、frontend reducer、full backend/frontend regression。
- 性能层：chat Kernel 本地 p95≤10ms、TTFT p95 回归≤5%、token SQLite writes=0、20 session lag p99≤20ms/throughput≤10%、workflow transaction/bytes≤10%、metadata≤128KB/run、10k Run RSS±5%。
- 真人层：按项目 windows-mcp 纪律执行 S-1～S-7，逐 case 保存动作前声明、截图、Tauri/backend 日志；禁止 WS 直注替代。
- 维护 `testcase/index.md` 与本计划 `results.md`；所有测试通过后同步 ARCHITECTURE，确认能力默认 ON。
- 依赖：WI-13。

## 8. 迁移门禁

| Gate | 必须满足后才能进入下一段 |
|---|---|
| G1 Safety | R1/R3 的 trusted context、unsafe barrier、grant→effect claim、unknown/late effect 故障注入全绿 |
| G2 Semantics | typed outcome 在 Registry/Drivers/RunPresenter/前端/Voice 一致；accepted/failed/unknown 契约全绿 |
| G3 Drivers | ReAct 与 Workflow driver contract 各自恢复/取消/终态测试全绿，Native 历史 fixtures 不变 |
| G4 Kernel | 六个公开操作、单 route、父子/cancel/final CAS、产品 import=0、Kernel LOC≤450、combined core/UoW≤2,800 |
| G4.5 Product parity | §2.4 每个能力族的 canonical input、事件、持久副作用和 UI 行为均 100% 等价；adapter 输入字段无静默丢弃 |
| G5 Cutover | Text/Code/DR/PPT/Voice/start/recovery/decision/delivery 在 R6 一次性全走 Kernel；旧 owner 同 commit 删除 |
| G6 Simplification | owner≤7、等价新增 owner=0、总 orchestration LOC 净减≥20%、legacy 新请求不可达 |
| G7 Release | AC-1～18、S-1～7、性能/隔离/内存/兼容/真人 E2E 全部有证据，ARCHITECTURE 已更新 |

## 9. AC 追溯（唯一生效）

R0 创建真实 testcase 名称后必须回写本表；R5 每行只能是 `PASS` 或阻止 R6，禁止 `PARTIAL`。下面的 production callsite 是验证目标，不是允许保留旧 owner。

| AC | R | production callsite / owner | testcase / evidence | 当前结果 |
|---|---|---|---|---|
| AC-1 | R2,R3,R5,R6 | `main.py` Text/Code → Preparer/Kernel/Presenter | parity: text short/tool/code | R0 待冻结 |
| AC-2 | R3,R4,R5 | Kernel/Driver registry（无产品分支） | AST + six-op contract | R0 待冻结 |
| AC-3 | R0,R3,R4,R5,R6 | Router → ReAct/Workflow Driver | route/profile + durable no-fallback | R0 待冻结 |
| AC-4 | R1,R3,R5 | trusted HostContext → UoW/ToolRegistry | reserved-field/capability tests | R0 待冻结 |
| AC-5 | R1,R4,R5 | ChildRun command/link/lease/session scope | cross-session + duplicate scheduling | R0 待冻结 |
| AC-6 | R1,R3,R4,R5 | Kernel cancel → Driver/Child tree/UoW | stop/barge-in/cancel restart | R0 待冻结 |
| AC-7 | R0,R1,R3,R5 | ToolRegistry batch/permission/effect | safe barrier + grant claim | R0 待冻结 |
| AC-8 | R1,R3,R4,R5 | Effect claim/execute/settle/reconcile | unknown late-effect fixture | R0 待冻结 |
| AC-9 | R1,R2,R3,R5 | Tool/Run outcome → Presenter/Voice/frontend | typed outcome golden | R0 待冻结 |
| AC-10 | R3,R5 | EvidenceContext → verifier/receipt/artifact | old-session same-name negative case | R0 待冻结 |
| AC-11 | R0,R3,R4,R5 | single Router/ProfileRegistry | read-only/Code/DR/PPT matrix | R0 待冻结 |
| AC-12 | R0,R2,R5,R6 | Voice transport → shared Preparer/Kernel/Presenter | tags/TTS/barge-in/handoff | R0 待冻结 |
| AC-13 | R1,R3,R5 | Decision/Boundary UoW | restart/duplicate/wrong-session signal | R0 待冻结 |
| AC-14 | R1,R2,R4,R5 | final UoW → Goal link/event/delivery | root/child/final race | R0 待冻结 |
| AC-15 | R4,R6,R7 | bootstrap registry/health/degraded UI | missing-driver/profile startup | R0 待冻结 |
| AC-16 | R0,R1,R3,R4,R6 | production owner reachability | AST owner + fixed-SHA LOC audit | R0 待冻结 |
| AC-17 | R0,R4,R7 | generated manifest + ARCHITECTURE facts | registry export/doc evidence | R0 待冻结 |
| AC-18 | R0,R2,R5,R6,R7 | observable product behavior end-to-end | parity ledger + S-1～S-7 | R0 待冻结 |

## 10. 关键假设 spike 证据与方案修订

> H-1～H-12 的运行事实仍可复用，但其中推导出的“新增独立 Ledger/Decision/Effect/Projector”设计结论已被首次 WI-12 反证并废止。R1 只复用关于事务、幂等、unsafe barrier、crash gap 和 Native recovery 的实验事实，具体 owner 以 §4.9 为准。

临时代码曾位于本计划目录的 `spikes/`，真跑后删除，不进入业务实现。以下保留可复现命令、实际输出和结论。

### H-1 — 普通 ReAct 是否能同步写完整 Ledger

命令：

```powershell
backend\.venv\Scripts\python.exe plans/2026-07-20-agent-harness-simplification/spikes/ledger_perf_spike.py
```

实际输出（第二轮）：

```json
{"ephemeral_runs":2000,"ephemeral_start_p95_ms":0.006,"runs":200,"op_p50_ms":28.121,"op_p95_ms":52.284,"op_p99_ms":67.717,"db_bytes":90112}
```

结论：**原假设不成立**。20 并发下每个 Run 同步执行 create+2 events+final 的 `WAL+synchronous=FULL` p95 远高于 10ms；stable in-memory start p95 0.006ms。方案已改成 §4.4 的分级持久化：普通短 ReAct 不同步写 execution DB，首次等待/Child/写 Effect 前原 id promotion；Workflow/Child 一开始 durable。执行期仍需用真实聊天 baseline 验证 TTFT，而不是把该 microbenchmark 当最终性能证据。

### H-2 — mixed batch 是否可保持源顺序和安全并发

命令：`...\python.exe ...\spikes\tool_batch_spike.py`

实际输出摘要：结果顺序为 `safe-a,safe-b,unsafe-a,unsafe-b,safe-c`；`safe-a/safe-b` 同时在 0.000x 秒开始，`unsafe-a` 在二者结束后开始，`unsafe-b` 在 `unsafe-a` 结束后开始，最后才启动 `safe-c`。

结论：**成立**。实现模式固定为“连续 safe segment 并发 + unsafe barrier”，不能接用当前会把全部 safe 提前的 helper。

### H-3 — timeout 后 late effect 与重启对账

命令：`...\python.exe ...\spikes\late_effect_spike.py`

实际输出：

```json
{"timed_out":true,"late_callback_cas":1,"restart_reconcile_cas":1,"target":"one-write","write_invocations":1}
```

结论：**成立，但有适用前提**。`shield` 保留 future 可在同进程 late-finalize；重启后必须依赖持久 reservation + tool-specific observable target/receipt reconcile，不能恢复丢失的 future。无 reconciler 的外部动作必须保持 `unknown`，禁止自动重试。当前 `workflows/adapters/tool_executor.py:57-114` 已实现 shield/late callback，WI-2 应泛化复用，不另写一套。

### H-4 — Native Workflow 能否保留外部分配的 Run id 与历史恢复语义

命令：

```powershell
...\python.exe ...\spikes\workflow_link_spike.py
...\python.exe -m pytest backend/tests/test_workflow_deep_research_graph.py::test_fetch_crash_restart_retains_checkpointed_search_result backend/tests/test_workflow_ppt_pro_graph.py::test_outline_restart_continue_does_not_repeat_research_or_outline backend/tests/test_workflow_code_graph.py::test_proposal_checkpoint_restart_does_not_repeat_llm_and_reuses_partial_effects -q
```

实际输出：

```text
{"external_id_preserved":true,"status":"completed","checkpoint_count":1,"restart_history_count":1,"runner_constructor_has_execution_ports":false}
3 passed in 4.87s
```

结论：**身份/immutable graph/checkpoint 假设成立；port 注入目前不存在**。`WorkflowRunner.start(run_id=..., trace_id=...)` 可保持 Kernel id，三条产品 graph restart 测试通过；但 Runner constructor 只有 store/saver/registry/trace，因此 WI-6 必须真实增加 `WorkflowExecutionPorts + ExecutionUnitOfWork` seam，不能宣称现成可插拔。

### H-5 — AgentLoop 是否可脱离 main.py 独立成为 Driver 核心

命令：

```powershell
...\python.exe -m pytest backend/tests/test_agent_loop_pipeline.py::test_pipeline_off_is_bc_no_pipeline_events backend/tests/test_agent_harness_runtime_contract.py::test_runtime_golden_tool_result_is_fed_back_before_final -q
```

实际输出：`2 passed in 0.26s`。

结论：**核心循环可独立运行**：fake provider/tool 下无需 main.py/WS/SessionDB。build_agent 的产品装配仍需按 WI-5 移到 Driver bootstrap；这不是运行时不可行假设。

### H-6 — ReAct waiting 是否存在可恢复的最小 command boundary

命令：`...\python.exe ...\spikes\react_boundary_spike.py`

实际输出：

```json
{"first_signal_cas":1,"duplicate_signal_cas":0,"effect_inserted_once":1,"duplicate_effect_insert":0,"continuation_status":"consumed","target":"written-once"}
```

结论：**成立**。持久 transcript cursor + PreparedCall/effect id + decision CAS 足以在新 Driver 实例恢复等待边界并保证一次写；但这要求 PermissionGate 从“内部 await Future”改为 `OpenDecision` command。普通 token iteration 仍不 checkpoint。

### H-7 — Voice transport 能否由 Kernel event 驱动而不构造 AgentLoop

命令：`...\python.exe ...\spikes\voice_kernel_spike.py`

实际输出：

```json
{"kernel_starts":1,"accepted_frames":1,"assistant_transcripts":1,"tts_binary_frames":1,"agentloop_constructed":false}
```

结论：**成立**。用真实 `VoicePipeline._process_utterance`、fake ASR/TTS 和 stub Kernel 覆盖“先短答再 accepted child”，TTS/WS 正常；实施需把临时 subclass seam 正式化为 constructor 注入的 Run client 并删除旧 `_run_with_tools`。

### H-8 — Team claim 与跨库 ChildRun start 能否 crash-safe

命令：`...\python.exe ...\spikes\team_saga_spike.py`

实际输出：

```json
{"command":["team:task-1:1","team:task-1:1",1],"run_count_after_duplicate_reconcile":1}
```

结论：**成立**。Team DB 同事务写 stable operation outbox，Execution start 以该 key 幂等，ack 丢失重放不会创建第二 Run。WI-8 必须实现此 saga/reconciler，不能尝试跨 SQLite 原子事务。

### H-9 — checkpoint/final 同库 UoW 与一次性 grant 是否可原子收敛

命令：`...\python.exe ...\spikes\checkpoint_uow_spike.py`

实际输出：

```json
{"wrong_grant":0,"correct_grant":1,"duplicate_grant":0,"all_six_faults_rolled_back":true,"success":["completed","completed","consumed",1,1]}
```

结论：**成立**。同一 SQLite connection 可把 checkpoint、decision consume、effect link、workflow internal terminal、execution terminal 与 final event 作为一个事务；在六个写点逐一注入异常均无部分提交。grant 对错 call 返回 0、正确消费 1、重复消费 0。该 spike 验证的是事务/约束形态；实施仍必须按 WI-6 将现有 `NativeCheckpointStore` 的 connection 传给 transaction-aware adapter。

### H-10 — durable/live 双序列与 per-sink delivery 能否避免互相确认

命令：`...\python.exe ...\spikes\event_delivery_spike.py`

实际输出：

```text
{'durable': [(1,), (2,)], 'live': [('epoch-a', 1), ('epoch-a', 2), ('epoch-b', 1)], 'deliveries': [('session_db', 'delivered', 1), ('tts', 'delivered', 1), ('ws', 'delivered', 2)], 'duplicate_rejected': True}
```

结论：**成立**。durable seq 与按 stream epoch 重置的 live seq 可在同一 event 表用互斥约束独立排序；同一 terminal event 的 SessionDB/TTS/WS delivery 独立 CAS，WS 首次失败只重试自己的 row，其他 sink 不被重复确认；同一 event/sink instance 的重复建单被唯一约束拒绝。实施仍需用 WI-3 的重启、epoch 迟到与前端 reducer 测试验证真实链路。

### H-11 — 无审批写 Effect 成功后、消息回填前崩溃能否安全恢复

命令：`...\python.exe ...\spikes\react_write_recovery_spike.py`

实际输出：

```text
{'next_index_at_crash': 1, 'rehydrated': [('read-1', 'committed', 'old'), ('write-1', 'committed', 'written-once')], 'target_rows': 1, 'same_order': True}
```

结论：**成立**。先冻结整个 mixed batch 与 stable effect ids，再以 journal outcome 对账，可以在 write 已提交但 continuation cursor 尚未推进时，按原 call 顺序重建两条 ToolOutcome，目标写入仍只有一条；恢复无需重新调用模型或重执行写 effect。实施必须覆盖真实 Registry/LateEffectSupervisor 的 committed 与 unknown 两类结果。

### H-12 — 通用 ChildRun create/schedule/ack crash gap 能否统一补偿

命令：`...\python.exe ...\spikes\child_command_recovery_spike.py`

实际输出：

```text
{'command': ('acked', 2), 'driver_start_count': 1, 'child': 'running', 'link_count': 1}
```

结论：**成立**。child run/link/command 同事务创建，scheduler 在“已 schedule 未 ack”后重启并提升 lease epoch，Driver 仍按 operation id 只 start 一次，最终 command ack、child running、link 各一条。该通用协议可承载 Delegate/Subagent/SkillCandidate/workflow child；Team 只额外处理 TeamStore→generic command 的跨库 saga。

LOC/owner 不是静态阅读无法确认的外部技术假设，而是执行门禁；以 §7.0.1 的固定 SHA + 自动发现 benchmark 和 R6 前 AST/owner audit 为准，最终结果不达标即不能完成。

## 11. 预期验证命令（执行阶段）

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/harness_simplification -q
$harnessTests = @(rg --files backend/tests | Where-Object { $_ -match 'test_agent_harness_.*\.py$' })
backend\.venv\Scripts\python.exe -m pytest $harnessTests -q
$workflowTests = @(rg --files backend/tests | Where-Object { $_ -match 'test_workflow_.*\.py$' })
backend\.venv\Scripts\python.exe -m pytest $workflowTests -q
backend\.venv\Scripts\python.exe scripts/acceptance/harness_owner_audit.py --json
backend\.venv\Scripts\python.exe scripts/bench/harness_baseline.py --compare plans/2026-07-20-agent-harness-simplification/baseline.json
backend\.venv\Scripts\python.exe scripts/acceptance/last_mile_smoke.py
Set-Location tauri-app
npm run test -- --run
npm run typecheck
```

最终执行者需按仓库实际测试分组拆跑，不能以 glob 不支持或单个慢套件为由跳过。真人 E2E 另按 `AGENTS.md` 的 Windows 真点击纪律执行。

## 12. 回退与兼容

- 当前安全基线是 schema v6；R1 先用纯 additive v6→v7 migration 增加 runtime/drain/owner/fence 数据，默认 `legacy` 且不切生产 owner，不修改历史 checkpoint blob/manifest。migration 失败整体事务回滚；R6 只执行已在 R1～R5 演练的 phase CAS 与 wiring activation。
- R1～R5 不改变生产 owner，可逐 commit 回退；R6 activation 前任一 gate 失败整体停在 R5。R6 在 ingress 关闭且尚未创建首条新 execution row 时可撤销 activation；首条新 row 产生后禁止代码回退，只能保持 fail-closed 并 roll-forward。
- 已创建的新 execution run 不允许降级到旧 ReAct/legacy workflow；恢复失败必须显式 degraded/failed 并保留诊断。
- 历史非终态 workflow 继续由 compatibility registry 恢复；删除条件是受支持历史 retention 窗口结束且 fixtures/用户迁移政策明确批准。
- 数据永不反向重写旧 manifest/checkpoint；Goal/Session/Artifact 只增加 typed link/projection，不删除用户历史。

## 13. 完成定义

- G1～G7 全部通过；AC-1～AC-18 无 PENDING/PARTIAL。
- 新请求只进入 RunKernel；Kernel 产品 import/分支=0、公开操作=6、Kernel≤450 LOC，`execution/ + harness/ + execution_uow.py`≤2,800 LOC。
- owner 基线 15→≤7，新增等价 owner=0；固定 orchestration 集合总 LOC 净减≥20%。
- unsafe write 无并发、reserved context 无覆盖、unknown late effect 无重复提交、跨 session child 泄漏=0、根 terminal delivery=1。
- S-1～S-7 真实 UI 证据完整；性能、内存、兼容、迁移指标达标。
- `ARCHITECTURE/`、PROJECT_STATUS、testcase、results 与默认 ON 配置在同一次交付更新。
