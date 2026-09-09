# 事件 AK 决策备忘：同 userdata 重启无法到达可发送态

- 日期：2026-09-09
- 触发：两轮完整流程旅程（S6 Task 7 / HM-AC-8 真 UI），`plans/2026-09-09-two-flow-journey/00-PLAN.md`
- 证据根：`.local-test-evidence/2026-09-09/twoflow-run1/`
  - 第一段进程 `primary-ui-lc1dpujx/`（flow1 T1–T11 全 COMPLETED，`twoflow-driver.log` / `twoflow-progress.jsonl` / `userdata/`）
  - 第二段进程 `primary-ui-o7npp9jv/`（`--userdata <第一段>/userdata` 重启，`native.log` / `launch.json`）
- 现象：重启后后端打了 `product_sdk_runtime_ready`、`workflow_service_ready`、`reconcile.recovered`、
  **31+ 条 `memory.evidence_ingestion_replayed`**、`companion_projection_history_closed_identity_unready`
  （`native.log:67`）；WebView 显示「已连接」，主对话却始终停在「等待主对话就绪」超过 3 分钟，
  flow2 两次发送都失败（`no new Run head after two sends`，`foreground_run_heads` 停在 11）。全程无报错、无 traceback。

---

## 1 现场重建

### 1.1 「等待主对话就绪」是什么

- 文案在 `tauri-app/src/views/PrimaryChatView.tsx:49`：`canSend = snapshot.ready && snapshot.state !== null`，
  为假时状态行就是这句；`tauri-app/src/code-panel/InputBar.tsx:647` 同源。
- `snapshot.ready` 的**唯一**来源是控制通道上的 `companion_profile_bound`
  （`tauri-app/src/primary/controller.ts` 的 `onMessage`，`companion_profile_bound` 分支才 `update({ ready: true })`
  并触发 `refresh()`）。
- 该帧由后端 `/ws/control` 在处理签名 `companion_profile_bind` 之后下发
  （`backend/deskpet/companion/control_ingress.py:1120` 生成 payload，`backend/main.py` 的 `/ws/control` 处理器发送）。

### 1.2 后端在重启后到底做到了哪一步

按 `primary-ui-lc1dpujx/userdata/data/companion.db` 只读取证（先复制含 `-wal`/`-shm` 再 `?mode=ro`）：

| 表 | 事实 |
| --- | --- |
| `profile_bindings` | `legacy_local_profile/1`，`binding_epoch=1`，**`status='ready'`**，`updated_at=2026-09-09T05:53:37.612Z` |
| `profile_control_commands` | 只有两行 `companion_profile_bind`：`05:34:54.367Z`（第一段）与 **`05:53:37.612Z`（第二段）** |

控制通道在 `native.log:73-75` 于 `05:53:37.566` 连上（`requested_scope=identity_bind`），**46 ms 后耐久绑定就已经
写成 `ready`**。也就是说：`ProfileBindingCoordinator.bind()`
（`backend/deskpet/companion/identity.py:198-300`，重启走 `first_process_bind=True` → `runtime.recover()` →
`start_prebound()` → `mark_profile_binding_ready()`）**全部成功**。

失败发生在它之后、就绪帧之前的那一段。`backend/main.py` 的 bind 处理链在 `coordinator.bind()` 返回后还要：
`_companion_identity_gate.freeze()` → `await _ensure_companion_inbox_route(...)` →
`await _companion_notification_service.bind_and_drain(...)` → 最后才 `await ws.send_json(response)`。
中间两个 await **完全无界**，异常被 `companion_projection_bind_drain_failed` 兜住，但**卡住不是异常**：
日志里既没有 `companion_control_rejected`，也没有 `companion_projection_bind_drain_failed`，
更没有 `harness_recovery_triggered_identity_ready` —— 一条都没有，正是"卡在无界 await 上"的签名。

### 1.3 `companion_projection_history_closed_identity_unready` 不是根因

`backend/main.py` 的 `_initialize_companion_projection_services()` 在启动时尝试 `identity_gate.freeze()`，
而身份门要等主窗口那次**签名 bind** 才可能 freeze，所以**每一次启动都会打这条**。
比对可证：第一段进程 `primary-ui-lc1dpujx/native.log:72` 同样打了这条，而 flow1 T1–T11 全部成功。
它只表示「闭合历史投影在等 `companion_profile_bind`」，不表示依赖缺失。本轮把它补成自描述收据（见 §3.3）。

### 1.4 31 次重放 = Host 短时索引的静默死循环（真正的根因）

`memory.evidence_ingestion_replayed` 来自 SDK 的 `_verify_replay`
（`backend/.venv/.../simple_harness_memory/backends/sqlite_v5.py:19836-19846`）——它只在
**同一条 evidence 已经摄入过**时打印并原样返回旧回执，SDK 侧本身是幂等的。所以重放的成因在 Host。

对 `primary-ui-o7npp9jv/native.log` 逐条统计：

- 共 **38** 条重放，只有 **11** 个不同 envelope；
- 前 11 条（`05:54:10.698`–`05:54:33.676`）各不相同，恰好等于 `state.db.memory_ingestion_outbox` 的 11 行；
- 从 `05:54:38.134` 起，只剩 `3652e751…` 与 `eb101ead…` **两条交替**，间隔 6 s / 8 s，
  一直持续到日志末尾 `05:57:38.898`（各 15 / 14 次）。

排除投递外发箱：`state.db.memory_ingestion_outbox` 11 行全是 `state='delivered'`、`attempts=1`，
`updated_at` 最大值落在 `05:52:26`（重启之前），重启后**一次都没动过**。

定位到 `backend/deskpet/memory/short_index_worker.py` 的 `PrimaryShortIndexWorker.step()`：

```python
async with asyncio.timeout(self.operation_timeout):      # 5.0 s
    group = await self.authority.registrations_for_run(run_id)
    ...
    await service.register_group(group)                  # 摄入 + 受理终态源 + 注册 ack（三次 SDK 写）
    pending.append(key)
except (ValueError, TypeError, RuntimeError, TimeoutError) as exc:
    blocked.append((run_id, getattr(exc, "code", "short_group_unavailable")))
```

- 组的确认缓存 `_confirmed` **只在 `pending` 里的 key** 上写入，而 `pending` 只在 `register_group` 完整成功后才追加；
- `register_group` 一旦超过 5 s（重启冷启：embedder 预热 51 s、SDK 写道争用，实测单组 6 s 以上），
  抛 `TimeoutError` → 落进 `blocked` → **key 永远进不了 `_confirmed`**；
- 下一趟整表重扫又轮到它，于是**再摄入一次同一条 USER 证据、再超时**——每趟一次，永不停；
- 这条路径**一行日志都没有**：`blocked` 只是返回值，`MemoryAnalysisLane.tick()` 不打印它，
  所以现场只看得到 SDK 侧的重放，看不到是谁在重放。

第一段进程也中招：`primary-ui-lc1dpujx/native.log` 从 `05:36:50` 起同一条 `3652e751…` 每 ~7.7 s 复现一次直到进程结束
（117 条重放 / 11 个不同 envelope）。只是 flow1 时它还只吃掉一部分写道预算，表现为
`twoflow-driver.log` 里 T4/T5/T6/T7/T10/T11 六轮都要「second send（retried）」。
重启后要重扫的组变多、embedder 还在冷加载，占空比升到足以饿死前台，于是 flow2 直接一轮都进不来。

这与 HM-TO-A6（`plans/2026-09-08-hm-to-a6/DIAG-RECALL-TIMEOUT.md`）**同类**：那次已经为
「payload 超过内联上限的组每周期重摄入一次」做过止血（`short_indexing.py` 的 `assert_group_admissible`），
但只堵了一个具体成因，**通用的超时路径仍然敞着**。

### 1.5 离线复现（去掉一切现场噪声）

`backend/tests/memory/test_short_index_restart_replay.py` 用生产 fixture 造 5 轮真实 userdata，
然后让第一个组「先真实摄入、再超时」。**修复前**：8 趟扫描 = 8 次重摄入（每趟一次，不收敛，零日志）。
**修复后**：8 趟 = 2 次（第一次失败立刻重试，第二次起进入退避窗口）。

---

## 2 决策（无需再问，按 delegate-decisions 口径直接定）

| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | 单组注册**独立预算** `registration_timeout=30 s`，与纯读扫描的 `operation_timeout=5 s` 解耦 | 注册是「读 + 三次 SDK 写」，与扫描差一个数量级；预算比实测（6 s）还小，等于把死循环写进设计 |
| D2 | 失败的组按 `host_run_id` 做**有界指数退避**并打稳定码日志 | 去掉的是**这一类**而不是这一个成因：任何原因都不可能再形成"每趟静默重摄入" |
| D3 | **第一次失败不退避** | 丢一次 ack 必须下一趟就重放，这是 `test_unknown_ack_reopen_replays_real_refs` 钉死的既有语义；从第二次连续失败起才是"循环" |
| D4 | `ConversationRegistrationUnavailable` **不进退避** | 它全部发生在第一次写之前，不会重摄入任何证据；迟到的 outbox 投递必须下一趟就被接住（`test_low_sequence_late_delivery_*`） |
| D5 | bind 收尾链**有界**（10 s），超时降级成稳定码，**就绪帧照发** | 就绪的耐久事实是 `profile_bindings.status='ready'`，闭合历史抽干只是补历史；未抽干的通知由 `wake_repair` 追平。宁可"可用 + 明说历史未补齐"，也不要"静默永远不可用" |
| D6 | 不在启动时预热闭合历史投影 | 会需要在没有签名 bind 的情况下伪造 `FrozenOwnerIdentity`，破坏身份门的 fail-closed 语义；D5 已经把等待时间上界钉死，收益不值这个风险 |
| D7 | 前端把降级稳定码**持久显示**，并把 `companion_control_error` 显出来 | 「fail loudly」的最后一公里在 UI；此前 bind 被拒时前端完全无声 |

---

## 3 改动

### 3.1 `backend/deskpet/memory/short_index_worker.py`（根因）

- 新常量：`SHORT_INDEX_MEASURED_GROUP_REGISTER_MS=6000`、`SHORT_INDEX_REGISTRATION_TIMEOUT_SECONDS=30`、
  `SHORT_INDEX_GROUP_BACKOFF_SECONDS=30`、`SHORT_INDEX_GROUP_BACKOFF_CAP_SECONDS=600`、
  `SHORT_INDEX_GROUP_TIMEOUT_CODE`、`SHORT_INDEX_GROUP_BACKOFF_CODE`。
- `step()` 拆成两段预算：`registrations_for_run` + 缓存命中判定仍在 `operation_timeout` 内（纯读），
  `register_group` 单独用 `registration_timeout`。
- 新增 `_group_backoff`（按 `host_run_id`，`(连续失败次数, 允许重试的单调时刻)`）：
  窗口内**一行不读一次不写**，直接 `blocked` 计 `short_group_retry_backoff`；
  任何一次成功（含缓存命中）立即清零 —— 它不是负缓存，修好的组不会被永久挡住（沿用 Task 6 评审 F-5 口径）。
- 新增 `log.warning("memory_short_index_group_blocked run_id=… code=… failures=… retry_in_s=…")`。
- `ShortIndexStep` 增补可观测量 `groups_backed_off`。
- 构造校验加 `registration_timeout >= operation_timeout`、`group_backoff_cap >= group_backoff`。

### 3.2 `backend/main.py`（就绪信号有界）

- 新增 `_settle_companion_bind_projection(frozen_identity, *, ensure_route, notification_service, timeout_seconds=10)`：
  在 `asyncio.timeout` 内跑 `_ensure_companion_inbox_route` + `bind_and_drain`，返回 `(session_id, failure_code)`，
  **永不抛**；超时打 `companion_projection_bind_drain_failed code=companion_projection_bind_drain_timeout`。
- `/ws/control` 的 `companion_profile_bind` 分支改用它；失败时在 `companion_profile_bound` 的 payload 里
  带上 `projection_degraded_code`，**就绪帧无条件下发**。

### 3.3 `backend/main.py`（收据）

`companion_projection_history_closed_identity_unready` 补上结构化字段：
`reason` / `code` / `durable_binding_present` / `durable_binding_status` / `awaits="companion_profile_bind"`。
下一次现场一眼能分清「在等签名 bind（正常）」和「依赖真的缺失」。

### 3.4 `tauri-app/src/primary/controller.ts`（UI 可读）

- `companion_profile_bound` 带 `projection_degraded_code` 时，进入 ready 的同时把
  「历史补读未完成（<code>）；可稍后刷新状态。」写进 `notice`，且**成功读取不会把它抹掉**（`readNotice()`）。
- 新增 `companion_control_error` 分支：未就绪时把稳定码写进 `error`（已就绪时不覆盖读态）。

---

## 4 测试

| 文件 | 用例 | 钉住什么 |
| --- | --- | --- |
| `backend/tests/memory/test_short_index_restart_replay.py` | `test_restart_reaches_ready_and_replays_each_evidence_at_most_once` | 同 userdata 重启后到达 wrapped 稳态、全部组确认；每条已提交证据**至多重摄入一次**；稳态后再扫 4 趟**零摄入**；新一轮仍被扫到 |
| 同上 | `test_registration_timeout_is_bounded_and_logged` | 注册超时 → `short_group_registration_timeout` + `memory_short_index_group_blocked` 日志；第一次失败不退避、第二次起退避窗口内零摄入；窗口过后必须重试 |
| 同上 | `test_registration_budget_covers_measured_group_cost` | 一致性用例：`registration_timeout >= 5 ×` 实测单组成本（体例同 HM-TO-A6 的「预算 × 实测 ≤ deadline」） |
| `backend/tests/companion/test_restart_identity_ready.py` | 5 例 | 收尾成功带回 session_id；路由/抽干卡死都在预算内降级成稳定码；依赖错误投影出自己的 code；源码序断言「有界收尾 → 无条件发就绪帧」 |
| `tauri-app/src/primary/controller.test.ts` | 新增 3 例 | 降级就绪帧仍进 ready 且稳定码持久可见；bind 被拒时显示稳定码；已就绪后不被控制错误覆盖 |

结果：后端新增 8 例全绿；`tauri-app` `src/primary` + `src/auth` 共 104 例全绿、`tsc -b --noEmit` 干净。

**基线口径**：`tests/memory/test_short_index_worker.py`（11 failed / 5 passed）与
`tests/memory/test_memory_ingestion_outbox.py`（8 failed / 10 passed / 1 skipped）在**主干与本 worktree 完全一致**，
失败集合逐条 diff 相同，原因均为本机 venv 缺 WeMM 权重
（`MemoryValidationError: short_horizon_embedder_required`），与本次改动无关。
新增用例因此只替换向量重建三件套，摄入 / 受理 / 注册 ack 全部走真实 SDK 写路径。

---

## 5 遗留

- **F-AK-1**：本轮只在 Host 侧把重放变成有界；`MemoryAnalysisLane.tick()` 仍不上报
  `ShortIndexStep.blocked`/`groups_backed_off`。建议下一轮把这两项接进现有可观测面。
- **F-AK-2**：`SHORT_INDEX_MEASURED_GROUP_REGISTER_MS = 6000` 取自本次现场日志的间隔推算（6–8 s），
  不是专门的基准。若后续要再调 `registration_timeout`，必须先补一次 `x2_memory_lanes_growth.py` 式的实测。
- **F-AK-3**：本轮未做真 UI 复验（需要重建前端 bundle，见报告"是否需要重建 bundle"）。
  重跑 S6 Task 7 时应确认：重启后 `native.log` 出现的重放条数收敛，且
  `memory_short_index_group_blocked`（若有）可见。
