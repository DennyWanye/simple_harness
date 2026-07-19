# DeepResearch 模块架构与状态

> **最后更新**：2026-07-19

## 2026-07-18 当前生产事实（v6）

- 新建调研默认进入 immutable `deep_research/v6`；v1-v5 仅用于历史读取、在途恢复和兼容 continuation，不再依赖开发环境覆盖切换版本。
- Q1 官方精确事实链路已经端到端可执行：语义规格 -> 国家统计局年度公报通用归档发现
  -> 同权威页面抓取 -> ref-only 证据账本 -> 标量事实提取 -> 完整性硬门 -> canonical
  terminal commit -> session/websocket/artifact exactly-once 投递。
- 实测问题“2024年中国总人口和出生人口分别是多少？优先国家统计局”返回年末人口
  `140828万人`、全年出生人口 `954万人`，引用可访问的
  `https://www.stats.gov.cn/sj/zxfb/202502/t20250228_1958817.html`。
- 适配器不硬编码答案或文章 URL：只配置官方年度公报归档入口，根据冻结语义时间范围
  选择带年份的同权威链接；归档发现与目标抓取共享同一有界 fetch 预算。
- 各阶段耗时同时写入 `metrics.jsonl` 和 trace child spans。最终 r9 总耗时 5.08s：
  归档发现 2.78s、目标页抓取约 1.10s、事实提取 153ms、评估/渲染 354ms；现有
  120s parent / 20s page 上限没有被接近，本 slice 不支持继续缩短。
- 重启语义已真机验证：一条用户消息、一条 `final_assistant`、一个 ArtifactCard、一个
  completed run，启动恢复没有重投递；历史恢复不再显示原始 artifact JSON。引用展示
  使用已成功抓取的 canonical/requested URL，最终地址的权威校验仍保留。
- 证据位于 `plans/2026-07-17-deepresearch-answer-contract-stability/evidence/`；门禁为
  v6/official/fetch 91 passed、v5/recovery/delivery 281 passed、前端消息/artifact
  30 passed，TypeScript/Vite relay build 通过。
- 完整 production graph 已覆盖 official exact fact、comparison、Top-N、policy 与 open research；所有 lane 统一写 fact-batch，主 graph 唯一负责准入、评估和三态终态（`completed` / `partial` / `insufficient_evidence`）。
- continuation head、snapshot closure、control journal、terminal manifest 和 canonical delivery 均进入 durable store；`generate_now` 双击只创建一个 command，并完整经历 accepted → observed → settled → consumed。
- 真实 UI 三次 2024 exact 均返回本地化的 `140828 万人` / `954 万人`；2019 双指标只准入出生人口 `1465 万人`，以 `partial` 和 `continue_research` 诚实交付。completed、partial、insufficient/generate-now 和重启/history 均已通过。
- 健康网络三次端到端 p50 `17.099s`、p95 `18.942s`、max `19.147s`；单页 p50 `10.977s`、p95 `11.405s`、max `11.453s`，0 timeout/cancel/预算违规。
- 用户指定的 `zai-org/GLM-5.2` 在 Relay 动态目录中的实际别名为 `sf-glm-5.2`；基础模型与 problem-pipeline 预分析模型均已按该可调用 ID 切换，后端默认值、已有 userdata 与前端首选值保持一致。Relay 当前未把上下文元数据接入运行时 resolver，因此 `model_info.BUILTIN` 暂时同时钉住 alias/canonical 为名义 1M、有效 95%、750K compaction、384K recall sweet spot；provider-advertised effective limit 仍是后续正确事实源。模型/上下文回归 `58 passed`；源码 Tauri 重启和真实 SC-STATS-2 run `81090268…` 均解析为 1M，Context usage 显示 950K/750K/384K，GLM 请求全 HTTP 200，durable final/artifact/delivery 一次完成。配置/桥接此前回归 `27 passed`、problem-pipeline 配置回归 `6 passed`，证据见 `evidence/glm52-live-20260719/`。
- 最终 release identity `manifest=8e4a7ea4… / implementation=32f65877…` 已分别完成真实 UI 三态：completed `e58b0281…`、partial `2ed15e00…`、重启后 generate-now insufficient `eeab90ba…`；同 userdata 重启恢复历史且 `recovered_deliveries=0`。
- 最终门禁：后端 `815 passed`；前端 `822/822 tests passed`；TypeScript、Vite production build、Rust 73 tests 和 cargo check 通过。证据见 [`execution-results.md`](../plans/2026-07-17-deepresearch-answer-contract-stability/execution-results.md)。
- 发布收尾边界：功能与验收已通过；release identity fixture 已纳入受控 Git 提交，巨大 dirty worktree 中的无关变更未混入。

## 历史生产事实（v5，2026-07-17）

- 新调研默认进入 immutable `deep_research/v5`；v1-v4 只用于历史读取和兼容恢复。
- v5 以版本化 `ResearchBrief`、维度覆盖、证据准入、readiness、报告质量审计和三态交付（完整、部分、证据不足）为事实源。建模 LLM 输出不合约时会记录降级并使用确定性 brief，不会让整个调研失败。
- Search Gateway 搜索与抓取预算按研究维度确定性轮询，避免结果列表前部维度挤占固定 passage 预算；CAPTCHA、登录墙、无正文和低相关页面不能进入 admitted evidence。
- 查询生成只使用聚焦目标和维度词并有 240 字符硬上限；中文国家统计局约束可进入 official-statistics 路径。产品比较先做双主体广域发现，再执行有界第一方补证，避免组合 `site:` 查询只命中一侧。
- 报告链在生成式 synthesis/repair 不可用时可从已接纳 passage 生成确定性抽取式交付；`partial` 只在存在实际可用证据时发布，非终态修复异常不会抹掉已有结果。
- `continue_research` 会创建带 parent/checkpoint lineage 的新 run，只补未覆盖维度；重启后历史 run、终态与继续补研动作从 durable store 恢复。
- `generate_now` 的 UI active control 不会再被无 control 字段的中间事件覆盖；服务端可从空 action payload 解析当前 head checkpoint。长 Search/Fetch effect 每 0.5 秒观察 durable control，到 30 秒 settle fence 后以业务 `cancelled` stage result 收敛，不把用户控制性取消误投影为节点永久失败。
- Win11 x64 已完成安装版真实 UI 验证：教育政策问题走完 v5 搜索、抓取、评分、缺口评估与诚实 `insufficient_evidence` 交付，继续补研与重启历史恢复通过。该次真实 run 没有足够 admitted evidence，因此不宣称生成了合格完整报告。
- 修复前五类 Win11 矩阵曾出现 101 documents / 0 admitted passage，并暴露 AI profile、弱调研路由、raw URL 投影和 elapsed 翻倍问题。修复后真实 UI 复验：AI Top 10 `9670a357...` 为质量 75 的 `partial`（5 个有效来源）；国家统计局人口题 `51781063...` 为质量 80 的 `completed`（11 个有效、5 个第一方来源、4/4 核心覆盖）；产品比较 `2b79cd88...` 为质量 60 的诚实 `partial`（9 个有效、1 个第一方来源）。三条 DB/UI elapsed 误差均小于 2 秒，默认卡未显示 raw query/URL。
- 立即生成真实 UI run `6ea6a3a4...` 在点击后约 0.227 秒 observed，settle/consume 后形成唯一 `insufficient_evidence` 终态；全部节点 succeeded、硬失败 0。单次控制主链 PASS，重复点击/重连/强杀恢复分支仍 PENDING。
- 当前结论是“核心检索/建模/交付修复与单次立即生成主链已通过代表性真实 UI 复验”；Gate F 总体仍为 PARTIAL，updater、卸载、立即生成恢复分支和完整固定场景审计尚未全部完成。
- Playwright 1.61.0 / Chromium Headless Shell r1228 随冻结后端离线封装；本次 scope 不包含 Win10、Hyper-V、虚拟机或 Windows Sandbox。
- v5 每个 durable workflow 节点现在统一记录 `started_at`、`ended_at`、`duration_ms`、`attempt` 与终态；`workflow.db` 的 `workflow_node_attempts` / `trace_spans` 是可跨重启查询的耗时事实源，`deepresearch_stage_timing` 同步镜像到结构化日志和 `metrics.jsonl`。成功、等待、取消、可重试失败和永久失败均覆盖，旧的 v5 `deepresearch_v2_stage.duration_ms=0` 误导性镜像已停止生成。
- fetch 节点进一步记录 `deepresearch_fetch_attempt_timing`，覆盖 robots、Scrapling、HTTPX、正文抽取、Playwright、Edge CDP 与 Jina 的实际耗时、状态和错误码。事件按 `run_id` 关联，不保存 query、URL、正文、标题或 prompt。当前这些 fetch 子阶段只进入可轮转的 `metrics.jsonl`，尚不是 30 天 durable trace child spans；30 天精确事实源目前只覆盖 workflow 节点级 timing。

> **用途**: deep research（深度调研）模块的历史实现盘点；当前生产事实只在本页保留摘要。
> 全局项目状态见 [`PROJECT_STATUS.md`](./PROJECT_STATUS.md)；本文件是 deep research 这一模块的深入架构档。
> **历史说明**：下方 v5、v4 与 2026-06-21 legacy pipeline 内容只保留演进记录，不应用于判断当前状态；当前事实以本页顶部 v6 摘要和 [`SEARCH_GATEWAY_DEEPRESEARCH.md`](./SEARCH_GATEWAY_DEEPRESEARCH.md) 为准。

## 历史生产摘要（v4，2026-07-15）

- 新调研默认进入 immutable `deep_research/v4`；旧版本仅用于历史读取和在途恢复。
- 宽主题技术情报按稳定 taxonomy 搜索和去重，不再接受泛化实体或页面元数据拼盘。
- 报告只发布 3～8 个过门发现，不为凑数放入弱候选；固定提供一页式执行摘要、组合建议、分主题 Top 技术、逐项成熟度/风险/日期/引用和方法局限。
- Session 默认只显示一张聚合进度卡；展开后 13 个阶段逐步呈现“做了什么、得到什么、为何降级/跳过”，失败时不伪造报告并提供幂等重试。
- 最终同进程连续真机 run `43e851a0...` 与 `59975eb1...` 均通过工作流与当前 17 项专业报告质量门；旧 run `66720bcf...` 及建议口径不一致的早期改进样本已明确保留为失败基线。

---

## 1. 一句话结论

**"旧 research vs 新 deepresearch" 是伪命题。** 桌宠当前唯一真实存在、接线可用的 deep research
是 `deepresearch`（原名 `research_run`，已于 `5b7d4e3` 更名；`backend/deskpet/tools/research_tools.py`，2282 行），它**已吸收各 plan 的成果**
（分层打分 / BGE-M3 / LLM 精排 / query 扩展 / 中文一手源 / reflection / cite-check / 报告落盘）。
传说中的"新 ReAct 子代理"**代码不在仓库**（从未被 git 跟踪、无历史、无 .pyc），唯一区别只是**控制架构**
（自主 ReAct vs 固定流水线），**能力上一项都没多**。

---

## 2. 现状实现：`deepresearch` 管线 + 能力

**入口**：`skills/builtin/deep-research/SKILL.md`（v0.2.0，路由到 `deepresearch`）。
**接线**：模块级 `_register_deepresearch_tool()`（:2260/:2280）自注册；`main.py` 注入 3 钩子
（主 LLM `set_live_llm_call` :674 / 廉价精排模型 gpt-4.1-mini `set_rerank_llm_call` :692 / BGE-M3 scorer `set_semantic_scorer` :1146）。**桌宠现在真能调到它。**

> ⚠️ 下表内 research_tools.py 的**逐阶段行号系更名前（`deepresearch` @:1338 之前）的旧锚点**，
> 更名后整体下移约 +370 行（函数体 ~:1338-2000），未逐条重核；以函数名为准、行号仅供大致定位。

| 阶段 / 能力 | 状态 | 证据 |
|---|---|---|
| ① Plan 拆题（3-6 子问题，失败降级用原题） | ✅ | research_tools.py:998-1008（旧锚点）|
| ② Query 扩展（multi-query + HyDE） | ✅ 默认开 | :1010-1015 |
| ③ Search（DDG + site: 定向官方域，区域感知） | ✅（**仅 DDG**，不接付费） | :1017-1057 |
| ④ Fetch（trafilatura → JS渲染 → Jina 三级） | 🟡 JS渲染仅 cdp-edge/Windows 落地 | default_extract:862-949 |
| ⑤ 过滤（字典站/AI生成/乱码/长度门） | ✅ | _passage_from:1088-1103 |
| ④.4 中文一手源直连（巨潮/国标 + EDGAR 兜底） | ✅ | :1133-1175 |
| ④.5 Reflection 反思补搜（仅 deep 档 2 轮） | ✅ 但固定 2 轮无收敛 | :1177-1209 |
| ④.6 BGE-M3 语义精化（取 max，只抬不埋） | 🟡 embedder 在场才生效 | :1211-1237 |
| ④.7 LLM 精排 reranker（候选池≤24，失败如实标） | ✅ 但仅云端 relay 注入 | :1241-1258 |
| ⑤ Synthesize（含口径提示，失败降级段落罗列） | ✅ 同函数内成文 | :1282-1296 |
| ⑥ Cite-check（脚注号对齐，缺失⚠️/未用裁掉） | ✅ 不校验 claim-evidence 对齐 | :1298-1318 |
| ⑦ 落盘 + artifact 卡片 | ✅ `DeepResearch/`（安装目录下，2026-06-21 由 OutPut/Research 迁移）+ `DeepResearch/index.md` 总索引（倒序/可点开） | _save_report:2174 + _update_deepresearch_index:2007 |

**打分子系统**（`research_scoring.py`，纯函数）：分层权威分（4 档 + 中文源 + 自媒体降到 2.0）`:203-218`；
新鲜度分 `:228-246`（⚠️见 §4 真 bug）；合成分 `:249-256`；多样性 `:259-285`（≥5 域、单域≤25%）；
主题速度启发式 `:288-297`。

**档位**：light(3问/2URL/8段/1轮) · standard(5/4/12/1) · deep(6/5/16/2)。

---

## 3. Plan 目标 vs 现状

plan 想要的能力**绝大多数已做完**（分层打分/BGE-M3/精排/query扩展/一手源/reflection/cite-check/落盘/多引擎降级）。
**未做的只有**：本地 bge-reranker（可选档，Phase-future）、crawl4ai webview 反向链路（GATE 未做）、
以及**ReAct 子代理架构整套**（受限只读工具子集 / 三重预算闸 / 分离 synthesis）——后者代码已丢失。

### ⚠️ 仓库内部架构方向冲突（需用户裁决）
- **v8-plan（06-13）明确写「单 agent，桌宠单机，不 fan-out subagent」** —— 白纸黑字拒绝子代理化。
- **subagent-status（06-19）转向「独立 ReAct 子代理」**，并宣布 v8/ROADMAP「已过时」。

两者方向对立，不是平滑迁移而是架构掉头。

---

## 4. recency 真 bug（**已于 Phase 2 修复**，2026-06-21 复核确认）

**历史 bug**：`score_recency` 依赖发布日期 `date_str`，但旧 `default_extract` 返回字典**根本没有 `date` 键**
（只有抓取时刻 `fetched_at`）→ 普通网页源永远传空串 → `score_recency` 永远返回默认 3.0 → 合成分里 0.2
权重恒为常数，排序实际只由 authority + relevance + depth 决定。
**现状（读码复核）**：`default_extract` 已抽取并返回 `date`（trafilatura metadata，含 JS 渲染兜底回填，
research_tools.py:888/898/926-928/:947），`deepresearch` 主流程已 `score_recency(str(payload.get("date") or ""), ...)`
真正喂日期（:1569-1570）。**recency 维度现已生效。** 仅 direct_sources 路径仍硬编码 recency=8.0（:1652）。

其它弱点：可观测性不足（异常吞进 errors + log.debug，无阶段耗时/命中率指标）；
loopback/ollama 用户拿不到 LLM 精排（main.py 仅非 loopback 注入）；JS 渲染仅 Windows 真落地；
工具 handler 层 `_handle_deepresearch` 测试覆盖待补。

---

## 5. 文件丢失记录（沙箱回滚事故）

| 文件 | 状态 |
|---|---|
| `research/DEEPRESEARCH-HANDOFF.md`（378 行） | ❌ 不可恢复（声称的 commit `c4f7e21` 是无效对象名，全历史无踪）|
| `backend/deskpet/agent/subagents/`（ReAct 子代理 10 文件） | ❌ 不在仓库（从未被 git 跟踪 / 无 .pyc）|
| `research/PENDING-blind-spots-section.md` | ✅ 用户已手动补回（内容并入本档 §6）|

根因：新文件未即时 `git add+commit` → 被沙箱回滚清除。

---

## 6. 调查边界 / 已知盲区（来自 PENDING，下一个设计者需自行补齐）

> ⚠️ 本次调查**全程静态读代码，一次都没运行过 research**。需要**运行时真相**的结论均为静态推断。

**🔴 高优先（几乎肯定需要，基本空白）**
- **现状真实质量基线**：旧 research_run 实际跑出来报告多好/多差、耗时、失败率 —— 零运行证据。
- **LLM 模型与成本**：一次 deep 档烧多少 token / 钱 / 延迟 —— 未查。
- **单机资源约束**：research 占多少内存/CPU、BGE-M3 + Edge 无头渲染开销、能并发几个 —— 未量化。

**🟡 中优先**：前端/UI 呈现形态（research 结果在桌宠里怎么显示/追问）；`v8-reference/evals` + `scorers.py`
（现成质量评测工具，**plan 过时 ≠ evals 过时**）；搜索引擎在中国网络的真实可用性；记忆系统耦合
（子代理的 `memory_search` 线没查）；真实失败模式分布。

**🟢 低优先**：用户真实使用数据（可能没埋点）；历史决策的"为什么"。

**结论**：本档在**静态代码层面**完整可靠，但**不是全部真相**。最划算的第一步是 **Step 0 质量对比 spike**
（真跑 research_run），一次性填掉"质量基线 / 成本 / 耗时 / 失败模式"几个高优先盲区。

---

## 7. 演进建议

**不删 `deepresearch`（原 research_run）。** 优先级：① ~~先做 Step 0 质量 spike 拿真实基线~~（已完成，见下）
→ ② ~~修 recency 真 bug~~（已修，见 §4）等确定项 → ③ 用 spike 数据决定"原地升级 vs 重建 ReAct 子代理"。详细方案见
[`plans/deepsearch/00-optimization-plan.md`](../plans/deepsearch/00-optimization-plan.md)。

### 进度（2026-06-20，升级 plan = [`plans/deepresearch-upgrade/00-upgrade-plan.md`](../plans/deepresearch-upgrade/00-upgrade-plan.md)）
- ✅ **Phase 1 更名** `research_run`→`deepresearch`（codex；76 单测；子代理评估 100%；真机 E2E PASS）
- ✅ **Phase 2** 修 recency 真 bug + coverage 可观测（codex；80 单测；评估 100%）
- ✅ **Phase 0 质量 spike** 完成（[`01-baseline-spike-report.md`](../plans/deepresearch-upgrade/01-baseline-spike-report.md)）：质量达标（6.5/6.9 PASS），但 **🔴 免费 Bing/DDG/百度持续负载下 IP 级封禁（11/13 运行 0 来源）= 检索层是第一瓶颈**；综合维度已达标 → **不支持 Phase 4 ReAct**。
- ✅ **§6.0 搜索可靠性改造（实现完成）**：A 直连源(wikipedia/arxiv/s2/wikidata,默认开,bypass SERP) + B bing-cdp 浏览器搜索(opt-in) + C SearXNG + D 硬化 + 观测(route集合/direct_source_empty/elapsed.direct);codex 并行 + Lead 集成;子代理评估 100%;83 单测绿。
  - 🔴 **真机 E2E 揪出并修复严重 bug**：搜索 0 结果(SERP 被封)时 early-return 跳过直连源 → §6.0-A 在最需要时失效;单测/5轮评审全漏(总提供搜索结果),真机才抓到。已修(commit `ab14e04`)+真机复测 TC-A1 PASS(wikipedia API 5+arxiv API 7,报告引用 arxiv,搜索薄时全靠直连兜底)。
- 🟡 **Phase 3 reranker 等（待执行）**：原 §6 三项(本地 bge-reranker/loopback 精排/crawl4ai)治 grounding 第二瓶颈,次优先。
- ⚠️ **生产 bug（spike 暴露）**：真实用户连续多次深度调研也会撞"搜索被封→无结果"，非仅 spike 现象。

---

## 关键文件
- 现实现：`backend/deskpet/tools/research_tools.py`（`deepresearch` @:1338，`_handle_deepresearch` @:2111，2282 行）+ `research_scoring.py`
- 入口：`backend/deskpet/skills/builtin/deep-research/SKILL.md`
- 接线：`backend/main.py` :669-698（live/rerank LLM 钩子）+ :1120-1146（BGE-M3 scorer）
- 评估基线：[`plans/2026-06-19-deep-research-current-vs-plan-assessment.md`](../plans/2026-06-19-deep-research-current-vs-plan-assessment.md)
- 目标架构 plan：`plans/2026-06-13-deep-research-v8/` · `plans/2026-06-14-deep-search-best-practices/` · `plans/2026-06-14-research-reranker/` · `plans/2026-06-16-crawl4ai-fetch-tier/`
- 盲区原档：`research/PENDING-blind-spots-section.md`
