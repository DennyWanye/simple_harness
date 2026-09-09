# 决策：`goal.set` 的两步流程在 32000 窗口下必然失败（事件 AL）

- 日期：2026-09-09
- 事件：HM-TO-A6 第 13 次整跑 T17 失败（A6-3「逐字记录 18 KB 目标」）
- 证据（只读）：`.local-test-evidence/2026-09-09/native-a6-run13/primary-ui-hwbyjnym/`
  - `native.log` 06:24:30Z–06:26:35Z
  - `userdata/data/simple-harness-sdk/execution-v6.sqlite3` 表 `provider_invocations`
    （T17 窗口内 4 次调用，`claimed_at` 为 epoch 秒）
- 工作分支：`worktree-event-al`（worktree `worktrees/event-al`），未合入 main

---

## 1. 问题

模型（`deepseek-v4-flash`，窗口 32000，thinking 关）在 T17 一轮里：

| # | 时间(UTC) | 请求字节 | 结果 |
|---|---|---|---|
| 1 | 06:24:55 | 31 720 | succeeded — 调 `task_scope_search` 读（回执 7912 字 candidates 披露）+ 一次 `context_route` 参数非法 |
| 2 | 06:25:30 | 41 417 | succeeded — `context_route` 成功（`continue_active`），回执 `recall_refs: []` |
| 3 | 06:25:46 | 43 788 | succeeded — 发出 `task_scope_update`（`goal.set`，6256 output token / canonical 34 680 B） |
| 4 | 06:26:28 | 52 457 | **failed** `sdk_provider_wire_input_budget_exceeded` |

第 3 步的 `evidence_refs` 填的是第 2 步 `context_route` 回执自己的
`binding_set_receipt_id`（`2cc4a020-cd05-561e-8e15-c87ccae10df2`），被
`task_scope_update_refs_outside_scope` 拒掉；**拒绝的公开消息里才第一次公布**
`allowed_evidence_refs`（8 个）与 `current_turn_evidence_ref`
（`96e7d785-d954-5efe-b38e-6a0567a5285b`）。

第 4 步（按公布的 id 重发）在物理发出之前被终局闸门打死：

```
sdk_provider_wire_input_budget_exceeded
floor=27176 effective=26752 wire=26072 carry=1104
observed_input=19898 output=6256
```

Run 直接 `run.fail`，A6-3 阻塞。

## 2. 根因

**两条，缺一不可。**

### 2.1 本轮证据 id 只在「拒绝」这一条路上才可见（设计出来的两步流程）

`TASK_SCOPE_UPDATE_DESCRIPTION` 当时逐字写着：

> …must cite evidence_refs from the closure instruction's `allowed_evidence_refs`;
> **if you have not been given that list, send your best payload once and the
> rejection publishes the admissible ids in its own `allowed_evidence_refs`**…

模型在思考里原样复述了这套流程。Run 中途本来就没有 closure instruction（事件 U 的
现场），于是「先发一次必被拒的载荷」是宿主指引出来的**正常路径**，不是模型乱猜。

问题在于这条指引对**载荷大小无感**：T17 要记的是用户逐字 18 KB 目标，那次注定被拒
的调用因此值 6256 output token / canonical 34 680 B。同一轮里模型还有另一次巨型入参
的 `context_route`（33 775 B，`invalid_tool_arguments` / `outcome=failed`）。

### 2.2 被拒调用的入参在历史里继续收费

事件 K 的修复让 `_wire_messages` 用 Host 自己的 `ToolCallArgumentsMemo` 把 assistant
的 `tool_calls.arguments` 逐字补回 payload（否则模型会照抄 `{}` 的坏 transcript）。
补回是对的，但它**不区分这次调用有没有被拒**：两份已经作废的入参
（34 680 B + 33 775 B ≈ 9.3 K token）在第 4 次请求里原样上线。

`wire=26072` 里差不多 9.3 K 就是它们。也就是说：撑爆窗口的不是要发的内容，是历史里
两份死掉的入参。7 KB 用户消息 + 8 KB `task_scope_search` 披露只是把余量吃干净的另一半。

**结论：两步流程 × 大目标 × 32000 窗口 = 必然失败。** 修 2.1 让第一步不再发生，修
2.2 让即使发生了也还有余量。

## 3. 方案取舍

### 3.1 让模型第一次就拿到 `current_turn_evidence_ref`

候选落点与结论：

| 落点 | 结论 |
|---|---|
| **`context_route` 被接受的结果**（选中） | 路由进任务 = 这一轮**可能**要写闭合，正是需要这个 id 的时刻；每条 Run 一次，与拒绝披露同源同名；不动任何 durable 契约（`ContextRouteReceipt` / 账本 schema 不变，只是工具结果多两个键） |
| `task_scope_search` 读结果的披露 | 语义不对：search 是「有哪些任务」，不是「这一轮该引哪条证据」；而且 search 未必被调用（T17 调了，但 `continue_active` 的常态是不调） |
| PERSONA / `task_scope_update` 描述里讲清楚 | 讲得再清楚也给不出**id 本身**；而且 PERSONA 与 schema 是 8192 档预算门上余量 0–2 token 的受保护文本，只能减不能加 |
| 把 id 塞进 `ContextRouteReceipt` | 要改 durable 回执契约 + `context_route_decisions` 落库 + 迁移，代价与收益完全不成比例 |

同时**不**在路由结果里公布整张 `allowed_evidence_refs`（最多 16 个 uuid ≈ 320 token
每次路由）：T17 缺的不是「一张表」，是「这一轮该引哪一条」。拒绝那条路仍然公布完整
列表，一个字没删。

id 的取数**复用同一条 Host 查询**（`_turn_evidence_rows_tx`），所以提前给的 id 与
拒绝会公布的那个逐字相同 —— 否则「提前给」等于「提前给错」，有专门单测钉住。

同时把工具描述里那句两步指引删掉，改成指向新的来源，净减 40 字符（不是加）。

### 3.2 预算侧兜底：被拒调用的入参压成短存根

阈值 2 KiB（canonical 字节）。命中条件：**紧随其后的 `tool` 结果信封是
`outcome ∈ {rejected, failed}`**。做法：结构逐字保留（键、层级、短标量全在），只把
超过 200 字的字符串截成前 200 字 + `…(truncated, resend in full)`。

取舍两处：

1. **不注入宿主自己的键**（如 `_rejected_reason_code`）。`task_scope_update` 的 schema
   是 `additionalProperties: false` 的严格 schema，模型照抄自己的 transcript 是事件 K
   实测过的通道：一旦它学会发这个键，之后每次调用都会死在
   `task_scope_update_payload_invalid`。reason code 就在紧下方那条 `tool` 结果里，
   本来就看得见。规格里的「保留 reason_code」因此按「保留 `operations[].reason_code`
   这个**模型自己写的**字段」执行 —— 它没被截断。
2. **落点在 `_wire_messages`**（而不是 `wire_input_budget.py`）：只有这里同时握着
   assistant 与紧随其后的 `tool` 结果，判据（被拒）与被修改的对象（入参）在同一处；
   而且它跑在 `enforce_wire_input_budget` **之前**，量的与发的仍是同一份 payload。
   成功调用与没到阈值的调用一个字节都不动。

## 4. 改动

| 文件 | 改动 |
|---|---|
| `backend/deskpet/sdk_adapters/task_scope_mutation.py` | 抽出 `_turn_evidence_rows_tx`（`_admissible_refs_tx` 与新读者共用的**同一条**查询）；新增 `read_current_turn_evidence_ref(db_path, sdk_run_id, task_scope_id)`（只读、fail-closed：Run 绑定必须唯一，否则空串）；新增共享常量 `CURRENT_TURN_EVIDENCE_REF_KEY`；`TASK_SCOPE_UPDATE_DESCRIPTION` 删掉「先发一次最佳载荷」两步指引，改指向路由公布的 id（1275 → 1235 字符） |
| `backend/deskpet/sdk_adapters/context_route.py` | 新增可注入 `current_turn_evidence_reader`（与既有 `current_turn_text_reader` 同体例，advisory、异常只记日志）；`_commit_receipt` 在**任何 durable 写之前**取本轮 id，被接受且带 scope 的路由结果多两个键 `current_turn_evidence_ref` / `current_turn_evidence_next_step` |
| `backend/main.py` | 把 `read_current_turn_evidence_ref` 接到 `ContextRouteToolService`（未注册新 service，`backend/context.py::_VALID_SERVICES` 无需改） |
| `backend/deskpet/sdk_adapters/tool_call_arguments.py` | 新增 `rejected_tool_result_reason` / `stub_rejected_tool_call_arguments` 与三个阈值常量 |
| `backend/deskpet/sdk_adapters/provider.py` | `_wire_messages` 对被拒的大入参改用存根，新增无载荷计数日志 `product_provider_rejected_tool_call_arguments_stubbed` |
| `backend/tests/sdk_adapters/test_context_route_current_turn_evidence.py` | 新增（5 例） |
| `backend/tests/sdk_adapters/test_rejected_tool_call_arguments_budget.py` | 新增（6 例） |

前端未改动，**不需要重建 bundle**。

## 5. 测试

`backend/.venv/bin/python -m pytest <文件>`（定向，未整目录跑 `tests/sdk_adapters`）。

### 新增

- `tests/sdk_adapters/test_context_route_current_turn_evidence.py` — 5 passed
  - 被接受的 `continue_active` 结果带 id + 用法；`direct_standalone` 什么也不带（读者根本不被调用）
  - 读者不可用 → 退回事件 AL 之前的结果，路由本身不受影响
  - **同源性**：`read_current_turn_evidence_ref` 返回的 id 与
    `task_scope_update_refs_outside_scope` 拒绝里 `current_turn_evidence_ref` 逐字相同，
    也与 `foreground_turns` 那一行相同
  - fail-closed：认不出的 Run / 空 scope / 跨 scope 一律空串
- `tests/sdk_adapters/test_rejected_tool_call_arguments_budget.py` — 6 passed
  - 按真机字符类重建 T17 第二次请求的 13 条消息 + 4 次工具调用 + 上一轮观测
    （`wire=18794 / input=19898` → `carry=1104`）
  - **对照组**（monkeypatch 关掉判据）：`WireInputBudgetExceeded`，
    `wire=26062 floor=27166 carry=1104`（真机 `wire=26072 floor=27176`，误差 10 token）
  - **验收**：同一条序列开启存根后 `wire=17292 floor=18307 ≤ effective=26752`，请求进得去
  - 存根保留 `kind` / `operation_id` / `reason_code` / `base_revision` / 结构，且**不多出**任何宿主键
  - 成功调用与小入参一字节不动；`outcome=failed` 的巨型入参同等对待

### 回归（定向）

| 文件 | 结果 |
|---|---|
| `tests/sdk_adapters/test_token_estimator_calibration.py` + `test_wire_input_budget.py` + `test_provider_tool_call_arguments_replay.py` | 128 passed |
| `test_persona_and_route_schema_still_fit_the_8192_tier_megabyte_turn`（单点） | passed（本轮模型可见文本**净减少**，8192 档余量未被吃） |
| `tests/execution/test_current_tool_megabyte.py -k 8192` | 1 passed |
| `tests/execution/test_page_offset_guidance.py` | 16 passed |
| `tests/sdk_adapters/test_context_route_tool.py` 等 5 个 context_route 文件 | 47 passed |
| `tests/sdk_adapters/test_task_scope_update_refs_disclosure.py` / `test_task_scope_update_clean_scope.py` | passed |

**基线红（与本改动无关，main 上同样红，已逐一对照）**：

- `tests/sdk_adapters/test_task_scope_update_tool.py` 3 例（`task_scope_update_scope_unbound`）
- `tests/sdk_adapters/test_s5b_acceptance_matrix.py` 9 例
- `tests/sdk_adapters/test_effect_gate_hardening.py` / `test_no_recall_gate.py` /
  `test_s5a_acceptance_matrix.py` 合计 13 例

## 6. 残余风险

1. **memo 是进程内的**。冷启动或换进程后 `_wire_messages` 退化成 `{}` 回退，存根也就
   无从谈起 —— 但那种情况下入参本来就不上线，预算问题不存在。
2. **存根改变了模型看到的自己的历史**。截断处写明「resend in full」，但仍有模型把截断
   文本当成「我已经发过的完整内容」的可能。缓解：只截**被拒**的调用（那份内容按定义
   必须重发），成功调用逐字保留。下一次整跑需要看 `goal.set` 落库的是否仍是逐字全文。
3. **id 提前公布 ≠ 模型一定会用**。指引写在结果里，不在受保护的 schema/PERSONA 里，
   模型仍可能自己编 id。拒绝那条路（完整 `allowed_evidence_refs` + 升级提示）一个字
   没动，仍然兜底。
4. **每次路由多一次只读 DB 查询**。放在任何 durable 写之前，异常/取消只丢这条提示，
   不影响路由；但它与 `read_current_turn_text` 一样没有超时，锁竞争下会拖慢路由。
5. **2 KiB 阈值与 200 字前缀是工程取值**，不是拟合出来的。真机上两份被拒入参分别是
   34 680 B / 33 775 B，离阈值一个量级，不存在边界争议；小载荷被拒的常见场景
   （`missing_required_argument`）根本不到阈值，行为与改动前逐字相同。
6. **T17 只被单测覆盖，尚未真机复跑**。数值取自真实证据且对照组误差 10 token，但
   「第 14 次整跑 T17 通过」仍需实机验证。
