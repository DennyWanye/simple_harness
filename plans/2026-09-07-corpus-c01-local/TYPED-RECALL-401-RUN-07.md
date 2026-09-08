# 401 矩阵 BLOCKED 收敛（run-13，M0.6.31 pin 不变）：选择车道接线、0.6.31 新见证接线、定义冲突裁决表

> 2026-09-08。分支 `worktree-matrix-401-r2`（Host main `f7b14325`）。只改 `testcase/human-memory-program/`（runners / adapters / tests）与本记录；**fixture rev 16 / layers rev 14 / 候选 pin 均未改动**（fixture sha `31fbb8bc…`、layers sha `83238bc6…`、bridge 常量不变，因此本轮无需 rev bump、无需改 `test_typed_recall_fixture_authorities.py` 的版本断言）。
> 对照基线：run-12（`.local-test-evidence/2026-09-08/typed-recall-401/run-12-m0631/`，`TYPED-RECALL-401-RUN-06.md`）。
> 用户授权：技术取舍由执行者裁决并记录，不回问。本文 §6 为逐项裁决。

## 0. 一句话结论

**PASS 268 → 279 / FAIL 3 → 0 / BLOCKED 130 → 122。** 12 格变化：11 格转 PASS，1 格（`authority:revoke`）由 FAIL 改判为**带完整正向见证的**公共契约冲突。runner 退出码回到 3（整体 `NOT_RUN/BLOCKED`，122 格未闭合，属预期）。没有任何一格由 PASS 退化，没有放松任何封存断言。

| 阻塞类别 | run-12 | run-13 | 变化 |
|---|---:|---:|---:|
| `EXECUTOR_UNIMPLEMENTED` | 20 | **17** | −3：`selection-budget/{tie-source-kind, tie-newer-source-time, budget:greedy}` 全部 PASS（§2） |
| `ORACLE_GAP` | 24 | **18** | −6：conflict 的 6 格 durable group/member/resolution 见证由 0.6.31 的 confirmation 成员历史可见性闭合（§3） |
| `FIXTURE_INVALID_OR_INSUFFICIENT` | 86 | **87** | +1：`current-use/authority:revoke` 由 FAIL 改判入本类（§1.2） |
| 合计 | 130 | **122** | −8 |

（PASS 增量 11 = 3 选择 + 6 conflict + 2 短时域投影修复；FAIL 3 全部清零。）

## 1. 修复 run-12 暴露的 3 格（0.6.29 / 0.6.30 的真实行为变化）

### 1.1 短时域投影结果新增两个审计计数（2 格回到 PASS）

`runners/typed_recall_authority_event_oracle.py`：0.6.30 给 `ShortHorizonProjectionBuildResult` 追加了 `split_group_count` / `truncated_group_count`。把原来的整 dict 等值比较**扩展为包含两个新字段并钉死为 0**：

```python
require(o['projection_build'] == {'projected_chunk_count': 1, 'removed_chunk_count': 0,
                                  'split_group_count': 0, 'truncated_group_count': 0,
                                  'audit_id': o['projection_build'].get('audit_id')}
        and isinstance(o['projection_build'].get('audit_id'), str), '…')
```

这是**新增义务而非放松**：矩阵的短时域目标文本远低于 `SHORT_HORIZON_CHUNK_MAX_CHARS = 2048` 码点，因此必须投影为**恰好一条未分段 chunk**、且不得有任何组被截断；同时 §RUN-06 4.1 已独立验证 chunk_id 与 0.6.28 逐字相同。`current-use/authority:{short_source_expiry, short_source_invalidation}` 回到 PASS。

### 1.2 `current-use/authority:revoke`：0.6.29 用途围栏使封存期望不可产生（FAIL → 带见证的 BLOCKED）

封存行 `authority_event_cases[revoke]` 要求 `expected_old_result_use = RECALL_AUTHORITY_STALE`。0.6.29 之后，revoke 把被绑定来源**完全恢复**到绑定时的状态，逐来源重校验全部通过，`authorize_recall_context_use` 因此签发收据（RUN-06 §4.2）。**没有任何公共构造**能让一次 revoke 使旧结果失效——这是格定义与公共契约的冲突，不是候选缺陷。

裁决：**不 FAIL、不改封存行、也不静默跳过**，而是把该事件归入新的 `RESTORING_EVENTS`（净效果为恒等的事件），**先把新行为完整见证下来，再报为公共契约冲突**：

- `runners/typed_recall_context_use_oracle.py`：`check_bundle` 新增**默认关闭**的 `allow_advanced_epoch` 形参（4 个 `current-use/context:*` 格仍走严格相等，一字未放松）；
- `runners/typed_recall_authority_event_oracle.py` 的 revoke 分支断言：
  1. 新 attempt **被准入**（有收据、无异常）；
  2. 收据的 `authority_epoch == epoch0 + epoch_delta + 1 > epoch0`，`policy_hash` 不变；
  3. 收据的 `item_bindings` 与 `result_hash` 与首次收据**逐字相同**，但 `receipt_id` 不同 —— 即「对同一组被绑定来源签发的第二张收据」；
  4. 其余 revoke 断言（压制目标、中间召回只剩 item2、撤销精确指向该 directive、恢复后两项都回来、事件顺序、历史 exact replay 零读）**一字未动**；
- 全部通过后返回 `BLOCKED`，reason 逐字记录冲突与见证值：

  > `PUBLIC_CONTRACT_CONFLICT:authority_event_cases[revoke].expected_old_result_use is RECALL_AUTHORITY_STALE, but M0.6.29 admits a fresh use whose authority epoch advanced when every bound source revalidates; a revoke restores exactly the bound sources, so the sealed rejection is not producible by any public construction on this candidate (witnessed: receipt authority_epoch 5 > bound 3, identical item_bindings, unchanged policy_hash)`

bridge 已有的 `PUBLIC_CONTRACT_CONFLICT:` 前缀归类把它计入 FIXTURE 类。**该判据是双向的**：新增反例 `revoke_rejected`（把观测改回 STALE 拒绝）会让 oracle FAIL —— 一旦候选将来恢复拒绝行为，矩阵会立刻发现并要求把该格移出冲突类，而不是继续静默 BLOCKED。

## 2. 选择车道三格接线（`tie-source-kind` / `tie-newer-source-time` / `budget:greedy`）

新增 `adapters/typed_recall_selection_cases.py` + `runners/typed_recall_selection_oracle.py`（纯标准库），由 `adapters/typed_recall_public_manager.py` 分发、`runners/typed_recall_bridge.py` 纳入 workspace 复制与 `execution_code_sha256`、`inputs["selection"]`、`runners/typed_recall_a2_oracle.py` 按 `selection_cell` 派发。

### 2.1 构造的关键事实（执行前由 pinned SDK 源码固定）

- **RRF 打分**：`score = round(Σ weight[lane] / (RRF_K + rank[lane]), 12)`（`core/recall.py::RecallCandidate.score`），封存的 `stable_tie_break_fields` 与 `core/recall.py::_stable_key` 逐字一致。
- **认知与短时域是两个独立的 lane 排名命名空间**（`backends/sqlite_v5.py` 的两个候选收集器分别 `enumerate(..., start=1)`）。因此**一条只命中 full_text 的认知候选与一条只命中 full_text 的短时域 chunk 会拿到完全相同的 rank 1 与完全相同的分数 0.30/61**，`matched_lane_count` 同为 1。
- **同一 source_kind 的两条候选永远不可能打平**：RRF 五个 lane 权重两两不等（0.4/0.3/0.15/0.1/0.05），而同一 lane 内的 rank 必然互异。**认知 + 短时域是本候选上唯一能把封存排序前两个字段同时打平的公共构造**——而打平前两个字段正是任何 tie-break 格能观测到被测字段的前提。
- **`source_time` 按记忆类型取值**：semantic 取 `created_at`（写入时刻，不可控），**episode 取 `occurred_start`（公共 payload 字段，可控）**（`_cognitive_public_payload_unlocked`）。因此三格一律用 episode 作认知侧，使 `-typed_source_time` 在两边都能被设定。

实测（真实公共执行，非推理）：两条候选的 `score` 均为 `0.004918032787`。

### 2.2 逐格构造与断言

| 格 | 构造（全部真实公共调用） | 观测 | 判据 |
|---|---|---|---|
| `tie-source-kind` | episode `occurred_start` **等于** 短 chunk `occurred_at`（均取封存行的 `2026-08-31T10:00:00Z`） | 顺序 `[cognitive_memory, short_horizon]` | 前三个字段（分数、lane 数、source_time）逐一相等，顺序由 `source_kind` 决定，且与封存 `expected_order` 映射出的 source_kind 序列一致 |
| `tie-newer-source-time` | **同一对封存时刻（10:00 / 11:00）跑两个方向**：`cognitive-newer`（episode 11:00 / chunk 10:00）与 `short-newer`（episode 10:00 / chunk 11:00） | 两向都是「更新的在前」；`short-newer` 顺序为 `[short_horizon, cognitive_memory]` | 除「更新者在前」外，额外断言 `short-newer` **反转了 source_kind 顺序** —— 这直接证明 `-typed_source_time` 优先级高于 `source_kind` |
| `budget:greedy` | 探针轮（无预算）确认「较大的 episode 排第一、较小的 chunk 排第二」；预算轮把 `max_bytes` 设为**探针中较小项 canonical envelope 的精确字节数**（实测 124），`max_items` 保持 8 | 预算轮只选中较小的短时域项，`truncated = true` | 独立重算两项的 canonical 字节（397 / 124），断言 `oversize > max_bytes == smaller`，且被跳过的是**排序在前**的那条 |

三格另共用一组完整绑定：`check_execution_wire`（decision/result/context/plan 全部公共 hash）、投影恰好一条未分段 chunk、`filtered_candidate_count == 2`、两项 payload 与冻结输入逐字相符、短项 `source_revision is None` / `memory_type is None`、以及**同请求的 durable exact replay 逐字相同**。

### 2.3 与封存字面值的偏离（runner 选择，逐条记录于 oracle 模块 docstring 与本节）

1. **`tie-source-kind`**：封存对给两名成员相同的 `source_ref: "same"`，并给短时域成员 `memory_type: "semantic"`。二者在公共面都不可构造（id 是内容寻址；短 chunk 的 `memory_type` 恒为 null），但**两者在封存优先级表里都排在 `source_kind` 之下**，一旦 `source_kind` 决出胜负就不会被读取。构造把 `source_kind` 之上的字段全部打平，只在 `source_kind` 上不同 —— 与该格要证明的义务等价。
2. **`tie-newer-source-time`**：封存对是两条认知 semantic 记忆，按 §2.1 不可能打平分数。该格要证的是 `-typed_source_time` 压过其下所有字段，因此改用可打平的认知/短时域对并**跑两个方向**；`short-newer` 方向让结论与 `source_kind` 相反，是比封存构造**更强**的见证。
3. **`budget:greedy`**：封存的 `item_bytes` 200/120 与 `max_bytes` 120 是编码前的字面值，单条真实公共 envelope 已超过它们。断言改为核对封存的**关系**（第一项超限、第二项恰好等于上限、第一项被跳过、第二项仍被选中、`truncated` 置位），字节数由 oracle 独立重算，不引用字面值。

### 2.4 反例

`tests/test_typed_recall_selection_public.py`：3 格真实执行 PASS + **15 项篡改反例**（顺序反转、分数篡改、时刻不再相等、`split_group_count` 非零、replay 非重放、`filtered_candidate_count` 变化、输入被改、两个方向各自反转、时刻被抹平、预算轮塞回两项 / 取消 truncated / 放宽 1 字节 / 选中超限项 / 探针顺序反转），另加一项「输入确实由封存 tie-break 行推导」的结构测试。

## 3. 0.6.31 新公共见证的排查与接线（原「公共 API 无对应见证」24 格）

对 0.6.28 → 0.6.31 的公共面做了逐项 diff：**根导出逐字未变**（`__init__.py` 唯一差异是版本串；`tests/artifact/public-api-0.6.{28,29,30,31}.json` 的 `root`(118) / `migrations`(21) / `removed_public_methods`(22) 三个集合完全一致）。三个版本合计只交付：

- **一个新公共方法**：`MemoryManager.read_recall_context_use_authority_notes(*, principal, run_id=None, limit=100)`（`core/manager.py:466`）；
- **两个新公共字段**：`ShortHorizonProjectionBuildResult.split_group_count` / `.truncated_group_count`（`core/short_horizon.py:651-658`，该类**是**根导出）；
- **一处根导出 API 的行为放宽**：`MemoryManager.check_history_visibility`（`core/manager.py:190`）——`backends/history_visibility.py:314-322` 起，`HistoryRecallBinding.item_hash` 在 `result.items` 里找不到时会到 `result.confirmation_groups[*].members` 里按 `result_member_hash` 逐字核对。

其余新符号（`RECALL_CONTEXT_USE_AUTHORITY_EPOCH_ADVANCED`、`RecallContextUseAuthorityNoteV1`、`core.short_horizon.parse_short_horizon_projection_key`、`features.conflict_slot.contested_slot_text`）**均不在根导出**，属矩阵禁止的「private submodule import」，不可用。

### 3.1 逐组判定

| 组 | 格数 | 0.6.31 判定 | 依据 |
|---|---:|---|---|
| **A** `CONFLICT_DURABLE_GROUP_MEMBER_RESOLUTION_HASH_ORACLE_PENDING` | 6 | **已闭合 → 全部接线为 PASS（§3.2）** | `check_history_visibility` 现在接受 confirmation 成员绑定，给出逐次调用的 durable 见证。冻结的 `group_hash`/`member_hash`/`resolution_hash` 三个**私有** hash 仍不可见（`export_canonical_state_manifest` 对这三张表只给聚合根 `CanonicalStateTableRootV1`），但 fixture 的 `approved_oracle.legacy_status` 已明确「legacy literal conflict/state commitments are NOT active acceptance evidence」且 `conflict_write_oracle` 在 `legacy_commitment_sections` 内 —— 该格义务是**状态**而非字面 hash 比对 |
| **B** `SHORT_COMPLETE_REGISTRATION_..._PENDING` | 6 | **仍开放** | 0.6.30 只把原渲染抽成具名纯函数 `render_short_horizon_line(role, public_text) -> f"{role}: {public_text}"`（`core/short_horizon.py:102-105`），语义一字未改；TTL 常量 `SHORT_HORIZON_RETENTION_SECONDS = 5*24*60*60`（`:38`）未变，`MemoryManager.rebuild_short_horizon_projection` 无 retention 形参。冻结 `payload_hash` / 任意 `expires_at` 依旧无法命中 |
| **C** `page_typed_recall_result` 无逐次候选读见证 | 4 | **仍开放** | `page_typed_recall_result`（`backends/sqlite_v5.py:4557`）的每条失败路径仍是裸抛（`typed_recall_result_binding_invalid`:4594、`typed_recall_result_expired`:4605、`typed_recall_page_offset_invalid`:4635、`typed_recall_page_budget_too_small`:4648），无一调用 `_attach_pre_candidate_rejection`；6 个调用点全在 `execute_typed_recall` 体内 |
| **D** apply 结果 reason_code 泛化 | 3 | **仍开放** | `mutation_contest_nested_group_rejected`(:8102)、`mutation_contest_exact_slot_required`(:8118)、`mutation_contest_distinct_evidence_required`(:8138) 仍被 `str(exc).startswith("mutation_contest_")` 统一映射为 `VALIDATION_REJECTED`（:7611-7620）；该段 0.6.28→0.6.31 一字未动 |
| **E** `PUBLIC_EXECUTED_LANE_WITNESS_UNAVAILABLE` | 3 | **仍开放** | `TypedRecallExecution.degradation_codes`（`core/recall.py:230`）两版逐字相同；唯一向量相关公共码 `cognitive_vector_unavailable` 只见证「没有 embedder」，`vector-not-requested` 连该码都不产生。0.6.31 的 lane 改动全在 `_collect_typed_recall_confirmation` 内部 |
| **F** `CURRENT_USE_HARNESS_RESERVATION_EXACT_ONCE_WITNESS_UNVERIFIED` | 2 | **仍开放** | `read_recall_context_use_authority_notes` 的 backend 实现有硬过滤 `if current_epoch <= bound_epoch: continue`（`backends/sqlite_v5.py:4933`），只导出「绑定后 epoch 已前进」的收据；exact-once 场景 epoch 不前进，返回空。且该义务本体是 **Host 侧**预留见证（`simple_harness.execution.sqlite.context_use` 的 `require_live(..., phase="provider_reserved")`，私有子模块，Harness 0.7.10 冻结），Memory 任何版本都无法改变 |

**结论：24 格中只有 A 组 6 格在 0.6.31 获得新公共见证，已全部接线；其余 18 格无任何新公共面可用。**

### 3.2 A 组 6 格的接线（精确公共调用与字段）

- **公共调用**：`MemoryManager.check_history_visibility(principal=…, disclosure_context=…, bindings=(HistoryRecallBinding(result_id, result_hash, item_id, item_hash), …))`
- **绑定字段**：`item_id` = `result.confirmation_groups[0].members[i].member.item_id`；`item_hash` = 同一成员的 **`result_member_hash`**（`TypedRecallConfirmationMemberV1.result_member_hash`，`field(init=False)`，域 `simple-harness/typed-recall-confirmation-member/v1`）
- **读出字段**：`HistoryVisibilitySnapshot.items[i].visible` / `.reason`（均为根导出类型）

改动：
1. `adapters/typed_recall_public_cases.py::execution_wire` 追加 `result_group_hashes` / `result_confirmation_member_hashes` / `result_confirmation_member_ids`（与既有 `result_item_hashes` 同性质，作为绑定输入）；
2. `adapters/typed_recall_conflict_cases.py` 新增 `group_visibility(case, bundle, *, tamper=False)`：在 confirmation 召回后对**两名成员**各取一次真实可见性，另取一次**篡改首个成员 hash 的负控制**；`resolve-*` 四格在 revision 9 落库后用**同一批绑定**再取一次；
3. `runners/typed_recall_a2_oracle.py::assess_conflict` 用 `check_group_visibility` 绑定：bindings 必须逐字等于公共 result/member 身份、`snapshot.subject` 等于 decision subject、逐项 `(visible, reason)` 符合预期、各项 `binding_hash` 互异；并**独立重算** `result_member_hash` 与 `result_group_hash`（域 `…/typed-recall-confirmation-member/v1` 与 `…/typed-recall-confirmation-group/v1`）与 wire 记录核对。

实测语义（执行前由 pinned 源码固定，实测复核）：

| 时点 | 两名成员 |
|---|---|
| 决议前 | `(true, "history_visible")` × 2 |
| 篡改其中一个成员 hash | `(false, "history_binding_mismatch")` + 另一项仍 `(true, "history_visible")` |
| 真实 resolution（revision 9）后 | `(false, "history_source_stale")` × 2 —— **整组原子失效** |

6 格全部 PASS：`conflict-state/{contest-create-distinct-evidence, contested-dependent-complete, resolve-replacement, resolve-select-incumbent, resolve-terminal-supersede, resolve-terminal-suppress}`。反例见 `tests/test_typed_recall_conflict_visibility.py`（9 项：成员不可见、篡改被接受、绑定 hash/result_id 被改、wire 成员 hash 被改、无决议格却带决议后见证、决议后仍可见 / 只半边失效 / 失效原因不对）。

## 4. 正式扫描（run-13）

### 4.1 命令（消费者 venv 与 SDK 源 worktree 沿用 RUN-06 §2.1/2.2；artifact 目录执行前不存在；不传 `--consumer-entrypoint`）

与 RUN-06 §2.3 完全相同，仅 `--artifact-dir` 改为 `.../typed-recall-401/run-13-m0631-selection`，输出到 `run-13.log` / `run-13.stderr`。退出码 3，stderr 为空。

runner 自回归（run-13 之前）：

```bash
TYPED_RECALL_SOURCE_CHECKOUT=/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk-0631-source \
/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/.venv/bin/python -m pytest \
  <worktree>/testcase/human-memory-program/tests -q -p no:cacheprovider
# 114 passed, 31 subtests passed（run-12 前为 110 passed + 1 failed；新增 2 个测试文件、修复 1 项、补 bridge inputs 集合断言）
```

### 4.2 结果

| 项 | 值 |
|---|---|
| artifact | `.local-test-evidence/2026-09-08/typed-recall-401/run-13-m0631-selection/`（557 MB） |
| `run_id` | `e2e45bc8c92d4bc98637d2856c9cef75`（2026-09-08T15:52:31.788803Z → 15:53:06.736615Z，35 s） |
| 候选身份 | Harness 0.7.10 / `031fdc68…` / `e559bc1b…`；Memory 0.6.31 / `ff8be5f3…` / `e8dc27cc…`（`candidate_pin_differences=[]`） |
| 整体 | `NOT_RUN/BLOCKED`；`acceptance_counts` **PASS 279 / FAIL 0 / BLOCKED 122** |
| 层 | public：passed 269 / failed 0 / blocked 19；source：passed 10 / failed 0 |
| 按 lane（PASS / BLOCKED） | conflict-state 14/6；current-use 11/6；eligibility 219/82；fault-recovery 7/0；protocol 4/7；selection-budget 5/21；unsupported-replay 19/0 |
| `fixture_sha256` / `execution_layers_sha256` | `31fbb8bc…` / `83238bc6…`（不变） |
| `oracle_code_sha256`（a2） | `3170b95cce311ee75ee877bb59832694ebbc11963b8696c46ad3766f0bb39a55` |
| 新增执行代码 sha | `typed_recall_selection_cases.py` `257ddcf2…`；`typed_recall_selection_oracle.py` `a3774e4d…` |
| 变更执行代码 sha | `typed_recall_conflict_cases.py` `8d98a107…`、`typed_recall_public_cases.py` `fc93e1e7…`、`typed_recall_authority_event_oracle.py` `faf97b6a…`、`typed_recall_context_use_oracle.py` `caf03463…` |
| bridge-summary / public-observations / source-observations sha256 | `c3f37ae0…` / `dd4147cf…` / `b8e36c8f…` |

## 5. 逐格对照 run-12 → run-13（12 格变化；其余 389 格状态与 reason 逐字相同）

| 格 | run-12 | run-13 | 依据 |
|---|---|---|---|
| `current-use/authority:short_source_expiry` | FAIL | **PASS** | §1.1 |
| `current-use/authority:short_source_invalidation` | FAIL | **PASS** | §1.1 |
| `current-use/authority:revoke` | FAIL | **BLOCKED**（`PUBLIC_CONTRACT_CONFLICT:…`，FIXTURE 类） | §1.2 |
| `selection-budget/tie-source-kind` | BLOCKED `CELL_EXECUTOR_NOT_IMPLEMENTED` | **PASS** | §2 |
| `selection-budget/tie-newer-source-time` | 同上 | **PASS** | §2 |
| `selection-budget/budget:greedy` | 同上 | **PASS** | §2 |
| `conflict-state/contest-create-distinct-evidence` | BLOCKED `CONFLICT_DURABLE_..._PENDING` | **PASS** | §3.2 |
| `conflict-state/contested-dependent-complete` | 同上 | **PASS** | §3.2 |
| `conflict-state/resolve-replacement` | 同上 | **PASS** | §3.2 |
| `conflict-state/resolve-select-incumbent` | 同上 | **PASS** | §3.2 |
| `conflict-state/resolve-terminal-supersede` | 同上 | **PASS** | §3.2 |
| `conflict-state/resolve-terminal-suppress` | 同上 | **PASS** | §3.2 |

原始观测层与 RUN-06 §4.3 相同（每轮随机 init authority_ref 与 rejection UUID 带来 hash 级差异，oracle 只看 hash 间关系）。本轮所有判定变化都可归因到 §1–§3 列出的 runner 层改动，`execution_code_sha256` 中变化的文件即 §4.2 所列。

## 6. 裁决记录（用户委托的技术取舍）

1. **`authority:revoke` 报冲突而不是 FAIL**：FAIL 的语义是「候选违反封存义务」；此处是候选在 0.6.29 带记录地**主动改了契约**，而封存行早于该改动。矩阵既有约定（run-11 §1.5）就是「当前公共契约无法产生的封存期望归 FIXTURE 类 BLOCKED」。同时坚持**先见证再判 BLOCKED**，并让 oracle 在候选恢复拒绝时 FAIL，避免冲突判定变成永久的静默豁免。
2. **`allow_advanced_epoch` 默认关闭**：只有 authority-event 的 restoring 事件传 True，4 个 `current-use/context:*` 格保持严格 epoch 相等；且 revoke 分支另行钉死 epoch 的精确期望值，实际约束比原来更强而非更弱。
3. **0.6.30 两个新审计计数钉成 0**：不是把断言放宽以容纳新字段，而是把「本格的短时域目标必须投影为恰好一条未分段、零截断的 chunk」变成显式义务。
4. **tie-break 三格允许偏离封存的**低优先级**字段字面值**：封存对在 `source_ref` / `memory_type` / `source_kind` 上的字面值有的不可公共构造。裁决依据是封存自己的 `stable_tie_break_fields` 是**字典序比较器**——被测字段一旦决出胜负，其下字段不再被读取。因此只要把被测字段**之上**的字段全部打平，构造就与该格义务等价。三处偏离逐条写进 oracle 模块 docstring 与 §2.3。
5. **`tie-newer-source-time` 用两个方向而不是一个**：单方向（认知更新）无法区分是 `-typed_source_time` 还是 `source_kind` 在起作用；`short-newer` 方向让两者结论相反，才是该格的判别性见证。
6. **`budget:greedy` 用关系而不是字面字节**：封存的 200/120/120 是编码前数值，真实公共 envelope 单条已超过；oracle 独立重算真实字节并核对封存的关系式，不做阈值迁移。
7. **A 组 6 格接受状态断言作为 durable 见证**：三个冻结 hash 含 `created_at`/`plan_id`，属私有；fixture 的 `approved_oracle.legacy_status` 已把 legacy 字面 commitment 排除出有效验收证据。因此以「组存活 / 整组原子失效 / 篡改成员被拒」的公共状态见证闭合，并明确不读任何私表。
8. **不改 fixture**：全部改动不触及封存格定义、攻击、阈值、oracle 输入与 pin；fixture/layers sha 与 bridge 常量不变。
9. 未改 `ARCHITECTURE/`、`backend/`、Host pin、桌面应用；未运行 18120 端口；全程单一 runner/pytest 进程。

## 7. 剩余 122 格 BLOCKED

### 7.1 定义与公共契约冲突 —— 需要 program 侧裁决（FIXTURE 类 87 格）

路径缩写：**H** = `/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-sdk/src/simple_harness/`（冻结 Harness 0.7.10 公共 DTO）；**M** = `/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk-0631-source/src/simple_harness_memory/`；**F** = `<worktree>/testcase/human-memory-program/fixtures/typed-recall-v3.json`。

| # | 格 ID / 模式（格数） | 封存文本（F 原文） | 冲突条款（公共契约事实 + SDK 符号:行） | 建议裁决 | 可转 PASS |
|---|---|---|---|---|---:|
| A | `eligibility/disclosure:{USER_SELF,HOUSEHOLD,TASK_COLLABORATOR,EXTERNAL_PARTY,PUBLIC,UNKNOWN}:AUDIT:{PUBLIC,PERSONAL,SENSITIVE,RESTRICTED}`（24） | `exhaustive_axis_contract.disclosure`：`"recipients": [6 项]`、`"purposes": [… "AUDIT","EXPORT"]`、`"unlisted_expected": "INELIGIBLE"`、`"unlisted_reason": "DISCLOSURE_COMBINATION_FORBIDDEN"`；`disclosure_cases` 只列 `{"recipient":"USER_SELF","purpose":"AUDIT","allowed":[],"reason":"SEALED_AUTHORITY_REQUIRED"}` | 轴把 `AUDIT` 与 6 个普通 recipient 做笛卡尔积，但公共 DTO 构造期即拒：`DisclosureContext.__post_init__`（`H runtime/disclosure_protocol.py:273`）`raise ValueError("audit disclosure requires an AuditAccessDecision and audit recipient")`；`AUDIT_REVIEWER` 不在 fixture 的 recipients 轴内。DTO 拒绝发生在召回之前，`INELIGIBLE` 的 `enters_rank_input:false / public_candidate_count_delta:0` 无法由一次真实召回见证 | **(b) 重定义**：`AUDIT` 只与 `AUDIT_REVIEWER` + `AuditAccessDecision` 配对；6×4 组合改列为显式「DisclosureContext DTO 拒绝」格，判据为固定 `ValueError` 文本 + Memory 零调用 | 24 |
| B | `eligibility/epistemic:{episode,semantic,procedure,prospective}:{llm_inference,unknown}:{source_verified,repeated_observation}`（16） | `exhaustive_axis_contract.epistemic_verification`：`"unlisted_expected":"INELIGIBLE"`、`"unlisted_reason":"EPISTEMIC_VERIFICATION_COMBINATION_FORBIDDEN"`；`llm_inference × source_verified` 另显式列为 `{"expected":"INELIGIBLE","reason":"EPISTEMIC_FORBIDDEN"}` | `_validate_epistemic_evidence_matrix`（`H runtime/memory_protocol.py:2647`）`raise ValueError("verified states require trusted typed observation evidence")`；`trusted_typed` 只能由 TOOL/EXTERNAL 的 `TYPED_OBSERVATION` span 满足，`llm_inference/unknown` 依定义不具备 | **(a) 接受「入口拒绝＝该组合永不可召回」为该格证明并转 PASS**：轴对该格的唯一断言就是 INELIGIBLE，SDK 以更强的写入期拒绝实现同一禁令 | 16 |
| C | `eligibility/epistemic:{4 类}:verified_external:{unverified,source_bound,user_confirmed,repeated_observation}`（16） | 同 B 的 `unlisted_expected`；`epistemic_verification_cases` 只承认 `verified_external × source_verified → ELIGIBLE` | `H runtime/memory_protocol.py:2638` `raise ValueError("verified_external requires source_verified state")`；Memory 同向：`M core/mutations.py:495` `MemoryValidationError("mutation_verified_external_state_invalid")` | **(a) 同 B，转 PASS**（Harness 与 Memory 双侧同向禁止） | 16 |
| D | `eligibility/epistemic:{4 类}:llm_inference:{source_bound,user_confirmed}`（8） | 同 B | `M core/mutations.py:523` `MemoryValidationError("mutation_inference_must_be_unverified")` | **(a) 转 PASS** | 8 |
| E | `eligibility/epistemic:{4 类}:unknown:{source_bound,user_confirmed}`（8） | 同 B；`unknown × user_confirmed` 显式 `{"expected":"INELIGIBLE","reason":"EPISTEMIC_FORBIDDEN"}` | `M core/mutations.py:510` `MemoryValidationError("mutation_unknown_must_be_unverified")` | **(a) 转 PASS** | 8 |
| F | `conflict-state/contest-{nested,one-member,three-members}`（3） | `conflict_write_oracle.reject_cases`：`contest-nested` 带 `"nested_group_ref":"conflict-python-1"` 期望 `NESTED_CONFLICT_FORBIDDEN`；`contest-one-member` / `three-members` 期望 `CONFLICT_MEMBER_CARDINALITY_INVALID` | 公共 contest 变更没有 `members` / `nested_group_ref` 输入：组成员由 `_insert_cognitive_conflict_group_unlocked`（`M backends/sqlite_v5.py:11624-11640`）硬编码为 `(1,"incumbent")`、`(2,"challenger")` 两员；`nested_group_ref` 在 M 全树无符号。攻击输入不存在 ⇒ 期望的 reject 不可触发 | **(b) 重定义**：成员基数与非嵌套是 Memory 内部不变式，已由 `create_case` 的 `expected_member_count: 2` 正向见证（本轮 §3.2 又补了 durable 见证）；三格并入该正向证明或改列为「公共面无此攻击输入」的契约事实格 | 3 |
| G | `eligibility/epistemic:prospective:{llm_inference,unknown}:unverified`（2） | 同 B（prospective 的 ELIGIBLE 仅 `explicit_user × {source_bound,user_confirmed}`，且为 `ELIGIBLE_WITH_TRIGGER_SIGNAL`） | `M core/mutations.py:507/520` 强制此类记忆停留在 `candidate/draft`，runner 只能以 `state='candidate'` 播种；而 prospective signal authority 要求 `state ∈ {pending, triggered}`，candidate 态不产生 `memory.prospective.registration.requested` outbox 命令 ⇒ `REQUIRED_APPLICABILITY_OR_SIGNAL_AUTHORITY_NOT_ESTABLISHED` | **(a) 接受「无法进入 pending ⇒ 无触发信号权威 ⇒ 永不可召回」为该格证明并转 PASS** | 2 |
| H | `selection-budget/projection:{semantic,episode,procedure,prospective}`（4） | `minimal_projection_oracle[*]`：`allowed_payload_fields`、`source_record` 含 `"evidence_ids":["secret-evidence"]`、`"classification":"SENSITIVE"`、`"cross_scope": true`、`"extra_typed_field":"must-strip"`，以及冻结 `payload` / `payload_hash`；episode 用 `"occurred_interval":{"start","end"}` | 严格公共 DTO 无法承载 canary：`SemanticMemoryPayload`（`H runtime/memory_protocol.py:2194`）只有四个字段且 `from_json` 走 `_exact_keys`；`EpisodeMemoryPayload`（`:2121-2130`）是 `occurred_start` / `occurred_end`，与冻结的 `occurred_interval` 结构不同 ⇒ `ORIGINAL_EPISODE_OCCURRED_INTERVAL_DIFFERS_FROM_PUBLIC_PROJECTION`；procedure/prospective 另缺适用性/信号权威接线 | **(b) 重定义**：canary/cross_scope 义务改述为「严格 DTO 不可表示 ⇒ 投影泄露面为零」的构造性事实，格断言收敛为 `allowed_payload_fields` 与冻结 `payload_hash` 相等；episode 冻结 payload 按公共 `occurred_start/occurred_end` 重新封存；procedure/prospective 复用已有 binding 适配器（runner 改动，无需 SDK 增量） | 4 |
| I-0 | `current-use/authority:revoke`（1，**本轮新增**） | `authority_event_cases`：`{"event":"revoke","before_epoch":41,"after_epoch":42,…,"expected_old_result_use":"RECALL_AUTHORITY_STALE"}` | 0.6.29 用途围栏把 epoch 相等性改为倒退性判据并逐来源重校验（`M backends/sqlite_v5.py:4674`）；revoke 的净效果是把被绑定来源恢复原状 ⇒ 重校验必过 ⇒ 收据签发。见证值见 §1.2 | **(b) 重定义**：该格改述为「revoke 之后旧结果的用途授权在来源未变时**应当**被准入，且 epoch 前进被如实记录」，或与 `suppression` 事件合并为一格（压制才是使旧结果失效的那一步）。**不建议** (c)：这是有记录的契约裁决，不是可观测性缺口 | 1 |
| I-1 | `current-use/authority:policy_hash_change`（1） | `authority_event_cases`：`before_policy_hash "a9abbb22…" → after_policy_hash "67b93526…"` | `_RECALL_POLICY_HASH` 是 Memory 常量（`M backends/sqlite_v5.py:300-315`，由 `{"policy":"typed-recall-eligibility/v1","schema":6,"rrf_k":60,"weights":{…}}` 的 canonical sha256 得出），公共 API 无策略版本入参 | **(b) 重定义**：改为「公共面不存在改变 policy_hash 的输入 ⇒ 该授权事件在公共契约上不成立」，正向由其余 10 条事件行的 `policy_hash` 恒定见证 | 1 |
| I-2 | `current-use/authority:short_source_cleanup`（1） | `authority_event_cases`：`{"event":"short_source_cleanup","before_epoch":48,"after_epoch":49,…}` | `cleanup_short_horizon` 只在后端 port 上（`M core/port.py:465-467`）；`MemoryManager` 只有 `cleanup_recall_results`（`core/manager.py:1245`），无短时域清理入口 | **(c) 维持 BLOCKED，挂钩 SDK 增量** `MemoryManager.cleanup_short_horizon`。不宜用 `short_source_expiry` 冒充同一事件 | 0 |
| I-3 | `current-use/context:new-continuation`（1） | `context_use_cases`：`new-continuation` 与 `new-provider-attempt` 的唯一差异即 `continuation_id: "continuation-2"` | `RecallContextUseAuthorizationRequestV1`（`H runtime/recall_protocol_v4.py:1327-1341`）**无 `continuation_id`**，该文件全文无 `continuation` 符号 | **(b) 重定义**：continuation 轴在公共契约上由 `turn_id` + `provider_attempt_id` 表达，该格与 `new-provider-attempt` 同构，合并或改列为「无对应公共字段」的契约事实格 | 1 |
| J | `eligibility/prospective-trigger-missing`（1） | `eligibility_cases`：`{"id":"prospective-trigger-missing","typed_trigger_complete":false,"expected":"INELIGIBLE","reason":"TYPED_TRIGGER_REQUIRED"}` | `ProspectiveMemoryPayload.__post_init__`（`H runtime/memory_protocol.py:2469`）`raise TypeError("trigger must use a strict time or event trigger")`；`from_json` 用 `_exact_keys` ⇒ 严格 DTO 无法表示「缺失 typed trigger」 | **(a) 接受「构造期拒绝＝该状态永不可进入召回」为该格证明并转 PASS** | 1 |
| K | `protocol/page-correct-binding`（1） | `result_page_cases`：`{"id":"page-correct-binding","bounds":{"offset":0,"length":128},"expected":"HASH_IDENTICAL_RESULT_BOUND_PAGE","payload_count":1}` | 分页按 binding 的 canonical JSON 计费（`M backends/sqlite_v5.py:4641-4648`），单个 `RecallResultPageBindingV1` 的规范 JSON 已超 128 字节 ⇒ `MemoryLimitError("typed_recall_page_budget_too_small")`；公共上限为 `RECALL_MAX_BYTES = 65_536` | **(b) 重定义**：128 字节界按旧 binding 形状冻结，改按「恰好容纳 1 个公共 binding」的字节预算重新封存 `bounds.length` 与 `expected_page_hash`，保留 `payload_count:1` 与结果绑定断言 | 1 |

**汇总**：若 **(a)**（B/C/D/E/G/J）被接受 → **51 格**转 PASS；若 **(b)**（A/F/H/I-0/I-1/I-3/K）被接受 → **35 格**转 PASS；两者全接受 → **86/87 格**转 PASS，PASS 由 279 → **365**，只剩 I-2 一格按 **(c)** 挂钩 SDK 增量。

分层差别（裁决时需区分）：A–E、J 是**入口/构造期拒绝**（Memory 未被调用），裁决点是「拒绝算不算该格的证明」；G、H 是**已真实执行但 fixture 附加义务未建立**，裁决点是「附加义务是否属于该格断言」；F、I、K 是**公共面根本没有该输入/该界**，裁决点是「重定义还是等增量」。

### 7.2 公共 API 无对应见证（ORACLE 类 18 格）

即 §3.1 的 B（6）/ C（4）/ D（3）/ E（3）/ F（2）五组，0.6.31 无新公共面可用。建议作为 Memory 0.6.32+ 的小增量提案：分页拒绝附 `TypedRecallRejectionV1`；apply 结果携带精确 validation reason（至少让三个 `mutation_contest_*` 一一映射）；公开「实际执行 lane」；`cleanup_short_horizon` 走 Manager。短时域渲染/TTL（B 组 6 格）按 NORMAL-EXECUTION.md 明定保持 BLOCKED。

### 7.3 执行器未实现（EXECUTOR 类 17 格）

| 格 | 需要什么 |
|---|---|
| `eligibility/short-{registration,classification}-invalid`（2） | 需确认 Memory 召回时是否重解析 conversation registration / item classification（Host authority port 可返回失配），未调查 |
| `selection-budget/budget:deadline`（1） | deadline 用 `time.monotonic()`，公共 builder 的 `clock` 不覆盖 → 只能真超时，非确定性；很可能只能列为契约冲突 |
| `selection-budget/budget:short-{emoji,escaping}`、`projection:short_horizon`（3） | 公共短时域 payload 带 `user: ` 前缀且 `occurred_at` 为数值，冻结字节/hash 无法命中；可执行后以契约差异 BLOCKED |
| `selection-budget/dedupe:*`（4）、`ranking-order`、`tie-{score,matched-lane-count,memory-type-empty,source-ref,source-revision-or-zero}`（6） | 本轮已证明认知/短时域配对是唯一能打平分数的公共构造。据此，`tie-score` 与 `tie-matched-lane-count` 下一轮**可用同一框架接线**（分别让两条候选分数不等、lane 数不等）；`tie-memory-type-empty` / `tie-source-ref` / `tie-source-revision-or-zero` 需要在 `source_kind` 相同的前提下打平，按 §2.1 在公共面不可构造，应转入 §7.1 的 (b) 重定义；`dedupe:*` 与 `ranking-order` 需要多 lane 候选注入，仍是 Memory 内部行为 |
| `eligibility/epistemic:procedure:observed_behavior:repeated_observation`（1） | draft→active 的观察晋升路径（`record_procedure_observation` TERMINAL_OUTCOME 成功计数） |

## 8. 后继

1. §7.1 的两句裁决（(a) 与 (b)）是最大的一块：接受即 PASS 279 → 365。
2. `tie-score` / `tie-matched-lane-count` 用本轮的认知+短时域框架接线（预计 +2）；`tie-memory-type-empty` / `tie-source-ref` / `tie-source-revision-or-zero` 提交为 (b) 类重定义。
3. 0.6.31 的争议短路收窄在 401 格里**没有对应负例格**（矩阵唯一的 conflict 查询词就是争议槽位本身）。建议新增一格：同库存在 contested head，但查询词只命中普通候选的槽位 → 期望正常返回 items、无 group、不置 `truncated`。这正是 0.6.31 修的那个生产缺陷，矩阵目前对它零覆盖。
4. 0.6.29 的用途围栏同理只有 `authority:revoke` 一格被动触及；建议新增「压制**别的**记忆后 epoch 前进、被绑定来源未变 → 应当签发收据」的正例格，与 §7.1 I-0 的重定义配套。
5. bridge 层级 PASS 规则仍缺失（任何一层永远停在 `NOT_RUN/BLOCKED`）—— 122 BLOCKED 未清零前不影响结论。
