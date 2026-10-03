# HTN 补齐 · 阶段 B 完成评估

日期：2026-10-03　　依据代码：主分支 `main`（`28ee2453`，含阶段 B 收尾）
评估人：独立评估子代理。只读代码，**没有跑任何测试**，除本文与台账 `需求与场景状态.md` 外没有改任何文件。

对照依据（均以最新版为准）：
- 计划：`HTN补齐计划-2026-10-02.md` 第 3.6 版（阶段 B 第 1～8 条、表二相关行、第六节流程）
- 卡住缺陷裁决：`HTN补齐-阶段B卡住缺陷-裁决.md`（九类）
- 收尾裁决：`HTN补齐-阶段B收尾-偏差裁决.md`（7 张）
- 补全方案第 1 批：`TaskGraph-补全-方案.md` 第 2 节
- 实施记录：`HTN补齐-实施记录.md` 阶段 B 各节

路径缩写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`BE/` = `backend/deskpet/orchestration/`；`FE/` = `tauri-app/src/`。

---

## 〇、结论

**阶段 B 的产品代码已按计划写完，但阶段 B 还不能判"按计划完成"。** 差在三类事上，都不是产品代码：

1. **未裁决偏差 2 条**（实施记录没登记、也没裁决，都是测试缺口）：
   - 做法审阅调用一直不回来，计划要的用例没写；而且有一条旧用例的断言和新行为正好相反，读码判断它很可能已经变红。
   - "条件不满足时点放弃被拒"计划要的测试没写，而界面上用户点得到这条拒绝。
2. **阶段收尾流程 3 项没做**：阶段 B 收尾这批代码还没做 Opus 只读核验，还没发版钉版，`ARCHITECTURE/` 也还没更新。
3. **计划文字 4 处要改**：3 处是本评估对 3 张偏差单的裁决落点，1 处是收尾裁决要求替换、实际没替换干净的残留。

这些做完，阶段 B 即按计划完成。

---

## 一、逐条对照表

结论只用四种：**按计划完成** / **有已裁决偏差** / **有未裁决偏差** / **没做**。

### 1.1 计划阶段 B 第 1～8 条

| 计划条目 | 计划要求 | 代码里的事实 | 结论 |
|---|---|---|---|
| 任务结束通知进主对话（第 1 条，表二 22） | 保证通道的通知交主 Agent；主对话出卡片；用户点了才写"已收到"；只动 Host 与前端 | `BE/notices.py`（116 行，原子替换写入）；`BE/service.py:183-200` 记录、`:332`/`:811` 启动时从 SDK 持久回执补回、`:1179-1209` 待确认与确认；主 Agent 上下文带通知：`backend/deskpet/execution/primary_context.py:84-113`；前端卡片 `FE/views/ChatMissionNotices.tsx`、接入 `PrimaryChatView.tsx`。用例：`backend/tests/orchestration/test_mission_notices.py::test_a_finished_mission_reaches_the_main_conversation_until_the_person_acknowledges_it`、`ChatMissionNotices.test.tsx` | 按计划完成 |
| 卡住能救、看得清（第 2 条 = 补全方案第 1 批） | 见 1.3 节逐条 | 见 1.3 节 | 有未裁决偏差（1 条测试缺口，见 1.3 节"验证"行） |
| 指标进诊断导出（第 3 条前半，表二 15a） | 指标接进诊断导出 | `BE/diagnostics.py:21` 引入、`:408` `"metrics": metrics(store, mission_id, unpriced=True)` | 按计划完成 |
| 停滞反复问规划器有上限（第 3 条后半，表二 20） | 不比计划内容；自上次有新尝试以来停滞问规划器满 3 次就不再问，以"没有可派发的工作"停，详情写次数 | `SDK/orchestrator/planning_repair_requests.py:174` `stall_asks_since_new_work`；`SDK/orchestrator/event_handler.py:2733-2762` 到上限不问、详情带 `stall_asks_without_new_work`、`stall_asks_cap`；上限复用 `max_planning_attempts`。用例：`T/full_target/test_stall_asks_planner_first.py::test_stall_asks_are_counted_since_the_last_new_attempt`、`::test_a_stall_asked_about_too_often_without_new_work_stops_without_asking_again` | 有已裁决偏差（用例口径，见第二节偏差单 1，本评估裁"接受"） |
| 新事实进覆盖清单、新拒绝码进错误码表（第 4 条） | 当批接上 | 覆盖清单本阶段只因删回填少了一个写入口（`SDK/observability/business_replay_inventory.json` 去掉 `update_artifact_storage`）；新写入只有运行表 `taskgraph_followups`；新停止原因 `store_fault`（`SDK/contracts/state_machines.py:58`），编解码清单随之重写一次（提交 `29c9cf35`）；错误码表加"暂时性"一列（`SDK/contracts/error_table.py:118-125`）。新增的 `action_outcome_unproven`、`SERVICE_TURN_IDENTITY_MISMATCH` 是内部守卫码，不在错误码表范围（与实施记录 B-8 说法一致） | 按计划完成 |
| 真机（第 5 条） | 故障注入造"已停、结构没变但收敛没推进"，真实鼠标点按钮 | 第 3.5 版流程起真机统一放到全部代码写完之后；已做过的真机点击（放弃、重新发送、拒绝发布、没生效）按第六节保留为证据，证据目录 `.local-test-evidence/2026-10-03/stuck-panel/`、`publish-clicks/`，注入手段写在实施记录 B-8 | 按计划完成（按第 3.5 版口径） |
| 卡住类缺陷九类（第 6 条） | 按卡住裁决逐类修 | 见 1.2 节 | 有未裁决偏差（第 6 类测试，见 1.2 节） |
| 两件删除（第 7 条） | 删启动时旧产物回填；删过时交接快照、校验脚本、已接入的增量目录 | 提交 `3a8c4697`：`backfill`、`list_all_artifacts`、`update_artifact_storage` 及只测它的用例已删（`git grep` 无残留）；`sdk/simple-harness-sdk/HANDOFF-SOURCE-MANIFEST.json`、`scripts/verify_development_handoff.py`、`development/taskgraph-host-overlay/` 均已不存在 | 按计划完成 |
| 保护补全（第 8 条） | 恢复、对账、启动绑定进同一边界；已结束任务收尾出错到上限就关调用、额度能按上限结清；根终审切包用完如实写 | 恢复：`SDK/orchestrator/event_handler.py:2058-2132`（按意图 `recover_intent:`、按任务 `recover`，失败记入 `_unrecovered`，`:3318` 每轮先重试恢复、其余跳过）；对账：`:2134-2163` 逐个动作进边界，`ActionExecutor.reconcile` 已删（`SDK/runtime/actions.py` 只剩 `reconcile_one`）；启动绑定：`:2016` 逐条接住记 `startup_bind:`；身份不符入"数据损坏"表：`SDK/orchestrator/failure_classes.py:162`；已结束任务到上限关调用：`event_handler.py:3368-3378`（原因 `round_fault_after_mission_end`），时间下限 `failure_classes.py:157`；切包用完：`event_handler.py:9128-9130`（`root_review_cut_budget_spent` + `stale_reasons`）。用例：`T/product_world/test_round_faults.py` 后三条、`T/product_world/test_review_call_unanswered.py::test_a_final_review_that_never_answers_stops_by_name` | 有已裁决偏差（用例口径，见第二节偏差单 2，本评估裁"接受"） |

### 1.2 卡住裁决九类：改动落点与测试

| 计划条目 | 计划要求 | 代码里的事实 | 结论 |
|---|---|---|---|
| 服务明确拒绝发布后无人收到——改动落点 | 加"连接器台账证明未落地"证明种类；新写文件发布对账适配器并登记进档案；对账范围扩到"失败且交接过、没有证明"；系统按原内容重交，最多 2 次，超了以"动作失败"停 | `SDK/contracts/operation_payloads.py` `CONNECTOR_LEDGER_NOT_LINKED`；`SDK/runtime/operation_reconciliation_file_publish.py`（读台账取同一把锁，文件头第 8 行）；档案登记 `SDK/runtime/operation_profiles.py:114-116`、`:212`；对账范围 `SDK/runtime/actions.py:217-219`；重交与上限 `SDK/orchestrator/system_operations.py:83`、`:356-363`（`publish_not_applied`） | 按计划完成 |
| 同上——测试 | 拒一次后出第二张卡；拒三次后按名停；改坏检验两条 | `T/product_world/test_operation.py::test_a_publish_the_service_refuses_is_proven_unapplied_and_offered_again`（参数 1、3 次）；另有核验回归 `::test_a_link_that_succeeded_before_an_error_is_never_proven_unapplied`、`::test_an_unproven_failure_with_no_publisher_bound_lets_the_loop_go_idle`；改坏检验记于实施记录 B-7 | 按计划完成 |
| 人拒绝发布后一直进行中——改动落点 | 新修复请求来源"对外操作未生效"；人拒绝交规划器，与找不到发布来源共用同一骨架；每次拒绝问一次，累计 2 次或规划器没换内容就以"批准被拒"停；"被拒效果不算合法等待"一项按收尾裁决不改 | `SDK/planning/htn/repair_decision.py:65`、`repair_adapter.py:50-51`；`SDK/orchestrator/system_operations.py:183`（共用骨架 `_ask_planner_once`）、`:246-267`（上限 `SOURCE_REPAIR_CAP`、详情 `operation_rejected`）；规划包升到 `planner-hierarchical-v19`（`SDK/runtime/role_templates.py:113`、`:184`） | 有已裁决偏差（"被拒效果不算等待"按收尾裁决第 5 张不改，计划已改） |
| 同上——测试 | 拒绝理由到规划器、改内容后完成；连拒两次停；改坏检验 | `T/product_world/test_operation_rejected.py` 五条：`test_a_rejected_publish_goes_to_the_planner_with_the_reason`、`test_a_publish_rejected_twice_stops_as_approval_rejected`、`test_a_planner_answer_that_leaves_the_content_unchanged_stops`、`test_a_planner_that_answers_no_change_stops_the_mission`、`test_a_rejected_publish_rewritten_by_a_successor_step_completes` | 按计划完成 |
| 人裁定失败没人收到、没到服务端不重交——改动落点 | 适配器答"没开始"走现有重新交接；"人工裁定未生效"证明；对外入口、Host 命令、前端两个按钮 | `HUMAN_RULED_NOT_APPLIED`（`SDK/contracts/operation_payloads.py`）；`SDK/api/facade.py:642` `resolve_unknown`；`BE/handlers.py:50`、`:223`、`:278` `mission_action_resolve`；`BE/service.py:1165`；前端 `FE/views/MissionsView.tsx` "已生效 / 没生效"。"新发送次数 0"读码确认没有代码读它拦交接，不改（实施记录 B-8 补记） | 按计划完成 |
| 同上——测试 | 没到服务端原地重交一次、只发布一份；人裁定没生效后出新卡 | `T/full_target/operation_completion/test_publish_variants.py::test_a_publish_lost_before_the_service_is_handed_off_again`、`::test_a_person_rules_on_a_publish_nobody_can_settle`（按裁定结果参数化） | 按计划完成 |
| 确认页收下到不了的完成标准——改动落点 | 抽成一个函数，确认页和物化都调；物化时被拒写事件并以"动作失败"停 | `SDK/runtime/operation_profiles.py:215` `require_effect_supported`（注释写明唯一条件）；停任务详情 `operation_capability_unsupported`：`SDK/orchestrator/system_operations.py:493` | 按计划完成 |
| 同上——测试 | 确认时提交到不了的标准被拒、库里不写 | `T/product_world/test_operation.py::test_the_confirmation_page_refuses_a_milestone_the_profile_cannot_reach` | 按计划完成 |
| 计划提交被拒后围栏不解除——改动落点 | 被拒时同事务解围栏；新围栏先替代旧的；错误码表加"暂时性"列，两处交接被拒读这一列 | 替代旧围栏 `SDK/storage/taskgraph_convergence.py:136`（`superseded_by_new_decision`）；作业结束时被挡通知放回待发 `:339` 起；崩溃窗口由收敛跟进补解（实施记录 B-5）；`SDK/contracts/error_table.py:118-125`；读这一列 `SDK/orchestrator/operation_runtime.py:201-202`、`event_handler.py:10221-10222` | 按计划完成 |
| 同上——测试 | 被拒后解围栏；围栏中的发布等待而不失败 | `T/product_world/test_repair_replace_method.py::test_a_refused_method_change_lifts_its_fence`（含"中间崩溃"参数）；`T/product_world/test_operation.py::test_a_fenced_publish_waits_instead_of_failing` | 按计划完成 |
| 审阅调用不回来——改动落点 | 做法审阅、根终审计时，到期按"被打断"收口，交现有第二次调用/重切路径；进展只认落库；重启后旧意图收口；上限按收尾裁决维持两层 | 计时 `SDK/orchestrator/event_handler.py` `_review_call_overdue`（运行中按单轮时限、阻塞按服务时限、不在本进程立即到期、从提交时刻算）；收口 `_end_overdue_review_call`（`:5524`），收集处 `:5402-5406`；`SDK/orchestrator/assurance_review_collect.py` `abandon_assurance_review`，码 `REVIEW_CALL_ABANDONED`（`failure_classes.py:24`）；做法审阅如实写"调用没拿到回复" `SDK/orchestrator/method_plan_reviews.py:169-180`；过时注释已改（`event_handler.py:8199-8222`） | 有已裁决偏差（上限维持两层，按收尾裁决第 2 张，计划已改） |
| 同上——测试 | 做法审阅不回来先重开、再报"没有结论"（含上限版）；重启后不让主循环一直忙；改坏检验 | 根终审四条在：`T/product_world/test_review_call_unanswered.py::test_a_root_review_that_never_answers_is_reopened`、`::test_a_review_that_never_answers_twice_is_recut_not_left_hanging`、`::test_a_root_review_interrupted_by_a_restart_does_not_keep_the_loop_busy`、`::test_a_slow_review_inside_the_turn_deadline_is_not_abandoned`。**做法审阅那两条没写**，实施记录 B-5 只列根终审用例，没登记偏差。另见第四节第 1 条：旧用例 `T/full_target/test_service_intent_provider_blocker.py::test_a_method_review_on_an_unknown_outcome_keeps_its_original_call` 断言与新行为相反 | **有未裁决偏差** |
| 计划损坏时停不下——改动落点 | 级联对缺绑定的步骤不写终止记录，与终止闸门共用绑定查询 | `SDK/orchestrator/commit_service.py:555` `_terminal_binding`（共用）、`:1386` `_cascade_stop` 内跳过缺绑定步骤 | 按计划完成 |
| 同上——测试 | 去掉预期失败标记；新增两任务隔离用例 | `T/full_target/test_hierarchical_event_flow.py::test_a_damaged_plan_stops_one_mission_and_is_read_as_corruption`（已无 xfail）；`T/product_world/test_round_faults.py::test_a_damaged_plan_history_stops_only_its_own_mission` | 按计划完成 |
| 取消后预留停在已预留——改动落点 | 三处改成先关意图再结账；刚结束的任务每轮重核；结束 15 分钟后按上限结清 | `SDK/orchestrator/accounting_recovery.py:306-314`（刚结束的任务每轮核）、`:231-261` `_settle_expired_ended_holds`（原因 `mission_ended_usage_unknown`） | 按计划完成 |
| 同上——测试 | p35 用例补断言；新增"取消后一次跑完内结清" | `T/p35/test_provider_accounting_loop.py::test_a_call_queued_for_the_only_slot_is_unbilled_and_a_cancel_never_hands_it_off`（产品同形世界，补了"几轮内结清"断言，等价于裁决要的新用例，二合一）；`::test_a_hold_left_by_an_ended_mission_is_counted_at_its_bound_after_three_full_passes` | 按计划完成 |
| 写失败/读侧拒绝冲出主循环——改动落点 | 一个任务一轮的边界；分类表两类；8 处散落的计划损坏停止收进边界；新停止原因"库读写故障" | `SDK/orchestrator/event_handler.py:3299` `_mission_round`、`:3331` `_round_fault`（写 `MissionRoundFault`，`:3350`）；分类表 `failure_classes.py:154-162`；`state_machines.py:58` | 按计划完成 |
| 同上——测试 | 一个任务写失败不影响另一个；持续失败按名停；损坏只停自己；原子性用例改断言 | `T/product_world/test_round_faults.py` 前四条；`T/full_target/taskgraph_exec/test_commit_atomicity.py:58` 改为等 `MissionRoundFault` | 按计划完成 |

### 1.3 补全方案第 1 批（第 2 节）

| 计划条目 | 计划要求 | 代码里的事实 | 结论 |
|---|---|---|---|
| 卡住原因 → 出口对照 | 面板显示原因和出口；放弃条件不满足就拒绝并显示原因 | `FE/views/PlanChangePanel.tsx:6-16`（四种原因：结果不明的出口在"等待原因"里的"已生效/没生效"，不重复；旧尝试还在停 → 取消；已停结构没变 → 放弃；通知被挡 → 重发）；拒绝原因由 `BE/taskgraph.py:136-139` 转成人话显示 | 按计划完成 |
| 放弃后告诉规划器 | 用户放弃的事实与理由进下一次规划请求 | `SDK/orchestrator/planner_views.py:203`、`:249` `abandoned_plan_changes_for_planner`；提示词说明 `SDK/runtime/role_templates.py:191`；用例 `T/product_world/test_stuck_plan_change_exits.py`（放弃参数断言规划器看到）、包结构 `T/full_target/test_planner_package_single_layer.py:52` | 按计划完成 |
| "被挡通知"读接口 | 只读接口列消息号、行版本、类型、错误码、失败次数 | `SDK/api/taskgraph.py:465-492`（`blocked_notifications`）；`why_not_ready` 在 `:425` | 按计划完成 |
| 两个操作动词 | 控制通道"放弃这次改计划""重新发送"，带期望版本号；只能人点，Host 以本机用户身份执行，每次点击自己的命令号 | `BE/handlers.py:40-41`、`:255-256`；`BE/taskgraph.py:100-145`（字段白名单、`ui-click-…` 命令号、主 Agent 工具 `BE/chat_tool.py` 里没有这两个动词） | 按计划完成 |
| 界面 | 只加"为什么还不开工"和"改计划进度"面板；读失败保留旧画面标"已过期" | `FE/views/liveGraph/LiveGraph.tsx`（为什么还不开工）；`PlanChangePanel.tsx:58`、`:119`（已过期）；前端用例 `PlanChangePanel.test.tsx` 四条、`LiveGraph.test.tsx` | 按计划完成 |
| 诊断导出 | 临时副本上重建执行图历史，只放报告、历史版本清单、相邻差异，不放库 | `BE/diagnostics.py:443-464` `taskgraph_history` 调 `replay_taskgraph`；导出用例见实施记录 B-8 | 按计划完成 |
| 验证（按收尾裁决改后的口径） | 产品同形用例两个出口各走一次，各验一次"旧版本号再点被拒、库里不多写"；Host 处理器测试；前端组件测试；**SDK 测试：放弃条件不满足时拒绝；放弃后的事实进入规划请求**；真机一局 | 产品同形用例与旧版本号断言：`T/product_world/test_stuck_plan_change_exits.py:111-122`；Host：`backend/tests/orchestration/test_taskgraph_operator_verbs.py` 四条；前端在；放弃事实进请求：在。**"放弃条件不满足时拒绝"没有任何用例**：`SDK/orchestrator/taskgraph_convergence_authority.py:95-115` 的 `TASKGRAPH_CONVERGENCE_NOT_QUIESCENT`、`TASKGRAPH_ABANDONMENT_OLD_DEMAND_CHANGED` 在测试里零引用；而面板对"旧尝试还在停"的作业也显示放弃按钮（`PlanChangePanel.tsx:120-131` 对每个作业都画按钮），用户点得到 | **有未裁决偏差** |

---

## 二、实施记录 B-9 末尾 3 张偏差单的裁决

裁决口径：判断交给 LLM、Harness 只管秩序；同一件事只留一条路径；开发期不做兼容；不做不必要的测试。另加一条本评估沿用的分界：**测试可以按流程攒到阶段 F；已知的代码缺口不能推给测试阶段**（第 3.5 版流程要求"先把代码全部写完，再一起测"）。

### 偏差单 1：停滞计数用两条小用例代替整条循环用例 —— 接受

- **理由**：
  - 计数函数是纯秩序（只数事件条数），由 `test_stall_asks_are_counted_since_the_last_new_attempt` 直接证明"有新尝试就清零"；接线由第二条证明"到 3 次不再问、按名停、详情带次数"。两条合起来覆盖了裁决要的全部行为。
  - 去掉上限的改坏检验在第二条上仍会变红。
  - 在产品同形世界里造"规划器反复提交不派新尝试的改动"，要专门构造可反复提交的改接输入，成本高、只多证明一件已证过的事，属于不必要的测试。
  - 这不是收尾裁决里的"退路"情形（循环不可达），所以计数照加、不删，计划表二第 20 条文字不用改。
- **计划文字怎么改**：收尾裁决第 1 张"代码：落点、上限、用例"里"用例（1 条）"一段替换为：
  > - **用例（2 条，2026-10-03 阶段评估改）**：`T/full_target/test_stall_asks_planner_first.py::test_stall_asks_are_counted_since_the_last_new_attempt`（计数只算最后一次新尝试之后的停滞请求）；`::test_a_stall_asked_about_too_often_without_new_work_stops_without_asking_again`（计数到 3 不再问，按"没有可派发的工作"停，详情带次数与上限）。改坏检验：去掉上限 → 第二条变红。整条循环在产品同形世界里造价高，不另写。

### 偏差单 2：恢复、对账、启动绑定的用例换成函数级注入，对账没有单独用例 —— 接受

- **理由**：
  - 函数级注入一个真实会发生的库错误（`sqlite3.OperationalError`）、一个真实会发生的身份不符，属于"模拟外界真会发生的事"，没有关掉或绕过产品闸门，合规；比两段式重开加触发器便宜，证明力相同。
  - 对账与恢复用的是**同一个**边界函数 `_mission_round`，边界本身已有改坏检验（边界改回原样抛出 → 变红）；对账循环只多一层调用。删掉 `ActionExecutor.reconcile` 后唯一一处循环能用，由发布相关用例回归证明。为对账这一处单独构造"已交接、结果不明的发布动作 + 注入错误"，成本与收益不相称。
  - 按第 3.5 版流程，"对账处出错"作为一个故障切点进阶段 F 的崩溃切点清单统一核，测试不丢。
  - 小瑕疵（不阻断，可顺手补一句断言）：`test_an_ended_missions_collection_that_keeps_failing_is_closed_and_settled` 只断言预留不再是"已预留"，没断言裁决要的"按上限计入"事件。
- **计划文字怎么改**：收尾裁决第 3 张"用例（2 条…）"整段替换为：
  > - **用例（3 条，都在 `T/product_world/test_round_faults.py`，2026-10-03 阶段评估改）**：`test_a_fault_while_recovering_one_mission_does_not_stop_the_others`（恢复处注入库错误：`run()` 不抛、B 完成、A 记地点 `recover`、恢复前跳过其余工作、排除后完成）；`test_a_startup_binding_that_refuses_one_intent_stops_only_its_mission`（启动绑定注入身份不符：服务照常起来，只停这一个任务，带码）；`test_an_ended_missions_collection_that_keeps_failing_is_closed_and_settled`（收尾一直出错到上限关调用、`run()` 回到空闲、预留按上限结清）。对账处与恢复共用同一个边界函数，不单独写用例，列入阶段 F 崩溃切点清单。注入用函数级替身模拟真实库错误与身份不符，不用两段式重开加触发器。

### 偏差单 3：保证通道每轮工作、迟到用量导入写库出错会冲出主循环，记入 F —— 不接受"记入 F"，改为阶段 C 的代码项

- **事实**（读码）：主循环 `_cycle_inner`（`SDK/orchestrator/event_handler.py:3462-3480`）在任务边界之外调用一串全局扫描：`import_late_accounting`（逐条 `_import_hold`，`SDK/orchestrator/accounting_recovery.py:218-219`）、`_assurance_tick.tick()`（`SDK/orchestrator/assurance_tick.py:156` 起按任务逐个处理，单个任务库错误或 `ASSURANCE_CREATION_LANE_MISMATCH` 会冲出整轮）、`wake_blocks`、执行图通知 `tick()`、`_wake_planning_waits`。它们和第 9 类是**同一个形状**：一个任务的坏数据让 `run()` 冲出，Host 连续失败后标"降级"，所有人都建不了新任务。
- **理由**：
  - 这是秩序问题，第 9 类的总原则已定："主循环里的意外异常，全部在'一个任务一轮'的边界上接住，不再在各处零散地 try"。修法不需要新判断，只是把这几处按任务、按条套进同一个 `_mission_round`。
  - 阶段 F 是测试阶段。把一个已知的代码缺口推给测试阶段，等于"先测出来再写代码"，违反第 3.5 版"先写完代码再一起测"。收尾裁决第 3 张把 `_import_hold` 记入 F 也是同一个问题，一并纠正。
  - 不放进阶段 B：阶段 B 第 8 条的计划文字只列了恢复、对账、启动绑定三处，这几处全局扫描不在 B 的范围里，按计划阶段 B 已做完它该做的。放到阶段 C 第 0 条之后：C 第 0 条删金额计价本来就要改 `accounting_recovery.py`（"按上限计入"事件的金额字段），同一文件一次改完。
- **计划文字怎么改**：
  1. 计划阶段 C 第 0 条之后插入：
     > 0′. **主循环全局扫描进同一边界**（2026-10-03 阶段 B 评估裁决，原记入 F 的改为代码项）：`_cycle_inner` 里在任务边界之外的全局扫描——迟到用量导入（按预留逐条）、保证通道每轮工作（按任务逐个）、执行图通知、规划等待唤醒、阻塞唤醒——按任务套进同一个 `_mission_round`，地点名写清；上限与停止原因沿用第 9 类（数据损坏当轮停；其余同一处连续 6 轮且至少 2 分钟以"库读写故障"停）。先清点 `_cycle_inner` 里所有在边界外、逐个任务处理的调用，再一次包完，不另写第二个边界。用例 1 条：参数化注入两处（迟到用量导入、保证通道每轮工作），断言 `run()` 不抛、另一个任务完成、出事任务记一轮故障。
  2. 收尾裁决第 3 张"不在本次范围"一段末句"记入阶段 F 的故障切点清单一起核，本次不改"改为：
     > 改为阶段 C 第 0′ 条的代码项（2026-10-03 阶段 B 评估裁决：已知代码缺口不推给测试阶段）。
  3. 计划修订记录加一行（升第 3.7 版）：
     > - **第 3.7 版（2026-10-03）**：阶段 B 完成评估（`HTN补齐-阶段B-完成评估.md`）：B-9 三张偏差单裁决改入——停滞计数、恢复/对账/启动绑定的用例口径按实际接受；主循环全局扫描进同一边界改为阶段 C 第 0′ 条代码项；卡住裁决第 6 类上限旧文字删净。

---

## 三、未完成清单

### 3.1 还要写的代码与用例（阶段 B 范围内）

1. **做法审阅调用不回来的用例**（卡住裁决第 6 类测试，未裁决偏差）。落点：`T/product_world/test_review_call_unanswered.py`，照现有 `_ReviewHeld` 把用途换成做法审阅：扣住一次 → 原意图以"被打断"结束、新会话重开、放行后拿到结论、任务继续；一直扣住 → 两次调用后记"没有结论"，原因含"审阅调用没拿到回复"，交给规划器。**同时改写或删掉**旧用例 `T/full_target/test_service_intent_provider_blocker.py::test_a_method_review_on_an_unknown_outcome_keeps_its_original_call`：它断言"审阅一直保留原调用、任务停在规划中、审阅员只被调 1 次"，而第 6 类之后，阻塞的审阅调用过了服务时限（该世界里是 0.3 秒）就按"被打断"收口并重开第二次——读码判断这条现在很可能是红的（未运行）。最省的做法是把它直接改成上面"做法审阅扣住一次"的那条。只跑这两个文件。
2. **"放弃条件不满足时拒绝"的断言**（补全方案第 1 批验证，未裁决偏差）。落点：`T/product_world/test_stuck_plan_change_exits.py` 的"放弃"参数里，在旧尝试还没停下时点一次放弃 → 按名拒收 `TASKGRAPH_CONVERGENCE_NOT_QUIESCENT`、事件与回执一行不多。一条断言，不新开用例。

### 3.2 阶段收尾流程（第六节）

3. 阶段 B 收尾这批（分支 htn-b5，提交 `3a8c4697`、`29a17fb7`、`8cffed4f`）做一次 **Opus 只读核验**（只报阻断）。实施记录 B-9 没有核验记录。
4. **发版**：换版本号（现在仍是 `opt.142`，`SDK/version.py`、`backend/deskpet/sdk_adapters/sdk_candidate.py:29`），Host 钉版。B-9 的改动还不在钉住的包里。
5. **更新 `ARCHITECTURE/`**：`ARCHITECTURE/PROJECT_STATUS.md` 最后一条还是 opt.142（第三批收尾），没有阶段 B 收尾（停滞计数、恢复/对账/启动绑定进边界、两件删除）。

### 3.3 还要改的计划文字

6. 第二节三张偏差单的三处改动，计划升第 3.7 版（见第二节）。
7. **卡住裁决第 6 类残留**：收尾裁决第 2 张要求"3. 上限"**整段替换**，实际只在前面加了一段说明，原来两条旧圆点（`HTN补齐-阶段B卡住缺陷-裁决.md:308-309`："同一审阅对象累计被打断：复用 `NON_MODEL_FAILURE_CAP = 6`""到了上限，记'没有结论'，原因写'审阅调用 N 次没有拿到回复'"）还在，和新文字矛盾，删掉。汇总表第 6 行（同文件 `:465`）"上限与具名停止"一格仍是"累计 6 次 → '没有结论'"，按收尾裁决第 2 张替换为：
   > 每次等 `_service_blocker_limit`；每包 2 次 × 同一版要求最多切 3 次包（根终审）/ 每个目标最多 3 个做法（做法审阅）→ "没有结论"或"没有可派发的工作"，详情写明切包用完与被打断
8. （记录用，非计划）收尾裁决第 3 张要求在实施记录 B-4 末句（"已知未处理……原有问题"）后补一句"2026-10-03 收尾裁决：上述两项按卡住裁决第 9 类补写后的口径做，复用同一个边界函数"，没补。

做完以上 8 项，阶段 B 按计划完成。

---

## 四、抽查："记录说做了但代码里没有 / 代码做了但记录没写"

1. **代码改了行为，旧用例没跟上，记录没写**：第 6 类让阻塞的审阅调用过时限就收口（`SDK/orchestrator/event_handler.py:5402-5406` → `_end_overdue_review_call`），但 `T/full_target/test_service_intent_provider_blocker.py:197-241` 仍断言做法审阅"保留原调用、永不重发、任务停在规划中"，文档字符串还写着"这种等待该不该自己结束是待定问题"。该文件最后一次改动在第 6 类之前（提交 `693d215e`），实施记录 B-5"只跑改动对应的测试"没有覆盖到它。判断依据是读码，没有运行。
2. **记录写"测试：产品同形 3 条（根终审…）"，没提做法审阅**：卡住裁决第 6 类要的做法审阅两条用例没写，也没登记偏差（见 1.2 节）。
3. **记录说做了、代码里也有**（抽查 8 处，均对得上）：通知启动时从回执补回（`BE/service.py:332`）；指标"金额一律未定价"（`BE/diagnostics.py:408` `unpriced=True`）；台账读写加锁（`SDK/runtime/operation_reconciliation_file_publish.py` 文件头）；`ActionExecutor.reconcile` 已删；被挡通知在作业结束时放回待发（`SDK/storage/taskgraph_convergence.py:339` 起）；第 6 类过时注释已改（`event_handler.py:8199-8222`）；放弃事实进规划包（`planner_views.py:249`）；"为什么还不开工"接到界面（`LiveGraph.tsx`）。
4. **代码做了但记录没写**（不影响结论）：本阶段时间段里有一笔隔离错误码改名 `RESTORE_QUARANTINED` → `ROOT_QUARANTINED`（提交 `6a53a9eb`，`SDK/assurance/contracts/host-error-v1.schema.json`），属阶段 A″ 的收尾，A″ 那节没记。
5. **记录与收尾裁决用语不一致（不影响结论）**：收尾裁决说改完升"第 3.5 版"，计划实际升为第 3.6 版（3.5 已被流程改定占用），修订记录写清了，无碍。

---

## 五、需求与场景台账

按收尾裁决第 6 张首次建立：`plans/2026-09-27-desktop-next/需求与场景状态.md`。本次最多标到"已接入"，场景一律"未运行"。
