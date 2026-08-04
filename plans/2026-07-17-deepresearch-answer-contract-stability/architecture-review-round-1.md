<!-- plan-status: architecture-reviewed-draft (plan-bs round 1); not finalized; implementation intentionally not executed -->
# DeepResearch 计划架构评估：Round 1

> 日期：2026-07-17  
> 评估对象：初始 `acceptance.md`、`architecture-baseline.md`、`plan.md` 及当前本地生产代码。  
> 范围：只做一轮架构挑战和计划修订；未执行实现、自动化测试或 UI E2E。

## 1. Verdict

**FAIL — 初始草案方向有价值，但不具备直接代码可执行性。**

三组独立挑战分别审查了：

1. intent/答案语义与版本身份；
2. requirement、证据、持久化与恢复模型；
3. gate 顺序、terminal commit、receipt、SessionDB 和 UI 投影。

三组都给出 FAIL，且 blocker 高度一致：初始设计不是缺少几项补充，而是 semantic owner、版本边界和依赖顺序需要重构。

## 2. Blockers

| ID | 发现 | 本地证据 | 为什么阻塞实施 |
|---|---|---|---|
| R1-B01 | 初始 `ResearchBrief -> AnswerContract` 形成双 owner | `deep_research_v5_contracts.py` 中 ResearchBrief 已持有问题/约束/dimensions | 两份对象都可决定“什么算完成”，恢复、repair 和 continuation 会漂移 |
| R1-B02 | 初始方案直接扩展 v5 | v5 identity test；runner 严格校验 manifest/implementation hash | 新字段/nodes 会改变冻结 identity，旧 checkpoint 无法安全按原定义恢复 |
| R1-B03 | 平铺 `AnswerSlot[]` 不支持动态集合 | Top-N 候选在检索前未知 | 预造 N 个 slot 不能标识实体；少项/去重/排名规则无法稳定表达 |
| R1-B04 | page/span/binding/slot/claim 所有权混合 | evidence runtime 每 candidate 一个 `_best_span`；report 按 dimension/ref 支持 | 同页多事实会复制 page 或丢事实，claim 与 evidence 语义无法逐条证明 |
| R1-B05 | quality gate 要求 persisted refs/outbox 会成环 | 当前 graph 为 synth→quality→persist→finalize | quality 等待 persist，而 persist 又应只保存通过 quality 的内容 |
| R1-B06 | engine/business/user delivery 三个可变标量 | state、terminal public、SessionDB、前端各有状态投影 | 重放/重连/跨 DB 投影时出现多个 truth source |
| R1-B07 | mutable slot ledger 与 coverage owner 不清 | v5 snapshot 无 spec/ledger；初始计划同时增加 ledger 和 coverage | checkpoint 恢复时无法判断重算结果还是旧 ledger 为真 |
| R1-B08 | route 写入 immutable contract | direct/source health/capability/budget 是运行时事实 | direct miss 或恢复时会迫使“不可变合同”变化 |
| R1-B09 | continuation 规则自相矛盾 | 初始计划同时写“immutable contract”和“child 可新增 slots” | parent/child 完成语义不可比较、不可审计 |
| R1-B10 | delivery intent/ref 不保证真实 owner | v5 可生成 `summary:<operation_id>` 等逻辑 ref；finalize 不保证完整 intents | engine completed 后仍可能没有用户正文或出现悬空 Artifact ref |

## 3. 关键可复用资产

评估没有建议推倒重写。以下现有资产足以承载目标架构：

- native durable workflow、checkpoint、lease、effect journal、control journal 和 recovery；
- `RegisteredBlobStore` 的内容寻址与 owner 注册；
- `commit_frontier` 对 checkpoint、outbox events/deliveries、terminal run 的单 workflow DB 原子事务；
- outbox event/delivery identity 与 SessionDB `workflow_event_id` 去重；
- Search Gateway、静态抓取/Playwright、budget/circuit/provider limiter；
- 前端 reducer 已有的 engine final 与 delivery projection 区分能力。

因此不采用“新建完整 research event store”或“用全动态多代理替换 durable graph”。

## 4. 采用的修订

| 初始方案 | 修订后 |
|---|---|
| 修改 `deep_research/v5` | 冻结 v5，新建独立 v6 definition/snapshot/adapter |
| `ResearchBrief + AnswerContract` | canonical `ResearchSpecV1`；其他对象只读投影同一 hash |
| `AnswerSlot[]` | Scalar/Matrix/Collection/ClaimSet requirement graph |
| contract 内 routing hint | 独立、可恢复的 `RouteDecisionV1` |
| 每页一个 best span/candidate | canonical body → 0..N spans → bindings |
| mutable slot ledger | append-only `EvidenceFactBatchV1` |
| persisted coverage decision | deterministic `AnswerAssessmentV1` projection/cache |
| DimensionCoverage/ReportClaim 参与 owning | readonly compatibility projection / renderer DTO |
| 一个 quality hard gate 包含 delivery | Answer Readiness → Report Integrity → Delivery Commit |
| 文件/逻辑 ref 先行 | 先 RegisteredBlobStore，再 manifest/ref |
| finalize 各自拼装消息 | 一个 `TerminalDeliveryManifestV1` + native deterministic expansion |
| 三个可变状态 owner | engine fact + answer manifest + receipt-derived delivery view |
| progress 从 SessionDB 删除 | durable UI projection 保留，显式排除 conversational context |
| continuation 可加 slots | same-scope continuation 继承 exact spec hash |

## 5. 方案选择记录

### 采用：v6 canonical spec + derived projections

原因：

- 保护 v5 恢复身份；
- 语义 owner 可审计；
- 能表达简单事实、矩阵、动态 Top-N 和开放 claim set；
- 恢复只需要验证 refs/hashes 并重算 assessment；
- 与现有 blob/checkpoint/outbox 事务边界一致；
- 可先做 SC-STATS-2 垂直切片快速验证，而不先建设全部 intent。

### 拒绝：继续 patch v5

不是因为不能写兼容代码，而是 definition identity 已被当作恢复合同。结构性字段和 graph 变化应显式成为新版本。

### 拒绝：完整 event-sourced research DB

现有 `EvidenceFactBatch` 可作为 content-addressed append-only facts，checkpoint 保存 ordered refs/head，已经满足当前审计和重建要求。再建 DB 会复制事务、GC、owner 和恢复机制。

### 限制使用：全动态 multi-agent

只允许作为 v6 adaptive route 的条件 work lanes；exact fact、matrix 和 terminal semantics 仍由主 graph/spec/assessment/manifest 统一控制。

## 6. 修订后的实施顺序

```text
freeze v5 + red fixtures
  -> v6 identity/version registry
  -> ResearchSpec/requirements
  -> page/span/binding
  -> fact batches/assessment
  -> route/direct
  -> Answer Readiness
  -> ClaimRecord/render
  -> Report Integrity
  -> canonical blobs/terminal manifest
  -> Delivery Commit/outbox/receipts/SessionDB/UI
  -> SC-STATS-2 quick checkpoint
  -> remaining intents/fan-out/continuation/fault matrix
```

这一顺序修复了初始计划“先 route、后定义 slot evaluator”和“quality 先要求尚未持久化 ref”的依赖错误。

## 7. 尚需用户 review 的产品决策

只有三项会实质改变后续实施：

1. **版本边界**：是否正式确认新 root 升到 v6，v5 只做旧 run 恢复。
2. **快速停靠点**：是否先只实现并真测 `SC-STATS-2`，确认成功后再扩展其它 intent。
3. **continuation cardinality**：是否坚持一个 parent 只能有一个 continuation child head；若是，必须落 repository CAS/unique constraint。

版本确认还包含 release 纪律：Q1 使用非默认的开发 v6；T13 才冻结完整 v6 identity。冻结后若再增加 callable/policy/schema，必须升 v7，不能重复“同版本换实现”。

## 8. 本轮状态

本轮已把全部共同 blocker 写回三份主计划文档。由于用户只要求一次评估和优化，没有继续运行 plan-bs 的多轮 challenger，也没有做用户逐条终审，所以计划仍为 `architecture-reviewed-draft`，不能标记 `finalized (plan-bs)`。
