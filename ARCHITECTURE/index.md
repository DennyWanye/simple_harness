# ARCHITECTURE 索引

## 2026-09-06 无边图谱标签布局已修复

最后更新：2026-09-06。Cytoscape无边节点用网格，布局包含标签尺寸并允许中文换行，保留有边有向布局及全部身份/遗忘/viewport行为。真实WebKit两个尺寸各7节点：标签重叠17/13→0/0，最终字高9.53/11.05px，真实选择/缩放通过。
前端18PASS/1个旧API-fixture未配置SKIP，TypeScript通过；所有浏览器/测试进程结束。合成fixture不代表真实API/native或密集边标签完成，原生复验仍待续。
[原红、实际测量、边界与证据](../plans/2026-09-06-graph-label-layout/RESULTS.md)。

## 2026-09-06 短期选中来源已合成

最后更新：2026-09-06。独审2d98e083合入7fafe03a，同时保留审计authority；实际factory每hit完整来源、裁减/遗忘不互相污染、显式长期零short与HUMAN审计WS组合42项通过（21.12秒、峰值238MiB）。
仅已有内部短请求来源路径闭合；自动生产索引worker、多消息完整producer、新模型short协议及原program仍未完成。无新模型/native运行。
[组合结果与边界](../plans/2026-09-06-selected-short-runtime/COMBINED.md)。

## 2026-09-06 审计查看入口组合验证

最后更新：2026-09-06。独审1097b272合入c53caff2：记忆面板显式打开用途绑定的HUMAN元数据审计，分页/持久ACK重放、关闭与身份失效拒绝；保留原图谱viewport及遗忘ACK修复。组合独审限定ACCEPT。
后端54项通过，新增真实/ws/control审计往返2项通过，前端44通过/1个可选API-fixture未配置跳过，TypeScript通过。单进程有界执行；没有真实Provider、native或全操作覆盖。初始snapshot成本及原生验收仍待续。
[组合证据、命令与范围](../plans/2026-09-05-agent-operation-audit/human-access-leaf/COMBINED.md)。

## 2026-09-06 模型召回类型选择局部完成

最后更新：2026-09-06。memory_standalone工具显式类型经Host校验传入已安装Memory0612公共计划，保留Host身份/权限/预算；显式长期选择不偷偷附带短期查询。成功类型枚举写既有Host审计记录，原proposal仅hash，非公共SDK完整参数回读。
独立叶子最终77项通过（50.57秒、峰值191MiB），包括实际选中来源/最终出站/任务披露链；没有真实Provider或native。固定49249dbd已独审限定ACCEPT并fast-forward主组合，完整类型质量、短期与调度、审计UI及原program仍未完成。
[契约、命令、历史红与证据边界](../plans/2026-09-06-model-recall-selection/RESULTS.md)。

## 2026-09-06 SDK073审计组合验证通过

最后更新：2026-09-06。组合源码78647bb0集成独审通过的终态身份叶子；主组合专用venv安装H073/M0612/S0312，348个SDK文件与本树vendor逐字节一致。
审计目录及candidate/composition组合100PASS/31.62s，单进程峰值258MiB，无本地模型、真实Provider或native。v1历史保留；全操作覆盖及受控审计UI仍待完成。
[实际结果及边界](../plans/2026-09-06-terminal-audit-identity/COMBINED.md)。

## 2026-09-06 Installed H073 exact terminal identity leaf

Last updated: 2026-09-06. Isolated Host candidate consumes exact H073 (wheel1a9ed5c9…)
through public RunTerminalAuditEvidenceV1.matches and existing Host raw-SDK normalization.
Every persisted page binds actual Run/event/full payload/state; legacy scoped evidence
uses its original envelope and terminal gate. RULE terminal-run-v2 preserves all v1 jobs.
Selective installed group21PASS2FAIL then necessary repairs2PASS; failures were a guarded
fixture mutation and obsolete global error-count expectation, retained verbatim. Non-null
committed-turn public head/receipt + same-cursor reopen, namespace negatives and late-source
rejection passed. Peak owned RSS147MiB; no model/native/full suite. Independent fixed-source
review pending; no main production switch or whole-operation completion claim.
See [contract and measured results](../plans/2026-09-06-terminal-audit-identity/RESULTS.md).

## 2026-09-05 Host terminal audit candidate

最后更新：2026-09-05。独立树 eaccab33 + 3e911c14 接入默认 terminal audit consumer；持久读取 attempt、
固定 snapshot/pages 和幂等 findings，不产生 Provider calls/usage/cost 总计。
仅 source-overlay/focused composition 验证，尚非 main installed successor 或完整 operation coverage。
[当前边界与交接](../plans/2026-09-05-agent-operation-audit/host-terminal-leaf/HANDOFF.md)。

## 2026-09-05 Source/auth/action combination verified

Fixed e31c6efd source index closes the independent unscoped-search/late-forget P1;
combined with exact SDK decisions, source-aware history, schema47 and action evidence:
**51 passed** on installed067. Only test-fixture signature required merge resolution.
The separate f9cbb7c8 native candidate also passed expanded visible authorization
by real mouse click; this combined tree has not run native. Cognitive UI/SDK suppress
and selected-source indexing remain incomplete; no main cutover or full program PASS.
See [combined evidence and boundaries](../plans/2026-09-05-primary-effect-sources/COMBINED.md).


## 2026-09-05 Primary source effect index v47 — reviewed local candidate

8e896472 independent P1 confirmed: unscoped search could escape source checks when
TaskScope reservations were absent. A Host append-only exact SDK effect identity
index now records real handler entry under the captured foreground lease; Provider
preflight reads actual SDK results and preserves search→create dependency prefixes.
No scope grant/watermark, SDK change or old evidence restamp. Default schema is47;
coordinator owns deferred S5c's explicit48 remap (historical47 AC remains historical).
Fixed real late-forget counterexample is green; adjacent search/scope17, page-in1,
startup/create32 and migration11 passed. Independent fixed-counterexample review accepted e31c6efd; no
main/native or full privacy completion claim. Ordinary page-in lacking source proof
rejects; generic page-in source projection and short source-only admission remain open.
Details and raw-log hashes: [source migration contract](../plans/2026-09-05-primary-effect-sources/SOURCE-MIGRATION-CONTRACT.md).

本目录是 simple_harness **当前生产架构与项目状态的唯一事实源**。实现计划记录“如何做”，本目录记录“现在实际怎么运行、完成到哪里、有哪些边界与风险”。

2026-09-05 installed067运行层v2保真/严格拒绝与new-message producer组合60绿；旧archive不改，
真实short登记/选中来源由Hegel另线验证，不称native/program完成。见[运行契约](../plans/2026-09-05-s6-primary-preparation/HISTORY-RUNTIME-CONTRACT.md)。

2026-09-05 scoped普通投影恢复候选：initial/resume/search实际manifest、MEMORY-only抑制后
结构effect/terminal相邻96绿；最终start字节负例2绿。原initialscoped红已恢复，仍待独立review，
不声称所有旧producer/short/native完成。见[来源契约](../plans/2026-09-05-primary-resume-sources/CONTRACT.md)。

2026-09-05 Primary history runtime隔离切片94项聚焦绿，**预先scoped ResumePackage首Provider仍有1项P1红**；
后继动态ResumePackage漏发纠正21项聚焦绿；来源功能闭合及其他carrier完整性审计仍未完成。
固定API/helper已入树；后继no_recall origin纠正，相邻合跑41绿/初始scoped1红，exit1。
不得合主生产。见[运行层契约](../plans/2026-09-05-s6-primary-preparation/HISTORY-RUNTIME-CONTRACT.md)。

2026-09-05 primary history API 隔离候选已接公开 Memory batch visibility，50项聚焦通过；
真实 memory-only forget / 跨 Run 来源 / recall binding / signed WS late fence 已有 library/API
证据。runtime 组合、独立复核、short exact carrier 与真实 UI/provider 仍独立待续，未改 gate。
见 [Memory 当前边界](MEMORY_SDK_BOUNDARY.md) 与 [验证记录](../plans/2026-09-05-primary-history-api/VALIDATION.md)。
2026-09-05 Primary 精确 SDK 授权新增独立候选：生产授权策略/installed SDK/真实文件 effect
确定性 fixture 通过，HUMAN bound socket 接权限补读与响应。native 未验收，WAITING通知
由运行层另行组合；底层旧列表非 SDK public port。见 [UI](UI.md) 与
[候选契约](../plans/2026-09-05-primary-sdk-decisions/CONTRACT.md)。

2026-09-05 Primary API 后继修复处理 terminal authority/raw SDK hash 差异、当前 source
过滤及 commit 后唤醒失败的 durable ACK；依赖 Carver 统一 helper，真实组合测试待主运行。
公开 suppression 无原子 snapshot/epoch，不将逐来源复查或既有40项局部绿色称为完整闭环。

2026-09-05 Primary API 隔离切片完成 source-bound history/state 与 exact control，聚焦40 passed。
queued 下界计数及 history keyset 有界；仅证明 Host source suppression，来源谱系扩展仍后续。
实际组合/UI 验证待主协调，不改变 S6/program 完成度。详见
[接口契约](../plans/2026-09-05-s6-primary-preparation/PRIMARY-API.md)。

2026-09-05 S6隔离分支新增control verified connection复用与无scope admission，聚焦29 passed；
standalone/dynamic route→生产effect/terminal identity及状态通知聚焦181 passed；
CREATE_NEW active None生产AUTO binding后继66 passed后发现origin P1；冻结来源纠正85 passed，
origin获独立限定ACCEPT；首tool启动同步后继87 passed，仍需独立复核/native验收；完整历史来源suppression未闭合，
Manual UI未接，UI/API组合待验证，不可合main。详见
[实施交接](../plans/2026-09-05-s6-primary-preparation/IMPLEMENTATION.md)，不改变main完成度。

2026-09-05 当前接续已安装 Harness 0.7.2 / Memory 0.6.3，两个真实 queue.enqueue root 完成
workspace effect、TaskScope closure 与认知物化；独立质量审查发现了 episode 时间 P2。
该 P2 已完成源码修复与 26 条回归，待真实入口复验。原生 chat 冷启动通过，S6 UI 仍未交付；完整 machine gate 尚未通过。
最新候选、测试统计与历史失败边界见 [PROJECT_STATUS](PROJECT_STATUS.md)。

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
