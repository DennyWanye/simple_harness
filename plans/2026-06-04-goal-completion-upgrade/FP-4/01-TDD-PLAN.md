# FP-4「记忆 + 人格」Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps `- [ ]`.

**Goal:** 跨会话记住目标/决策/约束(WI-3.1) + 人格画像主动注入 Component(WI-3.2) + Pin 钉住 & 衰减(WI-3.3，含修 `daily_decay` 从未被调用 bug) + 写入分级 light 快路(WI-3.4)。**红线：人格只作用交互风格，完成判定不受人格影响**（与 FP-3 WI-2.3 协同）。**MemEval 不回归**（7 路检索 Recall 不掉）。

**Architecture:** 复用已做好的 facts 半衰期核心(`_CATEGORY_DECAY`/`daily_decay`)、category-agnostic 召回（新 category 自动进召回，**零改检索**）、assembler fanout。新增=往字典加项 + 加列 + 接调度 + 1 个 component。

**Tech Stack:** 同前。测试绝对路径 venv python。

**权威依据：** [03-P0-3-memory-persona-execution.md](../03-P0-3-memory-persona-execution.md)（逐 WI + 现状行号）+ [FP-1/00-CONTRACT-FREEZE.md §1.7](../FP-1/00-CONTRACT-FREEZE.md)（单向钩 bind_on_goal_set，goal_store 不 import facts，防环）+ 文末协同契约。

---

## 关键约束
- **MemEval 不回归**：新 category 只增不改既有召回路（category-agnostic）。每个 WI 合并前跑 `test_deskpet_retriever.py` + enhanced_retriever 测试断言 Recall 不降。
- **字节契约**：所有新 flag 默认 False（goal_facts/persona_inject/pref_decay/light_write）；flag off → 行为字节同旧。schema 新列走 `schema_v2_migrator` ALTER（老库）+ `_DDL`（新库），`alter_failures` 守护。
- **红线**：3.2 画像块禁写"讨好/最大化粘性/延长在线"；verify 完成判定**不读** preference_profile（与 FP-3 2.3 隔离，单测断言）。

---

## Task 1: 阶段一 schema 地基 — facts 加 scope + pinned 列（一次加齐避免多次 ALTER）
**Files:** `memory_v2_schema.py`(_DDL facts 加 scope+pinned) · `schema_v2_migrator.py`(_COLUMN_ADDS["facts"] 加 scope/pinned) · 测试
依据 [03 §3.1.5 + §3.3.2](../03-P0-3-memory-persona-execution.md)。
- [ ] **Step 1 失败测试**：新库 _DDL facts 含 `scope`(TEXT DEFAULT 'user') + `pinned`(INTEGER DEFAULT 0)；老库（无这两列）经 ensure_memory_v2_columns ALTER 后补齐；ALTER 失败 → `alter_failures()` 记录、不崩。
- [ ] **Step 2-4** 实现 + 跑通 + schema 测试全绿（老库 ALTER + 新库 DDL）。
- [ ] **Step 5** commit。

## Task 2: WI-3.1 goal/decision/constraint 记忆（facts category + scope + prompt 双版本）
**Files:** `facts.py`(_CATEGORY_DECAY 加3类 + _EXTRACT_PROMPT 双版本 + ExtractedFact.scope + upsert scope) · `config.py`(memory.v2.goal_facts flag) · 测试 `test_goal_decision_facts.py`(新建)
依据 [03 §WI-3.1](../03-P0-3-memory-persona-execution.md)。
- [ ] **Step 1 失败测试**：_CATEGORY_DECAY 含 goal=0.005/decision=0.002/constraint=0.001（VALID_CATEGORIES 自动纳入）；flag on prompt 新版抽出三类（mock LLM 返三类 JSON→upsert 三条）；**flag off prompt 用旧版(4类,字节回归)**；scope 列写入 + list_active 按 scope；正向豁免：持续性目标/已做决策/长期约束 MUST 抽，一次性事件(明天3点开会)仍排除。
- [ ] **Step 2-4** 实现（_EXTRACT_PROMPT 拆双常量/参数化 categories 行；ExtractedFact.scope 默认 'user'；upsert scope 参数）+ 跑通。`_fact_row_to_hit` 按 category 前缀 `[goal]/[decision]/[constraint]`（可选 1 行，建议做）。
- [ ] **Step 5** commit。
> **不改 enhanced_retriever/retriever**（召回 category-agnostic 自动吃新类）。

## Task 3: WI-3.3 Pin + daily_decay（含修「daily_decay 生产从未调用」bug）
**Files:** `facts.py`(set_pinned + daily_decay SQL 加 `AND pinned=0`) · `main.py`(lifespan 接通 `await _facts_store.daily_decay()` 调度 ★bug 修) · `preference_memory.py`(match 乘 recency decay + pin 项 decay=1.0) · `p4_ipc.py`(memory_pin/unpin verb + facts_list 透传 pinned) · `config.py`(pref_decay flag) · 测试 `test_pin_and_pref_decay.py`(新建)
依据 [03 §WI-3.3](../03-P0-3-memory-persona-execution.md)。
- [ ] **Step 1 失败测试**：set_pinned(id,True)→list pinned=1；daily_decay 跳过 pinned(pin 项 confidence 不变)；pinned 列 ALTER 失败→回退旧 SQL 不崩；**★daily_decay 真被调度（lifespan 调用点存在）**；PreferenceMemory match 加 recency decay（老条目 effective 降、pin 不降；阈值比 cosine 保命中、排序用 effective）；旧 JSON(无 pinned)加载不报错(BC)。
- [ ] **Step 2-4** 实现 + 跑通。**★ main.py lifespan facts_store 构造后加 `await _facts_store.daily_decay()`（try/except 守护，对齐 retriever 启动跑一次）**。
- [ ] **Step 5** commit。

## Task 4: WI-3.4 写入分级 light 快路
**Files:** `session_db.py`(append_message 加 skip_embed: bool=False, hook 条件) · `config.py`(light_write flag) · 调用方(语音 tick/截屏高频流入口传 skip_embed=True，需 grep 定位) · 测试 `test_light_write_path.py`(新建)
依据 [03 §WI-3.4](../03-P0-3-memory-persona-execution.md)。
- [ ] **Step 1 失败测试**：append_message(skip_embed=True)→`_on_message_written` hook 不触发(mock call_count=0)、消息仍进 messages+FTS；skip_embed=False(默认)→hook 照常(字节回归)；flag off→skip_embed 强制 False。
- [ ] **Step 2-4** 实现（方案 A：skip_embed=True 时不触发 hook，消息照常入 L2/FTS 只不进 L3 向量）+ 定位高频流入口传 True（grep 语音 tick/截屏写 session_db 点；定位不到则记 defer 等确认，不误伤正常对话）。
- [ ] **Step 5** commit。
> BC 铁律：skip_embed 默认 False → 9+ 处现有 append_message 调用点字节不变。

## Task 5: WI-3.2 PreferenceProfileComponent + 双写钩(B-10)
**Files:** `assembler/components/preference_profile.py`(新建) · `assembler/__init__.py`(注册) · `config.py`(persona_inject flag) · `main.py`(传 facts_store + bind_on_goal_set 钩) · `goal_store.py`(bind_on_goal_set callback,**不 import facts**防环) · 测试 `test_preference_profile_component.py`(新建)
依据 [03 §WI-3.2 + §3.1.4 双写契约 + FP-1 freeze §1.7]。
- [ ] **Step 1 失败测试**：facts 有 preference/profile→Slice 含画像块 priority=85 bucket=**dynamic**(改偏好下轮反映)；facts_store=None→空 Slice 不抛；Pin 项置顶带 📌；flag off→component 不进 prefer/空 Slice(字节回归)；**TG-5 渲染不含粘性/讨好措辞(黑名单断言)**；**双写钩**：goal_store.set→bind_on_goal_set callback→facts category=goal upsert(key=goal_<sid>,scope=session)，goal_store 不 import facts(注入 callback,防 agent←memory import 环)；去重(同 session 重设→replace 不堆积)。
- [ ] **Step 2-4** 实现：component `provide(ctx)→Slice` 读 `list_active(category=preference/profile/constraint)` top-N + Pin 置顶 + 类 PROFILE.md 渲染；`bind_on_goal_set(callable)` main.py 接电时闭包捕获 facts upsert；flag 守护。
- [ ] **Step 5** commit。

## Task 6: 🚦手测门 + MemEval 回归 + STATUS
> 复用 harness。证据存 `plans/manual-results-2026-06-*-FP-4/`。
- [ ] **MR-3.1 跨会话召回（🔴 真模拟人）**：会话A"我这项目决定用 TypeScript，预算2000以内"→重启/新session→会话B"我之前定的技术栈?"→截图 LLM 答含 TypeScript + log `[decision]` 进 L3 块。
- [ ] **MR-3.2 改偏好下轮反映（🟠 真机+prompt log）**："我喜欢乌龙茶"→下轮"推荐饮料"→LLM 提乌龙茶 + prompt log 画像块含该条；改"改喝咖啡"→下轮反映咖啡(cross-key replace)。
- [ ] **MR-3.3 Pin 不衰减（🟢后端为主）**：set 偏好→Pin→注入 daily_decay 多次→pin 项 confidence 满、非 pin 降。
- [ ] **★ MemEval 不回归**：跑 `test_deskpet_retriever.py` + enhanced_retriever + `test_deskpet_context_assembler.py`，断言 Recall 不降、bundle 结构不破。
- [ ] **契约校验**：`scripts/e2e_goal_memory.py` live smoke：set goal→查 P0-1 store + facts 两处一致(防双源 disagree)。
- [ ] 全绿 → roadmap §3 FP-4 行打勾 + STATUS §3/§4 + 日期。

---

## 整体 build order
```
T1 schema(scope+pinned 一次加齐) → {T2 WI-3.1(facts category/prompt) + T3 WI-3.3(pin/decay/调度) 都改 facts.py→串行; T4 WI-3.4(session_db) 可并行}
→ T5 WI-3.2(component + 双写钩,依赖 T1 pinned 列 + T2 category + FP-1 store) → T6 手测门+MemEval 回归
```
**并行性**：T2 与 T3 都改 facts.py → **串行**（同文件）；T4(session_db/vector_worker) 与 T2/T3 独立 → 可并行。T5 依赖 T1/T2/T3。
