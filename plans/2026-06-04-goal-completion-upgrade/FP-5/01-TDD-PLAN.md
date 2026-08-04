# FP-5「Skills 分级披露 + 自创闭环」Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps `- [ ]`. **最高回归风险 FP**（4.0 改主 loop 影响所有长会话）→ 单独冲刺，4.0 先做 + 全回归，再 4.1+。

**Goal:** 省 context 的三级披露——4.0 接通 compaction(地基) + 4.1 embedding 强匹配自动载正文 + 4.2 压缩后 skill 重挂 + 4.3 技能自创(用户确认门，只生成声明式 SKILL.md 不执行代码)。

**Architecture:** 复用已做好的 ContextCompressor(FP-2 已给 compress() 加 goal_text/pending_tasks 参数)、check_budget 的 estimated_tokens、BGE-M3 embedder、SkillLoader/SkillMemoryStore、FP-1 的 ToolPath recorder(WI-1.6 已交付 get_completed_path)。

**Tech Stack:** 同前 + 前端 Tauri/React(4.3c 确认卡)。测试绝对路径 venv python。

**权威依据：** [04-P1-4-skills-execution.md](../04-P1-4-skills-execution.md)（逐 WI + 行号）+ [FP-1/00-CONTRACT-FREEZE.md §1.4](../FP-1/00-CONTRACT-FREEZE.md)（get_completed_path → ToolPath）+ FP-2 WI-1.3（compress goal_text 参数 + `[目标锚定]` marker，4.0 复用+对齐去重）。

---

## 关键协同 / 复用（避免重造）
- **compress() 已有 goal_text/pending_tasks 参数**（FP-2 Task1 加）→ 4.0 直接复用，不新增 preserve_* 参数。
- **re-anchor marker 对齐**：FP-2 WI-1.3 决策点注入用 `[目标锚定]`；4.0 compaction-time 注入**用同一 marker**，注入前扫 working_messages 去重（已存在同 marker system 块则原地更新不追加）。
- **ToolPath 已交付**：FP-1 WI-1.6 `tool_path.py` 的 `ToolPathRecorder.get_completed_path(sid, goal_id)` → 4.3 触发器消费（不自建）。
- **check_budget.estimated_tokens** → 4.0 should_compress 输入，不重算 token。
- **BGE-M3 embedder**（retriever 同实例）→ 4.1 SkillMatcher 注入，不双份模型。

---

## Task 1: WI-4.0 接通 compaction（地基，最先 + 全回归）★
**Files:** `agent/agent_loop.py`(loop 内 budget guard 后/LLM 调用前插 should_compress/compress) · `main.py`(构造 ContextCompressor 注入 build_agent/AgentLoop) · `config.py`(context.compaction flag 段) · 测试 `test_agent_loop_compaction_wiring.py`(新建)
依据 [04 §WI-4.0](../04-P1-4-skills-execution.md)（精确插入点 + 不绕 assemble 约束）。
- [ ] **Step 1 失败测试**：mock LLM registry 喂超长 history → should_compress 被调 + compress 被调 + working_messages 被替换；压缩后 skill_prelude system 块仍在(_partition 保留 system)；`goal_text` 复用 FP-2 参数 → re-anchor 块存在且用 `[目标锚定]` marker 去重(不重复注入)；**flag off → compressor 不构造 / loop 零调用(字节 BC)**；同 run 多次触发(warn-once latch)。
- [ ] **Step 2-4** 实现：agent_loop 在 budget guard 后(`_budget.estimated_tokens` 刚算完)插 `if self.compressor and self.compressor.should_compress(_budget.estimated_tokens): _cresult=await self.compressor.compress(working_messages, goal_text=_active_goal_text, pending_tasks=_pending)`；compressed 时替换 + log `p1_4_compaction_fired ... reduction=`；**不绕 assemble**(loop 内原地操作已 assemble 的 working_messages)；保留项注入 goal+pending 用 `[目标锚定]` marker 去重(对齐 FP-2 1.3)；main.py 构造 ContextCompressor(context_window 32000) 注入。
- [ ] **Step 5 ★全回归**：跑全 agent_loop + compressor + 长会话相关 suite，确认无回归（4.0 是所有会话走的主 loop）。
- [ ] **Step 6** commit。
> flag `context.compaction.enabled` dev on / prod off（改 LLM 看到的历史，prod 先 shadow）。

## Task 2: WI-4.1 二级披露做实（embedding 强匹配自动载正文）
**Files:** `skills/skill_matcher.py`(新建,注入 embedder) · `assembler/components/skill.py`(SkillComponent 升级:强匹配载正文) · `skills/loader.py`(read_body 纯读) · `assembler/bundle.py`(prelude 承载 desc+正文两段) · `config.py`(skills.auto_disclosure flag) · 测试
依据 [04 §WI-4.1](../04-P1-4-skills-execution.md)。
- [ ] **Step 1 失败测试**：3 skill(强/中/弱匹配某 query)→只强匹配(sim≥0.55)正文进 prelude、弱的只 desc；8K 预算溢出按 usage_count 丢最久未用;desc 列表段不可裁(priority 85);embedder=None 降级纯 desc(不报错);与 skill_invoke 并存(disable_model_invocation 标的不自动载)。
- [ ] **Step 2-4** 实现(SkillMatcher 缓存 skill desc embedding,随 reload 失效;query embedding cos 排序;三级分层;`read_body` 复用 execute 磁盘读不做 args 替换;encode 走 `await asyncio.to_thread`[T2]+缓存[R-T8])+ 跑通。
- [ ] **Step 5** commit。
> flag `skills.auto_disclosure.enabled` dev on / prod off。

## Task 3: WI-4.2 compaction 后 skill 重挂
**Files:** `agent/agent_loop.py`(4.0 压缩点后调 `_remount_skills`) · `skills/skill_matcher.py`(复用) · AgentLoop 注入 skill_loader+skill_matcher · 测试
依据 [04 §WI-4.2](../04-P1-4-skills-execution.md)。
- [ ] **Step 1 失败测试**：用了 skill A 的长对话→compaction→压缩后存在 `[已重挂技能]` system 块含 A 正文(25K 预算内);二次 compaction 重挂块单份不重复(注入前删旧 marker 块);本 run 没用 skill→no-op;skill_matcher/loader 未注入→no-op(BC)。
- [ ] **Step 2-4** 实现(`_remount_skills`:取本 run 最近用的 skill[4.1 强匹配集 + skill_invoke 调过的 name],按最近用+usage 排序,25K 预算重内联正文作新 system 块,marker `[已重挂技能]`,超预算丢最久未用)+ 跑通。
- [ ] **Step 5** commit。
> flag 跟随 4.0 的 context.compaction.enabled。

## Task 4: WI-4.3a-d 技能自创闭环（后端 + 前端确认卡）★
**Files(后端):** `skills/skill_codifier.py`(新建,触发检测+候选生成) · `memory/reflection.py`(SkillMemoryStore 加 pending status) · `agent/agent_loop.py`(final_answer 后 hook 调 codifier) · `main.py`(skill_candidate_proposed/confirm WS verb + 确认后写 user/SKILL.md + reload) · `config.py`(skills.codify flag + max_candidates_per_day) **Files(前端 T7):** `tauri-app/src/`(新确认卡组件 + WS verb 白名单,复用 plan-confirm 视觉但新通道) · 测试
依据 [04 §WI-4.3](../04-P1-4-skills-execution.md) + freeze §1.4 ToolPath。
- [ ] **Step 1 失败测试(后端)**：喂「6 工具+1 retry 恢复」ToolPath(从 FP-1 get_completed_path)→触发(hermes:≥5工具/从错误恢复/被纠正/非显然)+生成候选(status=pending,**未落盘 SKILL.md**);confirm(accept=true)→写 `<user_data>/skills/user/<slug>/SKILL.md` + reload 被调 + 新 skill 进 list_skills;confirm(accept=false)→删 pending 无文件;触发器各分支(<5 工具不触发/被纠正触发/降级路径只剩2条);**只生成声明式 SKILL.md,requires_script=false 硬编码(不照搬 hermes 自动执行 Python)**。
- [ ] **Step 2-4(后端)** 实现 codifier + SkillMemoryStore pending + agent_loop hook + main.py WS verb(skill_candidate_proposed/confirm,Future-await 按 candidate_id key,T7)+ 写 SKILL.md + reload + 限频(max_candidates_per_day=3,reject 指纹)。
- [ ] **Step 5(前端 T7)** ⚠️**新接线非纯复用**：tauri-app 新确认卡组件(可复用 plan-confirm 卡视觉但通道新建——现 `_PLAN_CONFIRM_WAITERS` 是 code 模式 plan 专用通不到桌宠主 UI)+ WS verb 白名单 + 前端测试。**前端改动建议用 flutter/typescript-reviewer 或单独前端子代理。**
- [ ] **Step 6** commit。
> flag `skills.codify.enabled` dev on / prod off + 用户确认门双重防乱造。

## Task 5: 🚦手测门 + STATUS
> 复用 harness。证据存 `plans/manual-results-2026-06-*-FP-5/`。**4.0 改主 loop → 先全回归长会话**。
- [ ] **MR-4.0 压缩后追原目标（🔴 真机+全回归）**：长对话堆过压缩阈值→截图「压缩历史」+log `p1_4_compaction_fired reduction=`→追问「我最初目标是什么」LLM 答对(re-anchor 生效)。
- [ ] **MR-4.1 相关 skill 自动载（🟠 log）**：说触发某 skill 的话→log `skill_auto_loaded name=`+无关 skill 未载。
- [ ] **MR-4.3 技能自创（🔴 真模拟人全链）**：多步目标(≥5 工具)→候选确认卡弹出→**真坐标点击保存**→`<user_data>/skills/user/<slug>/SKILL.md` 真落盘(log `skill_codified path=`)→新 session 同类目标一键复用;拒绝路径点丢弃→无落盘。
- [ ] 全绿 → roadmap §3 FP-5 行打勾 + STATUS §3/§4 + 日期 + **5 个 FP 全完成里程碑**。

---

## 整体 build order
```
T1 WI-4.0 compaction(地基,最先,★全回归) → {T2 WI-4.1 自动披露 + T3 WI-4.2 重挂(同批,4.2 依赖 4.0 压缩点+4.1 matcher)} → T4 WI-4.3 自创(最后,依赖 FP-1 ToolPath+4.0;后端+前端确认卡) → T5 手测门
```
**风险**：4.0 改主 loop 回归面最大→单独冲刺、保留项硬注入 goal+pending、prod off 先 shadow。4.3 前端确认卡是新接线(非纯复用 plan-confirm)。
