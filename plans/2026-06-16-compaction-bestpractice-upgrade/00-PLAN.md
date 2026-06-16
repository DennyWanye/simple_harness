# 上下文压缩升级（对标 Claude Code / Hermes / OpenClaw 最佳实践）— 实施 PLAN

> **状态**: 📋 规划中（已过子代理 2 轮对抗式评审，实现细节钉到可开工 — 见文末两轮评审记录）
> **目标**: 把 DeskPet 的上下文管理从"0.6 截断 + 0.8 扁平摘要（且阈值反序、默认关）"升级到业界最佳实践:
> **单调递进的级联压缩（microcompact → 结构化摘要 → 截断兜底）+ pre-flush 防丢任务 + 目标钉死不可压 +
> 触发用剩余 token buffer**；并把跨会话"记住用户"接进**已有的 L1/L3 记忆系统**（不重造）。
> **关联**: 调研结论见本会话对话；前序修复见 `plans/2026-06-16-context-compaction-optim`（#1~#4 + 触发 bug）。
> **最后更新**: 2026-06-16

## 0. 现状（这次会话真机摸清 + 已修）

DeskPet 两层上下文管理（真实代码）:
- **BudgetAllocator**（`deskpet/agent/assembler/budget.py`, `budget_ratio=0.6`）: 每轮 assemble 时把 prompt
  按 `窗口×0.6` 裁——**只丢低优先级组件 slice（memory/persona/skill/tool 文本）、不碰对话 history**。本会话修了它
  token→char 对 CJK 反向超预算的 bug。**⚠️ 第2轮订正**：它不是"防爆唯一主力" —— 真正把上下文收住的是
  「`l2_top_k=5` 截历史 + BudgetAllocator 裁组件 + token_budget BLOCK gate 硬兜底 + compaction」四道闸（见 WI-1 第2轮纠正块）。
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

### WI-1 触发改"剩余 token buffer"（治落后点 3）— **buffer 公式钉死（第2轮）**
- 触发判据从 `prompt ≥ 窗口×compact_at_pct` 改为 `剩余 = effective_window − 已用 ≤ buffer`。
- **🛑 buffer 不另造 13k 常数 / 不裸取 8%** —— 现成结构已给依据(读 `llm/model_info.py:51-78` +
  `agent/context_manager.py:160-178`)：每个模型有 `context_window` / `effective_pct`(留输出+安全余量) /
  `compact_at_pct`。**buffer 自适应公式**(随窗口/有效模型缩放，第2轮定稿)：
  ```
  effective_window = context_window × effective_pct        # 例 gpt-5.5 400K×0.95=380K
  output_reserve   = max(8_000, min(32_000, ctx_window // 32))  # 输出留头：400K→12.5K；1M→31.25K；32K→8K
  buffer           = output_reserve                        # = "还够不够写下一条输出/摘要"
  should_compress  = (effective_window − used_tokens) ≤ buffer
  ```
  - **依据**：Claude Code 的"剩 ~13k"= 200K 窗口的 ~6.5%；本公式 `ctx_window//32 ≈ 3.1%` 下限 8K、上限 32K，
    既对齐"小窗口别太晚压"(32K→8K=25% 偏保守但安全)，又避免"1M 窗口下 13k=1.3% 太迟压"(给到 31K)。
  - **等价性**：`(eff_win − used) ≤ buffer` ⟺ `used ≥ eff_win − buffer`，即把现状的绝对阈值
    `compact_at_tokens` 换成 `eff_win − buffer`。**两者数值可能不同**——现状 `compact_at_tokens =
    window×compact_at_pct`(gpt-5.5=400K×0.80=320K)，新阈值 `eff_win − buffer = 380K − 12.5K = 367.5K`。
    **决策：取两者 min** 作触发线(`min(window×compact_at_pct, eff_win − buffer)`)，谁先到先压，既不浪费大窗口
    又保证"留得下输出"。这是 WI-1 的**单一可实现判据**。
- **实现位置**：改 `ContextCompressor.should_compress(prompt_tokens)` 内部，或在 agent_loop L740-753 的
  `_ctx_should_compress` 计算块。建议**改 should_compress**——把 `context_window`/`effective_pct`/`compact_at_pct`
  三参经构造注入(main.py:1409 已注入 window+threshold，补 `effective_pct`+`output_reserve_fn` 即可)，
  agent_loop 不动判据逻辑(只继续喂 `_ctoken_est`)。**签名**：`should_compress(prompt_tokens)` 不变，
  内部改用 `min(self.threshold_tokens(), self.effective_window − self._buffer())`。

> **🛑 第2轮重大纠正 — 第1轮「history verbatim 无限注入」论断部分错误（读 `policy.py:203/244` +
> `manager.py:47/123-144` + `memory.py:54-71`）**
> - **铁证**：进 agent_loop 第一轮的 `bundle.history` **不是无限长** —— 它由 `MemoryComponent` 经
>   `MemoryManager.recall(policy)` 的 **`l2_top_k`** 拉取，chat policy 默认 **`l2_top_k=5`**(`policy.py:203,244`)，
>   manager 默认 10(`_DEFAULT_L2_TOP_K`)。即**历史对话在 assemble 阶段就被硬裁到最近 5~10 条**，verbatim 注入的是
>   "这 ≤10 条"，不是全部历史。第1轮"history 永远完整到达 compressor"**不准确**，已纠正。
> - **➜ 「compaction 是不是唯一防爆手段」答案钉死：不是，有四道闸**：① **`l2_top_k=5` 截断历史**(assemble，跨轮起点
>   永远 ≤10 条对话)；② **BudgetAllocator 裁组件 slice**(memory/skill/tool 文本)；③ **token_budget BLOCK gate**
>   (`agent_loop:666-687`，超 `block_pct` 直接 ErrorEvent 中止本轮，硬兜底不崩)；④ **compaction**(本升级)。
> - **➜ 真正会撑爆 working_messages 的不是历史对话，而是「单轮 agentic 多轮 tool 调用累积的 tool_result」**
>   (一轮 4×web_fetch 就 64K 字符，见 `context_manager.py:185-194` 注释)。这恰好印证 **WI-2 microcompact(清陈旧
>   tool_result) 才是本升级最高频生效的一层**，比"压历史对话"更对症。**WI-2 优先级应最高**(§7 拆期已让它进第1期，正确)。
> - **➜ compaction 默认关也不会"直接撑爆"**：闸 ① 把跨轮历史钉在 ≤10 条，闸 ③ 在单轮 tool 爆炸时 BLOCK 兜底
>   (中止本轮、报错、不崩)。compaction 的价值是**让单轮 agentic 长任务不被闸③ 打断**(平滑续跑而非报错停)，
>   不是"唯一防 OOM"。这修正了 plan 顶部"compaction=不崩兜底主力"的措辞——**真正兜底是闸①③，compaction 是体验升级**。

> **⚠️ 证伪「阈值反序」前提 — plan 最大假设被推翻（已读真实代码，见文末评审记录 §1）**
> - **执行序铁证**: `main.py:5318 assemble()` → `5353 build_messages()` 产出 `_msgs` → `5951 agent.run(_msgs)`。
>   `agent_loop.run()` 里 `working_messages = list(messages)`(L548)，compaction 在 L712-785（budget guard 后、LLM 前）
>   对 `working_messages` 原地跑。**assemble + BudgetAllocator 全在 agent_loop 之外、之前**，是两个独立阶段。
> - **0.6 截断**根本不碰对话历史: `BudgetAllocator.allocate()`(`budget.py:76`) **只裁 slice 组件**(memory/persona/
>   skill/tool 文本)；`bundle.history`(对话历史) 在 `bundle.py:300` 是 `messages.extend(history)` **verbatim 注入，
>   从不进 allocate**。所以"0.6 先把要摘要的内容裁掉了"这件事 **根本不存在** —— history 永远完整到达 compressor。
> - **结论**: 「0.6 截断 vs 0.8 摘要 阈值反序、互相打架」**是误诊**。两者作用对象不交叉(allocator=组件 slice，
>   compaction=对话 history)，**无先后冲突**，不需要"让摘要先于截断"。WI-1 关于"序"的整段论证 **🛑 删除/重写**。
> - **WI-1 仍保留的真改**: ✅ 把触发判据从裸比例改成「剩余 token buffer」(对齐 Claude Code 剩 ~13k)是合理升级，
>   且不依赖任何"序"假设。注意现状触发已是 `count_messages_tokens(working_messages) ≥ compact_at_tokens`(L734-746)，
>   本会话已修好；WI-1 只是把判据语义从「≥ 绝对阈值」换成「剩余 ≤ buffer」并让 buffer 随窗口/模型自适应。
>   **不要再写「让 assembler 在 compaction 之后兜底」**——assembler 在 agent_loop 之前，根本无法"之后兜底"。
> - **WI-0 已完成**（本轮评审即 WI-0 的产出）：作用点已量清，无需再单列 WI-0 调研步。

### WI-2 microcompact 层（廉价清陈旧 tool_result，不调模型）（治落后点 1）
- 在结构化摘要**之前**插一层: 把**陈旧的 tool_result**（非最近 K 个工具调用的）正文换成
  `[旧工具结果已清理]` 占位，**不调模型、不动对话语义**。最近工具调用受保护。
- 复用本会话已建的 `count_messages_tokens` 判大小；与现有 `_sanitize_tool_pairs` 协同(别造孤儿 tool)。
- 命中后若已降到阈值下 → 跳过重摘要(省一次 haiku 调用)。

> **✅ 核准 — 协议合法性已验证（读 `context_compressor.py:353-396` _sanitize_tool_pairs）**
> - **关键约束**: microcompact 必须 **保留 `tool` 消息本身 + 其 `tool_call_id`，只替换 `content` 正文**为占位串。
>   绝**不能删整条 `tool` 消息**——`_sanitize_tool_pairs` 的逻辑是「`tool_call_id ∈ open_ids` 才保留，否则当孤儿丢」
>   (L363-368)，删了 tool 消息不影响合法性，但删了会丢配对计数；最稳妥是 **保留 tool 消息壳、只换 content**，
>   这样 assistant.tool_calls ↔ tool 配对完整，OpenAI 协议合法。占位后这条 tool 仍是合法响应（content 非空即可）。
> - **不要动 assistant.tool_calls**: 保留发起调用的 assistant 消息原样即可，配对天然成立。
> - **执行点**: microcompact 应在 `compress()` 入口、`_partition` **之前**对 `working_messages` 做（或在 agent_loop
>   compaction 块内、调 compress 前先跑一遍）。两处都行，但 microcompact 的占位结果会再经 `_partition`+`_sanitize`，
>   双重兜底不冲突。
> - **范围提示**: 这是独立、低风险、不调模型的一层，**建议优先做、可独立交付**（见 §7 拆期）。

### WI-3 结构化摘要升级（4 段 → 完整 schema）+ 保最近 user 原文（治落后点 2）
- `_SUMMARY_SYSTEM` 从本会话的 4 段扩到对标 Claude Code 的 schema:
  **意图/目标 · 已完成 · 进行中(当前任务) · 关键事实与决策 · 涉及的文件/产物 · TODO/待办 · 下一步**。
- **保最近 user turn 全文**: last_n 保证含最近 ≥1 条完整 user 消息原文（不只摘要）；压完确保最近用户请求仍在尾段。
- **锚定增量**: 若上一次压缩已产出 summary，新摘要**并入**而非从头重建(防 drift)。需在 messages 里识别上次
  `[压缩摘要]` 标记 + 传给摘要器作 prior state。
- **🛑 锚定增量可实现方案（第2轮钉死 — 不改 compress 签名，内部 detect+分离）**：
  - **不改 `compress(messages, *, goal_text, pending_tasks)` 公开签名**(改了要动 main.py + 所有调用点 + 测试)。
    改 **`_partition` 之后、`_render_transcript` 之前**在 compressor 内部加一步 `_extract_prior_summary(middle_chunk)`：
    扫 `middle_chunk` 里 `role=="assistant"` 且 `content` 以 `"[压缩摘要 / compressed summary]"` 开头(即
    `_format_summary` L432-436 的前缀)的消息，**抽出最后一条**作 `prior_summary`，并**从 middle 里剔除**(避免它再被当待摘 transcript)。
  - **数据流**：`middle_chunk → (prior_summary, middle_without_prior)`。喂 haiku 的 user content = `middle_without_prior`
    的 transcript；prior_summary **作为独立段拼进 system**：`_SUMMARY_SYSTEM + "\n\n【已有摘要(在此基础上增量更新,不要丢已记录的任务/决策)】\n" + prior_summary`。
    新 summary 仍走 `_format_summary` 注入(前缀不变)，下一轮再被识别——形成**单条滚动锚定摘要**，不套娃。
  - **边界**：若 `middle_without_prior` 为空(中段只剩旧摘要+少量噪声) → 仍要更新摘要(把 prior 透传/轻量重写)，
    不能 no-op 丢掉 prior(否则旧摘要被剔除后彻底消失)。**这是必测边界**(见 §4 WI-3)。
  - **签名**：`def _extract_prior_summary(msgs: list[dict]) -> tuple[Optional[str], list[dict]]` —— 纯函数、好单测。
- **防幻觉**: prompt 强调"只基于待摘内容,不得编造用户未说的指令"(对标 issue #46602)；保留本会话已加的防反射。

> **✅ 核准 + ⚠️ 锚定增量需补一处实现细节（读 `_format_summary` L432-436 / `_partition` L292）**
> - **标记真相**: 现有标记是 `_format_summary` 注入的 **`[压缩摘要 / compressed summary]`** 前缀，role=assistant
>   (L237-240)。WI-3 识别上次摘要 = 扫 middle 里 role=assistant 且 content 以该前缀开头的消息。
> - **🛑 无限累积风险确认**: 现状每次压缩把上一条 `[压缩摘要]` assistant 消息**当普通中段消息再喂给 haiku**
>   (它在 first_n..last_n 之间会被划进 middle 重新摘) → 多轮压缩**会摘要套摘要**，且每次重摘都可能 drift。
>   **锚定增量必须做**：识别出旧摘要后，**把它从 middle 里抽出来作为 prior-state 单独传给摘要器**(放进
>   `_SUMMARY_SYSTEM` 或单独 system 段)，让模型「在 prior 基础上增量更新」，**而非把旧摘要正文混进待摘 transcript**。
>   否则旧摘要会被反复重写、低频信息逐趟蒸发（通用对标点已指出 drift）。这是 WI-3 的**必做项不是可选**。
> - 结构化 schema 升级(4 段→完整)+ 保最近 user 原文(last_n 已保 6 条)= ✅ 低风险，直接做。

### WI-4 pre-flush 落盘 + 目标钉进不可压段（治落后点 2、4，根治"看不到任务"）
- **pre-compaction flush**: compress 真正摘掉中段**之前**，把"当前任务 + TODO + 关键决策"写进**已有 L1
  记忆**(`MEMORY.md` 或专用 task-state 文件)，对标 OpenClaw。这样即使摘要丢细节,落盘的任务态可被下轮/下个
  session 召回。**复用现有 L1 写入 API,不新建存储。**
- **目标钉死**: 把"用户最初目标/约束 + 用户画像"放进**从不参与 `_partition` 压缩的 system 段**(现在 [目标锚定]
  是压缩后注入,改成 always-on 的不可压 system,对标 CLAUDE.md 钉死)。需确认 `_partition` 把这类 system 永久排除。

> **🛑 第2轮新增挑战 — 目标当前有「三处」注入 [目标锚定]，always-on 会变四处，必须先统一（读 agent_loop
> L819-851 周期性 anchor + L755-763 压缩后 anchor + context_compressor._build_goal_anchor L448-495）**
> - **现状三处都注 `[目标锚定] 当前目标：…`，内容近乎重复**：
>   1. **周期性 anchor**：`agent_loop:831-851`，每 `_GOAL_ANCHOR_EVERY` 轮注一条 `role=system` `[目标锚定]…请确保…服务于上述目标`，
>      有 `_last_anchor_iter` dedupe(同轮不重)。
>   2. **压缩后 anchor**：`compress()` 内 `_build_goal_anchor`(L448) 在压缩命中后注一条 `role=system` `[目标锚定] 当前目标：…[当前子目标]…`(L242)。
>   3. (WI-4 新增) **always-on**：每轮再注一条 → **第四处**。
> - **➜ 统一后单一注入策略（第2轮定稿，避免 2~4 条几乎同文的 [目标锚定] system 堆在 working_messages）**：
>   - **唯一注入点 = agent_loop，注入一条「常驻目标 system」**，放在 `working_messages = list(messages)` 之后、`for iteration` 循环**之前**(只注一次/轮，不在循环内)。
>     目标取 `session_goal_store.get_goal_text(session_id)`。**这条就是 always-on 的「不可压目标段」**(role=system → `_partition` L311-313 永久排除，天然不进 middle)。
>   - **删周期性 anchor(L831-851)**：always-on 已每轮常驻，周期性重注是冗余 → **删除该块 + `_last_anchor_iter` 变量**。
>     (注：周期性 anchor 的本意"防 drift"由 always-on 常驻 + WI-3 摘要保任务共同覆盖，不丢能力。)
>   - **删压缩后 `_build_goal_anchor` 注入**：compress() 不再自注 [目标锚定](always-on 那条压缩后仍在 system 段，不会被压掉)。
>     **`compress(goal_text=…)` 入参可保留**但只用于"摘要 prompt 里提示模型别丢目标"，**不再产出独立 system 锚消息**(改 `_build_goal_anchor` 返回 [] 或删调用 L242)。
>   - **去重断言(必测)**：压缩前后 working_messages 里 `content.startswith("[目标锚定]")` 的 system 消息 **恒为 ≤1 条**。这是 WI-4 的可证伪 DoD。
> - **子目标(pending_tasks)归属**：现 `_build_goal_anchor` 还带 `[当前子目标]`。统一后 always-on 那条也带子目标
>   (从 `session_goal_store` 取 pending)，**保留子目标能力、只是注入者改为 agent_loop 单点**。

> **① pre-flush L1 写入 API — ✅ 存在，但 ⚠️「同会话召回」是伪需求（读 `file_memory.py` 全文）**
> - **真实写入 API**: `FileMemory.append(target: str, content: str, salience: float=0.5)` — async；`target ∈ {"memory","user"}`
>   (`MEMORY.md`/`USER.md`)。pre-flush 写"任务态/TODO" → `append("memory", "<task state>", salience=高)`。
>   句柄经 `service_context.get("file_memory")`(`p4_ipc.py:861 _get_file_memory`) 可拿到，agent_loop 已能注入。
> - **🛑 关键陷阱（frozen-snapshot）**: `read_snapshot()`(L109) 是 **point-in-time，session boot 读一次后钉进 system
>   prompt，mid-session 的 append 不反映到当前会话**（注释 L21-26 明写「subsequent append 不 mutate cached snapshot」）。
>   所以 **pre-flush 写 L1 对「当前会话本轮压缩后」无效，只对「下一个 session」生效**（恰好印证 Hermes「记忆编辑下个
>   session 才生效」）。➜ plan 里"落盘的任务态可被**下轮**召回"这句 **要改为「下个 session 召回」**；
>   **「同会话看不到任务」的根治靠 WI-3 结构化摘要 + WI-4 目标钉死 system 段，不是靠 pre-flush 回读**。pre-flush 的价值
>   是**跨会话**记住任务，别把它当本轮防丢手段写。
> - **去重**: 已有 `memory/summarizer.py`(session 摘要器,age_days=30 归档) + `memory/reflection.py`。pre-flush 与它们
>   **粒度不同**(pre-flush=压缩时刻的 live 任务态，summarizer=会话级事后归档)，不直接重复；但**要避免每次压缩都
>   append 一条**导致 MEMORY.md 被任务态刷爆(50KB cap 会驱逐真实记忆)。建议: **task-state 用固定 key 覆盖式写**
>   (或 best-effort + salience 适中 + 限频)，不要无脑 append。这点 plan 没考虑，**补进 WI-4**。
>
> **② 目标钉死 — ✅ 可行，但实现要换地方（读 `_partition` L311-313 / `_build_goal_anchor` L448-495）**
> - **`_partition` 已永久排除所有 system**: L311-313 先把 `role=="system"` 全部抽成 `system_msgs` 原样保留，只对
>   non-system 切 first/middle/last。**所以任何 role=system 消息天然永不进 middle，永不被压** —— 目标钉死「放进不可压
>   system 段」**机制已现成成立**，不需要改 `_partition`。
> - **现状**: `[目标锚定]` 由 `_build_goal_anchor`(L448) 在 **compress() 内、压缩命中后**才注入(L242)，role=system。
>   即「只有触发压缩那一刻才有目标锚」。WI-4 要的「always-on」= **把目标 system 段的注入点从 compressor 内提到
>   `agent_loop` 每轮组装 working_messages 时**（或 assembler 的 frozen/system 段）。
> - **🛑 注入者归属要定**: 不该让 compressor 负责 always-on 注入（它只在压缩时跑）。两个候选: (a) **agent_loop** 在
>   `working_messages = list(messages)` 后、循环前 append 一条 `[目标锚定] system`(目标来自 `session_goal_store`,
>   L756 已有 `get_goal_text(session_id)`)；(b) **assembler** 把目标并进 `frozen_system`/独立 system block。
>   **建议 (a)**——agent_loop 已持有 `session_goal_store` 句柄，改动最小、不动 assemble 缓存。compress 内的
>   `_build_goal_anchor` 可保留作压缩后二次重锚(冗余但无害)，或在 always-on 落地后删掉避免双份 system。**WI-4 要明确选 (a) 还是 (b)**。
> - **防双注入**: agent_loop L604-607 已有 `_last_anchor_iter` dedupe 锚点注入逻辑，always-on 目标段要与它协调，别叠两条。

### WI-5（独立后续，可单列一期）跨会话分层记忆补强 — 复用 L1/L3
- 调研指出桌宠"记住用户"该走 Hermes 式: 精炼用户画像(常驻) + 按需召回。**DeskPet 已有 L1(`USER.md`)
  + L3 向量召回**——本 WI 只做**接线/策展强化**: ① 压缩 pre-flush 进 L1/L3(WI-4 已含) ② 给 `USER.md`
  加字符上限逼策展(对标 Hermes ~3.5k) ③ 确认 ContextAssembler 召回足够。**评估后决定是否单列一期,不在
  本期硬塞。**

> **⚠️ 证伪「给 USER.md 加字符上限」= 重复造（读 `file_memory.py:76-94`）**
> - **上限早已存在**: `FileMemory(user_md_max_kb=20, memory_md_max_kb=50)`，超 cap 时 `_evict_to_fit`(L251) 按
>   salience 驱逐——**策展机制(cap + 驱逐)已完整**。WI-5 ② 「给 USER.md 加字符上限」**删除**，改为：**若想对标
>   Hermes ~3.5k 更激进策展，只需把 `user_md_max_kb` 默认从 20 调小**(配置项，非新代码)；但 20KB 也不算离谱，
>   **是否真要调小需先看实际 USER.md 体积，别凭空改**。
> - ✅ WI-5 ① pre-flush 进 L1 = 已并入 WI-4。③ 确认 ContextAssembler 召回 = `MemoryComponent` 已注入 L1
>   snapshot + L3 召回(`memory.py`)，地基在。**本 WI 实际只剩「确认 + 可能调 cap」，工作量很小，建议并进 WI-4
>   验收一起做，不必单列一期。**

### WI-6 完成 WI-1~4 后，把 compaction 默认打开（治落后点 3）
- `config.py:419 compaction_enabled` 默认 False → True（**且必须先做完 P-B 修复**，否则窗口取错模型过早压）。
- 仅当 WI-1~4 + P-B 全绿 + 真机验证后才翻默认。

> **✅ 核准依赖（确认 `config.py:420 compaction_enabled=False` + P-B plan 存在）**
> - `compaction_enabled` 默认 False 属实(`config.py:420`)。`should_compress` 现状逐字: `prompt_tokens ≥ context_window
>   × threshold_percent`(L141-145, threshold 默认 0.75)；窗口靠 `context_window` 构造参注入 → 取错模型窗口会过早/过晚压。
> - **P-B 依赖写对了**: `plans/2026-06-16-effective-llm-model-resolution/00-PLAN.md` 存在。窗口必须按**有效出站模型**
>   解析(P-B 治的正是「读模型名走有效出站模型」)，否则 compaction 默认开会用错窗口。**依赖成立，保留。**
> - ⚠️ **翻默认前的真机门槛**: 默认开后**所有用户**都吃压缩，务必先在小窗口 + 长会话真机(windows-mcp)验证
>   「压缩后追问『继续/刚才在干嘛』桌宠仍记得任务」(§4 真机 case ②) 全绿，再翻。这是一票否决级。

## 4. 测试（每 WI 单测 + 集成 + 真机）— **第2轮：每条改可证伪自动断言**
- **WI-1**（buffer 自适应触发）：
  - `should_compress` 在 `used = eff_win − buffer − 1` 时返 False、`= eff_win − buffer` 时返 True（边界自动断言）。
  - 参数化三模型(gpt-5.5 400K / deepseek 1M / _default 32K)：断言 `buffer == max(8000, min(32000, window//32))`。
  - 断言触发线 = `min(window×compact_at_pct, eff_win − buffer)`（取 min 决策）。
- **WI-2**（microcompact）：陈旧 tool_result 的 `content` 被换占位串、`tool_call_id` 与 role=tool **壳保留**(不删整条);
  最近 K 个 tool 调用 content 原样;占位后过 `_sanitize_tool_pairs` 不产孤儿;命中后 `_ctoken_est` 降到阈值下 → 断言**跳过 haiku 调用**(mock 计数=0)。
- **WI-3**（结构化摘要 + 锚定增量）：
  - `_SUMMARY_SYSTEM` 含全部段名(意图/已完成/进行中/关键事实/文件产物/待办/下一步) — 字符串断言。
  - 最近 ≥1 条 user 原文仍在尾段(role==user 且原文子串命中)。
  - `_extract_prior_summary`：输入含 1 条 `[压缩摘要…]` assistant → 返回 (prior_text, middle 不含该条);**喂 haiku 的 user content 不含旧摘要前缀**(断言 transcript 里无 "[压缩摘要")。
  - **边界**：`middle_without_prior` 为空时仍输出含 prior 内容的新摘要(prior 不丢失)。
  - **防套娃**：连压 2 次,断言最终 working_messages 里 `[压缩摘要]` assistant 恒 ≤1 条(不累积)。
  - 防幻觉/反射断言(保留本会话已加的)。
- **WI-4**（目标统一注入 + pre-flush）：
  - **去重(核心)**：任意轮 working_messages 里 `content.startswith("[目标锚定]")` 的 system 恒 ≤1 条(压缩前后都测)。
  - always-on：有 goal 时第 1 轮 working_messages 即含 1 条 [目标锚定] system;压缩后仍在(role=system 不进 middle)。
  - pre-flush：mock FileMemory.append 被调用、target=="memory"、content 含任务态;**用固定 key 覆盖式/限频**(连压 3 次 append 调用 ≤ N,不无脑刷)。
- **真机(windows-mcp)**: 开 compaction + 小窗口,长 agentic turn 触发压缩 → ① 日志 `context_compacted` 摘要含【进行中/当前任务】
  ② 压缩后追问"刚才在干嘛/继续"桌宠仍记得(根治看不到任务) ③ L1 落盘可见(下个 session 召回，**非本轮**)。
- 回归: `pytest tests/ -k "compact or compress or token or budget or assembler or memory or agent_loop or goal_anchor"` 全绿(含下方 §8 必改清单)。

## 5. 降级/边界
- 任一级失败 → 退回上一级/原消息,绝不崩(对齐现有 safe-fail + circuit breaker 思想)。
- microcompact 不调模型 → 零额外延迟/成本。
- pre-flush 写 L1 失败 → 不阻断压缩(best-effort)。
- compaction 仍默认关直到 WI-1~4+P-B 全绿(WI-6 才翻)。

## 6. 文件清单（第1轮 + 第2轮修正）
- 改: `deskpet/agent/context_compressor.py`(WI-2 microcompact 层 + WI-3 结构化 schema + `_extract_prior_summary` 锚定增量
  + WI-4a **删/改 `_build_goal_anchor` 注入**(不再自产 [目标锚定] system) + WI-1 `should_compress` 改 min(阈值, eff_win−buffer)) ·
  `agent/agent_loop.py`(WI-4a **目标 always-on 单点注入** + **删周期性 anchor L831-851 + `_last_anchor_iter`** +
  WI-4b **pre-flush 调 `FileMemory.append`(第2期)**) · `main.py:1409`(给 `ContextCompressor` 构造**补 `effective_pct`
  + output_reserve 参数**，供 WI-1 buffer 公式) · `config.py`(WI-6 翻默认)
- ❌ **`budget.py` 不改** —— 已证伪「0.6 截断与 compaction 有序冲突」，BudgetAllocator 只裁组件 slice、不碰对话 history。
- ❌ **`model_info.py` 不改** —— per-model `context_window`/`effective_pct`/`compact_at_pct` 已现成，WI-1 buffer 公式直接复用。
- 关联: 复用 L1 写入 API = **`FileMemory.append(target, content, salience)`**(`deskpet/memory/file_memory.py:124`)，
  句柄走 `service_context.get("file_memory")` · 复用 `deskpet/agent/tokens.py`(`count_messages_tokens`) ·
  目标取 `session_goal_store.get_goal_text(session_id)`(agent_loop 已持句柄，L756/836) · per-model 预算走 `llm/model_info.py:resolve()`
- 测: 见 §8 受影响测试清单 + 各 WI 新增单测 + 集成 + 真机 manual-results

## 7. 范围裁决（评审第1轮 — 一期吞不下，拆 2 期）

原 6 WI 同期交付 **过大**(microcompact + 结构化摘要 + 锚定增量 + pre-flush + 目标钉死 + 触发改 buffer + 翻默认)，
且其中两条价值最高的(WI-2 / WI-3)互相独立、低风险，不该被高风险项(WI-4 pre-flush 跨会话 + WI-6 翻默认)拖住。

> **🛑 第2轮重审拆期边界 — 原拆法把"看不到任务"根治拆散到两期，违反"每期独立交付完整价值"**
> - 「压缩后看不到任务」的根治靠**两条腿**：WI-3(结构化摘要保【进行中/当前任务】) + **WI-4 目标钉死 always-on**。
>   原拆法 WI-3 在第1期、WI-4 在第2期 → **第1期交付后"看不到任务"只治了一半**(摘要保了任务，但目标段仍只在压缩时才有、
>   非 always-on，且三处 [目标锚定] 重复未统一)。真机 case ②"压缩后追问仍记得任务"**第1期可能过不了**(目标飘)。
> - **➜ 第2轮调整：把 WI-4 的「目标统一注入 always-on」上移进第1期**(它是"看不到任务"根治的另一条腿，且**不依赖
>   frozen-snapshot/跨会话语义**，纯 agent_loop 单点注入 + 删两处冗余，风险自包含)。**WI-4 的 pre-flush 写 L1 留在第2期**
>   (那才是跨 session、踩 frozen-snapshot 陷阱的部分)。即 **WI-4 一拆为二**：WI-4a(目标 always-on 统一注入)→第1期；WI-4b(pre-flush L1)→第2期。

**第 1 期（核心：根治"看不到任务" + 触发升级，低风险可快交，能独立拿真机 case ①② 证据）**
- **WI-2 microcompact**（独立、不调模型、协议已验证 — 本升级最高频生效层，见 WI-1 第2轮纠正块）
- **WI-3 结构化摘要完整 schema + 保最近 user 原文 + 锚定增量防套娃**（防 drift 是必做）
- **WI-4a 目标 always-on 统一注入**（agent_loop 单点 + 删周期性 anchor + 删压缩后 _build_goal_anchor 注入；
  去重 DoD：[目标锚定] system 恒 ≤1 条）— **"看不到任务"的第二条腿，必须与 WI-3 同期才能让真机 case ② 全绿**
- **WI-1 触发改剩余 buffer**（buffer 自适应公式，纯触发判据升级）
- 验收: 单测 + 集成 + 真机 case ①②（压缩后桌宠仍记得任务）+ §8 必改测试全绿

**第 2 期（跨会话记忆 + 默认开，依赖 frozen-snapshot 语义、影响全量用户，需更重真机）**
- **WI-4b pre-flush 写 L1**（定位为**跨 session** 记任务，非本轮防丢；用**固定 key 覆盖式 + 限频**写避免刷爆 MEMORY.md 50KB cap）
- **WI-5 收敛**: 并进第 2 期(只剩"确认 ContextAssembler 召回 + 是否调小 user_md_max_kb")，**不单列一期**
- **WI-6 翻默认**: 严格 gate 在 **第1+2期全绿 + P-B 修复 + 小窗口长会话真机 case ② 全绿** 之后

> 拆期理由(第2轮订正): WI-1/2/3/4a 不依赖 L1/frozen-snapshot 语义、不改默认、风险自包含，且**合起来才能完整根治
> "看不到任务"**(摘要保任务 + 目标 always-on 钉死)，能独立拿真机 case ①② 证据；WI-4b/6 牵扯跨会话语义(frozen-snapshot
> 陷阱)、默认开影响全量用户，必须更慎、单独一期收口。

## 8. 受影响的现有测试（第2轮 — 每 WI 必改/必加，开工即按此清单同步）

> 本会话刚修过触发 bug + 结构化摘要 + 防反射，核心路径回归面大。下表逐 WI 钉「会被打破/必同步改」的真实测试文件。

| WI | 会被打破的现有测试（必改） | 必新增的测试 |
|---|---|---|
| **WI-1** | `tests/test_deskpet_context_compressor.py`(`should_compress`/`threshold_tokens` 系列：判据从绝对阈值改 `min(阈值, eff_win−buffer)`)；`tests/test_agent_loop_compaction_wiring.py`(触发数值断言) | buffer 自适应参数化三模型；min 决策边界；`eff_win−buffer±1` 触发边界 |
| **WI-2** | 无直接破坏(新增层)；但 `tests/test_compressor_tool_pairs.py` 须确认 microcompact 占位后仍过 `_sanitize_tool_pairs` | microcompact 占位/保壳/跳过 haiku/不产孤儿 |
| **WI-3** | `tests/test_deskpet_context_compressor.py` L434-436(`_SUMMARY_SYSTEM` 段名断言 — schema 扩了要补段名)；L208/247/549(`[压缩摘要]` 前缀断言 — 前缀不变则不破，**别改前缀**) | `_extract_prior_summary` 单测；防套娃(连压 2 次 ≤1 条摘要);middle_without_prior 空边界 |
| **WI-4a** | **🛑 `tests/test_compressor_goal_anchor.py`(整文件 6+ 测试断言 compress 注入 [目标锚定]) + `tests/test_compactor_goal_anchor.py` + `tests/test_agent_loop_compaction_wiring.py` L286-408(断言压缩后产出 [目标锚定] block)** —— 删 `_build_goal_anchor` 注入会**全红**，必须改：把"压缩后注入"断言迁移成"always-on 单点注入 + 去重 ≤1 条"。`tests/test_deskpet_agent_loop.py`(`_GOAL_ANCHOR_EVERY`/`_last_anchor_iter` 周期 anchor 测试)删块后须移除/改写。 | always-on 第1轮即注入;[目标锚定] 恒 ≤1 条;子目标保留 |
| **WI-4b** | 无破坏(新增 pre-flush) | mock FileMemory.append 调用/覆盖式 key/限频 |
| **WI-6** | 翻默认会让一批"默认 OFF 字节级一致"BC 测试失效 — 须同步那些断言默认 ON 后的行为 | 默认 ON 的端到端 wiring |

**最高回归风险**：**WI-4a 删 `_build_goal_anchor` 注入** —— 横跨 3 个测试文件断言"压缩后有 [目标锚定]"。
开工顺序建议：先迁移这 3 文件的断言语义(从"压缩后注入"→"always-on 单点 + 去重")，再删旧注入码，避免红着提交。

## 评审记录 — 第1轮（对抗式挑战 + 就地修订，2026-06-16）

**对照真实代码下结论，非凭空。** 本轮 = 原 plan 里的 WI-0(量作用点)产出。

**🛑 最关键发现 1 — WI-1「阈值反序」是误诊，假设被推翻**
- 执行序铁证: `main.py:5318 assemble()` → `5353 build_messages()` → `5951 agent.run(_msgs)`；
  `agent_loop.run()` `working_messages=list(messages)`(L548)，compaction 在 **L712-785**(budget guard 后、LLM 前)。
- `BudgetAllocator.allocate`(`budget.py:76-141`) **只裁 slice 组件**(memory/persona/skill/tool 文本)；
  `bundle.history`(对话历史)在 `bundle.py:300` 是 `messages.extend(history)` **verbatim、从不进 allocate**。
- ➜ 「0.6 先把要摘要的内容裁掉/与 0.8 摘要打架」**不存在**。两者作用对象不交叉、无序冲突。WI-1 的"序"整段
  论证删除，只保留「触发判据改剩余 token buffer」；`budget.py` 从改动清单移除。

**✅ 关键发现 2 — L1 写入 API 真相 + frozen-snapshot 陷阱**
- 写入 API = `FileMemory.append(target∈{"memory","user"}, content, salience=0.5)`(`file_memory.py:124`)，async，
  句柄 `service_context.get("file_memory")`(`p4_ipc.py:861`)。
- **但 `read_snapshot` 是 frozen-snapshot(session boot 读一次钉进 system prompt，mid-session append 不回读，
  `file_memory.py:21-26`)** ➜ pre-flush **只对下个 session 生效，对当前会话本轮无效**。plan 原写"落盘可被**下轮**召回"
  误导，已改为「跨 session」。「同会话看不到任务」根治靠 WI-3 摘要 + WI-4 目标 system 段，**不是** pre-flush 回读。
- USER.md 字符上限**早已存在**(`user_md_max_kb=20` + salience 驱逐)，WI-5「加上限」是重复造，删。

**✅ 关键发现 3 — 目标钉死可行，机制现成，但注入点要搬家**
- `_partition`(`context_compressor.py:311-313) 已把所有 `role==system` 整组抽出永不进 middle` ➜ 「不可压 system 段」
  机制现成成立，**不需改 `_partition`**。
- 但现状 `[目标锚定]` 由 `_build_goal_anchor`(L448) 仅在**压缩命中后**注入(L242) ➜ 非 always-on。要 always-on 须把
  注入点提到 **agent_loop 每轮组装 working_messages 时**(建议 (a)，已持 `session_goal_store.get_goal_text`，L756)，
  并与现有 `_last_anchor_iter` dedupe(L604-607) 协调防双注入。

**其他**: WI-2 microcompact 协议合法(保留 tool 消息壳只换 content，配对不破，验 `_sanitize_tool_pairs` L353-396)，✅；
WI-3 锚定增量**必做**(否则旧 `[压缩摘要 / compressed summary]` 前缀消息会被反复重摘 → 套娃 drift)，✅；
WI-6 P-B 依赖成立(`plans/2026-06-16-effective-llm-model-resolution/00-PLAN.md` 存在)，✅。
**范围**: 6 WI 一期过大，拆 2 期(见 §7)。**总方向(对标最佳实践升级)不变，本轮只钉可行性 + 真实接线 + 范围。**
- 前置依赖: **P-B 修复**(`plans/2026-06-16-effective-llm-model-resolution`)——窗口取对模型,否则触发点错

## 评审记录 — 第2轮（对抗式挑战 + 实现细节钉到可开工，2026-06-16）

**对照真实代码复核第1轮 + 挖第1轮没碰的。总方向不变，本轮钉实现细节/拆期/测试影响/可证伪 DoD。**

**🛑 重大纠正 1 — 第1轮「history verbatim 无限注入、是唯一防爆」论断部分错误**
- 第1轮说"history 永远完整到达 compressor"**不准确**。铁证：`bundle.history` 由 `MemoryComponent` 经
  `MemoryManager.recall` 的 **`l2_top_k`** 拉取，chat policy 默认 **`l2_top_k=5`**(`policy.py:203,244`；
  manager 默认 10，`manager.py:47 _DEFAULT_L2_TOP_K`)。**历史对话在 assemble 阶段就被硬裁到最近 5~10 条**，
  verbatim 注入的只是"这 ≤10 条"。
- ➜ **「compaction 是不是唯一防爆」答案：不是，四道闸**：① `l2_top_k=5` 截历史；② BudgetAllocator 裁组件 slice；
  ③ `token_budget` BLOCK gate(`agent_loop:666-687`，超 block_pct 直接 ErrorEvent 中止本轮、不崩)；④ compaction。
  真正会撑爆 working_messages 的是**单轮 agentic 多轮 tool 调用累积的 tool_result**(一轮 4×web_fetch≈64K 字符)，
  不是历史对话 ➜ **WI-2 microcompact 才是最高频生效层**，比"压历史"更对症(§7 已让它进第1期，正确)。
  顶部"BudgetAllocator=不崩兜底主力"措辞已订正为四道闸。

**🛑 重大纠正 2 — 目标当前「三处」注入 [目标锚定]，always-on 会变四处，第1轮只点了双注入、漏了第三处**
- 实际三处：① 周期性 anchor(`agent_loop:831-851`，每 `_GOAL_ANCHOR_EVERY` 轮)；② 压缩后 `_build_goal_anchor`
  (`context_compressor.py:448`，压缩命中后注)；③ WI-4 新增 always-on。内容近乎同文。
- ➜ **统一单点策略(WI-4a 定稿)**：唯一注入点 = agent_loop 循环前注一条常驻 [目标锚定] system；**删周期性 anchor +
  删压缩后 `_build_goal_anchor` 注入**；去重 DoD = [目标锚定] system 恒 ≤1 条。这横跨 3 个测试文件断言(§8)，
  是本升级**最高回归风险**，开工须先迁移断言语义再删旧码。

**✅ 钉死 3 — buffer 公式有现成依据，不另造 13k 常数**
- `llm/model_info.py` 已给 per-model `context_window`/`effective_pct`/`compact_at_pct`(`gpt-5.5` 400K×0.95/0.80，
  `deepseek` 1M×0.95/0.75)；`main.py:1409` 构造 compressor 时已注入 window+compact_at_pct。WI-1 buffer 公式
  `output_reserve=max(8K,min(32K,window//32))`，触发线取 `min(window×compact_at_pct, eff_win−buffer)`，
  随窗口/有效模型自适应(回应"1M 下 13k=1.3% 太迟"：给到 31K)。`model_info.py` 不改、只复用。

**其他钉死**：
- WI-3 锚定增量**不改 compress 签名**，内部加 `_extract_prior_summary(msgs)->(prior, middle')` 纯函数，
  把旧 `[压缩摘要]` 抽出作 prior-state 拼进 system，**不混进待摘 transcript**(防套娃)；middle' 空是必测边界。
- 拆期边界订正：**WI-4 一拆为二** —— WI-4a(目标 always-on 统一注入)上移第1期(与 WI-3 同为"看不到任务"两条腿，
  缺一则真机 case ② 过不了)；WI-4b(pre-flush L1，踩 frozen-snapshot)留第2期。**每期独立交付完整价值**。
- §4 每条测试改为可证伪自动断言；§8 新增"受影响测试清单"逐 WI 钉必改文件(WI-4a 红 3 文件)。
- **第1轮结论复核结果**：发现1(阈值反序误诊)✅成立但需补"l2_top_k 才是历史防爆闸"；发现2(frozen-snapshot)✅完全成立；
  发现3(目标钉死机制现成)✅成立但注入统一比第1轮说的更复杂(三处不是两处)。
