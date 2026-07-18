<!-- plan-status: finalized; user-approved 2026-07-18; execution-authorized -->
# DeepResearch v6：答案语义、证据闭环与可靠交付实施计划

> 日期：2026-07-17  
> 状态：Q1 已通过；T11/T12 的 A2 定向修订已于 2026-07-18 完成 review 并获用户批准，恢复 Phase 3 实施。  
> 验收标准：[`acceptance.md`](./acceptance.md)  
> 架构基线：[`architecture-baseline.md`](./architecture-baseline.md)  
> 冻结契约：[`v6-contracts.md`](./v6-contracts.md)  
> 挑战记录：初始架构/计划 Round 1–7；A2 T11/T12 定向回炉 Round 1–13（最终专项 [`Round 10`](./plan-challenge-t11-t12-rework-round-10.md)、[`Round 11`](./plan-challenge-t11-t12-rework-round-11.md) 与集成 [`Round 13`](./plan-challenge-t11-t12-rework-round-13.md) 均 PASS）。

## 1. 目标和交付策略

### 主要矛盾

决定成败的核心问题不是搜索数量或报告样式，而是：**用户完成语义、证据事实、答案 assessment、用户可见 claim 与 terminal delivery 当前由多个可变 owner 分别解释**。只修其中一层会再次出现“engine completed 但没答案”或“报告存在但关键指标缺失”。因此任务顺序必须先冻结 canonical spec 和 evidence identity，再建设三道门与 terminal manifest；UI 只能消费最终投影，不能补造业务结果。

目标是在保留现有 durable workflow/checkpoint/outbox 底座的前提下，让 DeepResearch 对“必须回答什么、什么证据能支持、何时可声明完成、如何确保用户收到”拥有一条可恢复、可审计的单一语义链。

实施不直接修改 frozen `deep_research/v5`，而是新增 `deep_research/v6`。先以用户真实失败题 `SC-STATS-2` 做一个端到端垂直切片，快速确认架构和交付闭环；确认成功后，再扩展 comparison、Top-N、policy、open research 与 conditional fan-out。

```text
第一目标（快速确认）
SC-STATS-2 through real v6 graph
  -> official evidence
  -> two scalar requirements complete
  -> one final assistant in original Session
  -> restart/replay remains exactly once

完整目标
scalar + matrix + collection + claim-set
  -> completed / partial / insufficient
  -> durable receipts + honest UI/history
```

## 2. 不可变架构决策

1. **版本冻结**：不修改 v5 graph、contracts、snapshot 或 policy identity；旧 run/continuation 永远按 v5 恢复。v6 在 T13 前为非默认开发 identity，完整实现后才冻结；冻结后的结构变化升 v7。
2. **一个 semantic owner**：`ResearchSpecV1` 是用户问题和完成条件的唯一 owner；`ResearchBrief`/`AnswerContract` 如保留，只能是同一 spec hash 的只读投影。
3. **requirement graph**：使用 scalar/matrix/collection/claim-set tagged union，不使用统一平铺 slots。
4. **route 与 spec 分离**：route 取决于 capability/budget/source health，可升级但不得改 spec。
5. **事实日志而非双账本**：`EvidenceFactBatchV1` 追加持久化，`AnswerAssessmentV1` 确定性派生；coverage 只是投影。
6. **三道有序门**：Answer Readiness 在 synth 前；Report Integrity 在 render 后；Delivery Commit 在内容持久化后。
7. **复用已有事务**：canonical blobs + terminal manifest 交给现有 `commit_frontier` 原子写 checkpoint/outbox/run terminal；不新建 research DB。
8. **两事实一视图**：引擎终态来自 `workflow_runs`，答案终态来自 immutable manifest，交付状态从 durable receipts 聚合。
9. **WebSocket 非 durable owner**：SessionDB 投影是 required durable target，WebSocket 仅做实时加速。
10. **测试阶段默认开启**：全部验收通过后默认新 root 使用 v6，不做 shadow/灰度；v5 仍服务旧 run。

### 2.1 一手标准调研与 DeskPet 适配

| 一手实践 | 标准结论 | DeskPet 适配决策 |
|---|---|---|
| [RFC 8785 JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785) | 内容寻址对象需要唯一、可重复的 JSON 字节表示 | 采用“严格 JSON 值 + key 排序 + 禁 NaN + UTF-8 hash”的确定性原则，但不在本轮引入新的通用 JCS 依赖；先扩展现有 `workflows.contracts.canonical_json()` 并用 golden/property tests 固定 Python 实现。若跨语言验证需要完整 JCS，再作为显式 schema revision 引入，不能偷偷改变既有 hash。 |
| [JSON Schema 2020-12](https://json-schema.org/draft/2020-12) | tagged union 可用 `oneOf` 和严格 required/additionalProperties 约束表达 | `ResearchSpecV1.requirements` 使用版本化 tagged union；服务端 dataclass/validator 是生产 owner，生成的 schema 用于 LLM structured output 与 fixture 校验，不让 LLM schema 成为第二语义 owner。 |
| [W3C PROV-DM](https://www.w3.org/TR/prov-dm/) | provenance 区分 entity、activity、derivation 与 collection | 采用 page/body、span、binding、fact batch 的身份与派生方向，但不照搬 RDF/PROV serialization；本地内容寻址 JSON 足以满足单机审计，避免新图数据库和本体复杂度。 |
| [OpenTelemetry Trace API](https://opentelemetry.io/docs/specs/otel/trace/api/) | span 具有 start/end、status、attributes、parent/child；子操作应使用 child span，名称避免高基数实例值 | 复用本地 `TraceStore`，不引入外部 collector。node 是 parent span；search provider/fetch transport/extractor 是 child span；URL/query 只保留不可逆 correlation id，原文不进 span/metrics。实时 duration 使用 monotonic clock，epoch start/end 用于排序；崩溃 reconcile 明确标记 wall-clock fallback。 |
| [SQLite transactional guarantees](https://www.sqlite.org/transactional.html) | 单数据库事务在进程/系统故障下保持原子性 | 继续复用 `commit_frontier` 的单 `workflow.db` 事务写 checkpoint/run/outbox；SessionDB 是跨库 at-least-once projection，以稳定 event identity + unique constraint 去重，不伪装成跨库事务。 |

这些标准只用于校验身份、追溯、时钟和事务边界；不引入 Agent-Reach、云端 tracing、第二套 research DB 或把 DeskPet 改造成通用分布式平台。

## 3. 预计代码边界

### 3.1 新增 v6 owners

| 固定文件 | 职责 |
|---|---|
| `backend/deskpet/workflows/definitions/v6/__init__.py` | 注册 v6 definition |
| `backend/deskpet/workflows/definitions/v6/deep_research.py` | v6 graph、edges、snapshot codec、manifest identity |
| `backend/deskpet/workflows/definitions/deep_research_v6_contracts.py` | ResearchSpec、requirements、route、evidence、assessment、claim、terminal manifest contracts |
| `backend/deskpet/workflows/definitions/deep_research_v6_compiler.py` | 用户约束提取、candidate merge、spec validation/hash |
| `backend/deskpet/workflows/definitions/deep_research_v6_evidence.py` | page/span/binding policy 与 fact batch |
| `backend/deskpet/workflows/definitions/deep_research_v6_assessment.py` | deterministic completion/conflict assessment 与 gates |
| `backend/deskpet/workflows/definitions/deep_research_v6_nodes.py` | graph handlers；只编排 typed owners |
| `backend/deskpet/workflows/definitions/deep_research_v6_report.py` | ClaimRecord、intent renderer、Report Integrity、soft score |
| `backend/deskpet/workflows/definitions/deep_research_v6_delivery.py` | canonical content persistence、terminal manifest、cardinality validation |
| `backend/deskpet/workflows/adapters/deep_research_v6_evidence_runtime.py` | source/body/span extraction与解析 adapter |
| `backend/deskpet/retrieval/telemetry.py` | retrieval 层只依赖的 runtime `RetrievalTimingPort`/scope protocol；不导入 workflow store |
| `backend/deskpet/retrieval/official_sources.py` | v6 jurisdiction/authority-role 官方源注册表、resolver 与 final-URL verifier；不复用 legacy source packs |
| `backend/deskpet/workflows/adapters/retrieval_timing.py` | `RetrievalTimingPort` 的 durable TraceStore 实现与 privacy-safe metrics/structlog mirror |
| `backend/deskpet/workflows/deadlines.py` | `DurableDeadlineV1` 的双时钟状态转换、恢复与 child lease 派生 |
| `backend/deskpet/workflows/runtime_adapters.py` | 唯一通用 `WorkflowRuntimeAdapterRegistry[(name,version)]`，覆盖 deep/code/PPT；DeepResearch capability/continuation 是 typed extension/view，不建第二张 map |
| `backend/deskpet/workflows/runtime_dependencies.py` | immutable `NativeRuntimeDependencies(public_terminal_registry, terminal_commit_registry)`，沿真实 bind/materialize 链注入 executable |
| `backend/deskpet/workflows/delivery.py` | typed `DeliveryAttemptResult`/`DeliveryDisposition` 与 legacy handler 归一化 |
| `backend/deskpet/workflows/adapters/canonical_content.py` | v6 SessionDB/artifact delivery 的只读 manifest/content resolver，验证 run ownership 后解引用 canonical blob |
| `backend/deskpet/memory/migrations/011_message_projection_visibility_v19.sql` | messages projection/context visibility、索引及 FTS trigger 的事务迁移 |

以上新增文件名、模块归属和 owner 边界在本 plan 定稿后冻结；实施中若发现路径不可导入或与现存模块循环依赖，必须先回到 plan challenge 修订，不允许在编码时临时改名、合并回 v5 或复制到 UI。

### 3.2 扩展现有基础设施

| 文件/模块 | 计划改动 |
|---|---|
| `backend/deskpet/workflows/bootstrap.py` | 注册 v6 definition；两阶段 prepare→register/seal→activate/recovery，未 seal 不恢复 |
| `backend/deskpet/workflows/runner.py`、`definition.py` | 沿真实 `materialize -> bind` 链透传 immutable runtime dependencies；v6 禁止 pre-bound executable 绕过注入 |
| `backend/deskpet/workflows/service.py`、`launcher.py` | 通过 version registry 创建 root/continuation，不再只认识 v5 |
| `backend/main.py` | v6 initial ports、结构化 delivery outcome、移除 DeepResearch 无正文占位 fallback |
| `backend/deskpet/workflows/native.py` | 验证/展开 terminal manifest；不自行补造业务正文 |
| `backend/deskpet/workflows/terminal_projection.py`、`progress.py` | 保留同步 public projector；新增独立 async `TerminalCommitProjectionRegistry` 注册 v6 terminal commit capability，保持 v5 投影不变 |
| `backend/deskpet/workflows/store/registered_blob_store.py` | 增加 same-run/checkpoint/continuation closure 的 owner/ref 验证，不新建并行 blob store |
| `backend/deskpet/workflows/store/checkpointer.py` | 在 `commit_frontier` 同一事务内完成 ref authorization、checkpoint promotion、intent materialization 与 staging cleanup |
| `backend/deskpet/workflows/store/schema.py`、`effects.py` | workflow schema v4：v6 logical effect attempts/head、delivery aggregate 字段、continuation head；v5 rows/behavior 不变 |
| `backend/deskpet/workflows/store/research_repository.py`、`retention.py` | v6 continuation single-head 事务、server-side inherited closure 与新 owner reachability |
| `backend/deskpet/workflows/outbox.py`、`service.py` | delivery handler 返回 delivered/discarded_fenced/retryable_failure |
| `backend/deskpet/workflows/trace/models.py`、`context.py`、`store.py`、`observer.py` | idempotent child spans、双时钟、first-terminal-wins、node/retrieval timing completion |
| `backend/deskpet/memory/session_db.py`、`migrator.py` | v19 `projection_kind/context_visibility` contract、迁移版本与 durable receipt 投影 |
| `backend/deskpet/memory/vector_worker.py`、`retriever.py`、`manager.py`、`summarizer.py`、`reflection.py`、`context_segment_store.py` | 所有即时/回填/FTS/vector/recency/salience/reflection/context/page-in 路径统一执行 visibility policy |
| `backend/agent/snapshot.py`、`backend/scripts/facts_backfill.py`、`backend/scripts/chunk_backfill.py`、`backend/deskpet/memory/eval/qaset.py` | snapshot pressure 与派生记忆/backfill/eval 默认只读 conversation；history/admin/test-only 例外显式 allowlist |
| `tauri-app/src/types/messages.ts`、`stores/sessionsStore.ts` | manifest/receipt-derived UI 状态、identity 去重、history hydrate |
| config 与入口 | 全部验收后将新 root 默认版本切到 v6；不改变旧 run 的 version |

### 3.3 典型请求的生产调用链（“解剖麻雀”）

以 `SC-STATS-2` 为固定样本，代码和测试必须沿同一条真实链路取证：

```text
backend.main::_start_deepresearch_graph
  -> WorkflowLauncher.launch / launch_existing_run
  -> WorkflowService.start_workflow
  -> WorkflowRunner.start / resume
  -> RegisteredWorkflow.materialize -> CompiledWorkflow.bind
  -> NativeWorkflowExecutable
  -> v6 compile_spec node
  -> WorkflowRuntimeAdapterRegistry 的 DeepResearch typed extension 提供 v6 context/effect identity
  -> RouteDecisionV1(official_source_search 或 explicit seed direct)
  -> main 的 research search/fetch ports
  -> SearchGateway._run_provider / FetchExtractService
  -> DurableRetrievalTimingPort -> TraceStore child spans
  -> RegisteredBlobStore(page/body/span/binding/fact batch)
  -> AnswerAssessmentV1 -> AnswerReadinessGate
  -> ClaimRecordV1 -> renderer -> ReportIntegrityGate
  -> canonical content refs -> TerminalDeliveryManifestV1 -> DeliveryCommitGate
  -> commit_frontier(checkpoint + run terminal + outbox intents)
  -> outbox handler -> SessionDB durable receipt/projection
  -> WebSocket best-effort notification -> sessionsStore hydrate/dedupe/render
```

任何自动化或手工证据若绕过其中一段（例如直接调用 renderer、手工注入 intent、WebSocket 直注或只读 report 文件）只能作为单元证据，不能证明 Q1/T13 端到端通过。

## 4. 执行波次与门禁

```text
Gate 0 失败基线与 v5 冻结
  T0
   |
Gate 1 v6 identity + canonical semantics
  T1 -> T2
         |
Gate 2 evidence truth + deterministic completion
  T3 -> T4 -> T5 -> T5A -> T6
                             |
Gate 3 快速垂直切片
  T7 -> T8 -> T9 -> T10 -> Q1(SC-STATS-2 real E2E)
                              |
Gate 4 完整 intent 和控制面
  T11 -> T12
          |
Gate 5 fault matrix、真 UI、默认开启、文档回写
  T13
```

- T0～T2 串行，先固定 version/spec owner。
- T3 的 fixture 准备可与 T2 后半并行，但 evidence contract 只能在 spec schema 冻结后合入。
- T5A 的 trace primitives 可在 T3/T4 期间独立实现，但 Search/Fetch 接线必须在 T5 route/request identity 冻结后合入。
- T7～T10 必须按 claim → integrity → persistence/manifest → delivery 顺序，禁止先改 UI 文案伪造闭环。
- Q1 是“快速确认修复方法”的阶段门禁，不代表完整 plan 已完成。用户已在 2026-07-17 明确授权按 `plan-test` 完整开发与测试：Q1 PASS 后继续 T11～T13；Q1 FAIL 则停止昂贵扩展，先修复垂直切片并复测。

## 5. 详细任务

### T0 — 锁定失败基线、dirty worktree 和 v5 identity

**映射**：AC-VERSION-01、AC-COMPAT-01、全部防假绿要求。

动作：

1. 用 `git status --short` 和 scoped diff 清单记录当前用户修改；只建立索引，不清理、不 reset。
2. 保存三条已知 run 的脱敏交叉证据：workflow run/state、report/ref、outbox/delivery、SessionDB messages。
3. 固化 v5 manifest/implementation/state schema hash golden test，验证旧 checkpoint 与 v5 continuation 仍可恢复。
4. 新增旧实现红测：
   - engine completed 但原 Session 无 final assistant；
   - 四指标缺一仍 completed；
   - 同页四事实只能拿到一个 best span；
   - AI disclaimer 被当 winning evidence；
   - finalize 手工注入 intents 才能通过；
   - fenced/no-client handler 被误记 delivered。
5. fixture 必须来自脱敏正文或人工构造的独立事实页面，不把 query/预期答案原样拼进 body。

必须测试：

- 保留 `backend/tests/test_workflow_deep_research_v5_contracts_identity.py` 原断言。
- 新建 `backend/tests/test_deep_research_v6_failure_baseline.py` 和 delivery receipt 红测。

退出门：红测稳定指向本计划缺陷；现有 v5 identity 全绿；没有生产代码变更。

### T1 — 建立 v6 definition 与版本适配注册表

**映射**：AC-VERSION-01/02、AC-CONT-01、AC-COMPAT-01。

动作：

1. 新增 `definitions/v6`，复制的是 graph skeleton/port contract，不复制或修改 v5 identity；`WorkflowRegistry` 继续唯一拥有 compiled graph/manifest/recovery，`TerminalProjectionRegistry` 继续唯一拥有 projector。
2. 在 `runtime_adapters.py` 定义唯一通用运行时 registry，替换 `WorkflowLauncher._adapters` 的裸 tuple dict但保留所有 workflow：
   - `WorkflowRuntimeAdapter(workflow_name,workflow_version,state_factory,context_factory,extensions)`；registry `register/get/require/seal`，duplicate/unknown/late-register fail closed；
   - `DeepResearchRuntimeExtension(capability_snapshot_factory,new_runs_enabled,retry_from_start,action_ids,continuation,terminal_commit_capability)` 与 `DeepResearchContinuationAdapter(decode_snapshot,build_child_payload)`；
   - `DeepResearchRuntimeView` 只是 registry 对 `workflow_name='deep_research'` extensions 的只读 typed view，提供 default/version/capability API，不保存第二份 entries；
   - deep_research v1～v6、code_complex/v1、ppt_pro/v1 全部注册到同一 registry。`WorkflowLauncher.register_adapter()` 可暂作 pre-seal delegate 兼容现有 caller，但 launcher 不持有 `_adapters`；所有 lookup 读 `service.runtime_adapters`。
3. 逐点替换现有版本硬编码，而不是新增 `if v6`：
   - `backend/main.py::_start_deepresearch_graph` 的 v4/v5 allowlist、factory dict 和 capability snapshot；
   - `WorkflowLauncher.retry_from_start()`/`launch_existing_run()` 的 v4/v5 allowlist；
   - `WorkflowService.execute_run_action()`、retry、continuation 中固定的 v5 `ResearchEvidenceSnapshot` decoder；
   - `ResearchRepository` control/continuation 门禁改为接收 registry 已解析的 `expected_workflow_version`，child version/manifest/implementation/schema 仍只从 parent row 复制，客户端不能指定升级；
   - `terminal_projection.py` 保留现有同步 public projector，并新增以 `(workflow_name, workflow_version, capability)` 为 key 的 `TerminalCommitProjectionRegistry`；v6 extension 声明 `terminal_commit` capability。bootstrap 构造 terminal registries 与 `NativeRuntimeDependencies`，依次经 `WorkflowRunner.__init__ -> RegisteredWorkflow.materialize(saver,runtime_dependencies) -> CompiledWorkflow.bind(checkpointer,runtime_dependencies) -> NativeWorkflowExecutable.__init__` 注入。native 按 compiled manifest name/version optional lookup，不导入 runtime adapter registry、不出现 `if version == v6`；v1～v5 lookup miss 保持原同步路径。
   - `runtime_dependencies` 对 `WorkflowRunner`/`materialize`/`bind` 均为 optional；省略时建立“现有 default public registry + empty terminal-commit registry”，保证直接 `CompiledWorkflow.bind()` 与现有测试无需改调用。`WorkflowExecutable` facade 原样包装已注入的 `NativeWorkflowExecutable`；非 native/pre-bound legacy executable 不强行注入，但 registry 注册 v6 native executable 时若绕过 bind 则 fail closed。
4. 新增独立 `_deep_v6_context_factory`。不得直接复用 `_deep_v5_context_factory` 中 `research-v5-*` node execution id、`workflow_version="v5"` 或 `DurableV5ControlPort`；v6 effect identity、control port、snapshot codec 均从已校验 v6 adapter 构造。v5 factory 源码和行为保持不变。
5. T1 同时执行 workflow schema v3→v4 基础迁移，后续 T5/T10/T12 只消费该 schema，避免任务顺序倒置：
   - 严格创建 `v6-contracts.md` §4/§4.1 的 `workflow_research_deadlines`、resource budgets/reservations、effect logical attempt columns/head/FK/index；旧 effect rows 全为 NULL且继续旧 API。
   - 严格执行附录 §8 的 `workflow_deliveries` 四列、partial unique index 与 aggregate index；旧 rows 默认非 required 且不进入 v6 aggregate。
   - 创建 T12 的 `workflow_research_continuation_heads` 及 `idx_workflow_research_continuation_heads_snapshot`。因为 v6 尚未发布，迁移若检测任何 pre-v4 `workflow_version='v6'` continuation 直接 fail closed，开发/Q1 DB 重建；不在 schema migrator 读取外部 blob root，也不自动挑选 backfill winner。
   - `_migrate_v3_to_v4()` 全事务；fresh DB 与 v1→v2→v3→v4、重跑、每条 DDL fault rollback 必测。v1～v5 persisted rows 与行为不变。
6. 生产 bootstrap 使用明确两阶段：`build_workflow_service(..., activate=False)` 先构造 runtime services/service/唯一空 adapter registry但不执行 retention/recovery；`main.py` 创建 launcher并把 deep v1～v6、code v1、PPT v1 的现有闭包全部注册；随后 `await service.activate_runtime(required_runtime_identities={...})` 校验显式产品清单与 DB 中全部 nonterminal identities均有 adapter、seal，再执行 retention与 runner/delivery recovery。任何 start/retry/recover 在 activate 前 fail closed。service-only 空 DB tests 可用默认 `activate=True,required_runtime_identities=()`；涉及 launcher/persisted recovery 的测试必须显式注册后 activate。v6 在 T13 前 extension `new_runs_enabled=False`，默认 config 仍为 v5。Q1/T13 的真实 UI入口严格按附录 §10 dev override，客户端 payload 无 version 字段。
7. T13 在全部 v6 callable/policy/schema 落地后生成独立 release identity fixture，再把 `config.toml`、`WorkflowsConfig.deep_research_version` 和 factory revision 从 5/v5 一次性升到 6/v6；显式 pin v5 不变。冻结后改变 hash 必须升 v7。

必须测试：

- `test_workflow_deep_research_v6_contracts_identity.py`
- registry unknown/duplicate version fail-closed
- v5 parent→v5 child、v6 parent→v6 child
- restart/recovery definition lookup
- v1..v5 persisted registration/recovery matrix；v5 identity fixture字节不变
- v5/v6 snapshot 交叉输入 fail closed；v5/v6 terminal projector 独立
- v6 effect/context identity 中不得出现 `research-v5-*` 或 `workflow_version=v5`
- config factory revision 5→6、显式 pin v5、持久化 run 不受当前 default 变化

退出门：v5 和当前开发 bundle 的 v6 可同时注册/恢复；任何旧 v5 hash 未变化；没有把开发 v6 宣称为冻结发布版。

### T2 — 定义并编译 canonical `ResearchSpecV1`

**映射**：AC-SPEC-01/02/03/04。

动作：

1. 严格实现 [`v6-contracts.md`](./v6-contracts.md) §1～§2 的 exact-key JSON、ID/hash、四种 tagged union、merge lattice 与稳定错误码；该附录是本任务的冻结输入，不由编码者补字段：
   - `ResearchSpecV1`；
   - `ScalarRequirement`；
   - `MatrixRequirement`；
   - `CollectionRequirement`；
   - `ClaimSetRequirement`。
2. 区分 `work_dimensions` 与 `requirements`：前者可拆查询，后者拥有完成语义。Matrix cell、Collection item、Claim IDs 均按附录从实际数据派生，不预造 mutable slots。
3. compiler 先确定性抽取用户明确年份、地域、主体、metric、N、comparison axes、source preference；LLM 只输出候选补充。
4. validator 合并候选并禁止删除/降级用户约束；unknown intent 回退 `ClaimSetRequirement`，不得回退成一个泛化 dimension。
5. spec 在 retrieval 前写 `RegisteredBlobStore`，checkpoint 只持有 `spec_ref/spec_hash`；compiler/completion/render policy payload 同样注册为 blobs，spec refs/hash 逐一验证。semantic hash 与完整 serialized blob ref 按附录 §1 分离，任何 projection 都回显同一 semantic hash。
6. 第一阶段实现 `official_exact_fact` compiler；SC-STATS-2 的两个 scalar key/time/definition/unit contract 按附录固定，但数值/答案页/query 模板不得进入生产 contract。全部 union schema 在 T2 冻结，其余 intent compiler 在 T11 只填充既有 shape，不能改变 v6 identity。

必须测试：

- roundtrip/property/canonical hash tests
- 中文、英文、paraphrase、缺失 LLM、非法 JSON fixtures
- SC-STATS-2/4 golden spec
- Top-N 为一个 collection requirement 而非 N 个预命名 slots
- continuation spec hash immutable

退出门：spec 是唯一完成语义 owner；没有并行可变 AnswerContract。

### T3 — 建立 page/span/binding 证据身份

**映射**：AC-EVID-01/02/03/04/05。

动作：

1. official search 先持久化附录 §3 `OfficialSearchResultV1/SearchCandidateV1/SourceLocatorV1`；完整 URL 只进入 owner-protected locator blob，不进入 trace/log。canonicalize 正文后存 body blob；`PageRecord` 引用 locator ref、body hash 和 source/admission metadata，恢复后仍能生成真实就近引用。
2. `EvidenceSpan` 使用 canonical UTF-8 body byte offsets `[start,end)`、excerpt hash 和 page part；offset 必须位于 code-point boundary 且可从 blob 重建原文。
3. `EvidenceBinding` 绑定 requirement 或 collection item，记录解析值、normalized value、unit、time、scope/definition、source family/tier、validator version、status/reasons。
4. exact facts 优先使用确定性数字/单位/年份/邻近定义解析；LLM 可提议复杂 spans，最终 admission 由 validator 决定。
5. disclosure 以重叠 span/region 为边界；导航、页脚、搜索列表、模板和 disclaimer 默认不可成为 answer-bearing span。
6. 一个 page 可产生多个 spans/bindings；candidate identity 不再包含 dimension 作为页面身份。

必须测试：

- 同页四指标、跨句定义、相同值不同单位、年末人口 vs 全年出生人口
- UTF-8 中文 offset roundtrip
- 标题命中/正文不命中、AI disclaimer overlap、转述与第一方原文、冲突值

退出门：从每个 winning binding 可追到唯一 body range；同页多事实无复制 owner。

### T4 — 追加 `EvidenceFactBatchV1` 和确定性 `AnswerAssessmentV1`

**映射**：AC-LEDGER-01/02、AC-SPEC-04、AC-CONT-01。

动作：

1. 在 `deep_research_v6_evidence_runtime.py` 新增 `DurableV6PageReadEffectAdapter`，不扩充 v5 `DurableResearchReadEffectAdapter` 的 policy/spec identity。official search 使用独立 logical effect 与附录 §3 canonical result；page fetch 使用 §6 contracts。attempt/head/deadline/resource 规则严格实现 §4/§4.1；固定 policy=`deep-research-v6-page-read-v1`，tool spec=`official-source-search-v1|page-fetch-v1`，stable call ID=`sha256(run|route|operation|target/page|ordinal)`。
2. 所有已执行并收敛的 search/page outcome（含 empty/blocked/timeout/business-cancel/failed）先写 exact result及依赖 blobs，再按附录 §4.1 重算 closure：search=result+candidate locators；page=result+input/final locator+body/独立 evidence refs。`commit_idempotent_read_attempt` 同事务为整个 closure 添加 effect owner、把 sorted bare refs写 `artifact_refs_json`、删除 staging并设置 logical canonical result。只有 not-started 无 result。node 在 patch 前崩溃时必须能完整解引用正/负 result 的 locator/body；terminal cleanup 不得破坏 closure。
3. 独立 `admit_evidence` node 只读取 logical head 的 canonical page result refs，按冻结 ordinal 串行校验并产生 content-addressed `EvidenceFactBatchV1(previous_head, admitted facts, reject reasons, provenance)`；一次 frontier checkpoint 原子保存 ordered batch refs/head hash，不保存可独立修改的 slot ledger。部分 page 失败时仍能从 effect journal 重建同一批可用输入。
4. 实现纯函数 `assess(spec, batches, policy_hash)`：按附录 §6 派生 exact semantic assessment hash/ID，生成 requirement/item results、conflicts、missing、minimum-useful coverage 和 proposed answer status。admission/assessment policy注册为 blobs；完整 assessment bytes另存 registered blob并保存独立 `assessment_ref/hash`，checkpoint cache 只能引用且双重校验。
5. checkpoint 可保存 assessment cache，但必须带 spec hash、evidence head 和 policy hash；不匹配时重算或 fail closed。
6. 若旧 UI/loop 需要 `DimensionCoverage`，只提供 readonly adapter projection。
7. 把 `spec_ref/head_hash/assessment input hash/policy hashes` 纳入 v6 snapshot codec 和恢复校验。

必须测试：

- batch order/head tamper/owner mismatch/blob missing fail-closed
- assessment deterministic/idempotent/property tests
- scalar completeness、matrix half coverage、collection unique/min-items、claim-set counterevidence
- cache invalidation/restart/continuation

退出门：事实只有一个 append-only owner，assessment 可由持久化输入完全重建。

### T5 — 独立 `RouteDecisionV1` 与真实 official source path

**映射**：AC-ROUTE-01/02/03/04。

动作：

1. route decision 记录 spec hash、policy hash、capability/source-health/budget snapshot、理由、work groups 和允许升级边；route policy 与 frozen decision 都注册为 canonical blobs，恢复按 ref/hash 读取。
2. official exact fact 使用 [`v6-contracts.md`](./v6-contracts.md) §3 的 `OfficialSourcePolicyV1` 与 search/locator contracts：resolver 只读 jurisdiction/authority-role/source-type，初始 `cn.nbs` 是通用机构注册项，不含年份、指标、答案 URL 或完整问题。显式 seed URL 才可 `official_direct_fetch`；否则进入真实 `official_source_search -> SearchGateway -> SourceLocator -> fetch -> final-URL verify`，不显示空 direct 假阶段。
3. source miss 只允许按冻结边升级 general search；已 journaled effects 不重复执行，spec 不变。v6 禁止调用 legacy `_source_pack_queries_for()`/`_SOURCE_PACK_RULES`，也不把 SC-STATS-2 query 特判复制进新 resolver。
4. route budget 统一 query/fetch/browser/LLM/lane 计数；简单事实默认 fan-out=false。
5. matrix/collection/open 的完整 route 和 conditional fan-out 留到 T11，但 contract 与重放规则本任务冻结。
6. v6 持久化 [`v6-contracts.md`](./v6-contracts.md) §4 `DurableDeadlineV1`，运行期再创建本地 monotonic lease。恢复时离线时间计入余额；小幅 wall 回退 clamp，大于 2 秒 fail-closed；先以 run fence/version CAS 持久化恢复状态，胜者才能发 upstream。健康真实网络 120 秒是端到端 budget/wall guard，不是末端粗暴 `wait_for(120)`。`official_exact_fact` query≤6、fetch≤8、并发≤4；page child≤20 秒，其中 robots≤3 秒、静态 transport 单次≤12 秒，其余 transport 只用同一 child 剩余预算。
7. Scrapling/HTTPX/browser/Jina 的库内 retry 必须显式关闭或计入同一 page deadline；同一 transport 最多 1 次真实 upstream attempt，失败由 route policy 决定是否换 transport。禁止“20 秒参数 × 隐藏多次 retry”形成 60 秒以上黑盒等待。
8. `DurableV6PageReadEffectAdapter` 以 logical page attempt 为单位使用 bounded task group（上限 4）；official search 是独立 logical read，单页的 robots/static/httpx/browser/Edge/Jina 是同一 page attempt/deadline 下的 stage，不各建会重置预算的 effect。每页完成立即持久化 `PageExtractionResultV1` 并完成 effect transaction，随后由独立 admission node 按 ordinal 生成 chained fact batches并 checkpoint。
9. adapter 只以 `GenerateNowFence`/`PageDeadlineExpired` typed signals 表示业务收敛，fetch node 按 `v6-contracts.md` §6 转 exact `PageAttemptOutcomeV1[]` partial patch；外层 `asyncio.CancelledError` 永远传播。TaskGroup completion snapshot、not-started 补齐、control observed→settled 顺序按附录执行；恢复从 journal 重建字节相同 outcomes，不旁路 runner 写 checkpoint。

必须测试：

- official hit、direct miss→search、source health degraded、budget exhausted
- official resolver paraphrase/no-org-name/non-CN、registry forbidden-oracle scan、evil suffix/cross-domain redirect、SearchGateway directive spy、legacy source-pack non-call
- recovery route replay same decision
- SC-STATS-2 无 fan-out，query≤6/fetch≤8、fetch concurrency≤4
- 注入慢 robots/static/httpx/browser 和隐藏 retry fake，断言所有子阶段共享一个 page deadline、未进入 fallback 不产生调用
- 8 页中部分成功、部分 timeout/cancel：已完成 page effect refs 可在恢复时复用，admission 生成相同 ordered fact head，assessment 可重建且 generate-now 可在 settle target 内收敛
- offline/wall rollback/forward jump、双恢复 fence CAS、uncertain read 保守消费 reservation 后 attempt+1、opaque effect no replay、late worker orphan、head 永不指向不存在 attempt
- blob closure 写入+effect commit 后、node patch 前崩溃；重启完整解引用 result/locator/body；terminal cleanup 后所有 refs 仍可读；closure 缺一/夹带均 rollback
- generate-now 业务 fence 返回 typed partial patch 并进入 admission；真实 runner shutdown cancellation 继续传播

退出门：UI progress 每个 route stage 都对应真实动作；direct miss 不修改完成规范。

### T5A — Durable retrieval child spans 与全阶段耗时

**映射**：AC-OBS-01/02/03/04，并为非功能性能阈值提供事实源。

架构边界：retrieval 层不得反向依赖 workflow store。新增 `retrieval.telemetry.RetrievalTimingPort` runtime protocol；`workflows.adapters.retrieval_timing.DurableRetrievalTimingPort` 才依赖 `TraceStore`。`SearchRequest` / `FetchRequest` 只携带 `compare=False/repr=False` 的 runtime port 和 deterministic `request_id`，公开 response/state 不序列化 port。

动作：

1. **Trace primitive**：
   - `SpanKind` 增加 `RETRIEVAL`；新增 `SpanHandle`/`SpanCompletion` typed result。
   - `TraceStore` 注入 `wall_clock=time.time` 与 `monotonic_ns=time.perf_counter_ns`。wall start/end 只用于时间轴，正常 `duration_ms=(end_ns-start_ns)/1_000_000.0`，不再 `int`/整数 round；crash reconcile 才使用 wall endpoints，并写 `duration_source=wall_reconciled`。
   - child `span_id=sha256(parent_span_id|operation_kind|request_id|stage|provider_or_ordinal)`；`INSERT OR IGNORE` 后读回 canonical row。finish 使用 `WHERE status='running' ... RETURNING` first-terminal-wins；重复 finish 返回 `updated=False`，不覆盖原结果、不重复镜像。
2. **Node lifecycle hardening**：`record_node_start` 不重开 terminal attempt；`record_node_finish` 只允许首次 terminal transition。`WorkflowExecutionObserver` 保存 execution/span handle/monotonic start，并从 `TraceStore.finish_span()` 的同一 `SpanCompletion` 生成 `deepresearch_stage_timing`，覆盖 succeeded_pending/waiting/retryable/failed/cancelled。reconcile 覆盖所有终态，wall fallback 单独标记。
3. **Search attempt spans**：`SearchGateway._run_provider()` 成为唯一 wrapper，覆盖 limiter queue、unavailable、cooldown、budget、真实 upstream、hit/empty/timeout/error/cancel/rescue；散落 `_attempt_metric` 不再自行读钟，改为只镜像 durable completion。取消路径 `shield(finish(cancelled))` 后原样重抛。
4. **Fetch stage spans**：在 `FetchExtractService` 精确创建 `fetch.total`、`fetch.url_lock`、`fetch.robots`、`fetch.throttle`、`fetch.static`(Scrapling)、`fetch.httpx`、每次 `fetch.extract`、`fetch.playwright`、`fetch.edge`、`fetch.jina`。cache hit、lock wait、blocked、budget denied、empty、rejected、timeout、cancelled 都有真实 outcome；未进入的 fallback 不伪造 0ms span。
5. **Correlation**：v6 adapter 用 durable effect identity + task/attempt + ordinal 生成稳定 request id；span/metrics 只带 run/request/attempt/stage/provider/fetcher/extractor/outcome/error code/http status/count 等 typed 低基数字段。禁止 query、URL、title、body、prompt、cookie、token、`str(exc)`。
6. **镜像顺序**：先提交 durable span completion，再用同一 float duration 和 typed detail 写 structlog/`metrics.jsonl`；镜像 `try/except` 非致命。没有 durable port 的 quick-search/legacy 调用保持当前 metrics 行为，不让本任务改变 v5 definition identity。
7. **Schema与保留**：复用现有 `trace_spans` 字段，不新增 duration 列或 timing 专用索引，本任务不迁移 trace schema。diagnostic bundle 继续包含 metrics，trace 通过现有 workflow trace API 查询。

必须测试：

- `test_workflow_trace_store.py`：sub-ms float、双时钟、deterministic id、parent tree、CAS、duplicate/conflicting finish、wall reconcile。
- `test_deepresearch_stage_timing.py`：所有 node outcome、metrics/trace 同值、重复 finish 唯一、telemetry failure non-fatal。
- Search provider status/limiter suites：hit/empty/timeout/error/unavailable/cooldown/budget/queue/cancel/rescue child spans。
- `test_fetch_extract_service.py`：total/lock/robots/throttle/static/httpx/extract/playwright/edge/jina、cache/fallback/error/cancel；`to_thread` 不丢 parent correlation。
- 新增 `test_deepresearch_durable_child_timing.py`：v6 adapter→search/fetch→SQLite trace 集成；断言 `run_id + parent_span_id + request_id + stage` 可聚合，forbidden raw fields 不进入 trace/metrics/log。

退出门：节点和所有实际进入的检索/抓取子阶段都能从 durable trace 得到非伪造时长与 outcome；metrics/log 与 completion 同值且失败不影响 workflow。

### T6 — `AnswerReadinessGate` 与 requirement-driven gap loop

**映射**：AC-GATE-01、AC-QUALITY-01、AC-CONT-03。

动作：

1. synth 前只读取 spec + assessment；检查 required coverage、first-party、unit/time/scope、hard conflict 和 minimum-useful policy。
2. 结果为 `ready_completed_candidate / ready_partial_candidate / needs_evidence / insufficient`，不得在此检查 report/outbox refs。
3. gap planner 只为 missing/conflicted requirements 建 query targets，受 RouteDecision 和剩余预算限制。
4. generate-now 停止新 gap work 后重新 assessment；0 admitted evidence 进入 insufficient safe-summary 路径。
5. loop convergence 使用 evidence head + missing set + budget；相同 head/missing set 无新 action 时终止，防止无效循环。

必须测试：

- SC-STATS-4 缺一项、第一方缺失、unit mismatch、unresolved conflict
- generate-now at zero/partial/full evidence
- no-progress loop termination

退出门：任何 soft score、renderer 或 progress 文案都不能声明 completed。

### T7 — 唯一 `ClaimRecordV1` 和 exact-fact renderer

**映射**：AC-REPORT-01/02/03。

动作：

1. synthesis packet 只能消费 ready assessment 和 admitted bindings，生成 typed claim candidates。
2. deterministic validator 注册 `ClaimRecordV1`：normalized proposition、claim kind、binding ids、support status、visibility；claim policy 注册为 canonical blob。
3. exact-fact renderer 首屏按 scalar requirements 顺序显示 value/unit/time/scope 和就近 citation；缺口明确显示。
4. extractive fallback 只从 ClaimRecord→Binding→Span 重建句子；不保留未经重新验证的原 claim kind。
5. insufficient renderer 输出安全摘要、已知限制和可执行继续动作，不生成报告 artifact。

必须测试：

- claim/binding semantic mismatch、unsupported inference、omitted claim
- exact-fact golden snapshots 和 citation proximity
- 标题/导航/disclaimer 永不出现在关键结论

退出门：每个用户可见 claim 均能追溯 admitted bindings；renderer 无独立 claim ledger。

### T8 — `ReportIntegrityGate`、repair 和软质量分

**映射**：AC-GATE-02、AC-QUALITY-01、AC-REPORT-01/02/03。

动作：

1. render 后解析用户可见 claims，验证每个 claim id、binding support、citation placement 和 assessment consistency。
2. hard failure 只能：定向 repair、删除 unsupported claim、completed→partial/insufficient 降级；不可用软分覆盖。
3. hard integrity 通过后才计算 readability、source diversity、analysis depth、counterevidence、uncertainty 表达等 soft score。
4. repair 有次数/预算上限；每次输出新的 immutable claim/report candidate，而非原地篡改已 terminal 内容。
5. quality policy 注册为 canonical blob；quality audit 作为 canonical blob 持久化前的 typed payload，不先发逻辑 ref。

必须测试：

- quality=80 但缺 requirement 必须 hard fail
- dangling citation、同 dimension 不同 claim、repair 后仍 unsupported
- completed/partial/insufficient downgrade matrix

退出门：SC-STATS-2 只有两个 scalar claims 都绑定正确证据才能继续持久化。

### T9 — canonical content persistence 与 `TerminalDeliveryManifestV1`

**映射**：AC-PERSIST-01、AC-MANIFEST-01/02、AC-GATE-03。

动作：

1. report/final assistant/safe summary/claim batch/quality audit 全部先写 `RegisteredBlobStore` 并验证 run ownership/readability。授权条件只能是当前 run 的 `run_staging`、当前 expected head 精确 frontier 的 `pending_task`，或由 `workflow_checkpoint_owners` 证明属于当前 run 的历史 `checkpoint`；“digest 全局存在”不等于授权。
2. T9 不写 artifact 文件。`render_profile.artifact_required=true` 的 completed/partial 只声明 artifact intent/cardinality=1；T10 required artifact delivery 才从 canonical report blob 按附录 §8 确定性 materialize `ArtifactProjectionV1`。false/insufficient_evidence 不声明 artifact intent；文件 hash/path 不做业务 owner。
3. 先组装并注册附录 §6.1 `ResearchContinuationSnapshotV1`（spec、ordered batches/head、assessment、claim/provenance/policies exact closure），再组装 §7 exact `TerminalDeliveryManifestV1` 并引用 snapshot。每个 logical intent 明确列 `delivery_specs[]`（channel/target role/required/projection/visibility），materializer 禁止临场扩展 channel；content refs/cardinality/engine tuple 全部参与 manifest hash，禁止内联用户正文。
4. completed/partial 与内部 assessment `insufficient` 使用同一个 manifest builder/finalize path；后者唯一映射为 manifest `answer_status=insufficient_evidence`，且不产生 report artifact。manifest 注册后 finalize patch 只写 `state.values.terminal_manifest_ref/hash` exact pointers 并把 ref 纳入 state blob refs，不写 inline manifest/intents/public。
5. DeliveryCommitGate 验证 canonical closure、refs/hash/owner、status/cardinality 和 stable identities；completed/partial 必须 1 final assistant、1 canonical report、artifact 按 render profile 为 0/1，insufficient_evidence 必须 1 safe final assistant 且 report/artifact=0，所有状态 run_terminal=1；禁止 `summary:<run_id>` 等无 resolver ref。

必须测试：

- missing/corrupt/wrong-owner blob、snapshot closure夹带/缺失、insufficient 带 artifact
- semantic hash vs registered blob ref、wire ref↔bare digest strict roundtrip、manifest identity-seed/full-byte hash、restart idempotence

退出门：manifest 中每个 ref 可读，每个 intent 基数合法，尚未依赖 SessionDB/WS 成功。

### T10 — native atomic commit、receipt 语义、SessionDB/UI 投影

**映射**：AC-COMMIT-01、AC-DELIVERY-01/02/03、AC-CONTEXT-01、AC-MANIFEST-02。

动作：

1. 严格实现 `v6-contracts.md` §1/§7 `TerminalCommitRequestV1/ProjectionV1` 与 wire-ref boundary。native 先计算唯一 engine tuple；v6 async projector 路径跳过旧 `_terminal_projection/_terminal_intents`，验证 manifest engine tuple，把 projection wire refs strict parse 为 bare digest 后与 `_state_blob_refs` 去重合并，并只提交 projection intents。v1～v5 lookup miss 才走旧同步路径。
2. async projector 以 manifest 派生 sanitized state 调用**同一个**同步 `TerminalProjectionRegistry.project()` 一次，生成附录 §7 exact bounded `V6TerminalPublicV1`，只嵌入 workflow-final-status projection intent payload并随 outbox event持久化；不写 checkpoint terminal_public。native 在 projection 后才计算含 projection hash 的 `frontier_operation`。缺失/非法/engine tuple或public不一致均 fail closed，不补造正文。
3. `checkpointer.commit_frontier()` 在现有 `BEGIN IMMEDIATE` 中先 `_validate_and_promote_blob_refs`：逐 ref 区分 `blob_not_found/blob_owner_mismatch`，全部通过才插入 checkpoint owner、materialize intents、更新 run terminal，并删除本 frontier exact pending 与已提升 staging owners；任一失败整体 rollback。`effect` owner 不在这里删除；`ensure_genesis` 只允许 child same-run staging并提升 inherited closure；terminal failure 只清 dangling pending/staging，CAS 文件仍由 orphan grace 清理。
4. `delivery.py` 定义 typed result；v6 严格执行附录 §8 begin/due/expired-claim CAS：max attempts=5、无 jitter 1/2/4/8 秒、terminal failed 永不自动 reclaim。delivered→ack，fenced→discard，retryable/exception/非法→retry wait；legacy `None` 只允许列出的 v1～v5 handler；CancelledError 尽力结算后重抛。
5. `_materialize_intent()` 逐条物化 manifest `delivery_specs[]`，不得使用当前隐式 channel expansion；写 `intent_id/manifest_ref/required_durable`。Outbox 注入 `ManifestIntentReader`，按 `v6-contracts.md` §8 唯一归约 aggregate。`WorkflowService.run_detail()`、`hydrate_session_history_event_ids()` 与每次 delivery CAS notification 都附同一 aggregate DTO；IPC/backend-shaped event/前端 types/store 只消费该 DTO，前端不拼零散 rows。
6. `build_workflow_service()` 先构造 `RegisteredBlobStore + CanonicalContentResolver + ManifestIntentReader + registries` 为 immutable `WorkflowRuntimeServices`，再调用可选 `delivery_handler_factory(runtime_services)` 创建 main 捕获的 SessionDB/WS/Product handlers，最后构造 service/outbox/runner；现有 `delivery_handlers` 参数仅保留 legacy tests/v1～v5。resolver 严格实现附录 §8，Session text strict UTF-8 roundtrip；artifact handler 确定性生成/验证 `ArtifactProjectionV1` 再调用现有 ProductDelivery；outbox不内联全文，任何 ref/owner/media mismatch 不产生 placeholder。
7. state DB 升 v19：严格使用附录 §9 projection enum、legacy default 与 event/role/visibility/skip_embed mapping；两列带列级 CHECK，新增三个 visibility 索引。`migrator.py` 增加 `_V19_MIGRATION/_V19_SCHEMA_VERSION=19`、与 v17/v18 同类的 `_execute_transactional_script` special branch、durable marker→version 优先级和 main migration log=19；SQL 文件不自写 BEGIN/COMMIT/user_version，DDL、trigger、marker、user_version 由 migrator 同一事务持有。
8. v19 drop/recreate `messages_ai/messages_ad/messages_au`：仅 conversation 写 FTS，update 覆盖 include↔exclude；`search_fts` 再过滤。`SessionDB.append_message/append_message_if_epoch/_insert_message_row` 持久化两字段，exclude 强制 `skip_embed=True`；同一 event ID 分类冲突 fail closed。
9. 分类固定为附录 §9 exact enum/table；未知 `workflow.*` fail closed，不再使用不可执行的 `workflow_*` 通配枚举。严格执行 reader allowlist：recent/tool-boundary、Context OS、summarizer、MemoryManager、Reflection、`agent/snapshot.py` pressure、facts/chunk backfill、qaset-default 都只读 conversation；history/admin 是显式例外。repo-wide SQL audit test 阻止新增未分类 `FROM messages` reader。
10. Retriever FTS/recency/salience/vector 与 final metadata 均过滤；VectorWorker backfill 只扫描 conversation，write 前二次校验，stale excluded vec 不召回。
11. 前端从 engine terminal、answer manifest projection 和 aggregate 生成文案；按 event/intent 去重，迟到 progress 不覆盖 final。final assistant cardinality 只统计 `projection_kind=final_assistant`。

必须测试：

- exact terminal request/projection、engine tuple/public/ref merge 冲突 fail closed；commit 后 push 前崩溃、重复 claim、outbox replay、SessionDB unique idempotency
- epoch mismatch 后旧 run 永久 fenced/不 rebind、新 epoch 独立 run成功；无 WS client、WS failure、SessionDB transient failure、begin 后崩溃/claim expiry结算、attempt 5 terminal row不再 due、pending→queued DTO
- delivery factory 构造顺序/同一 blob store identity；canonical blob→SessionDB UTF-8 字节一致；artifact deterministic path/hash/ProductDelivery replay；missing/corrupt/wrong-owner 无 placeholder；aggregate precedence、max-attempt/backoff、run detail/history/CAS notification exposure
- fresh/v18→v19/重跑/每条 DDL/trigger/marker/user_version fault rollback；25～29 条 progress 不进入 recent/FTS/vector/facts/chunk backfill/Context OS/summarizer/reflection/snapshot pressure/qaset-default，但旧 Session history仍可加载；repo SQL audit allowlist
- backend-shaped event→reducer，而非手工理想对象

退出门：terminal crash/replay 后原 Session 仍只有一条 final assistant；delivery view 可由 receipts 重建。

### Q1 — 快速确认垂直切片

这是用户要求的快速确认门，不执行后续大范围优化即可先判断方法是否成立。

必须同时满足：

1. 真实启动 `deep_research/v6`，没有 query 特判或生产硬编码 oracle。
   启动必须是源码 Tauri + 附录 §10 dev override + 独立 user-data，由真实用户输入触发 `_start_deepresearch_graph`，不能直接调用 launcher 冒充 UI ingress。
2. SC-STATS-2 编译为两个 `ScalarRequirement`。
3. 国家统计局正文产生两个独立 bindings；AnswerAssessment 两项 supported。
4. 三道门全部通过，manifest refs 均可解析。
5. 原 Session 显示一条 final assistant：140828 万人、954 万人、各自语义/单位/就近引用。
6. 断开 WebSocket、重启并恢复历史后仍唯一；workflow engine/answer/delivery 三个视角不混淆。
7. v5 identity/recovery 回归仍绿。
8. trace 中能沿 run→node→search/fetch child span 重建本次全阶段耗时；实际进入的阶段 duration 非伪造，metrics 与 durable completion 同值。真实网络总耗时以 ≤120 秒为目标，若超出则 Q1 判 FAIL 并用 child spans 指明长尾阶段，不以扩大总 timeout 通过。

Q1 使用独立 `DESKPET_USER_DATA_DIR`/workflow DB；恢复证据限定在同一个 v6 source bundle。T11/T12 仍会改变开发中的 implementation hash，因此直到 T13 冻结前都不承诺跨 build 恢复，也不把未发布 v6 run 写入真实用户库。

若 Q1 失败，停止扩展 intent，按失败层定位 spec/evidence/assessment/manifest/receipt；不得用 UI fallback 或 fixture 注入绕过。

### T11 — 扩展 matrix、collection、claim-set 和 conditional fan-out

**映射**：AC-SPEC-03、AC-ROUTE-04、AC-REPORT-02 及 SC-COMPARE/TOPN/POLICY。

动作：

1. 完成 comparison compiler/renderer：subject×axis cells 独立 assessment，推荐区分 fact/inference/preference。
2. 完成 Top-N collection：动态 candidate items、unique key、统一 ranking rule/as-of、min_items=N；不足为 partial。
3. 完成 policy claim set：issuer/document/date/commitment facts 与 impact inference 分离。
4. 完成 open research claim set：conclusion/limitation/counterevidence/uncertainty 最低覆盖。
5. 只有独立 work groups≥3、merge key 明确且预算允许时启用 fan-out；lane 只写 canonical page/candidate result blob，主 graph 按附录 §6.2 稳定排序后唯一追加 `EvidenceFactBatchV1`，避免多个 lane 竞争 chained head。
6. 按附录 §6.2 完成 production graph 接线，不把已通过的纯 compiler/assessment/renderer/runtime helper 当作 T11 完成：
   - 新增 `EvidenceCandidateBundleV1` tagged union、real UTF-8 byte span 校验、一次 structured repair；
   - 把 `DurableResearchCallEffectAdapter` 的 effect identity 参数化，v5 default prepared-call bytes/hash golden 不变，v6 用独立 profile 和 budget；
   - 持久化 `AdmittedResearchFactV1` / `RegisteredInferenceV1`，`GenericAdmittedFactV1` 只作解码 DTO；
   - production definition使用附录§6.2的11节点：page-local facts先durable admit/checkpoint，再做global inference synthesis/register/checkpoint；全链消费route/policy/fact/inference registered refs，Q1 exact通过deterministic candidate producer迁移到同一generic core。

必须测试：各 intent golden spec、deterministic/LLM producer、candidate tagged-union/hash/real-byte-span、malformed→单次 repair→仍 malformed fail、provider return→raw put→effect owner→outcome commit→checkpoint fault matrix、跨页 fact-head→inference restart replay、v5 effect identity golden及v6 extract/repair/inference prepared goldens、registered fact/inference→assessment DTO restart byte equality、generic integrity/persistence、assessment matrix、renderer snapshots、lane deterministic merge。另设不可被helper替代的production gate：bootstrap/version registry + real `WorkflowRunner`，initial input仅topic/session，fake upstream但真实durable fetch/LLM journal/blob/deadline/budget；official exact/comparison/Top-N/policy/open各一条。exact设置LLM fail-on-call且budget=0，仍必须产出deterministic producer、唯一release batch并走共用admit/assess/render/integrity/persist。分别在raw result、candidate bundle、fact head、inference bundle、inference head commit后crash/restart，断言不重发、head唯一、snapshot closure完整、旧Q1 persistence未调用。definition manifest必须含§6.2 11节点、五类intent、三LLM profile/route/budget ports。

退出门：四类 requirement 都由 production graph 产生 durable candidate/fact/inference closure，无进程内或 exact-only 旁路；四类 intent 复用同一 assessment/integrity/manifest owners。

### T12 — continuation head、控制命令和兼容恢复

**映射**：AC-CONT-01/02/03、AC-COMPAT-01。

动作：

1. v6 continuation 继承附录 §6.1 exact `ResearchContinuationSnapshotV1` 的 spec ref/hash、ordered fact refs/head、assessment/claim/provenance/policy closure；新的 evidence 仅追加。service 只能从 server-side 已校验 terminal manifest 取得 snapshot ref，再验证 snapshot closure 与 parent checkpoint owners；客户端不能提交 manifest/snapshot/lineage/blob refs。
2. 使用 T1 已升级到 v4 的专属 owner 表，避免给含历史 sibling 的 v5 lineage 表加全局 UNIQUE：
   ```sql
   CREATE TABLE workflow_research_continuation_heads (
     parent_run_id TEXT PRIMARY KEY,
     child_run_id TEXT NOT NULL UNIQUE,
     parent_operation_id TEXT NOT NULL,
     child_operation_id TEXT NOT NULL UNIQUE,
     source_snapshot_hash TEXT NOT NULL,
     spec_hash TEXT NOT NULL,
     spec_blob_digest TEXT NOT NULL,
     evidence_head_hash TEXT NOT NULL,
     policy_version INTEGER NOT NULL CHECK (policy_version=1),
     claimed_at REAL NOT NULL,
     CHECK(parent_run_id <> child_run_id),
     FOREIGN KEY(parent_run_id) REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
     FOREIGN KEY(child_run_id) REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
     FOREIGN KEY(parent_operation_id) REFERENCES workflow_research_lineage(operation_id) ON DELETE RESTRICT,
     FOREIGN KEY(child_operation_id) REFERENCES workflow_research_lineage(operation_id) ON DELETE RESTRICT,
     FOREIGN KEY(source_snapshot_hash) REFERENCES workflow_research_snapshots(snapshot_hash) ON DELETE RESTRICT,
     FOREIGN KEY(spec_blob_digest) REFERENCES workflow_blobs(sha256) ON DELETE RESTRICT,
     CHECK(length(spec_hash)=64 AND spec_hash NOT GLOB '*[^0-9a-f]*'),
     CHECK(length(spec_blob_digest)=64 AND spec_blob_digest NOT GLOB '*[^0-9a-f]*')
   );
   CREATE INDEX idx_workflow_research_continuation_heads_snapshot
     ON workflow_research_continuation_heads(source_snapshot_hash,parent_run_id);
   ```
   service/repository 唯一调用 `create_or_get_continuation_v6(parent_run_id,caller_idempotency_key)`，禁止先走旧 resolver；同一 `BEGIN IMMEDIATE` 先查 head，已存在只验证 canonical child链并返回，不再要求 parent pin有效。不存在才校验 parent v6 terminal event/manifest/hash/snapshot pin/expiry与 inherited closure；child UUID/request/logical slot/start operation/payload严格按附录 §6.3，caller key只生成同事务 durable audit operation。同事务按十一个 write points写 child capability/run/audit/start/session refs/lineage/pin/operation/inherited owners/head。service 对 created/existing 都从 persisted start payload notify/launch canonical child；notify前崩溃由 created-run scan恢复。
3. `workflow_research_snapshots.manifest_ref` 对 v6 物理保存 bare snapshot blob digest，移除旧 `manifest_ref == snapshot_hash` 假设；domain 输出才 format wire ref。inherited 每个 ref 必须有 parent checkpoint owner且集合恰等于 snapshot closure，manifest pointer/hash、snapshot semantic hash/blob digest 全部一致；不能夹带任意 parent blob。通过后给 exact refs 增加 child staging，child genesis 提升 checkpoint owner。
4. generate-now、cancel、accepted/observed/settled/consume 继续使用现有 control journal，不另建 v6 控制面；v6 continuation policy 1 的 action payload 必须为空，新自然语言范围/目标走新 root/spec revision。
5. 恢复矩阵覆盖 v1..v5 historical reads、v5 continuation、v6 continuation 和 terminal replay。

迁移规则：v1～v5 lineage 不 backfill、不改变历史 sibling 语义；任何 pre-v4 v6 continuation 都使 v3→v4 migration fail closed，开发/Q1 隔离 DB 重建，不尝试在只有 DB path、没有 blob reader 的 schema migrator 中验证 closure。retention 位于 `backend/deskpet/workflows/retention.py`，按附录 §6.3 从真实 live roots 建 parent/child无向 component保护；无 root component才在单事务清checkpoint/effect/pending/owner refs，再按head→pins→descendant-to-root lineage→component全部run的start/audit operations/start requests/session refs→snapshot→零引用blobs/spec→descendant runs→root run后序删除，missing/corrupt closure fail closed。

必须测试：两个 repository connection 并发 different-key continue且固定 UUID/request/logical-slot golden完全相同、same-key audit唯一/different-key audit不同但child相同、空 action payload/非空拒绝、附录 §6.3 十一个 write point逐点 fault全 rollback、commit后notify前恢复、已有 head后 pin过期仍返回同 child且不再读取 closure、server snapshot exact closure分类与 arbitrary parent blob、child→grandchild chain、pre-v4 v6 detection rollback、retention parent-child/grandchild/shared-blob component后序删除与逐点rollback（retained checkpoint可解引用；删除后checkpoint/effect/pending/owner及root/child start/audit/request/session表均零残留）、process crash in accepted window、duplicate actions、root deadline `created_at/wall_not_after` 在 retry/resume/restart不变、T+895 accepted settle恰截到T+900、T+900后拒绝、resource budget contention；迁移前已有 v5 sibling lineage不阻止升级。

退出门：无 sibling continuation head；任何版本恢复都不会被错误 coercion。

### T13 — 全量验收、真实 UI、默认 v6 与事实源回写

**映射**：全部 AC 与 DoD。

动作：

1. 运行 scoped contract/evidence/assessment/report/delivery/frontend tests，再运行相关 workflow/retrieval 回归、TypeScript/Vite、Rust check；记录无关既有红项。
2. 运行 fault matrix：blob tamper、checkpoint/restart、commit/push 间崩溃、SessionDB 重试、fenced epoch、无 WS client、duplicate continue。
3. 按项目手工测试纪律启动真实源码 Tauri（只让 Tauri 管 backend/Vite，确认 `DESKPET_BACKEND_DIR`，并使用明确隔离的 `DESKPET_USER_DATA_DIR`），用 Computer Use 真输入/真点击。
4. 至少完成 completed、partial、insufficient/generate-now、reconnect/history、duplicate action 五类 UI case；保存截图、日志、checkpoint/manifest/receipt/SessionDB 交叉证据。
5. 保存 `timing-calibration.json`：固定时钟/慢阶段自动化各至少 20 次，固定离线 official-exact-fact fixture 完整路径至少 20 次并断言每次≤10 秒，真实 `SC-STATS-2` 健康网络至少 3 次，记录端到端及 node/search/fetch-stage 的 sample count、p50、p95、max、timeout/cancel count。每次真实 run 先记录 `environment_status=healthy|dns_unavailable|network_offline|official_upstream_unavailable` 及低基数探测 reason；非 healthy run 标 `environment_blocked`、不计性能样本也不能算 Q1/T13 PASS，必须在 healthy 环境重跑。healthy run 任一次 >120 秒或任一 page total >20 秒均为产品 deadline violation；外因分类不得放宽 budget，也不能只报平均值。
6. 所有开发态 P0/P1通过后新增 committed `backend/tests/fixtures/deep_research_v6_release_identity.json`，冻结 manifest/implementation/state/prompt/policy hashes及 v6 effect profile/prepared-call golden；factory pair=`v6/revision6`，resolver无env时接受configured v6，空/未知configured值回退factory v6并记录reason=`configured_invalid_factory_default`，只有显式合法v5 pin选择v5。factory migrator只把exact inherited `v5/revision5`提升到`v6/revision6`，explicit pin不变；覆盖fresh、inherited-v5、explicit-pin、`''|v7|garbage`。复验进程清除dev override（guarded代码保留），用新隔离user-data重启真实源码Tauri，重跑identity/recovery、SC-STATS-2真UI、reconnect/history和regression；旧run按DB version恢复，release fixture变化必须升v7。
7. 同次交付更新 `ARCHITECTURE/DeepResearch.md`、`ARCHITECTURE/PROJECT_STATUS.md`、testcase/results 和 plan results；未全绿不得写完成。

退出门：完成度审计 PASS、测试覆盖审计 PASS、真实 UI 交付证据完整。

## 6. AC 与场景追踪

| 验收 IDs | 主要任务 | 最关键证据 |
|---|---|---|
| AC-VERSION-01、AC-VERSION-02、AC-COMPAT-01 | T0、T1、T12、T13 | v5 hashes unchanged、v5/v6 recovery matrix |
| AC-SPEC-01、AC-SPEC-02、AC-SPEC-03、AC-SPEC-04 | T2、T4、T11、T12 | canonical spec/hash、union golden contracts、continuation same hash |
| AC-ROUTE-01、AC-ROUTE-02、AC-ROUTE-03、AC-ROUTE-04 | T5、T11 | direct hit/miss escalation、budget、conditional lanes |
| AC-EVID-01、AC-EVID-02、AC-EVID-03、AC-EVID-04、AC-EVID-05 | T3 | body offsets、same-page multi-span、binding validator、disclosure/conflict fixtures |
| AC-LEDGER-01、AC-LEDGER-02 | T4 | registered fact batches、head hash、deterministic assessment |
| AC-GATE-01、AC-GATE-02、AC-GATE-03、AC-QUALITY-01 | T6、T8、T9 | three-gate ordering、hard-failure matrix、soft-score isolation |
| AC-REPORT-01、AC-REPORT-02、AC-REPORT-03 | T7、T8、T11 | ClaimRecord bindings、profile snapshots、safe fallback |
| AC-PERSIST-01、AC-MANIFEST-01、AC-MANIFEST-02 | T9 | registered refs、manifest hash、cardinality |
| AC-COMMIT-01 | T10 | terminal checkpoint + intents + run status atomic commit |
| AC-DELIVERY-01、AC-DELIVERY-02、AC-DELIVERY-03、AC-CONTEXT-01 | T10 | outbox→SessionDB receipts、WS best-effort、history/context split |
| AC-CONT-01、AC-CONT-02、AC-CONT-03 | T6、T12 | same-version/spec continuation、CAS head、generate-now |
| AC-OBS-01、AC-OBS-02、AC-OBS-03、AC-OBS-04 | T5A、Q1、T13 | durable node/child spans、双时钟/CAS、metrics 同值、privacy scan、timing calibration |
| 快速确认 | Q1 | SC-STATS-2 real graph + original Session final + restart uniqueness |
| 完整 DoD | T13 | automation + real UI + DB/log/screenshot cross-evidence |

| 固定场景 | 覆盖任务/门 |
|---|---|
| SC-STATS-2 | T0、T2～T10、Q1、T13 |
| SC-STATS-4、SC-SAME-PAGE | T0、T2～T4、T6～T9、T13 |
| SC-COMPARE、SC-TOPN、SC-POLICY | T2、T3、T4、T6～T11、T13 |
| SC-DIRECT-MISS | T5、T6、T13 |
| SC-NOW-EMPTY | T6～T10、T12、T13 |
| SC-CRASH-COMMIT、SC-FENCED、SC-RECONNECT | T9、T10、T13 |
| SC-CONTINUE-RACE、SC-V5-RECOVERY | T0、T1、T12、T13 |
| SC-TIMING-SLOW、SC-TIMING-CANCEL、SC-TIMING-REPLAY | T5、T5A、Q1、T13 |

## 7. 明确禁止项

- 不修改 v5 definition 来塞入 v6 contracts/nodes/snapshot 字段。
- 不让 `ResearchBrief`、AnswerContract、slot ledger、DimensionCoverage、ReportClaim、terminal public 或 UI 成为并行业务 owner。
- 不在 production code 硬编码 140828/954/15.6/67.00 或为特定 query 分支。
- 不用 title/query 填充 fixture，不手工注入 terminal intents 证明 finalize 正确。
- 不把 AI disclosure 做成整页关键词黑名单。
- 不用 soft quality score 抵消 requirement、source、unit/time/scope 或 conflict hard failure。
- 不把 outbox/persist ref 放进 synth 前 readiness gate。
- 不把 WebSocket 连接存在当作 durable delivered。
- 不为本任务另建与 checkpoint/blob/outbox 重复的 event-sourced DB。
- 不在 dirty worktree reset/clean/checkout，不提交无关文件。
- 不在实现和测试完成前更新 `ARCHITECTURE/` 为“已完成”。

## 8. 本轮 plan-test 状态与已冻结产品决策

用户已授权以本计划为初始 plan 进入完整 `plan-test` 开发与测试，以下三项不再悬空：

1. **版本边界确认**：冻结 v5，新建 v6；完整验收后新 root 默认 v6，旧 run 按原版本恢复。
2. **快速门确认**：Q1 `SC-STATS-2` 是首个真实垂直门；PASS 后继续完整 intent，FAIL 时先修垂直链。
3. **continuation 基数确认**：v6 每个 parent 采用单 lineage head。repository 必须以 durable CAS/唯一约束保证不同 idempotency key 并发也只创建一个 child；失败请求返回已存在 child，不靠 UI 或进程内锁去重。

Q1 已通过。Phase 3 的 A2 审计把 T11/T12 退回 Phase 2；13轮增量挑战已将 production evidence/inference、continuation/recovery/retention 合同收敛，最终 T11专项 Round 10、T12专项 Round 11、集成 Round 13 均 PASS。用户已逐项 review 并于 2026-07-18 批准事实先落盘再推理、single-head continuation、既有默认安装升级 v6、完整验收强度与 T11→T12→T13 边界；计划恢复 finalized 并继续执行。
