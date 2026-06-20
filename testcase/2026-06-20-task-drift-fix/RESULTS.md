# 任务漂移修复 — windows-mcp 真机测试结果

> **执行**: 2026-06-20，windows-mcp 真模拟人（剪贴板中文输入 + SetCursorPos/SendInput 真点击 + 真实运行栈 log 硬证据 + 截图）
> **被测**: Fix A（组装层 Tier1 锚定/重定性 + Tier2 话题门控）+ Fix B（deepresearch 原话注入），最终 commit 见下「修复迭代」
> **环境 HARD GATE**: ✅ `[backend_launch] Dev python=...\backend\.venv\...`（master 代码，非 frozen exe）；✅ `p4_embedder_ready is_mock=False`（真 BGE-M3）
> **历史前提**: ✅ `session='default'` 627 条、最近主题 CATL/宁德时代（实测确认）

---

## 0. 真机测试触发的 3 轮「修复→复测」迭代（核心价值）

真机暴露了纯靠 plan 推不出的问题，逐轮修复复测：

| 轮 | 真机现象（硬证据） | 根因 | 修复 | commit |
|---|---|---|---|---|
| 1 | Tier1 锚定+重定性已注入（`anchor_applied=True relabel_applied=True`）、Fix B 已注入（`req_len=32`），**但 `p5s2` topic 仍漂回宁德/CATL** | Tier1 软指令不足以压住 5 轮 CATL 的对话惯性（handoff 预言） | 开 Tier2 默认 + 把过高的 `len>50` 合取阈值改可配置 `topic_shift_min_len=16`（Rust 请求仅 32 字被漏判） | b1c7749 |
| 2 | `topic_shift_gate=True` 但 `gate_sim=None l2_truncated=False` → Tier2 形同虚设 | 诊断日志 `task_drift_sim_skip reason=encode_timeout`：BGE-M3 subprocess 被 vector worker 回填 627 条 + 研究负载抢锁，encode >1s 撞 1500ms 组件 fan-out budget（bump 超时会丢 L2，obstacle A） | Tier2 加**词法内容词重叠兜底**：embedder 超时→退化到零延迟词法信号；跨域漂移 token 重叠≈0 必被抓 | fd4d820 |
| 3 | `l2_truncated=True shift_path='lexical' l2_count_in=5→out=1`，`p5s2` topic = 完整 Rust（含 async-std/smol/monoio/glommio 竞品） | — | **漂移根治** | fd4d820 |

---

## 2. 用例结果

| Case | 维度 | 判定 | 硬证据 |
|---|---|---|---|
| **TC-1 ★** | 核心漂移修复 | ✅ **PASS** | `task_drift_context_gate l2_truncated=True shift_path=lexical l2_count_in=5 l2_count_out=1`；`p5s2 topic="Rust 异步运行时 Tokio…竞品 async-std、smol、monoio、glommio"`（**不含 CATL**）；桌宠真在 `web_extract_article github.com/smol-rs/smol`（Rust 竞品）；报告完成"已保存为 Markdown"。截图 `TC-1-run1-rust-smol-research.png` / `TC-1-run1-report-done-rust.png`，log `TC-1-run1-log.txt` |
| **TC-2** | Tier1 锚定 | ✅ **PASS** | 每轮 `task_drift_context_gate ... anchor_applied=True` |
| **TC-3** | Tier1 重定性 | ✅ **PASS** | 有 L2 轮 `relabel_applied=True`（`l2_count_in>0`） |
| **TC-4 ★** | 追问连续性不被误伤 | ✅ **PASS** | step2「它的主要竞争对手有哪些」→ `l2_truncated=False`（"它"代词 anaphora 豁免）+ web_search query=`"…宁德时代 比亚迪…"`（正确指代 CATL 竞品）；step3「继续」→ `l2_truncated=False`（2字<10 短路豁免）+ run_shell `path='/tmp/catl_2024_annual.pdf' keywords=['营业收…']`（**正确续 CATL 财报分析、未失忆**）。截图 `TC-4-step{1,2,3}-*.png` |
| **TC-5** | Fix B 原话直传 | ✅ **PASS** | `task_drift_user_request_injected tool=deepresearch req_len=32`（=用户原话长度精确吻合） |
| TC-6 | Tier2 行为 | ✅ **PASS（语义已更新）** | 原"默认关"已改为**默认开**（真机证明 Tier1 不足）。真机确认 `topic_shift_gate=True` 生效、`shift_path` 字段（embed/lexical）、跨域截断、同域保留 |
| TC-8 | 单一主题/话题切换 | ✅ **PASS（t1）** | turn1「Python asyncio 事件循环」（相对 CATL 是切换）→ `l2_truncated=True`（正确截断 CATL）+ 桌宠答"事件循环 EventLoop 反复驱动 Task"（**连贯 asyncio、零 CATL 漂移**）。t2 同主题深入因 windows-mcp 点击未命中焦点未送达，同域保留已由 TC-4 两追问覆盖 |

### 未单独执行（理由）
- **TC-1b / TC-5b**：TC-1 + TC-8 已覆盖"多个不同跨域主题均不漂"（Rust、asyncio 两域 + CATL→asyncio 切换），且 Tier2 词法截断是**确定性**的（非概率），交叉验证冗余。
- **TC-7（Tier2 embedding 路径）**：真机 embedder 因锁竞争恒 `encode_timeout`（`shift_path=lexical`），embedding 主路真机难稳定触发；其正确性由单测 `test_topic_shift_gate_truncates_on_low_similarity...`（mock embedder）覆盖。词法兜底路已真机充分验证。
- **TC-9/10/11/12**：BC/边界回归（普通工具不误注入 / 首条无历史 / sentinel / 多窗口），由单测 + Fix B 的 `_tool_declares_user_request` 判定逻辑覆盖；本轮聚焦核心漂移 + 头号追问风险的真机闭环。

---

## 3. 结论

**核心修复（漂移根治 + 追问不误伤）已用真机硬证据闭环验证，连续多域不漂。** 修复经 3 轮真机「有问题→修复→复测」迭代收敛（Tier1 不足 → Tier2+min_len → embedder 锁竞争 → 词法兜底），全程单测同步绿。

判定全部基于真模拟人触发后**真实运行栈** log 硬证据（`task_drift_context_gate` / `task_drift_user_request_injected` / `p5s2_tool_call_args_dump` topic）+ 截图，无脚本回放/WebSocket/import 替代（符合 `feedback_real_e2e_not_script_replay`）。
