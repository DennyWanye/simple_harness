# Agent 编排框架接入 Host · 验收标准（第 2 版）

- 日期：2026-09-11（已处置 plan review 第 1 轮）
- 对应计划：同目录 `plan.md`
- 判定：每条都写明"什么算对"与证据位置。原生 App 验收（HA-12）不能用单元测试代替；测不了就写 BLOCKED，不降级。

## A. SDK 切片 S1（0.9.8 / agent_orchestrator 0.9.1）

| 编号 | 场景 | 什么算对 | 证据 |
|---|---|---|---|
| SA-1 | 关闭本机代码执行时，拒绝 code_test | `local_code_execution=False` 时，Planner 初始图、改图、Commit 提议里含 `code_test` 的 Task 都被拒绝，原因是 `verification_policy_undeployed`，写入 TaskGraphRejected / TaskGraphChangeRejected 事件 | `tests/orchestrator/host_support/test_local_code_execution.py` |
| SA-2 | 行为 oracle | 同一个场景里，Worker 往工作区写 `test_probe.py` 与 `conftest.py`（导入时写标记文件）；整个 Mission 跑完后，标记文件不存在，也没有启动过任何 pytest 子进程（`run_pytest` 被替换为计数器，计数为 0） | 同上 |
| SA-3 | 默认策略与模板 | 关闭时，Manager 与改图的默认 verification_policy、冲突 Task 的策略都不含 code_test；Planner 和 Manager 的角色模板文本里不再出现 code_test | 同上 |
| SA-4 | 冲突不死锁 | 关闭时，两个 Worker 的结论冲突，最后走到人工仲裁请求（`arbitration`），不会卡在没人能执行的 code_test 上 | 同上 |
| SA-5 | 创建入口 | `Orchestrator.create_mission` 满足以下各条：① 请求合法，返回 `(mission, True)`；同一个 key 再次提交，返回 `(mission, False)`；② provider_kind 按实际记录，不是 `"unknown"`；绑定的策略版本与 `submit_mission` 相同；③ 未启用的连接器所对应的 `action:` 条件被拒绝；④ 关闭本机代码执行时，`pytest:` 条件被拒绝；⑤ 工具超出部署政策时被拒绝；⑥ `MissionApi(orchestrator=…)` 与 CLI `mission create` 走这条入口 | `test_create_mission_entry.py` |
| SA-6 | 默认不变 | `local_code_execution` 默认为 True；`tests/orchestrator` 全部通过；`DeploymentPolicy(local_code_execution=False, allowed_tools=[…, "run_tests"])` 构造时报错 | 回归日志 |
| SA-7 | 交付 | SDK 全量回归的红集 ⊆ 73 条基线；wheel 在干净 venv 里安装后，测试与四个演示都通过；独立代码评审的意见已处置；推送 origin main | SDK `host-support-0.9.8/journal.md` |

## B. Host

| 编号 | 场景 | 什么算对 | 证据 |
|---|---|---|---|
| HA-1 | 钉版 0.9.8 | ① `simple_harness` 版本为 0.9.8，`agent_orchestrator` 版本为 0.9.1；② `sdk_candidate.py` 里的 sha 与 wheel、manifest 一致，候选校验通过；③ 启动冒烟（真实数据副本、独立端口）：`/health` ok、`startup complete`、`startup_errors=[]`、执行库迁移记录为 `[…, 9, 10]`；④ §5 的回归结果 ⊆ 基线 | `journal.md` §2 |
| HA-2 | 服务装配 | ① 默认 `enabled`，状态为 `available`；② 编排库在 `<user_data>/data/agent-orchestrator/`，不和 SDK 执行库在一起；软链被拒绝；③ 启动失败（目录不可写）时后端照常启动，status 返回 `available=false` 并附原因；④ `_VALID_SERVICES` 包含 `orchestration`；⑤ 测试路径下调用 `close()` 后不留任务 | `test_service_lifecycle.py` |
| HA-3 | 创建与运行 | ① 夹具 provider 下，合法请求自动跑到 COMPLETED；② 同一个 key 再次提交，返回同一个 id 且 `created=false`；③ 门口拒绝以下情况，且库里不留痕迹：空目标、空成功条件、`pytest:`（返回 `local_tests_disabled`）、带密钥形态的文本，包括当前 provider 的真实密钥（返回 `secret_rejected`，错误信息不回显原文）；④ 客户端传入的 `allowed_tools` 被忽略 | `test_service_missions.py`、`test_boundaries.py` |
| HA-4 | 进度与推送 | ① 投影只输出白名单字段，模型文本带 `source: "model"`；② 事件分页的 seq 严格递增、不重复、不丢，总数与库里一致；③ 状态变化后 2 s 内收到 `mission_changed`；④ 推送用的是只读连接；⑤ 投影与推送里没有密钥 | `test_projection.py` |
| HA-5 | 取消 | 运行中的 Mission 取消后变为 CANCELLED，不再派发新的 Attempt；重复取消得到幂等结果 | `test_service_missions.py` |
| HA-6 | 审批 | ① approval-action 测试场景：出现 PENDING 的 `action` 审批 → 批准 → 恰好交接一次、核对回执 → COMPLETED；`granted_by` 为本机身份；② 理由为空的拒绝在门口被拒；拒绝后动作状态为 REJECTED，Mission 变为 FAILED，停止原因 `approval_rejected`；③ auto 模式下审批仍为 PENDING；④ review 类（Host 自己写的 `needs_human` Critic 脚本）：复核通过后结果被接受；⑤ 含密钥的理由或评论被拒绝，不写入 | `test_approvals_bridge.py` |
| HA-7 | 策略只读 | ① status 返回 ACTIVE 版本 id（新库为 seed）；② 详情里带出 Mission 绑定的版本；③ 结构测试：`deskpet/orchestration` 不调用 PolicyApi 的任何写方法；④ 修改 `max_concurrency` 后重启，ACTIVE 策略不变，status 显示漂移 | `test_policy_readonly.py` |
| HA-8 | SIGKILL 恢复：回合已提交 | 在子进程里运行服务（夹具 provider，`lease_seconds` 缩短）；从父进程观察到结果已提交后，对子进程 `kill -9`；在同一目录另起一个进程（新 owner）→ 租约过期后 Mission 继续到 COMPLETED；已完成的 Task 不重跑；每条 `usage_ref` 最多导入一次 | `test_restart_recovery.py` |
| HA-9 | 边界 | ① 默认只有三个工作区工具；② 编排运行时里没有任何 Host 工具；③ 编排目录、日志、控制通道载荷扫描 `\bsk-` 与测试密钥，命中为 0 | `test_boundaries.py` |
| HA-10 | 前端 | ① 以下状态都能渲染：不可用、空、列表、详情、审批卡、接管、错误、测试场景、`local_tests_disabled`；② 目标或成功条件为空时不能提交；③ 按钮发出正确的消息，payload 中没有 `allowed_tools`；④ `mission_changed` 更新列表；迟到的旧推送不会让状态倒退；⑤ NOT_REQUIRED 不显示为通过，金额显示"未计价"；⑥ typecheck、vitest 全绿，lint 没有新签名 | vitest；`journal.md` |
| HA-11 | 真实模型（opt-in） | 用 `-m real_provider` 和 deepseek-flash 跑一个纯文字目标、只有 `file:` 或自然语言条件的 Mission，直到终态；COMPLETED，或 FAILED 并如实记录停止原因；证据扫描命中为 0 | `reports/real-run1.md` |
| HA-12 | 原生 App（AX 驱动） | 用新 bundle、真实数据副本、deepseek-flash：① 点击"任务编排"，看到视图和策略卡；② 用 AX 新建 Mission，状态流转到终态；③ 编排运行期间，主对话仍能完成一轮对话；④ 退出 App（SIGKILL）后用同一份数据重启，Mission 与详情都在，状态一致；如果退出时 Mission 在途，重启后 120 s 内继续推进，或者如实显示"结果未知"并能接管；⑤ 测试场景（§3.8）：审批卡出现 → 点"批准" → COMPLETED，编排库里有交接和回执；⑥ 启动时编排服务的 status 为 available | `reports/native-ui-run1.md` |
| HA-13 | 文档 | 新建 `ARCHITECTURE/AGENT_ORCHESTRATION.md`；更新 `index.md` 和 `PROJECT_STATUS.md`；修正 `AGENT_HARNESS.md` 的版本漂移；`journal.md` 里有装配位置登记和终态行 | 提交 diff |
| HA-14 | 本机代码执行默认关闭 | 默认设置下，Planner 提议含 code_test 时被拒绝；Worker 写出的测试文件和 `conftest.py` 全程不被执行（标记文件不存在）；打开 `allow_local_tests` 后，`pytest:` 条件可以创建 Mission | `test_boundaries.py` |
| HA-15 | SIGKILL 恢复：模型调用中途 | 子进程在 provider 调用的阻塞期间被 `kill -9`，另起新进程：① 详情显示回合"结果未知"，Mission 不假装还在正常运行；② `mission_takeover stop` 或 `retry_with_note` 生效；basis 为空时在门口被拒 | `test_restart_recovery.py` |
| HA-16 | 第二个实例 | 同一目录再起一个服务：状态为 unavailable，原因是"另一个实例正在使用编排目录"，不派发任何任务，也不写库；第一个实例被 SIGKILL 后，第二个可以拿到锁 | `test_instance_lock.py` |
| HA-17 | 没有模型 | provider 链为空或解析失败时，状态为 `unavailable("未配置模型")`，后端照常启动 | `test_service_lifecycle.py` |
| HA-18 | 动作条件 | 产品模式下，`action:` 条件被拒绝，返回 `action_criteria_disabled` | `test_service_missions.py` |
| HA-19 | 仲裁 | 出现 `arbitration` 请求后调用 arbitrate，裁决生效；basis 为空时在门口被拒 | `test_approvals_bridge.py` |
| HA-20 | 测试场景隔离 | 测试场景只写 `agent-orchestrator-test/`，正式库的 Mission 数不变；生效条件缺一不可（只有环境变量，或 userdata 不在 `.local-test-evidence/` 下，都不生效）；第二个 Mission 返回 `test_scenario_single_mission` | `test_test_scenario.py` |
| HA-21 | 并发写入 | `run()` 等待回合期间，并发调用 create / approve / cancel，不抛 StoreError，结果正确 | `test_service_concurrency.py` |

## C. LLM 行为变异（编排层已有测试覆盖；Host 只验收"端侧不崩、如实显示"）

| 变异 | Host 端断言 |
|---|---|
| 空结果或不合规的 Result Envelope | 详情显示 ResultRejected 及原因，Mission 由 SDK 的 no_progress 或预算停止，界面不报错 |
| provider 返回 5xx 或超时 | 详情显示执行池冷却或 Attempt 失败；服务不变成不可用；主对话不受影响 |
| 模型夹带修改策略的内容 | 事件里有 `PolicySuggestionRefused`，ACTIVE 版本不变 |
| 模型理由里带指令性文字 | 原样显示为"模型生成，未核实"，不自动执行 |
| Planner 硬塞 code_test | 被 `verification_policy_undeployed` 拒绝，详情里显示原因（HA-14） |

## D. 冷启动

- 全新 userdata：首次启动时建立 `agent-orchestrator/`，策略库 seed，视图显示空态；没有配置模型时显示"未配置模型"。
- 旧 userdata（第一阶段真实数据副本）：原有会话与执行库不变，新目录单独建立。
