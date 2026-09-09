# 事件 AI 决策备忘：归档证据种类词表有三份拷贝，生产者跑在词表前面就打死 Run

> 日期：2026-09-09 ｜ 义务：HM-TO-A6 原生真实模型验收（第 12 次整跑，attempt 12 / turn 18）
> 证据：`.local-test-evidence/2026-09-09/native-a6-run12/primary-ui-z9j48osx/`
> 前置裁决：`DECISION-AA-AUTHORITY-STALE-RECOLLECT.md`（事件 AA，同日）、
> `DECISION-F-Z1-READ-TOOL-CALL-GATE.md`（F-Z1 读工具闸门，同日合入）

---

## 1. 现象

`native.log:2403`

```
05:29:48.322508Z  error  simple_harness.runtime.kernel
                  event=sdk_run_driver_failed
                  error_type=ValueError
                  error_message=execution_evidence_kind_rejected
05:29:48.323236Z  info   run.fail
```

turn 18 仍是「读一下这个任务的 README / STATUS 视图」。失败 Run 是
`product-sdk-b24bb1343899ea01e1937a1552d195b019428d2a304a4bec5c99ec7ceac1eeaa`
（`harness_evidence_reservations` 里 `max(reserved_at)=1788931788.51929` = 05:29:48，唯一对得上）。

## 2. 根因：**被拒的不是读工具，是事件 AA 的有界重采回执**

### 2.1 逐行复原

`execution-v6.sqlite3` `execution_effects`（该 Run 全部 23 条效应）：

| turn | 工具 | 结果 |
|---|---|---|
| 1 | `task_scope_search`、`context_route` | succeeded |
| 2 | `context_route` | succeeded |
| 3–5 | `tool_search` ×2、`tool_describe` ×5 | succeeded |
| 6 | `tool_activate` ×4 | 第 1 条 `builtin:read_file` succeeded，其余 3 条 `catalog_describe_nonce_invalid` |
| 7 | `tool_describe` ×3 | succeeded |
| 8 | `tool_activate` ×3 | 第 1 条 `builtin:list_directory` succeeded，其余 2 条 nonce 失效 |
| 10 | `tool_describe` ×2 | succeeded |
| 11 | `tool_activate` ×1（`builtin:glob`） | succeeded |
| **12** | **`glob` ×3** | **全部 succeeded**（`effect-563a152e…` / `effect-80588708…` / `effect-6d19aff8…`） |

`state.db` `harness_evidence_reservations`（同 Run，47 行）：

| seq | source_event_id | kind | status |
|---|---|---|---|
| 43 | `effect:effect-563a152e…` | `tool_invocation` | **ingested** |
| 44 | `effect:effect-80588708…` | `tool_invocation` | **ingested** |
| 45 | `effect:effect-6d19aff8…` | `tool_invocation` | **ingested** |
| 46 | `snapshot:ctx-snap:…:13:89e6deb5492bbddd` | `context_snapshot` | ingested |
| 47 | `…:terminal:failed` | `run_terminal` | ingested |

`host_pre_admission_audit` 同时留下三条 `payload_kind=context_route` /
`reason_code=workspace_read.glob.admitted`（05:29:45.9 / 46.2 / 46.9）。

**结论一：三次 `glob` 从 F-Z1 读闸门到归档全程走通，`tool_invocation` 预留全部 `ingested`。
读工具效应从来就不需要新的证据种类，它按普通已结算 SDK 效应投影成 `tool_invocation`，
`public_payload.tool_name='glob'`。事故简报里「读工具可能需要 `harness.tool_invocation`
或一种读专用种类」的假设，被行数据否掉。**

### 2.2 真正被拒的那一条

seq 46 落的是 turn 13 的快照回执，之后 **`provider-turn:13` 的 `provider_invocation` 预留不存在**
——崩在两者之间。这中间只有一件事：`context_authority.compose`
（`sdk_adapters/context_authority.py:1819`）在 `record_snapshot_receipt` 之后调用
`typed_use_authority.snapshot_intents`：

```
snapshot_intents → _rebind（now + CONTEXT_USE_LEASE_MARGIN_SECONDS(10s) >= 租约到期）
                 → _recollect → memory.typed_recall（native.log:2402 的 "Batches: 1/1" 就是这次重采的向量编码）
                 → ledger.record_context_use_recollection
                 → context_authority._ingest_fact_tx(kind="context_use_recollection")
                 → evidence_ingress.ingest_ledger_fact_tx → reserve_tx
                 → kind not in RESERVATION_KINDS → raise ValueError("execution_evidence_kind_rejected")
```

`log_redaction` 的 `error_type` 取 `type(error).__name__`，日志写的是裸 `ValueError` 而不是
`TaskScopeProtocolError`，正好把肇事点钉死在 `evidence_ingress.py:392` 的 `raise ValueError`，
而不是 `protocol.py:209` 的 `TaskScopeProtocolError`（后者是 `ValueError` 子类，会打出子类名）。

`context_use_recollections` 表在整个证据库里是空的——事务回滚了，回执一行都没落。

### 2.3 缺陷本体：词表有三份拷贝，事件 AA 只动了生产者

`context_use_recollection` 这个 token 在同日合入的事件 AA 里诞生（v56 迁移
`primary/048_context_use_recollections_v56.sql` + `record_context_use_recollection` 写入路径），
但它同时缺席于**三处**：

1. `deskpet/execution/evidence_ingress.py` `RESERVATION_KINDS`（预留侧）；
2. `deskpet/task_scope/protocol.py` `_EXECUTION_KINDS`（导入侧证据校验）；
3. `harness_evidence_reservations.kind` 的 SQL CHECK（v46 迁移 038 定义）。

三份都没加。所以事件 AA 那句「租约到期时有界重采改绑，**不再击杀整个 Run**」在本轮反而
换了个地方击杀 Run：租约到期不再直接失败了，改成重采；重采成功了；然后重采回执入档被
自家词表拒绝，Run 照样死，而且死得更不可归因（裸 `ValueError`）。

**为什么第 12 次才炸**：这个路径要求「一轮之内绑定召回的租约进入 10s 余量」。turn 18 里
F-Z1 新暴露的读工具把这一轮拉长了——`tool_describe`/`tool_activate` 的 nonce 环路（§4）
烧掉 turn 4→11 共 8 个来回约 35 秒，再加 3 次 `glob`，从 05:28:58 的路由决策到 05:29:47
恰好越过余量线。读工具不是被拒的证据，但它是把这条潜伏路径踩响的引信。

## 3. 处置

### 3.1 词表三处同时前进（本轮做）

- `RESERVATION_KINDS` / `_EXECUTION_KINDS` 加入 `context_use_recollection`，并在两处写清
  投影规则：它是 state.db 侧 Harness 事实，**没有 `tool_name`**（不是工具效应），
  payload 只有身份与计数，导入为 `harness.context_use_recollection`。
- `semantic_closure.TRIVIAL_EVENT_KINDS` 加入 `harness.context_use_recollection`：重采只是
  给模型已经拿到的那串字节换一张授权绑定，不改变任何项目状态，对收口而言 trivial。
- SQL 那一份走新链步 **v57**：`primary/049_evidence_kind_recollection_v57.sql` +
  `deskpet/memory/evidence_kind_schema.py` + `schema_chain.DOMAIN_SCHEMA_STEPS[57]`，
  `main.py` 的启动初始化改指链头。SQLite 改不了 CHECK，按官方 12 步重建表：列定义逐字不变
  （因此 v42 恢复登记 `columns_json` 仍成立，**不重新登记**），索引、v46 的两个
  append-only/单调触发器、`_register_recovery_tables` 建的三个恢复围栏在脚本里逐字重建；
  迁移里前后比对行数，少一行即报 `evidence_kind_migration_row_loss`（丢一条预留会永久悬空
  一个 `source_sequence`，之后所有 `run_terminal` 都放不了行）。
  沿用 v46 起的割接前置：**有非终态前台 Run 时拒绝迁移**——正在写这张表的 Run 不能被搬走。

### 3.2 未知种类不得再静默打死 Run（本轮做，是这次的结构性修复）

`ValueError("execution_evidence_kind_rejected")` 换成
`EvidenceKindRejected(ValueError)`：保留原消息（旧调用方期望不变），带稳定 `code`
与 `.kind`，并在抛出前 `logger.error` 打出 `evidence_kind=<名字> run_id=… source_event_id=…`
（种类是词表 token，不是 payload，可安全落日志）。

更要紧的是**校验位置前移**：`ingest_ledger_fact_tx` 是全局唯一 `kind` 为运行时变量的入口
（`reserve`/`commit_fact`/terminal 三条路的 kind 都是字面量），现在它在
`resolve_run_scope_tx` 之前、在调用方事务里读写任何一行之前就先判词表；`reserve()` 也在
拿 `BEGIN IMMEDIATE` 之前先判。于是未知种类的爆炸半径正好是**产生它的那一个事实**：
不吃序号、不留半条 Harness 事实、不占写锁，调用方回滚这一个效应即可，此前的预留/回执/
水位一律不动，Run 继续往下走。用例 `test_unknown_kind_fails_one_effect_and_the_run_continues`
钉的就是这条：拒完之后下一条合法事实照常拿到 seq 4，水位连续。

### 3.3 词表漂移改由 CI 抓（本轮做）

`tests/task_scope/test_protocol_evidence_kinds.py` 把三份拷贝钉成同一个集合，并逐个种类
跑一遍 `validate_execution_evidence`。事件 AA 那种「生产者先落地、词表后补」的形态，
以后在 CI 红，而不是在原生第 18 轮红。

### 3.4 不做

- 不动 `context_authority.py`（事件 AG 在改）、不动 `read_gate.py` 与 `os_tools/*grep*|*glob*`
  （事件 AH 在改）。§3.2 的收敛全部落在 `evidence_ingress.py` 自己。
- 不给读工具新开证据种类（§2.1 已证明不需要）。
- 不改 `typed_context_use._recollect` 的失败语义：重采拿不到可用绑定时仍
  fail closed（`RecallContextUseAuthorityStale`），那是事件 AA 的既定裁决。

## 4. `catalog_describe_nonce_invalid` 环路：不是幂等问题，是「每轮只能成功一次激活」

事故里 5 条 `tool_activate` 失败，简报的猜测是「模型复用了陈旧 nonce / 重复激活已激活的工具」。
逐条看参数，两个猜测都不成立：

- turn 6 的 4 条 `tool_activate` 各自带**不同**的 `describe_nonce`，都是 turn 4/5 各自
  `tool_describe` 刚拿到的；turn 8 的 3 条同理。
- `glob` 在 turn 6、turn 8 各失败一次，直到 **turn 11 才首次激活成功**——整段里根本没有
  「重复激活已激活能力」这种调用。

真正的机制在 SDK：`RuntimeToolCatalog._describe_nonce` 的原像含 `exposure_revision`，
而一次成功的 `activate` 会把 `state.revision + 1`。于是**同一轮里持有的其它 nonce，在第一条
激活落地的瞬间同时失效**——批量发 N 条激活，永远只有第一条能成。模型看到的下一步指引却是
「再 `tool_describe` 一次，把 nonce 抄进 `tool_activate`」，直接把它送回同一个批量形态，
环路就此成立（turn 4→11）。

按目录契约（design-freeze §1/§7）**二次激活已激活能力本来就应该是幂等成功**，SDK 也确实
如此实现：`RuntimeToolCatalog.activate` 与 `_apply_receipt` 都在 `capability_id in
state.activated_ids` 时先返回收据/直接 return，**发生在 nonce 校验之前**——伪造的 nonce 也不报错。
所以契约与实现都无需改动，`tests/sdk_adapters/test_tool_activation_nonce_ai.py` 把这条钉住即可。

需要改的只有 Host 侧指引：`deskpet/tools/tool_search.py` 的
`_ACTIVATION_NEXT_ACTIONS["catalog_describe_nonce_invalid"]` 改成点名
「每轮只能成功一次 `tool_activate`，一次只激活一个 `capability_id`，已激活的不要再激活」。

## 5. 决定性测试

| 文件 | 例数 | 钉住 |
|---|---|---|
| `tests/execution/test_evidence_ingress_kind_ai.py` | 4 | 复现事故轮形状（3×`glob` → `context_snapshot` → `context_use_recollection` 全部入档，读效应带 `tool_name='glob'`、重采回执无 `tool_name`）；投影规则均为 trivial；未知种类只失败一个事实且 Run 继续（稳定码 + 种类名进日志 + 水位不动）；未知种类在拿写锁前被拒 |
| `tests/task_scope/test_protocol_evidence_kinds.py` | 10 | 三份词表相等（含直接解析 v57 迁移脚本里的 CHECK 字面量）、冻结集合、6 个种类逐个过 `validate_execution_evidence`、未知种类稳定码 |
| `tests/sdk_adapters/test_tool_activation_nonce_ai.py` | 3 | 一轮一次激活会让同轮其它 nonce 失效；重复激活已激活能力幂等成功；指引文案含新规则 |

复现用例在 `main@9412f961` 上红成事故那一个码：`kind not in RESERVATION_KINDS` →
`ValueError: execution_evidence_kind_rejected`（Python 词表），词表放开后再红成
`sqlite3.IntegrityError: CHECK constraint failed: kind IN (...)`（SQL 词表）——两道都补上才绿。

回归（单进程、点名文件）：`test_evidence_ingress_kind_ai` + `test_protocol_evidence_kinds` +
`test_read_tool_call_gate_f_z1` + `test_tool_activation_nonce_ai` + `test_evidence_reservations`
+ `test_s5c_schema_chain` = **44 passed**。
`tests/memory/test_effect_closure_migration_v46.py` 与 `tests/memory/test_s5b_v46_cutover.py`
共 7 例 FAILED，在 `main` 一次性 worktree 上 FAILED 集合逐条相同（`human_memory_program_marker_invalid`），
与本次改动无关。

## 6. 遗留

- `record_context_use_recollection` 的入档失败仍会顺着 `snapshot_intents` 冒到 driver。
  §3.2 让未知种类不再从这里冒出来（词表已补齐 + 未知种类在写任何一行之前就被拦下），
  但「归档拒绝一条上下文事实时 Run 该不该活」这个语义归事件 AG/AA 的边界，本轮不动。
- v57 迁移沿用 v46 割接前置，非终态前台 Run 存在时拒绝迁移。若上次进程崩溃留下 RUNNING 的
  run head，启动会被挡在迁移这一步——与同日 v56 的风险面相同，一并记录。
