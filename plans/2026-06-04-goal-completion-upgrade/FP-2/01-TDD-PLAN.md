# FP-2「抗漂移闭环」Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** 目标在执行中不丢——任务图跨 agent 共享(WI-1.2) + 压缩前/决策点 re-anchor(WI-1.3) + 多 agent handoff 带 goal(WI-1.4) + 中断 resume 续目标(WI-1.5)。

**Architecture:** 全部建立在 FP-1 的 `SessionGoalStore.get_goal_text(sid)`（已落地、永读内存 None-safe）之上。WI-1.3/1.4/1.5 只读 `get_goal_text` 注入 prompt（下游对 None BC）；WI-1.2 新建 `goal_tasks` 表 + `TaskGraphStore`（沿用 FP-1 sidecar 惯例 + R-T5 独立 ensure 门控）。

**Tech Stack:** 同 FP-1。测试前缀：`/g/projects/deskpet/backend/.venv/Scripts/python.exe -m pytest <path> -q`（cd 被 harness 剥离，用绝对路径，见 [[feedback_bash_cwd_venv]]）。

**权威依据：** [FP-1/00-CONTRACT-FREEZE.md](../FP-1/00-CONTRACT-FREEZE.md)（§1.5 双预算路径 / §1.6 注入文案 — 已冻结）+ [01-P0-1-execution.md](../01-P0-1-execution.md) WI-1.2~1.5 + [00-PLAN §14.2](../00-PLAN.md) 冻结签名（T4 compress / B-2 build_teammate_tools）。

---

## 冻结签名（并行前锁，抄自 00-PLAN §14.2 + 本 FP 核真）

| 符号 | 冻结 | 现状证据 |
|---|---|---|
| `ContextCompressor.compress` | `async def compress(self, messages, *, goal_text: str\|None=None, pending_tasks: list[str]\|None=None)` | 现单参 `compress(self, messages)` [context_compressor.py:135](../../../backend/deskpet/agent/context_compressor.py) |
| `compact_messages` | 加 `goal_text: str\|None=None` kwarg（与 compress 对齐 T4） | 现 [history_compactor.py:141](../../../backend/agent/history_compactor.py) `compact_messages(messages,*,summarize_fn,...)` |
| `spawn_team` / `_build_charter` | 加 `parent_goal_text: str\|None=None` / `parent_goal_id: str\|None=None`；None→省略 charter goal 段(BC) | [spawn_team.py:95,148](../../../backend/deskpet/agent/team/spawn_team.py) |
| `build_teammate_tools` | **改函数体**加 `task_graph_store=None, goal_id=None` 参数 + 新 triple（非"加进去"，现 return 写死） | [teammate_tools.py:176](../../../backend/deskpet/agent/team/teammate_tools.py) |
| `goal_tasks` claim | 同进程→SessionDB `_write_lock` 串行（T5 先核 teammate 同进程）；用独立 `ensure_session_goals_table` 同款门控建表（R-T5） | — |
| 软上限 | `max_system_inject_tokens≈1500`（compressor system 段，goal_text>子目标>pending 顺位裁，R-T2） | — |
| 注入文案 | `[当前目标] {text}` / `[当前子目标] {title}`（统一，便于 log grep `[目标锚定]`/`[goal]`） | 冻结 §1.6 |

> **BC 铁律**：所有新 kwarg 默认值 = 旧行为；`goal_text=None`（flag-OFF/无目标）时 compress/compact/charter/resume **逐字节同旧版**（现有单测全绿）。

---

## Task 0: T5 前置 spike — 确认 teammate 进程模型（决定 claim 并发原语）

- [ ] **Step 1:** Read `spawn_team.py` 的 `_run_teammate` + 默认 runner，确认 teammate 是**同进程 asyncio 协程**还是子进程。
- [ ] **Step 2:** 写结论到 `FP-2/00-T5-spike.md`：同进程 → `goal_tasks` claim 用 SessionDB 既有 `_write_lock`+`_with_retry` 串行（**非** TeamStore per-team `BEGIN IMMEDIATE`）。
- [ ] **Step 3:** 若发现是跨进程 → 升级为 `BEGIN IMMEDIATE` 并标记给 WI-1.2 实现者。
> 不写代码，纯 spike，产出决定 WI-1.2 claim 实现。**WI-1.2 build order 第 1 步前必做。**

---

## Task 1: WI-1.3 re-anchor — compressor `goal_text` 参数 + 注入（🔴 头号手测门）

**Files:** `backend/deskpet/agent/context_compressor.py` · 测试 `backend/tests/test_compressor_goal_anchor.py`(新建)

依据 blueprint [01 §WI-1.3](../01-P0-1-execution.md)。`_partition` 已把所有 `role==system` 全量保留(context_compressor.py:253-254)，故注入 `role=system` 的锚定 message 永不被压掉。

- [ ] **Step 1 写失败测试**（BC + 注入 + 软上限）`test_compressor_goal_anchor.py`:
```python
# SPDX-License-Identifier: BUSL-1.1
import pytest
from deskpet.agent.context_compressor import ContextCompressor

def _msgs(n=30):
    out=[{"role":"system","content":"sys"}]
    for i in range(n): out.append({"role":"user" if i%2==0 else "assistant","content":f"m{i}"*50})
    return out

@pytest.mark.asyncio
async def test_compress_bc_when_goal_none(monkeypatch):
    # goal_text=None → 输出与不传时一致(BC)；no_llm 降级也一致
    c=ContextCompressor(llm_registry=None)
    r1=await c.compress(_msgs())
    r2=await c.compress(_msgs(), goal_text=None)
    assert [m.get("content") for m in r1.messages]==[m.get("content") for m in r2.messages]

@pytest.mark.asyncio
async def test_compress_injects_goal_anchor_as_system():
    class _LLM:
        async def chat_with_fallback(self,*a,**k):
            class R: content="SUMMARY"
            return R()
    c=ContextCompressor(llm_registry=_LLM())
    r=await c.compress(_msgs(), goal_text="整理三个会议纪要")
    anchor=[m for m in r.messages if m.get("role")=="system" and "[目标锚定]" in (m.get("content") or "")]
    assert len(anchor)==1
    assert "整理三个会议纪要" in anchor[0]["content"]

@pytest.mark.asyncio
async def test_goal_anchor_respects_soft_cap():
    class _LLM:
        async def chat_with_fallback(self,*a,**k):
            class R: content="S"
            return R()
    c=ContextCompressor(llm_registry=_LLM())
    huge="X"*20000
    r=await c.compress(_msgs(), goal_text=huge, pending_tasks=["t1","t2"])
    inj="".join(m["content"] for m in r.messages if m.get("role")=="system" and ("[目标锚定]" in (m.get("content") or "")))
    # 软上限 ~1500 token ≈ 截断；断言注入段远小于 huge
    assert len(inj) < 8000
```
- [ ] **Step 2 跑确认失败**：`...python.exe -m pytest /g/projects/deskpet/backend/tests/test_compressor_goal_anchor.py -q` → FAIL（compress 不接受 goal_text）。
- [ ] **Step 3 实现**：`compress` 签名加 `*, goal_text=None, pending_tasks=None`；在 `_partition` 后、组装 `new_messages` 时，若 `goal_text` 非空，构造一条 `{"role":"system","content": f"[目标锚定] 当前目标：{goal_text}\n请确保接下来的动作仍服务于上述目标，不要被中间步骤带偏。"}`（+可选 `[当前子目标]`/pending 摘要），按软上限 `_MAX_SYSTEM_INJECT≈1500` token 截断（顺位 goal_text > 子目标 > pending），追加到 system 段尾（在原 system_msgs 之后）。`goal_text=None` → 完全不注入（BC 路径，含 no_llm/no_middle 早返回也不注）。
- [ ] **Step 4 跑确认通过** + BC 回归：`...pytest /g/projects/deskpet/backend/tests/test_compressor_goal_anchor.py <现有 compressor 测试> -q` 全绿。
- [ ] **Step 5 commit**（子代理只实现不 commit；主线统一提交）。

---

## Task 2: WI-1.3 cont — history_compactor `goal_text` + agent_loop 决策点 anchor

**Files:** `backend/agent/history_compactor.py` · `backend/agent/agent_loop.py`（压缩调用点 + 决策点注入）· 测试 `backend/tests/test_compactor_goal_anchor.py`(新建) + agent_loop 锚定单测

- [ ] **Step 1 失败测试**：`compact_messages(msgs, summarize_fn=..., goal_text="G")` 输出在 system 段含 `[目标锚定] ... G`；`goal_text=None` → 与旧版逐字节一致。agent_loop：构造活跃 goal + 跑到压缩调用点 → 断言传入了 `get_goal_text(sid)`；每 `anchor_every`(默认5) 轮决策点 append 一条 `[目标锚定]`（`last_anchor_iter` 去重，同 iter 不重复）。
- [ ] **Step 2** 跑确认失败。
- [ ] **Step 3 实现**：(a) `compact_messages` 加 `goal_text=None` kwarg，`inject_summary` 后在 system 栈尾追加锚定（None→跳过）。(b) `agent_loop` 压缩/紧缩调用点传 `self.session_goal_store.get_goal_text(session_id)`（getattr 兜底 None）。(c) 决策点：主循环每 `iteration % anchor_every == 0` 且有活跃 goal → append 锚定 system message（与 goal_checker nudge 语义正交：anchor=别跑偏、nudge=未达成）。
- [ ] **Step 4** 通过 + 回归（现有 compactor + agent_loop 测试全绿）。
- [ ] **Step 5** commit。
> ⚠️ T4：compress 与 compact 参数名都用 `goal_text`（契约漂移防护）。

---

## Task 3: WI-1.4 handoff goal checkpoint — charter 注入父 goal + 回收过滤

**Files:** `backend/deskpet/agent/team/spawn_team.py` · `backend/main.py`（spawn_team 调用点）· 测试 `backend/tests/test_spawn_team_goal.py`(新建)

- [ ] **Step 1 失败测试**：`_build_charter(team_id=,teammate_id=,initial_pool_summary=,parent_goal_text="整理纪要")` 输出含 `## Parent Goal` 段 + 目标文本；`parent_goal_text=None` → 输出与旧版逐字节一致(BC)；回收识别 `[off-goal]` 标记归入 flagged 组。
- [ ] **Step 2** 跑确认失败。
- [ ] **Step 3 实现**：`_TEAM_CHARTER_TEMPLATE` 顶部加可选 `## Parent Goal (do not drift)` 段（用占位 `{parent_goal_block}`，None→空串）；`_build_charter` + `spawn_team` 加 `parent_goal_text=None, parent_goal_id=None`；回收 `results` 时对每个 `result` 检 `[off-goal]` 标记 → 返回结构加 `aligned`/`flagged` 分组（不删数据，只打标）。main.py spawn_team 调用点传 `store.get_goal_text(sid)` + `goal_id`。
- [ ] **Step 4** 通过 + 现有 team 测试全绿(BC)。
- [ ] **Step 5** commit。

---

## Task 4: WI-1.5 resume 接 goal（窄版）— auto_resume 注入 goal_text

**Files:** `backend/agent/auto_resume.py`（:219-225 new_msgs）+ 构造/调用点注入 getter · 测试 `backend/tests/test_auto_resume_goal.py`(新建)

- [ ] **Step 1 失败测试**：mock `goal_text_getter` 返 "G" → `handle_failure` 的 nudge 分支 `new_msgs` 含 `{"role":"system","content":含"[goal]"和"G", "_is_goal_anchor":True}`；getter 返 None → 只含 supervisor hint(BC)。
- [ ] **Step 2** 跑确认失败。
- [ ] **Step 3 实现**：`AutoResumeOrchestrator.__init__` 加 `goal_text_getter: Callable[[str],str|None]|None=None`；nudge 分支(:221)在 supervisor hint 前插 goal anchor（getter 非空且返回非 None 时）。main.py 构造点注入 `lambda sid: store.get_goal_text(sid) if store else None`。
- [ ] **Step 4** 通过 + 现有 auto_resume 测试全绿(BC)。**R-T4**（respawn pending-resume 队列）：本 FP 若 main.py:2076-2083 redispatcher 未注册即静默 return 仍存在，加 pending-resume 队列 + WS 重连 drain；否则记 FP-2/ 文档 defer 理由等确认。
- [ ] **Step 5** commit。

---

## Task 5: WI-1.2 — goal_tasks 表 + SessionDB 原子 claim（依赖 Task 0 spike）

**Files:** `backend/deskpet/memory/memory_v2_schema.py`(加 `_GOAL_TASKS_DDL` + `ensure_goal_tasks_table`，同 R-T5 独立门控) · `backend/deskpet/memory/session_db.py`(goal_tasks CRUD + claim) · 测试 `backend/tests/test_goal_tasks_db.py`(新建)

DDL（独立 ensure，**不进共享 _DDL**，守 flag-OFF 字节基线）：
```sql
CREATE TABLE IF NOT EXISTS goal_tasks (
    task_id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, session_id TEXT NOT NULL,
    title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
    depends_on TEXT NOT NULL DEFAULT '[]', claimed_by TEXT, result TEXT,
    created_at REAL NOT NULL, updated_at REAL NOT NULL );
CREATE INDEX IF NOT EXISTS idx_goal_tasks_goal ON goal_tasks(goal_id, status);
```
- [ ] **Step 1 失败测试**：create/get/list round-trip；DAG ready（B depends_on=[A]，A 未 done 时 claim 跳过 B）；两协程并发 `claim_ready` 不双占（原子，pass^k=5）；`update(done)` 回填 `session_goals.progress`；环检测拒绝；flag-OFF（不调）→ goal_tasks 表不建（R-T5 同款单测）。
- [ ] **Step 2-4** 实现 SessionDB CRUD + 按 Task0 spike 结论的原子 claim（同进程=`_write_lock` 串行选 ready 候选再 claim）+ 环检测 + progress 回填；跑通过。
- [ ] **Step 5** commit。

---

## Task 6: WI-1.2 cont — TaskGraphStore + 工具 + build_teammate_tools 接电

**Files:** `backend/deskpet/agent/task_graph.py`(新建 TaskNode + TaskGraphStore) · `backend/deskpet/tools/task_graph_tools.py`(新建 TaskCreate/Update/List/Get) · `teammate_tools.py:176`(改函数体加 task_graph triple) · `spawn_team.py`(子 agent 工具集合并) · 测试

- [ ] **Step 1 失败测试**：TaskGraphStore 包装 SessionDB；`build_teammate_tools(..., task_graph_store=X, goal_id="g1")` 返回的工具集**含 TaskList/TaskUpdate**（现写死 5 元组 → 改函数体）；子 agent 经 charter goal_id 读写同一图、对父可见。
- [ ] **Step 2-4** 实现；跑通过 + 现有 teammate/team 测试 BC 全绿。
- [ ] **Step 5** commit。
> R-T8：`goal_tasks` 落库 debounce（高频 update 合并）——加简单 debounce 或记 defer。

---

## Task 7: 🚦手测门（windows-mcp 真机）+ STATUS

> 复用 FP-1 harness（SendInput 圣杯 + DPI 物理像素 + Clipboard 中文 + Code 面板 /goal）。证据存 `plans/manual-results-2026-06-04-FP-2/screenshots/`。

- [ ] **MR-1.3 抗漂移（🔴 真模拟人）**：Code 面板 `/goal 写个Python脚本统计目录文件数` → 插 20+ 轮闲聊顶过压缩阈值触发压缩 → 继续 → 截图确认 LLM 仍回原目标产脚本（不跑偏）→ log grep `[目标锚定]` 注入。pass^k=3。
- [ ] **MR-1.2 任务图（🔴/🟢）**：多步目标触发 spawn_team → TodoPanel 进度可见(截图)；并发 claim 不双占=后端 pass^k=5（照做不砍，不计真模拟人）。
- [ ] **MR-1.4/1.5（🟠 log 验证为主）**：子 agent prompt 含 Parent Goal（log grep charter）；中断→resume 续原目标（log grep `_is_goal_anchor`）。§14.3 已标"真机=log 验证为主"，不拿 log 当模拟人交差。
- [ ] **R-T5 字节基线**：`python scripts/e2e_flag_off_baseline.py` 扩断言 goal_tasks 也不建 → 退 0。
- [ ] 全绿 → 更新 roadmap §3 FP-2 行全打勾 + STATUS §3/§4 + 日期。

---

## Self-Review（spec 覆盖）

| WI/spike | Task | E2E 等级 |
|---|---|---|
| T5 进程模型 spike | T0 | — |
| WI-1.3 re-anchor (T4/R-T2) | T1+T2 | 🔴 真机 |
| WI-1.4 handoff | T3 | 🟠 log |
| WI-1.5 resume (R-T4) | T4 | 🟠 log |
| WI-1.2 task graph (B-2/R-T8) | T5+T6 | 🔴 真机进度 + 🟢 并发 |
| R-T5 字节基线扩 goal_tasks | T7 | 脚本 |

**build order**：T0 spike → {T1,T2,T3,T4 可并行(独立文件，只读 get_goal_text)} → T5→T6(task graph 串行，同表) → T7 手测门。
