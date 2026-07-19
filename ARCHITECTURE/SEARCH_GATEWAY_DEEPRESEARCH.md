# DeskPet Search Gateway 与 DeepResearch 进度架构基线

> 校准日期：2026-07-19

## 2026-07-19 当前生产事实

- 新建调研默认由 `resolve_deep_research_workflow_version()` 选择 immutable `deep_research/v6`；v1-v5 只保留历史读取、在途恢复与 continuation 兼容，不再代表新 run 的默认链路。
- 当前链路为 `deepresearch` tool -> workflow starter -> `WorkflowLauncher.launch(deep_research/v6)` -> v6 production nodes/runtime adapters -> Search Gateway 与 evidence ledger -> exact/report compiler -> durable terminal projection、artifact 和 assistant delivery。
- Search Gateway 使用请求级预算、provider limiter/cooldown、官方来源策略、质量排序和 static fetch -> bundled Playwright -> installed Edge 的有界抓取链；URL、query、正文与模型中间推理不会进入默认公共进度投影。
- Relay 对用户指定的 `zai-org/GLM-5.2` 暴露可调用别名 `sf-glm-5.2`。基础模型和 problem-pipeline 预分析默认均使用该别名；在 provider 元数据尚未成为运行时事实源前，alias/canonical 暂时按 1M 名义上下文解析。
- v6 的完整当前契约、边界和验证证据以 [`DeepResearch.md`](./DeepResearch.md) 为主事实源；下文 v5 与 v1 章节是 2026-07-17 的演进快照，不得再解读为当前默认生产链路。

## 2026-07-17 v5 历史增量事实

- production `deep_research/v5` 通过 `search_dimension_batch()` 使用父级有界预算，durable outcome 使用严格 JSON 投影，不泄漏 allocator 内部 tuple。
- fetch pool 不再对全局结果直接 `[:24]`；它按 locked brief 的维度顺序 round-robin 选取最多 24 条，稀疏维度不浪费共享预算，核心与后置维度不会被前置结果饿死。
- 抓取链为 static fetch -> bundled Playwright -> installed Edge（受策略约束）；v5 不使用 Jina 作为证据兜底。浏览器池为单 browser、每次独立 context、有界并发并在取消/超时后清理。
- 安装包内锁定 Playwright 1.61.0、Chromium Headless Shell revision 1228；冻结包断网动态渲染与哈希检查通过。模型不进入 NSIS thin bundle，由既有模型 provisioner 管理。
- v5 progress 只投影安全的计数、枚举、质量、缺口、control 和 lineage 字段；查询词、URL 与正文不会出现在默认进度卡。
- 长 Search/Fetch effect 执行期间每 0.5 秒轮询 durable control；`generate_now` 被观察后不再启动新补证，并允许当前原子 effect 收敛到 30 秒 settle fence。fence 触发的 adapter cancellation 作为业务 `cancelled` stage result 进入评分/缺口/终态，不作为节点失败。
- v5 query builder 以聚焦目标、维度提示和有界 suffix 生成最多 240 字符的探针；中文 `国家统计局/统计公报/stats.gov.cn` 约束会选择 official-statistics family。产品比较初始轮使用 broad-web pairwise discovery，第一方约束留给后续有界 rescue。
- 2026-07-17 修复复验中，国家统计局题接纳 11 个有效来源（5 个第一方）并以质量 80 `completed`；产品比较由修复前 2 来源/`insufficient_evidence` 提升为 9 来源/质量 60 的诚实 `partial`。
- 同日 TC-UI-NOW run `6ea6a3a4...` 真点击 generate-now 后约 0.227 秒 observed，deadline 后 settled/consumed；run completed、全部节点 succeeded、UI 硬失败 0，最终诚实交付 `insufficient_evidence`。
- DeepResearch v5 的 workflow 节点耗时由 durable observer 在所有终态统一落入 `workflow_node_attempts` 与 `trace_spans`，并以 `deepresearch_stage_timing` 镜像到 structlog / `metrics.jsonl`。记录字段为 `run_id`、`workflow_version`、`stage`、`attempt`、`status`、`duration_ms`；结构化运行日志另带 `started_at` / `ended_at`。v5 不再生成缺少真实时钟来源、耗时恒为 0 的 legacy stage metric。
- FetchExtract 对 DeepResearch run 追加 `deepresearch_fetch_attempt_timing`：robots、Scrapling、HTTPX、extract、Playwright、Edge CDP、Jina 的 transport/extractor 耗时与结果写入隐私安全的 metrics 镜像。该层只接受运行 ID、枚举、计数、耗时和错误码，不接受 URL、query、页面内容或生成文本。`metrics.jsonl` 有 2 MB / 最近约 2000 行的轮转边界；这些 fetch 子阶段当前没有写入 `workflow.db.trace_spans`，长期精确 trace 只覆盖 workflow 节点级 timing。

> 校准提交：`0117ad764f593d018392332541d475a1d52a07ac`
> 工作树状态：校准时存在大量未提交的用户改动；本文只记录当前可见代码事实，不以旧全局架构文档的 commit anchor 代替现场核验。
> 范围：快速搜索、DeepResearch 检索/抓取/并行、durable workflow 进度投影与聊天 UI。

## 1. 历史基线链路（2026-07-17 校准快照）

### 1.1 快速搜索

```text
LLM / AgentLoop
  -> deskpet_tool_registry_v2.web_search
  -> code_tools.web_search_tool.web_search()        [同步 handler]
  -> search_provider.search()                       [同步 provider 队列]
  -> HTTP SERP provider（bing / duckduckgo / baidu）
  -> JSON 字符串 {query, count, results, error?}
```

证据：

- `backend/deskpet/tools/code_tools/registration.py:123-143` 注册唯一 `web_search`，handler 是同步函数。
- `backend/deskpet/tools/code_tools/web_search_tool.py:29-48` 调用 `search_provider.search()` 并保留旧输出形状。
- `backend/deskpet/tools/search_provider.py:617-682` 的同步路径只支持普通 HTTP SERP。
- `backend/deskpet/tools/search_provider.py:637-638` 明确跳过 `bing-cdp`、`google-cdp`、`searxng`。
- `backend/deskpet/tools/search_provider.py:55-58` 默认队列却只有 `google-cdp`。

由此得到一个当前生产缺口：没有额外配置时，快速搜索把唯一默认 provider 直接跳过，通常返回空结果；CDP 与 SearXNG 只有异步路径可用。

### 1.2 DeepResearch（历史 v1 链路）

```text
强意图识别 / deepresearch tool
  -> research_tools 的 workflow starter
  -> main._start_deepresearch_graph()
  -> WorkflowLauncher.launch(deep_research/v1)
  -> NativeWorkflowExecutable
  -> normalize -> plan -> expand -> search -> direct -> fetch -> score
  -> gap loop -> rerank -> synth -> cite -> persist -> finalize
  -> workflow report / artifact / final assistant delivery intents
```

证据：

- `backend/main.py:3434-3477` 把 `deepresearch` 启动器绑定到 `deep_research/v1`；`[workflows].enabled` 和 `deep_research` 决定是否使用该路径。
- `backend/main.py:3345-3352` 通过 `legacy_ports()` 给原生图注入 LLM、搜索与抓取端口。
- `backend/deskpet/workflows/definitions/v1/deep_research.py:552-610` 定义当前原生图；所有研究节点是单链，仅 `gap` 有循环条件边。
- `backend/deskpet/workflows/definitions/v1/deep_research.py:612-633` 记录 prompt/policy manifest，但 `fanout_strategy` 目前只是声明，不对应子问题并行节点。
- `backend/deskpet/workflows/definitions/v1/deep_research.py:457-541` 将报告、artifact 和最终聊天答复变成 terminal delivery intents。

### 1.3 已启用但未进入原生图的 fan-out（历史）

- `config.toml:582-588` 默认开启 `subagent_fanout`，子问题范围 2–6。
- `backend/main.py:2984-2988` 把 research lane scheduler 注入 `research_tools`。
- `backend/deskpet/workflows/definitions/research_core.py:1077-1091` 只在可复用/direct core 的整段执行入口判断并调用 `_run_subagent_fanout()`。
- `backend/deskpet/tools/research_tools.py:1329-1485` 的旧 fan-out 在一个外层函数内部调度子 run、收集结果并综合。
- 原生 `deep_research/v1` 的节点 handler 分阶段调用 core stage，不经过 `run_research_core()` 的上述入口，因此 production durable graph 没有获得这段 fan-out。

结论：当前日志中“scheduler wired / fanout enabled”只能证明兼容执行能力已接线，不能证明默认原生 DeepResearch 图正在对子问题并行。

## 2. 搜索 provider 的当前职责与边界

`backend/deskpet/tools/search_provider.py` 同时承担：

- provider 配置解析与默认队列；
- Google/Bing CDP、Baidu、DuckDuckGo、Bing HTTP、SearXNG 实现；
- CAPTCHA/空结果判断；
- 结果缓存、引擎失败计数和冷却；
- 最近一次 engines/errors 诊断；
- sync 与 async 两套不同能力的公共入口。

当前状态存在三类耦合：

1. **能力不对称**：`search()` 跳过 CDP/SearXNG，`search_async()` 才完整支持 provider（`search_provider.py:685-855`）。
2. **只取首个成功源**：异步入口按队列顺序返回第一个非空结果，没有多源并发、聚合、URL 规范化去重或跨源排序。
3. **进程全局可变观测**：`_search_cdp_count`、failure/cooldown/cache、`_last_engines_hit` 与 `_last_search_errors` 位于模块级（`search_provider.py:106-115`）。缓存/冷却可以是共享资源，但 per-run budget 和 last-observation 不适合并发请求共享。

真正的 SearXNG 已经是可选 provider：配置 URL 后，异步路径调用其 JSON API（`search_provider.py:775-815`）。它不是当前桌面应用的运行前提，也没有被安装包管理。

DeepResearch 还有一条不经过通用 SERP provider 的权威/平台直连检索面：`research_core.direct_stage()` 根据 `direct_source_for()` 调用 `research_sources`，覆盖 Agent-Reach、Wikipedia/arXiv、CNInfo/EDGAR 等来源（`research_core.py:560-716,950-1000`）。因此目标边界不是把所有来源强塞进 SERP adapter：Search Gateway 统一通用搜索 contract；权威/平台 direct adapters 保持独立路由和专用语义，但输出必须归一化到同一 candidate/evidence contract 后再参与去重、评分和引用。

## 3. 抓取与正文抽取的当前链路

当前至少有三套相似实现：

| 调用方 | 入口 | 当前策略 |
|---|---|---|
| 通用 web tools | `web_tools._fetch_one()` | Scrapling → httpx；article 再用 Trafilatura → Selectolax |
| DeepResearch | `research_tools.default_extract()` | Scrapling（仅自持 client 时）→ httpx → Trafilatura → 可选本地 CDP → 可选 Jina |
| Scrapling 工具 | `scrapling_tools._scrapling_get_html()` | Scrapling → httpx，返回原始 HTML/文本 |

证据：

- `backend/deskpet/tools/web_tools.py:200-216,247-320,407-496`。
- `backend/deskpet/tools/research_tools.py:995-1111`。
- `backend/deskpet/tools/scrapling_tools.py:47-123,149-160,238-245`。

这些路径的超时、CAPTCHA/阻断识别、fallback 顺序、质量字段和诊断形状不统一。DeepResearch 还引用可选 `research_crawl4ai` 分支（`research_tools.py:403-420`），但仓库当前没有对应实现文件；这应在目标架构中移除或明确保持不可用，不能把它列为已落地能力。

## 4. 原生工作流 frontier、join 与恢复能力

原生工作流已经具备表达**有界静态多分支图**所需的数据结构：

- 一个节点可通过多条普通 edge 产生多任务 frontier；
- `Edge((source_a, source_b, ...), target)` 表示 join（`definition.py:93-112`）；
- engine 记录 `completed_activations` 和 `join_firings`，仅当 join 全部 source 完成后激活 target（`native.py:541-598`）；
- 并行写可用 `DICT_DISJOINT` 或 `STABLE_LIST` reducer 做确定性合并（`definition.py:722-780`）；
- frontier、state、join firing 均进入 checkpoint，支持重启恢复。

但当前 native executor 会按 `task_id` 排序后逐个 `await _run_task()`（`native.py:329-333`），因此同一 frontier 只是多任务 checkpoint/superstep，并不是真正并发执行。`NodeDispatch.PARALLEL` 虽已进入 definition/manifest（`definition.py:58-90,1124-1129`），当前 executor 没有消费它来并发调度。

当前引擎也没有按运行时列表动态创建未知数量节点的 public map primitive。DeepResearch 的最大子问题数已经被约束为 6，因此目标设计可采用固定 6 个 branch 节点、按实际子问题条件 no-op、静态 join；同时只为标记 `NodeDispatch.PARALLEL` 的纯读/幂等 branch frontier 增加有界并发执行，避免引入通用 dynamic-map 和 checkpoint schema 重写。

## 5. 当前进度事件与聊天投影

### 5.1 后端

`WorkflowProgressReporter` 当前：

- 只允许 `started / waiting / failed / cancelled`，没有 `completed`（`backend/deskpet/workflows/progress.py:20-21`）；
- DeepResearch 只公开 7 个粗粒度节点，`expand/direct/fetch/score/gap/rerank` 不投影（`progress.py:41-54`）；
- 使用 outbox `ensure_event()` 持久化并投递 `workflow.progress`，event key 包含 run、node、attempt、transition（`progress.py:131-164`）；
- payload 只包含 allowlist 后的用户安全字段，不暴露内部 state。

这里的 event key 没有 task/activation 身份；同一 `gap` 节点在循环中若 attempt 和 transition 相同，会被折叠为同一事件。目标计划必须明确“逻辑阶段”和“阶段实例”的稳定身份，既不能让循环/重新补证丢事件，也不能让同一 task 的重试产生重复完成气泡。

`NativeWorkflowExecutable` 在节点开始、等待、取消、失败时调用 reporter，但节点成功并写入 pending patch 后直接返回，没有报告成功完成（`backend/deskpet/workflows/native.py:475-539`）。

### 5.2 前端

当前前端遵循旧 AC-23 的“每个 run 一张卡”模型：

- `sessionsStore.ts:22-102` 只有 `workflow_progress` message role，并明确注释 one projection per run。
- `sessionsStore.ts:307-429` 以 `workflow-run:{run_id}` 为固定 id；新事件覆盖同一 message，旧 seq 和 terminal 后事件被拒绝。
- `sessionsStore.ts:638-676` 对历史 workflow events 按 run/seq 重放，能恢复同一张卡。
- `MessageStreamPanel.tsx:237-280,299-302` 把该 message 作为唯一 row。
- `MessageStreamPanel.tsx:596-710` 渲染固定高度 progress card 和 ARIA progressbar。
- `code-panel/ws.ts:693-711` 从历史消息抽出四类 workflow event 后交给 store reducer。

这套模型已有可靠的幂等、乱序和历史恢复基础，但与新需求冲突：它只覆盖 summary，不会为每个完成阶段追加一个独立、默认隐藏的聊天气泡。

完整持久化/回放链是：

```text
progress reporter -> outbox workflow.progress
  -> session_message delivery -> SessionDB assistant row + workflow_event_id
  -> websocket delivery -> live frontend reducer
history load
  -> use workflow_event_id hydrate workflow.db event envelope
  -> ws.ts consume ordinary assistant row
  -> sessionsStore replay workflow event
```

其中 session delivery 受 session epoch 约束并调用 `append_message_if_epoch()`（`backend/main.py:3259-3284`），历史加载再按 `workflow_event_id` hydration（`backend/main.py:4955-5004,6914-6921`）。新阶段气泡应调整这条既有双投影，而不是另建一份浏览器本地持久化。

## 6. 目标架构必须保留的兼容边界

1. `web_search` 工具名、参数与 `{query,count,results,error?}` 基本返回形状继续兼容；增强诊断放可选字段。
2. `deepresearch` 继续默认进入 durable workflow，不回退为进程内长协程；新 fan-out 必须作为 `deep_research/v2` 注册并成为新 run 默认，不能原地修改 immutable v1 definition/manifest hash。
3. `deep_research/v1` definition 和 launcher adapter 继续注册，专门恢复旧 in-flight run 并读取旧 checkpoint；旧 `workflow.progress` 事件和历史消息仍可投影。v2 新事件采用向后兼容 event type/payload kind，并在没有活跃 v1 run 后另行执行退休流程。
4. outbox 是进度事件事实源并执行 at-least-once 重试；稳定 event/delivery ID、SessionDB 的 `workflow_event_id` 幂等和前端 seq/event reducer 共同形成幂等投影。不能把它表述成传输层 exactly-once，也不另建一套仅 WebSocket 临时进度通道。
5. 用户安全投影继续采用 allowlist；provider 错误、URL、原始页面内容和 LLM 中间推理不能直接进入聊天气泡。
6. 桌面默认能力不得依赖 Docker、外部服务或付费搜索 API；真实 SearXNG 只作为可选 HTTP provider。
7. 测试阶段完成的 Search Gateway、原生 fan-out 与新进度 UI 出厂默认开启，只保留紧急 kill-switch/兼容读取路径。

## 7. 计划需要解决的架构切口

- 把 `search_provider.py` 的 provider 细节包进异步、请求作用域的 Search Gateway；让 quick search 和 DeepResearch 共用同一 contract。
- 将共享 cache/circuit 状态与请求级 diagnostics/budget 分开，保证并发 run 不串数据。
- 把重复抓取/抽取收敛为 FetchExtractService，并保留旧工具 adapter。
- 为 native executor 补齐 `NodeDispatch.PARALLEL` 的有界并发语义，并新增 `deep_research/v2`：计划后最多 6 条原生 branch、固定静态 join、确定性 reducer、branch 预算与局部失败契约；v1 保持不可变用于恢复。
- 在节点 commit 成功之后发出 `completed`，并把 summary upsert 与 stage append 分成两个前端投影语义。
- 复用 outbox/history replay，以稳定 `event_id/run_id/stage_id/sequence` 构建可折叠 progress group；普通完成阶段默认隐藏，waiting/failure/final 保持主流可见。

## 8. 关键测试资产

- 搜索：`backend/tests/test_search_provider.py`、`backend/tests/test_p4s22_web_search.py`。
- DeepResearch：`backend/tests/test_deepresearch_subagent_fanout.py`、`backend/tests/test_workflow_deep_research_graph.py` 及 research core/tool 测试。
- 原生并行/checkpoint：`backend/tests/test_workflow_native_engine.py`、definition/compiler/recovery 测试。
- 进度：`backend/tests/test_workflow_progress.py`。
- 前端 reducer/UI：`tauri-app/src/stores/sessionsStore.test.ts`、`tauri-app/src/components/MessageStreamPanel.workflow.test.tsx`。
- 最终验收还必须覆盖真实网络 smoke、应用重启恢复和 Windows 桌面真点击 E2E。

## 9. 实施后架构（2026-07-14）

本文 1～7 节保留为实施前校准基线。当前生产实现已变为：

```text
web_search async handler ─┐
                         ├─> SearchGateway.search(SearchRequest)
deep_research/v3/v4 search ─┘   ├─ provider waves + request-local budget/diagnostics
                                ├─ canonicalize/dedupe/rank/cache/cooldown
                                └─ optional hydrate_top -> FetchExtractService

web_fetch / scrapling_fetch / research fetch
  -> shared FetchExtractService
  -> Scrapling -> httpx -> shell-gated Edge CDP -> explicit Jina
  -> Trafilatura / Selectolax -> EvidenceDocument
```

- `[search_gateway].enabled=true` 是出厂默认；默认 providers 为百度、DuckDuckGo、Google CDP、Bing CDP。
- SearXNG 仍是可选 JSON HTTP provider；只有 URL 有效时注册，不是安装包中的第二服务。
- request diagnostics 存在 `ResearchSearchResults`/`SearchResponse` 返回对象内；cache/cooldown 可共享，但不再用模块级 latest-result 在并发请求间传递结果。
- 所有 transport 在累积读取时执行 2 MiB 硬门；注入的兼容测试 client 仍支持一次性 `.text` 读取。

## 10. DeepResearch 兼容基线与进度事实源

本节记录 v4 上线前的兼容基线：当时新 run 默认启动 `deep_research/v3`；当前新 run 默认 v4，见 §16。v1/v2/v3 definition 均保持 immutable，只负责历史读取和在途恢复。v3 继承 v2 的固定六个 branch slot、五阶段 branch pipeline、静态 join 和 `NativeExecutionPolicy.max_parallel_tasks` 有界并发；inactive slot 写确定性 no-op patch，并在 synth/cite/publish 内部升级为 passage→atomic claim→support gate。

证据链如下：

```text
public stage handler
  -> frozen stage_projection in succeeded_pending
  -> coordinator merge/route/join succeeds
  -> commit_frontier(state + head + completed intent) [one SQLite transaction]
  -> workflow outbox
       ├─ session_message delivery -> SessionDB + workflow_event_id
       └─ websocket delivery -> live reducer
  -> WorkflowProgressGroup(summary + collapsed stage children)
```

启动恢复只把启动前遗留的 `delivering` claim 重置为可重试；周期 dispatcher 不会抢占当前进程仍在投递的 claim。SessionDB 已成功、websocket 失败时两条 delivery 独立收敛，前端再按 event/seq 幂等合组。

13 个 public stages 为 normalize、plan、expand、search、direct、fetch、score、gap、rerank、synth、cite、persist、finalize。总体卡始终可见；completed child 默认折叠；waiting/failed/cancelled/final 保持主流可见；elapsed 基于 `workflow_updated_at` 在运行中实时增长。

## 11. 当前验证边界

- 聚焦与相邻自动化、前端全量、TypeScript 和 production build 已通过。
- 真实 quick search 与正文 fetch smoke 已通过；provider circuit 按失败类型隔离并使用 single-flight probe，连续请求不再把 request-local budget 升级成 Gateway 全局停摆。
- 真实 DeepResearch 与 Windows W01～W05 已完成：13 阶段进度、运行中重启恢复、多 run 隔离、最终报告、历史 Artifact 操作和实时 Artifact 广播均有真机证据，详见计划目录 `results.md`。
- 固定三类别真实 DeepResearch v3 在同一进程连续运行：3/3 completed、mean support rate 1.0、nearest-rank P95 66.409s；政策后的 WebGPU run 仍有 22 次 provider attempt 与 2 次受控 probe，跨主题 cooldown 连坐不再复现。
- 最终文本使用独立 `final_assistant` channel，只投递 SessionDB 与 websocket；历史错误 assistant receipt 会被兼容收敛且不生成完成凭证。

## 12. 2026-07-14 实测暴露的问题（已于 2026-07-15 解决）

以下三项保留为问题来源记录；其中 cooldown 与引用支持已由第 13～14 节实现并验收，provider refresh 不属于本轮范围。

1. **隔离 provider 健康与 cooldown 作用域**：当前 cooldown 会跨请求、跨主题保留。政策调研运行期间及结束后紧随其后的两次 WebGPU 尝试都在 search 阶段得到 0 candidates，说明一次高压 run 可以让无关请求被整条 Gateway 连坐。下一轮应区分 provider 级退避、请求级预算和 Gateway 级无候选，允许健康 provider 或低成本 direct-source 路径继续工作。
2. **提高 claim support，而不是单纯增加候选数**：官方技术文档成功样本有 8 个独立域和 12 条引用，但最终 support rate 约 0.109；政策样本同为 12 条引用却达到 0.75。差距说明瓶颈已从“能否搜到”转为“证据是否真正支撑报告论断”，应优化 query decomposition、passage selection、claim 粒度与 repair 策略。
3. **收口后台 provider refresh**：辅助 FactExtractor 仍会记录旧 relay key 的 401 warning，虽然不阻断主聊天和 DeepResearch，但会制造噪音并浪费重试预算。应统一使用当前 provider registry 的动态凭据刷新路径。

优先级判断来自真实 benchmark 和 live outbox/日志，不以新增 provider 数量或单测覆盖率代替线上质量信号。

## 13. 2026-07-15 质量修复架构决策

### 13.1 cooldown：provider circuit 与 request budget 分层

当前 `ProviderHealth` 只有 `failures + cooldown_until`，`SearchGateway._run_provider()` 在调用 provider 前直接返回 cooldown；空结果、timeout、blocked 和普通异常都进入同一个 `record_failure()`。因此一次高压调研可以把所有 provider 打开，后续无关请求只能得到一组 cooldown attempt。

本轮将保留“健康状态按 provider 共享、请求预算与 diagnostics 按 request 隔离”的边界，并把 health 改为带 lease 的原子状态机：

- `acquire_permit(provider, allow_probe)` 一次性返回 `closed/open/half_open/half_open_busy`、circuit generation 和不可伪造 probe token，消除 check-then-call。
- `_Health` 持有 `generation/probe_token/next_forced_at/probe_failures/failure_class`。probe 只可由持有同 generation/token 的 `complete_probe()` 提交；取消或超时必须在 `finally` 中 CAS 释放，旧 token 不能关闭新 generation。
- `closed` 正常放行。经过 provider 校验的 empty 是 transport success，关闭/保持 closed；parse-invalid 才是 failure。timeout、blocked/captcha、rate-limit(429) 与 HTTP/invalid-response 使用独立 threshold/cooldown/backoff policy，不再把 403 与 429 合并。
- `open` 只影响该 provider。若所有实际 `is_available()` provider 都 open，请求按 `(next_forced_at, 配置序号)` 选择一个。每个 open generation 只武装一个 forced permit；首个 `next_forced_at` 在 request deadline 内的 eligible owner 必须有界等待并 probe，领取后该 generation 其他请求只能得到 busy/cooldown。probe 失败产生新 generation，但 `next_forced_at` 从上一次 probe 时间按 failure class 退避推进，而不是立即重置，所以连续请求的上游调用数受 probe interval 严格限制，不会“一请求一轰”。安全时间晚于 deadline 时诚实返回 budget/cooldown。
- `half_open` 每个 provider 最多一个 probe in-flight；并发 loser 得到 `half_open_busy` 并继续其他 wave。probe hit 或 validated empty 关闭 circuit；failure 重新 open；cancel 释放 lease 但不伪造 success。

`open_until` 与 `next_forced_at` 分离：前者控制普通 routing 的完整冷却期，后者控制“所有 available provider 都 open”时的有界救援探测。timeout/HTTP/invalid-response 默认 `threshold=2, open=30s, probe_base=2s, probe_max=30s`；blocked 默认 `1/300s/15s/120s`；captcha 默认 `1/600s/30s/300s`；rate-limit 默认 `1/60s`，并以服务端有上限的 `Retry-After` 作为不得提前突破的 probe 下界。probe 失败按 failure class 指数退避到各自 probe max；成功或 validated empty 清零。所有值进入 `[search_gateway]` 显式配置并在 fake-clock 测试中逐类锁定。

这不是把 cooldown 改成 request-local：那会让每个新请求重新冲击已知故障上游。共享 circuit 仍然保护 provider，但不能升级成 Gateway 全局停摆。

SearchResponse 为每个 request/run 保留安全的 attempt/permit/probe 汇总；v3 search branch 将 `request_id/run_id + provider/status/permit/probe outcome/count/elapsed` 写入 coverage，禁止 query、URL、正文和凭据。这样真实 benchmark 可以只凭 workflow DB 的 run id 证明是否实际尝试过 provider，而不依赖进程级日志猜测。

### 13.2 引用支持：先选 passage，再生成 atomic claims，再做 publish gate

当前低支持率的主要机制证据是：`synth_handler()` 只给模型每篇正文开头 1200 字符；`evaluate_support()` 把被引全文拼接后做 Jaccard，长文会稀释相关句；repair prompt 只提供 claim/citation id，不提供证据文本，模型无法可靠收缩论断。

本轮保持现有 13 个 public stage，但不再从 Markdown 反向猜测主数据。新内部模型为：

- `EvidencePassage`：`passage_id=hash(source_content_hash + start + end)`、`source_citation_id`、`canonical_url`、`question_id`、`text/ref`、`relevance`；同一 URL 的多个 passage 始终仍是一个 citation source。
- `AtomicClaim`：稳定 `claim_id`、`text`、`kind(factual|inference|opinion)`、`citation_ids`；LLM 直接生成该 JSON，Markdown 只在发布决定后渲染。
- `SupportDecision`：只记录真正胜出的 `passage_ids` 与其 `source_citation_ids`，不能把全部有效 citation 当作支持。

1. plan prompt 要求“一个可独立核验方面一个子问题”，优先官方/一手来源并保留精确实体、版本和日期。
2. synth 前从每篇正文按 topic/sub-question 选择稳定 passage，而不是固定正文开头；fallback 也产出结构化 claim，不产出不可定位的 Markdown 句子。
3. synthesis 明确每条 factual claim 只包含一个事实关系。兼容 `parse_claims()` 与 replacement 共用同一 clause tokenizer/span，但新 v3 主链不依赖字符串查找修复。
4. support 对每个 cited passage 独立评分，所有 exact 数字/日期必须位于同一个 winning passage；不能跨来源拼接。唯一 repair 是可重放纯函数：按稳定 clause span 检查原 claim，只允许保留被同一 cited passage 支持的原文 clause，不改写、不新增事实/引用；每个 claim 最多一次。仍不支持的 factual claim fail-closed pruning。分母固定为 repair 前 factual claims，分子是直接支持或 repair 后支持的 claims，pruning 不能把 support rate 抬成 100%。
5. `render_body(published_claims, analysis, limitations) -> body_md` 是唯一 canonical 正文纯函数；按 `body_md.encode("utf-8")` 计算门禁，再由 `PublishDecision` 决定，最后 `render_report(body_md, decision, appendix, coverage)` 只组装一次完整报告。support rate 分母是 pre-repair factual claims；supported count 只含直接支持或确定性 repair 后支持的 factual claims；citations/domains 只来自最终 published factual claims 的唯一 canonical source；inference/opinion 不凑 8 条。body bytes 排除重复“直接回答/发现”、appendix、Coverage JSON 和错误摘要。
6. 达标后 renderer 才生成 `completed`；不达标输出 `no_results/insufficient_evidence`，不能用未支持论断、长 appendix 或重复正文填门槛。

### 13.3 聊天 UI：折叠详情不等于隐藏结果

Outbox 和 SessionDB 继续是进度事实源，`workflow_stage` child bubble 仍默认隐藏。compact timeline 是唯一确定的只读 projection：`durable completed children（按 seq/stage-instance） + 当前 workflow_progress summary 派生行（started/waiting/failed/cancelled）`。当前行使用 `run_id:current:stage_id` 稳定 key；收到该 stage completed child 后被替换而非重复；terminal failed 保留为当前行，terminal-late completed child 仍按既有 reducer 接纳但不覆盖 terminal summary。

后端 completed payload 增加白名单 `action/result/result_code/diagnostic_codes` 以及 `published/discarded/repaired` 计数；started/waiting/failed 继续由 summary payload 提供 label/text/error。`degraded` 是 `completed + warning reason`，不是第六种 workflow terminal status。总体卡新增始终可见的 compact timeline：每行显示状态图标、动作和一行结果；cooldown、timeout、低质量丢弃、pruning 等原因直接可见。点击阶段或“查看全部阶段”展开详细气泡，展示指标、结果/错误和下一步。原生 button 自带 Enter/Space 行为，不额外绑定重复 toggle；保留 `aria-expanded + aria-controls` 与 live-region。

前端不持久化第二份 timeline 状态；它仍从同一 run 的 durable stage messages 派生，因此历史恢复、乱序、重复投递和多 run 隔离继续由现有 event/seq reducer 保证。

### 13.4 immutable workflow 升级边界

现有 `deep_research/v2` 的 definition、handlers、quality 和 report 依赖保持字节/行为冻结，用于升级前在途 run 恢复。新实现注册为 `deep_research/v3`，默认配置切到 v3；v1/v2 registry 与 context factory 保留。v3 仍投影 `schema_version=2` 的 13-stage progress contract，因此前端按 `(workflow_name=deep_research, schema_version=2, stage allowlist)` 识别，不再硬编码 `workflow_version === v2`。历史 v2 fixture 必须在升级后继续通过 manifest/hash/recovery 验证。

### 13.5 外部实践适配

- Circuit breaker 采用 closed/open/half-open 与受限 probe，但不引入新依赖；DeskPet provider 数量固定且状态简单，现有 asyncio lock 足以实现原子 single-flight。
- Faithfulness 采用“拆分独立 statements，再逐条判断能否由 retrieved context 推出”的思路，但保留本地 deterministic lexical/exact-token 主判与可选语义 scorer，避免把发布门完全交给另一次 LLM 判断。
- UI 采用 disclosure card 与 progress status 的可访问语义；折叠态保留富摘要，展开控制只负责附加详情。

## 14. 2026-07-15 实施与验收结果

### 14.1 Search Gateway 当前生产状态

- `ProviderHealthCache` 已实现按 provider 共享的 closed/open/half-open 状态、generation、open deadline、next forced probe deadline 与 single-flight probe lease。
- timeout、普通 HTTP、invalid response、blocked、captcha、rate-limit 均使用独立 threshold/open/probe policy；429 可携带有界 `Retry-After` 下界。
- validated empty 被视为 transport success；parse-invalid 才累计失败。probe 成功或 validated empty 关闭 circuit；失败创建新 generation 并推进退避；取消通过 CAS 释放 lease，不伪造 success。
- 所有实际 available provider 都 open 时，Gateway 只为 deadline 内最早 eligible provider 分配救援 probe；同 generation 并发 loser 得到 `half_open_busy` 并继续其他路径。
- coverage/metrics 只暴露安全的 request/run/provider/permit/probe/计数/耗时字段；query、URL、正文和凭据不进入 diagnostics。

关键确定性门包括失败类型矩阵、fake-clock 自然半开取消、10 个连续请求仅一次上游调用、最早 eligible/fairness/loser、cache hit 不改变 open state，以及 metrics 隐私测试。

### 14.2 DeepResearch v3 质量基线（已由 v4 接替默认）

- 本节验收时默认版本为 `deep_research/v3`；当前默认已升级到第 16 节的 v4，v1/v2/v3 registry、context factory、fixture/hash/recovery 契约继续保留。
- rerank 的稳定优先级为独立 domain → 唯一 canonical URL → 重复 passage，避免单 URL crowding。
- `evaluate_support` 对每个 canonical URL 保留最强且能独立支持 claim 的 passage；数字和日期必须在同一 winning passage 中出现。
- fallback 先覆盖来源，再补充独立支持 claim 以满足正文质量门；镜像来源陈述相同事实时合并 claim，并保留所有实际支持它的 source id。
- publish gate 同时检查 support rate、published factual claims、citations、independent domains 与 canonical body bytes；任何一项不达标都 fail closed。

### 14.3 Session UI 当前生产状态

`workflow_stage` child 仍默认隐藏详细气泡，但总体卡新增默认可见的 compact timeline。每个阶段行直接展示：

1. 当前状态与人类可读动作；
2. 一行结果摘要；
3. cooldown、timeout、证据丢弃、repair/prune 等安全诊断；
4. 展开后才显示 round、计数、elapsed、错误详情和下一步。

timeline 仍从 durable completed children 与当前 progress summary 唯一派生，不在前端持久化第二份状态。Xiaomi 1920×1080 真机已验证 `2/13` 时“理解任务”“规划调研”在折叠态可读；展开“检查证据缺口”可见 round、新 queries/evidence、elapsed 和下一步 `rerank`，其他阶段保持紧凑。

### 14.4 最终质量门

- 同一进程连续三类真实 benchmark：3/3 completed，mean support `1.0`，nearest-rank P95 `66409 ms`。
- Python：13 factual claims / 9 citations / 6 domains；政策：12 / 10 / 7；WebGPU：14 / 8 / 8。
- 后端集成聚焦 `127 passed`；后端全量 `4461 passed`，10 个已知失败集合无新增。
- 前端 `77 files / 806 tests`；TypeScript、production build、scoped ESLint 全部通过。
- 最终报告、`final_assistant` 与 Markdown Artifact delivery 真机通过。
- Xiaomi 截图与 Tauri/backend/workflow DB 联合证据归档在 `plans/2026-07-15-search-quality-cooldown-support/evidence/README.md`；benchmark 机器输出只保留 query fingerprint，不持久化原始 query、URL、正文或 secret。

完整机器可读 benchmark、测试结果与复跑步骤分别见：

- `plans/2026-07-15-search-quality-cooldown-support/benchmark-live-final.json`
- `plans/2026-07-15-search-quality-cooldown-support/results.md`
- `testcase/2026-07-15-search-quality-cooldown-support/search-quality-cooldown-support-manual-test.md`

## 15. 宽主题技术情报失败基线（Session 16bbb4ce，2026-07-15）

固定复现 run `ed254c0673d04771bbb2901fed462741` 证明第 14 节的跨请求 circuit 修复仍留下一个 **run 内容量边界**：

```text
DeepResearch v3 native frontier (max_parallel_tasks=4)
  -> 4 concurrent branch_search handlers
     -> each branch executes multiple SearchGateway.search calls
        -> each call uses stable provider waves, routing to at most
           baidu / duckduckgo / google-cdp / bing-cdp and stopping early on enough hits
        -> no provider-level normal-call semaphore
```

代码证据：`backend/main.py:3506-3512` 把 DeepResearch native 并发设为 2～6；`backend/deskpet/workflows/native.py:772-784` 只限制 graph task 数；`backend/deskpet/workflows/definitions/deep_research_v3_nodes.py:410-449` 在每个 branch 内逐 query 调 SearchPort；Search Gateway 当前 circuit lease 只约束 half-open probe，不约束 closed 状态的普通调用。

该 run 的持久化事实：

- 规划 5 个宽子问题、13 条 query；search + gap coverage 记录 25 次真实 provider 调用与 76 条 routing decision。
- Baidu 为 19 次 validated empty；DuckDuckGo 4 次 timeout 后产生 15 次 cooldown；Google/Bing CDP 各 1 次 half-open probe timeout，其余为 cooldown/half-open-busy。
- 0 candidates → 0 fetch → 0 passages → 0 claims/citations；terminal 为 `failed/deep_research_no_results`。
- 用相同运行配置在新建 Gateway 进程重放其中一个子问题，DuckDuckGo 5.92 秒返回 10 条结果；Google/Bing CDP timeout、Baidu empty。这把主要矛盾定位为“宽 fan-out 对共享 provider 无背压”，而非题目天然不可搜索。

第二个边界是 empty-aware rescue。第 13.1 节的救援条件只在所有 available provider 都 open 时成立；Baidu validated empty 会保持 closed，因此即使其余有产出能力的 provider 已 open，也不会进入“全 open”救援选择。empty 不应记为 failure，但“closed 且本请求已 empty”也不应继续阻止本请求在 deadline 内尝试一个 eligible probe。

第三个边界是失败交付语义。`persist_handler()` 只在 completed 时保存文件，但 `finalize_handler()` 无条件生成 report、artifact_card 和 final_assistant intents（`deep_research_v3_nodes.py:1069-1085`）；无文件时把整个 no-results report 嵌入 Artifact payload。于是 Session 收到 ArtifactCard 和 507 字 Markdown 失败说明，同时 workflow terminal 又是 error，形成“像报告的失败”。

最后，现有 publish gate 只度量 support、事实数、引用数、独立域与正文 bytes；对“最新且最有价值的 AI 技术”没有专门的技术情报意图、时间窗和价值排序契约。当前 plan 将基础模型、Agent、多模态、效率、开源、应用和治理一次铺开，其中一个子问题同时包含开源生态、行业应用与风险治理，超出“技术信息”主诉求。

本轮修复必须同时解决：provider 级背压与 permit 复验、empty-aware 零候选救援、技术情报 profile/价值排序、no-results 提前终止与无假 Artifact、真实/跳过 attempt 计数分栏。唯一验收事实源为 `plans/2026-07-15-deepresearch-wide-topic-reliability/acceptance.md`。

### 15.1 目标 provider limiter ownership 与调用时序

普通调用 limiter 由进程级 `SearchGateway` 持有，按 provider id 建 slot；quick search 与所有 DeepResearch run/branch 共用同一个 slot 集合，不在 workflow 内各建一份 semaphore。目标调用时序固定为：

```text
request deadline/cancel scope
  -> await provider slot
  -> acquire/revalidate circuit permit while holding slot
  -> call provider only when permit is closed/half_open
  -> record success/failure + complete probe if applicable
  -> release slot in finally
```

排队等待受 request deadline 和 cancellation 控制；未拿到 slot 的 timeout/cancel 只产生 request-local `queue_timeout/cancelled`，不累计 provider failure、也不持有 probe lease。把 permit 获取放在 slot **之后**，确保前一调用开路后，队列中等待的旧任务不能继续携带过时 closed permit 冲击上游。

### 15.2 request-local empty-aware rescue

救援判断不再只读取请求开始时的全局 circuit snapshot。Search Gateway 在每个 stable wave 后维护本请求 provider outcome：`hit / validated_empty / open / half_open_busy / failure / unavailable`。若当前仍为 0 results，所有 closed provider 都已在本请求 validated-empty，而其余 available provider 为 open/busy，则这些 empty provider 对当前请求不再算“可产出健康路径”；Gateway 按 `(next_forced_at, provider order)` 选择 deadline 内一个 eligible probe。

每个请求最多进入一次 rescue wait/claim；真正 probe 仍由 provider generation/token single-flight 保证。并发 loser 得到 cooldown/busy 并结束或继续未尝试 wave，不能循环领取 probe。validated empty 继续作为 transport success，不增加 failure，也不把 provider 全局标记为不可用。

### 15.3 失败图路由与安全重试协议

`zero_candidates` 与 `insufficient_evidence` 是不同终态：

- `zero_candidates`：search + direct + 一轮 rescue 后仍为 0，在 search/direct join 后短路；fetch、score、gap/rerank、synth、cite、persist 投影为 `skipped/not_run`，不得冒充 completed。
- `insufficient_evidence`：有 candidates/passages，但 cite publish gate 不通过；保留已实际执行阶段和质量原因，不发布 report Artifact。

两者只投递结构化 failure summary 与 terminal receipt；不得发送 `artifact_card` 或完整 `final_assistant` Markdown 报告。重试由 workflow service 保存的 server-side retry reference 发起，前端只发送 `run_id + retry action id`；服务端以原 start request 重建新 run。retry action 使用幂等键防双击，旧 run delivery 不复用、不变更，raw topic/query 不进入 progress、前端 retry payload 或 diagnostics。

当前前端只有 disclosure，`sessionsStore` 的 `workflow_recovery_action` 也只是字符串；需要新增 allowlisted retry projection、pending/accepted/error 状态和原生 button 键盘语义。

### 15.4 跨层计数契约

Search coverage、progress allowlist 和 compact UI 使用同一组可校验口径：

- 真实 transport 结果桶互斥：`hits / empty / timeouts / blocked / captcha / rate_limits / errors`；其中 `errors` 再以 `error_class_histogram` 安全细分为 `http_error / invalid_response / parse_error / other`，但细分值不重复加入总数。
- `actual_requests = hits + empty + timeouts + blocked + captcha + rate_limits + errors`，只表示真正进入 provider transport 的次数。
- 已完成并返回的 SearchResponse 中，未调用上游的 decision 桶互斥：`cooldown_skips / busy_skips / queue_timeouts / unavailable_skips / budget_skips`；`cache_hits` 单列，也不算上游调用。外部 task cancellation 必须 re-raise，因此没有 SearchResponse，只进入独立的隐私安全 `cancelled_wait` metric，不伪造成 attempt。
- `routing_decisions = actual_requests + cooldown_skips + busy_skips + queue_timeouts + unavailable_skips + budget_skips + cache_hits`，表示已返回请求内的全部 Gateway decision；UI 可显示为“Gateway 决策”，不能命名为“来源”或“上游请求”。进程级 metrics 可另报 `cancelled_waits`，但不能把它混入某个不存在的 response 等式。
- `probes` 是 `permit=half_open` 的真实调用子集，满足 `0 <= probes <= actual_requests`，与上述真实结果桶正交，不能再次加入任何总数。

`provider_attempts` 仍可作为安全明细；`provider_attempt_count` 作为旧 coverage 字段保留兼容并等于 `actual_requests`。后端 progress template、`sessionsStore` allowlist 与 `WorkflowProgressGroup` 中文标签必须一一对应。

### 15.5 technology_intelligence 节点职责

- `normalize`：仅依据 topic 文本确定性识别“AI + 最新/趋势/价值 + 技术”意图，写 `intent_profile=technology_intelligence` 与默认时间窗；普通主题保持 generic。
- `plan`：生成 3～5 个单一技术主题，默认聚焦基础模型/推理、Agent、原生多模态、训练与推理系统、开源基础设施；不主动混入监管/商业案例。
- `expand/direct`：每个主题产生短 discovery query、官方发布 query、论文/仓库 query，并附主题 source-pack/direct seed；不再只把完整长问题加 `official` 后重复搜索。
- `score/rerank`：保留 authority/recency/relevance/depth，并为 intelligence profile 计算技术影响与成熟度/可采用性证据维度；缺证据时为 unknown，不靠模型臆测补值。
- `cite/report`：只对已通过 passage support 的 finding 做价值排序；报告首部写时间窗与排序口径，每项包含已核验变化、为什么重要、成熟度和实际 winning citations。通用 support/publish gate 保持不变。

### 15.6 v4 不可变升级边界

本节修复不原地改写已投入运行的 `deep_research/v3` graph definition、node handlers、manifest 或 checkpoint 语义。Search Gateway 的进程级 limiter、empty-aware rescue 与计数是共享基础设施修复；需要改变图路由、技术情报 state、失败 delivery 和 retry contract 的部分注册为 `deep_research/v4`，并仅让新启动 run 默认进入 v4。v1/v2/v3 registry、context factory、manifest/hash 与恢复 fixture 继续保留，使升级时已持久化的在途 run 仍按原版本恢复。

v4 沿用向后兼容的 progress event envelope，并以新增的 allowlisted payload 字段表达计数、`skipped/not_run` 与 retry action；前端按 workflow name、schema capability 和字段存在性渐进解析，不能把历史 v3 消息误当 v4，也不能要求迁移旧 SessionDB 消息。v4 单独拥有 normalize/plan/expand/search/direct/failure/finalize 等变化节点；允许调用稳定的只读公共 helper，但不得通过修改 v3 私有 helper 的行为间接改变 v3 恢复结果。

## 16. 宽主题技术情报 v4 与专业报告（2026-07-15）

第 15 节的 Session `16bbb4ce-c282-4b25-b630-7400b6be25c1` 是失败基线。随后产生的 `66720bcf...` 虽通过旧的数量/support 门，但因同实体重复、全部同分、英文 claim、串题和 Coverage diagnostics 过重，也已降级为内容质量失败样本。本节记录最终生产事实。

新 run 默认进入 immutable `deep_research/v4`；v1/v2/v3 仅用于历史读取和在途恢复。Search Gateway 的 provider limiter、permit 复验、empty-aware rescue 与互斥计数继续作为 quick search 和所有 research branch 的进程级共享基础设施。

> **适用边界（2026-07-16 校准）**：本节 3～8 项专业报告、technology taxonomy、价值/成熟度排序只适用于 `intent_profile=technology_intelligence`。generic 主题运行在 v4 的 structured synthesis 配置、第一方来源优先 rerank、zero-candidate/insufficient-evidence 分流和安全交付外壳内，但其问题建模、query expansion、内容综合与报告质量核心仍委托 v3。因此“generic 抽取事实全部有引用”目前不等于“报告完整回答用户问题”；固定教育失败样本 `DeepResearch/帮我调研一下，现在中国小学现在的教育现状和国家下一步计划-1454d11734354b5fae10db8c565646e9.md` 即为该边界的实证。后续改进验收事实源为 `plans/2026-07-16-deepresearch-playwright-quality/acceptance.md`，架构调研见同目录 `architecture-baseline.md`。

### 16.1 技术情报路径

- normalize 确定性识别 `technology_intelligence`。宽泛 AI 主题使用稳定的中文 taxonomy：基础模型与推理、Agent 与工具调用、原生多模态、训练与推理系统、开源基础设施；只有用户明确点名的实体才直接保留，防止模型臆造 `Plus`、`VLA`、`Context Window` 一类泛化主题。
- planner 针对每个单技术主题生成 discovery、官方、论文/仓库查询；rerank 同时考虑相关性、权威性、时效性、影响与成熟度，并先做 canonical source 多样性覆盖。
- 抓取内容进入 evidence 前过滤页面 chrome、作者/投稿/分享/导航、营销话术和只有挑战描述而没有技术变化的片段；同实体特性合并，重复 finding 在发布前裁掉。
- synth 只提出候选 finding/atomic claim；最终实体、分数、成熟度、近期性和引用都由确定性 repair/prune 与 publish gate 校验。官方 GitHub release 可保留被正文支持的模型/协议发布事实，第三方静态描述不能借此抬高成熟度。
- `zero_candidates` / `insufficient_evidence` fail closed：不生成报告、不投递 Artifact、不发送伪完成消息；Session 仍保留 13 阶段诊断和服务端幂等 retry。

### 16.2 报告契约

技术情报报告不再固定凑满 5 或 10 项，而是只发布 3～8 个过门 finding。少于 5 项时必须明确说明“未使用低质量候选补齐数量”。报告固定包含：

1. `一页式执行摘要`：给决策者直接可用的优先级判断，逐项列成熟度、价值、PoC 建议与证据风险。
2. `组合建议`：说明哪些技术应该一起验证，而不是把 Top 列表机械复述一遍。
3. `分主题 Top 技术`：每项给出核心变化、为什么重要、成熟度/采用信号、风险、来源日期与逐项引用。
4. `方法与局限`：用决策表解释证据边界和未纳入项，不把内部 Coverage diagnostics 倾倒给普通用户。

只有 `evidence_quality >= 3` 才能直接建议 PoC；其余在摘要、组合建议和逐项正文中统一写为“先补齐一手来源与独立来源，再决定是否 PoC”。显示序号按最终分组连续生成，引用附录必须全部可解析并映射到具体 finding；质量门同时拒绝双句号等明显成文瑕疵。

### 16.3 最终连续真实验收

Xiaomi 屏幕通过真实 Tauri 输入原问题，同一 Tauri/backend 进程连续完成两轮：

- `43e851a0d1264bcd8ecfe90f258c5afa`（Session `7cda06c1-f5f5-4c4d-b10e-056b8be632db`）：5 项、5 个引用全部映射；当前 17 项质量门全部 PASS；弱证据的 PoC 建议在摘要和正文一致。
- `59975eb1a85a409e97e0277dcfe4a705`（Session `f88880ed-944e-43fb-92e5-965c3f837a5a`）：5 项、5 个引用全部映射；当前 17 项质量门全部 PASS；5 个最终分数均不相同。

两轮原问题指纹均为 `6e255f47e47108b5`，各只投递 1 report、1 Artifact event、1 final assistant，分别耗时 `142129 ms` 与 `141713 ms`。第二轮仍抓取 17 条、成功 16 条并保留 16 个证据段落，证明 cooldown 没有跨 run 连坐。隔离失败 run `4e6d1b0be1364dc196d47b5ae439a9de` 仍验证无 report、无 Artifact、无 final assistant，并保留安全 retry。

自动化结果：报告/情报聚焦 `114 passed`；v4/progress/delivery `161 passed`；v1～v3 兼容 `52 passed`；Search Gateway 隔离复跑 `63 passed`；前端 `811 passed`，TypeScript 与 Vite production build PASS；最后代码后端全量 `4643 passed / 14 skipped / 9 deselected / 10 known failures`，失败集合与固定基线相同。scoped ESLint 的 36 个既有错误与提交 `0117ad...` 完全相同，本轮新增行不在报错位置，按“无新增”记录而不伪写为 lint 全绿。

证据与人工用例：

- `plans/2026-07-15-deepresearch-wide-topic-reliability/results.md`
- `plans/2026-07-15-deepresearch-wide-topic-reliability/evidence/consecutive-success-run1.json`
- `plans/2026-07-15-deepresearch-wide-topic-reliability/evidence/consecutive-success-run2.json`
- `plans/2026-07-15-deepresearch-wide-topic-reliability/evidence/report-quality-43e851a0d1264bcd8ecfe90f258c5afa.json`
- `plans/2026-07-15-deepresearch-wide-topic-reliability/evidence/report-quality-59975eb1a85a409e97e0277dcfe4a705.json`
- `testcase/2026-07-15-deepresearch-wide-topic-reliability/deepresearch-wide-topic-manual-test.md`

### 16.4 已知边界

外部站点速度和候选质量仍会使不同 run 的耗时、Top 技术与数量变化；这是实时调研的正常属性。当前契约选择少而可信，不允许通过放宽引用、实体或成熟度门来换固定数量。relay 并行子任务偶发 `401 INVALID_TOKEN` 会降低 fan-out 收益，但主图必须依靠现有检索证据诚实完成或 fail closed。

### 16.5 阶段耗时遥测验证（2026-07-17）

- 新增 timing 定向测试：`43 passed`，覆盖阶段所有终态、durable DB/trace 与 JSONL 一致性、fetch 子阶段和隐私白名单。
- workflow/native/v5 相关回归：`76 passed`；v5 evidence/effect/control/contract 回归：`68 passed`。
- 遥测不参与 checkpoint 决策，也不改变 timeout、settle fence、降级或抓取顺序；写日志失败不会影响 durable workflow 执行。
