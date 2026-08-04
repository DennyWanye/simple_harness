# T11/T12 定向回炉挑战 — Round 3（integration）

> 日期：2026-07-18  
> 角色：plan-test Phase 2 integration challenger  
> 范围：只读审计 `architecture-baseline.md`、`plan.md`、`acceptance.md`、`v6-contracts.md`、`HANDOFF.md`、`execution-results.md`、`AGENTS.md`，并对照当前 v5/v6 graph、effect adapter、workflow schema、research repository、retention 和测试。  
> 判定规则：只要仍存在会让两个合理实现产生不同持久化 schema、identity、恢复或发布行为的 code-level ambiguity，结论必须 FAIL。

## 结论

本轮仍不能进入 T11/T12 实现收敛。新增 §6.2/§6.3 已修正 lane 唯一 head writer、v6 child UUID、fault points、900 秒目标和 retention 路径，但 generic evidence 的最小持久化语义、snapshot transitive closure、continuation start identity、deadline 字段以及 release default migration 仍有互相冲突或未冻结之处。尤其是 collection/policy/open candidate 无法无损落到当前声明的 `EvidenceBindingV1` / `AdmittedResearchFactV1`，会直接让 assessment、claim derivation 和 continuation closure 出现第二种合理实现。

## Findings

### 1. [Blocker] Collection/policy/open candidate 不能无损投影到 frozen binding/fact schema

**证据**

- `v6-contracts.md:254` 的 `EvidenceBindingV1` 只有 `requirement_id,item_id,...`，没有 `field_key`、`item_unique_key`、`subject_key/axis_key`、`facet_key` 或 claim proposition。
- `v6-contracts.md:285` 要求 collection candidate 以 `item_unique_key + field_key` 表达同一 item 的多个独立 required fields。
- `v6-contracts.md:286` 要求 policy/open 使用 `claim_proposal`，但 exact payload 同时列出 `issuer,document_date,commitment,impact`，没有各字段 nullability/互斥规则；同一行又要求事实字段和 impact inference 不得在同一 candidate。
- `v6-contracts.md:294` 的 `AdmittedResearchFactV1` 对 `collection_field` / `claim_fact` 仍只有一个 `normalized_value`，没有 `field_key` / `facet_key` / `item_unique_key`；`candidate_id` 也不是可解析 registered ref。
- `v6-contracts.md:300` 又要求 collection field、issuer/document/date/commitment 从 admitted fact 唯一派生 claim。按当前字段集合，assessment 无法区分同一 item 的 name/vendor/pros/cons 等字段，claim derivation 也无法判断一个 policy fact 对应 issuer、document、date 还是 commitment。

**后果**

实现者只能在至少三种不等价方案中自行选择：把 `field_key/facet_key` 偷进 `definition`，反查 candidate bundle，或扩展 binding/fact schema。前两种都会建立隐藏第二 owner，第三种会改变 frozen contract。Top-N 完整度、policy fact/inference 分离和 open claim-set 因此不可确定实现。

**精确修订建议**

1. 把 `EvidenceBindingV1` 改成 tagged target：exact `target_kind=scalar|matrix_cell|collection_field|claim_fact|claim_inference`，并增加 exact `item_or_cell_id,field_or_facet_key`；scalar 两者均为 null，matrix 固定 cell id + axis key，collection 固定 item id + row-schema field key，policy/open 固定 facet key。
2. `AdmittedResearchFactV1` 同步持久化 `target_kind,item_or_cell_id,field_or_facet_key,item_unique_key`（不适用字段明确为 null），不能要求 assessment 反查 candidate bundle恢复语义。
3. 将 `claim_proposal` 拆为两个 tag：`claim_fact` exact payload=`facet_key,value,time_scope,scope,definition`；`inference_proposal` exact payload=`inference_kind,normalized_proposition,premise_candidate_ids`。若坚持单 tag，必须逐字段冻结 nullability 和互斥 truth table。
4. 增加 round-trip + old-shape reject + collection 两 items 多字段 + policy 四事实/一 impact + open 四 inference 的 production assessment tests。

### 2. [Blocker] 同名 `EvidenceFactBatchV1` 在冻结附录中有两个 `schema_version=1` exact shape，snapshot closure 未冻结新 refs 的归类

**证据**

- `v6-contracts.md:250-258` 宣布所有对象 exact-key/schema version 1，并在 `:257` 定义 Q1 shape：单 `page_result_ref`、binding id arrays。
- `v6-contracts.md:298` 再次定义同名、同 `schema_version=1` 的 final shape：`page_result_refs,candidate_bundle_refs,admitted_fact_refs,registered_inference_refs,...`，仅用 prose 说“替换”前一 shape。
- `v6-contracts.md:267-270` 的 continuation snapshot 声明 `closure_refs` 恰为 spec + batches + assessment + claim + provenance + 六类 policy refs；六类 policy exact keys 只有 `compiler,route,admission,assessment,claim,quality`。
- `v6-contracts.md:279-290` 又要求 prompt、raw provider result、repair input、LLM profile、extraction policy 进入 effect outcome/provenance closure；`:298` 还新增 admitted fact 和 registered inference refs，但没有冻结这些 refs 究竟必须进入 `provenance_refs`、`policy_refs` 还是仅由 batch 内部递归发现。
- T12 的 server closure 验证顺序（`v6-contracts.md:310`）要求 exact closure 和 policy cross-hashes，却没有一条可执行的 transitive expansion 表。

**后果**

旧 Q1 parser、final parser 和 snapshot validator可各自合法地接受不同 shape；continuation 可漏掉 fact/inference/raw/repair/profile refs，或把它们重复归入不同集合并得到不同 snapshot hash。`manifest -> snapshot -> closure` 的唯一事实源因此不成立。

**精确修订建议**

1. 删除/重命名 `:257` provisional 定义；final `EvidenceFactBatchV1` 使用唯一 schema（若要读取 Q1 开发 blob则显式 `schema_version=0` 且 release reader hard reject），不要让两个 shape 都叫 v1。
2. 冻结 `ResearchContinuationSnapshotV1` 的递归 expansion 表：
   - `fact_batch_refs` -> every batch；
   - batch -> page results、candidate bundles、admitted facts、registered inferences、provenance、batch policies；
   - page result -> locator/page/body/span/binding refs；
   - candidate bundle/effect outcome -> prompt/raw/repair/profile/extraction refs；
   - inference -> premise fact refs/model policy；
   - assessment/claim -> referenced fact/inference/binding owners。
3. 扩展 snapshot `policy_refs` exact keys至少包含 `extraction,llm_effect,inference`，或明确规定这些 registered policies 只在 provenance 中并给出唯一分类；同一 ref 不得有两种合法分类。
4. 增加 closure oracle：手工给定全图 ref set，少任一 transitive ref、加入任一 same-run arbitrary ref、把 policy 放错集合都 fail closed；restart/continuation 后 set 与 hash 字节一致。

### 3. [Blocker] 900 秒 contract 使用了不属于 `DurableDeadlineV1` 的字段名

**证据**

- `v6-contracts.md:126` 冻结 `DurableDeadlineV1` exact keys 为 `created_at,...,wall_not_after,...`；`:130-153` 的 schema v4 SQL 也只有这两个 wall anchor。
- `v6-contracts.md:312` 却要求第一次 route checkpoint 持久化 `automatic_started_at` 和 `automatic_wall_not_after`，这两个字段既不在 exact DTO，也不在已安装的 `workflow_research_deadlines` 表。
- 当前 `backend/deskpet/workflows/store/schema.py:497-518` 与附录 SQL一致，说明这不是单纯命名注释；实现必须选择新增 migration/字段或复用现有字段。

**后果**

两种实现都会看似满足 prose：扩展 schema v5，或用 `created_at/wall_not_after` 作为 alias。它们的 DB recovery、hash/DTO round-trip 和 pre-existing v4 dev DB 行为不同，T12 的 restart 断言没有唯一 oracle。

**精确修订建议**

选择并冻结一种方案。建议不再升级表：把 §6.3 改为 root `logical_scope='run:auto'` deadline row 的 `created_at=automatic_started_at`、`budget_ms=remaining_ms=900000`、`wall_not_after=created_at+900`，并明确该 row ID/policy hash 的派生；continuation 为新 root row。测试直接查询这三个既有列，retry/resume/takeover/restart 必须保持原值，30 秒 control settle 只取 `min(command_deadline,wall_not_after)`。

### 4. [High] Continuation 的 start operation、logical slot 和不同 delta 重放语义仍未冻结

**证据**

- `v6-contracts.md:306` 冻结了 child run/lineage operation/request/turn/trace/thread/budget IDs，但没有 `start_operation_id`、`logical_slot`、canonical start payload 或 request hash算法。
- `v6-contracts.md:308` 又把 `after_start_operation_insert` 列为必须 fault injection 的 write point。
- 当前 v5 repository 明确使用 `start_operation_id=f"{child_operation_id}:start"` 和 `request_hash=sha256(canonical(start_payload))`（`backend/deskpet/workflows/store/research_repository.py:880-883`），并把 `logical_slot=child_request_key`（`:985-1008`）；新 v6 contract 没有说明这些规则是否保留。
- `v6-contracts.md:310` 允许客户端提交 continuation natural-language delta，`:306` 又规定所有 child identity仅依赖 parent + policy。`plan.md:546` 规定已有 head 返回 canonical child，但未规定第二个不同 delta 是 first-writer-wins、typed conflict 还是被审计后忽略。

**后果**

并发 different-key + different-delta 时，两个实现可以分别返回既有 child或报冲突；也可能生成不同 request/start hashes，破坏“同一 canonical child/head”的测试 oracle。commit-before-notify recovery scan也不知道用哪一个 durable start operation作为启动输入。

**精确修订建议**

1. 冻结 `child_start_operation_id=child_operation_id+':start'`、`logical_slot=child_request_key`。
2. 定义 normalized delta exact schema与 hash：例如 `start_payload={schema_version,parent_run_id,source_snapshot_hash,normalized_delta}`，`request_hash=sha256(canonical_json(start_payload))`，caller idempotency key只写独立 audit row。
3. 冻结单-head冲突语义。建议 first committed head wins：相同 delta返回 existing/same child；不同 delta也返回 same child但 outcome=`continuation_already_exists_delta_ignored` 并写 caller audit，不修改 start payload/hash。若产品要 typed conflict，也必须仍返回 canonical child id且不得创建 sibling。
4. fault/recovery tests必须从 `workflow_operations` 的 canonical start result启动 created-run，而不是从重试请求参数重建。

### 5. [High] “production graph arbitrary page”测试仍不足以阻止 helper-only/Q1-state 注入假完成

**证据**

- `plan.md:503-511` 要求完整 production graph接线和任意网页输入，但测试项没有冻结启动层级、禁止 state 注入或必须跨 checkpoint/restart。
- 当前 graph 仍只有 `compile_spec -> collect_pages -> extract_facts -> assess_render`（`backend/deskpet/workflows/definitions/v6/deep_research.py:24-53`），manifest 只声明 `official_exact_fact`、无 LLM role（`:56-65`）。
- 当前 `initial_state()` 接受 `fetched_pages` 和 `compiler_candidates`（同文件 `:73-82,:107-123`）；直接塞入预取 pages 再调用 handler，仍可能被描述为“production graph test”，但完全绕过 route decision、durable LLM effect、candidate repair、effect replay和budget owner。
- Q1 已在 `execution-results.md:37-43` 明确有多个“prototype only / offline only”绿项，说明只测 helper 的误判风险是真实存在的。

**后果**

即使 T11 新 helper 全绿，compiled graph 仍可能保持 exact-only，并通过现有风格测试；T11 exit gate没有机器可判定的防旁路 oracle。

**精确修订建议**

在 `plan.md:509` 增加必须测试的具名 integration gate：通过 bootstrap/version registry + real WorkflowRunner 启动 `deep_research/v6`，初始输入只含用户 topic/session（不得传 `fetched_pages/compiler_candidates`），使用 fake upstream但真实 durable fetch/LLM effect journal/blob/deadline/budget ports；分别在 raw LLM committed、candidate bundle committed、fact batch committed 后 crash/restart，断言 provider不重发、head唯一、terminal snapshot closure完整。再加 definition manifest oracle：NODE_IDS包含 §6.2 九节点、五类 intent全部声明、LLM extraction role/ports与 route/budget ports存在。至少 comparison、Top-N、policy、open各一条必须走此入口，不能以 direct helper测试替代。

### 6. [High] T13“默认 v6”没有冻结现有安装的 factory revision migration 与 release identity oracle

**证据**

- `plan.md:568` 只说冻结 hashes、默认切 v6、清除 override、用新隔离 user-data复验；`v6-contracts.md:439` 同样只描述结果。
- 当前默认 owner 不止一处：`backend/config.py:23-60` 的 resolver目前只接受 configured v2..v5，v6只能 dev isolated override；`:596-602` 的 factory pair 是 `v5/revision=5`。
- 现有配置合并逻辑只在用户配置恰为 previous factory pair时逐 revision提升（`backend/config.py:1059-1090`）。只改 dataclass/default resolver会让已有安装继续停在 v5，fresh isolated user-data 的 T13测试却仍可通过。
- 现有 frozen fixture只覆盖 v5 graph manifest（`backend/tests/test_workflow_deep_research_v5_contracts_identity.py:19-24` 和 `backend/tests/fixtures/deep_research_v5_identity.json`）；计划没有指定 v6 release fixture、默认选择和 factory migration 的 exact oracle。

**后果**

“默认 ON”可被实现成仅 fresh install ON、existing install仍 v5；或者删除 dev gate后配置值 v6仍被 resolver降回 v5。两者都可能通过新目录 UI复验。release hash也可能只记录在 results prose，不能阻止默认切换后的 callable drift。

**精确修订建议**

1. T13冻结 release pair：bundle `deep_research_version='v6'`、`deep_research_default_revision=6`；resolver在无 env时接受 configured v6，persisted old run仍按 DB version恢复。
2. factory migrator只把 exact inherited `v5/revision5`提升到 `v6/revision6`；显式 pin（version/revision不等于前一 factory pair）保持不变。增加 fresh、inherited-v5、explicit-pin、invalid-config四类测试。
3. 明确“清除 override”是 T13复验进程不设置 `DESKPET_DEV_DEEPRESEARCH_VERSION`；是否保留 guarded dev override代码须明确，不能由实现者猜。
4. 新增 committed `deep_research_v6_release_identity.json`，固定 manifest/implementation/state/prompt/policy hashes以及 v6 effect profile/prepared-call golden；default switch后再跑同 fixture，任何 hash变化必须升 v7。

## 通过条件

Round 4 前至少完成以下文档修订：

1. binding/fact/candidate target schema可无损覆盖 collection field 与 policy/open facet；
2. 只保留一个 release `EvidenceFactBatchV1` shape，并给出 snapshot transitive closure expansion表；
3. 900 秒统一到现有 deadline exact字段或明确新 migration；
4. continuation start operation/logical slot/delta conflict语义冻结；
5. production runner + restart test gate和 T13 factory migration/release identity oracle写入 plan。

VERDICT: FAIL
