# Task 12 Durable 通知、摘要与主消息页 UI 验证结果

> 日期：2026-07-25
> 结论：Task 12 的实现、自动化门、独立完整性审计与真实 Tauri 主消息页 E2E 均通过。
> Companion authority 仍保持 `legacy/generation=1`，最终 writer/runtime 切换由 Task 13
> 的可恢复 cutover saga 统一完成。

## 1. 本阶段完成范围

- `CompanionNotificationProjectionService` 以稳定
  `profile_id + profile_generation + notification_id` 作为逻辑 inbox，把 Companion
  notification/outbox 投影到当前可信主消息 Session。缺 route 时保持 pending，route
  改变时 relocation，不复制第二行；投影异常会立即释放精确 claim，允许同一 outbox
  无需等待 lease 到期即可重试。
- 重要激活、评估、回滚、遗忘事实即时生成通知；普通偏好变化进入确定性日桶摘要。
  Reminder 投递和通知落卡位于同一事务，重复消费不会重复落卡。
- owner generation 删除会先写 `owner_deleted` 审计，再把旧通知标记为
  `superseded`、清空动作入口并 dead-letter 待处理投影，旧卡不会转移给新 generation。
- SessionDB 支持 owner-fenced append、relocate、redact 和 clear 后 absent replay。已
  redacted notification 只能重放固定 tombstone，旧 summary/actions/detail 不会复活。
- live/history 共用同一 Companion envelope；history 在返回正文前重读 Companion 当前
  hash/status。Store 不可读、owner stale 或 generation 不匹配时 Companion 行 fail
  closed，普通聊天历史不受影响。
- `companion_event` 在 FTS、vector/backfill、chunker、retriever、summarizer、
  reflection 和 QASet 链路均保持 `context_visibility=exclude`。
- 唯一只读 `companion_detail_get` 从 owner-scoped 权威表重建 overview、evidence、
  diff、evaluation、decision、operation receipt、current binding 与 audit，不信任通知
  预填详情。请求只有五个业务字段；游标 canonical HMAC 绑定 owner、control epoch、
  notification、section、detail version 与 last sort key。
- 详情读取由 Companion `Vc` 和完整 Platform `VpVector` 前后双读包围。无 Platform key
  的详情使用确定性空向量，不依赖无关 Catalog readiness；任何 required key token 或
  visible version 改变都返回 `detail_changed`，响应统一经过 `TraceRedactor`。
- 主消息页新增 owner-generation reducer、成长卡片、详情 modal、分页和 stale response
  fence。retract 会清除 actions/detail cache；owner 切换只清 Companion 卡，不清普通消息。
- evaluation、activation、rollback、forget 和 action confirmation 均由 typed
  production service 处理。外部动作只发送 `{decision_id,allow}`；evaluation 与
  activation nonce 不可跨域复用；可执行激活还要求独立
  `persistent_local_code_no_os_sandbox` ack，Auto 模式不能绕过。
- 修复真实冷启动时的 identity challenge 竞态：App 在 control listener 注册后消费缓存的
  challenge，并为 Relay identity restore 提供有界重试；`ControlChannel` 的 latest
  message cache 按连接清空，避免跨连接复用旧 challenge。

## 2. 自动化门

### Backend

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/companion/test_notifications.py `
  backend/tests/companion/test_detail_query.py `
  backend/tests/companion/test_control_decision_services.py `
  backend/tests/companion/test_window_control_credentials.py `
  backend/tests/companion/test_store_transactions.py `
  backend/tests/companion/test_store_evaluation_activation.py `
  backend/tests/companion/test_reminders.py `
  backend/tests/companion/test_activation_saga.py `
  backend/tests/companion/test_activation_platform.py `
  backend/tests/test_context.py `
  backend/tests/test_memory_companion_projection.py `
  backend/tests/test_memory_v19_projection_visibility.py `
  -q
```

结果：`126 passed in 27.96s`。

监控根 PID `25516`，create-time
`2026-07-25T16:44:35.0651513+08:00`，exit `0`，tracked `3`，释放 private
memory `885190656` bytes，`survivor=0`。

### Frontend

```powershell
node node_modules/vitest/vitest.mjs run `
  src/stores/sessionsStore.test.ts `
  src/stores/companionProjection.test.ts `
  src/stores/companionSelectors.test.tsx `
  src/code-panel/ws.chat.test.ts `
  src/components/MessageStreamPanel.workflow.test.tsx `
  src/components/companion/CompanionCard.test.tsx `
  src/components/companion/CompanionDetailModal.test.tsx `
  src/ws/ControlChannel.test.ts `
  --maxWorkers=1 --minWorkers=1
```

结果：`8 files / 88 tests passed`。监控根 PID `26060`，create-time
`2026-07-25T16:41:48.0897696+08:00`，exit `0`，tracked `13`，释放 private
memory `1432956928` bytes，`survivor=0`。

```powershell
node node_modules/typescript/bin/tsc -b --pretty false
```

结果：exit `0`。监控根 PID `28376`，create-time
`2026-07-25T16:44:06.2271190+08:00`，tracked `2`，释放 private memory
`583127040` bytes，`survivor=0`。

独立完整性审计逐项复核 authority 详情链、Platform typed façade、五种控制服务、真实
producer/daily digest、owner 删除、稳定游标和故障重放，最终 `VERDICT: PASS`。

## 3. 真实 Tauri 主消息页 E2E

### 启动与数据准备事实

- 隔离用户数据：`F:\projects\deskpet\.tmp\task12-e2e-clean-20260725-1550`
- 最终 Tauri 根 PID：`32980`
- create-time：`2026-07-25T16:38:56.0621076+08:00`
- 日志确认当前源码后端：
  `[backend_launch] Dev python=F:\projects\deskpet\backend\.venv\Scripts\python.exe backend_dir=F:\projects\deskpet\backend`

测试数据没有直接调用 notification API。准备脚本先走生产
`CompanionStore.record_growth_event()`，再走 `forget_growth_event()`，形成真实链路：

```text
growth_event_forgotten
  -> companion_notification
  -> notification_projection
  -> SessionDB
```

对应通知为 `growth_forget`，摘要为“已遗忘所选成长记录”；源 outbox、session projection
均为 `delivered`，SessionDB 投影行 id 为 `8`。数据准备进程 PID `28776`，create-time
`2026-07-25T16:32:05.0565336+08:00`，exit `0`，清理后 `survivor=0`。

### 真人点击步骤与结果

1. 在桌宠窗口真点击“消息”坐标 `(31,321)`，打开用户点击消息后进入的主消息页。
2. 滚动到真实 `growth_forget` 成长卡片，确认摘要为“已遗忘所选成长记录”。
3. 真点击“查看详情”坐标 `(68,452)`，打开 authority-backed 详情 modal。
4. 真点击 audit 页签坐标 `(422,254)`，页面显示 `source_table: audit_events`，并显示
   actor、action、reason 的脱敏 hash；详情不是通知预填 payload。
5. 关闭 modal 后按 F5，成长卡片从 SessionDB history 恢复，未通过 WebSocket/API
   直注或后端协议调用冒充真人 E2E。

截图：

![Task 12 主消息页通知与详情证据](./task12-main-message-e2e.png)

## 4. 精确进程清理

最终 E2E 按 Tauri 根 PID `32980`、create-time、命令行、worktree、端口和关联进程树
精确清理：

- tracked：`32`
- released private memory：`11108134912` bytes
- `survivor=0`
- 端口 listener count：`0`

诊断过程中的每棵失败/重启树也分别按其根 PID 与 create-time 精确清理，没有按进程名
广杀；每棵树均核对 `survivor=0`。

## 5. 阶段边界

Task 12 已完成用户可见 durable 通知、历史恢复、权威详情和控制动作入口，但没有提前切换
成长 writer。生产 `GrowthAuthorityRouter` 仍为 `legacy/generation=1`；V2 Reminder、
Candidate/Reflection 调度、Companion writer 与旧旁路删除统一留给 Task 13 的可恢复
cutover saga。
