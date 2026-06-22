# 上下文压缩优化 Round 2 — 完整 PLAN

> **对标**：Hermes / OpenClaw / Claude Code 的**剩余缺口**（Round 1 已抄完三家核心机制）。
> **前序**：[`plans/2026-06-16-compaction-bestpractice-upgrade/00-PLAN.md`](../2026-06-16-compaction-bestpractice-upgrade/00-PLAN.md)（WI-1~6 已实施，compaction 默认开）。
> **状态**：📋 规划中（待 codex 多轮对抗硬化到 EXECUTABLE-AS-IS 再开工）。
> **最后更新**：2026-06-22 ｜ 基线：读码核实（master）。
> **配套调研**：[`STATUS/AgentLoop.md`](../../STATUS/AgentLoop.md) §6 ContextManager · [`STATUS/AgentImprovements.md`](../../STATUS/AgentImprovements.md) §2 缺陷 #2。

---

## 0. 背景：Round 1 已做完什么（❌ 不要重做）

2026-06-16 那轮已对标三家、抄完核心机制，**以下已落地，本轮不碰**：

| 机制 | 来源 | 现状证据 |
|---|---|---|
| 触发=剩余 buffer 非裸比例 | Claude Code | [`context_compressor.py:176-196`](../../backend/deskpet/agent/context_compressor.py) `trigger_tokens()` = `min(threshold, eff_win − output_reserve)` |
| 7 段结构化摘要(含 TODO/当前任务) | Claude Code 9 段 | [`context_compressor.py:102-122`](../../backend/deskpet/agent/context_compressor.py) `_SUMMARY_SYSTEM` |
| 目标 always-on 钉死不可压 | Claude Code CLAUDE.md | [`agent_loop.py:683`](../../backend/agent/agent_loop.py) 循环前注 1 条 `[目标锚定]` system |
| microcompact 清陈旧 tool_result | Claude Code | [`context_compressor.py:228-252`](../../backend/deskpet/agent/context_compressor.py) `_microcompact_tool_results` |
| 摘要防幻觉/反射 | issue #46602 | [`context_compressor.py:374-387`](../../backend/deskpet/agent/context_compressor.py) `_looks_reflective` |
| 锚定增量防套娃 | 通用 | [`context_compressor.py:282-334`](../../backend/deskpet/agent/context_compressor.py) `_extract_prior_summary` |
| pre-compaction flush 落盘 | OpenClaw | [`agent_loop.py:906-936`](../../backend/agent/agent_loop.py) `file_memory.append("memory", …, salience=0.6)` |
| B1 tool_result 截断 + ref-store | 通用 | [`context_manager.py:379-402`](../../backend/agent/context_manager.py) head 2500 + tail 800 + 磁盘 spill |

**本轮只补三家剩下的 7 个缺口**（下表），全部不与现架构冲突。

---

## 1. 设计原则（沿用 Round 1 + 本轮新增）

- **单调递进、宁可早压不爆窗**（沿用）。
- **任务/产物程序级硬保，不靠 LLM 概率**（本轮新增，治缺陷 #2）。
- **降复杂度优先**：双轨 → 单轨（本轮新增）。
- **绕过 frozen-snapshot**：本会话续跑用可注入的 live state，不靠 L1 回读（本轮新增）。
- **失败 safe-fail、绝不崩**（沿用）；新增**连续失败熔断**避免空转。
- **治本 > 治标**：子代理 isolated context 从源头减少需压缩量（本轮串联到已有 plan）。

---

## 2. 现状代码事实（精确接线点，已读码核实 2026-06-22）

**生产压缩链（reactive，每轮 LLM 前）**：
- 构造：[`main.py:1471-1530`](../../backend/main.py) `ContextCompressor(...)`，`compaction_enabled` 默认 **True**，`service_context.register("context_compressor", …)`。
- 注入：[`main.py:6593`](../../backend/main.py) `service_context.get("context_compressor")` → `build_agent`。
- 调用：[`agent_loop.py:864-962`](../../backend/agent/agent_loop.py)：budget guard 后 → `should_compress(_ctoken_est)` → pre-flush → `compress(working_messages, goal_text=_gt)` → `_remount_skills`。

**双轨遗留（preflight，agent.run 之前）**：
- [`main.py:6266`](../../backend/main.py) `prepare_chat_messages_for_chain` → [`chat_prep.py:29`](../../backend/agent/chat_prep.py) → [`context_manager.py:488`](../../backend/agent/context_manager.py) `prepare_chat_messages` → `:328 maybe_compact` → [`history_compactor.py:141`](../../backend/agent/history_compactor.py) `compact_messages`（**≤600 字单条 prose 摘要**，与生产 7 段结构化**不同口径**）。

**已发现的接线缺口**：
- 🐞 [`agent_loop.py:937-940`](../../backend/agent/agent_loop.py) `compress(working_messages, goal_text=_gt)` **没传 `pending_tasks`** → 子目标永远进不了锚点。
- 🐞 [`goal_store.py:200-207`](../../backend/deskpet/agent/goal_store.py) `get_pending_tasks` 读 `SessionGoal.subgoals`（[`goal_store.py:66`](../../backend/deskpet/agent/goal_store.py)），但**全仓无生产代码填 subgoals**（producer 缺位）。
- compaction 失败仅 [`agent_loop.py:957-962`](../../backend/agent/agent_loop.py) `logger.debug`，**无连续失败计数/熔断**。
- backend 无任何 `handoff` 实现（grep 仅 `picker_tools.py` 命中，无关）。
- [`file_memory.py`](../../backend/deskpet/memory/file_memory.py) `FileMemory(user_md_max_kb=20, memory_md_max_kb=50)` + `_evict_to_fit`(salience)；`read_snapshot` 是 frozen（boot 读一次，mid-session append 不回读）。

---

## 3. WI 清单（7 项）

> 每项格式：缺口 → 借鉴 → 改法（精确落点）→ DoD 可证伪断言 → 风险/降级。

### WI-A（H1）双轨压缩器收敛 — **最高优先（降复杂度）**

- **缺口**：preflight（history_compactor，≤600 字 prose）与 reactive（ContextCompressor，7 段结构化）两套口径、两套阈值并存；preflight 那条还会先把历史压成 prose，再被 reactive 压一次，**摘要套摘要 + 维护双份**。
- **借鉴**：Hermes preflight 单轨主导（一种压缩口径）。
- **改法（二选一，建议 A1）**：
  - **A1（推荐·改动最小）**：保留 `prepare_chat_messages_for_chain` 调用点，但让 `ContextManager.prepare_chat_messages` 的 preflight **不再调 history_compactor**——要么直接 identity passthrough（把压缩完全交给 agent_loop 内的 ContextCompressor），要么改成委托同一个 `ContextCompressor`。鉴于 reactive 已每轮覆盖，**首选 passthrough**：`prepare_chat_messages` 在检测到 reactive compressor 启用（`compaction_enabled=True`）时直接返回原 messages。
  - **A2（彻底但动测试多）**：删 `history_compactor.py` + `ContextManager.maybe_compact` + chat_prep 调用，统一只剩 ContextCompressor。
- **落点**：[`context_manager.py:488-507`](../../backend/agent/context_manager.py) `prepare_chat_messages`；[`main.py:6266`](../../backend/main.py) 调用处传一个 `reactive_compaction_enabled` 标志。
- **DoD**：① 一个完整 chat turn 内 history 只被一种口径压缩（断言 preflight 不产生 `[Summary of N earlier turns]` 那条 history_compactor 专属 system；reactive 只产 `[压缩摘要]` assistant）。② `compaction_enabled=True` 时 preflight 为 no-op（mock summarizer 调用次数=0）。③ `compaction_enabled=False`（回退闸）时行为不变（BC）。
- **风险**：`test_p6_context_manager.py` / `test_p6_chat_handler_ctx.py` / `test_p5s2_history_compactor.py` 需同步（A1 改断言、A2 删文件）。**建议 A1**，BC 面小。

### WI-B（C2）产物 / 文件改动「程序级硬保护」— **高优先（治缺陷 #2）**

- **缺口**：`write_file`/`edit_file`/`run_shell`/`ppt_create` 等**产物类工具**的 path/操作摘要，现在只靠 haiku 在【涉及文件/产物】段记不记得住（[`AgentImprovements.md`](../../STATUS/AgentImprovements.md) §2 缺陷 #2 标🟠中高）。摘要一漂就丢。
- **借鉴**：Claude Code「pending tasks / 文件改动是压缩必留项」；OpenClaw handoff 的 `files` 段。
- **改法**：在 `compress()` 摘要**之前**（[`context_compressor.py:254`](../../backend/deskpet/agent/context_compressor.py) `_partition` 前后），加纯函数 `_extract_artifacts(messages) -> list[str]`：
  - 扫所有 `role=="assistant"` 的 `tool_calls`，命中**产物类工具白名单**（建议放 `ContextCompressor.__init__` 参数 `artifact_tools: set[str]`，默认 `{"write_file","edit_file","apply_patch","run_shell","ppt_create",...}`）。
  - 抽 `arguments` 里的 `path`/`file_path`/`command`（截断 ~120 字），去重，按出现序拼成一个 **`[产物清单/artifacts]` system 段**（不经 LLM）。
  - 该段拼进 `system_msgs`（永不被压），与 `[目标锚定]` 并列；soft-cap 字符数（复用 `_MAX_SYSTEM_INJECT_CHARS`），超限只留最近 K 条 + 计数提示。
- **落点**：`context_compressor.py` 新增 `_extract_artifacts` 纯函数 + `compress()` 在拼 `new_messages` 时插入该段（success / passthrough / 各 safe-fail 分支都要拼，保证压缩后产物清单恒在）。
- **DoD**：① 构造含 `write_file(path=A)` + `edit_file(path=B)` 的历史 → 压缩后 system 段必含 `[产物清单]` 且含 A、B（子串断言）。② 即使 haiku 摘要被 mock 成空/反射，产物清单仍在（程序级，不依赖 LLM）。③ 无产物类工具调用时不注入空段（no-op）。④ 超 cap 时只留最近 K + `(还有 N 个产物已省略)`。
- **风险**：白名单要覆盖真实产物工具——开工前 grep [`tools/`](../../backend/deskpet/tools/) registry 的 `permission_category in {write_file, desktop_write, shell}` 工具自动生成白名单，避免漏。低风险（纯增量 system 段）。

### WI-C（O1）/handoff 结构化交接 + 治 frozen-snapshot — **高优先（体验痛点）**

- **缺口**：pre-flush 写 MEMORY.md **本会话读不到**（frozen-snapshot），只对下个 session 生效；长任务中途压缩后「继续/刚才在干嘛」靠摘要，跨 session 续跑无结构化锚点。
- **借鉴**：OpenClaw `/handoff`（objective / progress / files / pending / blockers 五段）。
- **改法**：新增 `HandoffState`（session-scoped，结构化 dict）：
  - 数据结构：`{objective, progress[], files[], pending[], blockers[], updated_at}`。
  - 存储：session-scoped 文件 `<user_data>/handoff/<session_id>.json`（不走 frozen L1，**每轮可读**）。新增 `backend/agent/handoff_store.py`（参考 `goal_store.py` 的内存 + 持久化模式）。
  - **写**：压缩前（替换/增强现有 pre-flush 块 [`agent_loop.py:906-936`](../../backend/agent/agent_loop.py)）程序级填充：objective=goal_text、files=WI-B 的产物清单、pending=get_pending_tasks、progress=最近完成步骤摘要。
  - **读（治 frozen-snapshot 关键）**：agent_loop 循环前（[`agent_loop.py:683`](../../backend/agent/agent_loop.py) 目标锚定旁）若该 session 有 handoff，注入一条 `[handoff/任务交接]` system（不可压），**当前会话即可见**。
  - 复用：pre-flush 仍可保留写 L1（跨 session 冗余兜底），但「本会话续跑」改由 handoff live state 承担。
- **落点**：新增 `backend/agent/handoff_store.py`；`agent_loop.py` 写（压缩前）+ 读（循环前）；`main.py` lifespan 构造 + `build_agent` 注入（参考 `session_goal_store` 接线 [`main.py`](../../backend/main.py)）。
- **DoD**：① 压缩触发后 handoff 文件存在且五段非空（有数据时）。② **同会话**下一轮 working_messages 含 `[handoff]` system（断言子串）——证明绕过了 frozen-snapshot。③ 新 session 同 session_id 启动能读回 handoff（持久化）。④ 无 goal/无产物时 handoff 不注入空壳。⑤ handoff system 与 `[目标锚定]` 不重复堆叠（各 ≤1 条）。
- **风险**：新增存储层（中等工作量）。降级：handoff 读写失败 → 退回纯 pre-flush 行为（best-effort，绝不阻断压缩）。注意 `/stop`/session 清理时清 handoff 文件。

### WI-D（C1）compaction circuit-breaker — **中优先（省成本）**

- **缺口**：压缩失败只 `logger.debug`，下一轮 `should_compress` 仍为真 → 每轮白烧一次 haiku + pre-flush（大上下文下持续空转）。
- **借鉴**：Claude Code「连压 3 次失败即停」。
- **改法**：在 AgentLoop 加每-run 计数器 `_compaction_fail_streak`（与现有 `_compaction_warn_logged`/`_preflush_done` latch 并列，[`agent_loop.py:706-718`](../../backend/agent/agent_loop.py)）：
  - `compress()` 返回 `compressed=False` 且 `error` 非空（或抛异常）→ `streak += 1`；成功 → 归零。
  - `streak >= N`（默认 3）→ 本 run 后续**停止调用 compressor**（短路 [`agent_loop.py:864`](../../backend/agent/agent_loop.py) 的 `if self.compressor is not None`），落一条 `compaction_circuit_open` 告警；此后只靠闸③ token_budget BLOCK 兜底。
  - 「无收益」也算一次失败：`compressed=True` 但 `reduction_ratio≈0` 且 token 未降到线下，连续 M 次也开闸（防摘要无效空转）。
- **落点**：[`agent_loop.py:706-962`](../../backend/agent/agent_loop.py) 压缩块。
- **DoD**：① mock compress 连抛 3 次 → 第 4 轮起 compressor 不再被调用（mock 调用计数封顶 3）。② 一次成功重置 streak。③ 开闸后落 `compaction_circuit_open` 事件。④ BC：默认不触发时行为与现状字节级一致。
- **风险**：极低（纯计数 + 短路）。

### WI-E（O2）task-state ledger 增量 + 子目标 producer 接线 — **中优先（抗 drift）**

- **缺口**：① compress 调用没传 `pending_tasks`（🐞 [`agent_loop.py:937-940`](../../backend/agent/agent_loop.py)）；② `SessionGoal.subgoals` 无 producer 填充；③ 现状是「单条滚动摘要」，非结构化、可增量维护的 state 对象。
- **借鉴**：OpenClaw multi-part compaction + Hermes memory `add/replace/remove` 增量。
- **改法（分两步，B 依赖 A）**：
  - **E1 接线修复（小）**：`compress(working_messages, goal_text=_gt, pending_tasks=self.session_goal_store.get_pending_tasks(session_id))`（[`agent_loop.py:937`](../../backend/agent/agent_loop.py)）。同步给循环前 always-on `[目标锚定]` 注入也带子目标（[`agent_loop.py:683`](../../backend/agent/agent_loop.py)）。
  - **E2 子目标 producer（中）**：在规划/目标设定路径填 `SessionGoal.subgoals`。最小实现：复用现有 `maybe_extract_plan`（[`agent/plan.py`](../../backend/agent/plan.py)）或 goal 设定时让 LLM 产出 3-7 条子任务，写入 `SessionGoalStore`。**与 handoff（WI-C）的 pending 段共用同一数据源**，避免双写。
- **落点**：`agent_loop.py`（E1）；`goal_store.py` + goal 设定/plan 路径（E2）。
- **DoD**：① 设了带 subgoals 的 goal → 压缩后摘要的【待办/下一步】段含子目标（子串）。② always-on `[目标锚定]` 含 `[当前子目标]`（非空）。③ E2：goal 设定后 `get_pending_tasks` 返回非空（端到端，非仅管道单测）。
- **风险**：E2 牵涉 goal 设定路径，中等。E1 可独立先交付（修🐞）。

### WI-F（H2）用户画像激进策展 — **低优先（先量后调）**

- **缺口**：`USER.md` cap 20KB 偏松，未逼出 Hermes 式精炼 always-on 画像（~3.5k）。
- **借鉴**：Hermes prompt-memory 字符上限逼策展 + `add/replace/remove` 增量维护。
- **改法**：① **先量**真实 `USER.md`/`MEMORY.md` 体积（写脚本统计现网 user_data，**不凭空改**）。② 若确实臃肿，把 `user_md_max_kb` 默认 20→~6（配置项，[`file_memory.py`](../../backend/deskpet/memory/file_memory.py)），靠现有 `_evict_to_fit`(salience) 策展。③（可选）加 `replace`/`remove` 语义工具供 LLM 主动维护画像。
- **落点**：`config.py` 默认值 + `file_memory.py`（已有 cap/evict，多半只调参）。
- **DoD**：① 调小 cap 后 `_evict_to_fit` 按 salience 正确驱逐（已有测试覆盖，补边界）。② 不误删高 salience 的 Pin 记忆。
- **风险**：调小 cap 可能误驱逐有用记忆 → **必须先量 + 真机验证画像质量不降**，否则不调。可单列、不阻塞其他 WI。

### WI-G（O3）子代理 isolated context — **指针（已有独立 plan，本轮不重复）**

- **缺口/借鉴**：OpenClaw「tool args derive from bounded task description, not ambient parent-session context」——从源头让需压缩的上下文变少 = 最高 ROI 的「压缩」。
- **现状**：已在 [`plans/2026-06-21-task-drift-fix-v2/00-research-and-best-fix.md`](../2026-06-21-task-drift-fix-v2/00-research-and-best-fix.md) + fanout 推进。
- **本轮动作**：**不重复实现**，仅在本 plan 记为「治本主线」，确认其与 WI-A~F 协同（isolated 子代理各自上下文也走同一 ContextCompressor 单轨）。WI-A 收敛后子代理压缩口径自动统一。
- **DoD**：确认子代理上下文压缩复用收敛后的单轨；无新代码。

---

## 4. 分期交付

| 期 | WI | 主题 | 依赖 | 真机门槛 |
|---|---|---|---|---|
| **第 1 期** | WI-A（收敛）· WI-B（产物硬保护）· WI-E1（接线修🐞） | 降复杂度 + 治缺陷#2 + 修子目标接线 | 无（自包含） | 长 agentic turn 压缩后产物清单可见 + 摘要含子目标 |
| **第 2 期** | WI-C（handoff）· WI-D（circuit-breaker）· WI-E2（子目标 producer） | 续跑体验 + 省成本 + 子目标端到端 | 第1期（handoff 复用 WI-B 产物清单 + WI-E pending） | 压缩后**同会话**追问「继续」桌宠仍记得（handoff live state）；跨 session 续跑 |
| **第 3 期** | WI-F（画像策展）· WI-G（确认协同） | 记忆精炼 + 治本协同 | 先量体积 | 画像调小后质量不降真机验 |

**理由**：第1期全自包含、低风险、能独立拿真机证据；第2期 handoff 牵新存储层 + 跨 session，需更重真机；第3期需前置测量/已在独立推进。

---

## 5. 测试矩阵

每 WI：**单测（可证伪自动断言）+ 集成 + 真机（windows-mcp）**。

- **单测**：WI-A no-op/BC；WI-B `_extract_artifacts` 纯函数 + 各分支拼段 + LLM 空摘要仍保产物；WI-C handoff 读写/同会话注入/持久化/去重；WI-D 连失败 3 次开闸 + 成功重置；WI-E1 compress 收到 pending_tasks + 摘要含子目标；WI-E2 goal 设定后 pending 非空；WI-F evict 边界。
- **集成**：一个 chat turn 端到端只一种压缩口径（WI-A）；压缩后 system 段同时含 `[目标锚定]`+`[产物清单]`+`[handoff]` 各 ≤1 条不堆叠。
- **真机（windows-mcp，注入 `DESKPET_BACKEND_DIR` 跑当前码 + 小窗口逼触发，参考 [`testcase/2026-06-16-compaction-bestpractice-phase1/`](../../testcase/2026-06-16-compaction-bestpractice-phase1/)）**：
  - ★ case ①：长 deepresearch（30+ 工具调用）压缩后，追问「刚才在改哪些文件」→ 桌宠答出产物清单（WI-B）。
  - ★ case ②：压缩后追问「继续/刚才在干嘛」→ **同会话**仍记得任务（WI-C handoff live state，区别于 Round1 靠摘要）。
  - case ③：故意让 haiku 连失败 → 日志 `compaction_circuit_open` 且不再空转（WI-D）。
- **回归**：`pytest tests/ -k "compact or compress or context or token or budget or goal or handoff or memory or agent_loop"` 全绿。

---

## 6. 文件清单

- **改**：
  - [`backend/agent/context_manager.py`](../../backend/agent/context_manager.py)（WI-A preflight passthrough）
  - [`backend/agent/chat_prep.py`](../../backend/agent/chat_prep.py) + [`backend/main.py`](../../backend/main.py)（WI-A 传标志）
  - [`backend/deskpet/agent/context_compressor.py`](../../backend/deskpet/agent/context_compressor.py)（WI-B `_extract_artifacts` + 拼段）
  - [`backend/agent/agent_loop.py`](../../backend/agent/agent_loop.py)（WI-C handoff 读写 · WI-D circuit-breaker · WI-E1 传 pending_tasks）
  - [`backend/deskpet/agent/goal_store.py`](../../backend/deskpet/agent/goal_store.py) + goal/plan 路径（WI-E2 producer）
  - [`backend/config.py`](../../backend/config.py) + [`backend/deskpet/memory/file_memory.py`](../../backend/deskpet/memory/file_memory.py)（WI-F 调参，**先量后改**）
- **新增**：
  - `backend/agent/handoff_store.py`（WI-C，参考 `goal_store.py`）
  - `backend/tests/test_compaction_optim_round2.py`（全 WI 单测）
- **不改**：`token_budget.py` / `tool_result_truncator.py` / `model_info.py`（基建已足）。

---

## 7. 受影响的现有测试（开工即同步）

| WI | 会被打破/需同步 |
|---|---|
| WI-A | `test_p6_context_manager.py`（`maybe_compact` 系列）· `test_p6_chat_handler_ctx.py`（passthrough 断言）· `test_p5s2_history_compactor.py`（A2 才删，A1 不破） |
| WI-B | 无破坏（新增段）；`test_compressor_tool_pairs.py` 确认产物段不破坏 tool 配对 |
| WI-C | 无破坏（新增）；新建 handoff 测试 |
| WI-D | 无破坏（新增计数）；BC 断言默认不开闸 |
| WI-E | `test_compressor_goal_anchor.py` / `test_compactor_goal_anchor.py`（子目标进锚点后断言更新） |
| WI-F | `test_*file_memory*` evict 边界 |

---

## 8. 降级 / 边界 / 风险

- 任一 WI 失败 → 退回上一级/原行为，**绝不崩**（沿用 safe-fail + circuit-breaker 思想）。
- WI-A 选 A1（passthrough）BC 面最小；A2 仅在确认 history_compactor 无其他依赖后做。
- WI-C handoff 文件读写失败 → 退回纯 pre-flush（best-effort）；session 清理要清 handoff 文件防泄漏。
- WI-F **不凭空调 cap**：先量真实体积 + 真机验画像质量，否则保持 20KB。
- WI-B 白名单覆盖不全 = 漏保产物 → 用 registry `permission_category` 自动生成白名单兜底。
- 全程复用现有基建（ref-store / token 估算 / model_info / safe-fail），不新建存储层除 handoff（WI-C 必要）。

---

## 9. 开工顺序 + 硬化建议

1. **先 codex 多轮对抗硬化本 plan** 到 EXECUTABLE-AS-IS（feedback_codex_adversarial_plan_hardening）：重点挑 WI-A passthrough 是否真无遗漏调用点、WI-C handoff 与 frozen-snapshot/session 清理的边界、WI-E2 producer 接哪条 goal 路径。
2. 第 1 期按 **WI-E1（修🐞，最小）→ WI-B（产物硬保护）→ WI-A（收敛）** 顺序（先低风险拿绿，再动收敛）。
3. 实现方式：本轮多文件交织（context_compressor + agent_loop + context_manager），参考 Round 1「Lead 直接实现单序列握全上下文，子代理做评估 + 手测文档」的裁决；或按 codex 子代理分 WI 派活（WI 间文件重叠需串行 WI-A/B/E1）。
4. 完成一个 WI 跑通验收即按 [`CLAUDE.md`](../../CLAUDE.md) STATUS 纪律更新 [`STATUS/status.md`](../../STATUS/status.md)。
