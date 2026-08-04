# T11/T12 定向回炉挑战 — Round 9（integration）

> 日期：2026-07-18  
> 范围：只增量复核 Round 6 findings 的回写，以及新增 fact-first / inference-second 链与 snapshot closure 的一致性。已闭环项不重复挑战。  
> 判定：仍存在会改变 bundle/batch/head/snapshot bytes 的合理实现分歧即 FAIL。

## 结论

Round 6 的五项直接缺口已经闭环：per-tag truth table/rank inputs、三个 profile与 outcome dependency truth、durable audit、exact-fact real runner generic-core gate、invalid configured fallback均已形成可执行 oracle。新增的 fact-first/inference-second 架构方向也正确，并消除了 page-local inference 与事实 admission 混写。

但该新架构自身还留下一个 Blocker：`InferenceProposalBundleV1` 的重复 premise/profile/ordinal identity没有冻结 equality/派生，`EvidenceFactBatchV1` 的 facts/inferences 两种 batch 也没有完整 truth table。相同 fact head 可以生成不同 proposal bundle、inference batch与最终 evidence head，继而改变 assessment和 continuation snapshot hash。本轮仍不能 PASS。

## Finding

### [Blocker] Fact-first / inference-second 的 bundle 与双 batch canonicality 尚未唯一化

**证据**

- `v6-contracts.md:292` 的 `InferenceProposalBundleV1` 同时保存 top-level `premise_fact_refs` 与每个 proposal 的 `premise_fact_refs`，只要求前者可达、两者各自排序非空，没有要求 top-level 恰等于所有 proposal premises 的集合。一个实现可保存整个 fact head，另一个只保存实际使用的 union；两者都满足当前文字但 bundle ID不同。
- 同一对象的 `ordinal` 没有派生规则；可合理取 route work-group ordinal、仅有 inference 的局部 ordinal或 completion order。`profile_ref` 也没有明确要求逐字段等于 selected `ResearchLLMEffectOutcomeV1.profile_ref`（normal inference或repair profile），会让 bundle identity与 effect closure脱节。
- `v6-contracts.md:320` 只冻结 facts batch 的 candidate slots/inference slots，以及 inference batch 的 page/candidate/admitted arrays；未冻结其余 exact arrays：
  - facts batch 的 `registered_inference_refs` 是否必须空；
  - inference batch 的 `rejected_candidate_ids/conflict_ids` 是否必须空或复制历史事实结果；
  - `registered_inference_refs` 是包含所有 `RegisteredInferenceV1` records（含 `status=rejected`），还是只含 `status=registered`。
- `v6-contracts.md:316-318` 明确 `RegisteredInferenceV1.status=registered|rejected` 且 assessment排除 rejected，但没有给 rejected record唯一的 batch/closure归属。如果不进入 `registered_inference_refs`，实现可把它放进 `provenance_refs`或完全不持久化；如果进入，字段名又可被理解为仅 registered status。
- `v6-contracts.md:270` 的 snapshot expansion依赖每个 inference slot/outcome/bundle及 registered inferences。上述不同选择会产生不同 `provenance_refs/closure_refs`，所以不是仅命名问题。

**精确修订建议**

1. 冻结 `InferenceProposalBundleV1`：
   - `premise_fact_refs == sorted(unique(union(proposal.premise_fact_refs)))`，不得包含未被任何 proposal使用的 head fact；空 proposals时 top-level也必须空，并以 canonical empty bundle表示 honest no-inference outcome；
   - `ordinal` 恰等于该 work group在 persisted `RouteDecisionV1.work_groups` stable order中的 ordinal；
   - `profile_ref == selected effect outcome.profile_ref`，repair成功时为 `llm_repair`，且 `input_evidence_head_hash` 必须等于 facts frontier提交后的 head。
2. 给 `EvidenceFactBatchV1` 增加完整 per-`batch_kind` truth table：
   - `facts`：candidate slots/page refs/admitted facts/rejected candidates/conflicts为本 frontier exact结果；`inference_slot_results=[]`、`registered_inference_refs=[]`；
   - `inferences`：`page_result_refs=[]`、`candidate_slot_results=[]`、`admitted_fact_refs=[]`、`rejected_candidate_ids=[]`、`conflict_ids=[]`；inference slots完整。
3. 建议把 `registered_inference_refs` 明确定义为**所有**本 frontier 生成的 `RegisteredInferenceV1` record refs（包含 status registered/rejected，按 inference ID排序）；assessment只消费其中 registered，rejected仍保留 reason/audit/closure。若只保留 successful refs，则必须新增 `rejected_inference_refs`，不能把 rejected records随意塞进 provenance。
4. 增加固定 oracle：相同 facts head含一个未使用 fact、一个 registered proposal、一个 missing-premise rejected proposal；restart前后必须得到同一 proposal bundle ID、两批 head、registered/rejected record set、assessment hash与 snapshot closure。expected数据不得调用 production builder生成。

## 本轮确认闭环

- `EvidenceBindingV1` 已移除 inference binding；fact target equality、claim-kind投影和 collection `rank_inputs` 已冻结（`v6-contracts.md:254,294-296`）。
- snapshot policies已拆为 extract/inference/repair三个 profile，effect outcome按 round/status冻结 dependency set，batch slots提供 outcome前向边（`v6-contracts.md:269-270,298-322`）。
- continuation caller audit identity、request/result payload、same/different-key语义与两分支 fault points已冻结（`v6-contracts.md:334-340`）。
- official exact fact已进入 real WorkflowRunner generic-core gate，LLM fail-on-call/budget 0且禁止旧 Q1 persistence旁路（`plan.md:509`）。
- invalid configured version已固定回退 factory v6，并记录 `configured_invalid_factory_default`（`plan.md:568`、`v6-contracts.md:473`）。

VERDICT: FAIL
