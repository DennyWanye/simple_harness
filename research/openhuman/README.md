# openhuman 调研

> 调研对象: [tinyhumansai/openhuman](https://github.com/tinyhumansai/openhuman)
> 调研日期: 2026-06-04
> 调研目的: 为 DeskPet（本地桌宠数字伴侣）对标优化提供借鉴。重点看人格 / 记忆 / 情感 / 主动性 / 长期关系 / agent loop。

---

## 1. 项目概览

| 维度 | 内容 |
|---|---|
| **是什么** | 开源的「个人 AI 超级智能体」桌面助手（agentic assistant），定位 *"Private, Simple and extremely powerful"*。本质是一个**桌面级 agent harness + 桌宠 mascot**，强调本地记忆 + 必要时托管服务。 |
| **谁做的** | 组织 **Tiny Humans**（github.com/tinyhumansai）。 |
| **定位** | 解决 agent 的 **cold-start（冷启动）问题**——不靠慢慢学，而是一上来就把你的邮件/日历/repo/文档/消息全部接入并压缩进本地记忆，让 agent 立刻"懂你"。 |
| **Star / 活跃度** | **30.7k star**，3k fork，2802 commits（main）。最新 release **v0.57.13（2026-06-03）**，状态 *"Early Beta — Under active development"*，**非常活跃**。 |
| **许可证** | **GPL-3.0**（copyleft，商用对标需注意）。 |
| **技术栈** | **Tauri/Rust (62%) + TypeScript (35%) + React**。Node 24+、pnpm、Rust 1.93、CEF（Chromium Embedded Framework，用于 Meet agent）。可选 Ollama 本地推理。集成层用 Composio 做 OAuth/工具代理。 |
| **平台** | macOS / Windows / Linux 桌面。原生包（Homebrew / apt / AUR / MSI）。 |

**与 DeskPet 的关系**：技术栈高度同构（都是 Tauri+Rust+React+Python 风格的本地桌面 agent，都有桌宠 mascot + 语音 + 本地记忆 + 工具调用）。但定位差异明显：**openhuman 是「生产力 agent harness（接 118 个 SaaS 帮你干活）」，DeskPet 是「有人格的数字陪伴 + goal-completion」**。openhuman 的"陪伴/人格"是薄的（mascot 主要是个会说话的脸），它的硬核在**记忆工程 + 冷启动 + 多 agent 委派**。

---

## 2. 核心架构与关键设计

### 2.1 记忆系统（openhuman 最强的部分 ★）

三层 namespace 存储 + 分层摘要树，是 DeskPet 最值得抄的模块：

- **Memory Tree（记忆树）**：内容切成 **≤3k-token 的 Markdown chunk**，打分（scored），**折叠成分层摘要树（hierarchical summary trees）**，存在本机 **SQLite**。灵感来自 Karpathy 的知识管理工作流。
- **Obsidian 式 vault**：用户可直接编辑的 `.md` 文件，人可读、可改、可版本化。
- **三种存储层（UnifiedMemory）**：
  1. **Documents** — 全文 + embedding（语义检索）
  2. **Key-Value** — 结构化元数据/计数器（精确查）
  3. **Knowledge Graph** — 从文档自动抽取的 entity-relation 三元组
- **写入分级（关键工程设计）**：
  - `put_doc()` — 普通文档，做 embedding + **后台异步图谱抽取**
  - `put_doc_light()` — 高频临时数据（截屏、tick）**跳过 embedding**，避免瓶颈
  - `ingest_doc()` — 全同步管线，需要立即用抽取结果时才走
- **检索分级**：`query_namespace()`（语义，返回**已格式化好可直接塞进 prompt** 的文本）/ `recall_namespace()`（无 query 的时序召回）/ `kv_get()` / `graph_query()`。
- **混合打分 `RetrievalScoreBreakdown`**：融合 **graph + vector + keyword + episodic + freshness** 五路信号加权。
- **抽象边界纪律**：外部只准用 `MemoryClient`，禁止直接调 `UnifiedMemory`。

### 2.2 人格 / 自学习系统（PROFILE.md）★

openhuman 的"人格"不是预设性格脚本，而是**从用户行为持续抽取偏好并固化**：

- **personalization cache** → 物化成可编辑的 **`PROFILE.md`**，每次交互注入 system prompt。
- **5 个偏好生产者（producers）**：
  1. 从邮箱/Slack/Notion 账户字段抽身份
  2. 邮件签名分析身份信号
  3. 启发式探测器（消息长度、编辑模式、改写）→ 风格偏好
  4. LLM 反思 hook → 目标与风格线索
  5. **扩展树摘要（跨所有内容源的 rolling summary）** — 文档称这是 *"long-tail backbone（长尾骨干）"*，**无需新 API 调用**就能学到最泛化的偏好。
- **稳定性打分（persona drift 控制）★**：偏好用**衰减加权公式**打分，平衡证据强度/新近度/用户态（Pinned 钉住 / Forgotten 遗忘）。**class 半衰期 7 天（频道偏好）到 90 天（身份）**——允许自然漂移，又保留用户钉住的意图。
- **缓存层 vs 记忆树分工**：缓存层放**稳定抽象偏好**（verbosity=terse, role=engineer），记忆树做**上下文检索**（具体的人、过往决策、话题线）。
- **用户可对话控制**：直接让 agent "记住 / 忘掉" 某偏好。

### 2.3 情感 / mascot 表达

- mascot = **会说话的桌面脸**：speak、reacts to surroundings（webcam awareness）、lip-sync、跨周记忆。
- 语音管线：**STT（Whisper）→ LLM → ElevenLabs TTS → mascot 唇形同步**。
- ⚠️ **情感系统是弱项**：没找到独立的 emotion/情绪状态机文档。mascot 的"情感"主要靠 LLM 生成 + 唇形/反应，**没有显式情绪建模、情绪记忆或情感弧线**。

### 2.4 主动性 / 后台思考（proactivity）

- 卖点口号：mascot *"keeps thinking in the background even when you've stopped typing"*。
- 实际机制相对朴素：**每 20 分钟自动 auto-fetch** 各 SaaS 新数据（邮件/日历/repo/docs/消息）→ 压缩进记忆树 → 维持持续上下文感知。
- **Google Meet agent**：以真实参与者身份加入 Meet（CEF 窗口），监听 live captions → Whisper STT → wake-word（"Hey OpenHuman"）→ LLM 生成简短应答 → TTS 回灌音频流。
- ⚠️ **"主动"其实是被动触发**：Meet agent 文档明确说它是**任务捕获（note-taker）而非自主发起**——用户说"记得提醒 Bob"它确认接收，但**不会自己主动发起 follow-up**。所谓"后台思考"= 定时数据同步，**不是真正的目标驱动主动行为**。

### 2.5 Agent Loop（`Agent::turn`）

- **主循环**：resume 历史 transcript（复用 cache）→ 载入记忆上下文 → 构建 system prompt（一次）→ 迭代（调 provider → 解析 tool call → 执行 → 追加结果）→ 直到模型产出最终文本而非 tool call 则终止。
- **子 agent 委派（spawn_subagent）**：子 agent **不是完整 session 的副本**，是隔离的 mini-loop；**父 agent 只看到子 agent 的最终输出文本**，看不到其内部推理/transcript（上下文隔离 + 压缩）。
- **四级委派策略（DELEGATION_POLICY，direct-first）★**：
  1. 直接回答（无 tool）
  2. 直接工具（current_time / memory / workspace 等轻量）
  3. inline 子 agent（专门工作，<5 turn）
  4. dedicated thread（长任务 >5 turn，保父线程干净）
  - 原则：能不委派就不委派；**绝不把完整对话历史传给子 agent，只传任务相关细节**；worker 不能再 spawn worker（禁链式）。
- **Typed vs Fork 模式**：Typed=构建窄 prompt + 过滤父工具 + 解析子模型；Fork=复用父 prompt 与缓存 tool schema 做 **prefix-cache 加速**。
- **Token 压缩（TokenJuice）**：HTML→Markdown、长 URL 缩短、冗长工具输出去重摘要 → **~80% 成本/延迟降低**。
- ⚠️ **无显式 goal-completion 机制**：文档明说 *"no explicit completion mechanism exists"*——agent 靠"模型不再发 tool call"自然终止。**没有 DeskPet 那套 last-mile artifact/receipt/verify-gate 的产物校验闭环**。

---

## 3. 「帮用户完成目标」+「长期陪伴关系」的机制（重点）

### 3.1 完成目标（goal-completion）侧

openhuman 的 goal-completion 哲学是 **"广度优先 + 冷启动消除"**，而非 DeskPet 的 "深度交付 + 产物校验"：

- **冷启动即满血上下文**：一键 OAuth 接 118+ SaaS，20 分钟自动 fetch + 压缩，让 agent 立刻掌握你的全盘信息 → 减少"先问一堆背景"的摩擦。
- **direct-first 四级委派**：把"完成目标"拆成"能直接答就直接答，复杂才委派"，控制 token 与延迟。
- **真实工具集**：filesystem/git/lint/test/grep coder 工具 + web search/scraper + SaaS 写操作 → agent 能真动手。
- **❗ 短板**：**没有产物级验收闭环**。模型停发 tool call 就算"完成"，没有 verify-gate / receipt / outcome-verifier。这正是 **DeskPet 的 last-mile 工具层（artifact/receipt/verify-gate）反超 openhuman 的点**。

### 3.2 长期陪伴关系（long-term relationship）侧

- **跨周记忆**：记忆树 + SQLite 持久化，"remembers you across weeks"。
- **PROFILE.md 人格固化**：随交互演化的偏好画像，**带半衰期衰减**（7–90 天），既不僵化也不健忘 —— 这是**关系建模里最精巧的设计**。
- **可对话编辑记忆/偏好**：用户能直接让它"记住/忘掉"，关系是**用户可控、透明（md 可读）**的。
- **❗ 短板**：**没有情感弧线 / 亲密度成长 / 关系阶段建模**。它建模的是"你的偏好与事实"，**不是"我俩的关系"**。陪伴感来自 mascot 的脸 + 记得你，而非情感递进。这是 **DeskPet 作为"数字伴侣"可以做得更深、形成差异化的地方**。

---

## 4. 对 DeskPet 的可借鉴点（逐条）

| # | 借鉴什么 | 为什么 | DeskPet 现状与差距 |
|---|---|---|---|
| **B1 ★** | **分层摘要记忆树（≤3k-token chunk → scored → 折叠成 summary tree，存 SQLite，Obsidian 式 .md vault）** | 比纯向量召回更可解释、可人工编辑、可长期压缩，天然解决"记忆膨胀"。 | DeskPet 现用 BGE-M3 向量 + 自动总结 + 事实抽取，**但缺"分层摘要树 + 人可编辑 .md vault"这层结构**。可在现有总结之上加树状折叠 + 暴露可编辑 md。 |
| **B2 ★** | **混合检索打分（graph + vector + keyword + episodic + freshness 五路加权）** | 单一向量召回常漏精确事实/新近事实；多路融合显著提升相关性。 | DeskPet 主要靠向量。**差距：缺 keyword/episodic/freshness 的显式加权融合**。低成本高收益，建议优先抄。 |
| **B3 ★** | **PROFILE.md 人格画像 + 偏好半衰期衰减（7–90 天 + Pinned/Forgotten）** | 让人格"随时间演化但可被用户钉住"，避免性格僵死或乱漂。 | DeskPet 有人格但偏静态。**差距：无"偏好稳定性打分 + 衰减 + 用户钉住/遗忘"机制**。这是把"伴侣感"做活的关键。 |
| **B4** | **写入分级 put_doc / put_doc_light / ingest_doc（高频数据跳 embedding）** | 桌宠会持续吃截屏/语音 tick 等高频流，全 embedding 会爆。分级写入是性能护栏。 | DeskPet 记忆写入未必分级。**差距：高频临时数据应走 light 路径跳 embedding**。 |
| **B5** | **direct-first 四级委派策略 + 子 agent 上下文隔离（父只见子的最终输出）** | 控 token、控延迟、防上下文污染；与 DeskPet 多 agent 诉求一致。 | DeskPet 已有多 agent/slash/goal。**可借鉴：明确的"能不委派就不委派"四级阶梯 + 父子上下文压缩边界**。 |
| **B6** | **TokenJuice 工具输出压缩（HTML→MD / URL 缩短 / 工具输出去重摘要，~80% 降本）** | 工具调用层冗长输出直接吃 token；压缩对本地桌宠延迟体感提升大。 | DeskPet 工具层有 last-mile 但未必有统一输出压缩。**差距：可加一层 tool-output 压缩中间件**。 |
| **B7** | **冷启动：可选一键接 SaaS + 定时 fetch 进记忆** | 让桌宠"开箱即懂你"，减少冷启动尬聊。 | DeskPet 本地优先、几乎不接外部。**注意：这与 DeskPet「本地隐私」定位有张力**，可做成**可选开关**，不强绑。 |

**DeskPet 已领先 openhuman 的点（不必抄，反而是差异化护城河）**：
- ✅ **goal-completion 产物校验闭环**（last-mile artifact/receipt/verify-gate）—— openhuman **完全没有**，它"停发 tool call 即完成"。
- ✅ **全本地语音管线**（openhuman 的 TTS 用 ElevenLabs 云服务，账号/模型路由也走托管）。
- ✅ **数字伴侣人格深度** —— openhuman 的 mascot 是"会说话的脸 + 记得你"，**无情感建模/关系弧线**，DeskPet 可在此做出真正的差异化。

---

## 5. 局限 / 不适用

1. **GPL-3.0**：copyleft 传染。直接复制代码进 DeskPet 有许可证风险，**只借鉴设计思想/架构，不抄代码**。
2. **托管依赖**：默认体验依赖 openhuman 托管的账号/模型路由/搜索代理/OAuth，**与 DeskPet「本地隐私优先」定位冲突**。其"本地"是记忆本地，推理/集成仍偏云。
3. **"主动性"名不副实**：所谓"后台思考"= 20 分钟定时同步 + Meet note-taker，**不是真正目标驱动的自主主动**。DeskPet 若要做真主动（主动关心/主动提醒/主动推进 goal），**不能照搬，得自研**。
4. **情感/陪伴薄**：无情绪状态机、无关系阶段、无情感记忆。对标"数字伴侣"维度 openhuman **不是好范本**，只在记忆工程维度是范本。
5. **生产力定位**：openhuman 核心受众是"想要 agent 帮我处理 118 个 SaaS 工作流"的生产力用户，**与 DeskPet 的"有人格的陪伴 + 完成目标"受众部分重叠但不完全一致**。
6. **Early Beta + CEF/Meet 等重度依赖**：Google Meet agent 靠 CEF + caption bridge，工程复杂且脆弱，不建议照搬。

---

## 6. 关键引用

- 仓库主页 / README: https://github.com/tinyhumansai/openhuman
- README（raw）: https://raw.githubusercontent.com/tinyhumansai/openhuman/main/README.md
- 记忆架构: https://github.com/tinyhumansai/openhuman/blob/main/docs/memory-sync-functions.md
- Agent 自学习 / PROFILE.md: https://github.com/tinyhumansai/openhuman/blob/main/docs/AGENT_SELF_LEARNING.md
- Agent loop / 子 agent / 工具流: https://github.com/tinyhumansai/openhuman/blob/main/docs/agent-subagent-tool-flow.md
- 委派策略: https://github.com/tinyhumansai/openhuman/blob/main/docs/DELEGATION_POLICY.md
- Meet agent: https://github.com/tinyhumansai/openhuman/blob/main/docs/MEET_AGENT_SMOKE.md
- 组织: https://github.com/tinyhumansai
- Product Hunt: https://www.producthunt.com/products/openhuman

> 关键数据快照（2026-06-04）：30.7k star · v0.57.13 · GPL-3.0 · Tauri/Rust+React · 118+ 集成 · 记忆树 ≤3k-token chunk + SQLite · 偏好半衰期 7–90 天 · TokenJuice ~80% 降本。
