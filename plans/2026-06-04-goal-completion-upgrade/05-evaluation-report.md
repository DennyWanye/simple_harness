# 00-PLAN 评审报告（完整性 + 可执行性 + 风险）

> 评审对象：`plans/2026-06-04-goal-completion-upgrade/00-PLAN.md` (v1.1)
> 评审依据：5 份 research + comparison-gap-analysis + deskpet-self-audit（4 份 p0/p1 + SUMMARY）
> 评审方式：只读对照 research 借鉴点逐条核对 WI 覆盖；抽查代码事实校准（goal_store / agent_loop / config.py）
> 评审日期：2026-06-04 ｜ 评审角色：资深技术评审 + 架构师

---

## 1. 总评

**能不能直接拆阶段开发？—— 能，但要先补 3 处「跨 Phase 契约」与 2 处「underspecified WI」才动手。**

这份 plan 的**主线判断是正确且有说服力的**：它没有把任务理解成「加 4 个功能」，而是抓住了 self-audit 的核心洞察——缺口是「一条断掉的目标线」，第一块多米诺 `goal_store.py` 内存态同时卡死「目标持久 / verify 接 goal / goal 记忆沉淀」三件事，先推倒它后面连锁受益。执行顺序（§6）与 self-audit 的依赖判断一致，多 agent 并行切分（派 1-4）也合理。代码事实经抽查全部属实（`goal_store.py:10-11` TODO 留 v2、`agent_loop.py` 无 `should_compress` 调用、`config.py:250` verify_gate_mode 默认 `off`）。

**最大短板（3 条，按严重度）：**

1. **跨 Phase 契约只有口头约定，没有字节级 schema。** plan §6 说「派 2 依赖 1.1 的 goal_text 接口，约定契约后并行」，但**全文没有定义 `goal_text` / `SessionGoal` schema 长什么样**——1.1 产出什么字段、1.3/2.3/3.1 各自读哪些字段、`subgoals/progress/criteria` 是不是 1.1 就建好。这是并行开发的头号翻车点（pytest+tsc 都过但字段 disagree，正中 `feedback_cross_layer_contract`）。
2. **两个最高风险 WI（2.2、4.3）underspecified。** 2.2「真重规划重试」要替换 `ephemeral_subagent` stub + 复用 circuit/auto_resume 上限，但**没说重试预算怎么和现有两层上限（circuit threshold=3 / auto_resume max_attempts=2 / max_verify_nudges=2）合并**，存在三套上限叠乘失控风险；4.3「技能自创」涉及「写新 SKILL.md + 热加载 + 用户确认门」，跨 loader/store/UI/权限四层，是全 plan 改动面最大的，却只给了一行验收。
3. **§7 数字伴侣红线仍偏口号，落点不全。** plan 自己承诺「显式写进 verify gate 与人格注入，不止停在文字」，但只有红线 1（人格层不得介入完成判定）映射到了 WI-2.3，红线 2/4（反谄媚完成、不优化在线时长）**没有任何 WI 落点**，红线 3 因主动性不做而搁置。

只要修掉这 3 条（见 §5 Top-N），plan 即可拆 spec 进入并行实现。

---

## 2. 完整性审计（research 借鉴点 × WI 覆盖矩阵）

> 图例：✅ 有对应 WI ｜ 🟡 部分覆盖/隐含 ｜ ⏸ plan 显式 defer ｜ ❌ silent drop（漏掉，未提及取舍）

### 2.1 claude-code 借鉴点

| 借鉴点 | 覆盖 | WI / 取舍 | 评注 |
|---|---|---|---|
| 4.1 Skills 渐进披露三级做彻底 | ✅ | 4.0/4.1/4.2 | 二级 embedding 匹配 + 三级重挂 + budget 都点到 |
| 4.2 **plan mode 作为独立「只读权限」模式** | ❌ silent drop | — | research 反复 ★★ 强调（计划确认期是否物理只读）；gap §5、self-audit 均列为开放问，**plan 完全未提**。建议至少显式 defer + 一句「现状是否真只读」自查 |
| 4.3 `/verify`+`/run` bundled skill（真运行确认配方） | ❌ silent drop | — | §8 验收纪律提了 windows-mcp 真测，但**没把它固化成可调用 skill + 启动配方**（research 的核心：纪律变工具不靠自觉）。建议补一个 `run-deskpet`/`/verify` skill WI 或显式 defer |
| 4.4 hooks exit2 硬强制（质量门控确定性层） | 🟡 | §8 隐含 | research ★ 强调「软指令≠强制，能变 hook 就别只写 prompt」；plan §4「字节级契约+flag」沾边但**没把"没验证就收尾"做成 Stop/PostToolUse exit2 hook**。属元原则，建议显式表态 |
| 4.6 auto-memory（桌宠产品自写 learnings） | ❌ silent drop | — | research 4.6 ★ 指出 DeskPet 桌宠产品本身缺「给终端用户的轻量 auto-memory」。3.1 是规则抽取 goal/decision，**不等于** agent 自决「值得长期记什么」。建议归入 3.x 或显式 defer |
| 4.7 软指令塑造意图/硬机制保证边界（元原则） | 🟡 | §4 红线 | plan 有「flag 渐进点亮」惯例，方向对；但未逐条审「哪些 HARD CONSTRAINT 该变 hook」 |

### 2.2 openhuman 借鉴点

| 借鉴点 | 覆盖 | WI / 取舍 | 评注 |
|---|---|---|---|
| B1 分层摘要记忆树 + 可编辑 .md vault | ❌ silent drop | — | research B1 ★ 列为可抄。plan §4 护城河说「检索已领先别重写」，但**那是检索路数，不是"可人工编辑 .md vault"**——二者不同维度。建议显式 defer（合理不做，但要写明，别 silent） |
| B2 五路混合检索 | ⏸ | §1 非目标 | self-audit 已证 7 路做好，plan 明确「已做好」剔除——正确取舍 |
| B3 PROFILE.md 人格画像 + 半衰期/Pin/Forget | ✅ | 3.2/3.3 | 主动注入 + Pin + PreferenceMemory 补衰减，覆盖到位 |
| B4 写入分级 light 快路（跳 embedding） | ✅ | 3.4 | put_doc_light 思路落地 |
| B5 direct-first 四级委派 + 子 agent 上下文隔离 | 🟡 | 1.4 隐含 | 1.4 做 handoff goal 过滤，但**「能不委派就不委派」的委派分级**未独立落 WI；属可选，建议归入 1.x 或 defer |
| B6 TokenJuice 工具输出压缩 | ❌ silent drop | — | gap §5、openhuman B6 都列「降本降延迟可加 tool-output 压缩中间件」。本地桌宠延迟敏感，ROI 不低。**plan 未提**，建议显式 defer |
| B7 冷启动接 SaaS | ⏸ | §1 非目标隐含 | 与本地隐私定位冲突，不做合理；但 plan 未点名，建议一句带过 |

### 2.3 hermes 借鉴点

| 借鉴点 | 覆盖 | WI / 取舍 | 评注 |
|---|---|---|---|
| 借鉴1 agentic JSON-mode 结构化反思字段 | ✅ | 2.1 | error_analysis/critique/replanning 必填字段，覆盖 |
| 借鉴2 技能自创闭环触发器 | ✅ | 4.3 | 触发器（≥5工具/从错误恢复/被纠正/非显然）+ 用户确认门，覆盖 |
| 借鉴3 **周期性记忆 nudge（agent 自决记什么）** | ❌ silent drop | — | research 借鉴3 + gap P1-3 都列。3.1 是**规则抽取** goal/decision，hermes nudge 是**agent 主动反思「值得记什么」**，补规则漏掉的非显然信息。两者互补不重叠。**plan 未提**，建议归入 3.x 或显式 defer |
| 借鉴4 iteration budget + 显式完成信号 | ✅ | 2.2 隐含 | 「保留 retry 上限，复用 circuit/auto_resume」沾边（但见 §3 上限合并问题） |
| 借鉴5 **中断/恢复从 verified state 续跑** | 🟡 | 1.5 | 1.5 做「resume 接 goal_text」，但 research 强调的是「从最后 **verified state** 续跑（任务执行到第 N 步可恢复）」——self-audit 明确现状只有 supervisor hint，**无任务级 checkpoint**。1.5 验收只覆盖「注入 goal_text」，**未覆盖 verified-state checkpoint**。建议明确 1.5 是「窄版（只接 goal）」并 defer「任务级 checkpoint」 |
| 借鉴6 function-calling 模型无关协议 | ⏸ | — | research 自评「非紧急」，不做合理；可不提 |
| 借鉴7 Honcho 链式自审（用户建模多 pass） | ⏸ | — | research 自评「长期非 last-mile」，不做合理 |

### 2.4 cc-haha 借鉴点

| 借鉴点 | 覆盖 | WI / 取舍 | 评注 |
|---|---|---|---|
| 4.1 Task 工具族（依赖 + 跨 agent 共享状态） | ✅ | 1.2 | 已锁定「跨子 agent 共享状态」，覆盖到位 |
| 4.2 **集中式审批聚合 UX** | ❌ silent drop | — | gap §5、cc-haha 4.2 列「桌宠侧统一待审批聚合视图」。4.3 技能自创要弹「确认卡」，**正好需要一个审批 UX 落点**，但 plan 没把它和集中审批关联。建议至少在 4.3 注明确认卡走哪个 UI 通道，或显式 defer 聚合视图 |
| 4.3 **实时 diff 反馈环（桌宠侧轻量 diff 小卡片）** | ❌ silent drop | — | cc-haha 4.3 列。属体验增强，**plan 未提**，建议显式 defer |
| 4.6 gitignore-aware 上下文采集 | ❌ silent drop | — | cc-haha 4.6 ★ 与 MEMORY `feedback_context_overflow_bulk_reads` **完全同源**，被点名为「低成本高收益对齐项」。**plan 未提**，且这是低成本速赢，建议补一句自查或 defer |
| 4.4 多通道远程 IM/H5 | ⏸ | §1 非目标 | plan「不做多通道/远程 IM」已显式 defer——正确 |
| 4.7 Computer Use bridge | ⏸ | §1 护城河 | DeskPet 已更强，不抄——正确 |

### 2.5 最佳实践（goal-completion）借鉴点

| 借鉴点 | 覆盖 | WI / 取舍 | 评注 |
|---|---|---|---|
| §4.2 durable goal doc + re-anchoring | ✅ | 1.1 + 1.3 | 覆盖 |
| §4.2 handoff goal checkpoint | ✅ | 1.4 | 覆盖 |
| §4.2 **GD_actions / GD_inaction 漂移信号** | 🟡 | 1.3 提及 | 1.3 「跟踪 GD_actions/GD_inaction」点到了，但**没说怎么度量/在哪打点/触发什么**。属 1.3 内 underspecified（见 §3） |
| §3.1 外部验证 > 自我验证 | ✅ | 2.4 | 已纳入本轮（高后果触发） |
| §3.2 verify 重述原目标对照产物 | ✅ | 2.3 | 覆盖 |
| §4.3 记忆抽取目标/决策/约束 + **多 scope 打标** | ✅ | 3.1 | 「多 scope 打标(user/session)」点到。注意 self-audit 指出 facts 表只有 `subject` 无 `scope` 维度，3.1 需新建 scope 字段（实现细节，spec 时定） |
| §7.1 **pass^k 稳定性评测** | 🟡 | §8 提及 | §8 写了「关键能力测连续 k 次稳定」，但**没说 k 取几、哪些 WI 必做、怎么自动化**。属验收纪律 underspecified |
| §6 数字伴侣 6 反模式红线 | 🟡 | §7 | 见 §1 短板3 / 下方矩阵：红线 1 有落点，2/4 silent，3 因不做搁置 |
| §4.1 主动性 6 缓解模式 / perceive-decide-act | ⏸ | §1 非目标 | 主动性本轮不做——已显式 defer，正确 |

**goal drift 6 缓解模式落地核对**（research §4.2 列 6 个）：

| 缓解模式 | WI | 状态 |
|---|---|---|
| 1 durable goal document | 1.1 | ✅ |
| 2 显式子目标跟踪（小到每个上下文都带） | 1.1/1.2 | 🟡 1.1 提了 subgoals 字段但「current-subgoal 注入每个上下文」未明确，更多落在 1.3 |
| 3 handoff goal checkpoint | 1.4 | ✅ |
| 4 上下文边界 re-anchoring | 1.3 | ✅ |
| 5 planner/executor 进程分离 | — | ❌ silent drop（本轮架构不动 planner/executor 分离，合理不做但未表态） |
| 6 价值对齐约束框定 | — | ❌ silent drop（「把违背模型偏好的约束框成特例」无落点） |

### 2.6 遗漏项清单 + 建议取舍

> 按「该本轮做 / defer / 已被现有 WI 隐含覆盖」标注。**核心结论：没有"该做却漏"的硬伤，但有 6 处 silent drop 应在 v2 plan 里显式表态（哪怕只写一行 defer 理由），避免后续 agent 误以为遗忘。**

| # | 遗漏项 | 来源 | 建议 |
|---|---|---|---|
| G1 | gitignore-aware 上下文采集自查 | cc-haha 4.6 | **本轮做（低成本速赢）** 或至少自查现状一行 |
| G2 | 周期性记忆 nudge（agent 自决记什么） | hermes 借鉴3 | **可本轮做（归入 3.x 作 3.1 补充层）**，ROI 中、与 3.1 协同 |
| G3 | plan mode 真只读权限自查 | claude-code 4.2 | **显式 defer + 自查一行**（确认计划确认期是否真只读，不真则记 backlog） |
| G4 | `/verify`+`/run` bundled skill | claude-code 4.3 | **显式 defer**（与 §8 真测纪律强相关，下一轮值得做） |
| G5 | 桌宠产品级 auto-memory | claude-code 4.6 | **显式 defer**（与 3.1 区分清楚：规则抽取 vs agent 自决） |
| G6 | 任务级 verified-state checkpoint | hermes 借鉴5 | **显式 defer**（1.5 窄版只接 goal_text，checkpoint 单独立项） |
| G7 | TokenJuice 工具输出压缩 | openhuman B6 | **显式 defer**（本地延迟 ROI 不低，记 backlog） |
| G8 | 集中式审批聚合 UX | cc-haha 4.2 | **本轮关联（4.3 确认卡需指定 UI 通道）** 或 defer 聚合视图 |
| G9 | 可编辑 .md 记忆 vault | openhuman B1 | **显式 defer**（与本地隐私不冲突但工程量中） |
| G10 | 红线 2/4 落点（反谄媚完成/不优化在线时长） | 最佳实践 §6 | **本轮做**（§7 已承诺「写进 verify gate」就要给 WI 落点，见 §5 Top-3） |

---

## 3. 可执行性审计（逐 WI）

> 标注：可落地 = 实现路径清晰，self-audit 已给 file:line 最小路径 ｜ underspecified = 描述清楚但实现路径有歧义/缺关键决策

| WI | 可落地 | underspecified 点 | 依赖正确性 |
|---|---|---|---|
| **1.1** Durable Goal Store | ✅ 高 | **schema 未定义**（subgoals/progress/criteria 字段是否本轮全建？这是所有下游 WI 的契约源头）。self-audit 给了 `bind_persistence(sdb)` + `session_goals` 表最小路径，落地无障碍 | 第一块多米诺，无上游依赖 ✅ |
| **1.2** Task 任务图跨 agent 共享 | 🟡 中 | self-audit 揭示有**三套 todo 结构**（code_todos / todo.json / TeamStore），1.2 说「给 todo 加依赖+跨 agent 共享」**没说在哪套上加 / 要不要统一**。TeamStore 是 spawn_team 时新建库、完成即销毁——「跨子 agent 共享 + 持久化」与现有 TeamStore 生命周期冲突，需明确取舍 | 依赖 1.1（goal 执行层）✅ |
| **1.3** re-anchoring | ✅ 高 | **GD_actions/GD_inaction 怎么度量+打点**未定义；要改 `ContextCompressor` + `history_compactor` **两处**压缩器，需保证两处行为一致（契约漂移点） | 依赖 1.1 goal_text ✅ |
| **1.4** handoff goal checkpoint | ✅ 高 | 「子输出按目标过滤」的过滤判据未定义（关键词？LLM judge？）；self-audit 给了 `parent_goal_text` 参数路径，主体清晰 | 依赖 1.1 ✅ |
| **1.5** resume 接 goal | ✅ 高 | 验收只覆盖「注入 goal_text」，**未覆盖 verified-state checkpoint**（见 §2.3 借鉴5）。建议明确为窄版 | 依赖 1.1 ✅ |
| **2.1** 结构化反思字段 | ✅ 高 | response_format=json_object 约束 vs system message 强制 first-token，二选一未定（实现细节，可 spec 时定） | 无强依赖（可与 1.x 并行）✅ |
| **2.2** verify 失败真重规划 | 🟡 **低（最 underspecified）** | ① 要替换 `ephemeral_subagent` stub（self-audit P2 给了复用 goal_checker.llm_call 路径）；② **重试预算合并未定义**：现有 circuit(3) + auto_resume(2) + verify_nudges(2) 三套上限，2.2 再加「真重试」如何不叠乘失控？必须给统一预算账本。这是全 plan 最易死循环/回归的点 | 依赖 2.1（replan 字段驱动）✅ |
| **2.3** verify 接 goal_text | ✅ 高 | self-audit 揭示 VerifyGate 与 GoalChecker 是**两个独立 if 分支**（agent_loop:955 vs 1059），「合流」要改控制流，需防双重 LLM 调用。路径清晰但触碰护城河（verify gate） | 依赖 1.1 goal_text ✅ |
| **2.4** 外部 evaluator | ✅ 中 | 「高后果目标」判据未定义（改钱/改文件/不可逆——谁打标？哪一层判定触发？）。成本护栏方向对，但触发条件要可程序化 | 依赖 2.3 ✅（plan §6 未画此依赖，隐藏依赖见下） |
| **3.1** goal/decision/constraint 记忆 | ✅ 高 | self-audit 给了 `_CATEGORY_DECAY` 加 3 类 + extract prompt 正向引导 + goal_store 双写 fact 的完整路径。**与 1.1 的双写契约**要对齐（一个管活跃、一个管历史） | 与 1.1 协同 ✅ |
| **3.2** 人格画像主动注入 Component | ✅ 高 | self-audit 给了新 `preference_profile.py` Component (priority=85) 路径；出厂开/flag 待定（plan 自己也标 🟡） | 读 3.3 活跃偏好 ✅ |
| **3.3** Pin + PreferenceMemory 衰减 | ✅ 高 | self-audit 给了 `pinned` 列 + decay_rate=0 路径，清晰 | 无强依赖 ✅ |
| **3.4** 写入分级 light 快路 | ✅ 高 | `skip_embed` 参数加在 `append_message`/VectorWorker 入口，路径清晰 | 无强依赖 ✅ |
| **4.0** 接通 compaction | ✅ 高 | **关键前置**。self-audit 证实 agent_loop 无 `should_compress` 调用（已抽查确认 0 匹配），compaction 只活在测试里。接入要保证 assemble() 在 compress 后仍跑（skill_prelude 重建）。改动触碰主 loop，回归面大 | 4.1/4.2 的前置 ✅ |
| **4.1** 二级披露做实 | ✅ 中 | embedding 相似度匹配 + budget 溢出丢最少用，路径清晰 | 依赖 4.0 ✅ |
| **4.2** compaction 后 skill 重挂 | 🟡 中 | self-audit P4 指出：因 assemble() 每轮重建，compaction 接入后「重挂」**可能本就不缺**——4.2 要先确认 4.0 接入方式是否绕过 assemble，否则是空 WI | 依赖 4.0 ✅ |
| **4.3** 技能自创闭环 | 🟡 **低（改动面最大）** | 跨 loader（生成 SKILL.md+热加载）/ store（SkillMemoryStore 接通）/ UI（确认卡）/ 权限门 四层，self-audit P5 证实 SkillMemoryStore 与 SkillLoader **完全解耦**。一行验收撑不起。需拆子任务 | 依赖 P0-1 路径记录（哪些工具按序调用+成功判定）——**但 1.x 没有 WI 明确产出"工具调用路径录制"**，这是**隐藏依赖**（见下） |

### 3.1 依赖关系审查（§6 执行顺序）

- **无环**：1.1 → {2.x, 3.1, 1.3/1.4/1.5} → 抗漂移闭合；4.0 → 4.1/4.2 → 4.3。DAG 成立。
- **隐藏依赖 1（关键）**：**4.3 依赖「工具调用路径录制 + 成功判定」，但 P0-1 没有任何 WI 显式产出它。** plan §5 依赖栏写「4.3 依赖 P0-1 的任务路径记录」，但 1.1-1.5 没有一个 WI 叫「路径录制」。1.2 任务图最接近，但任务图记的是「任务依赖/状态」，不是「哪些工具按什么顺序调用成功」。**这条依赖在 v2 要么补一个录制 WI，要么把 4.3 降级为「先只做手动确认固化、路径录制 defer」。**
- **隐藏依赖 2**：2.4 外部 evaluator 实际依赖 2.3（接 goal_text 后才有「原目标」给 evaluator 对照），但 §6 的依赖图只画了「2.3 依赖 1.1」，**未画 2.4←2.3**。并行切分时若派 2 内部不串好，2.4 会拿不到 goal_text。
- **隐藏依赖 3**：1.3 re-anchoring 接 4.0「压缩保留活跃 goal」——plan 4.0 验收写了「压缩保留 pending tasks + 活跃 goal(接 1.3)」，即 **4.0 反向依赖 1.3**。但 §6 把 P1-4 排在最后「相对独立」，与「4.0 需要 1.3 的 goal 注入逻辑」矛盾。需澄清：是 1.3 先做好 re-anchor 注入点，4.0 接入时复用？还是两者要协同设计？

### 3.2 跨 Phase 契约（goal_text 在 1.1→2.3→3.1 间传递）

**这是全 plan 最薄弱、最该在 v2 补的环节。** 当前 plan 只有「约定契约后并行」一句话，没有：

1. **`SessionGoal` / goal_text 的字段 schema**：1.1 产出 `{session_id, text, subgoals[], progress, criteria, status, set_at, iterations_used}` 中哪些是本轮建？2.3 读 `text` 还是读 `text+criteria` 做对照？3.1 双写 fact 时映射哪些字段？
2. **「活跃 goal（1.1 store）」vs「历史 goal fact（3.1 facts）」的双写一致性契约**：谁是 source of truth？set 目标时双写还是 store 单写、3.1 异步抽取？done 后怎么从 active 转 historical？
3. **goal_text 注入点的统一约定**：1.3（压缩前 system 注入）/ 1.4（charter 注入）/ 1.5（resume 注入）/ 2.3（verify 对照）四处都要 goal_text，**注入格式应统一**（如 `[当前目标] {text}`），否则四处各写一版 = 契约漂移温床。

**建议**：v2 plan 加一节「§X goal_text 契约（schema + 注入格式 + 双写规则）」，作为派 1/派 2/派 3 并行前必须先锁的接口，配 `scripts/e2e_goal_contract.py` live smoke 兜底（对接 `feedback_cross_layer_contract`）。

### 3.3 「字节级契约 + flag 渐进点亮」+「真 E2E」纪律覆盖

- **flag 点亮**：plan 锁定了 2.x 后 verify_gate prod off→shadow（已抽查 config.py 现有 `run_build && off → 自动 shadow` 不变式，新决策与之兼容）。但 **3.2 人格注入出厂开/flag、4.1/4.3 出厂默认值 plan 自己标 🟡 待定**——这些都该在 spec 时显式决策，plan 已留口子，可接受。
- **真 E2E 纪律**：§8 整体覆盖（windows-mcp + 截图 + 日志 + pass^k + live smoke + 更新 STATUS）。但**逐 WI 没有标"哪些 WI 必须真机 E2E、哪些纯后端单测够"**。3.3/3.4/2.1 这类纯后端逻辑可不上 windows-mcp；1.1/1.3/2.3/4.3 触碰用户可感行为必须真机。建议 v2 给每个 WI 标 E2E 等级，避免「全部 windows-mcp」过度或「全部跳过」走捷径。

---

## 4. 风险与排期建议

### 4.1 最高风险 WI（改动面 / 回归 / 护城河）

| WI | 风险等级 | 风险点 |
|---|---|---|
| **4.0 接通 compaction** | 🔴 高 | 改主 agent_loop 控制流，compaction 是「已建未点亮」，接入即影响**所有长会话**。回归面最大。self-audit 证实生产路径未挂载，一旦接入要全回归长对话/skill_prelude/goal re-anchor 三处交互 |
| **2.3 verify 接 goal_text** | 🔴 高 | **直接触碰护城河**（goal-completion 校验闭环，plan §1 红线1）。要合并两个独立 if 分支，改错=伪完成漏过或真完成被误拦，且 prod 即将 off→shadow 放大暴露面 |
| **2.2 verify 真重规划** | 🔴 高 | 三套重试上限叠乘 → 死循环 / 本地资源耗尽风险；触碰 verify gate |
| **4.3 技能自创** | 🟠 中高 | 改动面最大（4 层）；「桌宠乱造技能」反模式风险（已用确认门缓解）；热加载新 SKILL.md 的安全面 |
| **1.1 Durable Goal Store** | 🟠 中 | 改动面小但是契约源头，schema 定错全线返工。新增 SessionDB 表需 migration |
| 3.x / 1.4 / 1.5 | 🟢 低 | 增量加 Component / 加参数，回归面小 |

### 4.2 §7 数字伴侣红线落点核查（不止口号）

| 红线 | plan 声称落点 | 实际有无具体 WI 落点 |
|---|---|---|
| 1 人格只作用交互、完成判定客观 | WI-2.3「人格层不得介入完成判定」 | 🟡 **半落点**：2.3 提了，但「人格层不介入」需要 verify/goal_checker 的 prompt **物理不注入人格块**——3.2 人格注入 Component 要显式排除 verify 路径。**这条交叉约束（3.2 × 2.3）plan 没点明**，是 silent 风险 |
| 2 拒绝谄媚式完成 | （§7 列了但无 WI） | ❌ **无落点**。该做成 verify/evaluator 的反谄媚校验项或 prompt 约束 |
| 3 主动性带价值不成瘾 | 本轮不做主动性 | ⏸ 合理搁置；但 3.2 注明「人格注入不得用于最大化粘性」无技术落点 |
| 4 不优化在线时长反指标 | （§7 列了但无 WI） | ❌ **无落点**。属指标/埋点纪律，至少该写「不新增在线时长优化型埋点」 |

**结论**：§7 红线**只有 1 条半落到 WI**，其余停在口号。plan 自己承诺「显式写进 verify gate 与人格注入，不止停在 CLAUDE.md 文字」——这个承诺当前**未兑现**。

### 4.3 工作量粗估与排序（利于增量交付）

plan §6 顺序总体合理（地基先行、人格并行、skills 最后）。给**利于用户自己分阶段增量交付**的微调建议：

**阶段 A（地基 + 单点速赢，1-2 周）**：
1. **1.1 + 契约节（§3.2 建议的 goal_text schema）** —— 必须最先，且先锁契约再写代码。
2. **3.3 Pin / 3.4 light 快路 / 3.1 记忆 category** —— 纯后端、低风险、与 1.1 协同，可作早期「看得见的交付」提振信心。
3. 补 G1 gitignore-aware 自查（半天速赢）。

**阶段 B（抗漂移闭环 + 自我纠错，2-3 周）**：
4. **1.3 / 1.4 / 1.5**（依赖 1.1）。
5. **2.1 → 2.3 → 2.2 → 2.4**（注意 2.2 上限合并先设计再实现；2.3 触护城河要 pass^k）。
6. **3.2 人格注入**（与 2.3 协同处理红线1交叉约束）。

**阶段 C（高风险大件，单独冲刺，2-3 周）**：
7. **4.0**（单独一个冲刺，全回归长会话）→ 4.1 → 4.2。
8. **4.3**（拆 ≥4 子任务，最后做；先做手动确认固化，路径录制 defer）。

**排序建议要点**：把 **4.0（最高回归风险）单独隔离**，不要和地基混做；把 **3.x 低风险项提前**给早期交付感；**2.2 的预算账本设计**列为 2.x 的前置 spike。

---

## 5. 必须在 v2 plan 里修掉的 Top-N 问题（按优先级）

| # | 问题 | 严重度 | 修法 |
|---|---|---|---|
| **1** | **goal_text 跨 Phase 契约未定义**（schema + 注入格式 + 1.1/3.1 双写一致性）。并行开发头号翻车点 | 🔴 必修 | 新增「§X goal_text 契约」节，锁 `SessionGoal` 字段、统一注入格式 `[当前目标]{text}`、双写规则；配 `scripts/e2e_goal_contract.py` live smoke。**派 1/2/3 并行前先锁** |
| **2** | **2.2 重试预算未给统一账本**（circuit 3 + auto_resume 2 + verify_nudges 2 + 新重试 → 叠乘失控） | 🔴 必修 | 2.x 加前置 spike：画一张「全局重试预算账本」，定义新重试如何嵌进现有上限，给死循环上界测试 |
| **3** | **4.3 隐藏依赖「工具路径录制」无 WI 产出** + 改动面最大却一行验收 | 🔴 必修 | 要么 P0-1 补「路径录制」WI，要么 4.3 拆子任务并把路径录制显式 defer（本轮只做手动确认固化） |
| **4** | **§7 红线 2/4 无 WI 落点 + 红线1 的 3.2×2.3 交叉约束未点明** | 🟠 高 | 给红线2（反谄媚完成校验）落进 2.3/2.4 prompt 约束；红线4 写「禁新增在线时长埋点」；3.2 显式排除 verify 路径注入人格 |
| **5** | **6 处 silent drop 未表态**（plan mode 只读 / `/verify` skill / auto-memory / 记忆 nudge / TokenJuice / .md vault / 审批聚合 / gitignore-aware） | 🟠 高 | 加「§Y 显式不做 / defer 清单」，每条一行理由（见 §2.6 G1-G10）。区分「合理不做」与「忘了」 |
| **6** | **1.2 任务图落在哪套 todo 未定**（三套结构 + TeamStore 生命周期冲突） | 🟠 高 | spec 前定：在 code_todos 上扩展依赖图？还是新建统一 goal-task 表？TeamStore「建后即销」与「跨 agent 持久共享」如何调和 |
| **7** | **逐 WI 缺 E2E 等级 + pass^k 参数**（k 取几、哪些 WI 必真机） | 🟡 中 | 每个 WI 标「纯后端单测 / 必须 windows-mcp 真机」；pass^k 指定 k 值 + 哪些关键能力（1.1 持久、2.2 重规划、2.3 verify）必测 |
| **8** | **隐藏依赖 2.4←2.3、4.0↔1.3 未在依赖图画出** | 🟡 中 | §6 依赖图补这两条边，避免并行切分时断契约 |
| **9** | **1.5 verified-state checkpoint 范围模糊** | 🟡 中 | 明确 1.5 是「窄版只接 goal_text」，任务级 checkpoint 显式 defer（G6） |

---

*报告路径：`G:\projects\deskpet\plans\2026-06-04-goal-completion-upgrade\05-evaluation-report.md`*
*评审基于只读代码核对，关键事实（goal_store 内存态 / agent_loop 无 should_compress / verify_gate_mode=off）已抽查确认属实。*
