# 401 矩阵 run-08 §7 后继落地（run-15，M0.6.31 pin 不变，fixture 未动）

> 2026-09-09。分支 `worktree-matrix-401-r3`（Host main `9877bf5e`）。
> 对照基线：run-14（`.local-test-evidence/2026-09-08/typed-recall-401/run-14-m0631-adjudication/`，`TYPED-RECALL-401-RUN-08.md`）。
> 用户授权：技术取舍由执行者作为独立裁决人从契约文本裁定、记录并实施，不回问。
> 本轮**没有动封存 fixture**：`fixture_revision` 仍为 17，fixture sha256、layers sha256、`fixture_change_lineage`、`layers_change_lineage`、`approved_oracle.unchanged_sections_sha256` 全部逐字未变。所有改动都在 runner/adapter/tests 三处执行代码上。

## 0. 一句话结论

**PASS 355 → 366 / FAIL 0 → 0 / BLOCKED 46 → 35。** 15 格发生变化：**11 格转 PASS**（H 组投影 4、分页零候选读 5、I-3 continuation 1、tie-score 1），**4 格保持 BLOCKED 但从「执行器没写」改判为「公共契约上不可配对」**并归入新类别 `CONTRACT_FACT_UNPAIRABLE`。其余 **386 格状态与 reason 逐字相同**，没有任何一格由 PASS 退化，没有放松任何封存断言。runner 退出码仍为 3（整体 `NOT_RUN/BLOCKED`，35 格未闭合，属预期）。

| 阻塞类别 | run-14 | run-15 | 变化 |
|---|---:|---:|---:|
| `ORACLE_GAP` | 19 | **14** | −5（5 个分页格转 PASS） |
| `EXECUTOR_UNIMPLEMENTED` | 18 | **12** | −6（I-3、tie-score 转 PASS，4 个 tie 格改判类别） |
| `FIXTURE_INVALID_OR_INSUFFICIENT` | 4 | **0** | −4（H 组投影全部转 PASS） |
| `SDK_INCREMENT_REQUIRED` | 2 | **2** | 不变（I-1 / I-2，SDK 侧另有代理处理） |
| `DUPLICATE_UNTESTABLE` | 3 | **3** | 不变（F 组 contest 三格） |
| `CONTRACT_FACT_UNPAIRABLE`（**新类**） | — | **4** | +4（见 §4） |
| 合计 BLOCKED | 46 | **35** | −11 |

按 lane（PASS / BLOCKED）：conflict-state 14/6；current-use 13/4；eligibility 294/7；fault-recovery 7/0；protocol 9/2；selection-budget 10/16；unsupported-replay 19/0。

## 1. 本轮处理的 run-08 §7 条目

| §7 条目 | 处置 | 结果 |
|---|---|---|
| 1. H 组投影接线（4 格） | 按 §2.H 配方接线，**未改 fixture** | 4 格 PASS，见 §2 |
| 2. I-3 `context:new-continuation` 接线（1 格） | 接线，且比原配方更强（补成对对照） | 1 格 PASS，见 §3 |
| 3. 分页零候选读见证（4 格） | 用「压制底层来源后分页字节不动」的间接见证，实际覆盖 **5 格**（含 `naked-source-ref`） | 5 格 PASS，见 §5 |
| 4. SDK 增量提案 | **跳过**（SDK 仓另有代理负责） | I-1 / I-2 维持 `SDK_INCREMENT_REQUIRED` |
| 5. `tie-score` / `tie-matched-lane-count` 接线 + 三格不可配对裁决 | `tie-score` 接线 PASS；`tie-matched-lane-count` **经裁决并入不可配对组**（与预估的 +2 不同，理由见 §4） | 1 格 PASS + 4 格改判 |
| 6. 矩阵覆盖缺口（0.6.31 争议短路负例 / 0.6.29 用途围栏正例） | 按封存规则**不允许加格**，只记录 | 见 §6 |
| 7. bridge 层级 PASS 规则 | 未变（35 格未清零前不影响结论） | — |

## 2. H 组 `selection-budget/projection:{semantic,episode,procedure,prospective}` → 4 格 PASS

裁决沿用 run-14 §2.H 的 (a)：**不改 fixture，只接线**。落地方式：

### 2.1 封存 canary 分两半，两半都被真见证

封存 `minimal_projection_oracle[*].source_record` 里不在 `allowed_payload_fields` 的六个键——`source_ref` / `evidence_ids` / `classification` / `conflict_status` / `cross_scope` / `extra_typed_field`——现在全部作为**构造输入**进入 recipe（`runners/typed_recall_normal_inputs.py`，新增 `projection_canary` 与 `conflict_status`，`privacy` 改为从封存 `classification` 推出而不是硬写 `'SENSITIVE'`）。

- **可植入的三个**真的落进库里：`evidence_ids` 的 `secret-evidence` 是**真被 admit 的证据 id**（adapter 用它作 `evidence_id` 种记忆）；`classification` 就是请求的 privacy class；`conflict_status` 就是 mutation 的实参（semantic 那格封存写的是 `resolved`，Memory 在 create 上确实接受，实测通过）。义务是这三者一个字节都不得出现在 `public_payload` 里。
- **严格 DTO 不可表示的三个**用**拒绝**见证：adapter 对 `SemanticMemoryPayload` / `EpisodeMemoryPayload` / `ProcedureMemoryPayload` / `ProspectiveMemoryPayload` 逐个做 `from_json({**wire, <canary key>: <canary value>})`，oracle 要求逐字命中
  `"<Class> fields differ; missing=[], extra=['<key>']"`，并且干净 wire 必须被接受（对照）。「必须被剥掉」在本契约上以**更强**形式成立：连进都进不来。
- `cross_scope` 同时两面钉死：`'cross_scope' not in public_payload` 且 `item['cross_scope'] is False`、`item['source_task_scope_ids'] == []`——公共契约把它放在 **item 层**而非 payload 层。

### 2.2 投影字节回绑封存 payload/payload_hash，而不是重新封存哈希

oracle 不再拿 recipe 的 payload 比对，而是：

1. `sha256(canonical(sealed['payload'])) == sealed['payload_hash']`（封存自洽性，本轮实测四型全为真）；
2. `set(sealed['payload']) == set(sealed['allowed_payload_fields'])`；
3. `item['public_payload'] == normal_projection({'memory_type': kind, 'payload': sealed['payload']})`。

`normal_projection()` 就是已声明的**抽象→公共**映射（episode `occurred_interval` → `occurred_start/occurred_end`、procedure `applicability` dict → `tool@version`、prospective `trigger` → typed trigger、semantic `qualifiers` → 有序容器）。因此 run-14 记的 `ORIGINAL_EPISODE_OCCURRED_INTERVAL_DIFFERS_FROM_PUBLIC_PROJECTION` 不再是阻塞，而是被这条映射显式承担并双向钉死；公共键集也由该映射的键集直接得出，等价于「恰好等于 `allowed_payload_fields` 的公共对应」。

4. 另加逐值扫描：canary 的所有字符串叶子（`mem-episode` / `secret-evidence` / `SENSITIVE` / `uncontested`、`resolved` / `must-strip`）都不得出现在 `canonical(public_payload)` 中；
5. `item['evidence_manifest_hash'] == hash_json(sorted(['secret-evidence']))`——被禁 id 只以哈希形式可达；
6. `item['effective_privacy_class'] == 'sensitive'`。

### 2.3 procedure / prospective 的前置权威

`projection` 家族加入 `typed_recall_procedure_cases.supported()` 与 `typed_recall_prospective_cases.supported()` 的 family 白名单（对应 oracle 的 scope 检查同步放行），复用既有 applicability 快照 / registration ack 适配器。因此 run-14 的 `REQUIRED_APPLICABILITY_OR_SIGNAL_AUTHORITY_NOT_ESTABLISHED` 自然消失——不是被忽略，而是权威真的建立了。

### 2.4 反例

`tests/test_typed_recall_normal_bridge.py::test_minimal_projection_canary_is_planted_and_never_leaks`（4 型参数化）对每一型跑 8 个反例：payload 混入 canary 键、payload 值被替换成 canary 值、payload 任一字段被改、DTO 探针变成「被接受」、探针整体缺失、`cross_scope` 变 true、植入证据 id 被换、classification 被降级——全部必须 FAIL。

## 3. `current-use/context:new-continuation` → 1 格 PASS

run-14 §2.I-3 已推翻 run-07 的「无对应公共字段」，裁定 continuation 轴 = 用途请求的 `turn_id`（`H runtime/kernel.py:1284 turn_id=continuation_id`、`:1299 context_use_turn_id`）。本轮接线时实测发现，公共契约给出的答案比原配方**更强**，于是按实测事实接线：

- 原配方设想「换 turn ⇒ 签发一张新收据」。实测：`authorize_recall_context_use` 在进入 epoch 围栏之前就比较 `typed_request.turn_id` 与**存储的召回 context 的 turn**（`M backends/sqlite_v5.py`，`typed_recall_context_use_invocation_binding_invalid`）。也就是说，旧结果**根本不能在另一个 continuation 里被使用**——这正是 `NEW_AUTHORIZATION_REQUIRED` 的更强形式：必须重新召回，而不是重新签发。

因此接线成**三次真实公共操作 + 成对对照**（同一个库、同一个时钟、同一份 recall 结果，只动 continuation 一根轴）：

| 操作 | provider attempt | context-use turn | 实测结果 |
|---|---|---|---|
| `same_attempt` | 封存的 `attempt-1`（已被首次用途占用） | `continuation-2` | `MemoryIdempotencyConflict: RECALL_CONTEXT_USE_IDEMPOTENCY_CONFLICT`，无收据 |
| `fresh_attempt` | 全新 `attempt-1-continuation-probe` | `continuation-2` | `MemoryValidationError: typed_recall_context_use_invocation_binding_invalid`，无收据 |
| `control`（对照） | 全新 `attempt-1-continuation-control` | **原 turn** | 真收据，`receipt_id` 与首张不同，`result_hash` / `item_bindings` 与首张逐字相同 |

对照的存在是关键：没有它，`same_attempt` 的拒绝可以被归因到「同 attempt 的用途预留」（`wrong-snapshot` 格已经证过那条），而不是 continuation 轴。加上 `fresh_attempt` + `control`，「0 收据」只能归因到 continuation。此外首张收据的 `validate_request(changed)` 必须以 `ValueError: receipt request_hash differs` 拒绝。

oracle 侧按 §7 要求给 `check_bundle` 加了**显式开关** `use_turn_id`：只有 continuation 格的三个 probe 允许 `request['turn_id'] != recall context turn`，且必须**恰好等于**封存的 `continuation-2`，其余四个 context-use 格保持严格相等，一个断言都没有放松。

配套：`inputs()` 新增两个**构造输入** `continuation_control_attempt` / `continuation_probe_attempt`（由封存 `provider_attempt` 派生），executor 版本 2 → 3（`assess_context_use` 的路由改为 `>= 2`，版本 1 的旧产品环形状不受影响）。`expected_receipt_hash` 保持原值不动（legacy 字面量，现有 context-use oracle 本来就不比对）。

**重复格修复**：`current-use/context:new-continuation` 原先被写在 `typed_recall_authority_event_cases.NO_PUBLIC_INPUT` 里（作为「执行器未实现」的占位）。接线后两个 adapter 同时声明同一格，bridge 直接判 `duplicate, unknown or wrong-layer cell` 并让整层 FAIL（本轮第一次扫描就是这么失败的）。已从 authority-event adapter 移除，测试里加了 `assertNotIn` 保证不会再被写回。

反例：`tests/test_typed_recall_context_use_bridge.py` 新增 8 个——`same_attempt` 变成被接受、`fresh_attempt` 换成别的拒绝码、对照被拒、对照的 turn 被改、probe 的 turn 改回原 turn、对照与 probe 共用同一 attempt、首张收据的拒绝理由被换、probe 整个缺失——全部必须 FAIL。

## 4. 四个 tie 格：`tie-score` 接线 PASS，其余四格裁决为「公共契约上不可配对」

### 4.1 先给判据：公共可打平性定理

从冻结契约文本读出的两条事实（写进 `runners/typed_recall_selection_oracle.py` 模块 docstring，附出处）：

1. **名次表按命名空间分片**。认知收集器按 `(memory_type, lane)` 排名（`M backends/sqlite_v5.py::_collect_typed_recall_candidates`），短时域收集器有自己的一张表（`::_collect_typed_recall_short_candidates`）。**同一命名空间内的两个候选，在任何共有车道上名次必然不同**，因而 RRF 分数必然不同。所以两个候选要在 `-rrf_score_12dp` 上打平，只能落在不同命名空间：要么 `source_kind` 不同，要么（同为认知）`memory_type` 不同。而这两个字段在封存 `stable_tie_break_fields` 里都**排在 `source_ref` / `source_revision_or_zero` 之上**。
2. **除 full_text / vector 外，每条车道都是硬过滤**。不匹配 entity 约束 / task scope / 时间窗的候选是被 `continue` 丢掉，而不是「只是没进这条车道」。因此两个幸存候选的 entity/task_scope/temporal 车道集合恒等，`matched_lane_count` 只能靠 full_text 一条车道产生差异——而 full_text 的权重 0.30/(60+rank) 同时会改变分数。本 candidate 上 vector 车道不可用（三个 `vector-*` 格本来就 BLOCKED 在 `PUBLIC_EXECUTED_LANE_WITNESS_UNAVAILABLE`）。

### 4.2 `tie-score` → PASS（接线）

封存 `tie-score` 根本不是「打平」，而是「**分数高的赢，哪怕对方车道更多、时间更新**」：`score-high`（0.02 / 2 车道 / 10:00Z）在 `score-low`（0.01 / 9 车道 / 11:00Z）之前。按 §4.1，`matched_lane_count` 那一半在公共面无法反转；能反转的最强字段是 `-typed_source_time`，于是构造：

- 同一个库两条**认知 episode**（同 memory_type ⇒ 同一命名空间）：标题含 query token 两次的为 `high`（lexical 2），含一次的为 `low`（lexical 1）；
- 因此 full_text 名次 1 / 2，分数为独立计算的 `0.30/61 = 0.004918032787` 与 `0.30/62 = 0.004838709677`（实测逐字相符）；
- plan 不开 entity / task_scope / 时间窗 / vector / short（oracle 逐项断言），所以两者的 `matched_lane_count` 都恰为 1，被测字段被隔离；
- 变体 `low-newer`：低分那条的 `occurred_start` **严格更新**（用封存行自己的两个 `typed_source_time`）；变体 `high-newer`：把这对时间反过来跑一遍作为对照。
- 两个方向都必须是高分在前。`low-newer` 里「更新的低分候选仍然排第二」就是 `-rrf_score_12dp` 压过 `-typed_source_time` 的见证。

反例（`tests/test_typed_recall_selection_public.py` 新增 5 个）：顺序反转、两分数被改成相等、plan 多开一条车道、两时间被改成相等、replay 被改成未重放——全部必须 FAIL。

### 4.3 四格改判为 `CONTRACT_FACT_UNPAIRABLE`（含原三格 + `tie-matched-lane-count`）

run-08 §7 第 5 条预估 `tie-matched-lane-count` 可与 `tie-score` 一起 +2。**本轮裁决推翻这个预估**：按 §4.1 第 2 条，「分数相等 + 车道数不等」在公共面无法构造。唯一能让两个幸存候选车道数不同的是 full_text 的有无，而它带 0.30 的权重；要用名次差补回 0.30 需要 `60+rank ≈ 7576`，远超本 candidate 的名次上限（`min(128, max(32, 8*max_items))`）与实际候选数。因此它与另外三格是同一类问题，不是「执行器还没写」。

| 格 | 为什么不可配对（reason 前缀 `PUBLIC_TIE_NOT_CONSTRUCTIBLE:`） |
|---|---|
| `tie-matched-lane-count` | 分数相等且车道数不等无法构造：除 full_text/vector 外每条车道都是过滤器，幸存者车道集合恒等；唯一差异源 full_text 同时改变分数，vector 在本 candidate 不可用 |
| `tie-memory-type-empty` | `memory_type_or_empty` 只有在 `source_kind` 打平（两个都是 `cognitive_memory`）时才会被读到，而认知候选**永远**带着它的 head `memory_type`；只有短时域候选投影出 `null`，而 `source_kind` 在它之前就决定了顺序。封存那行在公共契约上是**空的** |
| `tie-source-ref` | `source_ref` 只有在 `source_kind` **和** `memory_type` 都打平时才被读到，而那意味着两个候选落在同一个认知排名命名空间——按 §4.1 第 1 条，那里分数必然不同，封存需要的打平不可能存在 |
| `tie-source-revision-or-zero` | 更下一层：还要 `source_ref` 也打平，在公共契约上就是同一条当前 head；两个不同候选永远到不了这个字段 |

工程落地：`UNPAIRABLE_TIE_CELLS` 常量放在 selection oracle 里（与定理同文件），bridge 在**判定合并之后**才用它替换 reason，并且**只替换已经是 BLOCKED 且 reason 以 `CELL_EXECUTOR_NOT_IMPLEMENTED` 开头的格**——它永远不可能把一格变成 PASS 或 FAIL，也不会覆盖任何执行器真正观测过的格。新增 blocker 类别 `CONTRACT_FACT_UNPAIRABLE`，与「oracle 还没写」「执行器没写」「SDK 缺能力」「正向不变式的重复」分开记账。

反例：`tests/test_typed_recall_bridge.py::test_two_layer_dispatch_retains_exact_inventory_and_failures` 里断言这四格在真实 bridge 汇总里 status=BLOCKED、reason 逐字等于常量、`blocker_categories == ['CONTRACT_FACT_UNPAIRABLE']`，且这四格与 selection 执行器的 `CELLS` 不相交。

## 5. 分页零候选读见证 → 5 格 PASS

封存 `result_page_cases[*]` 与 `source_binding_cases[naked-source-ref]` 都带 `candidate_query_started: false`。run-14 判为 `ORACLE_GAP`：`TypedRecallRejectionV1`（逐次候选读计数）只由 `execute_typed_recall` 挂载，分页缝上没有计数器。

本轮走 §7 第 3 条给的 oracle 侧方案，并且做成**比计数器更强**的见证：

> `page_typed_recall_result` 完全由 durable 的 `typed_recall_results` 行服务（`M backends/sqlite_v5.py:4562+`：读 result 行 → 校验 hash/时限 → 从 `result.items` 生成 binding）。如果它读候选，那么当候选层的答案改变时，它的答案必须跟着变。

于是在同一个库里执行（`adapters/typed_recall_return_cases.py::page_zero_read_witness`）：

1. `control_before`：真跑一次召回 → 1 条命中，`candidate_query_started=true`、`candidate_query_count=1`；
2. 真压制那条被绑定的记忆（`SuppressionRequest(scope=MEMORY, reason=user_forget)`）；
3. `control_after`：同一次真召回 → **0 条**，`outcome=no_recall`，同样 `candidate_query_started=true`、`candidate_query_count=1`（候选层的答案确实从 1 变成了 0）；
4. 把本批**每一次**分页调用按各自的时钟（`page-expired-result` 用它自己的 `use_at`）**逐字重放**。

oracle（`runners/typed_recall_a2_oracle.py::check_page_zero_read`）要求：durable result 绑定的正是被压制的那条记忆；压制请求/裁决的 scope 与目标逐字正确；两个对照的候选读计数与命中数如上；**重放结果与压制前逐字相同**——成功页（含 `byte_count`、`bindings`、`page_id`）、预算拒绝、坐标拒绝、绑定拒绝、过期拒绝一视同仁。一个字节都没动 ⇒ 分页不可能读过候选。同时保留封存 `payload_count` 与实际页内容的一致性断言。

覆盖 5 格（比 §7 估的 4 格多一格 `naked-source-ref`，它同属这条 reason，且其 `TypeError` 发生在任何 DB 访问之前，重放同样逐字相同）：`page-correct-binding`、`page-wrong-result-hash`、`page-wrong-coordinate`、`page-expired-result`、`naked-source-ref`。

反例：`tests/test_typed_recall_admission_oracles.py` 对这 5 格各跑 5 个——见证整体缺失、重放字节被改一个（成功页 `byte_count+1` / 拒绝理由换字）、压制后对照仍返回旧结果、对照的 `candidate_query_started` 被改成 false、压制目标被换成别的 memory_id——共 25 个必须 FAIL。

## 6. 矩阵覆盖缺口（run-08 §7 第 6 条）→ 裁决为**不允许加格**，只记录

检查了封存修订规则的可执行实现（`tests/test_typed_recall_bridge.py::test_0613_successor_retains_original_obligations_and_candidate_lineage` 与 `runners/run_typed_recall_public_consumer.py::_validate_execution_layers`）：

- `fixture_change_lineage` 的语义**只有「在已存在的行里替换某个 field_path 的值」**。被谱系点名的段落必须满足 `set(old_rows) == set(new_rows)` 且行数不变；未被点名的段落必须与基线提交 `60f280dc` **逐字相等**。两条路都不允许新增行。
- layers 侧 `all_cells`（`count: 401` + `sorted_lane_cell_ids_sha256`）被断言与基线**逐字相等**，`combined_gate.required_exact_union_count/sha256` 同理。而 401 格集合完全由 fixture 的行派生（`_expected_product_cells`），加一行必然改这两个密封。

结论：**0.6.31 争议短路负例格、0.6.29 用途围栏正例格（与 FCL-002 配套）在当前封存规则下不能作为新格加入。**只记录，不加。将来若要加，需要的是一次显式的「格集合扩张」规则（新的 lineage 种类 + `all_cells` 迁移条目 + 双向反例），那是独立于本轮的规则变更，应单独裁决。

这条裁决本身也做成了可执行断言：新增 `tests/test_typed_recall_bridge.py::test_sealed_rules_forbid_introducing_a_new_cell`，分别往 `result_page_cases` 与 `context_use_cases` 追加一行（就用这两个缺口格的名字），断言 layers 路由 oracle 报出 `all cell count mismatch` / `all sorted cell-id hash mismatch` / `combined exact union count mismatch` / `combined exact union hash mismatch` 四条错误。

**顺带发现（不在本轮修）**：`_validate_execution_layers` 里 `layers.get("fixture_revision") not in (1, 2, 3, 4, 5)` 是一条过时白名单——layers 现在是 rev 15，所以 `--self-check` 路径今天必然 FAIL。401 正式扫描不走这条路径（RUN-04 起就不传 `--consumer-entrypoint`、不用 `--self-check`），所以对本轮结论无影响。上面那个新测试用「错误集合差」而不是 status 来判定，正是为了不把这条陈旧断言写死。列入 §8 后继。

## 7. 正式扫描（run-15）

### 7.1 命令

消费者 venv（`.local-test-evidence/2026-09-08/venv-m0631`）与 SDK 源 worktree（`simple-harness-memory-sdk-0631-source`，HEAD `ff8be5f3…`）沿用 RUN-06 §2.1/2.2，未重建、未改 pin。runner 自回归（run-15 之前）：

```bash
TYPED_RECALL_SOURCE_CHECKOUT=/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk-0631-source \
/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/.venv/bin/python -m pytest \
  <worktree>/testcase/human-memory-program/tests -q -p no:cacheprovider
# 119 passed, 31 subtests passed（run-14 前为 114 passed；本轮新增 5 个用例，未删任何用例）
```

正式扫描与 RUN-06 §2.3 逐字相同，仅 `--artifact-dir` 改为 `.../typed-recall-401/run-15-m0631-followups`，输出到 `run-15.log` / `run-15.stderr`；artifact 目录执行前不存在；**不传 `--consumer-entrypoint`**。退出码 3，stderr 为空。

### 7.2 结果

| 项 | 值 |
|---|---|
| artifact | `.local-test-evidence/2026-09-08/typed-recall-401/run-15-m0631-followups/`（565 MB） |
| `run_id` | `91a93edc2d4844ceb98f1401dddf8991`（2026-09-08T17:18:55.357Z → 17:19:29.926Z，35 s） |
| 候选身份 | Harness 0.7.10 / `031fdc68…` / `e559bc1b…`；Memory 0.6.31 / `ff8be5f3…` / `e8dc27cc…`（`candidate_pin_differences=[]`） |
| 隔离 | `isolated-process-verified-installed-wheels`；`oracle_blockers=[]` |
| 整体 | `NOT_RUN/BLOCKED`；`acceptance_counts` **PASS 366 / FAIL 0 / BLOCKED 35** |
| 层 | public：passed 356 / failed 0 / blocked 35；source：passed 10 / failed 0 |
| `fixture_sha256` / `execution_layers_sha256` | `d63b0bb6…` / `101c14ba…`（**与 run-14 逐字相同**） |
| `oracle_code_sha256`（a2） | `0ff6282d0394fad5af203be6e12b2cfe5c48d5ab5eda77e260bb05c0cafad522`（run-14 `97b58d33…`） |
| bridge-summary / public-observations / source-observations sha256 | `984d8cbd…` / `477925f1…` / `a917f01d…` |
| 变更执行代码（sha256 前 8 位） | `adapters/typed_recall_normal_cases.py` `92541ced…`、`typed_recall_return_cases.py` `172d04f7…`、`typed_recall_selection_cases.py` `f1af1298…`、`typed_recall_context_use_cases.py` `d7f9dccd…`、`typed_recall_authority_event_cases.py` `a5c642fd…`、`typed_recall_procedure_cases.py` `de8a6fa9…`、`typed_recall_prospective_cases.py` `051ce2b1…`；`runners/typed_recall_a2_oracle.py` `0ff6282d…`、`typed_recall_selection_oracle.py` `c324f0a4…`、`typed_recall_context_use_oracle.py` `21d9b74f…`、`typed_recall_normal_inputs.py` `5849ef5a…`、`typed_recall_procedure_oracle.py` `af633107…`、`typed_recall_prospective_oracle.py` `7cc13f50…`（`runners/typed_recall_bridge.py` 亦有改动——只加了 §4.3 的 reason 替换与新类别映射——它是父进程自身的编排代码，不在 `execution_code_sha256` / `validation_code_sha256` 两张表里；其行为由 `tests/test_typed_recall_bridge.py` 覆盖） |

## 8. 逐格对照 run-14 → run-15

**15 格变化；其余 386 格状态与 reason 逐字相同。**

### 8.1 转 PASS 的 11 格

| 组 | 格数 | 格 ID |
|---|---:|---|
| H 组投影 | 4 | `selection-budget/projection:{semantic,episode,procedure,prospective}` |
| 分页零候选读 | 5 | `protocol/{page-correct-binding,page-wrong-result-hash,page-wrong-coordinate,page-expired-result,naked-source-ref}` |
| I-3 continuation | 1 | `current-use/context:new-continuation` |
| 选择车道 | 1 | `selection-budget/tie-score` |

### 8.2 维持 BLOCKED 但改判的 4 格

| 格 | run-14 类别 / 理由前缀 | run-15 类别 / 理由前缀 | 依据 |
|---|---|---|---|
| `selection-budget/tie-matched-lane-count` | EXECUTOR_UNIMPLEMENTED / `CELL_EXECUTOR_NOT_IMPLEMENTED` | **CONTRACT_FACT_UNPAIRABLE** / `PUBLIC_TIE_NOT_CONSTRUCTIBLE:` | §4.1 / §4.3 |
| `selection-budget/tie-memory-type-empty` | 同上 | 同上 | §4.3 |
| `selection-budget/tie-source-ref` | 同上 | 同上 | §4.3 |
| `selection-budget/tie-source-revision-or-zero` | 同上 | 同上 | §4.3 |

原始观测层与 RUN-06 §4.3 同理：每轮随机 init authority_ref 与 rejection UUID 带来 hash 级差异，oracle 只看 hash 间关系。本轮所有判定变化都可归因到 §2–§5 列出的 runner/adapter 改动，**没有一条 fixture 改动**。

## 9. 剩余 35 格 BLOCKED

| 类别 | 格数 | 内容 |
|---|---:|---|
| `ORACLE_GAP` | 14 | conflict 精确 reason 3、context-use exact-once 2、短时域渲染/TTL 6（`short-chain-complete`、`short-expiry-equals-now`、`short-future`、`short-source-suppressed`、`protocol/mixed-long-short`、`protocol/short-only`）、executed-lane 3 |
| `EXECUTOR_UNIMPLEMENTED` | 12 | short registration/classification 2、budget deadline/short-emoji/short-escaping 3、dedupe 4、ranking-order 1、procedure 观察晋升 1、`projection:short_horizon` 1 |
| `CONTRACT_FACT_UNPAIRABLE` | 4 | 四个 tie 格，见 §4.3 |
| `DUPLICATE_UNTESTABLE` | 3 | `contest-{nested,one-member,three-members}` |
| `SDK_INCREMENT_REQUIRED` | 2 | `authority:policy_hash_change`、`authority:short_source_cleanup`（SDK 仓另有代理处理） |

## 10. 后继（followup 清单）

1. **短时域一组 8 格**（`ORACLE_GAP` 6 + `EXECUTOR_UNIMPLEMENTED` 2）是现在最大的一块，且彼此共享 `SHORT_COMPLETE_REGISTRATION_CLASSIFICATION_TIME_AND_ORIGINAL_HASH_BINDINGS_PENDING` 这一条 reason；建议下一轮整组接线（注册/分类/时间/原始哈希绑定）。
2. **dedupe 4 格 + ranking-order 1 格 + budget 3 格**：都是 `CELL_EXECUTOR_NOT_IMPLEMENTED`，可复用本轮新加的「两条同型 episode 落在同一排名命名空间」构造（`seed_two_episode_variant`），成本比 run-14 时低。
3. **`projection:short_horizon`（1 格）**：H 组四型已通，短时域那型需要走 conversation registration + `rebuild_short_horizon_projection` 路径，构造与 §2 不同，单列。
4. **conflict 精确 reason 3 格、executed-lane 3 格、context-use exact-once 2 格**：仍是需要 Memory/Harness 增量的见证缺口（run-08 §7 第 4 条 (c)(d)），保持提案态。
5. **`_validate_execution_layers` 的 layers revision 白名单过时**（见 §6 末）：`(1,2,3,4,5)` vs 实际 rev 15，使 `--self-check` 路径必然 FAIL。属独立缺陷，建议单独一轮修并补反例，本轮刻意未动以免与 401 结论混淆。
6. **格集合扩张规则**：若确实要补 0.6.31 争议短路负例与 0.6.29 用途围栏正例（§6），需先裁决并实现一套「加格」的封存规则，而不是在现有 `fixture_change_lineage` 上凑。
7. **bridge 层级 PASS 规则仍缺失**：任何一层永远停在 `NOT_RUN/BLOCKED`；35 格未清零前不影响结论。

## 11. 边界遵守

未改 `ARCHITECTURE/`、`backend/`、Host pin、桌面应用；未运行 18120 端口；全程单一 runner/pytest 进程。API key 未打印、未提交。所有改动限于 `testcase/human-memory-program/` 与本记录。封存 fixture 与 layers 两个文件本轮**零改动**（`git status` 中不出现），因此不涉及 rev bump、谱系条目与段落密封迁移；`test_0613_successor_retains_original_obligations_and_candidate_lineage` 与 `test_typed_recall_fixture_authorities.py` 全绿。
