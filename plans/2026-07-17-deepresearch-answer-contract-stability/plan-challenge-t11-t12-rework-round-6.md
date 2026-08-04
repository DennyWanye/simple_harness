# T11/T12 定向回炉挑战 — Round 6（integration）

> 日期：2026-07-18  
> 范围：增量复核 Round 1/2/3 指出的 integration gaps 与最新 `plan.md` / `v6-contracts.md` 回写；不重复已经闭环的事项。  
> 判定：任何仍会让两个合理实现产生不同 registered bytes、closure、identity、恢复或默认版本行为的 code-level ambiguity 均为 FAIL。

## 结论

本轮回写已经实质闭环 deadline 现有字段映射、空 continuation payload、UUID/start identity、existing-head 短路、production runner crash/restart 门的主体以及 v6/revision6 factory migration方向。但仍有两个 Blocker 和三个 High：generic target/fact 的 cross-field/nullability 尚未唯一化；repair 场景需要两个 LLM profiles，而 snapshot `policy_refs.llm_effect` 只能保存一个，同时 batch 没有可前向遍历的 effect-outcome edge；continuation audit identity/事务、exact-fact generic-core integration gate、invalid config release fallback 也没有固定 oracle。

## Findings

### 1. [Blocker] 无损 target/fact 已补字段，但 cross-field equality、claim-inference binding 和 ranking payload 仍有多种合法编码

**证据**

- `v6-contracts.md:254` 给 `EvidenceBindingV1` 增加了 `target_kind,item_or_cell_id,field_or_facet_key`，但所有 target 仍共享 `parsed_value,normalized_value,canonical_unit,time_scope,scope,definition`，只冻结了 scalar/matrix/collection/policy-open 的 target/key；没有规定 `claim_inference` 时这些值必须 null、是否创建 proposal binding，或只复用 premise bindings。
- `v6-contracts.md:286` 的 collection candidate 含 `unique_key_values,item_id,field_key,...,rank_inputs`，但 `rank_inputs` 没有 exact keys/order/type/nullability；相同 ranking rule 可以编码成 object、ordered array或只保存 metric。
- `v6-contracts.md:298` 的 persisted fact在 top level 和 `semantic_payload` 重复保存 identity：例如 top-level `item_or_cell_id/field_or_facet_key` 与 payload `cell_id/field_key`、`item_id/field_key`、`claim_instance_id/facet_ids`。文档没有逐 tag 要求它们相等，也没有冻结 `claim_fact.claim_kind` 是 requirement 的 `policy_fact` 还是最终 renderer 的 `fact`。
- `v6-contracts.md:304` 需要 fact/inference facet 原样传播并可反向解析；如果上述重复字段不一致，两个 decoder 可分别信任 top-level 或 payload，产生不同 assessment/claim bytes。

**为什么仍阻塞**

字段“存在”不等于唯一语义。strict loader 无法知道不适用字段的合法 null 值、重复 identity 的 authoritative copy，以及 collection ranking inputs 的 canonical representation；Round 1 要求的 registered fact→Generic DTO restart byte equality仍可由两种实现得到不同结果。

**精确修订建议**

1. 在 §6.2 增加 per-tag truth table：top-level target/key、binding value fields、semantic payload必填/null，以及所有重复字段 equality。
2. 建议 inference proposal不产生新的 `EvidenceBindingV1(target_kind=claim_inference)`；`RegisteredInferenceV1.premise_binding_ids` 只引用 admitted premise bindings。若保留 proposal binding，冻结其 value/unit/time/scope/definition全部为 null及其是否进入 premise set。
3. 冻结 `rank_inputs` exact object：keys 恰为 `ranking_rule.metric_key + tie_breakers`，按该顺序取 canonical scalar，missing 用唯一 sentinel/null policy；fact payload必须逐字保留 canonical form。
4. 冻结 equality：matrix top-level cell/key=`semantic_payload.cell_id/field_key`；collection item/key同理；claim fact instance相等且 `field_or_facet_key ∈ facet_ids`。同时明确 persisted `claim_kind=policy_fact`，claim derivation才投影为 `ClaimRecord.claim_kind=fact`。

### 2. [Blocker] Transitive closure 对 repair profiles 与 effect outcome 缺少可表示的前向边

**证据**

- `v6-contracts.md:269` 把 `policy_refs` 冻结为九个**单值** wire refs，其中只有一个 `llm_effect`，并规定 policy ref不能放入 provenance。
- `v6-contracts.md:290` 的 `ResearchLLMEffectProfileV1` identity包含 `role,response_format_hash`，而 shared roles明确分成 `evidence_candidate_extract` 和 `evidence_candidate_repair`。发生 repair 时两个 profile必然是不同 registered blobs，单一 `policy_refs.llm_effect` 无法同时表示。
- `v6-contracts.md:270` 要求从 candidate effect outcome扩展 prompt/raw/repair/profile dependencies；但 `EvidenceFactBatchV1` exact keys（`:302`）只有 `candidate_bundle_refs`，没有 `effect_outcome_refs`。Outcome→bundle 是正向引用（`:292`），从 batch→bundle不能反向唯一找到 canonical outcome；把 outcome随意塞入 batch `provenance_refs` 又没有 exact cardinality/映射规则。
- `ResearchLLMEffectOutcomeV1.dependency_refs`（`:292`）仅规定去重排序，没有按 status冻结 exact set；实现可把 profile/prompt/raw/bundle/repair refs放在 dependency refs的不同子集，得到不同 outcome ID和snapshot hash。

**为什么仍阻塞**

repair run无法构造满足“所有 policy只归 policy、closure恰好集合”的 snapshot；snapshot builder也不能从 top-level refs前向、确定性地找到 selected effect outcome。不同 dependency set还会直接改变 outcome identity。

**精确修订建议**

1. 把 snapshot policies改成可容纳两个 profile的唯一形状，例如 `llm_extract`、`llm_repair` 两个已预注册 wire refs（无 repair也都存在），或 `llm_effect_profiles` 去重排序数组；同步修改 batch policy shape和 closure分类。
2. 在 `EvidenceFactBatchV1` 增加 `candidate_effect_outcome_refs`，按 `(logical_page_id,work_group_id)` 排序且与 selected `candidate_bundle_refs` 一一对应；或者冻结 `provenance_refs` 中必须恰有这些 outcome refs并提供 explicit pair object。不能依赖全库 reverse lookup。
3. 给 `ResearchLLMEffectOutcomeV1` 每个 status/round 写 exact non-null/null/dependency-set truth table。`dependency_refs` 应恰等于该 outcome所有非空 registered dependency refs（排除 outcome自身），不能是任意子集。
4. closure golden必须包含 round0 malformed + round1 validated：两个 profiles、两个 outcomes、两个 raw refs、repair request与最终 bundle；删任一或错分 profile均 fail closed。

### 3. [High] Continuation result要求 durable audit ID，但未冻结 audit identity、owner与 existing-head 事务语义

**证据**

- `v6-contracts.md:314` 规定 caller idempotency key只写独立 audit/control operation。
- `v6-contracts.md:316` 的 `ContinuationCreateResultV1` 又把 `audit_operation_id` 设为 required exact key，但没有派生算法、operation kind、request/result payload或冲突规则。
- `v6-contracts.md:318` 的十个 write/fault points只覆盖新建 child分支；existing-head分支同样必须为新的 caller key返回 audit ID，却没有说明 audit insert是在同一 `BEGIN IMMEDIATE`、另一个事务、还是 best effort。

**后果**

两个 repository connection可得到相同 child但不同/非持久化 audit ID；existing-head返回后若 audit写失败，调用是否算成功也无唯一答案，duplicate caller-key的幂等性无法断言。

**精确修订建议**

冻结 `audit_operation_id=sha256('deepresearch-v6-continuation-audit|'+parent_run_id+'|'+caller_idempotency_key)`（或等价固定算法）、operation kind、canonical request/result JSON。audit row须在同一 `BEGIN IMMEDIATE` 对 created/existing两分支 insert-or-validate；同 caller key重放返回同 audit ID，不同 key返回不同 audit ID但相同 child。为 existing-head audit insert增加 fault point并规定失败全事务 rollback/不 notify。

### 4. [High] Production integration gate覆盖四个 generic intents，但未证明 Q1 exact fact已经迁移到同一 final batch/gates core

**证据**

- `plan.md:507` 明确要求 Q1 exact path迁移到同一 generic core。
- `plan.md:509` 的不可替代 production gate只列 comparison/Top-N/policy/open 四条；exact fact只剩 `budget/fan-out off` helper断言。
- 同一计划要求 final reader hard reject Q1 provisional fact-batch shape（`v6-contracts.md:257`）。若 exact production graph仍走旧 exact-only node/persistence，只测四个 generic intents也可让 T11错误标绿。

**精确修订建议**

把 official exact fact加入同一个 bootstrap/registry/real WorkflowRunner gate：无 LLM call、fan-out off，但必须经过九节点中共用的 admit/assess/render/integrity/persist owners，产出唯一 release `EvidenceFactBatchV1` 与 §6.1 closure。至少在 fact-batch commit后 crash/restart，断言 provisional decoder/旧 exact persistence入口未被调用；T13 SC-STATS-2真 UI不能替代该结构 oracle。

### 5. [High] v6/revision6 migration方向已冻结，但 invalid configured version 的 release行为仍无 expected result

**证据**

- `plan.md:568` 与 `v6-contracts.md:449` 要求覆盖 `invalid-config`，但只说明 resolver无 env时接受 configured v6，没有规定 configured空值/未知值应 fallback v6、fallback v5还是 fail closed。
- 当前实现的 invalid fallback是 v5（`backend/config.py:37-43`），而 T13后 factory default为 v6。若只把 allowlist加上 v6但保留旧 fallback，fresh/default测试可通过，损坏配置的已有安装却悄然回到 v5，违背默认 ON。

**精确修订建议**

冻结 invalid oracle。建议 release resolver对空/未知 configured值回退 factory `v6` 并返回低基数 reason=`configured_invalid_factory_default`；只有显式合法 v5 pin才选择 v5。增加无 env的 `''/v7/garbage`常量测试，以及 persisted old run仍按 DB version v1..v5恢复的独立测试。

## 本轮确认闭环、不再回炉

- 900 秒已统一到 schema v4 `created_at/last_observed_at/wall_not_after`，并冻结 T+895/T+900 control公式（`v6-contracts.md:322`、`plan.md:553`）。
- continuation policy 1已明确 empty payload、UUID golden、start operation/logical slot/request hash与 existing-head-before-pin顺序（`v6-contracts.md:312-320`）。
- retention component root、fail-closed decode和单事务后序删除算法已可执行（`v6-contracts.md:324`）。
- T13 factory pair v6/revision6、inherited v5/revision5迁移、explicit pin与 committed release fixture方向已闭环；只剩 Finding 5 的 invalid oracle。

VERDICT: FAIL
