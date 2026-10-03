# HTN 补齐 · 阶段 C3+摘要（做法跨任务复用与摘要）：开工裁决与施工清单

- 裁决人：独立裁决子代理（只读代码与文档，除本文件外没有改任何文件、没有跑测试）。日期 2026-10-03。代码以工作树 `simple_harness-a4`（分支 htn-e，`a425f7d8`，阶段 E 已写完、在评估核验中）为准；**下文行号都是"约"，以函数名为准**。
- 依据：`HTN补齐计划-2026-10-02.md`（第 3.14 版）阶段"C3+摘要"全节、表二第 1b、12a 条、第一节"暂时不做"、第二节"开工前裁决新增的偏离"、第五节④、第六节；`HTN补齐-阶段E-开工裁决与施工清单.md`（1.4 末条、偏差单 4、10）；`HTN补齐-实施记录.md` 阶段 E；`审阅通过结论入知识库-方案.md`；`TaskGraph-补全-方案.md` 第 6 批、2.4。
- 口径：判断交给模型，系统只管约束与秩序；同一件事只留一条路径；旧路径直接删、不做兼容；做好的功能默认开启；用户已定的八项"暂时不做"不排进来。**"学习"（暂不做）与本阶段的边界**：本阶段只做"成功做法经审阅后进全库、别的任务能读到它当先例、被明确归因失败两次退役"；不做任何按成功率、次数、相似度给做法打分或排序推荐，不做任何可训练的东西。
- 路径缩写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`Host/` = `backend/deskpet/orchestration/`；`F/` = `tauri-app/src/`。

---

## 〇、一句话结论

本阶段能做，**共 9 步（C3-0～C3-8），不依赖 TaskGraph 补全，全部在本阶段做完**。

- **要一个新迁移（40 号）**：删评测晋级表 `method_evaluations`、删规则摘要表 `summaries`，新建"全库做法"与"归因记录"两张表。迁移 1～39 一字不改（迁移 23 的建表文字原样挪进 `schema.py`）。
- **编码清单不动**（不碰 `contracts/htn.py`、`state_machines.py`、`models.py`、`evidence_state.py`、`task_network.py`）。
- **工具说明 `TOOL_SCHEMAS` 不动**（摘要层只在知识读工具的输出里多一种行）。
- **升四份提示词**：规划器第 22 版（规划包 13）、审阅员回复第 4 版（`REVIEW_INSTRUCTIONS`）、执行者第 7 版（AppWorld、无人机两份跟着升）、上下文组装第 6 版。主 Agent 新增一个对话工具 `method_library`（产品工具，不是执行池工具）。

核心裁决一句话：**全库做法只当"先例"，不在别的任务里原样采用**。新任务的规划器在规划包里看到"编号 + 一句用途"的目录，想用就先发一个只读决定把原文读进来，再按本任务的要求编号写一个新做法（注明"来自哪条全库做法"），照常过本任务的做法审阅。这样做法里的要求编号 `c-user-N`、步骤参数原文都不必跨任务"翻译"，只留"提出做法"这一条路。

偏差单 12 张，**没有需要用户本人决定的事**；6 条建议报用户知悉（第五节）。工期估 6～8 天（计划写 4～6 天）。

---

## 一、开工前裁决

### 1.1 前置改造还剩什么：根目标类型、步骤类型的原话与执行者引用

**结论：阶段 E 只做了"步骤类型不带本任务判据"。还剩四样：步骤类型的说明文字仍是用户原话；步骤类型的执行者引用按本任务工具算哈希；根类型带用户原话和本任务判据编号；开发库里没有"类型目录哈希"。改法是"类型一律通用，本任务的原话和判据只放在根绑定上"——阶段 E 清单 1.4 末条的备选"根类型也不带判据、由根绑定给出"**现在必须做**，否则根目标类型的哈希每个任务都不一样，全库做法按目标类型筛、按类型目录哈希绑定都无从谈起。**

现状（阶段 E 之后）：

| 位置 | 现在 | 与任务有关的地方 |
|---|---|---|
| `Host/hierarchical.py` `planning_world` 约 :26-40；同形世界 `SDK/testing/product_world.py` `user_goal_world` 约 :48-58 | 根签名 `GoalSignature("desktop.user-goal", …, mission.goal, 现行内容要求编号)`；读 `current_criteria` | 说明文字 = 用户原话；覆盖判据 = 本任务 `c-user-N` |
| 同上 约 :43-45 / :61-63 | 步骤与接续步骤签名的说明文字 = `mission.goal`（接续步骤前面加一句英文），覆盖判据已置空 | 说明文字 = 用户原话 |
| 同上 约 :93-95 / :93 | 执行者引用 `operator_ref` = `content_hash_of({"tools": mission.allowed_tools})` | 本任务允许的工具（随沙箱探测、技能目录变） |
| `SDK/deployment/root.py` `initialize_root` 约 :139-180；`SDK/orchestrator/requirements_amendment.py` 约 :113-123 | 根绑定的签名 = 根类型的签名；合同哈希 = 类型定义 + 参数 | 跟着根类型变 |
| `SDK/orchestrator/event_handler.py` `forget_planning_world` 约 :1291 | 改要求后丢掉规划世界、按新要求重建 | 只因为根类型带判据 |

改法（一处一处）：

1. **类型一律通用**（Host 与同形世界同样改）：
   - 根类型说明文字改为固定一句（"The user's goal; the root binding carries the user's own words and requirements."），覆盖判据置空；
   - 步骤类型、接续步骤类型的说明文字改为固定一句（"One step of the user's goal; the plan's links say which requirements it answers for."；接续步骤同理）；
   - 执行者引用改为固定体：`VersionedRef("desktop.workspace-worker", 1, content_hash_of({"capability": "workspace.prepare"}))`（同形世界用 `user.workspace-worker`）。这个引用今天不授予也不限制任何工具（Host 没配 `operator_tool_allowlists`，`event_handler.py` 约 :9989-9993 只在配了时查）；本任务实际能用哪些工具仍由"任务 ∩ 步骤 ∩ 角色 ∩ 部署"的交集决定（同处约 :9994），工具缺了由能力记录 `world.records` 标"未授权"——两处都已存在，不另写。
   - 世界构造不再读要求（删掉对 `current_criteria` 的调用）。
2. **根绑定带本任务的原话与判据**：`SDK/deployment/root.py` 新增一个函数 `root_binding(store, mission, definition, *, contract_revision, parameters)`，产出根绑定：签名与根类型同编号、同版本、同参数与输出 schema，**说明文字 = 用户原话，覆盖判据 = 现行要求里的内容要求编号**（非 `action:` 的那些；这条前缀过滤从 Host 挪进来，只留一份）；`requirement_refs` = 现行全部编号；合同哈希 = 类型定义 + 参数 + 这份签名。`initialize_root` 与 `requirements_amendment.py` 都改用它（同一份规则，两处写方）。
3. **删 `forget_planning_world`**（唯一调用方是改要求）：规划世界不再随要求变，改要求后不用重建。
4. **类型目录哈希**：`SDK/planning/htn/world.py` 加 `catalog_digest(world)` = 哈希（全部任务类型定义按编号排序 + 谓词声明 + 规划包版本号 `PLANNING_DECISION_PACKAGE_VERSION`）。同一部署上任意两个任务算出来必须相等——这就是"类型与任务无关"的验收（用例 1、12）。

连带核对（逐处读了代码）：

| 读者 | 读的是 | 改不改 |
|---|---|---|
| 做法提案目标一致检查 `SDK/orchestrator/planning_method_proposal.py` `prepare_method` 约 :124-128 | `goal_type.goal_signature != binding.goal_signature` 全等 | **改**：根绑定签名与类型签名的说明文字、判据不同了，改为比"编号、版本、参数 schema、输出 schema"四样（本文件里一个小函数，不动合同） |
| 注册协议的根覆盖检查 `SDK/planning/htn/registry.py` 约 :1946-1965 | **类型**签名的覆盖判据 | 不改代码；根类型判据为空后它对根不再起作用 |
| 子目标覆盖检查 `planning_method_proposal.py` `_coverage_problems` 约 :69-85 | 绑定签名带判据就直接放行（原来把根交给注册协议管） | **改**：绑定签名带判据（就是根）时，检查"每条判据都有链接"，缺的写 `ROOT_COVERAGE_GAP` 问题（与注册协议原来的说法一致，不加"不许多链"之类新规则）；其余分支不动 |
| 发布来源唯一检查 同文件约 :130-135 | 绑定签名判据 ∪ 上级分给它的 | 不改（读的是绑定） |
| 编译、计划校验、执行投影的根覆盖：`compiler.py` `_coverage_gaps` 约 :1124、`validation.py` 约 :449、`graph/projection_validation.py` 约 :780 | 根**绑定**签名 | 不改 |
| 完成范围、审阅判据、叶子判据：`completion_scopes.py` 约 :347、`assurance_check_policy.py` `planning_subject_criteria` 约 :441-451、`occurrence_tasks.py` 约 :311、`scoped_composition_review.py` 约 :69、`leaf_acceptance.py` 约 :211-226 | 绑定签名 | 不改 |
| 规划包"这个目标要负责的要求" `method_proposals.py` `build_context` 约 :489（`signature = goal.goal_signature`） | 绑定签名 | 不改 |
| 叶子任务行的"整个任务"一句 `occurrence_tasks.py` 约 :541 | **叶子类型**说明文字（原来就是用户原话） | **改**：叶子取 `mission.goal`（同函数约 :579 已在用），内容与今天逐字相同；复合目标照旧取说明文字 |
| 根终审请求里子步骤的标签 `root_review.py` 约 :1150 | 子绑定说明文字（叶子原来是用户原话，改后成固定句） | **改**：取子绑定参数里的 `goal`（这一步自己的说明），信息比原来多 |
| 根终审、派发里读根说明文字 `root_review.py` 约 :1063、`hierarchical_dispatch.py` 约 :1872 | 根**绑定** | 不改 |
| 测试世界 `SDK/testing/code_domain_world.py` 约 :49 | 代码领域的根类型自带判据 | 不改（不是产品路径，也不接全库做法） |

**做法里的判据链接写的是 `c-user-N`，跨任务复用时怎么办——裁决：不翻译、不映射。** `c-user-N` 保持"任务内编号"（第 1 版按位置、之后只增不复用，阶段 E 已定）。全库里的做法原文只是先例：规划器读到它时，链接里的编号指原任务的要求，规划包与提示词写明这一点；规划器按本任务的编号重写链接、提出本任务自己的新做法。理由：

- 链接编号与任务绑死不是唯一问题：做法步骤的参数（每一步"要做什么"的原话、文件名）也是原任务的；直接采用要么让编号碰巧对上（按位置的巧合，没有意义），要么在"采用"决定里加一张"原编号 → 本任务编号"的映射表——映射要进编译、完成范围、叶子判据、根终审等十几处读链接的代码，等于再造一条做法实例化的路；
- 规划器本来就会写做法；"读先例、改成本任务的、交审阅"是一条已有的路，审阅照常把关。

### 1.2 晋级只留一条路：评测晋级逐处删

**结论：计划里点名的 `evaluation/htn_method_cohort.py`、`scripts/acceptance/run_v14_runtime.py` 已不存在（阶段 A′ 已删）。现存的评测晋级是下面这些，连同表一起删；全库晋级的唯一一条路是 1.4 的"交付成功且根终审通过"。**

| 删什么 | 位置 | 谁在引用 / 怎么处理 |
|---|---|---|
| 评测晋级存储整个文件 | `SDK/storage/method_evaluation_store.py`（`freeze`/`evaluate`/`promote`/`refresh_registry`） | `planning/htn/world.py` 约 :340-361、:528-529；脚本 `run_h6_method_evaluation.py` |
| 评测记录、评测集、晋级策略 | `SDK/planning/htn/method_lifecycle.py` 整个文件 | `planning/htn/__init__.py` 约 :81-86、:148-151 的导出；`method_evaluation_store.py` |
| 规划世界上的两个方法 | `world.py` `freeze_method_evaluation`、`evaluate_method`（约 :340-361） | 无生产调用方 |
| 发布做法后的"按评测结果灌回注册表" | `world.py` `publish_methods` 末两行（约 :528-529） | **改为**本文件内一个小函数：只把"本任务试用范围"的做法（`TRIAL_ADMITTED` 且范围是本任务）灌回注册表；其余状态的行不再灌（评测删了以后没有写方） |
| 注册表里只为评测留的口子 | `registry.py` `allow_evaluation_trials`（约 :905）、`_evaluation_scopes`（约 :851、:866、:903、:1324） | 无生产调用方 |
| 永远回答"还不能晋级"的存根 | `registry.py` `promote`（约 :1187-1220） | 测试 `T/full_target/test_htn_novel_method_admission.py` 约 :366-382、`T/full_target/test_method_proposals.py` 约 :500-507 删对应断言 |
| 按字面相似推荐做法 | `registry.py` `suggest_for` 里 `statement` 那一支（约 :1405-1415，生产调用不传 `statement`） | 计划第 4 条"不做按字面重合度的推荐"；`statement_similarity` 函数本身留着（执行图共用建议 `grounding.py` 约 :390 在用，归 TaskGraph 补全） |
| 验收脚本 | `sdk/simple-harness-sdk/scripts/acceptance/run_h6_method_evaluation.py` | — |
| 测试与夹具 | `T/full_target/test_h6_method_lifecycle_v1.py`、`T/full_target/fixtures/h6/`（`README.md`、`cohort.json`） | — |
| 建表文字 | `SDK/storage/method_evaluation_schema.py` | **文字原样挪进** `storage/schema.py` 作 `DDL_V23` 字面量（迁移 23 校验和不变），再删这个文件 |
| 表 | `method_evaluations` | 迁移 40 `DROP TABLE method_evaluations;`（它不是保证通道源表，屏障里没有它的触发器） |
| 清单条目 | `SDK/observability/business_replay_inventory.json` 约 :1069；`SDK/orchestrator/taskgraph_deployment_manifest.json` 约 :300、:390-391 | 前者删条目；后者用 `scripts/build/taskgraph_manifest.py` 重生成 |
| 文档 | `sdk/simple-harness-sdk/ARCHITECTURE/index.md` 约 :431 | 改一句 |

Host 没有任何引用（已全仓搜过）。"状态枚举复用"不做：做法定义表的状态列一个值都不改（见 1.3、1.9），晋级与退役记在新表里；`MethodRegistryStatus` 枚举不动（它在编码清单文件里，动了要重写清单哈希；`EVALUATED`、`ADMITTED` 等值从此没有写方，留着不读，记一笔欠账，不为此动编码清单）。

### 1.3 晋级前的"可复用性"审阅：放进根终审，同一次调用

**结论：不另开审阅。根终审（`MISSION_FINAL`）的审查包加一节"本任务采用的做法"，审阅员回复加一个 `methods` 字段，对每个做法给"可不可复用 + 一句用途"（终审通过时有意义）和"是不是做法本身的错"（终审打回时有意义）。这和阶段 C 给步骤审阅加"本步待确认结论 + `claims`"是同一个套路。审阅员提示词要升（回复第 4 版）。**

- **时点**："交付成功且根终审通过即升"的那次根终审就是最合适的时点——它本来就要看全部产出与做法分解，多问一节不多一次调用；别的时点（任务完成后再开一次审阅）要新增一种审阅目的，保证通道的六种目的是封闭的，代价大得多。
- **审查包**：`SDK/contracts/resolution.py` `ReviewPackage` 加可选一节 `methods_to_judge`（照 `claims_to_confirm` 约 :845-1050 的写法），由 `SDK/orchestrator/root_review.py` `cut`（约 :830）切包时填：当前活动计划里**已采用、且是本任务提出并通过本任务做法审阅的**每个做法（根与子目标都算），每行 `{method_ref, goal（它服务的目标编号与目标参数原文）, based_on（来自哪条全库做法，没有为 null）, method（做法定义全文）}`。任务带不可信资料时（1.3 末），只列 `based_on` 不为空的那些（只为问"是不是做法的错"）；一行都没有就不带这一节。
- **回复**：`methods: [{"method_ref": "编号@版本", "reusable": true/false, "purpose": "一句用途", "at_fault": true/false, "reason": "一句理由"}]`。漏写的做法算"不可复用、不归因"，不整份拒收；写了表外的做法按"范围错误"可修重问（照 `claims` 的 `CLAIM_SCOPE`）；`purpose` 1～120 字。导入时写进正式审阅记录的清单（`SDK/orchestrator/assurance_review_import.py` 约 :408-442，与 `claims` 并列）。
- **审阅员提示词**（`SDK/assurance/review_input.py` `REVIEW_INSTRUCTIONS`，`SDK/assurance/reviews.py` `REVIEW_CODEC_VERSION` 约 :38 升 `assurance-review-reply-v4`，形状 `schema_version` 4）写清：`reusable` 问的是"把这个做法里本任务特有的原话、文件名去掉，它的拆法是否仍适合同类目标"；`purpose` 用一句不含本任务具体名称、文件名的话写它适合什么目标；`at_fault` 只在你判 REWORK/REJECTED 时写 true，意思是"没满足要求主要是因为拆法本身，而不是某一步没做好"，拿不准就写 false。与 1.8 摘要的 `summary` 字段同一版发。
- **做法表的"归属"**：不改做法定义表，归属列放在新的全库做法表上（1.9）。桌面取"租户 + 本人"：`<mission.tenant_id>/<要求第 1 版的 authority_subject>`，桌面恒为 `local-desktop/<本机用户>` 一个值；桌面没有"工作区 / 项目"这一层（只有一个授权发布目录，不是项目身份），不造这一层。目录、读原文、`based_on` 校验都只认同一归属。
- **不可信资料来的文字不进全库——只做任务级的可判定标记**：任务登记过资料（`sources` 表有本任务的行；这张表的 `trust` 恒为 `untrusted_external`，`SDK/storage/schema.py` 约 :466-480、`SDK/orchestrator/source_commits.py` 约 :32），或建任务时声明了不可信路径前缀（`untrusted_sources`，读法与上下文组装同一处，`event_handler.py` 约 :5298）——两样任一成立，本任务提出的做法**一律不晋级**，晋级处如实记一条"跳过：任务带不可信资料"。不逐字追踪文字来源，不做任何关键词判断。代价：带资料的任务做出的做法不会进全库（报用户知悉）。

### 1.4 晋级：写在唯一的完成写方里

**结论：在保证通道唯一的完成写方（`SDK/orchestrator/assurance_final_writer.py`，写 `MissionCompleted` 的那一段，约 :150-200）同一事务里调一个函数 `promote_methods(store, mission_id, resolution)`。**

- 读这次被采纳的根结论所依据的正式根终审记录（结论 → 审阅记录），取清单里 `methods` 中 `reusable=true` 的条目；
- 逐条再核秩序：做法确实在活动计划里被采用、是本任务提出并通过本任务做法审阅的、任务不带不可信资料、`purpose` 非空；
- 写全库做法表一行（1.9）：归属、目标类型编号、类型目录哈希（本任务规划世界的 `catalog_digest`）、做法引用、一句用途、来源任务、根终审记录编号（开工前裁决定的"晋级记录写来源任务与根终审回执编号"）、`based_on`（取自本任务该做法的 `PlanningMethodProposed` 事件）、晋级时间、状态"在列"；同一归属下同一做法已在列则跳过；
- 每条发一条 `MethodPromoted` 事件（在来源任务里）。
- 人裁决通过的根终审（审阅员判不下来、人点通过）照样读该记录里审阅员写的 `methods`；没写就不晋级。
- 任务失败、停止、取消一律不晋级（不经过这个写方）。

### 1.5 复用也要审：闸门放宽到"本任务采用的做法"

**结论：落点在计划提交事务里的闸门 `SDK/orchestrator/method_plan_reviews.py` `unreviewed_proposed_methods`（约 :94-125，调用方 `plan_commits.py` 约 :390），改名 `unreviewed_adopted_methods`。现在"本任务没开过审阅的做法一律放行"（假定它是部署自带的种子），改为"只有规划世界装入的种子做法放行，其余一律要有本任务通过（或人裁决通过）的做法审阅"。**

- 种子集合由规划世界给出（`world.py` 里 `install_library` 装入的那一批，世界上加一个只读属性）；桌面与同形世界没有种子，于是桌面上被采用的每个做法都必须在本任务审过。
- 按 1.1 的裁决，全库做法本来就进不了别的任务的注册表（灌回只灌本任务试用范围的），复用一定走"提出新做法 → 本任务审阅"，所以这条闸门是兜底：它保证以后任何路径漏进来的做法都过不去。拒绝码沿用 `METHOD_NOT_AUTHORIZED`，明细写 `review: NONE`。

### 1.6 列出方式与"按需读取"：目录 + 一个只读决定

**结论：规划包加 `views.method_library` 目录（每行只有编号、目标类型、一句用途、晋级日期）；规划器想看原文就发新的只读决定 `READ_METHOD_LIBRARY`，系统不改任何状态，下一轮规划包的 `views.library_reads` 带上原文。不在目录里放原文。**

- **目录**（`SDK/orchestrator/planner_views.py` 约 :89-199，与 `views.methods` 并列）：只列 ①同一归属、②类型目录哈希等于本任务的、③状态"在列"、④目标类型在"这次要找做法的目标"里（与 `method_signatures(network, under_repair)` 同一个集合，约 :89）的条目；每个目标类型最多 5 条，按晋级时间倒序；多出的只报个数。一个函数 `listed_entries(store, *, owner, digest, goal_types)` 给目录、读原文、`based_on` 校验三处共用。
- **为什么要新决定**：规划器没有工具（`tool_names=()`），"按需读取"只能是一个决定。可选做法比较：
  - A（采用）：新的只读决定 `READ_METHOD_LIBRARY`，载荷 `{"entries": [1～3 个目录编号]}`。照 `PROPOSE_METHOD`/`REQUEST_HUMAN` 那条"服务决定"的路处理（`event_handler.py` 约 :6620-6690，记 `NO_STATE_CHANGE`）：核对编号都在本次请求的目录里，记一条 `PlanningLibraryRead` 事件，加进叫醒规划器的事件集（约 :2989）。下一轮 `views.library_reads` 列出本任务读过的条目（最近 3 条）：编号、一句用途、做法定义全文，并附一句说明"链接里的要求编号指原任务的要求；要用就写一个新做法，`method_id` 用新的，链接按本任务的编号写"。每个任务最多 3 次读取决定，第 4 次按 `PLANNING_BOUND_REACHED` 退回（与"每个目标最多提 3 个做法"同一种上限写法，`planning_method_proposal.py` 约 :114-119）。
  - B（不采用）：目录里直接放原文——与计划"只放目录"相反，规划包每轮多出几 KB 到几十 KB。
  - C（不采用）：允许 `PROPOSE_METHOD` 只写 `based_on` 不写做法，系统拒收并把原文塞进拒绝信息——拿"拒绝"当"读取"，是旁路。
- 决定类型清单要改的地方：`SDK/contracts/planning_decisions.py`（枚举 `PlanningDecisionType` 约 :95、载荷类、`PAYLOAD_BY_DECISION_TYPE` 约 :1700）、`SDK/planning/decision_codec.py`、准入 `SDK/planning/decision_admission.py`、规划授权的默认决定表 `SDK/governance/planning_authorization.py` `PLANNING_DECISIONS`、重放覆盖清单（新事件）。这些文件都不在编码清单里。记为与原计划 §11"八种决定"的偏离（偏差单 5）。
- **提出做法时注明来源**：`PROPOSE_METHOD` 的 `method_proposal` 加可选 `based_on`（`SDK/planning/htn/registry.py` `MethodProposal.from_json` 约 :755-775 的可选字段）。`prepare_method` 用 `listed_entries` 核对它在列且目标类型一致，不在列写问题 `LIBRARY_ENTRY_UNAVAILABLE`（可修，照常进拒绝反馈）；核对通过后写进 `PlanningMethodProposed` 事件明细。
- 不做任何"推荐哪条"：目录只按时间排，选不选、读不读、怎么改都由规划器判断。

### 1.7 退役：只数明确写出的归因，两个不同任务即退役

**结论：归因只有两个来源——规划器换做法时写明、根终审打回时审阅员写明；程序只数，按"不同任务"计，同一条全库做法累计 2 个任务归因即自动退役。主 Agent 另有一个手动退役入口。**

- **规划器**：`REPAIR / REPLACE_METHOD` 的载荷（`SDK/contracts/planning_decisions.py` `RepairReplaceMethodDecision` 约 :1197-1262）加可选 `method_at_fault`（一句理由，1～300 字）。这次换做法的计划提交成功时（同一事务），若被换下的做法有 `based_on`，写一条归因（来源 = 这次决定编号）。被拒的提交不记。
- **审阅员**：根终审正式记录导入时，有效结论为 REWORK/REJECTED 且 `methods` 里某行 `at_fault=true`、该做法有 `based_on`，写一条归因（来源 = 审阅记录编号）。
- **不算归因的**：叶子内容审阅、组合审阅、做法审阅的打回（它们看不到"是全库那份的错还是本任务改写的错"）；没有 `based_on` 的做法（它不在全库，无可退役）。
- **计数**：归因表主键（全库条目, 来源编号），同一来源只记一次；**按不同任务计数**——同一个任务里规划器和审阅员各写一次，只算一次（同一次失败）。达到 2 个任务时，在写第 2 条归因的同一事务把条目置"已退役"（`retired_by="attribution"`，理由合并两条原话），发 `MethodLibraryEntryRetired` 事件（在触发它的任务里）。
- **退役后**：目录不再列，读原文、`based_on` 校验都拒；已经基于它提出并在用的做法不受影响（那是本任务自己的做法）。
- **主 Agent 入口**：Host 新对话工具 `method_library`（动作 `list` / `retire`），经 SDK 门面 `list_method_library()`、`retire_library_entry(command)`（门面主体固定，命令号 `chat-method-retire:<run_id>:<call_id>`，同号重放回原回执，回执 kind `method_library_retired`）。确认规矩与建任务、改要求相同：手动模式工具调用照常请人点确认（中文文案"把全库做法 X（用途：…）退役，之后不再列给新任务"），自动模式不弹（用户 2026-09-07 决定 > 计划"用户点击确认"，偏差单 7）。工具描述写明"只在用户明确要求时用"。不加界面。

### 1.8 摘要（表二 12a）：用已有的 `summary` 字段，审阅员顺带核对，开放第四层

**结论：执行者结果信封不加字段——`summary` 本来就是必填字段，只把执行者提示词里它的用途写清；审阅员在步骤内容审阅时顺带核对它是否忠实于结果；核对通过的摘要以 `layer=summary` 出现在知识读工具的输出里（工具说明一字不动），推给执行者的上下文里那一格也改读同一处；规则截断的旧摘要整套删。**

- **现状**：所谓"规则截断的摘要"是 `SDK/memory/summaries.py` + `SDK/context/compression.py`：每次验收后按"分支"把每步 `summary` 截到 200 字、拼成分支摘要与全任务摘要，写进 `summaries` 表（`commit_service.py` 约 :2901、`operation_outcomes.py` 约 :587、:666 调用），由 `event_handler.py` 约 :5355-5390 读出、经 `context_builder.py` 约 :121-126 推给执行者（`branch_summary`/`global_summary`）。分层任务的步骤 `dependency_ids` 恒为空，"分支"其实就是每步自己。知识读工具明说"规则截断的摘要层不开放"（`SDK/context/knowledge_tools.py` 约 :13）。
- **结果信封加字段不可行也不需要**：`ResultEnvelope` 在 `contracts/models.py`（编码清单文件之一），加字段要重写清单哈希、改严格解码；而 `summary` 已是必填（`models.py` 约 :762、:862）。
- **执行者提示词第 7 版**（`SDK/runtime/role_templates.py` 约 :459-491）：`summary` 写成"给后面步骤和审阅员看的摘要：这一步产出了什么（文件路径、关键结论），只写结果里确实有的，300 字以内；审阅员会核对它是否忠实，核对通过的才进黑板摘要层"；黑板从三层改为四层，加一句"摘要（layer=summary）：已验收步骤的摘要，经审阅员核对忠实于原结果，带原结果引用与哈希；帮你快速了解别的步骤做了什么，要当事实用仍以原产物或已验证知识为准"。AppWorld（`appworld_templates.py` 约 :10）与无人机（`role_templates.py` 约 :619）两份执行者模板接在同一正文后，版本号一起升。系统不截断摘要（不再有任何规则截断）。
- **系统绑定原文哈希、审阅员核对**：步骤内容审查包加可选一节 `summary_to_confirm`：`{result_ref（结果信封指纹，与阶段 C 候选引用同一个指纹）, summary, summary_sha256}`（`ReviewPackage` 同 1.3；切包处是 `leaf_acceptance.py` 约 :552-594 填 `claims_to_confirm` 的地方）。回复加 `summary: {"faithful": true/false, "reason": "…"}`（与 1.3 同一版回复形状）；漏写算"未核对"；导入时核对 `summary_sha256` 与包里一致，写进正式记录清单。只核对、不影响这一步过不过。
- **摘要层**：一个函数 `step_summaries(store, mission_id)`，每个已验收步骤一行：`{layer:"summary", id:"sum:<结果编号>", source_task, summary, summary_sha256, result_ref, artifacts:[路径, 版本, 内容哈希], checked_by: 审阅记录编号}`。只列两条都成立的：①这一步的验收现在仍站着（与知识"是否当前"同一个判定 `memory/knowledge_standing.py` `acceptance_is_current` 约 :31）；②这次验收所依据的正式审阅记录里 `summary.faithful=true` 且哈希一致。人裁决通过、审阅员没写 `summary` 的，不列（只剩原始记录引用一层）。开工前裁决要求的"读摘要的工具结果带摘要版本与原文哈希"由 `summary_sha256` 与 `result_ref`、`artifacts` 满足。
- **黑板读工具怎么露出**：`knowledge_tools.py` `_catalogue`（约 :61-95）把上面的行接在已验证、候选之后、原始引用之前；`knowledge_read` 读 `sum:` 编号返回摘要全文与上述引用；输出里的 `notice` 文字（运行时数据，不是工具说明）加一句摘要层的用法。`TOOL_SCHEMAS`（`runtime/tool_gateway.py` 约 :46-118）一字不动，执行池身份不变。
- **推给执行者的那一格**：`context_builder.py` 的 `branch_summary`/`global_summary` 两键删，换成一个 `step_summaries`（取同一个函数，最多 8 条，按验收时间倒序）；上下文组装升 `context-builder-v6`（约 :43）。
- **删**：`memory/summaries.py`、`context/compression.py`、`Store.upsert_summary`/`list_summaries`（`storage/store.py` 约 :1024-1060）与诊断导出里的 `summaries` 键（约 :1725）、上述三处调用、迁移 40 `DROP TABLE summaries;`、重放覆盖清单 `summaries` 条目（约 :2078）。相关旧测试（`T/step04/test_claims_knowledge_main_loop.py`、`test_retrieval_context.py`、`test_schema_migration.py` 里摘要那几条）跟着删改。

### 1.9 新表与保证通道屏障的开销

**结论：全库做法不改做法定义表，另建两张表，并且这两张表不登记为保证通道源表——晋级、退役、归因一律不触发屏障。计划第 7 条担心的"每改一次做法表就给每个绑定过的任务各写一条变更事件"在本阶段不会发生。**

迁移 40（`SDK/storage/schema.py`，`DDL_V40`）：

```sql
DROP TABLE method_evaluations;
DROP TABLE summaries;
CREATE TABLE method_library (
 entry_id TEXT PRIMARY KEY,                      -- 'lib-' + 哈希
 owner TEXT NOT NULL,                            -- 租户/本人
 goal_type_id TEXT NOT NULL,
 catalog_digest TEXT NOT NULL CHECK(length(catalog_digest)=64),
 method_id TEXT NOT NULL, method_version INTEGER NOT NULL,
 method_hash TEXT NOT NULL CHECK(length(method_hash)=64),
 purpose TEXT NOT NULL,
 source_mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 root_review_record_id TEXT NOT NULL,
 based_on TEXT REFERENCES method_library(entry_id),
 state TEXT NOT NULL CHECK(state IN ('LISTED','RETIRED')),
 retired_by TEXT, retired_reason TEXT, retired_at REAL,
 promoted_at REAL NOT NULL,
 UNIQUE(owner, method_id, method_version),
 FOREIGN KEY(method_id, method_version) REFERENCES method_contracts(method_id, method_version)
) STRICT;
CREATE INDEX method_library_listing ON method_library(owner, goal_type_id, catalog_digest, state, promoted_at);
CREATE TABLE method_library_attributions (
 entry_id TEXT NOT NULL REFERENCES method_library(entry_id),
 source_ref TEXT NOT NULL,                       -- 规划决定编号或审阅记录编号
 source_kind TEXT NOT NULL CHECK(source_kind IN ('PLANNER','ROOT_REVIEW')),
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 method_id TEXT NOT NULL, method_version INTEGER NOT NULL, method_hash TEXT NOT NULL,
 reason TEXT NOT NULL,
 recorded_at REAL NOT NULL,
 PRIMARY KEY(entry_id, source_ref)
) STRICT;
```

（字段名、索引由施工方定稿，语义以此为准。）存取放新文件 `SDK/storage/method_library_store.py`；晋级、目录、读取、归因、退役的秩序放新文件 `SDK/orchestrator/method_library.py`。

为什么可以不进保证通道：屏障保护的是"某张验收证书读过的事实变了"。全库表只决定"给规划器看哪些先例"，没有任何证书读它；被别的任务用到的始终是那个任务自己提出、自己审过的做法定义（做法定义表行不可改，晋级不动它）。所以这两张表不加进 `storage/assurance_source_inventory.py`（`GLOBAL_TABLES` 约 :54 不动）；重放覆盖清单里登记为"全局"表（不按任务重放，阶段 G 单独核对），新事件 `MethodPromoted`、`MethodLibraryEntryRetired`、`PlanningLibraryRead` 当批登记。

**已有的开销如实记下，不在本阶段改**：做法定义表的插入触发器（`storage/assurance_barrier_v26.sql` 约 :3226-3243）——也就是每次"提出新做法"——今天就会给全局纪元加 1，并给**所有曾经绑定过的任务**（`assurance_mission_bindings` 不许删行，含已结束的）各写一条 `AssuranceEvidenceChanged`。本阶段的复用走"提出新做法"，不比今天多。这条按任务数线性增长的开销留到阶段 F 量一次、阶段 G 收尾时决定是否改成"只给未结束任务写"（要新迁移重建四张全局表的触发器），写进实施记录。

### 1.10 迁移、提示词、工具说明一览

| 项 | 本阶段 |
|---|---|
| 新迁移 | 40 号一个（上节）；1～39 文字不改，迁移 23 的建表文字原样挪位 |
| 编码清单 | 不动 |
| `TOOL_SCHEMAS` | 不动；执行池身份基线应原样通过（若执行者引用改成固定体后 Host 的执行池身份字节测试变了，按"钉哈希基线随当前版本重生成"重生成并在实施记录写明） |
| 规划器提示词 | 第 22 版、规划包 13：①`views.method_library` 是什么、只给一句用途、不能原样采用；②`READ_METHOD_LIBRARY` 的写法与上限；③`views.library_reads` 里的原文怎么用、链接编号指原任务、`method_id` 用新的；④`method_proposal.based_on`；⑤`REPLACE_METHOD` 的 `method_at_fault`（拿不准就不写） |
| 审阅员提示词 | 回复第 4 版：`methods`（只在根终审包有 `methods_to_judge` 时）、`summary`（只在步骤内容审查包有 `summary_to_confirm` 时）；两节对应包的说明。审阅策略指纹随之变，旧任务起不来（开发期不兼容） |
| 执行者提示词 | 第 7 版（AppWorld、无人机各升一版）：`summary` 的用途、黑板第四层 |
| 上下文组装 | `context-builder-v6`：`step_summaries` 取代两个规则摘要键 |
| 主 Agent 对话工具 | 新增 `method_library`（`list`/`retire`）；不是执行池工具 |
| 前端 | 不改（若该工具结果默认不显示，按阶段 E 的做法在 `F/chat/messageVisibility.ts` 约 :34 加一项） |

**旧编排库不可用**（迁移 40 + 四份提示词升版），联测用新建的编排数据目录（与 C、D、E 相同要求）。

---

## 二、现状核对表

| 计划说的 | 代码现在（文件:行） | 结论 |
|---|---|---|
| 前置：类型身份与任务无关 | 步骤类型判据已空（阶段 E）；步骤说明文字仍是用户原话、执行者引用按工具哈希、根类型带原话与判据（`Host/hierarchical.py` 约 :39-45、:93-95） | 1.1 四样改完；根改由绑定给出 |
| 根覆盖、子目标覆盖、发布来源唯一 | 根覆盖靠注册协议读类型判据（`registry.py` 约 :1950）；其余读绑定 | 根覆盖挪到提案检查读绑定；其余不动 |
| 晋级只留一条路 | 评测晋级五个文件 + 一张表还在；`htn_method_cohort.py`、`run_v14_runtime.py` 已不存在 | 1.2 逐处删，迁移 40 删表 |
| 晋级前可复用性审阅 | 无 | 并进根终审一节（1.3） |
| 做法表归属列 | 做法定义表无归属；桌面只有一个租户一个人 | 新全库表带归属（1.3、1.9） |
| 不可信资料不进全库 | 资料表 `trust` 恒为不可信；任务可声明不可信路径 | 任务级标记，带了就不晋级（1.3） |
| 复用也要审 | 闸门放行"本任务没审过的做法"（`method_plan_reviews.py` 约 :94-125） | 只放行种子（1.5） |
| 列出方式 | 规划包只有本任务注册表里的做法（`planner_views.py` 约 :89） | 加目录 + 只读决定（1.6） |
| 退役 | 无归因记录；注册表 `retire` 只在内存 | 两个来源、按任务计数、主 Agent 入口（1.7） |
| 绑定类型目录与规划包版本哈希、清空命令 | 无 | `catalog_digest`；命令行 `method-library clear`（C3-6） |
| 改做法表触发屏障 | 做法定义表插入、更新都触发（`assurance_barrier_v26.sql` 约 :3226-3279） | 全库表不进通道，晋级退役零屏障（1.9） |
| 摘要 | 规则截断 200 字、按"分支"拼、推给执行者；摘要层不开放 | 1.8 |

---

## 三、施工清单

**结论：9 步。最重的是 C3-5（规划侧：目录、读取决定、来源、归因、闸门、提示词）和 C3-7（摘要）。**

| 步 | 内容 | 依赖 | 迁移 | 编码清单 | 提示词 |
|---|---|---|---|---|---|
| C3-0 | 金丝雀核对 | — | 无 | 不动 | 不动 |
| C3-1 | 前置改造：类型与任务无关 | C3-0 | 无 | 不动 | 不动 |
| C3-2 | 删评测晋级；迁移 40；全库存取 | C3-0 | 40 | 不动 | 不动 |
| C3-3 | 审查包两节 + 审阅员回复第 4 版 | C3-0 | 无 | 不动 | 审阅员 v4 |
| C3-4 | 晋级（完成写方）+ 不可信标记 | C3-1、C3-2、C3-3 | 无 | 不动 | 不动 |
| C3-5 | 规划侧：目录、读取决定、`based_on`、换做法归因、采用闸门、规划器 v22 | C3-1、C3-2 | 无 | 不动 | 规划器 v22、包 13 |
| C3-6 | 退役计数与自动退役、门面、Host 对话工具、命令行清空 | C3-3、C3-5 | 无 | 不动 | 不动 |
| C3-7 | 摘要：审查包一节的使用、摘要层、推送槽、删规则摘要、执行者 v7 | C3-2、C3-3 | （随 40） | 不动 | 执行者 v7、上下文 v6 |
| C3-8 | 清单、文档、发版 | 全部 | — | 不动 | — |

### C3-0　金丝雀核对（半天；只跑单条用例或临时脚本，不改正式代码）
1. 同形世界里建两个目标、要求、工具都不同的任务，打印两边规划世界里五个类型的引用哈希：确认现在不等（预期：根、步骤、接续三个不等）。这条留作 C3-1 的回归。
2. 确认唯一完成写方（`assurance_final_writer.py` 约 :150-200）事务里拿得到被采纳的根结论与它依据的正式根终审记录。
3. 确认所有审阅目的共用一套回复解码与导入（`assurance/reviews.py`、`assurance_review_import.py`），`claims` 的解码与"范围错误"处理在哪，C3-3 照抄。
4. 确认 `PROPOSE_METHOD`/`REQUEST_HUMAN` 的服务决定路径（`event_handler.py` 约 :6620-6690）与叫醒事件集（约 :2989）能直接容纳新的只读决定。
5. 确认把 `method_evaluation_schema.py` 的建表文字挪进 `schema.py` 后迁移 23 的校验和不变（迁移测试单跑一条）。
6. 确认 `knowledge_tools.py` 能从一个已验收步骤找到"这次验收所依据的正式审阅记录"（C3-7 用）。
7. 确认规划世界能给出种子做法集合（`world.py` `install_library` 一带），C3-5 第 6 条用。
8. 确认 Host 执行池身份字节测试（`backend/tests/orchestration/test_pool_identity_bytes.py`）是否读任务类型的执行者引用；读的话 C3-1 后重生成基线。

### C3-1　前置改造：类型与任务无关
1. `Host/hierarchical.py`、`SDK/testing/product_world.py`：根、步骤、接续步骤说明文字改固定句；根覆盖判据置空；执行者引用改固定体；删 `current_criteria` 调用（1.1 第 1 条）。
2. `SDK/deployment/root.py`：加 `root_binding(...)`（1.1 第 2 条，含 `action:` 前缀过滤）；`initialize_root` 改用它。
3. `SDK/orchestrator/requirements_amendment.py` 约 :113-123：改用 `root_binding`；删末尾 `forget_planning_world` 调用；删 `event_handler.py` 约 :1291 该方法。
4. `planning_method_proposal.py`：目标一致检查改比四样；`_coverage_problems` 加根分支（1.1 连带核对表）。
5. `occurrence_tasks.py` 约 :541 叶子取 `mission.goal`；`root_review.py` 约 :1150 子步骤标签取参数 `goal`。
6. `planning/htn/world.py`：加 `catalog_digest(world)`。
7. 单跑阶段 E 与以前的相关用例：`T/product_world/test_requirements_amend.py`、`test_full_circle.py`、`test_sub_goal.py`、`test_write_targets.py`、`test_repair_replace_method.py`。

### C3-2　删评测晋级；迁移 40；全库存取
1. 按 1.2 表逐处删；`publish_methods` 改为只灌本任务试用范围的做法。
2. `storage/schema.py`：`DDL_V23` 改成字面量（原文）；加 `DDL_V40` 与 `Migration(40, "orchestrator-method-library-and-drop-rule-summaries", DDL_V40)`。
3. 新文件 `storage/method_library_store.py`：插入、按条件列出、置退役、写归因（主键冲突即"已记过"）、按条目数不同任务、清空。
4. 重放覆盖清单：删 `method_evaluations`、`summaries`；加两张全局表（写方、事件）；守护测试跟着过。部署清单重生成。

### C3-3　审查包两节 + 审阅员回复第 4 版
1. `contracts/resolution.py` `ReviewPackage`：加可选 `methods_to_judge`、`summary_to_confirm`（行字段固定，照 `claims_to_confirm` 的行校验写法）。
2. `root_review.py` `cut`：按 1.3 填 `methods_to_judge`（任务带不可信资料时只列有 `based_on` 的）。`leaf_acceptance.py` 切步骤内容审查包处：填 `summary_to_confirm`。
3. 回复解码：加 `methods`、`summary` 两个字段；表外做法、哈希不符按可修的"范围错误"重问；`purpose` 长度 1～120。`REVIEW_CODEC_VERSION` 升 v4。
4. 导入：两样写进正式记录清单（与 `claims` 并列）。
5. `REVIEW_INSTRUCTIONS`：形状改 4，按 1.3、1.8 写两节说明与字段含义。

### C3-4　晋级
1. 新文件 `orchestrator/method_library.py`：`library_owner(store, mission)`、`mission_untrusted_input(store, mission)`、`promote_methods(store, mission_id, resolution, digest)`（1.4）。
2. `assurance_final_writer.py` 写 `MissionCompleted` 的同一事务里调用。类型目录哈希由该任务的规划世界给出（完成写方经编排器取世界；取不到就不晋级并记事件，不让完成失败）。
3. `MethodPromoted` 事件、"跳过：任务带不可信资料"事件登记进重放覆盖清单。

### C3-5　规划侧
1. `method_library.py`：`listed_entries(...)`（目录、读取、`based_on` 三处共用）。
2. `planner_views.py`：加 `views.method_library`（每类型 5 条、倒序、报省略数）与 `views.library_reads`。
3. `READ_METHOD_LIBRARY`：合同、解码、准入、授权默认表、服务决定处理、叫醒事件、每任务 3 次上限（1.6）。
4. `registry.py` `MethodProposal`：可选 `based_on`；`prepare_method` 核对并写进事件明细。
5. `RepairReplaceMethodDecision`：可选 `method_at_fault`；换做法的计划提交成功时写归因（C3-6 的计数函数）。
6. 闸门 `unreviewed_adopted_methods`（1.5），种子集合由世界给出。
7. 规划器提示词第 22 版、规划包 13（1.10 表）；钉哈希的模板测试按当前版本重生成。

### C3-6　退役与两个入口
1. `method_library.py`：`record_attribution(...)`（写一条 → 数不同任务 → 到 2 即同事务退役 + 事件）；根终审导入处（C3-3 第 4 条）调用；C3-5 第 5 条调用。
2. SDK 门面 `SDK/api/facade.py`：`list_method_library()`、`retire_library_entry(command)`（字段固定：`entry_id`、`command_id`、`reason`；门面主体固定；同号重放回原回执）。
3. Host：`Host/chat_tool.py` 加 `method_library` 工具（描述、参数表、中文确认文案）；`Host/service.py` 转调门面；`backend/main.py` 注册；`backend/deskpet/sdk_adapters/tools.py` 的产品工具表、`tool_authority.py` 的常驻工具表与确认文案各加一项；钉住这些表的 Host 测试跟着改。
4. SDK 命令行 `SDK/__main__.py`：`method-library list --db <库>`、`method-library clear --db <库> --yes`（只删两张全库表的行，打印删了几条；不碰做法定义表）。

### C3-7　摘要
1. `knowledge_tools.py`：`step_summaries(...)` 与第四层行、`knowledge_read` 读 `sum:`、`notice` 文字（1.8）。
2. `context_builder.py` 与 `event_handler.py` 约 :5340-5390：`step_summaries` 取代两个规则摘要键；`context-builder-v6`。
3. 删 `memory/summaries.py`、`context/compression.py` 与三处调用、存取方法、诊断导出键；改相关旧测试。
4. 执行者提示词第 7 版（三份）；钉哈希的模板测试重生成；Host 执行池身份字节测试应原样通过。

### C3-8　清单、文档、发版
1. 实施记录（含 1.9 末段那条已有开销、偏差单处理结果）、`ARCHITECTURE/`、`需求与场景状态.md`（由阶段评估子代理按计划第六节更新）同次更新；写明"旧编排库不可用，联测用新建编排数据目录"。
2. 本阶段末发版一次、Host 钉版。真机按第 3.5 版流程放到全部代码写完后（计划第 9 条：两个同类任务串起来，第一个完成且根终审通过 → 晋级；第二个任务的规划包目录里列出它，选不选不断言；退役用脚本化通道测）。

---

## 四、功能性用例清单（12 条）

**结论：SDK 10 条跑在 `SDK/testing/product_world.py` 上（同一个世界里建多个任务即共享同一个库）、用 `SDK/testing/scripted_replies.py` 的分层脚本化通道；Host 2 条；前端不加。**

| # | 用例名 | 文件 | 剧本 | 断言 |
|---|---|---|---|---|
| 1 | `test_types_do_not_depend_on_the_mission` | `T/product_world/test_method_library.py` | 同一世界建两个任务：目标、要求条数、允许工具都不同 | 两边 `catalog_digest` 相等、五个类型引用相等；两个根绑定签名的说明文字各是各的原话、判据各是各的内容要求编号；对根提出漏链一条要求的做法 → 按 `ROOT_COVERAGE_GAP` 退回（不花审阅） |
| 2 | `test_promoted_after_delivery_and_final_review` | 同上 | 任务 A 两步完成；根终审回复 `methods` 对根做法 `reusable=true`、写了用途；参数化：①正常；②`methods` 漏写；③任务 A 登记过一份资料；④任务 A 终审打回后失败 | ①全库一行：归属、目标类型、目录哈希、用途、来源任务、根终审记录编号、在列，一条 `MethodPromoted`；②③④全库无行，③有一条"跳过：带不可信资料" |
| 3 | `test_directory_read_and_derived_proposal` | 同上 | 接 2①，同一归属建任务 B；规划器第 1 轮看目录 → `READ_METHOD_LIBRARY` → 第 2 轮 `PROPOSE_METHOD`（新 `method_id`、`based_on`=该条）→ 审阅通过 → 采用 → 完成 | 第 1 轮目录行只有编号、类型、用途、日期（没有步骤）；第 2 轮 `library_reads` 有原文；读取决定没改任何计划状态；B 里开了一份做法审阅，通过前计划提交被闸门拒；事件明细带 `based_on`；B 完成后它自己的做法也晋级，`based_on` 指向 A 那条 |
| 4 | `test_directory_filters` | 同上 | 接 2①；参数化：①另一租户的任务；②把规划包版本号打补丁改一个数；③条目已退役；④同一目标类型 7 条 | ①②③目录为空、读取与 `based_on` 都被拒；④只列 5 条、按晋级时间倒序、报省略 2 |
| 5 | `test_adopted_method_needs_review_here` | 同上 | 直接往注册表塞一个本任务没审过、不是种子的做法并让规划器采用 | 计划提交按 `METHOD_NOT_AUTHORIZED` 拒，明细 `review: NONE` |
| 6 | `test_retired_after_two_missions_blame_it` | 同上 | 全库一条 E；任务 B、C 都基于 E 提出做法；B 里规划器换做法写 `method_at_fault`，同一任务里根终审又写 `at_fault`；C 里根终审打回写 `at_fault` | B 之后归因 2 条、按任务计 1，仍在列；C 之后计 2，E 已退役（`retired_by=attribution`），一条退役事件，目录不再列 |
| 7 | `test_manual_retire_and_clear` | 同上 | 门面 `retire_library_entry`；同一命令号再来一次；命令行 `method-library clear --yes` | 退役 `retired_by=user`、回执一条、重放不重复写；清空后两张全库表为空、做法定义表行数不变 |
| 8 | `test_library_writes_do_not_touch_the_barrier` | 同上 | 晋级、归因、退役各做一次，前后读全局纪元与 `AssuranceEvidenceChanged` 事件数 | 全局纪元不变、事件数不变 |
| 9 | `test_checked_summary_layer` | `T/product_world/test_blackboard_tools.py`（追加） | 两步任务；第 1 步审阅回复 `summary.faithful=true`；参数化：②`faithful=false`；③第 1 步被修复、出第二次验收 | ①第 2 步执行者 `knowledge_list` 有一行 `layer=summary`，带 `summary_sha256`、`result_ref`、产物哈希、`checked_by`；`knowledge_read sum:` 返回全文；推给执行者的 `step_summaries` 是同一行；②没有摘要行，原始引用行仍在；③旧摘要不再出现，新的出现 |
| 10 | `test_old_paths_are_gone` | `T/full_target/test_c3_guards.py` | 扫源码 + 新库 | 源码里没有 `method_evaluation`、`method_lifecycle`、`memory.summaries`、`context.compression` 的导入；新库没有 `method_evaluations`、`summaries` 两张表，有两张全库表；迁移 23 校验和与发布时一致 |
| 11 | `test_desktop_world_is_mission_independent` | `backend/tests/orchestration/test_hierarchical_world.py`（新建或追加） | Host `planning_world` 对两个不同任务各建一次 | `catalog_digest` 相等；执行者引用不含工具清单；根类型无判据 |
| 12 | `test_method_library_tool` | `backend/tests/orchestration/test_chat_method_library.py` | 对话工具：`list`；`retire` 正常；编号不存在；同一调用重放 | 列表有用途与状态；退役成功；不存在按名拒、库里没写；重放不重复写 |

另外：回复解码的两条小断言（非根终审包写了非空 `methods`、非内容审查包写了非空 `summary` → 范围错误重问）追加进现有 `T/product_world/test_review_reply_format.py`。

**改坏检验（每个功能一条；改前 `cp` 备份，改后清 `__pycache__`，不用 git 恢复）：**

| 功能 | 改坏哪里 | 应失败的用例 |
|---|---|---|
| 类型与任务无关 | 步骤类型说明文字改回用户原话 | 1、11 |
| 根覆盖在提案时查 | 删 `_coverage_problems` 的根分支 | 1 |
| 晋级只认审阅员判可复用 | 不看 `reusable`，采用的做法全升 | 2② |
| 不可信标记 | 去掉 `mission_untrusted_input` 判断 | 2③ |
| 归属与哈希过滤 | `listed_entries` 去掉归属条件 / 去掉目录哈希条件 | 4①/4② |
| 按需读取 | 读取决定不写 `library_reads` | 3 |
| 采用闸门 | 恢复"本任务没审过就放行" | 5 |
| 退役按任务计 | 改成数归因行数 | 6 |
| 不触发屏障 | 晋级改为改做法定义表状态（`set_method_registration`） | 8 |
| 摘要必须核对过 | `step_summaries` 不看 `faithful` | 9② |
| 摘要随验收失效 | `step_summaries` 不看验收是否仍站着 | 9③ |

只跑这些用例与被改文件直接对应的旧用例（`T/product_world/test_requirements_amend.py`、`test_full_circle.py`、`test_sub_goal.py`、`test_write_targets.py`、`test_repair_replace_method.py`、`test_blackboard_tools.py`、`test_knowledge_confirm.py`、`test_review_reply_format.py`；`T/full_target/test_htn_novel_method_admission.py`、`test_method_proposals.py`、`test_plan_commits.py`；`T/step04/test_schema_migration.py` 等摘要相关三个；Host 的 `test_chat_mission_start.py`、`test_chat_mission_amend.py`、`test_handlers_contract.py`、`test_pool_identity_bytes.py`）；不跑整目录、不跑全量；无关用例红了记下另行处理。

---

## 五、偏差单（12 条）

**结论：12 处与计划字面不同，都已裁决；没有一条需要用户本人决定。第 1、4、7、8、10、12 条改变用户看得到的行为或工期，建议报用户知悉。**

| # | 计划原文 | 发现的事实 | 裁决 | 理由 |
|---|---|---|---|---|
| 1 | 第 3 条"新任务里选用全库做法时，照样过做法审阅"（字面是在新任务里直接采用同一份做法） | 做法的要求链接写的是原任务的 `c-user-N`、步骤参数是原任务的原话与文件名；直接采用要在采用时做编号映射，并改编译、完成范围、验收、终审等十几处读链接的代码 | 全库做法只当先例：规划器读原文后提出本任务自己的新做法（注明 `based_on`），过本任务的做法审阅；不做编号映射、不原样采用 | 只留"提出做法"一条路；语义上的改写由规划器做、审阅员把关（**报用户知悉**：每次复用都多一次做法审阅；规划器要按本任务重写一遍） |
| 2 | 第 1 条"状态枚举复用"、第 2 条"做法表加归属列" | 改做法定义表的状态与列会触发保证通道屏障，且晋级、退役与任何证书无关 | 另建全库做法表与归因表，归属列在全库表上；做法定义表一个字不改；状态枚举不动 | 不碰编码清单、不碰屏障；同一事实只记一处 |
| 3 | 第 2 条"由审阅员判…是否通用"（未定时点与审查包） | 六种审阅目的封闭；根终审本来就在"交付成功且根终审通过"这个时点 | 并进根终审：审查包加 `methods_to_judge`，回复加 `methods`（同时给一句用途、是否做法的错） | 不多一次调用、不新增审阅目的 |
| 4 | 第 2 条"不可信资料里来的文字不进全库做法" | 逐字追踪文字来源做不到可判定，做了就成了关键词规则 | 任务级标记：登记过资料或声明了不可信路径的任务，其做法一律不晋级，如实记一条 | 秩序上可判定（**报用户知悉**：带资料的任务做出的做法不会进全库） |
| 5 | 第 4 条"原文由规划器按需读取" | 规划器没有工具 | 新增只读决定 `READ_METHOD_LIBRARY`（不改状态、每任务 3 次），下一轮规划包带原文；记为与原计划 §11"八种决定"的偏离 | 照"取证"那样一来一回；目录里不放原文、不拿拒绝当读取 |
| 6 | 第 5 条"审阅员或规划器在打回、换做法时明确写出'归因于做法'" | 叶子、组合、做法审阅都看不出"是全库那份的错还是本任务改写的错"；同一次失败可能被规划器和审阅员各说一遍 | 只收两处：规划器换做法写 `method_at_fault`、根终审打回写 `at_fault`；只对有 `based_on` 的做法记；按不同任务计数，2 个任务即退役 | 程序只数明确写出的归因；同一次失败不重复计 |
| 7 | 第 5 条"用户点击确认后执行（不加界面）" | 用户 09-07：自动模式不弹任何授权提示；阶段 E 改要求已按"与建任务同一规矩"处理 | 退役入口按权限模式：手动模式工具确认、自动模式不弹；工具描述限定"只在用户明确要求时用" | 计划第〇节"用户已作的决定 > 原计划"（**报用户知悉**） |
| 8 | 第 2 条"归属列（用户/工作区）" | 桌面只有一个租户一个人，没有"工作区 / 项目"身份 | 归属 = 租户 + 本人；不造工作区这一层 | 不发明产品里没有的概念（**报用户知悉**：本机所有任务共用一个全库） |
| 9 | 前置改造"原话和判据从类型里移出，由绑定传入"；阶段 E 实施记录偏差 5"根类型仍带现行要求" | 根类型带原话与判据则根做法无法按目标类型跨任务筛选 | 阶段 E 清单 1.4 末条的备选现在做：根类型通用，根绑定签名带原话与内容判据；删"改要求后重建规划世界" | 前置改造的本意 |
| 10 | 表二 12a"由执行者交结果时一并写摘要…开放黑板摘要层" | `summary` 已是必填字段；结果信封在编码清单文件里；规则摘要按"分支"拼，在分层任务里分支就是单步 | 不加字段，执行者提示词写清 `summary` 用途；审阅员顺带核对；只开放核对过的摘要；规则截断摘要、`summaries` 表、两个推送键整套删，推送那一格改读同一摘要层 | 一件事一条路（**报用户知悉**：执行者上下文里原来的"分支摘要 / 全任务摘要"换成"核对过的各步摘要"，没核对过的不推） |
| 11 | 第 1 条点名删 `evaluation/htn_method_cohort.py`、`scripts/acceptance/run_v14_runtime.py` | 两个文件已在阶段 A′ 删掉；还在的是 `run_h6_method_evaluation.py`、注册表里永远拒绝的晋级存根、字面相似推荐 | 按 1.2 表删现存的；字面相似推荐一并删（计划第 4 条"不做按字面重合度的推荐"） | 如实记 |
| 12 | 第七节 C3+摘要"4～6 天" | 多出只读决定、摘要整套替换、两个入口 | 估 6～8 天 | 如实记（**报用户知悉**） |

**主计划下一版（第 3.15 版）要改的文字（可直接粘贴）：**
- 标题改"第 3.15 版"。
- 表二第 1b 条做法要点改为："全库做法只当先例：交付成功且根终审通过、审阅员在根终审里判可复用的做法进全库（带归属、类型目录哈希、一句用途、来源任务与根终审记录）；新任务规划包只列目录，规划器用只读决定读原文后提出本任务的新做法（注明来源），照常过本任务做法审阅；明确归因于做法的失败按不同任务计，2 次退役；先做类型与任务无关的前置改造。"
- 表二第 12a 条做法要点改为："不新增角色、不加字段：执行者结果里必填的 `summary` 就是摘要，提示词写清用途；系统绑定结果指纹与产物哈希，审阅员审该步时顺带核对忠实；核对过的摘要作为黑板第四层经知识读工具读出，推给执行者的摘要也读同一处；规则截断摘要整套删。"
- 阶段"C3+摘要"第 1～8 条后加一句："按 `HTN补齐-阶段C3摘要-开工裁决与施工清单.md` 施工（9 步 C3-0～C3-8），偏差单 12 张已改入。"前置改造一句末尾的括号改为"（'步骤类型不带本任务判据'已在阶段 E 做；其余与'根类型通用、原话与判据只在根绑定上'在 C3-1 做）"。第 7 条改为："全库做法另建表、不进保证通道，晋级、退役不触发屏障；'提出新做法'本来就有的屏障开销如实记入实施记录，阶段 F 量、G 定。"
- 第二节"本计划新增的偏离"末尾加："新增只读规划决定 `READ_METHOD_LIBRARY`（原计划 §11 八种决定之外）；全库做法不在别的任务里原样采用，只作先例；做法定义表的状态值 `EVALUATED`/`ADMITTED` 等从此没有写方。"
- 第七节工作量表 C3+摘要改为"6～8 天"。
- 修订记录加："**第 3.15 版（2026-10-03）**：阶段 C3+摘要开工裁决（`HTN补齐-阶段C3摘要-开工裁决与施工清单.md`）12 张偏差单改入：全库做法只当先例、读原文后提出新做法；全库另建表、不触发屏障；可复用性并进根终审；不可信资料按任务级标记；新增只读决定；归因只收两处、按任务计数；退役入口按权限模式；归属=租户+本人；根类型通用；摘要复用必填字段、只开放核对过的；删除清单按现状更正；工期。"

---

## 六、不在本阶段做的

| 事项 | 去处 |
|---|---|
| 全局源表（做法定义、策略）的屏障只给未结束任务写变更事件 | 阶段 F 量开销、阶段 G 定（要新迁移） |
| 晋级与退役的崩溃切点（完成写方事务、归因写入）、24 行来源表里对应的需求行、真机两局串联 | F（真机按第 3.5 版流程放到全部代码写完后） |
| 全库两张表的重放核对（全局表，不按任务重放） | G |
| 执行图共用建议里的字面相似（`grounding.py` `suggest`）、"共用"与"沿用 + 重审" | TaskGraph 补全 |
| 叶子、组合、做法审阅打回时的归因 | 不做（偏差单 6） |
| 在别的任务里原样采用全库做法、要求编号映射 | 不做（偏差单 1） |
| 知识目录行里的 200 字预览（它是预览，工具已提示"读原文"，不是摘要层） | 不动 |
| 多模型、两人审批、NanoJev、42 组×3 局、金额、跨领域、组级管理员、学习 | 用户已定暂不做（全库做法按类型目录哈希天然只在桌面领域内；不做任何打分排序） |
