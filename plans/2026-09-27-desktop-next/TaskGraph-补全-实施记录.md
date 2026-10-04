# TaskGraph 补全 — 实施记录

方案：`TaskGraph-补全-方案.md` 第 3.2 版（施工中补到第 3.4 版，见方案修订说明）（评估 `TaskGraph-补全-方案-评估-1.md`、`-评估-2.md`）。分支 `tg-1`（worktree `simple_harness-a4`），基于 main `e2d869a3`（SDK opt.150）。用户 2026-10-04 同意开工。

方案评估做了两轮：第 1 轮阻断 5、重要 11、建议 5，全部改入（第 3.1 版）；第 2 轮只核阻断，第 1 轮的 5 条到位，又引出 5 条小阻断，照评估员给的文字原样改入（第 3.2 版）。按"核验最多两轮、只挡大错"不开第三轮，第 3.2 版即施工依据。

## 第一批　错误码表第二步：一轮故障按类型码处理

### 一-1　改动
- `SDK/contracts/error_table.py` 新增一节：`RoundFaultHandling`（`CORRUPT_STOP` / `RETRY_IN_PLACE`）、`RoundFaultCode`（8 个码）、表 `ROUND_FAULTS`、带码异常的共同基类 `CodedFault`、`round_fault_handling(error)`——只看异常对象上的类型码：登记过的按表；没带码、或带了没登记的一律原地重试（码照实带出）。
- 清单只追决定秩序的码：原来 `classify_round_fault` 按异常文字包含判"损坏"的 5 项，加上原来靠 `isinstance(GraphIntegrityError)` 判损坏的执行投影排不出先后（`projection_not_orderable`）和计划意思读不全的两种（`semantic_binding_missing`、`root_not_identified`）。其余内部码只作消息，不改变秩序，不归类。
- 带码异常：`storage/taskgraph_store.GraphIntegrityError`（历史完整性）、`graph/projection_validation.GraphIntegrityError`（投影排不出先后）、`storage/taskgraph_attempt_inputs.AttemptInputIntegrityError`、新 `storage/taskgraph_history_sources.SourceIntegrityError`、新 `storage/store.StoredResultCorrupt`（原来是一句"corrupt stored result"的 `StoreError`）、新 `orchestrator/event_handler.ServiceTurnIdentityMismatch`（两处）。`hierarchical_dispatch.PlanIntegrityError` 的码按实例给，构造时转成登记过的 `RoundFaultCode`，没登记当场报错。异常文字原样保留（只给人看）。
- `orchestrator/failure_classes.classify_round_fault` 改为返回 `(CORRUPT|RETRY, 码)`，只调 `round_fault_handling`，删 `_CORRUPT_CODES`；一轮故障事件 `MissionRoundFault` 加 `code` 字段。计划完整性停止的报告 `detail.code` 改为直接取类型码（原来对非投影错误是按文字冒号切出来的）；投影错误的码沿用 `projection_not_orderable` 字面，执行图那两处读它的地方不变。

### 一-2　用例
- 新 `T/full_target/test_round_fault_codes.py` 4 条：表全集；源码扫描（基类里有 `CodedFault` 的类必须写 `code = RoundFaultCode.<登记过的名字>`，扫到 8 个）；扫描能抓住没登记的码与没写码的类；分类函数源码不读异常文字，行为三种（登记的损坏码 → 当轮停；文字里写着损坏码、对象上没码 → 原地重试；带未登记码 → 原地重试并带出码）。
- `T/product_world/test_round_faults.py`：启动绑定那条用例原来抛一句带码文字的普通异常，改为抛 `ServiceTurnIdentityMismatch`；"计划历史损坏只停它自己"那条把等主循环结束时的异常先记下、先断言任务状态，再断言主循环没崩（否则改坏时报的是等待超时、执行器判"无效"）。
- 相关定向用例 368 条：367 过；1 条是阶段 G 就记过的老失败（`test_hierarchical_event_flow.py::test_a_mission_whose_root_meaning_does_not_read_back_stops_through_the_planner_branch`，任务停在"已创建"，主分支同样失败）。
- 改坏 TG3-01（表里"历史完整性"改成原地重试）KILLED。改坏状态下任务按原地重试走、要 2 分钟才按上限停，用例只等 30 秒——查过日志，没有出错漏出按任务边界。

## 第二批　小清理

### 二-1　改动
- **删试用次数**：`registry.py` 的 `note_trial_use` / `trial_uses` / `MethodCandidate.trial_uses` 与计数表、`taskgraph_plan_sources.py` 规划来源摘要里的 `trial_uses` 删除；用例 `test_htn_novel_method_admission.py` 两条计数用例合成一条"候选标明是试用范围"。
- **删需求方读取接口**：`TaskGraphStore.read_active_consumers` 删除；来源表 P15 的差异说明改写（偏离 22）。
- **写入目标默认规则**：核实无误、不改代码——没有显式规则时 `hierarchical_dispatch.target_rules_policy()` 给出 `TASK_WORKSPACE_V1`，启用执行图时 `taskgraph_policy_sources.installed_policy` 把它按内容摘要冻结成 `target_policy_ref`，读不到层级派发或部署策略即 `SourceUnavailable('taskgraph_installed_policy_source_missing')`。偏离 23 定稿。
- **"有没有绑定执行图"只留一个判断**：`taskgraph_enabled` 删；`require_bound` 是唯一的判断，没绑定抛 `NotBoundError`、内核版本不认抛 `KernelUnsupportedError`（都是带码异常，登记在一轮故障表里，按"数据损坏、按名停"）。7 处调用：
  - 补记账扫描（`accounting_recovery._import_hold`）、结清已核清动作（`taskgraph_action_settlement.settle_resolved_actions`，顺带改成逐任务包进一轮故障边界）、保证通道内容审批扫描（`deployment/duties.py`）、执行图读页面（`api/taskgraph._require_enabled`，答"此任务没有执行图"）、启动时跳过不服务的任务（`event_handler._domain_unreadable`）、停任务时的预留处理（`_prepare_terminal_ledger`）、关口（`_refuse_unsupported_contract`，按名停 `unsupported_unbound_mission`）——都调 `require_bound`，不该报错的捕获 `NotBoundError` 照原意处理。
  - 用例 `test_terminal_event_transaction.py` 对已不存在的 `taskgraph_dispatch.taskgraph_enabled` 打桩的那一行删掉（桩本来就没有作用）。
- **计划完整性停止也停"已创建"的任务**：`_plan_integrity_stop` 遇到还在"已创建"的任务先进规划再停（与关口同样做法）。阶段 G 记下的老失败 `test_a_mission_whose_root_meaning_does_not_read_back_stops_through_the_planner_branch` 由此修好。
- **删做法建议**：`registry.suggest_for`、`SuggestionReason`、`MethodSuggestion` 与提做法上下文的 `suggested_method_refs` 删除。核实：它列的是本任务里的旧版本做法（规划器引用就被判"做法过期"、白丢一轮，2026-10-03 产品同形世界跑出来过）和已暂停 / 不可取用的做法；跨任务的做法目录在阶段 C3 已有，按"一条路径"删。`statement_similarity` 随第三批删 `SharedGoalIndex` 一起删。

### 二-2　与方案的施工差异
- 方案写"全局扫描逐任务包进一轮故障边界，一个没绑定的老任务只报它自己"。实跑发现：没绑定的老任务会被扫描每轮报一次一轮故障（它没结清的预留永远在，每轮都扫到），而且改由一轮故障那条路停下，不再是关口按名停——两条路停同一件事，日志每轮一条警告。原来那几处"跳过"其实是有意的决定（没绑定的预留永远不按零用量结清），不只是宽容老数据。所以改成：判断只剩 `require_bound` 一个函数，扫描捕获带类型的 `NotBoundError` 照原意跳过，按名停只走关口一条路。方案的本意（删 `taskgraph_enabled`、一个判断、不连累别的任务）做到了。

### 二-3　用例与改坏
- 相关定向用例 266 条：265 过；1 条老失败（`p35/test_provider_accounting_loop.py::test_a_call_queued_for_the_only_slot_is_unbilled_and_a_cancel_never_hands_it_off`，主分支同样失败，与本批无关）。
- 改坏 TG3-02（补记账扫描不再捕获"没绑定"）KILLED：老任务改由一轮故障停、不是关口按名停，`test_unbound_legacy_mission.py` 变红；该用例文件头的改坏说明同步改写。TG3-03（计划完整性停止不处理"已创建"）KILLED。

### 二-4　完成评估（`TaskGraph-补全-第一二批-完成评估.md`，需补 3 项）与阻断核验（`-阻断核验.md`，0 条）
- 需补 1：`taskgraph_notifications.py` 里"迁入基线"的过时注释改掉。
- 需补 2：结清已核清动作的扫描新加的按任务边界没有用例守着；要造出"有对外动作的任务"才走得到，为它专门造局面不值——删掉这层边界，恢复原来的循环（照原意跳过没绑定的任务）。
- 需补 3：施工差异先补进方案（第 3.3 版：第 2 节那一行改写、第 7 节加偏离 27）。
- 阻断核验顺带记了一条老问题（不是这两批引入）：内核版本不认的老任务停不下来——停的时候又查一次绑定、又报同一个错，每轮原地重试，但主循环不崩、不写库。现在只有一个内核版本，产品库里没有这种任务，等以后升内核版本时再修。
- 台账由评估子代理更新（R07、R39、R57、R59 与场景 T007、T039、T057、T059）。

## 第三批　共用在产品里接通（方案第 3.4 版第 3 节）

### 三-1　改动
- **点名写在决定上**：选做法、继续分解、换做法三种决定加可选的 `reuse`（`{步骤名: 出现编号}`，字符串、非空、不超过 64 项、同一出现编号不点两次），三者都经 `decision_adapter._refine_operation` 落成同一个 `RefineOperation`，`reuse` 就放在它上面（为空时不进编码，网络编码清单重写一次）。JSON 规约三种载荷加 `reuse`。
- **系统只核秩序**：预览 `plan_preview._named_reuse` 先查"被点名的是不是现在能共用的步骤"（在跑，或按现行要求通过、恰有一份现行验收），再查"新做法交给它的要求是不是它已负责的要求的子集"，多出来的退回 `REUSE_NOT_ALLOWED`，说明里写清多了哪几条、它原来负责哪几条。做法落地 `grounding.plan_slots` 再查：步骤名存在、只能点名普通步骤（点名子目标退回）、类型 / 作用域 / 领域 / 副作用相同（`named_share_refusal`，写明哪一项不同）。数据输入相同、先后已放行、不成环由执行图层现有核对管。
- **候选一份来源**：`taskgraph_plan_sources.eligible_sharing` 是唯一读法；规划包 `sharing_candidates`（`planner_views.sharing_candidate_rows`，每行带类型、目标原话、负责的要求、写入目标、上游、状态"在跑 / 已验收"）、预览、提交核对都读它。
- **删**：按签名自动合并、`SharedGoalIndex`（含建议、相似度）、任务类型与做法步骤上的 `reuse_policy`、`effective_reuse_policy`、做法准入里对步骤复用策略的检查、`BIND_EXISTING_GOAL` 整条（决定、编译、准入、提示词、规划包里只为它服务的部分、`planning_graph_repairs.py` 整个文件）、`PlanDelta.resolution_reuses` 与提交时对它的核对。种子做法与测试领域的类型文件去掉 `reuse_policy` 键。前端执行图标签去掉对应项。Host 无引用。
- **换做法只删只属于被退休做法的边**：`compiler._merge` 的删边规则改为：任一端离开网络就删；两端都是被退休做法的孩子（或它的父目标）、且没有留下的已采纳做法同时持有两端才删。
- **提示词**升到 `planner-hierarchical-v24`：三种细化决定说明可选 `reuse`，"共用已有步骤"一段取代原 `BIND_EXISTING_GOAL` 段。
- **施工中修的两个缺陷**（产品同形用例跑出来）：
  1. 规划包核对做法适用报告时，同类型有两个目标、第一个目标已细化（不再评估）就报"做法没有适用报告"整份退回——`event_handler._hierarchical_admission_context` 改为取第一个**有**报告的目标。
  2. 第 8a 条（方案第 3.4 版新增）：在跑的步骤跨计划版本交结果。原规则"结果交上来时计划已换版一律归档为被取代"，而共用在跑的步骤必然提交一版新计划，于是共用步骤永远白做、下游永远等不到。新加 `OperationCompletionStore.scope_unchanged(旧范围, 现行范围)`（除计划版本外完全相同、旧范围确由真实提交生成、这一步读的数据边相同），"已验收内容跨版本沿用"与"在跑结果按现行范围验收"共用它：`completion_inputs._current_scope` 读结果的完成范围时，冻结的那份若与现行的只差版本就用现行的；验证途中计划换版（检查记录绑着旧范围）则不归档，下一轮按现行范围重验同一份结果（`event_handler._scope_carried`，仍受"同一原因连拒三次判失败"的上限约束）。范围真的变了照旧归档。

### 三-2　与方案的施工差异
- "共用方换做法 / 首建方换做法"两个方向在单元层（`test_htn_and_or_shared_goal.py`，走真实 `_merge`）测，没在产品同形世界里再造一遍：产品世界里换做法要先让一个分支失败或被审阅退回，局面造价高，而要守的正是 `_merge` 这一处。
- WAIT 的等待引用收紧为任务内容摘要（原来也收合同摘要，规划包里只给前者，一条路径）。
- 产品用例里两个子目标之间不排先后：共用步骤同时在两边，"左先于右"会成环，系统按成环退回（正确行为）；子目标做法每步至少负责一条要求，所以共用步骤负责的是两个子目标都承接的那条要求（out.md），超出即退回。这两点写进了方案第 3.4 版修订说明。

### 三-3　用例与改坏
- 新 `T/product_world/test_shared_steps.py` 2 条：两个子目标点名共用一个写文件的步骤，只有一个"写"、两个上级做法都持有它、右边下游读它的产出、任务完成；拿"一步负责两条要求"的做法点名共用 → `REUSE_NOT_ALLOWED` 退回、说明写清多出的要求，之后照常完成。两条分别实测走到了第 8a 条的两处（读结果时沿用、验证途中换版重验）。
- 单元层：`test_htn_and_or_shared_goal.py` 共用一节重写（点名共用只建一个、两个使用方；不点名就做两次；两个方向换做法保留共用步骤与留下分支的边；作用域 / 类型 / 副作用 / 步骤名 / 子目标各一条退回；在跑与已验收两种共用）；决定信封 / 合同 / JSON 规约 / 编解码 / 准入 / 适配各加 `reuse` 用例、删 `BIND_EXISTING_GOAL` 用例与夹具；`test_seed_methods.py` 删 3 条只测自动合并的用例。
- 相关定向用例 1321 条：1297 过、4 跳过、20 失败。17 条是主分支同样失败的老失败（`test_htn_store.py` 14 条、`test_planning_decision_store.py` 2 条、`test_h1h_commit_guard.py::test_o03_…` 1 条，主分支逐条对照过）；3 条是本批删步骤复用方式后过时的编码用例，合成一条"做法步骤不带复用方式、带了按未知字段拒绝"，该文件 110 过。
- 改坏：TG3-04（不比较交给共用步骤的要求）KILLED；TG3-05（`_merge` 改回"碰到就删"）KILLED——该用例原来是编译直接抛异常、执行器判"无效"，改成先捕获再断言；TG3-06（范围没变也不算沿用）KILLED。

## 第六批　逐步骤的预算去向（先于第四、五批做：不依赖它们）

### 六-1　改动
- `SDK/orchestrator/obligation_accounts.py`：新 `step_accounts`——逐任务算尝试数、失败数（只算模型的失败）、已结算 token、用量未知的尝试数；义务账改为把它按义务汇总（同一份算法，没有第二处计算）。`obligation_rows` 每个义务行带 `steps`：这个义务自己的普通步骤，每步带花费与 `branches`（有几个已采纳做法持有它；共用步骤只是一个任务，只出现一次）。
- Host 诊断：`STEP_FIELDS`，每个义务行带 `steps`（不带目标原文）。
- 界面"预算去向"：有步骤的义务行可展开（默认收起），每步一行同样写法，共用的标"几个分支共用"，超过 8 步合成"其余几步"。

### 六-2　用例与改坏
- `T/product_world/test_obligation_accounts.py`：子目标 + 收尾两步，每步一行、各项合计等于义务行。`test_shared_steps.py` 第一条：共用步骤只出现一次、标 2 个分支、只尝试一次。前端 `BudgetByDuty.test.tsx` 加一条（展开、共用标注、没花费的不列）。Host `test_mission_diagnostics.py`、`test_chat_mission_amend.py` 12 过。
- 改坏 TG6-01（步骤账不按任务分）KILLED。

## 第四批　改要求后沿用已验收的叶子，审阅员按新要求重审（方案第 3.5 版第 4 节）

### 四-1　接法（偏差单 `TaskGraph-补全-偏差单-第四批重审入口.md`，独立裁决）
方案原写"只放宽'尝试冻结范围 = 现行范围'这一条"。只读摸底发现现行"验证 → 验收"整条链约十处只服务刚交上来的结果，这个前提不成立。裁决：这条链上"每份结果一份"的键统一改为（结果, 要求版本），不建尝试、不建新表，重审由计划提交后的扫描驱动，核心流程与新结果共用。方案先改到第 3.5 版再动代码。

### 四-2　改动
- **键改为（结果, 要求版本）**：
  - 验证记录表加 `requirements_revision` 列，唯一约束改为（结果, 要求版本, 层）（迁移 42：表重建、四个触发器原样重建；旧库记录记 0，开发期不做旧数据兼容）。`Store.list_verifications` 必须给要求版本（`None` 只供展示读全部）。两份表清单同步。
  - 验收编号 `acceptance_id_for(任务, 结果, 要求版本)`、验收命令号、内容审阅的命令号都带要求版本；新 `result_acceptance_ids`（知识工具查"这份结果有没有现行算数的验收"用）。
  - 内容审阅记录的四处查找（`assurance_review_runtime.task_record` 与查旧调用、`assurance_validity.official_record_for_result`、`human_commits` 人工裁决处）加"要求版本"条件。
- **冻结输入的重审读法**：`completion_inputs.load_completion_result_inputs(..., requirements_revision=)`——只对已验收的结果成立；原冻结输入清单、端口认领不变，范围取现行的；`scope_unchanged(across_requirements=True)` 只放行"要求版本更新、任务合同 / 义务 / 副作用 / 数据边都没变"。新 `frozen_requirements_revision`（一份结果当初按哪一版要求做的）。正常路径不传参数，行为不变——在跑的结果撞上改要求照旧归档。
- **重审驱动**：新 `orchestrator/carried_review.py` `carried_reviews`——现行计划里的普通步骤、有通过的结果、结果是按更旧的要求版本做的、现行要求下不算数、这一版还没有验收也没被打回、它读的上游在现行要求下都已算数。主循环里与验证共用同一组名额。`event_handler._carried_review`：按原产物记录重建只读副本 → 本地检查（绑现行范围）→ 审阅员独立会话 → 通过则 `commit.accept_carried_result`（只写验收、输出、内容贡献与 `CarriedResultAccepted` 事件；不动尝试 / 任务 / 产物状态、不结清预算）；打回则记修复请求（原因 `CARRIED_RESULT_REJECTED`，带审阅员意见）；两次都判不下来则问用户裁决；检查或审阅出错、计划又变了则下一轮再审，不当成没过。
- **审阅员调用**：`run_task(..., requirements_revision=)`——不要尝试租约，审阅预留另记、不动原尝试的保护预算。"重审是否还活着"只有一个判断 `carried_review_alive`（任务没结束、这一步还在现行计划里），审阅交接 / 导入 / 证书三处共用的 `review_subject_stopped`、模型调用准入、停止判定都用它（原来这几处都按"原尝试、任务已结束"拒绝）。
- **派发处**：`ACCEPTED_UNDER_OLD_REQUIREMENTS` 删除，改为 `CARRIED_REVIEW_PENDING`（等重审）与 `CARRIED_RESULT_REJECTED`（重审没过，等规划器）。
- **共用候选第三种状态**：`eligible_sharing` 收"按旧版通过、待重审"的步骤（`SharedGoalEntry.carried`，规划包状态 `accepted_under_old_requirements`）；点名它时不查"负责的要求不变"，执行图共用核对放行；重审已打回的不再是候选。
- **提示词**（仍是 v24）：改要求一段改写为沿用 / 重审 / 打回的说明，共用一段补第三种状态。
- 顺带：验证副本按记录重建时不再要求原尝试的活动目录还在。

### 四-3　登记
- 新事件 `CarriedResultAccepted`、验证记录新列：全业务重放的表清单已更新，相关用例通过；本仓库没有单独的事件类型登记表。
- 新原因码 `CARRIED_REVIEW_PENDING` / `CARRIED_RESULT_REJECTED` 是给规划器看的说明，不决定秩序，按第一批"清单只追决定秩序的码"不进错误码表。

### 四-4　用例与改坏
- `T/product_world/test_requirements_amend.py::test_kept_old_step_is_reviewed_again_not_rerun`（原"如实报出、不自动重跑"那条改写）：留着的步骤只有一次尝试，按第 2 版多一层审阅通过与一条验收，任务按新版完成。
- 新 `T/product_world/test_carried_review.py` 3 条：重审打回 → 修复请求（带原因与意见）→ 规划器换掉重做 → 完成，同一份结果同一版只审一次；上游重审没过时下游不被送审；换做法时点名共用旧步骤 → 不重做、重审通过、a.md 只有一个任务在写。
- 改要求整组 + 共用 + 重放等 44 条通过（正常路径未受影响）。
- 改坏：TG4-01（通过不写验收）、TG4-02（打回不发修复请求）、TG4-03（重审不看上游）、TG4-04（审阅记录查找不认要求版本）；TG3-04 原文随代码更新。五条全部 KILLED。

## 第五批　重审没过的步骤换后继步骤重做；删"钉住旧版本"（方案第 5 节）

### 五-1　改动
- **删钉住**：做法里数据引用的 `pin` 字段（带了按未知字段拒绝）、`DataRequirement.source_revision_policy`、`SourceRevisionPolicy` 枚举、编译器里按钉住定策略的分支、`grounding.pinned_flows`、`hierarchical_dispatch._pinned_revisions`、输入解析里的钉住分支与 `ResolutionPolicy.pinned_revisions`、输入绑定上的 `source_revision_policy` 字段；数据边表里的同名列（迁移 43：表重建、索引与五个触发器原样重建，旧行 JSON 里的同名键去掉）；两份表清单同步；网络编码清单重写。数据边一律跟随现行算数的验收，读不到授权版本如实报"版本不可用"。
- **提示词**（仍是 v24）：数据引用不再提 `pin`；"换掉这一步"一段写明下游没通过的会改读后继重做、下游已通过时单换会被退回要改用换做法并用 `reuse` 点名仍想要的步骤、被共用的步骤要先让别的使用方放开。
- 换后继、下游改接、下游已通过时退回（`REPAIR_NOT_ALLOWED`）都是现成行为，没改代码，补了用例。

### 五-2　与方案的差异
- 方案写钉住的字段"随第三批 `htn.py` 改动一起删、编码清单只重写一次"，实际在第五批删，编码清单重写了两次。
- 方案没列到数据表的列与触发器，施工补了迁移 43。
- 导出片段的"跟随版本的数据边"特性标记删除；`requires_reacceptance` 恒为真、留着没删（方案第 3.6 版偏离 30）。

### 五-3　用例与改坏
- 新 `T/product_world/test_carried_redo.py` 3 条：重审通过后，换上来的下游冻结的输入引用的是按新版要求的那条验收；重审没过、下游还没通过 → 换后继，下游读后继的产出，任务完成；重审没过、下游已通过 → 单换被退回（`REPAIR_NOT_ALLOWED`），改用换做法后完成。
- `test_input_revisions.py`：钉住变体删除，加一条"做法里写 pin 被拒"。函数级 `test_input_manifest_resolution.py` 删 5 条只测钉住的用例、其余改为跟随口径（105 过）；`test_versioning_v2_manifest.py` 等同步。
- 产品同形受影响的一组 45 条：44 过，1 条是我新写用例里的函数名写错（已改，过）。`test_seed_methods.py` + `test_htn_store.py` 剩 19 条失败，全部在主分支的 36 条老失败之内。
- 改坏 TG5-01（下游取验收时不筛现行算数的；补 HTN 补齐 F1 第 6 号空缺）KILLED。
