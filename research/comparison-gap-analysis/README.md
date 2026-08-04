# DeskPet 跨项目对比 + Gap 分析（最需要优化的地方）

> 综合 5 份调研（claude-code / openhuman / hermes-agent / cc-haha / 最佳实践）。
> 目的：定位 DeskPet「更好帮用户完成目标」**最需要优化的地方**，给优先级。
> 最后更新：2026-06-04 ｜ 供后续 plan / 实现 agent 复用。

---

## 1. 横向能力对比矩阵

> 图例：✅ 强 / 做到位　🟡 部分 / 浅　❌ 无　— 不适用

| 能力维度 | DeskPet 现状 | claude-code | openhuman | hermes-agent | cc-haha |
|---|---|---|---|---|---|
| **产物级完成校验**(artifact/receipt/verify-gate) | ✅ 已有(护城河) | 🟡 `/verify`+test | ❌ 停发tool即完成 | 🟡 verify思想 | 🟡 diff人审 |
| **目标持久化 / 抗漂移** | 🟡 有`/goal`但缺durable doc | ✅ TODO贯穿compaction | 🟡 记忆树非目标 | ✅ session恢复+nudge | ✅ **Task工具族(依赖/共享)** |
| **自我纠错 / 失败重规划** | 🟡 verify不过就"停" | 🟡 evaluator-optimizer | ❌ | ✅ **agentic JSON-mode必填字段** | 🟡 observe回填 |
| **任务规划结构** | 🟡 计划确认(流程) | ✅ **plan mode(权限级)** | 🟡 4级委派 | ✅ 迭代+预算 | ✅ Plan-Reason-Act-Observe |
| **记忆-检索质量** | 🟡 BGE-M3向量为主 | 🟡 CLAUDE.md+auto | ✅ **五路混合打分** | 🟡 FTS5+摘要 | 🟡 git-first上下文 |
| **人格 / 关系演化** | 🟡 偏静态人格 | — | ✅ **PROFILE半衰期7–90天** | 🟡 SOUL.md+Honcho | — |
| **技能系统** | 🟡 14静态builtin | ✅ **渐进披露三级** | 🟡 | ✅ **自创闭环** | 🟡 |
| **主动性(proactivity)** | 🟡 supervisor有限 | 🟡 Routines/loop | 🟡 定时同步(伪主动) | ✅ cron无人值守 | ✅ 定时任务 |
| **确认门 / 审批 UX** | 🟡 后端权限+熔断 | ✅ deny-first+mode | 🟡 | 🟡 | ✅ **集中式审批界面** |
| **硬机制强制(非软指令)** | 🟡 codingsys 5 hook | ✅ **hook exit2阻断** | ❌ | 🟡 | 🟡 |
| **全本地语音+Live2D** | ✅✅ 独有护城河 | — | 🟡 云TTS+脸 | ❌ | ❌ |
| **Computer Use** | ✅ SendInput(更强) | 🟡 | 🟡 CEF | ❌ | 🟡 pyautogui(更弱) |
| **反谄媚 / 不假完成纪律** | ✅ 已有HARD纪律 | ✅ 客观证据 | ❌ | 🟡 | 🟡 |

**一句话读法**：DeskPet 在「**产物校验 + 全本地语音 + 不糊弄自己纪律**」三项已领先所有对标项目；
短板集中在「**目标持久化/抗漂移、失败自动重规划、记忆检索质量、技能动态化、真主动性**」——
而这几项恰恰是「把目标真办成」最吃劲的地方。

---

## 2. DeskPet 已领先的护城河（守住，不要为抄而抄削弱）

1. **goal-completion 产物校验闭环**：last-mile artifact/receipt/verify-gate 是 openhuman/hermes/cc-haha
   **全都没有**的能力——它们都是「模型不再发 tool call 就算完成」。这是 DeskPet 对标里最硬的差异化。
2. **全本地语音管线 + Live2D 表达力**：4 个对标项目无一覆盖。openhuman 的 mascot 只是「会说话的脸」+ 云 TTS。
3. **Computer Use 鲁棒性**：DeskPet 的 windows-mcp/SendInput 圣杯方案在 WebView2/Chromium 下优于
   cc-haha 的 pyautogui（老式 mouse_event 在现代 webview 失效）。
4. **「不糊弄自己 / 真 E2E ≠ 脚本回放」纪律**：与最佳实践调研的「反谄媚式假完成」结论同源，
   是数字伴侣里最该坚守、却最容易被牺牲的东西（Replika/Character.ai 血泪教训）。

> ⚠️ 元判断：抄对标项目时，凡可能削弱以上 4 条的，**优先级一律下调**。

---

## 3. 最需要优化的 5 个方向（优先级矩阵）

> 排序依据：①是否直击「帮用户完成目标」核心诉求　②DeskPet 现状缺口大小　③ROI（投入/收益）　④风险。

### 🔴 P0-1 目标持久化与抗漂移（地基，直击核心诉求）

- **问题**：DeskPet 有 `/goal` + 多 agent，但目标更像「一次性指令」，缺**持久目标文档**与**任务图**。
  最佳实践调研钉死：长链路里**所有模型都会 goal drift**，多 agent 还有「继承漂移」（父吃了子的漂移输出）。
- **要补的机制**（多源汇聚）：
  - **Durable goal document**（最佳实践§4.2）：目标存成持久 md，在**每个决策点 / context 压缩前 re-anchoring 重读校验**；跟踪 `GD_actions`/`GD_inaction` 漂移信号。
  - **Task 任务图**（cc-haha §4.1）：`TaskCreate/Update/List/Get` 带**依赖关系 + 跨子 agent 共享状态**，替代扁平 todo，作为 `/goal` 的执行层。
  - **handoff goal checkpoint**（最佳实践§4.2 成因3 + claude-code）：派子 agent 时显式带目标声明，回收输出前先按目标过滤——直接防 DeskPet 多 agent 的继承漂移。
  - **中断/恢复**（hermes 借鉴5）：长任务可从「最后 verified state」续跑，不是从头来。
- **DeskPet 现状差距**：`/goal` 条件/一票否决体系已有（全局 CLAUDE.md），但**任务图是否持久化 + 是否暴露给子 agent 读写 + 压缩前是否 re-anchor** 需补强。
- **ROI**：高。直接提升长任务完成率与稳定性，且与已有 `/goal`、记忆栈、verify-gate 能拼接。

### 🔴 P0-2 自我纠错闭环（从「校验不过就停」到「自动重规划重试」）

- **问题**：DeskPet verify gate 偏「**产物层校验**」（生成对不对），校验不过目前更像「**停下报错**」，
  缺「**过程层反思 → 重规划 → 重试**」。最佳实践警告：单 agent **自反思会自我强化错误甚至幻觉出假目标**。
- **要补的机制**：
  - **hermes agentic JSON-mode**（hermes 借鉴1，★最高价值）：在执行步**强制**产出结构化字段
    `error_analysis / execution_critique / task_replanning`，而非自由文本反思。
    → verify 不过时由 `task_replanning` 驱动**自动改方案重试**，而非直接甩错给用户。
  - **verify 重述原目标对照产物**（最佳实践§3.2）：防 verifier 自己漂移 / 被 self-reflection 幻觉带偏；完成判定**只认 artifact/receipt 客观证据**。
  - **外部验证 > 自我验证**（最佳实践§3.1）：必要时多 persona 交叉批判，抵消单 agent 漂移（DeskPet 多 agent 现成可用）。
- **DeskPet 现状差距**：verify-gate/receipt 已有（结果校验），但缺「校验不过→结构化重规划→重试」的循环字段。
- **ROI**：高。把「能发现没办成」升级为「发现后自动补救」，是完成率的第二级跳。

### 🟠 P1-3 记忆工程深化（低成本高收益 + 伴侣感差异化）

- **问题**：DeskPet 记忆以 BGE-M3 向量为主；openhuman 的记忆工程是其最强项，且最佳实践指出
  「存对话 < 抽取目标/决策/约束」。
- **要补的机制**：
  - **五路混合检索打分**（openhuman B2，★低成本高收益）：graph + vector + keyword + episodic + freshness 加权融合，显著提升相关性（DeskPet 当前主要靠向量，漏精确/新近事实）。
  - **PROFILE.md 人格画像 + 偏好半衰期衰减**（openhuman B3，★伴侣感命脉）：偏好按 7–90 天半衰期衰减 + 用户可 Pin/Forget，让人格**随时间演化又可被钉住**——这是 DeskPet「数字伴侣」差异化的关键，openhuman 自己也只做了偏好、没做情感弧线，DeskPet 可做更深。
  - **写入分级**（openhuman B4）：高频流（截屏/语音 tick）走 light 路径**跳 embedding**，防记忆膨胀。
  - **周期性记忆 nudge**（hermes 借鉴3）：周期性问 agent「刚发生的有什么值得长期记住」，由 LLM 自决，补规则抽取漏掉的「非显然但重要」信息。
  - **记忆转向抽取目标/决策/约束 + 多 scope 打标**（最佳实践§4.3）：让上一会话的目标在后续可检索。
- **DeskPet 现状差距**：向量召回为主、人格偏静态、写入未必分级。
- **ROI**：中高。五路检索 + 写入分级是工程量小的速赢；人格半衰期是差异化大招。

### 🟠 P1-4 Skills 从静态 → 分级披露 + 自创闭环

- **问题**：DeskPet 14 个 builtin skill 是**人写的静态**技能，且常驻语音桌宠 context 极宝贵。
- **要补的机制**：
  - **渐进式披露三级**（claude-code 4.1，★对常驻桌宠是必修）：启动只载 name+description（带字符预算）→ 触发载正文 → 附件按需读；compaction 后按预算重挂。DeskPet 大概率只做了「能加载」没做「省 context 分级」。
  - **技能自创闭环**（hermes 借鉴2）：完成多步目标后，按触发器（≥5 工具调用/从错误恢复/被纠正/非显然 workflow）自评是否固化成新技能 → 桌宠「越用越会办事」（如「每周生成 PPT 周报」从多步即兴变一键技能）。
- **DeskPet 现状差距**：需自查 skill 加载是否真分级；技能是静态注册，无「从经验自动造技能」闭环。
- **ROI**：中。分级披露护住 context 健康（常驻产品刚需）；自创闭环是长期复利。

### 🟡 P2-5 主动性真做（桌宠差异化命脉，但工程量大）

- **问题**：最佳实践指出主动性是桌宠**差异化命脉**，但 openhuman 的「后台思考」其实是**伪主动**（20 分钟定时同步 + note-taker，不会自己发起 follow-up）。DeskPet supervisor 也有限。
- **要补的机制**：
  - **perceive→decide→act→learn 持续循环 + 价值闸门**（最佳实践§4.1）：桌宠主动跟进目标进度，
    但每次主动前先判「现在介入的**价值密度**」，拒绝无意义推送（「想你了」式 = 成瘾陷阱反模式）。
  - **cron/无人值守推进**（hermes 借鉴6）：定时/事件触发推进长目标。
- **DeskPet 现状差距**：缺「目标驱动的真主动 + 介入时机判断」。
- **ROI**：中长期。差异化价值高，但要小心 §4 反模式，工程量大，排 P2。

---

## 4. ⚠️ 横切红线：数字伴侣特有的反模式（任何方向都要守）

来自最佳实践§6（Replika/Character.ai 血泪教训），与 DeskPet 既有纪律同源，**做上面任何优化都不能违反**：

1. **人格只作用于交互风格，完成判定必须冷静客观** —— 桌宠可以可爱，但**禁止为讨好而假装办成 / 报喜不报忧**。
2. **拒绝谄媚式完成**：低谄媚的 companion 反而提供更好社会支持、提升长期留存——长期价值与短期粘性背离。
3. **主动性带价值、不带成瘾设计**：主动提醒目标进度=价值；无意义推送=成瘾陷阱。
4. **不优化「在线时长」这个反指标**；对高依赖信号保持克制。

> 这条红线正是 DeskPet 相对纯陪伴产品（Replika）的**结构性优势**：它本来就有「真 E2E、不糊弄自己」的工程纪律。
> 优化「帮用户完成目标」时，把这条纪律**显式写进 verify gate 和主动性闸门**，而非只停在 CLAUDE.md 文字。

---

## 5. 其它可选借鉴（非 Top5，记录备查）

- **硬机制 vs 软指令元原则**（claude-code）：逐条审 DeskPet「必须 X」，能变 hook/权限/verify 的别只写进 prompt。
  （DeskPet codingsys 已有 5 hook，方向对；可深化 `Stop`/`PostToolUse` exit2 阻断「没验证就收尾」。）
- **plan mode 作为独立只读权限模式**（claude-code 4.2）：把「计划确认」从流程文字升级成「规划期物理不可能误改文件」。
  需确认 DeskPet 计划确认期是否**真切只读权限**。
- **`/verify`+`/run` 风格 bundled skill**（claude-code 4.3）：把 windows-mcp 真测经验沉淀成 `run-deskpet` skill + 启动配方记录。
- **集中式审批聚合视图 + 实时 diff 小卡片**（cc-haha 4.2/4.3）：桌宠侧轻量「agent 正在改什么」反馈环（非 IDE 式重界面）。
- **TokenJuice 工具输出压缩**（openhuman B6）：tool-output 压缩中间件（HTML→MD/URL 缩短/去重），降本降延迟。
- **`pass^k` 稳定性评测**（最佳实践§7.1）：关键功能不止测一次过，测连续 k 次稳定。

---

## 6. 给 review 的开放问题（待与用户确认优先级）

1. **P0-1 vs P0-2 谁先做**？目标持久化是地基，自我纠错是即时完成率——建议 P0-1 先（给 P0-2 提供 goal state）。
2. **人格半衰期（P1-3）是否提前**？它是「数字伴侣」差异化大招，工程量中等，可能值得插队到 P0。
3. **主动性（P2-5）的边界**：做到什么程度算「带价值不成瘾」？需要产品侧定义介入策略。
4. 是否要先做一轮**「现状自查」**（确认 skill 是否真分级披露 / 计划确认是否真只读 / 工作区采集是否 gitignore-aware），
   再决定哪些是「补缺口」哪些是「已有只是没确认」？—— 建议先自查，避免重复造轮子。
