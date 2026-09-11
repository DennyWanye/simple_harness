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
    后端草稿已按第 2 版计划调整：
    - 实例锁（HA-16）、每个进程独立的 owner、没有模型时不可用（HA-17）、`action:` 条件在门口被拒（HA-18）；
    - SIGKILL 子进程测试（`_child_service.py`）：HA-8 在等人工复核时杀进程，HA-15 在模型调用中途杀进程，然后接管；
    - `mission_takeover`、测试场景的两道门槛与独立目录（HA-20）、判定分歧的仲裁（HA-19）、拒绝后 FAILED + `approval_rejected`、并发写入（HA-21）。
    设计注记：scripted provider 的步骤在事件循环线程里同步执行，所以测试不能在步骤里阻塞。模型调用中途被杀的场景放在子进程里用 `time.sleep` 模拟。
  - SDK S1 进度：`627b90e` 已推送（0.9.8 / 0.9.1，host_support 18 条全绿，ruff 与 mypy 干净）。代码评审第 1 轮结论为 SHIP_WITH_FIXES，处置见 SDK journal §4；全量回归还在跑。
  - **计划升到第 3 版**（plan §0.1）。依据是用户 2026-09-11 22:39 放入的 `plans/taskSys2/agent-orchestrator-phase3-plan.zh-CN.md`，本工作就是其中的 P3.1（Host 直连路径）。新增：
    - SDK 切片 S2（0.9.9）：Facade 严格映射字段、快照与游标一致、按归属检查、`artifact_read`；
    - Host 部署清单 `DeploymentManifestV1`；
    - 去掉"允许本机执行测试"的选项；
    - 新增验收 SB-1 至 SB-6、HA-22 至 HA-24，以及与 P3.1-A01 至 A08 的对照表；
    - H1 改为钉 0.9.9。
  - Phase3 计划文件是用户自己放的，目前没有纳入版本管理；本工作不替用户提交它。
  - SDK 切片 S1 的计划在 SDK 仓库 `plans/2026-09-11-agent-orchestrator/host-support-0.9.8/plan.md`。关键取舍：本机代码执行关闭时，不创建冲突 Task，冲突走 DEFERRED。原因是冲突 Task 本身要在本机跑探针测试，只去掉 code_test 会留下没运行过的"假证据"。
  - 用户规则：真实模型测试一律用 deepseek-flash；额度快用完时先写 handoff，再提交推送。
- 2026-09-12：
  - SDK 已推送的提交：
    - S1 `627b90e`；
    - S2 `8444a39` / `4bd8c52`（0.9.9 / 0.9.2）；
    - 代码评审第 2 轮修复 `7915e40`（0.9.10 / 0.9.3）。处置表见 SDK journal §4.2。
  - Host 实现已完成，但还没有提交：
    - 后端 `backend/deskpet/orchestration/`：paths、lock、settings、provider、manifest、projection、pump、handlers、service、wiring；
    - `main.py` 接线，`context.py` 登记服务；
    - 前端：`MissionsView`、`missionsStore`，Sidebar 与 WorkbenchShell 的入口；
    - 文档 `ARCHITECTURE/AGENT_ORCHESTRATION.md`。
    钉版目前是 0.9.9，要改钉 0.9.10（审批夹具需要 `allowed_tools` 参数）。
  - 测试已经放进 `backend/tests/orchestration/`，按实现修正过：键名、`through_seq`、HA-8 轮询等待、HA-15 轮询等待。`drafts/backend-tests` 和 `drafts/frontend` 两份旧副本在 H2 / H4 提交时删除；`drafts/ui-scripts` 保留。
  - HA-11：新增 `tests/orchestration/test_real_provider.py`，打了 `real_provider` 标记，默认不跑。运行脚本在 scratchpad `real-ha11/run.sh`：只 source 凭证，只用 deepseek-flash，输出里的密钥替换成 `<redacted>`，最后扫描证据，只打印命中数。
  - HA-12 准备：
    - 构建脚本：`.local-test-evidence/2026-09-12/native-build-0910/build.py`，要求工作树干净，bundle 按短 sha 命名；
    - 启动脚本：`.local-test-evidence/2026-09-12/native-ui-0910/launch.sh <prod|scenario> <sha> [--keep]`。它先复制真实数据，再在副本里写入 deepseek-flash、不含密钥的 `llm_runtime.json`；`--keep` 表示在同一份数据上重启；
    - AX 脚本：
      - `ax_set.sh`：按 aria-label 给输入框填值；
      - `ax_dump.sh`；
      - `ax_click_prefix.sh`：按名称前缀点击。侧栏按钮有待审批时，名字会变成"任务编排 待处理 N"，完全匹配会点不到；
      - 固定名称的按钮沿用 `scripts/native/ax_click.sh`。
    - 核对脚本 `ha12_verify.py <mode-dir> [--health]`，只读，不打印密钥。它读取：
      - 启动冒烟项：`native.log` 标记、`/health` 的 `startup_errors`、执行库迁移；
      - 部署清单；
      - 编排库的 missions、attempts、approvals、approval_decisions、actions（除参数外的所有列）、commit_receipts，以及事件类型计数。
      已用 HA-11 run2 的编排库副本空跑，能正常输出。
    - 启动器 `scripts/native/launch_native_candidate.py` 新增 `--orchestration-test-scenario`。原因是启动器会丢弃继承来的 `DESKPET_*` 变量，测试场景变量原本传不进后端。
    - 已核实：真实 `config.toml` 里没有 provider 条目，启动时会用 `llm_runtime.json` 加 `DESKPET_CLOUD_API_KEY` 补种一条临时 provider "primary"。主对话和编排都用这一条，不会用到 luna。
  - HA-15 实测：租约 2 s 时，新 owner 启动约 6 s 后，心跳的 `liveness.blocked` 变为 true（`kind: provider`），Host 显示"回合结果未知"。不接管的话，Mission 会一直停在 ACTIVE：观察了 240 s，没有自动收敛。所以接管入口是必需的，不能省。
  - 进度（2026-09-12 下午）：
    - SDK 全量回归第 4 次：红集 = 基线，0 新红。
    - wheel 0.9.10 已在干净环境验证；SDK journal 已推送（`29daa9c`）。
    - Host 改钉 0.9.10，H1 回归已通过（§2）。
    - 代码评审第 1 轮：SHIP_WITH_FIXES，16 条全部接受并已修完（§1.1）：
      - 后端 `tests/orchestration` 94 passed；控制通道等 5 个文件复跑全绿；
      - 前端 vitest 769 passed，lint 与基线一致。
    - HA-11 真实 deepseek-flash 运行已通过：run1 停在人工复核，run2 到 COMPLETED（§4.2，`reports/real-run1.md`）。
  - 下一步：
    1. 提交 H1 至 H4 加评审修复，推送；
    2. 用干净的提交构建 bundle，做原生 AX 验收（HA-12，含 HA-1 ③ 的启动冒烟）：先跑正式模式，再跑测试场景；
    3. 文档收尾，写终态。
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

## 1.1 代码评审第 1 轮处置（`reports/code-review-round1.md`）

结论 SHIP_WITH_FIXES：5 个 P1、11 个 P2。安全边界的主干经核实没有问题。评审员原话："修完只需定点复查"。16 条全部接受。后端由我修改，前端（P1-1、P1-5 的界面部分、P2-5、P2-8 的界面部分）交给前端子代理，按固定的接口规格实现。

后端修完后，`tests/orchestration` 94 passed / 1 deselected（real_provider），用时 19.6 s。

| 编号 | 处置 | 决定性测试 |
|---|---|---|
| P1-1 推送 seq 被当成事件游标 | 接受，前端修：<br>• store 分开保存两个值：推送看到的 seq（`lastSeq`，只用来防倒退），和已加载事件的游标（`eventCursor`，取"已合并事件的最大 seq"与"快照的 `through_seq`"两者中较大的一个）；<br>• 拉事件一律从游标往后拉；<br>• 同一个 Mission 同时只有一页在途，在途期间收到的推送，等这一页回来后再补拉 | `missionsStore.test.ts` 的"推送只抬 lastSeq""事件游标不倒退"；`MissionsView.test.tsx` 的"P1-1 事件游标"组 5 条，其中"已加载到 seq 5，推送 12 → after_seq 5"就是评审指出的那个场景 |
| P1-2 重建失败导致驱动循环静默退出 | 接受：`_drive` 捕获重建异常，循环继续，状态标 degraded 并写明原因 | `test_review_fixes::test_a_failed_rebuild_keeps_the_loop_alive_and_degraded`。修复前重建异常会逃出循环，`not driver.done()` 必然失败；另断言恢复后回到 available |
| P1-3 provider 密钥只查目标与条件 | 接受：新增 `_refuse_secrets`，遍历请求里的全部字符串（连键名），决定、接管、评论同样检查，并带上 provider 密钥 | `test_provider_key_door`，共 8 条。用的密钥不是 `sk-` 形态，只有 provider 密钥分支能拦下。修复前，stop_conditions、workspace_seed、synthesis、决定、接管、评论这 6 条会穿过门口 |
| P1-4 HA-15 的断言放在条件分支里 | 接受：去掉条件分支，改成无条件断言；`raises` 收紧为 `OrchestrationRequestError(invalid_request)`；另外断言 `ui_state == "unknown"`，以及接管后 blocked 清空 | `test_restart_recovery::test_killed_inside_a_model_call_is_shown_and_can_be_taken_over` |
| P1-5 缺产物、评论、漂移、等待原因的界面 | 接受。后端补了 `policy_status.drift`、事件投影、`ui_state`。前端：<br>① 产物列表与"查看产物"：utf-8 内容直接显示，并标"模型生成，未核实"；截断的注明；binary 不显示；hash 不符时报错；<br>② 评论框，评论对象是 Mission；审批卡片下显示已有评论；<br>③ 策略漂移提示；<br>④ 等待原因；<br>⑤ 没被判为 blocked、但仍未结束的 Task 也提供"接管这个 Task"。这一项子代理没做，我补上了 | 后端：`test_policy_readonly::test_a_later_settings_change_is_shown_as_drift_not_applied`；`test_review_fixes::test_an_artifact_is_read_by_id_with_its_hash_checked`。<br>前端："P1-5 缺失界面"组（a 两条、b、c、d 两条），以及"P1-5⑤"组 3 条 |
| P2-1 degraded 时一律拒绝 | 接受：handlers 在 degraded 时放行；service 在 degraded 时拒绝新建（`orchestration_degraded`） | `test_review_fixes::test_degraded_still_cancels_but_refuses_new_missions` |
| P2-2 钉版检查排在打开编排库之后 | 接受：检查挪到 `_open()` 之前；版本不一致时只写 refused 清单 | `test_deployment_manifest::test_a_version_that_differs_from_the_pin_never_opens_the_library`（断言 `orchestrator.db` 不存在） |
| P2-3 事件没有经过投影 | 接受：新增 `project_events` 白名单 | `test_projection::test_an_event_row_never_carries_its_payload`；分页测试逐条断言没有 payload |
| P2-4 部分模型文本没有标注来源；动作参数没有长度上限 | 接受：Task 目标、critic_review 摘要、审批摘要都标 model；动作参数超过 600 字符只给截断预览 | `test_projection::test_approval_text_is_marked_and_action_params_are_bounded`；详情测试补了来源断言 |
| P2-5 角标不会实时更新 | 接受，前端新增 `useMissionsFeed`，挂在 App 上常驻：<br>• 启动时拉取 status 与列表；<br>• 收到推送后节流重拉列表，最多 1 次/秒；<br>• 视图不再重复做这些事，也不再把别的视图的失败应答当成自己的错误 | `useMissionsFeed.test.tsx` 6 条（含侧栏角标实时更新）；视图的"P2-5"组 |
| P2-6 非对象 payload 会打断控制通道 | 接受：由 `handle` 自己校验；`main.py` 把原始 payload 与 request_id 直接交给 `handle` | `test_review_fixes::test_a_payload_that_is_not_an_object_is_answered`，列表、字符串、数字三种形态 |
| P2-7 退避指数溢出 | 接受：新增 `backoff_delay`，指数封顶 16 | `test_the_backoff_delay_is_capped_and_never_overflows`；`test_a_long_failure_streak_never_ends_the_loop`：失败次数从 5000 起，驱动任务不结束 |
| P2-8 时间线与状态词汇 | 接受。后端新增 `projection.ui_state`。前端：<br>• 从游标 0 开始连续分页，每页 200 条，一次最多连拉 20 页，超出后显示"加载更多事件"；<br>• 时间线只渲染最近 50 条，并注明总数；<br>• 列表行与详情头改用 `ui_state` 的状态词，保留 `data-status`，新增 `data-ui-state`；<br>• 切换到别的 Mission 后，迟到的旧详情不会把当前详情覆盖掉 | `test_projection::test_ui_state_is_the_p31_vocabulary`（13 个分支）；前端"P2-8 时间线"组 4 条、"ui_state 词汇"组 3 条 |
| P2-9 `host_commit` 不管工作区是否干净 | 接受：清单加 `host_dirty` | `test_deployment_manifest::test_manifest_records_the_imported_sdk_and_the_fixed_policy` |
| P2-10 断言偏弱或缺失 | 接受：<br>• boundaries 两处改为精确断言：Mission 为 FAILED、有 stop_reason、没有 Attempt；工具集合与三个工作区工具完全相等，不含 run_tests；<br>• HA-21 补上审批决定与评论并发；<br>• 新增部署清单测试、HA-23 产物读取测试、`test_wiring_isolation`（3 条） | 对应文件均已通过 |
| P2-11 文档与代码不一致 | 接受：`AGENT_ORCHESTRATION.md` 已同步服务、协议、投影、密钥门口、清单、前端几部分 | — |

## 2. 切片

| 片 | 提交 | 内容 | 测试结果 |
|---|---|---|---|
| H0 | | 计划文档 | — |
| H1 | | 钉版 0.9.10（计划 §3.2 修订） | 2026-09-12：<br>• 钉版：`pin2.py` 一次完成，wheel sha `f36f467b…93b0`，manifest sha `3d858528…bb1f`，源提交 `7915e40`；导入版本为 `0.9.10 0.9.3`；候选校验通过；新建 installed target `installed-h0910-s0313`；0.8.0 的 wheel 与 manifest 已删除（grep 确认只有它自己的 manifest 引用它）。<br>• 回归（基线命令，三个目录）：68 failed / 943 passed / 1 error，用时 362 s。失败清单与 `baseline-backend-failures.txt` 逐条比对：69 = 69，**0 新红**，也没有意外变绿。<br>• 控制通道 / 启动 / context 相关的 5 个文件：52 passed / 1 skipped，与基线相同，全绿。<br>• 启动冒烟（HA-1 ③）：改在第一次原生启动时一并验收（HA-12 ⑥）。09-10 那次冒烟是手动 `python main.py` 起后端，而 Host CLAUDE.md 禁止手动起 `main.py`，所以这次改由 Tauri bundle 拉起后端。从原生日志与数据副本中核对四项：`/health` 为 200、`startup complete`、`startup_errors=[]`、执行库 `sdk_schema_migrations` 为 `[…, 9, 10]` |
| H2 | | 后端编排服务 | 2026-09-12，钉 0.9.10 后：`tests/orchestration` 56 passed，1 条被排除（`real_provider`，只在显式 opt-in 时运行），用时 17.8 s。其中包括 HA-8（等人工复核时 SIGKILL，租约过期后同一个 Attempt 完成）和 HA-15（模型调用中途 SIGKILL，界面显示"回合结果未知"，接管后完成） |
| H3 | | 控制通道协议 | 同上，`test_handlers_contract` 覆盖了全部 12 种消息、信封格式、`approval_id`、`artifact_read`，以及服务不可用时的应答 |
| H4 | | 前端视图 | 2026-09-12 初版：typecheck 通过；vitest 96 个文件、736 条全部通过（基线 723 条，新增的是编排视图与 store 的测试）；lint 共 161 条，按文件和级别逐一对比，与基线完全一致，新文件和改动过的文件都没有新增问题。<br>评审修复后（含 `useMissionsFeed` 与 P1-5⑤）：typecheck 通过；vitest 97 个文件、**769 条全部通过**；全仓 lint 仍是 161 条，按文件和级别与基线逐一一致；新建和改动的文件都是 0 问题，只有 `App.tsx` 保留原有的 15 条 `no-explicit-any`，数量没有变化 |
| H5 | | 真实模型、原生验收、文档 | |

## 3. Host 装配位置登记（按 ORCH-BUILD 第 15 行）

| 项 | 位置（2026-09-12，钉 0.9.10 的工作树） |
|---|---|
| 启动 | `backend/main.py:5674`：lifespan 在 `_activate_product_sdk_runtime()` 之后调用 `activate_orchestration(...)`。失败只记入 `startup_errors`，"未配置模型"不记 |
| 关停 | `backend/main.py:5825`：在 SDK stack 关闭之前调用 `deactivate_orchestration(service_context)`。只是有序关停；SIGKILL 时这一步不会执行，也不需要执行 |
| 控制通道 | `backend/main.py:14700`：`/ws/control` 中 `mission_*` / `orchestration_*` 分支交给 `deskpet.orchestration.handlers.handle` |
| 模块 | `backend/deskpet/orchestration/`：paths、lock、settings、provider、manifest、projection、pump、handlers、service、wiring |
| 服务登记 | `backend/context.py` 的 `_VALID_SERVICES` 加了 `orchestration`、`orchestration_pump` |
| 数据目录 | `<user_data>/data/agent-orchestrator/`；测试场景用 `<user_data>/data/agent-orchestrator-test/` |
| 生产事实 | `ARCHITECTURE/AGENT_ORCHESTRATION.md` |
| Host 提交 | 见 §2 切片表 |

## 4. 真实模型与原生验收

### 4.1 HA-15 计时（2026-09-12，scripted provider，子进程 SIGKILL）

租约 2 s 时，新 owner 启动约 6 s 后，心跳的 `liveness.blocked` 变为 true（`kind: provider`），Host 的 `_blocked()` 报 `turn_outcome_unknown`。不接管的话，Mission 停在 ACTIVE：观察 240 s，Attempt 一直是 RUNNING，没有自动收敛。据此，HA-15 测试改成最多轮询 45 s 等待 blocked 出现。

### 4.2 HA-11 真实模型

**run1**（2026-09-12，钉 0.9.10 的工作树，deepseek-flash 官方端点，证据在 `.local-test-evidence/2026-09-12/real-ha11-run1/`）：

- 45 s 后停在一个人工复核请求上，Mission 仍为 ACTIVE。测试按设计不替人做决定，所以判失败。这是 SDK 的正常流程，不是 Host 缺陷。
- Planner 把目标拆成 2 个 Task，并给 task-2 的验证政策加上了 `human_review`：
  - task-1：format、rule、critic 三层都 PASS，状态 DONE；
  - task-2：rule_check、critic_review 都 PASS，停在 `human_review` 层（SUSPENDED）。
- 两份 `SUMMARY.md` 都是 4 句中文，提到了新建 Mission、查看进度、人工审批。
- Worker 还自己写了 `tests/test_summary_compliance.py`，但它**没有被执行**：code_test 层为 NOT_REQUIRED，Critic 也记下"本轮没有可用的独立测试运行结果"。这是第一次在真实模型下看到 P3.1 §3.1"关闭本机代码执行"生效。
- 用量：2 个 Attempt，预留 40000 tokens；金额未计价（`amount_micros=None`）。
- 证据扫描：12 个文件，`sk-` 模式与真实密钥的命中都是 0。

处置：测试加了 `SH_REAL_REVIEW=pass` 开关。设了这个开关，测试执行者只代为通过 review 类请求，并在 note 里如实写明"按预先设置通过"；遇到其他类型的请求，照旧停下。产物内容保存在证据里，事后由执行者核对，结论写进 `reports/real-run1.md`。

**run2**（2026-09-12，评审修复后的工作树，证据在 `real-ha11-run2/`）：**PASS**。

- 用时 40 s，Mission 为 COMPLETED（`verification_passed`，`ui_state=delivered`）。
- task-2 的 human_review 发起了一次复核，由执行者按预设通过。我读过产物全文：`SUMMARY.md` 正文是 4 句中文，覆盖新建 Mission、查看进度、人工审批三点，同意这次复核结论。
- Worker 这次又写了 `test_summary.py`，同样没有被执行。
- 证据扫描：14 个文件，命中都是 0。

HA-11 判定为通过，报告见 `reports/real-run1.md`。

## 5. 遗留

（待填）

## 6. 结论

（待填）
