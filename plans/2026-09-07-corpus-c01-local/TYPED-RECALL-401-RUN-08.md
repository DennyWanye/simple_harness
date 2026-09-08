# 401 矩阵 86+1 格定义冲突裁决与落地（run-14，M0.6.31 pin 不变）

> 2026-09-09。分支 `worktree-matrix-401-adjudicate`（Host main `b7b5bcd0`）。
> 对照基线：run-13（`.local-test-evidence/2026-09-08/typed-recall-401/run-13-m0631-selection/`，`TYPED-RECALL-401-RUN-07.md`）。
> 用户授权：技术取舍由执行者作为独立裁决人从契约文本裁定、记录并实施，不回问。本文 §2 为 11 组逐组裁决。
> 本轮**动了封存 fixture**：`fixture_revision` 16 → 17，两处改写，按封存修订规则各带一条 `fixture_change_lineage` 条目（谱系 + 中文理由 + 契约引用 + 前后 sha），并把「封存段落只能按谱系条目变化」写成可执行断言（`tests/test_typed_recall_bridge.py`）。

## 0. 一句话结论

**PASS 279 → 355 / FAIL 0 → 0 / BLOCKED 122 → 46。** 83 格发生变化：**76 格转 PASS**，7 格保持 BLOCKED 但改判类别与理由。原「FIXTURE 定义冲突」87 格降到 **4 格**（只剩 H 组投影，已裁决为 (a) 但接线排到下一轮）。runner 退出码仍为 3（整体 `NOT_RUN/BLOCKED`，46 格未闭合，属预期）。没有任何一格由 PASS 退化，没有放松任何封存断言。

| 阻塞类别 | run-13 | run-14 | 变化 |
|---|---:|---:|---:|
| `FIXTURE_INVALID_OR_INSUFFICIENT` | 87 | **4** | −83（76 转 PASS，7 改判类别） |
| `ORACLE_GAP` | 18 | **19** | +1（`protocol/page-correct-binding` 从 FIXTURE 迁入，见 §2.K） |
| `EXECUTOR_UNIMPLEMENTED` | 17 | **18** | +1（`current-use/context:new-continuation` 从 FIXTURE 迁入，见 §2.I-3） |
| `SDK_INCREMENT_REQUIRED`（**新类**） | — | **2** | I-1 / I-2，见 §2 |
| `DUPLICATE_UNTESTABLE`（**新类**） | — | **3** | F 组，见 §2.F |
| 合计 BLOCKED | 122 | **46** | −76 |

## 1. 裁决方法（先说判据，再说结论）

三条判据按顺序适用，**只有契约文本与封存文本无歧义地互相矛盾时才动 fixture**：

- **(a) 封存义务在本契约上成立且可见证 → 不改 fixture，只接线 oracle。** 封存 `INELIGIBLE` 的义务是 `eligibility_common_expectations.INELIGIBLE` 的四个零：`enters_rank_input:false`、`rank_input_delta:0`、`public_candidate_count_delta:0`、`forbidden_canary_hits:0`。**该义务不含 reason code 字段**（`unlisted_reason` 是轴对「为什么不在白名单里」的标注，不是对 Memory 运行时输出的断言）。因此当公共契约在**写入期/构造期**就拒绝该组合时，该行永远不可能存在，四个零以最强形式成立。
- **(b) 封存义务成立但契约/SDK 缺能力 → 不改 fixture，写出精确的 SDK 增量（file:line），维持 BLOCKED。**
- **(c) 格本身是重复或结构上不可测 → 不改 fixture，不提增量，维持 BLOCKED 并写明理由。**
- **(i-amend) 封存字面值被冻结公共契约证伪 → 按封存修订规则改写 fixture**（新 rev + 谱系条目 + 中文理由 + 契约引用），并把改写后的义务接成**更强**而不是更弱的断言。

**「入口拒绝算不算证明」这一步不接受空口判定。** 本轮为每一格构造了**成对对照（control pair）**：在同一个数据库、同一 query、同一 disclosure、同一 privacy、同一 payload 下，只改被测那一根轴，跑真实召回。禁止组合给出 0 候选，被准许的同胞给出恰好 1 条命中。于是「0」可归因到该轴，而不是空库、查询不匹配或前置条件缺失。实现见 `runners/typed_recall_forbidden_oracle.py`（纯标准库）与 `adapters/typed_recall_normal_cases.py::forbidden_control`。

## 2. 11 组逐组裁决

路径缩写：**H** = `/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-sdk/src/simple_harness/`（冻结 Harness 0.7.10）；**M** = `/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk-0631-source/src/simple_harness_memory/`；**F** = `testcase/human-memory-program/fixtures/typed-recall-v3.json`。

| 组 | 格数 | 裁决 | 一句话依据 | run-14 |
|---|---:|---|---|---|
| A `disclosure:*:AUDIT:*` | 24 | **(a)** 不改 fixture，接线 | 封存自己就写着 `USER_SELF×AUDIT → allowed:[], reason:SEALED_AUTHORITY_REQUIRED`；DTO 的 `ValueError` 是同一条规则在更早的缝上执行 | 24 PASS |
| B–E `epistemic:*`（4 类 × 12 对） | 48 | **(a)** 不改 fixture，接线 | 封存 `unlisted_expected:INELIGIBLE` 与 Harness/Memory 的写入期拒绝同向；四个零由「该行不可能存在」满足 | 48 PASS |
| F `contest-{nested,one-member,three-members}` | 3 | **(c)** 维持 BLOCKED | 公共 contest 变更没有 `members` / `nested_group_ref` 输入，攻击不可表达；同一不变式已由 `create_case` 正向证明 | 3 BLOCKED（`DUPLICATE_UNTESTABLE`） |
| G `epistemic:prospective:{llm_inference,unknown}:unverified` | 2 | **(a)** 不改 fixture，接线 | 两条契约规则复合：非权威 epistemic 只能停在 `candidate`；registration 只认 `pending/triggered` | 2 PASS |
| H `selection-budget/projection:*` | 4 | **(a)** 不改 fixture，**接线排到 run-15** | 封存 `source_record` 的 canary 在公共面可分为「可植入」与「严格 DTO 不可表示」两半，都能见证；本轮未接线 | 4 BLOCKED（`FIXTURE_INVALID_OR_INSUFFICIENT`，理由待接线后改写） |
| I-0 `current-use/authority:revoke` | 1 | **(i-amend)** 改写 fixture（FCL-002） | 0.6.29 用途围栏是**更精确**而非更宽松；revoke 恢复原状后仍拒绝是假阳性，封存行被契约证伪 | 1 PASS |
| I-1 `current-use/authority:policy_hash_change` | 1 | **(b)** 维持 BLOCKED + SDK 增量 | 义务（策略变更使旧授权失效）成立，但 `policy_hash` 是 Memory 常量、公共面无版本入参 | 1 BLOCKED（`SDK_INCREMENT_REQUIRED`） |
| I-2 `current-use/authority:short_source_cleanup` | 1 | **(b)** 维持 BLOCKED + SDK 增量 | `cleanup_short_horizon` 只在后端 port 上，Manager 无入口 | 1 BLOCKED（`SDK_INCREMENT_REQUIRED`） |
| I-3 `current-use/context:new-continuation` | 1 | **(a)** 不改 fixture，**接线排到 run-15** | continuation 轴在公共契约上就是用途请求的 `turn_id`（`H runtime/kernel.py:1284/1299`），并非「无对应字段」——run-07 的判断被推翻 | 1 BLOCKED（改判为 `EXECUTOR_UNIMPLEMENTED`） |
| J `eligibility/prospective-trigger-missing` | 1 | **(a)** 不改 fixture，接线 | 严格 DTO 位置式与 wire 两条路都拒绝缺失 typed trigger | 1 PASS |
| K `protocol/page-correct-binding` | 1 | **(i-amend)** 改写 fixture（FCL-001） | 一条公共 binding 的 canonical JSON 恒为 174 字节，封存的 (128, payload_count 1) 对冻结契约自相矛盾 | 1 BLOCKED（改判为 `ORACLE_GAP`，见下） |

### 2.A `disclosure:{6 个普通 recipient}:AUDIT:{4 个 privacy}`（24 格）→ (a)，24 格转 PASS

**冲突是假的。** 封存 `disclosure_cases` 自己列着：

```json
{"recipient":"USER_SELF","purpose":"AUDIT","allowed":[],
 "denied":["PUBLIC","PERSONAL","SENSITIVE","RESTRICTED"],"reason":"SEALED_AUTHORITY_REQUIRED"}
```

即 AUDIT 目的在没有封存权威时**对四个 privacy 全拒**。公共 DTO `DisclosureContext.__post_init__`（`H runtime/disclosure_protocol.py:273`）写的是同一条规则：

```
if self.purpose is DisclosurePurpose.AUDIT and (
        self.source is not DisclosureSource.AUDIT_ACCESS_DECISION
        or self.recipient is not DeliveryRecipient.AUDIT_REVIEWER):
    raise ValueError("audit disclosure requires an AuditAccessDecision and audit recipient")
```

`SEALED_AUTHORITY_REQUIRED` 与 `AuditAccessDecision + AUDIT_REVIEWER` 是同一件事，封存与契约**一致**，不存在需要改写的封存定义。run-13 判 FIXTURE 类是 oracle 欠接线，不是定义冲突。

**接线（`assess_disclosure`）**，六条断言全部为真才 PASS：

1. 该格记忆**真的写进去了**（1 条 source、receipt revision 1、`check_seed_authority` 全链通过）——库不是空的；
2. 该格的 `DisclosureContext(recipient=R, purpose=AUDIT)` 抛出逐字相同的 `ValueError`；
3. **把规则的两半分别隔离**：`AUDIT + R + AUDIT_ACCESS_DECISION` 仍被拒（说明 recipient 那一半在起作用）；`AUDIT + AUDIT_REVIEWER` 无 decision 也被拒（说明 decision 那一半在起作用）；`AUDIT + AUDIT_REVIEWER + AUDIT_ACCESS_DECISION` 构造成功（说明封存权威路径确实存在）；
4. 同 recipient 换成 `task_execution` 构造成功——拒绝可归因到 **purpose=AUDIT**，不是 recipient、不是 privacy；
5. 整个过程 **Memory 的 `execute_typed_recall` 调用数为 0**（事件流里没有该调用）⇒ 不存在 rank input，四个零按构造成立；
6. 同 recipient + `task_execution` + 该 privacy 的**真实召回**必须与封存 `disclosure_cases` 的 allowed/denied 判定一致（允许 → 恰好 1 条；拒绝 → 0 条，且必须是两种真实拒绝之一：recipient/purpose 门在候选层之前拒（`rejected` / `recall_disclosure_denied` / 候选查询 0 次）或 privacy 在资格门拒（`no_recall` / `recall_no_eligible_memory` / 候选查询 1 次），两个分支都逐字钉死）。

### 2.B–E `epistemic:{episode,semantic,procedure,prospective}` 的 12 个禁止对（48 格）→ (a)，48 格转 PASS

封存 `exhaustive_axis_contract.epistemic_verification.unlisted_expected = "INELIGIBLE"`；公共契约在写入期同向禁止，逐对钉死（`runners/typed_recall_forbidden_oracle.py::INGRESS_REFUSALS`）：

| epistemic × verification | 公共拒绝 | 出处 |
|---|---|---|
| `verified_external × {unverified, source_bound, user_confirmed, repeated_observation}` | `ValueError: verified_external requires source_verified state` | `H runtime/memory_protocol.py:2638`；Memory 同向 `M core/mutations.py:495` `mutation_verified_external_state_invalid` |
| `{llm_inference, unknown} × {source_verified, repeated_observation}` | `ValueError: verified states require trusted typed observation evidence` | `H runtime/memory_protocol.py:2647`（`trusted_typed` 只能由 TOOL/EXTERNAL 的 `TYPED_OBSERVATION` span 满足，依定义与这两个 epistemic 互斥） |
| `llm_inference × {source_bound, user_confirmed}` | `MemoryValidationError: mutation_inference_must_be_unverified` | `M core/mutations.py:523` |
| `unknown × {source_bound, user_confirmed}` | `MemoryValidationError: mutation_unknown_must_be_unverified` | `M core/mutations.py:510` |

**接线（`assess_epistemic` 的 ingress 分支）**：

1. 封存对该格的期望必须是 INELIGIBLE（用 `normal_expected(..., applicability=True, trigger_signal=True)` 取最宽松解释后仍为 False）；
2. 观测到的异常类型与文本与上表**逐字**相同（表里没有的对 → 直接 FAIL；SDK 改了拒绝文本 → 直接 FAIL，不会静默通过）；
3. 该格没有产生任何 durable source；
4. **零候选召回**：在同一库、同一 query、同一 disclosure 下真跑一次召回，`outcome=no_recall`、`items=0`、`filtered_candidate_count=0`、`candidate_query_started=true`、`candidate_query_count=1`、replay 零读且逐字相同——是真查过，不是没查；
5. **对照**：同 payload、同类型、同 privacy/attributes/recipient，只把 epistemic/verification 换成 `explicit_user/source_bound` 再种一条，真跑召回必须恰好 1 条，且命中的 `source_ref` 是对照记忆、`public_payload` 与投影逐字相同、对照 mutation 的 `epistemic_status/verification_state` 确实是被准许的那一对。Procedure 的对照会因 applicability 快照提交新 head，oracle 用 `record_procedure_observation` 返回的 `base_revision/committed_revision` 精确钉死，而不是放宽；
6. **canary**：禁止组合留下的 evidence id 不得出现在任何一次公共 result 的 canonical JSON 里。

### 2.F `conflict-state/contest-{nested,one-member,three-members}`（3 格）→ (c)，维持 BLOCKED

封存 `conflict_write_oracle.reject_cases` 期望 Memory 拒绝带 `nested_group_ref` 的 contest、以及成员数为 1 或 3 的组。公共 contest 变更**没有** `members`，也没有 `nested_group_ref`；组成员由 `_insert_cognitive_conflict_group_unlocked`（`M backends/sqlite_v5.py:11624-11640`）硬编码为 `(1,"incumbent")` / `(2,"challenger")` 两员，`nested_group_ref` 在 M 全树无符号。

**这不是契约缺陷。** 封存要保护的不变式（恰好两员、不嵌套）在本契约上是**结构上不可违反**的：没有能表达违反的输入。同一不变式已由 `create_case.expected_member_count: 2` 正向证明，run-13 又补上了 durable group/member/resolution 见证。因此封存没错、契约没错、该格是正向证明的重复且不可测——三条路都不该走 (a)/(b)/(i-amend)。维持 BLOCKED，理由改成 `DUPLICATE_OF_POSITIVE_INVARIANT_WITNESS:`，并归入新类别 `DUPLICATE_UNTESTABLE`（与「oracle 还没写」和「SDK 缺能力」分开记账）。将来若契约允许多成员或嵌套组，这三格自动恢复为活格。

### 2.G `epistemic:prospective:{llm_inference,unknown}:unverified`（2 格）→ (a)，2 格转 PASS

run-13 的 `REQUIRED_APPLICABILITY_OR_SIGNAL_AUTHORITY_NOT_ESTABLISHED` 是**真实的可归因性顾虑**：任何没有信号权威的 prospective 都召不回，光看到 0 不能说明是 epistemic 轴在起作用。裁决是：这里的不可召回不是「前置条件恰好没搭」，而是**两条契约规则的复合**，而复合本身可以被见证：

1. `M core/mutations.py:507/520`：`llm_inference` / `unknown` 的 lifecycle 只能是 `candidate|draft`，否则 `mutation_{inference,unknown}_cannot_be_authoritative`；
2. prospective 的 registration 命令只对 `pending/triggered` 发出。

**接线**：先按契约唯一可构造的状态（`candidate`）种进去并真跑召回（0 条）；再**主动探测**同一条记忆的 `pending` 版本 → 必须以 `mutation_{inference,unknown}_cannot_be_authoritative` 被拒（逐字钉死）；再读公共 outbox，该 memory_id 的 `memory.prospective.registration.requested` 命令数必须为 **0**；最后跑 `explicit_user/source_bound + pending + 真实 registration` 的对照 → 恰好 1 条。于是「召不回」被归因到 epistemic 轴，而不是缺信号。

### 2.H `selection-budget/projection:{semantic,episode,procedure,prospective}`（4 格）→ (a)，接线排到 run-15

裁决：**不改 fixture**。封存 `minimal_projection_oracle[*].source_record` 的 canary 分两半，两半在公共面都能见证：

- **可植入的一半**：`evidence_ids`（真实 evidence，把 id 写成封存的 `secret-evidence`）、`classification: SENSITIVE`（recipe 已经是 `privacy=SENSITIVE`）、`source_ref`（memory_id）、`conflict_status`。义务是投影后的 `public_payload` 的键集恰好等于 `allowed_payload_fields` 的公共对应，且这些值不出现在 payload 内。
- **严格 DTO 不可表示的一半**：`extra_typed_field: "must-strip"` 在 `SemanticMemoryPayload` 等 DTO 的 `_exact_keys`（`H runtime/memory_protocol.py:2194` 起）下根本进不来。这**不是**封存写错，而是「必须被剥掉」在本契约上以更强形式成立：它连进都进不来。见证方式是对 `PayloadClass.from_json({**payload, "extra_typed_field": "must-strip"})` 的拒绝下断言，而不是去改 fixture。
- `cross_scope: true` 同理：公共契约把 `cross_scope` 放在 **item 层**而不是 payload 层（`item['cross_scope']`），所以「不得出现在 `allowed_payload_fields` 里」由公共契约的字段布局直接满足；接线时同时断言 payload 内无该键、item 层该标志存在。
- episode 的 `occurred_interval` vs 公共 `occurred_start/occurred_end`：runner 侧的 `normal_projection()` **本来就在做这个翻译**（`runners/typed_recall_a2_oracle.py`），全部已 PASS 的 episode 格都走它。封存 `payload`/`payload_hash` 是这套抽象形态的自封一致性密封，不是对公共线格式的断言。接线方式是把公共投影**逆翻译**回抽象形态后与封存 `payload` 逐字比对并核对 `payload_hash`，而不是重新封存哈希。
- procedure/prospective 另需把 `projection` 家族加入 `typed_recall_{procedure,prospective}_cases.supported()` 的 family 白名单，复用既有 binding 适配器。

本轮时间用于 A/B–E/G/J/I-0/K 的落地，H 组的接线**未做**，4 格仍为 `FIXTURE_INVALID_OR_INSUFFICIENT`。这是本轮唯一「裁决已定但未落地」的一组，列入 §5 后继第 1 项。

### 2.I-0 `current-use/authority:revoke`（1 格）→ (i-amend)，改写 fixture，转 PASS

封存行要求 revoke 之后旧结果的用途授权以 `RECALL_AUTHORITY_STALE` 被拒。M0.6.29 把用途围栏从「epoch 相等」改成「**epoch 不倒退 + 逐个被绑定来源重校验**」（`M backends/sqlite_v5.py:4674`，程序侧决策备忘 `testcase/human-memory-program/DECISION-2026-09-08-recall-authority-epoch.md`）。revoke 撤销的是一条压制指令，净效果是把被绑定来源**恢复到旧结果计算时的状态**：逐来源重校验必过，旧结果将要披露的内容与当初逐字相同。此时仍然拒绝是假阳性——会把「撤销遗忘」变成一次不必要的重新召回。

**契约更精确而非更宽松，封存行被证伪。** 按封存修订规则改写（`fixture_change_lineage` FCL-002）：

```
authority_event_cases[revoke].expected_old_result_use:
  "RECALL_AUTHORITY_STALE" -> "AUTHORIZED_WITH_ADVANCED_AUTHORITY_EPOCH"
```

改写后本格义务**更强**：oracle 必须见证 (1) 新用途被准入且有收据；(2) 收据 `authority_epoch` 严格大于旧结果绑定的 epoch；(3) `policy_hash` 不变；(4) `item_bindings` 与 `result_hash` 与首张收据逐字相同而 `receipt_id` 不同；(5) 压制目标、中间召回只剩 item2、撤销精确指向该 directive、恢复后两项都回来、事件顺序、历史 exact replay 零读——原有断言一字未动。判据仍是**双向**的：单测 `test_typed_recall_authority_event_public.py` 里既有「候选恢复 STALE 拒绝 → FAIL」的反例，也有「把封存行改回 STALE → FAIL」的反例。`expected_receipt_hash` 保持原值不动（legacy 字面量，`approved_oracle.legacy_status` 已将其排除出有效验收证据）。

### 2.I-1 `current-use/authority:policy_hash_change`（1 格）→ (b)，维持 BLOCKED

`_RECALL_POLICY_HASH` 由 `{"policy":"typed-recall-eligibility/v1","schema":6,"rrf_k":60,"weights":{…}}` 的 canonical sha256 得出，是 Memory 常量（`M backends/sqlite_v5.py:300-315`），公共 API 无任何策略版本入参。**封存义务本身是正当的**（召回策略变更后，此前基于旧策略签发的用途授权必须失效），只是这条安全属性在当前契约上无法被测试。因此不改 fixture，写出所需增量并维持 BLOCKED：

> **SDK 增量（Memory 0.6.32+）**：为 `MemoryManager` 提供公共的召回策略版本入口——要么把 eligibility policy 做成可注入的记录（带 `policy_id/policy_version`，`policy_hash` 由其推导），要么提供 `MemoryManager.set_recall_policy(...)` 之类的受控入口。位置：`M backends/sqlite_v5.py:300-315`（常量定义）+ `M core/manager.py`（公共入口）。

reason 前缀由 `PUBLIC_CONTRACT_CONFLICT:` 改为 `SDK_INCREMENT_REQUIRED:`，并归入新类别，避免继续被记在「fixture 不合法」账上。

### 2.I-2 `current-use/authority:short_source_cleanup`（1 格）→ (b)，维持 BLOCKED

`cleanup_short_horizon` 只存在于后端 port（`M core/port.py:465-467`）；`MemoryManager` 只有 `cleanup_recall_results`（`M core/manager.py:1245`）。

> **SDK 增量（Memory 0.6.32+）**：`MemoryManager.cleanup_short_horizon(...)`。

明确**不接受**用 `short_source_expiry` 冒充同一事件：过期与清理是两件事，前者已单独有格且已 PASS。

### 2.I-3 `current-use/context:new-continuation`（1 格）→ (a)，run-07 的判断被推翻，接线排到 run-15

run-07 判为 (b) 重定义，理由是「`RecallContextUseAuthorizationRequestV1` 无 `continuation_id`，continuation 轴在公共契约上无对应字段」。**这个理由不成立。** Harness 在把一次用户 continuation 入队时，直接把 continuation 身份当成用途请求的 turn 身份：

```
H runtime/kernel.py:1284   turn_id=continuation_id,
H runtime/kernel.py:1299   "context_use_turn_id": continuation_id,
```

即 continuation 轴在公共契约上**就是** `RecallContextUseAuthorizationRequestV1.turn_id`。封存那条「同 run / 同 turn-7 / 换 continuation」的组合在 Harness 自己的映射下根本不存在（换 continuation 必然换 context-use turn），但**封存要保护的属性完全可测**：同 run、同 provider attempt、换 context-use turn ⇒ 请求哈希不同 ⇒ `NEW_AUTHORIZATION_REQUIRED`。

裁决：不改 fixture（封存 `expect: NEW_AUTHORIZATION_REQUIRED` 就是要证的东西；`expected_receipt_hash` 是 legacy 字面量，现有 context-use oracle 本来就不比对它）。本轮把 reason 从「无对应公共字段」的错误陈述改为 `CELL_EXECUTOR_NOT_IMPLEMENTED:` 并写清接线配方（同 run 同 attempt 换 turn，期望新收据且首张收据的 `validate_request` 失败），归入 `EXECUTOR_UNIMPLEMENTED`。接线列入 §5 后继第 2 项。

### 2.J `eligibility/prospective-trigger-missing`（1 格）→ (a)，转 PASS

封存 `{"typed_trigger_complete": false, "expected": "INELIGIBLE", "reason": "TYPED_TRIGGER_REQUIRED"}`。严格 DTO 两条路都拒：位置式构造 `ProspectiveMemoryPayload(action, None)` 抛 `TypeError("trigger must use a strict time or event trigger")`（`H runtime/memory_protocol.py:2469`），wire 形式 `from_json` 走 `_exact_keys` 同样拒绝未类型化的 trigger。**接线**：两条拒绝都逐字记录并断言；随后在**同一个库**用同一条 action 和完整 typed trigger 种一条、真做 registration、真跑召回 → 恰好 1 条，全套 source/request/hash/access/replay 绑定沿用既有 `recall()` 校验器。缺 trigger 的那条永远不可能存在 ⇒ 四个零成立，且「0」可归因到 trigger 轴。单测里加了反例：改掉构造拒绝文本即 FAIL。

### 2.K `protocol/page-correct-binding`（1 格）→ (i-amend)，改写 fixture，但**仍 BLOCKED**（改判为 ORACLE_GAP）

封存 `bounds: {offset: 0, length: 128}` + `payload_count: 1` + `HASH_IDENTICAL_RESULT_BOUND_PAGE`。分页按每条 binding 的 canonical JSON 累加计费（`M backends/sqlite_v5.py:4641-4648`）；冻结的 `RecallResultPageBindingV1` 形状定长：

```json
{"binding_kind":"selected_item","item_hash":"<64 hex>","item_id":"recall-item:<24 hex>:1","ordinal":1}
```

canonical JSON **恒为 174 字节**。128 字节连一条都装不下，必抛 `MemoryLimitError("typed_recall_page_budget_too_small")`。封存三元组对冻结契约自相矛盾 ⇒ 封存字面值失效（`fixture_change_lineage` FCL-001，`length: 128 → 174`）。

**新值不是从观测数字迁移阈值**，而是由公共 DTO 的定长字段结构推导。为保证这仍是边界判定而不是「按实现改测试」，adapter 额外执行 **173 字节反例**，必须被 `typed_recall_page_budget_too_small` 拒绝；oracle 断言 `max_bytes == sealed`、`under_bound` 恰为 `sealed-1` 且被拒、`bindings` 数等于封存 `payload_count`、`byte_count == sealed`，并跑完整的 `check_page` 头/内容/结果项绑定。`expected_page_hash` 保持原值（legacy；页哈希含每轮变化的 `page_id/result_id`，公共面本就不可冻结）。

**该格仍 BLOCKED**：分页成功后暴露出**另一个独立**的缺口——封存 `candidate_query_started: false` 在公共面没有逐次调用的候选读见证（`TypedRecallRejectionV1` 只由 `execute_typed_recall` 挂载），与 `page-wrong-*` 三格是同一个 `PUBLIC_WITNESS_UNAVAILABLE`。所以本格从 `FIXTURE_INVALID_OR_INSUFFICIENT` 迁到 `ORACLE_GAP`：fixture 侧的缺陷已清除，剩下的是见证缺口。列入 §5 后继第 3 项。

## 3. 封存修订的可执行护栏

`fixture_change_lineage` 不是注释，是被断言的对象。`tests/test_typed_recall_bridge.py::test_0613_successor_retains_original_obligations_and_candidate_lineage` 现在这样判：

1. 每条谱系条目必须齐备 `entry_id / date / section / case_id / field_path / previous_value / new_value / previous_fixture_sha256 / previous_section_sha256 / new_section_sha256 / authority / contract_citation / rationale_zh`；
2. `fixture_revision_to == fixture["fixture_revision"]` 且 `fixture_revision_from == to - 1`；
3. **未被谱系点名的段落**仍按老规矩与基线提交 `60f280dc` 逐字相等；
4. **被点名的段落**：格集合必须与基线完全一致，且每一格必须等于「基线那一格 + 该格谱系条目声明的那些字段替换」——多改一个字节就 FAIL；
5. 谱系声明的 `previous_section_sha256 / new_section_sha256` 必须分别等于基线段落与当前段落的 canonical 摘要；
6. `approved_oracle.unchanged_sections_sha256` 的密封只允许在被点名的段落上移动，其余逐条不变；
7. layers 侧同理：`layers_change_lineage` 必须与 `fixture_revision` / `typed_recall_fixture_revision` 对齐，`all_cells` / `source_exact_commit_integration` / `combined_gate` 一字未动。

即：**静默改封存在本仓已经不可能**——要么带谱系，要么测试红。

修订后的常量：

| 项 | run-13 | run-14 |
|---|---|---|
| `fixture_revision` | 16 | **17** |
| fixture sha256 | `31fbb8bc…` | **`d63b0bb6dbd19874de86aa96622d0b327adbc4e34502970d1de093a49aef5d09`** |
| layers `fixture_revision` | 14 | **15** |
| layers sha256 | `83238bc6…` | **`101c14ba058dce423a1c59b682e28c1b508ba6011f534fa7382d0dfa9dfecc0a`** |
| `approved_oracle.unchanged_sections_sha256.result_page_cases` | `ac65e7dc…` | **`6e0787b6…`** |
| `approved_oracle.unchanged_sections_sha256.authority_event_cases` | `5ab482df…` | **`04fe212d…`** |

fixture 净改动：**41 行新增 / 3 行删除**（两处值 + `fixture_revision` + 两条谱系 + 两个段落密封）。

## 4. 正式扫描（run-14）

### 4.1 命令

消费者 venv（`.local-test-evidence/2026-09-08/venv-m0631`）与 SDK 源 worktree（`simple-harness-memory-sdk-0631-source`，HEAD `ff8be5f3…`）沿用 RUN-06 §2.1/2.2，未重建、未改 pin。runner 自回归（run-14 之前）：

```bash
TYPED_RECALL_SOURCE_CHECKOUT=/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk-0631-source \
/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/.venv/bin/python -m pytest \
  <worktree>/testcase/human-memory-program/tests -q -p no:cacheprovider
# 114 passed, 31 subtests passed（与 run-13 前同数；本轮改了 5 个测试文件的期望，未减少用例）
```

正式扫描与 RUN-06 §2.3 逐字相同，仅 `--artifact-dir` 改为 `.../typed-recall-401/run-14-m0631-adjudication`，输出到 `run-14.log` / `run-14.stderr`；artifact 目录执行前不存在；**不传 `--consumer-entrypoint`**。退出码 3，stderr 为空。

### 4.2 结果

| 项 | 值 |
|---|---|
| artifact | `.local-test-evidence/2026-09-08/typed-recall-401/run-14-m0631-adjudication/`（560 MB） |
| `run_id` | `7e6599f3274d4307ab76ef7ffc3b8677`（2026-09-08T16:35:46.349Z → 16:36:38.355Z，52 s） |
| 候选身份 | Harness 0.7.10 / `031fdc68…` / `e559bc1b…`；Memory 0.6.31 / `ff8be5f3…` / `e8dc27cc…`（`candidate_pin_differences=[]`） |
| 隔离 | `isolated-process-verified-installed-wheels`；`oracle_blockers=[]` |
| 整体 | `NOT_RUN/BLOCKED`；`acceptance_counts` **PASS 355 / FAIL 0 / BLOCKED 46** |
| 层 | public：passed 345 / failed 0 / blocked 19；source：passed 10 / failed 0 |
| 按 lane（PASS / BLOCKED） | conflict-state 14/6；current-use 12/5；**eligibility 294/7**（run-13 为 219/82）；fault-recovery 7/0；protocol 4/7；selection-budget 5/21；unsupported-replay 19/0 |
| `fixture_sha256` / `execution_layers_sha256` | `d63b0bb6…` / `101c14ba…` |
| `oracle_code_sha256`（a2） | `97b58d3315386bbe47110666ead5497696ea618c91066673052f5ade4a09a1c8` |
| 新增执行代码 | `runners/typed_recall_forbidden_oracle.py` `d798bb2b…` |
| 变更执行代码 | `adapters/typed_recall_normal_cases.py` `8ad53d89…`、`adapters/typed_recall_trigger_cases.py` `d3bcf9d3…`、`adapters/typed_recall_return_cases.py` `f09bdf87…`、`adapters/typed_recall_authority_event_cases.py` `06f37632…`、`runners/typed_recall_a2_oracle.py` `97b58d33…`、`runners/typed_recall_trigger_oracle.py` `4dec5e0c…`、`runners/typed_recall_authority_event_oracle.py` `3824db88…` |
| bridge-summary / public-observations / source-observations sha256 | `1e454765…` / `07ce90f8…` / `d6608617…` |

## 5. 逐格对照 run-13 → run-14

**83 格变化；其余 318 格状态与 reason 逐字相同。**

### 5.1 转 PASS 的 76 格

| 组 | 格数 | 格 ID 模式 |
|---|---:|---|
| A | 24 | `eligibility/disclosure:{USER_SELF,HOUSEHOLD,TASK_COLLABORATOR,EXTERNAL_PARTY,PUBLIC,UNKNOWN}:AUDIT:{PUBLIC,PERSONAL,SENSITIVE,RESTRICTED}` |
| B | 16 | `eligibility/epistemic:{4 类}:{llm_inference,unknown}:{source_verified,repeated_observation}` |
| C | 16 | `eligibility/epistemic:{4 类}:verified_external:{unverified,source_bound,user_confirmed,repeated_observation}` |
| D | 8 | `eligibility/epistemic:{4 类}:llm_inference:{source_bound,user_confirmed}` |
| E | 8 | `eligibility/epistemic:{4 类}:unknown:{source_bound,user_confirmed}` |
| G | 2 | `eligibility/epistemic:prospective:{llm_inference,unknown}:unverified` |
| J | 1 | `eligibility/prospective-trigger-missing` |
| I-0 | 1 | `current-use/authority:revoke` |

### 5.2 维持 BLOCKED 但改判的 7 格

| 格 | run-13 类别 / 理由前缀 | run-14 类别 / 理由前缀 | 依据 |
|---|---|---|---|
| `conflict-state/contest-nested` | FIXTURE / `PUBLIC_CONTRACT_CONFLICT:` | **DUPLICATE_UNTESTABLE** / `DUPLICATE_OF_POSITIVE_INVARIANT_WITNESS:` | §2.F |
| `conflict-state/contest-one-member` | 同上 | 同上 | §2.F |
| `conflict-state/contest-three-members` | 同上 | 同上 | §2.F |
| `current-use/authority:policy_hash_change` | FIXTURE / `PUBLIC_CONTRACT_CONFLICT:` | **SDK_INCREMENT_REQUIRED** / `SDK_INCREMENT_REQUIRED:` | §2.I-1 |
| `current-use/authority:short_source_cleanup` | 同上 | 同上 | §2.I-2 |
| `current-use/context:new-continuation` | FIXTURE / `PUBLIC_CONTRACT_CONFLICT:` | **EXECUTOR_UNIMPLEMENTED** / `CELL_EXECUTOR_NOT_IMPLEMENTED:` | §2.I-3 |
| `protocol/page-correct-binding` | FIXTURE / `FROZEN_128_BYTE_PAGE_BOUND…` | **ORACLE_GAP** / `PUBLIC_WITNESS_UNAVAILABLE:` | §2.K |

原始观测层与 RUN-06 §4.3 同理：每轮随机 init authority_ref 与 rejection UUID 带来 hash 级差异，oracle 只看 hash 间关系。本轮所有判定变化都可归因到 §2 列出的 runner 层改动与两条 fixture 谱系条目。

## 6. 剩余 46 格 BLOCKED

| 类别 | 格数 | 内容 |
|---|---:|---|
| `EXECUTOR_UNIMPLEMENTED` | 18 | run-13 的 17 格（short registration/classification 2、budget deadline/short-emoji/short-escaping 3、dedupe 4、ranking-order 1、tie-* 5、procedure 观察晋升 1、projection:short_horizon 1）+ 新迁入的 `context:new-continuation` |
| `ORACLE_GAP` | 19 | run-13 的 18 格（conflict 精确 reason 3、context-use exact-once 2、page 拒绝 3+`naked-source-ref`、短时域渲染/TTL 6、executed-lane 3）+ 新迁入的 `page-correct-binding` |
| `FIXTURE_INVALID_OR_INSUFFICIENT` | 4 | 仅 H 组 `selection-budget/projection:*`，已裁决为 (a)，接线未做 |
| `SDK_INCREMENT_REQUIRED` | 2 | `authority:policy_hash_change`、`authority:short_source_cleanup` |
| `DUPLICATE_UNTESTABLE` | 3 | `contest-{nested,one-member,three-members}` |

## 7. 后继（followup 清单）

1. **H 组接线（4 格）** —— 按 §2.H 的配方：真实 `secret-evidence` canary + SENSITIVE + `extra_typed_field` 的 `from_json` 拒绝见证 + payload 键集恰好等于 `allowed_payload_fields` + 逆翻译后核对封存 `payload_hash`；`projection` 家族加入 procedure/prospective 的 `supported()` 白名单。**不需要改 fixture。**
2. **I-3 接线（1 格）** —— 同 run、同 provider attempt、换 context-use turn（= 封存的 `continuation-2`），期望新收据、`receipt_id` 不同、首张收据 `validate_request` 报 `receipt request_hash differs`；oracle 侧 `check_bundle` 需要一个「turn 不等于 recall context turn」的显式开关。**不需要改 fixture。**
3. **分页零候选读见证（4 格：`page-correct-binding` + `page-wrong-*` 3）** —— 提交 Memory 增量提案：分页拒绝/成功都附 `TypedRecallRejectionV1` 或等价的逐次候选读计数。或以「压制底层来源后分页仍返回逐字相同的字节」作为间接见证（需新增执行步骤，属 oracle 侧方案，可先做）。
4. **SDK 增量提案（Memory 0.6.32+）** —— (a) 公共召回策略版本入口（I-1）；(b) `MemoryManager.cleanup_short_horizon`（I-2）；(c) apply 结果携带精确 validation reason，至少让三个 `mutation_contest_*` 一一映射（conflict 3 格）；(d) 公开「实际执行 lane」（vector 3 格）。
5. **选择车道剩余（run-07 §8.2 未变）** —— `tie-score` / `tie-matched-lane-count` 可用 run-13 的认知+短时域框架接线（预计 +2）；`tie-memory-type-empty` / `tie-source-ref` / `tie-source-revision-or-zero` 在公共面不可打平，应作为契约事实格另行裁决。
6. **矩阵覆盖缺口（run-07 §8.3/8.4 未变）** —— 0.6.31 的争议短路负例格、0.6.29 用途围栏的正例格（「压制别的记忆后 epoch 前进、被绑定来源未变 → 应当签发收据」）都还没有对应格；后者与本轮 FCL-002 配套，建议一并补。
7. **bridge 层级 PASS 规则仍缺失** —— 任何一层永远停在 `NOT_RUN/BLOCKED`；46 格未清零前不影响结论。

## 8. 边界遵守

未改 `ARCHITECTURE/`、`backend/`、Host pin、桌面应用；未运行 18120 端口；全程单一 runner/pytest 进程（本仓 `backend/tests` 上另有其他会话的 pytest 进程，与本轮的临时库、artifact 目录、进程树完全不相交）。API key 未打印、未提交。所有改动限于 `testcase/human-memory-program/` 与本记录。
