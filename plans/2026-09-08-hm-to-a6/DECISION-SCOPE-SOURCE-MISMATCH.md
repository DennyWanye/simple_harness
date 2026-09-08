# 裁决备忘：Incident R —— 同一 Run 内第二次 `context_route` 卡死前台驱动

2026-09-09。Host 工作树 `worktree-scope-source-mismatch`（基线 `f161f5a4`）。
本备忘由执行代理自行裁决并记录，未向用户提问。

## 1. 现象与原始证据

HM-TO-A6 第 7 次原生旅程（`deepseek-v4-flash`，窗口 32000）T1–T5 全部 COMPLETED。
T6 的 SDK 侧 `run.complete` / `run.terminal` 于 19:20:53.63Z 正常settle，随后 Host
终局观察连续 4 次抛：

```
{"event":"foreground.runtime.failed","error_code":"RuntimeError",
 "error_detail":"primary_message_scope_source_mismatch","attempt":1..4}
{"event":"foreground.runtime.stalled","attempts":4}
```

provider 调用就此停止，Run 头长期停在 RUNNING。

原始证据（只读，本轮只做副本，含 `-wal/-shm`）：
`.local-test-evidence/2026-09-09/native-a6-run7/primary-ui-6idcnskv/`
（`native.log`、`userdata/data/state.db`、`human_memory_v7.db`、
`userdata/data/simple-harness-sdk/execution-v6.sqlite3`）。

T6 的 `sdk_run_id = product-sdk-9b9ef04c…`，`host_run_id = 9cae01fc-…`。

## 2. 离线复现与失败判据定位

用安装版 Memory SDK + 本工作树 Host 代码，把三份 DB 的副本以 `mode=ro` 打开，
按 SDK `execution_effects` 重建 T6 的 18 条 `tool_causal_sources` 事实形状
（只用标识符与状态，不含任何会话正文），逐条调用驱动当时走的
`primary_message_v3.read_scope_sources_tx`：

| 事实 | tool_name | state | 结果 |
|---|---|---|---|
| 前 7 条（provider 轮 1–4） | tool_search | succeeded | OK（Run 尚无准入 Scope，无回执，不产生 Scope 证明） |
| `effect-14225708…`（轮 5） | context_route | succeeded | OK（控制血统，无 Scope 证明） |
| 中间 4 条（轮 6–9） | tool_search / tool_describe / task_scope_search | succeeded | OK（有 Scope） |
| **`effect-1ccf1fc0…`（轮 10）** | **context_route** | **failed** | **FAIL `primary_message_scope_source_mismatch`** |
| 其余 5 条（轮 11–13） | tool_search / tool_describe / task_scope_update(rejected) | — | OK（有 Scope） |

再对该条事实逐句核对 `_verify_route_control_tx` 的每一条判据，
只有一条为假：

| 判据 | `effect-14225708…`（首次路由） | `effect-1ccf1fc0…`（本轮失败） |
|---|---|---|
| `set(public)` 六键 | True | True |
| `public.tool_name == 'context_route'` | True | True |
| `public.raw_call_id == fact.raw_call_id` | True | True |
| **`reservation.tool_name is None`** | **True** | **False（`'context_route'`）** |
| `invocation_id` 形状 | True | True |
| `verdict ∈ {accepted,rejected,clarification}` | True | True |
| `canonical_sha256(body) == invocation_hash` | True | True |
| `public == expected`（账本回显） | True | True |

即：失败判据是 `backend/deskpet/memory/primary_message_v3.py`
（旧 23 行）`reservation['tool_name'] is not None`；失败事实是 T6 第 10 个
provider 轮的第二次 `context_route`（SDK effect state `failed`，路由 verdict `rejected`，
`decision_id` 为 NULL）。

## 3. 根因（生产方/校验方漂移，非数据损坏）

`harness_evidence_reservations.tool_name` 由两条**都合法**的生产路径写入：

1. `ToolAdapter._reserve_evidence`（`sdk_adapters/tools.py`）在**物理派发之前**预留，
   带 `tool_name=call.name`；但它要求 Run 已经有准入 Scope
   （`_evidence_scope` 解析不到 binding 就整个跳过）。
2. `ContextRouteLedgerStore._ingest_fact_tx → ingest_ledger_fact_tx`
   （`execution/evidence_ingress.py`）在路由账本自己的写事务内预留，**不带** `tool_name`。
   `reserve_tx` 对已存在的预留只在 `tool_name` **冲突**时报错，`None` 视为兼容。

于是同一个 `context_route` effect 的预留 `tool_name`，完全取决于哪条路径先跑：

* **一个 Run 的第一次 `context_route`** 正是绑定准入 Scope 的那一次调用——派发时还没有
  Scope，路径 1 不预留，只有路径 2 补上，`tool_name` 为 NULL。
  run7 的三个 Run 里所有 `source_sequence = 2` 的路由都是这种。
* **同一 Run 内的第二次 `context_route`**（run7 T6 `source_sequence = 17`）此时 Scope 已存在，
  路径 1 先预留，`tool_name = 'context_route'`。

`a4117ef6`（C05 控制/物理来源分派修复）只取样到前一种，把「预留没有物理 tool_name」
写成了控制血统的**正向**判据。这是生产方/校验方漂移。

**触发它的今晚合并**是 `c70f568f`（事件 A/B：standalone 路由下工程效果工具在
describe/activate 阶段即披露不可激活；**`task_scope_search` 零命中引导 `continue_active`**）。
该合并把「路由后可重试」写进了发现面的下一步指引，模型因此在同一 Run 内第二次调用
`context_route`——这是 `a4117ef6` 的判据从未见过、也永远无法接受的输入形状。
强制分页（`f161f5a4` / `8f9aeee6`）、F-K1 侧记（`383a1c84`）、K 入参备忘（`26395f01`）
经核对**均未**改动本条链路的任一字段（`effect_state`/`call_id`/`raw_call_id`/回执状态/摘要
在失败事实上全部相等）。

## 4. 裁决与修复

**裁决**：控制事实的预留 `tool_name` 有两种合法写法，判据必须同时接受；
不能因为一次取样就把某条路径的副产物当成契约。同时，v3 终局契约的 fail-closed
语义不变——真正不一致的来源仍然必须拒绝。

**修复**（`backend/deskpet/memory/primary_message_v3.py`）：

```
_check(reservation["tool_name"] in (None, CONTROL_TOOL_NAME),
       "route_control_reservation_tool_name", ordinal=ordinal)
```

即 `NULL` 与 `context_route` 都放行，**任何其它工具名仍然拒绝**
（不得把一个物理调用的预留改标成控制血统）。其余每一条判据（事件/回执/预留/属主
/摘要/账本 `invocation_hash`/公开载荷全等）一字未改，顺序也未改。

## 5. 可诊断性（把一个码拆成每条判据一个稳定码）

新增 `PrimaryScopeSourceError(code, reason_code, item_ordinal)`：

* `str(exc)` 与 `.code` 仍是原稳定码（`primary_message_scope_source_mismatch` /
  `primary_message_scope_source_missing` / `primary_message_scope_control_source_missing` /
  `primary_message_v3_contract_mismatch`），既有调用方与审计 grep 不受影响；
* `.reason_code` 只带 **Host 字段名**：`route_control_public_keys`、
  `route_control_reservation_tool_name`、`route_control_invocation_hash`、
  `reservation_status`、`reservation_source_sequence`、`public_effect_state`、
  `task_scope_owner_subject`、`terminal_scope_source_ingest_receipt_<字段>` …
* `.item_ordinal` 只带 transcript 序号（整数），不带任何 envelope / 工具入参 / 结果字节。

`verify_scope_sources_tx` 的整表比对也不再只报一个码：`_stored_source_reason`
定位到**第一条**不等的事实与**具体键名**（长度 / 键集 / `task_scope_id` /
`ingest_receipt.<字段>`）。

前台驱动（`ForegroundRuntimeExecutionAuthority._reason_audit_fields`）把
`error_reason_code` / `error_reason_ordinal` 同时补进 `foreground.runtime.failed`
与 `foreground.runtime.stalled` 两条审计线；异常没有 `reason_code` 时不新增任何键。

## 6. 测试

新增（`backend/tests/memory/test_procedure_scope_sources.py`）：

* `test_mid_run_route_control_keeps_its_dispatch_reservation_tool_name`
  —— 用真实的 `ContextRouteLedgerStore` + `ExecutionEvidenceIngress` 复刻事故的三种
  预留形状（绑定路由 `tool_name` NULL、Run 内二次路由 `'context_route'`、
  以及冒充控制的 `'write_file'`）。前两者通过且都不产生 Scope 证明；第三者仍然拒绝，
  并断言 `reason_code == 'route_control_reservation_tool_name'` 与 `item_ordinal == 7`。
  **回归有效性已验证**：把判据改回 `is None` 后该用例立刻 FAIL。
* `test_scope_source_clauses_carry_stable_payload_free_reason_codes`
  —— 四条物理判据 + 属主判据各自报出自己的 `reason_code` 与 `item_ordinal`，
  并断言驱动 `_reason_audit_fields` 携带它、对普通异常不加键。

用例只用合成标识符与状态，不含任何事故会话正文。

已跑（每次一个 pytest 进程，只点名文件）：

| 文件 | 结果 |
|---|---|
| `tests/memory/test_procedure_scope_sources.py` | 5 passed |
| `tests/memory/test_procedure_scope_runtime.py` | 1 passed |
| `tests/memory/test_primary_tool_causality.py` | 5 passed |
| `tests/memory/test_prospective_registration_source.py` | 7 passed |
| `tests/execution/test_primary_history_tool_calls.py` | 7 passed |
| `tests/execution/test_primary_history_outbound.py` | 16 passed / **1 failed（基线既有）** |
| `tests/execution/test_current_tool_pages.py` | 3 passed |
| `tests/execution/test_primary_foreground_runtime.py` | 19 passed |
| `tests/execution/test_evidence_reservations.py` | 6 passed |

`test_primary_history_outbound.py::test_late_history_denial_is_failed_while_sent_ambiguity_stays_unknown[sent_unknown]`
在**未改动的基线 `f161f5a4`** 上同样 FAIL（已把三个改动文件 `git checkout --` 回基线单独复验），
与本轮改动无关，另行跟进。

修复后再跑一次离线复现：T6 的 18 条事实**全部通过**，两条 `context_route`
都正确地不产生 Scope 证明。

## 7. 边界

无 DDL、无证据改写、无 SDK 改动、无新授权、不推断「最近一次路由」。
`conversation_registration` 对控制事实仍取 `task_scope_id = None`（v3 原语义未变）。
未触碰 `context_partitions.py` / `model_info.py`（另一代理在改）、桌面应用与 18120 端口。
