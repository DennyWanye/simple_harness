# 决策备忘：A6-4「后缀单调」把披露撤销读成了裁剪（事件 AB）

> 日期：2026-09-09
> 义务：`HM-TO-A6`（AC = HM-AC-6「动态上下文组装必须在模型预算内完成，裁剪不得破坏因果链」）
> 证据：`.local-test-evidence/2026-09-09/native-a6-run10/primary-ui-j5yjctfj/`
> （第 10 次原生真机跑，Host `43a8f835`，`deepseek-v4-flash`，window 32000 →
> `effective_input_budget = 26752`，thinking 关闭）
> 分支：`worktree-suffix-monotonic`（自 `255f3aad` 起）
> 结论：**(b) 验证器漂移**。装配侧三处丢组实现全部只从头部丢，本轮没有回归；
> 漂移的是 A6-4 的判据本身，它把「组消失」一律当成「裁剪」。

---

## 1. 事故与两对违例（逐条取证，不是推断）

第 10 次 `a6_verify.py` 的 A6-4：

```
requests_parsed=89; max_history_groups_in_one_request=3; requests_over_groups_max=0;
孤立 tool 消息 顶层=0/组内=0; 后缀单调违例=2  → FAIL
```

按 `claimed_at` 把 89 次 `provider_invocations.request_json` 排成一列，逐对复算
`item_a6_4` 的后缀判据，两对违例是（`source_ref` = 该历史组终态观察的 `evidence_id`）：

| # | 上一次请求（Run / 调用） | 下一次请求（Run / 调用） | 上一次组序列 | 下一次组序列 | 消失 | 该次请求回执 `trimmed_groups` |
|---|---|---|---|---|---|---:|
| 1 | `product-sdk-b6d404141d8f…`（`enqueue_sequence`=19）<br>`a105b7929a99…` | `product-sdk-d1353b1ef051…`（seq 20）<br>`72c92effd9d7…` | `[961ed286…, 46f2e6ae…]` | `[961ed286…]` | `46f2e6ae…`（**较新**那一组） | **0** |
| 2 | `product-sdk-0848c5f4dd76…`（seq 21）<br>`469b1f6d65c2…` | `product-sdk-ec9692ee47aa…`（seq 22）<br>`0201a0742355…` | `[961ed286…, 304ae965…]` | `[961ed286…]` | `304ae965…`（**较新**那一组） | **0** |

两对形状完全一样：**较老的那一组留下，较新的那一组消失**，所以「存活的旧组是上一次
序列的连续后缀」不成立。三个组各自是哪一轮（`foreground_turns.turn_json.payload.text`）：

| `source_ref` | 产出它的 Run | `enqueue_sequence` | 该轮用户原话 |
|---|---|---:|---|
| `961ed286-b55d-5f80-a423-4756aaa97a87` | `product-sdk-044c2ba7910b…` | 13 | 参照件 B 里 ANCHOR-BETA 后面那一整行原文是什么？照原文给我。 |
| `46f2e6ae-dd12-5e0e-a9e3-68555f08a98b` | `product-sdk-e6e22faeb87a…` | 15 | 记住：秋分资料整理这套校对流程，就按我前面说的 Python 环境执行。 |
| `304ae965-fbfc-5379-bd84-3741856429cd` | `product-sdk-d1353b1ef051…` | 20 | 不过我印象里上周好像还是按 3.12 在跑的，你说呢？ |

`PrimaryHistoryStore.read` 的窗口是 `ORDER BY t.enqueue_sequence DESC LIMIT 10` 再
`reversed(...)`，所以组序列**按 enqueue_sequence 升序**。seq 20 的窗口是 10–19、
seq 22 的窗口是 12–21，两次都**仍然覆盖**消失的那一组（15 和 20），窗口滑动解释不了。

## 2. 排除 (a)：这两次请求一组都没裁

`run_context_snapshot_receipts.expected_request_fingerprint` 与
`provider_invocations.request_fingerprint` **89/89 全部配上**，逐条读
`source_revisions`：

| Run | `trimmed_groups` | `groups_trimmed_for_budget` | `budget_headroom` | `planned_input_tokens` |
|---|---:|---:|---:|---:|
| seq 19 `b6d404141d8f…` | 0 | 0 | 14 739 | 12 013 |
| seq 20 `d1353b1ef051…` | 0 | 0 | 17 589 | 9 163 |
| seq 21 `0848c5f4dd76…` | 0 | 0 | 14 136 | 12 616 |
| seq 22 `ec9692ee47aa…` | 0 | 0 | 17 655 | 9 097 |

全跑 90 条回执里**只有两条 Run 真的裁过组**：`6ad6a40a2b48…`（seq 6，FAILED，
provider_turn_ordinal 6–21 各裁 1 组）与 `ba0ebb83534f…`（seq 17，FAILED，ordinal 13 裁 2 组）。
两对违例的 Run 一条都不在里面。**预算离满还有 14.7k/17.6k token，根本没有裁剪压力。**

装配侧三处丢组实现也逐行核对过，**全部只从头部丢**，并且都不在本轮改动范围内：

- `backend/deskpet/execution/primary_context.py::prepare` → `while complete and over_cap(): complete.pop(0)`
- `backend/deskpet/sdk_adapters/context_partitions.py::trim_causal_groups` / `assemble` → `kept.pop(candidates[0])`（`candidates` = 非 open 组的下标升序）
- `backend/deskpet/sdk_adapters/context_authority.py` 降级第二步 → `groups.pop(remaining[0])`

任务书列出的四个候选假设逐一排除：
- **事件 Y 的 reasoning 回传由老到新丢** —— 丢的是 `reasoning_content` 字段，不改
  `messages` 里的历史组，且 `request_json` 是 canonical 形态本来就不含该字段；
- **F-E3「descriptor 不更小就逐字保留」改了组身份** —— 组身份键是
  `historical_causal_group.source_ref`（= `evidence_id`），
  `project_history_group` 只改**组内** tool 正文，`source_ref`/`source_hash`
  在两对里逐字不变（`961ed286…` 的 `source_hash` 全程恒为 `2bf4683a…`），没有「新组」出现（`added=[]`）；
- **F-E2 省略通知替换闭合组内消息** —— 同上，替换发生在组内，不改 `source_ref`；
- **事件 V 的争议探针多插一对 `context_route` 消息** —— 探针不产生
  `historical_causal_group` 块，`history_groups()` 只按该 kind 取块。

## 3. 真因：披露撤销，不是裁剪

一个历史组进不了下一次请求，有两条**互不相干**的通路：

1. **裁剪** —— 上限/预算压力下丢整组，只从头部丢，就是 A6-4 要守的那条；
2. **披露撤销** —— `PrimaryHistoryStore.read` 末尾的
   `self._policy.check_evidence_ids(...)` 判该组不可见，`primary_context.prepare`
   **根本没收到**这一组，谈不上「裁」。可见性是对该组终态证据
   `visibility_dependencies` 的**传递**判定：任何一条依赖不可见，整组撤下。

三个组的 `human_memory_evidence.payload_json.visibility_dependencies` 逐条摊开：

| 组 | `evidence` 依赖 | `recall` 依赖 | `procedure_drafts` | 全程是否掉过 |
|---|---:|---:|---:|---|
| `961ed286…`（seq 13） | 2 | **0** | 0 | **从未** |
| `46f2e6ae…`（seq 15） | 4 | **3** | 0 | 第 1 对掉 |
| `304ae965…`（seq 20） | 11 | **3** | **1** | 第 2 对掉 |

时序对得上：seq 19 正是「**更正一下：校对脚本我现在统一用 Python 3.13，不是 3.12**」
（A6-7 supersede），它跑完之后 seq 20 的请求里 `46f2e6ae…` 立刻消失；seq 21 之后
（A6-8 争议 / T23 遗忘那一段）seq 22 的请求里 `304ae965…` 消失。**始终留下的
`961ed286…` 是三组里唯一 `recall` 为空的那一组**——它没有这条撤销通路，所以从头到尾没掉过。
这正是 A6-7 / A6-8 / A6-10 要求的行为：纠正、争议、遗忘必须让派生的历史退出上下文，
而**被撤销的那一组在序列里的位置是任意的**，它没有义务恰好是最老的。

旧判据把两条通路混成一条：`survivors = [r for r in prev if r in set(cur)]` 之后直接要求
后缀相等，于是把合法撤销读成「裁剪破坏了因果链」。第 5–9 次之所以 PASS，不是因为
装配器那时更正确，而是那几次的旅程没有让「带 recall 依赖的历史组」在「更老的无 recall 组
仍在窗口内」的时候被撤销（第 8/9 次实测 `disclosure_withdrawn_groups=0`）。

## 4. 修复：只在能排除撤销时才记违例

改动只在 `scripts/native/a6_verify.py`，装配侧一行未动（事件 Z 正在改
`context_authority.py` 的工具目录与收尾注入，本轮不碰）。

新增两个 `Evidence` 取数口：

- `receipt_group_trims_by_fingerprint()` → `(sdk_run_id, expected_request_fingerprint)`
  映射到该次请求真正丢掉的整组数，取 `trimmed_groups` 与 `groups_trimmed_for_budget`
  的较大者（fail closed 方向）。回执表缺失返回 `None`；表在但该指纹没有回执，
  `dict.get` 返回 `None` —— **「不知道」与「知道是 0」必须分开**。
- `revocable_history_sources()` → `source_ref` 映射到「它有没有可撤销的披露依赖」
  （`recall` / `short_horizon` / `procedure_drafts` 任一非空）。

`item_a6_4` 的后缀段改为：先算出「排在某个幸存组之后却消失了」的那些组
（`offenders`），只有当**两个条件同时成立**时才把它算作合法撤销 ——
① 该次请求的回执明确说 `trimmed_groups == 0`（裁过就必须守后缀，哪怕消失的组带 recall 依赖），
② 消失的那一组确实有可撤销的披露依赖。任一不成立即记违例。
`numbers` 新增 `budget_trim_transitions`、`disclosure_withdrawn_groups`、
`disclosure_withdrawal_sample`、`suffix_violation_sample`（后两者各留 4 条，含
`prev/cur` Run、两侧组序列、乱序消失的组、该次请求的 `trimmed_groups`），
让「凭什么放行」写在报告里，而不是藏在代码里。

## 5. 控制

`backend/tests/native/test_a6_verify_a6_4.py` 五例，全部在基线 `255f3aad` 上**全红**、本分支**全绿**：

| 用例 | 形状 | 期望 |
|---|---|---|
| `..._passes_when_a_newer_group_is_withdrawn_by_disclosure` | `[旧, 新] → [旧]`，回执 `trimmed_groups=0`，新组带 recall | PASS，`disclosure_withdrawn_groups=1`（基线上是 `后缀单调违例=1` → FAIL） |
| `..._still_fails_when_the_request_really_trimmed_out_of_order` | 同形状，但回执说这次裁过 1 组 | **FAIL** |
| `..._still_fails_when_the_vanished_group_has_no_revocation_path` | 同形状，消失的组 recall 全空 | **FAIL** |
| `..._fails_when_receipts_cannot_attribute_the_drop` | 同形状，回执表无对应行 | **FAIL**（无从归因不放行） |
| `..._head_drop_under_real_budget_pressure_still_passes` | `[旧, 新] → [新]`，回执裁了 1 组 | PASS，`budget_trim_transitions=1` |

七份真机证据用新判据复算，无一回归（`viol` 全为 0）：

| 证据 | 判定 | `requests_parsed` | 整组消失 | 其中真裁过 | 披露撤销 | 违例 |
|---|---|---:|---:|---:|---:|---:|
| run6 | PASS | 18 | 1 | 1 | 0 | 0 |
| run7 | PASS | 31 | 2 | 1 | 0 | 0 |
| run8 | PASS | 87 | 7 | 3 | 0 | 0 |
| run9 | PASS | 134 | 20 | 16 | 0 | 0 |
| **run10** | **PASS**（原 FAIL） | 89 | 8 | 1 | **2** | **0** |
| run10a | PASS | 59 | 1 | 0 | 0 | 0 |
| run10b | PASS | 27 | 2 | 0 | 2 | 0 |

其它命令：`a6_verify.py --selftest` 18 项全部可执行；对 run10 全量重跑，18 项里
**只有 A6-4 由 FAIL 变 PASS**（A6-12 的 INCONCLUSIVE 是因为本次没传 `--installed-target`，与本改动无关）。
`backend/tests/native/` + `tests/execution/test_control_result_bound.py` +
`test_descriptor_cost_bound.py` 单进程合计 **81 passed**。

## 6. Followup

- **F-AB-1**：`item_a6_4` 仍按全局 `claimed_at` 把所有 Run 的请求拉平成一条序列比较，
  跨 Run 的相邻对同时混着「历史窗口右移」「披露变化」「裁剪」三种差异。真正贴合
  `prepare` 契约的判据是**同一次请求内**「保留的组是可见组列表的后缀」，那需要把
  可见组列表也落进回执（今天只落了 `history_sources` 这个**结果**）。建议在
  `run_context_snapshot_receipts.source_revisions` 里加一个
  `history_sources_admitted`（本次 `PrimaryHistoryStore.read` 交出的组数与 ref），
  之后 A6-4 就能不依赖启发式地把两条通路分开。
- **F-AB-2**：本轮用「有没有 recall/short_horizon/procedure_draft 依赖」作为
  「有没有撤销通路」的代理。纯 `evidence` 依赖也可能被遗忘撤销（遗忘一条用户轮），
  届时这个代理会偏严（记成违例）。方向是 fail closed，可接受，但 F-AB-1 落地后应当撤掉。
