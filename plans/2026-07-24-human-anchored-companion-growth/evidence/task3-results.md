# Task 3 验证结果：durable GrowthEvent 与通用执行契约

日期：2026-07-25（Asia/Shanghai）

## 实现事实

- workflow schema 从 v16 显式迁移到 v17；新增 Run start snapshot、Provider invocation/
  outcome、Run fence、effect handoff、terminal extension receipt 与 delivery fence。
- state.db 从 v20 显式迁移到 v21；新增 session owner、growth ingress outbox、Companion
  excluded projection route/outbox 和 redaction receipt，并保留既有 message ID、sequence、
  workflow hydration、FTS 与 vector 映射。
- `ExecutionWriteLane` 统一 workflow DB 写连接；取消、commit outcome unknown、poison、
  fork PID 和有界关闭均有 fault test。
- `ProviderInvocationCoordinator` 在物理 transport 前 durable claim，handoff 后 durable
  ack/outcome；coordinated mode 关闭 SDK/adapter 盲重试。
- `PreparedRunContextV1`、RunStartSnapshot、start/terminal extensions 与 after-commit
  handshake/cleanup 已接入 Kernel；terminal delivery 在 start 冻结。
- Growth signal/contributor/sink 只接到 dormant Companion 分支；生产仍为 legacy authority，
  没有成长双写。

## 自动化

- Task 3 backend 合并聚焦：`250 passed in 51.42s`。
- 提交前最终聚焦复跑首次为 `242 passed, 1 failed`；失败是恢复测试在并发负载下 3 秒内
  尚未收到 resume。相同 case 单例随即 `1 passed`，未改代码重跑完整集合为
  `243 passed in 37.67s`，确认是测试时序波动而非持久状态错误。
- Provider/Agent/adapter/stream 聚焦：`88 passed, 2 skipped`。
- Workflow/Code/PPT/DeepResearch/ReAct 相邻回归：`106 passed`。
- 前端 provisional stream/retract：`16 passed`。
- `tsc -b`、Python compile 与 `git diff --check` 通过。
- pytest 精确命令行审计：`survivor=0`。

## 真实 Tauri 主消息页

启动方式只保留一棵 Tauri 树和一个 Vite：

- Tauri 使用 `.tmp/task3-e2e-tauri-config.json` 关闭 `beforeDevCommand`；
- Vite 唯一监听 `[::1]:5173`；
- 日志明确出现
  `[backend_launch] Dev python=... backend_dir=F:\projects\deskpet\backend`；
- backend 唯一监听 `127.0.0.1:8100`，使用隔离 user data
  `.tmp/task3-e2e-userdata`；
- fresh state.db 完整迁移到 `013_companion_projection.sql`，workflow service 与
  product Harness 均 ready。

Windows Computer Use 的真实操作链：

1. 点击 onboarding“跳过”；
2. 点击桌宠“消息”，打开独立 `DeskPet · 消息` 主消息页；
3. 点击输入框，真实键入并点击发送 `请只回复：Task3 E2E 通过`；
4. 模型把该文本解释为执行 E2E 测试，manual 策略下产生 durable
   `admission.waiting`，UI 显示运行任务；
5. 点击“停止”后 UI 出现 `cancelled — cancelled`。

数据库对账：

- Run `21b01f8ed1ed5739b9fa2881fb81a525` 有且仅有一条 RunStartSnapshot；
- 事件序列为 `admission.waiting` durable_seq=1、
  `run.final/cancelled` durable_seq=2；
- terminal event id 已写入，未产生伪 Provider invocation row；
- 设置页真实滚动并勾选“Agent 全开模式”后，
  `authorization_policy_state = mode=auto, generation=1`；
- backend 日志记录 `permission_auto_mode_set enabled=True`。

第二条纯聊天终态复测在 Windows 控制层检测到外部输入后按安全规则停止继续注入；没有使用
协议直注、后端调用或脚本回放冒充真人 E2E。Provider claim/outcome/流式 canonical replace
由上述自动化集成套件覆盖，本轮真人证据只结算已实际发生的 start/admission/cancel/auto
策略链。

## 进程与临时资源清理

- Tauri/Vite scoped 树清理后，记录的目标 PID `survivor=0`；
- 8100/5173 无监听者；
- 统计释放 private memory 约 9.13 GiB。
- `.tmp/task3-e2e-tauri-config.json`、`.tmp/task3-e2e-logs` 和
  `.tmp/task3-e2e-userdata` 已移入 Windows 回收站，工作区原路径均不存在。

清理审计发现一个过程缺陷：首次 descendant 枚举只依赖 `ParentProcessId`，Windows PID
复用让两个创建时间早于 backend launcher 的 Riot 后台进程被误纳入并终止。它们不是
DeskPet 代码或数据，没有进行文件删除；后续清理必须把“child 创建时间不早于 parent”
作为硬过滤，并在日期转换失败时 fail closed，不能继续执行。该问题不影响 Task 3 数据库、
测试或 Tauri 结果，但作为 cleanup 证据如实保留。
