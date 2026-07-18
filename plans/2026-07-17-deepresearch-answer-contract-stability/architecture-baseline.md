<!-- architecture-status: plan-test phase-0 recalibrated 2026-07-17; v6 not implemented -->
# 架构基线与决策：从 DeepResearch v5 到 v6

> 日期：2026-07-17  
> 性质：`plan-test` 实现前的当前事实、架构评估和目标设计；不表示 v6 已实现。  
> 验收标准：[`acceptance.md`](./acceptance.md)  
> 第一轮挑战记录：[`architecture-review-round-1.md`](./architecture-review-round-1.md)

## 1. 架构结论

初始方案的方向——答案完成契约、精确证据、硬门和可靠交付——是必要的，但原设计不能直接实施。它会同时引入双语义 owner、破坏冻结的 v5 definition identity、形成 quality/persist/delivery 依赖环，并把三份可变状态散落在 workflow、SessionDB 和 UI。

修订后的选择是：

1. 冻结 `deep_research/v5`，结构性升级在 `deep_research/v6` 落地。
2. 用一个 canonical `ResearchSpecV1` 统一用户问题和完成语义；其他 brief/contract 只能是只读投影。
3. 用 requirement graph 表达 scalar、matrix、dynamic collection 和 claim set，不使用统一平铺 `AnswerSlot[]`。
4. 用 page → span → binding → append-only fact batch 建立证据身份；`AnswerAssessmentV1` 为确定性投影。
5. 按 Answer Readiness → Report Integrity → Delivery Commit 三道门排序。
6. 复用现有 checkpoint、`RegisteredBlobStore`、`commit_frontier` 和 outbox；不新建一套完整 event-sourced research DB。
7. 保留两个 canonical terminal facts：引擎终态和答案/交付 manifest；用户 delivery view 从 receipts 派生。

v6 也不能在发布后原地无限演进：T1～T12 期间它是非默认的开发 definition，Q1 只证明同一 build 内的 restart/replay；T13 在完整 contracts/callables/policies 落地后冻结 v6 identity 并切默认。此后任何会改变 manifest/implementation/state schema hash 的改动必须发布 v7。

这是“保留 durable 底座、替换语义中段和最后一公里”的版本升级，不是重写整个检索系统。

## 2. 当前 v5 生产链路

```text
用户消息
  -> deterministic deep-research ingress
  -> main.py 启动 durable deep_research/v5
  -> normalize -> model -> plan -> expand
  -> search -> direct -> fetch -> score
  -> gap loop -> rerank
  -> synth -> quality -> persist
  -> finalize 或 insufficient_finalize
  -> native terminal event / outbox
  -> SessionDB + best-effort WebSocket
  -> sessionsStore / progress card / history recovery
```

主要 owner：

- graph：`backend/deskpet/workflows/definitions/v5/deep_research.py`
- contracts/snapshot：`backend/deskpet/workflows/definitions/deep_research_v5_contracts.py`
- nodes：`backend/deskpet/workflows/definitions/deep_research_v5_nodes.py`
- evidence runtime：`backend/deskpet/workflows/adapters/deep_research_v5_evidence_runtime.py`
- report/quality：`backend/deskpet/workflows/definitions/deep_research_v5_report.py`
- extractive delivery：`backend/deskpet/workflows/definitions/deep_research_v5_delivery.py`
- workflow native terminal：`backend/deskpet/workflows/native.py`
- atomic checkpoint/outbox commit：`backend/deskpet/workflows/store/checkpointer.py::commit_frontier`
- outbox：`backend/deskpet/workflows/outbox.py`
- SessionDB/UI projection：`backend/main.py`、`backend/deskpet/memory/session_db.py`、`tauri-app/src/stores/sessionsStore.ts`

## 3. 已核对的代码事实

### 3.1 v5 identity 已冻结

- `backend/tests/test_workflow_deep_research_v5_contracts_identity.py` 固定验证 v5 manifest identity。
- `backend/deskpet/workflows/runner.py` 在注册和恢复时严格验证 manifest/implementation hash。
- `backend/deskpet/workflows/store/research_repository.py` 创建 continuation child 时继承 parent 的 workflow version、manifest、implementation 和 state schema。

因此，在 v5 snapshot/definition 中直接加入 `AnswerContract`、ledger 或新 nodes 会改变 identity，和现有恢复语义冲突。不能用“兼容字段默认值”绕过 definition hash。

### 3.2 v5 没有统一完成语义

当前 `ResearchDimension` 只有：

```text
dimension_id
question
importance
expected_source_types
query_targets
first_party_required
not_applicable_when
```

`ResearchBrief` 持有问题/profile/as-of/locale/geography/subjects/decision/dimensions；v5 snapshot 没有 canonical spec、evidence log head 或 assessment policy hash。检索维度、报告 claims 和 quality 审计因而可各自解释“完成”。

### 3.3 页面、span 和 claim identity 不足

- evidence runtime 使用 title + whole body 计算相关性，再由 `_best_span` 选一个片段。
- 一个 candidate id 含 dimension id 且只带一个 `span_text`；同一官方正文支持四项统计时，无法自然表示四个独立 spans/bindings。
- report 的 hard support 主要验证 ref 存在和 dimension 相同，没有验证 metric/value/unit/time/scope/definition。
- extractive fallback 以 lexical overlap 选择 snippet，并保留原 claim kind，可能把标题、导航或免责声明升级为事实。

### 3.4 quality、persist、delivery 当前断裂

- v5 顺序为 `synth -> quality -> persist -> finalize`。如果把“ref 已持久化/outbox 已创建”放进 quality hard gate，就会形成 quality 依赖 persist，而 persist 又依赖 quality 的环。
- `persist_handler` 主要保存 report ref；delivery builder 仍可生成 `summary:{operation_id}`、`quality:{operation_id}` 这类没有 resolver owner 的逻辑 ref。
- finalize/insufficient finalize 设置 decision/public/status，但不稳定地产生完整 delivery intents。
- `native.py` 读取 state 中已有 `delivery_intents` 后才附加 engine final；缺少业务 intent 时，Session 投影会退化为“任务已结束：completed”。

### 3.5 现有 durable commit 可复用

`commit_frontier` 已能在一个 workflow DB 事务中写入：

- checkpoint/state；
- intents/events/deliveries；
- terminal run 更新。

outbox event identity 已使用 `(run_id, event_key)`，delivery identity 使用 `(event_id, channel, target)`。所以不需要再造研究专用事务日志；目标应是让 v6 在进入 `commit_frontier` 前产出完整、可验证、内容已持久化的 terminal manifest。

### 3.6 Session 投影和 receipt 语义仍需修正

- SessionDB 以 `workflow_event_id` 做跨 DB at-least-once 去重，适合 durable final 投影。
- WebSocket 是 best-effort，不能作为 durable delivered 的证据。
- 当前 delivery handler 在 session epoch mismatch 或没有 WS client 的路径上可能无显式失败结果，worker 仍可能标记 delivered；需要返回结构化 outcome。
- progress/final-status/card 目前可作为普通 assistant 历史存在。直接停止持久化会破坏 UI 历史，正确做法是增加 `projection_kind/context_visibility`，让 UI history 与 LLM conversational context 使用不同过滤规则。
- 前端 reducer 已能区分 engine final 与 delivery status，可以演进为 receipts 派生视图，无需再增加第三个可变 backend 状态。

### 3.7 timing 当前边界仍不足以支撑长期优化

- workflow node attempt/span 已把节点级起止、终态与 duration 保存到 `workflow.db`，并有 `deepresearch_stage_timing` 安全镜像。
- Search Gateway provider attempt 与 fetch 的 robots/Scrapling/HTTPX/extract/browser/Jina 分段主要写入可轮转 `metrics.jsonl`；fetch 子阶段尚未成为 durable trace child spans。
- 当前 fetch 分段没有完整分离 throttle、URL-lock 等待和 total fetch；毫秒整数舍入也允许极短操作显示为 0。
- 因此 v6 的 AC-OBS 不能把现有 metrics 当成已完成的 30 天事实源：需要统一 correlation identity、durable child timing、时钟/零值规则和 metrics/diagnostic 低基数镜像边界。

## 4. 已确认的运行缺陷

| Session / run | 引擎结果 | 研究/交付结果 | 架构含义 |
|---|---|---|---|
| Session `430bd129-9a45-4e8b-bb31-4813dd299dde` / run `6ea6a3a449c047e98750a84276760bb2` | `completed`，generate-now 控制链成功 | `insufficient_evidence`，原 Session 无报告、无 safe summary | 控制面正常不等于用户拿到答案 |
| run `517810633ee742399f1bc7622ab6d7ec` | `completed`，quality=80 | 文件已保存但无 final assistant；四指标不完整 | soft score 与文件存在制造“假完成” |
| run `7ae83d8d074b437fa11bab1c78edbcc8` | 生成报告 | 出生人口缺失，`{{aisd}} AI生成` 免责声明入选 | one-best-span 和 disclosure 策略不足 |

已有 41 个 targeted tests 通过，只证明现有断言未覆盖真实 final message、逐 requirement 完整性和 receipt outcome；它们不能作为产品健康结论。

## 5. 初始架构为什么不可执行

| 严重度 | 初始设计问题 | 后果 | 修订 |
|---|---|---|---|
| Blocker | `ResearchBrief -> AnswerContract` 两个对象都拥有用户语义 | 恢复或 continuation 后可能出现两个不同完成定义 | 只保留 canonical `ResearchSpecV1`；旧名仅作同 hash 的只读投影 |
| Blocker | 直接修改 frozen v5 | manifest/implementation/state schema identity 变化，旧 run 不能按原定义恢复 | 新建 v6；v5 完全冻结 |
| Blocker | 平铺 `AnswerSlot[]` | Top-N 未知候选无法预冻结；open research 被错误结构化 | requirement graph + runtime instances |
| Blocker | hard gate 同时要求 evidence、report ref、outbox | 当前 graph 出现 synth/quality/persist/delivery 依赖环 | 拆成三道有序门 |
| High | page、span、binding、slot、claim 多处复制事实 | 同页多指标、冲突和 provenance 无法稳定表达 | 分层身份 + append-only fact batch + sole ClaimRecord |
| High | engine/business/user delivery 三个可变状态 | state、terminal public、SessionDB、UI 会漂移 | 两个 canonical facts + receipt-derived view |
| High | slot ledger 既持久化又由 coverage 修改 | checkpoint 重放时谁是真相不明确 | fact log 为 owner，assessment 为确定性投影 |
| High | route 放进 immutable contract | capability、budget 和 direct miss 会迫使合同变更 | `RouteDecisionV1` 独立且记录升级边 |
| High | continuation “合同不可变但可新增 slots” | 自相矛盾，child 完成语义不可审计 | 不改范围则 exact same spec hash；范围扩展另建 revision/root |

## 6. 方案比较

| 方案 | 优点 | 主要问题 | 决策 |
|---|---|---|---|
| A. 在 v5 增加 AnswerContract/slot ledger | 表面改动少 | 破坏 frozen identity；双 owner；Top-N 不自然；门禁成环 | 拒绝 |
| B. v6 canonical ResearchSpec + derived projections | 版本边界清楚；恢复可审计；复用 durable 底座；支持多种 intent | 需要新增 v6 adapter/contracts 和明确迁移入口 | **采用** |
| C. 全动态 multi-agent/citation-agent 系统 | 开放题扩展性强 | 对 exact fact 过重；会复制现有 scheduler/budget/merge 机制 | 仅作为 v6 条件 work lanes，不做核心 |
| D. 新建完整 event-sourced research DB | 理论上审计最完整 | 与 checkpoint/blob/outbox 重复，复杂度和事务边界显著增加 | 当前拒绝 |

## 7. v6 目标链路

```text
ingress
  -> launch deep_research/v6
  -> normalize
  -> compile + persist ResearchSpecV1
  -> decide RouteDecisionV1
       ├─ official_direct -> fetch
       ├─ matrix/collection -> plan -> search -> direct -> fetch
       └─ adaptive_open -> plan -> conditional lanes -> merge -> fetch
  -> PageAdmission
  -> EvidenceSpan extraction
  -> EvidenceBinding validation
  -> append EvidenceFactBatchV1
  -> derive AnswerAssessmentV1
  -> AnswerReadinessGate
       ├─ gap/escalate within budget
       ├─ synthesize completed/partial candidate
       └─ synthesize insufficient safe summary
  -> register ClaimRecordV1
  -> intent-specific renderer
  -> ReportIntegrityGate -> repair/downgrade if needed
  -> persist canonical content blobs and optional file projection
  -> build TerminalDeliveryManifestV1
  -> DeliveryCommitGate
  -> native commit_frontier
  -> outbox -> SessionDB receipt + best-effort WebSocket
  -> receipt-derived UI delivery view / history recovery
```

`insufficient_evidence` 也必须先持久化 safe summary，再进入同一个 manifest/finalize 路径；不再维护一条会产生悬空 ref 的特殊终止链。

## 8. 目标领域模型

### 8.1 `ResearchSpecV1`

```text
ResearchSpecV1
  schema_version
  normalized_question
  intent_type
  user_constraints
  work_dimensions[]
  requirements[]
  completion_policy_ref/hash
  render_profile_ref/hash
```

requirements 是 tagged union：

- `ScalarRequirement`：单个 metric/value/unit/time/scope。
- `MatrixRequirement`：subjects × axes，每个 cell 独立评估。
- `CollectionRequirement`：`min_items`、`unique_key`、`row_schema`、`ranking_rule`、`as_of`；检索后才产生 item instances。
- `ClaimSetRequirement`：开放研究要求的结论、限制、反证、不确定性等 claim kinds 和最低覆盖。

`work_dimensions` 只用于拆分查询和预算，不拥有答案完成语义。

### 8.2 evidence identity

```text
PageRecord
  page_id, canonical_url, body_blob_ref/hash, source metadata, admission

EvidenceSpan
  span_id, page_id, body_start, body_end, excerpt_hash, page_part

EvidenceBinding
  binding_id, span_id, requirement_id/item_key
  parsed raw/normalized value, unit, time, geography/population scope, definition
  source tier/family, validator version, status, reason codes

EvidenceFactBatchV1
  batch_id/hash, previous_head_hash, admitted/rejected bindings, provenance
```

page 负责“正文是什么”，span 负责“正文哪一段”，binding 负责“这段能否支持哪个 requirement”。三者不可合并成一个 dimension candidate。

### 8.3 assessment 与 claim

```text
AnswerAssessmentV1 = assess(spec, ordered_fact_batches, policy_hash)
  requirement results
  collection instances
  conflicts
  missing set
  minimum useful coverage
  proposed answer_status

ClaimRecordV1
  claim_id, claim_kind, normalized proposition
  binding_ids, support_status, renderer_visibility
```

assessment 可以在 checkpoint 保存小型缓存以便 UI/恢复，但必须同时保存输入 head hash 和 policy hash；不匹配时重算或 fail closed。`DimensionCoverage` 如需兼容，只能从 assessment 投影。

### 8.4 terminal manifest

```text
TerminalDeliveryManifestV1
  manifest_version/hash
  run_id, spec_hash, assessment_hash
  answer_status: completed | partial | insufficient_evidence
  final_assistant_ref
  artifact_ref?              # completed/partial only, max 1
  final_status_payload
  claim/audit/report refs
  delivery intents + stable identities
  cardinality policy hash
```

manifest 生成前，所有 ref 必须已由 `RegisteredBlobStore` 注册且可读。native 只负责验证/展开 manifest 并调用现有 atomic commit，不再用占位文本补造业务结果。

## 9. 状态和投影模型

| 视角 | owner | 示例 | 规则 |
|---|---|---|---|
| 引擎 | `workflow_runs.status` | running/completed/failed/cancelled | 描述执行是否收敛，不描述答案是否充分 |
| 答案 | immutable terminal manifest | completed/partial/insufficient_evidence | 由 assessment + gates 决定 |
| 交付 | receipt aggregate query | queued/delivering/delivered/retrying | 由 required durable targets 派生，不写回第三个 truth 字段 |
| UI | reducer projection | progress card/final/artifact | 可重建，不拥有业务状态 |

SessionDB message projection增加明确元数据：

- `conversation`：用户消息、final assistant；进入下一轮 LLM context。
- `workflow_ui`：progress、accepted、decision、final-status、artifact-card；历史可见但 `context_visibility=exclude`、`skip_embed=true`。

## 10. v6 checkpoint 与恢复边界

v6 snapshot 至少包含：

```text
spec_ref/spec_hash
route_decision + route_policy_hash
ordered_evidence_batch_refs/evidence_head_hash
assessment cache/input hash/assessment_policy_hash
claim_batch_ref/hash
canonical content refs
terminal_manifest_ref/hash
budget/control/loop state
```

恢复规则：

1. definition/version/manifest/implementation/state schema 先由 runner 校验。
2. 所有 registered refs 必须属于正确 run/root owner 且 hash 可读。
3. assessment cache 输入 hash 不一致则重算；manifest 一旦 terminal commit 不再变更。
4. v5 checkpoint 继续由 v5 adapter 读取；不把旧 state coercion 成 v6。
5. v6 continuation 继承 exact `spec_hash` 和 evidence head。若需要改变范围，另建显式 revision/root，不在当前 continuation 偷改。
6. Q1 的开发 run 只承诺同一 source bundle 内恢复；v6 release hash 在 T13 冻结。冻结后不再以同一个 version 修改 callable/policy/schema。

## 11. 可快速确认的垂直切片

为了先验证架构，不需要第一步就完成所有 intent。最小可交付切片是 `SC-STATS-2`：

```text
v6 skeleton
  -> ScalarRequirement compiler
  -> official direct/search route
  -> page/span/binding + fact batch
  -> AnswerAssessment + three gates
  -> exact-fact renderer
  -> RegisteredBlobStore + terminal manifest
  -> commit_frontier/outbox/SessionDB
  -> 原 Session 唯一 final assistant
```

该切片必须走真实 v6 graph 和真实 terminal delivery，不能通过 query 特判、硬编码数字、手工注入 intents 或绕过 SessionDB 来“快速证明”。开发 v6 run 使用隔离的测试 user-data/workflow DB，避免后续 hash 变化在真实用户库留下无法恢复的未发布 run；通过后再扩展 matrix、collection、claim set 和 conditional fan-out。

## 12. 边界与未定风险

- 当前工作树已有大量用户修改；实施前必须做 scoped inventory，不得 reset/clean/checkout。
- continuation 单-head 是否为最终产品语义需要在实施 T11 前确认；若保留该语义，必须用 repository CAS/unique constraint，不只靠 idempotency key。
- 真实网络成本、900 秒 cap、accepted→observed 崩溃窗口、浏览器/搜索/LLM 并发资源竞争仍需在 fault matrix 验证，复现前不写成 confirmed bug。
- 本轮 phase-0 只校准架构事实；v6 尚未实现，也未做 v6 UI E2E。此前已完成的 timing 遥测作为当前工作树基线单独记录，不能被当作 v6 完成证据。只有未来实现通过验收后，才能按项目纪律把 v6 写成生产事实。
