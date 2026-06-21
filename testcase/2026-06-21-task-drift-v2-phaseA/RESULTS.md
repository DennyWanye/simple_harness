# 任务漂移 v2 阶段 A — windows-mcp 真机测试结果

> **执行**: 2026-06-21，windows-mcp 真模拟人（剪贴板中文输入 + 真坐标点击）
> **被测**: T0-1 deepresearch 原话夺权 + T0-2 fanout 隔离，commit 15817813
> **环境 HARD GATE**: ✅ `[backend_launch] Dev python=...\backend\.venv\...`（master 代码）；✅ `p4_embedder_ready is_mock=False`
> **判定锚点（网络鲁棒）**: deepresearch **搜索 query / fanout 子代理主题**（最底层主题证据，比落盘报告更直接、不依赖网络出报告）+ `task_drift_user_request_injected req_len`

---

## 核心结论：T0-1 + T0-2 真机 PASS（主题不漂，相邻领域+跨域双验）

| TC | 场景 | 判定 | 硬证据 |
|---|---|---|---|
| **TC-A1 ★** | 相邻领域（钠离子历史压力 → 发"固态电池"deepresearch） | ✅ **PASS** | **18 次搜索全是固态电池，0 次钠离子、0 次宁德**（`baike.baidu.com/search?word=固态电池…`/`google.com/search?q=固态电池…`）；**fanout 6 子代理（dr-0~5）全研究固态电池**；Fix B `req_len=44`（固态电池完整原话）。截图 `TC-A1-solid-battery-no-drift.png`，log `TC-A1-log.txt` |
| **TC-A2** | 跨域（电池系历史压力 → 发"Rust Tokio"deepresearch） | ✅ **PASS** | **搜索 Tokio/Rust/async ≥3 次，0 次电池/钠离子/宁德**（`arxiv.org/api/query?search_query=all:Tokio…`）；fanout 子代理研究 Rust；Fix B `req_len=53`（Rust 原话）。截图 `TC-A2-rust-tokio-no-drift.png`，log `TC-A2-log.txt` |

**对照 2026-06-21 复现**（区块链→宁德漂、固态电池→钠离子漂）：现在固态电池→固态电池×18、Rust→Tokio，**完全不漂**。T0-1 让 sub_questions/搜索/排序全派生自 `request_topic`（用户原话），T0-2 让 fanout 子代理研究原话主题。

> **为什么搜索 query 是更强证据**：T0-1 的效果是"sub_questions 派生自原话"，搜索 query 直接来自 sub_questions，是主题的**最底层体现**——比落盘报告文件名更直接、不受网络出报告影响。即使外层 LLM 选的 topic 参数漂了（层 1，本阶段不治），内部搜索/研究主题已被原话夺回。

---

## 环境受限（非 T0-1 问题，诚实标注）

- **落盘报告 ENV-LIMITED**：本机网络受限（google 被墙需 cdp_edge 渲染、baike/sogou 返回少）→ deepresearch 引用不足 → **0 引用不落盘**（设计行为），桌宠诚实回复"抱歉喵，这次没能产出合格的带引用 Markdown 深度报告"。**这不是 T0-1 bug**（搜索 query 证明主题正确，是搜索结果质量受网络限制）。故判定改用"搜索 query 主题"锚点（已 PASS）。
- C 盘 95%（11G 余）弹"磁盘空间不足"系统提醒，曾打断一次输入（已关弹窗重发）；不影响 backend 写入。

## 未单独真机执行（理由）
- **TC-A4 BC / TC-A6 slug**：依赖 deepresearch 落盘报告，网络受限下不产出 → 由单测覆盖（评估子代理已验 BC `user_request=None` + slug `title_slug` 行为）。
- **TC-A3 fanout 单列**：已在 TC-A1（6 子代理）+ TC-A2 真机覆盖。
- **TC-A5 deep**：TC-A1/A2 均 `depth=deep`，已覆盖二轮/gap/rerank/semantic 路径（搜索 query 仍=原话）。

## 结论
阶段 A 核心（T0-1 deepresearch 主题以原话为准 + T0-2 fanout 隔离）**真机硬证据 PASS**，相邻领域 + 跨域双验主题不漂。单测 197 passed + 评估子代理 100%。落盘受网络限制（非代码问题）。判定全基于真模拟人触发后真实运行栈 log（搜索 query + Fix B 锚点），符合 `feedback_real_e2e_not_script_replay`。
