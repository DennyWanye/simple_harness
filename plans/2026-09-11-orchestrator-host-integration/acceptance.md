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

SA-4 已按 S1 代码评审 P1-2 改写（处置见 SDK journal §4）：关闭本机执行时，冲突进入 DEFERRED（`local_code_execution_disabled`）；有争议的 Claim 保持 DISPUTED，不进入正式知识；Mission 不死锁；最终报告列出未解决的冲突。关闭状态下的人工冲突仲裁登记为遗留，归入 Phase3 的 P3.3。

## A2. SDK 切片 S2（0.9.9 / agent_orchestrator 0.9.2）：P3.1 外部控制面

| 编号 | 场景 | 什么算对 | 证据 |
|---|---|---|---|
| SB-1 | 严格映射字段（P3.1-A06） | 开放的字段完整入库，读回来一致；未知字段拒绝；暂不开放的字段（`allowed_tools`、`risk_level`、`task_kind`、金额预算）明确报错；都不写库 | `tests/orchestrator/host_support/test_facade.py` |
| SB-2 | 命令回执（P3.1-A03） | 同一个 key、同样内容再次提交，得到原回执；内容不同则报 `conflict`；不重复创建，不重复预留预算 | 同上 |
| SB-3 | 归属（P3.1-A04） | 另一个 tenant 对 snapshot / events / cancel / decide / comment / artifact_read 的请求全部报 `not_found`，报错内容不暴露对象是否存在 | 同上 |
| SB-4 | 快照与游标一致（P3.1-A05） | `snapshot.through_seq` 等于读快照时库里的最大 seq；从 `through_seq` 往后翻 `events`，不重复、不丢失；每页有上限 | 同上 |
| SB-5 | 产物读取 | 按 id 读取内容，并核对 hash；文件被改动过就报 `integrity_error`；超过大小上限就截断，并标出截断；本机路径不被接受 | 同上 |
| SB-6 | S1 代码评审补测 | 本机执行关闭时 Task 级 `pytest:` 条件被拒；旧 Task 的这类条件在 rule_check 判 FAIL；SA-2 在开、关两种设置下都跑且结果不同；网关兜底分支有测试；合成模板在入口处被检查（P2-6"最小关闭配置"不采纳，理由见 SDK journal §4） | `test_local_code_execution.py` |

## B. Host

| 编号 | 场景 | 什么算对 | 证据 |
|---|---|---|---|
| HA-1 | 钉版 0.9.10（原为 0.9.9；S2 代码评审第 2 轮修复后升版） | ① `simple_harness` 版本为 0.9.10，`agent_orchestrator` 版本为 0.9.3；② `sdk_candidate.py` 里的 sha 与 wheel、manifest 一致，候选校验通过；③ 启动冒烟（真实数据副本、独立端口）：`/health` ok、`startup complete`、`startup_errors=[]`、执行库迁移记录为 `[…, 9, 10]`；④ §5 的回归结果 ⊆ 基线 | `journal.md` §2 |
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
| HA-14 | 本机代码执行默认关闭 | 默认设置下，Planner 提议含 code_test 时被拒绝；Worker 写出的测试文件和 `conftest.py` 全程不被执行（标记文件不存在）；Host 不提供打开本机执行的选项（P3.1 §3.1） | `test_boundaries.py` |
| HA-15 | SIGKILL 恢复：模型调用中途 | 子进程在 provider 调用的阻塞期间被 `kill -9`，另起新进程：① 详情显示回合"结果未知"，Mission 不假装还在正常运行；② `mission_takeover stop` 或 `retry_with_note` 生效；basis 为空时在门口被拒 | `test_restart_recovery.py` |
| HA-16 | 第二个实例 | 同一目录再起一个服务：状态为 unavailable，原因是"另一个实例正在使用编排目录"，不派发任何任务，也不写库；第一个实例被 SIGKILL 后，第二个可以拿到锁 | `test_instance_lock.py` |
| HA-17 | 没有模型 | provider 链为空或解析失败时，状态为 `unavailable("未配置模型")`，后端照常启动 | `test_service_lifecycle.py` |
| HA-18 | 动作条件 | 产品模式下，`action:` 条件被拒绝，返回 `action_criteria_disabled` | `test_service_missions.py` |
| HA-19 | 仲裁 | 出现 `arbitration` 请求后调用 arbitrate，裁决生效；basis 为空时在门口被拒 | `test_approvals_bridge.py` |
| HA-20 | 测试场景隔离 | 测试场景只写 `agent-orchestrator-test/`，正式库的 Mission 数不变；生效条件缺一不可（只有环境变量，或 userdata 不在 `.local-test-evidence/` 下，都不生效）；第二个 Mission 返回 `test_scenario_single_mission` | `test_test_scenario.py` |
| HA-21 | 并发写入 | `run()` 等待回合期间，并发调用 create / approve / cancel，不抛 StoreError，结果正确 | `test_service_concurrency.py` |
| HA-22 | UI 重连与后端重启分开记录（P3.1 §3.5） | ① 只刷新 WebView 或关闭窗口再打开（后端不重启）：视图从快照 + 事件游标恢复，Mission 不重建，已提交的 Attempt 不重派，正式通知不重复（P3.1-A02）；② 后端进程重启（SIGKILL）后按 HA-8 / HA-15 恢复；两者分别记录，不混为一条 | 前端测试 + `reports/native-ui-run1.md` |
| HA-23 | 产物读取 | 详情里点击产物，经 `mission_artifact_read` 看到内容与 hash；另一个 tenant 或伪造的 id 返回 `not_found` | 契约测试 + 原生验收 |
| HA-24 | 部署清单（P3.1-A08） | 启动后 `deployment-manifest.json` 与 status 中的清单列出实际导入路径、版本、wheel sha、schema、生效政策与模型；版本与钉版不一致时服务标为 unavailable；清单中没有密钥 | `test_deployment_manifest.py` |

## E. 与 Phase3 P3.1 验收（P3.1-A01 至 A08）的对照

| P3.1 | 本计划对应 |
|---|---|
| A01 UI 创建一次：id 贯通、正式结果有验证依据 | HA-3、HA-4、HA-12② |
| A02 断线与重开：不重建、不重派、不重复通知 | HA-22① |
| A03 重试相同命令 | SB-2、HA-3② |
| A04 越权读取与操作 | SB-3（Host 单机单 tenant，另测伪造 id） |
| A05 状态快照竞争 | SB-4、HA-4② |
| A06 API 字段不丢失 | SB-1 |
| A07 人工等待可恢复 | HA-8（在等人工复核时被 SIGKILL）、HA-6 |
| A08 安装版本真实一致 | HA-24、HA-1 |

P3.1 的退出门槛：安装版 App 里一个 Mission 从创建到正式产物完整可操作（HA-12），并通过 Phase2 回归。拿不到安装版证据时，最多标"SDK 已就绪，Host 待验证"。

## F. 验收结果（2026-09-12）

| 编号 | 结果 | 证据 |
|---|---|---|
| SA-1 至 SA-7 | PASS | SDK `host-support-0.9.8/journal.md` §2、§3（全量回归 4 次，红集都等于基线；wheel 0.9.10 在干净环境验证） |
| SB-1 至 SB-6 | PASS | 同上，另见 §4.2 |
| HA-1 | PASS | journal §2 H1 行：钉版、回归逐条等于基线；③ 的启动冒烟在原生启动时核对 |
| HA-2 至 HA-5 | PASS | `tests/orchestration`，共 107 passed |
| HA-6 | PASS | `test_approvals_bridge.py`；① 另在原生 App 里验证，见 HA-12 ⑤ |
| HA-7 | PASS | `test_policy_readonly.py`，含漂移显示 |
| HA-8 | PASS | `test_restart_recovery.py` |
| HA-9 | PASS | `test_boundaries.py`；HA-11 两次运行和原生 `native.log` 的扫描命中都是 0 |
| HA-10 | PASS | vitest 97 个文件、772 条全部通过；typecheck 通过；lint 与基线逐文件一致 |
| HA-11 | PASS | `reports/real-run1.md`（run2 COMPLETED） |
| HA-12 | PASS（①–⑥） | `reports/native-ui-run1.md` |
| HA-13 | PASS | ARCHITECTURE 的 AGENT_ORCHESTRATION、index、PROJECT_STATUS、AGENT_HARNESS；journal 的装配登记与终态行 |
| HA-14 | PASS | `test_boundaries.py`；HA-11 真实运行里，Worker 自己写的测试文件两次都没有被执行 |
| HA-15 | PASS | `test_restart_recovery.py`（无条件断言）；原生 ④ 在途部分 |
| HA-16 至 HA-19 | PASS | 对应测试文件 |
| HA-20 | PASS | `test_test_scenario.py`；原生 ⑤ 的场景副本里没有正式编排库 |
| HA-21 | PASS | `test_service_concurrency.py`，含审批决定与评论并发 |
| HA-22 | ② PASS，① 部分完成 | ② 见原生 ④。① 在原生 App 里只做过视图重新挂载，WebView 刷新、关窗再开没有做；前端测试覆盖重连逻辑。已列为遗留 |
| HA-23 | PASS | `test_review_fixes.py`（按 id 读取并核对 hash，伪造 id 返回 not_found）；原生 App 里"查看产物" |
| HA-24 | PASS | `test_deployment_manifest.py`；原生启动的清单 |

另外，按裁决 C，本部署不提供无上限的 Mission：预算留空的项由 Host 补 400000 tokens / 12 次，见 `test_mission_budget_default.py` 与原生复验。

P3.1 退出门槛：在原生 verify bundle（debug .app 加源码后端）上达成。冻结打包的安装包没有验证（F-ORCH-6）。

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
