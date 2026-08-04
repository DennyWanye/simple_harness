# Round1 实现视角挑战（下周一动手的工程师）

> 视角：下周一就要亲手写代码的高级后端工程师（Python / SQLite / async / LLM agent loop）。
> 方法：把 00-PLAN.md + 4 份 blueprint 引用的每个类/方法/字段拿去**实地 Read 真代码**核实，猎杀"写到一半发现跑不通"的实现级阻塞点。
> 硬约束：**不砍任务**。所有"难做"的项一律拆细 / 补前置，不删。
> 核实日期：2026-06-04 ｜ 核实的真实文件见每条「证据 file:line」。

---

## 0. 总判：能不能直接照着写代码？

**结论：blueprint 质量很高，绝大多数符号引用属实、签名核对正确、BC 设计扎实——但有 3 个"写到一半才会爆"的实现级阻塞点，必须在 v3 先钉死再开工，否则并行开发会在第 3 天集体翻车。**

核实结论速览（好的一面，先肯定）：
- `goal_store.py:10-11` 纯内存 + sync 全对（`SessionGoal` 字段 `session_id/text/set_at/max_iterations/iterations_used/done` 逐字段属实）。
- `should_compress(prompt_tokens:int)`（`context_compressor.py:129`）签名对，blueprint 4.0 用 `should_compress(_budget.estimated_tokens)` 可直接调；budget guard 落点 `agent_loop.py:631`、selfcheck tier 落点 `:653` 都属实。
- `_make_str_llm_call(provider, *, max_tokens=512)`（`main.py:601`）返回 async `(prompt:str)->str`，provider=None 返 None——blueprint 2.x 复用方式正确。
- `_CATEGORY_DECAY`（`facts.py:57`）+ `VALID_CATEGORIES` 由其 keys 派生（`:69`）+ `_collect_fact_hits`（`enhanced_retriever.py:236`）category-agnostic——"新类自动进召回零改检索"成立。
- 🐛 **`FactsStore.daily_decay()`（`facts.py:772`）生产从未被调用属实**——`main.py` grep `daily_decay` 0 命中（只有 `retriever.py:837` 的 salience 版），3.3-bug 是真 bug，修对了。
- `verify_gate.check`（`:284`）与 `goal_checker.check`（agent_loop `:1070` await）确实是两个**顺序 if 块**（`agent_loop.py:960` vs `:1059`），2.3 合流判断正确。
- `_AUTO_RESUME_TRIGGER_REASONS` frozenset（`auto_resume.py:96`）+ `is_auto_resume_trigger`（`:104`）+ `max_attempts=2`（`:125`）全对。

**最大的"写到一半跑不通"风险（3 个，下面 §1 详述）：**
1. **WI-1.2 共享 state.db 并发模型与 blueprint 写的"复用 TeamStore 原子 claim"是两套不兼容机制**——TeamStore 用 per-team 独立 db 文件 + `BEGIN IMMEDIATE`，SessionDB 用单进程 `asyncio.Lock` 串行化所有写。把 `goal_tasks` 落 SessionDB 又想"子 agent 并发原子 claim"，会发现 SessionDB 根本没有 `claim`/`RETURNING` 这套，且其 `_write_lock` 只防同进程协程、不防多进程子 agent。这块要么补一套真并发原语，要么明确 claim 走 SessionDB `_write_lock` 串行（性能够用但要写清）。
2. **WI-2.2 `ephemeral_subagent` sync→async 改造涟漪没列全**——`verify_gate.check`（`:284`）**整个是 sync**、`consult_ephemeral_subagent`（`:394`）**sync**、agent_loop 调用点（`:973` `check`、`:992` `consult`）**都没 await**。blueprint 说"把 consult 改 async、调用点 `:992` 改 await"——但 `check` 本身也 sync，2.3 又要在 `check` 里读 goal 做对照（纯内存读 OK 不用 async），可一旦 ephemeral 改 async，`consult` 的调用点在 `check` 之外的 `:990-997`，要确认改 async 后 `check` 仍 sync、只有 consult 这一处 await——blueprint 没画清这条边界，容易顺手把 `check` 也染成 async 引发更大涟漪。
3. **WI-1.2 `build_teammate_tools` 不是"加进去"就行**——它（`teammate_tools.py:176`）是 keyword-only `(*, store, team_id, teammate_id)` 且 `return` 一个**写死的 5 元组 list**（`:281-287`），没有"追加任意工具"的入口。要让子 agent 拿到 `TaskList/TaskUpdate`，必须**改这个函数体**（加新 triple）或在 spawn_team 组装 subset registry 处另注入——且 `goal_tasks` 工具操作的是 SessionDB（跨 session 共享），而现有 teammate 工具操作的是 per-team TeamStore，两套 store 在子 agent 工具集里要共存。

---

## 1. 实现级阻塞点（逐条）

### B-1 🔴 WI-1.2：共享 state.db 并发 claim —— "复用 TeamStore 原子模式"是错配

- **问题**：blueprint（`01:137,176,187`）说 `goal_tasks` 落**共享 state.db**、"复用 TeamStore `claim_task` 的 `UPDATE...RETURNING` 原子模式"。但两者并发模型根本不同。
- **证据**：
  - TeamStore（`team_store.py:9`）= **每 team 独立 `.db` 文件**，`claim_task`（`:262-300`）用 `aiosqlite.connect(per_team_path)` + `BEGIN IMMEDIATE` + `UPDATE...RETURNING`。注释明说"per-team db files keep WAL lock-contention low"。
  - SessionDB（`session_db.py:97`）= **单一 state.db** + `self._write_lock = asyncio.Lock()`，所有写经 `_with_retry`（`:196`）在 `async with self._write_lock`（如 `:233`）串行。SessionDB **没有** `claim`/`RETURNING`/`BEGIN IMMEDIATE` 这套，也没有 `goal_tasks` 任何方法。
  - `asyncio.Lock` 只在**同一进程同一 event loop** 内互斥。子 agent 若是同进程协程（看 spawn_team 实现）→ `_write_lock` 够用；若跨进程 → `_write_lock` 完全不防，要靠 SQLite busy_timeout（`session_db.py:122` 有设 5000）。
- **具体修法（不砍任务）**：
  1. v3 在 WI-1.2 里**先回答一个事实问题**：子 agent（spawn_team 的 teammate）是同进程协程还是子进程？（读 `spawn_team.py` 的 spawn 实现确认——blueprint 没核这点）。
  2. 若同进程：`goal_tasks` 的 claim 走 SessionDB 既有 `_write_lock` 串行即可，**不需要 RETURNING 原子魔法**——直接在 `_write_lock` 内 `SELECT ready 候选 → UPDATE 单行 → 返回`，asyncio.Lock 保证原子。把 blueprint "复用 TeamStore RETURNING" 改写成 "复用 SessionDB `_with_retry`+`_write_lock` 串行 claim"。
  3. 若跨进程：必须给 `goal_tasks` 的 claim 显式 `BEGIN IMMEDIATE`（像 TeamStore），且确认 SessionDB 的 `aiosqlite.connect` 模式允许（SessionDB 共享一条连接还是每次新开？要 Read `session_db.py:86-130` `__init__`/连接管理确认，本轮我只核到有 `_write_lock` + WAL，未核连接复用方式）。
  4. **DAG ready 判定**两步法（blueprint `01:176` 已写"先 SELECT ready 再 claim"）在串行锁下天然安全；跨进程下两步之间要在同一 `BEGIN IMMEDIATE` 事务内，否则 TOCTOU。
- **影响 WI**：WI-1.2（核心）；连带 WI-1.4（子 agent 经 charter 拿 goal_id 后用这套工具）。

### B-2 🔴 WI-1.2：`build_teammate_tools` 是写死 5 工具的闭包工厂，不能"加进去"

- **问题**：blueprint（`01:138,193`）说"`TaskList/TaskUpdate` 加进 `build_teammate_tools`"、"`build_teammate_tools` 合并 task_graph 工具"。
- **证据**：`teammate_tools.py:176-287` `build_teammate_tools(*, store, team_id, teammate_id)` 签名 keyword-only，且函数末尾 `return [5 个写死的 triple]`（`:281`）。`store` 参数类型是 `TeamStore`（`:34` import），**不是** SessionDB / TaskGraphStore。没有 `extra_tools` 之类入口。
- **具体修法（不砍任务）**：
  1. 直接**改 `build_teammate_tools` 函数体**：加 `task_graph_store: Optional[TaskGraphStore]=None, goal_id: Optional[str]=None` 参数，在内部追加 `team_task_*` 之外的 `goal_task_create/list/update` triple（注意命名避免和现有 `team_task_*` 混淆）。
  2. 这些新工具闭包捕获的是 **TaskGraphStore（落 SessionDB）**，与现有捕获 TeamStore 的工具**并存**——子 agent 工具集里两套 store 并行，要在工具 description 里写清"team_task_* 是本团队临时任务、goal_task_* 是跨会话目标任务图"，否则 LLM 会混用。
  3. `FORBIDDEN_TEAMMATE_TOOLS`（`:42` = `{agent, agent_parallel, spawn_team}`）不含新工具，无需改；但 spawn_team 组装 subset registry 处要把新 triple 注册进去（读 `spawn_team.py:148-252` 确认注册循环）。
- **影响 WI**：WI-1.2、WI-1.4。

### B-3 🔴 WI-2.2：ephemeral sync→async 涟漪 + `check` 不能误染 async

- **问题**：blueprint（`02:137`）"推荐把 `consult_ephemeral_subagent` 改 async，agent_loop `:992` 改 `await`"。但没界定 `check` 是否跟着改、没列全调用点。
- **证据**：
  - `verify_gate.check`（`verify_gate.py:284`）sync；`consult_ephemeral_subagent`（`:394`）sync，内部 `bool(self.ephemeral_subagent({...}))`（`:418`）同步调 callable。
  - agent_loop 调用点：`check` 在 `:973`（无 await）；`consult` 在 `:990-997`（无 await）。两者都在 async 循环内但当前都同步调。
  - `ephemeral_subagent: Optional[Callable[[Any], bool]]`（`:275`）签名是 sync callable 返 bool。
- **具体修法（不砍任务）**：
  1. v3 明确：**只把 `consult_ephemeral_subagent` 改 async**，`check` 保持 sync（2.3 在 check 里读 goal_text 是纯内存读 `store.get(sid).text`，不需 async；客观证据 objective_evidence 来自已在内存的 ledger/receipt，也不需 async）。
  2. callable 类型从 `Callable[[Any], bool]` 改成 `Callable[[Any], Awaitable[bool]]`；`consult` 内 `bool(await self.ephemeral_subagent({...}))`。
  3. agent_loop `:992` 的 `self.verify_gate.consult_ephemeral_subagent(...)` 加 `await`（它本就在 async 协程 + try 块内，安全）。
  4. **接电点**（`main.py build_agent`，blueprint `02:138`）：`make_ephemeral_verifier(_make_str_llm_call(local_llm or cloud_llm, max_tokens=256))`——`_make_str_llm_call` 已返回 async callable（`main.py:609`），天然契合 async ephemeral，不用跨线程 `run_coroutine_threadsafe`（blueprint `02:137` 也倾向这个，对的）。
  5. **回归测试必加**：现有 `test_verify_gate.py` 里凡 mock `ephemeral_subagent` 的用例，mock 要从 sync 改 async（`AsyncMock`），否则 `await sync_mock()` 报 `TypeError: object bool can't be used in 'await'`——这是最容易漏的回归点。
- **影响 WI**：WI-2.2（核心）、WI-2.3（共用 ephemeral）、WI-2.4。

### B-4 🟠 WI-1.1/§6：`get_goal_text` 同步读 vs 异步落库的一致性窗口

- **问题**：§6.2 承诺 `get_goal_text(sid)` "sync 零 I/O + 落库异步"。blueprint `01:84` 写 `/goal set` 时 `store.set()`(sync 写内存) → `await store._persist(goal)`(async 落库)。一致性窗口要写清。
- **证据**：现 `set`（`goal_store.py:69`）纯 sync 写 dict。下游 `get`（`:92`）读 dict。如果 set 后内存先更新、`_persist` 还没 await 完，**同进程内** `get` 读到的是最新内存值（OK）；但 `load_persisted`（重启恢复）若在 `_persist` 失败时，内存有、库没有 → 重启丢。
- **具体修法（不砍任务）**：
  1. 明确写：**内存是 read 权威，库是 durability 备份**。`get_goal_text` 永远读内存（最新），重启走 `load_persisted` 灌内存。一致性窗口 = "set 后到 `_persist` 完成前若进程崩溃则该 goal 丢"——对单机桌宠可接受，但要在 §6 写明这个窗口。
  2. `_persist` 失败要 log + 不抛（safe-fail），否则 `_handle_goal` 改 async 后异常会冒泡到 slash 命令处理。
  3. WI-1.5 resume "iterations_used 从库恢复"（`01:106,346`）依赖 `_persist` 把 `iterations_used` 也写进去——但现 agent_loop `increment_iteration`（`:1091`）只改内存不落库！要补：每次 `increment_iteration` 后也要 async 落库一次，否则重启 iterations 仍归零（blueprint `01:106` 声称修了这个 bug，但没说 increment 落库点——这是隐藏缺口）。
- **影响 WI**：WI-1.1、WI-1.5。**新增子任务见 §2-T1**。

### B-5 🟠 WI-4.1：BGE-M3 复用可达性 + 冷启动时序

- **问题**：blueprint（`04:117-119`）SkillMatcher "复用现成 BGE-M3，与 retriever 同一实例"。要确认那个 embedder 在 SkillComponent 调用点真拿得到 + 启动时序。
- **证据**：
  - `_embedder`（`main.py:804`）是模块级单例，`_facts_store`/`_message_chunker`/retriever 全注入它（`:824,832,848,893`）——**实例存在且共享，可达性 OK**。
  - 但 SkillComponent（`assembler/components/skill.py`）当前**不持有 embedder**；要新走依赖注入把 `_embedder` 传进 SkillMatcher（仿 `WorkspaceMemoryComponent(store=...)` 先例，blueprint 3.2 `03:157` 也提这条路）。
  - **冷启动时序风险**：`_embedder` 构造（`:804`）在 lifespan（`:1416`）内还是之前？skill 注入在 assemble 时（每轮），embedder 若懒加载模型、首轮 query 时模型没 ready → blueprint `04:144` 已写"embedder 不可用降级为纯 desc 列表"，但要确认 `_embedder` 有 `is_ready()`/同步可调的 encode，否则首轮 assemble 在 async 里同步 encode 会阻塞 event loop。
- **具体修法（不砍任务）**：
  1. SkillMatcher 注入 `_embedder` 走构造参数（main.py build_assembler 处传），不自建模型——对。
  2. v3 补一句：skill description 的 embedding 在 **loader.reload() 后一次性预算**（blueprint `04:122` 已写"启动/reload 时算"），不要每轮 assemble 重算；query embedding 每轮算一次，复用 retriever 同一条 encode 路径（确认是否 async）。
  3. 若 `_embedder.encode` 是同步 CPU 密集：要 `await asyncio.to_thread(embedder.encode, query)` 避免阻塞 loop——blueprint 未提，**新增子任务见 §2-T2**。
- **影响 WI**：WI-4.1、WI-4.2（复用 matcher）。

### B-6 🟠 §7 重试账本：`verify_exhausted` 谁 emit、谁消费、会不会和现有 recv loop 打架

- **问题**：§7 + blueprint `02:96,113` 说 verify 三层耗尽 → `emit ErrorEvent(reason="verify_exhausted")` → auto_resume 接管。要确认 emit 点真能串起来 + 不和现有触发打架。
- **证据**：
  - `_AUTO_RESUME_TRIGGER_REASONS`（`auto_resume.py:96`）现集合不含 `verify_exhausted`（要 ADD 进 frozenset，`02:96` 写对了）。
  - 但 agent_loop 现在 verify 失败是 `continue`（`:1045`）**留在同一 loop 内重试**，不 emit ErrorEvent；ErrorEvent 是给 budget_block（`:610`）那种**终止**用的。要新增"verify_nudges 耗尽 + ephemeral fail"后 emit `ErrorEvent(reason="verify_exhausted")` 并 `return`（终止本 loop），让上层 auto_resume 接。
  - **打架风险**：现有 `max_iterations` / `circuit_open` 也会触发 auto_resume；verify_exhausted 叠加后，同一 turn 可能先撞 verify_exhausted 再撞 max_iterations，auto_resume 的 `max_attempts=2`（per-session 计数，`:167`）是否被两种 reason 共享耗尽？§7 账本说"不叠乘"，但 auto_resume 的 attempt 计数是**按 session 不按 reason**（要 Read `:148-180` handle_failure 确认计数 key）——若共享，verify 重试会偷吃 max_iterations 的 attempt 额度。
- **具体修法（不砍任务）**：
  1. v3 在 §7 明确 auto_resume attempt 计数粒度（per-session 还是 per-(session,reason)），并据此说清 verify_exhausted 是否独占额度。
  2. agent_loop 新增 emit 点：verify 三层耗尽处（`:1045` 那个 `continue` 的兜底分支外）改成 emit + return。要小心**不要**在 goal_checker 块（`:1059`）也重复 emit。
  3. 死循环上界单测（§7 已要求）必须断言：永久失败任务的总 LLM 调用 ≤ `2(verify nudge)*1 + 1(ephemeral) + 2(auto_resume spawn)*max_iter` 的明确上界。
- **影响 WI**：WI-2.2、§7 账本。

### B-7 🟠 WI-4.3：`skill_candidate_proposed` 复用"现有权限弹窗"——弹窗复用性未核

- **问题**：blueprint（`04:233`）4.3c 复用"现有权限弹窗/plan-confirm 确认门"。但未核那个弹窗的消息格式/前端 handler 能否承载技能候选。
- **证据**：
  - 后端确实有 plan-confirm 硬门基建：`_PLAN_CONFIRM_WAITERS`（`main.py:626`）+ Future await 机制。但它是 **code 模式 plan 确认**专用（`:620-626` 注释），payload 是 `{fut, text}`，不是通用审批通道。
  - TeamStore 有 `request_permission/grant_permission`（`team_store.py:460-503`）——但那是 **team 内**权限队列，不通到桌宠主 UI。
  - 没核到一个"通用前端确认卡组件"能直接吃 `skill_candidate_proposed`。前端 handler / WS verb 白名单（类似 `p4_ipc._VALID`）要新增 verb，不是纯复用。
- **具体修法（不砍任务）**：
  1. v3 把 4.3c 从"复用现有弹窗"改成"**新增 `skill_candidate_proposed`/`skill_candidate_confirm` WS verb，前端新增确认卡组件（可复用 plan-confirm 卡的视觉样式，但逻辑通道是新的）**"——诚实标注这是新接线不是纯复用。
  2. 后端复用 `_PLAN_CONFIRM_WAITERS` 同款 Future-await 模式（按 candidate_id key），不阻塞 WS recv loop。
  3. 前端改动 blueprint 已标"交接前端"（`03:241` 同款），但要在 §8 G8 关联里写清前端工作量不可省。
- **影响 WI**：WI-4.3c。

### B-8 🟡 WI-3.2：人格注入 bucket=dynamic 与 prompt cache + ComponentContext 注入路径

- **问题**：blueprint（`03:139,156`）PreferenceProfileComponent bucket=dynamic、priority=85、注入 facts_store。要核 ComponentContext / build_default_assembler 真有这些挂点。
- **证据**：`_facts_store`（`main.py:848`）存在可注入；blueprint 自己核了 `build_default_assembler`（`__init__.py:95`）、`registry.py fanout`（`:59`）、`bundle.py` priority 注释（`:78`）——这些引用看起来是真核过的（行号具体）。本轮我未逐一二次核 assembler 内部，但交叉证据（facts_store 可达、fanout 异常兜底存在）支持可行。
- **具体修法**：保持 blueprint 方案，v3 仅补一句"priority=85 介于 persona(90)/skill(70) 之间"要确认 bundle.py 的 BudgetAllocator 真按 priority 裁（blueprint `04:134` 提到 skill bucket 现在是"整块丢弃"，说明裁剪粒度是 bucket 级不是 priority 级——3.2 的 priority=85 能否保 dynamic 块不被整块丢，要核 BudgetAllocator 实现）。
- **影响 WI**：WI-3.2、WI-4.1（同样依赖 priority 裁剪语义）。**新增子任务见 §2-T3**。

### B-9 🟡 WI-1.3：compress 加 goal_text 参数 + 两处压缩器一致性

- **问题**：`compress(self, messages)`（`context_compressor.py:135`）单参，WI-1.3 要加 `goal_text` kwarg、WI-4.0 要加 `preserve_goal_text`/`preserve_pending_tasks`——**两个 WI 改同一个 compress 签名**，要协调成一套参数，否则后做的覆盖先做的。
- **证据**：blueprint 1.3（`01:243`）写 `compress(messages, *, goal_text=None)`；blueprint 4.0（`04:49-54`）写 `compress(working_messages, preserve_goal_text=..., preserve_pending_tasks=...)`。**两个不同参数名指同一意图**——这是跨 blueprint 契约漂移，正中 `feedback_cross_layer_contract`。
- **具体修法（不砍任务）**：v3 在 §6 或 §8 钉死 compress 最终签名一次：`async def compress(self, messages, *, goal_text=None, pending_tasks=None)`，1.3 和 4.0 都用这一套；history_compactor 的 `compact_messages` 同步对齐。**新增子任务见 §2-T4**。
- **影响 WI**：WI-1.3、WI-4.0（强协调）。

### B-10 🟡 WI-3.1：`_EXTRACT_PROMPT` 双版本 + goal_store 双写钩的 import 环风险

- **问题**：blueprint（`03:83`）`goal_store.set()` 成功后触发 `on_goal_set` → 调 `FactExtractor.upsert`。goal_store 当前**零依赖**（`goal_store.py` 只 import time/dataclass）。引入 facts 依赖可能造 import 环（agent ← memory ← agent）。
- **证据**：`goal_store.py` 在 `deskpet/agent/`，facts 在 `deskpet/memory/`。直接 import 会让 agent 包依赖 memory 包。
- **具体修法（不砍任务）**：用**回调注入**而非直接 import——`SessionGoalStore.bind_persistence` 时一并 `bind_on_goal_set(callable)`，callable 在 main.py 接电时闭包捕获 `_facts_store.upsert`。goal_store 本身不 import facts，保持解耦（blueprint `03:84` 说"单向事件钩"方向对，但要明确用注入 callable 而非 import）。
- **影响 WI**：WI-3.1、WI-1.1（bind 时一并接钩）。

---

## 2. 建议新增 / 拆分的子任务（不砍任务，只补细）

- **§2-T1（补 WI-1.5 / WI-1.1）**：新增子任务"`increment_iteration` 落库"——agent_loop `:1091` 每次 `increment_iteration` 后调一次 async `_persist_iterations(sid)`，否则重启 iterations_used 仍归零，blueprint `01:106` 声称的修复不完整。
- **§2-T2（补 WI-4.1）**：新增子任务"embedder.encode 异步化包裹"——若 `_embedder.encode` 同步 CPU 密集，SkillMatcher query embedding 走 `await asyncio.to_thread(...)`，防阻塞 event loop。先 spike 确认 encode 是否已 async。
- **§2-T3（补 WI-3.2 / WI-4.1）**：新增前置 spike"BudgetAllocator 裁剪粒度核查"——确认 priority=85 的 dynamic 块在预算溢出时是否被整块丢（blueprint `04:134` 暗示 skill 是 bucket 级整块丢）。若是 bucket 级，3.2 的 priority 保护要改成"独立 bucket + 不可裁标记"。
- **§2-T4（补 §6 / §8）**：新增"compress/compact 统一签名冻结"条目——在并行前钉死 `compress(messages, *, goal_text=None, pending_tasks=None)`，1.3 与 4.0 共用，写进 §6 契约的注入格式旁。
- **§2-T5（补 WI-1.2）**：新增前置 spike"子 agent 进程模型核查"——Read `spawn_team.py` 确认 teammate 是同进程协程还是子进程，决定 goal_tasks claim 走 `_write_lock` 串行还是 `BEGIN IMMEDIATE`。这是 B-1 的前提，必须在 1.2 开工前 1 天完成。
- **§2-T6（补 WI-2.2）**：新增"ephemeral mock 异步化回归"子任务——把现有 verify_gate 测试里所有 sync mock ephemeral 改 AsyncMock，纳入 2.2 的 build order 第 1 步（防 `await bool` 报错）。
- **§2-T7（补 WI-4.3c）**：新增"skill_candidate WS verb + 前端确认卡"子任务（明确为新接线，非纯复用 plan-confirm），含后端 Future-await + 前端组件 + WS 白名单。

---

## 3. 建议在 v3 plan 里改的具体条目（可直接套用的修订文字）

**改 §2 WI-1.2 行（"复用 TeamStore 原子 claim"）→ 改成：**
> WI-1.2：`goal_tasks` 落共享 state.db，claim 调度**复用 SessionDB 既有 `_write_lock`+`_with_retry` 串行原语**（非 TeamStore 的 per-team `BEGIN IMMEDIATE`——两者并发模型不同：TeamStore 用独立 db 文件，SessionDB 用进程内 asyncio.Lock）。**前置 spike §2-T5：先核 teammate 是否同进程**——同进程则 `_write_lock` 串行 claim 即原子；跨进程则 goal_tasks claim 显式 `BEGIN IMMEDIATE`。`TaskList/TaskUpdate` 通过**修改 `build_teammate_tools` 函数体新增 triple**（非"加进去"——该函数现 return 写死 5 元组）暴露给子 agent，与现有 team_task_* 工具并存。

**改 §3 WI-2.2 风险句 → 改成：**
> `ephemeral_subagent` sync→async：**只改 `consult_ephemeral_subagent`（verify_gate.py:394）为 async**，`check`（:284）保持 sync（goal_text/objective_evidence 均内存读）。callable 类型 `Callable[[Any],bool]`→`Callable[[Any],Awaitable[bool]]`，agent_loop:992 加 await。**所有现有 ephemeral mock 改 AsyncMock（§2-T6），否则 `await bool` 报错**。

**改 §6.2 → 补一句一致性窗口：**
> `get_goal_text` 永读内存（最新权威），库为 durability 备份。一致性窗口：set 后到 `_persist` await 完成前进程崩溃则该 goal 丢（单机桌宠可接受）。`_persist` 失败 safe-fail 不抛。**`increment_iteration` 也须落库（§2-T1），否则 resume iterations 归零。**

**改 §7 → 补 auto_resume attempt 计数粒度：**
> 明确 `AutoResumeOrchestrator` attempt 计数是 per-session（auto_resume.py:167）——verify_exhausted 与 max_iterations/circuit_open **共享同一 max_attempts=2 额度**（若确认 per-session）。死循环上界单测断言总 LLM 调用 ≤ 明确上界。`verify_exhausted` 需 ADD 进 `_AUTO_RESUME_TRIGGER_REASONS` frozenset（:96）。

**改 §8 依赖图 → 补一条强协调边：**
> 新增边：**WI-1.3 × WI-4.0 改同一个 `compress` 签名**——并行前先冻结 `compress(messages, *, goal_text=None, pending_tasks=None)`（§2-T4），两 WI 共用，禁各加各的参数名（防 `feedback_cross_layer_contract`）。

**改 §5 WI-4.3c → 诚实改写复用措辞：**
> 4.3c：**新增** `skill_candidate_proposed`/`skill_candidate_confirm` WS verb + 前端新确认卡组件（可复用 plan-confirm 卡视觉，但通道是新建，非纯复用——现 `_PLAN_CONFIRM_WAITERS` 是 code 模式 plan 专用）。后端复用 Future-await 模式（按 candidate_id key）。

---

## 4. 诚实标注：哪些 WI 的"真机 E2E"实际只能后端单测（但任务照做）

> 原则：任务全做，但对"真模拟人点击能产出什么证据"诚实分级。许多 WI 的核心逻辑在后端，windows-mcp 只能验"最终用户可感的表层结果"，验不到"内部三层重试/分支合流真走对了"——那部分必须后端单测，且不许借此砍真机 E2E。

| WI | blueprint 标级 | 真机 E2E 实际能验到的 | 只能后端单测的部分（仍要做） |
|---|---|---|---|
| **1.1** | 🔴 真机(重启) | ✅ 真能：设目标→taskkill→重启→`/goal` 查仍在，截图+log grep `load_persisted`——这是干净的真机证据 | iterations_used 落库恢复（§2-T1）只能单测断言 |
| **1.2** | 🔴 真机(多步可查进度) | 🟡 部分：截图 TodoPanel 进度变化可见；但"两 agent 并发 claim 不双占"**只能后端单测**（pass^k=5 并发竞争无法靠点击复现） | 原子 claim 竞争、DAG ready 跳过、环检测——全后端单测 |
| **1.3** | 🔴 真机+pass^k(防漂移) | ✅ 真能：长对话顶过压缩阈值→截图 LLM 仍回原目标——这是 1.3 最有价值的真机证据 | GD_actions/inaction 计数、`_partition` 保留 system——单测 |
| **1.4** | 🟠 真机 | 🟡 弱：子 agent prompt 含 Parent Goal 只能 **log grep**（charter 在后端拼，UI 看不到）；off-goal 拦截也是 log——诚实说这条"真机"≈ log 验证，非纯点击 | charter 注入 BC、回收过滤——单测 |
| **1.5** | 🟠 真机 | 🟡 弱：resume 续目标主要靠 **log grep `_is_goal_anchor`**，UI 层难直接看出"续的是原目标" | getter None BC——单测 |
| **2.1** | 🟢 后端单测 | （blueprint 已诚实标 🟢）—— 结构化字段解析纯后端 | parse_reflection 三形态、flag off 不注入——单测（对） |
| **2.2** | 🔴 真机+pass^k | ✅ 真能：伪完成 PPT→verify 拦→自动重规划→二次真生成 .pptx→ArtifactCard 渲染，截图链完整 | 三层重试上界、difflib 复述检测、ephemeral 救援——单测 |
| **2.3** | 🔴 真机+pass^k | 🟡 部分：伪完成被拦/真完成放行可截图；但"人格不泄漏进判定"`no_persona_leak` **只能后端单测**（注入诱导上下文断言判定不变，点击复现不了） | 两分支合流、objective_evidence 白名单、未来时不误判——单测 |
| **2.4** | 🟠 真机 | 🟡 部分：高后果 revise→重试可截图；`is_high_consequence_goal` 各分支命中**后端单测**；"普通目标不调 evaluator"断言 llm_call=0 也只能单测 | 高后果判定、provider=None safe-fail——单测 |
| **3.1** | 🔴 真机(跨会话召回) | ✅ 真能：会话 A 说决策→重启/新 session→会话 B 答出——干净真机证据 | 双写钩、scope 列、flag off 旧 prompt——单测 |
| **3.2** | 🟠 真机(改偏好下轮反映) | ✅ 真能：改偏好→下轮 LLM 反映，截图+prompt log | 渲染无粘性措辞黑名单、空 Slice——单测 |
| **3.3** | 🟢+真机(Forget) | 🟡：Pin 不衰减**主要后端单测**（要模拟时间推进 / 多次 daily_decay，真机难推进时间）；Forget 闭环可真机 | daily_decay 调度接通(§3.3-bug)、ALTER 守护——单测 |
| **3.4** | 🟢 后端单测 | （blueprint 诚实标 🟢）—— queued_total 不随 tick 增长靠 log/stats，非纯点击 | skip_embed hook 不触发、FTS 仍可召回——单测（对） |
| **4.0** | 🔴 真机+全回归 | 🟡：长对话触发压缩+压缩后追问"最初目标"答对可截图；但"skill_prelude system 块存活""re-anchor 去重"**只能后端单测** | should_compress 接电、保留项注入、marker 去重——单测 |
| **4.1** | 🟠 真机 | 🟡：说触发某 skill 的话→该 skill 正文自动载靠 **log grep `skill_auto_loaded`**，UI 看不出"正文进了 prelude" | 相似度阈值、usage tie-break、embedder=None 降级——单测 |
| **4.2** | 🟢 后端单测 | （blueprint 诚实标 🟢）—— 重挂块存活靠 log | 25K 预算、单份不重复——单测（对） |
| **4.3** | 🔴 真机 | ✅ 真能：多步目标→候选确认卡弹出→点保存→SKILL.md 落盘→新会话一键复用，这是最完整的真机链（含真坐标点击确认卡） | 触发器各分支、降级路径、拒绝删 pending——单测 |

**诚实总结**：标 🔴 真机的 WI 里，**1.1 / 1.3 / 2.2 / 3.1 / 4.3** 能产出干净的"真模拟人"证据（设目标/长对话/伪完成/跨会话/确认卡点击都是用户可感行为）；**1.4 / 1.5 / 4.0 / 4.1** 的"真机"实质偏向 **log grep**（核心行为在后端 prompt 拼装层，UI 表层看不全）——这几条要在 §11 诚实标注"真机=log 验证为主"，避免子代理拿 log 当"模拟人点击"交差（违反 HARD CONSTRAINT 的精神）。所有"只能后端单测"的部分**照做不砍**，只是不计入"真模拟人"证据。

---

*报告路径：`G:\projects\deskpet\plans\2026-06-04-goal-completion-upgrade\06-challenge-round1.md`*
*核实基础：实地 Read goal_store.py / teammate_tools.py / team_store.py / session_db.py(grep 锁) / context_compressor.py / verify_gate.py / agent_loop.py(955-1123,585-668) / facts.py(grep) / schema_v2_migrator.py(grep) / auto_resume.py(grep) / main.py(601-640,804-848,grep daily_decay/embedder/ephemeral) / config.py(grep flags)。*
