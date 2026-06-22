# 压缩触发器 companion 不触发 — 诊断结论（2026-06-22）

> 方法：在 `agent_loop.py` should_compress 检查处临时加 `compaction_trigger_debug` 打点 INFO（已移除，agent_loop.py 无 diff），真机 windows-mcp 跑桌宠对话捕获每轮真值。window=8000(阈值6400)+ctx_observability=true。

## 实测真值（debug 打点）

| 回合 | iter | ctoken_est | wm(working_messages) | last_real | should_compress | 压缩发生? |
|---|---|---|---|---|---|---|
| web_search 多迭代 | 1 | 1815 | 1815 | 0 | False | 否（对：<6400） |
| web_search 多迭代 | **2** | **17643** | **1902** | **17643** | **True** | **否（进了压缩块但 no-op）** |
| 长文essay1 | 1 | 2111 | 2111 | 0 | False | 否 |
| 长文essay2 | 1 | 2107 | 2107 | 0 | False | 否 |

## 根因（彻底定位，非触发器 bug）

1. **触发器本身工作正常**：多迭代回合第2次迭代 `_last_real_prompt_tokens=17643`（来自第1次 LLM 真实 prompt_tokens）→ `ctoken_est=17643 ≥ 6400` → `should_compress=True` → **正常进了压缩块**。单测 `test_compaction_bestpractice_upgrade` 也证明 working_messages 大时压缩正常 fire。**触发器没坏。**

2. **`compress()` 压的是 `working_messages`（对话历史），而它结构性地恒为 ~2100 token**：
   - 即使前面刚生成 essay1 的 **3195-token 长答复**，下一轮 `wm` 仍只有 ~2107 —— **长答复根本没被带进下一轮 working_messages**。
   - companion 模式按 **memory-召回架构**：`l2_top_k=5` 截断 + 长答复不以原始消息形式留存，LLM 看到的 17643-token 上下文大头是 **memory 注入**（BudgetAllocator 层2 按需召回），**不在 working_messages、压缩器压不了**。
   - 所以 `compress()` 拿到的可压内容只有 ~1900 token（head/tail 保留后 middle 近空）→ **no-op → 无 context_compacted 日志 → 无 toast**。

3. **单轮纯聊天**：`_last_real_prompt_tokens` 每回合重置 0，单轮不迭代第二次 → 触发器拿不到真值 → 更不触发。

## 结论：**不是 bug，是 companion 模式的设计后果**

- 压缩（4 层防线第4层 ContextCompressor）**为"长原始对话历史"设计**（code 模式 / 长 agentic 任务，working_messages 真的会涨大）。单测覆盖此场景且通过。
- **companion 聊天模式下 working_messages 结构性很小**（memory-召回架构，非原始长历史）→ 压缩在此天然 inert，`compress()` 正确 no-op。这解释了"prompt_tokens 17619 远超阈值仍不压"——因为那 17619 的大头是 memory 注入，**不是可压的对话历史**。
- **1B-2 压缩 toast** 因此在 companion 模式基本不会显示（设计使然），但在 **code 模式 / 长 agentic 任务**（working_messages 涨大触发真压缩）会正常显示。1B-2 观测代码路径由单测 `test_observability_on_emits_event` 验证（flag ON→压缩发生→yield 事件+字段正确）。

## 给用户的决策点（非我单方能定）

companion 模式"compaction 不工作"是否要改，取决于产品取舍：
- **(A) 接受现状**：compaction 是 code/agentic 模式的能力；companion 靠 memory-召回管上下文，l2_top_k=5 已够。1B-2 toast 属 code 模式特性 + 单测验证。**推荐**（不动 memory-召回护城河）。
- **(B) 让 companion 也能压**：需改 companion 上下文装配——要么把更多原始对话带进 working_messages（改变 memory-召回行为，碰护城河，风险高），要么让压缩/缓存覆盖 memory 注入（与 BudgetAllocator 层2 职责重叠）。属**独立架构 plan**，非快修。

> companion 模式真正的"上下文大头"是每轮重发的 ~15k memory 注入（层2 BudgetAllocator），不是对话历史。若要优化 companion 上下文占用，方向应是层2（memory 注入预算/缓存），对应 plan 的 OH-3 写入分级 / 1B-3 自适应阈值等 P3 项，而非层4 压缩。
