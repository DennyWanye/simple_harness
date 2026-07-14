# Plan：DeskPet 原生轻量 Workflow Engine

> **状态：✅ 2026-07-11 已完成。** 自动化、干净依赖、冻结构建、Windows Computer Use HITL、PPT 交付与重启历史恢复均通过。结果见 [`../manual-results-2026-07-11-native-workflow-engine/RESULTS.md`](../manual-results-2026-07-11-native-workflow-engine/RESULTS.md)。

## 主要矛盾

决定成败的核心问题不是“删掉三个依赖”，而是把 LangGraph 当前隐式承担的 **frontier、execution identity、checkpoint commit、interrupt continuation** 四项语义显式收回 DeskPet，同时保持 effect/lease/HITL/Trace/Session 产品契约不变。只改 import 会造成假轻量、恢复回退或重复副作用。

三条生产图的实际调度很小：DeepResearch 是单链加一次 gap 循环；PPT Pro 是单链、outline interrupt、逐页 `image_map` 自循环和有界视觉修订；Complex Code 是单链、有界 proposal/audit 循环与三个 interrupt。它们没有生产级 Pregel fan-out。公共 `WorkflowDefinition` 已声明 join 和 reducer，因此 native v1 保留“同一 frontier 内确定性顺序执行、统一 merge、join barrier”的小型兼容语义，不实现分布式或任意动态图。

## 关联验收标准

覆盖 AC-1..18 的既有 durable runtime 能力，并新增覆盖 AC-24..31。核心追溯：Task 1/2/3 → AC-25/26；Task 4 → AC-27；Task 5 → AC-29；Task 6 → AC-28；Task 7 → AC-24/30；Task 8/9 → AC-31 与无回归。

## 方案与取舍

### 采用：原生 canonical-JSON checkpoint + 确定性 frontier executor

- 复用现有 `WorkflowDefinition/StatePatch/WorkflowContext/WorkflowRunner/workflow.db`，只替换 `bind()` 后的内核。
- 节点 handler、ToolRegistry ports、effect ledger、Artifact/Receipt、Trace/Evaluation、Outbox 与 Session reducer保持产品边界。
- 每完成一个 frontier，短事务提交 state/frontier/head/ownership，并将 `succeeded_pending` 原子提升为 `succeeded`。
- interrupt 使用 DeskPet 控制流类型，在同一 fenced checkpoint 事务中写 waiting snapshot、open decision 和 run waiting。

### 放弃：继续保留 LangGraph 作为 optional legacy engine

它仍要求依赖、冻结收集和两套恢复语义，不满足 AC-24/30。当前用户数据库无 waiting/running/retryable legacy run，只有一个已 blocked run；终态历史可由 run/event/trace/artifact 表继续展示。旧非终态 checkpoint 不自动执行，明确 blocked/quarantine。

### 放弃：复刻 Pregel

三图没有生产图级并行；内部网络/图片并发已经封装在节点 port 内。只实现本项目已有声明契约所需的 frontier/join/reducer，不实现 distributed worker、subgraph checkpoint 或动态 Send。

## 最佳实践调研与项目适配

- LangGraph 的持久化实践是“每个 step 保存 state，失败 superstep 保留已成功 pending writes”。本项目保留这个结果语义，但不用其 `CheckpointTuple/channel_versions/pending_sends` 数据模型；DeskPet 三图更适合显式 `state + frontier + activation ledger`。[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- LangGraph interrupt 要求 checkpoint 和 resume cursor 持久化、interrupt 前副作用幂等。本项目照用语义，改造成 `WorkflowSuspended + decision CAS + effect ledger`，并把 waiting checkpoint/open decision放入同一SQLite事务。[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- AWS Step Functions 把 retry视为状态转换，持久跟踪attempt、interval、backoff和max delay。本项目数据量小、不需要服务化调度，但采用同样的 durable retry record，避免进程内sleep丢状态。[AWS Step Functions error handling](https://docs.aws.amazon.com/step-functions/latest/dg/concepts-error-handling.html)
- SQLite `BEGIN IMMEDIATE` 在事务开始时取得写事务，适合DeskPet单机多协程下的fence/head CAS短事务；不把LLM、网络或工具调用放在事务中，避免长锁。[SQLite transactions](https://www.sqlite.org/lang_transaction.html)

## 典型调用链（PPT 大纲确认）

1. `main.py` 路由到 `WorkflowLauncher`，创建 run 并调用 `WorkflowRunner.run()`。
2. Runner claim lease，materialize native executable，注入 traced ports、observer 和 progress。
3. Native executor 从 head snapshot 或 initial state 取得 `wait_outline_decision` task，生成稳定 `NativeExecutionInfo`。
4. handler 调用 `workflow_interrupt(payload)`；无 resume response 时抛 `WorkflowSuspended`。
5. native checkpoint store 在一个 `BEGIN IMMEDIATE` 中校验 fence/expected head，写 native snapshot（frontier 仍为当前节点）、open decision、attempt waiting、run waiting、head/owner并释放 lease。
6. Session 决定通过 nonce/version CAS 持久化；run 变 retryable，即使 IPC 在随后启动 resume 前崩溃，startup recovery 仍能发现 unconsumed response。
7. 新 Runner claim 后重进同一节点；`workflow_interrupt` 返回匹配 response；handler 路由到 preflight/revise/terminal；新 checkpoint commit 同时 consume decision。

这条模式推广到 Code clarification、plan approval 和 dynamic tool approval。

## 文件影响清单

| 文件 | 职责 | 本次改动 |
|---|---|---|
| `backend/deskpet/workflows/native.py` | 新原生执行内核 | NativeTask/Snapshot/ExecutionInfo、activation/join frontier、durable retry、route、merge、interrupt/resume |
| `backend/deskpet/workflows/control.py` | 原生控制流 | `WorkflowInterrupt`、`WorkflowSuspended`、context-local resume binding |
| `backend/deskpet/workflows/definition.py` | Graph contract/compiler | `bind()` 直接创建 NativeWorkflowExecutable；删除 LangGraph StateGraph/Command/control-flow imports |
| `backend/deskpet/workflows/store/checkpointer.py` | 原 checkpoint protocol | 重写为 `NativeCheckpointStore`，canonical JSON、fenced commit、waiting commit、history/fork primitives |
| `backend/deskpet/workflows/store/schema.py` | workflow.db | 增量 schema version/format metadata；保留现有 lineage/effect/outbox 表 |
| `backend/deskpet/workflows/store/run_store.py` | run/node transaction | attempt 分配、native checkpoint promotion、resolved decision work-item 查询 |
| `backend/deskpet/workflows/runner.py` | lease/recovery | 使用 native store/executable；startup 自动恢复 retryable decision；legacy head fail-closed |
| `backend/deskpet/workflows/launcher.py` | accepted-async driver | immediate resume与startup recovery统一按run lock/lease驱动，按head interrupt读取未消费decision |
| `backend/deskpet/workflows/service.py` | IPC/service bridge | resolve只提交权威decision并触发幂等driver，不把当前协程当恢复保证 |
| `backend/deskpet/workflows/bootstrap.py`, `backend/main.py` | 启动/入口 | quiesce legacy、注册native、恢复work item后开放入口；记录engine marker |
| `backend/deskpet/workflows/replay.py` | history/fork | 直接读取 NativeSnapshot；native fork transaction；旧终态历史不解码执行 |
| `backend/deskpet/workflows/human.py` | durable decision | native interrupt/open/consume 原子接口，保留 nonce/version CAS |
| `backend/deskpet/workflows/outbox.py`, `effects.py` | checkpoint附属记录 | 增加复用同一SQLite connection的in-transaction helpers |
| `backend/deskpet/workflows/store/__init__.py`, `workflows/__init__.py` | exports/env | 导出native store，删除旧saver与`LANGGRAPH_STRICT_MSGPACK`设置 |
| `definitions/v1/ppt_pro.py`, `definitions/code_nodes.py` | 业务 HITL | 改用 `workflow_interrupt`，删除 LangGraph imports；更新 engine policy manifest |
| `scripts/workflow_eval_adapter.py` | 本地 eval | 改为 native in-memory/temporary store，不依赖 `InMemorySaver`/`__interrupt__` |
| `backend/pyproject.toml`, `uv.lock` | 依赖 | 删除 LangGraph 三包及仅由其带入的传递依赖，`uv lock` 重建 |
| `backend/deskpet-backend.spec`, runtime hook | 冻结构建 | 删除 LangGraph hidden imports 和 hook；删除 hook 文件 |
| `backend/tests/test_workflow_*` | 回归 | 替换 LangGraph fixtures/types，增加 native engine/crash/import guard |
| `ARCHITECTURE/*`, `STATUS/*`, testcase/results | 文档与证据 | 回写原生架构、测试和真机结果 |

## Task 1：冻结原生模型与控制流 [AC-24/25/27]

- 新增 `NativeTask`：`task_id/node_id/invocation_key/activation_id/join_epoch/task_path/input/retry_attempt/next_attempt_at`；`NativeSnapshotEnvelope`：`engine_kind/snapshot_version/state_schema_version/thread_id/checkpoint_ns/checkpoint_id/parent_checkpoint_id/run_id/step/state/frontier/completed_activations/join_firings/node_writes/interrupt/metadata`。关系列与payload identity加载时逐项比对，不一致即quarantine/fail closed。
- 所有类型只接受 `JsonValue`；canonical JSON 有大小、深度、identity 和 schema 校验；持久判别符唯一固定为 `checkpoint_type="deskpet-native-json-v1"`，内部 `engine_kind="deskpet-native"/snapshot_version=1`。
- 新增 `NativeExecutionInfo`，字段与 `NodeExecutionIdentity.from_execution_info()` 现有要求一致。`task_id` 基于 run/thread/namespace/base checkpoint/invocation key；重试不改变 task id，attempt 从 durable attempts 分配。
- `workflow_interrupt(payload)` 读取 context-local execution control：有匹配且尚未 consumed 的 response 时返回并登记 `decision_id`；否则以稳定 `interrupt_id=hash(task_id|ordinal)` 抛 `WorkflowSuspended(BaseException)`。禁止复杂对象和同一 exclusive task 多 interrupt。
- 验证：纯类型/canonical/identity/interrupt 单测；生产源码 import guard。

## Task 2：实现 NativeCheckpointStore [AC-26/27/29]

- 将workflow schema升级为v2：在现有表上增量增加`workflow_checkpoints.engine_kind TEXT NOT NULL DEFAULT 'langgraph-legacy'`、`snapshot_version INTEGER`；`workflow_pending_writes.write_kind TEXT`、`payload_json TEXT`、`node_execution_id TEXT`；`workflow_node_attempts.next_attempt_at REAL`；`workflow_decisions.consumed_at REAL`、`consumed_checkpoint_id TEXT`。`initialize_workflow_db()`读取`PRAGMA user_version`，在`BEGIN IMMEDIATE`内按v1→v2执行可重复检查的`ALTER TABLE`/索引并最后写`user_version=2`；fresh DB直接创建v2。测试真实v1 fixture、重复initialize和中途失败rollback。
- 保留 `workflow_checkpoints/pending_writes/checkpoint_owners/checkpoint_effects` 表与主键；新 payload 写 canonical JSON bytes，旧行依 `checkpoint_type/engine_kind` 明确识别。
- `load_head/get/list` 返回 NativeSnapshot，不构造 `CheckpointTuple`。
- `load_execution(head)`同时读取该base head的native pending task results，按snapshot frontier验证task_id/activation_id。已有同payload `succeeded_pending`的task直接复用patch并由Trace reconciler收敛，不再次调用handler；只执行缺失或到期retry task。测试A已提交、B retry/crash后重启，A调用次数保持1。
- `ensure_genesis(fence, run, initial_state)`在claim后、任何handler前以deterministic `checkpoint_id=hash(run|namespace|"genesis")`做expected-head=NULL CAS，同事务写initial state、entry frontier、owner和head。重复启动返回同一head；覆盖genesis commit前/后崩溃。
- `commit_task_result(fence, expected_head, NativeTask, attempt, StatePatch)`以`(thread,ns,base_head,task_id,write_index)`幂等写pending `state_patch` JSON，并在同一事务把attempt/node置`succeeded_pending`；同key同payload重放幂等，异payload为`pending_write_conflict`。observer的span finish可在事务后收敛，但节点状态权威由该事务写。
- 所有提交API接受稳定`operation_id=hash(run|base head|operation kind|task/frontier identity)`，checkpoint ID同样确定。若CAS发现current head/attempt/run transition已带同operation，返回数据库权威结果；只有不同operation推进head才报stale。fault injection覆盖genesis/task/frontier/retry/interrupt/failure的`after_db_commit_before_return`。
- `commit_frontier` 使用 `BEGIN IMMEDIATE`：验证 lease epoch/run version/status/expected head；验证 frontier task results；确定性 merge state patch；写 checkpoint/owner/effect/blob refs；推进 branch head；消费 pending writes与本次resume使用的decision；把对应 attempt/node从 `succeeded_pending` 提升 `succeeded`；将StatePatch中的delivery intents以唯一`event_key="intent:{intent_id}"`写Outbox后commit。同一checkpoint多个同kind intent靠intent_id区分。
- 条件selector必须是无副作用pure route；frontier patches reduce后，执行器先调用`commit_route_selection(operation_id, expected_head, source, selected_route, next_frontier_payload_hash)`写native pending `route_selection`。崩溃恢复复用该route不再调用selector；同operation不同route/hash为`route_nondeterminism`并由`commit_engine_failure`隔离。
- `EffectJournal.link_checkpoint_in_transaction(db, ...)`、`WorkflowOutbox.ensure_event_in_transaction(db, ...)`和blob-ref helper接受现有connection、不自行commit；`commit_frontier`从`workflow_node_effects`及StatePatch refs收集effect/blob/artifact/receipt。公开旧API改为自己开事务后调用helper。Checkpoint commit是intent materialization唯一owner；Launcher不再创建terminal/artifact/outline事件，只读取并投递已materialize event ids，`workflow.final`也只由一个stable final intent产生。
- 新增 `commit_retry`：同一task/activation保持稳定，持久增加attempt、`next_attempt_at`和安全error ref，把run置`retryable`并释放lease；bootstrap只在到期后claim，绝不依赖进程内sleep。
- retry语义锁定：`max_attempts`表示包含首次调用的总尝试次数；attempt从1开始。仅当`attempt < max_attempts`且error code在allowlist时`commit_retry`，否则节点错误走`commit_failure`。默认`max_attempts=1`绝不重试；覆盖1/2次、重启后耗尽及failure final exactly-once。
- `commit_interrupt` 同事务写 waiting snapshot、pending interrupt、prepared/open decision、attempt/node waiting、run waiting并释放 lease；不能拆成 runner 补写。
- `commit_failure`同事务校验fence/expected head，写attempt/node failed、run error/status、稳定失败outbox并释放lease；取消在handler运行时先由`request_cancel`递增epoch/version，随后任何task/frontier/failure commit都因stale fence不能推进head，cancel reconciler统一收口attempt/decision。
- `commit_engine_failure(expected_head, frontier, error)`处理handler后发生的route未知、reducer冲突、frontier非法和snapshot canonical失败：保留pending evidence，把相关`succeeded_pending` attempts标`failed_engine`，原子关闭run/lease并写唯一failure final intent；operation幂等。补route/reducer/snapshot故障测试。
- cancel reconciler是外部取消终态的唯一materializer：运行中、waiting和active-effect延迟取消最终收敛到cancelled时，在attempt/decision/grant收口同一事务写`intent:{run_id}:cancel-final`与`workflow.final(cancelled)`；after-commit-return重放幂等。Launcher只投递该event，不另造final。
- `WorkflowRunner.finalize_run(operation_id, expected_head, domain_status)`是正常completed/domain-failed唯一终态materializer：在一个fenced事务更新run status/error/recovery、写reserved `intent:{run_id}:run-final` 的`workflow.final` payload，并保留terminal node已materialize的business intents。completed、domain failed、cancelled各自final恰好一次；Launcher不合成final。
- 每个Outbox intent在materialize事务中读取run持久`delivery` session ref/epoch并创建对应pending delivery row。独立`WorkflowLauncher.dispatch_pending_deliveries()`按delivery status扫描，不依赖run非终态或当前commit返回值；bootstrap在开放入口前和后台周期均扫未投递rows。因此`after_frontier/finalize_commit_before_delivery`进程退出后仍重投，event/delivery key幂等且epoch guard仍生效。
- 兼容旧pending表NOT NULL列：native row同时写`channel="__native__:" + write_kind`、`value_type="json"`、`value_blob=canonical payload bytes`，新列写相同语义；读取native只认`write_kind/payload_json`，读取legacy只认旧列。fresh v2与v1迁移后执行同一native insert/read测试。
- `workflow_decisions` 增加 `consumed_at/consumed_checkpoint_id`，`DecisionStatus.CONSUMED`；checkpoint commit以`resolved + expected version` CAS变为consumed，resume查询只返回resolved且未consumed，重复消费返回原权威结果而不再次推进。
- `commit_native_fork` 保留 deterministic fork saga、root namespace、危险 effect 和 pending/fanout fail-closed限制。
- legacy：终态 rows 只读元数据；无论有无head，所有非终态legacy implementation均在开放workflow ingress前被quiesce扫描为`legacy_checkpoint_incompatible`并block，绝不 pickle、父checkpoint fallback或自动重跑。启动顺序固定为：关闭入口/无旧runner → block legacy非终态 → 注册native → recover native → 开放入口。
- 验证：原 fault matrix 两阶段窗口、stale fence、corrupt/quarantine、fork 三窗口及 mixed-format tests。

## Task 3：实现 NativeWorkflowExecutable [AC-25/26]

- `WorkflowDefinition.bind(checkpointer)` 直接返回 native executable；不动态构造 TypedDict/Annotated/StateGraph。
- 初始调用必须先调用`ensure_genesis`再从entry frontier执行；恢复只从run-owned head读取state/frontier，禁止thread global latest。
- 每 step先调用`load_execution`复用已提交task results，再按task id排序执行缺失任务；每新task按 observer started → progress started → `use_span(node span)` → handler → `commit_task_result` → observer span succeeded_pending；retry policy只对allowlisted retryable `WorkflowNodeError`生效，失败通过`commit_retry`持久化attempt/到期时间。永久错误走`commit_failure`；`WorkflowSuspended`走`commit_interrupt`。
- activation规则冻结：每个parent checkpoint为派生任务生成`activation_id=hash(parent|source task|edge target|loop epoch)`；静态fan-out共享parent activation；join snapshot记录`join_epoch + expected predecessor task ids + completed ids + fired checkpoint id`，齐备后exactly-once派发。循环回边生成新epoch，严禁跨轮拼接。
- 合并分两步：`merge_patches(writes)`处理同frontier writer冲突；`reduce_state(previous, frontier_delta)`应用旧state。`SINGLE_WRITER`替换旧值；`DICT_DISJOINT`与旧key重叠仅同值幂等，否则冲突；`STABLE_LIST`按`item_id_key`保留旧顺序、同ID同值幂等、新ID按writer/task稳定顺序追加、同ID异值冲突。
- 条件edge在reduced state上选路；interrupt-capable node保持exclusive frontier。编译器使用SCC分析：任何含环SCC必须通过`loop_budget_bindings`显式绑定现有持久counter/上限；无budget自环、双节点环与未绑定多出口环拒绝。三图补齐显式binding。
- 每个 commit 后检查 cancel fence和 `max_supersteps`；超过上限稳定 `invalid_state`，不再保留 LangGraph recursion概念。
- `ainvoke/resume` 公共 facade保持，`astream`仅产生原生 committed snapshot事件或在无调用方时移除并由契约测试锁定。
- 验证：顺序、条件、显式budget loop、旧state reducer、frontier merge、循环内join、乱序join完成、join崩溃补齐/exactly-once、durable retry到期/重启、cancel、max-step、crash after handler/before commit；逐项覆盖unknown node、mixed edge、非法writer、interrupt sibling/parallel和无budget SCC。

## Task 4：迁移 HITL 与恢复 [AC-27]

- PPT 和 Code 四个 pause 点改用 `workflow_interrupt`；business payload和 route不变。
- Decision resolve 后的 `retryable + resolved/unconsumed` 是持久 work item；`recover_expired()` 和 bootstrap能够继续，不依赖原 IPC coroutine。
- resume 重新进入同一task；成功checkpoint commit将decision从`resolved` CAS为`consumed`并记录checkpoint。重复、过期、wrong-run response继续拒绝；已consumed response只返回幂等终态，永不重新排队。
- `HumanDecisionStore.build_run_resume_payload(run_id, interrupt_id)`只查询当前head snapshot绑定的`resolved AND consumed_at IS NULL` decision，拒绝历史其他interrupt。`WorkflowLauncher.drive_run(run_id)`是唯一driver：immediate resolve与startup都提交同一幂等drive request，由run lock+lease仲裁；`WorkflowService.resolve_decision`不再保证当前IPC内完成resume。`bootstrap`在quiesce/registry后枚举到期retry和resolved-unconsumed native run并交给launcher，解决resolve commit后进程崩溃窗口。Launcher删除旧`terminal:{intent_id}` ensure逻辑，只投递checkpoint已materialize的`intent:{intent_id}`。
- Launcher启动同时运行`dispatch_pending_deliveries`，覆盖已经terminal的run；driver只负责执行/恢复，不承担事件创建。
- `WorkflowLauncher.dispatch_due_work()`是native durable work唯一唤醒器：后台循环查询最早`next_attempt_at`、foreign `lease_expires_at`、resolved-unconsumed decision和cancel-requested run，以`min(next_due-now, capped idle interval)`等待并由stop event可中断；到点先`recover_expired()`，再为到期work提交幂等`drive_run`。即时resolve、startup和周期scan共享同一run lock+lease，永远只有一个driver。bootstrap创建launcher后先扫一次再启动循环，shutdown显式cancel/await；无due work时不忙轮询。
- 更新 policy manifest中的 `langgraph-interrupt` 为 `deskpet-native-interrupt-v1`，新 implementation hash自然变化；旧非终态不冒充同一实现。
- 验证：PPT outline、Code clarify/plan/tool四门，resolve→resume前崩溃、重启、双击、过期、取消。

## Task 5：迁移 replay/fork/eval/Trace [AC-29]

- Replay history把 NativeSnapshot投影为现有 service/IPC JSON；终态 legacy run的run/event/trace/artifact仍可查，legacy checkpoint内容标 `legacy_unavailable`。
- Fork读取native state/frontier，验证reserved identity和类型，生成native child root checkpoint；source不变，三crash window幂等。`commit_native_fork`清空source `node_writes/interrupt/pending retry`，用child run/thread/root checkpoint重新派生所有frontier `task_id/activation_id/join_epoch`；可继承纯state与已完成业务值，但不继承source execution IDs、pending results或fired marker。若join ledger无法安全重建则继续fail closed。测试source/child下一node相同但task/node execution完全隔离。
- `workflow_eval_adapter.py` 使用 native temporary SQLite/in-memory facade，interrupt以原生结构识别。
- node wrapper继续注入 Trace span、progress和执行生命周期；handler必须位于`use_span(SpanContext(trace_id,node_span_id,run_id))`内，自动化断言tool/LLM child span parent不变。现有`trace_runs`不加列；确定性workflow root span与每个node span attributes写`engine_kind="deskpet-native"`、`snapshot_version=1`，runner结构化日志写同字段，供AC-31测试和真机共用。Trace reconciler扫描native pending result：若attempt已`succeeded_pending`但node span仍running，则以相同deterministic span id幂等finish OK；覆盖task commit后/observer finish前崩溃且handler不重跑。
- 验证：history只读、fork lineage/GC/effect确认、eval datasets/experiments、Trace parent-child和Session seq无回归。

## Task 6：三条生产图迁移与产品契约回归 [AC-28]

- 注册的 DeepResearch/PPT Pro/Complex Code definition直接materialize native executable，默认flags保持ON。
- 保持所有节点、条件路由、loop budgets、ports、Artifact/Receipt/delivery intents和用户错误语义。
- PPT继续逐页稳定effect、strict full-page、不回退模板、多构图、visual warning和单卡progress。
- `outline_ready`不再直接发送无幂等notifier；handler把outline-card projection写入StatePatch delivery intent，由checkpoint同事务Outbox stable key发布。客户端继续按event id去重；fault test覆盖handler完成后/checkpoint前崩溃不重复大纲卡。
- Complex Code继续权限/VerifyGate/工具partition；DeepResearch继续阶段checkpoint和citations。
- 验证：三图聚焦+production wiring+launcher/product delivery+Session history tests。

## Task 7：删除依赖与冻结收集 [AC-24/30]

- 删除 `pyproject.toml` 三个LangGraph包；运行 `uv lock`，确认无 `langgraph*`、无仅由其带入的 `langchain-core/langgraph-sdk/prebuilt/checkpoint-sqlite`。
- 删除spec hidden-import循环、runtime hook引用与 `pyinstaller_runtime_hook_langgraph.py`；更新冻结验证脚本。
- 删除`workflows/__init__.py`中的`LANGGRAPH_STRICT_MSGPACK`环境设置、`store/__init__.py`旧saver export和bootstrap旧构造；source guard覆盖`scripts/workflow_eval_adapter.py`。
- 重写dependency smoke：扫描生产 `.py/.toml/.spec/.ps1` 禁止LangGraph import/package；创建无`system-site-packages`临时venv，按重建lock安装backend，在其中导入bootstrap、编译三图并跑最小native graph。
- 记录 lock package count、LangGraph传递包和spec hidden-import差异。

## Task 8：自动化门禁 [AC-24..30]

- 精确聚焦命令：`pytest -q backend/tests/test_workflow_native_engine.py backend/tests/test_workflow_checkpointer.py backend/tests/test_workflow_fault_matrix.py backend/tests/test_workflow_human.py backend/tests/test_workflow_human_integration.py backend/tests/test_workflow_runner.py backend/tests/test_workflow_recovery.py backend/tests/test_workflow_replay.py backend/tests/test_workflow_dependency_smoke.py`。必须包含genesis、task-result crash、schema v1→v2、durable retry、permanent failure、cancel stale fence、resolve-recovery和engine marker用例。
- 同组必须覆盖route selection持久化/非确定冲突、`max_attempts=1/2`、completed/domain-failed/cancelled final exactly-once、frontier/finalize commit后delivery前进程退出与terminal-run startup重投。
- durable wake测试：不重启时future retry到期自动继续；重启时foreign lease尚未过期，expiry后自动reclaim；periodic scan与immediate resolve并发只drive一次；dispatcher在wake前/后崩溃并重启仍收敛；stop生命周期无遗留task且idle不忙轮询。
- 三图/product命令：`pytest -q backend/tests/test_workflow_deep_research_graph.py backend/tests/test_workflow_ppt_pro_graph.py backend/tests/test_workflow_code_graph.py backend/tests/test_workflow_launcher.py backend/tests/test_workflow_product_delivery.py backend/tests/test_workflow_ppt_production_wiring.py backend/tests/test_workflow_code_production_wiring.py`；再跑`pytest -q backend/tests -k "workflow or ppt"`和harness/agent/tool相邻回归。
- 前端运行tsc和Session progress/Trace Inspector测试，确保事件协议不变。
- 运行源码guard、`uv lock --check`、项目既有verify脚本，并执行真实PyInstaller build；从冻结产物启动backend，断言native bootstrap/三图注册成功且不出现missing import。冻结构建不可因耗时跳过。
- 与 baseline 比较，区分既有红项和本次回归。

## Task 9：Windows Computer Use 真机闭环 [AC-31]

- 仅由Tauri启动backend/vite，日志确认source backend和`engine_kind=deskpet-native`。
- 新Session真实输入6页PPT请求；截图单卡progress和outline waiting；真实点击确认；观察native resume、逐页生成和最终Artifact。
- 断言Trace/log `engine_kind=deskpet-native`与`render_mode=full_page_images`；复制交付pptx到证据目录并结构检查6页每页`pictures=1`、`text shapes=0`、图片全幅覆盖。自动化另覆盖provider不可用时strict full-page以可恢复错误结束、不模板回退。
- 应用重启后回到同Session，确认一张完成卡和附件仍在；数据库/Trace检查run head checkpoint type为native JSON。
- 证据保存到`plans/manual-results-2026-07-11-native-workflow-engine/`，更新testcase/index、plans/index和STATUS。

## 可追溯矩阵

| AC | Tasks | 自动化 | 真机 |
|---|---|---|---|
| AC-24/30 | 1,7,8 | source/import/lock/frozen guard | boot log无LangGraph |
| AC-25 | 1,3 | native graph contract/engine tests | 三图真实执行 |
| AC-26 | 2,3,8 | fault/recovery/fence matrix | 重启恢复 |
| AC-27 | 1,2,4 | HITL crash/CAS tests | PPT大纲真实确认 |
| AC-28 | 6,8 | 三图/wiring/product回归 | PPT完整交付 |
| AC-29 | 2,5,8 | trace/eval/replay/fork tests | history/attachment恢复 |
| AC-31 | 9 | 辅助日志/DB断言 | Windows真实点击 |
