# 交接（补充）：任务漂移 2026-06-21 新复现证据 — 给另一个 session 处理

> **日期**: 2026-06-21
> **状态**: 📋 待修复（本文档是**新复现证据补充**；根因诊断 + 修复方向见既有 [2026-06-20-task-drift-fix-HANDOFF.md](./2026-06-20-task-drift-fix-HANDOFF.md)）
> **一句话**: 在 deepresearch 子代理 fan-out 真机测试（2026-06-21）中，**任务漂移又复现 2 次**——桌宠对全新请求漂去研究上下文里的旧主题；且 backend log 证 **Fix B（`task_drift_user_request_injected` 把本轮原话注入 deepresearch）确实在触发，但漂移仍发生** → 说明 Fix B 单独不足，根因（单一 `default` 会话从不切分）仍未解。

---

## 0. 先读既有交接（不重复其内容）

根因、`cbdf855` 实锤、Fix A（re-anchoring）/Fix B（user_request 注入）已做、修复方向（D1 会话切分作用域 / D5 deepresearch 传原话）——全在：
- [`plans/2026-06-20-task-drift-fix-HANDOFF.md`](./2026-06-20-task-drift-fix-HANDOFF.md)
- 核心根因：聊天硬编码单一 `session="default"`（[main.py:3771](../backend/main.py:3771)），旧历史直灌新任务上下文；CATL/宁德时代是历史里占压倒性的"吸引子"主题。

---

## 1. 本次新复现（2026-06-21，windows-mcp 真机，硬证据）

环境：Tauri dev 真机（Dev python 本树 backend），干净会话，relay gpt-5.5 已连接。我用 windows-mcp 真模拟人工在桌宠输入框逐条发以下请求。**对比"我发的话"与"LLM 实际研究并落盘的报告主题"（报告文件名 = LLM 生成的 topic slug，可读）**：

| # | 时间 | 我发的原话（verbatim） | LLM 实际研究/落盘的报告 | 是否漂移 |
|---|---|---|---|---|
| 1 | 16:12 | `帮我深度调研 钠离子电池 的工作原理、产业现状与代表企业…` | `钠离子电池的工作原理、产业现状与代表企业…-1782029545.md` | ✅ 未漂移 |
| 2 | 16:13 | `帮我深度调研 固态电池 的技术路线与产业化进展…` | `Sodium-ion-batteries-work-principle-indu-1782029683.md`（钠离子）+ 随后 `固态电池…-1782029909.md` | ⚠️ **漂移**（固态电池→钠离子，被上一条钠离子上下文带偏） |
| 3 | 16:53 | `帮我深度调研 2025年中国新能源汽车出口现状…` | 调度了 6 个子代理但 0 引用未落盘（网络受限）；topic 基本未漂 | （网络受限，无报告） |
| 4 | 17:08 | `帮我深度调研 区块链 的基本原理、发展历程、核心技术…` | `宁德时代2024年报核心数据…-1782033022.md`（fanout 6 子问题）+ 随后 `区块链…-1782033250.md` | ⚠️ **漂移**（区块链→宁德时代/CATL，同既有交接的 CATL 吸引子） |

**两次漂移的吸引子都是电池/CATL 系旧主题**（钠离子、宁德时代）——与既有交接里 "Rust→宁德时代" 的 CATL 吸引子**完全同源**，进一步坐实"旧历史主题占压倒性 → 压过新请求"。

> 注：fan-out 把漂移放大可见——漂移后的 `宁德时代` 请求被拆成 6 个子问题、派 6 个子代理（`subagent_scheduled kind=research run_id=default.dr-0..5`），即"错的主题被认真地并行研究了 6 份"。

---

## 2. 关键新发现：Fix B 在触发，但漂移仍发生 → Fix B 单独不足

backend log（`plans/manual-results-2026-06-21-wi8-deepresearch/tauri-dev.log`）锚点统计：

```
6 × task_drift_user_request_injected tool=deepresearch req_len=60
2 × task_drift_user_request_injected tool=deepresearch req_len=67
```

- `task_drift_user_request_injected` = **Fix B（acb69a0）的硬锚点**：分发层对声明 `user_request` 字段的工具（deepresearch）**确实注入了本轮原话**。
- **但漂移仍然发生**（见 §1 #2/#4）——说明：**Fix B 把原话注入 deepresearch 的 topic/prompt 还不够**，因为漂移发生在**更上游**：LLM 在**生成 deepresearch 工具调用的 topic 参数时**就已经被旧上下文带偏（raw 历史直灌），等到 Fix B 注入时 topic 已经是错的。
- 即：Fix B 治"工具内部用错请求"，但治不了"LLM 一开始就选错主题去调工具"。**根因仍是会话上下文污染（D1 会话切分未做）。**

---

## 3. log 记录（原始证据位置）

| 证据 | 位置 |
|---|---|
| 本次真机 backend 全量 log | `plans/manual-results-2026-06-21-wi8-deepresearch/tauri-dev.log`（structlog 走 stderr→tauri dev 重定向） |
| Fix B 锚点 | 同上 grep `task_drift_user_request_injected`（6×req_len=60 + 2×req_len=67） |
| fan-out 调度（漂移主题被并行研究 6 份） | 同上 grep `subagent_scheduled kind=research run_id=default.dr-0..5` |
| 漂移落盘报告（LLM 实际 topic） | `DeepResearch/Sodium-ion-batteries-…-1782029683.md`、`DeepResearch/宁德时代2024年报…-1782033022.md` |
| 既有交接的原始 log/截图 | `plans/manual-results-2026-06-20-deepresearch/tauri-dev.log.err:182`（p5s2_tool_call_args_dump 我发 Rust 它研究宁德）+ `screenshots/R2-1-report-done.png` |
| 本次证据归档 | `plans/manual-results-2026-06-21-wi8-deepresearch/EVIDENCE-task-drift-2026-06-21.txt`（本次复现 log 摘录，见下） |

---

## 4. 给接手 session 的建议（不替你拍方案，给方向）

1. **根因仍在 D1（会话切分）**：既有交接的 D1（按任务切分 session 作用域，不再单一 `default` 直灌全历史）是治本。本次证明只做 Fix A/B（re-anchoring + user_request 注入）不足以挡住"LLM 生成工具调用时就选错 topic"。
2. **验证口径**（沿用本次方法，可复现）：windows-mcp 真机发一个与近期历史无关的新主题（如 Rust / 区块链），grep backend log 看 **LLM 生成的 deepresearch `topic` 参数**是否=你发的主题；落盘报告文件名是否=你发的主题。**不要**只看 Fix B 锚点有没有触发（它会触发但治不了漂移）。
3. **fan-out 交互注意**：fan-out 已上线（本仓库 2026-06-21），漂移会被放大成"6 份错主题报告 + 6 个子代理"。修 D1 后建议同样用 fan-out 场景复验（漂移修好后，6 个子代理应研究的是**你发的主题**的 6 个子问题）。
4. **本次未动任何漂移修复代码**——本会话只做 deepresearch fan-out（plans/2026-06-21-deepresearch-subagent-fanout/），漂移是测试中**观察到的既有问题**，原样交接。

---

## 5. 一句话给接手 session

> 任务漂移在 2026-06-21 又真机复现 2 次（固态电池→钠离子、区块链→宁德时代，吸引子=CATL 系旧主题）；backend log 证 Fix B（user_request 注入）在触发但漂移仍发生 → 确认 Fix B 单独不足，**根因 D1（单一 default 会话从不切分、旧历史直灌）仍需实现**。证据见 `plans/manual-results-2026-06-21-wi8-deepresearch/`。根因/修复方向见 `plans/2026-06-20-task-drift-fix-HANDOFF.md`。
