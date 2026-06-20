# §6.0 搜索可靠性改造 — 真机 windows-mcp E2E 结果（2026-06-20）

> 真实 DeskPet App（master backend，HARD GATE 过：`[backend_launch] Dev python=...\.venv\Scripts\python.exe`）。
> 真模拟人：windows-mcp Click 聚焦 → Clipboard 中文 → Ctrl+V → Enter。证据 = tauri-dev2.log.err + OutPut/Research/*.md + 截图。

## 🔴 真机 E2E 揪出并修复的严重 bug（本轮最大价值）

**bug**：普通搜索返 0 结果（最常见因 Bing/DDG 被 IP 封——正是 Phase 0 spike 的病灶）时，`deepresearch` 有个 `if not url_to_question: return no_results` **early-return 在 §4.4 直连源块之前** → **直连源（§6.0-A，bypass SERP、专为封禁兜底）永远跑不到** → §6.0-A 的全部价值在"最需要它"的时候失效。

**为何单测 + 5 轮代码评审 + 子代理 100% 评估全都漏掉**：它们一直**提供搜索结果**（mock search 返非空），从没走到"搜索全空→直连应兜底"这条路。**只有真机 E2E（真 Bing 被封）才暴露。**

**修复**（commit `ab14e04`）：搜索 0 结果不再 early-return，继续走 fetch(空)→score→§4.4 直连源→最终"no usable passages"兜底真全空。+ 回归测试 `test_research_run_direct_sources_run_even_when_search_empty`（搜索空时直连源仍跑）。83 单测绿。

## 用例结果

| 用例 | 判定 | 硬证据 |
|---|---|---|
| 环境门 | ✅ | `[backend_launch] Dev python=...\.venv` + Application startup + 8100 |
| **TC-A1 直连源生效(核心)** | ✅ **PASS** | 修复后真机：**wikipedia API 5 次 + arxiv API 7 次**（`zh/en.wikipedia.org/w/api.php?action=opensearch`、`export.arxiv.org/api/query`），direct 异常 0；报告"向量数据库技术综述…"**引用 3 个 arxiv.org 源、引用自检通过**。 |
| **修复前对照** | ❌→✅ | 修复前同类研究：wikipedia/arxiv/cninfo API 调用全 **0**，报告仅 csdn 2 源（直连源被 early-return 跳过）。 |
| **§6.0-A 兜底价值验证** | ✅ | TC-A1 报告"**1 个独立域名(全 arxiv)**"——web 搜索薄/被封，**全靠 arxiv 直连源撑起整份报告**，正是 §6.0-A 设计目的。 |
| 工具更名(Phase1 回归) | ✅ | 真机工具调用为 `deepresearch(...)`（截图可见 `调用 deepresearch({"depth":"deep"...})`），非 research_run。 |

## 诊断方法（透明）
真机发现"直连源 0 调用"后，用 venv python import 隔离测 `deepresearch()`（mock 空搜索 + stub 直连 fetcher）复现 → 定位 early-return → 修 → 同隔离测确认直连源 fire（cninfo called + direct_sources_hit=['cninfo']）→ 真机重启复测 TC-A1 PASS。**import 隔离仅用于 bug 诊断，E2E 判定以真机 log + 报告为准。**

## 观察
- 任务漂移（用户问 A→桌宠研究上下文旧 B/CATL）在修复前那轮又出现一次；属已知 agent-loop 层问题（WI-4a 目标锚定需设 /goal），与 §6.0 无关。本轮 TC-A1 topic 未漂移。

## 结论
§6.0-A 直连源在真实运行栈生效（wikipedia/arxiv API 真调用 + 报告引用 + 搜索被封时兜底）。**真机 E2E 完成了它的核心职责——抓出一个单测/评审全漏的严重 bug 并验证修复。** 剩余用例（TC-A2 连续负载/TC-A3 cninfo/TC-A4 边界/TC-X1 兜底/TC-B1 bing-cdp）为补充覆盖。
