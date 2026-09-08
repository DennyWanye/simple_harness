# 401 矩阵 run-09 §10 后继落地（run-16，M0.6.31 pin 不变，fixture 未动）

> 2026-09-09。分支 `worktree-matrix-401-r4`（Host main `3f4af60b`）。
> 对照基线：run-15（`.local-test-evidence/2026-09-08/typed-recall-401/run-15-m0631-followups/`，`TYPED-RECALL-401-RUN-09.md`）。
> 用户授权：技术取舍由执行者作为独立裁决人从契约文本裁定、记录并实施，不回问。
> 本轮**没有动封存 fixture**：`typed-recall-v3.json`（rev 17）与 `typed-recall-execution-layers-v1.json`（rev 15）在 `git status` 中都不出现，fixture sha256 `d63b0bb6…` / layers sha256 `101c14ba…` 与 run-15 逐字相同。所有改动都在 runner / adapter / tests 三处执行代码上。

## 0. 一句话结论

**PASS 366 → 382 / FAIL 0 → 0 / BLOCKED 35 → 19。** 18 格发生变化：**16 格转 PASS**（短时域 9、选择/预算 6、程序观察晋升 1），**2 格保持 BLOCKED 但从「执行器没写」改判为「公共契约上不可构造重复」**并归入既有类别 `CONTRACT_FACT_UNPAIRABLE`。其余 **383 格状态与 reason 逐字相同**，没有任何一格由 PASS 退化，没有放松任何封存断言。**`EXECUTOR_UNIMPLEMENTED` 归零**（12 → 0）。runner 退出码仍为 3（整体 `NOT_RUN/BLOCKED`，19 格未闭合，属预期）。

| 阻塞类别 | run-15 | run-16 | 变化 |
|---|---:|---:|---:|
| `ORACLE_GAP` | 14 | **8** | −6（短时域 6 格转 PASS） |
| `EXECUTOR_UNIMPLEMENTED` | 12 | **0** | −12（10 格接线转 PASS，2 格改判类别） |
| `CONTRACT_FACT_UNPAIRABLE` | 4 | **6** | +2（两个 exact-dedupe 格，见 §6） |
| `DUPLICATE_UNTESTABLE` | 3 | **3** | 不变（F 组 contest 三格） |
| `SDK_INCREMENT_REQUIRED` | 2 | **2** | 不变（I-1 / I-2，SDK 仓另有代理处理） |
| 合计 BLOCKED | 35 | **19** | −16 |

按 lane（PASS / BLOCKED）：conflict-state 14/6；current-use 13/4；eligibility **301/0**（原 294/7，短时域 6 格 + 程序晋升 1 格全部闭合）；fault-recovery 7/0；protocol **11/0**（原 9/2）；selection-budget 17/9（原 10/16）；unsupported-replay 19/0。**eligibility、protocol、fault-recovery、unsupported-replay 四条 lane 已全绿。**

## 1. 本轮处理的 run-09 §10 条目

| §10 条目 | 处置 | 结果 |
|---|---|---|
| 1. 短时域一组 8 格整组接线 | 按 §3 配方接线，且额外把 `projection:short_horizon` 一并做掉 | **9 格 PASS** |
| 2. dedupe 4 + ranking-order 1 + budget 3 | ranking-order、dedupe 跨源 2 格、budget 3 格接线；exact-dedupe 2 格裁决为不可构造 | **6 格 PASS + 2 格改判** |
| 3. `projection:short_horizon` 单列 | 并入短时域组（同一 adapter 同一 oracle） | 见 §3.5 |
| 4. conflict 精确 reason 3、executed-lane 3、context-use exact-once 2 | 复核 0.6.31 公共面后**仍无见证**，保持提案态 | 见 §9 |
| 5. `_validate_execution_layers` 修订白名单过时 | 修复 + 反例 + 让 `--self-check` 真的跑路由 oracle | 见 §2 |
| 6. 格集合扩张规则 | 本轮不涉及（未加格） | — |
| 7. bridge 层级 PASS 规则 | 未变（19 格未清零前不影响结论） | — |
| 附加 | 程序观察晋升 1 格（run-09 §9 列在 `EXECUTOR_UNIMPLEMENTED`） | **1 格 PASS**，见 §5 |

## 2. `--self-check` 修订白名单缺陷修复

### 2.1 缺陷

`runners/run_typed_recall_public_consumer.py::_validate_execution_layers` 里写死
`layers.get("fixture_revision") not in (1, 2, 3, 4, 5)`。layers 文件今天是 rev 15，所以这条路由 oracle 必然报
`execution-layers schema/revision unsupported` —— 这个错误与「391/10 分层是否正确」毫无关系，纯粹是一条随封存 bump 一起过期的字面量白名单。

### 2.2 修法：从文件自己的谱系推导，而不是再写一个字面量

新增 `_execution_layers_revision_errors(layers)`，判据全部来自 layers 文件**自身**的 `layers_change_lineage`：

1. `fixture_revision` 必须是 ≥1 的整数（`bool` 不算整数）；
2. `layers_change_lineage` 必须是条目列表；**为空时** revision 必须是 1（从没被修订过）；
3. 每条条目 `layers_revision_to == layers_revision_from + 1`（单步 bump），条目之间首尾相接（`from == 上一条的 to`）；
4. **最后一条的 `layers_revision_to` 必须恰好等于声明的 `fixture_revision`**；
5. 每条的 `previous_layers_sha256` 必须是小写 hex64；
6. 最后一条的 `typed_recall_fixture_revision_to` 必须等于声明的 `typed_recall_fixture_revision`。

这样：**将来任何一次合规的 bump 都不需要改代码**，而任何一个与自身谱系对不上的 revision 仍然被拒。

### 2.3 顺带让 `--self-check` 真的走这条路由

`self_check()` 在 `fixture_revision >= 4` 时直接返回 `NOT_RUN/BLOCKED`（rev4 起改用独立 A2 oracle 回归，合成 artifact 自检刻意停用）。这条早退让**路由 oracle 一起被跳过**了。现在改为：先跑 `_validate_execution_layers`，路由失败照样返回 FAIL；路由通过则仍返回原来的 `NOT_RUN/BLOCKED`，但把路由结论一并报出来。今天实测：

```json
{"clean_wheel_public_manager_cells": 391, "combined_exact_union_cells": 401,
 "execution_layers_routing": "PASS", "source_exact_commit_integration_cells": 10,
 "status": "NOT_RUN/BLOCKED", ...}   // 退出码 3
```

即：路由 oracle 现在真的在 `--self-check` 路径上跑，并且是 PASS；合成 artifact 自检仍按原设计停用。**这条修改不可能把任何一格变成 PASS**（它只在 `self_check` 与 bridge 测试里被调用，401 正式扫描不走这条路径）。

### 2.4 反例

`tests/test_typed_recall_bridge.py::test_execution_layers_revision_is_derived_from_the_files_own_lineage`：真 fixture + 真 layers 必须 `status == PASS` 且 `--self-check` 报 `execution_layers_routing == PASS`；随后 8 个变体逐条必须报出对应错误——revision 改成 16、谱系清空、`layers_revision_from` 拉成 13（非单步）、两条条目之间留断口、`typed_recall_fixture_revision_to` 改成 16、`previous_layers_sha256` 换成非 hex64、revision 改成 0、谱系换成字符串。`test_sealed_rules_forbid_introducing_a_new_cell` 里原来那句「唯一残留错误是过时白名单」的注释同步删掉，并升级为 `status == PASS` 的正向断言。

## 3. 短时域一组 → 9 格 PASS

run-15 里这 9 格分成三堆：6 格 `ORACLE_GAP`（reason 全是 `SHORT_COMPLETE_REGISTRATION_CLASSIFICATION_TIME_AND_ORIGINAL_HASH_BINDINGS_PENDING`）、2 格 `EXECUTOR_UNIMPLEMENTED`（`short-registration-invalid` / `short-classification-invalid`）、1 格 `EXECUTOR_UNIMPLEMENTED`（`projection:short_horizon`）。三堆共用同一条公共链路，本轮整组做掉。

### 3.1 注册与原始哈希绑定：`resolve_typed_short_horizon_sources`

run-07 的「无见证」清单在 0.6.31 上被推翻。`MemoryManager.resolve_typed_short_horizon_sources(principal, disclosure_context, bindings)` 接受一组 `HistoryRecallBinding`（result_id / result_hash / item_id / item_hash，全部来自公共执行回执），返回 `ShortHorizonSourceSnapshot`，其中每个被选中的短时域条目展开为 `ShortHorizonSourceRef`：

`evidence_id / envelope_hash / source_ref / source_hash / sanitized_hash / admission_receipt_id / admission_receipt_hash / registration_id / registration_hash / item_ordinal / role`

oracle（`assess_short::check_short_source_bindings`）要求这一组**逐字等于** Host 在 Memory 见到它之前构造的注册对象（从 `register_conversation_evidence` 事件里取），并且 `visible / complete` 为真、`reason == 'history_visible'`。这正是那条 pending reason 里的「registration + original hash bindings」两半。

**反向见证**：同一次调用把每个 `item_hash` 翻一位再跑一遍，快照必须 fail-closed —— `visible=false`、`reason='history_binding_mismatch'`、`source_refs=[]`。（0.6.31 在这里不抛异常，而是返回不可见条目；oracle 按实测事实钉死这个更强的形状。）

### 3.2 分类绑定

`register_conversation_evidence` 事件里记录了 Host 侧 `has_authorized_public_text` / `effective_privacy_class`（由 `authorize_conversation_public_text` 派生，不是 runner 硬写）。oracle 要求：注册的 `effective_privacy_class` == 用例请求的 privacy class == 召回条目的 `item['effective_privacy_class']`，且 `information_attributes` 为空数组。

### 3.3 时间绑定与 TTL 边界

`item['public_payload']['occurred_at']` 必须等于注册时的 `occurred_at`；`selected_item['source_content_hash']` 必须等于 `sha256(渲染后的公共 content)`（即 `"user: " + public_text` 的哈希，独立重算）；快照的 `valid_until` 必须等于 `occurred_at + 5 天`（`SHORT_HORIZON_RETENTION_SECONDS`，作为 runner 侧输入常量引用并注明出处：封存行自己的 `expires_at` 是 1 小时的**前置字面量**，与产品 TTL 不是一回事，本轮不改封存值），并且召回时钟必须严格早于它。

### 3.4 `short-expiry-equals-now`：用产品自己算出的到期时刻重放边界

封存行要求 `expires_at == now ⇒ EXPIRED`。产品的到期时刻不是封存里的 1 小时，而是 `occurred_at + 5 天`——但**不需要**由 runner 假设：先在正常时钟下召回一次，从 §3.1 的快照里读回这条 chunk 自己的 `valid_until = E`，然后：

| 时钟 | 结果 |
|---|---|
| 原 now（`E − 5 天 + 1 小时`） | 1 条命中（对照，且完成 §3.1–3.3 全部绑定） |
| `E − 1s` | 1 条命中，且 public_payload 与上面逐字相同（边界另一侧） |
| **`E`（= expires_at）** | **0 条，`outcome=no_recall`，`filtered_candidate_count=0`** |

短时域候选收集器的过滤条件是 `occurred_at<=now AND expires_at>now`（`M backends/sqlite_v5.py`），所以「等于」这一侧落在 EXPIRED。封存行自身的关系（`expires_at == now`）也被断言，`short-chain-complete` 对称地断言封存的 `expires_at > now`。

### 3.5 `selection-budget/projection:short_horizon`

沿用 run-15 §2 的 canary 方法，落到短时域这一型：

- **抽象→公共映射**声明为 `short_projection(sealed_payload) = {'content': 'user: ' + content, 'occurred_at': seconds(occurred_at)}`（`render_short_horizon_line` 自 S3 Task 4 冻结，`assess_short` 原本就在用同一条渲染），并双向钉死：`sha256(canonical(sealed payload)) == sealed payload_hash`（实测为真）、`set(sealed payload) == set(allowed_payload_fields)`、`item['public_payload'] == short_projection(sealed payload)`、公共键集恰好等于 `allowed_payload_fields`。
- **可植入的 canary**：`evidence_ids=['secret-evidence']` 真的作为被 admit 的会话证据 id 落库；`classification='SENSITIVE'` 真的是本用例的 privacy class。义务是它们一个字节都不得进 `public_payload`：`evidence_manifest_hash == hash_json(sorted(['secret-evidence']))`（被禁 id 只以哈希形式可达）、`effective_privacy_class == 'sensitive'`。
- **契约上不可表示的 canary**：`chunk_ref` / `source_ref` / `extra_typed_field` / `conflict_status` 在短时域公共 payload 里根本没有位置（收集器只投影 `content` / `occurred_at` 两个键）。`chunk_ref` 被钉在 **item 层**（`selected_item['chunk_ref'] == selected_item['source_ref']` 且以 `short:` 开头），`cross_scope` 同样是 item 层（`False` 且 `source_task_scope_ids == []`）。
- **逐值扫描**：canary 的所有字符串叶子（`chunk-a` / `forbidden` / `secret-evidence` / `SENSITIVE` / `uncontested` / `must-strip`）都不得出现在 `canonical(public_payload)` 里。

### 3.6 `short-registration-invalid`（REGISTRATION_INVALID）

封存行是 `registration_valid=false, classification_valid=true`。Memory 自己的 fail-closed 分支在
`register_conversation_evidence`：它先向 Host 注册表要回注册对象，再比对
`(registration_id, registration_hash, evidence_id, envelope_hash)`，对不上就
`MemoryValidationError('conversation_registration_authority_rejected')`。

要观测到这条分支，Host 注册表必须**答非所问**。因此在 `CaseManager` 上加了一个显式的、按用例开关的 Host 失当探针 `conversation_misbinding`：只有这一格把「注册 1 的 id」映射到「注册 2 的持久对象」，其余所有用例仍走原来的严格绑定（探针默认空字典，绝不隐式生效）。观测：

- 先注册 10 个真实 filler 组填满 recent-10 窗口，于是**目标组是唯一可能被投影的组**；
- 用错绑引用调 `register_conversation_evidence` → `MemoryValidationError: conversation_registration_authority_rejected`；
- `rebuild_short_horizon_projection` → `projected_chunk_count == 0`；召回 0 条、`no_recall`；
- **成对对照**（另一个真数据库、同一段文本、同样 11 组）：走诚实绑定 → 1 chunk、1 条命中。拒绝只能归因到注册身份这一根轴。

### 3.7 `short-classification-invalid`（CLASSIFICATION_INVALID）

封存行是 `registration_valid=true, classification_valid=false`。构造：目标注册**不调用** `authorize_conversation_public_text`、也不带 `recall_item_authority`，于是 `short_horizon_eligible=False`、`has_authorized_public_text=False`、`effective_privacy_class=None`。实测：

- `register_conversation_evidence` **接受**（注册是不可变权威，Memory 不因为缺分类而拒绝持久化）；
- 但投影 SQL 是 `WHERE ... public_text IS NOT NULL`，所以 `projected_chunk_count == 0`，召回 0 条、`no_recall`；
- **成对对照**：同一段文本、同一组结构、带 Host 分类 → 1 chunk、1 条命中，且 `effective_privacy_class` 非空。

即「注册有效、分类无效 ⇒ 保留注册但永不进召回」，与封存行的语义逐字一致。

### 3.8 反例

`tests/test_typed_recall_normal_bridge.py::test_short_real_registration_projection_and_mixed_public_dispatch` 现在断言 9 格全 PASS，并跑 16 个反例：源展开整体缺失、快照条目改成不可见、`registration_hash` 翻一位、`valid_until` +1、被篡改探针改成可见、条目分类降级、到期时钟改成早 2 秒、`E−1s` 对照被清空、注册拒绝缺失、拒绝理由换字、对照被清空、`projected_chunk_count` 改成 1、未授权注册被谎报为已授权、公共 content 混入 canary 值、`cross_scope` 改成 true、`evidence_manifest_hash` 换成全 0 —— 全部必须 FAIL。

## 4. 选择/预算组 → 6 格 PASS

### 4.1 `ranking-order`

封存的 4 个候选带 vector 车道名次，而本 candidate 上 vector 车道公共面不可用（三个 `vector-*` 格本来就 BLOCKED 在这条 reason 上），所以封存的 per-candidate `lane_ranks` **不可复现**。封存 `expected_stable_order = [cand-d, cand-a, cand-b, cand-c]` 真正断言的三件事是可复现的，本轮用四个**真候选**一次性见证：

| 真候选 | 构造 | 分数 |
|---|---|---|
| episode | 标题含 query token 1 次 | `0.30/61` |
| semantic-high | 载荷含 token 2 次（同一 semantic 命名空间 rank 1） | `0.30/61` |
| short chunk | 11 组会话注册后投影 | `0.30/61` |
| semantic-low | 载荷含 token 1 次（同命名空间 rank 2） | `0.30/62` |

四者写入/注册都在同一个注入时钟上（semantic 的 `typed_source_time` 是 `created_at`，episode 是 `occurred_start`，短时域是 `occurred_at`，全部等于 `now`），plan 只开 full_text（entity / task_scope / 时间窗 / vector 全关，oracle 逐项断言），因此每个候选恰好命中一条车道。实测顺序 **[episode, semantic-high, short, semantic-low]**，正好逐条对上封存精度表的前三位：

1. `-rrf_score_12dp`：三个高分候选一律排在 rank-2 的 semantic-low 之前；
2. `source_kind`：分数/车道数/时间全等时，cognitive 在 short 之前；
3. `memory_type_or_empty`：再往下一层，`episode < semantic`。

并且 oracle 把公共事实重新按封存 `stable_tie_break_fields` 排一遍，要求**排序结果与产品发布的顺序逐字相同**（`matched_lane_count` 不是公共字段，在只开一条车道的 plan 上它对每个幸存候选恒为 1，不可能参与决策，这一点写在 `stable_key` 的 docstring 里）。

### 4.2 `dedupe:cross-source-merge` / `dedupe:cross-source-no-merge-evidence-differs`

`rank_candidates` 的窄合并键是 `(public_payload_hash, evidence_manifest_hash)`。要构造「同 payload 同清单」和「同 payload 异清单」两个方向，需要**两个不同的 chunk 载着逐字相同的公共 payload**。0.6.30 的分段投影给了这条公共路：一条超过 2048 码点的注册行会被按段落边界切成多段，每段**各自重新加角色前缀**渲染。于是把注册文本构造成 `P + P`（`P` 长 2042 码点、以 `\n` 结尾），投影出的两条 chunk 内容逐字相同（`"user: " + P`），chunk_id 不同（chunk payload 带段序 `k/K`），证据清单相同（同一条注册）。

| 格 | 构造 | 实测 |
|---|---|---|
| `cross-source-merge` | 1 个因果组 → 2 条同内容 chunk | `projected_chunk_count=2`、`split_group_count=1`、`recall_short_horizon` 返回 2 条同内容不同 chunk_ref 的命中，typed 召回 `filtered_candidate_count=1`、**1 条** item，其 `evidence_manifest_hash == hash(['conversation-evidence-1'])` |
| `cross-source-no-merge-evidence-differs` | 2 个因果组、同一段文本 → 4 条同内容 chunk | `projected_chunk_count=4`、`split_group_count=2`，typed 召回 `filtered_candidate_count=2`、**2 条** item，`public_payload` 哈希相同、`evidence_manifest_hash` 互不相同 |

两格的 `expected_count`（1 / 2）逐字来自封存行，封存行自身的形状（两行 `projection_hashes` 相等、`sorted_evidence_manifests` 一等一不等）也被断言。两个方向只差「证据清单是否相同」这一根轴。

### 4.3 `budget:short-emoji` / `budget:short-escaping`

封存 `canonical_json` 是**前置字面量**（它的 `occurred_at` 是 ISO 字符串，而真实短时域 payload 是 epoch 浮点），与 run-15 对 `budget:greedy` 的处理同源。因此分两步：

1. **封存自洽**：从封存 `canonical_json` 独立重算 utf8 字节数、码点数、`token_estimate = max(1, 码点, ceil(字节/3))`、sha256，四项必须与封存的 `utf8_bytes` / `unicode_codepoints` / `token_estimate` / `canonical_sha256` 逐字相符；`exact_limit.expected == SELECT`、`limit_plus_one.expected == SKIP_WHOLE_ITEM`。
2. **真候选重放同一关系**：把封存的 content（`计划✅🚀` / `a"b\c\n`）真的注册进一条短时域 chunk（前缀一个 query token 使其可检索），未加预算探针一次拿到真实信封字节/码点/token（实测 emoji 133 字节 / 124 码点、escaping 129 字节 / 129 码点），然后：

| 预算 | 实测 |
|---|---|
| `max_bytes = 实测字节`、`max_tokens = 实测 token` | 1 条整条选中，`truncated=false`，`outcome=recall` |
| 字节 −1 | 0 条，`truncated=true`，`no_recall`，`reason_codes=['recall_budget_exhausted']` |
| token −1 | 同上 |

即「恰好等于上限整条选中，任一单位少一个就整条跳过」在真实 emoji / 转义字节上成立。

### 4.4 `budget:deadline`

封存 `ranges.deadline_ms` 允许最小值 1，`deadline_case` 要求 `starts_at=PUBLIC_API_ENTRY`、`at_deadline_outcome=DEADLINE_EXCEEDED`、`payload_count=0`、`durable_terminal_required`、`close_drain_required`。构造：一条**合法**的最小预算请求（`deadline_ms=1`），并用 120 条 padding 证据把请求本身撑大，使 API 入口处的校验/哈希/入账开销确定性地超过 1 毫秒（实测 ~2.4 ms，12/12 稳定）。实测：

| 探针 | 实测 |
|---|---|
| 首次（`deadline_ms=1`） | `TimeoutError: DEADLINE_EXCEEDED`，无任何 payload |
| 同 key 同 body 重放 | 同样 `DEADLINE_EXCEEDED` |
| `close()` + 重开后同 key 同 body | 仍然 `DEADLINE_EXCEEDED`（close drain） |
| 同 key **改 body** | `MemoryIdempotencyConflict: IDEMPOTENCY_CONFLICT`（超时请求是**持久记录**，不是一次瞬时失败） |
| 对照：新 key、`deadline_ms=2000`（封存 `public_deadline_ms`） | 真召回，1 条 item |

对照的存在是关键：没有它，拒绝可以被归因到库/请求本身；有了它，`DEADLINE_EXCEEDED` 只能归因到 deadline 这一根轴。

**附带发现（本轮不作为该格判据，列入 §10 后继）**：当 1 毫秒预算恰好落在**候选收集阶段**（`asyncio.wait_for` 取消一个已经开着 SQLite 读事务的抑制解析）时，0.6.31 会抛
`sqlite3.OperationalError: cannot start a transaction within a transaction`
而不是契约要求的 `DEADLINE_EXCEEDED`，并且那一次不会落下 deadline 终态。这是候选侧的真实健壮性缺陷，命中与否取决于机器负载（12 次采样命中 5 次）。本格采用的构造把预算确定性地耗在**入口/入账阶段**（永不进入那个窗口），因此判据稳定；缺陷本身记录在此并建议单独立格/报 SDK，不在本轮把一个不确定的竞态写进 401 判定。

### 4.5 反例

`tests/test_typed_recall_selection_public.py` 新增 26 个反例：ranking-order 5（换序、分数改相等、多开一条车道、时间被移动、少一个候选）、dedupe 每格 5（条数、chunk 内容漂移、chunk 数、清单哈希、payload）、budget short 每格 5（恰好上限被跳过、少一个单位仍被选中、实测单位被改、上限被放宽、探针内容被换）、deadline 7（首次变成成功、理由换字、重开后变成成功、close 标记缺失、改 body 不再冲突、对照空、tiny 预算被写成 2000）——全部必须 FAIL。

## 5. `epistemic:procedure:observed_behavior:repeated_observation` → 1 格 PASS

封存期望是 `ELIGIBLE_WITH_APPLICABILITY`，而 Memory 在 create 上**永不**激活非 explicit epistemic 的 Procedure（`mutations._validate_epistemic_provenance`），公共面唯一可构造的初始状态是 draft。run-15 因此挂 `OBSERVED_PROCEDURE_ACTIVATION_PROMOTION_PATH_NOT_EXECUTED`。本轮把真实晋升路径跑通。

**晋升配方（全部为公共调用）**：在既有 applicability 快照之后，连做 3 次 `record_procedure_observation`（`kind=terminal_outcome`、`outcome=success`、`attributable=true`、`risk=low`、`hazard=none`），每次：

1. admit 两条证据：`…-call-N`（`source_kind=user_message`）与 `…-evidence-N`（**`source_kind=tool_result`**，Harness 把会话 role 与证据 source_kind 成对校验，为此给 `CaseManager.evidence` 加了显式的 `source_kind` 入参）；
2. 把两条证据注册成**一个完整的两项因果组**：item 1 role=user，item 2 role=tool 且携带 `ConversationToolCausalLink(parent_item_ordinal=1, terminal_receipt_id, terminal_receipt_hash)`——Memory 的 `_verify_procedure_evidence_unlocked` 要求终端回执必须由一条注册在**同一 task scope、同一 run** 下的 tool 项承载；
3. 每次观察用**各自独立的 task scope**（`procedure_observations` 上有 `UNIQUE(memory_id, qualification_epoch, task_scope_id)`）与各自的终端回执 id；
4. Host 在 intent 里预告转移，Memory 用自己算出的阈值逐条复核（必须相等）：

| 次序 | transition | Memory 报告的 `independent_successes` | `reason_code` |
|---:|---|---:|---|
| 1 | draft → draft | 1 | `procedure_low_risk_success` |
| 2 | draft → eligible_for_activation | 2 | `procedure_low_risk_success` |
| 3 | eligible_for_activation → **active** | 3 | `procedure_low_risk_success` |

第 3 次之后 head 是 active，同一次公共召回真的返回该 Procedure（`source_revision` == 最后一次提交的修订号，实测 5 = 1 create + 1 快照 + 3 观察）。

oracle（`runners/typed_recall_procedure_oracle.py::check_promotion`）逐条比对：intent 全字段（含 `applicability_fingerprint`、`evidence_span_hash`、转移对、观察时钟）、authority 的签发/过期/`intent_hash`/`authority_hash`/引用承诺、两项因果组的 role/task scope/run/tool 因果链、result 与其重放逐字相同且 `result_hash` 等于独立重算的域哈希、修订链 `base→committed` 连续、三次的 scope 与终端回执互不相同、最终生命周期必须是 `active`。召回侧的 `evidence_refs` 也相应扩展成「生命周期证据 + applicability 证据 + 6 条观察证据」的完整有序清单（`assess_normal` 原来的「召回必须绑定完整证据历史」断言一字未放松，只是把 procedure 侧的引用从单条改成有序多条）。

反例（`tests/test_typed_recall_admission_oracles.py`）：删掉最后一次观察、把第三次的生命周期改成 draft、把第二次的成功计数改成 3、让两次观察共用同一个 task scope、把 tool 项的因果链清空——全部必须 FAIL。

## 6. 两个 exact-dedupe 格改判为「公共契约上不可构造」

`ranking_oracle.dedupe_cases` 的前两行要求**两个 exact_key 相同的候选**合并成一条。从冻结契约读出的事实：

- `RecallCandidate.exact_key` 对认知候选是 `(source_kind, source_ref, source_revision)`，对短时域候选是 `(source_kind, chunk_ref, source_content_hash)`（`M core/recall.py`）；
- 认知收集器按 head 表逐行走，每个 `(memory_id, current_revision)` 只产出**一个**候选，车道在产出之前就已合并进 `lane_ranks`；短时域收集器把 eligible 行按 chunk_id 建索引，一条 chunk 只产出一个候选（`M backends/sqlite_v5.py`）；
- 两个收集器的键空间因 `source_kind` 天然不相交。

所以「两个 exact_key 相同的候选」在公共面不可能同时存在——这不是「执行器还没写」，与 run-15 §4 的四个 tie 格是同一类问题。两格改判为 `CONTRACT_FACT_UNPAIRABLE`，reason 前缀新增 `PUBLIC_DUPLICATE_NOT_CONSTRUCTIBLE:`（bridge 的类别映射同时接受这两个前缀）。**exact 键之上的窄跨源合并是公共可达的，并且已经被 §4.2 的两格真的执行了**——两条 reason 里都写明了这一点，避免把「不可构造」读成「没覆盖」。

工程落地与 run-15 相同：常量放在 selection oracle（与可达性定理同文件），bridge 只在**判定合并之后**替换「已经是 BLOCKED 且 reason 以 `CELL_EXECUTOR_NOT_IMPLEMENTED` 开头」的格，永远不可能把一格变成 PASS 或 FAIL。`tests/test_typed_recall_bridge.py` 里的不可配对断言同步扩到 6 格，并额外断言这两格与两个 `dedupe:cross-source-*` 格分属「不可构造」与「真执行」两侧。

## 7. 正式扫描（run-16）

### 7.1 命令

消费者 venv（`.local-test-evidence/2026-09-08/venv-m0631`）与 SDK 源 worktree（`simple-harness-memory-sdk-0631-source`，HEAD `ff8be5f3…`）沿用 RUN-06 §2.1/2.2，未重建、未改 pin。runner 自回归（run-16 之前）：

```bash
TYPED_RECALL_SOURCE_CHECKOUT=/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk-0631-source \
/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/.venv/bin/python -m pytest \
  <worktree>/testcase/human-memory-program/tests -q -p no:cacheprovider
# 120 passed, 31 subtests passed（run-15 前为 119 passed；本轮新增 1 个用例并大幅扩充既有用例的反例，未删任何用例）
```

正式扫描与 RUN-06 §2.3 逐字相同，仅 `--artifact-dir` 改为 `.../typed-recall-401/run-16-m0631-short-selection`，输出到 `run-16.log` / `run-16.stderr`；artifact 目录执行前不存在；**不传 `--consumer-entrypoint`**。退出码 3，stderr 为空。

### 7.2 结果

| 项 | 值 |
|---|---|
| artifact | `.local-test-evidence/2026-09-08/typed-recall-401/run-16-m0631-short-selection/`（585 MB） |
| `run_id` | `51501db56ec0401ab8fa54106f6b5729`（2026-09-08T18:16:26.341Z → 18:17:02.453Z，36 s） |
| 候选身份 | Harness 0.7.10 / `031fdc68…` / `e559bc1b…`；Memory 0.6.31 / `ff8be5f3…` / `e8dc27cc…`（`candidate_pin_differences=[]`） |
| 隔离 | `isolated-process-verified-installed-wheels`；`oracle_blockers=[]` |
| 整体 | `NOT_RUN/BLOCKED`；`acceptance_counts` **PASS 382 / FAIL 0 / BLOCKED 19** |
| 层 | public：passed 372 / failed 0 / blocked 19；source：passed 10 / failed 0 |
| `fixture_sha256` / `execution_layers_sha256` | `d63b0bb6…` / `101c14ba…`（**与 run-15 逐字相同**） |
| `oracle_code_sha256`（a2） | `d0d379aa1fe3124d0967bdf4582d9174b08625c9f2e3b351a0c9bb20f69f449c`（run-15 `0ff6282d…`） |
| bridge-summary / public-observations / source-observations sha256 | `5d3ace1d…` / `be5348c8…` / `7e944fa3…` |
| 变更执行代码（sha256 前 8 位） | `adapters/typed_recall_case_manager.py` `d4aba4e8…`、`typed_recall_short_cases.py` `2a320e7f…`、`typed_recall_selection_cases.py` `05a1c227…`、`typed_recall_procedure_cases.py` `a1c24f0f…`；`runners/typed_recall_a2_oracle.py` `d0d379aa…`、`typed_recall_selection_oracle.py` `ee21881d…`、`typed_recall_procedure_oracle.py` `f08f3a35…`、`typed_recall_normal_inputs.py` `8fb043f0…`（`runners/typed_recall_bridge.py` 与 `runners/run_typed_recall_public_consumer.py` 亦有改动——前者只加了 §6 的 reason 前缀映射，后者是 §2 的 `--self-check` 修复——它们是父进程自身的编排/自检代码，不在 `execution_code_sha256` / `validation_code_sha256` 两张表里；其行为由 `tests/test_typed_recall_bridge.py` 覆盖） |

## 8. 逐格对照 run-15 → run-16

**18 格变化；其余 383 格状态与 reason 逐字相同。**

### 8.1 转 PASS 的 16 格

| 组 | 格数 | 格 ID |
|---|---:|---|
| 短时域链路 | 6 | `eligibility/{short-chain-complete,short-expiry-equals-now,short-future,short-source-suppressed}`、`protocol/{mixed-long-short,short-only}` |
| 短时域链路负例 | 2 | `eligibility/{short-registration-invalid,short-classification-invalid}` |
| 短时域最小投影 | 1 | `selection-budget/projection:short_horizon` |
| 排序与去重 | 3 | `selection-budget/{ranking-order,dedupe:cross-source-merge,dedupe:cross-source-no-merge-evidence-differs}` |
| 预算 | 3 | `selection-budget/{budget:short-emoji,budget:short-escaping,budget:deadline}` |
| 程序观察晋升 | 1 | `eligibility/epistemic:procedure:observed_behavior:repeated_observation` |

### 8.2 维持 BLOCKED 但改判的 2 格

| 格 | run-15 类别 / 理由前缀 | run-16 类别 / 理由前缀 | 依据 |
|---|---|---|---|
| `selection-budget/dedupe:exact-cognitive` | EXECUTOR_UNIMPLEMENTED / `CELL_EXECUTOR_NOT_IMPLEMENTED` | **CONTRACT_FACT_UNPAIRABLE** / `PUBLIC_DUPLICATE_NOT_CONSTRUCTIBLE:` | §6 |
| `selection-budget/dedupe:exact-short` | 同上 | 同上 | §6 |

原始观测层与 RUN-06 §4.3 同理：每轮随机 init authority_ref 与 rejection UUID 带来 hash 级差异，oracle 只看 hash 间关系。本轮所有判定变化都可归因到 §2–§6 列出的 runner/adapter 改动，**没有一条 fixture 改动**。

## 9. 剩余 19 格 BLOCKED

| 类别 | 格数 | 内容 |
|---|---:|---|
| `ORACLE_GAP` | 8 | conflict 精确 reason 3（`contest-{active-group-exists,evidence-not-distinct,same-content}`）、executed-lane 3（`vector-{not-requested,only-unavailable,unavailable-full-text-survives}`）、context-use exact-once 2（`context:{duplicate-same-provider-attempt,receipt-first}`） |
| `CONTRACT_FACT_UNPAIRABLE` | 6 | 四个 tie 格（run-15 §4.3）+ 两个 exact-dedupe 格（§6） |
| `DUPLICATE_UNTESTABLE` | 3 | `contest-{nested,one-member,three-members}` |
| `SDK_INCREMENT_REQUIRED` | 2 | `authority:policy_hash_change`、`authority:short_source_cleanup`（SDK 仓另有代理处理） |

本轮复核了这 8 个 `ORACLE_GAP` 在 0.6.31 公共面上的见证情况，结论与 run-08 §7(c)(d) 一致，仍需 Memory/Harness 增量：

- **conflict 精确 reason 3**：`apply_memory_mutation_plan` 的 `reason_code` 是通用的 `memory_mutation_validation_rejected`，`ACTIVE_CONFLICT_EXISTS` / `DISTINCT_EVIDENCE_REQUIRED` / `DISTINCT_CONTENT_REQUIRED` 没有任何公共出口；
- **executed-lane 3**：执行回执只报 `degradation_codes`，不报「本次实际执行了哪些车道」，封存的 `expected_executed_lanes` 无公共对应字段；
- **context-use exact-once 2**：预留是 Harness 侧的一次性占用，Memory 公共面看不到「预留—消费」这一对事件。

## 10. 后继（followup 清单）

1. **1 毫秒预算落在候选收集阶段时抛 `sqlite3.OperationalError` 而不是 `DEADLINE_EXCEEDED`**（§4.4 附带发现）：候选侧真实缺陷，且那一次不落 deadline 终态。建议报 SDK 并考虑单独立格（立格要先走「格集合扩张规则」，见第 4 条）。
2. **conflict 精确 reason 3 格、executed-lane 3 格、context-use exact-once 2 格**：保持提案态，等 Memory/Harness 增量（公共 reason code、executed lanes 回执、用途预留事件）。
3. **`authority:policy_hash_change` / `authority:short_source_cleanup`**：SDK 仓另有代理负责。
4. **格集合扩张规则**：run-09 §6 的裁决仍然有效——0.6.31 争议短路负例、0.6.29 用途围栏正例、以及本轮的 deadline 缺陷格，都需要先有一套显式的「加格」封存规则才能进 401。
5. **bridge 层级 PASS 规则仍缺失**：任何一层永远停在 `NOT_RUN/BLOCKED`；19 格未清零前不影响结论。清零后需要先裁决层级 PASS 的判据。
6. **短时域 TTL 常量**：`SHORT_RETENTION_SECONDS = 5 天` 目前是 oracle 侧引用的产品常量（§3.3）。若将来 Memory 把它做成可配置项，这条断言应改成从公共配置读回。

## 11. 边界遵守

未改 `ARCHITECTURE/`、`backend/`、Host pin、桌面应用；未运行 18120 端口；全程单一 runner/pytest 进程。API key 未打印、未提交。所有改动限于 `testcase/human-memory-program/` 与本记录。封存 fixture 与 layers 两个文件本轮**零改动**（`git status` 中不出现），因此不涉及 rev bump、谱系条目与段落密封迁移；`test_0613_successor_retains_original_obligations_and_candidate_lineage` 与 `test_typed_recall_fixture_authorities.py` 全绿。
