# 任务漂移 v2：根因再诊断 + 业界对标(Hermes/OpenClaw/OpenHuman/SOTA) + 最佳修复方案

> **日期**: 2026-06-21　**状态**: 📐 调研定稿，待实现
> **触发**: 2026-06-21 fan-out 真机又复现 2 次漂移（固态电池→钠离子、区块链→宁德/CATL），见 [复现交接](../2026-06-21-task-drift-reproduction-handoff.md)
> **一句话**: 之前的软修复（Tier1 锚定 + Tier2 相似度/词法截断 + Fix B 注入原话）**对相邻领域必然失效**，这是 attention-sink + distractor-interference 的架构层问题，**prompt/门控治不了**；业界（OpenClaw / Claude Code / Morph / MemGPT）的共识解药是**结构化任务作用域 + 子代理干净上下文**，不是"自动检测话题切换再截断"。

---

## 1. 问题定性（用业界术语命名 deskpet 的 bug）

三个机制叠加，全是**架构层**问题：

1. **Primacy bias / attention sink（首因偏置 / 注意力汇）**——序列靠前/最频繁的 token 被后续每个 token 注意到，softmax 下 query 无强匹配时注意力"倾倒"到全局可见的早期 token。永续 `default` 会话里高频的"宁德时代/电池"就是这个 sink。〔[Lost-in-the-Middle](https://dev.to/thousand_miles_ai/the-lost-in-the-middle-problem-why-llms-ignore-the-middle-of-your-context-window-3al2)、[Morph Context Rot](https://www.morphllm.com/context-rot)〕
2. **Distractor interference（干扰项干扰）**——"语义相似但不相关"的内容**主动误导**模型，是最坏情况。**这精确解释了我们"软修复对相邻领域失效"**：相似度门控截断旧历史时，"区块链/固态电池"与"宁德/钠离子/电池产业链"在 embedding 空间太近，门控**放行**了旧历史 → 旧主题继续当吸引子。〔[Morph](https://www.morphllm.com/context-rot)〕
3. **Context rot 自增强**——降质输出触发更多补救动作，每次又往上下文塞更多 → 永续会话越长越糊。〔[Redis](https://redis.io/blog/context-rot/)、[Salesforce](https://www.salesforce.com/artificial-intelligence/ai-context/context-rot/)〕

### 我们的代码层实证（坐实"软修复治不了"）
- **词法兜底对相邻领域 = False（不截断）**：`_lexical_topic_shift("帮我深度调研 固态电池…", 钠离子L2)` 实测返回 `False`——固态电池↔钠离子共享"电池/产业化/技术"等词，重叠 ≥15% → 判同主题 → 不截断 → 漂移漏网。（跨域"区块链 vs 宁德" = True 能抓，但**相邻领域是死穴**。）
- **embedding 主路在真机恒超时**（`task_drift_sim_skip reason=encode_timeout`，BGE-M3 subprocess 被 vector-worker 抢锁撞 1500ms 组件 budget）→ 永远退化到词法 → 死穴常态化。
- **上游 topic 漂的精确位置**：`research_tools.py:_PLAN_PROMPT` 同时喂 `ORIGINAL USER REQUEST`(对) + `REFINED TOPIC={topic}`(LLM 漂掉的，如"宁德时代2024年报")，而 sub_questions、报告骨架 `# {topic}`、文件名 slug **全用漂的 `{topic}`**。所以即使 Fix B 把 user_request 注入了 prompt，**topic 字段夺不回主导权 → 报告/6 份 fanout 子问题仍是宁德的**。

> **结论**：漂移**不是 prompt 工程能软修的**。Morph 原话："post-hoc compaction 解决不了根因，因为伤害在压缩触发前已经造成。"——这正是我们 Fix A/B + Tier2 失效的理论原因。

---

## 2. 三方对标（Hermes / OpenClaw / OpenHuman）

| 系统 | 定位 | 会话/上下文模型 | 对 deskpet | 角色 |
|---|---|---|---|---|
| **Hermes**（Nous Research，[docs](https://hermes-agent.nousresearch.com/)） | 自托管持久 agent | **单一长会话 + 双层压缩**（50%/85% 阈值、四阶段、结构化摘要），**不做 task split / thread isolation** | **同病样本**：架构与 deskpet 现状一样。mem0 实测确认压缩会**静默丢跨任务依赖/硬约束/数值** | **证伪纯压缩路线** |
| **OpenClaw**（[docs](https://docs.openclaw.ai/)） | 多 harness 控制面 | **source-based session keying**：main/**isolated**(`cron:<jobId>`跑完即弃)/named；sub-agent 默认 `context:"isolated"` 干净子 transcript | **解药样本**：原话"**tool arguments derive from the bounded task description, not ambient parent-session context**" | **直接命中我们的 bug** |
| **OpenHuman**（[memory-tree.md](https://github.com/tinyhumansai/openhuman/blob/main/gitbooks/features/obsidian-wiki/memory-tree.md)） | 本地桌面个人 agent（同品类！） | **记忆按 topic（实体）切独立树** + L0/L1/L2 级联 + **按 scope 定向检索**（搜单源/下钻 topic/拉 global），**不把永续历史端给 LLM** | **同品类参照**：记忆按 topic 分片 + 按当前 query 的 topic-scope 取片 ≠ 单一历史 + 相似度门控 | **记忆侧分片样本** |

**OpenClaw 的决定性机制链**（直接抄）：默认 isolated → 子只看到自包含 `[Subagent Task]` 描述 → 生成工具参数（deepresearch 的 `topic`）时**物理上没有旧主题在 context 里 → 不可能漂**。子输出当"报告/证据"回灌，**不能当用户指令覆盖 policy**。

> OpenHuman 缺口（诚实）：对话**会话边界如何切**、topic-scope **由谁选定（用户显式 vs 自动）**未在文档实证（只覆盖记忆侧）；落地前需读其 retrieval/scope 源码。

---

## 3. 业界 SOTA：任务隔离怎么做

- **编码 agent 全是显式/半显式任务边界**：Aider `/clear`（纯手动换任务清历史保文件）、Cursor（消息多了**提示开新 chat**）、Claude Code（compaction + clearing tool results + **sub-agent 隔离** + 外部 note-taking）。**没有谁把"运行时自动 embedding 截断"当主力。**〔[Anthropic 上下文工程](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)、[Aider](https://aider.chat/docs/usage/commands.html)、[Cursor](https://forum.cursor.com/t/manually-start-new-chat-with-context/109731)〕
- **MemGPT / Letta**：永续历史进 **external context（disk）**，平时**不在 main context**，靠显式 function call **按需 page-in**。从根上消灭"旧主题常驻窗口顶部当 sink"。〔[arXiv 2310.08560](https://arxiv.org/pdf/2310.08560)〕
- **Claude Code sub-agents / Morph**：子代理**全新隔离 context window**，烧几万 token 探索只回 1–2k 蒸馏摘要；Morph 明断"**预防优于事后清理**，首选 subagent 架构做 context 隔离"。〔[sub-agents docs](https://code.claude.com/docs/en/sub-agents)、[Morph](https://www.morphllm.com/context-rot)〕
- **反例警示**：CrewAI/AutoGen 默认**共享 memory + 上下文向前携带**，隔离要**主动配**——"用了 multi-agent"≠"自动隔离"。〔[CrewAI](https://docs.crewai.com/en/concepts/memory)〕

### ★ 核心结论：自动检测 vs 显式切分 → 业界主力是**显式/结构化作用域**，自动 topic 检测只兜底
自动检测有**两类错误**：漏检（旧主题继续污染 = 我们现在的 bug）+ 误检（把同任务的合法跟进当新主题、错丢上下文）。桌宠"用户随口换话题"高频场景两类代价都高。**结构化作用域无这两类错误**（用户/系统在边界显式开新 scope，或 subagent 从结构上拿不到旧历史）。我们已用真机实测**证伪了自动相似度路线**（distractor interference 击穿相邻领域）——这正是业界放弃"自动悄悄截断"的同一个原因。

---

## 4. deskpet 最佳修复方案（分层，按 ROI×证据强度排序）

> 总原则：**不在主会话层做脆弱的相似度门控**（已证伪），改走**结构化任务作用域 + 子代理干净上下文 + 工具参数派生自用户当前原话**。

### T0 — 立刻能上、证据最硬、直击根因（最高 ROI）

**T0-1：deepresearch 工具参数严格派生自用户原话，夺回 topic 主导权（小改、低风险、直接堵报告漂）**
- `research_tools.py`：plan/sub_questions/报告骨架/slug **以 `user_request`（用户原话）为唯一主题源**，把 LLM 给的 `topic` 降级为"可选提炼，与原话冲突即丢弃"。
  - `_PLAN_PROMPT`：sub_questions **基于 `user_request`**，明确"REFINED TOPIC 若与 ORIGINAL USER REQUEST 冲突，以后者为准"。
  - 报告标题 `# {user_request}`（或其提炼）而非 `# {topic}`；落盘 slug 用 user_request。
  - fanout 6 子问题基于 user_request 派生。
- 命中 OpenClaw 圣杯"tool args derive from bounded task description"。**风险极低**（user_request 已由 Fix B 在 dispatch 注入，拿得到）。

**T0-2：deepresearch fanout 子代理用干净上下文（利用已有架构，圣杯路径）**
- spawn 研究子代理时**只注入"用户当前原话 + 当前子问题/任务目标"，绝不继承主线永续历史**；主 agent 只收子代理的蒸馏摘要。
- 对标 Claude Code sub-agents 默认隔离 + OpenClaw `context:"isolated"` + Morph"预防优于清理"。和 deskpet 2026-06-21 已上线的 fanout 架构**天然契合**，差的就是"默认 isolated context"这一条。
- 需核查现状：当前 fanout 子代理是否继承主会话历史？若继承 → 改为隔离即根治"6 份错主题报告"。

**T0-3：废弃/默认关 Tier2 相似度截断，主会话层只保留 Tier1 廉价锚定**
- Tier2（embedding/词法相似度截断旧 L2）已被实测 + 业界双重证伪（distractor interference），**默认关或移除**，避免"看似在防漂、实际相邻领域漏 + 偶尔误截追问"的负价值。
- Tier1（当前请求优先锚定 nudge）成本极低、对**闲聊回复**防漂仍有边际价值，**保留**（但别指望它根治）。

### T1 — 中期架构升级（根治会话污染，证据强）

**T1-1：按任务切分会话作用域（D1，但用结构化信号而非 embedding 检测）**
- **不靠"自动 topic 相似度检测"**（已证死穴）。改用**结构化信号**：
  - **显式**：桌宠加"新话题/`/new`"开关 + idle/daily reset（OpenClaw 式 `sessionStartedAt`/`idleMinutes`）。
  - **半结构化**：**完整祈使式自包含请求**（"帮我深度调研 X"这类自带完整主题的命令）= 强任务边界信号 → 该轮起新 scope / 不注入 raw 旧历史（靠句法结构，不靠语义相似度，抗 distractor）。
- 兼容多窗口共享 `default` fan-out（`main.py:3173`）。可复用 `code_mode/state.py` 的 session 切分模板做 `TaskSessionManager`。

**T1-2：永续历史降级为 external memory（MemGPT/Letta 式）**
- 旧历史**不再常驻 prompt 顶部**，进 external/recall storage，按需 page-in。从根上消灭 attention sink。deskpet 已有 L1/L2/L3 记忆分层 + 召回，**地基已在**，差"L2 不默认直灌、改按相关性 page-in"。
- 借 OpenHuman **topic-tree 分片 + scoped 检索**：记忆按 topic 切片，处理某请求按当前 topic-scope 取片（读其 [memory-tree.md](https://github.com/tinyhumansai/openhuman/blob/main/gitbooks/features/obsidian-wiki/memory-tree.md) 源码）。

### T2 — 仅作辅助（单独用会翻车）
- 若保留自动 topic-shift 检测，用 **LLM 分类器**判"新主题/意图切换"（比纯 embedding 抗 distractor），但**绝不单独依赖它做截断**——只当多信号之一的兜底。〔[arXiv 2505.07852](https://arxiv.org/html/2505.07852v1)〕

---

## 5. 落地优先级建议
1. **先上 T0-1 + T0-2**（deepresearch 原话夺权 + fanout 子代理隔离）——小改、低风险、直接消灭"研究漂主题 + 6 份错报告"这个最可见症状，且和现有 fanout 架构契合。
2. **同时 T0-3**（关 Tier2，止损负价值软门控）。
3. **再规划 T1-1/T1-2**（会话作用域 + external memory）作为根治，用**结构化/显式信号**（不要再投自动相似度检测）。
4. 真机验证口径（沿用复现方法）：发与近期历史无关的新主题（Rust/区块链/固态电池），grep backend log 看 **LLM 生成的 deepresearch `topic` 参数** + **落盘报告文件名 slug** 是否=你发的主题；**不要**只看 Fix B 锚点（它会触发但治不了）。相邻领域（固态电池↔钠离子）必须专门复验。

---

## 6. 证据强度
- **一手/源码级**：OpenClaw docs、OpenHuman memory-tree.md、MemGPT arXiv、Anthropic 官方上下文工程博客、Aider/Cursor/Claude Code 官方文档、Hermes 官方压缩文档；deskpet 代码层实测（词法兜底=False、_PLAN_PROMPT 用漂 topic、encode_timeout）。
- **二手但彼此印证**：Morph/Redis/Salesforce context-rot、mem0 压缩对比、各 multi-agent 对比博客。
- **缺口**：OpenHuman 对话会话边界/topic-scope 选定方式未实证（需读源码）；Hermes coordination/subagent 隔离强度说法不一（倾向弱）；Devin session 策略未找到一手源。

> 完整子代理调研原文（带全部 URL）见本会话记录；本文件为综合定稿。
