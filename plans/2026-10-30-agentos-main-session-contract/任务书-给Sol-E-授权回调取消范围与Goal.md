# 任务书 E（给 gpt-6.1-sol / Codex）：宿主授权回调、取消范围与屏障、owner 代次、关闭有活拒绝、恢复报告、Goal

日期：2026-10-31。发包人：Claude 会话（SimplaHarness）。审核人：同一 Claude 会话。
你只看这一份任务书和它指向的文件就够了。**在任务书 D 合并之后开工**，基线以发包时的 main 为准。

## 1. 背景（一段话）

Simple Harness SDK 的会话层已经有：父子会话、委派到子会话、子结果回父、排队输入处理、结果驱动的自动汇总、命令幂等回执。两个接入方（AgentOS、AIPhone）要的最后一块"秩序"是本任务书：①宿主授权回调（SDK 在受理、派发、每个连接器效果前问宿主能不能做，异常等于不允许）；②取消的范围与屏障（取消一个 Goal / 一条委派 / 整棵会话树，屏障后不派新活，屏障前已交出去的如实标状态）；③owner 代次对外暴露、第二实例报 `owner_conflict`；④关闭有活时拒绝；⑤恢复报告；⑥Goal（宿主登记的有界目标，有版本、完成条件、授权引用，收口只核结构事实）。不做运行期加工具（后续）、不做多当事人宿主。

## 2. 仓库与基线

- 仓库：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness`（私有，不推公开远端）。基线：发包时的 `main`。
- 工作树：`git worktree add ../simple_harness-sol-E -b sol/E-authorization-goal main`。提交到分支，不推送、不合并。
- 测试：`sdk/simple-harness-sdk/` 下 `uv run --frozen python -m pytest <文件> -q -p no:cacheprovider`；改源码后真宿主测试前先 `uv run --frozen python scripts/build/taskgraph_manifest.py generate --upstream ../../.local-test-evidence/2026-10-05/f2-upstream/evidence.json`（清单文件不提交）。不跑全量。
- 先读：`host/session.py`、`sessions.py`、`delegation.py`、`inbox.py`、`session_tree.py`、`continuation.py`、`host/host.py`（`auto_mode`、`decide`、`approvals`、`planning_authorization`、`cancel`、`instance_lock` 的用法、`_check_allowed_actions`）、`host/instance_lock.py`、`runtime/connectors.py`（`Connector.execute(idempotency_key)` / `lookup`、`Receipt`）、`host/settings.py`（`max_action_level`）；测试 `test_session_tree.py`、`test_continuation.py`、`test_host_connectors.py`、`test_host_recovery.py`。
- 设计依据：计划文件第 3 节"阶段 3"、第 2 节不变量 I2/I5/I6/I7/I8；AgentOS 合同原文第 5.2（`put_goal/finish_goal`、`cancel`、`close/archive`、`start/recover`）、5.3（`GoalSpec`、`AuthorizationRequest/Decision`、`RecoveryReport`）、5.4 错误码、第 7 节（取消屏障、恢复、效果对账）。都在同目录。

## 3. 车道（只能改这些文件）

- `src/agent_orchestrator/host/session.py`、`sessions.py`、`delegation.py`、`inbox.py`、`session_tree.py`、`continuation.py`
- 新建 `src/agent_orchestrator/host/authorization.py`、`src/agent_orchestrator/host/goals.py`、`src/agent_orchestrator/host/recovery_report.py`
- `src/agent_orchestrator/host/host.py`：只改 `auto_mode` 相关的判定入口（把它包成默认的 `AuthorizationPort` 实现）与 `instance_lock` 调用处（暴露 owner 代次）；不改任务层调用
- `src/agent_orchestrator/host/instance_lock.py`：加代次
- `src/agent_orchestrator/host/settings.py`：加字段
- `src/agent_orchestrator/host/session_chat.py`：`COMMAND_SCHEMAS` 只许加 `cancel`（范围取消）一个命令；提示词只追加一段
- `src/agent_orchestrator/host/__init__.py`、`public_api.py` 只加导出
- 新建测试 `tests/orchestrator/host/test_authorization_port.py`、`test_cancel_scope.py`、`test_goals.py`、`test_owner_and_close.py`；现有测试只许改与新字段直接相关的断言，逐条列在记录里
- `docs/orchestration-host.md` 加"授权回调、取消范围、Goal、恢复报告"一节

别的不碰。任务层、工具层、连接器协议不动。

## 4. 要做的事

### 4.1 授权回调（`authorization.py`）

- `AuthorizationPort` 协议：`async def decide(request: AuthorizationRequest) -> AuthorizationDecision`。
- `AuthorizationRequest` 由 SDK 注入可信字段：`principal_id, tenant_id, root_session_id, session_id, goal_ref?, delegation_id?, action`（`accept_input` / `delegate` / `create_child` / `connector_effect` / `continuation`）、`target_ref, params_hash, grant_ref?, policy_revision?, operation_key?`。模型参数里的身份字段不算数。
- `AuthorizationDecision`：`allow(constraints: Mapping = {}, revision: str = "", expires_at: float | None = None)` / `needs_approval(request_ref)` / `deny(reason)`。
- 调用点三处：命令受理（`send`、`delegate_task`、`create_child`、`child_send`）、实际派发（委派建 Mission 前、续办入队前）、每个连接器效果前（任务里的动作审批已有一条链；这里只接**会话层**发起的效果，与任务书 F 的动作工具衔接——F 未做前这一处只留接口与测试替身）。
- **异常或超时（≤5 秒）等于暂不允许**（`authorization_required`，可重试）；不默许。
- 缓存：`allow` 带 `expires_at` 的可缓存到期；宿主调用 `session.revoke_authorization(grant_ref)` 后缓存立即失效，已入队未执行的工作在执行前重新核验（`authorization_revoked`）。
- 默认实现 `AutoModeAuthorization`：`auto_mode=True` 时全部 allow；`auto_mode=False` 时 `needs_approval`，走现有人工卡片（`needs_response` 事件、`respond()`）。现有 `auto_mode` 语义不变。
- `needs_approval` 的审批绑定目标、参数摘要、版本：重复批准返回原结果；错误主体、过期、已取消拒绝（现有 `respond()` 的版本校验复用）。

### 4.2 取消范围与屏障

- `session.cancel(target_kind, target_id, *, command_id, expected_version?, reason="")`，`target_kind ∈ {goal, delegation, session_tree}`。
- 走命令幂等（`Commands.run`）。立即持久 `cancel_requested`（状态与屏障序号），事件 `cancel_requested`；之后收敛：各目标到终态后事件 `cancelled`（或如实 `failed` / `unknown`），不把"收到取消"伪装成"效果已撤销"。
- 屏障：屏障序号之后不再派新活（委派、续办入队、排队输入处理都要看屏障）；屏障前已交给外部的效果如实标 `handed_off / completed / unknown`。
- 迟到结果（屏障后到的子结果）可审计（事件 `result_received` 带 `after_cancel=True`），不触发已取消 Goal 的续办。
- `session.interrupt()` 只停当前回合，不隐式取消任何子会话/Mission（现状，加测试钉住）。
- 模型可见命令 `cancel {target_kind, target_id, reason?}`（身份由运行时注入），只能取消本树内的目标。

### 4.3 owner 代次与第二实例

- `instance_lock` 加单调递增的 `owner_generation`（持久在锁文件旁），宿主打开时 +1；`host.status()` 与 `session.capabilities()` 暴露。
- 第二个进程打开同一数据根：公开入口（`OrchestrationHost.open` / `Session.open`）报 `SessionError("owner_conflict")`（保留现有锁行为，只换成合同错误码并带代次）。
- 旧 owner 失锁后不得再提交：锁丢失时宿主进入拒绝新工作状态（现有 `_refuse_new_work` 机制），事件可见。

### 4.4 关闭有活拒绝与归档

- `session.close()` 加参数 `force: bool = False`：有未终态委派或未消费结果（`applied_turn_id is None` 且未被排除）时报 `active_work`，不悄悄终止子任务；`force=True` 保留现有行为（只释放资源、任务继续）。
- `session.archive(*, command_id)`：只允许在无未终态委派、无未消费结果、无未收口 Goal 时；归档后 `open_session` 报 `not_found_or_forbidden`（可带 `archived=True`）；归档不删数据。

### 4.5 恢复报告

- `OrchestrationHost.open` / `open_session` 后可取 `session.recovery_report()`：`{store_version, owner_generation, resumed_refs[], waiting_approval_refs[], held_refs[], unknown_effect_refs[], rejected_refs[], reasons{}}`。来自账本与任务库事实，不调模型。

### 4.6 Goal（`goals.py`）

- `put_goal(spec, *, command_id, expected_version)`：`GoalSpec = {goal_id?, objective, completion_criteria[≥1], stop_conditions[], budget{正整数}, grant_ref?, policy_revision?}`；`expected_version=0` 建，否则改；改版本保留旧版本号历史；终态 Goal 不可改（`stale_version`）。
- 每个根会话同时只许一个未收口 Goal（第二个报 `active_work`）。
- 委派行记 `goal_ref = {goal_id, goal_version}`（建委派时在用的 Goal）；子会话 `children` 行的 `goal_ref` 同理。
- `finish_goal(goal_id, outcome, *, command_id, expected_version, basis="")`：只核**结构事实**——名下委派全部终态、无未应用的结果通知（或被可信宿主以明确原因排除：`exclude_result(event_id, reason, command_id)`）、无待答请求、无 unknown 效果；`succeeded` 还要求 `basis` 非空。**好不好由审阅员判，这里不判语义。** 状态 `active|waiting|cancel_requested|succeeded|failed|cancelled`；有 unknown 效果保持 `waiting` 并暴露原因。
- Goal 更新后，旧版本启动的 Mission 保留冻结输入；旧结果归属旧版本（事件与行带 `goal_version`）。
- Goal 不给模型命令；只给宿主接口。能力发现：`capabilities` 加 `goals`、`authorization_port`、`cancel_scope`、`recovery_report`、`archive`；`unsupported` 只剩 `steer_running_mission`。

### 4.7 效果对账（接口预备）

- `continuation.py` / `delegation.py` 里会话层发起的效果统一经 `EffectLedger`（新表 `effects`）：`operation_key, params_hash, state: claimed|committed|not_committed|pending|unknown, receipt_ref`。执行前 claim，提交后查 `lookup`；同 key 同参数重试最多一个真实效果；不同参数拒绝；缺回执且结果未知保留 `unknown`，不自动重放。本任务书只做账本与对账规则（用测试替身连接器验证），真正把动作暴露给主会话是任务书 F。

## 5. 不变量（每条至少一个测试）

| # | 不变量 |
|---|---|
| A1 | 授权回调异常/超时等于不允许；`deny` 零效果；`needs_approval` 出卡片、批准后最多执行一次；重复批准返回原结果；旧版批准不授权新参数 |
| A2 | 撤销后已入队未执行的工作在执行前被拒（`authorization_revoked`） |
| I7 | 屏障后无新派发；屏障前已交接效果如实标状态；迟到结果不续办已取消 Goal；`interrupt` 不取消任何任务 |
| O1 | 第二实例 `owner_conflict`，带代次；旧 owner 失锁后不再提交 |
| C1 | 有活 `close()` 报 `active_work`；`force` 保留旧行为；收口后 `archive` 成功，之后打开报 `not_found_or_forbidden` |
| R1 | 恢复报告字段齐全且与账本事实一致，不调模型 |
| G1 | Goal 版本串行；终态不可改；一个根会话一个未收口 Goal；`finish_goal(succeeded)` 在有未终态委派 / 未应用结果 / 待答 / unknown 效果时报 `active_work`；`basis` 空报 `invalid_argument` |
| E1 | 同 `operation_key` 同参数最多一个真实效果；不同参数拒绝；回执缺失且未知保持 `unknown` 不重放 |

## 6. 测试要求

先红后绿，至少两次提交；`FakeHost` 做账本层，真宿主用 `ScriptedProvider`；连接器用测试替身（可控 `execute` / `lookup`，可注入崩溃）；不跑全量回归。

## 7. 完成标准

- 第 6 节测试全绿；`test_session_tree.py`、`test_continuation.py`、`test_session.py`、`test_session_replay.py`、`test_host_sessions.py`、`test_host_connectors.py`、`test_host_recovery.py` 全绿（真宿主前先重生成清单）；`tests/test_public_api.py` 绿；改到的文件 `ruff check` / `ruff format --check` 绿。
- 记录 `plans/2026-10-30-agentos-main-session-contract/记录-任务书E-Sol.md`：改了什么、测试命令与通过数、没做的、拿不准的、分支与提交号。中文。

## 8. 禁区

- 不改任务层的审批链、不改连接器协议、不改工具层。
- 命令表只加 `cancel`；不改池编号；提示词只追加一段。
- 不加兼容分支、不留旧路径；注释中文写"为什么"，不写代号。
- 不为让测试绿改表或改断言；不提交部署清单。
- 不推送、不合并、不跑全量回归；不碰密钥文件。

## 9. 有问题怎么办

写进记录"拿不准的"，按最合理的做法先做完，不停下等。

## 10. 任务书 D（异步受理与自动汇总）合并后的补充说明（2026-10-31 审核人追加）

发包基线已含 D 与 D2 的改动，以下几点与本任务书直接相关：

- **续办的"能不能做"只有一个决定点。** D 已有 `continuation.py` 的 `ContinuationPolicy.decide(context) → enqueue / hold / deny`（默认 enqueue，异常或超时 5 秒按 hold）。本任务书 §4.1 列的调用点里**去掉**"续办入队前问 `AuthorizationPort`"这一处，`AuthorizationRequest.action` 不要 `continuation` 这个值——同一件事不留两条路径。宿主要管续办就实现 `ContinuationPolicy`。
- **取消屏障要落在 D 的队列机制上。** 排队输入统一在 `Session._run_inputs` 里消费，它**先拿 `_send_lock`、再重读队列行状态**才标 `running` 和扣额；屏障检查放在这同一处（拿锁后、标 running 前），屏障后的 `result_event`（续办）和 `directive`（子会话追加输入）都不再起回合，状态记 `cancelled` 并发事件。`Continuation.tick()` 入队前也看屏障：被取消 Goal 名下的迟到结果记 `result_received(after_cancel=True)`，不入队。
- **D 的状态与表**：`queued_inputs`（`kind ∈ user_message / directive / result_event`，`state ∈ queued / running / done / failed`，`source`，`receipt`）、`continuation_results`（`pending / queued / applied / hold / deny / waiting`）、`continuation_usage`（`reserved_model_calls`、`model_calls`、`unknown`、`epoch`）、`voice_deliveries`（`queued / submitted / played`）。恢复报告（§4.5）的 `resumed_refs / held_refs / unknown_effect_refs` 要把这些算进去：`running` 的排队输入算 resumed，`hold` 的续办算 held，`voice_deliveries.submitted` 且绑定已不在的算 unknown。
- **关闭有活拒绝（§4.4）的"活"**：未终态委派、未消费结果（`applied_turn_id is None`）之外，加上 `queued / running` 的排队输入与 `pending / queued` 的续办。`force=True` 时 D 现有的 `close()` 行为不变（取消 `_input_task`，排队行留 `running`，重开接着跑）。
- **用量规则**：续办用量按"可多算不可少算"结算（`Continuation.settle_usage`）；效果账本 `effects` 的回执哈希与它无关，但 Goal 的 `budget` 若要算模型调用数，取 `continuation_usage` 与会话回合的保守值，不另起计数。
- **会话账本**：`SessionLedger` 已有 `delete(namespace, key)`；`meta.configuration` 只冻结 `version` 与 `policy`（不含工具名单）；`capabilities()` 已含 `catalog_version`、`async_accept`、`queued_input_execution`、`continuation`。本任务书 §4.6 加能力时放同一个字典，`unsupported` 只剩 `steer_running_mission`。
- **语音**：`VoiceBinding.announce(text, *, priority, source, input_id)` 已带结构化来源；取消一个 Goal 时，它名下已 `submitted` 的语音播报不撤回，只在 `voice_deliveries` 上标 `cancel_requested`，播完照常记 `played`（效果如实）。
- **提示词与命令**：`SESSION_INSTRUCTIONS` 已有 ToolSearch 与续办两段，本任务书只再追加 `cancel` 命令的一句；`COMMAND_SCHEMAS` 只加 `cancel`。
