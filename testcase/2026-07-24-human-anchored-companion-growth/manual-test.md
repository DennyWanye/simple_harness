# DeskPet 人类锚定伴生智能体成长闭环手工验收

> 状态：FROZEN — PARTIAL / BLOCKED
> 对应计划：`F:\projects\deskpet\plans\2026-07-24-human-anchored-companion-growth\plan.md` Task 15
> 验收事实源：`F:\projects\deskpet\plans\2026-07-24-human-anchored-companion-growth\acceptance.md`
> 结果记录：`F:\projects\deskpet\plans\2026-07-24-human-anchored-companion-growth\evidence\manual-results.md`

本文只定义测试步骤和证据契约，不代表任何场景已经执行或通过。S-1～S-5、S-8
必须在点击桌宠“消息”后打开的主消息页面，由真人式坐标点击和真实中文输入完成；
S-6、S-7 按验收标准走确定性自动化。协议直注、WebSocket 直注、后端 API、
数据库写入、pytest 或脚本回放都不能替代真人 UI 动作。

2026-07-26 执行注记：S-6/S-7 已 PASS；S-1 已在真实主消息页发送两条明确纠正并进入
生产 reflection Run，但真实 Relay 返回 HTTP 402 `account balance insufficient`，未形成
candidate/evaluation/activation，且 canonical case 截图/DB 文件未归档。因此 S-1 只能
记 `PARTIAL / BLOCKED`，S-2～S-5/S-8 保持 `NOT RUN`；本注记不改变下方冻结验收门。

## 1. 总门与执行顺序

执行顺序固定为：

1. 启动契约和真实 provider 前置检查；
2. S-1 最小正向价值 smoke；
3. smoke 达到业务质量线后才执行 S-2～S-5、S-8 和恢复边界；
4. S-6、S-7 自动化结果与人工矩阵一起汇总；
5. 每次 Launch 都执行 `Status → Stop`，保存 manifest 和 cleanup，最后证明
   survivor=0。

任一 required 场景仍为 `NOT RUN`、`PENDING` 或 `PARTIAL`，或正向场景只有空结果/
诚实降级，本组总门均不得写 PASS。

## 2. 唯一启动、登录和证据纪律

### 2.1 唯一 lifecycle 入口

只允许从仓库根调用：

```powershell
Set-Location F:\projects\deskpet
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\e2e\launch_companion_growth.ps1 `
  -Action Start `
  -ScenarioId <下表固定值> `
  -LaunchId <下表固定值> `
  -BackendPort 18100 `
  -VitePort 15173 `
  -ClockUtc <下表固定 UTC>
```

状态与停止使用完全相同的 `ScenarioId`、`LaunchId` 和端口：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\e2e\launch_companion_growth.ps1 `
  -Action Status -ScenarioId <固定值> -LaunchId <固定值> `
  -BackendPort 18100 -VitePort 15173

powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\e2e\launch_companion_growth.ps1 `
  -Action Stop -ScenarioId <固定值> -LaunchId <固定值> `
  -BackendPort 18100 -VitePort 15173
```

不得手动启动 backend 或第二个 Vite。launcher 必须给唯一 Tauri 树注入：

- `DESKPET_BACKEND_DIR=F:\projects\deskpet\backend`
- `DESKPET_PYTHON=F:\projects\deskpet\backend\.venv\Scripts\python.exe`
- `DESKPET_BACKEND_PORT=18100`
- `DESKPET_VITE_PORT=15173`
- `DESKPET_USER_DATA_DIR=<ScenarioRoot>\user-data`
- `DESKPET_DEV_MODE=1`
- `DESKPET_E2E_CLOCK_UTC=<ClockUtc>`

每次启动先从
`<LaunchRoot>\logs\tauri.log` 证明实际运行
`F:\projects\deskpet\backend` 源码。看到 `[backend_launch] Bundled exe=...`、其他
checkout、第二个 backend/Vite 或未知端口 owner，当前 Launch 直接 FAIL。

### 2.2 DEV 登录与 provider 安全

1. fresh Scenario 出现 onboarding 时，只从仓库根 gitignored
   `F:\projects\deskpet\LOCAL-DEV-CREDENTIALS.md` 读取 DEV 账号。文件缺失就把真实
   provider 场景标为 BLOCKED，不猜凭据。
2. 账号、密码、token、device key 不得进入命令行、`.env`、`secrets/`、manifest、日志、
   数据库证据 JSON 或本报告。
3. 必须经真实 onboarding UI 登录，等待 relay/keychain 和 IdentityReady；登录窗口完全
   关闭且密码字段不可见后，才允许保存第一张截图。
4. 结果记录只能使用 `profile-A`、`profile-B` 等测试别名；不得记录账号或凭据正文。
5. provider 必须是真实生产入口。若 provider 不可用，正向价值场景是 BLOCKED，不得以
   fake provider、协议脚本或已有自动化结果代替。

### 2.3 真人动作循环

每一个 UI 动作必须按以下顺序记录：

1. Snapshot/Screenshot；
2. 在结果报告写一行
   `坐标=(x,y)|动作=<点击/输入/粘贴/F5/切换窗口>|期望=<一个可观察结果>`；
3. 真坐标点击；中文通过已聚焦控件的真实粘贴或键盘输入；
4. 再截图；
5. 记录 backend 日志起止 offset，再用隔离数据库只读查询对账。

数据库与日志只用于对账已经发生的 UI 行为。任何 mutation 必须来自真实 UI。所有截图
都要记录 SHA-256；每个主消息输入记录 session、root run、provider invocation、candidate/
evaluation/decision/operation/effect/notification 等稳定 ID，不能在报告中猜 ID。

### 2.4 固定 Scenario、Launch 与时钟

| 场景 | ScenarioId | LaunchId（按顺序） | ClockUtc | user-data 规则 |
|---|---|---|---|---|
| S-1 + S-8 | `s1` | S-1=`cg-s1s8-main-l01`；可选 activation 恢复=`cg-s1s8-activation-l02`；S-8=`cg-s1s8-history-l03`；可选 rollback 恢复=`cg-s1s8-rollback-l04` | 每次 `2026-07-25T08:00:00Z` | S-1、S-8 必须共享同一 Scenario；进入 S-8 前固定复启 l03 |
| S-2 | `s2` | `cg-s2-main-l01` | `2026-07-25T08:00:00Z` | 独立 fresh Scenario |
| S-3 | `s3` | `cg-s3-thu-create-l01` → `cg-s3-fri-due-l02` → `cg-s3-fri-repeat-l03` | `2026-07-30T08:00:00Z` → `2026-07-31T08:00:00Z` → `2026-07-31T09:00:00Z` | 三次 Launch 共享同一 Scenario |
| S-4 | `s4` | `cg-s4-main-l01` | `2026-07-25T08:00:00Z` | 独立 fresh Scenario |
| S-5 | `s5` | `cg-s5-confirm-l01`；断网恢复复启 `cg-s5-recover-l02` | 每次 `2026-07-25T08:00:00Z` | 两次 Launch 共享同一 Scenario |
| S-6 | `s6` | `N/A`（自动化，不得调用 launcher） | 测试 fixture clock | 不创建手工 user-data |
| S-7 | `s7` | `N/A`（自动化，不得调用 launcher） | 测试 fixture clock | 不创建手工 user-data |

`LaunchId=N/A` 是明确的“不存在进程生命周期”，不是可传给 launcher 的 ID。S-6/S-7
若产生自动化证据，使用 pytest nodeid 和测试临时目录标识，不能伪造
`process-manifest.json` 或 cleanup 记录。

ScenarioId 必须使用上表短值。Launcher 会在创建任何 runtime 文件前按 Windows
MAX_PATH 保守预算 fail-fast；曾使用的长逻辑标签
`cg-s1s8-builtin-override` 会令 Godot 最长 schema 路径达到 261 字符，不能作为实际
ScenarioId。

### 2.5 固定证据路径

对所有手工场景：

```text
ScenarioRoot =
F:\projects\deskpet\plans\2026-07-24-human-anchored-companion-growth\
evidence\manual-runtime\<ScenarioId>

LaunchRoot = <ScenarioRoot>\launches\<LaunchId>
Tauri log = <LaunchRoot>\logs\tauri.log
Manifest = <LaunchRoot>\process-manifest.json
Cleanup = <LaunchRoot>\cleanup-result.json
Shots = <ScenarioRoot>\cases\<S-x>\shots\<LaunchId>-<step>.png
DB evidence = <ScenarioRoot>\cases\<S-x>\db-evidence\<LaunchId>.json
```

每个 DB evidence JSON 至少列出只读来源和查询时点：

- `<ScenarioRoot>\user-data\data\state.db`：主消息、成长通知 history/projection；
- `<ScenarioRoot>\user-data\data\companion.db`：growth event、preference、candidate、
  evaluation、decision、activation、reminder、notification、audit；
- `<ScenarioRoot>\user-data\data\workflow.db`：execution run/start snapshot、provider
  invocation、effect/decision/delivery、CapabilityStore binding/version/operation/receipt。

JSON 只保存必要字段、hash、稳定 ID、计数与状态，不复制凭据或不必要的私密正文。

## 3. S-1：明确长期纠正与 builtin override（首个价值 smoke）

**固定身份与路径**

- ScenarioId：`s1`
- 首次 LaunchId：`cg-s1s8-main-l01`
- screenshots：
  `...\manual-runtime\s1\cases\S-1\shots\cg-s1s8-main-l01-<step>.png`
- log：
  `...\manual-runtime\s1\launches\cg-s1s8-main-l01\logs\tauri.log`
- DB：
  `...\manual-runtime\s1\cases\S-1\db-evidence\cg-s1s8-main-l01.json`
- manifest / cleanup：
  `...\launches\cg-s1s8-main-l01\process-manifest.json` /
  `...\launches\cg-s1s8-main-l01\cleanup-result.json`

**步骤与预期**

| 步骤 | 真实 UI 动作 | 预期 UI / 价值结果 | 只读 DB、日志与 manifest 断言 |
|---|---|---|---|
| 1 | 登录完成后点击桌宠“消息”，进入主消息线程 | 打开的确是主消息页，不是 Code 面板或协议测试页 | trusted owner/generation 已 ready；日志证明 source backend |
| 2 | 分别发送“今天完成了成长闭环自动化测试。”“今天确认主消息页是唯一真人验收入口。”“明天要复核成长通知和回滚证据。”，再发送“请总结今天的对话要点” | checked-in `summarize-day` 基线输出含“主题”、恰好 3 条要点和单列“待跟进” | Run 冻结 builtin `skill-summarize-day` / `summarize-day`，记录实际 `version/manifest_hash/instruction_hash` 和 owner target-absent fence |
| 3 | 发送验收原文：“以后每日总结只保留最重要的两件事，并为每件附一个下一步；不要单列待跟进。” | 主线程得到非空正常回复；前台回复不等待后台反思 | 显式纠正 growth event 幂等落库；candidate 是同 pack 的 `builtin_override`，source exact builtin、target expected-absent |
| 4 | 等待真实后台候选、独立评测与低风险激活；从主消息页打开成长通知和详情 | 通知只出现一次，说明“改了什么、为什么、评测结果、如何撤销”；UI 仍显示同一个 `summarize-day` Skill | evaluation 使用 candidate exact hashes；GrowthPolicy 低风险通过；Capability operation receipt、owner binding 与 activation receipt 一致；无第二 active pointer |
| 5 | 再发送“请总结今天的对话要点” | 输出恰好两项；每项有一个下一步；没有独立“待跟进”段；内容非空且基于今日事实 | 新 Run 冻结 user builtin_override；selected binding、manifest、instruction fingerprint 与 receipt 一致 |
| 6 | F5/关闭再打开消息页并查看 history 与成长详情 | 历史正文、成长卡、详情和回滚入口仍存在且不重复 | history/live reducer 同一 projection；notification delivery 与 audit 各恰好一份 |

若最小 S-1 没有达到步骤 5 的质量线，立即停止昂贵矩阵，总门 BLOCKED。

**可选定时恢复切点（计划要求执行时启用）**

若在 activation dispatch 已持久化、receipt 尚未完成时成功观察到切点，先保存日志游标，
对 `cg-s1s8-main-l01` 执行 `Status → Stop`，再用同一 Scenario、
`cg-s1s8-activation-l02` 重启。不得用数据库写入制造切点。重启后必须收敛到恰好一个
binding、operation receipt 和通知。receipt 完成后再以
`cg-s1s8-history-l03` 重启一次，验证不重复激活或通知。若无法通过真实运行时命中切点，
如实记录 `NOT OBSERVED`，不能伪报覆盖；对应恢复门仍须由确定性 fault matrix 证明。

## 4. S-2：三次独立行为后晋升长期偏好

**固定身份与路径**

- ScenarioId / LaunchId：`s2` / `cg-s2-main-l01`
- shots：`...\manual-runtime\s2\cases\S-2\shots\cg-s2-main-l01-<step>.png`
- log：`...\manual-runtime\s2\launches\cg-s2-main-l01\logs\tauri.log`
- DB：`...\manual-runtime\s2\cases\S-2\db-evidence\cg-s2-main-l01.json`
- manifest / cleanup：同 LaunchRoot 的 `process-manifest.json` / `cleanup-result.json`

每个任务必须用主消息 UI 新建独立话题，不能把同一问题改写、retry 或 continuation
冒充三个 context：

1. 任务 A：发送
   “产品评审结论：保留消息页单入口。负责人小林，明天下午补齐空态文案。”
   并要求整理；收到结果后发送“再短一点，只留结论”。
2. 任务 B：发送
   “迭代复盘结论：优先修复提醒重复。负责人阿杰，本周五前补回归测试。”
   并要求整理；收到结果后发送“再短一点，只留结论”。
3. 任务 C：发送
   “上线协调结论：先发布本地提醒。负责人小周，今晚确认日志和回滚。”
   并要求整理；收到结果后发送“再短一点，只留结论”。
4. 新建第四个话题，发送
   “会议结论：采用每日摘要。负责人小陈；行动项：周一前完成通知详情页。”
   再发送验收原文“帮我整理这次会议记录”。

预期：

- 第 1、2 个独立有效 context 只更新近期层，不晋升长期层；
- 第 3 个有效 context 后，long-term preference 以 evidence set +
  `state_version` CAS 晋升一次；
- 三个 stable context key 彼此不同，证据可追溯且没有重复计数；
- 第四次结果明显简短，但仍保留关键结论、责任人和行动项；
- 不创建虚假的 capability candidate，也不改变无关 active binding；
- audit 明确记录 preference transition 的 before/after version 与原因。

## 5. S-3：主动提醒、引用草稿与跨重启恰好一次

**固定身份与路径**

- ScenarioId：`s3`
- Launch 1：`cg-s3-thu-create-l01`，ClockUtc=`2026-07-30T08:00:00Z`
- Launch 2：`cg-s3-fri-due-l02`，ClockUtc=`2026-07-31T08:00:00Z`
- Launch 3：`cg-s3-fri-repeat-l03`，ClockUtc=`2026-07-31T09:00:00Z`
- 每个 Launch 的 log/manifest/cleanup 位于各自 LaunchRoot；
- shots：
  `...\manual-runtime\s3\cases\S-3\shots\<LaunchId>-<step>.png`
- DB：
  `...\manual-runtime\s3\cases\S-3\db-evidence\<LaunchId>.json`

**Launch 1：周四创建**

1. 在主消息页分别发送：
   - “本周已完成角色移动。”
   - “本周待办是接入存档。”
   - “本周风险是移动端性能。”
2. 只读记录三条真实 source message ref；不得把 ref 写回聊天 payload。
3. 发送验收原文：“每周五下午提醒我整理这周的游戏开发进展，并提前给我准备一个草稿。”
4. UI 必须显示提醒已建立或可解释的确认状态；DB 中 V2 reminder、schedule version、
   stable effect id/hash 和 mutation receipt 一致。不得出现 legacy reminder tool。
5. 执行 `Status → Stop`，cleanup 必须 survivor=0。

**Launch 2：周五到期**

1. 用同一 Scenario、新 LaunchId、固定周五时钟启动。
2. 等待真实 scheduler/outbox；不调用后端 tick，不直写时钟或 reminder。
3. 主消息线程必须出现一次提醒和非空草稿。
4. 草稿逐项对应已授权的“角色移动 / 接入存档 / 移动端性能”source refs，不杜撰；
   physical external send=0。
5. 记录 occurrence id、claim/settle/outbox/notification/delivery 计数，然后
   `Status → Stop`。

**Launch 3：同周期稍后重启**

1. 用同一 Scenario、新 LaunchId、稍后时钟启动并等待 scheduler。
2. history 中仍只有同一个 occurrence 的一条提醒和一份草稿；不补发、不新增 occurrence。
3. reminder、occurrence、notification、delivery、receipt 的唯一性和 result hash 均保持。
4. `Status → Stop`，manifest 中发现的 late child、listener owner 和 Job membership
   最终全部 survivor=0。

## 6. S-4：一次性详细例外不污染长期状态

**固定身份与路径**

- ScenarioId / LaunchId：`s4` / `cg-s4-main-l01`
- shots：`...\manual-runtime\s4\cases\S-4\shots\cg-s4-main-l01-<step>.png`
- log：`...\manual-runtime\s4\launches\cg-s4-main-l01\logs\tauri.log`
- DB：`...\manual-runtime\s4\cases\S-4\db-evidence\cg-s4-main-l01.json`
- manifest / cleanup：同 LaunchRoot 的 `process-manifest.json` / `cleanup-result.json`

1. 在主消息页明确建立长期偏好：“以后整理内容时请保持简短，但不要遗漏结论和行动项。”
2. 用只读证据冻结该 preference 的 id、scope、state/version 和当前 active binding。
3. 发送验收原文：“今天这一次写详细一点，把过程都展开；以后还是保持简短。”
4. 本次回答必须明显详细并展开过程。
5. 再新建普通整理任务；输出恢复简短且保留结论/行动项。
6. 前后长期 preference version/value、Skill active binding 不因一次性例外改变；
   例外不得进入长期晋升证据计数或生成 capability candidate。

## 7. S-5：高风险发送必须双重确认且实际发送为零

**固定身份与路径**

- ScenarioId：`s5`
- LaunchId：`cg-s5-confirm-l01`；网络恢复后 `cg-s5-recover-l02`
- shots：`...\manual-runtime\s5\cases\S-5\shots\<LaunchId>-<step>.png`
- DB：`...\manual-runtime\s5\cases\S-5\db-evidence\<LaunchId>.json`
- log/manifest/cleanup：各自 LaunchRoot 的
  `logs\tauri.log`、`process-manifest.json`、`cleanup-result.json`

1. 设置页若有 `auto_mode`，先通过真实 UI 打开；记录截图，证明本场景验证 auto 也不能绕过
   AC-09。
2. 在主消息页发送验收原文：
   “把每日总结 Skill 改成生成完就直接发到所有项目群，以后不用问我。”
3. 候选必须进入 activation confirmation；确认前 physical send count=0，当前 binding 不变。
4. 打开详情，UI 必须展示 exact package/code/permission/effect digest、风险和持续调用边界；
   若没有这些信息，不得点击确认。
5. 真点击“确认激活”只授权本次 exact activation。activation receipt 落定后，
   physical send 仍为 0。
6. 通过主消息页触发一次每日总结。必须出现绑定原 durable call 的全新一次性 action
   decision；activation nonce、auto mode、历史授权或 delegated grant 都不能消费它。
7. 不点击实际发送许可，改为取消/拒绝；最终 physical send=0，原 durable call 可解释结束，
   不得显示“已发送”。
8. 在 action decision 可见时，用 Windows 真实网络 UI 暂时断开并恢复测试网络；
   每个 OS UI 动作同样声明坐标并截图。不得用 WS/API 修改 decision。
9. F5/关闭重开消息页；decision 仍绑定同一 owner/session/nonce/version，不重复创建或误消费。
   若计划执行跨进程恢复，先 `Status → Stop`，再以 `cg-s5-recover-l02` 重启。
10. 最终验证 execution effect/attempt 的 handoff/disposition、decision/grant、cancel/terminal
    与 UI 一致；外部 transport started ACK 不存在，因此 dispatch/send 均为 0。

若测试机器当时不能安全地隔离网络影响，网络断开子步骤标为 BLOCKED 并报告，不能用脚本
模拟断网后写 PASS；S-5 的确认和 zero-send 主断言仍照常执行。

## 8. S-6：无真实证据时模型自评不得成长（自动化）

- ScenarioId：`s6`
- LaunchId：`N/A`，不得创建 Tauri manifest/cleanup 或 UI 截图。
- exact hypothesis：“用户可能喜欢所有回答都更活泼。”
- 自动化至少覆盖
  `backend/tests/companion/test_growth_reflector.py` 中
  `insufficient_evidence` 路径，以及 candidate/policy/store 对无 live evidence 的拒绝。

确定性断言：

- 没有纠正、重复行为或结果 evidence；
- reflection 可以 abstain/记录 `insufficient_evidence`，但不能产生可激活 candidate；
- GrowthPolicy 不创建 activation request；
- CapabilityStore binding、notification 和 external effect 均不变；
- audit 有可关联的“不处理/证据不足”原因；
- 重跑不增加副作用或重复计数。

结果证据记录 pytest 命令、nodeid、退出码、原始 stdout/stderr 路径和临时目录 cleanup；
不能填手工截图或虚构 LaunchId。

## 9. S-7：新 Skill genesis 只能成为候选（自动化）

- ScenarioId：`s7`
- LaunchId：`N/A`，不得创建 Tauri manifest/cleanup 或 UI 截图。
- exact user request：“给我创建一个叫‘每日三件事’的新 Skill，以后用它安排当天重点。”
- 自动化至少覆盖
  `backend/tests/companion/test_candidate_builder.py`、
  `backend/tests/companion/test_candidate_build_coordinator.py` 和
  `backend/tests/capabilities/test_builder_validation.py` 的 explicit-user、
  reservation、candidate-only finalize 路径。

确定性断言：

- Builder admission 前已存在 trusted user proposal、stable target reservation 和 build lineage；
- 候选是 immutable genesis package，source 为空，target expected-absent；
- package/version/manifest/files/hash 与 host-issued build receipt 一致；
- candidate-only finalize 不调用 Manager 直接 publish；
- medium-risk 新能力在独立 activation confirmation 前没有 active binding、runtime start、
  ToolRegistry 可见项或外部 effect；
- 重放不产生第二个 reservation 或重复 candidate attempt。

## 10. S-8：从 S-1 成长卡回滚到 exact builtin

**前置和路径**

S-8 必须紧接已通过的 S-1，继续使用 ScenarioId
`s1` 和同一主消息线程。S-1 receipt 完成后，无论是否执行了可选
activation 恢复 Launch，都先精确停止当前 Launch，再固定以
`cg-s1s8-history-l03` 重启并从 history 打开原线程执行 S-8。rollback 恢复复启使用
`cg-s1s8-rollback-l04`。

- shots：
  `...\manual-runtime\s1\cases\S-8\shots\<LaunchId>-<step>.png`
- DB：
  `...\manual-runtime\s1\cases\S-8\db-evidence\<LaunchId>.json`
- log/manifest/cleanup：对应 LaunchRoot 的固定路径。

**步骤与预期**

1. 从 S-1 同一主消息线程的成长通知点击“查看详情”；详情必须显示 source builtin、
   current override、evaluation、decision、operation receipt 和回滚动作。
2. 冻结回滚前 user binding generation、override version/manifest、S-1 source builtin
   version/manifest/fingerprint，以及 profile-B 同 scope binding。
3. 真点击“回滚”并完成 UI 要求的明确确认。
4. UI 显示同一个 `summarize-day` Skill“已恢复内置版本”，而不是删除 Skill 或产生第二个
   logical Skill。
5. user binding 以 expected generation CAS 精确移除；新 Run 选择步骤 2 冻结的 exact
   builtin source；candidate、activation receipt、notification 均为 `rolled_back`。
6. override runtime set 在 publish lock 外精确清理；其他 profile 的 binding/runtime 不变。
7. 切换到 profile-B 再切回 profile-A，每次只走真实 UI。两边成长详情、binding 和 memory
   scope 不串读；profile-A 回滚不改变 profile-B。
8. 再发送“请总结今天的对话要点”，输出恢复 checked-in builtin 的“3 条要点 + 单列待跟进”
   结构，且内容非空。
9. F5/history 后回滚卡不复活成可点击 active action，不产生第二次 rollback receipt。

若在 rollback request 已提交、Capability operation receipt 未完成时命中真实切点，保存
日志游标后执行 `Status → Stop`，以 `cg-s1s8-rollback-l04` 重启；必须收敛到一次回滚。
命不中真实切点时如实记录 `NOT OBSERVED`，由 Task 14 fault matrix 提供确定性恢复证据，
不得用数据库写入人为制造。

## 11. 附加恢复与隐私边界

下列边界不增加 distinct scenario 计数，必须在对应 S 场景结果下作为 recovery/replay
记账：

| 边界 | 绑定场景 | 必须证明 |
|---|---|---|
| F5/history | S-1、S-5、S-8 | history/live 结果一致且不重复通知、decision 或 action |
| 断网/恢复 | S-5 | confirmation 保持同一 durable call；没有 physical send |
| activation dispatch/receipt 前后退出 | S-1 | 一次 binding/receipt/notification；未知窗口 fail closed |
| rollback request/receipt 前后退出 | S-8 | 一次 remove_override；坏/旧 projection 不重新开放 |
| reminder 到期前后重启 | S-3 | occurrence/提醒/草稿恰好一次 |
| profile A→B→A | S-8 | owner/generation、binding、memory、详情和通知不串读 |
| 从成长详情执行遗忘 | 独立 `cg-boundary-forget` 场景，Launch `cg-forget-main-l01` | 只经 UI 发起；正文/diff/action tombstone；受影响 binding fence/quarantine/rollback 或 disable；新 dispatch/chained effect=0 |

遗忘边界单独使用：

- shots：`...\manual-runtime\forget\cases\B-forget\shots\cg-forget-main-l01-<step>.png`
- DB：`...\manual-runtime\forget\cases\B-forget\db-evidence\cg-forget-main-l01.json`
- log/manifest/cleanup：`...\manual-runtime\forget\launches\cg-forget-main-l01\...`

遗忘只在隔离 Scenario 中进行，不复用 S-1/S-8 的 user-data，也不得删除用户真实数据。

## 12. 每个 Launch 的收尾

1. 先运行 `Status`，保存动态 Job membership、late descendants、listener owners、
   PID/create-time、parent、command line、private bytes 和 scope 结论。
2. 再运行 `Stop`；先 graceful，必要时只终止同一 Job/PID/create-time identity。
3. 验证 Job membership、repo/source backend、Scenario user-data、Launch temp/config/log
   marker、端口 owner 和 parent chain 多维 scope 的 survivor=0。
4. `cleanup-result.json` 必须记录逐 PID 结果、释放 private memory 总量和未知 scope；
   未知 scope 必须 fail closed，不得按进程名广杀。
5. temp config 只能由 launcher 按可信 manifest 和 containment 删除；Scenario user-data、
   日志、manifest、cleanup 和证据保留。
6. 结果报告中的 cleanup 仍为 `NOT RUN` 时，该 Launch 不可判 PASS。
