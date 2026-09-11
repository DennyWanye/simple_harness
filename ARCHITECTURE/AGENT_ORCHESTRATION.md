# Agent 编排（任务编排视图）· 生产事实

- 最后更新：2026-09-12
- 计划与记录：`plans/2026-09-11-orchestrator-host-integration/`（plan 第 3 版、acceptance、journal）
- 方向依据：用户的 Phase3 计划 `plans/taskSys2/agent-orchestrator-phase3-plan.zh-CN.md`。本模块是其中 **P3.1 真实 App Mission 控制闭环** 的 Host 直连实现。
- SDK：`simple-harness-sdk` 的 `agent_orchestrator`（与 `simple_harness` 同在一个 wheel 里）。Host 钉版以 `backend/deskpet/sdk_adapters/sdk_candidate.py` 为唯一来源。

> 状态（2026-09-12）：P3.1 Host 直连路径已交付。
> - 自动化：`tests/orchestration` 107 passed，vitest 772 passed。
> - 真实 deepseek-flash 运行：HA-11 通过。
> - 原生 App 验收：HA-12 ①–⑥ 全部通过，用的是 verify bundle `f51ddc37`（debug .app 加源码后端），报告见计划目录的 `reports/native-ui-run1.md`。
> - 冻结打包的安装包没有验证（PyInstaller spec 仍停在 0.6.4），HA-22 ① 的 WebView 刷新也没有做原生验收，两者都列为遗留。

## 1. 装配位置

| 层 | 位置 | 职责 |
|---|---|---|
| 启动 | `backend/main.py` lifespan，紧跟 `_activate_product_sdk_runtime()` | 调 `deskpet.orchestration.wiring.activate_orchestration`。失败只让编排服务不可用，并记入 `startup_errors`；"未配置模型"不算故障。任何情况下都不会让后端启动失败 |
| 关停 | `backend/main.py` lifespan 关停段，在 SDK stack 之前 | 调 `deactivate_orchestration`。这只是有序关停路径：App 真实退出时后端收到的是 SIGKILL，编排的正确性不依赖这一步 |
| 服务 | `deskpet/orchestration/service.py` 的 `OrchestrationService` | 持有目录锁、provider 快照、`Orchestrator` 实例与驱动循环、SDK facade、部署清单，并做 Host 门口检查。<br>驱动循环失败时按指数退避，时长不超过 `backoff_max_seconds`；指数本身也有上限，失败次数再多也不会溢出。连续 3 次失败就重建运行时，连续 5 次标为 degraded；重建本身失败时循环不退出，只标 degraded 并写明原因。<br>degraded 时，读取、取消、审批决定、接管、评论照常可用，只拒绝新建 Mission（`orchestration_degraded`） |
| 协议 | `deskpet/orchestration/handlers.py`，`/ws/control` 中 `mission_*` / `orchestration_*` 消息 | 纯分发函数，响应格式为 `<type>_response {request_id, ok, data ｜ error_code, error}`，异常不会抛进 socket 循环。payload 不是对象时回 `invalid_request`，不会断开主对话共用的控制通道 |
| 推送 | `deskpet/orchestration/pump.py` 的 `MissionChangePump` | 用只读连接每秒查看各 Mission 的状态和最大 seq，每次写操作后也立即查一次；有变化就广播 `mission_changed` |
| 投影 | `deskpet/orchestration/projection.py` | 按白名单输出，长度有上限；动作参数超过 600 字符时，只给截断后的预览。<br>模型写的文本都标注 `source: "model"`：结果摘要与 claims、Task 目标、critic_review 摘要、审批摘要、动作理由。<br>事件只给 seq、type、时间、Task / Attempt id 与 actor_type，payload 一律不外传；唯一例外是评论事件，带评论文字。<br>产物只给工作区相对路径和 hash，不给 `storage_uri`；不暴露 intents。<br>`ui_state` 按 P3.1 的状态词汇统一推导（请求已接收／排队／运行／待验证／待人／UNKNOWN／正式交付，另有失败、已取消），列表与详情共用同一套 |
| 前端 | `tauri-app/src/views/MissionsView.tsx`、`stores/missionsStore.ts`、`stores/useMissionsFeed.ts`、`components/Sidebar.tsx`（入口"任务编排"，角标显示待审批数）、`components/WorkbenchShell.tsx`（视图 `missions`） | 界面只做投影，所有改动都走控制通道命令。<br>• 常驻订阅：`useMissionsFeed` 挂在 App 上，与视图是否打开无关。启动时拉取 status 和列表；收到 `mission_changed` 就更新 store，并节流重拉列表（最多 1 次/秒），所以侧栏角标和列表的状态词会实时变化。<br>• 事件游标：推送看到的 seq（`lastSeq`，只用来防倒退）与已加载事件的游标（`eventCursor`）分开保存，拉事件一律从游标往后。推送的 seq 超过游标时，连续分页补拉，每页 200 条，一次最多 20 页，超出后显示"加载更多事件"。时间线只显示最近 50 条。<br>• 状态词统一用后端的 `ui_state`。<br>• 详情里还有：产物（"查看产物"，按 id 读取并核对 hash；二进制和截断都会注明）、评论（"评论"、"发表评论"，评论对象是 Mission）、策略漂移提示、等待原因（人工复核、动作审批、仲裁，附开始时间）。<br>• 模型写的文字标注"模型生成，未核实" |
| 服务登记 | `backend/context.py` 的 `_VALID_SERVICES`：`orchestration`、`orchestration_pump` | — |

## 2. 数据

- 正式目录：`<user_data>/data/agent-orchestrator/`，内含：
  - `orchestrator.db`：编排库，只有 SDK 的 Commit Service 写；
  - `execution.db`：编排自己的 SDK 执行库，与主对话的 `execution-v6.sqlite3` 分开；
  - `workspaces/`；
  - `deployment-manifest.json`；
  - `.instance.lock`。
- 测试场景目录：`<user_data>/data/agent-orchestrator-test/`（见 §6），正式库一行不写。
- 两个目录都不能是软链，也不能解析到 user_data 之外。
- **单实例**：编排目录上有 `flock`。第二个进程拿不到锁，就标为 unavailable，不派发任何任务。持锁进程被 SIGKILL 后，锁由内核释放。
- **owner**：每个进程各自一个，形如 `deskpet-orchestrator-<pid>-<随机8位>`。不能共用：SDK 会把同一 owner 名下的有效租约都当成自己的。

## 3. 部署政策（固定）

| 项 | 值 | 理由 |
|---|---|---|
| `code_execution` | 由启动时的探针决定：`sandboxed` 或 `off` | P3.2 §4.3：问题不再是"允不允许执行"，而是"隔离在这台机器上证明过没有"。每次启动跑 SDK 的能力探针（8 项：读家目录、读他人临时目录、写越界、联网、向宿主发信号、完整 daemonize 的回收、输出截断、CPU 上限）；**全过才是 `sandboxed`**，否则 `off`。Host **永不使用 `process_only`**——不隔离的子进程只适合可信代码，而模型写的代码不是。<br>`off` 时（与 P3.1 相同）：`code_test` 不是已部署层；`pytest:` 条件在入口被拒；旧 Task 的 `pytest:` 由规则层判 FAIL；冲突转 DEFERRED；`run_tests` 被拒。<br>`sandboxed` 时：这些重新开放，`run_tests` 进入 `allowed_tools`，模型写的代码只在一次性副本里、经 seatbelt 运行。探针报告写进部署清单与 `status()` |
| `allowed_tools` | 三个工作区工具 | 编排 Agent 拿不到 Host 的任何工具（shell、MCP、文件系统、浏览器都没有） |
| `enabled_connectors` | 默认空；授权发布目录后含 `file_publish`；`test_config` 仅测试场景 | P3.2 P32-14：只有用户在配置里指明了发布目录、该目录存在、**且能承载硬链接**（连接器唯一的原子提交点）时，`file_publish` 才启用。三者缺一就不启用，于是 Mission 连带 `action:file_publish…` 的成功条件都不能提交——拒绝发生在门口，而不是等模型产出候选之后。不支持硬链接的卷（exFAT、部分网络盘）在授权时就被挡下，不会变成运行期的 UNKNOWN |
| `max_action_level` | L2 | 单机只有一个人，满足不了 L3 要求的两个不同的人 |
| 本机执行测试的开关 | 不提供，也不会有 | P3.2 用"隔离是否证明过"取代了"允不允许"。配置里没有任何开关能打开它；决定权在探针，结果在部署清单里可查 |

## 4. 身份与授权

- **Principal**：`local-user:<companion 本机身份 profile_id>`，显示为"本机用户"。单机没有认证，身份就等于能操作这台 App 的人。这一点登记为限制。
- **与 Host 的 auto / manual 模式分开**：auto / manual 只管主对话里的工具效果。编排的人工审批（原文 §22）必须由人来决定：auto 不会自动批准，manual 也不会弹出主对话的授权窗。
- **tenant**：固定为 `local-desktop`。SDK facade 按 tenant 检查每个对象的归属：不属于调用方的对象和根本不存在的对象，一律返回同一句 `not_found`。
- **密钥门口**：下列内容会同时按通用密钥模式和当前 provider 的密钥检查，命中就回 `secret_rejected`，一个字都不写进编排库。provider 密钥即使不是 `sk-` 形态也能拦下。
  - 新建请求里的每一个字符串：目标、成功条件、`stop_conditions`、`synthesis`、`workspace_seed`，连键名也查；
  - 审批的理由和备注、接管依据、评论。
- **seq 是全库自增**：一个编排库只有一个 tenant 时没有影响；多 tenant 共用一个库时，跳号会暴露其他 tenant 的事件量。

## 5. 恢复语义（App 退出 = SIGKILL）

| 被杀的时点 | 新进程里发生什么 | 界面 |
|---|---|---|
| 等人工复核（所有回合都已提交） | 旧 owner 的租约过期（`lease_seconds`）后，新 owner 继续验证**同一个** Attempt，不重做已提交的工作（P3.1-A07） | 复核通过后几秒内进入正式交付 |
| 模型调用中途 | SDK 回合停在 running；心跳里的 `liveness.blocked` 变为 true（`kind: provider`） | 详情里显示"回合结果未知"，可以接管：停止，或带说明重试（`mission_takeover`，basis 必填）。2026-09-12 实测（租约 2 s）：新 owner 启动后约 6 s 出现 blocked。不接管的话，Mission 一直停在 ACTIVE，观察 240 s 也没有自动收敛，所以接管入口是必需的 |
| 其他时点 | SDK 按原文 §16.4 恢复：已完成的不重跑，SUBMITTED 未验证的重新进入验证 | — |

## 6. 仅测试用的场景

- `DESKPET_ORCHESTRATION_TEST_SCENARIO=approval-action`，并且 user_data 位于某个 `.local-test-evidence/` 目录下。两个条件缺一个都不生效，也不依赖 DEV_MODE。
- 生效后：使用独立目录；provider 换成 SDK 的 approval-action 夹具，只用工作区工具；启用测试配置服务连接器 `TestConfigService`；只允许一个 Mission；界面标出"测试场景"。
- 这个场景只用于原生验收里的审批流程，它的通过不代表生产授权。
- 原生启动器 `scripts/native/launch_native_candidate.py` 会丢弃继承来的 `DESKPET_*` 变量，所以要用 `--orchestration-test-scenario approval-action` 把场景传给后端。`--userdata` 必须位于 `.local-test-evidence/` 下，后端才会承认这个场景。

## 7. 设置（`config.toml [orchestration]`）

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | true | 按 CLAUDE.md：测试阶段已完成的能力默认开启 |
| `max_concurrency` / `max_concurrent_model_calls` | 1 / 1 | 与主对话共用 provider 限额。**只在编排库第一次 seed 时进入 ACTIVE 策略**，之后修改只会记一条 `PolicyConfigDrift`，要经策略晋级才会生效 |
| `default_mission_max_tokens` / `default_mission_max_attempts` | 400000 / 12 | 代码常量（`OrchestrationSettings`），不从 config 读。**本部署不提供无上限的 Mission**：<br>• 请求里预算留空的项，由 Host 门口补上这个默认值；<br>• 用户填了的值原样保留；<br>• 0、负数、非整数一律拒绝，不会被默认值替换；<br>• 默认值在进 facade 之前补上，所以回执的 spec hash 已经包含它；<br>• `orchestration_status.mission_budget_defaults` 把默认值下发给表单占位符，详情显示实际生效的预算。<br>依据：原生验收时，留空预算的 Mission 被真实 Planner 编出 800 tokens 的 Task 预算，结果以 `budget_exhausted` 失败。裁决见计划 journal §4.4。<br>12 次的理由：Mission 级尝试次数统计的是所有 Task 的全部 Worker Attempt |

## 8. 模型与费用

- provider 在启动时对 `get_chain()` 的第一个启用项做一次快照，之后换 provider 需要重启才生效。
- DeepSeek 官方端点把 `deepseek-v4-flash` 映射为 `deepseek-flash`，status 里同时显示两个 id。
- 没有注入价目表：金额记为 null，界面显示"未计价"，不写成 0。
- **Task 预算下限**（SDK 0.9.11 起，F-ORCH-1）：
  - Graph Manager 会拒绝预算低于 `k × (base + critic)` 的 Task。k 是每个 Task 的候选数；base 是单轮最多产出的 token 数，这个部署是 8192；critic 部分只在验证政策含 critic_review 时计入，是 Critic 的预留 6000。
  - 被拒后，Planner / Manager 会收到原因（`task_budget_below_floor`）并重新规划，系统不会替它们编一个数。它们的输入里也写明了下限。
  - 下限是**预留层面**的必要条件：它只保证在预留那一刻，第一个 Attempt 和它的 Critic 都能预留得到，前提是一轮结算的用量不超过它的预留。真实模型一轮会连输入一起结算，远超 base（原生验收时一轮结算了 22003），所以预算正好等于下限的 Task，第一轮之后照样可能付不起 Critic，之后的修复和重试也不在保证之内。
  - 这与 Host 门口的默认预算（见 §7）是两道互补的保护。
- **产物的验证状态**（SDK 0.9.11 起，F-ORCH-3），在对应的提交事务里一并写入：
  - 结果被接受：VERIFIED；
  - 结果被判 FAIL：REJECTED；
  - 被取代的候选：保持 UNVERIFIED。

  注意：产物的 REJECTED 和结果的 REJECTED 意思不同。
  - 产物 REJECTED 表示它所在的结果被判了 FAIL。
  - 被取代的结果，其 `verification_state` 也是 REJECTED（verdict=superseded），但它从来没有被评判过，所以它的产物是 UNVERIFIED。

  读模型把两者并排显示时，要按上面的含义解读。
- **Attempt 的 RETRY_WAIT**：这是失败 Attempt 的终态。原文 §25.2 没有 Attempt 的 FAILED 状态，重试的时候另起一个新 Attempt。所以 Mission 结束后，个别 Attempt 停在 RETRY_WAIT 是设计如此，不是还在排队重试。

## 9. 部署清单（P3.1-A08）

`deployment-manifest.json` 与 `orchestration_status.deployment_manifest` 记录以下内容，不含密钥：
- host_commit，以及 `host_dirty`：backend、tauri-app、scripts 下有没有未提交或未跟踪的改动；打包版没有 git 时为 null；
- `simple_harness` / `agent_orchestrator` 实际导入的文件路径与版本；
- distribution 版本与钉版的 wheel sha；
- schema；
- 生效的部署政策、测试场景、设置；
- 模型。

实际导入的版本与钉版不一致时，服务标为 unavailable。这个检查在打开编排库之前做，所以版本不对的 SDK 不会去迁移编排库；这种情况下，清单只记录 `refused: pin_mismatch` 和实际导入的信息。

## 10. 限制与遗留

- 身份是自报的本机用户，没有多用户认证。
- 沙箱（P3.2）的边界，如实记下：
  - **内存与进程数只是软限制**：本机没有 cgroup，`RLIMIT_AS` / `RLIMIT_DATA` 设不进去，`RLIMIT_NPROC` 又按整个 uid 计数，所以只能采样后回收，属于事后处理；`cpu_seconds` 是**每进程**的硬限制，整次执行靠墙钟兜底。
  - **没有 uid 隔离**：没有管理员权限，同一 uid 下的残余风险靠 seatbelt 规则收敛。
  - **元数据可读**：放行 stat 才能让 venv 的软链解析正常，代价是沙箱里的代码能探测任意路径是否存在、大小与修改时间，但读不到内容。
  - **用到的是已废弃且无公开文档的接口**：seatbelt 与 `sandbox_check`（Chromium、WebKit 也在用）。每次启动由探针重新验证；探针不过就退回 `off`。
  - **回收耗时受外部工具影响**：认进程要靠 `ps`，不隔离模式还要靠 `lsof`，两者都有单次超时，但极端情况下一次回收仍可能偏慢；正确性不受影响。
  - **宿主崩溃后的逃逸进程认不出来**：金丝雀随执行目录一起删除，宿主重启时没有线索可扫（SDK journal 已登记，留待后续处理）。
- DeepSeek 价目没有注入，金额显示"未计价"。
- 策略只读：提议、评测、晋级要用 SDK CLI。
- 编排的证据目录（workspaces）不会自动清理。
- PyInstaller 打包 spec 仍停在 0.6.4，尚未跟进。
- 模型调用中途被杀之后，需要人来接管，SDK 不会自己收敛。自动恢复"结果未知"的回合，归入 P3.5 处理。租约为默认 60 s 时，"结果未知"要多久才出现，还没有在原生 App 里测过。
