# Round2 运行时/集成/上线视角挑战

> 视角：负责把这套改动**真跑起来 + 跨运行栈集成 + 向团队证明 prod 真能用**的资深集成/运维工程师。
> 方法：专打 **Round1 静态符号审查抓不到的「运行时 / 集成 / 上线」层问题**——启动时序、预算实际裁剪行为、LLM 规模化失败、respawn 重连竞态、DB 迁移、pass^k 真机自动化、shadow 可观测性、分阶段独立可跑性、本地资源。
> 硬约束：**不砍任何任务**，只把任务拆细 / 补前置 / 加验收锚点。
> 核实日期：2026-06-04 ｜ 与 Round1（06）分工：06 打静态层（符号真实性/签名涟漪/锁原语/import 环），本文打**运行时层**，不重复。
> 核实的真实文件见每条「证据 file:line」。

---

## 0. 总判：v3 真跑起来最可能在哪炸？最被低估的运行时风险？

**结论：blueprint 的「写得出来」Round1 已基本背书；但「跑起来 + 上线」层有 3 个被低估的硬伤，全部是「单测全绿、真机第一次跑就翻车」型，必须在阶段 A 前补前置。**

最被低估的 3 个运行时风险（详见 §1）：

1. **「system 栈尾多注入源」根本没有共享预算闸——而且分属两套互不知情的预算系统。**
   - 真相：assembler 的 `BudgetAllocator.allocate`（`budget.py:97-109`）是**二值整块丢**（`priority < 80` 直接 `_zero_out` 整块），只有 `component_name=="memory"` 永不丢；而 1.3 re-anchor / 4.0 保留项 / 2.3 goal 对照走的是**另一条路**——它们作为 `role=system` 注进 `working_messages`，由 `context_compressor._partition`（`:253-255`）**无条件全保留、零预算上限**。两套预算系统对「system 栈尾」各管各的，谁都不给总账。同时点亮 4 源，compressor 路径会让 system 段无限膨胀（吃掉 last_n 给 LLM 的有效窗口），assembler 路径则会把 priority=85 人格保住、却把 priority=70 skill 正文**整块丢**——`re-anchor` 与 `4.0 保留` 都不是 Slice，不参与这个裁剪，去重 marker 防不了「跨两套系统重复」。**这是 v3 §6.3「统一注入 system 栈尾」最大的运行时盲区。**

2. **`session_goal_store` 在 lifespan 里只 `_GS()` 构造、从不 `load_persisted`——「重启仍在」这条 1.1 头号真机证据当前根本接不通。**
   - 真相：`main.py:1067-1080` 现在是 `_session_goal_store = _GS()` 然后 `register`，**没有任何 `load_persisted` 调用**（对比 code_mode 在 `:1466` 有显式 `load_persisted`）。1.1 blueprint 默认「重启后内存从库恢复」，但没人接这一步进 lifespan，且接的位置有时序硬约束（必须在 `_sdb.initialize()` 之后、且 `on_goal_set` 钩绑定之后、且 auto_resume redispatcher 注册之前）。漏接 = 1.1 的 🔴 真机「设目标→重启→仍在」直接 FAIL。

3. **respawn 后 auto_resume 与 control WS 重连存在竞态——goal 从库恢复了，但 redispatcher/WS 还没注册，auto_resume 静默 `return` 吞掉。**
   - 真相：auto_resume 触发时取 `_auto_resume_redispatchers.get(_sid)` + `_control_connections.get(_sid)`（`main.py:2076-2083`），二者都是**进程内内存 dict**，respawn 后为空，要等控制 WS 重连并重新注册才填上。1.5 resume「续原目标」若在 WS 重连前触发，命中 `auto_resume_no_redispatcher` 分支直接 `return`——goal 恢复了却不续跑。这正是项目已踩过的 `control_ws 快照`坑的同构变体（audio 先于 control 重连）。

---

## 1. 运行时/集成阻塞点（逐条：问题 | 证据/推演 | 具体修法 | 影响 WI）

### R-1 🔴 system 栈尾「多注入源」无共享预算 + 跨两套预算系统

- **问题**：1.3 re-anchor goal / 3.2 人格画像 / 4.1 skill 正文 / 4.0 压缩保留项 全声称注入「system 栈尾」。同时开会不会互相挤爆 token？谁先牺牲？
- **证据/推演**：
  - **assembler 路径**（人格 3.2 / skill 4.1）走 `BudgetAllocator.allocate`：`budget.py:97-109` 按 `priority` 升序，`priority < 80` 的 slice **整块 `_zero_out`**（非按比例截断）；`:99-101` 只有 `component_name=="memory"` 豁免（只缩不丢）。persona=90（`persona.py:77`，bucket=frozen）、workspace=45、skill blueprint 拟 70。→ **skill 正文 priority<80 在预算紧张时被整块丢**，人格 priority=85 保得住，但「dynamic vs frozen」bucket 不影响丢弃判定（判定只看 priority 和 component_name=="memory"）。Round1 T3 的疑虑这里坐实：**裁剪是 priority 阈值级整块丢，不是按 priority 比例缩**。
  - **compressor 路径**（1.3 re-anchor / 4.0 保留 / 2.3 对照）走 `context_compressor._partition`：`:253-255` `system_msgs = [m for m in messages if role=="system"]` **全量保留、无预算上限**，`compress` 只压 middle 非 system 段。→ 注进 working_messages 的 system 消息**不受任何预算裁剪**，但也**不计入 assembler 的 Slice 预算**——它们直接吃 LLM 上下文窗口里 last_n 之外的部分。
  - **两套系统互不知情**：assembler 算 `context_window=32_000, budget_ratio=0.6`（`main.py:1114-1115`）≈19.2K 给 component slices；compressor 另有自己的 `threshold_tokens`。同一份 goal_text 可能既被 assembler 当 component 注入、又被 compressor 当 system 保留——**§6.3 的「同 marker 去重」只在单条路径内生效，跨两套系统重复注入它防不住**。
- **具体修法（不砍任务）**：
  1. **§6.3 注入必须明确「走哪条路」**：re-anchor（1.3）/ 4.0 保留 / 2.3 对照是**注入 `working_messages` 的 `role=system` 消息**（compressor 路径，活过压缩）；人格（3.2）/ skill（4.1）是**assembler Slice**（assembler 预算路径）。两条路 v3 现在混在「system 栈尾」一句话里 → 拆成两节写清，各自的预算闸不同。
  2. **给 compressor system 段加软上限**：新增 `max_system_inject_tokens`（建议 1500）——re-anchor + pending tasks + goal 对照三者注入前先算总 token，超限按固定优先级裁（goal_text > current subgoal > pending tasks 摘要 > GD 信号）。否则长会话叠多源 system 会单调膨胀吃掉 last_n。
  3. **跨系统去重升级**：§6.3 marker 去重要求「同一 goal_text 只允许出现在 compressor 路径或 assembler 路径之一」——人格 component 不重复带 goal_text，goal 只由 compressor system 段权威注入；assembler 侧只注入「人格风格 + skill 正文」不碰 goal。
  4. **给一张「多注入源预算与优先级总账」**（见 §2 表）作为并行前冻结物。
- **影响 WI**：1.3、3.2、4.0、4.1、2.3；§6.3 契约。

### R-2 🔴 lifespan 启动时序：goal_store 无 load_persisted + 钩绑定/auto_resume 注册顺序未定

- **问题**：goal_store.bind_persistence / on_goal_set 钩 / daily_decay 调度 / embedder 加载 / SkillMatcher 预算 embedding 的先后对不对？有没有「A 没 ready 就被 B 用」？
- **证据/推演**：
  - **goal_store 当前 0 步恢复**：`main.py:1067-1080` 只 `_GS()` + `register`，无 `load_persisted`/`bind_persistence`/`bind_on_goal_set` 任何调用（对比 `code_mode.load_persisted` 在 lifespan `:1466` 显式调）。1.1 的「重启恢复」要新接一段进 lifespan，且依赖 `_sdb.initialize()`（`:1455`）已完成。
  - **embedder 是 fire-and-forget warmup**：`:1498` `asyncio.create_task(_embedder_warmup_bg())`，**不 await**。`skill_loader.start()` 在 `:1483`（**先于** embedder warmup）。→ 若 SkillMatcher（4.1）在 loader.reload 后立刻批量算 skill description embedding，此刻 embedder 模型**可能还没 warmup 完**（`_emb.warmup()` 是后台 task）。Round1 T2 的「encode 异步化」只解决阻塞，没解决「模型未 ready」——4.1 blueprint 已写「embedder 不可用降级纯 desc」，但**降级判定必须用 `_emb.is_ready()`/`is_mock()` 而非 try-except 兜空向量**，否则 warmup 未完时算出的 embedding 是 mock 向量、warmup 完后又不会重算 → skill 匹配长期用错向量。
  - **on_goal_set 钩接电时机**：3.1 用注入 callback（Round1 B-10 定）。callback 闭包捕获 `_facts_store.upsert`，而 `_facts_store` 在 `:848` 构造（lifespan 之前的 build 段）。钩必须在 `_session_goal_store` 构造后、且 facts_store 就绪后绑定。但**用户可能在 onboarding 后立刻 `/goal set`**——若钩还没绑，第一条 goal 不进 facts 投影（跨会话召回漏第一个目标）。
  - **daily_decay 调度（3.3 bug 修复）**：要新增一个后台 task（仿 `_reflection_loop` `:1604`）。它依赖 `_facts_store` 就绪 + `pinned` 列 ALTER 成功（schema_v2_migrator）。调度若在 ALTER 失败时仍跑 `daily_decay() ... AND pinned=0` → SQL `no such column: pinned` 每日报错。
- **具体修法（不砍任务）**：
  1. **写死 lifespan 接电顺序**（新增子任务，见 §7）：`_sdb.initialize()` → `schema_v2_migrator`(含 pinned) → `_facts_store` 就绪 → `_session_goal_store.bind_persistence(_sdb)` + `load_persisted()` → `bind_on_goal_set(_facts_store.upsert)` → 注册 auto_resume redispatcher → **最后**调度 `daily_decay_loop`。
  2. **goal_store load_persisted 必须 await**（不能 fire-and-forget）：否则 control WS 可能在恢复完成前连上、读到空 goal。但 await 不能阻塞太久 → load 只灌内存 dict，I/O 轻量，可接受同步 await。
  3. **SkillMatcher embedding 用 `is_ready()` 门控 + warmup 完成后回调重算**：4.1 注册一个「embedder ready」回调（或在 warmup_bg 末尾触发 skill embedding 预算），不在 loader.start 时立刻算。
  4. **daily_decay 调度前先查 `alter_failures()`**：`pinned` ALTER 失败则 `daily_decay` 走不带 `AND pinned=0` 的旧 SQL（pin 功能降级但不报错），并告警。
- **影响 WI**：1.1、1.5、3.1、3.3、4.1；lifespan。

### R-3 🔴 relay(gpt-5.5) 规模化失败：LLM 依赖点降级行为未定义

- **问题**：2.1 反思 / 2.4 evaluator / 1.4 子输出过滤 / 2.3 GoalChecker 靠 LLM 产 JSON 或判定。真实负载下畸形/超时/限流/空响应概率不低。降级后行为定义清楚没？
- **证据/推演**：
  - `context_compressor.compress` 已示范了好的降级：LLM 失败/空响应 → **返回原 messages、`compressed=False`、不抛**（`:182-204`）。这是该跟的范式。
  - 但 2.x/1.4 新增的 LLM 点**降级语义会改变「完成判定」**，不能简单「失败=放行」也不能「失败=死拦」：
    - **2.3 GoalChecker 失败**：若「LLM 超时=判定通过」→ 谄媚式放行（违反红线2）；若「超时=拦」→ relay 抖动时用户永远完不成目标。必须定第三态：**LLM 不可用 → 降级到「仅 VerifyGate+outcome_verifier 客观证据」判定，goal 对照标记 `goal_check=skipped` 记进 receipt**，而非默认 pass/fail。
    - **2.1 反思解析失败**：2.2 重试还走不走？若反思 JSON 畸形 → 应**降级为现有机械 nudge**（保底重试），而非中断重试链。
    - **2.4 evaluator 超时**：高后果目标的交叉验证超时 → **算「未通过交叉验证」拦住 + 提示用户手动确认**（高后果场景宁可保守），不能默认通过。
    - **1.4 子输出按目标过滤**：LLM judge 失败 → 降级为「不过滤，全回收 + 标记 unfiltered」，不能丢子输出。
- **具体修法（不砍任务）**：给**每个 LLM 依赖点一张「失败降级矩阵」**（见 §3 表），并要求每个降级路径：① 不抛冒泡；② 把降级事实写进 receipt/metrics（`goal_check=skipped` / `reflection=fallback_nudge` / `evaluator=timeout_hold`），让 shadow 期能量化「LLM 不可靠导致多少判定降级」。pass^k 必含「relay 注入故障（超时/畸形 JSON/空响应）下行为符合矩阵」的故障注入用例。
- **影响 WI**：2.1、2.2、2.3、2.4、1.4。

### R-4 🔴 backend respawn / 重连：goal_store 内存态 + auto_resume redispatcher 竞态

- **问题**：本项目踩过 `control_ws 快照`坑（audio 先于 control 重连致广播被挡）。新增 goal_store(内存态)/verify 状态在 respawn 后怎样？auto_resume 续跑 goal_text 从库恢复时序 vs ws 重连时序有无竞态？
- **证据/推演**：
  - auto_resume 触发取 `_auto_resume_redispatchers.get(_sid)` + `_control_connections.get(_sid) or .get("default")`（`:2076-2077`），缺任一则 `auto_resume_no_redispatcher` + `return`（`:2079-2083`）——**静默吞掉**。
  - respawn 后这俩 dict 为空，要等控制 WS 重连 + 重新注册 redispatcher 才填。**1.5 resume**「respawn 时注入 goal_text 续跑」若在 WS 重连前触发 → 命中吞掉分支，goal 恢复了但不续。
  - verify 状态（verify_nudges 计数、ephemeral 救援进度）是 **loop 内局部变量**，respawn 必然丢——这本身可接受（重新计数），但 §7 账本的「总上界」断言要明确「respawn 后计数清零是预期，不算违反上界」。
  - goal_store 内存态 respawn 丢，靠 R-2 的 `load_persisted` 恢复——但 §6.2 一致性窗口（set 后到 _persist 完成前崩则丢）意味着 respawn 前最后一个 goal 可能没落库。
- **具体修法（不砍任务）**：
  1. **auto_resume 续跑改成「等 WS 重连就绪再 fire」**：respawn 后若 goal 恢复但 redispatcher 未注册，不要 `return` 丢弃，而是**入一个 pending-resume 队列**（按 sid），控制 WS 注册 redispatcher 时 drain 该队列。复用项目已有的 control_ws 快照修复思路（control 先于 audio 就绪）。
  2. **1.5 resume 注入点放在「控制 WS 注册 redispatcher 之后」**，不在 lifespan startup 直接 fire。
  3. **§7 账本明确 respawn 语义**：verify 计数 respawn 清零属预期；auto_resume `max_attempts` 是否跨 respawn 持久（看 `auto_resume.py:148-180` 计数 key——Round1 已标 per-session）→ 若 per-session 且 session 跨 respawn 复用，attempt 计数会从库恢复还是清零，必须在 1.5 spike 里定，否则重启可绕过 max_attempts 无限重试。
- **影响 WI**：1.5、2.2、§7 账本、§6.2。

### R-4b 🟠 flag-OFF 字节级契约的运行时真验证（非 spec 文字）

- **问题**：多处声称「flag-OFF 字节不变」，怎么运行时真验证？
- **证据/推演**：现有 flag 默认值已核：`goal_mode=False`（`config.py:347`）、`preference_memory=False`（`:350`）、`verify_gate_mode="off"`（`:250`）。flag-OFF 时 store/checker=None（`main.py:1067-1078`），BC 路径存在。但「字节不变」目前只有 spec 文字断言，无运行时基线。
- **具体修法（不砍任务）**：新增 `scripts/e2e_flag_off_baseline.py`（接 §11 跨层契约兜底）：① flag 全 OFF 跑一遍冷启动 + 3 轮对话，`sqlite3 .dump` 出 state.db 关键表（messages/facts/goal_tasks 若不该建则断言**表不存在**）的规范化快照（去时间戳）；② 全 OFF 时断言 `goal_tasks`/`skill_candidates` 等新表**根本没被 CREATE**（Strangler-Fig：flag 关连表都不建，仿 workspace_memory `main.py:1092` 的「flag 关表不建」先例）；③ 对比基线 hash。这是**运行时字节级回归**，比 spec 文字硬。每个 Phase 合并前跑一次。
- **影响 WI**：全部带 flag 的 WI；§11 验收。

### R-5 🟠 DB 迁移 / 老用户首启：两套迁移系统 + pinned ALTER 时机 + 多 worktree 共享 db

- **问题**：3.3 facts 加 `pinned`（ALTER）、1.1/1.2 新表。老用户首升真实路径、失败回滚、半完成迁移、多 worktree 共享 db 打架？
- **证据/推演**：
  - **两套迁移系统并存**：① `memory/migrator.py::run_migrations`（numbered SQL + `user_version`，`:177`，conversation DB）；② `schema_v2_migrator.py`（additive ALTER + introspection，**不 bump user_version**，`:6-9`，memory-v2 facts）。3.3 `pinned` 走 ②（正确，idempotent + safe-fail，`:51-52,66`）。
  - **1.1/1.2 新表**（goal/goal_tasks）按 §2 注释「`CREATE TABLE IF NOT EXISTS` + `bind_persistence`，不写 migration 不 bump user_version」——与 ② 同风格，老用户首启自动建表，幂等安全。✅ 这条 blueprint 选型对。
  - **半完成迁移风险低**：ALTER 单列、`CREATE IF NOT EXISTS` 单表，无多步事务，半完成可能性低；schema_v2_migrator 单列失败只关该列依赖 flag（`:46`），不连坐。
  - **多 worktree 共享 db 才是真坑**：CLAUDE.md 拓扑里 main/memory-upgrade/tool-last-mile 各有端口隔离，但**若它们共享同一 `DESKPET_USER_DATA_DIR`** → 一个 worktree 跑了 `pinned` ALTER，另一个老代码读到带 `pinned` 列的表通常无害（SELECT *），但**新 worktree CREATE 了 goal_tasks，旧 worktree 的代码不认识也不会删**——主要风险是开发期串味，不是生产。
- **具体修法（不砍任务）**：
  1. **3.3 pinned ALTER 接进 schema_v2_migrator 的列清单**（`_DDL` 旁），lifespan 在 facts_store 就绪前跑一次，结果喂 `daily_decay` 调度门控（见 R-2）。
  2. **新表建表语句走 bind_persistence 内 `CREATE IF NOT EXISTS`**，与现有 sidecar 惯例一致，**不碰 user_version**（避免和 conversation DB 的 numbered migration 体系打架）。
  3. **老用户首启验收锚点**：拿一个 beta-0.5 的真实老 state.db（无 goal/pinned）→ 升级版冷启动 → 断言 ① `pinned` 列 ALTER OK、② goal_tasks 表建出、③ 老 facts/messages 数据 0 丢失、④ flag-OFF 时这些**都不发生**（接 R-4b）。
  4. **多 worktree 开发纪律**：每个 worktree 用**独立 `DESKPET_USER_DATA_DIR`**（不止端口隔离），写进 CLAUDE.md 拓扑表，防迁移串味。
- **影响 WI**：3.3、1.1、1.2；CLAUDE.md 拓扑。

### R-6 🔴 pass^k 真机自动化现实：复用 _cdp.py + _send.py 可脚本化循环

- **问题**：1.1/1.3/2.2/2.3/4.3 pass^k(k≥3) 真机在 windows-mcp/CDP 上实际怎么自动化？一轮多久？哪些只能后端跑（诚实标）？
- **证据/推演**：项目已有干净 harness：
  - `testcase/_cdp.py`：CDP 9333 `locate`（读元素物理坐标）/`eval`（页内 JS）/`shot`（截图）/`text`（抓 transcript innerText 末 1500 字）。
  - `testcase/_send.py`：locate textarea + 调 `deskpet-input.ps1 -Action send` 做**真 OS click+paste+click（SendInput）**——这就是 CLAUDE.md「圣杯」SendInput 路径，满足 HARD CONSTRAINT「真模拟人」。
  - → **pass^k 可写成一个 Python 驱动循环**：`for k in range(K): _send.py companion "<目标话>" → 轮询 _cdp.py text 抓回复 → _cdp.py shot 存截图 → 判 PASS/FAIL`。每轮真 SendInput 输入，不是脚本回放（回放的是「驱动」，实际出站是真 OS 事件 + 真 LLM 链路），不违反 `feedback_real_e2e_not_script_replay`。
- **具体修法（不砍任务）**：见 §5 落地方案表。诚实标：**1.1/1.3/2.2/3.1/4.3 可真机 pass^k**（设目标/长对话/伪完成/跨会话/确认卡点击都是用户可感行为，_send+_cdp 能驱动）；**2.3 的 no_persona_leak、1.2 的并发 claim、§7 死循环上界**只能后端 pytest pass^k（点击复现不了并发竞争/注入诱导）——照做不砍，只是不计入「真模拟人」证据。
- **影响 WI**：1.1、1.3、2.2、2.3、4.3；§11 验收。

### R-7 🔴 shadow 上线可观测性：采集什么判没误杀/没漏放 + go/no-go 量化

- **问题**：P0-2 后 verify prod off→shadow。shadow 采集什么指标判断没误杀/没漏放？翻 strict 的量化标准？现有 metrics/receipt 够吗？
- **证据/推演**：现有基建：`verify_gate_mode` off|shadow|strict（`config.py:250`）、INVARIANT-1「!=off 必须 emit_receipts」（`:481`）、`/metrics/event`（`main.py:2942`）、receipt store。shadow 模式语义已存在（不拦只记）。**但 shadow 要判「翻 strict 安不安全」需要的是「shadow 判定 vs 实际结果」的对照标签**——现有 receipt 记了 verify 判定，但没记「用户事后是否认为真完成了」（ground truth），无法直接算误杀率/漏放率。
- **具体修法（不砍任务）**：见 §4 完整方案。核心：shadow 期 receipt 多记 `shadow_verdict`（strict 模式下会拦/会放）+ `actual_outcome`（用户后续是否 redo/抱怨/satisfied 的弱信号）；go/no-go 量化标准（建议）：连续 N≥200 个高后果判定中，**误杀率（shadow 会拦但实际真完成）< 2%** 且 **漏放率（shadow 会放但实际伪完成）< 5%**，且 LLM 降级率（R-3 矩阵的 `goal_check=skipped`）< 10%，才翻 strict。
- **影响 WI**：2.3、2.4、P0-2 上线；observability。

### R-8 🟠 跨 Phase 运行时集成顺序：每阶段结束系统真能独立跑吗

- **问题**：按 §12 阶段 A/B/C 增量交付，每阶段结束系统真能独立跑吗？有没有「goal_store 持久化了却无消费者=死代码」的半截集成？
- **证据/推演**：
  - **阶段 A 半截风险**：A 做 1.1（durable store）+ 1.6（路径录制）+ 3.1/3.3/3.4。1.1 落库了但 **1.3/2.3/1.4 消费者全在阶段 B**——A 结束时 goal_store 是「写进库没人读」的半截集成。1.6 ToolPath 录制了但**消费者 4.3 在阶段 C**——录了一堆没人用。这不算 bug，但「可观测收益」为零，违反「每阶段可独立运行+可观测收益」。
  - **阶段 B 依赖 A 的 load_persisted（R-2）必须在 A 完成**，否则 B 的 1.5 resume 读不到恢复的 goal。
- **具体修法（不砍任务）**：见 §6 每阶段验收锚点表。核心：每阶段给一个「即使下游未做也能观测到的收益」——A 阶段 goal_store 的收益锚点 = `/goal` 设目标→重启→`/goal` 查仍在（1.1 自己闭环可观测，不依赖下游消费者）；1.6 收益锚点 = 后端单测断言 `get_completed_path` 返回正确 ToolPath（不需 4.3）。把「无消费者」诚实标为「阶段 N 落地、阶段 N+1 接消费」，避免误判死代码。
- **影响 WI**：§12 排期；全 WI。

### R-9 🟠 本地资源 / 性能：常驻桌宠新增调度叠加

- **问题**：daily_decay 调度 / SkillMatcher 每轮 query embedding / re-anchor 每次压缩 / goal_tasks 频繁落库，对常驻内存/CPU/磁盘 IO 影响？
- **证据/推演**：
  - 现状已有多个常驻后台 task：embedder warmup、vector backfill（`:1587` sleep 60 后跑）、reflection loop（`:1604` 每 6h）、summarizer（`:1672`）。新增 daily_decay loop 是**低频**（每日一次），叠加成本可忽略。
  - **SkillMatcher 每轮 query embedding 是热路径成本**：每轮对话 assemble 都算一次 query embedding。若 `_embedder.encode` 同步 CPU 密集（Round1 T2 未确认），在 async loop 里同步 encode 会**每轮卡顿 event loop**（影响语音 tick 实时性）。BGE-M3 encode 单条短文本通常几十 ms，但桌宠常驻、每轮都算，叠加语音/截屏高频流会累积。
  - **goal_tasks 频繁落库**：1.2 子 agent 进度更新走 SessionDB `_write_lock` 串行（Round1 B-1），高频 update 会和现有 message 写抢同一把 `_write_lock` → 串行化下可能拖慢对话落库。
  - **re-anchor 每次压缩**：1.3 只在 `should_compress` 触发时跑（非每轮），频率低，成本可忽略。
- **具体修法（不砍任务）**：
  1. **SkillMatcher query embedding 必须 `await asyncio.to_thread`**（Round1 T2 落实）+ **加每轮缓存**：同一轮内 query 不变则复用，且对极短/无意义 query（如 "嗯"）跳过匹配。
  2. **goal_tasks update 批量化 / 降频**：子 agent 进度不必每个 tool call 都落库，可 debounce（如每 2s 或每完成一个子任务落一次），减轻 `_write_lock` 争用。
  3. **加一个常驻资源回归锚点**：阶段 C 后跑「桌宠空闲 30 min + 10 轮对话」，监控 RSS 内存增长 / CPU 平均占用，对比升级前基线，确认无内存泄漏（多个常驻 task + embedding 缓存若不释放易泄漏）。
- **影响 WI**：4.1、1.2、3.3；§11 性能回归。

---

## 2. ★ system 栈尾「多注入源」预算与优先级总账（一张表）

> 锁定前提：拆成**两条互不相同的预算路径**，禁再用「都注入 system 栈尾」一句话糊过去。

| 注入源 | 归属 WI | 走哪条路 | 预算系统 | 超预算时行为 | 优先级/裁剪序 |
|---|---|---|---|---|---|
| **goal_text re-anchor** | 1.3 | `role=system` 注 `working_messages` | compressor `_partition`（全保留，**新增软上限 1500**） | 永不被 compress 丢；超软上限时**最后牺牲**（最高保） | system 段第 1 顺位保 |
| **current subgoal** | 1.3 | 同上 | 同上 | goal_text 之后牺牲 | 第 2 顺位 |
| **pending tasks 摘要** | 4.0 | 同上（compress 保留项） | 同上 | subgoal 之后牺牲；与 1.3 **同 marker 去重** | 第 3 顺位 |
| **goal 对照证据** | 2.3 | 内存读，**不注 prompt**（喂 GoalChecker 输入） | 不占 prompt 预算 | N/A（判定输入，非上下文） | N/A |
| **GD 观测信号** | 1.3 | metrics 打点，**不注 prompt** | 不占预算 | N/A | N/A |
| **人格画像 PROFILE** | 3.2 | assembler Slice（priority=85, dynamic） | `BudgetAllocator`（≥80 不整块丢） | priority≥80 保住；**不带 goal_text**（避免跨系统重复） | assembler 内 persona(90)>persona_profile(85)>skill(70) |
| **skill 正文** | 4.1 | assembler Slice（priority=70） | `BudgetAllocator` | **priority<80 → 预算紧张整块 `_zero_out`**（`budget.py:103`） | assembler 内最先丢；溢出再按 usage_count 丢最久未用 |

**总账规则**：
1. **goal_text 唯一权威路径 = compressor system 段**（1.3 注入），3.2/4.1 的 assembler Slice **不重复携带 goal_text**——跨系统去重靠「单一注入源」而非 marker。
2. **compressor system 段加软上限 `max_system_inject_tokens=1500`**，超限按上表顺位裁，防长会话单调膨胀吃掉 last_n。
3. **assembler Slice 预算独立**：人格(85)保、skill(70)在溢出时整块丢（接受——skill 是增益非必需）；**3.2 不能依赖「dynamic bucket 保护」**（裁剪只看 priority + memory 豁免，bucket 不影响丢弃）。
4. 同时点亮 4 源时，两条路各自有界，互不挤兑——这是并行前必须冻结的「多注入源共存」契约。

---

## 3. ★ LLM 依赖点失败降级矩阵（每个 LLM 调用点：失败=拦/放/降级？）

> 范式：跟 `context_compressor.compress`（`:182-204`）——**失败不抛、返回安全降级值、记录降级事实**。新增点不得「失败=默认 pass」（谄媚）也不得「失败=死拦」（relay 抖动锁死用户）。

| LLM 调用点 | WI | 失败类型 | 降级行为（非拦非放的第三态） | 记录到哪（shadow 可量化） |
|---|---|---|---|---|
| **结构化反思生成** | 2.1 | 畸形 JSON/空/超时 | 降级为**现有机械 nudge**（保底重试不中断） | `reflection=fallback_nudge` → metrics |
| **真重规划重试** | 2.2 | replan LLM 失败 | 用上次 nudge 文本重试一次；仍失败→走 §7 账本下一层 | `replan=llm_fail_degraded` |
| **GoalChecker 目标对照** | 2.3 | 超时/畸形 | **降级到仅 VerifyGate+outcome_verifier 客观证据判定**，goal 对照标 `skipped`（**不默认 pass/fail**） | `goal_check=skipped` → receipt（shadow 算降级率） |
| **ephemeral 独立 verify** | 2.2/2.3 | provider=None/超时 | provider=None 时本就 safe-fail（Round1 B-3 已定）；超时算「未额外确认」不改主判定 | `ephemeral=unavailable` |
| **外部 evaluator 交叉验证** | 2.4 | 超时/限流 | **高后果场景保守=「未通过交叉验证」拦住 + 提示用户手动确认**（不默认通过） | `evaluator=timeout_hold` → receipt |
| **子输出按目标过滤** | 1.4 | judge LLM 失败 | **降级=不过滤、全回收 + 标 unfiltered**（不丢子输出） | `handoff_filter=unfiltered` |
| **context 压缩摘要** | 4.0/1.3 | 已有降级 | 返回原 messages、`compressed=False`（现成范式） | `compressed=False`（已有） |
| **skill 候选生成** | 4.3b | LLM 失败/空 | 不生成候选、不弹确认卡（静默跳过，下次再触发） | `skill_candidate=gen_failed` |

**横切要求**：① 每个降级路径必须 `try/except` 不冒泡（防 `_handle_goal` async 化后异常上抛 slash 处理）；② 降级事实进 metrics/receipt，使 shadow 期能回答「relay 不可靠导致多少判定降级」；③ pass^k 必含**故障注入用例**（mock LLM 返超时/畸形/空），断言每点走对降级分支。

---

## 4. shadow 上线可观测性方案（采集指标 + go/no-go 标准）

**背景**：P0-2 后 `verify_gate_mode` 由 off→shadow（不拦只记），go 后才→strict。现有 receipt 记了 verify 判定但缺 ground truth 对照。

**shadow 期必采集（receipt/metrics 扩字段）**：
| 指标 | 含义 | 怎么采 |
|---|---|---|
| `shadow_verdict` | strict 模式**会拦还是会放** | shadow 跑完整 [A]VerifyGate→[B]outcome_verifier→[C]GoalChecker 三层，记最终 verdict，但不执行拦截 |
| `actual_outcome` 弱信号 | 用户事后是否 redo/抱怨/满意 | 后续同 session 是否「重发相同目标」「明确说没完成」「artifact 被用户采纳」等弱标签 |
| `goal_check_degraded` | R-3 矩阵的 `goal_check=skipped` 占比 | LLM 不可用降级计数 |
| `verify_latency_p95` | 三层 verify 端到端延迟 | strict 翻开前确认不拖卡对话 |
| `false_block_candidate` | shadow 会拦 + actual=真完成 | 误杀候选 |
| `false_pass_candidate` | shadow 会放 + actual=伪完成 | 漏放候选 |

**go/no-go 量化标准（建议，spec 时校准）**：连续 **N≥200** 个**高后果目标**判定中，全部满足才翻 strict：
- **误杀率**（false_block_candidate / 总会拦数）**< 2%**（误杀直接伤体验，最严）；
- **漏放率**（false_pass_candidate / 总会放数）**< 5%**（护城河红线，但比误杀稍宽）；
- **LLM 降级率**（goal_check_degraded）**< 10%**（降级太多说明 relay 不稳，strict 会随机拦）；
- **verify_latency_p95 < 3s**（不拖卡对话）。
- 任一不达标 → 留 shadow，先修 R-3 降级 / relay 稳定性，不强翻 strict。

**现有 metrics/receipt 够不够**：receipt 基建够（emit_receipts 已强制），但 `actual_outcome` 弱信号 + `shadow_verdict` 是**新字段**，需在 2.3 落地时一并埋（不是纯复用）。

---

## 5. pass^k 真机自动化落地方案（复用现有 harness）

**核心驱动**：`testcase/_send.py`（真 SendInput 输入）+ `testcase/_cdp.py`（locate/eval/shot/text 读状态），CDP 端口 9333。写一个 `testcase/passk_runner.py` 包成循环。

| WI | 真机 pass^k 怎么跑（每轮真 SendInput，非回放） | 一轮约耗时 | 诚实标：能否真机 |
|---|---|---|---|
| **1.1 持久** | for k: `_send.py companion "/goal <目标>"` → `taskkill /F /IM deskpet.exe` → 重启 Tauri → `_cdp.py eval` 查 `/goal` 列表含该目标 → `shot` 存证 | ~40s（含重启） | ✅ 真机 |
| **1.3 抗漂移** | for k: `_send.py` 连发 N 条长对话顶过压缩阈值 → `_send.py "我最初让你做什么"` → `_cdp.py text` 抓回复断言含原目标 → `shot` | ~90s（长对话） | ✅ 真机 |
| **2.2 重规划** | for k: `_send.py "帮我生成 PPT"`（构造会 verify 失败的伪完成）→ 轮询 `_cdp.py text` 等「重规划/二次生成」→ 断言 ArtifactCard 出现真 .pptx → `shot` | ~120s（真 LLM+生成） | ✅ 真机 |
| **2.3 verify** | for k: 制造伪完成 → `_cdp.py text` 断言被拦 + 真完成断言放行 → `shot` | ~80s | 🟡 表层可真机；`no_persona_leak` 只能后端 pytest pass^k |
| **4.3 自创** | for k: `_send.py` 跑多步任务 → 等候选确认卡 → `_cdp.py locate` 确认卡「保存」按钮坐标 → `_send.py`/deskpet-input.ps1 真点击 → 断言 SKILL.md 落盘 → 新 session 复用 | ~150s | ✅ 真机（含真坐标点击确认卡） |

**只能后端 pytest pass^k（点击复现不了，照做不砍，不计入真模拟人证据）**：
- 1.2 并发 claim 不双占（pass^k=5 并发竞争）；§7 死循环上界（构造永久失败任务断言总 LLM 调用 ≤ 上界）；2.3 no_persona_leak（注入诱导上下文断言判定不变）；R-3 各 LLM 点故障注入降级。

**关键纪律**：passk_runner 是「驱动器」，每轮实际出站是真 OS SendInput 事件 + 真 relay LLM 调用——不是 resolution 函数回放，不违反 `feedback_real_e2e_not_script_replay`。每轮必 `shot` 存 `screenshots/<wi>-k<n>.png`。失败 retry≥3 不同 workaround 才标环境受限。

---

## 6. 每阶段「可独立运行 + 可观测收益」验收锚点

> 原则：每阶段结束系统真能独立冷启动跑通，且有**不依赖下游消费者**的可观测收益；半截集成诚实标「阶段 N 落地、N+1 接消费」。

| 阶段 | 交付 WI | 独立可运行验收 | 可观测收益锚点（不依赖下游） | 半截集成诚实标注 |
|---|---|---|---|---|
| **A 地基** | 1.1+§6/§7、1.6、3.1/3.3/3.4、G1 | 冷启动无异常 + flag-OFF 字节基线（R-4b）通过 | **1.1**：`/goal` 设→重启→仍在（自闭环真机）；**3.1**：跨会话召回上次决策（自闭环）；**3.3**：Pin 不衰减（后端单测）；**daily_decay 调度真跑**（修 bug，log 验证每日触发） | **1.6 ToolPath 录了无消费者**（4.3 在 C 接）→ 标「阶段 A 录制、阶段 C 消费」，后端单测断言 `get_completed_path` 正确即验收 |
| **B 抗漂移+纠错** | 1.2/1.3/1.4/1.5、2.1→2.3→2.2→2.4、3.2 | 冷启动 + A 的 load_persisted 已接（R-2）+ 长会话不崩 | **1.3**：长对话不漂移（真机）；**2.2**：伪完成→重规划→二次真产物（真机）；**2.3**：verify 接 goal（shadow 模式开始采集 R-7 指标）；**3.2**：改偏好下轮反映（真机） | **2.3 shadow 不拦只记**→ 标「shadow 采集期，go/no-go 看 §4」，不在 B 翻 strict |
| **C 高风险大件** | 4.0、4.1、4.2、4.3a-d | 4.0 单独冲刺全回归长会话通过 + 常驻资源回归（R-9） | **4.0**：压缩后追问原目标答对（真机+保留项后端单测）；**4.1**：相关 skill 自动载（log grep）；**4.3**：多步→确认卡真点击→SKILL.md 落盘→复用（真机全链） | **4.1 真机=log grep**（UI 看不出正文进 prelude）→ 已 §14.3 标，严禁拿 log 当模拟人 |

**跨阶段硬锚**：A→B 之间必须验证「R-2 lifespan load_persisted 已接通」（否则 B 的 1.5 读不到恢复 goal）；B→C 之间验证「R-7 shadow 指标已在采集」（否则 C 上线无 go/no-go 依据）。

---

## 7. 建议在 v4 plan 里新增 / 拆分的子任务（不砍任务）

> 全是「让任务更可跑」的前置 spike / 接电 / 验收，补在既有 WI 下，不增减功能范围。新增 R-T1~R-T8。

| ID | 归属 WI | 内容 | 为什么必须 |
|---|---|---|---|
| **R-T1** | 1.1/1.5/lifespan | **goal_store 接电进 lifespan**：写死顺序 `_sdb.initialize → schema_v2_migrator(pinned) → facts_store → goal_store.bind_persistence + await load_persisted → bind_on_goal_set → 注册 auto_resume redispatcher → 调度 daily_decay_loop` | 现 `main.py:1067-1080` 只 `_GS()` 无 load_persisted，1.1「重启仍在」当前接不通；顺序错则「A 没 ready 被 B 用」 |
| **R-T2** | 1.3/4.0/§6.3 | **system 栈尾拆两条预算路径 + compressor 软上限 1500**：注入格式契约拆「compressor system 段（goal）」vs「assembler Slice（人格/skill）」，goal_text 单一权威路径，跨系统去重 | `budget.py` 整块丢 vs `_partition` 全保留是两套系统，§6.3「都注 system 栈尾」是运行时盲区 |
| **R-T3** | 2.x/1.4 | **LLM 失败降级矩阵实现 + 故障注入单测**（§3 表）：每点定第三态、记降级事实、mock 超时/畸形/空的 pass^k | relay 规模化必畸形/超时；现 blueprint 只说「宽松解析」没定降级后拦/放语义 |
| **R-T4** | 1.5/2.2/§7 | **respawn pending-resume 队列**：auto_resume 续跑改「WS 重连注册 redispatcher 后 drain」，不静默 return；§7 明确 respawn 计数语义 | `main.py:2076-2083` redispatcher 未注册即吞掉，重连竞态（control_ws 快照坑同构） |
| **R-T5** | 全 flag WI/§11 | **`scripts/e2e_flag_off_baseline.py`**：flag-OFF 跑冷启动+对话，断言新表不建、关键表字节快照 hash 不变 | 「flag-OFF 字节不变」现只有 spec 文字，无运行时回归 |
| **R-T6** | 2.3/2.4/observability | **shadow 可观测埋点**：receipt 扩 `shadow_verdict`/`actual_outcome` 弱信号/降级率字段 + go/no-go 标准（§4） | 现 receipt 无 ground truth 对照，无法量化误杀/漏放率判翻 strict |
| **R-T7** | 1.1/1.2/3.3/拓扑 | **多 worktree 独立 `DESKPET_USER_DATA_DIR` + 老用户首启迁移验收**：真实老 state.db 升级测 pinned ALTER/新表建出/0 丢失/flag-OFF 不发生 | 共享 db 迁移串味；老用户首启路径无验收锚点 |
| **R-T8** | 4.1/1.2/3.3/§11 | **常驻资源回归 + SkillMatcher query embedding to_thread+缓存 + goal_tasks 落库 debounce**：空闲 30min+10 轮监控 RSS/CPU 不泄漏 | 常驻桌宠叠多后台 task + 每轮 embedding，热路径同步 encode 卡 event loop / `_write_lock` 争用 |

---

## 8. Round2 总判

> v3 经 Round1 静态硬化后「写得出来」基本可信；本轮**运行时/集成/上线**层补出 8 个阻塞点（R-1~R-9）+ 8 个新子任务（R-T1~R-T8），全部「不砍任务、只让任务真能跑起来」。最致命的 3 个（system 栈尾无共享预算、goal_store 无 load_persisted、respawn 重连竞态）必须在**阶段 A 开工前**钉死，否则单测全绿、真机第一次跑就翻车。

*报告路径：`G:\projects\deskpet\plans\2026-06-04-goal-completion-upgrade\07-challenge-round2.md`*
*核实基础：实地 Read main.py(lifespan 1416-1730 / goal_store 接电 1067-1130 / auto_resume 2060-2099 / facts_store+migrator 848-867) / budget.py(76-150 allocate 整块丢) / context_compressor.py(129-258 should_compress+compress+_partition) / config.py(grep flags 默认值: goal_mode/preference_memory/verify_gate_mode) / schema_v2_migrator.py(additive ALTER safe-fail) / memory/migrator.py(numbered+user_version 对比) / testcase/_cdp.py + _send.py(pass^k harness)。*
