# TaskGraph 来源与 Host 接线审查

日期：2026-09-21。只读准备；不是合同等价、H1 READY、SDK 42场景或Host验收结论。SDK基于冻结 fingerprint `7b2281542b13127848164a45cc41e8979bf564b280f22b6db5843d4491de0d6e`；Host是当前目录的只读观察，最终接入须重新核对。

## P02/P09/P19（gpt-5.6-terra 独立静态审查）

下列SDK路径相对冻结SDK `src/agent_orchestrator/`。

| 来源 | 实际源码 | 已读事实 | 未闭合 |
|---|---|---|---|
| P02 | storage/planning_decision_store.py:254，PlanningDecisionStore.get_planning_request | request_id→PlanningRequestBinding或None；binding包含mission/package/hash/base/requirements/scope/visible refs/prompt/intent；governance/planning_authorization.py:114,159接入并把缺失转SourceUnavailable | 原get_request名不存在，typed binding消费者合同仍需行为及独立等价证明 |
| P09 | runtime/planning_operations.py:476,543，StoreOperationReader.read_running_work及同名module wrapper | 同read_view按mission读live attempts/intents和HANDED_OFF/UNKNOWN actions，保留lease/json；读取失败不当空 | 尚未证明MethodInstance→occurrence join及完整imported receipt覆盖；不能签完整producer |
| P19 | orchestrator/planning_admission_commits.py:46,80,167，PlanningAdmissionCommitsMixin.commit_planning_revision | typed admission固定request/decision/authority/operation/runtime/compilation/read-set；guard委托原commit_plan_revision，在其事务内重读 | 不因名称变化判不等价；但C01–C10未全行为闭合，仍NOT_PROVEN |

真实已读调用链：`CommitService` MRO（commit_service.py:360）→`HierarchicalDispatch.commit_preview_plan_proposal`（hierarchical_dispatch.py:3980,4023）→`commit_planning_revision`→`commit_plan_revision(...precommit_guard=guard)`（plan_commits.py:293）。存在缺新method的test-stub compatibility分支；它不自动证明生产旁路，也不构成C10全入口已覆盖。

C01 raw/PreAdmitted、C05交叉preview与NOT_CHECKED、C06单外层事务观测、C08四写边界与C10全route仍无完整证明。C02/03/04/07/09能在现有测试中找到相关候选，但没有运行或签PASS。所有实际nodeid须从最终源码收集并逐断言核对，不能按文件名映射为等价。

C-proof候选包括 test_h1h_authority_matrix、test_h1h_commit_guard、test_h1h_commit_interleaving、test_h1h_retired_running_work、test_h1i_production_entry。它们属于HTN原工作流；本任务未重复运行、修改这些文件。

## TG-E / V04 Host（gpt-5.6-luna 只读盘点）

| 层 | 当前真实入口 | 可复用/缺口 |
|---|---|---|
| transport | backend/main.py `/ws/control`（约13957）；编排分发约14708；backend/deskpet/orchestration/handlers.py | 复用现有消息transport；当前只有mission_list/get/events等，尚无TaskGraph读协议 |
| service/projection | backend/deskpet/orchestration/service.py list_missions:893、mission_detail:933、events:962；projection.py project_detail:603 | 当前读取可复用装配；没有TaskGraph revision/read_token/frontiers合同 |
| refresh | backend/deskpet/orchestration/pump.py MissionChangePump:61 | 当前只广播mission_id/status/last_seq，不能当图read token |
| feed/store | tauri-app/src/App.tsx:616；stores/useMissionsFeed.ts:31；stores/missionsStore.ts:110 | 常驻feed及seq/cursor机制可复用，当前选择/重连清detail，不满足未知错误保留stale图 |
| UI | tauri-app/src/views/MissionsView.tsx:391,583；components/Sidebar.tsx:91；WorkbenchShell.tsx:133 | 真实入口为“任务编排”→Mission；未来验证current/history、重连、权限切换及stale保留 |

`backend/deskpet/agent/task_graph.py`是旧goal_tasks DAG，与本计划immutable revision TaskGraphStore不同，不将其当已实现。

V04仍要求SDK TaskGraphReadApi和实际Host双侧证据：一个read_token贯穿view；历史planning/execution frontiers为空；未知错误保留合法画面并标stale；权限切换无旧token泄漏；UI不直接写图状态。未来必须运行实际Tauri并点击测试，组件测试不能代替。当前HOST_READY=BLOCKED。

## 全范围映射（不丢未完成验收）

| 原验收 | 工作包 | 后续实际执行 |
|---|---|---|
| S01–S08 | TG-A历史结构 / TG-B结构 | 当前7个迁移测试不填入S组 |
| D01–D06 | TG-B真实输入、ORDER/DATA | 未实施 |
| A01–A06 | TG-C准入/派发 | 未实施 |
| R01–R08 | TG-D收敛/共享/fence | 未实施 |
| C01–C08 | TG-A迁移及TG-C/D事务恢复 | 隔离迁移仅C06部分前置，不闭合C06/C07或其他C |
| V01–V06 | TG-E + 完整核心 | Host/实际模型/质量/规模保持未执行 |

TaskGraph 12 mutations全部保留；H1H另12 mutation不混为同一套。stateful 200×50、legacy/fullH1、4代表场景×3真实GPT-5.6 trial及实际Host均保留。当前无SDK 42case实际PASS，不能用102工具测试或7迁移测试凑数。
# 后继 C-proof 复核（2026-09-21，冻结源码）

本轮只读复核了 `test_h1h_commit_guard.py`、`test_h1h_commit_interleaving.py` 与 `test_h1h_request_binding.py`。没有运行完整H1-H套件，也没有修改候选。

六个现有过期/续期/交错授权、新增action/交错handoff和replay节点虽调用真实Store、授权/operation reader及Commit guard，但 `_setup` / `_admission_for_active_revision` 手工填充request的package/prompt/subject等占位hash，并直接构造PlanningCommitAdmission，未由真实H1 preview生产。它们只能作为guard级别诊断和未来测试oracle，不能登记为C03/C04/C07完整CONTRACT-PROOF PASS。初步代理建议已由主任务核源码纠正，未把建议写入actual_nodeids或测试成绩。

真实路径应从 `event_handler.py` 的 `new_mode.preview_plan_proposal(...)` 取得CandidatePreview，再生成PlanningCommitAdmission，经 `hierarchical_dispatch.py` 的typed preview/source校验进入 `commit_planning_revision`。request private seam中的`object.__new__`、SimpleNamespace与替换settle/rejection方法也不能替代此链。

C01–C10仍保持待真实producer证明及独立等价审查；此处的C编号属于V1.2.1 H1H commit-proof，与业务42场景的C组不混用。

## 2026-09-23：最终 HTN 来源重捕获与 TaskGraph 接线复核

HTN 已提供具名最终交接 `READY_FOR_TASKGRAPH_WIRING`。本轮在不修改 HTN 工作树和共享 Host 的前提下重新捕获最终 dirty SDK/Host：SDK 1404 个文件、Host 285 个文件；HTN wheel/manifest 的 528 个包文件与 SDK 源逐项匹配。最终 HTN SDK 与 TaskGraph WIP 三方合并无冲突；Host 按 12 个实际 TaskGraph 文件做精确合并无冲突。完成效果、OperationCompletion scope、运行中工作、计划提交、层级派发和 TaskGraph runtime/source 入口均已按最终 HTN API 做定向签名/导入/语法核对。

当前派生候选 15 的 package-owned `InstalledHtnWiringAcceptance._read()` 直接读回成功，部署 id 与 HTN wheel/manifest 身份一致，但 `taskgraph_acceptance=NOT_RUN`。因此来源身份门已核对，SOURCE_MAP_COMPLETE 的“捕获/解析”部分具备证据；行为覆盖、真实 H1 全门、TaskGraph TG-A 至 TG-E、真实模型及 Host/UI 验收仍保持 NOT_PROVEN/OPEN。Host `planning_authorization()` 现不自动启用 TaskGraph，显式认证 `enable_taskgraph_contract()` 仍是独立内部入口。
