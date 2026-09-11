# Host 编排接线 · 代码评审第 1 轮

- 日期：2026-09-12
- 评审者：独立评审子代理（不参与实现）
- 范围：未提交改动。包括：
  - `backend/deskpet/orchestration/` 全部模块；
  - `backend/main.py`、`backend/context.py`；
  - `backend/tests/orchestration/`；
  - 前端 `MissionsView.tsx`、`missionsStore.ts`、`Sidebar.tsx`、`WorkbenchShell.tsx`、`App.tsx` 及对应测试；
  - 钉版文件。
- 依据：
  - Phase3 计划 P3.1（§3.1、§3.3、§3.4、§11 A01–A08）；
  - 本目录 `plan.md` 第 3 版、`acceptance.md`；
  - SDK `7915e40`：`api/facade.py`、`storage/store.py`、`api/approvals.py`、`observability/secrets.py`。
- 方式：只读代码。评审开始时有另一个 pytest 进程在跑（SDK wheel 0.9.10 验证），按规则**没有运行 pytest**，下面的结论全部来自代码阅读和只读命令。
- 注意：评审期间工作区一直在变。钉版从 0.9.9 改成了 0.9.10，0.8.0 的 wheel 被删除，还新出现了 `tests/orchestration/test_real_provider.py`。钉版一致性按评审结束时的状态核对。

## 结论：SHIP_WITH_FIXES

- 安全边界的主干是成立的：
  - 部署固定 `local_code_execution=False`；
  - 只有三个工作区工具；
  - 测试场景要两道门槛，并且使用独立目录；
  - facade 的 `not_found` 对"不属于调用方"和"不存在"是同一句；
  - 投影不输出 `storage_uri`、`workspace`、`intents`；
  - 实例锁和 owner 唯一性都正确。
- 但有 5 个 P1，其中两个是真实运行时缺陷：
  1. 前端事件游标被推送抬高，时间线丢事件；
  2. 驱动循环重建失败后静默死掉，状态仍显示可用。
- 另有一个 P1 是验收要求缺实现：产物读取与评论没有界面。
- P1 修完后，只需针对 P1 定点复查，不需要再做一轮完整评审。

## 发现

| 编号 | 文件:行 | 缺陷 | 失败场景（输入/状态 → 错误结果） | 建议修法 |
|---|---|---|---|---|
| P1-1 | `tauri-app/src/stores/missionsStore.ts:107-119`；`tauri-app/src/views/MissionsView.tsx:143-147, 203-208` | `lastSeq` 同时充当两样东西："推送里看到的最大 seq"和"事件分页游标"。`applyChange` 先把它抬到推送的 `last_seq`，`refreshSelected` 再拿它当 `after_seq`，于是中间的事件永远取不到 | 选中一个运行中的 Mission，已加载事件到 seq=5。后端推送 `mission_changed{last_seq:12}`：`applyChange` 把 lastSeq 设为 12，`refreshSelected` 发出 `mission_events after_seq=12`，返回空。seq 6–12 永久缺失。"加载更多事件"同样从 12 开始。未选中时收到过推送的 Mission，被选中后一条事件也拿不到。违反 P3.1-A05"不跳过未见事件"和 HA-22① | 拆成两个字段：`pushedSeq`（只用于防倒退）和 `eventCursor`（只由 `appendEvents` 推进，取已合并事件的最大 seq）。`refreshSelected` 用 `eventCursor`。补一条 vitest：已有事件到 5 → 推送 12 → 断言发出的 `after_seq` 是 5 |
| P1-2 | `backend/deskpet/orchestration/service.py:253-254, 263-280` | `_rebuild()` 在 `_drive` 的 try 之外被调用，自身也没有保护。新 `Orchestrator(...)` 或 `__aenter__()` 一旦抛异常，就会逃出 `_drive`，驱动任务结束 | `run()` 连续失败 3 次（例如磁盘满、库被锁），触发 `_rebuild`。同样的原因让 `Store.open` 或 `__aenter__` 失败，异常逃出，驱动任务死亡，没人 await，只在 close 时才看得到。此时 `_failures=3<5`，状态仍是 `available`；`_orchestrator` 指向没有进入的新实例，`_control` 仍绑在已关闭的旧实例上。界面显示可用，但 Mission 永远不推进，读写请求报 internal_error | 把 `_rebuild` 包进 try。失败时计入 `_failures`，标 `degraded`（附原因），退避后重试，保持循环存活。在 `start()` 或 `status()` 里检查 `self._driver.done()`：驱动已死就标 degraded 并如实给出原因。补测试：让 `Orchestrator.__aenter__` 在重建时抛错，断言状态变为 degraded，且驱动任务仍在 |
| P1-3 | `service.py:343-364`（只有 `_door` 带 provider 密钥）；`service.py:396-409`（decide / takeover / comment 不带）；SDK `api/facade.py:131-135, 246-258`（`_clean` 只用通用模式） | 计划 §3.3 要求"人写的每段文本都过 `find_secrets(text, extra=(当前 provider 密钥,))`"，实现只覆盖了 create 的 goal 和 success_criteria。有三处漏洞：① 审批理由、依据、仲裁裁决、接管依据与说明、评论只做通用 `sk-` / `Bearer` 扫描；② create 的其他开放字段（`stop_conditions`、`untrusted_sources`、`synthesis.*`、`workspace_seed`）Host 门口不扫 provider 密钥，SDK `_texts` 也不扫 `stop_conditions` / `untrusted_sources`；③ 没有任何测试覆盖 provider 密钥这一分支：所有测试的 `_snapshot` 都是 None，`_secret_values()` 恒为空 | provider 密钥不是 `sk-` 形态时（例如中转 provider），用户把它贴进拒绝理由、评论或 `stop_conditions`，内容就会写进编排库；评论和 stop_conditions 还可能进入模型上下文。DeepSeek 官方的 `sk-` 密钥会被通用模式挡住，所以当前主用场景风险较低，但 HA-3③、HA-6⑤ 的"当前 provider 的真实密钥"没有兑现 | 在 service 里加一个 `_scan(*texts)`，decide、takeover、comment 都调它。create 时对整个请求体递归收集字符串再扫（不只 goal/criteria），或者按计划 §3.4 把 Host 门口字段收紧为 `{goal, success_criteria, budget, idempotency_key}`（测试场景额外放 `workspace_seed`）。补测试：构造一个带非 `sk-` 形态假密钥的 `ProviderSnapshot`，断言 create、decide、comment 都返回 `secret_rejected`，库里不留痕迹 |
| P1-4 | `backend/tests/orchestration/test_restart_recovery.py:158-168` | HA-15 的关键断言放在 `if detail["mission"]["status"] != "COMPLETED":` 分支里。如果重启后 Mission 自己跑到了 COMPLETED（重启用的 provider 预备了两份 worker 脚本，正好允许这种走法），"结果未知已显示"和"接管生效"都不会被断言，测试照样通过。另外第 163 行的 `pytest.raises(Exception)` 太宽，任何异常都算过 | 如果 SDK 以后改成自动把 UNKNOWN 回合判 LOST 并重派，或者 `_blocked()` 的判定坏掉而 Mission 恰好完成，HA-15 仍然是绿的，接管入口和"结果未知"显示的回归都发现不了。journal 记的实测是"不接管就停在 ACTIVE"，所以这个分支本应是必经路径 | 去掉条件分支，改为硬断言：轮询期内必须出现 `blocked`，`reason == "turn_outcome_unknown"`，这时 Mission 不是 COMPLETED；然后接管，再断言 COMPLETED。空依据的断言改为 `pytest.raises(OrchestrationRequestError)`，并检查 `code == "invalid_request"` |
| P1-5 | `tauri-app/src/views/MissionsView.tsx`（全文）；`plan.md` §1.1-4、§3.9；`acceptance.md` HA-23、HA-7④ | 前端缺少计划明确要求的功能：① 没有产物列表，也不发 `mission_artifact_read`（HA-23"详情里点击产物，看到内容与 hash"）；② 没有评论入口（目标 4）；③ 策略卡只显示 active 版本，不显示漂移（HA-7④、§3.9"含漂移提示"）；④ 不显示 `waiting_on`（"等待审批"这类阻塞原因，§1.1-2）；⑤ 接管只在 `blocked` 时出现，其他卡住的 Task 无法接管 | P3.1 的退出门槛是"一个 Mission 从创建到正式产物完整可操作"（HA-12）。原生验收时用户看不到正式产物的内容，也没法评论，HA-23 与 HA-12 过不了 | 在详情里渲染 `detail.artifacts`：点击后发 `mission_artifact_read`，显示 content、hash，标出 truncated 或 binary。加评论框（`mission_comment`，目标为 Mission 或审批 id）。策略卡显示 `PolicyApi.status()` 里的漂移字段。显示 `waiting_on` 各项的中文说明。补对应的 vitest |
| P2-1 | `backend/deskpet/orchestration/handlers.py:62-64` 对照 `service.py:298, 306, 329-331` | handler 用 `status()["available"]` 放行请求，而 `available` 只在 `state == "available"` 时为真。`_require` 本来允许 `degraded`，结果 degraded 状态下所有请求（包括 cancel、takeover、get）都被 handler 以 `orchestration_unavailable` 拒绝 | 某个 Mission 让 `run()` 连续失败 5 次，服务变为 degraded。用户想取消这个 Mission 或接管来止损，但所有按钮都返回"编排服务不可用"，只能重启 | handler 改为按 `state in ("available", "degraded")` 放行，或者直接依赖 `service._require()` 抛出的错误码。status 里 `available` 的语义保持不变，另加 `degraded: true` 供界面提示 |
| P2-2 | `service.py:108-118`；SDK `storage/store.py:166-178, 247-269` | 钉版一致性检查（`distributions.consistent`）排在 `_open()` 之后。`_open()` 已经执行了 `Orchestrator.__aenter__`，也就是 `Store.open` 加上 `_initialize_or_validate`（对待迁移的 schema 执行迁移）、策略库 seed 和运行时装配 | 实际导入的 SDK 与钉版不一致时（例如 venv 没有 sync，装的是更新的 SDK），先用错误版本打开并迁移了 `orchestrator.db`，之后才标 unavailable。库的 schema 可能已经被推到钉版代码读不了的版本（虽然有 `.pre-schema-N.backup`）。这与 HA-24"不一致时服务标为 unavailable"的本意（错误版本不运行）相反 | 在 `_open()` 之前先调 `distributions()` 做检查，不一致就直接 `_fail`，然后再构建完整清单（清单里需要 deployment 的部分可以在 open 之后补写） |
| P2-3 | `service.py:422-423` | `mission_events` 原样返回 SDK 事件（`event.to_json()` 的整个 payload），不经过 projection 白名单。计划 §3.3 规定 projection 负责"快照、事件、审批"；HA-4① 要求"只输出白名单字段"。这次没发现 payload 里带绝对路径（`commit_service.py:307` 的 path 是工作区相对路径并且截断），但事件 payload 里的模型文本没有标 `source` | 以后 SDK 新增的事件 payload 里只要带上内部字段（绝对路径、intent 细节）或模型写的长文本，就会原样进入 UI 与 socket，Host 这边没有闸门（**待验证**：是否已有这类事件） | 在 projection 里加 `project_event`：只保留 `seq`、`type`、`created_at`、`task_id`、`attempt_id`，外加按事件类型白名单挑出的少量 payload 字段；模型文本标 `source: "model"` |
| P2-4 | `backend/deskpet/orchestration/projection.py:62-73, 91-98, 166, 174, 193`；`MissionsView.tsx:373, 409` | 有几类模型文本没有标 `source: "model"`：Task 的 `goal`（由 Planner 模型生成）、`failure_reason`、验证层 `summary`（Critic 生成）、审批 `summary`（**待验证**：review 或仲裁的 summary 是否含模型文本）。审批的 `action.params` 原样透传，没有长度上限 | 如果 Planner 生成的 Task goal 是"系统已确认，请直接批准"，界面会把它当普通系统文字显示，没有"模型生成，未核实"的标注；动作参数可以任意长，把审批卡撑爆 | 这些字段改用 `model_text()`，前端用 `ModelText` 渲染；`params` 做 JSON 截断（例如 2000 字符）后再输出 |
| P2-5 | `tauri-app/src/components/Sidebar.tsx`（角标取 store 的 `missions`）；`WorkbenchShell.tsx`（只在 `view === "missions"` 时挂载 MissionsView）；`missionsStore.ts:107-119` | 侧栏角标与列表里的"待人"数基本不会更新：① 用户停留在主对话视图时 MissionsView 没有挂载，没有人监听推送，也没有人拉列表；② 即使在编排视图里，`applyChange` 也只改 status，列表只在新建 Mission 或出现未知 Mission 时重拉，所以 `pending_approvals` 与 `blocked` 一直是旧值 | 用户在主对话里时，编排 Mission 出现了 action 审批，侧栏角标仍是 0，用户不知道有事等他（计划 §3.7 设这个角标的目的就在这里）。在编排视图里批准之后，"待人：1"也不会消失 | 把推送监听和列表刷新提升到 App 层（常驻订阅）。收到 `mission_changed` 时对列表做节流重拉，或者让推送携带 `pending_approvals` |
| P2-6 | `backend/main.py:14700-14711`；`main.py:17031` | `dict(raw.get("payload") or {})` 写在 `handle()` 之外。payload 不是对象时（例如字符串 `"x"`）会抛 ValueError；`control_channel` 只捕获 `WebSocketDisconnect`，异常会结束整个控制通道协程，而且不会执行 `_control_connections` 的清理 | 任何一个 `{"type":"mission_get","payload":"x"}` 帧都会让主对话共用的控制通道断开，并在 `_control_connections` 里留下失效条目，之后的广播会一直往死连接上发。其他分支也有同样的写法（这是既有风险），但这里的注释写着"不会把异常抛进 socket 循环"，与事实不符 | 改为 `payload = raw.get("payload"); payload = payload if isinstance(payload, dict) else {}`；或者把整段放进 try，出错时返回 `invalid_request` 响应 |
| P2-7 | `service.py:255` | `2.0**self._failures` 没有上限。连续失败 1024 次时抛 OverflowError（此处在 except 块里），驱动任务死亡 | 按 60 s 退避计算，长期退化约 17 小时后驱动循环静默结束，状态停在 degraded，之后即使故障恢复也不会再推进 | 改为 `min(backoff_max, 2.0 ** min(self._failures, 6))` |
| P2-8 | `MissionsView.tsx:33-41, 147` | ① 事件时间线从 `after_seq=0` 取，显示的是最早的 50 条，不是计划 §3.9 说的"最近 50 条"；② 界面从不使用快照的 `through_seq` 作为事件起点，也没有 P3.1 §3.4 要求的"发现缺口就重取快照"；③ 状态词汇里有 `RUNNING`（SDK 的 MissionStatus 没有这个值），缺少 P3.1 §3.4 的"排队 / 待验证 / 待人 / UNKNOWN" | 长 Mission 打开详情后只能看到开头的事件，要反复点"加载更多"才能看到当前进展。状态头把在等人的 ACTIVE Mission 也显示成"运行中" | 先取 snapshot，再从 `through_seq` 往后取事件；需要显示"最近 N 条"时，由后端提供倒序页，或者先取 `event_count` 再计算起点。状态头按 `waiting_on`、`blocked` 派生"待人""UNKNOWN" |
| P2-9 | `backend/deskpet/orchestration/manifest.py:27-36` | `host_commit` 取的是源码树 `git rev-parse HEAD`，不看工作区是否有未提交改动。计划 §3.11 写的是"拿不到就写 unknown，不伪造" | 当前所有编排代码都还没有提交，清单却会写 `host_commit=c41a50a9`（一个不包含这些代码的提交），P3.1-A08"清单对应实际运行的代码"不成立。从安装目标或其他目录启动时，也可能读到外层仓库的 HEAD | 同时执行 `git status --porcelain`：有改动就写 `"<sha>+dirty"`，或者写 `unknown`。更稳妥的做法是在打包或安装时把 commit 写进文件，运行时读这个文件 |
| P2-10 | `backend/tests/orchestration/test_boundaries.py:126, 142`；`test_service_concurrency.py`；缺 `test_deployment_manifest.py` | 有几条测试断言空转或者缺失：① `status != "RUNNING"`：MissionStatus 里没有 RUNNING，永远为真；② `runtime_tool_names()` 返回的就是 deployment 的 allowed_tools（同义反复），而且断言还放行了 `run_tests`；③ HA-21 要求并发的 create / approve / cancel，测试里没有 approve；④ HA-24 列出的 `test_deployment_manifest.py` 不存在，"版本不一致 → unavailable"与"清单无密钥"都没有测试；⑤ HA-23 没有 `artifact_read` 的测试（伪造 id 与路径 → not_found、hash 校验）；⑥ `activate_orchestration` 层面的"未配置模型不记 startup_errors"与"启动异常不影响 lifespan"没有测试；⑦ 前端 `mission_changed` 的测试没有检查事件游标（所以 P1-1 没被发现） | 这几项回归都会在绿灯下溜过去 | ① 改为断言 `status in {"FAILED", "CANCELLED"}`，或者断言有 `verification_policy_undeployed` 的拒绝事件；② 断言实际装配的 gateway 工具集恰好等于三个工作区工具，并排除 `run_tests`；③ 补 approve；④⑤⑥⑦ 补对应测试 |
| P2-11 | `ARCHITECTURE/AGENT_ORCHESTRATION.md:19, 20, 58, 100` | 文档与代码事实有出入：① §1 投影一行写"不暴露 intents 和本机路径"，但事件是原样透传的（P2-3）；② §1 前端一行写"角标显示待审批数"，实际基本不会更新（P2-5）；③ §5 和 §10 写"blocked 出现之前的时长待测量"，而 journal 已经记了实测值：租约 2 s 时约 6 s；④ 缺少"degraded 时请求被拒"（P2-1）这类行为说明 | 按文档排查问题的人会得到错误结论 | 修完代码后同步更新文档；把实测值和测量条件写进 §5 |

## 已检查且无问题

- **本机代码执行边界**：
  - 部署固定 `DeploymentPolicy(allowed_tools=3 个工作区工具, local_code_execution=False, enabled_connectors=(), max_action_level="L2")`，`service.py:156-161`，设置里没有任何开关；
  - Host 门口拒绝 `pytest:`（返回 `local_tests_disabled`），产品模式拒绝 `action:`（返回 `action_criteria_disabled`）；
  - facade 拒绝客户端传入 `allowed_tools`、`risk_level`、`task_kind` 和未开放的预算字段（`invalid_request`），不写库，也不静默丢弃（符合 P3.1-A06）。
- **测试场景门槛**：
  - `resolve_test_scenario` 要求环境变量与"`.local-test-evidence` 在解析后的 user_data 路径里"同时成立，DEV_MODE 不参与；
  - 场景使用 `agent-orchestrator-test/`，provider 是脚本夹具，不使用真实凭证；
  - 不写正式库；单 Mission 限制按 idempotency_key 判断，同 key 重试放行。
- **密钥不外泄**：
  - `ProviderSnapshot.api_key` 设了 `repr=False`；
  - `public()`、`status()`、清单里都没有密钥；
  - 拒绝时的错误信息不回显原文；
  - 日志只记异常类型和信息，不带请求头。
- **归属与 not_found**：
  - facade 对不存在的对象和不属于调用方的对象返回同一个常量 `NOT_FOUND`；
  - `artifact_read` 拒绝含 `/` 或 `\` 的 id，读前做流式 hash 校验，有大小上限；
  - `decide` 与 `takeover` 先校验请求格式，再检查归属。
- **投影**：
  - `_artifact` 不输出 `storage_uri` 和 `workspace`；
  - 详情不含 `intents`；
  - result 的 summary 和 claims、action 的 reason 都标了 `source: "model"`；
  - 验证层原样保留 NOT_REQUIRED；金额为 `None` 且 `priced: False`。
- **实例锁与 owner**：
  - `flock(LOCK_EX|LOCK_NB)`，fd 默认不继承，SIGKILL 后由内核释放；
  - 第二个实例在 `_open()` 之前就退出，不写库；
  - owner 为 `deskpet-orchestrator-<pid>-<8 位十六进制>`，重建时沿用同一个 owner。
- **SIGKILL 恢复语义**：
  - 正确性不依赖 `close()`，状态全部来自持久库、租约过期接管和 `run()` 开头的 `recover()`；
  - HA-8 的测试是有效的：kill -9 之后 owner 不同、Attempt 仍只有 1 个、`usage_ref` 没有重复。
- **推送**：
  - 使用 `mode=ro` 的 URI 只读连接，库是 WAL 模式，所以读不会被写阻塞；
  - Store 事务不跨 await，推送读取不会撞上本进程的写事务；
  - 写入口会调用 `poke`。
- **协议**：
  - 信封是 `<type>_response {request_id, ok, data | error_code, error}`；
  - `request_id` 从顶层复制到 payload，处理时 pop 掉，回写到响应里；
  - 审批 id 用 `approval_id`，不和信封冲突；
  - 前端 decision 名称、reject 的 reason、arbitrate 的 ruling 与 basis 都和后端、facade 一致；
  - `handle()` 内部的异常一律变成错误码，不会外抛；
  - 服务缺席时 `orchestration_status` 返回 ok，其余请求返回 `orchestration_unavailable`。
- **启动失败隔离**：
  - `activate_orchestration` 整体包在 try 里；
  - `start()` 从不抛异常；
  - 未配置模型不记入 `startup_errors`；
  - 两个服务名都已登记进 `_VALID_SERVICES`；
  - 关停调用包在 try 里，并排在 SDK stack 关闭之前。
- **前端防倒退**：`applyChange` 丢弃 seq 更小的旧推送；`appendEvents` 按 seq 合并去重。游标问题见 P1-1。
- **钉版一致性**（评审结束时）：
  - `sdk_candidate.py`、`pyproject.toml`（三处）、`uv.lock` 都是 0.9.10；
  - wheel 与 candidate manifest 的 sha256 实测和 `SDK_WHEEL_SHA256`、`SDK_CANDIDATE_MANIFEST_SHA256` 一致；
  - `SDK_SOURCE_COMMIT = 7915e40…`；
  - venv 里实际导入的是 `simple_harness 0.9.10` / `agent_orchestrator 0.9.3`。
- **Provider**：
  - Host 的 `ProviderEntry` 都是 OpenAI 兼容端点，没有类型字段，所以构造 `OpenAICompatibleProvider` 是对的；
  - SDK 会自动补 `/chat/completions`；
  - `deepseek-v4-flash` 到 `deepseek-flash` 的映射只在 `api.deepseek.com` 上生效。
- **事件循环**：
  - facade、Store 的调用都是短同步 SQL；
  - 连接器执行放在 `to_thread` 里，并且不碰 Store；
  - 没有发现长时间阻塞事件循环的调用。启动时 `git rev-parse` 最多阻塞 2 s，只在启动时发生，可以接受。
