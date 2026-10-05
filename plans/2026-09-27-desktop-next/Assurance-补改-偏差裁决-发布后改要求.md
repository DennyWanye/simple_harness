# 偏差裁决：发布已生效后用户改要求，任务永远挂着

日期：2026-10-05。裁决人：独立裁决子代理（只读；只跑了点名的那一条用例，另用内存补丁在同一条用例上验证了修法，没有改任何仓库文件）。

复现用例：`SDK/tests/orchestrator/product_world/test_requirements_amend.py::test_requirements_amended_after_the_publish_never_publish_again`（下文 `SDK` = `sdk/simple-harness-sdk`，源码前缀 `src/agent_orchestrator/` 省略）。

原计划依据：Assurance 1.1 用例 E06（`plans/Assurance/specs/1.1/implementation/sdk-cases.json`）——"原动作已发生，用户要求或输入变化；旧回执晚到并重新使用；另试图重发 → 保存原事实/费用；旧接受不满足新要求；可新 scope 审原事实，禁止重发凑证据"。

---

## 一、因果链（读代码 + 实测）

实测方法：在同一条用例上用内存包装打印 `propose_action` 的返回、结果审查准备的成败、"合法等待"的读数（不改文件）。

### 1. 改要求后系统为同一效果又交了一份申请单

- 改要求 → 要求第 2 版 → 人重新确认完成映射 → 新映射的 `spec_hash` 变了。
- `orchestrator/system_operations.py:328-333` `pending_system_operations` 只按"（效果键, **当前** spec_hash）"找本效果的申请单（`mine`）。旧申请单属于旧 spec_hash，不算；于是 `mine` 为空、没有头，直接走到 `:385` `_resubmit_plan` 新交一份（`OperationIntentSubmitted` 第 2 条）。这里**不看操作台账**：同一个业务动作（任务 + 连接器 + 操作 + 目标，`action_commits.py:196` `business_action_id`）在本任务里已经 SUCCEEDED，这件事系统准备申请单时不知道。
- 新申请单的来源文件由 `_sources`（`system_operations.py:128-171`）按新计划挑：实测挑中了**另一步**（`task-38feece…`）产出的同名文件 `reports/weekly.md`，字节相同（内容哈希都是 `99a68de5…`），但产物编号不同（`artifact-3bd63aad…` 对比原来的 `artifact-32dcb1fd…`）。

### 2. 物化被台账拒绝，但拒绝被回滚、只留一条去重的"推迟"

- 申请单审阅通过 → `operation_runtime.py:142` `recover_materializations` → `operation_materialization.py:504` `propose_action`。
- `action_commits.py:363-382`："同一候选再交一次"只在参数哈希与产物哈希都相同时成立。参数里系统绑定了产物编号与存储位置（`action_commits.py:179-185` `bind_artifact_params`），产物编号变了 → 参数哈希不同（实测 `c16de795…` 对比原来 `7879616f…`）→ 落到 `:390-391` `refused = "action_already_executed"`（注释："reality moved; a new Mission asks again"）。
- `operation_materialization.py:518-519` 把 REFUSED 抛成 `OP_INTENT_CONFLICT`，整个物化事务回滚（`ActionRefused` 事件也一起回滚）；`operation_runtime.py:164-176` 记一条 `OperationMaterializationDeferred`，键是"申请单:错误码"，**每轮同样被拒、同一个键，不再出新事件**。所以：没再发布（好）、没新审批卡，但每轮白跑一次物化。

### 3. 本来就有"按新范围审原事实"的路，被两处"整份任务引用相等"挡死

- `operation_runtime.py:212-311` `advance_operation_outcomes` 遍历本任务**全部**申请单；对已生效的旧申请单，按**当前**要求与当前归属范围找结果审查绑定（`:241-248`），找不到就为**旧申请单 + 新范围**准备一份新的结果审查（`:278`）。这正是 E06 说的"可新 scope 审原事实"——设计上已经有，`runtime/operation_ref_resolver.py:188-201` `resolve_historical` 的注释也写明"执行事实由 T3 另行绑到当前已批准的效果归属"。
- 但它被挡了两次：
  - `operation_outcomes.py:148-158` `prepare_operation_outcome_review` 要求"原范围的任务引用 == 现行归属的任务引用"（编号 + 合同版本 + 合同哈希全相等），否则 `OP_EFFECT_SCOPE_STALE: executed owner differs`。效果的归属是根目标；**改要求本身就会让根目标换合同版本**（`operation_completion.py:431-433` 注释："用户改要求后根目标换了合同"）。所以改要求后这条路必然被挡。实测每轮都报这个错。
  - 用内存补丁放过第一处后，存储层 `storage/operation_completion_store.py:695-703` `insert_outcome_binding` 有同一条核对（`old_scope_document.task_ref != current_scope_document.task_ref`），报 `operation outcome fact is not valid for the current completion slot`。这一层本身就是为"旧申请单 + 新 spec"设计的（同一函数里分别读 `old_spec` / `current_spec`，`:651-692`），只是任务引用比得太严。
- 这两处被挡后错误都被 `operation_runtime.py:299-311` 吞成一条去重的 `OperationOutcomeDeferred`。

### 4. 为什么既不完成也不停、不问人

- 旧的效果验收按旧 spec 写的，新要求下不算数（`completion_status.py:410` `outcome.spec_hash != spec.content_hash()`、`:427` 审查包要求版本必须是现行）——这一半是对的（E06"旧接受不满足新要求"）。
- 停滞判断不触发：`orchestrator/event_handler.py:2492-2551` `_has_pending_operation_completion` 只问"有没有准备好内容、效果还没完成的归属"——有，于是 `:2607` 把它当**合法等待**（实测每次都是 True）。它不看"推进这项效果的每一条路是否都已被确定性地拒绝"。合法等待成立 → `_idle_facts` 直接返回 → 永远不记停滞、不问规划器、不停。

**一句话**：改要求换了完成映射，系统准备申请单只认"本版映射下有没有申请单"、不认台账"这次发布已经发生过"，于是又交一份、被台账拒绝；本该接手的"按新范围重审原事实"被两处"根目标合同版本必须不变"的核对挡死；而效果未完成被当成合法等待，停滞检测永远不触发。

---

## 二、修法

### 修法 A：仿照"沿用叶子重审"——不建新申请单，按新要求重审原来那次发布

**做法**
1. `system_operations.py` `pending_system_operations`：在定出来源文件之后（`:327` `artifact, acceptance, _ = found[0]` 之后）先读操作台账：本效果对应的业务动作（`business_action_id(任务, 连接器, 操作, 目标)`）最新一版（不计 REFUSED）若是 SUCCEEDED / HANDED_OFF / UNKNOWN，且本版映射下还没有物化过的申请单——
   - HANDED_OFF / UNKNOWN：跳过（交给现有的对账、人裁决流程）；
   - SUCCEEDED 且已发布的内容哈希（动作参数 `content_hash`）== 现在要发布的来源文件内容哈希：跳过，原因写"原事实待按新要求重审"——由 `advance_operation_outcomes` 现成的路接手；
   - SUCCEEDED 但内容哈希不同：见修法 B 的具名出口（不能重发，也不能拿旧发布冒充新内容）。
2. `operation_outcomes.py:154-158` 与 `operation_completion_store.py:695-703` 两处任务引用核对，从"整份引用相等"改成"**同一个任务编号**，且现行范围的要求版本不低于原范围的"（与沿用叶子重审的口径一致：TaskGraph 补全方案第 4 节第 3 条"任务合同不变、现行范围的要求版本不低于冻结的"——这里合同变化正是改要求本身带来的，身份按任务编号认）。义务相等、效果键归属的核对不变。

**效果**：原来那份申请单、那个动作、那次交接、那张回执原样保留（保存原事实/费用）；旧验收在新要求下照旧不算（不动 `completion_status.py` 的核对）；系统为**原申请单 + 新范围**准备一份新的结果审查，由结果审阅员（独立会话）按新要求判；通过就写新范围下的效果验收；全程不出新申请单、不出新审批卡、不重发。

**大约改动**：`system_operations.py` 约 25 行（台账读取小函数 + 闸门），两处核对各约 4 行（含注释），合计约 35 行。

**对现有用例的影响**：
- 不改要求的正常路径：闸门条件里"本版映射下还没有物化过的申请单"为假 → 行为不变（`test_publish_variants.py` 全组、`test_system_operation_intents.py` 不受影响；"证实没生效后按原内容重交"那条路台账最新一版是 FAILED，不进闸门）。
- 复现用例最后一条断言 `len(list_delivery_receipts) == 1` 要改：交付回执按"每一份效果验收一张"写（`operation_outcomes.py:526-527` 在 `validate_scoped_outcome_command` 里按绑定编号生成 `delivery:<绑定>`；`completion_status.py:482-490` 要求回执的验收编号等于本份验收），所以会有 2 张——**都指向同一个操作编号**。这不是第二次发布，是同一次发布在两版要求下各有一份验收记录。
- 已登记变异（`tests/orchestrator/acceptance_assets/mutations.json`）没有落在这几行上。

**是否符合硬约束**：符合。
- 判断交给 LLM：新要求下这次发布算不算数由结果审阅员判，系统不判；
- Harness 只管秩序：闸门读的是台账事实（同一业务动作在本任务里已经发生过），与 `action_commits.py:390-391` 台账自己的拒绝规则是同一条规则的提前一步，不是新语义规则；内容哈希相同 / 不同是字节事实，`system_operations.py:345` 已有同样的比较先例（人拒绝后"内容换了新版本才替代重交"）；
- 同一件事一条路径：去掉了"系统另交一份注定被拒的申请单"这条与 `advance_operation_outcomes` 竞争的旁路，"按新范围审原事实"只剩一条路；
- 不动提示词、工具说明、模板：不影响准入身份。

### 修法 B：最小秩序修法——不再反复重试，把事实交出去，任务不悬着

**做法**
1. **如实读"合法等待"**（`event_handler.py`）：新增 `_materialization_refusals(mission_id)`（仿 `:2482` `_handoff_refusals`）：当前头申请单（未被替代、没有物化回执）有 `OperationMaterializationDeferred` 记录的，列出（申请单、效果键、拒绝码）。`_has_pending_operation_completion` 开头遇到它返回 False（不再当合法等待）；`:2888`、`:2915`、`:2945` 三处停滞/停机详情里一并展开（与 `_handoff_refusals` 同样写法）。之后走现成的"停滞 → 再看一轮确认 → 先问规划器一次（`NO_DISPATCHABLE_WORK`，事实里带上被拒的物化）→ 规划器回应过仍在原地 → 具名停下"。
2. **只这一条**的话：复现用例会以"没有可派发的工作"停下（问过规划器一次），不再悬着，也不再发布；但任务**完成不了**——原来那次发布明明可以按新要求重审，B 单独用等于放弃 E06 的"可新 scope 审原事实"。

**大约改动**：约 20 行。

**对现有用例的影响**：只在出现物化推迟时改变判停；现有用例里物化推迟都是瞬时的（下一轮就物化成功），空闲判定前就消失，不受影响（需跑 `test_publish_variants.py`、`test_stall_asks_planner_first.py`、`test_progress_idle_verdict.py` 确认）。

**是否符合硬约束**：符合（只补"如实呈现事实"和"不把死等当合法等待"，判断交规划器 / 人）。但单独用不满足 E06。

### 修法 C（我的建议）：A 为主，B 的两个具名出口 + 一处兜底补在 A 的失败分支上

A 把"内容相同"这条主路打通；A 之后还剩三种情形会重新落入"永远等"，各用现成机制具名交出去：

1. **已发布内容与现在要发布的内容不同**（A 第 1 条第三种）：台账不许重发（本任务内同一目标只发生一次），也不能拿旧发布冒充新内容。按 `system_operations.py:242-270` `_ask_planner_after_rejection` 的写法，用现成的 `_ask_planner_once`（`:183`）把事实交规划器一次：目标、原发布的内容哈希与要求版本、现在来源文件的内容哈希与要求版本、"本任务不能再次发布同一目标"；规划器决定（问用户、恢复原内容、不改）；回应过仍未解决 → `_stop`（`:174`）具名停下。触发类型沿用现有的 `OperationNotApplied`（新要求下的这项效果无法生效），**不新增触发类型**（触发类型在规划器契约里，新增会改准入身份）。
2. **按新要求重审原发布，审阅员判不通过**：`operation_runtime.py:250-276` 现在对"正式结论是 REJECTED / REWORK"只 `continue`，效果又落回合法等待——这是改要求之前就存在的缺口（不改要求时结果审查被判不通过同样会挂），A 让它多了一个入口，必须一起补。按沿用叶子重审"没过 → 修复请求交规划器"的先例（`carried_review.py` `CARRIED_RESULT_REJECTED`），用 `_ask_planner_once` 交一次，触发类型沿用现有的 `VerifierAcceptanceRejected`，详情带审阅员的不通过条目与理由；回应过仍未解决 → 具名停下。判不下来（INCONCLUSIVE）仍走现成的"问人裁决"（`:261-268`），不变。
3. **兜底**：修法 B 第 1 条（物化被拒不当合法等待），防以后再出现别的"注定被拒的物化"时重新悬着。

**大约改动**：A 约 35 行 + 出口 1 约 30 行 + 出口 2 约 20 行 + 兜底约 20 行，源码合计约 **105 行**；用例约 120 行。

**是否符合硬约束**：符合。所有"算不算数"的判断仍在结果审阅员 / 规划器 / 人手里；Harness 新增的只有：读台账事实决定"不再交注定被拒的申请单"、把三种确定性死路如实交出去并设"问一次"的上限。

---

## 三、裁决

**选 C：先做 A（含两处核对），同一批补 C 的出口 1、出口 2 和兜底。**

对照 E06 逐句：
- "保存原事实/费用"：A 不建新申请单、不动原动作 / 交接 / 回执；原型实测全程 1 份申请单、1 张审批卡、1 次交接、发布目录 1 个文件。
- "旧接受不满足新要求"：不动 `completion_status.py:410/:427`，旧 spec 下的效果验收在新要求下照旧不算；原型实测新要求下另写了一份效果验收才算完成（`OperationOutcomeAccepted` 2 条，两份绑定都来自原申请单）。
- "可新 scope 审原事实"：A 正是让现成的 `advance_operation_outcomes` 在新范围下为原申请单开结果审查——不是新造一条路，而是拆掉两处把它挡死的核对；原型实测通过并完成。
- "禁止重发凑证据"：A 的闸门让系统不再为同一业务动作准备第二份申请单；内容不同的情形（出口 1）也不重发，交规划器 / 人。台账原有的拒绝保留作最后一道。

对照硬约束：判断都交审阅员 / 规划器 / 人；Harness 只读台账与字节事实、设上限；"按新范围审原事实"只留一条路；不碰提示词与工具说明；不留兼容分支（两处核对直接改成新口径，不保留旧比较）。

不选单独 B：任务不悬着了，但该完成的任务会被停掉，丢了 E06 的"可新 scope 审原事实"。
不选"让新申请单挂到旧动作上"（改 `propose_action` 把同字节当同一候选）：那等于给一份从没导致动作的新申请单造一次物化、把旧回执借给新申请单——正是 E06 "旧回执晚到并重新使用" 要防的情形，还会撞操作 ↔ 动作一对一的链接。

---

## 四、施工清单（按顺序）

> 改 `src` 后按惯例重生成部署清单并清字节码（`scripts/build/taskgraph_manifest.py generate --upstream …`，见上游证据目录），否则用例报 `taskgraph_deployed_source_unverified`。只跑下面列出的用例，不跑全量。

**第 1 步：结果审查准备处的归属核对**（`orchestrator/operation_outcomes.py` `prepare_operation_outcome_review`，约 :154-158）
```
原：original["document"].task_ref != owner.task_ref
改：original["document"].task_ref.id != owner.task_ref.id
    or int(original["document"].requirements_ref.revision) > int(owner.requirements_ref.revision)
注释：改要求会让效果归属（根目标）换合同版本；同一个任务、要求版本只往前走，就是"按新范围审原事实"（E06）。
```

**第 2 步：存储层同一条核对**（`storage/operation_completion_store.py` `insert_outcome_binding`，约 :695-703）
```
原：or old_scope_document.task_ref != current_scope_document.task_ref
改：or old_scope_document.task_ref.id != current_scope_document.task_ref.id
    or int(old_scope_document.requirements_ref.revision) > int(current_scope_document.requirements_ref.revision)
```

**第 3 步：系统准备申请单前先读台账**（`orchestrator/system_operations.py`）
```
新增 _ledger_head(store, mission_id, operation):
    live = [v for v in store.list_action_versions(business_action_id(mission_id, *operation)) if v["state"] != "REFUSED"]
    return live[-1] if live else None
新增 _materialized_under(store, intents, effect_key, spec_hash):
    本效果、本 spec_hash 的申请单里有没有已有 "materialize:<申请单>" 回执的
pending_system_operations 中，artifact, acceptance, _ = found[0] 之后：
    executed = _ledger_head(store, mission_id, operation)
    if executed 且 state ∈ {SUCCEEDED, HANDED_OFF, UNKNOWN} 且 not _materialized_under(...):
        if state == SUCCEEDED 且 executed["params"].get("content_hash") != artifact.content_hash:
            plans.append({"effect_key", "published_earlier": {target, effect_key, action_key,
                          published_content_hash, published_requirements_revision（从原申请单行取）,
                          current_content_hash, current_requirements_revision}})
        else:
            plans.append({"effect_key", "skip": "earlier_operation_fact"})   # 原事实交给结果重审 / 对账
        continue
```

**第 4 步：出口 1——内容不同，交规划器一次**（同文件）
```
新增 _ask_planner_about_earlier_publish(orch, mission, facts)，照 _ask_planner_after_rejection 写：
    key = f"operation-published-earlier:{effect_key}:r{current_requirements_revision}"
    event_type = "OperationNotApplied"（沿用，不新增）
    detail = {"reason": "published_under_earlier_requirements", **facts,
              "explanation": "本任务已按第 N 版要求发布过 <目标>（内容哈希 X）；现在的来源文件内容是 Y，同一目标在本任务里不能再发布"}
    stop = 回应过仍未解决 → _stop(orch, mission.id, {"reason": "published_under_earlier_requirements", ...})
prepare_system_operations 里加分支：if "published_earlier" in plan: 调它，返回 True 则 return True，否则 continue
```

**第 5 步：出口 2——按新要求重审原发布没通过，交规划器一次**（`orchestrator/operation_runtime.py` `advance_operation_outcomes`，约 :250-276）
```
在 current 循环里：record 存在、不是通过 / 已裁决通过、也不是待问人的 INCONCLUSIVE 时：
    if record.verdict in {REJECTED, REWORK}:
        from .system_operations import _ask_planner_once, _stop
        if _ask_planner_once(orch, mission, key=f"operation-outcome-rejected:{binding_id}",
                             event_type="VerifierAcceptanceRejected",
                             detail={"reason": "operation_outcome_rejected", "effect_key", "binding_id",
                                     "verdict", "findings"（审阅员不通过条目与理由）, "requirements_revision"},
                             stop=lambda why: _stop(orch, mission_id, {"reason": "operation_outcome_rejected", ...})):
            return True
        continue
```
（`_ask_planner_once` 现在是模块内函数；按现有写法直接导入即可，不改它的签名。）

**第 6 步：兜底——被拒的物化不当合法等待**（`orchestrator/event_handler.py`）
```
新增 _materialization_refusals(mission_id) -> {"materialization_refused": [...]} 或 {}：
    当前头申请单（未被替代、无 materialize 回执）里有 OperationMaterializationDeferred 事件的，列（申请单, 效果键, 拒绝码）
_has_pending_operation_completion 开头：if self._materialization_refusals(mission.id): return False
停滞 / 停机详情三处（约 :2888、:2915、:2945）在 **self._handoff_refusals(...) 旁边加 **self._materialization_refusals(...)
```

**第 7 步：改复现用例**（`tests/orchestrator/product_world/test_requirements_amend.py::test_requirements_amended_after_the_publish_never_publish_again`）
```
删掉 DIAG 打印块。
末条断言改为：
    deliveries = HtnStore(world.store).list_delivery_receipts(mission_id)
    assert len({d.operation_id for d in deliveries}) == 1      # 只有一次真实发布
    assert len(deliveries) == 2                                # 两版要求各一份效果验收
新增断言：
    OperationIntentSubmitted 恰 1 条；ApprovalRequested 恰 1 条；ActionHandedOff 恰 1 条
    OperationOutcomeAccepted 恰 2 条，对应两份绑定的 intent_id 相同（都是原申请单），第二份绑定的 spec_hash == 第 2 版映射的 spec_hash
    没有 OperationMaterializationDeferred
docstring 里"动作只交接一次"保留，补一句"新要求下由结果审阅员重审原来那次发布"。
```

**第 8 步：新增变体——内容不同**（同文件，约 60 行）
```
场景：同上，但第 2 版要求让写周报那一步按新要求重做、写出不同字节（例如脚本执行者按任务合同里的要求条目数写入内容，或改要求时改写 c-user-1 让旧叶子沿用不了）。
断言：发布目录仍只 1 个文件、交接仍 1 次、没有第二张审批卡；
      恰 1 条 PlanningRepairRequested，source_key 以 "operation-published-earlier:" 开头，详情含两个内容哈希；
      规划器回 NO_CHANGE 后任务 FAILED，final_report.detail.reason == "published_under_earlier_requirements"。
```

**第 9 步：新增变体——重审没通过**（同文件，约 50 行）
```
场景：同复现用例，审阅员脚本对 purpose == OPERATION_OUTCOME 且 requirements_revision == 2 的审查回 REJECTED。
断言：不重发、不出新卡；恰 1 条 PlanningRepairRequested（source_key 以 "operation-outcome-rejected:" 开头，详情含审阅员理由）；
      规划器回 NO_CHANGE 后任务 FAILED，reason == "operation_outcome_rejected"；任务不再停在 ACTIVE。
```

**第 10 步：改坏核验（不进用例，跑完恢复；恢复前先 cp 备份，不用 git checkout，跑完清 __pycache__）**
- 去掉第 3 步闸门 → 复现用例变红（2 份申请单）；且有第 6 步时任务应以"没有可派发的工作"具名停下、详情含 `materialization_refused`，而不是挂到用例超时——这同时验证兜底。
- 第 1 步或第 2 步改回整份引用相等 → 复现用例变红（任务不完成）。
- 第 4 步改成照样交申请单 → 第 8 步变体变红。
- 第 5 步改回只 `continue` → 第 9 步变体变红（挂住）。

**第 11 步：只跑这些用例**：本文件三条（复现 + 两个变体）；`full_target/operation_completion/test_publish_variants.py`、`test_system_operation_intents.py`（第 3 步闸门不影响正常路径的确认）；`full_target/test_stall_asks_planner_first.py`、`test_progress_idle_verdict.py`（第 6 步）；`product_world/test_carried_review.py`（同一口径的先例，确认不受影响）。

**第 12 步：记录**
- E06 承接写进 Assurance 用例对照（`Assurance-现状对照-2026-10-05.md` 把 E06 从"缺失"改为承接到上面三条用例）；
- 偏离记一条：交付回执按"每份效果验收一张"，改要求后同一次发布会有两张回执指向同一操作；
- `ARCHITECTURE/` 对应模块与 `PROJECT_STATUS.md` 按项目规则更新。

**会被打红、需要同步改的现有用例**：只有复现用例本身的末条断言（第 7 步）。其余列在第 11 步的用例按代码阅读不受影响，需实跑确认。

---

## 五、原型验证记录（内存补丁，未改任何文件）

在点名用例上，用 `exec` 改写函数源码的方式临时套上第 1、2、3 步（第 3 步只做"跳过"分支）：
- 改要求后系统没再交申请单（全程 `OperationIntentSubmitted` 1 条、`ApprovalRequested` 1 条、`ActionHandedOff` 1 条）；
- 原申请单在新范围下准备了新的结果审查，审阅通过，`OperationOutcomeAccepted` 2 条（两份绑定的申请单相同）；
- 任务 COMPLETED，判定按 `file:reports/weekly.md`、发布、`file:reports/extra.md` 三条逐条判，发布目录 1 个文件；
- 唯一失败的是末条断言 `len(list_delivery_receipts) == 1`：实际 2 张，都指向同一操作编号 `operation-1527…`、阶段 PERSISTED——与第 7 步的改法一致。
- 过程中出现过一次 `CHECK_POLICY_UNRESOLVED: OPERATION_OUTCOME`（检查策略还没投影），下一轮自动成功，属正常重试。

第 4、5、6 步没有做原型，按代码阅读给出；第 5 步涉及的"结果审查被判不通过就挂住"是改要求之前就有的缺口，读代码判断，未实跑复现。
