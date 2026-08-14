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

### T0.6 — 冻结 WorkflowRunner H16 authority relocation receipt [AC-5, AC-8]

- 文件：`workflow-runner-h16-transform.json`、
  `$PRODUCT_REPO/scripts/acceptance/{verify_sdk_oracles,build_sdk_symbol_disposition}.py`、对应golden tests。
- 原因：H16将 frozen `backend/deskpet/workflows/runner.py::WorkflowRunner` 内部 `LeaseManager`
  claim/heartbeat/lease-loss 及 `transition_run` terminal lease-clear 行为迁到 canonical
  `WorkflowLifecyclePort`，以保留单一durable authority；
  同时执行期曾产生一个未发布的SDK草稿 `WorkflowLeasePort`，它不是frozen source symbol，
  不能伪造或改写原184-symbol disposition。
- 冻结：保持原184-entry/hash不变；supplemental receipt同时锁定同一source commit下
  `backend/deskpet/workflows/runner.py` SHA-256
  `11b4230b96166f07d849f487d9243dccc6085fa2b480e260555d7d107e27ba51`
  与 `backend/deskpet/workflows/lease.py` SHA-256
  `c973acd0c95e3c0a76eef24c797418f9322197d95d0dcc2c963863e28c9b2573`，
  `WorkflowRunner -> simple_harness.workflow.runner.WorkflowRunner` 已有target，并精确记录
  `WorkflowRunner.__init__`、`LeaseManager.claim/run_with_heartbeat/_heartbeat_loop`、`transition_run`
  的真实method/function inventory 到
  `WorkflowLifecyclePort.claim_activation|bind_activation|renew_activation|release_activation` 的行为等价映射。
  这是冻结验收要求的authority relocation，不改变claim/heartbeat/lease-loss/zero-stale-write
  行为，不属于放宽black-box oracle。未发布的 `WorkflowLeasePort` 标记为
  `unshipped_sdk_draft_delete`，不进source-symbol disposition。
- gate：验证两个source path/hash与上述真实method/function AST inventory、target、receipt exact
  mapping、Runner public signature无 `lease`、SDK无public
  `WorkflowLeasePort`、`WorkflowLease.runtime_lease_epoch` 必填，并执行heartbeat、lease-loss、
  precreated Kernel projection与stale-writer零写source-equivalence tests。
- 依赖：T0.5；必须在H16/T4.1提交前GREEN，不得修改原184 inventory hash。

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
  对T4.2 workflow_spawn child completion只批准两个窄命令。第一条必须在child signal恢复后、执行任何k+1 Tool前调用：
  `ack_continuation_and_continue_tool_batch(continuation_claim, child_wait_receipt, pending_completion,
  expected_checkpoint, execution_lease, run_fence, *, now, fault?) ->
  BatchContinueOutcome(disposition=CONTINUE|BUDGET_TERMINAL, progress_receipt)`。同一BEGIN验证public pending completion、batch
  cursor与current authority，CAS continuation CLAIMED->ACKED、child-wait CLAIMED->ACKED_COMPLETION_PENDING、写unique
  progress receipt，并把ReAct checkpoint推进`TOOL_BATCH_CONTINUE(next_ordinal,pending_completion_queue)`，保持parent Run
  RUNNING与lease/fence active；after-commit exact replay返回同receipt。它按H10同步观察started/last-observed/deadline/policy：
  rollback或deadline命中时写BUDGET_TERMINAL code，后续只允许synthetic closure；未命中才允许physical k+1。此command成功前
  k+1 Provider/Tool/WAITING/terminal全为0，因此nested spawn/UNKNOWN永不携旧continuation claim。
  第二条`commit_pending_child_completions_and_react_ready(expected_checkpoint, pending_completion_queue,
  result_append_receipts, execution_lease, run_fence, *, now, fault?) -> ReactReadyOutcome(READY|BUDGET_EXCEEDED,
  progress_receipt)`只能在全部raw ToolResults以physical或synthetic方式闭合后调用；按`(spawn_ordinal,terminal_receipt_id)`稳定
  顺序append全部pending child completion messages并把各child-wait `ACKED_COMPLETION_PENDING->ACKED`，再推进
  READY_FOR_PROVIDER或BUDGET_EXCEEDED terminal convergence。任一queue/batch/append/checkpoint/authority不匹配零写。
  除这两条外仍禁止running中途ACK，原WAITING/terminal/exception continuation-aware exit contract不变。
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

### T4.1 — Native compiler/runner/checkpoint/control ports [AC-5, AC-7, AC-8]

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
  execution ledger 分叉。H16 的三 authority lifecycle 裁决显式覆盖旧 source constructor：
  `WorkflowRunner.__init__` 必填 registry/checkpoint/trace/`WorkflowExecutionPorts`，删除独立
  `lease`、可执行 policy `recovery` 参数，不再另收一份 UoW；canonical UoW 与 durable recovery
  primitives 分别只取 `execution_ports.unit_of_work` 与 `execution_ports.recovery`；
  consumer 不得注入 recovery classifier/callback。canonical UoW
  只取 `execution_ports.unit_of_work`。checkpoint authority 必须一次性
  `bind_execution_adapter(execution_ports.checkpoint)`，并暴露进程内 opaque `transaction_owner`；adapter
  的 `transaction_owner` 必须用 object identity 与 checkpoint authority相同，否则构造时零写拒绝。
  adapter method 的第一个参数是 checkpoint authority创建且仍打开的 `WorkflowTransaction`；transaction
  只允许 owner commit/rollback，adapter只能在其中执行 ledger writes。禁止 A checkpoint + B adapter、
  重复 configure、构造 concrete SQLite或 hidden fallback。保留
  retry/loop/HITL/quarantine/replay语义。contracts/definition/recovery/replay/runner/control/
  execution_ports 的 frozen target symbol 必须全部存在且由 API snapshot/symbol disposition gate校验。
  `CompiledWorkflow` 继续拥有 definition-derived pure graph operations：node/route lookup、patch validation与
  reducer merge、edge/join/activation/cycle budget计算；`WorkflowRunner` 只拥有registry/admission/
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
    H16 同时是对 frozen `backend/deskpet/workflows/runner.py::WorkflowRunner` constructor/internal
    `LeaseManager` 以及 `backend/deskpet/workflows/lease.py::transition_run` terminal lease-clear
    行为的authority relocation，由 `workflow-runner-h16-transform.json` 补充冻结：
    保留 public immutable `WorkflowLease` value，其 `runtime_lease_epoch` 必填且不可为
    `None`；将原行为唯一映射为
    `WorkflowLifecyclePort.claim_activation/bind_activation/renew_activation/release_activation`。Runner、Host fake
    与 consumer 均不得注入或持有第四套 lease mutation authority。API snapshot 必须断言
    Runner signature 无 `lease`、SDK public surface 无 `WorkflowLeasePort`，且全部 workflow lease mutation
    只能通过 canonical lifecycle。`WorkflowLeasePort` 是未发布SDK的执行期草稿，直接删除并
    记录为 `unshipped_sdk_draft_delete`；它不是frozen source symbol，不进入也不改写
    `source-symbol-disposition.json`。
    typed fields/invariants也属于public snapshot：`StartAdmissionReceipt(request_id, request_key,
    request_fingerprint, run_id, phase, version, claim_action?, claim_owner?, claim_epoch?, claim_expires_at?,
    activation?, serialized_outcome?)`，`StartClaimAction=NEW|RESUME`且
    `StartPhase=ADMITTED|CLAIMED|RUNNING|SETTLED`；`ResumeAdmissionRequest(receipt_id, run_id,
    expected_run_version, expected_checkpoint_head, pending_interrupts[(id,payload_hash)], responses,
    responses_hash)`；`ResumeAdmissionReceipt`保存request全部immutable identity、phase/version、claim
    owner+epoch/expiry、activation与serialized outcome；`CancelWorkflowRequest(cancel_id, run_id, reason,
    expected_generation)`/`CancelWorkflowOutcome(cancel_id,generation,phase,blocker_ids,terminal?)`；
    `ForkRequest(fork_id,fingerprint,source_run_id,source_namespace,source_checkpoint_id,source_run_version,
    source_head,engine/manifest/implementation/schema hashes,canonical patch,dangerous_confirmation?)`，
    `ForkReceipt`保存完整request identity、stable child run/trace/checkpoint IDs、phase/version/claim与outcome。
    所有hash由SDK从typed content重算，caller supplied值只可constant-compare。start receipt 的claim
    authority也是public snapshot的一部分：`ADMITTED`必须令action/owner/epoch/expiry/activation全为None；
    `CLAIMED|RUNNING`必须同时携`claim_action`、非空owner、正整数workflow claim epoch、finite acquisition
    expiry与同run/namespace/owner的`WorkflowActivation`；`claim_epoch` exact等于
    `WorkflowLease.epoch`，而`WorkflowLease.runtime_lease_epoch == ExecutionLease.epoch ==
    RunFenceLease.runtime_lease_epoch`，不得把workflow claim epoch与独立`RunFenceLease.fence_epoch`混用。
    `claim_expires_at` exact等于claim签发时`WorkflowLease.expires_at == ExecutionLease.expires_at`的immutable
    audit值，RunFence不携expiry也不被heartbeat续租。heartbeat只co-renew durable Runtime dispatch record、
    ExecutionLease与WorkflowLease rows，不重写receipt/version/RunFence；后续mutation在同一transaction重读
    current rows，要求Runtime dispatch/ExecutionLease/WorkflowLease owner+runtime epoch彼此相等、两条lease的
    current durable expiry相等且`> now`、RunFence仍active且owner/run/runtime epoch/fence epoch为current，同时
    caller immutable expiry不得晚于current durable expiry。`SETTLED`保留最后一次claim的
    action/owner/epoch/expiry作receipt-first audit identity、`activation=None`且必须有serialized outcome。
    `WorkflowLifecyclePort` exact mutation surface为
    `admit_start_standalone(transaction, request, *, now, fault?) -> StartAdmissionReceipt`、
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
    ttl_seconds, fault?) -> RecoveryClaim`；为保持policy在SDK而不是Port，还必须提供
    `repair_checkpoint_head(transaction, candidate, expected_snapshot, replacement_head,
    outcome, *, now, fault?) -> RecoveryOutcome` 与
    `quarantine_recovery_candidate(transaction, candidate, expected_snapshot, quarantine,
    outcome, *, now, fault?) -> RecoveryOutcome`。`replacement_head` 是SDK从同run+pinned namespace
    最高committed revision选出的typed `(checkpoint_id, revision, checkpoint_hash)`；
    `quarantine` 是canonical `(quarantine_id, reason, source_head?, observed_hash?, detail_ref?)`，
    Port只验证全snapshot CAS并原子写head/record+outcome，不得自行选head或reason。
    `WorkflowLifecyclePort` 还必须提供
    `list_unsettled_start_admissions(snapshot_cursor, *, limit) -> (receipts,next_cursor)` 与
    `list_unsettled_resume_admissions(snapshot_cursor, *, limit) -> (receipts,next_cursor)`，`limit`必须
    `1..50`，Port cursor只推进到本次实际返回的最后receipt，均以stable receipt key
    升序keyset；start cursor/identity必须使用schema唯一主键`request_key`，不得使用可重复的`request_id`；
    resume使用其唯一`receipt_id`。start枚举`ADMITTED|CLAIMED|RUNNING`，resume枚举
    `ADMITTED|CLAIMED|RETRY_WAIT`，返回完整immutable receipt而不按expiry/owner/mode/due做policy过滤。
    SDK scanner用注入`now`分类；precreated receipt必须先经Runtime recovery取得typed
    `ExecutionLease+RunFenceLease`才可claim，Port不得伪造第二套owner。
    `RecoverySnapshot` 还必须携完整无policy evidence：
    `checkpoint_candidates: tuple[RecoveryCheckpointCandidate,...]` 按revision升序，每条为
    `(checkpoint_id, namespace, revision, checkpoint_hash, status, schema_version)`，只枚举同run+
    pinned namespace的committed checkpoints；`blockers: tuple[RecoveryBlockerSnapshot,...]` 按
    blocker_id升序，每条为`(blocker_id, kind, ledger_id, observed_unknown_version,
    handoff_attempt, resolution_state?, resolution_version?, evidence_ref?)`。read只左连接/枚举真实
    durable evidence，不选replacement head、不解释resolution。RecoverySnapshot canonical hash包含
    这两组，所有recovery mutation同tx重读并CAS其全量digest；分页/读取后新checkpoint、
    blocker或resolution进展必须使旧outcome零写conflict。
    `ensure_and_bind_precreated_start(transaction, request, execution_lease, run_fence,
    dispatch_claim: RuntimeStartDispatchClaim, *, now, ttl_seconds, fault?) -> PrecreatedStartDispatch` 与
    `recover_precreated_start(transaction, recovery_work: WorkflowRecoveryWork, execution_lease, run_fence,
    *, now, ttl_seconds, fault?) -> PrecreatedStartDispatch` 也是
    `WorkflowLifecyclePort` 的必填durable mutation，不是Runner可以直连SQLite实现的helper。
    `ensure_and_bind_precreated_start`只处理无workflow start receipt的首次dispatch与其after-commit exact replay；
    `recover_precreated_start`处理已有CLAIMED/RUNNING/SETTLED receipt。CLAIMED/RUNNING takeover对start receipt
    `version/claim_owner/claim_epoch/claim_expires_at`与Workflow projection同tx CAS；稳定fault labels为
    `workflow:ensure_precreated_start:before_<table>_write/after_<table>_write/after_commit`，对实际写入的
    start receipt/workflow lease/start dispatch claim每张表都要列出。它必须在创建或重绑workflow start
    receipt的同一transaction以stable claim capability identity重读current dispatch record，验证
    run/owner/runtime epoch/claim epoch、current Runtime lease/RunFence与`record.expires_at > now`，再CAS
    current record version的`CLAIMED->CONSUMED`；不得比较Driver取得时的旧version/expiry。已CONSUMED只允许
    receipt-first匹配同一workflow receipt返回，不得进入第二Driver。
    `recover_precreated_start`只接受`receipt_kind=start`的完整frozen work，重读并constant-compare current
    receipt id/version/request fingerprint/snapshot及generic Run/head，以新Runtime lease/RunFence CAS重绑
    CLAIMED或RUNNING receipt；它不得要求/创建/消费dispatch claim，也不得新建start receipt。wrong kind、stale
    snapshot、live foreign workflow owner、terminal/WAITING、旧runtime epoch均零写。Runner/Driver只能按tag调用
    这两个Port methods，旧`admit_start_precreated` public path删除，禁止自行组合`bind_activation`或私有SQL。
    两个dispatcher在任何已有phase（CLAIMED/RUNNING/SETTLED包括receipt-first返回）都必须从dispatch claim绑定的
    request或`WorkflowRecoveryWork.receipt_snapshot`自行重算canonical request fingerprint，并与持久化
    `request_key/request_fingerprint/request_json/mode/run/trace/thread`全量constant-compare；同key任一
    ticket/input/manifest/identity字段变化均在读取outcome或rebind前零写conflict，不得因SETTLED
    或旧claim过期而跳过request identity。
    `WorkflowReplayPort` exact surface为
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
    `RecoveryCandidate` 是SDK recovery policy的immutable read authority，exact fields为
    `(run_id, run_version, status, runtime_lease_owner?, runtime_lease_epoch?,
    runtime_lease_expires_at?, workflow_lease_namespace?, workflow_lease_owner?,
    workflow_lease_epoch?, workflow_lease_expires_at?, run_fence_owner?,
    run_fence_runtime_lease_epoch?, run_fence_epoch?, run_fence_state?, checkpoint_head?)`。
    owner/epoch/expiry 必须成组全有或全无；workflow namespace不得用
    `namespace != runtime LIMIT 1` 猜测，必须从该Run的durable start/admission snapshot取pinned
    workflow namespace并读取精确row。RunFence四元组同样全有或全无。
    `list_candidates`/`read_recovery_snapshot` 只返回真实row snapshot，不接收 `now`、不判定
    expired/live、不内置quarantine/retry policy；SDK Runner/recovery必须用同一注入的
    Unix epoch `now` 按下述顺序分类。`commit_recovery_outcome` 必须对上述全部
    snapshot字段和expected head做同一transaction CAS；任一owner/epoch/expiry/state/namespace
    漂移都零写conflict，Port不得在SQL中代替SDK先做过期分类。
    `list_candidates` 的枚举authority固定为 `runs.driver_kind='workflow'` 且state非
    `completed|failed|cancelled|reserved_fork` 的全部Run，以`run_id` 升序做stable keyset
    pagination：`snapshot_cursor` 是上页最后一个`run_id`，下页只取`run_id > cursor`；
    每页返回不超过100条，`next_cursor` 只在还有后续row时返回该页最后
    `run_id`。实现必须以Run为主表、对pinned start/admission identity、Runtime lease、精确
    workflow namespace lease、RunFence和checkpoint head做LEFT JOIN/等价nullable read；缺任一row
    仍必须枚举并由nullable group表示。禁止按expiry、lease/fence state、head是否存在、
    manifest/implementation可用性、已有recovery outcome或实现者认定的“可恢复”状态
    预过滤；这些均属于SDK policy。分页期间live renew/takeover/head推进可导致后续
    `commit_recovery_outcome` 全snapshot CAS conflict，但不得使该Run从枚举集消失。
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
     SDK必须提供两个official private adapter，而不是让workflow/Host重写：
     `WorkflowProviderAdapter(coordinator, activation, clock)` 与
     `WorkflowEffectAdapter(executor, activation, clock)`。它们是每次Driver invocation构造的
     immutable value，每个method显式把同一`activation.execution_lease/run_fence/workflow_lease`
     传入T2.4/T2.5 public call，且run identity必须与Node context一致。
     `WorkflowRuntimeDriver.start` 只能从`DriverInvocation.services.provider/tools`与当次CLAIMED
     `WorkflowActivation` 构造adapter，再以typed `WorkflowContext.provider/effects` 字段传给
     Runner/Native；不得仅把activation序列化到configurable、不得放入可替换的字符串
     `ports` mapping或hidden mutable closure。pure route/interrupt context构造时必须移除这两个
     typed adapters。漏传、错run/owner、旧runtime/workflow/fence epoch均在transport/handler前零物理调用。
  3. precreated path必须经Port化 `_require_precreated` 从durable execution authority验证generic Run与
     workflow Run同一ID、driver kind、trace identity、lease owner/epoch；terminal则由
     `_assert_precreated_terminal` 验证workflow terminal checkpoint与generic terminal/event/delivery在同一
     transaction收敛。caller参数、内存对象或事后查询不能替代该绑定。
     precreated `run/resume`不得接收裸`owner_id`；必须接收当次`DriverInvocation`的typed
     `ExecutionLease + RunFenceLease`并走`bind_activation/claim_resume_precreated`。wrong owner、expired/old
     Runtime epoch、同owner旧RunFence或另一run的lease在admission/claim阶段零receipt、零checkpoint、零effect。
     start同样区分`StartMode.STANDALONE|PRECREATED`：precreated Runtime在Workflow Driver前已创建并激活generic
     Run。首次`START_NEW|START_ORPHAN`只能调用`ensure_and_bind_precreated_start`，在同transaction验证现有generic
     Run/start snapshot/version、typed leases与dispatch claim，同时创建workflow receipt/row与projection lease并
     消费claim，直接落CLAIMED；不得再创建reserved generic Run，
     不存在admission后再bind窗口，standalone scanner也不得claim PRECREATED receipt。
     official Runtime launch不得要求Host或测试事先seed admission：T4.2 launch在generic
     `StartSnapshot` 中pin入typed `workflow_admission: StartAdmissionRequest | None`，序列化时保留
     完整canonical request（不是只保留`start_input`），因此objective、spawn_origin、parent/root Run、fixed ATTACHED与
     child command identity也逐层进入start snapshot canonical payload/hash；任一层mutation/reopen必须在child/generic write前
     conflict。`driver_kind="workflow"` 必须非None，
     其他driver必须None。SDK `WorkflowRuntimeDriver.start` 在执行Native前先用该typed
     request、当次`DriverInvocation.execution_lease/run_fence`与reserved dispatch claim调用
     `ensure_and_bind_precreated_start`，然后用
     返回的CLAIMED receipt/activation调Runner；after-commit响应丢失重试必须只读同receipt。
     `run_precreated` 不得接受“admission必定已被外部创建”的隐藏前提。真实
     `Runtime.start(workflow profile)` 从空DB到genesis/terminal必须零手工seed GREEN。
     `WorkflowRuntimeDriver.recover`遇`RECOVER_START`则必须调用
     `recover_precreated_start(transaction, recovery_work, execution_lease, run_fence, ...)`，不得设置/伪造
     `workflow_start_dispatch`；`RECOVER_RESUME`只走`claim_resume_precreated`。上述步骤必须由对应SDK-owned
     tagged dispatcher执行；dispatch exact fields为
     `(action, receipt, activation?, serialized_outcome?)`，`action = NEW_CLAIMED | RESUME_CLAIMED |
     RESUME_RUNNING | SETTLED`。无receipt时原子admit+bind为NEW_CLAIMED；CLAIMED且同一
     active authority只读返回，旧claim过期且invocation的Runtime lease/RunFence已是current时
     CAS receipt version/owner并重bind为RESUME_CLAIMED；RUNNING必须验证genesis/head存在，
     然后用新invocation authority同tx rebind为RESUME_RUNNING，不重写genesis；SETTLED无论
     旧lease是否过期都receipt-first只读返回原outcome且activation=None。PRECREATED出现
     ADMITTED、RUNNING无genesis/head、live foreign owner、旧invocation epoch均fail closed/零写。
     `PrecreatedStartDispatch`构造时必须独立重算并constant-compare完整claim authority：对
     `CLAIMED|RUNNING`，receipt的`claim_owner/claim_epoch/claim_expires_at`必须分别等于activation中的共同
     owner、`WorkflowLease.epoch`与`WorkflowLease.expires_at == ExecutionLease.expires_at`；RunFence只校验
     run/owner/runtime epoch/fence epoch co-fence，不参与expiry比较。current durable expiry不由typed constructor
     猜测，而由上述同事务SQLite current-row gate验证。`workflow_start_admissions`必须durably保存
     `claim_action`；首次claim原子写`claim_action=NEW`，recovery/takeover推进receipt version与WorkflowLease
     claim epoch时原子改为`claim_action=RESUME`。`NEW_CLAIMED`只接受`claim_action=NEW`的CLAIMED receipt；该首次claim的
     after-commit响应丢失或same-authority exact replay必须receipt-first返回相同`NEW_CLAIMED`，不得改判action。
     `RESUME_CLAIMED`只接受`claim_action=RESUME`且recovery/takeover已原子推进durable receipt version+
     WorkflowLease claim epoch后的CLAIMED receipt，`RESUME_RUNNING`只接受`claim_action=RESUME`的RUNNING receipt及
     已验证genesis/head，`SETTLED`只接受SETTLED receipt、`activation=None`与exact serialized outcome。
     SETTLED branch必须先读取并constant-compare完整receipt+outcome后只读返回，不查询或要求仍active的lease、
     RunFence、dispatch或current expiry；保留的claim字段只用于audit/replay identity。
     typed constructor负责拒绝receipt/activation内部任一owner/epoch/expiry/action/phase/version交叉字段不一致；
     public immutable value本身不是durable provenance token，caller若整体构造一套字段自洽但并非current row的
     receipt+activation，必须由canonical Lifecycle method在同一open `WorkflowTransaction` / same
     `transaction_owner`内重读current receipt/version/authority rows并以等价atomic CAS线性化拒绝；官方
     `SqliteExecutionUnitOfWork`必须在同一`BEGIN IMMEDIATE`完成这些gate。Runtime不得接受caller-supplied
     `PrecreatedStartDispatch`，官方路径只能消费其刚调用canonical Lifecycle Port得到的结果，后续每个mutation
     仍须重验current authority。custom durable Port是显式trusted persistence authority，必须提供相同atomic CAS
     语义并通过共享conformance suite；恶意/不conformant Port不可能由value层证明来源，不属于value invariant。
     该value不得作为任何mutation authority单独使用；不得引入Host-issued seal、隐藏global cache或第二factory
     authority来伪装数据库来源。逐字段partial mutation、整体自洽replacement、close/reopen与custom Port
     conformance矩阵都属于H16 gate。
     `build_runtime`/`Runtime.__init__` 公开签名删除`workflow_driver`注入参数；改为必填
     SDK-owned `workflow_runner`/official factory binding（无workflow profile时可None），Runtime内部调
     `build_workflow_runtime_driver`并注册reserved key。Host `drivers` 仍只能传extension key，不能传入、
     删除或替换official driver object。
     `RuntimeUnitOfWork` public Protocol必须暴露opaque `transaction_owner`。构造时必须验证
     `type(workflow_runner) is WorkflowRunner`（禁subclass/alternate factory）且
     `workflow_runner.execution_ports.unit_of_work.transaction_owner is uow.transaction_owner`；Runner的
     checkpoint/lifecycle/recovery/replay已在其自身constructor绑定同owner，因而这个identity check
     将Runtime与全部Workflow authority锁在同一DB/transaction owner。跨库Runner、镜像run id、
     缺owner都必须在Runtime构造时零写拒绝。
     Native terminal不得先单独把generic `runs.state`终态化、释放authority，再让Kernel
     二次terminalize。`WorkflowLifecyclePort.commit_terminal(transaction, activation,
     expected_run_version, expected_checkpoint_head, terminal_checkpoint_id, terminal_state,
     terminal_payload, deliveries, terminal_fence_receipt_id, *, now, fault?) ->
     WorkflowTerminalOutcome` 是唯一terminal command：在checkpointer同一open transaction中验证
     三份current authority、new checkpoint/head，并原子写terminal workflow/generic Run、
     `run.<state>` event、outbox deliveries、terminal fence receipt，然后release Runtime/Workflow lease与
     RunFence。它保留H14 `finalize_run` 的stable `(run_id, terminal_checkpoint_id)` operation id/
     global receipt语义，但将其payload扩展为上述全部authority/terminal fields并由adapter重算
     hash；不得再保留只`UPDATE runs.state` 的旁路。after-commit重试先receipt-first只读返回
     同outcome，异payload零写conflict。Kernel看到Workflow Driver terminal result后只调
     `verify_workflow_terminal(outcome: WorkflowTerminalOutcome) -> bool`；UoW必须对七字段/
     outcome hash与durable receipt、exact terminal Run state/version、terminal checkpoint/head id+hash/revision、
     event、exact outbox immutable creation facts集合（delivery id、sink、idempotency key、canonical payload、
     created_at）、terminal fence receipt、Runtime/Workflow lease rows已删除/
     released且RunFence state=`released` 逐项constant-compare；任一缺失/多余/漂移返回false，
     验证成功即复用已提交
     terminal，不再调generic `_terminalize`。逐checkpoint/event/outbox/fence/lease/run write fault与
     close/reopen必须全回滚或全提交。
     outbox的当前delivery state是dispatch authority，允许在验证时为
     `pending|claimed|delivered|failed|released`；verifier不得把terminal commit时的mutable state写入
     outcome hash或要求它保持不变，但必须按terminal receipt保存的delivery IDs验证无缺失/额外行，且上述
     immutable creation facts逐项一致。补terminal commit后dispatcher在heartbeat/Driver return前把outbox推进到
     delivered或failed的barrier回归，两种状态都必须验证成功且不得重写terminal。
     `WorkflowTerminalOutcome` 是immutable typed value，exact fields为
     `(receipt_id, run_id, checkpoint_id, state, event_id, delivery_ids, outcome_hash)`；`outcome_hash`由SDK对
     前六字段和terminal payload/deliveries canonical SHA-256重算，DB receipt保存同hash。
     `DriverResult` 新增reserved typed `workflow_terminal: WorkflowTerminalOutcome | None`；只有exact
     `WorkflowRuntimeDriver` 返回terminal state时允许非None，其他driver设置、Workflow terminal缺失或
     WAITING携带均拒绝。Native→Runner→Driver必须原样传该typed value，禁止放进普通
     payload自声明。Kernel以七字段+UoW durable receipt constant-compare后只清理local task/
     heartbeat index，不重写terminal。
     terminal transaction先release Runtime lease而typed outcome尚未从Driver返回时，并发heartbeat
     可观测到stale lease。Runtime heartbeat在workflow Run renew conflict时必须先通过UoW
     `read_workflow_terminal_outcome(run_id) -> WorkflowTerminalOutcome | None` 读取并调同一
     full-outcome verifier；若完整terminal receipt/event/outbox/fence receipt已提交，则该conflict是
     benign terminal release，heartbeat只停止自身，不取消Driver、不走lease-loss/cancel/failure。
     无receipt或任一字段不完整仍按真实lease loss隔离。用barrier测terminal commit后、
     Driver return前heartbeat立即renew，最终必须返回同typed outcome且零cancel/failure。
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
     workflow namespace+owner+epoch+expiry, run-fence owner+runtime epoch+fence epoch+state,
     expected head)` 到 RETRYABLE+recoverable receipt；
     `(b)` cancel_requested/cancelling先按第4项收敛；`(c)` 任一active未过期owner则只读skip；
     `(d)` manifest/implementation/descriptor/hash不匹配则BLOCKED `graph_version_unavailable`；
     `(e)` 非active Run的missing/stale head只允许从同run+namespace、最高committed revision CAS repair；
     `(f)` 读取/identity/hash/schema失败则保存canonical quarantine record并BLOCKED `checkpoint_corrupt`；
     `(g)` Provider/Tool handed_off/unknown优先进入已有resolution blocker，`still_unknown`保持WAITING且不可自旋/
     replay，completed/confirmed_not_started按H13 resolution epoch唤醒；`(h)` succeeded_pending最后标记
     `resume_pending_checkpoint`。每个outcome固定`previous_status/status/action/reason/receipt_id`；mutation CAS都绑定
     status+run version+两类lease epoch+expected head。补双scanner、scanner-vs-live Runner、late reconciliation
     交错，旧scanner不得回退新head或quarantine active Run。
     Runner公开constructor删除可执行policy的`WorkflowRecoveryPort`/consumer callback；只依赖
     canonical `WorkflowExecutionPorts.recovery` durable primitives。`recover_expired` 必须遍历上述
     exact candidate pages并返回真实outcomes，不得stub为空；`recover(run_id)` 必须先走
     同一classify/repair/quarantine/uncertainty pipeline，不得绕过后直接`_execute`。
     precreated unsettled receipt不由Workflow scanner自行claim Runtime authority。SDK official
     `WorkflowRuntimeDriver.list_recovery_work(cursor, *, now) -> (items,next_cursor)` 把start/resume枚举转为
     immutable `WorkflowRecoveryWork(run_id, receipt_kind, receipt_id, receipt_version, mode, due_at?,
     request_fingerprint, receipt_snapshot: StartAdmissionReceipt | ResumeAdmissionReceipt)`，snapshot按kind保存
     完整canonical start request或resume responses并与flat identity重算一致；`receipt_kind=start`时
     `receipt_id` exact等于`StartAdmissionReceipt.request_key`，`receipt_kind=resume`时exact等于
     `ResumeAdmissionReceipt.receipt_id`，所有read/CAS/pagination均用这个durable primary key，不得按
     `StartAdmissionReceipt.request_id`查询或推进cursor；仅排除
     standalone，不查/领Runtime lease也不丢弃尚未due RETRY_WAIT。cursor是typed
     `WorkflowRecoveryCursor(start_cursor?, resume_cursor?)`：每轮分别从两个Port最多取50条，
     按`(receipt_kind, receipt_id)` 稳定合并返回最多100条，并将两个cursor分别推进到
     已消费的各自最后receipt；禁止用一个字符串cursor跨两表或因一类持续新增而
     饿饿另一类。Runtime startup `recoverable_runs`
     阶段消费items：用canonical UoW对该run取得或跳过active owner、claim
     `ExecutionLease`和RunFence，构造携带该work的`DriverInvocation`，再调
     `WorkflowRuntimeDriver.recover(invocation, work)`。Driver/Runner必须constant-compare work与当前receipt。
     precreated start receipt的RUNNING phase必须生成`RECOVER_START` work，由Runtime在lease expiry/takeover后调用
     `recover_precreated_start`；active owner只读skip。standalone RUNNING不得交给Runtime Driver或start scanner
     自领Runtime authority，仍由Runner的canonical recovery candidate classify/repair pipeline处理。其他start走
     对应tagged dispatcher，resume走`claim_resume_precreated`并复用receipt内responses。
     claim后、Driver前crash可由新Runtime epoch重枚举/接管；active foreign owner、wrong mode/
     receipt/version/run、旧epoch均零写且零node/effect。active owner同进程由Runtime的durable
     wake-drain消费，不等TTL。
     对`due_at > now`的RETRY_WAIT，Runtime以注入`clock/sleep`注册可取消timer，key为
     `(receipt_kind, receipt_id, receipt_version, due_at)`；到期后必须重读receipt并重走上述
     Runtime claim/typed recover，version/due/phase已变则no-op。close先取消/join所有timer；重启靠全量
     scan重建；timer与startup/takeover同时到期仍只能一个Runtime lease/receipt claim winner，
     不得遗失无外部signal的due wake。
     live Driver新写`RETRY_WAIT`时不能等下次startup scan：immutable
     `WorkflowRetryWake(run_id, receipt_id, receipt_version, mode, due_at, wait_event_id,
     generic_run_version, outcome_hash)` 由Native store
     从已提交receipt构造，经Runner/official Driver原样放入reserved
     `DriverResult.workflow_retry_wake: WorkflowRetryWake | None`。只有exact Workflow Driver的WAITING result
     可携带。`defer_resume_retry`/`commit_retry`必须在保存RETRY_WAIT receipt的同一transaction把generic
     Run CAS到WAITING、写stable `workflow.retry_waiting` event与durable wake identity，并release
     Runtime/Workflow lease和RunFence；不得只推进workflow receipt而把generic Run留RUNNING。
     canonical UoW必须提供
     `verify_workflow_retry_wake(wake: WorkflowRetryWake) -> bool`，逐项重读并constant-compare receipt、
     due/hash、generic Run WAITING state/version、wait event与三份authority已release。Runtime收到wake后先调用
     该verifier；成功则注册上述timer并跳过普通`commit_runtime_state(WAITING)`，失败则fail closed，禁止用第二个
     transaction修补generic Run。heartbeat在commit与Driver return之间遇到renew conflict时也必须先验证同一
     durable wake；完整则视为benign WAITING release，只停止heartbeat而不取消Driver。
     返回前crash由startup scan补偿；返回与timer同时到期、close/restart均仍由
     Runtime lease+receipt CAS保证唯一winner。其他driver设置、非WAITING携带或自声明payload
     中的due_at都拒绝。
     standalone `RETRY_WAIT` 不经Runtime Driver：Runner在Native返回同一
     `WorkflowRetryWake`后先重读receipt，再以同一注入`clock/sleep`注册Runner-owned
     cancellable timer；到期后走`claim_resume_standalone`并复用receipt responses。Runner必须有
     `close()`取消/join timers，且进程重启时由SDK standalone unsettled scanner全量枚举
     ADMITTED/CLAIMED/RETRY_WAIT，对未due重建timer、已due立即claim。live timer/startup scanner/
     并发Runner仍由receipt CAS只允许一个claim winner；无Runtime、无外部signal、不重启也
     必须在due时继续。
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
     request-factory hashes, objective, start_input, capability_snapshot, spawn_origin, parent_run_id, root_run_id,
     attachment_policy=AttachmentPolicy.ATTACHED, child_command_id`。`start_input`是唯一caller state authority，先按
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
     durable generic admission前的唯一factory为SDK
     `WorkflowRunner.prepare_start_admission(ticket: VerifiedWorkflowLaunchTicket,
     start: RunStart) -> StartAdmissionRequest`。Runtime在写generic Run/start snapshot之前调它；
     factory从exact registry/compiled workflow读manifest、schema/limits、implementation、terminal descriptor/
     request-factory与capability metadata，对`start.input`先canonical validate/freeze后自行填充全部
     hash/namespace/version字段。Host只能提交T4.2 ticket和RunStart，不能提交/覆盖
     `StartAdmissionRequest`、manifest hash、schema或descriptor。ticket/profile/catalog/workflow任一不匹配在
     generic receipt前零写拒绝。
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
     checkpoint head，同时把generic Run置WAITING、写stable retry-wait event/wake并release全部三份activation
     authority。到期前exact caller只读返回typed IN_PROGRESS；startup/due scanner
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
  必须包含precreated genesis已提交/start receipt=`RUNNING`后进程崩溃、关闭同路径DB、lease过期重开：startup
  enumeration构造同receipt snapshot的`RECOVER_START`，新Runtime epoch恢复且node/effect不重放；active owner及
  standalone RUNNING均不得被该Runtime scanner误claim。
  另补两个start receipts共享同一request_id、不同request_key，`limit=1`逐页枚举并分别recover；第二条不得被
  cursor跳过，按request_id读取任意行必须由API/SQL结构性禁止。
- 依赖：T2.1–T2.5。

### T4.2 — Profile catalog / orchestration control / launch ticket [AC-6, AC-8]

- 文件：`runtime/profiles.py`, `runtime/orchestration.py`, `runtime/drivers/workflow.py`,
  `runtime/drivers/react.py`, `runtime/drivers/react_loop.py`, `tools/contracts.py`, `tools/executor.py`,
  `tools/registry.py`, `tools/reconciliation.py`, `runtime/workflow_spawn.py`, `runtime/kernel.py`,
  `runtime/live_index.py`, `runtime/termination.py`, `runtime/react_checkpoint.py`, `runtime/context.py`,
  `workflow/execution_ports.py`, `execution/uow.py`, `execution/sqlite/uow.py`,
  `execution/sqlite/migrations/0001_initial.sql`。launch-ticket receipt与generic admission的schema/UoW
  owner明确属于本task；不得让Host新增表或实现第二Port。
- API：`ProfileDescriptor(key, description, use_when, avoid_when, input_schema_ref, generation,
  fingerprint)`；Agent-visible reserved Tool 名为 `workflow_spawn`，其 JSON input schema 只含
  `profile_key/objective/start_input/candidate_id?`，不是第二个 Python launch API，也不接受
  `catalog_generation` 或技术 identity；唯一 public Python execution API 是下文
  `RunClient.workflow_spawn(invocation: WorkflowSpawnInvocation)`；
  immutable `StartInputSchema(schema_ref, canonical_schema, schema_hash)` 与
  `WorkflowProfileRegistration(descriptor: ProfileDescriptor, workflow_name, workflow_version,
  start_input_schema: StartInputSchema)`。`StartInputSchema`只接受T1.3 fail-closed schema dialect：支持类型
  `object|array|string|integer|number|boolean|null`与关键字`type/properties/required/
  additionalProperties/items/enum/const/minLength/maxLength/minimum/maximum/minItems/maxItems/title/
  description/default`；root必须object、`additionalProperties=false`，所有其他关键字（含`$ref/$defs/pattern/
  format/oneOf/anyOf/allOf`）拒绝，不调用external resolver或正则引擎。raw mapping在canonicalize前用迭代遍历
  检测object-identity cycle并限制：canonical UTF-8 <= 32768 bytes、depth <= 12、schema nodes <= 256、
  total properties <= 256/per object <= 64、total required <= 256、enum entries total <= 256/per enum <= 64、
  key/ref/title/description/default及所有string literal各<=4096 UTF-8 bytes；numeric必须finite。schema hash由
  SDK重算，schema_ref必须exact等于descriptor.input_schema_ref；descriptor fingerprint则由SDK从
  key/description/use_when/avoid_when/input_schema_ref/generation独立重算并constant-compare，不能拿schema hash替代。
  registration与actual start input
  validation复用同一个SDK validator；start input另限canonical UTF-8 <= 65536 bytes、depth <= 12、JSON nodes
  <= 1024，超限在catalog/ticket/Run写入前typed reject；
  catalog/profile projection另有aggregate硬限：profiles `<=32`；profile key UTF-8 `<=128`，description/use_when/
  avoid_when各`<=2048`，schema_ref `<=4096`；完整`WorkflowCatalogAuthority`与
  `WorkflowCatalogSelectionSnapshot` canonical UTF-8各`<=262144` bytes，生成的全部workflow ToolSpec canonical bytes
  合计`<=262144`，包含messages/tools/input的最终ProviderRequest canonical UTF-8 `<=1048576`。这些bounds在任何
  catalog/checkpoint/provider ledger写入与物理Provider调用前用iterative bounded canonicalization执行；N+1 profiles、
  aggregate bytes+1、corrupt reopen均typed reject、零ledger/零transport。单项上限与aggregate上限必须同时满足，禁止
  先deepcopy/完整编码再检查。
  immutable `WorkflowCatalogProfileBinding(profile_key, description, use_when, avoid_when, input_schema_ref,
  profile_fingerprint, workflow_name, workflow_version, implementation_fingerprint, checkpoint_namespace,
  manifest_hash, state_schema_version, start_input_schema: StartInputSchema,
  terminal_projection_descriptor, terminal_request_factory_hash, capability_snapshot)` 与
  `WorkflowCatalogAuthority(authority_id="model_spawnable", generation, version, catalog_hash, profiles)`，
  其中profiles是按profile key排序的完整binding tuple；catalog hash由SDK从这组完整canonical fields重算，caller
  hash只可constant-compare。SDK-factory-only non-subclassable
  `VerifiedWorkflowCatalogAuthority(authority, registry_snapshot_id, registry_snapshot_hash, factory_token)`只能由
  `WorkflowRunner.prepare_catalog_authority(generation, registrations: Sequence[WorkflowProfileRegistration]) ->
  VerifiedWorkflowCatalogAuthority`从一次immutable registry snapshot构造。registration是profile→compiled的唯一
  typed mapping：SDK内置三个official registrations，Host只可为custom workflow提交descriptor+真实input schema，
  不可提交manifest/implementation/terminal/capability hashes。Runner按name/version查CompiledWorkflow，验证
  descriptor generation/fingerprint与schema ref/hash，从CompiledWorkflow manifest/definition重算manifest、
  implementation、state schema、terminal descriptor+request factory与capability snapshot；checkpoint namespace由SDK
  固定`workflow/{profile_key}/{workflow_name}/{workflow_version}`规则派生。漏字段、重复profile、generation回退、
  registry lookup mismatch或同profile不同compiled binding在factory阶段拒绝；
  immutable `WorkflowLaunchRequest(request_key, candidate_id?, profile_key, catalog_generation,
  session_id, request_id, turn_id, requested_run_id?, requested_trace_id?, requested_thread_id?,
  tool_catalog_generation, objective, start_input, spawn_origin: WorkflowSpawnOrigin, root_run_id,
  attachment_policy=AttachmentPolicy.ATTACHED, child_command_id)`；`workflow_spawn` v0.1只允许`ATTACHED`，root id必须从
  durable parent Run继承（parent为root时等于parent.run_id），child command id由payload-independent spawn operation id按固定
  `workflow-spawn/child-command/v1` domain确定性派生，Host/Agent不得选择或降级为DETACHED/new root；
  为把Agent的语义选择与Runtime durable authority明确分开，public SDK还必须提供immutable
  `WorkflowSpawnSelection(profile_key, objective, start_input, candidate_id?)`、immutable
  `WorkflowCatalogSelectionSnapshot(authority_id, generation, version, catalog_hash,
  profiles[(profile_key,description,use_when,avoid_when,profile_fingerprint,
  start_input_schema: StartInputSchema)], canonical_hash)`、
  `WorkflowSpawnOrigin(parent_run_id, parent_request_id, turn_id, internal_tool_call_id)`与SDK-factory-only、
  non-subclassable `WorkflowSpawnToolContext(run_id, request_id, turn_id, internal_tool_call_id,
  catalog_snapshot_hash, react_checkpoint_revision, execution_lease: ExecutionLease,
  run_fence: RunFenceLease, workflow_lease: WorkflowLease?, effect_id, effect_handoff_attempt, factory_token)`、
  immutable `WorkflowSpawnIssueAuthority(react_checkpoint_revision, execution_lease, run_fence,
  workflow_lease?, effect_id, effect_handoff_attempt, effect_request_hash)`、
  non-subclassable `WorkflowSpawnInvocation(spawn_operation_id, origin, start: RunStart, selection,
  catalog_selection: WorkflowCatalogSelectionSnapshot, issue_authority: WorkflowSpawnIssueAuthority,
  factory_token)`与SDK-owned immutable
  `WorkflowSpawnResult(schema_version="workflow_spawn.result.v1", child_run_id, child_request_id,
  parent_run_id, root_run_id, ticket_receipt_id, runtime_start_receipt_id, child_command_id,
  attachment_policy=AttachmentPolicy.ATTACHED)`；result canonical JSON的exact key set/order/UTF-8 bytes由SDK唯一factory从verified
  ticket + RuntimeStartReceipt + durable child Run/run_link/launch command重算，不能接受任意dict或序列化完整RunRecord。
  SDK内部另用immutable `ChildStartDispatchRef(child_start_receipt_id, child_dispatch_claim_id, child_run_id)`、
  factory-only immutable `WorkflowChildWaitBinding(parent_run_id, child_run_id, child_command_id,
  parent_wait_receipt_id, expected_parent_version, react_checkpoint_revision, expected_signal_domain,
  source_phase="tool_batch_reserved", batch_digest, spawn_ordinal, next_tool_ordinal,
  spawn_result_append_receipt_id, context_revision, termination_started_at, termination_last_observed_at,
  wall_deadline?, termination_policy_snapshot_hash)`与
  stable `WorkflowSpawnAdmissionOutcome(child_start_ref: ChildStartDispatchRef, result: WorkflowSpawnResult,
  suspension: WorkflowChildWaitBinding)`记录可跨进程byte/field-exact重放的committed事实；它不携lease/fence。
  child live control使用factory-only tagged union
  `WorkflowSpawnChildControl(kind=START|RECOVER|ATTACH|WAITING|CANCEL|TERMINAL, admission: RuntimeStartAdmission)`：START只允许
  `activation+dispatch_claim`，RECOVER只允许`activation+recovery_work`，ATTACH只允许current same-owner live index且不dispatch，
  WAITING/FOREIGN只注册durable observation不dispatch；CANCEL exact只接受CANCEL_PENDING且activation/dispatch/recovery/terminal
  全None；TERMINAL只读existing terminal/signal。任一wrong/missing optional field fail closed。
  reserved Tool seam另用factory-only
  `WorkflowSpawnToolOutcome(tool_result: ToolResult, child_control: WorkflowSpawnChildControl,
  child_start_ref: ChildStartDispatchRef, suspension: WorkflowChildWaitBinding)`；control中的live admission若存在必须是本次current authority，
  其余三字段由同一`WorkflowSpawnAdmissionOutcome`factory产生，禁止handler拆装。这些internal type不进入Agent Tool schema，也
  不能由Host构造。handler总返回sealed factory-only union
  `WorkflowSpawnHandlerOutcome = WorkflowSpawnSucceeded(control: WorkflowSpawnToolOutcome) |
  WorkflowSpawnFailed(tool_result: ToolResult, completion_receipt_id, batch_action=CONTINUE|PARENT_TERMINAL)`；catalog stale/
  graph unavailable active-driver failure为CONTINUE，parent terminal convergence为PARENT_TERMINAL，二者都不得携child control/
  suspension。UNKNOWN不是return variant，只能进入durable blocker。
  `RunClient.workflow_spawn_catalog() ->
  WorkflowCatalogSelectionSnapshot`、`RunClient.bind_workflow_spawn(context: WorkflowSpawnToolContext,
  selection) -> WorkflowSpawnInvocation`与
  `RunClient.workflow_spawn(invocation: WorkflowSpawnInvocation) -> WorkflowSpawnToolOutcome`（模块级
  `workflow_spawn(client, invocation)`只能是该method的薄facade）。该control outcome只能由下述reserved static handler返回并由
  SDK EffectExecutor消费，Host/programmatic caller不得取出`result`后忽略suspension；普通程序化child launch继续使用T3.2
  `Runtime.children.launch`，不存在第二条裸RunClient launch路径。`RunStart`新增nonoptional typed `turn_id`；
  Agent-visible official Tool schema只暴露selection四个字段，不能暴露request key、catalog generation、session/request/
  run/trace/thread/tool generation。SDK-owned Tool handler必须从current `DriverInvocation`、durable parent Run与ReAct turn
  构造`RunStart`（`execution_session_id=parent.execution_session_id`、`turn_id=current durable ReAct turn id`、
  `tool_catalog_generation=parent StartSnapshot`、`input=selection.start_input`）；parent run/request+durable turn+internal
  tool call identity用固定domain `workflow-spawn/operation/v1` SHA256派生payload-independent
  `spawn_operation_id`，child `RequestId/RunId`再分别用`workflow-spawn/request/v1`与`workflow-spawn/run/v1` domain
  只从该operation id确定性派生。official Tool handler必须通过SDK private factory从上述完整origin、生成的RunStart与
  selection与该Provider turn生成ToolSpec时取得的同一隐藏`catalog_selection`构造invocation；直接constructor/subclass/
  copy/replace/token mismatch拒绝。`workflow_spawn_catalog`从canonical Port同transaction读取current catalog并生成
  immutable snapshot；snapshot的canonical hash覆盖每个profile完整description/use_when/avoid_when/fingerprint与typed
  schema ref+canonical bytes+hash，SDK projection只能从该snapshot纯生成official ToolSpec，禁止二次读取current catalog
  或process cache。Provider pre-dispatch reserve必须在同一durable ReAct checkpoint原子pin snapshot canonical bytes/hash，
  并把hash纳入ProviderRequest fingerprint/tool spec snapshot；provider response checkpoint还必须pin ordered raw tool calls
  `(raw_call_id,name,canonical arguments hash)`。全局ToolRegistry只注册一个SDK-owned静态`workflow_spawn` handler，
  exact internal handler surface为`WorkflowSpawnToolHandler.execute(arguments, context: WorkflowSpawnToolContext) ->
  WorkflowSpawnHandlerOutcome`；ToolRegistry/ToolExecutor只允许该reserved handler返回此factory-only union，普通Tool仍只返回
  `ToolResult`。reserved handler执行前EffectExecutor仍是唯一PREPARED->HANDED_OFF writer；但
  `continue_spawn_admission`是该effect唯一terminal writer，并在parent authority释放前同BEGIN写Effect terminal result。
  handler返回后EffectExecutor严禁再次settle/write，只能以effect id/request hash/outcome.tool_result canonical hash做
  lease-free receipt-first read/compare；terminal row缺失则返回UNKNOWN/fail closed，不能补写，异ToolResult冲突。验证通过后把
  `WorkflowSpawnSucceeded.control`原样放入ReAct的typed `DriverResult.workflow_spawn_control`；
  `WorkflowSpawnFailed(batch_action=CONTINUE)`按T3.3 normal stable Context append/result-progress后继续next ordinal，零child/wait row；
  `PARENT_TERMINAL`只做terminal receipt-first收敛且不恢复batch。ReAct不得drop/rebuild任一字段，Kernel收到DriverResult后
  按`WorkflowSpawnChildControl.kind`唯一分流：START用activation+dispatch claim调Driver.start；RECOVER用activation+recovery_work
  调Driver.recover；ATTACH只关联existing live-index task；WAITING/FOREIGN只注册durable observation且不dispatch；CANCEL只附着/
  触发该child唯一durable cancel convergence，零Driver.start/recover，直到child terminal receipt；parent仍活且等待时cancelled child
  terminal走ATTACHED signal，parent已terminal则late quarantine；TERMINAL只读验证
  existing child terminal/signal。所有分支都在同一控制路径终止parent Driver，禁止把RECOVER当START或为ATTACH二次schedule。
  正常路径必须观测child Driver真实启动exact一次，测试不得私调child terminal；若crash在completion commit→handler return、
  handler→EffectExecutor、EffectExecutor→ReAct、ReAct→Kernel或schedule前，startup从durable receipt恢复同一child admission/
  dispatch并只schedule一次。
  禁止per-turn注册/替换/closure capture；`ToolContext`新增optional typed `workflow_spawn_context`，只有ReAct/
  EffectExecutor在逐call执行前可用private factory从current DriverInvocation+durable checkpoint/call mapping构造，非
  workflow_spawn call必须None。静态handler只把该typed context+Agent arguments交给同一个public binder。
  `bind_workflow_spawn`不接收caller snapshot/DriverInvocation，而是用factory token+run/request/turn/call/revision从
  `ReactCheckpointPort`重读durable pin，重算snapshot/hash并验证internal call id在ordered response中exact一次、name exact
  `workflow_spawn`、canonical selection payload与该call arguments exact；然后才用pinned snapshot构造invocation。
  binder必须把context中的完整lease/fence/effect authority复制进immutable `issue_authority`并逐字段交叉校验；
  factory token只证明SDK construction，禁止索引hidden side table/cache。Host不得重算origin/operation id或在metadata
  塞伪context。selection完整canonical payload只进入request fingerprint，不进入任何identity；
  同operation id+不同selection必须命中同request key并零ticket conflict。Host只注册official Tool，不写binder。
  `RunClient.workflow_spawn`在任何catalog/ticket write前必须重算`spawn_operation_id`并验证origin、RunStart派生的
  RequestId/RunId/turn与factory token，再验证`RunStart.input`与`selection.start_input` canonical bytes
  exact相等，之后只冻结/使用一个input value。SDK method按下面receipt-first顺序构造`WorkflowLaunchRequest`：
  `request_key=spawn_operation_id`、
  `catalog_generation=catalog_selection.generation`、`session_id=start.execution_session_id`、
  `request_id=start.request_id`、`turn_id=start.turn_id`、`requested_run_id=start.run_id`、requested trace/thread为None、
  `tool_catalog_generation=start.tool_catalog_generation`、objective/start input来自selection，并把完整canonical
  `spawn_origin`和catalog selection identity写入WorkflowLaunchRequest/ticket receipt payload+fingerprint。首次issue在
  同一transaction要求current catalog authority/version/hash与snapshot exact，且selected profile fingerprint/schema hash
  exact；G到G+1漂移必须typed `catalog_stale`/零ticket，不能把同profile key重绑。Runtime在transaction内验证RunStart的
  session/request/run/turn/tool generation、origin、selection snapshot与durable ticket；
  禁止从request id猜turn、从input抽objective、使用RuntimeProfile补catalog generation或让Host再传一份duplicate
  identity。首次无ticket receipt时还必须从`issue_authority`在同一open transaction重读parent Run/start、current ExecutionLease/
  RunFence/optional WorkflowLease与exact Effect ledger handed-off row，验证run/owner/runtime epoch/fence epoch、effect id/
  handoff attempt/internal call/request hash全匹配且parent非cancel/terminal后才可写ticket；old context跨TTL、same-owner
  new epoch、foreign takeover或parent cancel/terminal均零ticket/零child Run。只有matching generic admission是
  lease-free receipt-first terminal evidence；ticket-only只是incomplete intermediate，不允许旧issue authority继续
  admission。逐字段mapping/API snapshot、official Tool schema与mismatch零ticket gate属于T4.2。门禁还必须覆盖：G snapshot
  生成Provider request后暂停→publish G+1→同call bind仍只读取durable G pin且首次issue typed stale/零ticket；close/reopen
  销毁cache后由G pin纯生成的ToolSpec canonical bytes一致；跨DriverInvocation/run/turn/call id、wrong tool name或同call
  different selection全部在ticket前零写。
  还必须覆盖A/B两个并发Provider turn交错调用同一static handler仍各自绑定其durable snapshot/call、global registry中
  official handler count始终为1、crash/reopen后无需closure即可重建typed context，以及wrong/missing/Host metadata
  context在handler/ledger前拒绝。
  public immutable `WorkflowLaunchTicket(ticket_receipt_id, payload_hash, candidate_id, profile_key,
  catalog_generation)` 与SDK-factory-only immutable
  `VerifiedWorkflowLaunchTicket(ticket_receipt_id, ticket_id, candidate_id, profile_key, catalog_generation,
  catalog_authority_version, catalog_hash,
  profile_fingerprint, workflow_name, workflow_version, implementation_fingerprint, checkpoint_namespace,
  manifest_hash, state_schema_version, start_input_schema: StartInputSchema, terminal_projection_descriptor,
  terminal_request_factory_hash, capability_snapshot, session_id,
  request_id, turn_id, requested_run_id?, requested_trace_id?, requested_thread_id?, resolved_run_id,
  resolved_trace_id, resolved_thread_id,
  tool_catalog_generation, objective, objective_hash, start_input_hash, spawn_origin, parent_run_id, root_run_id,
  attachment_policy=AttachmentPolicy.ATTACHED, child_command_id)`。`objective`是独立Agent semantic
  authority，必须是strip后非空且原始UTF-8 bytes `<=32768`的string，完整原文与hash都持久化；不得只保存hash或从start input/
  Host callback/process cache重建。ticket由SDK在catalog generation/profile
  fingerprint/workflow binding全部校验后生成，identity字段与objective/start input的canonical
  hash共同进入`ticket_id`；requested identity缺省时，`issue`按固定公开namespace和
  `workflow-launch/{run|trace|thread}/v1` domain separator从canonical request fingerprint确定性派生三个
  nonoptional resolved IDs；caller显式requested值必须成为对应resolved值并进入fingerprint。
  `prepare_start_admission` 必须重算`RunStart.input` hash并与
  `start_input_hash` constant-compare，且逐项constant-compare
  `RunStart.execution_session_id/request_id/run_id/turn_id/tool_catalog_generation`与verified ticket的
  `session_id/request_id/resolved_run_id/turn_id/tool_catalog_generation`；`RunStart.input`必须与receipt冻结的完整
  canonical start input相同。完整objective必须从ticket receipt canonical payload重建，并与verified ticket的
  `objective+objective_hash`、`StartAdmissionRequest.objective`及`StartSnapshot.workflow_admission`逐层
  constant-compare；绝不能从start input、Host callback或process cache重建。Host不能
  构造或改写verified type。`workflow_spawn` 必须在canonical Runtime UoW同tx写
  只有持有Runtime-bound exact Runner的`RunClient.prove_graph_unavailable(ticket, ready_activation) ->
  VerifiedWorkflowGraphUnavailable`可调用Runner private proof factory；它在一次immutable registry snapshot中exact workflow
  name/version missing或implementation hash drift时返回，并把deterministic registry content digest与exact ready activation/
  continuation owner epochs写入proof。graph存在且匹配、另一Runner/Runtime/activation均拒绝。该proof不是admission
  authority，只能进入上述failure settlement；Runner自身不公开可脱离Runtime调用的proof API。
  `workflow_launch_ticket_receipts(ticket_receipt_id UNIQUE, canonical_payload, payload_hash, catalog_generation,
  catalog_authority_version, catalog_hash, profile_fingerprint, issued_at)`后才返回public ticket。唯一SDK-owned authority为
  `WorkflowLaunchTicketPort`，其exact surface为
  `publish_catalog(transaction, authority: VerifiedWorkflowCatalogAuthority, expected_version, *, now, fault?) ->
  WorkflowCatalogAuthority`、`read_catalog(transaction) -> WorkflowCatalogAuthority`、
  `issue(transaction, request: WorkflowLaunchRequest, issue_authority: WorkflowSpawnIssueAuthority,
  *, now, fault?) -> WorkflowLaunchTicket`、
  `read_issued(transaction, request_key) -> (WorkflowLaunchTicket, WorkflowLaunchRequest) | None`、
  `read_admitted(transaction, ticket: WorkflowLaunchTicket) -> RuntimeStartReceipt | None`、
  `claim_spawn_continuation(transaction, ticket: WorkflowLaunchTicket, issue_authority:
  WorkflowSpawnIssueAuthority, ready: WorkflowSpawnContinuationReady?, *, now, ttl_seconds, fault?) ->
  WorkflowSpawnContinuationClaim`、
  `mark_spawn_continuation_ready(transaction, ticket: WorkflowLaunchTicket, effect_snapshot,
  evidence_ref, *, now, fault?) -> WorkflowSpawnContinuationReady`、
  `list_ready_spawn_continuations(snapshot_cursor, *, limit) -> (ready_items,next_cursor)`、
  `consume_spawn_ready_and_claim_activation(transaction, ready: WorkflowSpawnContinuationReady,
  blocker_snapshot, owner_id, *, now, ttl_seconds, fault?) -> WorkflowSpawnReadyActivation`、
  `read_spawn_ready_activation(transaction, parent_run_id, activation_receipt_id?) ->
  WorkflowSpawnReadyActivation | None`、
  `reclaim_spawn_ready_activation(transaction, prior: WorkflowSpawnReadyActivation, owner_id,
  *, now, ttl_seconds, fault?) -> WorkflowSpawnReadyActivation`、
  `read_spawn_continuation_outcome(transaction, spawn_operation_id) -> ToolResult | None`、
  `read_spawn_admission_outcome(transaction, spawn_operation_id) -> WorkflowSpawnAdmissionOutcome | None`、
  `continue_spawn_admission(transaction, ticket: WorkflowLaunchTicket, continuation:
  WorkflowSpawnContinuationClaim, start: RunStart, request: StartAdmissionRequest, snapshot: StartSnapshot,
  claim: RuntimeActivationClaim, *, now, fault?) -> WorkflowSpawnToolOutcome`、
  `settle_spawn_continuation_catalog_stale(transaction, continuation: WorkflowSpawnContinuationClaim,
  ready: WorkflowSpawnContinuationReady?, *, now, fault?) -> ToolResult`、
  `settle_spawn_continuation_graph_unavailable(transaction, continuation:
  WorkflowSpawnContinuationClaim, ready: WorkflowSpawnContinuationReady?, evidence:
  VerifiedWorkflowGraphUnavailable, *, now, fault?) -> ToolResult`、
  `settle_spawn_continuation_for_parent_terminal(transaction, ticket: WorkflowLaunchTicket,
  ready_or_continuation, parent_terminal_snapshot, *, now, fault?) -> ToolResult`、
  `resume_admitted_runtime_start(transaction, ticket: WorkflowLaunchTicket, claim: RuntimeActivationClaim,
  *, now, fault?) -> RuntimeStartAdmission`、
  `verify(transaction, ticket) -> VerifiedWorkflowLaunchTicket` 与
  `admit_runtime_start(transaction, ticket, start: RunStart, request: StartAdmissionRequest,
  snapshot: StartSnapshot, claim: RuntimeActivationClaim, *, now, fault?) -> RuntimeStartAdmission`。
  `RuntimeActivationClaim(owner_id, namespace=RUNTIME_LEASE_NAMESPACE, lease_ttl_seconds)`是typed caller
  authority，namespace必须exact `runtime.kernel`、owner非空、TTL正数；禁止隐藏owner、从ticket派生owner或用real
  clock替代`now`。返回值拆成immutable
  `RuntimeStartReceipt(ticket_receipt_id, run_id, trace_id, thread_id, committed_run_version,
  start_snapshot_hash, workflow_request_hash, created_at)`、ephemeral
  `RuntimeStartActivation(execution_lease, run_fence)`、stable immutable capability
  `RuntimeStartDispatchClaim(claim_id, run_id, owner_id, runtime_lease_epoch, claim_epoch)`与durable
  `RuntimeStartDispatchRecord(claim_id, run_id, owner_id, runtime_lease_epoch, claim_epoch, expires_at,
  version, state)`（`state=CLAIMED|CONSUMED`）与
  `RuntimeStartDisposition = START_NEW | START_ORPHAN | ATTACH_CURRENT | RECOVER_START |
  RECOVER_RESUME | FOREIGN_ACTIVE | WAITING | CANCEL_PENDING | TERMINAL`、
  `RuntimeStartAdmission(receipt, disposition, activation?, dispatch_claim?, recovery_work?,
  workflow_terminal?, retry_wake?)`；
  ticket-only child admission continuation另用immutable
  `WorkflowSpawnContinuationClaim(spawn_operation_id, ticket_receipt_id, parent_run_id, owner_id,
  runtime_lease_epoch, run_fence_epoch, workflow_lease_epoch?, claim_epoch, expires_at, version)`与
  `WorkflowSpawnContinuationReady(ready_receipt_id, spawn_operation_id, ticket_receipt_id, effect_id,
  handoff_attempt, evidence_ref, version, created_at)`；该ready receipt是独立SDK-local recovery authority，不是
  `completed|confirmed_not_started|still_unknown`三态Tool reconciliation outcome，也不改变Effect handoff counters。
  wake activation使用immutable `WorkflowSpawnReadyActivation(ready_receipt, continuation_claim,
  execution_lease, run_fence, workflow_lease?, blocker_id, activation_receipt_id, activation_version,
  predecessor_activation_receipt_id?, state)`，其中`state=ACTIVE|SUPERSEDED|CONSUMED`；同一
  `spawn_operation_id/ready_receipt_id`任一时刻最多一个`ACTIVE`，successor必须以stable predecessor id串成不可分叉链。
  registry failure authority使用SDK-factory-only non-subclassable
  `VerifiedWorkflowGraphUnavailable(ticket_receipt_id, profile_key, workflow_name, workflow_version,
  expected_implementation_hash, registry_content_digest, activation_receipt_id, parent_run_id, owner_id,
  runtime_lease_epoch, run_fence_epoch, workflow_lease_epoch?, continuation_claim_epoch, observed_kind,
  observed_implementation_hash?, factory_token)`，`observed_kind=missing|drift`。
  durable start receipt永不保存或重写lease/fence token。`WorkflowRecoveryWork`必须携同一transaction读取并
  freeze的完整typed `receipt_snapshot: StartAdmissionReceipt | ResumeAdmissionReceipt`，且
  `receipt_kind/id/version/mode/due_at/request_fingerprint`必须与snapshot重算一致；resume snapshot包含原始
  canonical responses+hash，start snapshot包含完整canonical StartAdmissionRequest，Runtime不得在transaction
  外重读/重建这些字段。
  这些类型只可由该同事务command返回；首次`issue`必须在与Runtime UoW相同的
  `transaction_owner`中读取exact durable完整catalog profile binding，自行canonical validate/freeze
  objective+start input、派生所有identity/hash，验证current parent/effect authority，并原子写ticket receipt与
  `workflow_spawn_continuations(operation_id UNIQUE,ticket_receipt_id,parent_run_id,state=PENDING|CLAIMED|COMPLETED,
  owner_id?,runtime_lease_epoch?,run_fence_epoch?,workflow_lease_epoch?,claim_epoch,expires_at?,version,
  completion_receipt_id?,completion_path_kind?,effect_id,handoff_attempt,effect_request_hash,
  issue_authority_hash)`；terminal completion另写独立
  `workflow_spawn_completion_receipts(completion_receipt_id PRIMARY KEY,spawn_operation_id UNIQUE,
  ticket_receipt_id,parent_run_id,path_kind,effect_id,handoff_attempt,effect_request_hash,issue_authority_hash,
  tool_result_json,tool_result_hash,child_runtime_start_receipt_id?,failure_evidence_kind?,failure_evidence_id?,
  failure_evidence_json?,failure_evidence_hash?,activation_chain_head_id?,child_wait_receipt_id?,canonical_hash,created_at)`与
  `workflow_spawn_child_wait_receipts(parent_wait_receipt_id PRIMARY KEY,spawn_operation_id UNIQUE,parent_run_id,
  child_run_id,child_command_id,parent_pre_version,parent_waiting_version,react_checkpoint_revision,
  react_checkpoint_hash,expected_signal_domain,source_phase,batch_digest,spawn_ordinal,next_tool_ordinal,
  prior_result_append_receipts_json,budget_terminal_code?,synthetic_result_append_receipts_json,
  raw_tool_call_id,spawn_result_append_id,spawn_result_append_receipt_id,
  spawn_tool_message_hash,context_pre_revision,context_post_revision,released_runtime_lease_epoch,released_workflow_lease_epoch?,
  termination_started_at,termination_last_observed_at,wall_deadline?,termination_policy_snapshot_hash,
  released_run_fence_epoch,child_start_receipt_id,child_dispatch_claim_id,child_runtime_lease_epoch,
  state=UNCONSUMED|WOKEN|CLAIMED|ACKED_COMPLETION_PENDING|ACKED|ACKED_PARENT_TERMINAL,
  child_signal_id?,continuation_id?,wake_activation_receipt_id?,
  progress_receipt_id?,pending_child_completion_json?,pending_child_completion_hash?,
  child_completion_append_id?,child_completion_append_receipt_id?,child_completion_context_revision?,
  pending_completion_terminal_receipt_id?,pending_completion_terminal_state?,pending_completion_terminal_hash?,
  parent_terminal_phase_kind?,child_cancel_request_id?,child_cancel_receipt_id?,reused_child_cancel_receipt_id?,late_signal_quarantine_receipt_id?,
  claimed_continuation_terminal_ack_receipt_id?,
  version,identity_hash,lifecycle_hash,created_at)`；每次state推进同tx写stable successor ids并CAS version，
  identity hash永不变、lifecycle hash覆盖current state/ids。DDL采用可执行的单向闭环：先写child-wait row（`spawn_operation_id UNIQUE`并FK continuation），再写completion
  row，其non-null `child_wait_receipt_id UNIQUE` FK child-wait；child-wait不反向FK completion，reader通过两表相同operation id/
  canonical hash重算闭环。两row必须同transaction写，不能从current Run/checkpoint事后重建。
  failure evidence
  canonical JSON使用与start input相同的迭代preflight并限制UTF-8 `<=65536`、depth `<=12`、nodes `<=1024`，不得只存id或
  回查later current authority。receipt必须持久化封闭枚举
  `path_kind=DIRECT|READY_RECOVERY|PARENT_TERMINAL_TICKET_ONLY|PARENT_TERMINAL_READY_UNACTIVATED|
  PARENT_TERMINAL_ACTIVATED`并重复冻结上述effect/request/authority identity；ticket-only recovery另写
  `workflow_spawn_continuation_ready(ready_receipt_id PRIMARY KEY,operation_id UNIQUE,ticket_receipt_id,effect_id,
  handoff_attempt,evidence_ref,version,created_at,consumed_at?)`与专用
  `workflow_spawn_ready_activations(activation_receipt_id PRIMARY KEY,ready_receipt_id,spawn_operation_id,
  parent_run_id,effect_id,owner_id,runtime_lease_epoch,run_fence_epoch,workflow_lease_epoch?,continuation_claim_epoch,
  predecessor_activation_receipt_id? UNIQUE,state,version,canonical_hash,created_at,superseded_at?,consumed_at?)`；
  SQLite必须以partial unique index（`UNIQUE(ready_receipt_id) WHERE state='ACTIVE'`）或语义等价的独立current row
  保证每个ready任一时刻最多一个ACTIVE，且predecessor只能形成单链，不能覆盖旧row或复用通用
  `wait_activation_receipts(blocker_id UNIQUE)`。consume/reclaim/continue/failure/terminal settlement必须在同一BEGIN对该表
  做current ACTIVE CAS并重算canonical hash。receipt-first相同request key+相同全payload只读
  返回原ticket，任一字段不同零写conflict。stable fault labels至少覆盖
  `workflow:launch_ticket:before_receipt_write/after_receipt_write/after_commit`，close/reopen response-loss返回
  同一ticket；并发exact request只有一个receipt winner。current catalog/profile唯一authority是同库
  `workflow_catalog_authorities(authority_id PRIMARY KEY, generation, version, catalog_hash, canonical_profiles,
  updated_at)` row；catalog发布/替换只能走`publish_catalog`并以expected version CAS。Port必须验证exact
  verified type、non-exported factory token、registry snapshot/catalog/binding hashes并从verified content重算写入；
  `canonical_profiles`必须持久化每个binding的完整`StartInputSchema`（ref+canonical bytes+hash），不只存hash；
  launch ticket的`canonical_payload`也pin同一完整schema snapshot。`read_catalog/verify/admit_runtime_start`从这些
  durable bytes重建typed schema并用同一bounded validator校验actual input，close/reopen后不得查询Runner registry或
  信任caller预验；schema bytes/hash/ref任一漂移零写。
  `RuntimePorts.workflow_launch`必须是nonoptional `WorkflowLaunchTicketPort`，其`transaction_owner`与Runtime UoW及
  Runner canonical execution ports完全相同；`Runtime.__init__/build_runtime`必须fail closed验证，不得缓存Host
  registration或另持catalog。model-spawnable workflow profile不通过`RuntimeProfile`补metadata；official workflow
  driver是SDK reserved driver，profile/workflow binding只能来自本次durable ticket verification。
  直接constructor、subclass、copy/replace、手写全字段或token不匹配均零写。SQLite不得持有/查询第二个registry；
  SDK内存catalog只能是该row的
  immutable read view，不得成为另一个current authority或在transaction外热换。Runtime必须调用canonical
  `WorkflowUnitOfWork.run_atomic`取得同owner的open `BEGIN IMMEDIATE` transaction，并在该一个callback内依次
  `verify(transaction, ticket)`、调用Runner的pure `prepare_start_admission`、再调用
  `admit_runtime_start`；最后一个命令必须在同transaction重读ticket receipt与current durable catalog row，且SQL
  CAS exact `authority_id/generation/version/catalog_hash`及profile binding、
  重算request/snapshot并原子写generic Run、start snapshot及runtime activation/fence。不得在verify与generic
  write间commit、释放transaction或缓存verified object供后续transaction使用。`verify`重读该receipt、catalog/profile/
  payload hash并用非导出factory token构造exact verified type；Port `transaction_owner` 必须与
  Runtime UoW/Runner相同。`prepare_start_admission`只接exact verified type，先从Runner registry按workflow
  name/version取得CompiledWorkflow并从其manifest/schema/descriptor/factory/capabilities/input schema自行重算
  同一`WorkflowCatalogProfileBinding`，与verified ticket全字段constant-compare，之后才生成request/snapshot；
  `admit_runtime_start`再从durable catalog row+ticket canonical payload独立重算并全字段比较
  `StartAdmissionRequest`的session/request/turn/requested+resolved identities、checkpoint namespace、manifest/
  implementation/schema、terminal descriptor+factory、capability snapshot、full validated input，以及
  `StartSnapshot`的profile/driver/catalog/input/workflow admission。它不得信任caller预制hash或只比较
  run/request_key。factory token/receipt hash仍必须验证。直接constructor、copy/replace、subclass、异catalog/异DB
  receipt均在generic write前拒绝；
  restart后可从同durable public ticket receipt重新verify为同一semantic binding，不依赖进程内cache。
  `RunClient.workflow_spawn`先在canonical UoW transaction中以payload-independent `spawn_operation_id`调用
  `read_issued`：若已有receipt，必须从durable canonical payload重建完整request+public ticket并逐项比较本次
  parent/turn/tool-call identity与selection；exact则返回原ticket，selection任一变化则typed conflict，不读取或替换成
  current catalog generation。若无receipt，才读取current catalog generation、用上述唯一mapping构造
  `WorkflowLaunchRequest`并调用Port `issue`。`read_issued`只读同transaction owner的durable receipt，不接受Host
  payload，也不把旧catalog binding提升为current authority。取得ticket并进入process-local single-flight后，admission
  transaction先调用`read_admitted`：该read只在generic admission已存在时，从durable ticket receipt、
  RuntimeStartReceipt、Run、StartSnapshot与workflow request hash重算并返回原receipt；不存在返回None，任一
  corruption/conflict fail closed。它不查询current catalog，也不创建/claim authority。existing exact receipt必须在
  同一transaction调用`resume_admitted_runtime_start`；该command重读full terminal/authority rows并按既定
  START_ORPHAN/ATTACH/RECOVER/WAITING/CANCEL/TERMINAL矩阵原子claim或只读返回，禁止创建第二generic admission，且
  不依赖current catalog。只有`read_admitted is None`时才调用`claim_spawn_continuation`：PENDING或expired CLAIMED只可
  由current parent Runtime authority取得/reclaim，claim epoch原子+1；live foreign/current different epoch拒绝，same
  owner+same epoch exact返回current claim。original handler的same current authority可在PENDING时令`ready=None`；
  旧authority隔离后的新owner/epoch必须携exact unconsumed `WorkflowSpawnContinuationReady`，无ready或错effect/
  handoff evidence零写。随后才调用`verify(current catalog)`→Runner prepare→
  `continue_spawn_admission`；后者必须在同一transaction CAS current continuation owner/runtime/fence/workflow
  epoch+expiry、current parent authority与catalog/ticket/start request，并原子创建`parent_run_id=spawn_origin.parent_run_id`
  且`root_run_id=WorkflowLaunchRequest.root_run_id`的child Run、`attachment_policy=ATTACHED`的durable `run_links` row、stable
  child launch command/receipt与generic RuntimeStartReceipt/StartSnapshot，再把continuation置COMPLETED+completion receipt；
  这些rows必须逐项匹配ticket里的parent/root/child command identity，禁止写`parent_run_id=NULL`的新root或拆transaction调用
  第二套child launcher。同一BEGIN还必须ledger-first settle workflow_spawn Effect/ToolResult、把durable ReAct checkpoint CAS到
  terminal后先按T3.3 canonical Context protocol用raw Provider tool call id、stable
  `append_id=SHA256("workflow-spawn/context-result/v1",spawn_operation_id)`与expected context revision把exact ToolResult message
  append一次，并把append receipt/post revision/message hash写child-wait receipt；Effect result canonical hash必须与Context Tool
  message重算一致。随后才把durable ReAct checkpoint CAS到
  `CHILD_WAIT(child_command_id,child_run_id,parent_wait_receipt_id,expected_signal_domain,
  source_phase=tool_batch_reserved,batch_digest,spawn_ordinal,next_tool_ordinal,prior_result_append_receipts,
  spawn_result_append_receipt_id,context_revision)`、把parent Run从当前RUNNING revision CAS到
  WAITING并写unique wait receipt，然后release parent Runtime/Workflow lease与RunFence；返回的factory-only
  `WorkflowChildWaitBinding`必须逐项对应这些rows。也就是说child materialization、Tool outcome、parent WAITING/checkpoint与
  Context append、authority release是一个atomic WorkflowTransaction；不存在handler return后另tx补Tool message或“Tool成功后再
  进入WAITING”的窗口。该child terminal的COMPLETED/FAILED/CANCELLED强制走T3.2
  `finalize_child_and_enqueue_parent_signal`，从durable run_link读取ATTACHED policy并产生唯一parent signal/continuation；parent
  ReAct按既有FIFO continuation恢复，不能走root terminal/delivery。official EffectExecutor把
  `WorkflowSpawnAdmissionOutcome.result`作为用户可见ToolResult，同时把`suspension`作为typed internal outcome交给ReAct；ReAct
  必须在任何下一Provider/Tool/terminal前逐项验证binding并立即退出Driver，禁止Host handler自行决定是否suspend。旧Driver即使
  crash后恢复也因parent WAITING+released authorities零物理调用。old handler即使ticket exact也不能绕过claim；after-commit same claim只读返回原
  admission，异claim/payload零写。parent cancel/terminal时PENDING/CLAIMED continuation终止为COMPLETED failed outcome
  且不建child Run。因此旧generation
  未admit ticket返回typed `catalog_stale`且零Run，已admit ticket receipt-first返回原Run，不能误报payload conflict或
  签第二ticket。commit durable public ticket后才以`ticket_receipt_id`进入下述process-local
  single-flight；禁止直接构造ticket、直写SQLite receipt、
  调私有factory或由Host提供已验证binding。Port缺失、transaction owner不一致、catalog/profile漂移必须在
  generic Run/start admission前零写拒绝。
- `WorkflowLaunchTicketPort`/SQLite command不得持有第二个in-memory compiled registry，也不得接受Host提供的
  `StartAdmissionRequest`作为authority；durable catalog row是transaction内的compare authority，Runner compiled
  registry只负责纯构造并必须与它精确匹配。测试必须先用SDK `publish_catalog`+Runner factory生成合法binding，禁止
  手写任意manifest/schema hash；直接构造/copy/replace verified catalog均在catalog row前零写。逐一变更上述每个
  binding/request/snapshot字段均在runs/start snapshots前零写。
  start schema门禁必须覆盖oversized bytes、depth、node/property/required/enum/string各上限、raw cyclic mapping、
  external/recursive `$ref`、pattern/format/unknown keyword与非finite numeric；official/custom registrations均走同一
  validator并在超限时catalog/ticket/Run三层零写，actual start input oversized/deep同样在ticket/Run前拒绝。
  补catalog publish→close DB并销毁原registration/schema objects与cache→同路径reopen，以只含相同
  CompiledWorkflow registry、但没有Host提供StartInputSchema/registration cache的fresh Runner执行ticket issue/admit
  valid+invalid input：schema验证只凭durable bytes，compiled executable/manifest仍从fresh registry取得并独立与
  durable binding compare。fresh registry缺workflow或binding drift必须`graph_version_unavailable`/零Run；删除/篡改
  schema bytes、ref或hash同样fail closed且零Run，证明hash-only carrier不可通过且没有第二executable authority。
- `admit_runtime_start`的receipt-first key为ticket receipt ID，existing generic admission必须逐项比较
  resolved run/trace/thread、session/request/catalog、full canonical input、StartAdmissionRequest与StartSnapshot；
  exact replay永远返回同`RuntimeStartReceipt`，同ticket配不同RunStart/request/snapshot一律零写conflict，不能生成
  第二个Run。command在任何activation判断前必须同transaction读取并交叉验证generic Run state/version、workflow
  start/resume receipt phase、checkpoint head、start dispatch claim与terminal/retry receipts。新插入generic
  receipt必须在同transaction创建stable `(ticket_receipt_id, run_id)` dispatch record并返回对应claim capability的
  `START_NEW`；只有
  durable一致的RUNNING/可恢复orphan状态允许activation处理。active foreign owner返回`FOREIGN_ACTIVE`且不改
  receipt。Runtime `owner_id`必须是每个进程activation随机生成且不跨进程复用。Runtime在进入run_atomic前必须先以
  `ticket_receipt_id`取得进程内single-flight start slot，并持有到Driver task已原子注册进live index；followers只
  await/attach leader，不能调用admit command或Driver。leader遇after-commit异常必须释放slot，使下一caller可读取
  durable record继续。对这个唯一leader，同owner且未过期、已有`CLAIMED` dispatch record但无workflow receipt时返回
  同一`START_ORPHAN` activation+claim；已有matching start/resume receipt则返回`ATTACH_CURRENT`且不暴露
  activation/claim、不启动第二Driver。无authority或
  已过期才在同transaction按
  `max(workflow_leases epoch, run_fences.runtime_lease_epoch)+1` CAS claim新Runtime lease并取得匹配
  RunFence；无workflow start receipt时还必须CAS dispatch record到新owner/runtime epoch并令claim_epoch+1，才返回
  `START_ORPHAN`；已有start receipt返回携同tx
  `WorkflowRecoveryWork`的`RECOVER_START`，已有resume receipt返回`RECOVER_RESUME`，不得把新token回写
  immutable receipt。generic WAITING与匹配workflow
  WAITING/RETRY_WAIT只返回`WAITING`并交给canonical blocker/wake/recovery路径；即使due已到也不得在start replay
  中claim或调用Driver.start。generic CANCEL_REQUESTED/CANCELLING只返回`CANCEL_PENDING`并交cancel convergence；
  COMPLETED/FAILED/CANCELLED必须以full terminal verifier成功后返回`TERMINAL`。CREATED/ADMISSION_PENDING/QUEUED、
  generic/workflow phase不一致、terminal验证失败均fail closed/零写，不能猜测修复。
  durable dispatch record的`expires_at/version`必须与绑定Runtime lease同事务co-renew/release；Driver持有的
  stable claim capability不得随heartbeat替换。只要该Runtime lease仍active，
  即使本地task暂未写workflow receipt也禁止claim takeover。Runtime lease expiry/takeover后，新runtime epoch才可CAS
  claim；旧claim不能被same/different owner用于ensure或物理出站。
  exact allowed-field matrix为：`START_NEW|START_ORPHAN`只允许`activation+dispatch_claim`，其余四个optional全None；
  `RECOVER_START|RECOVER_RESUME`只允许`activation+recovery_work`且work kind必须匹配disposition，claim/terminal/
  wake全None；`ATTACH_CURRENT|FOREIGN_ACTIVE|CANCEL_PENDING`五个optional全None；`WAITING`仅允许在durable
  RETRY_WAIT时`retry_wake!=None`，普通HITL/blocker WAITING则五个optional全None；`TERMINAL`只允许
  `workflow_terminal!=None`。任何其他组合在Runtime调度前拒绝，零Driver、零timer、零convergence。
  Runtime只对`START_NEW|START_ORPHAN`调用`Driver.start`，只对两个`RECOVER_*`以结果携带的原样
  `WorkflowRecoveryWork`调用`Driver.recover`；Runtime必须把`START_*`结果中的dispatch claim作为reserved typed
  `DriverInvocation.workflow_start_dispatch`原样传给exact Workflow Driver，其他driver/RECOVER设置该字段均拒绝；
  Driver只能把它传给`ensure_and_bind_precreated_start`消费。`ATTACH_CURRENT|FOREIGN_ACTIVE`返回existing handle，WAITING注册或保留
  canonical wake并返回waiting handle，CANCEL_PENDING触发durable cancel convergence，TERMINAL只读返回原outcome。
  旧owner/epoch、wrong namespace、
  `now >= expires_at`均不能执行Driver。stable per-table before/after-write与after-commit fault、close/reopen、并发winner，以及
  catalog mutation与admission必须共享同一个`transaction_owner`/BEGIN IMMEDIATE：若publisher先CAS到G+1，旧ticket
  admission零Run/零snapshot；若admission先持write transaction，publisher必须阻塞到admission commit，已提交Run合法
  pin G且随后publish G+1不改写该snapshot。补final catalog row read后/first generic write前barrier覆盖这两种顺序，
  禁止用非durable registry swap伪造测试。
  另补generic start commit响应丢失且Driver尚未创建workflow receipt的两支：TTL内leader重试得到same receipt+
  same `START_ORPHAN` activation/claim，但single-flight保证原leader仍活跃时只attach且零第二Driver；TTL后新进程/
  owner重试得到same receipt+`START_ORPHAN` epoch递增的新activation/claim，
  旧activation不能bind workflow或出站。已有workflow start/resume receipt且live same owner只返回
  `ATTACH_CURRENT`，必须复用Runtime live index中的现有task而非二次dispatch。
  barrier回归固定A已取得dispatch claim并进入Driver、暂停在workflow receipt前，B同ticket重试在single-flight
  slot附着A，admit command/Driver/node/provider/tool计数保持单次；A在after-commit、注册task前异常释放slot后，
  B成为leader并用same claim启动一次。heartbeat续租跨多个原claim TTL仍不得让另一进程takeover。
  另用虚拟时钟让A暂停跨多个heartbeat续租：A恢复后以最初stable capability读取current record/version并只消费
  一次；随后新runtime epoch takeover后，A的旧runtime/claim epoch capability即使claim id相同也必须零写拒绝。
  再补terminal、HITL WAITING、未到期与已到期RETRY_WAIT、CANCEL_REQUESTED四类同ticket replay，全部必须
  `activation=None`且node/provider/tool零调用；RETRY_WAIT只能由timer/startup scanner取得下一activation。
  official `workflow_spawn`还必须提供SDK-owned T2.5 reconciliation，而不是复用Host generic observer猜结果。
  reconciler从EffectRecord冻结的run/turn/internal call/arguments与durable ReAct pin重算同一spawn operation id，并读取
  ticket/admission receipts：已有matching generic admission时必须调用上述唯一`WorkflowSpawnResult` factory，从同一组
  ticket/RuntimeStartReceipt/child Run/run_link/command生成byte-exact stable handle payload，以该durable evidence
  ledger-first settle exact `ToolResult.succeeded`，不要求旧parent lease且不再调用handler；只有ticket、尚无admission时，
  若旧parent Runtime/RunFence仍可能让handler继续，reconciler仍按冻结三态返回`still_unknown`；旧authority已被隔离且
  parent仍可恢复时，reconciler调用`mark_spawn_continuation_ready`写独立evidence-bound ready receipt+durable wake，不能
  claim Runtime authority、不能写三态resolution、不能直接admit。`list_ready_spawn_continuations`按stable operation id
  keyset、limit `1..50`只枚举unconsumed ready及其exact parent Tool blocker；ready先于WAITING/blocker时先durable保留，
  blocker后写后自然变eligible，不得丢wake。wake drain必须调用单一
  `consume_spawn_ready_and_claim_activation`：同一transaction验证ready/effect attempt/blocker、claim或reuse current
  Runtime lease+RunFence/WorkflowLease、CAS parent `WAITING->RUNNING`、写unique activation receipt，并把原blocker标
  `superseded_by=ready_receipt_id,wake_consumed=true`；same receipt exact replay返回同activation，任一identity变化零写。
  generic late reconciliation resolution遇superseded blocker只能记录evidence，不得再次activate。crash在consume任一
  write前后由全rollback或activation receipt+RUNNING recovery继续，禁止scanner先消费ready再另tx schedule。
  `DriverInvocation`新增factory-only optional `workflow_spawn_ready: WorkflowSpawnReadyActivation | None`；只允许ReAct
  driver携带，其他driver/non-ready invocation必须None。Kernel在consume成功后把exact returned value交给scheduled
  Driver；若crash在consume after_commit→schedule前或schedule→driver前，startup RUNNING recovery必须调用
  `read_spawn_ready_activation`只在same owner+same current runtime/fence/workflow epochs时从ACTIVE、unconsumed receipt
  重建同值。若原Runtime authority已expired/released，canonical RUNNING startup takeover必须调用
  `reclaim_spawn_ready_activation`：同一transaction验证prior ACTIVE/unconsumed、旧lease/fence失效、same ready/effect/
  operation，原子claim新ExecutionLease+RunFence/WorkflowLease、continuation claim epoch+1，写successor ACTIVE activation
  receipt并把prior置SUPERSEDED；任一时刻最多一个ACTIVE。异owner与same-owner epoch+1都走该path，旧owner恢复后所有
  continue/failure settlement零写。不得复用旧token或重新打开已superseded blocker。ReAct进入
  generic UNKNOWN observer前必须先逐项匹配run/turn/effect/handoff attempt/continuation claim并执行spawn continue或
  typed failure；completion同tx consume activation receipt。缺失/错receipt fail closed，不能再次写WAITING或新blocker。
  spawn ready activation成功进入`continue_spawn_admission`时，该ready/activation/original Tool blocker必须先在同一BEGIN
  完整consume，随后创建的是独立`CHILD_WAIT` receipt；两者不可同时eligible。child terminal signal无论在admission返回前或后
  到达，都只通过T3.2 HOL signal/continuation消费该CHILD_WAIT一次；generic late spawn resolution不得唤醒child wait。
  child signal/HOL continuation wake时，不得直接使用raw terminal payload；只能调用T4.3同一approved
  `terminal_public` factory从immutable child terminal projection生成
  `WorkflowChildCompletionMessage(version="workflow_child_completion.v1",child_command_id,child_run_id,terminal_state,
  terminal_receipt_id,public_result?,public_error?,delivery_refs)`。exact allowlist仅含这些key；public_error只含stable
  `code/message/recovery_action`，不得含private cause/trace/evidence；delivery只传validated safe content ref+bounded summary，不传blob/
  private metadata。投影在freeze/Context write前迭代限制canonical UTF-8 `<=65536`、depth `<=8`、nodes `<=256`、每string
  `<=8192`、arrays/maps各`<=64`并递归拒绝private/secret-like keys与nonfinite/cycle。若源projection超限或privacy-invalid，factory
  必须产生deterministic bounded public failure summary（stable code=`workflow_child_public_projection_rejected`，不含原始bytes）并继续
  completion/ACK，不能反复重试巨型payload或让parent stranded。生成的bounded canonical message写入out-of-band pending
  continuation receipt/checkpoint，不能立刻追加Provider Context或复用raw call k。随后必须在任何k+1 Tool前调用
  `ack_continuation_and_continue_tool_batch`，把pending message放进durable ordered queue、ACK当前continuation并推进child-wait到
  ACKED_COMPLETION_PENDING；`state in {ACKED_COMPLETION_PENDING,ACKED} iff continuation ack receipt exists`。
  ReAct再从checkpoint冻结的exact `next_tool_ordinal=spawn_ordinal+1`继续同一ordered batch，先核验batch digest/prior append
  receipts/context revision；按T3.3既有ordered progress逐个执行k+1..N、每个Effect与Context append均exact一次。k+1若再次
  workflow_spawn、UNKNOWN、retry或HITL，因旧continuation已ACK可独立进入其正常suspension/blocker，pending completion queue仍
  durable保留。全部raw ToolResults闭合后调用`commit_pending_child_completions_and_react_ready`，按
  `(spawn_ordinal,terminal_receipt_id)`稳定顺序用各自stable append id与exact Provider-compatible `role="user"` canonical JSON
  content追加所有pending completion message一次，写append receipt/revision并把对应child-wait推进ACKED；此后才允许下一Provider。
  parent因cancel、permanent Driver/handler exception或正常terminal提前收敛时，所有parent root terminal/cancel/
  continuation-aware terminal command必须在其同一BEGIN枚举并full-verify该parent所有nonfinal child-wait，统一CAS到
  `ACKED_PARENT_TERMINAL`并写parent terminal receipt id/state/hash，但按来源冻结
  `parent_terminal_phase_kind=CHILD_ACTIVE|SIGNAL_PENDING|CONTINUATION_CLAIMED|COMPLETION_PENDING`：CHILD_ACTIVE从durable
  ATTACHED run_link/child command执行receipt-first child cancel matrix：child为RUNNING|WAITING时首次派生stable child-cancel receipt并
  在parent terminal同一BEGIN CAS到CANCEL_REQUESTED；已为CANCEL_REQUESTED|CANCELLING时必须读取并full-verify该child唯一durable
  cancel command/receipt的child id、stable cancel identity与target terminal policy，在parent closure只写
  `reused_child_cancel_receipt_id`，不改child state/版本、不建第二request；cancel worker后续版本推进仍按immutable cancel receipt验证。
  existing cancel错child/损坏才conflict；child已terminal则receipt-first读取terminal outcome。该cancel receipt/
  child state必须成为以后每次Provider/Tool handoff同BEGIN的Run-state gate，CANCEL_REQUESTED时零ledger/transport/handler；Kernel
  durable cancel convergence隔离active task、reconcile已handoff effect并最终release child fence，不要求parent伪造child lease。
  child稍后terminal的signal只能写quarantine receipt；
  SIGNAL_PENDING同tx把未claim signal/continuation标terminal-quarantined；CONTINUATION_CLAIMED只允许continuation-aware terminal
  command凭current claim同tx写ack/progress+quarantine receipt；COMPLETION_PENDING按前述queue tombstone，不追加Context、不调用
  Provider。terminal对外完成前必须完成全部closure，不能由startup事后best-effort；after-commit exact replay返回同terminal/
  tombstone/cancel/quarantine evidence。A/B多项wait必须全有或全无地封闭，ATTACHED child不得成为无cancel authority的孤儿。
  Provider Context中不能生成第二条raw call k的tool message。不能从ordinal1重跑、跳到Provider或重扣已reserved batch预算；
  后续普通Tool已PREPARED/UNKNOWN/terminal分别走既有ledger-first恢复，不另造identity；每个尚未物理handoff的Tool都必须先校验
  current durable wall fence/last_observed，child wait前的call/cost/count reservation不重复扣，wall authority绝不因旧batch reservation
  被旁路。
  wake后的current ReAct owner读取ready activation，构造new
  `WorkflowSpawnIssueAuthority`并调用`claim_spawn_continuation`+`continue_spawn_admission`（不签第二ticket、不调用Tool
  handler/transport、不增加handoff_attempt或rehandoff_count）；current catalog仍匹配则创建exact一个Run并ledger-first
  settle success，已stale则settle typed `workflow_catalog_stale` failure，并原子consume ready/complete continuation。old handler
  无论ticket exact都因continuation owner/epoch CAS零写；无ticket时只有旧handler
  authority已确定失效/隔离才写`confirmed_not_started`，否则`still_unknown`，禁止blind replay。ticket或admission任一
  identity/hash/origin不匹配fail closed，不得用“存在某Run”伪证据。fault matrix覆盖ticket commit前/后、admission后、
  EffectResult settle before/after/after_commit、close/reopen、旧handler恢复与reconciler并发；最终必须exact一个ticket、
  一个child Run、一个terminal Tool outcome，parent解除UNKNOWN继续，且confirmed-not-started fresh attempt仍复用相同
  operation id并受T2.5至多两次物理handoff上限。
  claim后若current catalog确定性漂移，只能调用`settle_spawn_continuation_catalog_stale`，command在同tx自行重算
  durable current catalog/ticket mismatch；若Runner compiled registry missing/drift，Runner必须在一次immutable registry
  snapshot上用private factory生成`VerifiedWorkflowGraphUnavailable`，携expected/observed binding与snapshot id/hash。
  `settle_spawn_continuation_graph_unavailable`验证exact factory type/token、ticket/profile/expected hash与current claim，
  还必须逐项匹配current `WorkflowSpawnReadyActivation`的activation receipt、parent/owner/runtime/fence/workflow/claim epochs
  与Runtime-bound registry content digest，不让SQLite查询第二registry。所有settlement第一步调用
  `read_spawn_continuation_outcome`：只有确实不存在completion receipt时返回`None`；一旦completion row/continuation terminal
  pointer任一存在却缺行、损坏或错链，必须fail closed抛`UnitOfWorkConflict`，绝不能返回`None`后把已settled当成未settled重试。
  已有terminal outcome按operation id只读返回，不重造proof、不比较fresh process snapshot，
  但绝不能只按operation id信任一张completion row。reader必须在同一read transaction从该operation id开始逐层重算并
  constant-compare完整durable chain：ticket canonical payload/origin/request+selection hash -> continuation operation id/
  ticket id/completion receipt/path_kind/effect id/handoff attempt/request+issue-authority hash -> Effect terminal result canonical hash，
  并按封闭path matrix验证后续shape：`DIRECT`必须完全没有ready/activation row，且从continuation冻结的direct issue authority
  重算到Effect terminal；`READY_RECOVERY`必须存在exact ready receipt与完整ACTIVE->SUPERSEDED*->CONSUMED activation successor
  chain；三种parent-terminal path都必须关联exact parent terminal receipt：`PARENT_TERMINAL_TICKET_ONLY`必须零ready/零activation，
  `PARENT_TERMINAL_READY_UNACTIVATED`必须exact一个unconsumed ready、零activation并由terminal command同tx consume ready、
  supersede/consume原blocker，`PARENT_TERMINAL_ACTIVATED`必须有ready与被terminal command消费的完整activation chain。任何hybrid、
  path_kind漂移、该有却缺或该无却多均fail closed。
  success outcome还必须exact关联同ticket的RuntimeStartReceipt/child Run/StartSnapshot；
  failure outcome必须exact关联allowlist中的`catalog_stale|graph_version_unavailable|workflow_parent_terminal_before_spawn`
  immutable durable evidence与对应settlement receipt：catalog stale evidence冻结ticket catalog generation/version/hash与settlement时
  observed authority id/version/hash；graph unavailable evidence冻结完整verified proof fields、registry content digest、observed binding
  与activation receipt/claim epochs；parent terminal evidence冻结terminal receipt id/state/hash。settlement与Effect/completion同tx写
  canonical evidence bytes+hash，reader只从这些历史bytes重算kind/id/hash，不查询后续catalog或fresh registry。任一缺行、跨parent/child Run、跨ticket/effect、重复ACTIVE、broken predecessor、
  identity/hash/result漂移都fail closed，不得返回ToolResult。只有全链验证通过才receipt-first返回原terminal outcome；未settled时
  才验证current proof，另一Runner/Runtime/activation零写。两command均同tx CAS continuation/ready/ticket/effect、consume ready/activation receipt、complete
  continuation并settle exact terminal failed ToolResult，零Run/零handler。direct constructor/copy/replace、另一DB/ticket/
  registry snapshot、caller自选错误码、非确定性异常或stale evidence拒绝；覆盖claim后catalog mutation、两个不同registry
  进程（A graph正确、B graph missing/drift）交错伪proof零写、合法failure并发winner、逐write fault与after-commit换process/
  snapshot id后receipt-first exact replay；另以真实SQLite覆盖normal direct success、direct catalog-stale、ticket-only early、
  mark-ready after_commit但consume前parent terminal、activated parent terminal与ready-recovery的before/after/after_commit close-reopen，
  late consume/reclaim必须零写；逐path_kind/FK/identity/hash/predecessor/result/evidence field做删除、插入伪ready/activation与hybrid
  mutation，并在catalog settle后继续publish G+2/G+3、销毁Runner后仍从immutable evidence重放；close/reopen后只有原合法shape的
  全链outcome可只读返回。
  success path的`read_spawn_admission_outcome`采用同样receipt-first/fail-closed规则，并额外full-verify completion↔child-wait
  receipt的历史pre/post revision与CHILD_WAIT checkpoint hash↔released Runtime/Workflow/RunFence epochs↔child
  RuntimeStartReceipt/dispatch claim/current-or-recoverable child activation，以及Effect terminal ToolResult↔Context raw call/
  stable append id/receipt/pre-post revision/message hash exact equality；验证通过才由SDK factory重建field-exact
  stable `WorkflowSpawnAdmissionOutcome(child_start_ref,result,suspension)`，而不是只返回ToolResult或旧lease/fence。
  reader不得要求parent当前永远WAITING或checkpoint仍停CHILD_WAIT：wait state为UNCONSUMED时才验证current WAITING+CHILD_WAIT；
  WOKEN/CLAIMED/ACKED_COMPLETION_PENDING/ACKED/ACKED_PARENT_TERMINAL时按实际phase验证child signal -> T3.2 HOL continuation -> wake activation/pending
  completion/progress receipt/final context append successor chain；ACKED_COMPLETION_PENDING与ACKED必须有exact canonical
  continuation ack/progress receipt，前者必须无final completion append，后者必须有exact append。ACKED_PARENT_TERMINAL必须无
  late append，并使用封闭nullability matrix：CHILD_ACTIVE要求signal/continuation/ack/progress全None，且恰有首次child cancel
  receipt/state CAS或`reused_child_cancel_receipt_id`二者之一；
  SIGNAL_PENDING要求signal/continuation quarantine但claim/ack/progress全None；CONTINUATION_CLAIMED要求current claim+ack/progress+
  quarantine；COMPLETION_PENDING要求既有ack/progress+queue tombstone。任一phase出现多余/缺失字段都fail closed；同时
  exact关联parent terminal receipt与phase-specific evidence：CHILD_ACTIVE验证stable child cancel request以及late terminal signal quarantine，
  SIGNAL_PENDING验证signal/continuation quarantine，CONTINUATION_CLAIMED验证current-claim ack/progress/quarantine，
  COMPLETION_PENDING验证queue tombstone；不再要求current parent WAITING。parent
  后续再次WAITING或terminal则继续验证对应atomic progress/terminal receipt。新Runtime/Workflow/RunFence epoch只需严格高于历史
  released epoch，不得当corruption；伪successor、跳过HOL或错signal fail closed。child terminal+parent resume/terminal后再次read/
  reopen仍必须返回同一stable outcome。
  `continue_spawn_admission` after-commit重试第一步调用该reader：同process且原child activation仍current可只读取得；fresh process、
  TTL takeover或same-owner epoch+1必须用stable `ChildStartDispatchRef`调用canonical
  `resume_admitted_runtime_start`/START_ORPHAN recovery，在同tx取得本次current child activation，再由private factory组合成
  disposition-exact `WorkflowSpawnChildControl`与`WorkflowSpawnToolOutcome`。user result/suspension/ref byte/field exact，ephemeral activation只要求current-authority exact，绝不
  返回历史token。旧owner replay、TTL takeover与same-owner epoch+1时旧control零schedule，新control exact一次。任一binding/
  checkpoint/release/dispatch字段漂移抛`UnitOfWorkConflict`且零reschedule。逐字段mutation、每层return前crash与fresh-process
  replay覆盖stable Outcome与current live control。
  若parent已`CANCELLED|COMPLETED|FAILED`而无active Runtime owner，startup/cancel convergence必须调用
  `settle_spawn_continuation_for_parent_terminal`：同一transaction full-verify parent terminal receipt、ticket/
  continuation/ready/effect identity与handoff attempt，并枚举/full-verify该ready的唯一current ACTIVE activation（ticket-only
  early state允许不存在）；若存在则必须同BEGIN CAS `ACTIVE->CONSUMED`、写terminal completion receipt与chain hash，再原子把
  continuation+ready置COMPLETED/consumed并把Effect settle为terminal failed
  `workflow_parent_terminal_before_spawn`。command还须验证parent terminal已经释放/失效的Runtime/Workflow lease与RunFence，
  不要求伪造live authority，不创建child Run。terminal settle、reclaim与continue在同一current-ACTIVE CAS上只允许一个winner；
  loser receipt-first返回winner outcome或零写conflict，绝不能遗留ACTIVE给startup/Driver。same receipt exact replay只读返回，
  异evidence/payload conflict；覆盖三terminal states、两个reconciler并发、terminal/reclaim/continue三方barrier、每write fault与
  after_commit reopen，最终full-chain outcome可读、零ACTIVE activation。
- 改法：control schema从 frozen model-spawnable catalog生成；Host只 validate + bind ticket；无 regex/
  classifier/route_hint。conditional registration检查 required Ports。
- tests：`test_agent_selected_profile.py`, `test_stale_catalog.py`, `test_forged_binding.py`,
  `test_optional_profiles.py`；另补真实SQLite/Runtime `parent Agent -> workflow_spawn -> ATTACHED child ->
  child terminal -> unique parent signal -> FIFO continuation -> parent ReAct resume`闭环，覆盖child admission/run_link/command各
  write fault、child terminal before/after ToolResult return、admission after_commit→Driver crash、旧Driver恢复、parent terminal
  race、parent wake前后close/reopen；child signal前下一Provider/Tool/terminal物理调用必须为0，signal后只wake/恢复一次；逐项断言parent/root/ATTACHED/command identity及
  DIRECT/READY_RECOVERY/receipt-first三条路径的`WorkflowSpawnResult` typed fields与canonical bytes完全相同。另覆盖spawn为
  ordered batch first/middle/last、k+1 Tool已/未PREPARED/UNKNOWN、Context append before/after/after_commit与各cursor crash点；
  budget不重复，physical effect与Context Tool message各exact一次，下一Provider request必须先包含spawn及剩余batch的完整有序
  ToolResults，再包含exact一条`role=user` child completion canonical message。补direct/ready catalog stale、graph unavailable、parent
  terminal三类`WorkflowSpawnFailed`，active failure继续batch且child/wait rows为0，parent terminal不恢复batch。补completion append
  后/下一Provider前后crash、claim takeover，断言ACKED iff T3.2 ack receipt；补private cause/secret-like key、oversize/deep/cycle/
  nonfinite、artifact/delivery safe ref与reopen canonical bytes，Provider request零private payload且parent始终可progress/ACK。
  补child A wake→completion append→running ACK→下一Provider spawn child B，B先/后terminal及每write crash，A/B signal FIFO各一次；
  再覆盖ACK后下一步进入UNKNOWN resolution-before-WAITING、retry due、HITL decision与terminal，旧A claim不得出现在任何新blocker/
  wait command中。用virtual clock覆盖child跨parent wall deadline的`now==deadline`、`>deadline`、clock rollback与ACK各write
  before/after/after_commit：旧continuation仍可ACK且parent可BUDGET_EXCEEDED terminal收敛，剩余Tool ledger/handler全0；未超限
  path的每个后续Tool current wall fence通过且不重复预算扣减。deadline/rollback分别发生在wake前、k+1前、k+m前、child
  completion append前时，所有未执行raw call都按序得到exact synthetic rejected Context receipt，零Effect row/handler，旧claim最终
  ACK且不stranded。
  补A及A+B pending queue阶段的parent cancel、permanent FAILED exception、normal terminal与append/terminal race；每write/
  after_commit reopen必须全量落`ACKED_PARENT_TERMINAL`或全量append，零late Context/Provider，原spawn stable outcome仍可读。
  另在child terminal前、signal enqueue后/claim前、continuation claim后、completion pending后分别触发parent cancel/terminal及
  after_commit；逐phase断言stable child cancel或signal/continuation quarantine/current-claim ack evidence，最终无可唤醒
  continuation、无无cancel authority的active ATTACHED child，spawn outcome始终可读。
  对CHILD_ACTIVE补确定性barrier：child持active lease/fence暂停在Provider/Tool handoff前，parent terminal同tx写cancel receipt+
  CAS child CANCEL_REQUESTED后child恢复，effect ledger/transport/handler全0；已handoff只reconcile。cancel与child terminal并发只一
  terminal winner且late signal quarantine。四phase逐字段nullability mutation/reopen reader均覆盖。
  覆盖child cancel first→parent terminal、parent first→child cancel、CANCELLING barrier与cancel terminal after_commit：parent closure
  首次或复用恰一个cancel authority，parent terminal均可完成，旧child handoff仍为0。
  覆盖admission commit→schedule前cancel、active child cancel、parent-driven cancel与after_commit fresh reopen：control必须exact
  CANCEL_PENDING→CANCEL，wrong tag/optional fields拒绝，Provider/Tool物理调用0，最终signal或quarantine唯一收敛。
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
