# TaskGraph 补全 第三～六批 阻断核验

- 范围：`git diff fbc3ad63..HEAD -- sdk/simple-harness-sdk/src backend/deskpet tauri-app/src`（分支 tg-1，worktree simple_harness-a4）
- 口径：只报会导致错误行为、数据损坏、任务挂死 / 死循环、主循环崩溃、越权或安全问题的缺陷。
- 方法：读代码，逐项核对；迁移 42/43 在临时库上实测（先建到 v41，写入验证记录与数据边行，再用 `Store.open` 升到 43）。没有跑测试用例。
- 结论：**阻断 4 条**（B1～B4），另有 1 条疑似、未证实（S1）。

文件路径以 `SDK/` 代表 `sdk/simple-harness-sdk/src/agent_orchestrator/`。

---

## 阻断

### B1　重审的三种结局没有终止条件：每轮重跑检查，其中一种让任务永远挂住

- **位置**：`SDK/orchestrator/carried_review.py:86`、`:99-102`（`rejected()` 只认 `critic_review` 层 FAIL）；`SDK/orchestrator/event_handler.py:8237-8245`（出错时 `return False`）、`:8248-8255`（判不下来时去问人）；`SDK/orchestrator/hierarchical_dispatch.py`（派发处 `refused` 同样只看 `rejected()`）。
- **出错情形**：`carried_reviews` 只在两种情况下不再挑出这一步："这一版已有验收"，或"`critic_review` 判 FAIL"。另外三种结局都不留下能让它停的事实，所以主循环每一轮都会再挑出同一个（结果，版本）：
  1. **本地检查或代码测试 FAIL**：路由器在审阅员之前就短路，`critic_review` 记为 SKIPPED。`_carried_review` 发出修复请求，但下一轮 `rejected()` 仍是 False，于是又把 format/rule/code_test 跑一遍。`reuse=None`，所以 **code_test 每轮都真的执行**。`record_request` 的 source_key 相同，后面几轮不再产生请求。派发处这时仍报 `CARRIED_REVIEW_PENDING`（"审阅员正在审，不派执行者"），和已经发出的修复请求说法相反，规划器可能选择等待。
  2. **任一层 ERROR**，典型情形是审阅员两次格式都不合格：`_raise_final_failure` 抛 ContractError，`critic_review` 记为 ERROR。走到 `:8243` 返回 False，**不发修复请求，也不问人**。下一轮 `run_task` 用 `b.requirements_revision=?` 找到同一个 ordinal=1 的旧调用并复用，又一次 ERROR。这一步永远停在"待重审"，规划器收不到任何请求，**任务挂死**。普通路径遇到 ERROR 会把结果判 FAIL、转入重试，是有上限的；重审这条路没有上限。
  3. **审阅员判不下来、等人裁决**：每轮都重跑本地检查和 code_test，再调一次 `_ask_person_to_adjudicate`，直到有人回答（可能要几小时）。
- **为什么是阻断**：情形 2 让任务挂死。情形 1 和 3 每隔几秒执行一次测试，形成 CPU 空转，并一直占着验证名额。审阅员格式失败在真机上出现过（见"审阅 JSON 前多写文字判格式错误"那条决定），不是边角情形。
- **最小改法**：给每个（结果，版本）记一个持久的"已有结论"事实，`carried_reviews` 和派发处都读它。具体是把 `rejected()` 改成"这一版任一层 FAIL，或者修复请求 `carried-review:{result}:r{rev}` 已经存在"。ERROR 设一个有上限的计数（比如沿用 `VERDICT_REFUSAL_LIMIT`），到上限就当作没过，发修复请求。等人裁决期间，只要裁决问题已经开出、还没回答，就跳过这一步。

### B2　重审任务里有两类异常没被捕获，会冲垮主循环

- **位置**：`SDK/orchestrator/event_handler.py:8216-8217`（重审用的 `run_critic` 直接 `return await reviews.run_task(...)`）、`:8237`（捕获清单）；`SDK/orchestrator/assurance_review_runtime.py:433`（`_route_service("critic")`）、`:437`（重审另记的预留）；主循环 `event_handler.py:3776`、`:3807`（`_raise_if_verification_crashed`）。
- **出错情形**：
  - 普通路径 `_run_critic`（`event_handler.py:8519-8521`）会把 `AssuranceError` 和 `RoutingUnavailable` 包成 ContractError，路由器再把它记成 ERROR 层。重审的 `run_critic` 没有这层包装。
  - 审阅模型在冷却期、没有备用模型时，`_route_service` 抛 `RoutingUnavailable`（RuntimeError），2026-10-03 真机出现过。
  - 任务账户或总预算不够付 `critic_reserve_tokens` 时，`ensure_review_invocation → create_service_intent` 抛 `BudgetExhausted`。它是 BudgetError，继承自 StoreError，不是 CommitRejected。
  - 这两种异常都不在 `:8237` 的清单里。它们从路由器穿出，成为验证任务的异常，下一轮由 `_raise_if_verification_crashed` 在主循环里抛出，所有任务一起停。重启后，`carried_reviews` 在同样条件下又会挑出这一步，可能反复崩溃。
- **为什么是阻断**：主循环崩溃，而且触发条件（模型冷却、预算吃紧）在改要求之后的长任务里很常见。
- **最小改法**：重审的 `run_critic` 加上与 `:8519-8521` 相同的包装，把 `AssuranceError`、`RoutingUnavailable`、`BudgetError` 都转成 ContractError，让它们变成 ERROR 层。B1 修好后，ERROR 会按有上限的次数重试。

### B3　重审扫描放在每任务故障边界外，一个任务的计划损坏会让整个主循环崩溃

- **位置**：`SDK/orchestrator/event_handler.py:3795-3807`（扫描直接在主循环里跑，没有经过 `_mission_round`）；`SDK/orchestrator/carried_review.py:59`（`network = dispatch.network(mission_id)` 不在 try 里，只有下一行的 `read_taskgraph_outcomes` 包了 try）。
- **出错情形**：某个改过要求的任务（`latest.revision > 1`），只要计划读回时完整性出错，`HierarchicalDispatch.network()` 就抛 `PlanIntegrityError`（`hierarchical_dispatch.py:722-724`）。它从扫描里直接抛到主循环，所有任务一起停。第一批专门守住的规则是"计划历史损坏只停它自己"，这里绕过了它。同一段扫描里的 `store.get_result`（坏结果会抛 `StoredResultCorrupt`）等读取也是一样。
- **为什么是阻断**：一个任务的数据问题会拖垮所有任务的主循环，违反已交付的故障隔离。
- **最小改法**：每个任务的扫描放进 `_mission_round(mission_id, "carried_review_scan", ...)`。或者在 `carried_reviews` 里把 `dispatch.network()` 一起包进现有的 try，读不出就返回 `[]`，让它自己的那条路去停这个任务。

### B4　第 8a 条改动后，"步骤已移出计划"抛出的错误码变了，验证会冲垮主循环，而且重启后反复崩溃

- **位置**：`SDK/orchestrator/completion_inputs.py:368`（`_current_scope` 按**现行**计划读范围）；`SDK/orchestrator/operation_completion.py:357-361`（现行版本里没有这个出现的范围行时抛 `OP_COMPLETION_SCOPE_UNRESOLVED`）；`SDK/orchestrator/event_handler.py:8020`、`:8045-8046`（只放过 `CHECK_SCOPE_CHANGED` 和 `OP_EFFECT_SCOPE_STALE`，其他码都 raise）。
- **出错情形**：
  - 改动前：`load_completion_result_inputs` 按**冻结时**的计划版本调用 `read_scope`。计划换版后，第一道检查就抛 `OP_EFFECT_SCOPE_STALE`（"the adopted plan differs"），结果被归档为"被取代"。
  - 改动后：改用现行计划版本 + 原出现编号去读。如果新计划已经不含这个出现（换做法退休了这一支、取消分支、换后继），`get_scope_exact` 查不到，抛出的是 **`OP_COMPLETION_SCOPE_UNRESOLVED`**。
  - 这时的局面是：步骤的结果已经交上来（结果 PENDING），但还没开始验证（验证名额占满，或者交结果和规划提交落在同一轮）。提交计划不会取消这个尝试（`plan_commits._revoke_running_work` 写明"不取消尝试"），收集处也只检查"要求改了没有"。于是 `_verify` 照常跑，路由器开头就调 `prepare`，`load_completion_result_inputs` 抛 UNRESOLVED，`:8045` 再 raise，验证任务异常，主循环崩溃。结果仍是 PENDING/RUNNING，重启后同一份结果再次触发，崩溃会一直重复。
  - 补充：审阅这条路上也会读到 UNRESOLVED，但它在 `_run_critic` 里被包成 ContractError、记为 ERROR，所以只有本地检查准备（`prepare`）这条路会崩。
- **为什么是阻断**：主循环崩溃，而且是持续性的。触发条件是"换做法 / 取消分支时，被换掉的步骤恰好有结果在排队验证"，在多分支并行、验证名额只有 2 个的配置下完全可能出现。
- **最小改法**：在 `_current_scope` 里，如果现行计划的成员中已经没有 `frozen.occurrence_id`（或者捕获 `read_scope` 抛出的 `OP_COMPLETION_SCOPE_UNRESOLVED`），就改抛 `OP_EFFECT_SCOPE_STALE`。这样恢复原来"归档为被取代"的行为。`_scope_carried` 本来就把这种情况当作"不沿用"，不用改。

---

## 疑似、未证实（建议施工方顺手确认）

### S1　重审扫描遇到"范围除要求外也变了"时静默跳过，派发处却一直报"待重审"

- **位置**：`SDK/orchestrator/carried_review.py:91-94`（`load_completion_result_inputs` 失败就 `continue`，什么也不记）；`hierarchical_dispatch.py` 派发处（只要不是 `rejected()`，就报 `CARRIED_REVIEW_PENDING`）。
- **可能的情形**：沿用的步骤在新计划里任务合同、义务、副作用或数据边有一项变了（例如上游换了后继，下游的数据边随之改接，但下游没有重新定版），就会出现"系统永远不审、规划器被告知'审阅员正在审'"的局面。现有用例里，下游会被重新定版、重新执行，没有表现出挂死，所以没能证实。建议确认：这一步的任务合同 / 数据边一变，是否一定伴随重新定版（也就是 `accepted_result_id` 不再算这一步的结果）。如果不一定，跳过时应发修复请求，或者派发处改报另一个原因码。

---

## 查过、不是问题（各一句）

- **重审的审阅预留由谁结清**：走普通服务意图的收集路径。步骤离开计划或任务结束时，`_critic_subject_stopped` 的新分支会让收集器接手；重启后，旧调用按（结果，版本，ordinal=1）复用，不会重复预留。小口子（非阻断）：重启时恰好又改了一版要求（r3），r2 那次在途调用要等任务结束才被收集，期间占着一份 `critic_reserve_tokens`。
- **`_carried_review` 的重复调度**：`_verifying` 以 `carried:{result}:r{rev}` 为键，同一份不会并发两次，和普通验证共用名额上限。
- **`frozen_requirements_revision` 的新调用点**（`record_verification_layer`、`_human_inputs`、`human_commits`、`commit_service._knowledge_support`、`knowledge_tools`）：只有在尝试意图或冻结范围行缺失时才抛错。所有尝试在创建时都写入了 `completion_inputs`（`commit_service.py:1807`），所以正常数据下不会抛。`record_verification_layer` 本身在 `_verify` 的 try 里。
- **验收编号公式**：叶子验收的写入方和读取方都改用 `acceptance_id_for(任务, 结果, 要求版本)`（leaf_acceptance、commit_service 三处、assurance_validity、knowledge_tools）。`operation_outcomes` 里的 `"acc-"+binding_id` 是另一类编号，互不相干。旧库按旧公式算的编号对不上，按"开发期不兼容旧数据"处理。
- **`accept:{result}:r{rev}` 命令号**：每一版要求一条，同一版重放仍然幂等；只有旧库里的命令号还是旧格式。
- **第 8a 条"三次判失败"**：要在同一份结果的三次连续验证里，每次都赶上计划换版，才会累计到上限。审阅包编号只和（结果，要求版本）有关，与计划版本无关，所以第二次验证会复用已有审阅，时间窗口很短。有上限，不构成阻断。
- **`scope_unchanged` 是否漏项**：它比较完整的范围 JSON（只去掉 `plan_ref`），还核对旧范围确实来自真实提交的收据、这一步读的数据边相同。`across_requirements` 只放宽要求相关的三项，而且要求版本必须变新。
- **迁移 42/43**：在临时库上实测（v41 + 数据 → 43）通过，验证记录的要求版本记为 0，数据边 JSON 去掉了 `source_revision_policy` 键。整批迁移在一个事务里，失败会回滚，迁移前还会先备份。DROP 之前先删了表自己的 immutable / 记账触发器；新表在插入数据时还没建触发器，所以不会挡住 INSERT…SELECT。没有其他表用外键引用这两张表，也没有其他表的触发器引用它们。
- **`_merge` 的 `retires` 规则**：只要一端离开网络（orphaned）就删边，所以不会留下悬空的边；共用步骤与留下分支之间的边会保留；新做法自己编译自己的边，是否成环由执行图校验把关。
- **`eligible_sharing` 三种状态 / `_named_reuse` 对待重审的条目不查要求**：多交给它的要求由随后的重审按新范围把关；`validate_sharing` 对待重审的条目仍要求 `acceptance_ref` 完全相同。
- **`provider_budget_guard.authority` 对重审放开**：`carried_requirements_revision` 只出现在系统内部写入的意图配置里（`ensure_review_invocation`），Host 没有写入口。放开后仍然检查任务没结束、步骤还在现行计划里、租约和预留都有效，没有越权口子。
- **`_steps_by_duty` 的 SQL**：用 `COUNT(DISTINCT instance_id)` 计数，不会重复。跨所有版本的 `plan_memberships` 子查询、`step_accounts` 被算两遍、每个任务一次 `list_attempts`，这些都会让大任务变慢，但只在诊断和界面读取时调用，不影响主流程。
- **Host 诊断 / 前端**：只做字段挑选和展开显示，诊断里不带目标原文，没有发现问题。
