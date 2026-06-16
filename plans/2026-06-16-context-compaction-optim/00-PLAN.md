# 上下文压缩子系统优化 — 实施 PLAN

> **状态**: 📋 规划中 → 实施
> **目标**: 优化 DeskPet 上下文压缩(context compaction),四方向(用户全选):
> #2 保住当前任务 · #1 token 计数精度/防爆窗 · #3 两套系统对账 · #4 压缩 UX/可观测
> **最后更新**: 2026-06-16

## 0. 现状(测绘结论)
- 两层并联: **BudgetAllocator**(前置, `deskpet/agent/assembler/budget.py`, 按 window×0.6 裁组件)
  + **ContextCompressor**(LLM前, `deskpet/agent/context_compressor.py`, 到 window×0.75~0.83 滚动摘要)。
- 阈值按当前模型真实窗口动态算(`llm/model_info.resolve` 三层) — **设计已好,不动**。
- token 估算: `agent/token_budget.py::_weighted_chars` 已 CJK-aware + 安全偏上(CJK×4 / ASCII×8/7,
  FP-2 真机校准防爆窗) — **基线已好**;但散落多处仍裸 `len//4`(budget.py / assembler 各 component /
  context_compressor._approx_tokens) — **不一致**(=#3 靶子)。
- 摘要: claude-haiku, first_n=3/last_n=6, middle 摘成 ≤512token 一条 prose;摘后注入 [目标锚定]。
- 已知痛点(用户真机踩过): **压缩后"看不到上一轮任务"** → 摘要 prompt 只保"实体/时间/决策",
  没强保"正在做的任务"(=#2 靶子)。

## 1. 范围与改法

### WI-1 (#1+#3 合并) 统一 token 计数
- 新建 `backend/deskpet/agent/tokens.py`:
  - `count_text_tokens(text: str) -> int`: 复用 `_weighted_chars` 的 CJK-aware+安全偏上逻辑;
    **可选 tiktoken**: 仅当 `DESKPET_TIKTOKEN=1` 且 tiktoken 装好+BPE 可加载时用 o200k_base(精度增强),
    否则用启发式(零依赖/离线/中国安全/确定性)。失败静默回落启发式。
  - `count_messages_tokens(messages) -> int`: 逐条 content+tool_calls 走 count_text_tokens + per-msg overhead。
- 把以下散落估算**全部路由到 count_text_tokens**(消除 #3 不一致):
  - `context_compressor._approx_tokens`
  - `assembler/budget.py` 的 `len//4`(×2 处)
  - `assembler/components/*` 的 `_approx_tokens`/`len//4`(memory/persona/preference_profile/time/workspace/workspace_memory)
  - `agent/token_budget.py::_weighted_chars` 抽到 tokens.py 复用(token_budget 改 import,行为不变)。
- 集成测试: 同一段文本经 budget.py 与 context_compressor 估出的 token **一致**;CJK 文本估值 ≥ 字符数(防爆窗)。

### WI-2 (#2) 保住当前任务 — 最高价值,根治"压缩后看不到任务"
- 重写 `ContextCompressor._SUMMARY_SYSTEM`: 从"中立 prose 段"升级为**结构化分段**,显式保活:
  ```
  【进行中/当前任务】← 最重要: 当前正在做的任务 + 最近一条用户请求(短则原文保留)
  【已完成】已完成的步骤/结论/产物
  【关键事实与决策】实体/数字/日期/决定/用户让记的数据
  【待办/下一步】未完成项
  (空段省略;用用户语言;不杜撰)
  ```
  摘要上限 512→768 token(结构化稍长,但保任务不丢)。
- `_build_goal_anchor`: 已"goal 永不丢"。增强: 锚里附**最近一条用户消息摘要**(若 compress 传入),
  让"当前任务"在 goal 之外也有一道锚。新增可选参 `last_user_msg`。
- compress() 调用处(`agent/agent_loop.py`)把最近用户消息传入。
- 测试: 构造"目标在 middle 多次出现"的会话,压缩后断言摘要含【进行中/当前任务】段 + 最近用户请求关键词。

### WI-3 (#4) 压缩 UX/可观测 + auto_resume 不误触
- **可观测**: compress 命中处 emit structlog `event="context_compacted"
  before_tokens= after_tokens= reduction= summarized_msgs= model= trigger=`(threshold/compact_at)。
  (grep 锚点,排查"何时压了/压了多少")
- **auto_resume 不误触**(用户那次"停下来总结"困惑): `agent/auto_resume.py` 触发前判断——
  若**本回合刚收到新的用户消息**(fresh user turn),**不**注入"请暂停继续扩展操作,先总结"这类
  收敛 hint(那是给"自我循环跑飞"用的,不该打断用户新任务)。加 `_has_fresh_user_turn` 守卫。
- 测试: fresh user turn 下 auto_resume 不注入收敛 hint;非 fresh(自循环)仍注入。

## 2. 降级/安全
- tiktoken 全程可选,任何失败→启发式,绝不因 token 计数挂掉主流程。
- 摘要结构化只改 prompt + 上限,压缩失败仍 safe-fail 返原消息(现有行为不变)。
- 字节级 flag: 不引入新默认开关(本优化是对现有压缩的质量增强,默认即生效;无新 opt-in)。

## 3. 验收(DoD)
1. 散落 token 估算全部走 count_text_tokens;budget 与 compressor 对同段文本估值一致(集成测试)。
2. 压缩后摘要含【进行中/当前任务】结构 + 保最近用户请求(测试断言)。
3. compress 命中 emit `context_compacted` 锚点;fresh user turn 不误触 auto_resume 收敛 hint(测试)。
4. 回归: `pytest tests/ -k "compact or compress or token or budget or assembler or auto_resume or agent_loop"` 全绿。
5. 真机: 长会话触发压缩后,追问"刚才在干嘛/继续" → 桌宠仍记得当前任务(不再"看不到上一轮任务")。

## 3.5 真机测试发现的问题(2026-06-16 windows-mcp 测压缩)

> 把压缩窗口临时调小(model_overrides gemma4:e4b 2000)+ 开 `compaction_enabled` 测触发时发现:

- **P-A(核心 bug,已修)**: **压缩在桌宠主聊天里实际从不触发**。根因: agent_loop 压缩检查在
  LLM 调用**前**跑,用 `max(_budget.estimated_tokens, _last_real_prompt_tokens)`——但
  ① `_budget.estimated_tokens` 被 BudgetAllocator 压到 window×0.6(低于压缩阈值 window×0.8);
  ② `_last_real_prompt_tokens` 每条消息开头 line618 重置 0,只在 LLM 响应后(line1038)才拿到
  relay 真值,而单轮聊天消息不迭代第二次 → 检查永远拿不到真值。两路都够不到阈值 → 实测 12 条
  消息/relay 真 prompt 1746 token 仍**完全不压**。**修**: 检查处直接 `count_messages_tokens
  (working_messages)`(即将发送的真实消息大小,调用前可得、不受 allocator 截断、不延迟)作第 4 刀。
- **P-B(配置 mismatch,待评估)**: 压缩窗口取自 config `[llm] model`(gemma4:e4b→32K default),
  但**实际 LLM 是中转站 gpt-5.5(用户面板设了 1M)**。生产里压缩阈值按错模型算(32K 而非 1M)→
  过早压缩。应改成按真实出站模型解析窗口。(本期记录,单独修)
- **P-C(两套系统序,#3 已部分缓解)**: BudgetAllocator 截在 window×0.6、ContextCompressor 触发在
  window×0.8 → allocator 估算路径天然够不到压缩阈值。P-A 的"直接数 working_messages"绕开了它,
  但两阈值的序仍值得统一(budget_ratio 应 ≥ compact threshold,否则估算路径形同虚设)。
- **观察**: deep-research 报告**不进主 loop 上下文**(落盘/给用户,工具结果回灌 LLM 的很小),
  所以"deep-research 多次"并不会撑大主 loop → 靠 deep-research 触发压缩低效;真正撑大主 loop 的是
  普通多轮对话历史累积。

## 4. 文件清单
- 新: `backend/deskpet/agent/tokens.py` + `backend/tests/test_agent_tokens.py`
- 改: `context_compressor.py`(prompt+anchor+_approx_tokens) · `agent/token_budget.py`(复用 tokens) ·
  `assembler/budget.py` + `assembler/components/*`(路由 token) · `agent/agent_loop.py`(传 last_user_msg +
  emit 锚点) · `agent/auto_resume.py`(fresh-turn 守卫) · spec(tiktoken 可选,不强制入包)
- 测: 各模块单测 + 集成(budget↔compressor 一致) + auto_resume fresh-turn
