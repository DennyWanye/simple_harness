<!-- last-calibrated: 3c678e71d811b0951620dbff9fb0b3d9e6989e67 -->

# simple_harness Long-Running Agent Architecture Baseline

## SDK-first Tool / Capability 目录（2026-08-25）

- Host 当前 vendor Harness `0.6.2`（wheel SHA-256 `92f5be18…`）与 Memory `0.5.2`
  （`deff2fa8…`）。SDK 公共 `RuntimeToolCatalog` 统一表达 executable Tool、Skill resource 与 Workflow
  profile；Host 只提供 source metadata、权限事实和 physical handler。
- fresh Run 使用 `explicit-deferred-v1`：固定的小型 direct kernel 包含
  `tool_search/tool_describe/tool_activate` 与必要控制工具，其余 eligible built-in、健康 MCP 均可搜索而不在
  首个 Provider payload 中。search 只返回 bounded descriptor；describe nonce 绑定 Run、catalog
  fingerprint、exposure revision、capability/projection hash。
- `tool_activate` 只返回 typed `runtime_tool_activation_receipt/v1`。SDK 在 Effect terminal 结算后更新
  `CatalogRunToolExposure`，下一次 ready Provider attempt 重新投影 direct+activated；已 reserve 的 Provider
  request 继续从 durable snapshot 精确重放。v6 catalog envelope 与 ReAct checkpoint 持久化完整目录身份和
  activated IDs，恢复时不借用当前进程的新目录。
- 可见性不是授权。目标 Tool 仍经过 SDK `EffectExecutor`、Host prepared authorization/HITL、TaskGrant、
  workspace/origin scope 与 legacy physical dispatch。动态 physical admission 读取同一个 Run exposure，并
  精确核对 Run/session/request/scope；MCP 另核对原始 schema、incarnation、fixture 与 handler 组成的
  execution identity。任何缺失、漂移或跨 Run 调用均 fail closed。
- MCP manager 先 handshake/list_tools，再以单次 registry CAS 发布整服 catalog，最后才标记 running；reconnect
  推进 incarnation。旧 Run 不会调用 replacement session，同 schema/different handler、changed schema、
  removed tool 与部分注册失败均由回归覆盖。
- Skill/Workflow 进入同一目录但不会伪装成 executable Tool。普通 Turn 只得到 bounded metadata；Skill 正文
  由 exact locator/content hash 按需读取并按 untrusted data 进入后续 Context，公开 receipt/log 不保存正文。
- 自动化当前证据：Harness full `1463 passed, 2 skipped`，Memory full `218 passed, 7 skipped`，Host affected
  `291 passed`，Host 全分片 `15 passed + 2 implementation-before known failures`；Vitest、TypeScript、frontend
  build、Rust test/check 均绿。macOS 真 UI CAP-1 已完成渐进发现、两次同 Run 激活、真实 filesystem
  search/read 和基于 README 正文的最终回答；CAP-1 可标为通过。CAP-2 也已由独立真实 UI Run 完成
  Playwright 搜索/描述/激活、精确 localhost origin admission、真实导航与 `page.title()`；Host 只把经校验的
  loopback `local_page_url` 作为受信任项目事实注入，外部域名/凭据/query/fragment 均拒绝。CAP-3 也已由
  独立真实 UI Run 完成 Skill 搜索、exact locator 调用、冻结正文单次加载与 Markdown H1/H2/H3/列表翻译；
  SDK Run authority 的完整 `ToolExecutionContext` 是 Skill snapshot identity 的唯一执行来源。
  CAP-4 真实公网负例也已在 Playwright 客户端以 `ERR_BLOCKED_BY_CLIENT` 安全失败，页面和表单均未产生
  外部副作用。CAP-1～CAP-4 可标为通过；CAP-5 仍未关闭，不得发布。

## SDK Context / Memory 官方一等集成（2026-08-22，依赖身份于 2026-08-25 校准）

- 当前 Host exact 依赖是 Harness 0.4.0（wheel SHA
  `aaf8d79a71b75bde0d71157a635b841eb557ea8889e2824571cacd7d8a58ecb6`）与 Memory 0.5.0
  （wheel SHA `c274fa6b2db538c29897f684b3f2f85775cb4b3a6870018e83792ff90b51ea46`）。生产只构造一个
  `MemoryManager`，Harness Runtime 以 `BORROWED` ownership 使用；
  shutdown 先关闭 runtime borrower，再由 SessionDB 有界 drain 并唯一 close manager。
- root/continuation 入口只提交正式 Conversation input 与各自独立、content-addressed 的 non-Memory Context
  source ref；SDK 保存 claim 后调用 read-only provider，并自动完成 Memory recall、frozen stage 与成功 Turn
  record。产品 manual recall/prepare 与 `ConversationMemoryAdapter` query/sink 已退休。
- actor 权威固定为 `LocalAuthSnapshotProvider -> validate_auth_snapshot -> identity_namespace_hash`；deployment
  来自 state DB instance，household/session binding 持久且不可换绑。模型、payload、Provider/API key 与
  legacy profile label 不能覆盖身份。
- foreground Harness 只走 execution committed-turn outbox；非 Harness producer 保留 product outbox。
  ordinary foreground catalog 移除 live `memory_recall/memory_search`，避免自动 recall 后二次查询。
- 显式 remember/read/forget 使用 resolver 生成的完整 `MemoryPrincipal` 与正式 fact API；write 保留
  salience/pinned/tier 并返回准确 fact ID；forget 使用显式 action `source_event_id`，持久 True/False receipt
  在重放与重启后稳定。重试/冲突/跨 principal/forget 不复活语义由 exact wheel 回归覆盖。
- 自动化已完成：Harness full `1379 passed, 2 skipped`、Memory 默认 full `200 passed, 7 skipped` / 正式
  candidate gate `205 passed, 2 skipped`、产品最终聚焦
  backend `83 passed` / MemoryPanel `18 passed` / TypeScript PASS；full baseline 15 PASS + 2 个实施前 known-red，
  0 unexpected。macOS Computer Use 真人 SH-M1～SH-M6 与 SH-SURFACE 全部通过。
- 完整边界与当前验收状态见 [`MEMORY_SDK_BOUNDARY.md`](MEMORY_SDK_BOUNDARY.md)。

## SDK Context / Memory 切换前基线（历史，已由上节取代）

- 当前 exact 依赖为 Harness `0.2.0` 与 Memory `0.3.0`。前台真实入口是 Tauri/React
  `chat/chat_v2 -> control_channel -> _launch_product_harness_chat -> _run_product_harness_chat`，随后
  由产品调用 `prepare_consumer_conversation_context()` 生成 projection-v2 private stage，再把 stage
  交给 SDK Runtime。`build_production_runtime()` 自身尚不会仅凭一个 Memory 实例自动完成 recall；
  Runtime 只校验、消费消费者已准备的冻结 stage。
- projection-v2 已包含完整产品 Context、当前结构化消息、Memory query/result lineage、附件、
  catalog/budget/tool authority。Memory 只以 USER/untrusted data 投影；Provider/tool retry 与 SDK
  recovery 复用冻结 stage，不二次 recall。
- 生产组合仍显式构造 Memory SDK `SQLiteMemoryBackend` 与公开 `ConversationMemoryAdapter`，然后把同一
  adapter 分别作为 `conversation_query` / `conversation_sink` 传给 Harness production config；
  `MemoryManager` 尚不能直接作为一等 Runtime port。
- 当前 Harness Memory outbox 的载荷单位是**单条消息**：root/continuation 的 user intent 在 start/enqueue
  时提交，assistant intent 只在 completed terminal 提交。因此 failed/cancelled Run 仍可能已经把 user
  intent 投递到长期 Memory；它不是“一次成功 committed user+assistant Turn”的原子记录。
- Harness execution outbox 与 `state.db.product_memory_outbox` 按 provenance 分治，并非同一前台消息双写：
  Harness 前台投影标记 `memory_authority=harness`，由 execution outbox 投递；非 Harness product
  conversation 才由 product outbox 投递。任何收敛不得粗暴删除 product outbox，否则会丢失
  Companion/background 等非 Harness producer。
- Memory 目前是前台 Runtime 的硬依赖；backend 缺失或 context recall 异常会阻止 Runtime/Run，而不是
  自动冻结空 Memory stage。前台 catalog 还同时暴露 `memory_recall` / `memory_search`，模型调用时会在
  自动 recall 之外再次查询真实 Memory。两点都是当前生产缺口。
- 当前持久身份只有 immutable `session_id -> user_id`；普通路径可回落到单一默认 Memory user。尚无
  deployment/household/actor/owner/personal/family 全链路，不能把现有 user/session 隔离描述成家庭隔离。
- UI 每条普通消息当前启动 fresh root Run；产品 retry handle 仍未接通。SDK staging/outbox 的恢复能力
  已存在，但 simple_harness 产品重启后的四个 crash window 仍需分别验证，不能用 SDK 单测代替。

下方大量 v0.1.x、切换前 Context 与历史 Harness 章节作为演进记录保留；凡与本节、
[`SDK_EXTRACTION.md`](SDK_EXTRACTION.md)、[`AGENT_HARNESS.md`](AGENT_HARNESS.md) 或
[`MEMORY_SDK_BOUNDARY.md`](MEMORY_SDK_BOUNDARY.md) 冲突，均以这些 2026-08-22 当前事实为准。

## Agent activity timeline boundary (2026-08-20)

The user-facing execution timeline is a read-only projection of the existing
canonical Run ledger. The production path is:

`HarnessPublicReadService.create_manifest -> semantic_projection.reduce_public_manifest -> PublicRunSnapshotV3 -> harnessPublicSnapshotStore -> HarnessInspectorPanel/DurableTaskSteps`.

The timeline may expose bounded, redacted activity items such as a public phase,
tool action, safe target label, status, duration, and safe input/result preview.
It must not create another Run owner, state machine, persistence authority, or
execution channel. Event identity and terminal status continue to come from the
canonical Root Run and the two-source read cut.

The context boundary is explicit: public timeline summaries, Inspector details,
technical diagnostics, event names/correlation, and hidden reasoning are
`context_visibility=exclude`. They are display/audit projections only and are
never appended to the current or later model messages. A tool result can still
be part of the normal model protocol for the execution turn that requested it;
that does not make the separately projected UI detail a context source. In the
2026-08-21 historical chain, `_assemble_sdk_messages` was the conversation input owner and the full
Context assembler had not yet entered that production chain; this is not the current owner description.

The timeline reduces by stable event identity and causal/source order. A
duplicate event is rendered once, an out-of-order event is placed according to
the durable projection order, an incomplete event degrades to an explicitly
incomplete item, and late events cannot revive a terminal Run. Public tool
projection remains default-deny, redacted, and byte-bounded; raw prepared,
outcome, provider input/output, credentials, and reasoning are not returned to
the frontend.
> **Last verified: 2026-08-21**（代码锚点 `e92883a5c52d406b30d4b4e212589fbe4fb44e13`）。
>
> **历史校准：2026-08-21、SDK v0.1.4；已被页首 2026-08-22/25 当前事实取代。** 当时状态：
>
> - ✅ SDK Runtime 初始化与前台文字执行链：`_activate_product_sdk_runtime()` 创建 `_sdk_ingress`；
>   `chat/chat_v2` 经 `_run_product_harness_chat()` → `_execute_sdk_run()`（当前定义约
>   `backend/main.py:9513`）→ `SdkRuntimeIngress.start()` → `DeliveryDispatcher`；assistant 回复经
>   `chat_v2_final` 回推。Voice 当前关闭；Companion/background 使用独立 SDK client/start 入口，
>   不经过 `_execute_sdk_run()`。
> - ✅ `_DeliverySink.deliver()` / `ProductDeliveryAdapter` 已接通（2026-08-17/19 两轮接线；host 侧缺口修复记录见 PROJECT_STATUS 2026-08-19 里程碑）。
> - ✅ 旧 harness 死代码已删除（2026-08-17，~62k 行）；保留 23 个 harness 契约/引擎文件（非 `__init__.py` 口径，清单见 §1.2）供 companion/run_adapter.py 依赖链与契约类型引用。
> - ⚠️ **已知残留（不阻塞 foreground 主链路）**：`companion/run_adapter.py` 期望
>   `KernelRunClient` 但收到 SDK `RunClient`（接口不兼容，companion 后台功能不可用）；
>   `memory_recall_host_registration_deferred` 只表示 legacy Host V2 registry 未注册，不影响 SDK
>   product 77-tool catalog 中已接通的 `memory_recall`；curation 未接新 memory SDK。
> - ✅ 记忆链路（2026-08-19）：`SessionDB` 双写 SDK `memory.db`，recall/facts/digital-twin 委托 `simple-harness-memory-sdk`（边界事实源：[`MEMORY_SDK_BOUNDARY.md`](MEMORY_SDK_BOUNDARY.md)）。
> - ⚠️ **Context cutover 未完成（2026-08-21 代码校准）**：前台文字链路虽然进入 SDK Runtime，
>   但 `_run_product_harness_chat` 构造的 `TurnInput` 没有被执行链消费；`_execute_sdk_run` 直接通过
>   `_assemble_sdk_messages` 构造“公开工作叙述 system prompt + 最多 20 条普通 conversation 历史 +
>   当前文本”。保留的 `ProductTurnPreparer` / `ProductTurnPreparationService` 和
>   `ProductContextAdapter` 对该入口不可达，故 Persona、召回 Memory、Skill 指令、附件、项目/任务
>   快照、Context OS budget/compaction 与会话 `model_params` 未进入真实首个 Provider request。
> - ⚠️ **Context 观测 authority 未切换**：Context Inspector 仍独立读取 legacy persona/facts/V2
>   registry/history，并用 `tool_count * 60` 估算；它不是 SDK frozen request 的视图。SDK
>   `provider_invocations.usage_json` 有真实 usage，但 Session context usage 与 BillingLedger 仍读
>   legacy `last_usage`/attempt store，因此 UI 可显示 `0/0` 或旧样本。
> - 🚨 **Context Inspector 公开投影未满足隐私边界**：legacy breakdown 将 persona、facts 与历史
>   preview 未经统一 public redactor 送到前端 `<pre>`；敏感 header/token/用户正文可能直接可见。
>   该预览在切换到有界脱敏 frozen snapshot 前不能作为安全审计面。
> - ⚠️ **Session/Run 隔离缺口**：SDK stack 当前按 provider/model 全局 refresh，前台 Run 通过全局锁
>   串行，后台入口不共享同一运行锁；Session `model_params` 没有进入 Provider adapter。RunStart
>   catalog generation 固定为 `1`，context-dependent tool handler 又收到静态
>   `sdk-session/sdk-request/sdk-scope`，无法证明 Provider、executor、Inspector 和 Session identity
>   使用同一冻结事实。
> - ⚠️ **Inspector 请求隔离缺口**：`context_breakdown_request` 直接接受客户端 `session_id` 并读取
>   对应 facts/history/project root，当前分支没有显式 owner/peer-group access fence；请求/响应也没有
>   request id、snapshot id 或 expected authority version。切 Session、关闭重开或连续刷新时，前端
>   只按 Session 接收，无法拒绝同 Session 的晚到旧响应。
>
> **2026-08-21 历史 Context/模型/工具 cutover 对照（非当前生产事实）**：
>
> | 关注点 | 当前生产 owner / 实际行为 | 已确认缺口 |
> |---|---|---|
> | Foreground messages | `_assemble_sdk_messages` | 只有 system + max-20 conversation + current；历史读取失败静默降级 |
> | Persona / Memory / Skill / project / attachment | 只存在于未消费的 `TurnInput` 或不可达 preparer | 没进入首个 SDK Provider request |
> | Tool schemas | 77 项 product manifest 同时注册给 SDK Provider/executor | Run payload 只留 names；generation 固定 `1`，Inspector 读另一个 V2 registry |
> | Tool execution identity | SDK `ToolContext` + product fallback | metadata 不完整；Session/scope/workspace 可回退到 request/default 或固定假 id |
> | Authorization / capability | unconditional ALLOW + 空 selectors；capability bridge 为 stub | 未达到历史 prepared grant/scope 的 fail-closed 语义 |
> | Session model params | UI/SessionDB 保存，SDK stack 只消费 provider/model | thinking/fast/effort/context window 丢失；全局 stack refresh 跨 Session |
> | Reasoning mode | official DeepSeek base URL 时硬编码 thinking enabled/high | 非 provider-neutral；DeepSeeker relay、Kimi 等不按 Session 配置 |
> | Usage / cost | SDK invocation ledger 保存 usage；SDK price resolver 返回零价 | Session usage/BillingLedger 读 legacy 状态；辅助 narration 调用未独立建账 |
> | Inspector | legacy 动态 probe + raw preview | 无 snapshot/request lineage、可乱序覆盖，并有敏感预览风险 |
>
> 三层工具事实必须分开：**Provider schema** 与 **SDK executor registration** 当前都是 product
> manifest 的 77 项；**实际 capability/authorization/session-aware execution** 仍有上述 stub、ALLOW
> 和 identity 缺口。不能用“Provider 看到了 77 个工具”推导所有工具语义已接通。
>
> 入口也必须分开：foreground text 走 §2；Voice disabled；Companion/background 走独立 SDK client
> start；control/recovery 只做 lifecycle 操作。它们不得再统称为全部经过 `_execute_sdk_run()`。
>
> **2026-08-21 当时的生产链路（历史）**：
> ```
> chat/chat_v2 foreground text
>   -> _run_product_harness_chat()
>   -> _execute_sdk_run()  (main.py:9513 附近)
>   -> SdkRuntimeIngress.start()  (deskpet/sdk_adapters/ingress.py)
>   -> simple_harness.runtime (SDK v0.1.4 Runtime, vendored wheel)
>   -> DeliveryDispatcher -> _DeliverySink -> WebSocket 前端
> ```
>
> **保留的产品代码**: `backend/deskpet/{tools,skills,sdk_adapters,capabilities,companion}` 等产品特定模块完整保留，通过 SDK adapters 桥接到 SDK Runtime。
> 
> ---
> 
> The SDK Runtime `simple_harness.runtime` is the foreground production execution owner. One local
> owner/inbox may contain multiple UUID conversation Sessions; each Session may contain multiple isolated
> top-level Runs. WebSocket transport id and peer-group identity are routing metadata, not Session or Run
> identity. There is no Code/normal mode split. Every ordinary top-level Run is fixed to `agent.general`;
> the model may choose optional child Profiles through `workflow_spawn`.

> Current observability fact: workflow-node `started_at`/`ended_at`/status/attempt are durable in `workflow_node_attempts`, node `duration_ms` is durable in `trace_spans`, and `deepresearch_stage_timing` is a diagnostic mirror. Backend stdlib and structlog now share one JSON-lines formatter and one resolved user log directory; the rotating text log is diagnostic evidence, not the durable workflow authority. V7 additionally logs privacy-safe child id/attempt/status/reason/source count/duration; page content and full prompts are excluded.

> Historical calibration map: 本段及 §1-§2 是 2026-08-21 历史快照；§3、§4.2/§4.3、§5-§10、
> §15-§18 是历史/目标设计记录，不得用作当前 foreground Context/authorization/model-binding 事实。
> DeepResearch detail lives in [`SEARCH_GATEWAY_DEEPRESEARCH.md`](SEARCH_GATEWAY_DEEPRESEARCH.md).
> §20 是 v0.1.1 时点的历史清理记录；这些版本标签均不代表当前安装版本。当前依赖以页首
> 2026-08-25 基线与 `backend/pyproject.toml` 为准。

## 1. System Shape

simple_harness is a local desktop product, not a generic agent framework. The application now has one
product preparation path, one thin control plane and two available execution algorithms. Driver
selection is not a text classifier.

### 1.1 2026-08-21 历史前台文字架构（SDK v0.1.4）

```text
Tauri shell + React UI
        | chat/chat_v2 WebSocket
Foreground text -> _run_product_harness_chat -> _execute_sdk_run
        | _sdk_ingress (SdkRuntimeIngress)
        | simple_harness.runtime.RuntimePorts (SDK Runtime)
        | host 当时的临时 payload assembler (_assemble_sdk_messages)
simple_harness.runtime.kernel.RunKernel -> fixed root agent.general
        | simple_harness.runtime.drivers.react_loop (SDK ReAct Driver)
        | model may call workflow_spawn(profile_key)
        | durable ProfileLaunchTicket -> child ReAct OR Workflow Driver
        | simple_harness.workflow.WorkflowDriver (SDK Workflow Engine)
ProductToolsAdapter -> 77 registered product schemas/handlers
        | historical ProductAuthorizationAdapter policy was unconditional ALLOW
        | capability search/describe/activate bridge is stubbed
        | simple_harness.execution.EffectExecutor
        | simple_harness.execution.UnitOfWork (SDK UoW on SDK execution DB)
ProductDeliveryAdapter -> SessionDB / WS / TTS / artifacts
```

**代码证据**：
- `backend/main.py:9513` 附近：`async def _execute_sdk_run(...)`（前台文字协调器）
- `backend/main.py:8227`：`await _execute_sdk_run(...)`（当前前台调用点）
- `backend/main.py` `_activate_product_sdk_runtime()`：`_sdk_ingress.open()` 激活 SDK ingress
- `backend/deskpet/sdk_adapters/ingress.py:1-6`: 注释声明 "sole ingress for all product entry points"
- `backend/main.py:6363`: `# Slice C: Use SDK Runtime instead of legacy harness`

### 1.2 旧 harness 遗留代码：已于 2026-08-17 清理完毕

以下旧 harness 模块**已删除**（2026-08-17 清理，~62k 行；pytest 81 failed / 6466 passed，
对比基线 79 failed / 6636 passed，减少项为已删 harness 单测，无新 ImportError）：
- `backend/deskpet/harness/bootstrap.py`
- `backend/deskpet/harness/drivers/react.py`、`react_loop.py`、`react_artifact_completion.py`、`react_recovery.py`、`workflow.py`（即 `drivers/react*.py` 4 个 + workflow.py，与删除提交 `71f3a6e2` 一致，共 11 个模块）
- `backend/deskpet/harness/adapters/product_composition.py`、`product_profiles.py`、`subagent_registry.py`、`team.py`、`legacy_execution_migration.py`
- `main.py` 中 `_build_product_harness_stack()`、`_activate_product_harness()` 与 `_harness_*` 全局变量（已内联 3 行 `root_run_identity` 替代导入）

**保留的 23 个 harness 文件**（非 `__init__.py` 口径，2026-08-19 磁盘核实）：
contracts.py、ports.py、projector.py、context.py、profiles.py、skill_scope.py、kernel.py +
13 个引擎模块（runtime.py、reconciler.py、admission_launch.py、live_index.py、kernel_terminal.py、
attempts.py、child_runs.py、child_signal_runtime.py、execution_profiles.py、router.py、
start_snapshot.py、tool_executor.py、user_continuations.py）+ adapters/venues.py、
adapters/product_turn_open.py、drivers/react_boundary.py。保留原因：`companion/run_adapter.py` →
`venues.py` → `kernel.py` 依赖链，以及 6 个产品文件直接引用 harness 契约类型
（HostExtensionRefV1、HostContext、PreparedRunContextV1 等）。

The SDK Runtime `simple_harness.runtime` is the production execution boundary. `backend/main.py` owns ingress/transport and composition, but not a second execution loop. The executable contract is exported from SDK's public API; the canonical lifecycle and recovery boundaries are defined in [`AGENT_HARNESS.md`](AGENT_HARNESS.md) and SDK documentation.

## 2. Foreground Text Request Lifecycle（2026-08-21 历史 SDK v0.1.4 快照）

**实现状态**: ✅ **已接通**（2026-08-17 ingress cutover 完成 + 2026-08-19 真机 E2E PASS：主聊天经 `_execute_sdk_run` 走通，assistant 回复经 `chat_v2_final` 回推前端）

当时的生产请求路径（历史架构）：

```text
Main Session chat/chat_v2
  -> _run_product_harness_chat()
  -> _execute_sdk_run() ✅ main.py:9513 附近（调用点 :8227）
  -> SdkRuntimeIngress.start() ✅ sdk_adapters/ingress.py
  -> host 当时的临时 payload assembler `_assemble_sdk_messages`
       (public narration system prompt + max 20 ordinary conversation rows + current text)
       ⚠️ ProductContextAdapter/ProductTurnPreparer exist but are not reachable here
  -> simple_harness.runtime.kernel.RunKernel (SDK, top-level profile fixed to agent.general)
       -> simple_harness.runtime.drivers.react_loop (SDK ReAct Driver) -> same parent model
            -> direct answer or ordinary tools via ProductToolsAdapter
            -> workflow_spawn(profile_key, catalog_generation)
                 -> durable one-shot ProfileLaunchTicket
                 -> ChildRun using ticket-bound simple_harness.workflow.WorkflowDriver (SDK)
  -> DeliveryDispatcher ✅
       -> _DeliverySink.deliver() ✅ desktop_runtime.py:141-189（真实路由实现）
       -> ProductDeliveryAdapter ✅ delivery.py（完整实现）
  -> SessionDB + WS + TTS + UI (via product delivery adapter) ✅
```

**代码证据**：
- ✅ `backend/main.py:9513` 附近：`_execute_sdk_run()` 前台文字协调器（调用点 :8227）
- ✅ `backend/main.py` `_activate_product_sdk_runtime()`：`_sdk_ingress.open()` 打开 ingress barrier
- ✅ `backend/deskpet/sdk_adapters/ingress.py`: `SdkRuntimeIngress` 提供 `.start()/.signal()/.cancel()/.query()/.wait_idle()` 方法
- ✅ `backend/deskpet/sdk_adapters/composition.py`: 构建 SDK Runtime 并注入产品 adapters
- ⚠️ `backend/deskpet/sdk_adapters/context.py`: `ProductContextAdapter` 存在，但前台文字生产入口未构造或调用它
- ⚠️ `backend/deskpet/sdk_adapters/tools.py`: 当时 `ProductToolsAdapter` 注册 77 项 schema/handler；
  capability bridge 仍是空 search/describe 与拒绝 activate，且 handler Context identity 不完整
- ✅ `backend/deskpet/sdk_adapters/desktop_runtime.py:141-189`: `_DeliverySink.deliver()` 真实路由实现
- ✅ `backend/deskpet/sdk_adapters/delivery.py`: `ProductDeliveryAdapter` 完整实现

**关键方法签名（SdkRuntimeIngress）**：
```python
async def start(session_id, request_id, turn_id, payload, session_generation) -> IngressStartReceipt
async def signal(run_id, signal_name, payload) -> IngressSignalReceipt
async def cancel(run_id) -> None
def query(run_id) -> RunState
async def wait_idle(run_id) -> None
```

`backend/main.py` owns WebSocket/audio adapters and service composition.
当前 host 临时 assembler 只冻结有限消息与 capability names；完整产品 Context 尚未通过
`ProductContextAdapter`/`ProductTurnPreparer` 进入前台文字 Run。SDK `RunKernel` owns identity, coarse lifecycle
and recovery admission. For a root it resolves the fixed `agent.general` Profile; for a child it
only dereferences a valid launch ticket. SDK ReAct Driver is the dynamic LLM/context/completion
engine. Tool execution is reached through `ProductToolsAdapter`; catalog registration 可达不代表所有
capability/authorization/session-context 语义已接通。`ProductDeliveryAdapter` owns the
projection to WebSocket, SessionDB and TTS.

当前 authorization/tool context 仍有明确缺口：composition 注入 unconditional `ALLOW`、空
resource selectors 与固定宽 grant；SDK ReAct `ToolContext` 不携带完整 metadata，产品 handler 会把
Session/workspace 回退到 request/default workspace，`context_page_in` 更使用固定
`sdk-session/sdk-request/sdk-scope`。历史 `PreparedToolSet + ToolEligibilityContext` fail-closed 设计
尚未迁到这条 SDK foreground 链路。

ReAct and Workflow deliberately remain different execution algorithms. Short
chat does not write every token to the workflow checkpoint store. The fixed
`agent.general` parent can keep an explanation or ordinary tool task in ReAct;
when the model calls `workflow_spawn`, DeepResearch, PPT, capability building or
another registered long-running Profile can retain compiled workflow state,
leases and exact node checkpoints. They share identity, Effects, ChildRun,
lifecycle and presentation through the Harness rather than becoming one
universal graph. User text, `task_type`, legacy Code persona and regex routing
cannot override the model/ticket decision.

Ingress transports still have transport-specific responsibilities, but no
second agent owner:

- The legacy `/ws/audio` Voice path is disabled by default and returns
  `voice_temporarily_disabled`; startup does not load VAD/ASR/TTS. A future
  Realtime ingress must enter the same `ProductTurnPreparer`, `RunKernel` and
  Presenter as Text instead of reviving the old bypass.
- Slash/admin/control commands may perform explicit control-plane actions in
  `main.py`; any new agent execution still starts through RunKernel.
- Direct Tauri shell commands that are not agent work remain outside the
  Harness by design.

The source tree still spans `backend/agent/*` and `backend/deskpet/*`, but the
execution ownership is no longer split: `AgentLoop` is a ReAct Driver engine,
while RunKernel/UoW/Presenter are the shared production authorities.

## 3. Historical Persistence Baseline And Current Timing Owners

This section's original inventory predates the native workflow runtime and is retained to explain the migration. The current workflow fact source is `<user_data>/data/workflow.db`, as calibrated in §13; `state.db` remains the conversation/memory/product projection store rather than the workflow execution owner.

### 3.1 Current DeepResearch timing ownership

| Layer | Current owner | Persisted fields / retention | Current gap |
|---|---|---|---|
| Workflow node attempt | `workflow.db.workflow_node_attempts` | `started_at`, `ended_at`, `status`, `retry_attempt`; terminal workflow data defaults to 30-day retention | `duration_ms` is derived rather than a separate attempt column |
| Workflow node span | `workflow.db.trace_spans` | durable `started_at`, `ended_at`, `duration_ms`, node/status/attributes | no fetch-transport child spans yet |
| Search provider attempt | `search_gateway_attempt` in rotating `metrics.jsonl`; response/state may retain request-local outcome | `run_id`, request/provider/status/duration/count under the metrics privacy whitelist | not a durable trace-level attempt ledger for long-term latency analysis |
| Fetch transport/extractor | `deepresearch_fetch_attempt_timing` in rotating `metrics.jsonl` | `run_id`, stage/status/duration/fetcher/extractor/error/count | best-effort only; throttle/URL-lock/total fetch are not fully separated; millisecond rounding can produce `0` for sub-ms work |
| Diagnostic export | `<user_data>/metrics.jsonl` | max 2 MB; rotation keeps roughly the latest 2000 rows; included in support diagnostics | support bundle does not export rich workflow trace rows, so metrics is not 30-day exact history |

Current v7 must preserve the metrics privacy wall while adding durable, correlated child timing where AC-OBS requires later latency analysis. Telemetry failure must remain non-fatal to workflow execution. The earlier v6 requirement is retained only as historical implementation context in its module plan.

### 3.2 Pre-durable inventory (historical context)

The primary local database is `state.db`, managed by `deskpet.memory.session_db.SessionDB` and `deskpet.memory.schema.initialize_state_db`.

| Data | Current store | Durability | Limitation for workflows |
|---|---|---|---|
| Sessions and messages | `sessions`, `messages`, FTS/optional vec tables | Durable | Records conversation, not executable node state. |
| Code todos | `code_todos` | Durable | Full-list replacement; no node attempt/result/checkpoint model. |
| Code project binding | `code_sessions` | Durable | Identifies project/session only. |
| Awaiting code plan | `session_plans` | Durable record | The actual waiter is an in-memory `Future`; restart reloads the card but cannot resume the suspended coroutine. |
| Goals and goal tasks | `session_goals`, goal task tables | Durable | Completion tracking, not general workflow execution. |
| Team tasks/messages/permissions | Per-team SQLite files | Durable data | Worker coroutines and reclaim logic are process-local. |
| PPT outline history | `ppt_outline_history` in `state.db` | Durable record | Proposal status survives, but the running task and waiter are process-local. |
| Tool receipts | Receipt store | Durable evidence | Can prove side effects, but is not yet keyed to a workflow node attempt. |
| Artifacts | User data/output directories plus message envelopes | Durable files | No generic graph state references or replay policy. |
| Agent iteration trace | `<user_data>/traces/<task>.jsonl` | Append-only file | Flat per-run records, optional, no span tree/checkpoint link. |
| Anonymous metrics | `<user_data>/metrics.jsonl` | Rotating append-only file | Strict whitelist, intentionally too small for rich trace/replay. |
| Context decisions | In-memory `ContextAssembler` ring | Process-local | Context Trace UI loses history after restart. |

Additional stores include facts/workspace/skill memory and feedback tables in `state.db`, `billing.db`, supervisor hints, pending skill candidates, provider bindings, preference/runtime JSON files and permission-auto-mode state. Some are owned by formal migrations, while PPT outline and skill-candidate tables are still created lazily by their business modules. The workflow plan must inventory schema ownership and retention without attempting to merge unrelated product data into graph state.

`SessionDB` already provides WAL, busy timeout, an application write lock, retry with backoff, and idempotent migrations. It is the natural physical store for workflow metadata, but the graph core must depend on a storage protocol rather than directly on `SessionDB`.

The persistence layer currently has a version-contract defect that must be resolved before adding workflow tables: the configured target schema version is ahead of the highest effective migration/test baseline, while several memory-v2 tables are still created lazily by business modules. A single migration owner is required before the durable store becomes authoritative.

## 4. Long-Running Workflows Today

### 4.1 DeepResearch

New research runs are accepted as durable `deep_research/v7`; `backend/deskpet/tools/research_tools.py` provides the focused child/manager collection core while the v7 graph owns production orchestration. The current production chain is:

```text
deterministic ingress -> durable v7 launch
  -> normalize -> plan (2-6 subdirections)
  -> manager schedules one focused research child per direction
  -> classify -> diagnose/continue (max two attempts) -> join
  -> synthesize valid reports + explicit limitations
  -> persist -> generic terminal outbox
  -> SessionDB durable projection + best-effort WebSocket
```

Current UI/delivery boundary:

```text
plan/search -> durable v7 children snapshots keyed by stable child_id
  -> DeepResearch progress card (direction/status/attempt/source count)
  -> internal dr-i.aN scheduler rows filtered from the generic panel

persist -> canonical artifacts[] file envelope
  -> ProductDeliveryAdapter -> one FileArtifactCard
  -> open / save-as / reveal in folder / copy path
history -> old nested file metadata normalized to the same file card
```

The parent-run projection is keyed by stable `child_id`, with retries updating the same direction row. A separate
children sequence lets late child terminal snapshots converge without rolling the parent card back from synth/final.
Canonical file envelopes use the existing `FileArtifactCard`; old nested envelopes remain readable during history
hydration without generating new delivery side effects.

V7 is immutable after release: v1-v6 remain registered for historical reads and recovery and are never coerced into a newer checkpoint schema. V7 owns manager-style decomposition, child monitoring/continuation, citation-validity-based three-state business delivery and a single final report; v6 remains the compatibility path for its existing exact-evidence runs. Search and fetch share the process-wide Search Gateway and FetchExtractService; workflow/node state and traces are durable in `workflow.db`, while low-cardinality diagnostic mirrors remain in `metrics.jsonl`.

Provider health is shared per provider through a closed/open/half-open circuit. V7 can converge to `completed`, `partial`, or `insufficient_evidence`, but its current quality gate only checks report/citation availability and legal footnote references; it does not preserve explicit Top-N cardinality or prove per-item citation coverage. Engine completion is stored separately from business status, while v7 `business_status` currently reaches report/final-assistant envelopes but not the main `workflow.final` progress-card projection, so the card can show completed/100% for a partial report. These are known v7 boundaries and require a new immutable workflow version rather than mutating released v7. When public SERPs are unavailable, a child may nominate bounded direct URLs, but fetched text must pass the same evidence gates. Current implementation and spike evidence are documented in [`DeepResearch.md`](DeepResearch.md), [`SEARCH_GATEWAY_DEEPRESEARCH.md`](SEARCH_GATEWAY_DEEPRESEARCH.md) and [`../plans/2026-07-19-deepresearch-simplification/spike/result.md`](../plans/2026-07-19-deepresearch-simplification/spike/result.md).

### 4.2 PPT Pro

`backend/deskpet/tools/ppt_tools.py::_ppt_pro_orchestrate()` is an explicit but process-local workflow:

```text
DeepResearch
  -> draft outline
  -> user accept/modify/reuse/cancel loop
  -> render PPT
  -> preview/visual review
  -> artifact and receipt reporting
```

`ppt_outline_history` persists proposals. `_PPT_PRO_RUNNING`, `_PPT_PRO_TASKS` and the outline waiter registry hold active tasks/Futures in memory. Restarting the backend cannot continue the original coroutine after an outline decision. Rendering is a side-effecting boundary and must not be repeated blindly after recovery.

The top-level ToolRegistry handler returns `researching` immediately, so its first success receipt describes background-task acceptance rather than final deck delivery. The background path directly calls DeepResearch, LLM outline generation, image generation, rendering and visual review, then manually emits terminal artifacts/receipts. A graph migration must make those two meanings explicit instead of treating one tool receipt as the whole workflow.

The older `ppt_create` image path also starts a background worker and returns `generating` before the file exists. It shares the same acceptance-versus-delivery receipt and cancellation problem even though AC-7 focuses on PPT Pro.

### 4.3 Complex Code Tasks (historical pre-Harness context)

Before the single-main-Session cutover, Code mode used the same `AgentLoop`, augmented by:

- a code persona that asks the LLM to clarify, plan, execute and verify;
- an optional persisted plan-confirm card;
- durable `code_todos` and code-session/project binding;
- `ask_clarification`, permission Futures and UI responses;
- completion probes, VerifyGate, GoalChecker, external evaluator and self-check;
- optional sidecar subagents/teams.

These details explain the migration pressure; they are not a current product mode. New production
records do not select persona, tools, Driver or workspace through Code mode or `task_type`.
Historical todos/session bindings remain readable for compatibility.

Subagent execution has three separate shapes: blocking `agent_parallel`, process-local nonblocking subagent registry/queue, and a team task SQLite store whose workers are still process-local. None currently provides a durable lease and crash reclaim contract for workflow nodes.

## 5. Human-In-The-Loop Today

simple_harness has several real user gates:

| Gate | Durable record | Active waiter | Restart behavior |
|---|---|---|---|
| Tool permission | Audit/receipt around tool execution | In-memory `Future` in permission gate | Pending execution cannot resume exactly. |
| Code plan confirmation | `session_plans.awaiting` | `_PLAN_CONFIRM_WAITERS` Future map in `main.py` | UI card can rehydrate; original coroutine is gone. |
| `ask_clarification` | Conversation/UI event | In-memory pending Future | No durable suspended node. |
| PPT outline confirmation | `ppt_outline_history.status` | In-memory outline Future registry | Proposal survives; workflow continuation does not. |
| Skill candidate confirmation | Pending candidate row | No waiter; a later command resolves by candidate id | Pending data can survive, but proposal delivery is direct WebSocket-only and the main message page does not render the candidate role, so this is not a recoverable main-thread HITL path. |
| Team permission/decision | Per-team SQLite data | Process-local teammate coordination | Data survives; active worker continuation does not. |

The UI interactions are genuine Human-in-the-loop, but the suspension mechanism is not durable. A future graph runtime should turn each gate into a persisted `waiting` node plus an idempotent response command.

## 6. Recovery Today

Recovery is reactive:

- Session messages, goals, todos, plans, artifacts and receipts survive restart.
- `AutoResumeOrchestrator` classifies selected `ErrorEvent` reasons, asks the supervisor for a nudge, then dispatches a new chat run.
- Supervisor follow-up can enqueue another synthetic turn.
- Tool circuit breakers, termination gates and convergence controls stop local failure loops.
- A new message for the same session cancels the previous chat task, but cancellation does not guarantee that threadpool-backed side effects have stopped.

This is useful resilience, but the recovery unit is a chat run. There is no persisted node lease, committed state version, compare-and-swap owner, retry policy per node, or deterministic state merge for parallel branches.

## 7. Observability And Evaluation Today

Current observability is split across several systems:

1. `workflow.db` and SessionDB hold authoritative run/node/effect/event/delivery facts; node attempts and
   trace spans keep status, timing and parent-child identity across restart.
2. `IterationTracer` writes optional JSONL events keyed by task/session.
3. `MetricsSink` writes privacy-safe whitelisted counters to `metrics.jsonl`.
4. `ContextAssembler` keeps a 50-record in-memory decisions ring exposed by `ContextTracePanel`.
5. Prometheus exposes a small set of operational metrics such as LLM TTFT.
6. Individual workflows emit their own coverage, timings, logs, artifacts and receipts.
7. Python stdlib logs and structlog events share one JSON-lines formatter. The active file is
   `<resolved-user-log-dir>/backend.log`, honoring `DESKPET_USER_LOG_DIR`, portable mode and the selected
   user-data directory; it rotates at 20 MiB with five backups. Both logging paths cross one final redaction
   processor: sensitive field names are replaced recursively and credentials/keys/JWT/email/phone/card-like
   values embedded in messages are removed, while correlation fields remain intact. A third-party stdlib
   record and a native structlog record were both parsed as JSON in the current smoke check; redaction and
   observability regression is `12 passed`.
8. Tauri diagnostics collect/reveal the same user-data log directory and use native archive/reveal commands
   on Windows, macOS and Linux.

The durable workflow ledger can answer which node/effect failed, on which attempt, with which terminal status
and artifact/delivery outcome. The redacted JSON log can correlate many live failures by `session_id`, `run_id`,
`request_id`, event and exception type; node handlers now log the original exception instead of leaving only a
collapsed `workflow_node:...:permanent` reason. It still cannot guarantee that every legacy/third-party log line
contains all correlation fields, and the diagnostic bundle does not yet synthesize a one-click run timeline,
invariant report or replay package. The iteration tracer is disabled unless an unconfigured
`[agent].iteration_trace_enabled` key is added; `[context.assembler].trace_enabled` controls a different
feature. Context Trace remains a global process-local ring rather than a persisted, session-filtered trace.

Current quality checks are runtime gates rather than an evaluation platform:

- VerifyGate matches completion claims against receipt evidence.
- GoalChecker uses an LLM judge for active goal completion.
- ExternalEvaluator uses a different model/persona and returns score/issues/pass-or-revise.
- PPT visual review evaluates rendered slides.
- Existing scripts/tests provide domain-specific regression checks.

Remaining platform concepts include a fully common trace/span schema across legacy paths, read-only/forked
replay, evaluator records, datasets and version comparison. Parent-child workflow identity, persisted run/node
indexes and artifact delivery facts now exist for the durable Harness path, but are not yet exposed as one
complete diagnostic product.

There are concrete contract gaps behind that summary: evaluators return incompatible tuple/dataclass/dict
shapes; some evaluator event names and score fields are not accepted by the metrics whitelist; universal secret
redaction and required correlation fields are not enforced at every logging callsite; and the diagnostic bundle
does not include trace/checkpoint/evaluation summaries. These must be unified without weakening the existing
privacy boundary of anonymous metrics.

## 8. External And Platform Dependencies

| Area | Current dependency/boundary |
|---|---|
| Desktop | Tauri/Rust, WebView2(Win)/WKWebView(mac), React/TypeScript，单窗 Workbench（Chat/Skills/Artifacts/Settings）；桌宠 sprite/Canvas2D 与 Live2D 渲染均已退休。 |
| Backend | Python asyncio/FastAPI/WebSocket adapters. |
| Persistence | SQLite/aiosqlite, WAL, optional sqlite-vec. |
| Models/audio | Torch/CUDA, BGE-M3, Silero, faster-whisper, CosyVoice/edge-TTS. |
| LLM/secrets | OpenAI-compatible provider registry (user-supplied baseUrl + apiKey), OS keychain and per-session resolution. 2026-08-09: the hosted-account relay path was removed entirely. |
| Research/browser | Search providers, Scrapling/trafilatura, browser-use/Chromium or system Edge CDP, optional Jina/direct-source adapters. |
| PPT/Office | python-pptx, image generation provider, LibreOffice plus WPS/Office COM rendering on Windows. |
| Extensions | MCP subprocess/client integration and filesystem-based skills. |
| Local execution | AgentLoop-selected ToolRegistry path, plus slash handlers, background workers and Tauri commands. |

The graph core must remain Python-only and UI-agnostic. It must not require a cloud trace service. Tauri/WebSocket, provider, tool and storage concerns enter through adapters.

## 9. Ownership Boundaries For The Upgrade

The following boundaries are stable enough to build on:

```text
workflow core
  definitions / validation / runner / state patches / policies
        |
workflow ports
  checkpoint store / trace store / evaluator / human response / clock
        |
simple_harness adapters
  SessionDB / ToolRegistry / provider / WebSocket events / artifacts / receipts
        |
workflow definitions
  DeepResearch / PPT Pro / Complex Code
```

The current Harness remains responsible for product policy. Workflow adapters must reuse ToolRegistry where a registered tool exists. Internal workflow stages that are not public tools still need equivalent centralized permission, trace, idempotency, artifact and receipt ports; they must not silently preserve today's bypasses.

## 10. Technical Debt And Risks

| Risk | Current evidence | Planning consequence |
|---|---|---|
| `main.py` owns too much orchestration | Provider, WebSocket, plans, permissions, service wiring and persistence meet there. | Add adapters and service registration; avoid placing graph algorithms in `main.py`. |
| Three workflows may fork their own runtimes | Research, PPT and Code already have separate state conventions. | Build and test one generic runtime before workflow migrations. |
| Side effects are not node-addressable | Receipts exist but lack workflow/node attempt identity. | Add idempotency keys and receipt linkage before replay. |
| Durable waiting is split-brain | DB records survive while Futures do not. | Persist waiting commands first; Futures become transport optimizations only. |
| State payloads can become huge | Research passages and rendered images are large. | Checkpoints store references/artifacts for large blobs, not unlimited inline JSON. |
| Parallel state merge can be nondeterministic | Research fan-out and subagents complete in arbitrary order. | Require reducers or conflict detection per state field. |
| Trace privacy is stricter than debug needs | Metrics intentionally rejects rich payloads. | Keep metrics separate; trace uses explicit redaction and local retention policy. |
| Replay can repeat destructive actions | File/Office/system tools have side effects. | Default replay reuses committed outputs; re-execution requires policy and confirmation. |
| Migration blast radius | `state.db` contains messages, memory, code and goals. | Add idempotent migrations, backup/rollback tests and repository-level compatibility tests. |
| Schema ownership is already split | Formal migrations and lazy runtime table creation disagree on the target version. | Repair the migration baseline before adding workflow tables; prohibit new business-owned DDL. |
| Cancellation cannot stop all effects | Cancelling an asyncio task does not terminate already-running threadpool work. | Model `cancel_requested` separately from terminal `cancelled`; reconcile late effect results. |
| Existing task claims have no lease | Goal/team/task state is not a general crash-reclaim coordinator. | Add owner, epoch, expiry and CAS transitions to workflow runs/nodes. |
| Trace correlation is unreliable | Iteration trace is default-off and generated ids do not match the live run. | Create trace/run ids at ingress and pass one context through every adapter. |
| Evaluation telemetry can disappear | Runtime evaluators emit shapes/events outside MetricsSink's closed schema. | Persist normalized evaluation records first; metrics becomes a low-cardinality projection. |
| Multiple ingress venues drift | Text, voice, slash/control and background workers do not share one call sequence. | Route workflows through a venue-independent coordinator and keep venue adapters thin. |
| Tool timeout can outlive its receipt | `wait_for` cannot kill a running threadpool handler; the caller may return before the effect finishes. | Persist effect intent before dispatch and reconcile late completion before retry/replay. |

## 11. Relevant Feature Defaults

| Capability | Current default/source | Baseline meaning |
|---|---|---|
| Plan confirm / agent team | Enabled in `[features]` | UI gates and team tools are active, but waiting/execution is not durable. |
| VerifyGate / ExternalEvaluator | Strict / enabled | Runtime completion gates are active; outcomes are not a unified eval record. |
| Context decisions trace | `[context.assembler].trace_enabled=true` | In-memory Context Trace view. |
| Agent iteration trace | No configured `[agent].iteration_trace_enabled` | Effectively off by default. |
| DeepResearch v7 durable workflow | `[workflows].deep_research_version="v7"` | New runs use the six-node manager/children graph; v1-v6 remain compatibility/recovery definitions. |
| Search Gateway | `[search_gateway].enabled=true` | Shared provider/cache/circuit resources with request-local budget and diagnostics. |
| PPT preview/outline history | Code defaults enabled | Preview files and outline rows exist; active background task/Future remains process-local. |

## 12. Test Boundaries

- Pure graph definitions, state reducers, checkpoint transitions, leases and trace serialization: deterministic pytest.
- Crash/restart, SQLite migration, duplicate response and side-effect replay: integration tests with failure injection.
- Existing Harness adapters and three production workflows: focused regression suites plus live stack tests.
- Trace viewer, durable pause/resume, replay confirmation and human scores: project-mandated windows-mcp true UI testing with screenshots and backend logs.

No protocol-level WebSocket injection may replace real UI evidence for user-visible behavior.

## 13. 2026-07-11 Durable Runtime Calibration

The durable workflow upgrade is now present under `backend/deskpet/workflows/`. `WorkflowLauncher` owns accepted-async background driving and terminal outbox publication; `WorkflowRunner` owns leases, resume and terminal convergence; `WorkflowExecutionObserver` records node executions and structured spans. `workflow.db` is authoritative for runs, node executions, checkpoints, events, deliveries and trace/evaluation data.

The current session projection uses one durable public path:

```text
Workflow node wrapper
  -> WorkflowExecutionObserver
  -> node execution + Trace span persisted

Workflow node wrapper
  -> WorkflowProgressReporter (public-node whitelist)
  -> stable workflow.progress Outbox event
  -> SessionDB workflow_event_id + WebSocket delivery

WorkflowLauncher
  -> workflow.accepted / terminal intents / workflow.final
  -> WorkflowOutbox
  -> SessionDB + WebSocket delivery handlers in backend/main.py
  -> ws.ts reduces structured events into the original session
```

DeepResearch, PPT Pro and Complex Code publish user-safe stage starts through this reporter. The progress `event_key` contains workflow/node/transition/attempt and is scoped by Outbox `run_id`, so a new retry attempt receives a higher durable event sequence while replay of the same attempt remains idempotent. `ws.ts` and hydrated history now feed the same reducer instead of appending each event as a normal assistant message.

AC-23 changes the product projection, not the durable event source: live and hydrated history envelopes feed one `run_id`-keyed reducer, monotonic `seq` rejects replay and out-of-order regression, and one `workflow_progress` component is updated in place. A late history response reduces into the current store rather than replacing a newer live component. `workflow.accepted` creates the component, `workflow.progress` updates its node-level display state, `workflow.final` alone locks completed/failed/cancelled run terminal state, and `workflow.final_assistant` remains a separate answer bubble. Node-level failed/cancelled progress may be superseded by a later higher-seq recovery event. Waiting remains visible until the next public stage starts or the run reaches final. Progress projection never carries raw prompts, tool arguments or file content; unrelated ordinary tool messages keep their existing UI semantics.

Progress may be emitted when a public stage starts. A node wrapper's `succeeded_pending` callback is not a durable completion boundary, so it must not publish “completed” before the following checkpoint/superstep is committed. The next public stage start is the safe user-visible evidence that the previous stage advanced; waiting, failure, cancellation and final states remain explicit. Recovery, manual resume and retry must reconstruct the persisted `delivery` session reference rather than substituting `workflow_runs.session_id`, otherwise the epoch guard in `backend/main.py` correctly discards the event.

PPT outline decisions are a separate product state machine: `modify` validates non-empty feedback, resolves the current decision into `revise_outline`, creates a new revision and a new pending decision/card, and must say “正在修改大纲”; `accept` and `reuse` enter generation; `cancel` terminates without generation. Duplicate responses retain the existing durable decision CAS semantics.

PPT Pro v1 creates stable slide records, checkpoints each generated background, and uses a deterministic deck-level planner plus six Pillow compositors. The planner assigns `cover_band/text_left/text_right/visual_top/floating_card/quote_center`, enforces deck coverage and adjacency constraints, and runs the same text-fit policy used by the compositor before any image effect is committed.

The planner runs before effect identity is computed, aligns each text-free image prompt's negative-space direction with the corresponding Pillow safe region, validates a CJK-capable font, and fails explicitly on unfit copy. Variant, layout-spec, compositor and font-policy versions enter the effect hash. The final boundary remains a 1792x1008 raster page; `python-pptx` inserts exactly that one picture and adds no visible text or decoration shapes.

For AC-21 the image-mode boundary becomes:

```text
confirmed outline deck (AC-22 target)
  -> deterministic layout plan + variant-specific text-free background prompt
  -> exact visible copy + layout variant/spec version in the input hash
  -> one durable image effect per stable slide id + content revision/input hash
  -> 16:9 normalization + variant-aware deterministic text compositor
  -> blank PPT slide with exactly one full-bleed picture
  -> preview + visual evaluation
  -> regenerate only pages named by visual issues
  -> publish artifact
```

Template mode remains available for legacy/compatibility requests. Explicit `full_page_images` is strict: provider failure during initial generation or a visual revision terminates with a user-safe recoverable error and never reports a template deck as a successful full-page result. Full-page image mode intentionally trades away element-level editability; speaker notes and the outer `.pptx` artifact remain.

Visual issue invalidation is explicit: issue page -> stable slide id -> incremented page revision -> revised full-page prompt -> recomputed input hash -> only that record returns to `pending` -> image map generates a new effect. The PPT production adapter validates the generated file and the dedicated assembler owns 16:9 normalization and the `blank slide + exactly one full-bleed picture` invariant; the assembler never probes or calls the image provider.

## 14. Native Workflow Engine（当前）与替换基线（历史）

当前 durable layer 使用 simple_harness 原生引擎。`WorkflowDefinition.bind()` 直接创建 `NativeWorkflowExecutable`；新 checkpoint 使用 `deskpet-native-json-v1`，原生 frontier、条件边/join/reducer、重试、`WorkflowInterrupt`、fenced checkpoint、replay/fork/eval 均已成为生产实现。LangGraph/LangChain 的生产、锁文件和冻结包依赖已移除；旧名称 `FencedAsyncSqliteSaver` 仅作为兼容 import alias 指向 `NativeCheckpointStore`，不是第二个运行时。

> 下表和本节余下文字记录 2026-07-11 切换前的替换设计，用来解释边界由来；其中 LangGraph、StateGraph、`Command(resume=...)` 和 legacy saver 都是**迁移前事实**，不能用于描述当前生产链路。

迁移前的耦合及其已完成的原生替换边界如下：

| Coupling | Pre-migration owner | Native replacement boundary |
|---|---|---|
| Graph scheduling, join and retry | `workflows/definition.py::WorkflowDefinition.bind` | Execute `WorkflowDefinition` directly with a versioned static frontier; support sequential/conditional edges, multi-source join, exclusive barriers, bounded loops/retry and max-step cancellation checks. Dynamic map/Send is not part of the current contract. |
| Execution identity | LangGraph `runtime.execution_info` -> `NodeExecutionIdentity` | Deterministically generate checkpoint/task/attempt/first-attempt identities from run lineage, base checkpoint, invocation key and retry attempt before calling a handler. |
| Node lifecycle SPI | LangGraph node wrapper in `definition.py` | Native wrapper owns observer/progress callbacks, trace span activation, retry classification and `succeeded_pending`; storage remains external but these hooks are an engine contract. |
| Interrupt control flow | `definitions/v1/ppt_pro.py`, `definitions/code_nodes.py` | Raise a simple_harness `WorkflowInterrupt` carrying JSON-safe prompt/id. Native checkpoint commit atomically writes pending state, open decision and run `waiting`; Runner only owns lease/invocation/terminal convergence. |
| Checkpoint commit/projection | `store/checkpointer.py::aput_writes/aput` | Store canonical JSON native snapshots and preserve the two-phase invariant: handler success projects `succeeded_pending`; only the fenced checkpoint/head/ownership transaction promotes it to `succeeded`. |
| Resume/replay/fork | `definition.py::WorkflowExecutable`, `replay.py` | Resume from persisted native frontier; history/fork consume native snapshots. V1 fork remains root-namespace only, rejects pending writes/active fan-out, and requires the existing explicit confirmation gate for dangerous effects. |
| Evaluation adapter | `scripts/workflow_eval_adapter.py` | Use an in-memory/native checkpoint store and simple_harness interrupt metadata; preserve dataset/experiment behavior without framework types. |
| Legacy compatibility | `JsonPlusSerializer` typed checkpoint blobs | Detect by explicit persisted type. An isolated no-pickle reader may expose allowlisted metadata/state for read-only legacy history only; every legacy nonterminal run becomes safely blocked and is never converted or resumed. |
| Packaging and guard | `pyproject.toml`, `uv.lock`, `deskpet-backend.spec`, runtime hook, dependency smoke | Remove LangGraph/LangChain direct and transitive dependencies, hidden-import collection and strict-msgpack hook; replace smoke with a source/import/clean-interpreter guard and record lock/frozen deltas. |

The replacement must not duplicate a general Pregel engine. The three registered workflows require sequential nodes, conditional routes, bounded loops, multi-source joins, exclusive interrupt barriers and stable per-domain map effects. They do not require arbitrary dynamic topology, distributed scheduling or cross-machine consensus. ToolRegistry, permissions, effect ledger, Artifact/Receipt delivery, Trace/Evaluation storage, Session delivery, lease/CAS and retention remain authoritative product services outside the scheduler. Their execution lifecycle adapters do not remain untouched: observer/progress/waiting/failed classification and evaluation execution must be rewired as native engine SPI.

Checkpoint compatibility is a versioned boundary. New rows use `checkpoint_type="deskpet-native-json-v1"` and a canonical snapshot with `engine_kind="deskpet-native"`, `snapshot_version=1`, `state`, `frontier`, `step`, `node_writes`, `interrupt`, `parent_checkpoint_id` and deterministic identity metadata. Completed legacy runs keep their durable run/event/trace/artifact history; an isolated no-pickle reader may decode allowlisted legacy values only for that read-only view. Every legacy nonterminal run is blocked with a safe recovery action and is never converted, resumed, silently restarted or used to execute a side effect.

The engine switch changes implementation identity. Native registrations publish new implementation/engine metadata and do not pretend to match a legacy LangGraph implementation hash. Terminal legacy runs remain queryable. Legacy nonterminal runs are not converted or resumed in this migration: startup deterministically moves them to `blocked` with `legacy_checkpoint_incompatible`, preserves their existing run/session/decision/pending-write rows as read-only evidence, closes no decision and executes no effect. A user retry creates a new native run through normal ingress, so decision nonce/version and Session run identity never cross engine identities. This is deliberately separate from the existing fork saga, whose `prepare_fork()` copies source workflow/version/hashes and therefore cannot be used as an engine migration transaction. Existing native fork restrictions remain fail-closed, with dangerous effects allowed only after the existing explicit confirmation.

## 15. Context OS V1 Runtime（历史目标；当前 foreground 未接入）

> **2026-08-21 校准**：本节记录 2026-07 Context OS 的已实现组件与目标数据流，不是当前
> foreground SDK 请求链。`context_os_v1` 虽仍可配置，但 `_execute_sdk_run` 不读取该开关；
> `context_assembler` 在生产 service context 只注册空 slot。下述 planner/snapshot/attempt-store/
> prepared-tool 断言对当前文字 Run 均不可当作已生效事实。

历史设计中 Context OS V1 由 `context_os_v1=true` 启用；SDK cutover 后该 foreground 接线已经断开。

### 15.1 Owners and request flow

历史目标 flow 是：

```text
text/code/voice ingress
  -> SessionDB append (authoritative transcript)
  -> ContextAssembler
       typed fragments + ToolExposureIntent + task projection inputs
  -> ContextRequestPlanner.prepare_initial
       one catalog/policy resolve
       PreparedToolSet + fixed prefix/current turn/attachments/reserve
       SessionHistoryPlanner coverage plan
       common-safe provider-chain budget
  -> optional CoverageCompactionJob[]
       AgentLoop -> ContextCompressor -> ContextSegmentStore commit
       -> planner replan with the same PreparedToolSet
  -> AgentLoop
       exact prepared messages and logical tool set
       per-provider/model attempt re-budget
       provider transport
  -> ContextAttemptStore / ContextTrace
       planned -> sent -> terminal, matching usage and coverage facts
```

Ownership is deliberately narrow:

| Concern | Authoritative owner |
|---|---|
| Raw conversation and message identity | `SessionDB`; assembler top-k history is never treated as complete Session coverage. |
| Fragment lifecycle and stable-prefix candidates | `ContextAssembler` and typed `ContextFragment` metadata. |
| Initial whole-request plan and common provider-chain safe budget | `ContextRequestPlanner`; protected prefix, current turn, actual tool payload, attachment estimates and generation reserve are charged before history. |
| Lossy summarization | `ContextCompressor` only. The planner may return jobs but does not call an LLM or commit a summary. |
| Reactive execution, compaction ordering and post-compact remount | `AgentLoop`; snapshot flush precedes lossy compaction, and protected/stable fragments, active skills/path rules and the latest complete causal tail are remounted. |
| Provider-specific budget and request lifecycle | The immediate provider-attempt seam plus the public transport marker. Each candidate is re-estimated for its actual provider/model/window and `tools` or `None`; a budget failure cannot send and may continue to the next fallback candidate. |

Provider chains first plan against the candidate with the smallest effective input budget. The order is estimate, reversible history planning, required coverage compaction, replan, then recoverable block. Every actual attempt is checked again, so a provider-specific window or adapter difference cannot reuse an earlier provider's budget result.

Attachments remain provider message content blocks. `attachment_budget` creates body-free `AttachmentRef` records containing only message/content indexes, media type, byte size and estimate metadata; attachment bodies are neither copied into snapshots nor exposed by ContextTrace. Text, code, voice and subagent preparation all pass the resulting refs and token total into the same planner.

### 15.2 Snapshot and coverage persistence

`state.db` migration V18 owns two Context OS tables:

- `session_context_snapshots` stores a derived `TaskContextSnapshot` and a bounded prepared-tool summary. Workflow/goal/receipt/artifact stores remain authoritative; the snapshot is a recoverable projection and cannot promote pending evidence to completed state.
- `session_context_segments` stores raw/summary coverage nodes, continuous message-id ranges, source hashes, child lineage, token estimates and page-in references. `ContextSegmentStore` validates source hashes and commits summaries through CAS-safe operations.

Snapshot writes use distinct DB row revisions and typed `ContextSnapshotHandle`/`SnapshotWriteReceipt` values. Initial projection, activation and provider-attempt adapter updates settle asynchronous CAS outcomes before scope activation or transport; cancellation after a DB commit cannot make an unknown write authoritative.

For one Session, every eligible, undeleted user/assistant/tool message is covered exactly once. If all raw rows fit, all raw rows are loaded. Otherwise `SessionHistoryPlanner` selects a continuous raw tail plus valid summary nodes and page-in references. A reference does not count as content coverage. Gaps, overlaps, stale source hashes, broken tool-call causal groups or residual compaction jobs fail closed before provider transport. `session_history_page_in` is bound to the current runtime Session and pages only at complete causal-group boundaries.

### 15.3 Tool capability plane

`ContextAssembler` produces provider-neutral `ToolExposureIntent`. `ToolCapabilityResolver` reads one strict, session-aware catalog/policy snapshot and freezes an immutable `PreparedToolSet` containing exact direct schemas, bounded deferred references, deny decisions, registry/policy revisions and fingerprints. AgentLoop and diagnostics consume that same set; ON mode does not reduce it to names and reconstruct schemas later.

Deferred capabilities are exposed only through scoped `tool_search`, `tool_describe` and `tool_activate` bridges. Search cannot enumerate another Session or policy-hidden tools. Activation is an exclusive turn: eligibility, current spec hash, policy, budget and snapshot serialization are validated before the candidate revision is committed. Every provider attempt and real tool execution revalidates direct/activated references; MCP unregister/replace or policy drift makes the old reference stale and fails closed. Existing PermissionGate, durable effect authorization, receipts, artifacts, VerifyGate, timeouts and breakers remain downstream authorities.

An authorized capability scope is pinned by the sole `EffectBatchExecutor` while a prepared effect is in flight, then its orphan TTL restarts after settlement. Long-running tools therefore cannot expire their own live scope; an actually missing/expired scope remains fail-closed and is reported separately from a registry-wiring failure. Explicit Chinese `调研` requests, including timeline/chart deliverables, route to the durable DeepResearch profile instead of the synchronous ReAct fallback.

### 15.4 Attempt diagnostics and rollback

`ContextAttemptStore` is a body-free, bounded in-memory diagnostic ring. It records `purpose`, Session/request/attempt identity, provider/model/window, adapter identity, message and tool hashes, fragment decisions, tool-set revisions, attachment/reserve attribution, compression resolution, Session coverage facts and matching authoritative usage. Auxiliary capability-gate/classifier/planner/compressor/research/workflow calls receive explicit purposes; provider bottom seams create an attempt if no complete outer attempt scope exists. The public transport coroutine changes `planned` to `sent` only after it obtains send control; completion, failure and cancellation close the same attempt.

ContextTrace renders these frozen facts, including raw/summary/reference coverage, gaps/overlaps/stale counts and page-in references, without credentials, full tool arguments, attachment bodies or sensitive file content.

Rollback is intentionally one-dimensional:

- `context_os_v1=true` is the shipped default and activates the planner, snapshot/segment stores, capability scope and attempt diagnostics.
- `context_os_v1=false` restores the preserved legacy `tools` names/order, legacy preflight/reactive behavior and legacy context-usage payload. It does not read Context OS snapshots/segments or register an attempt store.
- A startup log states the active owner, resolved window and migration version without prompt content.

Automated verification and benchmark results are recorded in [`../plans/2026-07-13-context-os-v1/results.md`](../plans/2026-07-13-context-os-v1/results.md). Real Windows UI E2E remains a separate evidence boundary and is never inferred from unit tests or protocol-level calls.

## 16. 伴生智能体成长能力现状（Context 入口描述为历史）

本节成长 Store/Router 事实仍供历史追踪，但下述主消息
`ProductTurnPreparer → RunKernel` 入口已被 SDK cutover 绕过；当前 foreground 入口以 §2 为准。

2026-07-25 Task 13 已在既有 Product Harness 上完成长期层的单一生产 authority 切换：

- `<userdata>/data/companion.db` 由唯一 `CompanionStore` 持有；owner-domain 状态按
  `profile_id + profile_generation` 分区，所有权威写使用 `BEGIN IMMEDIATE`、
  `WAL + synchronous=FULL`、stable id/hash、CAS 与 lease epoch。
- `GrowthAuthorityRouter` 是唯一 durable writer 指针。启动在 Product ingress 关闭期间
  完成 `legacy → preparing → companion`，当前恢复为 `companion/generation=5`；
  roll-forward marker 后只能继续 Companion 或进入 `paused`，不能回退 legacy。
- 启动顺序固定为 Capability runtime rehydrate → Companion composition →
  Product Harness（ingress closed）→ authority cutover → notification projection /
  Companion runtime adapter → ingress open。Harness、Kernel、Driver 与 Provider 边界没有
  增加第二套实现。
- `[companion.growth]` 已完成能力默认开启，pause 是独立 kill-switch。
  `PreferenceResolver`、GrowthEvent/terminal contributor、单 scheduler `CompanionRuntime`、
  V2 Reminder 与 durable notification/history projection 已成为生产 Companion 分支。
- `CompanionRuntime.start_prebound()` 先校验 Store 中 exact
  owner/generation/binding epoch，profile coordinator 再冻结 `IdentityReadyGate`；失败会
  暂停 Runtime，不能开放新 Companion Turn。
- Relay owner 只保存 namespaced id hash。可信 bind 会恢复同 owner inbox，或创建新的空
  UUID inbox 并把 `session_id` 返回前端；已有内容的 unowned legacy `default` session
  不能被当前账号认领。
- state.db schema v21 保存 session owner、消息 ingress outbox、excluded projection
  route/outbox 与 redaction receipt；workflow.db v22 保存原子 RunStartSnapshot、真实
  Provider invocation/outcome、Run/effect/delivery fence 与 terminal extension receipt。
- Provider claim-before-transport、handoff unknown、provisional retract 和最后物理 effect
  fence 继续复用通用 `ExecutionWriteLane`、`RunExecutionFencePort` 与 Effect/UoW。
- Task 13 follow-up 用 `GrowthProductionPipeline + ReflectionPostprocessor` 把 committed
  message 的 reflection typed result 串到 candidate build、frozen independent evaluation
  与 activation dispatcher。后台任务使用零工具 prepared context，但仍经同一
  `RunClient → RunKernel → ReActDriver`；Store 按 exact build id claim，失败/unknown 进入
  durable failure/replan 或 fail-closed，不按当前 UI 状态重建。
- 旧 `SkillCodifier/CandidateProposal/ToolPathRecorder`、Presenter/Voice codify 回调、
  裸 candidate confirm 和进程内 Reminder writer 已删除。Skill/Workflow 候选改走
  durable evidence → candidate → independent evaluation → RiskPolicy → activation saga；
  Capability active pointer 仍只由 Manager 与 CapabilityStore binding CAS 修改。
- 历史安装包的 ToolSpec fingerprint 只在当前 spec 能精确重算 v1 时 CAS 迁移；未知漂移
  继续 fail closed。旧 JSON/PendingCandidate 只按 `legacy_local_profile` 幂等导入并作为
  只读升级残迹，不能转给当前 Relay owner；非空旧 Skill inventory 因当前没有可证明的
  immutable pack 发布 receipt，会在不可逆 marker 前零部分导入并可见地阻断。

2026-07-25 真实源码 Tauri Relay mode 在空 owner inbox
`26f5276d-69e9-42b0-ba65-b4a8988b86d6` 完成真人主消息页问答：输入
“请只回复：Task13主消息页通过”，UI 回复“Task13主消息页通过 ✅”并回到空闲，
`chat_v2_final` 使用同一 session id。精确清理 roots `16040/13620` 的 21 个进程，
释放 9749762048 bytes private memory，survivor=0、8100/5173 listener=0。Task 13
自动化门为聚焦 `166 passed`、Companion `476 passed`、Skill/Preference
`127 passed`、Capability `251 passed`、Workflow `53 passed`、前端 `851 passed`，
tsc/manifest/diff check 通过；Task 14 的故障/性能/隐私权限与 deterministic smoke
已经完成。

2026-07-26 真实主消息页又发送两条明确纠正，均形成 durable GrowthEvent 并进入 production
reflection Run；真实 Relay 返回 HTTP 402 `account balance insufficient`，三次尝试后
job 终止为 `failed/background_run_failed`。这证明入口、失败吸收和重试路径，但没有证明
真实 candidate/evaluation/Manager receipt/binding 价值链。Task 15 为 PARTIAL/BLOCKED，
Task 16 只能做不依赖 provider 的文档、worktree、进程和 staged-scope 收尾，不能宣告计划
完成。模块事实与证据见
[`COMPANION_GROWTH.md`](COMPANION_GROWTH.md)、
[`task13-cutover-results.md`](../plans/2026-07-24-human-anchored-companion-growth/evidence/task13-cutover-results.md)。

## 17. 单主 Session 通用行动与可执行能力包

2026-07-24 的生产增量建立在 Harness authority 上，没有增加第二套 agent runtime：

```text
ordinary message -> top-level agent.general / ReAct
  -> model answers or calls prepared tools
  -> model may call workflow_spawn(profile_key)
  -> durable ProfileLaunchTicket -> ticket-bound child Driver
  -> EffectBatchExecutor -> ToolRegistry V2 / managed capability proxy
  -> success, or complete FailureSet -> same parent model -> next Attempt
```

### 17.1 运行、失败与续聊事实源

- `workflow.db` schema v16 持久化 task goal、plan version、attempt/failure set、
  Profile launch ticket、capability revision/binding/operation/runtime lease，以及
  `execution_user_continuations` FIFO。
- 每个 accepted provider action batch 对应一个 Attempt。全部失败阶段都产生绑定真实
  call/effect/child/evidence 的 failure report；下一 Attempt 仍由同一
  `agent.general` 父模型决定策略。
- running-root 继续消息保留原 root/task scope，conversation reservation、FIFO、
  React boundary 和 `pending_resume_signal` 都可重启恢复。
- 一个 `LiveRun.task` 是活动 Driver owner。取消、恢复和 retiring owner 由
  `start_lock + driver_lock + Run CAS` 排序；`CANCEL_REQUESTED` 只向取消收敛，不能触发
  provider relaunch。

### 17.2 授权与外部安全边界

- Manual 以 task/resource/action category 签发可审计 TaskGrant，越界再授权。
- Auto 只省略 simple_harness 的 permission/plan 等待；grant、Receipt、错误、取消和验证不省略。
- UAC、外部登录、OTP 等进入 durable `waiting_external`，不算失败，不创建新 Attempt，
  不绕过系统或第三方安全界面。

### 17.3 能力目录与生成工具

- 能力包有稳定 ID/version/source/compatibility/entrypoint/tools/MCP/permissions/
  dependencies/hashes/uninstall 元数据，版本和 active binding 不原地覆盖。
- local function tool 通过有界 JSON 子进程代理执行；有状态扩展可使用受管 MCP runtime。
  runtime 崩溃、超时和取消不会把未验证代码载入 backend。
- `CapabilityBuilderHost` 只在 search evidence 证明无可执行匹配后 admission；staging
  必须通过 manifest/schema、happy path、错误输入、healthcheck 和副作用路径验证，
  才能原子发布 run/project/user scoped revision。
- catalog refresh 后当前 root 重新冻结 capability/schema/grant fingerprint 并立即调用
  新工具；用户无需重发消息或重启应用。
- builtin Godot `1.0.2` 复用唯一文件/Shell/下载/应用/桌面原语，提供 Godot 探测、
  项目检查、CLI/编辑器运行知识与验证规则，不携带硬编码塔防模板。

### 17.4 2026-07-27 历史校准：单一认知入口与授权资源闭环

> **非当前生产事实**：普通 Text 现不进入 `ProductTurnPreparer.prepare_context()`；SDK composition
> 当前也使用 unconditional ALLOW、空 selectors 与不完整 ToolContext metadata。以下内容保留为
> cutover 前行为/目标契约。

普通 Text 消息当时从 `ProductTurnPreparer.prepare_context()` 直接进入
`prepare_direct_run()` 和 `RunKernel.start()`。`ProductVenueRunAdapter` 不再调用
`route_intent()` 或 `plan_decision()`；旧方法仅供兼容测试/非生产调用保留。因而
Context 较少的 IntentTriage 不能再 short-circuit、单独澄清、插入另一份计划或阻止
主 Agent。Session 历史、记忆、任务快照、冻结工具集与 Profile Catalog 只交给同一个
主 Run；澄清/规划由主 Agent 完成，写操作准入由 prepared tool authorization 完成。
旧 Voice 已安全关闭；后续 Realtime 入口必须先完成同样的产品准备，不能直接调用 Kernel。

Committed 用户消息仍进入 Companion ingress outbox。直接路径不再为了成长优先级运行
前置 IntentTriage，而以 `growth_signal_kind=none` 结算；原始消息仍可由后续 reflection
解释，但成长系统不能取得会话执行 authority。

根 Run 的 failed/cancelled canonical terminal event 通过
`session_terminal/session-transcript-v1` durable delivery 投影为同 Session 的 assistant
摘要。Sink 只接受 root，绑定 Session epoch，以 terminal `event_id` 幂等去重并清理
本地路径/凭据。下一轮 Context 组装前先 read-through；修复前没有 delivery 的旧失败仅在
Run `auth_epoch` 等于当前 Session epoch 时回填，删除/重建后的旧错误不会复活。

授权链保持 fail closed，但空资源契约不再击穿整个 Driver：

- `workflow_spawn` 的 system selector 精确绑定
  `root_run_id + catalog_generation + profile_key`，access 为 `delegate`；
- workspace selector 只能来自可信 `ToolExecutionContext.workspace/write_scope_root`，
  模型参数不能扩大范围；
- Registry 为 Office/PPT/file organize/memory 等实际授权工具冻结确定性 file 或 logical
  selector；全局 `authorization_resource_gaps()` 审计当前为空；
- prepared call 仍需授权但 selector 为空时，ReAct 将其归一为可重规划的
  `authorization_scope_missing` 工具失败，而不是 `driver_failed`。

Deferred capability 的 canonical identity 仍是 `source:name`。完整 ID 精确匹配；裸名称
仅在当前 deferred 集合唯一匹配时规范化，零个或多个匹配继续 `capability_denied`。Describe
nonce 与 activate 仍绑定规范化后的 exact identity。

真实当前源码 Tauri 验收 Session
`e9d345e4-55da-4de7-a33b-8156076ea26d` / Run
`6048fc2701b75ec383550753a758040c` 使用原始中文命令完成
`tool_describe("desktop_create_file") → tool_activate("builtin:desktop_create_file")
→ desktop_create_file`，最终在桌面写出 `春天的散文.txt`，Run 六层均 completed。

详细实现约束和测试证据见 [`AGENT_HARNESS.md`](AGENT_HARNESS.md)。

## 18. 2026-08-03 历史校准：Session 模型一致性与运行可见性缺口

> **2026-08-21 覆盖说明**：Session binding 仍能解析 provider/model，但当前 foreground 只用
> `(provider_id, model)` 刷新一个全局 SDK stack；`model_params`、thinking/fast/context window 未冻结
> 进本 Run。不同 Session 的前台 Run被全局锁串行，background 又不共享该锁。以下“start snapshot
> 已冻结全部模型策略”的表述仅代表 cutover 前设计。

当时主执行链计划把 Session 模型绑定作为 root/child Run 的事实源：
`llm.resolution.resolve_session_provider_chain()` 从
`state.db/code_session_provider` 读取 `provider_id/preferred_model/model_params`，
`main._resolve_agent_provider_chain()` 构造该 Run 的 provider chain，Host start snapshot
再冻结 provider/model launch policy。这个边界能保证已启动 root 及其 child 不因运行中
Provider Registry 变化而换模型。

显式绑定并非当前所有情况下都 fail closed。若绑定的 `provider_id` 已删除或 disabled，
`resolve_session_provider_chain()` 会记录 `session_binding_stale` 后静默回退全局 chain。这个
兼容行为会让“用户明确选择 Kimi”在 provider 失效时改用其他模型，是后续一致性契约必须明确
收口的边界；未显式绑定的 Session 才天然适合使用全局 chain。

但会话附属 LLM 调用尚未进入同一解析边界。`main._make_live_str_llm_call()` 接收一个进程级
provider resolver；FactExtractor、query rewriter、entity extractor、GoalChecker、problem
pre-analysis、memory tools、reflection、curation、PreferenceTurnInterpreter 等当前传入
`lambda: local_llm or cloud_llm`。`ModelPersonalWorkflowMatcher` 还直接持有同一个进程级
resolver。这些调用只有 purpose 或没有完整 attempt 标签，没有 Session/root Run 身份，
所以主 Agent 使用 Kimi 时，会话相关的记忆或偏好处理仍可能调用全局 GLM。真正的跨 Session
维护任务和属于某轮的附属任务目前也没有类型化 policy 区分。这是模型一致性修复的主要结构缺口，
不能靠捕获单个 HTTP 402 补丁解决。

历史上 Context usage 曾从全局 `effective_llm_model(config)` 生成 `0/window` stub，并可能混合不同
sample；该缺陷已由 SessionDB `ContextUsageStateV2` reducer 修复。当前无测量时只从 Session binding
构造明确的 `binding_only` state，不再伪造全局 measured-zero。尚未修复的是：真实 SDK
`provider_invocations.usage_json` 没有桥接到这份 V2 authority，终态采样仍探测 legacy
`ContextAttemptStore/provider.last_usage`，所以状态会长期停在 binding-only 或误读旧 legacy sample。

Harness Inspector 的当前生产 authority 是 V3：
`HarnessInspectorPanel -> harness_inspector_snapshot_request -> HarnessPublicReadService -> PublicRunSnapshotV3`。
旧 `harness_inspector_snapshot -> SqliteExecutionUnitOfWork.inspect_harness_run()` endpoint 仅服务
历史客户端，不得作为新时间线或 Inspector 功能的接线入口。

V2 的原始事实源仍然正确且只读：
`SqliteExecutionUnitOfWork.inspect_harness_run()` 从 start snapshot、lineage、provider invocation、
tool/effect 和 canonical events 生成只读 schema v2 snapshot。当前前端
`buildHarnessActivityFeed()` 把准备、root/child、每次 provider invocation、每次 tool call 和
关键 canonical event 展开成逐条记录；`buildFriendlyGraphNodes()` 除合并相邻 prepare 记录外，
基本一条记录对应一个图节点。长任务因而会出现上百个“决定工具→执行工具→继续处理”节点，
它是审计时间线的轻量翻译，不是真正的语义阶段图。

这个 snapshot 目前还有静默截断：lineage/provider/batch/attempt 各 `LIMIT 200`，tool/effect/
failure/event 各 `LIMIT 400`，durable workflow head 各 `LIMIT 100`，响应没有 cursor、total 或
`truncated` 标记。超长任务进入前端前就可能缺少早期因果，任何语义阶段投影都不能在未知完整性
时宣称覆盖了全部步骤。

右侧 durable workflow 消息已有另一条较紧凑的投影：`workflow_plans + workflow_effects +
public_messages` 通过稳定 `workflow_step_id/call_id` 绑定为可折叠步骤和工具详情。它当前直接把
durable `prepared_json/outcome_json` 解码后交给 UI；Provider input 有单独的脱敏投影，但工具
参数/结果没有同等级的字段级 public projection，不能假定现状已经满足公开展示安全边界。
后续修复应复用稳定身份，新增有界、字段级脱敏的 tool public projection，并建立独立、可重建
的 root-level semantic phase projection；原始
activity feed 继续保留在步骤详情/技术记录中。子 Run 的真实 failed/cancelled 终态不得改写；
当 root 后续接管并完成时，只新增根级聚合结果（例如用户语义上的“已接管并完成”）。当前代码
还没有这种 recovered-child aggregate，顶层完成与 child failed 只能并列显示，容易被理解为
整个任务失败。聚合不能只看 `child failed + root completed`；它必须使用 child terminal signal、
`TaskFailureReport.child_run_id`、Attempt 的 `trigger_failure_set_id/supersedes_attempt_id` 与后续
root terminal 建立可证明的恢复因果，并定义 waiting/cancelled/failed/completed 的稳定优先级。

## 19. 2026-08-03 实施结果：冻结模型 authority 与 public semantic read model

第 18 节记录的缺口已由 Session 模型与运行可见性切片收口。Provider Registry entry 现在有
durable incarnation/config revision，Session binding 有 epoch CAS；删除后同 ID 重建、双窗口旧
请求和 stale model catalog 都稳定冲突或 fail closed。产品启动必须先完成 state.db v23～v26
migration、Registry durable load、binding reconcile marker，再开放 `ProviderRoutingReadiness`。
Root 已启动后只读取 Host start snapshot 的冻结 provider plan；Session 后续换模型只影响下一
Root。

主调用与 FactExtractor、query rewriter、entity extractor、GoalChecker、problem pipeline、
preference/personal workflow、memory tools 等附属调用共用 `ProviderWorkloadRouter`。调用方必须
声明 workload class/callsite/purpose/session/root/request/detached；会话附属缺身份或绑定不可用时
不再走全局 chain，跨 Session 维护必须使用显式 `BackgroundModelPolicy`。附属 401/402/429/5xx
按真实失效域 breaker 隔离并写低敏 audit，不拥有 Root terminal authority。

DEV 故障验收使用严格、consume-once 的 `ProviderFaultScriptV1`，注入点位于 durable provider
claim 之后、物理 transport 之前。Session auxiliary 绑定 Root，child main 绑定 child-run
correlation 且不冒充 Root，detached maintenance 只绑定 request correlation 并保持 session/root
为 NULL；audit 持久化低敏 correlation hash。生产模式发现该脚本环境变量会拒绝启动。

Context Usage v24 使用 immutable sample + materialized state。恢复选择单个完整 measured/compacted
sample；没有样本时只由 Session binding 生成 binding-only 状态和“尚无用量”，不会把全局默认
模型伪装成 Session 测量值。

运行观察不再由前端逐事件归并。`HarnessPublicReadService` 从 workflow/state 两个一致 read cut
做全量 keyset 读取，返回带 totals、HMAC cursor、completeness 和 diagnostics 的 immutable manifest；
`semantic_projection` 在完整公开事实上构造稳定 DAG，将事实归为最多七类且只显示实际出现的
阶段。`RootOutcomeView` 仅在 child terminal、FailureReport、failure set、replacement Attempt 和
后续 root terminal 完整时显示 `completed_with_recovery`；blocked 只能来自结构化
`RunBlockSignalV1`。真实历史 Godot Root 的 366 个 public facts 当前得到 6 个阶段、29 个唯一
逻辑工具（23 个 shell）、`projection_complete=true` 和 recovered aggregate，child failed 事实仍
保留。

公开工具与 Provider 详情均使用 Root 冻结 policy 的 default-deny、有界、字段级脱敏投影；raw
prepared/outcome/provider input/output 不进入 schema v3。前端三处消费者共用按 Session/root 索引的
`HarnessPublicSnapshotStore`，响应驱动 singleflight 轮询在完整 terminal 后停止；details 分页与
snapshot 刷新分槽，切换/取消和晚响应不串 Root。

停止后的工具完成由显式三阶段 handoff 收口。`StartedAck` 返回前即 arm completion observation；
consumer 取消会重新读取最新 durable effect version，将仍可能完成的 effect 收敛到
`unknown/started_may_complete`，原始 `CancelledError` 不会被 CAS 冲突遮蔽。真实 handler 完成后
process-local late evidence 在 durable terminal 决策前只 peek、不出 ready index；terminal Run 的
Reconciler 将其结算为 `late_reconciled/reconciled/reconciled_completed_suppressed` 后才 acknowledge，
不会恢复 Driver。running 窗口与 CAS loser 都保留重试能力。

## 20. 2026-08-17 SDK v0.1.1 Post-Cutover 代码清理基线

### 20.1 当前状态（SDK 已接入，旧代码待清理）

> **⚠️ 本节为 2026-08-17 清理执行前的历史记录**：§20.1/§20.2 描述的"待清理/待删除"工作已于
> 2026-08-17 全部执行完毕（见 §1.2 当前事实与 PROJECT_STATUS 2026-08-17 里程碑）；其中
> `main.py:9544`/`main.py:9949` 等行号证据已失效，runtime.py/reconciler.py 等 13 个引擎模块
> 实际被**保留**（非删除）。保留本节仅作清理决策的历史依据。

**SDK v0.1.1 已是唯一生产执行 authority**：

- 启动链：`main.py:_activate_product_sdk_runtime()` → `_build_product_sdk_runtime_stack()` → SDK Runtime
- 执行核心：`simple_harness.runtime.kernel.RunKernel` + `simple_harness.runtime.drivers.react_loop` + `simple_harness.workflow`
- 数据库：SDK 管理的 `<user-data>/data/simple-harness-sdk/execution-v1.sqlite3`
- Ingress：所有入口（text/voice/background）路由到 `_sdk_ingress.open_venue()`（`backend/main.py:9544`）
- 产品适配器：`backend/deskpet/sdk_adapters/*` 桥接产品服务到 SDK ports

**代码证据**：
- `backend/main.py:6363`: `# Slice C: Use SDK Runtime instead of legacy harness`
- `backend/main.py:9544`: `outcome = await _sdk_ingress.open_venue(...)`（真实调用）
- `backend/main.py:9949`: `_sdk_ingress.open()`（SDK ingress 被激活）
- `backend/deskpet/sdk_adapters/ingress.py:1-6`: 注释声明 “sole ingress for all product entry points”

**旧 harness 代码仍存在但未使用**：

虽然 SDK 已接管所有执行，但以下模块仍存在于源码树且 `_build_product_harness_stack()` 仍被调用：
- `backend/deskpet/harness/kernel.py`, `bootstrap.py`, `runtime.py`
- `backend/deskpet/harness/drivers/react*.py`, `workflow.py`
- `backend/deskpet/harness/reconciler.py`, `admission_launch.py`, `live_index.py` 等
- `_harness_runtime`, `_harness_venue`, `_harness_accepting` 全局变量

**关键事实**：`_harness_venue` 被构建但从未被任何 ingress 调用（全仓搜索 `await _harness_venue` 返回 0 结果）。

### 20.2 清理目标（删除死代码）

**待删除的模块**（已被 SDK 完全替代，但仍有遗留引用需清理）：
- `backend/deskpet/harness/kernel.py` → SDK `simple_harness.runtime.kernel`
- `backend/deskpet/harness/bootstrap.py` → SDK runtime 初始化
- `backend/deskpet/harness/runtime.py` → SDK runtime infrastructure
- `backend/deskpet/harness/drivers/react*.py` → SDK `simple_harness.runtime.drivers.react_loop`
- `backend/deskpet/harness/drivers/workflow.py` → SDK `simple_harness.workflow`
- `backend/deskpet/harness/reconciler.py`, `admission_launch.py`, `live_index.py`, `kernel_terminal.py` 等

**清理前必须处理的引用**：
- `main.py` 中 3 处 `deskpet.harness.kernel.root_run_identity` 导入（需确认 SDK 提供等价功能或内联实现）
- `main.py` 中 `_build_product_harness_stack()` 调用及其依赖链
- `deskpet.harness.adapters.product_composition` 及其对旧 drivers 的导入
- 17+ 个测试文件的 harness 模块导入（需评估是否迁移到 SDK 或删除相应测试）

**待删除的代码**（未使用的构建逻辑）：
- `main.py` 中的 `_harness_runtime`, `_harness_venue`, `_harness_accepting` 全局变量
- `_build_product_harness_stack()` 函数及其调用
- `deskpet.harness.adapters.product_composition` 及相关旧 adapter 代码

**保留的模块**（产品特定逻辑，通过 SDK adapters 工作）：
- `backend/deskpet/sdk_adapters/*` - 产品到 SDK 的适配器层（**必须保留**）
- `backend/deskpet/tools/*` - 产品特定工具实现
- `backend/deskpet/skills/*` - 产品特定技能
- `backend/deskpet/capabilities/*` - 产品能力系统
- `backend/deskpet/companion/*` - 产品陪伴成长系统
- `backend/deskpet/memory/*` - 产品记忆系统
- `backend/deskpet/session/*` - 产品会话管理
- `backend/deskpet/execution/*` - 执行相关契约（部分可能被 SDK 使用）

**保留的 harness 模块**（被测试或产品其他部分引用）：
- `backend/deskpet/harness/contracts.py` - 如果被 SDK adapters 或测试引用
- `backend/deskpet/harness/ports.py` - 如果被 SDK adapters 或测试引用
- 其他被测试套件或产品代码实际导入的模块

清理前需通过 grep 确认每个模块的实际引用情况，避免误删仍被使用的代码。

### 20.3 风险与验证

**主要风险**：
1. 误删被测试套件引用的 harness 模块 → 通过 grep 和测试套件验证
2. 误删被产品其他部分隐式依赖的代码 → 通过全仓导入分析
3. 遗漏清理某些旧代码引用 → 通过测试套件和启动验证
4. **测试套件依赖**：17+ 个测试文件导入旧 harness 模块（`test_run_kernel.py`, `test_harness_bootstrap.py`, `test_workflow_driver.py` 等），删除后这些测试会失败，需评估迁移到 SDK 测试或删除

**验证策略**：
- 所有删除前先 grep 确认引用情况
- 处理 `main.py` 中的 3 处 `root_run_identity` 导入（确认 SDK 等价功能或内联实现）
- 评估测试策略：迁移到 SDK 测试 vs 删除旧 harness 测试
- 删除后运行完整 pytest 套件
- 删除后手工测试所有 ingress（text/voice/background）
- 确认应用能正常启动并处理用户请求

**回退策略**：
- 所有改动可通过 `git revert` 立即回退
- 不涉及数据迁移，数据完整性不受影响
- 不影响 SDK Runtime 或产品 adapters

详细的依赖图、边界、证据和切换历史见 [`SDK_EXTRACTION.md`](SDK_EXTRACTION.md)。
