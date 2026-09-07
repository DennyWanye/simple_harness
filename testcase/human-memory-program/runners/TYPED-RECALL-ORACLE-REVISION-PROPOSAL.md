<!-- 2026-09-05 factual correction: SDK hashes E; state NUL unchanged; independently frozen vectors before candidate execution. -->
# TC-HM-13 oracle 修订提案（A2 已批准，有界实施）

日期：2026-09-05。状态：**§3–4 及两项 P2 已获用户明确批准；批准先于修改固化于 `38356e7f`。**
批准记录见 `TYPED-RECALL-A2-APPROVAL-2026-09-05.md`。下文保留受审原文；当前 fixture rev4/layers rev2 实施范围以 `approved_oracle` 为准。
本轮纠正：SDK 的既有 hash 为 E=H(C({domain,payload}))；先前NUL为提案事实性错误，用户明确授权自主纠正，独立向量先于候选执行固定。§4测试state NUL保持。Harness 候选依用户后续明确指令升级0.7.2/source2b842846/wheel53bded3f；Memory0.6.3不变。
当前首批执行及 blocker 见 [执行记录](TYPED-RECALL-A2-FIRST-BATCH.md)。下列详细方案/早期桥记录保留受审历史，实际 fixture 已按有界批准升 revision；原始 program acceptance 与 SDK 生产源码未由本分支修改。独立 S5b 工作继续。
本次 runner 是执行/观察桥，**TC-HM-13 验收 PASS = 0**。桥回归与真实 API probe 分开计数。

## 1. 批准对象与事实源

建议批准：依原始 S3 Task5 已规定的公开契约，修正 fixture 的 canonical 字段、domain 与真实状态见证；保留所有 cells、负例及数值阈值。不要将现有生产实现降级成 fixture 的简化模型。

权威关系：

1. Memory 仓 `plans/2026-08-29-human-memory-digital-twin/plan.md` §数据 authority 表：Host↔Memory DTO、canonical JSON/hash、reason/error code 的 authority 是 Harness SDK；canonical cognitive state 由 Memory 持有。该 plan 顶部指定 `acceptance.md` + `assurance-contract.json` 为验收事实源。
2. 同目录 `slices/S3-cognitive-systems-recall.md` Task5.1–5.7：source hash 与最小公开 payload hash 分离；完整 context/plan/authority/budget 的 replay binding；真实 durable 原子事务；公开 boundary 的 negative 与零候选访问；所有数值预算/排序规则。
3. Harness `runtime/memory_protocol.py`、`runtime/recall_protocol_v4.py` 定义具体公共 wire。Memory `core/recall.py`、`core/cognitive.py`、`core/audit.py` 和 `backends/sqlite_v5.py` 是待对照实现，**不能直接调用其 scorer/hash 函数产 gold**。
4. Memory `docs/human-memory-v1-schema.md` 的 public canonical manifest 说明明确：读取 manifest 自身会追加 access event；不能拿两次全库 hash 相等替代业务零增量。
5. Host `TC-HM-13-typed-recall-result.md` rev4 及两份 frozen fixture 是该需求的验收实例。与上面权威契约冲突的简化实例需要受审修订；不能静默改变 AC，也不能仅以“fixture 较旧”判断生产正确。

上述路径相对各自仓库；Memory 为 `simple-harness-memory-sdk`，Harness 为 `simple-harness-sdk`。

历史复核定位（`git show <commit> -- <path>` 可复查，历史 receipt 不作本机测试证明）：

| 提交 | 事实及影响 |
|---|---|
| Harness `64d409d`，2026-08-31 05:42 | 原始 Semantic DTO 已有 `memory_type/object_value_hash` 与数组 `qualifiers` |
| Memory `e5ce4b8`，2026-08-31 11:52 | cognitive content hash 使用完整公开 payload 的 canonical JSON |
| Host `23315733` → `e519e5fa`，2026-08-31 19:03 → 21:00 | fixture 写入简化 payload 与 `{}` qualifiers；问题早于后续 v5 升级 |
| Host `28691b89` | state hash 只 hash cell 标识和 old/new 标签，未绑定 durable state |
| Host `2ab09cfa` | TC13/layers 的 Memory pin 升至 `d069e0e9`，主 fixture 仍留 `9c79fa7e`；须分别记录，不能择一冒充一致 |
| Memory `dfd12ce`；Harness `5d78abc` | 已批准 semantic-relations increment §4.1 的 v5 `semantic_kind` 与实现；是后续合法协议增量 |
| Memory `bb711bf` | `MIGRATION-EVIDENCE-BOUNDARY.md` 允许同 source commit 本机重建及重新 pin，保存 `pre_migration`；不等于允许改变 oracle |

## 2. 保持不变的验收边界

两份 fixture 当前字节身份：

- `typed-recall-v3.json`: `373080e1488906badf5b66e4d13720224e6528697345fbaeae51b4206d621c12`
- `typed-recall-execution-layers-v1.json`: `2f18d942be3ddd4d4c95c4eadd888b15f49c783a27bd0f0c086462ce32b4d1ca`

修订前后必须有相同的 sorted `lane/cell_id` 集合：

| 集合 | 数量 | canonical JSON SHA-256 |
|---|---:|---|
| 全集 | 401 | `49e4433ebdc7d4551a3425e6006b8143dffd89ca2545331072a9196f4ba66dea` |
| clean-wheel public | 391 | `dfe4c42b0cbce37b8f8173d1d0e313bb36a94767835f632878ce458b904c119a` |
| exact-source/fault | 10 | `32d42ed588b5ca27b22a726217e6121a15c41ac45e6246c4f5f727026c687769` |

负例语义、完整/残缺冲突组、跨身份隔离、过期/零候选读取、strict version rejection、重放冲突、原子 old/new 与秘密 canary=0 全保留。
原阈值不变：max_items 1..32、bytes 1..65536、tokens 1..8192、deadline_ms 1..2000；cap `min(128,max(32,8*max_items))`、每类 128、union 640；RRF k=60、权重 .40/.30/.15/.10/.05；12 位 score；greedy/整项跳过；token `max(1,codepoints,ceil(bytes/3))`。
冻结 exact-limit/limit+1 的**现有数值**也不在本提案中授权改动。若正确公开 payload 字节数无法满足某个现有向量，必须将该 cell 列为未解决差异，提出单独受审向量修订；不得把 limit 向上调至产品恰好能过。
200+ query 人工冻结/真实模型质量门不由本次桥或 oracle 自检代替，required-type≥90% 等原质量门不变。

## 3. canonical 定义与字段级修订

统一记号：`C(x)` = UTF-8 JSON，键按字符串排序，separators `(',', ':')`，ensure_ascii=False，禁止 NaN/Infinity；数组保持契约顺序、null 保留，不自动丢字段。`H(x)=sha256(C(x))`。`E(domain,x)=H({domain:domain,payload:x})`，即 canonical JSON domain envelope。先前 NUL 写法为本提案事实性错误，现依用户已批准对齐既有公共契约的目的纠正；§4验证侧state仍用NUL。有数值时间的 wire 使用公开序列化后的秒值，不把 ISO 字符串混进其 hash。
独立 oracle 必须在运行候选之前从受审输入/契约计算；禁止从实际返回值反填 gold。实际动态 ID/time 可以用命名变量绑定到真实公共返回值，再验证字段结构/关联/独立重算 hash，但不得因此声称匹配一个预冻结 literal gold。

### 3.1 source content 与 provider projection

| 对象 | 旧 fixture | 拟采用契约 |
|---|---|---|
| Semantic claim source | `subject_entity,predicate,object_value,qualifiers={}` | v5 完整字段：`memory_type='semantic',semantic_kind='claim',subject_entity,predicate,object_value,object_value_hash,qualifiers=[]`；`object_value_hash=H(object_value)`，`source_content_hash=H(完整source)`，无 domain |
| Semantic provider payload | 与简化 source 混用 | 仅 `subject_entity,predicate,object_value,qualifiers`；值和数组类型遵循公开 DTO；`public_payload_hash=H(payload)`；不得把 source discriminator/value hash 塞进预算投影 |
| 其他 provider payload | 已冻结最小投影 | Episode `title,participants,goals,actions,results,impacts,occurred_interval`；Procedure `name,applicability,steps,effective_risk`；Prospective `action,trigger`；Short-Horizon `content,occurred_at`。具体 wire 键以受审公共投影契约逐项核对；本提案不授权增加字段或改字节预算 |
| source discriminator | cognitive/short 混合负例 | cognitive 必须 exact memory_type/source_ref/revision；short 仍 `memory_type=null,source_revision=null`，不合成认知身份 |

同一 `preferred_python=3.11` 示例：旧简化 hash `258031c5…`；初始公共 v4 wire `154a6682…`；合法 v5 claim wire `123d54bb…`。三者是不同规范化对象；仅升级 wheel pin 不会解决。
`source_content_hash` 修改会向 member/group/decision/result bindings 传递；必须按各自独立公式重新计算，不能搜索替换 hash 字符串。

### 3.2 replay request

旧 domain `hm-recall-request-v4`；旧平面对象字段：`protocol_version,principal_id,run_id,context_hash,context_revision,plan_id,plan_hash,disclosure_hash,recipient,purpose,budget`。
拟采用已存在的 domain `simple-harness-memory/typed-recall-request/v1`，完整 envelope 字段：

```
harness_protocol='recall-v4'
memory_protocol='typed-recall-v1'
principal_id
context = 完整 RecallContext wire
plan = 完整 RecallPlan wire
```

`RecallContext` 完整字段：`schema_version,run_id,subject,turn_id,context_revision,expires_at,query,active_task_scope_id,available_memory_types,short_horizon_allowed,allowed_selector_domains,allowed_retrieval_modes,allowed_task_scope_ids,allowed_entity_constraints,earliest_occurred_at,latest_occurred_at,event_constraint_refs,environment_constraint_refs,task_phase_authority_refs,procedure_applicability_fingerprints,disclosure_context,evidence_refs,budget`。
`context_hash=E('simple-harness/recall-context/v2',context)`。

`RecallPlan` 完整字段：`schema_version,plan_id,run_id,subject,context_hash,context_revision,query,requested_memory_types,include_short_horizon,selector_domains,retrieval_modes,task_scope_ids,entity_constraints,earliest_occurred_at,latest_occurred_at,event_constraint_refs,environment_constraint_refs,task_phase_authority_refs,disclosure_context,evidence_refs,budget,idempotency_key,reason_codes`。
`plan_hash=E('simple-harness/recall-plan/v2',plan)`；派生 plan_hash 不回填进自身 wire。

嵌套字段完整定义：

- disclosure：`schema_version,run_id,subject,recipient,recipient_id,intended_audience,purpose,source,trust,generation,authority_ref,reason_codes`。
- evidence_refs 每项：`evidence_id,content_hash,ordinal`，保持契约顺序。
- budget：`max_items,max_bytes,max_tokens,deadline_ms`。

保留原 14 个 one-field mutation cell ID：principal 修改 envelope；run/context revision/context hash/plan ID/disclosure/recipient/purpose/budget 修改对应公开字段并明确所需配套绑定；“plan_hash”通过变更可控 plan 输入触发派生 hash，不能向 strict wire 注入不存在的字段。protocol_version mutation 在 Host strict parser 层拒绝，记录 Memory 未调用；独立 hash 单元向量与真实 API 拒绝分开。每个 cell 应保留其原始攻击点和拒绝语义；不能用“计算两个 hash 不同”替代真实重放/零 candidate 证据。无法保持原语义者仍 BLOCKED，交 A2 逐项确认。

#### 独立审查 P2：14 个变异的执行前映射约束

实现执行器及运行候选之前，必须固定并审阅完整的 14 行映射表，每行绑定原 cell ID，并明确：**原攻击点 → 新公开字段/精确路径及变异值 → 必须同步或必须故意保持不变的配套绑定 → 预期拒绝层与 exact reason**。同时列明到达目标拒绝层的前置条件、是否允许调用 Memory、预期 candidate query count。不得在看过候选返回后调整映射或 reason。

- 配套绑定必须区分“为到达原攻击点而同步重算”的合法派生字段与“原攻击本就要求不一致而保留”的字段；不能因无关 parser/binding 错误提前拒绝，就声称原攻击得到验证。
- 派生 `plan_hash` 等字段必须说明具体可控输入与传播链，证明仍攻击原先的 replay/binding 语义；仅保留 cell ID 或获得不同 hash 不构成等价映射。
- 每行必须实际调用规定的公开入口，并记录真实拒绝层、原始 reason、调用边界与零候选访问见证。hash 计算/向量自检只能作辅助证据，不能替代真实拒绝；映射缺失、目标层不可达或无法保留原攻击语义时，该 cell 保持 BLOCKED，不以其他拒绝结果替代。

### 3.3 current-use receipt 与事件日志

旧 receipt oracle hash 的对象是事件描述：`event,before_epoch,after_epoch,before_policy_hash,after_policy_hash,evaluated_at,authorized_at,authority_expires_at,context_expires_at,use_at,decision_hash,result_hash,item_hashes,snapshot_hash,run_id,turn_id,continuation_id,provider_attempt,outcome`；它不是公开 use receipt。
拟保留这些字段作为**场景/事件输入和事件观察**，与公开 receipt 分开命名，不再冒充其 hash。

公开 `RecallContextUseReceiptV1` hash：`E('simple-harness/recall-context-use-receipt/v1',receipt_wire)`。
wire 完整字段：`schema_version,receipt_id,request_hash,subject,run_id,turn_id,provider_attempt_id,decision_id,decision_hash,result_id,result_hash,item_bindings,snapshot_manifest_hash,authority_epoch,policy_hash,authorized_at,expires_at`。
`item_bindings` 为有序 `{item_id,item_hash}` 数组；不含 receipt_hash 自身。receipt 的 expires_at 不能被“authority_expires_at”同名替换。
拒绝场景不能伪造 receipt。真实异常/拒绝 DTO、零 payload/candidate、epoch 与状态见证共同判定；reason 字符串只有受审的一对一对应可映射，未知 reason 直接 FAIL/BLOCKED。

相关公开 DTO domain 原样保留：`simple-harness/recall-selected-item/v4`、`simple-harness/recall-confirmation-member/v4`、`simple-harness/recall-confirmation-group/v4`、`simple-harness/recall-decision/v4`、`simple-harness/typed-recall-result-item/v1`、`simple-harness/typed-recall-confirmation-member/v1`、`simple-harness/typed-recall-confirmation-group/v1`、`simple-harness/typed-recall-result/v1`。递归取各 DTO 完整 `to_json` 契约，派生 hash 不参与自身 preimage；此处不授权重新设计这些 DTO。

### 3.4 conflict durable hashes

旧 evidence-set 仅 hash sorted evidence IDs；实际 durable conflict evidence hash 覆盖有序 evidence span 的完整 authority。二者不能合并为同一字段。拟分别保留 `evidence_id_manifest_hash`（用于原 Task5 跨 source dedupe）和 `conflict_evidence_set_hash`。
后者为 `H(按ordinal排序的span数组)`，每项字段完整清单：`ordinal,span_id,evidence_id,envelope_hash,sanitized_hash,admission_receipt_id,admission_receipt_hash,evidence_item_ordinal,evidence_item_id,evidence_item_json_pointer,byte_start,byte_end,exact_quote,quote_hash,source_hash,normalization_version,actor_role,provenance,source_kind,support_kind,observation_schema_id,observation_schema_version,observation_registered_schema_hash,observation_receipt_id,observation_receipt_hash,observation_authority_issuer_id,observation_json_pointer,observation_value_hash`；不含 memory_id/revision，null 保留。

- member preimage：`group_id,ordinal,role,principal_id,memory_id,revision,content_hash,evidence_set_hash`；hash=H，无 domain。
- group preimage：`group_id,principal_id,memory_id,incumbent_revision,challenger_revision,creation_plan_id,creation_plan_hash,operation_id,created_at,member_hashes`；member_hashes 保持 incumbent/challenger 顺序；hash=H。
- resolution preimage：`group_id,principal_id,memory_id,resolution_revision,resolution_kind,selected_member_ordinal,plan_id,plan_hash,operation_id,created_at`；hash=H。kind 为 `selected_incumbent/selected_challenger/replacement/superseded/forgotten` 的生产枚举，须与原 frozen scenario 含义逐项核对。

这些 durable 字段目前依靠 source 阅读确定，不授权公共 runner 读私有表。公开 receipt/manifest 无法揭示所需独立校验输入时，该 public cell 必须 BLOCKED，报告 API 观测缺口；不得挪进 source 10 以凑 PASS。

## 4. before/after 状态 hash 的可实施定义

旧 `D('simple-harness-memory/typed-recall-state/v1',{fixture_id,fixture_revision,lane,cell_id,terminal:'old'|'new'})` 只是场景标签。保留作历史 oracle tag，不再用作 before_hash/after_hash。

拟定义两种分开的实际见证：

1. **完整实际 manifest**：调用 public `export_canonical_state_manifest`，保存 `manifest.to_json()` 与实际 payload_hash/access_event_hash；payload_hash=H(完整manifest)，无 domain。manifest 字段为 `schema_version,storage_schema_version,schema_checksum,initialization_receipt_hash,principal_ref_hash,table_roots,total_row_count`；每个 root 为 `category,table_name,row_count,root_hash,first_leaf_hash,last_leaf_hash`，按 `(category,table_name)` 排序。保留全部 roots，不隐去发生变化的表。
2. **受保护状态 projection**（新验证侧定义，需本次 A2 批准）：
   `D('tc-hm-13/protected-state/v1',{schema_version:1,storage_schema_version,schema_checksum,principal_ref_hash,protected_tables,table_roots})`。
   protected_tables 为 case 开跑前由受审 lane/scenario 声明的完整表名列表，按名称排序；table_roots 只含这些表的上面六字段，保持规范顺序；缺表/重复表即拒绝。before/after 在同一隔离数据库计算，不拿运行后哈希生成预期。

拟定表集与判定：

- mutation/conflict 的零状态增量：`cognitive_apply_heads,cognitive_memory_heads,cognitive_memory_revisions,cognitive_evidence_spans,cognitive_revision_task_scope_origins,cognitive_relations,cognitive_conflict_groups,cognitive_conflict_members,cognitive_conflict_resolutions,cognitive_classification_decisions,cognitive_classification_evidence_authorities,recall_authority_heads,recall_authority_events`。对同一拒绝操作应完全相等。合法 invocation/audit append 独立展示，不能掩盖此集合的变化。
- recall final transaction：`typed_recall_decisions,typed_recall_decision_items,typed_recall_results,typed_recall_result_items,typed_recall_confirmation_groups,typed_recall_confirmation_members,typed_recall_terminals`。保留 start request/attempt 的允许增量（`typed_recall_requests,typed_recall_attempts`）为独立见证，不将其混进“没有半 terminal”的判定。
- current-use：在前述 recall final 集合上增加 `recall_authority_heads,recall_authority_events,recall_context_use_receipts`；按 case 比较预先声明的 epoch/receipt 变化，不能通用要求所有拒绝前后全库相等。
- source/fault 10：在 exact clean source commit 的隔离数据库用候选实现之外的观察器读取上述 final 集合，并独立按 PK 排序、逐列 canonical。no-fault 对照先通过下述独立业务断言，才可提供完整 after 终态参照；每个 fault 的允许状态按其事务阶段预先固定，不能统一任选 old/new。对照运行也是真实执行，不是从 `expected` 合成 rows。

#### 独立审查 P2：no-fault 对照与事务阶段约束

no-fault 对照必须先满足执行前固定的独立业务断言：请求的业务 outcome/reason 正确；selected/confirmation 内容、顺序和精确 source/evidence bindings 符合场景；decision/result/header/items/terminal 关联完整且唯一；预算与资格约束满足原 plan。断言从受审场景输入与业务契约定义，不调用候选 scorer、不从对照返回值生成预期。仅“运行未抛错”“hash 自洽”或“与 fault 运行一致”不够；对照断言失败应报告实际失败，并禁止该对照成为事务参照。

每个 fault cell 在执行前固定注入点、commit 是否已完成、观察时点与允许增量：

- **commit 前故障**：即时 durable 观察允许已写入的 start request/attempt；final transaction 集合必须保持提交前状态，不能残留任何 decision/result header、item 或 terminal fence 半组合。不能以“等于 no-fault 的 new 状态”接受一个本应回滚的 pre-commit 故障。
- **commit 后 ACK 丢失**：必须已持久化完整 committed terminal；重启或以同 key、同 request 重试后必须 exact replay，同一 decision/result 的 bytes/hash 不变、candidate query=0、不重复写入。返回 old、重新检索重算结果或生成新的 decision/result，即使业务内容相同也不能通过。
- **restart/recovery**：即时故障快照与恢复后的快照分别记录；未提交 attempt 的重试/过期终结依原 Task5.5 时限规则预先声明。恢复成功不能抹去先前出现的半状态，也不能把 post-commit ACK-loss 降格为可重新执行的 dangling attempt。

public manifest 内部 leaf 现定义 `H({schema_version:1,table:table_name,row:canonical_row})`，root 为 `H({schema_version:1,table:table_name,leaves:有序leafhash数组})`；public 消费者验证返回 manifest commitment 和受审不变量，不能宣称仅凭 root 能独立重建隐藏 rows。source 层才可重算 row/leaf。
manifest 的读取 access event 被下一次 snapshot 纳入；它及其他被明确允许的只读审计增量不加入 protected projection，但完整 manifest 仍保存。新增表、覆盖缺口或未知写入不可自动排除。两次 full manifest 不相等也不能直接判产品失败。

**仍需逐 cell 落实的具体阻点**：public builder 无冻结时钟参数、一些动态 ID/time 无公开控制、部分 private fault seam/public零候选访问观测不可从 receipt 单独证明。批准此方案不等于宣称这些 API 已足够；需要补公开可观测性时应在单独生产任务中按原 plan 修生产。不得 monkeypatch SDK 私有入口、直接 SQL 或将 public cells 迁入 source 层。当前 probe 使用真实 now 并记录 frozen/actual 差异，仅 OBSERVED。

## 5. oracle 修订、迁移 repin、候选升级必须分开

| 变化 | 权限/流程 | 允许改什么 | 不允许推导什么 |
|---|---|---|---|
| 同 source commit 本机重建 | 已有 `MIGRATION-EVIDENCE-BOUNDARY.md` 决定 | 构建环境/新wheel hash；旧身份留 pre_migration | 不改内容规范、domain、cells、阈值；不把旧 receipt 视为本机验证 |
| 当前候选版本/source 升级 | 对应候选升级批准与 exact-wheel 验证 | candidate identity 及准确的新验证记录 | 不自动改 oracle；源码 SHA 不能仅由 wheel version 推断 |
| 本提案 canonical/state 修订 | **本文件 A2 最终批准**，独立挑战后实现 | §3–4 精确列出的字段/公式/见证语义；新增 revision 与旧 hash 谱系 | 不删除 cells、降低阈值、把 self-check 计入产品 PASS |

本轮经独立复审采用的新候选：Memory **0.6.3**，source `2f3d73814fe6a884e0458d87567b918c5863033e`，wheel SHA-256 `6b20ae5bff6c3ecfe1108ccaff9bb41c4dc6a3b98bb754dac2c418673ab77c78`；Host main `26b50ee81e7ccd290e8da6c88d0053d7d8f8ecb1`。该候选由主执行者为修复主动构建；用户授权继续原 plan，并未亲自指定该版本。
该身份列为本提案目标 pin；此处不修改冻结 pin、不冒充已用 0.6.3 跑过 TC13。Harness 继续以候选 manifest 的 exact version/source/wheel 三元组审阅。

两种契约选择及影响供 A2 判定：

- **建议：按原 plan 的完整公共 DTO 与真实 durable state 修 oracle。** 维持现有协议与后续合法 v5/v7 增量；工作是受审的字段/hash 向量与真实状态见证，不包含放宽语义。
- **若坚持旧简化 canonical/标签 state 就是产品契约：需另立生产契约变更。** 这会改变 cognitive content/replay identity、历史 bindings 和 receipt wire；old/new 标签仍不能证明 durable 原子性，必须另外定义真实状态见证。不能作为“只改 pin”或 runner 兼容补丁偷偷实施。

A2 可以批准上述建议及候选升级的分别处理，而不批准任何未列出的 outcome/阈值变更。若任一 cell 还出现新契约差异，保留 BLOCKED 并单列增量供审阅。

## 6. 批准后的最小实施顺序与当前桥交付

1. 先为 §3–4 写独立 oracle 回归与 old/new 字段清单；固定变异、负例、数值阈值和 401 IDs；禁止读取产品 scorer/按实际结果回填 gold。
2. 单独修订 fixture revision/字段/hash，保留历史 hash；按 §5 分别记录候选 pin 来源。现有 exact-limit 数值无法兼容的 cell 继续 BLOCKED，不在本提案中放宽。
3. 按 cells 注册真实 public/source 执行器，未知实现显式 BLOCKED；分层验证 exact身份、run/request/hash/time、缺失/重复/串层，汇总禁止借用另一层证据。
4. 只有真实调用结果满足已批准 oracle 的 cell 才可走后续 PASS 评估；本次 bridge schema 没有 PASS 状态。自检、桥单测、历史 receipt 均不计入 TC13 PASS。

当前具体公共 probe：package-root `build_human_memory_v6` → `ingest_committed_evidence` → `apply_memory_mutation_plan` → `get_memory_mutation_receipt_view` → `execute_typed_recall` → exact replay → result-bound `page_typed_recall_result` → `close`。本机 Memory 0.6.2/Harness 0.7.1 已返回真实非空结果与重放/分页；它仅关联 unbounded-validity 路径，明确记录 `{}`→`[]` 和 frozen now→实际 now 的差异，**0 验收 PASS**。其他 390 public 和 10 source cells 尚未执行，不归咎于 oracle 自动消失。
框架只负责 subprocess 隔离、wheel bytes/installed origin/version 验证、请求/返回身份绑定和两层观察汇总。借用现有 venv 的模式明确不称为 clean-venv 证明。source commit 是有来源的调用方声明；wheel 字节核验不能证明构建源码历史，source 层另查 exact clean checkout。

最小桥回归（不跑401自检、不跑SDK全量）：

```sh
/Users/denny/projects/simple_harness/backend/.venv/bin/python -B -m pytest -q -p no:cacheprovider testcase/human-memory-program/tests/test_typed_recall_bridge.py
```

真实观察命令模板（从本分支 worktree 根运行，输出目录每次必须不存在）：

```sh
/Users/denny/projects/simple_harness/backend/.venv/bin/python -B testcase/human-memory-program/runners/run_typed_recall_public_consumer.py \
  --consumer-python /Users/denny/projects/simple_harness/backend/.venv/bin/python \
  --harness-wheel /ABS/HARNESS.whl --harness-wheel-sha256 HARNESS_SHA256 --harness-source-commit HARNESS_SOURCE40 \
  --memory-wheel /ABS/MEMORY.whl --memory-wheel-sha256 MEMORY_SHA256 --memory-source-commit MEMORY_SOURCE40 \
  --observe-candidate --artifact-dir .local-test-evidence/2026-09-05/UNIQUE_RUN
```

预期 exit 3、`NOT_RUN/BLOCKED`、passed_cells=[]；真实产品/传输异常 exit 1/FAIL，不能被 oracle BLOCKED 掩盖。`--observe-candidate` 只授权观察本次明确传入的候选，绝不修改冻结 pin。
source 接口为验证侧 `.py` 的 `async run(request, workspace)`，必须另给 exact clean `--source-checkout`；本次没有实现 fault10，不提供伪造默认 source adapter。所有原始 request/response/runtime/log/SQLite 留 ignored `.local-test-evidence`，仅方案、代码和文字验证结论入 Git。

### 框架交付补充（方案批准状态不变）

本机桥回归 **40 passed**（transport 合成样本只测桥，不能作401产品证据）。独立初审的字节码读取、Windows 路径分隔符、环境失败丢失清单三个问题已加回归修复；末次汇总还重验已收集文件，防后执行层改写前层证据。
2026-09-05 用本轮经独立复审采用的 Memory **0.6.3** + Harness **0.7.1** 实际执行同一 public probe：candidate query=1，exact replay query=0，分页非空；installed package 逐字节核验 Harness 151 文件、Memory 61 文件。结果仍 exit3 / NOT_RUN/BLOCKED，1 OBSERVED、390 public BLOCKED、10 source 未配置，**0 验收 PASS**。
本机原始观察索引：`.local-test-evidence/2026-09-05/typed-recall-bridge-063-smoke/bridge-summary.json`，引用同目录 request/observations/runtime 的 SHA-256。该观察早于代码提交，只代表这次实际执行；提交后复跑需使用新目录。

可复跑的 exact 0.6.3 命令（从 worktree 根执行；换一个未存在的目录名）：

```sh
/Users/denny/projects/simple_harness/backend/.venv/bin/python -B testcase/human-memory-program/runners/run_typed_recall_public_consumer.py \
  --consumer-python /Users/denny/projects/simple_harness/backend/.venv/bin/python \
  --harness-wheel /Users/denny/projects/simple_harness/backend/vendor/simple_harness_sdk-0.7.1-py3-none-any.whl \
  --harness-wheel-sha256 4d5d2b7ba5c2f8ef4956af77769d75e1ac7889a037acbdcf853d0b9a5b3a3218 \
  --harness-source-commit f5fe0dc7e8c5b521444e01c40cab176f3666c627 \
  --memory-wheel /Users/denny/projects/simple_harness/backend/vendor/simple_harness_memory_sdk-0.6.3-py3-none-any.whl \
  --memory-wheel-sha256 6b20ae5bff6c3ecfe1108ccaff9bb41c4dc6a3b98bb754dac2c418673ab77c78 \
  --memory-source-commit 2f3d73814fe6a884e0458d87567b918c5863033e \
  --observe-candidate --artifact-dir .local-test-evidence/2026-09-05/typed-recall-063-NEW_RUN
```

仅 runner/helpers/adapter、桥回归和此提案属于本次交付；不宣称原 program/S3 Task5 已完成，不修改 ARCHITECTURE 的产品完成度。原冻结自检和 SDK 全量/provider/UI 本轮均未重跑。

框架独立复审结论：只读复查确认上述三项修复，所审范围未发现新 P1；未代跑产品验收。review task `01a06efe-4c98-75c1-bdee-89ab69345f89`，本机索引 `.local-test-evidence/2026-09-05/typed-recall-bridge-review-followup.log`。
0.6.3 probe summary SHA-256：`6047fb279035247872d8411584c686471b8109cfe71f8a8c48a959e4b69b8879`。
