# 执行总纲 — 一个大功能点一个跑（superpowers + 手测门）

> 用途：把 v4 plan 切成 **5 个可独立手测的大功能点(Feature Point, FP)**，按依赖顺序串行执行。
> 规则：**一个 FP → superpowers 做细化计划 → TDD 实现 → `/sp:verify` → 手工测试门(windows-mcp/CDP 真机) → 过了才进下一个 FP**。
> 每个 FP 完成必须更新 `STATUS/status.md`（项目 HARD 纪律）。
> 主依据：[00-PLAN.md](./00-PLAN.md) v4 + blueprint `01~04` + 硬化 §14/§15。
> 最后更新：2026-06-04 ｜ 状态：待开工。

---

## 0. 每个 FP 的标准执行循环（superpowers）

```
① /sp:plan   —— 用 sp-writing-plans 把「本 FP 的 WI 切片 + blueprint 细节 + 关联 spike」
              做成一份 TDD 可执行计划（tasks.md 式，测试先行排序）。产出存本 FP 子目录。
② /sp:tdd    —— red-green-refactor 实现，每个任务先写测试再写实现，80%+ 覆盖。
③ /sp:verify —— sp-verification-before-completion：跑命令、确认输出，证据先于断言。
④ 🚦手工测试门 —— windows-mcp/CDP 真机 E2E（见各 FP「手测门」），截图存盘 + 抓日志。
              ❗真 E2E ≠ 脚本回放：复用 testcase/_cdp.py + _send.py SendInput 圣杯。
⑤ STATUS 更新 —— 改 §3 模块完成度 / §4 里程碑；本 FP 才算「完成」。
⑥ → 下一个 FP
```

> **开工前一次性前置（只做一次，约半天）**：冻结 §6 goal_text 契约 + §7 重试账本计数语义（读 `auto_resume.py:148-180` 定 attempt 粒度）。这是 readiness-gate 标的仅有的 3 个真阻塞里的 2 个，第 3 个（R-T1 lifespan 接电）在 FP-1 内做。

---

## 1. 五个大功能点（依赖顺序，串行）

### 🟦 FP-1　目标持久化地基（第一块多米诺）
> 把 `goal_store` 从内存态变成「持久化 + 可恢复」的唯一权威，并接通 lifespan。后面所有 FP 都读它。

- **含 WI / spike**：§6 契约冻结 · §7 账本 · **WI-1.1** Durable Goal Store · **R-T1** lifespan 接电(load_persisted) · **T1** increment_iteration 落库 · **WI-1.6** 工具路径录制(喂后续 FP-5) · **R-T5** flag-OFF 基线脚本(首次建立) · **R-T7** 多 worktree 独立 USER_DATA_DIR
- **blueprint**：[01-P0-1-execution.md](./01-P0-1-execution.md)（WI-1.1/1.6 段）
- 🚦**手测门**：① `/goal` 设目标 → `taskkill /F /IM deskpet.exe` → 重启 Tauri → `/goal` 查**仍在** ② 多轮后重启 `iterations_used` 恢复 ③ flag-OFF 时 goal 表**不建**（字节基线）
- **风险**：低-中（契约源头，schema 定错全线返工）→ 所以 §6 先冻结。

### 🟦 FP-2　抗漂移闭环
> 目标在执行中不丢：任务图 + 压缩前 re-anchor + 多 agent 带着目标 + 中断续目标。

- **含 WI / spike**：**WI-1.2** Task 任务图跨 agent 共享(+**T5** 进程模型 spike·**B-2** 改 build_teammate_tools·**R-T8** goal_tasks 落库 debounce) · **WI-1.3** re-anchor(+**T4** compress 签名冻结·**R-T2** 双预算路径+软上限1500) · **WI-1.4** handoff goal checkpoint · **WI-1.5** resume 接 goal(+**R-T4** respawn pending-resume 队列)
- **blueprint**：[01-P0-1-execution.md](./01-P0-1-execution.md)（WI-1.2/1.3/1.4/1.5 段）
- 🚦**手测门**：① 长对话顶过压缩阈值 → 追问「我最初让你做什么」→ 仍回原目标(截图) ② 多步目标 → TodoPanel 任务图进度可见 ③ 中断长任务 → resume 续**原目标** ④ 并发 claim 不双占（后端 pass^k，非真机）
- **风险**：中（改 compress 两处、并发 claim）。

### 🟦 FP-3　自我纠错闭环
> verify 不过不再「停」，而是结构化反思→真重规划→自动重试；verify 接原目标对照。

- **含 WI / spike**：**WI-2.1** 结构化反思字段 · **WI-2.2** 真重规划重试(+**T6** ephemeral mock 改 AsyncMock·§7 账本·**R-T3** LLM 失败降级矩阵) · **WI-2.3** verify 接 goal_text 对照 · **WI-2.4** 外部 evaluator(高后果触发) · **R-T6** shadow 可观测埋点
- **blueprint**：[02-P0-2-self-correction-execution.md](./02-P0-2-self-correction-execution.md)
- 🚦**手测门**：① 伪完成「帮我生成 PPT」→ verify 拦 → 自动重规划 → 二次真生成 .pptx → ArtifactCard 渲染(截图链) ② 真完成放行不误杀 ③ relay 故障注入(超时/畸形 JSON)走对降级分支（后端） ④ `no_persona_leak`（后端 pass^k）
- **出厂决策**：本 FP 后 verify_gate prod **off→shadow**（开始采集 R-T6 指标，go/no-go 见 §15.5）。
- **风险**：高（触护城河 verify gate + 三套重试上限合并）。

### 🟦 FP-4　记忆 + 人格（伴侣感差异化）
> 跨会话记住目标/决策/约束；人格画像主动注入 + Pin 钉住；写入分级。

- **含 WI / spike**：**WI-3.1** goal/decision/constraint 记忆(+**B-10** 注入 callback 防 import 环) · **WI-3.2** 人格画像 Component(+**T3** BudgetAllocator 裁剪粒度 spike·**R-T2** assembler 路径) · **WI-3.3** Pin+PreferenceMemory 衰减(+🐛修 `daily_decay` 从未被调用) · **WI-3.4** 写入分级 light 快路 · (**WI-3.5** 周期记忆 nudge，可选)
- **blueprint**：[03-P0-3-memory-persona-execution.md](./03-P0-3-memory-persona-execution.md)
- 🚦**手测门**：① 会话 A 说一个决策 → 重启/新 session → 会话 B 答得出 ② 改偏好 → 下轮 LLM 反映(截图+prompt log) ③ Pin 的偏好不随时间衰减(后端，模拟时间推进) ④ MemEval 不回归(7 路检索 Recall 不掉)
- **红线**：人格只作用交互风格，**完成判定不得被人格影响**（与 FP-3 的 2.3 协同）。
- **风险**：中（别破坏已有 7 路检索）。可与 FP-2/FP-3 并行（独立子系统），但**串行模式下排此处**。

### 🟦 FP-5　Skills 分级披露 + 自创闭环（最高回归风险，单独冲刺）
> 省 context 的三级披露 + 越用越会办事的技能自创（需用户确认）。

- **含 WI / spike**：**WI-4.0** 接通 compaction(前置·全回归长会话·+**R-T2** 协同) · **WI-4.1** 二级 embedding 披露(+**T2** encode 异步·**R-T8** 缓存) · **WI-4.2** 压缩后 skill 重挂 · **WI-4.3a-d** 技能自创+确认门(+**T7** skill_candidate WS verb+前端确认卡，依赖 FP-1 的 1.6 ToolPath)
- **blueprint**：[04-P1-4-skills-execution.md](./04-P1-4-skills-execution.md)
- 🚦**手测门**：① 说触发某 skill 的话 → 该 skill 正文自动载(log grep) ② 长对话触发压缩 → 追问原目标答对 ③ 多步任务 → 候选确认卡弹出 → **真坐标点击保存** → SKILL.md 落盘 → 新 session 一键复用(真机全链)
- **风险**：高（4.0 改主 loop 影响所有长会话）→ **单独冲刺，先全回归再做 4.1+**。

---

## 2. 横切（每个 FP 都要带，不单列 FP）

- **R-T5 flag-OFF 字节基线**：每个 FP 合并前跑 `scripts/e2e_flag_off_baseline.py`，断言新表不建、字节快照不变。
- **R-T7 多 worktree 隔离**：每个 FP 用独立 `DESKPET_USER_DATA_DIR` + 端口（见 CLAUDE.md 拓扑），防迁移串味。
- **诚实 E2E 分级**：手测门里标「✅真模拟人 / 🟡log验证为主 / 后端单测」（见 §14.3），严禁拿 log 当模拟人交差。
- **STATUS 更新**：每个 FP 过手测门即更新 `STATUS/status.md`。

---

## 3. 进度追踪（一个一个跑，过一个勾一个）

| FP | 名称 | superpowers 计划 | 实现 | /sp:verify | 🚦手测门 | STATUS 更新 | 状态 |
|---|---|---|---|---|---|---|---|
| 前置 | §6 契约 + §7 账本冻结 | — | — | — | — | — | ✅ 已冻结 2026-06-04（[FP-1/00-CONTRACT-FREEZE.md](./FP-1/00-CONTRACT-FREEZE.md)） |
| FP-1 | 目标持久化地基 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ **完成**（真机手测门 PASS；FP-1/02-manual-test.md + manual-results-2026-06-04-FP-1/） |
| FP-2 | 抗漂移闭环 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ **真机手测门 PASS**（[FP-2/02-manual-test.md](./FP-2/02-manual-test.md)）：真 UI 设 /goal→真 UI 多步 grep 任务→agent 13轮 ReAct→**log `wi13_goal_anchor_injected iter=5`** 决策点 re-anchor 真机触发+3截图；真机抓修 context_compressor 白名单 bug。55焦点+280回归绿。R-T4 defer |
| FP-3 | 自我纠错闭环 | ✅ | ✅ | ✅ | 🟡手测中 | ✅ | ✅ 实现(WI-2.1/2.2/2.3/2.4+T6+R-T3+R-T6)+286焦点/2459全suite绿;**2026-06-06 复评100%**+补 R-T3 接线(ExternalEvaluator 生产启用 conservative_on_error,之前默认False该降级分支生产不触发);真机手测门待跑(testcase TC-3.1~3.7);off→shadow 待 R-T6 签核 |
| FP-4 | 记忆 + 人格 | ✅ | ✅ | ✅ | 🟡手测中 | ✅ | ✅ 实现(WI-3.1/3.2/3.3/3.4+B-10双写钩+★修daily_decay bug)+MemEval 491无回归;**2026-06-06 复评100%**+补 B-10 fire-and-forget GC 修复(_fanout_tasks强引用)+补 scripts/e2e_goal_memory.py 跨层契约smoke(3场景exit0);**2026-06-06 真机 TC-4.5 B-10双写钩 PASS**(SendInput圣杯键鼠+真/goal set→facts category=goal行+session_goals行,验证GC修复;manual-results-2026-06-06-FP345/);余 TC-4.1/4.x待续;flag默认off=BC |
| FP-5 | Skills 分级 + 自创 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ **TC-5.3招牌全链真机PASS(2026-06-06)**：多步任务→技能自创卡渲染→**真坐标点保存→SKILL.md落盘**(meeting-minutes-to-ppt,requires_script:false声明式)。本会话抓修5类生产bug:config漏[skills.codify](功能死)/5处接线断裂/WI-1.6喂数据/relay ReadError鲁棒性(3处:adapter分类+registry重试+流式重试,真机救活agent)/前端ephemeral-card(卡reload丢失);+方案B(codify FinalEvent+ErrorEvent双触发);复评100%+156测试绿+flag-OFF退0。证据manual-results-2026-06-06-FP345/RESULTS.md |

> 串行铁律：**上一个 FP 的🚦手测门没过，不开下一个 FP。** 每个 FP 的 superpowers 细化计划建议存到 `plans/2026-06-04-goal-completion-upgrade/FP-N/`。

---

## 4. 自己跑 / 跨 session 接手（起手说明）

**每次坐下来跑一个 FP，对 Claude 说：**
> 「读 `plans/2026-06-04-goal-completion-upgrade/10-EXECUTION-ROADMAP.md` 的进度表，
> 接着跑下一个未完成的 FP：先 `/sp:plan` 把它的 WI 切片 + 对应 blueprint 做成 TDD 计划，
> 存到 `FP-N/`，再 `/sp:tdd` 实现 → `/sp:verify` → 我来过手测门。」

**铁律**：
- 上一个 FP 的🚦手测门没过 → **不开下一个**（进度表那一行不打勾就不往下）。
- 每个 FP 的 superpowers 细化计划存到本目录 `FP-N/`，手测证据存到 `plans/manual-results-<date>-FP-N/`。
- 每过一个 FP 手测门 → 立刻更新 `STATUS/status.md`（否则视为未完成）。

**第一步（开工当天）**：
1. **冻结 §6 + §7**（半天）：把 §6 的 `SessionGoal` schema 定稿、读 `auto_resume.py:148-180` 定 §7 attempt 计数粒度，写进 00-PLAN。
2. **FP-1 跑 `/sp:plan`**：WI-1.1 + R-T1 + 1.6 切片 + [01 blueprint](./01-P0-1-execution.md) → TDD 执行计划。
3. `/sp:tdd` → `/sp:verify` → 🚦FP-1 手测门（设目标→重启→仍在）→ STATUS。
