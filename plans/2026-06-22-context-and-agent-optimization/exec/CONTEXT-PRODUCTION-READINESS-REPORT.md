# 上下文压缩 / 上下文管理 生产就绪调查报告（2026-06-24）

> 用户追问「上下文压缩能不能上生产给正式用户用」，要求最严格判断。本报告补做了 3 个针对性真机测试（TEST①②③），并据此给出修正后的结论。所有证据来自真机日志 + 读码定位，标注「真机观测」vs「读码验证」。

---

## 一、最终结论（严格判断）

| 对象 | 能上生产吗 | 依据强度 |
|---|---|---|
| **核心机制：检索增强截断 + agent 回读** | **能，常见场景已真机证明可用** | 真机直证（TEST③）|
| **CC-2 规划期只读门** | **能，非平凡任务上真机生效**（修正先前"熄火"误判）| 真机全链路（enter→confirm→exit）|
| **B2 LLM 摘要式压缩** | 熄火但无害（被截断层先占）| 真机 + 读码 |
| **95% BLOCK 闸** | 近乎休眠的兜底（测量口径漏算 base，几乎打不响）| 真机 + 读码 |
| **整体** | **常见场景可上；两处窄风险见 §四** | 综合 |

**一句话**：上下文管理的**真正主力（检索增强截断 + agent 回读）真机证明是稳的、不丢信息的**，可以上生产给正式用户用（常见场景）。那些"高级"兜底（B2 压缩、95% 闸）是休眠的，但它们休眠**不影响安全**，因为主力层已经把上下文压力稳稳接住了。

---

## 二、3 个针对性测试结果

### TEST③ — 截断到底丢不丢信息？（最关键的质量测）
**做法**：window=1M，code 模式让 agent 读 main.py（421KB，被截断到 head1500+tail500），然后问只存在于**被截中段**的 `_approx_tokens` 函数（main.py:3729）实现。
**真机观测（tauri-dev-cc2.log）**：agent 行为链 =
1. `read_file main.py` → `p5s2_tool_result_truncated kept_len=3385`（被截）
2. `grep pattern="def _app..."`（定位函数行号）
3. `read_file main.py offset=3718 limit=35`（**精准回读 3718–3753 行，正好覆盖 _approx_tokens 的 3729–3739**）

**结论**：**截断是检索增强的、非破坏性的**。被截掉的中段内容并没丢——agent 用 grep+offset 回读，从磁盘原文件精准取回了它真正需要的那几行。truncator 还在标记里写着 `ref_id=… use fetch_tool_result to read more`，并把全文存进 ref 柜（256-LRU）。所以 agent 至少有**两条**取回路径（grep+offset 回读 / fetch_tool_result）。质量不会因为截断而静默劣化。

### TEST② — 桌子满 95% 的硬刹车能刹住吗？
**做法**：把 window 压到 5000（95%=4750），让真实 prompt 超过它。
**真机观测（tauri-dev-test2.log）**：真实 `prompt_tokens=5680`（=window 的 **113%**），但 `p5s2_token_budget_block` **一次都没响**。
**读码定位为什么（agent_loop.py:850-874 + token_budget.py）**：BLOCK 闸算的是 `check_budget(working_messages)` —— **只数对话历史 working_messages，不数完整 prompt**。而完整 prompt 里那块固定的「系统提示词+工具 schema+技能前言」≈4864 token（被 prompt-cache，cached_tokens=4864 印证）**根本没被计入闸的分母**。
**结论**：所谓"95% 硬刹车"实际是"working_messages 的 95%"。由于截断层把 working_messages 一直压得很小，这个闸在生产（window=1M）里**几乎永远不会触发**——它是个**口径漏算 base、近乎休眠的兜底**。万一触发，恢复是优雅的（读码验证：`yield ErrorEvent(reason="context_budget_block") + 中文建议 + return`，不崩、不卡死，给用户"建议压缩或拆分任务"的提示）。

### TEST① — ref 柜 256 条淘汰后会丢东西吗？
**做法 + 证据**：256-LRU 满了淘汰最老的 ref；`fetch_tool_result` 取一个被淘汰的 ref（读码验证 fetch_tool_result_tool.py:56-68）→ 返回**优雅错误** `{"ok":false,"error":"ref_id … not found … re-run the original tool call to get a fresh ref"}` —— 不抛异常、不崩。
**关键真机佐证（来自 TEST③）**：agent 取回截断内容的**首选策略是从磁盘回读**（grep+offset），**根本不依赖 ref 柜**。
**结论**：对**文件读取**，ref 淘汰**完全无害**——源文件一直在磁盘上，随时能重读。唯一可能真丢的是**不可重跑的临时输出**（如某条 shell 命令的 stdout）在一个会话内被截断 256+ 次后淘汰，属窄边角。

### 附带修正 — CC-2（规划期物理只读）其实是工作的
先前我在"单文件写"小任务上没看到门触发，**误判为熄火**。本轮在一个**非平凡多步任务**上，真机日志同时出现：
`plan_read_only_enter`（只读锁 arm）→ `plan_confirm_gate_awaiting`（挂起等确认，UI 出 [▶执行]/[取消] 6 步计划）→ 点[执行]后 `plan_confirm_received` → `plan_read_only_exit`（锁解除）→ 跑 ReAct。
**结论**：**CC-2 全链路真机生效**，触发条件是"任务非平凡到 maybe_extract_plan 能产出结构化 plan"。这是对先前结论的修正——更深入的真测纠正了一个错误判断。

---

## 三、回答用户的两个问题

1. **能上生产吗？** —— **能（常见场景）**。理由不是那些花哨特性，而是**检索增强截断 + agent 回读这套主力真机证明稳、不丢信息**。桌宠读 421KB 大文件不崩、上下文被稳稳压住、需要细节时精准回读。这是现代 agent（含 Claude Code 自身）的正经做法。
2. **之前的测试有价值吗？** —— **有，而且这轮更值**：B2 测试证实压缩熄火（真）；CC-2 我先前误判熄火，**本轮严格复测纠正为"真机生效"**——这正说明为什么不能停在第一个结果上。3 个针对性测试回答了"生产就绪"这个原始 B2/CC-2 没回答的真问题。

---

## 四、仍存在的窄风险（诚实保留）

1. **临时不可重跑输出的超长会话丢失**：单会话内截断 256+ 次后，最老的 ref 被 LRU 淘汰；若那是不可重跑的 shell stdout，则真丢（文件读取无此问题）。建议：ref 柜大小可配 / 关键临时输出落盘。
2. **BLOCK 闸的 base 盲区**：闸不数固定 base prompt。对 gpt-5.5（真实 window 1M）无害；但若换**小 window + 大工具 schema** 的模型，真实 prompt 可能超 100% 而闸不响 → 模型端截断/质量下降而无告警。建议：闸的分母改为"完整 prompt 估算"而非仅 working_messages。
3. **端到端输出质量未做对照基准**：最干净的"有/无截断答案质量对照"被会话历史污染干扰（检索机制已真机证明，但未跑 A/B 质量打分）。若要签"质量无损"的字，建议补一个干净会话的 A/B。

> 这三条都是**边角 / 跨配置**风险，不是当前 gpt-5.5 + 1M 配置下的常见用户路径问题。

---

## 五、本轮处置（2026-06-24）：Risk 2 已修 + Risk 3 已做 A/B

### Risk 2 —— 已修复 + 单测 + 真机验证 ✅
**改动**（3 文件，BC-safe，默认 0）：给 budget 闸的估算加一条 floor = 上一次真实 `prompt_tokens`（`_last_real_prompt_tokens`，含系统/工具 schema 固定头），口径从"只数 working_messages"升级为"max(working_messages 估算, 真实整 prompt)"。
- [token_budget.py](backend/agent/token_budget.py) `check_budget(... real_prompt_tokens_floor=0)` → `tokens = max(estimate, floor)`
- [context_manager.py](backend/agent/context_manager.py) `check_budget(... real_prompt_tokens_floor=0)` 透传
- [agent_loop.py](backend/agent/agent_loop.py) 调用点传 `_last_real_prompt_tokens`（与压缩触发器同源信号）

**验证**：
- 单测 `tests/test_p5s2_token_budget.py` +3 例（floor 抬高估算触发 BLOCK / floor=0 BC / floor 不下压大估算），**17 passed**；相关 `test_p6_context_manager`+`test_context_config_v2`+`test_token_budget_per_model` **39 passed**。
- **真机**（window=5000）：发读取任务 → `ERROR p5s2_token_budget_block iter=2 tokens=14205 window=5000 ratio=2.84` → **闸真触发了**（修复前同样 113% 只读出 16% 不触发）。优雅退出（ErrorEvent + 建议，不崩）。

### Risk 3 —— 干净 A/B 已做，结论：截断不损质量 ✅
**夹具**：生成 `abtest_truncation.py`（54914 字符，>12000 阈值必被截）植入三个不可猜测标记——HEAD@47（内联头，控制组）/ MIDDLE@27470（深中段，会被截掉，实验组）/ TAIL@54897（内联尾，控制组）。同一文件、同一次运行隔离"是否被截"这单一变量。
**真机执行**（干净会话，window=1M）：让 agent 读该文件并逐字报三个 *_TOKEN 值。
**agent 行为链**（tauri-dev-abtest.log）：`read_file` → `p5s2_tool_result_truncated kept_len=3385`（首读被截，MIDDLE 不在内联视图）→ `grep` 定位三处赋值行 → `read_file offset=1118 limit=12`（中段）+ `offset=2240`（尾段）回读精准片段。
**评分（对 ground truth 逐字核）**：

| 标记 | 位置 | ground truth | agent 答 | 判定 |
|---|---|---|---|---|
| HEAD_TOKEN | 内联头（控制，无需取回）| z4k9-7Q2-alpha | z4k9-7Q2-alpha | ✅ |
| **MIDDLE_TOKEN** | **被截中段（实验，须取回）** | x8w3-5R1f-bravo-deep | x8w3-5R1f-bravo-deep | ✅ |
| TAIL_TOKEN | 内联尾（控制）| 9m2v-Lp0-charlie | 9m2v-Lp0-charlie | ✅ |

**结论**：被截掉的中段值被**逐字精准取回**，与两个无截断控制组答案**同样正确**。agent 还主动说明"首次输出被截、随后定位读取了对应片段"。**→ 检索增强截断在真机上不损答案质量，"质量无损"这句可以坐实**（单文件、3 探针：2 控制 + 1 实验，标记不可猜，实验组逐字命中）。

### Risk 1 —— 已收窄为可配 ✅
**改动**（[tool_result_truncator.py](backend/agent/tool_result_truncator.py)）：ref 柜的两个上限从硬编码改为可配——
- 内存 LRU `max_entries`（默认 256）、磁盘 spill 文件数（默认 400）现支持显式参数 + **env 覆盖**：`DESKPET_TOOL_REF_MAX_ENTRIES` / `DESKPET_TOOL_REF_SPILL_MAX_FILES`（优先级：显式参数 > env > 默认；非法/缺失回落默认，不崩）。
- `_spill_write` 由 staticmethod 改实例方法，剪枝用 `self._spill_max`（不再读模块常量）。默认值不变 = 字节级 BC + 磁盘占用可预期；有"不可重跑临时输出超长会话"顾虑的可把磁盘窗口调大（磁盘便宜）。

**验证**：`test_tool_ref_spill.py` +4 例（参数覆盖 / env 覆盖两上限 / 非法 env 回落 / 参数优先于 env），原有 spill 测全保留（仍 monkeypatch 模块常量作默认）→ **truncator+spill 22 passed**，`p6_context_manager`+`for_session` **23 passed**。

**净状态（全部三条已处置）**：Risk 2 修复（真机闸触发 ratio=2.84）；Risk 3 A/B 证明质量无损（被截中段值逐字命中）；Risk 1 收窄为 env 可配。**上下文管理：核心已真机验证 + 三处已知盲区全部已补/已收窄。**

**净状态**：Risk 2 修复落地（含真机闸触发证据）；Risk 3 A/B 证明质量无损；Risk 1 极窄保留。**上下文管理可上生产给正式用户用的判断从"条件成立"上调为"核心已验证 + 已知盲区已补"。**
