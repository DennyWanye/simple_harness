# 401 矩阵 BLOCKED 收敛（run-11，M0.6.28 pin 不变）：执行器/oracle/前置条件补齐

> 2026-09-08。分支 `worktree-matrix-401`（基于 Host `26395f01`），只改 `testcase/human-memory-program/`（runners / adapters / tests）与本记录；**fixture rev 15 / layers rev 13 / 候选 pin 均未改动**（fixture sha `3e4f23b7…`、layers sha `ab0d0ad7…`、bridge 常量不变，因此本轮无需 rev bump、无需改 `test_typed_recall_fixture_authorities.py` 的版本断言）。
> 对照基线：run-10（`.local-test-evidence/2026-09-08/typed-recall-401/run-10-m0628/`，`TYPED-RECALL-401-RUN-04.md`）。
> 用户授权：技术取舍由执行者裁决并记录，不回问。本文 §3 为逐项裁决。

## 0. 一句话结论

**PASS 227 → 271 / FAIL 0 → 0 / BLOCKED 174 → 130**（public 217 → 261，source 10 不变）。44 格由 BLOCKED 转 PASS，另 32 格 BLOCKED 原因改写为精确的公共契约/见证缺口，没有任何一格由 PASS 退化。runner 退出码仍为 3（整体 `NOT_RUN/BLOCKED`，130 格未闭合，属预期）。

| 阻塞类别 | run-10 | run-11 | 变化 |
|---|---:|---:|---:|
| `EXECUTOR_UNIMPLEMENTED` | 30 | 20 | −10：8 格 PASS（authority 事件）；3 格改判为公共契约冲突→FIXTURE；`procedure:observed_behavior:repeated_observation` 由前置拒绝（FIXTURE）改为「晋升路径未执行」计入本类（+1） |
| `ORACLE_GAP` | 48 | 24 | −24：20 格 PASS（conflict 拒绝 4、解析器 4、状态格 6、基线/重放 5、conflicting-replay 1）；3 格 contest 改判契约冲突→FIXTURE；`prospective-trigger-missing` 归类修正→FIXTURE；余 24 = conflict 泛化码 3 + page 4 + durable hash 6 + short 6 + vector 3 + current-use 2 |
| `FIXTURE_INVALID_OR_INSUFFICIENT` | 96 | 86 | −10：16 格 PASS（epistemic unverified 6、procedure 非显式 epistemic 5、procedure 生命周期 5）；+7 改判入本类（authority 3、contest 3、trigger-missing 1）；−1 移出（promotion path）；另 18 格前置拒绝原因改为 SDK 精确码/签名缺口 |
| 合计 | 174 | 130 | −44 |

## 1. 本轮改动（全部在 runner 层，未改 sealed 格定义、未放松任何断言）

### 1.1 新执行器 + 新 oracle：`current-use/authority:*`（8 格 PASS）

- `adapters/typed_recall_authority_event_cases.py`（新）：复用已审的两项 Context-use 基座（真实 admission → 两条 mutation → 两项 recall → 逐项整页 page → fragment → 首次 `authorize_recall_context_use` 收据），在「首次使用」与「新尝试」之间插入**一个真实公共事件**：
  - `revoke`：`suppress(memory1)` → 中间 recall（只剩 item2，epoch+1）→ `revoke_suppression(directive)`；
  - `supersede` / `contest` / `classification_change`：对被绑定 head 做真实授权的 supersede（replacement 3.13）/ contest（challenger 3.12，无 correction grant）/ revise（同 payload、`proposed_privacy_class=restricted`）；
  - `result_expiry`：item1 携带 `valid_until` = 冻结 `authority_expires_at`（10:01:00），context 过期设为其后 60 s（Memory 绑定 `authority_expires_at = min(context.expires_at, source valid_to)`，只有 context 晚于结果边界时结果边界才可观测——runner 选择，记录于输入）；
  - `context_expiry`：context 过期 = 冻结 10:00:30；
  - `short_source_invalidation` / `short_source_expiry`：第二项改为真实短时域 chunk（11 个公共 conversation registration + `rebuild_short_horizon_projection`，目标文本 `continue task A preferred_python`），事件分别为 `suppress(evidence=conversation-evidence-1)` 与「时钟推到 chunk 五天 TTL 边界 + 公共投影重建移除 chunk」。
  - 过期三格额外做**边界前一微秒的正控制**（accepted）与**边界处的拒绝**；历史 exact replay 在 context 仍有效时取得。
- `adapters/typed_recall_context_use_cases.py`：`context_bundle` 按 selected source_kind 生成 fragment（短时域 → `ContextFragmentType.SHORT_HORIZON`、`source_revision=None`，Harness `ContextFragmentV2` 契约）；认知项路径不变。
- `runners/typed_recall_authority_event_oracle.py`（新，纯标准库）：`inputs()` 只编译构造输入（时间来自 fixture `authority_event_cases` 行与 `authority_event_common_binding`），`assess()` 逐项校验两项来源/receipt/证据链、bundle（复用 context-use oracle）、首次收据 epoch/policy、事件 mutation 的 kind/target/payload/grant、新尝试被拒、`after` 新鲜 recall 的 epoch 增量 = fixture 行 `after_epoch - before_epoch`（revoke 相对抑制后再 +1）、policy 不变、未受影响项保留/受影响项退出、历史 exact replay 零读。
- `runners/typed_recall_context_use_oracle.py`：`check_bundle` 按 source_kind 校验 fragment 类型与 revision 判别。
- **reason 映射（执行前由 pinned SDK 源码 `authorize_recall_context_use` 固定）**：Memory 对 epoch 变化、policy 变化、`now >= authority_expires_at` 三种情况只抛同一个稳定公共码 `RECALL_AUTHORITY_STALE`。原 `RECALL_RESULT_EXPIRED` / `RECALL_CONTEXT_EXPIRED` 由**被见证的条件**区分：epoch 不变 + `use_at == result.authority_expires_at < context.expires_at`（结果边界）/ `use_at == context.expires_at == result.authority_expires_at`（context 边界）+ 边界前一微秒正控制通过。映射表写在 oracle 模块 docstring。

### 1.2 epistemic 轴：runner 选择合法创建状态（6 格 PASS，另 5 格 procedure PASS）

- `runners/typed_recall_normal_inputs.py` 新增 `epistemic_seed_state()`：`llm_inference` / `unknown` 的种子改为 Memory 允许的非权威状态（procedure→`draft`，其余→`candidate`）；非 `explicit_user` 的 procedure 种子改为 `draft`（Memory 拒绝在 create 时激活非显式 procedure）。这是 RUN-03 §2 已指出的「默认 active 由 runner 选择」问题的修正，格定义未变。
- 效果：`epistemic:{episode,semantic,procedure}:{llm_inference,unknown}:unverified` 6 格真实进入召回门（`candidate_query_count=1`、`no_recall`/`recall_no_eligible_memory`、filtered 0）→ PASS（prospective 的 2 格因 candidate 状态无法建立 scheduler 注册而保持 BLOCKED，见 §1.5）；`procedure:observed_behavior:{source_bound,source_verified,unverified,user_confirmed}` + `procedure:verified_external:source_verified` 5 格以 draft + 真实 applicability 快照进入召回门 → INELIGIBLE 得证 → PASS。
- Memory 源码 `_cognitive_recall_state_allowed` 的资格表与 fixture `epistemic_verification_cases` 逐条一致（episode/semantic 5 组合、procedure 3 组合、prospective 仅 explicit_user），即召回门本身独立于生命周期地拒绝这些 epistemic 组合；候选/草稿状态只是唯一可公共构造的持久状态。
- `procedure:observed_behavior:repeated_observation`（fixture 期望 ELIGIBLE_WITH_APPLICABILITY）：draft + 快照已真实执行，但 draft→active 的观察晋升路径未实现 → oracle 显式 BLOCKED `OBSERVED_PROCEDURE_ACTIVATION_PROMOTION_PATH_NOT_EXECUTED`（不再是前置拒绝）。

### 1.3 Procedure applicability 快照推广到全部生命周期状态（5 格 PASS）

- `adapters/typed_recall_procedure_cases.py`：`supported()` 覆盖 draft/eligible/active/reinforced/revised/inapplicable/superseded（非显式 epistemic 仅 draft），快照 intent 的 `transition_from/to` 用实际最终状态；`runners/typed_recall_procedure_oracle.py` 同步。
- 实测 Memory 对上述每个状态的 `APPLICABILITY_SNAPSHOT` 均落一条新 revision（`procedure_applicability_bound`），召回：reinforced → 1 项、其余 → 0 项，与 fixture `lifecycle_cases` 一致。`eligibility/procedure-{draft,inapplicable,reinforced,revised,superseded}` PASS。

### 1.4 已有观测格的完整准入（a2 oracle 补断言，附执行器补见证）

| 格 | 补齐内容 | 结果 |
|---|---|---|
| `eligibility/valid-until-null-unbounded` | 执行器记录 `execution/replay` wire、`mutation_plan`、`apply_result(+hash)`；oracle 加：完整公共 hash/身份绑定（`check_execution_wire`）、durable exact replay 零读、冻结时钟（`decided_at/evaluated_at == now`）、filtered=1、classification、mutation 操作（create/active/explicit_user/source_bound/`valid_time_interval={valid_from, null}`/payload=完整 v5 claim）与 apply 结果/receipt/plan hash 绑定 | PASS |
| `unsupported-replay/exact-replay` | first/replay 的 wire 绑定、first 为新鲜单次候选查询、冻结 base_request 绑定、before/after control 在 7 张 final 表上精确 +1/0 增量 | PASS |
| `unsupported-replay/{event-only,combined-selector-mode,combined-all}` | 执行器补 replay；oracle 加 wire 绑定、rejected 计划的 durable exact replay、输入载体（memory_type + 原 selectors、默认 full_text、幂等键）绑定 | PASS |
| `unsupported-replay/conflicting-replay` | 执行器记录 context/plan/principal 与 `rejection_receipt`；oracle 校验 context/plan = 基线仅改 query、`check_rejection_witness`（stage idempotency、reason IDEMPOTENCY_CONFLICT、零候选读、request/context/plan hash 绑定） | PASS |
| `protocol/{strict-v3-rejected,invalid-source-discriminant,cognitive-missing-revision,short-fake-revision}` | Harness 严格解析器攻击：`memory_called=False`、calls 仅解析器、解析输入 = 真实 decision/selected item 仅改冻结字段、受保护 manifest 不变 → 零候选读由构造保证 | PASS |
| `eligibility/{current-head,stale-head,suppressed,ordinary-resolved,ordinary-contested}`、`conflict-state/contested-dependent-partial` | 执行器在唯一状态迁移前后各取一次 manifest；oracle 加：每个 revision 的 receipt/证据/action grant 链（contest 无 grant、resolved 标记）、迁移不写任何 final 表、mutation 表精确增量（revise/contest/resolve：revisions +1、heads 变化、authority events +1；suppress：revisions +0、heads 不变、events +1）、suppression request/decision 绑定 | PASS |
| `conflict-state/contest-{no-evidence,stale-target,cross-principal,cross-memory}` | 新 `REJECTION_MAP`（执行前按 Harness DTO 与 Memory apply 源码固定）：no-evidence → Harness `_MemoryMutationAdmissionError`（Memory 未被调用）；stale-target → `MemoryWriterConflict:cognitive_target_revision_stale`（target rev 6）；cross-principal/cross-memory → `MemoryValidationError:mutation_target_not_found`（principal-2 / mem-other）；加 r1..r7 完整 receipt/grant 链、受保护 mutation+final 表 hash 前后相等 | PASS |

### 1.5 BLOCKED 原因精确化（不改判定，32 格）

- `PUBLIC_CONTRACT_CONFLICT:`（归 FIXTURE 类）：`authority:policy_hash_change`（`policy_hash` 是 Memory 常量 `typed-recall-eligibility/v1`，无公共 policy 版本输入）、`authority:short_source_cleanup`（`cleanup_short_horizon` 仅在 backend port，`MemoryManager` 无入口）、`context:new-continuation`（`RecallContextUseAuthorizationRequestV1` 无 continuation 字段）、`contest-{nested,one-member,three-members}`（冲突组成员/嵌套是 Memory 内部结构，公共 contest 恒成一对）。
- `PUBLIC_WITNESS_UNAVAILABLE:`（归 ORACLE 类）：`contest-{same-content,evidence-not-distinct,active-group-exists}`（公共 apply 结果 reason_code 只有泛化的 `memory_mutation_validation_rejected`，原 DISTINCT_CONTENT/DISTINCT_EVIDENCE/ACTIVE_CONFLICT 无一对一公共见证；状态零增量与冻结输入已校验）；`protocol/{naked-source-ref,page-wrong-result-hash,page-wrong-coordinate,page-expired-result}`（`page_typed_recall_result` 拒绝不带 `TypedRecallRejectionV1`，原 `candidate_query_started=false` 无法从公共 API 证明）。
- epistemic 16 格前置拒绝原因由 `*_cannot_be_authoritative` 变为 SDK 精确码 `mutation_inference_must_be_unverified` / `mutation_unknown_must_be_unverified`（种子已是合法状态，剩余冲突只在 verification 轴）。
- `epistemic:prospective:{llm_inference,unknown}:unverified` 2 格：candidate 状态的 prospective 无法做 scheduler 注册（signal authority 不可建立）→ 由前置拒绝改为 `REQUIRED_APPLICABILITY_OR_SIGNAL_AUTHORITY_NOT_ESTABLISHED`。

### 1.6 bridge / 测试

- `runners/typed_recall_bridge.py`：workspace 复制新增 `typed_recall_authority_event_cases.py`；`execution_code_sha256` 纳入新 oracle；request inputs 新增 `authority_events`；阻塞归类新增 `PUBLIC_CONTRACT_CONFLICT:`/`CONSTRUCTION_CONFLICT` → FIXTURE、`PROMOTION_PATH_NOT_EXECUTED` → EXECUTOR。
- `adapters/typed_recall_public_manager.py`：分发 authority-event 格。
- 测试：新增 `tests/test_typed_recall_authority_event_public.py`（4 格真实执行 PASS + 2 格契约冲突 + 15 项篡改反例）、`tests/test_typed_recall_admission_oracles.py`（baseline/replay/unsupported/conflicting、解析器攻击、conflict 拒绝映射、epistemic 合法状态种子，各带反例）；`test_typed_recall_normal_bridge.py` 状态格改断 PASS 并加 3 项反例；`test_typed_recall_bridge.py` inputs 集合加 `authority_events`。

```bash
/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/.venv/bin/python -m pytest \
  /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.claude/worktrees/matrix-401/testcase/human-memory-program/tests -q -p no:cacheprovider
# 110 passed, 31 subtests passed + 1 failed（test_typed_recall_source_oracle 默认 checkout 路径按 worktree 相对推导不存在）
TYPED_RECALL_SOURCE_CHECKOUT=/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk-0628-source <同上仅 source 测试>
# 1 passed  → 合计 111 passed
```

## 2. 正式扫描（run-11）

### 2.1 命令（绝对路径；消费者 venv 与 SDK 源 worktree 沿用 RUN-04 §2.1/2.2；artifact 目录执行前不存在）

```bash
/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/.venv/bin/python -B \
  /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.claude/worktrees/matrix-401/testcase/human-memory-program/runners/run_typed_recall_public_consumer.py \
  --consumer-python /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/venv-m0628/bin/python \
  --artifact-dir /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/typed-recall-401/run-11-m0628-admissions \
  --harness-wheel /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/vendor/simple_harness_sdk-0.7.10-py3-none-any.whl \
  --harness-wheel-sha256 e559bc1b58ebfce0423247bc11ead0969f2364d76209fe7b9481bb2892ad2539 \
  --harness-source-commit 031fdc688ceea604ffa409a06a69fd85071aa612 \
  --memory-wheel /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/vendor/simple_harness_memory_sdk-0.6.28-py3-none-any.whl \
  --memory-wheel-sha256 16498e49a8494ae2a3cb24bb1325a80576e3b2063bc921f732568cbcf55436f9 \
  --memory-source-commit e554c20dd8699606d44ac5af3bc88958822b8f56 \
  --source-adapter /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.claude/worktrees/matrix-401/testcase/human-memory-program/adapters/typed_recall_source_cases.py \
  --source-checkout /Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk-0628-source \
  --child-timeout 300 \
  > /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/typed-recall-401/run-11.log \
  2> /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/typed-recall-401/run-11.stderr
# 退出码 3；stderr 为空；不传 --consumer-entrypoint（RUN-04 禁令）
```

### 2.2 结果

| 项 | 值 |
|---|---|
| artifact | `.local-test-evidence/2026-09-08/typed-recall-401/run-11-m0628-admissions/`（`run_id 520201753de74f48a9c8cd28671101bc`，10:55:15 → 10:55:51 UTC） |
| 候选身份 | Harness 0.7.10 / `031fdc68…` / `e559bc1b…`；Memory 0.6.28 / `e554c20d…` / `16498e49…`（与 pin 一致，`candidate_pin_differences=[]`） |
| 整体 | `NOT_RUN/BLOCKED`；`acceptance_counts` **PASS 271 / FAIL 0 / BLOCKED 130** |
| 层 | public：passed 261 / failed 0；source：passed 10 / failed 0（两层状态仍 `NOT_RUN/BLOCKED`，层级 PASS 规则未定义，同 RUN-03 §3） |
| 按 lane | conflict-state 8 PASS / 12 BLOCKED；current-use 12 / 5；eligibility 219 / 82；fault-recovery 7 / 0；protocol 4 / 7；selection-budget 2 / 24；unsupported-replay 19 / 0 |
| `fixture_sha256` / `execution_layers_sha256` | `3e4f23b7…` / `ab0d0ad7…`（不变） |
| `oracle_code_sha256`（a2） | `d8fe8c3c8beb2096f8eddd170981c0b577f3029a1905d56a9d9b6b38db376dc8` |
| 新增执行代码 sha | `typed_recall_authority_event_cases.py` `a1cf512d…`；`typed_recall_authority_event_oracle.py` `e17f72f1…` |
| bridge-summary / public-observations / source-observations sha256 | `be2e53de…` / `b47c949d…` / `e5abef4c…` |

## 3. 逐格对照 run-10 → run-11（76 格变化；其余 325 格状态与 reason 逐字相同）

### 3.1 BLOCKED → PASS（44 格）

- **current-use（8）**：`authority:{revoke,supersede,contest,classification_change,result_expiry,context_expiry,short_source_invalidation,short_source_expiry}`（原 `CELL_EXECUTOR_NOT_IMPLEMENTED`）。
- **eligibility/epistemic（11）**：`episode:llm_inference:unverified`、`episode:unknown:unverified`、`semantic:llm_inference:unverified`、`semantic:unknown:unverified`、`procedure:llm_inference:unverified`、`procedure:unknown:unverified`（原 `*_cannot_be_authoritative` 前置拒绝）；`procedure:observed_behavior:{source_bound,source_verified,unverified,user_confirmed}`、`procedure:verified_external:source_verified`（原 `mutation_observed_procedure_cannot_activate`）。
- **eligibility/procedure 生命周期（5）**：`procedure-{draft,inapplicable,reinforced,revised,superseded}`（原 `REQUIRED_APPLICABILITY_OR_SIGNAL_AUTHORITY_NOT_ESTABLISHED`）。
- **状态格（6）**：`eligibility/{current-head,stale-head,suppressed,ordinary-resolved,ordinary-contested}`、`conflict-state/contested-dependent-partial`（原 `STATE_COMPLETE_RECEIPT_AND_PROTECTED_TRANSITION_BINDING_PENDING`）。
- **conflict 拒绝（4）**：`contest-{no-evidence,stale-target,cross-principal,cross-memory}`（原 `CONFLICT_REJECTION_OR_PRECONDITION_REQUIRES_FULL_ORACLE`）。
- **protocol 解析器（4）**：`strict-v3-rejected`、`invalid-source-discriminant`、`cognitive-missing-revision`、`short-fake-revision`（原 `RETURN_CELL_COMPLETE_ATTACK_AND_READ_WITNESS_ADMISSION_PENDING`）。
- **基线与重放（6）**：`eligibility/valid-until-null-unbounded`、`unsupported-replay/{exact-replay,event-only,combined-selector-mode,combined-all}`（原 `FULL_ORIGINAL_CELL_ADMISSION_PENDING`）；`unsupported-replay/conflicting-replay`（原 `PUBLIC_EXCEPTION_HAS_NO_PER_INVOCATION_CANDIDATE_READ_WITNESS`）。

8 + 11 + 5 + 6 + 4 + 4 + 6 = 44，与 `acceptance_counts` 的 PASS 增量一致。

### 3.2 BLOCKED → BLOCKED，reason 改写（32 格）

| 格 | run-10 reason | run-11 reason（类别） |
|---|---|---|
| `authority:policy_hash_change`、`authority:short_source_cleanup`、`context:new-continuation` | `CELL_EXECUTOR_NOT_IMPLEMENTED` | `PUBLIC_CONTRACT_CONFLICT:…`（FIXTURE） |
| `contest-{nested,one-member,three-members}` | `CONFLICT_REJECTION_OR_PRECONDITION_REQUIRES_FULL_ORACLE` | `PUBLIC_CONTRACT_CONFLICT:…`（FIXTURE） |
| `contest-{same-content,evidence-not-distinct,active-group-exists}` | 同上 | `PUBLIC_WITNESS_UNAVAILABLE:apply result reason_code is the generic …`（ORACLE） |
| `protocol/{naked-source-ref,page-wrong-result-hash,page-wrong-coordinate,page-expired-result}` | `RETURN_CELL_COMPLETE_ATTACK_AND_READ_WITNESS_ADMISSION_PENDING` | `PUBLIC_WITNESS_UNAVAILABLE:page_typed_recall_result rejections carry no per-invocation candidate-read witness`（ORACLE） |
| `epistemic:{4 类}:llm_inference:{source_bound,user_confirmed}`（8） | `…mutation_inference_cannot_be_authoritative` | `…mutation_inference_must_be_unverified`（FIXTURE） |
| `epistemic:{4 类}:unknown:{source_bound,user_confirmed}`（8） | `…mutation_unknown_cannot_be_authoritative` | `…mutation_unknown_must_be_unverified`（FIXTURE） |
| `epistemic:prospective:{llm_inference,unknown}:unverified`（2） | 前置拒绝 | `REQUIRED_APPLICABILITY_OR_SIGNAL_AUTHORITY_NOT_ESTABLISHED`（FIXTURE） |
| `epistemic:procedure:observed_behavior:repeated_observation` | `…mutation_observed_procedure_cannot_activate` | `OBSERVED_PROCEDURE_ACTIVATION_PROMOTION_PATH_NOT_EXECUTED`（EXECUTOR） |

### 3.3 原始观测层

与 run-10 一样，每轮的 `sqlite-human-memory:init-<随机>` authority_ref 与 rejection `invocation_id`（UUID）带来 hash 级差异，oracle 只看 hash 间关系，不看绝对值（RUN-04 §4.2）。本轮判定变化全部可归因于 §1 的 runner 层改动，`execution_code_sha256` 中变化的文件即 §1 列出的文件。

## 4. 剩余 130 格 BLOCKED（按原因分组）

### 4.1 格定义与公共契约冲突 —— 需要 program 侧裁决（FIXTURE 类 86 格）

| 原因 | 格数 | 说明 / 建议 |
|---|---:|---|
| `…audit disclosure requires an AuditAccessDecision and audit recipient` | 24 | `disclosure:{6 recipient}:AUDIT:*`：普通 recipient 携带 purpose=AUDIT 在 Harness DTO 层即不可构造；NORMAL-EXECUTION.md 禁止改 recipient 为 AUDIT_REVIEWER。需 program 侧重定义（如改为「AUDIT 只能配审计 recipient，其余组合为 DTO 拒绝」并单列）。 |
| `…verified states require trusted typed observation evidence` | 16 | `llm_inference/unknown × {source_verified,repeated_observation}`：Memory 要求这两种 epistemic 必须 `unverified`，组合不可存在。 |
| `…verified_external requires source_verified state` | 16 | `verified_external × 其余 4 个 verification`：Memory `mutation_verified_external_state_invalid`。 |
| `…mutation_inference_must_be_unverified` / `…mutation_unknown_must_be_unverified` | 8 + 8 | `llm_inference/unknown × {source_bound,user_confirmed}`：同上，组合不可存在。以上 48 格 epistemic 轴的 `unlisted_expected: INELIGIBLE` 在 SDK 中以入口拒绝实现；若 program 侧接受「入口拒绝 = 该组合永不可召回」的证明形式，可整体转 PASS（这是定义层决定，本轮不越权）。 |
| `PUBLIC_CONTRACT_CONFLICT`（6） | 6 | `policy_hash_change`（SDK 常量）、`short_source_cleanup`（无 Manager 入口）、`new-continuation`（无字段）、`contest-{nested,one-member,three-members}`（内部结构）。 |
| `REQUIRED_APPLICABILITY_OR_SIGNAL_AUTHORITY_NOT_ESTABLISHED` | 2 | `epistemic:prospective:{llm_inference,unknown}:unverified`：candidate 状态的 prospective 不产生 scheduler 注册命令，signal authority 无法建立；若 program 侧允许「无注册即不可召回」作为该格证明，可转 PASS。 |
| `FULL_SOURCE_RECORD_CANARY_AND_CROSS_SCOPE_SETUP_NOT_ESTABLISHED`（含组合） | 4 | `selection-budget/projection:{semantic,episode,procedure,prospective}`：`extra_typed_field` canary 在严格 DTO 中不可表示；cross-scope 需要 task-scope 起源接线；episode 区间投影差异。 |
| `CONSTRUCTION_CONFLICT`（prospective-trigger-missing）、`FROZEN_128_BYTE_PAGE_BOUND`（page-correct-binding） | 2 | 既有结论不变。 |

### 4.2 公共 API 无对应见证 —— 需要 Memory/Harness 增加公共可观测性（ORACLE 类 24 格）

| 原因 | 格数 | 缺口 |
|---|---:|---|
| `CONFLICT_DURABLE_GROUP_MEMBER_RESOLUTION_HASH_ORACLE_PENDING` | 6 | 冻结的 group/member/resolution 私有 hash（含 created_at/plan id）不在公共 receipt/manifest 中（ORACLE-REVISION-PROPOSAL §3.4 已定：不得读私表）。 |
| `SHORT_COMPLETE_REGISTRATION_CLASSIFICATION_TIME_AND_ORIGINAL_HASH_BINDINGS_PENDING` | 6 | 公共投影 content 为 `user: ` + 文本、TTL 固定五天，与冻结 payload_hash / 任意 expires_at 不符（NORMAL-EXECUTION.md 明定保持 BLOCKED）。 |
| `PUBLIC_WITNESS_UNAVAILABLE`（page 4 + conflict 3） | 7 | `page_typed_recall_result` 无 rejection receipt；apply 结果 reason_code 泛化。 |
| `PUBLIC_EXECUTED_LANE_WITNESS_UNAVAILABLE` | 3 | 无「实际执行 lane」公共见证。 |
| `CURRENT_USE_HARNESS_RESERVATION_EXACT_ONCE_WITNESS_UNVERIFIED` | 2 | Host 侧 exact-once 见证，不在 Memory 公共 API。 |

### 4.3 执行器未实现（EXECUTOR 类 20 格）

| 格 | 需要什么 |
|---|---|
| `eligibility/short-{registration,classification}-invalid` | 需确认 Memory 在召回时是否重解析 conversation registration / item classification（Host authority port 可返回失配），未调查。 |
| `selection-budget/budget:greedy` | 两条字节数 200/120 的语义记忆且超大者先排（需控制 full_text lane 内排序），oracle 需从 Harness `truncated` 契约定义。 |
| `selection-budget/budget:deadline` | deadline 用 `time.monotonic()`，公共 builder 的 `clock` 不覆盖 → 只能真超时，非确定性；很可能只能列为契约冲突。 |
| `selection-budget/budget:short-{emoji,escaping}`、`projection:short_horizon` | 公共短时域 payload 带 `user: ` 前缀且 `occurred_at` 为数值，冻结字节/hash 无法命中；可执行后以契约差异 BLOCKED。 |
| `selection-budget/dedupe:*`（4）、`ranking-order`、`tie-*`（7） | 候选注入/多 lane（vector/entity/task_scope/temporal）为 Memory 内部行为；公共可构造的疑似只有 `tie-source-kind`（认知 vs 短时域同分）与 `tie-newer-source-time`（不同类型各自 lane rank 1）。 |
| `epistemic:procedure:observed_behavior:repeated_observation` | draft→active 的观察晋升路径（`record_procedure_observation` TERMINAL_OUTCOME 成功计数）。 |

## 5. 裁决记录（用户委托的技术取舍）

1. **epistemic 种子状态**：格 ID 只约束 epistemic×verification；lifecycle 由 runner 选择（RUN-03 已记）。选 Memory 唯一允许的 candidate/draft，让召回门真实执行；Memory 召回门 `_cognitive_recall_state_allowed` 本身按 epistemic 排除，不依赖生命周期，故证明不被混淆。
2. **过期码映射**：Memory 公共只有 `RECALL_AUTHORITY_STALE`；以「被见证条件 + 边界前一微秒正控制」区分结果/上下文过期，并把映射表写入 oracle 源码。未新造任何 reason 字符串。
3. **result_expiry 的 context 过期时间**：fixture 通用绑定（context 10:00:30 早于 authority 10:01:00）在 Memory `min()` 契约下不可实现；为隔离结果边界，把 context 过期设为结果边界后 60 s，作为 runner 输入记录。
4. **short_source_expiry**：Memory 只在严格晚于过期时刻才移除 chunk（召回在边界即排除）；投影重建与新尝试取边界后一微秒，两种见证（结果授权边界 = chunk 过期、removed_chunk_count=1 且 epoch+1）都记录。
5. **conflict 拒绝映射**：PRINCIPAL_MISMATCH 与 MEMORY_MISMATCH 都映射到 Memory fail-closed 的 `mutation_target_not_found`，以攻击输入（principal-2 / mem-other）区分；泛化码的三格不冒充一对一映射，保持 BLOCKED。
6. **解析器四格准入**：Harness 层拒绝、Memory 未被调用 + 受保护 manifest 不变，等价于零候选读；page 四格因无 receipt 保持 BLOCKED。
7. **不改 fixture**：全部改动不触及 sealed 格定义、攻击、阈值、oracle 输入与 pin；fixture/layers sha 与 bridge 常量不变。
8. 未改 `ARCHITECTURE/`、`backend/`、Host pin、桌面应用；未运行 18120 端口。

## 6. 后继

- 4.1 的 48 格 epistemic 组合与 24 格 AUDIT 组合是最大的一块，需要 program 侧一句话裁决「入口拒绝是否算该格证明」；若接受，PASS 可到 343。
- 4.2 的公共见证缺口可作为 Memory 0.6.29+ 的小增量提案：page 拒绝附 `TypedRecallRejectionV1`、apply 结果携带精确 validation reason、`cleanup_short_horizon` 走 Manager。
- 4.3 中 `tie-source-kind` / `tie-newer-source-time` / `budget:greedy` 值得下一轮实现；其余多为内部行为。
