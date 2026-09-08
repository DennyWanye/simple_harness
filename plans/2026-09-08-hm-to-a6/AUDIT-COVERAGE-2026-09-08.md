# HM-AC-7 全操作审计覆盖核对（2026-09-08，尝试 3 / 尝试 4 证据）

> 目的：把 program 状态里的「普通对话审计与 driver 覆盖 ✅；全操作覆盖未验」
> （`simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/PROGRESS-2026-09-07.md:40`）
> 从"未验"推进为"按操作种类逐条核对过"。本文只记录覆盖事实与缺口，不改判 A6 各项。
> 分支 `worktree-audit-coverage`（基线 `26395f01`）。证据只读，DB 先复制再打开。

## 1. 做了什么

- 新增核对器 `backend/deskpet/quality/audit_coverage.py`（CLI 薄包装 `scripts/audit/audit_coverage.py`）：
  `--evidence <primary-ui-dir|userdata/data> [--json] [--markdown] [--fail-on-gap]`。
- 对每个操作种类：从执行账本枚举 observed → 按显式 join 键找"应有的审计记录" → 报 observed / audited / missing（含 id 前缀与原因）；
  同时标注该种类经哪个受控读取面可达，并扫描三个业务库（state / human_memory_v7 / execution）确认没有审计标识
  （`memory-attempt:`、`memory-request:`、audit_jobs.job_id、audit_pages.snapshot_hash、finding_id、audit_ref、read_ref）泄漏进普通面。
- join 键全部来自公开契约，不猜时间戳：
  - SDK 审计页 `operation_id = kind + ":" + sha256(canonical([kind, raw_id]))`（独立实现，测试与 `simple_harness.execution.audit.audit_reference` 比对）；
  - provider ↔ Host 结算：`invocation_id`；snapshot ↔ 发送：`request_fingerprint`；typed recall ↔ Host journal：`result_hash`（退回 `context.run_id` 的 digest）；
  - 分析：`request_hash`（Host attempt ↔ llm_invocations ↔ analysis_batches）+ `plan_id`（mutation receipts）+ `invocation_id`（decision_records）；
  - memory port：`audit_hash([intent_id, run_id, payload_hash, created_at])`；TaskScope：`source_event_id` 前缀 `execution:route:` / `mutation-plan:` / `execution:<run>`。
- 测试 `backend/tests/quality/test_audit_coverage.py`（7 个）：合成证据夹具每种类各含覆盖与缺口样本；SDK 哈希与 OA1 family 表漂移守护；审计操作集与普通面操作集不相交；CLI 出报告与 `--fail-on-gap`；证据只复制不改原件。

## 2. 契约要求的操作种类（枚举依据）

| 种类 | 契约来源 | 账本（observed） | 应有审计记录 | 受控读取面 |
|---|---|---|---|---|
| `run_terminal` 终态 Run | HM-AC-7；S6 T4 | `foreground_terminal_receipts` | `audit_jobs(enumerated)+audit_pages`；scope 内 `harness.run_terminal` | Host audit_pages（仅进程内 `AuditStore.inspect`） |
| `tool_effect` 工具 effect | HM-AC-7 tool 链路 | `execution_effects` | 审计页 effect head（失败须带 error_code）+ `primary_effect_identities` | 同上 |
| `provider_attempt` Provider 调用 | HM-AC-7 model/Token/费用 | `provider_invocations` | 审计页 provider head(usage) + `sdk_provider_attempt_audit`（Host 结算 token） | 同上 |
| `context_snapshot` ContextSnapshot | HM-AC-6 可审计快照 | `provider_invocations` | `run_context_snapshot_receipts`（fingerprint 同 run 匹配） | Host 账本 |
| `route_decision` 路由决策 | HM-AC-7 Recall need / TaskScope route | `context_route_decisions` | context_tool→`context_route_tool_invocations`+effect head；no_recall→`context.no_recall` 边界；scope→`harness.route_decision` | 审计页；scope 事件走 `task_scope.evidence_page`（执行面） |
| `context_page_in` page-in | HM-AC-6 受控引用 | effects(`context_page_in`) | effect head；Host 无持久回执 | 审计页 |
| `task_scope_search_open` | HM-AC-1 普通 search/open | effects(`task_scope_search`) | effect head + `task_scope_search_access_receipts` | 审计页 |
| `task_scope_mutation` | HM-AC-7 TaskScope ledger | `task_scope_mutation_attempts` | `task_scope_mutation_decisions` + `mutation.plan` 事件 | Host 账本 |
| `taskscope_event_ledger` | HM-AC-7 append-only ledger | `task_scope_execution_ingest_receipts` | `task_scope_events` | Host 账本 |
| `memory_analysis_apply` 分析 LLM 与 apply | HM-AC-2/7；S2 T4/T5 | `post_turn_invocation_attempts(analysis)` | `llm_invocations` + `analysis_batches` + `job_attempt_events` + receipts/decision_records | OA1 `job_transition,mutation_commit,mutation_rejection` |
| `typed_recall_foreground` / `typed_recall_analysis` | HM-AC-7 Recall；S2 T4 | `typed_recall_requests` + Host journal | `typed_recall_terminals` ↔ journal；raised 时 `memory_call_findings` | OA1 `typed_*`；journal 仅进程内 |
| `recall_context_use` 使用授权 | HM-AC-6/7 | `provider_context_use_receipt_bindings` | `recall_context_use_receipts` + provider head | OA1 `recall_context_use` |
| `short_horizon_recall` | HM-AC-7 | `short_horizon_audit(recall_started)` | 同 query 的 recall 终态 | OA1 `short_recall` |
| `forget_suppression` | HM-AC-1；S2 T3；HM-S7 | `memory_action_events`+effects(`memory_forget`) | `suppression_directives`；effect head | OA1 `suppression` |
| `procedure_operation` / `procedure_bind_step` | HM-AC-5/7 | journal(procedure callers)+effects；`procedure_uses` | observation captured_bound；`procedure_observation_journal`+步骤 effect | journal 仅进程内；OA1 无 procedure family |
| `prospective_source_read` / `current_input_visibility` | HM-AC-5/7；HM-AC-1 | journal(caller) | 结算 + 捕获 observation | journal 仅进程内 |
| `preparation_rejection` pre-SDK 拒绝 | HM-AC-7 | `foreground_run_transitions(preparation-rejected:v1)` | `preparation_audit_sources` | 仅进程内 |
| `turn_ingestion` | HM-AC-1/7 | `memory_ingestion_outbox` | `ingestion_receipts` + 审计页 memory_port | 审计页 |
| `runtime_log_events` | 记录性 | native.log `foreground.runtime.*` | 仅日志 | 仅日志 |

## 3. 覆盖结果

### 尝试 4（`native-a6-run4/primary-ui-7f_uv2a1`，24 轮单进程）

observed 741 / audited 741 / missing 0；分权扫描：313 表、96 个标识，无泄漏。

| 种类 | 状态 | observed | audited | 备注 |
|---|---|---:|---:|---|
| run_terminal | ✅ | 22 | 22 | COMPLETED 18 / FAILED 4，全部 enumerated |
| tool_effect | ✅ | 127 | 127 | context_page_in 4、context_route 35、procedure_discover 15、run_shell 6、task_scope_search 4、task_scope_update 2、tool_activate 7、tool_describe 8、tool_search 46 |
| provider_attempt | ✅ | 124 | 124 | 审计头 usage 与 Host 结算 token 均在 |
| context_snapshot | ✅ | 124 | 124 | 1 个回执冻结后未发送（非缺口） |
| route_decision | ✅ | 27 | 27 | continue_active 2、create_new 2、direct_standalone/no_recall 10、memory_standalone 10、resume_existing 3 |
| context_page_in | ◐ | 4 | 4 | 仅 effect 级审计（见 G1） |
| task_scope_search_open | ◐ | 4 | 4 | Host 回执 open 6 / search 4，无法逐条关联（G2） |
| task_scope_mutation | ✅ | 2 | 2 | |
| taskscope_event_ledger | ✅ | 147 | 147 | 事件 155 = 147 harness + 6 host.file + 2 mutation.plan |
| memory_analysis_apply | ◐ | 22 | 22 | 9 次 no_mutation 无结构化决策记录（G3） |
| typed_recall_foreground | ✅ | 10 | 10 | |
| typed_recall_analysis | ✅ | 22 | 22 | |
| recall_context_use | ✅ | 10 | 10 | |
| short_horizon_recall | ⓘ | 4 | 4 | |
| forget_suppression | — | 0 | 0 | 本次无遗忘操作（事件 M 遗忘按钮不可达） |
| procedure_operation | ◐ | 26 | 26 | 15 effect 中 4 个前置失败未到 SDK（仅 effect 审计） |
| procedure_bind_step / prospective_source_read / current_input_visibility / preparation_rejection | — | 0 | 0 | 未观测 |
| turn_ingestion | ✅ | 22 | 22 | 4 个 FAILED Run 无 SDK memory port，Host 仍投递原始证据 |
| runtime_log_events | ⓘ | 44 | 44 | bound 22 / closure_settled 22，仅日志 |

### 尝试 3（`native-a6-b3682fe1/merged-attempt3`，含中途重启）

observed 969 / audited 969 / missing 0；无泄漏。与尝试 4 的差异：

| 种类 | 状态 | observed | audited | 备注 |
|---|---|---:|---:|---|
| run_terminal | ✅ | 23 | 23 | COMPLETED 16 / FAILED 7 |
| tool_effect | ✅ | 169 | 169 | 含 memory_forget 18（工具按 DECISION-MEMORY-FORGET-TOOL 关闭，全部 failed 且审计头带 error_code） |
| provider_attempt | ✅ | 183 | 183 | failed 1 亦有审计头（finding `provider_usage_unavailable` 1） |
| task_scope_mutation | ✅ | 3 | 3 | task_scope_update effect failed 10 / rejected 7 未进入 attempt（前置拒绝，仅 effect 审计） |
| memory_analysis_apply | ◐ | 23 | 23 | 2 个 failed batch 有终态 job 事件；11 次 no_mutation（G3） |
| typed_recall_foreground | ◐ | 14 | 14 | 11 次 Host 侧超时 raise：SDK terminal=deadline_exceeded、journal=raised/absent+finding，只能按 run ref 关联（G4） |
| forget_suppression | ✅ | 18 | 18 | 全为 memory_forget effect；suppression_directives=0 |
| 其余 | 同尝试 4 | | | |

### 受控读取面事实（两次运行一致）

- `primary.audit.page`（S6 Task 4，OA1 九个 family）：family 表有行（尝试 4：typed 32/32/32、job_transition 110、mutation_commit 13、recall_context_use 10、short_recall 82、suppression 0、mutation_rejection 0），
  但本次运行**没有实际打开过审计面**：`human_audit_grants/deliveries` 表不存在，SDK `sealed_audit_access_events / audit_trace_access_events / audit_access_authority_events` 均为 0。
  可达性是按 family 表构造判定的，不是实测分页（G5）。
- Host `audit_pages`：22/23 个 Run 全部 `enumerated`，findings 只有 `operation_error_observed`（52/102）+ `provider_usage_unavailable`（0/1）；
  **没有任何 `primary.audit.*` 或其他 API 暴露它**，只有进程内 `AuditStore.inspect/coverage`（G6）。
- Host `memory_call_attempts` journal：仅进程内 `journal.page()`；procedure / prospective / current-input 三类调用在 OA1 里没有 family（G7）。
- 普通面隔离：`HUMAN_AUDIT_OPERATIONS` 只含 `primary.audit.open/page/close`，与普通面操作集不相交；证据库扫描无审计标识泄漏。

## 4. 缺口与处理

本次**没有做代码层的发射修复**：核对到的缺口都不是"确定且无 payload 风险的小修"，全部记录为 followup。

| 编号 | 缺口 | 位置 | 处理 |
|---|---|---|---|
| G1 | page-in 引用的发放/消费无 Host 持久回执，只有 effect 级审计 | `backend/deskpet/tools/context_page_in_tools.py:52`（`ContextPageInStore`，进程内 TTL）、`:62`（`put`） | followup：给 page-in 发放/消费落 payload-free 回执（引用 id hash、scope、run），或在 effect result 中显式携带引用 hash |
| G2 | `task_scope_search_access_receipts.receipt_json` 只有 operation/request_hash/result_hash/subject，无 effect_id/sdk_run_id，无法与 effect 逐条对账 | `backend/deskpet/task_scope/search.py:524-526` | followup：回执加 `effect_id`/`sdk_run_id`（改变 receipt_hash 契约，需同步 schema_version） |
| G3 | 分析结论为 `no_mutation` 时，只有 `llm_invocations` + job `applied`，没有 `memory_mutation_receipts(plan_outcome=no_mutation)` 与 `decision_records`；HM-AC-7 要求每次 LLM 操作都有结构化决策记录，OA1 `mutation_commit` family 本就定义了 `no_mutation` 事件种类 | SDK `simple_harness_memory/backends/sqlite_v5.py:12906-12928`（no_mutation 视为 valid 且不走 apply）；Host 侧 `backend/deskpet/memory/analysis_proposal.py:505` | followup（SDK 0.6.x）：no_mutation 也签发 receipt/决策记录 |
| G4 | Host 先于 SDK 结算 raise（DECISION-RECALL-TIMEOUT-HOST-SIDE）时，journal 行 `raised/absent`、无 result_hash；SDK `typed_recall_attempts` 不持久 Host attempt ref（`observation_context` 只回流到错误 observation），两侧只能按 run ref 关联 | Host `backend/deskpet/operation_audit/memory_attempts.py:280`（传 `observation_context`）、`:128-134`（`absent`）；SDK `typed_recall_attempts` 无 host ref 列 | followup（SDK）：attempt 行持久 `host_attempt_ref_hash`，OA1 item 带出 |
| G5 | S6 Task 4 受控面在 A6 两次运行中未被调用，分页/ACK 可达性未在真实运行里实测 | `backend/deskpet/operation_audit/human_access.py` | followup：第 5 次运行加一轮 UI「为什么记住」触发 `primary.audit.open/page/close`，核对 `human_audit_deliveries` 与 SDK access events |
| G6 | 终态 Run 审计页（tool effect / provider / route / page-in / ingestion 的审计）没有任何受控 UI 读取面，只有进程内 inspect | `backend/deskpet/memory/human_memory_api.py:34`（`HUMAN_AUDIT_OPERATIONS`）、`backend/deskpet/operation_audit/store.py:547`（`inspect`） | followup（S6）：把 `audit_pages` 纳入 `primary.audit.page` 的分页（或新增 `primary.audit.runs`），同样走签名 grant + 预算 |
| G7 | procedure / prospective / current-input 调用只在 Host journal，OA1 无对应 family | SDK `simple_harness_memory/core/operation_audit.py:170`（`FAMILIES`） | followup（SDK） |
| G8 | `_record_audit` 只写日志（`_AuditSink → logger.info`），`foreground.runtime.failed/stalled` 等不进持久审计 | `backend/deskpet/execution/foreground_runtime.py:541`、`backend/main.py:3129` | 记录：持久审计由 `foreground_run_transitions` 承担；如需可审计的 stalled/failed 原因，落 transitions 而非日志 |
| G9 | `MemoryAttemptJournal.page()` 的 `coverage` 常量只写两类 caller，但同表已由 procedure/prospective/current-input 子类写入 | `backend/deskpet/operation_audit/memory_attempts.py:348` | 记录：诊断口径失真，不影响发射 |

## 5. 结论

- 在真实运行里，**每一个**被观察到的 tool effect、provider attempt、context snapshot、route 决策、TaskScope 变更/事件、分析调用、typed recall（含超时）、
  使用授权、turn ingestion、终态 Run 都能在相应审计记录里找到对应项（两次运行 missing=0），普通业务库中无审计标识泄漏。
- "全操作覆盖"目前的真实状态是：**发射覆盖 ✅（有观测的种类）；读取面覆盖 ◐**——Memory 侧九个 family 经 S6 受控面可达但本次未实测，
  Host 侧终态 Run 审计页与 journal 没有 UI 入口（G5/G6/G7）。程序状态建议改为「全操作发射覆盖 ✅（A6 尝试 3/4 实证）；受控面覆盖待 G5/G6」。
- 遗忘/suppression、procedure 绑定、prospective、current-input、pre-SDK 拒绝五类在两次运行里都没有出现，覆盖仍待第 5 次运行（尤其遗忘依赖事件 M 修复）。

## 6. 复现

```bash
cd simple_harness
PYTHONPATH=backend backend/.venv/bin/python scripts/audit/audit_coverage.py \
  --evidence .local-test-evidence/2026-09-08/native-a6-run4/primary-ui-7f_uv2a1 \
  --json /tmp/run4-coverage.json --markdown /tmp/run4-coverage.md
PYTHONPATH=backend backend/.venv/bin/python scripts/audit/audit_coverage.py \
  --evidence .local-test-evidence/2026-09-08/native-a6-b3682fe1/merged-attempt3 --fail-on-gap
cd backend && python -m pytest tests/quality/test_audit_coverage.py -q
```

---

## 6. 2026-09-08 追加：G1 / G2 / G5 / G6 已实现（本文其余内容保持核对当时的事实）

见 `DECISION-AUDIT-SURFACE-G1-G2-G6.md`。要点：

- **G6**：新增受控只读操作 `primary.audit.host.page`（section=`runs` / `run_operations` / `memory_calls`），
  与 `primary.audit.page` 共用同一张签名 grant，Host 读取不消耗 SDK 页预算，按 section+target 分流各 32 页，
  行只含标识/状态码/哈希/计数/时间戳；「操作记录」tab 新增「本机执行审计」小节（全部显式点击）。
- **G1**：`operation-audit.db` 新表 `context_page_in_receipts`（issued / consumed / denied，payload-free，
  带 `sdk_run_id` / `effect_id`），核对器按 `effect_id` 与 effect 逐条对账。
- **G2**：`task_scope_search_access_receipts.receipt_json` 升到 `schema_version 2`，带 `effect_id` / `sdk_run_id`
  （HUMAN 通道为 null），`receipt_hash` 随之改变。
- **G5**：`backend/tests/operation_audit/test_audit_surface_exercise.py` 走生产 `/ws/control` 路径做
  grant → 分页 → ACK → close 的无 UI 演练，并用核对器断言三个受控面 `exercised=true`；
  真实运行的实测挂在 00-PLAN 的 T16/T24 追加取证上。
- **旧证据不变**：本文第 3 节的两次运行里 `context_page_in` / `task_scope_search_open` 仍是 ◐、
  受控面 `exercised=false`——回执与交付表只可能出现在新的运行里。
- G3 / G4 / G7（SDK 侧）与 G8 / G9（记录不修）维持原结论。
