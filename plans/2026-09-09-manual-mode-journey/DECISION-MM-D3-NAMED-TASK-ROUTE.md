# 裁决备忘：点名任务 ≠ 活跃任务时的路由与 `task_scope_conflict` 文案（MM-D3）

- 日期：2026-09-09
- 事故：Manual 模式旅程 run4，T7
- 证据目录：`.local-test-evidence/2026-09-09/native-manual-run4/primary-ui-dys43uzo/`
- 相关记录：`plans/2026-09-09-manual-mode-journey/00-PLAN.md`、`RUN-04-PARTIAL.md`
- 冻结 SDK：`simple_harness_sdk`（`TaskScopeRoute` 只有五个路由，本次未改）
- 分支：`worktree-scope-conflict`（基线 main `255f3aad`）
- 口径：与 HM-TO-A6 事故 B（`DECISION-STANDALONE-ROUTE-TOOL-AUTHORITY.md` §二.3）完全一致
  ——**稳定码与 schema 一律不动，只补披露与可行动文案**。

---

## 一、事故序列（逐行取自证据）

Run `product-sdk-169be262ea0c5ebfb180a1d415069916dae0c59fac8cd90251cc905ec634dffd`
（`userdata/data/simple-harness-sdk/execution-v6.sqlite3` 的 `execution_effects`）：

| # | tool | 参数 | 结果 |
|---|---|---|---|
| 1 | `task_scope_search` | `{"query":"二号任务 Task No.2"}` | succeeded，3 个候选 |
| 2 | `context_route` | `{"route":"continue_active"}` | **accepted → 绑定一号 `249de2ed…`** |
| 3 | `task_scope_search` | `{"query":"手动 核验 manual"}` | succeeded |
| 4 | `context_route` | `resume_existing 5e880579…` + `expected_source_hash` | **rejected `task_scope_conflict`** |
| 5–12 | `tool_search` / `context_page_in` ×8 | 找 `list_directory` | 循环 |
| 13 | `task_scope_update` | `outcome=no_mutation`，closure_reason 自述「一个 Run 无法改绑」 | rejected |

`state.db.context_route_tool_invocations` 里第 4 行的 `detail_json` **全文**是：

```json
{"code":"task_scope_conflict","contested_probe":"clear",
 "contested_probe_query_sources":["model_query","user_turn"],
 "message":"execution_run_scope_conflict"}
```

## 二、诊断：模型在选 `continue_active` 之前看到了什么

### 2.1 `task_scope_search` 结果没有标出「谁是活跃任务」，也没标出「谁是被点名的那个」

第 1 次搜索的 `result_json`（三个候选）：

| task_scope_id | `scope_disclosure.status` | `disclosure.fields` |
|---|---|---|
| `249de2ed…`（**一号**，当时的活跃任务） | `active` | `title=手动模式核验一号`、`goal=…` |
| `5e880579…`（二号） | `active` | `{}`（title/goal 皆 `original_sources_unavailable`） |
| `84b45f9b…` | `active` | `{}`（同上） |

三点结论：

1. **`is_active` 这个事实当时根本不存在。** 候选项的键只有
   `task_scope_id / source_id / source_hash / scope_disclosure`
   （`sdk_adapters/context_route.py` 旧 `handle_task_scope_search`）。
2. **`status` 是个陷阱。** 它是 scope 自身的生命周期（未完成即 `active`），
   三个候选全是 `active`，它从来不回答「`continue_active` 会选中谁」。
3. 唯一带标题的候选恰好是**一号**（另外两个的 title 被披露判定为
   `original_sources_unavailable`）。所以模型能读懂的那一条正好是错的那一条；
   它在第 13 步的 closure_reason 里自己写明了这一点：
   「候选 5e880579 和 84b45f9b 的标题不可用，无法确认哪个是二号任务」。

`note` 里唯一相关的一句是「Active tasks may use resume_existing with their exact
task_scope_id」——只说了「可以」，没说**点名任务不是活跃任务时必须**这么做。

### 2.2 `context_route` 的 schema / 描述 / PERSONA 都没有这条规则

- schema（`CONTEXT_ROUTE_SCHEMA`）对 `continue_active` 无任何描述文字。
- 工具描述（`backend/main.py`）：`continue_active (exact current task)`，
  `resume_existing (exact task_scope_id from a confirmed task_scope_search candidate)`
  ——没有说「用户点名的任务若不是活跃任务，只能 `resume_existing`」。
- PERSONA（`execution/primary_context.py`）原文：
  「continue_active continues this Run's own active task; an active scope needs no
  search. To find earlier work the user names by an old name, a project name or a
  year, call task_scope_search first … Then resume_existing with its exact
  task_scope_id.」
  这是**面向「找旧东西」**的措辞。T7 的二号任务是个**当前存在、未完成**的任务，
  不落在「earlier work / old name / a year」的语感里，所以这条指引没有触发。
- 反向证据：事故 B 的修复刚刚给零命中回执加了「有活跃任务就 `continue_active`，
  不需要搜索」。该文案本身正确，但它是**唯一**在工具面被强化过的路由启发，
  在 T7 这种「有命中且点名了非活跃任务」的场景下把模型往错的方向推了一把。

### 2.3 `task_scope_conflict` 拒绝里没有任何可执行的下一步

- 抛出点：`execution/evidence_ingress.py:498`（`_assert_run_scope_tx`）与
  `:1213`（`_advance_watermark_tx`），码 `execution_run_scope_conflict`，
  异常类 `task_scope/store.py:35` 的 `TaskScopeConflict.code = "task_scope_conflict"`。
- 到达模型的路径：`context_route.handle_context_route` 的通用
  `except Exception` → `code = exc.code`、`message = str(exc)[:512]`。
- 因此模型拿到的就是上面那两个字符串：**不报被绑 scope、不报被请求 scope、
  不给下一步**。它随后花了 8 步 `tool_search`/`context_page_in`、一次
  `task_scope_update`，最后自己推理出了正确结论——但那时 react 预算已经耗尽，
  MM-4 / MM-5 一项都没跑到。

---

## 三、裁决 (d)：**同 Run 不允许改绑**，不实现 `handoff` 语义

任务书问：一个「唯一带 scope 的效果只是 `continue_active` 路由决定、还没有实质
效果」的 Run，能否再改路一次？**不能**，理由是契约 + 证据两条：

1. **「还没有实质效果」这个状态不存在。** 该 Run 的
   `state.db.harness_evidence_reservations` 首行就是：

   ```
   source_sequence=1  kind=route_decision  task_scope_id=249de2ed…  reserved_at=1788915986.28
   ```

   而 tool_invocation 落库时间是 `…986.34`。也就是说
   `ContextRouteLedgerStore.record_route_decision` 在**记录决定的同一个事务里**
   经 `_ingest_fact_tx` → `EvidenceIngress.ingest_ledger_fact_tx` 把这条 route
   decision 写进了一号的 canonical archive，并建立
   `task_scope_run_watermarks(run_id → task_scope_id)`。**接受路由本身就是实质
   效果。** 到 T7 结束时该 run 的 watermark 已推进到 41。

2. **S4/S5 冻结契约不给改绑留口子。**
   - S4 Task 2（`slices/S4-host-primary-taskscope.md`）：canonical archive
     append-only，业务 payload 与 hash 不允许 UPDATE/DELETE，修订只能用新 event；
     `source_event_id+hash` 幂等 + 持久 ingest receipt/cursor。已入档的 4 行
     （route_decision / tool_invocation / context_snapshot / provider_invocation）
     无法回收。
   - S5 Task 5（`slices/S5-host-context-integration.md`）：
     「stale/missing fail-closed，**下一轮 route 才能刷新**」——刷新单位是下一个
     Run 的 route，不是 Run 内改绑。
   - S5 Task 2 的验证清单直接点名要 fail-closed 的两个危害就是
     「active scope inertia、false resume」。
   - 冻结 SDK 的 `TaskScopeRoute` 只有五个成员，**没有** `handoff`；
     `context_route` 的 `ROUTES` 与之一一对应。新增第六个路由 = 改冻结契约。

**结论：唯一诚实的下一步就是「本轮结束、下一轮直接 `resume_existing`」。**
本次不新增任何路由、不放宽任何权威，只把这句话写进拒绝回执。

---

## 四、实际做的修复（披露 + 文案，事件 B 口径）

| 文件 | 改动 |
|---|---|
| `backend/deskpet/sdk_adapters/context_route.py` | ① 每个搜索命中新增 `is_active`（来自 `_current_active_task_scope_id()`，读 `latest_task_route_decision()`，账本不可用时退化为 `False`/「本 Run 尚无活跃任务」，**绝不**让搜索失败）；② 命中时新增 `route_hint`（`_named_task_route_hint`）：说明 `is_active` 既不是「用户点名的任务」也不是 `scope_disclosure.status`，并点名 `resume_existing` + 活跃任务的 id；③ `task_scope_conflict` 拒绝新增 `reason_code=context_route_run_scope_bound_elsewhere`、`bound_task_scope_id`、`requested_task_scope_id`、`next_step`（英文）与 `next_step_zh`（「本 Run 已绑定 X；请回复用户说明并在下一轮直接 resume_existing Y」）。 |
| `backend/main.py` | `context_route` 与 `task_scope_search` 的工具描述各补一句「点名任务若 `is_active=false` 就走 `resume_existing`；一个 Run 只绑一个 scope、永不改绑」。 |
| `backend/deskpet/execution/primary_context.py` | PERSONA 路由段就地改写：`For any other task the user names — by an old name, a project name or a year — call task_scope_search first … Then resume_existing with its exact task_scope_id, **never continue_active: it binds this Run to the active task irreversibly.**` |
| `backend/deskpet/execution/primary_dependencies.py` | `read_run_dependencies` 的候选项再推导：把 `is_active` 排除在内容等价比较之外，并要求它是 bool。 |

### 4.1 为什么 `primary_dependencies.py` 必须一起改（4 行）

`primary_dependencies.py:410-413` 用**精确字典等价**把搜索候选项从已验证的
`scope_disclosure` 重新推导一遍，多一个键就抛 `scope_search_candidate_unverified`
（`tests/execution/test_scope_disclosure_runtime.py` 的三个 `-search` 变体因此变红）。

裁决：**`is_active` 作为「Host 注解」放行，而不是当作可再推导的内容。**
它是 Host 自己的 route 账本游标，不携带任何披露内容，而且**故意不在验证时重算**
——活跃游标与 package 相互独立地移动，事后重读得到的是另一个事实而不是一次校验。
因此该处只允许**恰好一个布尔注解**，其余每个内容字段仍是对 package 的精确再推导。
缺省 `False` 保证旧回执照常通过。

（该文件的 Run 生命周期部分归事件 X；本次改动只在 `read_run_dependencies` 的搜索
候选校验处，最小 diff，不触碰生命周期路径。）

### 4.2 8192 档 PERSONA 预算门

`tests/sdk_adapters/test_token_estimator_calibration.py::
test_persona_and_route_schema_still_fit_the_8192_tier_megabyte_turn`
在 F-NC1 之后只剩 3 token 余量。本次在同一块内做了**语义中性**压缩来抵消：

| 原文 | 压缩后 |
|---|---|
| `if retrieval finds no supporting record or fails` | `if retrieval finds none or fails` |
| `find a saved one with procedure_discover` | `find one with procedure_discover` |
| `adopt neither value … until the user has confirmed` | `adopt neither … until the user confirms` |
| `a missing existing workspace never prevents…` | `a missing workspace never prevents…` |
| `Never conclude from the absence of a Procedure…` | `Never conclude from a Procedure's absence…` |
| `with no such reference_id present, do not call…` | `without such a reference_id, do not call…` |
| `It overrides the same value…` | `It overrides that value…` |
| `nothing after that marker belongs to it` / `including when` | `nothing after it belongs to the quotation` / `even when` |
| `procedure_use inside the routed TaskScope` | `procedure_use in the routed TaskScope` |
| `checks its binding authorization` | `checks binding authorization` |

结果：PERSONA `1121 → 1122` token（净 +1），fixed `2284 → 2285`，
门余量 `3 → 2`，字符数 4482 → 4487（上限 4900）。
被既有测试钉住的三句原样保留：
`an active scope needs no search`、`task_scope_search first`、
`Rewriting or shortening the user's own words needs no context tool or recall.`、
`ask the user which one applies`。

**工具描述不进这道门**（该测试的 fixture 用 `description=name`），
所以两段描述的扩写不占 PERSONA 余量；它们仍然是真实 wire token，
计入的是 8192 档的可变半边。

---

## 五、测试

新增于 `backend/tests/sdk_adapters/test_context_route_tool.py`（6 条）：

1. `test_search_hits_flag_which_candidate_is_the_runs_active_task` —— 活跃/非活跃两条命中的 `is_active` 与 `route_hint` 点名 id；搜索仍不写路由决策。
2. `test_search_hints_say_so_when_no_task_is_active_yet` —— 无活跃任务时全 `False` 且文案说明。
3. `test_active_flag_degrades_to_false_when_the_ledger_is_unavailable` —— 账本抛异常只退化为「不知道」，不失败、不标错。
4. `test_run_scope_conflict_names_both_scopes_and_the_only_next_step` —— 稳定码仍是 `task_scope_conflict` / `execution_run_scope_conflict`，新增 `reason_code` + 两个 scope + 中英文下一步，且原样落库。
5. `test_persona_routes_a_named_task_that_is_not_the_active_one` —— PERSONA 新句 + 既有三条钉子 + 4900 字符上限。
6. `test_context_route_description_names_the_named_task_rule` —— 两段工具描述的正则断言。

单进程、逐个文件执行结果：

| 文件 | 结果 |
|---|---|
| `tests/sdk_adapters/test_context_route_tool.py` | 41 passed（35 原有 + 6 新） |
| `tests/sdk_adapters/test_token_estimator_calibration.py` | 全绿（含 8192 档门） |
| `tests/task_scope/test_projections_search.py` + `test_search_access_receipts.py` | 全绿 |
| `tests/execution/test_completed_scope_workspace_continuation.py` | 全绿 |
| 以上四组合计 | **112 passed, 1 failed**（唯一一条见下） |
| `tests/execution/test_scope_disclosure_runtime.py` | 13 passed / 3 failed —— 三条 `memory_suppressed-*` 与基线同码同因；改前基线为 4 failed（含一条 flaky 的 `False-search`），改后**严格少于基线** |
| `tests/execution/test_unscoped_search_late_forget.py::…[create]` | failed —— 已在同一 worktree 上 `git checkout -- backend` 复跑基线，**同样 failed**，与本次改动无关 |
| `tests/quality/test_audit_coverage.py` + `test_s5a/s5b_acceptance_matrix.py` | 12 failed / 26 passed —— 与基线**逐条相同** |
| `tests/execution/test_current_tool_megabyte.py::…[4096]` | failed —— 该 tier 是 `DECISION-TOKEN-ESTIMATOR.md §附录` 记录在案的既有红；8192 与 32768 绿 |

---

## 六、旅程驱动的 T7 措辞：**不改**

T7 原句「我在 <ROOT_B> 放了资料，把这个目录也纳入二号任务的工作范围，然后用
list_directory 工具列出它里面的文件。」是真实用户会说的话：他知道自己有两个任务，
按名字点名了其中一个。**把它改写成「先切到二号任务」等于用测试文案掩盖产品缺陷。**
保持原样，run5 从 T1 重跑同一脚本；MM-4 / MM-5 至今仍未被执行过一次。

## 七、残余

- 二号 / 84b45f9b 两个候选的 `title`/`goal` 被披露判成
  `original_sources_unavailable`，模型因此**看不到任何一个候选的名字**。
  本次的 `is_active` 只解决了「哪个是活跃的」，没解决「哪个叫二号」。
  若 run5 里模型仍无法把用户说的名字对上某个候选，那是**披露源可用性**的独立问题
  （`verify_scope_disclosure` / producer 源），应另开一个事件，不要往路由文案里加料。
- 事故 B 给零命中回执加的「有活跃任务就 `continue_active`」仍然正确，但它与本次
  的命中侧规则构成一对；两者今后若要改动，必须同时看。
