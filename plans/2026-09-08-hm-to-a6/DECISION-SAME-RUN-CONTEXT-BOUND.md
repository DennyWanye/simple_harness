# 决策备忘：同一 Run 内 Provider Context 的有界化（Incident E）

> 日期：2026-09-08
> 义务：`HM-TO-A6`（AC = HM-AC-2 / HM-AC-6）
> 契约依据：`simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/acceptance.md`
> HM-AC-6 —— **动态上下文组装必须在模型预算内完成，大 tool result 必须分页**。
> 本备忘只处理 HM-AC-6 的「预算内」半边；「大 tool result 分页」半边已实现，本次复用其载体。

---

## 1. 事实与证据（不是推断）

证据来源：`.local-test-evidence/2026-09-08/native-a6-b3682fe1/primary-ui-xmqudtzt/userdata/`
（DeepSeek 原生真机跑；`model_overrides.toml` 把 window 钉在 **32000** → `budget_tier = 8192`，
`effective_input_budget = 32000 − 2048 − 3200 = **26752**）。

### 1.1 单个 Run 内请求无界增长

SDK 库 `data/simple-harness-sdk/execution-v6.sqlite3`，Run
`product-sdk-7ee655dafcc27e2190e35c27f1a2b5ad5aa3bd0c0124aa69efeed8e322f9fa17`
（`provider_invocations` rowid 76–100，**同一个 Run 内 25 次 provider 调用**）：

| rowid | messages | 请求体 KB | `role=tool` 条数 | tool 合计 KB | 最大单条 tool KB | Host 估算 token |
|---:|---:|---:|---:|---:|---:|---:|
| 76 | 5 | 7.6 | 0 | 0.0 | 0.0 | 1857 |
| 80 | 13 | 35.8 | 4 | 25.7 | 8.7 | 8574 |
| 84 | 24 | 66.7 | 11 | 53.8 | 8.7 | 15885 |
| 88 | 33 | 72.1 | 16 | 58.0 | 8.7 | 16946 |
| 92 | 42 | 77.7 | 20 | 58.8 | 8.7 | 18089 |
| 96 | 51 | 89.0 | 25 | 65.2 | 8.7 | 20659 |
| 100 | 58 | 85.4 | 29 | 68.3 | 8.7 | 19753 |

该 Run 内工具分布：`run_shell` ×11、`tool_search` ×7、`tool_describe` ×4、`tool_activate` ×3、
`workspace_prepare` / `task_scope_search` / `task_scope_update` / `context_route` 各 1。

Provider 实际计费（`usage_json`）：`input_tokens` **5584 → 24124 → 30437 → 35320 → 44378**，
单调递增，最后一轮 **44378 > 26752（effective_input_budget）**，也超过 window 的可用部分。
Host 侧投影 `state.db.sdk_provider_attempt_audit.input_tokens` 同值。

### 1.2 现有裁剪为什么没有生效

`state.db.run_context_snapshot_receipts` 该 Run 全部 25 行的 `source_revisions_json` 都是：

```json
{"budget_tier":8192,"causal_groups":3,"context":<N>,"trimmed_groups":0}
```

`trimmed_groups` 恒为 0、`causal_groups` 恒为 3。原因是链路上**只有历史被裁剪**：

- `backend/deskpet/execution/primary_context.py::prepare` 的
  `while complete and over_cap(): complete.pop(0)` —— 只对**已结算的历史组**整组丢弃；
- `backend/deskpet/sdk_adapters/context_partitions.py::trim_causal_groups` —— 只丢 `closed()` 组，
  **`open_run` 组永不裁剪**；
- 当前 Run 自己的 tool 结果全部落在那个唯一的 open 组里；
- `backend/deskpet/execution/current_tool_pages.py::CurrentToolProjector` 只在**单条**结果
  `> DEFAULT_LARGE_RESULT_BYTES = 16 KiB` 时才摘要；本 Run 最大单条 8.7 KiB，**一条都没触发**。

结论：**「历史有界」≠「一个 Run 的 Provider Context 有界」**。29 条各自合规的小结果，
合起来 68 KB，把请求顶到窗口上限。这就是 Incident E 的真实机理。

### 1.3 顺带确认的第二个事实（不在本次修复范围内）

同一份证据里另一个 Run `product-sdk-cba43a68…` 同样 25 次调用、24 条 `role=tool`，
但全部是 `task_scope_search`(18)/`task_scope_update`(5)/`context_route`(1) —— 即
`CONTROL_TOOLS`。请求从 18.2 KB 长到 34.7 KB。这些载体带可执行的 context/recall 凭证，
**按既有设计不允许被摘要**，本次不动，记为 followup F-E2（见 §7）。

---

## 2. 契约边界（哪些东西不能动）

1. **SDK 冻结**：Host 负责准备上下文；`RunContextSnapshot` / `provider_request_fingerprint`
   必须可复算（`plans/2026-09-08-hm-to-a6/00-PLAN.md` §0、§3(a)）。
   → 任何改动必须对同一 Run 状态**确定性**产生同一份 messages。
2. **`source_revisions` 不进指纹**：`expected = provider_request_fingerprint(probe)` 只吃
   messages/tools/temperature/max_output_tokens；`payload_hash == expected`。
   → 往 `source_revisions` 加字段**不会**改指纹，可安全用于归因。
3. **因果链不可撕开**：任何 `role=tool` 消息必须保留其 `name`/`call_id`，
   不得产生孤立 tool 消息（00-PLAN §3(c)、A6-4）。
4. **`primary_history.py` 不得修改**（字节等价 checkpoint 依赖它）。本次未触碰。
5. **`context_page_in` 只接受本次请求真正准备过的 page 引用**（PERSONA 明文约束 +
   `admitted_current_page` 的 `not_admitted` 校验）。

---

## 3. 候选方案与取舍

| 方案 | 内容 | 评估 |
|---|---|---|
| (1) 累积上界 | 当前 Run 已结算的 tool 结果总量超过 `effective_input_budget` 的一份额时，把**最旧的**结果体换成既有的 `primary_settled_effect_v1` 摘要 + `context_page_in` 引用 | 复用既有内容寻址载体，零新协议；摘要可逆（`content_hash`/`content_bytes` 在 descriptor 里）；**采用** |
| (2) 自适应降低单条门限 | 把 `DEFAULT_LARGE_RESULT_BYTES` 按剩余预算动态下调 | 门限一旦随预算漂移，`verify_request`→`source_content` 在 page-in 时刻重算的门限可能与摘要生成时刻不同，导致**同一份摘要在翻页时验不过**。要修就得把门限写进 descriptor，等于改 page 引用形状 → 破坏 (2) 的「最小改动」前提。**否决为独立方案**，其可取部分已被 (1) 覆盖（(1) 事实上就是把门限对旧结果降到 0） |
| (3) 两者都做 | — | 在 (1) 之上再叠 (2) 只会增加不确定性，不增加有界性。**否决** |

**决定：采用 (1)。** 份额取 `effective_input_budget // 4`
（`CURRENT_TOOL_BUDGET_DIVISOR = 4`）：window=32000 → 6688 estimator-token（≈26 KB），
window=32768 → 6349。理由：protected（PERSONA + 工具 schema）与历史组也要占位，
当前 Run 自己的结果占到 1/4 已经很宽松；且 Host 估算器相对 provider 真实 tokenizer
偏低（§1.1：估算 19753+schema 3997 ≈ 23.8K vs 实际 44378，约 1.9×），留够安全边际。

### 3.1 明确不做的事

- **不丢弃**任何 tool 结果 —— 丢弃会产生孤立 tool 消息，直接违反 A6-4。只做「体→摘要」替换。
- **不碰 `CONTROL_TOOLS`** —— 它们携带可执行的 context/recall 凭证。
- **不替换最新一个 provider turn 的结果** —— 那一批是 in-flight 调用刚产出、
  模型正在据以行动的结果（并行 tool_calls 时是一整批，不是一条）。
- **不替换非 succeeded 的已结算 effect** —— failed/rejected/partial 没有可分页的 public body，
  它们保留自己的字节，绝不因此让整个 Run 失败（见 §4.1 与 §7 评审）。
- **不碰 `primary_history.py`**、不碰历史组投影（`primary_tool_result_summary_v1` 一路照旧）。

---

## 4. 实现

### 4.1 `backend/deskpet/execution/current_tool_pages.py`

- 新常量 `CURRENT_TOOL_BUDGET_DIVISOR = 4`（带机理注释与证据数字）。
- `source_content(facts, *, min_bytes=DEFAULT_LARGE_RESULT_BYTES)`：把
  `source_not_large` 从硬编码门限改为参数。**这是策略闸门，不是完整性闸门**——
  完整性由 descriptor（全部来自 public audit/effect 事实）+ `summary(descriptor, content)`
  的逐字节相等提供。`verify_request` 与累积上界路径都传 `min_bytes=0`。
- 新增 `current_tool_allowance(metadata)`：复用 `_resolve_window_tokens` 解析 window，
  缺失时退到最小冻结档（与 `_plan_turn_messages` 同向的「宁可多裁」）。
- 新增 `_settled_tool_tokens(messages)`：统计本 Run 自己的非 CONTROL tool 体占用。
  （顶层 `role=tool` 只可能是当前 Run 的：历史组是 `role=user` 的引用块。）
- `CurrentToolProjector.__call__`：
  - 早退条件由 `if not large` 改为 `if not large and not settled`（仍然保证「本来什么都不做的
    轮次」照旧返回 `None`，请求字节零漂移）；
  - 读到 Host 事实后计算 `allowance` 与 `over_bound`，`if not large and not over_bound: return None`；
  - 主体拆到 `_project()`，外面包一层：捕获 `PrimaryContextPageUnavailable` /
    `PrimaryToolCausalityUnavailable` 后，**若 `large` 非空则原样抛出**（>16 KiB 结果绝不裸奔，
    保留本 authority 原有 fail-closed 语义）；**若 `large` 为空**（纯累积上界路径，
    改动前这一轮是直接 `return None` 的），则退回 `None`，让这一轮完全维持改动前的样子
    ——`_plan_turn_messages` 仍会在真的超预算时 fail closed。
  - `_project()` 主路径：先按既有 >16 KiB 规则替换；剩余候选剔除
    **最新一个 `provider_turn_ordinal`** 的整批、剔除 `state != "succeeded"` 的，
    再按**消息索引升序**从最旧开始替换，直到 `carried <= allowance` 或候选耗尽；
    单条替换再包一层 `except PrimaryContextPageUnavailable: continue`（纵深防御）；
  - 若摘要不比原体小则**不替换**（`return 0`），避免把小结果换大。

### 4.2 `backend/deskpet/sdk_adapters/context_authority.py`

- 新增 `_current_tool_page_facts(messages)`：**从最终 messages 本身**统计
  `current_tool_pages`（本 Run 有多少条已结算结果以 page 引用形态出发）与
  `current_tool_tokens`（非 CONTROL tool 体仍占的 token）。
  从 messages 统计而不是让 projector 自报，保证回执记录的是「真实发出的请求」的事实。
- `build_snapshot` 的 `source_revisions` 合并该二字段。

### 4.3 快照回执如何记录（A6-3 / A6-4 归因口径）

`state.db.run_context_snapshot_receipts.source_revisions_json` 内层
`source_revisions` 由

```json
{"budget_tier":…, "causal_groups":…, "context":…, "trimmed_groups":…}
```

变为

```json
{"budget_tier":…, "causal_groups":…, "context":…, "trimmed_groups":…,
 "current_tool_pages":<int>, "current_tool_tokens":<int>}
```

- `current_tool_pages > 0` ⇒ 本轮同 Run 累积上界（或 >16 KiB 规则）确实生效；
- `current_tool_tokens` ⇒ 本轮当前 Run 自己的结果体占用，A6-3 的「有界」可直接对它做单调/上限断言；
- 二者都是 int，落在既有 `receipt_trim_stats()` 的读取形状里，不需要 schema 迁移；
- **不进指纹**（§2.2），A6-12 的重放断言不受影响。

> `scripts/native/a6_verify.py` 在本 worktree 中**不是 git 追踪文件**（主 checkout 里存在但
> untracked，属于另一路在飞的工作）。为避免制造分叉副本，本次**不新建/不修改**该脚本，
> 只在此固定字段契约。A6-3/A6-4 侧的最小接入：在 `receipt_trim_stats()` 里追加
> `current_tool_pages_total = Σ current_tool_pages`、`max_current_tool_tokens = max(current_tool_tokens)`，
> 并把「未观测到裁剪 → INCONCLUSIVE」的判据放宽为「`trimmed_groups` 与 `current_tool_pages` 皆为 0」
> 才记 INCONCLUSIVE。记为 followup F-E1。

---

## 5. 测试

### 5.1 新增真实运行时用例

`backend/tests/execution/test_current_tool_pages.py::
test_same_run_settled_results_get_a_per_result_ceiling_and_page_back_exact_bytes`

走真实前台 Runtime + 真实 SDK 栈 + `httpx.MockTransport` 物理层（与既有用例同一套 fixture）：
一个 Run 内 9 批 × 4 次 `write_file` = **36 条已结算 tool 结果**，每条 ≈3.2 KB
（`write_file` 自身 3000 字符硬上限，所以逐条都远低于 16 KiB —— 正是 Incident E 的形状），
其中**第一批第一条故意抛异常**，落成 `state=failed` 的已结算 effect。

断言：

1. 一条**小于 16 KiB** 的已结算结果被累积上界换成了 page 引用，且
   `context_page_in` 以 `primary-effect-page:v1:` 引用 + 指定 offset 取回**逐字节相等**的原文
   （`page.content == raw.encode()[offset:].decode()`，`page_hash` 自洽）；
2. `len(settled) >= 20`；未摘要时这些结果体本身
   `raw_total // 4 > effective_input_budget` —— 即**修复前这个 Run 会 fail closed / 超发**；
2b. 那一条 failed 结果保留自己的字节、**没有**让 Run 失败（`holder.failed == 1` 且 Run 走到终态）；
3. 每一次真实 provider 请求的估算 token `<= effective_input_budget`，
   且当前 Run 自己的体占用 `<= effective_input_budget * 3 // 4`；
4. 该 Run 的每一行 `run_context_snapshot_receipts` 都带
   `current_tool_pages` / `current_tool_tokens`，且 `max(current_tool_pages) >= 20`。

**该用例确实能抓住 §7 评审指出的缺陷**：把 `state == "succeeded"` 过滤与
`except PrimaryContextPageUnavailable` 两道防护临时摘掉后，用例以
`ForegroundQueueError: foreground_terminal_closure_pending` 失败（整个 Run 被
`primary_effect_page_source_not_pageable` 打死）；装回防护后通过。

### 5.2 回归

（见 §9 结果表）

---

## 6. 有界性的诚实边界（按评审意见修正措辞）

摘要本身有固定成本：descriptor + 1 KiB `excerpt` ≈ **1823–1937 字节 / 约 456–485 token 每条**。
因此本方案给出的是**每条结果的上界（per-result ceiling）**，
**不是**「请求总量收敛到 `allowance`」：

- 修复前：每条结果按原体计入（本次证据里 8.7 KiB/条，上限 16 KiB），单 Run 内无任何上界；
- 修复后：越过份额之后，每条已结算结果最多贡献 ~456 token；最新一个 provider turn 的整批
  与非 succeeded 的结果保留原体；
- 评审侧实测（window 32768、budget 25396、allowance 6349）：
  `tool=12 → 8954 tok`、`tool=20 → 12863`、`tool=28 → 16834`、`tool=36 → 20803`
  —— 仍然**线性增长**，约 496 token/条，约在 45–50 条时撞到 fail-closed；
- 也就是说：本次把 Incident E（29 条 × 8.7 KiB）换来的是 **约 4–5 倍余量**，
  **不是渐近有界**。用例名与文案都已按此改为「per-result ceiling」，不再写「stay bounded」。

一旦超过 ~13 条结果，`carried <= allowance` 这条 break 事实上不可达
（456 token × 14 > 6349），行为退化为「除最新一批外全部分页」。这是可接受的降级形态，
但要清楚它就是当前设计的真实语义。

真正的兜底仍然是 `_plan_turn_messages` 的 fail-closed（估算超预算就抛
`ContextBudgetExceeded`，绝不超发）。要拿到真正的渐近有界，需要把 `excerpt` 长度纳入
descriptor（保持 `summary(descriptor, content)` 仍可逐字节复算、`reference_id`/`source_hash`
语义不变），把最旧的一批压到 0–128 字节 excerpt，单条成本从 ~456 降到 ~110 token
—— 见 followup F-E3。

---

## 7. 独立评审（严格复核，另起 agent，只读）

评审覆盖 8 个问题：安全/授权、可复算性与冻结 SDK 契约、因果链完整性、
早退路径行为漂移、预算兜底、有效性、工具 schema token 缺失、其他正确性缺陷。

**判定 OK 的项（附评审复核到的事实）**

- **安全/授权**：`min_bytes=0` 不扩大可达面。准入靠的是「该摘要确实出现在发起 page 调用的
  那个 request 里」——`admitted_current_page` 先用 `read_effect_facts` 把
  `parent_request` 钉死在 `provider_request_fingerprint == invocation.request_fingerprint` 上，
  再由 `verify_request` 对每个 marker 重算 `summary(descriptor, content)` 并要求
  `name` / `call_id` / `run_id` / `source_hash` 全等，最后是 `primary_effect_identities`
  的先后序 + `read_run_dependencies` + `policy.check_dependencies` + disclosure 稳定性复检。
  `CONTROL_TOOLS` 有三道独立防护（`source_content` 的 `source_not_pageable`、`settled` 过滤、
  projector 的逐条跳过），未被削弱。
  唯一残留：「必须够大」这条策略现在只活在 projector 里，`verify_request` 少了一层背书
  —— 但真正的不变量 `source_not_pageable` 完好。
- **可复算性**：`sources` 顺序由 `sorted(providers.items())` + `enumerate(tool_calls)` 决定且
  拒绝空洞；`candidates` 按消息索引排序；`allowance` 来自冻结的 start 快照；
  `source_revisions` 不进 `payload_hash`，两个新字段无法移动指纹；
  SDK 侧只校验 `str -> 非负 int`。重放同一 turn 产出同一指纹，`record_snapshot_receipt`
  幂等键 `(sdk_run_id, provider_turn_ordinal, payload_hash)` 成立。
- **因果链**：`replace` 重建时保留 `role`/`name`/`call_id`，assistant tool_call ↔ tool 结果配对不断。
- **非主线调用方**：`if not rows and "visibility_dependencies" not in metadata: return None`
  在任何新 `_require` 之前；chat lane 的 start 不带 `visibility_dependencies` 且无
  `foreground_runs` 行；scoped lane 的 `turn`/`input.text` 齐备。
- **预算兜底**：`current_tool_allowance({})` = 665 偏激进，但 `_plan_turn_messages` 对同一份
  metadata 本来就退到 4096 档（总预算 2663）并 fail closed，projector 跑在它之前，
  多分页只会提高该轮存活率。评审逐一核过三种生产 start 形状都带 `context_window`。

**MUST-FIX（已修）**：评审端到端复现——Run 内只要有**一条非 succeeded 的已结算 tool 结果**
（被拒的写、校验失败、rejected effect 都很常见），越过上界的那一轮就会因
`primary_effect_page_source_not_pageable` 打死**整个 Run**：

```
{"error_type":"PrimaryContextPageUnavailable",
 "error_message":"primary_effect_page_source_not_pageable",
 "event":"sdk_run_driver_failed"}
OUTCOMES: {'succeeded': 11, 'failed': 1}   # 第 7 个 provider turn 死掉
```

原因：`read_tool_causal_sources` 接受 `{succeeded, partial, failed, rejected}`，
而 `source_content` 只接受 succeeded。修法见 §4.1：候选过滤
`source["state"] == "succeeded"` + 单条 `except PrimaryContextPageUnavailable: continue`
+ 纯累积上界路径整体退回 `None`。新用例（§5.1）覆盖并已验证能抓住该缺陷。

**RISK（已修）**：`bounded[:-1]` 只保护「最新一条」，并行 tool_calls 时同一批的其余 N−1 条
会在下一轮立刻被分页。改为按 `provider_turn_ordinal` 保护**最新一整个 provider turn**。

**RISK（已按评审改文案，未改行为）**：有效性 —— 见 §6。

**RISK（已改注释，未改行为）**：`_current_tool_page_facts` 统计的是「本请求内的 `role=tool`」，
在主线各 lane 上等价于「本 Run 自己的已结算结果」（历史以 `role=user` 引用块出现），
在 chat lane 上退化为整请求工具 token 计数。docstring 已如实说明。

---

## 8. Followups

| 编号 | 内容 |
|---|---|
| F-E1 | `scripts/native/a6_verify.py`（当前 untracked，属他人在飞工作）接入 `current_tool_pages` / `current_tool_tokens`，用于 A6-3/A6-4 归因，见 §4.3 |
| F-E2 | `CONTROL_TOOLS` 结果在单 Run 内同样线性累积（证据：`product-sdk-cba43a68…` 24 条 control 结果 → 34.7 KB）。它们携带可执行凭证不能摘要，需要另一种有界策略（例如只保留最近 N 条 attestation 的完整体、更旧的换成 typed attestation 引用） |
| F-E3 | 摘要固定成本 ~1.9 KB/条（~456 token）；把 `excerpt` 长度纳入 descriptor，把最旧一批压到 0–128 字节 excerpt，可把单条成本降到 ~110 token 并拿到真正的渐近有界（`reference_id`/`source_hash` 语义与 reader 均不变） |
| F-E4 | `_plan_turn_messages` 的 `protected_tokens` **不含工具 schema token**，而 `primary_context.py::prepare` 含。原生证据里 schema ≈3997 estimator-token，且 Host 估算器整体比 provider 真实 tokenizer 低约 1.9×（估算 23.8K vs 实际 44378）。逐轮预算校验因此系统性偏低。**本次不改**：`prepare_snapshot` 手里的 `tools` 是 `ProviderToolSpec` 元组而非带 `schema_token_count` 的 catalog，需要新铺一条量；且给每轮 protected 底座加 ~4000 token 会在所有 lane 上新增 fail-closed。单独立项、单独 gate |
| F-E5 | `replace` 每条都要 `read_run_operation_audit(limit=4096)` + 最多 32×256 页 projection receipt，而 Context 里仍是原体，所以**每一轮都要把整批重算一遍** —— 单 Run 内 O(n²) 的审计扫描（评审 14 轮探针 ≈38s）。需要按 (run_id, effect_id) 做请求内缓存 |

---

## 9. 结果

改动文件（3 个）：

- `backend/deskpet/execution/current_tool_pages.py`
- `backend/deskpet/sdk_adapters/context_authority.py`
- `backend/tests/execution/test_current_tool_pages.py`

**回归口径**：把 `HEAD` 版本的这三个文件复制到隔离副本
（`$SCRATCH/base/backend`，`PYTHONPATH` 指向它），同一台机、同一 venv、
同一批参数（`-p no:logging -p no:randomly`）跑「改动前 / 改动后」两遍逐个比对，
而不是拿绝对失败数当结论。

| 套件 | 改动后 | 改动前（同机基线） | 差异 |
|---|---|---|---|
| `tests/execution/test_current_tool_pages.py` + `test_revoked_scope_terminal.py` | **5 passed** | 4 passed（无新用例） | +1 新用例，无回归 |
| `tests/execution`（全量，确定性顺序） | 42 failed / 203 passed / 11 errors | 42 failed / 202 passed / 11 errors | 净 +1 通过（新用例）；两侧失败集合差异只落在**已知红**的 `test_scope_disclosure_runtime`（顺序相关抖动）与仅基线侧失败的 `test_unscoped_search_late_forget` |
| `tests/sdk_adapters`（context 相关子集：`test_context_route_authority` / `test_s5a_acceptance_matrix` / `test_s5a_milestone_real_provider` / `test_product_host_ports`） | 3 failed / 52 passed / 3 errors | 3 failed / 52 passed / 3 errors | **完全一致** |
| `tests/memory` + `tests/task_scope` | 63 failed / 574 passed / 1 error | 63 failed / 574 passed / 1 error | **完全一致** |
| importers | `main` / `current_tool_pages` / `primary_context_pages` / `primary_context` / `context_authority` / `context_partitions` / `causal_groups` / `primary_tool_causality` / `composition` / `context_page_in_tools` 全部导入 OK，`deskpet.__file__` 指向本 worktree | — | — |

已知环境问题（与本改动无关，两侧一致）：`tests/sdk_adapters/test_composition.py`
在两棵树上都**挂死在同一位置**（第 12 个用例之后不再前进），因此 sdk_adapters
只能按子集比对；`tests/execution` 的大批既有红（`test_primary_decisions`
`'NoneType' has no attribute 'sdk_run_id'` ×29、`test_preparation_rejection` setup ERROR、
`KeyError: 'H077_IDENTITY_JSON'`、`fixture 'caplog' not found` 等）在基线上同样存在。
