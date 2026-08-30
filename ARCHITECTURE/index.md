# ARCHITECTURE 索引

本目录是 simple_harness **当前生产架构与项目状态的唯一事实源**。实现计划记录“如何做”，本目录记录“现在实际怎么运行、完成到哪里、有哪些边界与风险”。

2026-08-30 Human Memory Program 已完成 S4 Task 1–3 的 Host fresh epoch、永久 evidence、Canonical TaskScope
Archive 与 recoverable task-home provisioning 基础；当前提供结构协议验证、权威归档、CAS revision、
ExecutionEvidence ingress/watermark、terminal gate seam 和 committed provision receipt，不含正式 composition、
workspace binding authority/阅读视图/search consumer/FIFO/UI，也不能作为产品成功
声明。当前边界与证据见 [`MEMORY_SDK_BOUNDARY.md`](MEMORY_SDK_BOUNDARY.md)。

2026-08-29 当前 Project-scoped managed Skill 安装事实：聊天和 Settings 统一进入
`ProjectSkillInstallService`，外部 GitHub 内容先冻结 exact commit、成员清单和 Project identity，再由
`skill_install` 的真实 UI 授权继续；模型、通用 shell 和 UI boolean 都不能代替授权。Manager 原子发布后，
同一个 durable intent 必须再完成 canonical `skill.install.verify` Run，证明新 Run 能从 exact owner +
Project scope 的 frozen catalog 解析正文，才可结算为 `succeeded`。Capability Center 以当前 Session 的可信
Project binding 查询，projectless 或其他 Project 不继承。macOS 隔离 debug App 已完成目标仓库
`4d8c803ba03b…` 的真实安装与 UI 可见性验证；消息输入栏的 slash catalog 也从同一可信 Session/Hub/Store
投影读取，打开 `/` 时按当前 Session 重新拉取，不缓存安装前旧目录，`/plan-` 已真 UI 显示三个成员且
projectless 会话不泄漏。本轮是该故障链的验收证据，不替代 plan 中尚未执行的完整
恶意仓库、跨 Project 和全 surface 矩阵。详情见 [`ARCHITECTURE.md`](ARCHITECTURE.md)、
[`AGENT_HARNESS.md`](AGENT_HARNESS.md)、[`UI.md`](UI.md) 与
[`plans/2026-08-27-chat-skill-install/results.md`](../plans/2026-08-27-chat-skill-install/results.md)。该段记录
切换前的 Project-scoped 验证链；当前新安装以以下 user-global authority 为准。

2026-08-29 当前普通 Session 与 Skill 安装事实：未选择目录时，Host 在
`Documents/SimpleHarnessProjects/Session-<id>` 分配独立工作目录；显式选择时使用用户选择目录，绑定在
Session 创建后不可改。Settings/Chat Skill 安装共享一个 user-global managed authority，固定 Git commit
与 digest，经确认、Manager 原子 publish、fresh Run 验证和 durable activation 后才进入所有 Session 的
catalog；Skill/Tool 可发现集合为全局集合，实际执行仍经权限、健康与 scope 策略。新安装默认授权模式为
Auto。SDK 前台 Run 会在 publish lock 内冻结 user-global Hub snapshot，把其中的 Skill metadata 投影到该
Run 的 `RuntimeToolCatalog`；slash help/list/schema/dispatch 也从同一快照构造 catalog，前端每次开始新的
`/` 输入都会刷新，避免安装或切换 Session 后继续使用旧缓存。`tool_search -> skill_invoke` 再按
locator/content hash 读取冻结正文。Session 创建时动态冻结当前 Companion owner；旧的空 ownerless Session
在首次 admission 时只允许幂等绑定当前 owner，非空或跨 owner 数据仍 fail closed。真实
`deepseek-v4-flash` 已在冷重启后的旧 Session、新建默认 Session及用户选择目录的新 Session 中完成该链路。详见
[`ARCHITECTURE.md`](ARCHITECTURE.md) 顶部与 [`PROJECT_STATUS.md`](PROJECT_STATUS.md)。

2026-08-29 安装收敛补充：完整 40 位 commit URL 直接使用 GitHub codeload，不再先消耗 GitHub REST
`/commits` 限额；branch/tag/HEAD 仍必须经 REST 解析为 immutable commit。安装失败会产生结构化、可终止、
可查询的 Run 结果，同一稳定 failure identity 不会被 Agent 盲目重放，只有显式 retry 才推进 durable attempt
generation。统一能力中心与兼容 Skill Store 都从同一个 `user:v2:*` managed catalog 投影已安装项。

2026-08-30 当前 SDK 消费组合：Service SDK `0.3.12`（wheel SHA-256 `710ae66b…`）、
Harness SDK `0.6.4` candidate（source `21f3c7a…`，wheel SHA-256 `ecb6e85c…`）和 Memory SDK
`0.5.2`（`deff2fa8…`）。Service 的发布 manifest 仍记录 Harness `0.6.2` 构建成员；消费端按其
`>=0.4,<0.7` 约束独立准入 0.6.4，并分别校验 Harness candidate manifest 与 Service authority root。
0.6.4 尚未 tag/release，所以当前不标记为官方三 SDK release unit。上一次干净 macOS 实例是
0.6.2 组合的历史证据；本次 rebase 后的 0.6.4 构建仍需重新完成真实 UI/provider 验收。

2026-08-30 Realtime 消费端现状：旧 `/ws/audio` 与本地 VAD/ASR/TTS 链继续关闭；新的
`/ws/realtime-voice` 由 Service SDK `0.3.12` 的 loopback protocol、Realtime client 和 provider transport
负责，前端只有一个电话式开始/挂断入口，并且只在用户点击后申请麦克风、创建 AudioContext 和连接后端。
本轮自动化覆盖本地鉴权、origin、PCM framing、barge-in、挂断和资源释放；真实 Provider 连续多轮通话尚未
重新验收，因此该路径是已接线候选，不标记为 release PASS。它只承载 provider-native voice；若未来加入
Agent Tool/Workflow，仍必须进入正式 `ProductTurnPreparer`/RunKernel authority。

2026-08-27 当前 Project-scoped Sessions 事实：macOS 冻结场景 S-PS-01～S-PS-08 已全部通过。Session 在创建时绑定 Project，现有 Session 不能修改根目录；要在另一目录工作需基于目标 Project 新建 Session。终端、内置文件工具和 Project Rules 只使用冻结的 execution root，project-bound Run 不暴露进程级固定根的动态 `mcp:filesystem`。从旧 schema 升到 v33 会按全新安装清空升级前 Session、消息、Project 与会话派生数据，同时保留全局 Provider/设置/Keychain 和磁盘文件。Windows 是未来独立范围。详情见 [`ARCHITECTURE.md`](ARCHITECTURE.md)、[`AGENT_HARNESS.md`](AGENT_HARNESS.md)、[`UI.md`](UI.md) 与 [`PROJECT_STATUS.md`](PROJECT_STATUS.md)。

2026-08-29 当前 Tool/Capability 事实：simple_harness 已 vendor Harness 0.6.4 candidate（source
`21f3c7a…`，wheel SHA `ecb6e85c65e9140c6838666f59f38239557e15cf410c1afe023ffd06bfb35be7`）
与 Memory 0.5.2（wheel SHA
`deff2fa85a269a3978f2c6efcd99fda77abcb74444170361365fd00ec0164e9e`）。SDK 公共 runtime catalog 统一
built-in、健康 MCP、Skill metadata 与 Workflow profile；fresh Run 采用 compact direct kernel，其余能力
在同一 durable Run 内搜索、描述、激活并刷新下一 Provider attempt。目录不授予权限，目标执行继续经过
Host prepared authorization、scope 与 physical identity。完整事实、恢复和当前 UI evidence 边界见
[`ARCHITECTURE.md`](ARCHITECTURE.md) 与 [`AGENT_HARNESS.md`](AGENT_HARNESS.md)。

生产组合继续使用一个 borrowed `MemoryManager`、正式 AgentIdentity、SDK-prepared
Memory 与 read-only product Context provider；root 与 continuation 使用各自 immutable source ref。
foreground committed Turn 与非 Harness product outbox 按 provenance 分治，普通前台工具不再二次 live recall。
显式 remember/read/forget 使用完整可信 principal 与正式 fact API，返回准确 fact ID；forget 以显式
`source_event_id` 生成持久 action receipt，同 action 重放保留结果、后续 action 稳定返回 no-op；shutdown 先关闭借用
runtime、再有界 drain/关闭唯一 SessionDB owner，重复关闭不重复释放 manager。自动化门禁已完成且 0 新红；
macOS Computer Use 真人 SH-M1～SH-M6 与 SH-SURFACE 已全部通过；包含真实 DeepSeek、PPT/权限/Artifact、
跨进程冷重启召回、recall timeout 安全降级、record transient 未落库即退出及无故障启动恢复。
开发期 schema 变化用显式三库 reset 从空库开始，不实现用户运行时全面抹除。聚焦自动化 D1/D2/D3、
Rust diagnostics 与 build 已绿；simple_harness macOS 真人消费者 CTX-1～CTX-5 与 surface smoke 也已
完成。消息页文本附件以 private `input_text` 进入 frozen stage，公开 Context 只显示有界元数据，
Provider wire boundary 才降低为兼容文本；budget-only cancel receipt 不会再阻塞 ordered projection cursor。

当前 Memory 一等集成边界、身份 trust chain、Context source lifecycle、迁移/fault 语义与真实 UI 验收状态，
以 [`MEMORY_SDK_BOUNDARY.md`](MEMORY_SDK_BOUNDARY.md) 为准。

历史记录 — 2026-08-21 SDK Context cutover 校准（代码锚点 `e92883a5`）：当时前台文字 Run 的链路是
`_run_product_harness_chat -> _execute_sdk_run -> _assemble_sdk_messages -> SDK Runtime`。
它尚未消费已经构造的 `TurnInput`，也未进入保留的 `ProductTurnPreparer` /
`ProductTurnPreparationService`；因此当前首个 Provider 请求只有公开工作叙述 system prompt、最多
20 条普通 conversation 投影、当前用户文本和 SDK Tool catalog。Persona、召回 Memory、Skill 指令、
附件、项目/任务快照、Context OS 预算/压缩、会话 `model_params` 都尚未由这条生产链路冻结并交付。
Context Inspector 仍是 legacy persona/facts/V2 tool registry/history 的独立估算，不是 Provider
请求事实源；SDK provider invocation 的真实 usage 也尚未投影到 Session context usage/billing。
legacy preview 还缺少统一公开脱敏，可能把敏感 header/token/正文直接展示。这些是已确认的生产
缺口，不能继续把 `ProductContextAdapter` 或 Inspector 估算描述成当时已接通事实。

以上段落仅保留为切换前历史校准记录；其 Context/Memory 主缺口后续已关闭，不代表当前 0.4.0 Host
依赖链。当前事实以本索引顶部 2026-08-25 段落及各专项事实源为准。

2026-08-20 校准：Agent 执行时间线继续复用 canonical Run ledger 和
`HarnessPublicReadService`，细粒度活动条目不得创建第二套状态机；时间线及 Inspector 详情
明确属于 `context_visibility=exclude`，不会自动进入模型上下文。SDK tool turn 的公开工作叙述
与工具卡按 canonical Run 聚合为可折叠“思考过程”；隐藏 reasoning/CoT 不投影、不持久化、
不进入后续模型上下文。

维护规则：功能通过测试后，同一次交付必须更新对应模块架构；完成度、里程碑、worktree 与项目级已知问题同时汇总到 `PROJECT_STATUS.md`。`STATUS/` 只保留历史链接兼容，禁止继续双写。

| 文档 | 范围 |
|---|---|
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | 全局模块完成度、活跃 worktree、最近里程碑、项目级已知问题与验证入口；新任务接手的第一站 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | simple_harness 全局长任务架构基线：单主 Session Harness、模型驱动 Profile、持久化、DeepResearch/PPT、通用行动/能力包、HITL、恢复、Trace/Eval 与升级边界 |
| [UI.md](UI.md) | 当前暗色优先 UI 主题、共享语义样式、页面覆盖范围、业务边界与真实 Windows 验证状态 |
| [COMPANION_GROWTH.md](COMPANION_GROWTH.md) | Companion 长期成长当前事实：唯一 Store/Router、可信 owner inbox、durable GrowthEvent、同一 RunKernel 的 reflection/candidate/evaluation 生产编排、Manager activation receipt、V2 Reminder、legacy writer 退休边界与真实 provider 阻塞状态 |
| [AGENT_HARNESS.md](AGENT_HARNESS.md) | 当前 Agent Harness 事实源：固定 `agent.general` root、`workflow_spawn` ticket/Driver、TaskGoal/Attempt 失败闭环、running-root FIFO、Manual/Auto 与可执行能力目录 |
| [SDK_EXTRACTION.md](SDK_EXTRACTION.md) | Simple Harness SDK 提取与消费事实源：当前 vendored Harness 0.6.4 candidate / Memory 0.5.2 / Service 0.3.12、历史 release/迁移与消费者边界 |
| [MEMORY_SDK_BOUNDARY.md](MEMORY_SDK_BOUNDARY.md) | 官方一等 Memory 生产链、validated local identity、immutable Context source、outbox authority、自动化与真实 UI 验收状态 |
| [Harness R7 历史流程图](../plans/2026-07-20-agent-harness-simplification/target-architecture.md) | R7 时点的“一个产品准备入口、一个薄 Kernel、两个 Driver、一套 Effect/UoW 底座”证据；其中主线程/Code 工作台产品边界已被 2026-07-24 单主 Session 多 root 架构取代，当前口径以 `AGENT_HARNESS.md` 为准 |
| [AgentLoop.md](AgentLoop.md) | ReAct 主循环、工具注册/分发、完成守门、ContextManager 与 main 装配 |
| [ARCHITECTURE.md §14](ARCHITECTURE.md#14-native-workflow-engine-replacement-baseline) | LangGraph 调度/身份/生命周期/HITL/评测/打包耦合、原生执行器替换边界、checkpoint 与旧 run 兼容策略 |
| [DeepResearch.md](DeepResearch.md) | DeepResearch 模块专项架构、能力边界、历史缺口与演进记录 |
| [DEEP_RESEARCH_AGENT_REACH.md](DEEP_RESEARCH_AGENT_REACH.md) | 历史方案记录；当前 DeepResearch v7 不依赖 Agent-Reach，不能作为生产链路事实源。 |
| [SEARCH_GATEWAY_DEEPRESEARCH.md](SEARCH_GATEWAY_DEEPRESEARCH.md) | 快速搜索、SearXNG-like Search Gateway、DeepResearch 原生并行、抓取抽取与聊天进度投影的当前代码基线。 |
| [PPT.md](PPT.md) | PPT 生成端到端链路、渲染路径、模板/图片/视觉评审、验证状态与已知短板 |
