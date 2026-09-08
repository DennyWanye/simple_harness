# 决策：历史因果组补回 assistant.tool_calls 的真实入参（HM-TO-A6 F-K1）

日期：2026-09-08
工作树：`.claude/worktrees/history-args`（分支 `worktree-history-args`，基线 `26395f01`）
前置裁决：[DECISION-TOOL-CALL-ARGUMENTS-REPLAY.md](DECISION-TOOL-CALL-ARGUMENTS-REPLAY.md) §5（F-K1）、§5 末（F-K2）

---

## 1. 现象与根因

同一 Run 内的跨跳重放已由 `26395f01` 的进程内备忘把真实入参贴回线体。**跨轮**则不然：
上一轮的对话以 `historical_causal_group` 的形式、被引号包进一条 user 消息进入下一轮请求，
其 assistant 条目来自 `composition.py::project_primary_transcript`，只产出
`{"role","content"}`——工具调用的 `name`/`arguments` 在投影这一步就丢了。模型读到的是
「assistant 说了 X，然后出现了一个工具结果」，看不见「它当时是拿什么参数调的」。

三个硬约束决定了修法不能是「往 transcript 里加字段」：

1. `primary_message_v2.representable` / `item_ordinals` 硬性要求 assistant 条目键集恰为
   `{"role","content"}`，逐条消息 S1 与会话登记按此形状复算；
2. 已归档的 `primary_run_terminal` envelope hash 覆盖 `messages` 的精确形状，改形状即让
   全部历史组不可验证；
3. `primary_history.transcript_matches` 语义不得变（任务硬约束）。

## 2. 裁决：旁路记录放 Host state（v55），不进 evidence payload

用户授权由本代理裁决技术取舍。两个候选：

| 候选 | 结论 | 理由 |
|---|---|---|
| (a) 把 `tool_calls` 写进终态 evidence payload（新契约 `primary-message-v4`） | **否决** | 旧行 hash 必然失效、不能回填（S1：终态一经归档不改）；`representable`/`pairs`/`conversation_registration`/`primary_context_pages` 四处按现形状逐字节复算，属于要与 Memory SDK 协同的契约升级（F-A6-1 同类），不是本次能单独打的补丁 |
| (b) Host state 里的**内容寻址旁路记录**，按 `(sdk_run_id, message_ordinal)` 键入，绑定到它所属的那一条终态观察 `(evidence_id, envelope_hash)`，投影时 join | **采纳** | envelope 一字不改；旧归档没有旁路行就按今天的样子渲染；数据源是 SDK **公共** provider 记录（`provider_invocations.response_json`，`MEMORY_SDK_BOUNDARY` 口径：Host 只读公共 port，不开 SDK 表）；旁路行是「enrichment」不是「authority」——这与 S1「缺失 metadata 的 evidence 永久保存但不入短时域」是同一种分层 |

**数据来源与信任链**：`primary_tool_causality.read_tool_causal_sources` 本来就在终态观察时
逐条读取已落定的 provider invocation（`audit_hash(raw) == op.result_hash`）、用
`provider_response_from_json` 反序列化，并把 `response.tool_calls[i]` 与每个 effect 的
`raw_call_id`/`tool_name`/`turn_ordinal`/`call_ordinal` 逐项对上，最后整份 transcript 复投影
比对（`whole_transcript_mismatch`）。**入参就在 `response.tool_calls[i].arguments` 里。** 本次把
这一趟已验证的读取扩成 `read_tool_causality`，多返回一份按 assistant 条目序号键入的
`tool_calls` 投影；`read_tool_causal_sources` 保持原返回、逐字节不变。不新增任何 SDK 读法。

## 3. 设计

### 3.1 表（`primary/047_primary_assistant_tool_calls_v55.sql`，user_version 54 → 55）

```
primary_assistant_tool_calls(
  record_id PK = "primary-assistant-tool-calls:<sdk_run_id>:<ordinal>",
  sdk_run_id, host_run_id, message_ordinal (>1),
  observation_evidence_id, observation_envelope_hash(64),
  body_json, body_hash(64), recorded_at,
  UNIQUE(sdk_run_id, message_ordinal))
```
不可 UPDATE/DELETE（触发器），并以 taxonomy `A` 登记进 `human_memory_recovery_table_registry`
（恢复围栏触发器与 v53/v54 表同款）。`message_ordinal` 是 1-based transcript 序号，
`ordinal=1` 永远是 USER 原文，故 CHECK `>1`。

### 3.2 body（`primary_assistant_tool_calls/v1`）

```
{schema_version:1, kind, sdk_run_id, host_run_id, message_ordinal,
 provider_invocation_id, provider_turn_ordinal, provider_response_hash,
 tool_calls:[{call_id, name, arguments:<canonical JSON 字符串，已 redact_credential_shapes>}]}
```
`body_hash = canonical_hash(body)`。`provider_response_hash` 与终态 `tool_causal_sources[*]`
的同名字段一致，可回溯到同一条 provider 响应。写前过 `reject_private_payload`；不过关的条目
**跳过并记只含标识符的告警**（`primary_assistant_tool_calls_skipped`），绝不让终态提交失败。

### 3.3 写入（`primary_history.record_terminal_observation(assistant_tool_calls=...)`）

与终态观察**同一事务**、在 envelope 与逐条消息 S1 之后写入，绑定刚提交的
`(evidence_id, envelope_sha256)`。只在 `terminal == COMPLETED` 且 `user_version == 55`。
**既有观察分支永不回填**（与 `primary_message_v2` 的「Legacy terminals are never backfilled」同口径）。

### 3.4 读取与三处一致的 join

- `PrimaryHistoryStore.read`：命中终态观察后 `read_assistant_tool_calls_tx(...)`，有行才在组上
  加 `assistant_tool_calls: {ordinal: [...]}`；**没有工具调用的 Run 组形状一字不变**。
- `primary_context_pages._source_group`：page-in 的源重建做同一个 join，因此
  `verify_history_projections` 从真实源复算出的投影与 start snapshot 里的逐字节一致。
- `project_history_group`：assistant 条目在 map 里则渲染为
  `{**item, "tool_calls":[{call_id, name, arguments}]}`；单条 `arguments` 超过
  `DEFAULT_LARGE_RESULT_BYTES`（16 KiB）时改为确定性摘要
  `primary_tool_arguments_summary_v1{excerpt(1024B), source_hash, content_bytes}`——与超大
  tool_result 的处理同构，避免一次大写入在此后每一轮都占满预算。

读取校验：`record_id` 形制、`body_hash`、body 内 `sdk_run_id/host_run_id/message_ordinal`
与行一致、`kind/schema_version`，以及**与归档 transcript 的一致性**：该序号必须是 assistant
条目，其后紧跟恰好 `len(tool_calls)` 条 tool 条目且 `call_id`/`name` 逐一相同（这是
`read_tool_causality` 证明过的形状，join 时按归档重查而不是信行）。**任一行不过 → 整组退回
无 `tool_calls` 的旧渲染并记只含标识符的告警**，不抛异常：A6 事故已经证明「一条读失败让整条主
对话永死」比信息缺失糟得多；旁路记录是对已验证归档的增补，不是它的权威。

### 3.5 指纹与形状稳定性

- 无工具调用的 Run：组字典键集不变、`ContextLineage.source_hash` 不变、provider 请求线体不变
  （专测 `test_turn_without_tool_calls_keeps_its_group_shape_and_request_bytes`）。
- 旧归档 / v49–v54 库 / `primary-terminal:` 回退源：无旁路行，渲染与修复前逐字节相同
  （专测 `test_archive_without_side_table_renders_exactly_as_before`）。
- `transcript_matches` 未改动；`project_primary_transcript` 未改动；`representable`/`item_ordinals`
  未改动；所有 envelope hash 未改动。

## 4. 覆盖面与明确不做

- **只有具备已验证 tool causality 的终态（`primary-message-v2/v3` 契约）才会有旁路行。** 它们与
  `tool_causal_sources` 来自同一趟读取；读取不成立（`PrimaryToolCausalityUnavailable`，例如 Host
  effect 身份索引缺失）时终态降级 v1，两者一起为 None。这正是生产路径（`ProductEffectExecutor` +
  `primary_effect_identities`）；非 dynamic 的测试夹具走不到这里，所以运行时专测用 `dynamic=True`。
- 被拒（effectless denial）的调用：与该轮其他调用同在一条 assistant 记录里，入参照记——模型能看到
  「我当时用这些参数调了、被拒了」。
- **F-K2（reasoning 标记）本次不做，理由**：SDK 明令 hidden reasoning 不得进入 durable response state
  （`provider_invocations.py:259`），durable 公共记录里**没有**「这轮有过推理」的事实；
  `opaque_continuation_ref` 只是续接引用（夹具无推理也带它），拿它当「有推理」标记是不诚实的。
  唯一知道推理存在的是进程内 provider 路径，而它不是耐久事实。保持 followup，待 SDK 把 reasoning
  存在性作为公共字段暴露后与本旁路记录同表加一列。
- 上游正解不变：SDK 把 assistant `tool_calls` 当一等公共 transcript 字段后，`project_primary_transcript`
  直接产出它，本旁路表与 join 一并退役。

## 5. 迁移链

`primary_tool_call_schema.py` 形制对齐 `procedure_recovery_schema.py`：先确保 v54，再在
`BEGIN IMMEDIATE` 内校验 `current == 54`、cutover 前置（无活跃前台 Run / 无 WAITING）、恢复围栏
OPEN，执行脚本、登记恢复表、写 `human_memory_migration_chain` + `schema_migrations`、
`PRAGMA user_version=55`，`fault_inject("primary_tool_call.migration.before_commit")` 后 commit。
链上每个「显式后继」白名单同步加 55：`s5c_schema`/`s5c_timer_schema`/`s5c_terminal_schema`/
`procedure_schema`/`procedure_recovery_schema`（新增 `_expected_user_version` 参数）/
`prospective_signal_store`/`schema._validate_composed_extension`（55 → 本校验器）/
`dispatch_startup_epoch`、`initialize_state_db` 的 `maximum_human_schema_version=55`/
`primary_history` 的 v3 契约门 `(53, 54, 55)`。`main.py` 在 v54 安装之后调用
`initialize_primary_tool_call_state_db`。v56 由后继者按同一套显式白名单继续。

## 6. 实现文件

| 文件 | 变更 |
|---|---|
| `backend/deskpet/memory/migrations/primary/047_primary_assistant_tool_calls_v55.sql` | 新增：表、索引、不可变触发器 |
| `backend/deskpet/memory/primary_tool_call_schema.py` | 新增：v55 校验/安装 |
| `backend/deskpet/execution/primary_tool_calls.py` | 新增：body 构造、transcript 一致性、写/读（同事务） |
| `backend/deskpet/memory/primary_tool_causality.py` | `read_tool_causality` 返回 `(sources, assistant_calls)`；`read_tool_causal_sources` 成为其 `[0]` 包装 |
| `backend/deskpet/sdk_adapters/composition.py` | 新增 `read_primary_tool_causality`；`read_primary_tool_causal_sources` 委托之 |
| `backend/deskpet/execution/foreground_runtime.py` | 终态观察优先用合并读取，把 `assistant_tool_calls` 交给 `record_terminal_observation`；旧栈无此方法则走原路 |
| `backend/deskpet/execution/primary_history.py` | `record_terminal_observation` 新增 `assistant_tool_calls`；`PrimaryHistoryStore.read` join；v3 门加 55 |
| `backend/deskpet/execution/primary_context_pages.py` | `project_history_group` 渲染 `tool_calls`（含超大入参摘要）；`_source_group` 同一 join |
| `backend/deskpet/memory/{schema,s5c_schema,s5c_timer_schema,s5c_terminal_schema,procedure_schema,procedure_recovery_schema,prospective_signal_store}.py` | 显式后继白名单加 55 |
| `backend/main.py` | 启动安装 v55 |
| `backend/tests/memory/test_primary_tool_call_schema.py` | 迁移：原子发布/故障回滚/幂等/全链校验器接受/不可变与围栏/未来版本拒绝 |
| `backend/tests/execution/test_primary_history_tool_calls.py` | 真实运行时：两次工具调用的一轮 → 下一轮请求引号组里 assistant 带 `tool_calls`（含中文入参）、旁路行绑定到归档终态、`_source_group` 同 join；无旁路表回退；无工具调用形状不变；损坏/异源行降级；超大入参摘要；凭据脱敏 |

## 7. 测试与回归

解释器 `backend/.venv/bin/python`（Memory SDK 0.6.31），`PYTHONPATH=<worktree>/backend`
（已核对 `deskpet.__file__` 指向本工作树）。单进程串行跑。

### 7.1 新增用例（10 passed）

`backend/tests/memory/test_primary_tool_call_schema.py`（3）：

- `test_v55_atomic_publication_and_every_prior_validator_accepts_it`：commit 前故障注入 →
  user_version 仍 54、无表、迁移链一字未动、库文件冻结比对相等；正常安装后**重复调用幂等**、
  v54 初始化器接受其后继、恢复表登记只多出本表且 taxonomy=A、迁移链新增 55 行、
  `validate_s5c_timer_runtime_state_db` / `ProspectiveSignalStore` / `dispatch_startup_epoch`
  全链接受，`decision.user_version == 55`。
- `test_v55_rows_are_immutable_and_fenced`：UPDATE/DELETE 触发器拒绝；
  `UNIQUE(sdk_run_id, message_ordinal)` 拒绝重复；`CHECK(message_ordinal>1)` 拒绝 ordinal=1；
  拆掉恢复围栏触发器后校验器报错。
- `test_v55_rejects_a_future_database_and_a_non_v54_base`：user_version=56 与非 54 基线均拒绝。

`backend/tests/execution/test_primary_history_tool_calls.py`（7，真实 SDK ReAct/SQLite 运行时，
复用 `test_primary_foreground_runtime.build(..., dynamic=True)` + `settled_run_reader`）：

- `test_second_turn_quotes_the_prior_tool_calls_with_their_real_arguments`：一轮里两次真实工具
  调用（一次成功、一次被拒），下一轮请求的引号组里 assistant 条目带
  `tool_calls=[{call_id,name,arguments}]`（含中文入参逐字）；**归档 transcript 的 assistant 条目键集
  仍恰为 `{role,content}`**，终态 evidence 全文不含 `tool_calls`；旁路行绑定到该终态的
  `(evidence_id, envelope_hash)`、`body_hash` 可复算、`provider_response_hash` 与终态
  `tool_causal_sources[0]` 同源；`_source_group` 的 page-in 源重建做出**完全相同**的 join 与投影。
- `test_archive_without_side_table_renders_exactly_as_before`：不装 v55（库停在 49）跑同样两轮，
  组里无 `assistant_tool_calls`、引号组与归档 `messages` 逐字相等、全文无 `tool_calls`。
- `test_turn_without_tool_calls_keeps_its_group_shape_and_request_bytes`：装了 v55 但两轮都无工具
  调用 → 组字典键集与修复前完全一致、旁路表 0 行、请求线体不含历史引号块。
- `test_corrupt_or_foreign_side_rows_degrade_to_the_prior_rendering`：换 envelope hash / 换
  evidence id 读不到；与归档 transcript 不一致（工具名不符、条目不足）→ 整组退回旧渲染；
  body_hash 被篡改、record_id 与 (run, ordinal) 不符 → 整组退回；**告警只含标识符，不含入参正文**。
- `test_projection_quotes_arguments_verbatim_and_summarizes_oversized_ones`：超 16 KiB 入参降级为
  `primary_tool_arguments_summary_v1{excerpt=1024B, source_hash, content_bytes}`、原文不出现在线体里；
  普通入参逐字；键不是 assistant 条目则不渲染（等于旧投影）；同输入同字节（确定性）。
- `test_side_record_body_redacts_credential_shapes_and_never_raises_on_them`：`Bearer sk-…` 形状被
  `redact_credential_shapes` 替换，`reject_private_payload` 通过。
- `test_a_second_write_for_the_same_ordinal_never_fails_the_observation`：同 `(run, ordinal)` 第二次
  写入被 append-only 唯一约束拒绝时，`write_assistant_tool_calls_tx` 捕获 `IntegrityError`、返回 0、
  只记标识符告警——**增补记录永远不能让归档提交失败**（自审新增，见 §8.4）。

### 7.2 回归（受影响文件，与 `git stash` 基线逐条对比）

| 批次 | 结果 |
|---|---|
| `execution/test_primary_context_pages` + `execution/test_current_tool_pages` + `memory/test_primary_tool_causality` + `memory/test_procedure_schema` + `memory/test_procedure_recovery_schema` + `memory/test_composed_startup_extensions` | 24 passed, 1 failed |
| `execution/test_primary_foreground_runtime` + `execution/test_primary_history_outbound` | 34 passed, 2 failed |
| `memory/test_primary_visibility` + `memory/test_primary_tool_message_ingestion` + `operation_audit/test_terminal_identity` + `memory/test_procedure_recovery_runtime` + `memory/test_prospective_notice` | 69 passed, 1 failed |
| `memory/test_human_memory_api_and_fence` + `sdk_adapters/test_typed_terminal_recovery` | 4 passed, 4 failed |

合计 **141 passed / 8 failed**，8 条全部为既有红：

- `test_composed_startup_extensions::test_unknown_successor_is_rejected_even_with_valid_older_markers`
  ——**基线 stash 后同样失败**（该用例把 v52 库的 user_version 改成 53 后期待 `future`，而 53 早在
  本次之前就已是链上已知版本，实际报 `human_memory_marker_chain_invalid`；与 55 无关）；
- `test_primary_foreground_runtime::test_primary_none_routes_to_exact_task_and_writes_real_file[True]`
  ——**基线 stash 后同样失败**（20s `wait_for` 超时）；
- `test_primary_history_outbound::test_late_history_denial_is_failed_while_sent_ambiguity_stays_unknown[sent_unknown]`、
  `test_primary_visibility::test_real_memory_only_forget_cold_user_and_terminal_no_host_rewrite`
  ——任务交接里点名的既有红；
- `test_typed_terminal_recovery` 4 条——**基线 stash 后同样 4 failed / 0 passed**。

未做全量 `tests/execution` 扫描（超出交接给定的 ~20 分钟预算）；已知红清单里的短索引 embedder、
s5b/effect_gate/s5a、scope_disclosure、typed_context_use_primary、`/Users/denny` 路径类未触碰。

## 8. 自审

### 8.1 旧行的 envelope hash 未变

`record_terminal_observation` 里旁路写入位于 `prior is None` 之后、`committed = append_evidence_tx(...)`
**之后**，payload 在 `evidence_pair` 前就已定稿；`assistant_tool_calls` 从未进入 payload、
`bound_terminal_payload`、`reject_private_payload` 或 `evidence_pair` 的任何输入。既有观察分支
（`prior is not None`）原样返回、不写任何旁路行，**旧归档不可能被回填**。
`test_second_turn_…` 断言归档 assistant 条目键集仍是 `{role,content}` 且终态 payload 全文无
`tool_calls`，`test_archive_without_side_table_…` 断言无表时投影逐字不变。

### 8.2 没有超出「模型本来就能看到的入参」的载荷

渲染进线体的只有 `{call_id, name, arguments}`。`arguments` 就是同一轮 assistant 自己发出的、
已经在**当轮**线体里出现过的入参（`26395f01` 的进程内备忘保证当轮逐字回显），本次只是让它
跨轮存活。body 里多存的 `provider_invocation_id` / `provider_turn_ordinal` /
`provider_response_hash` **只用于溯源校验，不进入任何投影**。写前 `redact_credential_shapes`
+ `reject_private_payload`；读失败与写跳过的告警只打 `sdk_run_id` / `message_ordinal` / 原因码，
专测断言入参正文不出现在日志里。数据源全部是 SDK **公共** port（`provider_invocations` 已结算
响应 + `execution_effects`），没有新增任何 SDK 私有读法，也没有新增 grant。

### 8.3 迁移幂等且原子

`initialize_primary_tool_call_state_db`：`version == 55` → 只校验后返回（幂等）；`> 55` → 拒绝；
否则先 `initialize_procedure_recovery_state_db` 确保 v54，再在 `BEGIN IMMEDIATE` 内复核
`current == 54`（并发下若已被别的进程推到 55 则回滚 + 校验后返回）、校验 v54 完整性、
cutover 前置、恢复围栏 OPEN，然后脚本 + 恢复表登记 + 迁移链 + `schema_migrations` +
`PRAGMA user_version=55` **同事务提交**；任何异常整体 rollback。故障注入用例证明中断后库
逐字节不变。链上每个显式白名单（`s5c_schema`/`s5c_timer_schema`/`s5c_terminal_schema`/
`procedure_schema`/`procedure_recovery_schema`/`prospective_signal_store`/
`_validate_composed_extension`/`maximum_human_schema_version`）都是**枚举新增 55**，没有一处
放宽成 `>=`，v56 仍会被拒。

### 8.4 复审中改掉的一处

初稿里旁路 INSERT 未防 `IntegrityError`。虽然 `terminal_observation_tx` 的 prior 检查 +
`_validate_sdk_binding_tx` 让同一 `(sdk_run_id, ordinal)` 二次写入在生产路径上不可达，但一旦可达，
未捕获的 `IntegrityError` 会**让终态归档提交失败**——这与本设计「增补永不成为权威、更不能拖垮
归档」的第一原则直接冲突。已改为捕获并记标识符告警（SQLite 的语句级回滚保证外层事务完好），
并补 `test_a_second_write_for_the_same_ordinal_never_fails_the_observation`。

### 8.5 仍然成立的边界

- `transcript_matches`、`project_primary_transcript`、`primary_message_v2.representable/item_ordinals`
  **一行未改**；
- `read_tool_causal_sources` 返回值逐字节不变（现为 `read_tool_causality(...)[0]`），既有调用方形状不变；
- 旁路行损坏 → 整组退回旧渲染，**不抛**；但 page-in 的 `verify_history_projections` 会因投影与
  start 快照不符而拒绝该页——这是正确的严格方向（拒绝一页，而不是放行一个无法复算的引用）；
- F-K2（reasoning 标记）按 §4 保持 followup，本次不做。
