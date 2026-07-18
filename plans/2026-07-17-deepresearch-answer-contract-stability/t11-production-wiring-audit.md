# T11 production wiring audit

> 日期：2026-07-18  
> 范围：只读审计；未修改生产代码，未运行测试。  
> 事实源：已批准的 `plan.md` T11、`v6-contracts.md` §6.2，以及当前 checkout 的 v6 definition、nodes、retrieval runtime、bootstrap/context、integrity、delivery/native terminal projection。

## 结论

**当前 production wiring 不满足 T11 退出门。** 现有 `deep_research/v6` 仍是 Q1 的四节点图：

```text
compile_spec -> collect_pages -> extract_facts -> assess_render -> END
```

它只支持 `official_exact_fact`，并允许通过 `initial_state(fetched_pages=..., compiler_candidates=...)` 注入预取页面和 compiler candidates。`extract_facts` 在一个 node patch 内同时完成 exact extraction、旧式 binding batch 与 assessment；`assess_render` 又在一个 node patch 内完成解码、Q1 renderer、Q1 integrity 和 Q1 terminal persistence。因此当前没有批准方案要求的 candidate producer owner、registered fact owner、fact checkpoint、durable inference effect、registered inference owner、inference checkpoint，以及 generic integrity/persistence。

已经存在且可复用的部分主要是：五类 intent compiler、route/fan-out 纯函数、retrieval 的 query/source/canonical-page 算法、exact scalar extractor、四类 requirement 的纯 assessment 算法、typed report renderer、Claim/Quality/Terminal manifest 的基础 contract，以及 native async terminal projection。它们目前尚未形成一条生产闭环。

## 1. 当前生产路径与精确缺口

| 层 | 当前事实 | 相对 §6.2 的缺口 |
|---|---|---|
| Definition | `definitions/v6/deep_research.py` 的 `NODE_IDS` 只有 `compile_spec/collect_pages/extract_facts/assess_render`；manifest 只声明 `official_exact_fact`、无 LLM role，external ports 只有 `blob/fetch`。 | 必须替换为冻结的 11 节点；manifest 必须声明五类 intent、三个 v6 LLM profile、route/deadline/budget/effect ports。`max_supersteps=8` 也不足以承载 11 个串行 checkpoint，至少应覆盖 11 个 node frontier 加 terminal commit。 |
| Ingress/state | `initial_state()` 接受 `fetched_pages` 与 `compiler_candidates`，并把它们直接写入 `values`/`blob_refs`。main 的真实 state factory 当前没有传这两项，但 helper/直接 graph 调用仍可注入。 | 生产入口只能接收 topic/session/locale/run identity。`stage=pending` 时任何非空 page/candidate/fact/inference 输出都必须 fail closed，不能仅依赖 main 恰好没传。 |
| Compiler | `compile_spec_handler()` 调 `compile_official_exact_fact()`，并消费 `values.compiler_candidates`。 | 改用已有 `compile_research_spec()`；compiler policy/completion policy 应在该 node 注册并由 ref 进入后续 closure。禁止 compiler candidate 注入。 |
| Route | 没有 `plan_route` node。`DeepResearchV6EvidenceRuntime.retrieve()` 在真实 search 前临时构造 `RouteDecisionV1`，其中 `policy_ref` 只是由 `_policy_hash` 拼出的 wire ref；node 没有先注册 route policy/decision。 | route policy bytes 与 frozen decision 必须先注册并 checkpoint。后续 retrieval 只能按 `route_decision_ref` 解码，不能重新计算或根据实时 health 改 decision。route decision/ref 必须进入 batch provenance、snapshot policy/closure。 |
| Retrieval | `load_pages()` 只返回 pages，直接丢弃 `DeepResearchV6RetrievalResult.route_decision`。runtime 直接调用 SearchGateway/fetch、使用进程内 deadline；文件内没有 `EffectJournal/begin_with_budget/commit_or_hold`。 | `load_pages` 必须消费 immutable decision，返回 durable page/search outcome refs 与 ref-only page slots。search/fetch 正负结果、deadline、owner、replay 必须走 canonical effect journal；node patch 前崩溃不得重发。现有 query/fetch 算法只能作为 durable transport 内核复用。 |
| Route budget | runtime 对所有 intent 固定 `budget.llm=0`；main v6 context 只注入 `fetch/blob`。 | exact 保持 LLM hard limit 0 且 port fail-on-call；generic route 需按 §6.2 预留 extraction groups + inference groups + 各组至多一次 repair，并与 token/cost reservation、root/page wall guard同事务收敛。 |
| Candidate production | 不存在 `CandidateProducerOutcomeV1`、`EvidenceCandidateBundleV1`、`EvidenceRepairRequestV1`、`ResearchLLMEffectProfileV1/OutcomeV1` production contract/loader。 | exact 也必须生成 registered deterministic producer outcome + candidate bundle；generic 走 extract profile，malformed 只允许一次 repair。real UTF-8 byte span/hash 必须由 body bytes 重建验证。 |
| Fact admission | `extract_facts_handler()` 直接写 `V6ExtractedEvidenceRefV1`，按页面创建旧 `EvidenceFactBatchV1(page_result_ref, admitted_binding_ids, ...)`；没有 persisted `AdmittedResearchFactV1`。 | admission 必须按冻结排序/首错 reason code执行；每个 admitted/conflicted semantic fact 都先注册。主 graph 独占 facts batch/head writer，batch 记录 page results、candidate slots、fact refs、rejected IDs、policy refs。lane 不能改 head。 |
| Fact checkpoint | extraction、batch 构造和 `assess_q1_evidence()` 都在 `extract_facts_handler()` 的同一个 patch 内。 | `admit_facts` 返回并提交唯一 facts frontier 后，下一 superstep 才允许启动 inference；这次 native `commit_frontier` 是明确的 durable fact checkpoint。 |
| Inference | 没有 inference producer/effect、proposal bundle、registered inference contract或 node。当前 `GenericAdmittedFactV1` 还允许用 `inference_ref` 表示推理，混合了 fact 与 inference DTO。 | 新增 `synthesize_inferences` 和 `register_inferences`；proposal 只能引用 persisted fact refs。`RegisteredInferenceV1` 单独持久化；assessment 使用独立 non-persistent `GenericAdmittedInferenceV1`。fact DTO 不再充当 inference persistence owner。 |
| Inference checkpoint | 不存在 inference batch/head。 | `register_inferences` 写 `batch_kind=inferences`，把 registered/rejected records 全部纳入 batch，更新同一 evidence head；assessment 只能在该 checkpoint 后解码 ordered refs。 |
| Assessment | 已有四类 pure assessor，但入口只接 `facts`，没有 §6.2 `decode_assessment_inputs()`，也没有 `assessment_input_hash`。现有 `AnswerAssessmentV1` 仍是 Q1 shape。 | 从 ordered batch chain 解码 registered facts/inferences；计算并持久化 `assessment_input_hash`。交换、遗漏或 policy 变化都必须重新计算/失败，不能用 node 内 DTO。 |
| Renderer/claims | typed renderer 已存在，但 production `assess_render_handler` 仍使用 exact renderer，并从进程内 evidence DTO构造 claim。 | `render_claims` 必须按唯一 derivation table从 registered fact/inference 生成 ClaimRecord；comparison/preference、policy impact、open inference 都必须可反向解析。typed renderer只消费 persisted assessment/claims/facts。 |
| Integrity | `deep_research_v6_integrity.py` 的 batch/assessment keys 和唯一 builder仍是 Q1 exact-scalar；只验证 admitted binding IDs。 | 新增 generic integrity core：验证两类 batch、registered fact/inference反向边、assessment input hash、claim derivation、route/profile/policy refs和完整 closure。Q1 只成为 exact intent 的输入分支，不能保留独立 persistence gate。 |
| Persistence | `persist_q1_terminal_bundle()` 接收 inline `research_spec/answer_result/integrity_fact_batches/integrity_assessment`，重新构造 Q1 claims/integrity；snapshot 缺 `assessment_input_hash`，`policy_refs` 只有 6 项且没有 route decision、LLM profiles、inference policy。 | 替换为 generic manifest builder，只读取 registered refs。snapshot 必须满足 §6.1 exact keys/11 policy refs及 transitive closure；不能重新执行 Q1 业务逻辑或读取 inline DTO。 |
| Bootstrap/context | `_deep_v6_context_factory()` 只构造 `DeepResearchV6EvidenceRuntime`，注入 `fetch/blob`；没有 EffectJournal、run fence resolver、三 profile LLM ports、LLM budget/root deadline port。adapter 仍 `new_runs_enabled=False`（T13 默认切换前可以保持）。 | T11 context 必须构造真实 durable retrieval/effect dependencies与 profile-bound `llm_extract/llm_inference/llm_repair`。exact route虽可见这些 ports，但必须 reservation=0、producer不得调用。 |
| Native terminal | `NativeWorkflowExecutable` 已在 terminal frontier 构造 async request、await v6 projector、校验 manifest/ref、合并 projection refs/intents，并把 canonical projection纳入 frontier operation hash；default async registry已注册 v6。 | 这段可以复用。仍需保证新 `persist_manifest` 只把 `terminal_manifest_ref/hash` 写入 values并把 manifest digest加入 state blob refs。注意当前 registry 是 `CompiledWorkflow.bind()` 内部 default，不是 plan T1 描述的显式 `NativeRuntimeDependencies` 注入；若 T1 的可注入 registry仍是最终 DoD，此缺口不能被 T11 测试掩盖。 |

## 2. 批准后的 11 节点生产接线图

下表中的“输出”都指 registered wire refs 或稳定 hash；不得把 candidate/fact/inference/report正文作为跨 node owner。

| # | Node | 唯一允许输入 | 端口/可复用 helper | 必须注册并写入 state 的输出 | checkpoint 语义 |
|---:|---|---|---|---|---|
| 1 | `compile_spec` | `topic,answer_locale,run identity` | `compile_research_spec()` | `spec_ref/spec_hash`，`policy_refs.compiler`；completion policy随 spec ref验证 | compiler 输出冻结；pending ingress 中预置 page/candidate/fact/inference 一律拒绝 |
| 2 | `plan_route` | `spec_ref/spec_hash` | route planner；可复用 `build_route_decision_from_spec()` 和 conditional fan-out helper | `route_policy_ref/hash`、`route_decision_ref/route_id`、frozen budget/work-group order；补齐 `policy_refs.route` | retrieval 前唯一 route checkpoint；replay只能读取 ref |
| 3 | `load_pages` | `spec_ref,route_decision_ref` | durable v6 search/page-read port；复用现有 query planning、authority verification、canonical page merge | ordered `page_result_refs`/page slots，locator/body/effect outcome closure refs；不得返回 route 的第二份解释 | 每个 read outcome先 journal/owner收敛；node patch只引用 canonical outcomes |
| 4 | `extract_candidate_bundles` | registered page slots + route/work-group refs | exact：`extract_scalar_evidence()`的 span/value算法包装成 deterministic producer；generic：profile-bound extract port；malformed转 repair port一次 | ordered `candidate_slot_results[{logical_page_id,work_group_id,producer_outcome_ref,bundle_ref}]` 以及 producer/effect/profile dependency refs | 只持久化 producer结果；不更新 evidence head |
| 5 | `admit_facts` | spec、route、page results、candidate slots | 新 generic admission validator；可复用现有 ID/hash/byte-span primitives | `AdmittedResearchFactV1` refs；一个 canonical `batch_kind=facts`；append `fact_batch_refs`，更新 `evidence_head_hash` | **事实 checkpoint**。只有主 graph按 route slot顺序写 head；lane只提供 blobs |
| 6 | `synthesize_inferences` | 已提交的 fact batch chain/head | exact：空 inference plan且 LLM fail-on-call；generic：profile-bound inference port，必要时 repair一次 | ordered `inference_slot_results[{work_group_id,effect_outcome_ref,proposal_bundle_ref}]` | 只写 durable effect/proposal结果；必须以 persisted fact head作为 input identity |
| 7 | `register_inferences` | persisted fact refs/head + inference proposals | 新 inference validator/decoder | 所有 `RegisteredInferenceV1`（registered/rejected）refs；一个 `batch_kind=inferences`；append batch并更新 head | **推理 checkpoint**。registered/rejected均可审计，只有 registered进入答案输入 |
| 8 | `assess_answer` | ordered fact/inference batches、spec、assessment policy | 复用 matrix/collection/claim-set assessor；新增 strict fact/inference decoder | `assessment_ref/hash/assessment_input_hash`，`policy_refs.assessment` | assessment只能由 registered refs重建；禁止读取 node 6/7 的进程内对象 |
| 9 | `render_claims` | spec、assessment、registered facts/inferences、locators | 复用 typed report renderer；新唯一 claim derivation函数 | `claim_batch_ref`、canonical final/report/safe-summary refs，`policy_refs.claim`；必要的 citation/provenance refs | 所有 visible claim必须可反向解析到 fact或inference owner |
| 10 | `integrity` | batch chain、assessment、claim batch、content refs、全部 policy refs | 泛化 Claim/Quality contract和 closure validator | `quality_audit_ref`、honest `answer_status`、`policy_refs.quality`；hard fail时仅保留安全摘要 | completed/partial/insufficient唯一 gate；不能调用 Q1 builder兜底 |
| 11 | `persist_manifest` | 上述 registered refs与 answer status | 泛化现有 manifest/snapshot builder；复用 `TerminalDeliveryManifestV1` identity和 native projector | §6.1 exact snapshot、§7 terminal manifest；state只写 `terminal_manifest_ref/hash`并合并 manifest digest | terminal frontier随后由现有 native async projector原子提交 intents/blob closure/run terminal |

关键时序必须是：

```text
candidate producer blobs
  -> admit_facts patch
  -> native commit_frontier (fact head durable)
  -> inference effect begin/reuse
  -> register_inferences patch
  -> native commit_frontier (inference head durable)
  -> assessment/render/integrity/manifest
```

任何把 node 5 与 node 6 合并、或把 node 7 与 node 8 合并的实现都会重新引入“崩溃后重发/assessment读取未提交 DTO”的问题。

## 3. 推荐 state surface 与 registered identity

### 3.1 `values` 的最小生产字段

建议保持一个 owner清晰、只含 pointers/hashes/小型状态的 schema：

```text
topic, answer_locale, contract_schema_version, stage
spec_ref, spec_hash
route_policy_ref, route_policy_hash, route_decision_ref, route_id
page_result_refs
candidate_slot_results
fact_batch_refs, evidence_head_hash
inference_slot_results
assessment_ref, assessment_hash, assessment_input_hash
claim_batch_ref, quality_audit_ref
answer_status
final_assistant_ref, report_ref, safe_summary_ref
policy_refs
terminal_manifest_ref, terminal_manifest_hash
```

约束：

- `page_result_refs` 只能来自 durable retrieval outcome；不保留 `fetched_page_refs` 作为 ingress。
- slot arrays只保存 §6.2 exact ref records，不内联 bundle/candidate/proposal。
- `fact_batch_refs` 是 facts batch + inference batch 的 append order；`evidence_head_hash` 是两阶段共用 head。
- `policy_refs` 最终必须恰为 `compiler,route,extraction,llm_extract,llm_repair,llm_inference,admission,inference,assessment,claim,quality`。route decision不是 policy，应作为 provenance中的独立 registered ref。
- terminal state不保存 `research_spec/answer_result/integrity_fact_batches/integrity_assessment/final_assistant/report_markdown` 等 inline业务副本。
- `blob_refs` 始终使用 bare digest items供 native owner promotion；domain values使用 `sha256:<digest>` wire ref，边界处 strict parse/format。

### 3.2 必须可追溯的 registered IDs/refs

生产闭包至少包含以下身份链：

```text
spec_ref/spec_hash
route_policy_ref -> route_decision_ref/route_id
search/page effect outcome refs -> locator/body/page_result refs
candidate producer outcome refs -> candidate bundle refs
admitted fact refs -> facts batch ref/head
LLM profile/prompt/raw/outcome refs -> inference proposal bundle refs
registered inference refs -> inference batch ref/head
assessment_ref/hash/input_hash
claim_batch_ref -> quality_audit_ref -> final/report/safe-summary refs
continuation_snapshot_ref/hash -> terminal_manifest_ref/hash
```

snapshot 的 `provenance_refs` 必须通过这些前向边展开；禁止依赖全库 reverse lookup。route decision、producer outcome、effect outcome、admitted fact、registered inference都必须有自己的 registered blob identity，不能只存在于 state JSON或 Python DTO。

## 4. Context ports 与预算接线

推荐 main v6 context显式注入：

| Port key | 责任 |
|---|---|
| `blob` | 当前 `RegisteredBlobStore`；所有 node只通过 wire ref读写并验证 same-run owner。 |
| `retrieval` | profile/route-aware durable v6 retrieval port；`plan_route`获得 frozen policy inputs，`load_pages`只消费 decision；内部用 EffectJournal收敛 search/fetch。不要继续让 `fetch.load_pages(spec)`自行决定 route。 |
| `llm_extract` | 绑定 `ResearchLLMEffectProfileV1(role=evidence_candidate_extract)` 的 durable port。 |
| `llm_inference` | 绑定 `role=evidence_inference_synthesize` 的 durable port。 |
| `llm_repair` | 绑定 `role=evidence_structured_repair` 的 durable port；只有 committed malformed round0可以调用。 |
| `deadline`/effect context | 同一 run fence、root automatic deadline与 child page deadline；restart/load不得重建 wall guard。若 deadline由各 durable port内部持有，仍需共享同一个 persisted root owner。 |
| `native_execution_policy` | conditional fan-out的并发上限；最大并发必须再取 `min(route lane budget, remaining LLM reservations, 3)`。 |

`DurableResearchCallEffectAdapter` 可复用其 run fence、`begin_with_budget()`、opaque reconciliation、cost/token actual settlement和 result blob owner流程，但必须先参数化 identity：

- 构造参数携带 `EffectPolicy/tool_name/tool_spec_version/schema_hash/permission_policy_version/effect_type/response-format resolver`；
- 缺省参数必须逐字保持现有 v5 `_POLICY`、prepared-call bytes和 effect ID；
- v6 三个 profile分别生成独立 prepared identity，不能靠 role值复用 v5 tool spec；
- stable call ID使用 §6.2 `v6-{extract|infer|repair}:...`，round0/repair各自有 canonical logical effect；
- adapter的成功返回不能直接成为 node DTO，必须封装/注册 `ResearchLLMEffectOutcomeV1`并由 replay reader解码 selected bundle。

route `llm` hard limit：exact固定0；generic在 route policy中冻结 extraction slots + inference groups + 每个 slot/group至多一个 repair的上限。每个 round0在 effect begin与 token/cost reservation同事务占用一次；只有 committed malformed才可占用 repair reservation。unused reservation不允许被 node临时挪作额外重发。

## 5. 可直接复用与必须替换的 helper

### 可复用（接上 registered边界后）

- `compile_research_spec()` 及 comparison/Top-N/policy/open compiler。
- `RouteDecisionV1`、`build_route_decision_from_spec()`、`conditional_fanout_*()`；前提是 route policy先真实注册，decision也注册并被后续按 ref读取。
- `DeepResearchV6EvidenceRuntime` 的 bounded query、source target、authority/redirect verification、candidate selection、canonical page ordering和 child span算法；仅作为 durable effect transport内核。
- `extract_scalar_evidence()` 的 exact parsing与 byte-span primitives；外层必须生成 generic candidate union/outcome。
- `derive_matrix_cell_id()`、`derive_collection_item_id()`、matrix/collection/claim-set assessment与 Top-N ranking helper。
- `render_typed_report()` 的四类 renderer和 citation output；输入改为 persisted decode结果。
- `ClaimRecordV1/ClaimBatchV1/QualityAuditV1/TerminalDeliveryManifestV1` 的 strict identity基础。
- `project_v6_terminal_commit()` 与 native terminal projection/operation hash/atomic `commit_frontier` 路径。

### 必须替换或泛化

- `compile_official_exact_fact()` 的 production-only调用、`compiler_candidates`输入。
- `collect_pages_handler()` 的 injected pages分支和 `fetch.load_pages(spec)`接口。
- `V6ExtractedEvidenceRefV1` + 旧 `EvidenceFactBatchV1(admitted_binding_ids)` 作为生产 ledger owner。
- `GenericAdmittedFactV1.inference_ref` 的 fact/inference混合语义。
- `assess_q1_evidence()`、`assess_and_render_exact_facts()`、`build_q1_exact_scalar_integrity()` 在 production graph中的旁路地位。
- `persist_q1_terminal_bundle()` 的 inline DTO/Q1重算路径。

## 6. 最小有序编辑序列

1. **先冻结 persistence contracts**：在 v6 contract/evidence模块实现 candidate producer/bundle、repair/effect profile+outcome、admitted fact、inference proposal/registered inference、新两类 `EvidenceFactBatchV1`、assessment input hash及 strict loaders/media types。先做 pure golden，避免 node接线时继续漂移。
2. **参数化 durable LLM effect但锁住 v5**：重构 `research_runtime.py` 的 effect identity；先跑现有 v5 prepared/effect golden，再新增三个 v6 profile golden。任何 v5 bytes/hash变化立即停止。
3. **建立 v6 durable LLM wrapper**：实现 extract/inference/repair prompt→raw→outcome→selected bundle journal、一次 repair、budget denied/no-call outcome与五个 fault boundary。
4. **拆 route 与 retrieval**：runtime暴露可注册的 route policy inputs；`plan_route`持久化 policy+decision。`load_pages`改为强制接收 frozen decision/ref，search/fetch包入 durable effect；删除自行重算 route与 `llm=0` generic默认。
5. **替换 definition/initial state**：改为 11 nodes/10 edges，扩大 superstep上限；manifest声明五 intent、三 LLM roles和完整 ports；删除 `fetched_pages/compiler_candidates`参数与 values。
6. **实现 nodes 1–5**：generic compiler、route、durable pages、deterministic/LLM candidate producer、single-writer admission。完成后先做 exact + generic fact-head crash/restart gate。
7. **实现 nodes 6–8**：fact-head keyed inference effect、registered inference batch、strict assessment decoder/input hash。完成后做 inference bundle/head crash/restart gate。
8. **实现 nodes 9–11**：唯一 claim derivation、generic integrity、generic snapshot/manifest persistence；移除 production对 Q1 renderer/integrity/persist helper调用。
9. **接 main context**：创建同 DB的 RegisteredBlobStore/EffectJournal/run-fence resolver/root deadline，注入 retrieval与三个 profile ports以及 native并发 policy；exact LLM使用计数 fail-on-call断言。
10. **最后跑 production gates**：bootstrap/version registry + `WorkflowLauncher/WorkflowRunner`，只给 topic/session。通过后再进入 T12；不要先做默认 v6切换。

该顺序的关键是：contract与 effect identity先稳定，definition/node再接；否则 crash/replay测试会同时暴露 schema漂移和接线错误，无法定位。

## 7. 阻止 `fetched_pages/compiler_candidates` 注入的 focused integration tests

以下测试必须使用真实 `WorkflowRunStore + NativeCheckpointStore + WorkflowRegistry + WorkflowRunner/WorkflowLauncher`；可以 fake upstream transport，但不能直接调用 handler或构造已抓取 page/candidate作为成功输入。

### 7.1 Ingress surface hard fail

1. `test_v6_initial_state_has_no_prefetched_or_compiler_candidate_parameters`
   - `inspect.signature(initial_state)` 不含 `fetched_pages/compiler_candidates`。
   - 显式传任一参数得到 `TypeError`。
   - 生成 state 的 pending values不含这两个 key。
2. `test_v6_compile_spec_rejects_nonempty_upstream_outputs_in_pending_state`
   - 篡改 pending state，分别预置 `fetched_page_refs/page_result_refs/compiler_candidates/candidate_slot_results/fact_batch_refs/inference_slot_results`。
   - 通过 Native runner启动，每项都在 upstream call count=0 时 fail closed。
3. `test_v6_launcher_start_payload_allowlist`
   - 从 service/launcher start，只提交 topic/session/locale。
   - start payload附加 page/candidate/route/blob ref字段必须被 adapter/state factory拒绝，而不是忽略后继续。

### 7.2 Definition/port manifest gate

4. `test_v6_definition_is_exact_11_node_graph`
   - node/edge顺序逐项等于 §6.2；无旧 `collect_pages/extract_facts/assess_render`。
   - manifest intent_types含 `official_exact_fact/comparison/top_n/policy/open_research`，LLM roles恰为三项，ports含 blob/retrieval/三LLM profile/deadline-budget。
5. `test_v6_load_pages_requires_registered_route_decision`
   - 缺 route decision、wrong-owner ref、decision/spec mismatch、decision policy ref unreadable均在 gateway call count=0 时失败。
   - runtime不能接受只有 spec的旧接口。

### 7.3 五类真实 Runner gate

6. 参数化 `official_exact_fact/comparison/top_n/policy/open_research`：initial input仅 topic/session；fake gateway提供原始 search/fetch response，fake LLM只提供 raw JSON bytes。断言最终 snapshot从真实 effect/page/candidate/fact/inference链得到，不能由 fixture注入 bundle。
7. exact case使用 `llm_extract/llm_inference/llm_repair` fail-on-call对象且 route LLM budget=0；仍应产生 deterministic producer outcome/bundle、`AdmittedResearchFactV1`、facts batch、空 inference batch或 contract规定的完整 inference frontier、generic assessment/integrity/manifest。断言旧 `persist_q1_terminal_bundle/build_q1_exact_scalar_integrity`若被 monkeypatch为抛错，production run仍成功。
8. generic cases断言 route decision/policy/profile refs均可从 snapshot closure解引用；comparison/policy/open的 visible inference claim必须反向引用 registered inference，Top-N每个 required field反向引用 admitted fact。

### 7.4 两 checkpoint 与不重发 gate

9. 使用同一 DB/blob root，分别在以下 durable边界后模拟进程退出并重建 service/runner/context：
   - provider return后；
   - raw blob put后；
   - candidate bundle/outcome commit后；
   - facts frontier commit后；
   - inference proposal/outcome commit后；
   - inference frontier commit后。
10. 每次恢复断言：provider call count不增加；facts/inference head各唯一；batch ordinal连续；effect outcome复用；assessment bytes/hash一致；snapshot closure恰好完整。
11. 特别断言 inference effect的 prompt/input head等于重启前已提交 fact head；如果 facts patch尚未 commit，inference upstream call count必须仍为0。

### 7.5 Fan-out single-writer gate

12. ≥3 work groups且lane budget足够时并发上游返回采用不同完成顺序；两次运行最终 candidate slot顺序、fact IDs、facts batch bytes/head、inference batch/head、assessment hash必须逐字相同。
13. lane尝试写 `fact_batch_refs/evidence_head_hash` 或返回无 producer outcome的裸 candidate bundle必须 fail closed；只有 `admit_facts/register_inferences` node是 head writer。

### 7.6 Native terminal projection gate

14. 从真实 Runner走到 `persist_manifest -> END`，只允许 state pointers触发 async projector；删 manifest blob、wrong owner、projection closure多/少 ref均使 terminal `commit_frontier`整体失败且不物化 outbox/run terminal。
15. crash after terminal commit/before push后恢复：projection hash/frontier operation、manifest/intents均不变，原 session只有一个 final assistant。该测试复用当前 native路径，但输入必须来自新的 generic 11-node graph。

## 8. T11 implementation completion checklist

- [ ] production definition严格11节点，五 intent manifest与三 LLM profiles完整。
- [ ] production ingress无法注入 fetched pages/compiler candidates或后续 registered refs。
- [ ] route policy与decision在 retrieval前注册、checkpoint；load只消费 immutable decision。
- [ ] exact与generic都产出 candidate producer/bundle；exact LLM reservation=0且fail-on-call。
- [ ] facts由主 graph唯一 admission并 durable checkpoint。
- [ ] inference只在 fact checkpoint后执行，注册后第二次 durable checkpoint。
- [ ] assessment/renderer/integrity/persistence只从 registered refs解码。
- [ ] snapshot包含 exact policy/provenance/closure，terminal state只保留 manifest pointers。
- [ ] v5 prepared/effect identity golden不变；v6三 profiles golden固定。
- [ ] 五 intent真实 Runner、六 fault/restart边界、fan-out nondeterministic completion、native terminal replay全部通过。
- [ ] 完成时按 `AGENTS.md` 同次更新 `ARCHITECTURE/DeepResearch.md` 与 `ARCHITECTURE/PROJECT_STATUS.md`；T13门禁前不把默认版本宣称为 v6。

