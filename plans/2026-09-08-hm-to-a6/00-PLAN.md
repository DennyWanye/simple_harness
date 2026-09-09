# HM-TO-A6 原生真实模型验收方案（2026-09-08）

> 义务：`HM-TO-A6`（delivery，AC = HM-AC-2 / HM-AC-6）
> 决定性测试原文（SDK 仓 `simple-harness-memory-sdk` 的 `plans/2026-08-29-human-memory-digital-twin/acceptance.md`「测试义务矩阵」HM-TO-A6 行）：
> 「20+ turn/大型 tool result 动态组装 + README/STATUS 超限拆分；clean-wheel public API 在同一 plan
> 创建节点与 relation memory，验证 edge 更新/纠正/争议/ordinary projection policy 过滤/relation 或
> endpoint 遗忘/close-reopen；snapshot 重放断言图谱内容不进入 Provider Context」
>
> 执行方式：**真实主模型 + 原生 App + System Events UI 驱动，无截图**。
> 驱动脚本：`scripts/native/a6_driver.sh`。
> 证据：SDK 库 `human_memory_v7.db`、Host `state.db`、`operation-audit.db`、
> Harness SDK `simple-harness-sdk/execution-v6.sqlite3`、`native.log`。

---

## 0. 调查结论：ContextSnapshot 到底落在哪里

`backend/deskpet/agent/context_snapshot_store.py` 是 **STUB**（已被移除、仅防崩溃），不要拿它当证据源。
真实的 ContextSnapshot 三段式落库如下（已在 r11 证据库上实测确认）：

| 层 | 位置 | 关键列 | r11 实测 |
|---|---|---|---|
| Host 冻结的"准备好的上下文" | `PreparedSdkContextSnapshotV1.build()`（`backend/deskpet/execution/primary_context.py` 末尾）→ `snapshot_id = sdk-context:<64hex>` | 作为 `FrozenContextAuthority.authority_ref/authority_hash` 传给 SDK | — |
| Host 每个 provider turn 的快照回执 | `state.db` → **`run_context_snapshot_receipts`**（迁移 `037_context_route_ledger_v45.sql`） | `snapshot_id = ctx-snap:<sdk_run_id>:<ordinal>:<..>`、`provider_turn_ordinal`、`snapshot_revision`、`source_revisions_json`、`payload_hash`、`expected_request_fingerprint`、`receipt_hash` | 11 行 |
| provider 实际发送 | Harness SDK `simple-harness-sdk/execution-v6.sqlite3` → **`provider_invocations`** | `request_json`、`request_fingerprint`、`usage_json`、`state` | 11 行 |
| provider 结算审计（Host 侧投影） | `state.db` → **`sdk_provider_attempt_audit`** | `snapshot_id`（`sdk-context:` 家族）、`input_tokens`、`total_tokens`、`state` | 11 行 |

补充事实：
- `state.db` 的 `sdk_context_public_snapshots`（迁移 020）在当前构建 **为空**（r11 实测 0 行），
  **不得**把它当 PASS 依据；快照身份走 `sdk_provider_attempt_audit.snapshot_id`。
- 日志侧三元组：`native.log` 每个前台 Run 出现一组
  `context.preparing` → `context.staged` → `context.consumed`（logger `simple_harness.host_observability`）。
  三者缺一即为组装异常。
- 一个 Run 内多个 provider turn 共享同一个 `sdk-context:` 快照 id（r11 实测 2 个 invocation 同 id），
  而 `run_context_snapshot_receipts` 每个 provider turn 一行、`provider_turn_ordinal` 从 1 递增。

动态组装实现要点（决定本方案的阈值）：
- `backend/deskpet/sdk_adapters/causal_groups.py`：`DEFAULT_LARGE_RESULT_BYTES = 16_384`
  → **大 tool result 的门限是 16 KiB，不是 32 KiB**；`groups_max = 10`。
- `backend/deskpet/sdk_adapters/context_partitions.py`：`PARTITION_CAPS` 三档（4096/8192/32768）；
  `budget_window()` 取 **不超过 window 的最大档**。gpt-5.6-luna window=32000 → 落 **8192 档**：
  `recent_causal_groups = {groups_max:10, items_max:80, bytes_max:196608}`；
  `effective_input_budget = 32000 - 2048 - safety_margin`。
- `primary_context.py::prepare()` 的裁剪是 `while over_cap(): complete.pop(0)`
  —— **整组丢弃、从最旧开始**，因此 tool call/result 的因果顺序永不被撕开。
- `primary_context_pages.py`：历史组里 `role=tool` 且 >16 KiB 的内容被替换为
  `primary_tool_result_summary_v1`（含 1024 字节 `excerpt`、`reference_id = primary-tool-page:v1:<64hex>:<offset>`、
  `page_tool="context_page_in"`），并给该消息打 `metadata.source = primary_tool_history_v1`。
  `admitted_page()` 每页 `PAGE_BYTES = 1024`，返回 `next_reference_id` 供顺序翻页。
- `current_tool_pages.py`：当前 Run 内已结算 effect 的分页前缀是 `primary-effect-page:v1:`；
  `CONTROL_TOOLS`（`context_route`/`context_page_in`/`task_scope_search`/`task_scope_update`）**不被摘要**。
- `context_page_in` 工具 schema 只有 `reference_id` + `source_hash`（`context_page_in_tools.py`）。
- `task_scope/projections.py`：`VIEW_LIMITS = {README:16 KiB, PLAN:32 KiB, STATUS:12 KiB, DECISIONS:32 KiB,
  RESUME:24 KiB, EVIDENCE:16 KiB}`；`_bounded_view()` 对 README 做「前缀 + `…[bounded; details are
  content-addressed in EVIDENCE]`」，对其余视图整体换成
  `{"bounded":true,"view_kind":...,"full_content_sha256":...,"full_byte_length":N,"details_view":"EVIDENCE"}`；
  细节以 `TARGET_BLOCK_BYTES = 31 KiB` 切块进 `task_scope_read_blocks`。
- README/STATUS 的可控放大量只有 `goal`：`store.py` 建档时 `identifier(goal,"goal",16_384)`，
  `protocol.py` 对 mutation 的 `operation_value` 上限 `32_768` → 一条 ~18 KiB 的 `goal.set`
  同时打爆 README(16 KiB) 和 STATUS(12 KiB)，这是本方案触发超限拆分的**唯一确定杠杆**。

---

## 1. 验收目标 → 可观测证据映射

| 目标条款 | 可观测证据 | PASS 判定 |
|---|---|---|
| A6-1 20+ turn 动态组装 | `foreground_run_heads` ≥24 行且全部终态；`run_context_snapshot_receipts` 每 Run 的 `provider_turn_ordinal` 从 1 连续无洞 | 24 轮全部有终态 Run；receipt ordinal 连续 |
| A6-2 大 tool result 分页 | 历史组投影出现 `primary_tool_result_summary_v1`；`provider_invocations.request_json` 中该消息带 `metadata.source=primary_tool_history_v1`；`execution-v6.sqlite3` 的 `execution_effects.tool_name='context_page_in'` 计数增加 | ≥2 个不同 `reference_id` 前缀 `primary-tool-page:v1:` 被摘要；≥2 次 `context_page_in` 成功返回 `ok=true` 且答案与原文逐字一致 |
| A6-3 预算内有界 | 每次 `provider_invocations.request_json` 的 token 估算 ≤ `effective_input_budget`；`sdk_provider_attempt_audit.input_tokens` 单调有界（不随轮次线性增长到窗口上限） | 最大 `input_tokens` < 32000 − 2048；后 8 轮的 `input_tokens` 不超过前 8 轮最大值的 1.6 倍 |
| A6-4 裁剪不破坏因果链 | `request_json.messages` 中每个 `historical_causal_group` 的 `messages` 内部 tool 消息与其发起 assistant 消息同组；被裁的组整组消失 | 任一请求中不存在孤立 tool 消息；组数 ≤10 |
| A6-5 README/STATUS 超限拆分 | `state.db.task_scope_read_view_revisions`：README `content` 以 `…[bounded; details are content-addressed in EVIDENCE]` 结尾且 `length(content) ≤ 16384`；STATUS `content` 含 `"bounded":true` 且带 `full_content_sha256`/`full_byte_length` | 两视图都进入 bounded 形态，且 EVIDENCE 视图的 `event_count` == `task_scope_events` 实际行数（canonical facts 不丢） |
| A6-6 同一 plan 创建节点 + relation memory | `human_memory_v7.db.cognitive_relations` 新增 1 行；该行的 `plan_id`/`plan_hash` 与**本 plan 新建的端点**（流程节点）以及 relation memory 自身在 `cognitive_memory_revisions` 里的 `plan_id`/`plan_hash` **相同**；已有端点记录其当时的 `current_revision`；`relation_memory_id` 在 `cognitive_memory_heads` 中存在 | 一条 `relation_kind='applies_to'`：target 是本 plan 新建流程节点的 exact revision，source 是 T1 那条 Python 版本事实的 current revision。**不再要求两端都是本 plan 的 revision**——那要求本轮再造一条同值 semantic（第二个槽位，正是事件 L 要消灭的东西），而 SDK 仓 `simple-harness-memory-sdk` 的 `plans/2026-08-29-human-memory-digital-twin/acceptance.md`「测试义务矩阵」HM-TO-A6 行对本项的要求逐字只有「clean-wheel public API 在同一 plan 创建节点与 relation memory」。同文件另有两处更紧的措辞：HM-S12 场景行「clean-wheel public API 创建**两个** canonical nodes + 一条 relation memory」、HM-TO-A2 行「clean-wheel public API 在同一原子 plan 正向创建**两个端点**及一条 `applies_to` Semantic relation」；「两个 canonical node」这条义务由 **HM-TO-A2 的 clean-wheel oracle** 履行（SDK 公共 API 直接造两个端点，不经分析车道），本项不重复证明，追踪项 F-T6。**只新建 source**（本轮再造一条同值语义再连一条旧流程）与本行相反，判 FAIL。裁决见 [DECISION-T-RELATION-FORM.md](DECISION-T-RELATION-FORM.md) §1 |
| A6-7 edge 更新/纠正 | 纠正后 `cognitive_memory_revisions` 出现新 revision（`lifecycle_state` supersede 语义），旧 revision 退出 active；图谱边指向新 revision | 图谱只显示 1 条 active edge，端点为新 revision |
| A6-8 争议 | `cognitive_conflict_groups` 新增行（`incumbent_revision`/`challenger_revision`）；对应 head 的 `conflict_status` 变 contested | 出现 contested；且依赖该值的执行问句得到"要求确认"而非直接用旧值 |
| A6-9 ordinary projection policy 过滤 | `twin graph view`（`PrimaryCognitiveControls.list` → `manager.get_twin_graph_view`）返回的 node/edge 中不含 `redacted` 项；contested/suppressed 期间 edge 不出现 | 争议/遗忘态下普通图谱 edge 数按预期降到 0 |
| A6-10 relation/endpoint 遗忘 + close/reopen | 遗忘后 `cognitive_relations` 行仍在（append-only，不物理删除），但普通图谱 0 边；关闭图谱面板再打开仍 0 边 | 关表重开边数不复活；`human_memory_v7.db` 的关系行数不减少 |
| A6-11 图谱不进入 Provider Context | 全量 `provider_invocations.request_json` 中不出现 `cognitive_relations.relation_id` / `relation_hash` / `cognitive_memory_heads.memory_id` 任一取值，也不出现 `twin_graph` / `graph_edge` / `"edges"` 结构键 | 命中数 = 0；且 T16/T24 两次纯 UI 图谱操作 **不新增** `provider_invocations` 行 |
| A6-12 snapshot 重放 | 用安装目标的 venv：`from simple_harness.execution.provider_invocations import provider_request_from_json, provider_request_fingerprint`，对每行 `request_json` 重算指纹 | 重算值 == `provider_invocations.request_fingerprint` == 同 (run, ordinal) 的 `run_context_snapshot_receipts.expected_request_fingerprint` |

---

## 2. 对话脚本（24 轮，≥22 要求满足）

同一永久主对话内完成；主 TaskScope =「秋分资料整理」。`T` 列即 `a6_driver.sh` 的轮号（`--start` 可从任意轮续跑）。

| T | 用户输入（原文） | 预期行为 | 预期 DB/日志证据 | PASS |
|---:|---|---|---|---|
| 1 | 记住：我做资料校对时，统一用 Python 3.12 跑脚本。 | 明确记住 → 结构化提案 | `cognitive_memory_heads` +1（semantic）；`accepted_analysis_plans` +1 | 落库且 `epistemic_status` 为用户原话类 |
| 2 | 另外记住：我的校对结果一律存到「外接硬盘 / 校对归档」这个目录。 | 第二条早期事实（供 T19 远距召回） | `cognitive_memory_heads` +1 | 同上 |
| 3 | 把我上一句话改得更简洁一点。 | **负控**：no_recall | `context_route_decisions.origin='no_recall'` 或无 recall 记录；`llm_invocations` 不新增召回类 | 不查询长期库 |
| 4 | 新建一个项目任务：秋分资料整理。 | `context_route(create_new)` | `task_scope_*` 建档；`context_route_decisions.route='create_new'` | 有 exact scope + binding |
| 5 | 在这个任务里，先找一下你有没有能读本地文件的工具。 | `tool_search`/`tool_activate` | `operation-audit.db` 出现 tool_search/tool_activate | `read_file` 进入 catalog |
| 6 | 读 `<FIXTURE_A>` 的全文，先只告诉我它的标题和总行数。 | 大 tool result #1（~40 KiB） | 本轮 `provider_invocations` 有 >16 KiB tool 消息 | 工具成功返回 |
| 7 | 把这次任务的目标记下来：把 FIXTURE_A 里的清单核对一遍。 | `task_scope_update(goal.set)` | `task_scope_events` +1；`task_scope_read_view_revisions` 新 revision | goal 落 canonical state |
| 8 | 再读 `<FIXTURE_B>` 的全文，告诉我它的第一行是什么。 | 大 tool result #2（~48 KiB） | 同 T6 | 工具成功返回 |
| 9 | 这两个文件分别是干什么用的？就用你已经看到的内容回答。 | 普通推进 | 本轮请求里 T6/T8 的 tool 结果已被 `primary_tool_result_summary_v1` 摘要 | 出现摘要 + `reference_id` |
| 10 | 记一个决定：以 FIXTURE_A 为准，FIXTURE_B 只作参照。 | `decision.record` | `task_scope_read_view_revisions.DECISIONS` 含该决定 | 决定入 canonical |
| 11 | 主清单 A 里 `ANCHOR-ALPHA` 那一条的完整取值是什么？照原文给我，不要概括。 | **page-in #1** | `execution_effects.tool_name='context_page_in'` 出现；返回 `primary_tool_history_page_v1` | 答案逐字等于 fixture 内锚点值 |
| 12 | 顺便问一句，今天几号？ | **负控**：无关闲聊不改 active scope、不建新 scope | `context_route_decisions` 无 `create_new`；active scope 不变 | scope 未漂移 |
| 13 | FIXTURE_B 里 `ANCHOR-BETA` 后面那一整行原文是什么？ | **page-in #2** | 同 T11 | 答案逐字命中 |
| 14 | 以后有机会我想学画画。 | **负控**：只进 Semantic Goal（分析协议 v10：模糊将来愿望不得成为 Prospective，`time` 触发必须由引文里的时间表达接地——事件 AE） | 无 pending Prospective 行；`prospective_scheduler_registrations` / `prospective_trigger_events` 本轮不新增 | 不调度不提醒 |
| 15 | 记住：秋分资料整理这套校对流程，就按我前面说的 Python 环境执行。 | **同一 plan 建流程节点 + relation memory**（分析协议 v9 分支②） | `cognitive_relations` +1（`applies_to`）；target 端 revision 的 `plan_id`/`plan_hash` 与关系行一致；source 端是 T1 事实的 current revision；本轮**不**新增第二条 Python 版本 semantic | A6-6 |
| 16 | （**UI 操作**，不发消息）打开记忆图谱面板，读取节点/边 | 普通图谱出现 1 条 `applies_to` 边，两端分别是 T1 的语义节点与 T15 新建的流程节点；relation memory 自身不作为节点出现（节点总数按当轮已落库记忆计，不是 2） | 图谱读取前后 `provider_invocations` **行数不变** | A6-11 前半 |
| 17 | （脚本发送 ~18 KiB 目标说明）把下面这段完整的目标说明**逐字**记为这个任务的目标，不要概括、不要省略：`<GOAL_TEXT>` | `task_scope_update(goal.set)` 超长值 | README 视图 bounded 截断；STATUS 视图 `"bounded":true` | A6-5 |
| 18 | 你把刚才那段目标保存成功了吗？把它的前两句原样复述一遍。 | 复核（若 T17 被概括，本轮是第二次机会） | 同 T17 | A6-5 兜底 |
| 19 | 按我**最早**说过的，校对结果该存到哪里？只依据我以前说过的回答。 | 跨 15+ 轮后引用早期事实：T2 已被整组裁出最近 10 组，必须走召回补位 | 本轮 `request_json` 中不含 T2 原文的 causal group；出现 typed recall（`memory_call_attempts` / recall 决策） | 答出「外接硬盘 / 校对归档」且来源是召回而非上下文 |
| 20 | 更正一下：校对脚本我现在统一用 Python 3.13，不是 3.12。 | 明确纠正 → supersede | `cognitive_memory_revisions` 新 revision；旧 revision 退出 active | A6-7 |
| 21 | 不过我印象里上周好像还是按 3.12 在跑的，你说呢？ | 含糊相反说法 → contested | `cognitive_conflict_groups` +1；head `conflict_status` = contested | A6-8 |
| 22 | 那你现在按哪个版本执行这套校对流程？ | 争议期依赖该值 → 要求确认 | 回答含明确的"需要你确认"，且不直接执行 | A6-8 后半 |
| 23 | 把「这套流程按那个 Python 环境执行」这条关系忘掉。 | relation 逻辑遗忘 | `cognitive_relations` 行数**不减**（append-only），suppression 记录写入 | A6-10 |
| 24 | （**UI 操作**）关闭图谱面板 → 重新打开 → 再问一次 T22 | close/reopen 不复活 | 普通图谱 0 边；回答不再引用该关系 | A6-10 / A6-9 |

固定文本与夹具由 `a6_driver.sh` 自动生成（默认 `~/SimpleHarnessWorkSpace/a6-fixture/`，
即默认 workspace root 的真实后代，可被 Auto 模式绑定），已实测：
- `qiufen-checklist-a.md` 40003 B，`ANCHOR-ALPHA: 归档编号 QF-2026-0908-ALPHA-7731` 在字节 2937
  （越过 1024 B `excerpt`，需 3 次翻页）；
- `qiufen-reference-b.md` 47670 B，`ANCHOR-BETA` 在字节 2549，其下一行是唯一串
  `QF-2026-0908-BETA-4419-…`；
- `qiufen-goal.txt` 18291 B（> README 16384 且 > STATUS 12288，< `operation_value` 上限 32768）。

---

> **2026-09-08 用户决定后的调整**：T23 改为 **UI 操作**——在记忆面板「关系图」中对该关系（或其端点记忆）点击「忘记这条记忆」，记录 suppression 行；T24 保持关闭/重开图谱并重发 T22。对话式遗忘（模型面 `memory_forget` 忘记认知记忆）记为 F10，随 S5c 模型可见记忆视图一起做。


> **2026-09-08 尝试 4 教训**：README/STATUS 视图按读取时物化（`task_scope_read_view_revisions` 不随 `goal.set` 自动重生成），T18 改为要求模型读取 README/STATUS 视图并报告是否截断，否则 A6-5 永远 INCONCLUSIVE。

## 3. ContextSnapshot 审计核对方法

**（a）每次真实发送内容与 snapshot 一致**

```
# 1) SDK 侧实际请求指纹 == 自身 request_json 重算指纹（重放）
$INSTALLED/backend/.venv/bin/python - <<'PY'
import json, sqlite3
from simple_harness.execution.provider_invocations import (
    provider_request_from_json, provider_request_fingerprint)
db = sqlite3.connect("<userdata>/data/simple-harness-sdk/execution-v6.sqlite3")
for iid, rid, fp, rj in db.execute(
        "select invocation_id,run_id,request_fingerprint,request_json from provider_invocations"):
    assert provider_request_fingerprint(provider_request_from_json(json.loads(rj))) == fp, iid
PY
# 2) Host 冻结快照的期望指纹 == SDK 实际指纹（按 run + ordinal 对齐）
#    state.db: run_context_snapshot_receipts(sdk_run_id, provider_turn_ordinal, expected_request_fingerprint)
#    execution-v6: provider_invocations(run_id, 按 claimed_at 升序的第 N 次)
# 3) 同一行内 payload_hash == expected_request_fingerprint（r11 实测恒等，作为回执自洽断言）
```

PASS：三条全等，且 `count(run_context_snapshot_receipts where sdk_run_id=R)`
== 该 Run 的 `provider_invocations` 行数；`native.log` 每 Run 有且仅有一组
`context.preparing`/`context.staged`/`context.consumed`。

**（b）超预算裁剪可追溯**

- 逐轮解析 `request_json.messages`，统计 `historical_causal_group` 的组数与 `source_ref` 集合；
- 断言组数 ≤ 10（`groups_max`）、`canonical_json(rows)` 字节 ≤ 196608（8192 档 `bytes_max`）、
  估算 token ≤ `effective_input_budget`；
- 断言「被裁掉的 `source_ref` 一定是更旧的那些」——即相邻两轮的 `source_ref` 序列满足后缀单调
  （只从头部丢组，符合 `complete.pop(0)`）；
- `run_context_snapshot_receipts.source_revisions_json` 逐轮对照，记录裁剪发生的轮次。
- 未发生任何裁剪（组数一直 <10 且未超字节）时，本项记 **INCONCLUSIVE**，不能记 PASS
  —— T19 之前必须已经出现至少 1 次整组丢弃，否则脚本轮数不足以证明有界性。

**（c）tool-call 因果链完整**

- 对每个 `historical_causal_group.messages`：若含 `role=tool` 的项，必须在同组内且顺序与原始
  `sanitized_payload.messages` 完全一致（`primary_context_pages.project_history_group` 保证整组以
  quoted user 消息形式出现，不会产生裸 tool 消息）；
- 断言任一 `request_json.messages` 顶层不存在 `role=tool` 的原生消息；
- 断言被摘要的 tool 消息里 `content_hash` == 原 `evidence_envelopes.sanitized_payload` 中该消息内容的
  SHA-256，且 `content_bytes` 与原长一致 —— 证明摘要是可逆引用而不是有损改写。

**（d）图谱不入 Context（snapshot 重放断言）**

```
needles = set(relation_id) | set(relation_hash) | set(memory_id from cognitive_memory_heads)
          | {"twin_graph","graph_edge","relation_memory_id"}
for rj in provider_invocations.request_json: assert not (needles & tokens(rj))
```
并断言 T16/T24 两次纯 UI 图谱操作的时间窗内 `provider_invocations` 行数增量 = 0。

---

## 4. 负控与不计入 PASS 的情形

**负控（必须为"不发生"）**
1. T3 简单改写不得触发长期库查询（no_recall）。
2. T12 无关闲聊不得创建/切换 TaskScope。
3. T14 模糊愿望不得产生 pending Prospective 或提醒。
4. T22 争议期不得直接使用旧值 3.12 执行。
5. T16/T24 图谱操作不得新增 `provider_invocations` 行。
6. 全程 `request_json` 不得出现凭据形状（`sk-`/`Bearer `/`ghp_` 等，`protocol.py::_CREDENTIAL_PATTERNS`）。

**不计入 PASS（记 BLOCKED / INCONCLUSIVE，不记 FAIL）**
- provider 传输超时后 Run 停摆（**已知缺陷 F06**，见 `NATIVE-R11-PROCEDURE-CHAIN.md`）：
  出现 `transport_timeout` / `provider_attempt.degraded` / `reconcile.unknown_settled` 且 Run 长期 RUNNING
  → 该轮 BLOCKED，重启同 userdata 从该轮 `--start` 续跑；不作为 A6 的功能失败。
- 模型拒不调用 `tool_search`/`read_file`（T5/T6/T8 失败）→ 大结果分页无法触发，记 BLOCKED（环境/路由），
  不记 A6-2 FAIL。
- T17/T18 两次机会后模型仍把长目标概括（未逐字 `goal.set`）→ README/STATUS 未超限，A6-5 记
  **INCONCLUSIVE**；改由「同一 TaskScope 内追加多条 `decision.record` 把 PLAN 顶到 32 KiB」作为备用杠杆重试一次。
- 首个进程窗口消失但进程仍在（r11 已见）→ 同 userdata 重启继续，不影响判定。
- 任何一轮超过 6 分钟未见终态：脚本记 `timeout` 并继续，该轮所有断言降级为 INCONCLUSIVE。
- 截图缺失不影响本义务判定（本轮无 computer-use 授权，全部以 DB/日志为证据）。

**明确的 FAIL 条件**
- 重放指纹与 `expected_request_fingerprint` 不等；
- 请求中出现孤立 tool 消息或组数 >10；
- README/STATUS 超限后 EVIDENCE 里 canonical 事件数变少；
- 图谱标识出现在任一 `request_json`；
- 遗忘后 `cognitive_relations` 行被物理删除，或 close/reopen 后 edge 复活。

---

## 5. 执行与证据归档

```
python scripts/native/launch_native_candidate.py --launch \
  --source <worktree> --bundle <verify.app> --installed-target <installed> \
  --evidence-root .local-test-evidence/2026-09-08/native-<host-sha>/ --port 18120
# 记下打印的 evidence 目录 <E>，其 userdata 在 <E>/userdata
bash scripts/native/a6_driver.sh <bundle-id> <E>/userdata <E> [start-turn]
```

退出后对 `<E>/userdata/data/{human_memory_v7.db,state.db,operation-audit.db}`、
`<E>/userdata/data/simple-harness-sdk/execution-v6.sqlite3`、`<E>/native.log`、`<E>/a6-progress.jsonl`
逐个取 SHA-256 记入本目录的结果文档。

预期总时长：**24 轮 × 3–5 分钟 ≈ 100–150 分钟**（含 2 次大文件读取与 5–7 次 `context_page_in` 翻页），
加事后核对脚本约 20–30 分钟。

---

> **2026-09-08 追加（受控审计面 G5/G6）**：T16 与 T24 两轮纯 UI 图谱操作，**同时**打开记忆面板的
> 「操作记录」tab 并完成一次显式取证：点「查看我的记忆操作记录（仅元数据）」拿授权 →
> 至少翻一页记忆系统记录（`primary.audit.page`）→ 在「本机执行审计」里点「读取终态 Run 审计」
> （`primary.audit.host.page` section=runs）→ 对其中一个 Run 点「查看该 Run 的操作」（section=run_operations）
> → 点「读取记忆调用记录」（section=memory_calls）→ 切回「记忆列表」触发 `primary.audit.close`。
>
> 目的：把 `AUDIT-COVERAGE-2026-09-08.md` 的 G5（受控面在真实运行中未被调用）与 G6（终态 Run 审计页
> 无 UI 读取面）从"按表构造判定"变成实测。核对依据：`operation-audit.db` 的
> `human_audit_grants` / `human_audit_deliveries` / `human_audit_host_deliveries` / `human_audit_host_streams` 有行，
> SDK `sealed_audit_access_events` 等访问事件 > 0；核对器 `--evidence` 的「受控读取面事实」里
> `primary.audit.page(OA1).exercised`、`host.audit_pages.exercised`、`host.memory_call_attempts.exercised` 全为 true。
>
> 负控不变且扩展：审计面读取是本机只读，**不得**新增 `provider_invocations` 行（并入 §4 第 5 条
> "T16/T24 图谱操作不得新增 `provider_invocations` 行"），也不得让 `human_audit_grants.reads`
> （SDK 记忆页预算）因为 Host 分节翻页而增加。审计标识（`memory-attempt:`、`memory-request:`、
> `audit_jobs.job_id`、`finding_id` 等）仍不得出现在 state / human_memory_v7 / execution 三个业务库中。
