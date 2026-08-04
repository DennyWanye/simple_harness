# Task 11 持久 Reminder、草稿与外部确认结果

> 日期：2026-07-25
> 结论：Task 11 实现、自动化门和主消息页真人 E2E 通过；V2 Reminder 已完成但按计划保持
> Companion phase dormant，生产 authority 仍为 `legacy/generation=1`，切换由 Task 13
> 唯一 cutover 完成。

## 1. 实现事实

- Companion schema v4 新增 job execution wait；Reminder create/list/cancel 使用精确
  `core.reminder_*.v2` checked authority，未注册到当前 legacy 生产 handler set。
- create/cancel 使用稳定 effect id 与跨库 receipt 去重；重复请求不重复创建、取消或发送。
- `ReminderScheduler` 覆盖 occurrence CAS claim/reclaim、daily frequency reservation、
  overdue expire-no-catch-up、policy defer、weekly next occurrence 和 exactly-once delivery
  outbox。
- 需要模型准备的提醒只创建非空 delegated draft；grant 仅含 read/draft/
  reversible-local，固定 `external_send_allowed=false`。
- 外部动作在 Auto 模式下仍进入 `waiting_decision` 并释放 lease；确认后恢复同一个
  Run/decision/call/effect，复用既有 decision、grant 与 effect 权威。缺外部 receipt
  时按 unknown fail closed，不重发；终态只结算原 job/wait 一次。
- Task 11 真机暴露出一个相邻的 durable catalog 缺口：已连接 filesystem/playwright MCP
  tool 没有 host build identity，普通问答在 Run capture 前报
  `prepared_tool_build_identity_missing`。修复后只有能冻结真实 stdio launcher，或 npx
  已安装 package bundle/package-lock 与 launch config digest 的 MCP tool 才获得
  `ExecutionBuildIdentity`；无法证明来源的 entry 仍只能发现，不能进入 durable Run。

## 2. 自动化验证

| 门 | 结果 | 精确进程审计 |
|---|---:|---|
| Reminder + external confirmation 组合 | `102 passed in 11.46s` | root PID 5344，create 14:37:57，tracked=6，释放 900239360 bytes，survivor=0 |
| Companion 全量 | `394 passed in 30.01s` | root PID 15804，create 14:40:24，tracked=3，释放 923709440 bytes，survivor=0 |
| MCP identity + run catalog lease | `23 passed in 5.31s` | root PID 23564，create 14:56:00，tracked=3，释放 895250432 bytes，survivor=0 |
| execution build manifest | generator `--write`、`--check` 均通过 | 无残留进程 |

全量 Companion 首轮曾得到 `393 passed, 1 failed`。失败是既有 migration boundary 测试仍把
当前 schema v22 当作 v19；测试现显式删除 v21/v22 对象和 migration rows 后重建真实 v19
边界，再迁移到当前 `WORKFLOW_SCHEMA_VERSION`。修复后 focused migration `8 passed`，
随后全量 `394 passed`。

## 3. 主消息页真人 E2E

### 启动与交互

- 仅启动一套 Tauri；未手动启动 backend 或 Vite。
- Tauri 环境：
  - `DESKPET_BACKEND_DIR=F:\projects\deskpet\backend`
  - `DESKPET_PYTHON=F:\projects\deskpet\backend\.venv\Scripts\python.exe`
  - `DESKPET_USER_DATA_DIR=F:\projects\deskpet\.tmp\task11-e2e-login-20260725-1448`
- 日志确认：
  `[backend_launch] Dev python=...\backend\.venv\Scripts\python.exe backend_dir=F:\projects\deskpet\backend`
- 真人操作链：
  1. 完成 onboarding 的 Next / Finish；
  2. 点击桌宠“消息”；
  3. 点击主消息页输入框；
  4. 键入 `Reply only 35: 70/2=?`；
  5. 点击“发送”；
  6. 等待真实 provider。
- UI 返回 `35`，底部状态回到“空闲”。截图：
  [task11-main-message-e2e.jpg](./task11-main-message-e2e.jpg)。
- backend 日志记录真实 `POST https://chinzy.com/v1/chat/completions` 为 HTTP 200，最终
  `chat_v2_final_send_completed chars=2`，没有 WebSocket/API 直注。

### 耐久事实

- `execution_runs.run_id=5597356dc24d55eeb965a9597b3ff5be`
- `request_id=request-7d251eb5-32de-4c8b-b093-9394f9639437`
- `status=completed`，`driver_kind=react`
- `context_owner_key=companion:relay_2ec0ae7233b2c99caa452c4f40a783cd:1`
- `profile_bindings.status=ready`，generation=1，binding_epoch=1
- `growth_authority_state.phase=legacy`，generation=1
- SessionDB 同一 root Run 保存 user `Reply only 35: 70/2=?` 与 assistant `35`
- Run catalog 保存 141 项 exact entry；MCP host entry 的
  `host_build_identity.provider=mcp-stdio`，包含 DeskPet adapter、真实 `npx.cmd`、
  package bundle 与 package-lock digest。

## 4. 失败吸收与环境缺口

1. 首次复用默认 userdata 时，旧 Capability binding 与当前 ToolSpec 不同，启动在 rollback
   对账处 fail closed。root PID 24940 的 22-PID 树精确清理，释放 10758815744 bytes，
   survivor=0。
2. 首个隔离 userdata 的 HMR 暴露 React 19 `Maximum update depth exceeded`：selector
   每次返回新的 `Object.values(...)` 数组。Task 12 前端分片改为稳定 map subscription +
   `useMemo`，并增加“无关更新不重渲染、目标 stream 更新一次”的 subscriber 回归。
   root PID 29756 的 32-PID 树释放 10419027968 bytes，survivor=0。
3. 完成正式 onboarding 后，首个真人问答因 MCP build identity 缺失失败。修复前 root
   PID 13560 的 32-PID 树释放 11049041920 bytes，survivor=0；修复后重新启动并通过。

## 5. 最终清理

最终 Tauri root PID 28664，create
`2026-07-25T14:59:12.0725364+08:00`。按 PID、create-time、完整 command、repo、
userdata、日志路径和父子树验证后，从叶到根精确停止 32 个进程，释放
10055626752 bytes private memory。清理后：

- captured survivor=0
- userdata/log command scope survivor=0
- backend 8100 listener=0
- Vite 5173 listener=0
