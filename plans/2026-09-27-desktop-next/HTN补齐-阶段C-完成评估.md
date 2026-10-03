# HTN 补齐 · 阶段 C 完成评估

- 评估人：独立评估子代理（只读代码与文档；除本文件外没有改任何文件、没有提交、**没有跑任何测试**）。日期 2026-10-03。
- 对象：工作目录 `simple_harness-a4`，分支 `htn-c`，`git diff main...htn-c`（18 个提交，125 个文件）。
- 依据：`HTN补齐计划-2026-10-02.md` 第 3.9 版阶段 C（第 0、0′、1～6 条）与第六节；`HTN补齐-阶段C-开工裁决与施工清单.md`（C0～C8、12 条用例、改坏检验）；`HTN补齐-阶段C-偏差裁决-知识支持集合.md`；`HTN补齐-阶段C-删金额计价-清单.md`；`HTN补齐-实施记录.md` 的"阶段 C"两节。
- 路径缩写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`。行号以 `htn-c` 当前内容为准。

---

## 〇、结论

**阶段 C 的主体代码已按计划写完，但还不能判"完成"。** 有 1 处代码缺口必须在本阶段补：交接前核对（第 4 条 / C7）跳过了"不可用"的见证，且一直被拒的动作会被当成"等待审批"挂住，永远到不了规划器。另有 6 处偏差没有登记、2 处自述与代码不符，改坏检验也只做了 5 条里的 1 条。

- 三项特别核对都过了：
  - "判不下来"只覆盖回复回来了但不能用的情形，调用没回来的不算；
  - 知识依据只记在 `support` 字段，`KnowledgeRecord.dependencies` 已删净；
  - `tool_gateway.py` 的改动为零行。
- 偏差单 2～7：**接受 5 张（2、3、5、6、7）**；第 4 张**部分接受**（用例构造方式接受，"验收不再当前一直拒绝"那一支背后有代码缺口，不接受推后）。
- 未裁决偏差：实施记录里待裁的 6 张（2～7）由本文裁决；另有**未登记偏差 6 处**（第二节）要补登记，裁决意见本文一并给出。

---

## 一、逐条对照

状态说明：✅ 按计划完成；◐ 有已裁决偏差；△ 有未裁决或未登记偏差；✗ 没做。

### 1.1 计划阶段 C 各条

| 条 | 状态 | 代码落点与说明 |
|---|---|---|
| 格式口径 | ✅ | `SDK/assurance/checks.py:347` `decode_review_reply` 是唯一解码入口（剥整段围栏，按层删去值为空的多余键，其余仍按严格规则拒收）；两处调用都已改用：`assurance_review_collect.py:161`、`assurance_review_import.py` `_interpret`。代码里已没有 `ReviewReply.from_json(decode(` 的旧写法 |
| 0 删金额计价 | ✅ | 迁移 38（`SDK/storage/schema.py:793`）删 13 列并重建触发器；`governance/provider_prices.py` 已整删；准入身份升 v4。在 `agent_orchestrator`、Host 编排目录、前端下 grep 金额相关词，只剩清单 1.10 节列为"不动"的原生层 `simple_harness`。执行池身份字节基线没变，与清单第〇节第 3 条一致 |
| 0′ 全局扫描进同一边界 | △ | `event_handler.py:3339` `_round_boundary` 与 `:3359` `_settle_parked_faults`。五处扫描都已套上：`accounting_recovery.py:202`、`assurance_tick.py:216,237`、`taskgraph_notifications.py:245`、`event_handler.py:3185`、`planning_runtime_block.py:148`。**未登记偏差**：①用例注入的是"执行图通知"，计划写的是"迟到用量导入"（`T/product_world/test_round_faults.py:336`）；②计划要求的"先清点 `_cycle_inner` 里在边界外、逐个任务处理的调用"没有写出清点结论，`_refuse_unsupported_contract`（`:1452`，逐任务写库）仍在边界外 |
| 1 知识进库 | ◐ | 确认读取在 `commit_service.py:1192` `_review_confirmations`：要求整体通过或人裁决通过、逐条确认、内容哈希一致、至少一项证据不是审查对象本身。依据由 `:1225` `_knowledge_support` 算出，随知识行一次写入；验收时由 `:2847` 把锁定证书里的正式记录传进来。读时判定在 `SDK/memory/knowledge_standing.py:85`，验收是否当前在 `:31`。偏差单 1 已裁决；偏差单 2 见第三节 |
| 2 黑板取用 | △ | 三层目录在 `SDK/context/knowledge_tools.py:61` `_catalogue`，按是否当前过滤在 `:51`。`memory/blackboard.py` 已删。推给执行者的上下文先过滤：`event_handler.py:5242`。执行者模板加了两个工具：`role_templates.py:431`。审查包两节在 `leaf_acceptance.py:613`，合同字段在 `contracts/resolution.py:845,849`。审阅员信息缺口见 C6。小偏差见第二节 U6 |
| 3 没有结论 → 判不下来 | ✅ | 消费者 `assurance_review_consumer.py:134-142`；导入器 `assurance_review_import.py:171`（第 2 次的格式错误可以导入）、`:293` `_no_usable_reply`、`:306` `_interpret`（第 2 次的可修解读错误也记 `NO_USABLE_REPLY`）。结果审查问人：`operation_outcomes.py:579,604`、`operation_runtime.py:259-273`、`event_handler.py:8939`。完成度一侧也认人的裁决：`completion_status.py:434`。裁决题过期上报在 `event_handler.py:8951`，根终审与组合审查各发一条，按 `source_key` 去重。停止明细在 `:9115,:9165` |
| 4 有效性接线 | △ | `action_commits.py:922,1109` `_validity_refusal`；`contracts/error_table.py:127-134` 把这类拒绝登记为暂时性。**有代码缺口**，见第四节第 3 条 |
| 5 提示词升版 | ✅ | 执行者 `worker-hierarchical-v6`（`role_templates.py:430`）、无人机 v2（`:590`）、AppWorld v2（`appworld_templates.py:10`）；审阅员指令（`assurance/review_input.py`）、编解码版本 v3（`assurance/reviews.py:38`）、上下文组装 v5（`context_builder.py:43`）；规划器没动 |
| 6 真机 | — | 按第 3.5 版流程，放到全部代码写完后统一做，不算缺项 |

### 1.2 施工清单 C0～C8

| 步 | 状态 | 说明 |
|---|---|---|
| C0 金丝雀 | △ | 第 1 项已做：`T/product_world/test_blackboard_tools.py:31`，原生池能把调用路由到编排网关，不用改路由。**第 2、3 项没写进实施记录**：信息缺口第 4、5 条是否存在，以及 `prepare_official_review` 能否走到导入（后者实际已在 `assurance_review_import.py:171` 改通） |
| C1 | ✅ | 同上。修复提示改写在 `assurance_review_transport.py` 的 `_FORMAT_FEEDBACK` / `_INTERPRETATION_FEEDBACK` |
| C2 | ✅（第 7 项◐） | C2.7 要求给重放清单 `review_records` 加备注，没加，原因与偏差单 6 相同（业务表条目不带 `note` 键），按偏差单 6 一并接受 |
| C3 | ✅（小偏差 U6） | `claims` 形状升 v3（`checks.py:258-273`），`DUPLICATE_CLAIM`、`CLAIM_SCOPE` 归入可修错误（`assurance_review_import.py:286`），漏写的结论按未确认处理。"首次切包后冻结"通过读回已存的包实现（`leaf_acceptance.py:628-633`） |
| C4 | ◐（小缺 U7） | 依据记在 `support`（`verified_knowledge.py:58`），代码里已没有 `insert_justification_set` 的调用；`dependencies` 删净（`code_observations.py:94` 和 `commit_service.py:2319,2413` 写的是结论合同 `Claim.dependencies`，不是知识记录的字段）；`编号@版本` 的核对在 `verified_knowledge.py:174-221`。**没做**：C4 第 4 条，`memory/claims.py` 的模块说明与 `basis.reason` 文字没改 |
| C5 | ✅ | `retrieval.py`、`summaries.py` 里没人传的 `stale` 参数已删；`knowledge_view` 增加 `ref` / `basis`，`dependencies` 改从 `support.knowledge` 生成 |
| C6 | △ | 第 1～3 项已做：目录带路径、是否现行、取代者（`reviewer_evidence_tools.py:208-224`）；查找也按路径匹配；候选引用的哈希改成结果信封指纹（`leaf_acceptance.py:605`）。**偏差**：`citable_now` 只在"在初始目录里"时为真（`:325`），计划要的是"初始目录里，**或本回合已整段读过**"，未登记。**第 4 项没有记录** |
| C7 | △ | 见第四节第 3 条；另有偏差单 4、5 |
| C8 | △ | 提示词与钉哈希基线已重生成。文档：`PROJECT_STATUS.md` 已经写了"opt.144"和本评估文件名，但还**没有发版**；`AGENT_ORCHESTRATION.md` 只写了删金额那一条，知识、黑板、判不下来、交接核对都没写进去。需求与场景状态台账没更新（本评估受命只写一份文件，留给下一步） |

### 1.3 十二条用例与改坏检验

| # | 状态 | 落点 |
|---|---|---|
| 1、2 | ✅ | `T/product_world/test_review_reply_format.py:48,59` |
| 3 | ✅ | `test_knowledge_confirm.py:78`。按偏差单 1 断言：支持集合表无行、收尾不报 `EVIDENCE_STALE` |
| 4 | ✅ | 拆成两条：`:109`（只引审查对象本身）、`:128`（总结论为返工） |
| 5 | ✅ | `:158` |
| 6 | △ | 没跑在产品同形世界上，而是并进了 `T/step04/test_claims_knowledge_main_loop.py:398`（旧的 `production_fixture` 世界；入库的知识来自测试观察，不是审阅确认）。"执行者调工具后带版本引用、验收按 `used_knowledge_stale` 拒收"没有端到端走过，版本核对只在函数级直接调用。**未登记** |
| 7 | ◐ | 偏差单 3 |
| 8 | △ | 同样并进了 step04 那条（`:426-437`），不在产品同形世界上。**未登记** |
| 9、10 | ✅ | `test_review_no_verdict.py:62,131`；另加了"打回"的变体 `:148` |
| 11 | ◐ | `:167`，偏差单 4 |
| 12 | ◐△ | `test_validity_handoff.py:42`，只做了"纪元动了"一种情形；见偏差单 4 与第四节第 3 条 |

**改坏检验**：计划要求 A～E 五个功能各一条。实施记录只写了 D（第 2 次格式错误送回"格式用完" → 根终审问人那条变红）。A、B、C、E 只在用例的说明文字里写了"**改坏检验**"，没有执行记录。**算没做完。**

### 1.4 自述与代码不符

1. 实施记录 C-1 第 359 行写"未重写编码清单"，但 `graph/network_codec_manifest_v5.json` 在提交 347062fc（删金额第 1 步）里因为 `contracts/models.py` 删了 `Budget.max_cost_micros` 而改过；同一节第 362 行也自己写了"网络编解码清单哈希更新"。按计划阶段 A 第 6 条，要写明"本阶段重写一次，旧任务的执行图历史读不出"。
2. `PROJECT_STATUS.md` 写的"opt.144"和"完成评估"超前于实际（还没发版、没合并）。

---

## 二、未登记偏差（要补登记；附本评估的裁决意见）

| # | 事项 | 意见 |
|---|---|---|
| U1 | 0′ 用例注入的是"执行图通知"，不是"迟到用量导入" | 接受：五处用的是同一个边界，迟到用量导入那一处的注入用例归入阶段 F 的故障切点清单。另外要补写一句 `_cycle_inner` 的清点结论；`_refuse_unsupported_contract` 也逐任务写库，按同一边界包上（几行代码） |
| U2 | C7 没按清单复用就绪判断的 `_witness_verdict`，也没用合同自带的 `is_fresh_for`，而是自己比纪元 | **不接受**：同一件事出现了第二份判定。见第四节第 3 条 |
| U3 | C6 `citable_now` 只看是否在初始目录里 | 接受，计划文字改为："`citable_now` 只表示在初始目录里；不在初始目录里的条目整段读过后才可引用，由 `cite_rule` 说明"。由取证工具去查披露回执，会给它加一种新的读法，不值得 |
| U4 | C0 第 2、3 项和 C6 第 4 项没有记录 | 必须在阶段 C 内补：核对第 4、5 条缺口，存在就修，不存在就注明"已核对、不存在" |
| U5 | 用例 6、8 跑在 step04 旧世界上，用例 6 的端到端部分缺失 | 接受推到 F：代码路径都已接上（`commit_service.py:2693` 的核对、`knowledge_tools`），属于测试攒到 F。要在实施记录里登记 |
| U6 | 审查包"相关条目"里的已用知识取的是当前版本，没有比对引用的版本和是否当前（C3 第 2 条） | 小事：验收核对会拒掉版本不对的引用。建议补两行，只列"引用版本 = 当前版本且当前"的那些；也可以登记后接受 |
| U7 | C4 第 4 条 `memory/claims.py` 的文字没改 | 在 C 内补（只改文字） |

---

## 三、偏差单 2～7 裁决

**偏差单 2（`acceptance_is_current` 复用完成度读取，不改写两处旧函数）：接受。** 判定的核心推法只有 `read_occurrence_completion` 这一处，三方都站在它上面；去改写 `read_completion_support`（它判的是"读一份完成支持"，不是同一个问题）没有收益。只有"义务没被取消或取代"那几行与 `current_child_supports` 重复，可以留着。施工清单 C4 第 1 条第一小条改为：
> - `acceptance_is_current(store, mission_id, acceptance_id) -> (bool, reason)`：复用完成度读取 `read_occurrence_completion`（与 `current_child_supports` 同一推法）：验收本身是当前的、义务没被取消或取代、所在步骤在现行计划里仍由这次验收撑着。`read_completion_support` 与 `current_child_supports` 不改写（2026-10-03 阶段 C 完成评估接受）。

**偏差单 3（用例 7 不造修复场景）：接受。** 读时判定的函数已经落地，用"依据为空""来源验收不存在"两种情形钉住了；真实修复后旧验收是否被判"不再当前"属于行为验证，按流程攒到 F。用例表第 7 行改为：
> 7 | `test_blackboard_layers_versioned_citation_and_review_package_sections`（step04 世界）| 读时判定：依据为空、来源验收不存在 → 过时；知识行不变 | 完整的"修复后过时"场景归阶段 F（2026-10-03 完成评估接受）

**偏差单 4（用例 11 用管理纪元造过期；用例 12 少两个子情形）：部分接受。**
- 用例 11：接受。产品里没有让任务进行中改计划版本的用户入口，用管理纪元让裁决题过期是真实会走的路。
- 用例 12 的"见证重发后交接成功"：接受推到 F（输入见证每轮重发，`event_handler.py:8673`）。
- 用例 12 的"验收不再当前一直拒绝 → 卡死明细"：**不接受推后**。读代码发现两处代码缺口（第四节第 3 条），已知的代码缺口不能推给测试阶段。

**偏差单 5（错误码表不加 `VALIDITY_STALE`）：接受。** 交接拒绝不跨边界，阶段 B 第 5 类已经立了"交接拒绝暂时性表"这一条路，再加一个码就是两处登记。施工清单 C7 第 3 条改为：
> 3. 交接拒绝原因不进错误码表：在 `contracts/error_table.py` 的交接拒绝暂时性登记里加前缀 `validity_stale:`（与阶段 B 第 5 类的 `taskgraph_target_fenced` 同一处）。

**偏差单 6（重放清单 `knowledge` 条目不加备注）：接受。** 业务表条目的键固定为 class / events / fields / writers（/ gaps），只有非业务表才有 `note`；口径已写进 `knowledge_standing.py` 的模块说明。C2 第 7 条的 `review_records` 备注同样按此处理。施工清单 C4 第 5 条改为：
> 5. 重放清单 `business_replay_inventory.json` 不动（业务表条目不带备注键）；"依据记在知识记录的 support 字段、是否当前读时推出"写在 `memory/knowledge_standing.py` 的模块说明里。C2 第 7 条同理。

**偏差单 7（停滞测试改用"最终审查调用一直不回"）：接受。** 这是行为改了以后测试必须跟着改的地方："两次回复不能用"现在会问人，不再造成停滞。

---

## 四、三项特别核对

1. **"判不下来"只覆盖"回复回来了但不能用"：通过。**
   - 消费者认定"第 2 次不能用"的条件是事件类型为 `AssuranceReviewFormatRejected` 且序号为 2（`assurance_review_consumer.py:134`）。这种事件只在解码失败（`FORMAT_INVALID`）时发出（`assurance_review_collect.py:186-189`）。
   - 调用没回来的分类是 `TURN_FAILED`，发出的是 `AssuranceReviewClassified`，照旧进 `_prepare_format_repair`，写"格式用完"。
   - 导入器的可导入条件也只放行 `FORMAT_INVALID` 加序号 2（`assurance_review_import.py:171-173`）。
   - 模型身份不符、证据暴露不可用这两类分类不受影响。
   - 做法审查的 `_no_verdict_reason` 现在只剩"调用没回来"一类会走到（`method_plan_reviews.py:170`）。
2. **知识依据只记在 `support`：通过。**
   - `KnowledgeRecord.support`（`verified_knowledge.py:58`）有形状校验；写入在 `commit_service.py:1225`，与知识行一起 `upsert_knowledge`。
   - `agent_orchestrator` 里写 `justification_sets` / `support_members` 的只剩 `storage/htn_store.py` 的原有函数（去留归 D），没有新的调用方。
   - 用例 3 断言该任务在表里没有行。
   - `KnowledgeRecord.dependencies` 已删净；`retrieval.py:346` 输出的同名键改从 `support.knowledge` 生成。
3. **工具说明一字未改：通过。** `git diff main...htn-c -- …/runtime/tool_gateway.py` 为 0 行；审阅员证据工具只改了返回内容，没改说明。

**另：交接前核对（第 4 条 / C7）的缺口**，必须在阶段 C 内补：
- (a) `_validity_refusal` 遇到不是 `USABLE` 的见证直接跳过（`action_commits.py:1134`）。按 `hierarchical_dispatch.issue_input_witnesses` 的说明，上游验收不再当前时，重发的见证正是 `UNUSABLE`，作用就是让下游知道"已被撤销"。所以重发之后，最新见证变成 `UNUSABLE`，核对反而放行。这一点是按代码和注释推断的，没有运行验证。
- (b) 判新鲜没用合同自带的 `ValidityWitness.is_fresh_for`（`evidence_state.py:652`），截止时间那一条没比。
- (c) 一直被拒的动作停在 `APPROVED` / `PROPOSED`，而这两个状态属于 `OPEN_ACTION_STATES`。空闲判定的 `approvals_pending`（`event_handler.py:2435`）把它当成合法等待，卡死确认永远不会触发，规划器也收不到。这与施工清单 C7 第 2 条"否则卡死明细里如实写出交给规划器"不符，和阶段 B 卡住类缺陷是同一个形状。

修法（只补秩序，不加语义分支）：
- 最新见证为 `UNUSABLE` 时同样拒绝；
- 判新鲜改调 `is_fresh_for`，纪元取 `current_scope_epochs`；
- 最近一次交接拒绝属于 `validity_stale:` 的动作不算"等待审批"，卡死明细里写出这次拒绝的原因，交给规划器；
- 用例补"验收不再当前 → 一直拒绝 → 卡死明细里有它"一条，配一条改坏检验。

---

## 五、还没做完的清单

### 必须在阶段 C 内补

1. **代码**：第四节第 3 条 (a)(b)(c)，含 1 条用例与 1 条改坏检验。
2. **代码**：`_refuse_unsupported_contract` 套进同一边界，并写出 `_cycle_inner` 的清点结论（U1）。
3. **代码 / 记录**：C0 第 2 项、C6 第 4 项（信息缺口第 4、5 条）核对，存在就修，不存在就注明（U4）。
4. **代码（文字）**：`memory/claims.py` 的模块说明与 `basis.reason`（U7）；U6 两行补齐或登记。
5. **改坏检验**：A（去掉剥围栏）、B（去掉"总结论通过或人裁决通过"）、C（读工具不调 `knowledge_standing`）、E（去掉 `_validity_refusal`）四条逐一执行并写进实施记录。按规则：先 `cp` 备份，改完清 `__pycache__`。
6. **记录**：实施记录补登记 U1～U7；改正"未重写编码清单"那句；偏差单 2～7 写上本文的裁决结果。
7. **计划文字**：主计划升第 3.10 版，修订记录写明"阶段 C 完成评估：偏差单 2、3、5、6、7 接受，4 部分接受，第 4 条补交接缺口"；施工清单 C4 第 1、5 条、C6 第 2 条（`citable_now` 口径）、C7 第 3 条、用例表第 7、11、12 行按第二、三节的文字改。
8. **收尾流程**：
   - 补完后做一次 Opus 只读核验（只报阻断）；
   - 更新 `需求与场景状态.md` 台账（本评估受命只写本文件，没有动它）；
   - `AGENT_ORCHESTRATION.md` 补阶段 C 的生产事实；
   - `PROJECT_STATUS.md` 的版本号等真正发版时再写；
   - 合并 main、发版、Host 钉版、推送。

### 可以留到后面（各有去处）

- 用例 6、8 搬到产品同形世界并走通端到端引用（U5）、用例 7 的真实修复场景、用例 12 的"见证重发后交接成功"、迟到用量导入的故障注入用例：都归阶段 F。
- `justification_sets` / `support_members` / `SupportSetRead` 的去留，以及作用域纪元的生产写方：归 D、E（已在计划里）。
- 真机一局（两步入库、带版本引用、修复后过时）：按第 3.5 版流程，全部代码写完后统一做。
- `KnowledgeIndex` 的"义务是否还在"判断在两处重复（偏差单 2 附带）：可以等以后清理旧路径时一起做，不阻塞。
