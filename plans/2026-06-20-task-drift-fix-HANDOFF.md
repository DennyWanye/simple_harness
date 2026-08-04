# 交接：桌宠「任务漂移」修复（给另一个 session 冷启动接手）

> **日期**: 2026-06-20　**状态**: 📋 待修复（本文档是调研交接，实现放另一个 session）
> **一句话**: 桌宠对一个**全新、无关**的请求，会漂去研究/处理**上下文里占压倒性的旧主题**——根因是聊天单一 `session="default"` 从不按任务切分，旧历史直灌新任务的上下文。

---

## 1. 症状 + 实测证据（硬证据，不是推测）

**实测（2026-06-20，windows-mcp 真机 E2E）**：在桌宠聊天框发
`帮我深度调研 Rust 异步运行时 Tokio 的架构与竞品对比`，
但 backend log 显示 LLM 实际调用的是
`deepresearch(topic="宁德时代…CATL 财报…")`——**完全漂到了上下文里的旧 CATL 任务**。

- 证据 1：`plans/manual-results-2026-06-20-deepresearch/tauri-dev.log.err:182`
  `p5s2_tool_call_args_dump … name='deepresearch' … args_preview='{…"topic":"宁德…'`（我发 Rust、它研究宁德）。
- 证据 2：桌宠 `<think>` 气泡当场承认冲突："There's a conflict here: the user is asking for [Rust]… There's a conflict…"——**模型看到了我的 Rust 请求，但被旧 CATL 上下文压过去了**。
- 证据 3：截图 `plans/manual-results-2026-06-20-deepresearch/screenshots/R2-1-report-done.png`（最终生成的是宁德时代报告）。

> ⚠️ 这次漂移发生在既有修复 `cbdf855`（2026-06-19）**之后** —— 说明那次修复没覆盖全，详见 §3。

---

## 2. 根因（已被 `cbdf855` 用真实 state.db 实锤 + 本次补充确认）

`cbdf855` commit message 原文实锤：
> companion 所有任务**共用单一 session="default" 且从不切分**（872 msgs/一个月，CATL 主题占比压倒性）。新任务继承旧历史，两个搬运工把旧任务反复送回上下文。

本次补充确认（grep 实证）：
- `backend/main.py:3771` `session_id = ws.query_params.get("session_id", "default")` —— 桌宠聊天**硬编码单一 "default" 会话**。
- 全 backend `grep new_session|new_task|reset_context|task_boundary|split.*session` —— **桌宠聊天侧零命中**（code 模式有 code-XXX session，但桌宠聊天永远 "default"）。**没有任何新任务检测/会话切分/清上下文机制**。
- `main.py:3173-3190`：多窗口（主桌宠 + 消息面板）**共享同一 "default" 会话** fan-out —— 任何会话切分方案都要处理这个多窗口共享。

**疾病本体 = 单一 "default" 会话从不按任务切分。** 三个"搬运工"把旧主题灌进新任务上下文（见 §3）。

---

## 3. 已有修复覆盖了什么、漏了什么（关键）

`cbdf855` + `plans/2026-06-19-task-scope-context-isolation/00-plan.md` 处理了**两个**搬运工，但**漏了第三个**，且**没动根因（会话切分）**：

| 搬运工 | 机制 | 状态 |
|---|---|---|
| ① compaction 滚动摘要"任务棘轮" | prior 摘要强制增量保活，CATL 永不脱落 | ✅ 已修（`cbdf855` WI-1，`context_compressor.py`：加"任务边界优先"+【早前已结束任务】分段，打破增量棘轮）|
| ② L3 召回同 session 零时近门控 | `_session_affinity` 同 session 恒 1.0，旧 CATL 记忆恒召回 | ✅ 已修（`cbdf855` WI-2，`retriever.py`/`manager.py`/`config.py` 加时近降权；后记 commit `0d60dfe` 修空召回）|
| ③ **raw 最近对话历史** | state.db 里 "default" 会话的 raw 消息（CATL 占压倒性）**直接进 LLM 上下文** | ❌ **未修** —— 这正是我这次漂移的路径 |
| 根因·会话切分 | 新任务起新作用域 | ❌ **未做** |
| WI-3 goal_mode 常驻锚定 | 始终注入当前目标 | 🟡 `cbdf855` 明确 **deferred**（"先不做"）；后来作为 agent-loop **WI-4a** 落地（`agent_loop.py:622-644`），**但只在设了 `/goal` 时触发，且只锚外层、进不到 deepresearch 内部** |

**为什么漂移在修复后仍发生（机理）**：
- 启动 log `tauri-dev.log.err:71` 显示 `compaction_enabled context_window=1000000 threshold=0.80` —— compaction 要到 **1M 窗口的 80%** 才触发。
- 我那次 E2E session 很短（远没到 80万 token）→ **compaction 根本没触发** → WI-1 的"摘要任务作用域化"**完全没生效**（它只在 compaction 触发时起作用）。
- 于是 state.db 里 "default" 会话的 **raw CATL 历史直接进上下文**（搬运工③，没人管），把我的 Rust 新请求压过去。
- 我那次也**没设 `/goal`** → WI-4a 目标锚定也没触发。

→ **结论：短会话 + 大窗口下，raw 历史是主漂移路径，现有修复全部落空。**

---

## 4. 候选修复方向（给接手 session 选，带优先级/风险）

> 建议组合 **D1（根治）+ D5（deepresearch 侧兜底）**；D2/D3 是较轻的过渡。

- **D1 ⭐ 根治：按任务切分会话作用域**。检测"明显新任务"（话题跳变）→ 起新作用域（新 session_id，或把旧消息标记 inactive/archived 不再进上下文）。
  - 风险：①"新任务"检测的可靠性（误切会断连续对话）②多窗口共享 "default" fan-out（main.py:3173）要兼容 ③用户有时确实想接着上文——需给"继续上一话题"的退路。
- **D2 过渡：当前请求主导 / 旧无关历史降权**。组装上下文时，若最新用户请求与历史主体明显无关，截断/降权旧无关 raw 历史。比 D1 轻、不改会话模型。
- **D3 过渡：raw 历史窗口按相关性裁剪**。限制进上下文的 raw 最近消息条数，或按与当前请求的相关性过滤（embedder 已有）。
- **D4：隐式 goal 化**。每条新用户请求自动派生一个隐式 goal，让 WI-4a 常驻锚定对每个任务都触发（不止显式 `/goal`）。注意仍只锚外层、进不到 deepresearch 内部。
- **D5 ⭐ deepresearch 侧兜底**（与 §deepresearch-upgrade plan 协同）：工具内部 `llm_call`（research_tools.py 的 plan/synth）**看不到外层目标锚定**——把**用户原始请求文本**显式传进工具的 plan prompt（而非让它从被污染的上下文里推 topic），硬化主题贴合。这条独立于 D1，能直接堵住"工具内部选题漂移"。

---

## 5. 关键文件 + 调查 leads（接手起点）

**先读**（理解前序分析，避免重复造轮子）：
- `plans/2026-06-19-task-scope-context-isolation/00-plan.md`（根因分析 + WI-1/2 已做 + WI-3 deferred）
- `git show cbdf855`（两个搬运工的具体修法）

**会话/历史注入链**（D1/D2/D3 的战场）：
- `backend/main.py:3771`（session_id="default" 硬编码入口）· `:3173-3190`（多窗口共享 default fan-out）· `:2609`（`get_messages(sid, limit=50)` 一处历史读取，确认桌宠主链路的 raw 历史 load 点 + limit）
- `backend/deskpet/agent/assembler/`（上下文组装：raw 历史怎么拼进 LLM messages，找确切 load 点 + 条数上限）
- `backend/deskpet/memory/manager.py` / state.db `get_messages`（raw 历史数据源）

**已有部分修复**（D2/D3 可在此叠加）：
- `backend/deskpet/agent/context_compressor.py`（WI-1，仅 compaction 触发时生效）
- `backend/deskpet/memory/retriever.py`（WI-2 L3 时近降权）
- `backend/deskpet/agent/agent_loop.py:622-644`（WI-4a 目标锚定，仅 /goal + 仅外层）

**deepresearch 侧兜底（D5）**：
- `backend/deskpet/tools/research_tools.py` 的 `deepresearch()` plan 阶段（约 :998 `_PLAN_PROMPT`）+ `_call` 闭包（:1764）—— 把用户原始请求显式带进 plan prompt。

---

## 6. 复现 + 验证

**可靠复现**（漂移是概率性的，取决于旧历史权重，需放大）：
1. 在桌宠 "default" 会话里**先灌入大量主题 A 的对话**（如反复聊 CATL/宁德，或直接用现成的：state.db 里已有 872 条 CATL 历史）。
2. 然后发一条**明显无关的主题 B 请求**（如 "深度调研 Rust Tokio"）。
3. 看 LLM 实际调工具的 topic / 回复主题是 B（正确）还是漂回 A（bug）。
4. **按项目手工测试纪律**：windows-mcp 真机模拟点击+输入，抓 backend log（`p5s2_tool_call_args_dump` 的 topic）+ 截图判定，**不可用脚本回放替代**（feedback_real_e2e_not_script_replay）。

**修好的判据**：同样"重 A 历史 + 发 B 请求"，LLM 调工具的 topic = B、回复主题 = B；连续 ≥3 次不漂。

---

## 7. 坑 / 注意事项

1. **别假设 compaction 兜底**：threshold=80% × 1M 窗口，短会话永不触发 → WI-1 的摘要作用域化对短会话**无效**，raw 历史才是主路径。
2. **多窗口共享 "default"**（main.py:3173）：任何会话切分/重置要兼容主桌宠窗 + 消息面板窗同步。
3. **WI-4a 只锚外层 + 只 /goal**：别指望它根治；它进不到 deepresearch 内部 sub_questions（工具内 llm_call 不经 agent loop）。
4. **deepresearch-upgrade plan 协同**：D5 与 `plans/deepresearch-upgrade/00-upgrade-plan.md` 相关，但本 bug 是**上下文层**的、范围更广（影响所有工具/回复，不止 deepresearch），建议作为**独立修复**，不要塞进 deepresearch plan。
5. **真实 state.db 是金矿**：`%APPDATA%/deskpet/data/state.db` 有真实的 872 条 CATL 历史，是复现 + 验证的现成素材。

---

## 8. 一句话给接手 session

桌宠聊天是**一个永续 "default" 会话、从不按任务切分**；前人修了 compaction 摘要 + L3 召回两条搬运工，但**raw 最近历史直灌**这条没修、**会话切分根因**没动 —— 短会话（compaction 不触发）下新任务被旧主题压垮。**根治走 D1（按任务切分作用域）+ D5（deepresearch 把用户原话显式传进 plan）**，按手工测试纪律真机复现验证。
