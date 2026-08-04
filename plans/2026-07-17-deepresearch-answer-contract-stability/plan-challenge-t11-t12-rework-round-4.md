# T11/T12 定向回炉挑战 — Round 4（T11 增量复核）

> 范围：只复核 Round 1 的七项 T11 finding 在最新 `plan.md` / `v6-contracts.md` 中是否闭环，并检查这些修订引入的新矛盾。已闭环项不重复作为 finding。  
> 方法：对照 Round 1/2/3 报告、最新 §6.1/§6.2、T11 production integration gate，以及当前 v6 graph/effect/assessment 生产边界。

## 增量结论

以下四项已闭环：

- matrix/collection 只有一个明确的 canonical ID 算法（`v6-contracts.md:64,74,285-286`）；
- fact-batch 的 per-ref owner 分类与 frontier checkpoint promotion 已明确（`v6-contracts.md:302`）；
- route policy/decision 在 `plan_route` 先注册、`load_pages` 只消费 frozen ref（`v6-contracts.md:306`）；
- 每个 admission frontier 恰好一个 batch，ordinal/head/replay identity 已冻结（`v6-contracts.md:302`）。

tagged binding/fact payload也已经覆盖 matrix/collection/policy fact，并明确 fact→Generic DTO decoder（`v6-contracts.md:254,298`）。但 durable candidate producer、跨页 inference 和 LLM profile 三处修订互相形成了新的不可执行矛盾；此外 registered inference 仍没有 assessment 投影契约。因此本轮仍不能 PASS。

## Findings

### 1. [BLOCKER] exact fact 的 `llm=0` 与“batch 只能从 canonical LLM effect outcome 选 bundle”互斥

**证据**

- `v6-contracts.md:278` 把最终生产链冻结为 `durable LLM effect -> EvidenceCandidateBundleV1 -> ...`，没有非 LLM candidate producer 分支。
- `v6-contracts.md:302` 又要求每个 `(logical_page_id,work_group_id)` 的 bundle 必须由“唯一 canonical effect outcome”选出，作为唯一 batch 输入。
- `v6-contracts.md:306` 要求所有 intent 走同一九节点 graph，`extract_candidate_bundles` 使用 v6 durable profile；`plan.md:507,509` 同时要求 Q1 exact path 迁入 generic core，且 production integration gate覆盖五类 intent。
- `v6-contracts.md:308` 明确规定 exact fact 的 LLM hard limit固定为 0。
- 当前 Q1 生产原型确实使用确定性 scalar extractor而非 LLM（`backend/deskpet/workflows/definitions/deep_research_v6_nodes.py:403-495`），所以“exact 不调用 LLM”是既有架构意图，不是测试特例。

**为什么是新矛盾**

按最新 exact contract，exact fact 既不能开始任何 LLM effect，又不能在没有 canonical LLM effect outcome 的情况下把 candidate bundle送入唯一 fact batch。实现者只能选择：继续 exact-only binding旁路、偷偷给 exact 分配 LLM budget，或发明一个未冻结的 deterministic outcome；三者产生不同 identity/provenance。

**精确改法**

冻结一个统一的 typed candidate-producer 边界：

1. 把总链改为 `CandidateProducerOutcomeV1 -> EvidenceCandidateBundleV1 -> admission`；outcome tagged union=`deterministic|llm`，共有 exact `outcome_id,origin,logical_page_id,work_group_id,bundle_ref,dependency_refs,policy_ref,status`。
2. exact fact 使用 registered deterministic extraction policy，在 `extract_candidate_bundles` 中产生 content-addressed bundle/outcome，不创建 LLM call reservation；generic intents使用 `ResearchLLMEffectOutcomeV1`。两种 origin都进入 batch provenance，但只能各自通过对应 owner/policy验证。
3. §302 的 canonical selection改为“每个 slot唯一 canonical candidate-producer outcome”，并冻结 deterministic outcome 的 owner promotion/restart规则。
4. 增加 exact production runner oracle：LLM port 为 fail-on-call、`llm=0`，仍产生 generic `EvidenceCandidateBundleV1/AdmittedResearchFactV1/EvidenceFactBatchV1` 并走同一 assessment/integrity/manifest；不得回到 Q1 provisional batch或直接注入 binding。

### 2. [BLOCKER] page-local extraction bundle 无法生成依赖跨页 admitted facts 的 comparison/open inference

**证据**

- `EvidenceCandidateBundleV1` 固定绑定一个 `logical_page_id/page_result_ref`（`v6-contracts.md:280`），所有 candidate 还共用该页面的 byte span/excerpt contract（`v6-contracts.md:282`）。
- `inference_proposal` 却要求 `premise_candidate_ids`，并用于 `comparison|preference|conclusion|counterevidence` 等跨事实推断（`v6-contracts.md:288`）。
- `RegisteredInferenceV1` 要求所有 premise candidate先映射成“本次/历史 admitted fact refs”，否则 rejected（`v6-contracts.md:300`）。然而 factual candidate 和 inference proposal目前在同一 pre-admission bundle中，admission仍按一条 candidate顺序逐项处理（`v6-contracts.md:296`），没有冻结 fact-first 两阶段。
- claim derivation要求跨 cell comparison/preference、policy impact、open conclusion/limitation/counterevidence/uncertainty都来自这些 registered inference（`v6-contracts.md:304`）。冻结 graph却只有 `extract_candidate_bundles -> admit_evidence -> assess_answer`，没有在 facts durable 后执行 global inference synthesis的阶段（`v6-contracts.md:306`）。

**为什么是新矛盾**

comparison 的两个 subject、open research 的结论与反证常来自不同 page/bundle。一个 page-local LLM call既看不到其他并行 bundle最终 candidate IDs，也不能合法引用尚未 admitted 的 premise fact refs。即使同一 frontier最后汇总全部 bundle，inference candidate ordinal早于其 premise时会被 `inference_premise_missing` 拒绝，语义取决于 LLM 输出顺序。common page span也迫使真正的跨页推断伪装成某一页的 extractive事实。

**精确改法**

必须冻结一个 durable fact-first / inference-second pipeline，不能只靠实现约定：

1. page-local `EvidenceCandidateBundleV1` 只允许 evidence-bearing fact tags（scalar/matrix/collection/claim_fact）；这些 candidate必须有真实 body span。
2. factual admission先产生并 checkpoint `AdmittedResearchFactV1` batch/head。
3. 新增 `InferenceProposalBundleV1`：exact keys至少包含 `run_id,spec_hash,work_group_id,input_evidence_head_hash,premise_fact_refs,inference_proposals,profile_ref`；proposal直接引用 ordered registered premise fact refs，不携带单页 byte span。
4. 在 final assessment前增加可恢复的 `synthesize_inferences -> register_inferences` node，或在 `admit_evidence` 内冻结同等的两个 durable sub-frontier与 fault points。只有 factual head commit后才能发 inference LLM effect；restart只复用 outcome，不重发。
5. inference registration按 `(work_group_id,inference_kind,proposal_id)`稳定排序，premise lookup基于已 checkpoint facts；然后追加一个唯一 inference frontier batch/head，再运行 final assessment。
6. 更新 definition manifest/node-count oracle及 comparison/open crash-replay tests，证明跨两个 page results的 premise refs在重启后生成同一 inference ID/head。

### 3. [BLOCKER] `ResearchLLMEffectProfileV1.role` 是单值，但 extraction/repair 被要求共享一个未定义的“独立 profile”

**证据**

- `ResearchLLMEffectProfileV1` exact keys含单值 `role,response_format_hash`（`v6-contracts.md:290`）。同一行新增两个不同 role：`evidence_candidate_extract|evidence_candidate_repair`，且两者显然使用不同 candidate/repair JSON schema。
- 该行随后只定义“v6 使用独立 registered profile”一组 policy/tool字段，没有分别冻结 extract profile与repair profile的 role、response-format hash、profile ID/ref。
- `EvidenceRepairRequestV1` 只有一个 `profile_ref`，`ResearchLLMEffectOutcomeV1` 也只有一个 `profile_ref`（`v6-contracts.md:292`）；无法判断 repair request应引用原 extraction profile、repair execution profile，还是两者。
- snapshot `policy_refs` 只有一个 `llm_effect` slot（`v6-contracts.md:269`），也无法无歧义保存两个 profile identity。

**后果**

一个实现可能用一个 extract profile却临场改变 repair role/response format；另一个实现会注册两个 profile。两者 PreparedToolCall bytes、effect ID、snapshot policy closure与v5 golden保护均不同。

**精确改法**

- 冻结两个完整 profile常量：`V6_EVIDENCE_EXTRACT_PROFILE` 与 `V6_EVIDENCE_REPAIR_PROFILE`，分别给出 `workflow_version,role,response_format_hash,policy/tool/schema` 全字段；profile ID/hash按现有公式独立派生。
- `EvidenceRepairRequestV1` exact keys同时包含 `original_profile_ref,repair_profile_ref`；round-0 outcome只引用 extract profile，round-1 outcome只引用 repair profile并在 dependencies保留 round-0 outcome/request。
- snapshot policy refs改为明确的 `llm_extract,llm_repair`（若新增 Finding 2 的 inference LLM，再增加 `llm_inference`），不能用一个 ref代表多个 registered blobs。
- v5 default adapter profile保持 byte-identical；新增 prepared-call golden分别固定 extract、repair（及 inference）三种 v6 call bytes/IDs。

### 4. [BLOCKER] registered inference 仍没有唯一的 assessment DTO 映射

**证据**

- `v6-contracts.md:298` 只冻结 `AdmittedResearchFactV1 -> GenericAdmittedFactV1` decoder；`RegisteredInferenceV1` 在 `v6-contracts.md:300` 是独立 persisted owner，但没有对应 assessment projection算法。
- 当前 generic assessor只接收 `GenericAdmittedFactV1`（`backend/deskpet/workflows/definitions/deep_research_v6_assessment.py:403-420`）；claim-set completion按 `fact.claim_kind`匹配 requirement rule，并从 `inference_ref`判断 inference support（同文件 `:358-390`）。
- policy requirement的 rule名称是 `policy_fact` 与 `impact_inference`（`backend/deskpet/workflows/definitions/deep_research_v6_compiler.py:632-645`），而 registered inference kind是 `impact`；open kinds则是 `conclusion|limitation|counterevidence|uncertainty`（compiler `:693-704`）。最新契约没有冻结 `impact -> impact_inference`、premise bindings、facet与item identity如何投影。

**后果**

facts现在可以无损重建 Generic DTO，但 policy/open inference仍可被实现为：直接传 RegisteredInference、转换成 Generic DTO、或只在 ClaimRecord阶段使用。前两种需要不同 mapping，第三种会让 assessment永远缺 impact/open minima。

**精确改法**

- 冻结 `decode_assessment_inputs(ordered_fact_refs,ordered_inference_refs,spec)`：fact使用已定义 decoder；inference一对一投影 `item_or_cell_id,facet_ids,premise_binding_ids,inference_ref,status`。
- 明确 claim-set kind mapping：policy `impact -> impact_inference`，policy claim facts恒为 `policy_fact`；open inference kind原样；rejected inference不得进入 supported input。comparison/preference inference只用于 claim/integrity，不得冒充 matrix factual cell coverage。
- assessment input hash必须覆盖 ordered fact refs + inference refs + policy hash；增加 registered inference round-trip/restart golden，断言 policy/open assessment hash与 requirement results字节一致。

## Round 4 收敛判定

Round 1 的 ID、owner、route和batch-boundary问题已经实质闭环，不需要再回炉。下一轮只需增量复核以上四项：typed deterministic/LLM candidate producer、facts durable 后的 global inference阶段、分离的 extract/repair/inference profiles，以及 registered inference 的唯一 assessment projection。四项未冻结前，production integration gate仍可能通过 exact旁路或 page-local伪 inference，不能视为100% code-executable。

VERDICT: FAIL
