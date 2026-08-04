# DeepResearch v6 冻结契约附录

> 本文件是 `plan.md` T2/T5/T12 的规范性组成部分。字段、枚举、ID/hash、merge 与错误规则在 Phase 2 定稿后冻结；实现不得自行发明兼容字段或静默降级。

## 1. 通用序列化与 ID

- 所有对象 exact-key、`schema_version=1`、unknown key fail closed；JSON 只允许 UTF-8、有限数值、无 NaN/Infinity。
- set-like 数组在构造时按 canonical key 去重排序；语义有序数组（requirements、work dimensions、axes、fields、claim kinds）保持 ordinal/order 并进入 hash。
- `canonical_json` 使用仓库现有严格 JSON + sorted keys；persisted load 必须重算所有派生 ID/hash。
- `requirement_id = "req_" + sha256(canonical_json(requirement_without_id_and_ordinal))[:24]`。
- `spec_hash = sha256(canonical_json(spec_without_spec_id_and_spec_hash))`；`spec_id="rs_"+spec_hash[:24]`。`spec_ref` 是完整 serialized spec（包含 spec_id/spec_hash）的 registered blob wire ref，因此不要求 digest 等于 semantic `spec_hash`；两者分别校验 bytes owner 与业务语义 identity。
- domain JSON/wire 中所有 content-addressed ref 固定为 `sha256:<lowercase-64hex>`；workflow DB 的 `workflow_blobs.sha256`/FK/owner 与 native `state.blob_refs` 固定为 bare lowercase 64hex。唯一边界函数 `parse_blob_ref(wire)->digest` 与 `format_blob_ref(digest)->wire` strict roundtrip；`RegisteredBlobStore.put()` 返回的 `BlobRef` 只能由 formatter 进入 domain JSON。resolver 先 parse 再查 owner；native 合并 async projection refs 时先 parse 为 bare digest，v1～v5 `_state_blob_refs()` 语义不变。prefix、大小写、长度或 digest 不匹配一律 fail closed。

## 2. `ResearchSpecV1`

顶层 exact keys：

| 字段 | 类型/约束 |
|---|---|
| `schema_version` | literal `1` |
| `spec_id` / `spec_hash` | 按 §1 派生 |
| `normalized_question` | non-empty string；只用于审计，不参与 source registry 规则匹配 |
| `intent_type` | `official_exact_fact|comparison|top_n|policy|open_research` |
| `answer_locale` | BCP-47 string |
| `subjects` | ordered `SubjectV1[]`，每项 exact keys=`subject_id,label,aliases,entity_type` |
| `user_constraints` | exact keys=`schema_version,as_of_date,jurisdictions,preferred_authority_ids,explicit_exclusions` |
| `work_dimensions` | ordered `WorkDimensionV1[]`，exact keys=`dimension_id,ordinal,requirement_ids,search_concepts,time_scope,source_constraint` |
| `requirements` | ordered tagged union，ordinal 从 0 连续且 key/id 唯一 |
| `completion_policy_ref/hash` | ref 与 64hex hash 一致 |
| `render_profile_ref/hash` | ref 与 64hex hash 一致 |
| `compiler_policy_hash` | 64hex |

共用 `ScopeV1` exact keys：`schema_version,jurisdiction,geography_ids,population_definition,qualifiers`。

共用 `TimeScopeV1` exact keys：`schema_version,kind,start,end,as_of,label`：

- `instant` 只允许 `as_of`；`period` 只允许 `start/end` 且 `start<=end`；
- `latest` 的 `as_of` 是编译时冻结日期；`timeless` 的三日期均为 null。

共用 `SourceConstraintV1` exact keys：`schema_version,first_party,authority_roles,preferred_authority_ids,eligible_source_types,minimum_source_families,secondary_evidence`；`first_party=required|preferred|not_required`，`secondary_evidence=context_only|support_allowed`。

### 2.1 `ScalarRequirement`

Exact keys：`schema_version,requirement_id,ordinal,kind,key,label,importance,subject_ids,scope,time_scope,source_constraint,value_schema,cardinality`；`kind=scalar`，`importance=required|optional`。

- `value_schema` exact keys=`schema_version,value_type,quantity_kind,canonical_unit,accepted_units,definition,tolerance`。
- unit exact keys=`unit_id,symbol,to_canonical_numerator,to_canonical_denominator`，分母>0。
- tolerance exact keys=`mode,numerator,denominator`，`mode=exact|absolute|relative_ppm`。
- cardinality exact keys=`minimum,maximum`，scalar 固定 `1/1`。
- SC-STATS-2 固定编译为：
  - `key=population.total.year_end`，time=`instant/2024-12-31`，definition=`year_end_total_population`；
  - `key=population.births.period`，time=`period/2024-01-01..2024-12-31`，definition=`births_during_period`。
  两者 canonical unit=`person`，接受 `person` 与 `ten_thousand_person(×10000)`；数值不进入生产 contract。

### 2.2 `MatrixRequirement`

Exact keys：共用前缀加 `axes,cell_policy,required_cells,coverage`；`kind=matrix`。

- axis exact keys=`axis_id,role,label,members`，role=`subject|criterion|time|custom`。
- member exact keys=`member_id,label,subject_id,value_schema`。
- `cell_policy` exact keys=`minimum_admitted_bindings,allow_inference`。
- `required_cells` exact keys=`mode,excluded`，v1 仅 `mode=cartesian_product`；excluded 是稳定 member-id tuple。
- `coverage` exact key=`minimum_ratio_ppm`，0..1_000_000。
- 不持久化 mutable cells；唯一算法 `derive_matrix_cell_id(requirement_id,axis_member_ids)`：`axis_member_ids` 必须按 requirement `axes` 顺序各取一个 member ID，`cell_id='cell_'+sha256(canonical_json({requirement_id,axis_member_ids}))[:24]`。candidate、admission、assessment 只能调用该算法。

### 2.3 `CollectionRequirement`

Exact keys：共用前缀加 `item_schema,selection,dedupe`；`kind=collection`。

- `item_schema` exact keys=`entity_type,unique_key,fields`；field exact keys=`field_key,label,value_type,required,unit,minimum_admitted_bindings`。
- `selection` exact keys=`mode,minimum_items,maximum_items,as_of,ranking_rule`；v1 mode=`top_n|bounded_set`。
- `ranking_rule` exact keys=`metric_key,direction,tie_breakers,missing_metric`；direction=`ascending|descending`，missing=`ineligible|last`。
- `dedupe` exact keys=`normalizer,collision_policy`；v1 固定 `nfkc_casefold_v1` 与 `merge_equal_identity_else_conflict`。
- 唯一算法 `derive_collection_item_id(requirement_id,unique_key_values)`：按 `item_schema.unique_key` 的 field-key 顺序取值，每个 string 先做 Unicode NFKC 再 casefold（非 string 按 canonical JSON scalar），`item_id='item_'+sha256(canonical_json({requirement_id,normalized_unique_key_values}))[:24]`。只在发现实体后创建；candidate、admission、assessment 只能调用该算法，禁止预造 N 个空 slot。

### 2.4 `ClaimSetRequirement`

Exact keys：共用前缀加 `claim_kinds,topic_facets,coverage_mode,contradiction_policy`；`kind=claim_set`。

- claim-kind exact keys=`claim_kind,minimum_claims,maximum_claims,minimum_admitted_bindings,support_rule`；kind=`conclusion|limitation|counterevidence|uncertainty|policy_fact|impact_inference`；support=`admitted_binding|admitted_binding_or_registered_inference`。
- facet exact keys=`facet_id,label,minimum_claims`。
- v1 `coverage_mode=all_minima`，`contradiction_policy=surface|block_completed`。
- requirement 只声明 minima/facets，不预造 claim IDs。

### 2.5 compiler merge 与错误

Merge precedence：

1. deterministic extractor 先生成 base，LLM 只提供 candidate bundle；base requirement 不可删除。
2. 先按 requirement `key` 合并再生成 ID；exact duplicate 去重。
3. `kind/subjects/scope/time/value unit/axes/unique key/ranking rule` 不同即冲突，不 last-write-wins。
4. `required>optional`；`first_party required>preferred>not_required`；minimum 取较大，maximum 取较小 non-null，空交集冲突。
5. eligible source types 取交集；aliases/search concepts/facets union 后稳定排序。
6. LLM 新增 requirement 默认 optional，只有 deterministic policy 可提升 required；非法 candidate bundle 整包丢弃并记录诊断，最多一次 structured repair；deterministic base 非法则 retrieval 前终止。
7. unknown intent 由 deterministic compiler 生成 claim-set fallback，不依赖 LLM 成功。

`ResearchSpecValidationError(code,path,message)` 的稳定 code：`spec_keys_differ,spec_schema_unsupported,requirement_kind_unsupported,requirement_id_mismatch,spec_hash_mismatch,duplicate_requirement_key,duplicate_requirement_id,ordinal_invalid,reference_missing,time_scope_invalid,unit_invalid,bounds_empty,spec_merge_conflict,policy_ref_hash_mismatch,continuation_spec_mismatch`。persisted malformed requirement 一律 block，不静默删除。

## 3. `OfficialSourcePolicyV1`

固定 owner：`backend/deskpet/retrieval/official_sources.py`。禁止复用或扩充 legacy `_SOURCE_PACK_RULES` 来实现 v6。

registry exact keys=`schema_version,registry_id,entries`；entry exact keys=`schema_version,authority_id,jurisdiction,authority_roles,organization_names,registrable_domains,source_types,search_directives,seed_urls,priority`。初始通用条目：`authority_id=cn.nbs`、jurisdiction=`CN`、role=`national_statistics_office`、domain=`stats.gov.cn`、source type=`official_statistic`、directive=`site:stats.gov.cn`。registry 禁止年份、指标值、答案页 URL、完整测试问题；`policy_hash=sha256(canonical_json(registry))`。

```python
resolve_official_sources(request: OfficialSourceRequestV1) -> tuple[OfficialSourceTargetV1, ...]
verify_official_source(target: OfficialSourceTargetV1, final_url: str) -> SourceAuthorityMatchV1
```

Request exact keys=`schema_version,request_id,requirement_ids,jurisdiction,authority_roles,source_types,preferred_authority_ids,locale`；request ID 从除 ID 外 canonical payload 派生。Target exact keys=`schema_version,target_id,policy_hash,authority_id,jurisdiction,authority_role,organization_name,registrable_domains,source_types,search_directives,seed_urls,domain_match,priority,reason_codes`；`domain_match=host_or_subdomain`。

`RouteDecisionV1` exact keys=`schema_version,route_id,run_id,spec_hash,policy_ref,policy_hash,capability_snapshot_hash,source_health,budget,work_groups,initial_route,allowed_escalations,reason_codes`。source health item exact keys=`authority_id,status,reason_code`，status=`healthy|degraded|unavailable`；budget exact keys=`query,fetch,browser,llm,lane`（非负整数 hard limits）；work group exact keys=`work_group_id,requirement_ids,merge_key`；escalation exact keys=`from_route,to_route,condition_code`。所有数组按 stable ID/key 排序；route ID 从其余 canonical payload 派生。恢复读取 registered decision ref/hash，不按当前 health/policy重算。

resolver 只读结构化 jurisdiction/role/type，不读完整问题。query planner 把 work-dimension concepts + time + directive 组合；SearchGateway 保持 provider-neutral。fetch 后按 final redirect URL 重新验证 exact host/subdomain，`stats.gov.cn.evil.com` 与站外 redirect 拒绝。RouteDecision 持久化 target + policy hash；恢复不按新 registry 偷偷重算。

Official search 是独立 logical read effect，canonical result contracts：

- `SourceLocatorV1` exact keys=`schema_version,locator_id,canonical_url,final_url,canonical_url_hash,final_url_hash,authority_id,verification_status,verification_policy_hash,redirect_chain_hashes`；verification status=`verified|rejected|unverified`，redirect hashes 保持实际顺序。unverified 固定 `final_url=canonical_url,final_url_hash=canonical_url_hash,redirect_chain_hashes=[]`；verified/rejected 保存实际 final 与 ordered redirect hashes。ID 从除 ID 外 payload 派生。完整 URL 只存在 owner-protected canonical evidence blob，可用于用户引用；trace/metrics/log 仍只允许 hash/typed outcome。
- `SearchCandidateV1` exact keys=`schema_version,candidate_id,ordinal,source_locator_ref,title_hash,snippet_hash,authority_match,reason_codes`；`authority_match=verified|rejected|unverified` 且等于 locator status；ordinal 从 0 连续，candidate ID 从 payload 派生，title/snippet 原文不持久化到 search result。
- `OfficialSearchResultV1` exact keys=`schema_version,result_id,logical_effect_id,attempt_no,request_id,query_hash,target_id,candidates,outcome,error_code,deadline_id`；outcome=`succeeded|empty|timeout|cancelled_business|failed`，candidates 按 ordinal 排序，非 succeeded 必须为空。所有已执行并收敛的 outcome 都写 blob并 canonical commit effect；只有尚未派发的 search 无 result。

Search candidate locator 通常是 `unverified`（只做 host/suffix precheck）；page fetch 只能消费 committed candidate 的 unverified locator，完成 redirect 后生成新的 `verified|rejected` final locator。只有 verified final locator 能进入 `PageRecordV1`，rejected redirect 形成 committed blocked/failed page result。恢复直接复用 search canonical result，不重算 registry、不重复真实 search；PageRecord locator 必须同 run、authority/hashes 与 record 完全一致。

## 4. `DurableDeadlineV1`

持久化 exact keys：`schema_version,deadline_id,parent_deadline_id,logical_scope,policy_hash,budget_ms,remaining_ms,created_at,last_observed_at,wall_not_after,offline_policy,rollback_tolerance_ms,revision,status,terminal_reason,terminal_at`。所有 wall timestamps 都是 UTC Unix seconds `REAL`，duration/budget 是整数毫秒；v1 只允许 `offline_policy=count`、rollback tolerance=2000ms、status=`open|expired|completed`。

workflow schema v4 的 durable owner：

```sql
CREATE TABLE workflow_research_deadlines (
  deadline_id TEXT PRIMARY KEY,
  schema_version INTEGER NOT NULL CHECK(schema_version=1),
  run_id TEXT NOT NULL,
  parent_deadline_id TEXT,
  logical_scope TEXT NOT NULL,
  policy_hash TEXT NOT NULL,
  budget_ms INTEGER NOT NULL CHECK(budget_ms > 0),
  remaining_ms INTEGER NOT NULL CHECK(remaining_ms >= 0),
  created_at REAL NOT NULL,
  last_observed_at REAL NOT NULL,
  wall_not_after REAL NOT NULL,
  offline_policy TEXT NOT NULL CHECK(offline_policy='count'),
  rollback_tolerance_ms INTEGER NOT NULL CHECK(rollback_tolerance_ms >= 0),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  status TEXT NOT NULL CHECK(status IN ('open','expired','completed')),
  terminal_reason TEXT,
  terminal_at REAL,
  FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
  FOREIGN KEY(parent_deadline_id) REFERENCES workflow_research_deadlines(deadline_id) ON DELETE CASCADE
);
CREATE INDEX idx_workflow_research_deadlines_run_status
  ON workflow_research_deadlines(run_id,status);

CREATE TABLE workflow_research_resource_budgets (
  budget_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  policy_hash TEXT NOT NULL,
  resource_kind TEXT NOT NULL CHECK(resource_kind IN ('query','fetch','browser','llm','lane')),
  hard_limit INTEGER NOT NULL CHECK(hard_limit >= 0),
  reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved >= 0),
  consumed INTEGER NOT NULL DEFAULT 0 CHECK(consumed >= 0),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  UNIQUE(run_id,resource_kind),
  FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
);
CREATE TABLE workflow_research_resource_reservations (
  reservation_id TEXT PRIMARY KEY,
  budget_id TEXT NOT NULL,
  deadline_id TEXT NOT NULL,
  effect_id TEXT NOT NULL,
  amount_reserved INTEGER NOT NULL CHECK(amount_reserved > 0),
  amount_actual INTEGER CHECK(
    amount_actual IS NULL OR (amount_actual >= 0 AND amount_actual <= amount_reserved)
  ),
  status TEXT NOT NULL CHECK(status IN ('reserved','committed','released')),
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  UNIQUE(effect_id,budget_id),
  FOREIGN KEY(budget_id) REFERENCES workflow_research_resource_budgets(budget_id) ON DELETE CASCADE,
  FOREIGN KEY(deadline_id) REFERENCES workflow_research_deadlines(deadline_id) ON DELETE CASCADE,
  FOREIGN KEY(effect_id) REFERENCES workflow_effects(effect_id) ON DELETE CASCADE
);
```

root deadline/resource budget rows 在 route decision checkpoint 前按 run+policy 幂等 create-or-load；已存在时使用原 persisted wall timestamps，只校验 immutable policy/budget，不用重试时的新 wall 值覆盖。`begin_idempotent_read_attempt()` 事务顺序固定：validate run fence → 对 page child create-or-load 后严格比对 parent/scope/budget/wall guard（search 使用 root）→ `UPDATE deadline ... WHERE revision=? AND status='open'` → `UPDATE resource_budget SET reserved=reserved+amount,revision=revision+1 WHERE consumed+reserved+amount<=hard_limit` → insert effect attempt/head → insert target/resource reservations → commit。任何 fault 全 rollback。commit/reconcile 在同事务把 budget `reserved` 扣回、`consumed += amount_actual`，reservation→committed/released；同一 reservation 只能结算一次。

恢复算法：

1. 不加载旧进程 monotonic 值；`raw_delta=wall_now-last_observed_at`。
2. 回退超过 tolerance → expired/`clock_rollback`；否则 `effective_wall=max(wall_now,last_observed_at)`。
3. `remaining_ms -= max(0,effective_wall-last_observed_at)`；达到 0 或 `effective_wall>=wall_not_after` → expired (`budget_exhausted|wall_guard`)。
4. 未过期则 revision+1、更新 last observed；由 `EffectJournal.begin_idempotent_read_attempt()` 在同一个 `BEGIN IMMEDIATE` 中以 run fence/version CAS 持久化 deadline revision、effect attempt 与 budget/target reservation。事务胜者才建立 `local_monotonic_deadline_ns=mono_now+remaining_ms*1e6` 并发 upstream；checkpoint 只是 node 返回后的镜像，不是 page I/O 前的 owner。
5. 正常 checkpoint 只扣本进程 monotonic delta并更新 wall observation；恢复才补扣离线窗口，避免双扣。

page child：`remaining=min(20_000,parent.remaining)`、wall guard 取 min、ID 从 `parent_id+logical_page_id+attempt_group` 派生。robots/Scrapling/HTTPX/Playwright/Edge/Jina 共用该 child，不重置预算。

### 4.1 v6 logical read attempts

workflow schema v4 为 `workflow_effects` 增加 nullable `logical_effect_id/attempt_no/supersedes_effect_id`，并新增：

```sql
ALTER TABLE workflow_effects ADD COLUMN logical_effect_id TEXT;
ALTER TABLE workflow_effects ADD COLUMN attempt_no INTEGER
  CHECK(attempt_no IS NULL OR attempt_no >= 1);
ALTER TABLE workflow_effects ADD COLUMN supersedes_effect_id TEXT
  REFERENCES workflow_effects(effect_id) ON DELETE RESTRICT;
CREATE TABLE workflow_effect_attempt_heads (
  logical_effect_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  latest_attempt_no INTEGER NOT NULL CHECK(latest_attempt_no >= 1),
  canonical_effect_id TEXT,
  policy_id TEXT NOT NULL,
  updated_at REAL NOT NULL,
  FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
  FOREIGN KEY(canonical_effect_id) REFERENCES workflow_effects(effect_id)
);
CREATE UNIQUE INDEX uq_workflow_effect_logical_attempt
  ON workflow_effects(run_id,logical_effect_id,attempt_no)
  WHERE logical_effect_id IS NOT NULL;
CREATE INDEX idx_workflow_effect_attempt_heads_run
  ON workflow_effect_attempt_heads(run_id,updated_at);
```

- `logical_effect_id=sha256(run_id|route_id|operation_kind|target_or_page_id|ordinal)`，不含 attempt；`effect_id=sha256(run_id|logical_effect_id|attempt_no)`。
- v6 attempt 的现有 `effect_fingerprint=sha256(logical_effect_id|attempt_no)`，因此不会撞上 v3 的 `UNIQUE(run_id,effect_fingerprint)`；v1～v5 fingerprint 算法不变。三个新增字段必须 all-null（legacy）或 `logical_effect_id+attempt_no` 同时非空（v6），`attempt_no>1` 时 `supersedes_effect_id` 必须指向同 run、同 logical ID、attempt-1；这些跨行约束由 begin API 在 `BEGIN IMMEDIATE` 内验证并由迁移/contract tests 审计。
- v6 idempotent read policy 固定 `policy_id=deep-research-v6-page-read-v1,max_attempts=2`；一次 attempt 内每个 transport 最多一次，无库内隐藏 retry。
- `begin_idempotent_read_attempt(fence, logical_effect_id, attempt_no, deadline_revision, budget, targets, policy)` 在单事务校验 head/latest、deadline revision、run fence、attempt cap，创建 effect 与 reservations；v1～v5 继续旧 `begin()` 路径。
- `commit_idempotent_read_attempt(canonical_result_ref,dependency_refs,...)` 先从 typed result 重算 exact dependency closure并要求与 caller refs 相等：official search=`result + all candidate locators`；page=`result + input/final locator + body + result 中任何独立 evidence refs`（negative page通常无 body）。所有 wire refs strict parse；同一事务验证整个 closure 都有 same-run staging owner，为每个 digest 添加 `owner_kind='effect',owner_id=effect_id`，把 sorted bare closure 写入现有 `workflow_effects.artifact_refs_json` 作为 auditable v6 effect closure，删除对应 staging owners，再 CAS `canonical_effect_id IS NULL`。effect owner 保留到 effect record安全删除；checkpoint owner只是附加 owner。closure 任一缺失/多余都整体 rollback。
- v6 committed row 的 `outcome_json` exact keys=`schema_version,canonical_result_ref,dependency_refs,result_kind`，result kind=`official_search|page_extraction`，refs 为 wire、dependency refs去重排序且包含 canonical result自身；parse 后必须逐项等于 `artifact_refs_json` 的 sorted bare closure。recovery/admission 只从 head→canonical effect→该 outcome 取 result，不扫描 staging或“最新 effect”。
- `reconcile_idempotent_read_for_retry(...)` 同事务将 stale running/uncertain 旧 attempt→failed、按保守规则把全部 reserved amount 计为 consumed、释放 target reservation并写 ended_at；它不创建或预占下一 attempt，head 的 latest 仍指向这个真实存在的 failed row。随后 `begin(...,attempt_no=latest+1)` 在另一个 `BEGIN IMMEDIATE` 中验证 cap/余额，插入 successor 后才原子更新 head.latest，禁止出现 head 指向不存在 attempt 的 gap。
- admission 只读取 head 的 `canonical_effect_id` result，不读取“最新 effect”；late result 对非 running/canonical-loser 标 `late_orphan`，不能覆盖 head、推进 graph 或延长 deadline。

Effect replay：committed canonical result 即使过期仍可用于 assessment；有效 lease running 不双开；旧 lease running→uncertain。blob 写入与 effect commit 之间只有 run-staging owner，没有 provisional locator，因此任何未完成 canonical commit 的 uncertain attempt 都按“无可恢复结果、reserved resource 已消费”结算；staging blob 走 orphan grace 清理，不猜测复用。仅在余额/attempt 允许时以 attempt+1 重试；opaque LLM/mutation 不自动重发；late worker first-terminal-wins，标 `late_orphan` 且不延长 deadline。

`effects.py` 新增上述 begin/commit/reconcile 三个 v6 API；相同 fence/logical ID/attempt 重放返回 canonical record，冲突 fail closed。

## 5. 规范测试

- strict roundtrip/unknown keys/ID/hash/order/set normalization/property tests。
- 四 requirement union、SC-STATS-2/4 golden、matrix Cartesian cell、Top-N single collection、claim-set no precreated claims、merge lattice/conflicts。
- official resolver paraphrase invariance、无机构名结构化解析、非 CN 不命中、registry forbidden oracle scan、SearchGateway directive spy、evil suffix/redirect、v6 不调用 legacy source packs。
- OfficialSearchResult/SourceLocator strict roundtrip，negative search canonical replay，unverified candidate→verified/rejected final locator，URL 可用于 citation 但不进入 trace/log。
- deadline monotonic deduct、offline 5/120/expired、wall rollback clamp/fail-closed/forward jump、双恢复 CAS、committed/uncertain/opaque/late result、all transports one child deadline。
- wire ref↔bare digest roundtrip、semantic hash 与 blob digest 不混用、negative page result restart replay、head latest 永远引用真实 attempt。
- continuation snapshot exact/transitive closure、artifact projection replay、delivery attempt-5 non-due/expired claim、projection enum/unknown workflow fail-closed。

## 6. Evidence、page outcome 与 assessment contracts

所有下列对象 exact-key、`schema_version=1`、ID 从去除 ID 字段后的 canonical payload 派生并在 load 时重算。

- `PageRecordV1`: `schema_version,page_id,source_locator_ref,canonical_url_hash,final_url_hash,authority_id,source_family_id,source_tier,body_ref,body_hash,fetched_at,media_type,admission_status,reason_codes`。URL 原文只在 `SourceLocatorV1` canonical blob；`page_id="page_"+hash(final_url_hash+body_hash)[:24]`，locator hashes/authority 必须完全一致。
- `EvidenceSpanV1`: `schema_version,span_id,page_id,body_ref,start_byte,end_byte,excerpt_hash,page_part,region_kind`；UTF-8 byte range `[start,end)` 必须 code-point aligned 并重建 excerpt hash。
- `EvidenceBindingV1`: `schema_version,binding_id,requirement_id,target_kind,item_or_cell_id,field_or_facet_key,span_id,parsed_value,normalized_value,canonical_unit,time_scope,scope,definition,source_family_id,source_tier,validator_policy_hash,status,reason_codes`；`target_kind=scalar|matrix_cell|collection_field|claim_fact`，status=`admitted|rejected|conflicted`。inference proposal 不创建 binding，只复用 premise fact bindings。ID 覆盖全部 target/value 字段，禁止把 field/facet 偷放进 definition；逐 tag null/equality truth table 见 §6.2。
- `PageExtractionResultV1`: `schema_version,result_id,logical_page_id,logical_effect_id,attempt_no,source_locator_ref,page_record,spans,bindings,outcome,error_code,deadline_id,control_command_id`；outcome=`succeeded|empty|blocked|timeout|cancelled_business|failed`。locator 指向本次已知的最终 verified/rejected locator；尚未获得 final URL 时指向输入 unverified locator。非 succeeded 时 `page_record=null,spans=[],bindings=[]`；spans/bindings 按 ID 排序；result ID 覆盖全部字段。
- `PageAttemptOutcomeV1`: `schema_version,outcome_id,logical_page_id,ordinal,logical_effect_id,attempt_no,canonical_effect_id,result_ref,status,deadline_id,control_command_id`；journal status 只允许 `committed|not_started`。所有已经派发并收敛的业务 outcome（包括 empty/blocked/timeout/cancelled_business/failed）都必须先 canonical commit 一个 `PageExtractionResultV1`，因此 status=committed 且 attempt/effect/result refs 全部非空，业务 outcome/error 只从 result blob读取；not_started 的 attempt/effect/result 全为 null。数组按冻结 page plan ordinal 完整排列。
- `EvidenceFactBatchV1` 的唯一 release shape 见 §6.2；Q1 曾使用的单 `page_result_ref/admitted_binding_ids` 开发 shape 不再是 schema v1，final reader 必须 hard reject 并重建隔离 Q1 DB，不存在两套同名 v1 decoder。
- `AnswerAssessmentV1`: `schema_version,assessment_id,assessment_hash,assessment_input_hash,spec_hash,evidence_head_hash,policy_hash,requirement_results,conflicts,missing_requirement_ids,minimum_useful,status,reason_codes`；`assessment_hash=sha256(canonical_json(assessment_without_assessment_id_and_assessment_hash))`，`assessment_id='asa_'+assessment_hash[:24]`。input hash公式见§6.2。status=`completed_candidate|partial_candidate|insufficient|needs_evidence`；requirement result exact keys=`requirement_id,item_or_cell_id,support_status,binding_ids,reason_codes`。完整serialized assessment的registered ref是独立wire ref；load同时重算semantic/blob identity。
- `ClaimRecordV1`: `schema_version,claim_id,requirement_id,item_or_cell_id,claim_kind,normalized_proposition,binding_ids,inference_ref,support_status,visibility`；claim kind=`fact|inference|preference|limitation|counterevidence|uncertainty`，visibility=`user|audit`。
- `ClaimBatchV1`: `schema_version,batch_id,run_id,spec_hash,evidence_head_hash,assessment_hash,claim_policy_hash,claims,visible_claim_ids,status`；status=`valid|invalid`，claims 按 claim ID 排序，visible IDs 必须恰好引用 `visibility=user` 且 support 可显示的 claims。batch ID 从其余 payload 派生。
- `QualityAuditV1`: `schema_version,audit_id,run_id,spec_hash,assessment_hash,claim_batch_ref,quality_policy_hash,hard_gate_status,hard_failure_codes,soft_scores,repair_count,answer_status`；hard gate=`passed|failed`，soft_scores exact keys=`readability,source_diversity,analysis_depth,counterevidence,uncertainty`，值为 0..1_000_000 ppm；repair count 非负，audit ID 从其余 payload 派生。hard failed 不得 answer completed。

Cancellation origin：adapter 内部只用 typed `GenerateNowFence(command_id,deadline_id)` 与 `PageDeadlineExpired(deadline_id)` 表示业务收敛；这些被 fetch node 转成 `PageAttemptOutcomeV1` partial patch。父 runner/shutdown 的 `asyncio.CancelledError` 永远不捕获转换。TaskGroup 在收到业务 signal 时停止派发新页、shield 已完成 attempt commit、等待 in-flight 在 child deadline 内结算，再按冻结 page plan/ordinal 补 `not_started`。下一 `admit_evidence` node 只有在包含 outcomes 的 frontier checkpoint 已提交后才调用 control port `observed -> settled`，因此 command journal 与 partial evidence 有 durable 顺序。

### 6.1 `ResearchContinuationSnapshotV1`

continuation 的唯一继承 owner 是 registered snapshot blob，exact keys=`schema_version,snapshot_id,snapshot_hash,run_id,workflow_name,workflow_version,spec_ref,spec_hash,fact_batch_refs,evidence_head_hash,assessment_ref,assessment_hash,assessment_input_hash,claim_batch_ref,provenance_refs,policy_refs,closure_refs`。

- `fact_batch_refs` 保持 append order；`provenance_refs` 与 `closure_refs` 去重排序。`policy_refs` exact keys=`compiler,route,extraction,llm_extract,llm_repair,llm_inference,admission,inference,assessment,claim,quality`，每项为预注册 policy/profile wire ref；即使本 run 未触发 repair，repair profile 仍存在。policy ref 只归该 object，不同时列入 provenance。
- snapshot builder 必须按唯一 transitive expansion 构造 provenance：batch→page results/每个 candidate slot 的 producer outcome+bundle/每个 inference slot 的 effect outcome+proposal bundle/admitted facts/registered inferences/batch provenance；page result→locator/page/body/spans/bindings；producer/effect outcome→prompt/raw/result/repair request/prior outcome/profile dependencies（profile本身归 policy）；inference→premise facts/bindings/model policy（model/inference policy归 policy）；assessment/claim→它们引用的 fact/inference/binding owners。`provenance_refs` 是此图中除 spec、top-level batches、assessment、claim batch、policy refs 外的全部 registered refs 的恰好集合。
- `closure_refs` 必须恰好等于 `{spec_ref} ∪ fact_batch_refs ∪ {assessment_ref,claim_batch_ref} ∪ provenance_refs ∪ values(policy_refs)`，不得漏任何 transitive ref、夹带任意 same-run blob或把 ref 放入错误分类；少/多/错类都 fail closed。
- `snapshot_hash=sha256(canonical_json(snapshot_without_snapshot_id_and_snapshot_hash))`，`snapshot_id='rcs_'+snapshot_hash[:24]`。snapshot blob ref 指向包含这两个字段的完整 bytes，digest 不要求等于 semantic snapshot_hash；load 同时重算 semantic identity 与 blob digest。
- snapshot `assessment_input_hash` 必须逐字段等于 assessment blob，并由 ordered batch chain按§6.2公式重算；不一致fail closed。
- snapshot 在 terminal manifest 之前持久化；现有 `workflow_research_snapshots.snapshot_hash` 保存 semantic snapshot hash，历史命名的 `manifest_ref` 列保存完整 snapshot blob的 **bare digest**（domain DTO 读取时才 `format_blob_ref`）。v6 repository 必须移除旧 `manifest_ref == snapshot_hash` 校验，改为分别验证 semantic hash与 blob digest/owner；列名本轮不迁移但物理格式服从 §1。snapshot 不反向引用 terminal manifest，避免 hash cycle。
- `TerminalDeliveryManifestV1.continuation_snapshot_ref/hash` 引用该对象。T12 只从 server-side terminal manifest 取 snapshot pointer，再以 snapshot closure + current-run checkpoint owners 验证 inherited refs；客户端不能提交或扩展 closure。

### 6.2 v6 candidate、admission、inference 与 durable LLM contract

Q1 的 exact-fact binding 是垂直切片，不是通用 extraction owner。v6 最终生产链固定为 `CandidateProducerOutcomeV1 -> EvidenceCandidateBundleV1 -> admit facts/checkpoint -> InferenceProposalBundleV1 -> register inferences/checkpoint -> AnswerAssessmentV1 -> ClaimRecordV1`；lane 只能提交 producer/result blob，只有主 graph 的 fact/inference frontier nodes 可以串行追加同一 fact-batch/head，禁止多个 lane 竞争 chained head。

`CandidateProducerOutcomeV1` exact keys=`schema_version,outcome_id,origin,status,logical_page_id,work_group_id,bundle_ref,llm_effect_outcome_ref,policy_ref,dependency_refs`；origin=`deterministic|llm`，status=`validated|malformed|opaque_uncertain|deadline|budget_denied`，ID从其余payload派生。deterministic+validated必须 `bundle_ref!=null,llm_effect_outcome_ref=null,policy_ref=extraction policy`，dependencies恰为page result、bundle、policy refs；llm outcome的status/result必须逐字段等于被引 `ResearchLLMEffectOutcomeV1`，`policy_ref=effect_outcome.profile_ref`（正常为extract，修复成功为repair），dependencies恰为page result、bundle（若有）、LLM outcome、selected profile refs。exact fact只走deterministic origin：LLM port fail-on-call、call reservation=0，但仍产生registered producer outcome/bundle并进入generic admission。

`EvidenceCandidateBundleV1` exact keys=`schema_version,bundle_id,run_id,spec_hash,work_group_id,logical_page_id,page_plan_ordinal,page_result_ref,route_decision_ref,route_policy_ref,extraction_policy_ref,repair_round,candidates,bundle_reason_codes`。`repair_round=0|1`；`candidates`按 `(candidate_ordinal,candidate_id)` 严格升序且ordinal从0连续；`bundle_id='ecb_'+sha256(canonical_json(bundle_without_bundle_id))[:24]`。deterministic origin的bundle随producer outcome进入closure；LLM origin还必须通过effect outcome前向包含prompt/raw/repair/profile refs。

candidate 是 tagged union，共有 exact keys=`schema_version,candidate_id,candidate_kind,work_group_id,logical_page_id,page_plan_ordinal,candidate_ordinal,requirement_id,span_start_byte,span_end_byte,excerpt_hash,normalized_proposition,payload`。byte range 是 `PageRecordV1.body_ref` 解码后 UTF-8 bytes 的 `[start,end)`，必须 code-point aligned，且 `sha256(body_bytes[start:end])=excerpt_hash`；不得接受 LLM 自报但无法由 body 重建的摘录。`candidate_id='ecd_'+sha256(canonical_json(candidate_without_candidate_id))[:24]`。各 tag 的 `payload` exact keys 与 nullability：

- `scalar`: `item_or_cell_id,value,canonical_unit,time_scope,scope,definition`，其中 item/cell 为 null；
- `matrix_cell`: `cell_id,axis_member_ids,field_key,value,canonical_unit,time_scope,scope,definition`；member IDs 按 axes 顺序，cell ID 必须等于 §2.2 唯一算法；
- `collection_field`: `item_id,unique_key_values,field_key,value,canonical_unit,as_of,rank_inputs`；key values 按 `item_schema.unique_key` 顺序且 item ID 必须等于 §2.3 唯一算法，同 item 的每个 required field 各有独立 candidate/binding；
- `claim_fact`: `claim_instance_id,facet_key,value,canonical_unit,time_scope,scope,definition`；facet 只允许 compiler 冻结的 `policy_issuer|policy_document|policy_date|policy_commitment` 或 requirement topic facet，且每个 policy facet 独立 candidate；
- page-local candidate bundle 只允许上述四种 evidence-bearing fact tags并要求真实 page span；inference 不得伪装成某一页 candidate。

`InferenceProposalBundleV1` exact keys=`schema_version,bundle_id,run_id,spec_hash,work_group_id,input_evidence_head_hash,ordinal,profile_ref,premise_fact_refs,proposals`；只有fact frontier checkpoint后创建。`ordinal`恰为persisted `RouteDecisionV1.work_groups` stable order中的ordinal；`profile_ref`必须等于selected effect outcome profile（normal inference或repair）；top-level premise refs必须恰等于所有proposal premise refs的sorted unique union，proposals空则union也空，不得夹未使用fact。ID从其余payload派生。

proposal exact keys=`schema_version,proposal_id,requirement_id,inference_kind,item_or_cell_id,facet_ids,normalized_proposition,premise_fact_refs,model_id,model_policy_ref`；requirement必须属于该work group，全部premise facts的persisted requirement ID必须相同且等于它，跨requirement v1 fail `inference_premise_requirement_mismatch`。kind/facet/item必须被该 requirement允许；facet/premise refs去重排序且非空，proposal ID覆盖全部字段；proposal不含page span。inference synthesis按 `(work_group_id,inference_kind,proposal_id)`稳定排序，restart只复用durable outcome/bundle，不重发。

binding/fact truth table：scalar 的 top-level target/key均 null；matrix `item_or_cell_id=semantic_payload.cell_id`、`field_or_facet_key='value'`、axis members按spec顺序；collection `item_or_cell_id=semantic_payload.item_id`、`field_or_facet_key=semantic_payload.field_key`；claim fact `item_or_cell_id=semantic_payload.claim_instance_id`、`field_or_facet_key` 必须等于其唯一 `facet_ids[0]`、persisted claim kind固定=`policy_fact`（renderer才投影`fact`）。所有 fact binding 的 parsed/normalized value非null；unit仅当 requirement/field无unit时null，time/scope/definition必须与 candidate+spec canonical值逐字段相等。不适用字段必须缺失于tag payload或按top-level truth table为null，loader遇重复identity不等立即fail。

collection `rank_inputs` exact为 array，顺序恰为 `[ranking_rule.metric_key]+tie_breakers`；entry exact keys=`field_key,value,missing`，value为canonical scalar，missing=true时value=null且按`missing_metric`处理，false时value非null。fact semantic payload逐字保留该array；unique key values按§2.3顺序/normalizer。

`ResearchLLMEffectProfileV1` exact keys=`schema_version,profile_id,profile_hash,workflow_version,role,response_format_hash,policy_id,policy_version,tool_spec_version,schema_hash,permission_policy_version,effect_type,tool_name,max_attempts,max_repair_rounds,late_result_policy`；hash/ID按通用规则由去除两identity字段的 canonical payload派生。v5 default prepared-call bytes/effect ID golden必须不变。v6预注册三个独立 profile（共有 `workflow_version=v6,policy_version=v1,permission_policy_version=research-readonly-v1,effect_type=research_llm,tool_name=research_llm_v2,max_attempts=1,late_result_policy=commit_or_hold_no_resend`）：

- extract：role=`evidence_candidate_extract`，policy=`deep-research-v6-extraction-at-most-once`，tool spec=`deep-research-v6-evidence-candidate-v1`，schema=`evidence-candidate-bundle-v1`，response hash=frozen `EvidenceCandidateBundleV1` schema hash，max repair=1；
- inference：role=`evidence_inference_synthesize`，policy=`deep-research-v6-inference-at-most-once`，tool spec=`deep-research-v6-inference-proposal-v1`，schema=`inference-proposal-bundle-v1`，response hash=frozen `InferenceProposalBundleV1` schema hash，max repair=1；
- repair：role=`evidence_structured_repair`，policy=`deep-research-v6-repair-at-most-once`，tool spec=`deep-research-v6-structured-repair-v1`，schema=`structured-repair-result-union-v1`，response hash为 candidate/inference bundle tagged union schema hash，max repair=0。

shared role enum增加上述三值；prepared-call fixture分别冻结三个v6 profile。snapshot `policy_refs.llm_extract/llm_inference/llm_repair`分别引用它们，不能用单值代表多blob。

v6 logical effect key为 `v6-{extract|infer|repair}:{checkpoint_ns}:{checkpoint_id}:{task_id}:{work_group_id}:{logical_page_id_or_head}:r{repair_round}`，stable call ID为其SHA-256 hex。`EvidenceRepairRequestV1` exact keys=`schema_version,repair_id,result_kind,original_profile_ref,repair_profile_ref,original_prompt_ref,prior_outcome_ref,raw_result_ref,validation_reason_codes,logical_page_id,work_group_id,repair_round`，result kind=`candidate_bundle|inference_bundle`，round固定1，ID从其余payload派生，media type=`application/vnd.deskpet.deepresearch-v6-repair+json`。

`ResearchLLMEffectOutcomeV1` exact keys=`schema_version,outcome_id,logical_effect_id,effect_id,status,result_kind,profile_ref,prompt_ref,raw_result_ref,result_ref,repair_request_ref,prior_outcome_ref,repair_round,reason_codes,dependency_refs`；status=`validated|malformed|opaque_uncertain|deadline|budget_denied`，result kind=`candidate_bundle|inference_bundle`。round0使用extract/inference profile且repair/prior refs=null；round1只使用repair profile且repair/prior refs非null。validated必须raw+result；malformed必须raw且result=null；opaque/deadline/budget_denied的raw/result均null。`dependency_refs`恰等于该outcome所有非null registered refs `{profile,prompt,raw,result,repair_request,prior_outcome}`去重排序，不得任意增减；ID覆盖它们。valid round1的result是唯一selected bundle，round0 malformed outcome/raw/request仍在provenance。

provider 返回后的 transaction/fault boundary 固定为 `after_provider_return -> after_raw_blob_put -> after_raw_effect_owner -> after_outcome_commit -> after_node_checkpoint`。raw/prompt/bundle/repair/outcome/profile dependency refs 在 outcome commit 前由 pending/staging owner持有，commit 时原子增加 same-run effect owner，node checkpoint 再原子增加 checkpoint owner；replay reader只从 canonical effect outcome解码选中 bundle。首次 malformed 时仅允许一次结构化 repair；repair 不得改变 page/body/spec/work-group refs，也不得引入原 body 中不存在的 span。第二次 malformed、预算不足、opaque uncertain 或 deadline到期都 canonical commit terminal outcome，不再发送；late result按 `commit_or_hold_no_resend` 收敛。

admission 严格按 `(work_group_id,page_plan_ordinal,candidate_ordinal,candidate_id)` 排序，逐 candidate 使用固定验证顺序：`shape -> identity -> spec_target -> body_span -> source_policy -> type_semantics -> unit_time_definition -> duplicate -> conflict`。稳定 reason code 依次为 `candidate_shape_invalid,candidate_id_mismatch,requirement_missing,requirement_kind_mismatch,span_out_of_bounds,span_not_utf8_aligned,excerpt_hash_mismatch,source_policy_rejected,payload_semantics_invalid,unit_mismatch,time_scope_mismatch,definition_mismatch,duplicate_candidate,conflicting_candidate`；第一项失败即决定 rejected reason，后续检查不覆盖它。

`AdmittedResearchFactV1` 是 persisted semantic owner，exact keys=`schema_version,fact_id,run_id,spec_hash,requirement_id,target_kind,item_or_cell_id,field_or_facet_key,candidate_id,page_id,span_id,binding_id,source_family_id,source_tier,admission_policy_hash,status,semantic_payload`；`target_kind=scalar|matrix_cell|collection_field|claim_fact`，status=`admitted|conflicted`。`semantic_payload` 是严格 tagged payload：scalar=`value,canonical_unit,time_scope,scope,definition`；matrix=`axis_member_ids,cell_id,value,canonical_unit,time_scope,scope,definition,claim_kind`；collection=`unique_key_values,item_id,field_key,value,canonical_unit,as_of,rank_inputs`；claim fact=`claim_instance_id,claim_kind,facet_ids,value,canonical_unit,time_scope,scope,definition`。`fact_id='arf_'+sha256(canonical_json(fact_without_fact_id))[:24]`。runtime `GenericAdmittedFactV1` 只能由该 registered blob一对一解码；decoder 显式映射 axis/entity/field/value/claim/facet/as_of/conflicted 字段，不能反查 candidate bundle或形成第二持久化 schema。

`RegisteredInferenceV1` exact keys=`schema_version,inference_id,run_id,spec_hash,requirement_id,item_or_cell_id,inference_kind,facet_ids,normalized_proposition,proposed_premise_fact_refs,premise_fact_refs,premise_binding_ids,model_id,model_policy_ref,inference_policy_hash,status,reason_codes`；kind=`impact|comparison|preference|conclusion|limitation|counterevidence|uncertainty`，arrays去重排序，model fields等于committed effect/profile，ID从其余payload派生。`proposed_premise_fact_refs`逐字保存proposal的非空refs；`premise_fact_refs`只含由input head成功解析且同requirement的子集，bindings恰为这些facts的binding IDs。

status truth table：registered要求 `proposed_premise_fact_refs=premise_fact_refs`、两者及bindings非空、reason codes空；rejected要求proposed非空，resolved refs/bindings允许空且保持subset/equality，reason codes非空（`inference_premise_missing|inference_premise_requirement_mismatch|inference_semantics_invalid`）。snapshot只沿resolved premise refs扩张closure，proposed invalid/unreachable字符串仅留在registered inference audit bytes。所有registered/rejected records都进入inference batch；只有status=registered可供assessment/claim。

assessment唯一入口 `decode_assessment_inputs(ordered_fact_refs,ordered_inference_refs,spec)`：facts按上述decoder得到 `GenericAdmittedFactV1`；registered inference一对一得到非持久化 `GenericAdmittedInferenceV1(requirement_id,item_or_cell_id,assessment_claim_kind,facet_ids,premise_binding_ids,inference_ref,support_status)`。mapping固定：policy `impact -> impact_inference`，policy claim facts=`policy_fact`；open四kind原样；comparison/preference只供claim/integrity，不计matrix factual cell coverage；rejected inference不进入supported input。assessment input hash覆盖ordered fact refs、inference refs与assessment policy hash，restart round-trip必须得到字节相同requirement results/assessment hash。

`assessment_input_hash=sha256(canonical_json({schema_version:1,spec_hash,evidence_head_hash,ordered_fact_refs,ordered_inference_refs,assessment_policy_hash}))`；ordered refs只能按batch chain ordinal展开、并按每batch canonical ref序取得，caller不能重排。该值持久化到 `AnswerAssessmentV1`、参与assessment hash/ID，并复制到continuation snapshot逐字段交叉验证；交换/缺少ref或policy变化必须触发新hash/重算。

v6 唯一 `EvidenceFactBatchV1` exact keys=`schema_version,batch_id,batch_kind,run_id,spec_hash,previous_head_hash,ordinal,page_result_refs,candidate_slot_results,inference_slot_results,admitted_fact_refs,registered_inference_refs,rejected_candidate_ids,conflict_ids,provenance_refs,policy_refs,head_hash`，batch kind=`facts|inferences`。candidate slot exact keys=`logical_page_id,work_group_id,producer_outcome_ref,bundle_ref`；inference slot exact keys=`work_group_id,effect_outcome_ref,proposal_bundle_ref`，失败时bundle ref=null；两数组按route logical/work group stable order。

per-kind truth table：facts batch的page/candidate/admitted/rejected/conflict是本frontier exact结果，`inference_slot_results=[]`,`registered_inference_refs=[]`；inferences batch的`page_result_refs=[]`,`candidate_slot_results=[]`,`admitted_fact_refs=[]`,`rejected_candidate_ids=[]`,`conflict_ids=[]`，inference slots完整。`registered_inference_refs`包含本frontier所有 `RegisteredInferenceV1` records（status registered与rejected都含，按inference ID排序）；assessment只消费registered，rejected reason仍在closure。两类 `ordinal=len(existing_batch_refs)`，replay只接受exact identity。

所有 producer/effect outcome是batch的显式前向边，snapshot不得靠全库reverse lookup。refs除frozen slot order外去重排序，`policy_refs`是§6.1 exact object。授权按ref class：canonical page/search/LLM effect closure必须有same-run effect owner；deterministic producer、新bundle/fact/inference/policy可有当前node pending/staging owner；历史batch/ref必须有current-run checkpoint owner。frontier checkpoint原子提升batch及新refs并追加head，effect owners保留。`head_hash=sha256(previous_head_hash+canonical_json(batch_without_batch_id_and_head_hash))`，`batch_id='efb_'+head_hash[:24]`。

claim derivation 是纯函数且唯一：scalar/collection field/policy issuer-document-date-commitment `claim_fact` 从 `AdmittedResearchFactV1` 生成 `claim_kind=fact,inference_ref=null`；matrix direct cell 同样是 fact，跨 cell comparison/preference 必须引用同名 inference；policy `impact`、open conclusion/limitation/counterevidence/uncertainty 必须来自 exact 同名 registered inference。每个 ClaimRecord 的 `binding_ids` 恰为 fact/premise bindings 的去重排序集合，facet从 fact/inference原样传播；无法反向解析到 registered fact/inference 的 claim 一律 hard fail。golden bundles固定验证policy四facts+一个impact inference，以及open四inferences。

production graph冻结11节点：`compile_spec -> plan_route -> load_pages -> extract_candidate_bundles -> admit_facts -> synthesize_inferences -> register_inferences -> assess_answer -> render_claims -> integrity -> persist_manifest`。`plan_route`在retrieval前注册route policy/decision并checkpoint；`load_pages`只消费immutable decision ref。`extract_candidate_bundles`对exact使用deterministic producer、generic使用extract profile；`admit_facts`是facts frontier writer。只有fact head checkpoint后才运行inference profile，`register_inferences`追加inference frontier。assessment/renderer/integrity/persistence只消费registered refs，不能从进程内DTO或exact-only字段旁路。

v6 route budget的 `llm` hard limit=`generic extraction group count + inference group count + each group's one possible repair`，exact fact固定0。每个round0在effect begin时与既有token/cost reservation同事务消费一call；只有committed malformed round0才可为repair再预留一call，opaque/uncertain消耗原reservation且不重发，budget denied产生canonical no-call outcome。最大并发=`min(route lane budget,remaining llm reservations,3)`，deadline取root/page wall guard最小。`_deep_v6_context_factory`必须注入三个profile-bound durable LLM ports与generic非零budget，不能只注入fetch/blob。

### 6.3 v6 continuation identity、原子写点、恢复与 900 秒上限

continuation identity policy version 1 固定使用：`namespace=uuid5(NAMESPACE_URL,'https://deskpet.local/workflow/deep-research/v6/continuation')`（`a7e5f48a-9b2b-549f-a67c-3ebdee7b9c78`），`stable=uuid5(namespace,canonical_json({parent_run_id,policy_version:1}))`，`child_run_id=str(stable)`，`child_operation_id='research:'+child_run_id`，`child_start_operation_id=child_operation_id+':start'`，`child_request_key='research-continuation-v6:'+parent_run_id+':1'`，`logical_slot=child_request_key`，`child_request_id='continue:v6:'+child_run_id`，`child_turn_id=uuid5(stable,'turn').hex`，`child_trace_id=uuid5(stable,'trace').hex`，`child_thread_id=uuid5(stable,'thread').hex`，`budget_lease_id=uuid5(stable,'budget').hex`。golden：parent `11111111-1111-1111-1111-111111111111` 的 canonical name=`{"parent_run_id":"11111111-1111-1111-1111-111111111111","policy_version":1}`，child=`2c2bba0d-c87b-5e70-ba41-1bf66ade02ff`，turn=`7a7859966d71596fa9ab7d17b75526bc`，trace=`12f427c8b6a85a5aa12ccdde03333ece`，thread=`b072c692df6c5afdb875d5b84f28250d`，budget=`5c544596151f5b6b8266a8055e774b1b`；expected 常量测试不得调用 production helper生成。

v6 policy 1 的 continuation action payload 必须为空；用户提出新自然语言范围/目标时创建新 root/spec revision，不进入 single-head continuation。canonical child start payload exact keys=`schema_version,parent_run_id,source_snapshot_hash`，`request_hash=sha256(canonical_json(start_payload))`。caller idempotency key只写独立 audit/control operation，不进入 child identity、logical slot、capability/start payload或request hash；different caller keys必须得到上述全部相同值。

service/repository 的唯一入口为 `create_or_get_continuation_v6(parent_run_id,caller_idempotency_key)->ContinuationCreateResultV1`，result exact keys=`schema_version,parent_run_id,child_run_id,child_operation_id,created,start_payload,start_request_hash,audit_operation_id`。该入口拥有 registered blob decoder，不允许 service先调用旧 `resolve_continuation_source()` 形成 TOCTOU。单个 `BEGIN IMMEDIATE` 中首先由 parent ID计算 identity并查 `workflow_research_continuation_heads`：head已存在时只验证 head→child run→lineage→start operation/request→session refs canonical identity，不重读或要求有效 parent pin，返回 canonical child；head不存在才做完整 parent/manifest/snapshot/pin/closure校验并创建。

caller audit 复用 `workflow_operations` 并与 create/get 同事务：`caller_key_hash=sha256(utf8(caller_idempotency_key))`，`audit_operation_id=sha256(utf8('deep-research-v6-continuation-audit-v1|'+parent_run_id+'|'+caller_idempotency_key))`，`operation_kind='research_continue_audit_v1'`，request exact JSON=`schema_version,parent_run_id,caller_idempotency_key_hash`，`request_hash=sha256(canonical_json(request))`，result exact JSON=`schema_version,parent_run_id,child_run_id,child_operation_id,created`。row 的 `run_id=child_run_id`，因此新建分支在 child run insert 后插入；existing分支验证 head后插入。same key已有 row时逐字段返回首次 stored result（包括 created），different key生成不同 audit row但同 child/start/head；任何冲突fail closed。

新建分支的 fault-injection/write-point 顺序固定为：`after_capability_insert,after_run_insert,after_audit_operation_insert,after_start_request_insert,after_session_refs_insert,after_lineage_insert,after_snapshot_pin_insert,after_start_operation_insert,after_inherited_staging_owners_insert,after_continuation_head_insert,before_commit`；existing分支至少覆盖 `after_audit_operation_insert,before_commit`。任一点异常都 rollback，数据库中不得残留 child/head/owner/audit；commit 后返回 stored audit result与 canonical child/persisted start payload。dispatcher notify不在事务内：service对首次 stored result的 `created=true|false` 都调用同一 `launch_existing_run(child_run_id,persisted_start_payload)`/notify；launcher created-run recovery scan从 persisted capability/start operation启动已 commit 的 `status=created` v6 child，因此 `after_commit_before_notify` 崩溃可恢复且不会创建 sibling。

server-side closure 校验顺序固定为：parent run/version/status -> terminal event -> manifest blob/hash/identity -> manifest snapshot pointer/hash -> snapshot blob digest/semantic hash -> §6.1 exact transitive `closure_refs`/classification -> every ref current parent terminal checkpoint owner/readable -> spec/fact-head/assessment/claim/policy cross-hashes -> live parent pin。客户端除 parent run ID、空 payload、caller audit idempotency key外的 manifest/snapshot/spec/head/lineage/blob ref一律拒绝。通过后 exact closure refs批量写 child staging owner，child genesis checkpoint原子提升；arbitrary same-parent blob因不在 closure set必须拒绝。

v6 每个 run 的 automatic cap固定复用现有 `DurableDeadlineV1`/schema v4，不新增字段：root row `owner_id=run_id,logical_scope='run:automatic',budget_ms=remaining_ms=900000,parent_deadline_id=null,created_at=last_observed_at=first_route_checkpoint_wall,wall_not_after=created_at+900,offline_policy=count,status=open`，`deadline_id=deadline_id_for(run_id,'run:automatic',registered_auto_cap_policy_hash,None)`。create-or-load逐字段核对 immutable值，retry/resume/lease takeover/restart只更新既有 revision/remaining/last_observed，不覆盖 created_at/wall_not_after。control `settle_deadline=min(accepted_at+30.0,root.wall_not_after)`；accepted_at>=wall guard时拒绝新控制，被截断时 terminal reason=`wall_guard`。到 wall guard停止派发、提交已有 canonical outcomes、补not-started并进入 honest partial/insufficient terminal。continuation child是新 run/新 root deadline，不继承 parent deadline或未结算 effect。

retention实现在 `backend/deskpet/workflows/retention.py`。初始 run roots仅为 nonterminal、terminal-retention窗口内、open control、required delivery非terminal、有效snapshot pin或 component外部共享的 checkpoint/delivery blob owner。对每个 continuation head做 parent/child无向 component closure：任一 endpoint为初始 root就保护整个 component、lineage/head/snapshot及§6.1 exact closure；missing/corrupt/owner mismatch时 fail closed保护 component。只有 component无任何初始 root/live external owner时才可在单个 `BEGIN IMMEDIATE` 中删除，并先收集全部 run/thread/checkpoint/effect IDs再次校验。

component 的唯一后序删除为：`workflow_checkpoint_effects -> workflow_pending_writes -> workflow_checkpoint_owners -> workflow_blob_refs(owner_kind in checkpoint|pending_task) -> workflow_checkpoints -> workflow_node_effects/effect target+resource reservations及其 blob refs -> descendant continuation heads -> inherited child staging refs与pins -> lineage rows按descendant-to-root -> component全部run的start/audit operations、workflow_start_requests、session refs -> snapshot rows -> reference-count为零的 blob refs/blobs/spec -> descendant child runs -> root run`；特别是root start request无run FK cascade，必须显式删除。`workflow_effects`随run FK cascade，但无FK rows必须在前述步骤清空。每个fault全rollback；删除后component所有checkpoint/owner/effect/pending/start/audit/request/session集合为零且无dangling ref。

## 7. Terminal commit contracts

`TerminalDeliveryManifestV1` exact keys：`schema_version,manifest_id,workflow_name,workflow_version,run_id,answer_status,spec_hash,assessment_hash,claim_policy_hash,quality_policy_hash,continuation_snapshot_ref,continuation_snapshot_hash,content_refs,intent_specs,cardinality,engine_terminal`。先以 `identity_seed=sha256(canonical_json({workflow_name,workflow_version,run_id,answer_status,spec_hash,assessment_hash}))` 派生 `manifest_id='tdm_'+identity_seed[:24]`，再以包含 manifest_id 的完整 canonical manifest bytes 派生 `manifest_hash`；registered blob digest 必须等于 manifest_hash，`manifest_ref=format_blob_ref(manifest_hash)`。manifest JSON 本身没有 manifest_hash/ref 字段，load 时按此顺序重算，禁止 self-hash 歧义。

- `answer_status=completed|partial|insufficient_evidence`；assessment 的内部 `insufficient` 唯一投影为 manifest `insufficient_evidence`，与既有 public/session 契约一致。这三种 honest answer outcome 都对应 native `engine_terminal.status='completed'`；在 manifest 形成前发生的 engine `failed|cancelled` 继续走现有 engine failure/cancel terminal path，不允许伪造 answer manifest。v6 terminal-commit capability 在 terminal frontier 缺 manifest、manifest 非 completed engine tuple 或 ref 不可读时 fail closed 到 engine failure，不能退回旧 graph-state terminal intents。
- `content_refs` exact keys=`final_assistant_ref,report_ref,safe_summary_ref,claim_batch_ref,quality_audit_ref`。
- `cardinality` exact keys=`final_assistant,workflow_final_status,report,artifact,run_terminal`，值均为非负整数。`completed|partial` 必须 `final_assistant=1,workflow_final_status=1,report=1,artifact=render_profile.artifact_required?1:0,run_terminal=1`；`insufficient_evidence` 必须 `final_assistant=1,workflow_final_status=1,report=0,artifact=0,run_terminal=1`。builder 必须校验 refs、logical content 与 physical intent 的对应基数，不允许仅凭数组长度推断。
- `engine_terminal` exact keys=`status,error_code,recovery_action`；answer manifest 中固定 `status='completed',error_code=null,recovery_action=null`，并必须逐字段等于 native 本次计算的 engine tuple。
- `intent_specs[]` exact keys=`intent_id,event_key,event_type,content_ref,delivery_specs`。
- `delivery_specs[]` exact keys=`delivery_spec_id,channel,target_role,required_durable,projection_kind,context_visibility`；ID 从 intent ID +其余字段派生。
- `claim_batch_ref` 与 `quality_audit_ref` 在三种 answer status 均必须是 `sha256:<64hex>`；manifest 只接受 status=valid 的 ClaimBatch 与 hard_gate_status=passed、answer_status 完全一致的 QualityAudit。所有非 null content ref 同格式并属于本 manifest closure。logical intent identity 固定为：final assistant `event_key='answer:final',event_type='workflow.final_assistant'`；workflow status `event_key='run:terminal',event_type='workflow.final'`；artifact（cardinality=1 时）`event_key='artifact:report',event_type='workflow.artifact_card'`。`intent_id=sha256(run_id|event_key|event_type|content_ref-or-empty)`；数组按 event_key 排序。
- content binding 固定：final-assistant intent 的 `content_ref=final_assistant_ref`；workflow-final-status intent 的 `content_ref=null`，其 bounded payload 只从 manifest/engine tuple 投影；artifact intent 的 `content_ref=report_ref`。`final_assistant_ref` 在所有 answer status 必须非空；insufficient_evidence 时 `safe_summary_ref` 必须非空且等于 `final_assistant_ref`，completed/partial 时 `safe_summary_ref=null`。`report_ref` 只在 completed/partial 非空。claim/quality refs 不直接形成 delivery intent，但属于 manifest closure。
- 物理映射固定：
  - final assistant：`session_message/original_session/required/final_assistant/conversation` + `websocket/current_epoch/optional/final_assistant/conversation`；
  - workflow final status：`session_message/original_session/required/workflow_final_status/exclude` + websocket optional；
  - artifact cardinality=1 时：`artifact/original_session/required/artifact_card/exclude` + websocket optional；
  - 不允许 materializer 添加 manifest 未声明的 channel。

`TerminalCommitRequestV1` exact keys=`schema_version,workflow_name,workflow_version,run_id,manifest_ref,manifest_hash,engine_status,engine_error_code,recovery_action`。

`TerminalCommitProjectionV1` exact keys=`schema_version,manifest_ref,manifest_hash,answer_status,blob_refs,intents`；`blob_refs` 是去重排序的 wire refs；projection intent exact keys=`intent_id,event_key,event_type,content_ref,delivery_specs,payload`。final-assistant/artifact identity payload exact keys=`schema_version,manifest_ref,intent_id,content_ref`，不含正文；workflow-final-status 使用下方 exact payload。

projection `blob_refs` 必须恰好等于：manifest ref + 非 null content refs + continuation snapshot ref + snapshot closure refs；不得仅返回顶层 manifest，也不得夹带任意 same-run staging blob。projector 在返回前逐 ref 验证 current-run owner/readability，native commit 才把完整 closure 一次提升为 terminal checkpoint owners。

async projector 调用同一 sync public registry，从 sanitized manifest state 生成 `V6TerminalPublicV1`，exact keys=`schema_version,answer_status,manifest_ref,action_matrix`；answer status 等于 manifest answer status，绝不承载 physical receipt aggregate。action exact keys=`action_id,enabled,reason_code`，只允许 version adapter 注册的 stable action IDs、最多 3 项、按 action_id 排序，reason code≤80 chars；canonical JSON≤4096 bytes，不得含正文/URL/error message。

workflow-final-status projection payload exact keys=`schema_version,kind,status,error,recovery_action,manifest_ref,answer_status,public,card`；固定 kind=`final`,status=`completed`,error/recovery=null，`public` 是上述 exact V6TerminalPublic，card exact keys=`run_id,status,error,recovery_action,manifest_ref,answer_status`。payload≤8192 bytes，不读取 graph terminal public，也不内联 final answer。它作为 final-status projection intent 的 payload 在同一 terminal outbox event持久化，是 public view 的唯一出口；不写回 checkpoint state。final-assistant/artifact handlers分别从 content ref/ArtifactProjection 取正文。

v6 finalize patch 在 `state.values` 只写 wire pointer `terminal_manifest_ref` 与 bare `terminal_manifest_hash`，并把 `parse_blob_ref(terminal_manifest_ref)` 纳入 native state `blob_refs`；不得把 inline manifest、terminal intents 或另一份 terminal public 写回 graph state。Native 顺序：先从 graph routing/exception 计算唯一 engine tuple；若 async projector 存在则从这两个 pointers 构造 request（缺失/格式错即 fail closed）→跳过旧 `_terminal_projection/_terminal_intents`→await projector→校验 manifest ref/hash 与 engine tuple→strict parse/merge blob refs→只接受 projection intents。`frontier_operation` 必须在 projection 完成后计算，并把 canonical projection hash纳入 operation identity，保证幂等键覆盖实际持久化 intents/payload；随后一次 `commit_frontier`。v1～v5 才走旧同步路径。

## 8. Canonical content resolver 与 delivery aggregate

workflow schema v4 对现有 delivery 表执行以下 exact migration（旧 row 的新增字段保持 NULL/0，不进入 v6 aggregate）：

```sql
ALTER TABLE workflow_deliveries ADD COLUMN intent_id TEXT;
ALTER TABLE workflow_deliveries ADD COLUMN manifest_ref TEXT;
ALTER TABLE workflow_deliveries ADD COLUMN required_durable INTEGER NOT NULL DEFAULT 0
  CHECK(required_durable IN (0,1));
ALTER TABLE workflow_deliveries ADD COLUMN claim_expires_at REAL
  CHECK(claim_expires_at IS NULL OR claim_expires_at >= 0);
CREATE UNIQUE INDEX uq_workflow_deliveries_manifest_spec
  ON workflow_deliveries(run_id,manifest_ref,intent_id,channel,target_id)
  WHERE manifest_ref IS NOT NULL;
CREATE INDEX idx_workflow_deliveries_manifest_required_status
  ON workflow_deliveries(run_id,manifest_ref,required_durable,status,next_attempt_at);
```

v6 materializer 把 `target_role` 解析为本次 durable `target_id` 后写 row；同一 `delivery_spec_id` 必须稳定得到同一 `(intent_id,channel,target_id)`，冲突 fail closed。begin/due/claim 的精确 CAS 服从下方 delivery policy；任一 terminal/retry transition 清空 claim。恢复只 reclaim `delivering AND claim_expires_at<=now`，不得像 legacy startup 路径那样无条件抢回所有 delivering；v1～v5 仍走原语义。

```python
resolve(
    run_id: str,
    manifest_ref: str,
    intent_id: str,
    content_ref: str,
    allowed_media_type: str,
) -> ResolvedCanonicalContent
```

resolver 重新读取 manifest，校验 manifest/current-run checkpoint owner、intent/content closure、content current-run checkpoint或effect owner、media type。Session text 仅允许 `text/plain; charset=utf-8` 或 `text/markdown; charset=utf-8`，strict UTF-8 decode 后 re-encode 必须等于 blob bytes；返回 `text,sha256,media_type`。任何 mismatch 不投影 placeholder。

v6 handler 返回 runtime-only `DeliveryAttemptResultV1` exact keys=`schema_version,disposition,reason_code,artifact_projection`；disposition=`delivered|discarded_fenced|retryable_failure`，reason code 是≤80 chars 低基数 enum，artifact projection 只在 artifact delivered 时非 null。非法 return/exception 归一为 `retryable_failure/handler_contract_error|handler_exception`，不得记录 `str(exc)`；legacy `None` 只适用于显式列出的 v1～v5 handlers。

Artifact 不在 T9 提前写文件；required `artifact` delivery handler 用 resolver 读取 canonical `report_ref`，再确定性生成 `ArtifactProjectionV1` exact keys=`schema_version,projection_id,run_id,manifest_ref,intent_id,report_ref,artifact_kind,display_name,relative_path,sha256,size_bytes`。固定 `artifact_kind='markdown_report'`、`display_name='deep-research-'+run_id[:8]+'.md'`、`relative_path='deepresearch/'+run_id+'/'+report_digest+'.md'`；projection ID 从其余字段派生。handler 在 artifact root 内 atomic temp+replace，既有文件必须 hash/size 相同，随后把 exact projection 转为现有 ProductDelivery artifact payload；只有文件校验与 ProductDelivery/SessionDB ArtifactCard 都成功才返回 delivered。crash/replay 重建同 path/bytes/identity，不另存 mutable artifact DTO；路径不得进入 manifest hash或业务 owner。

Delivery policy 固定 `deep-research-v6-delivery-v1`：max attempts=5，确定性 retry backoff=`1,2,4,8` 秒（cap 30，无 jitter）。initial row=`pending,attempts=0,next_attempt_at=NULL`；begin 只允许 `(pending AND attempts=0) OR (failed AND attempts<5 AND next_attempt_at IS NOT NULL AND next_attempt_at<=now)`，同一 CAS 写 `delivering,attempts=attempts+1,claim_expires_at=now+30`。第 1～4 次 retryable failure 写 `failed,next_attempt_at=now+backoff[attempts-1]`，第 5 次写 terminal `failed,next_attempt_at=NULL`；`attempts>=5 OR (failed AND next_attempt_at IS NULL)` 永不可自动 begin。expired delivering 保留已计数的当前 attempt，CAS 为同 attempt 的 retryable failure/terminal failure后才重新走 due query，不直接重投。typed fenced 写 `status='discarded',last_error='fenced:<code>'`，aggregate 映射为 fenced。

fenced row 是该 run/manifest/intent/target identity 的永久 terminal 结果，禁止因 session epoch 改变而 rebind、supersede 或生成第二 delivery row。若用户在新 epoch 再次需要结果，必须创建新的 root/continuation run，其新 manifest/delivery identity独立交付；旧 run aggregate 继续诚实显示 fenced。

aggregate exact keys=`schema_version,run_id,manifest_ref,status,required_total,pending,delivering,delivered,retrying,fenced,failed,updated_at`。row status `pending` 在 public aggregate 映射为 `status='queued'`；其余 public status=`delivering|delivered|retrying|fenced|failed`，前端只显示这些术语。

Required rows 归约：全部 delivered→delivered；否则 failed>fenced>retrying>delivering>queued。`retrying` 是 failed 且 next_attempt_at 非空、attempts<5；terminal failed 是 attempts≥5/next_attempt_at null。无 required delivery 对 v6 manifest 是 invalid，不返回 delivered。Outbox 用注入的 manifest resolver核对 specs，不让前端推断。

## 9. Visibility reader allowlist

state DB v19 冻结 enum：`projection_kind IN ('legacy_message','user_message','assistant_message','tool_message','system_message','final_assistant','workflow_progress','workflow_accepted','workflow_decision','workflow_final_status','artifact_card')`，`context_visibility IN ('conversation','exclude')`。重建表时旧 row 按 role 映射为 user/assistant/tool/system message（未知 role→legacy_message），全部保持 conversation；为兼容遗留 direct SQL，列默认仅允许 `projection_kind='legacy_message',context_visibility='conversation'`。所有新 SessionDB API caller 必须显式传 projection kind；同一 workflow event ID 的分类冲突 fail closed。

v19 exact column/index declarations（migration 由 transactional helper 持有；随后单独重建 FTS triggers）：

```sql
ALTER TABLE messages ADD COLUMN projection_kind TEXT NOT NULL DEFAULT 'legacy_message'
  CHECK(projection_kind IN ('legacy_message','user_message','assistant_message','tool_message','system_message','final_assistant','workflow_progress','workflow_accepted','workflow_decision','workflow_final_status','artifact_card'));
ALTER TABLE messages ADD COLUMN context_visibility TEXT NOT NULL DEFAULT 'conversation'
  CHECK(context_visibility IN ('conversation','exclude'));
UPDATE messages SET projection_kind=CASE role
  WHEN 'user' THEN 'user_message'
  WHEN 'assistant' THEN 'assistant_message'
  WHEN 'tool' THEN 'tool_message'
  WHEN 'system' THEN 'system_message'
  ELSE 'legacy_message' END,
  context_visibility='conversation';
CREATE INDEX idx_messages_session_visibility_time
  ON messages(session_id,context_visibility,created_at DESC);
CREATE INDEX idx_messages_visibility_role_time
  ON messages(context_visibility,role,created_at DESC);
CREATE INDEX idx_messages_visibility_salience
  ON messages(context_visibility,salience DESC,id DESC);
```

新写入 mapping：

| 来源/event type | role | projection kind | visibility | skip_embed |
|---|---|---|---|---|
| 普通用户消息 | user | user_message | conversation | false |
| 普通 assistant | assistant | assistant_message | conversation | false |
| tool result | tool | tool_message | conversation | false |
| system/summary | system | system_message | conversation | false |
| `workflow.final_assistant` | assistant | final_assistant | conversation | false |
| `workflow.progress` | assistant | workflow_progress | exclude | true |
| `workflow.accepted` | assistant | workflow_accepted | exclude | true |
| `workflow.decision` | assistant | workflow_decision | exclude | true |
| `workflow.final` | assistant | workflow_final_status | exclude | true |
| `workflow.artifact_card` | assistant | artifact_card | exclude | true |

任何其他 `workflow.*` 投影到 SessionDB 必须 fail closed，先升级 enum/migration；不能默认 conversation。v19 FTS trigger 只同步 conversation，include↔exclude update 必须 delete/insert 正确。

所有模型输入/派生记忆 reader 必须 SQL 或最终 metadata 过滤 `context_visibility='conversation'`，包括 Session recent/tool boundary、MemoryManager、Retriever 四路、VectorWorker、Context OS、summarizer、ReflectionWorker、`agent/snapshot.py` token pressure、`scripts/facts_backfill.py`、`scripts/chunk_backfill.py`。history/UI/admin API（`get_messages/list_turns/session_messages`）是显式例外。`memory/eval/qaset.py` 默认过滤，只有测试-only `include_excluded=True` 例外。新增 repo-wide SQL audit test扫描 `FROM messages` call sites，要求每个命中属于 filtered reader 或固定 allowlist，新增未分类 reader测试失败。

## 10. Dev v6 ingress 与 release 复验

未发布 v6 的真实 UI 选择只通过环境契约：`DESKPET_DEV_DEEPRESEARCH_VERSION=v6` 仅在 `DESKPET_DEV_MODE=1`、`DESKPET_USER_DATA_DIR` 显式设置且 resolved path 不等于默认用户目录时生效；否则 fail closed。客户端 payload 不允许指定 version。`_start_deepresearch_graph` 读取该 override 后仍经 version registry/capability gate，日志记录低基数 selected version/reason。Q1/T13 在隔离源码 Tauri 使用此入口。

全部开发验收后提交 `backend/tests/fixtures/deep_research_v6_release_identity.json`，固定 manifest/implementation/state/prompt/policy hashes、三个v6 effect profiles与prepared-call golden。factory pair exact=`deep_research_version:v6,deep_research_default_revision:6`；无env resolver接受configured v6，configured为空/未知值回退factory v6并记录低基数reason=`configured_invalid_factory_default`，只有显式合法v5 pin选择v5；factory migrator只把exact inherited `v5/revision5`提升到`v6/revision6`，explicit pin不变。fresh/inherited-v5/explicit-pin/`''|v7|garbage`均有固定测试。release复验不设置dev override（guarded代码保留），用新隔离user-data重启并重跑identity/recovery、SC-STATS-2真UI、reconnect/history与regression；最终默认bundle才允许完成，fixture变化必须升v7。
