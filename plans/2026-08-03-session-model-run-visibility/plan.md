<!-- plan-status: finalized (plan-bs) -->
# Plan：Session 模型一致性与 Agent 运行可见性修复

> 基线 commit：`9b7fd640ccaae9cd75b79505e5d2c840246200d3`
> 验收真相：仓库根 [`acceptance.md`](../../acceptance.md) 的 AC-SRV-1～AC-SRV-8
> 发布结构：两个独立垂直切片；切片 A 先完成并验收，切片 B 才开始。
> 行为契约：[`behavior-contract.md`](./behavior-contract.md)（BC-1～BC-7 及四项既有行为变更已获批准）。
> Spike 证据：[`spike-results.md`](./spike-results.md)。

## 主要矛盾

决定成败的核心问题不是某次 GLM 402，也不是把 106 个节点换一种样式，而是：

> 同一个 Session 的模型选择、附属 Provider 调用、Context Usage 和用户运行视图没有共同消费
> 一套带身份、版本、来源和完整性证明的事实契约。

只在 `_make_live_str_llm_call()` 捕获 402，会继续出现其他附属调用绕过 Session；只在前端合并
节点，会继续受静默截断、敏感工具参数和父子因果缺失影响。本计划先统一模型路由事实，再在
不可变 execution ledger 之上建立可重建 public read model，不新增第二套执行状态机。

## 关联验收标准

- 切片 A：AC-SRV-1、AC-SRV-2、AC-SRV-3、AC-SRV-4。
- 切片 B：AC-SRV-5、AC-SRV-6、AC-SRV-7、AC-SRV-8。
- 两个切片共同覆盖 S-SRV-1～S-SRV-5、V-SRV-1～V-SRV-8。

## 最佳实践选择与 DeskPet 适配

### 1. 请求作用域路由必须显式，ContextVar 只能运输身份

- 采用：每个 Provider 调用携带类型化 `ProviderWorkloadContext`，至少包含
  `workload_class/callsite_id/purpose/session_id/root_run_id/request_id/detached`。Session/Run
  binding 是选择模型的唯一 authority；ContextVar 只把已经明确的上下文传到 provider 底层，
  不能在缺失身份时回退全局模型。
- DeskPet 适配：保留现有 `llm.resolution.resolve_session_provider_chain()` 和 Host start
  snapshot，不引入依赖注入框架；新增一个薄的路由服务和显式 callsite inventory。
- 放弃：继续让长期服务持有 `lambda: local_llm or cloud_llm`。它简单，但无法知道是哪一个
  Session，也无法证明后台错误属于哪个 root。

OpenTelemetry 的语义约定强调区分逻辑调用与物理 attempt、使用稳定语义属性，并明确警告工具
参数和结果可能包含敏感信息。本项目只借用“类型化身份与低基数字段”原则，不接入云端 tracing：
<https://opentelemetry.io/docs/specs/semconv/how-to-write-conventions/>、
<https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/>。

### 2. Provider 失败按真实影响范围熔断，不能用一个 key 粗暴处理

- 采用：`session-auxiliary` 和 `system-maintenance` 通过统一策略驱动的 Provider workload breaker。
  401/403 按 provider credential 配置、402 按 provider 账户（适配器明确模型额度时才细分 model）、
  model-not-found 按 provider+model、429 按 provider+model+workload、5xx/连接错误按 endpoint 隔离；
  冷态与 half-open 同一失效域只放一个物理探测。只有对应配置 revision 改变、用户显式 reset 或冷却
  到期后的 half-open probe 才恢复；429/5xx/连接错误尊重 `Retry-After` 并沿用有界 transient 策略。
- DeskPet 适配：这是单进程桌面应用，不引入 Redis/服务网格；breaker 状态进程内保存，转移状态
  写 privacy-safe audit。root 主调用继续使用现有 `ProviderInvocationCoordinator` 语义，不能被
  附属 breaker 反向阻断。
- 放弃：固定 sleep 后无限重试，以及把 Kimi/GLM 的错误合并进一个全局 breaker。

Circuit Breaker 的核心是依赖明显不可用时 fail fast，并按资源维度隔离；Retry 与 Circuit
Breaker 不是同一件事：<https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker>。

### 3. Durable ledger 不改写，用户视图使用可重建 read model

- 采用：execution/content trace 保持不可变；`RootOutcomeView`、`SemanticPhaseView`、
  `ToolPublicView` 都由稳定 ID、因果边和单调序号重建。重复事件幂等，旧 schema 容忍缺字段，
  但不猜测 recovered 状态。
- DeskPet 适配：已有 append-only execution ledger，不再建设新的 event store、消息队列或云端
  CQRS 服务；只增加本地 SQLite keyset reader 和纯 reducer。read model 可以按需重建，缓存不是
  authority。
- 放弃：写回/修改旧 child failed 状态；前端各组件分别推导一套终态；使用展示文字做去重键。

Event Sourcing/CQRS 的适用点是“不可变事实 + 幂等 projection”，同时官方也提醒不要为简单系统
全量引入复杂 event infrastructure。本项目已经有 ledger，因此只补 read model：
<https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing>、
<https://learn.microsoft.com/en-us/azure/architecture/patterns/cqrs>。

### 4. 工具详情使用默认拒绝的 public projection

- 采用：durable `prepared_json/outcome_json` 继续服务恢复与审计；面向用户只返回版本化、字段
  allowlist、有界长度、二次敏感文本扫描后的 `ToolPublicView`。未知工具默认只显示工具名、状态
  和“详情不可公开”，而不是递归 dump 原始 JSON。
- DeskPet 适配：复用 `TraceRedactor`、`redact_sensitive_text()` 和 ToolSpec 元数据；路径、query、
  shell preview 等逐工具声明安全字段。截断返回 `truncated/original_size/hash`，不假装完整。
- 放弃：依赖前端折叠来保护敏感内容；只按 key 名替换 token 后公开整份对象。

OWASP 建议不记录不必要的敏感数据，并对路径、连接信息、凭据等按使用边界处理：
<https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html>。

## 解剖麻雀：一次 Kimi Session 的典型链路

### 当前链路

1. `code_session_provider` 保存 `preferred_model=kimi-k3`。
2. `llm.resolution.resolve_session_provider_chain()` 只为主 Run 解析 Session chain。
3. Host start snapshot 冻结 root/child provider/model。
4. Assistant 消息提交后，`SessionDB._on_message_written(mid, text)` 触发 facts fanout；hook 只传
   message id 和正文。
5. FactExtractor 的长期 callback 调 `_make_live_str_llm_call(lambda: local_llm or cloud_llm)`，
   provider 底层只能记成 `session_id=global`，于是可能调用全局 GLM。
6. Context Usage 有 sample 时从 history 恢复；无 sample 时从全局 config 生成 stub。

### 统一后的通用模式

1. message id 从 `messages` 行读取 `session_id/root_run_id/task_scope_id/role`，形成明确
   `ProviderWorkloadContext`。
2. 每个附属 callsite 先声明 `session-auxiliary` 或 `system-maintenance`，再由
   `ProviderWorkloadRouter` 解析 provider entries；session-auxiliary 缺身份直接 skip/fail closed。
3. 显式 Session binding 失效返回 `session_provider_unavailable`，绝不改用全局 chain；未绑定
   Session 才允许 global chain。
4. Provider attempt 使用同一 workload context 写观测字段；附属错误只结算附属任务。
5. Context Usage reducer 返回带 `source/sample_id/version` 的唯一状态；UI 不再自己猜模型。

FactExtractor、query rewriter、entity extractor、GoalChecker、problem pre-analysis、memory tools、
preference interpreter、personal workflow matcher 使用同一模式。Reflection、跨 Session summarize
等维护任务显式走 `BackgroundModelPolicy`。

## 关键假设清单（已真跑）

| ID | 结果 | 方案约束 |
|----|------|----------|
| H-1 | PASS：真实账本具备 child signal → FailureReport/failure set → superseding Attempt → root final 完整链 | aggregate outcome 必须验证全链，禁止只看两个终态 |
| H-2 | PASS WITH CONTRACT CHANGE：上游可获得/回查身份，但 FactExtractor 与 fire-and-forget curator 当前入口会丢身份 | 改入口参数和任务登记；ContextVar 只运送已验证 context，不作 authority |
| H-3 | PASS：1,500 条、256/page，6 页完整有序，归约 2.408 ms | 正式实现用复合 keyset cursor + 一致性快照，并保留压力回归 |
| H-4 | PASS WITH DESIGN CHANGE：default-deny 可把 78,322 bytes 降到 3,470 bytes，但只显示命令动词仍不够直观 | ToolSpec 必须提供可冻结的声明式 `action_code + safe target label` policy；通用 redactor 只做第二层清洗 |

## 文件影响清单

| 文件 | 当前职责 | 本次改动 |
|------|----------|----------|
| `backend/llm/resolution.py` | Session provider entry 解析 | 区分未绑定与 stale 显式绑定；生产 strict fail-closed；返回绑定 provenance |
| `backend/llm/provider_registry.py` | provider 配置与运行时列表 | 增加持久 provider incarnation、config revision、原子 mutation/change event；删除不再清 Session binding |
| `backend/deskpet/execution/provider_workloads.py`（新增） | 无 | workload 类型、callsite inventory、路由服务、附属 breaker、稳定错误 |
| `backend/agent/context_messages.py` / `context_report.py` | provider attempt ContextVar 与诊断 | 增加 root/workload/callsite/detached；缺 Session 的附属调用不再归为 global 会话 |
| `backend/main.py` | 组合根与现有长期 LLM callback | 装配统一 router/factory；删除生产 `local_llm or cloud_llm` 附属绕行；Context Usage 调共享 reducer |
| `backend/deskpet/memory/migrator.py` + migrations 015～018 | state.db v22 authority | 按 v23～v26 顺序迁移 binding lifecycle、usage authority、workload audit、archive projection |
| `backend/deskpet/memory/session_db.py` | Session/message/binding/usage durable store | durable binding tombstone/epoch；immutable usage sample + materialized state；active/archive public facts |
| `backend/deskpet/agent/context_compressor.py` / `agent_loop.py` / `run_presenter.py` | 压缩执行与用量样本 | 删除全局 compactor；按 Root frozen route 压缩；写 usage lineage |
| memory/goal/companion 相关调用点 | 长期服务调用 LLM | 接受或绑定显式 workload context；系统维护使用 BackgroundModelPolicy |
| `backend/deskpet/execution/run_read_model.py`（新增） | 无 | RootOutcome、SemanticPhase、completeness 的纯 reducer 与版本化 contract |
| `backend/deskpet/execution/harness_public_read_service.py`（新增） | 无 | 每库一致读、不可变 projection manifest、public fact 合成、签名 cursor 与缺源语义 |
| `backend/deskpet/execution/provider_workload_audit.py`（新增） | 无 | main/session/maintenance 统一低敏 provenance；无 execution Run 也可审计 |
| `backend/deskpet/security/tool_public_projection.py`（新增） | 无 | ToolSpec allowlist、字段/文本脱敏、大小上限、默认拒绝 |
| `backend/deskpet/security/provider_public_projection.py`（新增） | 无 | provider input/output/policy 的 default-deny 用户视图 |
| `backend/deskpet/workflows/store/execution_uow.py` | execution ledger 与 Inspector snapshot | keyset 读取、内部流式聚合、schema v3 public snapshot；raw 详情分页 |
| `backend/deskpet/workflows/store/schema.py` | workflow.db v28 durable schema | 升级 v29，增加 presentation-only 的 Root tool spec snapshot 和结构化 block signal 扩展，不改变执行 fingerprint |
| `backend/main.py::_handle_control_ws_message` | Inspector WS 请求 | 校验 cursor/page size，返回 v3 completeness/details page |
| `tauri-app/src/types/messages.ts` | WS/snapshot 类型 | 增加 aggregate outcome、semantic phases、tool public view、cursor/truncated/source |
| `tauri-app/src/sessionModelMessages.ts` / `code-panel/ChangeModelModal.tsx` / `controlWs.ts` / `stores/sessionsStore.ts` | Session 模型选择请求与本地镜像 | 请求携带用户所见 provider incarnation/config revision，冲突时刷新而非绑定到同名新 provider |
| `harnessInspectorModel.ts` | 当前逐事件 feed/reducer | 保留 raw detail helper；停止前端推导根终态与阶段图 |
| `HarnessInspectorPanel.tsx` / `HarnessRunGraph.tsx` | 左侧运行观察 | 默认消费 backend semantic phases；按需加载详情页 |
| `MessageStreamPanel.tsx` / `MessagePanelRoot.tsx` | Session 消息流装配与 activity 消息入口 | 改为订阅共享 public snapshot store，不再从 raw harness snapshot 独立重建 activity trace |
| `AgentActivityMessage.tsx` / `DurableTaskSteps.tsx` | 右侧 child 步骤与工具详情 | 改用同一 v3 phase/tool public contract，移除 raw prepared/outcome 展示 |

## 发布切片 A：Session 模型与 Context 一致性

### Task 1 — 建立 Provider workload contract 与 strict Session resolver

- 覆盖：AC-SRV-1、AC-SRV-4、V-SRV-8。
- 改动文件：`backend/llm/resolution.py`、新增
  `backend/deskpet/execution/provider_workloads.py`、`backend/config.py`、`config.toml`。
- 现状：`resolve_session_provider_chain()` 在显式 provider stale 时回退 global；附属调用只有
  purpose，缺少 workload/callsite/root 身份。
- 修改方式：
  1. 定义 `ProviderWorkloadClass(main, session_auxiliary, system_maintenance,
     explicit_independent)`、`ProviderWorkloadContext`、`ResolvedProviderRoute` 和稳定错误
     `SessionProviderUnavailable`。有 `root_run_id` 时，route authority 是 immutable
     `execution_run_start_snapshots.run_context_json.provider_plan`；只有 Root 尚未建立的 Session 前置
     调用才解析当前 Session binding。运行中更换 Session 模型只影响下一个 Root。
  2. `resolve_session_provider_chain()` 返回 binding provenance；显式绑定 stale/disabled/model
     不可用时生产入口 fail closed，未绑定才读 global chain。用 registry entry 的 `models` 校验
     model，不发探测请求猜可用性。
  3. 建立枚举化 callsite inventory；factory 创建调用时必须提交 callsite id 和 workload class，
     未注册项构造失败。
  4. `BackgroundModelPolicy` 明确 `provider_id/model/global_chain` 模式；默认可沿用 global chain，
     但必须标成 system-maintenance，不能伪装成 Session 调用。
  5. Provider entry 新增持久 `incarnation_id`（删除后同 ID 重建也不同）与单调
     `config_revision`；Registry mutation 遵守下一条冻结的 durable commit 协议。
     `code_session_provider` 升级为 durable binding state：清空也保留 tombstone row，任何 set/clear/
     inherit 都在目标 Session 上 `binding_epoch+1`，inherit 只复制 route、不复制 source epoch，从而禁止
     ABA。binding 保存 incarnation；删除 provider 保留 binding row，UI 显示“原模型已不可用，请重新
     选择”，不再调用 `clear_bindings_for_provider()`。普通 credential/model 更新保留 incarnation，
     只增加 config revision。
  6. 冻结 Registry mutation 提交协议。所有 add/update/remove/reorder/set-enabled 和 Session bind/clear/
     inherit 共用同一 mutation lock 和 expected `(incarnation_id,config_revision)` CAS：先构造、验证不可变
     candidate；有新 secret 时写入按 incarnation/revision 命名的新 keychain slot并读回校验；再把完整 TOML
     写入同目录临时文件、flush+fsync 后 atomic replace；随后一次性切换内存 snapshot，最后才发布
     `ProviderRegistryChanged`。TOML commit 前失败则删除 staged secret并保留旧 authority；TOML commit 后
     keychain 旧 secret 的删除是可重试 GC，失败不能回滚已提交配置。remove 先 durable commit 删除 entry，
     再切内存/发事件，最后 best-effort 清 secret。Session binding 在同一 lock 内验证 incarnation/revision、
     提交 SQLite row并在返回前再验证 snapshot；CAS 不符返回 `provider_binding_conflict`，不写“成功但已
     stale”的绑定。事件永远不能在 durable commit 前发出。
     `session_provider_state`、Session hydration 和每次 mutation ack 必须返回当前 incarnation/
     config revision/binding epoch；前端所有 binding mutation（set provider、set model、clear、inherit）
     必须同时回传用户操作时所见的 `expected_binding_epoch`，涉及 provider 的请求再携带 expected
     incarnation/revision。任一字段缺失或不匹配都返回 `provider_binding_conflict`、不写 SQLite并刷新
     authoritative state；handler 不能在收到旧请求后自行读取“当前版本”冒充 expected。这样既禁止
     remove/re-add 同 ID 的 ABA，也禁止 provider 未变化时两个窗口的旧 `set_model` 覆盖新选择。
  7. 冻结启动状态机：删除模块 import 时创建 `_provider_registry` 的 authority；模块级只保留 `None`/
     类型声明，所有 resolver/设置 handler 从 lifespan `ServiceContext` 取已就绪实例。lifespan 严格执行：
     state.db v23～v26 migration → Registry 加载 TOML；旧 entry 缺 incarnation 时生成随机 UUID并原子写
     TOML，写成功才成为 authority；随后按 registry incarnation 幂等
     reconcile，写入 `provider_binding_reconcile_marker(registry_digest,completed_at)`；最后
     `ProviderRoutingReadiness` 才开放 product ingress。任一步崩溃均从最后 durable marker重试；ready前
     请求返回 `provider_routing_initializing`，不得猜 global。`ProviderWorkloadRouter.resolve()` 自身也
     必须检查 readiness，使 startup fanout/maintenance 不能绕过 HTTP ingress gate。remove/re-add same id
     因新 incarnation使旧 tombstone保持 stale。
- 验证：resolution table tests 覆盖 bound/unbound/stale/disabled/deleted/re-add same id/model missing、
  set→clear→set、inherit、旧 epoch 晚到、Root 启动后 Session 改模型；启动故障测试逐点模拟 TOML 写入/
  SQLite migrate/reconcile crash并证明 ingress及后台 router未提前开放；mutation fault tests 覆盖 keychain
  stage/TOML temp/fsync/replace/内存切换/事件发布，remove/re-add 与 bind 并发只能得到旧提交成功或稳定
  conflict；双窗口同 provider 并发 set-model 只有一个 epoch CAS 成功，旧 ack/旧请求不能覆盖；静态测试
  扫描生产 resolver旁路和模块级 Registry authority。
- 依赖：无。

### Task 2 — 把显式 Session/root 身份接入全部会话附属 LLM

- 覆盖：AC-SRV-1、AC-SRV-4、H-2。
- 改动文件：`backend/main.py`、`backend/deskpet/memory/session_db.py`、
  `backend/deskpet/memory/facts.py`、`query_rewriter.py`、`entity_extractor.py`、
  `backend/deskpet/agent/goal_checker.py`、problem pipeline 相关 adapter、
  `backend/deskpet/companion/turn_authority.py`、preferences/personal-workflow matcher、
  `backend/deskpet/tools/memory_tools.py`、对应 tests。
- 现状：message hook 只有 `(mid,text)`；大量长期 callback 在调用时无法解析 Session。
- 修改方式：
  1. 新增 `SessionDB.get_message_routing_context(message_id)`，一次读取
     `session_id/root_run_id/task_scope_id/role/projection_kind`；不改历史 message schema。
  2. fire-and-forget 前构造不可变 workload envelope并作为 task 参数传入，禁止依赖创建 task 时
     偶然复制的 ContextVar。统一协议为 `SessionAwareLLMCall(prompt, *, workload_context)`；所有服务
     的调用方法显式接受并传它，不保留 `router.bind()` 二选一。router 解析完 exact route 后，才在
     provider bottom seam 的短作用域 ContextVar 中写 attempt metadata。
  3. 逐 callsite 冻结来源与结算：FactExtractor 从 message routing row 得到 frozen root route，属于
     post-turn owned task，可晚于 root terminal 写 memory但不能改 execution terminal；query/entity/
     GoalChecker/problem pre-analysis/memory tool/preference/workflow matcher 属于 in-turn owned task，Root
     cancel 时取消；MemoryCurator 在调度时复制 frozen route，结果只写 memory；Reflection 是 detached
     system-maintenance。每项在 inventory 中声明 `owner_policy/result_sink/failure_policy`，未声明拒绝启动。
  4. `ContextCompressor` 不再在启动期持有 `_compactor_llm = local_llm or cloud_llm` 或全局窗口。
     AgentLoop 每次压缩提交当前 Root 的 frozen route，由 session-aware compaction resolver 构造 exact
     provider/model 和该模型窗口；`context_metadata_enabled` 开关不能改变模型 authority。
  5. Reflection、跨 Session summarize 明确标记 system-maintenance；用户手动对单个
     Session 发起的 summarize 仍绑定该 Session。
  6. `ProviderAttemptOptions` 只承载已经解析的 root/workload/callsite；provider bottom seam 不再
     把缺身份的 session-auxiliary 自动命名为 `global`。
- 验证：参数化 callsite contract tests；FactExtractor/GoalChecker/Preference/ContextCompressor/
  MemoryCurator 的 fire-and-forget 测试证明 Session Kimi 不调用 GLM；Root 启动后切换 UI 模型，旧
  Root 的 compaction/aux 仍用旧 frozen route，新 Root 才用新模型；maintenance 明确 detached。
- 依赖：Task 1。

### Task 3 — 附属 Provider breaker、错误隔离与 provenance

- 覆盖：AC-SRV-2、AC-SRV-4、S-SRV-4。
- 改动文件：`provider_workloads.py`、新增 `backend/deskpet/execution/provider_workload_audit.py`，
  修改 `backend/agent/context_report.py`、`backend/deskpet/memory/session_db.py` migration、
  `backend/main.py`。
- 修改方式：
  1. 先用版本化 `ProviderFailurePolicyV1` 决定失效域和恢复，不用一个复合 key处理全部错误：
     401/403=`credential` scope（provider incarnation/config revision，跨 model/purpose/workload）；
     402=`account_quota` scope（provider incarnation，适配器明确 model quota 时才加 model）；
     model-not-found=`provider+model`；429=`provider+model+workload` 并尊重 Retry-After；5xx/transport=
     endpoint scope并沿用有界 retry。每个 scope 新 revision 初态为
     `unproven`：同 key 只允许一个 leader 发出首个物理请求，其余等待同一个 admission Future；leader
     返回 401/402 时立即 open，followers 复用稳定失败且不出站；leader 成功后标记 proven/closed，
     后续健康并发不共享语义结果、各自正常出站。429/5xx/transport 走已有有界 transient policy。
  2. 状态机字段固定为 `state/opened_at/open_until/failure_count/reset_generation/leader_future`。
     402 默认 cooldown 30s并指数退避至15min，服务端 Retry-After取更大值；401/403直到 credential
     config_revision变化或用户显式 reset。首个 `now>=open_until` 请求原子转 half-open且只放一个 probe；
     `provider_breaker_retry` 控制消息只允许用户对当前选中 provider/scope将 reset_generation+1并回到
     unproven，UI额度错误卡的“我已恢复额度，重试”先调用 reset成功再创建新 Root。所有定时逻辑注入
     test clock。registry config revision change淘汰旧 credential/model state；取消 leader唤醒followers，
     不能悬挂 Future。
  3. 唯一跨 workload provenance authority 为 state.db 新表 `provider_workload_audit`：稳定 call id、
     workload/callsite/purpose、provider incarnation/model/config revision、session/root nullable、detached、
     owner policy、状态、duration、error class、breaker transition、injection ref；不设 execution FK，
     不记录 prompt/key，保留 30 天或最近 100,000 行（先按时间再按稳定 id 清理）。Root 内现有
     execution provider ledger 继续负责执行事实，但公共路由审计只读这张统一表。
     Retention由 system-maintenance task在启动后10min及每6h运行，单事务最多删1,000行，busy/失败只
     记录审计维护错误并下次重试，不阻塞provider调用。
  4. session-auxiliary 失败由拥有它的 fanout/附属服务结算为 degraded，不生成 root terminal；
     system-maintenance 失败只影响维护队列。in-turn owned task 在 Root cancel 时取消；post-turn memory
     task 可完成但只能写自身 sink/audit；所有 late completion 都受现有 Root terminal fence，不能提交
     Run outcome。
- 验证：冷态并发 20 个同 credential scope 的401跨 purpose/model也只有一个物理请求；20个402同scope
  只有一个；冷态健康 leader成功后其余请求各自执行、不误共享 prompt；test clock覆盖 cooldown/
  half-open/backoff，显式额度恢复 reset 后只放一个 probe；leader cancel、credential revision、
  delete/re-add same ID及retention busy都有并发测试；主 Kimi Run仍完成。
- 依赖：Task 1、Task 2。

### Task 4 — Context Usage 确定性 reducer 与冷恢复

- 覆盖：AC-SRV-3、AC-SRV-8、S-SRV-1。
- 改动文件：新增 `backend/deskpet/agent/context_usage.py`、
  `backend/deskpet/memory/session_db.py`、`backend/main.py`、
  `tauri-app/src/types/messages.ts`、Context usage store/components 及 tests。
- 修改方式：
  1. 冻结 state.db 迁移链并把 `memory/migrator.py` 的 target 提升到 26：
     `015_provider_binding_lifecycle_v23.sql` 增加 tombstone/incarnation/binding_epoch/reconcile marker；
     `016_context_usage_authority_v24.sql` 为 history 增加 `source_event_id/payload_hash/
     based_on_sample_id/binding_epoch/provider_id/model_id` 和唯一索引 `(session_id,source_event_id)`，新增
     每 Session 一行的
     `session_context_usage_state_v2` materialized authority及 `(session_id,state_version)` 索引；定义
     `ContextUsageStateV2`：`source(measured|compacted|binding_only)`、`sample_id`、`version`、
     binding_epoch、availability、model/window/tokens、`based_on_sample_id`、updated_at。
     `017_provider_workload_audit_v25.sql` 建 audit表/retention索引；
     `018_message_archive_projection_v26.sql` 给 archive补 root/projection/workflow元数据及索引。
     在 `migrator.py` 的显式 `MIGRATION_STEPS` 登记四步并把 target 提升到 26；SQL 文件禁止自带
     transaction control/`PRAGMA user_version`，统一 runner 校验后用一个显式
     `BEGIN IMMEDIATE ... DDL ... schema marker ... PRAGMA user_version ... COMMIT` 执行，异常必 rollback。
     未登记版本/gap 直接拒绝启动，不能落入当前非原子 `executescript()` 默认分支。fresh install、v22逐级
     upgrade、每步在 DDL/marker/user_version 前后中断重跑、失败保持上个 durable version、备份恢复均有
     migration tests，禁止多个任务争用同一版本。
  2. producer 生成不可变 source identity：provider sample 使用 frozen root + request/attempt stable id；
     compaction 使用 durable `ContextCompactedEvent.source_event_id`（root/request/compaction cycle/iteration）
     并携带 `based_on_sample_id`。canonical payload 计算 SHA-256；同 identity `INSERT DO NOTHING` 后读取
     比较 hash，不同 payload 立即 `context_usage_sample_conflict`，禁止 UPDATE 历史。
  3. `record_context_usage_sample()` 在同一个 `BEGIN IMMEDIATE` 内重读 state并归约：只让匹配当前
     Session binding_epoch 的 provider sample按 `(completed_at,source_event_id)` 前进；compaction 只有
     `based_on_sample_id == current.sample_id` 才替换 authority。晚到/旧 lineage 只保留 history。
     `UPDATE ... WHERE state_version=?` CAS 失败最多重读重试 3 次，仍冲突返回稳定错误；成功 version+1。
     provider producer 写 frozen route provider/model/window；compaction producer 修改
     `run_presenter.py`/event contract，不猜 0 window。
  4. `set_session_provider_binding()` 在同事务增加 binding_epoch；新绑定把 public authority推进为
     `binding_only`（旧 measured 留 history）。stale binding 返回 `availability=unavailable` 和明确模型，
     不显示成普通“尚无用量”；旧 Root 的晚到 sample 因 epoch 不匹配不能覆盖新模型。
  5. `context_usage_request` 只调用 SessionDB/read service；无 sample 时读取
     `code_session_provider` 并发送 `binding_only + has_measurement=false`，不构造全局 0/window。
  6. 旧库首次读取没有 materialized row 时，用 `(created_at,sample_id)` keyset 全量扫描 history（不受
     2,000 limit）重建并原子写 row；无法建立 compaction lineage 的旧样本不拼模型，回退最后一个完整
     measured 或 binding_only，并标 `legacy_incomplete=true`。
  7. `_session_context_state` 仅作为带 version 的 cache；旧/晚到 version 不能覆盖新状态。
  8. UI 对 binding-only 显示“尚无本会话用量”，折线图只把 measured/compacted 当数据点。
- 验证：provider→compaction→provider、同 timestamp、乱序、重复、重启、无样本、有 stale binding
  的 reducer tests；真机冷启动模型按钮和 Context 均为 Kimi。
- 依赖：Task 1。

### 切片 A 门禁

- 聚焦 pytest：provider resolution/workload/callsite、Context attempts、SessionDB/context usage、
  companion preferences、memory fanout。
- 真实 Kimi 正向 smoke：新 Session、重启、首轮前 Context binding-only、主调用和至少一个
  session-auxiliary 都为 Kimi。
- 负向 smoke：独立 maintenance provider 402，证明物理请求有界、主 Run 不失败。
- 通过后独立提交并更新 `ARCHITECTURE/AGENT_HARNESS.md`、`UI.md`、`PROJECT_STATUS.md`；默认启用。

## 发布切片 B：根结果与人类可理解运行视图

### Task 5 — 建立完整、可分页、可脱敏的 Inspector read boundary

- 覆盖：AC-SRV-6、AC-SRV-7、V-SRV-4、V-SRV-7、H-3、H-4。
- 改动文件：新增 `backend/deskpet/execution/run_read_model.py`、
  `backend/deskpet/execution/harness_public_read_service.py`、
  `backend/deskpet/security/tool_public_projection.py`、
  `backend/deskpet/security/provider_public_projection.py`，修改 SessionDB public query、
  `backend/deskpet/workflows/store/execution_uow.py`、`backend/main.py`、ToolSpec 元数据及 tests。
- 修改方式：
  1. `HarnessPublicReadService.create_manifest(root)` 不伪造跨库 ACID：先在 workflow.db 单个 read
     transaction 中读取该 root 涉及的 runs/events/attempts/batches/invocations/effects/failures 当前
     一致版本并立刻转成 internal facts；再在 state.db 单个 read transaction 中读取 active messages 与
     archive。`messages_archive` migration 补齐 root/projection/workflow metadata，summarizer 事务原样复制；
     旧 archive 没 root 的行标 `legacy_unscoped`，不得按时间猜归属。
     workflow.db 明确升级为 v29：`_migrate_v29()` 在一个 transaction 中建立
     `execution_run_tool_presentation_specs`、`execution_tool_public_projections` 与
     `execution_run_block_signals`、必要索引、migration marker 和
     `user_version=29`；覆盖 fresh/v28 upgrade、DDL/marker/user_version 中断重试。Root 启动时通过
     `start_commit_extensions` 原子写入 `execution_run_tool_presentation_specs`：每个已激活工具冻结完整、
     可解释的 `ToolPresentationPolicyV1`（tool name、activity kind、action code、safe arg/result JSON pointer
     allowlist、target-label template、字段/总 byte cap、redaction/interpreter version、policy hash）。它是
     presentation-only sidecar，不进入 execution intent/fingerprint，也不改变工具准入；child 读取 root
     snapshot。policy 只能使用 core 内置、永久保留的声明式 V1 interpreter，不能引用插件 Python/TS
     callable。旧 Root 无 sidecar 时固定使用永不改写的 `LegacyToolPresentationSpecV1`，未知/已删除工具只
     映射为 execute + 安全名称。
  2. 两边都产出 `PublicFactEnvelope(source, stable_id, root_run_id, workflow_event_id?, invocation_id?,
     source_seq?, created_at, kind, public_payload)`；优先用 `root_run_id + workflow_event_id/invocation_id`
     join，只有 root 的 narration 以 message id 作为独立 content fact，绝不按文案/时间猜 join。
     两个 transaction 的 `captured_at/data_version` 和 unmatched refs 写入 `ReadCutV1`；source 缺失明确
     incomplete/unknown。
  3. reducer 一次消费完整 internal fact set，生成不可变 `ProjectionManifestV1`（aggregate、phases、
     public detail rows、per-source counts/completeness、read cut），放入 TTL 120s、最多 32 个 manifest
     的 LRU cache。单 manifest 默认上限 50,000 个 public facts / 32 MiB（配置允许收紧，不能静默放宽）；
     超限返回 `projection_too_large + projection_complete=false`，不截断冒充完整；实现期压力验收至少
     覆盖 1,500 facts。服务重启/淘汰后客户端收到
     `manifest_expired` 并重新创建，不持久化第二套执行状态。
  4. snapshot response 直接返回 aggregate/phases 和 `projection_id`；details cursor 只分页已冻结 manifest，
     不恢复 reducer accumulator。复用 `companion/detail_query.py` 的 HMAC cursor 思路，绑定 schema/
     projection id/session/root/query kind/offset/page size/expiry；篡改=`invalid_cursor`，过期=
     `manifest_expired`，版本不支持=`unsupported_schema`。totals 按 `workflow_facts/content_facts/
     provider_details/tool_details` 分项返回。
  5. `ToolPublicProjectorV1` 默认拒绝；ToolSpec 逐工具声明可编译到 `ToolPresentationPolicyV1` 的 safe
     args/result fields、action code 和 target-label template，输出稳定
     `action_code + safe_target_label + status + bounded_result`。不得用“截断原始
     command”冒充摘要。所有公开字符串再过 `TraceRedactor + redact_sensitive_text`，单字段/单工具/
     单页都有 byte 上限和 truncation hash。
     工具 outcome 提交时使用 Root 冻结 policy 通过 core V1 interpreter 生成 immutable
     `ToolPublicProjectionV1`，与 effect terminal 在同一 execution transaction 写入
     `execution_tool_public_projections`；projector 异常也必须写 default-deny 的 name/status/ref row。历史
     read 优先读取这行已物化安全结果；仅对 sidecar 存在但旧版本漏写 projection 的记录用冻结 policy
     重建，绝不加载当前插件实现。这样插件升级/删除不会改变同一历史 Root 的公开详情。
  6. `ProviderPublicProjectorV1` 同样 default-deny：公共 payload 只允许调用阶段、provider/model、
     purpose、状态、耗时、低敏 usage、稳定 narration code/已发布 assistant content ref；禁止 raw
     provider `input/output/policy/system prompt/reasoning`。Inspector schema v3 也不含 raw tool
     `prepared/outcome`；技术诊断只返回受控 ref/hash。
- 验证：>1,200 条合成账本全量重建/分页无重复无遗漏；秘密、data URI、文件正文、超长 shell
  output fixtures 不泄露；未知 ToolSpec 默认拒绝详情。
- 依赖：切片 A 完成。

### Task 6 — RootOutcomeView：用因果证据表达“接管并完成”

- 覆盖：AC-SRV-5、AC-SRV-8、S-SRV-3、V-SRV-6、H-1。
- 改动文件：`run_read_model.py`、`execution_uow.py`、snapshot types/tests。
- 修改方式：
  1. 定义显示态 `running/waiting/blocked/completed/completed_with_recovery/failed/cancelled/unknown`；它不是
     execution run status，不能写回旧 ledger。
  2. `completed_with_recovery` 必须同时存在：child terminal failed/cancelled、绑定该 child 的
     FailureReport、由该 failure set 触发或 supersede 的后续 Attempt、时间/序号更晚的 root
     completed final。缺一项就只显示 root completed，并在详情标注 child warning。
  3. 终态优先级：root cancelled > root completed(with causal recovery) >
     `(root failed + valid RunBlockSignalV1)` blocked > root failed > waiting > running。`waiting` 必须有未解决
     human/admission boundary；`blocked` 必须有 terminal/明确不可恢复
     failure 且没有 pending boundary/replacement attempt；`RootBlockReasonV1` 只允许
     `provider_binding_unavailable/capability_unavailable/external_dependency_unavailable/
     workspace_unavailable` 四类结构化原因，禁止匹配错误文案推断 blocked；晚到 child/effect 不能越过
     root terminal fence。
  4. 新增 presentation-only `RunBlockSignalV1(reason_code,evidence_refs,producer,created_event_id)`，由集中
     `RunBlockReporter` 作为 terminal commit extension 原子写入：Session/frozen route resolver 只产
     provider-binding；capability admission 只产 capability；已穷尽 retry 且无 replacement 的外部依赖只
     产 external-dependency；workspace resolution 只产 workspace。没有该结构化 signal 就不能显示
     blocked；旧失败显示 failed/unknown。signal 只允许与 root failed 同事务提交，不能与 completed/
     cancelled 并存，不改变原 execution status/fingerprint。
     对当前发生在 Root 创建前的 provider binding/workspace/capability preflight，新增
     `start_blocked_root()`：先分配 root id，在一个 execution transaction 中写最小 start snapshot（用户
     request identity、requested binding provenance、`route_availability=unavailable`，没有 provider
     invocation）、failed terminal 和对应 block signal；随后正常 Session projection 才能显示 blocked。
     外部依赖在已启动 Root 内仍走普通 terminal extension。未形成 durable Root 的 malformed/unauthorized
     请求只返回协议错误，不伪造运行图节点。
  5. 返回 `evidence_refs` 和 `explanation_code`，前端文案只翻译稳定 code。
- 验证：状态真值表含 `failed+block=blocked`、`failed-no-block=failed` 及 block 与 completed/cancelled
  冲突拒绝；四类 block producer、三类 preflight `start_blocked_root()` 各一条 contract test；重复/乱序、无 FailureReport、无
  superseding Attempt、多 child、一 child 失败另一 child 成功、root cancel 后晚到完成；消息文本含
  “blocked/不可用”但无 signal 时不得误判。
- 依赖：Task 5。

### Task 7 — SemanticPhaseView：从完整事实生成分层阶段

- 覆盖：AC-SRV-6、AC-SRV-8、S-SRV-2、V-SRV-1/2/3/5。
- 改动文件：`run_read_model.py`、Inspector snapshot tests。
- 修改方式：
  1. 顶层固定低基数 taxonomy：理解需求、准备/规划、委派、执行、验证与修复、等待用户、交付；
     只返回实际出现的阶段，最多 8 个顶层阶段。phase id 固定为
     `hash(contract_version, root_run_id, taxonomy)`，同一 Root 每类最多一个；不能把 causal anchor 放进
     phase id 造成阶段重复。phase 内事实按 stable id 去重、按 `CausalOrderKeyV1` 排序。
     manifest 构建时先生成 `CausalOrderKeyV1`：以显式 parent/child、workflow event、plan version、Attempt
     supersedes/trigger failure set、provider batch→invocation→effect、run terminal 边组成 DAG；稳定拓扑
     排序用 `(topological_depth,source_rank,kind_rank,stable_id)` 打破并列。`source_seq` 只增加同源边；
     跨库 unlinked narration 是旁支证据，不能推进 phase/current status。发现 cycle 时按 stable id 断开并
     标 `projection_complete=false + causal_cycle`，不得使用当前时间或仅 wall-clock 决胜。
  2. 冻结 `SemanticPhaseMappingV1`，按以下优先级只命中第一条：
     - root final/cancel → 交付；未解决 human/admission boundary → 等待用户；
     - FailureReport/failure set/replacement attempt/failed child → 验证与修复；
     - child command/accepted/workflow accepted/child terminal → 委派；
     - ToolSpec 必填 `activity_kind=inspect|plan|mutate|verify|communicate|wait`：inspect 且位于首次
       mutation 前 → 理解需求，plan → 准备/规划，mutate/communicate → 执行，verify/test/build/health
       或处于 failure/plan_version>1 之后 → 验证与修复，wait → 等待用户；未知工具 → 执行并标
       `mapping_reason=legacy_unknown_tool`；
     - workflow progress 的显式 step/phase hint 映射到上述枚举，未知 hint → 执行；
     - provider preanalysis/classifier → 准备/规划，首次 main effect 前 → 理解需求，后续 main → 执行；
       detached system-maintenance 不进入 Session 阶段；user request/content → 理解需求。
     durable workflow 的 `workflow_step_id` 只作为阶段内子步骤 authority，不生成额外顶层 taxonomy。
     工具分类必须读取 Root 冻结的 `execution_run_tool_presentation_specs`，禁止读取当前 live ToolSpec；旧 Root 只读
     `LegacyToolPresentationSpecV1`。
  3. phase 状态固定为 `running/waiting/completed/completed_with_recovery/failed/cancelled/unknown`，真值表
     固定如下：projection 不完整且缺少本阶段终态证据=`unknown`；完整且无 Root terminal/boundary 时，仅
     order key 最大的阶段=`running`、更早阶段=`completed`；未解决 boundary 使等待用户=`waiting`；Root
     cancelled 使当前阶段=`cancelled`、更早阶段不回滚；Root failed 使当前阶段=`failed`；Root completed
     使当前阶段及交付=`completed`；验证与修复只有完整 failure→replacement→success 因果链才是
     `completed_with_recovery`。晚到的早期事实只追加证据，不能把当前阶段回滚；failure 保留显式子节点。
     reducer 对重复、乱序、缺页产生确定结果，不能读当前时间决定状态。
  4. phase 内保存 ordered fact/tool/child refs，不用标题文本去重。测试/校验后的修改仍归“验证与修复”，
     避免检查—修改反复产生几十个顶层节点。未知映射必须保留 `mapping_reason`，不得静默丢事实。
  5. 有显式 workflow plan 时显示 `当前 n/m 步`；通用 ReAct 未知总数时显示“阶段 n”，不伪造
     计划总步数。projection 不完整时显示加载状态。
- 验证：366-record 真实 Godot recovery fixture 精确聚合为 6 个实际出现的顶层阶段；另保留
  106-record 合成 taxonomy 单元覆盖，但不再把它当作真实 Root oracle；同一事件重放不新增；新增晚到页后阶段
  单调更新；table-driven tests 覆盖跨库无 seq、causal cycle、ToolSpec 升级/删除、重启冷恢复、公开
  narration 缺失，均得到 byte-stable 结果和诚实 fallback。
- 依赖：Task 5、Task 6。

### Task 8 — 前端只消费统一 public read model

- 覆盖：AC-SRV-5、AC-SRV-6、AC-SRV-7、AC-SRV-8。
- 改动文件：`tauri-app/src/types/messages.ts`、`MessageStreamPanel.tsx`、`MessagePanelRoot.tsx`、
  `HarnessInspectorPanel.tsx`、
  `HarnessRunGraph.tsx`、`harnessInspectorModel.ts`、`AgentActivityMessage.tsx`、
  `DurableTaskSteps.tsx`、CSS 和对应 Vitest。
- 修改方式：
  1. 运行图直接渲染 `semantic_phases`；删除 `buildFriendlyGraphNodes(activityFeed)` 作为默认图源，
     activity feed 只留“步骤详情/技术记录”。
  2. 顶部使用 `aggregate_outcome`，显示“子任务失败，主 Agent 已接管并完成”；child failed 在所属
     phase 展开后显示，不把整体染成失败。
  3. phase 默认仅当前阶段展开；每阶段可独立折叠。工具一行显示名称/动作/状态/耗时，第二层展开
     public input，结果再单独展开；不得读取 raw details。
  4. 冻结 WS 协议：`harness_inspector_snapshot_request` 每 connection/root 只有一个后台 task；前端只在
     收到 response/error 后延迟 1.2s 发下一次，不用 interval 堆请求。新 snapshot 或
     `harness_inspector_cancel` 取消旧 snapshot task；服务端 task registry 按 `(connection_id,
     request_kind,root_id)` 管理，query loop 检查 cancellation，`finally` 关闭两个 read transaction并
     清 registry。details request 使用独立 request kind/slot和 manifest cursor，不被 snapshot refresh
     取消。Session/Run切换与 WS close取消旧 root全部task；响应回显 request id，客户端仍丢弃竞态晚
     响应。terminal 且 projection_complete 后停止 snapshot轮询；历史 detail可继续加载。
  5. 新增按 `(session_id,root_run_id)` 索引的 `HarnessPublicSnapshotStore`，左侧运行图、
     `MessageStreamPanel`、右侧 durable steps 全部只订阅这一个 v3 store 和同一 status translator；组件
     只维护展开/收起等视图状态，不保存第二份业务 reducer。`MessageStreamPanel` 删除从 raw snapshot 调
     `buildAgentActivityTrace()` 的生产路径，Agent activity 消息直接由 semantic phase/tool public view
     生成；同一 stable id 在左右只各渲染一次，不因刷新重复。
  6. v2/未知 schema 只能显示最小安全 fallback（工具公开名称、unknown 状态、不可用提示）；不得回退读取
     raw args/result/prepared/outcome/provider input/output。生产构建增加 raw-field import/访问静态禁用测试，
     contract test 构造含秘密的 raw fixture并证明 MessageStreamPanel 和 Inspector DOM 均不可见。
- 验证：React tests 覆盖 completed_with_recovery、分页、truncated、工具两层展开、取消晚到、两个
  Session 并发、同一 stable id 不重复、旧 schema 安全 fallback；生产 consumer test 证明左右两路都只
  从 shared store 读取，无横向溢出和重卡片。
- 依赖：Task 5～7。

### Task 9 — 跨层验收、旧数据兼容与架构回写

- 覆盖：AC-SRV-1～8、S-SRV-1～5、DoD。
- 改动文件：跨层 e2e 脚本、testcase 索引、`ARCHITECTURE/AGENT_HARNESS.md`、`UI.md`、
  `PROJECT_STATUS.md`、本 plan 验证记录。
- 修改方式：
  1. 定稿后、实现前把 `source-request.md`、用户批准的 BC-1～BC-7、BC-STALE-001、
     BC-PUBLIC-002、BC-GRAPH-003、BC-CONTEXT-004、`acceptance.md`
     hash 与外部 black-box testcase 逐文件 hash 写入 gate init manifest；旧 stale-fallback testcase
     只有绑定批准 artifact 才能反转，禁止失败后迁就实现改 expected。
  2. 新增可复现 fixture：`backend/scripts/generate_harness_public_fixture.py` 只读真实 root并只导出结构
     identity/status/order/tool family，所有内容替换为合成安全文本；提交
     `backend/tests/fixtures/harness_public_read/godot_recovery_v1.json` 与 `provenance.json`（source root
     hash、tested code SHA、read cut、按 source/kind 的计数、generator version、fixture SHA-256）。A2
     只读复跑已纠正旧 UI 数字误用，固定预期为：366 个完整 public facts、6 个实际出现的顶层阶段、
     29 个唯一逻辑工具（其中 23 个 shell）、1 条 child failure→FailureReport/failure set→replacement
     Attempt→root terminal 的 completed_with_recovery 完整链，且 projection_complete=true；generator重复运行
     byte-identical，secret scanner为零。若真实源结构变化，先更新 generator并重新批准 oracle，不能手改。
  3. 新增仅 `DESKPET_DEV_MODE=1` 可启用的 `ProviderFaultScriptV1` 文件注入：规则按可信 workload
     identity + callsite + purpose + occurrence 匹配并 consume-once。Session auxiliary 绑定实际 root；child
     main 绑定 `child_run_id` correlation 且保留 `bound_root_id=NULL`；detached maintenance 绑定该次
     request correlation 且 session/root 均为 NULL。支持 401/402/429/child provider failure，audit写
     injection ref、correlation hash 与适用的 bound identity；测试结束精确删除规则并证明无 active
     injection。生产模式发现该 env 直接拒绝加载。正向 Kimi run 不加载 fault script；负向 run 单独
     启动并按上述身份类型核对 audit，禁止笼统要求所有故障都绑定 root。
  4. 自动化故障矩阵：provider 401/402/429、stale binding、child fail→replan→root complete、root
     cancel+late event、>1,200 records、敏感/超长工具 payload、schema v2 history。
  5. 真机按 S-SRV-1～5 点击/输入；至少两个独立 LLM root，其中一个 Session ≥10 轮历史；先跑
     2～5 个正向 value smoke，再跑完整矩阵。
  6. 验证窗口、backend `/health`、Vite、resident worker 和真实 provider；不以 WebSocket 直注
     替代 UI。故障注入只用于对应负向 case。
  7. 测试通过后同交付更新架构事实源；两个切片分别在干净 HEAD 复验，分别提交，不混入 Godot
     项目修复或其他工作树内容。
- 依赖：Task 1～8。

## AC 追溯表

| AC | 主要任务 | 自动化证据 | 真机证据 |
|----|----------|------------|----------|
| AC-SRV-1 | T1/T2 | strict resolver + callsite matrix | S-SRV-1/S-SRV-2 provider audit |
| AC-SRV-2 | T3 | breaker concurrency/fault tests | S-SRV-4 |
| AC-SRV-3 | T4 | reducer/restart/ordering tests | S-SRV-1 |
| AC-SRV-4 | T1/T2/T3 | inventory/static gate + provenance | S-SRV-1/S-SRV-4 logs |
| AC-SRV-5 | T6/T8 | causal truth table | S-SRV-3 |
| AC-SRV-6 | T5/T7/T8 | >1,200 合成长账本压力测试 + 366-fact 真实派生 recovery fixture | S-SRV-2 |
| AC-SRV-7 | T5/T8 | redaction/limit/unknown-tool tests | S-SRV-2 tool expansion |
| AC-SRV-8 | T4/T6/T7/T8 | concurrent/cancel/late-event tests | S-SRV-5 |

## 明确不做

- 不修改 `F:\projects\jurassic-park-escape`；Godot 窗口、玩法和 ObjectDB leak 另立验收。
- 不公开隐藏 reasoning，不用 LLM 生成“思考过程”。
- 不改写历史 child failed/cancelled ledger，不把 read model 状态写回 execution terminal。
- 不引入 LangGraph、Kafka、Redis、云端 tracing 或新的跨进程队列。
- 不在前端依据文案猜模型、恢复因果或工具安全字段。
- 不用一个大提交同时交付切片 A 和 B。

## 计划出口

- 两个 release slice 各自 ≤3 个高风险面、各自可回滚。
- H-1～H-4 已真跑，命令与实际输出见 `spike-results.md`；H-2/H-4 已据结果修改入口契约与公开投影设计。
- 本文件已完成七轮挑战，关键假设均已真跑，并于 2026-08-03 经用户 review 批准定稿；批准证据见
  [`approval.md`](./approval.md)。
