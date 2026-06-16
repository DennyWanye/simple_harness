# 上下文压缩升级（对标 Claude Code / Hermes / OpenClaw 最佳实践）— 实施 PLAN

> **状态**: 📋 规划中（待子代理 2 轮挑战）
> **目标**: 把 DeskPet 的上下文管理从"0.6 截断 + 0.8 扁平摘要（且阈值反序、默认关）"升级到业界最佳实践:
> **单调递进的级联压缩（microcompact → 结构化摘要 → 截断兜底）+ pre-flush 防丢任务 + 目标钉死不可压 +
> 触发用剩余 token buffer**；并把跨会话"记住用户"接进**已有的 L1/L3 记忆系统**（不重造）。
> **关联**: 调研结论见本会话对话；前序修复见 `plans/2026-06-16-context-compaction-optim`（#1~#4 + 触发 bug）。
> **最后更新**: 2026-06-16

## 0. 现状（这次会话真机摸清 + 已修）

DeskPet 两层上下文管理（真实代码）:
- **BudgetAllocator**（`deskpet/agent/assembler/budget.py`, `budget_ratio=0.6`）: 每轮 assemble 时把 prompt
  按 `窗口×0.6` 裁——丢低优先级组件、缩记忆段。**一直开，是"不崩"的兜底主力**。本会话修了它 token→char
  对 CJK 反向超预算的 bug。
- **ContextCompressor**（`deskpet/agent/context_compressor.py`）: 到 `窗口×0.8` 把对话中段（保 first_n=3
  +last_n=6）用 haiku 摘成**一条**，重注入 `[目标锚定]`。**出厂默认关**（`config.py:419 compaction_enabled=False`）。
- **触发**（`agent/agent_loop.py`）: 本会话修了"压缩永不触发"的核心 bug（改用 `count_messages_tokens
  (working_messages)` 直接数即将发送的消息）。
- **已有记忆系统（关键，别重造）**: L1 文件记忆（`MEMORY.md`/`USER.md`，always-on 注入）+ L2 会话 DB
  （SQLite+FTS5）+ L3 向量（BGE-M3 按需召回）+ session 摘要器（age_days=30 归档）。ContextAssembler
  已经在每轮拉 memory 组件。**"分层记忆"的地基已存在**。

**调研对标后的 4 个落后点**（带来源，见 §2）:
1. 🔴 **阈值反序**: 0.6 先截断丢、0.8 才摘要保——业界相反（廉价淘汰→摘要→截断兜底）。
2. 🔴 **摘要不分段**: 压成一条，没 completed/current/pending → 这是"压缩后看不到任务"的根因。
3. 🟡 **触发用比例**而非剩余 token buffer；compaction 默认关。
4. 🟡 **压缩层没接已有记忆**: 压掉的内容没 pre-flush 到 L1/L3，跨会话"记住用户"在压缩侧缺位。

## 1. 设计原则（对标业界）

- **单调递进、越往后越重**: 先不调模型的廉价手段，最后才动语义。
- **结构化保任务**: 摘要强制分段，任务态/TODO/目标永不被"压没"。
- **目标钉死**: 用户最初目标 + 用户画像放进**从不参与压缩**的 system 段（对标 Claude Code 把 CLAUDE.md 钉死）。
- **触发对齐"还够不够写摘要"**: 用剩余 token buffer，不用裸比例。
- **复用已有 L1/L3 记忆**: pre-flush 落到现有记忆系统 + 按需召回，不新建存储层。

## 2. 业界最佳实践对标（来源）

- **Claude Code（泄露源码逆向）**: 五级级联 `工具结果预算→microcompact(清陈旧 tool_result,不调模型)→
  context collapse→autocompact→reactive`；autocompact 触发 = `有效窗口 − ~13k token`（剩余 buffer,非比例）；
  **9 段结构化摘要**(意图/概念/文件/错误/求解/用户消息/**TODO/当前工作/下一步**)；CLAUDE.md 永久钉死不压；
  circuit breaker(连压 3 次失败即停)；已知坑: 摘要会幻觉用户没说过的指令(issue #46602)→必须基于原文可校验。
  - 来源: oldeucryptoboi.com/blog/context-compaction-deep-dive · justin3go.com/.../09-context-compaction-in-codex-claude-code-and-opencode · github.com/anthropics/claude-code/issues/46602
- **Hermes（长寿命记忆 agent）**: prompt-memory **卡字符上限逼策展**的精炼用户画像(always-on) + SQLite 按需
  召回 + memory `add/replace/remove` 增量维护；记忆编辑下个 session 才生效(单会话稳定)。来源: hermes-agent.org
- **OpenClaw**: **multi-part compaction** + **pre-compaction flush(压前先落盘 task state/TODO/identifiers)** +
  `/handoff`(objective/progress/files/pending/blockers) + 三层记忆作用域。来源: github.com/andrehuang/claude-claw
- **通用**: 滚动摘要有 drift(第3趟低频信息蒸发)→ 用**锚定增量摘要**(并入持久 state 非每次重建)；OpenCode
  永远保最后 2 轮 user turn 全文 + 压完 replay 最后一条 user 消息。

## 3. 范围与改法（WI，按优先级 = 调研落后度）

### WI-1 触发改"剩余 token buffer" + 阈值单调递进（治落后点 1、3）
- 触发判据从 `prompt ≥ 窗口×0.8` 改为 `剩余 = 窗口 − 已用 ≤ buffer`（buffer 取 `max(摘要预留, 输出预留)`，
  如 ~窗口的 8% 或固定值，对齐 Claude Code 剩 ~13k）。`should_compress` 接受/计算剩余。
- 与 BudgetAllocator 的序: **明确 BudgetAllocator 的 0.6 截断是"最后兜底"**，compaction 的结构化摘要应在
  截断**之前**生效（即压缩触发阈值要 < 截断阈值，或让 assembler 在 compaction 之后再兜底）。**WI-0 先量两者
  实际作用点**(assemble 在 agent_loop 哪一步、compaction 在哪一步)，确认能否让"摘要先于截断"。

### WI-2 microcompact 层（廉价清陈旧 tool_result，不调模型）（治落后点 1）
- 在结构化摘要**之前**插一层: 把**陈旧的 tool_result**（非最近 K 个工具调用的）正文换成
  `[旧工具结果已清理]` 占位，**不调模型、不动对话语义**。最近工具调用受保护。
- 复用本会话已建的 `count_messages_tokens` 判大小；与现有 `_sanitize_tool_pairs` 协同(别造孤儿 tool)。
- 命中后若已降到阈值下 → 跳过重摘要(省一次 haiku 调用)。

### WI-3 结构化摘要升级（4 段 → 完整 schema）+ 保最近 user 原文（治落后点 2）
- `_SUMMARY_SYSTEM` 从本会话的 4 段扩到对标 Claude Code 的 schema:
  **意图/目标 · 已完成 · 进行中(当前任务) · 关键事实与决策 · 涉及的文件/产物 · TODO/待办 · 下一步**。
- **保最近 user turn 全文**: last_n 保证含最近 ≥1 条完整 user 消息原文（不只摘要）；压完确保最近用户请求仍在尾段。
- **锚定增量**: 若上一次压缩已产出 summary，新摘要**并入**而非从头重建(防 drift)。需在 messages 里识别上次
  `[压缩摘要]` 标记 + 传给摘要器作 prior state。
- **防幻觉**: prompt 强调"只基于待摘内容,不得编造用户未说的指令"(对标 issue #46602)；保留本会话已加的防反射。

### WI-4 pre-flush 落盘 + 目标钉进不可压段（治落后点 2、4，根治"看不到任务"）
- **pre-compaction flush**: compress 真正摘掉中段**之前**，把"当前任务 + TODO + 关键决策"写进**已有 L1
  记忆**(`MEMORY.md` 或专用 task-state 文件)，对标 OpenClaw。这样即使摘要丢细节,落盘的任务态可被下轮/下个
  session 召回。**复用现有 L1 写入 API,不新建存储。**
- **目标钉死**: 把"用户最初目标/约束 + 用户画像"放进**从不参与 `_partition` 压缩的 system 段**(现在 [目标锚定]
  是压缩后注入,改成 always-on 的不可压 system,对标 CLAUDE.md 钉死)。需确认 `_partition` 把这类 system 永久排除。

### WI-5（独立后续，可单列一期）跨会话分层记忆补强 — 复用 L1/L3
- 调研指出桌宠"记住用户"该走 Hermes 式: 精炼用户画像(常驻) + 按需召回。**DeskPet 已有 L1(`USER.md`)
  + L3 向量召回**——本 WI 只做**接线/策展强化**: ① 压缩 pre-flush 进 L1/L3(WI-4 已含) ② 给 `USER.md`
  加字符上限逼策展(对标 Hermes ~3.5k) ③ 确认 ContextAssembler 召回足够。**评估后决定是否单列一期,不在
  本期硬塞。**

### WI-6 完成 WI-1~4 后，把 compaction 默认打开（治落后点 3）
- `config.py:419 compaction_enabled` 默认 False → True（**且必须先做完 P-B 修复**，否则窗口取错模型过早压）。
- 仅当 WI-1~4 + P-B 全绿 + 真机验证后才翻默认。

## 4. 测试（每 WI 单测 + 集成 + 真机）
- WI-1: 剩余 buffer 触发的边界(刚好够/不够写摘要);compaction 触发点 < 截断点(集成)。
- WI-2: 陈旧 tool_result 被占位、最近的保留、不产孤儿 tool;命中后降到阈值下则跳过摘要。
- WI-3: 摘要含完整 schema 段;最近 user 原文仍在;锚定增量(给 prior summary 不从头重建);防幻觉/反射断言。
- WI-4: pre-flush 真写进 L1(读回校验);目标 system 段永不进 `_partition` middle(压多轮仍在)。
- 真机(windows-mcp): 开 compaction + 小窗口,长会话/长 agentic turn 触发压缩 → ① 日志结构化摘要含
  【进行中/当前任务】② 压缩后追问"刚才在干嘛/继续"桌宠仍记得(根治看不到任务) ③ L1 落盘可见。
- 回归: `pytest tests/ -k "compact or compress or token or budget or assembler or memory or agent_loop"` 全绿。

## 5. 降级/边界
- 任一级失败 → 退回上一级/原消息,绝不崩(对齐现有 safe-fail + circuit breaker 思想)。
- microcompact 不调模型 → 零额外延迟/成本。
- pre-flush 写 L1 失败 → 不阻断压缩(best-effort)。
- compaction 仍默认关直到 WI-1~4+P-B 全绿(WI-6 才翻)。

## 6. 文件清单
- 改: `deskpet/agent/context_compressor.py`(microcompact 层 + 结构化 schema + 锚定增量 + pre-flush 接 L1 +
  目标 always-on) · `agent/agent_loop.py`(剩余 buffer 触发 + 与 assemble 序) · `deskpet/agent/assembler/
  budget.py`(明确 0.6 为兜底,序对齐) · `config.py`(WI-6 翻默认)
- 关联: 复用 L1 记忆写入 API(`deskpet/memory/*` 或现有 L1 模块,WI-0 定位) · 复用本会话 `deskpet/agent/tokens.py`
- 测: 各 WI 单测 + 集成 + 真机 manual-results
- 前置依赖: **P-B 修复**(`plans/2026-06-16-effective-llm-model-resolution`)——窗口取对模型,否则触发点错
