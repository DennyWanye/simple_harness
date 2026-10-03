# HTN 补齐 · 阶段 E（用户中途改要求）完成评估

- 评估人：独立评估子代理。只读代码与文档；除本文件外没有改任何文件，没有动 git。日期 2026-10-03。
- 评估对象：工作树 `simple_harness-a4`，分支 `htn-e`（HEAD `a425f7d8`，相对 `main` 10 个提交，`git diff main...htn-e`，48 个文件，+1634/−133）。评估时工作树干净。
- 对照依据：`HTN补齐计划-2026-10-02.md`（第 3.14 版）阶段 E 与第六节；`HTN补齐-阶段E-开工裁决与施工清单.md`（下称"清单"，以按偏差单 10 改过的版本为准）；`HTN补齐-阶段E-偏差裁决-沿用旧步骤.md`（下称"偏差单 10 裁决"）；`HTN补齐-实施记录.md` 阶段 E 三节（下称"施工记录"）。
- 路径缩写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`Host/` = `backend/deskpet/orchestration/`；`F/` = `tauri-app/src/`。行号均为 a4 工作树当前版本。
- 本次跑过的测试（都只跑单个文件，见第七节）：4 个旧测试文件（**9 红 1 绿**）、准则单一来源守护测试（1 绿）、规划包两个旧测试文件（70 绿）。其余全部靠读代码。

---

## 〇、结论

1. **主体做完了，方向是对的。** 改要求的一次提交（七样同一事务、编号不复用、重放回原回执、按名拒绝）、四道屏障（派发、规划、代确认、窗口里交回的结果）、规划包第 12 版与提示词第 21 版、派发处如实报"按旧版通过"、步骤类型去判据、主对话工具 `mission_amend`、预算去向与还没细化的目标、前端两段与卡片版本号，都落了地。偏差单 10 裁决的两条补充都按裁决做了。
2. **有 2 处必须改（挡合并）**：
   - **清单 B 类第 9 处没改**：收尾时"有没有操作要求"仍读建任务时的章程（`SDK/orchestrator/event_handler.py:8781` `current.success_criteria`）。后果是具体的：给一个纯内容任务**加一条 `action:` 要求**后，收尾走"纯内容"分支，逐条判定跳过操作要求，`judge_mission` 因"判定没按现行要求逐条覆盖"拒绝提交（`commit_service.py:2979-2981`），任务在收尾处每轮报错、完成不了。守护测试没抓到，因为它的匹配式只认 `mission.` / `spec.` / `charter.` 三种变量名（`T/full_target/test_requirements_single_source.py:26`），这里变量叫 `current`。
   - **改函数签名弄坏了 4 个旧测试文件共 9 条用例**（我实际跑过，见第七节）：执行者上下文构造多了必填参数、操作合同与 `task_criterion_text` 从"文字列表"改成"编号→原文"，但 `T/step04/test_retrieval_context.py`、`T/p32/test_action_scope_by_requirement_id.py`、`T/p32/test_p32_action_schema_context.py`、`T/p33/test_user_requirement_criterion_text.py` 没跟着改。这四个文件正是被改源文件直接对应的旧用例。只改测试即可。
3. **施工方自述的 8 条偏差都如实、可接受；"施工中修掉的 8 个问题"都是秩序层面的修正，没有语义规则，可接受。** 但按计划第六节流程，这 8 条目前只是"登记、交评估"，还没有走"裁决 + 计划升版"，合并前要补这道手续（见第六节）。
4. **没登记的偏差 19 条**（第三节）。除上面两处必须改的，另有 3 条建议合并前顺手改：Host 改要求时"发布前补写出文件"的规则另写了一份（没复用建任务那份）；用例 5② 没有断言"裁决题过期 → '没有结论'交规划器"（这是阶段 D 第 8 条偏差留到这里要补的）；自动代确认扫描写死了状态清单（含不存在的 `STOPPED`）。其余补登记即可。
5. **硬约束**：没有新增"遇到某种语义情况就那样办"的规则；没有兼容分支；`TOOL_SCHEMAS`、迁移 1～39 一字未改，没有新迁移；编码清单五个文件没碰。有两处轻微"同一件事两份"（发布补文件规则、完成度读不到时的两种兜底），见第五节。
6. **总评：改掉第六节"必须改"的 2 项（都很小：一行源码 + 守护测试正则 + 4 个测试文件）、补完偏差登记与计划升版后，可以合并发版。** 不需要返工任何一步的主体代码。

---

## 一、清单 E0～E8 逐条对照

判定：做了 / 部分 / 没做 / 做法不同。"登记"列：施工记录里有没有写。

### E0 金丝雀核对

| 条 | 判定 | 依据 | 登记 |
|---|---|---|---|
| 1 直接写第 2 版、看修复请求/派发/冻结 | 做了（以用例代证） | 用例 2、3 覆盖了三件事 | 否（轻微） |
| 2 共用旧步骤 | 做了，结论"不通" | 施工记录偏差单 10，已裁决 A | 是 |
| 3、4 | 作废 | 偏差单 10 | — |
| 5 找"发出规划请求"的唯一入口 | 做了，结论"不唯一" | 实际三条路径直接建规划请求；改成一个判断函数、各入口都问（施工记录"修掉的问题"第 5 条） | 是 |
| 6 快照读活动网络的方法 | 做了 | `SDK/api/facade.py:729-749` 用 `_dispatch_for(...).network` | 否（轻微） |
| 7 找切叶子内容审查包的唯一入口 | 没做（换了做法） | 没在切包入口加判断，改在"结果到达时"归档为被取代（见未登记 U6） | 否 |
| 8 根类型签名变后能否装回已采纳做法 | 做了（以用例代证） | 用例 2、4 里旧根做法仍在、换做法/换后继都走通；施工方偏差第 5 条 | 是 |
| 9 步骤类型置空、跑三条旧用例 | 做了 | 兜底改用本地判据 `SDK/orchestrator/occurrence_tasks.py:312-317`（修掉的问题第 8 条） | 是 |

### E1 改要求的一次提交

| 条 | 判定 | 依据 | 备注 |
|---|---|---|---|
| 1 `build_requirements` / `apply_changes` | 做了 | `SDK/deployment/root.py:36-50`（唯一构造，第 1 版 `user_requirements` :53-60 也走它）、`:79-127`（增改删、编号按"历来最大号"往上、条目版本 +1、非空与去重） | 另加 `current_criteria` / `current_statements` :63-76（E3 第 2 条） |
| 2 新模块七样一个事务 + 拒绝码 | 做了 | `SDK/orchestrator/requirements_amendment.py:58-141`：核对 → 要求第 n+1 版（带凭证）→ 根合同新版本（世界在同一事务里重建、做法原样带过）→ 根义务编号 → 作用域纪元 → 事件 → 回执；同一命令号重放回原回执、命令号挪用拒 | 多了 `AMEND_COMMAND_REUSED`、`AMEND_MISSION_UNKNOWN` 两个码（U16）；错误码表未登记（施工方偏差 3） |
| 3 `revise_requirement_refs` | 做了 | `SDK/storage/obligation_store.py:234-248` | — |
| 4 门面 `amend_requirements` | 做了 | `SDK/api/facade.py:408-437`：字段固定、`@_native_root`、主体固定、过密钥扫描 | — |
| 5 世界缓存键加要求版本 | 做法不同 | 没改缓存键；改要求事务提交后调 `forget_planning_world` 主动丢弃（`event_handler.py:1291-1294`，`requirements_amendment.py:140`） | 未登记（U5），效果等价 |
| 6 修复请求明细加上一版号与增改删 | 做了 | `SDK/orchestrator/planning_repair_requests.py:504-517`（按编号比两版，是比对不是判断） | — |
| 7 `_RECHECK_EVENTS` 加事件 | 做了 | `SDK/orchestrator/taskgraph_notifications.py:35` | — |
| 8 重放覆盖清单加写方 | 没做 | 守护测试只核表字段，照样通过 | 是（施工方偏差 7，归 G） |

### E2 四道新屏障

| 条 | 判定 | 依据 | 备注 |
|---|---|---|---|
| 1 派发闸门 | 做法不同 | `SDK/graph/eligibility.py:283-284` 加两个字段；`_stale_gate` :936-947 两版不等时给"绑定已过期 + 明细码 `requirements_changed`"；赋值在 `SDK/orchestrator/hierarchical_dispatch.py:854`、:857-873 | 没新增就绪原因枚举（施工方偏差 2，可接受） |
| 2 规划闸门 | 做法不同（可接受） | 一个判断 `_requirements_unconfirmed`（`event_handler.py:3788-3794`），四处都问：首次规划 :3961、规划意图 :3803、修复轮 :2981、等待唤醒 :3303 | 清单写"挪到唯一入口、删 `_start_planning` 那次"；实际没有唯一入口（修掉的问题第 5 条已说明） |
| 3 代确认扫进行中任务、按确认页这一版判 action | 做了 | `SDK/deployment/duties.py:73-94` | 状态清单写死且含不存在的 `STOPPED`（U17） |
| 4 切包闸门 | 部分 / 做法不同 | 结果到达时若要求已改且新计划未提交，归档为"被取代"、不解析不切包（`event_handler.py:7394-7406`）；做法审阅的包在窗口里按"没有完成范围"处理（`assurance_purpose_reviews.py:450-454`）。**叶子内容审查切包入口本身没加判断**（`SDK/orchestrator/assurance_content_review.py` 未改） | 未登记（U6）；改要求前已交、改要求后才切包的结果仍会白审一次，提交时被读集挡住，不会出错 |

另：要求待确认算"在等人"、不算停滞（`event_handler.py:2527-2530`，修掉的问题第 6 条）；只有现行计划里的步骤算"还在跑"（`event_handler.py:2501-2513`，**未登记**，U15）。

### E3 准则只留一个来源

| 条 | 判定 | 依据 |
|---|---|---|
| 1 B 类 17 处 | **16 处做了或做法不同，1 处（B9）没做** | 见下表 |
| 2 `current_criteria` 共用 | 做了 | `SDK/deployment/root.py:63-76`；Host 世界 `Host/hierarchical.py:28`、同形世界 `SDK/testing/product_world.py:50` 都用它 |
| 3 `task_criterion_text` 按编号 | 做了，但旧测试没改 | `SDK/verification/criteria.py:60-69`；调用方 `SDK/runtime/action_schema.py:39-67`、`:150-163`；**4 个旧测试文件红**（必须改 2） |
| 4 投影删章程、`mission_status` 加现行要求 | 做了 | `Host/projection.py` `MISSION_FIELDS` 与 `_mission` 删字段；`Host/chat_tool.py:172-176` |
| 5 守护测试 SDK、Host 各一条 | 做了，但 SDK 那条太窄 | `T/full_target/test_requirements_single_source.py`（只认三种变量名、整文件放行 `commit_service.py` 与 `verification/criteria.py`、`event_handler.py` 只按"恰好 2 处"计数）；Host `backend/tests/orchestration/test_chat_mission_amend.py:104-113` |

**B 类 17 处逐处结论**

| # | 位置 | 判定 | 依据（现行代码） |
|---|---|---|---|
| B1 | Host / 同形世界的根目标与步骤类型 | 做了 | 根覆盖判据读现行要求：`Host/hierarchical.py:28-41`、`SDK/testing/product_world.py:50-59`；步骤类型置空：`Host/hierarchical.py:43-45`、`product_world.py:61-63` |
| B2 | 自动代确认判 action | 做了 | `SDK/deployment/duties.py:91-94`（读确认页这一版原文） |
| B3 | 规划包 `mission.requirements` | 做了 | `SDK/planning/htn/planner_package.py:911`；取值 `SDK/orchestrator/planner_views.py:242-247` |
| B4 | 执行者上下文 `mission_success_criteria` | 做法不同 | `SDK/context/context_builder.py:205,235` 键名不变；值取**最新版**（`event_handler.py:9881`），不是清单写的"该步完成范围绑定那一版"。窗口里派发已停、窗口里交回的结果归档为被取代，实际等效（U4） |
| B5 | `task_criterion_text` 按编号 | 做了 | `SDK/verification/criteria.py:60-69`；`SDK/runtime/action_schema.py:39-67` |
| B6 | 规划世界读集 `criterion_files` | 做了 | `event_handler.py:2392-2396` |
| B7 | `_protected_seed` | 做了 | `event_handler.py:8157` |
| B8 | `_worker_action_contract` | 做了 | `event_handler.py:8181` |
| **B9** | **收尾判断"有没有 action"** | **没做** | **`event_handler.py:8781` 仍是 `current.success_criteria`（章程）** |
| B10 | `_evaluate_criteria` / `_decide_actions` / `_judge_with_actions` | 做了 | `event_handler.py:10255`、`:10285`、`:10385`、`:10468` |
| B11 | `judge_mission` | 做了 | `SDK/orchestrator/commit_service.py:2977-2981`（:2923 只是文档字符串，:2992 是报告字段名） |
| B12 | 候选操作核对 | 做了 | `SDK/orchestrator/action_commits.py:281` |
| B13 | 操作材料核对 | 做法不同 | `SDK/orchestrator/operation_materialization_inputs.py:263` 读现行要求，不是"意图绑定的那一版"（施工方偏差 4，可接受） |
| B14 | 做法链接与发布来源 | 做了 | `SDK/orchestrator/hierarchical_dispatch.py:3325-3327` |
| B15 | Host 投影删章程 | 做了 | `Host/projection.py`（`MISSION_FIELDS`、`_mission`） |
| B16 | `mission_status` 给现行要求 | 做了 | `Host/chat_tool.py:172-176`，描述 :111-118 |
| B17 | 测试夹具读同名键 | 不改（按清单） | `SDK/testing/fixtures.py:175` |

### E4 如实标出 + 步骤类型去判据 + 旧步骤如实报

| 条 | 判定 | 依据 |
|---|---|---|
| 1 `accepted_results` 加 `requirements_revision` / `counts_under_current` | 做了 | `SDK/orchestrator/planner_views.py:134-146`、`:164-168`；用完成度读取判，不另写规则 |
| 2 步骤类型去判据 | 做了 | 同 B1；没被做法链接到要求的步骤兜底本地判据（`occurrence_tasks.py:312-317`） |
| 3（新）派发处如实报 `ACCEPTED_UNDER_OLD_REQUIREMENTS` | 做了 | `SDK/orchestrator/hierarchical_dispatch.py:2477-2493`（用完成度读取的 `preparation_acceptance_ids` 区分）、`:2542-2550`；停滞交规划器的请求里看得到（现有出口带出 `withheld` 明细，用例 4 验证）；错误码表未登记（施工方偏差 3） |
| 原 3～5 | 作废 | 偏差单 10 |

### E5 规划器提示词 v21、规划包 12

| 条 | 判定 | 依据 |
|---|---|---|
| 1 四点 + 删第 20 版那句 + 包 11→12 | 做了 | `SDK/runtime/role_templates.py:113`（v21）、`:143-145`（第 1 点）、`:170-173`（第 3 点，`accepted_results` 两字段 + "不会自动带入新计划，也不会自动重跑"）、`:197-203`（第 2、4 点及"还留在计划里会一直停在按旧版通过、原因代码 ACCEPTED_UNDER_OLD_REQUIREMENTS"）、`:251`（BIND_EXISTING_GOAL 段删掉"内容步骤也可以这样共用……"）、`:430`（包版本 12）；原第 4 点 `CarriedResultRejected` 不存在 |
| 2 钉版测试、执行者与审阅员不动 | 做了 | 只改 `T/full_target/test_planning_decision_enablement_contract.py:50`；执行者、审阅员模板与 `TOOL_SCHEMAS` 未改 |

**偏差单 10 裁决两条补充**：补充一（提示词措辞）做了，措辞与裁决 2.3 一致；补充二（派发处如实报）做了，位置与判法与裁决 2.4 一致。

### E6 Host

| 条 | 判定 | 依据 |
|---|---|---|
| 1 `mission_amend` 描述、参数表、命令号、版本核对 | 做了 | `Host/chat_tool.py:186-258`；命令号 `chat-amend:<run>:<call>`；从快照取完整引用再核版本号 |
| 2 `service.amend_requirements` 门口检查共用 | 部分 | `Host/service.py:984-1012` 抽出 `_check_criteria`（pytest/action），密钥用同一个 `_refuse_secrets`；**"发布前补写出文件"在 :1129-1140 另写了一份**，没复用建任务的 `_with_publish_sources`（:82-100）（U7） |
| 3 注册与三张表、中文确认文案 | 做了 | `backend/main.py:8722-8761`；`backend/deskpet/sdk_adapters/tools.py`（两张表）；`tool_authority.py`（常驻表与"修改后台任务的要求：新增 x 条、改写 y 条、删除 z 条"） |
| 4 透传 `budget_by_duty`、`unrefined_goals` | 做了 | `Host/projection.py:454-455` |

### E7 SDK 读模型

| 条 | 判定 | 依据 |
|---|---|---|
| 1 `obligation_rows` + 快照两项 | 做了 | `SDK/orchestrator/obligation_accounts.py:65-107`（数字取自同一个 `obligation_accounts`）；`SDK/api/facade.py:729-749`、`:761-762`。**注意**：清单说"与执行图快照同一个函数"，事实不是——执行图的规划前沿用的是就绪层 `PlanningFrontier.compute`（`hierarchical_dispatch.py:592-593`、`graph/eligibility.py:1612-1623`），这里用的是 `planning/htn/refinement.py:64` 的"还没有做法的复合目标"。按"还没细化"的含义，后者是对的；但施工记录照抄了"同一函数"（U9） |

### E8 前端

| 条 | 判定 | 依据 |
|---|---|---|
| 1 详情两段、卡片版本号、三处工具名、确认页标题带版本号 | 部分 | 预算去向 `F/views/MissionsView.tsx:259-281`、`:1047`；还没细化 `:1048-1052`；卡片 `F/views/ChatMissionCard.tsx:140,148`；三处工具名改成共用集合 `F/views/chatMission.ts:10`。**确认页 `OperationWorkspace.tsx` 标题带版本号没做**（U14） |
| 2 组件测试 | 部分 | `F/views/BudgetByDuty.test.tsx`（2 条）、`F/views/ChatMissionCard.test.tsx` 追加 1 条；"还没细化为空不显示"没测；文件名与清单不同（U14） |

---

## 二、施工方自述的偏差与"修掉的 8 个问题"

### 2.1 自述 8 条偏差

| # | 内容 | 如实？ | 可接受？ | 说明 |
|---|---|---|---|---|
| 1 | 沿用 + 重审不做 | 是 | 是 | 已裁决（偏差单 10），计划已升 3.14 |
| 2 | 派发闸门不加新枚举，复用"绑定已过期"+ 明细码；`ActivePlanView` 另加两字段 | 是 | 是 | 原 `requirements_revision` 字段确有读集一致性用途，不动是对的 |
| 3 | 拒绝码与明细码未登记错误码表 | 是 | 是 | 核过：`SDK/contracts/error_table.py` 只收规划拒绝码，`PREPARATION_ALREADY_ACCEPTED` 等确实都不在 |
| 4 | B13 用现行要求 | 是 | 是 | 窗口里旧意图本来会因纪元 +1 失效 |
| 5 | 根类型仍带现行要求、世界按任务重建 | 是 | 是 | 用例 2、4 证明旧根做法仍能装回 |
| 6 | 桌面"预算去向"通常只有"整个任务"一行 | 是 | 是 | 但要知道：这项界面在桌面几乎没有逐项信息，价值要等 TaskGraph 补全 |
| 7 | 重放覆盖清单没加新写方 | 是 | 是 | 清单第六节本来就把事件内容核对归 G |
| 8 | 用例 1 注入点不同、用例 3 用"关掉代确认"代替手动模式 | 是 | 是 | 注入"写事件时抛错"确实验证了全部回滚 |

### 2.2 施工中修掉的 8 个问题

| # | 改动 | 位置 | 评价 |
|---|---|---|---|
| 1 | 窗口里完成度读取如实答"都没完成" | `SDK/orchestrator/completion_status.py:588-598` | 可接受；偏差单 10 裁决第六节已认可，仍是一处判定 |
| 2 | 做法审阅在窗口里按"没有完成范围"处理 | `SDK/orchestrator/assurance_purpose_reviews.py:450-454` | 可接受（与 1 同理；比较写在本地，没调 `requirements_changed`，轻微重复） |
| 3 | 冻结完成范围叠上库里最新合同 | `SDK/orchestrator/operation_completion.py:430-435` | 可接受；核过：计划提交里已有任务的绑定改写在冻结之前、同一事务里写入（`plan_commits.py:1169-1172` 与收回执行权），叠最新与"读计划时"一致 |
| 4 | 系统自己收回的结果不算步骤失败 | `SDK/orchestrator/planning_repair_requests.py:477-480` | 可接受；范围比改要求宽（所有"被取代"），施工方已写明是老问题 |
| 5 | 规划闸门一个判断、多处入口 | 见 E2-2 | 可接受 |
| 6 | 要求待确认算"在等人" | `event_handler.py:2527-2530` | 可接受 |
| 7 | 窗口里交回的结果归档为被取代 | `event_handler.py:7394-7406` | 可接受；它同时承担了 E2 第 4 条的大部分作用（见 U6） |
| 8 | 无链接步骤兜底本地判据 | `occurrence_tasks.py:312-317` | 可接受（复用已有的本地判据，不是新规则） |

---

## 三、没登记的偏差（19 条）

"处理"列：**改** = 建议改代码或测试；**登记** = 代码可留，补写进实施记录与计划。

| # | 内容 | 位置 | 处理 |
|---|---|---|---|
| U1 | **B9 没改**：收尾"有没有操作要求"仍读章程；加一条 `action:` 后收尾判定被拒、任务完成不了 | `event_handler.py:8781` | **改（必须）** |
| U2 | 守护测试太窄：只认三种变量名；整文件放行 `commit_service.py`、`verification/criteria.py`；`event_handler.py` 只按"恰好 2 处"计数 | `T/full_target/test_requirements_single_source.py:17-26` | **改（必须，随 U1）**：改成匹配任意 `.success_criteria`、显式排除 `task.`/步骤合同，或按"允许的行"而不是"允许的文件"放行 |
| U3 | 改签名弄坏 4 个旧测试文件 9 条用例 | 见第七节 | **改（必须）** |
| U4 | B4 读最新版而非"完成范围绑定那一版" | `event_handler.py:9881` | 登记（实际等效） |
| U5 | E1 第 5 条：缓存不按版本取键，改为改要求后主动丢弃世界 | `event_handler.py:1291-1294` | 登记 |
| U6 | E2 第 4 条：切包入口没加判断，改在结果到达时拦；改要求前已交、之后才切包的结果会白审一次（提交时被读集挡住） | `assurance_content_review.py` 未改；`event_handler.py:7394-7406` | 登记（或补一处判断 + 一条用例） |
| U7 | Host 改要求的"发布前补写出文件"另写一份，没复用 `_with_publish_sources`；施工记录写"与建任务共用门口检查"不完全属实。另：删掉某条 `file:X` 而保留"发布 X"不会被拦 | `Host/service.py:1129-1140` 对 `:82-100` | **改（建议）**：给 `_with_publish_sources` 加"已有要求"参数后两处共用 |
| U8 | Host `service.amend_requirements` 没有任何用例；工具层用例用的是假服务，用例 9 的"库里没写"只在假服务上成立 | `backend/tests/orchestration/test_chat_mission_amend.py:26-42` | 登记，建议补一条真服务用例 |
| U9 | "还没细化"与执行图"规划前沿"不是同一个函数（清单前提有误，施工记录照抄）；用例 8 没做"与执行图相同"的比对 | `facade.py:729-749`；`hierarchical_dispatch.py:592-593` | 登记（代码不改；文字改成"还没有做法的复合目标"） |
| U10 | 用例 1 缺"任务已在收尾"子情形（`AMEND_AFTER_CLOSEOUT` 无用例），"任务已结束"也没测 | `test_requirements_amend.py:185-256` | 登记，建议补 |
| U11 | 用例 2 少两条断言：不开工原因含 `requirements_changed`；新步骤执行者上下文是第 2 版原文 | `test_requirements_amend.py:332-387` | 登记，建议补 |
| U12 | 用例 5② 没断言"裁决题过期 → '没有结论'修复请求交规划器"，对应改坏检验没做；这是阶段 D 第 8 条偏差留到这里补的 | `test_requirements_amend.py:546-614` | **改（建议）**：加一条断言 + 改坏检验 |
| U13 | 改坏检验与清单不同（见第四节）；实施记录没写做了哪些改坏检验 | 施工记录 E-1 | 登记 |
| U14 | 前端：确认页标题带版本号没做；"还没细化为空不显示"没测；测试文件名不同 | `F/views/OperationWorkspace.tsx` 未改 | 登记 |
| U15 | "只有现行计划里的步骤算还在跑"（停滞判断改动），不在 8 个问题里 | `event_handler.py:2501-2513` | 登记 |
| U16 | 多两个拒绝码 `AMEND_COMMAND_REUSED`、`AMEND_MISSION_UNKNOWN` | `requirements_amendment.py:72-91` | 登记（无害） |
| U17 | 代确认扫描写死状态清单（含不存在的 `STOPPED`），不用 `TERMINAL_MISSION`；且每轮对每个未结束任务取一次完整快照（快照现在还多读网络与义务表），以前只扫 CREATED | `SDK/deployment/duties.py:73-80` | **改（建议）**：用 `TERMINAL_MISSION`；取快照前先按"最新要求版本 + 哈希"查 `completion_done`，已处理的跳过 |
| U18 | 同一读数两处兜底相反：完成度读不到时规划包报"不算数"，派发处报"已通过等完成" | `planner_views.py:139-145`；`hierarchical_dispatch.py:2542-2550` | 登记，建议统一 |
| U19 | 用例 10 放进 `test_chat_mission_amend.py` 而不是 `test_projection.py` | — | 登记（无害） |

---

## 四、用例与改坏检验

### 4.1 改过的用例表（11 条）

| # | 清单用例 | 实际 | 判定 |
|---|---|---|---|
| 1 | 一次提交同事务（①正常 ②注入失败 ③旧引用 ④收尾中） | `test_amend_writes_everything_in_one_transaction`：①②③都有，另测了编号不存在、先删后加、删光再加不复用、重放 | 部分：缺④（U10） |
| 2 | 停派发 → 重排 → 交付 | `test_amend_holds_dispatch_then_replans_and_delivers` | 部分：缺"不开工原因"与"执行者上下文第 2 版"两条断言（U11）；其余（代确认来源、规划包第 2 版、`(1, False)`、修复明细、根结论第 2 版、终报逐条）都有 |
| 3 | 手动模式确认前不规划 | `test_planning_waits_for_the_amended_requirements_to_be_confirmed` | 做法不同（关掉代确认代替手动模式，施工方偏差 8） |
| 4 | 留在计划里的旧步骤如实报 | `test_kept_old_step_is_reported_not_rerun` | 做了，断言与改过的清单一致 |
| 5 | 在途回复与待答问题 | `test_inflight_reply_and_pending_question_after_amend` | 部分：①做了；②只断言题目变"过期"和任务完成，没断言"没有结论"交规划器（U12） |
| 6 | 知识过时 | `test_knowledge_goes_stale_when_requirements_are_amended` | 做了（时点在改要求那一刻，与裁决一致）；"读工具目录里看不到"没断言（轻微） |
| 7 | 准则单一来源守护 | `test_charter_criteria_read_only_at_the_door` | 写了，但漏抓 B9（U2） |
| 8 | 预算去向与还没细化 | `test_budget_by_duty_and_unrefined_goals` | 部分：没比对执行图前沿（U9），"子目标行名字""被换掉的义务仍列出"因桌面只有一行而没法断言（施工方偏差 6） |
| 9 | `mission_amend` 工具 | `test_amend_sends_one_sourced_command_and_replays_it`、`test_refusals_are_named_and_write_nothing`、`test_status_gives_the_current_requirements_and_the_tool_is_registered` | 做了，但跑在假服务上（U8） |
| 10 | 投影透传与 Host 守护 | `test_projection_passes_budget_and_drops_charter`、`test_host_reads_the_charter_only_at_the_door` | 做了，放的文件不同（U19） |
| 11 | 前端三条 | `BudgetByDuty.test.tsx` 两条、`ChatMissionCard.test.tsx` 一条 | 部分（U14） |

另加的一条：`test_a_result_that_lands_while_requirements_are_unconfirmed_is_not_reviewed_under_the_old_ones`（窗口里交回的结果归档为被取代、不白审、不算停滞），有价值。

### 4.2 改坏检验

施工方称做了 9 条；实施记录里没有记录，无法核对是否真跑过。下面按读代码判断"这条改坏后对应用例会不会红"：

| 施工方的改坏 | 对应清单哪一行 | 对应用例会红吗 |
|---|---|---|
| 根义务不改 | 清单是"根义务改写挪到事务外"（验同事务） | 会红（用例 1 断言义务编号），但验的是"写没写"，不是"同不同事务"；同事务由注入失败那段证明，只是那段没有配套改坏 |
| 编号只看上一版 | 编号不复用 | 会红（删光再加应得 c-user-6） |
| 派发闸门关掉 | 派发闸门 | 大概率会红（窗口里会派新尝试） |
| 规划闸门关掉 | 规划闸门 | 会红（用例 3） |
| 代确认只扫新建 | 代确认扫进行中 | 会红（用例 2 断言代确认来源） |
| 规划包读回章程 | 准则单一来源 | 会红（用例 2 + 守护） |
| 算不算数恒真 | 如实标出 | 会红（用例 2 断言 `(1, False)`） |
| 旧步骤细分码改回 | 旧步骤如实报 | 会红（用例 4） |
| 未细化恒空 | （清单是"预算合计只算本级"） | 会红（用例 8） |

**清单要求而没做的**：问题过期出口（5②，用例本身也没断言，U12）；"预算合计只算本级"（桌面只有一行，这条改坏本来也测不出，换成"未细化恒空"合理）。登记在 U13。

---

## 五、硬约束核对

| 约束 | 结论 | 依据 |
|---|---|---|
| 判断交给模型、系统只管秩序 | 符合 | 新增的都是秩序：版本比对（整版比、按编号比增改删）、闸门、如实标出、按名拒绝。"重做哪一步"全交规划器；提示词只陈述事实与后果。没有关键词匹配或按任务内容分支（`action:` / `file:` / `pytest:` 是结构化前缀，沿用既有约定） |
| 同一件事只留一条路径 | 基本符合，两处轻微 | ①"发布前补写出文件"在 Host 有两份（U7）；②完成度读不到时规划包与派发处兜底相反（U18）。另"还没细化"与执行图前沿两个定义是旧有的、含义也不同（U9），不算本阶段新增 |
| 开发期不做兼容 | 符合 | 没有新旧双分支；规划包直接升 12、旧库不保证能跑。`current_criteria` 在"还没有第 1 版要求书"时按章程编号（`root.py:69-70`），这是建任务事务里非保证通道任务先建世界、后写第 1 版的正常顺序（`initialize_root` :148-171），不是旧数据兼容 |
| `TOOL_SCHEMAS` 不改 | 符合 | 未出现在改动中；执行者、审阅员模板未改 |
| 迁移 1～39 不改、不新增迁移 | 符合 | 改动中没有任何迁移文件 |
| 编码清单五个文件不碰 | 符合 | `contracts/htn.py`、`state_machines.py`、`models.py`、`evidence_state.py`、`task_network.py` 均未改；`ActivePlanView` 在 `graph/eligibility.py` |
| 部署清单 | 已重生成 | `taskgraph_deployment_manifest.json` 随源文件哈希更新、新文件已登记 |

---

## 六、能不能合并

**能，改完下面 2 项（必须改）并补手续后合并发版。**

### 6.1 合并前必须改（只列真正挡合并的）

1. **B9 改读现行要求，并收紧守护测试。** `SDK/orchestrator/event_handler.py:8781` 的 `current.success_criteria` 改为 `current_statements(self.store, current)`；`T/full_target/test_requirements_single_source.py` 的匹配式改成能抓到任意变量名读 Mission 章程（例如匹配所有 `.success_criteria`，再按行显式放行建任务门口、`Task`/步骤合同与第 1 版构造），确认改前红、改后绿。可顺带在用例 2 的剧本里加一个"改要求加 `action:`"的小变体，或至少用守护测试钉住。
2. **修 4 个旧测试文件的 9 条红用例**（只改测试）：`T/step04/test_retrieval_context.py` 给 `build_worker_package` 传 `mission_requirements`；`T/p32/test_action_scope_by_requirement_id.py`、`T/p32/test_p32_action_schema_context.py` 把 `mission_criteria` 改成"编号→原文"；`T/p33/test_user_requirement_criterion_text.py` 改成按编号查的新语义（"编号不存在照原样返回"仍可测）。

### 6.2 合并前手续（不改代码）

- 按计划第六节"偏差只允许已登记、已裁决、计划已改的"：施工方 8 条偏差与本文第三节里标"登记"的各条，写进实施记录，交裁决（本文第二、三节的判断可作依据），计划升第 3.15 版并在修订记录写明。
- 实施记录补一份改坏检验清单（做了哪几条、各自让哪条用例变红）。

### 6.3 建议合并前顺手改（不挡合并）

- U7：两处"发布前补写出文件"收成一个函数。
- U12：用例 5② 加"没有结论交规划器"的断言与改坏检验。
- U17：代确认扫描用 `TERMINAL_MISSION`，并在取快照前按最新要求版本跳过已处理的任务。

---

## 七、本次跑过的测试

都在 `sdk/simple-harness-sdk` 下用 `PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q -p no:cacheprovider <文件>` 单独跑，没有写任何文件：

| 文件 | 结果 | 原因 |
|---|---|---|
| `tests/orchestrator/step04/test_retrieval_context.py` | 2 红 1 绿 | `build_worker_package` 缺新必填参数 `mission_requirements` |
| `tests/orchestrator/p32/test_action_scope_by_requirement_id.py` | 2 红 | 传的是文字列表，新代码要"编号→原文"（`.values()` 报错） |
| `tests/orchestrator/p32/test_p32_action_schema_context.py` | 4 红 | 同上 |
| `tests/orchestrator/p33/test_user_requirement_criterion_text.py` | 1 红 | `task_criterion_text` 第二个参数从列表改成映射 |
| `tests/orchestrator/full_target/test_requirements_single_source.py` | 1 绿 | 证明守护测试没抓到 B9 |
| `tests/orchestrator/full_target/test_planner_package_single_layer.py`、`test_planner_package_refs_and_fields.py` | 70 绿 | 确认规划包升 12 没弄坏这两组旧用例 |

没有跑新写的 `test_requirements_amend.py` 等重用例（按纪律以读代码为主）。

---

## 八、说明

- 计划第六节要求"评估子代理顺带更新台账 `需求与场景状态.md`"；本次按用户指示只写本评估文件，台账没有更新，留给施工方在补手续时一并处理。
- 发版前照例补 `ARCHITECTURE/` 与实施记录（清单 E9）。
