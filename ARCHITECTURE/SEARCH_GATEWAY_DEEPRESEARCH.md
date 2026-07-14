# DeskPet Search Gateway 与 DeepResearch 进度架构基线

> 校准日期：2026-07-14
> 校准提交：`e4a3bdc7528066e7d6a290004eeeb2eee5e5edf6`
> 工作树状态：校准时存在大量未提交的用户改动；本文只记录当前可见代码事实，不以旧全局架构文档的 commit anchor 代替现场核验。
> 范围：快速搜索、DeepResearch 检索/抓取/并行、durable workflow 进度投影与聊天 UI。

## 1. 当前生产链路

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

### 1.2 DeepResearch

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

### 1.3 已启用但未进入原生图的 fan-out

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
deep_research/v2 search ─┘      ├─ provider waves + request-local budget/diagnostics
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

## 10. DeepResearch v2 与进度事实源

新 run 默认启动 `deep_research/v2`，旧 v1 只负责历史读取和在途恢复。v2 使用固定六个 branch slot、五阶段 branch pipeline、静态 join 和 `NativeExecutionPolicy.max_parallel_tasks` 有界并发；inactive slot 写确定性 no-op patch。

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
- 真实 quick search 与正文 fetch smoke 已通过；5 秒连续查询 benchmark 会受到上游 timeout/cooldown 影响，最新样本成功率 70%。
- 真实 DeepResearch 与 Windows W01～W05 已完成：13 阶段进度、运行中重启恢复、多 run 隔离、最终报告、历史 Artifact 操作和实时 Artifact 广播均有真机证据，详见计划目录 `results.md`。
- 固定三类别真实 DeepResearch 样本完成率 2/3、nearest-rank P95 270.548s；政策样本 support rate 0.75，WebGPU 样本因持久化 cooldown 诚实 no-results。验收已完成，但 cooldown 突发容量仍是下一轮首要质量优化项。
- 最终文本使用独立 `final_assistant` channel，只投递 SessionDB 与 websocket；历史错误 assistant receipt 会被兼容收敛且不生成完成凭证。
