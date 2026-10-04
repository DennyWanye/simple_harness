# TaskGraph 补全第三、四、五、六批 — 完成评估

- 评估对象：分支 `tg-1`（worktree `simple_harness-a4`），基线 main `fbc3ad63`（第一二批已发版），HEAD `3608ad12`。范围 `git diff fbc3ad63..HEAD`（104 个文件）：第三批 `7757a63c`、`b41353f2`；第六批 `b333ebc3`；第四批 `dca6951a`（方案 3.5）、`f6370f85`、`87a55dc7`；第五批 `3608ad12`。
- 对照：`TaskGraph-补全-方案.md`（修订说明到第 3.6 版）第 3～6 节、第 7 节偏离 26～30；`TaskGraph-补全-实施记录.md` 第三、四、五、六批；`TaskGraph-补全-偏差单-第四批重审入口.md`（裁决"丙"）；`TaskGraph-补全-第四批-施工设计.md`；仓库根 `CLAUDE.md` 硬约束。
- 方法：只读代码与文档，**没有跑任何测试**（用例通过与改坏 KILLED 照实施记录，本次没有复跑；只用脚本静态核了改坏条目的原文在源码里恰好出现一次、点名的用例函数存在）。行号按 `3608ad12`。
- 路径缩写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`Host/` = `backend/deskpet/orchestration/`。

## 结论

**主体按方案做到了，需补：必须补 4 项，可记为偏离 / 顺手清理 9 项。**

- 第三批：点名共用（`reuse` 写在三种细化决定共同落成的 `RefineOperation` 上）、系统只核秩序、候选一份来源、`_merge` 只删只属于被退休做法的边、第 8a 条跨版本交结果、提示词 v24、`BIND_EXISTING_GOAL` 与自动按签名合并整删，都落了。**方案"用例"六条里有两条没有对应用例**（点名读上游的步骤却没点名上游 → 退回；改接过输入的步骤所在做法被换掉 → 照常提交），一条只是隐含覆盖；"两个方向换做法"降到单元层，方案没改。删除清单有一处漏删：`ChildBinding.resolution_ref`（复用子目标结论的槽位字段，现在没有任何写入方）。
- 第四批：按偏差单裁决"丙"做——（结果, 要求版本）为键、扫描驱动、不建尝试、不建新表、核心与新结果共用（`router.verify` + `LeafAcceptanceAssembly`）。方案五条用例都有对应（变体做法略不同）；"审阅没有结论 → 问用户"有代码、**没有用例**（方案用例没列）。新码不进错误码表的口径与方案原文不同，记录写了理由、**方案没改**。新事件"登记进全业务重放清单"的说法成立（该清单按表登记，本仓库没有事件类型登记表）。
- 第五批：钉住在 src 里删干净（剩下的只是旧迁移脚本里的历史 DDL，由迁移 43 重建），tests 里还有几处过时的说明文字 / 一处测试资产描述。方案两条用例都有。
- 第六批：SDK 步骤账、Host 诊断 `STEP_FIELDS`、界面展开与用例都到位。
- 改坏登记：TG3-04～06、TG4-01～04、TG5-01、TG6-01 与方案要求逐条对得上（TG3-05 守的是单元用例，TG4-04 只改四处查找中的一处，见第五节）。
- 硬约束：没有发现新增的旧数据兼容分支、双路径或语义判断规则。

## 一、第三批　共用在产品里接通

### 1.1 做法逐条

| 方案条目 | 判定 | 证据 file:line | 说明 |
|---|---|---|---|
| 1 规划器点名写在决定上：REFINE / REFINE_DEEPER / REPLACE_METHOD 加可选 `reuse`，落成同一个 `RefineOperation`；PROPOSE_METHOD 不带；做法定义不带出现编号 | 已做 | `SDK/contracts/planning_decisions.py:1036` `_reuse_map`、`:1071`、`:1192`、`:1223`；`SDK/contracts/htn.py:2385` `RefineOperation`、`:2392` `reuse`；`SDK/planning/decision_adapter.py:200-214`；JSON 规约 `SDK/contracts/schemas/planning-decision-v1.schema.json:357/407/654` | 只在预览处读（`SDK/planning/plan_preview.py:337`）。`MethodStep.reuse_policy` 已删，`SDK/contracts/htn.py:710` 注释写明去重不写在做法里 |
| 2a 被点名的步骤：同一任务、普通步骤、在跑或已验收 | 已做 | 候选 `SDK/orchestrator/taskgraph_plan_sources.py:78-123`；点名不在候选里即退回 `SDK/planning/plan_preview.py:383-388`；点名子目标退回 `SDK/planning/htn/grounding.py:587-588` | "未开工的不能点名"仍由执行图层 `validate_sharing` 的开工许可核对挡（`SDK/graph/taskgraph_sharing.py` 约 :162）；候选里状态 `running` 也包括还没开工的，见第七节 |
| 2b 任务类型相同 | 已做 | `SDK/planning/htn/grounding.py:208-219` `named_share_refusal`（类型 / 作用域 / 领域 / 副作用逐项比，写明哪项不同） | — |
| 2c 负责的要求不变（子集；待重审的不查） | 已做 | `SDK/planning/plan_preview.py:369-399`（`:393` `extra = [] if entry.carried else sorted(handed - owned)`），退回 `REUSE_NOT_ALLOWED` 写明多了哪几条与原负责哪几条 | 改坏 TG3-04 |
| 2d 负责写出的文件相同（由要求推出；保留作退回原因说明；`criterion_files` 一路传进做法落地） | 部分做 | 规划包候选行带 `writes`（`SDK/orchestrator/planner_views.py:69-92`）；落地 `plan_slots` 没收 `criterion_files`，退回说明里不写文件 | 方案本身说"上一条成立时同时成立"，行为上无缺口；但"传进去、写进退回原因"没做，也没记偏离 |
| 2e 数据输入相同（上游一并点名） | 已做（沿用现行核对） | `SDK/graph/taskgraph_sharing.py:107` `TASKGRAPH_SHARED_DATA_SLOT_MISSING`、`:114` `..._DECLARATION_DIFFERS`；提示词 `SDK/runtime/role_templates.py:272` 起写明"reads_from 不为空时上游一并点名" | **无用例**，见 1.2 第 3 条 |
| 2f 共用在跑步骤时先后已放行、开工许可现行 | 已做（沿用） | `SDK/graph/taskgraph_sharing.py` 约 :156-216（`SHARE_ACTIVE_*`） | — |
| 2g 不成环；有对外副作用的不能共用 | 已做 | 副作用 `SDK/planning/htn/grounding.py:216-218`；成环由执行投影校验 | 单元用例 `test_a_side_effecting_step_is_never_shared` |
| 3 候选只有一份来源（规划包、预览、提交核对同一函数；每行带类型 / 要求 / 写入 / 上游 / 状态） | 已做 | `eligible_sharing`（`SDK/orchestrator/taskgraph_plan_sources.py:78`）被 `sharing_candidates`（`:173-182`）与提交来源（`:251`）共用；规划包 `SDK/orchestrator/planner_views.py:69-92`；`planning_graph_repairs.py` 整个删除 | 状态三种：`running` / `accepted` / `accepted_under_old_requirements` |
| 4 删：`SharedGoalIndex`（含 `suggest`、`ShareSuggestion`、`statement_similarity`）、按签名自动查、`TaskTypeSpec.reuse_policy`、`MethodStep.reuse_policy`、`effective_reuse_policy`、准入复用策略检查、`may_share` 类型分支、`BIND_EXISTING_GOAL` 整条、`resolution_ref`（只为它服务的） | 基本已做，**一处漏删** | grep src/tests/Host/前端：`SharedGoalIndex`、`ShareSuggestion`、`statement_similarity`、`effective_reuse_policy`、`BIND_EXISTING_GOAL`、`BindExistingGoal`、`bind_existing`、`graph_repair_sources`、`resolution_reuses`、`may_share`、`BindSharedGoalOperation` 均 0 处；`plan_slots` 只认点名（`SDK/planning/htn/grounding.py:531-613`）。**漏删**：`ChildBinding.resolution_ref`（`SDK/contracts/htn.py:1601`，校验分支 `:1652-1657` "reused compound binds one exact GoalResolution"，编解码 `:1672`、`:1702`）——它是"复用子目标结论"在槽位上的落点，`BIND_EXISTING_GOAL` 删后全库没有写入方（唯一一处 `SDK/planning/htn/graph_repair.py:357` 只把它清成 None） | 与"只共用普通步骤"（偏离 15）矛盾，是旧路径残留。`T/full_target/fixtures/plan_pack/*.schema.json` 里的 `bind_shared_goal` 是原计划的冻结规约样本，不算 |
| 4 留：`ReusePolicy`、`ChildBinding.reuse_policy`、`SharedGoalEntry`、`shared_goal_index`、`validate_sharing`；`SharingSignature` 只留秩序项 | 已做 | `SDK/contracts/htn.py:194-203`、`:1591`；`SDK/planning/htn/grounding.py:175-205`、`:222-245`；`SDK/orchestrator/hierarchical_dispatch.py:3888` | `SharingSignature` 现只有类型 / 作用域 / 领域 / 副作用 |
| 4 受影响约 10 个测试文件改写成点名方式 | 已做 | `T/full_target/test_htn_and_or_shared_goal.py`、`test_seed_methods.py`（删 3 条只测自动合并的）、`admitted_plans.py`、`fixtures/htn/` 等，见 diff stat | — |
| 5 `_merge`：任一端离开网络就删；两端都属被退休做法且没有留下的已采纳做法同时持有两端才删 | 已做 | `SDK/planning/htn/compiler.py:1172-1203`（`retires`），用在 `:1250`、`:1258` | 不加边的归属字段，编码清单未因此改。改坏 TG3-05 |
| 6 费用：共用步骤一个任务、一份花费 | 已做 | 共用槽不产生新任务绑定 `SDK/planning/htn/grounding.py`（`child_task_bindings` 约 :649 `if plan.shared`） | 产品用例断言只一个"写"、只尝试 1 次、预算去向只出现一次 |
| 7 提示词升一版（reuse 写法、只普通步骤、上游一并点名、系统只核秩序、删 BIND 段） | 已做 | `SDK/runtime/role_templates.py:113` `planner-hierarchical-v24`；`:229-260`、`:272-281` | — |
| 8 合同：`MethodStep.reuse_policy` 删、编码清单重写、三种决定与 `RefineOperation` 加 `reuse`、删 `BindExistingGoalDecision` | 已做 | `SDK/graph/network_codec_manifest_v5.json`（diff 14 行）；`SDK/contracts/planning_decisions.py` 见上 | 第五批又重写一次（偏离 30 已记） |
| 8a 在跑步骤跨计划版本交结果，范围除版本外未变就按现行范围验收；与已验收内容沿用共用一个判断 | 已做 | `SDK/storage/operation_completion_store.py:967` `scope_unchanged`，`retained_content_contributions` 改调它；`SDK/orchestrator/completion_inputs.py:338` `_current_scope`；验证途中换版 `SDK/orchestrator/event_handler.py:5905` `_scope_carried` | 偏离 28 已记。改坏 TG3-06 |

### 1.2 用例逐条

| 方案用例 | 判定 | 证据 file:line | 说明 |
|---|---|---|---|
| 1 两分支共用一步（写同一文件）、留下分支有下游；只建一个任务、只跑一次、只扣一次；**两个方向**换做法：共用步骤不取消不重做、下游边还在、任务完成 | 部分做（做法与方案不同，记录写明、方案没改） | 产品同形：`T/product_world/test_shared_steps.py:184` `test_a_named_shared_step_is_done_once_and_feeds_the_other_branch`；两个方向：`T/full_target/test_htn_and_or_shared_goal.py:425-457`（参数化 first / second，走真实编译） | 两个方向只在单元层核"共用步骤还在、只剩一个上级、到留下分支下游的数据边还在、投影合法"，没核"不取消、不重做、任务完成"。实施记录三-2 写了理由；方案第 3 节原文写"联测必须补的两条之一"，第 7 节没有对应偏离 |
| 2 第二分支给共用步骤多链一条要求 → 整份退回写明多了哪几条；改正后通过 | 已做 | `T/product_world/test_shared_steps.py:202` `test_sharing_does_not_widen_what_the_named_step_answers_for` | 改坏 TG3-04 |
| 3 点名一个读上游产出的步骤、却没点名上游 → 退回，写明输入不同 | **没做** | `T/` 下 `TASKGRAPH_SHARED_DATA_SLOT_MISSING` / `..._DECLARATION_DIFFERS` / `SHARE_ACTIVE_INPUTS_CHANGED` 0 处引用；`test_htn_and_or_shared_goal.py` 与 `test_shared_steps.py` 都没有带上游的被共用步骤 | 核对靠沿用的 `validate_sharing`，本批没有任何用例守住"上游一并点名"这条新口径 |
| 4 会写文件的步骤共用后，不被判成"只读步骤还在写" | 部分做（隐含覆盖） | `T/product_world/test_shared_steps.py:184`：被共用的"写 out.md"步骤走到任务完成 | 走通即说明没被判越权，但没有专门断言（例如核共用步骤的任务绑定 `resource_writes` 仍含 out.md） |
| 5 改接过输入（`REBIND_INPUT`）的步骤所在做法被换掉，换做法照常提交（不留悬空的边） | **没做** | `T/` 下同时涉及 `REBIND_INPUT` 与换做法的只有决定编解码 / 准入类用例；`test_h4_graph_repair_commits.py:239` 只测改接本身 | `_merge` 规则①"任一端离开网络就删"没有任何用例守；TG3-05 改的是规则② |
| 6 改坏：`_merge` 改回碰到就删 → 第一条变红；去掉要求核对 → 第二条变红 | 已做（TG3-05 守的是单元用例） | `T/acceptance_assets/mutations.json` TG3-04、TG3-05；另加 TG3-06（第 8a 条） | 见第五节 |

## 二、第四批　改要求后沿用已验收的叶子，审阅员按新要求重审

### 2.1 做法逐条

| 方案条目（第 3.5 版） | 判定 | 证据 file:line | 说明 |
|---|---|---|---|
| 1 触发只看事实：现行计划里的普通步骤、只按旧版通过、现行要求下不算数 → 系统请审阅员重审；原样留着或换做法时被点名 | 已做 | 扫描 `SDK/orchestrator/carried_review.py:45-96`；主循环与验证共用名额 `SDK/orchestrator/event_handler.py:3794-3806`；被退休做法里的旧叶子由 `kept_by_share` 接住（`SDK/planning/htn/compiler.py:1172`） | — |
| 1 取代 `ACCEPTED_UNDER_OLD_REQUIREMENTS`，细分码"等审阅员按新要求重审"（登记错误码表） | 做法与方案不同（记录写明，方案没改） | `SDK/orchestrator/hierarchical_dispatch.py:2457-2477`：`CARRIED_REVIEW_PENDING` / `CARRIED_RESULT_REJECTED`；旧码全库 0 处 | 两个码不进 `SDK/contracts/error_table.py`。实施记录四-3 理由："给规划器看的说明，不决定秩序，按第一批口径不进错误码表"——理由成立（旧码当初也没登记，派发细分码不被 `classify_round_fault` / `refusal_charges_planner` 读），但方案第 4 节第 1、6 条原文仍写"登记错误码表" |
| 1 重审按数据先后：上游在现行要求下都算数后才审 | 已做 | `SDK/orchestrator/carried_review.py:88-90` | 改坏 TG4-03 |
| 2 只做叶子；子目标不跨版本沿用（提示词写明） | 已做 | `SDK/orchestrator/carried_review.py:70`（只取 primitive）；提示词 `SDK/runtime/role_templates.py:211-217` | — |
| 3 键改为（结果, 要求版本）：验证层记录加列、唯一约束（结果, 要求版本, 层）；审阅绑定四处查找加要求版本；验收编号与命令号带要求版本；不建新表 | 已做 | 迁移 42 `SDK/storage/schema.py:1183`、登记 `:1418`；`Store.list_verifications` 必给要求版本 `SDK/storage/store.py:967-970`；验收编号 `SDK/orchestrator/assurance_validity.py:92`；四处查找：`SDK/orchestrator/assurance_review_runtime.py` `task_record`（约 :108-133）与查旧调用（约 :411-421）、`SDK/orchestrator/assurance_validity.py:217-236`、`SDK/orchestrator/human_commits.py:310-317` | 重放清单表字段同步（`SDK/observability/business_replay_inventory.json` 加 `requirements_revision`） |
| 3 不建尝试、不调执行者、不改尝试 / 任务 / 产物状态；存活看现行计划；审阅预算另记任务账户、不计尝试次数 | 已做 | 驱动 `SDK/orchestrator/event_handler.py:8170-8268`；写验收 `SDK/orchestrator/commit_service.py:2796-2859`（复用 `LeafAcceptanceAssembly`，只写验收与 `CarriedResultAccepted`）；审阅预留 `SDK/orchestrator/assurance_review_runtime.py:437`；存活唯一判断 `SDK/orchestrator/assurance_review_import.py:168-180` `carried_review_alive`，被审阅运行（`:529`）、模型调用准入（`SDK/runtime/provider_budget_guard.py` 约 :169-177）、停止判定（`event_handler.py:5511-5515`）共用 | 核心"本地检查 → 审阅员 → 写验收"与新结果共用 `router.verify` 与 `LeafAcceptanceAssembly`，没有另写一套 |
| 3 冻结范围核对改为"任务合同不变、现行要求版本不低于冻结的"，只走重审入口 | 已做 | `SDK/storage/operation_completion_store.py:967-1000`（`across_requirements`：只许要求三项变且版本更新）；`SDK/orchestrator/completion_inputs.py:393` 起（只对已验收结果成立）；切包与验收核对 `SDK/orchestrator/scoped_content_review.py:317-321`、`:366-367` | 正常路径不传参数，行为不变 |
| 3 共用核对"复用已验收"加"按旧版通过、待重审" | 已做 | `SDK/graph/taskgraph_sharing.py:124-129`；候选 `SDK/orchestrator/taskgraph_plan_sources.py:105-111`、`:126-146` | 重审已打回的不再是候选 |
| 3 通过 → 新版验收；没过 → `CARRIED_RESULT_REJECTED` 修复请求（带不通过准则与理由） | 已做 | `SDK/orchestrator/event_handler.py:8232-8236`、`:8256-8266` | 改坏 TG4-01、TG4-02 |
| 3 审阅没有结论 → 现行"判不下来"出口（同模型新会话复审一次，仍不行问用户） | 已做，**无用例** | `SDK/orchestrator/event_handler.py:8218-8224`（读用户裁决回灌）、`:8248-8254`（`_ask_person_to_adjudicate`）；复审由 `router.verify(needs_human_allowed=True)` 走现行路径 | `T/product_world/test_carried_review.py`、`test_carried_redo.py`、`test_requirements_amend.py` 都没有 `suspended` / `adjudicate-carried` 场景；方案用例没列这一支 |
| 4 每步每要求版本最多重审一次（键天然唯一，不另计数） | 已做 | `SDK/orchestrator/carried_review.py:81-87`（已有该版验收或已打回即跳过） | — |
| 5 提示词（与第三批同一版）：沿用 / 重审 / 打回 / 不想沿用就换掉；子目标那句 | 已做 | `SDK/runtime/role_templates.py:211-217` | — |
| 6 新事件、新码登记进全业务重放清单与错误码表 | 部分做（说法成立的部分已做） | 重放清单按**表**登记（`business_replay_inventory.json` 顶层只有 `tables` 等，无事件类型表），验证表新列已登记；全库没有单独的事件类型登记表（`MissionRoundFault` 等也不在任何清单里），"本仓库没有单独的事件类型登记表"属实。新码见第 1 条 | 方案文字仍是"登记进错误码表"，需改方案 |

### 2.2 用例逐条

| 方案用例 | 判定 | 证据 file:line | 说明 |
|---|---|---|---|
| 1 旧叶子原样留着：没有新尝试、按新版多一份审查包与一条验收、任务按新版完成 | 已做 | `T/product_world/test_requirements_amend.py:544` `test_kept_old_step_is_reviewed_again_not_rerun`（只 1 次尝试、第 2 版审阅员层 PASS、两条验收编号、`CarriedResultAccepted` 一条、按新版判成功） | "多一份审查包"没有直接断言审查包 / 绑定行；打回那条用例断言了第 2 版绑定恰 1 行（`test_carried_review.py` 约 :152-154） |
| 2 换做法时新做法点名共用旧叶子：同上 | 已做 | `T/product_world/test_carried_review.py:249` | 断言候选状态、无重做、两版验收、a.md 只一个任务在写 |
| 3 变体：重审打回 → 修复请求（带不通过条目）→ 规划器**换做法**（没过的那步新做，下游一起新做，其余旧叶子用 `reuse` 点名）→ 按新版完成 | 部分做（做法略不同，记录没写明） | `T/product_world/test_carried_review.py:98`：打回后规划器用 `PROPOSE_SUCCESSOR` 换掉那一步（两步互不依赖，没有下游）；"换做法 + reuse 点名其余旧叶子"只在未打回的第 2 条里出现；第五批 `test_carried_redo.py:177` 打回后换做法但全部新做、没点名 | 各环节分散覆盖，但"打回后换做法并点名其余旧叶子"这一组合没有一条走通 |
| 4 上游重审没过时，下游不被重审 | 已做 | `T/product_world/test_carried_review.py:189` | 改坏 TG4-03 |
| 5 同一步同一要求版本不重审第二次 | 已做 | `T/product_world/test_carried_review.py:146-154`（第 2 版审阅员层恰一条 FAIL、绑定恰 1 行，跑到任务完成之后再核） | — |
| 改坏：通过不写新版验收 → 第一条红；打回不发修复请求 → 变体红；审阅绑定查找不带要求版本 → 第一条红 | 已做 | TG4-01、TG4-02、TG4-04（另 TG4-03） | 见第五节 |

## 三、第五批　换后继步骤重做；删"钉住旧版本"

### 3.1 做法与残留

| 方案条目 | 判定 | 证据 file:line | 说明 |
|---|---|---|---|
| 1 重审没过的步骤用 `PROPOSE_SUCCESSOR` 重做，不改任务状态机、不改"原样再做一次"合同 | 已做（现成行为） | `SDK/planning/htn/graph_repair.py` `compile_successor`；状态机文件未动 | — |
| 2 下游没通过 → 改指后继重做；下游已通过 → `REPAIR_NOT_ALLOWED` 写明"要连同下游一起换"；共用步骤先放开才能换后继；提示词写明 | 已做（拒绝说明文字与方案略不同） | `SDK/orchestrator/plan_commits.py:416-425`；提示词 `SDK/runtime/role_templates.py:257-260` | 拒绝说明是 "accepted dependent requires an explicit successor"，"要连同下游一起换"写在提示词里，不在退回说明里 |
| 3 删钉住：`OutputValue.pin`、编译器分支、`_pinned_revisions`、解析钉住分支、提示词、钉住变体用例；`DataRequirement.source_revision_policy`、`SourceRevisionPolicy` 删 | 已做（src 干净） | grep src：`SourceRevisionPolicy`、`pinned_flows`、`_pinned_revisions`、`pinned_revisions` 0 处；`source_revision_policy` 只剩迁移 43 自身（`SDK/storage/schema.py:1278-1300`）与历史建表 DDL（`SDK/storage/htn_schema.py:176-177`、`SDK/storage/assurance_barrier_v26.sql:1290`，均被迁移 43 重建表 / 触发器覆盖）；`requires_reacceptance=True` 恒真（`SDK/artifacts/input_bindings.py:1023`，偏离 30 已记） | 数据表列与触发器（迁移 43）方案原文没列，施工补上，偏离 30 已记 |
| 3 tests 残留 | 部分做 | `T/acceptance_assets/seams_current.json:264` 仍写"钉住值来源 `HierarchicalDispatch._pinned_revisions`"；`T/full_target/test_input_manifest_resolution.py:21`、`:730` 仍讲 `PINNED` vs `FOLLOW_AUTHORIZED_REVISION`，`:742` 与 `:755` 两条用例现在入参完全相同；`T/product_world/test_input_revisions.py:40` `relay(pin=...)` 参数已无调用方；`SDK/artifacts/input_bindings.py:605` 注释 "Nothing pins this input yet" | 都是说明文字 / 死参数，不影响行为 |
| 4 记偏离：产出只有一个版本，重做一律后继 | 已做 | 方案偏离 20、30 | 方案第 5 节第 3 条"随第三批一起删"未改原文，由修订说明 3.6 与偏离 30 交代 |

### 3.2 用例

| 方案用例 | 判定 | 证据 file:line | 说明 |
|---|---|---|---|
| 下游没通过时换后继 → 下游作为"输入已替换"重做、拿到新产出 | 已做 | `T/product_world/test_carried_redo.py:138` | 断言下游冻结输入的生产结果是后继的结果 |
| 下游已通过时换后继被拒，写明要连同下游一起换 | 已做（只核码） | `T/product_world/test_carried_redo.py:177`（核 `REPAIR_NOT_ALLOWED`，之后改用换做法完成） | — |
| F1 第 6 号空缺：沿用的叶子重审通过后，下游新尝试冻结的输入引用新版验收 | 已做（单独一条，未与第四批第一条合写） | `T/product_world/test_carried_redo.py:111` | 等价 |
| 改坏：解析时允许旧版验收 → 变红 | 已做 | TG5-01（改在 `SDK/orchestrator/hierarchical_dispatch.py` 下游取验收的筛选处） | 改的是"取候选验收"而非 `input_bindings` 解析，守的是同一件事 |
| 钉住变体删除、加"写 pin 被拒" | 已做 | `T/product_world/test_input_revisions.py:111-118` | — |

## 四、第六批　逐步骤的预算去向

| 方案条目 / 用例 | 判定 | 证据 file:line | 说明 |
|---|---|---|---|
| 读时推出、不改义务结构；义务行下按步骤分一层（尝试、失败、已结算 token、用量未知），与义务账同一份算法 | 已做 | `SDK/orchestrator/obligation_accounts.py:24` `step_accounts`；义务账改为按它汇总（约 :51-77）；`:85` `_steps_by_duty`；行带 `steps`（约 :147） | 没有第二处计算 |
| 共用步骤只出现一次，标被几个分支用 | 已做 | `branches` 按已采纳做法计数（`obligation_accounts.py` 约 :91-97） | — |
| 快照 `budget_by_duty` 每行带 `steps`；Host 诊断同步 | 已做 | `Host/diagnostics.py:52` `STEP_FIELDS`、`:372-374`（不带目标原文） | Host 用例 `backend/tests/orchestration/test_mission_diagnostics.py:110-113` |
| 界面"预算去向"展开义务可见步骤（默认收起，最多 8 行，其余合并） | 已做 | `tauri-app/src/views/MissionsView.tsx:280-305`（`:293` 展开、`:298` 共用标注、`BUDGET_ROWS_SHOWN = 8` `:259`） | — |
| 用例：两步任务每步一行、合计等于义务行 | 已做 | `T/product_world/test_obligation_accounts.py` `test_budget_by_duty_and_unrefined_goals`（新增约 :125-130） | — |
| 用例：共用场景共用步骤只出现一次 | 已做 | `T/product_world/test_shared_steps.py:198-200` | — |
| 前端 vitest 一条 | 已做 | `tauri-app/src/views/BudgetByDuty.test.tsx:30-44` | — |
| 改坏：步骤行不按任务分 → 变红 | 已做 | TG6-01 | — |

## 五、改坏登记对照（`T/acceptance_assets/mutations.json`）

静态核过：每条 `original` 在所指源码里恰好出现一次，`tests` 点名的用例函数都存在。KILLED 照实施记录，本次没复跑。

| 编号 | 方案要求的改坏 | 登记内容 | 判定 |
|---|---|---|---|
| TG3-04 | 去掉"负责的要求不变"→ 第三批第二条红 | `plan_preview.py` `extra = []` → `test_sharing_does_not_widen_...` | 对得上 |
| TG3-05 | `_merge` 改回碰到就删 → 第三批**第一条**红 | `compiler.py` `retires` 改回"任一端属于被退休做法" → 单元用例 `test_one_branch_changing_its_method_keeps_...`（两个方向） | 改坏对得上；守的是单元用例而非产品用例（随 1.2 第 1 条的施工差异） |
| TG3-06 | （方案未要求，第 8a 条补） | `scope_unchanged` 恒 False → `test_a_named_shared_step_is_done_once_...` | 额外，合理 |
| TG4-01 | 重审通过不写新版验收 → 第一条红 | `accept_carried_result` → `pass` → `test_kept_old_step_is_reviewed_again_not_rerun` | 对得上 |
| TG4-02 | 打回不发修复请求 → 变体红 | `record_request` 换成恒 False → `test_a_kept_step_rejected_...` | 对得上 |
| TG4-03 | （方案未要求）重审不看上游 | `carried_review.py:89` → `if False` → `test_a_downstream_step_is_not_reviewed_...` | 额外，覆盖方案用例第 4 条 |
| TG4-04 | 审阅绑定查找不带要求版本 → 第一条红 | 只改 `assurance_review_runtime.task_record` 一处（固定取冻结版本）→ `test_kept_old_step_...` | 对得上；但四处查找只守了一处，`official_record_for_result`、`run_task` 查旧调用、`human_commits` 三处没有改坏 |
| TG5-01 | 解析时允许旧版验收 → 变红（F1 第 6 号） | `hierarchical_dispatch.py` 取验收不再筛"现行算数的" → `test_the_replaced_downstream_reads_...` | 对得上 |
| TG6-01 | 步骤行不按任务分 → 变红 | `obligation_accounts.py` 按任务改按任务全体 → `test_budget_by_duty_and_unrefined_goals` | 对得上 |

缺口：1.2 第 3、5 条没有用例，自然也没有改坏；`_merge` 规则①（任一端离开网络就删）没有改坏守住。

## 六、硬约束核对

| 约束 | 判定 | 说明 |
|---|---|---|
| 判断交给 LLM，Harness 只管秩序 | 符合 | "两步是不是同一件事"由规划器点名；系统只核类型 / 作用域 / 副作用 / 要求子集 / 输入 / 先后（`grounding.py:208`、`plan_preview.py:369`）；相似度推荐与按签名合并整删；重审过不过由审阅员定，扫描只找该审的步骤（`carried_review.py` 文件头） |
| 同一件事一条路径 | 符合 | 候选一份来源（`eligible_sharing`）；跨版本"范围未变"一个判断（`scope_unchanged`）；重审存活一个判断（`carried_review_alive`）；重审与新结果共用 `router.verify` 与 `LeafAcceptanceAssembly`；`BIND_EXISTING_GOAL` 与点名共用合并成一条 |
| 开发期不做旧数据兼容、旧路径直接删 | 基本符合，一处残留 | 迁移 42 旧验证记录记版本 0、迁移 43 去掉 JSON 里的旧键，都是一次性改写，不是读时兼容；验收编号换算法后旧库验收对不上，按开发期口径接受。残留：`ChildBinding.resolution_ref`（见 1.1 第 4 行）。`list_verifications(requirements_revision=None)` 读全部只供展示（`store.py:967-970` 写明），不算双路径 |

## 七、看到但不算需补

- **可能的静默等待**：派发处对"有通过结果、现行不算数、还没被打回"的步骤一律报 `CARRIED_REVIEW_PENDING`（`hierarchical_dispatch.py:2463-2471`），而扫描对"除要求外范围也变了""冻结版本读不出"的步骤直接跳过（`carried_review.py:76-80`、`:91-94`）——这种步骤会一直显示"等重审"却永远不审。目前改接已通过步骤的输入会被 `REPAIR_NOT_ALLOWED` 挡住，正常走不到；以后放开时要让派发处与扫描用同一判断。
- `carried_reviews` 读执行图结果失败时 `except Exception: return []`（`carried_review.py:60-63`），注释说"由它自己的路径报"，不吞写操作，暂可。
- 共用候选的状态 `running` 也包括还没开工的步骤（`OccurrenceOutcome.RUNNING` 是"未终结"），也可能列出子目标；点名后由执行图层开工许可 / 类型核对退回。提示词写的是"在跑的"，模型可能白丢一轮。
- `plan_preview.py:370-376` 与 `taskgraph_plan_sources.py:80-83` 的文档串还写"在跑或按现行要求通过"，没提第四批的第三种状态。
- `MissionsView.tsx:272` `BudgetByDuty` 的说明注释现在挂在 `spendLine`（`:274`）上面，位置错了。
- 方案文件首行标题仍写"第 3.3 版"，修订说明已到 3.6。
- 第三批施工中顺带改的两处（`_hierarchical_admission_context` 取第一个有报告的目标；WAIT 等待引用收紧为任务内容摘要）都是缺陷修复，实施记录写了，方案无需改。
- 台账《需求与场景状态》与 ARCHITECTURE 本次没有更新（评估员只允许写本文件），留给主会话。

## 八、需补清单

### 必须补

1. **第三批用例：点名读上游产出的步骤却没点名上游 → 退回、写明输入不同**（方案第 3 节用例第 3 条）。现在 `TASKGRAPH_SHARED_DATA_*` 在测试里 0 处引用。补一条单元或产品同形用例（被共用步骤带一条数据边，新做法只点名它不点名上游 → 退回且原因可读；再一并点名 → 通过），并配一条改坏。
2. **第三批用例：改接过输入（`REBIND_INPUT`）的步骤所在做法被换掉，换做法照常提交、不留悬空边**（方案第 3 节用例第 5 条）。这是 `_merge` 规则①"任一端离开网络就删"唯一的守护，现在没有用例也没有改坏。
3. **漏删 `ChildBinding.resolution_ref`**（`SDK/contracts/htn.py:1601`、`:1652-1657`、`:1672`、`:1702`；`SDK/planning/htn/graph_repair.py:357` 清空处一并改）：`BIND_EXISTING_GOAL` 删后它没有写入方，是"复用子目标结论"的残留，与"只共用普通步骤"矛盾。删掉（编码清单要再重写一次）；若有理由保留，在方案第 3 节"留"里写明。
4. **方案文字随施工改**（按"冲突先改方案"）：
   - 第 4 节第 1、6 条"新码登记错误码表"→ 改为现行口径（`CARRIED_REVIEW_PENDING` / `CARRIED_RESULT_REJECTED` 只作说明、不决定秩序，按偏离 25 不进表；新事件按表登记，验证表新列已登记）；
   - 第 3 节用例第 1 条"两个方向换做法"在产品世界的部分：要么补产品同形用例，要么在第 7 节加一条偏离写明"两个方向下沉到单元层（走真实 `_merge`），产品层留联测 F2"，并确认 F2 清单里有它。

### 可记为偏离 / 顺手清理

1. 第三批"负责写出的文件相同"：`criterion_files` 没传进做法落地，退回说明不写文件（方案说由要求推出、只作说明）——在第 7 节记一句偏离即可。
2. 第三批"会写文件的步骤共用后不被判成只读步骤还在写"只由产品用例走通隐含覆盖：可在 `test_shared_steps.py` 第一条加一句断言（共用步骤的任务绑定仍写 out.md）。
3. 第四批"审阅没有结论 → 问用户"有代码无用例（方案用例未列）：建议联测补一条，或在实施记录写明留到 F2。
4. 第四批变体用例做法不同（打回后用后继步骤、没有"换做法 + 点名其余旧叶子"的完整组合）：在实施记录四-4 写明，或改造 `test_carried_review.py:98` 为三步带下游的局面。
5. TG4-04 只守住四处要求版本查找中的一处：可在实施记录写明另三处靠什么守，或加改坏。
6. 第五批"要连同下游一起换"只在提示词里，退回说明是英文原句、用例只核码：可接受，记一句。
7. 第五批测试里的钉住残留文字：`T/acceptance_assets/seams_current.json:264`、`T/full_target/test_input_manifest_resolution.py:21`、`:730`（及 `:742` / `:755` 两条重复用例合一）、`T/product_world/test_input_revisions.py:40` 死参数 `pin`、`SDK/artifacts/input_bindings.py:605` 注释。
8. 第七节两处过时文档串（`plan_preview.py:370`、`taskgraph_plan_sources.py:80`）与 `MissionsView.tsx:272` 注释位置；方案首行版本号。
9. 第七节"可能的静默等待"：记入实施记录的已知风险，放开改接已通过步骤输入时一并处理。
