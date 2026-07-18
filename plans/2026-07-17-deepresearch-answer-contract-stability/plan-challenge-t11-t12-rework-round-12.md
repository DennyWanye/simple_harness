# T11/T12 定向回炉挑战 — Round 12（narrow integration）

> 日期：2026-07-18  
> 范围：只复核 Round 9 唯一 Blocker、双 frontier forward closure，以及新增 `assessment_input_hash` 与 continuation snapshot 的一致性。  
> 判定：任一 schema/status 组合无法编码，或仍可产生不同 head/snapshot bytes，即 FAIL。

## 结论

Round 9 的直接 Blocker已经闭环：proposal premise union、ordinal、selected profile与 empty bundle均冻结；facts/inferences batch有完整 per-kind truth table；producer/effect outcomes成为显式前向边；assessment input hash能从 ordered batch chain重算并与 continuation snapshot交叉验证。

但窄复核发现一个新的 schema-level Blocker：计划要求把 `status=rejected` 的 inference records全部纳入 inference batch/closure，同时 `RegisteredInferenceV1` 又无条件要求 `premise_binding_ids` 非空。对 `inference_premise_missing`，尤其所有 premise ref都不存在/不可解码时，系统无法生成符合 exact schema 的 rejected record。实现者只能丢弃 rejected record、伪造 binding ID或违反 non-empty约束，三者都会改变 batch、assessment input hash和snapshot closure。

## Finding

### [Blocker] `inference_premise_missing` 无法编码为当前 exact `RegisteredInferenceV1(status=rejected)`

**证据**

- `v6-contracts.md:319` 规定 `RegisteredInferenceV1.status=registered|rejected`，并无条件要求 `premise_fact_refs` 与 `premise_binding_ids` 两个数组都非空。
- 同一行又规定 proposal premises不全由 input fact head可达、或无法由它们确定 binding IDs时，产生 `inference_premise_missing` rejected record。
- 当 proposal只引用一个不存在、损坏或不属于该 head的 fact ref时，validator可以保留非空 `premise_fact_refs`，但没有任何合法 `premise_binding_id` 可填；exact schema因此不可满足。
- `v6-contracts.md:327` 要求 `registered_inference_refs` 包含本 frontier **全部** registered/rejected records，rejected reason仍须进入 closure。不能通过“不持久化这个 rejection”规避。
- `v6-contracts.md:323` 的 `assessment_input_hash`覆盖 ordered inference refs，snapshot又在 `:267,273` 保存并重算该 hash；是否丢弃不可编码的 rejection会直接改变 assessment/snapshot identity。

**精确修订建议**

冻结 status truth table：

- `status=registered`：`premise_fact_refs` 与 `premise_binding_ids` 均非空，后者恰由前者逐 ref解码得到；`reason_codes=[]`。
- `status=rejected`：`premise_fact_refs` 必须原样保存 proposal 的非空 sorted refs；`premise_binding_ids` 恰为其中可解码且属于 input head的 binding IDs，可为空；`reason_codes` 非空，missing/corrupt/not-reachable统一或分别使用冻结低基数 code，首个失败规则固定。
- rejected record仍进入 `registered_inference_refs`，assessment input hash覆盖它，但 decoder不把它加入 supported input。

增加固定 oracle：proposal仅引用一个不存在 ref，生成 `status=rejected,premise_binding_ids=[]` 的 registered blob；restart后 inference batch/head、ordered inference refs、assessment input hash、assessment hash与 continuation snapshot hash完全一致，且 claim不可见。

## 本轮确认闭环

- `InferenceProposalBundleV1.premise_fact_refs` 已等于 proposal union；empty proposals对应 empty union；ordinal和 profile equality均唯一（`v6-contracts.md:293-295`）。
- 双 batch的所有业务 arrays已按 kind冻结，registered/rejected records统一有 batch owner（`v6-contracts.md:325-329`）。
- Snapshot从 batch slots前向遍历 outcome/bundle/fact/inference，不依赖 reverse lookup（`v6-contracts.md:269-273,329`）。
- `assessment_input_hash`覆盖 spec、final evidence head、ordered facts、ordered inference records与assessment policy；snapshot复制并从 batch chain重算，没有循环或双 owner（`v6-contracts.md:258,267,273,321-323`）。

VERDICT: FAIL
