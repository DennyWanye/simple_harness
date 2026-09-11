# plan review 第 1 轮（独立评审，opus，只读）

- 日期：2026-09-11
- 对象：同目录上级 `plan.md` / `acceptance.md` 草案（含摸底后补充的 F-1）
- 结论：**NOT_READY**。3 个 P0；改完 P0 和 P1-1 到 P1-4 后可以进入实现，不需要再做一轮完整评审。
- 处置：见 `../journal.md` §1。

## 1. 总评

计划的结构、切片和大部分事实都对，抽查的行号全部核对无误。有 3 个 P0：

- 默认关闭本机测试的方案挡不住本机执行模型写的代码；
- "App 单实例、有 writer lock"这个前提在源码里不存在；
- App 真实的退出方式是 SIGKILL，计划的恢复验收没有覆盖。

## 2. 发现

### P0

**P0-1（§3.4、D3）关闭 run_tests、在门口拒绝 `pytest:` 条件，挡不住本机执行模型写的代码。**

- code_test 层是否运行，只看 Task 的 verification_policy（`verifier_router.py:91`、`203-210`）。
- 没有 `pytest:` 条件时，它照样对整个验证副本跑 pytest（`deterministic_checks.py:183-184`，`targets.append(None)`）。
- Planner 的提示词让模型自己选（`role_templates.py:59`）；Manager 和改图的默认策略里本来就有 code_test（`graph/changes.py:146`、`planning/manager.py:30,113`）。
- code_test 属于安全边界，不能消融（`assembly.py:57-69`）；DeploymentPolicy 也没有关闭某个层的字段（`policies.py:26-47`）。
- 后果：模型写一个 `conftest.py`，就会在用户机器上带着 HOME 环境执行（`tool_gateway.py:62`、`109-125`）。
- 建议：先出 SDK 0.9.8，在部署政策里加"本机代码执行"开关。关闭时，图校验按现有的 `verification_policy_undeployed` 拒绝 code_test（`task_graph.py:265-268`、`changes.py:514-517`）。H1 改为钉 0.9.8。

**P0-2（§3.2 owner、D2）"单实例、有 writer lock"与源码不符。**

- Tauri 没有单实例插件，Host 后端也没有数据目录锁。
- SDK 把同一个 owner 名下的有效租约都当作自己的（`commit_service.py:2335-2341`、`2466`）；SDK 执行层 fence 比较的也是 owner_id（`simple_harness tools/executor.py:329`）。
- 所以两个进程用同一个稳定 owner 时，fence 失效，同一任务会被派发两次。
- SDK 的设计本意是每个实例一个 owner（`event_handler.py:219-221`）；换 owner、等租约过期后接管，已有测试证明（`step03/test_multi_scheduler.py:82-150`）。

**P0-3（目标 6、HA-8、HA-12④）App 退出是 SIGKILL，lifespan 的关停代码不会执行。**

- 退出路径：RunEvent::Exit → kill_child → `child.kill()` / SIGKILL（`lib.rs:239-241`、`process_manager.rs:208-221`）。
- 计划里的优雅 `close()` 和 HA-8 的"关闭服务"只覆盖测试路径。
- 进程在 provider 调用中途被杀时，SDK 回合是 UNKNOWN 并阻塞（HANDOFF §4:46）。阻塞的回合不会触发停滞超时（`event_handler.py:1420` 的条件是 `not liveness.blocked`），Mission 会一直停在"运行中"。
- 建议：
  - HA-8 改为对子进程 `kill -9`，分两个点：`after_turn_committed` 必须续跑到 COMPLETED；provider 调用中途被杀时，详情如实显示阻塞原因，并能接管（`ApprovalApi.takeover`，`approvals.py:156`；协议加 `mission_takeover`）。
  - 目标 6 同步改写；等待时限不少于 120 s（租约 60 s + SDK 30 s）。

### P1

- **P1-1（§3.2 写入口）** `MissionApi.create` 不检查动作条件，也不传 provider_kind（`missions.py:70` 对照 `event_handler.py:734-742`；provider_kind 默认记为 `"unknown"`，`commit_service.py:489`）。产品模式没有连接器，带 `action:` 条件的 Mission 会被收下，而且永远完不成。建议门口拒绝 `action:` 条件（测试场景除外）。
- **P1-2（§3.7、D6）测试开关会污染产品路径。**
  - 夹具 Mission 会写进正式库（production 角色），影响策略状态和评测样本。
  - `DESKPET_DEV_MODE=1` 会关闭 WS 认证（`main.py:13128-13134`），本身就改了产品路径。
  - "启动器显式传入"其实就是同一个环境变量。
  - 夹具脚本用完后进入 UNKNOWN 并挂起。
  - 场景依赖 `action:test_config.set:feature_flags.new_ui` 条件与 workspace_seed（`fixtures.py:1447-1463`）。
  - `TestConfigService(path=<json 文件>)`（`connectors.py:150-163`）必须同时传进 `connectors=` 和 `enabled_connectors`。
- **P1-3（HA-6⑤、目标 4）wheel 里没有 needs_human 夹具。**
  - `critic_step` 不产出 needs_human（`fixtures.py:182-197`），只有测试代码里有（`step07/test_human_review.py:76-83`）。
  - 仲裁没有验收条目。
  - arbitrate 的 basis 必填（`human_commits.py:342-356`）；review 的 verdict 只能是 pass/fail（`human_commits.py:242`）。
  - 拒绝后应写死为 FAILED + `approval_rejected`（`step07/test_approvals.py:266`），不能写"按 SDK 语义"。
- **P1-4（§3.2 provider、D9）"活动 provider"没有定义。**
  - Host 的口径是 `get_chain()` 第一个启用项；全新安装时这里会抛异常（`main.py:10985-10991`），编排服务要显示"不可用：未配置模型"。
  - Attempt 会冻结 model 并核对回显（`event_handler.py:622-630`），换模型后重启，在途回合会回显不符。
  - 配置里写的是 `deepseek-v4-flash`，官方 id 是 `deepseek-flash`，需要校验或映射。
- **P1-5（D8）** `max_concurrency` 只在第一次 seed 时进入 ACTIVE 策略（`promotion.py:148`）；以后改 config.toml 只会记一条漂移，ACTIVE 照旧（`event_handler.py:319-355`）。设置说明和 ARCHITECTURE 要写明。
- **P1-6（§5）** 基线只覆盖三个目录。控制通道分发与 task_projections 在同一个函数里（`main.py:6305`、`6507`），应把控制通道、lifespan、context 相关测试加进基线。wheel 新增了 `agent_orchestrator` 顶层包，要确认 Host 没有同名冲突。

### P2

- **驱动循环：**
  - 有在途回合时 `run()` 不返回（`event_handler.py:809-812`）。
  - 只剩等待审批时，每 2 s 一次的 `run()` 都会做 recover 和 reconcile（`759-796`），而审批最长可挂 24 h。建议这时 tick 改为 15–30 s，人做出决定后立即唤醒。
  - 连续失败时应重建 Orchestrator，而不是只重试。
- **并发：** 结论成立（单连接 + RLock，事务不跨 await，跨任务进入会抛 StoreError，`store.py:367-393`）。要补一个测试：`run()` 等回合的同时，并发调用 approve / cancel / create。
- **身份：** 直接用 `load_or_create_local_identity`（`companion/identity.py:15-25`），"文件不存在就用 default"的分支是多余的。
- **密钥检查：** `find_secrets` 传 `extra=(当前 provider 密钥,)`（`secrets.py:33`）。
- **表单：** 不提供金额预算。未计价时 SDK 不能执行金额上限（`assembly.py:204-209`）。
- **AX：** 成功条件固定为"每行一条"；AX 设值后，以"提交 Mission"变为可点，作为 React 状态已更新的判据。

## 3. D1–D9 裁决

| 编号 | 裁决 | 理由 / 要求 |
|---|---|---|
| D1 数据目录分开 | 采纳 | 复用 runtime_paths 的软链与越界检查 |
| D2 稳定 owner | 修改 | owner 改为 `deskpet-orchestrator-<pid>-<随机串>`；编排目录加 flock，拿不到锁就把服务标为 unavailable；恢复靠租约过期后接管 |
| D3 默认关闭本机测试 | 修改 | 方向采纳，但必须配合 SDK 0.9.8 关闭 code_test；设置放在"任务编排"组，默认关，改动后重启服务生效，打开时写明风险 |
| D4 审批与授权模式分开 | 采纳 | — |
| D5 本机身份、L2 | 采纳 | L2 与 SDK 语义一致（`policies.py:36-38`） |
| D6 测试场景开关 | 修改 | 独立目录 `agent-orchestrator-test/`；生效条件 = 环境变量 + userdata 位于 `.local-test-evidence/` 下，不依赖 DEV_MODE；界面和 status 标出"测试场景"；只允许一个 Mission |
| D7 1 s 轮询推送 | 采纳 | 用只读连接（`store.py:180`）；每个写入口执行后立即推送一次 |
| D8 并发 1 / 1 | 采纳 | 同时写明 P1-5 |
| D9 provider | 修改 | 启动时对 provider 做快照并校验模型 id；解析失败时服务 unavailable；换 provider 只影响新 Mission；`price_table=None` 采纳 |

## 4. 补充验收

- HA-14：默认政策下，Planner 提议含 code_test 被拒，全程没有 pytest 子进程；打开开关后，同一 Mission 能跑测试。
- HA-15：SIGKILL 两个点的恢复（见 P0-3）。
- HA-16：同一个数据目录起第二个实例：显示 unavailable，不派发任何任务。
- HA-17：没有 provider 或 provider 解析失败：显示"未配置模型"，主对话照常可用。
- HA-18：产品模式下 `action:` 条件在门口被拒绝。
- HA-19：仲裁：出现请求 → arbitrate → 生效；basis 为空在门口被拒绝。
- HA-20：测试场景只写独立目录，正式库的 Mission 数不变。
