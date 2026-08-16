# Slice A — SDK v0.1.1 public Runtime/Workflow candidate

> 状态：FINALIZED — A7 gate 迁移已获用户明确批准（2026-08-16）  
<!-- plan-status: finalized -->
> 仓库：`/Users/denny/projects/simple-harness-sdk`  
> AC：SDK-AC-5/6/7/8 的 SDK-owned 部分  
> 风险面：1（SDK Harness/Workflow authority）  
> 依赖：program acceptance/behavior contract/oracle 已冻结；不依赖产品代码修改

## 出口与失败边界

本 slice 只生成本地 `0.1.1` exact wheel/sdist candidate，不改产品 vendor、不建远端 tag/Release。
任一 public seam、HITL、delivery、official Profile、conformance、reproducibility 或本地 exact-candidate 门失败，
slice A 不出 receipt，slice B 不开工。

## 已验证技术假设

`spike-public-runtime.md` 已证明：v0.1.0 的第一个阻断是空的
`simple_harness.workflow` public namespace；临时 lazy exports + frozen Host-port injection 后，真实
SQLite、Runtime、official `personal_v1`、terminal、close/reopen/recover 可闭环，Host effect 不重放。
正式实现必须保留 lazy export boundary，并补 spike 未覆盖的 durable_task/capability_build/mid-node。

## A1 — Public composition 与 host-owned Workflow registration [AC-6,7,8]

- 新增 `src/simple_harness/workflow/__init__.py` lazy `__getattr__`/`__dir__` public exports，至少公开
  `WorkflowContext/WorkflowDefinition/WorkflowRunner/WorkflowRegistry/CheckpointExecutionAdapter/
  ExecutionPorts/WorkflowProfileRegistration`，避免 `runtime.kernel ↔ workflow.runner` eager cycle；
  现有 registration 实现在 `runtime/orchestration.py:108-123`，必须从稳定 public builder 导出。
- `workflow/contracts.py` 定义 frozen `WorkflowHostServices`：字段采用 typed Protocol，不接受任意
  mapping；official optional groups 为 durable-task、personal、capability-build；未知字段 fail closed。
- `workflow/runner.py` 构造时接收同一 frozen Host services，所有 start/start_precreated/run/resume/
  recover/recover_expired 路径都从 runner 注入，不允许调用方按次篡改；terminal recover 幂等返回。
- public registry 接收 Host-owned frozen `WorkflowDefinitionRegistration`：字段固定为 `profile`
  （`WorkflowProfileRegistration`）、`definition`（`WorkflowDefinition`）、`dependency_lock_hash`、
  `expected_manifest_hash`、`expected_implementation_fingerprint` 和 `transaction_owner`。SDK 唯一 public
  `compile_workflow_registration()` 负责 `definition -> CompiledWorkflow -> sealed registry entry`，重新计算
  manifest/implementation fingerprint并核对 expected 值；`transaction_owner` 必须在 compile/register 前与
  Runner UoW owner 使用 `is` 相等。profile key 或 `(workflow_name, workflow_version)` 重复时，相同 fingerprint
  幂等返回，不同 fingerprint fail closed；该 registration 与 builder 都进入 `simple_harness.workflow`
  public export/API snapshot。graph execution、checkpoint、lease、ticket、recovery 和 terminal delivery仍完全
  由 SDK-owned Runner 执行。
- `simple_harness.tools`公开host-neutral `EffectKind/EffectPolicy/ToolInventoryRecord/Sidecar`与typed bounded
  scalar-map schema合同：动态map只允许key pattern+maxProperties+scalar value type/maxLength，继续拒绝
  `additionalProperties:true`和无界嵌套object；这些合同不含产品路径、权限类别或UI字段。
- 测试：public-only import snapshot；eager-cycle negative；host definition 与 official definition 用同一
  transaction owner；wrong owner/duplicate key/version/fingerprint/unknown port fail closed；closed object/
  typed bounded map正例与裸open/unbounded/deep map负例。

## A2 — 三个 official Profile factory [AC-6,7]

- 对齐 `workflows/durable_task/ports.py` Protocol 与 `nodes.py` 实际调用；删除 tests 中绕过真实
  `WorkflowContext` 的裸 mapping mocks。
- `durable_task/__init__.py` 公开 frozen profile descriptor、definition 与 initial-state factory。
- `personal_v1/__init__.py` 公开 compiled definition/profile factory；candidate selection 仍是 frozen
  `PersonalWorkflowSelectionV1`，graph/owner/version 只由 Host trusted binding 提供。
- `capability_build/` 从常量升级为真实 durable-task specialization factory，定义 search/source-policy/
  isolated-build/package-store/activate/authorization typed Ports；成功后 `active=true` 为出厂行为。
- conditional registration：缺某组 optional Ports 只让对应 Profile unavailable；Core 与其他 ready
  Profile 仍注册。全 Ports 齐备时三个 official Profiles 默认可见，无 shadow/default-OFF。
- 测试：三 factory 经 public Runner 真启动；capability_build payload 固定 budget/admission；Personal
  safe-replay Tool 集；伪造 selection/generation/fingerprint 在 child precreate 前拒绝。

## A3 — Durable Tool authorization state machine [AC-5]

- 扩展 `tools/authorization.py` 为durable Host合同：`prepare(prepared_effect)` 可即时
  `ALLOW/DENY` 或返回frozen `REQUIRE_USER` request descriptor；`bind_decision()`与
  `bind_effect_handoff()`返回immutable receipt ref/hash或typed pending/permanent conflict。产品
  policy/grant store不进SDK，但generic decision/wait、binding blocker与effect handoff authority必须在SDK。
- `EffectExecutor` 在 effect intent 已验证、物理 handoff 前，用
  `decision_id=authorization:<effect_id>`、随机 nonce、expected version 原子创建 OPEN decision，
  将同一 Run 置 WAITING并返回 typed wait result，不把 require-user 转 rejected。
- SDK UoW public Protocol 补 `read/open/resolve_authorization_decision`、authorization blocker与双receipt
  effect transition；SQLite用现有decisions/run wait表和CAS。`RunClient.signal()`只接受decision id/
  nonce/version/decision；ALLOW先取得Host prepare receipt，再提交SDK decision但保持Run waiting，Host
  回传decision-bound receipt后才消费blocker。effect只有同时持有互相hash-bound的SDK handoff与Host
  consume receipts才可转HANDED_OFF并调用Tool。Host或SDK任一CAS失败可按相同receipt重试；永久冲突在
  handoff前confirmed-not-started、之后UNKNOWN。DENY/EXPIRE/CANCEL按handoff fence terminalize。
- restart 扫 open decisions 恢复等待；重复同 decision signal 幂等；错 Run/nonce/version、late/expired
  signal quarantine；未授权路径 physical Tool count 恒为 0。
- 测试：allow、deny、cancel、duplicate、wrong nonce/version、expired、crash before/after open、各binding/
  handoff receipt前后与restart；Host用内存fault-double模拟独立durable store。每例核对Run/effect/decision/
  blocker/receipt ledger；SDK handoff commit前物理调用恒0，commit后最多1。

## A4 — SDK-owned delivery pump [AC-5,8]

- `Runtime.start()` 顺序固定为 reconciliation→recover→resolved waits→startup delivery drain→启动
  wake-drain 与 delivery-pump；Runtime 是唯一 pump owner。生命周期是锁保护的
  `NEW→STARTING→READY→CLOSING→CLOSED` 或 `STARTING→FAILED→CLOSED`：并发 `start()` 共享同一 startup
  task并等待同一结果；READY 前 `RunClient.start/signal/cancel` 统一 fail-fast `runtime_not_ready`；
  `close()` 与 STARTING 竞争时先阻止 readiness 发布、等待/取消 startup并执行同一清理，不允许半 ready。
- `start()` 使用局部tasks并包围在失败清理中：当前`kernel.py:714-724`在reconciliation前即
  `_started=True`，正式实现若任一步抛错必须停止已建pump/task、释放lease并回到可安全close状态；
  不允许同一失败Runtime二次start。
- terminal/outbox commit 后 SDK internal wake event 唤醒 pump；空队列使用 bounded backoff；sink failure
  经 `DeliveryDispatcher` release/retry，不丢 idempotency key。
- `Runtime.close()` 先拒绝新 start/wake，做 bounded delivery drain，再 cancel pump，随后 close live Runs/
  heartbeat/release leases；重复 close 幂等。
- 测试：concurrent start、READY 前 client start/signal/cancel、start-vs-close、startup failure、terminal wake、
  启动 backlog、无在线消费者、sink fail→retry、重复 delivery、close race、crash after sink before settle、
  restart backfill；product-style sink 只需实现幂等 Port。

## A5 — ReAct hard policy 与 LLM payload matrix [AC-5]

- public `build_react_driver()` 必填 frozen `TerminationLimits`、`BudgetPolicy` 与
  `FrozenPriceEstimator|None`；不得隐式采用调用时产品可变配置。
- candidate 默认/产品目标值：turns `32`、Tool calls `64`、wall `900s`、cost
  `10_000_000 micros`、same Tool `3`、unknown cost refuse；start snapshot/checkpoint 持久化完整 policy
  fingerprint，recover 校验相同 fingerprint。
- 独立测试五个 hard stop；usage/cost missing 不是零；乱序 result 按 run+call correlation；duplicate/
  malformed/超长 args 和 ToolResult 在 handler/context/SQLite 前受界；拒不调用 Tool 的 controlled
  scenario 在 max-turn/termination gate 诚实失败或降级，不无限循环/伪造结果。
- public full-runtime matrix：no Tool、one Tool、multi-turn、multi-tool、attached child、cancel、unknown
  reconcile，全部使用 production Runtime/Driver/UoW，只有物理 Provider/Tool 为 double。

## A6 — Executable conformance protocol [AC-8]

- 新增 `testing/contracts.py` 的 frozen metadata/result/report 与 `ConformanceHost` Protocol；同步 Host
  factory，`open_suite(name)` 返回 fresh async context，suite 退出必须 aclose。
- 新增 `testing/suites/{provider,tool,runtime,workflow}.py`；CLI 与 pytest plugin 调同一 runner。
  protocol-major mismatch、missing capability、required skip/fail/error 一律非零。
- report 固定含 SDK/protocol/host/platform/Python/artifact SHA、逐 case status/duration/evidence；不得含
  secret、原始 provider body 或任意认证材料。
- suite 映射：Provider=physical request/error/usage/redaction；Tool=schema/five-state/reconcile；
  Runtime=Kernel/Driver/session persistence/HITL/delivery/budget/restart；Workflow=host-owned + 三 official
  Profile/ticket/reopen。
- wheel 不含 SDK tests/source 时，fake consumer host 的 CLI 和 pytest protocol 仍四套真执行且 exit 0。

## A7 — 0.1.1 reproducible candidate [AC-8]

- `pyproject.toml` 与单一 version module 改 `0.1.1`；API snapshot、migration/release notes、BUILD_INFO、
  SBOM、NOTICE、SHA256SUMS 由 clean commit 生成。
- 两次 `SOURCE_DATE_EPOCH=0 uv build` 解包后 canonical file list/hash 相同；wheel/sdist metadata、planned
  tag commit、BUILD_INFO commit一致。旧 v0.1.0 tag/asset/bytes不移动不覆盖。
- 同一个 candidate SHA 在 macOS ARM64、Windows x64、Linux ARM64 Python 3.11 跑 import side-effect、
  schema init/reopen、四 conformance suites；平台只下载该 bytes，不自行 rebuild。
- 三平台 runner 固定在 `.github/workflows/release-candidate-conformance.yml`（或已有等价CI）中，以
  candidate SHA和artifact digest为输入；本 slice 只静态验证 workflow 与输入合同，未获push/dispatch
  授权时远端run保持 PENDING。Windows x64/Linux ARM64与发布后macOS同bytes复验属于 `SDK-C7`
  required release gate，不能用本机模拟结果替代。许可证owner确认receipt与
  Apache-2.0/SBOM/NOTICE一起入candidate manifest。
- 只保存本地 candidate 与 receipts；创建远端 tag/Release 不在本 slice 授权范围。

## Slice A tests and machine gate

- 开工前初始化独立 `verification/<slice-a-run>/plan-test-run.json`，冻结 program oracle + Slice A 新增
  black-box public-consumer testcase；baseline 指向 `baseline.md`。
- 必跑 SDK full pytest、ruff/type checks（若项目配置）、public API snapshots、exact-wheel clean macOS
  Python 3.11 venv、deterministic build、远端 matrix workflow静态校验、secret scan、full-surface SDK entries。
- 独立 auditor 必须证明：无 deep import、无 product Harness import、无 expected-red/skip 冒充 PASS；
  `finalize` exit 0 才允许把 candidate SHA 交给 Slice B。
