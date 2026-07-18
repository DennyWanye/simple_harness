# T11/T12 定向回炉挑战 — Round 7（T11 最终增量复核）

> 范围：只复核 Round 4 四个 blocker及本轮新增的 candidate producer、fact/inference 两阶段、三个 LLM profiles、assessment projection、batch slot前向边和11节点；不重复已闭环事项。

## 复核结论

Round 4 的主体架构缺口已基本闭环：

- **exact budget=0 仍走 generic core：PASS。** `CandidateProducerOutcomeV1` 已区分 deterministic/LLM，exact fail-on-LLM且仍产生 registered outcome/bundle（`v6-contracts.md:278-282`）；T11 production gate也包含 official exact（`plan.md:507-509`）。
- **fact-first、global inference-second：PASS。** page-local bundle只允许四种 fact tags，fact head checkpoint后才创建 `InferenceProposalBundleV1`；11节点明确包含 `synthesize_inferences/register_inferences`（`v6-contracts.md:284-292,326`）。
- **三个 profile：PASS。** extract/inference/repair role、tool/schema、repair能力和 snapshot policy slots均独立（`v6-contracts.md:269,298-310`），plan要求三组 prepared golden（`plan.md:509`）。
- **batch slot前向边：PASS。** facts batch显式保存 producer outcome→bundle slot，inference batch保存 effect outcome→proposal bundle slot，snapshot不再依赖 reverse lookup（`v6-contracts.md:320-322`）。
- **RegisteredInference assessment mapping主体：PASS。** `GenericAdmittedInferenceV1`、policy/open mapping、matrix coverage隔离与 rejected过滤均已冻结（`v6-contracts.md:318`）。

仍有两个会让两个合理实现产生不同 registered bytes/assessment identity 的窄 Blocker，因此本轮不能 PASS。

## Findings

### 1. [BLOCKER] `InferenceProposalBundleV1.proposal` 缺少 `requirement_id`，无法唯一构造 `RegisteredInferenceV1`

**证据**

- proposal exact keys只有 `proposal_id,inference_kind,item_or_cell_id,facet_ids,normalized_proposition,premise_fact_refs,model_id,model_policy_ref`（`v6-contracts.md:292`）。
- 目标 persisted owner `RegisteredInferenceV1` 必填 `requirement_id`（`v6-contracts.md:316`）。
- proposal bundle只有 `work_group_id`；而 `RouteDecisionV1.work_group.requirement_ids` 是数组，不保证一个 group只含一个 requirement（`v6-contracts.md:112` 的 work-group contract）。
- 最新 contract只要求 premise refs由 input head可达，没有要求它们属于同一 requirement，也没有冻结从 work group还是premises派生 requirement identity（`v6-contracts.md:292,316`）。

**影响**

实现A可从第一条 premise fact复制 requirement，实现在跨 requirement premises时仍成功；实现B可要求整个 work group只有一个 requirement；实现C可按 item/facet反查 spec。三者生成不同 `RegisteredInferenceV1.inference_id`、assessment results和snapshot hash。

**精确改法**

1. 给 inference proposal exact keys增加 `requirement_id`，proposal ID覆盖它。
2. 注册前要求：该 ID属于 `work_group.requirement_ids`；所有 `premise_fact_refs` 的 persisted `requirement_id` 与 proposal相同；`item_or_cell_id/facet_ids/inference_kind` 被该 requirement允许。
3. 跨 requirement synthesis若未来需要，必须升 schema并使用显式 ordered `premise_requirement_ids`，本 v1 fail closed，稳定 reason=`inference_premise_requirement_mismatch`。
4. 增加一个 work group含两个 requirements的负测，证明不能用first-premise或first-group-item隐式选择。

### 2. [BLOCKER] “assessment input hash覆盖 refs”没有 exact 字段、公式或 snapshot owner

**证据**

- `v6-contracts.md:318` 要求 assessment input hash覆盖 ordered fact refs、inference refs和assessment policy hash。
- `AnswerAssessmentV1` exact keys仍只有 `assessment_id,assessment_hash,spec_hash,evidence_head_hash,policy_hash,...`，没有 `assessment_input_hash`（`v6-contracts.md:258`）。
- `ResearchContinuationSnapshotV1` exact keys也只有 `assessment_ref,assessment_hash`，没有 input hash（`v6-contracts.md:267`）。
- plan仍要求把 `assessment input hash` 纳入 v6 snapshot codec和恢复校验（`plan.md:295`）。

**影响**

实现A可能把 `evidence_head_hash`当作 input hash；实现B另算但只放checkpoint cache；实现C把refs直接混入 `assessment_hash`。它们都能声称“覆盖 inputs”，但 persisted assessment/snapshot bytes和cache invalidation oracle不同。

**精确改法**

冻结并持久化唯一值：

```text
assessment_input_hash = sha256(canonical_json({
  schema_version: 1,
  spec_hash,
  evidence_head_hash,
  ordered_fact_refs,
  ordered_inference_refs,
  assessment_policy_hash
}))
```

- `ordered_fact_refs/ordered_inference_refs` 必须从 ordered batch chain按 batch内 canonical ref顺序展开，禁止caller自选顺序。
- 把 `assessment_input_hash` 加入 `AnswerAssessmentV1` exact keys并参与 `assessment_hash/assessment_id`；同时加入 `ResearchContinuationSnapshotV1` exact keys并与assessment blob逐字段交叉验证。若决定只用 `spec_hash+evidence_head_hash+policy_hash`，则删除“覆盖 ordered refs”的独立 input-hash要求并明确该三元组就是唯一 cache key，不能保留两种解释。
- 增加 restart oracle：交换 refs顺序、少一个 inference ref、policy hash变化均使 input hash变化并触发重算/fail closed；相同 batch chain得到字节相同 assessment/snapshot。

## Round 7 判定

Candidate producer、跨页 inference阶段、三个 profiles、batch前向边和11节点已经可以按唯一主链实施，不再需要结构性回炉。只需补齐 proposal的 requirement identity与 assessment input hash owner/公式后再做一次窄复核；在此之前 inference ID和assessment snapshot仍无法达到100% code-executable。

VERDICT: FAIL
