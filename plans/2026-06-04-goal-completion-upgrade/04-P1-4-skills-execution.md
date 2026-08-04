# P1-4 执行 Blueprint — Skills 分级披露 + 自创闭环

> 状态：**可执行实现 blueprint（基于真实代码事实，只读产出）**
> 日期：2026-06-04 ｜ 作者：资深架构师（深入代码后）
> 上游：[00-PLAN.md §5](./00-PLAN.md) + [research/deskpet-self-audit/p1-4-skills.md](../../research/deskpet-self-audit/p1-4-skills.md)
> 对标：claude-code（渐进披露三级 / compaction 重挂 25K 预算）、hermes-agent（技能自创触发器）

---

## 0. 现状速览（代码核实，路径确切）

| 事实 | 证据 | 对本 Phase 的含义 |
|---|---|---|
| **一级披露已做**：启动注入 `name+desc` 列表，正文留磁盘 | `assembler/components/skill.py:69-77`（只取 `name`/`summary`）、`loader.py:316-343`（`_load_single` body 不进 cache） | 不动；4.1 在它之上加「自动载正文」 |
| **二级靠 LLM 自由裁量**：`skill_invoke` 工具按需 `execute()` 重读磁盘 | `loader.py:489-517`（execute 重读磁盘）、`:611-669`（tool 注册） | 4.1 要补「embedding 相似度→系统自动载」，且与现有 skill_invoke **并存不替换** |
| **compaction 根本没接 AgentLoop** ★ | `context_compressor.py:129-133`（`should_compress` 接口存在）；`grep` 确认仅 `__init__.py` / 测试引用；`agent_loop.py` 无任何 `should_compress`/`compress` 调用 | **4.0 是地基**，4.1/4.2 全部依赖它 |
| **消息在 loop 外一次性 assemble，loop 内不重跑 assemble** ★ | `main.py:4717` `assemble()` → `:4750` `build_messages()` → 一次性 `_msgs` 传进 `_agent.run(messages=_msgs)`；`agent_loop.run():504` `working_messages = list(messages)` 之后**只在 loop 内 append**，从不回头调 `assemble()` | compaction 必须**在 loop 内**做，不能绕 assemble 重跑（assemble 在 loop 外）。4.2 重挂必须在 loop 内自己重建 skill 块 |
| **skill_prelude 是 `role=system` 消息** | `bundle.py:16,290-293`、`context_compressor.py:_partition:253-255`（system 消息全保留） | 一级 desc 列表 compaction 后**天然存活**；但 4.1 自动载入的**正文**若作为非 system 注入会落进 middle 被摘要掉 → 4.2 必须重挂正文 |
| **AgentLoop 已注入 `session_goal_store`/`goal_checker`** | `agent_loop.py:404-405,433-435`；`main.py:5300-5313` build_agent 传入 | P0-1 资产在 loop 内**已可读** → 4.0 压缩保留项 + 4.3 路径记录可直接复用 |
| **`check_budget` 已算 `estimated_tokens`+`context_window`** | `agent_loop.py:593-595`、`token_budget.py:205-255`（返回 `BudgetResult.estimated_tokens/context_window`） | 4.0 `should_compress` 直接复用此结果，**不重算 token** |
| **技能自创全缺**：`SkillMemoryStore` 孤立 CRUD，注释「Phase F 才做」 | `reflection.py:16-19`（明文）、`:167-268`（CRUD，与 `SkillLoader` 文件系统完全解耦） | 4.3 要接通 store→SKILL.md 文件，且加用户确认门 |
| **ReflectionWorker 已有后台调度范式** | `main.py:1604-1622`（`_reflection_loop` 后台 task、flag `config.memory.v2.reflection`） | 4.3 候选技能提取可复用同款 flag + 后台/末轮 hook |

**核心区分（本 blueprint 的主线）**：
- 「**能加载 skill**」= 已有（`skill_invoke` 二级 + slash 三级触发），**不是缺口**。
- 「**省 context 的分级披露**」= 缺 ①compaction 没接（4.0）②自动载正文不存在（4.1）③压缩后重挂没有（4.2）。
- 「**自创**」= 4.3 完全缺。
- **优先级**：4.0 是其余一切的地基，必须先做。

---

## WI-4.0（前置）— 接通 compaction 到 AgentLoop

### 1. 目标文件
- `backend/agent/agent_loop.py`（loop 内插 `should_compress`/`compress` 触发）— 主改点
- `backend/main.py`（构造 `ContextCompressor` 并注入 `build_agent`/`AgentLoop`，~640-750 + ~5302）
- `backend/deskpet/agent/context_compressor.py`（扩 `compress()` 支持「保留 pending tasks + 活跃 goal」）— 小改
- `backend/config.py` + config schema（新增 `config.context.compaction` flag 段）
- 测试：`backend/tests/test_deskpet_context_compressor.py`（已有）+ 新 `test_agent_loop_compaction_wiring.py`

### 2. 接通点（精确到行）+ 不绕 assemble 的约束

**插入位置**：`agent_loop.py` 的迭代循环内，**在 budget guard 之后、LLM 调用之前**（即 `:631`「Budget gate BEFORE the call」之后、`:668` `try:` LLM 调用之前）。这一段 `working_messages` 在作用域内，且 `_budget`（含 `estimated_tokens`/`context_window`）刚算完。

```python
# agent_loop.py ~ 在 line 630 (budget except 块) 之后、line 632 budget gate 之前/之后均可，
# 推荐紧接 _budget 计算后，复用其 token 估算：
if self.compressor is not None and self.compressor.should_compress(_budget.estimated_tokens):
    _cresult = await self.compressor.compress(
        working_messages,
        preserve_goal_text=_active_goal_text,      # 见下「保留项」
        preserve_pending_tasks=_pending_tasks,     # 见下
    )
    if _cresult.compressed:
        working_messages = _cresult.messages
        # 重挂 skill（4.2）在这里调用 self._remount_skills(working_messages)
        logger.info("p1_4_compaction_fired sid=%s tid=%s iter=%d "
                    "in_tok=%d out_tok=%d reduction=%.2f",
                    session_id, tid, iteration,
                    _cresult.input_tokens, _cresult.output_tokens,
                    _cresult.reduction_ratio)
        yield CompactionEvent(...)   # 可选，给前端「已压缩历史」提示
```

**关键约束「不绕 assemble」**：assemble() 在 loop 外只跑一次（`main.py:4717`），loop 内**没有也不应**回头调 assemble（会重跑 classifier/fanout/budget，开销大且语义错）。因此：
- compaction 直接在**已 assemble 好的 `working_messages`** 上原地操作。
- `skill_prelude`/`frozen_system`/`memory_block` 都是 `role=system`，`context_compressor._partition` 已保证 system 消息全保留 → **一级 desc 列表、persona、frozen system 天然不被压缩掉**。这是「不绕 assemble」的天然兑现：assemble 产出的稳定头部在 compaction 中原样保留。
- compaction 只摘要「中段非 system 的对话/工具往返」，与 assemble 的职责正交。

### 3. 压缩保留项必须含 pending tasks + 活跃 goal（与 P0-1 1.3 协同，不冲突）

`ContextCompressor.compress()` 现在只做「中段→Haiku 摘要」。扩成可注入「锚点保留块」：

- **入参新增**（保持 BC，默认 None）：`preserve_goal_text: str | None`、`preserve_pending_tasks: list[dict] | None`。
- **实现**：在生成 `summary_message` 时，把 goal_text + pending tasks **拼进摘要 prompt 的硬约束头**（「以下目标与未完成任务必须原样保留在摘要里，不得省略」），并在摘要前额外注入一条独立的 `role=system` **re-anchor 块**：
  ```
  [活跃目标 / active goal] {goal_text}
  [未完成任务 / pending tasks] - {task1} - {task2} ...
  ```
  作为 system 消息插在 `first_chunk` 之前，确保它（a）不被未来再次 compaction 吃掉（system 保留）（b）位置稳定可被 prompt cache。

- **与 1.3 re-anchoring 的分工契约（不冲突）**：
  - **1.3 负责「内容来源」**：从 `session_goal_store` 读 `goal_text`、跟踪 `GD_actions/GD_inaction` 漂移信号、决定「关键决策点」何时 re-anchor。
  - **4.0 负责「压缩时机的注入」**：在 compaction 这一个具体触发点，调用 1.3 暴露的 `get_active_goal_text(session_id)` + pending tasks 读取接口，把锚点塞进保留块。
  - **契约**：4.0 **不自己定义 goal 读取逻辑**，只消费 1.3/1.1 的接口（`session_goal_store.get_active(session_id)`）。若 1.3 尚未落地，4.0 用 `session_goal_store` 直读做降级（goal_text only，无漂移信号），后续 1.3 落地后无需改 4.0 接口。
  - **去重**：1.3 在「每次 LLM 调用前 re-anchor」与 4.0 在「compaction 时 re-anchor」可能重复注入。约定 re-anchor 块用固定 marker（`[活跃目标 / active goal]`），注入前先扫 `working_messages` 去重（已存在同 marker 的 system 块则原地更新而非追加）。

### 4. 边界 case + flag 默认值
- **空 middle**：`compress()` 已处理（`:151` no-op 返回原 messages）。
- **LLM 摘要失败**：`compress()` 已 never-raise，返回原 messages + `error` → loop 照常推进。
- **compressor=None**（未注入/flag off）：`should_compress` 短路，零开销。
- **同一 run 多次触发**：允许（长任务多轮工具往返可能多次越线）；用 warn-once latch 防日志刷屏。
- **flag**：`config.context.compaction.enabled`，**dev 默认 on / prod 默认 off**（延续字节级契约「dev 先开、prod 谨慎」惯例；compaction 改 LLM 看到的历史，prod 需先 shadow 观测）。`threshold_percent` 默认 0.75（沿用 compressor 默认），可配。

### 5. 测试计划
- **单测**：
  - `test_agent_loop_compaction_wiring.py`：mock LLM registry 喂超长 history → 断言 `should_compress` 被调、`compress` 被调、`working_messages` 被替换。
  - 断言 compaction 后 `skill_prelude` system 块仍在（`_partition` 保留 system）。
  - 断言 `preserve_goal_text` 出现在压缩后消息里（re-anchor 块存在）。
  - flag off → compressor 不被构造，loop 零调用。
- **真机 E2E**（windows-mcp，遵守 HARD CONSTRAINT）：长对话堆到越线 → 截图「压缩历史」提示 → 抓 backend log `p1_4_compaction_fired` + `reduction=` → 压缩后追问「我最初的目标是什么」LLM 仍答对（验证 re-anchor 生效）。

### 6. build order
**最先做**。4.1/4.2 依赖它。

---

## WI-4.1 — 二级披露做实（description embedding 相似度 → 自动载正文）

### 1. 目标文件
- `backend/deskpet/agent/assembler/components/skill.py`（SkillComponent 升级：desc 列表之外，对强匹配 skill **载入正文**）— 主改点
- `backend/deskpet/skills/loader.py`（新增 `read_body(name) -> str` 纯读正文方法，复用 `execute` 的磁盘读但不做 args 替换）
- 新文件 `backend/deskpet/skills/skill_matcher.py`（embedding 相似度匹配 + 预算分配）
- `backend/deskpet/agent/assembler/bundle.py`（`skill_prelude` 可承载「desc 列表 + 内联正文」两段）
- 测试：`test_deskpet_skill_auto_disclosure.py`

### 2. embedding 相似度匹配实现（复用现成 BGE-M3）

**复用现成嵌入器**：DeskPet 检索栈已有 embedding 基建（7 路检索 + VectorWorker，见 P0-3 §4 现状）。`skill_matcher` **不自建模型**，注入现有 embedder（与 retriever 同一个 BGE-M3 实例 / 同一 embedding endpoint），避免双份模型常驻。

**机制**：
1. 启动/reload 时：对每个 skill 的 `description`（+ `when_to_use` 若有）算 embedding，缓存进 `SkillMatcher`（随 `loader.reload()` 失效重算；skill 数量小，开销可忽略）。
2. 每轮 assemble 时：`SkillComponent.provide()` 拿 `ctx.user_message`（+ 近期 history 拼一小段当 query）算 query embedding → cos 相似度排序。
3. **三级分层输出**：
   - **强匹配**（sim ≥ `strong_threshold`，默认 0.55）：把 skill **正文**内联进 `skill_prelude`（截断到单 skill 上限，默认 ~2K token）。
   - **弱匹配 / 全部**：只给 `name+desc`（现状一级，不变）。
   - 这样 query 高度相关的技能正文「自动到手」，无关的不占 context — 即真正的系统级二级披露。

### 3. 字符/token 预算 + 溢出丢最少用

- **总预算**：`skill_prelude` 自动载正文段独立预算，默认 **8K token**（一级 desc 列表另算，极小）。对标 claude-code 思路（4.2 用 25K 重挂预算，此处自动载入预算保守些）。
- **分配**：强匹配按 sim 降序填正文，累计超预算即停（后续强匹配降级回 desc-only）。
- **「丢最少用」**：当多个 skill 同 sim 抢预算，用 `SkillMemoryStore.usage_count` / `last_used_at`（已有字段，`reflection.py:200`）做 tie-break，**丢最久未用的**（保留高频用的正文）。无 usage 数据时退化为纯 sim 序。
- **预算溢出整体保护**：现状 `BudgetAllocator` 对 skill bucket（priority 70）是「整块丢弃」（self-audit P4-S4 指出）。改为：正文段可被裁，但 **desc 列表段不可裁**（提到 priority 85，介于 persona 90 与 skill 70 之间），保证至少一级披露永远在。

### 4. 与现有 skill_invoke 自由裁量怎么并存（不替换）
- **二者并行，职责不同**：
  - 4.1 自动载入 = **系统**基于 query 相似度「预喂」最可能用到的正文（省一次 LLM 往返 + 省 LLM 漏调风险）。
  - `skill_invoke` 工具 = **LLM 主动**调任意 skill（包括相似度没匹中的、或 LLM 临时决定要用的）。
- **去重**：自动载入已内联正文的 skill，若 LLM 再调 `skill_invoke` 同名 → loader 照常返回正文（幂等，不报错）；可在 prelude 标注「以下技能正文已预载，无需再 skill_invoke」减少冗余调用。
- **disable_model_invocation / user_invocable** 等已有 frontmatter 字段（`loader.py:84-89`）继续生效：标了 `disable_model_invocation` 的 skill 不自动载（避免预载一个 LLM 不该自己触发的）。

### 5. 边界 case + flag 默认值
- **embedder 不可用 / 离线**：matcher 降级为纯 desc 列表（现状），不报错。
- **skill 数为 0**：现状 `provide()` 已 None-safe。
- **query 太短（如纯表情）**：sim 普遍低于阈值 → 不载正文，零额外开销。
- **flag**：`config.skills.auto_disclosure.enabled`，**dev on / prod off**（先观测自动载入命中率与 context 占用再 prod 点亮）。阈值/预算可配。

### 6. 测试计划
- **单测**：构造 3 个 skill（描述分别强/中/弱匹配某 query）→ 断言只有强匹配正文进 prelude、弱的只剩 desc；超预算时高 usage_count 的正文保留、低的降级；embedder=None 降级路径。
- **真机 E2E**：说一句明确触发某 skill 的话 → 抓 log 确认该 skill 正文被自动载（`skill_auto_loaded name=...`）、无关 skill 未载；对比说闲聊时 prelude 不含任何正文。截图对话 + log。

### 7. build order
4.0 之后。与 4.2 同批（4.2 的重挂依赖 4.1 的载入逻辑）。

---

## WI-4.2 — compaction 后 skill 重挂

### 1. 目标文件
- `backend/agent/agent_loop.py`（compaction 触发点后调 `_remount_skills`）— 主改点
- `backend/deskpet/skills/skill_matcher.py`（4.1 的 matcher 复用：提供「按最近用 skill 重挂」）
- AgentLoop 需新注入 `skill_loader` + `skill_matcher` 引用（`__init__` + `build_agent` + main.py 构造）
- 测试：`test_deskpet_skill_remount_after_compaction.py`

### 2. 为什么需要重挂（精确缺口）
- 一级 desc 列表是 system 消息 → compaction 后**仍在**（不需重挂）。
- 但 **4.1 自动载入的正文** + **LLM 通过 `skill_invoke` 拉进对话的 user-role 正文**（`loader.py:662` 返回 `{"role":"user",...}`）都是**非 system 消息**，落进 `middle` chunk → **被 Haiku 摘要成一句话**，正文细节丢失。压缩后 LLM「记得用过这个 skill」但「正文步骤没了」。

### 3. 重挂实现（接 4.0 的压缩点 + claude-code 25K 预算思路）
在 4.0 的 `if _cresult.compressed:` 分支内：
```python
working_messages = self._remount_skills(working_messages, session_id)
```
`_remount_skills` 逻辑：
1. 取「本 run 最近用过的 skill」集合 — 来源：①4.1 SkillMatcher 本轮强匹配集 ②本 run 内 `skill_invoke` 调过的 name（loop 内已可从 `record_tool_call` 追踪，`agent_loop.py:1285`）。
2. 按「最近使用顺序」+ usage_count 排序，**按 25K token 预算**重新内联正文（对标 claude-code 重挂预算；正文已在磁盘，`loader.read_body` 重读）。
3. 作为**一条新的 `role=system` skill-remount 块**插入（system → 不会被下次 compaction 再吃掉），带 marker `[已重挂技能 / remounted skills]`。
4. 超 25K 预算 → 丢最久未用（同 4.1 「丢最少用」策略）。

**接 4.0**：重挂只在 compaction 真发生后调用（`_cresult.compressed=True`），不增加非压缩轮的开销。

### 4. 边界 case + flag
- **本 run 没用过任何 skill**：`_remount_skills` no-op。
- **skill_matcher/skill_loader 未注入**：no-op（BC，旧 caller 不受影响）。
- **重复 compaction**：重挂块是 system，第二次 compaction 保留它；但要防「重挂块越堆越多」→ 重挂前先删旧 `[已重挂技能]` marker 块再插新的（单块，原地更新）。
- **flag**：跟随 4.0 的 `config.context.compaction.enabled`（重挂是 compaction 的一部分，不单独开关）。

### 5. 测试计划
- **单测**：构造「用了 skill A 的长对话」→ 触发 compaction → 断言压缩后存在 `[已重挂技能]` system 块且含 skill A 正文；断言重挂块在预算内；二次 compaction 后重挂块仍单份不重复。
- **真机 E2E**：用某 skill 做事 → 继续长对话堆到 compaction → 压缩后让 LLM 用「刚才那个 skill 的第 3 步」→ LLM 能正确执行（证明正文没丢）。抓 log `skill_remounted names=...` + 截图。

### 6. build order
紧跟 4.1（同批）。依赖 4.0（压缩点）+ 4.1（matcher/载入逻辑）。

---

## WI-4.3 — 技能自创闭环（需用户确认门）★

### 1. 目标文件
- 新文件 `backend/deskpet/skills/skill_codifier.py`（触发检测 + 候选技能生成）
- `backend/deskpet/memory/reflection.py`（`SkillMemoryStore` 接通：候选写入；改注释去掉「Phase F 才做」）
- `backend/agent/agent_loop.py`（final_answer 后 hook：调 codifier 检测；`:1127` `record_final_answer` 附近）
- `backend/main.py`（候选技能确认 WS 事件 + 确认后写 SKILL.md + 触发 `skill_loader.reload()`）
- 前端：复用现有确认门组件（见下 §4 UX）
- 新 SKILL.md 落盘目录：`<user_data>/deskpet/skills/user/<slug>/SKILL.md`（`loader.py:177-195` user 目录，热加载已支持）
- 测试：`test_deskpet_skill_codifier.py` + 前端 confirm 卡测试

### 2. hermes 触发器检测 → 依赖 P0-1 的任务路径记录

**hermes 触发条件**（满足任一 → 候选）：
1. **≥5 工具调用** 完成一个目标
2. **从错误恢复**（中途有 tool 失败/retry 后成功）
3. **被用户纠正**（用户中途否定后改方向再成功）
4. **非显然 workflow**（多工具按特定顺序组合）

**检测所需数据 = P0-1 的任务路径记录**：
- 需要：①**哪些工具按序调用** ②**每步成功/失败判定** ③**最终是否达成 goal**。
- 现状 loop 内已有部分原料：`record_tool_call(tc.name, args)`（`:1285`）、`record_final_answer`（`:1127`）、`session_goal_store`/`goal_checker`（完成判定）。但「按序 + 成功判定 + 与 goal 关联」的**结构化路径记录**是 P0-1（1.1 Durable Goal Store + 1.2 Task 图）的产物。
- **对 P0-1 的依赖契约**（4.3 消费、不自建）：
  - P0-1 须暴露 `goal_store.get_completed_path(session_id, goal_id) -> ToolPath`，其中 `ToolPath = [{tool, args_digest, ok, ts}, ...]` + `goal_text` + `corrected: bool` + `recovered_from_error: bool`。
  - 若 P0-1 路径记录未就绪，4.3 **降级**：仅从 loop 内 `record_tool_call` 序列 + final_answer 自采一个最小路径（无 corrected/recovered 信号，触发器只剩「≥5 工具」「非显然 workflow」两条）。降级路径功能可用但触发更保守。
  - **这是 4.3 唯一的硬依赖**，在 build order 中 4.3 排最后正是为此。

**生成候选**：触发后，把 ToolPath 喂给 LLM（复用 ReflectionWorker 的低频 LLM 范式，`main.py:1604`）生成 `SkillMemoryEntry{name, description, trigger_pattern, steps}` → 写 `SkillMemoryStore`（状态 `pending_confirmation`，需给 store 加一个 `status` 列或用单独 pending 表，避免未确认的进入 recall）。

### 3. 候选写哪 + 确认后写 SKILL.md 到哪
- **候选**：先写 `SkillMemoryStore`（`pending` 状态，不进 `recall`/`list_all` 默认结果，不进 skill_prelude）。
- **确认后**：codifier 把 entry 渲染成标准 SKILL.md（frontmatter `name/description/version/author=self-codified/when_to_use=trigger_pattern` + body=steps）→ 写 `<user_data>/deskpet/skills/user/<slug>/SKILL.md` → 调 `skill_loader.reload()`（热加载，`loader.py:277` 已支持）→ 下次 assemble 即可被 4.1 自动载 / `skill_invoke` 调用。
- **拒绝**：删 pending entry，不落盘。

### 4. 用户确认门 UX（复用现有确认门，不新造）
- **复用点**：DeskPet 已有「权限弹窗 / plan-confirm」类确认门（`loader.py:84` allowed_tools 权限、`main.py` 权限 popup 链路）。候选技能确认**复用同一个前端确认卡组件**（与工具权限弹窗同款 modal），而非新造一套。
- **WS 事件**：backend final_answer 后若产生候选 → 发 `skill_candidate_proposed` WS 消息（含 name/description/steps 预览）→ 前端弹「桌宠学会了一个新技能『X』，是否保存？[保存] [丢弃]」卡片（桌宠主界面气泡 / MemoryPanel 内）。
- **回执**：用户点「保存」→ 前端发 `skill_candidate_confirm{id, accept:true}` → backend 走上面「确认后写 SKILL.md」。点「丢弃」→ `accept:false` → 删 pending。
- **防打扰**：候选生成限频（每会话/每天上限，复用 `config.memory.v2.reflection` 同款 flag 风格 `config.skills.codify.enabled` + `max_candidates_per_day`）。**默认 prod off**（用户确认门 + 默认关，双重防乱造）。

### 5. 走权限门，不照搬 hermes 自动执行 Python
- **红线**：hermes 会自动执行生成的 Python；DeskPet **不照搬**。本闭环只生成**声明式 SKILL.md（Markdown body + frontmatter）**，不生成可执行 `script.py`。
- 即使未来 codified skill 需要脚本，也必须 `requires_script` 显式 + 走 `invoke_script` 的受限子进程沙箱（`loader.py:519-606` 已有 `-I` 隔离 + builtins 白名单）。**自创闭环 v1 一律不生成 script.py**（`requires_script: false` 硬编码）。
- 与全局 CLAUDE.md「不加沙箱护栏（feedback_no_sandbox_constraints）」不冲突：这里不是给桌宠加沙箱，是「自创的技能默认不可执行任意代码」——防的是「桌宠自己造出会跑代码的技能」这一手滑级风险，符合「只防手滑级破坏」。

### 6. 边界 case + flag 默认值
- **重名 skill**：codifier 生成的 name 与已有 SKILL.md 冲突 → slug 加后缀（`-v2`）或确认卡提示「将覆盖已有技能 X」让用户决定。
- **LLM 生成空/垃圾候选**：codifier 校验 name/description/steps 非空 + steps≥2，否则不弹卡。
- **用户连续拒绝同类候选**：记 reject 指纹，N 次后该 trigger_pattern 静默不再弹（防反复打扰）。
- **flag**：`config.skills.codify.enabled` **dev on / prod off**；`max_candidates_per_day` 默认 3。

### 7. 测试计划
- **单测**：
  - 喂一个「6 工具 + 1 次 retry 恢复」的 ToolPath → 断言触发、生成候选 entry（status=pending）、**未落盘 SKILL.md**。
  - confirm(accept=true) → 断言 SKILL.md 写入 user 目录 + `reload()` 被调 + 新 skill 出现在 `list_skills()`。
  - confirm(accept=false) → 断言 pending 删除、无文件。
  - 触发器各分支（<5 工具不触发、被纠正触发、降级路径只剩 2 条触发器）。
- **真机 E2E**（HARD CONSTRAINT 全套，windows-mcp）：
  - 跑一个真实多步目标（≥5 工具，如「查天气→生成 PPT→保存」）→ 完成后**截图候选确认卡弹出** → 点「保存」（真坐标点击）→ 抓 log `skill_codified path=...` + 确认 `<user_data>/.../user/<slug>/SKILL.md` 真生成 → **重启或新会话**做同类目标 → 验证该 skill 被自动载/可 `skill_invoke`（一键复用）。
  - 拒绝路径：另一候选点「丢弃」→ 截图卡消失 + 确认无落盘。
  - 每个 case 遵守 declare「坐标=(x,y)|动作|期望」+ 截图存 `plans/manual-results-<date>/screenshots/`。

### 8. build order
**最后做**。硬依赖 P0-1 的任务路径记录（ToolPath 接口）；依赖 4.0（compaction，长任务才会触发路径足够长）。

---

## 整体 build order + 依赖契约

```
① WI-4.0 接通 compaction（地基，最先）
        ├─ 复用 check_budget 的 estimated_tokens 当 should_compress 输入
        ├─ compress() 扩保留项：consume P0-1 的 get_active_goal_text + pending tasks
        └─ 与 1.3 re-anchoring 用同 marker 去重，分工：1.3 给内容、4.0 给压缩时机注入
                │
                ▼
② WI-4.1 二级披露做实（embedding 自动载正文，复用现有 BGE-M3）
        └─ 与 skill_invoke 并存不替换；预算溢出按 usage_count 丢最少用
                │ （提供 SkillMatcher + read_body 给 4.2 复用）
                ▼
③ WI-4.2 compaction 后重挂（接 4.0 压缩点 + 4.1 载入逻辑，25K 预算）
        └─ 只重挂非 system 的 skill 正文；system desc 列表天然存活
                │
                ▼
④ WI-4.3 技能自创闭环（最后，硬依赖 P0-1 路径记录）
        ├─ 触发器消费 P0-1 ToolPath（降级：自采最小路径）
        ├─ 候选→pending→用户确认门（复用权限弹窗）→写 user/SKILL.md→reload
        └─ 只生成声明式 SKILL.md，不自动执行 Python（不照搬 hermes）
```

### 对 P0-1（任务路径记录）的依赖契约（汇总）

| 4.x 消费方 | 需要 P0-1 暴露 | 降级（P0-1 未就绪时） |
|---|---|---|
| 4.0 压缩保留项 | `session_goal_store.get_active(session_id) -> goal_text` + pending tasks 列表 | 直读 `session_goal_store`（仅 goal_text，无漂移信号） |
| 4.0 re-anchor 去重 | 与 1.3 共用 marker `[活跃目标 / active goal]` | 4.0 自管 marker，1.3 落地后对齐 |
| 4.3 触发器 | `get_completed_path(session_id, goal_id) -> ToolPath`（含 tool 序、ok 判定、corrected、recovered、goal_text） | loop 内自采 `record_tool_call` 序列 + final_answer，触发器只剩「≥5 工具」「非显然 workflow」 |
| 4.3 完成判定 | `goal_checker` 完成结论（已注入 loop） | 用 final_answer stop_reason 近似 |

**契约原则**：4.x **只消费 P0-1 接口，绝不重新定义 goal/路径读取逻辑**，避免跨层 schema 漂移（feedback_cross_layer_contract）。接口未就绪一律走降级而非阻塞，保证 4.0/4.1/4.2 可独立于 P0-1 先交付，仅 4.3 必须等 P0-1。

### Flag 出厂默认值汇总（延续字节级契约「dev 先开 / prod 谨慎」）

| flag | dev | prod | 理由 |
|---|---|---|---|
| `config.context.compaction.enabled`（4.0/4.2） | on | off | 改 LLM 看到的历史，prod 先 shadow 观测 |
| `config.skills.auto_disclosure.enabled`（4.1） | on | off | 先测自动载命中率/context 占用 |
| `config.skills.codify.enabled`（4.3） | on | off | 用户确认门 + 默认关，双重防乱造 |

---

## 附：最大风险登记

1. **compaction 改历史引发目标漂移**（4.0×P0-1 交叉）：压缩把关键上下文摘掉 → LLM 跑偏。缓解 = 保留项硬注入 goal+pending tasks + re-anchor system 块 + prod 默认 off 先 shadow。**这是本 Phase 最大风险**，因为 compaction 是新接通的地基，一旦摘错内容会污染整条目标线。
2. **自动载正文反而吃爆 context**（4.1）：相似度阈值过低 → 一堆正文涌入。缓解 = 8K 预算上限 + desc 列表不可裁 + usage tie-break + prod off。
3. **自创技能乱造 / 打扰用户**（4.3）：缓解 = 用户确认门（锁定决策）+ 默认 prod off + 限频 + reject 指纹 + 只生成声明式 SKILL.md 不可执行代码。
4. **P0-1 路径记录接口漂移**（4.3 依赖）：缓解 = 明确 ToolPath 契约 + 降级路径，4.3 排最后等 P0-1 稳定。
