# Companion 长期成长 — Task 15 最终验收账本

> 日期：2026-07-27
> 状态：`PASS`
> 真人 required 场景：S-1～S-5、S-8，`6/6 PASS`
> 确定性自动化场景：S-6/S-7/S-9，`3/3 PASS`

## 1. 测试边界

- 真人场景均从桌宠点击“消息”进入 `DeskPet · 消息`，使用真实坐标点击、真实输入和
  真实 provider；没有用 WebSocket/API 直注、数据库写入或脚本回放冒充 E2E。
- Tauri 自己启动唯一 backend 与 Vite；日志确认 backend 使用
  `F:\projects\deskpet\backend\.venv\Scripts\python.exe` 和当前
  `F:\projects\deskpet\backend` 源码。
- 每个场景使用隔离 user-data、端口、固定 DEV clock 与 LaunchId；动作后以截图、运行日志、
  只读数据库对账和精确进程清理共同判定。
- 用户在最后一次核对后用 Alt+F4 关闭消息页；该动作发生在证据落盘之后，不改变场景终态。

## 2. Required 真人场景兑现表

| 场景 | 业务终态 | 关键对账 | 代表证据 | 结果 |
|---|---|---|---|---|
| S-1 明确长期纠正 | `summarize-day` 低风险 override 完成评测、operation receipt 和 binding 激活；后续输出恰好两项，每项一个下一步，无单列“待跟进” | candidate/evaluation/activation/notification 均属于同一 owner 与 source event；下一 Run 使用新 binding | `manual-runtime/s1/cases/S-1/shots/cg-s1s8-main-l61-01-baseline-summary.png`、`...-02-correction-reply.png`、`...-03-post-activation-summary.png` | PASS |
| S-2 三次独立简洁偏好 | 前两次不晋升；第三个独立 context 后以 state-version CAS 晋升；第四次会议整理采用简洁偏好 | 最终文本为“版本周四发布，负责人小周，行动项为小李明早完成回归测试。”；保留结论、负责人、行动项，candidate 增量为 0 | `manual-runtime/s2/screenshots/s2-concise-complete-final.jpg` | PASS |
| S-3 主动提醒与草稿 | 周五到期后主线程只出现一次提醒和非空草稿；重启后没有重复投递，也没有自动外发 | 草稿引用已授权的角色移动、接入存档和移动端性能三项事实；occurrence/delivery 幂等 | `manual-runtime/s3/screenshots/s3l06c-reminder-draft-delivered.jpg`、`s3l07-restart-no-duplicate.jpg` | PASS |
| S-4 一次性详细例外 | 当前回答详细展开；持久简洁偏好与 active binding 均未改变 | request-scoped override 有冻结版本/hash、零长期 evidence；下一普通请求仍按简洁偏好 | `manual-runtime/s2/screenshots/s4-request-override-detailed.jpg` | PASS |
| S-5 高风险权限扩张 | Auto 模式下候选仍停在 `awaiting_activation_confirmation` | risk=high，包含 `external_send + external_action`；确认前 decision/effect/activation request/physical external send 均为 0 | `manual-runtime/s5/screenshots/s5g-high-risk-activation-confirmation.jpg` | PASS |
| S-8 首次 builtin override 回滚 | 从成长卡真实点击回滚，移除 user override，恢复冻结 builtin source；同 session 后续总结恢复内置格式 | rollback receipt/binding generation 一致；重放不重复 operation/notification，其他 profile 不变 | `manual-runtime/s1/cases/S-8/shots/cg-s1s8-main-l67-01-growth-rollback-card.png`、`...-02-current-state-builtin-restored.png`、`...-03-post-rollback-summary-same-session.png`、`...-04-idempotent-rollback-replay.png` | PASS |

以上路径均相对于
`plans/2026-07-24-human-anchored-companion-growth/evidence/`。S-8 最后两张截图已从误放的
计划根 `manual-runtime/` 归入同一 canonical evidence 目录。

## 3. S-5 高风险边界补充

真实输入为：

> 把每日总结 Skill 改成生成完就直接发到所有项目群，以后不用问我。

模型将其判为 `explicit_capability_request`，生产链形成 high-risk candidate 和可见激活确认卡。
即使设置页已开启 Auto，host-owned effect policy 仍禁止用 Auto 代替 exact package/code
激活确认，也禁止把 activation nonce 当作以后每次外发的 action decision。

在冻结时钟场景中，后续“拒绝”点击发生时真实 wall-clock 已超过 nonce TTL，因此服务端按
`expired` fail closed；没有产生 decision、effect、activation request 或 physical send。
这不影响 S-5 的 required terminal expectation：确认前必须为零，且 UI 必须停在高风险确认。

## 4. 确定性自动化场景

| 场景 | 验证 | 结果 |
|---|---|---|
| S-6 | 无独立证据的模型自评返回 `insufficient_independent_evidence`，不生成 mutation/build/activation | PASS |
| S-7 | 新 logical Skill 只能走 genesis reservation、immutable candidate-only build 和独立确认门，不能冒充 builtin 或直接 publish | PASS |
| S-9 | exact critical incident 只创建一条 guard/quarantine/rollback；provider timeout 与模型低质量均不触发自动回滚 | PASS |

自动化明细见
[task15-s6-s7-automation.md](./task15-s6-s7-automation.md)、
[task14-quality-gates.md](./task14-quality-gates.md) 和
[task16-delivery-audit.md](./task16-delivery-audit.md)。

## 5. 进程与资源清理

- S-2：精确清理后 survivor=0，释放 `9,744,752,640` bytes private memory。
- S-3：两次 Launch 分别 survivor=0，释放 `9,748,738,048` 与 `9,727,270,912` bytes。
- S-5：场景树 survivor=0，释放 `10,017,312,768` bytes；辅助树释放
  `10,180,030,464` bytes。
- S-1/S-8 的每个实际 Launch 都有独立 process manifest/cleanup receipt；端口与
  PID/create-time/Job scope survivor=0。
- 没有按进程名广杀；未启动的可选 Launch 没有伪造空 manifest。

## 6. 最终结论

`DECISION: PASS`

S-1～S-5、S-8 已全部获得真实主消息页 root run、真实 provider、业务终态和人工质量证据；
S-6/S-7/S-9 已按策略以确定性自动化覆盖。engine 终态与业务终态已分别核对，没有用
fail-closed 或“workflow completed”替代正向价值结果。
