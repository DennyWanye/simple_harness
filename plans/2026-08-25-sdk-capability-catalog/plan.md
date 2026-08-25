# Plan：SDK-first 统一能力目录与同 Run 渐进披露

## 主要矛盾

- 决定成败的核心问题不是“少传几个 schema”，而是让 **Provider 可见集合可以在同一 durable Run
  内受控演进，同时执行授权保持不可变地由既有 Tool/Effect authority 决定**。若只改 Host prompt 或首包
  allowlist，激活无法进入下一次 Provider attempt；若让 catalog 直接执行 handler，又会把可见性误当授权。
- 典型调用链：`_run_product_harness_chat -> SdkRunToolAuthorityRegistry.prepare_run -> SDK RunStart
  capability_snapshot -> ReActDriver._tools -> ReActLoop ProviderRequest(tools=...) -> tool_search/describe/activate
  handler -> prepared scope revision -> 下一轮 ProviderRequest`。当前断点是 `ReActRunInput.tools` 在进入 loop 前
  一次求值，后续 attempt 永远复用同一 tuple。

## 关联验收标准

- 覆盖 AC-18～AC-24；测试义务 TO-A18～TO-A24、TO-R12～TO-R15。
- 行为保持：执行仍经过 SDK `EffectExecutor`、Host authorization/HITL、resource scope、MCP staleness、
  idempotency/reconciliation；Memory、Voice、外部 marketplace 和其他消费者产品不改变。

## 最佳实践调研与本项目适配

- OpenAI Codex 参考锚点 `0d9bb6c34c2742ee8bcddfccb6404a447926ff9f`：`spec_plan.rs` 先构造单一
  registry，再把 MCP 合入并施加 Direct/Deferred/Hidden；存在 deferred 时才暴露 `tool_search`；
  `tool_search.rs` 索引名称、描述和 schema 字段；Skill 使用有界 catalog，正文按明确选择加载。
  本项目照用“一个目录、分层披露、按需完整内容”，但不照搬 Responses API `defer_loading` wire 字段，
  因为 simple_harness 需支持通用 OpenAI-compatible Provider。
- SDK 适配：把 catalog/search/describe/activation state 和 Provider projection 放进
  `simple_harness.tools`；消费者只提供 source metadata、ToolSpec/handler 和 eligibility/permission facts。
  Host-specific MCP health、Skill loader、workspace 与权限决策留在 Host adapter。
- durable ReAct 适配：每个 Provider request 在 `ready` 阶段从 Run-local exposure port 重新投影工具；已经
  durable reserve 的 request 继续重放其原始 snapshot，不能因进程重启或 catalog 更新改变。每批 Tool
  结果结算后，把 activated IDs/hash 写进 ReAct checkpoint；恢复从 frozen catalog + checkpoint 重建。
- 放弃方案：仅靠关键词预路由会丢失未命中能力；每轮发送 75 schema 没解决成本和选择噪音；让
  `tool_activate` 直接执行目标 handler 会绕过授权；依赖 Provider 专有 deferred wire 会破坏 Provider-neutral。

## 文件影响清单

| 文件 | 职责 | 本次改动 |
|---|---|---|
| `/Users/denny/projects/simple-harness-sdk/src/simple_harness/tools/catalog.py` | 公共能力目录 | 新增 typed source record、exposure、search/describe/activate、稳定 ID/hash/nonce、有界投影 |
| SDK `tools/__init__.py`、顶层 public exports/snapshots | 公共 API | 导出新 API 并锁定 typing/import purity |
| SDK `runtime/drivers/react_loop.py`、`react.py`、termination/checkpoint contract | 同 Run Provider loop | ready attempt 动态读取 Runtime Tool exposure；checkpoint 持久 activated identity，reserved request 仍精确重放 |
| SDK tests/conformance、integration、artifact、future-consumer fixture | SDK 证据 | 独立消费、同 Run 激活、恢复、重复/旧 nonce、类型与 wheel 契约 |
| Host `backend/deskpet/sdk_adapters/capability_catalog.py` | 产品适配 | 合并 built-in、健康 MCP、Skill metadata、Workflow 为 SDK source records |
| Host `backend/deskpet/sdk_adapters/tool_authority.py` | 权限/Run authority | 改用 SDK catalog/exposure；保留 product authorization，并适配 checkpoint restore |
| Host `backend/deskpet/sdk_adapters/tools.py`、`backend/main.py` | 启动装配 | SDK ToolRegistry 纳入 eligible MCP proxy 与控制工具；fresh Run 使用 explicit-deferred-v1 |
| Host MCP manager / capability revision hook | catalog generation | connect/disconnect/reconnect 只影响新 Run generation，进行中 Run 继续 frozen snapshot |
| Host Skill catalog/`skill_tools.py`/Context snapshot | Skill 按需加载 | 普通 Turn 注入 bounded metadata；`skill_invoke` 返回 exact instruction 作为后续 USER/untrusted context |
| Host/SDK README、ARCHITECTURE、PROJECT_STATUS | 事实源 | 写回最终生产链、版本、hash、证据与限制 |
| Host SDK dependency pin、release metadata | exact wheel | SDK 发布后以 exact version+SHA、installed origin fail-closed 消费 |

## Complexity inventory

| 复杂度表面 | 新增 | 理由 / 绑定 |
|---|:---:|---|
| 新依赖 | 否 | 搜索使用标准库归一化/tokenization；避免引入索引服务 |
| 新公共 API | 是 | SDK Capability Catalog / Run exposure，AC-18、AC-20、AC-24 |
| 新持久化字段 | 是 | ReAct checkpoint 只保存 catalog identity/digest、exposure revision、activated IDs/hash 与 describe facts；direct/deferred 从 frozen v6 envelope 推导，AC-20、AC-23、TO-R13 |
| 新配置项 | 否 | 测试阶段默认启用；direct kernel 是代码内显式 policy |
| 新抽象层 | 是 | Host-neutral source + exposure port，解除 Host catalog 与 SDK Provider loop 错位，AC-18 |
| 新后台任务 | 否 | generation 复用现有 MCP lifecycle；不新增 watcher |
| 复用已有实现 | Host `ToolCapabilityBridgeService`、`SdkRunToolAuthorityRegistry` | 迁移纯算法/保留权限 adapter，避免双实现 |
| 标准库 | dataclass/enum/hashlib/json/re | canonical identity、有界索引与稳定排序 |

## Assurance / 信任与失败边界

- Profile：FULL；共享 Provider/runtime/registry 初始化、LLM 输出、权限与 crash recovery 均在范围。
- trust boundary：capability metadata 是消费者输入，冻结前必须校验唯一 namespace、JSON schema、大小、
  source identity 和 eligibility；搜索结果只是 descriptor。describe nonce 绑定 run、catalog fingerprint、
  scope revision、capability ID、schema hash；activate 只改 exposure state。
- 数据流：live Host sources → immutable SDK catalog generation → RunStart frozen snapshot → compact direct
  Provider projection → search/describe/activate → checkpoint activated set → next ready Provider request。
  目标执行仍进入 ToolRegistry/EffectExecutor/Host policy；目录不保存正文参数、凭据或 tool result。
- 范围内失败：catalog collision、MCP race/stale、超长 descriptor/schema、跨 Session nonce、重复/乱序
  activation、crash/restart、Provider 不支持专有 deferred、Skill 正文泄露、权限误提升。
- 停止追踪点：MCP server 内部实现、第三方 Provider 模型质量、AIPhone/K6 等消费者、远程 marketplace、
  用户授权后的外部系统业务结果均不在本次实现；只验证边界 receipt 与可观察结果。

## 任务清单（按依赖排序）

### Task 1 — SDK 公共 Catalog 与 Run exposure [AC-18, AC-19, AC-20, AC-22, AC-23]

- 新建 SDK `tools/runtime_catalog.py`，定义 closed discriminated union：`ExecutableToolRecord`、
  `SkillResourceRecord`、`WorkflowProfileRecord`。canonical capability ID 与 Provider name、Skill locator、
  Workflow `profile_key` 分字段；只有 executable Tool 投影 ToolRegistry/ProviderToolSpec，Skill 通过 direct
  `skill_invoke`，Workflow 通过 direct `workflow_spawn`。
- 定义 `RuntimeToolCatalogSnapshot`、`ToolExposureMode`、`RunToolExposureState`、`RuntimeToolCatalog` 和
  `RunToolExposurePort`。冻结时校验 ID/namespace/schema/source revision/limit，预构建搜索文档；search
  稳定打分分页且不返回完整 schema；describe 生成绑定 exact revision/hash 的 nonce；activate 校验后幂等。
- `provider_specs(state)` 只返回 direct+activated，稳定排序且去重；audit summary 只含计数、source 和 reason code。
- 把现有 Host 纯 capability 算法的通用部分迁入 SDK，Host 只保留 product eligibility/permission adapter。
- 验证：SDK unit/property-style 矩阵覆盖 duplicate、collision、invalid schema、bounds、旧 nonce、跨 Run、重复激活。

### Task 2 — SQLite v6 catalog、ReAct 动态投影与原子恢复 [AC-18, AC-20, AC-23, AC-24]

- 修改 `ReActRunInput`：静态 `tools` 保持兼容，新增可选 `tool_exposure` port；在 phase=`ready` reserve
  Provider request 前调用 `provider_specs(run_id, checkpoint exposure state)`，而 `provider_reserved` 永远从
  durable request snapshot 重放。
- `SqliteExecutionUnitOfWork` 成为唯一 owner：v6 envelope 完整冻结 kind、canonical ID、source revision/
  incarnation、mode、descriptor/search facts、schema/locator/profile、alias 与 handler locator。使用两个不混义
  的 digest：`provider_specs_fingerprint` 保持 v5 ProviderToolSpec 兼容语义，`catalog_envelope_digest_v6` 覆盖完整
  v6 envelope；StartSnapshot 分别 pin generation、legacy-compatible fingerprint 与可选 v6 digest。RuntimeServices 用一个 resolver 读 stored bytes，
  并按 exact ID/revision/spec hash 重绑 handler。
- 新建 exact fresh schema v6 与 backup-first `migrate_execution_v5_to_v6`：旧 specs_json 转
  `legacy_static_tool` envelope，原 `provider_specs_fingerprint` 与 Run 引用 byte-for-byte 保留，同时从迁移后
  envelope 计算独立 `catalog_envelope_digest_v6`；resolver 按 snapshot schema version 选择校验集合，绝不把
  两个 digest 互相比较。替换前后 integrity/readback，失败恢复。
- versioned checkpoint 只保存 catalog identity/digest、exposure revision、activated IDs/hash 与 describe facts；
  direct/deferred 从 frozen v6 envelope 推导；legacy checkpoint 显式 static-tools mode。
- `tool_activate` 产出 typed catalog-bound body-free receipt并进入 durable terminal effect result；首次执行与
  terminal-effect replay 均确定性 reapply，checkpoint exposure 是 Provider/authorization 恢复 authority。
  覆盖 handler、effect settle、context append、checkpoint CAS 前后 crash window。
- 验证：单 root fake Provider 三 attempt（search→activate→target execute）、provider-reserved crash、激活后
  crash、duplicate/late activation、static backward compatibility。

### Task 3 — SDK future-consumer 与制品契约 [AC-18, AC-24]

- 扩展 `tests/conformance/future_consumer_fixture.py`：fixture 仅定义 source/permission/handler，构造
  registry/catalog/exposure/Runtime，不 import Host；验证执行仍经过 authorization/effect ledger。
- 更新 public API JSON、strict mypy、import-purity、README/API 使用文档与 artifact manifest tests；本任务不
  提前做最终版本构建，也不重复写最终架构状态。

### Task 4 — Host 四源统一适配与 compact kernel [AC-18, AC-19, AC-21, AC-22]

- 新建 `sdk_adapters/capability_catalog.py`，把产品 manifest built-in、legacy registry 中当前健康 MCP、
  first-party Skill metadata、Workflow metadata 映射成 SDK source records；namespace 使用
  `builtin:*`、`mcp:<server>:*`、`skill:*`、`workflow:*`，冲突 fail-closed。
- direct kernel 显式包含结束/澄清、search/describe/activate、Skill invoke、必要 Context/Workflow 控制，
  总数≤24；其余 eligible 全 deferred，不做关键词丢弃。记录旧 75 项 schema token baseline 与新首包比例。
- MCP manager 在 initialize+list_tools 后生成 opaque incarnation；整个 server tool set 一次 registry CAS
  原子发布，随后才 state=running。SDK record 冻结 server/incarnation/schema/remote/build/adapter identity；
  proxy/check_fn/mcp_call 执行前核对 expected incarnation。任何 reconnect 推进新 generation；旧 Run 只能
  stable stale/unavailable，绝不能借用 replacement session。
- 普通 Turn 只注入 bounded Skill descriptors；SDK 新增 private `InstructionContribution`，绑定 locator、
  content hash、trust=`untrusted_data`、byte/token bounds 与 body。`skill_invoke` ToolResult 只含 body-free
  receipt，ReAct 下一 attempt 单次投影，不依赖 Provider 会丢弃的 metadata；公开投影/日志/receipt 无正文。
- 验证：四源 identity 一致、real manifest coverage 100%、direct/budget 门禁、MCP reconnect race、Skill bounds。

### Task 5 — Host authority/Provider/执行接线 [AC-20, AC-21, AC-23, AC-24]

- `SdkRunToolAuthorityRegistry.prepare_run` 改由 SDK snapshot/state 构造并持有 exposure port；
  `SdkCapabilityBridgeAdapter` 委托 SDK search/describe/activate，commit 后同步 capability hash/scope revision。
- `_execute_sdk_run` 将 exposure port 传入 SDK ReAct，替换 `SDK_FULL_CATALOG_DISCLOSURE_POLICY` 为测试阶段
  默认开启的 explicit-deferred；Provider adapter 无需 Responses 专有字段。
- authorization `_prepared_call` 继续要求 direct-or-activated 且核对 schema/execution identity；目录激活
  不创建 grant。公开日志/Inspector 只投影 bounded counts/source/reason，不含 schema 参数、Skill body、凭据。
- 验证：generic OpenAI-compatible mock/真实 relay payload，policy denied、cross-session、workspace/origin、
  alias compatibility、WAITING/restart 恢复。

### Task 6 — 自动化、真实 UI、冷启动与两阶段发布 [AC-18～AC-24]

- 先跑 focused SDK/Host tests，再跑 affected/critical/full-surface、TypeScript/Rust/build；按大型仓库
  `baseline_runner.py` 分片并区分既有红。
- 用隔离 bundle/userdata/ports、只启动 Tauri（由其自管 backend+Vite），真实 UI 逐一执行 CAP-1～CAP-5；
  至少两个 root、一个≥10轮历史、一个真实 generic Provider。保存截图/log/hash 到 ignored
  `.local-test-evidence/2026-08-25/sdk-capability-catalog/`。
- 选择未使用且大于已 tag 0.5.2 的 final `VNEW`；clean `CSDK` 只构建一次 authoritative
  wheel/sdist/BUILD_INFO/SHA256SUMS。发布前在隔离 Host candidate 保留完整 0.4.0 rollback tuple，vendor
  exact VNEW wheel并更新 pyproject/lock/sdk_candidate（含 CSDK），跑 SDK full、Host full、generic Provider、
  CAP-1～CAP-5，生成 hash-bound prepublish receipt。
- Task 6 是唯一版本/构建/发布与最终事实源 owner：在这里一次性 bump/核对 final version，执行 full、ruff、
  mypy、build、twine、REUSE、clean installed-wheel，并最终更新 SDK ARCHITECTURE 与 Host ARCHITECTURE/
  PROJECT_STATUS；Task 3 不重复这些动作。
- prepublish 失败则不发布、不移动 production pin；receipt 全绿后才创建 exact tag/release并上传原 bytes，
  下载回验每个 asset/BUILD_INFO/SHA，再推广已测 Host commit并重跑 fail-closed identity+startup smoke。
- promotion 后失败则停止 app，恢复 0.4.0 wheel/pyproject/source/lock/sdk_candidate tuple并跑旧 Run recovery；
  v6 state 必须先证明 downgrade-readable 或 lossless restore。错误 release/tag 不覆盖，修正版用新版本。
- 同一次交付更新两仓 ARCHITECTURE、Host PROJECT_STATUS；生成 plan-test gate receipt。任何 required
  Provider/UI/MCP/Skill 场景被环境阻塞都明确标 BLOCKED，不能用 unit test 代替。

## 实施前 spike（challenge required）

- Spike S1：证明当前 ReAct `ready` 每次都重新构造 `ProviderRequest`，而 `provider_reserved` 从 durable
  snapshot 重放；在 `ready` 注入 exposure port 不破坏 unknown-outcome replay。
- Spike S2：用 Host 真实 75 项 frozen catalog 生成 SDK snapshot，验证 direct≤24、deferred coverage 100%、
  首包 schema token≤基线 50%。
- `SP-ACTIVATION-CRASH`：在 handler mutation、effect settle、Tool context append、checkpoint CAS 前后杀进程
  并重开；证明 typed receipt 重放且 schema 恰好激活一次。
- `SP-CATALOG-V6-RESTORE`：构造 active legacy Run/catalog 的 v5 DB 并 backup-first 迁移；旧 Run specs
  byte-identical，新 progressive Run 恢复 activated；reorder/missing/changed handler fail-closed。
- `SP-MCP-INCARNATION`：fake MCP A→B（同 schema 不同结果、changed schema、failure、注册中 barrier），
  证明旧 Run 永不调用 B，新 Run只见完整 B catalog，无 partial set。
- `SP-BLACKBOX-FIXTURES`：固定 15193 Tauri-managed Vite fixture、真实 Playwright navigate/title，UI 附加
  冻结 Markdown并解析 exact translate-doc locator；原始证据写 ignored 路径，小型 index 保存 hash。
- 所有 spike 命令与实际输出回写本文件；任一关键假设不成立先修 plan，再实施正式代码。

## 实施前 spike 实际结果（2026-08-25）

- `python3 /tmp/simple_harness_capability_spike.py`：
  `{"SP-ACTIVATION-CRASH":"PASS","SP-CATALOG-V6-RESTORE":"PASS","SP-MCP-INCARNATION":"PASS"}`。
  该 disposable protocol spike 覆盖 terminal effect receipt 在 effect/context/checkpoint 三个 crash window 的
  幂等重放、legacy catalog envelope 升级后旧 specs/fingerprint 保持、MCP A→B 原子 publication/incarnation
  fence；它证明协议可实现，不替代正式 SQLite/Runtime/MCP 集成测试。
- `SP-BLACKBOX-FIXTURES` 隔离启动仅由 Tauri 管理 backend+Vite：bundle
  `com.dennywanye.simpleharness.capabilityspike`，backend=18193、Vite=15193、独立 userdata；日志确认
  `DESKPET_BACKEND_DIR` 指向当前 checkout，filesystem MCP 14 tools、playwright MCP 24 tools 均 connected。
  `curl` 对固定 URL 返回 HTTP 200、`text/html`，fixture SHA-256 为
  `1b82ba9faf234fc79c0a663d3ef1b06aff5bee24a949fa66857d98c1d9eb26d8`。
- 用与 Playwright MCP 相同的已安装 runtime 和 Chrome channel 打开固定 URL，实际输出：
  `{"status":200,"contentType":"text/html;charset=utf-8","title":"Simple Harness Capability Catalog Fixture","marker":"Capability Catalog Browser Fixture\n\nREADY: cap-2-v1","finalUrl":"http://localhost:15193/capability-catalog-fixture.html"}`。
- CAP-3 通过真实 SimpleHarness 附件按钮打开 macOS 文件选择器，用 `Cmd+Shift+G` 定位冻结文件；系统面板
  实际选中 `cap-3-source.zh.md`（269 bytes、Markdown Document、exact file URL）并启用 Open。第一次点击后
  Computer Use 短暂 `timeoutReached`，服务恢复后真实 UI 明确显示 `📄 cap-3-source.zh.md` attachment chip；截图
  `.local-test-evidence/2026-08-25/sdk-capability-catalog/spikes/cap3-attachment-chip.png` SHA-256
  `6dee125a58ff85aed4537a99922378945471fd95fd892e33ca7f31cf37e67071`，随后经真实 UI 移除，未发送草稿。
  committed pack/Host source adapter focused test解析 exact `skill:translate-doc` locator/hash。故 fixture/readiness
  spike 判定 **PASS**；Skill 实际同 Run 加载 trace 与翻译结构仍必须由 Task 6 正式 CAP-3 证明，不能由 readiness
  结果替代。
- SDK real ReAct seam spike 已固化为
  `tests/integration/runtime/test_react_dynamic_tool_exposure.py`：第一次 ready 请求只有 direct tool，terminal
  Tool result 后 exposure state 写入真实 `TerminationState`/`DurableReactCheckpoint`，第二次 ready 请求重新投影
  并出现 deferred tool；另一个 case 在 Provider reserved 后故障重开，即使 live exposure 改变仍从 durable
  `ProviderRequest` snapshot 精确重放原 tools。与 termination v3 round-trip 合跑 `17 passed`，ruff PASS。
- `SP-ACTIVATION-CRASH` 不再只依赖 toy SQLite：SDK focused integration 使用真实 `EffectExecutor` 构造已
  terminal succeeded 的 `tool_activate` effect，重开执行时 handler 调用数严格为 0，并将其 typed receipt
  交给 fresh `CatalogRunToolExposure.observe_tool_result` 后恢复 deferred Provider tool；另测
  `prepare_activation` 在 terminal observation 前 provider projection 不变、重复 receipt 幂等、伪造/错 Tool
  fail-closed。与 ReAct ready/reserved checkpoint cases、termination v3 合跑 `26 passed`，ruff 与 focused mypy
  PASS。正式 SQLite fault injection 仍保留为 Task 2 required test，不能以本 focused spike替代完整迁移验收。
- `SP-CATALOG-V6-RESTORE` 已改为真实 SDK `Database`/`SqliteExecutionUnitOfWork`：显式 backup-first
  `migrate_execution_v5_to_v6` 只接收 exact closed v5，保留原 `specs_json`、generation 与
  `provider_specs_fingerprint`，另算 `catalog_envelope_digest_v6`；迁移后由正式 loader reopen。resolver 对
  handler binding reorder 可恢复，对 missing/changed/extra binding fail-closed，Skill/Workflow resource 不被
  误当 handler；相同 Provider projection 但不同 v6 envelope 也 fail-closed。schema/migration focused
  `5 passed`，ruff 与 focused mypy PASS。
- `SP-MCP-INCARNATION` 已固化到真实 Host `MCPManager`/`ToolRegistry`：每次 handshake+list_tools 后生成
  incarnation，整服 tools 在现有 RLock/revision 下原子替换，完整 publication 后才 `running`；A→B 同 schema
  不同结果、B→C changed schema/removed tool 都证明旧 handler 返回 `mcp_incarnation_stale` 且当前 session
  零调用，fresh snapshot 只见完整新集合。第二 Tool 注册失败恢复旧 tools/retired/revision；线程 reader 在
  registration barrier 阻塞后只见完整两项。manager+registry `48 passed`，compileall/diff-check PASS。
- 独立 future-consumer fixture 只声明 capability source、permission、handler，使用 SDK public API 建
  catalog/exposure，无 Host import；deferred search/describe/receipt 仅改变 visibility、不调用 handler，实际目标
  Tool仍经真实 `EffectExecutor` authorization/handoff 与 SQLite `execution_effects` terminal ledger。consumer、
  public API、wheel contents focused `7 passed`，ruff/mypy PASS。
- 隔离 spike 无用户 provider，SDK product runtime 按设计跳过；因此真实 relay 下 search→activate→execute、
  provider attempt trace、MCP 实际模型调用、Skill 正文单次加载仍是实施后 required acceptance，未提前宣称通过。
- 清理：向唯一隔离 Tauri PTY 发送 Ctrl-C；15193/18193 均已释放，未停止用户原有 SimpleHarness 进程。

<!-- plan-status: finalized -->
