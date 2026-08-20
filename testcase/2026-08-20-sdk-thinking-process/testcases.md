# SDK 公开思考过程与终态折叠：黑盒测试用例

## 1. 范围与判定边界

- 验收对象：AC-8、AC-9；测试义务：TO-A8、TO-A9、TO-R4、TO-R5、TO-R6。
- 可展示内容仅限模型主动公开的工作叙述、公开状态摘要及既有工具卡；隐藏
  `reasoning_content`、reasoning token、原始 Provider payload 或未经公开的 Chain-of-Thought
  都不得出现在正文、持久化公开投影或后续模型上下文中。
- 本文件只定义动作、oracle 与所需证据，不记录执行结果。原始证据应写入本地 ignored 证据目录，
  testcase 结果只引用 Run/scenario ID、相对索引和 SHA-256。

## 2. 共用夹具与证据规则

| 夹具 | 定义 |
|---|---|
| F-RUNNING | 同一 canonical Run 已有至少一条非空公开工作叙述，Run 状态为 running（或等价非终态） |
| F-TERMINAL | F-RUNNING 转为 completed、failed 或 cancelled 之一，开始/结束时间有效 |
| F-HISTORY | 已持久化的 F-TERMINAL 会话，供刷新、切换会话或重启后 hydration |
| F-MALFORMED | 同一 Run 的重复 summary、乱序终态/晚到 running、缺开始时间、缺终态时间、超长叙述等变异事件 |
| F-SCHEMA | 逐项变异：缺 `run_id`、缺/空/重复 `summary_id`、缺 `status`、非法 status、公开正文错放到非 `text` 字段 |
| F-HIDDEN | Provider 响应同时包含公开 assistant content 与仅供 Provider 内部使用的 `reasoning_content` 标记串 |

自动化证据至少包含：测试命令与用例名、断言失败即非零退出、必要的结构化投影/出站 payload
快照（脱敏）。Computer Use 证据必须遵守“动作前坐标声明 → 真点击/输入 → 截图 → 日志或 durable
状态核对”，不得用 WebSocket 直注代替真实 UI 动作。

## 3. Required 自动化场景

### TC-AUT-01 — SDK 公开叙述形成 durable exclude 投影

- 绑定：AC-8；TO-A8、TO-R4。
- 前置：构造含稳定 Run identity、非空公开 assistant content 和 tool call 的 SDK tool turn；测试入口必须
  覆盖 Provider adapter、per-Run Delivery adapter、RunPresenter 与 durable Session append，不能只调用
  sanitizer 或 React 组件。
- 动作：
  1. 经生产等价 Provider→Delivery→Presenter 黑盒入口处理一个含两个 call ID 的 turn，并以
     call-2、call-1 的顺序触发调用、交错回传结果。
  2. 再处理下一 Provider turn 的 call-3；捕获 UI/Session 可消费的公开消息序列及持久化投影。
  3. 分别在 capture、persist、WS projection 三处注入异常；每个失败点独立重跑并核对工具调用与终态。
  4. 对同一 Run 触发一次后续上下文组装并捕获出站消息。
- Oracle：
  1. tool call 前出现且只出现一条归属同一 canonical Run 的公开 `reasoning_summary`。
  2. summary 为 durable，且 `context_visibility=exclude`。
  3. call-1/call-2 共享 iteration，交错结果仍回到各自 call，call-3 使用下一 iteration；三个失败点
     均不使工具调用、工具结果和 Run 终态重复或改变。
  4. 后续出站上下文不含该 summary、UI 标题、耗时或 activity 技术字段。
- 证据：聚焦自动化测试名与退出码；按 Run ID 脱敏后的投影顺序快照；后续出站 payload 的禁止字段
  断言摘要。

### TC-AUT-02 — running 默认展开并在终态自动折叠

- 绑定：AC-9；TO-A9、TO-R5。
- 前置：渲染包含 progress、tool call、tool result 的 F-RUNNING；准备该 Run 的 final assistant，及
  下一 Run 的首个事件；随后在同一组件实例中将前一 Run 推进为 F-TERMINAL。
- 动作：
  1. 首次渲染 running Run。
  2. 推送第二条同 Run 公开叙述，推进虚拟时间。
  3. 依次参数化推进为 completed、failed、cancelled，并注入 final assistant 与下一 Run 首事件。
- Oracle：
  1. running 时正文默认展开，新叙述追加在同一思考区且可见，耗时非负并随时间推进。
  2. 每一种终态到达后正文自动折叠，标题显示可读“耗时 X”；Run 不再显示“思考中”。
  3. 折叠只影响显示，不移除 durable 内容，不改变最终回复、工具卡事实或终态。
  4. 对应最终回复只出现一次并保持在该 Run 思考分组之后；跨 Run 的首次事件 anchor 顺序不变。
- 证据：三种终态的组件断言/快照；虚拟时钟断言；同 Run 只有一个分组的计数断言。

### TC-AUT-03 — 用户可展开重看并再次收起

- 绑定：AC-9；TO-A9。
- 前置：已自动折叠的 F-TERMINAL。
- 动作：
  1. 点击思考区标题一次。
  2. 检查全部公开叙述与既有工具卡。
  3. 再点击标题一次。
- Oracle：第一次点击展开原有内容且标题/耗时保持正确；第二次点击重新收起；两次操作均不修改
  Run 状态、消息顺序或持久化事实。
- 证据：click 前、第一次 click 后、第二次 click 后的组件断言/快照；终态未变断言。

### TC-AUT-04 — hydration 恢复终态默认折叠且不串 Run

- 绑定：AC-9；TO-A9、TO-R5。
- 前置：准备 F-HISTORY，另有一个不同 Run 的历史 summary；可另参数化一个真正未终态 Run。
- 动作：先卸载组件并用 Session store hydration 重建同一消息流；再切换到其他 Session 后切回；完整桌面
  进程重启由 TC-CU-02 覆盖，不用组件 remount 冒充进程恢复。
- Oracle：终态 Run 恢复后默认折叠并显示耗时或安全降级“已完成”；未终态 Run 默认展开；不同 Run
  的叙述与工具卡不串组；终态不会因历史晚到 running 事件复活。
- 证据：hydration 后组件断言/快照；分组 Run identity 与数量断言；终态保持断言。

### TC-AUT-05 — 无公开叙述但有工具事件时仍形成可折叠执行过程

- 绑定：AC-8、AC-9；TO-A8、TO-A9。
- 前置：SDK Run 有 tool call/result 和最终回复，但普通 assistant content 为空或仅空白。
- 动作：完成该 Run，并分别检查实时消息流与 hydration 后消息流。
- Oracle：同 Run 的公开工具卡形成唯一思考区，running 默认展开、terminal 自动折叠并可点击重看；
  不伪造叙述文本。只有进度与工具事件都不存在，或二者均被用户隐藏时，才不出现空思考区；最终回复、
  终态与输入区空闲行为照常存在。
- 证据：实时及 hydration 组件断言/快照；tool-only 分组、展开折叠、隐藏组合、最终回复存在性断言。

### TC-AUT-06 — 重复、乱序、schema 违约、缺时间和超长叙述的安全降级

- 绑定：AC-8、AC-9；TO-A8、TO-A9、TO-R5。
- 前置：F-MALFORMED + F-SCHEMA 参数矩阵；超长输入至少 10,000 字符。
- 动作：
  1. 重放相同 `summary_id`，再发送同 Run 的乱序摘要。
  2. 在终态后发送晚到 running/旧摘要。
  3. 分别移除开始时间、结束时间，并注入会导致负耗时的时间顺序。
  4. 注入超过公开展示上限的叙述。
  5. 分别注入：无 `run_id`、无/空/重复 `summary_id`、无 `status`、非法 status、正文仅存在于错误字段。
- Oracle：
  1. 重复 summary 幂等，同一 Run 只生成一个思考区；内容不重复计数。
  2. 终态不复活、不自动展开为 running，晚到内容不改变 canonical 终态。
  3. 时间缺失/异常时仅显示“思考中”或“已完成”等安全文案，不显示负数、`NaN`、`Infinity`。
  4. 超长内容有界展示且容器内部可滚动，不撑破消息页、不使渲染崩溃。
  5. 无 `run_id` 的非空公开消息安全平铺且不与其他 Run 串组；无/空 `summary_id` 或正文错字段的事件
     被拒收且不生成空组；重复 `summary_id` 幂等；缺/非法 status 只能安全降级为非终态，不能覆盖
     canonical terminal projection。所有分支都必须保留用户继续发送/重试的入口。
- 证据：每个参数行的独立断言；分组数量与终态断言；无负数/NaN 的文本断言；容器
  `scrollHeight > clientHeight` 且消息页宽度不增长的断言/快照。

### TC-AUT-07 — 隐藏 CoT 不进入 UI、持久化公开正文或后续上下文

- 绑定：AC-8；TO-A8、TO-R4。
- 前置：F-HIDDEN，使用唯一 canary 字符串标识隐藏 `reasoning_content`；公开 assistant content 使用
  不同的公开 canary。
- 动作：处理 Provider 响应，检查实时 UI 消息、durable public projection、Session hydration，并在
  下一轮请求中捕获出站上下文。
- Oracle：公开 canary 可按契约形成 summary；隐藏 canary 在 UI 正文、可展开详情、持久化公开投影、
  Session 历史和下一轮出站上下文中均为零命中；最多允许出现不含原文的无内容 activity 信号。
- 证据：五个检查面的零命中断言；公开 canary 正向命中断言；脱敏 payload/投影字段摘要。

## 4. Required Computer Use 真机场景

### TC-CU-01 — 真实运行展开 → 终态折叠 → 点击重看

- 绑定：AC-8、AC-9；TO-A8、TO-A9、TO-R6。
- 前置：源码应用由唯一前端/backend 进程启动；使用新的或已知干净的 Session；公开进度/工具显示
  开关处于显示状态；Provider 可执行一个持续时间足够截图的多工具只读任务。长历史 Session 必须由
  真实 UI 完成至少 10 个完整 user→assistant 普通对话 turn，禁止复制 SessionDB/profile 或协议注入制造。
- 动作：
  1. 在新 Session 与已有至少 10 轮历史的 Session 各执行一次独立 root Run。每次先
     Snapshot/Screenshot，声明输入框坐标；真点击输入框并输入：“读取项目中的一个公开测试文件，
     运行一个只读检查，然后告诉我结果。执行前请用普通回复简短说明你正在做什么。”
  2. 声明发送坐标并真点击发送；在 Run 尚未终态时截图。
  3. 等待真实 Run 达到 completed、failed 或 cancelled 终态，再截图。
  4. 声明思考区标题坐标并真点击展开，截图；再真点击一次并截图确认收起。
  5. 以 Run ID 核对日志/durable 状态及公开 summary，不使用协议直注制造 UI 证据。
  6. 记录两个不同 Run ID、长历史 Session ID、发起本次 Run 前的完整 turn 计数；终态截图必须同时
     显示折叠思考组在上、唯一 final assistant 在下的 AX/可见顺序。
- Oracle：
  1. 运行中思考区自动展开，可见公开叙述/执行摘要和新增步骤，不显示隐藏 CoT。
  2. 终态后自动折叠，标题显示可读且非负的“耗时 X”或缺时间安全文案。
  3. 第一次点击可重看完整公开内容和对应工具卡，第二次点击收起。
  4. Run 只有一个思考分组；最终回复、工具执行与输入区终态正常；日志/durable 状态与 UI 一致。
  5. 两个独立 root Run 均完整终态，至少一个来自 ≥10 轮历史 Session。若某次真实 Provider 没有公开
     叙述，该次只按 TC-AUT-05 记录“无伪造且工具/最终回复正常”的载荷变异，不能拿来证明运行中展开，
     也不能无限重试后隐去该事实。
- 证据：动作前截图、运行中展开截图、终态折叠截图、手动展开截图、再次收起截图；每步坐标声明；
  Run ID；脱敏日志/DB 核对摘要；每个原始文件 SHA-256。

### TC-CU-02 — 历史终态恢复默认折叠

- 绑定：AC-9；TO-A9、TO-R6。
- 前置：TC-CU-01 产生的终态历史 Session。
- 动作：通过真实 UI 切换离开再回到该 Session；随后重启桌面应用并再次进入该 Session；每次均截图，
  再点击标题展开一次。
- Oracle：两种 hydration 路径下终态思考区均默认折叠，不显示 running、不串入其他 Run；点击后仍能
  恢复相同公开内容，最终回复与工具卡保持可用。
- 证据：切换会话恢复截图、重启恢复截图、恢复后展开截图；同一 Session/Run ID；脱敏 durable 状态
  核对与 SHA-256。

### TC-CU-03 — 全新 profile 首次配置后的历史恢复入口

- 绑定：AC-9；TO-A9、TO-R6（stateful-init change-risk）。
- 前置：新的隔离 `DESKPET_USER_DATA_DIR`，不得复制既有 state/session 数据；认证凭据由用户本人在
  首次设置 UI 输入，测试证据不得包含密码、API key 或 token。
- 动作：从全新 profile 启动应用 → 完成首次 Provider 配置 → 直达消息页完成一次至少含公开叙述或
  公开工具事件之一的工具 Run → 完整退出并重启 → 回到该历史 Session。
- Oracle：首次配置后无需额外暖启动即可完成工具 Run；重启恢复的终态思考区默认折叠、可点击重看，
  Provider/Session/Run identity 不串到既有 profile；无凭据时必须保持 NOT_RUN 并升级给用户，不能用
  暖 profile 或协议注入替代。
- 证据：脱敏的首次设置前后截图、Run 终态/重启恢复截图、Session/Run ID、日志摘要与 SHA-256。

## 5. Critical 与 affected-surface smoke

### TC-SMOKE-01 — 关键路径与只增不退化验证

- 绑定：AC-8、AC-9；TO-A8、TO-A9、TO-R4、TO-R5。
- 影响面：Provider→Delivery→RunPresenter→SessionDB/control WS→Session store→ChatView→消息流；
  Run 终态 authority、上下文组装、既有工具卡和最终回复为关键相邻面。
- 动作（仓库根执行，全部要求零退出）：
  1. `backend/.venv/bin/python -m pytest -q backend/tests/test_product_delivery_adapter.py backend/tests/sdk_adapters/test_product_host_ports.py backend/tests/test_execute_sdk_run.py backend/tests/test_provider_runtime_refresh.py`
  2. `npm --prefix tauri-app test -- --run src/views/ChatView.test.tsx src/components/MessageStreamPanel.workflow.test.tsx src/code-panel/ws.chat.test.ts`
  3. `npm --prefix tauri-app run typecheck`
  4. `PYTHONPATH=backend backend/.venv/bin/python scripts/acceptance/agent_activity_timeline_smoke.py`
  5. `git diff --check`
- Oracle：全部命令零退出；新增 summary 不改变 Provider 结算、工具执行次数、canonical Run 终态和最终
  回复；无 Run ID 的旧消息安全平铺；隐藏工具/进度开关仍生效；上下文仍不含 exclude 投影或隐藏 CoT。
- 证据：完整命令清单、退出码和测试计数；失败测试名（若有）；关键禁止字段断言摘要。不得以 smoke
  替代 TC-CU-01/02 的真实桌面证据。

## 6. 覆盖矩阵

| testcase | AC-8 | AC-9 | TO-A8 | TO-A9 | TO-R4 | TO-R5 | TO-R6 |
|---|---:|---:|---:|---:|---:|---:|---:|
| TC-AUT-01 | ✓ |  | ✓ |  | ✓ |  |  |
| TC-AUT-02 |  | ✓ |  | ✓ |  | ✓ |  |
| TC-AUT-03 |  | ✓ |  | ✓ |  |  |  |
| TC-AUT-04 |  | ✓ |  | ✓ |  | ✓ |  |
| TC-AUT-05 | ✓ | ✓ | ✓ | ✓ |  |  |  |
| TC-AUT-06 | ✓ | ✓ | ✓ | ✓ |  | ✓ |  |
| TC-AUT-07 | ✓ |  | ✓ |  | ✓ |  |  |
| TC-CU-01 | ✓ | ✓ | ✓ | ✓ |  |  | ✓ |
| TC-CU-02 |  | ✓ |  | ✓ |  |  | ✓ |
| TC-CU-03 |  | ✓ |  | ✓ |  |  | ✓ |
| TC-SMOKE-01 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |  |
