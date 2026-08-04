# FP-1 开工前置 — §6 goal_text 契约 + §7 重试账本 冻结

> 状态：**🔒 FROZEN（2026-06-04）** — 并行/编码前唯一权威。
> 用途：把 00-PLAN §6 / §7 里"待 spec 时定"的口子全部钉死，解决 00-PLAN §6.1 与
> [01-blueprint](../01-P0-1-execution.md) §2 之间的 schema 分歧，给 FP-1~FP-5 下游一个
> 不会漂移的字节级契约（防 `feedback_cross_layer_contract`：pytest+tsc 都过但字段 disagree）。
> 依据：实地核真代码（见下「核真证据」），非纸面推演。

---

## 0. 核真证据（本冻结的事实基础，全部已 Read 实地核对）

| 断言 | 证据（file:line） | 结论 |
|---|---|---|
| `SessionGoal` 现字段 | [goal_store.py:52-57](../../../backend/deskpet/agent/goal_store.py) | `session_id/text/set_at/max_iterations=10/iterations_used=0/done=False`，**纯内存 dict**(`:67`) |
| store API 全 sync 无 I/O | goal_store.py:69-124 | `set/get/clear/mark_done/increment_iteration`；无 `bind_persistence`/`load_persisted`/`_persist` |
| 现存代码读哪些字段 | agent_loop.py:1063-1118 | 读 `.done` / `.iterations_used` / `.max_iterations` / `.text`；写 `increment_iteration` / `mark_done` |
| `increment_iteration` 只改内存不落库 | [agent_loop.py:1091](../../../backend/agent/agent_loop.py) | **T1 待修**：重启 `iterations_used` 归零 |
| lifespan 只构造不恢复 | [main.py:1067-1081](../../../backend/main.py) | 只 `_GS()` + `register`，**无 `bind_persistence`/`load_persisted`** → R-T1 待接电，"重启仍在"当前接不通 |
| §7 attempt 计数粒度 | [auto_resume.py:148-173](../../../backend/agent/auto_resume.py) | 读 `sa.auto_resume_attempts`，按 **sid**（per-session），与 reason 无关 → **所有 reason 共享 `max_attempts=2`** |
| §7 计数存储 = in-memory | [session_activity.py:9-13,102](../../../backend/agent/session_activity.py) | 注释明确 "in-memory only (rebuilt on backend restart)" → **进程重启清零** |
| §7 计数 reset 时机 | session_activity.py:237-247 | 用户发新消息 → `reset_auto_resume_attempts(sid)=0`（新回合重置自愈预算） |
| 触发集 frozenset | auto_resume.py:96-101 | 现含 `max_iterations/permanent_tool_error/circuit_open/hallucination`，**无 verify_exhausted** |
| 出厂 flag | config.py `FeaturesConfig.goal_mode=False` | goal_mode 默认 OFF；flag-OFF → store=None → 整链 BC |

---

## 1. §6 冻结：唯一 `SessionGoal` schema（解决分歧）

### 1.1 分歧点（必须现在定，否则下游各写一版）

| 字段 | 00-PLAN §6.1 | 01-blueprint §2 | 🔒 冻结决定 |
|---|---|---|---|
| `done` vs `status` | 只有 `status` | 两者并存 | **两者并存**，`status` 为落库权威，`done` 为 `status=='done'` 的内存便利镜像（BC：agent_loop:1064 读 `.done`） |
| `criteria` | 有（标"2.3 时定"） | 无 | **schema + DDL 都加占位**（`criteria TEXT NULL`），WI-1.1 **不填不消费**，留 FP-3(2.3) 直接用，避免后续 ALTER |
| `subgoals` 类型 | `list[str]` | `list[SubGoal]` + session_subgoals 表 | **`list[str]`，WI-1.1 恒空**；current-subgoal 由 WI-1.2 `goal_tasks` 派生（单一真相源 = goal_tasks）。**砍掉 session_subgoals 表**（避免双表同步 bug） |
| `max_iterations` | 未列 | 有(=10) | **保留**（goal_checker cap 依赖，agent_loop:1067 读） |
| `set_at` / `updated_at` | 只有 `set_at` | 两者 | **两者并存**（set_at=创建，updated_at=最后改） |
| `progress` | 有 | 有 | **保留**（0.0~1.0，由 WI-1.2 goal_tasks 完成比回填；WI-1.1 恒 0.0） |
| `goal_id` | 有 | 有(默认 `""`) | **保留**，uuid4；**空串=未落库的纯内存态**（未 bind_persistence 时 BC） |

### 1.2 🔒 冻结后的 `SessionGoal`（dataclass，全新字段有默认值 = BC）

```python
@dataclass
class SessionGoal:
    # —— 现有字段（不动，保证 agent_loop / goal_checker / /goal 现读法 BC）——
    session_id: str
    text: str                       # 原始目标全文（verify 对照 / re-anchor 注入用这个）
    set_at: float
    max_iterations: int = 10        # goal_checker rebound 上限（agent_loop:1067 读）
    iterations_used: int = 0        # T1：必须落库，重启恢复（否则归零）
    done: bool = False              # 内存便利镜像；不变式 done == (status == "done")
    # —— 新增字段（全默认值；未落库的纯内存态 goal_id="" 即旧行为）——
    goal_id: str = ""               # uuid4 主键；""=未持久化的内存态
    status: str = "active"          # 落库权威生命周期：active | done | abandoned
    progress: float = 0.0           # 0.0~1.0，WI-1.2 goal_tasks 回填；WI-1.1 恒 0.0
    criteria: str | None = None     # 占位，WI-1.1 不消费，FP-3(2.3) 用
    updated_at: float = 0.0         # 最后修改时间；0.0 时取 set_at
    subgoals: list[str] = field(default_factory=list)  # WI-1.1 恒空，current-subgoal 派生自 goal_tasks
```

**不变式（实现 + 单测必须守）**：
- `done == (status == "done")`：`mark_done()` 同时设 `done=True; status="done"`；`set()` 同时设 `done=False; status="active"`。
- `goal_id == ""` ⟺ 该 goal 仅存在于内存、未落库（未 `bind_persistence` 或落库失败 safe-fail）。
- `get(sid)` 返回该 session **最新 `status=='active'`** 的一行（多目标物理支持、API 单活跃 last-write-wins）。

### 1.3 🔒 DDL（追加进 `memory_v2_schema._DDL`，runtime `CREATE TABLE IF NOT EXISTS`，不写 migration、不 bump user_version）

```sql
CREATE TABLE IF NOT EXISTS session_goals (
    goal_id         TEXT    PRIMARY KEY,                    -- uuid4
    session_id      TEXT    NOT NULL,
    text            TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'active',      -- active|done|abandoned
    progress        REAL    NOT NULL DEFAULT 0.0,
    criteria        TEXT,                                    -- 占位，FP-3 用，NULL 默认
    max_iterations  INTEGER NOT NULL DEFAULT 10,
    iterations_used INTEGER NOT NULL DEFAULT 0,
    set_at          REAL    NOT NULL,
    updated_at      REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_goals_sid
    ON session_goals(session_id, status);
```
> **不建 session_subgoals 表**（§1.1 决定）。`goal_tasks` 表见 WI-1.2（FP-2 落地）。

### 1.4 🔒 对外只读契约（下游唯一入口，经 `service_context.get("session_goal_store")`）

```python
store.get_goal_text(session_id: str) -> str | None    # sync, None-safe; flag-OFF/无目标/store=None → None
store.get(session_id: str)           -> SessionGoal | None
store.get_completed_path(session_id, goal_id) -> ToolPath | None   # WI-1.6 交付，喂 FP-5 的 4.3
```

**语义冻结**：
- **None 稳定**：flag-OFF / 无活跃目标 / store=None 一律返 None；下游全程对 None BC（不报错、走旧行为）。
- **同步零 I/O**：`get_goal_text`/`get` **永读内存**（最新权威），落库异步备份；下游热路径读不阻塞。
- **一致性窗口**：`set` 后到 `_persist` await 完成前进程崩 → 该 goal 丢（单机桌宠可接受）；`_persist` 失败 **safe-fail 不抛**（否则 `_handle_goal` 改 async 后异常冒泡到 slash 处理）。
- **下游不得碰 store 内部**，只走上面 3 个方法 + `service_context`。

### 1.5 🔒 注入预算（两条互不知情的路径，禁各写一版）— 抄定 00-PLAN §6.3 + R-T2

- **compressor 路径**（FP-1 的 1.3 re-anchor / FP-5 的 4.0 保留项 / 1.5 resume）：作 `role=system` 注 `working_messages`，`context_compressor._partition` 全量保留 → **必须加软上限 `max_system_inject_tokens≈1500`**，超限按 `goal_text > 子目标 > pending tasks` 顺位裁。
- **assembler 路径**（FP-4 的 3.2 人格 / FP-5 的 4.1 skill）：作 Slice 走 `BudgetAllocator`，`priority<80` 预算紧张时**整块丢**。
- **goal_text 唯一权威 = compressor system 段**；人格/skill Slice **不得重复携带 goal_text**（跨系统去重靠"单一注入源"）。
- **2.3 goal 对照证据 / GD 观测信号**：内存读 / metrics 打点，**不注 prompt、不占预算**。

### 1.6 🔒 统一注入文案（1.3/1.4/1.5 共用，便于 log grep `[goal]`/`[目标锚定]`）

```
[当前目标] {text}
[当前子目标] {subgoals[current]}     # 可空，无则省略
```
（re-anchor 变体加一句"请确保接下来的动作仍服务于上述目标，不要被中间步骤带偏"，见 01-blueprint §1.3。）

### 1.7 🔒 活跃 goal × 历史 fact 双写规则（1.1 × FP-4 的 3.1）

- **source of truth = 1.1 store**（活跃目标）。3.1 facts `category=goal/decision/constraint` 是**只读投影**。
- **单向事件钩**：`store.set()` 成功 → `on_goal_set` callback → facts upsert（**不从 facts 回写 store**）。
- **钩用注入 callback**（`bind_on_goal_set(callable)`，main.py 接电时闭包捕获 `_facts_store.upsert`）；**goal_store 不 import facts**（防 agent←memory import 环）。
- done/abandoned：store 改 status，facts 投影保留供跨会话召回。

---

## 2. §7 冻结：全局重试预算账本（attempt 计数语义钉死）

### 2.1 🔒 两类计数器语义辨析（关键，别混）

| 计数器 | 存储 | 粒度 | 重启 | 落库? | 含义 |
|---|---|---|---|---|---|
| `SessionGoal.iterations_used` | session_goals 表 | per-goal | **恢复**（T1 落库后） | **是**（T1 必做） | 目标进度：goal_checker rebound 了几轮 |
| `SessionActivity.auto_resume_attempts` | 内存(SessionActivity) | **per-session** | **清零** | **否**（维持现状） | 单次用户回合的自愈预算：auto_resume spawn 了几次 |

> **冻结决定**：两者语义不同，**故意区别对待**。goal 进度要跨重启活下来（T1 落库）；自愈预算是"本回合"概念，重启清零与现有 SessionActivity 设计哲学一致（"watchdog activity restarts cleanly with the process"），**不为 R-T4 把 auto_resume_attempts 落库**（那会引入新持久化面 + 与 reset-on-new-message 语义打架）。

### 2.2 🔒 attempt 粒度 = per-session 共享额度（已核真）

`AutoResumeOrchestrator.handle_failure` 按 `sid` 读 `auto_resume_attempts`（auto_resume.py:165-167），**与 reason 无关**。因此：

- 新增的 `verify_exhausted` 与现有 `max_iterations` / `circuit_open` / `permanent_tool_error` / `hallucination` **共享同一个 `max_attempts=2` 额度**。
- ⚠️**§7 上界单测必须按"共享额度"断言**（verify 重试会偷吃 max_iterations 的 attempt，不是各自独占 2 次）。

### 2.3 🔒 死循环上界账本（FP-3 的 2.2 实现，单测先行钉这张表）

```
① loop 内 verify 重规划：复用 max_verify_nudges=2（每次带"反思+goal对照+新方案"，非机械 nudge；difflib 防假重试）
   ↓ 2 次仍失败
② ephemeral_subagent 独立 verify 1 次（救援，不计入任何计数器）
   ↓ 仍失败
③ emit ErrorEvent(reason="verify_exhausted") + return 终止本 loop
   （verify_exhausted 必须 ADD 进 _AUTO_RESUME_TRIGGER_REASONS frozenset / auto_resume.py:96）
   ↓
④ auto_resume：per-session max_attempts=2（与 max_iterations/circuit_open 共享，§2.2）
   ↓ 2 次仍失败
⑤ 优雅降级：告诉用户进度 + 留可恢复 state（接 WI-1.5 resume）
———————————————————————————————————————————
工具级 circuit_breaker(threshold=3)：独立兜底，不同层，不与①~⑤叠乘
```

- **emit 点**：agent_loop verify 三层耗尽处（`:1045` continue 兜底分支外）改 `emit verify_exhausted + return`；**不在 goal_checker 块(:1059)重复 emit**。
- **不新造第四套计数器**（否则叠乘失控/死循环）。
- **死循环上界测试（FP-3 必写）**：构造永久失败任务，断言总 LLM 调用 ≤ 明确上界、最终走⑤优雅降级不卡死、`auto_resume_attempts` 按共享额度封顶。具体数字 FP-3 spec 时定，本冻结只锁"共享额度 + 有限上界 + 优雅降级"三性质。

### 2.4 🔒 respawn / 重启语义（R-T4）

- **同进程内 respawn**：loop 内 verify 计数 respawn 后清零属**预期**（不算违反上界）。
- **跨进程重启**：`auto_resume_attempts` in-memory → 清零（§2.1 决定接受）。R-T4 的 **pending-resume 队列只处理"同进程 respawn 后 redispatcher 未注册即静默 return"竞态**（main.py:2076-2083，control_ws 快照坑同构变体），**不处理跨重启绕过**（接受单机桌宠该风险）。

---

## 3. 冻结后的下游影响（给 FP-1~FP-5 的硬约束）

1. **任何人改 `SessionGoal` 字段 / DDL 列 / 3 个只读方法签名 → 必须回这里改冻结表并标新日期**，不许在 blueprint 或代码里偷偷漂。
2. FP-1 实现 = §1.2 schema + §1.3 DDL + §1.4 接口 + T1 落库 + R-T1 lifespan 接电。
3. FP-3 的 2.2 实现 = §2.3 账本（先写上界单测）；`verify_exhausted` 进 frozenset。
4. FP-4 的 3.1 = §1.7 单向钩；FP-2 的 1.2 = goal_tasks 派生 subgoals/progress。
5. 跨层 live smoke：`scripts/e2e_goal_contract.py`（§6）+ `scripts/e2e_flag_off_baseline.py`（R-T5）兜底。
