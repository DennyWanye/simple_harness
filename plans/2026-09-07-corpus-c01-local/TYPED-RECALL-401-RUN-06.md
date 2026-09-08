# 401 矩阵重 pin 到 Memory 0.6.31 与正式扫描（run-12）

> 2026-09-08。分支 `worktree-matrix-401-r2`（rebase 到 Host main `f7b14325`，即「采用 Memory 0.6.31」提交；worktree 无历史提交，rebase 无冲突）。
> 上一轮同类记录：`TYPED-RECALL-401-RUN-04.md`（run-10 重 pin 到 0.6.28）、`TYPED-RECALL-401-RUN-05.md`（run-11 接线，PASS 271）。
> 本轮对照基线：run-11（M0.6.28，fixture rev 15 / layers rev 13，`.local-test-evidence/2026-09-08/typed-recall-401/run-11-m0628-admissions/`）。
> 只改 `testcase/human-memory-program/` 与本记录；未动 `backend/` 源码、`ARCHITECTURE/`、桌面应用、18120 端口；未执行 `uv sync`；未改 Host pin。

## 0. 一句话结论

**PASS 271 → 268 / FAIL 0 → 3 / BLOCKED 130 → 130。** 401 格里恰好 **3 格**发生变化，全部集中在 `current-use/authority:*`，全部可归因到 0.6.29 与 0.6.30 的两项真实行为变更；其余 **398 格逐字节相同**。runner 退出码由 3 变为 1（整体 `FAIL`），这是本轮重 pin 的真实信号，不是环境问题。三格的处置在 `TYPED-RECALL-401-RUN-07.md`（run-13）完成。

| 变化格 | run-11 (M0.6.28) | run-12 (M0.6.31) | 归因 |
|---|---|---|---|
| `current-use/authority:short_source_expiry` | PASS | **FAIL** `short projection did not project exactly the oldest target` | 0.6.30 给 `ShortHorizonProjectionBuildResult` 加了 `split_group_count` / `truncated_group_count` 两个字段，oracle 对整个 dict 做等值比较 |
| `current-use/authority:short_source_invalidation` | PASS | **FAIL** 同上 | 同上 |
| `current-use/authority:revoke` | PASS | **FAIL** `receipt public hash/epoch/policy/request/time differs` | 0.6.29 用途围栏：revoke 之后被绑定来源全部恢复，`authorize_recall_context_use` 不再拒绝而是签发收据，封存行要求的 `RECALL_AUTHORITY_STALE` 不再可产生 |

## 1. 本轮改动的 pin

跳过 0.6.29 与 0.6.30（Host 未单独 pin，0.6.31 已包含两者），矩阵直接从 0.6.28 重 pin 到 0.6.31。

| 对象 | 文件 | 旧值 | 新值 |
|---|---|---|---|
| fixture 版本 | `fixtures/typed-recall-v3.json` | rev 15 | **rev 16** |
| fixture 自身 sha256 | 同上 | `3e4f23b7a72bc271fc8a225ccf9cb862eb3be6bf6ee505d100c95d105f184d5a` | **`31fbb8bc8186143f5a06e77af42b36b56de8bb36e40ffa65edb5f60c013225c9`** |
| Memory 候选身份（`public_consumer.candidate_identity_pins.memory` 与 `approved_oracle.candidate_identity.memory` 两处逐字相同） | 同上 | 0.6.28 / `e554c20dd8699606d44ac5af3bc88958822b8f56` / wheel `16498e49a8494ae2a3cb24bb1325a80576e3b2063bc921f732568cbcf55436f9` | **0.6.31 / `ff8be5f38e6b7e925f54bcc9f4f5dc65d450b3db` / wheel `e8dc27cc7585a34e38f99a5b94ba1d5b500dcde537548a0cfc2dc85ae72b9896`** |
| Harness 候选身份 | 同上 | 0.7.10 / `031fdc688ceea604ffa409a06a69fd85071aa612` / `e559bc1b…` | 不变 |
| `candidate_pin_lineage` | 同上 | 末项 rev 14（M0.6.26） | 追加 **rev 15（fixture sha `3e4f23b7…`，M0.6.28）** |
| `candidate_pin_change` | 同上 | scope「0.6.26 → 0.6.28」 | scope 改写为「0.6.28 → 0.6.31」（见 §1.1）；`source_commit`/`wheel_sha256` 同步；date 仍 2026-09-08 |
| layers 版本 | `fixtures/typed-recall-execution-layers-v1.json` | rev 13 | **rev 14** |
| `typed_recall_fixture_revision` / `typed_recall_fixture_sha256` | 同上 | 15 / `3e4f23b7…` | **16 / `31fbb8bc…`** |
| `clean_wheel_public_manager.candidate_memory_identity` | 同上 | 0.6.28 三元组 | **0.6.31 三元组** |
| `candidate_pin_previous_layers_sha256` | 同上 | `2f483112…`（rev 12 自身 sha） | **`ab0d0ad7f27b213cd4e243f93bee54f72a3bbf9f94fc93eaf125570619faa022`**（rev 13 自身 sha） |
| `candidate_pin_layers_lineage` | 同上 | 末项 `db7a68a4…`（9 项） | 追加 **`2f483112…`**（10 项） |
| layers 文件自身 sha256 | 同上 | `ab0d0ad7…` | **`83238bc6691ba6b1c70a2aa7e0b89a2316f38475f2901b0f35524914389a42aa`** |
| bridge 常量 `FIXTURE_SHA` / `LAYERS_SHA` | `runners/typed_recall_bridge.py` | `3e4f23b7…` / `ab0d0ad7…` | **`31fbb8bc…` / `83238bc6…`** |
| source oracle `PINNED_SCHEMA_HASH` / `PINNED_COLUMNS_PK_HASH` | `runners/typed_recall_source_oracle.py` | `9702ea1e…` / `7a5ad1d1…` | **不变**（见 §1.2），只更新血缘注释与两条错误文案 M0628→M0631 |
| bridge 自回归 pin 断言 | `tests/test_typed_recall_bridge.py:376` | `("0.7.10", "0.6.28")` | **`("0.7.10", "0.6.31")`** |
| source 测试默认 checkout | `tests/test_typed_recall_source_oracle.py:23` | `simple-harness-memory-sdk-0628-source` | **`simple-harness-memory-sdk-0631-source`** |
| 安装版本断言 | `tests/test_typed_recall_fixture_authorities.py:271` | `"0.6.28"` | **`"0.6.31"`**（`backend/.venv` 现状已是 0.6.31，由 Host 采纳提交 `f7b14325` 带来；`backend/` 本轮未动） |

401 格集合、攻击集合、oracle 输入与阈值均未变；本次是纯候选身份 pin。

### 1.1 `candidate_pin_change.scope` 新文案（英文原文照录）

> Candidate identity only: Memory 0.6.28 -> 0.6.31 (0.6.29 replaces the recall-context-use authority-epoch equality test with a non-regression test, so an advanced epoch is admitted only when the per-bound-source revalidation inside the same write lock and the same BEGIN IMMEDIATE transaction passes, and otherwise still rejects with the same stable RECALL_AUTHORITY_STALE code, zero payload and zero receipt row; policy_hash mismatch, expiry and epoch regression remain hard failures, and a read-only read_recall_context_use_authority_notes view plus the authority_epoch_advanced code are added without touching the receipt DDL or hash domain. 0.6.30 caps a short-horizon chunk at SHORT_HORIZON_CHUNK_MAX_CHARS = 2048 code points, deterministically segmenting an over-long causal group into at most SHORT_HORIZON_CHUNK_MAX_SEGMENTS = 8 content-addressed chunks stored under a projection key that joins the causal group id and the segment ordinal with U+001F; unsegmented groups keep the bare Host id and the byte-identical chunk_id payload, projection rebuild becomes incremental and generation rebuild reuses active-generation vectors for unchanged chunks. 0.6.31 narrows conflict-group admission from any-lane hit to slot-level relevance: lexical matching sees only contested_slot_text (the public fields whose values differ between the two members, plus the semantic predicate slot name), and the vector lane requires member cosine >= 0.45 and not below any ordinary candidate of the same type, so an unrelated query no longer short-circuits the whole typed-recall lane; a non-admitted group is not a candidate, is not counted in filtered_candidate_count and does not set truncated. History visibility may now bind confirmation-group members by result_member_hash with whole-group atomicity, while typed-short source expansion still rejects them. Public payload shape, hash domain, eligibility gates, 0.45 threshold, lane budgets, Harness v4 wire shape, build_host_confirmation_execution, recall_authority_events/heads DDL and memory schema 7.4 unchanged, no DDL change; memory-only forget, CJK lexical gate and prospective trigger rendering retained). Original 401 cells, attacks, oracle inputs and thresholds unchanged.

### 1.2 source oracle 描述符按 0.6.31 fresh 根复核（含 0.6.30 无 DDL 变更的核实）

用 oracle 自身的 `capture()` 对一个由 0.6.31 wheel 新建（`SQLiteHumanMemoryBackend(path)` + `initialize()`）的空库重算：

```
memory version   0.6.31
tables           94
schema_hash      9702ea1ecf969324d138418abafe1efafad6464d479b21c03ff1cd1ba7394aab
columns_pk_hash  7a5ad1d179a8eea7fe1dc39dc72f1a414e8ad3c77e799737abadec0d34cb4384
```

与 0.6.23/0.6.24/0.6.25/0.6.26/0.6.28 的 pin **逐字相同**，因此两个常量不动。

**0.6.30「短时域投影键」的核实（本轮要求专门验证的一点）**：0.6.30 改的是 `short_horizon_chunks.primary_conversation_id` 里**存的值**（分段组存投影键 `<causal_group_id>\x1f<k>/<K>`，未分段组仍存裸 Host id），不是 DDL —— `UNIQUE (principal_id, primary_conversation_id, causal_group_id)` 与全部列逐字不变，schema 7.5 明确推迟。fresh 根的 schema 描述符因此不变，与 CHANGELOG「无 DDL 变化（7.4 checksum 不变）」一致。这一结论已写进 `typed_recall_source_oracle.py` 的血缘注释。

## 2. 命令行（全部绝对路径）

### 2.1 SDK 源 worktree（source 层 10 格用）

`/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk-0631-source`（HEAD `ff8be5f38e6b7e925f54bcc9f4f5dc65d450b3db`，工作树 clean）由 Host 侧已建好，本轮直接复用，**按要求保留不删**。

### 2.2 消费者 venv（专用；不跑 `uv sync`、不动 pin）

```bash
uv venv --python 3.12 /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/venv-m0631

VIRTUAL_ENV=/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/venv-m0631 \
uv pip install --offline --no-deps \
  /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/vendor/simple_harness_sdk-0.7.10-py3-none-any.whl \
  /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/vendor/simple_harness_memory_sdk-0.6.31-py3-none-any.whl

VIRTUAL_ENV=/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/venv-m0631 \
uv pip install --offline httpx aiosqlite numpy pydantic structlog
```

装出的依赖集合与 run-10/run-11 的 `venv-m0628` **逐项相同**：aiosqlite 0.22.1 / annotated-types 0.8.0 / anyio 4.15.0 / certifi 2026.7.22 / h11 0.16.0 / httpcore 1.0.9 / httpx 0.28.1 / idna 3.19 / numpy 2.5.2 / pydantic 2.13.5 + pydantic-core 2.46.5 / structlog 26.1.0 / typing-extensions 4.16.0 / typing-inspection 0.4.4，Python 3.12.14。

### 2.3 正式 401 扫描（run-12）

artifact 目录执行前必须不存在；**不传 `--consumer-entrypoint`**（`typed_recall_bridge.py` 对该参数无条件抛 `product test-helper entrypoints are prohibited`，见 RUN-04 §2.4）。

```bash
/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/.venv/bin/python -B \
  /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.claude/worktrees/matrix-401-r2/testcase/human-memory-program/runners/run_typed_recall_public_consumer.py \
  --consumer-python /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/venv-m0631/bin/python \
  --artifact-dir /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/typed-recall-401/run-12-m0631 \
  --harness-wheel .../simple_harness_sdk-0.7.10-py3-none-any.whl \
  --harness-wheel-sha256 e559bc1b58ebfce0423247bc11ead0969f2364d76209fe7b9481bb2892ad2539 \
  --harness-source-commit 031fdc688ceea604ffa409a06a69fd85071aa612 \
  --memory-wheel .../simple_harness_memory_sdk-0.6.31-py3-none-any.whl \
  --memory-wheel-sha256 e8dc27cc7585a34e38f99a5b94ba1d5b500dcde537548a0cfc2dc85ae72b9896 \
  --memory-source-commit ff8be5f38e6b7e925f54bcc9f4f5dc65d450b3db \
  --source-adapter .../adapters/typed_recall_source_cases.py \
  --source-checkout /Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk-0631-source \
  --child-timeout 300 \
  > .../typed-recall-401/run-12.log 2> .../typed-recall-401/run-12.stderr
# 退出码 1（整体 FAIL）；stderr 为空
```

### 2.4 runner 自回归（重 pin 后、run-12 前）

```bash
TYPED_RECALL_SOURCE_CHECKOUT=/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk-0631-source \
/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/.venv/bin/python -m pytest \
  <worktree>/testcase/human-memory-program/tests -q -p no:cacheprovider
# 1 failed, 110 passed, 31 subtests passed
```

唯一失败项 `test_typed_recall_authority_event_public.py::test_public_events_pass_and_independent_negatives_fail`，就是 §0 表里 `authority:revoke` 的同一根因（该测试用 `backend/.venv` 的 0.6.31 真实执行）。**这条失败是重 pin 的真实产出，不是接线遗漏**；修复在 RUN-07。

## 3. 扫描结果（run-12）

| 项 | 值 |
|---|---|
| artifact 目录 | `.local-test-evidence/2026-09-08/typed-recall-401/run-12-m0631/`（548 MB，`.gitignore` 已排除） |
| 汇总副本 | `.local-test-evidence/2026-09-08/typed-recall-401/run-12.log`（= `run-12-m0631/bridge-summary.json`） |
| `run_id` | `74cc87a463a84ccfbeab86cdd9eccb2a` |
| 起止 | 2026-09-08T15:24:59.000469Z → 15:25:38.496139Z（39 s） |
| `status` | **`FAIL`**（退出码 1） |
| `acceptance_counts` | **PASS 268 / FAIL 3 / BLOCKED 130**（`required_cells` 401） |
| `candidate_pin_differences` | `[]`（无 pin 偏差，非 observe 模式） |
| `isolation` | `isolated-process-verified-installed-wheels` |
| 子进程实测版本 | harness 0.7.10、memory **0.6.31**，Python 3.12.14 |
| 分层 | public：PASS 258 / FAIL 3 / BLOCKED 22（层状态 FAIL）；source：PASS 10 / FAIL 0 |
| 按 lane | conflict-state 8/12/0；current-use 9/5/**3**；eligibility 219/82/0；fault-recovery 7/0/0；protocol 4/7/0；selection-budget 2/24/0；unsupported-replay 19/0/0（PASS/BLOCKED/FAIL） |
| `fixture_sha256` / `execution_layers_sha256` | `31fbb8bc…` / `83238bc6…` |
| `oracle_code_sha256`（a2） | `d8fe8c3c8beb2096f8eddd170981c0b577f3029a1905d56a9d9b6b38db376dc8`（与 run-11 相同——本轮未改 a2 oracle） |
| bridge-summary / public-observations / source-observations sha256 | `a8711aeb…` / `e1742a03…` / `cb3e0696…` |

130 个 BLOCKED 的分类与 run-11 **完全一致**（`EXECUTOR_UNIMPLEMENTED` 20、`FIXTURE_INVALID_OR_INSUFFICIENT` 86、`ORACLE_GAP` 24），逐条 reason 逐字相同。

## 4. 逐格对照 run-11 → run-12：只有 3 格变化，逐格归因

对 401 格的 `cell_results` 做规范化 JSON 比较：格 ID 集合相同，**398 格逐字节相同**（含 `business_assertions`、`reason`、`blocker_categories`），3 格变化如下。

### 4.1 `current-use/authority:short_source_expiry`、`current-use/authority:short_source_invalidation`（PASS → FAIL）

失败断言：`short projection did not project exactly the oldest target`（`typed_recall_authority_event_oracle.py`）。原断言把 `rebuild_short_horizon_projection` 的返回值做**整 dict 等值比较**：

| 观测路径 | run-11（M0.6.28） | run-12（M0.6.31） |
|---|---|---|
| `projection_build`（两格） | `{"projected_chunk_count":1,"removed_chunk_count":0,"audit_id":"short-audit:…"}` | `{"projected_chunk_count":1,"removed_chunk_count":0,"split_group_count":0,"truncated_group_count":0,"audit_id":"short-audit:…"}` |
| `projection_rebuild.result`（仅 expiry 格） | `{"projected_chunk_count":0,"removed_chunk_count":1,"audit_id":…}` | 同上 + `split_group_count:0, truncated_group_count:0` |

归因：0.6.30 给 `ShortHorizonProjectionBuildResult` 追加了 `split_group_count` / `truncated_group_count`（默认 0，位置构造不变），执行器用 `dataclasses.asdict()` 原样记录，因此新字段直接进了观测；oracle 的等值比较随之不成立。

**这不是行为退化**：两格的短时域 chunk id 在两轮之间**逐字相同**（`short:901c156d4aaa12fe2e55f8386c6f83f3e6` / `short:23cdcf4d95624755931cf53a59349e2321`），`removed_chunk_count`、召回项、授权边界、epoch 关系全部不变——即 0.6.30 关于「未分段组的 `chunk_id` payload 与 0.6.29 逐字相同」的声明在本矩阵上得到独立验证。处置（把两个新字段作为**新增义务**钉成 0，而不是放松断言）见 RUN-07 §1.1。

### 4.2 `current-use/authority:revoke`（PASS → FAIL）

失败断言：`receipt public hash/epoch/policy/request/time differs`（`typed_recall_context_use_oracle.py::check_bundle` 的 `receipt['authority_epoch'] == result['authority_epoch']`）。

| 观测路径 | run-11（M0.6.28） | run-12（M0.6.31） |
|---|---|---|
| `uses.after_event.exception` | `{"type":"MemoryValidationError","reason":"RECALL_AUTHORITY_STALE"}` | **`null`** |
| `uses.after_event.receipt` | 不存在 | **存在**，`authority_epoch = 5` |
| `initial.execution.result.authority_epoch`（被绑定 epoch） | 3 | 3 |

归因：**0.6.29 的用途围栏**。该格的构造是「首次使用 → `suppress(memory1)` → 中间召回（只剩 item2，epoch+1）→ `revoke_suppression(directive)`（epoch 再 +1）→ 对**同一旧结果**发起新 attempt」。0.6.28 只比较 epoch 相等性，epoch 由 3 变 5 即拒绝；0.6.29 把它改成**倒退性**判据，epoch 前进时交给同一把写锁内的 `_validate_recall_context_use_sources_unlocked` 逐来源重校验——而 revoke 恰恰把被绑定的两条来源**完全恢复**到绑定时的状态，重校验全部通过，于是签发收据。运行日志里可见 0.6.29 新增的无载荷结构化日志 `typed_recall.context_use.authority_epoch_advanced authority_epoch=5 bound_authority_epoch=3 bound_source_count=2 reason_code=authority_epoch_advanced`。

**这是封存格定义与公共契约的真实冲突，不是候选缺陷**：`authority_event_cases` 中 `revoke` 行写死 `expected_old_result_use: "RECALL_AUTHORITY_STALE"`；而在 0.6.29 之后，**任何** revoke 都只会把绑定来源恢复原状，因此该封存期望在本候选上**不存在任何公共构造**能产生。相反的三类事件（supersede / contest / classification_change 改动被绑定 head，short_source_invalidation 压制被绑定证据，两个 expiry 走独立的过期硬判据）在 run-12 里仍全部 PASS，说明围栏只放宽了「净效果为恒等」的那一条车道。处置（改判为 `PUBLIC_CONTRACT_CONFLICT` 并补正向见证）见 RUN-07 §1.2 与 §4。

### 4.3 未变化的两类原始观测差异

与 RUN-04 §4.2 一致：每轮随机的 `sqlite-human-memory:init-<32 位随机 hex>` authority_ref 与 rejection `invocation_id`（UUID）带来 hash 级差异，oracle 只看 hash 之间的关系而非绝对值，判定不受影响。

另外，0.6.31 的**争议短路收窄**在本矩阵上**没有留下任何判定痕迹**：矩阵里唯一会产生 conflict group 的是 `conflict-state/*` 的 `preferred_python` 争议对，而这些格的查询词就是争议槽位本身（`preferred_python`），槽位级准入照常命中，`needs_user_confirmation` + `items=()` 的终态与 run-11 逐字相同。0.6.31 收窄的是「与争议槽位无关的查询不再被短路」，401 格里没有这样的负例格（这正是 RUN-07 §5 的后继建议之一）。

## 5. 历史对照

| run | Memory pin | fixture rev / layers rev | PASS | FAIL | BLOCKED | 整体 |
|---|---|---|---:|---:|---:|---|
| run-06 | 0.6.23（`78ddf386`） | 11 / 9 | 227 | 0 | 174 | NOT_RUN/BLOCKED |
| run-07 | 0.6.24（`3b51e0f6`） | 12 / 10 | 227 | 0 | 174 | NOT_RUN/BLOCKED |
| run-08 | 0.6.25（`b45db92c`） | 13 / 11 | 227 | 0 | 174 | NOT_RUN/BLOCKED |
| run-09 | 0.6.26（`9b148b96`） | 14 / 12 | 227 | 0 | 174 | NOT_RUN/BLOCKED |
| run-10 | 0.6.28（`e554c20d`） | 15 / 13 | 227 | 0 | 174 | NOT_RUN/BLOCKED |
| run-11 | 0.6.28（接线，pin 不变） | 15 / 13 | 271 | 0 | 130 | NOT_RUN/BLOCKED |
| **run-12** | **0.6.31（`ff8be5f3`）** | **16 / 14** | **268** | **3** | **130** | **FAIL** |
| run-13 | 0.6.31（接线，pin 不变） | 16 / 14 | 279 | 0 | 122 | NOT_RUN/BLOCKED |

（0.6.27 `0ccec4f9`、0.6.29、0.6.30 为中间候选，Host 未 pin，矩阵未单独扫描；其改动包含在 0.6.31 内。）

## 6. 裁决记录（用户委托的技术取舍）

1. **run-12 保持「原样重 pin」**：本轮**故意不**在扫描前修改任何 oracle，让 0.6.29/0.6.30 的行为变化以 FAIL 的形式如实暴露，run-12 因此是纯重 pin 的对照点。所有处置放到 run-13，两轮之间的差异全部可归因到 runner 层改动（RUN-07 §3）。
2. **schema 常量不动**：0.6.30 的投影键变化在值层不在 DDL 层，fresh 根描述符实测与 0.6.23 起一致（§1.2），因此 `PINNED_SCHEMA_HASH` / `PINNED_COLUMNS_PK_HASH` 保持不变；把「投影键是值不是 DDL」的判据写进 oracle 注释，供下一轮复核。
3. **不改 fixture 的格定义**：本轮只动候选身份 pin 与由它派生的 sha/rev，401 格集合、攻击、oracle 输入与阈值一字未改。
4. 未改 `ARCHITECTURE/`、`backend/`、Host pin、桌面应用；未运行 18120 端口；全程单一 runner/pytest 进程。

## 7. 收尾

- SDK 源 worktree `/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk-0631-source` **按要求保留**（0.6.20–0.6.29 的同名 sibling worktree 也仍在）。`tests/test_typed_recall_source_oracle.py` 的默认 checkout 已指向它；若该目录被移除，需用 `TYPED_RECALL_SOURCE_CHECKOUT=<路径>` 覆盖，否则该测试以 `BridgeError: cannot verify source checkout` 失败。
- run-12 的 3 格 FAIL 在 run-13 全部收敛（2 格回到 PASS、1 格改判为公共契约冲突并附正向见证），见 `TYPED-RECALL-401-RUN-07.md`。
