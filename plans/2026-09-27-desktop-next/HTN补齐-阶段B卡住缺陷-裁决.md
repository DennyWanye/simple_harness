# HTN 补齐 · 阶段 B 第 6 条：卡住类缺陷裁决

日期：2026-10-03　　依据代码：工作树 `simple_harness-a4`，分支 `htn-a2`（`737e3509`）
裁决人：独立裁决子代理（只读代码；只跑过一条预期失败用例确认现状，见第 7 类）

路径缩写：
- `SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`
- `T/` = `sdk/simple-harness-sdk/tests/orchestrator/`
- `BE/` = `backend/deskpet/orchestration/`
- `FE/` = `tauri-app/src/`

---

## 〇、总原则（本裁决怎么判）

1. **先分清是"秩序"还是"判断"。**
   - 秩序归 Harness 修：证明有没有、围栏该不该还在、账结没结、异常有没有隔离。
   - 判断交出去：发布失败后怎么改内容、人拒绝的理由是什么意思、审查结论是什么。交给规划器或人。
2. **每一类只留一条出口。**
   - 对外发布：凡是"这次发布确认没生效、而且不是人拒绝的"，都走同一条出口，即系统按原内容重新准备申请、再出一张批准卡（卡上写明上次的事实）。凡是"人拒绝了"，都走另一条唯一出口：交规划器。
   - 主循环里的意外异常，全部在"一个任务一轮"的边界上接住。不再在各处零散地 try。
3. **上限尽量复用现有常量，不新造数字。** 能复用的有：
   - 同一步不扣次数的失败上限 `NON_MODEL_FAILURE_CAP = 6`（`SDK/orchestrator/failure_classes.py:20`）
   - 系统替代重交上限 `SYSTEM_RESUBMIT_CAP = 2`、为同一发布请规划器补步骤上限 `SOURCE_REPAIR_CAP = 2`（`SDK/orchestrator/system_operations.py:83-84`）
   - 一个动作最多交接 2 次 `MAX_HANDOFFS_PER_ACTION = 2`（`SDK/orchestrator/action_commits.py:50`）
   - 规划答错次数 `max_planning_attempts`（默认 3）
   - 等服务回音的时限 `_service_blocker_limit`（`SDK/orchestrator/event_handler.py:7921`）
4. **停止原因尽量用已有的。** `MissionStopReason`（`SDK/contracts/state_machines.py:28-55`）里已有：
   - "批准被拒"（`approval_rejected`）
   - "动作失败"（`action_failed`）
   - "规划失败"（`planning_failed`）

   全文只新增一个：**"库读写故障"（`store_fault`）**，见第 9 类。

---

## 一、逐条裁决

### 第 1 类　发布服务明确拒绝后，动作停在"未了结"，规划器和人都没收到

**现状：真实。**

证据：
- 服务说"不"时，动作记为失败，拒绝原因只写进 `error` 字段。
  - `SDK/runtime/actions.py:171-175`：`ConnectorRejected` → `record_action_outcome(outcome="failed")`。
  - `SDK/orchestrator/action_commits.py:1122-1150`：写 FAILED，发 `ActionFailed`。
- 判定"未了结"：`SDK/runtime/planning_operations.py:241-259`。规则是：失败且交接过（`handoffs ≥ 1`），又没有"权威的未生效证明"，就判"未了结"，并关上操作闸门（同文件 :443-445）。闸门关着时，规划变更一律被拒，拒绝码为 `OPERATION_UNRESOLVED`（`SDK/planning/decision_admission.py:1401-1407`）。
- 证明种类只有三种（`SDK/contracts/operation_payloads.py:59-62`），里面没有"服务明确拒绝"。
- 文件发布这边：
  - 档案里登记的证明种类是空的（`SDK/runtime/operation_profiles.py:189`）。
  - 档案登记时只给了执行适配器，没有对账适配器（:203-207）。
  - 生产代码里一个对账适配器都没有登记。
- 失败的动作根本不进对账：`SDK/runtime/actions.py:219` 只扫"结果不明"和"已交接"两种状态。
- 事实交不出去：
  - 等待视图 `SDK/storage/store.py:1658-1690` 不列失败的动作，所以人看不到。
  - 修复请求来源表 `SDK/orchestrator/planning_repair_requests.py:23-28` 不含任何动作或批准事件，所以规划器也收不到。

**裁决**：

1. **交给谁**
   - 先由 Harness 把"没生效"这件事证实。这属于秩序：证据就在发布连接器自己的台账里。
   - 证实以后，交给**人**：系统按原内容重新准备一份申请，出一张新的批准卡。
   - 这次发布的目标和参数是人在确认页定死的，规划器改不了，所以不先交规划器。
   - 人在卡上点"批准"就是重试，点"拒绝"就进第 2 类的出口。人如果觉得是内容问题，可以在拒绝理由里写明，由规划器去读。
2. **怎么呈现**
   - 在台账上补一份**未生效证明**：
     - 证明种类：新增"连接器台账证明未落地"。
     - 证明来源：文件发布连接器自己的台账。它的文件头注释写明，台账里没有这个键的意图行或最后一行是"已放弃"，就说明链接从未发生（`SDK/runtime/connectors_publish.py:12-19`、`lookup` 在 :289-307）。
     - 服务以"冲突"等理由拒绝时，拒绝发生在写意图行之前（:233-237），所以台账同样证明"没落地"。
   - 新批准卡复用现有的批准请求（`_open_action`，`SDK/orchestrator/action_commits.py:518`），卡上附三项事实：
     - 上次的结局（服务拒绝 / 没送到 / 人裁定失败）；
     - 服务给的原文理由；
     - 这是第几次。
3. **上限**
   - 证据读不到时（连接器不可用），每轮对账重试，同一动作最多 `NON_MODEL_FAILURE_CAP` 次。到了上限，标"需要人"，进"结果不明"卡片（第 3 类那张）。
   - 系统为同一效果按原内容重交，最多 `SYSTEM_RESUBMIT_CAP = 2` 次，也就是连同第一次最多 3 张卡。再没生效，就以"动作失败"停任务，详情写 `publish_not_applied`、每次的结局和理由。
4. **改动落点**
   - `SDK/contracts/operation_payloads.py:59` 的证明种类枚举加一项"连接器台账证明未落地"（与第 3 类的"人工裁定未生效"同一次改）。
   - 新写文件发布对账适配器 `SDK/runtime/operation_reconciliation_file_publish.py`：
     - 读连接器台账时取同一把文件锁；
     - 只在该动作的原调用已结束后才作答（`_release_when_done` 已经在跟踪原调用）；
     - 产出现有的已存否定证明（`SDK/runtime/operation_reconciliation.py:151-208` 的格式）。
   - `SDK/runtime/operation_profiles.py:189,203-207`：在档案里登记这种证明种类和这个适配器。
   - `SDK/runtime/actions.py:219`：对账范围扩到"失败且交接过、但还没有证明"的动作。一条路：凡是离开过我们手、又不是"已完成"的动作，都由对账适配器下结论。
   - `SDK/orchestrator/system_operations.py:292`：现在发现申请已进入执行链就 `continue`。改为：当前这一版的动作已证实未生效、且不是人拒绝的，就按原内容准备替代申请，受第 3 点的上限约束。
   - 改动量：**中**。**涉及协议改动**：证明种类枚举、档案登记的证明种类、对账适配器。档案文档的哈希会变；开发期不兼容旧数据，按当前版本重钉。
5. **测试**（产品同形世界，`FilePublishConnector` 加部署策略，照 `T/product_world/test_operation.py` 的确认页写法）：
   - `test_a_publish_the_service_refuses_is_closed_and_offered_again`：
     - 人批准之前，先在目标目录放一个同名文件。文件名按动作的幂等键算（`_name_for`），模拟外面已有人放了文件。
     - 断言：
       - 动作失败，带"连接器台账证明未落地"证明；
       - 操作闸门打开；
       - 系统出第二张批准卡，卡上带服务原文理由；
       - 删掉那个文件再批准，任务完成，目录里只有一份发布。
   - `test_a_publish_refused_three_times_stops_by_name`：每次都放冲突文件。第 3 次以后任务以"动作失败"停，详情列出三次结局。
   - 改坏检验：
     - 把档案里的对账适配器登记删掉 → 第一条变红（动作停在未了结、没有第二张卡）。
     - 把重交上限加 1 → 第二条变红。

### 第 2 类　人拒绝发布后，任务一直停在"进行中"

**现状：真实。**

证据：
- 拒绝时只做了三件事：批准请求和动作改成"已拒绝"，发 `ApprovalRejected`（`SDK/orchestrator/action_commits.py:737-755`）。
- 没交接过的动作判"确认未生效"（`SDK/runtime/planning_operations.py:252-253`），所以闸门不关。但是：
  - 修复请求来源表里没有 `ApprovalRejected`，规划器收不到；
  - 系统代办看到申请已进执行链就跳过（`SDK/orchestrator/system_operations.py:292`）；
  - 效果状态是"执行被拒"（`SDK/orchestrator/completion_status.py:124`），但"操作效果待完成"仍被当成合法等待（`SDK/orchestrator/progress.py:69`、`SDK/orchestrator/event_handler.py:2317,2356`），所以永远走不到"停滞前问规划器"。
- 用户那边，等待视图不列已拒绝的动作，界面显示"进行中"。

**裁决**：

1. **交给谁**：**规划器**。人拒绝的理由是一段需要理解的话，比如"标题写错了""先别发"，这是判断。规划器可以：
   - 改内容：内容变了，系统按现有逻辑"内容换了新版本：替代重交"，再出一张卡；
   - 问人：用现有的"请人回答"决定；
   - 不改。
2. **怎么呈现**
   - 修复请求里写明：效果名、目标、人的拒绝理由原文、拒绝人、这个效果已被拒几次、还剩几次。
   - 要新增一种修复请求来源"对外操作未生效"，不再借用别的名字。现有先例 `_ask_planner_for_source` 借了"验收被拒"的名字（`SDK/orchestrator/system_operations.py:215`），那是名不副实。
   - 这属于**规划包协议改动**，与阶段 B 第 2 条"放弃事实进规划请求（规划包升版）"**同一次升版**。
3. **上限**：完全照搬 `_ask_planner_for_source` 的模式（`SDK/orchestrator/system_operations.py:183-219`）。
   - 每次人拒绝，只问规划器一次。
   - 规划器提交了决定、但内容没换出新版本，就以"批准被拒"停任务，详情写"规划器已回应但内容未变"。
   - 同一效果被人拒绝累计 `SOURCE_REPAIR_CAP = 2` 次，第 2 次拒绝后直接以"批准被拒"停任务。
   - 被人拒绝过的同一份内容，系统不会再自动重交。这条是秩序：不拿同一个问题反复问一个已经说"不"的人。
4. **改动落点**
   - `SDK/planning/htn/repair_decision.py:52-63` 与 `SDK/planning/htn/repair_adapter.py:34-50`：加来源"对外操作未生效"。
   - `SDK/orchestrator/planning_repair_requests.py:421` 的 `collect_triggers`：把"人拒绝批准"的事件接成这种请求。
   - `SDK/orchestrator/system_operations.py`：加"人拒绝 → 问规划器 → 没换内容就停"的分派，与 `_ask_planner_for_source` 共用同一个函数骨架，不另写一套。
   - ~~`_has_pending_operation_completion` 改判~~ **不改**（2026-10-03 收尾裁决）：人拒绝后，系统代办同一轮就把拒绝接走（重交或交规划器或停）；规划器回应前，等的是在途的规划回合；它问人时，等的是人的回答；它改写内容时，等的是在跑的步骤。每一段都有正当的等待来由，"被拒的效果"从不是唯一挂住任务的东西。按"同一件事只留一条路径"不另加判定。
   - 改动量：**中**。**涉及规划包协议改动。**
5. **测试**：
   - `test_a_rejected_publish_goes_to_the_planner_with_the_reason`：
     - 人拒绝，理由是"标题要改成周报"。
     - 规划器收到一条"对外操作未生效"修复请求，里面带理由原文。
     - 脚本化规划器改内容步骤，系统出新卡，人批准，任务完成。
   - `test_a_publish_rejected_twice_stops_as_approval_rejected`：连拒两次，任务以"批准被拒"停。
   - 改坏检验：在修复请求来源表里去掉"人拒绝"这一项 → 第一条变红（超时，任务仍是进行中）。

### 第 3 类　人把"结果不明"裁成失败后规划器收不到；请求没到服务端时永远不重新交接

**现状：两半都真实。**

前一半：人裁定失败后，闸门仍关着。
- 人工裁决 `override_action_outcome`（`SDK/orchestrator/action_commits.py:1232-1283`）把动作改成失败，`error="human_ruled_failed"`。
- 但没有否定证明，交接过 1 次，所以仍判"未了结"，闸门一直关着（`SDK/runtime/planning_operations.py:252-259`）。
- 这个入口只有命令行能用：`SDK/api/approvals.py:163-173`。对外入口的决定种类 `SDK/api/facade.py:75` 里没有它，Host 和前端也都没有入口（`FE/views/MissionsView.tsx:996-1000` 只显示文字）。

后一半：没到服务端也重交不了。
- 重新交接必须同时满足"结果不明"和"已确认未开始"，并且有已存的否定证明（`SDK/orchestrator/action_commits.py:1048-1052`、:958-963）。
- 但带操作链接的动作，只要结局不是"已完成"，就一律被写成"仍然不明"（`SDK/runtime/actions.py:276-277`）。找不到对账适配器时也写"仍然不明"（:302-305）。
- 所以连接器自报的"查询结果是权威的"（`SDK/runtime/connectors_publish.py:82-84`）不起作用，永远到不了"已确认未开始"。
- 测试 `T/full_target/test_h1h_action_handoff.py:10-14` 也写了这一点。

**裁决**：

1. **交给谁**
   - "没到服务端"属于基础设施失败，由 **Harness 原地重交**（09-28 决定）。
   - 重交用完以后，以及人裁定失败以后，都并入第 1 类的出口：系统重新准备申请、出新批准卡，由人决定。
   - 不另开去规划器的路，原因同第 1 类：参数是人定死的。
2. **怎么呈现**
   - 后一半：第 1 类的对账适配器同时回答"没开始"（台账里没有这个键）。动作得到"已确认未开始"加已存证明，现有的重新交接（`_rehandoff_scoped`，`SDK/runtime/actions.py:206-214`）就能走通，不用新写。
   - 前一半：人的裁定本身就是结论，要写成证明。证明种类新增"人工裁定未生效"（和第 1 类同一次改枚举），证据是裁定人、理由、证据摘要。写入后判"确认未生效"，闸门打开。
   - 人工裁决接到对外入口：
     - SDK 对外入口（`SDK/api/facade.py`）加 `resolve_unknown`；
     - Host（`BE/handlers.py`、`BE/service.py`）加一个命令；
     - 前端"结果不明"条目上加两个按钮："已生效" / "没生效"。必须是用户真实点击。
3. **上限**
   - 重新交接沿用 `MAX_HANDOFFS_PER_ACTION = 2`（`SDK/orchestrator/action_commits.py:50`）。
   - 用完后，经现有的 `finish_proven_nonapplication`（`SDK/runtime/actions.py:211`）结束这个动作，然后进第 1 类的重交上限。
   - "需要人裁定"的卡不设时间上限。人没来，任务就以"等你处理"显示在等待视图里（现有字段 `needs_human`）。这不是卡死：它可见、有名字、能被人解开。
4. **改动落点**
   - `SDK/runtime/actions.py:276-277`：有登记的对账适配器时，按它的结论走，不再强制写"仍然不明"。
   - `SDK/orchestrator/action_commits.py:1232-1283`：裁定"失败"时，在同一事务里写"人工裁定未生效"证明。
   - 对外入口、Host、前端：各加一个小入口。
   - 文件发布的重试政策文档写的是"新发送次数 0"（`SDK/runtime/operation_profiles.py:42-46`）。实施时要核对：重新交接"已确认未开始"的动作，算不算"新发送"。如果读码确认这个政策会挡住重新交接，就把数字改成 1，和交接上限对齐，档案哈希随之重钉。
   - 改动量：**中**（SDK 小，Host 加前端小）。**涉及协议改动**：证明种类。
5. **测试**：
   - `test_a_publish_lost_before_the_service_is_handed_off_again`：
     - 连接器设 `fail_after="intent"`。这是现成的注入手段，模拟写完意图行后连接断开；连接器自己会记"已放弃"。
     - 断言：对账得出"已确认未开始"；系统原地重交一次；任务完成；只发布了一份。
   - `test_a_person_rules_an_unknown_publish_failed_and_gets_a_new_card`：
     - 连接器设 `fail_after="link"`，然后删掉已发布的文件，模拟用户自己删了。连接器查询会报"已发布但不见了"，结果不明，需要人。
     - 经对外入口裁定"没生效"。
     - 断言：带"人工裁定未生效"证明，闸门打开，出新卡。
   - 改坏检验：恢复 `SDK/runtime/actions.py:276-277` 的强制"仍然不明" → 第一条变红。

### 第 4 类　确认页收下了档案到不了的"已送达"

**现状：真实。** 这里的"已送达"指完成标准选了 `DELIVERED`，"档案"指连接器档案。

证据：
- 确认页提交时，`SDK/orchestrator/operation_completion.py:167` 起的 `approve_operation_completion_spec` 不拿要求的完成标准去对照档案支持的标准。合约那边（`SDK/contracts/operation_completion.py:265-266`）也只校验格式。
- 要到提交申请单时才被拒：`SDK/runtime/operation_profiles.py:215-217`（另一处同样的检查在 `SDK/orchestrator/operation_materialization_inputs.py:288-289`），拒绝码 `OP_CAPABILITY_UNSUPPORTED`。
- 被拒以后只记在内存日志里：`SDK/orchestrator/system_operations.py:357-358` 调 `_note`，不进事件。
- 效果一直停在"等申请"，被当成合法等待。

**裁决**：

1. **交给谁**：不交给任何人。这是准入秩序：档案不支持的完成标准，确认页当场拒收，拒绝码为 `OP_CAPABILITY_UNSUPPORTED`。前端已经只列出档案支持的选项（`SDK/api/operation_workspace.py:43-51`），正常点选不会碰到。这一道闸防的是绕过前端、或者部署改变。
2. **怎么呈现**
   - 确认接口返回具名拒绝，前端照常显示错误。
   - 物化时被拒（确认之后部署变了）改为写进事件，不再只记内存日志，然后立即以"动作失败"停任务，详情带拒绝码和完成标准名。
   - 这种拒绝是确定性的，重试没有用，所以不设重试。
3. **上限**：无重试。具名停止原因为"动作失败"，详情写 `operation_capability_unsupported`。
4. **改动落点**
   - `SDK/orchestrator/operation_completion.py:167` 起：用部署已登记的档案（`BuiltinOperationProfiles(loop.connectors)` 的 `supported_milestones`、`milestone_policy_ref`、`evidence_policy_ref`）核对每个效果，与 `SDK/runtime/operation_profiles.py:215-226` 是同一组条件。
   - 把这组条件抽成**一个**函数，确认页和物化两处都调它。同一件事只写一份条件，物化那处只作为"确认之后部署变了"的复核。
   - `SDK/orchestrator/system_operations.py:357`：事件加停止。
   - 改动量：**小**。不涉及协议改动。
5. **测试**：
   - `test_the_confirmation_page_refuses_a_milestone_the_profile_cannot_reach`：确认时提交 `DELIVERED`，接口返回 `OP_CAPABILITY_UNSUPPORTED`，库里没有写进完成要求。
   - 改坏检验：删掉确认处的调用 → 变红。

### 第 5 类　计划提交被拒后，执行图收敛的围栏不解除，任务最终以"动作失败"告终

**现状：真实。**

围栏怎么立：
- 换做法时，`begin_convergence`（`SDK/storage/taskgraph_convergence.py:92-203`）在提交前就写一条"已立围栏"的作业（:152）。
- 被围的目标一律不准派发、不准交接：
  - 派发：`SDK/orchestrator/taskgraph_dispatch.py:67-74`，拒绝码 `TASKGRAPH_TARGET_FENCED`；
  - 交接：`SDK/orchestrator/action_commits.py:895-901`，拒绝原因 `taskgraph_target_fenced`。

被拒以后为什么不解：
- 解除围栏只有两个出口：
  - `mark_applied`（:238-270），只在提交成功时调用；
  - `abandon`（:272-288），只有人工命令会调，而且整个仓库没有调用方。
- 提交被拒的两个处理分支（`SDK/orchestrator/event_handler.py:6775-6803`、:6805-6834）都只记"提交被拒"、走规划阶梯，不碰收敛作业。`SDK/orchestrator/taskgraph_resume.py:29-32` 的注释明说"被拒的决定不解除围栏"。
- `begin_convergence` 不查同一批目标上已有的围栏，所以重提会叠第二道。

为什么最后落到"动作失败"：
- 这个任务有发布时，交接会因围栏被拒。
- `SDK/orchestrator/operation_runtime.py:200-203` 对**任何**交接拒绝都以"动作失败"停任务。

这一环是顺着代码推出来的，还没有测试复现过。

**裁决**：

1. **交给谁**
   - 解除围栏归 **Harness**。围栏只属于一个决定，决定被最终拒绝时，围栏随之解除。这是生命周期秩序。
   - "被拒"这个事实照旧走现有规划阶梯交给**规划器**（`reject_planning`），不变。
2. **怎么呈现**
   - 写"提交被拒"记录时，在**同一事务**里调用现有的 `abandon`，原因写"决定被拒"，并发收敛作业的结束事件。
   - 阶段 B 第 2 条要做的"放弃这次改计划"按钮也调用这个函数。同一件事一个函数，按钮和自动解除只差原因字段。
   - 开新围栏前，若同一任务已有未结束的收敛作业，就在同一事务里先以"被新决定替代"结束旧作业。一个任务同一时刻只有一个活的收敛作业。
   - 交接被拒以后停不停任务，改为查表决定，不在代码里分支：
     - 在错误码表 `SDK/contracts/error_table.py` 给交接拒绝码加一列"暂时性"，`taskgraph_target_fenced` 标"暂时"；
     - `SDK/orchestrator/operation_runtime.py:200` 读这一列：暂时的就留在可交接状态、下一轮再试；不是暂时的才停任务。
     - 这和实施记录里定过的做法一致："哪道秩序要按类型码决定，就在表里加一列让生产代码去读"。
3. **上限**
   - 围栏的寿命等于它所属决定的寿命。决定只有提交或被拒两种结局，而被拒受规划阶梯 `max_planning_attempts` 约束，用完以"规划失败"停任务。所以不需要另设时间上限。
   - 暂时性交接拒绝的重试，随围栏解除而结束，不单独计次。
4. **改动落点**
   - `SDK/orchestrator/event_handler.py:6775-6834`：两个被拒分支调用 `abandon`。
   - `SDK/storage/taskgraph_convergence.py:92` 的 `begin_convergence`：处理旧的活作业。
   - `SDK/orchestrator/taskgraph_resume.py:29-32`：注释和行为同步改掉。被拒后不再抛"未提交"，因为作业已结束。
   - `SDK/contracts/error_table.py`：加"暂时性"一列。`SDK/orchestrator/operation_runtime.py:200`、`SDK/orchestrator/event_handler.py:9983` 都读这一列。
   - 改动量：**中**。不涉及外部协议，但错误码表要加列。
5. **测试**（产品同形世界，两步并行，照 `T/product_world/test_repair_replace_method.py` 搭）：
   - `test_a_refused_method_change_lifts_its_fence`：
     - 修复轮里规划器提新做法，审阅通过，立了围栏。
     - 提交时被拒：在提交事务里用触发器制造一次计划来源变更或冲突，复用 `T/full_target/taskgraph_exec/test_commit_atomicity.py:51-58` 的触发器手法。
     - 断言：收敛作业已结束、原因为"决定被拒"；被围的步骤重新可派发；规划器收到被拒；任务最终按旧做法或下一次提交完成，不以"动作失败"结束。
   - `test_a_fenced_publish_waits_instead_of_failing`：
     - 带发布的任务，发布交接时恰好有围栏。
     - 断言：动作仍可交接；围栏解除后发布成功。
   - 改坏检验：去掉被拒分支里的 `abandon` → 第一条变红（被围的步骤一直不派发）。

### 第 6 类　做法审阅、根终审交出去后结果不明；重启后旧审阅意图停在"已提交"；任务挂着，主循环每轮自称有进展

**现状：真实。**

重启后不收口：
- 重启恢复（`SDK/orchestrator/event_handler.py:2007-2036`、:2060-2065）对审阅意图只做两件事：取消已停任务的回合，重新绑工具。意图留在"已提交"，不收口。

每轮自称有进展：
- 每轮收集时，做法审阅和根终审属于"规划类"意图。拿不到结果就进入 `_resolve_provider_blocked_service`，对审阅直接返回"放弃"（:8010-8011，注释"reviews keep their original executor"）。
- 但上层（:5225）把"返回值不为空"当成有进展，于是每轮 `progressed = True`。
- 空转检测（:2115-2125）只会退避睡眠，不会收口。另外 `_has_inflight`（:2777）把任何"已提交"都算在路上，主循环永不空闲。

两种审阅都永远收不了口：
- 做法审阅：只要还有一个意图没结束，就永远不出"没有结论"（`SDK/orchestrator/method_plan_reviews.py:168-182`、:241-244）。
- 根终审：一直停在"等审阅"（`SDK/orchestrator/root_review.py:770-771`）。现有的"被打断就重切新包"（:653-659）要等回合以失败结束才会触发（`SDK/orchestrator/assurance_review_collect.py:211-216`），回合不结束就永远走不到。

对比内容审阅：它已经有"等到时限 → 取消回合 → 记为被打断 → 不扣次数原地重做"这一套（`SDK/orchestrator/assurance_review_runtime.py:465-474`、`SDK/orchestrator/failure_classes.py:22,41-47`）。

**裁决**：

1. **交给谁**
   - 审阅调用结果不明属于基础设施问题，由 **Harness 原地重开**。
   - 重开用完还没拿到结论，就并入现有的"审查没有结论"出口，不新开路：
     - 做法审阅：`SDK/orchestrator/method_plan_reviews.py:226-240` 的"无结论"；
     - 根终审：`_exhausted_reviews`（`SDK/orchestrator/event_handler.py:8827`）。
   - 阶段 C 第 3 条已定：把"审查没有结论"接到"判不下来"那一条路（新会话复审一次，仍不行出卡片问用户）。阶段 B 只负责把结果不明的审阅**送到这个出口**，不在 B 里再造卡片。
2. **怎么呈现**
   - 三种审阅统一照内容审阅的样子处理。等原调用等到 `_service_blocker_limit` 还没结果，就：
     - 取消回合；
     - 意图记"失败"，失败原因用现有的"被打断的审阅"文字（`INTERRUPTED_REVIEW`），这样 `classify_failure` 自然归为"被打断"、不扣次数；
     - 在同一事务里写"本次审阅调用被打断"记录（`SDK/orchestrator/assurance_review_collect.py:211-216` 已有，改为不要求拿到回合结果）；
     - 下一轮由现有的"重切新包"在新会话里重开。
   - 重启恢复时，回合不在本进程里的"已提交"审阅意图，同样交给上面这一条路。等待时间从意图提交时刻算起，重启不清零。
   - "进展"只认落了库的变化。`SDK/orchestrator/event_handler.py:5225` 只在确实写了东西时才返回"有进展"，单纯在等不算。
3. **上限**
   - 每次等待：`_service_blocker_limit`。
   - **（2026-10-03 收尾裁决改：维持现有两层上限，不另设跨包统一计数；下面原文的"累计 6 次"以此为准。）** 根终审一个包最多调用 2 次，第 2 次也被打断就重切新包，同一版要求最多切 3 次——合起来同一版要求最多 6 次调用；切满后记"切包用完"，走停滞路（先问规划器一次，仍停在原地以"没有可派发的工作"停），停止报告写明"切包用完"和重切原因。做法审阅一份最多调用 2 次，都没回来记"没有结论"交规划器；每个目标最多 3 个新做法。阶段 C 完成后"没有结论"的出口是新会话复审一次、再不行出卡片问用户。
   - 同一审阅对象累计被打断：复用 `NON_MODEL_FAILURE_CAP = 6`，和内容审阅同一个常量、同一张分类表。
   - 到了上限，记"没有结论"，原因写"审阅调用 N 次没有拿到回复"，之后由"没有结论"的出口接手。阶段 C 完成后，这个出口就是新会话复审一次，再不行出卡片问用户裁决。
4. **改动落点**
   - `SDK/orchestrator/event_handler.py`：
     - `_resolve_provider_blocked_service` 里审阅那一支（:8010-8011），从"放弃"改为"超时就收口"，可参照同函数里执行尝试那一支（:7993-8006）；
     - :5225 的进展判断；
     - `recover`（:2060-2065）。
   - `SDK/orchestrator/assurance_review_collect.py:211-216`。
   - `SDK/orchestrator/method_plan_reviews.py:168-182`：意图以"被打断"结束后，按上限决定是重开还是"没有结论"。
   - 改动量：**中**。不涉及协议改动。
5. **测试**：
   - `test_a_method_review_that_never_answers_is_reopened_then_reported`：
     - 用 `LayeredScriptedProvider` 把审阅员扣住（`held`），把 `stall_seconds` 调小。
     - 断言：原意图以"被打断"结束；新会话重开；放行后拿到结论，任务继续。
     - 再写一个上限版本：一直扣住，到上限后记"没有结论"。
   - `test_a_review_interrupted_by_a_restart_does_not_keep_the_loop_busy`：
     - 两段式：第一段在根终审调用中途退出，第二段重开同一个库，照 `T/full_target/operation_completion/test_completion_consumers.py:41-69` 的写法。
     - 断言：第二段跑到空闲时 `run()` 能正常返回（不再需要"跑到条件成立"的绕法）；旧意图已收口；任务完成。
     - 那个用例第 58 行注释描述的绕法随之删掉，改成跑到空闲。
   - 改坏检验：把 :8010-8011 恢复成直接"放弃" → 两条都变红。

### 第 7 类　计划损坏时停任务失败

**现状：真实**，已用严格预期失败用例钉住：`T/full_target/test_hierarchical_event_flow.py:389`，两个参数都是预期失败。本次只跑了这一条，结果 `2 xfailed`。

证据链：
1. 完整性失败时，`_plan_integrity_stop`（`SDK/orchestrator/event_handler.py:1532`）调用"让任务失败"，即 `fail_mission`（`SDK/orchestrator/commit_service.py:1333-1362`）。
2. 它在一个事务里做级联（:1364-1413），给每个"就绪/进行中"的步骤写"已取消"。
3. 终止事件的闸门（:568-579）要求每个执行图成员都有语义绑定。缺绑定的那一步被拒，拒绝码 `TASKGRAPH_TERMINAL_BINDING_MISSING`。
4. 整个事务回滚，任务仍是"进行中"。

**更正用例理由的一处**：异常并没有逃出 `run()`。`CommitRejected` 被 `_cycle`（`SDK/orchestrator/event_handler.py:3224-3229`）吞掉，代价是**整轮**都跳过，所有任务一起跳。下一轮又在同一处失败，所以不但这个任务停不下，还拖住了同一进程里的其他任务。后一半在第 9 类一起修。

**裁决**：

1. **交给谁**：**Harness**。停任务属于秩序。
2. **怎么呈现**：没有绑定，就写不出一条如实绑定的终止记录。所以级联对"是执行图成员、但语义绑定读不到"的步骤，按现有对"受阻"步骤的处理办：
   - 不写终止事件，随任务一起结束；
   - 它的尝试和意图照常关闭；
   - 任务的最终报告里写明哪些步骤因为绑定损坏没写终止记录。

   这是通用规则：任何一次停任务，碰到绑定损坏都这样办。不只限于完整性停止，不另开"损坏专用"的停法。不放宽 `_emit` 的闸门，它仍是"图里的步骤结束必须记绑定"的唯一守卫。
3. **上限**：无重试，当轮停下。停止原因保持现有的"规划失败"，详情带完整性错误码。
4. **改动落点**：`SDK/orchestrator/commit_service.py:1364` 的 `_cascade_stop` 先做与 `_emit`（:568-576）相同的绑定查询，绑定缺失的步骤跳过终止事件。查询抽成一个函数，两处共用。改动量：**小**。不涉及协议改动。
5. **测试**：
   - 去掉 `T/full_target/test_hierarchical_event_flow.py:389` 的预期失败标记，用例转为通过。
   - 新增 `test_a_damaged_plan_stops_only_its_own_mission`：放进第 9 类，见下。
   - 改坏检验：恢复级联对缺绑定步骤写终止事件 → 用例回到失败。

### 第 8 类　取消任务后，尝试的预留一直停在"已预留"，主循环不再重试结账

**现状：真实。** 根因是一处先后顺序错了。

证据：
1. 取消后，主循环收已停任务的回合（`SDK/orchestrator/event_handler.py:5189-5190`）。这里**先**结账（`_settle_if_known`），**后**关意图（`_settle_intent`）。:5242-5243、:6848 是同样的顺序。
2. 结账要求意图已经关闭（`SDK/orchestrator/assurance_settlement.py:47-52`），所以第一次结账必然失败。失败后记一条"预留保留，原因：结账待定"，按对象去重，只记一次。
3. 之后的重试：
   - 已结束任务的旧预留每 300 秒才全量重核一次（`SDK/orchestrator/accounting_recovery.py:28,186-195`）；
   - `run()` 空闲返回以后就没人扫了。

所以计划里写的"不再重试"说得重了一点，实际是"5 分钟后才可能结掉；如果循环已空闲退出，就永远结不掉"。

**裁决**：

1. **交给谁**：**Harness**。记账是秩序。
2. **怎么呈现**
   - 三处一律改成"先关意图，再结账"，和其他调用点（:4955、:6862）统一成一个顺序。
   - 任务刚结束的那一轮，对它做一次全量结账重核，不等 300 秒。
   - 如果用量仍然不明，就按 09-26 用户决定"未知用量按上限结清"、以及"宁可多算不可少算"处理：
     - 复用现有的 `settle_at_upper_bound` 和"按上限计入"事件（`SDK/orchestrator/assurance_consumers.py:697-705`）；
     - 详情原因写"任务已结束、用量始终不明"。
3. **上限**：任务结束后，用量不明的预留最多等 `ENDED_MISSION_RECHECK_SECONDS × 3`（15 分钟）。期间有迟到的用量记录就照实结；过了就按上限结清。不新设常量名，用"300 秒 × 3 次重核"表达。
4. **改动落点**
   - `SDK/orchestrator/event_handler.py:5189-5190`、:5242-5243、:6848：调换顺序。
   - `SDK/orchestrator/accounting_recovery.py:179` 的 `import_late_accounting`：加"刚结束就重核一次"和"超时按上限结清"。
   - 改动量：**小**。不涉及协议改动。
5. **测试**：
   - 把 `T/p35/test_provider_accounting_loop.py:225` 那条用例 docstring 里"不对它下断言"的那半句，补成断言：取消后两次尝试的预留都结清，有用量的照实结，没用量的按上限并留下"按上限计入"事件。
   - 新增 `test_a_cancelled_mission_settles_its_holds_without_waiting_for_the_sweep`：
     - 产品同形世界里，取消一个有在途调用的任务；
     - 断言：在一次 `drain` 之内，预留不再是"已预留"。
   - 改坏检验：把 :5189-5190 换回原顺序 → 新用例变红。

### 第 9 类　磁盘损坏、写失败导致的读侧拒绝逃出 `loop.run()`

**现状：真实**，并且范围比计划写的大。

1. **`_cycle` 只兜三类异常**（`SDK/orchestrator/event_handler.py:3216-3229`）：`StoreBusy`、`CommitRejected`、`IllegalTransition`，而且一兜就整轮跳过。其他异常全部冲出 `run()`，同进程所有任务一起停：
   - 存储层的 `StoreError`、`StoreConflict`；
   - `sqlite3` 的 I/O 错误或磁盘满（`SDK/storage/store.py:441-443` 回滚后原样抛出）；
   - `OSError`、`ContractError`。

   Host 在 `BE/service.py:508-536` 会退避后重跑 `run()`。但坏字节一直在，就会每次在同一处失败，整个服务一直处于降级。
2. **读侧完整性拒绝"同名不同类"**：
   - 主循环接的 `GraphIntegrityError` 是投影层的类（`SDK/graph/projection_validation.py:109`）。
   - 执行图历史读侧抛的是**另一个**同名类（`SDK/storage/taskgraph_store.py:64`，属于 `ContractError`），没有任何地方接它。

   一条确定能冲出去的路径：`collect_triggers` → `dispatch.network()` → `_read_network` → `read_revision` → 历史记录身份不符 → 冲出 `run()`。
3. 迁移裁决里"计划提交时库写入出错"那一条（`HTN补齐-阶段A撇-迁移裁决.md:165-187`）定下的方向至今**完全没有实现**。它建议复用的"凭冻结字节续上"表已在 A′ 删掉，不能再复用。

**裁决**：

1. **交给谁**：**Harness**。按 09-28 决定办：基础设施失败原地重试、不计规划次数、设上限、不影响别的任务。
2. **怎么呈现**：在 `_cycle_inner` 里，把"一个任务这一轮的工作"包进**同一个**边界函数（暂名 `_mission_round`）。边界覆盖：
   - 规划前阶段（:3291-3326）；
   - 按意图的派发和收集（:3340-3368），按意图所属任务归属；
   - `_decide`（:3393-3400）；
   - `run()` 开头的重启恢复（按任务一次、按意图一次）与动作对账（按动作一次，三个调用点都算）；启动时绑定审阅工具按意图接住、交第一轮处理（身份不符算数据损坏）。恢复失败的任务每轮先在自己的边界里重试恢复，成功前跳过它这一轮的其余工作。已结束任务的收尾收集一直出错，到上限后把这条意图关为失败，让额度预留能按上限结清，不冻结。（2026-10-03 收尾裁决补）

   边界接住任何异常以后：
   - 当前事务已经整体回滚；
   - 另开一个短事务写一条"任务一轮故障"事件（类型码、出事的地方、异常摘要、第几次）。这条也写不进去（例如磁盘满），就只在内存里计数；
   - 跳过这个任务这一轮剩下的工作，**别的任务照常**；
   - 下一轮原地再来。重来时不会重新调模型：原始回复和决定都已落库，收集是幂等的；
   - `CommitRejected` 也改由这个边界按任务处理，`_cycle` 不再因为它整轮跳过。

   "重试有没有用"不在代码里分支，而是查表：在 `SDK/orchestrator/failure_classes.py` 加一张"一轮故障"分类表，按异常类和拒绝码分两类：
   - **数据损坏**，当轮停：两个 `GraphIntegrityError`、`TASKGRAPH_HISTORY_INTEGRITY`、`TASKGRAPH_ATTEMPT_INPUT_INTEGRITY`、`TASKGRAPH_SOURCE_INTEGRITY`、`corrupt stored result`；
   - **其余**，原地重试：I/O、磁盘满、锁、触发器拒绝、未知异常。表里没有的一律按"重试"处理，由上限兜住。

   现有散落在 8 处的 `_plan_integrity_stop` 调用点（:3313、:3497、:7246、:8338、:8412、:9047、:9250、:9269、:9333）收进这个边界：边界遇到"数据损坏"类，就调用 `_plan_integrity_stop`。各处的 try 删掉。这样同一件事只留一条路径。
3. **上限**
   - 同一任务、同一出事地点**连续**失败达到 `NON_MODEL_FAILURE_CAP = 6` 轮，并且距第一次失败至少 2 分钟，才停。之所以要两个条件都满足，是为了不让一阵磁盘满在几百毫秒内就耗光次数。中间有一轮成功，计数清零。
   - 超限后以新停止原因**"库读写故障"（`store_fault`）**停这个任务，详情带类型码、地点、次数。
   - 选新原因而不是借用"规划失败"或"运行环境不可用"，因为后两个各有含义，现有注释也明说不许把不同的事报成同一个名字。
   - 停止这一步本身也写不进去时，就不停，继续退避。Host 现有的降级显示会提醒用户，库恢复以后自然停下或恢复。
4. **改动落点**
   - `SDK/orchestrator/event_handler.py`：`_cycle`、`_cycle_inner`、`run`，以及 8 处散落的 try。
   - `SDK/orchestrator/failure_classes.py`：加分类表。
   - `SDK/contracts/state_machines.py`：加"库读写故障"。**这个文件一改，就要重写执行图编解码清单的哈希**（`graph/network_codec_manifest_v5.json`），按补齐计划的要求，和本阶段其他合约改动并成一次。
   - 改动量：**中到大**。主要量在把 8 处收进一个边界，以及给 `_cycle_inner` 分段。
5. **测试**：
   - `test_a_store_fault_in_one_mission_does_not_stop_the_others`：
     - 产品同形世界里建两个任务 A、B。
     - 用触发器 `RAISE(ABORT)` 只挡 A 的某种写入，手法同 `T/full_target/taskgraph_exec/test_commit_atomicity.py:51-58`，条件里限定 A 的任务号。
     - 断言：`run()` 不抛；B 完成；A 留下"任务一轮故障"事件；模型调用次数没有因重试增加；去掉触发器以后 A 原地继续并完成。
   - `test_a_persistent_store_fault_stops_its_mission_by_name`：触发器不去掉，把时间下限调小。断言 A 以"库读写故障"停，B 不受影响。
   - `test_a_damaged_plan_stops_only_its_own_mission`：
     - 两个任务，用 `_damage_binding` 改坏 A 的绑定（同第 7 类）。
     - 断言：A 当轮以"规划失败"停，带完整性错误码；B 同一轮照常推进。这条同时证明第 7 类修复以后，不再整轮跳过。
   - 历史读侧那条：删掉不准更新的触发器，改坏 A 的执行图版本记录（手法见 `T/full_target/taskgraph_exec/test_revision_read_cache.py:46-49`）。断言 A 当轮停、`run()` 不抛。可以并进上一条，参数化成两种损坏方式。
   - 原子性用例 `T/full_target/taskgraph_exec/test_commit_atomicity.py:55` 那段"`pytest.raises` 冲出本轮"改成新断言：本轮不抛；什么都没写进去（原子性断言保留）；留下故障事件。
   - 改坏检验：
     - 把边界里的"写故障事件并跳过"改回"原样抛出" → 第一条变红。
     - 把分类表里历史完整性码改成"重试" → 损坏用例变红（不再当轮停）。

---

## 二、汇总表

| # | 缺陷 | 现状 | 交给谁 | 上限与具名停止 | 改动量 | 协议改动 |
|---|---|---|---|---|---|---|
| 1 | 服务明确拒绝后未了结 | 真实 | Harness 证实，然后人（新批准卡） | 证据重读 6 次；按原内容重交 2 次 → "动作失败" | 中 | 是：证明种类、档案登记、文件发布对账适配器 |
| 2 | 人拒绝发布后一直进行中 | 真实 | 规划器 | 每次拒绝问一次；累计 2 次或规划器没换出新内容 → "批准被拒" | 中 | 是：修复请求来源（并入规划包升版） |
| 3 | 人裁定失败没人收到；没到服务端不重交 | 真实 | 没到服务端：Harness 原地重交；其余并入第 1 类 | 交接 2 次；然后同第 1 类 | 中 | 是：证明种类（和第 1 类同一次改） |
| 4 | 确认页收下档案到不了的"已送达" | 真实 | 确认页准入闸当场拒收 | 无重试；确认后部署变了 → "动作失败" | 小 | 否 |
| 5 | 提交被拒后围栏不解除 | 真实 | Harness 解围栏；被拒事实照旧交规划器 | 随决定生命周期；规划阶梯 3 次 → "规划失败" | 中 | 否（错误码表加一列） |
| 6 | 审阅结果不明、重启后意图挂着、空转报进展 | 真实 | Harness 原地重开；用完进"没有结论"出口（阶段 C 接卡片） | 每次等 `_service_blocker_limit`；累计 6 次 → "没有结论" | 中 | 否 |
| 7 | 计划损坏停任务失败 | 真实（已钉） | Harness | 当轮停 → "规划失败" | 小 | 否 |
| 8 | 取消后预留停在已预留 | 真实（先后顺序错） | Harness | 结束后 15 分钟 → 按上限结清 | 小 | 否 |
| 9 | 读侧拒绝、写失败逃出 `run()` | 真实，范围更大 | Harness | 同一处连续 6 轮且至少 2 分钟 → "库读写故障"（新） | 中到大 | 是：停止原因（要重写编解码清单哈希） |

---

## 三、实现顺序与分批

**第一批：主循环秩序**（第 9、7、8 类）
- 先做第 9 类的边界，第 7 类顺手做完：预期失败用例转为通过，两任务隔离用例能直接证明。第 8 类是三处调换顺序，和它们放在一起。
- 本批唯一的合约文件改动是 `state_machines.py` 加一个停止原因，编解码清单哈希只重写这一次。
- 这一批不碰对外发布，是后面所有批次的地基：后面的用例都依赖"出事不冲垮主循环"。

**第二批：等待会结束**（第 5、6 类）
- 两类都是"在等一个永远不会来的东西"：一个是被拒决定留下的围栏，一个是没人回的审阅。
- 第 5 类的 `abandon` 就是阶段 B 第 2 条"放弃这次改计划"按钮要调的那个函数，建议和第 2 条的按钮同批交付，一个函数两个调用方。
- 第 6 类只把审阅送到"没有结论"出口，卡片留给阶段 C 第 3 条。

**第三批：对外发布**（第 4、1、3、2 类）
- 先做第 4 类（小，独立）。
- 再做第 1 类和第 3 类：两种证明种类、文件发布对账适配器、人工裁决入口三件一起做，协议只改一次。
- 最后做第 2 类：它需要新的修复请求来源，和阶段 B 第 2 条的规划包升版**同一次升版**。如果第 2 条的升版排在本批之后，第 2 类就跟着它走。
- 本批的真机项：按阶段 B 第 5 条的口径，用 computer-use 真实鼠标点击"拒绝发布""结果不明 → 没生效"两个按钮各一次。

**测试口径**（硬约束）：每批只跑改动文件对应的测试，加上本文列出的新用例。不跑全量回归，不跑整目录。每批至少做一条本文列出的改坏检验，确认能变红。

---

## 四、不在本裁决范围、顺带记下的

1. `_ask_planner_for_source` 借用"验收被拒"的来源名把系统事实交给规划器（`SDK/orchestrator/system_operations.py:215`），名不副实。第 2 类加了"对外操作未生效"来源以后，可以评估要不要给它也换一个如实的名字。本次不改。
2. 系统准备的发布申请被审阅员判"不认可"时直接停任务，停止说明里却写着"需要人来判断"（`SDK/orchestrator/system_operations.py:294-299`），说法和行为对不上。归阶段 C 审阅出口统一时一起看。
3. `_resolve_provider_blocked_service` 的文档字符串（`SDK/orchestrator/event_handler.py:7962-7966`）说"根终审会被记为读不出"，和代码不符。第 6 类修复时一并改正。
