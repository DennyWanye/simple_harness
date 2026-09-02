<!-- last-calibrated: 892aa15c3005e1d39d4700879f8532568dfa4100 -->

# simple_harness Long-Running Agent Architecture Baseline

## S5a Context/Route Authority（2026-09-02 生产事实）

- 每个 provider turn 由 `ProductRunContextAuthority.prepare_snapshot` 重算 RunContextSnapshot：
  protected 前缀（system+memory 块）+ 最近 10 完整因果组（tool 对不拆、open 尾组不裁、大结果
  typed summary+page_ref）；窗口从 start context_metadata（标量 context_window / run_binding /
  harness budget 三来源）解析映射到冻结档（4k/8k/32k），超 effective budget 裁尽仍超 →
  `ContextBudgetExceeded` fail-closed；`payload_hash == expected_request_fingerprint` 自检 +
  SDK react loop 三 hash 链（receipt 在 provider reservation 前入 checkpoint）。
- 五路 `context_route`（CONTEXT_CONTROL、direct kernel、host-composed）由 Host 裁决并写 v45
  durable decisions/invocations；search 命中不授权，resume 需 exact ID；continue_active 承
  最近 ROUTED_TASK durable 决策并与 binding head 复核；create_new 走幂等 create+append_binding。
- 终局 `no_recall` 前 mandatory occurrence-inbox reconcile（Memory 0.6 只读 inbox：matched ∧
  live/presentable ∧ privacy 资格 ∧ 非 suppressed ∧ occurrence_key ∉ v45 presented set）；
  pending → `NoRecallBlockedError` + 合资格摘要注入 snapshot；presented set 由 Host 持有，
  S5a 零写入（S5b settled 状态机接管）。
- memory_standalone → `HumanMemoryV7Runtime`（human_memory_v7.db，fresh-only）双 recall lane；
  嵌入模型 WeMM-Embedding-2B（本地快照）；legacy 记忆组合同模。
- 组合根按 state.db epoch 门控：<35 legacy 保持裸路径；≥35 且 <45 stable fail；≥45 注册三
  authority + v7 runtime（缺件 startup fail，无 Noop 降级）。

## S5b Task 1 workspace EffectGate 最小闭环（2026-09-02 生产事实）

- PROJECT_EFFECT 清单（`sdk_adapters/tool_authority.py` `PROJECT_EFFECT_TOOL_NAMES`，design-freeze §1）：
  write_file/file_write/edit_file/move_file/file_organize/run_shell/process_start/doc_create/doc_edit/
  excel_create/ppt_create/pdf_export/download_file/workspace_prepare → `(project_effect, required, required)`
  进 SDK `ExecutableToolRecord`；读取类保持 SDK 默认；`task_scope_update` 留 Task 3。
- 二分口径：每个物理 PROJECT_EFFECT 只有两种结局。① **rejected**（`sdk_adapters/effect_gate.py`
  `EffectGate.verify`，在 `ProductEffectExecutor.execute` 的 `assert_workspace_current` 之前）：返回
  `ToolResult.rejected(稳定码)`、`effect=None`，不落 `execution_effects`、不产 host 事件，模型可见并可再路由；
  检查顺序 = design-freeze §4 的 1→3→4→5→6（envelope 缺失/身份回声 → 冻结 admission authority：scope /
  projectless / 冻结写根 → `verify_task_execution_envelope` 对 v45 durable route receipt，S4 码集原样透传 →
  `workspace_binding_receipt_superseded`（strict head==receipt，Manual/Auto 同规则）→
  `effect_gate_task_scope_not_active`）；§4 第 2 步 sticky memo 与第 7 步 confirm-only 留 Task 6。
  ② **整 Run 故障**（不是 rejected）：`sdk_task_execution_route_authority_missing`（standalone 路由下
  PROJECT_EFFECT）、`sdk_task_execution_root_authority_ambiguous|missing`（`BindingRootResolver` 从 route
  receipt 的 exact binding-set receipt 解析恰一 root）、`catalog_execution_policy_unavailable`（hidden 工具）
  → 异常逃出冻结 SDK ReActLoop → SDK `run.failed`（公开码只有 `driver_failed`）→ Host `SqliteSdkTerminalObserver`
  记 durable FAILED，并把 `RunFaultMemo`（`sdk_adapters/run_faults.py`，进程内、首码优先）里的稳定码写进
  `run_terminal` ExecutionEvidence `public_payload.error_code`；不新增 host.* 事件种类。
- 降概率：route ≠ ROUTED_TASK 的 provider turn，`ProductRunContextAuthority.prepare_snapshot` 不向模型暴露
  PROJECT_EFFECT 工具（只裁本轮 spec 列表；catalog fingerprint/可执行 exposure 不变）。
- 冻结 authority 口径：foreground 单 root 冻结为 `workspace_resolution.kind=legacy` + exact effective_root
  （Session-only project_bound validator 被有意绕过），故门的 project-bound 判据 = 存在 exact 冻结写根；
  projectless/missing/无根 → `effect_gate_projectless_project_effect`。reason code 全表见 Memory 仓
  `increments/2026-09-02-s5b-effect-closure-memory/design-freeze.md` §4。
- 组合：`main.py` 在 stack build 时构造 `EffectGate`（binding store + v45 ledger + canonical scope store +
  registry.resolve/resolve_exposure）注入 `ProductEffectExecutor`，`sdk_task_execution_authority` 带
  `BindingRootResolver` + 故障备忘，registry/terminal observer 共用同一备忘；`sdk_effect_gate` 进缺槽断言。
- 已知边界：`execution_effects` 行 + `host.file` 同事务由 Task 2 覆盖（oracle 已留 strict xfail）；legacy
  （<v35）epoch 无 route 能力，清单工具在该 epoch 下稳定 fail-closed（整 Run 故障）而非静默执行；
  备忘为进程内，crash 后终态证据退回 SDK 公开码。

## S5b Task 2 客观事件同事务直写、Harness 证据预留/排空与脏标记（2026-09-02 生产事实）

- **v46 表**（`memory/migrations/038_effect_closure_memory_v46.sql`，design-freeze §5 全部 7 张一次建齐，后续 Task 只写）：
  `task_scope_closure_receipts`（UNIQUE(sdk_run_id, closure_watermark, outcome)，outcome ∈ mutate|no_mutation|pending）、
  `harness_evidence_reservations`（UNIQUE(run_id, source_sequence)、source_event_id UNIQUE、status reserved→ingested|abandoned）、
  `memory_ingestion_outbox`(+`_evidence_links`)、`post_turn_invocation_attempts`(+`_members`)、`effect_gate_rejections`、
  `host_pre_admission_audit`；全部 append-only 触发器 + 状态单调守卫；`effect_closure_marker` + 恢复表注册（taxonomy A）+
  迁移链；旧 runtime（target 45）打开 v46 → `human_memory_future_epoch_unsupported` 稳定拒绝。037（v45）按 S5a 现状不在
  S4 迁移链/恢复注册内（已知遗留），038 重新入链。
- **预留/排空契约**（`execution/evidence_ingress.py` 单一 owner，§3）：其它 DB 事实（SDK effect、Provider invocation）在
  物理动作前 `reserve` 拿 seq（`effect:{effect_id}` / `provider:{request_id}`），完成后 `commit_fact`；state.db 事实
  （snapshot receipt / route decision / route tool invocation）在 `ContextRouteLedgerStore` 各自写事务内 `ingest_ledger_fact_tx`
  （reserve+ingest 同 commit，`snapshot:{snapshot_id}` / `route:{decision_id}` / `effect:{effect_id}`）；
  `next_sequence = MAX(reservations ∪ receipts)+1`（probe A9 的迟到行不再被 terminal 永久拒绝）；
  `SqliteSdkTerminalObserver` 是唯一排空者：写 `run_terminal` 前对每条 reserved 行经 `ProductSdkRuntimeStack.read_reserved_fact`
  读 SDK effect/provider 账本——终态记录 → 同一 `commit_fact` 路径导入（含客观事件），否则同 kind tombstone
  `{"status":"abandoned","reservation_id":…}` 并置 abandoned；`authorize_terminal` 只在无 reserved 行且 durable==terminal 时放行；
  crash 后新 owner 重放同一排空（source_event_id 幂等；fault lane `objective-event-commit` / `terminal-watermark` /
  `observer-next-sequence-race` 三 seam 为 kill→replay 用例）。无 foreground 绑定的 Run（无 admission scope）不产 Harness 证据。
- **客观事件映射**（`sdk_adapters/effect_gate.py` `OBJECTIVE_EVENT_MAP`，§2，确定性、禁关键词/正则）：14 个 PROJECT_EFFECT
  工具中写/编辑/移动/整理/文档/下载/`workspace_prepare` → `host.file`；`run_shell`/`process_start` 命令首 token 序列 ∈
  `TEST_RUNNER_COMMANDS`（pytest、python -m pytest、npm test、pnpm test、cargo test、go test、vitest、jest）且结果带退出码 →
  `host.test`，否则 `host.file`；成功/失败都记；载荷只含 tool/effect/call id、outcome、error_code、targets（路径）或
  command_head+exit_code（不含文件内容、命令全文、输出）。`ProductEffectExecutor` 在 SDK settle 后由 `commit_fact` 把
  `host.file|host.test` 事件 + `human_memory_evidence` 行（`host-typed-ingress/v1`，refs 可用）+ `harness.tool_invocation`
  导入写进**同一 state.db 事务**——SDK `execution_effects` 与 state.db 是两个库，不可能同一 SQLite 事务，二者经预留 seq 幂等关联；
  crash 于两者之间由 terminal 排空重放收敛。非 PROJECT_EFFECT 工具只记 `harness.tool_invocation`（trivial）。
- **脏标记**（`execution/semantic_closure.py` `dirty_state`）：自该 scope 最后一条 outcome∈{mutate,no_mutation} 的 closure
  receipt 的 `closure_watermark` 起，`event_sequence` 更大的 material 事件集合（`host.file`/`host.test`、PROJECT_EFFECT 的
  `harness.tool_invocation`）；无 receipt 自 0；pending 不清脏。终态门、`task_scope_update` 与兜底留 Task 3。
- **EffectGate 步骤 0**（Task 1 审查 F-1）：`ProductEffectExecutor.execute` 先查 `uow.read_effect(effect_id)`，已有 durable
  记录（任一状态）则跳过 gate 直接交 SDK replay/reconcile；仅首次出现才重验。

## ToolReceipt HMAC key authority（2026-09-01）

- `backend/deskpet/tools/receipt_store.py` 仍对 ToolReceipt 做 HMAC-SHA256 签名/验签，但 receipt key 只存在
  应用私有 `userdata/secrets/receipt_hmac.key`。首次以 exclusive create 生成，POSIX 强制 0600，并发 loser
  重读 winner；文件不可读、长度不符或创建失败时 fail closed。
- receipt 路径不再 import/call Python `keyring`，也不再使用或探测 `deskpet.receipt_hmac` OS credential
  service。已有钥匙串条目保持原样且永不被该路径访问；Provider/API 登录凭据仍有独立 credential owner。

## Human Memory Program S4 Task 5–8 Host Runtime Execution Closure（2026-09-01）

本节记录 S4 Host TaskScope + Runtime Execution Closure 增量（Task 5–8 + 用户 A2 批准的最小 S5
execution composition）在整改后的当前生产事实。S5 剩余 RecallPlan/Memory recall/动态 Context/semantic
closure 与 S6 UI 仍未实施，不作产品成功声明。

- v39–v44 schema 链已落地：六 bounded 阅读视图与 checkpoint verifier（`task_scope/projections.py`）、
  permission-first FTS locator 与 exact open（`task_scope/search.py`）、单 foreground Run/durable
  FIFO/control/lease（`execution/foreground_queue.py`）、recovery fence/drain/WAL/emergency export
  （`execution/recovery_fence.py`）、immutable execution preparation/start/observation/reconciliation
  audit（v44）。`backend/main.py` 的 fresh HUMAN lane 默认注册真实 binding/recovery/scheduler/runtime
  authority；legacy/future lane 保持 `None` 且旧 Session CRUD 对 primary 稳定返回
  `human_memory_primary_authority_immutable`。
- production foreground execution authority（`execution/foreground_runtime.py`）：claimed-turn exact
  read → inert draft → atomic claim → 冻结 Context/Provider/Tool authority（全部携带并断言 exact
  `(host_run_id, sdk_run_id, owner_id, generation)` 四元组）→ deterministic `sdk_run_id` → 唯一
  `SdkRuntimeIngress.start`（v3 `ContextRouteReceipt(origin=host_initial)` / StartSnapshot v7 /
  ReAct checkpoint v6）→ RUNNING → SDK 认证 terminal → settle → next。重启 reconciliation 按 durable
  binding/SDK state 补观察或以同一 identity 重试，永不产生第二个 SDK Run。
- generation fence（2026-09-01 P1 整改闭合）：`ForegroundQueueStore.authorize_effect` 按
  `EffectBoundary`（`SDK_START`=CLAIMED、`SDK_CONTROL`=*_REQUESTED、`TOOL`=全部非终态活动态
  ——RUNNING/PAUSE_REQUESTED/PAUSED/STOP_REQUESTED/CANCEL_REQUESTED；2026-09-01 review 修复：
  TOOL fence 只拦 stale worker 与终态，控制过渡期的工具调用不再被打成 FAILED）在每个外部副作用
  紧前做最终 current-generation admission；物理 Tool dispatch 经 `ForegroundEffectAdmissionGate` 在
  `ProductEffectExecutor.execute` 内接入同一 durable admission（main.py 以单例 gate 同时接 executor 与
  runtime）。lease reclaim 后旧 worker 的 start/signal/tool 副作用计数为 0（bind→start、signal-read→send、
  tool-admission→dispatch 三类 reclaim race 有专项测试）。`ClaimedExecution` 的 admission receipt 字段
  必填，initial route 的 host authority ref/hash 只来自 immutable admission receipt。
- live control delivery（2026-09-01 P1 整改闭合）：durable control commit 后
  `HumanMemoryHostService.control_current_run` 即时 `after_control` 唤醒 active Runtime；控制泵与
  terminal 观察并发运行（事件驱动 + ≤1s poll 兜底）。pause 送达并 ACK 后推进 PAUSED；STOP 与 CANCEL
  保持不同信号身份（`sdk-stop:`/`sdk-cancel:` ack）与不同 Host 终态（`resolve_host_terminal`：SDK
  cancelled + durable STOP_REQUESTED → STOPPED，否则 CANCELLED；completed/failed 恒随 SDK 证据，Host
  不伪造取消终态）。audit sink 固定同步。
- 验证事实（候选 `56d99a21`，gate run `r2-p1-closure`，本机 fresh 证据）：独立 code-audit round-3
  PASS（2 个 P1 resolved；6 个 P2 已登记为已知边界，见下）；100k archive cold-resume value smoke、
  100k execution value runner、9/9 boundary fault runner、22-case critical/affected API smoke（含 live
  pause control 与 Manual 两阶段 binding）、full-surface route smoke 全 PASS；聚焦套件
  12/36/25/25/36 passed；Host full pytest `6218 passed / 6 failed`，6 个失败全部为既有项（4 个
  baseline known-red + 2 个本机环境失败并在未修改 main 上复现），required 转绿项
  `test_real_product_sdk_production_composition_starts` 已绿；changed-surface ruff/mypy 相对 main 零新增。
- 已知边界（P2，未阻塞本增量）：foreground composition 与旧 chat ingress 双构造路径尚未收敛；
  effect gate 为进程内注册表（未注册 run 放行，重启窗口由启动唤醒缓解）；控制泵异常降级为 audit 行；
  PAUSED 无生产 resume 控制（2026-09-01 review 修复后暂停期 tool dispatch 已正常放行，仅 resume
  缺失）；CLAIMED 期 control 会阻住 SDK_START
  且无终态路径（既有 liveness 缺口，fence 使其无外部副作用）。多 root project effect 仍稳定 fail closed。

## Human Memory Program S4 Task 1–4 当前边界（2026-08-30）

本节记录 Human Memory Host 当前隔离实现事实；仍未接入的后续 composition/UI 不作完成声明。

- fresh-only `human-memory-v1` state.db 已推进至 v38：v35 永久 evidence/唯一 primary conversation、v36
  Canonical TaskScope Archive、v37 recoverable task_home provisioning、v38 append-only multi-root binding。
  普通生产 Session/UI 尚未切换到该路径，不能把 schema 就绪当作 foreground composition 完成。
- `WorkspaceBindingAuthorityStore` 只接受 SDK 0.7 strict DTO。Manual challenge/decision 必须先由显式 Host
  verifier 命中 exact durable user evidence 与 authenticated interaction，再形成 durable decision；Auto snapshot
  由 Host port 签发并绑定 Run/subject/context/configuration/configured-root identity。Auto authorize、首次 append
  transaction 前和 commit 前均重载 source snapshot 并重验 active Run、exact context/config revision、时窗和
  configured-root filesystem identity；对应 verifier 缺失时 fail-closed。公开 DTO/hash 自洽、provision receipt、
  proposed root、generic metadata 或模型声明 Auto 都不是 authority。
- 每次 append 由 grant、exact parent、sorted unique root identity set 和 CAS base revision 构成 immutable
  `WorkspaceBindingSetReceipt`；相同 root 可被不同 TaskScope 引用，同一 set 只能 append。route schema v2 与
  `TaskExecutionEnvelope` 交叉绑定 exact receipt id/hash/revision，effect 执行前重验 frozen membership 和当前
  filesystem identity；后续 append 不扩大当前 Run。POSIX 使用 no-follow fd，Windows 当前 fail-closed。
- ~~S4 Task 5–8 当前逐项未实现~~（2026-09-01 已被上方"S4 Task 5–8 Host Runtime Execution Closure"
  节取代：projections/search/foreground_queue/recovery_fence 与 main.py fresh 接线均已落地并验证）。
  S5 Task 8 仍是后续主模型 route/recall/context/tool production composition，不是 S4 Task 8。
- Host 启动 Memory SDK 时启用 fact worker 但未注入 LLM extractor，因此 SDK 使用默认正则 extractor。
- 首轮与 continuation 的 Context 都从同 Session 历史按 Token 预算截断；没有固定最近 10 个完整因果组、
  五天 short-horizon index、TaskScope current state 与按需 typed long-term recall 的分区组装。
- `memory_recall` / `memory_search` 被排除在 model-visible SDK catalog，automatic recall 在首个 Provider
  attempt 前完成；`memory_write` / `memory_read` / `memory_forget` 仍作为显式 Fact 管理工具可见。因此当前
  主模型可以按 exact Fact 做显式管理，但不能在同一 ReAct Run 内输出版本化 route/RecallPlan 并执行 typed recall。
- `messages` 和会话目录仍有物理删除 API/SQL；当前 Context snapshot 已能冻结并哈希实际 Provider 输入，
  但尚未形成 program 要求的永久 raw evidence、LLM invocation/decision、TaskScope event/checkpoint 统一审计链。
- 数字孪生体当前没有独立知识图谱 UI。V1 目标必须保持 display-only，任何图谱派生数据都不得进入 Provider
  Context、RecallPlan、Tool 选择或 effect。

## Project-scoped managed Skill 安装（2026-08-29）

- 聊天 `skill_install` 与 Settings 安装入口只做 Host adapter；唯一 application owner 是
  `ProjectSkillInstallService`。它持有 durable install intent、冻结 GitHub exact commit、排序成员集、
  Project identity、确认 nonce/version、Manager receipt 和 runtime verification refs。模型参数、前端
  `approved: bool`、legacy 目录复制与通用 shell 都不能成为发布 authority。
- SDK prepared authorization 在物理 handler 前完成 bounded source preflight。确认卡绑定冻结摘要；SDK
  decision、Product authorization saga 与 Host handoff 全部持久化后，resolver 才签发 typed receipt。恢复时
  嵌套 tuple/immutable metadata 必须 thaw 为 JSON list/dict；nonce reissue 和 durable decision lookup 复用同一
  request identity，不能因进程重启产生 `Decision was not found` 或第二次发布。
- Capability Manager 批量发布并绑定 `owner_key=sdk-runtime` 与 versioned Project scope。Hub 的 snapshot、
  cache key、Store query 和 per-Run lease 全程携带同一 owner；owner 不是只用于互斥锁。目录中缺少
  `execution_build_identity` 的 legacy ToolRegistry entry 不进入 SDK catalog。
- Manager committed 不等于可对用户宣称成功。service 启动或查询时恢复
  `published_pending_runtime_verification`，通过 canonical `skill.install.verify` Run 获取新的 Project-scoped
  catalog lease，按 manifest/content hash page-in 全部成员；attestation 与 release receipt 持久化后 intent 才
  CAS 到 `succeeded`。失败或未知 attempt 会先被 supersede/release，再以同一 Manager receipt 幂等续验，
  不重新授权、下载或发布。
- Capability Center 的 `capability_list` 必须携带当前 `session_id`，后端通过
  `SessionProjectBinding` 重新取得可信 Project identity，再用 `scope + owner_key` 查询；无 workspace 时
  fail closed。这样 Settings 显示的 Project Skill 与新 Run 实际冻结的目录是同一个事实源。
- slash command 的 help/schema/dispatch 三个入口同样先解析可信 `SessionProjectBinding`，再由
  `ProjectSkillDiscoveryService` 从 Capability Hub/Store 的有效 `sdk-runtime` Project binding 构造只读
  immutable projection；它不扫描安装目录，也不创建第二份 catalog authority。InputBar 每次打开 slash
  autocomplete 都带当前 `session_id` 重拉，因此安装完成后无需重启页面，也不会沿用模块级旧缓存。
- 当前 macOS 隔离 debug App 已真实完成 `DennyWanye/plan-test-skill` exact commit
  `4d8c803ba03b1a60d62dfd7133c173265dfbbf1f` 的三成员安装。intent 为 `succeeded`，第 4 次验证 attempt
  `attested`；Capability Center 显示 `plan-bs`、`plan-task`、`plan-test` 均为 Project scope、健康、同一版本
  `0.0.0+git.4d8c803ba03b`；当前 Project 会话输入 `/plan-` 的真 UI 下拉同时显示三项，projectless API
  对照为零项。本证据只关闭本次真实故障链；完整恶意 fixture、跨 Project 与 full-surface
  验收仍按 plan 独立执行。

## SDK-first Tool / Capability 目录（2026-08-30 校准）

- 2026-08-30 历史 production 快照中，Host vendor Service `0.3.12`（wheel SHA-256 `710ae66b…`）、Harness `0.6.4` candidate
  （`ecb6e85c…`）与 Memory `0.5.2`（`deff2fa8…`）。Service manifest 记录的构建时 Harness 是
  `0.6.2`；消费端按 Service `>=0.4,<0.7` 约束独立准入 0.6.4，且不将其冒充为官方三 SDK
  release unit。当前 Human Memory candidate 依赖已固定 Harness `0.7.0`（见 `backend/pyproject.toml`）；这个
  0.6.4 组合只保留为历史验收证据，不再表示当前 candidate pin。SDK 公共 `RuntimeToolCatalog` 统一表达 executable
  Tool、Skill resource 与 Workflow
  profile；Host 只提供 source metadata、权限事实和 physical handler。
- fresh Run 使用 `explicit-deferred-v1`：固定的小型 direct kernel 包含
  `tool_search/tool_describe/tool_activate` 与必要控制工具，其余 eligible built-in、健康 MCP 均可搜索而不在
  首个 Provider payload 中。search 只返回 bounded descriptor；describe nonce 绑定 Run、catalog
  fingerprint、exposure revision、capability/projection hash。
- `tool_activate` 只返回 typed `runtime_tool_activation_receipt/v1`。SDK 在 Effect terminal 结算后更新
  `CatalogRunToolExposure`，下一次 ready Provider attempt 重新投影 direct+activated；已 reserve 的 Provider
  request 继续从 durable snapshot 精确重放。v6 catalog envelope 与 ReAct checkpoint 持久化完整目录身份和
  activated IDs，恢复时不借用当前进程的新目录。
- 可见性不是授权。目标 Tool 仍经过 SDK `EffectExecutor`、Host prepared authorization/HITL、TaskGrant、
  workspace/origin scope 与 legacy physical dispatch。动态 physical admission 读取同一个 Run exposure，并
  精确核对 Run/session/request/scope；MCP 另核对原始 schema、incarnation、fixture 与 handler 组成的
  execution identity。任何缺失、漂移或跨 Run 调用均 fail closed。
- MCP manager 先 handshake/list_tools，再以单次 registry CAS 发布整服 catalog，最后才标记 running；reconnect
  推进 incarnation。旧 Run 不会调用 replacement session，同 schema/different handler、changed schema、
  removed tool 与部分注册失败均由回归覆盖。
- Skill/Workflow 进入同一目录但不会伪装成 executable Tool。普通 Turn 只得到 bounded metadata；Skill 正文
  由 exact locator/content hash 按需读取并按 untrusted data 进入后续 Context，公开 receipt/log 不保存正文。
- 自动化当前证据：Harness full `1463 passed, 2 skipped`，Memory full `218 passed, 7 skipped`，Host affected
  `291 passed`，Host 全分片 `15 passed + 2 implementation-before known failures`；Vitest、TypeScript、frontend
  build、Rust test/check 均绿。macOS 真 UI CAP-1 已完成渐进发现、两次同 Run 激活、真实 filesystem
  search/read 和基于 README 正文的最终回答；CAP-1 可标为通过。CAP-2 也已由独立真实 UI Run 完成
  Playwright 搜索/描述/激活、精确 localhost origin admission、真实导航与 `page.title()`；Host 只把经校验的
  loopback `local_page_url` 作为受信任项目事实注入，外部域名/凭据/query/fragment 均拒绝。CAP-3 也已由
  独立真实 UI Run 完成 Skill 搜索、exact locator 调用、冻结正文单次加载与 Markdown H1/H2/H3/列表翻译；
  SDK Run authority 的完整 `ToolExecutionContext` 是 Skill snapshot identity 的唯一执行来源。
  CAP-4 真实公网负例也已在 Playwright 客户端以 `ERR_BLOCKED_BY_CLIENT` 安全失败，页面和表单均未产生
  外部副作用。CAP-1～CAP-4 可标为通过；CAP-5 仍未关闭，不得发布。

### 全局 Skill URL 安装与普通 Session 工作区（2026-08-29）

- 普通 Session 的工作目录是创建时冻结的 authority：未选择时由 Tauri Host 解析 macOS Documents，并在
  `SimpleHarnessProjects/Session-<id>` 下分配唯一目录；选择目录时使用显式路径。v34 saga 先准备目录再提交
  Session binding，失败补偿只删除 exact marker、identity 和空叶目录都匹配的自动目录，用户内容永不误删。
- URL 安装只写 Manager-owned immutable Capability Pack。应用服务把 GitHub URL 解析为固定 commit、规范化
  raw Skill pack、member-set digest 与权限摘要；Settings 确认由已认证 control connection 签发 typed receipt，
  renderer 不能提交 principal/scope 作为 authority。
- 用户全局 owner 为 `user:v2:<identity-namespace-hash>`。Manager 在同一 batch publish 后执行 fresh Run
  page-in 验证，再以 durable activation intent 原子切换 user binding；崩溃恢复会补齐 registry/binding 边界。
  pending 或 verification 失败版本不会进入 catalog，旧有效 catalog 保持不变。
- `CapabilityHub` 对 `user:v2:*` scope 使用该 global owner 读取 bindings；因此所有既有/新建 Session 与冷重启
  后的 fresh Run 共享 Skill/Tool 可发现集合。可发现不等于授权，真实调用仍经过 Host prepared authorization、
  TaskGrant、workspace/origin scope 和 Tool health admission。
- 前台 SDK Run 在 `CapabilityPlatform.publish_lock` 内取得 exact user-global Hub snapshot，解析 immutable
  pack version 后，把 body-free `SkillResourceRecord` 冻结进该 Run catalog。所有 Skill record 由同一个
  `product-skill-catalog` namespace authority 发布；具体 user/pack owner、version、manifest/content/scope
  hash 保留在 per-record metadata，避免跨 owner 的 namespace collision，同时不改变执行授权。
- `tool_search` 找到 locator 后，`skill_invoke` 只从该 Run catalog 与 exact snapshot resolver 读取正文；不存在
  SDK Run 时才允许 legacy resolver 回退。2026-08-29 真实 `deepseek-v4-flash` 在重启后的既有 Session
  `4b2f9fd2…` 和新建默认 Session `2290a54a…` 中均完成 search/invoke，日志记录相同正文 SHA-256
  `32ac9804…`，且新 Session 绑定 `Documents/SimpleHarnessProjects/Session-2290a54a`。
- slash `/api/commands/help`、`/api/skills/list`、schema 查询与 WebSocket dispatch 不再读取 legacy
  first-party projection；它们在 publish lock 内复用当前 user-global Hub snapshot，并与 first-party catalog
  做 collision-checked union。前端每次开始新的 `/` 输入强制刷新候选，slash frame 携带同一客户端
  `request_id/turn_id`。因此安装、冷重启或切换到显式目录 Session 后，菜单和实际 Run 使用同一冻结 Skill。
- `SessionCreationService` 不在应用装配时捕获可能尚未 ready 的 owner；创建事务发生时才调用当前
  `IdentityReadyGate.freeze()` 并持久化 `companion_session_owners`。为兼容该缺陷产生的历史空 Session，
  ingress 只可用 `bind_session_owner_if_absent` 幂等认领空/同 owner 数据，已有消息、墓碑或不同 owner 继续拒绝。
  当前 macOS `.app` 在用户选择目录 `/Users/denny/projects/生成视频` 新建 Session `21030143…` 后，owner 行已在
  首次发送前存在；真实 `/plan-test` Run `ea0488e47…` 捕获 3 个全局 Skill、加载 `plan-test` 正文并以
  `deepseek-v4-flash` 完成。
- 默认权限状态为 `auto`，factory provenance 与 legacy migration 可区分；用户后续显式修改仍持久保留。
- GitHub source 对完整 40 位 commit SHA 走 immutable codeload URL，避开 GitHub REST commit lookup 的匿名
  rate limit；非 SHA ref 继续先经 REST 固定 commit，不能用 branch/HEAD 的可变 zipball 冒充固定来源。
- 安装失败以稳定 source/ref/error identity 写入 durable failure receipt；Agent 自动重放会返回同一失败而不再
  反复下载，只有用户显式 retry 才 CAS claim 下一 attempt generation，并以 settlement receipt 记录成功或失败。
  前端 Run status query/cancel 使用 session/run/version fencing；查询 ack 暂时缺失只标记展示错误，不把仍在
  执行的 Run 伪装成 idle，终态由权威版本帧清除。
- Capability Center 默认 scope 现显式携带同一个 `user:v2:*` global owner，因此安装成功后统一能力中心、旧
  Skill Store、slash catalog 和新 Run catalog 四个入口都看到同一三项 managed Skill；不是由 legacy
  `<userdata>/skills` 目录或前端缓存拼出。当前 macOS 最新源码 bundle 中能力总数由 127 增至 130，搜索
  `plan-` 显示 `plan-bs/plan-task/plan-test` 且均为健康；第二个普通 Session 的 `/plan-` 同时显示三项。

### Skill URL 安装切换前历史断层（2026-08-27，已关闭）

- 设置页的 `skill_install_from_url` WebSocket 分支仍由 legacy marketplace
  `SkillInstaller` 处理，stage/finalize 的目标是 `<userdata>/skills`；多 Skill 仓库还会在
  stage 后直接 `finalize_batch`，没有共享的用户确认门。代码锚点：
  `backend/main.py:586-610,13284-13382`。
- 生产 `_SkillLoader` 显式以 `skill_dirs=[]` / `skill_scopes=[]` / `enable_watch=False`
  启动；其注释也限定该 Loader 为 migration/test reader，不做 publish/bind/write。
  Manager-owned immutable pack 仍是生产 Skill 正文与 Run scope 的唯一 authority。因此对旧目录
  `reload()` 不能构成“已安装可用”证据。代码锚点：
  `backend/main.py:2572-2593`、`backend/deskpet/skills/loader.py:4-25`、
  `backend/deskpet/companion/skills.py:229-234`。
- 普通聊天 Run 的 SDK runtime catalog 当前没有与设置页共享的 typed Skill URL
  installer，所以模型在接到“安装到当前项目”时可以搜到 shell，却不能搜到正式
  installer。底层并非没有 typed lifecycle：`capability_install` 已支持 `source_type=git`、
  Manager 原子 publish/bind 和 operation receipt，也已注册进 process-wide ToolRegistry；但
  SDK `PRODUCT_TOOL_NAMES` 只投影 `capability_build/capability_repair`，没有投影
  `capability_install`。代码锚点：`backend/deskpet/capabilities/tools.py:372-445,652-704`、
  `backend/deskpet/capabilities/platform.py:2455-2472`、`backend/deskpet/sdk_adapters/tools.py:31-44`。
- 现有 `CapabilityScope.for_run(..., project_root=...)` 的 Project binding key 仅基于路径，
  尚未携带 Project Session 已冻结的 `project_id + project_revision + project_identity`。
  因此本次不只需要共享的编排服务和 adapter，还必须扩展 trusted execution context、
  Capability scope/binding key 及 Hub/Store 查询与兼容语义。代码锚点：
  `backend/deskpet/capabilities/tools.py:45-60,372-405`、
  `backend/deskpet/session/project_binding.py:300-340,378-435`。
- Git Capability source 已限制只写 staging，并有 subdirectory traversal、symlink 与 materialized
  package validation 护栏（`backend/deskpet/capabilities/source.py:4-8,39-107,225-237`）。
  但它只接收正式 Capability Pack；legacy `stage_recursive()` 接收 raw `SKILL.md` 树，
  而 `finalize_batch()` 是允许部分成功的 best-effort copy。原始 Skill repo 到 immutable
  Capability Pack 的 canonical conversion、单/多 Skill pack 粒度与 exact digest 还没有生产合约；
  `finalize_batch()` 不能被复用为 publish primitive。
- 当前系统也没有把 URL/revision/digest 绑定的 staged batch 发布到 exact
  Project identity，更没有用新 Run 对 manifest/content hash 做 resolve/page-in 证明。
  Capability Center 对 install/activate/repair 又显式返回 `model_driven_action_required`，而 legacy
  Skill Store 直接写目录；修复必须归并为唯一 application service ownership，再由
  chat 和 Settings 做薄 adapter，避免第三套入口。
  这是跨 UI/chat/Capability 权威的结构性缺口，不能通过为提示词增加
  `.claude/skills`、`.codex/skills` 或 shell copy 特例解决。

## SDK Context / Memory 官方一等集成（2026-08-22，依赖身份于 2026-08-25 校准）

- 当前 Host exact 依赖是 Harness 0.4.0（wheel SHA
  `aaf8d79a71b75bde0d71157a635b841eb557ea8889e2824571cacd7d8a58ecb6`）与 Memory 0.5.0
  （wheel SHA `c274fa6b2db538c29897f684b3f2f85775cb4b3a6870018e83792ff90b51ea46`）。生产只构造一个
  `MemoryManager`，Harness Runtime 以 `BORROWED` ownership 使用；
  shutdown 先关闭 runtime borrower，再由 SessionDB 有界 drain 并唯一 close manager。
- root/continuation 入口只提交正式 Conversation input 与各自独立、content-addressed 的 non-Memory Context
  source ref；SDK 保存 claim 后调用 read-only provider，并自动完成 Memory recall、frozen stage 与成功 Turn
  record。产品 manual recall/prepare 与 `ConversationMemoryAdapter` query/sink 已退休。
- actor 权威固定为 `LocalAuthSnapshotProvider -> validate_auth_snapshot -> identity_namespace_hash`；deployment
  来自 state DB instance，household/session binding 持久且不可换绑。模型、payload、Provider/API key 与
  legacy profile label 不能覆盖身份。
- foreground Harness 只走 execution committed-turn outbox；非 Harness producer 保留 product outbox。
  ordinary foreground catalog 移除 live `memory_recall/memory_search`，避免自动 recall 后二次查询。
- 显式 remember/read/forget 使用 resolver 生成的完整 `MemoryPrincipal` 与正式 fact API；write 保留
  salience/pinned/tier 并返回准确 fact ID；forget 使用显式 action `source_event_id`，持久 True/False receipt
  在重放与重启后稳定。重试/冲突/跨 principal/forget 不复活语义由 exact wheel 回归覆盖。
- 自动化已完成：Harness full `1379 passed, 2 skipped`、Memory 默认 full `200 passed, 7 skipped` / 正式
  candidate gate `205 passed, 2 skipped`、产品最终聚焦
  backend `83 passed` / MemoryPanel `18 passed` / TypeScript PASS；full baseline 15 PASS + 2 个实施前 known-red，
  0 unexpected。macOS Computer Use 真人 SH-M1～SH-M6 与 SH-SURFACE 全部通过。
- 完整边界与当前验收状态见 [`MEMORY_SDK_BOUNDARY.md`](MEMORY_SDK_BOUNDARY.md)。

## SDK Context / Memory 切换前基线（历史，已由上节取代）

- 当前 exact 依赖为 Harness `0.2.0` 与 Memory `0.3.0`。前台真实入口是 Tauri/React
  `chat/chat_v2 -> control_channel -> _launch_product_harness_chat -> _run_product_harness_chat`，随后
  由产品调用 `prepare_consumer_conversation_context()` 生成 projection-v2 private stage，再把 stage
  交给 SDK Runtime。`build_production_runtime()` 自身尚不会仅凭一个 Memory 实例自动完成 recall；
  Runtime 只校验、消费消费者已准备的冻结 stage。
- projection-v2 已包含完整产品 Context、当前结构化消息、Memory query/result lineage、附件、
  catalog/budget/tool authority。Memory 只以 USER/untrusted data 投影；Provider/tool retry 与 SDK
  recovery 复用冻结 stage，不二次 recall。
- 生产组合仍显式构造 Memory SDK `SQLiteMemoryBackend` 与公开 `ConversationMemoryAdapter`，然后把同一
  adapter 分别作为 `conversation_query` / `conversation_sink` 传给 Harness production config；
  `MemoryManager` 尚不能直接作为一等 Runtime port。
- 当前 Harness Memory outbox 的载荷单位是**单条消息**：root/continuation 的 user intent 在 start/enqueue
  时提交，assistant intent 只在 completed terminal 提交。因此 failed/cancelled Run 仍可能已经把 user
  intent 投递到长期 Memory；它不是“一次成功 committed user+assistant Turn”的原子记录。
- Harness execution outbox 与 `state.db.product_memory_outbox` 按 provenance 分治，并非同一前台消息双写：
  Harness 前台投影标记 `memory_authority=harness`，由 execution outbox 投递；非 Harness product
  conversation 才由 product outbox 投递。任何收敛不得粗暴删除 product outbox，否则会丢失
  Companion/background 等非 Harness producer。
- Memory 目前是前台 Runtime 的硬依赖；backend 缺失或 context recall 异常会阻止 Runtime/Run，而不是
  自动冻结空 Memory stage。前台 catalog 还同时暴露 `memory_recall` / `memory_search`，模型调用时会在
  自动 recall 之外再次查询真实 Memory。两点都是当前生产缺口。
- 当前持久身份只有 immutable `session_id -> user_id`；普通路径可回落到单一默认 Memory user。尚无
  deployment/household/actor/owner/personal/family 全链路，不能把现有 user/session 隔离描述成家庭隔离。
- UI 每条普通消息当前启动 fresh root Run；产品 retry handle 仍未接通。SDK staging/outbox 的恢复能力
  已存在，但 simple_harness 产品重启后的四个 crash window 仍需分别验证，不能用 SDK 单测代替。

下方大量 v0.1.x、切换前 Context 与历史 Harness 章节作为演进记录保留；凡与本节、
[`SDK_EXTRACTION.md`](SDK_EXTRACTION.md)、[`AGENT_HARNESS.md`](AGENT_HARNESS.md) 或
[`MEMORY_SDK_BOUNDARY.md`](MEMORY_SDK_BOUNDARY.md) 冲突，均以这些 2026-08-22 当前事实为准。

## Agent activity timeline boundary (2026-08-20)

The user-facing execution timeline is a read-only projection of the existing
canonical Run ledger. The production path is:

`HarnessPublicReadService.create_manifest -> semantic_projection.reduce_public_manifest -> PublicRunSnapshotV3 -> harnessPublicSnapshotStore -> HarnessInspectorPanel/DurableTaskSteps`.

The timeline may expose bounded, redacted activity items such as a public phase,
tool action, safe target label, status, duration, and safe input/result preview.
It must not create another Run owner, state machine, persistence authority, or
execution channel. Event identity and terminal status continue to come from the
canonical Root Run and the two-source read cut.

The context boundary is explicit: public timeline summaries, Inspector details,
technical diagnostics, event names/correlation, and hidden reasoning are
`context_visibility=exclude`. They are display/audit projections only and are
never appended to the current or later model messages. A tool result can still
be part of the normal model protocol for the execution turn that requested it;
that does not make the separately projected UI detail a context source. In the
2026-08-21 historical chain, `_assemble_sdk_messages` was the conversation input owner and the full
Context assembler had not yet entered that production chain; this is not the current owner description.

The timeline reduces by stable event identity and causal/source order. A
duplicate event is rendered once, an out-of-order event is placed according to
the durable projection order, an incomplete event degrades to an explicitly
incomplete item, and late events cannot revive a terminal Run. Public tool
projection remains default-deny, redacted, and byte-bounded; raw prepared,
outcome, provider input/output, credentials, and reasoning are not returned to
the frontend.
> **Last verified: 2026-08-21**（代码锚点 `e92883a5c52d406b30d4b4e212589fbe4fb44e13`）。
>
> **历史校准：2026-08-21、SDK v0.1.4；已被页首 2026-08-22/25 当前事实取代。** 当时状态：
>
> - ✅ SDK Runtime 初始化与前台文字执行链：`_activate_product_sdk_runtime()` 创建 `_sdk_ingress`；
>   `chat/chat_v2` 经 `_run_product_harness_chat()` → `_execute_sdk_run()`（当前定义约
>   `backend/main.py:9513`）→ `SdkRuntimeIngress.start()` → `DeliveryDispatcher`；assistant 回复经
>   `chat_v2_final` 回推。Voice 当前关闭；Companion/background 使用独立 SDK client/start 入口，
>   不经过 `_execute_sdk_run()`。
> - ✅ `_DeliverySink.deliver()` / `ProductDeliveryAdapter` 已接通（2026-08-17/19 两轮接线；host 侧缺口修复记录见 PROJECT_STATUS 2026-08-19 里程碑）。
> - ✅ 旧 harness 死代码已删除（2026-08-17，~62k 行）；保留 23 个 harness 契约/引擎文件（非 `__init__.py` 口径，清单见 §1.2）供 companion/run_adapter.py 依赖链与契约类型引用。
> - ⚠️ **已知残留（不阻塞 foreground 主链路）**：`companion/run_adapter.py` 期望
>   `KernelRunClient` 但收到 SDK `RunClient`（接口不兼容，companion 后台功能不可用）；
>   `memory_recall_host_registration_deferred` 只表示 legacy Host V2 registry 未注册，不影响 SDK
>   product 77-tool catalog 中已接通的 `memory_recall`；curation 未接新 memory SDK。
> - ✅ 记忆链路（2026-08-19）：`SessionDB` 双写 SDK `memory.db`，recall/facts/digital-twin 委托 `simple-harness-memory-sdk`（边界事实源：[`MEMORY_SDK_BOUNDARY.md`](MEMORY_SDK_BOUNDARY.md)）。
> - ⚠️ **Context cutover 未完成（2026-08-21 代码校准）**：前台文字链路虽然进入 SDK Runtime，
>   但 `_run_product_harness_chat` 构造的 `TurnInput` 没有被执行链消费；`_execute_sdk_run` 直接通过
>   `_assemble_sdk_messages` 构造“公开工作叙述 system prompt + 最多 20 条普通 conversation 历史 +
>   当前文本”。保留的 `ProductTurnPreparer` / `ProductTurnPreparationService` 和
>   `ProductContextAdapter` 对该入口不可达，故 Persona、召回 Memory、Skill 指令、附件、项目/任务
>   快照、Context OS budget/compaction 与会话 `model_params` 未进入真实首个 Provider request。
> - ⚠️ **Context 观测 authority 未切换**：Context Inspector 仍独立读取 legacy persona/facts/V2
>   registry/history，并用 `tool_count * 60` 估算；它不是 SDK frozen request 的视图。SDK
>   `provider_invocations.usage_json` 有真实 usage，但 Session context usage 与 BillingLedger 仍读
>   legacy `last_usage`/attempt store，因此 UI 可显示 `0/0` 或旧样本。
> - 🚨 **Context Inspector 公开投影未满足隐私边界**：legacy breakdown 将 persona、facts 与历史
>   preview 未经统一 public redactor 送到前端 `<pre>`；敏感 header/token/用户正文可能直接可见。
>   该预览在切换到有界脱敏 frozen snapshot 前不能作为安全审计面。
> - ⚠️ **Session/Run 隔离缺口**：SDK stack 当前按 provider/model 全局 refresh，前台 Run 通过全局锁
>   串行，后台入口不共享同一运行锁；Session `model_params` 没有进入 Provider adapter。RunStart
>   catalog generation 固定为 `1`，context-dependent tool handler 又收到静态
>   `sdk-session/sdk-request/sdk-scope`，无法证明 Provider、executor、Inspector 和 Session identity
>   使用同一冻结事实。
> - ⚠️ **Inspector 请求隔离缺口**：`context_breakdown_request` 直接接受客户端 `session_id` 并读取
>   对应 facts/history/project root，当前分支没有显式 owner/peer-group access fence；请求/响应也没有
>   request id、snapshot id 或 expected authority version。切 Session、关闭重开或连续刷新时，前端
>   只按 Session 接收，无法拒绝同 Session 的晚到旧响应。
>
> **2026-08-21 历史 Context/模型/工具 cutover 对照（非当前生产事实）**：
>
> | 关注点 | 当前生产 owner / 实际行为 | 已确认缺口 |
> |---|---|---|
> | Foreground messages | `_assemble_sdk_messages` | 只有 system + max-20 conversation + current；历史读取失败静默降级 |
> | Persona / Memory / Skill / project / attachment | 只存在于未消费的 `TurnInput` 或不可达 preparer | 没进入首个 SDK Provider request |
> | Tool schemas | 77 项 product manifest 同时注册给 SDK Provider/executor | Run payload 只留 names；generation 固定 `1`，Inspector 读另一个 V2 registry |
> | Tool execution identity | SDK `ToolContext` + product fallback | metadata 不完整；Session/scope/workspace 可回退到 request/default 或固定假 id |
> | Authorization / capability | unconditional ALLOW + 空 selectors；capability bridge 为 stub | 未达到历史 prepared grant/scope 的 fail-closed 语义 |
> | Session model params | UI/SessionDB 保存，SDK stack 只消费 provider/model | thinking/fast/effort/context window 丢失；全局 stack refresh 跨 Session |
> | Reasoning mode | official DeepSeek base URL 时硬编码 thinking enabled/high | 非 provider-neutral；DeepSeeker relay、Kimi 等不按 Session 配置 |
> | Usage / cost | SDK invocation ledger 保存 usage；SDK price resolver 返回零价 | Session usage/BillingLedger 读 legacy 状态；辅助 narration 调用未独立建账 |
> | Inspector | legacy 动态 probe + raw preview | 无 snapshot/request lineage、可乱序覆盖，并有敏感预览风险 |
>
> 三层工具事实必须分开：**Provider schema** 与 **SDK executor registration** 当前都是 product
> manifest 的 77 项；**实际 capability/authorization/session-aware execution** 仍有上述 stub、ALLOW
> 和 identity 缺口。不能用“Provider 看到了 77 个工具”推导所有工具语义已接通。
>
> 入口也必须分开：foreground text 走 §2；Voice disabled；Companion/background 走独立 SDK client
> start；control/recovery 只做 lifecycle 操作。它们不得再统称为全部经过 `_execute_sdk_run()`。
>
> **2026-08-21 当时的生产链路（历史）**：
> ```
> chat/chat_v2 foreground text
>   -> _run_product_harness_chat()
>   -> _execute_sdk_run()  (main.py:9513 附近)
>   -> SdkRuntimeIngress.start()  (deskpet/sdk_adapters/ingress.py)
>   -> simple_harness.runtime (SDK v0.1.4 Runtime, vendored wheel)
>   -> DeliveryDispatcher -> _DeliverySink -> WebSocket 前端
> ```
>
> **保留的产品代码**: `backend/deskpet/{tools,skills,sdk_adapters,capabilities,companion}` 等产品特定模块完整保留，通过 SDK adapters 桥接到 SDK Runtime。
> 
> ---
> 
> The SDK Runtime `simple_harness.runtime` is the foreground production execution owner. One local
> owner/inbox may contain multiple UUID conversation Sessions; each Session may contain multiple isolated
> top-level Runs. WebSocket transport id and peer-group identity are routing metadata, not Session or Run
> identity. There is no Code/normal mode split. Every ordinary top-level Run is fixed to `agent.general`;
> the model may choose optional child Profiles through `workflow_spawn`.

> Current observability fact: workflow-node `started_at`/`ended_at`/status/attempt are durable in `workflow_node_attempts`, node `duration_ms` is durable in `trace_spans`, and `deepresearch_stage_timing` is a diagnostic mirror. Backend stdlib and structlog now share one JSON-lines formatter and one resolved user log directory; the rotating text log is diagnostic evidence, not the durable workflow authority. V7 additionally logs privacy-safe child id/attempt/status/reason/source count/duration; page content and full prompts are excluded.

> Historical calibration map: 本段及 §1-§2 是 2026-08-21 历史快照；§3、§4.2/§4.3、§5-§10、
> §15-§18 是历史/目标设计记录，不得用作当前 foreground Context/authorization/model-binding 事实。
> DeepResearch detail lives in [`SEARCH_GATEWAY_DEEPRESEARCH.md`](SEARCH_GATEWAY_DEEPRESEARCH.md).
> §20 是 v0.1.1 时点的历史清理记录；这些版本标签均不代表当前安装版本。当前依赖以页首
> 2026-08-25 基线与 `backend/pyproject.toml` 为准。

## 1. System Shape

simple_harness is a local desktop product, not a generic agent framework. The application now has one
product preparation path, one thin control plane and two available execution algorithms. Driver
selection is not a text classifier.

### 1.1 2026-08-21 历史前台文字架构（SDK v0.1.4）

```text
Tauri shell + React UI
        | chat/chat_v2 WebSocket
Foreground text -> _run_product_harness_chat -> _execute_sdk_run
        | _sdk_ingress (SdkRuntimeIngress)
        | simple_harness.runtime.RuntimePorts (SDK Runtime)
        | host 当时的临时 payload assembler (_assemble_sdk_messages)
simple_harness.runtime.kernel.RunKernel -> fixed root agent.general
        | simple_harness.runtime.drivers.react_loop (SDK ReAct Driver)
        | model may call workflow_spawn(profile_key)
        | durable ProfileLaunchTicket -> child ReAct OR Workflow Driver
        | simple_harness.workflow.WorkflowDriver (SDK Workflow Engine)
ProductToolsAdapter -> 77 registered product schemas/handlers
        | historical ProductAuthorizationAdapter policy was unconditional ALLOW
        | capability search/describe/activate bridge is stubbed
        | simple_harness.execution.EffectExecutor
        | simple_harness.execution.UnitOfWork (SDK UoW on SDK execution DB)
ProductDeliveryAdapter -> SessionDB / WS / TTS / artifacts
```

**代码证据**：
- `backend/main.py:9513` 附近：`async def _execute_sdk_run(...)`（前台文字协调器）
- `backend/main.py:8227`：`await _execute_sdk_run(...)`（当前前台调用点）
- `backend/main.py` `_activate_product_sdk_runtime()`：`_sdk_ingress.open()` 激活 SDK ingress
- `backend/deskpet/sdk_adapters/ingress.py:1-6`: 注释声明 "sole ingress for all product entry points"
- `backend/main.py:6363`: `# Slice C: Use SDK Runtime instead of legacy harness`

### 1.2 旧 harness 遗留代码：已于 2026-08-17 清理完毕

以下旧 harness 模块**已删除**（2026-08-17 清理，~62k 行；pytest 81 failed / 6466 passed，
对比基线 79 failed / 6636 passed，减少项为已删 harness 单测，无新 ImportError）：
- `backend/deskpet/harness/bootstrap.py`
- `backend/deskpet/harness/drivers/react.py`、`react_loop.py`、`react_artifact_completion.py`、`react_recovery.py`、`workflow.py`（即 `drivers/react*.py` 4 个 + workflow.py，与删除提交 `71f3a6e2` 一致，共 11 个模块）
- `backend/deskpet/harness/adapters/product_composition.py`、`product_profiles.py`、`subagent_registry.py`、`team.py`、`legacy_execution_migration.py`
- `main.py` 中 `_build_product_harness_stack()`、`_activate_product_harness()` 与 `_harness_*` 全局变量（已内联 3 行 `root_run_identity` 替代导入）

**保留的 23 个 harness 文件**（非 `__init__.py` 口径，2026-08-19 磁盘核实）：
contracts.py、ports.py、projector.py、context.py、profiles.py、skill_scope.py、kernel.py +
13 个引擎模块（runtime.py、reconciler.py、admission_launch.py、live_index.py、kernel_terminal.py、
attempts.py、child_runs.py、child_signal_runtime.py、execution_profiles.py、router.py、
start_snapshot.py、tool_executor.py、user_continuations.py）+ adapters/venues.py、
adapters/product_turn_open.py、drivers/react_boundary.py。保留原因：`companion/run_adapter.py` →
`venues.py` → `kernel.py` 依赖链，以及 6 个产品文件直接引用 harness 契约类型
（HostExtensionRefV1、HostContext、PreparedRunContextV1 等）。

The SDK Runtime `simple_harness.runtime` is the production execution boundary. `backend/main.py` owns ingress/transport and composition, but not a second execution loop. The executable contract is exported from SDK's public API; the canonical lifecycle and recovery boundaries are defined in [`AGENT_HARNESS.md`](AGENT_HARNESS.md) and SDK documentation.

## 2. Foreground Text Request Lifecycle（2026-08-21 历史 SDK v0.1.4 快照）

**实现状态**: ✅ **已接通**（2026-08-17 ingress cutover 完成 + 2026-08-19 真机 E2E PASS：主聊天经 `_execute_sdk_run` 走通，assistant 回复经 `chat_v2_final` 回推前端）

当时的生产请求路径（历史架构）：

```text
Main Session chat/chat_v2
  -> _run_product_harness_chat()
  -> _execute_sdk_run() ✅ main.py:9513 附近（调用点 :8227）
  -> SdkRuntimeIngress.start() ✅ sdk_adapters/ingress.py
  -> host 当时的临时 payload assembler `_assemble_sdk_messages`
       (public narration system prompt + max 20 ordinary conversation rows + current text)
       ⚠️ ProductContextAdapter/ProductTurnPreparer exist but are not reachable here
  -> simple_harness.runtime.kernel.RunKernel (SDK, top-level profile fixed to agent.general)
       -> simple_harness.runtime.drivers.react_loop (SDK ReAct Driver) -> same parent model
            -> direct answer or ordinary tools via ProductToolsAdapter
            -> workflow_spawn(profile_key, catalog_generation)
                 -> durable one-shot ProfileLaunchTicket
                 -> ChildRun using ticket-bound simple_harness.workflow.WorkflowDriver (SDK)
  -> DeliveryDispatcher ✅
       -> _DeliverySink.deliver() ✅ desktop_runtime.py:141-189（真实路由实现）
       -> ProductDeliveryAdapter ✅ delivery.py（完整实现）
  -> SessionDB + WS + TTS + UI (via product delivery adapter) ✅
```

**代码证据**：
- ✅ `backend/main.py:9513` 附近：`_execute_sdk_run()` 前台文字协调器（调用点 :8227）
- ✅ `backend/main.py` `_activate_product_sdk_runtime()`：`_sdk_ingress.open()` 打开 ingress barrier
- ✅ `backend/deskpet/sdk_adapters/ingress.py`: `SdkRuntimeIngress` 提供 `.start()/.signal()/.cancel()/.query()/.wait_idle()` 方法
- ✅ `backend/deskpet/sdk_adapters/composition.py`: 构建 SDK Runtime 并注入产品 adapters
- ⚠️ `backend/deskpet/sdk_adapters/context.py`: `ProductContextAdapter` 存在，但前台文字生产入口未构造或调用它
- ⚠️ `backend/deskpet/sdk_adapters/tools.py`: 当时 `ProductToolsAdapter` 注册 77 项 schema/handler；
  capability bridge 仍是空 search/describe 与拒绝 activate，且 handler Context identity 不完整
- ✅ `backend/deskpet/sdk_adapters/desktop_runtime.py:141-189`: `_DeliverySink.deliver()` 真实路由实现
- ✅ `backend/deskpet/sdk_adapters/delivery.py`: `ProductDeliveryAdapter` 完整实现

**关键方法签名（SdkRuntimeIngress）**：
```python
async def start(session_id, request_id, turn_id, payload, session_generation) -> IngressStartReceipt
async def signal(run_id, signal_name, payload) -> IngressSignalReceipt
async def cancel(run_id) -> None
def query(run_id) -> RunState
async def wait_idle(run_id) -> None
```

`backend/main.py` owns WebSocket/audio adapters and service composition.
当前 host 临时 assembler 只冻结有限消息与 capability names；完整产品 Context 尚未通过
`ProductContextAdapter`/`ProductTurnPreparer` 进入前台文字 Run。SDK `RunKernel` owns identity, coarse lifecycle
and recovery admission. For a root it resolves the fixed `agent.general` Profile; for a child it
only dereferences a valid launch ticket. SDK ReAct Driver is the dynamic LLM/context/completion
engine. Tool execution is reached through `ProductToolsAdapter`; catalog registration 可达不代表所有
capability/authorization/session-context 语义已接通。`ProductDeliveryAdapter` owns the
projection to WebSocket, SessionDB and TTS.

当前 authorization/tool context 仍有明确缺口：composition 注入 unconditional `ALLOW`、空
resource selectors 与固定宽 grant；SDK ReAct `ToolContext` 不携带完整 metadata，产品 handler 会把
Session/workspace 回退到 request/default workspace，`context_page_in` 更使用固定
`sdk-session/sdk-request/sdk-scope`。历史 `PreparedToolSet + ToolEligibilityContext` fail-closed 设计
尚未迁到这条 SDK foreground 链路。

ReAct and Workflow deliberately remain different execution algorithms. Short
chat does not write every token to the workflow checkpoint store. The fixed
`agent.general` parent can keep an explanation or ordinary tool task in ReAct;
when the model calls `workflow_spawn`, DeepResearch, PPT, capability building or
another registered long-running Profile can retain compiled workflow state,
leases and exact node checkpoints. They share identity, Effects, ChildRun,
lifecycle and presentation through the Harness rather than becoming one
universal graph. User text, `task_type`, legacy Code persona and regex routing
cannot override the model/ticket decision.

Ingress transports still have transport-specific responsibilities, but no
second agent owner:

- The legacy `/ws/audio` Voice path remains disabled and returns
  `voice_temporarily_disabled`; startup does not load VAD/ASR/TTS. The candidate
  `/ws/realtime-voice` path uses the versioned Service SDK local protocol and
  provider-native Realtime transport, starts only after an explicit UI action,
  and does not create an Agent Run. Real-provider multi-turn E2E remains open.
  If voice later gains Tools, Workflows or product Context, that agent work must
  enter the same `ProductTurnPreparer`, `RunKernel` and Presenter as Text.
- Slash/admin/control commands may perform explicit control-plane actions in
  `main.py`; any new agent execution still starts through RunKernel.
- Direct Tauri shell commands that are not agent work remain outside the
  Harness by design.

The source tree still spans `backend/agent/*` and `backend/deskpet/*`, but the
execution ownership is no longer split: `AgentLoop` is a ReAct Driver engine,
while RunKernel/UoW/Presenter are the shared production authorities.

## 3. Historical Persistence Baseline And Current Timing Owners

This section's original inventory predates the native workflow runtime and is retained to explain the migration. The current workflow fact source is `<user_data>/data/workflow.db`, as calibrated in §13; `state.db` remains the conversation/memory/product projection store rather than the workflow execution owner.

### 3.1 Current DeepResearch timing ownership

| Layer | Current owner | Persisted fields / retention | Current gap |
|---|---|---|---|
| Workflow node attempt | `workflow.db.workflow_node_attempts` | `started_at`, `ended_at`, `status`, `retry_attempt`; terminal workflow data defaults to 30-day retention | `duration_ms` is derived rather than a separate attempt column |
| Workflow node span | `workflow.db.trace_spans` | durable `started_at`, `ended_at`, `duration_ms`, node/status/attributes | no fetch-transport child spans yet |
| Search provider attempt | `search_gateway_attempt` in rotating `metrics.jsonl`; response/state may retain request-local outcome | `run_id`, request/provider/status/duration/count under the metrics privacy whitelist | not a durable trace-level attempt ledger for long-term latency analysis |
| Fetch transport/extractor | `deepresearch_fetch_attempt_timing` in rotating `metrics.jsonl` | `run_id`, stage/status/duration/fetcher/extractor/error/count | best-effort only; throttle/URL-lock/total fetch are not fully separated; millisecond rounding can produce `0` for sub-ms work |
| Diagnostic export | `<user_data>/metrics.jsonl` | max 2 MB; rotation keeps roughly the latest 2000 rows; included in support diagnostics | support bundle does not export rich workflow trace rows, so metrics is not 30-day exact history |

Current v7 must preserve the metrics privacy wall while adding durable, correlated child timing where AC-OBS requires later latency analysis. Telemetry failure must remain non-fatal to workflow execution. The earlier v6 requirement is retained only as historical implementation context in its module plan.

### 3.2 Pre-durable inventory (historical context)

The primary local database is `state.db`, managed by `deskpet.memory.session_db.SessionDB` and `deskpet.memory.schema.initialize_state_db`.

| Data | Current store | Durability | Limitation for workflows |
|---|---|---|---|
| Sessions and messages | `sessions`, `messages`, FTS/optional vec tables | Durable | Records conversation, not executable node state. |
| Code todos | `code_todos` | Durable | Full-list replacement; no node attempt/result/checkpoint model. |
| Legacy Code-mode project mapping | `code_sessions` | Durable compatibility data | Retired Code-mode map keyed by `base_session_id`; its upsert can replace `project_root`, so it is not an immutable ordinary-Session project authority. |
| Awaiting code plan | `session_plans` | Durable record | The actual waiter is an in-memory `Future`; restart reloads the card but cannot resume the suspended coroutine. |
| Goals and goal tasks | `session_goals`, goal task tables | Durable | Completion tracking, not general workflow execution. |
| Team tasks/messages/permissions | Per-team SQLite files | Durable data | Worker coroutines and reclaim logic are process-local. |
| PPT outline history | `ppt_outline_history` in `state.db` | Durable record | Proposal status survives, but the running task and waiter are process-local. |
| Tool receipts | Receipt store | Durable evidence | Can prove side effects, but is not yet keyed to a workflow node attempt. |
| Artifacts | User data/output directories plus message envelopes | Durable files | No generic graph state references or replay policy. |
| Agent iteration trace | `<user_data>/traces/<task>.jsonl` | Append-only file | Flat per-run records, optional, no span tree/checkpoint link. |
| Anonymous metrics | `<user_data>/metrics.jsonl` | Rotating append-only file | Strict whitelist, intentionally too small for rich trace/replay. |
| Context decisions | In-memory `ContextAssembler` ring | Process-local | Context Trace UI loses history after restart. |

Additional stores include facts/workspace/skill memory and feedback tables in `state.db`, `billing.db`, supervisor hints, pending skill candidates, provider bindings, preference/runtime JSON files and permission-auto-mode state. Some are owned by formal migrations, while PPT outline and skill-candidate tables are still created lazily by their business modules. The workflow plan must inventory schema ownership and retention without attempting to merge unrelated product data into graph state.

`SessionDB` already provides WAL, busy timeout, an application write lock, retry with backoff, and idempotent migrations. It is the natural physical store for workflow metadata, but the graph core must depend on a storage protocol rather than directly on `SessionDB`.

### 3.3 当前 Session / Run / workspace authority（2026-08-25）

当前 schema v33 由 `projects`、immutable `session_project_bindings`、创建 receipt 与 catalog revision
组成 Project-scoped Session 的 durable authority。项目 Session 在创建事务中一次性
绑定 `project_id` 与 `execution_kind`；binding row 禁止更新或删除。零消息 Session 也从 catalog 查询返回，
不再依赖 `messages` 聚合才进入侧栏。无 binding row 的 Session 是显式 typed projectless，不会继承最近 Run、
旧 Code Session 或全局 companion workspace。

fresh root Run 只通过 `ProjectBindingService` 解析 `WorkspaceResolutionV1`。`project_root` 表示项目归属；
`execution_root` 表示物理执行边界，允许测试 fixture/未来 worktree 两者不同。解析结果冻结进
`TaskWorkContext`、Host workspace/write scope 和 SDK Run tool-authority v3 fingerprint；built-in 终端、
文件工具及 `ProjectRulesComponent` 都从同一 app-private Run authority 取根。project-bound Run 不投影
进程级固定根的动态 `mcp:filesystem`；浏览器与非文件 MCP 保持原有目录能力。目录缺失、identity
漂移或 projectless 本地开发请求在物理执行前 fail closed，不能回退其他路径来源。

Project relocation 只允许在无活跃 root Run 时，以 expected project revision CAS 将 Project 的
`canonical_root` 更新到 filesystem identity 相同的新目录；Session binding 本身保持不变，选择无关目录
会被拒绝。`code_sessions.project_root` 与 latest-Run workspace 不再 backfill 或兼容恢复为新 Session authority。
迁移在会话入口开放前完成；v33 的 `025_legacy_session_reset_v33.sql` 配合外部 store coordinator，一次性删除
升级前全部 Project、Session、消息、Run、上下文和会话派生数据，完成 phase ledger 后才开放 Memory、Workflow、
Companion 与 SDK ingress。全局 Provider/默认模型/应用设置/Keychain/账单/Skills/Plugins 和磁盘项目/产物文件
明确保留；这是产品逻辑清理，不承诺取证级安全擦除。当前 `TARGET_SCHEMA_VERSION=33`。
state.db 历史可选 `facts` 表按存在性清空；workflow/sdk product-state 的 Capability 清理限定于 Run catalog、
runtime 与 snapshot/lease 表；companion 采用精确会话派生表 allowlist，清除 Run/job、growth event、偏好证据、
消息决策回执、Memory scope 和 notification/outbox，同时保留 candidate package、capability governance、
growth authority、profiles 和 reminders，避免以“除少数表外全部删除”的反向清单误伤 Skills/Plugins。
workflow 的 `execution_runtime_state`、candidate draft receipt/material 属于全局 authority，同样保留；Run 表上的
immutable delete trigger 仅在清理事务内临时移除，随后按原 DDL 恢复，保证正常运行期的不变性约束继续有效。
当前 HEAD `8631ddcc` 的完整 v32 隔离启动证据确认：旧会话域为空、SDK Runtime/Provider 仍可用，真实
`deepseek-v4-flash` Run 完成，且完整重启后新建 Session 与消息保持。
补齐 companion 混合表后的实现 `9424da77` 再以真实 UI/Provider/完整重启确认同一边界：旧引用清零、
全局 capability/runtime authority 保留、升级后新 Session/消息/growth event/outbox 正常持久化。

验证边界：当前代码/自动化、100k catalog 性能探针和 macOS 当前 debug `.app` 的注册、分组、Inspector、
projectless 与重启恢复核心路径已通过。真实 `deepseek-v4-flash` Run 已实际调用终端、文件读取和文件写入：
绑定根内 `pwd`/读/写成功，`../unrelated` 读写均被拒绝且未产生越界文件。`tool_describe` 的模型可见契约
现在只有一个顶层 activation `schema_hash`；手动逐项授权与全自动两轮真实 Run 都完成 describe、activate 和
项目内写入，未改变 Session binding 或物理 workspace 校验。2026-08-26 TC-PS-04 进一步以五个真实 root
Run 证明 terminal cwd、Host/write scope、Project Rules 和物理文件根一致；前端、legacy、latest-Run 与模型
文本冲突路径全部无效，缺失目录在 Provider/Tool 零副作用时 fail closed，完整重启后 fresh Run 仍使用同一
binding。相关聚焦自动化 `75 passed`；缺失根错误 turn 的历史恢复显示另列为持久化观察项。TC-PS-06
又以 distinct filesystem identities 的 explicit binding 验证：Project 分组/Inspector 始终使用
`project_root`，三个真实 root Run 的 terminal/read/write 只使用 `execution_root`；Project-only canary
相对读取失败，完整重启后 split 不变，且无自动 worktree 副作用。聚焦 backend `109 passed`、frontend
`3 passed`。Windows 已按用户 2026-08-25 决定移出本轮
目标平台。2026-08-26 TC-PS-07 又在真实 macOS 当前构建验证：missing-root preflight 在 Provider/Tool effect
之前提交 durable failed SDK root；无关目录 relocation 被拒绝；活跃 Run 不切根并在重启 recovery 后以
`workspace_unavailable` 收敛；同身份 relocation 后 Project revision 原子更新、两个 Session 与历史跨重启
保持。2026-08-27 最终 gate 已补齐 S-PS-01～S-PS-08 的全部 macOS required lane；25 个 machine root Run、
当前 debug `.app` 和真实 `deepseek-v4-flash` 证明注册、持久化、handoff、authority、侧栏性能、root split、
relocation 与 v33 reset 全部通过。真实模型测试还修复了自动 Tool effect 的 TaskGrant identity 冲突，以及
process-wide `mcp:filesystem` 错误进入 project-bound catalog 的越界风险。Windows 仍是未来独立范围。
最终独立审计又补齐目录错误与 catalog 并发边界：注册会显式拒绝不可读/不可搜索目录，空路径、文件、
已删除目录均不产生半 Project/Session；Session 标题变化推进 catalog revision，使分页间新增、删除或重命名
都令旧游标 fail closed。500 × 200 并发变更探针刷新后得到唯一、完整且归组正确的 200 条结果。

## 4. Long-Running Workflows Today

### 4.1 DeepResearch

New research runs are accepted as durable `deep_research/v7`; `backend/deskpet/tools/research_tools.py` provides the focused child/manager collection core while the v7 graph owns production orchestration. The current production chain is:

```text
deterministic ingress -> durable v7 launch
  -> normalize -> plan (2-6 subdirections)
  -> manager schedules one focused research child per direction
  -> classify -> diagnose/continue (max two attempts) -> join
  -> synthesize valid reports + explicit limitations
  -> persist -> generic terminal outbox
  -> SessionDB durable projection + best-effort WebSocket
```

Current UI/delivery boundary:

```text
plan/search -> durable v7 children snapshots keyed by stable child_id
  -> DeepResearch progress card (direction/status/attempt/source count)
  -> internal dr-i.aN scheduler rows filtered from the generic panel

persist -> canonical artifacts[] file envelope
  -> ProductDeliveryAdapter -> one FileArtifactCard
  -> open / save-as / reveal in folder / copy path
history -> old nested file metadata normalized to the same file card
```

The parent-run projection is keyed by stable `child_id`, with retries updating the same direction row. A separate
children sequence lets late child terminal snapshots converge without rolling the parent card back from synth/final.
Canonical file envelopes use the existing `FileArtifactCard`; old nested envelopes remain readable during history
hydration without generating new delivery side effects.

V7 is immutable after release: v1-v6 remain registered for historical reads and recovery and are never coerced into a newer checkpoint schema. V7 owns manager-style decomposition, child monitoring/continuation, citation-validity-based three-state business delivery and a single final report; v6 remains the compatibility path for its existing exact-evidence runs. Search and fetch share the process-wide Search Gateway and FetchExtractService; workflow/node state and traces are durable in `workflow.db`, while low-cardinality diagnostic mirrors remain in `metrics.jsonl`.

Provider health is shared per provider through a closed/open/half-open circuit. V7 can converge to `completed`, `partial`, or `insufficient_evidence`, but its current quality gate only checks report/citation availability and legal footnote references; it does not preserve explicit Top-N cardinality or prove per-item citation coverage. Engine completion is stored separately from business status, while v7 `business_status` currently reaches report/final-assistant envelopes but not the main `workflow.final` progress-card projection, so the card can show completed/100% for a partial report. These are known v7 boundaries and require a new immutable workflow version rather than mutating released v7. When public SERPs are unavailable, a child may nominate bounded direct URLs, but fetched text must pass the same evidence gates. Current implementation and spike evidence are documented in [`DeepResearch.md`](DeepResearch.md), [`SEARCH_GATEWAY_DEEPRESEARCH.md`](SEARCH_GATEWAY_DEEPRESEARCH.md) and [`../plans/2026-07-19-deepresearch-simplification/spike/result.md`](../plans/2026-07-19-deepresearch-simplification/spike/result.md).

### 4.2 PPT Pro

`backend/deskpet/tools/ppt_tools.py::_ppt_pro_orchestrate()` is an explicit but process-local workflow:

```text
DeepResearch
  -> draft outline
  -> user accept/modify/reuse/cancel loop
  -> render PPT
  -> preview/visual review
  -> artifact and receipt reporting
```

`ppt_outline_history` persists proposals. `_PPT_PRO_RUNNING`, `_PPT_PRO_TASKS` and the outline waiter registry hold active tasks/Futures in memory. Restarting the backend cannot continue the original coroutine after an outline decision. Rendering is a side-effecting boundary and must not be repeated blindly after recovery.

The top-level ToolRegistry handler returns `researching` immediately, so its first success receipt describes background-task acceptance rather than final deck delivery. The background path directly calls DeepResearch, LLM outline generation, image generation, rendering and visual review, then manually emits terminal artifacts/receipts. A graph migration must make those two meanings explicit instead of treating one tool receipt as the whole workflow.

The older `ppt_create` image path also starts a background worker and returns `generating` before the file exists. It shares the same acceptance-versus-delivery receipt and cancellation problem even though AC-7 focuses on PPT Pro.

### 4.3 Complex Code Tasks (historical pre-Harness context)

Before the single-main-Session cutover, Code mode used the same `AgentLoop`, augmented by:

- a code persona that asks the LLM to clarify, plan, execute and verify;
- an optional persisted plan-confirm card;
- durable `code_todos` and code-session/project binding;
- `ask_clarification`, permission Futures and UI responses;
- completion probes, VerifyGate, GoalChecker, external evaluator and self-check;
- optional sidecar subagents/teams.

These details explain the migration pressure; they are not a current product mode. New production
records do not select persona, tools, Driver or workspace through Code mode or `task_type`.
Historical todos/session bindings remain readable for compatibility.

Subagent execution has three separate shapes: blocking `agent_parallel`, process-local nonblocking subagent registry/queue, and a team task SQLite store whose workers are still process-local. None currently provides a durable lease and crash reclaim contract for workflow nodes.

## 5. Human-In-The-Loop Today

simple_harness has several real user gates:

| Gate | Durable record | Active waiter | Restart behavior |
|---|---|---|---|
| Tool permission | Audit/receipt around tool execution | In-memory `Future` in permission gate | Pending execution cannot resume exactly. |
| Code plan confirmation | `session_plans.awaiting` | `_PLAN_CONFIRM_WAITERS` Future map in `main.py` | UI card can rehydrate; original coroutine is gone. |
| `ask_clarification` | Conversation/UI event | In-memory pending Future | No durable suspended node. |
| PPT outline confirmation | `ppt_outline_history.status` | In-memory outline Future registry | Proposal survives; workflow continuation does not. |
| Skill candidate confirmation | Pending candidate row | No waiter; a later command resolves by candidate id | Pending data can survive, but proposal delivery is direct WebSocket-only and the main message page does not render the candidate role, so this is not a recoverable main-thread HITL path. |
| Team permission/decision | Per-team SQLite data | Process-local teammate coordination | Data survives; active worker continuation does not. |

The UI interactions are genuine Human-in-the-loop, but the suspension mechanism is not durable. A future graph runtime should turn each gate into a persisted `waiting` node plus an idempotent response command.

## 6. Recovery Today

Recovery is reactive:

- Session messages, goals, todos, plans, artifacts and receipts survive restart.
- `AutoResumeOrchestrator` classifies selected `ErrorEvent` reasons, asks the supervisor for a nudge, then dispatches a new chat run.
- Supervisor follow-up can enqueue another synthetic turn.
- Tool circuit breakers, termination gates and convergence controls stop local failure loops.
- A new message for the same session cancels the previous chat task, but cancellation does not guarantee that threadpool-backed side effects have stopped.

This is useful resilience, but the recovery unit is a chat run. There is no persisted node lease, committed state version, compare-and-swap owner, retry policy per node, or deterministic state merge for parallel branches.

## 7. Observability And Evaluation Today

Current observability is split across several systems:

1. `workflow.db` and SessionDB hold authoritative run/node/effect/event/delivery facts; node attempts and
   trace spans keep status, timing and parent-child identity across restart.
2. `IterationTracer` writes optional JSONL events keyed by task/session.
3. `MetricsSink` writes privacy-safe whitelisted counters to `metrics.jsonl`.
4. `ContextAssembler` keeps a 50-record in-memory decisions ring exposed by `ContextTracePanel`.
5. Prometheus exposes a small set of operational metrics such as LLM TTFT.
6. Individual workflows emit their own coverage, timings, logs, artifacts and receipts.
7. Python stdlib logs and structlog events share one JSON-lines formatter. The active file is
   `<resolved-user-log-dir>/backend.log`, honoring `DESKPET_USER_LOG_DIR`, portable mode and the selected
   user-data directory; it rotates at 20 MiB with five backups. Both logging paths cross one final redaction
   processor: sensitive field names are replaced recursively and credentials/keys/JWT/email/phone/card-like
   values embedded in messages are removed, while correlation fields remain intact. A third-party stdlib
   record and a native structlog record were both parsed as JSON in the current smoke check; redaction and
   observability regression is `12 passed`.
8. Tauri diagnostics collect/reveal the same user-data log directory and use native archive/reveal commands
   on Windows, macOS and Linux.

The durable workflow ledger can answer which node/effect failed, on which attempt, with which terminal status
and artifact/delivery outcome. The redacted JSON log can correlate many live failures by `session_id`, `run_id`,
`request_id`, event and exception type; node handlers now log the original exception instead of leaving only a
collapsed `workflow_node:...:permanent` reason. It still cannot guarantee that every legacy/third-party log line
contains all correlation fields, and the diagnostic bundle does not yet synthesize a one-click run timeline,
invariant report or replay package. The iteration tracer is disabled unless an unconfigured
`[agent].iteration_trace_enabled` key is added; `[context.assembler].trace_enabled` controls a different
feature. Context Trace remains a global process-local ring rather than a persisted, session-filtered trace.

Current quality checks are runtime gates rather than an evaluation platform:

- VerifyGate matches completion claims against receipt evidence.
- GoalChecker uses an LLM judge for active goal completion.
- ExternalEvaluator uses a different model/persona and returns score/issues/pass-or-revise.
- PPT visual review evaluates rendered slides.
- Existing scripts/tests provide domain-specific regression checks.

Remaining platform concepts include a fully common trace/span schema across legacy paths, read-only/forked
replay, evaluator records, datasets and version comparison. Parent-child workflow identity, persisted run/node
indexes and artifact delivery facts now exist for the durable Harness path, but are not yet exposed as one
complete diagnostic product.

There are concrete contract gaps behind that summary: evaluators return incompatible tuple/dataclass/dict
shapes; some evaluator event names and score fields are not accepted by the metrics whitelist; universal secret
redaction and required correlation fields are not enforced at every logging callsite; and the diagnostic bundle
does not include trace/checkpoint/evaluation summaries. These must be unified without weakening the existing
privacy boundary of anonymous metrics.

## 8. External And Platform Dependencies

| Area | Current dependency/boundary |
|---|---|
| Desktop | Tauri/Rust, WebView2(Win)/WKWebView(mac), React/TypeScript，单窗 Workbench（Chat/Skills/Artifacts/Settings）；桌宠 sprite/Canvas2D 与 Live2D 渲染均已退休。 |
| Backend | Python asyncio/FastAPI/WebSocket adapters. |
| Persistence | SQLite/aiosqlite, WAL, optional sqlite-vec. |
| Models/audio | Torch/CUDA, BGE-M3, Silero, faster-whisper, CosyVoice/edge-TTS. |
| LLM/secrets | OpenAI-compatible provider registry (user-supplied baseUrl + apiKey), OS keychain and per-session resolution. 2026-08-09: the hosted-account relay path was removed entirely. |
| Research/browser | Search providers, Scrapling/trafilatura, browser-use/Chromium or system Edge CDP, optional Jina/direct-source adapters. |
| PPT/Office | python-pptx, image generation provider, LibreOffice plus WPS/Office COM rendering on Windows. |
| Extensions | MCP subprocess/client integration and filesystem-based skills. |
| Local execution | AgentLoop-selected ToolRegistry path, plus slash handlers, background workers and Tauri commands. |

The graph core must remain Python-only and UI-agnostic. It must not require a cloud trace service. Tauri/WebSocket, provider, tool and storage concerns enter through adapters.

## 9. Ownership Boundaries For The Upgrade

The following boundaries are stable enough to build on:

```text
workflow core
  definitions / validation / runner / state patches / policies
        |
workflow ports
  checkpoint store / trace store / evaluator / human response / clock
        |
simple_harness adapters
  SessionDB / ToolRegistry / provider / WebSocket events / artifacts / receipts
        |
workflow definitions
  DeepResearch / PPT Pro / Complex Code
```

The current Harness remains responsible for product policy. Workflow adapters must reuse ToolRegistry where a registered tool exists. Internal workflow stages that are not public tools still need equivalent centralized permission, trace, idempotency, artifact and receipt ports; they must not silently preserve today's bypasses.

## 10. Technical Debt And Risks

| Risk | Current evidence | Planning consequence |
|---|---|---|
| `main.py` owns too much orchestration | Provider, WebSocket, plans, permissions, service wiring and persistence meet there. | Add adapters and service registration; avoid placing graph algorithms in `main.py`. |
| Three workflows may fork their own runtimes | Research, PPT and Code already have separate state conventions. | Build and test one generic runtime before workflow migrations. |
| Side effects are not node-addressable | Receipts exist but lack workflow/node attempt identity. | Add idempotency keys and receipt linkage before replay. |
| Durable waiting is split-brain | DB records survive while Futures do not. | Persist waiting commands first; Futures become transport optimizations only. |
| State payloads can become huge | Research passages and rendered images are large. | Checkpoints store references/artifacts for large blobs, not unlimited inline JSON. |
| Parallel state merge can be nondeterministic | Research fan-out and subagents complete in arbitrary order. | Require reducers or conflict detection per state field. |
| Trace privacy is stricter than debug needs | Metrics intentionally rejects rich payloads. | Keep metrics separate; trace uses explicit redaction and local retention policy. |
| Replay can repeat destructive actions | File/Office/system tools have side effects. | Default replay reuses committed outputs; re-execution requires policy and confirmation. |
| Migration blast radius | `state.db` contains messages, memory, code and goals. | Add idempotent migrations, backup/rollback tests and repository-level compatibility tests. |
| Schema ownership is already split | Formal migrations and lazy runtime table creation disagree on the target version. | Repair the migration baseline before adding workflow tables; prohibit new business-owned DDL. |
| Cancellation cannot stop all effects | Cancelling an asyncio task does not terminate already-running threadpool work. | Model `cancel_requested` separately from terminal `cancelled`; reconcile late effect results. |
| Existing task claims have no lease | Goal/team/task state is not a general crash-reclaim coordinator. | Add owner, epoch, expiry and CAS transitions to workflow runs/nodes. |
| Trace correlation is unreliable | Iteration trace is default-off and generated ids do not match the live run. | Create trace/run ids at ingress and pass one context through every adapter. |
| Evaluation telemetry can disappear | Runtime evaluators emit shapes/events outside MetricsSink's closed schema. | Persist normalized evaluation records first; metrics becomes a low-cardinality projection. |
| Multiple ingress venues drift | Text, voice, slash/control and background workers do not share one call sequence. | Route workflows through a venue-independent coordinator and keep venue adapters thin. |
| Tool timeout can outlive its receipt | `wait_for` cannot kill a running threadpool handler; the caller may return before the effect finishes. | Persist effect intent before dispatch and reconcile late completion before retry/replay. |

## 11. Relevant Feature Defaults

| Capability | Current default/source | Baseline meaning |
|---|---|---|
| Plan confirm / agent team | Enabled in `[features]` | UI gates and team tools are active, but waiting/execution is not durable. |
| VerifyGate / ExternalEvaluator | Strict / enabled | Runtime completion gates are active; outcomes are not a unified eval record. |
| Context decisions trace | `[context.assembler].trace_enabled=true` | In-memory Context Trace view. |
| Agent iteration trace | No configured `[agent].iteration_trace_enabled` | Effectively off by default. |
| DeepResearch v7 durable workflow | `[workflows].deep_research_version="v7"` | New runs use the six-node manager/children graph; v1-v6 remain compatibility/recovery definitions. |
| Search Gateway | `[search_gateway].enabled=true` | Shared provider/cache/circuit resources with request-local budget and diagnostics. |
| PPT preview/outline history | Code defaults enabled | Preview files and outline rows exist; active background task/Future remains process-local. |

## 12. Test Boundaries

- Pure graph definitions, state reducers, checkpoint transitions, leases and trace serialization: deterministic pytest.
- Crash/restart, SQLite migration, duplicate response and side-effect replay: integration tests with failure injection.
- Existing Harness adapters and three production workflows: focused regression suites plus live stack tests.
- Trace viewer, durable pause/resume, replay confirmation and human scores: project-mandated windows-mcp true UI testing with screenshots and backend logs.

No protocol-level WebSocket injection may replace real UI evidence for user-visible behavior.

## 13. 2026-07-11 Durable Runtime Calibration

The durable workflow upgrade is now present under `backend/deskpet/workflows/`. `WorkflowLauncher` owns accepted-async background driving and terminal outbox publication; `WorkflowRunner` owns leases, resume and terminal convergence; `WorkflowExecutionObserver` records node executions and structured spans. `workflow.db` is authoritative for runs, node executions, checkpoints, events, deliveries and trace/evaluation data.

The current session projection uses one durable public path:

```text
Workflow node wrapper
  -> WorkflowExecutionObserver
  -> node execution + Trace span persisted

Workflow node wrapper
  -> WorkflowProgressReporter (public-node whitelist)
  -> stable workflow.progress Outbox event
  -> SessionDB workflow_event_id + WebSocket delivery

WorkflowLauncher
  -> workflow.accepted / terminal intents / workflow.final
  -> WorkflowOutbox
  -> SessionDB + WebSocket delivery handlers in backend/main.py
  -> ws.ts reduces structured events into the original session
```

DeepResearch, PPT Pro and Complex Code publish user-safe stage starts through this reporter. The progress `event_key` contains workflow/node/transition/attempt and is scoped by Outbox `run_id`, so a new retry attempt receives a higher durable event sequence while replay of the same attempt remains idempotent. `ws.ts` and hydrated history now feed the same reducer instead of appending each event as a normal assistant message.

AC-23 changes the product projection, not the durable event source: live and hydrated history envelopes feed one `run_id`-keyed reducer, monotonic `seq` rejects replay and out-of-order regression, and one `workflow_progress` component is updated in place. A late history response reduces into the current store rather than replacing a newer live component. `workflow.accepted` creates the component, `workflow.progress` updates its node-level display state, `workflow.final` alone locks completed/failed/cancelled run terminal state, and `workflow.final_assistant` remains a separate answer bubble. Node-level failed/cancelled progress may be superseded by a later higher-seq recovery event. Waiting remains visible until the next public stage starts or the run reaches final. Progress projection never carries raw prompts, tool arguments or file content; unrelated ordinary tool messages keep their existing UI semantics.

Progress may be emitted when a public stage starts. A node wrapper's `succeeded_pending` callback is not a durable completion boundary, so it must not publish “completed” before the following checkpoint/superstep is committed. The next public stage start is the safe user-visible evidence that the previous stage advanced; waiting, failure, cancellation and final states remain explicit. Recovery, manual resume and retry must reconstruct the persisted `delivery` session reference rather than substituting `workflow_runs.session_id`, otherwise the epoch guard in `backend/main.py` correctly discards the event.

PPT outline decisions are a separate product state machine: `modify` validates non-empty feedback, resolves the current decision into `revise_outline`, creates a new revision and a new pending decision/card, and must say “正在修改大纲”; `accept` and `reuse` enter generation; `cancel` terminates without generation. Duplicate responses retain the existing durable decision CAS semantics.

PPT Pro v1 creates stable slide records, checkpoints each generated background, and uses a deterministic deck-level planner plus six Pillow compositors. The planner assigns `cover_band/text_left/text_right/visual_top/floating_card/quote_center`, enforces deck coverage and adjacency constraints, and runs the same text-fit policy used by the compositor before any image effect is committed.

The planner runs before effect identity is computed, aligns each text-free image prompt's negative-space direction with the corresponding Pillow safe region, validates a CJK-capable font, and fails explicitly on unfit copy. Variant, layout-spec, compositor and font-policy versions enter the effect hash. The final boundary remains a 1792x1008 raster page; `python-pptx` inserts exactly that one picture and adds no visible text or decoration shapes.

For AC-21 the image-mode boundary becomes:

```text
confirmed outline deck (AC-22 target)
  -> deterministic layout plan + variant-specific text-free background prompt
  -> exact visible copy + layout variant/spec version in the input hash
  -> one durable image effect per stable slide id + content revision/input hash
  -> 16:9 normalization + variant-aware deterministic text compositor
  -> blank PPT slide with exactly one full-bleed picture
  -> preview + visual evaluation
  -> regenerate only pages named by visual issues
  -> publish artifact
```

Template mode remains available for legacy/compatibility requests. Explicit `full_page_images` is strict: provider failure during initial generation or a visual revision terminates with a user-safe recoverable error and never reports a template deck as a successful full-page result. Full-page image mode intentionally trades away element-level editability; speaker notes and the outer `.pptx` artifact remain.

Visual issue invalidation is explicit: issue page -> stable slide id -> incremented page revision -> revised full-page prompt -> recomputed input hash -> only that record returns to `pending` -> image map generates a new effect. The PPT production adapter validates the generated file and the dedicated assembler owns 16:9 normalization and the `blank slide + exactly one full-bleed picture` invariant; the assembler never probes or calls the image provider.

## 14. Native Workflow Engine（当前）与替换基线（历史）

当前 durable layer 使用 simple_harness 原生引擎。`WorkflowDefinition.bind()` 直接创建 `NativeWorkflowExecutable`；新 checkpoint 使用 `deskpet-native-json-v1`，原生 frontier、条件边/join/reducer、重试、`WorkflowInterrupt`、fenced checkpoint、replay/fork/eval 均已成为生产实现。LangGraph/LangChain 的生产、锁文件和冻结包依赖已移除；旧名称 `FencedAsyncSqliteSaver` 仅作为兼容 import alias 指向 `NativeCheckpointStore`，不是第二个运行时。

> 下表和本节余下文字记录 2026-07-11 切换前的替换设计，用来解释边界由来；其中 LangGraph、StateGraph、`Command(resume=...)` 和 legacy saver 都是**迁移前事实**，不能用于描述当前生产链路。

迁移前的耦合及其已完成的原生替换边界如下：

| Coupling | Pre-migration owner | Native replacement boundary |
|---|---|---|
| Graph scheduling, join and retry | `workflows/definition.py::WorkflowDefinition.bind` | Execute `WorkflowDefinition` directly with a versioned static frontier; support sequential/conditional edges, multi-source join, exclusive barriers, bounded loops/retry and max-step cancellation checks. Dynamic map/Send is not part of the current contract. |
| Execution identity | LangGraph `runtime.execution_info` -> `NodeExecutionIdentity` | Deterministically generate checkpoint/task/attempt/first-attempt identities from run lineage, base checkpoint, invocation key and retry attempt before calling a handler. |
| Node lifecycle SPI | LangGraph node wrapper in `definition.py` | Native wrapper owns observer/progress callbacks, trace span activation, retry classification and `succeeded_pending`; storage remains external but these hooks are an engine contract. |
| Interrupt control flow | `definitions/v1/ppt_pro.py`, `definitions/code_nodes.py` | Raise a simple_harness `WorkflowInterrupt` carrying JSON-safe prompt/id. Native checkpoint commit atomically writes pending state, open decision and run `waiting`; Runner only owns lease/invocation/terminal convergence. |
| Checkpoint commit/projection | `store/checkpointer.py::aput_writes/aput` | Store canonical JSON native snapshots and preserve the two-phase invariant: handler success projects `succeeded_pending`; only the fenced checkpoint/head/ownership transaction promotes it to `succeeded`. |
| Resume/replay/fork | `definition.py::WorkflowExecutable`, `replay.py` | Resume from persisted native frontier; history/fork consume native snapshots. V1 fork remains root-namespace only, rejects pending writes/active fan-out, and requires the existing explicit confirmation gate for dangerous effects. |
| Evaluation adapter | `scripts/workflow_eval_adapter.py` | Use an in-memory/native checkpoint store and simple_harness interrupt metadata; preserve dataset/experiment behavior without framework types. |
| Legacy compatibility | `JsonPlusSerializer` typed checkpoint blobs | Detect by explicit persisted type. An isolated no-pickle reader may expose allowlisted metadata/state for read-only legacy history only; every legacy nonterminal run becomes safely blocked and is never converted or resumed. |
| Packaging and guard | `pyproject.toml`, `uv.lock`, `deskpet-backend.spec`, runtime hook, dependency smoke | Remove LangGraph/LangChain direct and transitive dependencies, hidden-import collection and strict-msgpack hook; replace smoke with a source/import/clean-interpreter guard and record lock/frozen deltas. |

The replacement must not duplicate a general Pregel engine. The three registered workflows require sequential nodes, conditional routes, bounded loops, multi-source joins, exclusive interrupt barriers and stable per-domain map effects. They do not require arbitrary dynamic topology, distributed scheduling or cross-machine consensus. ToolRegistry, permissions, effect ledger, Artifact/Receipt delivery, Trace/Evaluation storage, Session delivery, lease/CAS and retention remain authoritative product services outside the scheduler. Their execution lifecycle adapters do not remain untouched: observer/progress/waiting/failed classification and evaluation execution must be rewired as native engine SPI.

Checkpoint compatibility is a versioned boundary. New rows use `checkpoint_type="deskpet-native-json-v1"` and a canonical snapshot with `engine_kind="deskpet-native"`, `snapshot_version=1`, `state`, `frontier`, `step`, `node_writes`, `interrupt`, `parent_checkpoint_id` and deterministic identity metadata. Completed legacy runs keep their durable run/event/trace/artifact history; an isolated no-pickle reader may decode allowlisted legacy values only for that read-only view. Every legacy nonterminal run is blocked with a safe recovery action and is never converted, resumed, silently restarted or used to execute a side effect.

The engine switch changes implementation identity. Native registrations publish new implementation/engine metadata and do not pretend to match a legacy LangGraph implementation hash. Terminal legacy runs remain queryable. Legacy nonterminal runs are not converted or resumed in this migration: startup deterministically moves them to `blocked` with `legacy_checkpoint_incompatible`, preserves their existing run/session/decision/pending-write rows as read-only evidence, closes no decision and executes no effect. A user retry creates a new native run through normal ingress, so decision nonce/version and Session run identity never cross engine identities. This is deliberately separate from the existing fork saga, whose `prepare_fork()` copies source workflow/version/hashes and therefore cannot be used as an engine migration transaction. Existing native fork restrictions remain fail-closed, with dangerous effects allowed only after the existing explicit confirmation.

## 15. Context OS V1 Runtime（历史目标；当前 foreground 未接入）

> **2026-08-21 校准**：本节记录 2026-07 Context OS 的已实现组件与目标数据流，不是当前
> foreground SDK 请求链。`context_os_v1` 虽仍可配置，但 `_execute_sdk_run` 不读取该开关；
> `context_assembler` 在生产 service context 只注册空 slot。下述 planner/snapshot/attempt-store/
> prepared-tool 断言对当前文字 Run 均不可当作已生效事实。

历史设计中 Context OS V1 由 `context_os_v1=true` 启用；SDK cutover 后该 foreground 接线已经断开。

### 15.1 Owners and request flow

历史目标 flow 是：

```text
text/code/voice ingress
  -> SessionDB append (authoritative transcript)
  -> ContextAssembler
       typed fragments + ToolExposureIntent + task projection inputs
  -> ContextRequestPlanner.prepare_initial
       one catalog/policy resolve
       PreparedToolSet + fixed prefix/current turn/attachments/reserve
       SessionHistoryPlanner coverage plan
       common-safe provider-chain budget
  -> optional CoverageCompactionJob[]
       AgentLoop -> ContextCompressor -> ContextSegmentStore commit
       -> planner replan with the same PreparedToolSet
  -> AgentLoop
       exact prepared messages and logical tool set
       per-provider/model attempt re-budget
       provider transport
  -> ContextAttemptStore / ContextTrace
       planned -> sent -> terminal, matching usage and coverage facts
```

Ownership is deliberately narrow:

| Concern | Authoritative owner |
|---|---|
| Raw conversation and message identity | `SessionDB`; assembler top-k history is never treated as complete Session coverage. |
| Fragment lifecycle and stable-prefix candidates | `ContextAssembler` and typed `ContextFragment` metadata. |
| Initial whole-request plan and common provider-chain safe budget | `ContextRequestPlanner`; protected prefix, current turn, actual tool payload, attachment estimates and generation reserve are charged before history. |
| Lossy summarization | `ContextCompressor` only. The planner may return jobs but does not call an LLM or commit a summary. |
| Reactive execution, compaction ordering and post-compact remount | `AgentLoop`; snapshot flush precedes lossy compaction, and protected/stable fragments, active skills/path rules and the latest complete causal tail are remounted. |
| Provider-specific budget and request lifecycle | The immediate provider-attempt seam plus the public transport marker. Each candidate is re-estimated for its actual provider/model/window and `tools` or `None`; a budget failure cannot send and may continue to the next fallback candidate. |

Provider chains first plan against the candidate with the smallest effective input budget. The order is estimate, reversible history planning, required coverage compaction, replan, then recoverable block. Every actual attempt is checked again, so a provider-specific window or adapter difference cannot reuse an earlier provider's budget result.

Attachments remain provider message content blocks. `attachment_budget` creates body-free `AttachmentRef` records containing only message/content indexes, media type, byte size and estimate metadata; attachment bodies are neither copied into snapshots nor exposed by ContextTrace. Text, code, voice and subagent preparation all pass the resulting refs and token total into the same planner.

### 15.2 Snapshot and coverage persistence

`state.db` migration V18 owns two Context OS tables:

- `session_context_snapshots` stores a derived `TaskContextSnapshot` and a bounded prepared-tool summary. Workflow/goal/receipt/artifact stores remain authoritative; the snapshot is a recoverable projection and cannot promote pending evidence to completed state.
- `session_context_segments` stores raw/summary coverage nodes, continuous message-id ranges, source hashes, child lineage, token estimates and page-in references. `ContextSegmentStore` validates source hashes and commits summaries through CAS-safe operations.

Snapshot writes use distinct DB row revisions and typed `ContextSnapshotHandle`/`SnapshotWriteReceipt` values. Initial projection, activation and provider-attempt adapter updates settle asynchronous CAS outcomes before scope activation or transport; cancellation after a DB commit cannot make an unknown write authoritative.

For one Session, every eligible, undeleted user/assistant/tool message is covered exactly once. If all raw rows fit, all raw rows are loaded. Otherwise `SessionHistoryPlanner` selects a continuous raw tail plus valid summary nodes and page-in references. A reference does not count as content coverage. Gaps, overlaps, stale source hashes, broken tool-call causal groups or residual compaction jobs fail closed before provider transport. `session_history_page_in` is bound to the current runtime Session and pages only at complete causal-group boundaries.

### 15.3 Tool capability plane

`ContextAssembler` produces provider-neutral `ToolExposureIntent`. `ToolCapabilityResolver` reads one strict, session-aware catalog/policy snapshot and freezes an immutable `PreparedToolSet` containing exact direct schemas, bounded deferred references, deny decisions, registry/policy revisions and fingerprints. AgentLoop and diagnostics consume that same set; ON mode does not reduce it to names and reconstruct schemas later.

Deferred capabilities are exposed only through scoped `tool_search`, `tool_describe` and `tool_activate` bridges. Search cannot enumerate another Session or policy-hidden tools. Activation is an exclusive turn: eligibility, current spec hash, policy, budget and snapshot serialization are validated before the candidate revision is committed. Every provider attempt and real tool execution revalidates direct/activated references; MCP unregister/replace or policy drift makes the old reference stale and fails closed. Existing PermissionGate, durable effect authorization, receipts, artifacts, VerifyGate, timeouts and breakers remain downstream authorities.

An authorized capability scope is pinned by the sole `EffectBatchExecutor` while a prepared effect is in flight, then its orphan TTL restarts after settlement. Long-running tools therefore cannot expire their own live scope; an actually missing/expired scope remains fail-closed and is reported separately from a registry-wiring failure. Explicit Chinese `调研` requests, including timeline/chart deliverables, route to the durable DeepResearch profile instead of the synchronous ReAct fallback.

### 15.4 Attempt diagnostics and rollback

`ContextAttemptStore` is a body-free, bounded in-memory diagnostic ring. It records `purpose`, Session/request/attempt identity, provider/model/window, adapter identity, message and tool hashes, fragment decisions, tool-set revisions, attachment/reserve attribution, compression resolution, Session coverage facts and matching authoritative usage. Auxiliary capability-gate/classifier/planner/compressor/research/workflow calls receive explicit purposes; provider bottom seams create an attempt if no complete outer attempt scope exists. The public transport coroutine changes `planned` to `sent` only after it obtains send control; completion, failure and cancellation close the same attempt.

ContextTrace renders these frozen facts, including raw/summary/reference coverage, gaps/overlaps/stale counts and page-in references, without credentials, full tool arguments, attachment bodies or sensitive file content.

Rollback is intentionally one-dimensional:

- `context_os_v1=true` is the shipped default and activates the planner, snapshot/segment stores, capability scope and attempt diagnostics.
- `context_os_v1=false` restores the preserved legacy `tools` names/order, legacy preflight/reactive behavior and legacy context-usage payload. It does not read Context OS snapshots/segments or register an attempt store.
- A startup log states the active owner, resolved window and migration version without prompt content.

Automated verification and benchmark results are recorded in [`../plans/2026-07-13-context-os-v1/results.md`](../plans/2026-07-13-context-os-v1/results.md). Real Windows UI E2E remains a separate evidence boundary and is never inferred from unit tests or protocol-level calls.

## 16. 伴生智能体成长能力现状（Context 入口描述为历史）

本节成长 Store/Router 事实仍供历史追踪，但下述主消息
`ProductTurnPreparer → RunKernel` 入口已被 SDK cutover 绕过；当前 foreground 入口以 §2 为准。

2026-07-25 Task 13 已在既有 Product Harness 上完成长期层的单一生产 authority 切换：

- `<userdata>/data/companion.db` 由唯一 `CompanionStore` 持有；owner-domain 状态按
  `profile_id + profile_generation` 分区，所有权威写使用 `BEGIN IMMEDIATE`、
  `WAL + synchronous=FULL`、stable id/hash、CAS 与 lease epoch。
- `GrowthAuthorityRouter` 是唯一 durable writer 指针。启动在 Product ingress 关闭期间
  完成 `legacy → preparing → companion`，当前恢复为 `companion/generation=5`；
  roll-forward marker 后只能继续 Companion 或进入 `paused`，不能回退 legacy。
- 启动顺序固定为 Capability runtime rehydrate → Companion composition →
  Product Harness（ingress closed）→ authority cutover → notification projection /
  Companion runtime adapter → ingress open。Harness、Kernel、Driver 与 Provider 边界没有
  增加第二套实现。
- `[companion.growth]` 已完成能力默认开启，pause 是独立 kill-switch。
  `PreferenceResolver`、GrowthEvent/terminal contributor、单 scheduler `CompanionRuntime`、
  V2 Reminder 与 durable notification/history projection 已成为生产 Companion 分支。
- `CompanionRuntime.start_prebound()` 先校验 Store 中 exact
  owner/generation/binding epoch，profile coordinator 再冻结 `IdentityReadyGate`；失败会
  暂停 Runtime，不能开放新 Companion Turn。
- Relay owner 只保存 namespaced id hash。可信 bind 会恢复同 owner inbox，或创建新的空
  UUID inbox 并把 `session_id` 返回前端；已有内容的 unowned legacy `default` session
  不能被当前账号认领。
- state.db schema v21 保存 session owner、消息 ingress outbox、excluded projection
  route/outbox 与 redaction receipt；workflow.db v22 保存原子 RunStartSnapshot、真实
  Provider invocation/outcome、Run/effect/delivery fence 与 terminal extension receipt。
- Provider claim-before-transport、handoff unknown、provisional retract 和最后物理 effect
  fence 继续复用通用 `ExecutionWriteLane`、`RunExecutionFencePort` 与 Effect/UoW。
- Task 13 follow-up 用 `GrowthProductionPipeline + ReflectionPostprocessor` 把 committed
  message 的 reflection typed result 串到 candidate build、frozen independent evaluation
  与 activation dispatcher。后台任务使用零工具 prepared context，但仍经同一
  `RunClient → RunKernel → ReActDriver`；Store 按 exact build id claim，失败/unknown 进入
  durable failure/replan 或 fail-closed，不按当前 UI 状态重建。
- 旧 `SkillCodifier/CandidateProposal/ToolPathRecorder`、Presenter/Voice codify 回调、
  裸 candidate confirm 和进程内 Reminder writer 已删除。Skill/Workflow 候选改走
  durable evidence → candidate → independent evaluation → RiskPolicy → activation saga；
  Capability active pointer 仍只由 Manager 与 CapabilityStore binding CAS 修改。
- 历史安装包的 ToolSpec fingerprint 只在当前 spec 能精确重算 v1 时 CAS 迁移；未知漂移
  继续 fail closed。旧 JSON/PendingCandidate 只按 `legacy_local_profile` 幂等导入并作为
  只读升级残迹，不能转给当前 Relay owner；非空旧 Skill inventory 因当前没有可证明的
  immutable pack 发布 receipt，会在不可逆 marker 前零部分导入并可见地阻断。

2026-07-25 真实源码 Tauri Relay mode 在空 owner inbox
`26f5276d-69e9-42b0-ba65-b4a8988b86d6` 完成真人主消息页问答：输入
“请只回复：Task13主消息页通过”，UI 回复“Task13主消息页通过 ✅”并回到空闲，
`chat_v2_final` 使用同一 session id。精确清理 roots `16040/13620` 的 21 个进程，
释放 9749762048 bytes private memory，survivor=0、8100/5173 listener=0。Task 13
自动化门为聚焦 `166 passed`、Companion `476 passed`、Skill/Preference
`127 passed`、Capability `251 passed`、Workflow `53 passed`、前端 `851 passed`，
tsc/manifest/diff check 通过；Task 14 的故障/性能/隐私权限与 deterministic smoke
已经完成。

2026-07-26 真实主消息页又发送两条明确纠正，均形成 durable GrowthEvent 并进入 production
reflection Run；真实 Relay 返回 HTTP 402 `account balance insufficient`，三次尝试后
job 终止为 `failed/background_run_failed`。这证明入口、失败吸收和重试路径，但没有证明
真实 candidate/evaluation/Manager receipt/binding 价值链。Task 15 为 PARTIAL/BLOCKED，
Task 16 只能做不依赖 provider 的文档、worktree、进程和 staged-scope 收尾，不能宣告计划
完成。模块事实与证据见
[`COMPANION_GROWTH.md`](COMPANION_GROWTH.md)、
[`task13-cutover-results.md`](../plans/2026-07-24-human-anchored-companion-growth/evidence/task13-cutover-results.md)。

## 17. 单主 Session 通用行动与可执行能力包

2026-07-24 的生产增量建立在 Harness authority 上，没有增加第二套 agent runtime：

```text
ordinary message -> top-level agent.general / ReAct
  -> model answers or calls prepared tools
  -> model may call workflow_spawn(profile_key)
  -> durable ProfileLaunchTicket -> ticket-bound child Driver
  -> EffectBatchExecutor -> ToolRegistry V2 / managed capability proxy
  -> success, or complete FailureSet -> same parent model -> next Attempt
```

### 17.1 运行、失败与续聊事实源

- `workflow.db` schema v16 持久化 task goal、plan version、attempt/failure set、
  Profile launch ticket、capability revision/binding/operation/runtime lease，以及
  `execution_user_continuations` FIFO。
- 每个 accepted provider action batch 对应一个 Attempt。全部失败阶段都产生绑定真实
  call/effect/child/evidence 的 failure report；下一 Attempt 仍由同一
  `agent.general` 父模型决定策略。
- running-root 继续消息保留原 root/task scope，conversation reservation、FIFO、
  React boundary 和 `pending_resume_signal` 都可重启恢复。
- 一个 `LiveRun.task` 是活动 Driver owner。取消、恢复和 retiring owner 由
  `start_lock + driver_lock + Run CAS` 排序；`CANCEL_REQUESTED` 只向取消收敛，不能触发
  provider relaunch。

### 17.2 授权与外部安全边界

- Manual 以 task/resource/action category 签发可审计 TaskGrant，越界再授权。
- Auto 只省略 simple_harness 的 permission/plan 等待；grant、Receipt、错误、取消和验证不省略。
- UAC、外部登录、OTP 等进入 durable `waiting_external`，不算失败，不创建新 Attempt，
  不绕过系统或第三方安全界面。

### 17.3 能力目录与生成工具

- 能力包有稳定 ID/version/source/compatibility/entrypoint/tools/MCP/permissions/
  dependencies/hashes/uninstall 元数据，版本和 active binding 不原地覆盖。
- local function tool 通过有界 JSON 子进程代理执行；有状态扩展可使用受管 MCP runtime。
  runtime 崩溃、超时和取消不会把未验证代码载入 backend。
- `CapabilityBuilderHost` 只在 search evidence 证明无可执行匹配后 admission；staging
  必须通过 manifest/schema、happy path、错误输入、healthcheck 和副作用路径验证，
  才能原子发布 run/project/user scoped revision。
- catalog refresh 后当前 root 重新冻结 capability/schema/grant fingerprint 并立即调用
  新工具；用户无需重发消息或重启应用。
- builtin Godot `1.0.2` 复用唯一文件/Shell/下载/应用/桌面原语，提供 Godot 探测、
  项目检查、CLI/编辑器运行知识与验证规则，不携带硬编码塔防模板。

### 17.4 2026-07-27 历史校准：单一认知入口与授权资源闭环

> **非当前生产事实**：普通 Text 现不进入 `ProductTurnPreparer.prepare_context()`；SDK composition
> 当前也使用 unconditional ALLOW、空 selectors 与不完整 ToolContext metadata。以下内容保留为
> cutover 前行为/目标契约。

普通 Text 消息当时从 `ProductTurnPreparer.prepare_context()` 直接进入
`prepare_direct_run()` 和 `RunKernel.start()`。`ProductVenueRunAdapter` 不再调用
`route_intent()` 或 `plan_decision()`；旧方法仅供兼容测试/非生产调用保留。因而
Context 较少的 IntentTriage 不能再 short-circuit、单独澄清、插入另一份计划或阻止
主 Agent。Session 历史、记忆、任务快照、冻结工具集与 Profile Catalog 只交给同一个
主 Run；澄清/规划由主 Agent 完成，写操作准入由 prepared tool authorization 完成。
旧 Voice 已安全关闭；后续 Realtime 入口必须先完成同样的产品准备，不能直接调用 Kernel。

Committed 用户消息仍进入 Companion ingress outbox。直接路径不再为了成长优先级运行
前置 IntentTriage，而以 `growth_signal_kind=none` 结算；原始消息仍可由后续 reflection
解释，但成长系统不能取得会话执行 authority。

根 Run 的 failed/cancelled canonical terminal event 通过
`session_terminal/session-transcript-v1` durable delivery 投影为同 Session 的 assistant
摘要。Sink 只接受 root，绑定 Session epoch，以 terminal `event_id` 幂等去重并清理
本地路径/凭据。下一轮 Context 组装前先 read-through；修复前没有 delivery 的旧失败仅在
Run `auth_epoch` 等于当前 Session epoch 时回填，删除/重建后的旧错误不会复活。

授权链保持 fail closed，但空资源契约不再击穿整个 Driver：

- `workflow_spawn` 的 system selector 精确绑定
  `root_run_id + catalog_generation + profile_key`，access 为 `delegate`；
- workspace selector 只能来自可信 `ToolExecutionContext.workspace/write_scope_root`，
  模型参数不能扩大范围；
- Registry 为 Office/PPT/file organize/memory 等实际授权工具冻结确定性 file 或 logical
  selector；全局 `authorization_resource_gaps()` 审计当前为空；
- prepared call 仍需授权但 selector 为空时，ReAct 将其归一为可重规划的
  `authorization_scope_missing` 工具失败，而不是 `driver_failed`。

Deferred capability 的 canonical identity 仍是 `source:name`。完整 ID 精确匹配；裸名称
仅在当前 deferred 集合唯一匹配时规范化，零个或多个匹配继续 `capability_denied`。Describe
nonce 与 activate 仍绑定规范化后的 exact identity。

真实当前源码 Tauri 验收 Session
`e9d345e4-55da-4de7-a33b-8156076ea26d` / Run
`6048fc2701b75ec383550753a758040c` 使用原始中文命令完成
`tool_describe("desktop_create_file") → tool_activate("builtin:desktop_create_file")
→ desktop_create_file`，最终在桌面写出 `春天的散文.txt`，Run 六层均 completed。

详细实现约束和测试证据见 [`AGENT_HARNESS.md`](AGENT_HARNESS.md)。

## 18. 2026-08-03 历史校准：Session 模型一致性与运行可见性缺口

> **2026-08-21 覆盖说明**：Session binding 仍能解析 provider/model，但当前 foreground 只用
> `(provider_id, model)` 刷新一个全局 SDK stack；`model_params`、thinking/fast/context window 未冻结
> 进本 Run。不同 Session 的前台 Run被全局锁串行，background 又不共享该锁。以下“start snapshot
> 已冻结全部模型策略”的表述仅代表 cutover 前设计。

当时主执行链计划把 Session 模型绑定作为 root/child Run 的事实源：
`llm.resolution.resolve_session_provider_chain()` 从
`state.db/code_session_provider` 读取 `provider_id/preferred_model/model_params`，
`main._resolve_agent_provider_chain()` 构造该 Run 的 provider chain，Host start snapshot
再冻结 provider/model launch policy。这个边界能保证已启动 root 及其 child 不因运行中
Provider Registry 变化而换模型。

显式绑定并非当前所有情况下都 fail closed。若绑定的 `provider_id` 已删除或 disabled，
`resolve_session_provider_chain()` 会记录 `session_binding_stale` 后静默回退全局 chain。这个
兼容行为会让“用户明确选择 Kimi”在 provider 失效时改用其他模型，是后续一致性契约必须明确
收口的边界；未显式绑定的 Session 才天然适合使用全局 chain。

但会话附属 LLM 调用尚未进入同一解析边界。`main._make_live_str_llm_call()` 接收一个进程级
provider resolver；FactExtractor、query rewriter、entity extractor、GoalChecker、problem
pre-analysis、memory tools、reflection、curation、PreferenceTurnInterpreter 等当前传入
`lambda: local_llm or cloud_llm`。`ModelPersonalWorkflowMatcher` 还直接持有同一个进程级
resolver。这些调用只有 purpose 或没有完整 attempt 标签，没有 Session/root Run 身份，
所以主 Agent 使用 Kimi 时，会话相关的记忆或偏好处理仍可能调用全局 GLM。真正的跨 Session
维护任务和属于某轮的附属任务目前也没有类型化 policy 区分。这是模型一致性修复的主要结构缺口，
不能靠捕获单个 HTTP 402 补丁解决。

历史上 Context usage 曾从全局 `effective_llm_model(config)` 生成 `0/window` stub，并可能混合不同
sample；该缺陷已由 SessionDB `ContextUsageStateV2` reducer 修复。当前无测量时只从 Session binding
构造明确的 `binding_only` state，不再伪造全局 measured-zero。尚未修复的是：真实 SDK
`provider_invocations.usage_json` 没有桥接到这份 V2 authority，终态采样仍探测 legacy
`ContextAttemptStore/provider.last_usage`，所以状态会长期停在 binding-only 或误读旧 legacy sample。

Harness Inspector 的当前生产 authority 是 V3：
`HarnessInspectorPanel -> harness_inspector_snapshot_request -> HarnessPublicReadService -> PublicRunSnapshotV3`。
旧 `harness_inspector_snapshot -> SqliteExecutionUnitOfWork.inspect_harness_run()` endpoint 仅服务
历史客户端，不得作为新时间线或 Inspector 功能的接线入口。

V2 的原始事实源仍然正确且只读：
`SqliteExecutionUnitOfWork.inspect_harness_run()` 从 start snapshot、lineage、provider invocation、
tool/effect 和 canonical events 生成只读 schema v2 snapshot。当前前端
`buildHarnessActivityFeed()` 把准备、root/child、每次 provider invocation、每次 tool call 和
关键 canonical event 展开成逐条记录；`buildFriendlyGraphNodes()` 除合并相邻 prepare 记录外，
基本一条记录对应一个图节点。长任务因而会出现上百个“决定工具→执行工具→继续处理”节点，
它是审计时间线的轻量翻译，不是真正的语义阶段图。

这个 snapshot 目前还有静默截断：lineage/provider/batch/attempt 各 `LIMIT 200`，tool/effect/
failure/event 各 `LIMIT 400`，durable workflow head 各 `LIMIT 100`，响应没有 cursor、total 或
`truncated` 标记。超长任务进入前端前就可能缺少早期因果，任何语义阶段投影都不能在未知完整性
时宣称覆盖了全部步骤。

右侧 durable workflow 消息已有另一条较紧凑的投影：`workflow_plans + workflow_effects +
public_messages` 通过稳定 `workflow_step_id/call_id` 绑定为可折叠步骤和工具详情。它当前直接把
durable `prepared_json/outcome_json` 解码后交给 UI；Provider input 有单独的脱敏投影，但工具
参数/结果没有同等级的字段级 public projection，不能假定现状已经满足公开展示安全边界。
后续修复应复用稳定身份，新增有界、字段级脱敏的 tool public projection，并建立独立、可重建
的 root-level semantic phase projection；原始
activity feed 继续保留在步骤详情/技术记录中。子 Run 的真实 failed/cancelled 终态不得改写；
当 root 后续接管并完成时，只新增根级聚合结果（例如用户语义上的“已接管并完成”）。当前代码
还没有这种 recovered-child aggregate，顶层完成与 child failed 只能并列显示，容易被理解为
整个任务失败。聚合不能只看 `child failed + root completed`；它必须使用 child terminal signal、
`TaskFailureReport.child_run_id`、Attempt 的 `trigger_failure_set_id/supersedes_attempt_id` 与后续
root terminal 建立可证明的恢复因果，并定义 waiting/cancelled/failed/completed 的稳定优先级。

## 19. 2026-08-03 实施结果：冻结模型 authority 与 public semantic read model

第 18 节记录的缺口已由 Session 模型与运行可见性切片收口。Provider Registry entry 现在有
durable incarnation/config revision，Session binding 有 epoch CAS；删除后同 ID 重建、双窗口旧
请求和 stale model catalog 都稳定冲突或 fail closed。产品启动必须先完成 state.db v23～v26
migration、Registry durable load、binding reconcile marker，再开放 `ProviderRoutingReadiness`。
Root 已启动后只读取 Host start snapshot 的冻结 provider plan；Session 后续换模型只影响下一
Root。

主调用与 FactExtractor、query rewriter、entity extractor、GoalChecker、problem pipeline、
preference/personal workflow、memory tools 等附属调用共用 `ProviderWorkloadRouter`。调用方必须
声明 workload class/callsite/purpose/session/root/request/detached；会话附属缺身份或绑定不可用时
不再走全局 chain，跨 Session 维护必须使用显式 `BackgroundModelPolicy`。附属 401/402/429/5xx
按真实失效域 breaker 隔离并写低敏 audit，不拥有 Root terminal authority。

DEV 故障验收使用严格、consume-once 的 `ProviderFaultScriptV1`，注入点位于 durable provider
claim 之后、物理 transport 之前。Session auxiliary 绑定 Root，child main 绑定 child-run
correlation 且不冒充 Root，detached maintenance 只绑定 request correlation 并保持 session/root
为 NULL；audit 持久化低敏 correlation hash。生产模式发现该脚本环境变量会拒绝启动。

Context Usage v24 使用 immutable sample + materialized state。恢复选择单个完整 measured/compacted
sample；没有样本时只由 Session binding 生成 binding-only 状态和“尚无用量”，不会把全局默认
模型伪装成 Session 测量值。

运行观察不再由前端逐事件归并。`HarnessPublicReadService` 从 workflow/state 两个一致 read cut
做全量 keyset 读取，返回带 totals、HMAC cursor、completeness 和 diagnostics 的 immutable manifest；
`semantic_projection` 在完整公开事实上构造稳定 DAG，将事实归为最多七类且只显示实际出现的
阶段。`RootOutcomeView` 仅在 child terminal、FailureReport、failure set、replacement Attempt 和
后续 root terminal 完整时显示 `completed_with_recovery`；blocked 只能来自结构化
`RunBlockSignalV1`。真实历史 Godot Root 的 366 个 public facts 当前得到 6 个阶段、29 个唯一
逻辑工具（23 个 shell）、`projection_complete=true` 和 recovered aggregate，child failed 事实仍
保留。

公开工具与 Provider 详情均使用 Root 冻结 policy 的 default-deny、有界、字段级脱敏投影；raw
prepared/outcome/provider input/output 不进入 schema v3。前端三处消费者共用按 Session/root 索引的
`HarnessPublicSnapshotStore`，响应驱动 singleflight 轮询在完整 terminal 后停止；details 分页与
snapshot 刷新分槽，切换/取消和晚响应不串 Root。

停止后的工具完成由显式三阶段 handoff 收口。`StartedAck` 返回前即 arm completion observation；
consumer 取消会重新读取最新 durable effect version，将仍可能完成的 effect 收敛到
`unknown/started_may_complete`，原始 `CancelledError` 不会被 CAS 冲突遮蔽。真实 handler 完成后
process-local late evidence 在 durable terminal 决策前只 peek、不出 ready index；terminal Run 的
Reconciler 将其结算为 `late_reconciled/reconciled/reconciled_completed_suppressed` 后才 acknowledge，
不会恢复 Driver。running 窗口与 CAS loser 都保留重试能力。

## 20. 2026-08-17 SDK v0.1.1 Post-Cutover 代码清理基线

### 20.1 当前状态（SDK 已接入，旧代码待清理）

> **⚠️ 本节为 2026-08-17 清理执行前的历史记录**：§20.1/§20.2 描述的"待清理/待删除"工作已于
> 2026-08-17 全部执行完毕（见 §1.2 当前事实与 PROJECT_STATUS 2026-08-17 里程碑）；其中
> `main.py:9544`/`main.py:9949` 等行号证据已失效，runtime.py/reconciler.py 等 13 个引擎模块
> 实际被**保留**（非删除）。保留本节仅作清理决策的历史依据。

**SDK v0.1.1 已是唯一生产执行 authority**：

- 启动链：`main.py:_activate_product_sdk_runtime()` → `_build_product_sdk_runtime_stack()` → SDK Runtime
- 执行核心：`simple_harness.runtime.kernel.RunKernel` + `simple_harness.runtime.drivers.react_loop` + `simple_harness.workflow`
- 数据库：SDK 管理的 `<user-data>/data/simple-harness-sdk/execution-v1.sqlite3`
- Ingress：所有入口（text/voice/background）路由到 `_sdk_ingress.open_venue()`（`backend/main.py:9544`）
- 产品适配器：`backend/deskpet/sdk_adapters/*` 桥接产品服务到 SDK ports

**代码证据**：
- `backend/main.py:6363`: `# Slice C: Use SDK Runtime instead of legacy harness`
- `backend/main.py:9544`: `outcome = await _sdk_ingress.open_venue(...)`（真实调用）
- `backend/main.py:9949`: `_sdk_ingress.open()`（SDK ingress 被激活）
- `backend/deskpet/sdk_adapters/ingress.py:1-6`: 注释声明 “sole ingress for all product entry points”

**旧 harness 代码仍存在但未使用**：

虽然 SDK 已接管所有执行，但以下模块仍存在于源码树且 `_build_product_harness_stack()` 仍被调用：
- `backend/deskpet/harness/kernel.py`, `bootstrap.py`, `runtime.py`
- `backend/deskpet/harness/drivers/react*.py`, `workflow.py`
- `backend/deskpet/harness/reconciler.py`, `admission_launch.py`, `live_index.py` 等
- `_harness_runtime`, `_harness_venue`, `_harness_accepting` 全局变量

**关键事实**：`_harness_venue` 被构建但从未被任何 ingress 调用（全仓搜索 `await _harness_venue` 返回 0 结果）。

### 20.2 清理目标（删除死代码）

**待删除的模块**（已被 SDK 完全替代，但仍有遗留引用需清理）：
- `backend/deskpet/harness/kernel.py` → SDK `simple_harness.runtime.kernel`
- `backend/deskpet/harness/bootstrap.py` → SDK runtime 初始化
- `backend/deskpet/harness/runtime.py` → SDK runtime infrastructure
- `backend/deskpet/harness/drivers/react*.py` → SDK `simple_harness.runtime.drivers.react_loop`
- `backend/deskpet/harness/drivers/workflow.py` → SDK `simple_harness.workflow`
- `backend/deskpet/harness/reconciler.py`, `admission_launch.py`, `live_index.py`, `kernel_terminal.py` 等

**清理前必须处理的引用**：
- `main.py` 中 3 处 `deskpet.harness.kernel.root_run_identity` 导入（需确认 SDK 提供等价功能或内联实现）
- `main.py` 中 `_build_product_harness_stack()` 调用及其依赖链
- `deskpet.harness.adapters.product_composition` 及其对旧 drivers 的导入
- 17+ 个测试文件的 harness 模块导入（需评估是否迁移到 SDK 或删除相应测试）

**待删除的代码**（未使用的构建逻辑）：
- `main.py` 中的 `_harness_runtime`, `_harness_venue`, `_harness_accepting` 全局变量
- `_build_product_harness_stack()` 函数及其调用
- `deskpet.harness.adapters.product_composition` 及相关旧 adapter 代码

**保留的模块**（产品特定逻辑，通过 SDK adapters 工作）：
- `backend/deskpet/sdk_adapters/*` - 产品到 SDK 的适配器层（**必须保留**）
- `backend/deskpet/tools/*` - 产品特定工具实现
- `backend/deskpet/skills/*` - 产品特定技能
- `backend/deskpet/capabilities/*` - 产品能力系统
- `backend/deskpet/companion/*` - 产品陪伴成长系统
- `backend/deskpet/memory/*` - 产品记忆系统
- `backend/deskpet/session/*` - 产品会话管理
- `backend/deskpet/execution/*` - 执行相关契约（部分可能被 SDK 使用）

**保留的 harness 模块**（被测试或产品其他部分引用）：
- `backend/deskpet/harness/contracts.py` - 如果被 SDK adapters 或测试引用
- `backend/deskpet/harness/ports.py` - 如果被 SDK adapters 或测试引用
- 其他被测试套件或产品代码实际导入的模块

清理前需通过 grep 确认每个模块的实际引用情况，避免误删仍被使用的代码。

### 20.3 风险与验证

**主要风险**：
1. 误删被测试套件引用的 harness 模块 → 通过 grep 和测试套件验证
2. 误删被产品其他部分隐式依赖的代码 → 通过全仓导入分析
3. 遗漏清理某些旧代码引用 → 通过测试套件和启动验证
4. **测试套件依赖**：17+ 个测试文件导入旧 harness 模块（`test_run_kernel.py`, `test_harness_bootstrap.py`, `test_workflow_driver.py` 等），删除后这些测试会失败，需评估迁移到 SDK 测试或删除

**验证策略**：
- 所有删除前先 grep 确认引用情况
- 处理 `main.py` 中的 3 处 `root_run_identity` 导入（确认 SDK 等价功能或内联实现）
- 评估测试策略：迁移到 SDK 测试 vs 删除旧 harness 测试
- 删除后运行完整 pytest 套件
- 删除后手工测试所有 ingress（text/voice/background）
- 确认应用能正常启动并处理用户请求

**回退策略**：
- 所有改动可通过 `git revert` 立即回退
- 不涉及数据迁移，数据完整性不受影响
- 不影响 SDK Runtime 或产品 adapters

详细的依赖图、边界、证据和切换历史见 [`SDK_EXTRACTION.md`](SDK_EXTRACTION.md)。
