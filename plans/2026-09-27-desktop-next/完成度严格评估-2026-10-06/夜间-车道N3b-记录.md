# 夜间车道 N3b 记录（2026-10-07 夜）

工作树 `simple_harness-n3b`，分支 `night-n3b`，基线 main `47bf424a`。三条：N3-02、N3-03、N3-13。
依据：`夜间-N3-建议裁决.md` 第二节施工说明；CLAUDE.md 架构核心思想（事实如实交给规划器，判断归规划器）。

## 一、每条做了什么

### N3-02 规划器看到"目标改了"（完成）

- `orchestrator/planning_repair_requests.py`：生成 `RequirementsUpdated` 请求时，按修订号找同一次修订的
  `RequirementsAmended` 事件（`payload.requirements_revision == number`），把载荷里的 `goal`
  （`{"previous", "current"}` 或 None）照抄进 `detail["goal"]`。只读事件，不另存一份。
- `runtime/role_templates.py`：规划器提示词在 `context.changes` 一句后加一句：`context.goal` 不为空表示
  任务目标也改了，`previous` 旧目标原文，`current` 新目标原文（只改目标时 changes 三个列表都是空的）；为空表示目标没改。
- 用例 `T/product_world/test_requirements_amend_goal.py::test_the_planner_is_told_the_goal_changed`：同一局里
  ①只改目标 → `goal == {旧, 新}`、changes 三列表全空；②目标与条目一起改 → 两样都在；③只改条目 → `goal is None`；
  提示词有 `context.goal`。

### N3-03 规划视图补候选结论层（完成）

- `context/knowledge_tools.py`：列候选结论的循环抽成 `candidate_claims(store, mission_id, *, verified_ids)`。
  目录（`_catalogue`）和规划视图都调它，一条路径。
- `planning/htn/planner_package.py` `knowledge_rows`：加 `candidates` 参数；每行 `layer: "candidate"`、`id`、`status`、
  `marker`、`key`、`content`、`source_task`。排序：已验证 → 摘要 → 候选，16 行上限先裁候选。候选行不带 `ref`，不进 `visible_refs`。
- `orchestrator/planner_views.py`：用 `current_knowledge` 的编号作 `verified_ids`，传入候选。
- `runtime/role_templates.py`：`views.knowledge` 说明加一句"layer=candidate……candidate 层是线索不是事实，不能当已验证依据"。
- 用例 `T/full_target/test_batch2_g_planner_knowledge.py`：新增单元用例（候选排最后、行形状、上限先裁候选、提示词那句话）；
  世界用例扩充（已验证的那条把结论行改回 SUPPORTED 也只以 verified 出现一次；留一条 PROPOSED 结论 → 包里有 candidate 行；
  REJECTED 的不出现；候选不进 visible_refs）。

### 版本号（N3-02、N3-03 一次升）

- 规划器提示词 `planner-hierarchical-v27 → v28`，规划包 `PLANNING_DECISION_PACKAGE_VERSION 14 → 15`。不留旧版本；旧配对不合法。
- 钉子同步：`test_batch2_g_planner_knowledge.py`（v28/15，v27/14 不合法）、`test_planning_decision_enablement_contract.py`（15）。
  `test_planner_package_single_layer.py` 读的是常量，没有字面钉子，不用改。Host 没有钉这两个版本。
- 部署清单 `taskgraph_deployment_manifest.json` 重生成（钉哈希基线随之更新）。已有执行池的准入身份会变，开发期不兼容，属预期。

### N3-13 复合目标跨版本重组联测（用例写完；结论：OBLIGATION_NOT_OPEN 走不到，另发现缺陷，交主会话）

先写产品同形用例（根 → 子目标 → 两个叶子；子目标按第 1 版形成目标结论、收尾一步扣住还在跑时，改写子目标负责的第二条要求）。

**OBLIGATION_NOT_OPEN 在产品路径上走不到**（用例守住）：
- 产品路径上做法的每一步都是 `refines_parent`（`independent_authorized` 要有授权引用，规划本身造不出来）。子目标与根共用
  一份义务 `user-duty-<任务>`。
- 子目标的结论照存，但 `resolution_commits.py` 对共用义务写 `adopted=not shared`——不是采用结论，义务不 SATISFIED，一直开着。
- 根结论采用后，改要求本身按 `AMEND_AFTER_CLOSEOUT` 拒（已有用例 `test_amend_refused_while_closing_out_and_after_the_end`）。
- 所以"复合目标已有采用结论、再提交被 `_require_open_duty` 拒"这条路走不到。与主会话 d8bed5b0 那条（根职责按 §7.2 已了结，正确拒绝）不是一回事；本条没遇到这个码。
- 用例 `test_a_sub_goal_already_resolved_does_not_close_the_duty_an_amendment_reopens`（绿）：子目标第 1 版结论存在且不是采用结论、
  义务 UNSATISFIED、改要求被接受、之后义务仍开着、事件里没有 OBLIGATION_NOT_OPEN。

**另发现的缺陷**（今夜不修，用例 `test_a_sub_goal_already_resolved_is_resolved_again_under_the_amended_requirements`
标 `xfail(strict=True)` 守住；修好后 XPASS 会报红，提醒去掉标记）：子目标已细化之后改要求，规划器三条路都走不通，任务按
"规划次数用完"失败。
1. **为根提新做法再 REPLACE_METHOD**（提示词写的常规做法）：新做法审阅通过，换做法被拒——
   `the increment does not merge into the current network: method instance <子目标采用的做法实例> refines unknown task <旧子目标>`
   （`graph/task_network.py:418`，INTERNAL_CONTRACT_ERROR）。换掉根做法时，旧子目标下面采用的做法实例没有随子目标一起退役。
   这一条与改要求无关：任何"上级换做法、下级复合目标已细化"都会撞上。
2. **保留子目标、只换子目标的做法**（提示词说"想沿用就保留子目标、只换子目标的做法"）：改要求后子目标的完成范围读出
   `OP_EFFECT_SCOPE_STALE requirements have changed`，`assigned_criterion_ids` 吞掉错误返回空，子目标的写做法材料
   `criterion_evidence` 是空的，任何做法都因 `criterion_links must have at least 1 entries` 不可读。
3. **只给写 b.md 的叶子提继任（PROPOSE_SUCCESSOR）**：子目标第 1 版的结论仍是 CURRENT，`plan_commits.py:425` 按
   `resolved dependent requires an explicit successor` 拒。

修法涉及"改要求时哪些结论/做法实例作废、子目标的完成范围按哪一版读"，按裁决单单独裁决，今夜没有碰
`_require_open_duty`、`plan_commits.py`、`task_network.py`。

## 二、改动文件

SDK（`sdk/simple-harness-sdk/src/agent_orchestrator/`）：
- `orchestrator/planning_repair_requests.py`（N3-02）
- `context/knowledge_tools.py`、`planning/htn/planner_package.py`、`orchestrator/planner_views.py`（N3-03）
- `runtime/role_templates.py`（N3-02、N3-03 提示词与两个版本号）
- `orchestrator/taskgraph_deployment_manifest.json`（重生成）

测试（`sdk/simple-harness-sdk/tests/orchestrator/`）：
- `product_world/test_requirements_amend_goal.py`（N3-02 新 1 条）
- `full_target/test_batch2_g_planner_knowledge.py`（N3-03 新 1 条、扩 1 条、版本钉子）
- `full_target/test_planning_decision_enablement_contract.py`（版本钉子）
- `product_world/test_requirements_amend.py`（N3-13 新 2 条，其中 1 条 strict xfail）

没碰：`recovery_coordinator.py`、`event_handler.py`、`api/facade.py`、`commit_service.py`、`obligations.py`、`stop_conditions.py`。

## 三、测试与改坏

测试（只跑改动文件对应的测试文件，最终代码、清单重生成后）：
- `test_requirements_amend.py`、`test_requirements_amend_goal.py`、`test_batch2_g_planner_knowledge.py`、
  `test_planning_decision_enablement_contract.py`、`test_planner_package_single_layer.py`、`test_blackboard_tools.py`：**45 过、1 预期失败（strict xfail）**。
- 读规划器提示词的五份（`test_planner_prompt_single.py`、`test_planner_typed_proposal.py`、`test_planner_format_feedback.py`、
  `test_htn_end_to_end.py`、`test_planner_prompt_fields.py`）：92 过；黑板读者两份（`test_batch2_g_reviewer_blackboard.py`、
  `test_batch2_g_knowledge_recheck.py`）：随上面一并 13 过。

改坏（cp 备份 → 改 → 清 `__pycache__` → 重生成清单 → 跑 → 备份恢复 → 清缓存 → 重生成清单）：

| 编号 | 改坏 | 结果 |
|---|---|---|
| N3-02 | 请求里不带 `goal` | `test_the_planner_is_told_the_goal_changed` 红 |
| N3-03 ① | `knowledge_rows` 不收候选 | 候选单元用例、世界用例两条红 |
| N3-03 ② | `candidate_claims` 不排除已验证编号 | 世界用例（只出现一次）红 |
| N3-13 | 组合审查通过后不提交子目标的目标结论（`composition_review.py` 返回 None） | 守住用例红 |

## 四、留给主会话

1. N3-13 发现的缺陷三处（第一节），要裁决修法；修好后去掉 strict xfail。第 1 处（上级换做法时下级做法实例不退役）不限于改要求。
2. 合并时与主干对齐（主干 d8bed5b0 改了 `publishing_round.py`、`test_h1h_commit_guard.py`、`governance/budgets.py`、
   `contracts/obligations.py`、`commit_service.py` 预算段）；部署清单在合并后要再重生成一次。
3. 文档登记（N3-15）：规划器提示词 v28 / 包 15；`views.knowledge` 三层；T12 结论与缺陷。
