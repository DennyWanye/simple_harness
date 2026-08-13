# Code-level implementation tasks

> 本文件是 [`plan.md`](plan.md) 的可执行 task ledger。每个 Task 只能在其 listed tests PASS 且证据写入 gate receipt 后完成。`$SDK_REPO` 固定为独立 checkout `simple-harness-sdk`；`$PRODUCT_REPO` 为本仓库。

## S0 — repository / provenance / oracle

### T0.1 — 建立独立 package 与 immutable build [AC-1, AC-8]

- 文件：`$SDK_REPO/pyproject.toml`, `uv.lock`, `src/simple_harness/__init__.py`,
  `.github/workflows/{ci,release}.yml`。
- 改法：Hatch `src` layout；`requires-python >=3.11`；显式 wheel packages 与 sdist
  include/exclude；`__init__.__all__` 只导出 version/public factories。release job 只接受 `v*` tag，
  build job生成一次 artifact，test/publish jobs下载同一 artifact，不再 build。
- tests：`tests/artifact/test_build_contents.py`, `test_import_purity.py`,
  `test_reproducible_build.py`；命令 `SOURCE_DATE_EPOCH=0 uv build` 两次 hash 相同。
- 依赖：无。

### T0.2 — 逐文件 Apache provenance gate [AC-1, AC-8]

- 文件：`$PRODUCT_REPO/.../source-manifest.tsv`, `$SDK_REPO/REUSE.toml`, `LICENSES/Apache-2.0.txt`,
  `NOTICE`, `scripts/check_source_provenance.py`。
- 改法：对 [`cutover-manifest.md`](cutover-manifest.md) 每个 source blob 写 commit/blob hash、git
  authors、copyright、third-party/generated、license verdict、target；script 拒绝空字段与
  `approved` 以外状态。rewrite 文件仍记录行为来源但不得复制未批准文本。
- tests：`tests/artifact/test_provenance_complete.py` + `reuse lint`。
- 依赖：T0.1。

### T0.3 — 冻结/迁移 black-box oracle [AC-5..8]

- 文件：现有 `testcase-lock.json`, `behavior-contract.md`；新增
  `$PRODUCT_REPO/scripts/acceptance/verify_sdk_oracles.py` 和 `behavior-changes/BC-SDK-IMPORTS.json`。
- 改法：BC 文件精确定义 old production imports/symbols -> SDK public imports/symbols；允许机械
  import/fixture migration与删除只验证 retired router/ticketless behavior 的用例，禁止改变其余
  expected terminal/event/effect/delivery assertions。脚本验证“旧 hash 或 approved transform +
  assertion AST hash”二选一；不再要求已删除模块的测试 bytes 永久不变。
- tests：对一个未批准 expected 改写做 self-test，必须报 `FROZEN_ORACLE_CHANGED`。
- 依赖：`BC-SDK-IMPORTS` 已由 SR-8 显式批准；T0.1。

### T0.4 — 生成并冻结完整 symbol disposition [AC-5, AC-8]

- 文件：现有 `cutover-symbols.json`, `cutover-manifest.md`；新增
  `$PRODUCT_REPO/scripts/acceptance/build_sdk_symbol_disposition.py`、`source-symbol-disposition.json`。
- 改法：先验证 30 个 source SHA 与 184-symbol inventory hash；AST枚举每个 public top-level symbol，
  强制人工赋唯一处置 `sdk_public/sdk_private/product_adapter/product_owned/retire` 和 target symbol；
  任何 source drift、新 symbol、重复/未分类项 fail。review 后冻结完整 disposition hash，T1–T6 每次
  source变化必须更新 mapping而不能只更新 hash。最终 gate还扫描 forbidden owner definitions。
- tests：generator golden/self tests覆盖 source drift、unclassified、duplicate target、forbidden survivor。
- 依赖：T0.2；必须在 T1.1 前 PASS，不能推迟到 cutover。

## S1 — contracts / Provider / Tool

### T1.1 — 冻结 JSON、identity、message/event/error public API [AC-1, AC-2]

- 文件：`$SDK_REPO/src/simple_harness/contracts/{json,identity,messages,events,errors}.py`,
  `__init__.py`, `docs/api/contracts.md`。
- 改法：实现 frozen/slots dataclasses；所有 ID 用 distinct wrapper；recursive JSON validator 拒绝
  NaN/bytes/arbitrary object；stable error含 `code/public_message/retryable`，private cause 不序列化。
- tests：`tests/unit/contracts/test_{json,identity,messages,events,errors}.py`、public API snapshot。
- 依赖：T0.4。

### T1.2 — Provider Port 与一次调用 HTTP Adapter [AC-2, AC-3]

- 文件：`providers/{base,openai_compatible,errors,redaction}.py`。
- 签名：`Provider.target -> ProviderTarget(provider_id, model, pricing_key, endpoint_identity,
  adapter_key)`；
  `Provider.invoke(request: ProviderRequest, *, cancel: CancelToken) -> ProviderResponse`；
  `OpenAICompatibleProvider(client, base_url, provider_id, model, pricing_key, secret, timeout)`。
  官方 Adapter 在构造时从 normalized base URL、固定 adapter key 与 model 自己生成 immutable
  `ProviderTarget`；实际 endpoint/model 必须直接取自同一组字段。Host 和
  `ProviderRequest.metadata` 都不能覆盖 target。可信边界明确为 Host composition：SDK 不承诺防止
  恶意 Host 自己实现一个撒谎的 Provider；自定义 Provider 必须由 Host 信任并通过 conformance。
- 改法：从当前 Adapter只复刻 protocol oracle；移除 Agent/context/metrics/retry/fallback；解析
  structured tool calls与 usage；401/402/429/5xx/timeout/cancel 分 typed error；异常 `repr` 先 redaction。
- tests：`tests/conformance/test_provider_contract.py`, `tests/integration/test_openai_mock_server.py`,
  `test_secret_canary.py`；新增 target immutability、normalized endpoint identity、payload 使用 exact
  target model、metadata/model override 拒绝，以及自定义 Provider trust-boundary 文档/contract；
  live probe 复跑 H4。
- 依赖：T1.1。

### T1.3 — Tool Registry / Authorization / reconciliation contracts [AC-2, AC-4]

- 文件：`tools/{contracts,registry,authorization,reconciliation,errors}.py`。
- 签名：`Tool.invoke(arguments, context)->ToolResult`；`AuthorizationPort.authorize(prepared)`；
  `ToolReconciliationPort.observe(effect)->ReconciliationObservation`。
- 改法：schema在 handler 前校验 additionalProperties/type/enum/length/reserved fields；五态 outcome；
  observation 仅三态 `confirmed_not_started/completed/still_unknown` 并绑定 evidence ref。
- tests：`tests/conformance/test_tool_contract.py`, `test_authorization_contract.py`,
  `test_reconciliation_contract.py`；malformed/duplicate/late/cancel/secret cases。
- 依赖：T1.1。

## S2 — execution schema / UoW

### T2.1 — 定义 schema v1 与 migration owner [AC-2, AC-5, AC-7]

- 文件：`execution/sqlite/{schema.py,migrations/0001_initial.sql,database.py}`。
- 改法：只创建 cutover manifest 所列 execution tables；`Database.open(path)` 显式，启用 FK、
  configurable WAL，`close()` flush；migration table记录 SDK schema，不读取 DeskPet schema 1–29。
- tests：`tests/integration/execution/test_schema_v1.py`, `test_open_close.py`, `test_integrity.py`。
- 依赖：T1.1。

### T2.2 — root start/admission/decision/continuation 原子命令 [AC-5]

- 文件：`execution/uow.py`, `execution/sqlite/uow.py`。
- 函数：`create_with_start_snapshot`, `start_admission`, `resolve_admission`, `commit_decision`,
  `enqueue/claim/ack_continuation`；一个函数一个 `BEGIN IMMEDIATE` transaction。
- tests：`test_atomic_root_start.py`, `test_atomic_decision.py`, `test_continuation_fifo.py`；每个 SQL write
  point before/after fault + reopen。
- 依赖：T2.1。

### T2.3 — ticket/child/signal 原子命令 [AC-5, AC-6]

- 文件：同 T2.2 + `execution/contracts/children.py`。
- 函数：`claim_profile_launch_and_commit_child`, `finalize_child_and_enqueue_parent_signal`,
  `claim_next_child_signal`, `ack_child_signal_and_commit_parent_progress`；移除 ticketless command
  public method。`child_signals`
  schema v1 必须持久化 `claimed_by/claimed_at/claim_expires_at/claim_epoch`；每次 first claim/reclaim
  都在同一 CAS transaction 中递增 `claim_epoch`，并在 `ChildSignalRecord` 返回 owner/epoch/expiry。
  `claim_next_child_signal(parent_run_id, owner_id, now, lease_seconds)` 的唯一 eligibility 是该
  parent 中按 `(created_at, signal_id)` 排序的 oldest non-acked head：head 若被未过期 owner
  claim 则返回 `None`，禁止跳过它处理后续 pending signal；head pending/过期才可
  first claim/reclaim。`ack_child_signal_and_commit_parent_progress` 必须在单一
  `BEGIN IMMEDIATE` transaction 内同时 CAS `state=claimed + claimed_by + claim_epoch`、
  写入 parent continuation/event/payload 并落 durable ack receipt；continuation/event/receipt 均用稳定
  identity 唯一约束。after-commit 重试只有在同一 owner/epoch 且
  continuation/event/payload/receipt 身份一致时返回原 outcome，任一异同拒绝；
  不存在先 ack 后写 parent progress 或先写 progress 后 ack 的 public path。
- tests：`test_atomic_child_launch.py`, `test_atomic_child_terminal_signal.py`,
  `test_ticket_generation.py`；duplicate/stale/reused ticket；signal claim 并发唯一 owner、claim 后崩溃
  reopen、未过期不可偷取、过期 reclaim 且 epoch 递增、旧 owner/epoch ack 拒绝、
  ack after-commit 同 receipt 幂等/异 receipt 拒绝；两个 signal 在并发、崩溃和 reopen 下都不得
  跳过未过期的 FIFO head；signal CAS、continuation、event、payload、receipt 每个
  write point 前后 fault injection + reopen，只允许全部提交或全部回滚。
- 依赖：T2.2。

### T2.4 — Provider invocation ledger 与 budget authority [AC-3, AC-5]

- 文件：`execution/{provider_invocations,dispatch,budget}.py`, SQLite UoW methods。
- 状态：`claimed -> handed_off -> succeeded|failed|unknown`；只有同 invocation CAS settle；usage
  absence -> estimator upper-bound or `unknown`，policy不得累计 0。`FrozenPriceEstimator` 必须绑定
  exact full `ProviderTarget` digest。claim 行持久化 canonical target snapshot/digest、estimator
  snapshot ID/digest。stable logical call identity 固定为数据库唯一的 `run_id + request_id`；request
  fingerprint 只描述不可变请求内容，target/estimator digest 是同一 logical row 的不可变 CAS 属性，
  不能因配置变化生成第二个 invocation ID；
  Coordinator 在 durable claim 和物理 handoff前验证当前 Provider/estimator 与冻结值。首次配置
  mismatch 或 claim 后重启替换 target/estimator，都以稳定 `provider_budget_target_mismatch` 拒绝，
  Provider 调用次数为 0。不接受响应后才标 unknown 或 request metadata override 作为
  pre-dispatch hard-cap authority。官方 Adapter target 是 SDK 可验证 authority；自定义 Provider 的
  真实性仍属于可信 Host composition 边界，不宣传为防恶意 Host。
- tests：`test_provider_dispatch_atomic.py`, `test_provider_unknown.py`, `test_budget_recovery.py`；新增
  相同 model/pricing key 但不同 provider/endpoint、claim 后重启替换 target/estimator snapshot、两个
  Coordinator 用不同 target 并发 claim 同一 logical call（恰好一行、第二 transport 零调用）、声明
  target 与 official adapter payload 一致性；全部 mismatch 在 transport 前拒绝。移植 current
  `test_provider_dispatch.py` oracle。
- 依赖：T2.2、T1.2。

### T2.5 — Effect ledger / fence / reconciliation [AC-4, AC-5]

- 文件：`execution/{effects,fences}.py`, `tools/executor.py`, SQLite UoW methods。
- 状态：prepared/handoff/outcome/unknown；stable call/effect id；reopen 调 T1.3 reconciliation Port；
  `still_unknown` 禁止 dispatch。
- tests：`test_effect_atomic.py`, `test_effect_reconcile.py`, `test_fence_epoch.py`；移植
  `test_wi2_tool_executor.py` oracle。
- 依赖：T2.2、T1.3。

### T2.6 — generic terminal delivery outbox [AC-5]

- 文件：`execution/delivery.py`, SQLite UoW methods。
- 函数：`commit_root_terminal_with_deliveries`, `claim/complete/release_delivery`；root terminal、outbox、
  terminal fence receipt 同 transaction；dispatcher sink失败只重试 delivery，不重开 Run。
- tests：`test_terminal_delivery_atomic.py`, `test_delivery_dispatcher.py`；移植
  `test_workflow_outbox_tx.py` 全部行为断言。
- 依赖：T2.2。

## S3 — Kernel / ReAct

### T3.0 — 冻结真实 Provider/ReAct/Tool/child/delivery seam [AC-5, AC-8]

- 文件：把 `spikes.md` H7 的 current integration cases复制为 SDK RED conformance skeleton：
  `tests/conformance/test_full_runtime_seam.py`；只替换 imports/factories，不改 assertion semantics。
- 改法：一个 fixture要求五个显式 Ports：Provider、Tool、Authorization、Context、Delivery；执行
  `Provider -> AgentLoopCollaborator -> ReActDriver -> Kernel -> EffectBatchExecutor -> same Driver`，
  另一个执行 attached child terminal -> parent signal -> root terminal -> delivery。开始 T3.1 时测试因
  SDK runtime未实现而 RED；T3.3 完成前必须 GREEN，不能用单组件 tests代替。
- tests：current source 12-case seam matrix必须先保持 GREEN；SDK `test_full_runtime_seam.py` 的 RED
  reason只能是 missing planned public symbol，不能是 fixture/import错误。测试侧必须用
  temp SQLite 和可观测 fake 五 Ports 驱动真实 public API；case name/预期标签不得
  传入生产实现，禁止 `conformance_case`/静态字典自证后门。调用顺序必须来自
  test-owned spy，terminal/ledger/context/child signal/outbox 必须查真实 Runtime/SQLite；
  restart case 必须 close Runtime/DB、同路径重建并 reconcile，直接断言物理调用数、
  稳定 ID 和 no-replay。T3.0 首个字符串 oracle commit 仅是中间 RED checkpoint，
  不得作为 T3 通过依据。
- 依赖：T2.6、T1.2–T1.3；是 T3.1/T3.3 的前置 gate。

### T3.1 — Kernel lifecycle closure [AC-5, AC-6]

- 文件：`runtime/{kernel,terminal,admission,context,start_snapshot,live_index}.py`。
- API：`build_runtime(uow, profiles, drivers, ports, root_profile_key="agent.general")` 不接受 classifier；
  `Runtime.start/close`、`RunClient.start/query/signal/cancel`。`RuntimePorts` 必须显式提供
  Provider coordinator、Tool executor、Authorization、Context、Delivery、Reconciliation 与 typed
  ReAct checkpoint authority（仅暴露携 invocation `ExecutionLease` 的 read/CAS）；这些字段
  不得为 optional/`None` 或由 Driver 私有引用绕过，test 若不需要某能力也必须
  显式注入 typed no-op/spy Port。
- 改法：移植 atomic start/activation、single owner/recovery lease、terminal lifecycle；root key const
  校验；`tool_catalog_stale` 走一次 permanent terminal。`ContextPort` 冻结为 durable
  `load -> ContextSnapshot(revision, messages)` 与
  `append(run_id, execution_lease, expected_revision, append_id, entries) -> ContextSnapshot`；
  `append_id + payload`
  重试幂等，同 ID 异 payload 或 stale revision 拒绝，SDK conformance 提供 close/reopen 后
  仍保持 revision/receipt 的 SQLite 实现；官方 SQLite Context 使用
  `workflow_checkpoints` 的 `react.context.v1` namespace 持久化 snapshot 与 append receipt，
  每次 append 在同一 transaction 中校验 `workflow_leases` 的 active
  owner+epoch+expiry；旧 owner 在新 epoch 接管后即使 revision 尚未变也必须拒绝，
  不增加第二个内存 authority。
  Runtime 必须以严格小于 TTL 的间隔 heartbeat，用 owner+epoch CAS 原子
  `renew_runtime_lease`；同 owner 再 claim 不得只返回旧 expiry。renew 失败/超时必须立即
  cancel 该 owner 的 Driver 与所有尚未 handoff 的 side effects，之后任何 Provider/Tool/
  Context/terminal command 必须用相同 active lease fence。Provider/Tool 的 durable
  handoff transition 必须在同一 UoW transaction 内校验 active owner+epoch+expiry；旧 owner
  失租后未 handoff 的操作零写入/零出站拒绝。失租前已原子进入 `handed_off`
  的物理调用可完成一次，但新 owner 只能读取/reconcile 原 ledger，绝不得重放；
  `close()` 必须先取消 Driver/未 handoff 操作并在 heartbeat 仍有效时 join 完成，
  然后停止并 join heartbeat，最后释放 lease；禁止先停续租后等待慢取消。
- 跨 slice API 责任：`execution/dispatch.py::ProviderInvocationUnitOfWork.
  hand_off_provider_invocation(..., execution_lease)` 与 `execution/effects.py::EffectUnitOfWork.
  mark_effect_handed_off(..., run_fence, execution_lease)` 为必改 public Port；SQLite 实现在各自
  `BEGIN IMMEDIATE` 内先匹配 `workflow_leases(run_id, runtime.kernel, owner_id, epoch,
  expires_at > now)` 再执行 ledger CAS。Tool handoff 同时校验原 `RunFenceLease` 和
  Runtime `ExecutionLease`，两者缺一拒绝；不得只比较 effect row 中的历史
  `fence_epoch`。同 transaction 必须查 `run_fences` 当前行为
  `(run_id, execution_lease.owner_id, run_fence.epoch, active)`，并要求
  `run_fence.run_id == execution_lease.run_id` 且 `run_fence.owner_id == execution_lease.owner_id`。
  handoff 还必须同时匹配
  `run_fences.runtime_lease_epoch == execution_lease.epoch == run_fence.runtime_lease_epoch`；
  同 owner 以 runtime epoch+1 接管但尚未 acquire 新 RunFence 的窗口，旧 fence + 新
  ExecutionLease 组合也必须零 handoff 拒绝。
  `EffectExecutor` 不得用构造时静态 owner 或自行 acquire/release fence；必须消费
  Kernel 通过 `DriverInvocation` 传入的 per-call `execution_lease + run_fence`。Provider/Tool
  post-handoff settle/reconcile 是唯一
  active-runtime-lease 例外：只能按原 ledger identity/version 终结一次，不得发起第二
  transport/handler。这些 API/SQL/tests 属 T3.1 H11 amendment，不得留给 Host 外部 check。
  T2.6 terminal 也是必改跨 slice API：`commit_root_terminal_with_deliveries(...,
  run_fence, execution_lease, now)` 及任何其他 terminal path 必须在同一
  `BEGIN IMMEDIATE` 内先校验 active `workflow_leases` owner/epoch/expiry，再校验
  current `run_fences` owner/fence epoch/state 以及
  `row.runtime_lease_epoch == run_fence.runtime_lease_epoch == execution_lease.epoch`，
  然后才允许 terminal event + delivery outbox + Run terminal CAS。失租后没有 terminal
  settle 例外；旧 owner 必须零 event/零 outbox/零 Run 变化拒绝。
  `RunFencePort.acquire(run_id, execution_lease, *, now)` 也必须在递增/替换
  `run_fences` 前，
  于同一 transaction 校验 active Runtime lease 与 run/owner；只有 Kernel `_activate`
  调用 acquire，并传当次 `ExecutionLease` 与同一 injected Unix epoch clock 的 `now`；
  UoW 不得在内部偷用 `time.time()` 或忽略 expiry。禁止 claim runtime lease 后另做一个
  无 fence 的 RunFence acquire，防止旧 owner 在两步之间过期，恢复后覆盖新 owner
  的 current RunFence。若 `run_fences` 已是同一 active execution owner 且当前 runtime
  lease epoch 未变，acquire 必须幂等返回原 RunFenceLease，不递增 epoch；只有新
  runtime owner/epoch 接管才递增。Kernel 与该 Run 的所有 Tool call 共用这张
  run-level fence；EffectExecutor 只验证/消费传入的 fence，不 acquire，也不在
  `finally` release。只有 Kernel terminal/close/takeover lifecycle 可释放/替换 fence，
  禁止每 Tool call 创建或释放 fence 使 Kernel terminal fence 失效。
  为区分同 owner ID 在 lease 过期后以 epoch+1 重新接管，`run_fences` schema v1
  必须新增 `runtime_lease_epoch` 并由 `RunFenceLease` 返回/校验；幂等条件是
  owner_id + runtime_lease_epoch 同时相同，同 owner 但新 runtime epoch 仍必须使 RunFence
  epoch +1。
  `ExecutionLease` 与 Kernel-owned `RunFenceLease` 必须从
  `DriverInvocation.{execution_lease,run_fence}` 沿
  `ReActDriver/AgentLoopCollaborator/ReActLoop -> ProviderInvocationCoordinator.invoke(...,
  execution_lease) -> hand_off_provider_invocation` 和 `ReActLoop -> EffectExecutor.execute(...,
  execution_lease, run_fence) -> mark_effect_handed_off` 逐层必填传递；禁止存入共享可变
  coordinator/executor state、闭包或 Host pre-check。每层都校验
  `execution_lease.run_id == command/context run_id` 且 namespace 为 canonical `runtime.kernel`；
  漏传由类型签名阻止，错 run/旧 epoch 必须在 handoff 前零物理调用拒绝。
- tests：`test_kernel_start.py`, `test_fixed_root.py`, `test_start_recovery.py`,
  `test_catalog_stale_terminal.py`；虚拟 clock 跨过多个 TTL 时 heartbeat 保持唯一 owner/
  第二 Runtime 零接管；停 renew 并由新 epoch 接管后，旧 owner 的 Driver 被 cancel，
  未 handoff Provider/Tool/Context/terminal 都是零新调用/写入；已 handoff 仅允许原调用
  完成/reconcile 而第二 transport/handler 为 0；close 后无 heartbeat task。
  直接回归文件：`tests/integration/execution/test_provider_dispatch.py`、
  `test_effect_atomic.py`、`tests/integration/runtime/test_kernel_start.py`；在 lease check 后/ledger CAS 前发生
  epoch+1 takeover 的并发用例必须证明旧 owner 两种 handoff 均零写入/零物理调用。
  non-cooperative Provider/Tool 的 bounded close 超时分支必须将未 handoff 隔离并依靠 stale
  lease 拒绝后续步骤；已 handoff 交 ledger reconciler，旧 task 不得保持无界 heartbeat，
  且 close 有界返回。
  补充 focused type/runtime tests 覆盖 Coordinator/Executor 漏传不可调用、错 run lease、
  旧 epoch lease，三者均在 handoff row/transport/handler 计数为 0 时拒绝；另覆盖
  新 Runtime acquire 使 `run_fences` epoch 递增但 effect row 仍是旧 epoch 的场景，旧
  RunFenceLease 必须零 handoff，以及 RunFence/ExecutionLease owner mismatch 零 handler。
  再补 runtime-lease claim 后暂停 -> TTL 过期 -> 新 owner claim+acquire -> 旧 owner 恢复 acquire
  的确定性竞态；旧 acquire 必须零更新拒绝，新 owner 的 RunFence epoch/owner 保持不变。
  同 active owner 的 Kernel acquire 后执行 N 次 Tool，必须始终传递同一 fence epoch，
  Tool 期间 `run_fences.state` 仍为 active，最后 terminal commit 仍用该 epoch 成功；
  仅 takeover 使 epoch +1。直接测试 EffectExecutor 不调用 `RunFencePort.acquire/release`。
  用 virtual epoch clock 证明 `now == expires_at` 及 `now > expires_at` 时 acquire 零更新拒绝；
  同 owner_id 以 runtime lease epoch+1 接管时 RunFence epoch 必须 +1，不得返回旧 fence。
  直接构造“同 owner ID、新 ExecutionLease epoch+1、旧 RunFence”组合，Provider/Tool
  handoff 均必须在 ledger/transport/handler 全为 0 时拒绝。
  对 terminal 补确定性 race：新 owner（及同 owner epoch+1）已 claim runtime lease ->
  暂停在新 RunFence acquire 前 -> 旧 owner 尝试 terminal，断言 runs/run_events/
  delivery_outbox 全部零变化。
- 依赖：T3.0、T2.2、T2.6。

### T3.2 — child/continuation/reconciler closure [AC-5]

- 文件：`runtime/{child_runs,child_signal_runtime,user_continuations,reconciler,runtime}.py`。
- 改法：child 入口只接 `ProfileLaunchTicketRef`；恢复 lease/epoch；parent signal/continuation FIFO；
  startup reconciliation顺序 provider -> effects -> child signals -> deliveries -> recoverable Run。
  `ChildSignalRuntime` 只能消费 T2.3 durable head-of-line signal claim lease，处理成功后
  用记录中的相同 owner/claim_epoch ack；crash/reopen 保留 claim 并等 lease 到期 reclaim，
  未过期 head 不得被后续 signal 超越，禁止进程内 list/lock 作为 authority。
- tests：`test_root_two_children.py`, `test_child_restart.py`, `test_continuation_restart.py`,
  `test_startup_reconcile_order.py`。
- 依赖：T2.3–T2.6、T3.1。

### T3.3 — ReAct Driver / termination budgets [AC-3..6]

- 文件：`runtime/drivers/{react,react_loop}.py`,
  `runtime/{context,termination,react_checkpoint}.py`，以及 T2.2 SQLite UoW 的 checkpoint CAS。
- 改法：把现 `AgentLoop` state machine改用 Context/Provider/Tool/Authorization/Trace Ports；Provider
  每 turn 只经 T2.4 coordinator；Tool只经 T2.5；max turns/calls/wall/cost/repeat hard gates写代码。
  `workflow_checkpoints` 的 `react.termination.v1` namespace 是唯一 durable termination authority，
  checkpoint 包含 version/lease_epoch、Unix epoch wall-clock `started_at/last_observed_at`、
  `provider_turns_reserved_total/tool_calls_reserved_total/repeat_key/repeat_streak`、
  phase 及当前 provider request/tool batch 稳定身份。每个 Provider 轮次在出站前先用
  owner+lease_epoch+expected_version CAS 到 `provider_reserved` 并递增 turn，RequestId 只由
  `(run_id, durable turn ordinal)` 派生；每个 Tool batch 在 prepare 任何 effect 前，
  按 Provider response 中的 call order 对每个 call 逐个模拟预算，`repeat_key` 冻结为
  `tool name + SHA-256(canonical JSON arguments)`。只有整个 batch 都不超限时才用一次
  CAS 原子写入新 total/key/streak 并进入 `tools_reserved`；任一 call 超限则
  checkpoint/effect/handler 全部零变化。恢复时必须先用同一
  request/effect identity 读取或 reconcile T2.4/T2.5 ledger，完成后用 T3.1 的稳定
  `append_id` 幂等写 context，再 CAS 到下一 phase；`unknown` 禁止分配新 turn/effect。
  崩溃可保守地消耗已 reserve 的 budget，但绝不得重置、重用 RequestId 或额外物理调用。
  wall-clock 不得使用跨进程不可比的 monotonic origin；若 injected epoch clock 比
  durable `last_observed_at` 倒退，则 fail closed 为 wall-clock termination，禁止通过调钟绕过上限。
- tests：`test_react_no_tool.py`, `test_react_tool_roundtrip.py`, `test_react_budgets.py`,
  `test_react_cancel.py`, `test_react_crash.py`；在 provider reserve/handoff/completed/context append 前后
  和 tool batch reserve/每个 result append 前后 fault injection，close/reopen 后计数不归零、
  RequestId/effect ID 不变、已 completed 不额外 transport/handler、unknown 不重放；虚拟 wall
  epoch 跨 reopen 触发硬上限，stale owner/lease_epoch/checkpoint version 全部拒绝；
  旧 owner 在 lease epoch 接管后携带尚新 context revision 进行 append 也必须零写入拒绝；
  同 batch 多个相同/不同 signature、跨 batch 同 signature、reserve 后 crash/reopen，
  并断言任一 call 超 max Tool/repeat 时 effect/handler 为 0。
- 依赖：T1.2–T1.3、T2.4–T2.5、T3.1。

### T3.4 — SDK runtime conformance surface [AC-4, AC-5, AC-8]

- 文件：`testing/{models,fakes,provider,tool,runtime}.py`, `testing/__init__.py`。
- API：`ConformanceHost(provider_factory, tool_factory, runtime_factory, paths_factory)`；
  `run_conformance(host, suites)->ConformanceReport`，protocol=`simple-harness-conformance.v1`。
- tests：SDK自身 fake host 执行 no-tool/one-tool/multi-tool/cancel/restart/duplicate/unknown；JSON report
  schema snapshot。
- 依赖：T3.2–T3.3。

## S4 — Workflow runtime / selection

### T4.1 — Native compiler/runner/checkpoint ports [AC-5, AC-7]

- 文件：`workflow/{contracts,definition,compiler,runner,checkpoint,lease,recovery,replay,trace}.py`。
- 改法：`WorkflowRunner.__init__` 必填 registry/checkpoint/lease/recovery/trace/UoW；禁止构造 concrete
  SQLite；保留 retry/loop/HITL/quarantine/replay语义。
- tests：`test_compiler.py`, `test_runner_fault_matrix.py`, `test_checkpoint_reopen.py`,
  `test_lease_quarantine.py`, `test_replay.py`。
- 依赖：T2.1–T2.5。

### T4.2 — Profile catalog / orchestration control / launch ticket [AC-6]

- 文件：`runtime/profiles.py`, `runtime/orchestration.py`, `runtime/drivers/workflow.py`。
- API：`ProfileDescriptor(key, description, use_when, avoid_when, input_schema_ref, generation,
  fingerprint)`；`workflow_spawn(profile_key, objective, ..., candidate_id?, catalog_generation)`。
- 改法：control schema从 frozen model-spawnable catalog生成；Host只 validate + bind ticket；无 regex/
  classifier/route_hint。conditional registration检查 required Ports。
- tests：`test_agent_selected_profile.py`, `test_stale_catalog.py`, `test_forged_binding.py`,
  `test_optional_profiles.py`。
- 依赖：T2.3、T3.3、T4.1。

## S5 — official Profiles

### T5.1 — durable_task graph/ports [AC-5, AC-7]

- 文件：`workflows/durable_task/{definition,state,ports,nodes,output_contract}.py`。
- 改法：节点固定 plan -> HITL -> execute batches -> test/audit -> bounded repair -> output audit ->
  terminal；completion只依据 plan/effect/output receipts；所有产品能力经 Proposal/CapabilityCatalog/
  Workspace/Artifact/Authorization Ports。
- tests：`test_durable_task_graph.py`, `test_durable_task_human.py`, `test_durable_task_recovery.py`,
  `test_durable_task_output_negative.py`。
- 依赖：T4.1–T4.2。

### T5.2 — personal descriptor/catalog/interpreter [AC-6, AC-7]

- 文件：`workflows/personal_v1/{contracts,catalog,compiler,interpreter,definition}.py`。
- 改法：bounded descriptor只暴露 whitelist；candidate id + generation/fingerprint进入 control；Host从
  CatalogPort绑定 owner/version/graph；interpreter只允许 idempotent_read/deterministic_reusable。
- tests：`test_personal_catalog.py`, `test_personal_selection_live.py`, `test_personal_forgery.py`,
  `test_personal_safe_tools.py`, `test_personal_reopen.py`。
- 依赖：T4.2；H3。

### T5.3 — capability_build durable specialization [AC-6, AC-7]

- 文件：`workflows/capability_build/{contracts,profile,payload,validation}.py`，pyproject extra。
- 改法：Profile绑定 durable_task v1；payload必须含 current-stamp search-miss receipt；required Ports
  六项全部 ready才注册；build/test/install/activate每步有 authorization/receipt，成功后默认可用。
- tests：`test_capability_profile.py`, `test_search_miss_gate.py`, `test_build_failure.py`,
  `test_install_activate.py`, `test_required_ports.py`。
- 依赖：T5.1、T4.2。

### T5.4 — conformance CLI/pytest consumer API [AC-8]

- 文件：`testing/cli.py`, `testing/pytest_plugin.py`, `docs/api/conformance.md`, pyproject entry point。
- API：`python -m simple_harness.testing --host module:factory --suite provider,tool,runtime,workflow
  --json report.json`；extra `[testing]` 安装 pytest；plugin fixture `simple_harness_conformance_host`。
- 兼容：report记录 SDK version、protocol version、host id/platform；major protocol不匹配 fail closed。
- tests：`tests/conformance/test_cli.py`, `test_pytest_plugin.py`, `test_protocol_version.py`；wheel install后
  执行 CLI，证明 tests/源码目录不存在也可运行。
- 依赖：T3.4、T5.1–T5.3。

## S6 — Simple Harness consumer/cutover

### T6.1 — Product SDK adapters [AC-6..8]

- 文件：`backend/deskpet/sdk_adapters/{composition,ingress,provider,authorization,context,tools,
  reconciliation,personal_catalog,capability_host,delivery,runtime_paths}.py`。
- 改法：逐项实现 SDK Protocol；`composition.build_product_runtime()` 只 import SDK public API；
  Presenter/SessionDB/Tool handlers/permissions/Companion/capability platform保持产品 owner。
- tests：每个 Adapter 一个 contract test；用 T5.4 CLI 对 `deskpet.sdk_adapters.conformance:build_host`
  运行 provider/tool/runtime/workflow suites。
- 依赖：T5.4。

### T6.2 — 生产 ingress/path dependency 首次切换 [AC-6, AC-8]

- 文件：`backend/main.py`, `backend/deskpet/harness/adapters/product_turn_open.py`（改成薄转发后删除）、
  `backend/pyproject.toml`, `backend/uv.lock`。
- 改法：开发期 `uv sources` 指向 `$SDK_REPO`；main composition/ingress只用 T6.1；禁止 fallback
  flag；运行 oracle迁移脚本，保持 approved assertions。
- tests：现有 Harness targeted suite + SDK-S1..S7 automation；`assert_sdk_cutover.py --phase path`。
- 依赖：T6.1、T0.3。

### T6.3 — 删除旧 authority / matcher / router [AC-6..8]

- 文件：按 [`cutover-manifest.md`](cutover-manifest.md) SDK-authority rows删除或裁出产品-owned部分；
  删除 `ModelPersonalWorkflowMatcher` production wiring、legacy selector/ticketless public branch。
- 改法：消费 T0.4 已冻结的完整 disposition并逐 symbol删除；Adapter不得 import deleted owner；历史测试按
  BC-SDK-IMPORTS迁到 SDK public API，assertion AST hash保持。
- tests：`scripts/acceptance/assert_sdk_cutover.py --phase deleted`；全 Harness/companion/capability tests；
  negative `rg`/AST/module inventory。
- 依赖：T6.2 parity PASS。

### T6.4 — 审计式 dev execution reset [AC-7]

- 文件：`scripts/dev/reset_sdk_execution_data.py`, `backend/tests/test_sdk_execution_reset.py`,
  `docs/sdk-reset.md`。
- 改法：resolve exact user-data；dry-run inventory DB/WAL/SHM+hash；生成 nonce；只有
  `--confirm-reset <nonce>` 且 allowlist exact match才删除；拒绝 root/home/repo/evidence/Product Session。
- tests：temp directory positive；symlink/path traversal/wrong nonce/broad target negative；产品真实 reset
  前单独取得执行确认，输出只记录安全 hash。
- 依赖：T6.2；在 T6.3 final run 前执行。

### T6.5 — Simple Harness desktop真 E2E [AC-5..8]

- 文件：`scripts/acceptance/sdk_desktop_receipt.py`、本 plan result/index（原始证据 local only）。
- 改法：按 AGENTS.md 启动唯一 Tauri-managed backend/vite；SDK-S1..S5逐案真实点击/输入；receipt按
  run/child/effect/provider/delivery ID 审计，supervisor restart验证无重复。
- tests：SDK-S1..S7 terminal expectations；截图/日志/DB hashes在 `.local-test-evidence`。
- 依赖：T6.3–T6.4。

## S7 — exact artifact / release / handoff

### T7.1 — 私有 immutable release artifact [AC-1, AC-8]

- 文件：SDK `.github/workflows/release.yml`, `docs/release/v0.1.0.md`。
- 改法：默认 remote `git@github.com:DennyWanye/simple-harness-sdk.git`、visibility private；public 化需
  另行显式批准。GitHub Release保存 wheel/sdist/SBOM/NOTICE/attestation；tag build只生成一次，所有
  runner下载该 artifact；release note记录 commit/tag/hash。
- tests：release dry-run；artifact digest/attestation verify；remote/tag ancestry。
- 依赖：T0.2、T5.4。

### T7.2 — 三平台安装同一 wheel [AC-1, AC-8]

- 文件：SDK CI `artifact-consumers.yml`。
- matrix：`ubuntu-24.04-arm` (Linux ARM64), `macos-14` (ARM64), `windows-latest` (x64), Python 3.11；
  job下载 T7.1 exact wheel，校验 hash，clean venv装 `[testing]` 并跑 import/conformance CLI。
- tests：三个原生 runtime report必须同 wheel SHA/protocol/version；任一缺 runner/PASS则 release blocked。
- 依赖：T7.1 candidate artifact。

### T7.3 — Product vendor exact wheel 与 PyInstaller [AC-1, AC-8]

- 文件：`backend/vendor/simple_harness_sdk-0.1.0-py3-none-any.whl`, `backend/pyproject.toml`,
  `backend/uv.lock`, `scripts/verify_sdk_wheel.py`, `scripts/build_backend.ps1`, PyInstaller spec/hook。
- 改法：从 private Release下载一次，验证 T7.1 SHA后把**同一 bytes** vendored进 private产品 repo；
  backend dependency使用 local wheel direct reference并由 `uv.lock` 锁 hash；build前 verify；PyInstaller
  collect SDK modules/metadata。禁止 editable/path/PYTHONPATH。
- tests：clean checkout `uv sync --frozen`、wheel provenance、frozen backend import/conformance、
  `assert_sdk_cutover.py --phase wheel`，然后重跑 T6.5。
- 依赖：T7.2 PASS。

### T7.4 — AIPhone Handoff [AC-8]

- 文件：SDK `docs/consumers/aiphone-handoff.md`, release manifest JSON。
- 改法：写 private Release artifact获取方法、exact identifiers、Python/platform results、factory/Port/
  conformance CLI、三个 Profile、Mobile Host boundary、known limits；不含 secret/设备控制码。
- tests：在 clean Linux ARM64 job用 handoff命令安装 exact artifact并运行 fake Mobile Host Tool Adapter
  conformance；本 release不写/部署 AIPhone repo。
- 依赖：T7.3。

### T7.5 — 架构事实与 program closure [AC-1..8]

- 文件：SDK architecture/status/changelog；产品 `ARCHITECTURE/SDK_EXTRACTION.md`,
  `ARCHITECTURE/PROJECT_STATUS.md`；`ac-trace.json`, `FINAL_REPORT.md`。
- 改法：生成 `AC -> Task -> code symbols -> testcase -> result/receipt`；记录 old authority absence、
  exact wheel、consumer reports与所有非 PASS gate。只有 AC-1..8 全 PASS 才标完成。
- 依赖：T0.1–T7.4 全部 PASS。
