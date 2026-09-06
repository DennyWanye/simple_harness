# ARCHITECTURE 索引

## 2026-09-06 原触发执行器组合验证

最后更新2026-09-06。固定1f9b575d合入已独审trigger叶并保留全部oracle指纹；三个必要交叉集成通过，无进程残留。原正式2PASS/1构造BLOCKED保持独立Run，不外推新401。[主复核及证据](../plans/2026-09-06-typed-recall-trigger-executor/COMBINED.md)。

2026-09-06主复核：Prospective后六格dfec8bbb源/9raw hash一致，合入c202be39；实际共享执行器组合9项通过7.17秒，峰111MiB且无残留。前13/后6分别保持正式Run证据，非一次401/Host提醒验收。[组合边界](../plans/2026-09-06-typed-recall-prospective-lifecycle/COMBINED.md)。


2026-09-06主组合复核：固定ea57e720公开Prospective 13格叶纳入29479573；13个raw hash一致，四oracle指纹均保留。受影响组合18项通过6.31秒，峰112MiB/组已清空；synthetic SDK信号不代表实际Host提醒，余6个lifecycle仍继续。[复核与证据](../plans/2026-09-06-typed-recall-prospective-public/COMBINED.md)。


2026-09-06主复核：Procedure公开适用性固定ad189/3de9已纳入1c690bdb；9份原始证据hash一致，受合并影响的15项组合检查通过、进程已清理。原四格独立PASS，不代表Host观察晋升或全401；[组合范围与证据](../plans/2026-09-06-typed-recall-procedure-public/COMBINED.md)。

## 2026-09-06 Context与source oracle已组合复验

最后更新：2026-09-06。独审后的两个执行器合并固定9bad3a43，两个代码指纹入口均保留；12项必要组合检查通过、进程组32908已退出。原source正式10PASS与Context正式4PASS/2BLOCKED按各自固定源及Run保留，不拼成新401全量；Harness凭据消费/continuation仍需实现，原SDK pin与阈值未改。详见[组合结果](../plans/2026-09-06-context-use-full/COMBINED.md)。


## 2026-09-06 current-use 双 item 测试工具叶子

独立原六格真实public执行：6OBSERVED，4PASS/0FAIL/2BLOCKED，其余395未选。
两格Harness reservation/exact-once消费见证仍缺，原new-continuation未执行；无Memory新缺陷声明，
不表示S3/401完成或主树已合入。冻结H073/M0613与阈值不变，测试槽已释放。
[逐格结果与范围](../plans/2026-09-06-context-use-full/RESULTS.md)。

## 2026-09-06 Prospective trigger executor公开runner叶子

最后更新2026-09-06。固定7e6337b5获Dirac源码限定ACCEPT后，installed H073/M0613新集成方法1PASS0.63s（原3格+3篡改），正式原3格2PASS/0FAIL/1BLOCKED。
Run edb882f0ec224e1fbdbfff4e5bcc714c；missing trigger无法公开构造，未以DTO拒绝冒充eligibility通过；其余398未选，整体NOT_RUN/BLOCKED、exit3。
pending ACK仅synthetic registration合同，不是Host提醒；两projection旧wire/hash及canary/scope义务、32非法组合保持边界。
旧19未重跑、不并历史为新401。最大135408KiB、进程无残留、槽释放。
[命令、边界和9raw hash](../plans/2026-09-06-typed-recall-trigger-executor/RESULTS.md)。



## 2026-09-06 Prospective剩余6 lifecycle公开runner叶子

最后更新2026-09-06。ec68源码审查P1（receipt目标连续性与candidate正控来源）修复为ea030952并限定ACCEPT。
实际installed H073/M0613一个新集成方法PASS1.17s（6真实格+11篡改），原6格正式6PASS/0FAIL/0BLOCKED，
Run b7b8fe83520d430b8c52d93f72f4bb4f，dependency[]；其余395未选/整体NOT_RUN/BLOCKED/exit3。
实际public ACK/matched signal/授权REVISE绑定原source与真实revision；candidate原负例+独立同ID正控，
expired/completed仅synthetic显式状态更新，不称外部时间signal或任务完成。无Host/SDK生产修改。
前13未重跑，不并片为同Run19或新401；projection/非法组合边界保留。
两PGID均退出无残留、槽释放，最大135536KiB。
[命令、P1与raw/hash](../plans/2026-09-06-typed-recall-prospective-lifecycle/RESULTS.md)。


## 2026-09-06 Prospective公开scheduler fixture叶子

最后更新2026-09-06。4aee0cdb源码、dd988b19精确expiry test delta均Dirac限定ACCEPT。
实际installed H073/M0613必要3方法PASS，受影响1方法窄复验PASS；原13格正式13PASS/0FAIL/0BLOCKED，
Run ae075cb1eaed43a3b8f8221160d2c874，无dependency，其余388未选/整体NOT_RUN/BLOCKED/exit3。
真实public outbox ACK+synthetic signal绑定原trigger/source/run/clock/expiry与实际revision，重开exact零读取。
这是SDK合同synthetic scheduler，不声称Host真实提醒/外部event；原19另6lifecycle及projection未覆盖。
不合旧182/source10/Procedure4为完整401；无SDK/Host生产变更。三PGID均退出无残留，槽释放，最大135296KiB。
[命令、边界、raw与hash](../plans/2026-09-06-typed-recall-prospective-public/RESULTS.md)。


## 2026-09-06 Procedure公开适用性runner叶子

最后更新：2026-09-06。固定ad189f52已Dirac限定源码ACCEPT；实际installed H073/M0613
必要公开测试3PASS1.09s，原四格正式4PASS/0FAIL/0BLOCKED（Run3913be071c484d069b48082fc5cec12a）。
公开conversation registration/authority snapshot绑定真实revision，错fingerprint不召回，重开exact重放零candidate读取；
eligible保留原literal/INELIGIBLE，经draft→授权REVISE映射eligible_for_activation，不算观察晋升。
其余397未选，整体NOT_RUN/BLOCKED/exit3，不合旧182或source10为新全量；无SDK/Host生产代码变更。
两批默认OS锁2GiB/180s，最大135584KiB，自有进程均退出无残留、槽释放。
[实际结果、命令、raw索引与hash](../plans/2026-09-06-typed-recall-procedure-public/RESULTS.md)。


## 2026-09-06 source10完整oracle后继（独审待回）

固定65990a68，exact installed H073/M0613f2 source层正式10PASS/0FAIL/0BLOCKED。
完整90表schema/PK/nonfinal根、request/attempt/terminal关系、原distinct admitted source与member/group hash、
确切reopen outer/cause/trace及零recall/零写均独立判定；没有改SDK错误码、fixture、10AC/阈值。
1个集成test含10正控+30篡改检查通过；schema2/extra-key、swap/reuse重hash命中目标reason。
首轮three-member错误cause导致1红，已保留并定向修正为实际FKcause；不改原证据。
本次未选391public，不与626/fbeb旧public计为新401全量，不称program/quality/native完成。
全部默认OS锁2GiB/180s，最大157920KiB，无残留且slot已释放。
[命令、红绿与原始hash](../plans/2026-09-06-typed-recall-source-oracle/RESULTS.md)。


## 2026-09-06 固定626后续正式分批（整体仍BLOCKED）

H073/M0613、runner626ff8d8的11个fresh bounded调用互斥覆盖原391public+10source；
public182PASS/0FAIL/209BLOCKED，source0PASS/0FAIL/10BLOCKED。
本次分批并集182/0/219，非一个full401 Run、非质量/机器gate；不拼旧2格observe或旧178历史。
355public+10source实际OBSERVED，36executor未实现；BLOCKED原因为122fixture/setup、61oracle、36executor。
source使用exact clean M0613f2；真实fault/corruption仅source证据，完整oracle仍缺。
全部默认OS共享锁、2GiB/180s/批，最大147904KiB，所有进程组无残留且槽释放。
[逐批Run、命令与逐格分类索引](../plans/2026-09-06-typed-recall-0613/FORMAL-BATCHES.md)。


## 2026-09-06 H073/M0613 runner successor（独立测试工具叶子）

独立 `feat/typed-recall-0613`，base60f280dc；候选pins显式后继并保留旧lineage，
原401/391+10/14攻击/阈值不变。observe在public/source层及cell统一不授PASS，FAIL保留。
必要工具测试12passed；两原格真实installed public OBSERVED且业务断言通过，正式0PASS/0FAIL/2BLOCKED。
source10与其余399未执行，旧178/0/223历史不覆写、不拼接。H164/M72包文件逐字节核对；无模型/native。
资源入口145baed3默认共享锁，两组无残留且槽已释放。原runner applicability WIP未动，原三格仍BLOCKED。
该工具叶子不表示S3/program或401全量完成，未合主树。
[命令、资源与证据hash](../plans/2026-09-06-typed-recall-0613/RESULTS.md)。

本目录是 simple_harness **当前生产架构与项目状态的唯一事实源**。实现计划记录“如何做”，本目录记录“现在实际怎么运行、完成到哪里、有哪些边界与风险”。

2026-09-01 Human Memory Program 已完成 S4 Task 5–8 Host TaskScope + Runtime Execution Closure（含用户
A2 批准的最小 S5 execution composition）：v39–v44 schema、六 bounded 阅读视图/checkpoint verifier、
permission-first search/exact open、单 foreground Run/durable FIFO/control、recovery fence/emergency
export、fresh HUMAN production composition 与唯一 `SdkRuntimeIngress.start`。2026-09-01 整改闭合两个
P1：generation fence（`EffectBoundary` 最终 admission 覆盖 SDK start/每次 control 发送/物理 Tool
dispatch，`ForegroundEffectAdmissionGate` 接入 `ProductEffectExecutor`，stale worker 外部副作用为 0）与
live control delivery（durable commit 即时唤醒、控制泵、pause ACK→PAUSED、STOP/CANCEL 独立信号与
终态）。验证：code-audit round-3 PASS、100k archive/execution value、9/9 fault、22-case API smoke、
full pytest 6218 passed（6 个失败全部为既有基线/本机环境项且在未修改 main 复现）。S5 剩余
RecallPlan/recall/动态 Context/semantic closure 与 S6 UI 未实施；PAUSED 无生产 resume、双 composition
路径漂移等 P2 边界见 [`ARCHITECTURE.md`](ARCHITECTURE.md) 对应节。gate run 位于
`plans/2026-08-29-human-memory-digital-twin/increments/2026-09-01-s4-host-closure/verification/r2-p1-closure/`。

2026-08-30 Human Memory Program 已完成 S4 Task 1–4 的 Host fresh epoch、永久 evidence、Canonical TaskScope
Archive、recoverable task-home provisioning 与 append-only multi-root binding authority。state schema v38
以 Host verifier 确认的 durable user evidence/interaction 或 Host-issued Auto Run snapshot 生成 grant；
Auto 在 authorize、append transaction 前和 commit 前都从 durable source receipt 重验 snapshot 时窗、active
Run、exact context/config revision 与 configured-root filesystem identity，缺少 current-Run authority port 时
fail-closed。binding receipt 持有 canonical sorted root-set commitment、exact parent/grant 与 immutable
revision。POSIX root 在 commit/effect 前以 no-follow fd 和 filesystem identity 重验，Windows 无等价实现时
fail-closed。schema-v2 route receipt 与
`TaskExecutionEnvelope` 必须交叉绑定 exact binding-set receipt id/hash/revision，当前 Run 不能使用后续 append
的新 root。provision receipt/proposed root 仍只是候选，不能自动升级为 authority。当前逐项缺口是：
S4 Task 5 六阅读视图/checkpoint verifier、Task 6 permission-first search/exact open、Task 7 单 foreground
Run/durable FIFO、Task 8 Host composition/旧入口 fence/data epoch/recovery/emergency export；S5 Task 8
才负责主模型 route/recall/context/tool 的最终 production composition，S6 才切 UI。不能把这些边界混作一个
“Task 5”，也不能把 S4 Task 8 误记为 S5。当前能力不作为产品成功声明。当前边界与证据见
[`MEMORY_SDK_BOUNDARY.md`](MEMORY_SDK_BOUNDARY.md)。

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

2026-08-30 历史 production 消费快照：Service SDK `0.3.12`（wheel SHA-256 `710ae66b…`）、
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

2026-08-29 历史 Tool/Capability 验收快照：当时 simple_harness vendor Harness 0.6.4 candidate（source
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
