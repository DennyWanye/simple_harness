<!-- plan-status: finalized (plan-bs) -->

# Plan：提取完整 durable Simple Harness SDK v0.1 并迁移 Simple Harness

> plan-status: finalized（plan-bs；2026-08-13）  
> 代码基线：`simple_harness@122ec55989f8a77e023aeb44ba1b4dae1b694269`  
> 独立仓库：`simple-harness-sdk`（实施时记录其初始 commit）  
> 行为事实源：[`behavior-contract.md`](behavior-contract.md)  
> 验收事实源：[`acceptance.md`](acceptance.md)  
> 规范性 task ledger：[`implementation-tasks.md`](implementation-tasks.md)  
> source/symbol disposition：[`cutover-manifest.md`](cutover-manifest.md)

## 1. 主要矛盾

决定成败的核心不是“把哪些文件复制出去”，而是把当前 `Harness / Workflow -> DeskPet
permissions / tools / companion / product stores` 的依赖方向，重构为：

```text
simple_harness SDK contracts + durable authorities <- consumer Ports/Adapters
```

同时必须保持跨表事务、single-owner、fence、unknown/reconciliation 和 terminal delivery 等
durable 不变量。当前 `SqliteExecutionUnitOfWork` 约 14,845 行，既是这些不变量的唯一写 authority，
又混入产品权限/投影/能力数据；如果先复制文件或按 repository 表拆事务，会得到两份可运行但无法
证明一致的 Kernel。故任务顺序必须是：**先冻结 contracts/oracles 与原子命令，再建立独立 SDK
authority；随后按调用链迁移；最后让 Simple Harness 从 exact wheel 消费并删除旧同源实现。**

架构事实和源码证据见 [`../../ARCHITECTURE/SDK_EXTRACTION.md`](../../ARCHITECTURE/SDK_EXTRACTION.md)。

## 2. 行为与范围决策

- 顶层 root 永远为 `agent.general`；SDK public API 不暴露 classifier/router。
- 同一个 root Agent 基于 frozen descriptor catalog 决定 direct answer、普通 Tool 或三个官方
  Workflow Profile；Host 只校验 catalog、authorization、candidate binding 和一次性 ticket。
- `workflow.durable_task` 为 SDK 官方 graph/runtime；`workflow.personal_v1` 保留当前单 Native
  `execute` checkpoint + EffectJournal、仅 safe-replay Tool 的能力边界；
  `workflow.capability_build` 是 SDK 官方 public Profile，但实现为受治理的 `durable_task` 特化。
- 不迁移开发期历史 Run/schema 1–29；执行一次精确、审计式 dev reset，从 SDK schema v1 开始。
- Simple Harness 是第一个真实消费者；AIPhone 本 release 仅 install/conformance/Handoff。
- SDK Apache-2.0，产品继续 BUSL-1.1；没有逐文件可重新许可结论的源码不得进入 SDK。

## 3. 最佳实践调研及本项目适配

| 实践 | 前提 | 本项目适配结论 |
|---|---|---|
| PyPA `pyproject.toml` + `src/` layout + wheel/sdist | import package 与仓库工具文件隔离，构建后从制品安装 | **照用**。独立仓库使用 `src/simple_harness/`，测试分两层：源码开发测试与“空 venv 只安装 wheel”的制品测试。SDK 是纯 Python，目标 wheel 应为 `py3-none-any`；三平台仍需原生 runner 安装/运行，而不只是构建标签检查。[PyPA packaging tutorial](https://packaging.python.org/en/latest/tutorials/packaging-projects/)、[src layout](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/) |
| runtime extras 与 development dependency groups 分离 | extras 会进入发行 metadata；dependency groups 不进入发行 metadata | **改造后用**。Core 只强依赖 `httpx`（若 sqlite async 继续用 `aiosqlite`，经 spike 后决定）；`capability-build` 用 `[project.optional-dependencies]`；pytest/docs/lint/build 用 dependency groups，避免消费者被装入开发工具。[PyPA pyproject spec](https://packaging.python.org/en/latest/specifications/pyproject-toml/)、[dependency groups](https://packaging.python.org/en/latest/specifications/dependency-groups/) |
| SemVer 必须先声明 public API | 版本号只能表达已定义的公共边界 | **照用但保持 0.x**。从 `0.1.0` 开始，只有 `simple_harness.__all__` 与 `docs/api/` 标为 public；消费者禁止 deep import `_internal`。0.x 仍用 changelog/API snapshot test 管理破坏变更。[SemVer](https://semver.org/) |
| SQLite 单事务提供 crash 下的 atomic commit | 同一逻辑命令必须落在同一个 connection/transaction；外部副作用不属于 DB 原子域 | **严格照用**。保留命令级 UoW，不按表拆 repository；Provider/Tool 出站用 prepare/handoff/outcome/unknown ledger 连接 DB 与外部世界。[SQLite transactional](https://www.sqlite.org/transactional.html)、[atomic commit](https://www.sqlite.org/atomiccommit.html) |
| REUSE/SPDX 逐文件声明 | 每个 covered file 有确定版权与 license，所有 license text 在仓库内 | **照用并作为迁移前置门**。先生成 source manifest，再迁移；不能因为用户拥有仓库就自动假设所有共同作者/第三方 snippet 可 Apache 重许可。SDK 用 `LICENSES/Apache-2.0.txt`、`REUSE.toml` 或逐文件 header，并跑 `reuse lint`。[REUSE 3.3](https://reuse.software/spec/) |
| tag-gated trusted publishing / immutable artifacts | 构建、发布、身份与制品可追溯 | **首版先 private release artifact，接口按同一门设计**。tag 触发 build，一次构建后测试并发布同一组 artifacts；记录 commit/tag/SHA-256/SBOM/attestation。若暂不上传 PyPI，也不能在消费者机器重建一个“同版本”wheel。[PyPA publish workflow](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/) |
| cibuildwheel 平台矩阵 | 主要用于含 native extension 的 platform wheel | **不照搬为默认构建器**。SDK 目标是纯 Python `py3-none-any`，一个 canonical wheel 即可；Linux ARM64/macOS ARM64/Windows x64 用原生 Python 3.11 runner安装并执行 conformance。只有引入 native dependency 时才启用 cibuildwheel；不能用 x64 上“可交叉构建但不可运行”冒充 ARM64 验证。[cibuildwheel platforms](https://cibuildwheel.pypa.io/en/latest/platforms/) |

## 4. 解剖麻雀：一条 durable child Tool 链如何迁移

典型链路选择 `agent.general -> workflow_spawn(workflow.durable_task) -> child Tool -> terminal
delivery`，因为它同时穿过语义选择、ticket、child、Workflow、Effect、UoW、恢复和交付：

1. `backend/deskpet/agent/turn_preparer.py:278-313` 冻结 Profile catalog，主模型在
   `backend/deskpet/tools/orchestration_controls.py:88-172` 的 schema 中选择 Profile。
2. `backend/deskpet/harness/adapters/subagent_registry.py:255-340` 校验 generation/权限/快照并
   签发 launch ticket；`execution/uow_ports.py:627-657` 用单命令 claim ticket + precreate child。
3. `harness/drivers/workflow.py:71-269` 按 ticket-bound Profile 启动 Native Workflow；
   `workflows/runner.py:209-258` 使用 registry/lease/checkpoint/recovery。
4. `harness/tool_executor.py` prepare/handoff/settle Effect；崩溃后的 late evidence 通过显式
   reconciliation 收敛为 completed/confirmed-not-started/still-unknown。
5. `harness/kernel_terminal.py:99-178` 把 child terminal + parent signal，或 root terminal +
   delivery outbox 原子提交；`harness/projector.py::ExecutionDeliveryDispatcher` 只把 generic
   delivery 发给产品 sink。

同类迁移的通用模式是：**先把每一步的 immutable contract 与命令级 UoW 放进 SDK；所有产品
读取/权限/执行/展示改为 Port；一条链切换后删除产品中的同源 authority，再迁移下一条链。**
不得先做 SDK 镜像、最后一次性换 import。

## 5. 目标源码布局

```text
simple-harness-sdk/
  pyproject.toml
  uv.lock
  src/simple_harness/
    __init__.py
    contracts/              # JsonValue, message, error, identity, events
    execution/              # records, UoW commands, sqlite v1, fences, ledgers, delivery
    providers/              # Port + OpenAI-compatible HTTP adapter
    tools/                  # spec/registry/control/reconciliation contracts
    runtime/                # Kernel, lifecycle, profiles, drivers, child, continuation, reconciler
    workflow/               # definition/compiler/runner/checkpoint/lease/replay/trace
    workflows/durable_task/
    workflows/personal_v1/
    workflows/capability_build/
    testing/                # reusable conformance suites/fakes/fault scripts
  tests/{unit,integration,conformance,artifact}/
  docs/{api,quickstart,migration,release}/
```

Simple Harness 产品只保留 `backend/deskpet/sdk_adapters/`：composition、authorization、context、
tool executor、personal catalog、capability host、delivery/presenter、runtime paths/reset。

`simple_harness.testing` 随 wheel 的 `[testing]` extra 发布，不依赖 SDK checkout 的 `tests/` 目录。
消费者实现 `ConformanceHost` factories，并运行：

```text
python -m simple_harness.testing \
  --host consumer.sdk_adapter:build_conformance_host \
  --suite provider,tool,runtime,workflow --json report.json
```

JSON report 绑定 SDK version、`simple-harness-conformance.v1`、host/platform；protocol major 不匹配
fail closed。pytest plugin 是同一 runner 的可选薄封装，不成为唯一入口。

## 6. Program 切片与门禁

每个 Slice 都是独立 release unit，MUST AC 不超过 8；只有上一 Slice 的 SDK conformance 与产品
parity gate 通过后才进入下一 Slice。开发期 path dependency 只用于切片联调；Slice 7 必须在无
SDK source path 的 clean venv/frozen backend 中安装 exact wheel 后重跑全部验收。

下列 Slice 描述解释架构顺序；逐文件、逐函数、逐 testcase 的规范性执行单元是
[`implementation-tasks.md`](implementation-tasks.md) 的 T0.1–T7.5。执行 receipt 和最终
`ac-trace.json` 必须使用 Task ID，不能只写 Slice ID。

### Slice 0 — provenance、oracle 与独立仓库骨架 [SDK-AC-1,2,8]

**改动文件**

- SDK：`pyproject.toml`, `uv.lock`, `src/simple_harness/__init__.py`, `LICENSES/Apache-2.0.txt`,
  `REUSE.toml`, `NOTICE`, `docs/api/public-api.md`, `.github/workflows/ci.yml`。
- 产品：本目录 `source-request.md`, `behavior-contract.md`, `acceptance.md`；新增
  `source-manifest.tsv`、`testcase-lock.json`、`migration-ledger.md`。

**实现**

1. 在同级独立 Git repo `simple-harness-sdk` 建 `src/` layout；Python `>=3.11`；Core import
   禁止环境读取、目录创建、线程/task/网络；`__init__` 只 re-export 明确 public symbols。
   Hatch 配置同时显式限定 wheel packages 与 sdist include/exclude（排除 `.venv*`、build/dist、
   evidence）；CI 必须先构建 sdist、再从 sdist 构建 wheel，防止 checkout 文件意外进入制品。
2. 逐个拟提取文件记录 source path、source commit/blob hash、copyright/SPDX、共同作者/第三方、
   target path、处置（relicense/rewrite/exclude）。任何 `license_status != approved` 阻断复制。
3. `testcase-lock.json` 冻结第 7 节 oracle 的 SHA-256；实现期允许新增测试，不允许无
   `behavior_change_id` 修改/删除/放宽 frozen oracle。
4. CI 先建 sdist、从 sdist 建 wheel，检查 metadata、REUSE、import side effects、禁止依赖/DeskPet import，
   然后从 wheel 安装测试；不发布。

**验证**

- `uv build`; `twine check dist/*`; `reuse lint`；两次 clean build 比较 canonical wheel 内容。
- clean venv `pip install dist/*.whl` 后运行 `tests/artifact/test_import_purity.py`；用 monkeypatch/
  audit hook 断言无 socket、filesystem write、thread/task、environment scan，且 `sys.modules` 无
  FastAPI/Torch/Playwright/DeskPet。

### Slice 1 — public contracts、Provider/Tool Ports [SDK-AC-1,2,3,4]

**当前来源与目标**

- 审计/拆分 `backend/deskpet/execution/contracts.py`、`harness/contracts.py`、
  `providers/base.py`、`tools/contracts.py`；目标写入 SDK `contracts/*.py`、
  `providers/base.py`、`tools/{base,registry,reconciliation}.py`。
- 不复制 `backend/providers/openai_compatible.py`；在 SDK `providers/openai_compatible.py` 按
  public Provider Port 重写单次 HTTPS 调用，把 DeskPet context/report/metrics/E2E hook 排除。

**实现**

1. frozen dataclass/enum 定义 `Message`, `ProviderRequest/Response/Usage`, `ToolSpec/Call/Result`,
   `Run/Session/Request/Call/Effect IDs`, typed events、稳定 errors 和严格 recursive `JsonValue`。
2. ToolRegistry 在 handler 前拒绝 additional fields/type/enum/length、reserved host fields；
   `ToolResult` 只允许五态。新增 `ToolReconciliationPort.observe(effect)` 返回
   `confirmed_not_started/completed/still_unknown` + typed evidence ref。
3. OpenAI Adapter 显式接收 `httpx.AsyncClient/base_url/model/Secret`；一次调用、无 Agent 状态/
   fallback/无限 retry；支持 structured tools、timeout/cancel、错误分类和最小披露。
4. redactor 遍历 request/response/event/error/trace JSON；canary 测试覆盖 repr/exception/SQLite。

**验证**

- SDK Provider/Tool conformance + 本地受控 HTTP server tool-call roundtrip。
- 将产品 `backend/tests/test_openai_compatible.py`、`test_p5s2_malformed_tool_args.py`、
  `companion/test_provider_dispatch.py` 的通用 oracle 移植为 SDK black-box tests；产品原测试保持。

### Slice 2 — clean schema v1 与 durable execution authority [SDK-AC-2,5,7]

**当前来源与目标**

- 参考 `backend/deskpet/execution/{contracts,uow_ports,provider_invocations,dispatch,fences}.py`、
  `workflows/store/{schema,execution_uow}.py`；在 SDK `execution/` 新建精简合同、SQLite schema v1、
  command UoW、Provider/Effect/delivery ledgers。

**实现**

1. schema v1 只含 execution session identity、run/start snapshot/event、profile ticket、child
   command/link/signal、continuation、decision、checkpoint/lease、provider invocation、effect、
   delivery outbox、schema migration/version；不含 Product Session/Message/UI、DeskPet grant/
   capability projection。
2. 保持一个 connection/transaction owner；实现 [`SDK_EXTRACTION.md §7`](../../ARCHITECTURE/SDK_EXTRACTION.md)
   八组原子命令。禁止把一次命令拆成多个 repository commit。
3. Provider invocation 记录 `claimed -> handed_off -> succeeded/failed/unknown`，恢复时不把
   `started/start_unknown` 自动当作未开始；cost 从可信 usage/冻结 estimator 得到，否则为
   `unknown` 并按 hard-cap policy fail closed。
4. Effect ledger 记录 prepared/handoff/outcome；reopen 后调用 `ToolReconciliationPort`，无证据
   保持 `unknown`。delivery outbox 在 root terminal 同事务写入，dispatcher 幂等 settle。

**验证**

- 每个原子命令在 commit 前后逐写点 fault injection：reopen 后只允许完整 before/after 状态。
- WAL/rollback 模式分别测 process kill/reopen、并发 CAS、lease epoch、duplicate/late result、
  Provider/Tool unknown；运行 `PRAGMA integrity_check` 与 foreign-key check。

### Slice 3 — 完整 RunKernel、ReAct 与 conformance [SDK-AC-4,5,6]

**当前来源与目标**

- 重构来源：`backend/deskpet/harness/{kernel,kernel_terminal,admission_launch,context,
  child_signal_runtime,child_runs,live_index,runtime,start_snapshot,user_continuations,reconciler,
  tool_executor,profiles,ports}.py`、`harness/drivers/{react,react_loop}.py`、
  `backend/agent/{agent_loop,termination_gate,context_manager}.py`。

**实现**

1. SDK `runtime.build()` 必填 `root_profile=agent.general`；删除 public classifier/router 和
   ticketless child 参数/分支。child launch 只能是 frozen ticket；catalog stale 产生 typed
   `tool_catalog_stale` 并走 single permanent terminal path。
2. 迁移完整 lifecycle：admission、immutable start snapshot、activation、live index、single
   owner/recovery lease、DriverRuntime、continuation FIFO、signal/cancel、child coordinator、
   terminal delivery、startup reconciliation、显式 async `start/close`。
3. 把 ReAct state machine 从 DeskPet Context/permissions/capability/tool registry 中剥离，改注入
   `ContextPort/ProviderPort/ToolPort/AuthorizationPort/TracePort`；保留 max turns/tool calls/wall
   clock/cost/repeated-tool hard gates 和 structured termination。
4. SDK orchestration controls 是 reserved control contracts，不继承 DeskPet `shell` category；
   Host AuthorizationPort 在 ticket/effect commit 前校验。

**验证**

- fake Provider/Tool 覆盖 no-tool、one-tool、multi-turn、多 Tool、cancel、HITL、continuation、
  root+2 child、crash/reopen、no duplicate Provider/Effect/delivery。
- 移植并保持产品 oracle：`test_general_agent_root.py`, `test_run_kernel.py`,
  `test_harness_bootstrap.py`, `test_wi2_tool_executor.py`, `test_execution_continuations_uow.py`,
  `test_provider_dispatch.py` 的通用断言。

### Slice 4 — Native Workflow Engine 与 Agent 自主 Profile 选择 [SDK-AC-5,6,7]

**当前来源与目标**

- `backend/deskpet/workflows/{contracts,errors,control,definition,native,runner,recovery,replay,
  execution_ports}.py`、checkpoint/lease/trace store；`harness/drivers/workflow.py`、
  `tools/orchestration_controls.py`、`harness/adapters/subagent_registry.py`。

**实现**

1. WorkflowRunner 只通过注入的 immutable registry、checkpoint/lease/recovery/quarantine/
   replay/trace/UoW Ports 工作，不在内部 new `SqliteExecutionUnitOfWork`。
2. Profile catalog descriptor 包含 stable key、自然语言职责、use/avoid、input schema ref、
   generation/fingerprint；主 Agent 的 `workflow_spawn` 控制 schema 只枚举当轮 model-spawnable
   Profile。Host 只做 deterministic validation/ticket binding。
3. 删除生产可达 regex router/independent semantic classifier；多文件 durable rule 写进
   `workflow.durable_task` descriptor 建议，不是 Host 路由。
4. conditional registry：模块安装且 required Ports 完整才注册；缺 DeepResearch/PPT 不影响启动。

**验证**

- conformance 检查 direct/tool/workflow 三路均从 `agent.general` 出发；unknown Profile、stale
  generation、伪造 driver/graph/version、reused ticket fail closed。
- Native runner fault matrix覆盖 node retry/loop/HITL/checkpoint/lease expiry/quarantine/replay。

### Slice 5 — 三个官方 Workflow Profile [SDK-AC-5,6,7]

**5A durable_task**

- 重构 `workflows/definitions/v1/durable_task.py` 与 `definitions/code_*`、
  `adapters/code_runtime.py` 的通用部分到 `workflows/durable_task/`；把 LLM proposal、capability
  search、Tool、workspace、output contract、artifact、authorization 改为 Ports。
- 保留 plan/HITL/tool loop/test-audit-repair/declared output/receipt/terminal delivery；移除
  `workflows/routing.py` 对 tool-free completion 的语义权威，改用 plan/effect/output receipt。

**5B personal_v1**

- 把 personal manifest/compiler/interpreter 和 safe Tool policy 移到 `workflows/personal_v1/`；
  新增 `PersonalWorkflowDescriptor`、bounded catalog、privacy whitelist、candidate id、generation/
  fingerprint；`workflow_spawn(candidate_id=...)` 后 Host 绑定 owner/version/graph。
- 删除 `companion/turn_authority.py::ModelPersonalWorkflowMatcher` 生产调用；Companion store/UI
  通过 `PersonalWorkflowCatalogPort` 提供候选。v0.1 不新增任意副作用 personal node。

**5C capability_build**

- 把 `capabilities/builder.py`、builder validation/package contracts 的通用治理流程改造成
  `workflows/capability_build/` 可选模块；Profile 明确绑定 `durable_task@v1`，注入缺口 receipt、
  source/build/test/install/activate payload。
- required Ports：`CapabilitySearch`, `SourcePolicy`, `IsolatedBuildExecutor`, `PackageStore`,
  `CatalogActivation`, `Authorization`；任一缺失不注册，齐全即默认启用。

**验证**

- 三个 Workflow 各有独立 fake-host E2E、crash point matrix、output/receipt audit。
- Personal 两候选真模型选择与伪造 graph negative；Capability 必须先有 current-stamp search miss，
  越权/坏 hash/测试失败不得安装，成功安装后同 Run catalog refresh 可用。

### Slice 6 — Simple Harness 首个真实消费者与开发数据重置 [SDK-AC-1..8]

**改动文件**

- 新建 `backend/deskpet/sdk_adapters/{composition,authorization,context,tools,reconciliation,
  personal_catalog,capability_host,delivery,runtime_paths}.py`。
- 改 `backend/deskpet/harness/adapters/product_composition.py`、`product_profiles.py`、
  `product_turn_open.py`、`backend/main.py` 与 backend packaging，改为 SDK public API。
- 产品 Tool handlers、Presenter/SessionDB/ArtifactCard、Companion store、permission UI 保持产品
  ownership；旧 Harness/execution/三个 Workflow 同源模块在 parity 通过后删除。

**实现**

1. 先用本地 path dependency 完成 Adapter；禁止 wrapper 内再调用旧 Kernel/Workflow。
2. 写 `scripts/dev/reset_sdk_execution_data.py`：先解析并打印 exact user-data DB/sidecar 路径和
   SHA-256 inventory，要求显式 `--confirm-reset <generated-id>`，只删除允许列表中的开发
   execution DB/WAL/SHM；不碰 Product Session、`.local-test-evidence` 或宽目录。
3. composition 建 SDK runtime + 三个 Profile；普通产品 ingress 只调用 SDK run client；产品
   context/tool/authorization/delivery/personal/capability 实现 Ports。
4. 每切一条生产链，AST/import/call-graph gate 断言旧同源 authority 无生产引用；最后删除旧实现，
   保留必要的产品 Adapter 与历史文档，不留 feature flag 双轨。

**验证**

- 原 Harness 精确回归、backend tests、frontend tests、build 全绿；新增 SDK-S1..S7 自动化。
- 按 AGENTS.md 用真实 Tauri/桌面 UI 做 SDK-S1..S5：截图、坐标点击、日志与 Run/child/effect/
  delivery ledger 交叉验证；原始证据只存 `.local-test-evidence/`。
- 重启 supervisor 后验证同 Run ID、无重复 Provider/Tool/child/ArtifactCard，terminal delivery 可达。

### Slice 7 — exact wheel cutover、跨平台、发布与 AIPhone Handoff [SDK-AC-1,8]

**实现**

1. SDK release CI 在 Linux runner 一次构建 canonical pure-Python wheel/sdist/SBOM，检查两个 clean
   build canonical 内容一致；三平台 Python 3.11 原生 runner安装同一个 wheel SHA-256并跑
   conformance（Linux ARM64、macOS ARM64、Windows x64）。
2. Simple Harness lock 从 path dependency 改为 exact wheel/version/hash；clean checkout/venv
   禁止 `PYTHONPATH`、editable/path source 注入，重跑 Slice 6 全部门禁并构建桌面应用。
3. 扫描产品生产 import，必须只有 SDK public API，不得 import SDK `_internal`，不得存在旧同源
   Kernel/Workflow；记录 negative search 与 package inventory。
4. tag `v0.1.0` 绑定已测试 commit；发布**同一组** artifacts，不重建；生成 SHA-256、SBOM、
   third-party notices、API reference、quickstart、migration/reset guide、changelog/attestation。
5. AIPhone Handoff 包含 exact tag/commit/wheel hash、Python/platform matrix、Provider/Tool/
   Authorization/RuntimePaths/SQLite contracts、三个 Profile 装配方式、Mobile Host Adapter 边界、
   install/conformance 命令和已知限制；不修改/部署手机。

发布绑定固定为：`git@github.com:DennyWanye/simple-harness-sdk.git`，初始 visibility **private**；
Apache-2.0 不等于自动公开，改 public 需另行显式批准。immutable artifacts 位于该 repo 的 private
GitHub Release。Simple Harness 从 Release 下载并校验后，把同一 bytes wheel vendored 到
`backend/vendor/`，由 `backend/pyproject.toml + uv.lock` 锁定；`build_backend.ps1` 和 PyInstaller
先验 hash 再 collect SDK。这样 clean/offline desktop build 不依赖 private Release 登录态，也没有
SDK source/path/editable 注入。AIPhone Handoff 使用 private Release下载说明与同一 SHA-256。

**最终门禁**

- SDK-AC-1..8 的 `AC -> slice/task -> code -> testcase -> result` 全部有证据。
- SDK 和 Simple Harness `ARCHITECTURE/`、`PROJECT_STATUS.md` 在通过测试的同次交付更新；SDK
  记录 release architecture，产品记录实际消费 wheel/hash 和旧 authority 删除事实。

## 7. Frozen black-box oracle

已对以下文件在 `122ec559` 的 current bytes 计算 SHA-256 并冻结到
[`testcase-lock.json`](testcase-lock.json)。由于 approved cutover 会删除其部分 import owner，冻结
策略不是强迫保留旧模块：[`behavior-changes/BC-SDK-IMPORTS.json`](behavior-changes/BC-SDK-IMPORTS.json)
精确定义允许的机械 import/fixture relocation与 retired-router test处置；其余 assertion AST 必须
保持。未匹配该 transform 的 byte change 仍为 `FROZEN_ORACLE_CHANGED`。

- `backend/tests/harness_simplification/test_general_agent_root.py`
- `backend/tests/harness_simplification/test_run_kernel.py`
- `backend/tests/harness_simplification/test_harness_bootstrap.py`
- `backend/tests/harness_simplification/test_wi2_tool_executor.py`
- `backend/tests/harness_simplification/test_execution_continuations_uow.py`
- `backend/tests/harness_simplification/test_workflow_driver.py`
- `backend/tests/harness_simplification/test_workflow_outbox_tx.py`
- `backend/tests/companion/test_provider_dispatch.py`
- `backend/tests/companion/test_personal_workflow.py`
- `backend/tests/harness_simplification/test_product_workflow_profiles.py`
- `backend/tests/harness_simplification/test_model_workflow_spawn.py`
- `backend/tests/capabilities/test_same_run_activation_harness.py`

## 8. 关键假设与 spike 门

以下假设决定方案成败，不能留到实现期才发现：

| ID | 假设 | 定稿前真跑证据 | 不成立时的方案变化 |
|---|---|---|---|
| H1 | 选定的 package/build/release机制可生成 deterministic `py3-none-any` 且 clean import contract可自动审计 | 可丢弃最小 package + `uv build` + clean venv audit；记录命令/输出 | 更换 build配置/后端；Core 不接受 platform wheel |
| H2 | 当前 UoW 的关键原子命令在 fault/reopen 下确实满足 before-or-after，且可作为 schema v1 oracle | 运行现有 atomic/recovery/fault tests；列出 pass count 与覆盖命令 | 若现有语义已坏，先在产品修复/冻结 oracle，再提取 |
| H3 | 同一主模型只看 bounded descriptor 能稳定选择 personal candidate，Host 可拒绝伪造字段 | 用当前 live Provider 对固定两候选跑多次 JSON control-call probe；只保存脱敏统计/响应 hash | 若不稳定，改 descriptor/schema/clarification loop；不得恢复前置 matcher |
| H4 | OpenAI-compatible Adapter 的最小一次调用合同能覆盖当前 relay structured tool call/usage/cancel | 受控 HTTP server + 可用 live relay 各跑 at-most-once probe；不输出 key/body | 若 relay 偏离，适配写在 Provider Adapter，不污染 Kernel/Host |
| H5 | pure-Python canonical wheel 能在当前 macOS ARM64/Python 3.11 从无源码注入的 clean venv 安装运行 | 安装 H1 生成的 exact wheel 并执行 import audit；记录平台/Python/输出 | 若失败则 packaging 方案不成立；Linux ARM64/Windows x64 不是纸上假设，而是 Slice 7 必跑的原生 acceptance gate |
| H6 | 当前真实 Kernel/Workflow/ReAct 能通过 fake-host 行为 oracle，但 import闭包确有可测的 product coupling RED | 对八个真实核心模块加禁止 write/network/thread guard导入，并运行 fixed-root/Workflow/Profile/Personal fake-host tests | 若 fake-host行为已红，先修产品基线；若无 coupling RED，则缩减重构范围 |
| H7 | 当前真实运行 seams 能覆盖 Provider/ReAct/Tool/Kernel 与 attached child/terminal delivery，足以作为五类 Port 的 pre-extraction oracle | 真跑现有 integrated tests，冻结 12-case 结果；T3.0 先建立同 assertion 的 SDK RED test | 若 current seam 已红先修基线；若无法形成单个 SDK fixture则重新划分 Port，而非在 Adapter藏旧 owner |

Spike 结果写入 [`spikes.md`](spikes.md)；临时代码放 `mktemp -d`，跑完删除，不滚入实现。

## 9. 追溯矩阵

| AC | Tasks | 主要 tests/gates |
|---|---|---|
| SDK-AC-1 | T0.1–0.2, T1.1–1.2, T7.1–7.3 | artifact import purity、wheel metadata、三平台 exact-wheel install |
| SDK-AC-2 | T0.2, T1.1–1.3, T2.1 | contract snapshots、JSON/redaction canary、SQLite scan |
| SDK-AC-3 | T1.2, T2.4, T3.3, T7.2 | Provider conformance、controlled/live at-most-once、dispatch recovery |
| SDK-AC-4 | T1.3, T2.5, T3.3–3.4 | schema validation、five outcomes、duplicate/late/cancel/reconcile |
| SDK-AC-5 | T2.1–2.6, T3.1–3.4, T4.1, T5.1, T6.5 | atomic matrix、Kernel/child/HITL/crash/delivery E2E |
| SDK-AC-6 | T2.3, T3.1, T4.2, T5.2–5.3, T6.1–6.3 | fixed root、agent control、Personal candidate、no router/matcher |
| SDK-AC-7 | T2.1, T4.1, T5.1–5.3, T6.4–6.5 | three Profile E2E、clean schema/reset、restart |
| SDK-AC-8 | T0.1–0.3, T3.4, T5.4, T6.1–6.5, T7.1–7.5 | consumer CLI、desktop exact-wheel self-use、old authority absence、release/Handoff |

## 10. 执行纪律与完成定义

- 本 plan 只由 `/plan-task` 执行；开工先用 `baseline_runner.py` 锁定 668 个测试文件的分片绿色
  基线，不能裸跑全量套件；既有红与新红分别记录。
- 每个 Slice 单独提交/receipt；原始日志、截图、DB、SBOM intermediate 放
  `.local-test-evidence/YYYY-MM-DD/<slice>/`，Git 只保存结论/index/hash。
- 功能完成即默认开启；只有未安装 optional module 或 required Ports 不完整时不进 catalog，不能
  另设默认 OFF/shadow 双轨。
- 测试通过的同次交付更新 SDK 架构文档、产品 `ARCHITECTURE/SDK_EXTRACTION.md` 与
  `ARCHITECTURE/PROJECT_STATUS.md`；缺文档更新即未完成。
- 不做旧 Run兼容；但不能以“开发重置”为理由跳过新 schema v1 migration、reopen、crash、
  idempotency、unknown/reconciliation 测试。
