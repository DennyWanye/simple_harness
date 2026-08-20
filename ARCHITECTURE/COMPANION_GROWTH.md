# Companion 长期成长架构

> 最后更新：2026-08-20
> 当前状态：Task 0～16 已完成。committed message → 模型语义判定 → GrowthEvent →
> reflection → candidate build → independent evaluation → activation/rollback 已接入唯一生产
> 组合根；S-1～S-5、S-8 已通过真实 Tauri 主消息页点击/输入 E2E，S-6/S-7/S-9 已通过
> 确定性自动化。启动恢复到 `companion/generation=5`，Companion 偏好、Runtime、成长事件、
> V2 Reminder 与通知投影是唯一生产成长链；旧 Codifier、ToolPath recorder、
> Skill candidate 临时确认面和进程内 Reminder writer 已退休。

## 当前生产边界

Companion 长期成长层复用现有
`ProductTurnPreparer → RunKernel → ReAct/Workflow Driver → Effect/UoW → RunPresenter`
主链，不拥有第二套 Harness、Driver、Provider 重试器或 Capability active pointer。

- `companion.db` 是成长证据、候选、评估、决策和 owner-domain 状态的唯一事务事实源。
- `GrowthAuthorityRouter` 是 legacy/companion writer 的唯一 durable 切换点。Task 13 在
  durable journal 与 ingress drain 内完成 `legacy → preparing → companion` 单指针切换；
  marker 后只能前滚 Companion 或进入 `paused`，不会回到 legacy 或产生双写窗口。
- trusted profile owner 由 Rust 签名的 `main/identity_bind` 与
  `message-panel/companion_action` 两条独立 control lease 建立；shared secret 不能授予
  Companion mutation authority。
- Rust 签名凭据与当前本地身份事实不一致时，后端以
  `credential_facts_mismatch + rechallenge=false` 终结该次绑定；前端只对可恢复的重新挑战做
  有界指数退避（最多 10 次），不会形成同步 challenge/rechallenge 风暴或无限日志增长。
- 消息面板的 `controlWs` 在 renderer 级缓存最近一条
  `companion_identity_status/companion_identity_unready`，新挂载的面板订阅者会立即收到重放。
  因此 React 重挂载或 Vite HMR 复用既有 WebSocket 时，不会因错过一次性身份就绪事件
  而永久卡在“正在恢复身份…”。缓存只是同 renderer 内的连接状态投影，不替代
  Rust 签名凭据或后端 `IdentityReadyGate`。
- `state.db` schema v21 保存 session owner、消息 ingress outbox、Companion excluded
  projection route/outbox 和 redaction receipt。一个 session 生命周期只能绑定一个 exact
  `profile_id + profile_generation`；已有消息的未绑定 legacy session 不能被当前账号直接
  认领。可信 profile bind 会恢复现有 owner inbox，或创建新的空 UUID inbox，并把该
  `session_id` 返回前端切换；因此 Relay 身份不会继承 `default` session 的旧历史。
- “新话题”是显式会话边界：无论当前查看 owner inbox 还是未绑定的 legacy 历史，
  `TaskSessionManager` 都先分配新的空 UUID session，Companion 再把它绑定到 exact owner
  generation、更新默认 route，并在任何 Run 启动前向主窗口/消息页投影
  `session_switched + task_session_started`。空白点击只创建并切换 session，不运行空
  AgentLoop；携带草稿时，首条 user echo 在新 session id 已知后才投影。普通发送仍不能
  写入只读 legacy session，错误会以 typed `companion_session_read_only` 立即返回。
- 2026-07-27 验证：聚焦 backend `62 passed`、frontend `32 passed`、TypeScript 编译通过；
  全新源码 Tauri 主消息页真人点击验证了空白新建、带草稿新建、即时 user echo 和真实
  Relay final 均落在新 UUID session。旧 HMR 连接曾把消息写入热更新前的 store，因此
  所有真人验收都以全新 Tauri/WebSocket 实例为准。
- 2026-08-20 升级后真实 `.app` 冷启动验证：全新隔离 profile 在默认 8100 上完成本地身份绑定，
  两条 control WS 均连接且工作台显示“已连接”；陈旧 profile 的事实不匹配只记录有界拒绝，
  未再产生无界重挑战。credential 聚焦回归 `10 passed`。

## Task 3 已投入通用执行主链的能力

### Durable Run start

每个 durable Run 在 `execution_runs` 同一事务内写一条
`execution_run_start_snapshots`。快照冻结 canonical messages、session cursor、
PreparedRunContext、capability/catalog/tool refs、provider launch policy 和 terminal
delivery set；恢复按 ref/hash 重开，不能用当前 UI/profile 状态重建。

产品扩展通过 host-only `StartCommitExtensionV1`、
`AfterStartCommitHandshakeV1`、`TerminalCommitExtensionV1` 和
`AfterTerminalCommitCleanupV1` 接入。Kernel 只验证内容寻址契约和调用时序，不解释
Companion 业务。

### Provider dispatch 与物理 effect fence

`ProviderInvocationCoordinator` 为每次真实 transport dispatch 建立独立
claim/outcome。claim 确定提交前物理请求数必须为零；handoff 后取消、断连或结果不确定
统一落 `unknown`，禁止 AgentLoop、Registry 或 SDK 盲目重试。

OpenAI-compatible、OpenAI、Anthropic 和 Gemini coordinated 路径关闭 SDK retry。
ReAct、Code、Research、DeepResearch、PPT 与 `execute_prepared` 的最后物理调用前共享同一
`RunExecutionFencePort`。流式 delta 只发送带 `invocation_id + stream_epoch` 的 provisional
envelope；durable outcome 提交后才发布 canonical 结果，失败、取消或重连会撤回临时缓冲，
SessionDB 不保存半截正文。

### 串行 durable writer

workflow schema v17 使用单进程、单规范化 DB path 的 `ExecutionWriteLane`。它复用一条
`WAL + synchronous=FULL` writer connection，但仍保持每个既有 crash boundary 独立
`BEGIN IMMEDIATE/COMMIT`。进入 commit 后取消会等待确定结果；无法证明结果时 lane
poison/close 并返回 typed unknown，不在 lane 内自动重放。

### GrowthEvent 与消息投影

Task 3 已定义三类 durable GrowthEvent：用户消息、客观工具/Run outcome、用户决策。
用户消息与 `companion_ingress_outbox` 在同一个 state.db 事务提交；tool outcome 和 terminal
只消费 durable execution event，不从进程内 recorder 反推事实。

Growth delivery contributor/sink 已具备冻结 target、owner fence、幂等和 tombstone 能力，
并在 Companion authority 就绪后成为生产写入口。主消息提交使用
`append_user_message_with_growth_outbox()`，所有消息先按普通 committed ingress 落库，不在
模型前用正则猜测“纠正”或“能力请求”。`ProductTurnPreparer` 的同一个模型预处理返回 typed
`growth_signal_kind`（`none / explicit_correction / explicit_capability_request`）；host
只做枚举、身份和幂等校验并单调提升 outbox 语义，再按 owner fence drain。模型失败或没有
合法值时以 `none` 收敛，不阻塞普通聊天。dispatcher 校验 committed message bytes/hash 后写
`MESSAGE_INGRESS` 并幂等创建零工具 reflection job。Product Harness 的 native terminal
delivery 同时写 `run.final` 客观 outcome，且跳过 reflection/evaluation 内部 Run，避免递归。
普通聊天仍走原 Product Harness；成长写入、terminal delivery 与通知投影只由当前
owner-generation 的 Companion 分支执行。

2026-07-25 的真实源码 Tauri 主消息页点击证明：用户消息 `id=25`、outbox
`message:default:25(status=delivered)`、`message_ingress` GrowthEvent、前台 `run.final`
GrowthEvent 与 `reflection:growth_f03e…` job 使用同一 root Run。该 job 在固定测试时钟下被
quiet-hours 策略保留为 queued；当时的候选/评测/激活表为空，因此这份证据只证明生产入口，
不代表 S-1 或 Task 13 已重新完成。

2026-07-26 的 l44 复核继续从真实桌宠点击“消息”打开 `DeskPet · 消息`，发送两条明确
纠正。`message:default:42/43` 分别形成
`growth_1398032a3656c389e06eac31a45dbc9d` 与
`growth_1d9a5bd150d789de9dc2d1d00a7a0a8a`，对应 reflection job 都进入既有
Product Harness 并按 durable retry 执行三次；真实 provider 每次返回 HTTP 402
`account balance is insufficient`，最终 job 为 `failed/background_run_failed`。因此这两个
新事件没有 candidate、evaluation、activation 或 notification；隔离 DB 中另有 2 个更早
诊断 candidate artifact，不能把它们误归于 l44。

2026-07-27 余额恢复后，冻结场景重新从真实主消息页执行完成。S-1 的 built-in
`summarize-day` 低风险 override 形成候选、评测、operation receipt 与新 binding，并在后续
输出中恰好保留两项且每项包含下一步；S-8 从成长卡真实点击回滚后恢复冻结的内置版本，
同 session 与重放均未重复副作用。S-2 三个独立上下文晋升简洁偏好，第四次输出仍保留结论、
负责人和行动项；S-3 到期只投递一次 reminder 与非空草稿，重启没有重复；S-4 的详细回答
只作用于当前 Run；S-5 在 Auto 模式下仍停在高风险 activation confirmation，确认前
dispatcher/effect/external send 均为零。

### Task 13 follow-up 生产编排

`GrowthProductionPipeline` 是 Companion Runtime handler 的 host-owned 后处理器，不是第二套
Harness。它为 reflection/evaluation/delegated candidate Run 构造零工具
`PreparedBackgroundContext`，由 `BackgroundRunAdapter` 继续调用同一
`RunClient → RunKernel → ReActDriver`。`ReflectionPostprocessor` 只接受 schema/version、
owner、source event、目标和稳定 hash 都匹配的 typed JSON；普通模型文本不能直接激活能力。

reflection 成功后，pipeline 依次创建/复用 proposal 与 candidate-build job；candidate
完成后只从 immutable draft receipt/material 组合候选，再创建冻结 evaluation；评测报告和
RiskPolicy 通过后才交给 `ActivationDispatcher` 与 Capability Manager receipt saga。
Store 的 scheduler claim 可按 exact `build_id` 过滤，避免一个已绑定 build 的 job 误认领
更旧过期 build；claim owner/epoch/attempt 在同一事务校验。provider handoff unknown 会
稳定归类并 fail closed，原始 plan 的 `trigger_failure_set_id=None` 在恢复时不再被后来
failure set 改写；失败由同一模型通过 durable failure/replan 边界吸取原因，不另建恢复器。

## Task 4 已完成的双层偏好权威

`PreferenceResolver` 已实现 scope-first 解析：当前 request 的 typed 显式例外优先，但只进入
该 Run 的冻结快照；持久层内部按“显式长期纠正 → 已晋升长期 → 近期隐式 → 模型假设”选择
winner。隐式偏好默认需要三个未衰减、未 tombstone、无冲突的独立 `context_key` 才能晋升，
阈值只从 typed `[companion.growth]` 配置构造。

一次 preference event 由公开 `CompanionStore.apply_preference_event()` 在同一个
`BEGIN IMMEDIATE` 内提交 GrowthEvent、evidence、状态 CAS、策略审计和 detail version。
同 event 重放只校验 immutable facts，不重复晋升或增加版本；异 hash fail closed。transition
audit 可查询当时冻结的 policy value/hash，Run dependency 只引用实际 winner 的
key/state version/content hash/evidence ids。request-scoped override 以 version/hash、零
evidence 进入本 Run 快照，不写回长期偏好。

测试组合中的 `ProductTurnPreparer` 会把解析结果写入
`run_growth_snapshots/run_growth_dependency_items/run_growth_dependency_evidence`，再把真实
snapshot ref/hash 交给 `PreparedRunContextV1`。删除 evidence 与既有 lineage invalidation 在
同一 Store 事务内重算、降级或撤销偏好并投递通知。Companion Facts 分支只生成 preference
observation/evidence；legacy FactsStore 默认行为保持不变。

生产组合根现由 `CompanionPreferenceResolver` 提供偏好读写。旧 JSON 数据只在 cutover
prepare 阶段按 `legacy_local_profile`、稳定 hash 和幂等 marker 导入，marker 后保留为只读
升级残迹；当前 Relay owner 不会取得 legacy local 数据的归属。

## Task 5 已完成的可恢复 Runtime 基础

`CompanionRuntime` 只有一个 scheduler task，并以有界 child set 执行 durable job。生命周期
提供幂等 `recover/start/pause/switch/close/diagnostics`；停止时先拒绝新 claim，再有界等待、
取消剩余 child，并将安全 reflection lease 重排队。生产 profile coordinator 先以
`start_prebound()` 校验 CompanionStore 中 exact owner/generation/binding epoch，再把
`IdentityReadyGate` 冻结为 ready；失败会暂停 Runtime，且不会开放主消息 Companion 入口。

Runtime 每次 claim 前从 workflow execution UoW 重算未终态 foreground root；按
`root_run_id` 去重，background venue 不计，缺 port、缺字段或读取异常一律视为 busy。
Companion Store 以单事务完成 budget window 计算与 job claim：leased job 计 reservation，
terminal job 计 actual usage。恢复只把明确列入 safe retry policy 的 reflection 排队；
过期 reminder 标 expired，delegated/external/unknown kind 标 fail closed，不能盲重试。

`ClockPort` 统一 wall clock、monotonic deadline 与 scheduler sleep。`DevFrozenClock` 只在
非 frozen build 且 Tauri 进程同时提供 `DESKPET_DEV_MODE=1` 与严格 absolute UTC
`DESKPET_E2E_CLOCK_UTC` 时构造；生产、打包或非法值不能激活，也没有 chat/WS/tool tick
入口。Reminder 中文解析与 occurrence exactly-once 仍属于 Task 11。

Profile bind 的 canonical owner key 已统一为
`companion:<profile_id>:<generation>`；真正切号会在等待 cleanup 前先置 identity unready，
同 owner 重绑不会暂停 Runtime，新进程首次 trusted bind 会执行注入 lifecycle 的 recover。
Runtime 与 IdentityReadyGate 共用这份 durable binding，避免“Runtime 启动必须先 ready，
Identity ready 又依赖 Runtime 启动”的循环。

原有 memory reflection loop 仍是独立 lifespan-owned、可取消且可等待的 memory worker，
不再承担 Skill Codifier 或成长 authority。关闭顺序为 Companion Runtime/memory reflection
→ Harness/Kernel → Capability 与
Workflow launcher → execution UoW；Task 3 的长寿命 writer 会显式 close。

## Task 6A 已完成的统一动作策略

ToolSpec 的 immutable identity 已覆盖 stable handler、typed effect/idempotency/target
normalizer、artifact build 与 dispatch adapter；source/effect/build 三份 checked manifest
由同一 generator 对账并随 frozen backend 打包。PreparedToolSet 冻结 confirm-only 集和
effect policy hash；旧 snapshot 缺字段时按空集合兼容读取。

所有 Companion foreground/delegated 的 send/delete/pay/credential/privacy/unknown 都由
host policy 强制 confirm-only。ReAct 持久化 policy snapshot ref/hash，Auto 不能短路；
显式确认生成绑定原 durable decision/nonce/call/effect 与 snapshot 的一次性授权。
ToolExecutor 在 physical dispatch 前再次从 pinned PreparedToolSet 重验同一 hash 和 exact
grant。reflection/evaluation 不暴露这些高风险工具。

Companion schema v2 新增 durable owner memory read scope。generation-0 growth snapshot
一次冻结 preference 与 owner/generation/session-set/as-of memory scope；`memory_recall`
模型只提供 query/limit，production Retriever 使用 readonly/query-only 连接且不更新
salience/last-touch。

`BackgroundRunAdapter` 直接走 Kernel typed client，不创建产品展示 session；action decision
service 只从 trusted message-panel control identity 恢复原 execution decision。
`PreparedToolDispatch` 已由 `ToolRegistry.begin_prepared()` 与内建
`FunctionPreparedToolDispatch` 接入现有 ToolExecutor：prepare 不产生 effect，start 返回
绑定 exact call/effect/adapter/runtime identity 的 ACK，completion 才等待长响应。
`CurrentExecutionScopeLeasePort` 已成为注入式执行边界；owner/generation/epoch 只经
`PreparedRunContextV1 → RunContext → ToolExecutionContext` 的 host-only 链传递。生产
CapabilityPlatform 已从 durable RunStart/catalog rows 重建 current scope，并注入主组合根；
一个 ToolBatch 共享同一短 lease，直到全部 dispatch-start ACK/NotStarted/Unknown 落定才按
反向锁序释放。当前没有第二套临时 effect authority。

## Task 7 已进入主线的第一批 Capability / Skill foundation

workflow schema v18 与 Capability schema v2 已由同一 workflow migration owner 管理。
Capability binding 现在带 owner 与 management policy；Store 已具备永久 owner detail
token、OwnerBindingSetStamp、RunCatalogContentStamp、ProcessCatalogStamp、run-catalog
snapshot 和 lease-intent 基础。ProductVenue 只通过
`CapabilityPlatform.prepare_run_catalog_lease()` 组合一次 Hub catalog，并把 start/terminal
extension 与 after-commit handshake 放入同一个 `PreparedRunContextV1`。

17 个 shipped Skill 已各自迁移为 v2 first-party Capability Pack，旧 builtin SKILL.md
内容副本已经删除。v2 parser 会校验 Skill manifest/frontmatter 的 canonical
`allowed-tools` 等值关系，并支持固定 Workflow adapter 与严格 Personal Workflow DAG。
Loader 已删除 script 执行路径；slash 输入会重新进入主消息 ProductVenue/Harness，而不是
直接调用 Loader。

生产 `SkillLoader()` 不再隐式挂载用户目录；只有显式指定目录时才作为只读 migration/test
reader。first-party 与生产 Skill 只经 hash-verified managed discovery projection 提供发现。
matcher identity 覆盖 owner/pack/version/manifest/content；
`skill_invoke` 与 slash 都产生 typed frozen scope 并在真实 Run resolver 再次校验。
Personal Workflow 已冻结 ToolSpec topology、JSON Pointer root 与 stable effect/call id；
Task 10 已接入解释器、journaled effect 与恢复链，最终完成状态仍等待相邻 child/fence
分片回归。

Task 10 已用 frozen invocation/activation 链收口 managed Skill 与 legacy Loader 的发现
投影边界，并接通 Personal Workflow Effect/UoW interpreter。run-catalog exact
lease/projection receipt、`CapabilityStoreRuntimeSetLedger`、native Job/MCP adapter 与
owner projection commit 已进入生产 Platform；任何无法证明 exact build/runtime identity
的 entry 仍在 Run capture 或启动前 fail closed。

## Task 8 已完成的 Candidate 生产组合

Companion schema v3 已把显式用户创建 Skill/Workflow 的 trusted root request 在通用
Builder admission 前转换为唯一 `StructuredGrowthProposalV1`。Store 在一个事务内冻结
user-message evidence、target、proposal bytes/hash、source/target fence 和
`candidate_builds(status=proposed)`；同 source 重放只返回原 build，owner/evidence/hash
漂移 fail closed。

`CompanionCandidateBuildCoordinator` 使用 Store 签发的 host-only permit 驱动
`launch_pending → child_precreated → running → handoff_pending → built`。Companion 与
execution 两库之间不持双 writer；row 有/child 无、child 有/row 未 ACK、receipt 已提交但
handoff 未 ACK 都恢复同一 launch/child/receipt，不重建 bytes 或启动第二个 child。

workflow schema v19 新增 immutable `execution_candidate_draft_receipts`；v20 追加
immutable `execution_candidate_draft_materials`，保存 Builder 实际产出的 canonical archive
bytes 与 manifest/file-set/archive hash。candidate-only terminal extension 把 child final
event、host-issued receipt、精确 material 和 terminal extension receipt 放进同一 execution
UoW；commit 前故障全部不存在，commit 后重放逐字段核验同一 durable row，不重跑 child、
不从 seed 重建 bytes。

候选 identity builder 已按 owner、完整文件 bytes/mode、manifest template、Tool/Workflow
schema、permissions 和 effect topology 生成无自引用的确定性 version、manifest/archive/
package hash；同 `(pack_id,version)` 的异 exact hash 返回 typed collision。
`CapabilityPackageValidator` 统一 Task 0 限额、Windows 路径规则、Zip central-directory 与
streaming actual bytes/CRC/hash，并只签发 host-only `ValidatedCapabilityPackageRefV1`。

`CapabilityBuilderHost` 已区分 candidate-only 与 general-install：candidate-only 只返回
确定性 build evidence，禁止调用 Manager。host-owned terminal extension 在同一 UoW 落
receipt/material；`SqliteCandidateDraftMaterialQuery` 只读返回 exact immutable bytes。
`CandidateCompositionService` 按 receipt → 当前 Store fence → exact material → 统一 package
validator 的顺序校验，并用 `CompanionStore.commit_candidate_build()` 在一个事务内 CAS
build detail、创建/复用 package/source/attempt、写 blobs/files 并推进 `built`。

first-party/local/configured/git/companion-growth 来源现在共用安全 materializer 与
`CapabilityPackageValidator`；Manager 只接受并在安装前再次验证
`ValidatedCapabilityPackageRefV1`。普通 Builder 的 general-install 行为保持不变。
reserved builder child 的生产 scheduler/固定 ToolSet 已随 Task 13 authority cutover
注入；候选仍必须经过独立评测、RiskPolicy 与 activation saga，不能直接发布。

## Task 9 已完成的评测与激活 foundation

Companion Store 已保存 immutable risk facts、evaluation suite/case/attempt/report、activation
decision/request/operation/receipt 和 24 小时 guard 状态。`StaticRiskPreflight` 不 import、
不运行 candidate code；评测只消费 Task 8 的 exact validated package，使用 checked-in suite
resource、冻结的只读 memory snapshot、逐 case durable launch/ACK/cleanup/recovery，并由
`CapabilityRiskPolicy` 从 manifest/effect/permission/code facts 作确定性分类。

`CapabilityActivationSaga` 与 guard monitor 已建立低风险自动激活、需确认激活、回滚、
disable、崩溃恢复和 immutable lineage 的事务边界。可执行候选必须提供 host-issued activation
decision、exact package/code digest 与
`activation_risk_ack=persistent_local_code_no_os_sandbox`；通用 Auto、TaskGrant 或 lifecycle
permission 都不能替代。

`CompanionActivationPlatform` 是 Task 9 的唯一 Manager/Runtime façade。它在任何静态准备前
校验上述 decision proof，并要求 Manager 暴露“不注册、不启动、不换 binding”的
`prepare_installed_static()` 生命周期接口。Task 13 已接入该静态 seam；静态准备与
runtime start/health 仍由 exact decision/package/code digest 和当前 owner binding fence
约束，缺少任一事实继续 fail closed。

## Task 10 已完成的 Personal Workflow / Skill Runtime

一个 Run 只使用 publish-lock 内的单次 catalog capture。冻结选择保存 owner、pack/version、
manifest/content hash、ToolSpec exact facts 与 `RunCatalogContentStamp`；Skill 的有效工具面
只取声明 `allowed-tools`、PreparedToolSet 与该 capture exact refs 的交集。`skill_invoke`
通过 execution UoW 原子提交 activation receipt、continuation 与 effect 结算；commit-unknown
恢复只查询同一 immutable activation，不重新解析当前目录或重复物理 effect。

workflow schema v22 为 `workflow.personal_v1` launch ticket 加入 partial-unique selection
identity，并加入 immutable `execution_skill_scope_activations`，同时持久化
`RunContext.owner_key/profile_generation/binding_epoch`。Personal Workflow 解释器只执行
冻结 DAG 与 ToolSpec topology；effect 在 checkpoint 前已结算时，恢复会复用 journal
receipt，测试已证明物理调用仍为一次。v20→v21 结构与 v21→v22 owner identity migration
均已验证。

生产 Skill 正文读取不再把 `ManagedSkillDiscoveryProjection` 当 body authority。
CapabilityPlatform 初始化后构造 Manager-backed `SkillPackSnapshotResolver`，同时注入
主 `AgentLoop` 和既有 `ContextAssembler/SkillComponent`；Assembler 与 SkillComponent
通过单次、Manager-backed 的显式 bind seam 接线，不把私有字段当生产契约。projection
只负责发现和签发 typed selection，正文与资源从 exact Manager version root 按
manifest/content hash 重验。
`skill_tools` 的 Run 内调用继续使用 run-catalog resolver，两者不形成第二份 live binding。

`CompanionTurnAuthority` 把可信 owner、generation、lease 与 RunStart capture 组合为 typed
authority；ProductVenue 只把同一次冻结 capture 交给 Kernel，不再次读取 live identity。
child 先建立独立 pending pin 与 bound lease intent，只有 after-commit
`SnapshotLeaseReadyGate` 打开后才启动 Driver；父 Run terminal 不会释放 child authority。
provider launch、tool dispatch、terminal commit 与 delivery 都读取同一 execution fence。
恢复链按 frozen ToolSet/Context OS snapshot refs 校验，不再依赖 legacy tool-name list。

MCP host entry 只有在 DeskPet adapter、真实 stdio launcher 或 npx 已安装 package bundle、
package lock 和 launch/config identity 都能冻结成 `ExecutionBuildIdentity` 时，才能进入
生产 Registry 与 durable Run catalog。2026-08-11 起，无法证明来源的 MCP tool 在连接阶段
即 fail closed 并从生产 Registry 隐藏，不能再污染普通问答的 prepared tool set；macOS npx
缓存缺省路径按 `~/.npm` 解析（环境变量与 Windows `LOCALAPPDATA/npm-cache` 仍优先）。聚焦
回归 `21 passed`，真实 Tauri 已确认 filesystem/playwright 均取得 build identity，原
`prepared_tool_build_identity_missing` 不再出现。

## Task 11 已完成的持久 Reminder、草稿与外部确认恢复

Companion schema v4 与 workflow schema v22 共同保存 reminder job、occurrence、delivery
outbox、execution wait 和原 Run continuation。新的
`core.reminder_create.v2/core.reminder_list.v2/core.reminder_cancel.v2` 使用 checked
source/effect/build authority；create/cancel 以稳定 effect id 跨库去重，重复执行只返回
原 receipt。三个 V2 handler 已随 Companion authority 原子进入生产 Registry；
`legacy.list_reminders.v1` 及 `backend/tools/reminder.py` 已退休，不保留旧名 alias。

`ReminderScheduler` 复用唯一 `CompanionRuntime`：occurrence 用 CAS claim/reclaim，daily
frequency 在投递前耐久预留，过期 occurrence 标记 expired 而不补发；quiet-hour/policy
会延期而不是丢弃。通知先写 Companion outbox，再由后续 Task 12 投影。需要模型准备的
reminder 只创建非空 delegated draft，能力范围限定为 read/draft/reversible-local，
`external_send_allowed=false`；occurrence 与 outbox 在同一 settle 中完成。

外部动作即使在 Auto 模式也进入 `waiting_decision` 并释放 job lease。用户确认后恢复同一个
Run、decision、call 与 effect，继续使用既有 decision/grant/effect 权威，不建立第二张
授权表；缺少外部 receipt 时结果为 unknown 并禁止重发。终态 delivery 只结算原 job/wait
一次，重启和重复确认均不产生第二次物理发送。

## Task 12 已完成的 durable 通知与主消息页投影

notification/outbox 以稳定 `profile_id + profile_generation + notification_id` 作为逻辑
inbox。投影 worker 每次 claim 后解析当前可信 default Session/epoch，缺 route 时保持
pending，route 变化时用 `relocate_projection_if_epoch()` 移动同一 excluded row，不复制
第二条通知。Session clear、route change 与 Companion redaction 都有 durable outbox；
已投影通知在 Session 行缺失时会重读 Companion 当前事实并只 append 当前 envelope，已
redacted 的只能生成固定 tombstone，旧 summary/actions/detail 不会因迟到 outbox、F5 或
重启复活。投影异常会立即释放精确 outbox claim，不需要等待 lease 到期即可重试。

重要 activation/evaluation/rollback/forget 事实即时生成通知；普通偏好变化进入确定性
日桶摘要。Reminder 投递与通知落卡同事务结算。删除 owner generation 会先写
`owner_deleted` 审计，再 supersede 旧通知、清空动作并 dead-letter 待处理投影，不把旧卡
转移给新 generation。

live 与 history 使用同一 owner-fenced Companion envelope。history hydration 会按当前
notification status、payload hash 与 redaction version 覆盖旧 Session 内容；Store 不可读、
owner stale 或 generation 不匹配时 Companion 行 fail closed，普通 conversation history
仍正常。前端 reducer 先比较 active owner-generation，再按 event/seq/route version 单调
归并；retract 清空 actions 和 detail cache，切号只移除上一 owner 的 Companion 卡。流式
provisional delta 只在内存中按 run/invocation/epoch 保存，断线、retract、unknown 或刷新
不会留下幽灵半截文本。

`companion_event` 强制 `context_visibility=exclude`。FTS、vector/backfill、chunker、
retriever、summarizer、reflection 与 QASet 均在数据读取层排除，人工残留的 excluded chunk
也不能被召回；Task 6 的 readonly owner scope、as-of 上界和零写约束保持不变。

`CompanionDetailQueryPort` 是 evidence、diff、evaluation、decision、operation receipt、
current binding 与 audit 的唯一详情入口；内容从 owner-scoped 权威表重建，不信任通知
预填详情。请求只接受五个业务字段，page size 最大 20、
单项最大 8 KiB、响应最大 64 KiB；opaque cursor 由 canonical HMAC 绑定 owner、
control epoch、notification、section、detail version 与 last sort key。每页都由 Companion
`Vc` 与包含 absent key 的完整 Platform `VpVector` 前后双读包围，任一变化返回
`detail_changed`，所有输出统一脱敏；无 Platform key 的详情使用确定性空向量，不依赖
无关 Catalog readiness。

主消息页已经接入成长卡片、分页详情 modal 与三类相互隔离的 decision。external action
只发送 `{decision_id,allow}` 并恢复原 execution decision；evaluation 与 activation 使用
不同 nonce，可执行激活还要求
`persistent_local_code_no_os_sandbox`。Auto 模式不能越过这些确认。冷启动 identity
challenge 会在 listener 注册后消费连接内缓存，Relay identity restore 采用有界重试；
`ControlChannel` 每次重连清空 latest-message cache，旧 challenge 不会跨连接复用。

## Task 13 已完成的生产 authority cutover

生产启动顺序固定为：

```text
Capability runtime rehydrate
  → 组合 Companion Store/Runtime/Router（入口关闭）
  → 构造 Product Harness（入口仍关闭）
  → 执行 durable GrowthAuthority cutover
  → 启动通知投影与 Companion runtime adapter
  → 打开 Product ingress
```

这样 V2 Reminder、Capability 生命周期和 Harness 注册表都已存在后，才提交唯一 Companion
pointer。启动日志依次记录 `product_harness_ready_ingress_closed`、
`growth_authority_ready phase=companion` 与
`companion_runtime_adapter_ready product_ingress=open`。marker 前故障可恢复 legacy；
marker 后不完整只能进入 `paused` 并前滚，不能重开旧 writer。

旧用户 Skill 只有在能取得 immutable pack/Manager 发布 receipt 时才允许迁移。当前组合根
没有这个证明，因此非空 legacy Skill inventory 在 `legacy_import`、marker 前 fail closed，
且不会先部分导入 Preference/Candidate；空 inventory 才继续。cutover 使用
CapabilityStore 的真实 binding generation 与 owner stamp，当前无 Capability mutation，
所以 old/new facts 相同，不伪造 publish。

旧 `SkillCodifier/CandidateProposal/ToolPathRecorder`、Presenter/Voice codify 回调、
裸 `skill_candidate_confirm` 入口和进程内 Reminder 实现已经删除。偏好只由 Companion
resolver 写，Reminder 只公开三个 V2 handler，能力 active pointer 仍只由
`CapabilityPackManager + CapabilityStore binding CAS` 修改。

Capability rehydrate 对历史已安装包只接受一种兼容迁移：当前 ToolSpec 必须能精确重算旧
fingerprint v1，Store 才以 CAS 升级到当前 fingerprint；任意未知漂移继续 fail closed。
可信身份恢复则先验证 durable prebound owner，再开放 IdentityReadyGate。若旧 `default`
session 已有消息，不会被当前 Relay owner 认领，而是创建新的空 UUID owner inbox 并通知
前端切换。

## 当前验证

- Task 3 backend 聚焦组合：`250 passed`。
- Provider/Agent/adapter/stream 聚焦：`88 passed, 2 skipped`；相邻
  Workflow/Code/PPT/DeepResearch/ReAct：`106 passed`。
- 主消息页真实 Tauri 验证：manual 策略下 Run 写入 start snapshot 后进入
  `admission.waiting`；UI 取消后同一 Run durable 终态为 `cancelled`。设置页开启 Agent
  全开后，workflow DB 为 `mode=auto, generation=1`，backend 日志记录
  `permission_auto_mode_set enabled=True`。
- 当前阶段的实现和真人证据见
  [Task 3 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task3-results.md)。
- Task 4：Companion/Preference/Router/Preparer 组合 `178 passed`；Facts 回归分别
  `62 passed` 与 `21 passed`。该阶段生产 authority 静态/组合审计确认 Router 仍为 legacy，
  Companion preference writer 未进入生产请求路径。实现证据见
  [Task 4 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task4-results.md)。
- Task 5 focused `59 passed`；Companion/context/runtime activation/write lane/venue 扩大
  回归 `182 passed`。scheduler/child/test process survivor=0，8100/5173 无监听。实现证据见
  [Task 5 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task5-results.md)。
- Task 6A：manifest/policy `31 passed`，memory scope/readonly recall `65 passed`，
  background/action/dispatch 邻接 `84 passed`，新增 confirm-only/authorization
  `24 + 4 passed`；扩大组合 `151 passed`，一个既有 timing case 隔离重跑通过。实现证据见
  [Task 6A 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task6-results.md)。
- Task 6B dispatch slice：ToolExecutor/Kernel `25 + 62 passed`，brokered
  effect/workflow production 邻接 `21 passed`。实现证据见
  [Task 6B 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task6b-dispatch-results.md)。
- Task 7 第一批：Companion `210 passed`、Capability `165 passed`、Skill/slash
  `83 passed`、execution build manifest `7 passed`；Windows long-path 与 17 pack install
  聚焦通过，相关 pytest survivor=0。范围与未完成 blocker 见
  [Task 7 foundation 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task7-foundation-results.md)。
- Task 7 Skill/Workflow 分片：聚焦与兼容回归 `87 passed`，编译与 diff check 通过，
  精确进程 survivor=0。证据见
  [Task 7 Skill/Workflow 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task7-skill-workflow-results.md)。
- Task 7 runtime/authority 集成：Capability `165 passed`，Companion `259 passed`，
  runtime/catalog 聚焦 `18 passed`，Task 7 关键组合 `155 passed`；Harness 分批回归及
  authority/parity/LOC 门恢复全绿。两次超时 pytest 均按 exact PID/create-time 清理，
  survivor=0。证据见
  [Task 7 runtime 集成结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task7-runtime-integration-results.md)。
- Task 8 Candidate foundation：proposal/identity/coordinator/receipt/package-limit/schema 与
  execution 邻接组合 `140 passed`；package 安全切片独立 `90 passed`，监控 PID 16728
  exit=0、survivor=0，释放 private memory 811008 bytes；组合复核 survivor=0。证据见
  [Task 8 foundation 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task8-foundation-results.md)。
- Task 8 生产组合：Candidate/Builder/receipt/composition `39 passed`，Companion 全量
  `335 passed`；Capability 全量 `249 passed`，监控树 `survivor=0`，释放
  982175744 bytes private memory。Harness 相邻按文件通过 `4 + 62 + 4 + 14`，大组合观察到
  既有 aiosqlite event-loop shutdown warning，超时树均按 exact PID/create-time 清理为
  `survivor=0`。证据见
  [Task 8 生产集成结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task8-production-integration-results.md)。
- Task 9：Companion 全量 `335 passed`（覆盖 preflight、suite、evaluation execution、
  immutable risk facts、risk policy、activation saga/guard/platform）；Capability 全量
  `249 passed`，activation platform 聚焦 `15 + 20 + 6 passed`。可执行确认不可被 Auto
  替代，缺静态生命周期接口时 fail closed。证据见
  [Task 9 评测与激活结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task9-evaluation-activation-results.md)。
- Task 10 最终门：后端 **443 passed**，前端 `11 passed + tsc pass`。真实源码 Tauri
  主消息页点击发送“44减17”，UI 返回 **27** 且状态回到“空闲”；durable Run
  `2a2f1e89ead057f4a853896d159be580` 为 `completed`，exact owner identity 与
  `run.final={"text":"27"}` 均已落库。E2E 的 19-PID 树释放 9653006336 bytes private
  memory，8100/5173 释放，survivor=0。证据见
  [Task 10 最终验证结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task10-runtime-verification-results.md)。
- Task 11：Reminder/外部确认组合 **102 passed**，Companion 全量 **394 passed**；
  MCP host identity 与 catalog lease 聚焦 **23 passed**。真实源码 Tauri 主消息页点击
  输入“Reply only 35: 70/2=?”，真实 provider HTTP 200 后 UI 返回 **35**，durable Run
  `5597356dc24d55eeb965a9597b3ff5be` 为 `completed`，owner 为当前
  `companion:<profile>:1`；该阶段生产 authority 仍为 `legacy/generation=1`。最终 32-PID
  Tauri 树释放 10055626752 bytes private memory，8100/5173 释放且 survivor=0。证据见
  [Task 11 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task11-reminder-results.md)。
- Task 12：通知、详情、五种 owner control 与相邻回归组合 **126 passed**；前端
  **8 files / 88 tests passed**，`tsc -b` 通过。真实源码 Tauri 主消息页由生产
  `record_growth_event -> forget_growth_event` 链产生 `growth_forget` 卡片，真点击
  “查看详情”并进入 audit 页看到 `source_table: audit_events` 与脱敏审计 hash；F5 后卡片
  从 history 恢复。notification/projection outbox 均 delivered。最终 32-PID 树释放
  11108134912 bytes private memory，端口 listener=0 且 survivor=0。证据见
  [Task 12 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task12-notification-results.md)。
- Task 13：聚焦 `166 passed`、Companion `476 passed`、Skill/Preference
  `127 passed`、Capability `251 passed`、Workflow `53 passed`，前端 `851 passed`
  且 tsc/manifest/diff check 通过；R4.5 预算门转 Task 14 收口。2026-07-25 真实源码 Tauri
  Relay mode 主消息页切换到空历史 owner inbox
  `26f5276d-69e9-42b0-ba65-b4a8988b86d6`，真人点击输入
  “请只回复：Task13主消息页通过”，UI 回复“Task13主消息页通过 ✅”并回到空闲；
  backend `chat_v2_final` 使用同一 session id。最终精确清理 roots `16040/13620` 的
  21 个进程，释放 9749762048 bytes private memory，survivors=0，8100/5173 listeners=0。
  证据见
  [Task 13 cutover 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task13-cutover-results.md)。
- Task 14：故障矩阵、性能、隐私聚焦 `54 passed`；Companion 确定性全量
  `540 passed`、组合根 `39 passed`，smoke 明确输出 `DECISION: SHIP`。真实
  ProductVenue/Kernel/Driver 的 Final、ToolBatch(1/3)、existing/new goal 与
  retry/fallback p95 回退为 `+5.442%～+6.202%`，均低于 10%，仍使用同一
  `WAL + synchronous=FULL` ExecutionWriteLane。当前
  raw/adjusted/core/Kernel LOC=`129997/129488/44063/1277`，public operations=6、
  unknown=0；AgentLoop AST=3800、transaction starters=53。aiosqlite 测试连接生命周期
  已收口，相关 `79 passed` 在 worker warning 升级为 error 后仍全绿。证据见
  [Task 14 质量门结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task14-quality-gates.md)。
- Task 15：S-1～S-5、S-8 已从真实 Tauri 主消息页完成 `6/6 PASS`；S-6/S-7/S-9
  通过确定性自动化。每个真人场景都使用当前源码 backend、单一 Tauri/Vite 生命周期、
  固定 DEV clock 和真实 provider，并以截图、日志、只读 DB 对账与精确进程清理收口。
  S-5 证明 Auto 模式不会越过 external-send/权限扩张的高风险激活门，确认前
  decision/effect/physical send 均为零。证据见
  [Task 15 自动化](../plans/2026-07-24-human-anchored-companion-growth/evidence/task15-s6-s7-automation.md)
  与 [真人结果账本](../plans/2026-07-24-human-anchored-companion-growth/evidence/manual-results.md)。

## 最终验收状态

Task 14～16 已完成。Companion 全量为 `639 passed`；Skill/Memory 为 `128 passed`；
Harness 为 `636 passed, 4 xfailed`，Capability/Workflow 为 `308 passed`；Frontend 为
`93 files / 853 tests passed`，TypeScript、`cargo check` 与 `78` 项 Rust 测试通过。
deterministic smoke 为 `639 + 40 passed`，结论 `DECISION: SHIP`。仓库级
`cargo fmt --check` 仍命中本计划开始前已存在的 Rust 树格式漂移，本计划没有 Rust
生产代码修改，因此没有批量改写无关文件。

真人场景 S-1～S-5、S-8 为 `6/6 PASS`，确定性场景 S-6/S-7/S-9 为 `3/3 PASS`。
证据账本见
[manual-results.md](../plans/2026-07-24-human-anchored-companion-growth/evidence/manual-results.md)，
100% DoD 与进程/worktree/staged-scope 审计见
[task16-delivery-audit.md](../plans/2026-07-24-human-anchored-companion-growth/evidence/task16-delivery-audit.md)。
