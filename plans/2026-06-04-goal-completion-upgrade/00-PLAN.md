# DeskPet 升级 Plan（v2 完整版）— 让桌宠真把用户目标办成（goal-completion）

> 状态：**v4 — 9 必修 + Round1 静态硬化(§14) + Round2 运行时硬化(§15)，可拆阶段开发**（用户后续自行分阶段）
> 日期：2026-06-04 ｜ 作者：调研 + 现状自查 + 5 子代理 blueprint + Round1 静态挑战 + Round2 运行时挑战 综合
> 本文件是**主 plan / 索引**。逐 WI 实现细节见 4 份 blueprint，评审见 05，静态挑战见 06，运行时挑战见 07。
> **§14（Round1 静态前置）+ §15（Round2 运行时前置）= 并行开工前必读硬化**，含 15 个前置 spike(T1-T7 + R-T1~R-T8) + 签名冻结 + 多注入源预算总账 + LLM 失败降级矩阵 + shadow 可观测 + pass^k 真机自动化 + 诚实 E2E 分级。

## 文件地图（本 plan 套件）
| 文件 | 内容 |
|---|---|
| **00-PLAN.md**（本文） | 主 plan：主线 / 全 WI / **goal_text 契约** / **重试预算账本** / 红线落点 / defer 清单 / 排期 |
| [01-P0-1-execution.md](./01-P0-1-execution.md) | P0-1 目标持久化 逐 WI blueprint（含 schema/接线/build order） |
| [02-P0-2-self-correction-execution.md](./02-P0-2-self-correction-execution.md) | P0-2 自我纠错 逐 WI blueprint |
| [03-P0-3-memory-persona-execution.md](./03-P0-3-memory-persona-execution.md) | P0-3 记忆+人格 逐 WI blueprint |
| [04-P1-4-skills-execution.md](./04-P1-4-skills-execution.md) | P1-4 Skills 逐 WI blueprint |
| [05-evaluation-report.md](./05-evaluation-report.md) | 完整性+可执行性+风险 评审报告（9 必修项来源） |
| [06-challenge-round1.md](./06-challenge-round1.md) | Round1 静态实现挑战（逐符号核真代码，§14 硬化来源） |
| [07-challenge-round2.md](./07-challenge-round2.md) | Round2 运行时/集成/上线挑战（启动时序/预算/降级/shadow/pass^k，§15 硬化来源） |
| 依据 | [research/index.md](../../research/index.md) · [comparison-gap-analysis](../../research/comparison-gap-analysis/README.md) · [self-audit/SUMMARY](../../research/deskpet-self-audit/SUMMARY.md) |

---

## 0. 主线（一句话）

不是「加 4 个功能」，而是**把 DeskPet 那条断掉的「目标线」打通成闭环**：

```
用户说目标 →【持久化】→ 执行中【re-anchoring 防漂移】→ 多agent【handoff 带 goal】
   → verify【接 goal_text 对照】→ 不过则【结构化 replan 重试】→ 完成后【沉淀进记忆】
   → 下次【人格画像主动注入 + Pin】懂你；多步路径【固化成技能】
```

自查证明断点几乎全在「目标」线上。**第一块多米诺 = `goal_store.py` 纯内存态**，
它同时卡住目标持久、verify 接 goal、goal/decision 记忆三件事。**先推倒它，连锁受益。**

---

## 0.1 已锁定决策（2026-06-04 两轮 review）

1. 执行顺序：**WI-1.1 Durable Goal Store 第一块多米诺先行**。
2. **2.4 外部 evaluator 纳入本轮**（仅高后果目标触发，控本地成本）。
3. **1.2 Task 任务图做到跨子 agent 共享状态**。
4. **4.3 技能自创需用户确认一次才生效**。
5. **P0-2 落地后 verify_gate prod 出厂 off → shadow**。
6. 优先级：**人格半衰期(P0-3)提到 P0**；**主动性(原 P2-5)本轮不做**。

## 0.2 v2 相对 v1.1 的修订（整合评审 9 必修项）

- 🔴 新增 **§6 goal_text 跨 Phase 契约**（schema + 注入格式 + 双写规则 + live smoke）——并行前必锁。
- 🔴 新增 **§7 全局重试预算账本**——解决 circuit/auto_resume/verify_nudges/新重试 叠乘失控。
- 🔴 新增 **WI-1.6 工具路径录制**（补 4.3 的隐藏依赖）；4.3 拆 4 子任务、路径录制不再悬空。
- 🟠 **§9 红线落点**补全：红线 2（反谄媚完成）落进 2.3/2.4、红线 4（不优化在线时长）落埋点纪律、红线 1 的 **3.2×2.3 交叉约束**点明。
- 🟠 新增 **§10 显式 defer / 不做清单**（G1-G10，消除 6 处 silent drop）。
- 🟡 **1.2** 明确落在哪套 todo + TeamStore 生命周期取舍；**1.5** 明确为窄版（任务级 checkpoint defer）。
- 🟡 **§11** 逐 WI 标 E2E 等级 + pass^k 参数；补隐藏依赖边 2.4←2.3、4.0↔1.3。
- 🐛 **3.3** 补修隐藏 bug：`FactsStore.daily_decay()` 生产从未被调用（lifespan 接通调度）。

---

## 1. 范围与护城河红线

**做（本轮）**：P0-1 目标持久化抗漂移、P0-2 自我纠错闭环、P0-3 记忆+人格、P1-4 Skills 分级+自创。
**不做（本轮，§10 有完整 defer 清单）**：真主动性、多通道远程 IM、五路混合检索（**已做好**）、可编辑 .md vault、TokenJuice、`/verify` bundled skill、任务级 checkpoint 等。

**护城河（任何改动不得削弱）**：
1. goal-completion 产物校验闭环（artifact/receipt/verify-gate）——对标里最硬差异化。
2. 全本地语音 + Live2D；windows-mcp/SendInput Computer Use。
3. **「不糊弄自己 / 真 E2E ≠ 脚本回放」+ 反谄媚**纪律（§9 红线，要落到 WI 不止文字）。
4. **字节级契约 + flag 渐进点亮**：新能力出厂默认值显式决策，flag-OFF 用户 DB 字节不变、dev 先开。

---

## 2. Phase P0-1 — 目标持久化与抗漂移（地基，最高杠杆）

> blueprint：[01-P0-1-execution.md](./01-P0-1-execution.md)。沿用 DeskPet sidecar 惯例
> （`CREATE TABLE IF NOT EXISTS` + `bind_persistence` + 薄 SessionDB 方法，**不写 migration、不 bump user_version**）。

| WI | 标题 | 现状 | 要做什么（详见 01） | E2E 等级 |
|---|---|---|---|---|
| **1.1** ★ | **Durable Goal Store** | ❌ 纯内存(`goal_store.py:10-11`) | `SessionGoalStore` 落 SessionDB；**产出 §6 契约的唯一权威**；冻结接口后下游并行。⚠️Round2 关键：**现 lifespan `main.py:1067-1080` 只 `_GS()` 构造、从不 `load_persisted` → 「重启仍在」当前接不通**，必须接电进 lifespan(R-T1 写死顺序) | 🔴 真机(设目标→重启→仍在) |
| **1.2** ★ | **Task 任务图（跨 agent 共享）** | 🟡 三套 todo 无依赖 | **新建 `goal_tasks` 表落共享 state.db**，按 `goal_id` 关联；⚠️Round1 修正：claim 调度**复用 SessionDB 既有 `_write_lock`+`_with_retry` 串行原语**（非 TeamStore per-team `BEGIN IMMEDIATE`——两者并发模型不兼容，见 §14-T5 前置 spike 先核 teammate 同进程/跨进程）；`TaskList/TaskUpdate` 需**改 `build_teammate_tools`(teammate_tools.py:176) 函数体新增 triple**（现 return 写死 5 元组，无追加入口），与现有 `team_task_*` 工具并存 | 🔴真机进度+🟢后端单测(并发claim) |
| **1.3** ★ | **re-anchoring 抗漂移** | ❌ 压缩不读 goal | `ContextCompressor`+`history_compactor` **两处**压缩前 + 决策点重读 goal_text 注入 system 栈尾（活过压缩）；`GD_actions/GD_inaction` 仅作**观测信号**打点（非硬门，见 §11） | 🔴 真机+pass^k(长对话不漂移) |
| **1.4** ★ | **handoff goal checkpoint** | ❌ Charter 无 goal(`spawn_team.py:66-92`) | Team Charter 注入父 goal_text（§6 统一格式）；子输出回收按目标过滤（判据 spec 时定：LLM judge 优先） | 🟠 真机(子 prompt 含目标) |
| **1.5** | **resume 接 goal（窄版）** | 🟡 基建有未接(`auto_resume.py:220`) | resume respawn 时注入 goal_text。**明确窄版**：只接 goal，**任务级 verified-state checkpoint 显式 defer→G6** | 🟠 真机(中断→续原目标) |
| **1.6** ✚新增 | **工具路径录制（喂 4.3）** | ❌ 无 | 录制目标完成时的 `ToolPath`{工具序 + ok 判定 + corrected/recovered 标记 + goal_text}，产出 `get_completed_path(sid, goal_id)`；**这是 4.3 技能自创的依赖源**（评审隐藏依赖1） | 🟢 后端单测 |

**P0-1 对外契约**：见 **§6**。**build order**：1.1（含 §6 契约冻结）→ {1.2, 1.6} 并行 →（下游就绪后）1.3/1.4/1.5。

---

## 3. Phase P0-2 — 自我纠错闭环（从「停」到「自动重规划重试」）

> blueprint：[02-P0-2-self-correction-execution.md](./02-P0-2-self-correction-execution.md)。新建 `reflection.py`；
> 复用 goal_checker 三级 JSON 容错解析（不强依赖中转站 `response_format`）；flag `structured_reflection` 默认 off。

| WI | 标题 | 现状 | 要做什么（详见 02） | E2E 等级 |
|---|---|---|---|---|
| **2.1** ★ | **结构化反思字段** | ❌ 自由文本三档 | `StructuredReflection{error_analysis, execution_critique, task_replanning, next_action, confidence}`；**只在「verify 失败 rebound」「selfcheck tier2/3」两个本就回灌点追加 schema 指令**（零正常路径开销） | 🟢 后端单测(断言字段存在) |
| **2.2** ★ | **verify 失败→真重规划重试** | 🟡 最多 2 次文本 nudge、stub | 把现有机械 nudge 升级成「反思+goal对照+新方案」真重试；⚠️Round1 修正：**只把 `consult_ephemeral_subagent`(verify_gate.py:394) 改 async**，`check`(:284) 保持 sync；callable 类型→`Awaitable[bool]`、agent_loop:992 加 await；**build order 第 1 步先把现有 ephemeral mock 改 AsyncMock**(§14-T6，否则 `await bool` 报错)；`difflib` 比对 task_replanning 防假重试；**重试预算遵守 §7 账本**(不新造计数器) | 🔴 真机+pass^k(校验不过→改方案→二次过) |
| **2.3** ★ | **verify 接 goal_text 对照** | 🟡 两 if 分支独立(`:955` vs `:1059`) | 串联 [A]VerifyGate(claim 对账)→[B]outcome_verifier(文件/sha/diff 客观证据)→[C]GoalChecker(原目标对照，**只喂客观证据不喂裸文本**)；完成判定=三者全绿；**人格层禁入**（§9 红线1，见 2.3 四重保证） | 🔴 真机+pass^k(伪完成被拦) |
| **2.4** ★ | **外部/多 persona 交叉验证** | ❌ 单 LLM 自评 | **仅高后果目标**（改钱/改文件/不可逆，判据见 02）触发独立 evaluator/多 agent 交叉批判；普通目标不走（成本护栏）；**反谄媚校验项落此**（§9 红线2） | 🟠 真机(关键目标≥2 视角) |

**依赖**：2.1→2.2；2.3←1.1(goal_text)；**2.4←2.3**（评审隐藏依赖2，§8 已补边）。P0-1 未就绪时 2.x 以 `goal_text=None` 全程 BC，可与 P0-1 并行。
**出厂**：verify_gate prod off→shadow（落地后）。**风险**：中转站强制 JSON 可靠性 → 宽松解析兜底 + pass^k 硬关；`ephemeral_subagent` sync→async 是唯一动既有签名处。

---

## 4. Phase P0-3 — 记忆 + 人格（已提 P0；砍掉已完成项）

> blueprint：[03-P0-3-memory-persona-execution.md](./03-P0-3-memory-persona-execution.md)。
> ✅ **已做好别碰**：`retriever.py`+`enhanced_retriever.py`（7 路 RRF）；`facts.py _CATEGORY_DECAY`+`daily_decay()` 半衰期核心。

| WI | 标题 | 现状 | 要做什么（详见 03） | E2E 等级 |
|---|---|---|---|---|
| **3.1** ★ | **goal/decision/constraint 记忆** | ❌ facts 5 类无此 | **单向事件钩解耦**：P0-1 store 是活跃目标唯一权威，facts `category=goal/decision/constraint` 是其**只读记忆投影**供跨会话召回（不双向回写）；⚠️Round1 修正：钩用**注入 callback**（`bind_on_goal_set(callable)`，main.py 接电时闭包捕获 `_facts_store.upsert`），goal_store **不 import facts**（防 agent←memory import 环）；`_collect_fact_hits` category-agnostic → **新类自动进召回零改检索**；改 `_EXTRACT_PROMPT`(正向豁免别误杀时效性目标)+`_CATEGORY_DECAY` 加 3 类 + scope 字段(user/session) | 🔴 真机(跨会话召回上次目标/决策) |
| **3.2** ★ | **人格画像主动注入 Component** | 🟡 抽了不置顶 | 新 `PreferenceProfileComponent`(构造注入 facts_store，priority=85，bucket=**dynamic** 改偏好下轮即反映)，渲染类 PROFILE.md 块；**显式排除 verify/goal_checker 路径注入人格**（§9 红线1 交叉约束）；**出厂 flag 默认 False、dev 先开** | 🟠 真机(改偏好下轮反映) |
| **3.3** | **Pin + PreferenceMemory 衰减（+修 bug）** | 🟡 衰减核心有缺 Pin | facts 加 `pinned` 列(`schema_v2_migrator` ALTER + `_DDL`)，`daily_decay()` SQL 加 `AND pinned=0`；`PreferenceMemory`(JSON) `match` 内乘 recency decay(对齐 0.005)、pin 项 decay=1.0；🐛**修隐藏 bug：`daily_decay()` 生产从未被调用 → main.py lifespan 接通调度** | 🟢 后端单测(Pin 不衰减)+真机(Forget) |
| **3.4** | **写入分级 light 快路** | 🟡 无 skip_embed | VectorWorker 加 `skip_embed`/`put_doc_light`；高频流(语音 tick/截屏)走 light 跳 embedding，不破坏正常对话入向量 | 🟢 后端单测 |
| **3.5** ⏸可选 | **周期性记忆 nudge** | ❌ | （评审 G2）周期性问 agent「刚发生的有什么值得长期记住」补规则抽取漏掉的非显然信息。**与 3.1 协同；本轮可选，不做则 defer** | 🟢 后端 |

**依赖**：3.1 与 1.1 协同（单向钩，非双写）。3.2 读 3.3 活跃偏好。**红线**：人格只作用交互风格，**完成判定不得被人格影响**；不优化在线时长。

---

## 5. Phase P1-4 — Skills 分级披露 + 自创闭环

> blueprint：[04-P1-4-skills-execution.md](./04-P1-4-skills-execution.md)。三个 flag 均 dev on / prod off。

| WI | 标题 | 现状 | 要做什么（详见 04） | E2E 等级 |
|---|---|---|---|---|
| **4.0** ✚前置 | **接通 compaction** | ❌ `agent_loop` 无 `should_compress` | 触发点在 `agent_loop.run()` 迭代内、budget guard 后/LLM 调用前，复用 `_budget.estimated_tokens` 喂 `should_compress`，原地压 `working_messages`；`skill_prelude`/persona/frozen 都是 `role=system` 被 `_partition` 天然保留；**压缩保留项硬注入 goal_text + pending tasks**（与 1.3 同 marker 去重） | 🔴 真机+全回归长会话 |
| **4.1** | **二级披露做实** | 🟡 LLM 自由裁量 | 新 `SkillMatcher` 复用现成 BGE-M3 对 description 算 embedding，**强匹配(sim≥0.55)才内联正文**进 prelude、弱匹配只留 desc；8K 预算，溢出按 `usage_count` 丢最久未用；与 `skill_invoke` **并存不替换** | 🟠 真机(相关 skill 自动载) |
| **4.2** | **compaction 后 skill 重挂** | ❓ 可能不缺 | **先确认** 4.0 接入是否绕过 assemble：assemble() 每轮重建 prelude 则「重挂」本不缺→该 WI 可能收敛为「验证不丢失」（评审 §3 P4） | 🟢 后端单测 |
| **4.3** ★ | **技能自创闭环 + 用户确认门**（拆 4 子任务） | ❌ Phase F deferred | **4.3a** 触发器检测(hermes：≥5工具/从错误恢复/被纠正/非显然，读 **WI-1.6** 的 ToolPath)；**4.3b** LLM 生成候选写 `SkillMemoryStore(pending)`；**4.3c** ⚠️Round1 修正：**新增 `skill_candidate_proposed`/`skill_candidate_confirm` WS verb + 前端新确认卡组件**(可复用 plan-confirm 卡视觉，但通道是新建——现 `_PLAN_CONFIRM_WAITERS` 是 code 模式 plan 专用通不到桌宠主 UI)；后端复用 Future-await 模式(按 candidate_id key)；**4.3d** 批准才渲染 SKILL.md 落 `user/` 目录+热 reload，拒绝删。**只生成声明式 SKILL.md，绝不自动执行 Python** | 🔴 真机(重复任务→候选→确认卡真点击→下次一键) |

**依赖**：4.0 先做(地基) → 4.1/4.2；4.3 排最后（硬依赖 1.6 ToolPath）。P0-1 未就绪时 4.0/4.1/4.2 走降级直读 goal_store。
**风险**：4.0 改主 loop、回归面最大 → 单独冲刺 + 保留项硬注入 + prod off 先 shadow。

---

## 6. ★ goal_text 跨 Phase 契约（并行前必锁，评审必修#1）

> 🔒 **已冻结（2026-06-04）→ [FP-1/00-CONTRACT-FREEZE.md](./FP-1/00-CONTRACT-FREEZE.md)**：定稿唯一 `SessionGoal`
> schema（解决本节 §6.1 与 [01-blueprint](./01-P0-1-execution.md) §2 的 done/status、criteria、subgoals、
> max_iterations 分歧）+ DDL + 注入预算两路径。**以冻结文档为准**，本节为背景。
> 这是并行开发头号翻车点（pytest+tsc 都过但字段 disagree，正中 `feedback_cross_layer_contract`）。
> **派 1/2/3 并行前先冻结本节**，配 `scripts/e2e_goal_contract.py` live smoke 兜底。

### 6.1 `SessionGoal` schema（WI-1.1 产出，唯一权威）
```python
@dataclass
class SessionGoal:
    goal_id: str            # 稳定 id（done 后转 historical 用）
    session_id: str
    text: str               # 原始目标全文（verify 对照 / 注入用这个）
    subgoals: list[str]     # 子目标（本轮建表，current-subgoal 注入见 1.3）
    progress: float         # 0..1
    status: str             # active | done | abandoned
    criteria: str | None    # 完成判据（可空；2.3 是否用 spec 时定）
    set_at: float
    iterations_used: int
```
### 6.2 只读接口（下游唯一入口，经 `service_context.get("session_goal_store")`）
```python
store.get_goal_text(session_id) -> str | None     # sync, None-safe, flag-OFF/无目标返 None
store.get(session_id)           -> SessionGoal | None
store.get_completed_path(session_id, goal_id) -> ToolPath | None   # WI-1.6, 喂 4.3
```
- **None 语义稳定**：flag-OFF 或无目标一律 None；下游全程对 None BC（不报错、走旧行为）。
- **同步零 I/O**：内存缓存 + 落库异步，下游热路径读不阻塞。
- ⚠️**一致性窗口（Round1）**：`get_goal_text` **永读内存（最新权威）**，库为 durability 备份。窗口 = set 后到 `_persist` await 完成前进程崩溃则该 goal 丢（单机桌宠可接受）；`_persist` 失败 **safe-fail 不抛**（否则 `_handle_goal` 改 async 后异常冒泡到 slash 处理）。
- ⚠️**`increment_iteration` 必须落库（§14-T1）**：现 `agent_loop.py:1091` 只改内存不落库 → 否则重启 `iterations_used` 仍归零，1.5 resume 不完整。

### 6.3 注入格式 + ⚠️两条预算路径（Round2 R-1/R-T2 修正，禁各写一版）
统一注入文本格式（1.3/1.4/1.5 共用）：
```
[当前目标] {text}
[当前子目标] {subgoals[current]}   # 可空
```
⚠️**Round2 关键修正：「system 栈尾」其实横跨两套互不知情的预算系统，必须拆开写清**（详见 §15.2 总账表）：
- **compressor 路径**（1.3 re-anchor / 4.0 保留项 / 1.5 resume）：作 `role=system` 注 `working_messages`，由 `context_compressor._partition` **全量保留、零上限** → **必须新增软上限 `max_system_inject_tokens≈1500`**，超限按 `goal_text > 子目标 > pending tasks` 顺位裁，否则长会话单调膨胀吃掉 last_n。
- **assembler 路径**（3.2 人格 / 4.1 skill）：作 Slice 走 `BudgetAllocator`，`priority<80` 预算紧张时**整块丢**（非比例缩；`budget.py:103`）。
- **goal_text 唯一权威 = compressor system 段**；**3.2 人格 / 4.1 skill 的 Slice 不得重复携带 goal_text**（跨系统去重靠「单一注入源」，marker 去重只在单条路径内生效）。
- **2.3 goal 对照证据 / GD 观测信号**：内存读 / metrics 打点，**不注 prompt、不占预算**。

### 6.4 活跃 goal vs 历史 fact 的双写规则（1.1 × 3.1）
- **source of truth = 1.1 store**（活跃目标）。3.1 facts `category=goal/decision/constraint` 是**只读投影**。
- **单向事件钩**：`store.set()` 成功 → `on_goal_set` → facts upsert（**不从 facts 回写 store**）。
- done/abandoned：store 改 status，facts 投影保留供跨会话召回。

---

## 7. ★ 全局重试预算账本（2.2 前置 spike，评审必修#2）

> 🔒 **已冻结（2026-06-04）→ [FP-1/00-CONTRACT-FREEZE.md](./FP-1/00-CONTRACT-FREEZE.md) §2**：实地核真
> `auto_resume.py:148-173` + `session_activity.py` → attempt 计数 = **per-session 共享额度**（所有 reason
> 共享 `max_attempts=2`）、**in-memory 重启清零**；区别于 `iterations_used`（T1 落库恢复）。以冻结文档为准。
> 现有三套上限：circuit_breaker threshold=3（工具级）/ auto_resume max_attempts=2（目标级 respawn）/
> max_verify_nudges=2（loop 内）。2.2 新「真重规划重试」**不得新造第四套计数器**，否则叠乘失控/死循环。

**账本规则（spec 前先实现这张表的单测上界）**：
```
loop 内重规划：复用 max_verify_nudges=2（每次带"反思+新方案"，非机械 nudge）
   ↓ 2 次仍失败
调 ephemeral_subagent 做一次独立 verify（不计入新计数器）
   ↓ 仍失败
emit verify_exhausted → auto_resume（max_attempts=2，目标级 spawn fresh）
   ↓ 2 次仍失败 → 优雅降级：告诉用户进度 + 留可恢复 state（接 1.5）
工具级 circuit_breaker(3) 独立兜底，不与上述叠乘（不同层）
```
- **死循环上界测试**：构造永久失败任务，断言总 LLM 调用 ≤ 明确上界、最终优雅降级不卡死。

⚠️**Round1 实现要点**：
- `verify_exhausted` 需 **ADD 进 `_AUTO_RESUME_TRIGGER_REASONS` frozenset**（`auto_resume.py:96`）。
- emit 点：在 agent_loop verify 三层耗尽处（`:1045` 那个 `continue` 兜底分支外）改成 `emit ErrorEvent(reason="verify_exhausted")` + `return` 终止本 loop；**不要在 goal_checker 块（:1059）重复 emit**。
- **attempt 计数粒度要先确认**：`AutoResumeOrchestrator` 的 `max_attempts=2`（`:167`）若是 **per-session**，则 verify_exhausted 与 max_iterations/circuit_open **共享同一额度**（verify 重试会偷吃 max_iterations 的 attempt）→ 上界测试要按"共享额度"断言；若 per-(session,reason) 则独占。spec 前 Read `:148-180` 定。
- ⚠️**Round2 respawn 语义（R-T4）**：loop 内 verify 计数 respawn 后清零属**预期**（不算违反上界）；但 `max_attempts` 若 per-session 且 session 跨 respawn 复用——**计数是从库恢复还是清零必须定**，否则重启可绕过 max_attempts 无限重试。且 respawn 后 auto_resume 续跑不能因 redispatcher 未注册就静默 `return`（`main.py:2076-2083`），改 **pending-resume 队列、WS 重连注册后 drain**（control_ws 快照坑同构变体）。

---

## 8. 执行顺序 + 依赖图（补全隐藏依赖边）

```
① WI-1.1 Durable Goal Store + §6 契约冻结 ──┬─→ 1.2 Task图 ∥ 1.6 路径录制
   (第一块多米诺)                            ├─→ 1.3 re-anchor / 1.4 handoff / 1.5 resume(窄)
                                            ├─→ 2.3 verify接goal ─→ 2.4 外部evaluator (隐藏边 2.4←2.3)
                                            └─→ 3.1 goal/decision记忆(单向钩)
② 2.1 结构化反思 ─→ 2.2 真重规划(遵 §7 账本)
③ P0-3 人格 3.2/3.3/3.4(+3.5可选) ── 独立，可全程并行
④ 4.0 接通compaction ─→ 4.1/4.2 ;  4.3(依赖 1.6 ToolPath，最后做)
   隐藏边：4.0 ↔ 1.3（压缩保留活跃 goal，同 marker 去重，需协同设计）
```
**多 agent 并行（Lead-Expert + worktree 隔离 + 端口隔离）**：派1=P0-1(先锁 §6)、派2=P0-2、派3=P0-3、派4=P1-4。
**派 1/2/3 启动前必须先冻结 §6 + §7**。

---

## 9. ⚠️ 数字伴侣红线 × WI 落点（评审必修#4，不止口号）

| 红线（最佳实践 §6） | WI 落点（v2 补全） |
|---|---|
| **1 人格只作用交互、完成判定客观** | 2.3：完成判定输入白名单 + 独立无 persona 的 llm_call + `no_persona_leak` 单测；**3.2 显式排除 verify/goal_checker 路径注入人格**（交叉约束点明） |
| **2 拒绝谄媚式完成** | 2.3/2.4 prompt 加**反谄媚前缀**（"基于客观证据判定，不为讨好放行/报喜"）+ evaluator 反谄媚校验项 |
| **3 主动性带价值不成瘾** | 本轮不做主动性（⏸）；3.2 注明"人格注入不得用于最大化粘性"（设计约束，无新埋点） |
| **4 不优化在线时长反指标** | 纪律：**本轮不新增任何在线时长/粘性优化型埋点**；现有 metrics 不加 engagement 目标 |

---

## 10. 显式 defer / 不做清单（评审必修#5，消除 silent drop）

> **核心：没有"该做却漏"的硬伤，但以下 silent drop 显式表态，避免后续 agent 误以为遗忘。**

| # | 项 | 来源 | 决定 | 理由 |
|---|---|---|---|---|
| G1 | gitignore-aware 上下文采集自查 | cc-haha 4.6 | **本轮做(半天速赢)** | 与 `feedback_context_overflow_bulk_reads` 同源、低成本；至少自查现状一行 |
| G2 | 周期性记忆 nudge | hermes 借鉴3 | **可本轮(WI-3.5 可选)** | 与 3.1 协同；不做则 defer |
| G3 | plan mode 真只读权限自查 | claude-code 4.2 | **defer + 自查一行** | 确认计划确认期是否真切只读；不真则记 backlog |
| G4 | `/verify`+`/run` bundled skill | claude-code 4.3 | **defer** | 与 §11 真测纪律强相关，下一轮值得做 |
| G5 | 桌宠产品级 auto-memory | claude-code 4.6 | **defer** | 与 3.1 区分：规则抽取 vs agent 自决「值得长期记什么」 |
| G6 | 任务级 verified-state checkpoint | hermes 借鉴5 | **defer** | 1.5 窄版只接 goal_text；任务级 checkpoint 单独立项 |
| G7 | TokenJuice 工具输出压缩 | openhuman B6 | **defer** | 本地延迟 ROI 不低，记 backlog |
| G8 | 集中式审批聚合 UX | cc-haha 4.2 | **本轮关联** | 4.3c 确认卡指定走现有权限弹窗通道；聚合视图 defer |
| G9 | 可编辑 .md 记忆 vault | openhuman B1 | **defer** | 与本地隐私不冲突但工程量中 |
| G10 | planner/executor 进程分离 / 价值对齐约束框定 | 最佳实践 §4.2 模式5/6 | **defer** | 本轮架构不动 planner/executor 分离 |
| — | 五路混合检索 / 冷启动接 SaaS / function-calling 模型无关协议 / Honcho / Computer Use bridge / 多通道远程 | 多源 | **不做** | 已做好(检索) / 与本地隐私冲突 / 非紧急 / DeskPet 已更强 |

---

## 11. 验收纪律（对接 DeskPet HARD CONSTRAINT）

- **逐 WI E2E 等级**（见 §2-§5 表末列）：🔴 必须 windows-mcp/CDP 真机 + 截图 + 抓日志；🟠 真机轻验；🟢 纯后端单测够。避免「全部 windows-mcp 过度」或「全部跳过走捷径」。
- **真 E2E ≠ 脚本回放**：验证真实运行栈出站行为，不能 import 内部模块查状态当证据。
- **pass^k 稳定性**：关键能力 **k≥3** 连续测稳定——指定必测：**1.1 持久(重启)、1.3 抗漂移、2.2 重规划、2.3 verify、4.3 自创**。
- ⚠️**真机 E2E 证据诚实分级（Round1，§14.3 详表）**：标 🔴 的 WI 里 **1.1/1.3/2.2/3.1/4.3 能产出干净的「真模拟人」证据**（设目标/长对话/伪完成/跨会话/确认卡真点击都是用户可感行为）；**1.4/1.5/4.0/4.1 的「真机」实质偏 log grep**（核心行为在后端 prompt 拼装层，UI 表层看不全）——这几条 §14.3 已标注「真机=log 验证为主」，**严禁子代理拿 log 当「模拟人点击」交差**（违反 HARD CONSTRAINT 精神）。所有「只能后端单测」的部分**照做不砍**，只是不计入「真模拟人」证据。
- ⚠️**pass^k 真机自动化（Round2 §15.4 / R-6）**：复用 `testcase/_cdp.py`(locate/eval/shot/text) + `testcase/_send.py`(真 SendInput 输入) 写 `testcase/passk_runner.py` 循环——**每轮真 OS 事件 + 真 relay 调用，不是 resolution 回放**（不违反 `feedback_real_e2e_not_script_replay`），每轮 `shot` 存证。1.1/1.3/2.2/4.3 可真机 pass^k；§7 死循环上界 / 1.2 并发 claim / 2.3 no_persona_leak 只能后端 pytest pass^k（照做不砍，不计入真模拟人证据）。
- ⚠️**shadow 上线可观测（Round2 §15.5 / R-T6）**：verify off→shadow 后 receipt 需扩 `shadow_verdict`/`actual_outcome` 弱信号/降级率字段；**go/no-go 翻 strict 量化标准**：连续 N≥200 高后果判定中 误杀率<2% 且 漏放率<5% 且 LLM 降级率<10% 且 verify_latency_p95<3s。
- **flag-OFF 运行时基线**：`scripts/e2e_flag_off_baseline.py`（R-T5）——flag 全 OFF 冷启动+对话，断言新表不建、关键表字节快照 hash 不变（运行时验证字节级契约，非 spec 文字）。
- **跨层契约**：`scripts/e2e_goal_contract.py`（§6）+ `scripts/e2e_*.py` live smoke 兜底。
- **MemEval 不回归**：P0-3 改动后跑现有记忆严测，确认 7 路检索 Recall 不掉。
- **常驻资源回归（R-T8）**：阶段 C 后「空闲 30min + 10 轮对话」监控 RSS/CPU 不泄漏（多后台 task + embedding 缓存）。
- 完成即更新 `STATUS/status.md`（项目 HARD 纪律）。

---

## 12. 建议分阶段交付（用户自行排期参考，评审 §4.3）

**阶段 A — 地基 + 低风险速赢（先锁 §6/§7）**
1. **WI-1.1 + §6 契约冻结**（最先，先锁契约再写码）+ §7 账本单测。
2. **3.3 Pin / 3.4 light / 3.1 记忆 + WI-1.6 路径录制** —— 纯后端低风险，早期看得见的交付。
3. G1 gitignore-aware 自查（半天）。

**阶段 B — 抗漂移闭环 + 自我纠错**
4. **1.2 / 1.3 / 1.4 / 1.5**（依赖 1.1）。
5. **2.1 → 2.3 → 2.2 → 2.4**（2.2 先做 §7 账本 spike；2.3 触护城河上 pass^k）。
6. **3.2 人格注入**（与 2.3 协同处理红线1 交叉约束）。

**阶段 C — 高风险大件（单独冲刺）**
7. **4.0 单独冲刺**（全回归长会话）→ 4.1 → 4.2。
8. **4.3 拆 4.3a-d 最后做**（依赖 1.6）。

**排序要点**：4.0 最高回归风险**单独隔离**；3.x 低风险**提前**给早期交付感；2.2 预算账本列为**前置 spike**。

⚠️**Round2 跨阶段硬锚 + 半截集成诚实标（§15.6 / R-8）**：
- **A→B 硬锚**：A 阶段必须先接通 **R-T1 lifespan `load_persisted`**，否则 B 的 1.5 resume 读不到恢复的 goal。
- **B→C 硬锚**：B 阶段 2.3 落地即开始采集 **R-T6 shadow 指标**，否则 C 上线无 go/no-go 依据。
- **半截集成诚实标**：A 阶段 **1.6 ToolPath 录了但消费者 4.3 在 C**——标「阶段 A 录制、阶段 C 消费」，后端单测断言 `get_completed_path` 正确即验收，不误判死代码。每阶段验收用「不依赖下游消费者的自闭环收益锚点」（A=`/goal` 重启仍在；B=长对话不漂移+伪完成重规划；C=确认卡真点击落 SKILL.md）。

---

## 13. 仍待定（spec 时决，已留口子）

- 3.2 人格注入出厂开还是 flag（倾向 flag default False，dev 先开）。
- 1.2 任务图：在 `code_todos` 扩展依赖 vs 新建统一 `goal_tasks` 表（blueprint 倾向后者，TeamStore「建后即销」与「持久共享」的调和方案）。
- 2.3 完成对照用 `text` 还是 `text+criteria`。
- 是否走 OpenSpec change / 拆 13 组式 TDD 测试组再实现。
- 各 Phase 是否多 worktree 并行（端口隔离见 CLAUDE.md 拓扑）。

---

## 14. ★ 实现前置硬化（Round1 整合 — 并行开工前必读）

> 来源 [06-challenge-round1.md](./06-challenge-round1.md)（逐符号实地核真代码）。
> **不砍任何任务**——以下全是「让任务更可执行」的前置 spike / 签名冻结 / 诚实分级，新增 7 个子任务 T1-T7（都补在既有 WI 下，不独立成新功能）。

### 14.1 开工前必做的 7 个前置 spike / 补充子任务

| ID | 归属 WI | 内容 | 为什么必须先做 |
|---|---|---|---|
| **T1** | 1.1/1.5 | **`increment_iteration` 落库**：`agent_loop.py:1091` 每次 increment 后 async 落库 | 否则重启 `iterations_used` 归零，blueprint 声称的修复不完整 |
| **T2** | 4.1 | **embedder.encode 异步化包裹**：若 `_embedder.encode` 同步 CPU 密集，SkillMatcher query 走 `await asyncio.to_thread(...)` | 防首轮 assemble 在 async 里同步 encode 阻塞 event loop |
| **T3** | 3.2/4.1 | **BudgetAllocator 裁剪粒度核查**：确认 priority=85 dynamic 块在预算溢出时是否被**整块丢**（`04:134` 暗示 skill 是 bucket 级整块丢） | 若 bucket 级，3.2 人格块要改「独立 bucket + 不可裁标记」否则会被整块裁掉 |
| **T4** | 1.3/4.0 | **compress/compact 统一签名冻结**：钉死 `async def compress(self, messages, *, goal_text=None, pending_tasks=None)`，1.3 与 4.0 共用，history_compactor 对齐 | 1.3 和 4.0 **改同一个 compress 函数**、参数名不一致 = 契约漂移（正中 `feedback_cross_layer_contract`） |
| **T5** | 1.2 | **子 agent 进程模型核查**：Read `spawn_team.py` 确认 teammate 是同进程协程还是子进程 | 决定 goal_tasks claim 走 `_write_lock` 串行（同进程）还是 `BEGIN IMMEDIATE`（跨进程）；B-1 的前提 |
| **T6** | 2.2 | **ephemeral mock 异步化回归**：现有 `test_verify_gate.py` 里所有 sync mock ephemeral 改 `AsyncMock`，列为 2.2 build order **第 1 步** | 否则 `await sync_mock()` 直接 `TypeError: object bool can't be used in 'await'`，最易漏的回归 |
| **T7** | 4.3c | **skill_candidate WS verb + 前端确认卡**（明确为**新接线非纯复用**）：后端 Future-await(按 candidate_id) + 前端新组件 + WS verb 白名单 | 现 `_PLAN_CONFIRM_WAITERS` 是 code 模式 plan 专用，通不到桌宠主 UI；前端工作量不可省 |

### 14.2 冻结的关键签名 / 并发原语（并行前锁，禁各写各的）

| 符号 | 冻结决定 | 证据 |
|---|---|---|
| `compress` | `async def compress(self, messages, *, goal_text=None, pending_tasks=None)`（1.3+4.0 共用） | `context_compressor.py:135` 现单参 |
| `consult_ephemeral_subagent` | 改 **async**；`check` 保持 sync；callable→`Callable[[Any],Awaitable[bool]]` | `verify_gate.py:284/394`，agent_loop 调用 `:973/:992` |
| `goal_tasks` claim | 同进程→SessionDB `_write_lock` 串行 claim（非 RETURNING 魔法）；跨进程→`BEGIN IMMEDIATE` | `session_db.py:97` asyncio.Lock vs `team_store.py:262` per-team BEGIN IMMEDIATE |
| `build_teammate_tools` | **改函数体**加 `task_graph_store/goal_id` 参数 + 新 triple；非"加进去" | `teammate_tools.py:176-287` return 写死 5 元组 |
| `on_goal_set` 钩 | **注入 callback**（`bind_on_goal_set`），goal_store 不 import facts | `goal_store.py` 现零依赖，防 agent←memory import 环 |
| `verify_exhausted` | ADD 进 `_AUTO_RESUME_TRIGGER_REASONS` frozenset；emit+return 终止 loop | `auto_resume.py:96`，agent_loop `:1045` |

### 14.3 诚实 E2E 分级（防子代理拿 log 当"模拟人"交差）

| WI | 标级 | 真机能产出干净"模拟人"证据? | 必须后端单测的部分(照做不砍) |
|---|---|---|---|
| 1.1 | 🔴 | ✅ 设目标→taskkill→重启→`/goal` 查仍在(截图+log) | iterations 落库恢复(T1) |
| 1.2 | 🔴/🟢 | 🟡 TodoPanel 进度可见；**并发 claim 不双占只能后端单测** | 原子 claim/DAG ready/环检测 |
| 1.3 | 🔴 | ✅ 长对话顶过压缩→LLM 仍回原目标(截图) | GD 信号计数/`_partition` 保留 |
| **1.4** | 🟠 | ⚠️**真机≈log grep**(charter 后端拼，UI 看不到) | charter 注入 BC/回收过滤 |
| **1.5** | 🟠 | ⚠️**真机≈log grep `_is_goal_anchor`** | getter None BC |
| 2.1 | 🟢 | 纯后端 | parse 三形态/flag off |
| 2.2 | 🔴 | ✅ 伪完成→拦→重规划→二次真产物→ArtifactCard | 三层上界/difflib/ephemeral 救援 |
| 2.3 | 🔴 | 🟡 伪完成拦/真完成放行可截图；**`no_persona_leak` 只能后端单测** | 两分支合流/证据白名单 |
| 2.4 | 🟠 | 🟡 高后果重试可截图；**判定/0调用断言后端单测** | 高后果判定/safe-fail |
| 3.1 | 🔴 | ✅ 会话A说决策→新session→会话B答出 | 双写钩/scope/flag off |
| 3.2 | 🟠 | ✅ 改偏好→下轮反映(截图+prompt log) | 黑名单/空 Slice |
| 3.3 | 🟢/🔴 | 🟡 Pin 不衰减**主要后端单测**(难推进时间)；Forget 可真机 | daily_decay 调度(bug)/ALTER 守护 |
| 3.4 | 🟢 | 纯后端 | skip_embed/FTS 仍召回 |
| **4.0** | 🔴 | ⚠️压缩后追问原目标可截图；**skill_prelude 存活/去重只能后端单测** | should_compress 接电/保留项/marker |
| **4.1** | 🟠 | ⚠️**真机≈log grep `skill_auto_loaded`**(UI 看不出正文进 prelude) | 相似度阈值/tie-break/降级 |
| 4.2 | 🟢 | 纯后端 | 25K 预算/不重复 |
| 4.3 | 🔴 | ✅ 多步→候选确认卡→真点击保存→SKILL.md 落盘→新会话复用 | 触发器分支/拒绝删 pending |

**结论**：1.1/1.3/2.2/3.1/3.2/4.3 有干净"真模拟人"链；**1.4/1.5/4.0/4.1 的"真机"= log 验证为主**（已在 §11 标注）；所有"只能后端单测"项全做、只是不计入"真模拟人"证据。

### 14.4 Round1 总判（来自 06）

> blueprint 质量很高，绝大多数符号引用、签名、BC 设计经实地核真代码属实，**可以照着写**；但上述 3 个致命阻塞（1.2 并发模型错配 / 2.2 ephemeral async 涟漪 / 1.2 build_teammate_tools 非"加进去"）+ T1-T7 必须在**并行开工前 1 天**钉死，否则并行开发会在第 3 天集体翻车。**这些全是"先想清楚再写"的硬化，不增不减任务范围。**

---

## 15. ★ 运行时硬化（Round2 整合 — 跑起来/集成/上线前必读）

> 来源 [07-challenge-round2.md](./07-challenge-round2.md)（实地核 lifespan/预算/迁移/重连/harness）。
> Round2 专打 Round1 静态层抓不到的**运行时**问题。**不砍任何任务**，新增 8 个子任务 R-T1~R-T8（都补在既有 WI 下）。

### 15.1 Round2 三大致命运行时风险（单测全绿、真机首跑就翻车型）

1. 🔴 **system 栈尾「多注入源」无共享预算 + 跨两套互不知情的预算系统**（`budget.py` 整块丢 vs `_partition` 全保留）→ §6.3 已拆两条路径 + 软上限；总账见 §15.2。
2. 🔴 **`session_goal_store` 在 lifespan 从不 `load_persisted`**（`main.py:1067-1080`）→ 1.1 头号真机证据当前接不通 → R-T1 写死 lifespan 接电顺序。
3. 🔴 **respawn 后 auto_resume 与 control WS 重连竞态**（`main.py:2076-2083` 未注册即静默 return）→ R-T4 pending-resume 队列。

### 15.2 8 个新子任务 R-T1~R-T8（补在既有 WI 下，不增减功能）

| ID | 归属 WI | 内容 | 为什么必须 |
|---|---|---|---|
| **R-T1** | 1.1/1.5/lifespan | **goal_store 接电进 lifespan**（写死顺序）：`_sdb.initialize → schema_v2_migrator(pinned) → facts_store → goal_store.bind_persistence + await load_persisted → bind_on_goal_set → 注册 auto_resume redispatcher → 调度 daily_decay_loop` | 现无 load_persisted，1.1「重启仍在」接不通；顺序错则「A 没 ready 被 B 用」 |
| **R-T2** | 1.3/4.0/§6.3 | **system 栈尾拆两条预算路径 + compressor 软上限 1500**（见 §6.3 + §15.3 总账） | 两套预算系统，§6.3 原「都注 system 栈尾」是运行时盲区 |
| **R-T3** | 2.x/1.4 | **LLM 失败降级矩阵实现 + 故障注入单测**（见 §15.4） | relay 规模化必畸形/超时；blueprint 只说「宽松解析」没定降级后拦/放 |
| **R-T4** | 1.5/2.2/§7 | **respawn pending-resume 队列** + §7 respawn 计数语义 | redispatcher 未注册即吞掉，control_ws 快照坑同构 |
| **R-T5** | 全 flag WI/§11 | **`scripts/e2e_flag_off_baseline.py`**：flag-OFF 断言新表不建 + 字节快照 hash 不变 | 「flag-OFF 字节不变」现只有 spec 文字 |
| **R-T6** | 2.3/2.4/observability | **shadow 可观测埋点**：receipt 扩 `shadow_verdict`/`actual_outcome`/降级率 + go/no-go 标准（见 §15.5） | 现 receipt 无 ground truth，无法量化误杀/漏放判翻 strict |
| **R-T7** | 1.1/1.2/3.3/拓扑 | **多 worktree 独立 `DESKPET_USER_DATA_DIR` + 老用户首启迁移验收**（真实老 db 测 pinned ALTER/新表/0 丢失/flag-OFF 不发生） | 共享 db 迁移串味；老用户首启无验收锚点 |
| **R-T8** | 4.1/1.2/3.3/§11 | **常驻资源回归 + SkillMatcher query embedding `to_thread`+缓存 + goal_tasks 落库 debounce** | 常驻桌宠叠多后台 task，热路径同步 encode 卡 event loop / `_write_lock` 争用 |

### 15.3 多注入源预算与优先级总账（详表见 07 §2）

两条路径独立有界：**compressor system 段**（goal_text re-anchor 第1顺位保 > 子目标 > pending tasks 摘要，软上限 1500）；**assembler Slice**（persona 90 > persona_profile 85 保 > skill 70 溢出整块丢）。goal_text 只由 compressor 段权威注入，人格/skill Slice 不重复携带。3.2 **不能依赖 dynamic bucket 保护**（裁剪只看 priority + memory 豁免）。

### 15.4 LLM 依赖点失败降级矩阵（详表见 07 §3）

范式跟 `context_compressor.compress`（失败不抛、返回安全降级、记录降级事实）。关键第三态：**2.3 GoalChecker 超时 → 降级到仅客观证据判定 + 标 `goal_check=skipped`（不默认 pass/fail）**；2.1 反思畸形 → 降级机械 nudge；2.4 evaluator 超时 → 高后果保守拦+提示手动确认；1.4 judge 失败 → 不过滤全回收+标 unfiltered。每个降级事实进 metrics/receipt 供 shadow 量化；pass^k 必含故障注入用例。

### 15.5 shadow 上线可观测（详方案见 07 §4）

receipt 扩 `shadow_verdict`(strict 会拦/会放) + `actual_outcome`(用户后续 redo/抱怨/采纳弱信号) + 降级率 + `verify_latency_p95`。**go/no-go 翻 strict**：连续 N≥200 高后果判定，误杀率<2% ∧ 漏放率<5% ∧ LLM 降级率<10% ∧ p95<3s。任一不达标留 shadow。

### 15.6 Round2 总判（来自 07）

> v3 经 Round1 静态硬化「写得出来」可信；Round2 运行时层补出 8 阻塞（R-1~R-9）+ 8 子任务，全部「不砍任务、只让任务真能跑」。最致命 3 个（栈尾无共享预算 / goal_store 无 load_persisted / respawn 重连竞态）必须在**阶段 A 开工前**钉死，否则单测全绿、真机首跑翻车。
