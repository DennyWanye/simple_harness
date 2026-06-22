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
| OH-1 五路混合检索 | 🔴 缺口 | ✅ **四路基础 + 可选第五路**：`retriever.py:236` 四路 RRF(vec/fts/recency/salience) + `enhanced_retriever.py` 可选叠 facts/entity/rerank/rewrite（非 RRF lane）| **降级/删除**（见 3.1） |
| OH-2 人格半衰期 | 🔴 缺口 | ✅ **衰减+Pin 机制都已实现**：`facts.py:_CATEGORY_DECAY`+`set_pinned`(882)+`preference_memory.py:68 pref_decay`(默认 off,:169+ 逻辑全有,`test_pin_and_pref_decay.py` 覆盖)+`preference_profile.py` 注入 | **降级**（只剩：决定是否默认开 pref_decay + 暴露对话式 Pin/Forget 入口） |
| OH-3 写入分级 | 🔴 缺口 | 🟡 **部分**：`session_db.py:258 skip_embed` 已有（FP-4 WI-3.4）；缺统一 `put_doc_light` 语义 + 高频流接线 | 保留（缩小范围） |
| OH-4 记忆 nudge | 🔴 缺口 | ❌ **真缺口（已实读确认）**：`memory/reflection.py` 无周期性 self-curation nudge | **保留** |
| HM-1 自我纠错闭环 | 🔴 缺口 | ✅ **机制已实现（默认 off）**：`reflection.py:StructuredReflection`(error_analysis/critique/replan)+`agent_loop.py:1395+` verify 守门+stagnation+`verify_gate.py:564 make_ephemeral_verifier`(真 async LLM)+`main.py:936` 真机注入+`agent_loop.py:1492` 真调用。⚠️ `ephemeral_subagent_model`(config.py:271) 配置**未生效**（main.py:936 复用 local/cloud_llm，专用模型 dead config）| **点亮决策 + 修 dead config + 清 stale 注释**（见 3.2） |
| HM-2 技能自创可执行 | 🔴 缺口 | 🟡 已有独立 LOCKED plan | **引用对齐**（见 3.2） |
| CC-1 skills 三级披露 + compaction 后重挂 | 🔴 缺口 | ✅ **三级披露 + compaction 后重挂都已实现且有测试**：`skill.py` auto_disclosure(embedding 强匹配+body inline+预算+LRU)+`loader.py:read_body`；`agent_loop.py:2212 _remount_skills()`（compaction 后 re-inline skill 正文，:946 真调用）+ `main.py` 已传 skill_loader/skill_matcher + `test_deskpet_skill_remount_after_compaction.py` 全套覆盖 | **确认已实现（含重挂），无接线工作**（见 3.3） |
| CC-2 plan mode 只读权限 | 🔴 缺口 | ❌ **真缺口（已实读确认）**：`code_mode/state.py:36-45` 无 plan/read_only 字段；`permissions/gate.py` 无「计划期全禁写」模式；auto_mode 反而全允许 | **保留** |
| CC-3 `/verify`+`/run` skill | 🔴 缺口 | 🟡 后端 verify-gate 已有；缺面向真机 GUI 的 bundled skill + 启动配方 | 保留 |
| CC-4 hooks exit-2 | 🔴 缺口 | ❌ **DeskPet 运行时无 hook 机制**（codingsys 的 hook 是 Claude-Code 侧，与桌宠运行时无关） | **保留（但重新定位，见 3.3）** |
| CC-5 auto-memory | 🔴 缺口 | 🟡 **部分**：`facts.py` 自动抽取+`preference_profile` 注入 = 事实级 auto-memory；缺「踩坑/配方」类 learnings | 保留（缩小范围） |
| OC-1 depth 计数 | 🟠 补强 | 🟡 **真缺口（已实读确认）**：`task_kinds.py:28-39,122-124` 仅靠剥 spawn 类工具保证 depth=1，无显式 depth 数值/上界 | 保留（小） |
| OC-2 背压指标 | 🟠 补强 | 🟡 **前端面板已有运行中指标(N/M+计时)、缺累计指标**（前端实读确认）：`SubagentProgressPanel.tsx:117-118` 运行中 N/M + :273-274 实时计时 + :193-195 完成/排队/失败汇总，已挂消息流；后端 `subagent_scheduler.py:49-50` 仅瞬时 _running/_queued + snapshot，无累计 peak/total_queued/total_rejected | 保留（小，前后端各补累计字段） |
| TG-1 Task 任务图持久化 | 🟠 | 🟡 **DAG + goal 持久化都已实现；存在三套任务概念**：`task_graph.py:98 TaskGraphStore`(DAG+claim_ready)+`session_db.py` `goal_tasks`/`session_goals` 落库 + `goal_store.py:139 bind_persistence`/:143/:166 goal 持久化（有 `test_goal_store_persistence.py`）。三套概念：`team_task_*`(TeamStore)/`goal_task_list+update`(TaskGraphStore,仅 teammate,无 create)/GoalStore | **先做设计决策：主 agent 用哪套**（见 3.5） |
| TG-2 前端审批聚合 | 🟠 | ❌ **无聚合视图（前端实读确认）**：后端 `permissions/gate.py` 完整；前端只有散落 FIFO 单弹窗（`PermissionPopup.tsx` 单请求三按钮 + `usePermissionRequests.ts:26-49` 单一 FIFO 队列一次只显示一条 + `App.tsx:1777-1780` 全局逐个展示），无批量审批 | 保留（新建 ApprovalCenterPanel，**L**）|

> **给 Lead 的取舍**：方向二真正「值得新建」的高杠杆缺口收敛为 **OH-4（记忆 nudge）/ CC-2（plan 只读权限物理拦截）/ TG-1（A 路 create 工具 + 暴露，待 Lead 拍板用哪套任务概念）/ OH-3 接线 / OC-1·OC-2 补强**。另有 **1 处真 bug：HM-1 的 `ephemeral_subagent_model` dead config（main.py:936 未消费）**。其余多是「调参点亮 + 补观测 + 补对话入口」。CC-1（含 compaction 后重挂）已实现且有测试，无接线工作。这与 2026-06-21 子代理并发 plan、FP-4/FP-5 已落地高度重叠。

---

## 3.1 openhuman → 记忆工程深化

> 对标：`research/openhuman/README.md` §2.1-2.2（记忆树/混合检索/PROFILE 半衰期/写入分级）。
> **读码核实：B2 四路检索基础(+可选第五路 facts/entity 增强层)、B3 半衰期+Pin 机制均已做好**（与 `research/deskpet-self-audit/SUMMARY.md` §1 修正一致）。本节聚焦真缺口。

#### WI-OH-1 五路混合检索  [flag: 已有，无需新 flag | 优先级 P3（降级/删除）| 对标: openhuman B2]
- 对标点：openhuman `RetrievalScoreBreakdown` 融合 graph+vector+keyword+episodic+freshness 五路（README §2.1）。
- DeskPet 现状（已实读核实）：**四路基础 RRF + 可选 facts/entity 增强层**。`backend/deskpet/memory/retriever.py:226 recall()` fan-out **四路**（vec / fts5-keyword / recency-freshness / salience-episodic，retriever.py:236 docstring 明写「四路 fan-out」）→ RRF 融合（`_RRF_K=60`，权重 vec0.5/fts0.3/recency0.15/salience0.05，retriever.py:28-30）；**第五路（facts/entity graph 三元组）在 `enhanced_retriever.py:59 EnhancedRetriever` 是可选增强层**（受 flag/store/extractor 条件，走 rerank 合并而**非 RRF 内** lane）。
- 改法：**不新建**。唯一设计决策：**是否把第五路（facts/entity）从可选增强层提升为默认 RRF lane**（接进 retriever.py 的四路 fan-out + 给一个权重项），以及 freshness 权重是否可调。⚠️ 收益边际、风险碰 1200+ 测试，建议**删除本 WI**或留作 backlog。
- BC 保证：不动 retriever 即 BC。
- 测试点：N/A（已被 `tests/test_retriever*.py` / `test_enhanced_retriever*.py` 覆盖 ⚠️ 文件名待核；若真做第五路 lane，扩展 `test_retriever*.py` 加一个「facts lane 进 RRF」case，不新建文件）。
- 依赖：无。
- 工作量预估：S（若只接 graph 路）/ 0（删除）。

#### WI-OH-2 PROFILE 人格半衰期  [flag: 已有 memory.v2.persona_inject；**pref_decay 默认开=True（决策①，2026-06-22）** | 优先级 P2（机制已实现；**默认开 pref_decay** + 补 Pin/Forget 入口，pin 入口为硬前置）| 对标: openhuman B3]
- 对标点：偏好按 class 半衰期 7-90 天衰减 + Pin/Forget + 注入 system prompt（README §2.2）。
- DeskPet 现状：**核心已实现**。① 衰减：`backend/deskpet/memory/facts.py:57 _CATEGORY_DECAY`（profile 永不衰减 / preference≈200d / goal≈200d / decision≈1y / constraint 最慢）+ `daily_decay()`(facts.py:905)。② Pin：`facts.py:882 set_pinned()` + schema `pinned` 列（`memory_v2_schema.py:102`、`schema_v2_migrator.py:37`），pinned 行跳衰减（facts.py:907 `AND pinned=0`）。③ 注入：`backend/deskpet/agent/assembler/components/preference_profile.py`（PreferenceProfileComponent，priority=85，📌 标记 Pin 置顶，读 preference/profile/constraint，**严禁谄媚措辞**）。
- 改法（已实读核实：`preference_memory.py:68 pref_decay=False` 默认关，:115 已支持 pin，:169+ 已有 decay/pinned 逻辑，`test_pin_and_pref_decay.py` 已覆盖 → **机制都在，只剩开关 + 用户入口决策**）：
  1. **默认开 `pref_decay=True`（决策①已定，2026-06-22）**：`PreferenceMemory.__init__(..., pref_decay=False)`（preference_memory.py:68）出厂默认值改为 **True**。衰减逻辑已实现（:169+）且有测试，不是「重写 JSON decay」，仅改出厂默认。
  2. **暴露用户层 Pin/Forget 入口（pin 为硬前置，必须与衰减同一批上线）**（前端已实读核实）：
     - **forget 入口前端已有**：`tauri-app/src/components/MemoryPanel.tsx:941-1060`「事实」tab 已可查看 + 🗑 删除偏好（`handleFactForget:488`，:1005-1018 遗忘按钮，test id `fact_forget_*`）。硬前置中的「forget」已满足，无需新建。
     - **pin 入口前端缺**（搜 pin/固定/star = 0 命中），但**后端 pin 已就绪**：`facts.py:882 set_pinned` + `p4_ipc.py:56/98-101` WS 路由 `memory_pin/memory_unpin` 已注册；仅前端 `types/messages.ts` 无 `memory_pin_request/response` 定义。
     - **pin 入口两条路**（默认走路 A，最省）：
       - **路 A 纯对话式（推荐，前端 0 工作量）**：用户说「记住我喜欢 X」→ 后端意图路由直接调已注册的 `memory_pin` WS（无需任何前端改动），可与衰减同批上线。
       - **路 B GUI 按钮（S）**：在 `MemoryPanel.tsx` facts 行加 pin 按钮 + 补 `types/messages.ts` 的 `memory_pin` 消息类型。
- **依赖 / 验收硬前置（决策①，前端实读后收敛）**：默认开 `pref_decay` **必须与「用户 pin/忘记某条偏好」的入口同一批上线**。否则桌宠会自动淡忘偏好，而用户无法保留想留的偏好（衰减开了但没有「钉住」的逃生口 = 用户体验倒退）。**实读后两入口现状**：① **forget 已满足**（MemoryPanel 🗑 遗忘按钮已在）；② **pin 默认走对话式路 A（前端 0 工作量）** —— 后端 `memory_pin` WS 已注册，意图路由接上即可，与衰减同批上线。验收门：pref_decay 默认 ON 的那次交付，pin（钉住跳衰减）+ forget（主动遗忘）入口可用并真机验证。两者不可拆批分别上线。
- BC 保证：**注意决策①已定 `pref_decay` 默认翻 True（非字节 BC）** —— 默认行为变（偏好开始衰减），靠强回归 + 真机验收兜底，且受 pin 硬前置约束（见上）。pin 工具未注册时不出现在 registry（字节一致），但本决策要求 pin 入口与衰减同批上线，故出厂态 = 衰减开 + pin 入口在。
- 测试点：**扩展现有 `backend/tests/test_pin_and_pref_decay.py`**（加「默认开 pref_decay 后衰减生效」+「pin 后跳衰减」case，勿新建重复文件）+ `test_preference_profile_component.py`（已有 ⚠️）；真机：对桌宠说「记住我用 neovim」→ 重启 → 下轮 prompt 含 `📌 [preference] 编辑器: neovim`（grep `preference_profile_injected`）。
- 依赖：无。
- 工作量预估：M（pin 默认走对话式路 A → 前端 0；衰减默认开 + 意图路由接 memory_pin WS 为主；路 B GUI 按钮可选 +S）。

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
> **读码核实：HM-1 机制已实现（默认 off），但发现 1 处 dead config（`ephemeral_subagent_model` 未生效）需修**；HM-2 有独立 LOCKED plan。

#### WI-HM-1 agentic JSON-mode 自我纠错闭环  [flag: structured_reflection + verify_gate.mode **默认 ON（全档默认开，含陪伴档；决策①，2026-06-22）** | 优先级 P2（机制已实现；**改默认值为全档开** + 修 dead config + 清 stale 注释）| 对标: hermes 借鉴1]
- 对标点：强制产出 `error_analysis / execution_critique / task_replanning` 结构化字段，verify 不过时由 replan 驱动自动重试，而非报错给用户（hermes README §3(2)）。
- DeskPet 现状：✅ **已完整实现**（与主纲假设的「校验不过就停」**矛盾**——读码推翻该假设）：
  - `backend/deskpet/agent/reflection.py:26 StructuredReflection`（5 段：error_analysis/execution_critique/task_replanning/next_action/confidence）+ `_REFLECTION_INSTRUCTION`(reflection.py:48) 强制 JSON 输出 + `parse_reflection` 3 级 fallback。
  - `backend/agent/agent_loop.py:1395-1520+` VerifyGate end_turn 守门：verify 不过 → 回灌 reflection schema system message + continue（自动重试，`max_verify_nudges` 默认 2）；**WI-2.2 stagnation 检测**（agent_loop.py:1450，difflib ratio>0.85 判 replan 抄袭 → 提前升级）；**ephemeral 子代理救援**（agent_loop.py:1494 `consult_ephemeral_subagent`）；`verify_exhausted` 终态。
  - `verify_gate.py:57 GoalAlignment`（WI-2.3：重述原目标 vs 客观产物对照，防 verifier 漂移）+ `agent_loop.py:1419` 把 goal_text 穿进 check。
  - `backend/deskpet/agent/external_evaluator.py` + `goal_checker.py`（独立 evaluator，对标「外部验证>自我验证」）。
- ✅ **ephemeral 救援已是真实现（非 stub）**（已实读核实）：`verify_gate.py:564-600 make_ephemeral_verifier()` 真包 async LLM call + `main.py:936-947` 真机注入 + `agent_loop.py:1492-1506` 真调用 + `agent_loop.py:1633` `ephemeral_rescued` 日志。**整条闭环代码层无缺口。** 唯一 stale：`verify_gate.py:12` 顶部注释仍写「ephemeral_verifier_subagent stub（接 LLM 留 WI-T2.4b）」，实际早已接。
- ⚠️ **已实读发现一处真 bug：`ephemeral_subagent_model` 配置未生效**。`config.py:266/271 ephemeral_subagent_model`（默认 "haiku"，:608 有白名单校验）本意是给 ephemeral 救援用专用小模型，但 `main.py:936-938 _ephemeral_llm = _make_str_llm_call(local_llm or cloud_llm, ...)` **直接复用主 local/cloud LLM，从未读 `verifier_cfg.ephemeral_subagent_model`** → 该配置是 dead config（写了校验、永不被消费）。这是真 bug，需修。
- 现状定性：机制在 `verify_gate.mode != "off"` 时可用，**默认 off**；无 subagent 时 `make_ephemeral_verifier(None)` → VerifyGate 保守失败（BC），非「生产可用」。措辞从「已完整实现」收为「机制已实现，默认 off」。
- 改法：**不新建主闭环**。真正待办：
  1. **修 dead config**：让 `main.py:936` 在构造 `_ephemeral_llm` 时按 `verifier_cfg.ephemeral_subagent_model` 选模型（若该模型对应 provider 不存在则 fallback 主 LLM 并 log warning）。否则配置项形同虚设。
  2. **出厂默认值（决策①已定，2026-06-22）：全档默认开** —— 把 `structured_reflection` 默认 False→**True**、`verify_gate.mode` 默认 "off"→**非 off**（如 "ephemeral"/"on"），**所有档位默认开，含陪伴档**（companion / code / 长 goal 一律开）。不再保持 opt-in。落地需配套 dead config 修复（见第 1 点），确保 ephemeral 救援真有可用模型。
  3. **可观测补全**：`verify_replan_stagnant` 已埋点（agent_loop.py:1482），补 `verify_exhausted` / `ephemeral_pass` 的 metrics_sink 计数，进 1B-2 dashboard。
  4. **清理过时注释**：删/改 `verify_gate.py:12` 仍写「stub 留 WI-T2.4b」的 docstring 行（实现已落地，注释误导后人）。纯文档/注释清理，无行为变化。
- BC 保证：**注意决策①已定全档默认开（非字节 BC）** —— 出厂值从 OFF 翻 ON 后，默认行为会变（verify 守门 + reflection 介入），靠强回归 + 真机验收兜底，不再以「维持 OFF = 字节一致」为基线。若需保守灰度，可临时保留 OFF→ON 的切换能力做回归对照，但**最终出厂值 = 全档开**。dead config 修复在 `verify_gate.mode != "off"` 路径生效（全档开后即默认触达）。
- 测试点：**扩展现有 `backend/tests/test_build_agent_verify_wiring.py`**（加「按 ephemeral_subagent_model 选模型」断言）+ `test_goal_loop_integration.py` / `test_goal_checker.py`（ephemeral 真实现已覆盖，勿新建 stub 测试）。真机：给桌宠一个「生成 PPT」目标，故意让首轮虚报完成 → 观察 verify 拦截 → reflection JSON → 重试真生成（grep `ephemeral_rescued` / `verify_exhausted` + receipt 落盘）。
- 依赖：无。
- 工作量预估：S（修 dead config + 点亮决策 + 观测 + 清注释；主闭环已实现）。

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

#### WI-CC-1 skills 三级渐进披露 + compaction 后重挂  [flag: 已有 skills.auto_disclosure.enabled | 优先级 P3（**已实现且有测试覆盖，无需接线工作**）| 对标: claude 4.1]
- 对标点：启动只载 name+description（字符预算）→ 触发载正文 → 附件按需 → compaction 后技能正文仍生效（claude README §2.2）。
- DeskPet 现状：✅ **三级渐进披露 + compaction 后重挂都已实现且有测试覆盖**（已实读核实，**推翻 R1「重挂是伪缺口」的结论** —— 重挂机制确实存在并已接线）：
  - **三级披露**：`backend/deskpet/agent/assembler/components/skill.py:46 SkillComponent` —— ① 一级：desc list（priority 85 never-cut，skill.py:147）；② 二级：`auto_disclosure.enabled`(默认 False) 开时 embedding 强匹配（`skill_matcher.match_async`，阈值 0.55）→ body inline（`loader.read_body`，skill.py:214）+ 预算填充（budget_tokens 默认 8000 / per_skill 2000 / LRU 淘汰）；③ 附件：`loader.py:574 invoke_script` 按需。
  - **compaction 后重挂已实现**：`agent_loop.py:2212 _remount_skills()` —— compaction 后把本 run 用过的 skill 正文（按 `_skills_used_order` MRU 序、25K char 预算、LRU-drop）re-inline 成单个 role=system `[已重挂技能]` 块（重复 compaction 不堆叠）。真调用点 `agent_loop.py:946`（compaction 成功后立即 `working_messages = self._remount_skills(...)`）；`main.py:885` 已传 skill_loader/skill_matcher（None 时 :2236 no-op = BC）；测试 `backend/tests/test_deskpet_skill_remount_after_compaction.py` 全套覆盖（R1/R2/R4/R8 + 幂等 + loader=None no-op）。
- 改法：**无需任何接线工作**——三级披露 + 重挂闭环已完整实现并测试。如需调优，仅动 `_remount_skills` 内部策略（如合并 SkillMatcher 强匹配集，:2260 已留 TODO 扩展位；或调 `_REMOUNT_TOKEN_BUDGET`）。本 WI 计 0。
- BC 保证：auto_disclosure OFF 时本就无 body 段；skill_loader 为 None 时 `_remount_skills` no-op（agent_loop.py:2236）= 字节一致。
- 测试点：已有 `backend/tests/test_deskpet_skill_remount_after_compaction.py` 覆盖；**勿新建重复测试**。若做强匹配调优，扩展该文件加一个「matcher 强匹配集并入重挂」case。
- 依赖：无（重挂行为已实读 + 测试确认）。
- 工作量预估：0（已实现）/ S（仅做可选强匹配调优）。

#### WI-CC-2 plan mode 只读权限模式  [flag: code_mode.plan_read_only 默认 False | 优先级 P1 | 对标: claude 4.2]
- 对标点：规划期作为**独立只读权限模式**，物理禁写类工具，非仅流程提示；批准时五选一审批闸（claude README §2.8）。
- DeskPet 现状（已实读核实）：❌ **物理拦截缺口**。① 已有 `features.plan_confirm_gate`（`main.py:6382`，code 模式出 plan 后 emit `awaiting_confirm` + await 用户点 [执行]/[取消]），但**确认后仍走普通工具，规划期本身不切只读** —— 即「流程提示」而非「物理只读」。② `code_tools/agent_tool.py:36 _DEFAULT_READONLY_TOOLS`（read_file/list_directory/glob/grep/web_search）只作用于 **nested `agent` 工具**，不是全局 plan-mode。③ **DeskPet 没有叫 Edit/Write 的工具**；实际写类工具真名（已实读 `tools/os_tools/registration.py`）= **`write_file`(:59) / `edit_file`(:85) / `run_shell`(:124) / `desktop_create_file`(:181)**。
- 改法（**三选一物理拦截层，推荐 ②**）：
  1. **① ToolRegistry schema 过滤**：plan 模式下 `agent_loop.run()` 工具过滤（CC-1 对齐 plan §0.2 指出 chat 模式 `agent_loop.py:605` 不传 tools_filter，过滤落点在此）直接**不暴露**写类工具 —— LLM 连工具都看不到，最干净但需改 run() 签名。
  2. **② execute 层拦截（推荐）**：`tools/registry.py:execute_tool`(618) 入口，plan 模式对写类工具名集 deny。集中、改动小、与现有 receipt/gate 同层。
  3. **③ code toolset denylist**：在 code 模式工具装配处维护 plan-mode denylist。
  - **要禁的真名集**（不写「Edit/Write」）：`{write_file, edit_file, run_shell, desktop_create_file}`（+ 任何后续新增写类，建议建一个 `_WRITE_TOOLS` 常量集中维护）。
  - 接入点：复用现有 `main.py:6382 plan_confirm_gate` 作进/出 plan 模式的信号（进「计划确认」阶段 → 置 plan 只读；用户点 [执行] → 解禁），无需另造状态机。
  - 审批：复用现有计划确认 WS 消息，扩展为「批准并执行 / 批准并逐条确认 / 带反馈重规划」（语音桌宠适配，不照搬 claude 五选一文本菜单，见 claude README §5.4 局限）。
- BC 保证：flag OFF → 不切 plan 只读、写类工具照常 = 现状字节一致。
- 测试点：`backend/tests/test_plan_mode_readonly.py`（新）—— plan 模式下 execute_tool("write_file"/"edit_file"/"run_shell"/"desktop_create_file") 被 deny；execute 模式放行。真机：code 模式计划期让桌宠尝试改文件 → 被拒 + 提示「规划期只读」→ 批准后可改。
- 依赖：无（plan_confirm_gate 已存在作接入点）。
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
- DeskPet 现状（前后端均已实读核实）：🟡 **前端面板已有运行中指标、缺累计背压指标**。
  - **前端**：`tauri-app/src/code-panel/SubagentProgressPanel.tsx` 已挂消息流并展示运行中指标（:117-118 运行中 N/M、:273-274 实时计时、:193-195 完成/排队/失败汇总）；但 `code-panel/subagentStore.ts:14-24 SubagentRunView` **无累计字段**；`code-panel/ws.ts:432-448` 后端仅推 `subagent_progress` 单条事件。
  - **后端**：`subagent_scheduler.py:85/122/174` 已发 queued/running/completed/failed 事件（:59 `_emit`，含 ts/status）+ `:180-182 snapshot()` 仅返 `{running, queued}` + `subagent_scheduled` 日志锚点(:110)。**进度事件不需重做**；**缺口收窄为累计指标**：峰值并发 / 累计排队 / 拒绝·超时计数 / queue wait 时长（P50/P95）/ export 到 metrics_sink。
- 改法（**只加累计层，前后端各补字段，不动既有进度事件**）：
  1. **后端（S，≈30 行）**：scheduler 加累计计数器，`snapshot()` 与 `subagent_progress` 事件补 `peak_concurrent / total_queued / total_rejected`（+ 可选 `lane_wait_ms` 直方图）；完成时 `observability/metrics_sink.record("subagent_lane_wait", {...})`（metrics_sink 已存在，HM-1 已用）。
  2. **前端（S，≈20 行）**：`code-panel/subagentStore.ts:14 SubagentRunView` + `SubagentProgressPanel` 加这几个累计字段展示。
- BC 保证：纯增观测，不改调度行为/不动既有事件；metrics 失败已 try/except 吞（scheduler.py:64 模式）= 无功能影响。
- 测试点：`backend/tests/test_scheduler_metrics.py`（新）—— 跑 N 个超 cap 任务 → 断言 snapshot 含 peak_concurrent/total_queued/total_rejected 累计统计；真机：并发子代理时前端面板（SubagentProgressPanel）显示累计排队/峰值并发/拒绝数。
- 依赖：无。
- 工作量预估：M（后端 S + 前端 S）。

---

## 3.5 cc-haha → Task 任务图 + 审批聚合

> 对标：`research/cc-haha/README.md` §2.2(Task 工具族)/§2.3(集中式审批)。
> **读码核实：TaskGraphStore 已实现且持久化；本节缩为「补 LLM 工具暴露 + 前端聚合」。**

#### WI-TG-1 Task 任务图：方案 A（提升 TaskGraphStore）+ 补 create/可见性 + 边界澄清  [flag: agent.goal_mode 默认 False | 优先级 P2 | 对标: cc-haha 4.1 | 决策已定: 方案A]
- 对标点：`TaskCreate/Update/List/Get` 带依赖 + 跨子 agent 共享状态 + 落盘，替代扁平 todo（cc-haha README §2.2）。
- DeskPet 现状（已实读核实）：**存储 + goal 持久化都已实现；存在三套任务概念未厘清，是本 WI 第一道坎**。
  - ✅ **DAG 存储 + 持久化已实现**：`backend/deskpet/agent/task_graph.py:98 TaskGraphStore` —— DAG（`_has_cycle` DFS 防环）+ `create` / `claim_ready`（原子认领，用 SessionDB `_write_lock`）/ `update`（done 触发 `session_goals` 进度回填）；落库走 `session_db.py` 的 `goal_tasks` 表（专用 `ensure` 守 flag-OFF 字节基线 R-T5）+ `session_goals` 表。`TaskNode`(task_graph.py:29) 含 `depends_on`/`claimed_by`/`result` = 跨 agent 共享态。
  - ✅ **goal 持久化也已实现**（**删去原「goal_store 补落库」待办——已落库**）：`goal_store.py:139 bind_persistence` / :143 `persist` / :166 `load_persisted`（+ `persist_abandon/iteration/done`）+ 测试 `test_goal_store_persistence.py` 覆盖。⚠️ **但 `goal_store.py:11-12` 文件头 docstring 仍写「Persistence is deliberately NOT implemented in v1 … 留 v2」——过期注释，与实现矛盾，需清。**
  - ⚠️ **三套任务概念（已实读，派发前必须厘清，否则 worker 乱接）**：
    - `team/teammate_tools.py:53 team_task_create`（+ _claim/_update）—— 维度是 **TeamStore**（团队协作任务），**有 create**，teammate 工具集。
    - `tools/task_graph_tools.py:32 goal_task_list` / :44 `goal_task_update` —— 维度是 **TaskGraphStore**（goal 子任务 DAG），**仅 teammate 可见、不全局注册**（:6-13 明说），且 **无 create**（节点只能内部 `session_db.create_goal_task` 走 goal 流程，未暴露 LLM 工具）。
    - `goal_store.py:GoalStore`（SessionGoalStore）—— 维度是 **goal 文本本身**（一句话目标），内存权威 + SQLite `session_goals` 持久镜像。
- 改法：
  1. ✅ **设计决策已拍板（2026-06-22 用户定）：方案 A** —— 新建 `goal_task_create/get` 把 TaskGraphStore 三件套提升为主 agent 全局可见（复用已有 DAG 存储，带依赖/持久化最完整，最贴 cc-haha Task 工具族）。
     - （备选 B「复用 `team_task_*`」已否决：TeamStore 是团队协作维度、无 DAG 依赖语义，对标 cc-haha「带依赖任务图」会缩水。）
     - 工作量 M-L（create 工具从零建 + 可见性提升接线）。**无阻塞，可直接进实现。**
  2. **（A 路）新建 `goal_task_create`（+ 可选 `goal_task_get`）工具**：schema + handler 与现有 `goal_task_list/update` 同风格放 `tools/task_graph_tools.py`，handler 调 `task_graph_store.create(..., depends_on=...)`。
  3. **（A 路）可见性提升接线**：把三件套从「仅 teammate 可见」提升到「主 agent 全局可调」 —— 接线点 `tools/registry.py`（全局注册）vs `team/teammate_tools.py`（teammate 专属）。
  4. **边界澄清 + 清过期注释（非「补落库」）**：明确 **SessionGoalStore(内存热路径权威，goal_store.py:75 _goals) vs session_db.session_goals(持久化镜像) 的职责边界**（已分层非冲突，无 source-of-truth 之争）；**清 `goal_store.py:11-12` 那段「v1 不持久化」过期注释**（实现已落 `bind_persistence/persist/load_persisted`）；persist 钩子集中（统一状态变更点触发 `persist_*`，避免多处手动调漏调致镜像漂移）。
- **goal_mode 仍默认手动（决策①区分，2026-06-22）**：与 HM-1（全档默认开）/ OH-2（pref_decay 默认开）不同，**`goal_mode` 不全局默认开，维持默认 False（手动开启）**。goal 任务图是重能力，按需手动启用，不出厂默认开。
- BC 保证：`goal_mode` 默认 False（手动）→ goal_tasks/session_goals 表**永不建**（session_db.py R-T5）= 字节基线；新 task 工具 OFF 时不注册（registry 无此工具名 → 字节一致）。
- 测试点：**扩展现有 `backend/tests/test_goal_*.py` / `test_goal_store_persistence.py`**（goal 持久化往返已覆盖，勿重建）；A 路补 `test_goal_task_create_tool.py`（create→list 往返 + 主 agent 可调断言）+ 集中 persist 钩子的镜像同步断言（加进 `test_goal_store_persistence.py`）。真机：设长 goal → 桌宠用 `goal_task_create` 建带依赖的任务 → 重启 → 任务图与依赖仍在。
- 依赖：✅ 无（设计决策已定方案 A，2026-06-22）。
- 工作量预估：**M-L**（A 路：create+get 工具从零建 + 可见性提升接线 + persist 钩子集中 + 清注释；存储/持久层已有）。

#### WI-TG-2 前端审批 UX 聚合视图  [flag: 前端开关 | 优先级 P3 | 对标: cc-haha 4.2]
- 对标点：危险命令/工具调用/agent 反问汇聚到**一个**审批入口，批量批准，主循环不打断（cc-haha README §2.3）。
- DeskPet 现状（前端已实读确认）：❌ **无聚合视图，只有散落 FIFO 单弹窗**。后端 `backend/deskpet/permissions/gate.py` 已有 3 按钮 modal 协议（gate.py:16-18「Yes once / Yes always for session / No」+ 60s 超时 auto-deny）+ pluggable responder（接 control WS broadcaster）。**前端无任何聚合/批量审批**：`tauri-app/src/components/PermissionPopup.tsx`（单请求三按钮弹窗：拒绝 / 本会话始终允许 / 允许一次）；`tauri-app/src/hooks/usePermissionRequests.ts:26-49`（单一 FIFO 队列，**一次只显示一条**，后续排队）；`App.tsx:1777-1780`（全局顶层逐个展示）。
- 改法（前端实读确认散落 → 直接进新建）：新增前端 `ApprovalCenterPanel`（聚合多个 pending PermissionRequest，支持批量批准/拒绝），接 control WS；后端 gate 增「列出当前 session pending 请求」WS 推送（gate 已持有 pending 状态）。保留纪律「不加重权限墙、只做聚合 UX」（cc-haha README §5）—— 不做细粒度权限系统。
- BC 保证：前端新面板默认隐藏/flag 控制；后端不改 gate 决策逻辑（只加只读「列 pending」接口）。
- 测试点：前端组件测试（ApprovalCenterPanel 聚合多 pending + 批量批准）+ 真机：并发触发 2+ 权限请求 → 聚合面板一屏显示 → 批量批准。
- 依赖：无。
- 工作量预估：**L**（前端从零建聚合面板 + 批量批准交互 + 后端「列 pending」WS 推送；现状仅散落 FIFO 单弹窗，无可复用聚合层）。

---

## 残留风险 / 需 Lead 定夺

1. **🔴 方向二大面积已实现 —— plan 范围需重定标**。HM-1（自我纠错闭环，含 ephemeral 真 LLM 救援机制）、OH-1（四路检索）、OH-2（半衰期+Pin 机制）、**CC-1（三级披露 + compaction 后重挂，均已实现且有测试）** 均**已落地**；TG-1 的 DAG 存储 + goal 持久化都已落地，但 **create 工具/暴露层仍缺、三套任务概念待厘清**。若照主纲「都当缺口新建」会重复造轮子。**建议 Lead 把方向二重定为「点亮 flag + 补观测 + 补对话/前端入口 + 真缺口 + 1 处 dead-config bug」**，真缺口 = OH-4 / CC-2（物理拦截）/ OC-1 / OC-2（+ OH-3 接线 / CC-3 skill / TG-1 工具与暴露 / TG-2 前端）。
2. **★ TG-1 派发前必须由 Lead 拍板「主 agent 用哪套任务概念」（唯一真·设计决策）**。已实读确认三套并存：`team_task_*`(TeamStore,有 create)/`goal_task_list+update`(TaskGraphStore,仅 teammate,无 create)/`GoalStore`(goal 文本)。方案 A（提升 TaskGraphStore 三件套 + 新建 create，DAG 最强）vs B（复用 team_task_*，无 DAG 依赖语义）。推荐 A，但 create 需从零建 + 暴露接线 → 工作量 M-L。**未拍板不进实现。** goal_store(内存权威) vs session_goals(持久镜像) 职责已厘清为分层非冲突；goal 持久化已落库（删去原「补落库」待办），仅余清 `goal_store.py:11-12` 过期注释 + persist 钩子集中。
3. **🐛 HM-1 发现 1 处真 bug：`ephemeral_subagent_model` dead config**。`config.py:271`（默认 "haiku"，:608 有白名单校验）本意给 ephemeral 救援用专用模型，但 `main.py:936-938` 直接复用 `local_llm or cloud_llm`，**从未读该配置** → 写了校验、永不消费。需修（按配置选模型 + fallback）。除此之外自我纠错闭环代码层无缺口（`make_ephemeral_verifier`/`main.py:936` 注入/`agent_loop.py:1492` 调用/:1633 日志俱在），措辞从「已完整实现」改为「机制已实现、默认 off」。另遗留 `verify_gate.py:12` 过时「stub」docstring，纯注释清理。HM-1 工作量 **S**。
4. **CC-1 确认已实现（含 compaction 后重挂），无接线工作 —— 推翻 R1「重挂是伪缺口」结论**。已实读 + 测试确认：`agent_loop.py:2212 _remount_skills()`（compaction 后 re-inline skill 正文，:946 真调用，main.py:885 传 skill_loader）+ `test_deskpet_skill_remount_after_compaction.py` 全套覆盖。R1 误判为「伪缺口/不会发生」，**本轮以实读为准更正**：重挂机制确实存在并已接线。CC-1 计 0（仅可选强匹配调优）。
5. **flag 出厂默认值（决策①已拍板，2026-06-22）**。三处定调：
   - **HM-1（structured_reflection / verify_gate.mode）= 全档默认开**（含陪伴档；companion / code / 长 goal 一律开）。
   - **OH-2（pref_decay）= 默认开（True）+ pin 入口硬前置**（衰减与 pin/forget 对话式入口必须同一批上线，否则桌宠自动淡忘而用户无法保留偏好）。
   - **TG-1（goal_mode）= 仍默认手动**（不全局默认开，重能力按需启用）。
   注：HM-1 / OH-2 翻 ON 属非字节级 BC，靠强回归 + 真机验收兜底（延续「能力已建、出厂值由产品决策」惯例，但此三项已无 Lead 待决）。
6. **✅ 前端已实读核查（2026-06-22）**。三处涉及 `tauri-app/src/` 的 WI 已逐文件读真代码定工作量：
   - **TG-2（审批聚合）= ❌ 无聚合视图 → L**：只有散落 FIFO 单弹窗（`PermissionPopup.tsx` + `usePermissionRequests.ts:26-49` 单一 FIFO 一次一条 + `App.tsx:1777-1780`），需从零建 `ApprovalCenterPanel`。
   - **OC-2（背压指标）= 前端面板已有运行中指标(N/M+计时)、缺累计指标 → M**：`SubagentProgressPanel.tsx` 已展示运行中 N/M+计时，但 `subagentStore.ts:14 SubagentRunView` 无累计字段；后端 scheduler 补 peak/total_queued/total_rejected（S）+ 前端补字段展示（S）。
   - **OH-2（pin/forget 入口）= forget 已有 + pin 走对话式(前端 0)**：`MemoryPanel.tsx:941-1060` 🗑 遗忘按钮已在（forget 满足）；pin 后端 `memory_pin` WS 已注册（`p4_ipc.py:56/98-101`），默认走对话式路 A 前端 0 工作量（路 B GUI 按钮可选 +S）。
7. **CC-4 不引入通用 hook**。DeskPet 运行时无 hook 且不应加（单机桌宠过度工程）。已有 verify-gate end_turn 守门即 Stop-hook 等价物，CC-4 主要是文档化 + 确认无遗漏，几乎无新代码。
8. **测试纪律：勿新建重复测试**。`test_goal_store_persistence.py` / `test_pin_and_pref_decay.py` / `test_deskpet_skill_remount_after_compaction.py` 均已存在；相关 WI 一律「扩展现有文件 + 加 case」，不新建同名/同域文件。

## 跨 WI 依赖图

```
独立可并行（无依赖）:
  OH-1(删/可选)  OH-2(开关+入口决策)  OH-3  CC-1(已实现含重挂,计0)  CC-2(物理拦截)  CC-3  OC-1  OC-2

强依赖链:
  OH-4(记忆nudge机制) ──► CC-5(learnings = OH-4 多产一个 category + 注入)   [合并实现]

✅ 设计决策已定（2026-06-22 用户拍板，无阻塞）:
  TG-1 ──► 方案A：提升 TaskGraphStore 三件套为主 agent 全局可见 + 新建 create+get 工具
          + 清注释 + persist 钩子集中（M-L，可直接进实现）

HM-1 含 1 处真 bug（独立可修）:
  HM-1 ──► 修 ephemeral_subagent_model dead config（main.py:936 未消费 config.py:271）

抽象共享（非阻塞，同源勿重复造）:
  HM-1(机制已实现) ◄─同一「收尾门」抽象─► CC-4(确定性未验证不收尾)
  HM-1 ──观测复用──► 1B-2 dashboard  ；  OC-2 ──► 同 dashboard

外部 plan 对齐（不在本 plan 改代码）:
  HM-2 ──► plans/2026-06-22-skill-executable-function-call (LOCKED)

已实读消解（不再阻塞）:
  TG-1 ── goal 持久化已落库（删「补落库」待办）；内存权威 vs 持久镜像职责已厘清，分层非冲突
  CC-1 ── compaction 后重挂已实现且有测试（agent_loop.py:2212 _remount_skills + 测试文件），R1 误判更正
```

> 无环。实现编排：TG-1 决策已定方案 A（无阻塞）；4 个真缺口（OH-4/CC-2 物理拦截/OC-1/OC-2）+ TG-1 独立可并行；HM-1 的 dead-config bug 独立可修；CC-1 已实现计 0。其余多为「点亮 + 观测 + 入口」。全 plan 无待决项 = EXECUTABLE-AS-IS。
