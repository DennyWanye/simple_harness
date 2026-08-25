# Simple Harness SDK 提取与消费架构

> 最后校准：2026-08-25
> 代码基线：simple_harness `3c678e71` + 当前工作树；当前依赖固定 Harness 0.6.2 / Memory 0.5.2 exact wheels
> 状态：SDK v0.6.2 是 foreground text 的唯一生产执行 authority；Voice 关闭，Companion/background
> 使用独立 SDK client 入口。下文 v0.1.0-v0.1.3 release/切换叙述均为历史记录。

## 2026-08-25 SDK-first runtime capability catalog（候选已消费）

- Harness 0.6.2 提供产品中立的 typed runtime catalog、Run-local exposure、search/describe/activation receipt、
  dynamic ReAct projection 与 v6 durable catalog/recovery；future-consumer fixture 不 import Host。
- Host vendor exact wheel `ffb7c0619851f3c936fcc1d0cf527d07f49e87770291b85e57fe87032ac02c2e`
  （source `67f5769…`），Memory 0.5.2 wheel
  `deff2fa85a269a3978f2c6efcd99fda77abcb74444170361365fd00ec0164e9e`（source `46624b…`）。
  `pyproject.toml`、`uv.lock`、candidate/origin/hash 门保持 exact bytes。
- SDK 目录只拥有发现/可见性；Host 继续拥有权限、TaskGrant、workspace/origin、MCP lifecycle 与 physical
  handler。当前候选自动化、CAP-1～CAP-5、冷重启对、exact-wheel consumer 与 packaged macOS UI 均通过。
  最终 source `67f5769…` reproducible wheel 与完整真测 wheel 的运行时包逐文件相同；Host 重锁、重装后
  又完成无 `PYTHONPATH` 冷启动与可操作 UI 冒烟。候选仍未发布 tag/release：本次只授权代码提交到远程
  主分支，没有授权 SDK tag、release 上传和 download-back promotion；不得把本地 wheel 描述为 production release。

## 2026-08-23 Host S3 observability composition（已 revendor）

- Host 新增 `backend/observability/sdk.py`，在 Harness observability API 可用时默认组合一个共享
  `CompositeSink`：固定 256 条的 `RingBufferSink`、1 MiB × 3 文件的 `JsonlSink` 与只消费安全
  envelope 的 `LoggingSink`。同一 sink 注入 Memory `build_production` 和 Harness
  `ProductionRuntimeConfig`；Memory 同时消费 Host correlation policy。
- 真实 `_execute_sdk_run` ingress 以 SDK physical Run、Session 和 request authority 生成不可读 correlation，
  Memory 子操作继承同 Session trace 并生成 child operation。correlation 不进入 principal/owner/session
  判定，也不替换授权参数；跨 Session 会重新根化。
- Host 只在用户 log 目录写显式且有界的 `sdk-observability-events.jsonl{,.1,.2}`、
  `sdk-observability-ring.json` 与 `sdk-observability-snapshot.json`。snapshot source 故障、路径不安全、
  序列化或 SDK 缺失均降级为稳定状态，不阻断业务。
- Rust diagnostic bundle 对 SDK observability 使用 exact filename allowlist、1 MiB 单文件与
  2.5 MiB 合计上限；拒绝 symlink/non-regular/oversize/canary 文件。ambient backend logs、crash reports、
  metrics、DB、outbox 和 product content 不进入 bundle。
- Host 已固定并校验 Harness 0.4.0 / Memory 0.5.0 wheels；新 observability API 默认启用。
  Harness wheel SHA `aaf8d79a…58ecb6`，Memory wheel SHA `c274fa6b…51ea46`。
- 验证：sibling-source Host observability `5 passed`；exact-wheel backend focused `212 passed`；
  Rust diagnostics `7 passed`，Rust lib 全套 `83 passed`；目标 Rust 文件 rustfmt check 通过。canary 覆盖
  Memory 正文、API key、Authorization/header、token、exception、外部 correlation、ring/JSONL/snapshot/bundle，
  并覆盖 SDK/snapshot/export/bundle missing/degraded。

## 2026-08-22 官方 Agent Memory 产品验收

- exact Harness `fbb156f` / wheel `cf629cee…` 与 Memory `3d4247b` / wheel `bfcd2506…` 已由
  simple_harness source backend 从 site-packages 消费并通过 installed-origin/hash 门。
- root 与 continuation 的 immutable source ref、自动 recall/frozen stage、terminal-only committed-turn outbox、
  trusted four-part identity、Facts list/forget UI 与 product projection 已成为生产链路。
- macOS Computer Use 在真实 DeepSeek 下完成 7 个 required UI 类别；record transient 在成功回复后、Memory
  未落库时退出，下一次无故障启动由 durable outbox 收敛并可跨 Session 召回。
- AIPhone、K6/AgentOS、NovelTagSystem 仍未修改或验证；只能声明接口就绪，不能声明产品接入。

## 2026-08-21 exact candidate 与消费者准入

simple_harness vendor 的 Harness wheel SHA-256 为 `e1f7d4b10f6d02c071b8fabfddeaf52b48f60431cba0fefca1aa349c7be3d233`
（source `869c76f2050b5f492b4edee68f4ce2400030b832`，CI run `32446683554`，artifact
`9434287332`）；Memory wheel SHA-256 为
`6f0682fdcd958a666e52a294ba5c6e4e721bed53f1669f1f7af63cd33027f014`（source
`87820fe2c4cdde21c3a9356ca461b93fe00aadcb`）。`sdk_candidate.py` 对 vendored bytes、安装版本和
direct-url origin fail closed；Runtime 使用 SDK `build_production_runtime`，conversation Memory 与
context staging 默认 ON。

Provider Context 由 consumer-prepared projection-v2 stage 唯一承载；Memory recall 的 query/result
lineage、structured current message、attachment blocks、catalog/budget/tool authority 均进入同一私有
snapshot，Memory 内容按 USER/untrusted 投影。Harness execution outbox 与 simple_harness `state.db` product
outbox 按 provenance 分治，不允许同一消息双写。开发 reset 只用于 schema 变化后清空测试三库。

自动化门禁：D1 `58 passed`、D2/exact `17 passed`、Rust diagnostics `4 passed`、D3 前端
`615 passed`、typecheck/build PASS。D-ALL 暴露既有 baseline inventory 未登记失败与既有 ESLint
171 项债务；本切片聚焦门禁无新增红。D-UI 需要 windows-mcp 真桌面点击，当前工具面不可用，状态为
BLOCKED 而非脚本 PASS。

## 2026-08-21 消费者 Context cutover 校准

> 本节记录切换前缺口；上述 exact-candidate consumer-prepared 实现已关闭 Context/Memory 主缺口。

SDK Runtime 已是 simple_harness 唯一生产执行 ingress，但“进入 SDK”不等于完整产品 Context 已完成
cutover。普通文字入口当前绕过保留的 `ProductTurnPreparer` / `ProductTurnPreparationService` 和
`ProductContextAdapter`，由 `backend/main.py::_assemble_sdk_messages` 临时构造有限消息；已构造的
`TurnInput`（含 attachment blocks、memory policy、workspace/provider/catalog basis）没有被后续执行
消费。下文将 `ProductTurnPreparer` 画在生产链路中的图属于目标架构或 cutover 前基线，不能作为
2026-08-21 当前事实。

SDK execution DB 已持久化真实 Provider invocation usage，但产品 SessionDB Context usage、Inspector
和 BillingLedger 尚未消费该 authority。产品侧还把 `session_generation` 固定为 `1`，并给
context-dependent tool handler 提供静态 execution identity；这些必须在消费者 Context 单一事实源
切换中一并消除，而不是在 UI 再做一套估算。

## 0. 当前实施状态（2026-08-20 校正）

**2026-08-17/19 之后的当前事实**（本节下方 2026-08-16 原始记录保留为历史）：

- **Ingress cutover 已执行**：`main.py` 中 `_sdk_ingress` 是所有产品入口（text/voice/background）的唯一活跃
  ingress，执行链为 `_execute_sdk_run()`（`main.py:9228`）→ `SdkRuntimeIngress.start()` →
  `DeliveryDispatcher`。旧 `_harness_venue` 构建代码、`_build_product_harness_stack()`、
  `_activate_product_harness()` 与 11 个未使用 harness 模块已删除（2026-08-17 清理，pytest
  81 failed / 6466 passed，基线 79 failed / 6636 passed，减少项为已删 harness 单测）。
- **保留的 harness 文件**：23 个（非 `__init__.py` 口径：contracts/ports/projector/context/profiles/skill_scope/kernel + 13 个引擎
  模块 + adapters/venues.py + product_turn_open.py + drivers/react_boundary.py），原因是
  companion/run_adapter.py → venues.py → kernel.py 依赖链与 6 个产品文件的契约类型引用。
- **公开工作叙述与工具投影**：Provider adapter 只捕获 normalized assistant `content`，显式忽略
  `reasoning_content`；真实 SDK 的 Provider request id 为 `provider-turn:N`，不含 Run identity，单一
  活跃 Run 时可保留模型公开 content，并发歧义时 fail closed。Delivery adapter 在每个准确 Run 的
  tool call 前保证投影一次叙述：模型公开 content 为空时只按公开 tool name 生成有界 fallback；同一
  Provider turn 多 call 共享 iteration 并按 call_id 对账结果。叙述持久化为
  `workflow_progress/context_visibility=exclude`，`_assemble_sdk_messages` 只允许普通 conversation
  投影，避免 UI 摘要回灌下一轮。delivery 在 ingress.start 前预注册，消除首个 Provider/tool turn
  丢投影竞态。
- **已知残留（不阻塞主链路）**：companion/run_adapter.py 期望 `KernelRunClient` 但 main.py 传入 SDK
  `RunClient`（接口不兼容，companion 后台功能当前不可用，修复需接口适配层）；curation 未接新
  memory SDK。产品 SDK 的 `memory_recall`/`memory_search` 已于 2026-08-20 接入真实只读查询。
- **SDK 仓库已发布 v0.1.2**（release commit `91df02d`，dist/ 含 wheel + tar.gz）：新增消费者友好层
  `build_consumer_runtime` / `ConsumerRuntimePorts`（3 个 Protocol + database_path）、MemoryQueryPort /
  MemoryWritePort、quickstart/integration-guide/api 文档与 examples/minimal-consumer。宿主仍 vendor
  v0.1.1，切换工作见 `plans/2026-08-19-sdk-usability-optimization/`。
- **B3 状态维持**：0.1.1/0.1.2 均尚无 public Tool inventory sidecar 合同，B3 不得标 PASS。
- 下方"### 历史记录：2026-08-16 原始状态"至"## 3."中引用的 `deskpet.harness` 生产链路描述为
  **cutover 前历史基线**（product_composition/bootstrap/subagent_registry 等已删除），仅作设计上下文保留。

### 历史记录：2026-08-16 原始状态

- SDK Slice A 后续发现 PPT durable interrupt 缺少 public atomic resolve/resume seam；本地 hotfix
  提交 `f13a30a` 现生成 active `0.1.1` wheel
  `simple_harness_sdk-0.1.1-py3-none-any.whl`，SHA-256 为
  `48048ffbb827df15ae27efad67fa78d31302c9869381cb175d0d908c5f204e2f`。该 exact bytes 已通过
  targeted 21 tests、SDK 全量 `1181 passed, 2 expected skips`、artifact/exact-wheel/build 10 tests
  与双构建 byte-identical；candidate manifest SHA-256 为
  `aa960d8219f941a8bc56cf75748bf7b105ed264c7248f2117d02c1dd0e38981b`。旧 wheel
  `371ceb98...10d5` 的机器 receipt 已被此 hotfix 取代，active bytes 仍须补发新机器 receipt。
  未创建 tag、push、dispatch 或 Release。
- 独立仓库 `/Users/denny/projects/simple-harness-sdk` 的远端仍为
  `0e38532`（本地现已前进到未推送的 `f13a30a`）。旧 v0.1.0 release identity 仍漂移：本地 `v0.1.0` tag 指向 `88e19eb`，远端
  同名 tag 指向 `54b62f6`，GitHub Release 的 `BUILD_INFO.txt` 又声明 build commit 为
  `88e19eb`。产品 vendored wheel 为
  `backend/vendor/simple_harness_sdk-0.1.0-py3-none-any.whl`，SHA-256 是
  `d9a1d4f94f826cdf97fb1c23085c85e727400a92f725c7022b0ebf63a18f4d91`，并由
  `backend/pyproject.toml:161-173` 与 `backend/uv.lock:7797-7804` 固定。该 bytes 可审计，
  但 tag/commit/manifest 不一致使它不能关闭 SDK-AC-8。
- 安装依赖不等于生产切换。当前真实启动链仍是
  `backend/main.py:7878-8107 -> deskpet.harness.adapters.product_composition`
  `:28-205 -> deskpet.harness.bootstrap:58-214`，由产品源码树中的
  `RunKernel/ReActDriver/WorkflowDriver/SqliteExecutionUnitOfWork` 持有执行 authority。
- Product Slice B 的 B1/B2 已形成 closed-ingress 基线：`runtime_paths.py` 只解析
  `<user-data>/data/simple-harness-sdk/execution-v1.sqlite3`，校验 exact `0.1.1` wheel 的
  version/SHA/direct-url origin，并拒绝 home/repo/evidence/symlink escape；`composition.py`
  直接使用 SDK public Runtime 与真实 SQLite Database/UoW，提供串行 start/reconcile/recover/query/close、
  failure cleanup、dependency re-read retry 和 close-during-start 屏障。dependency loader 创建的 product
  resources 通过 typed closer bundle 转交 stack ownership，正常关闭和 partial failure 都按
  Runtime → resources逆序 → SDK DB 清理且每项只关闭一次。完整成功后只发布 immutable
  `ServiceContext.sdk_runtime_ready`；`main.py` 当前仅注册空 slot，text/voice/background ingress 仍保持旧链路，
  因而这不是生产 cutover。workflow runner/registrations/closers 现只能由
  `workflow_factory(database, uow)` 在 SDK Database/UoW 创建后构建，并显式核对同一 transaction owner；
  `WorkflowFactoryResourceScope` 在 factory 返回前持有 partial resources，异常时逆序清理，成功
  transfer 后再由 stack 接管；foreign owner、runtime start partial failure 与正常 close 均 fail closed/
  逆序清理。B2/B3 聚焦测试当前为 `27 passed`。
- Product Slice B B3 的产品侧已把 pre-cutover 79 个 reachable identity 显式收敛为 77 个 SDK Tool 与
  `workflow.deep_research`/`workflow.presentation` 两个 Workflow profile。catalog 使用 checked-in
  real metadata manifest（语义 SHA-256 `891ae13615229ee98715f8b18f39a5a045c1f995a29e984a4b86c4eaa2f310bf`），
  全部普通 object schema 均递归 closed；实际 70 个变更 Tool 的 old/new hash 已逐项固定，包含 14 项
  specialized migration。其中 `app_launch`/`process_start.environment` 采用 SDK
  0.1.1 可表达的 bounded key/value array，并在真实 handler adapter 处拒绝重复/越界后还原 map。
  `deskpet.tools` import 不再 `pkgutil` 扫描；21 个 legacy static provider 模块均改成 import-pure
  `register_static_tools(registry)`，新 SDK catalog 直接绑定真实 product handlers/factories。65 个 static
  descriptor 已逐项在 fresh process 证明 resolve 不加载 config、legacy registry、compat sink、旧 harness/
  workflows；即使新 catalog 先 resolve 全部 65 项，旧 closed-ingress main 仍显式重放 44 个 base
  registrations 并恢复 79 reachable（含 `generate_image`/`ppt_create`/`ppt_pro`）。SDK ToolCall 的真实
  `call_id` 进入旧 handler context/effect identity，写路径
  resource fence 与相同 request 下不同 call 的 idempotency identity 已覆盖；新 stack 的
  `await_subagents` 只调用 typed Host join port，不再导入旧 harness。六类代表
  （sync/async/context/staged/control/provider）已真实调用；14 个 specialized schema migration 也已逐项
  对照真实 handler 的 legacy shape 与 SDK migrated shape，environment 由 typed process double 实际观察
  还原 map。B3/B2 聚焦为 `27 passed`，相邻 Tool/manifest 回归为 `47 passed`。全
  `tests/sdk_adapters` 当前为 `90 passed, 1 failed`；唯一失败是并行 Workflow slice 的 fresh-process
  import purity（`deskpet.workflows.__init__` 仍加载旧 contracts/definition），不属于 B2/B3 所有权。
  **B3 仍为 PENDING**：当前 exact 0.1.1 public SDK 没有可承载/执行完整 effect/resource/parser/outcome/
  control/lifecycle metadata 的 Tool inventory sidecar 合同，产品 manifest metadata 虽已保留但尚不能进入
  SDK 执行决策；须等待新 wheel 提供 public sidecar/五态合同后接线与重验，不能把当前注册成功写成 B3 PASS。
- exact 0.1.0 wheel 的 `simple_harness.testing.run_conformance_suite` 与 CLI 仍是占位：
  `src/simple_harness/testing/__init__.py:24-39` 抛 `NotImplementedError`，
  `testing/cli.py:154-171` 返回 `status=not_implemented`。因此当前不能把“1122 tests
  passing”解释为消费者 conformance 或桌面 SDK 自用验收完成。
- SDK 0.1.0 也尚不能驱动三个官方 Workflow 的真实消费者接入：sealed
  `WorkflowRuntimeDriver` 未注入 durable-task/personal/capability Host ports，
  `WorkflowContext` 会拒绝节点实际读取的 port 名；`personal_v1` 缺少 public definition/profile
  factory，`capability_build` 只有 profile 常量。现有 immutable 0.1.0 不允许覆写，因此先形成
  **v0.1.1 candidate**，在 tag/commit/BUILD_INFO/SHA256SUMS/wheel bytes 全一致且通过门禁后再发布。
- 生产 composition 仍构造 `ModelPersonalWorkflowMatcher`（`backend/main.py:7881-7885`,
  `:7968-7974`），与 SDK-AC-6 禁止 Personal 前置 matcher 的要求冲突；T6 cutover 必须删除该
  production wiring，由同一个 `agent.general` 通过 frozen descriptor/catalog 选择。
- 当前验收事实源仍是
  `plans/2026-08-13-simple-harness-sdk/acceptance.md`。按其中 SDK-AC-6..8，下一
  release slice 必须先闭合 Product Adapter、SDK Runtime ingress、schema v1/reset、旧
  authority 退休与 conformance，再执行 `testcase/2026-08-13-simple-harness-sdk/manual-test.md`
  的 SDK-S1..S5 桌面 E2E 与 SDK-S6..S7 自动化 fault/reopen/reconcile。直接在现状跑桌面
  S1..S5 只会验证旧 Harness，不能关闭 SDK 门。
- 下一安全顺序固定为：将上述 exact v0.1.1 wheel 安装进当前 Simple Harness App → 完成
  Product Adapter/production composition 与 schema reset/reopen → 只在该 App 做 SDK-S1..S7
  桌面/故障验收。Windows x64、Linux ARM64 同 bytes 远端复验以及发布仍须用户单独批准，
  当前均为 `PENDING_OUT_OF_SLICE`。

## 1. 主要矛盾

本次提取成败不取决于能否把 Python 文件放进另一个仓库，而取决于能否反转当前依赖方向：

```text
当前：Harness / Workflow -> simple_harness permissions / tools / companion / product stores
目标：SDK contracts + ports <- Simple Harness / AIPhone adapters
```

SDK v0.1 必须保留完整 durable `RunKernel`、原生 Workflow Engine、root/child Run、
continuation/signal/cancel、Effect/UoW/fence、checkpoint、HITL、crash recovery 和
reconciliation；同时不能要求消费者重新实现 `workflow.durable_task`、
`workflow.personal_v1`、`workflow.capability_build`。验收事实源见
[`../plans/2026-08-13-simple-harness-sdk/acceptance.md`](../plans/2026-08-13-simple-harness-sdk/acceptance.md)。

## 2. 当前生产链路

```text
ProductVenueRunAdapter
  -> ProductTurnPreparer
     -> full Context + PreparedToolSet + Profile Catalog
  -> RunKernel(root_profile_key="agent.general")
     -> ReActDriver / AgentLoop
        -> direct answer or ordinary Tool
        -> workflow_spawn / capability_build
           -> ProfileLaunchTicket
           -> attached child Run
           -> WorkflowDriver
           -> WorkflowService / NativeWorkflowRunner
           -> checkpoint + effect ledger + delivery
     -> ChildRunCoordinator / HarnessReconciler
  -> RunPresenter / SessionDB / WebSocket / UI
```

代码证据：

- 产品组合固定 `root_profile_key="agent.general"`，并装配唯一 ReAct/Workflow Driver、
  `ChildRunCoordinator` 与 `EffectBatchExecutor`：
  `backend/deskpet/harness/adapters/product_composition.py:123-175`。
- Kernel 在固定 root Profile 存在时不调用 legacy router：
  `backend/deskpet/harness/kernel.py:387-405`。
- 生产入口调用 `prepare_direct_run()`，不调用旧 `route_intent()` 或 `plan_decision()`：
  `backend/deskpet/harness/adapters/product_turn_open.py:243-255`。
- Preparer 把 `model_spawnable` Profile 的自然语言描述、key 和 generation 注入同一个
  主 Agent，并明确禁止按关键词推断 Driver：
  `backend/deskpet/agent/turn_preparer.py:278-313`。
- `workflow_spawn` schema 的 key 来自当前 `ProfileRegistry.model_spawnable`，要求按描述
  而不是关键词选择：`backend/deskpet/tools/orchestration_controls.py:88-172`。
- Host 将模型调用解析为受 catalog generation、launch policy、parent/root/Attempt 和
  trusted snapshot 约束的 launch ticket；它验证选择，不做第二次语义路由：
  `backend/deskpet/harness/adapters/subagent_registry.py:255-340`。
- `WorkflowDriver` 只按冻结 Profile 绑定 workflow name/version，启动、signal、cancel 和
  recover 都经 durable dispatch/fence：`backend/deskpet/harness/drivers/workflow.py:71-269`。
- 完整 Kernel 闭包不只包含 `RunKernel` 类，还包含 `AdmissionLauncher`、
  `HostContextFactory`、`ChildSignalRuntime`、`BoundedLiveIndex`、`DriverRuntime`、start
  snapshot/activation 和 terminal lifecycle；它们共同拥有 atomic start、single owner、
  recovery lease 与 terminal commit：`backend/deskpet/harness/kernel.py:52-89`、`:171-215`、
  `backend/deskpet/harness/kernel_terminal.py:90-184`。
- Provider 调用不是普通网络 helper。`ProviderInvocationCoordinator` 持久化 claim/outcome/
  `unknown`，并通过 dispatch handoff/fence 区分 not-started、started、start-unknown：
  `backend/deskpet/execution/provider_invocations.py:37-68`、`:331-461`，
  `backend/deskpet/execution/dispatch.py:66-98`。
- 根 Run terminal 与 generic delivery 在同一个 UoW commit 中受 fence 保护，child terminal
  与 parent signal 同样原子提交；`RunPresenter` 可以留在产品层，但 `DeliverySpec`、outbox、
  dispatcher 与 startup reconciliation 是 durable Kernel 闭包：
  `backend/deskpet/harness/kernel_terminal.py:99-178`、
  `backend/deskpet/harness/bootstrap.py:136-206`。
- 当前执行数据库由 `workflow.db` schema 29 承载；schema 同时包含 workflow checkpoint/
  effect/trace 与 execution run/child/continuation/ticket/provider/fence 数据：
  `backend/deskpet/workflows/store/schema.py:12-220`、`:695-1780`、`:2046-2150`、
  `:2259-2521`、`:3371-3785`。
- 最新增量 `4b803e33` 把 durable Tool catalog snapshot 缺失收敛为 typed
  `tool_catalog_stale`，并让 precreated child 在进入 Driver/Provider/Tool 前通过统一 permanent
  recovery failure 通道只提交一次 failed terminal；这是 SDK catalog generation/fingerprint 与
  recovery conformance 必须保留的新基线：`backend/deskpet/tools/registry.py:474-483`、
  `:1644-1658`，`backend/deskpet/harness/kernel.py:1202-1225`。

## 3. Workflow 选择 authority

### 3.1 已符合目标的部分

所有普通顶层任务固定进入 `agent.general`。主 Agent 可以直接回答、调用普通 Tool，或发出
显式控制调用。Kernel、venue、mode、用户 payload、正则和旧 Code persona 都不能覆盖 root
Driver。`backend/tests/harness_simplification/test_general_agent_root.py:55-102` 对不同文本和
venue 锁定了该行为。

`workflow.durable_task` 由同一个主 Agent 从 Profile Catalog 选择。Host 只校验 Profile、
catalog、workspace、输出合同、Provider snapshot、授权和一次性 ticket。

`workflow.capability_build` 当前通过专用 `capability_build` 控制 Tool 启动，而不是普通
`workflow_spawn` enum。它仍由主 Agent 发起，但 Host 要求能力搜索 receipt 与当前 catalog
stamp 证明没有可复用能力；这是准入校验，不是语义路由。

### 3.2 必须在 SDK 提取时修正的部分

`workflow.personal_v1` 当前先由独立 `ModelPersonalWorkflowMatcher` 对用户原文和候选做一次
模型预选，再把唯一 frozen candidate 提示给主 Agent：
`backend/deskpet/companion/turn_authority.py:279-345`、`:790-823`。这不是正则，但它形成第二个
语义 authority，且不符合“同一个主 Agent 基于完整 Context 统一选择”的已确认目标。

SDK 目标是 Host 只冻结安全的 Personal Workflow descriptor catalog；同一个
`agent.general` 选择稳定 candidate id。Host 随后绑定可信 graph/owner/version/权限，模型不
能提交或改写这些 authority 字段。现有 `subagent_registry` 对 personal authority 字段的
拒绝和 frozen selection 校验应保留：
`backend/deskpet/harness/adapters/subagent_registry.py:284-325`。

仓库仍保留 `backend/deskpet/workflows/routing.py:1-109` 的正则 router，但生产 ingress 不再
调用它；当前生产引用只在 `code_nodes` 内辅助判断 tool-free completion：
`backend/deskpet/workflows/definitions/code_nodes.py:825-852`。SDK 不应将它作为公开或生产可达
的 Workflow selector；内部完成条件应逐步改用冻结计划、effect/receipt 与明确声明。

“固定 root + ticket”目前只是产品装配保证，不是待提取 Kernel 类型本身的强制不变量：
`RunKernel` 仍接受 legacy router，bootstrap 在没有固定 root Profile 时仍能注入 classifier，
ticketless child 路径仍可信任 `route_hint/driver_kind`：
`backend/deskpet/harness/kernel.py:126-155`、`:387-405`，
`backend/deskpet/harness/bootstrap.py:103-105`，`backend/deskpet/harness/child_runs.py:170-176`。
SDK public API 必须删除或私有化这些旁路；顶层只能固定 `agent.general`，child 只能消费一次性
launch ticket。

当前 system instruction 还规定多文件软件/项目任务在首次副作用前 `MUST use
workflow.durable_task`：`backend/deskpet/agent/turn_preparer.py:291-298`。目标 SDK 不把这条
写成确定性 Host router；它改为 catalog descriptor 中的强建议和风险说明，最终仍由同一个主
Agent 发出控制调用。无论它选择何种 Workflow，effect、授权、预算和 output-contract gate 都是
不可绕过的确定性约束。

## 4. 当前规模与耦合事实

基于当前 Python 源码 AST/行数盘点：

| 区域 | Python 文件 | 约 LOC | 主要内部依赖 |
|---|---:|---:|---|
| `backend/deskpet/harness` | 37 | 18,338 | execution、tools、capabilities、workflows、permissions、companion |
| `backend/deskpet/execution` | 17 | 10,259 | security、types、harness、provider/agent |
| `backend/deskpet/workflows` | 124 | 92,682 | execution、types、security、tools、capabilities、companion、permissions |
| `backend/agent` | 32 | 12,368 | simple_harness agent/memory/execution/workflow 与 legacy `llm` |
| `backend/deskpet/capabilities` | 31 | 28,820 | tools、companion、types、execution、permissions、workflow |

三个特别大的聚合点是：

- `execution/contracts.py` 约 2,133 行，混合通用 Run/Effect/Attempt/Ticket 合同和
  simple_harness task/grant/projection 字段；
- `execution/uow_ports.py` 约 862 行，14 组 Protocol 既含 Kernel/Driver/child/recovery，
  也含 product projection/team/admission；
- `workflows/store/execution_uow.py` 约 14,845 行，是当前唯一 SQLite 写 authority，但同时
  承担 Workflow、Harness、权限、Provider、Capability、产品投影等事务。

因此不可把现有 `deskpet` 目录整体复制或仅改 import 前缀。SDK schema v1 必须从当前事务
不变量重新切出通用表/Port；已确认不迁移当前开发期历史 Run，所以不携带 schema 1-29 的兼容
迁移器，只为 SDK 自身未来升级建立版本化迁移。

## 5. 提取分类

### 5.1 可作为 SDK 内核来源，但仍需 namespace/API 清理

| 当前来源 | SDK 归属 | 保留语义 |
|---|---|---|
| `workflows/contracts.py`, `errors.py`, `control.py`, `definition.py` | `simple_harness.workflow` | JSON-safe state、编译校验、edge/reducer、interrupt、retry/loop budget |
| `workflows/native.py`, checkpoint/lease/recovery primitives | `simple_harness.workflow.runtime` | 原生调度、checkpoint、lease、recover、cancel、HITL |
| `harness/profiles.py`, generic portions of `harness/contracts.py/ports.py` | `simple_harness.runtime` | Profile/Driver catalog、Driver event/signal、join policy |
| Kernel lifecycle、child coordinator、continuation、runtime、reconciler | `simple_harness.runtime` | root/child identity、单 owner、signal/cancel/recover、late result reconciliation |
| admission/start snapshot/activation、live index、terminal lifecycle | `simple_harness.runtime` | atomic start、single owner/recovery lease、terminal commit |
| generic execution identity/event/effect/fence/ticket contracts | `simple_harness.execution` | durable state machine、idempotency、CAS/fence、launch ticket |
| Provider invocation coordinator + dispatch ledger | `simple_harness.execution` | claim/handoff/outcome/unknown 与恢复后防重复 dispatch |
| generic `DeliverySpec`、terminal delivery outbox/dispatcher/reconciler | `simple_harness.execution.delivery` | root terminal + delivery 原子性；产品 Presenter 只消费 delivery |
| Provider/Tool public contracts and conformance fixtures | `simple_harness.providers/tools/testing` | 单次 Provider call、typed Tool、稳定错误与 redaction |

“可作为来源”不等于逐文件无修改搬运。所有公开类型必须先从 simple_harness 名称、产品路径、UI 投影和
隐含环境读取中解耦，并建立显式 `__all__`/public namespace。

### 5.2 必须先做依赖倒置

| 当前耦合 | 证据 | SDK Port / 拆分方向 |
|---|---|---|
| `RunKernel` 直接依赖 simple_harness admission/TaskGrant | `harness/kernel.py:49-50` | `AdmissionPort`、generic grant/decision contracts；产品策略由 Adapter 实现 |
| `ReActDriver` 直接依赖 capabilities、companion skill、permissions、task work context | `harness/drivers/react.py:15-76` | 拆为通用 Driver state machine + capability/authorization/workspace ports |
| `EffectBatchExecutor` 直接依赖 `ToolRegistry` 与 simple_harness Tool context | `harness/tool_executor.py:24-39` | `ToolExecutorPort`、`ToolReconciliationPort`、prepared-call/outcome contracts、host authorization hook；重启后由 Adapter 返回 `confirmed_not_started/completed/still_unknown` |
| `AgentLoop` 直接依赖 simple_harness provider invocation/trace 与 legacy context/memory | `agent/agent_loop.py:36-70` | SDK Context/Provider/Trace ports；Simple Harness Context OS 作为 Adapter |
| `SqliteExecutionUnitOfWork` 直接导入 simple_harness task context、permissions/grants | `workflows/store/execution_uow.py:20-139` | 通用 execution repository + 可选 extension transaction participants |
| `durable_task` 复用 `code_task/code_nodes`，runtime 直接调用 simple_harness provider/capability | `workflows/definitions/v1/durable_task.py:8-11`; `workflows/adapters/code_runtime.py:20-28` | 官方 durable-task graph + `ProposalPort`、`CapabilityCatalogPort`、`ToolExecutionPort`、`OutputContractPort` |
| `personal_v1` graph 直接导入 Companion personal workflow types | `workflows/definitions/personal_workflow.py:12-17` | SDK-owned manifest/descriptor/selection contracts + `PersonalWorkflowCatalogPort` |
| Capability Builder 直接导入 Companion build admission | `capabilities/builder.py:29-48` | SDK-owned optional builder workflow + source/build/test/install/activate/admission ports |
| `workflow_spawn` 既是 SDK control 又被注册成 simple_harness `OPAQUE_MANUAL/shell` Tool | `tools/orchestration_controls.py:323-349` | SDK-owned orchestration control contract + Host `AuthorizationPort`；SDK 不继承产品 policy id/category |
| OpenAI Adapter 直接依赖 simple_harness dispatch/context/report/sanitizer/metrics/E2E hook | `backend/providers/openai_compatible.py:1-29`, `:268-334` | 基于 SDK Provider contract 重写薄 HTTP Adapter；dispatch ledger 留在 execution，产品 metrics/hook 留在 Adapter 外 |

`WorkflowRunner` 的提取闭包还包含 immutable registry、lease、recovery/quarantine、replay、
checkpoint store、trace 和 execution-checkpoint adapter；当前 runner 甚至会自行构造具体
`SqliteExecutionUnitOfWork`：`backend/deskpet/workflows/runner.py:22-40`、`:209-258`，
`backend/deskpet/workflows/execution_ports.py:17-94`。目标是由 composition root 注入这些 Port，
而不是把现有 concrete store 藏进 SDK global。

### 5.3 留在 Simple Harness 产品层

- `backend/main.py`、Tauri/React、FastAPI/WebSocket、Voice 和窗口/托盘生命周期；
- `ProductTurnPreparer` 的 Persona、Memory、Skill、Attachment 与 simple_harness Context OS 装配；
- Simple Harness 的 Tool handler、桌面/文件/Office/浏览器实现；
- 产品权限 UI、TaskGrant 策略和本机确认体验；
- RunPresenter、SessionDB、ArtifactCard、TTS 与产品消息投影；
- 产品级 Session/Message/history 及 UI projection；SDK 只拥有 execution session identity、
  Run/event/effect/provider/delivery ledger，不能把二者都叫成同一个 Session store；
- Companion growth/reflection/notification/owner inbox。只有 Personal Workflow 的通用
  descriptor/selection/execution contract 进入 SDK，Companion 的保存和管理通过 Adapter 接入；
- 产品专属 Workflow、UI card 和交付格式。

### 5.4 v0.1 不提取

DeepResearch、PPT、语音、本地模型、浏览器/桌面自动化、完整长期 Memory、MCP marketplace、
simple_harness team 实现和默认 shell Tool。历史 DeepResearch v1-v6、旧 LangGraph checkpoint 和当前
开发数据库 schema 兼容也不进入 SDK。

## 6. 三个官方 Workflow 的归属

### `workflow.durable_task`

状态机、节点编排、HITL、repair/test/audit、输出合同和恢复由 SDK 官方模块维护。消费者提供
Provider、Tool、Workspace、Artifact 和 Authorization ports。

### `workflow.personal_v1`

manifest/graph schema、compiler/interpreter、候选 descriptor、可信 selection binding 和当前
恢复语义由 SDK 维护。当前事实是 Native graph 只有一个 `execute` 节点，Personal graph 在该节点
内用进程内拓扑循环解释；只有 Tool EffectJournal 逐 Effect durable，Personal node cursor 和中间
outputs 不逐节点 checkpoint：`workflows/definitions/personal_workflow.py:388-409`、
`workflows/adapters/personal_runtime.py:475-549`。v0.1 明确保留该边界，并继续只允许
`idempotent_read`/`deterministic_reusable` Tool（`:238-257`）；不能宣称支持任意副作用 Personal
node。消费者只负责候选保存/编辑/列举和产品 UI，主 Agent 是唯一语义选择者。

为移除独立 matcher，SDK 新增 bounded `PersonalWorkflowDescriptor` catalog（稳定 candidate id、
自然语言职责、公开输入合同、隐私字段白名单、generation/fingerprint）。
`workflow_spawn` 控制调用新增 `candidate_id`，Host 在签发 ticket 时绑定 frozen
graph/owner/version；模型不能提交这些 authority 字段。当前 schema 不含 `candidate_id` 且仅接受
唯一预选 candidate：`tools/orchestration_controls.py:88-170`、
`companion/turn_authority.py:777-813`、`harness/adapters/subagent_registry.py:289-319`。

### `workflow.capability_build`

当前 `workflow.capability_build` 不是独立 Workflow graph：产品把该 Profile 绑定为
`workflow_name="durable_task", version="v1"`，再由专用 control 注入 Builder payload：
`harness/adapters/product_profiles.py:152-157`、`:242-245`，
`harness/adapters/subagent_registry.py:637-654`。v0.1 延续并正式定义它为 SDK 官方、受治理的
`durable_task` 特化，而不是伪称第四套独立引擎。能力缺口证明、构建计划、生成/测试/修复、候选
发布、激活/回滚协议由可选模块维护；消费者提供 source policy、隔离 build executor、package
store、catalog activation 和 authorization ports。

当前产品 registry 无条件要求 Personal、Durable、Capability、DeepResearch、Presentation 五组
Adapter：`harness/adapters/product_profiles.py:114-197`、`:226-249`。SDK 必须改为条件注册：某
官方模块已安装且 required Ports 完整时才进入 catalog；排除 DeepResearch/PPT 不得导致启动失败。
测试阶段一旦 capability-build 模块安装且 Ports 齐全就默认可用，不用另一个默认 OFF 灰度 flag。

## 7. 数据与迁移边界

- 当前项目没有真实用户或必须续跑的生产 Run；SDK 不读取或迁移现有开发 `workflow.db/state.db`。
- 切换前将精确解析开发数据目录，保留需要的原始证据索引/hash，然后执行一次经用户确认的
  数据重置；不得以宽路径或未解析变量删除。
- SDK 从干净 schema v1 建立 execution/workflow/capability 核心表；产品 Session/Message/UI
  projection 数据仍由 Simple Harness 自己拥有。
- schema v1 必须用接口与 fault-injection tests 锁定以下跨表原子不变量：

| 原子边界 | 同一事务内必须同时成立 |
|---|---|
| root start | Run + immutable start snapshot + initial event/activation state |
| child launch | launch ticket claim + child command/link + child precreate/start snapshot |
| workflow progress | checkpoint + decision/effect link + lease/fence epoch |
| child finish | child terminal event + parent signal enqueue |
| root finish | root terminal event + generic delivery outbox + terminal fence release receipt |
| Provider dispatch | invocation claim + physical handoff state；outcome/unknown 只按同一 invocation CAS settle |
| continuation | FIFO enqueue/claim/ack + continuation version |
| Tool effect | prepare/handoff/outcome/unknown + stable `call_id/effect_id` |

当前 UoW 已把这些边界暴露为跨表命令：`execution/uow_ports.py:77-211`、`:627-774`，
`workflows/execution_ports.py:21-83`。拆 store 时不得把一次命令降级为多个独立 repository commit。
- SDK schema v1 之后的每次升级必须有幂等 migration 和 close/reopen/restart 测试。
- stable `run_id/call_id/effect_id` 仍是新 SDK 内部必须保持的恢复不变量；“不兼容旧数据”不等于
  可以削弱新 Run 的 idempotency 或 side-effect replay 防护。

## 8. 发布与消费者边界

已确认独立仓库名为 `simple-harness-sdk`，通用 SDK 使用 Apache-2.0，Simple Harness 产品层继续
BUSL-1.1。只有版权持有人拥有或可重新许可的代码才能进入 SDK；第三方代码和依赖需要逐项
notice/兼容审计。

统一 namespace、可选官方模块：

```text
simple_harness.core
simple_harness.execution
simple_harness.runtime
simple_harness.workflow
simple_harness.workflows.durable_task
simple_harness.workflows.personal
simple_harness.workflows.capability_build   # optional dependency set
simple_harness.providers
simple_harness.testing
```

Simple Harness 是本 program 的第一个真实消费者。开发期允许本地路径联调，但最终验收必须在
无 SDK 源码路径注入的环境中安装 exact wheel；生产入口不得深层 import SDK 私有模块，也不得
保留第二套同源 Kernel/Workflow。AIPhone 本 release unit 只验消费合同/可安装性并接收 exact
tag/commit/wheel hash Handoff，不修改或部署手机端。

## 9. 当前风险与计划前必须闭环的假设

1. **事务拆分风险**：14,845 行 UoW 同时是多个领域 authority。必须证明拆出的 SDK repository
   仍能让 ticket claim、child link、effect settle、terminal delivery 保持原子/CAS 语义。
2. **合同污染风险**：`execution/contracts.py` 的通用类型和 simple_harness 字段混在一起；public API
   冻结前必须完成字段 provenance 审计。
3. **AgentLoop 依赖风险**：当前 AgentLoop 不是独立 Core；必须用真实 import-side-effect 和
   fake Provider/Tool spike 验证最小依赖集合。
4. **Personal Workflow authority 风险**：移除前置 matcher 后，需要验证 bounded descriptor
   catalog 能被主 Agent稳定选择，同时 Host 仍可防止伪造 graph/owner/version。
5. **Capability Builder 平台风险**：Linux ARM64、macOS ARM64、Windows x64 的隔离构建/安装
   能力不同；公共 Workflow 必须依赖 Port，不可把 simple_harness 本机实现写入 Core。
6. **双实现风险**：Simple Harness 迁移必须按 slice 删除旧同源实现；只新增 SDK 并保持产品继续
   使用旧路径不算完成。
7. **依赖重量风险**：当前 backend 强依赖 FastAPI、Playwright、Torch、语音、Office 等；SDK
   fresh install/import gate 必须证明这些不会成为 Core 的强制依赖。
8. **预算权威风险**：当前 Provider 没返回 `cost_usd` 时累计为 `0.0`：
   `backend/agent/agent_loop.py:3353-3363`。SDK 必须选择可信 usage/cost、冻结价格上界估算，或
   `unknown -> fail closed`；恢复累计只读 durable Provider invocation ledger。
9. **许可证风险**：拟提取的 OpenAI Adapter、Native Engine、Code Runtime、Builder 文件均带
   BUSL header。进入 Apache 仓库前必须逐文件记录 copyright/共同作者/第三方/生成来源，取得
   可重新许可结论；不能用移动路径代替重新许可审计。

## 10. 架构基线结论

提取完整 durable Harness 和三个官方 Profile 在技术上可行，但不是机械搬运。现有产品装配已经
具备固定 root Agent、模型显式 Profile 选择、durable launch ticket、child Run、native Workflow、
Provider/Tool dispatch ledger、terminal delivery、fenced Effect 和 reconciliation 骨架；待提取
类型本身仍保留 legacy router/ticketless child 等旁路，三个 Profile 的 durable 边界也不相同。
真正工作量集中在 public authority 收窄、完整 Kernel 闭包、原子 UoW、Personal catalog contract、
条件注册、许可证 provenance 与产品依赖倒置。实施 plan 必须以这些结构性切分为先决任务，不能
从复制目录或调整 import 开始。
