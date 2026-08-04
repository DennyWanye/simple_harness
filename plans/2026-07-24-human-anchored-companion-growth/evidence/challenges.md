# 计划挑战记录

> 日期：2026-07-24  
> 规则：每轮独立、串行；上一轮问题修完并整体重校准后，才开始下一轮。

## 第 1 轮

结论：`VERDICT: FAIL`

主要问题：

1. durable Run 首个 Driver boundary 前没有完整恢复事实；
2. 把一个 Run 错当成一次 provider operation，无法表达多轮/fallback/retry；
3. terminal delivery 没有在 Run start 冻结；
4. profile identity/generation、Loader/Matcher 与通知 owner fence 不完整；
5. genesis、评测逐例恢复、遗忘与新旧 authority cutover 缺少可执行契约；
6. 评测授权和激活确认没有完全分离。

处理结果：

- 增加 `RunStartSnapshot`、per-operation provider claim/outcome、start-frozen delivery；
- 增加 profile generation、逐例 lease、genesis reservation、双授权门；
- 增加 RevocationBarrier、GrowthAuthority 单指针 cutover、SessionDB excluded projection。

## 第 2 轮

结论：`VERDICT: FAIL`

已确认第 1 轮的主要修复已进入计划，但发现更根本的结构漂移：

1. master 已有 `CapabilityStore/CapabilityPackManager/ToolRegistry/CapabilityHub`，原计划却在
   `companion.db` 再建版本与 active pointer，形成双权威；
2. 现有 capability binding 没有对应人类的 owner-generation；
3. 通用 Auto 可能绕过成长专用的高风险确认；
4. 架构基线仍按旧提交和单 worktree 描述；
5. 没有明确依赖正在实施的通用 Capability 计划。

处理结果：

- Companion 只保留证据、候选包、评测、决策与 activation saga；
- installed version/active binding 统一交给 CapabilityStore，执行统一交给 ToolRegistry；
- 引入 exact candidate pack → Manager operation receipt 的跨库对账；
- binding/Hub/Loader/Registry 增加 owner-generation；
- growth-managed mutation 使用 host-only permit，通用 Auto/TaskGrant/lifecycle tool 不可替代；
- 通用 Builder 产出 Skill/Workflow 时强制 candidate-only handoff；
- 实施 Task 0 先等待通用 Capability worktree 合并并重新锁定 schema/HEAD；
- 目标架构和计划已按 `a33471e1` 重新校准。

## 第 3 轮

结论：`VERDICT: FAIL`

第 1/2 轮的单权威方向成立，但执行细节仍有 11 个会造成误激活、遗忘失效或假 E2E 的问题：

1. workflow schema 基线把已提交版本和共享脏工作区混在一起；
2. 把 Registry、文件系统和 SQLite 激活误写成“一个事务”；
3. RevocationBarrier lease 覆盖整个 provider/tool 长调用，会让 forget 被永久挂起；
4. Companion quarantine 尚未 ready 时就恢复旧 ToolSpec/Skill/MCP/runtime；
5. authority cutover 在 durable 不可逆 marker 前已经切 active 外部状态；
6. 低风险 instruction/声明式 Workflow 没有“不需要评测授权但仍需独立评测”的合法状态；
7. Pack v2 只有概念，没有 schema 文件、版本分派和 v1 兼容验证；
8. Personal Workflow 没有精确定义跨恢复稳定的 effect id；
9. 一次性外部动作确认与既有 execution grant/effect 权威边界不清；
10. stream delta 可能在崩溃/重连后变成幽灵半截消息；
11. 真人 E2E 没有唯一 launcher、source backend 断言、隔离目录和 PID manifest。

处理结果：

- 明确当前提交/共享脏工作区两层基线，迁移号只在 Task 0 重新分配；
- 激活改为
  `publish_intent commit → Registry/managed root/runtime swap → binding+receipt commit`
  三段 saga，并用 catalog/ingress gate 隐藏中间态；
- barrier 只覆盖权威 fence、durable claim 和有界 `dispatch_started` ack，长调用期间释放，
  response/settle/emission 再按 revocation epoch 复验；
- Companion profile/quarantine ready 前只建立 dormant descriptor，不注册或启动执行能力；
- cutover 先冻结 journal 并提交 `roll_forward_required`，随后才允许 active switch；
- 安全 instruction/声明式图可从 `proposed→evaluating`，代码/hook/unknown 才要求 evaluation
  authorization；激活授权仍是独立门；
- 增加 `deskpet-pack-v2.schema.json`、v1/v2 parser 分派和 golden compatibility；
- effect id 固定为 `hash(child_run_id,selection_id,graph_hash,node_id)`；
- delegated grant 只管可代理范围，外部动作消费继续由 execution grant+effect UoW 独占；
- delta 使用 invocation/stream epoch 的纯内存 provisional buffer，断线/unknown/cancel 清除，
  completed canonical event 才替换；
- Task 15 固定唯一 Tauri launcher、source backend env、隔离端口/用户目录、日志、PID tree
  manifest 和精确 cleanup。

补充：一次候选审查因代理配额错误退出，没有产出审查内容，因此不计为挑战轮次。

## 第 4 轮

结论：`VERDICT: FAIL`

前三轮的方向已经稳定，但审查仍找到 7 个会让实现无法按计划安全落地的阻塞项：

1. provider 持久化顺序与 SP-02 使用的事务模型不一致，少算 canonical emit 前的 outcome
   commit；
2. activation 把 runtime prepare/健康检查放进 RevocationBarrier，挂起时会饿死 forget；
3. “catalog/ingress gate”只有概念，没有 owner、key、关闭/开放语义和冷启动恢复；
4. 后台外部动作确认没有真正接到现有
   `DecisionOpen/execution_grants/claim_tool_call()` 执行链；
5. SessionDB 一边要求 Task 0 动态分配 `S→S+1`，一边又硬编码 v20/012 与测试文件名；
6. 真人 E2E 把“稳定 user-data”和“每次启动唯一 RunId”混为一谈，也没有可执行的
   Start/Status/Stop 协议；
7. AC-13/16 只写“卡片可查看”，没有 owner-fenced、分页、遗忘后脱敏的详情查询协议。

处理结果：

- 按真实 `AgentLoop → Coordinator → ReActDriver` 顺序明确四个事务：
  create+start、provider claim、pre-emit outcome、既有 turn fence；重新运行 SP-02，
  p95 回退 `7.146%`，低于 10%，且精确清理全部测试 PID；**该模型随后被第 5 轮
  逐行生产链复核推翻，不能作为最终性能证据**；
- inactive stage 与 non-routable runtime prepare/healthcheck 全部移到 barrier 外并设硬超时；
  barrier 内只做已就绪 pointer/catalog swap、DB intent/binding/receipt，随后立即释放；
- 新增 Platform-owned `CapabilityCatalogGate`：
  `(owner_key,scope,scope_key,pack_id)`，close 后新 snapshot 返回
  `catalog_reconciling`，既有 lease 继续，binding/receipt/committed stamp 对账后才 open；
  冷启动先从 pending intent 恢复 closed gate；
- `PreparedToolSet` 增加 hash/序列化覆盖的 `confirm_only_names`；外部调用固定走原
  durable call 的
  `DecisionOpen → authorization_commit → execution grant → GrantConsume +
  claim_tool_call(effect_type)`，`auto_mode` 不能放行，Companion 不建第二张消费表；
- 所有实施名改为 Task 0 动态锁定的 `S→S+1`、`<NEXT_MEMORY_MIGRATION>` 和稳定
  feature 文件名；v19→v20 只保留为 spike 的历史快照；
- launcher 明确
  `-Action Start|Status|Stop -ScenarioId <stable> -LaunchId <unique>`；Scenario 独占
  user-data，Launch 独占进程 manifest/log/temp，Stop 逐 PID identity 清理；
- 增加 `CompanionDetailQueryPort/companion_detail_get`：可信 control binding 提供 owner，
  详情分 section/cursor/大小上限读取 redacted lineage、diff、report、receipt 与 binding；
  跨库版本变化返回 `detail_changed`，遗忘/旧 generation 不返回原文或 archive diff。

## 第 5 轮

结论：前三次复核均为 `VERDICT: FAIL`；每次均先修正并整体复核，再进行下一次复审。

首次复核发现：

1. 第 4 轮的统一四事务仍不是真实链：`ReactFinal` 没有 turn fence，`ToolBatch` 则有
   boundary、首次 goal、turn fence、batch/attempt 与逐 call prepared；
2. confirm-only 只改 auto policy 不够：可信 frozen snapshot 没有强制 OR
   `authorization_indexes`；单 await `execute_prepared()` 也没有真正的 dispatch-start ack；
   当前主消息 session 不能授权不同 session 的后台 Run；
3. runtime 可能在 spawn 后、PID evidence 写入前崩溃，留下 non-routable orphan；
4. DetailQuery 的 `Vc/Vp` 只是名字，没有同事务递增的 durable token；
5. E2E manifest 只冻结 ready 时 PID，会漏 late child、reparent 和 root crash；
6. Task 4/5/6 可并行写 `main.py/turn_preparer.py`，其他共享生产文件也没有唯一 wave owner；
7. CatalogGate 曾出现 `gate read → publish_lock` 与 publisher 反向的 AB-BA；
8. `.sp` 仍残留一个指向旧 G 盘的 broken junction，清理报告与磁盘不一致。

修正结果：

- SP-02 用真实 `SqliteExecutionUnitOfWork + ReActDriver` 分测 Final、ToolBatch(1/3)：
  当前提交数 `0/5/7`，增加 claim/outcome 后 `2/7/9`。per-transaction connection 的 p95
  回退 `43.257%/21.793%/10.887%` 不通过；保持 `WAL+synchronous=FULL` 和全部事务边界，
  改为串行长寿命 `ExecutionWriteLane` 后相对绿色基线为
  `+6.734%/-32.379%/-42.648%`，据此锁定实现前置门；
- prepared snapshot 以 host-only ref/hash 固化 per-call confirm-only，Driver 强制 OR
  authorization index，ToolExecutor 二次核验；新增
  `begin_prepared→start→DispatchStartedAck|NotStarted→completion`，function/MCP/local-runtime
  各自必须实现；无 ack fail closed；
- 新增 owner-fenced `CompanionActionDecisionService`：主消息 UI 只发
  `{decision_id,allow}`，服务端从原后台 Run 重建 session/principal/auth epoch/nonce/version，
  并记录 explicit-user-click provenance；
- `prepare_activation_runtime()` 先提交 runtime intent/instance id，再把 local child 放进
  per-operation Job Object 后启动并 CAS PID/session；reconciler 覆盖 spawn-before-PID 等
  全部崩溃点；
- Companion 增加同事务 CAS 的 `companion_detail_versions`；Platform 增加
  `capability_owner_detail_versions/PlatformDetailToken`，所有页读取被 `Vc0/Vp0` 与
  `Vc1/Vp1` 包围；
- E2E 使用稳定 helper + `KILL_ON_JOB_CLOSE` Job；Status 动态追加 late descendants/
  listener owners，Stop 后按 Job、repo、user-data、temp/config、端口和 parent scope 做
  survivor=0 审计；
- Task 0 生成机器可读 file-claims，关键共享文件定义执行 wave；Task 5 改为依赖 Task 4，
  从而形成 `Task 4→5→6`；
- 全局锁序统一为
  `RevocationBarrier（若需要）→ publish_lock → CatalogGate → 单库事务`，静态禁止逆序；
- 已精确删除 `.sp` broken junction；复核 `.sp/.sp2/.sp3` 均不存在，git status 无 warning。
  第 5 轮 writer 重跑的 PID `32632→25976/15352` 全部消失，峰值约 `827.86 MiB` 已回收，
  scoped survivor=`0`。

### 第 5 轮第二次复审

当时结论：`VERDICT: FAIL`；以下执行级缺口修正后，由同一代理进入第三次复审。

第二次复审发现：

1. 三段 tool 协议没有认领真实 `ToolRegistry`、MCP、local-runtime、Platform 与 Workflow
   调用方，普通 callable 也无法表达 adapter-specific ACK；
2. `auto_mode=ON` 时现有 permission runtime/UoW 会把显式点击仍记成 `policy:auto`，
   `user_explicit_companion_card` 不可达；
3. `ProviderInvocationCoordinator` 只有概念，没有确定文件/API；provider/MCP/local worker
   的 dispatch-start seam 也没有真 spike；
4. `ExecutionWriteLane` 没有区分 commit 开始前后取消；
5. Task 3/6 已依赖 `PreparedRunContext`，但原计划到 Task 10 才新增；
6. Builder、Reminder、built-in paths 仍有“到时候再选文件”的声明；
7. 架构图把 ACK 错画在物理 handoff 前；E2E ID/manifest 缺 canonical containment 与
   onboarding 真登录纪律；
8. Platform detail token 的 `binding_generation` 没说明是 owner 全局还是单 pack；
9. Windows suspended-create/Job helper 生命周期尚未真验证。

修正结果：

- Task 3 新建确定的 `execution/dispatch.py` 与 `execution/provider_invocations.py`；
  `ProviderInvocationCoordinator` 独占 provider attempt/retry/fallback，httpx 走公开
  `AsyncBaseTransport` handoff；Task 6 认领 Registry、MCP manager/transport、local runtime/
  proxy/Platform、Workflow Protocol/code runtime 与全部 permission 文件；
- ToolSpec 纳入 `PreparedDispatchAdapter` identity；MCP 在 `ClientSession` 构造前包装 SDK
  公开 write stream，禁止私有 `_write_stream`；local worker 必须
  suspended-create→per-call Job→resume→pipe handoff；`mcp/httpx` 锁定已验证的
  `1.28.1/0.28.1`；
- confirm-only 增加 host-only `explicit_only` 与
  `PreparedAuthorizationCommit.authorization_origin`；explicit decision 即使 auto mode
  也只能签 `source=user`，UoW 分支验证原 decision/boundary/nonce/epoch/provenance；
- Task 3 前置定义 `PreparedRunContextV1 + Kernel start keyword`，Task 10 只填 namespaced
  Companion selection extension；
- `ExecutionWriteLane` 固化 commit-start 前 cancel/rollback 与 commit-start 后
  shield/bounded outcome；不确定时 poison connection 并返回 `write_outcome_unknown`；
- Builder 精确落在 `capabilities/builder.py::CapabilityBuilderHost.finalize_child_completion`
  的 output port；Reminder 唯一落在 `companion/reminder_tools.py`；built-in root 精确落在
  `backend/paths.py`，旧 builtin tree 进入明确迁移/删除清单；
- `PlatformDetailToken` 改为 owner-scope 的 `owner_catalog_generation`，每个具体 pack 另带
  binding generation；架构图改为 handoff→ACK→completion；
- launcher 删除任意 ManifestPath，ID 使用窄正则，所有路径 canonical containment/reparse
  检查；fresh Scenario 的 onboarding 只读 gitignored DEV 凭据并在登录窗关闭后才截图；
- SP-10 真跑 httpx fake transport、MCP 1.28.1 stdio server、本地 worker：三条链均
  cancel-before-handoff=0、ACK 先于长 completion、post-ACK crash/timeout=unknown。
  root PID `5640`，峰值 `102.50 MiB`，临时根删除，内外 survivor=`0`；
- SP-11 真跑 Windows `CREATE_SUSPENDED→AssignProcessToJobObject→ResumeThread`、root
  退出后的 late child、helper 正常 close/crash：resume 前 effect=0，两条 Job 路径都清完
  launcher/真实解释器。root PID `9524`，峰值 `71.26 MiB`，临时根删除，内外
  survivor=`0`。

### 第 5 轮第三次复审

当时结论：`VERDICT: FAIL`；以下问题修正后，由同一代理进入第四次复审。

第三次复审发现：

1. Task 7 明确要改 workflow schema `N+1→N+2`，共享 wave 也写了 `3→7→10`，但 Task 7
   文件清单漏认领 `backend/deskpet/workflows/store/schema.py`；
2. activation 的整个 runtime prepare 位于 RevocationBarrier 外，存在
   `forget 已提交 → 旧 prepare 随后才 spawn/connect` 的 post-forget launch 竞态；
3. 现有 `LocalEvaluationRunner` 可逐例直接调用任意 `execute(example)`，代码/hook 评测没有
   candidate/owner revocation fence；evaluating 中 forget 后仍可能领取下一 case 并启动代码；
4. baseline 把整个 UI/Godot 分支写成 patch-equivalent，但 `git cherry` 仍把 HEAD
   `9d85a9a8` 标为 `+`。

修正结果：

- Task 7 文件清单显式认领 workflow `schema.py`，与 machine-readable claim 和
  `3→7→10` wave 一致；
- activation runtime 拆成：
  `static prepare（锁外、零启动）→ 第一次短 lease 下重验 + durable launch claim +
  Job/session start-ACK → 锁外只等待同一 health operation → 第二次短 lease 下重验 epoch +
  activate`。NotStarted 必须启动数为 0；Unknown 不得在 lease 释放后 late start；
  forget-after-ACK 不等待 health，按持久 Job/PID/session 精确 abort；
- 新增 `evaluation_case_launches` 与
  `FenceAwareEvaluationCaseExecutor`；生产 Runner 不再注入 raw execute callback。每个
  case/variant/attempt 都在短 lease 内重验 candidate/eval authorization/owner/case epoch，
  durable claim 后完成 start-ACK；completion、settle 和下一 case 前再次重验。forget 会
  invalidates case lease、写 exact abort request，迟到结果不进 report；
- Task 9/14 增加 runtime 与 evaluation 的 forget-before-ACK、forget-after-ACK、case 间
  forget、timeout 后 late-handoff=0、最终 scoped survivor=0 fault matrix；
- SP-10/11 已分别真实证明 start/completion split、cancel-before-handoff=0 和 Windows
  suspended-create/Job 清理原语；本轮只把已验证原语接到 activation/evaluation 的计划契约，
  没有把 spike 冒充生产竞态验收；
- baseline 改为：builder 等价；UI/Godot 前两提交等价，但分支 HEAD 仍须 Task 0 逐文件与
  测试对账，不能提前删除。

### 第 5 轮第四次复审与最终结论

第四次复审继续发现 5 组执行级缺口：

1. 低风险自动回滚只有概念，缺少固定观察窗、可信触发条件、锁序、跨库恢复和版本换绑时
   的旧 guard 退役规则；
2. Reminder 目标 handler 集、旧 handler 迁移身份和 legacy/companion 两阶段 manifest
   关系不确定；
3. Reminder create/cancel 横跨 Companion 与 execution 两个库，崩溃恢复时可能重复修改；
4. 外部 effect 把“可能已开始”误写成新 status，且没有把 handoff 与完成处置组合固定下来；
5. 偏好晋升所需的独立上下文数量仍是可漂移描述，无法得到确定性验收。

修正结果：

- 增加 `companion_guard_v1`：固定 24 小时、可信严重事件阈值为 1；触发时遵循
  `RevocationBarrier → publish_lock → CatalogGate → Companion transaction`，并为
  update、builtin override 和 genesis 分别冻结 rollback/remove-override/disable 计划。
  换绑时同事务 supersede 旧 guard；自动新绑定建立新 guard，人工绑定不建立自动 guard；
  迟到旧 incident 不得生成 rollback request；
- 固定三个 V2 handler：
  `core.reminder_create.v2/core.reminder_list.v2/core.reminder_cancel.v2`。旧
  `legacy.list_reminders.v1` 在 Task 13 退役且不保留 alias；legacy 与 companion phase
  分别做 Registry 与 source/effect/build manifest 的严格相等校验；
- 新增 owner、generation、stable effect id 作用域的
  `reminder_mutation_receipts`。create 的 reminder id 确定性派生；create/cancel 的
  mutation、schedule CAS、occurrence/outbox 和 receipt 同一 Companion 事务提交。若随后
  execution settle 前崩溃，只能按 exact effect id/hash 读取 receipt 补 settle；
- 保留既有 effect status 集，并固定
  `status + handoff_state + completion_disposition` 合法组合。外部 start 已进入但 ACK
  未返回的超时/崩溃保守记为
  `unknown + started_may_complete + inflight_effect_may_complete`；迟到完成只做受抑制
  reconcile，不得标成功、投递正文或产生 chained effect；
- `preference_promotion_independent_context_threshold` 出厂值固定为 `3`，允许配置范围
  为 2～10；S-2 使用默认值，重复、已衰减、已 tombstone 或冲突上下文不得计数。

同一独立审查者随后从最新磁盘版本重新核对计划、验收、架构基线与保存的架构图，确认：

- guard 换绑、并发锁序、崩溃恢复与迟到事件隔离闭合；
- Reminder handler 集及跨库幂等 receipt 可直接编码；
- 外部 effect 三元组与既有 schema 兼容；
- 主消息页真人 E2E、精确进程清理、worktree 对账合并与清理均进入 DoD；
- Task 0 会在共享上游、工作树和 schema 未稳定时阻止实施。

最终结论：`VERDICT: PASS`。未发现新的架构或可执行性 blocker。
