# T11/T12 定向回炉挑战 — Round 10（T11 窄复核）

> 范围：只复核 Round 7 的两个 blocker，并核对它们与 Round 9 的 proposal bundle / 双 batch truth table 是否一致；不重复旧项。

## 结论

Round 7 两项均已闭环，且没有与 Round 9 修订产生新的 registered-bytes、batch-head 或 snapshot-hash 分歧。本轮范围可以 PASS。

## 1. Inference proposal requirement identity — PASS

最新契约已经冻结：

- `InferenceProposalBundleV1.proposal` exact keys显式包含 `requirement_id`，且 proposal ID覆盖该字段（`v6-contracts.md:295`）。
- requirement必须属于 persisted work group；全部 premise fact refs的 persisted requirement ID必须相同并等于 proposal requirement（同上）。
- cross-requirement premises在 v1 使用稳定负向 oracle `inference_premise_requirement_mismatch` fail closed，不再允许从第一条 premise、work-group第一项或 spec反查临场选择（同上）。
- `RegisteredInferenceV1.requirement_id` 因而只能逐字段复制已验证 proposal identity；premise refs仍必须由 `input_evidence_head_hash` 可达，并确定 premise binding IDs（`v6-contracts.md:293,319`）。

与 Round 9 一致性：

- bundle top-level `premise_fact_refs` 必须恰等于所有 proposal premise refs的 sorted unique union，不能夹带未使用 facts（`v6-contracts.md:293`）。
- 因每个 proposal自身先通过 same-requirement检查，union只负责 closure完整性，不会重新成为 requirement owner。
- rejected inference仍作为 `RegisteredInferenceV1` record进入 inference batch closure，assessment只消费 status=registered；negative oracle不会丢失审计记录（`v6-contracts.md:319,327`）。

因此，相同 work group、fact head和proposal bytes只能生成一个 requirement identity与 inference ID。负测 expected result可直接固定为上述稳定 reason code，无需调用 production helper计算。

## 2. Assessment input hash identity / owner / snapshot cross-check — PASS

最新契约已经冻结全部四层：

1. **Exact formula**：

   `sha256(canonical_json({schema_version:1,spec_hash,evidence_head_hash,ordered_fact_refs,ordered_inference_refs,assessment_policy_hash}))`（`v6-contracts.md:323`）。

2. **Canonical ordering owner**：ordered refs只能从 batch chain ordinal展开，并使用每个 batch的 canonical ref顺序；caller不能重排（同上）。Round 9 truth table又固定 facts/inferences batch中哪些 ref arrays必须为空、哪些包含完整 frontier records，以及 registered/rejected inference refs按 inference ID排序（`v6-contracts.md:325-329`）。

3. **Assessment owner**：`assessment_input_hash` 已加入 `AnswerAssessmentV1` exact keys，并参与 assessment hash/ID；load重算 semantic/blob identity（`v6-contracts.md:258,323`）。

4. **Continuation owner**：该字段已加入 `ResearchContinuationSnapshotV1` exact keys；snapshot必须与 assessment blob逐字段相等，并从 ordered batch chain按同一公式重算，不一致 fail closed（`v6-contracts.md:267,273`）。

交换或缺少 fact/inference ref、改变 policy hash都会产生不同 input hash并触发重算；相同 ordered双 batch chain则得到字节相同 assessment与snapshot。Round 9 将 rejected inference records保留在 batch/closure、只在 assessment projection中过滤，与 input hash覆盖完整 durable input set的语义一致。

## Round 10 判定

Inference proposal requirement identity、same-requirement premise负向 oracle、assessment input hash公式/字段/owner，以及 snapshot cross-check均已达到 code-level executable；与 bundle premise union、facts/inferences batch truth table和前向 closure边一致。本轮无新增 blocker或 high ambiguity。

VERDICT: PASS
