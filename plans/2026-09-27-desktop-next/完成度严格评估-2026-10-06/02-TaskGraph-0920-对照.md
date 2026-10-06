# TaskGraph 原始计划（2026-09-20 代码级实施计划）逐条对照 — 严格评估

- 原始计划：`plans/TaskGraph/v1/simpleharness-taskgraph-code-execution-plan.zh-CN.md`（TG-EXEC-2.0，全文 2603 行已读完）及同目录资料包 `simpleharness-taskgraph-code-plan-kit.zip`（`implementation/seams.json` 24 行、`mutations.json` 12 条、`schemas/` 8 份、`sql/001_taskgraph_extension.sql`，解到草稿目录只读核对）。
- 代码版本：Host main `b4f1d314`（SDK opt.162）。只读，没有跑任何测试；只用了 grep、读文件和一次"把 `taskgraph_schema.DDL` 与资料包 SQL 逐字节比对"的零副作用 python。
- 路径简写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`Host/` = `backend/deskpet/orchestration/`；`FE/` = `tauri-app/src/`；`a4证据/` = `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness-a4/.local-test-evidence/`。
- C 级口径：代码与原始计划不一致，且没有任何文档把它**作为对原计划的偏离**登记或裁决过（只在进度日志里当缺陷修复顺带一提的，也算 C）。

## 结论摘要

1. 逐条对照共 **168 条**：按计划在用 **109**、做法不同 **34**、部分 **14**、做了没接上 **1**、只在测试 **0**、未做 **3**、已删 **4**、A 级排除 **3**（"按计划在用"里有不少条带"另有偏离见 B/C"的备注，以第二、三节为准）。
2. 主干（版本化不可变结构、钉、精确输入冻结、同事务原子提交、交接围栏、收敛状态机、通知、只读接口、离线重建）都在产品默认路径上，九张表的建表语句与资料包 SQL **逐字节相同**。
3. **C 级无记录偏离 12 条**（第二节）。最严重的三条：
   - **C7**：`SDK/orchestrator/event_handler.py:7157` 按异常文字前缀（`TASKGRAPH_SHARED_` 等）决定"退回规划器"还是"原地重试"——正是原计划 §12 明令禁止的"按异常文本分类"，也违背补全第一批"决定秩序的码必须进错误码表"的定稿口径（联测 opt.154 当缺陷修复加进来，从未登记为偏离）。
   - **C1**：格式兼容声明带转换器时，`SDK/artifacts/input_bindings.py:640-648` 直接拿原字节绑定、只记一个 `converter_ref`，不执行转换、也不拒绝——原计划 §5.5 原文禁止"拿原字节加新 schema 标签"。桌面登记表为空，产品目前走不到，但代码语义是错的。
   - **C3/C4**：附录 E 的 8 份 JSON Schema 根本没进 SDK，视图、解释、收敛视图三种返回只是字典、没有严格编解码；收敛视图还多了一个原 Schema（不许多余字段）没有的 `blocked_notifications`。
4. B 级（子代理裁决或"同意开工"间接认可的偏离）共 **43 条**：补全方案第七节 33 条 + HTN 侧影响 TaskGraph 的 10 条（第三节）。其中 6 条在别的文档里有"用户拍板"的转述，建议用户确认后升 A。
5. 收尾没完成的硬事实：部署验收门里 `taskgraph_acceptance` 仍是 `NOT_RUN`（`SDK/orchestrator/taskgraph_deployment_manifest.json`），而 `enable_taskgraph_contract` 照样放行生产开关（`SDK/orchestrator/taskgraph_deployment.py:77`）；真实模型"分支共用并换做法"一局没观察到点名共用；12 个改坏最后一次全量执行在 opt.152/153 代码上，opt.154～162 之后没重跑。

---

## 第一节：逐条对照大表

### §0 范围、前提与裁定

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| 1. 把 HTN 编译结果接到"版本化网络→精确输入→原子提交→派发→独立验收→局部修复与恢复" | §0.1 | 按计划在用 | `SDK/orchestrator/plan_commits.py:295-380`（`commit_plan_revision` 内调 `taskgraph.prepare/record_applied`）；`SDK/orchestrator/taskgraph_dispatch.py:280-367`；`SDK/orchestrator/composition_review.py` | 产品入口：`Host/service.py:216-221` → `SDK/deployment/assembly.py:118-127` 建任务即绑定 → 主循环 `event_handler` |
| 2. 不另建 Goal 服务 / Agent 生命周期 / 数据库，Task/Obligation/MethodInstance/Occurrence/ORDER/DATA/InputManifest/PlanRevision/Acceptance 原样保留 | §0.1 | 按计划在用 | `SDK/storage/htn_schema.py`（原表）；九张新表只存钉、关联、收敛、通知 | 无 |
| 3. 三项前置门：H1H 三链真实实现 + H1 完整门禁通过才开生产开关 | §0.3-1/2 | 做法不同 | `SDK/orchestrator/taskgraph_deployment.py:77`（`taskgraph_acceptance` 取 `NOT_RUN` 也放行）；清单 `taskgraph_deployment_manifest.json` 现值 `NOT_RUN` | 用"源码字节清单 + 上游真机局重放一致"代替门禁通过；替代它的"整体验收"本身也没完成（B9） |
| 4. `BIND_EXISTING_GOAL`、`REPAIR/PROPOSE_SUCCESSOR` 只解码不执行 | §0.3 | 做法不同 | 后继步骤可执行 `SDK/planning/htn/graph_repair.py:293`（`compile_successor`）；`BIND_EXISTING_GOAL` 全库 0 处 | 后继步骤已开放（B6）；绑定已有目标整条删除（B17） |
| 5. NanoJev 保持影子、真实模型只用 GPT-5.6 | §0.3 | A 级排除 | — | A#1（NanoJev 不做）、A#2（只用 DeepSeek） |
| 6. 不自动改基（rebase），旧回复失效后新请求 | §0.4 | 按计划在用 | `SDK/orchestrator/plan_commits.py:638`（`_check_plan_revision`）；`SDK/orchestrator/taskgraph_plan_commit.py:58-60`（`TASKGRAPH_COMMIT_BASE_STALE`） | 无 |
| 7. 历史结构（不可变文档）与当前状态（读上下文）分开 | §0.4 | 按计划在用 | `SDK/orchestrator/taskgraph_sources.py:27-96`；`SDK/storage/taskgraph_store.py:269-340` | 无 |
| 8. 只加九张扩展表，不建第二套状态/预算表 | §0.4、§6.1 | 按计划在用 | `SDK/storage/taskgraph_schema.py` 的 `DDL` 与资料包 `sql/001_taskgraph_extension.sql` 逐字节相同（18626 字节）；迁移 25 注册 | 迁移 29 曾加 `taskgraph_requirements`、迁移 35 删掉，现已无第十张 |
| 9. 未细化复合目标是显式 pending，`NOT_CHECKED` 不等于部分展开 | §0.4 | 按计划在用 | `SDK/graph/taskgraph_validation.py:15-56`；`SDK/planning/htn/validation.py`（改坏 M10） | 无 |
| 10. CANCELLED 不足以放行，要真实结清 | §0.4 | 按计划在用 | `SDK/orchestrator/taskgraph_settlement.py:25-97`；`SDK/graph/eligibility.py`（改坏 M09） | 无 |
| 11. 换方法不重发旧动作，全任务操作闸门 | §0.4 | 按计划在用 | `SDK/runtime/planning_operations.py`（`build_operation_snapshot`）；`SDK/orchestrator/action_commits.py:917-922` | 无 |

### §1 两处静态观察

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| 12. 历史结构不能按"最新语义 + 当前做法状态"拼 | §1 | 按计划在用 | `SDK/storage/taskgraph_store.py:269-340`（按钉读、祖先哈希链逐级核）；改坏 M03 | 10-03 加了按行指纹的缓存（只为性能） |
| 13. 去掉 `result.manifest is None → InputManifest()` 的回退 | §1 | 按计划在用 | `SDK/orchestrator/hierarchical_dispatch.py:2509`、`:3574`；改坏 M05 | 无 |

### §2 十六条不变量

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| 14. I01 单一逻辑提交，模型/UI/图算法不直接写业务表 | §2 | 按计划在用 | `SDK/orchestrator/plan_commits.py:316-380` 单事务；Host 只读路由 `Host/taskgraph.py:23-95` | 界面仅两个操作员动作（放弃收敛、重发通知），走 SDK 带命令号的写入口 |
| 15. I02 每任务一个 ACTIVE 修订、历史不可改 | §2 | 按计划在用 | `SDK/storage/htn_schema.py:127-128`（唯一索引）；九表不可改删触发器（`taskgraph_schema.DDL`） | 无 |
| 16. I03 每个目标最多一个采用方法 | §2 | 按计划在用 | `taskgraph_one_adopted_per_goal_idx`（DDL） | 无 |
| 17. I04 方法内必需槽都满足；compound 不建 Worker 尝试 | §2 | 按计划在用 | `SDK/graph/eligibility.py`（改坏 M12）；`SDK/orchestrator/hierarchical_dispatch.py` 复合阶段 | 无 |
| 18. I05 ORDER 不传文件，DATA 只绑声明端口 | §2 | 做法不同 | `SDK/orchestrator/hierarchical_dispatch.py:3671-4015`（`_is_continuation`：接续步骤顺带交出它收到的文件）；改坏 M04 | B1 |
| 19. I06 发给 Worker 的输入全部冻结，恢复不取最新 | §2 | 按计划在用 | `SDK/storage/taskgraph_attempt_inputs.py:109-256`；`SDK/orchestrator/taskgraph_dispatch.py:77-131` | 库层守卫被迁移 30 放宽一处（见 C5） |
| 20. I07 身份完整匹配，不凭文本/前缀授权 | §2 | 按计划在用 | `SDK/orchestrator/taskgraph_dispatch.py:77-131`；`SDK/graph/taskgraph_sharing.py:52-219` | 无 |
| 21. I08 历史完成不回退，当前有效性另算 | §2 | 按计划在用 | `SDK/orchestrator/carried_review.py:54`（改要求后旧验收不算数但不回退）；`acceptance_id_for(任务, 结果, 要求版本)` | 无 |
| 22. I09 新工作延续义务，不重置累计费用/失败/燃料 | §2 | 部分 | `SDK/orchestrator/obligation_accounts.py:24`（尝试/失败/用量按义务汇总） | "按义务扣燃料"已删（HTN 计划"本计划新增的偏离"，R08），燃料这一维不存在（B-H3） |
| 23. I10 共享生产者按实际需求判断，不沿退休父节点盲取消 | §2 | 按计划在用 | `SDK/graph/convergence.py:62-148`；`SDK/planning/htn/compiler.py:1141`（`_merge`）、`:1315`；改坏 M07、M07b；产品用例 `T/product_world/test_shared_steps.py` | 真实模型局没用过点名共用（第五节） |
| 24. I11 预览无写入，不先取消旧工作再检查 | §2 | 按计划在用 | `SDK/orchestrator/taskgraph_preview.py`（预览纯计算，收敛另走 `ensure_convergence`） | 无 |
| 25. I12 运行/操作数据读不到不等于空 | §2 | 按计划在用 | `SDK/runtime/planning_operations.py`（改坏 M02） | 无 |
| 26. I13 同命令同内容回原回执，异内容冲突 | §2 | 按计划在用 | `SDK/orchestrator/plan_commits.py:328-330`（`_replayed_receipt`）；`SDK/orchestrator/taskgraph_policy.py:118-124` | 无 |
| 27. I14 提交时重查权限/版本/集合/费用/在途 | §2 | 按计划在用 | `SDK/orchestrator/plan_commits.py:350-368`；`SDK/orchestrator/planning_admission_commits.py:142`（`_check_planning_commit`） | 无 |
| 28. I15 未受影响在途尝试可继续，受影响旧代次不得新交接，晚到结果归档计费 | §2 | 按计划在用 | `SDK/orchestrator/taskgraph_dispatch.py:67-131`；改坏 M08 | 无 |
| 29. I16 图重建不调模型/工具、不恢复租约或授权 | §2 | 按计划在用 | `SDK/observability/taskgraph_replay.py:157-269`（报告固定 `RUNTIME_RESUME_NOT_AUTHORIZED`） | 无 |

### §3 类型与来源

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| 30. `CompleteRead`/`GraphReadToken`/`PlanEffectSet` 等 frozen/slots/kw_only，构造时校验 bool≠int、64 位哈希、重复键、边界 | §3.1 | 按计划在用 | `SDK/graph/execution_contracts.py:274-333` | 无 |
| 31. 已有同名 `SourceUnavailable` 直接导入，不再定义第二份 | §3.1 | 按计划在用 | `SDK/graph/execution_contracts.py:14` | 无 |
| 32. 三种按用途分开的读上下文（结构/执行/改计划） | §3.1、附录C §1.2 | 做法不同 | `StructuralReadContext` `SDK/orchestrator/taskgraph_sources.py:27`；`ExecutionReadContext` `SDK/orchestrator/taskgraph_plan_sources.py:71`（字段是 local/running_work/execution_policy/operation_snapshot，与附录C §1.2 列的 16 个字段不同）；"改计划上下文"由 `PlanMutationSources`（`taskgraph_preview.py`）承担；另有第四种 `SeedStructuralReadContext` | B3 |
| 33. `PlanEffectSet` 驱动"保留/重验/退休/新物化/共享保留"的局部效果 | §3.1、§5.6 | 部分 | `SDK/graph/convergence.py:151-173`（`coverage` 恒为 `CONSERVATIVE`，`revalidate`=全部保留节点）；唯一用处是事件里的 `effect_set_hash`（`SDK/graph/revision_events.py:19-32`） | 效果集只进事件哈希，没有消费者按它重验；精确影响由"屏障+使用前重验"代替（B5） |
| 34. 来源 P01 调用方（固定身份 + 租户守卫，不从载荷取） | §3.2、seams P01 | 按计划在用 | `SDK/api/facade.py` `MissionControlV1._mission`；`SDK/api/taskgraph.py:99`；`Host/taskgraph.py:49`（不从 IPC 体取 tenant/principal） | 无 |
| 35. 来源 P02 规划请求原件 | seams P02 | 按计划在用 | `SDK/orchestrator/taskgraph_plan_sources.py:198-222`（包哈希、基准版本、要求版本逐项核） | 函数改名 `get_planning_request`（改名表，B-H10） |
| 36. 来源 P03 规划授权（含"启用前须有有效委派"） | seams P03、§6.5 | 做法不同 | `SDK/orchestrator/taskgraph_plan_sources.py:228-233`（每次提交仍核授权）；`SDK/orchestrator/taskgraph_policy_sources.py:85-93`（启用时不查委派） | 建任务即绑定，不再要求先有规划授权（B-H1） |
| 37. 来源 P04 计划结构 `read_revision` | seams P04 | 按计划在用 | `SDK/storage/taskgraph_store.py:269-340` | 无 |
| 38. 来源 P05 做法定义与登记 | seams P05 | 按计划在用 | `SDK/orchestrator/taskgraph_plan_sources.py:247-255` | 无 |
| 39. 来源 P06 证据（用途/消费者/支持/纪元都匹配） | seams P06 | 按计划在用 | `SDK/orchestrator/hierarchical_dispatch.py` `witnesses/input_witness_index/start_witness_index`；`SDK/artifacts/input_bindings.py:77-99`（目的、消费者、支持、纪元各一种拒绝） | 无 |
| 40. 来源 P07 能力（声明≠健康；需健康证据而无 reader 时报不可用） | seams P07、附录C §1.1 | 做法不同 | `SDK/planning/htn/world.py` `DeploymentPlanningWorld.capabilities` | "连得上/兼容"不预探，调用失败如实交规划器（B-H7） |
| 41. 来源 P08 操作快照（含已退役工作，读失败≠空） | seams P08 | 按计划在用 | `SDK/runtime/planning_operations.py`；`SDK/runtime/taskgraph_operation_sources.py`；改坏 M02 | 无 |
| 42. 来源 P09 在跑工作 | seams P09 | 按计划在用 | `SDK/runtime/planning_operations.py` `read_running_work`；`SDK/runtime/taskgraph_local_work.py` | 无 |
| 43. 来源 P10 已验收产出 | seams P10 | 按计划在用 | `SDK/orchestrator/hierarchical_dispatch.py` `accepted_outputs`（只取现行算数的验收，改坏 TG5-01） | 无 |
| 44. 来源 P11 ORDER 结局，`settlement_facts` 四分支联合 | seams P11、§3.3 | 做法不同 | `SDK/orchestrator/taskgraph_settlement.py:20-100` 只返回"已结清"字典，其余靠 `OccurrenceOutcome` 区分 | 没有 `AcceptedOccurrence/InFlightOccurrence/OutcomeUnknown` 联合类型，拆到两个模块（B3、B-H10） |
| 45. 来源 P12 格式兼容（只认已装声明，不隐式转换） | seams P12、§5.5 | 部分 | `SDK/orchestrator/taskgraph_policy_sources.py:47`（登记表冻结进策略）；`SDK/artifacts/input_bindings.py:626-658` | 无声明时正确拒绝；有转换器声明时拿原字节绑定（C1） |
| 46. 来源 P13 输入解析 | seams P13 | 按计划在用 | `SDK/artifacts/input_bindings.py:837`（`resolve_declared_inputs`） | 来源表本行"差异"文字还写着已删的 `_pinned_revisions`（第六节） |
| 47. 来源 P14 写入目标（无显式规则即 `TARGET_RULES_UNAVAILABLE`） | seams P14、附录C §1.1 | 做法不同 | `SDK/orchestrator/hierarchical_dispatch.py:675-686`（默认 `TASK_WORKSPACE_V1`）；读不到才报 `SDK/orchestrator/taskgraph_dispatch.py:302` | B23 |
| 48. 来源 P15 需求方 `read_active_consumers` | seams P15、§6.3 | 做法不同 | 接口已删（全库 0 处）；改读修订钉里的 demand_refs（`SDK/orchestrator/taskgraph_execution_sources.py`）与 `SDK/orchestrator/taskgraph_demands.py` | B22 |
| 49. 来源 P16 预算 | seams P16 | 做法不同 | `SDK/governance/budgets.py` `BudgetLedger.account`；`SDK/storage/obligation_store.py` | 金额维度删、只记用量（A#1"金额计价已删"） |
| 50. 来源 P17 预览 | seams P17 | 按计划在用 | `SDK/planning/plan_preview.py`；`SDK/orchestrator/taskgraph_preview.py` | 无 |
| 51. 来源 P18 输入冻结 | seams P18 | 按计划在用 | `SDK/storage/taskgraph_attempt_inputs.py:109`；`SDK/orchestrator/taskgraph_dispatch.py:348-367` | 挪到独立模块（B-H10） |
| 52. 来源 P19 提交守卫 | seams P19 | 按计划在用 | `SDK/orchestrator/planning_admission_commits.py:142,259` | 改名 `commit_planning_revision`（B-H10） |
| 53. 来源 P20 收敛推进 | seams P20 | 按计划在用 | `SDK/orchestrator/taskgraph_convergence.py`；`SDK/storage/taskgraph_convergence.py:230-248` | 另有系统解除围栏两条路（B-H4） |
| 54. 来源 P21 组合验收 | seams P21 | 按计划在用 | `SDK/orchestrator/resolution_commits.py` `commit_goal_resolution`；`SDK/orchestrator/composition_review.py` | 无 |
| 55. 来源 P22 公共只读视图（同一读事务） | seams P22、§11 | 做法不同 | `SDK/api/taskgraph.py:184-330`（`snapshot`）；界面只读分页的 `execution_snapshot`（`:335-386`） | B11；收敛视图多字段（C4） |
| 56. 来源 P23 图重建 `rebuild_projection` | seams P23、§10.3 | 做法不同 | `SDK/observability/taskgraph_replay.py:157`（改名 `replay_taskgraph`）；产品入口 `Host/diagnostics.py:404-430` | 起点只剩种子修订，"捕获基线"已删（B-H2） |
| 57. 来源 P24 通知消费 | seams P24 | 按计划在用 | `SDK/orchestrator/taskgraph_followups.py:69-118`；`SDK/orchestrator/taskgraph_notifications.py:233-249`（主循环每轮 `tick`） | 无 |
| 58. 规划授权只管改计划，执行看自己的当前执行授权 | §3.2 关键分工 | 按计划在用 | 执行授权走 `ExecutionImports.read_execution_policy`（`SDK/orchestrator/taskgraph_plan_sources.py:59-67`）与交接时的当前授权检查 | 无 |
| 59. `read_structure(mission, revision, caller)` | §3.3 | 按计划在用 | `SDK/orchestrator/taskgraph_sources.py:64-96` | 历史读不依赖现行规划授权 |
| 60. `read_execution_context(mission, caller)` | §3.3 | 做法不同 | `SDK/orchestrator/taskgraph_plan_sources.py:170-187`（`read_execution`） | 类方法、字段不同（B3） |
| 61. `read_plan_context`：一次一致读事务内按固定顺序读全部来源，再生成来源回执 | §3.3 | 做法不同 | `SDK/orchestrator/taskgraph_plan_sources.py:198-347`（`__call__` 在 `store.read_view()` 内，`read_view` 是真实 `BEGIN`，`SDK/storage/store.py:483-512`） | 改为类的 `__call__`，产出 `PlanMutationSources`（B3） |
| 62. `build_plan_effects(before, after, changed_supports, demands_after, coverage)` | §3.3、§5.6 | 部分 | `SDK/graph/convergence.py:151`（`compute_plan_effects(before, candidate)`，不吃支持变化与需求） | 无 DATA/支持反向传播（B5） |
| 63. `build_dispatch_inputs` 返回 完整/等待/无效/来源不可用 四分支 | §3.3 | 做法不同 | 解析结果用 `ResolutionProblemKind`（`SDK/artifacts/input_bindings.py:77-99`）+ `TaskGraphDispatchBinding.prepare`（`taskgraph_dispatch.py:280-326`） | 没有同名函数和联合类型，功能散在两处（B3） |
| 64. `settlement_facts` 四分支 | §3.3 | 做法不同 | 同第 44 条 | 同第 44 条 |
| 65. 外部执行事实走可靠导入，不 ATTACH 执行库 | §3.3 | 按计划在用 | `SDK/orchestrator/taskgraph_plan_sources.py:44-67,170-187`（`ExecutionImports`，导入不全报 `taskgraph_execution_import_invalid`） | 无 |

### §4 内部协议

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| 66. `NetworkDocumentV1` 字段与 7 种对象 kind | §4.1 | 按计划在用 | `SDK/graph/network_codec.py:35-60,226-270` | 无 |
| 67. 对象存原 `to_json` 规范字节 + 哈希，原 codec 解码，重复身份先拒，顶层排序、方法内顺序保留，文档上限 16 MiB | §4.1、附录C §3.1、§8 | 按计划在用 | `SDK/graph/network_codec.py:35,195,254-256,379-393,414-516` | 无 |
| 68. `codec_manifest_hash` 绑版本化清单，升级不覆盖旧清单 | §4.1、附录C §3.1-5 | 做法不同 | 清单文件 v1～v5 在 `SDK/graph/`，但 `network_codec.py:141` 只认 v5、v5 已多次原地重写 | B4（命中 A#17 开发期不兼容） |
| 69. `PreviewBindingV1`：12 通道各一次、全部 COMPLETE、系统生成 | §4.2 | 按计划在用 | `SDK/graph/execution_contracts.py:18-31,141-262`；生成 `SDK/orchestrator/taskgraph_plan_sources.py:342-347` | 无 |
| 70. `TaskGraphViewV1`（读令牌、节点、边、两个前沿、根结论引用、`complete=true`、`next_cursor=null`） | §4.3、附录E | 部分 | `SDK/api/taskgraph.py:318-330` 组字典，字段与 Schema 一致 | 没有严格编解码，也没有对 Schema 的一致性测试（C3） |
| 71. `FollowupV1` 只三类，绑原事件/任务/对象/修订/原因 | §4.3 | 按计划在用 | `SDK/graph/notification_contracts.py:53-151` | 无 |
| 72. `ErrorV1` origin/stage/code/detail/retry_kind/source_identity | §4.3 | 按计划在用 | `SDK/graph/notification_contracts.py:152-224` | 无 |
| 73. `snapshot_hash` = 去掉本字段后的完整视图哈希，`manifest_hash` 是结构文档哈希 | §4.3 | 按计划在用 | `SDK/api/taskgraph.py:318-329` | 无 |
| 74. Diff：before/after 不同时为空、相等不列入，由严格 codec 校验 | §4.3 | 按计划在用 | `SDK/graph/structural_diff.py:74-120` | 无 |
| 75. Explanation / ConvergenceView 严格合同 | §4.3、附录E | 部分 | `SDK/api/taskgraph.py:427-448`（解释）、`:465-494`（收敛） | 只是字典无 codec（C3）；收敛视图多 `blocked_notifications`（C4） |
| 76. 修订证书是"普通提交 / 捕获基线"两种的受限联合 | §4.3 | 做法不同 | `SDK/graph/revision_records.py:73-111` 只剩 `PLAN_ADMISSION` | 捕获基线删（B-H2） |
| 77. 先冻结文档→求哈希→写事件→写记录；命令身份用原 `intent_hash()` | §4.4 | 按计划在用 | `SDK/orchestrator/taskgraph_plan_commit.py:114-157` | 无 |
| 78. 附录 E 八份 Draft 2020-12 Schema 作为边界合同随实现交付，并与 Python 边界 codec 对同一正负样本一致 | §4、附录E | 未做 | SDK 里找不到任何 `*schema.json`（`graph/` 只有编码清单）；`T/` 里没有用这 8 份 Schema 的测试（搜了 `schema.json`、`jsonschema`、`taskgraph-view-v1`） | C3 |

### §5 网络语义与算法

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| 79. 投影：固定根、compound 入口出口不计费、ORDER/DATA 成边、悬空端点报错、无环只是结构检查 | §5.1 | 按计划在用 | `SDK/graph/task_network.py:636`；`SDK/graph/projection_validation.py` | 无 |
| 80. 未展开 compound 只进规划前沿；已采用方法零必需孩子 → COVERAGE_GAP | §5.1 | 按计划在用 | `SDK/planning/plan_preview.py:193-194` | 无 |
| 81. 结构检查完整 vs 分解完整分开；pending 列表 | §5.2 | 按计划在用 | `SDK/graph/taskgraph_validation.py:15-56`；提交时比 pending 集合 `taskgraph_plan_commit.py:81-82` | 无 |
| 82. 槽身份与共享生产者身份分存，按 ChildBinding 核验 | §5.3 | 按计划在用 | `SDK/contracts/htn.py:1583-1600`（`occurrence_id` 与 `goal_occurrence_id` 分开）；`SDK/graph/revision_pins.py` | 无 |
| 83. 共享条件：合同、参数、输入版本、范围、新鲜度、语义一致才可共享；写型默认不共享 | §5.3 | 做法不同 | `SDK/planning/plan_preview.py:369`（`_named_reuse`）；`SDK/planning/htn/grounding.py:208,536`；`SDK/graph/taskgraph_sharing.py:52-134` | 只在规划器点名 `reuse` 时共享，系统只核秩序（B14、B16） |
| 84. 活跃共享不加"过去本应满足"的新前置，不成立则拒 SHARE_ACTIVE | §5.3 | 按计划在用 | `SDK/graph/taskgraph_sharing.py:149-219` | 无 |
| 85. 退休只撤该方法槽需求，`retired_targets` 公式，结果网络再查保留生产者有需求 | §5.3 | 按计划在用 | `SDK/graph/convergence.py:62-148`；`SDK/graph/taskgraph_sharing.py:63-73`；改坏 M07/M07b/TG3-05/TG3-08 | 无 |
| 86. 共享子目标 | §5.3 | 做法不同 | 点名子目标被 `grounding.plan_slots` 退回 | 只共用普通步骤（B15） |
| 87. ORDER 按 accepted / settled_terminal 放行，UNKNOWN、lease 过期、只 CANCELLED 都不放行 | §5.4 | 按计划在用 | `SDK/orchestrator/taskgraph_settlement.py:40-97`；改坏 M09 | 无 |
| 88. 输入：缺生产者 DATA_UNBOUND / 生产者未完成 WAITING_DATA / 读失败 SOURCE_UNAVAILABLE | §5.5 | 按计划在用 | `SDK/artifacts/input_bindings.py:77-99`；`SDK/graph/eligibility.py` | 无 |
| 89. 多版本无授权选择时歧义拒绝，不选最新 | §5.5 | 按计划在用 | `SDK/artifacts/input_bindings.py:80`（`AMBIGUOUS_SINGLE_PORT`）、`:972` | 无 |
| 90. PINNED 不自动升级，FOLLOW_AUTHORIZED_REVISION 只在新绑定/新尝试前解析 | §5.5 | 已删 | `SourceRevisionPolicy`、`_pinned_revisions` 全库 0 处；迁移 43 删列（`SDK/storage/schema.py:1419`） | 数据边一律跟随现行算数的验收（B20、B30） |
| 91. 需要转换器时必须有实际转换产物与验收，转换路径未部署就明确拒绝 | §5.5 | 做了没接上 | `SDK/artifacts/input_bindings.py:640-648`（声明带转换器即返回 `converter_ref`，原字节照用）；全 SDK 没有执行转换的代码；产品登记表恒空（`input_bindings.py:305` 默认空、无写方） | 产品走不到，但走到时违背计划（C1） |
| 92. SINGLE 一份；SET/LIST 按排序；MAP 按声明 key、重复 key 拒绝 | §5.5 | 部分 | `SDK/contracts/htn.py:213-217` 只有 `SINGLE`、`SET` | 没有 LIST、MAP（C2） |
| 93. 合法空清单只在来源完整、无必需端口、无待定要求时由解析器明确给出 | §5.5 | 按计划在用 | `SDK/orchestrator/hierarchical_dispatch.py:2509-2522`；改坏 M05 | 无 |
| 94. 局部影响：DATA 与支持边反向 BFS 传播 NEEDS_RECHECK，ORDER-only 不传播，覆盖不全保守扩大 | §5.6 | 做法不同 | `SDK/graph/convergence.py:151-173`（保留节点全体重验） | B5 |
| 95. 需求变化要有授权的要求修订，不让规划器减难准则 | §5.6 | 按计划在用 | `SDK/orchestrator/requirements_amendment.py`（只经主 Agent `mission_amend` 入口） | 无 |

### §6 数据模型与 SQL

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| 96. 复用原表不重建 | §6.1 | 按计划在用 | `SDK/storage/htn_schema.py`、`schema.py` | 无 |
| 97. 九张新表建表语句（列、主键、外键、CHECK、索引） | 附录A | 按计划在用 | `taskgraph_schema.DDL` 与资料包 SQL 逐字节相同 | 无 |
| 98. 触发器 `tg_revision_source_guard` | 附录A | 做法不同 | 迁移 34 重建，删捕获基线分支、库层拒绝该来源（`SDK/storage/schema.py:633-647`） | B-H2 |
| 99. 触发器 `tg_attempt_identity_guard`（清单绑定版本必须等于本次输入版本） | 附录A、附录C §4 | 做法不同 | 迁移 30 改成 `b.input_binding_revision<=NEW.input_binding_revision`（`SDK/storage/schema.py:567-596`），迁移 34 再删基线分支（`:648-670`） | 迁移 30 的放宽没有作为偏离登记（C5）；迁移 34 部分是 B-H2 |
| 100. 其余守卫触发器、不可改删触发器、收敛/通知状态转换触发器 | 附录A | 按计划在用 | 同 DDL，后续迁移未改 | 无 |
| 101. 写入者/读取者表（策略只由 `enable_taskgraph_contract` 写、`TaskGraphStore.policy` 读；需求索引由 `read_active_consumers` 读） | §6.2 | 做法不同 | 策略行由 `SDK/orchestrator/taskgraph_policy.py:147` 直接 `INSERT`，读由 `read_installed_graph_policy`（`:60`）；`read_active_consumers` 删 | 策略读写不经 Store 接口（C9）；需求方见 B22 |
| 102. `TaskGraphStore` 接口清单（policy、insert_policy、read_revision、insert_revision_record、list_member_pins、list_method_pins、read_active_consumers、insert/get_attempt_inputs、insert/cas_convergence、active_fences、append/claim/ack/retry_followup） | §6.3 | 做法不同 | 拆成四个类：`SDK/storage/taskgraph_store.py:83-340`、`taskgraph_attempt_inputs.py:36-256`、`taskgraph_convergence.py:69-358`、`taskgraph_followups.py:73-345` | `policy/insert_policy/list_member_pins/list_method_pins` 不存在（钉随 `read_revision` 一起返回）、`cas_convergence` 叫 `advance_state/_transition`；挪位部分有改名表（B-H10），缺的四个接口没登记（C9） |
| 103. `insert_*` 同键异哈希冲突，不用 `INSERT OR REPLACE` | §6.3 | 按计划在用 | 九表相关模块里 `INSERT OR REPLACE/IGNORE` 0 处 | 无 |
| 104. 程序核对全部哈希/同任务/合法转换；外键开启；只做增量迁移 | §6.4 | 按计划在用 | `SDK/storage/store.py:214`（`PRAGMA foreign_keys = ON`）；迁移只追加（`schema.py:1376-1419`） | 无 |
| 105. `taskgraph_attempt_inputs` 一次尝试一行，原清单内容一份 | §6.4、附录C §4 | 按计划在用 | `SDK/storage/taskgraph_attempt_inputs.py:109-200` | 无 |
| 106. `enable_taskgraph_contract`：租户守卫、非 legacy、planning-decision-v1、H1 部署验收、有效规划委派、未终结；只给固定 Host/内部 API | §6.5、附录C §2 | 部分 | `SDK/orchestrator/taskgraph_policy.py:108-170`；`SDK/orchestrator/taskgraph_policy_sources.py:85-93` | 不查规划委派（B-H1）；"H1 部署验收"接受 `NOT_RUN`（B9） |
| 107. 已有活跃任务迁入：停派发、收敛、一致快照、明确命令、捕获 `CAPTURED_BASELINE` | §6.5 | 已删 | `SDK/orchestrator/taskgraph_policy.py:137-138`（有计划修订即 `TASKGRAPH_MISSION_ALREADY_PLANNED`） | B-H2 |
| 108. 没有原始记录的历史版本返回 `HISTORICAL_STRUCTURE_UNAVAILABLE`，不用 latest 补 | §6.5、§12 | 按计划在用 | `SDK/storage/taskgraph_store.py:333` | 无 |
| 109. 默认旧路径不写这九表 | §6.5 | A 级排除 | 旧平面路径已删，`plan_commits.py:335-340` 未绑定直接拒 | A#3 |

### §7 预览与原子改图

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| 110. 只经原决定管线进入，无裸改边公网 API | §7.1 | 按计划在用 | `SDK/orchestrator/taskgraph_preview.py`；Host 只读路由与两个操作员动作（`Host/handlers.py:34-41`） | 规划决定新增可选 `reuse` 字段（`SDK/contracts/planning_decisions.py:1036-1082`），见 B-H9 |
| 111. `TaskGraphCandidate` = 原预览 + 效果集 + 文档草稿 + 钉/需求 + 来源回执，纯计算 | §7.1 | 按计划在用 | `SDK/orchestrator/taskgraph_candidate.py:95-112`；`taskgraph_preview.py` `freeze` | 无 |
| 112. 完整提交顺序同一写事务（回执→授权→读集→操作→候选→预算→写→APPLIED→事件→记录/钉/通知→收敛 APPLIED） | §7.2 | 按计划在用 | `SDK/orchestrator/plan_commits.py:316-380`；`SDK/orchestrator/taskgraph_plan_commit.py:56-157` | 无 |
| 113. 关联集合整体复核（不只比原来几个 ID） | §7.2 | 按计划在用 | `taskgraph_plan_commit.py:83`（`recheck_sources` 逐通道比读数）；改坏 M06 | 无 |
| 114. 无关改图不作废在途尝试，尝试按自己的合同/输入/代次比 | §7.3 | 按计划在用 | `SDK/orchestrator/taskgraph_dispatch.py:77-131` | 无 |
| 115. 回执丢失按原命令号取原回执，不重新铸命令 | §7.4 | 按计划在用 | `plan_commits.py:328-330`；`T/full_target/taskgraph_exec/test_process_recovery.py`；改坏 M11 | 无 |

### §8 派发、冻结、并发与结果

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| 116. 派发前短事务重读绑定/需求/ORDER/见证/输入/围栏/执行权限/预算；原 Attempt 路径与原预留 | §8.1 步 1-4 | 按计划在用 | `SDK/orchestrator/taskgraph_dispatch.py:280-326` | 无 |
| 117. 解析器 `problems=()` 才冻结；DispatchIntent 与 `taskgraph_attempt_inputs` 同事务；准入检查与修订记录同源 | §8.1 步 5-6 | 按计划在用 | `taskgraph_dispatch.py:348-367`；触发器 `tg_attempt_identity_guard` | 无 |
| 118. 物化：CAS 逐文件核哈希、只读清单路径、拒跨目录/大小写/符号链接 | §8.1 | 按计划在用 | `SDK/orchestrator/taskgraph_materialization.py:22-120` | 无 |
| 119. Allocator 只排序合法候选 | §8.1 | 按计划在用 | `SDK/scheduling/allocator.py` | NanoJev 部分见 A#1 |
| 120. 恢复用 `get_attempt_inputs` 逐项比，缺行阻断，不重新解析最新 | §8.2 | 按计划在用 | `SDK/storage/taskgraph_attempt_inputs.py:202`；`SDK/orchestrator/taskgraph_dispatch.py:328-346` | 无 |
| 121. 交接围栏接入模型调用、工具、外部动作交接 | §8.3 | 按计划在用 | `SDK/runtime/provider_budget_guard.py:193`；`SDK/runtime/tool_gateway.py:774-782`；`SDK/orchestrator/action_commits.py:917-922`；`SDK/orchestrator/commit_service.py:542-551` | 无 |
| 122. 审阅包按该尝试冻结的输入构造，不给审阅员改成 latest | §8.4 | 按计划在用 | `SDK/orchestrator/taskgraph_review.py:24` | 改要求后的沿用重审按（结果, 要求版本）另开入口（B29） |
| 123. 组合审阅由当前采用方法的必需槽验收构造，根完成走原收尾 | §8.4 | 按计划在用 | `SDK/orchestrator/composition_review.py`；`event_handler.py:1439-1475` | 无 |

### §9 在跑换方法

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| 124. 收敛状态机 FENCED/WAITING/READY/APPLIED/ABANDONED，READY 仍围，按行版本 CAS | §9.1、附录C §5 | 按计划在用 | `SDK/storage/taskgraph_convergence.py:213-358`；DDL `tg_convergence_transition` | 无 |
| 125. ABANDONED 只在证明无在途危险动作且操作者/系统规则批准时 | §9.1 | 做法不同 | 人点：`SDK/orchestrator/taskgraph_operator.py:91`；系统：`SDK/storage/taskgraph_convergence.py:308-327`（`release_for_decision`，决定提交被拒即结束围栏，不调 `require_safe_abandonment`），调用处 `event_handler.py:7406-7414`、`taskgraph_resume.py:16-31` | B21 写"只认用户点击"，与代码不符；系统那条路有 HTN 阶段 B 裁决（B-H4） |
| 126. `begin_convergence` 在独立受控事务重验请求/授权/候选/基准/来源，非法预览不建作业；先立围栏、不先改正式计划 | §9.2 | 按计划在用 | `SDK/orchestrator/taskgraph_preview.py:221-262`；`SDK/storage/taskgraph_convergence.py:92-211` | 无 |
| 127. 最终提交复查围栏/在途/操作，只按系统重算的退休目标换代，`_retired_children` 不作最终取消集合 | §9.3 | 按计划在用 | `plan_commits.py:363-367`；`taskgraph_plan_commit.py:97-111`（核 `job.targets == impact.targets`） | 无 |
| 128. 已生效外部效果不被遗忘，无 `mark_reconciled(True)` | §9.4 | 按计划在用 | `SDK/runtime/planning_operations.py`；全库无 `mark_reconciled` | 无 |
| 129. 补偿走原受控 Operation | §9.4 | 已删 | 补偿入口删除 | HTN 表一 13（B-H8） |

### §10 事件、通知、恢复与重建

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| 130. `TaskGraphRevisionRecorded` 载荷（schema_version、mission/revision/parent_hash、manifest_hash、sdk_snapshot_hash、command_id、decision_id、source_kind、effect_set_hash） | §10.1 | 按计划在用 | `SDK/graph/revision_events.py:13-32`；`taskgraph_plan_commit.py:135-141` | 无 |
| 131. 辅助事件 `TaskGraphContractEnabled`、`ConvergenceRequested/Advanced`、`DispatchBound` | §10.1 | 按计划在用 | `taskgraph_policy.py:157-163`；`taskgraph_preview.py:253-258`；`storage/taskgraph_convergence.py:342-352`；`taskgraph_dispatch.py:363` | 另有 `TaskGraphFollowupConsumed`、`TaskGraphSourceUnavailable`、`TaskGraphPreviewRevalidated` 等附加事件 |
| 132. 三种消费者：REEVALUATE 只唤醒、CONVERGE 调 `advance_convergence`、REQUEST_COMPOSITION 调组合审阅 | §10.2 | 按计划在用 | `SDK/orchestrator/taskgraph_notifications.py:156-194`；`event_handler.py:1439` | 无 |
| 133. 先有下游持久回执再 ACK；退避 1/2/4/8 秒、第 5 次 BLOCKED；租约 30 秒 | §10.2、附录C §6 | 按计划在用 | `SDK/storage/taskgraph_followups.py:149-302`；`SDK/orchestrator/taskgraph_followups.py:47` | 无 |
| 134. BLOCKED 只能在真实修复 + 明确操作者命令后由 Q13 重置，不自动循环 | §10.2、附录C §6 | 做法不同 | 人点重发 `taskgraph_operator.py:112`；系统自动：`SDK/storage/taskgraph_convergence.py:332-341`（作业结束时把被挡的 CONVERGE 通知改回 PENDING） | HTN 阶段 B（B-H5） |
| 135. 监听来源含 PlanRevisionCommitted、验收/结论、见证失效、尝试/意图终态、动作核对、预算/审批变化 | §10.2 | 做法不同 | 重查事件集 `taskgraph_notifications.py:23-38` 不含计划修订事件；但提交事务本身就追加一条 REEVALUATE（`taskgraph_plan_commit.py:150-153`） | 计划修订后的唤醒由提交参与方直接写，效果到位；B24 写"不加"与代码不符（第六节） |
| 136. 游标存原 scheduler_state、事件水位与派生通知同事务 | §10.2 | 按计划在用 | `SDK/orchestrator/taskgraph_followups.py:132-166` | 无 |
| 137. 离线重建：新目标库、哈希链、版本化 reducer、比对、报 `GRAPH_PROJECTION_VERIFIED/RUNTIME_RESUME_NOT_AUTHORIZED` | §10.3、附录C §7 | 按计划在用 | `SDK/observability/taskgraph_replay.py:85-269`；`Host/diagnostics.py:404-430` | 改名（B-H10） |
| 138. 捕获基线的覆盖边界报告 | §10.3 | 已删 | 重建只接受种子开头（`taskgraph_replay.py:190`） | B-H2 |

### §11 Host 接口与界面

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| 139. `TaskGraphReadApi` 四个方法（snapshot/why_not_ready/diff/convergence），复用租户守卫 | §11 | 按计划在用 | `SDK/api/taskgraph.py:99,184,427,450,465`；`Host/taskgraph.py:23-95` | 另加 `execution_snapshot`、`execution_detail` |
| 140. 唯一模式 FULL_BOUNDED_SNAPSHOT，超限 BOUND_REACHED，不开放 cursor/limit | §11 | 做法不同 | `snapshot` 本身合规（`:236-238`）；`execution_snapshot` 开放 cursor/limit（`:335-386`），界面只用它 | B11 |
| 141. 历史版本 `HISTORICAL_STRUCTURE`、前沿为空、`HISTORY_ONLY`、`HISTORICAL_VIEW_NON_EXECUTABLE` | §11、附录C §7 | 按计划在用 | `SDK/api/taskgraph.py:250-259` | 无 |
| 142. `why_not_ready` 不调 LLM、给原因与合法来源引用 | §11 | 按计划在用 | `SDK/api/taskgraph.py:427-448` | 无 |
| 143. `diff` 只比两份不可变记录 | §11 | 按计划在用 | `SDK/api/taskgraph.py:450-463` | 无 |
| 144. `convergence` 显示作业与未决对象，不自标已核对 | §11 | 按计划在用 | `SDK/api/taskgraph.py:465-494` | 多一个 Schema 外字段（C4） |
| 145. 界面的树与执行图读同一令牌，界面无业务写权限 | §11 | 做法不同 | `FE/views/liveGraph/LiveGraph.tsx:42-44`、`FE/views/missionStory/MissionStory.tsx:22-23` 只读 `execution_snapshot/execution_detail/why_not_ready`；`FE/views/PlanChangePanel.tsx:28,128,138` 读收敛、两个操作员动作 | 界面不读 `snapshot`；历史与差异只进诊断导出（B13） |
| 146. Host 路由 `taskgraph.snapshot` → SDK，走现有通道，不新建网关 | §11 | 按计划在用 | `Host/handlers.py:34-41`；`Host/service.py:1404-1410` | 无 |
| 147. 读失败返回 TaskGraphError，界面保留最后合法画面并标过期 | §11、附录C §7 | 按计划在用 | `Host/taskgraph.py:79-95`；`FE/views/liveGraph/LiveGraph.tsx:352-377` | 无 |

### §12 错误分类

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| 148. 九类、优先级顺序 | §12 | 按计划在用 | `SDK/contracts/error_table.py:24-37` | 无 |
| 149. 表中 12 行映射（SOURCE_UNAVAILABLE、GRAPH_INTEGRITY、ORDER/REFINEMENT_CYCLE、DATA_UNBOUND/WAITING_DATA、REQUEST_BINDING_STALE/VALIDITY_RECHECK_PENDING、DEFERRED、BUDGET_INSUFFICIENT/PLANNING_BOUND_REACHED、COMMAND_PAYLOAD_CONFLICT、HISTORICAL_STRUCTURE_UNAVAILABLE） | §12 | 按计划在用 | `SDK/contracts/error_table.py:40-48` 及规划拒绝码表；各码在 SDK 有生产方（grep 计数均 >0） | 无 |
| 150. 所有映射全集测试、未知新码 fail-closed | §12 | 部分 | `T/full_target/test_error_table.py:20-45` 只测枚举本身；`TaskGraphBoundaryCode` 除表与测试外无人引用；`SDK/api/taskgraph.py` 实际对外发 `BOUND_REACHED/INVALID_CURSOR/INVALID_REQUEST/INVALID_REVISION/NOT_ENABLED/NOT_FOUND/REVISION_NOT_FOUND/SNAPSHOT_CHANGED/SOURCE_CHANGED` 9 个未登记码 | C6 |
| 151. 不按异常文本包含做分类 | §12 | 部分 | 一轮故障已改按类型码（`SDK/orchestrator/failure_classes.py:162-168`）；但 `event_handler.py:7157` 按文字前缀分流、`requirements_amendment.py:104` 从异常文字切码、`Host/taskgraph.py:137` 从异常文字切码 | C7、C8 |
| 152. 内部守卫码也按类型归类 | §12 | 做法不同 | `SDK/contracts/error_table.py` 只收"决定秩序"的 8 个一轮故障码 | B25 |
| 153. WAIT/NO_CHANGE/DECLARE_BLOCKED 不进图编译与操作管线、不写 TaskGraphRevisionRecorded | §12 | 按计划在用 | `event_handler.py:6565-6566,6934-6981`（不改计划的分支） | 无 |

### §13～§16 施工、验收、完成关口

| 计划条目 | 出处 | 判定 | 代码证据 | 差距说明 |
|---|---|---|---|---|
| 154. NEW 文件清单（taskgraph_policy、execution_contracts、network_codec、taskgraph_schema、taskgraph_store、taskgraph_sources、taskgraph_convergence、taskgraph_followups、taskgraph_replay、api/taskgraph、planning/repair/impact、tests/.../taskgraph_exec） | §13 | 做法不同 | 均存在；`planning/repair/impact.py` 落在 `SDK/orchestrator/repair_impact.py`；`taskgraph_sources.py` 只剩结构读取 | 改名挪位见改名表（B-H10） |
| 155. allowlist 之外不改；不新增公开规划决定类型 | §13 | 做法不同 | `SDK/orchestrator/` 下另有约 30 个 `taskgraph_*.py` 新模块不在清单；新增只读决定 `READ_METHOD_LIBRARY`、细化决定加 `reuse` | 决定类型有登记（B-H9）；模块扩散没登记（C10） |
| 156. 42 组场景、每组按编号绑定、每条真实 SDK | §15、附录D | A 级排除 | `T/full_target/taskgraph_exec/README.md` 自述"只覆盖命名子用例，不是 42 组" | A#1：只保留六组代表 + 12 改坏、每场景 1 局 |
| 157. 六组代表（A#1 保留部分） | §15 | 按计划在用 | 联测记录 2.10 表；代表用例存在（`T/full_target/test_readiness_reasons.py`、`test_input_manifest_resolution.py`、`test_h1h_authority_matrix.py`、`product_world/test_shared_steps.py`、`test_process_recovery.py`、`product_world/test_repair_replace_method.py` 等） | 无 |
| 158. 真实内核：只 stub 模型与外部目标 | §15 | 按计划在用 | `SDK/testing/product_world.py`（脚本化模型，其余为产品部署） | 无 |
| 159. 安全层失败测试都要记"无新计划/无新意图/无外部调用/预算未误放" | §15 | 部分 | `T/full_target/safety_facts.py` 只被 `test_h1h_commit_guard.py:54`、`test_h1h_authority_matrix.py:307` 两处使用 | 其余安全层拒绝用例没有四件事实断言（C11） |
| 160. Hypothesis stateful：200 例 × 50 步、七种动作、每步对照完整重算、存种子与最小反例 | §15 | 做法不同 | `T/product_world/random_sequences.py:52-53,275-284`、`test_random_sequences.py:23-24`（默认 1 种子 50 步；联测 4 种子 × 500 步 = 2000 步，约为计划 1 万步的 1/5） | 固定种子不用 Hypothesis（B-H6） |
| 161. 12 个定点改坏，被对应场景的行为断言杀死 | §15、kit `mutations.json` | 按计划在用 | `T/acceptance_assets/mutations.json` M01～M12（另 M07b）；执行器 `sdk/simple-harness-sdk/scripts/acceptance/run_mutations.py`；结果 `a4证据/2026-10-05/mutations/results-002909.json`（12 条 KILLED）、`results-021950.json`（89 条全量）、`results-030829.json`（M11 重做） | 绑定的是函数级用例而非 42 组场景用例（42 组本身 A#1 不做）；最后一次在 opt.152/153 代码上跑，opt.154～162 没重跑 |
| 162. 真实模型四代表场景（有序数据链、分支共享并换方法、前提/输入失效重规划、回执丢失恢复） | §15 | 部分 | 联测记录 3.1、6.2：数据链通过；回执丢失通过；资料换版重规划修复后通过（`mission-6f7f…`）；分支共享换方法"完成但未观察到点名共用" | 模型与局数按 A#1/A#2；"分支共享并换方法"实质没覆盖 |
| 163. 规模：默认 256/2048/8192/64，等于边界与 +1，超限显式拒绝 | §15 | 按计划在用 | `SDK/graph/task_network.py:88-98`；`T/full_target/test_projection_integrity.py`（等于与 +1） | 无 |
| 164. 规模：记录实际延迟、内存与 SQL 次数 | §15、附录D S08 | 未做 | `test_projection_integrity.py` 无计时/内存/SQL 计数；联测记录 2.5 也没有 | C11 |
| 165. §15.1 命令与 focused 目录（`taskgraph_exec`、`h1h_admission`），已有红项与新红项分开 | §15.1 | 部分 | `T/full_target/taskgraph_exec/` 存在；`h1h_admission` 目录不存在（h1h 用例平铺在 `full_target/`）；联测记录 6.4/6.6 记 10 条老失败"另立待办" | 10 条已知红用例未清 |
| 166. 完成关口 PRODUCER_MAP_COMPLETE（24 项来源有真实路径/连接/完整性/复核测试） | §16 | 按计划在用 | `T/acceptance_assets/seams_current.json` 24 行；`T/acceptance_assets/test_acceptance_assets.py:69-77`（生产方可导入、用例存在） | 第 46 条所述文字过时 |
| 167. 完成关口 TASKGRAPH_SQL_PASS / CORE_PASS / REAL_MODEL / HOST_SEAM / INDEPENDENT_REVIEW | §16 | 部分 | 数据表负控 `T/product_world/test_table_guards.py`；升级迁移不测（B12）；`taskgraph_deployment_manifest.json` 的 `taskgraph_acceptance` 仍 `NOT_RUN`；独立核验只做自动化部分一轮（联测记录 5.3 自述） | 五个关口无一完整关闭 |
| 168. 交付目录（source-map/脏文件哈希、迁移号与校验和、42 组 nodeid 映射、各类报告、真实模型清单、Host 接口图、核验与处置） | §16 | 未做 | 没有成套交付目录；资料散在联测记录、`a4证据/`、`seams_current.json` | C11 |

（附录 B 的 Q01～Q15 读取/CAS 模板：Q04 随 `read_active_consumers` 删除，其余 14 条在 `taskgraph_store.py`、`taskgraph_convergence.py:213-224`、`taskgraph_followups.py:149-345`、`taskgraph_sources.py:84-85` 中逐条可见，已并入第 102、124、133、136 条判定，不单列。附录 C §1～§8 已分别并入第 32、40、47、67、105、106、124、133、137、141 条。）

---

## 第二节：C 级无记录偏离清单

| 编号 | 计划原文要点 | 代码现状 | 为什么算无记录 | 严重度 |
|---|---|---|---|---|
| C1 | §5.5"registry 声明需要 converter 时，实际转换必须形成新产物和验收；不能拿原字节加新 schema 标签。未部署转换执行路径就明确拒绝" | `SDK/artifacts/input_bindings.py:640-648`：声明匹配即返回 `rule.converter_ref`，原产物字节照常绑定；全 SDK 没有执行转换的代码。产品登记表恒空，所以产品目前走不到 | 10-04 现状对照把它判"在用"；补全方案、HTN 计划都没登记 | 中（潜伏；一旦有人登记声明就会把未转换的字节当新格式交给执行者） |
| C2 | §5.5"SET/LIST 按 PortOrdering/显式列表；MAP 按声明 key，重复 key 拒绝" | `SDK/contracts/htn.py:213-217` 只有 `SINGLE`、`SET` | 任何文档都没提 LIST/MAP | 低 |
| C3 | §4 / 附录E"本包 schemas/ 八份文件都是完整 Draft 2020-12……必须与原 Python 边界 codec 对同一正负样本保持一致" | SDK 内无这 8 份 Schema；视图、解释、收敛视图没有严格 codec，只是 `dict`（`SDK/api/taskgraph.py:318-330,445-448,493-494`）；无一致性测试 | 现状对照判"在用" | 中（对外合同只靠手写字典保证） |
| C4 | 附录E `taskgraph-convergence-view-v1` 顶层 `additionalProperties:false`，只许 5 个字段 | `SDK/api/taskgraph.py:493-494` 多返回 `blocked_notifications` | HTN 阶段 B 把它当功能加进来，没登记为对 Schema 的改动 | 低 |
| C5 | 附录A / 附录C §4：`tg_attempt_identity_guard` 要求 `b.input_binding_revision=NEW.input_binding_revision` | 迁移 30（`SDK/storage/schema.py:567-596`）改成 `<=`，并延续到迁移 34 | 只在 `PLAN-STATUS.md:502` 作为"换代后开工核对过严"的缺陷修复提到；对照文档写"迁移 34 改写两个守卫触发器"，漏了迁移 30 | 中（库层精确绑定被放宽；精确版本改由程序层保证） |
| C6 | §12"所有 enum 映射必须全集测试；未知新 enum fail-closed"；补全第一批"新加的跨边界码必须在这里登记" | `SDK/api/taskgraph.py` 对界面/Host 发出 9 个不在 `TaskGraphBoundaryCode` 里的码（`BOUND_REACHED`、`INVALID_CURSOR`、`INVALID_REQUEST`、`INVALID_REVISION`、`NOT_ENABLED`、`NOT_FOUND`、`REVISION_NOT_FOUND`、`SNAPSHOT_CHANGED`、`SOURCE_CHANGED`）；`TaskGraphBoundaryCode` 除表与 `test_error_table.py` 外无人引用，发出时不经 `classify` | 没有文档提到这 9 个码 | 中 |
| C7 | §12"同一检查阶段按 typed code 排序……不按异常文本 contains 做分类" | `SDK/orchestrator/event_handler.py:7157`：`ContractError` 文字以 `TASKGRAPH_SHARED_`/`TASKGRAPH_REUSE_`/`TASKGRAPH_ACCEPTED_PRODUCER_` 开头 → 记为规划器被拒（`REUSE_NOT_ALLOWED`）并退回；否则抛出当库故障原地重试。决定的是秩序（扣不扣规划次数、要不要重试） | 联测记录 3.2 只当缺陷修复写了"这类拒绝记成被打回的规划决定"（opt.154）；与补全第一批"一轮故障只看类型码、决定秩序的码进错误码表"的定稿口径冲突，没登记 | 高 |
| C8 | 同 §12 | `SDK/orchestrator/requirements_amendment.py:104` 从 `ValueError` 文字切出拒绝码；`Host/taskgraph.py:137` 从 `StoreError` 文字切码再映射成中文说明 | 未登记 | 低（前者决定拒绝码，后者只影响展示） |
| C9 | §6.2/§6.3 策略行唯一写入口由 `enable_taskgraph_contract` 经 Store 接口（`insert_policy` 需外层事务、`policy()` 读）；`list_member_pins/list_method_pins` 两个读接口 | `SDK/orchestrator/taskgraph_policy.py:147` 直接写 SQL；`policy/insert_policy/list_member_pins/list_method_pins` 不存在（钉只随 `read_revision` 返回） | 改名表只登记了 P18 等挪位，这四个接口缺失没登记 | 低 |
| C10 | §13"若某现有函数因新接口必须小改，source-map 列出具体调用边和对应负测；不能把整仓授权给一次重构" | 清单外新增约 30 个 `SDK/orchestrator/taskgraph_*.py`（assembly、bindings、candidate、completion_sources、convergence_authority、demands、deployment、execution_policy、execution_sources、execution_view、outcomes、plan_commit、plan_sources、policy_sources、preview、reconciliation_proof、resolutions、resume、review、review_evidence、runtime、runtime_imports、runtime_observation、settlement、sharing_inputs、terminal、wakeups、action_settlement、materialization、operator、notifications、dispatch） | 来源表只覆盖 24 个来源点，没有逐模块的 source-map | 低（流程性） |
| C11 | §15/§16 验收资产：安全层失败都记四件事实；规模记录延迟/内存/SQL 次数；交付目录成套 | 四件事实只两处用（第 159 条）；规模没有计时/内存/SQL 计数（第 164 条）；没有交付目录（第 168 条） | 联测记录只写了做到的部分，没把缺的登记为偏离 | 中（验收证据缺口） |
| C12 | §16"必需用例不能 skip；已有红项与新红项分开……'只通过 focused'不能标全部完成" | 联测记录 6.4、6.6 记 10 条老失败（含 `test_execution_view_edges`、`test_root_review_coordinator`"重切次数用完"）"另立待办"，至今未清；部署门仍 `NOT_RUN` | 有记录说"另立待办"，但没有对原计划完成关口作偏离登记 | 中 |

---

## 第三节：B 级子代理裁决偏离清单

"用户原话依据"一栏：**无** = 只有子代理裁决或"用户同意开工"的间接认可；**有（转述）** = 别的文档写了"用户定/用户采纳"，但我没见到用户原话，需用户确认后才能升 A；**命中 A#n** = 已在 A 清单里。

### 3.1 《TaskGraph 补全方案》第七节（第 2 版 13 条 + 第 3 版新增 20 条，共 33 条）

| 编号 | 计划原文 | 现状（代码） | 裁决出处 | 用户原话依据 |
|---|---|---|---|---|
| B1 | I05"ORDER 不传播文件/授权" | 接续步骤顺带交出收到的文件：`SDK/orchestrator/hierarchical_dispatch.py:3671-4015` | 补全方案第 2 版第七节第 1 条 | 无 |
| B2 | §6.5"新 Mission 在第一次 TaskGraph 提交前启用"（由明确命令） | 建任务同事务绑定：`SDK/deployment/assembly.py:118-127` | 第七节第 2 条；HTN 计划表一 16 | 有（转述：HTN 计划第二节"都是用户逐项定过"；记忆"09-27 所有功能默认开启"） |
| B3 | §3.1/§3.3 三种读上下文与四个装配函数 | 类方法与 `PlanMutationSources`，无联合返回类型（第 32、44、60～64 条） | 第七节第 3 条 | 无 |
| B4 | 附录C §3.1-5"升级不能覆盖旧清单" | v5 清单原地重写多次：`SDK/graph/network_codec.py:141` | 第七节第 4 条 | 命中 A#17 |
| B5 | §5.6 局部影响反向 BFS | 屏障 + 使用前重验，效果集恒保守：`SDK/graph/convergence.py:151-173` | 第七节第 5 条；HTN 计划表一 3 | 有（转述：`PLAN-STATUS.md:476`"用户决定（2026-09-30）……按我的建议"） |
| B6 | §0.3"REPAIR/PROPOSE_SUCCESSOR 只解码不执行" | 已执行：`SDK/planning/htn/graph_repair.py:293` | 第七节第 6 条 | 无 |
| B7 | §8.1"candidate_policy 允许另一个 Attempt" 下的多尝试择优 | 已删 | 第七节第 7 条；HTN 表一 4 | 有（转述："用户 10-02"） |
| B8 | §0.3/§15 GPT-5.6 | 只用 DeepSeek | 第七节第 8 条 | 命中 A#2 |
| B9 | §0.3 前置门禁通过才开生产开关 | 由"整体验收"替代；部署读取接受 `NOT_RUN`（`taskgraph_deployment.py:77`），而整体验收门至今 `NOT_RUN` | 第七节第 9 条 | 无（且替代前提未兑现） |
| B10 | §15 42 组、每组 3 次 | 六组代表 + 12 改坏、每场景 1 局 | 第七节第 10 条 | 命中 A#1 |
| B11 | §11 不开放 cursor/limit | `execution_snapshot` 分页、整体哈希钉住：`SDK/api/taskgraph.py:335-386` | 第七节第 11 条 | 无 |
| B12 | §16 SQL 关口含"升级" | 升级迁移不测 | 第七节第 12 条 | 命中 A#17 |
| B13 | §11 历史与差异给界面 | 只进诊断导出：`Host/diagnostics.py:404-430`；界面无调用 | 第七节第 13 条；HTN 表一 23 | 有（转述：HTN 计划"用户逐项定过"） |
| B14 | §5.3 类型允许复用即按签名去重 | 只在规划器点名 `reuse` 时共享：`SDK/planning/plan_preview.py:369`、`SDK/contracts/planning_decisions.py:1036-1082` | 第七节第 14 条 | 无（由 A#8 核心思想推导） |
| B15 | §5.3 共享子目标 | 只共用普通步骤 | 第七节第 15 条 | 无 |
| B16 | §5.3 共享条件 | 同输入（上游一并点名）、同要求（不得超出已负责的） | 第七节第 16 条 | 无 |
| B17 | §0.3 `BIND_EXISTING_GOAL` | 整条删除（全库 0 处） | 第七节第 17 条 | 无（A#8"同一件事一条路径"推导） |
| B18 | §5.3"相似只产生候选" | 相似度推荐删（`SharedGoalIndex`、`statement_similarity` 0 处） | 第七节第 18 条 | 无 |
| B19 | （改要求后的沿用，原计划未写） | 只沿用叶子，子目标不跨版本 | 第七节第 19 条 | 无 |
| B20 | §5.5 钉住/跟随 | 只有一个产出版本，重做一律后继步骤 | 第七节第 20 条 | 无 |
| B21 | §9.1 ABANDONED"操作者/系统规则批准" | 写的是"只认用户点击"，但代码另有系统解除（第 125 条） | 第七节第 21 条 | 无；且条文与代码不符 |
| B22 | §6.3 `read_active_consumers` | 删除 | 第七节第 22 条 | 无 |
| B23 | §3.2/附录C §1.1 无规则即 `TARGET_RULES_UNAVAILABLE` | 默认每步工作区 `TASK_WORKSPACE_V1`：`hierarchical_dispatch.py:681-686` | 第七节第 23 条 | 无 |
| B24 | §10.2 监听计划修订事件 | 重查事件集不加；但提交事务自写 REEVALUATE（第 135 条） | 第七节第 24 条 | 无；条文理由不完整 |
| B25 | §12 全部码归类 | 只归决定秩序的码；不带码异常原地重试 | 第七节第 25 条 | 后半命中 A#14；前半无 |
| B26 | §8.4 审阅按尝试冻结范围 | 放宽"冻结范围 = 现行范围"（已被 B29 取代） | 第七节第 26 条 | 无 |
| B27 | §6.5（老任务） | 没绑定的老任务扫描照原意跳过，按名停只走关口：`SDK/storage/taskgraph_store.py:47-69`（`require_bound`） | 第七节第 27 条 | 无（与 A#17 相关） |
| B28 | §7.3/§8.4 | 在跑步骤跨计划版本交结果、范围未变按现行范围验收：`SDK/storage/operation_completion_store.py:970`（`scope_unchanged`） | 第七节第 28 条 | 无 |
| B29 | §8.4 | 重审以（结果, 要求版本）为键、扫描驱动：`SDK/orchestrator/carried_review.py:54`；迁移 42 | 第七节第 29 条；偏差单《第四批重审入口》独立裁决"选丙" | 无 |
| B30 | §5.5 | 钉住整个删除含迁移 43；`requires_reacceptance` 恒真留着（`SDK/artifacts/input_bindings.py`、`taskgraph_inputs.py` 4 处） | 第七节第 30 条 | 无 |
| B31 | §15 产品级用例 | "两个方向换做法""改接后换做法"只在单元层测 | 第七节第 31 条 | 无 |
| B32 | （共用退回原因） | 由结构核对 `single_port_overbound` 先拦下 | 第七节第 32 条 | 无 |
| B33 | §5.3 ChildBinding | `resolution_ref` 删除（`SDK/contracts/htn.py:1583-1600` 已无此字段） | 第七节第 33 条 | 无 |

### 3.2 HTN 补齐侧影响 TaskGraph 原计划的偏离

| 编号 | 计划原文 | 现状 | 裁决出处 | 用户原话依据 |
|---|---|---|---|---|
| B-H1 | §6.5"有效 planning 委派"后才可启用 | 启用不查委派（`taskgraph_policy_sources.py:85-93`），每次提交仍核授权 | `HTN补齐计划-2026-10-02.md:63-64`"阶段 A′ 新增的偏离"，"裁决员和新会话复审员一致建议，用户采纳" | 有（转述："用户采纳"） |
| B-H2 | §6.5/§10.3/§4.3 `CAPTURED_BASELINE` 迁入与覆盖边界 | 删除；迁移 34 库层拒绝 | HTN 计划"本计划新增的偏离"；实施记录阶段 A | 无（与 A#17 相关） |
| B-H3 | I09 燃料不重置 | `refine()` 按义务扣燃料删（R08） | HTN 计划"本计划新增的偏离" | 无 |
| B-H4 | §9.1 ABANDONED 条件 | 决定提交被拒或被新决定替代时系统结束围栏（`taskgraph_convergence.py:136,308-327`） | `HTN补齐-阶段B卡住缺陷-裁决.md` 第 5 类 | 无 |
| B-H5 | 附录C §6 BLOCKED 只经操作者 Q13 重置 | 作业结束时被挡通知自动回待发（`taskgraph_convergence.py:332-341`） | `HTN补齐-实施记录.md:316`；阶段 B 完成评估 | 无 |
| B-H6 | §15 Hypothesis 200×50 | 固定种子驱动，联测 2000 步 | `HTN补齐计划-2026-10-02.md:78-82` 阶段 F 开工裁决 | 无 |
| B-H7 | 附录C §1.1 需健康证据的能力无 reader 报不可用 | 不预探"连得上/兼容"（R48） | 同上 | 无 |
| B-H8 | §9.4 补偿走受控 Operation | 补偿入口删 | HTN 计划表一 13 | 有（转述："用户逐项定过"） |
| B-H9 | §13"不在 allowlist：新公开 PlanningDecision 类型" | 新增 `READ_METHOD_LIBRARY`；三种细化决定加 `reuse` | HTN 计划"本计划新增的偏离"；补全方案第三批 | 无 |
| B-H10 | §3.3/§6.3/§10.3 函数名与模块位置 | `get_planning_request`、`commit_planning_revision`、`replay_taskgraph`、`TaskGraphAttemptInputStore` 等改名挪位 | `HTN补齐-阶段F-开工裁决与施工清单.md` 1.6 改名表；`seams_current.json` 各行 difference | 无 |

---

## 第四节：A 级排除命中清单

| A 编号 | 命中的计划条目 |
|---|---|
| A#1 | §0.3 NanoJev 影子（第 5 条）；§15 42 组 × 3 局 → 六组代表 + 12 改坏、每场景 1 局（第 156 条、B10）；P16 金额维度删除（第 49 条） |
| A#2 | §0.3/§15 GPT-5.6 → 只用 DeepSeek（第 5 条、B8） |
| A#3 | §6.5"默认旧路径不写九表"——旧平面模式整条删，未绑定任务提交直接拒（第 109 条） |
| A#8 | 不单独命中条目；B14、B17、B18 由它推导，但具体做法是子代理裁决，仍列 B |
| A#14 | B25 后半"不带码的异常原地重试、上限兜住" |
| A#17 | 编码清单原地改写（第 68 条、B4）；升级迁移不测（B12）；与 B27、B-H2 相关 |

---

## 第五节：D 级待办

- 口径第四节列的 4 项 D 级里，与 TaskGraph 原计划有关的只有"F2 联测待用户定两件（资料换版本入口、主对话列任务工具）"。按 `HTN补齐-阶段F2-联测记录.md` 6.1～6.6，用户 10-05 已定、两件都做了（opt.160/161，Host `ba39d505`），资料换版本真机局 `mission-6f7f…` 通过。**这项 D 级已关闭**，口径文件需要更新。
- 其余与 TaskGraph 有关、但不属于"用户说排最后"的未完成项（照严格口径算未完成，不是 D 级）：
  1. 部署验收门 `taskgraph_acceptance` 仍为 `NOT_RUN`（资料换版本局通过后没刷新）。
  2. 真实模型"分支共享并换方法"没有观察到点名共用，也没观察到换方法。
  3. 12 个改坏最后一次在 opt.152/153 代码上执行，opt.154～162 改过的 `compiler.py`、`event_handler.py`、`taskgraph_sharing.py` 等没重跑。
  4. 10 条已知红用例"另立待办"。
  5. 独立核验没有按原计划六道关口重做（联测记录 5.3 自述）。

---

## 第六节：与此前对照文档不一致的地方

| # | 文档与原结论 | 本次结论 | 谁对 |
|---|---|---|---|
| 1 | `TaskGraph-现状对照-2026-10-04.md` §10.2"监听来源含计划修订已提交：做法不同未记录"；补全方案偏离 24"不加" | 计划提交事务自己就追加一条 REEVALUATE 通知（`taskgraph_plan_commit.py:150-153`），提交后的唤醒其实已有，只是不经重查事件集 | 本次（两份都漏看了提交参与方这一段） |
| 2 | 同上 §4.3"视图、通知、错误三种协议：在用"；附录 E"在用" | 视图/解释/收敛视图没有严格 codec，8 份 Schema 不在 SDK，收敛视图多字段（C3、C4） | 本次 |
| 3 | 同上 §5.5"格式转换器：在用" | 有转换器声明时原字节照用、不拒绝，违背原文；产品走不到（C1） | 本次 |
| 4 | 同上 §12"跨边界码……未知码拒绝：在用" | 执行图只读接口实际发出 9 个未登记码，`TaskGraphBoundaryCode` 没有被任何生产代码使用（C6） | 本次 |
| 5 | 同上附录 A"迁移 34 改写两个守卫触发器" | 还漏了迁移 30 把开工身份触发器放宽成 `<=`（C5） | 本次 |
| 6 | 补全第一批（实施记录"一-1"）"分类只看异常对象上的类型码" | 之后联测 opt.154 在 `event_handler.py:7157` 又加了按文字前缀分流（C7），第一批的结论对当时代码成立，对现行代码不成立 | 本次（代码在第一批之后退化） |
| 7 | 补全方案偏离 21"放弃改计划只认用户点击" | 代码另有"决定被拒/被替代时系统结束围栏"两条路（HTN 阶段 B 裁决），条文不完整 | 本次 |
| 8 | 现状对照 §5.3"共享：只在测试" | 补全第三批已接通：产品同形用例 `T/product_world/test_shared_steps.py` 走规划器点名 → 预览 → 提交 → 只做一次；判"在用"（真实模型未用过） | 本次（10-04 时原结论正确，之后代码已变） |
| 9 | 现状对照 §15"12 个定点改坏：没做（已排联测）"、§15 规模"没做" | 改坏 12 条已在 10-05 全部抓到（证据在 `a4证据/2026-10-05/mutations/`）；规模边界用例已写，但延迟/内存/SQL 计数仍没有 | 本次（时间差） |
| 10 | `seams_current.json` P13 difference"阶段 D 补了'钉住'值来源（`_pinned_revisions`）"；联测记录"24 行全部有现行生产方与用例" | `_pinned_revisions` 第五批已删，该行文字过时；生产方可导入、用例存在这两点仍成立 | 本次（文档没随第五批刷新） |
| 11 | 现状对照 §6.3"在用（一处未记录）" | 除 `read_active_consumers` 外，`policy/insert_policy/list_member_pins/list_method_pins` 四个接口也不存在、策略写入不经 Store（C9） | 本次 |
| 12 | 联测记录 3.4"清单里'执行图验收'仍是没跑……'资料换版本后重新规划'没有产品入口、没跑成" | 6.1 已更正入口存在，6.4 真机局已通过，但部署清单仍 `NOT_RUN`，前后矛盾未收口 | 本次（清单该刷未刷） |
| 13 | 现状对照 §3.2"24 个来源：在用" | 24 行里 P03、P07、P11、P14、P15、P16、P22、P23 八行是"做法不同"，P12 是"部分"，不宜笼统写"在用" | 本次（口径更严） |
| 14 | `需求与场景状态.md` R11"几个目标共用的子成果只做一次：已接入" | 与本次一致（产品路径接通、真实模型未观察到），不冲突 | 一致 |
| 15 | `HTN实现与原始计划差距评估-2026-10-02.md` 第五节第 3 条、`原始计划对照-2026-10-02.md:43` 把"屏障 + 使用前重验"记为用户 09-30 定 | 与本次 B5 一致；建议用户确认后升 A | 一致 |
| 16 | 口径文件第四节 D 级"F2 联测待用户定两件" | 用户 10-05 已定、已做完并真机通过 | 本次（口径文件该更新） |
