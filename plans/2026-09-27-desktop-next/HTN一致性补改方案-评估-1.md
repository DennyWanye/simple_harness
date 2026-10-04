# HTN 一致性补改方案 · 评估第 1 轮（2026-10-04）

- 评估对象：`HTN一致性补改方案-2026-10-04.md`（第 1 版）；代码基线：worktree `simple_harness-a4`，分支 `htn-h`（基 main `57560720`）。
- 方式：只读核对代码，没有跑测试，除本文件外没有改任何文件。只报会让实施走错或返工的问题。
- 路径缩写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`，`Host/` = `backend/deskpet/orchestration/`，`ST/` = `sdk/simple-harness-sdk/tests/orchestrator/`，`HT/` = `backend/tests/orchestration/`。

## 结论：改完下面几条就能照着写代码

H-4、H-8 只改文档，没有问题。H-6、H-7 小改就行。H-1、H-2、H-3、H-5、H-9 各有一处写错了现状，或者照写会改坏现有功能，必须先改方案。阻断级 4 条：H-1 第 1、2 条，H-3 第 1 条，H-5 第 1 条。

## 逐项问题

| # | 级别 | 问题 | 依据（文件:行） | 建议改法 |
|---|---|---|---|---|
| H-1-1 | 阻断 | 判据"最新许可是 BLOCKED"太宽。前提的真值还是"未知"时，许可同样是 BLOCKED：只有真值为 TRUE、并且通过授权闸才是 USABLE。没人看过的前提本来就走"取证"这条路，照方案写会在取证前就把规划器叫起来，而且每次都叫 | `SDK/orchestrator/hierarchical_dispatch.py:1453-1454,1483`；取证流程见 `ST/product_world/test_desktop_preconditions.py:82` | 只认 `truth` 为 FALSE 的许可（CONFLICT 要不要算，写明）。UNKNOWN 不发请求 |
| H-1-2 | 阻断 | 说 `phase_check_points` 没有调用方，与代码不符。派发闸 `start_preconditions` 正在用它，而且有专门的用例 | `SDK/graph/eligibility.py:64,859`；`ST/full_target/test_semantic_binding_codec.py:473-476` | 删它就得同时改派发闸，还要动编码清单（见 H-1-5）。建议走方案里的备选：**保留它**，计划里记偏离"验收时不复查前提，phase 恒为 SELECT"，不动 `evidence_state.py`，也不动 `network_codec_manifest_v5.json` |
| H-1-3 | 重要 | 读取方法写得不清楚 | 许可存在 `validity_witnesses` 表（`SDK/storage/htn_schema.py:419`，迁移 18 加了 `subject_digest`）。现成的读函数是 `HierarchicalDispatch.start_witness_index(mission_id)`（`hierarchical_dispatch.py:1543`），返回"步骤 → 条件摘要 → 最新那张"，"最新"按 (support_revision, as_of_ms) 取。方案里写的函数名 `issue_condition_witnesses` 不存在，实际是 `issue_start_witnesses`（`:1359`） | 直接用 `start_witness_index`，不要再写一套"取最新"。"未开工"定义为：该步骤在现行网络的原子步骤里，并且 `store.list_attempts(task_id)` 为空 |
| H-1-4 | 重要 | 去重键用"许可编号"会重复发请求。许可编号含 support_revision，记任何一条无关的新观察都会换一张新许可，前提一直是假的步骤就会反复给规划器发请求，把规划次数耗光 | `hierarchical_dispatch.py:1468-1478`；去重靠 `record_request` 的 source_key（`planning_repair_requests.py:377`） | 键改成 `"precondition:" + task_id + ":" + 条件集摘要 + ":" + scope_epoch`。纪元只在真值翻转时加一（`SDK/storage/htn_store.py:1328`），所以前提每翻一次只发一条 |
| H-1-5 | 说明 | 动 `evidence_state.py` 要付的代价 | `network_codec.py:136-182,237,422`：清单哈希写在每份网络文档里，改了以后所有已存的计划都解码失败（"codec manifest is not explicitly supported"）；回放范围摘要也跟着变（`SDK/storage/source_records.py:72-87`）。部署清单本来每次发版都要重新生成，不算额外代价；执行池身份不受影响 | 按 H-1-2 的建议，这次不动 |
| H-1-6 | 重要 | 复用 `EvidenceInvalidated` 可以，但规划器的提示词没有专门讲这类请求（`SDK/runtime/role_templates.py:190-219` 只讲了另外几类），请求包里也写着"真实发生的失败"。`detail` 写得含糊，规划器可能把它当成要重做 | 同上 | 不改提示词。`detail` 自己说清楚：`reason: "method_precondition_false"`，加一句中文说明"这一步所在做法的前提现在不成立，这一步不会开工"，再写上步骤、做法、前提原文和观察。`trigger_refs` 写步骤的 task_id，这样上级目标会进入作用范围，规划器能换做法（`trigger_scope`，`:397`） |
| H-1-7 | 重要 | 删 `recheck_method_instance` 会牵连别处，方案没列 | `ST/full_target/test_predicate_truth_table.py:64,72,515-536`；`SDK/planning/htn/__init__.py:33,183`；`compiler.py:1386`、`grounding.py:594` 的注释 | 同一提交里删掉这两条用例和导出，改掉注释 |
| H-1-8 | 重要 | 用例怎么造没写清。观察只在"世界变了"（有新验收或资料变动）时才重读，计划提交以后直接改文件不会被看到 | `ST/product_world/test_desktop_preconditions.py:1-11,204` | 建议用两步做法：第一步验收（触发重读）时把前提对应的文件改掉，第二步前提变假。断言在任何 `NoDispatchableWork` 请求之前就出现了带这条前提的 `EvidenceInvalidated` 请求；改坏后只剩停滞请求，用例变红 |
| H-2-1 | 重要 | 通知只带编号。传给 Host 的内容只有 `mission_id / event_id / state_version`，Host 的通知记录也只存编号，界面卡片显示的是任务"现在的状态"。照方案写，核对结果的那条通知会再显示一遍"已取消" | `SDK/orchestrator/assurance_consumers.py:787-791`；`Host/notices.py:57-69`；`Host/service.py:1224-1243` | SDK 用 `request_assured_notification(commit, mission_id, 核对事件, state_version)` 发通知（这条路对已结束的任务照常工作，见 `assurance_tick.py:113`）。Host 在 `pending_notices` 里按 notice_id 读出那条事件，判断是核对事件后再拼文字。要改的地方写全：卡片 `tauri-app/src/views/ChatMissionNotices.tsx`、主 Agent 上下文 `notice_context_text` |
| H-2-2 | 重要 | "定出结果"不止一个地方。对外操作的结果可能来自四处：①交出时接口晚回的结果 `record_outcome`（取消那一刻租约还没到期）；②核对确认已发生 `record_reconciliation`；③发布类动作的"确认没生效"，走的是记了 `NOT_APPLIED_FINAL` 回执的另一条路；④人工裁决。"没生效"在状态上仍是 UNKNOWN，只是 `reconcile` 字段变了 | `SDK/orchestrator/action_commits.py:1236-1311`；`SDK/runtime/actions.py:255-260`；`SDK/runtime/operation_reconciliation.py:160-176` | 写明"已生效"和"没生效"各对应哪个状态、哪个字段，只在一处判断"任务已结束且这个操作的结果有变化"后发通知。建议放在 `event_handler` 的核对循环里 `reconcile_one` 返回之后，同时覆盖 ①（`_resolve_action`）。另外核对过：任务结束后不会再次交出（`action_commits.py:1068`） |
| H-2-3 | 重要 | 任务失败也会留下结果不明的操作，而且和取消走的是同一个 `_cascade_stop` | `commit_service.py:1483-1515,1570-1571` | 在 `_cascade_stop` 的调用方统一写 `unresolved_actions`（包括取消和失败），不要只写在取消里 |
| H-2-4 | 重要 | 方案说"沿用 test_operation.py 的局面"，但那里没有"发布进行中取消"的用例，得新写 | `ST/product_world/test_operation.py`（6 条用例都不是这个局面） | SDK 新写一条：最终报告有未决操作；放开核对后，传输层收到那条核对事件的通知。Host 另写一条：卡片和上下文里有那句话。改坏要分别对应 |
| H-3-1 | 阻断 | 把分类删到"只留 REQUIRED_OUTCOME / SEMANTIC"会改坏产品路径。叶子步骤的验收准则用的是 `DETERMINISTIC`，对外操作提案的审阅用的是 `HARD_CONSTRAINT`，必过准则编号也是由硬约束算出来的。"审阅侧的硬约束分支走不到"这个前提不对 | `SDK/orchestrator/leaf_acceptance.py:195-196,248-249`；`operation_proposal_review.py:188-189`；`assurance_purpose_reviews.py:310`；`assurance_content_review.py:223`；`assurance_review_transport.py:72`；`assurance_check_policy.py:247,421`；`verification/acceptance_rules.py:255,278-283` | 第 2 条缩小为：只删没人生产的 `RequirementClass.PREFERENCE` 和 `Criterion.phase`，其余枚举都保留。查过：这几个类型不在网络编码清单范围里，htn.py 没有引用它们。注意 `Criterion.to_json` 一变，旧的要求记录就解码不了，开发期可以接受，写明即可 |
| H-3-2 | 重要 | 审阅员拿到的材料和新判据互相矛盾。根审查包里每条准则都带 `requirement_class: REQUIRED_OUTCOME`，请求里还有 `formula`（"全部都要"）；提示词又写着"ACCEPT＝全部准则成立"。只加一句"按原话判"，审阅员很可能还是按"必须"判 | `SDK/orchestrator/root_review.py:1215`；`SDK/assurance/review_input.py:24-25,117`（document 的 formula） | 从根审查包里删掉 `requirement_class`（系统不做分类），并在提示词里写清判法：原话里是偏好或可选、没做到的那条准则判 PASS，在 limitations 里写明没做到什么；"A 或 B"做到一项即判 PASS |
| H-3-3 | 重要 | "提示词版本号加一、重生成钉哈希基线"在代码里对不上。审阅员没有单独的提示词版本号：`REVIEW_CODEC_VERSION` 同时是回复格式版本，导入时也要对它（`assurance_review_import.py:886,902`）。也没有钉审阅员提示词哈希的测试 | `SDK/assurance/reviews.py:38`；先例 opt.125（`29a24bcc`）改了提示词，没有动版本号 | 照 opt.125 的做法：只改 `REVIEW_INSTRUCTIONS`，不升版本号。指纹（`{"instructions","codec"}`）会自动跟着变；改完后，还在飞的旧审阅意图会被拒（`assurance_review_handoff.py:71`），开发期可以接受。审阅员提示词不在执行池身份里（见 `native_pools.py` 顶部的说明） |
| H-3-4 | 重要 | 用例是自己证明自己。脚本审阅员不读提示词，"交付被接受"无论改不改都成立 | 脚本审阅见 `SDK/testing/scripted_replies.py` | 用例改成两条事实：①`REVIEW_INSTRUCTIONS` 里有这条判据（仿照 `ST/full_target/assurance_exec/test_review_reply_instructions.py`）；②发给审阅员的请求里，那条准则的原话"最好带图表"一字不差，并且不带 `requirement_class`。能不能真的"按原话判"，放到真实模型联测里看 |
| H-5-1 | 阻断 | `budget_lineage_ref` 并不是"没人读写"。它是义务表的一列，有索引，三处保证通道触发器、回放清单 JSON、保证来源清单里都有它，规划包也在读（`budget.lineage_ref`） | `SDK/storage/htn_schema.py:242,264`；`storage/schema.py:731,791`；`storage/assurance_barrier_v26.sql:1416`；`observability/business_replay_inventory.json:753`；`storage/assurance_source_inventory.py:299`；`storage/obligation_store.py:85-101,292-314`；`orchestrator/planner_views.py:126` | 这次只删 `authority_ref`、`satisfaction_policy`、`SatisfactionPolicy`（只存在 JSON 里）。`budget_lineage_ref` 记偏离先保留，要删就单开一刀，带迁移 |
| H-5-2 | 重要 | 方案说"现有用例照跑"，实际上现有用例直接构造了这几个字段 | `ST/full_target/test_obligation_store.py:27,60-71`；`test_obligation_conservation.py:28,52-53,129-131` | 同一提交里把这两个文件改掉 |
| H-6-1 | 重要 | 诊断导出明确不带目标原文，有金丝雀用例守着。`obligation_rows` 里的 `label` 取自步骤的目标原文 | `SDK/orchestrator/obligation_accounts.py:64-75`；`HT/test_mission_diagnostics.py:126,248`；`Host/diagnostics.py:371-377` | 直接读快照里现成的 `snapshot["budget_by_duty"]`（`SDK/api/facade.py:786-788`，不新增 SDK 调用），只输出 obligation_id、父级、层级和数字，**不带 label** |
| H-6-2 | 重要 | "总数等于任务已结算"不一定成立：义务账只统计各步骤的尝试，规划器和审阅员服务花的 token 不挂在义务上 | `obligation_accounts.py:20-57`；`Host/diagnostics.py:58-80` | 断言改成：这一节根那一行等于同一份快照里 `budget_by_duty` 的根行（或 `obligation_accounts` 的根），不和账本总数比 |
| H-7-1 | 重要 | 方案写"改坏：快照不带 → 前端用例变红"，但前端 vitest 用的是假数据，快照怎么样它都不会变红 | `tauri-app/src/views/MissionsView.test.tsx` | 改坏要写在 Host 快照用例上。另外别抄两份：把 `planner_views.py:139-166` 里"还算不算数"的计算抽成一个函数，规划包和 `facade.snapshot` 共用；`Host/projection.py:453` 一带照 `budget_by_duty` 的写法原样透传 |
| H-9-1 | 重要 | 只改 `settings.py:56` 不会生效。真正的配置都经过 `load_settings`，那里写死了"不是 524288 就用 262144" | `Host/settings.py:129-130` | 改成"不是 262144 就用 524288"。方案里"现有解析不变"这句要删掉 |
| H-9-2 | 重要 | 现有用例会变红：它用默认设置，又只提供了 256K 的池 | `HT/test_thinking_pools.py:61-72` | 一起改期望值。执行池身份不受影响：池 ID 只看尺寸和思考模式（`native_pools.py:47-52`），两档都会注册（`Host/service.py:835-846`），`pool_identity_baseline.json` 不读默认值 |

## 第 6 问：照方案写的改坏能不能让用例变红

- **H-01**：按 H-1-8 写用例就会红。
- **H-02**：最终报告那一半会红；通知那一半要按 H-2-4 补一条单独的改坏。
- **H-03**：只有提示词文字的断言会红，"交付被接受"那部分无论如何都过。按 H-3-4 改。
- **H-06**：会红。
- **H-07**：照方案写不会红，按 H-7-1 改。
- **H-09**：按 H-9-1 一起改了解析才会红；只改 dataclass 的默认值，用例测的东西和产品实际行为对不上。
