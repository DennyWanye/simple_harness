# 验证器口径修正：A6-3 增长比 / A6-7 生命周期豁免 / A6-11 memory_id 归属

- 日期：2026-09-09
- 触发：HM-TO-A6 第 11 次（`RUN-11-RESULT.md`，证据
  `.local-test-evidence/2026-09-09/native-a6-run11/primary-ui-9izlp1ao/`，
  验证器 JSON `RUN-11-a6-verify.json`）里 A6-3 / A6-7 / A6-11 三项 FAIL
- 涉及：`scripts/native/a6_verify.py`、`plans/2026-09-08-hm-to-a6/00-PLAN.md`
  第 1 节的三行验收口径、`backend/tests/native/test_a6_verify_a6_3_a6_7_a6_11_rules.py`
- 回归基线：同一份验证器在第 9 次（`native-a6-run9/primary-ui-8whts2lo`）与
  第 10 次（`native-a6-run10/primary-ui-j5yjctfj`）证据上的判定**一个都不变**

---

## 0. 结论先行

三项 FAIL **全部是验证器口径缺陷，不是 Host / SDK 缺陷**。三条判据都在拿
「不是本项要判的那块量」当证据：

| 项 | 旧判据实际在判什么 | 本项要判的是什么 |
|---|---|---|
| A6-3 | 开头几轮的**浅 ReAct 循环** vs 末尾几轮的**深 ReAct 循环** | history/装配 质量有没有随**轮次**膨胀 |
| A6-7 | 任何新 revision 都要有 evolution 血缘边 | **内容**前进要有血缘边（生命周期推进按契约没有） |
| A6-11 | 任何 memory_id 字符串出现在 request_json 里 | **图谱结构**（关系 id/哈希/边）有没有进 provider context |

第 11 次三项改判 PASS，第 9/10 次逐项不变（第 9 次的 A6-3 仍 FAIL——它是真的
把 prompt 撑到窗口的 2.40 倍，绝对判据在增长比之前就拦下了）。

---

## 1. A6-3：「后 8 轮」是轮，不是尝试；用户自己那条消息不算增长

### 1.1 事故形态

第 11 次的说明是「后 8 轮 `input_tokens` 峰值是前 8 轮的 **2.269** 倍」。
按真正的轮（= 一条前台 `sdk_run_id`）聚合，同一份证据是 **0.999**。

差在哪：旧实现拿 `sdk_provider_attempt_audit` 的**逐次尝试行**当轮次用。
第 11 次 22 轮共 84 行，前 8 行全落在 T1~T4 的浅循环（每轮 2 次尝试），
后 8 行全落在 T20~T22 的深循环（一轮 8~10 次尝试，工具结果一层层叠上去）。
比出来的是「循环深度」，不是「轮次增长」。plan 的原文写的就是「后 8 **轮**」。

### 1.2 那 18 KB 目标文本在哪

第 11 次 T17 一次性发进来 18 393 字符的逐字目标说明。在 `request_json` 里它有
两种落点：

- **T17 那一轮**（run `a8282151`）：作为当轮 user 消息本体，7 199 字符 ≈
  5 515 token，受保护、裁不掉也不该裁；
- **后续轮次**（run `e3fb7c7d` 等）：模型自己 `read_file` 把 README 读回来，
  落在**本 Run 自己的 tool 结果**里（`current_tool_tokens`，由 F-E2 的
  `current_tool_allowance` / 分页另行有界，是 A6-2 的判域）。

两者都不是「history 组随轮次堆积」。把它们算进增长比，等于拿用户的输入长度
和模型的取证行为当有界性缺陷。

### 1.3 新口径

每轮的**调整后峰值** =
`max(该轮各次尝试的 usage.input_tokens)` − 该次自己那条当轮 user 消息的 token
− 可观测的 reasoning 回传。

- 「当轮 user 消息」= `request_json.messages` 里 `role=user` 且**不是**历史组
  包裹（不含 `historical_causal_group`、`metadata.source != primary_tool_history_v1`）
  的消息。历史组本身正是要判的那块量，一个 token 都不扣。
- 「reasoning 回传」：按 [DECISION-Y-REASONING-ECHO](DECISION-Y-REASONING-ECHO.md)
  §1，它逐字写在 wire payload 的 `reasoning_content` 上，**落库的 canonical
  `request_json` 没有它**；能观测到它的只有 wire 记账那一行，而那行只在越界时
  才打印。所以口径是「观测到多少扣多少」：`native.log` 里 `reasoning_relay=` 的
  峰值为 0 时扣 0（`reasoning_relay_basis = observed_zero`，方向 fail closed，
  不替 Host 减重）；观测到非 0 时按同 Run 更早尝试累计的 `reasoning_tokens`
  这个下界扣（`ledger_cumulative_prior_reasoning`）。第 9/10/11 次实测均为 0
  （型号 `deepseek-v4-flash`，事件 Y 之后 `reasoning_mode` 默认关）。

**绝对判据一律不动，且仍排在增长比之前**：前台 `sdk_context_budget_exceeded`
一次都不许有；`usage.input_tokens` 不许超过 `effective_input_budget`，被实测
闸门 `sdk_provider_wire_input_budget_exceeded` 拦在发出之前的那几次单独取证。
增长比只是这两条之上的补充。

### 1.4 三份证据上的实测（`numbers` 同时记原始与调整）

| 证据 | 旧口径（逐次尝试） | 新口径（逐轮，未调整） | 新口径（逐轮，调整后） | 判定 |
|---|---|---|---|---|
| 第 9 次 | 4.528 | 1.858 | **1.727** | FAIL（绝对判据先拦：4 次 `sdk_context_budget_exceeded`，计费峰值 76 708 = 窗口的 2.40 倍） |
| 第 10 次 | 1.466 | 1.046 | **1.045** | FAIL（绝对判据先拦：1 次 `sdk_context_budget_exceeded`） |
| 第 11 次 | 2.269 | 0.999 | **0.987** | PASS |

第 9 次的调整后比 **1.727 仍越 1.6 上限** —— 换口径没有把真实的膨胀放过去，
这正是这条判据要保住的能力。

### 1.5 备选路线与不采用的理由

- **「receipt 的 planned − protected_messages」**：`run_context_snapshot_receipts`
  的 `source_revisions` 没有 `protected_messages` 字段（有的是
  `planned_input_tokens` / `tool_schema_tokens` / `current_tool_tokens` /
  `budget_headroom` 等）；`protected_messages=` 只出现在
  `sdk_context_budget_exceeded` 那一行日志上，而正常轮次不打这行。不可用。
- **再扣掉 `tool_schema_tokens`**：工具 schema 是个 15 741 → 16 936 字符的大
  常量，扣掉它等于把分母缩小 2/3，比值被放大——实测第 10 次会从 1.046 涨到
  1.832，把一份健康证据判成 FAIL。不采用。
- **再扣掉 `current_tool_tokens`（本 Run 自己的工具结果）**：方向对（那是 A6-2
  / F-E2 的判域），但第 9 次会从 1.858 涨到 2.144、第 10 次从 1.046 涨到
  0.962——两头都动，说明这一项在不同证据上并不单调，收益不抵口径复杂度。
  **不扣**，只作为 numbers 旁证保留。

---

## 2. A6-7：Prospective 到点推进不是内容纠正，按契约没有血缘边

### 2.1 事故形态

第 11 次：「1 个新 revision 缺少 evolution 血缘边:
(`cognitive-memory-654c6a54…`, 2)」。

查库：

| revision | lifecycle_state | content_hash | plan_id |
|---|---|---|---|
| 1 | `pending` | `c3682f0e…` | `host-analysis-plan-5ae9db75…` |
| 2 | `triggered` | `c3682f0e…`（**逐字相同**） | `prospective-signal-plan-045b09ca…` |

内容 `{"action":"有机会时开始学画画","memory_type":"prospective",
"trigger":{...,"trigger_at":1788920760.0}}` 一个字节都没变，变的只有生命周期
状态：到点了。

### 2.2 契约取证

已安装 SDK `simple-harness-memory-sdk-0.6.37-source`：

- `src/simple_harness_memory/backends/sqlite_v5.py:7595` 起，Prospective 信号
  被消费且判定 `APPLIED` 时，走的是
  `_copy_cognitive_revision_unlocked(memory_id, base_revision, committed_revision,
  lifecycle_state=next_state.value, plan_id=_stable_id("prospective-signal-plan",
  authority.authority_id), ...)` + `_copy_cognitive_payload_unlocked(...)` ——
  **逐字复制**上一条 revision，只改 `lifecycle_state`。
- 随后写的是 `_append_prospective_mutation_outbox_unlocked` 与
  `_insert_prospective_trigger_event_unlocked`，再 CAS 前进
  `cognitive_memory_heads.current_revision`。**全程不写 `cognitive_relations`**。
- 血缘边（`relation_domain='evolution'`，kind `amends` / `contests` /
  `supersedes`）是 revise / supersede / contest 这些**内容**通路的产物
  （`core/mutations.py`）。

也就是说：「新 revision 必有 evolution 边」这条判据对生命周期推进恒为 FAIL，
是验证器缺陷。

### 2.3 新口径

对 revision `rN`（N ≥ 2），先看它是不是**纯生命周期推进**：

```
content_hash(rN) == content_hash(rN−1)
  且 ( lifecycle_state(rN) != lifecycle_state(rN−1)
       或 plan_id(rN) 以 prospective-signal-plan / lifecycle-plan 开头 )
```

是 → 豁免血缘边，记进 `lifecycle_only_revisions_exempted` /
`lifecycle_only_revision_samples`（第 11 次为
`("cognitive-memory-654c6a54…", 2, "pending->triggered")`）；
否 → 照旧要求 `evolution` 边 rN → rN−1，缺了判 FAIL。

**内容哈希必须相同是硬门槛**：哈希变了就是内容纠正，哪怕带调度器出身也照样
FAIL（负例测试
`test_a6_7_lifecycle_move_alone_is_not_enough_when_content_changed`）。
`content_hash` 列缺失的旧证据目录一律不给豁免（fail closed，负例测试
`test_a6_7_without_a_content_hash_column_gives_no_exemption`）。
内容哈希相同但 lifecycle 也没动、又没有调度器出身的重复写入同样不豁免。

---

## 3. A6-11：判的是图谱**结构**，不是 memory_id 这串字符

### 3.1 事故形态

第 11 次：`hits_memory_id=37`、`hits_memory_id_only_in_tool_results=false` →
FAIL。逐条归类之后，37 次命中的载体是：

| 载体 | 次数 | 说明 |
|---|---|---|
| `system:prospective_inbox` | **33** | Prospective 到点提醒注入 context（本次新增形态）：`{"count":1,"entries":[{"action":"有机会时开始学画画","memory_id":"cognitive-memory-654c6a54…","occurred_at":…}]}`，`metadata.source=prospective_inbox`、`trust=host_authority` |
| `history_group_tool_result` | 3 | 历史组包裹里、组内 `role=tool` 消息中的 procedure 候选回执 |
| `tool_result` | 1 | 本 Run 自己的 `role=tool` 回执（procedure 候选 `candidates[].candidate.memory_id`） |
| 图谱形状载荷 | **0** | —— |

`relation_id` / `relation_hash` / `twin_graph` / `graph_edge` /
`relation_memory_id` 命中数全部为 **0**。**没有一次命中是图谱载荷**。

旧判据只认「全部落在 `role=tool` 消息里」这一种豁免，`prospective_inbox` 这条
`role=system` 的到点提醒把它掀翻了——但它本来就不是图谱投影，而是 Prospective
机制要求送进 context 的可寻址回执。

### 3.2 新口径

每一次 memory_id 命中按**载体**归类（消息 `role` + `metadata.source`），直方图
进 `numbers.memory_id_hit_histogram`。判 FAIL 的只有**结构性载体**：

- `relation_id` / `relation_hash` 任一取值命中；
- `twin_graph` / `graph_edge` / `relation_memory_id` 结构键命中；
- memory_id 落在**图谱形状载荷**里——命中点前后各 1 200 字符的窗口内出现
  `source_memory_id` / `target_memory_id` / `relation_kind` / `relation_domain` /
  `source_revision` / `target_revision` / `relation_id` / `relation_hash` /
  `twin_graph` / `graph_edge` / `relation_memory_id` / `"edges"` 任一键。

回执/通知类载体（工具回执、历史组内工具回执、事件 V 的 `conflict_notice`、
事件 Y / F-EPI-1 的召回提示、Prospective 的 `prospective_inbox`）判 PASS，并把
直方图逐字写进说明。`request_json` 解析不出来时按结构性命中记（fail closed）。
`--strict-a6-11` 保持 plan 字面语义：任何 memory_id 命中即 FAIL。

**这一轮的结论**：37 次命中里没有一次是图谱载荷，所以第 11 次的 A6-11 是
误判，改 PASS。若哪一次真的出现图谱载荷，判据照旧 FAIL（负例测试
`test_a6_11_fails_when_a_memory_id_rides_a_graph_shaped_payload`）。

---

## 4. 回归证据

`python scripts/native/a6_verify.py --evidence <E> --installed-target
.local-test-evidence/2026-09-07/installed-h0710-m0637-s0313 --out <scratch>/…`

| 证据 | 改前 tally | 改后 tally | 变动项 |
|---|---|---|---|
| 第 9 次 `native-a6-run9/primary-ui-8whts2lo` | PASS 10 / FAIL 6 / INCONCLUSIVE 2 | 同 | 无 |
| 第 10 次 `native-a6-run10/primary-ui-j5yjctfj` | PASS 14 / FAIL 2 / INCONCLUSIVE 2 | 同 | 无 |
| 第 11 次 `native-a6-run11/primary-ui-9izlp1ao` | PASS 13 / FAIL 4 / BLOCKED 1 | PASS 16 / FAIL 1 / BLOCKED 1 | A6-3、A6-7、A6-11 三项 FAIL → PASS，其余逐项不变 |

（改前基线取的是当前 main `59b08b81`，已含事件 AC 的 A6-5 水位判据；第 11 次
唯一剩下的 FAIL 是 NC-3。）

测试：`backend/tests/native/test_a6_verify_a6_3_a6_7_a6_11_rules.py` 19 条，
在 main 的验证器上 13 红 6 绿、在本改动上 19 绿；`backend/tests/native/` 全量
89 条通过（其中
`test_a6_verify_budget_bypass.py::test_a6_3_judges_the_foreground_lane_and_reports_the_others_separately`
的夹具同步修正为「17 轮 = 17 条前台 Run」，它自己的注释写的就是「17 轮」）。
`a6_verify.py --selftest`、`manual_verify.py --selftest`、
`twoflow_verify.py --selftest` 均 OK。
