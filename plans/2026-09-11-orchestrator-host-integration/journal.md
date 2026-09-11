# Agent 编排框架接入 Host · 记录

- 日期：2026-09-11 起
- 计划：同目录 `plan.md`；验收：`acceptance.md`

## 0. 用户指示（原话）

- 2026-09-11："请你开始Host 产品接线任务"
- 长期指示：遇到专门术语先查 `agent-orchestration-theory/` 的定义；真实模型只用 deepseek-flash；所有记录与回复用中文；技术取舍交独立评审子代理裁决并记录。

## 0.1 现在做到哪里 / 下一步（handoff，每个切片提交时更新）

- 2026-09-11：
  - 摸底完成；基线已取（`baseline.md`）。
  - plan review 第 1 轮 NOT_READY（3 P0 / 6 P1），已全部处置（§1），计划与验收升到第 2 版。
  - 下一步：提交 H0 → **SDK 切片 S1（0.9.8 / 0.9.1）**，在 SDK 仓库测试先行 → H1 钉 0.9.8 → H2 至 H5。
  - 测试草稿已入库在本目录 `drafts/`，这个目录不在 pytest / vitest / tsc 的扫描路径里，不会让主分支变红：
    - `backend-tests/`：到 H2 时复制到 `backend/tests/orchestration/`。工作副本也在那里，还没有跟踪。
    - `frontend/`：到 H4 时复制到 `tauri-app/src/`。
    - `ui-scripts/`：第一阶段的 AX 驱动脚本，H5 用。
    草稿还要按第 2 版计划调整：锁、owner、SIGKILL 子进程测试、`mission_takeover`、测试场景目录、`Orchestrator.create_mission`。
  - SDK 切片 S1 的计划在 SDK 仓库 `plans/2026-09-11-agent-orchestrator/host-support-0.9.8/plan.md`。关键取舍：本机代码执行关闭时，不创建冲突 Task，冲突走 DEFERRED。原因是冲突 Task 本身要在本机跑探针测试，只去掉 code_test 会留下没运行过的"假证据"。
  - 用户规则：真实模型测试一律用 deepseek-flash；额度快用完时先写 handoff，再提交推送。
- 接手须知：凭证只从 `.local-test-evidence/2026-09-07/credentials/deepseek.env` 读取，不打印；只用 deepseek-flash；App 运行时不跑 `tests/sdk_adapters/test_composition.py`。

## 1. plan review 处置

第 1 轮（`reports/plan-review-round1.md`）结论为 NOT_READY：3 个 P0、6 个 P1、6 个 P2。评审原话："改完 P0 和 P1-1 到 P1-4 后可以进入实现，不需要再做一轮完整评审。"下表逐条写明处置，全部落在计划第 2 版和验收第 2 版里。

| 编号 | 处置 | 落点 |
|---|---|---|
| P0-1 本机执行挡不住 | 接受。先做 SDK 0.9.8：部署政策加 `local_code_execution`；关闭时，三个判定点按 `verification_policy_undeployed` 拒绝 code_test，默认策略和角色模板也去掉 code_test，`pytest:` 条件在创建时拒绝。Host 改为钉 0.9.8 | plan §3.1 S1-a、§3.5；SA-1 至 SA-4；HA-14 |
| P0-2 前提"单实例 + writer lock"不存在 | 接受。owner 改为 `deskpet-orchestrator-<pid>-<随机串>`；编排目录加 flock，拿不到锁就标为 unavailable；恢复靠租约过期后接管 | plan §3.3 `lock.py` 与生命周期；HA-16 |
| P0-3 SIGKILL 退出 | 接受。目标 6 按 SIGKILL 重写；正确性不依赖 `close()`；HA-8 改为对子进程 `kill -9`，分"回合已提交""模型调用中途"两个点（后者即 HA-15）；协议加 `mission_takeover`；原生验收的等待时限不少于 120 s | plan §1.1-6、§3.3、§3.4；HA-8、HA-12④、HA-15 |
| P1-1 创建入口绕过检查 | 接受。SDK 新增 `Orchestrator.create_mission`，一步完成解析、校验、动作检查、本机执行检查，并带上 provider_kind / 策略参数；`MissionApi(orchestrator=…)` 与 CLI 都走这里。Host 门口另外拒绝 `action:` 条件（测试场景除外） | plan §3.1 S1-b、§3.3 门口检查；SA-5；HA-18 |
| P1-2 测试开关污染产品 | 接受 D6 的修改：独立目录；生效需要"环境变量 + userdata 位于 `.local-test-evidence/`"两个条件同时满足，不依赖 DEV_MODE；自动带 seed；连接器同时传给 `connectors=` 和 `enabled_connectors`；只允许一个 Mission；界面标出测试场景 | plan §3.8；HA-20 |
| P1-3 needs_human 夹具与仲裁 | 接受。Host 测试里自己写 needs_human 的 Critic 步骤（`tests/orchestration/_support.py`）；新增仲裁验收；协议写死：reason 与 basis 必填、review 映射为 pass / fail、拒绝后为 FAILED + `approval_rejected` | plan §3.4；HA-6、HA-19 |
| P1-4 活动 provider 未定义 | 接受 D9 的修改：启动时对 `get_chain()` 第一个启用项做快照；失败时显示"未配置模型"；`deepseek-v4-flash` 在官方端点上映射为 `deepseek-flash`，status 如实显示；换模型只影响新 Attempt，在途回合回显不符照实显示 | plan §3.3 provider；HA-17 |
| P1-5 并发只在 seed 时生效 | 接受。设置说明与 ARCHITECTURE 写明；策略卡显示漂移 | plan §3.3 设置表；HA-7④ |
| P1-6 基线不全 | 接受。补测控制通道、启动、context 相关的 5 个文件（52 passed / 1 skipped，全绿）；确认 Host 里没有 `agent_orchestrator` 同名模块 | `baseline.md` |
| P2 驱动循环 | 接受。只剩等人时 tick 20 s；写入后立即唤醒；连续 3 次失败重建，连续 5 次标 degraded | plan §3.3 |
| P2 并发测试 | 接受 | HA-21 |
| P2 身份 | 接受。用 `load_or_create_local_identity` | plan §3.6 |
| P2 密钥检查带上 provider 密钥 | 接受 | plan §3.3 门口检查；HA-3③ |
| P2 表单不收金额预算 | 接受 | plan §1.2、§3.4 |
| P2 AX 判据 | 接受。成功条件每行一条；以"提交 Mission"变为可点作为判据 | plan §3.9 |
| D1、D4、D5、D7、D8 | 采纳（D7 加只读连接，写入后立即推送；D8 加 P1-5 说明） | — |

## 2. 切片

| 片 | 提交 | 内容 | 测试结果 |
|---|---|---|---|
| H0 | | 计划文档 | — |
| H1 | | 钉版 0.9.7 | |
| H2 | | 后端编排服务 | |
| H3 | | 控制通道协议 | |
| H4 | | 前端视图 | |
| H5 | | 真实模型、原生验收、文档 | |

## 3. Host 装配位置登记（按 ORCH-BUILD 第 15 行）

（实现后填写：Host 提交、`main.py` lifespan 行号、`deskpet/orchestration/` 模块、数据目录）

## 4. 真实模型与原生验收

（待填）

## 5. 遗留

（待填）

## 6. 结论

（待填）
