# HTN 补齐 · 阶段 F1 阻断核验

- 核验人：独立只读核验子代理，日期 2026-10-04。
- 对象：工作树 `simple_harness-a4`，分支 `htn-f1`，提交范围 `1729d790..0d3c496e`（HEAD = `0d3c496e`，工作树干净）。
- 口径：只报阻断级。没有跑任何测试，没有改除本文件外的任何文件。
- 路径缩写：SDK = `sdk/simple-harness-sdk/src/agent_orchestrator/`。

---

## 结论

**有 1 条阻断（有条件触发）**：F1 对"检查入账时撞上改要求"的修补只修了一半。被放下或还没开始验证的结果，要等"新计划关掉旧尝试"才归档；但新计划**原样保留这一步**时旧尝试不会被关掉。新计划提交后这份结果会被重新验证，读到的完成范围仍是旧计划的，报错冲出主循环。

其余四项（检查入账的错误映射、偏差单 2、三个注入点、部署清单）都没有阻断问题。

---

## 阻断 B1：改要求后新计划保留了正在验证的那一步时，旧结果不归档，重验时冲出主循环

**位置**
- `SDK/orchestrator/event_handler.py` 验证调度约 :3736-3738（窗口里 `continue` 跳过，注释写"关掉旧尝试时它随之归档"）。
- `SDK/orchestrator/event_handler.py` `_verify` 约 :7944-7951（放下分支 `return False`，结果停在 RUNNING，尝试停在 VERIFYING）。

**归档前提不总成立（代码依据）**
- 新计划提交时，旧尝试只经执行图收敛被关掉。收敛目标只有两类：离开计划的步骤，以及绑定（合同哈希/版本、输入绑定版本、派发代数）变了的步骤（`SDK/graph/convergence.py` `compute_convergence_impact` 约 :92-116）。注释原文是"整体计划换版本或上级换做法本身，不撤销一个生产者"。
- 改要求只给根目标换合同（`requirements_amendment.py` 第 2 步），叶子步骤的绑定不变。
- 规划器面对"要求已更新"时可以只做局部修复，例如对**另一步**用 PROPOSE_SUCCESSOR，或只换某个子目标的做法。这时正在验证的那一步原样留在新计划里，不是收敛目标，尝试不会被关。这条路在产品里走得到：`test_requirements_amend.py::test_kept_old_step_is_reported_not_rerun` 就是"改要求后只换一步、另一步留在计划里"；只不过那里留下的步骤已经验收完，不在验证中。

**触发条件**
1. 改要求落在某一步的结果"待验证"或"验证中"。
2. 规划器的新计划保留这一步，没有换掉装着它的做法。

两条同时成立即触发。F1 的确定性用例 `test_an_amendment_landing_while_a_check_is_imported_sets_that_verification_aside` 用的规划器换掉了根做法（REPLACE_METHOD），所有旧叶子都离开计划，所以没走到这条路。

**后果**
- 新计划一提交，`requirements_changed` 就变回 False。调度处重新验证这份 PENDING/RUNNING 结果，走到 `_verify` → `router.verify` → 本地检查 `prepare`（`assurance_local_checks.py` 约 :551）→ `load_completion_result_inputs` → `read_scope(旧计划版本)`。
- 现行计划版本已经不同，于是抛 `OperationCompletionError("OP_EFFECT_SCOPE_STALE", "the adopted plan differs")`（`operation_completion.py` 约 :349-354）。它属于 `ContractError`，不是 `AssuranceError`。
- `_verify` 只接 `AssuranceError`，这个错误冲出验证任务；随后 `_raise_if_verification_crashed` 把它抛出 `run()`（约 :2954-2962）。**整个编排主循环停下，所有任务都停。**
- 这和 F1 随机序列发现的是同一类崩溃。F1 之前在改要求窗口里当场崩，F1 之后推迟到新计划提交后再崩。所以 F1 记录里"主循环不崩、新计划把旧尝试归档"在这种情形下不成立。

**底层原因（说明性质）**：尝试冻结的完成输入钉的是派发时的计划版本（`completion_inputs.py` 约 :381-384），换计划版本后不会重新冻结。所以"不改要求的普通局部修复 + 兄弟步骤恰在验证中"也会走到同一个报错。这一半是 F1 之前就有的缺陷，不是 F1 引入的。

**建议修法（与收集处同一条规则，一处判断）**
1. 窗口里不要"跳过、等新计划"。调度处看到 `requirements_changed` 时，对 PENDING/RUNNING 结果直接把它的尝试按"被取代"关掉，走 `CommitService._close_attempt` 同一条路：结果改为 REJECTED/superseded，意图结清；有未结账的调用就不结清预留，照原逻辑处理。这和收集时 `reject_result(reason="superseded")` 是同一条规则。
2. `_verify` 的放下分支同样改为关掉尝试，不再 `return False` 留着 RUNNING。新计划若保留这一步，派发处会按新计划给它开新尝试。
3. 补一条确定性用例：改要求落在 B 验证中，规划器只对 C 做 PROPOSE_SUCCESSOR、保留 B；断言主循环不崩、B 的旧结果为"被取代"、任务按第 2 版完成。配一条改坏：恢复成"跳过等待"后变红。
4. "普通局部修复时兄弟步骤结果钉着旧计划版本"那一半另开单，交 TaskGraph 补全或 G 处理：结果冻结的计划版本不是现行版本时，统一归档为被取代，或明确规定"换计划版本时重新冻结"。不在 F1 顺手做。

---

## 其余重点逐项结论（无阻断）

1. **空转与吞错**：窗口里跳过不算进展，也不进 `_verifying`，不会空转反复重验。放下分支只在"错误码是 `CHECK_SCOPE_CHANGED` 且要求确实已改"时才不抛；其他保证通道错误照旧抛出，没有吞掉该冒出来的错误。问题只在 B1 所说的"之后谁来归档"。
2. **`assurance_check_import.py` 只映射 `OP_EFFECT_SCOPE_STALE`**：正确。这个码的各个来源（要求已改、计划不同、Spec 不同、合同不同、计划回执不同）语义都是"范围变了"；`OP_COMPLETION_SCOPE_UNRESOLVED`、`OP_REQUIREMENT_MAPPING_*` 等完整性错误照旧抛出。
   - 下游影响只有好处：受理路径 `_prepare_accept_use`（`commit_service.py` 约 :2758-2763）原来会让 `ContractError` 冲出，现在变成 `ResolutionCommitRejected`，判定被丢弃、不崩。
   - 保证通道的周期处理（`assurance_tick.py` 约 :300）两类错误都接，行为不变。
3. **偏差单 2**
   - 闸门：`unreviewed_adopted_methods` 只是把原循环体搬进 `adoption_refusal`，判据、顺序、行字段都没变，与改前等价。
   - 候选过滤：种子集合与计划提交处读的是同一个 `require_planning_world().seed_methods`。人判通过的经 `review_of` 也返回 PASSED。候选的内容哈希来自注册表 `MethodRef`，不会为空，构造 `MethodRef` 不会抛。能采用的做法不会被挡掉。
   - 提示词：只改了规划器模板一段文字，版本升 v23，与新字段 `not_adoptable`、`views.methods[].review` 对得上。没有碰 `TOOL_SCHEMAS`，也没有碰已发布的迁移文本。
4. **三个新注入点**：`Store.fault` 在没装注入时只遍历空集合，零副作用。
   - K06 在 `commit_goal_resolution` 的事务之前。
   - K08 在交接已提交、外部调用之前。
   - K09 在调用返回、记录结果之前。
   - 三处都不在事务中间，不会把事务打断成两半。
5. **部署清单**：逐个核对 686 个源文件的哈希，全部一致；没有漏列的 `.py`。按生成规则（去掉 `deployment_id` 后的规范 JSON 取 sha256）重算 `deployment_id`，结果一致。

---

## 非阻断但值得记（3 条）

1. **跨线程改要求的极窄竞态**：如果宿主在另一线程或进程提交改要求，并且恰好落在"调度处检查"与 `prepare` 之间，`prepare` 读完成范围会因"要求已变"抛 `OperationCompletionError`。这个错误没被映射，照样冲出主循环。同一事件循环里两处之间没有让出点，不会发生。按 B1 的修法在调度处直接关掉尝试后，这个窗口只剩"检查已开始"一侧；建议放下分支同时认 `OperationCompletionError(OP_EFFECT_SCOPE_STALE)`，且仅在要求已改时才认。
2. **`views.methods` 的优先顺序只看可采用候选**（`planner_views.py` 约 :95 的 `first=`）：被列进 `not_adoptable` 的做法超出条数上限时，可能在 `views.methods` 里被省略。提示词说"原因看 views.methods 该条的 review"，届时找不到；不过 `not_adoptable` 行本身已带审阅结论，不影响判断。
3. **放下后尝试一直停在 VERIFYING，租约不续**：按 B1 改成关掉尝试后，这个问题也就没有了；不改的话，要确认重启恢复时对这种尝试的处理与预期一致。
