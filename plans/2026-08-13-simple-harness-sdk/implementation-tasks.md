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

### T0.5 — 补冻 Workflow error vocabulary oracle [AC-5, AC-8]

- 文件：`workflow-errors-supplemental-oracle.json`、
  `$PRODUCT_REPO/scripts/acceptance/{verify_sdk_oracles,build_sdk_symbol_disposition}.py`、对应golden tests。
- 原因：H15发现`backend/deskpet/workflows/errors.py`被原30-file inventory漏掉；不修改或放宽既有
  30-file/184-symbol freeze，而是增加独立supplemental authority。
- 冻结：source commit=`122ec55989f8a77e023aeb44ba1b4dae1b694269`，SHA-256=
  `2d5e1c536f2a1c73dcae8034425572c3749ca904029d44253af2f0bcdb01bb93`，12个symbols逐项
  `sdk_public -> simple_harness.workflow.errors.*`，并全部进入final product forbidden-survivor扫描。
- gate：两个acceptance scripts必须同时校验原oracle与supplemental hash/inventory/disposition/API targets；
  source drift、missing/extra symbol、target缺失、产品保留任一同名definition/constant都fail closed。
- tests：supplemental unchanged PASS；逐项source drift/unclassified/duplicate target/missing SDK target/
  forbidden survivor golden FAIL；不得重新生成或改变原184 inventory hash。
- 依赖：T0.4；必须在T4.1/T4.3提交前GREEN。

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
  `tests/conformance/test_full_runtime_seam.py`；只替换 imports/factories，不改 assertion semantics；
  `scripts/acceptance/verify_full_runtime_stage.py`；`pyproject.toml` 注册
  `h7_runtime_gate` / `h7_workflow_terminal_gate` strict markers。
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
  稳定 ID 和 no-replay。冻结的 12-case 文件按真实实现 owner 拆成两个**阶段门而非删减门**：
  4 个 `h7_runtime_gate`（Provider/ReAct/Tool、restart reconciliation、attached child -> parent ->
  root terminal -> delivery）由 T3.1–T3.3 负责；8 个 `h7_workflow_terminal_gate`（strict public
  terminal、6 个 invalid state、legacy byte shape）由 T4.3 负责。T3.3 退出时前 4 个必须
  GREEN；后 8 个不得 skip/xfail，必须由 verifier 逐 nodeid 证明唯一 RED 原因为
  冻结的 T4.3 pending-symbol 集合：缺 `simple_harness.workflow.native.NativeWorkflowExecutable`
  和/或 `simple_harness.workflow.errors.InvalidStatePatch`；即使 T4.1 已创建 package，也只允许这两个
  T4.3 authority 尚缺，不接受 fixture/assertion/其他 import error。T4.3 退出时后 8 个必须 GREEN，且最终全 12
  GREEN。verifier 必须先 collect 并精确断言 runtime=4、workflow=8、全集=12；stage=`runtime`
  要求 runtime pytest exit 0 且 workflow pytest exit 1/逐 nodeid exact reason；stage=`workflow`
  要求两组和全集均 exit 0。marker 未注册、case 漂移、额外 error 或普通 pytest failure 均 fail
  closed。T3.0 首个字符串 oracle commit 仅是中间 RED checkpoint，不得作为 T3 通过依据。
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

- 文件：`runtime/{child_runs,child_coordinator,child_signal_runtime,user_continuations,reconciler,kernel}.py`，
  T2.2/T2.6 SQLite UoW continuation-aware commit methods。
- 改法：child 入口只接 `ProfileLaunchTicketRef`；恢复 lease/epoch；parent signal/continuation FIFO；
  startup reconciliation顺序 provider -> effects -> child signals -> deliveries -> recoverable Run。
  `ChildSignalRuntime` 只能消费 T2.3 durable head-of-line signal claim lease，处理成功后
  用记录中的相同 owner/claim_epoch ack；crash/reopen 保留 claim 并等 lease 到期 reclaim，
  未过期 head 不得被后续 signal 超越，禁止进程内 list/lock 作为 authority。
  Runtime 必须公开 typed `children.launch(ChildLaunchRequest) -> ChildRunHandle`：facade 只调用
  `claim_profile_launch_and_commit_child`，再由同一 Runtime 对返回的 durable child run 获取
  ExecutionLease/RunFence 并 schedule，返回 handle 中的真实 `RunRecord`；禁止测试/Host 直调
  私有 `_activate/_schedule`。Runtime 还必须公开 `reconcile()`，严格执行 startup reconciler后
  `recover()`，以及公开 `dispatch_deliveries_once()`，不得让 conformance 访问 `_ports`。
  Kernel 对 `parent_run_id is not None` 的 child terminal 分支不得调用 root terminal API：ATTACHED/
  ROOT_TERMINAL_CHILD child 的 COMPLETED/FAILED/CANCELLED 也统一调用
  `finalize_child_and_enqueue_parent_signal(..., run_fence, execution_lease, now)`，以
  `(child_run_id, child version, terminal state)` 派生
  stable command/signal/event IDs，并在同一 transaction 写 child terminal + 唯一 parent signal；
  transaction 还必须落 child terminal fence receipt 并 CAS release 当前 RunFence；terminal 后不得遗留
  active fence。每个 child terminal command 的第一步按 stable command/signal identity + terminal
  outcome hash 只读已提交 receipt：完全相同即使 fence 已 released 也返回原 signal/outcome；identity
  或 hash 不同立即 conflict/零写。只有没有 receipt 时才校验后述双 fence并写入，绝不生成第二条signal。
  producer transaction 必须从 durable `run_links` 读取唯一 attachment policy（不信任 caller/内存
  branch），并先校验 active `workflow_leases`、current `run_fences` 与 runtime epoch 三方等式；
  DETACHED 使用同样双 fence 的 `commit_detached_child_terminal`，但不创建 parent signal。ATTACHED
  由 parent Driver消费 signal 后继续；ROOT_TERMINAL_CHILD 由 parent Driver消费同一 durable
  signal 后直接把 child terminal projection 作为 root terminal。DETACHED child 只原子 terminalize
  自身且不 signal parent；root (`parent_run_id is None`) 仍走 fenced root terminal + delivery path。
  三种 child terminal state、attached/root-terminal-child/detached、逐
  write-point crash/reopen 都要覆盖；DETACHED terminal 同样落 fence receipt/release fence，并采用
  receipt-first exact replay。
  等待态 parent 被 durable continuation 唤醒时，Runtime 用当次 `ExecutionLease` 按 FIFO claim 至多
  一个 continuation，将该 immutable `ContinuationRecord` 放进 `DriverInvocation.continuations`。
  clean schema v1 的 continuation row 必须补 `claimed_by/runtime_lease_epoch/claim_epoch/
  ack_receipt_id` 与 durable progress receipt。continuation claim **不另设独立 TTL**；其有效性完全绑定
  `workflow_leases(run_id, runtime.kernel, owner, runtime_lease_epoch, expires_at > now)`，所以 Runtime
  heartbeat 续 ExecutionLease 即同步维持 claim，避免双租约漂移。每次首次 claim、或原 runtime lease
  已失效/epoch 已被接管后的 reclaim，原子递增独立 claim_epoch。每个 Run 只允许 oldest non-acked
  HOL：head 仍绑定 active runtime lease 时返回 None，禁止越过它 claim 后续 pending；只有该 runtime
  lease 已失效/换 epoch，当前新 ExecutionLease 才能 CAS reclaim。
  Driver 返回 WAITING 时只允许
  `commit_runtime_state_and_ack_continuation(..., continuation_claim, execution_lease, receipt_id, now)`；
  terminal 时只允许
  `commit_root_terminal_with_deliveries_and_ack_continuation(..., continuation_claim,
  run_fence, execution_lease, receipt_id, now)`。两个 command 都必须在单个 `BEGIN IMMEDIATE` 内完成；
  每个 atomic progress command 的**第一步**必须按 `receipt_id` 只读 durable progress receipt：若
  claim identity（continuation/owner/runtime epoch/claim epoch）与 outcome hash 完全相同，即使 Run
  已 terminal、旧 lease/fence 已释放，也只读返回原 outcome；receipt identity 或任一 hash 不同立即
  conflict/零写。只有没有 receipt 时，才校验 active Runtime lease、current RunFence（terminal path）、
  continuation owner+runtime epoch+claim epoch，再同时写 ack receipt
  与对应 parent state/event（terminal 还包括 delivery outbox）；不得暴露“先 ack 后 progress”或
  “先 progress 后 ack”的 Runtime public path。same receipt + same claim/outcome hash 重试返回原 outcome，
  异 receipt/owner/runtime epoch/claim epoch/version/payload 拒绝；逐 write-point crash/reopen 后只能
  全回滚或全提交。无 continuation 的原 root path 继续走现有 fenced command。
  WAITING progress+ack commit 后 Runtime 必须立即重查同 Run 的 eligible HOL；若已有 pending 或
  reclaimable continuation，则在同一 active owner lease 下重新 schedule（不得等待外部 signal/
  `reconcile()`）。若 head 仍绑定另一个 active Runtime lease，则当前 owner 返回 None；该 lease失效或
  takeover 后由 normal Runtime recovery/reconcile 重查；
  terminal Run 的剩余 continuations 必须 durable quarantine/拒绝再 claim，不能重新唤醒 terminal。
  每个携 claimed continuation 的 Driver 出口必须封闭：正常 WAITING/terminal 走上述两个 atomic
  progress command；普通 exception 走 continuation-aware FAILED terminal + ack（同 receipt-first/
  double-fence 规则）。Runtime close 或 task cancellation 若尚未 commit，不得用无-continuation terminal
  path；必须先隔离/cancel task，再停止 heartbeat并 release 该 Run 的 ExecutionLease/RunFence，使旧
  claim 立即可被新 runtime epoch reclaim。non-cooperative task 超过 bounded close timeout 时也按该顺序
  释放 authority；遗留 task 后续所有 progress/handoff 被 stale lease/fence 拒绝。禁止保留 active
  Runtime lease + orphan claimed continuation。
- tests：`test_root_two_children.py`, `test_child_restart.py`, `test_continuation_restart.py`,
  `test_startup_reconcile_order.py`；另补 public child launch/schedule、public reconcile/delivery、
  continuation -> DriverInvocation -> atomic WAITING/terminal ack 的 fault/reopen matrix；另测两条
  continuation 的 HOL 不越序、claim crash/runtime-lease失效/reclaim、同 owner runtime epoch+1 携旧 claim零写，
  两条预先 queued continuation 不手动 reconcile 而按 FIFO 各消费一次并最终 terminal、terminal 后
  残余 continuation 被 quarantine 且 Driver 不再启动；虚拟时钟跨多个原 claim 时刻但 ExecutionLease
  heartbeat 持续 renew 时，长 Driver 只 commit 一次、无重复 Driver；child producer 补 stale owner、
  同 owner runtime epoch+1 + old fence、caller policy mismatch 的 child/event/signal 零写；progress
  after_commit 关闭DB/reopen后用旧 lease + exact receipt 只读成功、异 receipt/hash 零写，并由 T3.0
  四个 runtime seam assertions验证闭环。child attached/root-terminal/detached after_commit reopen 的
  exact receipt 只读成功、异 payload 零写且 terminal 后无 active fence；claimed-continuation Driver
  普通 exception、cancel、non-cooperative timeout 三案必须无需等自然 TTL 就可由新 epoch恢复，且
  progress/terminal/signal 不重复。
- 依赖：T2.3–T2.6、T3.1。

### T3.3 — ReAct Driver / termination budgets [AC-3..6]

- 文件：`runtime/drivers/{react,react_loop}.py`,
  `runtime/{context,termination,react_checkpoint}.py`，`providers/reconciliation.py`，
  `execution/{dispatch,provider_invocations,effects}.py`、`tools/{executor,reconciliation}.py`，以及
  T2.1 clean schema v1 / T2.2 SQLite UoW 的 checkpoint、wait-blocker、resolution、reauthorization CAS。
- 改法：把现 `AgentLoop` state machine改用 Context/Provider/Tool/Authorization/Trace Ports；Provider
  每 turn 只经 T2.4 coordinator；Tool只经 T2.5；max turns/calls/wall/cost/repeat hard gates写代码。
  `workflow_checkpoints` 的 `react.termination.v1` namespace 是唯一 durable termination authority，
  checkpoint 包含 version/lease_epoch、Unix epoch wall-clock `started_at/last_observed_at`、
  `provider_turns_reserved_total/tool_calls_reserved_total/repeat_key/repeat_streak`、phase、
  ProviderRequest canonical snapshot + fingerprint + context revision、ProviderResponse/tool-call order
  canonical snapshot + digest，以及每个 Tool result append progress。每个 Provider 轮次在出站前先用
  owner+lease_epoch+expected_version CAS 到 `provider_reserved` 并递增 turn，RequestId 只由
  `(run_id, durable turn ordinal)` 派生；每个 Tool batch 在 prepare 任何 effect 前，
  按 Provider response 中的 call order 对每个 call 逐个模拟预算，`repeat_key` 冻结为
  `tool name + SHA-256(canonical JSON arguments)`。只有整个 batch 都不超限时才用一次
  CAS 原子写入新 total/key/streak 并进入 `tools_reserved`；任一 call 超限则
  checkpoint/effect/handler 全部零变化。恢复时必须先用同一
  request/effect identity 读取或 reconcile T2.4/T2.5 ledger，完成后用 T3.1 的稳定
  `append_id` 幂等写 context，再 CAS 到下一 phase；`unknown` 禁止分配新 turn/effect。
  恢复必须严格按 phase 执行 `ledger read/reconcile -> assistant append -> ordered effects -> each result
  append -> ready CAS`：`provider_reserved` 先按 checkpoint 中冻结的 exact ProviderRequest 读同一
  invocation ledger，terminal response只读返回，绝不得用已变化的最新 Context重建同 RequestId；
  `tool_batch_reserved` 已有 frozen response/tool-call snapshot，绝不得再次调用 Provider。每个 append
  用稳定 receipt推进 checkpoint progress，因此 assistant/result append前后 crash均只读 replay。
  checkpoint 对每个 Tool call 同时持久化 `raw_provider_call_id` 与 bounded internal ID（canonical tuple
  SHA-256，display prefix可读但总长受限）。Effect ledger、append receipt、SDK correlation使用 internal
  ID；Provider-facing assistant tool-call 与 Tool result `Message.call_id` 必须保持 raw ID；恢复时验证
  raw/internal/turn ordinal 映射。同 Run 不同 turn 重复 raw ID是两个不同 effect，同 turn duplicate
  raw ID fail closed。
  `RuntimePorts`/`RuntimeServices` 增加 required typed `ProviderReconciliationPort`。其 observation 为：
  `completed(response, usage/cost evidence, evidence_ref)`、`confirmed_not_started(evidence_ref)`、
  `still_unknown(evidence_ref)`；completed 必须匹配原 RequestId/request fingerprint/target digest，可信
  usage 仍按 frozen estimator/policy计算。Provider response + budget settle + resolution receipt 必须同一
  transaction，禁止不匹配 late response污染 Context或budget。
  Provider/Tool unknown 的等待协议使用 durable `run_wait_blockers` + `reconciliation_resolutions`，不靠
  时序通知。Provider invocation/effect ledger 增加 explicit `handoff_attempt`，每次物理 handoff前原子
  +1，UNKNOWN 保留该 immutable uncertainty epoch；官方 policy 对 confirmed_not_started 最多允许一次
  re-handoff，并用 durable `rehandoff_count` 约束。Kernel 在一个 transaction 提交 WAITING +
  blocker(kind, ledger identity, handoff_attempt/observed unknown version)；
  reconciler 对 completed 在一个 transaction settle ledger + unique resolution receipt；对
  confirmed_not_started 只验证 ledger identity/version/attempt并写 evidence-bound resolution，原 ledger
  保持 UNKNOWN/不可dispatch，不能在尚未恢复Runtime authority时reset。无论先后，blocker创建时
  必须查已有 resolution，resolution写入时也更新已有 blocker。recoverable-WAITING只枚举
  `resolved AND wake_unconsumed`；resolution identity/唯一键固定为
  `(kind, ledger_identity, handoff_attempt)`，blocker必须引用同一 epoch，同 epoch same outcome hash幂等、
  异 outcome conflict，旧 epoch resolution绝不得resolve新 blocker。still_unknown不写 actionable
  resolution，因此保持WAITING、零调用、零自旋。completed恢复；confirmed_not_started按 durable
  rehandoff_count最多允许同 logical identity一次重新 handoff，不能靠禁止后续 epoch resolution限次。
  wake 不得先 consume 再在内存 schedule：`consume_resolved_wait_and_claim_activation` 必须在一个
  `BEGIN IMMEDIATE` 内校验 blocker resolved/unconsumed并分支处理 Runtime lease：同 owner的 active
  ExecutionLease 原子 renew/reuse同 epoch；没有 active lease或已expired才 claim epoch+1；active异owner
  不得偷取。随后同事务 CAS Run `WAITING -> RUNNING`、写 activation receipt并标 wake consumed。
  exact activation receipt重试只读返回同 lease/outcome；提交后即使 schedule前崩溃，普通 running
  recovery仍能接管。Runtime 随后必须在任何 Provider reset/Tool reauthorization/handoff前，以该
  ExecutionLease acquire/reuse匹配 `runtime_lease_epoch` 的 RunFence；same-process wake复用原 active
  fence，新 epoch takeover使fence epoch+1。旧 Runtime lease失效后新 owner/epoch可 reclaim，不能形成
  永久 claimed wake或让内存中的旧lease/fence继续出站。
  每个持有 WAITING Run active lease 的 Runtime 必须运行 durable wake-drain（可与 heartbeat同一个
  physical task，但 drain enable/disable/join control 必须与 heartbeat续租生命周期逻辑独立可测）：
  只扫描该 owner+epoch 且 `resolved AND wake_unconsumed` blockers，
  所以 still_unknown 不自旋。非owner reconciler只写 durable resolution；active owner wake-drain自动
  same-epoch consume/activate/schedule，无需外部手动 `reconcile()`。close先disable并join drain分支，
  但 heartbeat续租必须继续；然后按既定 cancel/隔离并join Driver，Driver安全退出后才停止/join
  heartbeat physical loop并release authority。若两者合并为一个task，disable drain不得终止task或
  heartbeat分支。新owner随后才能takeover。
  Effect recovery 必须先用 turn-scoped EffectId读取 ledger，并在返回/重concile前比对 run、turn ordinal、
  internal CallId、tool name、canonical arguments/request hash；任一不匹配conflict。Tool reconciliation
  直接消费 immutable `EffectRecord`，不能先依赖当前 Registry/Authorization。terminal匹配后直接返回
  原结果。confirmed_not_started 不得先 reset成携旧授权的 dispatchable PREPARED；resolution/wake先恢复
  active Runtime lease + matching RunFence，ReAct再做当前 Registry validation + fresh Authorization，
  然后由 `reauthorize_effect_not_started` 在一个 transaction CAS 原 unknown/handed_off version +
  matching resolution attempt/evidence、绑定 current fence/fresh authorization receipt并转为 PREPARED。
  crash在fresh authorize前后都保留不可dispatch旧state；只有该CAS成功才可handoff。若 crash发生在
  PREPARED CAS成功后、physical handoff前，新 epoch恢复必须 ledger-first识别“无handoff receipt的
  PREPARED”，fresh authorize后调用 `refresh_prepared_effect_authority`，CAS frozen intent、matching
  resolution attempt、effect version、handoff_attempt未增长、current Runtime lease/RunFence与fresh
  receipt，只更新 fence/auth authority而不增加handoff_attempt；旧 fence/receipt不得出站。随后才
  mark handed_off。after-commit exact refresh receipt幂等只读，异payload conflict。Provider
  confirmed_not_started也先resolution/wake，再由ReAct用 frozen request + 新active lease/fence CAS
  原 UNKNOWN 到同 identity retry-ready state；reconciler自身不得无authority重发。
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
  并断言任一 call 超 max Tool/repeat 时 effect/handler 为 0。补 assistant append、tool batch reserve、
  每个 result append前后 close/reopen；跨 turn重复 raw call ID；已terminal effect后授权策略改deny仍
  ledger-first返回原结果；Provider/Tool unknown 的 completed/confirmed_not_started/still_unknown late
  evidence各自 wake矩阵，尤其覆盖 resolution-before-WAITING lost-wakeup；still_unknown零自旋，两个
  reconciler并发只恢复一次。Provider completed验证response/request/target/usage-budget绑定与同事务
  resolution；Tool terminal intent mismatch拒绝；confirmed_not_started在fresh authorize前后 crash不留
  dispatchable旧receipt；两个turn重复raw ID产生distinct effects但Provider-facing Tool messages仍用raw ID。
  wake activation逐 write-point/after_commit response loss/commit后schedule前进程终止均可恢复；Provider
  和 Tool 都覆盖第一次 unknown -> confirmed_not_started -> 第二次 handoff/unknown -> late completed，
  两个 uncertainty epoch/resolution、同一 logical identity、物理 handoff最多两次、最终只恢复一次。
  same-process late evidence复用当前 lease/fence epoch且heartbeat持续；close/reopen或expired lease走
  epoch+1新fence；activation commit后schedule前crash、新epoch提交但fence acquisition前crash均可恢复，
  旧heartbeat/旧fence后续ledger/物理调用为0。另测A持WAITING active lease持续heartbeat、B写resolution
  且activation被拒时，无手动A.reconcile，A wake-drain仍只恢复一次。Tool补reauthorization CAS
  after-commit响应丢失、CAS成功后handoff前close/reopen：新epoch refresh authority不增加handoff_attempt，
  旧fence/receipt物理调用0、最终handler最多一次。补 combined wake-drain+heartbeat task 的
  non-cooperative close：drain停止后Driver隔离完成前lease持续renew，最后loop才终止且无task泄漏。
- 依赖：T1.2–T1.3、T2.4–T2.5、T3.1。

### T3.4 — SDK runtime conformance surface [AC-4, AC-5, AC-8]

- 文件：`testing/{models,fakes,provider,tool,runtime}.py`, `testing/__init__.py`。
- API：`ConformanceHost(provider_factory, tool_factory, runtime_factory, paths_factory)`；
  `run_conformance(host, suites)->ConformanceReport`，protocol=`simple-harness-conformance.v1`。
- tests：SDK自身 fake host 执行 no-tool/one-tool/multi-tool/cancel/restart/duplicate/unknown；JSON report
  schema snapshot。
- 依赖：T3.2–T3.3。

## S4 — Workflow runtime / selection

### T4.1 — Native compiler/runner/checkpoint/control ports [AC-5, AC-7]

- 文件：`workflow/{contracts,definition,compiler,runner,checkpoint,lease,recovery,replay,trace,control,execution_ports}.py`，
  以及T2.1 clean schema v1 / T2.2 SQLite UoW 的 workflow checkpoint/adapter operation receipt 与
  `terminal_projection_prepares` authority（本任务是这些Workflow-owned表/命令的唯一owner）。
- 迁移基线：这是 frozen sources 的 full semantic extraction，不是 greenfield rewrite。以
  `backend/deskpet/workflows/{contracts,definition,runner,recovery,replay,control,execution_ports}.py` 为
  behavior oracle，除已批准 `BC-SDK-IMPORTS`、Host dependency Port化、H14明确新增的 pure-interrupt 与
  same-transaction/receipt约束外，保留所有 reusable public/private行为、构造签名、状态枚举、validation、
  manifest/hash、retry/loop/HITL/quarantine/replay/recovery语义；不得因 focused tests未覆盖而删除或
  用小型替代实现。`compiler/checkpoint/lease/trace` 可以从源模块拆分职责，但拆分前后行为必须由迁移的
  source oracle assertions与API snapshot证明等价。
- 改法：完整兑现 frozen symbol disposition 的非-native Workflow authority，不能只实现 runner
  最小表面：`control.py` 必须提供 `ExecutionControl`、`WorkflowInterrupt`、`WorkflowSuspended`、
  `bind_execution_control`、`workflow_interrupt`；`execution_ports.py` 必须提供 public
  `WorkflowExecutionPorts` 与 private `CheckpointExecutionAdapter`，并由 runner 的真实 HITL/checkpoint
  路径消费，禁止成为未接线兼容壳。`CheckpointExecutionAdapter` 的每个 execution-ledger write
  必须接收 checkpointer 已打开的 transaction handle，并与对应 checkpoint/head/decision/effect/finalize
  写入同一个 commit/rollback boundary；adapter 不得 commit、不得另开 connection、不得持有 hidden
  concrete store。逐 write-point fault 后 close/reopen 必须全回滚或全提交，不能出现 checkpoint 与
  execution ledger 分叉。`WorkflowRunner.__init__` 必填
  registry/checkpoint/lease/recovery/trace/`WorkflowExecutionPorts`，不再另收一份 UoW；canonical UoW
  只取 `execution_ports.unit_of_work`。checkpoint authority 必须一次性
  `bind_execution_adapter(execution_ports.checkpoint)`，并暴露进程内 opaque `transaction_owner`；adapter
  的 `transaction_owner` 必须用 object identity 与 checkpoint authority相同，否则构造时零写拒绝。
  adapter method 的第一个参数是 checkpoint authority创建且仍打开的 `WorkflowTransaction`；transaction
  只允许 owner commit/rollback，adapter只能在其中执行 ledger writes。禁止 A checkpoint + B adapter、
  重复 configure、构造 concrete SQLite或 hidden fallback。保留
  retry/loop/HITL/quarantine/replay语义。contracts/definition/recovery/replay/runner/control/
  execution_ports 的 frozen target symbol 必须全部存在且由 API snapshot/symbol disposition gate校验。
  `CompiledWorkflow` 继续拥有 definition-derived pure graph operations：node/route lookup、patch validation与
  reducer merge、edge/join/activation/cycle budget计算；`WorkflowRunner` 只拥有registry/lease/admission/
  recovery orchestration，materialize一个T4.3 `NativeWorkflowExecutable` 后委托执行，禁止自己成为第二个
  node task/frontier/retry/HITL state-machine owner。Runner与Native的delegation protocol/API snapshot必须
  明确，product cutover gate扫描并禁止SDK外第二个 Workflow state-machine owner。
  Conditional route selector是pure graph authority：`ConditionalEdge` 必须声明
  `selector_effect_policy="pure"`（缺失/其他值compile fail），调用时只传immutable state与
  `PureRouteContext`；该context只含immutable workflow/run/checkpoint/task/source identity、deep-copied validated
  state与checkpoint中冻结的logical timestamp，不含live clock、observer/progress或任何callable/Port。
  Engine可在selector返回/route receipt提交后，用engine-owned observer/progress发通知，但selector自身零
  callback authority。effectful routing必须拆成durable effect node +
  pure selector。selector结果canonical validate后，用stable
  `(run_id, checkpoint_id, task_id, source)` route operation identity同事务提交；selector调用后、route
  receipt前crash可以重算，但selector physical调用始终为0。
  Adapter 六类 write 都必须携 caller从 durable input派生的 `operation_id`；canonical `payload_hash`
  必须由 adapter 对该method全部会影响write或outcome的实际参数自行 canonicalize + SHA-256重算，禁止
  信任 caller传入hash或遗漏可写字段。若保留 caller expected hash，只能先constant-compare adapter重算值，
  不匹配零写。并在同 transaction 落 `operation_id UNIQUE` 的全局 receipt（receipt另存
  `adapter_method`，因此同id跨method也冲突）：`mark_running_on_claim` identity由
  `(run_id, checkpoint namespace, lease/claim epoch)` 派生；`consume_decisions` 由
  `(run_id, checkpoint_id, canonical ordered decision ids)` 派生；`open_decision` 由
  `(run_id, interrupt_id)` 派生；`materialize_intent` 由 `(run_id, stable intent id)` 派生；
  `link_effects` 由 `(run_id, checkpoint namespace, checkpoint_id, canonical ordered effect ids)` 派生；
  `finalize_run` 由 `(run_id, terminal checkpoint_id)` 派生。receipt保存method、identity、adapter重算的
  payload hash与
  serialized outcome；after-commit重试先读receipt，同key+同hash只读返回原outcome，即使lease/head已推进，
  同key异hash或跨method复用key均零写 conflict；并发跨method同id只能一个transaction winner，另一方
  整体回滚。禁止 caller随机 UUID或adapter按重试时间生成新identity。
- tests：`test_compiler.py`, `test_runner_fault_matrix.py`, `test_checkpoint_reopen.py`,
  `test_lease_quarantine.py`, `test_replay.py`；另补 control suspend/resume/interrupt 与 execution ports
  checkpoint adapter真实接线测试，证明 missing authority fail closed、重复 interrupt/reopen 幂等且
  runner 没有绕过 Ports；注入 A/B mismatched checkpoint/adapter transaction owner、重复 bind 或额外
  hidden UoW 必须在构造时零写拒绝；checkpoint
  transaction 内每个 adapter write 前后 fault、after-commit response loss、close/reopen 均验证 ledger
  与 checkpoint同生共死；六类method各覆盖 exact operation replay 与 same-key/different-payload conflict，
  对每个method逐一变更每个可写/影响outcome字段都必须触发adapter重算hash conflict；另测两个并发
  method复用同global operation id只有一个winner，loser ledger/checkpoint零写，并断言 receipt/outcome
  identity未变化。另迁移 frozen source 的 contracts/definition/runner/recovery/replay assertions（只允许
  import/factory/明确H14 transform）；fail closed 比较 source public surface与SDK disposition targets，
  并覆盖完整 `WorkflowManifest` hashes、SCC/cycle budget、recursion/superstep、channel writer/reducer、
  tool/prompt/policy/callable-source validation、runner start/resume/cancel/recover/precreated paths，禁止用
  仅能通过新写happy tests的小引擎替代。
- H16 lifecycle/recovery/replay closure（本任务完成门，不能以A-slice/Native GREEN替代）：
  - public authority surface先冻结：`execution_ports.py` 新增 immutable typed
    `WorkflowActivation(execution_lease: ExecutionLease, run_fence: RunFenceLease,
    workflow_lease: WorkflowLease)`、`StartAdmissionRequest/Receipt/Mode/Phase`、
    `ResumeAdmissionRequest/Receipt/Phase/CommitBinding`、`CancelWorkflowRequest/Outcome/CancelConvergenceLease`、
    `ForkRequest/ForkReceipt/ForkPhase/ForkWriteLease`、`DangerousEffectObservation/Confirmation`、
    `RecoveryCandidate/RecoveryOutcome`；新增 required Protocol
    `WorkflowLifecyclePort`、`WorkflowRecoveryStorePort`、`WorkflowReplayPort`，并作为
    `WorkflowExecutionPorts(unit_of_work, checkpoint, lifecycle, recovery, replay)` 必填字段。五个authority的
    `transaction_owner` 必须与canonical UoW object identity一致。每个mutation method第一参均为现有仍open的
    `WorkflowTransaction`，显式接收expected version/head/lease/`now`与stable operation/receipt id；Port只做
    durable read/CAS，策略仍由SDK Runner/recovery/replay拥有。API snapshot + structural Host fake必须实现全部
    public methods；禁止 `getattr`、concrete SQLite、consumer callback policy或hidden fallback。
    typed fields/invariants也属于public snapshot：`StartAdmissionReceipt(request_id, request_key,
    request_fingerprint, run_id, phase, version, claim_owner?, activation?, serialized_outcome?)`且
    `StartPhase=ADMITTED|CLAIMED|RUNNING|SETTLED`；`ResumeAdmissionRequest(receipt_id, run_id,
    expected_run_version, expected_checkpoint_head, pending_interrupts[(id,payload_hash)], responses,
    responses_hash)`；`ResumeAdmissionReceipt`保存request全部immutable identity、phase/version、claim
    owner+epoch/expiry、activation与serialized outcome；`CancelWorkflowRequest(cancel_id, run_id, reason,
    expected_generation)`/`CancelWorkflowOutcome(cancel_id,generation,phase,blocker_ids,terminal?)`；
    `ForkRequest(fork_id,fingerprint,source_run_id,source_namespace,source_checkpoint_id,source_run_version,
    source_head,engine/manifest/implementation/schema hashes,canonical patch,dangerous_confirmation?)`，
    `ForkReceipt`保存完整request identity、stable child run/trace/checkpoint IDs、phase/version/claim与outcome。
    所有hash由SDK从typed content重算，caller supplied值只可constant-compare。
    `WorkflowLifecyclePort` exact mutation surface为
    `admit_start_standalone(transaction, request, *, now, fault?) -> StartAdmissionReceipt`、
    `admit_start_precreated(transaction, request, execution_lease, run_fence, expected_generic_run_version,
    *, now, ttl_seconds, fault?) -> StartAdmissionReceipt`、
    `claim_activation(transaction, run_id, expected_run_version, owner_id, *, now, ttl_seconds, fault?) ->
    WorkflowActivation`、`bind_activation(transaction, run_id, expected_run_version, execution_lease, run_fence,
    *, now, ttl_seconds, fault?) -> WorkflowActivation`、`renew_activation(transaction, activation, *, now,
    ttl_seconds, fault?) -> WorkflowActivation`、`release_activation(transaction, activation, expected_run_version,
    outcome, *, now, fault?)`、`request_cancel(transaction, request, expected_run_version, activation?, *, now,
    fault?) -> CancelWorkflowOutcome`、
    `admit_resume(transaction, request, *, now, fault?) -> ResumeAdmissionReceipt`、
    `claim_resume_standalone(transaction, receipt_id, expected_receipt_version, owner_id, *, now, ttl_seconds,
    fault?) -> ResumeAdmissionReceipt`、
    `claim_resume_precreated(transaction, receipt_id, expected_receipt_version, execution_lease, run_fence,
    *, now, ttl_seconds, fault?) -> ResumeAdmissionReceipt`、
    `settle_resume(transaction, binding, activation, committed_checkpoint, outcome, *, now, fault?) ->
    ResumeAdmissionReceipt`、
    `claim_cancel_convergence(transaction, cancel_id, expected_generation, owner_id, *, now, ttl_seconds,
    fault?) -> CancelConvergenceLease`、
    `settle_cancel_convergence(transaction, cancel_lease, resolution_snapshot, terminal_checkpoint,
    terminal_event, deliveries, *, now, fault?) -> CancelWorkflowOutcome`；所有
    receipt-first方法以same id+same canonical payload返回原outcome、same id+different payload conflict。
    `WorkflowRecoveryStorePort`只提供`list_candidates(snapshot_cursor) -> (candidates,next_cursor)`,
    `read_recovery_snapshot(run_id) -> RecoverySnapshot`,
    `commit_recovery_outcome(transaction, candidate, expected_snapshot, outcome, *, now, fault?)`与
    `claim_resolved_recovery(transaction, blocker_id, expected_resolution_version, owner_id, *, now,
    ttl_seconds, fault?) -> RecoveryClaim`；`WorkflowReplayPort` exact surface为
    `read_fork(fork_id) -> ForkReceipt?`、
    `prepare_fork(transaction, request, expected_source_snapshot, *, now, fault?) -> ForkReceipt`、
    `claim_fork(transaction, fork_id, expected_receipt_version, owner_id, *, now, ttl_seconds, fault?) ->
    ForkWriteLease`、
    `checkpoint_fork(transaction, fork_lease, expected_target_head, checkpoint_operation_id, checkpoint,
    *, now, fault?) -> ForkReceipt`、
    `commit_fork(transaction, fork_lease, expected_receipt_version, *, now, fault?) -> ForkReceipt`、
    `rollback_fork(transaction, fork_lease, expected_receipt_version, reason, *, now, fault?) -> ForkReceipt`、
    `list_orphaned_forks(snapshot_cursor, *, now) -> (receipts,next_cursor)`。read/list返回immutable snapshot+
    version token；mutation CAS包括receipt phase/version、claim owner/epoch/expiry及source/target expected head。
    `claim_fork`允许PREPARED、expired CLAIMED，以及expired CHECKPOINTED；后者返回
    `ForkWriteLease(mode="commit_only")`且绑定receipt中已写checkpoint id/hash，`checkpoint_fork`见commit-only
    lease必须拒绝，只有`commit_fork`可消费，避免重写已提交checkpoint。
    fault label是稳定的`workflow:<method>:before_<table>_write/after_<table>_write/after_commit`，实现必须对
    该method实际写到的每张authority table枚举label，不允许只有一个笼统fault点。
  1. `RegisteredWorkflow.materialize(...)` 必须从canonical `WorkflowExecutionPorts` materialize真实
     `NativeWorkflowExecutable` + `SqliteNativeCheckpointStore`，store/checkpoint adapter/UoW 的
     `transaction_owner` 必须同一object identity；禁止注册或缓存预绑定 executable、fake checkpointer、
     global authority-bound executable。registry只可缓存immutable definition/factory metadata，每次Run按当轮
     Ports重新materialize。compiled graph必须用真实SQLite close/reopen运行通过。
  2. Runner所有 `start/resume/recover/precreated` 执行统一进入一个 `_execute` authority path：先以注入时钟
     claim/validate绑定后的`WorkflowActivation`，取得并持续heartbeat，沿Native/checkpoint/effect/terminal逐层显式传递
     owner+epoch fence；lease loss取消/隔离旧task且后续零写。WAITING、terminal、failure、cancel出口必须按各自
     durable outcome release/失效lease，不得绕过统一入口或由Host做pre-check。precreated入口必须从T4.2
     `DriverInvocation`显式传入原始`ExecutionLease + RunFenceLease`；`WorkflowLifecyclePort.bind_activation(
     transaction, run_id, execution_lease, run_fence, *, now, ttl_seconds)`同事务验证三者run/owner/runtime epoch、
     Runtime namespace/expiry与current run-fence row，派生/renew同owner+runtime epoch的`WorkflowLease`。独立
     `start/run`则只能调用`claim_activation(...)`让canonical Port在同事务取得Runtime lease、RunFence和派生
     WorkflowLease；禁止Runner自行claim第二套owner。每个checkpoint/effect pre-handoff/terminal都校验三份
     current row；post-handoff settlement仍按H11 ledger identity例外、不得第二次出站。
     WorkflowLease不是可独立漂移的第四个clock：它是Runtime lease的checkpoint namespace投影，owner、
     `runtime_lease_epoch`、expiry必须等于ExecutionLease；`renew_activation`在一个transaction同时renew两行，
     任一失败整体回滚。precreated mode不启动第二个workflow heartbeat：T3 Kernel仍是唯一Runtime heartbeat owner，
     T2.2 `renew_runtime_lease`/release SQL在存在workflow projection时必须同transaction renew/release matching
     projection；standalone mode才由Runner调用`renew_activation`。因此不存在Runtime续租成功而Workflow单独过期
     的合法状态，fault在任一row write都会全回滚。Provider coordinator/Provider handoff UoW与
     `EffectExecutor.execute`/Tool handoff UoW public
     API增加`workflow_lease: WorkflowLease | None`；generic ReAct可传None，但durable run driver_kind/profile为
     workflow时SQLite同一handoff transaction强制非None并验证run/owner/runtime epoch/workflow epoch/expiry、
     current RunFence三方关系，缺失/过期零transport/handler。Workflow context effect/provider adapter必须显式
     携immutable activation调用，禁止hidden mutable closure。补仅WorkflowLease expiry/mismatch而Runtime lease+
     RunFence仍active的零physical regression。
  3. precreated path必须经Port化 `_require_precreated` 从durable execution authority验证generic Run与
     workflow Run同一ID、driver kind、trace identity、lease owner/epoch；terminal则由
     `_assert_precreated_terminal` 验证workflow terminal checkpoint与generic terminal/event/delivery在同一
     transaction收敛。caller参数、内存对象或事后查询不能替代该绑定。
     precreated `run/resume`不得接收裸`owner_id`；必须接收当次`DriverInvocation`的typed
     `ExecutionLease + RunFenceLease`并走`bind_activation/claim_resume_precreated`。wrong owner、expired/old
     Runtime epoch、同owner旧RunFence或另一run的lease在admission/claim阶段零receipt、零checkpoint、零effect。
     start同样区分`StartMode.STANDALONE|PRECREATED`：precreated Runtime在Workflow Driver前已创建并激活generic
     Run，故只能调用`admit_start_precreated`，在同transaction验证现有generic Run/start snapshot/version及typed
     leases，同时创建workflow receipt/row与projection lease，直接落CLAIMED；不得再创建reserved generic Run，
     不存在admission后再bind窗口，standalone scanner也不得claim PRECREATED receipt。
  4. cancel是durable状态机：`lifecycle.request_cancel(transaction, request, expected_run_version,
     activation?, now)`先原子落 `cancel_requested`，同时release/increment current Runtime lease、RunFence与派生
     WorkflowLease，使任一旧writer三方epoch校验失败；已prepared/handed-off/
     unknown/late effect必须按现有effect reconciliation收敛为 `CANCELLING`/`BLOCKED` 或最终
     `CANCELLED`，禁止把未知物理结果伪报取消完成。precreated generic terminal event/delivery也必须与最终
     workflow cancel checkpoint同事务；逐write fault/reopen不得双terminal或漏delivery。
     cancel receipt带单调`cancel_generation`。若存在UNKNOWN/handed-off blocker，startup/late-resolution wake只能
     `claim_cancel_convergence`取得`CancelConvergenceLease(run_id,generation,owner_id,epoch,expires_at)`；该authority
     只允许observe/reconcile已有ledger与`settle_cancel_convergence`，所有node执行、Provider/Tool prepare/handoff
     及普通checkpoint方法见cancel状态或此authority均拒绝。late resolution绑定generation唤醒；settle同事务
     验证blocker全部resolved、写terminal cancel checkpoint+generic terminal/event/deliveries、释放cancel lease。
     ADMITTED/CLAIMED cancel claim逐write fault/reopen、expired claimant接管与exact receipt replay均须覆盖。
     这条同时修订T3 `runtime/kernel.py`与T4.2 Workflow Driver public seam，不能只改Workflow内部：新增typed
     `DriverCancellationCoordinator.cancel(run, start_snapshot, *, reason, now, recovery) -> DriverCancelOutcome`。
     official `driver_kind="workflow"`是SDK保留key，coordinator只由SDK exact
     `WorkflowRuntimeDriver`实现并在Runtime内部随verified official driver factory注册，绑定T4.2 pinned
     implementation fingerprint；Host不能在`RuntimePorts`传入/覆盖/删除该key，构造时发现重复、自声明同key、
     subclass/错误implementation identity立即零写拒绝。Host extension registry只允许非官方driver key。
     Workflow profile注册缺SDK-owned coordinator fail closed。Kernel `_cancel_run`、startup
     `recover`、`_drive`的pre-cancel/`CancelledError`以及`_terminalize_cancelled`遇durable
     `driver_kind="workflow"`时一律委托该coordinator，绝不直接写generic CANCELLED；coordinator返回terminal时
     Kernel还要从UoW验证terminal event关联同cancel-generation receipt，未terminal则保持CANCELLING/BLOCKED。
     generic fallback只允许明确非workflow driver。补live cancel、CANCEL_REQUESTED进程重启、UNKNOWN late
     completed三条真实Kernel trace，断言generic terminal只能由`settle_cancel_convergence` transaction产生。
  5. recovery orchestration属于SDK而非Host：固定startup扫描顺序、状态分类、manifest/implementation/hash/
     checkpoint-head验证与可修复head CAS、quarantine原因、Provider/Tool uncertainty policy。Recovery Port只
     提供durable read/CAS primitives，不能把分类策略回调给consumer。每一类用真实SQLite并发/fault/reopen
     证明exact receipt replay、异payload零写和quarantine后零执行。决策顺序与CAS tuple固定为：
     `(a)` RUNNING且Runtime/Workflow lease过期：CAS `(run state/version, runtime owner+epoch+expiry,
     workflow owner+epoch+expiry, run-fence epoch, expected head)` 到 RETRYABLE+recoverable receipt；
     `(b)` cancel_requested/cancelling先按第4项收敛；`(c)` 任一active未过期owner则只读skip；
     `(d)` manifest/implementation/descriptor/hash不匹配则BLOCKED `graph_version_unavailable`；
     `(e)` 非active Run的missing/stale head只允许从同run+namespace、最高committed revision CAS repair；
     `(f)` 读取/identity/hash/schema失败则保存canonical quarantine record并BLOCKED `checkpoint_corrupt`；
     `(g)` Provider/Tool handed_off/unknown优先进入已有resolution blocker，`still_unknown`保持WAITING且不可自旋/
     replay，completed/confirmed_not_started按H13 resolution epoch唤醒；`(h)` succeeded_pending最后标记
     `resume_pending_checkpoint`。每个outcome固定`previous_status/status/action/reason/receipt_id`；mutation CAS都绑定
     status+run version+两类lease epoch+expected head。补双scanner、scanner-vs-live Runner、late reconciliation
     交错，旧scanner不得回退新head或quarantine active Run。
  6. replay/fork必须只用canonical `WorkflowExecutionPorts`，并验证指定历史checkpoint的run owner、namespace、
     engine version、manifest/implementation hash、pending results/fanout、interrupt decisions、state/schema/type
     compatibility。危险确认不是bool：`DangerousEffectObservation(effect_id, kind, state, ledger_version,
     request_hash, handoff_attempt)`按effect_id canonical排序；`DangerousEffectConfirmation(scope,
     observations, digest)`由SDK重算digest。prepare同事务重读ancestor effect集合/版本并constant-compare；集合/
     状态/version变化必须conflict并要求重新确认，digest进入request fingerprint。`checkpoint_fork`和
     `commit_fork`（含commit-only reclaim）也必须在各自transaction重读相同source effect scope并比较receipt
     digest；若变化则原子把仍未公开的fork标`ROLLED_BACK(reason=effect_snapshot_changed)`并保留tombstone/
     reserved child不可执行，调用者须用新observation+confirmation创建新fork request，不能复用旧确认或公开旧
     checkpoint。fork使用durable prepare saga，
     稳定派生child run/trace/checkpoint IDs；
     after-prepare、checkpoint write前后、after-commit响应丢失的close/reopen都只能返回同child或整体回滚，
     不能生成第二child/trace或重放ancestor物理effect。`ForkPhase = PREPARED | CLAIMED | CHECKPOINTED |
     COMMITTED | ROLLED_BACK`，receipt保存request fingerprint、source run/namespace/head/version/hashes、stable child
     identities、phase/version/claim owner+epoch/outcome。`replay.prepare_fork(...)`原子写request receipt+child generic/
     workflow reservation；`claim_fork(...)`只CAS PREPARED或expired CLAIMED；`checkpoint_fork(...)`携operation id、
     source/target expected head与active activation，CAS CLAIMED->CHECKPOINTED；`commit_fork(...)`原子公开child并
     CAS COMMITTED。startup orphan scanner按expired claim接管；只有尚未公开且无checkpoint的permanent invalid
     prepare，或第6项最终risk snapshot变化且child始终reserved未公开，才可ROLLBACK并永久保留tombstone。
     exact retry逐phase读receipt返回/接管同一child，并发worker只能一个claim winner。
     `claim_fork`返回的`ForkWriteLease(fork_id,target_run_id,owner_id,claim_epoch,expires_at,
     expected_receipt_version)`是唯一目标checkpoint写authority；它不是source activation，source terminal也可fork。
     `checkpoint_fork`只校验reserved child+fork lease+target empty/expected head；COMMITTED前child generic/workflow
     rows标`reserved_fork`，Runtime/normal recovery扫描必须排除，不能claim/execute/deliver。checkpoint write
     after-commit响应丢失后receipt-first返回同CHECKPOINTED；lease过期可被双scanner中唯一winner以commit-only
     reclaim并公开同一child，不能重写checkpoint或生成第二child。
  7. start admission必须是同一transaction的request-key receipt：`StartAdmissionRequest` exact canonical字段为
     `request_key, mode, session_id, request_id, turn_id, profile_key, driver_kind="workflow",
     tool_catalog_generation, workflow_name/version, requested_run_id?, requested_trace_id?, requested_thread_id?,
     checkpoint_namespace, manifest_hash, implementation_hash, state_schema_version, terminal projection descriptor+
     request-factory hashes, start_input, capability_snapshot`。`start_input`是唯一caller state authority，先按
     compiled schema/limits canonical validate/freeze；SDK纯函数从它+全部pinned manifest/descriptor/capability/
     identity字段生成唯一immutable `StartSnapshot`，caller不得同时提交另一份snapshot。precreated Runtime已有
     start snapshot时，admission重算并constant-compare全部字段后才bind，mismatch零写。SDK从snapshot内部重算
     `capability_hash`，caller supplied hash若保留必须constant-compare。key与上述canonical payload hash相同只读返回同一
     run，key相同但任一payload字段变化fail closed；首次写同时冻结capability hash、start snapshot、manifest+
     implementation hashes、schema、trace/thread identities。覆盖并发双start、逐write fault、after-commit与
     close/reopen；禁止 `del request_key/capability_hash/trace_id` 或随机重建identity。
     `profile_key/driver_kind/tool_catalog_generation`只能来自T4.2 verified launch ticket/DriverInvocation并进入
     request fingerprint，不能从Host callback或事后snapshot猜测。start receipt使用`StartPhase`：standalone
     admission同事务写ADMITTED+reserved run，claim只可CAS到CLAIMED；precreated admission按第3项原子落CLAIMED。
     `SqliteNativeCheckpointStore.ensure_genesis`必须在其canonical transaction同时写genesis operation/head并CAS
     receipt CLAIMED->RUNNING，故RUNNING蕴含genesis receipt+head存在；禁止claim直接标RUNNING。startup scanner枚举ADMITTED与
     expired CLAIMED，按request receipt接管同一run；RUNNING/SETTLED exact retry返回相同run/outcome。覆盖
     admit after-commit且caller永不重试、genesis每个before/after write与after-commit，scanner仍启动同一run且
     不生成第二identity；无head的CLAIMED可恢复，无genesis的RUNNING视为不可能并fail closed quarantine。
     requested run/trace/thread id缺省时不在admission前生成随机值：SDK从canonical request fingerprint用固定
     namespace+domain separator确定性派生三者，并将resolved identities写receipt/start snapshot；若caller显式
     给值则该值进入fingerprint。并发exact start与after-commit reopen必须得到相同三identity。
  8. resume不是单一bool receipt而是versioned state machine：`ResumePhase = ADMITTED | CLAIMED | RETRY_WAIT |
     SETTLED`。
     `admit_resume(transaction, request, expected_run_version, expected_checkpoint_head, now)`以
     `(run_id, WAITING revision, canonical pending interrupt ids+payload hashes, canonical responses hash)`原子写
     ADMITTED receipt，并保存responses、target revision、request fingerprint；异revision/interrupt/response零写
     conflict。standalone走`claim_resume_standalone`，在同事务CAS ADMITTED或expired CLAIMED并取得新
     `WorkflowActivation`；precreated必须走`claim_resume_precreated`并携DriverInvocation原始typed
     ExecutionLease+RunFence，bind exact current rows后写同owner/epoch activation；两者并发均仅一个winner。
     `settle_resume(...)`与WAITING/terminal checkpoint outcome同transaction写SETTLED+serialized outcome。
     exact retry遇SETTLED只读返回原结果；ADMITTED由caller或startup recovery claimant首次执行；CLAIMED且owner
     active返回typed `IN_PROGRESS`，claim过期才由新owner接管。crash recovery后的首次执行允许继续，但所有物理
     effect仍经既有ledger stable identity去重；禁止笼统把ADMITTED当最终outcome或让Run永久stranded。
     Runner构造`ResumeCommitBinding(receipt_id, expected_receipt_version, target_run_revision,
     request_fingerprint)`并作为必填参数传入`NativeWorkflowExecutable.resume`；Native把binding沿drive传到第一次
     合法`commit_frontier`/interrupt/terminal commit，`SqliteNativeCheckpointStore`在同一open canonical
     transaction调用`lifecycle.settle_resume`，使decision consume、new head/outcome与receipt SETTLED同生共死。
     WAITING、terminal、非terminal advance都保存deterministic status/head/serialized outcome；Native resume
     返回后Runner不得第二transaction补settle。补每个checkpoint write前后/after-commit response-loss断言
     receipt与head永不分叉。
     binding不是只覆盖frontier happy path：Native所有会结束本次resume attempt的durable出口——`commit_retry`、
     task/node permanent failure、`commit_engine_failure`/max-supersteps、interrupt WAITING、frontier ADVANCED、
     terminal——都必须把同一binding传给store transaction并原子settle为deterministic
     `RETRYABLE|FAILED|WAITING|ADVANCED|TERMINAL` outcome+operation/head。仅`commit_task_result`等必然继续到下一
     durable出口的中间write可保持CLAIMED。`commit_retry`是唯一不直接SETTLED的出口：它同transaction把receipt
     CAS到RETRY_WAIT，保存immutable原responses+hash、decision ids、retry operation id/attempt、next_attempt_at与
     checkpoint head，并release当前activation。到期前exact caller只读返回typed IN_PROGRESS；startup/due scanner
     只允许一个claimant CAS RETRY_WAIT->CLAIMED，复用receipt内同一responses调用Native resume，decision与effect仍
     由既有operation/ledger receipt去重；再次retry更新同receipt attempt/next time。只有随后WAITING/ADVANCED/
     FAILED/TERMINAL durable出口才SETTLED。每种出口逐table before/after-write、after-commit reopen，加到期双claimant
     后receipt/head/failure或retry schedule一致，handler/node与decision/effect不重复执行。
     RETRY_WAIT保留原StartMode：standalone scanner走`claim_resume_standalone`；precreated只能先由Runtime recovery
     取得同run的typed ExecutionLease+RunFence，再走`claim_resume_precreated`，不得用scanner自声明owner或第二套
     workflow-only activation。
  H16 tests必须直接证明registry无bound authority cache、漏传/错owner store构造零写、heartbeat lease loss、
  precreated identity mismatch、cancel unknown convergence、repair/quarantine、fork dangerous confirmation、start
  并发幂等和resume CAS；所有关键命令含before/after write与after-commit fault、关闭数据库并以同路径重开。
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

### T4.3 — Native execution kernel / terminal projection / legacy compatibility [AC-5, AC-8]

- 文件：`workflow/{native,errors}.py`，`tests/conformance/test_full_runtime_seam.py` 的既有
  `h7_workflow_terminal_gate`（只允许补 marker/import factory，不改 assertion semantics）。
- API：完整保留 source `NativeWorkflowExecutable` 构造与 `ainvoke/resume/astream` 执行表面，并新增/保留
  public `terminal_intents(state, *, run_id, status, error, recovery_action)`；`errors.py` 完整拥有
  `WorkflowErrorCode`、`ErrorDisposition`/`ERROR_DISPOSITIONS`、`WorkflowContractError`、
  `WorkflowDefinitionError`、`InvalidStatePatch`、`StateMergeConflict`、`WorkflowDependencyUnavailable`、
  `AsyncOnlyWorkflowError`、`UnsupportedDeltaChannelError`、`LeaseLostError`、`WorkflowNodeError`，保留source
  inheritance、stable code/details、node error retry/default disposition与`to_envelope()` byte shape。原
  30-file/184-symbol disposition完全漏掉`errors.py`；H15 T0.5 supplemental oracle把整套error vocabulary
  明确列为SDK public authority，API snapshot冻结所有类型与constant。
- 迁移基线：`backend/deskpet/workflows/native.py` 是 executable behavior oracle，不得只创建同名类型或
  terminal facade。必须机械迁移 genesis/load/drive/frontier/task worker/route/retry/failure/interrupt/
  completion intents、stable task/activation/invocation identity、checkpoint operations与stream/resume，
  再把持久化/执行依赖接到T4.1 Ports；10个 disposition targets要有行为等价测试，不接受“symbol存在”。
  `NativeWorkflowExecutable` 是唯一 node task/frontier state-machine owner：ledger-first pending task recovery、
  bounded parallel frontier与deterministic error winner、StatePatch validate/merge/reduce、conditional route
  receipt、join activation/firing、cycle epoch/budget、retry/failure/engine-failure、max-supersteps、node-level
  interrupt以及completion/terminal atomic commit都在这里；T4.1 Runner只能delegate。
  Source中产品化 constructor dependencies做唯一 approved transform：删除 concrete
  `TerminalProjectionRegistry`/`AsyncTerminalCommitProjectionRegistry` 参数，替换为必填 generic
  `TerminalProjectionPort` 与 `TerminalCommitProjectionPort`（protocol由SDK `workflow/native.py` owner）。
  前者同步`project_public(workflow_name,workflow_version,raw,engine_status)`；后者先同步
  `lookup(workflow_name,workflow_version)->TerminalCommitProjector | None`，`None`明确表示legacy fallback到
  下述generic delivery/final projection，存在projector才异步调用`project(request, ProjectionContext)`。
  `ProjectionContext`只含run/workflow/checkpoint identity、validated immutable state摘要与deadline/clock，
  不含Provider/Tool/effect/artifact/delivery/任意Host callback Port；projector contract为deterministic pure
  computation，不得physical I/O。这里的clock/deadline都是start/checkpoint冻结的数值，不是callable/live
  time。返回值必须是同一bounded generic intent list + optional SHA-256 blob refs，完整通过delivery
  schema/size/depth/privacy/identity校验。Terminal capability是否存在不能由restart时registry现状推断：
  `WorkflowManifest` 与 immutable start snapshot必须持久化nullable `TerminalProjectionDescriptor`
  (`capability_id`,`version`,`projector_fingerprint`,`request_schema_hash`,`request_factory_hash`)及descriptor
  digest；`None`才是legacy。非None时每次lookup必须按exact descriptor匹配，missing/version/fingerprint/hash
  drift均fail closed，不得降级legacy。request factory是SDK-owned pure canonical function，输入字段/schema与
  hash进入descriptor；Host只实现projector，不能临时拼request。
  Projector output在terminal final transaction前先用stable
  `(run_id, terminal_checkpoint_id, descriptor_digest)` operation写 durable `terminal_projection_prepares`
  receipt，保存input hash、validated canonical output JSON/output hash与blob refs。prepare已存在时只读使用，
  不再调用projector；同identity异input/descriptor conflict。若crash发生在projector返回但prepare commit前，
  允许重算，因为projector是trusted pure且尚无外部/持久化结果；不声称能检测该窗口的非确定输出。首个
  成功prepare commit的输出成为唯一authority，随后terminal+delivery transaction原子consume该receipt，
  exact after-commit replay不重投影。Host可注入产品projector，SDK descriptor=None默认保留legacy。
  `TerminalProjectionDescriptor`、`ProjectionContext`、`TerminalCommitProjector`、两个Projection Ports与
  prepare receipt/result都是SDK public typed contracts；SQLite prepare/read/consume commands经T4.1
  canonical `WorkflowExecutionPorts` 暴露，Native不能访问concrete SQLite或另开transaction。
  `_freeze_public_progress`、DeepResearch-specific `_v2_observer_attributes/_emit_v2_metrics` 不进入SDK engine；
  替换为可选 `WorkflowProgressPort.freeze_patch(...)` 与 `WorkflowObserverPort.node_started/node_finished(...)`，
  未注入时只保留validated StatePatch且不发metrics，绝不得import/no-op产品DeepResearch代码。constructor/
  Ports/API snapshot与测试必须证明此exact transform，不能只保留method名字。
- 改法：`terminal_public` 采用严格 bounded schema，只允许 frozen metrics/diagnostic codes/
  skipped stage IDs/retry action，拒绝未知字段、负数、重复 stage、非 allowlist 值，且绝不投影
  topic/raw query 等私有 state；没有 `terminal_public` 的 legacy workflow 保持 frozen exact
  canonical JSON byte shape。实现属于通用 Workflow runtime，不包含 DeskPet 产品 schema。
  generic delivery不是产品字段：保留 source `values.delivery_intents` 的bounded contract，校验每项stable
  intent identity/event key/channel/payload并按canonical order生成delivery intents，再追加workflow.final。
  v0.1精确schema：list最多16项；每项只允许`intent_id`、`kind`、`channel`、`payload`，四字段都必填；
  `intent_id`为1..128字符且仅`[A-Za-z0-9._:-]`、全list唯一；`kind`为1..64字符同字符集且不得为`final`；
  `channel`为1..64字符同字符集；`event_key`由SDK唯一派生`terminal:{intent_id}`、`event_type`唯一派生
  `workflow.{kind}`，caller不得提供/覆盖。payload必须是JSON object，canonical UTF-8 bytes<=32KiB、最大
  nesting depth 8、总container items<=512；payload key禁止`topic`、`raw_query`、`prompt`、`messages`、
  `credentials`、`secrets`、`private_state`，并递归拒绝以`_private`/`secret_`开头的key。SDK按
  `(intent_id, kind, channel, SHA256(canonical payload))`排序；重复identity、未知字段、空channel/kind、
  超限/深嵌套/隐私key一律整批fail closed，禁止静默skip。
  terminal + delivery intents必须在同一 checkpoint/execution transaction落地，after-commit exact replay
  不重复。产品 terminal registry/metrics仍留Host adapter，不能因此删generic delivery。
  T4.3 同时是 T4.1 `control.py` 的 node-level consumer owner：`NativeWorkflowExecutable` 每次执行单个
  native task 必须构造 `ExecutionControl(task_id, durable_responses)`，在
  `bind_execution_control(...)` scope 内调用 node；捕获 `WorkflowSuspended` 后只能经 T4.1 canonical
  checkpoint/execution transaction提交 durable interrupt并终止本次 node，resume时同 stable
  interrupt id消费 response。Python coroutine不能跨进程恢复栈，因此 v0.1 明确冻结 interrupt-capable
  node 为 pure-before-interrupt：`NodeDefinition` 对 interrupt-capable node 必须声明
  `pre_interrupt_effect_policy = pure`，compiler拒绝缺失/其他值；`WorkflowContext` 在该node执行期间不注入
  Provider/Tool/任意physical effect Port。任意裸I/O/Host callback属于trusted Workflow code违反SDK
  contract；SDK不承诺也不伪装成Python沙箱，官方 profiles 与 conformance host禁止这种用法。
  需要 durable/non-idempotent effect 的workflow必须拆成 pre-interrupt durable effect node + pure interrupt
  node + post-interrupt node；前后 effect node沿正常 graph intent 进入现有 T2.5 EffectExecutor，不新增
  consumer-owned Port或测试fake去重authority。禁止伪称可以恢复Python continuation，
  禁止仅由 runner 外层 bind，禁止 native 绕过 `WorkflowExecutionPorts`/transaction adapter。
- tests：T3.0 冻结的 8 个 `h7_workflow_terminal_gate` 全 GREEN；
  `verify_full_runtime_stage.py --stage workflow` 还必须证明 4+8+全 12 GREEN，禁止 skip/xfail。
  另补真实 native node 调 `workflow_interrupt` 的首次 suspend + close/reopen + response resume测试；断言
  node-level bind有效、stable interrupt/decision identity、同 transaction rollback、exact replay一次，
  并用无 bind/错误 authority证明 fail closed。另构造三node
  `durable effect -> pure interrupt -> durable effect` 经真实 T2.5 ledger close/reopen，断言前置 effect稳定
  identity/物理调用一次、interrupt稳定resume、后置effect只在response后一次；任何 interrupt node声明
  非pure policy必须compile fail closed，且该node context不可取得physical effect Port。不得用内存counter
  要求不可能的coroutine续跑语义；API docs标明 trusted Workflow code / no raw I/O 边界。
  同时迁移 source native engine 的 genesis/ainvoke/astream/route/retry/parallel barrier/loop budget/
  interrupt/failure/completion oracle；close/reopen后从真实 checkpoint恢复，不能由InMemory fake自证；
  `NativeWorkflowExecutable` 缺任一 execution method或绕过T4.1 canonical Ports即FAIL。至少覆盖linear、
  conditional route、join、bounded cycle、parallel deterministic winner、retry、pending-result crash/reopen、
  route receipt crash、interrupt suspend/reopen/resume、max-step/failure、terminal+generic delivery atomic/no replay，
  并证明InMemory与durable store行为等价。`NodeTaskOutcome`等9个辅助target必须保留source types/fields/invariants
  （如StatePatch/NodeExecutionIdentity与pending progress），不能用未接线Mapping壳代替。
  errors source oracle/API snapshot覆盖全词汇；constructor transform测试用generic fake Ports证明product
  registry/DeepResearch metrics不可import且unknown projection fail closed；delivery逐字段mutation、17项、
  32KiB边界、depth/items、duplicate id、privacy keys、canonical order与terminal atomic rollback/reopen全覆盖。
  conditional selector-before-route-receipt crash用真实Provider/Tool ledger spy证明selector physical count=0；
  compiler拒绝非pure/missing selector policy。terminal commit projector覆盖lookup None legacy fallback、显式
  capability missing/version/fingerprint/request hash drift fail、narrow context无physical Port、request factory
  字段mutation、project-before-prepare crash重算但零外部调用、prepare-after-commit reopen不重投影、同prepare
  identity异input conflict、returned intent/blob逐字段mutation与prepare后/final commit前reopen。route tests另
  用mutable wall clock与callback spies证明selector拿不到live time/callable且reopen分支一致。
- 依赖：T3.0、T4.1。

## S5 — official Profiles

### T5.1 — durable_task graph/ports [AC-5, AC-7]

- 文件：`workflows/durable_task/{definition,state,ports,nodes,output_contract}.py`。
- 改法：节点固定 plan -> HITL -> execute batches -> test/audit -> bounded repair -> output audit ->
  terminal；completion只依据 plan/effect/output receipts；所有产品能力经 Proposal/CapabilityCatalog/
  Workspace/Artifact/Authorization Ports。
- tests：`test_durable_task_graph.py`, `test_durable_task_human.py`, `test_durable_task_recovery.py`,
  `test_durable_task_output_negative.py`。
- 依赖：T4.1–T4.3。

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
