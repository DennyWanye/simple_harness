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

## 2. 一起改的

1. `host/authorization.py` 的 `ensure_sync`：同步命令路径里的授权要么只允许默认 `AutoModeAuthorization`（宿主自定义端口一律走异步 `ensure`），要么把等待上限收到 1 秒并在记录里写明"同步路径会阻塞事件循环"——按审阅结论定，默认选前者。
2. 记录末尾追加一节"E2 补修"：改了什么、测试命令与通过数（指定八文件 + 四个新文件全绿）、提交号。中文。拿不准的按最合理做法做完，不停下问。

## 3. 不动的

命令表只有 `cancel`；池编号、工具说明、任务层、工具层、连接器协议不动；不加兼容分支；不提交部署清单；不推送、不合并、不跑全量回归。
