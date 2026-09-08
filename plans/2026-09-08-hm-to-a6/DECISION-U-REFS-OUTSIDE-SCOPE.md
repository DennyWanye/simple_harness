# 决策：`task_scope_update_refs_outside_scope` 必须可执行（事件 U）

- 日期：2026-09-09
- 现场：HM-TO-A6 第 9 次尝试，T17（`deepseek-v4-flash`）
- 证据（只读）：`.local-test-evidence/2026-09-09/native-a6-run9/primary-ui-8whts2lo/`
- 对照：第 5 次尝试的成功样本在 `.local-test-evidence/2026-09-08/native-a6-run4/primary-ui-7f_uv2a1/`
- 代码：`backend/deskpet/sdk_adapters/task_scope_mutation.py`
- 用例：`backend/tests/sdk_adapters/test_task_scope_update_refs_disclosure.py`
- 前序：`DECISION-STANDALONE-ROUTE-TOOL-AUTHORITY.md`（事件 B：拒绝必须带可执行下一步）、
  `DECISION-CLOSURE-DIRT-AND-PROTOCOL-RETRY.md`（事件 C：拆掉挡住用户口述档案变更的闸，并留痕冻结文本漂移）

---

## 0. 三句话结论

1. T17 不是模型不会用工具：那一轮里**任何** `task_scope_update` 载荷都必然被拒——该 TaskScope 在整个 Run
   期间 `task_scope_evidence_links` 为 0 行，而 `evidence_refs` 是 `minItems: 1`，可引用集合是空集。
2. 拒绝回执只回显违规 ref，既不说哪些 id 可引用，也不给下一步；模型只能连猜 7 次直到烧完整轮，
   **A6-5 因此不可达**（18 KiB 逐字 `goal.set` 是触发 README/STATUS 超限拆分的唯一杠杆）。
3. 修复：可引用集合补上「本 Run 已受理的 USER 证据」，拒绝回执披露有界的可引用 id（仅 id）与可执行下一步，
   同码重复拒绝超上限后给有界升级提示。守卫本身**不放宽**，稳定码与 schema 不动。

---

## 1. 现场

T17 的用户消息是「把下面这段完整的目标说明**逐字**记为这个任务的目标，不要概括、不要省略：…」（约 18 KiB）。
Run `product-sdk-4996b84d…`，admission scope `9d98c965-19db-58c0-9497-2351e17bb790`（T7 由
`context_route route=create_new` 在本 Run 内新建）。

模型在同一 Run 里 7 次调用 `task_scope_update`（`outcome=mutate`、单条 `goal.set`、`base_revision=1`），
每次换一批 `evidence_refs`：

| 次序 | prepared_at | 猜的 ref 形状 | 结果 |
| --- | --- | --- | --- |
| 1 | 1788904727 | 4 个 uuid（含 `20f23807-…`） | `refs_outside_scope` |
| 2 | 1788904760 | 创建回执 id `85f998dd-…` | `refs_outside_scope` |
| 3 | 1788904801 | 原始信封哈希 `9cd8f80a…` 等 | `refs_outside_scope` |
| 4 | 1788904844 | 绑定哈希 + 原始 `call_id` + `run_id` | `refs_outside_scope` |
| 5 | 1788904899 | `foreground-execution-4908…` | `refs_outside_scope` |
| 6 | 1788904994 | `binding:` / `effect:` / `envelope:` / `evidence:` / `task_scope:` / `receipt:` / `run:` 前缀形式 | `refs_outside_scope` |
| 7 | 1788905020 | `["probe-noop"]` + `no_mutation` | `nothing_to_close` |

`host_pre_admission_audit`：`task_scope_update_refs_outside_scope` 6 行 + `task_scope_update_nothing_to_close` 1 行。
Run 最终 `terminal:failed`。第 7 次的 `closure_reason` 里模型已经自己把话说完了：

> 「…宿主反复以 `task_scope_update_refs_outside_scope` 拒绝（创建回执、信封、scope/run 等标识均不被允许作证据引用），
> 本轮未产生可引证的作用域变更；完整目标全文已在对话中逐字保留，**等待宿主提供允许的证据引用后再登记**。」

### 1.1 为什么必然失败

`task_scope_evidence_links` 只在**追加事件**时写（`CanonicalTaskScopeStore._link_refs_tx`），
而会写链接的事件只有三类：`host.file` / `harness.tool_invocation`（material 效果）、`harness.run_terminal`
（Run 终态）、`mutation.plan`（收口自身）。scope `9d98c965` 的 1–46 号事件全是
`harness.route_decision` / `harness.tool_invocation`（只读工具）/ `harness.context_snapshot` /
`harness.provider_invocation`，**没有一条**会铸链接。

该 scope 有史以来第一条 `task_scope_evidence_links` 行的 `created_at = 1788905048.93`，
由本 Run 自己的 `harness.run_terminal`（`terminal:failed`，事件序号 47）铸出——**比最后一次拒绝晚 28 秒**。

也就是说：Run 内新建 scope + 本轮无 material 效果 ⇒ 可引用集合恒为空集 ⇒
`evidence_refs`（`minItems: 1`）无解 ⇒ 100% 拒绝。这不是引导问题，是**载荷空间为空**。

### 1.2 唯一的披露通道恰好也关着

`allowed_evidence_refs` 只经收口指令（`closure_instruction_for_run`）下发，而该指令
`if not dirty.is_dirty and not pending: return None`——同一套证据链接机制既决定「有没有可引用 id」，
也决定「要不要下发允许清单」。于是**最需要清单的那一轮，结构上保证拿不到清单**。

实测：本 Run 全部 14 次 provider 请求里，`task_scope_closure_required` 出现 **0 次**；
`allowed_evidence_refs` 字面量出现 14 次，全部在 `tools[6].description` 里（工具描述叫模型
「引用收口指令的 `allowed_evidence_refs`」，而收口指令根本不存在）。同一个 DB 里其它 5 个 Run
共 42 次请求带有该系统消息——它们都有 `material_events`。

### 1.3 第 5 次为什么成功

第 5 次成功的样本其实在 `native-a6-run4`：Run `product-sdk-bd350dbc…` 第 3 轮
`goal.set` 落库，`evidence_refs` 只有一个 id `0c14c843-0f77-5cd4-b387-f8b1afcceea7`。

- 它**也没有**收口指令（该 Run 的 4 次请求同样 0 次 `task_scope_closure_required`）。
- 模型是从 `task_scope_search` 回执的
  `candidates[0].scope_disclosure.disclosure_manifest.dependencies.evidence[0].evidence_id` 里**反推**出来的。
- 之所以蒙对，是因为该 scope 由 `route=resume_existing` 复用，链接由**先前若干 Run** 铸好
  （`0c14c843` 来自建档 Run 的 `terminal:completed`，另有 4 个 `host.file` 来自更早一个 Run 的 `run_shell`）。

第 9 次模型做了**同构**的推断，只是抓错了字段：`context_route` 回执的
`producer_dependencies.evidence[]`。两个清单都在向模型广播 evidence id，**没有任何东西区分「可引用」和「不可引用」**。
所以第 5 次的成功本身也是运气，不是设计。

---

## 2. 契约怎么说

- **S5b Task 3**（`simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/increments/2026-09-02-s5b-effect-closure-memory/plan.md:181`）：
  只冻结了**谓词**「refs ∈ 该 scope 已链接 evidence」与稳定码，对**拒绝回执的响应体**一字未提。
  同条第 ② 项把 snapshot 注入限定为「只控制收口指令文本」——即 `allowed_evidence_refs` 设计上**只**走收口指令。
- **design-freeze §7**：冻结的是**请求** schema（模型填哪些字段）与拒绝码集合，同样不约束响应体。
- **design-freeze §2**：`host.turn` 记为 `trivial`，目的是**让普通对话不能强行触发收口**，
  不是「用户口述的目标不可登记」。事件 C 已经据此拆掉 `nothing_to_close` 对 `mutate` 的拦截。
- **披露口径**：把这批 id 交给模型**不是新披露**。`semantic_closure.py:465-472/508/553-565` 早就把
  **同一张表、同一个 scope** 的 `allowed_evidence_refs`（上限 64、最旧在前）放进
  `task_scope_closure_required` 系统消息发给模型，生产装配在 `main.py:9195-9208`。
  本次「最新在前、上限 16」是这套已披露集合的**收窄投影**。
  受保护的是内容不是标识：`DECISION-STANDALONE-ROUTE-TOOL-AUTHORITY.md` 拒绝回显 scope **标题**
  （标题在 `_scope_disclosure_reader` 这道披露权威后面），同时明说 `task_scope_id`
  「本来就已经在 `context_route_active_scope_mismatch` 的拒绝里回给模型过，属既有口径」。
- **拒绝必须可执行**：事件 B 的口径「稳定码与 schema 不动，只让文案可行动」，
  以及事件 C 的落地方式「新常量进入拒绝 message（handler 已有 `message += canonical_json(detail)` 的既定渲染）」，
  本次照抄。
- **2026-09-02 code review F-2 早就预言过这一幕**：
  「`allowed_evidence_refs`/`current_revision` 只经此消息暴露给模型……
  若模型在 Run 内自发调用 `task_scope_update`，refs/base_revision 只能猜 → `refs_outside_scope`/CAS 反复。」

结论：披露有界的 evidence **id**（不带载荷）在契约内，且有直接先例；拒绝可执行是既定口径。

---

## 3. 决定与落地

### 3.1 可引用集合 = scope 已链接证据 ∪ 本 Run 已受理的 USER 证据

`_admissible_refs_tx()`（`task_scope_mutation.py`）。第二个来源是
`foreground_turns.evidence_id`——即**本轮正在回答的那条用户消息**的已净化证据。
这正是宿主自己给本轮 Memory 批次用的同一条证据
（`foreground_queue._append_memory_ingestion_outbox_tx`：「turn group's sanitized evidence =
the foreground turn's admitted user evidence」）。第 9 次现场里它就是 `20f23807-…`，
`source_kind='user_message'`，`envelope_sha256 = 9cd8f80a…`——**模型第 1 次就引对了这个 id**，
只是同时夹带了 3 个错的，被整条拒掉，回执还把这个对的一起列进「违规 ref」里。

严格 fail-closed，查询里没有任何模型输入：

```
foreground_run_sdk_bindings b
  JOIN foreground_runs r  ON r.host_run_id = b.host_run_id
  JOIN foreground_turns t ON t.turn_id = r.turn_id AND t.subject = r.subject
  JOIN human_memory_evidence e ON e.evidence_id = t.evidence_id
WHERE b.sdk_run_id = ? AND r.host_run_id = ? AND r.subject = ?
  AND e.subject = ? AND e.envelope_sha256 = t.evidence_hash
  AND (t.task_scope_id IS NULL OR t.task_scope_id = ?)
```

受理后 `store._link_refs_tx` 会把它链接到本 scope，**守卫的后置条件被恢复**——这是一次
bootstrap，不是把闸打开。缺前台表的单测夹具走 `no such table` 分支，贡献空集（宁可不放宽）；其余 `OperationalError`（锁、I/O）原样上抛，绝不吞成一次伪造的 `refs_outside_scope`。

### 3.2 拒绝回执披露（仅 id）

`task_scope_update_refs_outside_scope` 的 detail 新增：

| 字段 | 含义 |
| --- | --- |
| `refs` | （不变）违规 ref，≤8 |
| `allowed_evidence_refs` | 可引用 id，**≤16，最新在前，仅 id**；模型**不需要**也不应发 `content_hash` |
| `allowed_evidence_refs_total` | 可引用集合总数（说明是否被截断） |
| `current_turn_evidence_ref` | 本轮 USER 消息自己的 evidence id（只在存在时出现） |
| `next_step` | 可执行下一步：原样重发本条载荷，`evidence_refs` 与每条 operation 的 `evidence_refs` 从 `allowed_evidence_refs`（或收口指令给过的那份）里取；明确禁止用 run id / call id / effect id / 信封哈希 / 前缀形式拼 id |

集合为空时（本 Run 确实无解）：`allowed_evidence_refs: []` + 明说「本 Run 没有可接受的载荷，
不要再换 id 试，把内容放进最终回答，档案由宿主在 Run 终态自己收口」——**模型循环有终点**。

### 3.3 有界升级提示

同一 Run 内同码拒绝次数取自 `host_pre_admission_audit` 行本身（在写审计行的同一个
`BEGIN IMMEDIATE` 里数），超过 `_REFS_ESCALATION_AFTER = 2` 后 detail 追加 `escalation`：
点名已被拒 N 次、停止编造 id、要么照 `allowed_evidence_refs` 原样发、要么停止调用本工具去答复用户。
**不新增状态**（纯从审计行派生）、**不放宽守卫**、每次升级都有审计行可查。

为什么需要它：事件 D 的 provider 协议重采（`_MAX_PROVIDER_PROTOCOL_ATTEMPTS = 2`）是另一条车道，
够不着工具拒绝；SDK 侧 `max_consecutive_same_tool=10` 的重复键是「工具名 + canonical arguments hash」，
模型每次改 `evidence_refs` 就重置连击，实际只剩 `max_turns=25` 兜底 → `react_max_turns_exceeded` → `driver_failed`。

### 3.4 工具描述

`TASK_SCOPE_UPDATE_DESCRIPTION` 补一句：没拿到收口指令的清单时，先如实发一次，
拒绝回执会在自己的 `allowed_evidence_refs` 里公布可引用 id；不得用 run id / call id / effect id / 哈希拼 id；
`content_hash` 永远由宿主解析、模型不发。

### 3.5 不改的东西

稳定码集合、请求 schema、状态迁移表、审计行写入点、`nothing_to_close` 与 `scope_unbound` 的判据、
收口指令的下发条件，全部原样。

---

## 4. 冻结文本漂移（留痕，本轮不改）

S5b Task 3 与 design-freeze §7 把谓词写成「refs ∈ 该 scope 已链接 evidence」，
本次实现是「∈ 该 scope 已链接 evidence **∪ 本 Run 已受理的 USER 证据**」。
两份文件在 memory-sdk 仓、本轮口径为**只读**，故照事件 C 的先例留痕，由 program 侧下次改 design-freeze 时一并修：

> §7 / Task 3 的 refs 谓词应改为「refs ∈ 该 scope 已链接 evidence ∪ 本 Run 已受理的 USER 证据
> （`foreground_turns.evidence_id`，subject/Run 双绑定 + `envelope_sha256` 校验）；受理后即链接到本 scope」。
> 同时补一句：`refs_outside_scope` 的拒绝回执须披露有界可引用 id 与可执行下一步。

考虑过但否决的「更忠于冻结文本」方案：在 Run 受理时追加一条 `host.turn` 事件并链接本轮 USER 证据，
这样谓词字面成立。否决理由：`CanonicalTaskScopeStore.append_host_event` **根本没有 evidence_refs 参数**
（`TaskEventRecorder.record_turn` 也无生产调用方），要做就得动 canonical 事件流、水位、投影与读视图，
爆炸半径远大于本次修复，且 `host.turn` 一旦入流还要重新论证 dirty/watermark 语义。

---

## 5. 测试

新增 `backend/tests/sdk_adapters/test_task_scope_update_refs_disclosure.py`（真前台队列 + 真 state.db +
真 `CanonicalTaskScopeStore`，复现「Run 内新建 scope、零链接证据」的现场）：

| 用例 | 断言 |
| --- | --- |
| `test_refs_outside_scope_discloses_admissible_ids_and_next_step` | 拒绝带 `allowed_evidence_refs` / `current_turn_evidence_ref` / `allowed_evidence_refs_total` / `next_step`；仍回显违规 ref 且 ≤8；证据的 `evidence_hash` / `envelope_sha256` **值**不过境 |
| `test_disclosed_ref_lets_mid_run_verbatim_goal_set_apply` | **A6-5**：披露的 id 此刻还不在 `task_scope_evidence_links` 里（主干上无解）；用它发 18 KiB（>16384 字节）逐字 `goal.set` → 受理，revision 1→2，`state_json.goal` 与原文**逐字相等**；落库后该 id 成为 scope 已链接证据 |
| `test_gate_stays_fail_closed_when_only_some_refs_are_admissible` | 夹带一个范围外 ref → 整条照旧拒绝，`refs` 精确为那一个，revision 不动 |
| `test_empty_admissible_set_names_the_terminus` | 集合为空 → `allowed_evidence_refs: []`、无 `current_turn_evidence_ref`、`next_step` 为「本 Run 无解」文案 |
| `test_tool_path_audits_every_rejection_and_escalates_after_bound` | Tool 路径每次拒绝写审计行；前 N 次无 `escalation`，第 N+1 次起有且逐字等于升级文案 |
| `test_turn_evidence_needs_both_run_bindings[run_id / host_run_id]` | 加宽来源的两条 Run 绑定各自必需：任一条对不上 → 可引用集合塌成空集，直接引用那条 id 照旧被拒（删掉任一谓词本例即转红） |

基线对照（一次性 detached worktree，跑同一份文件）：

- **主干 7/7 红**，修复后 **7/7 绿**。
- 回归：`test_task_scope_update_tool.py` / `test_task_scope_update_clean_scope.py` /
  `test_closure_request_guard.py` / `test_s5b_acceptance_matrix.py` / `tests/faults` / `tests/task_scope` /
  `test_closure_resume_sources.py` / `test_completed_scope_guidance.py`
  —— 主干与分支 FAILED 集合**逐条完全相同（同样 25 条，均为既有失败）**，
  分支 116 passed vs 主干 109 passed（差值即新增 7 条）。
- 另跑全绿：`test_audit_coverage.py` / `test_primary_foreground_runtime.py` / `test_closure_resume_sources.py` /
  `test_canonical_archive.py` / `test_missing_argument_shape_echo.py` / `test_completed_scope_guidance.py` /
  `test_procedure_binding_ergonomics.py`（61 passed）。

---

## 6. 自审

- **只有 id 过境**：`allowed_evidence_refs` / `current_turn_evidence_ref` 是不透明 uuid；
  `content_hash`、信封、载荷、标题、目标文本一律不出现（用例逐条断言证据哈希值不在回执里）。
- **不放宽守卫**：范围外 ref 照旧 `rejected`，稳定码不变，`_verify_refs_tx` 在 apply 时仍二次校验
  `envelope_sha256`，被拒载荷不推进 revision。
- **subject/Run 双绑定**：可引用集合的两个来源都受 subject 约束，跨 scope / 跨 subject 的证据进不来。
- **确定性**：两个来源都有全序（`enqueue_sequence DESC, evidence_id` 与 `linked_at DESC, evidence_id`），
  同一状态重复调用得到同一份清单。
- **审计不减**：每个拒绝点照旧写 `host_pre_admission_audit`；升级提示本身就派生自这些行。
- **模型循环有终点**：空集合明说无解并要求停手；非空集合超上限后升级提示要求停手。
- **已知未做**：`task_scope_search` 的 `disclosure_manifest.dependencies.evidence[]` 与 `context_route` 的
  `producer_dependencies.evidence[]` 都在向模型广播 evidence id，却**不区分可否引用**——
  第 5 次蒙对、第 9 次蒙错都源于此。给这两个清单加「可引用」标注属于驱动/披露面改动，本轮未做，见 §7。

---

## 7. 遗留与下一步

| 编号 | 事项 | 归属 | 本轮是否做 |
| --- | --- | --- | --- |
| U-1 | design-freeze §7 / S5b Task 3 的 refs 谓词按 §4 扩写 | program 侧（memory-sdk 仓） | 否（留痕） |
| U-2 | `task_scope_search` 的 `disclosure_manifest.dependencies.evidence[]` 与 `context_route` 的 `producer_dependencies.evidence[]` 标注「可否作 `evidence_refs` 引用」 | 披露面 / 驱动侧 | 否 |
| U-3 | 收口指令的下发条件：干净 scope 也给一份 `allowed_evidence_refs`（现在 `closure_instruction_for_run` 在干净 scope 返回 `None`） | 需单独论证——它同时控制收口指令**文本**，改动会让普通对话轮也看到收口措辞 | 否 |
| U-4 | 两条通道的排序不一致（收口指令最旧在前上限 64；拒绝回执最新在前上限 16） | 若后续统一，以「最新在前」为准并同步收口指令 | 否 |
| U-5 | PERSONA / 工具描述是否还需驱动侧或 prompt 侧改动 | 本轮已改静态 `TASK_SCOPE_UPDATE_DESCRIPTION`；是否再在 PERSONA 里点明「evidence id 不可推导」，待下一次原生旅程观察 | 否 |

---

## 8. 独立评审（只读 opus，一次）

结论：**无 MUST-FIX**。授权/披露、fail-closed、与 store `_verify_refs_tx` 的一致性、审计、契约取舍四项均判通过。
已落地的 SHOULD-FIX / NIT：

| 项 | 处理 |
| --- | --- |
| 空集合时升级文案与「不要再试」自相矛盾 | 升级提示改为仅在 `allowed_evidence_refs` 非空时附加 |
| `except aiosqlite.OperationalError` 过宽（会吞掉 `database is locked` → 伪造一次 `refs_outside_scope`） | 收窄为只吞 `no such table`，其余原样上抛；并在 docstring 里钉死「本函数在显式事务外调用」这个前提 |
| 加宽查询缺 `task_scope_id` 谓词（跨 scope 不变量靠调用方保证） | 补 `AND (t.task_scope_id IS NULL OR t.task_scope_id=?)`，把不变量落在断言它的查询里 |
| 缺「别的 Run 不能引用本轮未链接 USER 证据」的用例 | 新增参数化用例，分别打掉 `sdk_run_id` / `host_run_id` 绑定 |
| 升级用例断言近乎空转（只找字符 `"3"`） | 改为逐字匹配渲染后的升级文案 |
| 空集合分支缺 `allowed_evidence_refs_total` | 补 `0`，保持同一拒绝码的 detail 形状稳定 |
| 文案「ids you cannot derive」不准确（其实是 `uuid5` 确定性派生） | 改为 `cannot construct or guess` |
| 「drawn **only** from allowed_evidence_refs」可能让模型丢掉收口指令给过的合法 ref（两条通道排序/上限不同，见 U-4） | 改为「从下方 `allowed_evidence_refs`（或收口指令的 `allowed_evidence_refs`，若你拿到过）里取」 |

未改（评审已确认无害）：`ORDER BY linked_at DESC, evidence_id` 理论上非全序，但 `_verify_refs_tx`
把 `content_hash` 钉死为不可变的 `envelope_sha256`，加上 `seen` 去重，结果稳定；
工具描述里「先如实发一次以取回 id」会占掉一次升级计数，即实际阈值是 2 次真实错误而非 3 次——
升级只是加提示，无副作用。
