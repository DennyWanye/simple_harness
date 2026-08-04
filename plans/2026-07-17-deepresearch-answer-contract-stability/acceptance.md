<!-- acceptance-status: finalized-for-plan-test 2026-07-18; user-approved; execution-authorized -->
# 验收标准：DeepResearch v6 答案语义与可靠交付

> 日期：2026-07-17  
> 状态：用户已确认作为 `plan-test` 的初始验收真相源；阶段 0 架构校准与七轮 plan challenge 已完成，Round 7 PASS，等待用户 review 后定稿。  
> 计划入口：[`plan.md`](./plan.md)  
> 架构事实与决策：[`architecture-baseline.md`](./architecture-baseline.md)  
> 本轮评估记录：[`architecture-review-round-1.md`](./architecture-review-round-1.md)

## 1. 验收目标

DeepResearch 的成功标准不是 `workflow.status=completed`，而是：系统按用户问题冻结完成语义，取得能直接支持答案的证据，诚实判定 `completed / partial / insufficient_evidence`，并在原 Session 中幂等、可恢复地交付一条最终答案。

本计划冻结 `deep_research/v5` 的 definition identity，新语义在 `deep_research/v6` 实现。v5 只做兼容读取、恢复和原版本 continuation，不在原 manifest 上追加结构性能力。

本轮按 `plan-test` 完整执行；只有全部验收、自动化、真实 UI 和文档门禁通过后，才能更新为已完成。

## 2. 唯一事实源与派生对象

| 语义 | 唯一 owner | 派生对象不得做什么 |
|---|---|---|
| 用户问题与完成条件 | `ResearchSpecV1` | `ResearchBrief`、prompt、renderer 不得另存一份可变规则 |
| 运行时路线 | `RouteDecisionV1` | 不得反向改写 `ResearchSpecV1` |
| 页面正文 | canonical body blob + `PageRecord` | span 不得复制并篡改正文 |
| 可用证据 | append-only `EvidenceFactBatchV1` | coverage、dimension 不得成为第二份证据账本 |
| 当前完成度 | 由 spec + fact batches 确定性计算的 `AnswerAssessmentV1` | 缓存不得在输入 hash 不一致时继续使用 |
| 用户可见 claim | `ClaimRecordV1` | `ReportClaim` 只可作为 renderer DTO |
| 答案业务终态和交付基数 | `TerminalDeliveryManifestV1` | terminal public、SessionDB、UI 不得各自改写业务终态 |
| 引擎终态 | `workflow_runs.status` | 不得代替答案业务终态 |
| 用户交付情况 | required durable delivery receipts 的查询时聚合 | 不持久化第三个可漂移的 `user_delivery_status` 标量 |

## 3. 功能验收条款

### 3.1 版本和答案规范

| ID | 验收点 | 可验证条件 | 优先级 |
|---|---|---|---|
| AC-VERSION-01 | 冻结 v5 | v5 manifest hash、implementation hash、state schema hash 与现有 identity 测试保持不变；旧 run 可按 v5 恢复 | P0 |
| AC-VERSION-02 | 独立 v6 与冻结点 | 新 root run 使用 `deep_research/v6` 的独立 definition、snapshot、terminal projection 和 continuation adapter；不存在运行中 v5→v6 隐式升级。v6 在 T13 前是非默认、未发布的开发 identity；完整实现后一次性冻结 hashes 再默认开启，冻结后的结构性变化必须升 v7 | P0 |
| AC-SPEC-01 | 单一规范 | 每个 v6 root 在检索前持久化版本化、内容寻址的 `ResearchSpecV1`；后续节点只通过 `spec_ref + spec_hash` 使用同一规范 | P0 |
| AC-SPEC-02 | 服务端冻结 | 原问题、intent、对象、指标、时间、地域、人群、Top-N 数量、比较轴、第一方要求由确定性 validator 冻结；LLM 只能提出候选补充，不能删除或降级用户明确约束 | P0 |
| AC-SPEC-03 | requirement graph | 规范支持 `ScalarRequirement`、`MatrixRequirement`、`CollectionRequirement`、`ClaimSetRequirement`；Top-N 表示为 `min_items=N + unique_key + row_schema + ranking_rule`，不预造十个未知实体槽位 | P0 |
| AC-SPEC-04 | 规范不可变 | 同一 run 和“不改变范围”的 continuation 使用完全相同的 `spec_hash`；范围扩展必须创建新 root 或显式 spec revision，本计划不允许静默增删 requirements | P0 |

### 3.2 路由、证据和完成度

| ID | 验收点 | 可验证条件 | 优先级 |
|---|---|---|---|
| AC-ROUTE-01 | 路由分离 | `RouteDecisionV1` 单独记录 `spec_hash`、route policy version/hash、capability/budget snapshot、原因和允许的升级边；route 不属于 immutable spec | P0 |
| AC-ROUTE-02 | 官方事实快路径 | 简单官方统计题优先走通用 authority registry 解析出的 first-party seed（若存在）或 `official_source_search -> fetch -> final URL verify` 有界路径；满足 requirements 后立即进入 readiness gate，不调用 legacy source-pack query 特判，也不启动 fan-out | P0 |
| AC-ROUTE-03 | 可恢复升级 | direct miss 可按已冻结升级边转入 search/adaptive 路径，升级不改变 `spec_hash`，重放得到相同决策 | P0 |
| AC-ROUTE-04 | 条件 fan-out | 只有存在至少 3 个独立 work groups、合并键明确且总预算允许时才 fan-out；lane 结果都写回同一 evidence fact log | P1 |
| AC-EVID-01 | 页面和 span 分层 | `PageRecord` 只判定页面可用性；一个 canonical body 可产生 0..N 个带真实正文 offset、excerpt hash、page part 的 `EvidenceSpan` | P0 |
| AC-EVID-02 | binding 精确校验 | `EvidenceBinding` 指向 requirement 或 collection item，并记录 raw/normalized value、unit、time、geography/population scope、definition、source tier/family、validator version、status/reasons | P0 |
| AC-EVID-03 | 同页多事实 | 同一国家统计局正文可用四个独立 spans/bindings 支持四项统计，不复制页面 candidate，也不受“每页一个 best span”限制 | P0 |
| AC-EVID-04 | disclosure 范围 | AI 声明、导航、页脚和模板文本仅污染重叠区域；不得因页面提到“AI生成”而整页拒绝，也不得把这些区域升级为 winning evidence | P0 |
| AC-EVID-05 | 第一方与冲突 | first-party、source-family、定义、单位、时点和范围在 binding 层校验；未解决的 hard conflict 明确进入 assessment，不可被软质量分抵消 | P0 |
| AC-LEDGER-01 | 追加式事实日志 | admission 结果按内容寻址的 `EvidenceFactBatchV1` 追加；checkpoint 保存有序 batch refs/head hash，不维护可独立漂移的 mutable slot ledger | P0 |
| AC-LEDGER-02 | 确定性 assessment | `AnswerAssessmentV1` 只由 `ResearchSpecV1 + EvidenceFactBatch[] + assessment policy hash` 计算，重复计算结果相同；缓存 hash 不匹配时 fail closed 或重算 | P0 |

### 3.3 三道门与报告

| ID | 验收点 | 可验证条件 | 优先级 |
|---|---|---|---|
| AC-GATE-01 | Answer Readiness | synth 前检查 required requirements、最小有用覆盖、第一方要求、单位/时点/范围和冲突；失败只能补证、partial 或 insufficient，不能生成 completed 报告 | P0 |
| AC-GATE-02 | Report Integrity | render 后逐个检查所有用户可见 claim 是否注册为 `ClaimRecordV1` 并绑定 admitted evidence；允许定向 repair、降级或删除 unsupported claim | P0 |
| AC-GATE-03 | Delivery Commit | 内容持久化后检查所有 refs 可解析及 manifest 基数；通过后才允许 native terminal commit。delivery/outbox 不作为 synth 前质量门，避免依赖环 | P0 |
| AC-QUALITY-01 | 硬门优先 | readability、source diversity、analysis depth 等软分只在相关硬门通过后计算；任何 hard failure 都不能通过加权得分变成 completed | P0 |
| AC-REPORT-01 | 唯一 claim owner | 每个用户可见事实、比较判断和推断都有稳定 `claim_id`、claim kind、binding ids 和支持状态；renderer DTO 不可成为另一账本 | P0 |
| AC-REPORT-02 | 分型输出 | exact fact、comparison、ranked list、policy、open research 分别使用适配的 renderer；首屏直接回答已满足 requirements，并就近给出引用和诚实缺口 | P0 |
| AC-REPORT-03 | 安全降级 | extractive fallback 只能消费 admitted bindings；导航、标题、免责声明、搜索导流和未绑定 snippet 不得生成事实 claim | P0 |

### 3.4 持久化、终态和用户交付

| ID | 验收点 | 可验证条件 | 优先级 |
|---|---|---|---|
| AC-PERSIST-01 | 真实内容 owner | report、final assistant、safe summary、claim/audit payload 先写 `RegisteredBlobStore`，再产生可解析 ref；文件系统报告只是 canonical blob 的投影 | P0 |
| AC-MANIFEST-01 | 终态 manifest | `TerminalDeliveryManifestV1` 在全部内容持久化后生成并冻结，包含 `answer_status`、spec/assessment/policy hashes、内容 refs、delivery cardinality 和 intent identities | P0 |
| AC-MANIFEST-02 | 基数 | completed/partial：恰好 1 条 final assistant、0..1 个 ArtifactCard、恰好 1 条 final status；`insufficient_evidence`：恰好 1 条 safe-summary final assistant、0 个 ArtifactCard、恰好 1 条 final status | P0 |
| AC-COMMIT-01 | 原子 terminal commit | native 使用现有 `commit_frontier` 在同一 workflow DB 事务中写 terminal checkpoint、run terminal、outbox events/deliveries；崩溃重放不增加 manifest 或 intent 基数 | P0 |
| AC-DELIVERY-01 | durable receipt 语义 | SessionDB projection 成功才计入 required durable receipt；session epoch 不匹配返回 `discarded_fenced`，暂时故障返回 `retryable_failure`，不得静默标 delivered | P0 |
| AC-DELIVERY-02 | WebSocket 定位 | WebSocket 仅为 best-effort 实时投影；无客户端不影响 durable success，也不能单独证明 delivered | P0 |
| AC-DELIVERY-03 | 派生状态 | UI 的 queued/delivering/delivered/retrying 由 required receipt aggregate 查询获得；state、terminal public、SessionDB、UI 不持久化可互相漂移的第三状态 owner | P0 |
| AC-CONTEXT-01 | 历史与上下文分离 | SessionDB 投影携带 `projection_kind/context_visibility`；progress、accepted、decision、final-status、artifact-card 保留给 UI/history 且 `skip_embed`，只有用户消息和 final assistant 进入 conversational context | P0 |

### 3.5 continuation 与控制面

| ID | 验收点 | 可验证条件 | 优先级 |
|---|---|---|---|
| AC-CONT-01 | 原版本 continuation | v5 parent 只生成 v5 child；v6 parent 只生成 v6 child。v6 child 继承同一 spec hash、证据 batch refs、policy hashes 和 provenance | P0 |
| AC-CONT-02 | 单 lineage head | v6 每个 parent 只能有一个 continuation child；repository 通过 CAS/唯一约束原子声明 head，不同 idempotency key 的并发请求也不能形成 sibling heads | P0 |
| AC-CONT-03 | generate-now | generate-now 只停止后续补证并以当前 assessment 进入三道门；0 admitted evidence 必须生成 insufficient safe summary，不能绕过 gate | P0 |
| AC-COMPAT-01 | durable 能力不回退 | checkpoint、lease、effect journal、cancel、generate-now、continue、恢复以及 v1..v5 历史读取保持可用 | P0 |

### 3.6 可观测性与性能诊断

| ID | 验收点 | 可验证条件 | 优先级 |
|---|---|---|---|
| AC-OBS-01 | 全阶段耗时事实源 | v6 每个 durable workflow 节点均保存 `started_at`、`ended_at`、`duration_ms`、attempt 与终态；成功、等待、取消、可重试失败和永久失败都可按 `run_id` 查询 | P0 |
| AC-OBS-02 | 检索/抓取分段耗时 | search provider attempt 与 fetch 的 robots、throttle/static transport、HTTPX、正文抽取、Playwright/Edge/Jina fallback 均输出可聚合耗时和 outcome；同一 run 能定位长尾发生在哪一层 | P0 |
| AC-OBS-03 | 持久化与隐私 | 精确 workflow timing 保存到 durable trace，安全镜像写入 `metrics.jsonl`/结构化日志并进入诊断包；不得记录 query、URL、正文、标题、prompt、cookie 或 token | P0 |
| AC-OBS-04 | 指标正确性 | v6 不产生缺少真实时钟来源或恒为 0 的 stage duration；固定时钟集成测试中 metrics 与 trace duration 一致，遥测写入失败不得改变 workflow 结果 | P0 |

## 4. 固定验收场景

| ID | 输入/故障 | 必须结果 |
|---|---|---|
| SC-STATS-2 | “2024年中国总人口和出生人口分别是多少？优先国家统计局。” | 两个 scalar requirements 均由国家统计局正文 bindings 支持；最终消息给出年末全国人口 140828 万人、全年出生人口 954 万人及各自语义、单位、引用 |
| SC-STATS-4 | 总人口、出生人口、65岁及以上占比、城镇化率 | 同一正文允许产生四个 spans/bindings；结果分别为 140828 万、954 万、15.6%、67.00%；少一项不得 completed |
| SC-SAME-PAGE | 一个页面含多个目标值和 AI 免责声明 | 正文四项可分别绑定；免责声明区域不可获胜，也不得整页误杀 |
| SC-TOPN | 当前最值得关注的 10 个 AI 产品及优缺点 | `CollectionRequirement(min_items=10)` 动态实例化候选；少于 10 项为 partial，不伪造空槽或未知实体 |
| SC-COMPARE | 两个产品按六个轴比较 | 形成 subject×axis matrix；每个 completed cell 有本侧 evidence，推荐区分事实与偏好推断 |
| SC-POLICY | 最新政策方向及影响 | 文件事实包含机关、文件名、日期和原文承诺；影响分析使用 inference claim kind |
| SC-DIRECT-MISS | authority registry 无 seed 或显式 direct 未命中 | 在预算内按 RouteDecision 进入/升级到 official-source search；spec hash 不变，重放不重复消费已 journaled effect |
| SC-NOW-EMPTY | 0 admitted evidence 时 generate-now | 唯一 safe-summary final assistant、无 ArtifactCard、answer status 为 insufficient_evidence |
| SC-CRASH-COMMIT | terminal commit 后、SessionDB 投影前崩溃 | outbox 恢复投递；最终正文、status 和 artifact 基数不增加 |
| SC-FENCED | 旧 session epoch 的 delivery | 旧 run receipt 为 `discarded_fenced` 而非 delivered，且永不隐式 rebind 到新 epoch；在新 epoch 下新建的独立 run/delivery 可成功，不能复用旧 fenced identity 冒充恢复 |
| SC-RECONNECT | final 前后断网、无 WebSocket 客户端、重启并加载历史 | SessionDB durable final 唯一；实时连接变化不改变业务终态；UI 从历史恢复一致 |
| SC-CONTINUE-RACE | 两个不同 idempotency key 并发 continue | 若启用单-head语义，只有一个 child 获得 lineage head，另一个返回已有 child |
| SC-V5-RECOVERY | 恢复现有 v5 checkpoint/continuation | 使用原 v5 manifest 和 adapter 成功恢复，不经过 v6 schema coercion |
| SC-TIMING-SLOW | robots/static/httpx/browser 分别注入可控慢响应与隐藏 retry | 所有真实 attempt 共用同一 page monotonic deadline；page total≤20 秒，未进入的 fallback 无伪造 span，trace 能定位慢阶段 |
| SC-TIMING-CANCEL | page fetch 进行中触发 generate-now/cancel | 已提交页面事实保留，未完成任务收敛为 cancelled/timeout；30 秒 settle target 内进入可重建 assessment，不留下 running span |
| SC-TIMING-REPLAY | 同一 request/effect completion 重放 | child span first-terminal-wins，metrics/log 不重复镜像且 duration 与 durable completion 完全一致 |

以上固定数字只可作为 fixture/test oracle 和人工验收预期，禁止进入生产回答逻辑或 query 特判。

## 5. 非功能验收

- 固定 fixture 的 official exact-fact 自动化路径目标 ≤10 秒；健康真实网络 UI 路径目标 ≤120 秒，query ≤6、fetch ≤8。超时按预算收敛，不无限等待。
- spec、fact batches、claim batches、manifest 和政策版本全部可通过 hash 审计；恢复后相同输入产生相同 assessment 和 intent identities。
- terminal 内容不泄露 raw prompt、cookie、token、异常栈或内部 URL 队列。
- 新能力完成全部验收后按测试阶段规则默认 v6 ON，不设置 shadow/灰度；v5 仍作为旧 run 的恢复版本。
- dirty worktree 中只修改计划或实施任务明确列出的文件；不得 reset/clean/checkout 用户改动。

## 6. 完成定义（DoD）

- 每个 P0 AC 均具备 `AC -> task -> production owner -> automated test -> UI/DB evidence` 追踪；P1 无未解释缺口。
- 新增测试能在旧实现稳定复现“引擎 completed 但无答案”“四指标缺项仍 completed”“同页只能抽一个 span”“terminal ref 悬空/误报 delivered”，并在 v6 转绿。
- v5 identity/recovery、v6 contracts、evidence、assessment、三道门、manifest/outbox、SessionDB context visibility、前端 reducer 均有 scoped 自动化覆盖。
- completed、partial、`insufficient_evidence` 至少各有一个真实 DeskPet UI case；每个 case 有截图、日志、workflow checkpoint/manifest/receipt、SessionDB 交叉证据。
- scoped backend/frontend tests、相关 workflow/retrieval 回归、TypeScript/Vite、Rust check 全部通过，已知无关红项单独记录。
- 实现和验收完成的同一次交付中更新 `ARCHITECTURE/DeepResearch.md`、`ARCHITECTURE/PROJECT_STATUS.md` 与 testcase/results；在此之前不得写“已完成”。
- 完成度审计与测试覆盖审计均为 PASS；否则只能报告 PARTIAL/BLOCKED。
