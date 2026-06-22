# 02 — 对标系统优化（openhuman / hermes / claude / openclaw / cc-haha）

> **状态**: DRAFT v0.1（主纲 [00-PLAN.md](./00-PLAN.md) §3.1~3.5 的展开）
> **作者**: 对标优化子代理（Claude Opus 4.8）
> **日期**: 2026-06-22
> **基线**: 全部 WI 已**读真代码核实**（master）—— 文件路径/函数签名/是否已存在均有出处；推断处标 ⚠️。
> **铁律**: 每个 WI 挂 `config` flag 出厂 OFF = 字节级 BC；不削护城河；不加沙箱护栏。

---

## 0. ⚠️ 读码后对主纲的关键修正（先看这个）

主纲 §3 的现状表与 `research/` 调研档（2026-06-04 写）相对**乐观地低估了 DeskPet 已实现度**。本次逐行读码（master, 2026-06-22）发现：调研后 DeskPet 已落了 **FP-4 / FP-5 / WI-2.x / 子代理并发 8 模式**等多批升级，对标差距比纲领假设的小很多。**结论：方向二有相当一部分是「已实现，只需点亮 flag / 补观测 / 接线」，而非「新建」。**

| 主纲 WI | 调研档判定 | **读码后真实现状** | 处置 |
|---|---|---|---|
| OH-1 五路混合检索 | 🔴 缺口 | ✅ **已实现**：`retriever.py` 4 路 RRF + `enhanced_retriever.py` 叠 facts/rerank/chunk/rewrite | **降级/删除**（见 3.1） |
| OH-2 人格半衰期 | 🔴 缺口 | ✅ **核心+Pin 都已实现**：`facts.py:_CATEGORY_DECAY`+`set_pinned`(882)+`preference_profile.py` 注入 | **降级**（仅剩 PreferenceMemory JSON 无衰减 + 对话式 Pin/Forget 入口） |
| OH-3 写入分级 | 🔴 缺口 | 🟡 **部分**：`session_db.py:258 skip_embed` 已有（FP-4 WI-3.4）；缺统一 `put_doc_light` 语义 + 高频流接线 | 保留（缩小范围） |
| OH-4 记忆 nudge | 🔴 缺口 | ❌ **真缺口（已实读确认）**：`memory/reflection.py` 无周期性 self-curation nudge | **保留** |
| HM-1 自我纠错闭环 | 🔴 缺口 | ✅ **已完整实现（含 ephemeral 真 LLM 救援）**：`reflection.py:StructuredReflection`(error_analysis/critique/replan)+`agent_loop.py:1395+` verify 守门+stagnation+`verify_gate.py:564 make_ephemeral_verifier`(真 async LLM)+`main.py:936` 真机注入+`agent_loop.py:1492` 真调用 | **确认已实现**（仅点亮+观测+清 stale 注释，见 3.2） |
| HM-2 技能自创可执行 | 🔴 缺口 | 🟡 已有独立 LOCKED plan | **引用对齐**（见 3.2） |
| CC-1 skills 三级披露 | 🔴 缺口 | ✅ **三级已实现 + 正文在受保护 system 分区（compaction 安全）**：`skill.py` auto_disclosure(embedding 强匹配+body inline+预算+LRU)+`loader.py:read_body`；compaction 只摘要 non-system middle、verbatim 保留 system | **降级为可选增强**（「重挂」是伪缺口，见 3.3） |
| CC-2 plan mode 只读权限 | 🔴 缺口 | ❌ **真缺口（已实读确认）**：`code_mode/state.py:36-45` 无 plan/read_only 字段；`permissions/gate.py` 无「计划期全禁写」模式；auto_mode 反而全允许 | **保留** |
| CC-3 `/verify`+`/run` skill | 🔴 缺口 | 🟡 后端 verify-gate 已有；缺面向真机 GUI 的 bundled skill + 启动配方 | 保留 |
| CC-4 hooks exit-2 | 🔴 缺口 | ❌ **DeskPet 运行时无 hook 机制**（codingsys 的 hook 是 Claude-Code 侧，与桌宠运行时无关） | **保留（但重新定位，见 3.3）** |
| CC-5 auto-memory | 🔴 缺口 | 🟡 **部分**：`facts.py` 自动抽取+`preference_profile` 注入 = 事实级 auto-memory；缺「踩坑/配方」类 learnings | 保留（缩小范围） |
| OC-1 depth 计数 | 🟠 补强 | 🟡 **真缺口（已实读确认）**：`task_kinds.py:28-39,122-124` 仅靠剥 spawn 类工具保证 depth=1，无显式 depth 数值/上界 | 保留（小） |
| OC-2 背压指标 | 🟠 补强 | 🟡 **真缺口（已实读确认）**：`subagent_scheduler.py:49-50` 仅瞬时 _running/_queued + snapshot，无累计 peak/total_queued/total_rejected | 保留（小） |
| TG-1 Task 任务图持久化 | 🟠 | 🟡 **DAG 已实现且持久化，但 create 工具不存在、list/update 仅 teammate 可见**：`task_graph.py:TaskGraphStore`(DAG+claim_ready)+`session_db.py` `goal_tasks`/`session_goals` 落库；`task_graph_tools.py:8-13` 仅 `goal_task_list/update` 两件、不全局注册 | **加大**（新建 create 工具 + 提升可见性，见 3.5） |
| TG-2 前端审批聚合 | 🟠 | 🟡 后端 `permissions/gate.py` 完整；前端聚合视图待核 ⚠️ | 保留 |

> **给 Lead 的取舍**：方向二真正「值得新建」的高杠杆缺口收敛为 **OH-4（记忆 nudge）/ CC-2（plan 只读权限）/ CC-4（运行时 hook 或等价确定性门）/ OH-3 与 OC-1/OC-2 的补强**。其余多是「调参点亮 + 补观测 + 补对话入口」。这与 2026-06-21 子代理并发 plan、FP-4/FP-5 已落地高度重叠。

---

## 3.1 openhuman → 记忆工程深化

> 对标：`research/openhuman/README.md` §2.1-2.2（记忆树/混合检索/PROFILE 半衰期/写入分级）。
> **读码核实：B2 五路检索、B3 半衰期核心+Pin 均已做好**（与 `research/deskpet-self-audit/SUMMARY.md` §1 修正一致）。本节聚焦真缺口。

#### WI-OH-1 五路混合检索  [flag: 已有，无需新 flag | 优先级 P3（降级/删除）| 对标: openhuman B2]
- 对标点：openhuman `RetrievalScoreBreakdown` 融合 graph+vector+keyword+episodic+freshness 五路（README §2.1）。
- DeskPet 现状：**已实现，等价或更强**。`backend/deskpet/memory/retriever.py:226 recall()` fan-out 4 路（vec / fts5-keyword / recency-freshness / salience-episodic）→ RRF 融合（`_RRF_K=60`，权重 vec0.5/fts0.3/recency0.15/salience0.05，retriever.py:28-30）；`backend/deskpet/memory/enhanced_retriever.py:59 EnhancedRetriever` 再非侵入叠加 facts(graph 等价)+cross-encoder rerank+query rewrite。
- 改法：**不新建**。可选小补强：把 entity/graph 三元组（`memory/entity_extractor.py` 已有）显式接成 RRF 第 5 路权重项（当前 enhanced 走 rerank 合并，非 RRF 内）。⚠️ 收益边际、风险碰 1200+ 测试，建议**删除本 WI**或留作 backlog。
- BC 保证：不动 retriever 即 BC。
- 测试点：N/A（已被 `tests/test_retriever*.py` / `test_enhanced_retriever*.py` 覆盖 ⚠️ 文件名待核）。
- 依赖：无。
- 工作量预估：S（若只接 graph 路）/ 0（删除）。

#### WI-OH-2 PROFILE 人格半衰期  [flag: 已有 memory.v2.persona_inject | 优先级 P2（降级）| 对标: openhuman B3]
- 对标点：偏好按 class 半衰期 7-90 天衰减 + Pin/Forget + 注入 system prompt（README §2.2）。
- DeskPet 现状：**核心已实现**。① 衰减：`backend/deskpet/memory/facts.py:57 _CATEGORY_DECAY`（profile 永不衰减 / preference≈200d / goal≈200d / decision≈1y / constraint 最慢）+ `daily_decay()`(facts.py:905)。② Pin：`facts.py:882 set_pinned()` + schema `pinned` 列（`memory_v2_schema.py:102`、`schema_v2_migrator.py:37`），pinned 行跳衰减（facts.py:907 `AND pinned=0`）。③ 注入：`backend/deskpet/agent/assembler/components/preference_profile.py`（PreferenceProfileComponent，priority=85，📌 标记 Pin 置顶，读 preference/profile/constraint，**严禁谄媚措辞**）。
- 改法：**真缺口只剩两小块**：
  1. `backend/deskpet/agent/preference_memory.py`（PreferenceMemory，存计划/意图的 JSON）**无衰减** —— 加 `daily_decay()` 等价方法（self-audit §1 指出）。⚠️ 需先读 preference_memory.py 确认数据结构。
  2. **对话式 Pin/Forget 入口**：用户说「记住我喜欢 X / 别再提 Y」→ 触发 `facts.set_pinned()` 或 `is_active=0`。新增轻量工具 `memory_pin(key, action)` 注册进 `tools/registry.py`，或在意图分类里加规则路由。
- BC 保证：preference_memory 衰减挂 flag `memory.v2.preference_decay`（默认 False，OFF=不调用 decay）；pin 工具 OFF 时不注册（registry 无此工具名 → 字节一致）。
- 测试点：`backend/tests/test_preference_memory_decay.py`（新）+ `test_preference_profile_component.py`（已有 ⚠️）；真机：对桌宠说「记住我用 neovim」→ 重启 → 下轮 prompt 含 `📌 [preference] 编辑器: neovim`（grep `preference_profile_injected`）。
- 依赖：无。
- 工作量预估：M。

#### WI-OH-3 记忆写入分级（light 快路）  [flag: memory.v2.write_tiering 默认 False | 优先级 P2 | 对标: openhuman B4]
- 对标点：`put_doc()`（embed+异步图抽）/ `put_doc_light()`（高频流跳 embedding）/ `ingest_doc()`（全同步）三级（README §2.1）。
- DeskPet 现状：🟡 **部分**。`backend/deskpet/memory/session_db.py:258 append_message(..., skip_embed=False)` 已有 light 原语（FP-4 WI-3.4，skip_embed=True 时跳 on_message_written hook → 不进 VectorWorker 队列，session_db.py:306-308）。**但缺**：① 统一的「高频流（截屏/语音 VAD tick/supervisor 感知）走 light」接线点；② `put_doc_light` 命名语义封装。
- 改法：
  1. 在写记忆的调用方（找 supervisor 感知流 / 语音 tick 写入点 ⚠️ 需定位，疑在 `backend/pipeline/` 或 supervisor 模块）按来源标 `skip_embed=True`。
  2. 可选：在 `memory/manager.py` 暴露 `write(target, ..., light: bool=False)` 形参，light=True 透传 skip_embed（manager.py:71 `MemoryManager` 已是三层 façade，加形参不破坏现有 `recall`/`write`）。
- BC 保证：flag OFF → 所有写入路径 `skip_embed=False`（现状），字节一致；flag 仅切换高频流来源的 skip_embed。
- 测试点：`backend/tests/test_write_tiering.py`（新）—— 断言 light 写入后 VectorWorker 队列未增长；真机：连续语音 tick 后查 embedder 队列不暴涨。
- 依赖：无。
- 工作量预估：S-M（核心原语已有，主要是定位高频流接线点）。

#### WI-OH-4 记忆 self-curation nudge  [flag: memory.v2.curation_nudge 默认 False | 优先级 P1 | 对标: hermes 借鉴3 + openhuman]
- 对标点：周期性给 agent 一个内部 prompt「回看刚发生的，有什么值得长期记住？」由 LLM 自决写不写（hermes README §3(4)）。
- DeskPet 现状：❌ **真缺口**。`backend/deskpet/memory/reflection.py` 有 ReflectionSummarizer（batch 总结，reflection.py:101 「no turns in window, skipping」）+ `facts.py` 规则/LLM 抽取，但**都是被动批处理**，无「agent 主动判断该不该记」环节（自查 §1「缺 agent 主动反思该不该记」一致）。注意：`backend/deskpet/agent/reflection.py`（StructuredReflection）是**纠错反思**，与记忆 curation 不同，勿混。
- 改法：
  1. 新增 `backend/deskpet/memory/curation.py::MemoryCurator`，方法 `async def nudge(self, recent_turns: list, *, llm: LLMCall) -> list[CurationDecision]`。prompt 复用 reflection.py 的 3 级 JSON fallback；输出 `[{should_remember: bool, category, key, value, reason}]`。
  2. 触发点：在 `agent_loop.py` 的 FinalEvent 之后（每 N 轮或每会话结束）异步 fire-and-forget 调 curator（**不挡主回合**，照 vector_worker 异步模式）；决定要记的走 `facts.upsert()`。
  3. 频率门控：`curation_nudge_every_n_turns`（默认 8，对齐 hermes「周期性」）。
- BC 保证：flag OFF → curator 不构造、agent_loop 不调用，字节一致（照 verify_gate=None 跳过整段的惯例）。
- 测试点：`backend/tests/test_memory_curation.py` —— mock LLM 返 should_remember=True → 断言 facts 表新增行；should_remember=False → 不写。真机：聊一段含隐含偏好的对话 → 下次冷启动桌宠「记得」该偏好（grep curation 日志 + preference_profile 注入）。
- 依赖：无（但与 OH-2 对话式 Pin 互补）。
- 工作量预估：M。

---

## 3.2 hermes → 自我纠错闭环 + 技能自创

> 对标：`research/hermes-agent/README.md` §3（agentic JSON-mode / 技能自创触发器）。
> **读码核实：HM-1 已完整实现**（远超调研档假设）；HM-2 有独立 LOCKED plan。

#### WI-HM-1 agentic JSON-mode 自我纠错闭环  [flag: 已有 structured_reflection + verify_gate.mode | 优先级 P2（点亮+调参+清 stale 注释，**确认已实现非缺口**）| 对标: hermes 借鉴1]
- 对标点：强制产出 `error_analysis / execution_critique / task_replanning` 结构化字段，verify 不过时由 replan 驱动自动重试，而非报错给用户（hermes README §3(2)）。
- DeskPet 现状：✅ **已完整实现**（与主纲假设的「校验不过就停」**矛盾**——读码推翻该假设）：
  - `backend/deskpet/agent/reflection.py:26 StructuredReflection`（5 段：error_analysis/execution_critique/task_replanning/next_action/confidence）+ `_REFLECTION_INSTRUCTION`(reflection.py:48) 强制 JSON 输出 + `parse_reflection` 3 级 fallback。
  - `backend/agent/agent_loop.py:1395-1520+` VerifyGate end_turn 守门：verify 不过 → 回灌 reflection schema system message + continue（自动重试，`max_verify_nudges` 默认 2）；**WI-2.2 stagnation 检测**（agent_loop.py:1450，difflib ratio>0.85 判 replan 抄袭 → 提前升级）；**ephemeral 子代理救援**（agent_loop.py:1494 `consult_ephemeral_subagent`）；`verify_exhausted` 终态。
  - `verify_gate.py:57 GoalAlignment`（WI-2.3：重述原目标 vs 客观产物对照，防 verifier 漂移）+ `agent_loop.py:1419` 把 goal_text 穿进 check。
  - `backend/deskpet/agent/external_evaluator.py` + `goal_checker.py`（独立 evaluator，对标「外部验证>自我验证」）。
- ✅ **ephemeral 救援已是真实现（非 stub）**（已实读核实）：`verify_gate.py:564-600 make_ephemeral_verifier()` 真包 async LLM call + `main.py:936-947` 真机注入 + `agent_loop.py:1492-1506` 真调用 + `agent_loop.py:1633` `ephemeral_rescued` 日志。**整条闭环代码层无缺口。** 唯一 stale：`verify_gate.py:12` 顶部注释仍写「ephemeral_verifier_subagent stub（接 LLM 留 WI-T2.4b）」，实际早已接。
- 改法：**不新建**。真正待办：
  1. **出厂默认值决策**：`structured_reflection` 默认 False、`verify_gate.mode` 默认 "off"（self-audit §2「已建未点亮」）。Lead 定夺是否在某档（如 code 模式 / 长 goal）默认点亮，或保持 opt-in。
  2. **可观测补全**：`verify_replan_stagnant` 已埋点（agent_loop.py:1482），补 `verify_exhausted` / `ephemeral_pass` 的 metrics_sink 计数，进 1B-2 dashboard。
  3. **清理过时注释**：删/改 `verify_gate.py:12` 仍写「stub 留 WI-T2.4b」的 docstring 行（实现已落地，注释误导后人）。纯文档/注释清理，无行为变化。
- BC 保证：两 flag 维持默认 OFF = 现状字节一致（已有惯例）。
- 测试点：已有 `backend/tests/test_build_agent_verify_wiring.py` / `test_goal_loop_integration.py` / `test_goal_checker.py`（ephemeral 真实现已有覆盖，无需新建 stub 测试）。真机：给桌宠一个「生成 PPT」目标，故意让首轮虚报完成 → 观察 verify 拦截 → reflection JSON → 重试真生成（grep `ephemeral_rescued` / `verify_exhausted` + receipt 落盘）。
- 依赖：无。
- 工作量预估：S（点亮+观测+清注释；闭环已完整实现，无代码级缺口）。

#### WI-HM-2 技能自创产可执行 function call  [flag: 见对齐 plan | 优先级 P3 | 对标: hermes 借鉴2]
- 对标点：完成多步目标后按触发器（≥5 工具调用/从错误恢复/被纠正/非显然 workflow）自评固化成**可复用、可直接调用**的技能（hermes README §3(3)）。
- DeskPet 现状：
  - **触发器闭环已实现**：`backend/deskpet/skills/skill_codifier.py:95 detect_trigger`（4 条件：≥5 步 / recovered / corrected / ≥3 distinct tools，与 hermes 触发器 1:1）+ `SkillCodifier.propose/confirm`（pending 表 + 用户确认门 + 落 SKILL.md + reload）。
  - **但只产 Markdown**：`skill_codifier.py:146 render_skill_md` 硬编码 `requires_script: false`（plan G2）；技能 = prompt 注入，**不是** LLM 可 `tool_call` 的 function。
- 改法：**不在本 plan 重复造**。已有独立 **LOCKED** plan：[`plans/2026-06-22-skill-executable-function-call/00-PLAN.md`](../2026-06-22-skill-executable-function-call/00-PLAN.md)（v1.0 R3 EXECUTABLE-AS-IS）。该 plan 引入「可执行技能 = SKILL.md + tool.json(契约) + recipe.json(声明式回放，默认) / script.py(沙箱 opt-in)」+ 动态注册进 ToolRegistry + 安全门。本 WI **仅作引用对齐**：
  - 本上下文优化 plan 不动 codifier；与该 plan 的接口约定保持一致（codifier 未来按 flag 可额外产 tool.json）。
  - ⚠️ 注意该 plan §0.2 关键架构事实：chat 模式当前**全量暴露 registry 工具**（`agent_loop.py:605` 不传 tools_filter），可见性控制落点是 `agent_loop.run()` 工具过滤，**不是** assembler。任何「自创工具可见性」改动须在那里做。
- BC 保证：见对齐 plan（全程 flag 默认 OFF）。
- 测试点：见对齐 plan。
- 依赖：**对齐** `plans/2026-06-22-skill-executable-function-call`（不并行改 codifier，避免双改冲突）。
- 工作量预估：L（但归属该独立 plan，本 plan 计 0）。

---

## 3.3 claude（Claude Code）→ harness 机制补深

> 对标：`research/claude-code/README.md` §2.2(skills 三级)/§2.8(plan mode)/§2.3(hooks)/§2.2(/verify·/run)/§2.9(auto-memory)。

#### WI-CC-1 skills 三级渐进披露（已实现；可选增强：compaction 后扩 inline 预算）  [flag: 已有 skills.auto_disclosure.enabled | 优先级 P3（可选增强，非缺口）| 对标: claude 4.1]
- 对标点：启动只载 name+description（字符预算）→ 触发载正文 → 附件按需 → compaction 后技能正文仍生效（claude README §2.2）。
- DeskPet 现状：✅ **三级渐进披露大部分已实现，且正文在受保护分区（compaction 安全）**（已实读核实，**推翻调研档「需 compaction 后重挂」的假设**）：
  - **三级披露**：`backend/deskpet/agent/assembler/components/skill.py:46 SkillComponent` —— ① 一级：desc list（priority 85 never-cut，skill.py:147）；② 二级：`auto_disclosure.enabled`(默认 False) 开时 embedding 强匹配（`skill_matcher.match_async`，阈值 0.55）→ body inline（`loader.read_body`，skill.py:214）+ 预算填充（budget_tokens 默认 8000 / per_skill 2000 / LRU 淘汰）；③ 附件：`loader.py:574 invoke_script` 按需。
  - **关键事实——「compaction 后重挂」解决的是一个不会发生的问题**：skill 正文由 SkillComponent 注入到**系统提示（role=system 分区）**；compaction（`context_compressor.py:_partition` 455-480 + `agent_loop.py:858-864`）**把所有 role=system 整段 verbatim 保留、只摘要 non-system 的 middle**。grep `reattach/remount/on_compact` 全 0 命中。**结论：skill 正文在受保护分区，compaction 永不压它 → 无需「重挂」机制。**
- 改法：**去掉「compaction 后重挂」这一伪缺口**（该缺口不存在）。**仅保留一条可选低优先增强**（非缺口）：compaction 释放掉 non-system middle 预算后，下一轮 assemble 允许 SkillComponent **提高 body inline 量**（如临时上调 `per_skill` / `budget_tokens`，把更多强匹配技能正文 inline）。属「有了更多预算就多披露」的锦上添花，不是修复。
- BC 保证：auto_disclosure OFF 时本就无 body 段；增强挂 flag `skills.post_compaction_inline_boost`（默认 False）OFF = 现状字节一致。
- 测试点（仅增强落地时需要）：`backend/tests/test_skill_inline_boost.py` —— 断言 compaction 后 assemble 的 skill body inline 量随释放预算上升；真机：长对话触发压缩后，强匹配技能正文仍在 system 分区生效（本就成立，作回归保护）。
- 依赖：无（compaction 行为已实读确认，不再依赖 1B 接线核实）。
- 工作量预估：0（不做增强）/ S（仅做可选 inline-boost）。

#### WI-CC-2 plan mode 只读权限模式  [flag: code_mode.plan_read_only 默认 False | 优先级 P1 | 对标: claude 4.2]
- 对标点：规划期作为**独立只读权限模式**，物理禁 Edit/Write，非仅流程提示；批准时五选一审批闸（claude README §2.8）。
- DeskPet 现状：❌ **真缺口**。grep `backend/deskpet/code_mode/` **无** plan_mode/read_only/permission_mode（本次 Grep 0 命中）。code 模式有「计划确认」流程（意图门→澄清→计划→执行），但**底层权限未切只读**（self-audit / 主纲 4.2 一致）—— 规划期 LLM 仍能调 write_file/edit_file。
- 改法：
  1. `backend/deskpet/permissions/gate.py` 已是中央 choke-point（gate.py:4 「Every tool call ... goes through gate.check」）。新增 `gate.set_mode(session_id, "plan" | "execute")`，plan 模式下对写类 category（write_file/edit_file/run_shell 等危险集）直接 deny（fail-closed，照 `[permissions.deny]` 路径 gate.py:13-15）。
  2. code_mode 状态机进「计划确认」阶段时调 `gate.set_mode(sid, "plan")`；用户批准后 `set_mode(sid, "execute")`。⚠️ 需读 `code_mode/state.py` 确认状态机接入点。
  3. 审批：复用现有计划确认 WS 消息，扩展为「批准并执行 / 批准并逐条确认 / 带反馈重规划」（语音桌宠适配，不照搬 claude 五选一文本菜单，见 claude README §5.4 局限）。
- BC 保证：flag OFF → gate 永远 "execute" 模式 = 现状（无 set_mode 调用，字节一致）。
- 测试点：`backend/tests/test_plan_mode_readonly.py` —— plan 模式下 `gate.check("write_file", ...)` 返 deny；execute 模式放行。真机：code 模式计划期让桌宠尝试改文件 → 被拒 + 提示「规划期只读」→ 批准后可改。
- 依赖：无（gate 已存在）。
- 工作量预估：M。

#### WI-CC-3 `/verify`+`/run` bundled skill  [flag: 作为 user skill 安装，无运行时 flag | 优先级 P2 | 对标: claude 4.3]
- 对标点：bundled `/verify`（build+run 对真实 app 确认，不退回单测）+ `/run`（启动驱动真 app）+ `run-skill-generator`（记录启动配方）（claude README §2.2/§3.3）。
- DeskPet 现状：🟡 后端 last-mile 有 artifact/receipt/verify-gate（产物层）；**缺面向真机 GUI 运行的 `/verify` skill + 启动配方记录**（主纲 4.3 一致）。DeskPet 已有 windows-mcp/SendInput 真测经验（`plans/manual-results-2026-05-26-master/UI_AUTOMATION_BREAKTHROUGH.md`）但是 Claude-Code 侧纪律，未沉淀成桌宠可调 skill。
- 改法：作为 **DeskPet 内置 SKILL.md**（走 `loader.py` built-in 目录）落地：
  1. `run-deskpet` skill：body 写「如何启动真桌宠（Tauri dev + DESKPET_BACKEND_DIR 注入 + 端口隔离，照项目 CLAUDE.md §坑 7/8/9）」，`requires_script: false`（prompt 注入即可）。
  2. `verify-deskpet` skill：body 写「对真机产物校验 SOP：截图→真坐标点击→截图→grep 日志判定，引用 SendInput 圣杯」。
  ⚠️ 注意：这些是**给开发/测试 agent 用的工程 skill**，不是给终端桌宠用户的——放 built-in 但标 `user_invocable: false`（知识片段，knowledge_enabled 门控）以免污染普通用户 /help。
- BC 保证：新增 SKILL.md 文件 + `user_invocable:false` → `loader.py:308` 默认（knowledge_enabled=False）**不进** snapshot = 字节一致。
- 测试点：`backend/tests/test_run_deskpet_skill.py`（加载 + frontmatter 解析）；真机：开 knowledge_enabled flag 后 agent 能 `skill_invoke("verify-deskpet")` 拿到 SOP。
- 依赖：无。
- 工作量预估：S（主要写 SKILL.md 内容）。

#### WI-CC-4 确定性「未验证不收尾」门（DeskPet 运行时无 hook，改用等价机制）  [flag: 见下 | 优先级 P2 | 对标: claude 4.4]
- 对标点：`Stop`/`PostToolUse` hook exit-2 阻断「没验证就收尾」，把质量门从软指令变客户端强制（claude README §2.3）。
- DeskPet 现状：❌ **DeskPet 桌宠运行时无 hook 机制**。注意区分：codingsys 的 5 个 hook 是 **Claude-Code（开发环境）侧**，对桌宠产品运行时**不生效**。桌宠 agent_loop 内**已有**确定性收尾门（这正是 hook 的等价物）：VerifyGate end_turn 守门（HM-1）= 「verify 不过不让 FinalEvent」，本质就是 `Stop` hook exit-2 的功能。
- 改法：**不引入通用 hook 系统**（对单机桌宠过度工程，违 `feedback_no_sandbox_constraints`）。把对标点重定位为「**强化已有的 end_turn 确定性门**」：
  1. 把 HM-1 的 verify_gate end_turn 守门**确认为唯一「收尾门」抽象**，文档化为「DeskPet 的 Stop-hook 等价物」。
  2. 可选补一个 `PostToolUse` 等价钩子：在 `tools/registry.py:execute_tool`(618) 完成后，对「危险写类工具」追加确定性后置校验（如 write_file 后读回校验 sha256）—— 但 receipt 机制（`tools/receipt.py`）**已做** sha256 记录，verify-gate 已对账，故此项**大概率冗余**，建议仅文档化，不新建代码。
- BC 保证：不新增通用 hook = 无 BC 风险；若加 PostToolUse 后置校验则挂 flag 默认 OFF。
- 测试点：复用 HM-1 测试 + receipt 对账测试（已有 `test_receipt_store.py` / `test_verify_gate.py`）。
- 依赖：HM-1（同一抽象）。
- 工作量预估：S（主要是文档化 + 确认无遗漏，不写新机制）。

#### WI-CC-5 auto-memory（learnings 类）  [flag: memory.v2.auto_learnings 默认 False | 优先级 P2 | 对标: claude 4.6]
- 对标点：除用户写的偏好，agent 自动把「踩坑/配方/build 命令/用户习惯」写进 per-project 轻量 memory，跨会话复用（claude README §2.9/§4.6）。
- DeskPet 现状：🟡 **部分**。事实级 auto-memory 已有：`facts.py` 自动抽取（preference/profile/project/event/reflection/goal/decision/constraint）+ `preference_profile.py` 注入。**缺**：claude 式「learnings / 踩坑 / 任务配方」这类**过程性**记忆（如「这个用户上次 PPT 要深色主题」「生成周报的步骤」）。
- 改法：**与 OH-4（curation nudge）合并实现**——curation nudge 的输出 category 扩一类 `learning`（procedural），写 facts 表（`_CATEGORY_DECAY["learning"]≈0.01`）；注入由 preference_profile 或新建 LearningsComponent（priority 略低）渲染。**避免双造**：OH-4 是机制，CC-5 是「机制产出多一个 category + 注入」。
- BC 保证：`learning` category 仅当 OH-4 flag 开时产生；注入挂 `memory.v2.auto_learnings`（默认 False）。
- 测试点：随 OH-4 测试 + `test_learnings_inject.py`。
- 依赖：**WI-OH-4**（强依赖，合并实现）。
- 工作量预估：S（OH-4 之上的增量）。

---

## 3.4 openclaw → 子代理调度打磨

> 对标：`research/comparison-gap-analysis` + `plans/2026-06-21-subagent-concurrency-driver/05-external-research.md` 8 模式。
> **读码核实：8 模式已落地（2026-06-21 V1-V5 真机 PASS）；本节是补强。**

#### WI-OC-1 depth 计数真生效  [flag: agent.subagent_explicit_depth 默认 False | 优先级 P3 | 对标: openclaw/hermes 模式6]
- 对标点：递归守门从「剥工具」升到「显式 depth 上界」（openclaw session key 编码 depth vs maxSpawnDepth；hermes max_spawn_depth=1；外部研究模式6「depth 计数留扩展位」）。
- DeskPet 现状：🟡 **结构守门已有，无显式数值**。`backend/deskpet/agent/task_kinds.py:122 _strip_forbidden` 剔除 spawn 类工具 → 子代理不能再 spawn → **隐式 depth=1**（task_kinds.py:17 注释明说「扁平 fan-out、depth=1」）；`_FORBIDDEN_IN_KIND`(task_kinds.py:30) + `teammate_tools.FORBIDDEN_TEAMMATE_TOOLS` + `agent_parallel._FORBIDDEN_NESTED_TOOLS` 三处对齐。**无显式 depth int 字段**（外部研究模式6 写「depth 计数留扩展位」未填）。
- 改法：在子代理 spawn 链（`agent/subagent_scheduler.py` / `subagent_registry.py` / spawn_team）传一个 `depth: int=0` 并在 spawn 时 `depth+1`，超 `max_spawn_depth`(默认 1，硬上限 3 对齐 openhuman) → 拒绝（forbidden）。**保留** `_strip_forbidden` 作 defence-in-depth（双保险）。
- BC 保证：flag OFF → 不检查 depth、仍靠 strip = 现状（strip 已保证 depth=1，故 OFF 行为不变，字节一致）。flag ON 才允许「显式 depth>1 受控嵌套」（未来扩展位）。
- 测试点：`backend/tests/test_subagent_depth.py` —— depth 达上界 spawn 返 forbidden；真机：复杂任务确认子代理不无限嵌套（grep depth 日志）。
- 依赖：无。
- 工作量预估：S。

#### WI-OC-2 背压/lane 指标可观测  [flag: 无（纯观测增强）| 优先级 P3 | 对标: openclaw queue.md]
- 对标点：调度器埋点（queued/running/排队时长/per-lane 占用）→ 可观测（openclaw lane 队列背压）。
- DeskPet 现状：🟡 **基础已有**。`backend/deskpet/agent/subagent_scheduler.py:180 snapshot()` 返 `{running, queued}`；`progress_sink` 发 queued→running→completed/failed 事件（scheduler.py:59 `_emit`）+ `subagent_scheduled` 日志锚点(scheduler.py:110)。**缺**：累计指标（总排队时长 P50/P95、per-lane 峰值占用、拒绝/超时计数）+ 导出到 metrics_sink。
- 改法：① scheduler 内累计 `lane_wait_ms` 直方图 + per-lane 峰值；② `snapshot()` 扩字段；③ 完成时 `observability/metrics_sink.record("subagent_lane_wait", {...})`（metrics_sink 已存在，HM-1 已用）。进 1B-2 dashboard。
- BC 保证：纯增观测，不改调度行为；metrics 失败已 try/except 吞（scheduler.py:64 模式）= 无功能影响。
- 测试点：`backend/tests/test_scheduler_metrics.py` —— 跑 N 个超 cap 任务 → 断言 snapshot 含 wait 统计；真机：并发子代理时前端面板（SubagentProgressPanel）显示排队/运行数。
- 依赖：无。
- 工作量预估：S。

---

## 3.5 cc-haha → Task 任务图 + 审批聚合

> 对标：`research/cc-haha/README.md` §2.2(Task 工具族)/§2.3(集中式审批)。
> **读码核实：TaskGraphStore 已实现且持久化；本节缩为「补 LLM 工具暴露 + 前端聚合」。**

#### WI-TG-1 Task 任务图：补 create 工具 + 提升可见性 + 一致性加固  [flag: agent.goal_mode 默认 False | 优先级 P2 | 对标: cc-haha 4.1]
- 对标点：`TaskCreate/Update/List/Get` 带依赖 + 跨子 agent 共享状态 + 落盘，替代扁平 todo（cc-haha README §2.2）。
- DeskPet 现状（已实读核实，**工作量曾被低估**）：
  - ✅ **DAG 存储 + 持久化已实现**（保留这点）：`backend/deskpet/agent/task_graph.py:TaskGraphStore` —— DAG（`_has_cycle` DFS 防环，task_graph.py:58）+ `create` / `claim_ready`（原子认领，用 SessionDB `_write_lock`）/ `update`（done 触发 `session_goals` 进度回填）；落库走 `session_db.py` 的 `goal_tasks` 表（:1094+，专用 `ensure` 守 flag-OFF 字节基线 R-T5）+ `session_goals` 表（:991+）。`TaskNode`(task_graph.py:29) 含 `depends_on`/`claimed_by`/`result` = 跨 agent 共享态。
  - ❌ **create 工具根本不存在**：`task_create`/`TaskCreate` 全 backend **0 命中**。`backend/deskpet/tools/task_graph_tools.py:8-13` 明说这些工具**不全局注册**，只在有 TaskGraphStore+goal_id 时 append 到 **teammate** 工具集；且只有 `goal_task_list`(:31) + `goal_task_update`(:43) **两个**，**无 create**。创建节点只能内部 `session_db.create_goal_task`(:1098) 走 goal 流程，**未暴露成 LLM 工具**。
  - **goal 状态分层（非冲突，已厘清）**：goal 文本以**内存为权威**（`goal_store.py:75 _goals dict`，:196 注释「永读内存最新权威」），SQLite `session_goals` 表是**持久化镜像**（`bind_persistence`/`persist`/`load_persisted`；goal_mode OFF 不建表=BC）；`goal_tasks` 表是另一维度的**子任务 DAG**，与 session_goals 不重叠、不冲突。一致性靠多处手动 `persist_*` 调用，**易漏**。
- 改法（工作量上调，create 工具需从零建）：
  1. **新建 `goal_task_create` 工具**（schema + handler，与现有 `goal_task_list/update` 同风格放 `tools/task_graph_tools.py`，handler 调 `task_graph_store.create(..., depends_on=...)`）—— 这是当前**完全缺失**的一环。
  2. **决定 task_graph_tools 三件套（create/list/update）的可见性**：是否从「仅 teammate 子代理可见」提升到「主 agent 也能全局调」。接线点：`task_graph_tools.py`（工具定义）+ `tools/registry.py`（全局注册）/ `team/teammate_tools.py`（teammate 专属）—— Lead 拍板暴露范围（主 agent 全局 vs 维持 teammate-only）。
  3. **一致性加固（定性为「加固」，非冲突/去重）**：内存权威 + SQLite 镜像分层已清晰，无 source-of-truth 之争；待办是**集中 persist 钩子**（统一在状态变更点触发 `persist_*`），避免当前多处手动调用漏调导致镜像漂移。
- BC 保证：`goal_mode` 默认 False → goal_tasks/session_goals 表**永不建**（session_db.py:991 注释 R-T5）= 字节基线；新 task 工具 OFF 时不注册（registry 无此工具名 → 字节一致）。
- 测试点：已有 `backend/tests/test_goal_*.py`；补 `test_goal_task_create_tool.py`（新建工具往返：create→list 见到该节点）+（若提升可见性）主 agent 可调断言 + `test_persist_hook.py`（状态变更后镜像必同步）。真机：设长 goal → 桌宠用 `goal_task_create` 建带依赖的任务 → 重启 → 任务图与依赖仍在。
- 依赖：无（DAG 存储与持久表已存在）。
- 工作量预估：**M-L**（create 工具从零建 + 可见性提升接线 + persist 钩子集中；存储层已有但工具/暴露层缺口比原估大）。

#### WI-TG-2 前端审批 UX 聚合视图  [flag: 前端开关 ⚠️ | 优先级 P3 | 对标: cc-haha 4.2]
- 对标点：危险命令/工具调用/agent 反问汇聚到**一个**审批入口，批量批准，主循环不打断（cc-haha README §2.3）。
- DeskPet 现状：🟡 **后端完整，前端待核**。`backend/deskpet/permissions/gate.py` 已有 3 按钮 modal 协议（gate.py:16-18「Yes once / Yes always for session / No」+ 60s 超时 auto-deny）+ pluggable responder（接 control WS broadcaster）。**前端是否有统一「待审批」聚合视图**（vs 散落弹窗）⚠️ 未核实（需读 `tauri-app/src/` 权限相关组件）。
- 改法：⚠️ **先核实前端现状**。若散落：新增前端 `ApprovalCenterPanel`（聚合多个 pending PermissionRequest，支持批量批准/拒绝）；后端 gate 增「列出当前 session pending 请求」WS 推送（gate 已持有 pending 状态 ⚠️ 核实）。注意项目纪律「不加重权限墙、只防手滑」（cc-haha README §5）—— 做**聚合 UX**，不做细粒度权限系统。
- BC 保证：前端新面板默认隐藏/flag 控制；后端不改 gate 决策逻辑（只加只读「列 pending」接口）。
- 测试点：前端组件测试 ⚠️ + 真机：并发触发 2+ 权限请求 → 聚合面板一屏显示 → 批量批准。
- 依赖：无。
- 工作量预估：M（前端为主）。

---

## 残留风险 / 需 Lead 定夺

1. **🔴 方向二大面积已实现 —— plan 范围需重定标**。HM-1（自我纠错全闭环，含 ephemeral 真 LLM 救援）、OH-1（五路检索）、OH-2 核心（半衰期+Pin）、CC-1（三级披露 + 正文受 compaction 保护）均**已落地**；TG-1 的 DAG 存储+持久表已落地但 **create 工具/暴露层仍缺**。若照主纲「都当缺口新建」会重复造轮子。**建议 Lead 把方向二重定为「点亮 flag + 补观测 + 补对话/前端入口 + 4 个真缺口 + TG-1 工具层」**，真缺口 = OH-4 / CC-2 / OC-1 / OC-2（+ OH-3 接线 / CC-3 skill / TG-1 create 工具与暴露 / TG-2 前端）。
2. **TG-1 工作量上调（create 工具不存在）**。已实读确认：`goal_task_create`/`TaskCreate` 全 backend 0 命中——create 工具需**从零建**；现有 `task_graph_tools.py` 仅 list/update 两件且**仅 teammate 可见**，是否提升到主 agent 全局可调需 Lead 拍板。goal_store(内存权威) vs session_goals(持久镜像) 职责**已厘清为分层非冲突**，原「source-of-truth 之争」消解，仅余 persist 钩子加固。工作量从 M 上调到 **M-L**。
3. **HM-1 确认已实现（非缺口）**。ephemeral 救援已是真 LLM 实现（`verify_gate.py:564 make_ephemeral_verifier` + `main.py:936` 注入 + `agent_loop.py:1492` 调用 + :1633 `ephemeral_rescued` 日志），整条自我纠错闭环代码层无缺口。唯一遗留是 `verify_gate.py:12` 过时「stub」docstring，纯注释清理。HM-1 工作量定为 **S**（点亮+观测+清注释），不再有 S/M 不确定。
4. **CC-1 从缺口改为可选增强**。已实读确认：skill 正文注入 role=system 分区，compaction（`context_compressor.py:_partition` + `agent_loop.py:858-864`）verbatim 保留 system、只摘要 non-system middle，`reattach/remount/on_compact` 0 命中——「compaction 后重挂」是**伪缺口**（不会发生）。CC-1 降为 P3 可选增强（compaction 释放预算后下一轮 assemble 多 inline 技能正文），原「复核 context_manager」前置消解。
5. **flag 出厂默认值是产品决策**。HM-1（structured_reflection / verify_gate.mode）、OH-2、TG-1（goal_mode）等大量能力「已建未点亮」。哪些在哪个档（companion / code / 长 goal）默认点亮，是产品体验 vs BC 风险的权衡，需 Lead/用户拍板（延续「字节级契约 + flag 渐进点亮」惯例）。
6. **前端现状未读** ⚠️。TG-2（审批聚合）、OC-2（进度面板）、OH-2（Pin 入口）涉及 `tauri-app/src/` 前端，本次只读了后端，前端实现度待核实后才能精确定工作量。
7. **CC-4 不引入通用 hook**。DeskPet 运行时无 hook 且不应加（单机桌宠过度工程）。已有 verify-gate end_turn 守门即 Stop-hook 等价物，CC-4 主要是文档化 + 确认无遗漏，几乎无新代码。

## 跨 WI 依赖图

```
独立可并行（无依赖）:
  OH-1(删/可选)  OH-2(降级)  OH-3  CC-1(可选增强,非缺口)  CC-2  CC-3  OC-1  OC-2  TG-1(create工具+暴露)  TG-2

强依赖链:
  OH-4(记忆nudge机制) ──► CC-5(learnings = OH-4 多产一个 category + 注入)   [合并实现]

抽象共享（非阻塞，同源勿重复造）:
  HM-1(自我纠错) ◄─同一「收尾门」抽象─► CC-4(确定性未验证不收尾)
  HM-1(已实现)  ──观测复用──► 1B-2 dashboard  ；  OC-2 ──► 同 dashboard

外部 plan 对齐（不在本 plan 改代码）:
  HM-2 ──► plans/2026-06-22-skill-executable-function-call (LOCKED)

前置厘清（已实读消解，不再阻塞）:
  TG-1 ── goal_store(内存权威) vs session_goals(持久镜像) 职责已厘清，分层非冲突（仅 persist 钩子加固）
  CC-1 ── compaction 行为已实读确认（system 分区 verbatim 保留），无「重挂」缺口
```

> 无环。真正的实现编排：先做 4 个真缺口（OH-4/CC-2/OC-1/OC-2，皆独立可并行）；TG-1 工作量上调（create 工具从零建 + 暴露），其余多为「点亮 + 观测 + 入口」。
