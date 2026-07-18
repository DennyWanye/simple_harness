# T11/T12 定向回炉挑战 — Round 13（final narrow integration）

> 日期：2026-07-18  
> 范围：只复核 Round 12 唯一 Blocker：registered/rejected inference premise schema、batch/closure归属及assessment过滤。  
> 判定：missing/corrupt/unreachable premise必须能生成唯一合法 rejected record，且不能把无效 ref提升进snapshot closure。

## 结论

Round 12 的唯一 Blocker已经闭环，未发现新的 code-level ambiguity。`RegisteredInferenceV1` 现在把模型原提议的 refs 与成功解析的 refs/bindings分离，并按 status冻结非空/可空规则；所有 registered/rejected records都由 inference batch持有；snapshot只沿 resolved premises扩张；assessment input hash审计全部 records但 supported input明确排除 rejected。missing、corrupt、unreachable premise均能得到合法、可重放且不会污染 continuation closure 的 rejected record。

## 核对结果

### 1. Missing/corrupt/unreachable premise 可合法编码

- `v6-contracts.md:319` 的 exact schema新增 `proposed_premise_fact_refs`，逐字保存 proposal 的非空 refs；`premise_fact_refs/premise_binding_ids` 只保存从 input fact head成功解析、同 requirement的子集。
- `v6-contracts.md:321` 冻结 status truth table：registered要求 proposed=resolved且 refs/bindings非空；rejected允许 resolved refs/bindings为空并要求非空 reason codes。
- 因此单一 syntactically valid但 missing/corrupt/unreachable ref可编码为：proposed非空、resolved/bindings空、`status=rejected,reason_codes=['inference_premise_missing']`，不需要伪造 binding或丢弃 record。
- Requirement mismatch和semantic invalid也有冻结 reason code，不会与 missing分支临场合并成不稳定异常文本。

### 2. Rejected record 有 durable owner，但 invalid premise 不进入 snapshot closure

- `v6-contracts.md:329` 要求 inference batch的 `registered_inference_refs`包含本 frontier全部 status registered/rejected records，按 inference ID排序；rejected blob因此有稳定 batch/head/checkpoint owner。
- `v6-contracts.md:321` 明确 snapshot只沿 resolved `premise_fact_refs`扩张；proposed invalid/unreachable字符串仅保留在 registered inference audit bytes。
- `v6-contracts.md:269-271` 的 typed forward expansion从 batch→registered inference blob，再仅沿其合法 resolved facts/bindings；closure equality不会把 proposed invalid digest当作可提升 blob，也不会遗漏 rejected record本身。
- continuation server继续只继承经过 terminal checkpoint owner验证的 exact closure；invalid proposed string不可能获得 child staging/checkpoint owner。

### 3. Assessment过滤与 identity 一致

- `v6-contracts.md:323` 明确 rejected inference不进入 `GenericAdmittedInferenceV1` supported input或claim，只有 status registered可供assessment/claim。
- ordered inference refs仍包含 batch中的全部 records，因此 rejected reason通过 ref进入 `assessment_input_hash`审计；它不贡献coverage/support，两层语义不冲突。
- `assessment_input_hash`由 final evidence head、ordered facts、ordered all-record inference refs及policy重算，并复制进 continuation snapshot；restart/continuation不会因过滤 rejected而改变输入 identity。

## 最终判断

Round 12要求的 status truth table、all-record batch归属、resolved-only closure expansion和assessment rejected过滤已形成单一可执行实现与固定测试 oracle。可以结束本轮定向 plan challenge并返回后续执行阶段。

VERDICT: PASS
