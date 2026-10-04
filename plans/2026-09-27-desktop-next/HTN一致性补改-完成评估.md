# HTN 一致性补改：完成评估（2026-10-04）

- 评估人：独立评估子代理。只读代码与文档；除本文件和台账《需求与场景状态》外没有改任何文件；**没有跑测试**（下面两条需补项都是读代码就能确定的事实，不需要跑用例来证明）。
- 依据：分支 `htn-h` 当前提交 `f03f4a31`；代码改动 `git diff 57560720..HEAD -- sdk backend tauri-app`（提交 `bbce635b`、`99788b2f`、`5dfaaade`、`b6c09637`、`f02f6856`）。
- 对照：方案 `HTN一致性补改方案-2026-10-04.md`（以文末"第 2 版"一节为准）、复核原文 `HTN与原始计划一致性复核-2026-10-04.md`、实施记录"一致性补改"一节、主计划第 3.23 版（第一节暂不做表、第二节"一致性补改记的偏离"、修订记录）。
- 用户已定：第 3 处选"交审阅员按原话判"，第 8 处目前只做 macOS，第 9 处默认 512K、256K 可选、不做对比实验，其余按推荐。
- 路径缩写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`，`BE/` = `backend/deskpet/orchestration/`，`FE/` = `tauri-app/src/`，`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`。

## 结论

**需补 2 项**，两项都在 H-2（取消或失败时，把结果不明的对外操作告诉用户）里，改动都很小。其余 8 项（H-1、H-3～H-9）按方案第 2 版做到。实施记录"与方案第 2 版的差异"列的 4 条都可以接受，不违背用户决定，也不违背"判断交给 LLM、Harness 只管秩序、一件事一条路径"。

## 需补项

### 需补 1　有两类任务失败没走"写明结果不明的对外操作"

- **在哪**：`SDK/orchestrator/commit_service.py:1418` `fail_planning`，以及 `:3062` 收尾时判"准则没达到"的失败分支。
- **缺什么**：方案第 2 版写的是"取消与失败都经 `_cascade_stop`，在它的调用方统一写"。实际只有经过 `_cascade_stop` 的三处会调 `_note_unresolved_actions`：取消 `:1470`、任务级失败 `:1503`、步骤失败带停任务 `:3347`。但方案"都经 `_cascade_stop`"这个前提不完整，下面这些失败不经过它，所以没调：
  - `fail_planning` 一路：预算用尽（`SDK/orchestrator/event_handler.py:4038`）、规划次数用尽（`:5971`）、运行时不可用（`:6074`、`:8611`）、库故障（`:3594`）、计划完整性（`:1604`）。
  - 收尾判"准则没达到"的失败：`commit_service.py:3062`。
- **后果**：比如任务正在发布、发布结果还在核对，这时预算用尽或规划失败，任务停下。最终报告里不会列出这个发布，主对话也不会提。后台判断"已结束任务的操作核对出结果"时，只看报告里列过的动作（`event_handler.py:2213`），所以之后核对出结果也不会通知。复核第 2 处说的"用户以为什么都没发生"在这些失败路径上仍然存在。
- **怎么补**：在 `fail_planning` 和 `:3062` 写报告之前，也调一次 `_note_unresolved_actions`。补一条用例，例如"预算用尽停下时，有一个发布结果不明 → 报告里列出它，之后核对出结果有通知"，再配一条改坏。

### 需补 2　"停下后核对出结果"的通知写库时没套任务边界

- **在哪**：`SDK/orchestrator/event_handler.py:2213` `_notice_actions_settled_after_stop`，由 `_reconcile_actions` 在 `:2210` 调用。`run()` 一开头（`:2252`）就会走到这里，主循环中途也会走到。
- **缺什么**：阶段 B 裁决第 9 类和 2026-10-03 收尾裁决第 3 张已经定了：一个任务出库故障，只算这个任务这一轮的错，其他任务照常跑（`_mission_round` `:3473`、`_round_boundary` `:3500`）。阶段 C 第 0′ 条也已经把所有全局扫描都放进了这道边界。新加的这一处逐个读取已停的任务，开事务写事件、写通知请求，却放在任何任务的边界之外。只要其中一个任务读出错或写出错，异常就会直接从 `run()` 抛出去，所有任务这一轮都停下；下一轮走到这里，还会再撞上同一个错。
- **怎么补**：每个任务单独套 `_mission_round(mission_id, "notice_settled", …)`（或者 `_round_boundary`）。用例可以照 `T/product_world/test_round_faults.py::test_a_fault_in_a_global_scan_is_one_missions_round_fault` 的参数化，加一处"停下后核对通知"。

## 逐项核对（做到的只写一句）

| 项 | 方案第 2 版要求 | 核对结果 |
|---|---|---|
| H-1 做法前提被推翻直接交规划器 | 只认真值 FALSE；用 `start_witness_index` 读开工许可；"还没开工"指现行网络里的原子步骤、而且没有任何尝试；去重键 = 步骤 + 条件集摘要 + 纪元；detail 写原因、中文说明、步骤、做法、前提原文、观察依据；`trigger_refs` = 步骤；删 `recheck_method_instance` 及其两个结果类型、两条用例、包导出，改两处注释；保留 `phase_check_points` | 做到。见 `SDK/orchestrator/planning_repair_requests.py:121` `precondition_triggers`，接在 `:525`。用例 `T/product_world/test_precondition_overturned.py` 断言请求早于任何停滞请求。改坏 H-01 已登记。 |
| H-2 取消或失败时写明结果不明的对外操作 | 写进最终报告 `unresolved_actions`；四种结果来源统一判断；经同一条通知路径告知；Host 卡片和主 Agent 上下文都写；SDK、Host 用例和改坏各一 | 大体做到："结果不明"的判定只留一处（`SDK/runtime/operation_reconciliation.py:160`，对账原来的判断也改成调它）；Host `BE/service.py:73`、`:1238` 和 `FE/views/ChatMissionNotices.tsx` 都写了。**缺两处，见需补 1、需补 2。** |
| H-3 要求的轻重交审阅员按原话判 | 只删 `PREFERENCE` 和 `Criterion.phase`，其他分类保留；根审查包删 `requirement_class`；审阅员提示词写判据，不升版本号；用例只核"提示词有这条判据"和"发给审阅员的是原话" | 做到。见 `SDK/contracts/resolution.py:88`、`SDK/assurance/review_input.py:77-80`、`SDK/orchestrator/root_review.py:1214`。`is_required` 也删了：删掉"偏好"之后它恒为真，所以按"是否必须"分叉的代码一起删，结果对。审阅包里的准则仍带类别字段，这条差异见下一节。 |
| H-4 知识只有一组依据 | 不改代码；计划记偏离；台账 R29 改措辞 | 计划第二节已记；台账本次已改。 |
| H-5 删义务的死字段 | 只删 `authority_ref`、`satisfaction_policy`、`SatisfactionPolicy`；`budget_lineage_ref` 保留并记偏离；同一提交改两条用例 | 做到。全库 grep 不再有这两个义务字段的残留。 |
| H-6 诊断导出补"预算去向"一节 | 读快照里的 `budget_by_duty`，不带目标原文；断言和快照一致 | 做到。见 `BE/diagnostics.py:49`、`:369`。用例逐字段比对，并断言没有 `label`。 |
| H-7 任务详情显示"不再算数"的步骤 | 把"还算不算数"抽成一个函数，规划包和快照共用；Host 原样透传；前端加一行；改坏写在 Host 用例上 | 做到。见 `SDK/orchestrator/planner_views.py:38` `accepted_steps`、`SDK/api/facade.py:778`、`BE/projection.py:456`、`FE/views/MissionsView.tsx:262`。 |
| H-8 隔离只做 macOS | 不改代码；台账 R49 改成"不做"；计划"暂不做"加一条 | 计划第一节已加；台账本次已改。 |
| H-9 默认窗口 512K、256K 可选 | 解析规则改成"不是 262144 就用 524288"；默认值 524288；同时改 `test_thinking_pools.py` 的期望 | 做到。见 `BE/settings.py:57`、`:130-131`。已有任务仍用它冻结时的执行池。 |

## 实施记录"与方案第 2 版的差异"能否接受

1. **H-2 判断点放在每轮对账之后的一处，没有在四个来源各自挂钩**：可以接受。这样只有一处读取，晚到的结果和人工裁决会在下一轮对账时读到（人工裁决后 Host 会唤醒主循环）。符合"一件事一条路径"。这一处本身缺任务边界，单独列为需补 2。
2. **H-3 审阅包里的准则仍带 `requirement_class`**：可以接受。这个字段进了保证通道审阅包的哈希，不剥掉是为了不动包身份。提示词写明这个字段是系统默认值、轻重以原话为准，判断仍然由审阅员按原话做。计划第二节已经记了偏离。
3. **H-7 步骤名取步骤目标文字的前 60 字**：可以接受，方案本来没规定。
4. **H-1 前提只在派发前查**：这是方案第 2 版本来的口径，计划第二节已记。

## 顺带说明（不算需补）

- H-1 的请求里列出的是这个做法的**全部**前提，再加上合起来的真值"假"，没有指出具体是哪一条变成了假。原因是一张开工许可本来就把一个做法的全部前提合在一起判。规划器拿到了前提原文和观察依据，可以自己判断是哪一条。
- 已经跑过的测试和改坏结果以实施记录"一致性-3""一致性-4"为准，本次没有复跑。
