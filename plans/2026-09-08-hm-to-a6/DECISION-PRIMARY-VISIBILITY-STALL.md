# HM-TO-A6 原生跑失速：primary_read_policy_unavailable 根因、定性与修复

- 日期：2026-09-08
- 事故证据：`.local-test-evidence/2026-09-08/native-a6-7cec5249/primary-ui-cw3ifsxo/`
  （`native.log`、`userdata/data/{state.db,human_memory_v7.db,operation-audit.db}`）
- Host 基线：main `7cec5249`（H0.7.10 / M0.6.26 / S0.3.13）
- 工作树：`.claude/worktrees/agent-a057e3ebd26f3de05`，分支 `worktree-agent-a057e3ebd26f3de05`

## 1. 现象

第 5 轮 Run 正常 COMPLETED（`foreground.runtime.closure_settled status=clean`，02:59:16.300Z）后 0.5 秒，
前台驱动 `foreground_runtime.py::_run_driver` 抛异常退出：

```
{"error_code": "primary_read_policy_unavailable",
 "error_detail": "primary_read_policy_unavailable",
 "error_type": "PrimaryVisibilityError",
 "event": "foreground.runtime.failed", "timestamp": "2026-09-08T02:59:16.812459Z"}
```

此后：

- UI 永久停在「等待主对话就绪 / 正在重新读取… / primary_read_policy_unavailable」；
- 第 6 轮用户消息 `d5bf3805-d457-5287-b338-9f52f4c3ea07` 已经进入 `foreground_turn_heads`
  （`current_state='QUEUED'`，enqueue 时间 1788836319.9，早于失败），但 `foreground_run_heads`
  永远停在 5 行——没有任何东西再去推进它；
- `native.log` 从 03:00:07 起以 ~2.15 秒的固定节奏刷
  `memory.evidence_ingestion_replayed`（`envelope_hash 9ec45597…`，全日志共 418 条）。

## 2. 根因（离线复现）

复现脚本按前台读路径（`PrimaryHistoryStore.read` → `PrimaryHistoryPolicy.check_evidence_ids`）
对拷贝出的 DB 重跑同一批 roots（5 轮 USER 证据 + 5 条 `primary-runtime:` 终态观察）。
被 `primary_visibility.py:392` 的 `except Exception` 吞掉的真实异常是：

```
simple_harness_memory.core.errors.MemoryLimitError:
    evidence_payload_requires_controlled_blob_ref
  ← simple_harness_memory/core/evidence.py:310 validate_sanitized_evidence
  ← backends/history_visibility.py:381 check_history_visibility
```

触发它的 envelope 是第 5 轮的主对话终态观察：

| 字段 | 值 |
| --- | --- |
| `evidence_id` | `f4c7b74d-f11e-544e-bef9-3315bdb42de8` |
| `source_kind` / `source_ref` | `runtime_event` / `primary-runtime:product-sdk-2d0548f4…` |
| `envelope_sha256` | `9ecefb5c483c…` |
| canonical payload 字节 | **74196** |
| SDK 上限 `MAX_INLINE_EVIDENCE_BYTES` | **65536**（64 KiB） |

即：`primary_history.py::record_terminal_observation` 把整条 Run 的
`messages`（第 5 轮有 12 条 `tool_result`，单条 8–9 KB）内联进 S1 evidence 的
`sanitized_payload`，没有任何长度前置校验。Host S1 收下了这条记录，
**但 Memory SDK 之后每一次读到它都会拒绝**。全库仅此 1 条超限（其余 69 条都在上限内）。

### 2.1 为什么整条主对话都读不了

`_check` 把所有 binding 组成**一个** SDK 批次。批次里只要有一条 envelope 结构上不可受理，
`check_history_visibility` 的第一轮 `validate_sanitized_evidence` 就整批抛错，
于是 `_check` 把它包成稳定码 `primary_read_policy_unavailable` 抛出：

- `PrimaryHistoryStore.read`（`primary_history.py:237`，`check_evidence_ids` 会外抛）
  → `PrimaryForegroundContextPort._source` → `draft_lineage` → `_drive_once` → 驱动死亡；
- 同一个批次也是 UI 读消息列表的路径 → UI 永久报 `primary_read_policy_unavailable`。

`check_dependencies` 那条路吞掉异常返回 False，所以不是它。

### 2.2 为什么会有 2 秒一次的重放风暴

同一条超限 envelope 的第二个受害者是短期索引车道：

`PrimaryShortIndexWorker.step()` → `PrimaryShortIndexingService.register_group(group)`：

1. `index == 0` 先 `ingest_committed_evidence(USER envelope)` —— 该 envelope 早已由
   `memory_ingestion_outbox` 摄入过，于是 SDK 走 `_verify_replay`，
   打出 `memory.evidence_ingestion_replayed`（就是日志里那条，`9ec45597…` = 第 5 轮 USER 证据）；
2. 紧接着 `admit_evidence_source(terminal envelope)` —— 这一步命中同一个
   `MemoryLimitError`（`backends/source_admission.py:88` 同样调 `validate_sanitized_evidence`）；
3. `MemoryLimitError` 是 `RuntimeError` 子类，被 `step()` 的
   `except (ValueError, TypeError, RuntimeError, TimeoutError)` 收进 `blocked`，
   **该组永远进不了 `_confirmed` 缓存**；
4. 下一个轮询周期原样重来 → 每周期一条 replay 日志 + 一次无意义写。

日志佐证：第 1–4 轮都是「ingested 一次 + replayed 一次」后就再无动静
（各自被 `_confirmed` 记住），只有第 5 轮 `replayed` 418 次不停。

## 3. 定性

**Host 缺陷。** 不是 Memory SDK 缺陷，也不是第 5 轮那两次 `tool_arguments.invalid`
工具拒绝本身造成的数据损坏。

- SDK 的行为完全符合契约：内联 payload 上限 64 KiB，超限必须改用
  controlled blob ref（`{blob_ref, content_hash, byte_length}`），否则拒绝受理。
  拒绝发生在受理与读取两侧是一致的、确定性的。
- Host 违反了这个契约：`record_terminal_observation` 构造 payload 时没有任何
  尺寸前置条件，写入 S1 时也没有。工具密集的一轮把它顶穿了。
- 工具拒绝只是**放大器**：两次 `tool_arguments.invalid` 让模型多跑了几轮工具，
  transcript 变长，才越过 64 KiB。换任何一个长回合都会复现。TaskScope 绑定无关。
- 真正把「一条坏记录」放大成「整条会话不可用 + 驱动永死」的，是 Host 自己的两个设计问题：
  ① 把确定性的、逐 envelope 的结构性拒绝当成了瞬时的「策略不可用」；
  ② 驱动一抛异常就永久退出。

## 4. 修复

一次事故暴露了三层缺陷，三层都修：**写入点**产出了不可受理的 envelope（根因）、
**读取点**把逐 envelope 的确定性拒绝当成整批的策略不可用（放大器）、
**前台驱动**一抛异常就永久死亡（把一次读失败变成永久停机）。

### 4.1 (a) 根因：写入点不得写出 Memory 之后一定会拒绝的 envelope

`backend/deskpet/execution/primary_history.py`

`record_terminal_observation` 现在做两件事：

1. **落库前用 SDK 自己的规则校验**：payload 组好、`evidence_pair` 造出 envelope/receipt
   之后调 `_warn_if_unadmissible`，它直接调 `assert_source_admissible`
   （内部就是 SDK 的 `validate_sanitized_evidence`）。
2. **确定性降级**：payload 组齐之后（`tool_causal_sources` / `tool_scope_sources` /
   `visibility_dependencies` 全都在里面了），`bound_terminal_payload` 按**真实字节数**
   判定；只有确实越过上限时才收缩 transcript。

降级算法：

- **预算是精确的，不是预留**。`canonical_json` 键序固定、分隔符固定，所以
  「整条 payload 的字节数」= 「messages 换成 `[]` 时的字节数」− 2 +「messages 数组自身的字节数」。
  于是 messages 的预算就是 `上限 − 空数组渲染长度 + 2`，一字不多一字不少。
  **能放下的就一个字节都不动**——这条至关重要：`context_page_in` 正是按这些字节分页的，
  一个 Memory 本来会受理的大 tool_result 绝不能因为「保守预留」被砍掉。
  （第一版用了保守预留：turn_id 按 512 最坏长度、每条 tool 事实预留 512 字节、
  再加 2048 零头。实测它把 `test_primary_context_pages` 里一条本来放得下的 transcript
  砍了，分页用例直接红——这正是「预留」不可接受的证据。）
- `bound_terminal_messages` 本身是纯函数，只依赖 `messages` 与预算。
- 先牺牲 tool_result（按字节数从大到小，同长按 ordinal），一旦落回预算内立即停止；
  tool 全部让完仍不够才动 assistant 正文。**`messages[0]`（USER 原文）永不改动**——
  `conversation_registration` / `primary_message_v2._pairs` /
  `primary_context_pages` 都拿它跟 USER 证据逐字节绑定。
- 被牺牲的正文换成**内容寻址的省略标记**
  `[deskpet:elided-tool-result sha256=<64hex> bytes=<n>]`。标记可复算，于是
  两个「必须逐字节相等」的比较点都改成 `transcript_matches`——逐条相等，
  或该条正文的 sha256 标记恰好相等，其余键（role/call_id/name）必须一字不差：
  ① `primary_context_pages` 校验「已结算的 SDK transcript == 归档的 transcript」；
  ② `record_terminal_observation` 幂等分支比较入参 messages 与既有观察的 messages
  （重放时降级会重新算出同一结果，但用 `transcript_matches` 表达更直接，
  也让「先写入、后来才需要降级」这种历史行都仍然可重放）。
  **没有放松任何完整性**。
- 角色、键集、`call_id`、`name`、ordinal 全部保留，所以 `representable()` 仍为真、
  contract 仍是 v2/v3、逐条消息 S1 与短期索引车道照旧成立。
- 实测事故形状（1 条 USER + 12 条 9 KB tool_result，74 KB）：只牺牲 6 条即落回上限内，
  耗时 1.3 ms；未超限时是恒等函数，一个字节都不动。
- **牺牲不管用就一条都不牺牲**：如果溢出的是 payload 的其余部分（例如极大的
  `tool_scope_sources`），把 transcript 全部省略也压不回上限，那就原样保留，
  由落库前的 SDK 校验记 `primary_terminal_observation_unadmissible` 外显。
- 发生降级时记一条 `primary_terminal_observation_degraded`
  （`sdk_run_id` / `elided_ordinals` / `message_count`），无 transcript 内容。

**取舍（明确记录）**：

- **不选「写入时报错拒绝落库」**：长回合的 Run 会永远无法结算，比现在更糟。
- **不选「改 envelope 形状」**（新增顶层「已降级」字段，或整条 payload 改走
  controlled blob ref）：`terminal_observation_tx` 按存下来的 payload 逐字节复算
  envelope，`primary_message_v2|v3.pairs` 用 `/messages/N` 指针把每条消息内联进
  各自的 S1，`conversation_registration` 与 `primary_context_pages` 也都按现有形状
  校验。改形状是需要与 Memory SDK 协同的契约变更 → **F-A6-1**。
- **代价**：被省略的 tool_result 正文不再进 Memory 车道，也不再能被
  `context_page_in` 分页读回（省略后长度低于 `DEFAULT_LARGE_RESULT_BYTES`，
  历史投影里不再生成摘要与分页引用，模型看到的是那句自描述标记）。
  完整正文仍在 SDK 的 Run transcript 与 Host 操作审计里。
  对比事故里的现状——**整条主对话不可读、该轮退化成只剩用户那句话、驱动永死**——
  这是严格更好的确定性降级。
- **兜底**：连全部降级也压不回上限时（例如 `tool_scope_sources` 自身超限），
  仍然落库并记 `primary_terminal_observation_unadmissible`（带 SDK 给出的稳定原因码），
  由 4.2 的读取侧守卫把影响限制在这一条源上。尺寸计算本身绝不成为
  「Run 无法结算」的新原因：预算算不出来就退回原行为。

### 4.2 (b) 读取点：不可受理的源只让自己不可见

`backend/deskpet/memory/primary_visibility.py`

- 新增 `assert_source_admissible(envelope, receipt)`：**直接调用 SDK 自己的**
  `validate_sanitized_evidence(envelope, receipt, supported_filter_policies=…)`，
  把 `MemoryValidationError` / `MemoryLimitError` 映射成
  `PrimaryVisibilityError("primary_visibility_source_unadmissible")`。
- 在 `_check.visit()` 组批**之前**逐条检查。`visit` 的 per-root 异常处理原本就把
  非 `primary_visibility_limit` 的 `PrimaryVisibilityError` 转成「该 root 不可见」，
  于是一条毒记录只让自己那一个 root 不可见，其余 root 照旧走真实 SDK 快照。
- `supported_filter_policies` 取 `human_memory_v7.HOST_SUPPORTED_FILTER_POLICIES`，
  与构造 Memory manager 时传进 SDK 的是**同一个常量**，生产上不可能漂移。

**为什么不是手写的 64 KiB 判定**（Task 6 评审 F-1）：第一版自己复算
`canonical_json` 字节数并放行 `{blob_ref, content_hash, byte_length}` 形状。
实测（离线探针）它会放行下列全部、而每一条都同样能复现失速：
`blob_ref` 写成 `blob:sha256:…`（SDK 只认 `memory-blob:` 前缀，不匹配就抛
`evidence_blob_ref_invalid`，不会退回内联）、`byte_length` 非法、5000 个节点、
深度 40、超大公开字符串、凭据边界键。守卫必须镜像 SDK 的**整条**受理规则，
唯一不会漂移的做法就是调它本人。

**这是 fail-closed 的**：只会让更多东西不可见，绝不会让任何 binding 变可见，
也没有跳过任何 disclosure / suppression / 策略检查。

### 4.3 (c) 可诊断性：被包住的真实异常必须能落到审计里

- `PrimaryVisibilityError(code, cause=None)` / `PrimaryReadError(code, cause=None)`
  带 `cause_type` / `cause_detail`；`foreground.runtime.failed` 新增
  `error_cause_type` / `error_cause_detail` / `attempt`，新增
  `foreground.runtime.stalled`（重试用尽，带 `attempts`）。
- **只有稳定码才留消息正文**（Task 6 评审 F-3）：`cause_fields` 现在要求异常
  ①有 `.code` 属性，或②是白名单里的 Host/SDK 稳定错误类型
  （`MemoryErrorBase` 及其子类、`MemoryLimitError`、`MemoryCorruptionError`、
  `EmbeddingError`、`TaskScopeProtocolError`）。其余一律只落 `error_cause_type`。
  否则最内层的 cause 可能是 `sqlite3.IntegrityError` 带整条 SQL、或 HTTP 库带
  URL/响应体，那是不该进持久审计行的东西。留下的消息仍然再过一遍
  `redact_credential_shapes` 并截到 200 字节。
- `foreground.runtime.failed` 的 `error_detail`（外层异常正文）也补了同一道
  凭据形状红线（原来是裸 `str(exc)[:500]`）。

同样的事故如果再发生，日志会直接写出
`error_cause_type=MemoryLimitError error_cause_detail=evidence_payload_requires_controlled_blob_ref`。

### 4.4 (d) 驱动不再永久死亡

`backend/deskpet/execution/foreground_runtime.py`

- 有界退避重试（4 次，0.5s→1s→2s→4s，上限 8s），用尽记一条独立的
  `foreground.runtime.stalled` 退出；任何一条退出路径都在锁内把 `self._driver`
  置空，下一次 `after_enqueue` 必定新建驱动。
- **重试计数只在真正安静之后才清零**（Task 6 评审 F-4）。第一版是「`_drive_once`
  返回 True 就清零」，于是「推进一步 → 抛异常」交替出现时 attempt 永远回到 0，
  退避退不上去，驱动会以 ~0.5 秒一轮永久刷 `failed` 审计——正是要消灭的空转。
  现在只在两个确定时刻清零：①退出前的 `_rewake_pending` 复查点
  （说明本轮已经完整地无进展跑完一遍），②距上次失败已安静地跑满
  `DRIVER_RETRY_COOLDOWN_SECONDS`(30s)。
- 退避等待改成等 `close()` 置位的 `_shutdown` 事件（`asyncio.wait_for`），
  关闭不必等满一次退避、也不必靠 `close()` 的 5 秒 timeout 去 cancel。
- **不做的事**：不把待处理 Run 显式判失败（毒记录与用户这一回合无关），
  也不无限重试（那正是短期索引车道犯过的错）。
- **既有用例的连带调整**：`tests/execution/test_revoked_scope_terminal.py::
  test_revoked_unclosed_scope_real_fallback[True]` 把「注入一次 `terminal.before_commit`
  崩溃 → 驱动永久退出 → 什么都没有半落库」写死进了断言。有界重试之后驱动会在
  第二次尝试里正常提交终态，这条用例要验的场景就不再发生。改法是在该用例里把
  `DRIVER_RETRY_ATTEMPTS` 固定成 1，精确保留它原本的语义；驱动的重试语义由
  `test_primary_foreground_runtime` 的专门用例覆盖。顺带确认：重试重跑那一次
  `_drive_once` 是幂等的——`task_scope_closure_receipts` 仍然只有 1 行，
  只是 closure fallback 的 `pending` 结算会被重复观察到。

### 4.5 (e) 短期索引车道：重放风暴止血

`backend/deskpet/memory/short_indexing.py`

- `assert_group_admissible(group)` 在 `register_group` 的**第一次写之前**校验
  该组所有 (envelope, receipt)（含 `terminal_source`），不可受理时抛
  `ConversationRegistrationUnavailable("short_group_source_unadmissible")`。
  于是「先把同一条 USER 证据摄入一遍、再在终态上抛错」变成一次确定性拒绝，
  `memory.evidence_ingestion_replayed` 归零。
- `reconcile()` 里也在扫描阶段判一次，把不可受理的组算作 `blocked` 行而不是
  中止整个扫描（评审 F-7）——否则一个坏 Run 会挡住它之后所有 Run 的索引。

`backend/deskpet/memory/short_index_worker.py`

- **决定：不加负缓存**（Task 6 评审 F-5，采纳评审建议）。第一版加了
  `_rejected` 有界负缓存，key 只覆盖 `registrations`、漏掉 `terminal_source`，
  于是**修好那条终态证据之后该组会被永久挡住**。止血的是上面那个「首次写之前」
  的断言，不是缓存；缓存只省掉每周期一次纯读的受理校验（事故形状实测 1.3 ms 级），
  却换来一整类过期 key 缺陷。删掉它，`short_index_worker.py` 除注释外回到基线。
  代价记录：不可受理的组每个轮询周期会重跑一次 `validate_sanitized_evidence`
  （`registrations_for_run` 本来每周期就要读一次，增量只有校验本身）。
- 顺带记录：既有的 `_confirmed` 正向缓存也只按 registrations 记 key、不含
  `terminal_source`，属于既有问题、本次未动 → **F-A6-4**。

### 4.6 (f) `current_user_denial` 不再把「不可受理」降级成 `None`

评审 F-7：当前这一轮 USER 源不可受理，不是「没有被证明的拒绝」，而是
「这个 Run 永远读不回自己的输入」。`_check` 新增 `propagate_unadmissible`，
`current_user_denial` 用它让稳定码外抛。这与既有契约一致——
`test_policy_infrastructure_failure_never_issues_permanent_rejection` 早就断言
基础设施故障时该方法抛 `PrimaryVisibilityError` 而不是返回 `None`。

## 5. 改动文件

| 文件 | 内容 |
| --- | --- |
| `backend/deskpet/execution/primary_history.py` | 写入侧确定性降级（`bound_terminal_payload` / `bound_terminal_messages` / `elided_content` / `transcript_matches`）＋落库前 SDK 校验；幂等分支比较改 `transcript_matches` |
| `backend/deskpet/memory/primary_visibility.py` | `assert_source_admissible`（调 SDK）；组批前逐条检查；cause 白名单；`propagate_unadmissible` |
| `backend/deskpet/memory/primary_read_model.py` | `PrimaryReadError` 带 cause |
| `backend/deskpet/execution/foreground_runtime.py` | 审计带 cause + attempt；有界退避重试（冷却后才清零、等待 `_shutdown`）；`stalled` 事件；退出必置空 `_driver`；`error_detail` 过红线 |
| `backend/deskpet/memory/short_indexing.py` | `assert_group_admissible` + `SOURCE_UNADMISSIBLE`；`reconcile` 把不可受理的组算 `blocked` |
| `backend/deskpet/memory/short_index_worker.py` | 仅注释：记录「不加负缓存」的决定与理由 |
| `backend/deskpet/execution/primary_context_pages.py` | 已结算 transcript 比对改 `transcript_matches`（接受可复算的省略标记） |
| `backend/tests/memory/test_primary_visibility.py` | +9 例（其中 1 例 6 参数化） |
| `backend/tests/memory/test_short_index_worker.py` | +4 例 |
| `backend/tests/execution/test_primary_foreground_runtime.py` | 改写 1 例 + 新增 3 例 |
| `backend/tests/execution/test_primary_history_outbound.py` | +4 例 |
| `backend/tests/execution/test_revoked_scope_terminal.py` | 连带调整：把 `DRIVER_RETRY_ATTEMPTS` 固定成 1，保留该用例原本的「崩在提交之前」语义 |
| `ARCHITECTURE/PROJECT_STATUS.md` | 事故与修复条目 |

## 6. 测试

### 6.1 新增/改写用例

| 文件 | 用例 |
| --- | --- |
| `tests/memory/test_primary_visibility.py` | `test_oversized_source_is_invisible_and_never_fails_the_batch`（毒 envelope 只让自己不可见，好的 root 仍走真实快照）<br>`test_oversized_source_reaches_the_sdk_batch_without_the_guard`（反向对照）<br>`test_real_sdk_batch_rejects_the_oversized_envelope`（对照：SDK 真的拒绝）<br>`test_structurally_doomed_sources_are_all_withheld`（**6 个参数**：超限内联、`blob:sha256:` 坏 blob ref、非法 `byte_length`、5000 节点、深度 40、凭据边界键；每例都先用真实 SDK 断言它确实被拒）<br>`test_admissible_sources_pass_the_guard`（正常、恰好等于上限、`memory-blob:` 合法 controlled ref）<br>`test_visibility_error_carries_payload_free_cause`<br>`test_cause_detail_only_survives_for_stable_codes`（`sqlite3.IntegrityError` 带 SQL+URL、裸 `RuntimeError` → 只留类型）<br>`test_cause_detail_is_redacted_and_bounded`<br>`test_read_error_reexports_the_visibility_cause` |
| `tests/memory/test_short_index_worker.py` | `test_unadmissible_group_is_blocked_before_any_write`<br>`test_unadmissible_group_never_writes_across_repeated_cycles`（评审 F-6：**监视 `register_group` 与 `assert_group_admissible`**，4 个周期各进入一次、写入次数恒为 0；删掉 `assert_group_admissible` 即红）<br>`test_corrected_terminal_source_is_retried`（registrations 身份不变、终态源修好后立刻恢复——这正是负缓存会挡住的场景）<br>`test_admissible_group_still_registers` |
| `tests/execution/test_primary_foreground_runtime.py` | `test_primary_driver_error_invalidates_without_publishing_exception`（改写：重试次数、cause 与 attempt、一条 stalled、退出后 `_driver is None`、异常正文仍不外发；评审 F-7：状态通知数放宽成区间，审计条数仍是精确值）<br>`test_primary_driver_recovers_after_a_transient_error`<br>`test_primary_driver_progress_then_raise_cycle_still_terminates`（评审 F-4：进展/抛错交替，冷却设 3600s，必须在 4 次后 stalled 退出）<br>`test_primary_driver_backoff_does_not_delay_close`（退避 30s 时 `close()` 仍在 2s 内返回且驱动不是被 cancel 的） |
| `tests/execution/test_primary_history_outbound.py` | `test_oversized_transcript_degrades_instead_of_poisoning_memory`（评审 F-7 要求的「走 `record_terminal_observation`」：真实前台 Run，助手正文超真实 64 KiB 上限，断言落库的终态观察通过 SDK 受理、省略标记复算等于已结算 transcript、USER 原文不动、日志无 transcript 内容、历史仍读得到这一轮、下一轮照常跑）<br>`test_transcript_degrade_prefers_tool_results_and_stays_verifiable`（降级策略本身：tool 优先、USER 永不动、纯函数可重放、不二次省略、伪造标记不匹配）<br>`test_residual_unadmissible_observation_is_reported_at_its_source`（兜底：降级修不好的 payload 仍落库但记稳定原因码，日志无 transcript 内容）<br>`test_terminal_payload_budget_is_exact_not_a_reserve`（放得下就一个字节不动；越限才降级——第一版保守预留砍掉了本来放得下的 transcript，直接把分页用例弄红） |

### 6.2 回归对照

同一批目录（`tests/execution tests/memory tests/operation_audit tests/faults tests/test_context.py`，
`-p no:randomly`），基线是 main `7cec5249` 的独立 worktree：

| | 失败 | 错误 | 通过 |
| --- | --- | --- | --- |
| 基线（`7cec5249`，独立 worktree，878.58s） | 116 | 12 | 878 |
| 改动后（884.88s） | 117 | 12 | 902 |

失败/错误集合逐条比对（`FAILED`/`ERROR` 行去重后 `comm`）：**基线 129 条全部原样保留，
零消失**；改动后多出**唯一 1 条**
`test_unscoped_search_late_forget::test_unscoped_search_rechecks_forgotten_source_before_second_delegate[create]`。
单独跑该文件，**基线与改动后同样 1 failed / 2 passed**——它是既有的顺序/环境相关抖动，
不是本次改动引入的（基线整跑里恰好碰上了让它通过的顺序）。

通过数 +24 = 新增的 25 个用例项减去上面那 1 条抖动。整跑耗时 878.58s → 884.88s
（+0.7%）：读路径每条 envelope 多跑一次 SDK 的 `validate_sanitized_evidence`，
代价在噪声范围内。

已知的环境既有红（与本次改动无关）：short-horizon embedder 缺失导致的
`test_short_index_worker` 11 例、`test_primary_decisions`、`test_foreground_runtime` 的
时序 `CancelledError` flake、`test_primary_visibility::test_real_memory_only_forget_…`、
`test_primary_history_outbound[sent_unknown]`、`/Users/denny` 路径用例等。

命令：

```
PYTHONPATH=$PWD python -m pytest tests/execution tests/memory tests/operation_audit \
  tests/faults tests/test_context.py -q -p no:randomly
```

## 7. 独立评审与处置

| # | 评审意见 | 处置 |
| --- | --- | --- |
| F-1 | 手写的守卫只复刻了 64 KiB 一条规则；探针证明它放行坏 `blob_ref`、5000 节点、深度 40、超大公开串、凭据边界，每条都能复现失速。应改调 SDK 的 `validate_sanitized_evidence`（把 receipt 穿进去），映射 `MemoryValidationError`/`MemoryLimitError`，保持 fail-closed | **已采纳**。`assert_inline_admissible(envelope)` → `assert_source_admissible(envelope, receipt)`，内部就是 SDK 那个函数；receipt 在 `visit`（`read_evidence_pair` 的返回值）与 `assert_group_admissible`（`registration.admission_receipt` / `terminal_source[1]`）两处本来就有。§4.2 |
| F-2 | blob-ref 提前返回被 F-1 覆盖；测试里把 `blob:sha256:` 当作可受理是错的 | **已采纳**。正例改用 `memory-blob:…`，并新增坏 `blob_ref` 与非法 `byte_length` 两个反例（先用真实 SDK 断言它们确实被拒） |
| F-3 | `_cause_audit_fields` 不该输出最内层 cause 的消息正文，除非它有稳定 `.code` 或属于白名单类型 | **已采纳**，实现放在唯一的产出点 `cause_fields`（`_cause_audit_fields` 与两个异常类都经它）。新增 `test_cause_detail_only_survives_for_stable_codes` |
| F-4 | `attempt = 0` 一有进展就重置 → 进展/抛错循环会以 ~0.5s 一轮永久刷审计；应只在完全安静的一遍之后或经过冷却才清零。（nit）退避应观察 `_closed` | **已采纳**，两条都做：清零点改为 `_rewake_pending` 复查点 + 30s 冷却；退避改成等 `close()` 置位的 `_shutdown` 事件。新增两个用例 |
| F-5 | `_rejected` 负缓存的 key 漏掉 `terminal_source`，修好终态源后该组永久被挡；建议整个删掉（首次写之前的断言已经止血），或把 key 补全；`_confirmed` 同理 | **已采纳评审建议：删掉**。理由与代价见 §4.5，并留注释在源码里；`_confirmed` 的同类问题记为 F-A6-4 |
| F-6 | 负缓存的两个测试是空的（删掉缓存也过）；应监视 `register_group`/`assert_group_admissible` | **已采纳（按新设计重写）**。缓存删掉后「恰好调用一次」不再是正确的期望；改为监视两者、断言 4 个周期各进入一次而**写入次数恒为 0**（删掉 `assert_group_admissible` 即红），并补 `test_corrected_terminal_source_is_retried` 正面证明「不加缓存」修掉了 F-5 那个缺陷 |
| F-7 | （nit）`current_user_denial` 对超限的当前 USER 应外抛而非返回 `None`；`short_indexing.reconcile` 应把不可受理的组算 `blocked` 而不是中止；补凭据形状的 redaction 测试；`test_primary_history_outbound` 走 `record_terminal_observation`；放宽 `len(calls) == DRIVER_RETRY_ATTEMPTS` 的时序断言 | **全部已做**：§4.6、§4.5、`test_cause_detail_is_redacted_and_bounded`、`test_oversized_transcript_degrades_instead_of_poisoning_memory`（真实前台 Run 走写入点）、`1 <= len(calls) <= DRIVER_RETRY_ATTEMPTS`（审计条数仍精确断言） |

## 8. Follow-up

- **F-A6-1（需要 SDK 协同）**：终态观察的 `messages` 应改为 controlled blob ref
  或逐条消息引用，让 payload 天然有界、不必牺牲 tool_result 正文。
  在此之前，超长回合里最大的几条 tool_result 在 Memory 车道与
  `context_page_in` 分页里只剩自描述的省略标记。
- **F-A6-2**：`primary_read_model.py` 里 `raise PrimaryReadError(str(exc))` 把异常消息
  当稳定码用，属于既有问题，本次未动。
- **F-A6-3**：`assert_source_admissible` 的 `supported_filter_policies` 取的是 Host 常量
  `HOST_SUPPORTED_FILTER_POLICIES`（与构造 manager 用的是同一个），生产上不会漂移；
  但测试里用子集 fixture 的后端仍可能比守卫更严。要彻底消除，需要把实际的
  policy 集合从 manager 穿到 `PrimaryHistoryPolicy`。
- **F-A6-4**：`short_index_worker._confirmed` 正向缓存的 key 同样只含 registrations、
  不含 `terminal_source`（既有问题，本次未动）。
- **SDK 侧结论**：**重跑 A6 不需要 SDK 改动**。SDK 行为符合契约，Host 侧修复后
  已被污染的库也能自愈（超限那一条自动退化为不可见，其余照常；新写入的
  终态观察一律先降级到可受理）。

  是否建议 SDK 也在**摄入时**拒绝？——它已经在拒绝了：
  `admit_evidence_source` / `ingest_committed_evidence` 都走同一个
  `validate_sanitized_evidence`。真正的缺口不在 SDK：Host 的 S1
  （`human_memory_evidence` 表）是 Host 自己的存储，SDK 在写入那一刻根本不在调用链上，
  没有任何位置可以拦。所以「写入时校验」只能由 Host 做，也就是 §4.1。
  唯一值得向 SDK 提的是能力增强（F-A6-1 的 controlled blob ref 写入辅助），
  不是重跑前置条件。
