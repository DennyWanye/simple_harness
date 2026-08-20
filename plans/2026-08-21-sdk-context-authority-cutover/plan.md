<!-- plan-status: finalized -->
# Plan：SDK Context 单一事实源、真实用量账本与旧链路退役

> 日期：2026-08-21  
> 状态：Phase 1 调研完成，Phase 2 待 challenge / spike / 用户确认；尚未执行业务代码  
> 关联验收：本目录 `acceptance.md`（从仓库根已批准 AC-10～AC-17 独立成 release unit）、TO-A10～TO-A17、TO-R7～TO-R11  
> 关联调研：`../2026-08-21-provider-reasoning-capabilities/00-RESEARCH-AND-DESIGN.md`

## 1. 主要矛盾

当前 foreground SDK Run、Context Inspector、Context usage 三者没有共享同一个事实源：

- foreground text 入口虽然构造了 `TurnInput`，实际却由 `backend/main.py::_assemble_sdk_messages`
  重新拼接“公开叙述 system prompt + 最多 20 条 user/assistant 历史 + 当前输入”；
- `ProductTurnPreparationService` / `ProductTurnPreparer` / `ProductContextAdapter` 没有进入当前生产文本链，
  Persona、真实 Memory recall、Skill、附件、项目/任务快照、预算/压缩均未进入实际首轮 Provider 请求；
- Context Inspector 点击后又动态读取 legacy Persona/facts/V2 registry/history，并以 `tool_count * 60`
  估算工具，既可能与真实请求不同，也会把敏感预览直送前端；
- SDK provider invocation 已持久化真实 request/response/usage，但 Session Context usage 仍读取另一条 legacy
  provider 的 `last_usage`，所以 UI 显示 `(no model yet)`、`0 / 0` 或旧模型数据；
- SDK runtime 绑定全局 provider/model，切换会话时 refresh 整个 stack，导致不同 Session 串行、运行中
  refresh 关闭活跃 stack、后台 Run 使用错误 binding 的风险；
- 会话已持久化的 Thinking/Fast/effort 没进入 SDK Provider adapter；当前 DeepSeek 行为按 base URL
  猜测并固定 `thinking=enabled + reasoning_effort=high`。

因此本次不是修一个 Context 弹窗，而是建立一个 **per-Run immutable prepared snapshot**：同一快照同时
驱动 RunStart、首轮 Provider 请求、tool catalog、Provider binding、Context Inspector 与用量 lineage；入口、
Inspector 和 legacy presenter 不再各自重算。

## 2. 当前生产链路与缺陷证据

### 2.1 真实可达链

```text
WS chat/chat_v2
  -> _run_product_harness_chat (构造 TurnInput，但随后未消费)
  -> _execute_sdk_run
  -> _assemble_sdk_messages / _sdk_capability_snapshot
  -> SdkRuntimeIngress.start(RunStart)
  -> SDK ReActDriver
  -> ProviderRequest(context.messages, executor.provider_tool_specs(names))
  -> ProductProviderInvocationCoordinator
  -> ProductProviderAdapter
```

SDK v0.1.4 的 `ReActDriver` 从 `RunStart.input.messages` 生成初始 Context，并从
`RunStart.input.capability_snapshot.tools` 向执行 registry 取 ProviderToolSpec；ReAct 后续每轮使用同一 SDK
Context。也就是说，只要 RunStart 输入由唯一准备 authority 冻结，就能让 Provider 与 executor 共用同一
catalog；当前缺陷来自 App 在 ingress 前另组 payload，而非 SDK driver 自己重组。

### 2.2 旧/旁路清单

| 链路 | 生产状态 | 问题 | 处置 |
|---|---|---|---|
| `_assemble_sdk_messages` | foreground 可达 | 绕过 Context authority、最多 20 条、异常静默退化 | 删除生产调用；只保留迁移期测试 helper 或直接删除 |
| `_sdk_capability_snapshot` | foreground 可达 | 只有静态工具名，没有 generation/schema fingerprint | 改由冻结 catalog snapshot 输出 |
| `_compute_context_breakdown` | UI 可达 | 动态 probe legacy sources、估算 schema、敏感预览 | 替换为 durable public snapshot query |
| `_snapshot_context_usage_event` / `_emit_context_usage` | SDK 终态可达 | 读取 legacy provider/attempt，可能空、旧、错 model | SDK 路径退役；由每次 invocation settlement 投影 |
| `RunPresenter.finish_turn` 的 legacy provider usage/billing | SDK 完成可达 | 不是实际 SDK provider | SDK delivery finish 不再交给该 usage authority |
| `_refresh_sdk_runtime_for_binding` | 每个 Run/provider 变更可达 | 全局 stack swap，关闭活跃 Run，跨 Session 漂移 | stable runtime + per-Run provider binding router |
| `_PUBLIC_PROGRESS_SUMMARY_PROMPT` 二次 delegate 调用 | tool turn 可达 | 未独立 durable attempt，usage 合并、用户看到兜底 | 删除二次 LLM 调用；只用正常 content/progress 或诚实状态 |
| `ProductTurnPreparationService` 等旧产品 preparer | foreground 不可达 | 看似 authority，实际 dormant；其中 Memory component 仍 stub | 不直接复活；提取可复用纯组件，建立 SDK-specific authority |
| static `sdk-session/sdk-request/sdk-scope` tool context | tool 可达 | 多 Session identity 污染 | 从 per-Run binding 注入真实身份 |
| allow-all SDK authorization / zero hashes | tool 可达 | 权限与 capability 身份不可信 | 接入当前产品 permission/grant authority，fail closed |
| minimal capability bridge | tool 可达 | search/describe 空、activate 报错 | 接入当前 `ToolCapabilityBridgeService` |

## 3. 目标架构

### 3.1 `PreparedSdkContextSnapshotV1`

新增产品拥有的冻结契约（命名可在实现时微调，但语义不可拆散）：

```text
snapshot_id / snapshot_version / snapshot_fingerprint
session_id / request_id / root_run_id / sdk_run_id / turn_id
owner/profile identity + conversation boundary/version
provider_binding:
  provider_id / incarnation / config_revision / binding_epoch
  model_id / model_params / reasoning profile / context_window
provider_messages: exact ordered messages/content blocks for RunStart
catalog:
  generation / content fingerprint
  ordered tool names / per-tool schema fingerprint
attachments: exact structured private blocks + bounded public refs + body retention policy
persona/memory/skills/history/project/task: frozen inputs + public counts/token estimates
budget: estimated prompt tokens / effective ceiling / compact_at / truncation decisions
public_breakdown: default-deny, bounded, redacted Inspector projection
```

约束：

1. 同一 fresh Run 只调用一次 `prepare()`；失败必须显式失败或生成带 unavailable reason 的可审计降级，
   不得静默丢掉某一部分继续执行。
2. exact provider messages/content blocks 进入 SDK RunStart，并由 SDK StartSnapshot/provider request ledger 持久化；
   SessionDB 只保存 Inspector 所需的 public projection，不复制完整敏感正文。
3. `snapshot_fingerprint` 使用 canonical JSON + SHA-256；所有动态数据先冻结再计算。
4. fresh Run history 只接受 `context_visibility=conversation` 且 role/projection allowlist；公开思考摘要、
   tool/artifact、耗时、隐藏 reasoning 永不进入新 Run。
5. 同一 SDK tool loop 的私有 `reasoning_content` 仅按 Provider 协议保存在 SDK message metadata 并回传，
   不进入 public snapshot、SessionDB conversation、日志或 Inspector。

### 3.2 单一准备服务

新增 `SdkContextPreparationService.prepare(TurnInput, FrozenRunBinding)`，从当前生产 authority 读取：

- Persona：当前 owner/profile 的产品 persona authority；
- Memory：SessionDB/current memory SDK 的一次 scoped recall；不得使用旧 active-facts 列表代替 recall；
- Skills：当前 managed skill selection/projection；
- History：SessionDB conversation allowlist + token budget，不写死 20 条；
- Current input 与 attachments：正文进入 exact structured content blocks，公开快照只存类型、数量、尺寸、
  脱敏名称/ref。SDK v0.1.4 的 `Message.content` 仅支持 `str`，所以 Slice A 必须先在
  `../simple-harness-sdk` 扩展 typed `MessageContent = str | tuple[ContentBlock, ...]`、canonical JSON、
  StartSnapshot、ReAct driver 与 OpenAI-compatible serializer，并构建/校验 vendored v0.1.5 wheel；不得用
  `str(list)` 或只传 attachment ref 假装已把附件交给 Provider。默认 hard bounds：单 block 8 MiB、单 Run
  16 MiB，超限在 Provider dispatch 前给用户明确错误；精确 body 仅持久化在本机 SDK execution store，
  生命周期随 Session/Run 数据删除，diagnostic bundle/public snapshot/log 全部排除；
- Project/task/workspace：当前 workflow/project identity；
- Catalog：本次 runtime build 的真实 `tool_inventory`、generation、schema fingerprints；
- Provider：当前 Session binding + registry incarnation/config revision + model params/capability profile；
- Budget/compaction：以该模型 context window 和 exact message/tool schema 估算，不用 `count * 60`。

旧 `ProductTurnPreparer` 中只有已证明仍由当前 authority 支持、且能以纯函数/port 复用的部分可提取；不得
恢复 `main.py.bak2` 或重新启用已移除的 Context OS singleton。

### 3.3 Stable runtime + per-Run binding

不再按 `(provider, model)` 重建全局 SDK stack。保留一个 stable SDK runtime/catalog generation，新增
`SdkRunBindingRegistry`：

- 在 `ingress.start()` 前注册 immutable Run binding；canonical terminal 才清 durable binding；live cache 可在
  WAITING 释放，但 durable snapshot/incarnation/catalog lease 必须支持 restart/recovery 重建；
- Provider coordinator 以 `run_id` 选择冻结的 `ProductProviderAdapter` 与 optional estimator；
- 一个 Session 的设置变化只影响未来 Run，不改变已启动 Run；
- Provider 配置更新/删除先拒绝会破坏 active binding 的 destructive mutation，或保留被引用 incarnation
  到所有 Run terminal，不能 close 当前 runtime/client；
- foreground/background 使用同一 binding/lease 规则，可并发，不再依赖覆盖整次 Run 的全局 lock。

SDK `ProviderInvocationCoordinator` 会在 claim 与 charge 多次读取 `self._provider.target`，并在构造时把
唯一 `FrozenPriceEstimator` 绑定到该 target 的 `pricing_key`，ReAct driver 还冻结全局 budget fingerprint。
SP-1 已证明仅用 ContextVar provider proxy 无法正确携带不同 Run 的 estimator/budget。因此 SDK v0.1.5
必须采用正式的原子 `resolve(run_id) -> (provider, optional estimator, budget policy/fingerprint)` port，
或由产品 coordinator
完整拥有“resolve → claim → invoke → settle”的同等原子边界；禁止用进程全局可变 target 或只切 Provider
不切 estimator 的代理方案。RunStart 冻结本 Run budget fingerprint，resume/recovery 核对同一值；未知
pricing 使用 `estimator=None/charge unavailable`，不得以 0 价格伪装。

### 3.4 Immutable versioned catalog resolver

SDK v0.1.5 增加按 generation/content fingerprint 解析的 immutable catalog lease：

- product build 把完整 canonical tool specs（含协议字段）写入 SDK execution DB 的
  `tool_catalog_snapshots(generation, content_fingerprint, specs_json)`；相同 fingerprint 幂等复用 generation，
  不同内容单调分配新 generation；
- RunStart 冻结 generation + catalog fingerprint；Provider spec 与 executor 都从该 generation 解析，不从
  当前全局 registry 重新取；
- catalog 更新产生新 generation，旧 generation 在所有引用它的 running/waiting Run terminal 前保留，
  重启后仍可解析；缺失时明确 `catalog_snapshot_unavailable`，不得改用最新；
- `deskpet_public_progress` 成为 canonical tool schema 的正式 **optional string property**，不得加入
  `required`。ProductToolsAdapter 在业务参数 schema 校验前对该保留字段做 tolerant protocol normalization：
  非空 string 才捕获 narration，missing/blank/wrong-type 都视为“无公开进度”并剥离，业务工具继续执行，
  UI 投影一次诚实状态。Provider/executor/Inspector 的完整 schema/fingerprint 相同，删除 Provider adapter
  的临时 `_with_public_progress_fields` wire mutation。

### 3.5 Provider capability / Thinking mapping

Run binding 内部保存 provider-neutral 语义：

```text
reasoning_mode = default | thinking | fast
reasoning_effort = default | low | high | max
```

由 `(provider profile, model id, declared capabilities)` 映射 wire fields：

- DeepSeek V4：`thinking.type=enabled/disabled`；effort 按官方支持值映射；
- Kimi always-thinking：不发送不支持的 `thinking`；Fast 若语义只能 lower-effort，UI 必须明确显示，不能
  冒充“关闭思考”；
- Kimi toggleable 模型：按模型 profile 映射；
- unknown OpenAI-compatible：不猜测、不按 base URL；仅发送配置声明支持的字段；
- `thinking.enabled` 只表示请求能力，不保证一定返回非空 `reasoning_content`；缺失时投影一次诚实状态，
  不生成固定“模型思考”兜底。

会话模型窗口是 Session/Run authority；Provider 设置页只管理 endpoint/default model。context window 的
选择改成 Session binding 参数，不再写 global model override。

### 3.6 每个 physical invocation 的真实账本

每个 SDK Provider attempt（成功、明确失败、unknown、cancel after handoff）都必须有 canonical durable
identity，并关联：

```text
invocation_id / sdk_run_id / root_run_id / session_id / request_id
snapshot_id / provider binding epoch / provider target / response model
request fingerprint / state / started_at / completed_at
usage availability / input / output / total / cache / reasoning（若协议有且可信）
charge availability / error code
```

- SDK v0.1.5 扩展 `ProviderUsage`、OpenAI-compatible parser、response JSON 与 invocation ledger，使
  `cache_tokens/reasoning_tokens` 为 nullable；Provider 不返回时是 unavailable，不是实测 0。
- SDK provider invocation ledger 是物理调用 authority。新增 durable projection outbox/cursor/reconciler：
  SDK settlement 同事务写 `provider_projection_outbox(invocation_id, settlement_version, payload_hash)`；SDK
  提供按 `(settled_at, invocation_id)` 稳定游标读取，SessionDB 保存 consumer cursor，并以 invocation id
  幂等落 attempt；启动、周期 reconcile 和即时 best-effort delivery 都从 ledger/cursor 补齐，覆盖“两库
  之间进程崩溃”。cursor 只能在 SessionDB attempt/sample 事务提交后推进；
- 有可信 usage 的 succeeded attempt 再以 `source_event_id=sdk-provider-invocation:<id>` 调现有
  `record_context_usage_sample`，利用其幂等与 conflict fence 更新 ContextUsageStateV2。
- usage 缺失不伪造 0；仍保留 attempt + `usage_available=false`。失败/unknown 也不制造 measurement。
- 本增量不修改价格或外部计费契约。SDK 不再固定 `(0,0)` 冒充真实价格；当前没有 versioned pricing
  authority 时使用 estimator/charge unavailable。旧 BillingLedger 不接收 SDK 重放，UI 不显示虚假 0 成本。
- 删除 public narration 的第二次 delegate Provider 调用。公开文字优先使用同一正常 response 的
  assistant content / `deskpet_public_progress`；两者都没有时显示工具活动或一次诚实状态。

### 3.7 Context Inspector 只读投影

`context_breakdown_request` 必须带 `session_id + request_id`，后端校验 websocket owner/peer group 和当前
Session；响应带 `snapshot_id + snapshot_version + correlation_id`。

Inspector：

- active Run：读取该 Run 已冻结 public breakdown；
- idle Session：读取最近一个成功 prepared snapshot；没有快照时显示“尚无真实模型请求”，允许单独显示
  当前 Session binding，但不能把估算冒充 LLM 实测；
- 不再触发 Memory/persona/history/catalog 动态读取；
- 默认只显示 section label/count/token/availability/脱敏 ref。任何 preview 都经过 allowlist redactor、
  长度上限和 secret/header/path canary；不得显示 Authorization、API key、cookie、原始 attachment body、
  私有 reasoning、完整用户历史；
- 前端只接受当前 correlation/session 的响应；版本倒退、equal-version 不同 payload、缺 session 的 legacy
  frame 全部 fail closed，不得覆盖较新数据。

## 4. 行为变更契约（实施前需用户确认）

| 场景 | 现在 | 修复后 |
|---|---|---|
| 打开 Context Inspector | 现场重新估算 legacy Persona/facts/tools/history | 读取本 Session 最近一次真实 Run 的冻结公开快照 |
| 新会话尚未调用模型 | 可能显示 7.4k 等估算，同时顶部 0/0 | 明确“尚无真实调用”；binding 可见，usage 不伪造 |
| Session A/B 选择不同模型 | 全局 SDK stack swap，Run 串行/可能串 binding | 每个 Run 冻结自己的 provider/model/params，可并发 |
| Run 中修改 Provider/模型 | 可能 refresh/close 活跃 stack | 只影响未来 Run；当前 Run 保持原 binding |
| Thinking/Fast | UI 已保存但 SDK 忽略/DeepSeek 强制 high | 由 Session binding + 模型 capability 精确映射 |
| Provider 无 reasoning 内容 | 固定/二次生成兜底可能被称为思考 | 不伪造；显示工具轨迹或一次“未返回可展示摘要” |
| Context usage | 0/0、旧 provider `last_usage` 或错 model | 每个真实 physical attempt 独立持久化并幂等汇总 |
| Tool catalog | 静态名称 + generation=1，Inspector 又读 legacy registry | Provider/executor/Inspector 共享同一 generation/schema fingerprint |
| history 读取失败 | 静默退化到只发当前文本 | Run 明确失败或带 unavailable reason 的受控降级，用户可见 |
| Context preview | 可能显示原始 persona/history/header/secret | 默认拒绝敏感正文，只显示有界脱敏 manifest |

Feature policy：cutover，不保留双写/双读；兼容只允许读取旧 Session message 和 usage state，不能让旧
assembler/Inspector/provider usage 继续参与新 SDK Run 的 authority。

## 5. 实施切片（最多三个高风险 slice）

### Slice A — Prepared snapshot、Run binding 与 exact catalog `[AC-10,11,12,14,16,17]`

1. 新建 prepared snapshot contracts/service/store/public redactor；为 SessionDB 增加 versioned public snapshot
   schema 和 idempotent upsert/query。
2. 用当前 Persona/Memory SDK/Skills/SessionDB history/attachments/project ports 组装 exact structured messages；
   定义每个 unavailable source 的 fail/degrade policy，以及 attachment body size/retention/cleanup/diagnostic
   exclusion。
3. 在 `../simple-harness-sdk` 实现 structured content、nullable extended usage、per-Run provider/budget、
   per-generation catalog resolver 和 durable projection receipt；完成 SDK conformance/restart tests，构建
   v0.1.5 wheel，更新 backend pin/hash/license/source manifest。
4. SDK stack build 冻结完整 canonical tool specs、generation、per-schema fingerprints；把 public progress
   纳入正式 schema，Provider/executor 都按 Run generation 解析。
5. 新建 `SdkRunBindingRegistry`；把 provider/model/model_params/context window、tool execution identity、
   permission/grant/capability bridge 全部绑定到 run_id。
6. 将 foreground `_execute_sdk_run` 改为只接收 prepared snapshot；删除 `_assemble_sdk_messages`、静态
   capability snapshot 和 session_generation=1 生产调用。
7. 将 context window 从 global override 改为 Session model params；catalog UI 使用被选 Session/provider 的
   catalog，不再永远读取 chain 第一项。

### Slice B — per-Run Provider routing 与真实 attempt/usage `[AC-13,15,16,17]`

1. 接入 SDK per-run provider+optional-estimator/budget resolver，使 claim target、reservation、estimator
   digest、dispatch adapter、response charge/budget fingerprint 共享同一 frozen binding。
2. Provider adapter 消费 capability profile 与 Session reasoning mode；删除 base URL heuristic 和固定 high。
3. 每次 provider settlement 产生 durable projection receipt；reconciler 幂等投影 Session attempt；可信
   succeeded usage 写现有 usage state；missing/failed/unknown 保留 attempt 但不伪造 sample。
4. 删除 public narration 二次 Provider 调用与 usage 合并；正常 Provider response 是一次 physical attempt。
5. SDK completed/failed/cancelled 才释放 durable binding/incarnation/catalog lease；WAITING（authorization、
   provider/tool unknown、child wait）保留并可 restart/resume。所有 settlement 可由 reconciler 补投影；
   `RunPresenter.finish_turn` 不再从 legacy provider 读取 SDK usage/billing。
6. 移除 per-run global runtime refresh/close 和 full-run lock；provider mutation 遵守 active incarnation fence。

### Slice C — Inspector cutover、frontend fencing 与旧链路 denylist `[AC-10,13,14,15,16,17]`

1. 后端 `context_breakdown_request` 改为 owner/session/correlation-fenced public snapshot query；删除动态 probes。
2. 前端 Context modal 显示 snapshot identity、真实 model/usage availability、exact section counts/token estimate；
   stale/wrong-session/equal-version conflict fail closed。
3. 清除 missing-session → active-session fallback；restart hydration 只从 durable snapshot/usage authority 恢复。
4. 建 production-link denylist tests：foreground 不得调用 `_assemble_sdk_messages`、legacy breakdown、legacy
   `last_usage`、global SDK refresh；SDK request 不得出现 public summary/artifact/private reasoning。
5. 更新 Context/Provider/Thinking UI 文案，明确“估算”和“LLM 实测”；未返回 reasoning 不显示伪摘要。
6. 全绿后同步更新 `ARCHITECTURE/` 与 PROJECT_STATUS，删除或显式 deprecated 未使用的旧 authority。

## 6. 决策 spike 与 mandatory slice gates

只有 SP-1 的 estimator bind 负向证明是 pre-implementation decision spike，已经完成并否决
ContextVar-only 方案。完整 SP-1/2/3 依赖新增的 SDK v0.1.5 API，属于对应 Slice 实现后的 mandatory gate：
SP-1/2 在 Slice A 完成后、进入 Slice B 前通过；SP-3 在 Slice B 完成后、进入 Slice C 前通过。DoD 仍要求
三个完整 gate 最终全绿。

### SP-1 — per-Run Provider 并发隔离

用一个真实 SDK SQLite UoW 和两个 fake Provider 并发运行 Run A/B；模型、target、pricing availability、
usage 各不相同。断言 ledger 的 target/request/reservation/estimator digest/charge/budget fingerprint 全部
与各自 Run binding 一致，中途切换 registry 不改变已 claim Run；用 barrier 证明 physical intervals 重叠。

### SP-2 — snapshot → RunStart → Provider exact equality

用包含 CJK Persona、Memory canary、Skill、真实结构化附件 content block、项目、长历史和两项完整工具
schema（含 public progress 字段）的 prepared fixture：

- prepared exact messages/content blocks == SDK StartSnapshot == 首次 physical provider request JSON；
- snapshot catalog names/fingerprints == provider specs == executor specs；
- public Inspector projection 不含正文 secret/reasoning/Authorization/path canary；
- snapshot canonical serialization 重放 fingerprint 相同。

### SP-3 — physical attempt → Session usage projection

同一 Run 执行 succeeded-with-usage（含 cache）、succeeded-without-usage、definite-failed、unknown 四个
attempt fixture；断言四条 attempt 均 durable，只有第一条更新 ContextUsageStateV2；在 SDK settle 前、
settle commit 后/outbox 前、outbox 后/SessionDB 前、SessionDB 后/cursor 前逐点 fault injection，重启 reconcile
后无漏无重；hash conflict fail closed，Inspector 恢复相同 model/usage/snapshot lineage。

### Phase 2 spike 记录（2026-08-21）

- `SP-1 / 部分 PASS + 方案否决`：真实 `FrozenPriceEstimator.bind()` 对第二个不同 pricing key target 稳定
  抛 `estimator pricing_key does not match provider target`。结论：ContextVar-only proxy 被否决，正式
  provider+estimator/budget resolver 成为强制 SDK v0.1.5 实现项；完整双 Run 并发用例在 resolver spike
  中继续完成。
- `SP-2 / SDK seam PASS`：真实 SDK v0.1.4 `ReActDriver._messages/_tools` 对 CJK system/user 顺序与
  `capability_snapshot.tools` 顺序原样保留，并从 executor 返回对应完整 schema。结论：RunStart 可以成为
  exact message/tool-name 输入，但 prepared snapshot service 与 fingerprint equality 仍需在 Slice A 实装测试。
- `SP-3 / storage sink PASS`：真实 `SessionDB + SQLite` 在先冻结 binding epoch 后，对
  `sdk-provider-invocation:<id>` sample 首写、重放、不同 ingestion timestamp 保持单条；相同 source id 改
  token payload 稳定抛 `ContextUsageSampleConflict`，materialized state 显示正确 provider/model/usage。
  结论：现有 ContextUsageStateV2 可复用为“可信成功 usage”汇总，但仍需新增所有 attempt 的 audit table。

## 7. 测试与证据矩阵

### 自动化

- contracts/store：snapshot schema、canonical fingerprint、CAS/idempotency、owner/session/version fences；
- assembler：各 section、预算/截断、history allowlist、failure policy、附件公开/私有边界；
- SDK v0.1.5：structured content、extended nullable usage、per-Run provider/budget、versioned catalog、
  projection receipt/reconcile、WAITING restart conformance；
- catalog：真实 generation、完整 schema equality、active/waiting/restart old-generation lease；
- provider：DeepSeek/Kimi/unknown capability fixtures、tool-loop private reasoning replay、无 reasoning 诚实状态；
- public progress：missing/blank/wrong-type 三个 fixture 都不阻断业务工具；仅非空 string 产生 narration；
- concurrency：两 Session 不同 provider/model/params 并发、设置变化不影响 active Run；
- usage：每个 physical attempt、cache nullable、missing/failed/unknown、两库 fault injection/reconcile、restart；
- Inspector：不触发动态 source、correlation/version/session fence、敏感 canary 全拒绝；
- denylist/reachability：新 foreground production path 无 legacy assembler/breakdown/last_usage/global refresh。

### affected-surface smoke

- Provider add/edit/delete/reorder/default model、本地持久化与 Keychain ref；
- chat send/stop/retry/history hydration；
- tool call/result/permission/wait/cancel/recovery；
- thinking group 运行展开、终态折叠、点击重看；
- memory_recall/search、file/read/shell/web 的成功/失败 durable result；
- Context modal 空会话、成功、缺 usage、失败、重启、多 Session 切换。

### Computer Use 真机 E2E

按仓库手工纪律，使用 App UI 真点击/键入，不用 WS 注入：

1. Session A 选择 DeepSeek Thinking，Session B 选择另一模型/Fast；用测试 Provider barrier/ledger interval
   receipt 证明两个 physical invocation 真正重叠，并记录两边 Run/snapshot/binding/target；
2. 运行中分别打开 Context，核对 model、snapshot、section、真实 usage availability；
3. 修改 Provider 默认/会话模型，确认 active Run 不漂移，下一 Run 才生效；
4. 完成/失败/停止各一例，重启 App 后核对 usage、Context、工具结果和思考分组；
5. 输入敏感 canary（伪 Authorization/path/attachment 内容），确认 Inspector/日志/截图不泄露；
6. 原始证据只写 `.local-test-evidence/2026-08-21/sdk-context-authority-*`，Git 只提交结论、scenario/run id、
   hash 与相对索引。

## 8. Assurance / 威胁与失败边界

- Assets：Provider secret、用户 conversation/persona/memory/附件、私有 reasoning、usage、tool authority。
- Trust boundaries：UI WS → product preparation；SessionDB/memory/skills → frozen snapshot；SDK Runtime →
  Provider；Provider response → public projection；tool catalog → authorization/executor。
- 失败必须显式：source unavailable、catalog drift、binding stale、usage unavailable、charge unavailable、snapshot
  conflict、wrong session、late response、provider unknown。
- 任何 projection/Inspector 失败不得改变 physical provider settlement、tool effect、Run terminal；但准备阶段
  缺失会改变模型语义的 source 不得静默跳过。
- 最大 blast radius 由 per-Run immutable binding 限定；一个 Provider mutation/Session corruption 不得关闭或
  污染其他 active Run。

## 9. DoD

1. AC-10～AC-17、TO-A10～A17、TO-R7～R11 均有 required PASS 证据；
2. 三个 spike 全绿，或记录被否决方案并采用已证明替代；
3. 自动化、typecheck/py_compile、affected smoke、Computer Use 真机测试全绿；
4. Context Inspector 不再调用 legacy dynamic probes，SDK usage 不再读取 legacy `last_usage`；
5. 两 Session 可并发使用不同 immutable provider/model/model params，无 stack refresh 串扰；
6. Provider/executor/Inspector 的 catalog generation + fingerprint 一致；
7. hidden reasoning/public summary/artifact 不进入 fresh Run，敏感 canary 不进入 UI/log/evidence；
8. `ARCHITECTURE/` 对应事实源与 PROJECT_STATUS 在同一交付更新；
9. testcase/results/gate receipt 按 plan-test 规则落盘，原始证据保持 Git ignored；
10. 不留默认 OFF 的已完成能力，不保留新旧双 authority。

## 10. 明确停止追踪点

- 不把原始私有 CoT 展示给用户；只展示真实公开内容、基于允许来源的有界摘要或诚实状态。
- 不恢复 `main.py.bak2` 的整套旧 Context OS composition。
- 不把完整敏感 provider request 复制到 SessionDB/Inspector。
- 不用模型 ID/Base URL 猜测未知 Provider 的私有参数。
- 不在本 plan 中重写整个 SDK workflow/child-run 架构；SDK v0.1.5 只扩展 structured messages、usage、
  per-Run provider/budget、versioned catalog 和 projection receipt 所需 contract/port。
- 不修改 Provider 价格或外部计费 API；价格未知明确 unavailable，本增量不宣称真实 cost。
- 不删除历史数据库记录；只停止旧链路参与新的 SDK Run，并提供兼容只读/迁移。

## 11. 外部一手资料与本项目适配

- DeepSeek Thinking Mode：`thinking.type` 与 `reasoning_effort`，tool loop 需回传 private
  `reasoning_content`。适配：只在 capability profile 明确支持时发送，并限制在同 Run 私有 metadata。
  <https://api-docs.deepseek.com/guides/thinking_mode/>
- Kimi K2.6：thinking/tool loop 同样需要 preserved reasoning。适配：按具体模型 profile，不与 DeepSeek
  共用 base URL heuristic。<https://platform.kimi.ai/docs/guide/kimi-k2-6-quickstart>
- OpenTelemetry GenAI attributes：每个调用记录 request/response model 与 input/output/cache/reasoning usage；
  messages/system/retrieval 可能敏感。适配：physical attempt 单独记账，public Inspector 默认不展示正文。
  <https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/>
- RFC 8785 JSON Canonicalization Scheme：适合稳定 fingerprint。适配：复用项目/SDK canonical JSON 语义并
  固化 golden fixtures，不引入另一套排序实现。<https://www.rfc-editor.org/rfc/rfc8785.html>
