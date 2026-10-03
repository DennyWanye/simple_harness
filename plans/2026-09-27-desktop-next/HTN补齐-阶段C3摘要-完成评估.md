# HTN 补齐 · 阶段 C3+摘要（做法跨任务复用与摘要）：完成评估

- 评估人：独立阶段完成评估子代理。日期 2026-10-03。
- 依据代码：工作树 `simple_harness-a4`，分支 `htn-c3`（`bdbb0dc9`），与 `main` 的分叉点 `85c465d5`。对照文件：`HTN补齐-阶段C3摘要-开工裁决与施工清单.md`（下称"清单"）、`HTN补齐计划-2026-10-02.md` 第 3.16 版（下称"主计划"）、`HTN补齐-实施记录.md` 末节"阶段 C3+摘要"（下称"记录"）、仓库根 `CLAUDE.md`。
- 我做了什么：读了本分支全部改动（83 个文件）；只跑了下面几条单测，没有跑整目录、没有跑全量：
  - SDK：`T/product_world/test_method_library.py` 全部 10 条、`T/full_target/test_c3_guards.py`、`T/product_world/test_blackboard_tools.py::test_checked_summary_layer`（3 支）——**14 条全过**（98 秒）；
  - Host：`backend/tests/orchestration/test_chat_method_library.py`（5 条）、`test_hierarchical_host_integration.py::test_desktop_world_is_mission_independent`——**6 条全过**（用主仓库 backend 虚拟环境，`PYTHONPATH` 指向本工作树的 backend 与 SDK 源码）；
  - 两个临时探针（只在我的临时目录，没进仓库）：①根终审打回并写 `at_fault`，归因是否真的经导入路径写进归因表——**通过**（写入一行 `ROOT_REVIEW`）；②回复解码对"值为空的多余字段"的容忍——**`summary` 对象里多一个空字段会被拒收**（见必须改第 2 项）。
- 路径缩写同清单：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`Host/` = `backend/deskpet/orchestration/`。行号是本分支现行代码的行号。

---

## 结论

**可以合并，合并前必须改 2 项（都是小改动，各几行）**：

1. **晋级出错只兜住了四类异常**（`SDK/orchestrator/assurance_final_writer.py:239` `_promote_methods` 只捕获 `StoreError / ContractError / KeyError / ValueError`）。数据库约束错误（`sqlite3.IntegrityError`）、`TypeError`、`AttributeError` 等会直接穿出，让整个"任务完成"事务回滚——这正是清单 C3-4 第 2 条、用户点名的高风险点"晋级出错不让任务完成失败"。改法：捕获 `Exception`（保存点照旧回滚、照旧记 `MethodPromotionSkipped` 并带错误原文）。
2. **审阅员回复里的 `summary` 对象不容忍"值为空的多余字段"**（`SDK/assurance/checks.py:393` `decode_review_reply`，:410-413 的容忍循环只处理 `assessments / findings / claims / methods` 四个数组，`summary` 是对象，没进去）。实测 `{"summary": {"faithful": true, "reason": "r", "note": ""}}` 被拒为 `OBJECT_FIELDS_UNKNOWN`，而同样写法在 `claims`、`methods` 里被容忍。这违反用户 2026-10-02 定的回复格式口径（"忽略值为空的多余字段"），新字段不能例外。改法：`summary` 是对象时同样过一遍 `_drop_empty_extras(value["summary"], _SUMMARY_KEYS)`，并在 `test_review_reply_format.py` 或回复说明测试里加一条断言。

其余发现都不阻断合并：**建议合并前顺手改 6 项、之后再补 5 项**（见第二节），**未登记偏差 21 条**（U1～U21，含上面 2 项必须改与 1 条记录缺口，其余建议补登记），**已登记 9 条偏差：8 条接受、1 条有条件接受**（第三节）。

主体按清单做了：类型与任务无关、评测晋级与规则摘要整套删（迁移 40）、根终审判可复用后在唯一完成写方里晋级、目录 + 只读决定 + `based_on`、采用闸门只放种子、两处归因按任务数退役、主 Agent 与命令行入口、核对过的摘要层，以及四份提示词升版。`TOOL_SCHEMAS`、编码清单五个文件、保证通道源表清单与屏障 SQL 都没动；迁移 1～39 文字没动（迁移 23 原样挪位，守护测试钉住校验和）。没有新增兼容或旧路分支。

---

## 一、逐条对照

### 1.1 第一节裁决 1.1～1.10

| 裁决 | 结论 | 位置 | 说明 |
|---|---|---|---|
| 1.1-1 类型一律通用（根、步骤、接续固定句，根无判据，执行者引用固定体，世界不读要求） | 做了 | `Host/hierarchical.py:23-37`、`:85-87`；`SDK/testing/product_world.py:45-58`、`:88` | Host 与同形世界两边一致；`current_criteria` 调用已删 |
| 1.1-2 `root_binding`（原话 + 内容判据、`action:` 过滤挪进 SDK、`requirement_refs` 全部编号、合同哈希含签名） | 做了 | `SDK/deployment/root.py:136` `root_binding`；`:167` `initialize_root` | Host 的 `action:` 过滤已删，只留 SDK 一份 |
| 1.1-3 删 `forget_planning_world`，改要求用同一函数 | 做了 | `SDK/orchestrator/requirements_amendment.py` 约 :113-121；`event_handler.py` 原方法已删 | 改要求取当前缓存世界的根类型定义 |
| 1.1-4 `catalog_digest`（类型 + 谓词 + 规划包版本） | 做了 | `SDK/planning/htn/world.py:387` | 用例 1、11 证明两任务相等 |
| 1.1 连带：目标一致检查比四样 | 做了 | `SDK/orchestrator/planning_method_proposal.py:69` `_goal_identity` | |
| 1.1 连带：`_coverage_problems` 加根分支（`ROOT_COVERAGE_GAP`） | 做了 | 同文件 `:76` | 用例 1 证明不花审阅 |
| 1.1 连带：叶子"整个任务"取 `mission.goal`；根终审子步骤标签取参数 `goal` | 做了 | `SDK/orchestrator/occurrence_tasks.py` 约 :541；`root_review.py` 约 :1152 | |
| 1.2 评测晋级逐处删（存储、生命周期、世界两方法、注册表口子与 `promote`、字面相似推荐、H6 脚本与夹具、建表文字挪位、表、清单条目） | 做了，**文档一处没改** | 文件删除见 diff；`registry.py` `suggest_for` 字面一支已删；`schema.py` `DDL_V23` 字面量 | `sdk/simple-harness-sdk/ARCHITECTURE/index.md:431` 仍写着 `method_lifecycle.py`（清单 1.2 末行"改一句"没做）；重放清单里 `commit_receipts.gaps` 还留一行"经 `MethodEvaluationStore.record_oracle`（做法评测）不发事件"（`SDK/observability/business_replay_inventory.json:760`） |
| 1.2 `publish_methods` 只灌本任务试用范围的做法 | 做法不同（更宽一点） | `SDK/planning/htn/world.py:529` `restore_trial_methods` | 灌回的是"试用范围是本任务"的全部状态（含被本任务暂停的），不只 `TRIAL_ADMITTED`；见未登记偏差 U2，可接受 |
| 1.3 根终审包加 `methods_to_judge`（只列本任务提出且本任务审过的已采用做法；带不可信资料只列有 `based_on` 的） | 做了 | `SDK/contracts/resolution.py` `ReviewPackage`；`SDK/orchestrator/method_library.py:81` `adopted_methods`、`:107` `methods_to_judge`；`root_review.py:908` | |
| 1.3 回复 `methods`（漏写不拒、表外按范围错误重问、用途 1～120） | 做了 | `SDK/assurance/checks.py:268` `MethodJudgement`、约 :359-372；`assurance_review_import.py` `_interpret_reply` 内 `METHOD_SCOPE` | |
| 1.3 审阅员提示词、回复第 4 版 | 做了 | `SDK/assurance/review_input.py` `REVIEW_INSTRUCTIONS`；`reviews.py` `REVIEW_CODEC_VERSION = v4` | |
| 1.3 归属 = 租户/本人 | 做了 | `method_library.py:46` `library_owner` | |
| 1.3 不可信资料任务级标记 | 做法不同（已登记偏差 1） | `method_library.py:53` `mission_untrusted_input` | 看 `SourceRegistered` 事件与声明的不可信路径 |
| 1.4 唯一完成写方同事务晋级、只读被采纳根结论依据的正式记录、`reusable=true` 且有用途、逐条再核秩序、已在列跳过、每条 `MethodPromoted` | 做了 | `assurance_final_writer.py:189`（写 `MissionCompleted` 之后、同一事务）、`:239`；`method_library.py:131` `promote_methods` | 类型目录哈希取自提出做法时的事件（未登记偏差 U1）；异常兜底不全（必须改 1） |
| 1.4 人裁决通过照读审阅员写的 `methods`；失败/停止/取消不晋级 | 做了 | 同上（读 `resolution.review_receipt_id` 指向的记录）；全仓 `promote_methods` 只有这一个调用方 | |
| 1.5 闸门改名 `unreviewed_adopted_methods`、只放种子、拒绝码 `METHOD_NOT_AUTHORIZED` + `review: NONE` | 做了 | `SDK/orchestrator/method_plan_reviews.py:94`；`plan_commits.py` `_check_method_reviews`；种子 `world.py:494`；命令里带种子 `hierarchical_dispatch.py` 约 :3553 | 文档串"只在保证通道上生效"已过时（现在无条件生效），见 U16 |
| 1.6 目录 `views.method_library`（四个过滤、每类型 5 条、倒序、报省略） | 做了 | `planner_views.py:212`；`method_library.py:175` `listed_entries`、`:183` `directory` | |
| 1.6 只读决定 `READ_METHOD_LIBRARY`（合同、准入、授权、服务决定、叫醒、每任务 3 次） | 做了 | `contracts/planning_decisions.py:1709`；`decision_admission.py` 约 :1181；授权表由 `ENABLED_DECISIONS` 自动派生；`event_handler.py` 约 :6635-6666、叫醒集约 :3008；`planning_method_proposal.py:165` `read_library` | 读取的说明文字放在规划器提示词，没放进 `library_reads` 每行（U9） |
| 1.6 `PROPOSE_METHOD.based_on`，`prepare_method` 核对、写进事件 | 做了 | `registry.py` `MethodProposal.based_on`；`planning_method_proposal.py:150`、`:192` `proposal_origin` | |
| 1.7 规划器 `method_at_fault`，换做法提交成功同事务记归因 | 做了（无用例） | `contracts/planning_decisions.py:1208`；`event_handler.py:1291` `_blame_replaced_method`，在计划提交事务内调用（约 :7182） | 已登记偏差 3 |
| 1.7 根终审打回 `at_fault` 记归因 | 做了（无正式用例，我的探针跑通） | `assurance_review_import.py:530` `_record_method_blame`、`:624` | |
| 1.7 按不同任务计、2 个即同事务退役、退役事件在触发任务里 | 做了 | `method_library.py:225` `record_attribution`、`:250` `retire_entry` | |
| 1.7 主 Agent 入口与门面、命令号、回执、确认文案 | 做了 | `Host/chat_tool.py:292`；`Host/service.py` 约 :1150；`backend/main.py` 约 :8762；`sdk_adapters/tools.py`、`tool_authority.py`；`SDK/api/facade.py:439`、`:446`；`method_library.py:272` `retire_by_command` | |
| 1.8 执行者提示词第 7 版（三份）写清 `summary` 用途、黑板四层 | 做了 | `runtime/role_templates.py` 约 :478-521、:643；`appworld_templates.py`；`governance/domains.py` 模板表 | |
| 1.8 步骤内容审查包 `summary_to_confirm`、回复 `summary`、导入记哈希 | 做了 | `leaf_acceptance.py:612` `_summary_section`；`assurance_review_import.py` `_interpret_reply`（`SUMMARY_SCOPE`） | 容忍规则漏了 `summary`（必须改 2） |
| 1.8 `step_summaries`（验收仍站着 + 记录 `faithful` + 哈希与结果指纹一致；人裁决未写不列） | 做了 | `SDK/context/knowledge_tools.py:64` | |
| 1.8 读工具第四层、`knowledge_read sum:`、`notice` 加一句；`TOOL_SCHEMAS` 不动 | 做了 | 同文件 `:112` `_catalogue`、`:155` `read_knowledge_tool` | `knowledge_read` 读摘要行时 `notice` 仍是"This claim is not verified…"（U14） |
| 1.8 推送槽换成 `step_summaries`、`context-builder-v6` | 做了 | `context_builder.py:43`、约 :121；`event_handler.py:5402`（最多 8 条，不含本步） | 不含本步是已登记偏差 7 |
| 1.8 删规则摘要整套 | 做了 | `memory/summaries.py`、`context/compression.py` 删；`store.py` 两方法与诊断键删；三处调用删；`policies.py` 版本源删；迁移 40 `DROP TABLE summaries` | |
| 1.9 迁移 40 两张新表、不进保证通道 | 做了 | `storage/schema.py` `DDL_V40`、`Migration(40, …)`；`storage/method_library_store.py` | `assurance_source_inventory.py` 未改，`GLOBAL_TABLES` 仍只有四张旧表 |
| 1.9 重放清单：两张表登记全局、三种新事件当批登记 | 部分 | `business_replay_inventory.json` 两张表已登记为 `global` | 四种新事件（`MethodPromoted`、`MethodPromotionSkipped`、`MethodLibraryEntryRetired`、`PlanningLibraryRead`）清单里一处都没有；退役命令写 `commit_receipts`，该表的 `events` 也没列 `MethodLibraryEntryRetired`（U13） |
| 1.9 末段已有开销如实记下 | 做了 | 记录"已有开销如实记下"一条 | |
| 1.10 提示词、版本、主 Agent 工具、前端 | 做了 | 规划器 v22/包 13、审阅员回复 v4、执行者 v7（AppWorld、无人机 v3）、上下文 v6 | 前端没改：工具结果在"隐藏工具消息"时被隐藏，清单是条件句，可接受（U20） |

### 1.2 第三节施工清单 C3-0～C3-8

| 步 | 小步 | 结论 | 说明 |
|---|---|---|---|
| C3-0 | 1～8 金丝雀 | **记录里没写结果** | 记录只说"按 C3-0～C3-7"，第 8 条（Host 执行池身份字节测试是否读执行者引用、要不要重生成基线）没有结论；diff 里该测试没改，推定原样通过。建议补记一句（U21） |
| C3-1 | 1 世界改通用 | 做了 | |
| | 2 `root_binding` | 做了 | |
| | 3 改要求改用它、删 `forget_planning_world` | 做了 | |
| | 4 目标一致四样、根覆盖分支 | 做了 | |
| | 5 叶子取原话、子步骤标签取参数 | 做了 | |
| | 6 `catalog_digest` | 做了 | |
| | 7 单跑阶段 E 相关旧用例 | 自报做了 | 记录称约 700 条旧用例全过；我没复跑 |
| C3-2 | 1 按表删、`publish_methods` 改 | 做了（灌回范围略宽，U2） | |
| | 2 `DDL_V23` 字面量、`DDL_V40` | 做了 | 守护测试钉住迁移 23 校验和 |
| | 3 `method_library_store.py`（插入、列出、退役、归因、清空） | 做了 | "按条目数不同任务"放在秩序层 `record_attribution` 里数，不在存取层，可接受 |
| | 4 重放清单、部署清单重生成 | 部分 | 表已改、部署清单已重生成；事件没登记、残留一行旧 gap（U13） |
| C3-3 | 1 `ReviewPackage` 两节 | 做了 | |
| | 2 根终审切包、步骤切包填两节 | 做了 | 根终审切包改为"同编号已存在就用已存的那份"（U11） |
| | 3 解码两字段、范围错误、v4 | 做了 | `summary` 容忍漏了（必须改 2） |
| | 4 导入写进正式记录 | 做了 | |
| | 5 `REVIEW_INSTRUCTIONS` | 做了 | |
| C3-4 | 1 `library_owner` / `mission_untrusted_input` / `promote_methods` | 做了 | `promote_methods` 没有 `digest` 参数，改读提案事件里的哈希（U1） |
| | 2 完成写方同事务调用、出错不让完成失败 | **部分** | 只兜住四类异常（必须改 1） |
| | 3 两种事件登记进重放清单 | 没做 | U13 |
| C3-5 | 1 `listed_entries` 三处共用 | 做了 | 见第四节 |
| | 2 `views.method_library` / `library_reads` | 做了 | `library_reads` 加进了"体积超限可缩减"名单（U19） |
| | 3 `READ_METHOD_LIBRARY` 全链 | 做了 | |
| | 4 `based_on` | 做了 | |
| | 5 `method_at_fault` 与归因 | 做了 | 无用例（已登记偏差 3） |
| | 6 闸门 | 做了 | |
| | 7 规划器 v22 / 包 13、钉哈希模板测试重生成 | 做了 | |
| C3-6 | 1 `record_attribution` 两处调用 | 做了 | |
| | 2 门面两方法 | 做了 | |
| | 3 Host 工具、服务、注册、两张工具表、确认文案 | 做了 | |
| | 4 命令行 `method-library list / clear --yes` | 做了 | 参数用的是现有命令共用的 `--evidence-dir`，不是清单写的 `--db`（U8） |
| C3-7 | 1 `step_summaries`、第四层、`sum:`、`notice` | 做了 | `knowledge_read` 的 `notice` 没区分摘要层（U14） |
| | 2 推送槽、v6 | 做了 | |
| | 3 删规则摘要、改旧测试 | 做了 | |
| | 4 执行者 v7 三份 | 做了 | |
| C3-8 | 1 实施记录、`ARCHITECTURE/`、台账 | 部分 | 记录与仓库 `ARCHITECTURE/` 两份已更新；SDK 自己的 `ARCHITECTURE/index.md` 没改（U12）；台账按计划由本评估给建议（第五节） |
| | 2 发版、Host 钉版 | 未做（按流程在评估、核验之后） | 记录写明"待评估、核验、合并、发版" |

### 1.3 第四节 12 条用例

| # | 用例 | 结论 | 说明 |
|---|---|---|---|
| 1 | `test_types_do_not_depend_on_the_mission` | 做了 | 我跑过，通过 |
| 2 | `test_promoted_after_delivery_and_final_review` | 部分（已登记偏差 2） | ①②③有，③用"声明不可信路径"触发；④（终审打回后失败）没写 |
| 3 | `test_directory_read_and_derived_proposal` | 基本做了 | 没断言"审阅通过前计划提交被闸门拒"（U6） |
| 4 | `test_directory_filters` | 做了 | ②只断言目录为空，读取与 `based_on` 没单独断言（三者同一函数，可接受） |
| 5 | `test_adopted_method_needs_review_here` | 做法不同（已登记偏差 4） | 对闸门函数的单元断言 |
| 6 | `test_retired_after_two_missions_blame_it` | 做法不同（已登记偏差 3） | 直接调计数函数；两处真实接线无正式用例 |
| 7 | `test_manual_retire_and_clear` | 部分（已登记偏差 5） | 命令行只测了"库不存在" |
| 8 | `test_library_writes_do_not_touch_the_barrier` | 做法不同（**未登记**，U4） | 读的是作用域纪元表 `validity_epochs`，不是清单说的保证通道全局纪元（`assurance_environment_state.epoch`）；快照取在晋级之后，晋级那一次写不在被测窗口内 |
| 9 | `test_checked_summary_layer` | 部分（**未登记**，U5） | ③用打补丁让"验收仍站着"恒假，没有真的修复第 1 步出第二次验收，"新的摘要出现"没断言 |
| 10 | `test_old_paths_are_gone` | 做了 | 我跑过，通过 |
| 11 | `test_desktop_world_is_mission_independent` | 做了（位置不同，U7） | 追加在 `test_hierarchical_host_integration.py`，不是新建 `test_hierarchical_world.py`；我跑过，通过 |
| 12 | `test_method_library_tool` | 做了（名为 `test_chat_method_library.py` 三条） | 用假服务；真实门面的重放在用例 7 覆盖；我跑过，通过 |
| 附 | 回复解码两条范围错误断言 | 做了 | `test_review_reply_format.py::test_a_section_the_package_does_not_have_is_a_scope_error` |

### 1.4 改坏检验表

记录自报 12 条全部抓到（清单 11 条，"归属/哈希"拆成两条）。我核对了每条对应用例的断言确实能抓到所述改坏：

| 功能 | 清单要求的改坏 | 记录实际做的 | 判断 |
|---|---|---|---|
| 类型与任务无关 | 步骤说明改回原话 | 同 | 用例 1、11 都会因哈希不等失败，有效 |
| 根覆盖在提案时查 | 删根分支 | 同 | 有效 |
| 晋级只认可复用 | 不看 `reusable` | 同 | 2② 会出现条目，有效 |
| 不可信标记 | 去掉判断 | 同 | 2③ 有效（只覆盖"声明路径"那一支） |
| 归属/哈希过滤 | 去掉条件 | 同 | 4①/4② 有效 |
| 按需读取 | 读取不写 `library_reads` | "读取决定不留痕" | 有效 |
| 采用闸门 | 恢复"没审过就放行" | 同 | 单元断言有效 |
| 退役按任务计 | 改成数行数 | 同 | 有效 |
| 不触发屏障 | **晋级**改为改做法定义表状态 | **退役**时改做法定义表状态 | 做法不同、未登记（U4）：用例 8 的快照在晋级之后，抓不到晋级一侧的改坏 |
| 摘要必须核对过 | 不看 `faithful` | 同 | 有效 |
| 摘要随验收失效 | 不看验收是否仍站着 | 同 | 有效（但用例本身是打补丁版，见 U5） |

---

## 二、记录没有登记的偏差

| # | 偏差（代码与清单不一致之处） | 位置 | 判断 |
|---|---|---|---|
| U1 | 晋级写入的类型目录哈希取自"提出这个做法时"的事件，不是完成写方经编排器取规划世界现算；`promote_methods` 没有 `digest` 参数；没有哈希的做法跳过、不记事件 | `method_library.py:152-160` | 可接受（同一部署同一任务内两者相等，且更贴近"做法是对着哪份目录写的"），建议补登记 |
| U2 | 灌回注册表的是"试用范围是本任务"的全部登记状态，不只 `TRIAL_ADMITTED` | `world.py:529` | 可接受（本任务自己暂停的做法重启后仍应是暂停；比原来"所有任务的非试用行都灌"窄得多），建议补登记 |
| U3 | 晋级异常只兜四类 | `assurance_final_writer.py:239` | **合并前必须改**（结论第 1 项） |
| U4 | 用例 8 读作用域纪元而非保证通道全局纪元；晋级不在被测窗口；改坏检验改在退役一侧 | `T/product_world/test_method_library.py:334` | 建议改：在用例里读 `assurance_environment_state.epoch`，并在快照之后再调一次 `promote_methods`（或把快照提前到第二个任务完成前）；改坏按清单改在晋级一侧再做一次 |
| U5 | 用例 9③ 用打补丁代替"修复后第二次验收"，"新摘要出现"没断言 | `T/product_world/test_blackboard_tools.py` `test_checked_summary_layer` | 可接受到阶段 F，建议补登记；真实"修复后旧摘要消失、新摘要出现"留给 F 的修复场景 |
| U6 | 用例 3 没断言"做法审阅通过前，计划提交被闸门拒" | `test_method_library.py:181` | 可接受，建议补一行断言（读 `PlanningRejected` 里的 `METHOD_NOT_AUTHORIZED`，若剧本里没出现就如实记"脚本化规划器等审阅通过才采用，闸门未被触发"） |
| U7 | 用例 11 追加在 `test_hierarchical_host_integration.py` | — | 可接受，补登记一句 |
| U8 | 命令行用 `--evidence-dir`（与现有命令一致），不是 `--db` | `SDK/__main__.py:244` | 可接受，补登记 |
| U9 | "链接编号指原任务"的说明写在规划器提示词里，没附在 `views.library_reads` 每行 | `role_templates.py` 规划器 v22 | 可接受（一份说明，不重复），补登记 |
| U10 | 读取决定核对的是"本任务可见的任一在列条目"（含目录里被省略、没显示的），不是字面上"本次请求目录里显示的那几条" | `planning_method_proposal.py:165` → `visible_entry` | 可接受（过滤规则同一个；省略的条目只是没显示），补登记 |
| U11 | 根终审切包改为"同编号的包已存在就用已存的那份"（原来用新算的那份） | `root_review.py` 约 :910 | 可接受（包编号不含做法一节，重切时用首次冻结的内容才一致），补登记 |
| U12 | SDK `ARCHITECTURE/index.md:431` 仍介绍 `method_lifecycle.py` | 同 | 建议合并前顺手改一句 |
| U13 | 重放清单：四种新事件没登记；`commit_receipts.events` 缺 `MethodLibraryEntryRetired`；残留一行指向已删 `MethodEvaluationStore.record_oracle` 的 gap | `business_replay_inventory.json:760` 等 | 建议合并前改（清单 1.9、C3-4 第 3 条明写"当批登记"；阶段 G 要靠这份清单） |
| U14 | `knowledge_read` 读 `sum:` 行时 `notice` 仍是"This claim is not verified; it is a lead, not a fact." | `knowledge_tools.py:218` | 建议合并前改：摘要层给自己的一句（与目录 `notice` 同义），这是运行时数据，不碰工具说明 |
| U15 | 回复 `summary` 对象不容忍空多余字段 | `checks.py` 约 :410 | **合并前必须改**（结论第 2 项） |
| U16 | `_check_method_reviews` 与 `unreviewed_adopted_methods` 的文档串仍说"只在保证通道上生效"，现在闸门无条件生效 | `plan_commits.py` `_check_method_reviews` | 建议合并前顺手改文字 |
| U17 | 阶段 A 记录里"`note_trial_use` 唯一调用方已删、试用次数恒为 0，留到阶段 C3 处理"——清单和本阶段记录都没接这件事；`registry.py:1254` `note_trial_use` 仍无调用方，`trial_uses` 仍作为恒 0 的字段出现在做法行与执行图来源（`taskgraph_plan_sources.py:163`） | — | 建议补登记并定去向：要么本阶段删掉（旧路径直接删），要么写明移交 TaskGraph 补全 |
| U18 | 计划提交命令取种子用 `getattr(world, "seed_methods", ())` | `hierarchical_dispatch.py` 约 :3553 | 可接受（缺省是更严，不是放行），补登记 |
| U19 | `library_reads` 加进规划包"体积超限时可缩减"的视图名单 | `planner_package.py` `_SHRINKABLE` | 可接受，补登记 |
| U20 | 前端没给 `method_library` 加"隐藏工具消息时仍显示"的例外 | `tauri-app/src/chat/messageVisibility.ts:34` | 可接受（它没有卡片，主 Agent 会转述；清单本是条件句），补登记 |
| U21 | C3-0 金丝雀八条没有记录结论（尤其第 8 条执行池身份字节测试） | 记录 | 建议补记一句 |

（共 21 条：U3、U15 是两条必须改；U21 是记录缺口；其余 18 条可接受或建议改，均建议补登记进记录。）

**建议合并前顺手改（6 项，均为几行）**：U4（用例 8 读全局纪元并覆盖晋级）、U12、U13、U14、U16，以及把我的临时探针改成正式用例（见第三节偏差 3）。
**之后再补（5 项）**：U5、U6、U17 定去向、偏差 2 里"带资料登记"那一支、偏差 5 命令行清空的正常路径。

---

## 三、已登记 9 条偏差的意见

| # | 记录里的偏差 | 意见 | 理由 |
|---|---|---|---|
| 1 | 不可信按"用户登记资料事件 / 声明不可信路径"判，不按资料表有没有行 | **接受** | 核实 `SourceRegistered` 只由资料命令发（`SDK/orchestrator/source_commits.py` 约 :406）；声明路径读的是 `mission.final_report.untrusted_sources`，与执行者上下文同一处（`event_handler.py` 约 :9828）。仍是任务级可判定标记，没有关键词判断 |
| 2 | 用例 2 不可信一支用声明路径；"终审打回后失败不晋级"没写 | **接受** | ④由结构保证：`promote_methods` 全仓唯一调用方是完成写方，失败/取消不经过它（已核对）。建议阶段 F 前补"登记资料"那一支（`SourceRegistered` 分支现在无用例） |
| 3 | 用例 6 直接调计数函数；两处真实接线只靠读代码 | **有条件接受** | 两处是归因的唯一生产写方，没有正式用例等于"退役"这个功能没被端到端证明。我用临时探针跑通了审阅员一侧（根终审打回写 `at_fault` → 导入 → 归因表一行 `ROOT_REVIEW`）；规划器一侧读代码核对了实例编号的对应关系（`decision_admission.py:214` `ref_key` 用的就是 `instance_id`），未见问题。条件：把审阅员一侧的探针收为正式用例（约 40 行，可直接复用 `test_method_library.py` 的 `_reader_planner` 与 `_deliver`），规划器一侧在阶段 F 前补 |
| 4 | 用例 5 是闸门函数的单元断言 | **接受** | 闸门是纯秩序函数，单元断言足以钉住"只放种子"；产品路径上种子为空已核对（桌面与同形世界都是 `domains=()`）。建议同时做 U6 |
| 5 | 命令行清空只测了用法错误 | **接受** | 命令行是存取层 `clear()` 的薄壳，`clear()` 有断言。建议阶段 F 前补一条正常路径（同形世界的数据目录就在 `tmp_path` 下，一行即可） |
| 6 | 两个范围错误码放在"导入时拒"的提示表 | **接受** | 与阶段 C 的 `CLAIM_SCOPE` 同一处、同一规矩（同一次尝试里可修重问） |
| 7 | 推给执行者的摘要不含本步自己以前的摘要 | **接受** | 合理，且与"别的步骤做了什么"的用途一致；读工具仍能读到，不丢事实 |
| 8 | 规划决定 JSON schema 测试里 `required_for` 上限 8 → 9 | **接受** | 机械随决定种类数变化 |
| 9 | 审阅员提示词钉文字测试早已过时，随第 4 版更新 | **接受** | 钉哈希/钉文字随当前版本更新，符合"只保留一份当前版本" |

---

## 四、高风险点核对

| 点 | 结论 | 依据 |
|---|---|---|
| 晋级只在唯一完成写方的同一事务 | **是** | `finalize_assured_mission` 开头要求 `in_transaction`；`_promote_methods` 紧跟 `MissionCompleted` 之后（`assurance_final_writer.py:189`）；全仓唯一调用方 |
| 只认根终审正式记录里 `reusable=true` 的条目 | **是** | 读 `resolution.review_receipt_id` 指向的记录的输入清单 `methods`，只取 `reusable` 且 `purpose` 非空；再核"本任务提出 + 本任务审过 + 在 ADOPTED 实例里"（`method_library.py:81`、`:143-152`）。结论要求是 ACCEPT（完成写方已核）；人裁决通过时读同一份记录 |
| 晋级出错不让任务完成失败 | **部分**（必须改 1） | 保存点 + 回滚 + 记事件的结构对；只兜四类异常 |
| 两张表不在保证通道源表清单、写它们不碰纪元 | **是** | `storage/assurance_source_inventory.py` 未改，`GLOBAL_TABLES` 只有 `method_contracts / policy_versions / policy_proposals / policy_activations`；屏障 SQL 未改；`events` 表的屏障触发器只认六种 `Assurance*` 事件，新事件不在内；退役回执写的 `commit_receipts` 不是源表。用例 8 的覆盖面偏窄（U4） |
| 目录 / 读原文 / `based_on` 共用同一过滤函数（归属、类型目录哈希、在列、目标类型） | **是** | 目录 `directory` → `listed_entries`；读取 `read_library` → `visible_entry` → `listed_entries`；`based_on` `prepare_method` → `proposal_origin` → `visible_entry` → `listed_entries`；`listed_entries` 只调存取层 `listed(owner, digest, goal_type)`，SQL 条件四样齐（`method_library_store.py:50`）。读过之后在后几轮展示原文的 `library_reads` 只复查"仍在列"（读时已过完整过滤，同一任务内归属与目录哈希不变），晋级时继承 `based_on` 只查"条目存在"（记血统，退役的也记）——两处都是有意的，不是旁路 |
| 采用闸门只放种子；桌面与同形世界没有种子 | **是** | `unreviewed_adopted_methods` 只对 `(id, 版本, 哈希)` 在种子集合里的放行，其余必须本任务审过（`method_plan_reviews.py:94`）；种子 = `install_library` 装入后 `admit_domain` 的那批（`world.py:494`）；Host `planning_world` 与同形世界都是 `build_planning_world(..., domains=())`，种子为空；计划提交命令全仓只有一处构造（`hierarchical_dispatch.py` 约 :3543），都带种子 |
| 摘要层只列"核对忠实 + 哈希一致 + 验收仍当前" | **是** | `step_summaries` 四个条件：记录 `summary.faithful`、`summary_sha256` 等于对现存结果摘要重算的哈希、`result_ref` 等于结果指纹、`acceptance_is_current`；另要求正式记录通过或人裁决通过（`knowledge_tools.py:64`） |
| 推给执行者的与工具读到的是同一函数 | **是** | 读工具 `_catalogue` 与执行者上下文 `event_handler.py:5402` 都调 `step_summaries`；推送只多两条秩序（去掉本步、最多 8 条） |
| `TOOL_SCHEMAS` 与编码清单五个文件未改 | **是** | `git diff 85c465d5..htn-c3` 对 `runtime/tool_gateway.py`、`contracts/htn.py`、`contracts/state_machines.py`、`contracts/models.py`、`contracts/evidence_state.py`、`graph/task_network.py` 均无改动 |
| 迁移 1～39 文字未改、迁移 23 原样挪位 | **是** | `schema.py` 只有新增；`DDL_V23` 字面量与原 `method_evaluation_schema.py` 的 `DDL` 逐字相同（已比对）；`test_c3_guards.py` 钉住迁移 23 校验和，我跑过通过 |
| 没有新增兼容 / legacy 分支 | **是** | 新增代码里检索 legacy / compat / 兼容 / 回落，只有一处 `getattr(..., "seed_methods", ())`，缺省是更严（U18） |
| 旧路径删干净 | **是（代码）**，文档与清单各留一处 | 源码与测试里没有 `method_evaluation`、`method_lifecycle`、`memory.summaries`、`context.compression`、`refresh_summaries`、`build_summaries`、`branch_summary`、`global_summary`、`SUMMARY_VERSION` 的残留（`simple_harness/execution` 里的同名 `upsert_summary` 是执行库自己的，无关）；残留在 SDK `ARCHITECTURE/index.md:431`（U12）与重放清单一行 gap（U13）。另有阶段 A 转来的 `note_trial_use` 死代码未处理（U17） |

---

## 五、`需求与场景状态.md` 更新建议（只给文字，不改文件）

注意：本分支里的台账停在阶段 D，`main` 上已有阶段 E 的更新（`40b494a4`）。下面的建议以 **`main` 上的版本**为底，合并时取 `main` 的台账再加这些改动。前提：结论里两项必须改已改完。

**1. 文件开头"更新记录"加一段：**

> 更新记录：2026-10-03 阶段 C3+摘要完成后更新（依据代码分支 `htn-c3` `bdbb0dc9`，合并与发版后把提交号改成 `main` 上的；只读代码、实施记录阶段 C3+摘要、阶段 C3+摘要开工裁决与施工清单、阶段 C3+摘要完成评估，跑了本阶段新增用例中的 20 条）。改动：需求 R07、R30、R33、R54 四行；改了状态的 3 行：**R07、R30、R33 计划中 → 已接入**（全库做法晋级、目录与读原文、归因退役、核对过的摘要层都在产品默认路径上）。场景 T007、T030、T033、T061、T088 五行补阶段 C3 的功能性用例，状态仍是"未运行"。待核 4 条（R23、R26、R29、R48）复核主计划第 3.16 版仍没有归属。

**2. "整个目标还没满足的编号"一节：**
- 第 1 组改为"还有代码没写（6 条，全是'计划中'）：R11、R23、R26、R29、R37、R48"；
- 第 2 组改为 38 条，加入 R07、R30、R33；
- "状态条数"改为"已接入 38，已实现 0，计划中 6，不做 9，删除 7，共 60"；场景一句末尾加"5 个（T007、T030、T033、T061、T088）有阶段 C3 新增的功能性用例已通过"。

**3. 需求行：**

- **R07**（状态：计划中 → **已接入**；最近更新 C3）依据改为：
  > 提出与审阅：规划器提新做法、过本任务独立审阅（`SDK/orchestrator/plan_commits.py` `_check_method_reviews`）；采用闸门只放部署种子，桌面没有种子，任何被采用的做法都要本任务审过（`SDK/orchestrator/method_plan_reviews.py:94` `unreviewed_adopted_methods`）。跨任务：交付成功、根终审通过、审阅员在终审里判可复用并写一句用途，才在唯一完成写方同一事务进全库（`SDK/orchestrator/method_library.py:131` `promote_methods`，带归属、类型目录哈希、来源任务与根终审记录；另建两张表、不进保证通道，迁移 40）；别的任务规划包只列目录（每类型 5 条、按晋级时间），规划器用只读决定 `READ_METHOD_LIBRARY` 读原文后写本任务的新做法（`based_on`），照常过审阅——全库做法只当先例、不原样采用（计划第二节偏离）。淘汰：规划器换做法写 `method_at_fault`、根终审打回写 `at_fault` 才记归因，按不同任务数，2 个即退役；主 Agent 工具 `method_library` 可列出与手动退役，命令行 `method-library list|clear`。带资料的任务不晋级。评测晋级整套删。用例 `T/product_world/test_method_library.py` 10 条、`backend/tests/orchestration/test_chat_method_library.py`。**还差（不拖低状态，F 前补）**：两处归因接线没有正式用例（审阅员一侧评估时用临时探针跑通）；"带资料登记"不晋级那一支没有用例。
- **R30**（状态：计划中 → **已接入**；最近更新 C3）在现有依据末尾把"还差：摘要绑定原文哈希（阶段 C3+摘要）"换成：
  > 阶段 C3：摘要绑定结果指纹与摘要原文哈希，只在这一步的验收仍站着、审阅员核对忠实时列出，验收失效即不再列出（`SDK/context/knowledge_tools.py:64` `step_summaries`，与知识过时同一个 `acceptance_is_current` 判定）；做法一侧由类型目录哈希承担（类型、谓词或规划包版本一变，全库做法自动不再列出）与归因退役承担（计划表二"做法的失败与过期条件不另加字段"）。用例 `T/product_world/test_blackboard_tools.py::test_checked_summary_layer`（"修复后旧摘要消失、新摘要出现"用打补丁代替，完整修复场景归 F）。
- **R33**（状态：计划中 → **已接入**；最近更新 C3）"还差"一句换成：
  > 阶段 C3：黑板第四层"摘要"开放——执行者结果里必填的 `summary` 就是摘要（执行者 v7 写清用途，系统不截断），审阅员审该步时顺带核对忠实（审阅员回复 v4），核对过的以 `layer=summary` 经 `knowledge_list / knowledge_read` 读出，带结果指纹、哈希、产物与核对它的审阅记录；推给执行者的 `step_summaries` 读同一处（上下文 v6）；规则截断摘要整套删（迁移 40）。按字面混合检索不做（计划表二 12b）。
- **R54**（状态不变）依据末尾加："做法晋级见 R07（阶段 C3：交付成功且根终审判可复用才进全库，评测晋级已删）。"

**4. 场景行（状态都仍是"未运行"）：**

- **T007**：加"阶段 C3：`T/product_world/test_method_library.py::test_promoted_after_delivery_and_final_review`、`::test_directory_read_and_derived_proposal`、`::test_directory_filters`、`::test_retired_after_two_missions_blame_it`、`::test_manual_retire_and_clear`（部分：归因计数直接调函数，两处真实接线没有正式用例）"；"最近更新"改 C3。
- **T030**：加"阶段 C3：`T/product_world/test_blackboard_tools.py::test_checked_summary_layer`（部分：摘要随验收失效用打补丁模拟；修复后过时仍没覆盖）"。
- **T033**：加"阶段 C3：`::test_checked_summary_layer`（核对过的摘要作为第四层、推送与工具读同一处；没核对的不列）"。
- **T061**：加"阶段 C3：桌面没有种子做法，全库为空时规划器照常自己提做法（`test_method_library.py::test_types_do_not_depend_on_the_mission`）"。
- **T088**：加"阶段 C3：`test_method_library.py::test_adopted_method_needs_review_here`（部分：全库做法不原样采用，任何非种子做法都要本任务审过才能采用；执行者引用固定、工具仍由任务与部署的交集决定）"。

**5. 表二对应关系提示**：主计划表二 1b（做法库）对应台账 R07/T007，表二 12a（摘要）对应 R33/T033 与 R30/T030 的摘要部分；台账里不必另设 1b、12a 行。
