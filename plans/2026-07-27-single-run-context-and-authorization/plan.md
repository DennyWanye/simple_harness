# Plan：单一主 Run 上下文与授权资源闭环

## 主要矛盾

- 决定成败的核心问题不是给 `IntentTriage` 再补几段历史，而是取消“低上下文前置
  模型可以阻断高上下文主 Agent”的第二认知 authority，同时把它承担的权限、验证、
  Companion 成长信号等职责迁回各自真正的 Host authority。
- 同一边界问题也造成了 `workflow_spawn` 故障：模型可以表达目标和选择 Profile，
  但可授权资源只能由冻结 Run identity、catalog generation、Profile 和 Host workspace
  决定。任何需要授权的 prepared call 都不能在没有确定性 selector 的情况下进入
  AuthorizationRuntime。

## 关联验收标准

- 覆盖 `acceptance.md` 的 AC-IT-1 至 AC-IT-18。
- 场景覆盖 S-IT-1 至 S-IT-8；S-IT-1 至 S-IT-7 需要 Windows 真机交互证据，
  S-IT-8 使用自动化歧义 fixture。

## 最佳实践调研与本项目适配

1. **终态采用 durable projection，而不是 Presenter 双写。**
   Microsoft Event Sourcing 指南要求 projection consumer 对 at-least-once delivery
   幂等，并以事件 identity 去重。DeskPet 已有 execution terminal delivery/outbox，
   因此复用它，不引入新的全量 Event Store，也不让 `RunPresenter` 成为第二写入权威。
   参考：
   <https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing>
2. **授权 deny by default，但契约故障与真实拒绝分开表达。**
   OWASP 要求默认拒绝、逐请求验证、最小权限。DeskPet 保留空 selector fail closed；
   仅把“Host 没有准备出可授权资源”归一为结构化工具失败，让模型重新规划。用户拒绝、
   过期、nonce/version 冲突和 scope 越界仍是授权拒绝，不能自动降级为许可。参考：
   <https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html>
3. **授权资源 identity 必须显式且稳定。**
   RFC 8707 的核心约束是授权请求明确指出受保护资源。DeskPet 的适配不是照搬 OAuth，
   而是保证 exact authorization request 中的 `ResourceSelector` 由 Host 规范化，
   selector、最终参数和 handler containment 指向同一资源。参考：
   <https://datatracker.ietf.org/doc/rfc8707/>
4. **放弃的方案。**
   - 不把完整 ContextBundle 继续喂给 IntentTriage：这仍保留两个认知 authority 和
     一次额外 Provider 往返。
   - 不在 `RunPresenter._present_error()` 直接 append Session：这会制造 execution
     terminal 与 UI presenter 的双写窗口。
   - 不关闭 `workflow_spawn` 授权、不改成只读分类、不绕过 AuthorizationRuntime。
   - 不允许唯一裸 capability 名兼容扩展成模糊猜测；0 个或多个匹配继续拒绝。

## 典型调用链解剖

### 普通前台 turn

现状：

`Venue -> prepare_context(canonical messages) -> route_intent(current text + prior_task_type)
-> 可短路/澄清 -> plan_decision -> RunKernel.start -> ReAct`

目标：

`Venue -> prepare_context(canonical messages + Companion specialized decisions)
-> freeze terminal Session target -> RunKernel.start -> ReAct 内澄清/工具准入/验证`

主 Run 成为唯一会话认知 authority；Companion preference/growth interpreter 只产生
非授权的 typed evidence，不能短路或选择 Driver。

### 需要授权的工具调用

目标通用模式：

`model args -> ToolSpec.prepare 规范化 final_params -> resource_scope_resolver(final_params,
trusted ToolExecutionContext) -> PreparedToolCall.resource_selectors
-> AuthorizationRuntime.build_exact_request -> decision/grant -> execute exact final_params`

resolver 缺失、空 selector 或 resolver 合同异常均在 Driver 工具边界形成
`authorization_scope_missing`；真实 deny/expiry/conflict 仍走现有 fail-closed 协议。

### 根 Run 终态错误

目标通用模式：

`root terminal RunEvent -> frozen DeliverySpec(session_id + epoch)
-> ExecutionDeliveryDispatcher -> SessionTerminalDeliverySink
-> append_message_if_epoch(workflow_event_id=event.event_id)`

child terminal 不投影；重复 replay 由 event ID 去重；Session tombstone/epoch 改变时
discard，不产生幽灵消息。

## 关键假设 spike

### workflow_spawn 当前失败是可运行复现，不是静态推测

使用现有 `test_model_workflow_spawn.py` 的真实 ProfileRegistry、ToolRegistry 和
ToolExecutionContext，prepare 后调用 `PreparedAuthorizationRuntime.build_exact_request`。

命令：

```powershell
$env:PYTHONPATH='F:\projects\deskpet\backend'
# 内联 Python：复用 _profiles()/_request()，prepare workflow_spawn 后打印 policy、
# selectors，并调用 build_exact_request。
```

实际输出：

```text
authorization_required= True
resource_selectors= ()
exact_request= ValueError prepared tool has no deterministic resource selectors
```

这闭环了 Task 6/7 的关键前提：`dangerous=False` 并未跳过授权，空 selector 会在
exact request 稳定失败。

### delivery claim fencing 可复用现有 outbox，而不是旁路 sink

运行现有真实 SQLite/UOW 故障测试：

```powershell
backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/harness_simplification/test_execution_projector.py::test_expired_claim_is_recovered_and_stale_worker_cannot_ack `
  backend/tests/harness_simplification/test_execution_projector.py::test_terminal_session_message_is_visible_once_after_crash_restart -q
```

结果为 `2 passed`。它证明现有 owner-generation、lease expiry、delivery-version CAS 和
event-id 去重语义可直接复用于 targeted selector；新 API 只允许在同一事务查询上增加
event/sink 过滤，不能另建一条投递链。

### staged binary 假设被真实 handler spike 否定

在一次性 `TemporaryDirectory` 中调用真实 `doc_create(..., output_path=probe.docx)`，
结果 `ok=true`，目录内只有最终 `probe.docx`，没有 staging sibling。这证明当前
Office handler 是 direct writer；计划因此改为如实使用 `opaque_manual` +
PreparedTarget/exact selector，而不在没有 stage/commit 执行者时宣称 `staged_file`。

同时对当前已加载 builtin registry 做只读审计，得到 40 个 spec，其中 9 个
authorization-required spec 缺 resolver：
`doc_create, doc_edit, excel_create, file_organize, memory_forget, memory_write,
pdf_export, ppt_create, ppt_pro`。动态注册的 `workflow_spawn` 另由 Task 6 的真实
ProfileRegistry spike 覆盖。Task 9 的固定 audit 必须让两类目录最终都为 0。

### preference 并发没有 provider exactly-once 前提

代码路径是 `load_turn_decision -> await interpreter.interpret ->
commit_preference_turn_decision_receipt`，receipt 建立前没有 claim/lease。计划已删除
“只调用一次 provider”的错误承诺，只验收 first-writer durable decision 以及一次
growth admission/reflection；因此本 slice 不依赖未经证明的 singleflight。

## 文件影响清单

| 文件 | 职责 | 本次改动 |
|---|---|---|
| `backend/deskpet/harness/adapters/venues.py` | 产品 turn 编排 | 删除 route/plan 前置阻断，直接启动主 Run；冻结 session epoch；更新 trace |
| `backend/deskpet/agent/turn_preparer.py` | Context 组装 | 删除 IntentTriage/plan 决策 API；从 Companion specialized decision 结算 growth；生成行为型 verification profile |
| `backend/deskpet/agent/intent_triage.py` | 旧前置模型分诊 | 删除 |
| `backend/deskpet/agent/problem_pipeline.py` | 旧问题流水线包装 | 删除 |
| `backend/config.py`, `backend/main.py` | 生产组合 | 删除旧配置/接线；注册 Session terminal sink/contributor |
| `backend/deskpet/companion/preferences.py` | 现有 specialized interpreter | 在同一次既有判定中返回非授权 `growth_signal_kind` |
| `backend/deskpet/companion/turn_authority.py` | Companion turn authority | 冻结并暴露 typed growth decision，不新增 Provider 往返 |
| `backend/deskpet/companion/signals.py` | growth ingress | 使用 Companion 决策幂等结算 message ingress |
| `backend/deskpet/agent/session_terminal_delivery.py` | 新增会话终态投影 | 定义 target/contributor/sink、脱敏摘要、epoch fence |
| `backend/agent/agent_loop.py` | 主循环验证门 | 去掉 IntentCard 字段依赖，按冻结 verification profile、实际 receipt/effect/claim 运行 |
| `backend/deskpet/agent/self_check_gate.py`, `evidence_gate.py` | 验证策略 | 改为行为型档位；删除 problem_type 作为唯一门槛 |
| `backend/deskpet/tools/orchestration_controls.py` | delegate control | 增加 workflow_spawn Host-bound resolver |
| `backend/deskpet/tools/capabilities.py`, `tool_search.py` | deferred capability | 唯一裸名规范化为 canonical ID；强化 schema 文案 |
| `backend/deskpet/harness/drivers/react.py` | prepared/permission 边界 | 归一两条授权资源合同失败路径为结构化工具失败 |
| `backend/deskpet/tools/registry.py` | ToolSpec 目录不变量 | 增加授权资源合同审计 API/测试钩子，不放宽执行时 fail closed |
| `backend/deskpet/tools/doc_tools.py`, `excel_tools.py`, `pdf_tools.py`, `ppt_tools.py`, `file_organize_tools.py`, `memory_tools.py` | 其他写类工具 | 为可达写工具增加与 final_params/Host scope 同源的 resolver/prepare/containment |
| `ARCHITECTURE/AGENT_HARNESS.md`, `COMPANION_GROWTH.md`, `PROJECT_STATUS.md` | 架构事实源 | 测试通过后同步生产链路、状态和证据 |
| `testcase/` | 持久测试资产 | 增加本 slice 的自动/真机 case 与证据索引 |

## 任务清单（按依赖顺序）

### Task 1 — 锁定绿色基线与旧耦合清单  [AC-IT-10, AC-IT-12]

- 改动文件：
  `plans/2026-07-27-single-run-context-and-authorization/baseline.md`
- 现状：
  工作树已有其他 Harness observability/context usage 修改，不能覆盖；旧 triage 测试
  分散在 turn preparer、pipeline、venue、AgentLoop 和 main composition。
- 修改方式：
  - 记录 `git status --short` 和本 slice 允许修改的文件；
  - 用 `rg` 固化 `IntentTriage|IntentCard|problem_pipeline|chat_v2_intent|
    chat_v2_contradiction|pipeline_problem_type|pipeline_needs_investigation` 清单；
  - 跑最小基线：turn preparer、React Driver、permissions、capability bridge、
    workflow_spawn、Companion ingress、terminal delivery 测试。
- 验证：
  基线报告区分既有失败与本 slice 回归；不修改用户其他 dirty 文件。

### Task 2 — 取消前置认知/计划阻断，普通 turn 直接进入唯一主 Run  [AC-IT-1, 2, 4, 8, 10, 11, 12]

- 改动文件：
  `backend/deskpet/harness/adapters/venues.py`、
  `backend/deskpet/agent/turn_preparer.py`、
  `backend/config.py`、`backend/main.py`。
- 现状：
  venue 依次执行 `route_intent()`、两个短路出口和 `plan_decision()`，然后才启动 Run。
- 修改方式：
  - venue 在 `prepare_context()` 后直接构造 request payload 和
    `PreparedRunContextV1`，进入 `KernelRunClient.start()`；
  - 删除 `RoutedTurnIntent/PlannedTurnDecision`、`route_intent()`、
    `plan_decision()`、`ProblemHandlingPipeline` 生产接线和 feature config；
  - 删除 `<意图>/<主要矛盾>` 注入和 `chat_v2_intent/chat_v2_contradiction`；
  - active skill 保持由 Context OS 冻结的 scope 直接进入主 Run；
  - trace 改为 `prepare_context -> run_start`，保留 request/turn/root identity；
  - 普通 turn 不再构造 `AdmissionSpec`，因此不再为 root-plan admission 传入
    `provider_launch_snapshot`，也不再调用 `domain_sink.store_plan()` 或
    `set_plan_read_only()`；`AdmissionSpec`/Kernel admission 协议本身保留给独立
    workflow 和其他显式产品 admission；
  - manual mode 改为首个真实 prepared action 由
    `PreparedAuthorizationRuntime.plan_prepared_call()` 打开 exact durable
    decision，允许后在同一授权事务创建/扩展 TaskGrant；auto mode 也只在 exact
    prepared-call path 创建 policy grant。删除的是模型猜测 action-category 形成的
   宽 root-plan grant，不是 TaskGrant 或 exact authorization；
  - 删除旧模块及只为旧链路存在的测试，迁移仍有价值的场景到主 Run 测试。
- 验证：
  - spy provider 证明 ordinary turn 在 root Run 前没有 `purpose=classifier`；
  - chitchat、歧义请求、active skill 均有非空 root_run_id；
  - 相同 `session_id+request_id+turn_id` 仍恢复同一 Run。
  - `test_r55_admission_kernel.py` 证明独立 admission 未被删除；
    `capabilities/test_authorization_runtime.py` 和 ReAct 集成覆盖 manual first grant、
    grant expansion、auto exact grant、user deny、expiry/conflict；
  - 旧/新 oracle 严格绑定本目录 `behavior-contract.md` 的 BC-IT-1/2。
- 依赖：Task 1。

### Task 3 — 迁移成长信号到现有 Companion specialized authority  [AC-IT-7, 8, 11]

- 改动文件：
  `backend/deskpet/companion/preferences.py`、
  `backend/deskpet/companion/turn_authority.py`、
  `backend/deskpet/companion/signals.py`、
  `backend/deskpet/agent/turn_preparer.py`。
- 现状：
  IntentTriage 的 `growth_signal_kind` 在 route finally 中结算 message ingress；
  Companion turn authority 已有一次模型 preference interpretation 和 durable receipt。
- 修改方式：
  - 把 `PREFERENCE_TURN_INTERPRETER_VERSION` 从 `1` 升为 `2`，扩展既有
    provider schema，增加只允许
    `none|explicit_correction|explicit_capability_request` 的
    `growth_signal_kind`；
  - 同一个已有 Provider 调用同时给出 preference observation 与 growth hint，
    不增加新的 Run 前模型往返；
  - 将 decision 冻结在 `PreparedCompanionTurnV1`，由 `prepare_context()` 完成
    `settle_semantic_intent()`；失败不阻止主 Run，但保留 retryable receipt 语义；
  - growth hint 只触发 Companion admission/reflection，不授予任何工具或副作用权限；
  - `PreferenceTurnDecision.from_mapping()` 兼容 receipt schema v1/v2：历史 v1
    缺字段固定解释为 `growth_signal_kind=none`，不回放模型、不改写 receipt；新
    inference/receipt 写 schema v2；
  - v2 assessment payload 含 interpreter version，因此 assessment hash 自然变化；
    `source_message_ref+source_message_hash` 仍是 first-writer replay fence；
  - prompt 的旧规则“Skill 修改应 abstain”改为“preference decision 仍 abstain，
    但 growth_signal_kind=explicit_capability_request”，拆开两个正交输出。
- 验证：
  明确纠正、明确能力创建、一次性任务、闲聊四类 typed fixture；v1 receipt replay；
  同一消息并发 prepare 允许在 receipt 建立前出现重复的 provider sample，但
  `source_message_ref+source_message_hash` 的 first-writer receipt 决定唯一有效决策，
  只允许一次 growth ingress/reflection；本 slice 不虚构 provider exactly-once。
- 依赖：Task 2。

### Task 4 — 将验证策略从 IntentCard 改为冻结行为证据  [AC-IT-5, AC-IT-6]

- 改动文件：
  `backend/deskpet/agent/turn_preparer.py`、
  `backend/main.py`、`backend/agent/agent_loop.py`、
  `backend/deskpet/agent/evidence_gate.py`、
  `backend/deskpet/agent/self_check_gate.py`。
- 现状：
  EvidenceGate 由 `needs_investigation` 开关；SelfCheck 由 `problem_type` 选档。
- 修改方式：
  - 新增 `backend/deskpet/agent/verification_policy.py` 的 frozen
    `VerificationProfileV1(schema_version, evidence_required,
    self_check_mode, reason_codes)`，其中 mode 为 `off|light|strict`；
  - `prepare_workflow_request_payload()` 从 Context OS 已有 `bundle.task_type`、
    tool exposure intent、active skill scope 确定 profile，序列化到 immutable
    request payload 的 `verification_profile`，不调用新模型；
  - ReAct Driver 严格反序列化该 Host 字段并传给 AgentLoop；非法/缺失值按当前
    `bundle.task_type` 重新推导，仍无法识别时进入 `strict + evidence_required`，
    绝不降级到 light；模型参数不能构造；
  - 第一阶段（候选回答前）：research/debug profile 且 `_evidence_gathered=False`
    时由 EvidenceGate 阻止零证据终结；
  - 第二阶段（候选回答产生后）：profile 非 off、ledger 非空或候选文本含
    VerifyGate 可识别的 effect claim 时运行 SelfCheck。外部事实要求由第一阶段覆盖，
    文件/副作用完成声明由第二阶段 claim-to-receipt 对账覆盖；
  - 纯对话/解释且无外部事实 claim 时走 light/pass；
  - 保留 Receipt、VerifyGate、provider unknown-after-handoff 现有语义；
  - 删除 AgentLoop 的 `pipeline_problem_type/pipeline_needs_investigation` 命名和依赖。
- 验证：
  调试零证据会被 nudge；真实取证后通过；写入无 receipt 不得声称成功；普通解释不被
  强迫调用工具。
- 依赖：Task 2。

### Task 5 — 根 Run 终态错误 durable 投影到原 Session  [AC-IT-2, 3, 9, 11]

- 改动文件：
  新增 `backend/deskpet/agent/session_terminal_delivery.py`；
  修改 `backend/deskpet/harness/adapters/venues.py`、
  `backend/deskpet/agent/session_history_planner.py`、
  `backend/deskpet/harness/bootstrap.py`、`backend/deskpet/harness/projector.py`、
  `backend/deskpet/execution/ports.py`、
  `backend/deskpet/workflows/store/execution_uow.py`、`backend/main.py` 和导出模块。
- 现状：
  Presenter 只将错误发给 WebSocket；execution terminal delivery 已支持 frozen sink。
- 修改方式：
  - 在 turn 启动前从 SessionDB 读取 `session_delivery_state`，把 epoch 放入 Host-only
    request payload；
  - `SessionTerminalDeliveryContributor` 只为前台根 Run 冻结
    `session_id+epoch` target，使用 `DURABLE_REQUIRED`；
  - `SessionTerminalDeliverySink` 只接受 root terminal failed/cancelled，生成包含任务
    摘要、稳定 code/class、可理解 message 的脱敏 assistant/system 摘要；
  - 以 `workflow_event_id=event.event_id` 调用 `append_message_if_epoch()`，
    projection 标记为 conversation；epoch 改变或 session tombstone 时 discard；
  - 新增 frozen `SessionTerminalDeliveryTargetV1(session_id, session_epoch)`，target ID
    使用 canonical JSON + URL-safe encoding，parse 时严格检查 schema/version；start
    前若 state 已 tombstone，则拒绝创建新的普通 turn；
  - 解决 async epoch / sync contributor：venue 在 async ingress 中读取 state，并把
    Host 生成的 typed `session_terminal_target` 放入保留 payload；contributor 只同步
    parse，模型 payload 若带同名保留字段直接拒绝；
  - `sink.is_bound()` 检查当前 epoch/未删除，`deliver()` 再用
    `append_message_if_epoch()` 做最终 CAS；返回 None 时抛
    `DeliveryDiscarded("session_epoch_mismatch")`；
  - composition 在开放 ingress 前同时注册 contributor 和
    `SinkRegistration("session_terminal","session-transcript-v1",sink)`；缺任一边
    startup fail fast；
  - 为“报错后立即追问”增加双保险：
    1. 给 `ExecutionUnitOfWork`/SQLite 实现增加
       `claim_delivery_for_event(event_id, sink_keys, owner_generation, ttl)`，并给
       `ExecutionDeliveryDispatcher` 增加 `run_for_event(event_id, sink_key)`；
       terminal observer 在 commit 后、事件呈现前只 targeted drain 当前 event 的
       Session sink，不阻塞其他 growth/workflow delivery；
    2. `SessionHistoryPlanner` 组装下一 turn 时 read-through execution UOW 的最近
       root terminal；若该 event_id 尚未出现在 transcript，则临时合并同一脱敏摘要。
       最终 delivery 到达后按 event_id 去重；
  - Presenter 保持 UI 职责，不直接持久化终态错误。
- 验证：
  root fail 一条、replay 不重复、child fail 不刷屏、session 删除后晚到投影被丢弃、
  provider unknown outcome 只解释不重发；另加并发测试：terminal commit 后立即启动
  下一 turn、后台 dispatcher 尚未自然轮询时，Context 仍包含该 event 一次。
- 依赖：Task 2。

### Task 6 — 修复 workflow_spawn 的确定性授权资源  [AC-IT-13, 14]

- 改动文件：
  `backend/deskpet/tools/orchestration_controls.py`、
  `backend/tests/harness_simplification/test_model_workflow_spawn.py`。
- 现状：
  `shell + dangerous=False` 仍需授权，但 ToolSpec 没有 resolver。
- 修改方式：
  - 新增 `_workflow_spawn_resources(profiles)` resolver factory；registration 时闭包冻结
    `profiles.generation` 和 `model_spawnable` keys，调用时校验 args 中 generation/key
    与闭包一致；
  - system selector 固定为
    `workflow_spawn:{root_run_id}:{catalog_generation}:{profile_key}`，
    access=`delegate`；
  - catalog generation 不信任模型参数，也不从缺少该字段的 ToolExecutionContext
    猜测；selector 使用闭包冻结 generation，模型字段只做等值校验；
  - 仅当 `write_scope_root/workspace` 是可信且 containment 一致时增加 filesystem
    read/write selector；忽略模型 objective/workspace_ref 中扩大范围的路径；
  - 注册 resolver identity=`workflow-spawn-parent-scope`, version=`v1`。
- 验证：
  prepared selector 精确值、模型路径不可扩大、exact request 成功、auto/manual 均进入
  DelegateRun、ticket/child/recovery 回归。
- 依赖：Task 1。

### Task 7 — 授权资源合同错误归一为可重规划工具失败  [AC-IT-15]

- 改动文件：
  `backend/deskpet/harness/drivers/react.py`，必要时
  `backend/deskpet/tools/registry.py`。
- 现状：
  resolver/prepare 异常成为通用 `tool_prepare_failed`；prepared 空 selector 在
  permission decision 阶段抛 `ValueError` 并击穿 Driver。
- 修改方式：
  - `registry._resolve_resource_scope()` 将“授权型 spec 无 resolver、resolver 空、
    resolver 合同 ValueError/TypeError”包装为稳定 `AuthorizationScopeMissing`；
    registry 仍允许明确不需授权的 read-only spec 没有 resolver；
  - `_map()` 在仍持有原始 provider tool_call 时，prepare 后立即检查
    `prepared_execution_policy(call).authorization_required` 与 selectors；异常/空均生成
    raw tool failure：
    `error_code=authorization_scope_missing`,
    `source_kind=authorization_prepare`, `retriable=true`, `replan=true`；
    control call 失败时不产生 `ReactControlBatch`，而是带 raw failure 的普通
    `ReactToolBatch` 回填模型；
  - `_map()` 前置识别发生在 boundary 持久化前，因此可以保留 provider call order、
    将该 index 直接初始化为结构化 FAILED outcome；
  - 对测试构造、旧 continuation 或恢复路径中已经进入 `pending_calls` 的空 selector，
    `_permission_decision_for_call()` 返回 typed `PermissionPreparationFailure`，不抛异常；
    `_next_permission_after_progress()` 对对应 index 调用 `boundary.with_outcomes()` 写入
    `OutcomeStatus.FAILED` 和结构化 metadata，立即通过 `_save_progress()` 持久化，再循环
    查找下一 permission index；批中其他 index 保持原位、继续授权或执行；
  - `provider_call_order`、`pending_calls`、`tool_contexts` 永不重排或删除。全部 pending
    index 结算后由现有 `_resume_completed()` 按原 provider order 回填 tool results；
  - 不捕获用户 deny、Decision/Grant fence、nonce/version/expiry、scope mismatch。
- 验证：
  缺 resolver、空、受控 ValueError、prepared empty 四个 fixture；模型收到结构化
  tool result 可改用别的工具；Run 不成为 driver_failed；真实 deny 仍 fail closed。
- 依赖：Task 6。

### Task 8 — 唯一裸 capability 名规范化  [AC-IT-16]

- 改动文件：
  `backend/deskpet/tools/capabilities.py`、
  `backend/deskpet/tools/tool_search.py` 及 capability bridge tests。
- 现状：
  search 同时返回 canonical ID 和 name；describe 只接受 exact canonical ID。
- 修改方式：
  - 新增 `_canonical_deferred_id(input_id)`：含 `:` 时只做 exact；裸名按当前
    deferred refs 的 `name` 匹配；
  - 唯一匹配返回 canonical ID；0 或多个统一 `capability_denied`；
  - describe nonce 写入 canonical ID，返回 canonical ID；activate 继续 exact 比对；
  - tool schema 明确“优先原样复制 capability_search.matches[].capability_id”。
- 验证：
  full ID、唯一 bare、missing bare、ambiguous bare、nonce 用裸名/别名重放失败、
  catalog revision 变化 stale。
- 依赖：Task 1。

### Task 9 — 全目录授权资源不变量与写类工具修复  [AC-IT-17]

- 改动文件：
  `backend/deskpet/tools/registry.py`、
  `doc_tools.py`、`excel_tools.py`、`pdf_tools.py`、`ppt_tools.py`、
  `file_organize_tools.py`、`memory_tools.py`、`office_paths.py`；
  新增授权目录契约测试。
- 现状：
  多个 write/dangerous ToolSpec 没有 resolver；只添加宽泛 workspace selector 会与
  handler 实际路径漂移。
- 修改方式：
  - registry 暴露只读 audit：枚举 `dangerous=True` 或 write/desktop/shell/
    skill_install category 的 executable specs，要求 resolver identity/version；
  - 文件类工具用 custom/default `prepare` 冻结实际 read/write target；resolver 只读取冻结
    `final_params`，handler 再验证 target 位于同一 Host scope/用户已选 office root；
  - 默认输出路径在 prepare 阶段生成一次并写回 final_params，执行阶段不得重新生成；
  - `file_organize(dry_run=true)` 精确降为 read selector，`false` 为 read/write；
  - `memory_write/memory_forget` 使用逻辑 `system_change` selector，绑定 owner/profile
    和被改事实 identity，不伪装成文件路径；
  - 对生产不可达或刻意只读的 spec 明确排除理由，不用 silent allowlist。

逐工具资源模型：

| tool | frozen final params | selectors | Host containment |
|---|---|---|---|
| `doc_create` | `output_path=<write_scope>/outputs/doc-<call>.docx` | output file `write` | prepare 与 handler 均检查 write_scope |
| `doc_edit` | canonical `file_path` | same file `read,write` | file 必须位于 write_scope |
| `excel_create` | `output_path=<write_scope>/outputs/excel-<call>.xlsx` | output file `write` | 同 doc_create |
| `pdf_export` | canonical `input_path`; frozen `output_path` | input `read`; output `write` | input 位于 workspace，output 位于 write_scope |
| `ppt_create` | frozen `output_path=<write_scope>/outputs/ppt-<call>.pptx` | output `write` | `_resolve_output_path` 只用 frozen path |
| `ppt_pro` | 同上，另含 async job logical ID | output `write` + `system_change:ppt_pro:<root>:<call>` `execute` | handoff 复制 frozen path |
| `file_organize` | canonical `dir_path`; normalized `dry_run` | dry-run=`read`; mutate=`read,write` | dir/destination 均不得出 write_scope |
| `memory_write` | normalized tier/category/content hash | `system_change:memory:<owner_key>:<content_hash>` `write` | owner_key 只取 ToolExecutionContext |
| `memory_forget` | sorted IDs 或 normalized query hash | `system_change:memory:<owner_key>:forget:<target_hash>` `delete` | resolver/handler 复用同一 target digest |

  - registry 现有 `_default_prepared_targets()` 已为 doc/excel/pdf 冻结 final path；
    将其默认根从 `Path.cwd()/outputs` 改为传入的 trusted
    `write_scope_root/outputs`，resolver 读取同一 `final_params`；
  - 当前 `doc_create/doc_edit/excel_create/pdf_export` handler 会直接写最终文件，
    并没有调用 `StagedFileLifecycle.stage/commit`。本 slice 不继续把它们伪装成
    `staged_file`：将这些直接写入型 spec 明确分类为 `opaque_manual`，仍保留
    `PreparedTarget` 用于目标预留、exact authorization 与恢复时的人工 reconcile；
    后续若真正改成 staged binary writer，必须让 handler 只产出 staging artifact，
    再由 effect executor 执行 commit/rollback，不能只改 effect 标签；
  - 为避免“是否生成 PreparedTarget”和“是否真实执行 staged lifecycle”继续由同一个
    `_GRAPH_STAGED_FILE_TOOLS` 集合误表达，拆成
    `prepared_file_target_tools` 与 `true_staged_file_tools` 两个事实集合；本次四个
    Office direct writer 只属于前者；
  - `ppt_create/ppt_pro` 不强行改成 staged-file effect；仅增加 deterministic prepare
    与 resolver，保留现有 sync/accepted_async completion semantics。
- 验证：
  全目录 audit 0 缺口；每个修复工具至少一个 prepare+exact request 正例和一个模型
  越界路径负例；selector 无空值/重复且与 final_params 一致。
- 依赖：Task 7。

### Task 10 — 自动化回归、真机 E2E、架构与 testcase 收口  [AC-IT-1..18]

- 改动文件：
  `testcase/`、
  `ARCHITECTURE/AGENT_HARNESS.md`、
  `ARCHITECTURE/COMPANION_GROWTH.md`、
  `ARCHITECTURE/PROJECT_STATUS.md`、
  本 plan 的 result 文档。
- 修改方式：
  - 跑聚焦 unit/contract/integration/fault-injection，再跑 Harness/Context/
    permissions/Companion/provider dispatch 相关回归；
  - exact 回归入口至少包括：
    `harness_simplification/test_product_venue_chain.py`、
    `test_product_turn_components.py`、`test_product_turn_parity.py`、
    `test_voice_run_adapter.py`、`test_r55_admission_kernel.py`、
    `capabilities/test_authorization_runtime.py`、
    `capabilities/test_admission_task_grant.py`、
    `companion/test_turn_authority.py`、`test_growth_signals.py`、
    `harness_simplification/test_execution_projector.py`、
    `test_model_workflow_spawn.py`、`test_wi5_react_driver.py`、
    `test_tool_capability_resolver.py`；
  - 仅用 Tauri 自管 backend+Vite，确认 worktree 使用 dev Python；
  - Windows 真点击/真输入跑 S-IT-1..7；S-IT-6 记录桌面新 txt 路径、UTF-8、
    700–950 中文字符、Run completed、selector 和 DelegateRun 证据；
  - 更新 testcase 和 AC→task→code→test→result 追踪矩阵；
  - 测试全绿后同步三个 ARCHITECTURE 事实源和 PROJECT_STATUS。
- 验证：
  所有 must AC 有证据；manual case 无 WebSocket 注入/脚本回放；原始故障命令真机成功。
- 依赖：Task 2–9。

## 建议自动化命令

从 `F:\projects\deskpet` 运行：

```powershell
backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/harness_simplification/test_model_workflow_spawn.py `
  backend/tests/harness_simplification/test_wi5_react_driver.py `
  backend/tests/harness_simplification/test_product_venue_chain.py `
  backend/tests/harness_simplification/test_product_turn_components.py `
  backend/tests/harness_simplification/test_product_turn_parity.py `
  backend/tests/harness_simplification/test_voice_run_adapter.py `
  backend/tests/harness_simplification/test_r55_admission_kernel.py `
  backend/tests/harness_simplification/test_execution_projector.py `
  backend/tests/capabilities/test_authorization_runtime.py `
  backend/tests/capabilities/test_admission_task_grant.py `
  backend/tests/companion/test_turn_authority.py `
  backend/tests/companion/test_growth_signals.py `
  backend/tests/test_tool_capability_resolver.py -q
```

完成实现后用 `rg` 清零旧生产耦合；新增测试在上述真实 suite 或本 plan 明确命名的
新文件中，不因为文件重命名而跳过语义类别。

## Round 2 实现闭环（覆盖前文中较粗的描述）

### A. Targeted terminal delivery 的精确领取与竞争语义

- 在 `ExecutionStorePort` 和 SQLite UOW 增加：
  `claim_delivery_for_event(event_id, sink_key, owner_generation,
  claim_ttl_seconds) -> DeliveryRecord | None`。
- 它必须复用 `claim_delivery_tx()` 的同一 eligible predicate、owner generation fence、
  `delivery_version` CAS、attempt 递增和 lease expiry；唯一新增过滤是
  `d.event_id=? AND d.sink_kind=? AND d.sink_instance=?`，不能创建第二套 delivery row。
- `ExecutionDeliveryDispatcher.run_for_event(event_id, sink_key)` 只调用上述 claim，再复用
  与 `run_once()` 完全相同的 authorize/is_bound/deliver/settle helper。
- 前台 targeted drain 与后台 dispatcher 同时竞争时，只允许一个 claim CAS 成功；败者
  得到 `None` 或 `DeliveryClaimConflict` 后重新读取 row，不直接调用 sink。过期 claim
  仍按现有 durable policy 被恢复，旧 worker 用旧 version settle 必须失败。
- targeted drain 在 root terminal commit 后、Presenter 发送最终 UI 事件前仅尝试一次，
  不等待别的 sink；若 row 已被后台领取，下一条的 read-through 提供立即一致性。

### B. Session terminal read-through 的查询与合成契约

- 新增 frozen DTO：
  `RootTerminalContextV1(event_id, run_id, root_run_id, session_id,
  durable_seq, created_at, status, failure_code, failure_class,
  public_summary, target_id)`。
- UOW 新增
  `list_session_root_terminals(session_id, session_terminal_target_id, limit)`：
  联结 `execution_runs/events/deliveries`，要求
  `run_id=root_run_id`、event kind 为 root final、delivery sink 是
  `session_terminal/session-transcript-v1`、`target_id` 与当前
  `session_id+epoch` 完全一致、delivery 未 discarded；排序固定为
  `created_at DESC, event_id DESC`，返回后按正序合成。
- `public_summary` 与正式 sink 必须调用同一个纯函数 sanitizer；只允许稳定 failure
  code/class 和用户可理解摘要，禁止 traceback、token、内部路径和 provider secret。
- `SessionHistoryPlanner` 先读取 transcript 及其 `workflow_event_id` 集合，再合并尚未出现
  的 DTO；synthetic message 放在当前 user message 之前的独立 causal group，计入
  coverage report 和 token budget，按 event ID 去重。正式 projection 后，同一 event ID
  自然被 transcript 覆盖，不会重复进入模型上下文。
- tombstone 或 epoch 改变时 target ID 不匹配，查询和 sink 都不可见；child terminal
  因 `run_id!=root_run_id` 永不进入结果。

### C. Preference 并发承诺

- 本 slice 保证的是一个 durable decision receipt、一个 growth admission/reflection，
  不是 provider 物理调用 exactly-once。两个并发 reader 可在 receipt 提交前各自采样，
  但 `commit_preference_turn_decision_receipt` 的 first-writer 结果是唯一有效决策，
  loser 必须加载 winner receipt 后再派生 ingress。
- 因此不新增高复杂度 singleflight/lease 状态机；测试明确允许 provider call count
  大于一，同时断言 receipt、GrowthEvent 和 reflection admission 各至多一次。

### D. VerificationProfile 的保守矩阵

| Context OS task type | evidence_required | self_check_mode | 说明 |
|---|---:|---|---|
| `web_search`, `recall` | true | strict | 外部/历史事实必须有证据 |
| `task`, `code`, `command` | true | strict | 工具效果与完成声明必须对 receipt |
| `plan` | false | light | 只规划、不声称外部事实或副作用 |
| `chat`, `emotion` | false | light | 纯解释/闲聊；一旦实际调用工具仍升级 strict |
| unknown / malformed | true | strict | 保守失败，不静默降级 |

- 第一阶段 EvidenceGate 防止 evidence-required profile 在零证据时终结；第二阶段
  SelfCheck/VerifyGate 校验候选答案中的外部事实引用和 effect completion claims。
  profile 为 chat 但 ledger 出现网络/文件/side-effect receipt 时，Host 自动升级 strict。
- 模型只能看到冻结后的 profile，不能通过 tool args 或回复文本降低它。

### E. 单个 permission scope failure 的状态机

1. `_permission_decision_for_call()` 返回
   `OpenDecision | PermissionPreparationFailure`。
2. `_next_permission_after_progress()` 若收到 failure：
   - 保持 index、call ID 和 provider order 不变；
   - `with_outcomes({index: authorization_scope_missing},
     {index: OutcomeStatus.FAILED}, metadata)`；
   - `_save_progress()` 持久化新 boundary；
   - 继续寻找下一待授权 index。
3. 若批中仍有正常 executable indexes，继续走现有 ExecuteTools；全部结算后
   `_resume_completed()` 按原 provider order 将失败和成功结果一起回填模型。
4. control delegate 的 scope failure 也结算其唯一 index，不产生 DelegateRun；
   用户 deny、过期、nonce/version/scope 冲突继续走现有 authorization 状态机。

### F. 直接文件 writer 的真实 effect 契约

- 实测 `doc_create` 对显式 output path 只生成最终 `probe.docx`，没有 staging sibling；
  因而当前 handler 是 direct writer。`doc_create/doc_edit/excel_create/pdf_export`
  先按真实行为归类 `opaque_manual`，不声称拥有尚不存在的 stage/commit/reconcile。
- prepare 仍必须一次性冻结所有实际资源：输出 `PreparedTarget`、输入 read selector、
  输出 write selector；handler 只消费 frozen final params，并用同一 Host containment
  helper 再校验。timeout/进程丢失按 opaque unknown-after-handoff 处理，禁止盲目重放。
- `ppt_create/ppt_pro` 保持其现有 completion policy，但也必须冻结 output path 和 logical
  handoff resource；此 slice 不借授权修复之名重写 Office 二进制生成器。

### G. 必须新增的负向回归

- ordinary Text/Voice turn 不再产生 plan UI/state 或宽泛 root TaskGrant。
- 独立 durable workflow 的 generic Kernel admission 仍能 create → wait → resume，
  并保留自身状态展示。
- 同一 provider batch 中一个 `authorization_scope_missing`、另一个正常工具：前者 FAILED，
  后者继续执行，最终 Run 不成为 `driver_failed`。
