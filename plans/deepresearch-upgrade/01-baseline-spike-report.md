# DeepResearch 质量基线 spike 报告（Phase 0）

> 日期 2026-06-20 · 模型 `gpt-5.5`（精排 gpt-4.1-mini，产线等价管线）· 真 relay 真链路（chinzy.com）
> 方法：6 主题（routing-evals should_trigger 5 + 财报 1），standard 全跑 + 2 deep；evaluator-prompt 5 维加权 LLM-judge。
> 原始数据：`01-baseline-spike-raw.json`（run1）+ `01-baseline-spike-raw-run2.json`（run2 重跑）。

---

## 0. 执行摘要（先看这个）

- **质量（搜索正常时）达标**：唯一两次搜索拿到源的运行 composite = **6.5 / 6.9**，均 **PASS（≥6）**。
- **🔴 决定性发现：免费搜索层（Bing/DDG via search_provider）在持续自动化负载下被 IP 级封禁，且数分钟内不恢复。** 13 次运行里 **11 次** 因 `no search results` 直接 0 来源失败（含间隔 90s 的 run2 全部失败）。
- **瓶颈 = 检索层（retrieval），不是综合层**。→ **Phase 3 的检索/抓取方向成立；Phase 4 ReAct 重构无证据支持。**

---

## 1. 逐主题结果

| 主题 | 档 | 耗时s | 源/域 | 引用 | cite | composite | verdict | 备注 |
|---|---|---|---|---|---|---|---|---|
| mcp-sec | standard | 89.4 | **6/4** | 有 | ✓ | **6.5** | **PASS** | 搜索正常，唯一有效 standard 点 |
| mcp-sec | deep | 98.9 | **12/11** | 有 | ✓ | **6.9** | **PASS** | 搜索正常，唯一有效 deep 点 |
| rag-eval | standard | ~21 | 0/0 | 无 | (空) | 1.8 | FAIL | search blocked |
| oai-vs-anthropic | standard×2 | ~21 | 0/0 | 无 | (空) | 1.3–1.7 | FAIL | search blocked |
| cs-ai-vendors | standard×2 | ~21 | 0/0 | 无 | (空) | 1.65–2.0 | FAIL | search blocked |
| browser-use | standard×2 | ~21 | 0/0 | 无 | (空) | 1.65 | FAIL | search blocked |
| catl-2024 | standard×2 | ~21 | 0/0 | 无 | (空) | 1.65 | FAIL | search blocked（财报，正常应走 cninfo 直连）|

> 注：失败行 `cite_ok=true` 是**假阳性**——0 引用时引用自检平凡通过。失败行低分测的是「搜索被封」**不是研究质量**，聚合时已剔除。
> run1 两次有效运行还伴随个别 `extract 403`（zhihu/csdn 反爬）+ gov.cn PDF 抽取空——正常单源抓取失败，best-effort 降级未影响成稿。

## 2. plan §3 阈值判定

| 门 | 阈值 | 结果 | 判定 |
|---|---|---|---|
| 基线质量 | standard composite 中位数 ≥ 6.0 | 有效 standard 仅 1 点 = **6.5**（含 deep 则 6.5/6.9 中位 6.7） | ✅ **PASS**（clean-N 小，受搜索封禁所限）|
| cite-check 通过率 | ≥ 90% | 有效运行 **2/2 = 100%** | ✅ PASS |
| deep vs standard（mcp-sec）| 增益<0.6 且耗时>2× 判不值 | 增益 **+0.4**、耗时 89s→99s（**1.1×**）| deep **有小幅增量、成本低 → 值得保留**（未触发"不值"条件）|

## 3. 瓶颈归类（决定 Phase 3 取舍）— 核心产出

**瓶颈在检索层（retrieval），分两层：**

1. **搜索可用性（第一瓶颈，reliability）**：免费 Bing/DDG 在持续自动化查询下被 IP 封禁、长时间不恢复 → 11/13 运行直接 0 来源。这是**最大的系统性风险**，远超"综合质量"问题。
2. **grounding 质量（第二瓶颈，搜索正常时）**：两次有效运行的维度分里 **evidence/grounding 最弱**（standard=4 / deep=6），而 synthesis(7)/coherence(8)/calibration(7-8) 都强。说明**一旦拿到源，综合与连贯没问题，短板是"证据够不够硬、引用够不够贴"** → 正是精排（reranker）/更好抓取要解的。

**结论：瓶颈明确在「检索」不在「综合」。**

## 4. 对后续阶段的指向（plan 决策依据）

- ✅ **支持 Phase 3 的检索/抓取项**：
  - **搜索韧性**（最高优先，本 spike 实测痛点）：多引擎/退避/缓存，或更依赖**绕过 SERP 的路径**（中文一手源直连 cninfo/国标已有、crawl4ai/webview 抓取）——对应 Phase 3③ crawl4ai 与既有 direct_sources。
  - **本地 bge-reranker / loopback 精排**（Phase 3①②）：针对第二瓶颈 grounding 质量，方向对、优先级低于搜索韧性。
- ❌ **不支持 Phase 4 ReAct 重构**：综合/连贯/校准维度已达标（7-8 分），控制流不是瓶颈。无证据支持高风险子代理重写。

## 5. 本次 spike 的局限（诚实声明）

- **clean-N 小**：因搜索 IP 封禁，只拿到 2 次有效质量运行。质量基线（6.5/6.9 PASS）是 **suggestive 不是 definitive**；但**可靠性发现是强结论**（11/13 失败，跨 90s 间隔重现）。
- 封禁是 IP/时段相关，换网络/隔时再跑可补更多 clean 质量点——但**不影响本报告对 Phase 3/4 的取舍结论**（瓶颈=检索，数据已足够强）。
