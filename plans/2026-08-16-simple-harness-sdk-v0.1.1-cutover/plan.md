# Program Plan：Simple Harness SDK v0.1.1 consumer cutover

> 状态：FINALIZED — 7 轮 challenger 收敛并获用户批准（2026-08-16）
<!-- plan-status: finalized -->
> 日期：2026-08-16
> Program acceptance：`../2026-08-13-simple-harness-sdk/acceptance.md`
> 覆盖：SDK-AC-5、SDK-AC-6、SDK-AC-7、SDK-AC-8
> Program scope：4 条 MUST AC；实际执行拆为 A/B/C 三个独立 release unit

## 主要矛盾

决定成败的不是“产品能 import SDK”，而是**新的 SDK candidate 是否真的拥有唯一的
Runtime/Workflow/Execution authority，并让产品只通过公开 Port 提供 Provider、Tool、权限、
Session/UI、Personal 与 Capability 宿主能力**。如果只在旧 `deskpet.harness` 外包一层 facade，
产品会继续使用旧 schema、旧 Kernel、旧 child/recovery/delivery 事务，桌面 E2E 即使通过也不能
证明 SDK；如果直接用 0.1.0，则 Workflow Host ports、official profile factories 与 conformance
placeholder 又会使切换无法完成。

因此顺序固定为：

1. 修复 SDK public Workflow/Conformance surface，形成未发布的 0.1.1 candidate；
2. 用 SDK SQLite/UoW/Runtime/Workflow authority 建立产品 Adapter composition；
3. 一次切换 text/voice/background ingress 与 terminal delivery，禁止 fallback；
4. parity 通过后退休旧 generic authority、审计式 reset、re-vendor exact candidate；
5. 自动化 fault/reopen 与真实桌面 E2E 全部通过后，才允许请求发布批准。

## Release-unit 拆分（第 1 轮挑战闭环）

本文件只承担 program 依赖图，不再用一个 receipt 覆盖四类高风险面。三个 slice 各自有
`plan.md`、gate manifest、baseline、run-dir、challenge loop、AC 子集和失败出口；前一 slice
必须拿到本地 receipt，后一 slice 才能开始实现。

| Slice | 独立计划 | 高风险面 | AC 子集 | 出口 |
|---|---|---|---|---|
| A | `slice-a-sdk-candidate.md` | SDK Harness/Workflow authority | AC-5/6/7/8 的 SDK-owned 部分 | 0.1.1 本地 exact candidate；public seam、HITL、delivery pump、预算、三官方 Profile、conformance 全绿 |
| B | `slice-b-product-adapters.md` | Harness + Provider + 权限 | AC-5/6/7/8 的 consumer Adapter 部分 | 产品只用 public SDK 组装，但 ingress 仍关闭；DeepResearch/PPT 已改为 SDK engine 上的 host-owned definitions |
| C | `slice-c-cutover-e2e.md` | Harness + Product Session/UI | AC-5/6/7/8 的 cutover/E2E 部分 | 所有入口一次切换、旧 authority 退休、reset/frozen backend/桌面 E2E/发布候选收尾 |

Slice A 失败时不得修改产品依赖；Slice B 失败时不得打开产品 ingress；Slice C 失败时不得执行
真实开发数据 reset、删除旧 authority 或创建远端 Release。外部发布仍是 program 最终单独批准点。

## Phase 2 已冻结事实

- program gate 已在任何生产代码修改前用
  `testcase/2026-08-13-simple-harness-sdk/gate-manifest.json` 初始化；source request、behavior
  contract、applicability 与六个 black-box oracle 已逐文件冻结。各 slice 开工前再初始化自己的
  manifest；新增测试只新增登记，不修改冻结 oracle。任何既有 oracle byte 变化必须有用户批准的
  behavior-change artifact。
- 绿色基线见 `baseline.md`：SDK `1122 passed, 2 skipped`；产品 `8 passed` shards +
  `9 known-failure` shards且无新增签名；public conformance 是明确 expected-red，不能冒充 PASS。
- public Runtime/Workflow 技术假设已真跑，见 `spike-public-runtime.md`：exact v0.1.0 在
  `simple_harness.workflow` public import 处失败；`/tmp` 最小 lazy-export/Host-port patch 后，真实
  SQLite + official `personal_v1` 完成 start→terminal→close→reopen→recover，Host effect 只调用一次。
  该 spike 不替代 durable_task/capability_build/mid-node/HITL/delivery/conformance 正式门。

## 适用性与边界

- `input_sensitive=true`：S1..S5 的路由与结果受自然语言语义影响。
- `llm_payload_driven=true`：Tool call、Workflow selection、HITL、Capability build payload 驱动
  durable state machine。
- `stateful_init=false`：沿用已确认 SR-9；本 slice 不改 onboarding/auth，产品冷登录另列 follow-up。
- 原始证据只写 `.local-test-evidence/<date>/simple-harness-sdk/<run-id>/`，Git 只保存小型结论、
  run/scenario ID、相对索引与 SHA-256。
- v0.1.0 不移动 tag、不覆盖 Release asset、不改同版本 bytes。产品仓库不得推送到公开位置。
- 能力完成即默认启用；不保留 SDK/legacy fallback flag、shadow 双轨或默认 OFF。
- 外部发布是最后一道不可逆动作；本地 candidate、测试与 draft metadata 完成后，必须取得用户
  明确批准才创建/发布远端 v0.1.1 tag/Release。

## 调研结论与方案权衡

### 当前典型调用链（解剖麻雀）

真实 chat 从 `backend/main.py:13231-13553` 进入
`_run_product_harness_chat_with_timeout`，再由 `main.py:8830-9113` 调
`ProductVenueRunAdapter.open()`；旧 venue 在 `backend/deskpet/harness/adapters/venues.py:446-881`
冻结 identity/context/tool/catalog 后调用旧 `KernelRunClient.start()`。旧 Kernel 在
`backend/deskpet/harness/kernel.py:631-648` 原子创建 root，child 在
`backend/deskpet/harness/child_runs.py:394-425` 原子预创建，terminal 在
`backend/deskpet/harness/kernel_terminal.py:99-176` 原子提交并 enqueue delivery。

同类切换的通用模式：product ingress 先生成产品拥有的 session/turn/host facts，再转换为 SDK
typed `RunStart`；从该边界以后 root/child/effect/provider/checkpoint/delivery ledger 只能由 SDK UoW
写。Presenter/SessionDB/WebSocket 是 SDK DeliverySink 的消费者，不得回头创建或推进 Run。

### SDK 0.1.0 阻断

- `simple_harness.runtime.drivers.workflow.WorkflowRuntimeDriver` 未向 `WorkflowContext` 注入 Host
  ports，而 `WorkflowContext` allowlist 又拒绝 durable/personal 节点实际读取的 port 名。
- `durable_task` 的 Protocol 方法名与 nodes 实际调用漂移；`personal_v1` 缺 public
  definition/profile factory；`capability_build` 只有常量。
- `simple_harness.testing.run_conformance_suite` 抛 `NotImplementedError`，CLI 固定返回
  `status=not_implemented`。
- 产品全部 `backend/deskpet/sdk_adapters/*.py` 仍为 stub；production composition 仍构造旧
  `RunKernel` 与 `ModelPersonalWorkflowMatcher`。

### SDK 0.1.1 必须新增的状态机边界

- **Host-owned Workflow registration**：SDK public Workflow engine/registry 接受消费者定义，
  但 engine、checkpoint、lease、recovery、ticket 与 delivery 仍由 SDK 拥有。这样产品 DeepResearch/PPT
  graph/Ports 留在产品层，却不保留第二套 generic Runner。
- **Durable Tool authorization**：`AuthorizationPort.authorize()` 的即时 `ALLOW/DENY` 继续支持；
  `REQUIRE_USER` 不再转 rejected，而由 SDK 用 deterministic
  `decision_id=authorization:<effect_id>` 原子 open decision + Run WAITING。Host 只发布 UI/policy
  request；`RunClient.signal()` 携带 decision_id/nonce/version/decision，SDK 校验后原子 resolve 并
  唤醒同一 Run。重复 signal 幂等，错 nonce/version、过期、late signal fail closed；restart 从 open
  decision 恢复，不再次 prepare/invoke physical Tool。
- **Delivery lifecycle**：SDK Runtime 在 `start()` 中先 startup-drain pending outbox，再启动唯一
  delivery pump；terminal commit 后 wake，sink failure 由现有 outbox release/backoff 重试；`close()`
  停止接新 wake、bounded drain、取消 pump、最后释放 lease。产品只实现幂等 `DeliverySink`，不写
  generic dispatcher loop。
- **硬预算装配**：public ReAct factory 显式接收 frozen `TerminationLimits` 和 provider
  `BudgetPolicy/FrozenPriceEstimator`。产品 candidate 使用 `max_turns=32`、`max_tool_calls=64`、
  `max_wall_seconds=900`、`max_cost_micros=10_000_000`、
  `max_consecutive_same_tool=3`、`refuse_on_unknown=true`；这些值随 Run start snapshot/checkpoint
  冻结，恢复不得读取变化后的配置。五类 limit 各有独立 fault test。

### Product startup readiness contract

产品只在一个局部 `ProductSdkRuntimeStack` 完整启动后原子发布全局引用：

1. 先验证已有登录态下 keychain/provider registry、SessionDB、capability platform、permission runtime
   与 product Workflow definitions 均 ready；动态 provider/keychain/权限决策按 Run 读取，不在进程
   启动时永久缓存默认值。
2. 创建 SDK DB/UoW、Ports、Workflow registry、Runtime；`await runtime.start()` 完成 startup
   reconciliation、delivery drain、recover 后才设置 `sdk_runtime_ready=true`。
3. 任一步失败则按 delivery pump→Runtime→Workflow resources→DB 的逆序 close，局部对象不发布，
   `_harness_accepting` 保持 false；重试重新读取全部动态依赖，不复用半初始化 catalog。
4. text/voice/background 都只观察同一 ready barrier；不得各自缓存 readiness。测试覆盖服务延迟就绪、
   首次构建失败后重试成功、close 中 ingress、重复 start/close。本合同不扩张到首次登录冷启动。

### Product-owned Workflow disposition

- `backend/deskpet/workflows/definitions/**` 的 DeepResearch/PPT graph/state/nodes和产品runtime adapters
  保留产品所有权；`spike-product-workflows.md`已用SDK SQLite Runtime证明DeepResearch terminal reopen与
  PPT interrupt close/reopen/resume不重复effect。
- `backend/deskpet/harness/adapters/product_profiles.py` 的 payload/profile descriptor 转入
  `sdk_adapters/workflow_catalog.py`，通过 SDK public host-definition registration 注册
  `workflow.deep_research`→`deep_research@v7-sdk1`与`workflow.presentation`→`ppt_pro@v2`；三个
  official Profile仍由SDK factory提供。旧开发Run按acceptance reset，不注册旧engine recovery catalog。
- PPT v2把v1中回答后仍写store的interrupt handler拆为纯barrier+独立幂等effect节点，7个只读selector
  显式声明pure；不覆盖v1 manifest。DeepResearch v7的四个physical节点全部走typed Host Ports。
- `WorkflowService/WorkflowLauncher` 中 generic runner/checkpoint/recovery authority 退休；其产品
  delivery handlers、content resolver 与 definition-specific Ports 作为 SDK Host ports 适配。
- 切换前后必须复跑现有 DeepResearch v7、PPT editable/full-page、continuation、artifact delivery 和
  restart smoke；任何功能删除都违反 `FEATURE_POLICY=only-add`。

### 外部最佳实践及本项目适配

- GitHub immutable release 将 tag、commit 与 assets 作为同一供应链身份。当前仓库尚未形成一致
  identity，因此不修移动旧 `v0.1.0`，而生成新 candidate。参考：
  `https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases`。
- Python Packaging Guide 要求 distribution version 唯一，bug fix 应发布新 final version而不是
  post-release；本项目是 0.x，但仍用 `0.1.1` 表示兼容性修复。参考：
  `https://packaging.python.org/en/latest/discussions/versioning/`。
- 常规 Ports-and-Adapters 会把宿主能力放在边界外；本项目还多一条 durable 原子性约束，所以
  不采用“旧 UoW 包装成 SDK UoW”的快捷方案，而直接使用 SDK schema v1/transaction owner，
  Product Session/UI 只做投影。

放弃的方案：

- 覆盖/移动 v0.1.0：破坏 immutable identity，拒绝。
- 产品 deep-import SDK `_internal` 或 monkeypatch sealed driver：违背 public API/consumer contract，拒绝。
- 先切 chat、后切 voice/background：会产生两个 active authority，拒绝。
- 暂时保留 legacy fallback flag：测试阶段能力必须默认开启且 acceptance 禁止双实现，拒绝。

## 文件影响清单

| 范围 | 文件 | 本次职责 |
|---|---|---|
| SDK Workflow | `src/simple_harness/workflow/contracts.py`, `runtime/drivers/workflow.py`, `workflows/durable_task/*`, `workflows/personal_v1/*`, `workflows/capability_build/*` | typed Host ports、official factories、三个 Profile 可执行合同 |
| SDK composition/testing | `runtime/__init__.py`, `workflow/__init__.py`（若缺则创建）, `testing/{__init__,cli,pytest_plugin}.py`, `tests/conformance/**` | 可公开装配的 Runtime/Runner/Registry 与真实 conformance |
| SDK release | `pyproject.toml`, `src/simple_harness/__init__.py`, `docs/release/v0.1.1.md`, release scripts/manifest | 0.1.1 candidate、单一版本来源、identity/checksum |
| Product adapters | `backend/deskpet/sdk_adapters/{composition,runtime_paths,provider,authorization,context,tools,reconciliation,delivery,ingress,personal_catalog,capability_host,conformance}.py` | 实现 SDK public Ports，禁止导入旧 generic authority |
| Product ingress | `backend/main.py` 与 text/voice/background product adapters | 单一 SDK RunClient start/signal/cancel/query/recover |
| Product projection | `backend/deskpet/agent/{turn_preparer,run_presenter,session_terminal_delivery}.py` | 只保留产品 context/presentation/SessionDB/WebSocket 投影 |
| Product profiles | product profile/catalog/capability/companion adapter 文件 | frozen descriptors、去除 Personal 前置 matcher、默认启用三个 official Profiles |
| Persistence/reset | SDK schema v1 path composition、`scripts/dev/reset_sdk_execution_data.py`, `docs/sdk-reset.md` | 独立 execution DB、dry-run/nonce/allowlist reset |
| Cutover/audit | `scripts/acceptance/assert_sdk_cutover.py`, `scripts/acceptance/sdk_full_surface_smoke.py`, `backend/tests/sdk_adapters/**`, impacted harness tests | old authority absence、全入口 smoke、contract/restart tests |
| Packaging | `backend/vendor/simple_harness_sdk-0.1.1-py3-none-any.whl`, `backend/pyproject.toml`, `backend/uv.lock`, PyInstaller hooks | exact candidate bytes/hash/frozen import |
| Docs/gate | `ARCHITECTURE/{ARCHITECTURE,SDK_EXTRACTION,PROJECT_STATUS,index}.md`, testcase result index, verification run-dir | 事实源与机器门账本 |

## Task 1 — 修复 SDK Workflow Host-port 与 official Profile public contract [AC-5,6,7]

- SDK 文件：
  - `src/simple_harness/workflow/contracts.py`：把 workflow Host services 改为 typed、冻结、可枚举
    的 port registry；未知 port 仍 fail closed。
  - `src/simple_harness/runtime/drivers/workflow.py`：从 Runtime composition 注入同一个 frozen
    workflow service set；start/recover 必须使用 checkpoint/ticket 固定的 generation/fingerprint。
  - `src/simple_harness/workflows/durable_task/{ports,nodes,definition}.py`：统一 Protocol 与节点方法，
    不允许 tests 用无约束 mock 绕过 production `WorkflowContext`。
  - `src/simple_harness/workflows/personal_v1/{__init__,definition,ports,selection}.py`：公开 official
    factory/profile descriptor 与 frozen candidate binding。
  - `src/simple_harness/workflows/capability_build/*`：提供真实 durable-task specialization factory、
    typed build/install/activate/admission Ports；成功能力默认 active。
- 同一 SDK slice 增加：
  - public host-owned Workflow definition registration，使用与 official definitions 相同的 SDK
    `WorkflowRunner/Registry`、transaction owner、ticket、checkpoint 与 recovery；
  - `tools/authorization.py` + runtime/UoW decision integration 的 durable two-phase Tool HITL；
  - `Runtime.start/close/terminal` 拥有的 delivery pump/wakeup/bounded-drain lifecycle；
  - public ReAct composition factory 的显式 `TerminationLimits/BudgetPolicy/FrozenPriceEstimator`。
- 测试：三个 official Profile 均从 public API + production WorkflowRuntimeDriver 启动；缺 port 时只
  不注册对应 Profile，补齐即注册；伪造 graph/version/generation 在 child start 前拒绝。
- 状态机测试：Tool 授权 approve/deny/cancel/duplicate/expired/wrong nonce/restart；terminal wake、
  startup backfill、sink failure/backoff、close race；max turns/Tool calls/wall/cost/same Tool 五类硬停；
  乱序 correlation、超长 args/result、拒不调用 Tool 的诚实失败/降级。
- 不变量：Workflow Engine 与 official graph 在 SDK；产品不重写节点/恢复/HITL/交付协议。
- 依赖：无。

## Task 2 — 实装 SDK conformance 与 0.1.1 exact-wheel candidate [AC-5,7,8]

- SDK 文件：
  - `src/simple_harness/testing/{contracts,suites/__init__,suites/provider,suites/tool,suites/runtime,
    suites/workflow,__init__,cli,pytest_plugin}.py`：定义 frozen `ConformanceMetadata`、
    `ConformanceHost` Protocol 与 async `ConformanceHostSession`。同步 factory 只创建 Host；每个 suite
    调 `host.open_suite(name)` 得到 fresh async context，退出必须 `aclose`；protocol major 不匹配
    fail closed，required case 不允许 skip。
  - suite 输入/输出固定为 public `ConformanceCaseResult(case_id,status,duration_ms,evidence)` 与
    `ConformanceReport(protocol_version,sdk_version,host_name,host_version,platform,python_version,
    artifact_sha256,suites,cases,status)`；CLI 与 pytest plugin 调用同一 runner，任一 required fail/error/
    skip 返回非零。
  - Provider suite 覆盖一次物理调用、typed HTTP error/cancel/usage/secret；Tool suite 覆盖 schema、
    五态、duplicate/late/malformed/reconciliation；Runtime suite 覆盖 Kernel/Driver/no-tool/one-tool/
    multi-turn/multi-tool、五预算、HITL、delivery、restart-without-replay；Workflow suite 覆盖 host-owned
    definition、三个 official Profile、ticket/fingerprint、execution-session persistence/reopen。
  - `src/simple_harness/runtime/__init__.py`、Workflow public surface：公开消费者真正需要的稳定
    composition factories，禁止要求 deep import private symbols。
  - `pyproject.toml` 与单一版本模块：改为 `0.1.1`；增加 public API snapshot/exact-wheel tests。
- 构建：从 clean SDK commit 两次 `SOURCE_DATE_EPOCH=0 uv build`，canonical wheel/sdist 内容一致；
  BUILD_INFO、SHA256SUMS、SBOM/NOTICE、planned tag commit 全对齐。
- 测试：SDK full suite、无源码 clean venv CLI、fake host 四 suite、三个 official Profile
  consumer seam；证明 wheel 内没有 tests/source 也能 conformance。
- 输出：只形成本地 candidate/manifest；不创建远端 tag/Release。
- 平台复验：同一 immutable candidate SHA 在 macOS ARM64、Windows x64、Linux ARM64 原生 Python
  3.11 环境跑 import side-effect、schema init/reopen 与四 suite；平台不能自行重建 wheel。缺任一 required
  平台则 slice A 保持 BLOCKED，不继承 v0.1.0 平台证据。
- 依赖：Task 1。

## Task 3 — 建立产品 SDK Runtime composition 与唯一 schema v1 owner [AC-5,7,8]

- 产品文件：
  - `sdk_adapters/runtime_paths.py`：从 product user-data root 解析 exact SDK execution DB；拒绝
    home/repo/evidence/symlink escape。
  - `sdk_adapters/composition.py`：只依赖 SDK public API 与 product Adapter contracts；创建 SDK
    `Database/SqliteExecutionUnitOfWork/SqliteContextPort/WorkflowRunner/Runtime`，返回 product-owned
    lifecycle facade，但不得导入/包装旧 `HarnessRuntime/ProductHarnessUnitOfWork`。
  - 生命周期 facade 公开 `start/close/recover/reconcile/query` 等 main 真需要的方法，禁止访问
    `.kernel._uow`/`.reconciler` private owner。
- 测试：clean schema v1 首建、close/reopen、启动恢复、同进程重复 start/close、第二 owner fencing、
  product SessionDB 不进入 SDK DB。
- 依赖：Task 2 candidate wheel。

## Task 4 — 实现 Provider/Tool/Authorization/Reconciliation Adapter [AC-5,6,8]

- `sdk_adapters/provider.py`：把 product registry 的 selected provider/model/base URL 与 keychain secret
  转成 SDK Provider；冻结 per-Run provider/pricing snapshot；401/402/429/5xx/timeout/cancel 分类；
  secret 不进入 SDK ledger/log/report。
- `sdk_adapters/tools.py`：把可信 product ToolSpec/handler 注册到 SDK registry；保留 reserved host
  field/schema/length 校验与五态 outcome；提供 catalog generation。
- 退休`deskpet.tools`包import即`pkgutil`扫描+全局singleton注册：每个产品handler模块改为无副作用
  `registrations(deps)`，新`tool_catalog/{contracts,builtin}.py`保存显式、冻结、可审计provider manifest；composition
  在依赖ready后逐项加载，任一required module失败则ready fail closed，不再log-and-skip。产品catalog只
  拥有handler/schema/permission metadata，generic ToolSpec/effect/outcome/inventory/dispatch改接SDK public
  contracts；`tools.registry`旧dispatcher/runtime authority退役。
- 切换前冻结79个reachable builtin/conditional registration keys及完整metadata；最终SDK Tool catalog为77
  keys，因为`deepresearch`/`ppt_pro`双入口映射为required
  `workflow.deep_research`/`workflow.presentation` profiles，`ppt_create`仍是Tool。manifest记录
  legacy→canonical映射；MCP/plugin只经独立generation/CAS transaction追加，不混入builtin import。
- 在实现前批量跑77项schema compatibility inventory；不静默改schema。普通object补显式
  `additionalProperties:false`并升级spec/schema hash；确需动态map时SDK只新增host-neutral typed bounded
  scalar-map合同（key pattern、maxProperties、value type/maxLength），绝不接受裸`true`。每项old/new
  hash与理由进入migration manifest并复跑handler等价测试。
- `sdk_adapters/authorization.py`：把 SDK Tool/Workflow authorization request 映射到现有 durable
  permission/grant UI；Host 只创建/呈现 policy decision，SDK 拥有 deterministic
  decision identity、nonce/version、WAITING/signal/restart 状态机。
- `sdk_adapters/reconciliation.py`：实现 tool/provider/startup reconciliation；handed-off unknown 不盲重放。
- 测试：SDK-S2、S6、S7 自动化；effect/provider physical call count、duplicate/late/cancel、secret canary、
  crash before/after receipt；正常新进程import`deskpet.tools`零注册/零旧Workflow import，再显式装配完整
  inventory并与切换前intended manifest逐项相等（上述2个canonical mapping除外），frozen backend startup
  无skip；故意破坏任一required provider必须整体fail而非部分ready。
- 依赖：Task 3。

## Task 5 — 实现 Context、Personal、Capability 与 official Profile 装配 [AC-5,6,7]

- `sdk_adapters/context.py`：ProductTurn preparation/memory/tool/catalog facts 转 SDK typed start/context；
  durable ReAct messages/checkpoint 只写 SDK context authority。
- `sdk_adapters/personal_catalog.py`：读取两个受信 candidate descriptor，Host 绑定 frozen
  graph/owner/version/fingerprint；删除 production `ModelPersonalWorkflowMatcher`。
- `sdk_adapters/capability_host.py`：连接 catalog search、source policy、isolated build/test、package store、
  activate/rollback 与 authorization receipts；成功后同 Run refresh 且默认 active。
- composition 注册 `agent.general` 与三个 SDK official Profiles；缺 optional port 时只缺对应 Profile，
  Core 启动不受影响，不保留 disabled/shadow Profile。
- `sdk_adapters/workflow_catalog.py` 另注册 product-owned DeepResearch v7-sdk1 与 PPT Pro v2
  definitions；只把definition/state factory/payload/typed Ports交给SDK public registry，不复制
  Runner/checkpoint/recovery；旧版本因dev reset不进入新catalog。
- 测试：SDK-S3/S4/S5 fake-provider contract、双 root identity、伪造 negative、install idempotency/restart。
- 回归：DeepResearch v7 production definition/continuation/source delivery 与 PPT editable/full-page/
  ArtifactCard 仍从 `agent.general` child ticket 进入同一 SDK Workflow authority。
- 依赖：Task 3、Task 4。

## Task 6 — 一次切换 text/voice/background ingress 与 exactly-once delivery [AC-5,6,7,8]

- `sdk_adapters/ingress.py`：把现有 ProductTurn identity/session/turn/venue/mode 转 SDK `RunStart`；
  deterministic root、resume、continuation FIFO、HITL signal、cancel、query 只经 SDK `RunClient`。
- `sdk_adapters/delivery.py`：SDK terminal/delivery event 映射到 RunPresenter、SessionDB、WebSocket、
  ArtifactCard/TTS；live presenter 与 restart backfill 用同一 delivery identity/epoch 去重。
- `backend/main.py:_build_product_harness_stack` 改成唯一 `build_product_runtime`；chat、voice、companion
  background 同一提交切换；删 direct old `root_run_identity/HostContext/ProductVenue` private access。
- 禁止 fallback flag；任一 required Adapter 缺失时启动 fail closed，不回旧 Harness。
- 测试：三个 ingress 最小 root、同 request replay、不重复 terminal/message/artifact、supervisor restart、
  关闭 websocket 后 durable backfill。
- 依赖：Task 4、Task 5。

## Task 7 — 退休旧 authority、审计 reset、vendor exact candidate [AC-6,7,8]

- 按 `../2026-08-13-simple-harness-sdk/cutover-manifest.md` 与 symbol disposition 删除/迁移 generic
  `RunKernel/Driver/child/reconciler/effect/delivery/UoW` product owners；保留 product adapters，不做
  机械整目录删除。
- 新建/更新 `scripts/acceptance/assert_sdk_cutover.py`：AST/import/runtime manifest 检查 production
  不引用旧 owner、SDK `_internal`、matcher/router/ticketless/fallback；所有入口只指向 SDK facade。
- 完成 `scripts/dev/reset_sdk_execution_data.py`：把冻结“exact dev execution DB”绑定为
  `<user-data>/data/workflow.db`及WAL/SHM（当前schema 1–29旧开发执行库）；dry-run输出inventory/hash/
  backup/nonce。拒绝新SDK execution DB、`product_state.db`、root/home/repo/evidence/Product Session/
  symlink/path traversal；实际旧库reset在独立确认后执行。新SDK与product-state均从clean schema首次创建。
- 将 Task 2 exact 0.1.1 wheel vendored，更新 `pyproject.toml/uv.lock/PyInstaller`；禁止 path/editable/
  `PYTHONPATH`。frozen backend 日志输出 SDK version/wheel SHA/Runtime owner/schema version。
- 测试：cutover audit、reset negatives/positive temp fixture、clean `uv sync --frozen`、PyInstaller inventory、
  old authority absence、全 backend/frontend/Rust 回归。
- 依赖：Task 6 parity PASS。

## Task 8 — 机器门、全表面 smoke、桌面 E2E 与事实源收尾 [AC-5,6,7,8]

- 自动化：SDK full suite；product Adapter/contracts；SDK-S6/S7；cutover/wiring/secret scans；text/voice/
  background/API 全入口 smoke；frontend type/build/tests；Rust tests；frozen backend exact-wheel smoke。
- 价值 smoke：昂贵矩阵前先跑 S1、S2 与一个 official Workflow 正向根；任一无有效结果则早停。
- 真桌面：按 frozen `manual-test.md` 跑 SDK-S1..S5；S3/S4/S5 各两个独立 root，S3 第二个在
  ≥10 轮历史；S3 另跑 HITL 拒绝/取消；全部做 supervisor restart 对账。至少一个 required root
  真人驾驶；若全 AI，先记录用户 all-AI-driving 明确批准 hash。
- 证据：每步先记录坐标/动作/期望，截图与原始日志只进 `.local-test-evidence`；Git 结果索引写
  Session/Run/child/effect/provider/delivery ID、SHA-256 与 PASS/FAIL/BLOCKED。
- 机器门：沿用开工前已冻结的 source request/behavior contract/oracle；三个 slice manifest 均声明
  `input_sensitive=true`、`llm_payload_driven=true`、`stateful_init=false`。Slice A/B分别record
  runs/evidence/audit并`finalize`；Slice C先生成除SDK-C7外全绿的C-prepublish checkpoint，此时
  `finalize`预期因C7 NOT_RUN失败且不构成receipt。不得在此阶段才首次冻结testcase。
- 文档：同一交付更新 `ARCHITECTURE/SDK_EXTRACTION.md`、`ARCHITECTURE/PROJECT_STATUS.md`、SDK
  release notes/API/handoff/ac-trace。只有全部 required gate PASS 才把 consumer cutover 标完成。
- 发布：Slice A/B receipts + C-prepublish checkpoint齐备后向用户请求发布批准；获批后创建与
  commit/BUILD_INFO/SHA256SUMS完全一致的v0.1.1 tag/Release，再从远端下载bytes验证SDK-C7并与
  product vendor比较（不同则FAIL），最后重跑audit并仅在此时让Slice C`finalize` exit 0。
- 依赖：Task 7。

## 验收追踪

| AC | Tasks | 关键证据 |
|---|---|---|
| SDK-AC-5 | 1,3,4,5,6,8 | SDK Runtime/child/effect/delivery/recovery tests，S1/S2/S3/S7 |
| SDK-AC-6 | 1,4,5,6,7,8 | fixed root/catalog ticket/anti-forgery/matcher absence，S1..S6 |
| SDK-AC-7 | 1,2,3,5,7,8 | 三 official Profiles、schema v1/reset/reopen，S3/S4/S5/S7 |
| SDK-AC-8 | 2,3,6,7,8 | exact wheel conformance/cutover/frozen backend/E2E/identity receipt |

## Phase 2 challenge round 1 closure ledger

| Dedupe key | 闭环位置 |
|---|---|
| `release-unit-high-risk-count` | 本 plan `Release-unit 拆分` + `slice-a/b/c-*.md`；三个独立 manifest/baseline/receipt/失败出口 |
| `runtime-public-seam-spike` | `spike-public-runtime.md` 的 exact 0.1.0 failure + `/tmp` patched real SQLite result；Slice A A1/A2继续覆盖未证明项 |
| `ac5-policy-matrix` | Slice A A5 固定 exact policy values、五 hard-stop 与 LLM 变异 oracle |
| `oracle-freeze-order` | program gate已在代码前 init；每个slice开工前独立freeze，Task 8只消费冻结值 |
| `startup-readiness-contract` | 本 plan readiness contract + Slice B B2 的single immutable slot、reverse close、fresh retry、503/fail-fast |
| `product-workflow-consumer-migration` | `spike-product-workflows.md` + 本 plan disposition + Slice B B5；DeepResearch v7-sdk1/PPT v2经public SDK，PPT pure interrupt版本化，旧engine checkpoints按acceptance reset |
| `authorization-hitl-state-machine` | 本 plan状态机 + Slice A A3 + Slice B B4；SDK generic decision/wait，product policy/grant Host Port |
| `delivery-dispatch-lifecycle` | 本 plan状态机 + Slice A A4 + Slice C C2；SDK唯一pump，产品只做sink |
| `conformance-host-protocol` | Task 2与Slice A A6固定Protocol、async suite lifecycle、report schema与case map |
| `candidate-platform-revalidation` | Task 2与Slice A A7固定同bytes macOS ARM64/Windows x64/Linux ARM64 required matrix |
| `product-state-store-detachment` | Slice B B1/B2/B4；`product_state.db`独立schema owner，CapabilityStore/Builder不再绑定旧execution UoW，三库相互absence与旧workflow DB-only reset有门 |
| `cross-db-authorization-saga` | Slice A A3 + Slice B B4/B4 oracle；P1/S1/P2/S2/S3/P3/S4/P4/S5/S6/P5写序、双receipt handoff、全部crash/cancel/expiry/permanent-conflict分支已冻结 |
| `release-approval-gate-cycle` | Slice C C7分成非receipt的C-prepublish checkpoint与publish后SDK-C7/finalize；frozen manual冲突绑定待用户批准behavior change |
| `ingress-ui-gate-mismatch` | Slice C C6 + SDK-C1U required UI oracle/manifest；真实麦克风与Companion可见入口各建SDK root并对账 |
| `slice-b-impact-path` | Slice B manifest已修正`backend/context.py`并纳入main、product_state、permissions、capabilities及跨库调用点 |
| `product-tool-import-detachment` | Task 4 + Slice B B3/B5；无副作用显式Tool catalog、SDK public effect/contracts、完整inventory parity与旧Workflow import guard |

## 完成出口

- 8 个 Task 全部完成且无 expected-red/skip 冒充 PASS。
- old generic authority、Personal matcher、legacy fallback、SDK deep import 全部为零。
- v0.1.1 exact candidate 的 version/tag/commit/wheel/sdist/SBOM/BUILD_INFO/SHA256SUMS 一致。
- SDK-S1..S5 桌面、SDK-S6..S7 自动化全部 required PASS。
- `plan_test_gate.py finalize` exit 0，生成有效 `gate-receipt.json`。
- 架构事实源、release docs、AIPhone Handoff 与结果索引已同步。
