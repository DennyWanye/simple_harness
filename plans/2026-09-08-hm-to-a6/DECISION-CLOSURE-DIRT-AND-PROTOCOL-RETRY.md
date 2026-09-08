# 决策：TaskScope 收口脏闸的口径（事件 C）与 provider 协议畸形的一次重采（事件 D）

> 独立子代理裁决 + 实现 + 自审。用户已授权技术取舍自决，不再回问。
> 对象：Host `simple_harness` main `afceea9e`（worktree 分支 `worktree-agent-affbb0daabd2756a4`）。
> 证据：`.local-test-evidence/2026-09-08/native-a6-b3682fe1/primary-ui-xmqudtzt/userdata/data/
> simple-harness-sdk/execution-v6.sqlite3`，Run `product-sdk-26c66feb…`（HM-TO-A6 尝试 3 第 10 轮）；
> 记录见 `plans/2026-09-08-hm-to-a6/RUN-01-ATTEMPTS.md`「尝试 3」表格 T10 行。
> 相关但**不属本次范围**：事件 A（`sdk_task_execution_route_authority_missing`）、事件 B
> （工具描述/发现面）由另一子代理在另一 worktree 处理；本文件不改任何工具 description 字符串。

---

## 0. 两句话结论

* **事件 C**：`require_dirty` 只对 `outcome=no_mutation` 生效。`outcome=mutate` 的计划本身就是
  material 变更（同事务追加 decision + revision，受 `base_revision` CAS 约束），干净档案上照样受理。
  其余守卫一条不改；剩下的 `nothing_to_close` 拒绝理由改成会说清「缺的是什么、什么载荷才算数」。
* **事件 D**：Host 在 `ProductProviderAdapter`（SDK coordinator 之前的最后一帧）对且**仅对**
  `check=tool_call_arguments_not_json` 的 200 响应就地重采一次**同一请求**；第二次仍失败原样上抛，
  Run 照旧判死。合法响应零行为变化，其它协议缺陷不重采，用户已取消时不重采。

---

## 1. 事件 C：`task_scope_update_nothing_to_close` 挡住了用户口述的档案变更

### 1.1 现场

HM-TO-A6 第 10 轮，用户在活动 TaskScope「秋分资料整理」里说：

> 记一个决定：以主清单 A 为准，参照件 B 只作参照。

模型在同一 Run 里连发 **7 次**格式完全正确的 `task_scope_update`：`outcome=mutate`、
`base_revision`、`evidence_refs=[该用户消息的 evidence id]`、`idempotency_key`、
`operations=[decision.record …]` 一应俱全。SDK 执行库里这个 Run 有 9 条
`audit.tool.v1 operation_name=task_scope_update`（`durable_seq` 75/98/108/131/154/177/200/223/246），
每一条都被 Host handler 以 `task_scope_update_nothing_to_close` 拒绝；Run 最终
`run.failed code=driver_failed`。

原因很直接：那一轮**没有任何 material 事件**。design-freeze §2 的映射表把 `host.turn`
定为 trivial，所以 `dirty_state(scope)` 为空、没有 pending receipt，
`task_scope_mutation.py` 的 `require_dirty` 分支就把整个计划判为「没有可关闭的东西」。

同病的还有第 7 轮的 `goal.set` 和第 17 轮那条 18 KiB 逐字 `goal.set`——后者正是验收项
**A6-5**（README/STATUS 视图超限拆分）唯一的触发手段（`plans/2026-09-08-hm-to-a6/00-PLAN.md`
第 105 行 T17）。也就是说，这条规则按现状会让 A6-5 永远不可达。

### 1.2 契约怎么说

| 出处 | 原文要点 |
|---|---|
| design-freeze §2（`increments/2026-09-02-s5b-effect-closure-memory/design-freeze.md`） | `dirty_state(scope)` = 最后一条 closing receipt 水位之后的 **material 事件**集合；`host.turn` 是 **trivial** |
| design-freeze §7 | handler 拒绝码 `task_scope_update_nothing_to_close`（**无脏/pending**，不递增 revision） |
| challenge synthesis / plan.md:184 ① （`task-scope-update-projectless-safe-vs-dirty-exposure`） | 「只在脏/pending 时」**由"工具可见性"改为 handler 级门**——因为 SDK 对不可见工具的调用是致命异常而非拒绝 |
| acceptance HM-AC-7 | TaskScope 以 raw evidence、append-only event ledger、canonical state 与 immutable checkpoint revisions 为**事实层**，并物化 README/PLAN/STATUS/DECISIONS/RESUME/EVIDENCE 六个可追溯阅读视图 |
| `plans/2026-09-08-hm-to-a6/00-PLAN.md` T7 / T10 / T17 | 预期证据分别是 `task_scope_events` +1 与 `task_scope_read_view_revisions` 新 revision、`DECISIONS` 视图含该决定、README/STATUS 进入 bounded 形态 |

关键在第三行：这条门的**来历**是「工具只在脏/pending 时暴露」。它之所以变成 handler 门，
纯粹是因为隐藏工具被调用在冻结 SDK 里是整 Run 故障，只能改成拒绝码——它从来不是
"谁有权写 canonical 档案"的授权规则，而是 effect-closure 循环的触发条件。

两个佐证：

1. design-freeze §2 把 `host.turn` 定为 trivial 的**目的**是"普通对话不得逼出一次收口"
   （否则每轮都要收口、都要走兜底 provider 调用），不是"用户口述的目标/决定不得入档"。
2. Host 自己的 typed 路由 `deskpet/memory/human_memory_service.py`（`_apply` → 
   `self._scopes.apply_mutation_plan(plan)`，约 752 / 802 行）把**同一批 operation**
   （`goal.set`/`decision.record`/…）写进同一个 canonical store，**完全没有脏检查**。
   同一份档案，UI typed 路由写得进去、模型转述用户原话写不进去，这不是不变量，是不一致。

而 `task_scope_update` 是模型侧**唯一**的档案写入路由：`deskpet/task_scope/protocol.py`
的 `_MUTATION_KINDS` 就是全部词汇表，全仓 `apply_mutation_plan` 的调用点只有
closure handler、closure 兜底与 Host typed 路由三处。

### 1.3 选项与取舍

| 选项 | 判定 |
|---|---|
| **(1) mutate 计划自带 material 性，`require_dirty` 只管 no_mutation / effect-closure** | **采纳**。契合门的来历（可见性条件而非写入授权）、契合 HM-AC-7 的事实层定位、与 Host typed 路由口径一致、让 T7/T10/T17 与 A6-5 可达 |
| (2) 保留规则，让模型改用别的方式记决定 | 否决。**没有别的方式**——模型侧不存在第二条档案写入路由，且 A6-5 必须重新设计。代价与收益完全不成比例 |
| (3) 把当前 USER evidence ref 算作脏 | 否决。`dirty_state` 同时驱动**终态门**（`record_sdk_terminal` 要求 material 事件被 receipt 覆盖，否则 `foreground_terminal_closure_pending`）与**兜底 provider 调用**。把用户轮算作 material，等于**每一轮**都变脏、都要收口、都可能触发一次额外 provider 调用——把一个局部拒绝换成全局的成本与语义污染 |

关于「是否只放开 user-canonical 的那几个 kind」：考虑过按 kind 白名单（goal/decision/plan/status）
放行，最终**不采用**。理由是它挡不住任何真实风险：`task.complete` 在有无关脏事件时本来就能调用，
脏与不脏跟"是否该完成"毫无关系；防止过早/缩小完成的守卫是迁移表 `_check_transitions` 与收口指令文本，
不是脏闸。多一张需要维护的冻结子集清单，换不到任何新保证。因此规则取最诚实的形式：
**`mutate` 就是变更本身**。

### 1.4 落地

`backend/deskpet/sdk_adapters/task_scope_mutation.py`

```python
if require_dirty and applied_before is None and str(payload["outcome"]) != "mutate":
    dirty = await dirty_state_tx(db, task_scope_id)
    pending = await pending_receipts_tx(db, task_scope_id)
    if not dirty.is_dirty and not pending:
        raise ClosureRejected("task_scope_update_nothing_to_close", accepts=_NOTHING_TO_CLOSE_GUIDANCE)
```

* 检查顺序、其余全部拒绝码（`scope_unbound` / `payload_invalid` / `refs_outside_scope` /
  迁移表 / `mutation_base_revision_conflict` / 幂等 replay）、audit 写入点、`require_dirty=False`
  的兜底路径——**一字未动**。
* 新常量 `_NOTHING_TO_CLOSE_GUIDANCE` 进入拒绝 message（handler 已有
  `message += " " + canonical_json(detail)` 的既定渲染），内容：缺的是"上次收口以来的 material
  事件（写文件 / shell / 测试 / project-effect 工具）或 pending receipt"，而想记录用户所述应改用
  `outcome=mutate` + 对应 operations（goal.set / goal.revise / decision.record / plan.step.* /
  task.* / resume.update），并明说 mutate 在干净档案上会被受理。
* 模块 docstring 的 handler 顺序第 3 条同步改写并指向本备忘录。

**冻结文本待修订（未做，留痕）**：design-freeze §7 那半句「无脏/pending」按字面已不再等价于实现，
应改为「`no_mutation` 且无脏/pending」。该文件在 memory-sdk 仓、本次任务口径为**只读**，故此处留痕，
由 program 侧下次改 design-freeze 时一并修。工具 description（`TASK_SCOPE_UPDATE_DESCRIPTION`
里"only after real project effects happened"）同样与新语义不符，但描述字符串归另一子代理，本次**不动**，
在此点名交接。

---

## 2. 事件 D：一次畸形工具调用直接判死整个 Run

### 2.1 现场

同一 Run：

```
product_provider_response_parse_failed … diagnostic={"arguments_length":42,
 "check":"tool_call_arguments_not_json","json_error_kind":"expecting_delimiter",
 "json_error_position":10,"finish_reason":"tool_calls"}
→ sdk_run_driver_failed ProviderProtocolError → run.fail
```

`finish_reason=tool_calls`、arguments 只有 42 字节 → 不是截断，是模型这一次采样把工具入参序列化坏了。
Host 已有的确定性修复 `_repaired_tool_arguments`（commit `9323126f`）只救得了"多吐一个 `}`"那两种形态；
这一种救不回来，于是 `ProviderProtocolError` 上抛。

### 2.2 为什么这会杀死整个 Run，以及为什么现有重试车道够不着

SDK `execution/dispatch.py:216-223` 的 `_DEFINITE_PROVIDER_FAILURES` **包含**
`ProviderProtocolError`。落进这一档的异常走 `settle_failed`，不进 UNKNOWN 账本、不挂
wait-blocker——因此 F06 那条重试车道（`reconcile_incomplete` → `CONFIRMED_NOT_STARTED` →
`reauthorize_provider_not_started` 一次 rehandoff，见
`plans/2026-09-07-native-main-journey/DECISION-PROVIDER-TIMEOUT-STALL.md` §2.1/§4）
**永远看不到它**：那条车道只观察 UNKNOWN 记录。

结论：SDK 冻结、reconciliation 层够不着，修法只能在 Host 自己的 provider 适配层。

### 2.3 决定：重采一次，且只在这一种检查上

判据：与 F06 同源的"客户端拿到的这次采样对用户不产生可消费效果，重来一次是安全的"。
更强的一点是——F06 重发的是**未知是否已送达**的请求（最坏重复计费一次未被使用的补全），
而这里第一份样本**确定已经作废**（工具调用没被解析出来，SDK 从未派发任何 effect），
所以"无双重副作用"不是策略断言，是结构事实。

层次选择：

* **不改 SDK**（冻结）；
* **不放在 `_ProductOpenAICompatibleProvider._post_once`**：那是 SDK 私有方法的重写，
  且拿不到"这次失败属于哪种检查"的清晰边界；
* **放在 `ProductProviderAdapter.invoke`**：Host 自有类、SDK coordinator 之前的最后一帧、
  已经承载 `product_provider_attempt_*` 审计行，而且同一个缺陷类的确定性修复本来就归它这一层。
  它仍在 SDK `invoke` 的 cancel 竞速之内，取消语义不变。

实现（`backend/deskpet/sdk_adapters/provider.py`）：

1. 新增 `_ToolArgumentsProtocolError(ProviderProtocolError)`（`__slots__ = ()`，
   `error_code`/`retryable` 全部继承，公开分类学零变化）。`_parse_response` 的失败分支在
   已经算出的诊断 `check == "tool_call_arguments_not_json"` **且**原异常确实是
   `ProviderProtocolError` 时改抛这个子类；其它一律原样上抛。
2. 新增 `_invoke_with_protocol_resample`：最多 `_MAX_PROVIDER_PROTOCOL_ATTEMPTS = 2` 次，
   无退避，**同一个 `ProviderRequest`（同 request_id、同请求体）**，只捕获该子类；
   `attempt >= 上限` 或 `cancel.is_cancelled` 时原样上抛。
3. 审计行 `product_provider_protocol_resampled request_ref=… attempt=… max_attempts=…
   check=tool_call_arguments_not_json elapsed_ms=…`——只有不透明 ref、有界枚举与整数，
   **无载荷、无密钥**。第二次失败仍由既有 `product_provider_attempt_failed
   stage=response_protocol` 记录，随后 SDK 照旧判 Run 失败。

账本影响：重采发生在**一次 SDK hand-off 之内**，`provider_invocations` 仍是一条记录、一个结局，
`handoff_attempt` 与 `rehandoff_count` 不变。代价是最坏多计费一次被丢弃的补全——与 F06 §5 已接受的
同一笔账。超时预算方面：能走到这条分支说明 200 响应已经收到，第一次尝试并未耗掉 transport 超时。

---

## 3. 测试

| 文件 | 用例 | 证明 |
|---|---|---|
| `backend/tests/sdk_adapters/test_task_scope_update_clean_scope.py`（新增，4 例） | `…user_stated_decision_and_goal_are_admitted_on_a_clean_scope` | 干净档案（已收口、零 material、有 evidence 链接）上 `decision.record` 落 canonical，`goal.set` 含 >16 KiB 逐字目标同样受理，revision 逐次 +1 |
| | `…no_mutation_on_a_clean_scope_is_rejected_and_says_what_would_count` | 仍是 `nothing_to_close`、不递增 revision、不写 receipt；理由文本同时含 `outcome=mutate` 与 goal.set/decision.record/plan.step./resume.update |
| | `…no_mutation_still_accepted_while_dirt_or_pending_exists` | 反向：脏闸没被拆掉，有 material 事件时 `no_mutation` 正常收口 |
| | `…clean_scope_keeps_every_other_guard` | 干净档案上 `payload_invalid` / `refs_outside_scope` / `mutation_base_revision_conflict`（可重试）/ 幂等同 receipt / `after_complete` 逐条原样生效 |
| `backend/tests/sdk_adapters/test_task_scope_update_tool.py`（改 1 行） | `test_handler_rejection_codes_and_audit_rows` | 原来用 `mutate` 触发 `nothing_to_close`，改用 `no_mutation`（该断言的本意就是"无可关闭内容"） |
| `backend/tests/sdk_adapters/test_provider_tool_arguments_repair.py`（新增 8 例） | `…raise_the_resampleable_protocol_error` / `…not_resampleable`（3 参数） | 只有 `tool_call_arguments_not_json` 改抛子类；`error_code`/`retryable` 不变 |
| | `…resampled_once_and_the_run_survives` | 恰好 2 次物理请求、两次请求体**逐字相同**、第二份样本正常返回、审计行 1 条且不含引文/入参/密钥、无 `attempt_failed` |
| | `…second_malformed_sample_still_fails_the_run_and_never_sends_a_third` | 第二次仍失败 → `ProviderProtocolError`；**恰好 2 次**、绝无第三次；`stage=response_protocol` |
| | `…wellformed_response_is_never_resampled` / `…repairable_defect_is_repaired_in_place_without_a_second_call` / `…other_protocol_failure_is_not_resampled_end_to_end` | 合法响应、可确定性修复的响应、其它协议缺陷一律 1 次请求 |
| | `…cancelled_turn_is_not_resampled` | 用户已停止时不补采 |

**反证（确认非空洞）**：把 `require_dirty` 条件改回原样，C 的两个正向用例失败；把
`_MAX_PROVIDER_PROTOCOL_ATTEMPTS` 改成 1，D 的两个端到端用例失败。改回后全绿。

**运行结果**（`backend/.venv`，`PYTHONPATH=backend`）：

* `tests/sdk_adapters/test_task_scope_update_clean_scope.py` 4 passed
* `tests/sdk_adapters/test_provider_tool_arguments_repair.py` 43 passed
* 触及模块的 importer 集合（`test_closure_request_guard` / `test_closure_resume_sources` /
  `test_completed_scope_guidance` / `test_completed_scope_workspace_continuation` /
  `test_model_short_outbound` / `test_primary_dynamic_resume_visibility` /
  `test_primary_history_outbound` / `test_foreground_fifo_closure` /
  `test_context_route_nonstrict_wire` / `test_context_route_nullable` / `test_objective_events` /
  `test_primary_provider_preflight` / `test_product_host_ports` / `test_provider_projection_pump` /
  `test_provider_rejection_diagnostic` / `test_provider_timeout_is_a_safety_net` /
  `test_provider_tool_call_continuation` / `task_scope/test_canonical_archive`）
  135 passed / 3 failed，**三条全部与改动前逐字相同**（`test_late_history_denial…[sent_unknown]`
  在既定忽略清单内；`test_objective_events` 两条经 stash 对照确认为基线既红）。
* `test_primary_foreground_runtime` + 6 个 memory importer：40 passed / 10 failed，
  经 stash 对照与基线**逐条一致**。
* `test_task_scope_update_clean_scope` + `test_provider_tool_arguments_repair` +
  `test_task_scope_update_tool` 合跑：49 passed / 3 failed（3 条全在
  `test_task_scope_update_tool`，基线同样红）。
* `test_post_turn_invoker` / `test_effect_gate_replay` / `test_effect_gate_hardening`：
  21 passed / 8 failed（8 条全在 `test_effect_gate_hardening`，属既红族）。
* `test_provider_projection_pump` 全绿；`test_typed_context_use_primary` 9 failed（既定忽略清单）。
* `test_composition.py`：7 条失败经 stash 对照与基线**逐字相同**（startup/close/registration 类，
  与本次改动无关）；该文件另有一条 close-during-start 用例在本机长时间不返回——同一份代码在
  stash 前后表现一致，且本机当时有另一代理的三个全量 pytest 在并发跑，判为环境竞争而非本次回归。

**基线既红、与本次无关**（stash 对照确认）：`tests/sdk_adapters/test_effect_gate*.py`、
`test_s5b_acceptance_matrix.py`、`test_s5b_milestone_effect_closure_memory.py`，以及
`test_task_scope_update_tool.py` 的 3 例——共同根因是 `s5b_effect_gate_harness` 里
脚本化 `context_route` + `write_file` 已经走不到执行器（`env.effects.write_file_calls` 为空、
handler 拿到 `task_scope_update_scope_unbound`），与事件 A 同一片区域。
**正因如此，事件 C 的回归用例没有建在那个基座上**，而是直接驱动 Tool 路径与兜底路径共用的
`TaskScopeUpdateService.apply_closure(require_dirty=True)`，基座换成仍然可用的
`s5b_closure_harness`（真实 state.db + 真实 ForegroundQueueStore / ExecutionEvidenceIngress /
CanonicalTaskScopeStore）。

---

## 4. 自审

* **只放开了该放开的**：C 的改动是一个合取项 `and outcome != "mutate"`；检查顺序、拒绝码集合、
  audit 行、`require_dirty=False` 的兜底路径均未触碰，并由
  `test_clean_scope_keeps_every_other_guard` 逐条钉死。
* **收口水位不受污染**：干净档案上的 mutate 写出的 receipt，`closure_watermark` 仍取该 decision
  自身的 revision watermark（与 apply 同事务），只覆盖 ≤ 该水位的事件；`UNIQUE(sdk_run_id,
  closure_watermark, outcome)` 不受影响。
* **未新增"覆盖并发 Run 事件"的风险面**：干净档案上的 mutate 与任何一次既有 mutate 走同一条
  receipt 写入路径，watermark 取自身 decision 的 revision watermark（Task 3 审查 F-1 的既定设计），
  同 scope 并发 Run 的覆盖问题（见 `verification/code-review-task3.md` 第 69 行对
  `force_close_pending` 的记载）机制未变、未被放大；且前台队列对同一 subject 的主对话 Run 本就串行。
* **模型循环有终点**：同 `idempotency_key` 同载荷 → 幂等 replay 返回同一 receipt；
  换 key 但 `base_revision` 过期 → `mutation_base_revision_conflict`（可重试，会让模型重读）。
  实跑第 10 轮那种"同一载荷连发 7 次"在新口径下第一次就成功，其余为 replay。
* **D 未扩大重试面**：唯一的判据是 `_parse_failure_diagnostic` 已经算出的 `check` 字段，
  且要求原异常确实是 `ProviderProtocolError`；超时/传输/HTTP 状态/其它响应缺陷全部按原路径走。
* **D 不破坏取消与账本**：重采仍在 SDK `invoke` 的 cancel 竞速之内，且显式检查
  `cancel.is_cancelled`；重采在一次 hand-off 内，账本仍是一条记录一个结局。
* **日志隐私**：新审计行只有不透明 `request_ref`、整数与固定 slug，用例逐一断言引文、入参原文与
  API key 都不出现。
* **已知遗留**：① design-freeze §7 与 `TASK_SCOPE_UPDATE_DESCRIPTION` 的措辞待同步（见 §1.4，
  分别归 program 侧与描述子代理）；② `s5b_effect_gate_harness` 基线既红未修（属事件 A 范围）；
  ③ 本次未做原生复跑，事件 C/D 的真实闭环需在 A–D 全部合入后按 `RUN-01-ATTEMPTS.md`
  的条件跑满 24 轮才算正式判定。
