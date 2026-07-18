# Plan challenge — T11/T12 rework round 1

> Scope: read-only Phase 2 audit of the revised T11 and normative `v6-contracts.md` §6.2 against the current v6 production graph, nodes, adapters, contracts, store ownership model, and tests. This report does not treat the expected absence of T11 implementation as a defect by itself; it fails the round only where the frozen plan still leaves incompatible or non-deterministic implementation choices.

## Verdict summary

T11 is not yet 100% code-executable. The revision correctly freezes the intended owner chain and explicitly rejects the Q1 exact-only shortcut, but seven contract gaps remain. Four are blockers: persisted facts cannot reconstruct the assessment DTO, matrix/collection IDs have two incompatible formulas, the durable LLM result/repair journal has no exact v6 outcome and ownership contract, and fact-batch ownership rules reject the effect-owned page results they are required to consume.

## Findings

### 1. [BLOCKER] `AdmittedResearchFactV1` cannot reconstruct the sole assessment DTO

Evidence:

- `v6-contracts.md:294` freezes a persisted fact with `item_or_cell_id`, one `normalized_value`, and source metadata, then requires `GenericAdmittedFactV1` to be decoded only from that blob.
- `backend/deskpet/workflows/definitions/deep_research_v6_assessment.py:50-61` requires additional semantic inputs: `axis_member_ids`, collection `entity_values`, `field_key`, `value`, `claim_kind`, `facet_ids`, `inference_ref`, `as_of`, and `conflicted`.
- Matrix assessment consumes exact axis members at `deep_research_v6_assessment.py:181-211`; collection dedupe/ranking consumes unique-key values, field keys, values, and as-of at `deep_research_v6_assessment.py:236-318`; claim-set assessment consumes claim kind, facet IDs, and inference refs at `deep_research_v6_assessment.py:358-400`.

Why this blocks implementation: the registered fact schema does not contain enough data to deterministically rebuild the existing generic assessor input. An implementer must either invent hidden lookups into candidate bundles or persist a second DTO, both violating the frozen single-owner rule.

Required contract change:

- Make the persisted fact payload a strict tagged union by `fact_kind`, or add one strict `semantic_payload` tagged union:
  - scalar: `value`;
  - matrix: `axis_member_ids,cell_id,value,claim_kind`;
  - collection: `item_id,normalized_unique_key_values,field_key,value,as_of,rank_inputs`;
  - claim fact: `claim_instance_id,claim_kind,facet_ids,value`.
- Freeze conflict representation (`status` or `conflict_ids`) and the exact `AdmittedResearchFactV1 -> GenericAdmittedFactV1` decoder. Add a round-trip oracle proving that assessment reconstructed only from registered fact/inference refs is byte-identical before and after restart.

### 2. [BLOCKER] Matrix-cell and collection-item identities have incompatible frozen formulas

Evidence:

- `v6-contracts.md:64` defines a matrix cell from `requirement_id + ordered member ids`; `v6-contracts.md:284` instead defines it from the object `{requirement_id,subject_key,axis_key}`.
- `v6-contracts.md:74` defines a collection item from `requirement_id + normalized unique-key values`; `v6-contracts.md:285` instead hashes `{requirement_id,item_unique_key}` without freezing the NFKC/casefold normalization or multi-column ordering.
- Production helper formulas already follow the earlier contract: `deep_research_v6_assessment.py:131-149` hashes an ordered list and normalizes strings with NFKC/casefold.

Why this blocks implementation: admission can accept a candidate ID that assessment later derives differently. The same evidence then becomes a different cell/item after restart or is rejected as an identity mismatch.

Required contract change:

- Declare `derive_matrix_cell_id()` and `derive_collection_item_id()` once as normative algorithms and reference them from §2 and §6.2.
- Matrix candidates must carry the complete ordered `axis_member_ids[]`, not the special-case `subject_key/axis_key`, unless v1 explicitly restricts matrix to exactly those two roles.
- Collection candidates must carry unique-key values in `item_schema.unique_key` order; normalize each with `nfkc_casefold_v1` before hashing. Freeze golden vectors for Unicode/case variants and multi-field keys.

### 3. [BLOCKER] The v6 durable LLM effect has no executable result/repair journal contract

Evidence:

- `v6-contracts.md:288-290` freezes a profile and says raw output, canonical bundle, repair input, failure, replay, and late results enter the durable closure, but it defines no exact `ResearchLLMEffectOutcomeV1`, no result-kind/status enum, no media types, no ref ownership transition, and no canonical selection rule between round 0 and round 1.
- The existing adapter accepts only the v5 role set (`backend/deskpet/workflows/definitions/research_core.py:134-142`) and selects only v5 structured response formats (`backend/deskpet/workflows/adapters/research_runtime.py:257-262`). Its prepared call hardcodes v5 identity at `research_runtime.py:731-757`.
- Its durable success outcome currently contains only `result_ref,role,model,usage_source,usage,request_id` (`research_runtime.py:1015-1034`), and `_result_from_outcome()` decodes that ref as the v5 `ResearchLLMResult` wrapper (`research_runtime.py:759-773`). There is no candidate-bundle/failure/repair closure reader.

Why this blocks implementation: parameterizing three strings is insufficient. There is no frozen way to distinguish raw provider bytes from a validated bundle, recover a malformed round-0 result, select a repaired result, or authorize those refs after the node task owner disappears.

Required contract change:

- Add an exact v6 profile identity derivation (`profile_id/hash/ref`) and a v6 extraction role/response-format contract; state whether the shared role enum is extended or the adapter accepts a profile-owned role/schema.
- Define exact `ResearchLLMEffectOutcomeV1` keys, for example: `status=validated|malformed|opaque_uncertain|deadline|budget_denied`, `profile_ref`, `prompt_ref`, `raw_result_ref`, `candidate_bundle_ref`, `repair_input_ref`, `repair_round`, `reason_codes`, `dependency_refs`.
- Freeze the transaction that promotes prompt/raw/bundle/repair/policy refs to the effect owner, the recovery reader, and the rule that a valid repair-round-1 result supersedes only a malformed round-0 result while both remain in provenance.
- Freeze the repair request JSON exact keys/ID/hash/media type, not only a prose list of five inputs. Add fault oracles at provider return -> raw put -> outcome commit -> node checkpoint.

### 4. [BLOCKER] Fact-batch authorization contradicts the page-effect closure contract

Evidence:

- `v6-contracts.md:229` requires committed search/page closures to lose staging ownership and retain `owner_kind='effect'`; checkpoint ownership is only additive later.
- `v6-contracts.md:298` requires every final fact-batch ref to be readable through only current-run staging/checkpoint ownership. The batch must simultaneously include `page_result_refs`, which are effect-owned after canonical page commit.
- The current graph's owner checker explicitly recognizes effect ownership (`backend/deskpet/workflows/definitions/deep_research_v6_nodes.py:113-139`), confirming that this is a real production ownership class rather than an abstract concern.

Why this blocks implementation: a correct canonical page result fails the revised batch validator before it can be promoted into the next checkpoint.

Required contract change:

- Freeze authorization per ref class: page/search result and their transitive inputs must have a same-run canonical effect owner; newly created candidate/fact/inference/policy blobs may have current-node pending/staging ownership; historical batch refs require current-run checkpoint ownership.
- Define the `admit_evidence` frontier promotion set and exact closure equality check. The fact-batch blob itself and all newly admitted refs must be promoted atomically with the new head, while canonical effect owners remain intact.

### 5. [BLOCKER] Candidate-to-fact/inference mapping is under-specified for policy and open claims

Evidence:

- `v6-contracts.md:281-286` allows candidate claim kinds `fact|inference|preference|limitation|counterevidence|uncertainty`; there is no `conclusion` tag and no exact mapping from generic `inference` to `impact|comparison|conclusion`.
- `v6-contracts.md:296` requires `RegisteredInferenceV1.inference_kind=impact|comparison|preference|conclusion|limitation|counterevidence|uncertainty` and adds `model_id/model_policy_ref`, but does not say which durable result supplies them or how premise facts/bindings are chosen.
- `v6-contracts.md:300` requires all open conclusions and policy impacts to resolve to the matching registered inference, so the missing mapping cannot be deferred to the renderer.
- The policy compiler requires four `policy_fact` minima in one claim-set (`backend/deskpet/workflows/definitions/deep_research_v6_compiler.py:610-659`), while the candidate payload has no exact `policy_field` discriminator for issuer/document/date/commitment.

Why this blocks implementation: two conforming implementations can register different inference kinds or treat one composite policy candidate as one versus four facts, producing different assessment and answer status from the same bundle.

Required contract change:

- Add a strict candidate discriminator such as `claim_semantic_kind=policy_issuer|policy_document|policy_date|policy_commitment|impact|comparison|preference|conclusion|limitation|counterevidence|uncertainty` with per-tag null/required rules.
- Freeze candidate -> admitted fact / registered inference mapping, premise selection, `model_id/model_policy_ref` source, claim-instance ID, facet propagation, and rejection reason when premises are absent.
- Add golden policy/open bundles that reconstruct four policy facts plus a separate impact inference, and conclusion/limitation/counterevidence/uncertainty as four independently assessed registered inferences.

### 6. [HIGH] Route provenance has an outcome requirement but no replacement port contract

Evidence:

- `v6-contracts.md:302` freezes a separate `plan_route` node and requires `load_pages` to carry registered route decision/policy refs into provenance.
- The current runtime owns a `RouteDecisionV1` only inside `DeepResearchV6RetrievalResult` (`backend/deskpet/workflows/adapters/deep_research_v6_evidence_runtime.py:88-100`), while `load_pages()` deliberately returns only `.pages` (`deep_research_v6_evidence_runtime.py:550-557`).
- The graph-facing port also returns only a sequence of page refs (`backend/deskpet/workflows/definitions/deep_research_v6_nodes.py:75-83`), and `collect_pages_handler` persists no route refs (`deep_research_v6_nodes.py:364-400`).

Why this remains ambiguous: §6.2 says what must survive but not which production owner creates/persists the route refs or what the `load_pages` input/output becomes.

Required contract change:

- Freeze one design. Recommended: `plan_route` registers `route_policy_ref/hash` and `route_decision_ref/hash` before any retrieval; state carries those four pointers; `load_pages` consumes the immutable decision ref and returns only canonical page-attempt refs. Remove route recomputation from `retrieve()`.
- Alternatively define an exact registered retrieval-envelope return type and checkpoint it before extraction. In either design add restart oracle: changing current capability/source health cannot change the persisted decision or provenance.

### 7. [HIGH] LLM budget and deterministic batch boundary are not frozen

Evidence:

- T11 says the v6 profile must use its own budget (`plan.md:505`) and §6.2 says extraction counts against the LLM budget (`v6-contracts.md:302`), but neither defines the unit, whether repair consumes a second unit, reservation/release/uncertain accounting, or interaction with token/cost reservations.
- The current v6 route snapshot sets `llm=0` (`backend/deskpet/workflows/adapters/deep_research_v6_evidence_runtime.py:583-589`), and the v6 runtime context exposes only `fetch` and `blob` ports (`backend/main.py:3973-3995`).
- `v6-contracts.md:292` freezes a global candidate ordering and `v6-contracts.md:298` adds a batch ordinal/head, but it does not freeze how ordered candidates are partitioned into batches, which canonical bundle is selected per `(work_group,page,repair_round)`, or how replay recognizes an already-appended batch.

Why this matters: different batching boundaries produce different head hashes from identical candidates. Budget handling can also allow repair after exhaustion in one implementation and deny it in another.

Required contract change:

- Define LLM call-count reservation atomically with the existing token/cost reservation: round 0 consumes one call slot; repair consumes a second only after a committed malformed outcome; opaque/uncertain consumes its reservation and is never resent; budget-denied creates a canonical no-call outcome. Freeze max in-flight extraction calls and deadline parent/child relation.
- Define one deterministic batching rule, e.g. exactly one `EvidenceFactBatchV1` per `admit_evidence` frontier over the sorted canonical bundle set; `ordinal=len(existing_batch_refs)`; replay recomputes the same batch/head and accepts only exact identity. Freeze `policy_refs` as either an exact-key object or a sorted array, and define canonical bundle selection per logical page/work-group.
- Add production wiring tasks for `_deep_v6_context_factory`: inject the profile-bound durable LLM port and nonzero route LLM budget; prove exact fact still consumes zero LLM and fan-out remains off.

## Round-1 exit assessment

The revised plan successfully closes the earlier architectural direction errors: lanes no longer write a chained head, the production graph is explicitly required to leave the Q1 shortcut, and facts/inferences are intended to be durable owners. It cannot pass this round until the seven items above are incorporated into the normative contract and T11 test oracles. The next challenge should specifically verify that one persisted input set reproduces the same fact head, assessment hash, claims, and manifest across restart without consulting `GenericAdmittedFactV1` or route data held only in memory.

VERDICT: FAIL
