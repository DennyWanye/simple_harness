# 任务书 E2（给 gpt-6.1-sol / Codex）：任务书 E 的补修

日期：2026-11-01。发包人与审核人：Claude 会话。在**同一工作树** `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness-sol-E`、同一分支 `sol/E-authorization-goal` 上继续，基线是你的 `f8d798ca3`。先 `cd /Users/taiwan/PROJECTS/SimplaHarness/simple_harness-sol-E/sdk/simple-harness-sdk`。

## 0. 审核裁定（先看这个）

你记录里的 BLOCKED 冲突，审核人裁定如下，照此改：

1. **`Session.close()` 恢复原语义："只放手、任务继续"。** 删掉 `force` 参数与 `active_work` 拒活；`close()` 仍像 D 之前那样释放资源、取消后台协程，排队中的输入与未消费结果留在账本等重开。合同里 SDK-14 的"有活拒绝关闭"落在 **`archive()`** 上（它本来就要求无未终态委派、无未消费结果、无未收口 Goal，再加上无 `queued / running` 的排队输入与 `pending / queued` 的续办）。能力发现 `archive` 保留；文档"授权回调、取消范围、Goal、恢复报告"一节里把这条口径写清（"close 是放手，archive 才是收口"）。新测试 `test_owner_and_close.py` 里凡是断言 `close()` 报 `active_work` 的，改成断言 `archive()` 报 `active_work`、`close()` 正常放手且重开后责任仍在。
2. **旧取消测试按新屏障语义改断言。** `test_session.py::test_cancel_creation_then_recover_and_completed_is_immutable`：取消一条尚未派发的委派后，恢复**不再**创建 Mission（屏障后不派新活），委派终态 `CANCELLED`、`mission_id` 为空、`host.cancelled == []`。改这条断言是审核人授权的"修过时断言"，在记录里逐条列出。
3. 这两条改完，§7 的指定八文件必须全绿（不许再留"精确失败事实"）。

## 1. 审阅子代理的阻断级（必须）

两条都是"实现对了、测试没兜住"：审阅者在副本里把对应源码删掉，108 条新测试照样全绿。

1. **拿锁后、标 running 前的屏障检查没有测试。** `session.py` `_run_inputs` 里两处 `_input_cancelled` 检查，删掉后新测试全绿——现有 `test_barrier_is_checked_after_send_lock_before_queue_claim` 靠 `cancel()` 的收敛先把 `queued` 行标成 cancelled，根本走不到那两处。只有这两处能挡住的场景：屏障之前已经是 `running` 的续办批次（崩溃/重开后再跑）。补测试：先拿住 `_send_lock`，`tick()` 排入一条续办，手工把它改成 `running`，再 `cancel("session_tree", …)`，放锁；断言模型调用 0 次、状态 `cancelled`。
2. **"委派建 Mission 前"的授权重核验没有测试。** `delegation.py` `_create` 里的 `ensure_sync("delegate", …)` 删掉后全绿。补测试：委派已登记但 Mission 未建（`delegation_recorded` 处崩溃）→ 宿主 `revoke_authorization(grant_ref)` → 重开 `recover()`；断言 `host.made` 为空、`held_reason == "authorization_revoked"`。同类：`child_send` 受理时的授权删掉也全绿，补一条 deny 的测试（受理即拒、不入队）。

## 1b. 审核人另外拍板的三件（一起改）

- **人工模式（`auto_mode=False`）下默认授权端口**：`accept_input / create_child / delegate` 一律 `allow`，只有 `connector_effect` 走 `needs_approval`。理由：产品口径"manual 只逐次确认动作"，收一句话、开一个工作会话、把工作交给后台都不是对外效果，不该弹卡片。改 `AutoModeAuthorization.decide` 与对应测试，文档写明。
- **`cancel("session_tree", …)` 不是永久封死根会话**：屏障只管屏障之前已存在的活（委派、续办、排队输入、Goal）；屏障序号之后用户的新输入照常受理、照常可以再委派。把钉住"取消整棵树后再也不能受理"的测试（`test_real_model_cancel_command_reaches_scope_barrier`）改成"屏障前的活全部收敛、屏障后新输入照常跑"。
- **owner 代次暴露的测试补强**：重开一次后 `host.status()["owner_generation"]` 与 `capabilities()` 都应为 2，别只在等于 1 时断言。

非阻断一起做：`archive()` 的三条拒绝路径（未终态委派 / 未消费结果 / 排队输入）各补一条测试（C1 迁过来时顺手）；`InstanceLock.held` 每次 fstat + 读代次文件 + 另开 fd 试锁，挂在每个 `_check_open` 上开销偏大——同一进程内把探测结果缓存 0.5 秒（失锁最多晚 0.5 秒发现，记录里写明取舍）。

## 2. 规范轴审阅的阻断级（必须）

### 2.1 授权只留异步一条路，删掉 `ensure_sync`

现状：`ensure_sync` 在新线程里 `asyncio.run` 跑宿主端口，再 `queue.get(timeout)` 阻塞主事件循环（实测端口耗时 2 秒就卡循环 2 秒；端口若用宿主循环上的对象——等卡片回答的 future、`httpx.AsyncClient`——在另一个线程的新循环里直接失败，同步路径立刻返回 `authorization_required`）。pump 每 0.25 秒经 `recover() → _create` 再问一次，会反复卡顿。同一个端口两种调用方式，是"同一件事两条路"。

改法：
- `runtime/tool_gateway.py` 两个调用控制命令的地方（`execute` 约 767 行、`observe` 约 603 行）：`command.invoke(...)` 的返回值若可等待就 `await`（它们本身已是 async）。**本任务书放宽车道允许改这两处。**
- `Delegations.delegate / _create / recover`、`Session.child_send`、模型发起的 `create_child` 改成 async，统一走 `ensure`；调用方（`_pump`、`_send`、`realtime_bridge._dispatch`、命令处理函数、测试里直接调 `session._delegations.delegate(...)` 的地方如 `test_chat_cancel_recovery.py`）跟着 `await`。
- **删掉 `ensure_sync`**。文档里"端口不应依赖宿主事件循环绑定的对象"那句删掉——现在可以依赖了。
- 测试：用一个"等 0.1 秒后在宿主循环上放行"的端口（future）跑模型发起的 `delegate_task`，断言放行且事件循环最大阻塞 < 50 毫秒（可用监控协程打点）。

### 2.2 取消屏障收不了口时不许每轮重写落盘

现状：屏障停在 `cancel_requested`（如交接结果未知）时，`_converge_cancellations` 每轮无条件重写同一行（实测每秒 28 次落盘，永不停止）；`continuation.py` 对每条"取消后迟到的结果"每轮也重写一次。改法：状态没变就不写；`_converge_cancellations` 被 pump、各会话 `recover`、`_send` 重复调用——合并为每个根会话每轮最多跑一次。测试：一条交接未知的委派，闲置 3 秒内账本写入次数 ≤ 2。

### 2.3 三条一起改

- **恢复顺序**：`dispatch_state="claimed"` 的行恢复时先只读 `find_mission` 一次，查到就直接关联（不再问授权），查不到才走授权再建。
- **`create_child` 两条入口的授权口径统一**：模型路径与公开接口都用 `{"child_key","purpose"}` 算参数摘要、都取根 Goal 当前 ref 并冻结进 `children` 行；一边批准另一边有效。
- **`_root_session()` 不得悄悄新建根 Session**：找不到根句柄或根已关闭时抛 `SessionError("session_closed")`（或 `not_found_or_forbidden`），不开新账本连接；`_session_handles` 在会话关闭时清理。加测试：根关闭后子会话操作报错而不是多出一个连接。

欠账（本轮不做，记录里写）：`session.py` 新增 500 多行、`_converge_cancellations` 约 120 行把匹配/审批/委派/语音/效果/收口混在一起，后续拆成独立的取消模块。

## 2b. 完成标准与记录

- 以下文件全绿（改源码后先重生成清单；语音文件要 `--extra realtime`）：四个新文件 + `test_session_tree.py`、`test_continuation.py`、`test_session.py`、`test_session_replay.py`、`test_host_sessions.py`、`test_host_connectors.py`、`test_host_recovery.py`、`test_session_async.py`、`test_realtime_bridge.py`、`test_chat.py`、`test_chat_cancel_recovery.py`、`test_tool_catalog.py`、`test_orchestration_host.py`、`tests/test_public_api.py`、`tests/orchestrator/step08/test_policy_snapshot.py`。
- 改到的文件 `ruff check` / `ruff format --check` 绿。
- 记录末尾追加一节"E2 补修"：改了什么、测试命令与通过数、提交号。中文。拿不准的按最合理做法做完，不停下问。

## 3. 不动的

命令表只有 `cancel`；池编号、工具说明、任务层、工具层、连接器协议不动；不加兼容分支；不提交部署清单；不推送、不合并、不跑全量回归。
