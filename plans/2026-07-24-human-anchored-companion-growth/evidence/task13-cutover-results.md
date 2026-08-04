# Task 13 — Companion 生产 authority cutover 结果

> 日期：2026-07-25
> 阶段：Task 13
> 结论：生产单指针 cutover、旧 authority 收缩与真实主消息页价值路径通过；
> R4.5 LOC/transaction-starter 预算门按计划转入 Task 14 收口

## 1. 本阶段生产事实

- `GrowthAuthorityRouter` 已在 durable journal 与 ingress drain 内完成
  `legacy → preparing → companion`；本次真实启动恢复为
  `phase=companion, generation=5`。
- 启动顺序为 Capability runtime rehydrate → Companion composition →
  Product Harness（ingress closed）→ cutover → notification projection /
  Companion runtime adapter → Product ingress open。
- 生产成长写入口现为 Companion Preference、GrowthEvent、Runtime、V2 Reminder 与通知
  outbox/projection。
- `legacy.list_reminders.v1`、进程内 Reminder writer、
  `SkillCodifier/CandidateProposal/ToolPathRecorder`、Presenter/Voice codify 回调和裸
  candidate confirm 入口已退休。
- 可信 profile bind 会恢复同 owner inbox；若不存在，则创建空 UUID owner inbox 并把
  `session_id` 返回前端。已有消息的 legacy `default` session 不会被当前 Relay owner
  认领。
- 历史 Capability ToolSpec 只有在当前定义能精确重算旧 fingerprint v1 时才允许 Store
  CAS 迁移；任意其他漂移继续 fail closed。
- 当前组合根没有可证明的旧用户 Skill → immutable pack 发布 receipt，因此非空 legacy
  Skill inventory 会在 `legacy_import`、不可逆 marker 之前可见地阻断，Preference、
  Candidate 与 Skill 均为零部分导入；空 inventory 才继续 cutover。计划使用
  CapabilityStore 的真实 binding generation 与 owner-scoped committed stamp，
  old/new 相同并显式声明 `capability_mutation=none`，不虚构发布。

## 2. 自动化门

| 门 | 状态 | 最终证据 |
|---|---|---|
| Companion / cutover / reminder / profile 聚焦 | PASS | `166 passed in 17.14s` |
| Companion 全量 | PASS | `476 passed in 43.74s` |
| Skill / Preference 兼容回归 | PASS | `127 passed in 12.35s` |
| Capability 全量 | PASS | `251 passed in 29.23s` |
| Workflow 相邻回归 | PASS | `53 passed in 12.90s` |
| Harness 拆分功能组 | PASS | `106 passed, 4 xfailed`；`62 passed`；`38 passed`；`44 passed`；`15 passed`；`121 passed`；`195 passed` |
| Harness R4.5 预算组 | 转 Task 14 | AgentLoop `3988 > 3800`；新增 Companion/Capability/Personal Runtime 符号尚未进入 core budget 分类；transaction starter `55 > 53` |
| 前端 Vitest | PASS | `92 files / 851 tests passed` |
| TypeScript `tsc -b` | PASS | exit `0` |
| authority / parity / build manifest / diff check | PASS | authority manifests 已刷新；legacy parity `141/141`；execution build manifest current；`git diff --check` 通过 |

cutover fault matrix 还逐步覆盖 3 个 pre-marker、5 个 post-marker callback failure，
以及全部 8 步“物理 effect 已生效、receipt 尚未落库”的冷启动恢复；完成后物理 effect
始终为 1。聚合 Harness 长跑会因遗留 aiosqlite worker 未及时结束而超时，已拆组获得上表
确定性结果；该资源关闭问题与 R4.5 预算一起进入 Task 14，不冒充全绿。

## 3. 真实 Tauri 主消息页 E2E

测试使用真实源码 backend：

- `DESKPET_BACKEND_DIR=F:\projects\deskpet\backend`
- `DESKPET_PYTHON=F:\projects\deskpet\backend\.venv\Scripts\python.exe`
- Relay mode Vite；不是 protocol 注入、脚本回放或直接 backend 调用。

启动日志确认生产顺序：

1. `product_harness_ready_ingress_closed`
2. `growth_authority_ready phase=companion generation=5`
3. `companion_runtime_adapter_ready product_ingress=open phase=companion`

真人操作与结果：

- 主消息页激活的 owner inbox：
  `26f5276d-69e9-42b0-ba65-b4a8988b86d6`
- 该 inbox 初始历史为空，未继承 legacy `default` session。
- 真人点击输入框，输入：`请只回复：Task13主消息页通过`
- 真人点击发送。
- UI 显示回复：`Task13主消息页通过 ✅`
- UI 状态回到“空闲”。
- backend 日志 `chat_v2_final` 使用相同 session id
  `26f5276d-69e9-42b0-ba65-b4a8988b86d6`。

结论：可信 Relay identity → owner inbox → ProductTurnPreparer → RunKernel/Driver →
真实 provider → 主消息页 final 的生产链路通过，且没有认领旧 Session 历史。

[Task 13 主消息页最终截图](./task13-main-message-e2e.jpg)

## 4. 失败吸收与修复

本次真实启动/点击暴露并吸收了五类生产问题：

1. **ToolSpec fingerprint schema 升级**
   历史已安装 Godot pack 的 v1 fingerprint 与当前 schema 不同。新增精确 v1 重算与
   Store CAS migration；只有完整复现旧 fingerprint 才迁移，未知 drift 仍拒绝启动。

2. **IdentityReadyGate 与 Runtime 启动循环**
   profile coordinator 原先要求 Runtime 启动，但 Runtime 又要求 identity ready。
   新增 `start_prebound()`，先验证 durable owner/generation/binding epoch，再冻结
   IdentityReadyGate；失败时暂停 Runtime。

3. **legacy `default` session 不可认领**
   Relay owner 不能把已有消息的 unowned legacy session 绑定为自己的 inbox。生产 bind
   现在恢复 exact owner route，或新建空 UUID inbox，并把新 session id 发送给前端切换。

4. **Python keyring stale 与 Rust credential slot 优先级**
   身份恢复以 Rust 持有的 exact credential slot 为权威，避免 Python keyring 中陈旧值
   覆盖当前 Relay 登录事实。

5. **Vite Relay mode**
   真人 E2E 必须以 `--mode relay` 启动 Vite，确保使用真实 Relay 身份与 provider 链；
   普通 dev mode 不能作为本场景的等价证据。

## 5. 精确清理

- 根进程：`16040 / 13620`
- 清理进程数：`21`
- 释放 private memory：`9749762048 bytes`
- `survivors=0`
- backend `8100` listener：`0`
- Vite `5173` listener：`0`

清理按 exact root PID/create-time/command tree 执行，没有按进程名广杀。

自动化诊断期间三次超时进程树也均按完整 pytest 命令与 exact PID 清理，分别释放
`1326133248`、`929112064`、`1298485248` bytes；后续两次二分诊断分别释放
`916312064`、`912936960` bytes，每次均验证 `survivors=0`。

## 6. Task 13 边界

Task 13 证明单一 Companion production authority、owner inbox 与真实主消息页问答可用。
Task 14 仍负责故障/性能/隐私与权限门，Task 15 仍负责完整真实 provider 价值矩阵，
Task 16 负责最终 100% 完成度、testcase、架构与交付审计；因此这里不宣告整个计划完成。
