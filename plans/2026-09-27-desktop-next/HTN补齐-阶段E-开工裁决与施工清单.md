# HTN 补齐 · 阶段 E（中途改要求）：开工裁决与施工清单

- 裁决人：独立裁决子代理（只读代码与文档，除本文件外没有改任何文件、没有跑测试）。日期 2026-10-03。代码以工作树 `simple_harness-a4`（分支 htn-d，`5278851a`，SDK opt.145）为准；该工作树在核验中还在小修，**下文行号是"约"，以函数名为准**。
- 依据：`HTN补齐计划-2026-10-02.md`（第 3.12 版）阶段 E 全节、表二第 11、21 条、第一节"暂时不做"、第五节②、第六节；`TaskGraph-补全-方案.md` 第 3 批（第 4 节）；`HTN补齐-阶段D-开工裁决与施工清单.md`；`HTN补齐-实施记录.md` 阶段 D（第 8 条偏差）。
- 口径：判断交给 LLM，Harness 只管约束与秩序；同一件事只留一条路径、只记一处；旧路径直接删、不做兼容；做好的功能默认开启；用户已定的八项"暂时不做"不排进来。
- 路径缩写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`Host/` = `backend/deskpet/orchestration/`；`F/` = `tauri-app/src/`。

---

## 〇、一句话结论

阶段 E 能做，**共 10 步（E0～E9）；不需要新迁移（40 号不用）；编码清单不动；工具说明 `TOOL_SCHEMAS` 不动（执行池身份不变）；只升规划器提示词一次（v20→v21，规划包 11→12），执行者、审阅员不动；主 Agent 新增一个对话工具 `mission_amend`（这是主对话的产品工具，不是执行池工具）。**

改要求走"和建任务同一条规矩"：主 Agent 把用户的话整理成"增 / 改 / 删哪几条"，调用 `mission_amend` 一次提交写完要求第 n+1 版、根合同、根义务、作用域纪元和"要求已更新"事件；人的把关就是建任务时那道"确认完成要求"（手动模式人点；自动模式只有纯内容要求时由系统代确认，带 `action:` 的照旧等人点）。旧验收按"要求版本变了就不算数"这条现有秩序失效，系统如实标出"哪些是按旧要求通过的"；规划器若在新计划里沿用某一步，系统让审阅员按新要求重审它已有的结果（不重跑），重审没过就交回规划器。

偏差单 9 张，**没有需要用户本人决定的事**；4 条建议报用户知悉（第五节）。工期估 5～7 天（比计划写的 3～5 天多，多在第 4 步"沿用即重审"）。

---

## 一、开工前裁决

### 1.1 现状盘点：改要求现在完全没有入口，写方只有建任务

**结论：主对话、界面、SDK 门面都没有"改要求"入口；要求修订只在建任务时写第 1 版；"要求已更新"修复触发器在等一个全库没人写的凭证字段；派发侧没有任何要求版本闸门。**

- **写方只有一个**：建任务事务里保证通道工厂写第 1 版（`SDK/orchestrator/assurance_factory.py` `create`，约 :66-72），内容由 `SDK/deployment/root.py` `user_requirements`（约 :36-50）从章程 `mission.success_criteria` 按位置生成：编号 `c-user-<n>`、每条 `revision=1`、`USER_EXPLICIT`、全部必需、"全部满足"表达式；`initialize_root`（约 :56-101）比对同一份、不重复写。`amendment_credential_ref`（`SDK/contracts/resolution.py` `RequirementsRevision`，约 :455）全库无人写。
- **读方**：`HtnStore.get/latest/list_requirements_revision`（`SDK/storage/htn_store.py` 约 :914-935）约 40 处在读，见 1.1b。
- **"要求已更新"修复触发器**：`SDK/orchestrator/planning_repair_requests.py` `collect_triggers` 约 :498-504——逐版扫，只有带 `amendment_credential_ref` 的版本才产生 `RequirementsUpdated` 修复请求（明细是整份要求 JSON）。逻辑已在，缺写方。
- **已有的屏障**（不用新写）：
  - 规划请求绑定要求修订号（`event_handler.py` 约 :4088、:4481），回复迟到 → `REQUEST_BINDING_STALE`（`SDK/planning/decision_admission.py` 约 :799-813），阶段 D 已定"请求过期不算答错"；
  - 计划提交读集带要求修订号，提交时核（`SDK/orchestrator/_read_set.py` `_check_requirements` 约 :255）；
  - 根终审包绑要求修订号，变了报 `REQUIREMENTS_MOVED`、按版本另给切包额度（`SDK/orchestrator/root_review.py` `stale_reasons` 约 :607-631）；
  - 等回答的问题绑计划修订号与要求修订号（`SDK/storage/planning_human_store.py` `binding_current` 约 :54-61），变了标"过期"；
  - `requirements_revisions` 插入触发器给保证通道纪元 +1（`SDK/storage/assurance_barrier_v26.sql` 约 :504-523）；
  - 叶子与组合验收读集带要求修订号（`leaf_acceptance.py` 约 :715、`composition_review.py` 约 :411），提交时核。
- **缺的屏障**：派发。`HierarchicalDispatch.plan_view`（`hierarchical_dispatch.py` 约 :806-853）从不给 `ActivePlanView.requirements_revision` 赋值（恒 0，`SDK/graph/eligibility.py` 约 :279），`_same_origin`（约 :1440）两边都是 0；派发选择（`admissions` 约 :2414）与建尝试都不比要求版本。**改了要求，旧计划会继续派新尝试。**
- **完成映射是按版本的**：计划提交时冻结完成范围必须有"最新要求版本"的已确认完成映射，否则 `OP_REQUIREMENT_MAPPING_MISSING`（`SDK/orchestrator/operation_completion.py` `freeze_plan_completion_scopes` 约 :416-433）。确认页读的也是最新版（`SDK/api/operation_workspace.py` 约 :32-37，最新版无映射即显示 `CONFIRMATION_REQUIRED`）。但自动模式代确认只扫 `status='CREATED'` 的任务（`SDK/deployment/duties.py` `auto_confirm_content_completion` 约 :73），**任务进行中出了第 2 版，自动模式永远不会代确认**；开工闸门 `ready`（`SDK/deployment/root.py` `install_planning` 约 :108-125）只在 `_start_planning`（`event_handler.py` 约 :3932）用一次，之后的规划轮不看。

### 1.1b "完成标准"的来源与读取：逐处清单（"准则只留一个来源"的前提）

**结论：真正读章程 `Mission.success_criteria` 的地方共 25 处（SDK 20、Host 5）；其余 `success_criteria` 命中是步骤合同 `Task.success_criteria`，与本题无关。分三类：A 保留（初始输入与建任务）；B 必须改读要求修订；C 已经读要求修订。补全方案写的"SDK 约 30 个文件、95 处"把步骤合同也算进去了，实际要改的只有 B 类 17 处。**

**A 类：保留——只作"初始输入"、建任务门口，或第 1 版要求书的唯一构造**

| # | 位置 | 用途 |
|---|---|---|
| A1 | `SDK/api/missions.py` 约 :30、:63、:96 | 建任务请求校验、构造 `MissionSpec` |
| A2 | `SDK/api/facade.py` 约 :50、:68、:551 | 建任务请求字段表、密钥扫描 |
| A3 | `SDK/orchestrator/commit_service.py` 约 :205、:719 | `MissionSpec` 序列化、写 Mission 行 |
| A4 | `SDK/orchestrator/event_handler.py` 建任务门口（`_check_action_criteria` 调用处与 `pytest:` 检查，约 :1939-1945） | 建任务时能不能用 action/pytest |
| A5 | `SDK/orchestrator/assurance_assembly.py` 约 :188；`SDK/deployment/root.py` 约 :33、:43 | **章程 → 第 1 版要求书的唯一入口** |
| A6 | `SDK/verification/criteria.py` `mission_contract_revision` 约 :78-93；`SDK/orchestrator/tail_revision.py` 约 :40 | 受保护尾部的任务身份哈希（初始输入身份；去留归 G） |
| A7 | `Host/service.py` `_door` 约 :989-1028；`Host/chat_tool.py` 约 :44-68；`Host/skill_catalogue.py` 约 :196 | 建任务入参与门口检查 |
| A8 | `Host/diagnostics.py` 约 :244 | 诊断导出的"输入引用"哈希 |

**B 类：必须改读要求修订（最新版，或该处已绑定的那一版）**

| # | 位置 | 现在 | 改成 |
|---|---|---|---|
| B1 | `Host/hierarchical.py` `planning_world` 约 :26、:32；`SDK/testing/product_world.py` `user_goal_world` 约 :48、:54 | 规划世界的根目标、步骤类型签名的覆盖判据按章程位置算 | 根目标覆盖判据读最新要求修订；**步骤类型（prepare/continue）不再带本任务判据**（见 1.4） |
| B2 | `SDK/deployment/duties.py` `auto_confirm_content_completion` 约 :77 | 按章程判"有没有 action:" | 按确认页给的最新版 `criteria` 判；并扫进行中任务（1.3） |
| B3 | `SDK/planning/htn/planner_package.py` 约 :909 | 规划包 `mission.success_criteria` | 改为 `mission.requirements`：版本号 + 每条编号、条目版本、原文（规划包升 12） |
| B4 | `SDK/context/context_builder.py` 约 :233 | 执行者上下文 `mission_success_criteria` = 章程 | 键名不变，值取该步完成范围绑定那一版要求的原文（审阅按什么判，执行者就看什么） |
| B5 | `SDK/verification/criteria.py` `task_criterion_text` 约 :63-75；调用方 `SDK/runtime/action_schema.py` 约 :40-67、:152-163 | `c-user-<n>` 按位置回查章程 | 按编号查要求修订（删一条后位置就错了） |
| B6 | `event_handler.py` 规划世界读集里的 `criterion_files`（约 :2387） | 同上，按位置 | 按编号 |
| B7 | `event_handler.py` `_protected_seed`（约 :8109） | 章程里的 `pytest:` 目标 | 最新版 |
| B8 | `event_handler.py` `_worker_action_contract`（约 :8133） | 章程 | 最新版 |
| B9 | `event_handler.py` 收尾判断里"有没有 action"（约 :8736） | 章程 | 最新版 |
| B10 | `event_handler.py` `_evaluate_criteria`（约 :10153-10270）、`_decide_actions`（约 :10339）、`_judge_with_actions`（约 :10422） | 收尾逐条判定按章程 | 按根终审绑定的那一版（它必等于最新版，否则 `REQUIREMENTS_MOVED`） |
| B11 | `commit_service.py` `judge_mission` 约 :2977、:2990 | "判定必须按章程顺序覆盖"、最终报告 | 按同一版 |
| B12 | `SDK/orchestrator/action_commits.py` 约 :279 | 候选操作核对按章程 | 最新版 |
| B13 | `SDK/orchestrator/operation_materialization_inputs.py` 约 :262 | 同上 | 按该操作意图已绑定的那一版（`operation_intent_bindings.requirements_revision` 已记） |
| B14 | `hierarchical_dispatch.py` 约 :3290 | 做法链接与"要发布的文件归谁"按章程 | 最新版 |
| B15 | `Host/projection.py` 约 :26、:147 | 把章程原样作为 `mission.success_criteria` 给前端 | **删**（前端不渲染它；现行要求在确认页 `operation_workspace.criteria`，只留这一条路） |
| B16 | `Host/chat_tool.py` `mission_status` | 不给要求 | 增加"当前要求"（版本号、编号、原文），取自 `operation_workspace` |
| B17 | `SDK/testing/fixtures.py` 约 :175 | 读上下文包的同名键 | 跟着 B4，不改键名 |

**C 类：已经读要求修订，不改（备查）**：`root_review.py`、`assurance_purpose_reviews.py`、`completion_status.py`、`scoped_content_review.py`、`scoped_composition_review.py`、`leaf_acceptance.py`、`composition_review.py`、`api/operation_workspace.py`、`api/assurance.py`（实时视图读最新；"某时刻视图"读激活时那一版，属历史，不改）、`planning_human_store.py`、`taskgraph_plan_sources.py`/`taskgraph_sources.py`/`taskgraph_preview.py`/`taskgraph_outcomes.py`、`_read_set.py`、`plan_commits.py`、`operation_completion.py`、`operation_outcomes.py`、`system_operations.py`、`planning_graph_repairs.py`、`planning_runtime_block.py`、`assurance_check_policy.py`、`duties.py` 的做法检查策略（键里已带要求版本，约 :297-301）。

**界面、报告、终审、成功判定、规划包、审查包、根合同、义务各读哪**（题目要求逐处列）：界面 = 确认页 `operation_workspace.criteria`（C 类，已对）+ Host 投影里的章程（B15，删）；报告 = `judge_mission` 最终报告（B11）；终审 = 根终审包（C 类）+ 收尾逐条判定（B10）；成功判定 = 完成度读取（C 类）+ `judge_mission`（B11）；规划包 = B3 + `criterion_evidence`（取自类型签名，经 B1 修正）；审查包 = C 类；根合同 = 根绑定 `requirement_refs` 与签名覆盖判据（来自 B1 的世界，见 1.3）；义务 = 根义务 `requirement_refs`（建任务时写，1.3 改写）。

### 1.2 授权入口：主 Agent 发起，与"建任务"同一条规矩把关

**结论：入口只有一个——主 Agent 的新对话工具 `mission_amend`，经 Host 服务调 SDK 门面新方法 `amend_requirements`。任务页不另加表单（第五节偏差单 1）。凭证 = 这次修改命令的回执（记来源：主对话的运行号与调用号、当时的权限模式）+ 新版本的"完成要求确认"回执（手动模式人点；自动模式纯内容由系统代确认，`action:` 照旧等人点）。**

- 事实：主 Agent 建任务（`mission_start`）就是直接调同一个 `create_mission`，不需要额外点击（`Host/chat_tool.py` 约 :1-15、:96-133）；手动模式下对话工具本来就逐次请人确认，自动模式不弹（用户 2026-09-07 决定，`backend/deskpet/sdk_adapters/tool_authority.py`）；人对"要求"的把关在"确认完成要求"这一步（用户 2026-09-26：纯内容自动确认、操作类等人），确认按版本、计划提交前必须有（1.1）。
- 改要求的性质和建任务相同（都是"把用户的话写成要求书"），所以照搬建任务的规矩，不另造第二套：
  - **手动模式**：主 Agent 调 `mission_amend` 时对话里照常弹工具确认（人点一次）；提交后确认页出现第 n+1 版"等你确认完成要求"，人在对话卡片或任务页上点确认（`USER_CONFIRMED`）。
  - **自动模式**：工具不弹；第 n+1 版全是内容要求时，部署职责代确认，记 `HOST_AUTO_PERMISSION`；有 `action:` 的照旧等人点（SDK 已拒绝系统代确认带操作的映射，`operation_completion.py` 约 :182-185）。
- 补全方案原写"确认必须是用户的真实点击"，与用户 09-07"自动模式不弹任何授权提示"、09-26"纯内容自动确认"冲突。按计划第〇节"用户已作的决定 > 原计划"裁决如上（偏差单 2），报用户知悉。
- 门面方法只收主体固定为本机用户的命令（与 `approve_operation_completion_spec` 同一个门面主体），模型拿不到这个入口；规划器没有任何改要求的决定类型（补全方案第 3 批第 7 条已满足，不另加代码）。

### 1.3 一次提交写什么，同一事务

**结论：新文件 `SDK/orchestrator/requirements_amendment.py`，一个函数 `amend_requirements(orchestrator, *, mission_id, command_id, expected_requirements_ref, changes, reason, source, principal)`，在一个 `store.transaction()` 里依次写下列 7 样，任何一步失败全部回滚。不新增表、不改表结构。**

1. **核对（不写）**：任务存在且属本租户、不是终态；**根目标还没有采纳的结论、收尾没开始**（`adopted_goal_resolution` 为空且 `assured_closeout_pending` 为假）——已经在收尾的任务改要求一律拒，请用户开新任务（偏差单 7）；`expected_requirements_ref` 等于最新版（编号、版本、哈希都对上，否则"要求已被改过，请先读最新"）；`changes` 非空、每条编号存在、改后不重复、改后不为空；新文字过同一套门口检查（密钥、`pytest:` 需要隔离环境、`action:` 连接器已启用、发布文件前补 `file:`——Host `_door` 里这三段抽成一个函数，建任务与改要求共用）。
2. **命令回执**：`commit_receipts` 一行，`kind="requirements_amended"`，`commit_id=command_id`，回执正文含来源（`{"kind":"MAIN_AGENT","run_id","call_id","permission_mode"}`）、旧版引用、新版哈希、改动摘要。同一命令号重放返回原回执（照 `approve_operation_completion_spec` 的写法）。
3. **要求第 n+1 版**：未改的条目原样带过；"改写"保留编号、条目 `revision+1`、换原文；"删除"去掉；"新增"编号取 `c-user-<现有最大号+1>`，**编号永不复用**；表达式仍是"全部满足"（与第 1 版同一规则）；`authority_subject=principal`；`amendment_credential_ref=command_id`；`revision_id=req-<mission>-<n+1>`。经 `HtnStore.insert_requirements_revision` 写（唯一写方，触发器顺带给保证通道纪元 +1）。**要求书构造与 `user_requirements` 放在同一模块**（`deployment/root.py`），只有一份"每条必需、全部满足"的规则。
4. **根合同新版本**：先按新版本重建本任务的规划世界（`orchestrator._planning_world_factory(mission)`，世界在同一事务里读到第 n+1 版，见 B1），取根类型定义；写根绑定 `contract_revision+1`，`requirement_refs`=新版全部编号，`goal_signature`=新根类型的签名，`contract_hash` 照 `initialize_root` 的算法，**`adopted_method_instance_id` 原样带过**（做法换不换由规划器定）。网络读取本来就叠加"每个任务的最新绑定"（`hierarchical_dispatch.py` `_read_network` 约 :756-765），不需要动计划修订。
5. **根义务**：`ObligationStore` 新增一个写方 `revise_requirement_refs(mission_id, duty, refs)`，只改 `obligation_json` 里的 `requirement_refs`（屏障触发器会照常给通道纪元 +1）。
6. **作用域纪元**：`HtnStore.bump_epoch(mission, "mission", bumped_by="requirements:<revision_id>")`——第五节②已定"E 的要求修订提交是第二个写方"。效果：本任务所有见证、开工前提、对外交接（阶段 C 交接前核对）都要按新纪元重来。
7. **事件** `RequirementsAmended`（载荷：旧版号、新版号、新版哈希、命令号、增/改/删的编号），加进 `SDK/orchestrator/taskgraph_notifications.py` `_RECHECK_EVENTS`。

提交后：
- `_dispatch_for`（`event_handler.py` 约 :1290）的世界缓存按"任务 + 要求版本"取，版本变了就重建（不另写失效通知）；
- 下一轮 `collect_triggers` 自然产生 `RequirementsUpdated` 修复请求；**明细补两样由系统算的事实**：`previous_revision` 与 `changes`（按编号比两版：新增、改写、删除）——这是比对，不是语义判断。

### 1.4 已有计划、在跑的尝试、已通过的验收：怎么处理

**结论：系统只施加一条现有秩序——"验收按哪一版要求通过，就只在那一版下算数"（完成度读取本来就这样比，`completion_status.py` `_official_accept_review` 约 :160、`_compound_content` 约 :261）——并把它如实摆给规划器；重做什么由规划器定；沿用的那一步由审阅员按新要求重审已有结果。**

已有计划：
- 不作废、不自动改。根合同换了新版，根的旧做法还"采纳"着，但新加的要求它覆盖不到；规划器收到 `RequirementsUpdated` 修复请求，通常会对根发 `REPLACE_METHOD`（产品同形世界已能走通，`T/product_world/test_repair_replace_method.py`），也可以只改子目标。
- 新计划提交时，完成范围按第 n+1 版冻结（现有），前提是第 n+1 版的完成映射已确认（1.5 的规划闸门保证先确认后规划）。

在跑的尝试：
- 跑完照常归档；**不再为它切审查包**（1.5 第 4 条）：它的完成范围绑的是旧版，审出来的验收提交时也会因读集过期被拒，白花一次审阅调用。
- 新计划若沿用这一步，结果按下面"沿用"处理；不沿用就留作历史。

已通过的验收：
- **如实标出**：规划包 `accepted_results` 每行加 `requirements_revision`（这次验收按第几版通过），以及 `counts_under_current`（系统按上面那条秩序算出的真假，`SDK/orchestrator/planner_views.py` 约 :143-153）。
- **规划器决定**：要沿用某一步，就在新做法里共用那一步（`sharing_candidates`、`BIND_EXISTING_GOAL SHARE_ACTIVE`，或写出类型与参数相同的步骤，`SDK/planning/htn/grounding.py` `SharingSignature` 约 :188-228 按类型引用、参数、输入版本匹配，不看要求编号）；不沿用就安排新步骤。
- **沿用即重审**（新秩序，E4）：活动计划里一个叶子步骤，任务已有通过的结果、但那次验收的要求版本不等于它现在完成范围的版本 → 系统对**同一个结果**按新范围切一份内容审查包（包号本来就按"结果 + 要求版本"派生，`leaf_acceptance.py` `_package` 约 :551，旧包不受影响），交审阅员判；通过 → 写一条按第 n+1 版的新验收，完成度自然认它；没通过 → 不重开任务（任务行已完成、派发会以 `PREPARATION_ALREADY_ACCEPTED` 拒，`hierarchical_dispatch.py` 约 :2457-2465），而是记一条新的修复请求 `CarriedResultRejected`（明细：步骤、结果、审阅记录与不通过的条目）交规划器——重做哪一步、怎么做仍由规划器定。
- **为什么不按"每条要求的版本没变就继续算数"**：补全方案第 3 批第 5 条的字面可以这样读，但新增一条"全部用英文"就会让条目没变的旧结果不再合格——"这一步的结果在新要求下还对不对"是语义判断，必须交审阅员（偏差单 3）。

为让"共用旧步骤"在改要求后仍可能，**步骤类型不能随要求变**：
- 现在桌面的 `prepare-delivery` / `continue-delivery` 类型签名带"本任务全部内容要求"（`Host/hierarchical.py` 约 :32-41；同形世界约 :54-58），类型引用哈希随要求变 → 改要求后新步骤与旧步骤的共用签名永远对不上。
- 裁决：**步骤类型的覆盖判据置空**，和中间目标类型一样（`Host/hierarchical.py` 约 :59-64 的片 B 写法）；一步要负责哪些要求，只来自上级做法的链接（`occurrence_tasks.py` 约 :280-300、`leaf_acceptance.py` `criteria_for` 约 :201-240 已按链接取）。这是 C3 前置改造里"步骤类型"那一半，提前到 E（偏差单 4）；`operator_ref` 按工具算哈希、根类型等其余前置改造仍归 C3。
- 根类型的覆盖判据读最新要求（根本来就要换合同）。根类型引用随之变化后，世界重建要能装回挂在旧根类型上的已采纳做法——E0 第 8 条核；装不回就改为"根类型也不带判据、根的判据由根绑定给出"（照中间目标 `assigned_criterion_ids` 的路子），施工方照此做，不另开偏差单。

### 1.5 屏障：先后怎么保证

**结论：已有的五道照旧（1.1）；补四道新的，都是秩序。**

1. **派发闸门**：`plan_view` 给 `ActivePlanView.requirements_revision` 赋"活动计划读集里的要求版本"（`HtnStore.active_plan_revision(...).read_set.requirements_revision`，已存，`htn_store.py` 约 :557-577），另加 `current_requirements_revision`=最新版；就绪判断第一层在两者不等时对所有基本步骤给出不开工原因 `REQUIREMENTS_CHANGED`（新的就绪原因值，"为什么还不开工"自动带出；按阶段 A 第 4 条登记进错误码表，归"请求过期"类）。新计划提交后两者相等，闸门自动打开。
2. **规划闸门**：把开工闸门 `ready`（`deployment/root.py` 约 :108-125）从 `_start_planning` 挪到"发出规划请求"的唯一入口（E0 第 5 条找到它），每一轮都看——最新版没有已确认的完成映射，就不问规划器，任务显示"等确认完成要求"（确认页本来就会显示）。否则规划器出的计划必在冻结完成范围时被拒。
3. **自动代确认扫进行中任务**：`auto_confirm_content_completion` 的查询从 `status='CREATED'` 改为非终态任务；去重键本来就含版本与哈希（约 :93-96），不会重复签。
4. **审查切包闸门**：切叶子内容审查包前，若该步完成范围的要求版本不是最新版，不切（等新计划）。组合审查与根终审已有同类判断，不动。

先后关系一句话：改要求提交（纪元 +1、版本 +1）→ 在途规划回复按"请求过期"退回、不扣次数 → 在途审阅的验收提交按读集过期被拒 → 派发停 → 等确认完成要求 → 规划器按新版出计划 → 计划提交（读集核最新版）→ 派发恢复、沿用的步骤重审。等回答的问题因绑定的要求版本变了而过期；其中裁决题过期按阶段 C 已有出口"没有结论 → 交规划器"——**阶段 D 第 8 条偏差留下的用例在这里补**（用例 5）。

### 1.6 "要求修订让旧验收失效 → 知识过时"：链条自然成立，不补代码

**结论：成立，时点在新计划提交的那一刻；不加第二套判定。**

- 链条：`knowledge_standing` → `acceptance_is_current`（`SDK/memory/knowledge_standing.py` 约 :31-82）→ `read_occurrence_completion` 的 `preparation_acceptance_ids`。新计划的完成范围绑第 n+1 版，旧验收不再被保留（贡献身份含要求引用，`SDK/storage/operation_completion_store.py` 约 :977-989；且 `_official_accept_review` 比版本），于是 `acceptance_is_current` 返回 `step_no_longer_rests_on_this_acceptance` 或 `step_not_in_active_plan`，知识读出来就是"已过时"。
- 改要求到新计划提交之间，旧计划的范围仍绑旧版，旧知识仍"当前"。这段时间派发已停（1.5 第 1 条），没有新执行者能读到；在途执行者读到的也是旧版下成立的东西，它的结果不会在旧版下被接受（读集过期）。**不为这段窗口另写"要求版本变了就过时"的规则**——那会和完成度读取成为两套判定（偏差单 8）。
- 沿用并重审通过的步骤：旧知识照样过时（它的依据是旧验收）；新验收里审阅员确认的结论按阶段 C 的规则重新入库。

### 1.7 界面：预算去向、还没细化的目标

**结论：两样都在 SDK 门面快照里各加一个读模型，Host 白名单透传，前端在任务详情顶部那一块加两小段；不加新页面、不加按钮。**

- **预算去向**：`obligation_accounts`（`SDK/orchestrator/obligation_accounts.py`）已在门面快照里（`facade.py` 约 :705-707），但只有义务编号与四个数，没有名字；Host 投影白名单（`Host/projection.py` `project_detail` 约 :443-468）把它过滤掉了。
  - SDK：同文件加 `obligation_rows(store, mission_id)`，每行 `{obligation_id, parent_obligation_id, depth, label, lifecycle, attempts, failed_attempts, settled_tokens, unknown_usage_attempts}`；数从 `obligation_accounts` 取（同一函数，不另算）；`label` = 绑在这个义务上的任务的 `goal` 参数原文（截 60 字），根义务写"整个任务"；已取消、已被取代的义务照样列出（钱花在被换掉的做法上也是"去向"），`lifecycle` 原样给。门面快照加 `budget_by_duty`。
  - Host：`project_detail` 透传 `budget_by_duty`。
  - 前端（`F/views/MissionsView.tsx` 详情顶部，"预算"一行之后，约 :1015-1017）：一个默认收起的"预算去向"，按层级缩进，只列花过钱或试过的；每行"名字 · 已用 token · 尝试 n 次（失败 m 次）"；有"用量未知"的尝试时末尾加"另有 k 次用量未知"；最多 8 行，其余合成"其余 x 项"。
- **还没细化的目标**：唯一定义是 `SDK/planning/htn/refinement.py` `planning_frontier`（约 :64-79，执行图快照 `planning_frontier` 也用它，`SDK/api/taskgraph.py` 约 :295）。
  - SDK：门面快照加 `unrefined_goals`，用同一个函数对活动网络求值（读网络的方法与执行图快照同一个，E0 第 6 条确认），每行 `{occurrence_id, task_id, label}`。
  - Host 透传；前端在"等待原因"附近（约 :1023-1035）加一行"还没细化：A；B（规划器还没给它们定做法）"，为空不显示。执行图里已有的"等待拆分"标注不动（`F/views/liveGraph/model.ts` 约 :141、:159）。
- 对话卡片（`F/views/ChatMissionCard.tsx`）：`mission_amend` 的工具结果也挂这张卡（`F/components/MessageStreamPanel.tsx` 约 :765、`F/views/PrimaryChatView.tsx` 约 :211、`F/chat/messageVisibility.ts` 约 :34 三处工具名判断加上它）；卡片在要求版本大于 1 时显示"要求第 n 版"；确认仍是卡片里现有的 `OperationWorkspace`。

### 1.8 迁移、提示词、工具说明

**结论：不加迁移；编码清单不动；`TOOL_SCHEMAS` 不动；只升规划器提示词 v21 与规划包 12。**

- **迁移**：不需要。要求修订、回执、义务、绑定、纪元、事件都写进已有表；就绪原因是代码枚举。迁移 1～39 一字不改。
- **编码清单**：本阶段不改 `contracts/htn.py`、`state_machines.py`、`models.py`、`evidence_state.py`、`task_network.py`（`ActivePlanView` 在 `graph/eligibility.py`，不在清单内）。施工中若不得不碰这五个文件，合并成一次并在实施记录写明。
- **规划器提示词 v21**（`SDK/runtime/role_templates.py` 约 :113；规划包版本 `PLANNING_DECISION_PACKAGE_VERSION` 约 :419 从 11 升 12）一次写全：
  1. 请求包 `mission.requirements` 是当前要求（第几版、每条编号、条目版本、原文），取代原来的 `mission.success_criteria`；
  2. `repair_requests` 里的 `RequirementsUpdated`：用户改了要求，明细列出新增、改写、删除的编号；先前的计划是按旧版定的；根目标的 `criterion_evidence` 已是新版，做法要让新版每一条都落到步骤上，删掉的不用再覆盖；
  3. `accepted_results` 每行的 `requirements_revision` / `counts_under_current`：按旧版通过的结果在新版下不算数；想沿用某一步，就在新做法里共用它，系统会请审阅员按新要求重审它已有的结果、不重跑；不想沿用就安排新步骤；
  4. `CarriedResultRejected`：沿用的那一步按新要求重审没通过，审阅意见在明细里，由你决定怎么重做；
  5. 改要求后，之前问用户的题目会作废，需要的话重新问。
- **执行者、审阅员**：不升。执行者上下文 B4 只换值的来源、键名与格式不变；重审用的是同一种内容审查包。钉哈希的模板测试只重生成规划器那份；执行池身份基线不变。
- **主 Agent 的对话工具**（不是执行池工具）：新增 `mission_amend`（描述、参数表、中文确认文案），`mission_status` 描述加一句"返回当前要求的版本与编号，改要求前先读"。

---

## 二、现状核对表

| 计划说的 | 代码现在（文件:行） | 结论 |
|---|---|---|
| 授权入口 | 无；`amendment_credential_ref` 无人写（`resolution.py` 约 :455；`planning_repair_requests.py` 约 :498-504 在等它） | 新增主 Agent 工具 + 门面方法（1.2） |
| 四样同一事务 | 第 1 版在建任务事务里写；没有第 n+1 版的写方 | 新模块一个函数写七样（1.3） |
| 准则只留一个来源 | 章程读 25 处，B 类 17 处要改（1.1b） | E3 改完 + 守护测试 |
| 屏障 | 规划回复、计划提交、根终审、问题、验收读集已有；派发无（`plan_view` 约 :806-853 不赋值） | 补派发、规划、代确认、切包四道（1.5） |
| 旧验收自然失效 | 按整版比，已成立；但沿用的叶子会卡死（任务已完成、完成度不认、派发拒） | 如实标出 + 沿用即重审（1.4） |
| "要求已更新"进重查事件集 | `_RECHECK_EVENTS` 无此类 | 加 `RequirementsAmended`（1.3 第 7 条） |
| 知识过时 | 读时推出，新计划提交时成立 | 不补代码，补用例（1.6） |
| 预算去向 | 门面快照有、只有编号；Host 白名单滤掉 | SDK 读模型 + 透传 + 前端一段（1.7） |
| 还没细化的目标 | `planning_frontier` 在执行图快照里；详情顶部没有 | 门面快照加一个读模型（1.7） |

---

## 三、施工清单

**结论：10 步。最重的是 E3（17 处改读，跨 SDK 与 Host）和 E4（沿用即重审，含步骤类型去判据）。**

| 步 | 内容 | 依赖 | 迁移 | 编码清单 | 提示词 |
|---|---|---|---|---|---|
| E0 | 金丝雀核对 | — | 无 | 不动 | 不动 |
| E1 | 改要求的一次提交 + 门面方法 | E0 | 无 | 不动 | 不动 |
| E2 | 四道新屏障 | E1 | 无 | 不动 | 不动 |
| E3 | 准则只留一个来源 + 守护测试 | E1 | 无 | 不动 | 不动 |
| E4 | 如实标出 + 步骤类型去判据 + 沿用即重审 | E1、E2、E0 第 2～4、8 条 | 无 | 不动 | 不动 |
| E5 | 规划器提示词 v21、规划包 12 | E3、E4 | 无 | 不动 | v21 |
| E6 | Host：`mission_amend`、`mission_status`、透传 | E1、E7 | 无 | 不动 | 不动 |
| E7 | SDK 读模型：预算去向、还没细化的目标 | E0 第 6 条 | 无 | 不动 | 不动 |
| E8 | 前端 | E6 | 无 | 不动 | 不动 |
| E9 | 清单、文档、发版 | 全部 | 无 | 不动 | — |

### E0　金丝雀核对（半天～1 天；只跑单条用例或临时脚本，不改正式代码）
1. 产品同形世界两步任务：第 1 步验收后，用临时脚本直接写一份带凭证的第 2 版要求，跑几轮。确认三件事：出现 `RequirementsUpdated` 修复请求；旧计划仍在派发（派发无闸门）；自动模式不代确认、规划器出的计划在冻结完成范围时被拒。这条留作 E2 的回归用例。
2. 共用旧步骤：同一任务里规划器对根发 `REPLACE_METHOD`，新做法里写一个与旧叶子类型、参数相同的步骤（或走 `sharing_candidates`），确认旧占位被共用、同一任务号（阶段 D 用例 10 推迟的那一类）。**不通 → E4 第 3 条停，登记偏差单交裁决**；E1～E3、E5～E8 照做，E5 提示词去掉"沿用"一段。
3. 对一个已验收的结果，按另一版要求的完成范围走一次内容审查（`assurance_content_review.py` 约 :76 的切包路径）：包号不同、验收能写第二条（同一任务、不同要求版本，`acceptances_task_idx` 不唯一）、任务的 `accepted_result_id` 不变。
4. 沿用一个子目标时：它下面的叶子重审通过后，子目标的组合审查与结论是否会按新版自动重做（组合审查包绑要求版本，`composition_review.py` 约 :385-411）；不会则 E4 第 4 条照叶子的做法补一个触发。
5. 找到"发出规划请求"的唯一入口（`_try_planner_intent` 约 :3769 与 `_planner_round_on_committed_plan` 约 :3868 的公共下游），E2 第 2 条放在那里。
6. 门面快照里读活动网络的方法（与执行图快照 `current` 同一个），E7 用。
7. 找到切叶子内容审查包的唯一入口（保证通道的内容审查工作），E2 第 4 条与 E4 第 3 条放在那里。
8. 根类型签名随要求变化后，世界重建能否装回挂在旧根类型上的已采纳做法（1.4 末条），按结果选根类型的写法。
9. 把两处步骤类型的覆盖判据置空，单跑 `T/product_world/test_full_circle.py`、`test_sub_goal.py`、`test_write_targets.py` 三条，确认步骤负责的要求仍按链接给出、没有链接的步骤仍有 `LEAF_LOCAL_CRITERION`。

### E1　改要求的一次提交
1. `SDK/deployment/root.py`：抽出 `build_requirements(mission_id, revision, criteria, principal, credential)`，`user_requirements` 与改要求共用；加 `apply_changes(previous, changes) -> criteria`（增改删、编号不复用、条目版本 +1、去重与非空检查）。
2. 新文件 `SDK/orchestrator/requirements_amendment.py`：1.3 的七样，一个事务；拒绝码 `AMEND_REQUIREMENTS_STALE`、`AMEND_MISSION_TERMINAL`、`AMEND_AFTER_CLOSEOUT`、`AMEND_EMPTY`、`AMEND_UNKNOWN_CRITERION`、`AMEND_DUPLICATE`，按阶段 A 第 4 条登记进错误码表。
3. `SDK/storage/obligation_store.py`：加 `revise_requirement_refs`。
4. `SDK/api/facade.py`：加 `amend_requirements(command)`（字段固定：`mission_id`、`command_id`、`expected_requirements_ref`、`changes`、`reason`、`source`；门面主体固定，加 `@_native_root`，过密钥扫描）。
5. `event_handler.py` `_dispatch_for`：世界缓存键加要求版本。
6. `planning_repair_requests.py` 约 :498-504：明细加 `previous_revision`、`changes`。
7. `taskgraph_notifications.py`：`_RECHECK_EVENTS` 加 `RequirementsAmended`。
8. 重放覆盖清单（`SDK/observability/business_replay_inventory.json`）：`requirements_revisions` 加写方；`obligations` 加写方；`task_semantics` 加写方；`commit_receipts` 的新 kind；新事件登记。守护测试跟着过。

### E2　四道新屏障
1. `hierarchical_dispatch.py` `plan_view`：赋 `requirements_revision`（活动计划读集）与 `current_requirements_revision`；`graph/eligibility.py`：`ActivePlanView` 加字段、`ReadinessReason` 加 `REQUIREMENTS_CHANGED`、第一层判断；错误码表登记。
2. 开工闸门 `ready` 挪到 E0 第 5 条找到的入口，`_start_planning` 里的那次删掉（只留一处）。
3. `deployment/duties.py` `auto_confirm_content_completion`：扫非终态；判"有没有 action"改读 `workspace["criteria"]`（同时完成 B2）。
4. E0 第 7 条的入口：完成范围的要求版本不是最新版就不切包。

### E3　准则只留一个来源
1. 按 1.1b 的 B 类表逐处改（B1 中"根目标覆盖判据读最新要求"在这里做，"步骤类型去判据"在 E4）。
2. 两个世界构造（Host 与同形世界）共用一个 SDK 函数 `current_criteria(store, mission_id) -> tuple[(编号, 原文)]`，放在 `deployment/root.py`。
3. `verification/criteria.py` `task_criterion_text` 改为按编号查给定的要求修订（参数从"章程文字列表"改成"编号→原文"）；`runtime/action_schema.py` 调用方跟着改。
4. `Host/projection.py`：删 `success_criteria`（`MISSION_FIELDS` 与 `_mission`）；`Host/chat_tool.py` `mission_status` 加 `requirements`。
5. **守护测试**（SDK 一条、Host 一条）：扫源码，`Mission` 的 `success_criteria` 只允许出现在 A 类文件里；漏一处就红。

### E4　如实标出 + 步骤类型去判据 + 沿用即重审
1. `planner_views.py` 约 :143-153：`accepted_results` 每行加 `requirements_revision`、`counts_under_current`（用 `read_occurrence_completion` 判，不另写规则）。
2. 步骤类型去判据：`Host/hierarchical.py` 与 `testing/product_world.py` 的 `prepare-delivery`/`continue-delivery` 签名覆盖判据置空（E0 第 9 条已验证）；根类型按 E0 第 8 条的结果。
3. 沿用即重审：在 E0 第 7 条的入口加"活动计划里任务已有通过结果、但验收的要求版本不等于完成范围版本、且这一对（结果, 版本）还没有审阅记录"的扫描 → 对同一结果切包、走同一条审阅；通过照常写验收；不通过写 `CarriedResultRejected` 修复请求（来源键 `rereview:<审阅记录号>`，同一记录只记一次），触发类型登记照阶段 D 的 `WriteConflict` 一样走全（修复触发表、v3 覆盖清单、规划包说明）。
4. 子目标：按 E0 第 4 条结果，必要时补组合审查的同类触发。
5. 次数：每个沿用步骤每个要求版本最多重审一次（再没过就交规划器，不再自动重审）；审阅调用照常记账、受任务总额度约束。

### E5　规划器提示词 v21、规划包 12
1. `role_templates.py`：按 1.8 写全五点；规划包版本 11→12；`planner_package.py` 约 :909 改 `mission.requirements`（同时完成 B3）。
2. 钉哈希的模板测试按当前版本重生成；`TOOL_SCHEMAS` 不动；执行者、审阅员模板不动；Host 执行池身份字节测试应原样通过。

### E6　Host
1. `Host/chat_tool.py`：`MISSION_AMEND_TOOL_NAME="mission_amend"`、描述（只在用户明确要求改某个后台任务的要求时用；先用 `mission_status` 读当前要求的版本与编号；手动模式会请用户确认，带 `action:` 的要求需要用户在卡片上确认）、参数表（`mission_id`、`expected_revision`、`changes` 1～12 条、`reason`，不收多余字段）、`amend_mission(...)`：命令号 `chat-amend:<run_id>:<call_id>`，从快照的 `operation_workspace.requirements_ref` 取完整引用并核版本号。
2. `Host/service.py`：`amend_requirements(request)`，门口检查与建任务共用一个函数（1.3 第 1 条），调门面、`wake()`。
3. `backend/main.py`：照 `mission_start` 注册；`backend/deskpet/sdk_adapters/tools.py` 的 `PRODUCT_TOOL_NAMES`、`HOST_COMPOSED_TOOL_NAMES`，`tool_authority.py` 的常驻工具表与中文确认文案（"修改后台任务的要求：新增 x 条、改写 y 条、删除 z 条"）各加一项；钉住这些表的 Host 测试跟着改。
4. `Host/projection.py`：透传 `budget_by_duty`、`unrefined_goals`。

### E7　SDK 读模型
1. `obligation_accounts.py` 加 `obligation_rows`；`facade.py` `snapshot` 加 `budget_by_duty`、`unrefined_goals`（后者用 `refinement.planning_frontier`）。

### E8　前端
1. `MissionsView.tsx` 详情顶部两段（1.7）；`ChatMissionCard.tsx` "要求第 n 版"；三处工具名判断加 `mission_amend`；`OperationWorkspace.tsx` 标题带版本号。
2. 组件测试各一条（见用例 11）。

### E9　清单、文档、发版
1. 实施记录、`ARCHITECTURE/`、需求与场景状态（计划第六节）同次更新；写明"旧编排库因规划包升版不可用，联测用新建编排数据目录"（阶段 D 已要求新建，不额外增加负担）。
2. 本阶段末发版一次、Host 钉版；真机按第 3.5 版流程放到全部代码写完后（真机一局：任务中途在对话里加一条要求，看到出第 2 版计划并按新要求交付）。

---

## 四、功能性用例清单（11 条）

**结论：SDK 8 条跑在 `SDK/testing/product_world.py` 上、用 `SDK/testing/scripted_replies.py` 的分层脚本化通道（第 7 条是源码扫描）；Host 2 条；前端 1 条。**

| # | 用例名 | 文件 | 剧本 | 断言 |
|---|---|---|---|---|
| 1 | `test_amend_writes_everything_in_one_transaction` | `T/product_world/test_requirements_amend.py` | 两条要求的任务跑到第一份计划提交；门面 `amend_requirements` 改写 c-user-1、新增一条；参数化：①正常；②在写事件前用 `store.fault` 注入失败；③`expected_requirements_ref` 用旧的；④任务已在收尾 | ①第 2 版（c-user-1 条目版本 2、新增 c-user-3、凭证=命令号）、根绑定合同版本 2 且编号齐、根义务编号齐、作用域纪元 +1 且 `bumped_by` 为 `requirements:…`、一条 `RequirementsAmended`、一条回执；同一命令号重放回同一回执；②～④按名拒绝，七样一样都没写 |
| 2 | `test_amend_holds_dispatch_then_replans_and_delivers` | 同上 | 两步任务，第 1 步验收后改要求加 `file:extra.md`；规划器收到请求后对根 `REPLACE_METHOD`（全新步骤） | 改后到新计划前没有新尝试，`why_not_ready` 原因含 `REQUIREMENTS_CHANGED`；自动模式代确认第 2 版；规划器收到 `RequirementsUpdated`（明细带 `changes`），规划包 `mission.requirements` 是第 2 版、`accepted_results` 里第 1 步 `requirements_revision=1`、`counts_under_current=false`；新步骤执行者上下文 `mission_success_criteria` 是第 2 版原文；任务完成，根结论 `requirements_version=2`，最终报告按第 2 版逐条 |
| 3 | `test_manual_mode_waits_for_confirmation_before_planning` | 同上 | `auto=False`，改要求后跑几轮，再用门面以人的身份确认 | 确认前规划器一次都没被问；确认后才问并继续 |
| 4 | `test_carried_step_is_rereviewed_not_rerun` | `T/product_world/test_requirements_carry.py` | 新根做法共用旧的已验收步骤；变体：重审时审阅员打回，规划器随后换做法 | 那一步没有新尝试；按第 2 版有一份新审查包、一条新验收，任务完成；变体：一条 `CarriedResultRejected` 修复请求到规划器（明细带不通过条目），同一记录不重复记，规划器换做法后完成 |
| 5 | `test_inflight_reply_and_pending_question_after_amend` | `T/product_world/test_requirements_amend.py` | ①规划器第一次被问时（脚本回调里）提交改要求；②根终审判不下来出裁决卡，裁决前改要求 | ①一条 `REQUEST_BINDING_STALE`、不扣规划次数；②裁决题变"过期"，一条"没有结论"修复请求交规划器（补阶段 D 第 8 条偏差留下的用例） |
| 6 | `test_knowledge_goes_stale_when_new_plan_lands` | `T/product_world/test_requirements_amend.py` | 第 1 步结论被确认入库；改要求；新计划不沿用第 1 步 | 新计划提交前该知识 `CURRENT`；提交后 `STALE:step_not_in_active_plan`（或 `step_no_longer_rests_on_this_acceptance`），读工具目录里看不到它 |
| 7 | `test_charter_criteria_read_only_at_the_door` | `T/full_target/test_requirements_single_source.py` | 扫 SDK 源码 | `Mission.success_criteria` 只出现在 A 类文件 |
| 8 | `test_budget_by_duty_and_unrefined_goals` | `T/product_world/test_obligation_accounts.py`（追加） | 有子目标的任务，子目标还没细化时与一次失败后各取一次快照 | `budget_by_duty` 根行"整个任务"、合计等于 `obligation_accounts`、子目标行名字是它的 `goal`、被换掉的义务仍列出；`unrefined_goals` 与执行图快照 `planning_frontier` 相同 |
| 9 | `test_mission_amend_tool` | `backend/tests/orchestration/test_chat_mission_amend.py` | 对话工具调用：正常；版本号对不上；`action:` 连接器没启用；带密钥文字；同一调用重放 | 正常返回第 n 版与任务号；三种按名拒绝、库里没写；重放不重复写 |
| 10 | `test_projection_passes_budget_and_drops_charter` | `backend/tests/orchestration/test_projection.py`（追加） + 守护扫描 Host | — | 明细带 `budget_by_duty`、`unrefined_goals`，不带 `mission.success_criteria`；Host 源码只在 A 类位置读章程 |
| 11 | 前端三条小断言 | `F/views/MissionsView.test.tsx`、`F/views/ChatMissionCard.test.tsx` | 给定详情数据 | "预算去向"默认收起、展开后行数与文案对；"还没细化"为空不显示；`mission_amend` 工具结果出卡片且显示"要求第 2 版" |

**改坏检验（每个功能一条；改前 `cp` 备份，改后清 `__pycache__`，不用 git 恢复）：**

| 功能 | 改坏哪里 | 应失败的用例 |
|---|---|---|
| 一次提交同事务 | 根义务改写挪到事务外 | 1② |
| 编号不复用 | 新增编号取"条数 +1" | 1①（先删后加的子情形） |
| 派发闸门 | `plan_view` 不赋 `requirements_revision` | 2 |
| 规划闸门 | `ready` 只留在 `_start_planning` | 3 |
| 自动代确认扫进行中任务 | 查询改回 `status='CREATED'` | 2 |
| 准则单一来源 | 规划包改回读 `mission.success_criteria` | 2 与 7 |
| 如实标出 | `counts_under_current` 恒为真 | 2 |
| 沿用即重审 | 关掉重审扫描 | 4 |
| 重审不通过交规划器 | 不写 `CarriedResultRejected` | 4 变体 |
| 问题过期出口 | 裁决题过期不产生"没有结论" | 5② |
| 预算去向 | `budget_by_duty` 合计改为只算本级 | 8 |

只跑这些用例与被改文件直接对应的旧用例（如 `T/product_world/test_repair_replace_method.py`、`test_sub_goal.py`、`test_full_circle.py`、`test_knowledge_confirm.py`、`test_obligation_accounts.py`、`T/full_target/test_plan_commits.py`，Host 的 `test_chat_mission_start.py`、`test_auto_confirm_content_completion.py`、`test_handlers_contract.py`、`test_projection.py`、`test_pool_identity_bytes.py`）；不跑整目录、不跑全量；无关用例红了记下另行处理。

---

## 五、偏差单（9 条）

**结论：9 处与计划或补全方案字面不同，都已裁决；没有一条需要用户本人决定。第 2、3、4、6 条改变用户看得到的行为或阶段内容，建议报用户知悉。**

| # | 原文 | 发现的事实 | 裁决 | 理由 |
|---|---|---|---|---|
| 1 | 补全方案第 3 批第 1 条"界面任务详情和主 Agent 都能发起" | 编排的使用者是主 Agent；用户要界面简洁 | 只做主 Agent 入口（对话工具）；任务页只显示"要求第 n 版"与现有确认，不加改要求表单 | 一个入口、一条路径；人把关在确认完成要求那一步，任务页本来就有 |
| 2 | 补全方案"确认规则和第 1 批第 4 条一样，必须是用户的真实点击" | 用户 09-07：自动模式不弹任何授权提示；09-26：纯内容要求自动确认、操作类等人；主 Agent 建任务本来不需要额外点击 | 与建任务同一条规矩：手动模式工具确认 + 完成要求确认由人点；自动模式纯内容由系统代确认（记 `HOST_AUTO_PERMISSION`），带 `action:` 的等人点 | 计划第〇节"用户已作的决定 > 原计划"；不另造第二套把关（**报用户知悉**） |
| 3 | 补全方案第 3 批第 5 条"准则自带版本号，版本对不上的旧验收不算满足" | 完成度读取按整版比；按"每条版本没变就继续算"会让系统替审阅员判"旧结果在新要求下还对不对" | 保持整版比；规划器沿用某一步 → 审阅员按新要求重审已有结果（不重跑）；没过 → `CarriedResultRejected` 交规划器 | 语义判断交模型，系统只施加版本秩序（**报用户知悉**：改要求后沿用的每一步多一次审阅调用） |
| 4 | 计划阶段 C3 前置改造"桌面类型身份与任务无关"整体在 C3 | 步骤类型签名带本任务全部内容要求，改要求后类型引用哈希变、旧步骤无法共用，"沿用"不可能 | "步骤类型不带本任务判据"这一半提前到 E4；其余前置改造（执行者引用、编号、根类型）留 C3 | 不提前则规划器没有"保留"的选择，等于系统替它决定全部重做（**报用户知悉**：步骤审阅包里不再列"该步并未负责的要求"） |
| 5 | 补全方案第 3 批第 2 条"根合同新版本按 §6.4 走现有合同更新" | 没有现成的根合同更新路径；网络读取叠加每个任务的最新绑定 | 改要求事务里直接写根绑定新版本（合同版本 +1、编号、签名），已采纳做法原样带过 | 不碰计划修订；做法换不换由规划器定 |
| 6 | （计划未写）改要求能改到什么程度 | 要求修订里没有"目标"字段；根参数 `goal` 是根合同身份的一部分 | 只能增、改、删要求条目；改目标本身 = 新任务；根目标已有结论、收尾已开始的任务不再接受改要求 | 秩序上的边界，不是语义判断（**报用户知悉**） |
| 7 | 补全方案第 3 批第 4 条"在跑的尝试跑完照常归档" | 跑完的结果会照常切审查包、调审阅，验收提交时必被读集过期拒绝 | 加"完成范围版本不是最新就不切包"；新计划沿用这一步再走重审 | 少一次白花的审阅调用；同一结果只在新版本下审一次 |
| 8 | 计划阶段 C"要求修订让旧验收失效 → 知识过时" | 知识当前性是读时推出，跟随完成度读取 | 时点定在新计划提交那一刻；不为改要求到新计划之间的窗口另写规则 | 只留一套判定；窗口期派发已停，没有新执行者能读到 |
| 9 | 计划阶段 E"3～5 天" | 多出沿用即重审与步骤类型去判据 | 估 5～7 天，计划第七节表改 | 如实记 |

**主计划下一版（第 3.13 版）要改的文字（可直接粘贴）：**
- 标题改"第 3.13 版"。
- 表二第 11 条做法要点改为："主 Agent 对话工具 `mission_amend` 一个入口，与建任务同一条把关规矩（手动模式人确认；自动模式纯内容系统代确认，`action:` 等人点）；一次事务写要求第 n+1 版（带凭证）、根合同新版本、根义务编号、作用域纪元、`RequirementsAmended` 事件；准则只读要求修订（章程只作初始输入，守护测试钉住）；屏障补派发、规划、代确认、切包四道；旧验收按整版失效并如实标给规划器，沿用的步骤由审阅员按新要求重审已有结果，没过交规划器；步骤类型不带本任务判据（C3 前置改造的一半提前）"。
- 表二第 21 条做法要点改为："任务详情顶部加'预算去向'（按义务、含下级与被换掉的做法，读时推出，默认收起）与'还没细化的目标'（与执行图规划前沿同一函数）；对话卡片显示要求版本"。
- 阶段 E 第 1～3 条后加一句："按 `HTN补齐-阶段E-开工裁决与施工清单.md` 施工（10 步 E0～E9），偏差单 9 张已改入"。
- 阶段 C3+摘要"前置改造"一句末尾加："（其中'步骤类型不带本任务判据'已在阶段 E 做）"。
- 第七节工作量表 E 改为"5～7 天"。
- 修订记录加："**第 3.13 版（2026-10-03）**：阶段 E 开工裁决（`HTN补齐-阶段E-开工裁决与施工清单.md`）9 张偏差单改入：入口只在主 Agent；把关与建任务同规矩；旧验收整版失效、沿用即重审；步骤类型去判据提前到 E；根合同在改要求事务里写新版本；改要求的边界；不为过期结果切包；知识过时时点；工期。"

---

## 六、不在本阶段做的

| 事项 | 去处 |
|---|---|
| 类型与任务无关的其余前置改造（执行者引用按工具哈希、`c-user-<n>` 编号、根类型）、做法跨任务复用 | C3+摘要 |
| 子目标结论（resolution）跨要求版本直接沿用（不经重审）；"每条要求版本没变就继续算数" | 不做（偏差单 3） |
| 改目标本身；在任务页改要求 | 不做（偏差单 1、6） |
| 改要求场景的崩溃切点（七样同事务、重审切包）、24 行来源表里的需求行、真机一局 | F（真机按第 3.5 版流程放到全部代码写完后） |
| A6 受保护尾部的章程身份哈希去留；重放覆盖清单里新写方的事件内容核对 | G |
| 多模型、两人审批、NanoJev、42 组×3 局、金额、跨领域、组级管理员、学习 | 用户已定暂不做 |
