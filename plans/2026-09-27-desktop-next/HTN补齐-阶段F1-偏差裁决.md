# HTN 补齐 · 阶段 F1：施工中两张偏差单的裁决

- 裁决人：独立偏差裁决子代理（只读代码与文档；没有改任何源码或正式测试）。日期 2026-10-04。
- 工作树：`simple_harness-a4`，分支 `htn-f1`，最新提交 `0ade6b70`（工作区里另一会话未提交的注入点改动与本裁决无关）。
- 依据：`HTN补齐-阶段F-开工裁决与施工清单.md`（1.3 第 14、17 条；第四节用例 9、10）；主计划 `HTN补齐计划-2026-10-02.md` 第 3.18 版（阶段 C 第 3 条"裁决题过期按'没有结论'交规划器"）；仓库根 `CLAUDE.md`（判断交给 LLM、Harness 只管秩序；同一件事一条路径；开发期不兼容旧数据）。
- 路径缩写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`。
- 本次核对跑过的测试：只跑了 `T/product_world/test_review_no_verdict.py::test_a_root_ruling_question_made_stale_by_an_amendment_goes_to_the_planner` 一条（通过，约 100 秒），外加一个照抄它、只多打印几行事件的临时脚本一次（跑完已删，没有留在仓库里）。

**结论一览**

| 单号 | 裁决 | 要不要改代码 | 问用户 |
|---|---|---|---|
| 1 | **按实际改计划**：改要求让旧的最终审查整份作废，规划器只收一条"要求已更新"，不另发"没有结论"。用例补两条反向断言 | 不改产品代码；用例补断言、改说明文字 | 否 |
| 2 | **按计划的本意改代码**：候选清单只列"现在采用不会被闸门拒"的做法，和闸门用**同一个判据函数**；被挡下的另列一行说明。在 F1 里作为一处小修做掉 | 改 3 个产品文件 + 1 个用例 | 否 |

---

## 偏差单 1：等人裁决的最终审查题目因改要求而过期

### 计划原文

- 施工清单 1.3 第 14 条：用例 5 缺"裁决题过期 → '没有结论'修复请求交规划器"的断言与改坏 → F1-1（改要求让等人裁决的根终审题目过期）。
- 第四节用例 9：等人裁决的根终审题目因改要求过期 → 一条"没有结论"（`NO_VERDICT`）的修复请求交规划器；改坏"过期时不发修复请求"。
- 来源：阶段 E 施工清单第 140、287 行（"等回答的问题因绑定的要求版本变了而过期；其中裁决题过期按阶段 C 已有出口'没有结论 → 交规划器'"），主计划阶段 C 第 3 条。

### 发现的事实（含我的核对）

1. **题目怎么过期**：`SDK/storage/planning_human_store.py` `retire_stale` 把"绑定的计划版本号或要求版本号已不是现行的"待答题目标为 `STALE`；只有 `SDK/orchestrator/event_handler.py` `_resume_planning_services` 调它。绑定只有计划版本与要求版本两项（阶段 D 偏差单 6 去掉了纪元）。
2. **"没有结论"从哪发**：根终审只在 `_advance_root_review` 读到状态为"等人"（`AWAITING_PERSON`）时进 `_ask_person_to_adjudicate_root`，题目已过期才调 `_request_root_review_repair(..., ruling_stale=True)`，带上 `_RULING_STALE`（`outcome: NO_VERDICT`）。
3. **改要求后状态还是不是"等人"**：不是。`SDK/orchestrator/root_review.py` `RootReviewCoordinator.state` 里，审查包绑的要求版本不等于现行版本时 `stale_reasons` 给出 `REQUIREMENTS_MOVED`，状态变成"要重切"（`RECUT_REQUIRED`，或在新计划还没验收完时更早停在 `NOT_READY`），根本走不到读正式记录、判"等人"那一段。所以改要求这条路上，"没有结论"分支**按设计就不会走**——旧审查是"整份作废、换新包重审"，不是"这份审查等不到结论"。
4. **临时脚本核对**（照抄该用例，改要求后多看几项）：
   - 改要求后 8 轮：修复请求只有一条，`source_key = requirements:<第 2 版>`；**没有** `root-review:<旧记录>` 那条；规划器收到的全是 `REQUIREMENTS_UPDATE`。
   - 旧裁决题 `adjudicate-root:<旧记录>` 为 `STALE`。
   - 任务最后 `FAILED`：原因是脚本化规划器 `planner_reply`（`SDK/testing/scripted_replies.py`）见到修复请求就返回空、不重排，属剧本限制，不是产品问题。用例说明里"规划器……按新要求重排"这半句因此**没有被这条用例证明**。
5. **"没有结论"这条出口本身还有没有用**：有，但很窄。题目过期而审查包仍站着，只可能是"计划版本变了、要求和各步成果都没变"。阻断性题目在等时规划轮不会开（`_resume_planning_services` 见 `pending` 即返回），只剩"题目登记前已在途的一轮规划回来提交了新计划"这一种。真发生时若没有这条出口，`_ask_person_to_adjudicate` 见题目不是"已答"就永远返回 False，任务只能等空闲判停。所以它是**防卡死的最后出口**，应保留，不属于"旧路径"。

### 裁决

**按实际改计划。** 产品代码不动；用例保留现在钉的行为，并补两条反向断言、改说明文字。

### 理由

- **一件事一条路**：用户改要求，规划器需要知道的事实是"要求变了、变了哪几条、旧结果在新要求下还算不算数"，这些都在"要求已更新"请求里（`context.changes`、`accepted_results[].counts_under_current`）。旧终审是按旧要求做的，它的"没有结论"对新要求没有任何意义；再发一条，等于让规划器为一份已作废的审查做修复，还会白占一次"最终审查打回交规划器"的名额（`max_root_review_repairs`，`_root_review_request_keys` 按记录计数）。
- **判断仍在 LLM**：新要求下的新计划做完后会重切一份新审查；新审查若仍判不下来，会以新记录号再问一次人。不存在"人该裁的事被系统吞掉"。
- **与用户 10-02 定的规则不冲突**：规则的用意是"裁决题过期后不能让审查永远停在'在等'，要如实交规划器"。改要求时审查不是"停在等"，而是"作废重来"，规则的前提不成立；审查仍站着、题目却过期的情形（第 5 条），规则照旧生效。
- 原计划这句话是把"题目过期"和"审查作废"当成一回事写的，属于计划写错了机制，不是代码偏离。

### 要改的计划文字

1. 施工清单第四节用例 9 改为：
   > | 9 | 同文件用例 5（补，实际落在 `test_review_no_verdict.py`） | 等人裁决的根终审题目因改要求过期（`STALE`）；旧终审随旧要求作废，规划器收到"要求已更新"修复请求（带改了哪几条），**不**另发针对旧记录的"没有结论"请求，旧终审不再是"等人" | 改要求时不让题目过期 |
2. 施工清单 1.3 第 14 条"事项"一栏括号里补一句："改要求时旧终审整份作废，走'要求已更新'一条路；'没有结论'只用于审查仍站着而题目过期的情形（F1 偏差单 1）"。
3. 第五节偏差单表加一行 **F-13**：计划原文"裁决题因改要求过期 → '没有结论'交规划器" / 事实"改要求先让审查包作废（`REQUIREMENTS_MOVED`），根终审走不到'等人'" / 裁决"按实际；'没有结论'出口保留作审查仍站着时的防卡死出口" / 理由"一件事一条路，不为已作废的审查占修复名额" / 告知用户：否。
4. 主计划阶段 C 第 3 条"裁决题过期按'没有结论'交规划器"后加半句："（审查本身因改要求作废、要重切时，不另报'没有结论'，由'要求已更新'一条交规划器——第 3.18 版 F1 偏差单 1）"。版本记录第 3.18 版一行里带上"F1 偏差单 1、2"。
5. 阶段 E 施工清单是历史文件，不回改。

### 要改的用例（`T/product_world/test_review_no_verdict.py::test_a_root_ruling_question_made_stale_by_an_amendment_goes_to_the_planner`）

在现有两条断言之后加：

```python
record_id = row["request"]["repair_context"]["record_id"]
# 一条路：旧终审随旧要求作废，不再为旧记录发"没有结论"
assert not [e for e in world.store.list_events(mission_id)
            if e.type == "PlanningRepairRequested"
            and e.payload.get("source_key") == "root-review:" + record_id]
# 旧终审已不是"等人"（要重切或还没就绪）
from agent_orchestrator.orchestrator.root_review import RootReviewStatus
state = world.loop._root_review(mission, world.dispatch).state(mission_id)  # 取协调器的方式按产品同形世界现有写法
assert state.status is not RootReviewStatus.AWAITING_PERSON
```

（取协调器的写法以产品同形世界里已有的为准；做不到就只留第一条断言，第一条是必须的。）

说明文字把"规划器收到'要求已更新'按新要求重排"改成"规划器收到'要求已更新'（带改了哪几条），不另收针对旧审查的'没有结论'"——这条用例的剧本不重排，不要写它没证明的事。

改坏仍用现有那条（改要求时不让题目过期 → 变红）。不必为反向断言另做改坏（要造"改要求后仍对旧记录发'没有结论'"得往产品里加代码，不值）。

### 顺手记一处小问题（不阻断，可不改）

`event_handler.py` 的 `_RULING_STALE.reason` 写"计划、要求或管理纪元变了"，纪元早已不在题目绑定里（阶段 D 偏差单 6）。这是给规划器看的运行时文字，不在执行池身份里，改不改都不影响行为；若改，同时改 `method_plan_reviews.advance` 里同样的那句，保持一个口径。

---

## 偏差单 2：候选做法里仍列着本任务审阅没通过的做法

### 计划原文

- 施工清单 1.3 第 17 条与第四节用例 10："未审过的做法直接提交 → 计划提交被拒、带 `METHOD_NOT_AUTHORIZED`"；剧本里闸门没被触发就"改用直接提交未审做法的剧本触发一次"。计划默认的前提是：规划器正常读候选就不会碰到闸门，只有"硬要采用"才会被拒。

### 发现的事实（含我的核对）

1. **闸门**：`SDK/orchestrator/plan_commits.py` `_check_method_reviews` 调 `SDK/orchestrator/method_plan_reviews.py` `unreviewed_adopted_methods`：只放行部署自带的种子做法（`command.seed_methods`，来自 `hierarchical_dispatch.py` 约 3553 行 `require_planning_world().seed_methods`）和本任务审阅"通过"或"人裁决通过"的做法（`review_of(...).passed`）。其余一律拒：在审（`PENDING`，含审阅调用用完没出记录的）、等人裁决（`AWAITING_PERSON`，含裁决题过期的）、打回（`REJECTED`）、本任务没审过（`NONE`）。
2. **打回是终局**：`review_of` 按"做法编号+版本+内容哈希"取本任务最近一份做法审阅；同一版本不能再提（身份已占用），打回记录不能被人裁决翻（裁决只针对"判不下来"）。所以一个被打回的版本，**闸门对它永远说不**。
3. **候选清单**：`SDK/orchestrator/planning_selection.py` `candidate_context` ← `hierarchical_dispatch.py` `method_candidates` ← `method_applicability`。后者只用 `registry.retrievable`（最新版本、种子或本任务试用中的做法）和能力、类型筛；**不看审阅结论**。所以本任务提出、正在审或已被打回的做法照样出现在 `method_selection[].applicable` 里。
4. **规划器被告知的东西互相打架**：提示词（`SDK/runtime/role_templates.py` 约 160 行）说"可以采用的只有……review.outcome=PASSED 的；采用别的会在提交时被拒"，约 174 行又说 method_selection "系统只按能力和类型把跑不了的筛掉"。规划器在一个叫"候选"的清单里看到一个明知采用会被拒的做法。
5. **有现成先例**：`SDK/planning/htn/registry.py` `retrievable` 里，旧版本做法早就不进候选，注释原话大意是"把旧版本当候选给出，规划器就会引用它、白丢一轮在 `METHOD_STALE` 上"（2026-10-03 产品世界那一局）。这次是同一类问题：候选里列了闸门必拒的东西。
6. **脚本化规划器本来就是这么理解候选的**：`SDK/testing/scripted_replies.py` `planner_reply` 取 `applicable[0]` 直接采用，决定理由写的就是"采用已通过独立审阅的做法"。施工中用例死循环（打回 → 照候选采用 → 闸门拒 → 再来，直到规划次数用完、任务失败），就是这个前提和实际不符的直接表现。
7. **规划器在审阅没结束时也可能被叫**：`method_plan_reviews.awaiting` 只用于空闲判定（`event_handler.py` 约 3089 行），不挡规划轮；多目标任务里规划器为别的目标出计划时，候选里也会列着"还在审"的做法。
8. **全库做法不受影响**：全库条目不在 `views.methods`、也不在候选里（提示词约 176～179 行），借鉴走 `PROPOSE_METHOD` 重新提、重新审。所以"排除没审过的"不会误伤复用路径。

### 裁决

**这是秩序，不是语义；按计划本意改代码。** 候选清单只列"现在采用不会被闸门拒"的做法，判据与闸门是**同一个函数**；被挡下的做法不从规划包里消失，另起一栏列出编号和审阅状态，审阅意见仍在 `views.methods[].review` 里由规划器自己读。**在 F1 里作为一处单独的小修做掉**（不推给 TaskGraph 补全，也不推给 F2）。

### 理由

- **秩序还是语义**：一个做法"能不能被采用"不是 Harness 的判断——是审阅员（或人）已经下了的结论，闸门只是照正式记录执行。候选清单是一份"你现在能用哪些"的视图；视图里列出闸门必拒的东西，不是"把判断留给 LLM"，而是**同一条秩序在两处说法不一致**。让视图和闸门用一个判据，正是"同一件事一条路径"。
- **判断仍在 LLM**：要不要改好重提、换别的做法、问用户，规划器照旧读 `views.methods` 里的审阅意见自己定。我们只是不再把"必拒"的东西摆进"可选"那一栏。
- **与四种情形的关系（不冲突）**：
  - 种子做法：闸门放行 → 仍在候选。
  - 人裁决通过：`review_of` 返回"通过" → 仍在候选（裁决前在"挡下"一栏，裁决后自动回来）。
  - 在审 / 等人裁决：闸门拒 → 挡下，结论出来后自动回到候选（审阅有结论时本来就会叫醒规划器）。
  - 打回 / 审阅给不出结论：闸门永远拒 → 挡下；规划器要用就改好以新版本重提。
- **为什么现在做、在 F1 做**：阶段 F 开工裁决立的分界是"测试可以攒到最后，**已知的代码缺口不能推给测试阶段**"；这不是 TaskGraph 的功能（是 HTN 选做法的视图），放进 TaskGraph 补全会混；改动只有三个文件、一个判据函数、一句提示词。F1 还没发版，顺带做掉成本最低。真实模型上它的代价是白丢规划轮，F2 真机时再暴露只会更贵。
- **F1 原写"不升提示词"**：那句话是为了不给 G 添乱、不动执行池身份。提示词模板不在执行池身份里（身份只记工具说明哈希与分词器指纹，见记忆《工具说明即执行池身份》2026-10-01 补记），我也没找到钉这段提示词字节的测试；G 改的是事件写入，与此无关。所以这句话改成"只有规划器提示词一句话的改动，执行池身份不变"即可。

### 要改的代码与断言

1. **`SDK/orchestrator/method_plan_reviews.py`**：从 `unreviewed_adopted_methods` 里抽出单个做法的判据，作为唯一判据：

   ```python
   def adoption_refusal(store, mission_id, reference, seeded) -> dict | None:
       """这一个做法现在采用会不会被闸门拒：不会 → None；会 → 拒绝行（与闸门同一份）。
       seeded 是 {(method_id, version, content_hash)}。"""
   ```

   内容就是现在循环体：种子 → None；`review_of(...).passed` → None；否则返回 `{"method_ref", "review", 可选 "findings", 可选 "human_ruling"}`。`unreviewed_adopted_methods` 改成对每个 draft 调它、收集非 None 的行。闸门行为一字不变。加进 `__all__`。

2. **种子集合一处取**：在 `SDK/orchestrator/hierarchical_dispatch.py` 加一个小方法（如 `seed_method_keys()`，返回上面的三元组集合，来源就是现在 3553 行那句 `require_planning_world().seed_methods`），3553 行的计划提交与下面的候选过滤都用它，免得两处各取一遍。

3. **`SDK/orchestrator/planning_selection.py` `candidate_context`**：对每个目标，把 `result.applicable` 按 `adoption_refusal(dispatch.store, mission_id, MethodRef(method_id=c.method_id, version=c.method_version, content_hash=c.method_content_hash), seeds)` 分成两组：
   - `applicable`：返回 None 的（截前 12 个），`applicable_count`、`omitted_count` 按这一组算；
   - 新加 `not_adoptable`：其余的，每条只放 `{"method_id", "method_version", "review"}`（`review` 取拒绝行里的状态：`PENDING` / `AWAITING_PERSON` / `REJECTED` / `NONE`），最多 12 条。审阅原话不复制，在 `views.methods` 里。
   - `hierarchical_dispatch.method_candidates` 不动（它的说明"只按能力和类型筛"仍然对）；`planner_views.py` 里 `method_rows(first=...)` 照旧用 `applicable`，不用改。

4. **`SDK/runtime/role_templates.py`**（约 174～175 行）把"系统只按能力和类型把跑不了的筛掉，没有替你选"改成：
   > 系统把跑不了的（能力、类型不符）和本任务里现在采用会被拒的（审阅还在进行、等用户裁决、已打回）筛掉——后者列在 not_adoptable 里，原因看 views.methods 该条的 review；除此之外没有替你选：……

   同时把约 160 行"没有 review 字段的（库里原有的）和 review.outcome=PASSED 的"保持不变（与新判据一致）。改完重生成依赖这段文字的基线（若有）。

5. **用例 `T/product_world/test_method_library.py::test_a_method_not_passed_here_is_refused_at_the_plan_commit`**：
   - 去掉剧本里绕开候选的那段（`refused` / `usable` 过滤与第 463 行注释）：强行采用一次之后直接交给 `planner_reply`——候选里已没有打回的做法，它自然会走"提一个新做法"。
   - 在规划器回调里记下审阅打回之后第一次收到的规划包，断言：
     - 打回的做法（编号+版本）**不在** `method_selection[0]["applicable"]` 里；
     - 它**在** `method_selection[0]["not_adoptable"]` 里，`review == "REJECTED"`；
     - `views.methods` 里该条 `review.outcome == "REJECTED"`（审阅意见仍交给规划器）。
   - 原有断言保留：强行采用那次被拒、拒绝码恰为 `["METHOD_NOT_AUTHORIZED"]`；任务最终完成。
   - **改坏检验**（加进 F1-5 的改坏清单）：`candidate_context` 不做这一步过滤 → 剧本照候选采用打回的做法、反复被拒到规划次数用完 → 任务不完成 / 上面"不在 applicable"的断言变红。原有改坏"闸门放行没审过的做法"保留，两条分别守视图和闸门。
   - 只跑这一个文件里相关的几条，外加 `T/product_world/` 里直接用 `planner_reply` 走种子做法的一条现成用例确认种子仍在候选（任选一条），不跑整目录。

6. 施工清单与主计划文字：
   - 第四节用例 10 的断言栏补："候选清单不列本任务审阅未通过（在审、等裁决、打回）的做法，另列 `not_adoptable`；硬要采用时闸门拒、带 `METHOD_NOT_AUTHORIZED`"；改坏栏补"候选不按闸门判据过滤"。
   - 第五节偏差单表加 **F-14**：计划默认"正常读候选不会碰闸门" / 事实"候选不看审阅结论，打回的做法仍在候选，脚本化规划器死循环" / 裁决"候选与闸门共用 `adoption_refusal`，挡下的另列；F1 内小修" / 理由"秩序一处说清；有 `retrievable` 排除旧版本的先例" / 告知用户：否。
   - F1-7 收尾那句"没有提示词……变化"改成"只有规划器提示词里 method_selection 一句说明的改动（不在执行池身份里），执行池身份不变"。
   - 主计划第 3.18 版版本记录带上"F1 偏差单 2：候选做法与采用闸门同一判据"。

---

## 需要用户本人决定的事

没有。
