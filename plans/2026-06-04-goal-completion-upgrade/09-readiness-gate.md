# 开工就绪度评估（Go/No-Go）

> 评估人：交付负责人（delivery lead，只读不改）
> 评估对象：`plans/2026-06-04-goal-completion-upgrade/` v4 套件（00-PLAN + 01~04 blueprint + 05 评审 + 06/07 两轮挑战 + 08 对齐审计）
> 评估问题：**这个 plan 现在能开始写代码了吗？还是必须再迭代一轮？**
> 抽查基础：实地 Read `goal_store.py`（现状纯内存属实）+ `main.py:1067-1080`（R-T1 lifespan 无 load_persisted 属实）
> 日期：2026-06-04

---

## 0. 裁决：**GO — 阶段 A 立刻动手，停止评估**

**理由（一句话）**：v4 已具备「一个工程师拿着 blueprint 01 + §6 契约 + §14/§15 spike 就能写 WI-1.1」的就绪度，5 份研究→自查→评审→2 轮挑战→对齐审计的迭代已让**边际收益趋零**，再开一轮是分析瘫痪，不是价值增量；唯一的 GO 前提是**阶段 A 开工第一天先冻结 §6/§7 + 钉死 3 个 lifespan 接电 spike（R-T1/R-T2/R-T4 的设计决策，非全量实现）**，这些是「开工当天的第一批任务」，不是「再迭代一轮」。

---

## 1. 开工就绪度（阶段 A 第一步能不能立刻动手）

**结论：能立刻动手，路径清晰到「拿着就能写」。**

阶段 A 第一步 = **WI-1.1 Durable Goal Store + §6 契约冻结 + R-T1 lifespan 接电**。逐项核查就绪度：

| 就绪要素 | 状态 | 证据 |
|---|---|---|
| **目标文件清单** | ✅ 已列全 | blueprint 01 §WI-1.1.1：改 `goal_store.py` / `memory_v2_schema.py` / `main.py:~1075` / `commands/__init__.py` + 新建 `test_goal_store_persistence.py` |
| **schema 字节级定义** | ✅ 已冻结 | §6.1 `SessionGoal` dataclass 9 字段 + blueprint §2 `session_goals`/`session_subgoals` 两表完整 DDL（PK=goal_id、index、默认值齐全） |
| **接电点 file:line** | ✅ 已核真 | 抽查确认 `main.py:1067-1080` 现仅 `_session_goal_store = _GS()`，grep `load_persisted` 仅命中 `_code_mode_manager`（行 1466）——**goal_store 确实从不 load/bind**，R-T1「重启仍在接不通」属实且已开方 |
| **落库惯例有先例** | ✅ 4 次先例可抄 | blueprint §0 证：`code_todos/session_plans/code_sessions/supervisor_hints` 已跑顺「runtime CREATE TABLE IF NOT EXISTS + bind_persistence + 薄 SessionDB 方法」；不写 migration、不 bump user_version。`_code_mode_manager` 的 sync-bind + async-load 时序（main.py:1228/1466）就是 goal_store 要照抄的同构模板 |
| **build order（WI 内）** | ✅ 6 步明确 | blueprint §9：DDL→SessionDB 薄方法→扩字段→bind/persist/load→_handle_goal 改 async+main 接电→单测全绿→restart E2E |
| **测试/验收可执行** | ✅ 已定义到可跑 | 单测 5 条（含 BC 断言）+ 真机 TC-1.1-restart（设目标→taskkill→重启→`/goal` 查仍在，pass^k=3）+ flag-OFF 字节基线（R-T5） |
| **对外契约（冻结给下游）** | ✅ 唯一权威已定 | §6.2 只读接口 `get_goal_text/get/get_completed_path` + None 语义稳定 + 同步零 I/O + 一致性窗口（永读内存）都已写死 |

**没有「必须先答的未知」卡住 WI-1.1 开工。** §13 待定 5 项里唯一沾 1.1 的是「2.3 完成对照用 text 还是 text+criteria」——那是 P0-2 阶段 B 的事，1.1 把 `criteria` 字段建成 nullable 即可，不阻塞。

**测试/验收路径**（pass^k harness / flag-OFF 基线 / shadow 埋点）就绪度：
- **pass^k harness**：§11 + 07 §5 已定复用 `testcase/_cdp.py`+`_send.py` 写 `passk_runner.py`，每轮真 SendInput + 真 relay，1.1/1.3/2.2/4.3 真机 pass^k、§7/1.2/2.3 后端 pytest pass^k。可执行。
- **flag-OFF 基线**：R-T5 `scripts/e2e_flag_off_baseline.py` 断言新表不建 + 字节 hash 不变，方案明确。
- **shadow 埋点**：R-T6 receipt 扩 `shadow_verdict/actual_outcome/降级率` + go/no-go 量化标准（N≥200、误杀<2%、漏放<5%、降级<10%、p95<3s）。**但这是阶段 B 的事**（2.3 落地才采集），不阻塞阶段 A。

---

## 2.「开工前必须先定」vs「可边做边定」清单（防拖延）

> 核心防误判：把「可边做边定」当「必须先解决」就是拖延开工的元凶。

### 2.1 真阻塞（阶段 A 开工**当天**先定，但属「先想清楚再写」非「再迭代一轮」）

| 项 | 为什么是开工前阻塞 | 成本 |
|---|---|---|
| **§6 契约冻结**（SessionGoal schema + 注入格式 + 双写规则） | 派 1/2/3 并行的头号翻车点（正中 `feedback_cross_layer_contract`）；schema 是所有下游 WI 的契约源头，定错全线返工 | 已写好，开工当天 review 确认即可，**0 新增调研** |
| **§7 重试预算账本**（attempt 计数 per-session vs per-reason） | 决定 2.2/§7 死循环上界单测怎么断言；blueprint 已指 `auto_resume.py:167` per-session 倾向，但要 Read `:148-180` 终判 | 半天 spike（读代码确认），**非新一轮评估** |
| **R-T1 lifespan 接电顺序** | 已实地确认 goal_store 无 load_persisted；顺序错则「A 没 ready 被 B 用」。写死 `_sdb.init→migrator→facts→goal_store.bind+load→bind_on_goal_set→redispatcher→daily_decay_loop` | 顺序已在 plan 写死，照接即可 |
| **R-T2 两条预算路径软上限 1500** | 三大致命运行时风险之一（compressor `_partition` 全保留 vs assembler 整块丢）；1.3/4.0 共用 compress 签名，不先冻结=契约漂移 | T4 已冻结签名 `compress(messages,*,goal_text=None,pending_tasks=None)`，照冻结写 |
| **T5 子 agent 进程模型核查** | 决定 1.2 claim 走 `_write_lock` 串行（同进程）还是 `BEGIN IMMEDIATE`（跨进程）；阶段 A 不做 1.2，但锁原语选型影响 schema | 半天 spike，**仅阶段 B 启动前必须**（阶段 A 可后置） |

**关键判断**：以上「阻塞」全是**已在 plan 里写好答案、开工当天确认/小 spike 即可**的，**没有一项需要「再迭代一轮 plan 文档」**。§6/§7/R-T1/R-T2/T4 都已经是冻结决策，不是开放问题。

### 2.2 可边做边定（写到那一步再定也不晚，**别拖开工**）

| 项 | 为什么可后置 |
|---|---|
| 2.3 完成对照用 `text` 还是 `text+criteria`（§13） | 阶段 B 的 2.3 才用；1.1 把 criteria 建 nullable 即可 |
| 3.2 人格注入出厂开/flag（§13） | 阶段 B 的 3.2 才决；倾向 flag default False 已写，spec 时拍板 |
| 1.2 在 code_todos 扩展 vs 新建 goal_tasks 表（§13） | blueprint 已倾向新建 goal_tasks，T5 核完进程模型即定；阶段 B 前 |
| 1.4 子输出过滤判据（LLM judge vs 关键词） | blueprint §6 已给「默认 [off-goal] 标记法 + 高后果才 LLM judge」，写到 1.4 直接用 |
| 2.4 高后果判据可程序化 | blueprint 02 已给判据；阶段 B 实现时落 |
| 是否走 OpenSpec / 拆 13 组 TDD（§13） | 流程选择，不影响代码内容，开工后任何时候可决 |
| 各 Phase 是否多 worktree 并行（§13） | 端口隔离拓扑已在 CLAUDE.md；先单树跑阶段 A 也行 |
| R-T6 shadow go/no-go 翻 strict | 阶段 B 2.3 落地后才采集，C 上线前才判，**绝不阻塞 A/B 开工** |

**反拖延断言**：§13 的 5 个待定 + §14/§15 的 15 个 spike 里，**真正卡阶段 A 第一步的只有「§6/§7/R-T1 确认」3 项，且全是已写好答案的当天任务**。其余 12+ 个 spike 都挂在阶段 B/C 的具体 WI 下，**写到那一步再 spike 完全来得及**。把它们当「开工前必须全部解决」就是制造瘫痪。

---

## 3. 完整度核查（WI/契约/验收 是否够）

### 3.1 4 个 Phase 的 WI 完整度

每个 WI 都有「目标文件 + 接口 + 验收 + E2E 等级」——**无一停在「描述清楚但没实现路径」**：

| Phase | WI 数 | 目标文件 | 接口/schema | 验收 | E2E 等级 | 缺口 |
|---|---|---|---|---|---|---|
| P0-1 | 6（1.1~1.6） | ✅ blueprint 01 逐 WI 列 file:line | ✅ §6 契约 + 各 WI dataclass/DDL | ✅ 单测+真机+pass^k | ✅ §2 表末列 | 无 |
| P0-2 | 4（2.1~2.4） | ✅ blueprint 02 | ✅ StructuredReflection schema + 三验串联 | ✅ + §7 账本上界测 | ✅ | 无 |
| P0-3 | 5（3.1~3.5） | ✅ blueprint 03 | ✅ facts category + Component priority | ✅ + MemEval 不回归 | ✅ | 3.5 标⏸可选，已显式 |
| P1-4 | 5（4.0~4.3） | ✅ blueprint 04 | ✅ SkillMatcher + WS verb（T7 新接线） | ✅ + 全回归长会话 | ✅ | 4.2 标「可能收敛为验证不丢失」已诚实 |

### 3.2 跨 Phase 契约（防并行子代理打架）

| 契约 | 是否齐全 | 证据 |
|---|---|---|
| §6 goal_text 跨 Phase 契约 | ✅ 字节级齐全 | schema + 唯一权威读接口 + None 语义 + **两条预算路径拆清（R-T2）** + 双写规则（单向钩）+ live smoke 兜底脚本 |
| §7 重试账本 | ✅ 齐全 | 三套现有上限映射 + 新重试嵌入规则 + 死循环上界测 + respawn 计数语义（R-T4） |
| §9 红线落点 | ✅ 4 条全有 WI 落点 | 红线1→2.3 白名单+no_persona_leak 单测+3.2 排除 verify；红线2→2.3/2.4 反谄媚前缀；红线3→不做主动性；红线4→禁新增在线时长埋点 |
| §10 defer 清单 | ✅ G1-G10 全表态 | 评审 05 标的 6 处 silent drop 已全部补回（审计 08 §2-3 确认无残留） |
| §14 冻结签名表 | ✅ 6 个关键符号冻结 | compress/consult_ephemeral_subagent/goal_tasks claim/build_teammate_tools/on_goal_set/verify_exhausted——并行前锁，禁各写各的 |

**契约齐全到不会让并行子代理打架**：头号风险点（compress 签名 1.3×4.0 共用、build_teammate_tools 非「加进去」、goal_text 唯一权威=compressor 段不重复携带）都已点名冻结。

### 3.3 验收路径完整度

✅ 逐 WI E2E 分级（§14.3 诚实表，防子代理拿 log 当「模拟人」交差）+ pass^k 参数指定（1.1/1.3/2.2/3.1/4.3 真机）+ flag-OFF 字节基线 + 跨层契约 live smoke + MemEval 不回归 + 常驻资源回归 + shadow go/no-go 量化。**比绝大多数 plan 的验收都更可执行**。

---

## 4. 该不该再迭代（边际收益判断 + 有无未覆盖盲区）

### 4.1 已覆盖的评估维度（4 轮 + 审计，覆盖已饱和）

| 维度 | 覆盖文档 | 结论 |
|---|---|---|
| 研究对齐（重心没漂移） | 08 对齐审计 | 🟢 绿，5 方向/7 易走偏点/4 护城河全忠实落地 |
| 完整性（research × WI 矩阵） | 05 评审 | 9 必修已全整合进 v4 |
| 静态实现（逐符号核真代码） | 06 Round1 | 3 致命阻塞 + T1-T7，全进 §14 |
| 运行时/集成/上线 | 07 Round2 | 3 致命运行时风险 + R-T1~R-T8，全进 §15 |
| 接电点实地核真 | 本评估抽查 | goal_store 纯内存 + R-T1 无 load_persisted 属实，plan 诊断准确 |

### 4.2 有无未覆盖盲区？—— **覆盖已饱和，无开工级新盲区**

逐维度找「没被任何一轮覆盖的真盲区」：
- **静态正确性**：06 逐符号核真，覆盖。
- **运行时集成**：07 核 lifespan/预算/迁移/重连/harness，覆盖。
- **研究忠实度**：08 覆盖。
- **接电真实性**：本评估抽查 2 个最高风险点属实，覆盖。
- **唯一可议的「半盲区」= 真机 E2E 实操中的工具障碍**（windows-mcp Click schema bug / 中文 IME / WebView2 不响应老 mouse_event）——但这**不是 plan 盲区**：全局 + 项目 CLAUDE.md 已有完整 workaround 表（SendInput 圣杯 + STA Clipboard），且 `plans/manual-results-2026-05-26-master/UI_AUTOMATION_BREAKTHROUGH.md` 有 16 case 真 PASS 实战索引。**这是执行期边做边解决的事，不是再迭代一轮 plan 能消除的。**

### 4.3 再迭代一轮的边际收益判断

**边际收益已趋零，不该再迭代。** 论据：
1. 第 3、4 轮（06/07）已经在「逐符号 + 逐 lifespan」颗粒度核真代码，再细就是写代码本身了——**继续评估 = 在 plan 文档里模拟编译器**。
2. 08 审计专门检验「4 版迭代后重心有没有漂」，结论绿灯，说明迭代已收敛而非发散。
3. 本评估抽查的 2 个最高风险接电点，plan 描述与真代码**逐字对应**——blueprint 质量已经过实战级核验。
4. 剩余所有「未定」都是**阶段 B/C 的局部决策**，本质是「写到那再定」，**不构成阶段 A 的开工阻塞**。为它们再开一轮 = 典型分析瘫痪。

**若有人主张再迭代，唯一站得住的理由只能是**：发现某个阶段 A 的 WI 实现路径有歧义。但本评估已逐项核查 WI-1.1，路径无歧义。**故：不需要再迭代。**

---

## 5. 给用户的明确建议：现在动手 or 再迭代？第一步具体做什么？

### 建议：**现在动手，停止评估。**

这份 plan 的评估深度已经超过绝大多数工程实践所需。再加一轮只会增加文档、推迟价值交付，且无法消除「真机 E2E 工具障碍」这类只能在执行期解决的问题。**该写代码了。**

### 阶段 A 第一步的具体动作序列（开工第一天）

1. **冻结确认（半天，非新调研）**：
   - Review §6 契约 + §14.2 冻结签名表，团队/子代理对齐 schema 与 6 个冻结符号。
   - Read `auto_resume.py:148-180` 终判 §7 的 `max_attempts` 是 per-session 还是 per-reason，落 §7 上界测的断言口径。

2. **WI-1.1 按 blueprint 01 §9 的 6 步 build order 写**：
   - ① `memory_v2_schema._DDL` 加 `session_goals`+`session_subgoals` 两表 + 建表幂等单测
   - ② SessionDB 加 3 薄方法（upsert/get_active/list_active）+ round-trip 单测
   - ③ `SessionGoal` 扩字段（全默认值，BC）
   - ④ `bind_persistence` + async `_persist` + `load_persisted`
   - ⑤ **R-T1 lifespan 接电**：照 `_code_mode_manager` 的 sync-bind（main.py:1230 同构）+ async-load（main.py:1466 同构）模板，按 R-T1 写死顺序接 goal_store；`_handle_goal` 改 async 落库
   - ⑥ 单测全绿 → windows-mcp TC-1.1-restart（设目标→taskkill deskpet.exe→重启→`/goal` 查仍在，pass^k=3）+ R-T5 flag-OFF 字节基线

3. **并行低风险速赢**（阶段 A 第 2 批，纯后端不依赖 1.1 真机）：3.3 Pin / 3.4 light / 3.1 记忆 + WI-1.6 路径录制 + G1 gitignore 自查。

4. **阶段 A→B 硬锚**：B 启动前验证「R-T1 load_persisted 已接通」（否则 B 的 1.5 resume 读不到恢复 goal）；T5 进程模型核查在 1.2 开工前补。

**一句话**：§6/§7 当天冻结确认 → 直接进 WI-1.1 的 6 步 build order → restart 真机 E2E 见绿 → 阶段 A 落地。不要再开评估轮。
