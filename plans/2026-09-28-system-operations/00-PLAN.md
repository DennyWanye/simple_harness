# 发布交给系统 + 按"谁的错"扣次数（2026-09-28 方案）

> 背景：复杂真机题（wordfreq 三份文件 + 发布两份）五局暴露 16 个问题，第四、五局都因为模型写"发布申请单"反复出错，把整个任务 12 次尝试耗光。用户 2026-09-28 同意两个方向：①发布完全由系统做；②只有模型自己做错才扣次数，非模型原因按每一步设上限。
>
> 本文第一部分给用户看（大白话），第二部分是给实现和审阅用的技术附录。

---

## 第一部分：一页方案

### 改完后，一个带发布的任务怎么走

| 阶段 | 你会看到什么 | 和现在的区别 |
|---|---|---|
| 1. 新建任务、确认页 | 照旧填目标和成功条件，在确认页选"必须完成的效果"（发布哪个文件、怎么算完成） | 不变 |
| 2. 拆步骤 | 执行图里只有"写内容"的步骤，不再出现"发布"步骤 | 规划器不再把发布分给任何步骤 |
| 3. 执行内容步骤 | 模型只写文件。如果它顺手在申请单目录里写了东西，系统直接无视 | 不再因为"写了不该写的申请单"整份退回 |
| 4. 准备发布 | 内容步骤全部通过后，系统用你在确认页选的目标 + 通过检查的那份文件，自动生成申请单，审阅员过一遍，操作区出现"待你批准" | 以前要你在操作区挑候选、点"提交"，再点"批准"；以后只点"批准" |
| 5. 批准和核对 | 你点批准 → 系统发布 → 读回来核对内容 → 任务完成 | 不变 |

如果内容都做完了，却找不到要发布的那个文件（比如要发布 README.md，但没有任何步骤写出它），系统会明确告诉规划器"缺一个写 README.md 的步骤"，让它补上，而不是卡在那里。

### 哪些情况扣次数

| 情况 | 扣任务次数吗 | 系统怎么处理 |
|---|---|---|
| 模型做错：检查没通过、审阅员不认可、没写出要求的文件 | **扣** | 照旧交给规划器决定怎么修 |
| 模型原地打转：用完了单次允许的回合数或工具次数 | **扣** | 照旧交给规划器 |
| 格式没写对：结果块缺失、结果里认领的文件对不上 | 不扣 | 直接让它重做，不去问规划器 |
| 服务出错：DeepSeek 返回的内容坏了、服务暂时不可用 | 不扣 | 直接重做 |
| 执行卡住、重启打断、调用结果丢失 | 不扣 | 直接重做 |

不扣次数的这三类，**同一步合计最多 6 次**。超过就停下这一步，并在任务里写明"服务或格式可能有问题"，避免无限重试。

说明：原来设想"格式错就在同一次尝试里当场改"。查代码发现，每次尝试都是一次独立的模型会话，真要当场改，得动底层执行层，风险大。所以这次改成"马上重做、不扣次数、不问规划器"，效果接近。真正的当场改放到以后再做。

### 分三批做

| 批次 | 内容 | 做完怎么验证 |
|---|---|---|
| 第 1 批 | 次数按"谁的错"来扣 | 先写测试再写代码；跑回归 |
| 第 2 批 | 发布交给系统：步骤不再分到发布要求；内容步骤里不再有申请单；系统自动准备申请单；操作区去掉"挑候选/提交"，只留批准 | 同上；**在应用里演示一局给你看** |
| 第 3 批 | 找不到要发布的文件时，让规划器补步骤 | 同上 |
| 验收 | 同一道题跑两局，一局中途重启，都走完"你点批准 → 发布 → 核对一字不差" | 四张表报告，问你是否推送 |

### 风险和应对

- **旧任务可能显示异常**：开发期不兼容旧数据，这是之前已经定下的；只保证新建的任务。
- **审阅员还在**：准备申请单时和发布之后各有一个审阅员把关。它们不扣任务次数，这次先保留，以后可以考虑对系统生成的申请单省掉第一道审阅。
- **性能**：不在这次范围内，已记入待办。

---

## 第二部分：技术附录（实现与审阅用）

路径均相对 `sdk/simple-harness-sdk/src/agent_orchestrator/`，Host 路径以 `backend/` 开头。

### A. 失败分类与次数（第 1 批）

1. **一张分类表**：新模块 `orchestrator/failure_classes.py`，函数 `classify_attempt_failure(status, failure) -> MODEL | FORMAT | INFRA | INTERRUPTED`。键：`failure.reason`、`error_kind`、`error.error_code`、`error.source_kind`、`failures[].layer`，以及尝试状态（RETRY_WAIT / TIMED_OUT / LOST）。
   - MODEL：`verification_failed`（规则检查、断言、审阅）、`inconclusive`、`outcome_*`、`protected_path_rewritten`、`result_evidence_kind_not_allowed`，以及 `turn_failed` 中的 `react_max_turns_exceeded` / `react_max_tool_calls_exceeded`。
   - FORMAT：`envelope_invalid`（缺块、端口认领错）。
   - INFRA：`turn_failed`，且 `error_kind ∈ {provider_unavailable, provider_error}` 或 `source_kind == tool_parse`；`runtime_unavailable`（`planning_retry.py:51` 已在特殊处理）。
   - INTERRUPTED：`executor_stalled`（TIMED_OUT）、`executor_turn_missing`、`executor_agent_missing`、`provider_outcome_unknown`（LOST）；审阅被打断（原因字段是 `verification_failed`，所以**先看失败明细里的审阅层是否"等待原调用对账"，再看原因字段**，否则会误判成模型错）。
   - 不在表内：`model_echo_mismatch` 是部署或模型不对，任务当场停（`event_handler.py:9166-9180`），维持现状。
   - 表里没有的原因一律按 MODEL 处理：宁可多扣，不能漏扣。
   - 现有散落的集合（`model_router` 的 UNAVAILABLE/NEUTRAL、`planning_selection._lost_execution/_interrupted_review`、`allocator.FAILED_REASONS`）本批只让重试路由改用新表，其余不动。
2. **次数退还**：`create_attempt` 仍在创建时预扣（`commit_service.py:3409`，保持"先扣后做、不会超额"）。尝试以非 MODEL 类别终结时，在同一事务里做两件事，缺一不可：
   - 执行 `BudgetLedger.release_attempt(subject)`，沿任务→任务总账→全局账各减 1；
   - 同时把 `task.attempt_count` 减 1。能否再试（`event_handler.py:10099`）、管理者看到的剩余次数（`:10760`）、人工重试上限（`human_commits.py:803`）、模型上下文（`context_builder.py:635`）读的都是这个字段，只退账本会两边对不上。

   只退普通预留路径（`commit_service.py:3406`）扣的次数；系统预留池和尾部额度路径（`:3380-3402`）扣的不退，否则等于凭空多给次数。发一个 `AttemptChargeReleased` 事件，按尝试 id 保证幂等。只退"次数"，token 和费用照常按实际或上限结清（多算不少算的硬规则不变）。
3. **重试路由**：非 MODEL 类别直接由系统发出"同一方法重试"，复用并推广 `planning_selection._runtime_lost_retry`，不经规划器。删掉 `NATIVE_RUNTIME_RETRY_MAX_ORDINAL`，改用第 4 条的每步上限。
   - 原实现要求待处理修复请求**恰好 1 条**（`planning_selection.py:63`），但规划包里是整个任务的全部请求（`planner_views.py:171`），两步同时失败就不成立。改为：从多条请求里挑出第一条非 MODEL 类别的原地处理，其余留给后续几轮。
   - MODEL 类别维持现状，走规划器修复。
4. **每步上限**：从该步已有尝试按类别现算（不加新列）。非 MODEL 累计 ≥ 6 时，停掉这一步，原因记为 `non_model_failures_exhausted`；任务按现有"步骤停止"路径收尾，停止原因写清楚。
5. **测试**：分类表逐项测试（含未知原因按 MODEL）；退还的幂等与账链；第 6 次停止；非 MODEL 失败不产生规划器请求；把退还或分类改掉的变异必须变红。

### B. 发布交给系统（第 2 批）

1. **根任务只要求内容**：Host 根类型 `desktop.user-goal`（`hierarchical.py:28`）的覆盖要求改为只含内容要求（和步骤类型的 `content_criteria` 一致）。这一处改动同时让 registry、compiler、validation、`projection_validation._check_root_coverage`（`:773-800`）四处覆盖检查不再要求给操作要求安排步骤，**不改任何模板文字**（已发布模板按字节冻结，规划提示版本钉在每个任务的绑定里）。
   - 效果仍由已批准规格和根的完成条件把关（`completion_status` 的效果就绪），根审阅和收尾不受影响。实现时核对 `initialize_root` 的 `requirement_refs` 与效果 `obligation_id` 的来源，确认改覆盖要求不会让效果找不到所属义务。
2. **步骤不再承担操作要求**：`occurrence_tasks.occurrence_criteria`（约 L289–305）里过滤掉操作要求 id（已批准效果覆盖的，或原文以 `action:` 开头的）。**不在准入时剔除链接**：剔掉后"只链接了操作要求"的步骤会变成"未链接"，从而回退到全部内容要求，被要求写出所有文件（`completion_scopes.py:316-322`、`occurrence_tasks.py:296`），又回到 09-26 的老问题。
   - 有了第 1 条，合成器不再需要为操作安排步骤。万一方法里仍出现"只链接操作要求"的步骤，就在方法准入时用 `schema_feedback` 退回让合成器改（代价是一次合成重问，不是步骤尝试）。
3. **Host 不再声明申请单端口**：`backend/deskpet/orchestration/hierarchical.py` L38–40 去掉 `action_candidate` 端口。任务类型身份随任务变化，只影响新任务。
4. **SDK 去掉模型侧申请单机制**：`declared_action_outputs` 的端口分支、`with_system_candidate`、`_claim_system_candidate`、`worker_action_contract` 的使用、`planner_action_contract` 中要求声明 `actions/*.json` 的说明，都删掉。内容步骤结果里的 `actions/*.json` 在规则检查（`_action_candidate_problems`）和接收（`_action_candidates`）中一律当普通文件忽略，不退回。
5. **实现 `AUTHORIZED_SLOT` 来源**（`operation_intent_sources.py:77` 目前直接拒绝）。原则：**候选引用仍指向那份真实的源文件本身，只有申请单 JSON 换成系统生成的**。这样整条链对"已通过、已接受的真实文件"的检查照样成立，发布的一定是审过的字节。
   - 现有检查全部保留：`operation_intent_sources.py:131-139、186-195、214-233`；参数绑定 `action_commits.py:334-354`；`operation_materialization_inputs.py:241-255`；提案审阅 `operation_proposal_review.py:284-290`；结果核对 `operation_outcomes.py:250`、`completion_status.py:463`。
   - `slot_key` 为效果键，`origin_receipt_id` 为 `operation_completion_spec_approved` 回执。
   - 目标 = 效果的 `criterion_ids` → 要求原文 → `parse_action_criterion`。
   - 源文件 = 当前已接受内容产出中路径等于目标（其次文件名相同）的唯一产物；生产者为持有它的内容接受记录。零个或多个都不提交，见 C。
   - 申请单 JSON 由系统确定性生成，存入操作载荷库（重启恢复本来就从载荷库读，`operation_materialization.py:486`）。
   - 实现时逐个核对上面每一处检查读的是"源文件"还是"候选 JSON 字节"；读后者的改读系统载荷（开发期单版本契约，不做兼容）。
   - **只准备 L2 及以上（需要人批准）的操作**。发布是 L2（`connectors_publish.py:88-89`）；L0/L1 不需要批准会自动执行（`permissions.py:77`），不在系统代办范围内。
6. **触发**：编排循环新增"准备系统操作"一步。条件：任务有已批准效果、该效果没有当前意图、内容范围都已就绪、唯一源文件存在。
   - 提交入口要求调用者是人（`operation_materialization.py:60-64`），所以**以确认页批准人的身份提交**（取批准回执里的签发人，`operation_completion.py:205-207`）。
   - 幂等键由"效果 + 规格哈希 + 生产者接受记录"确定性生成，重启后能认出同一次提交（现有 intent_id 是随机的，回执 id 由调用者 + 幂等键推出，`operation_materialization.py:163-173`）。
   - 其后沿用现有流程：申请单审阅 → 物化 → 等你批准 → 交接执行 → 结果审阅 → 计入效果。重启恢复沿用 `recover_materializations`。
7. **界面**：`OperationWorkspace.tsx` 去掉候选列表和"提交"，每个效果显示状态（等内容完成 / 系统准备中 / 审阅中 / 待你批准 / 已发布并核对）。批准仍在批准卡片上点。
8. **测试**：步骤要求过滤（链接路径和回退路径各一）；准入剔除操作链接、覆盖检查豁免；内容步骤写的申请单被忽略；系统来源的提交在各种情况下都成立或拒绝（找到唯一文件、零个、多个、重启后幂等）；端到端：确认页 → 内容通过 → 系统申请单 → 批准 → 发布 → 哈希一致。

### C. 找不到源文件时补步骤（第 3 批）

内容范围全部就绪后，如果某个效果的目标文件零匹配，就发一个规划修复请求，写明"效果 X 需要一个产出 `<目标>` 的步骤"，让规划器补步骤。多匹配时同样处理，写明"`<目标>` 由多个步骤产出，需指定一个"。这个请求算规划器的一轮，不算步骤尝试。

- 现有五种触发类型（`repair_adapter.py:34-46`）没有专门的"缺源文件"，"要求变更"那种又必须带修改凭证。所以沿用"审阅不通过"这一类，触发对象填效果所在的根步骤，这样只有规划器对根步骤做出修复才能消掉这条请求（`planning_repair_requests.py:161-185`）。请求键按"效果 + 计划版本"固定，不重复发。
- 规划器若回答"不改"，请求消不掉，系统会一直认为还欠一次修复（`event_handler.py:7732-7739`）。所以这类请求设上限（同一效果 2 次），超过就停下并写明原因，不空转。

没有放在定计划时检查，原因是：计划阶段步骤产出只按端口声明（`delivery` 单文件），不知道具体文件名，没法可靠判断。

### D. 不在本次范围

- 性能改造（主循环只处理有变化的任务）
- 真正的"同一次尝试内当场改格式"
- 省掉系统申请单的第一道审阅

### E. 交付纪律

- 每批：先写测试 → 实现 → 变异 → 相关目录测试 → SDK 发布 → Host 钉版。
- 全量回归只在合并前后各跑一次，逐条对照基线失败名单。
- 独立审阅每批最多两轮，只报阻断。
- 架构文档（`ARCHITECTURE/TASKGRAPH.md`、`ARCHITECTURE/PROJECT_STATUS.md` 等）随交付更新。
- 真机点击用模拟鼠标完成；长任务要配卡死看门狗。
