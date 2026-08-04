# DeskPet Harness R7 主消息线程真人测试

> 状态：EXECUTED — S-1～S-7 全部 PASS（2026-07-22 用户批准缩减范围）
>
> 对应计划：`plans/2026-07-20-agent-harness-simplification/plan.md` R7
>
> 产品口径：主线程是单任务基线；Code 模式是多个同构任务的并行工作台。本轮唯一真人入口为桌宠主界面点击“消息”后打开的主消息页。

## 1. 前置与证据纪律

1. 环境编排（不是 UI 证据）：只启动一个 Tauri，由它自行启动唯一 Vite 与 backend。工作目录固定为 `F:\projects\deskpet-harness-r55\tauri-app`；启动环境固定为 `DESKPET_BACKEND_PORT=18100`、`DESKPET_VITE_PORT=15173`、`DESKPET_USER_DATA_DIR=F:\projects\deskpet-harness-r55\.e2e-r6\userdata`、`DESKPET_DEV_MODE=1`、`DESKPET_BACKEND_DIR=F:\projects\deskpet-harness-r55\backend`、`DESKPET_PYTHON=F:\projects\deskpet\backend\.venv\Scripts\python.exe`、`DESKPET_WORKSPACE_DIR=F:\projects\deskpet-harness-r55\.e2e-r7\main-thread-fixture`，参数固定为 `tauri dev --config F:\projects\deskpet-harness-r55\.e2e-r7\tauri.e2e.json`。日志必须出现 worktree Dev python 与 `product_harness_activated ... open`。
2. 每个动作前声明 `坐标=(x,y)|动作=|期望=`，每次动作后重新截图；中文用真实 UI 粘贴或输入。
3. 证据分层：Computer Use 负责点击、输入、等待、截图与点击打开产物；环境编排负责启动、重启和精确清理；日志/SQLite 只读旁证负责核对同一真实 UI run 的 run/turn/effect/outcome，不能代替点击。
4. 截图存到 `plans/2026-07-20-agent-harness-simplification/evidence/r7-main-thread/screenshots/`；运行记录写入同目录 `results.md`。
5. 每个启动批次结束后只清理匹配本 worktree、E2E config 与端口 `18100/15173` 的精确进程树，记录释放私有内存并确认端口清空。
6. 主消息页按钮必须显示“隐藏工具消息”，表示工具轨迹当前可见；若显示“显示工具消息”，先真实点击切换并截图。
7. 每个请求携带唯一场景标记：基础标记后必须追加 attempt 与发送时间，例如 `R7-S3-A1-20260722T130501`，重试不得复用。以主消息页当前可见 session id（可为 `default` 或“新话题”生成的 UUID）+ 完整场景标记 + UI 提交时间，从日志/SQLite 只读定位 run/request/turn；涉及唯一性时明确记录 root run、terminal delivery 与 Artifact 计数。

## 2. 分步用例

### S-1 普通只读解释

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 点击桌宠“消息”，再点“+ 新话题” | 打开干净的主消息页任务，记录顶部当前 session id，能看到空白消息区与输入框 |
| 2 | 先只读记录 `calculator.py` 与 `failure.txt` 的 SHA-256；输入“`R7-S1-<attempt>`：不要修改任何文件。必须读取工作区内 `calculator.py` 和 `failure.txt`，只告诉我断言失败的原因。”并发送 | 消息真实出现在主线程；产生当前 run 的只读工具调用，不进入可写 workflow |
| 3 | 最长等待 120 秒，完成后截图 | 准确指出 `add` 错把加法写成减法；该 turn 只有只读工具轨迹、0 个写 Effect、文件 hash 不变；只出现一次最终回复 |

### S-2 同线程追问与身份连续性

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 在 S-1 同一主线程输入“`R7-S2A-<attempt>`：请用一句话说明 Harness/Kernel 负责什么。”并等待完成（最长 120 秒） | 回答明确以 Harness/Kernel 为主语；形成 S-2 的上下文锚点 |
| 2 | 紧接着输入“`R7-S2B-<attempt>`：那它和 Driver 的边界呢？”并等待完成（最长 120 秒） | 无需重复完整主语即可理解“它”指 Harness/Kernel；回答至少包含：Harness/Kernel 管身份、路由和生命周期，Driver 管具体 ReAct/Workflow 执行；同 session、不同 request/turn；不覆盖前一 turn 终态 |

### S-3 DeepResearch 长任务

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 输入“`R7-S3-<attempt>`：调研 2026 年本地 AI 助手的主要技术路线、隐私取舍和适用团队，给出带来源的选型建议。” | 从主线程路由到 durable workflow；卡片显示“进行中”和逐步进度 |
| 2 | 最长等待 15 分钟；完成后截图，并从主消息页点击报告 Artifact 的“打开”检查外部查看器 | 至少 3 个有效子方向；关键结论有来源；卡片显示“已完成”；最终报告/Artifact=1、terminal=1 |

### S-4 PPT 审批与产物

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 输入“`R7-S4-<attempt>`：做一份介绍 DeskPet 当前 Harness 架构和下一步优化方向的 PPT，先让我确认大纲再生成。”；最长等待 180 秒 | 主线程出现绑定本 run 的大纲卡；点击前 Artifact=0 |
| 2 | 截图后点击“✅ 确认生成” | 卡片显示“已确认生成”并进入“进行中”；同一 run 继续、root run=1 |
| 3 | 最长等待 15 分钟；完成后从主消息页点击 PPT Artifact“打开” | PPT Artifact=1、terminal=1；文件非空可打开，内容与大纲一致 |

### S-5 主线程重启恢复

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 输入“`R7-S5-<attempt>`：深度调研 2026 年本地语音 Agent 的端侧推理、隐私和延迟取舍，给出带来源报告。”；等到卡片“进行中”和至少一个逐步进度、但尚未 terminal（最长 180 秒）；通过日志/SQLite 旁证记录 run id（卡片本身不显示 run id） | 得到明确运行中状态；数据库中该 run 只有一个根 admission |
| 2 | 在 progress 后 10 秒内精确停止当前 Tauri 进程树并重新启动唯一实例；点击“消息” | 回到同一主线程；历史与进行中/已恢复状态可见 |
| 3 | 最长等待 15 分钟，等待原 run 终态并核对 run id、root admission、Artifact 数量 | 沿用原 run；根 admission=1、最终回复=1、Artifact≤1，无重复外部执行 |

### S-6 停止与后续 turn 隔离

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 输入“`R7-S6-<attempt>`：深度调研 Rust 桌面 Agent 的并发模型，给出带来源报告。”；最长等待 180 秒看到卡片“进行中” | 得到唯一明确的运行中 root |
| 2 | 任务仍运行时，在输入框粘贴“`R7-S6B-<attempt>`：只回答：后续短问题正常。”并按 Enter 尝试提交；10 秒内截图并只读核对 active root | 固定产品契约：主线程运行时阻止第二次提交，UI 不新增该用户消息，旧 root 仍是唯一 active；若出现第二个 active root 或短问题开始执行，立即判 FAIL |
| 3 | 清空输入框并点击“■ 停止”；最长等待 120 秒到旧 run terminal，超时记 FAIL/INSUFFICIENT | 旧 run 显示“已取消”或可解释的 late-reconciled；停止目标唯一 |
| 4 | 旧 run terminal 后发送 `R7-S6C-<attempt>` 短问题，最长等待 120 秒；完成后再等 30 秒截图 | 短问题正常 completed，不被旧任务污染；任一时刻 active root≤1；无迟到绿色成功卡；后台状态与 UI 一致 |

### S-7 工具失败诚实投影

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 先输入“`R7-S7A-<attempt>`：必须调用文件读取工具读取工作区内 `receipt-source.txt` 并原样告诉我内容。”（最长等待 120 秒） | 当前 session 产生一次成功的 file-read receipt，UI 显示成功工具轨迹，回复包含唯一 token `R7_CURRENT_RECEIPT_OK_20260722` |
| 2 | 只读确认工作区内 `r7-definitely-missing.txt` 不存在；再输入“`R7-S7B-<attempt>`：必须调用文件读取工具读取 `r7-definitely-missing.txt`，明确说明是否成功，不要引用上一文件、不要编造内容。”（最长等待 120 秒） | 新 run/turn 产生失败 file-read；UI 显示失败工具轨迹，不能用上一步成功 receipt 通过 |
| 3 | 等待完成并截图，核对两次 tool call 的 run/turn/call/effect | 第二次 tool/UI 不显示绿色成功；最终诚实说明不存在/失败，不出现唯一 token；typed outcome 与 UI 一致 |

## 3. Code 模式后续专项边界

Code 模式不重复上述单任务语义，只验证四件额外能力：多个任务真并行、任务上下文/事件完全隔离、每个任务可单独停止、所有任务的进度与结果可同时查看。该专项不计入本轮主线程完成度。

## 4. 结果账本

| scenario | gate | root runs | retry | continuation | engine 终态 | 业务终态 | 证据 | 状态 |
|---|---|---:|---:|---:|---|---|---|---|
| S-1 | positive-value | 1 | 0 | 0 | completed×1 | completed×1 | 只读文件 + UI + 日志 | PASS |
| S-2 | positive-value | 2 | 0 | 1 | completed×2 | completed×2 | 同线程两 turn + UI + 日志 | PASS |
| S-3 | positive-value | 1 | 0 | 0 | completed×1 | completed×1 | 进度卡 + 报告 Artifact | PASS |
| S-4 | positive-value | 1 | 0 | 1 | completed×1 | completed×1 | 大纲卡真点击 + durable decision | PASS |
| S-5 | positive-value | 1 | 0 | 1 | completed×1 | completed×1 | 真重启 + 同 run 恢复 | PASS |
| S-6 | negative-safety | 2 | 0 | 0 | cancelled×1 + completed×1 | cancelled×1 + completed×1 | busy 输入阻止 + 真取消 + 后续 turn | PASS |
| S-7 | negative-safety | 2 | 0 | 0 | completed×2 | completed×2（含一次工具失败） | 成功/失败 file-read receipt 对照 | PASS |

`distinct_scenarios=7 / ui_submissions=10 / root_runs=10 / retry_in_accepted_runs=0 / continuation=3 / completed=9 / cancelled=1 / partial=0 / insufficient=0 / failed_root=0`

这里只统计最终被接受用于验收的执行；定位缺陷时留下的早期诊断尝试保留在截图目录，但不混入最终 root/terminal 总账。S-6 的 busy 输入尝试被前端阻止，因此不计一次 UI submission 或 root run。

## 5. 出口

执行结果与截图索引见 [`results.md`](./results.md)。

- S-1～S-7 均有真实主消息页 root run 与截图；所有 required 行不再为 PENDING/PARTIAL/NOT RUN。
- 至少覆盖普通 ReAct、同线程 continuation、DeepResearch、PPT decision/Artifact、restart recovery、cancel isolation、typed failure 七类风险。
- 结论明确限定为“用户批准后的主消息线程范围”；Code 并行工作台专项与 Voice venue 不计入本轮真人完成度。
