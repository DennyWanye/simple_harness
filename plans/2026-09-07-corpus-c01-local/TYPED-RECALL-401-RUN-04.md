# 401 矩阵重 pin 到 Memory 0.6.28 与正式扫描（run-10）

> 2026-09-08。本机 Host main（重 pin 前 HEAD `8e9b7c8d`，Memory SDK 采用提交 `e721c926`）。
> 上一轮同类记录：`TYPED-RECALL-401-RUN-03.md`（run-03 分析 + 主代理处置，含 run-06 M0.6.23 首次 PASS 227）。
> 本轮对照基线：run-09（M0.6.26，fixture rev 14 / layers rev 12，`.local-test-evidence/2026-09-07/typed-recall-401/run-09-m0626/`）。

## 0. 一句话结论

**PASS 227 / FAIL 0 / BLOCKED 174，与 0.6.23–0.6.26 四轮完全一致；401 格的逐格判定（`bridge-summary.json.cell_results`）与 run-09 逐字节相同，没有任何一格的裁决发生变化。** runner 退出码 3 = 整体仍 `NOT_RUN/BLOCKED`（174 格缺执行器/oracle/前置条件），属既有正常状态。0.6.27+0.6.28 唯一在矩阵观测里留下痕迹的行为变化是 **5 个短时域相关格的 `authority_epoch` 各降 1**，正是 0.6.28 「召回授权 epoch 只随资格变化推进」的预期效果，这 5 格本就 BLOCKED，不影响判定。

## 1. 本轮改动的 pin

跳过 0.6.27（Host 未 pin，0.6.28 已包含其全部改动），矩阵直接从 0.6.26 重 pin 到 0.6.28。

| 对象 | 文件 | 旧值 | 新值 |
|---|---|---|---|
| fixture 版本 | `testcase/human-memory-program/fixtures/typed-recall-v3.json` | rev 14 | **rev 15** |
| fixture 自身 sha256 | 同上 | `de75d943dd46726f2cd4e4ac215633702039ec691692d024b267f0cc995d323c` | **`3e4f23b7a72bc271fc8a225ccf9cb862eb3be6bf6ee505d100c95d105f184d5a`** |
| Memory 候选身份（`public_consumer.candidate_identity_pins.memory` 与 `approved_oracle.candidate_identity.memory` 两处逐字相同） | 同上 | 0.6.26 / `9b148b969926c7286fbac0e0371d81f9359b2bcd` / wheel `abe301b03f912b0b749cf5dedd1c0291a8f87cb449628e6b860b474603e4306c` | **0.6.28 / `e554c20dd8699606d44ac5af3bc88958822b8f56` / wheel `16498e49a8494ae2a3cb24bb1325a80576e3b2063bc921f732568cbcf55436f9`** |
| Harness 候选身份 | 同上 | 0.7.10 / `031fdc688ceea604ffa409a06a69fd85071aa612` / `e559bc1b…` | 不变 |
| `candidate_pin_lineage` | 同上 | 末项 rev 13（M0.6.25） | 追加 **rev 14（fixture sha `de75d943…`，M0.6.26）** |
| `candidate_pin_change` | 同上 | date 2026-09-07；scope「0.6.25 → 0.6.26」 | date **2026-09-08**；scope 改写为「0.6.26 → 0.6.28」（见 §1.1）；`source_commit`/`wheel_sha256` 同步 |
| layers 版本 | `testcase/human-memory-program/fixtures/typed-recall-execution-layers-v1.json` | rev 12 | **rev 13** |
| `typed_recall_fixture_revision` / `typed_recall_fixture_sha256` | 同上 | 14 / `de75d943…` | **15 / `3e4f23b7…`** |
| `clean_wheel_public_manager.candidate_memory_identity` | 同上 | 0.6.26 三元组 | **0.6.28 三元组** |
| `candidate_pin_previous_layers_sha256` | 同上 | `db7a68a48afdf12f931ff59b1a6c9261e6ff9e4a137193b2d8d8ae37e3df16f1` | **`2f483112ffd4ca3478f979f5a2bacb436d215b465576b9a1d911cc2374cfe97d`**（即 rev 12 文件自身 sha） |
| `candidate_pin_layers_lineage` | 同上 | 末项 `21be7cff…` | 追加 **`db7a68a4…`** |
| layers 文件自身 sha256 | 同上 | `2f483112…` | **`ab0d0ad7f27b213cd4e243f93bee54f72a3bbf9f94fc93eaf125570619faa022`** |
| bridge 常量 `FIXTURE_SHA` / `LAYERS_SHA` | `runners/typed_recall_bridge.py` | `de75d943…` / `2f483112…` | **`3e4f23b7…` / `ab0d0ad7…`** |
| source oracle `PINNED_SCHEMA_HASH` / `PINNED_COLUMNS_PK_HASH` | `runners/typed_recall_source_oracle.py` | `9702ea1e…` / `7a5ad1d1…` | **不变**（见 §1.2），仅更新注释与错误文案 M0626→M0628 |
| bridge 自回归 pin 断言 | `tests/test_typed_recall_bridge.py` | `("0.7.10", "0.6.26")` | **`("0.7.10", "0.6.28")`** |
| source 测试默认 checkout | `tests/test_typed_recall_source_oracle.py` | `simple-harness-memory-sdk-0626-source` | **`simple-harness-memory-sdk-0628-source`** |
| 安装版本断言 | `tests/test_typed_recall_fixture_authorities.py:271` | `"0.6.26"` | **`"0.6.28"`**（`backend/.venv` 现状已是 0.6.28，由 Host 采纳提交 `e721c926` 带来；`backend/` 本轮未动） |

### 1.1 `candidate_pin_change.scope` 新文案（英文原文照录）

> Candidate identity only: Memory 0.6.26 -> 0.6.28 (0.6.27 rebuilds short-horizon and cognitive-vector generations in three phases with embed_batch outside the write lock plus optimistic manifest CAS, and takes the recall write lock under the request deadline so the vector lane degrades with cognitive_vector_deadline instead of failing; 0.6.28 advances the recall authority epoch only for eligibility-changing events, so index-only generation activations no longer bump it and short-horizon projection bumps only when removed_chunk_count > 0. Public payload shape, hash domain, eligibility gates, 0.45 threshold, lane budgets, recall_authority_events/heads DDL and memory schema 7.4 unchanged, no DDL change; memory-only forget, CJK lexical gate and prospective trigger rendering retained). Original 401 cells, attacks, oracle inputs and thresholds unchanged.

401 格集合、攻击集合、oracle 输入与阈值均未变；本次是纯候选身份 pin。

### 1.2 source oracle 描述符按 0.6.28 fresh 根复核

用 oracle 自身的 `capture()` 对一个由 0.6.28 wheel 新建（`SQLiteHumanMemoryBackend(...).initialize()`）的空库重算：

```
tables 94
schema_hash      9702ea1ecf969324d138418abafe1efafad6464d479b21c03ff1cd1ba7394aab
columns_pk_hash  7a5ad1d179a8eea7fe1dc39dc72f1a414e8ad3c77e799737abadec0d34cb4384
```

与 0.6.23/0.6.24/0.6.25/0.6.26 的 pin 完全相同 —— 与 0.6.27/0.6.28 CHANGELOG「无 DDL 变化（7.4 checksum 不变）、`recall_authority_events`/`recall_authority_heads` 的 DDL 与行形状不改」一致。因此 `PINNED_SCHEMA_HASH` / `PINNED_COLUMNS_PK_HASH` 两个常量不动，只把注释的血缘链与两条错误文案从 M0626 更新到 M0628。

## 2. 命令行（全部绝对路径）

### 2.1 SDK 源 worktree（source 层的 10 格用）

```bash
git -C /Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk worktree add \
  /Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk-0628-source e554c20d
# HEAD = e554c20dd8699606d44ac5af3bc88958822b8f56，工作树 clean
```

### 2.2 消费者 venv（专用，与 Host `backend/.venv` 无关；不跑 `uv sync`、不动 pin）

```bash
uv venv --python 3.12 /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/venv-m0628

VIRTUAL_ENV=/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/venv-m0628 \
uv pip install --offline --no-deps \
  /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/vendor/simple_harness_sdk-0.7.10-py3-none-any.whl \
  /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/vendor/simple_harness_memory_sdk-0.6.28-py3-none-any.whl

VIRTUAL_ENV=/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/venv-m0628 \
uv pip install --offline httpx aiosqlite numpy pydantic structlog
```

装出的依赖集合与 run-09 的 `venv-m0626` 逐项相同（aiosqlite 0.22.1 / numpy 2.5.2 / pydantic 2.13.5 + pydantic-core 2.46.5 / httpx 0.28.1 + httpcore 1.0.9 + h11 0.16.0 + anyio 4.15.0 + idna 3.19 + certifi 2026.7.22 / structlog 26.1.0 / typing-extensions 4.16.0 / typing-inspection 0.4.4 / annotated-types 0.8.0），Python 3.12.14。

### 2.3 runner 自回归

```bash
/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/.venv/bin/python -m pytest \
  /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/testcase/human-memory-program/tests -q
# 106 passed, 31 subtests passed in 17.44s
```

另跑 `run_typed_recall_public_consumer.py --self-check` → 退出 0，`{"status":"NOT_RUN/BLOCKED","reason":"rev4 uses independent A2 oracle regressions; legacy 401 synthetic artifact self-check is disabled"}`（既有行为）。

### 2.4 正式 401 扫描（run-10）

artifact 目录执行前必须不存在。

```bash
/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/.venv/bin/python -B \
  /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/testcase/human-memory-program/runners/run_typed_recall_public_consumer.py \
  --consumer-python /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/venv-m0628/bin/python \
  --artifact-dir /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/typed-recall-401/run-10-m0628 \
  --harness-wheel /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/vendor/simple_harness_sdk-0.7.10-py3-none-any.whl \
  --harness-wheel-sha256 e559bc1b58ebfce0423247bc11ead0969f2364d76209fe7b9481bb2892ad2539 \
  --harness-source-commit 031fdc688ceea604ffa409a06a69fd85071aa612 \
  --memory-wheel /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/vendor/simple_harness_memory_sdk-0.6.28-py3-none-any.whl \
  --memory-wheel-sha256 16498e49a8494ae2a3cb24bb1325a80576e3b2063bc921f732568cbcf55436f9 \
  --memory-source-commit e554c20dd8699606d44ac5af3bc88958822b8f56 \
  --source-adapter /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/testcase/human-memory-program/adapters/typed_recall_source_cases.py \
  --source-checkout /Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk-0628-source \
  --child-timeout 300 \
  > /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/typed-recall-401/run-10.log \
  2> /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/typed-recall-401/run-10.stderr
# 退出码 3；stderr 为空
```

**不要传 `--consumer-entrypoint`**：`typed_recall_bridge.py:263-264` 对该参数无条件抛 `product test-helper entrypoints are prohibited`，整轮直接 FAIL（本轮第一次尝试即因此 FAIL，未产生 artifact 目录，随后原样重跑）。公共适配器由 bridge 自行选定（`adapters/typed_recall_public_manager.py`）。

## 3. 扫描结果（run-10）

- artifact 目录：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/typed-recall-401/run-10-m0628/`（534 MB，`.gitignore:249` 已排除）
- 汇总副本：`.local-test-evidence/2026-09-08/typed-recall-401/run-10.log`（= `run-10-m0628/bridge-summary.json`）

| 项 | 值 |
|---|---|
| `run_id` | `cd2e737aed714f6b918d059e2eed95eb` |
| 起止 | 2026-09-08T08:57:22.665824Z → 08:57:56.756653Z（34 s） |
| `status` | `NOT_RUN/BLOCKED`（退出码 3） |
| `acceptance_counts` | **PASS 227 / FAIL 0 / BLOCKED 174**（`required_cells` 401） |
| `candidate_pin_differences` | `[]`（无 pin 偏差，非 observe 模式） |
| `isolation` | `isolated-process-verified-installed-wheels` |
| 子进程实测版本 | harness 0.7.10（校验 174 个 wheel 文件）、memory **0.6.28**（校验 95 个 wheel 文件），Python 3.12.14 |
| 分层 | public：PASS 217 / BLOCKED 30 / FAIL 0；source：PASS 10 / FAIL 0 |
| `fixture_sha256` / `execution_layers_sha256` | `3e4f23b7…` / `ab0d0ad7…` |
| `oracle_code_sha256` | `93fb15522ac454a1dc8d71e204a426b2b390ea37d3b2a8e4ac73c51b9dca36fa`（与 run-09 相同） |
| public artifact sha256 | `aa9ba8a5354b7a2da9d509819ce16b8eb5beb12fd5d24ce5cc0696a333f96663` |
| source artifact sha256 | `0767b37e3e3086feaa79178a14f53395cb5887aa5f2b6ada1cc1de002d975f4a` |

174 个 BLOCKED 的分类与 run-06/07/08/09 完全一致：`FIXTURE_INVALID_OR_INSUFFICIENT` 96、`ORACLE_GAP` 48、`EXECUTOR_UNIMPLEMENTED` 30（逐类明细见 `TYPED-RECALL-401-RUN-03.md` §2，本轮无增减）。

## 4. 与 run-09（M0.6.26）逐格对照

### 4.1 判定层：零变化

对 `bridge-summary.json.cell_results` 401 格逐格做规范化 JSON 比较：

- 格 ID 集合相同（401 = 391 public + 10 source）；
- **status 差异 0 格**；
- **整体字段（含 `business_assertions`、`reason`、`blocker_categories`）差异 0 格 —— 逐字节相同**；
- `passed_cells`、`layers.*.{passed,failed,blocked,observed,missing}_cells` 全部相同；
- `oracle_code_sha256` 相同。

`execution_code_sha256` 中唯一变化的文件是 `runners/typed_recall_source_oracle.py`（`60e255e3…` → `631ed564…`），来自 §1.2 的注释/文案更新，不影响任何判定。

**没有任何一格的裁决发生变化，因此不存在需要归因的变更格。**

### 4.2 原始观测层：只有两类差异

`public-observations.json` 294 格、`source-observations.json` 10 格的原始观测与 run-09 不逐字节相同。逐叶比对后分为两类：

1. **每轮随机的初始化身份（非版本相关）**。所有差异叶子的根源是 `memory_mutation_receipts.authority_ref` 里的 `sqlite-human-memory:init-<32 位随机 hex>`，它进而改变 receipt/result/outbox 的 payload 与 hash、以及 `manifest.table_roots[*]` 的 root/first/last leaf hash，另有 14 个 `rejection_receipt.invocation_id`（UUID）。**同样的 294/10 差异也存在于 run-08（M0.6.25）与 run-09（M0.6.26）之间**，而那两轮的 `cell_results` 是逐字节相同的 —— 证明这是 run 间不确定性，不是 0.6.28 引入的。oracle 判据只看 hash 之间的关系（before/after、all-old/all-new 终态关系），不看绝对值，所以判定不受影响。

2. **`authority_epoch` 各降 1（0.6.28 的预期效果，共 5 格 15 个叶子）**：

| 格 | 观测路径 | run-09 (0.6.26) | run-10 (0.6.28) | run-09 判定 | run-10 判定 |
|---|---|---:|---:|---|---|
| `eligibility/short-chain-complete` | `before.execution.result` / `recall.execution.result` / `replay.result` 的 `authority_epoch` | 2 / 2 / 2 | 1 / 1 / 1 | BLOCKED | BLOCKED（同因） |
| `eligibility/short-expiry-equals-now` | 同上 | 2 / 2 / 2 | 1 / 1 / 1 | BLOCKED | BLOCKED（同因） |
| `eligibility/short-source-suppressed` | 同上 | 2 / 3 / 3 | 1 / 2 / 2 | BLOCKED | BLOCKED（同因） |
| `protocol/mixed-long-short` | 同上 | 5 / 5 / 5 | 4 / 4 / 4 | BLOCKED | BLOCKED（同因） |
| `protocol/short-only` | 同上 | 2 / 2 / 2 | 1 / 1 / 1 | BLOCKED | BLOCKED（同因） |

原因：0.6.28 停止在 `short_horizon_generation_changed`（短时域向量世代激活）与 `cognitive_vector_generation_changed` 上推进 epoch，并把 `short_horizon_projection_changed` 收窄为「仅当 `removed_chunk_count > 0`」。这 5 格都会在召回前建立短时域投影/世代，因此每格的 epoch 恰好少推进一次；`short-source-suppressed` 的抑制事件仍推进，所以它的 recall/replay 由 3 降到 2 而不是降到 1，epoch 的相对关系（`before` → `recall` 差 1）逐格保持。

这 5 格本来就因 `SHORT_COMPLETE_REGISTRATION_CLASSIFICATION_TIME_AND_ORIGINAL_HASH_BINDINGS_PENDING`（oracle 未闭合）而 BLOCKED，epoch 绝对值不进入任何断言，所以裁决不变。

除以上两类外，**没有任何非 hash/非 UUID 的语义叶子差异**：退化码（消费者未绑定 embedder，仍为 `cognitive_vector_unavailable`）、资格门顺序、候选读取计数、终态 reason_codes 全部不变；0.6.27 新增的 `cognitive_vector_deadline` 与 `TypedRecallDeadlineExceeded` 在本矩阵的预算下未被触发。

## 5. 历史对照

| run | Memory pin | fixture rev / layers rev | PASS | FAIL | BLOCKED |
|---|---|---|---:|---:|---:|
| run-06 | 0.6.23（`78ddf386`） | 11 / 9 | 227 | 0 | 174 |
| run-07 | 0.6.24（`3b51e0f6`） | 12 / 10 | 227 | 0 | 174 |
| run-08 | 0.6.25（`b45db92c`） | 13 / 11 | 227 | 0 | 174 |
| run-09 | 0.6.26（`9b148b96`） | 14 / 12 | 227 | 0 | 174 |
| **run-10** | **0.6.28（`e554c20d`）** | **15 / 13** | **227** | **0** | **174** |

（0.6.27 `0ccec4f9` 为中间候选，Host 未 pin，矩阵未单独扫描；其改动包含在 0.6.28 内。）

## 6. 未做 / 后继

沿用 `TYPED-RECALL-401-RUN-03.md` §4 的清单，本轮未推进任何一项：

1. fixture rev 加 `intended_audience` 轴与 4 格受众负例（把 M0.6.14 的受众绑定变成矩阵显式义务）；
2. 174 BLOCKED 的补齐：执行器未实现 30、fixture 与公共契约不符 96、oracle 未闭合 48；
3. `selection-budget/vector-*` 3 格在消费者绑定 embedder 后的退化码期望更新（0.6.23 起新增 `cognitive_vector_no_generation`/`stale`/`deadline`，0.6.27 起 `deadline` 可由取锁超时产生）；
4. bridge 层级 PASS 规则缺失（任何一层永远停在 `NOT_RUN/BLOCKED`）—— 174 BLOCKED 未清零前不影响结论；
5. 若矩阵要镜像生产「每个 typed 计划恒请求 VECTOR」，需同改 recipe modes、`a2_oracle.py:521` 的 modes 比较与全部 normal 格的退化码期望。

另：0.6.28 的 epoch 车道收窄意味着 `authority_epoch` 不再是「任何写入都会动」的计数器。若将来矩阵要把 epoch 语义本身钉死（例如断言「纯索引重建不推进」），需要新增显式格，当前 401 格里没有任何一格对 epoch 绝对值或推进次数做断言。

## 7. 收尾

- SDK 源 worktree `/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk-0628-source` 在记录完成后移除（`git worktree remove` + `prune`）。
  **注意**：`tests/test_typed_recall_source_oracle.py` 的默认 checkout 已指向该目录，worktree 移除后该测试会以 `BridgeError: cannot verify source checkout` 失败（106 → 105 passed + 1 failed），其余 105 项与 31 subtests 不受影响。要再跑 source 层测试或再做一次 401 扫描，先恢复：

  ```bash
  git -C /Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk worktree add \
    /Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk-0628-source e554c20d
  ```

  或临时用 `TYPED_RECALL_SOURCE_CHECKOUT=<路径>` 指向别处。（0.6.20–0.6.26 的同名 worktree 仍保留在 sibling 目录下，本次按要求只清理 0628 这一个。）
- `backend/` 未做任何改动，未执行 `uv sync`，未改 Host pin，未运行桌面应用。
